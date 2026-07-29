"""
Baixa dados de infraestrutura do OpenStreetMap (via Overpass API, usando a
biblioteca osmnx) recortados pela área de estudo do projeto
(config/area_estudo.geojson) e gera dois vetores consolidados:

    data/raw/vetor/malha-viaria_osm_atual_vetorial.gpkg
    data/raw/vetor/saude-estabelecimentos_osm_atual_vetorial.gpkg

Camadas baixadas:
    1. Malha viária (rede de ruas/estradas) — network_type="drive"
    2. Estabelecimentos de saúde — amenity in
       [hospital, clinic, doctors, pharmacy] ou healthcare=*

Idempotente: se os arquivos de saída já existirem, não baixa de novo (a
menos que --forcar seja usado). Loga fonte, data e tamanho do download.

A área de consulta é sempre lida do arquivo único de área de estudo do
projeto (scripts/utils/recorte_municipio.py) — não hardcoda o polígono do
município, permitindo reuso em outros municípios.

Uso:
    python scripts/download/infraestrutura_osm.py
    python scripts/download/infraestrutura_osm.py --forcar
"""

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import osmnx as ox

sys.path.append(str(Path(__file__).resolve().parents[1] / "utils"))
from recorte_municipio import CRS_PADRAO, carregar_area_estudo, recortar_vetor  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

CRS_OSM = "EPSG:4326"  # osmnx exige polígono de consulta em lat/lon não projetado

TAGS_SAUDE = {"amenity": ["hospital", "clinic", "doctors", "pharmacy"], "healthcare": True}

CAMINHO_MALHA_VIARIA_DEFAULT = (
    Path(__file__).resolve().parents[2] / "data" / "raw" / "vetor" / "malha-viaria_osm_atual_vetorial"
)
CAMINHO_SAUDE_DEFAULT = (
    Path(__file__).resolve().parents[2] / "data" / "raw" / "vetor" / "saude-estabelecimentos_osm_atual_vetorial"
)


def _obter_poligono_consulta(area_estudo: gpd.GeoDataFrame):
    """Reprojeta a área de estudo para EPSG:4326 (exigido pelo osmnx) e une em um único polígono."""
    return area_estudo.to_crs(CRS_OSM).union_all()


