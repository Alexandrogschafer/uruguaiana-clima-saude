"""
Corrige as geometrias inválidas de cotas-inundacao_sgb (4 polígonos com
self-intersection, verificado via shapely.validation.explain_validity) e
exporta a extensão real da mancha de inundação por cota_cm para o geoportal.

Esta é uma camada adicional (não fazia parte da lista original de camadas
da ETAPA 1) — complementa setores-inundacao.geojson mostrando o contorno
real da inundação, em vez de só o setor censitário recolorido. Fica em
data/geoportal/cotas-inundacao.geojson; use ou não no slider é decisão de
UX da ETAPA 2.

Não sobrescreve o .gpkg bruto em data/raw/ (convenção do projeto é não
alterar dados brutos) — a correção é aplicada em memória, só no momento
da exportação.
"""

from pathlib import Path

import geopandas as gpd

from common import DIR_GEOPORTAL, RAIZ_PROJETO, logger, salvar_geojson_wgs84

CAMINHO_ORIGEM = RAIZ_PROJETO / "data" / "raw" / "vetor" / "cotas-inundacao_sgb_atual_vetorial.gpkg"


def main() -> None:
    gdf = gpd.read_file(CAMINHO_ORIGEM)
    n_invalidas = int((~gdf.geometry.is_valid).sum())

    # buffer(0) resolve self-intersection sem alterar a área útil do polígono
    gdf["geometry"] = gdf.geometry.buffer(0)
    n_invalidas_apos = int((~gdf.geometry.is_valid).sum())
    if n_invalidas_apos:
        raise ValueError(f"{n_invalidas_apos} geometrias permanecem inválidas após buffer(0)")

    salvar_geojson_wgs84(
        gdf,
        DIR_GEOPORTAL / "cotas-inundacao.geojson",
        descricao="Extensão real da mancha de inundação por cota (SGB) — geometrias corrigidas (buffer(0), self-intersection).",
        fonte={"caminho_origem": str(CAMINHO_ORIGEM.relative_to(RAIZ_PROJETO))},
        transformacao=(
            f"correção de {n_invalidas} geometrias inválidas via buffer(0) (self-intersection); "
            "reprojeção -> EPSG:4326"
        ),
    )
    logger.info("cotas-inundação: %d geometrias inválidas corrigidas", n_invalidas)


if __name__ == "__main__":
    main()
