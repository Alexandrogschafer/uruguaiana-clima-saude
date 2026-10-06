"""
Baixa as Áreas Urbanizadas do Brasil 2019 (IBGE) e grava o recorte do
município de referência.

Entradas e saídas (tudo em data/raw/vetor/, FORA do git):
  areas-urbanizadas_ibge_2019_brasil.zip      como a fonte serve (país inteiro)
  areas-urbanizadas_ibge_2019_brasil.json     endereço, data, tamanho, sha256
  areas-urbanizadas_ibge_2019_recorte-municipio.gpkg   polígonos dentro do limite municipal (EPSG:31981)
  areas-urbanizadas_ibge_2019_recorte-municipio.json   metadado do recorte

Recorte: polígonos da fonte que tocam o limite de config/area_estudo.geojson,
cortados por ele (interseção). A fonte vem em coordenadas geográficas; a
reprojeção para EPSG:31981 é feita antes do corte e do cálculo de área. Todos
os atributos da fonte são mantidos; entra a coluna area_km2 (área da parte
dentro do município).

Idempotente: o que já existe não é baixado nem regravado (use --forcar). Um
pedido só ao servidor, com User-Agent identificado; se o servidor recusar,
não há nova tentativa e nada é gravado.

Uso:
  python scripts/download/areas_urbanizadas_ibge.py
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
from recorte_municipio import CAMINHO_AREA_ESTUDO_PADRAO, CRS_PADRAO, carregar_area_estudo  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

RAIZ = Path(__file__).resolve().parents[2]
USER_AGENT = "pesquisa academica (script de download de dados publicos)"
TEMPO_LIMITE_S = 600
URL = ("https://geoftp.ibge.gov.br/organizacao_do_territorio/tipologias_do_territorio/"
       "areas_urbanizadas_do_brasil/2019/Shapefile/AreasUrbanizadas2019_Brasil.zip")
FONTE = "IBGE — Áreas Urbanizadas do Brasil 2019, shapefile do país inteiro"
PASTA = RAIZ / "data" / "raw" / "vetor"
BRUTO = PASTA / "areas-urbanizadas_ibge_2019_brasil.zip"
RECORTE = PASTA / "areas-urbanizadas_ibge_2019_recorte-municipio.gpkg"


def codigo_da_area_de_estudo() -> str:
    """Código do município gravado em config/area_estudo.geojson (propriedade codarea)."""
    d = json.loads(CAMINHO_AREA_ESTUDO_PADRAO.read_text(encoding="utf-8"))
    return str(d["features"][0]["properties"]["codarea"])


def baixar(forcar: bool) -> Path | None:
    """Baixa o zip do país. Devolve o caminho, ou None se o servidor não entregou (sem nova tentativa)."""
    if BRUTO.exists() and BRUTO.with_suffix(".json").exists() and not forcar:
        logger.info("já existe: %s (%d bytes)", BRUTO.relative_to(RAIZ), BRUTO.stat().st_size)
        return BRUTO
    try:
        r = requests.get(URL, headers={"User-Agent": USER_AGENT}, timeout=TEMPO_LIMITE_S)
    except requests.RequestException as erro:
        logger.warning("sem resposta de %s (%s) — sem nova tentativa", URL, erro)
        return None
    if r.status_code != 200 or not r.content[:2] == b"PK":
        logger.warning("o servidor não entregou o arquivo: HTTP %d em %s — sem nova tentativa", r.status_code, URL)
        return None
    PASTA.mkdir(parents=True, exist_ok=True)
    BRUTO.write_bytes(r.content)
    BRUTO.with_suffix(".json").write_text(json.dumps({
        "arquivo": BRUTO.name, "fonte": FONTE, "fonte_url": URL,
        "data_acesso": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "tamanho_bytes": len(r.content), "sha256": hashlib.sha256(r.content).hexdigest(),
        "tipo_de_conteudo_do_servidor": r.headers.get("Content-Type"),
        "conteudo_do_zip": zipfile.ZipFile(BRUTO).namelist(),
        "observacao": "arquivo do país inteiro, como a fonte serve; o recorte do município fica no arquivo irmão de sufixo recorte-municipio"},
        ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("baixado: %s — fonte %s — %d bytes", BRUTO.relative_to(RAIZ), URL, len(r.content))
    return BRUTO


def recortar(arq: Path, forcar: bool) -> Path:
    """Polígonos da fonte dentro do limite municipal, no CRS do projeto."""
    if RECORTE.exists() and RECORTE.with_suffix(".json").exists() and not forcar:
        logger.info("já existe: %s", RECORTE.relative_to(RAIZ))
        return RECORTE
    limite = carregar_area_estudo()  # já em EPSG:31981
    shp = [m for m in zipfile.ZipFile(arq).namelist() if m.lower().endswith(".shp")]
    if len(shp) != 1:
        raise ValueError(f"esperava um shapefile no zip, achei {shp}")
    crs_origem = gpd.read_file(f"zip://{arq}!{shp[0]}", rows=1).crs
    # lê só o que cai no retângulo do município (em coordenadas da fonte), depois reprojeta e corta pelo limite
    g = gpd.read_file(f"zip://{arq}!{shp[0]}", bbox=tuple(limite.to_crs(crs_origem).total_bounds))
    campos = [c for c in g.columns if c != "geometry"]
    g = g.to_crs(CRS_PADRAO)
    g["geometry"] = g.geometry.buffer(0).intersection(limite.union_all())
    g = g[~g.geometry.is_empty].copy()
    g["area_km2"] = g.geometry.area / 1e6
    if g.empty:
        raise ValueError("nenhuma área urbanizada dentro do limite municipal")
    g.to_file(RECORTE, driver="GPKG", layer="areas_urbanizadas")
    RECORTE.with_suffix(".json").write_text(json.dumps({
        "arquivo": RECORTE.name, "fonte": FONTE, "fonte_url": URL, "origem": str(BRUTO.relative_to(RAIZ)),
        "sha256_da_origem": hashlib.sha256(arq.read_bytes()).hexdigest(),
        "data_processamento": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "codigo_ibge": codigo_da_area_de_estudo(),
        "transformacao": f"polígonos que tocam o limite de config/area_estudo.geojson, reprojetados de {crs_origem} para {CRS_PADRAO} e cortados pelo limite; "
                         "todos os atributos da fonte; area_km2 = área da parte dentro do município",
        "crs": CRS_PADRAO, "n_feicoes": len(g), "area_total_km2": float(g.area_km2.sum()), "campos_da_fonte": campos,
        "tamanho_bytes": RECORTE.stat().st_size, "sha256": hashlib.sha256(RECORTE.read_bytes()).hexdigest()},
        ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    logger.info("recorte: %s — %d polígonos, %.2f km²", RECORTE.relative_to(RAIZ), len(g), g.area_km2.sum())
    return RECORTE


def main() -> None:
    p = argparse.ArgumentParser(description="Áreas Urbanizadas do Brasil 2019 (IBGE): arquivo do país e recorte do município da área de estudo.")
    p.add_argument("--forcar", action="store_true", help="baixa e regrava mesmo que já exista")
    a = p.parse_args()
    arq = baixar(a.forcar)
    if arq is None:
        raise SystemExit("áreas urbanizadas não baixadas: o servidor não entregou o arquivo")
    recortar(arq, a.forcar)


if __name__ == "__main__":
    main()
