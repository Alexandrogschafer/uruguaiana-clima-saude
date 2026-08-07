"""
Substitui a antiga camada única "vulnerabilidade socioeconômica" (cor fixa,
sem choropleth) por três camadas de setores censitários com atributo
próprio cada uma, a partir da mesma fonte:
data/processed/setores-censitarios_vulnerabilidade_unido.gpkg

    densidade-populacional.geojson  — populacao_total / AREA_KM2 (oficial IBGE)
    criancas-0-4.geojson            — % censitário 0-4 anos + estimativa absoluta
    idosos-60-mais.geojson          — % censitário 60+ anos + estimativa absoluta

Os três outros campos que compunham o popup antigo (renda média
domiciliar per capita, % domicílios com água/esgoto inadequados) são
valores ÚNICOS repetidos em todas as 179 linhas do gpkg — são médias/
percentuais MUNICIPAIS (confirmado: 1 valor único por coluna), não
calculados por setor, então não têm informação espacial para uma camada
de mapa. Vão para o cartão "Indicadores municipais" do painel em vez de
uma camada — ver gerar_indicadores_municipais.py.

Estimativa de contagem absoluta de crianças/idosos
---------------------------------------------------
O gpkg só tem o percentual pré-calculado por setor (pct_populacao_0_a_4_anos,
pct_populacao_60_anos_ou_mais), não a contagem bruta por faixa etária.
A estimativa absoluta aqui é `round(pct/100 * populacao_total)` — uma
aproximação derivada do percentual censitário, não uma contagem direta do
Censo. O front-end deixa isso explícito no popup.

Sigilo censitário
------------------
Os NaN em pct_populacao_0_a_4_anos (4 setores) e pct_populacao_60_anos_ou_mais
(18 setores) são por sigilo censitário (setores com poucos domicílios/
população, confirmado no commit 4b08af0), não ausência real de dado.
Marcados aqui com uma coluna booleana `sem_dado` por camada — o setor é
mantido no GeoJSON (não descartado), só sinalizado para o front-end
estilizar com hachura neutra em vez de excluir do mapa.
"""

from pathlib import Path

import geopandas as gpd

from common import DIR_GEOPORTAL, RAIZ_PROJETO, salvar_geojson_wgs84

CAMINHO_ORIGEM = RAIZ_PROJETO / "data" / "processed" / "setores-censitarios_vulnerabilidade_unido.gpkg"

COLUNAS_BASE = ["CD_SETOR", "NM_BAIRRO", "NM_DIST", "AREA_KM2", "populacao_total", "geometry"]


def _carregar_base() -> gpd.GeoDataFrame:
    gdf = gpd.read_file(CAMINHO_ORIGEM)
    return gdf[COLUNAS_BASE].copy()


def gerar_densidade_populacional() -> None:
    gdf = _carregar_base()
    gdf["densidade_hab_km2"] = (gdf["populacao_total"] / gdf["AREA_KM2"]).round(1)
    salvar_geojson_wgs84(
        gdf,
        DIR_GEOPORTAL / "densidade-populacional.geojson",
        descricao=(
            "Densidade populacional por setor censitário (Censo 2022): "
            "população total / área do setor (AREA_KM2, oficial IBGE)."
        ),
        fonte={"caminho_origem": str(CAMINHO_ORIGEM.relative_to(RAIZ_PROJETO))},
        transformacao=(
            "densidade_hab_km2 = populacao_total / AREA_KM2 (arredondado a 1 casa); "
            f"reprojeção {gdf.crs} -> EPSG:4326"
        ),
    )


def gerar_criancas_0_a_4() -> None:
    gdf = gpd.read_file(CAMINHO_ORIGEM)
    gdf = gdf[["CD_SETOR", "NM_BAIRRO", "NM_DIST", "populacao_total", "pct_populacao_0_a_4_anos", "geometry"]].copy()
    gdf["sem_dado"] = gdf["pct_populacao_0_a_4_anos"].isna()
    # NaN se propaga da divisão automaticamente (setor sem_dado -> estimativa NaN);
    # o driver GeoJSON do GDAL escreve NaN como null, mantendo o campo numérico
    # nos demais setores (evita virar string ao forçar object dtype)
    gdf["estimativa_criancas_0_a_4"] = (
        gdf["pct_populacao_0_a_4_anos"] / 100 * gdf["populacao_total"]
    ).round(0)
    salvar_geojson_wgs84(
        gdf,
        DIR_GEOPORTAL / "criancas-0-4.geojson",
        descricao=(
            "Crianças de 0-4 anos por setor censitário (Censo 2022): percentual direto do "
            "Censo (pct_populacao_0_a_4_anos) + estimativa de contagem absoluta "
            "(round(pct/100 * populacao_total) — aproximação, não contagem direta). "
            "sem_dado=true em setores com percentual omitido por sigilo censitário."
        ),
        fonte={"caminho_origem": str(CAMINHO_ORIGEM.relative_to(RAIZ_PROJETO))},
        transformacao=(
            "estimativa_criancas_0_a_4 = round(pct_populacao_0_a_4_anos/100 * populacao_total); "
            f"reprojeção {gdf.crs} -> EPSG:4326"
        ),
    )


def gerar_idosos_60_mais() -> None:
    gdf = gpd.read_file(CAMINHO_ORIGEM)
    gdf = gdf[["CD_SETOR", "NM_BAIRRO", "NM_DIST", "populacao_total", "pct_populacao_60_anos_ou_mais", "geometry"]].copy()
    gdf["sem_dado"] = gdf["pct_populacao_60_anos_ou_mais"].isna()
    gdf["estimativa_idosos_60_mais"] = (
        gdf["pct_populacao_60_anos_ou_mais"] / 100 * gdf["populacao_total"]
    ).round(0)
    salvar_geojson_wgs84(
        gdf,
        DIR_GEOPORTAL / "idosos-60-mais.geojson",
        descricao=(
            "Idosos de 60 anos ou mais por setor censitário (Censo 2022): percentual direto "
            "do Censo (pct_populacao_60_anos_ou_mais) + estimativa de contagem absoluta "
            "(round(pct/100 * populacao_total) — aproximação, não contagem direta). "
            "sem_dado=true em setores com percentual omitido por sigilo censitário."
        ),
        fonte={"caminho_origem": str(CAMINHO_ORIGEM.relative_to(RAIZ_PROJETO))},
        transformacao=(
            "estimativa_idosos_60_mais = round(pct_populacao_60_anos_ou_mais/100 * populacao_total); "
            f"reprojeção {gdf.crs} -> EPSG:4326"
        ),
    )


def main() -> None:
    gerar_densidade_populacional()
    gerar_criancas_0_a_4()
    gerar_idosos_60_mais()


if __name__ == "__main__":
    main()
