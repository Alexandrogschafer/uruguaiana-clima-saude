"""
Gera um JSON não-espacial com os indicadores que antes apareciam no popup
da camada "vulnerabilidade socioeconômica" mas que são valores ÚNICOS
para todo o município (confirmado: 1 valor por coluna nas 179 linhas do
gpkg de setores) — sem variação espacial, não fazem sentido como camada
de mapa/choropleth. Exibidos como cartão fixo no painel (seção
"Indicadores municipais"), não no mapa.
"""

import json

import geopandas as gpd

from common import DIR_GEOPORTAL, RAIZ_PROJETO, logger

CAMINHO_ORIGEM = RAIZ_PROJETO / "data" / "processed" / "setores-censitarios_vulnerabilidade_unido.gpkg"
CAMINHO_SAIDA = DIR_GEOPORTAL / "indicadores-municipais.json"

COLUNAS_MUNICIPAIS = [
    "rendimento_medio_domiciliar_per_capita_reais_municipio",
    "pct_domicilios_agua_inadequada_municipio",
    "pct_domicilios_esgoto_inadequado_municipio",
]


def main() -> None:
    if CAMINHO_SAIDA.exists():
        logger.info("já existe, pulando: %s", CAMINHO_SAIDA.relative_to(RAIZ_PROJETO))
        return

    gdf = gpd.read_file(CAMINHO_ORIGEM)

    for coluna in COLUNAS_MUNICIPAIS:
        n_valores_unicos = gdf[coluna].nunique(dropna=False)
        assert n_valores_unicos == 1, (
            f"{coluna} tem {n_valores_unicos} valores distintos — não é mais constante "
            "por município; revisar se ainda faz sentido excluir do mapa"
        )

    indicadores = {coluna: float(gdf[coluna].iloc[0]) for coluna in COLUNAS_MUNICIPAIS}

    saida = {
        "descricao": (
            "Indicadores socioeconômicos com granularidade municipal (não por setor "
            "censitário) — mesma fonte dos setores censitários, mas repetidos como valor "
            "único em todas as linhas do Censo 2022. Exibidos como cartão fixo no painel, "
            "não como camada de mapa, por não variarem espacialmente."
        ),
        "fonte": {"caminho_origem": str(CAMINHO_ORIGEM.relative_to(RAIZ_PROJETO))},
        "nivel": "municipio",
        "indicadores": indicadores,
    }

    CAMINHO_SAIDA.parent.mkdir(parents=True, exist_ok=True)
    CAMINHO_SAIDA.write_text(json.dumps(saida, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("gerado: %s", CAMINHO_SAIDA.relative_to(RAIZ_PROJETO))


if __name__ == "__main__":
    main()
