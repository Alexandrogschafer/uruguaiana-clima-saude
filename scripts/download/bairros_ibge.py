"""
Baixa a malha de bairros do Censo 2022 (IBGE) da UF do município e grava os
bairros do município de referência.

Entradas e saídas:
  data/raw/vetor/bairros_ibge-censo_2022_uf-<uf>.zip      como a fonte serve (UF inteira)
  data/raw/vetor/bairros_ibge-censo_2022_uf-<uf>.json     endereço, data, tamanho, sha256
  data/processed/bairros/bairros_ibge_2022_vetorial.gpkg  bairros do município (EPSG:31981)
  data/processed/bairros/bairros_ibge_2022_vetorial.json  metadado e conferência

Campos do produto: código e nome do bairro como no IBGE; área (km²); número de
setores e população de 2022 (soma dos setores censitários do projeto com aquele
código de bairro); endereços de domicílio particular dentro do bairro (pontos
do estudo de exposição, se o arquivo existir).

Conferência independente: os setores do projeto são dissolvidos pelo código do
bairro e comparados com a malha (nomes e diferença simétrica por bairro). Se o
download falhar, o produto sai dessa dissolução, e o metadado registra isso.

Tudo fica FORA do git (data/raw/ e data/processed/). Idempotente: o que já
existe não é baixado nem regravado (use --forcar). Um pedido só ao servidor,
com User-Agent identificado; se o servidor recusar, não há nova tentativa.

Uso:
  python scripts/download/bairros_ibge.py                  (código lido de config/area_estudo.geojson)
  python scripts/download/bairros_ibge.py --codigo-ibge CODIGO
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import requests

sys.path.append(str(Path(__file__).resolve().parents[1] / "utils"))
from recorte_municipio import CAMINHO_AREA_ESTUDO_PADRAO, CRS_PADRAO  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

RAIZ = Path(__file__).resolve().parents[2]
USER_AGENT = "uruguaiana-clima-saude (pesquisa academica)"
TEMPO_LIMITE_S = 120
URL = ("https://geoftp.ibge.gov.br/organizacao_do_territorio/malhas_territoriais/"
       "malhas_de_setores_censitarios__divisoes_intramunicipais/censo_2022/bairros/shp/UF/{uf}_bairros_CD2022.zip")
FONTE = "IBGE — Malha de Bairros do Censo Demográfico 2022 (Divisões Intramunicipais), shapefile por UF"
# dois primeiros dígitos do código do município -> sigla da UF (é a sigla que entra no endereço)
UF_POR_CODIGO = {"11": "RO", "12": "AC", "13": "AM", "14": "RR", "15": "PA", "16": "AP", "17": "TO", "21": "MA", "22": "PI",
                 "23": "CE", "24": "RN", "25": "PB", "26": "PE", "27": "AL", "28": "SE", "29": "BA", "31": "MG", "32": "ES",
                 "33": "RJ", "35": "SP", "41": "PR", "42": "SC", "43": "RS", "50": "MS", "51": "MT", "52": "GO", "53": "DF"}
SETORES = RAIZ / "data" / "processed" / "dinamica_populacional" / "populacao-setores_ibge-censo_2022_setor.gpkg"
PONTOS = RAIZ / "data" / "processed" / "exposicao_inundacao" / "enderecos-exposicao-inundacao_sgb-ibge-cnefe_2022_pontos.gpkg"
SAIDA = RAIZ / "data" / "processed" / "bairros" / "bairros_ibge_2022_vetorial.gpkg"


def codigo_da_area_de_estudo() -> str:
    """Código do município gravado em config/area_estudo.geojson (propriedade codarea)."""
    d = json.loads(CAMINHO_AREA_ESTUDO_PADRAO.read_text(encoding="utf-8"))
    return str(d["features"][0]["properties"]["codarea"])


def baixar(uf: str, forcar: bool) -> Path | None:
    """Baixa o zip da UF. Devolve o caminho, ou None se o servidor não entregou (sem nova tentativa)."""
    destino = RAIZ / "data" / "raw" / "vetor" / f"bairros_ibge-censo_2022_uf-{uf.lower()}.zip"
    if destino.exists() and not forcar:
        logger.info("já existe: %s (%d bytes)", destino.relative_to(RAIZ), destino.stat().st_size)
        return destino
    url = URL.format(uf=uf)
    try:
        r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=TEMPO_LIMITE_S)
    except requests.RequestException as erro:
        logger.warning("sem resposta de %s (%s) — sem nova tentativa", url, erro)
        return None
    if r.status_code != 200 or not r.content[:2] == b"PK":
        logger.warning("o servidor não entregou o arquivo: HTTP %d em %s — sem nova tentativa", r.status_code, url)
        return None
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_bytes(r.content)
    destino.with_suffix(".json").write_text(json.dumps({
        "arquivo": destino.name, "fonte": FONTE, "fonte_url": url,
        "data_acesso": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "tamanho_bytes": len(r.content), "sha256": hashlib.sha256(r.content).hexdigest(),
        "tipo_de_conteudo_do_servidor": r.headers.get("Content-Type"),
        "conteudo_do_zip": zipfile.ZipFile(destino).namelist(),
        "observacao": "arquivo da UF inteira, como a fonte serve; o recorte do município é feito no produto"},
        ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("baixado: %s — fonte %s — %d bytes", destino.relative_to(RAIZ), url, len(r.content))
    return destino


def dissolver_setores(setores: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Um polígono por bairro, pela união dos setores que têm aquele código de bairro."""
    s = setores[setores.CD_BAIRRO.notna()]
    return s.dissolve(by="CD_BAIRRO", aggfunc={"NM_BAIRRO": "first"}).reset_index()


