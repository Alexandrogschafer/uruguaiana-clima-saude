"""
Guarda cópia local dos DADOS ESPACIAIS que fundamentam o estudo das manchas de
inundação, como a fonte os serve (sem reprojetar nem recortar além do filtro
indicado), cada um com .json irmão (endereço, data, tamanho, sha256).

Itens (escolha com --itens; padrão: todos):
  risco     setorização de risco do SGB (MapServer, camada 0), filtrada pelo
            código do município                       -> data/raw/vetor/
  manchas   serviços de manchas de inundação do SGB (pasta "hidrologia") cujo
            nome casa com --padrao-servicos: lista as camadas e baixa as
            feições das que cobrem o município          -> data/raw/vetor/
  glofas    mapas globais de perigo de inundação (GloFAS/JRC): lista a pasta e
            baixa os blocos pedidos, para todos os tempos de retorno, mais os
            arquivos de leia-me e licença               -> data/raw/raster/
  bater     população em áreas de risco (IBGE, 2018): zip inteiro; só conta as
            feições do município                        -> data/raw/vetor/
  fepam     área diretamente atingida em maio de 2024: salva a página, baixa o
            arquivo e verifica a interseção com o município -> data/raw/vetor/

Tudo fica FORA do git (data/raw/). Idempotente: arquivo que já existe não é
baixado de novo (use --forcar). Educação com os servidores: User-Agent
identificado, pausa de 2 s entre pedidos ao mesmo servidor, tempo limite de
60 s, uma nova tentativa. Arquivo acima de 500 MB não é baixado.

Uso:
  python scripts/download/inundacao_dados_espaciais.py --codigo-ibge CODIGO --padrao-servicos "NOME_DO_MUNICIPIO|mancha"
  python scripts/download/inundacao_dados_espaciais.py --itens glofas --blocos-glofas ID85_S20_W60 ID86_S30_W60
  python scripts/download/inundacao_dados_espaciais.py --codigo-ibge CODIGO --itens manchas --padrao-servicos "/(mancha_NOME|manchas_inundacao)$" --tempo-limite 120 --uma-tentativa
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import geopandas as gpd
import requests

sys.path.append(str(Path(__file__).resolve().parents[1] / "utils"))
from recorte_municipio import CRS_PADRAO, carregar_area_estudo  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

RAIZ = Path(__file__).resolve().parents[2]
VETOR, RASTER, DOCS = RAIZ / "data" / "raw" / "vetor", RAIZ / "data" / "raw" / "raster", RAIZ / "data" / "raw" / "documentos"
USER_AGENT = "uruguaiana-clima-saude (pesquisa academica)"
PAUSA_S, TEMPO_LIMITE_S, MAX_ARQUIVO = 2.0, 60, 500 * 1024**2
TENTATIVAS = 2  # o pedido e uma nova tentativa (--uma-tentativa: só o pedido)
URL_RISCO = "https://geoportal.sgb.gov.br/server/rest/services/gestaoterritorial/risco/MapServer"
URL_HIDROLOGIA = "https://geoportal.sgb.gov.br/server/rest/services/hidrologia"
URL_GLOFAS = "https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/CEMS-GLOFAS/flood_hazard/"
URL_BATER = ("https://geoftp.ibge.gov.br/organizacao_do_territorio/tipologias_do_territorio/populacao_em_areas_de_risco_no_brasil/"
             "base_de_dados/PARBR2018_BATER.zip")
URL_FEPAM = "https://fepam.rs.gov.br/arquivos-geoespaciais-eventos-climaticos-de-maio-de-2024"
RESUMO: dict = {}  # o que cada item achou (impresso no fim e gravado em data/raw/vetor/)

_sessao = requests.Session()
_sessao.headers["User-Agent"] = USER_AGENT
_ultimo: dict[str, float] = {}


def pedir(url: str, **kw) -> requests.Response:
    host = urlparse(url).netloc
    for tentativa in range(1, TENTATIVAS + 1):
        espera = PAUSA_S - (time.time() - _ultimo.get(host, 0))
        if espera > 0:
            time.sleep(espera)
        try:
            r = _sessao.get(url, timeout=TEMPO_LIMITE_S, **kw)
            _ultimo[host] = time.time()
            if r.status_code >= 500 and tentativa < TENTATIVAS:
                continue
            return r
        except requests.RequestException as erro:
            _ultimo[host] = time.time()
            if tentativa == TENTATIVAS:
                raise
            logger.warning("falha em %s (%s) — uma nova tentativa", url, erro)
    return r


def meta(caminho: Path, **campos) -> None:
    b = caminho.read_bytes()
    caminho.with_suffix(".json" if caminho.suffix != ".json" else ".meta.json").write_text(json.dumps(
        {"arquivo": caminho.name, "data_acesso": datetime.now(timezone.utc).isoformat(timespec="seconds"), "tamanho_bytes": len(b),
         "sha256": hashlib.sha256(b).hexdigest(), **campos}, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    logger.info("gravado: %s — %d bytes", caminho.relative_to(RAIZ), len(b))


def baixar(url: str, destino: Path, forcar: bool, **campos) -> Path | None:
    """Baixa um arquivo inteiro (até 500 MB). Devolve o caminho, ou None se não baixou."""
    if destino.exists() and not forcar:
        logger.info("já existe: %s", destino.relative_to(RAIZ))
        return destino
    r = pedir(url, stream=True)
    if r.status_code != 200:
        logger.warning("HTTP %d em %s", r.status_code, url)
        return None
    if int(r.headers.get("Content-Length") or 0) > MAX_ARQUIVO:
        logger.warning("NÃO baixado (acima de 500 MB): %s — %s bytes", url, r.headers.get("Content-Length"))
        return None
    destino.parent.mkdir(parents=True, exist_ok=True)
    with open(destino, "wb") as f:
        for bloco in r.iter_content(1 << 20):
            f.write(bloco)
    meta(destino, fonte_url=url, tipo_de_conteudo_do_servidor=r.headers.get("Content-Type"), **campos)
    return destino


def consulta_geojson(camada_url: str, destino: Path, forcar: bool, params: dict, **campos) -> int | None:
    """Feições de uma camada de MapServer em GeoJSON, como o serviço entrega (paginado). Devolve o número de feições."""
    if destino.exists() and not forcar:
        logger.info("já existe: %s", destino.relative_to(RAIZ))
        return len(json.loads(destino.read_text(encoding="utf-8")).get("features", []))
    feicoes, incompleto = [], False
    while True:  # o deslocamento só é pedido se o serviço disser que cortou a resposta (há serviços sem paginação)
        r = pedir(f"{camada_url}/query", params={"outFields": "*", "f": "geojson", **({"resultOffset": len(feicoes)} if feicoes else {}), **params})
        d = r.json()
        if "error" in d:
            logger.warning("erro do serviço em %s: %s", camada_url, d["error"].get("message"))
            if not feicoes:
                return None
            incompleto = True
            break
        feicoes += d.get("features", [])
        if not d.get("exceededTransferLimit") and not d.get("properties", {}).get("exceededTransferLimit"):
            break
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(json.dumps({"type": "FeatureCollection", "features": feicoes}, ensure_ascii=False), encoding="utf-8")
    meta(destino, fonte_url=f"{camada_url}/query", parametros=params, n_feicoes=len(feicoes), resposta_incompleta=incompleto,
         crs="como o serviço entrega em GeoJSON (coordenadas geográficas, WGS 84); sem reprojeção", **campos)
    return len(feicoes)


def caixa_do_municipio() -> str:
    """Envelope do município em coordenadas geográficas (para o filtro espacial dos serviços)."""
    x0, y0, x1, y1 = carregar_area_estudo().to_crs(4326).total_bounds
    return f"{x0},{y0},{x1},{y1}"


# ---------------------------------------------------------------- itens
def item_risco(a) -> None:
    info = pedir(f"{URL_RISCO}/0", params={"f": "json"}).json()
    destino = VETOR / "setorizacao-risco_sgb_atual_vetorial.geojson"
    n = consulta_geojson(f"{URL_RISCO}/0", destino, a.forcar, {"where": f"cd_geocmu='{a.codigo_ibge}'"}, fonte="SGB — setorização de risco (MapServer, camada 0)",
                         filtro=f"cd_geocmu = código do município ({a.codigo_ibge})", campos=[f["name"] for f in info.get("fields", [])])
    RESUMO["risco"] = {"arquivo": destino.name, "n_feicoes": n, "campos": [f["name"] for f in info.get("fields", [])]}


def item_manchas(a) -> None:
    pasta = pedir(URL_HIDROLOGIA, params={"f": "json"}).json()
    nomes = [s["name"] for s in pasta.get("services", []) if s["type"] == "MapServer" and re.search(a.padrao_servicos, s["name"], re.I)]
    caixa = caixa_do_municipio()
    x0, y0, x1, y1 = (float(v) for v in caixa.split(","))
    saida = []
    for nome in nomes:
        try:
            _um_servico(a, nome, caixa, (x0, y0, x1, y1), saida)
        except (requests.RequestException, ValueError, KeyError) as erro:  # um serviço fora do ar não derruba os outros
            logger.warning("serviço %s sem resposta: %s", nome, erro)
            saida.append({"servico": nome, "data_da_tentativa": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                          "erro": f"{type(erro).__name__}: sem resposta em {TEMPO_LIMITE_S} s, " + ("depois de uma nova tentativa" if TENTATIVAS > 1 else "em uma tentativa só")})
    RESUMO["manchas"] = saida


def _um_servico(a, nome: str, caixa: str, limites: tuple, saida: list) -> None:
    x0, y0, x1, y1 = limites
    if True:
        base = f"{URL_HIDROLOGIA}/{nome.split('/')[-1]}/MapServer"
        srv = pedir(base, params={"f": "json"}).json()
        if "error" in srv:  # o servidor respondeu, mas com erro do serviço (não confundir com serviço sem camadas)
            saida.append({"servico": nome, "data_da_tentativa": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                          "erro": f"o servidor respondeu com erro do serviço: {srv['error'].get('code')} — {srv['error'].get('message')}",
                          "detalhes": srv["error"].get("details")})
            return
        camadas = [(c["id"], c["name"]) for c in srv.get("layers", []) if not c.get("subLayerIds")]
        reg = {"servico": nome, "camadas": [{"id": i, "nome": n} for i, n in camadas], "baixadas": []}
        ext = srv.get("fullExtent") or {}
        if ext and ext.get("spatialReference", {}).get("latestWkid", ext.get("spatialReference", {}).get("wkid")) in (4326, 4674):
            if ext["xmax"] < x0 or ext["xmin"] > x1 or ext["ymax"] < y0 or ext["ymin"] > y1:
                reg["cobre_o_municipio"] = False  # serviço de outro lugar: só lista as camadas
                saida.append(reg)
                return
        filtro = {"where": "1=1", "geometry": caixa, "geometryType": "esriGeometryEnvelope", "inSR": 4326, "spatialRel": "esriSpatialRelIntersects"}
        for i, n in camadas:
            cont = pedir(f"{base}/{i}/query", params={**filtro, "returnCountOnly": "true", "f": "json"}).json()
            total = cont.get("count")
            if not total:
                continue
            curto = re.sub(r"[^a-z0-9]+", "-", f"{nome.split('/')[-1]}-camada{i}-{n}".lower()).strip("-")
            destino = VETOR / f"manchas-inundacao_sgb-{curto}_atual_vetorial.geojson"
            nf = consulta_geojson(f"{base}/{i}", destino, a.forcar, filtro, fonte=f"SGB — {nome} (MapServer), camada {i}: {n}",
                                  filtro="feições que cruzam o envelope do município")
            atributos, campos = {}, []
            if nf:
                campos = list(json.loads(destino.read_text(encoding="utf-8"))["features"][0]["properties"])
                for f in json.loads(destino.read_text(encoding="utf-8"))["features"][:200]:
                    for k, v in f["properties"].items():
                        if re.search(r"cota|tr|nivel|tempo", k, re.I):
                            atributos.setdefault(k, set()).add(v)
            reg["baixadas"].append({"id": i, "nome": n, "arquivo": destino.name, "n_feicoes": nf, "campos": campos, "atributos_de_cota": {k: sorted(map(str, v)) for k, v in atributos.items()}})
        reg["cobre_o_municipio"] = bool(reg["baixadas"])
        saida.append(reg)


def item_glofas(a) -> None:
    idx = pedir(URL_GLOFAS).text
    linha = r'<a href="([^"?/][^"]*)">[^<]*</a></td><td[^>]*>\s*(\d{4}-\d{2}-\d{2} \d{2}:\d{2})\s*</td><td[^>]*>\s*([\d.]+[KMG]?|-)\s*</td>'  # índice de pasta do servidor
    entradas = re.findall(linha, idx)
    reg = {"pasta": [{"nome": n, "data": d, "tamanho": t} for n, d, t in entradas], "tempos_de_retorno": {}, "baixados": [], "nao_baixados": []}
    destino = RASTER / "perigo-inundacao_glofas-jrc"
    for n, _, _ in entradas:
        if not n.endswith("/"):  # leia-me, licença, registro de mudanças, extensão dos blocos
            baixar(urljoin(URL_GLOFAS, n), destino / n, a.forcar, fonte="GloFAS/JRC — flood hazard maps (arquivo de apoio da pasta)")
    for n, _, _ in entradas:
        if not re.fullmatch(r"RP\d+/", n):
            continue
        sub = pedir(urljoin(URL_GLOFAS, n)).text
        arqs = [x for x in re.findall(linha, sub) if x[0].endswith(".tif")]
        reg["tempos_de_retorno"][n.strip("/")] = {"n_arquivos": len(arqs), "exemplo": arqs[0][0] if arqs else None}
        for bloco in a.blocos_glofas:
            achou = [(x, t) for x, _, t in arqs if bloco in x]
            if not achou:
                reg["nao_baixados"].append({"tr": n.strip("/"), "bloco": bloco, "motivo": "bloco ausente na pasta"})
            for x, t in achou:
                p = baixar(urljoin(urljoin(URL_GLOFAS, n), x), destino / x, a.forcar, fonte=f"GloFAS/JRC — flood hazard map, {n.strip('/')}", bloco=bloco,
                           tamanho_na_listagem=t)
                (reg["baixados"] if p else reg["nao_baixados"]).append({"tr": n.strip("/"), "arquivo": x, "tamanho_na_listagem": t})
    RESUMO["glofas"] = reg


def item_bater(a) -> None:
    destino = VETOR / "populacao-areas-de-risco_ibge_2018_bater.zip"
    p = baixar(URL_BATER, destino, a.forcar, fonte="IBGE — População em áreas de risco no Brasil (2018), base territorial estatística de áreas de risco (BATER)")
    reg = {"arquivo": destino.name, "baixado": bool(p)}
    if p:
        g = gpd.read_file(f"zip://{p}")
        cols = [c for c in g.columns if c != "geometry" and g[c].astype(str).str.fullmatch(r"\d{7}").mean() > 0.9]
        reg.update({"n_feicoes_total": len(g), "campos": [c for c in g.columns if c != "geometry"], "crs": str(g.crs),
                    "campos_com_codigo_de_municipio": cols,
                    "n_feicoes_do_municipio": int(max([(g[c].astype(str) == a.codigo_ibge).sum() for c in cols] or [0]))})
    RESUMO["bater"] = reg


def item_fepam(a) -> None:
    r = pedir(URL_FEPAM)
    pagina = DOCS / "pagina-arquivos-geoespaciais-maio-2024_fepam_pagina.html"
    reg = {"pagina": pagina.name, "http": r.status_code}
    if r.status_code == 200:
        if not pagina.exists() or a.forcar:
            pagina.parent.mkdir(parents=True, exist_ok=True)
            pagina.write_bytes(r.content)
            meta(pagina, fonte_url=URL_FEPAM, observacao="página salva como prova do que estava publicado")
        links = [urljoin(URL_FEPAM, h) for h in re.findall(r'href="([^"]+\.zip)"', r.text)]
        alvo = [h for h in links if re.search(r"atingida", h, re.I)]
        reg["arquivos_zip_na_pagina"] = links
        if alvo:
            p = baixar(alvo[0], VETOR / "area-diretamente-atingida_fepam_2024-05_vetorial.zip", a.forcar,
                       fonte="FEPAM-RS — arquivos geoespaciais dos eventos climáticos de maio de 2024: área diretamente atingida", pagina=URL_FEPAM)
            if p:
                g = gpd.read_file(f"zip://{p}")
                mun = carregar_area_estudo().to_crs(CRS_PADRAO).union_all()
                inter = g.to_crs(CRS_PADRAO)  # reprojeção explícita só para testar a interseção; o arquivo fica como veio
                toca = inter[inter.intersects(mun)]
                reg.update({"arquivo": p.name, "n_feicoes": len(g), "crs": str(g.crs), "campos": [c for c in g.columns if c != "geometry"],
                            "feicoes_que_cruzam_o_municipio": len(toca), "area_no_municipio_km2": float(toca.intersection(mun).area.sum() / 1e6)})
    RESUMO["fepam"] = reg


ITENS = {"risco": item_risco, "manchas": item_manchas, "glofas": item_glofas, "bater": item_bater, "fepam": item_fepam}


def main() -> None:
    global TEMPO_LIMITE_S, TENTATIVAS
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--codigo-ibge", required=True, help="código IBGE do município da área de estudo (config/area_estudo.geojson)")
    p.add_argument("--padrao-servicos", default="mancha", help="expressão regular para escolher os serviços da pasta de hidrologia do SGB")
    p.add_argument("--itens", nargs="*", default=list(ITENS), choices=list(ITENS))
    p.add_argument("--blocos-glofas", nargs="*", default=["ID85_S20_W60", "ID86_S30_W60"], help="blocos dos mapas globais a baixar")
    p.add_argument("--forcar", action="store_true")
    p.add_argument("--tempo-limite", type=int, default=TEMPO_LIMITE_S, help="tempo limite de cada pedido, em segundos")
    p.add_argument("--uma-tentativa", action="store_true", help="não repete o pedido que falhar (padrão: uma nova tentativa)")
    a = p.parse_args()
    TEMPO_LIMITE_S, TENTATIVAS = a.tempo_limite, 1 if a.uma_tentativa else 2
    for nome in a.itens:
        try:
            ITENS[nome](a)
        except (requests.RequestException, ValueError, KeyError) as erro:
            logger.error("item %s falhou: %s", nome, erro)
            RESUMO[nome] = {"erro": f"{type(erro).__name__}: {erro}"}
    resumo = VETOR / "inundacao-dados-espaciais_resumo-do-download.json"
    anterior = json.loads(resumo.read_text(encoding="utf-8")) if resumo.exists() else {}
    if isinstance(RESUMO.get("manchas"), list) and isinstance(anterior.get("manchas"), list):
        # serviços que não foram pedidos nesta execução continuam no resumo (o padrão pode ter escolhido só alguns)
        pedidos = {s["servico"] for s in RESUMO["manchas"]}
        RESUMO["manchas"] = sorted([s for s in anterior["manchas"] if s["servico"] not in pedidos] + RESUMO["manchas"], key=lambda s: s["servico"].lower())
    anterior.update(RESUMO)
    resumo.write_text(json.dumps(anterior, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(RESUMO, ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    main()
