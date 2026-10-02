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

Opção --malha-pontes (rodada 08, acessibilidade com vias alagadas): baixa a
mesma rede (network_type="drive"), mas com todas as partes desconectadas
(retain_all) e simplificada SEM fundir trechos com valores diferentes de
bridge/tunnel/layer — na malha original, a simplificação do osmnx fundia a
ponte com as ruas de chegada num trecho só (pontes de 0,1 a 25 km), o que
impede tratar a ponte separada da rua. Grava um arquivo à parte (a malha
original, usada pelo geoportal, fica intacta), com as camadas "trechos" e
"nos":
    data/raw/vetor/malha-viaria-pontes-separadas_osm_atual_vetorial.gpkg

Idempotente: se os arquivos de saída já existirem, não baixa de novo (a
menos que --forcar seja usado). Loga fonte, data e tamanho do download.

A área de consulta é sempre lida do arquivo único de área de estudo do
projeto (scripts/utils/recorte_municipio.py) — não hardcoda o polígono do
município, permitindo reuso em outros municípios.

Uso:
    python scripts/download/infraestrutura_osm.py
    python scripts/download/infraestrutura_osm.py --forcar
    python scripts/download/infraestrutura_osm.py --malha-pontes
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

# cache do Overpass em pasta ignorada pelo git (o padrão do osmnx é ./cache na raiz, versionada)
ox.settings.cache_folder = str(Path(__file__).resolve().parents[2] / "data" / "raw" / "cache_osmnx")

TAGS_SAUDE = {"amenity": ["hospital", "clinic", "doctors", "pharmacy"], "healthcare": True}

CAMINHO_MALHA_VIARIA_DEFAULT = (
    Path(__file__).resolve().parents[2] / "data" / "raw" / "vetor" / "malha-viaria_osm_atual_vetorial"
)
CAMINHO_MALHA_PONTES_DEFAULT = (
    Path(__file__).resolve().parents[2] / "data" / "raw" / "vetor" / "malha-viaria-pontes-separadas_osm_atual_vetorial"
)
# atributos que não podem ser fundidos na simplificação: a ponte/túnel fica como trecho próprio
ATRIBUTOS_NAO_FUNDIR = ["bridge", "tunnel", "layer"]
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


def baixar_malha_viaria_pontes(poligono):
    """Rede 'drive' com todas as componentes e pontes/túneis como trechos próprios (ver docstring do módulo)."""
    logger.info("Baixando malha viária (network_type='drive', retain_all, sem simplificar) via Overpass API...")
    if "layer" not in ox.settings.useful_tags_way:
        ox.settings.useful_tags_way = list(ox.settings.useful_tags_way) + ["layer"]
    try:
        grafo = ox.graph_from_polygon(poligono, network_type="drive", simplify=False, retain_all=True)
    except Exception as erro:
        raise RuntimeError(f"Falha ao baixar a malha viária do OSM: {erro}") from erro
    n_bruto = (grafo.number_of_nodes(), grafo.number_of_edges())
    grafo = ox.simplify_graph(grafo, edge_attrs_differ=ATRIBUTOS_NAO_FUNDIR)
    nos, vias = ox.graph_to_gdfs(grafo, nodes=True, edges=True)
    logger.info("Malha (pontes separadas): %d nós / %d trechos brutos -> %d nós / %d trechos simplificados",
                *n_bruto, len(nos), len(vias))
    return nos.reset_index(), vias.reset_index(), n_bruto


