"""
Converte a malha viária federal oficial (DNIT/SNV) para GeoJSON em WGS84 —
segunda fonte da camada "Malha viária" do geoportal (a primeira, OSM, já
é `malha-viaria.geojson`; esta é `malha-viaria-dnit.geojson`, mesmo padrão
de sufixo por fonte já usado em saude-cnes/saude-osm).

Decisões:
- Atributos reduzidos ao necessário para popup + estilo: rodovia (BR),
  trecho (local inicial/final), pavimentação, jurisdição e a extensão
  REAL dentro do município (`km_dentro_area_estudo`, coluna derivada já
  calculada na aquisição — `vl_extensa` da fonte é do trecho nacional
  completo, não da parte local, ver malha_viaria_dnit.py).
- Coluna derivada `divergencia_osm`: sinaliza o trecho de BR-377
  co-sinalizado com a BR-290 (`vl_br == "377"` e `desc_coinc == "Coinc"`)
  — achado já documentado na aquisição (2026-08-11): esse trecho existe
  fisicamente no OSM mas tagueado como BR-290/RSC-377, não como BR-377.
  Não é geometria distinta, é o mesmo traçado físico registrado 2x no SNV
  (uma entrada por rodovia coincidente) — aqui sinalizamos só a entrada
  BR-377 para não duplicar o aviso na BR-290 correspondente.
- Sem simplificação de geometria: só 18 trechos, arquivo já pequeno
  (~350KB no gpkg de origem), não precisa do mesmo tratamento do OSM.
"""

from pathlib import Path

import geopandas as gpd

from common import DIR_GEOPORTAL, RAIZ_PROJETO, logger, salvar_geojson_wgs84

CAMINHO_ORIGEM = RAIZ_PROJETO / "data" / "raw" / "vetor" / "malha-viaria_dnit-snv_202607A_vetorial.gpkg"

COLUNAS = [
    "vl_br",
    "ds_local_i",
    "ds_local_f",
    "ds_legenda",
    "sg_legenda",
    "ds_jurisdi",
    "km_dentro_area_estudo",
    "desc_coinc",
    "geometry",
]


def main() -> None:
    caminho_saida = DIR_GEOPORTAL / "malha-viaria-dnit.geojson"
    if caminho_saida.exists():
        logger.info("já existe, pulando: %s", caminho_saida.relative_to(RAIZ_PROJETO))
        return

    gdf = gpd.read_file(CAMINHO_ORIGEM)
    n_original = len(gdf)

    gdf = gdf[COLUNAS].copy()
    gdf["rodovia"] = "BR-" + gdf["vl_br"].astype(str)
    gdf["divergencia_osm"] = (gdf["vl_br"].astype(str) == "377") & (gdf["desc_coinc"] == "Coinc")
    gdf = gdf.drop(columns=["vl_br", "desc_coinc"])

    salvar_geojson_wgs84(
        gdf,
        caminho_saida,
        descricao=(
            "Malha viária federal oficial (DNIT/SNV) — segunda fonte da camada "
            "'Malha viária' do geoportal, ao lado de malha-viaria.geojson (OSM). "
            "Estilizada por pavimentação (Pavimentada/Planejada)."
        ),
        fonte={"caminho_origem": str(CAMINHO_ORIGEM.relative_to(RAIZ_PROJETO))},
        transformacao=(
            "colunas reduzidas a [rodovia (derivada de vl_br), ds_local_i, ds_local_f, "
            "ds_legenda, sg_legenda, ds_jurisdi, km_dentro_area_estudo, divergencia_osm "
            "(derivada: BR-377 coincidente com BR-290, tagueada como BR-290/RSC-377 no OSM)]; "
            "sem simplificação de geometria (18 feições); reprojeção -> EPSG:4326"
        ),
    )
    logger.info("malha viária DNIT: %d features de entrada -> %d exportadas", n_original, len(gdf))


if __name__ == "__main__":
    main()