def _sanitizar_para_gpkg(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Converte colunas com valores list/dict (comuns em atributos OSM) para string.

    O driver GPKG não aceita tipos de coluna heterogêneos ou compostos
    (ex.: maxspeed=['50','40'] quando uma via tem trechos com placas
    diferentes); sem isso o to_file falha.
    """
    gdf = gdf.copy()
    coluna_geom = gdf.geometry.name
    for coluna in gdf.columns:
        if coluna == coluna_geom:
            continue
        if gdf[coluna].apply(lambda v: isinstance(v, (list, dict, set))).any():
            gdf[coluna] = gdf[coluna].apply(
                lambda v: ";".join(map(str, v)) if isinstance(v, (list, set)) else (str(v) if isinstance(v, dict) else v)
            )
    return gdf


def baixar_malha_viaria(poligono) -> gpd.GeoDataFrame:
    """Baixa a rede viária (network_type='drive') e retorna as arestas (vias) como GeoDataFrame."""
    logger.info("Baixando malha viária (network_type='drive') via Overpass API...")
    try:
        grafo = ox.graph_from_polygon(poligono, network_type="drive", simplify=True)
    except Exception as erro:
        raise RuntimeError(f"Falha ao baixar a malha viária do OSM: {erro}") from erro

    gdf_vias = ox.graph_to_gdfs(grafo, nodes=False, edges=True)
    gdf_vias = gdf_vias.reset_index()  # u, v, key (topologia do grafo) viram colunas normais
    logger.info("Malha viária: %d trecho(s) baixado(s) (antes do recorte final)", len(gdf_vias))
    return gdf_vias


def baixar_estabelecimentos_saude(poligono) -> gpd.GeoDataFrame:
    """Baixa estabelecimentos de saúde (hospital/clinic/doctors/pharmacy/healthcare=*)."""
    logger.info("Baixando estabelecimentos de saúde (tags=%s) via Overpass API...", TAGS_SAUDE)
    try:
        gdf = ox.features_from_polygon(poligono, tags=TAGS_SAUDE)
    except Exception as erro:
        raise RuntimeError(f"Falha ao baixar estabelecimentos de saúde do OSM: {erro}") from erro

    gdf = gdf.reset_index()  # element_type, osmid (multi-índice do osmnx) viram colunas normais
    logger.info("Estabelecimentos de saúde: %d feição(ões) baixada(s) (antes do recorte final)", len(gdf))
    return gdf


def calcular_km_por_tipo_via(gdf_vias: gpd.GeoDataFrame) -> dict:
    """Km de via por categoria `highway`, calculado no CRS métrico padrão do projeto.

    Quando um trecho tem múltiplos valores de highway (lista), a categoria é
    tratada como uma combinação própria (ex. "residential;service") em vez de
    duplicar o comprimento em cada tipo.
    """
    tipo = gdf_vias["highway"].apply(lambda v: ";".join(map(str, v)) if isinstance(v, list) else str(v))
    km_por_tipo = (gdf_vias.geometry.length / 1000).groupby(tipo).sum()
    return {k: round(v, 3) for k, v in km_por_tipo.sort_values(ascending=False).items()}


def salvar_malha_viaria(gdf_vias: gpd.GeoDataFrame, caminho_base: Path, area_estudo: gpd.GeoDataFrame) -> None:
    gdf_vias = gdf_vias.to_crs(CRS_PADRAO)
    gdf_vias = recortar_vetor(gdf_vias, area_estudo)
    gdf_vias = _sanitizar_para_gpkg(gdf_vias)

    caminho_base.parent.mkdir(parents=True, exist_ok=True)
    caminho_gpkg = caminho_base.with_suffix(".gpkg")
    gdf_vias.to_file(caminho_gpkg, driver="GPKG", layer="malha_viaria")
    logger.info("Malha viária salva em %s (CRS: %s, %d trechos)", caminho_gpkg, CRS_PADRAO, len(gdf_vias))

    km_por_tipo = calcular_km_por_tipo_via(gdf_vias)
    tamanho_kb = caminho_gpkg.stat().st_size / 1024

    metadados = {
        "fonte": "OpenStreetMap (via osmnx / Overpass API)",
        "url_api": "https://overpass-api.de/api/interpreter",
        "consulta": {"network_type": "drive"},
        "n_feicoes_total": len(gdf_vias),
        "km_total": round(sum(km_por_tipo.values()), 3),
        "km_por_tipo_highway": km_por_tipo,
        "tamanho_gpkg_kb": round(tamanho_kb, 1),
        "crs_original": CRS_OSM,
        "crs_processado": CRS_PADRAO,
        "data_processamento": datetime.now(timezone.utc).isoformat(),
        "transformacao_aplicada": (
            f"download via osmnx.graph_from_polygon (network_type='drive'), conversão grafo->vetor "
            f"(graph_to_gdfs), reprojeção para {CRS_PADRAO} e recorte pela área de estudo do projeto"
        ),
    }
    caminho_metadados = caminho_gpkg.with_suffix(".json")
    caminho_metadados.write_text(json.dumps(metadados, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("Metadados salvos em %s", caminho_metadados)


def salvar_estabelecimentos_saude(gdf: gpd.GeoDataFrame, caminho_base: Path, area_estudo: gpd.GeoDataFrame) -> None:
    gdf = gdf.to_crs(CRS_PADRAO)
    gdf = recortar_vetor(gdf, area_estudo)
    gdf = _sanitizar_para_gpkg(gdf)

    caminho_base.parent.mkdir(parents=True, exist_ok=True)
    caminho_gpkg = caminho_base.with_suffix(".gpkg")
    gdf.to_file(caminho_gpkg, driver="GPKG", layer="estabelecimentos_saude")
    logger.info("Estabelecimentos de saúde salvos em %s (CRS: %s, %d feições)", caminho_gpkg, CRS_PADRAO, len(gdf))

    contagem_amenity = gdf["amenity"].value_counts(dropna=True).to_dict() if "amenity" in gdf.columns else {}
    n_healthcare_sem_amenity = (
        int(((gdf.get("healthcare").notna()) & (gdf.get("amenity").isna())).sum())
        if "healthcare" in gdf.columns and "amenity" in gdf.columns
        else 0
    )
    tamanho_kb = caminho_gpkg.stat().st_size / 1024

    metadados = {
        "fonte": "OpenStreetMap (via osmnx / Overpass API)",
        "url_api": "https://overpass-api.de/api/interpreter",
        "consulta": {"tags": TAGS_SAUDE},
        "n_feicoes_total": len(gdf),
        "contagem_por_amenity": contagem_amenity,
        "n_feicoes_healthcare_sem_amenity": n_healthcare_sem_amenity,
        "tamanho_gpkg_kb": round(tamanho_kb, 1),
        "crs_original": CRS_OSM,
        "crs_processado": CRS_PADRAO,
        "data_processamento": datetime.now(timezone.utc).isoformat(),
        "transformacao_aplicada": (
            f"download via osmnx.features_from_polygon (tags={TAGS_SAUDE}), reprojeção para "
            f"{CRS_PADRAO} e recorte pela área de estudo do projeto"
        ),
    }
    caminho_metadados = caminho_gpkg.with_suffix(".json")
    caminho_metadados.write_text(json.dumps(metadados, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("Metadados salvos em %s", caminho_metadados)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Baixa malha viária e estabelecimentos de saúde do OpenStreetMap (osmnx) para a área de estudo."
    )
    parser.add_argument(
        "--malha-viaria-saida", type=Path, default=CAMINHO_MALHA_VIARIA_DEFAULT, help="Caminho base de saída da malha viária (sem extensão)"
    )
    parser.add_argument(
        "--saude-saida", type=Path, default=CAMINHO_SAUDE_DEFAULT, help="Caminho base de saída dos estabelecimentos de saúde (sem extensão)"
    )
    parser.add_argument("--forcar", action="store_true", help="Baixa novamente mesmo se os arquivos já existirem")
    args = parser.parse_args()

    caminho_malha_gpkg = args.malha_viaria_saida.with_suffix(".gpkg")
    caminho_saude_gpkg = args.saude_saida.with_suffix(".gpkg")

    if caminho_malha_gpkg.exists() and caminho_saude_gpkg.exists() and not args.forcar:
        logger.info(
            "Malha viária e estabelecimentos de saúde já existem (%s, %s) — nada a fazer (use --forcar para baixar de novo).",
            caminho_malha_gpkg, caminho_saude_gpkg,
        )
        return

    area_estudo = carregar_area_estudo()
    poligono = _obter_poligono_consulta(area_estudo)

    if caminho_malha_gpkg.exists() and not args.forcar:
        logger.info("Malha viária já existe em %s — pulando (use --forcar para baixar de novo).", caminho_malha_gpkg)
    else:
        gdf_vias = baixar_malha_viaria(poligono)
        salvar_malha_viaria(gdf_vias, args.malha_viaria_saida, area_estudo)

    if caminho_saude_gpkg.exists() and not args.forcar:
        logger.info("Estabelecimentos de saúde já existem em %s — pulando (use --forcar para baixar de novo).", caminho_saude_gpkg)
    else:
        gdf_saude = baixar_estabelecimentos_saude(poligono)
        salvar_estabelecimentos_saude(gdf_saude, args.saude_saida, area_estudo)


if __name__ == "__main__":
    main()
