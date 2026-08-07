"""
Converte a malha viária (OSM, via osmnx) para GeoJSON em WGS84, simplificando
a geometria para reduzir o peso do arquivo — esta camada é só contexto visual
no geoportal, não precisa de precisão métrica nem da maioria dos atributos OSM.

Decisões:
- Simplificação de geometria é feita em EPSG:31981 (CRS métrico de origem);
  tolerância em graus não teria significado consistente. Testada em 5/7.5/10m
  — 7.5m reduz o tamanho do arquivo sem distorcer visualmente o traçado das
  vias na escala do município.
- Atributos reduzidos a name/highway (nome da via e tipo/hierarquia viária),
  os únicos úteis num popup de contexto; colunas de topologia de grafo do
  osmnx (u, v, key, osmid) e demais tags OSM (oneway, lanes, maxspeed, etc.)
  são descartadas.
"""

from pathlib import Path

import geopandas as gpd

from common import DIR_GEOPORTAL, RAIZ_PROJETO, logger, salvar_geojson_wgs84

CAMINHO_ORIGEM = RAIZ_PROJETO / "data" / "raw" / "vetor" / "malha-viaria_osm_atual_vetorial.gpkg"
TOLERANCIA_SIMPLIFICACAO_M = 7.5


def main() -> None:
    caminho_saida = DIR_GEOPORTAL / "malha-viaria.geojson"
    if caminho_saida.exists():
        logger.info("já existe, pulando: %s", caminho_saida.relative_to(RAIZ_PROJETO))
        return

    gdf = gpd.read_file(CAMINHO_ORIGEM)
    n_original = len(gdf)

    gdf = gdf[["name", "highway", "geometry"]].copy()
    # simplify em CRS métrico (EPSG:31981), antes de reprojetar para 4326
    gdf["geometry"] = gdf.geometry.simplify(TOLERANCIA_SIMPLIFICACAO_M, preserve_topology=True)

    # highway pode vir como lista (via com múltiplas classificações no OSM); normaliza para string
    gdf["highway"] = gdf["highway"].apply(lambda v: v[0] if isinstance(v, list) else v)
    gdf["name"] = gdf["name"].apply(lambda v: v[0] if isinstance(v, list) else v)

    salvar_geojson_wgs84(
        gdf,
        caminho_saida,
        descricao="Malha viária (OSM) simplificada, camada de contexto visual (sem atributos de rota/hierarquia completos).",
        fonte={"caminho_origem": str(CAMINHO_ORIGEM.relative_to(RAIZ_PROJETO))},
        transformacao=(
            f"simplify(tolerância={TOLERANCIA_SIMPLIFICACAO_M}m, preserve_topology=True) em EPSG:31981; "
            "colunas reduzidas a [name, highway]; reprojeção -> EPSG:4326"
        ),
    )
    logger.info("malha viária: %d features de entrada -> %d exportadas", n_original, len(gdf))


if __name__ == "__main__":
    main()
