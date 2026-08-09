"""Baixa os temas Geologia, Geomorfologia e Pedologia do Banco de Dados de
Informações Ambientais do IBGE (BDiA), recortados pela área de estudo —
mesmo acervo e mesmo método já usados para Vegetação
(scripts/download/vegetacao_ibge_bdia.py):

    data/raw/vetor/geologia_ibge-bdia_atual_vetorial.gpkg (+ .json)
    data/raw/vetor/geomorfologia_ibge-bdia_atual_vetorial.gpkg (+ .json)
    data/raw/vetor/pedologia_ibge-bdia_atual_vetorial.gpkg (+ .json)

Fonte e método
---------------
Workspace BDIA do GeoServer do IBGE (WFS 2.0.0), endpoint
https://geoservicos.ibge.gov.br/geoserver/BDIA/ows — camadas
BDIA:geol_area, BDIA:geom_area, BDIA:pedo_area (confirmadas reais por
leitura do GetCapabilities/DescribeFeatureType; o mesmo acervo cujo tema
Vegetação já foi processado neste projeto). Escala 1:250.000 (mesma nota
de limitação já registrada para vegetação: adequado para análise
regional/exploratória, não para licenciamento/fiscalização).

Cada tema tem um campo de classe temática diferente na fonte —
geologia usa `nm_unidade` (nome da unidade geológica/litológica; não há
campo `legenda` nessa camada), geomorfologia e pedologia usam `legenda`
— mapeado em CAMADAS_BDIA.

Recorte: bbox da área de estudo (WFS BBOX, EPSG:4674) seguido de recorte
exato pelo polígono municipal (gpd.clip via recorte_municipio), porque o
BBOX é retangular e traz feições fora do limite real do município —
mesmo método de vegetacao_ibge_bdia.py.

IMPORTANTE — não confundir geologia_ibge-bdia com risco-geologico_cprm
(já processado neste projeto, scripts/download/risco_geologico_cprm.py):
geologia_ibge-bdia mapeia UNIDADES GEOLÓGICAS/LITOLÓGICAS (rochas,
formações, idade geológica) a 1:250.000; risco-geologico_cprm é uma
classificação de SUSCETIBILIDADE a movimentos de massa e inundação
(produto de modelagem de risco, validado em campo em 2021, cobertura
restrita a ~23 municípios prioritários do RS). São temas diferentes que
respondem perguntas diferentes.

Idempotente: se o arquivo do tema já existir, não baixa de novo (por
tema — `--forcar` refaz todos, `--tema` roda só um).

Uso:
    python scripts/download/geociencias_ibge_bdia.py
    python scripts/download/geociencias_ibge_bdia.py --tema geologia --forcar
"""

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import requests

sys.path.append(str(Path(__file__).resolve().parents[1] / "utils"))
from recorte_municipio import CRS_PADRAO, carregar_area_estudo, recortar_vetor  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

RAIZ = Path(__file__).resolve().parents[2]
DIR_SAIDA = RAIZ / "data" / "raw" / "vetor"

CRS_ORIGEM_BDIA = "EPSG:4674"  # SIRGAS 2000 geográfico (confirmado por srsName aceito no WFS)
URL_WFS_BDIA = "https://geoservicos.ibge.gov.br/geoserver/BDIA/ows"
ESCALA_FONTE = "1:250.000"

NOTA_GEOLOGIA_VS_RISCO_CPRM = (
    "não confundir com risco-geologico_cprm (já processado neste projeto): geologia_ibge-bdia mapeia "
    "UNIDADES GEOLÓGICAS/LITOLÓGICAS (rochas, formações, idade geológica) a 1:250.000; "
    "risco-geologico_cprm é uma classificação de SUSCETIBILIDADE a movimentos de massa e inundação "
    "(modelagem de risco validada em campo em 2021, cobertura restrita a municípios prioritários do "
    "RS) — temas diferentes, respondem perguntas diferentes."
)

CAMADAS_BDIA = {
    "geologia": {
        "camada_wfs": "BDIA:geol_area",
        "campo_classe": "nm_unidade",
        "rotulo_classe": "unidade geológica (nm_unidade)",
        "arquivo_saida": "geologia_ibge-bdia_atual_vetorial.gpkg",
        "nota_extra": NOTA_GEOLOGIA_VS_RISCO_CPRM,
    },
    "geomorfologia": {
        "camada_wfs": "BDIA:geom_area",
        "campo_classe": "legenda",
        "rotulo_classe": "unidade geomorfológica (legenda)",
        "arquivo_saida": "geomorfologia_ibge-bdia_atual_vetorial.gpkg",
        "nota_extra": None,
    },
    "pedologia": {
        "camada_wfs": "BDIA:pedo_area",
        "campo_classe": "legenda",
        "rotulo_classe": "classe de solo (legenda)",
        "arquivo_saida": "pedologia_ibge-bdia_atual_vetorial.gpkg",
        "nota_extra": None,
    },
}