def main() -> None:
    p = argparse.ArgumentParser(description="Malha de bairros do Censo 2022 (IBGE) para um município.")
    p.add_argument("--codigo-ibge", help="código IBGE do município (padrão: o de config/area_estudo.geojson)")
    p.add_argument("--forcar", action="store_true", help="baixa e regrava mesmo que já exista")
    a = p.parse_args()
    codigo = a.codigo_ibge or codigo_da_area_de_estudo()
    uf = UF_POR_CODIGO[codigo[:2]]
    if SAIDA.exists() and not a.forcar:
        logger.info("já existe (use --forcar para refazer): %s", SAIDA.relative_to(RAIZ))
        return
    if not SETORES.exists():
        raise FileNotFoundError(f"{SETORES.relative_to(RAIZ)} ausente — rode scripts/processamento/dinamica_populacional_2022.py")
    # setores do projeto, já em EPSG:31981 (reprojeta se não estiverem, antes de qualquer operação espacial)
    setores = gpd.read_file(SETORES).to_crs(CRS_PADRAO)
    setores = setores[setores.CD_SETOR.astype(str).str.startswith(codigo)]
    diss = dissolver_setores(setores)

    arq = baixar(uf, a.forcar)
    campos_da_fonte, crs_origem = None, None
    if arq is not None:
        uf_inteira = gpd.read_file(f"zip://{arq}")
        campos_da_fonte, crs_origem = [c for c in uf_inteira.columns if c != "geometry"], str(uf_inteira.crs)
        b = uf_inteira[uf_inteira.CD_MUN.astype(str) == codigo].to_crs(CRS_PADRAO)[["CD_BAIRRO", "NM_BAIRRO", "geometry"]]
        origem = "malha de bairros do IBGE (Censo 2022), filtrada pelo código do município e reprojetada para EPSG:31981"
        logger.info("malha da UF: %d bairros; no município %s: %d", len(uf_inteira), codigo, len(b))
    else:
        b = diss[["CD_BAIRRO", "NM_BAIRRO", "geometry"]].copy()
        origem = "DISSOLUÇÃO dos setores censitários do projeto pelo código do bairro (o download da malha de bairros falhou)"
        logger.warning("produto gerado pela dissolução dos setores")
    b["CD_BAIRRO"] = b.CD_BAIRRO.astype(str)
    b = b.sort_values("NM_BAIRRO").reset_index(drop=True)
    b["area_km2"] = (b.area / 1e6).round(4)  # área na projeção de trabalho (UTM 21S)

    por_bairro = setores[setores.CD_BAIRRO.notna()].assign(CD_BAIRRO=lambda d: d.CD_BAIRRO.astype(str)).groupby("CD_BAIRRO")
    b["setores_2022"] = b.CD_BAIRRO.map(por_bairro.size()).fillna(0).astype(int)
    b["populacao_2022"] = b.CD_BAIRRO.map(por_bairro["pop"].sum()).fillna(0).astype(int)
    enderecos_total = None
    if PONTOS.exists():
        pts = gpd.read_file(PONTOS, columns=["COD_UNICO_ENDERECO"]).to_crs(CRS_PADRAO)
        dentro = gpd.sjoin(pts, b[["CD_BAIRRO", "geometry"]], predicate="within", how="inner")
        b["enderecos_domicilio_particular"] = b.CD_BAIRRO.map(dentro.groupby("CD_BAIRRO").size()).fillna(0).astype(int)
        enderecos_total = int(len(pts))
    else:
        b["enderecos_domicilio_particular"] = None
        logger.warning("pontos de endereço ausentes (%s): campo de endereços fica vazio", PONTOS.relative_to(RAIZ))

    # conferência independente: malha de bairros x dissolução dos setores
    d = diss.assign(CD_BAIRRO=lambda x: x.CD_BAIRRO.astype(str)).set_index("CD_BAIRRO")
    conf = []
    for _, r in b.iterrows():
        if r.CD_BAIRRO not in d.index:
            conf.append({"CD_BAIRRO": r.CD_BAIRRO, "NM_BAIRRO": r.NM_BAIRRO, "nos_setores": False})
            continue
        g = d.loc[r.CD_BAIRRO, "geometry"]
        conf.append({"CD_BAIRRO": r.CD_BAIRRO, "NM_BAIRRO": r.NM_BAIRRO, "nos_setores": True,
                     "mesmo_nome": r.NM_BAIRRO == d.loc[r.CD_BAIRRO, "NM_BAIRRO"],
                     "area_dos_setores_km2": round(g.area / 1e6, 4),
                     "diferenca_simetrica_pct": round(100 * r.geometry.symmetric_difference(g).area / r.geometry.area, 4)})
    so_nos_setores = sorted(set(d.index) - set(b.CD_BAIRRO))
    sem = setores[setores.CD_BAIRRO.isna()]

    SAIDA.parent.mkdir(parents=True, exist_ok=True)
    if SAIDA.exists():
        SAIDA.unlink()
    b.to_file(SAIDA, layer="bairros", driver="GPKG")
    SAIDA.with_suffix(".json").write_text(json.dumps({
        "produto": SAIDA.name, "codigo_ibge": codigo, "uf": uf, "crs": CRS_PADRAO, "fonte": FONTE, "fonte_url": URL.format(uf=uf),
        "arquivo_bruto": str(arq.relative_to(RAIZ)) if arq else None, "crs_origem": crs_origem, "campos_da_fonte": campos_da_fonte,
        "transformacao_aplicada": origem,
        "campos": {"CD_BAIRRO": "código do bairro (IBGE)", "NM_BAIRRO": "nome do bairro (IBGE)",
                   "area_km2": "área do polígono em EPSG:31981",
                   "setores_2022": "setores censitários do projeto com aquele código de bairro",
                   "populacao_2022": "soma da população dos setores do bairro (Censo 2022)",
                   "enderecos_domicilio_particular": "pontos de endereço de domicílio particular (CNEFE 2022) dentro do polígono"},
        "bairros": int(len(b)), "setores_com_bairro": int(b.setores_2022.sum()), "populacao_nos_bairros": int(b.populacao_2022.sum()),
        "area_dos_bairros_km2": round(float(b.area.sum()) / 1e6, 2),
        "setores_sem_bairro": {"setores": int(len(sem)), "populacao": int(sem["pop"].sum()),
                               "por_distrito": {str(k): int(v) for k, v in sem.groupby("NM_DIST").size().items()}},
        "enderecos_no_arquivo_de_pontos": enderecos_total,
        "enderecos_dentro_de_bairro": int(b.enderecos_domicilio_particular.sum()) if enderecos_total is not None else None,
        "conferencia_contra_a_dissolucao_dos_setores": {"bairros_so_nos_setores": so_nos_setores, "por_bairro": conf},
        "status": "fora do portal; publicação a decidir",
        "data_processamento": datetime.now(timezone.utc).isoformat(timespec="seconds")},
        ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    logger.info("gravado: %s — %d bairros, %d setores, %d habitantes, %.2f km²", SAIDA.relative_to(RAIZ), len(b),
                b.setores_2022.sum(), b.populacao_2022.sum(), b.area.sum() / 1e6)


if __name__ == "__main__":
    main()
