"""
Gera a camada de densidade populacional por setor censitário para os Censos
históricos 2000 e 2010, a partir de
data/raw/vetor/setores-censitarios_ibge_{ano}_vetorial.gpkg
(scripts/download/setores_censitarios_historico.py):

    densidade-populacional-2000.geojson
    densidade-populacional-2010.geojson

Só a densidade populacional entra no geoportal para esses dois anos —
"crianças 0-4" e "idosos 60+" por setor (como já existe para 2022) exigiriam
a distribuição etária por setor, que a fonte usada para 2000/2010 não traz
(só população total e domicílios; ver docstring de
scripts/download/setores_censitarios_historico.py).

NÃO comparar/sobrepor estas malhas com a de 2022 (converter_setores_demografia.py)
nem entre si — o IBGE reconhece desalinhamento de fronteira entre malhas de
anos censitários diferentes. O front-end (js/layers.js) trata isso como um
seletor de ano de página única (1 ano de densidade visível por vez, nunca
sobreposto a outro).
"""

from pathlib import Path

import geopandas as gpd

from common import DIR_GEOPORTAL, RAIZ_PROJETO, salvar_geojson_wgs84

CAMINHO_ORIGEM_TEMPLATE = RAIZ_PROJETO / "data" / "raw" / "vetor" / "setores-censitarios_ibge_{ano}_vetorial.gpkg"

COLUNAS = [
    "cd_setor", "situacao", "populacao_total", "domicilios_total",
    "area_km2", "densidade_demografica_hab_km2", "dados_atributivos_ausentes", "geometry",
]


def gerar_densidade_ano(ano: int) -> None:
    caminho_origem = Path(str(CAMINHO_ORIGEM_TEMPLATE).format(ano=ano))
    gdf = gpd.read_file(caminho_origem)[COLUNAS].copy()
    gdf = gdf.rename(columns={"dados_atributivos_ausentes": "sem_dado"})

    salvar_geojson_wgs84(
        gdf,
        DIR_GEOPORTAL / f"densidade-populacional-{ano}.geojson",
        descricao=(
            f"Densidade populacional por setor censitário, Censo {ano}: populacao_total / area_km2. "
            "NÃO comparável geometricamente com a malha de outro ano censitário (a malha de setores "
            "muda de configuração a cada Censo, com desalinhamento de fronteira reconhecido pelo "
            "IBGE) — tratar como uma foto independente da divisão territorial vigente naquele Censo."
        ),
        fonte={"caminho_origem": str(caminho_origem.relative_to(RAIZ_PROJETO))},
        transformacao=(
            f"reprojeção {gdf.crs} -> EPSG:4326; sem_dado=true nos setores sem linha de atributos na "
            "fonte original (não preenchido com zero — ver metadado do gpkg de origem)"
        ),
    )


def main() -> None:
    gerar_densidade_ano(2000)
    gerar_densidade_ano(2010)


if __name__ == "__main__":
    main()
