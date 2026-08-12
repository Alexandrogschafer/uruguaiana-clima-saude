"""
Converte a cobertura móvel (ANATEL) para GeoJSON do geoportal — camada
nova, grupo próprio "Cobertura móvel (ANATEL)", com seletor de trimestre
(re-estiliza a mesma camada já carregada, sem refetch — mesmo princípio
do slider de cota de inundação).

Escopo reduzido (decisão confirmada com o usuário, 2026-08-12): só os
períodos com ano_censo=2022 (os únicos que fazem join direto com a malha
de setores 2022 já usada no geoportal — códigos de setor do Censo 2010
usados pela ANATEL até 09-2024 são incompatíveis, ver
scripts/download/telecom_anatel.py) e só o agregado "Todas" as
operadoras (não abre por operadora individual — o objetivo da camada é
"onde não há cobertura hoje", não análise comparativa entre operadoras).

Join com a malha de setores 2022
----------------------------------
Mesma geometria-base de densidade-populacional/criancas-0-4/idosos-60-mais
(data/processed/setores-censitarios_vulnerabilidade_unido.gpkg, CD_SETOR),
não a lista de setores da própria ANATEL — os 179 setores da malha oficial
do geoportal são o universo de referência, com `sem_dado=true` (mesmo
padrão/tratamento de criancas-0-4/idosos-60-mais) nos que não têm
correspondência na ANATEL. LEFT JOIN, não INNER: mantém os 179, nunca
descarta um setor da malha por falta de dado ANATEL.

ACHADO (não é bug): 175 dos 179 setores da malha batem com algum setor da
ANATEL para ano_censo=2022; os outros 4 ficam sem_dado=true. Do lado da
ANATEL, 198 códigos de setor existem para ano_censo=2022, mas só 175
correspondem a um CD_SETOR da malha oficial — os 23 restantes têm código
de 15 dígitos (mesmo formato, não é problema de zero à esquerda) mas não
batem com nenhum setor da malha, provavelmente uma revisão de malha
diferente da versão oficial 2022 já usada neste projeto. Esses 23 ficam
de fora (LEFT JOIN a partir da malha, não da ANATEL) — documentado aqui,
não recuperável sem uma segunda fonte de correspondência de código.

Também real (não bug): no período mais antigo (12-2024) só 143 dos 175
setores correspondentes têm valor — os outros ficam NaN só NESSE período
(não sem_dado=true geral, que é por setor não por período). O front-end
trata valor ausente num período específico com o mesmo estilo de
sem_dado, mesmo que o setor tenha dado em outros períodos.
"""

import json
from pathlib import Path

import geopandas as gpd
import pandas as pd

from common import DIR_GEOPORTAL, RAIZ_PROJETO, logger, salvar_geojson_wgs84

CAMINHO_MALHA = RAIZ_PROJETO / "data" / "processed" / "setores-censitarios_vulnerabilidade_unido.gpkg"
CAMINHO_ANATEL = RAIZ_PROJETO / "data" / "raw" / "cobertura-movel_anatel_2021-2026-06_setor-censitario.csv"


def _campo_periodo(periodo: str) -> str:
    mes, ano = periodo.split("-")
    return f"cobertura_pct_{ano}{mes}"


def main() -> None:
    caminho_saida = DIR_GEOPORTAL / "cobertura-movel-anatel.geojson"
    if caminho_saida.exists():
        logger.info("já existe, pulando: %s", caminho_saida.relative_to(RAIZ_PROJETO))
        return

    malha = gpd.read_file(CAMINHO_MALHA)[["CD_SETOR", "NM_BAIRRO", "NM_DIST", "geometry"]].copy()
    n_setores_malha = len(malha)

    anatel = pd.read_csv(CAMINHO_ANATEL)
    anatel = anatel[(anatel["ano_censo"] == 2022) & (anatel["operadora"] == "Todas")].copy()
    anatel["codigo_setor"] = anatel["codigo_setor"].astype(str)

    periodos = sorted(anatel["periodo"].unique(), key=lambda p: (p.split("-")[1], p.split("-")[0]))
    n_setores_anatel = anatel["codigo_setor"].nunique()

    pivot = anatel.pivot(index="codigo_setor", columns="periodo", values="cobertura_todas_tecnologias_pct")
    pivot.columns = [_campo_periodo(p) for p in pivot.columns]
    campos_periodo = [_campo_periodo(p) for p in periodos]

    gdf = malha.merge(pivot, left_on="CD_SETOR", right_index=True, how="left")
    gdf["sem_dado"] = gdf[campos_periodo].isna().all(axis=1)
    n_sem_dado = int(gdf["sem_dado"].sum())
    n_so_anatel = n_setores_anatel - (n_setores_malha - n_sem_dado)

    metadados_periodos = {
        "campos_por_periodo": {p: _campo_periodo(p) for p in periodos},
        "periodo_mais_recente": periodos[-1],
        "campo_periodo_mais_recente": _campo_periodo(periodos[-1]),
        "limiar_alerta_pct": 50,
    }
    (DIR_GEOPORTAL / "cobertura-movel-anatel-periodos.json").write_text(
        json.dumps(metadados_periodos, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    salvar_geojson_wgs84(
        gdf,
        caminho_saida,
        descricao=(
            "Cobertura móvel por setor censitário (ANATEL) — escopo reduzido a ano_censo=2022 "
            f"({len(periodos)} períodos: {periodos[0]} a {periodos[-1]}), agregado 'Todas' as operadoras, "
            "todas as tecnologias combinadas (cobertura_todas_tecnologias_pct da fonte). Camada nova do "
            "geoportal, grupo 'Cobertura móvel (ANATEL)', com seletor de período (re-estiliza sem refetch). "
            "Objetivo: identificar onde não há cobertura hoje para planejar comunicação alternativa em "
            "eventos extremos, não análise histórica — por isso só o agregado, sem abrir por operadora/"
            "tecnologia, e com o período mais recente como padrão ao ativar a camada."
        ),
        fonte={"caminho_origem_malha": str(CAMINHO_MALHA.relative_to(RAIZ_PROJETO)), "caminho_origem_anatel": str(CAMINHO_ANATEL.relative_to(RAIZ_PROJETO))},
        transformacao=(
            f"LEFT JOIN da malha de setores 2022 (179 setores, CD_SETOR) com a tabela ANATEL pivotada por "
            f"período (índice codigo_setor); sem_dado=true em {n_sem_dado} setores da malha sem nenhum "
            f"período com valor ANATEL (mesmo padrão de criancas-0-4/idosos-60-mais); "
            f"{n_so_anatel} setores presentes na ANATEL (ano_censo=2022) sem correspondência na malha "
            "oficial ficam de fora (join a partir da malha, não da ANATEL); "
            f"colunas de período: {', '.join(campos_periodo)}; reprojeção -> EPSG:4326"
        ),
    )
    logger.info(
        "cobertura móvel ANATEL: %d setores na malha, %d sem_dado, %d períodos (%s a %s)",
        n_setores_malha, n_sem_dado, len(periodos), periodos[0], periodos[-1],
    )


if __name__ == "__main__":
    main()