def baixar_camada_bdia(camada_wfs: str, bbox: tuple, timeout: int = 120) -> gpd.GeoDataFrame:
    minx, miny, maxx, maxy = bbox
    parametros = {
        "service": "WFS",
        "version": "2.0.0",
        "request": "GetFeature",
        "typeNames": camada_wfs,
        "outputFormat": "application/json",
        "srsName": CRS_ORIGEM_BDIA,
        "bbox": f"{minx},{miny},{maxx},{maxy},{CRS_ORIGEM_BDIA}",
    }
    logger.info("Consultando WFS do BDiA (%s) — bbox %s", camada_wfs, bbox)
    resposta = requests.get(URL_WFS_BDIA, params=parametros, timeout=timeout)
    resposta.raise_for_status()
    geojson = resposta.json()
    if "features" not in geojson:
        raise RuntimeError(f"Resposta do WFS do BDiA sem 'features' para {camada_wfs}: {geojson}")
    gdf = gpd.GeoDataFrame.from_features(geojson["features"], crs=CRS_ORIGEM_BDIA)
    logger.info("%d feição(ões) na área de busca (bbox retangular, antes do recorte exato).", len(gdf))
    return gdf


def processar_tema(tema: str, config: dict, area_estudo: gpd.GeoDataFrame, bbox: tuple, forcar: bool) -> None:
    caminho_saida = DIR_SAIDA / config["arquivo_saida"]
    if caminho_saida.exists() and not forcar:
        logger.info("[%s] %s já existe — nada a fazer (use --forcar para baixar de novo).", tema, caminho_saida)
        return

    gdf_bruto = baixar_camada_bdia(config["camada_wfs"], bbox)
    gdf = recortar_vetor(gdf_bruto, area_estudo)
    logger.info("[%s] %d feição(ões) após recorte exato pela área de estudo.", tema, len(gdf))

    if gdf.empty:
        raise RuntimeError(f"[{tema}] Nenhuma feição restou após o recorte — verifique bbox/serviço.")

    gdf.columns = [c.strip().lower() for c in gdf.columns]

    campo_classe = config["campo_classe"]
    area_por_classe = None
    n_por_classe = None
    if campo_classe in gdf.columns:
        gdf["_area_km2"] = gdf.geometry.area / 1e6
        area_por_classe = gdf.groupby(campo_classe)["_area_km2"].sum().round(3).sort_values(ascending=False).to_dict()
        n_por_classe = gdf[campo_classe].value_counts().to_dict()
        gdf = gdf.drop(columns="_area_km2")
    else:
        logger.warning("[%s] campo de classe '%s' não encontrado nas colunas: %s", tema, campo_classe, list(gdf.columns))

    caminho_saida.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_file(caminho_saida, driver="GPKG", layer=tema)

    metadados = {
        "fonte": f"IBGE — Banco de Dados de Informações Ambientais (BDiA), tema {tema.capitalize()}",
        "url_wfs": URL_WFS_BDIA,
        "camada_origem": config["camada_wfs"],
        "escala_fonte": ESCALA_FONTE,
        "data_acesso": datetime.now(timezone.utc).date().isoformat(),
        "n_feicoes": len(gdf),
        "campo_classe_usado": config["rotulo_classe"],
        "n_feicoes_por_classe": n_por_classe,
        "area_km2_por_classe": area_por_classe,
        "area_km2_total": round(gdf.geometry.area.sum() / 1e6, 2),
        "crs_original": CRS_ORIGEM_BDIA,
        "crs_processado": CRS_PADRAO,
        "metodo": (
            f"consulta WFS (GetFeature) à camada {config['camada_wfs']} filtrada por bbox retangular da "
            "área de estudo, seguida de recorte exato pelo polígono municipal (gpd.clip) — mesmo método "
            "usado em vegetacao_ibge-bdia (BDiA)"
        ),
        "aviso_escala": (
            f"levantamento na escala {ESCALA_FONTE} (mapeamento sistemático nacional) — adequado para "
            "análise regional/exploratória, não para delimitação em nível de propriedade, licenciamento "
            "ambiental ou fiscalização"
        ),
        "data_processamento": datetime.now(timezone.utc).isoformat(),
    }
    if config["nota_extra"]:
        metadados["nota_distincao_conceitual"] = config["nota_extra"]

    caminho_metadados = caminho_saida.with_suffix(".json")
    caminho_metadados.write_text(json.dumps(metadados, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

    logger.info("[%s] Salvo: %s (%d feições)", tema, caminho_saida, len(gdf))
    logger.info("[%s] Metadados salvos em %s", tema, caminho_metadados)
    logger.info("[%s] Área (km²) por classe (top 10): %s", tema, dict(list((area_por_classe or {}).items())[:10]))


def main() -> None:
    parser = argparse.ArgumentParser(description="Baixa Geologia, Geomorfologia e Pedologia do BDiA/IBGE recortados pela área de estudo.")
    parser.add_argument("--tema", choices=list(CAMADAS_BDIA), default=None, help="Baixa só um tema (default: todos)")
    parser.add_argument("--forcar", action="store_true", help="Baixa novamente mesmo se o arquivo do tema já existir")
    args = parser.parse_args()

    area_estudo = carregar_area_estudo()
    area_estudo_origem = area_estudo.to_crs(CRS_ORIGEM_BDIA)
    bbox = tuple(area_estudo_origem.total_bounds)

    temas = [args.tema] if args.tema else list(CAMADAS_BDIA)
    for tema in temas:
        processar_tema(tema, CAMADAS_BDIA[tema], area_estudo, bbox, args.forcar)


if __name__ == "__main__":
    main()
