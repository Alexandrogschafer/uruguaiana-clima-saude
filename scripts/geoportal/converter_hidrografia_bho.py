"""
Converte os dados hidrográficos da BHO (ANA) para GeoJSON do geoportal,
recortando pela área de estudo MUNICIPAL oficial (config/area_estudo.geojson)
— não pela área estendida com buffer usada só na etapa de busca/download
(scripts/download/hidrologia_bho.py, config/area_estudo_bacias.geojson).

    bacias/nivel{1..7}.geojson   — ottobacias por nível Otto Pfafstetter
    rede-hidrografica.geojson    — cursos d'água (camada curso_dagua do gpkg
                                   multi-layer; trecho_drenagem/ponto_drenagem
                                   não usados aqui por serem granularidade
                                   técnica demais para uma camada de contexto)

Simplificação de geometria
---------------------------
Mesmo critério de converter_malha_viaria.py: camadas de contexto (não usadas
em cálculo de área/estatística) não precisam de precisão métrica. O nível 7
sem simplificação teria ~36 MB (1.585 feições) — pesado demais pra servir
via fetch() no navegador. Tolerância aplicada em EPSG:31981 (CRS métrico),
antes de reprojetar para 4326.
"""

import geopandas as gpd

from common import DIR_GEOPORTAL, RAIZ_PROJETO, logger, salvar_geojson_wgs84

CAMINHO_AREA_ESTUDO = RAIZ_PROJETO / "config" / "area_estudo.geojson"
DIR_RAW_VETOR = RAIZ_PROJETO / "data" / "raw" / "vetor"
DIR_BACIAS_SAIDA = DIR_GEOPORTAL / "bacias"

NIVEIS_OTTO = range(1, 8)
TOLERANCIA_SIMPLIFICACAO_BACIAS_M = 10
TOLERANCIA_SIMPLIFICACAO_REDE_M = 7.5


def _area_estudo_municipal() -> gpd.GeoDataFrame:
    return gpd.read_file(CAMINHO_AREA_ESTUDO)


def converter_bacias_hidrograficas() -> None:
    area_estudo = _area_estudo_municipal()

    for nivel in NIVEIS_OTTO:
        caminho = DIR_RAW_VETOR / f"bacias-hidrograficas_ana-bho_nivel{nivel}_vetorial.gpkg"
        gdf = gpd.read_file(caminho)
        assert "codigo_otto" in gdf.columns, f"coluna codigo_otto não encontrada em {caminho}"

        gdf_recortado = gpd.clip(gdf, area_estudo)
        gdf_recortado["geometry"] = gdf_recortado.geometry.simplify(
            TOLERANCIA_SIMPLIFICACAO_BACIAS_M, preserve_topology=True
        )

        salvar_geojson_wgs84(
            gdf_recortado,
            DIR_BACIAS_SAIDA / f"nivel{nivel}.geojson",
            descricao=(
                f"Ottobacias nível Otto Pfafstetter {nivel} (BHO 6.2.4, ANA), recortadas pela "
                "área de estudo municipal oficial (não a área estendida com buffer usada na "
                "busca) — nível 1 = bacia mais abrangente, nível 7 = mais detalhado. ATENÇÃO: "
                "o atributo area_m2_bho é a área da bacia completa a montante (escala nacional, "
                "conforme calculada pela própria ANA/BHO), não a área do fragmento recortado "
                "pelo município nem está em m² apesar do nome do campo — comparação com "
                "geometry.area indica que já está em km²; não usado no geoportal por ambiguidade."
            ),
            fonte={"caminho_origem": str(caminho.relative_to(RAIZ_PROJETO))},
            transformacao=(
                f"recorte (gpd.clip) por {CAMINHO_AREA_ESTUDO.relative_to(RAIZ_PROJETO)} "
                f"(área municipal, não a estendida); "
                f"simplify(tolerância={TOLERANCIA_SIMPLIFICACAO_BACIAS_M}m, preserve_topology=True); "
                f"reprojeção {gdf.crs} -> EPSG:4326"
            ),
        )
        logger.info("bacias nível %d: %d feições originais -> %d após recorte", nivel, len(gdf), len(gdf_recortado))


def converter_rede_hidrografica() -> None:
    caminho = DIR_RAW_VETOR / "rede-hidrografica_ana-bho_atual_vetorial.gpkg"
    gdf = gpd.read_file(caminho, layer="curso_dagua")

    area_estudo = _area_estudo_municipal()
    gdf_recortado = gpd.clip(gdf, area_estudo)
    gdf_recortado["geometry"] = gdf_recortado.geometry.simplify(
        TOLERANCIA_SIMPLIFICACAO_REDE_M, preserve_topology=True
    )

    salvar_geojson_wgs84(
        gdf_recortado,
        DIR_GEOPORTAL / "rede-hidrografica.geojson",
        descricao=(
            "Cursos d'água (camada curso_dagua da BHO 6.2.4, ANA), recortados pela área de "
            "estudo municipal oficial (não a área estendida com buffer usada na busca)."
        ),
        fonte={"caminho_origem": f"{caminho.relative_to(RAIZ_PROJETO)} (layer=curso_dagua)"},
        transformacao=(
            f"recorte (gpd.clip) por {CAMINHO_AREA_ESTUDO.relative_to(RAIZ_PROJETO)} "
            f"(área municipal, não a estendida); "
            f"simplify(tolerância={TOLERANCIA_SIMPLIFICACAO_REDE_M}m, preserve_topology=True); "
            f"reprojeção {gdf.crs} -> EPSG:4326"
        ),
    )


def main() -> None:
    converter_bacias_hidrograficas()
    converter_rede_hidrografica()


if __name__ == "__main__":
    main()
