"""
Baixa as ÁREAS DE ÁGUA (polígonos de rios largos, lagos, açudes e
reservatórios) do OpenStreetMap para a área de estudo, com uma faixa de
3 km além do limite municipal — o rio principal costuma ser o próprio
limite, e a margem do outro lado só aparece com essa folga.

    data/raw/vetor/hidrografia-area-agua_osm_atual_vetorial.gpkg  (+ .json)

Uso no projeto: camada de APOIO cartográfico (desenhar o rio como área de
água, com a margem, por cima dos polígonos temáticos) e para medir a
distância dos endereços à margem. Não entra em contagem de população.

Por que OSM: o repositório não tinha camada de massa d'água; a hidrografia
da BHO/ANA já usada é de linhas (eixo dos cursos d'água). As tags lidas são
as de área de água do OSM: natural=water (com water=river/lake/reservoir/
pond/...), waterway=riverbank (esquema antigo) e landuse=reservoir/basin.

Idempotente: não baixa de novo se o .gpkg existir (a menos de --forcar).
CRS: o osmnx exige consulta em EPSG:4326; a saída é reprojetada para
EPSG:31981 (padrão do projeto).

Uso:
    python scripts/download/hidrografia_area_agua_osm.py
    python scripts/download/hidrografia_area_agua_osm.py --forcar
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import osmnx as ox

sys.path.append(str(Path(__file__).resolve().parents[1] / "utils"))
from recorte_municipio import CRS_PADRAO, carregar_area_estudo  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

RAIZ = Path(__file__).resolve().parents[2]
SAIDA = RAIZ / "data" / "raw" / "vetor" / "hidrografia-area-agua_osm_atual_vetorial.gpkg"
FOLGA_M = 3000  # faixa além do limite municipal (margem oposta do rio de fronteira)
# cache do Overpass em pasta ignorada pelo git (o padrão do osmnx é ./cache na raiz)
ox.settings.cache_folder = str(RAIZ / "data" / "raw" / "cache_osmnx")
TAGS = {"natural": "water", "waterway": "riverbank", "landuse": ["reservoir", "basin"]}
COLUNAS = ["element", "id", "name", "natural", "water", "waterway", "landuse", "intermittent"]


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--forcar", action="store_true", help="baixa de novo mesmo se o arquivo existir")
    a = p.parse_args()
    if SAIDA.exists() and not a.forcar:
        logger.info("Já existe: %s (%.0f kB) — nada a fazer (use --forcar)", SAIDA.name, SAIDA.stat().st_size / 1024)
        return

    area = carregar_area_estudo()
    consulta = area.buffer(FOLGA_M).union_all()
    poligono_4326 = gpd.GeoSeries([consulta], crs=CRS_PADRAO).to_crs("EPSG:4326").iloc[0]
    logger.info("Baixando áreas de água do OSM (Overpass, tags=%s)...", TAGS)
    try:
        gdf = ox.features_from_polygon(poligono_4326, tags=TAGS)
    except Exception as erro:  # Overpass fora do ar, tempo esgotado etc.
        raise SystemExit(f"Falha na consulta ao Overpass: {erro}") from erro
    gdf = gdf.reset_index()
    gdf = gdf.drop_duplicates(subset=["element", "id"])  # relação repetida no retorno do osmnx
    gdf = gdf[gdf.geometry.geom_type.isin(["Polygon", "MultiPolygon"])].copy()
    if gdf.empty:
        raise SystemExit("Nenhum polígono de água retornado — verifique a consulta")
    for col in COLUNAS:
        if col not in gdf.columns:
            gdf[col] = None
    gdf = gdf[COLUNAS + ["geometry"]].to_crs(CRS_PADRAO)
    gdf["geometry"] = gdf.geometry.make_valid().intersection(consulta)
    gdf = gdf[~gdf.geometry.is_empty & gdf.geometry.geom_type.isin(["Polygon", "MultiPolygon", "GeometryCollection"])]
    gdf["geometry"] = gdf.geometry.apply(lambda g: g if g.geom_type != "GeometryCollection" else
                                         gpd.GeoSeries([x for x in g.geoms if x.area > 0]).union_all())
    gdf["area_ha"] = gdf.geometry.area / 1e4
    for col in COLUNAS:
        gdf[col] = gdf[col].astype("string")
    SAIDA.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_file(SAIDA, driver="GPKG", layer="area_agua")

    meta = {
        "fonte": "OpenStreetMap (via osmnx / Overpass API) — contribuidores do OpenStreetMap, licença ODbL",
        "url_api": "https://overpass-api.de/api/interpreter",
        "consulta": {"tags": TAGS, "recorte": f"limite municipal (config/area_estudo.geojson) + {FOLGA_M} m"},
        "data_acesso": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "n_poligonos": len(gdf),
        "area_total_ha": round(float(gdf.area_ha.sum()), 1),
        "por_tipo_ha": gdf.groupby(gdf.water.fillna(gdf.natural).fillna(gdf.waterway).fillna(gdf.landuse).astype(str)).area_ha.sum().round(1).to_dict(),
        "crs_original": "EPSG:4326",
        "crs_processado": CRS_PADRAO,
        "tamanho_kb": round(SAIDA.stat().st_size / 1024, 1),
        "transformacao_aplicada": "só polígonos; make_valid; recorte pela área de consulta; reprojeção 4326 -> 31981",
        "uso": "camada de apoio cartográfico (rio como área de água) e distância à margem; não entra em contagem",
    }
    SAIDA.with_suffix(".json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("Gravado %s: %d polígonos, %.0f ha, %.0f kB", SAIDA.name, len(gdf), meta["area_total_ha"], meta["tamanho_kb"])


if __name__ == "__main__":
    main()