def salvar_malha_viaria_pontes(nos, vias, n_bruto, caminho_base: Path, area_estudo: gpd.GeoDataFrame) -> None:
    # sem recorte das arestas pelo limite: o recorte cortaria a geometria e a deixaria diferente do grafo
    # (a consulta já foi feita pelo polígono da área de estudo; as pontas fora dele são truncadas pelo osmnx)
    nos = _sanitizar_para_gpkg(nos.to_crs(CRS_PADRAO))
    vias = _sanitizar_para_gpkg(vias.to_crs(CRS_PADRAO))
    caminho_gpkg = caminho_base.with_suffix(".gpkg")
    caminho_gpkg.parent.mkdir(parents=True, exist_ok=True)
    vias.to_file(caminho_gpkg, driver="GPKG", layer="trechos")
    nos.to_file(caminho_gpkg, driver="GPKG", layer="nos")
    ponte = vias["bridge"].notna() if "bridge" in vias.columns else vias.index.isin([])
    metadados = {
        "fonte": "OpenStreetMap (via osmnx / Overpass API) — contribuidores do OpenStreetMap",
        "url_api": "https://overpass-api.de/api/interpreter",
        "consulta": {"network_type": "drive", "simplify": False, "retain_all": True,
                     "simplificacao_posterior": {"edge_attrs_differ": ATRIBUTOS_NAO_FUNDIR}},
        "n_nos_brutos": n_bruto[0], "n_trechos_brutos_direcionados": n_bruto[1],
        "n_nos": len(nos), "n_trechos_direcionados": len(vias),
        "km_total_direcionado": round(float(vias.geometry.length.sum() / 1000), 3),
        "trechos_ponte": int(ponte.sum()),
        "valores_bridge": vias["bridge"].value_counts().to_dict() if "bridge" in vias.columns else {},
        "valores_tunnel": vias["tunnel"].value_counts().to_dict() if "tunnel" in vias.columns else {},
        "tamanho_gpkg_kb": round(caminho_gpkg.stat().st_size / 1024, 1),
        "crs_original": CRS_OSM,
        "crs_processado": CRS_PADRAO,
        "data_processamento": datetime.now(timezone.utc).isoformat(),
        "o_que_mudou": ("arquivo NOVO, à parte da malha original (malha-viaria_osm_atual_vetorial.gpkg, intacta): "
                        "na original a simplificação fundia a ponte com as ruas de chegada; aqui ponte/túnel/layer "
                        "não são fundidos, todas as componentes desconectadas são mantidas e os nós são gravados; "
                        "snapshot novo do OSM (pode diferir da original, de 2026-07-27)"),
        "transformacao_aplicada": (
            f"osmnx.graph_from_polygon(network_type='drive', simplify=False, retain_all=True), "
            f"osmnx.simplify_graph(edge_attrs_differ={ATRIBUTOS_NAO_FUNDIR}), graph_to_gdfs (nós e trechos), "
            f"reprojeção para {CRS_PADRAO}; trechos direcionados (via de mão dupla = dois trechos); sem recorte das arestas"
        ),
    }
    caminho_gpkg.with_suffix(".json").write_text(json.dumps(metadados, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("Malha (pontes separadas) salva em %s (%d trechos, %d pontes, %.1f kB)", caminho_gpkg, len(vias),
                int(ponte.sum()), metadados["tamanho_gpkg_kb"])


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
    parser.add_argument("--malha-pontes", action="store_true",
                        help="Baixa só a malha com pontes/túneis como trechos próprios (arquivo à parte; ver docstring)")
    parser.add_argument("--malha-pontes-saida", type=Path, default=CAMINHO_MALHA_PONTES_DEFAULT,
                        help="Caminho base de saída da malha com pontes separadas (sem extensão)")
    args = parser.parse_args()

    if args.malha_pontes:
        caminho = args.malha_pontes_saida.with_suffix(".gpkg")
        if caminho.exists() and not args.forcar:
            logger.info("Malha com pontes separadas já existe em %s — nada a fazer (use --forcar).", caminho)
            return
        area_estudo = carregar_area_estudo()
        nos, vias, n_bruto = baixar_malha_viaria_pontes(_obter_poligono_consulta(area_estudo))
        salvar_malha_viaria_pontes(nos, vias, n_bruto, args.malha_pontes_saida, area_estudo)
        return

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
