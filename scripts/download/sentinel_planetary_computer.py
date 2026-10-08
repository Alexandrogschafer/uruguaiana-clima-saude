"""
Cenas Sentinel-1 (radar) e Sentinel-2 (óptico) do Microsoft Planetary Computer,
lidas só na janela da área de estudo, para mapear a água de cheias observadas em
imagens Sentinel e comparar com as manchas por cota do SGB.

Fonte e acesso (sem conta e sem chave):
  - catálogo STAC: https://planetarycomputer.microsoft.com/api/stac/v1
  - token anônimo de leitura, por coleção: .../api/sas/v1/token/<coleção>
  - arquivos (GeoTIFF otimizado para nuvem, COG) no armazenamento da coleção
O token só vive na memória: não é gravado em arquivo, log ou metadado; o
endereço registrado de cada arquivo é o do catálogo, sem o token.

Coleções:
  - sentinel-1-rtc  — retroespalhamento corrigido do terreno (gama zero), 10 m;
    ativos vv e vh;
  - sentinel-2-l2a  — reflectância de superfície; ativos B03, B08 (10 m), B11 e
    SCL (20 m). A SCL é lida primeiro: cena com nuvem (classes 8, 9 e 10) em
    --nuvem-max % ou mais da área de estudo fica só com a SCL.

Área de estudo: retângulo envolvente da união das manchas por cota do SGB
(--manchas), no CRS do projeto, com --margem-m de margem. Só entram cenas cuja
pegada no catálogo contém o retângulo inteiro. Cada arquivo é a janela da cena
que cobre o retângulo, na grade e no CRS originais da cena (nada é reamostrado
aqui; a reprojeção para o CRS do projeto é do roteiro de processamento).

Idempotente: arquivo já presente, com o mesmo identificador de cena e o mesmo
sha256 no .json irmão, não é lido de novo.

Saídas (fora do git): data/raw/sentinel/<sensor>/*.tif (+ .json) e a tabela
data/raw/sentinel/cenas_planetary-computer_<anos>_cena.csv (+ .json).

Passagens de radar em fatias (--fatias): o catálogo entrega algumas passagens em
duas fatias vizinhas, cada uma cobrindo só parte da área de estudo; sem essa
opção elas não são lidas. Com ela, o roteiro trata SÓ essas passagens: lê a
janela de um retângulo menor (--retangulo-geografico, com --margem-m) numa
fatia só, se ela bastar, ou nas duas, e junta (onde as duas têm valor, a média).
Um arquivo por passagem e por polarização, no mesmo padrão de nome, com a
lista das fatias no .json; a tabela das passagens fica em
data/raw/sentinel/passagens-em-fatias_planetary-computer_<anos>_passagem.csv.

Uso:
  python scripts/download/sentinel_planetary_computer.py
  python scripts/download/sentinel_planetary_computer.py --janelas J1=2019-01-05/2019-01-31
  python scripts/download/sentinel_planetary_computer.py --fatias --retangulo-geografico -29.8 -29.7333 -57.1333 -57.0333 --margem-m 500
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
import requests
from rasterio.warp import Resampling, reproject, transform_bounds
from rasterio.windows import Window, from_bounds
from shapely.geometry import box, shape

RAIZ = Path(__file__).resolve().parents[2]
sys.path.append(str(RAIZ / "scripts" / "utils"))
from recorte_municipio import CRS_PADRAO  # noqa: E402

logger = logging.getLogger(__name__)

SCRIPT = "scripts/download/sentinel_planetary_computer.py"
MOTIVO = "mapear a água de cheias observadas em imagens Sentinel e comparar com as manchas por cota do SGB"
STAC = "https://planetarycomputer.microsoft.com/api/stac/v1"
SAS = "https://planetarycomputer.microsoft.com/api/sas/v1/token"
BRUTO = RAIZ / "data" / "raw" / "sentinel"
ARQ_MANCHAS = RAIZ / "data" / "raw" / "vetor" / "cotas-inundacao_sgb_atual_vetorial.gpkg"
FUSO_LOCAL = timezone(timedelta(hours=-3))  # hora local da área de estudo (UTC−3, sem horário de verão desde 2019; ver --fuso-horas)
# sensor -> coleção, ativos a ler (o primeiro é o de conferência de cobertura) e prefixo do nome do arquivo
SENSORES = {
    "sentinel1-rtc": {"colecao": "sentinel-1-rtc", "ativos": ["vv", "vh"], "tema": "radar"},
    "sentinel2-l2a": {"colecao": "sentinel-2-l2a", "ativos": ["SCL", "B03", "B08", "B11"], "tema": "optico"},
}
SCL_NUVEM = (8, 9, 10)  # nuvem de probabilidade média e alta e cirrus, na classificação da própria cena
# períodos padrão: cheias registradas na régua do rio da área de estudo e um período de rio baixo (J0)
JANELAS_PADRAO = {
    "J1": ["2019-01-05/2019-01-31"], "J2": ["2017-05-10/2017-07-05"], "J3": ["2023-09-01/2023-12-15"], "J4": ["2024-04-15/2024-06-15"],
    "J5": ["2015-07-05/2015-08-10", "2015-12-05/2016-01-15"], "J0": ["2020-03-01/2020-06-30"],
}
MAX_CENAS_POR_JANELA = 50
MARGEM_PADRAO_M = 3000.0  # margem da área de estudo inteira (o retângulo das manchas)
CRS_GEOGRAFICO = "EPSG:4674"  # SIRGAS 2000, o das coordenadas de --retangulo-geografico
_TOKENS: dict[str, tuple[str, float]] = {}


def sem_token(texto: str) -> str:
    """Tira de uma mensagem qualquer parâmetro de endereço (onde o token vai)."""
    return re.sub(r"\?[^\s'\"]*", "?<removido>", str(texto))


def sha256(caminho: Path) -> str:
    h = hashlib.sha256()
    with open(caminho, "rb") as f:
        for bloco in iter(lambda: f.read(1 << 20), b""):
            h.update(bloco)
    return h.hexdigest()


def area_de_estudo(manchas: Path, margem_m: float) -> tuple[float, float, float, float]:
    """Retângulo envolvente da união das manchas, no CRS do projeto, com margem."""
    g = gpd.read_file(manchas).to_crs(CRS_PADRAO)  # as manchas podem vir em outro CRS: o retângulo é medido em metros
    x0, y0, x1, y1 = g.total_bounds
    return (float(x0 - margem_m), float(y0 - margem_m), float(x1 + margem_m), float(y1 + margem_m))


def pedir(metodo: str, url: str, tentativas: int = 5, **kw) -> requests.Response:
    """Pedido HTTP com nova tentativa em erro passageiro do servidor (5xx) ou de conexão."""
    for n in range(tentativas):
        try:
            r = requests.request(metodo, url, **kw)
            if r.status_code < 500:
                return r
            erro = f"HTTP {r.status_code}"
        except requests.RequestException as e:
            erro = type(e).__name__
        if n < tentativas - 1:
            logger.warning("%s em %s; nova tentativa em %d s", erro, sem_token(url), 10 * (n + 1))
            time.sleep(10 * (n + 1))
    raise RuntimeError(f"{erro} em {sem_token(url)} depois de {tentativas} tentativas")


def token(colecao: str) -> str:
    """Token anônimo de leitura da coleção (só na memória; renovado perto de vencer)."""
    tk, validade = _TOKENS.get(colecao, ("", 0.0))
    if time.time() > validade - 300:
        r = pedir("GET", f"{SAS}/{colecao}", timeout=60)
        if r.status_code in (401, 403):
            raise PermissionError(f"o serviço de token recusou o acesso anônimo à coleção {colecao} (HTTP {r.status_code})")
        r.raise_for_status()
        j = r.json()
        tk = j["token"]
        validade = datetime.fromisoformat(j["msft:expiry"].replace("Z", "+00:00")).timestamp()
        _TOKENS[colecao] = (tk, validade)
    return tk


def licenca(colecao: str) -> dict:
    """Licença da coleção, como o catálogo informa."""
    j = pedir("GET", f"{STAC}/collections/{colecao}", timeout=60).json()
    return {"colecao": colecao, "titulo": j.get("title"), "licenca": j.get("license"),
            "endereco_da_licenca": [x["href"] for x in j.get("links", []) if x.get("rel") == "license"], "provedores": [p.get("name") for p in j.get("providers", [])]}


def buscar(colecao: str, periodo: str, bbox_ll: tuple) -> list[dict]:
    """Itens da coleção que tocam o retângulo no período (AAAA-MM-DD/AAAA-MM-DD), com paginação."""
    ini, fim = periodo.split("/")
    corpo = {"collections": [colecao], "bbox": list(bbox_ll), "datetime": f"{ini}T00:00:00Z/{fim}T23:59:59Z", "limit": 200}
    url, itens = f"{STAC}/search", []
    while True:
        r = pedir("POST", url, json=corpo, timeout=120)
        r.raise_for_status()
        j = r.json()
        itens += j.get("features", [])
        seguinte = [x for x in j.get("links", []) if x.get("rel") == "next"]
        if not seguinte:
            return itens
        url, corpo = seguinte[0]["href"], seguinte[0].get("body", corpo)


def ler_janela(href: str, colecao: str, ret: tuple) -> tuple[np.ndarray, dict]:
    """Lê do COG só a janela que cobre o retângulo (CRS do projeto), na grade original da cena."""
    for tentativa in range(3):
        try:
            with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", GDAL_HTTP_MAX_RETRY="3", GDAL_HTTP_RETRY_DELAY="2"):
                with rasterio.open(f"{href}?{token(colecao)}") as src:
                    lim = transform_bounds(CRS_PADRAO, src.crs, *ret)  # retângulo no CRS da cena, antes de achar a janela
                    jan = from_bounds(*lim, transform=src.transform).round_offsets().round_lengths()
                    jan = jan.intersection(Window(0, 0, src.width, src.height))
                    dados = src.read(1, window=jan)
                    perfil = {"driver": "GTiff", "dtype": src.dtypes[0], "count": 1, "crs": src.crs, "transform": src.window_transform(jan),
                              "width": int(jan.width), "height": int(jan.height), "nodata": src.nodata, "compress": "deflate", "tiled": True,
                              "blockxsize": 256, "blockysize": 256, "predictor": 3 if np.issubdtype(dados.dtype, np.floating) else 2}
                    return dados, perfil
        except Exception as e:  # a mensagem do GDAL traz o endereço: nunca é propagada com o token
            if tentativa == 2:
                raise RuntimeError(sem_token(e)) from None
            time.sleep(5 * (tentativa + 1))
    raise RuntimeError("leitura não concluída")


def nome_do_arquivo(sensor: str, ativo: str, quando: datetime, res: float) -> Path:
    return BRUTO / sensor / f"{SENSORES[sensor]['tema']}-{ativo.lower()}_{sensor}_{quando:%Y%m%dT%H%M%SZ}_{res:g}m.tif"


def baixar_ativo(sensor: str, item: dict, ativo: str, ret: tuple, lic: dict) -> dict:
    """Um ativo de uma cena: lê a janela e grava GeoTIFF + .json; pula o que já existe com o mesmo identificador e sha256."""
    colecao = SENSORES[sensor]["colecao"]
    quando = datetime.fromisoformat(item["properties"]["datetime"].replace("Z", "+00:00"))
    href = item["assets"][ativo]["href"]
    for arq in (BRUTO / sensor).glob(f"{SENSORES[sensor]['tema']}-{ativo.lower()}_{sensor}_{quando:%Y%m%dT%H%M%SZ}_*m.tif"):
        meta = arq.with_suffix(".json")
        if meta.exists():
            m = json.loads(meta.read_text(encoding="utf-8"))
            if m.get("identificador_da_cena") == item["id"] and m.get("sha256") == sha256(arq):
                return {**m, "situacao": "já existia"}
    dados, perfil = ler_janela(href, colecao, ret)
    arq = nome_do_arquivo(sensor, ativo, quando, abs(perfil["transform"].a))
    arq.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(arq, "w", **perfil) as dst:
        dst.write(dados, 1)
    m = {"arquivo": arq.name, "fonte": f"Microsoft Planetary Computer — coleção {colecao}", "colecao": colecao, "identificador_da_cena": item["id"], "ativo": ativo,
         "endereco_do_arquivo": sem_token(href).replace("?<removido>", ""), "data_hora_da_cena_utc": quando.isoformat(), "data_hora_local": quando.astimezone(FUSO_LOCAL).isoformat(),
         "data_do_download": datetime.now(timezone.utc).isoformat(timespec="seconds"), "tamanho_bytes": arq.stat().st_size, "sha256": sha256(arq),
         "crs": str(perfil["crs"]), "resolucao_m": abs(perfil["transform"].a), "largura": perfil["width"], "altura": perfil["height"], "sem_dado": perfil["nodata"],
         "licenca": lic.get("licenca"), "endereco_da_licenca": lic.get("endereco_da_licenca"), "script": SCRIPT, "motivo": MOTIVO,
         "transformacao": "janela da cena que cobre a área de estudo, na grade e no CRS originais; sem reamostragem; GeoTIFF com compressão deflate",
         "retangulo_da_area_de_estudo": {"crs": CRS_PADRAO, "limites": list(ret)}}
    arq.with_suffix(".json").write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
    return {**m, "situacao": "lida", "_dados": dados, "_sem_dado": perfil["nodata"]}


def cobertura_e_nuvem(sensor: str, registro: dict) -> tuple[float, float | None]:
    """% da janela com dado e, no óptico, % de nuvem pela SCL (lê o arquivo gravado quando a cena já existia)."""
    dados = registro.get("_dados")
    if dados is None:
        with rasterio.open(BRUTO / sensor / registro["arquivo"]) as src:
            dados = src.read(1)
    if sensor.startswith("sentinel2"):
        return float(100 * (dados != 0).mean()), float(100 * np.isin(dados, SCL_NUVEM).mean())
    valido = np.isfinite(dados) & (dados != registro.get("sem_dado", registro.get("_sem_dado"))) & (dados > 0)
    return float(100 * valido.mean()), None


def retangulo_geografico(lat_sul: float, lat_norte: float, lon_oeste: float, lon_leste: float, margem_m: float) -> tuple[float, float, float, float]:
    """Retângulo dado em graus (SIRGAS 2000) -> limites no CRS do projeto, com margem em metros."""
    x0, y0, x1, y1 = transform_bounds(CRS_GEOGRAFICO, CRS_PADRAO, lon_oeste, lat_sul, lon_leste, lat_norte, densify_pts=21)
    return (x0 - margem_m, y0 - margem_m, x1 + margem_m, y1 + margem_m)


def ler_janela_cheia(href: str, colecao: str, ret: tuple) -> tuple[np.ndarray, dict]:
    """Como ler_janela, mas devolve a janela INTEIRA do retângulo: o que cai fora da fatia vem como sem dado."""
    for tentativa in range(3):
        try:
            with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", GDAL_HTTP_MAX_RETRY="3", GDAL_HTTP_RETRY_DELAY="2"):
                with rasterio.open(f"{href}?{token(colecao)}") as src:
                    lim = transform_bounds(CRS_PADRAO, src.crs, *ret)
                    jan = from_bounds(*lim, transform=src.transform).round_offsets().round_lengths()
                    dados = src.read(1, window=jan, boundless=True, fill_value=src.nodata)
                    perfil = {"driver": "GTiff", "dtype": src.dtypes[0], "count": 1, "crs": src.crs, "transform": src.window_transform(jan),
                              "width": int(jan.width), "height": int(jan.height), "nodata": src.nodata, "compress": "deflate", "tiled": True,
                              "blockxsize": 256, "blockysize": 256, "predictor": 3}
                    return dados, perfil
        except Exception as e:
            if tentativa == 2:
                raise RuntimeError(sem_token(e)) from None
            time.sleep(5 * (tentativa + 1))
    raise RuntimeError("leitura não concluída")


def juntar(partes: list[tuple[np.ndarray, dict]]) -> tuple[np.ndarray, dict, dict]:
    """Junta as janelas das fatias de uma passagem: onde mais de uma tem valor, a média."""
    base, perfil = partes[0]
    sem = perfil["nodata"]
    pilha = []
    for dados, p in partes:
        if p["transform"] != perfil["transform"] or dados.shape != base.shape:  # grades diferentes: a fatia vai para a grade da primeira
            dest = np.full(base.shape, sem, dtype=base.dtype)
            reproject(dados, dest, src_transform=p["transform"], src_crs=p["crs"], src_nodata=sem, dst_transform=perfil["transform"], dst_crs=perfil["crs"], dst_nodata=sem, resampling=Resampling.nearest)
            dados = dest
        pilha.append(np.where(np.isfinite(dados) & (dados != sem) & (dados > 0), dados, np.nan))
    pilha = np.stack(pilha)
    n = np.isfinite(pilha).sum(axis=0)
    with np.errstate(invalid="ignore"):
        junto = np.where(n > 0, np.nansum(pilha, axis=0) / np.maximum(n, 1), sem).astype(base.dtype)
    info = {"cobertura_de_cada_fatia_pct": [float(100 * np.isfinite(x).mean()) for x in pilha], "cobertura_da_juncao_pct": float(100 * (n > 0).mean()),
            "pixels_com_mais_de_uma_fatia_pct": float(100 * (n > 1).mean())}
    return junto, perfil, info


def fatias_de_radar() -> None:
    """Só as passagens de radar que vêm em fatias (nenhuma fatia contém a área de estudo inteira): janela do retângulo pedido, fatias unidas."""
    sensor = "sentinel1-rtc"
    colecao, ativos = SENSORES[sensor]["colecao"], SENSORES[sensor]["ativos"]
    inteira = area_de_estudo(ARGS.manchas, MARGEM_PADRAO_M)
    ret = retangulo_geografico(*ARGS.retangulo_geografico, ARGS.margem_m) if ARGS.retangulo_geografico else area_de_estudo(ARGS.manchas, ARGS.margem_m)
    alvo_inteira, alvo = box(*transform_bounds(CRS_PADRAO, "EPSG:4326", *inteira)), box(*transform_bounds(CRS_PADRAO, "EPSG:4326", *ret))
    lic = licenca(colecao)
    token(colecao)
    passagens: dict[tuple, dict] = {}
    for janela, periodos in ARGS.janelas.items():
        itens = {i["id"]: i for p in periodos for i in buscar(colecao, p, alvo_inteira.bounds)}
        for i in sorted(itens.values(), key=lambda i: i["properties"]["datetime"]):
            if shape(i["geometry"]).contains(alvo_inteira):
                continue  # cena inteira: é do modo padrão
            chave = (i["properties"].get("platform"), i["properties"].get("sat:absolute_orbit"))
            passagens.setdefault(chave, {"janela": janela, "itens": []})["itens"].append(i)
    def ordem(p):  # prioridade das leituras, quando o teto de arquivos aperta
        d = p["itens"][0]["properties"]["datetime"][:10]
        return (0 if d in ARGS.primeiro else 1, {"J2": 0, "J3": 1, "J4": 2}.get(p["janela"], 3), d)
    linhas, novos = [], 0
    for p in sorted(passagens.values(), key=ordem):
        itens = p["itens"]
        prop = itens[0]["properties"]
        quando = datetime.fromisoformat(prop["datetime"].replace("Z", "+00:00"))
        toca = [i for i in itens if shape(i["geometry"]).intersects(alvo)]
        sozinha = [i for i in toca if shape(i["geometry"]).contains(alvo)]
        usar = sozinha[:1] or toca
        linha = {"janela": p["janela"], "data_hora_utc": quando.strftime("%Y-%m-%d %H:%M:%S"), "data_hora_local": quando.astimezone(FUSO_LOCAL).strftime("%Y-%m-%d %H:%M:%S"),
                 "plataforma": prop.get("platform"), "orbita_absoluta": prop.get("sat:absolute_orbit"), "orbita_relativa": prop.get("sat:relative_orbit"), "direcao": prop.get("sat:orbit_state"),
                 "polarizacoes": "+".join(prop.get("sar:polarizations", [])), "fatias_no_catalogo": len(itens), "identificadores": "; ".join(i["id"] for i in itens),
                 "fatias_lidas": len(usar), "uma_fatia_basta": bool(sozinha), "cobertura_do_retangulo_pct": np.nan, "pixels_com_duas_fatias_pct": np.nan, "situacao": "", "arquivos": ""}
        if not usar:
            linha["situacao"] = "não lida: nenhuma fatia toca o retângulo"
        elif novos + len(ativos) > ARGS.max_arquivos:
            linha["situacao"] = f"não lida: teto de {ARGS.max_arquivos} arquivos novos"
        else:
            try:
                feitos = []
                for ativo in ativos:
                    arq = nome_do_arquivo(sensor, ativo, quando, 10)
                    meta = arq.with_suffix(".json")
                    if arq.exists() and meta.exists() and (m := json.loads(meta.read_text(encoding="utf-8"))).get("identificadores_das_cenas") == [i["id"] for i in usar] and m.get("sha256") == sha256(arq):
                        feitos.append({**m, "situacao": "já existia"})
                        continue
                    with ThreadPoolExecutor(max_workers=len(usar)) as pool:
                        partes = list(pool.map(lambda i: ler_janela_cheia(i["assets"][ativo]["href"], colecao, ret), usar))
                    dados, perfil, info = juntar(partes)
                    arq.parent.mkdir(parents=True, exist_ok=True)
                    with rasterio.open(arq, "w", **perfil) as dst:
                        dst.write(dados, 1)
                    m = {"arquivo": arq.name, "fonte": f"Microsoft Planetary Computer — coleção {colecao}", "colecao": colecao, "identificadores_das_cenas": [i["id"] for i in usar], "ativo": ativo,
                         "enderecos_dos_arquivos": [sem_token(i["assets"][ativo]["href"]).replace("?<removido>", "") for i in usar], "fatias_unidas": len(usar) > 1,
                         "regra_da_juncao": "onde mais de uma fatia tem valor, a média; onde só uma tem, o valor dela" if len(usar) > 1 else "uma fatia cobre o retângulo inteiro",
                         **info, "data_hora_da_cena_utc": quando.isoformat(), "data_hora_local": quando.astimezone(FUSO_LOCAL).isoformat(),
                         "data_do_download": datetime.now(timezone.utc).isoformat(timespec="seconds"), "tamanho_bytes": arq.stat().st_size, "sha256": sha256(arq), "crs": str(perfil["crs"]),
                         "resolucao_m": abs(perfil["transform"].a), "largura": perfil["width"], "altura": perfil["height"], "sem_dado": perfil["nodata"], "licenca": lic.get("licenca"),
                         "endereco_da_licenca": lic.get("endereco_da_licenca"), "script": SCRIPT, "motivo": MOTIVO, "retangulo_lido": {"crs": CRS_PADRAO, "limites": list(ret)},
                         "transformacao": "janela do retângulo na grade e no CRS originais da cena; sem reamostragem quando as fatias estão na mesma grade"}
                    meta.write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
                    feitos.append({**m, "situacao": "lida"})
                    novos += 1
                linha.update(cobertura_do_retangulo_pct=feitos[0]["cobertura_da_juncao_pct"], pixels_com_duas_fatias_pct=feitos[0]["pixels_com_mais_de_uma_fatia_pct"],
                             situacao="lida" if any(f["situacao"] == "lida" for f in feitos) else "já existia", arquivos="; ".join(f["arquivo"] for f in feitos),
                             _bytes=sum(f["tamanho_bytes"] for f in feitos))
            except Exception as e:
                linha["situacao"] = f"erro: {sem_token(e)[:200]}"
        logger.info("%s %s (órbita %s, %d fatia(s)): %s", linha["janela"], linha["data_hora_utc"], linha["orbita_relativa"], len(itens), linha["situacao"])
        linhas.append(linha)
    t = pd.DataFrame(linhas).sort_values("data_hora_utc").reset_index(drop=True)
    total = int(t.get("_bytes", pd.Series(dtype=float)).fillna(0).sum())
    t = t.drop(columns=[x for x in ("_bytes",) if x in t])
    anos = sorted({int(x) for ps in ARGS.janelas.values() for p in ps for x in (p[:4], p.split("/")[1][:4])})
    arq = BRUTO / f"passagens-em-fatias_planetary-computer_{anos[0]}-{anos[-1]}_passagem.csv"
    t.to_csv(arq, index=False)
    arq.with_suffix(".json").write_text(json.dumps({
        "arquivo": arq.name, "fonte": "Microsoft Planetary Computer — catálogo STAC, acesso anônimo", "script": SCRIPT, "motivo": MOTIVO, "data_acesso": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "colecao": lic, "retangulo_lido": {"crs": CRS_PADRAO, "limites": list(ret), "retangulo_geografico": ARGS.retangulo_geografico, "crs_do_retangulo_geografico": CRS_GEOGRAFICO, "margem_m": ARGS.margem_m},
        "area_de_estudo_inteira": {"crs": CRS_PADRAO, "limites": list(inteira)}, "janelas": ARGS.janelas, "teto_de_arquivos_novos": ARGS.max_arquivos, "arquivos_novos": novos, "bytes_dos_arquivos": total,
        "o_que_e_passagem_em_fatias": "itens da mesma plataforma e da mesma órbita absoluta em que nenhuma pegada contém a área de estudo inteira",
        "campos": {"janela": "período de busca", "data_hora_utc": "início da primeira fatia, UTC", "data_hora_local": "o mesmo, em UTC−3", "plataforma": "satélite", "orbita_absoluta": "órbita absoluta",
                   "orbita_relativa": "órbita relativa", "direcao": "direção da órbita", "polarizacoes": "polarizações", "fatias_no_catalogo": "itens da passagem no catálogo",
                   "identificadores": "identificadores das fatias", "fatias_lidas": "fatias lidas", "uma_fatia_basta": "a pegada de uma fatia contém o retângulo inteiro",
                   "cobertura_do_retangulo_pct": "% do retângulo com dado depois da junção", "pixels_com_duas_fatias_pct": "% do retângulo em que duas fatias têm valor (média)",
                   "situacao": "o que foi feito", "arquivos": "arquivos gravados"}}, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    logger.info("Passagens em fatias: %s (%d passagens; %d arquivos novos; %.1f MB)", arq.relative_to(RAIZ), len(t), novos, total / 1e6)


def main() -> None:
    BRUTO.mkdir(parents=True, exist_ok=True)
    if ARGS.fatias:
        fatias_de_radar()
        return
    ret = area_de_estudo(ARGS.manchas, ARGS.margem_m)
    bbox_ll = transform_bounds(CRS_PADRAO, "EPSG:4326", *ret)  # o catálogo é consultado em coordenadas geográficas
    alvo = box(*bbox_ll)
    logger.info("Área de estudo (%s): %s", CRS_PADRAO, [round(v, 1) for v in ret])
    linhas, avisos, licencas = [], [], {}
    for sensor in ARGS.sensores:
        cfg = SENSORES[sensor]
        colecao = cfg["colecao"]
        licencas[sensor] = lic = licenca(colecao)
        token(colecao)  # falha aqui se o acesso anônimo for recusado
        for janela, periodos in ARGS.janelas.items():
            itens = {i["id"]: i for p in periodos for i in buscar(colecao, p, bbox_ll)}
            itens = sorted(itens.values(), key=lambda i: i["properties"]["datetime"])
            cobrem = [i for i in itens if shape(i["geometry"]).contains(alvo)]
            # o mesmo imageamento pode ter mais de um processamento no catálogo: fica o mais recente (maior identificador)
            ultimo = {}
            for i in cobrem:
                chave = (i["properties"]["datetime"][:19], i["properties"].get("s2:mgrs_tile", ""))
                if chave not in ultimo or i["id"] > ultimo[chave]["id"]:
                    ultimo[chave] = i
            usar = sorted(ultimo.values(), key=lambda i: i["properties"]["datetime"])
            logger.info("%s %s: %d itens no catálogo, %d cobrem a área inteira, %d imageamentos distintos", sensor, janela, len(itens), len(cobrem), len(usar))
            demais = len(usar) > MAX_CENAS_POR_JANELA
            if demais:
                avisos.append(f"{sensor} {janela}: {len(usar)} cenas (mais de {MAX_CENAS_POR_JANELA}); a leitura da janela não foi feita")
            for i in itens:
                p = i["properties"]
                quando = datetime.fromisoformat(p["datetime"].replace("Z", "+00:00"))
                linha = {"sensor": sensor, "colecao": colecao, "janela": janela, "identificador": i["id"], "data_hora_utc": quando.strftime("%Y-%m-%d %H:%M:%S"),
                         "data_hora_local": quando.astimezone(FUSO_LOCAL).strftime("%Y-%m-%d %H:%M:%S"), "plataforma": p.get("platform"),
                         "orbita_relativa": p.get("sat:relative_orbit"), "direcao": p.get("sat:orbit_state"), "polarizacoes": "+".join(p.get("sar:polarizations", [])) or "",
                         "quadricula": p.get("s2:mgrs_tile", ""), "versao_do_processamento": p.get("s2:processing_baseline", ""),
                         "nuvem_no_catalogo_pct": p.get("eo:cloud_cover"), "pegada_contem_a_area": i in cobrem, "cobertura_da_area_pct": np.nan, "nuvem_na_area_scl_pct": np.nan,
                         "situacao": "", "arquivos": ""}
                if i not in cobrem:
                    linha["situacao"] = "não lida: a pegada da cena não contém a área inteira"
                elif i not in usar:
                    linha["situacao"] = "não lida: há processamento mais recente do mesmo imageamento"
                elif demais:
                    linha["situacao"] = f"não lida: a janela tem mais de {MAX_CENAS_POR_JANELA} cenas"
                linhas.append((linha, i if (i in usar and not demais) else None))

    def tratar(par):
        linha, item = par
        if item is None:
            return linha
        sensor = linha["sensor"]
        ativos = SENSORES[sensor]["ativos"]
        try:
            primeiro = baixar_ativo(sensor, item, ativos[0], ret, licencas[sensor])
            linha["cobertura_da_area_pct"], nuvem = cobertura_e_nuvem(sensor, primeiro)
            feitos = [primeiro]
            if nuvem is not None:
                linha["nuvem_na_area_scl_pct"] = nuvem
            if nuvem is not None and nuvem >= ARGS.nuvem_max:
                linha["situacao"] = f"só a SCL: nuvem em {ARGS.nuvem_max:g} % ou mais da área"
            else:
                feitos += [baixar_ativo(sensor, item, a, ret, licencas[sensor]) for a in ativos[1:]]
                linha["situacao"] = "lida" if any(f["situacao"] == "lida" for f in feitos) else "já existia"
            linha["arquivos"] = "; ".join(f["arquivo"] for f in feitos)
            linha["_bytes_lidos"] = sum(f["tamanho_bytes"] for f in feitos if f["situacao"] == "lida")
            linha["_bytes"] = sum(f["tamanho_bytes"] for f in feitos)
        except Exception as e:
            linha["situacao"] = f"erro: {sem_token(e)[:200]}"
            logger.error("%s: %s", linha["identificador"], sem_token(e)[:300])
        logger.info("%s %s %s: %s", sensor, linha["janela"], linha["data_hora_utc"], linha["situacao"])
        return linha

    with ThreadPoolExecutor(max_workers=ARGS.paralelo) as pool:
        feitas = list(pool.map(tratar, linhas))
    t = pd.DataFrame(feitas)
    lidos, total = int(t.get("_bytes_lidos", pd.Series(dtype=float)).fillna(0).sum()), int(t.get("_bytes", pd.Series(dtype=float)).fillna(0).sum())
    t = t.drop(columns=[x for x in ("_bytes_lidos", "_bytes") if x in t]).sort_values(["sensor", "data_hora_utc", "identificador"]).reset_index(drop=True)
    anos = sorted({int(p[:4]) for ps in ARGS.janelas.values() for p in ps} | {int(p.split("/")[1][:4]) for ps in ARGS.janelas.values() for p in ps})
    arq = BRUTO / f"cenas_planetary-computer_{anos[0]}-{anos[-1]}_cena.csv"
    t.to_csv(arq, index=False)
    meta = {"arquivo": arq.name, "fonte": "Microsoft Planetary Computer — catálogo STAC, acesso anônimo", "endereco_consultado": f"{STAC}/search", "script": SCRIPT, "motivo": MOTIVO,
            "data_acesso": datetime.now(timezone.utc).isoformat(timespec="seconds"), "colecoes": licencas,
            "area_de_estudo": {"crs": CRS_PADRAO, "limites": list(ret), "margem_m": ARGS.margem_m, "manchas": str(ARGS.manchas.relative_to(RAIZ)) if ARGS.manchas.is_relative_to(RAIZ) else ARGS.manchas.name,
                               "limites_geograficos": list(bbox_ll)},
            "janelas": ARGS.janelas, "nuvem_max_pct": ARGS.nuvem_max, "classes_scl_de_nuvem": list(SCL_NUVEM), "fuso_da_hora_local": "UTC−3",
            "cenas_por_situacao": t.situacao.str.split(":").str[0].value_counts().to_dict(), "bytes_lidos_nesta_execucao": lidos, "bytes_dos_arquivos_das_cenas": total, "avisos": avisos,
            "campos": {"sensor": "sensor e produto", "colecao": "coleção no catálogo", "janela": "período de busca", "identificador": "identificador da cena no catálogo",
                       "data_hora_utc": "data e hora do imageamento, UTC", "data_hora_local": "a mesma, em UTC−3", "plataforma": "satélite", "orbita_relativa": "órbita relativa",
                       "direcao": "direção da órbita (ascending/descending)", "polarizacoes": "polarizações do radar", "quadricula": "quadrícula do óptico",
                       "versao_do_processamento": "versão do processamento do óptico (a partir da 04.00 os valores trazem deslocamento de 1000)",
                       "nuvem_no_catalogo_pct": "nuvem da cena inteira, segundo o catálogo", "pegada_contem_a_area": "a pegada da cena no catálogo contém o retângulo inteiro",
                       "cobertura_da_area_pct": "% da janela lida com dado", "nuvem_na_area_scl_pct": "% da janela com nuvem, pela SCL", "situacao": "o que foi feito com a cena",
                       "arquivos": "arquivos gravados da cena"}}
    arq.with_suffix(".json").write_text(json.dumps(meta, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    logger.info("Tabela de cenas: %s (%d linhas); %.1f MB lidos nesta execução; %.1f MB no total", arq.relative_to(RAIZ), len(t), lidos / 1e6, total / 1e6)
    for a in avisos:
        logger.warning(a)


def _janelas(valores: list[str] | None) -> dict:
    if not valores:
        return JANELAS_PADRAO
    out: dict[str, list[str]] = {}
    for v in valores:
        nome, periodo = v.split("=", 1)
        out.setdefault(nome, []).append(periodo)
    return out


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    logging.getLogger("rasterio").setLevel(logging.ERROR)
    _p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    _p.add_argument("--manchas", type=Path, default=ARQ_MANCHAS, help="camada das manchas por cota que define a área de estudo")
    _p.add_argument("--margem-m", type=float, default=MARGEM_PADRAO_M, help="margem em torno do retângulo das manchas (m); com --fatias, margem do retângulo lido")
    _p.add_argument("--janelas", nargs="*", help="períodos NOME=AAAA-MM-DD/AAAA-MM-DD (o nome pode se repetir); padrão: os períodos de cheia e de rio baixo do roteiro")
    _p.add_argument("--sensores", nargs="+", default=list(SENSORES), choices=list(SENSORES))
    _p.add_argument("--nuvem-max", type=float, default=30.0, help="o óptico só é lido inteiro com nuvem abaixo disto (%% da área, pela SCL)")
    _p.add_argument("--paralelo", type=int, default=4, help="cenas lidas ao mesmo tempo")
    _p.add_argument("--fatias", action="store_true", help="trata só as passagens de radar entregues em fatias (ver o texto no topo); sem esta opção nada muda")
    _p.add_argument("--retangulo-geografico", type=float, nargs=4, metavar=("LAT_SUL", "LAT_NORTE", "LON_OESTE", "LON_LESTE"),
                    help="com --fatias: retângulo a ler, em graus decimais (SIRGAS 2000); padrão: o retângulo das manchas")
    _p.add_argument("--max-arquivos", type=int, default=120, help="com --fatias: teto de arquivos novos")
    _p.add_argument("--primeiro", nargs="*", default=[], help="com --fatias: datas (AAAA-MM-DD, UTC) lidas antes das outras")
    ARGS = _p.parse_args()
    ARGS.janelas = _janelas(ARGS.janelas)
    main()
