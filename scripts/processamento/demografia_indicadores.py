"""
Calcula a série de população municipal e os indicadores demográficos
derivados (crescimento, envelhecimento, dependência, urbano/rural,
população vulnerável) a partir dos dados brutos SIDRA baixados por
scripts/download/demografia_ibge_sidra.py.

Gera em data/processed/:
    populacao-serie-temporal_ibge-sidra_2000-2025_municipal.csv
    demografia-indicadores_ibge-sidra_2000-2010-2022_municipal.csv

Nível de agregação: SEMPRE municipal, nunca por setor censitário — a malha de
setores muda a cada Censo (2000/2010/2022 não são comparáveis espacialmente
entre si), então esta série usa apenas o nível de agregação estável no tempo.
O detalhe espacial por setor (só Censo 2022) está em
data/raw/vulnerabilidade-censo_ibge_2022.csv e não é duplicado aqui.

Uso:
    python scripts/processamento/demografia_indicadores.py
"""

import argparse
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

RAW_DIR = Path(__file__).resolve().parents[2] / "data" / "raw"
PROCESSED_DIR = Path(__file__).resolve().parents[2] / "data" / "processed"

# Grupos etários de 5 anos com nomes idênticos entre as tabelas 200 (2000/2010) e 9514 (2022) —
# ver scripts/download/demografia_ibge_sidra.py para a verificação nos metadados SIDRA.
FAIXAS_0_4 = ["0 a 4 anos"]
FAIXAS_0_14 = ["0 a 4 anos", "5 a 9 anos", "10 a 14 anos"]
FAIXAS_15_64 = [
    "15 a 19 anos", "20 a 24 anos", "25 a 29 anos", "30 a 34 anos", "35 a 39 anos",
    "40 a 44 anos", "45 a 49 anos", "50 a 54 anos", "55 a 59 anos", "60 a 64 anos",
]
FAIXAS_60_MAIS = [
    "60 a 64 anos", "65 a 69 anos", "70 a 74 anos", "75 a 79 anos", "80 a 84 anos",
    "85 a 89 anos", "90 a 94 anos", "95 a 99 anos", "100 anos ou mais",
]
FAIXAS_65_MAIS = [
    "65 a 69 anos", "70 a 74 anos", "75 a 79 anos", "80 a 84 anos",
    "85 a 89 anos", "90 a 94 anos", "95 a 99 anos", "100 anos ou mais",
]
# Censos 1970/1980/1991: a tabela 200 só publica a abertura fina de 80+ (80-84...100+) a partir de 2000 —
# nesses 3 censos, só a categoria agregada "80 anos ou mais" tem dado (confirmado por inspeção real: todas as
# faixas finas de 80+ vêm NaN). Usa-se a agregada como equivalente nesses períodos.
PERIODOS_SEM_QUEBRA_FINA_80_MAIS = {1970, 1980, 1991}
FAIXAS_60_MAIS_COARSE = ["60 a 64 anos", "65 a 69 anos", "70 a 74 anos", "75 a 79 anos", "80 anos ou mais"]
FAIXAS_65_MAIS_COARSE = ["65 a 69 anos", "70 a 74 anos", "75 a 79 anos", "80 anos ou mais"]

CENSOS = [1970, 1980, 1991, 2000, 2010, 2022]
# (período, período anterior, nº de anos entre eles) — para a taxa de crescimento geométrico anual
INTERVALOS_CRESCIMENTO = [(1980, 1970, 10), (1991, 1980, 11), (2000, 1991, 9), (2010, 2000, 10), (2022, 2010, 12)]


def carregar_populacao_por_faixa(periodo: int) -> pd.DataFrame:
    """Retorna população por grupo de idade (sexo=Total) para 1970-2010 (tabela 200) ou 2022 (tabela 9514)."""
    if periodo == 2022:
        df = pd.read_csv(RAW_DIR / "populacao-sexo-idade_ibge-sidra-tabela9514_2022_municipal.csv")
        return df[df["sexo"] == "Total"][["grupo_idade", "populacao"]]
    df = pd.read_csv(RAW_DIR / "populacao_ibge-sidra-tabela200_1970-2010_municipal.csv")
    sub = df[(df["periodo"] == periodo) & (df["sexo"] == "Total") & (df["situacao_domicilio"] == "Total")]
    return sub[["grupo_idade", "populacao"]]


def carregar_urbano_rural(periodo: int) -> tuple[float, float, float]:
    """Retorna (total, urbana, rural) para 2000/2010 (tabela 202) ou 2022 (tabela 9923)."""
    if periodo == 2022:
        df = pd.read_csv(RAW_DIR / "situacao-domicilio_ibge-sidra-tabela9923_2022_municipal.csv")
        total = df[df["situacao_domicilio"] == "Total"]["populacao"].values[0]
        urbana = df[df["situacao_domicilio"] == "Urbana"]["populacao"].values[0]
        rural = df[df["situacao_domicilio"] == "Rural"]["populacao"].values[0]
        return total, urbana, rural
    df = pd.read_csv(RAW_DIR / "situacao-domicilio_ibge-sidra-tabela202_1970-2010_municipal.csv")
    sub = df[(df["periodo"] == periodo) & (df["sexo"] == "Total")]
    total = sub[sub["situacao_domicilio"] == "Total"]["populacao"].values[0]
    urbana = sub[sub["situacao_domicilio"] == "Urbana"]["populacao"].values[0]
    rural = sub[sub["situacao_domicilio"] == "Rural"]["populacao"].values[0]
    return total, urbana, rural


def montar_serie_temporal() -> pd.DataFrame:
    """Consolida população total 1970-2025: censos (200/9514) + estimativas anuais (6579, só cobre 2001+)."""
    linhas = []

    for periodo in CENSOS:
        pop = carregar_populacao_por_faixa(periodo).query("grupo_idade == 'Total'")["populacao"].iloc[0]
        linhas.append({"ano": periodo, "populacao_total": pop, "fonte": "censo"})

    df_est = pd.read_csv(RAW_DIR / "populacao-estimada_ibge-sidra-tabela6579_2001-2025_municipal.csv")
    for _, linha in df_est.iterrows():
        linhas.append({"ano": int(linha["periodo"]), "populacao_total": linha["populacao"], "fonte": "estimativa"})

    serie = pd.DataFrame(linhas).sort_values("ano").reset_index(drop=True)
    anos_faltantes_intercensitarios = sorted(set(range(2000, 2026)) - set(serie["ano"]))
    logger.info(
        "Série temporal: %d pontos (1970-2025; 1970-1991 só pontos de censo, sem estimativa anual — tabela 6579 "
        "só cobre 2001+); anos sem dado dentro de 2000-2025: %s (Contagem da População, não baixada)",
        len(serie), anos_faltantes_intercensitarios,
    )
    return serie


def taxa_crescimento_geometrico(pop_inicial: float, pop_final: float, anos: int) -> float:
    """Taxa de crescimento geométrico anual (%): r = ((P_final/P_inicial)^(1/anos) - 1) * 100."""
    return round((((pop_final / pop_inicial) ** (1 / anos)) - 1) * 100, 3)


def montar_indicadores_censitarios() -> pd.DataFrame:
    linhas = []
    populacoes_totais = {}

    for periodo in CENSOS:
        faixas = carregar_populacao_por_faixa(periodo)
        pop_total = faixas.query("grupo_idade == 'Total'")["populacao"].iloc[0]
        pop_0_4 = faixas[faixas["grupo_idade"].isin(FAIXAS_0_4)]["populacao"].sum()
        pop_0_14 = faixas[faixas["grupo_idade"].isin(FAIXAS_0_14)]["populacao"].sum()
        pop_15_64 = faixas[faixas["grupo_idade"].isin(FAIXAS_15_64)]["populacao"].sum()

        if periodo in PERIODOS_SEM_QUEBRA_FINA_80_MAIS:
            pop_60_mais = faixas[faixas["grupo_idade"].isin(FAIXAS_60_MAIS_COARSE)]["populacao"].sum()
            pop_65_mais = faixas[faixas["grupo_idade"].isin(FAIXAS_65_MAIS_COARSE)]["populacao"].sum()
        else:
            pop_60_mais = faixas[faixas["grupo_idade"].isin(FAIXAS_60_MAIS)]["populacao"].sum()
            pop_65_mais = faixas[faixas["grupo_idade"].isin(FAIXAS_65_MAIS)]["populacao"].sum()

        _, urbana, rural = carregar_urbano_rural(periodo)

        populacoes_totais[periodo] = pop_total
        linhas.append({
            "periodo": periodo,
            "populacao_total": pop_total,
            "indice_envelhecimento": round(100 * pop_60_mais / pop_0_14, 2),
            "razao_dependencia": round(100 * (pop_0_14 + pop_65_mais) / pop_15_64, 2),
            "pct_urbano": round(100 * urbana / pop_total, 2),
            "pct_rural": round(100 * rural / pop_total, 2),
            "pop_0_4_anos": pop_0_4,
            "pct_pop_0_4_anos": round(100 * pop_0_4 / pop_total, 2),
            "pop_60_anos_ou_mais": pop_60_mais,
            "pct_pop_60_anos_ou_mais": round(100 * pop_60_mais / pop_total, 2),
        })

    df = pd.DataFrame(linhas)
    df["taxa_crescimento_geometrico_anual_pct"] = None
    for periodo, periodo_anterior, anos in INTERVALOS_CRESCIMENTO:
        df.loc[df["periodo"] == periodo, "taxa_crescimento_geometrico_anual_pct"] = taxa_crescimento_geometrico(
            populacoes_totais[periodo_anterior], populacoes_totais[periodo], anos
        )

    colunas = [
        "periodo", "populacao_total", "taxa_crescimento_geometrico_anual_pct",
        "indice_envelhecimento", "razao_dependencia", "pct_urbano", "pct_rural",
        "pop_0_4_anos", "pct_pop_0_4_anos", "pop_60_anos_ou_mais", "pct_pop_60_anos_ou_mais",
    ]
    return df[colunas]


def salvar_com_metadados(df: pd.DataFrame, nome_arquivo: str, metadados: dict) -> None:
    caminho_csv = PROCESSED_DIR / nome_arquivo
    df.to_csv(caminho_csv, index=False, encoding="utf-8")
    logger.info("Salvo %s (%d linhas)", caminho_csv, len(df))

    metadados_completos = {**metadados, "data_processamento": datetime.now(timezone.utc).isoformat()}
    caminho_json = caminho_csv.with_suffix(".json")
    caminho_json.write_text(json.dumps(metadados_completos, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    logger.info("Metadados salvos em %s", caminho_json)


def main() -> None:
    parser = argparse.ArgumentParser(description="Calcula indicadores demográficos derivados a partir dos dados brutos SIDRA.")
    parser.parse_args()

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

    serie = montar_serie_temporal()
    salvar_com_metadados(
        serie, "populacao-serie-temporal_ibge-sidra_1970-2025_municipal.csv",
        {
            "descricao": "População total municipal, 1970-2025 — todos os 6 Censos (1970/1980/1991/2000/2010/2022) + estimativas anuais IBGE nos anos entre censos a partir de 2001",
            "fontes": {
                "censo": "SIDRA tabelas 200 (1970/1980/1991/2000/2010) e 9514 (2022) — contagem exata do Censo",
                "estimativa": "SIDRA tabela 6579 — estimativa populacional anual do IBGE, só publicada a partir de 2001",
            },
            "limitacao_comparabilidade": (
                "entre 1970 e 2000 só há os 3 pontos de censo (1970/1980/1991), sem estimativa anual preenchendo "
                "o intervalo — tabela 6579 só cobre 2001 em diante; interpolar visualmente entre censos distantes "
                "(9-11 anos) é uma aproximação grosseira, diferente do trecho 2001-2025 que tem 1 ponto por ano. "
                "2007 e 2023 (Contagem da População) também não estão nesta série — tabela 6579 não os publica e "
                "a Contagem da População (tabela separada) não foi priorizada para este estudo. Nível SEMPRE "
                "municipal, nunca por setor censitário (malha de setores muda a cada Censo, não comparável "
                "espacialmente entre pontos)."
            ),
        },
    )

    indicadores = montar_indicadores_censitarios()
    salvar_com_metadados(
        indicadores, "demografia-indicadores_ibge-sidra_1970-2022_municipal.csv",
        {
            "descricao": (
                "Indicadores demográficos derivados, nível municipal, para os 6 Censos (1970/1980/1991/2000/2010/2022). "
                "1970/1980/1991 foram incluídos a pedido, além do trio 2000/2010/2022 do escopo original — os dados "
                "de sexo x idade x situação do domicílio já estavam disponíveis nas tabelas 200/202 (Etapa A) e são "
                "robustos o bastante para os mesmos indicadores, com uma única ressalva na faixa 80+ (ver "
                "'grupos_etarios_usados')."
            ),
            "formulas": {
                "taxa_crescimento_geometrico_anual_pct": (
                    "((população_final / população_inicial) ^ (1 / nº de anos) - 1) × 100, calculada sobre o "
                    "intervalo até o censo anterior: 1980 sobre 1970-1980 (10 anos), 1991 sobre 1980-1991 (11 anos), "
                    "2000 sobre 1991-2000 (9 anos), 2010 sobre 2000-2010 (10 anos), 2022 sobre 2010-2022 (12 anos); "
                    "vazio (null) para 1970, sem censo anterior nesta série para servir de base"
                ),
                "indice_envelhecimento": "(população 60 anos ou mais / população 0-14 anos) × 100",
                "razao_dependencia": "((população 0-14 anos + população 65 anos ou mais) / população 15-64 anos) × 100 — fórmula padrão IBGE (idoso = 65+, não 60+, para não sobrepor com o limite superior do grupo 15-64)",
                "pct_urbano": "população em situação urbana / população total × 100 (1970-2010: SIDRA tabela 202; 2022: tabela 9923)",
                "pct_rural": "população em situação rural / população total × 100 (mesma fonte de pct_urbano)",
                "pop_0_4_anos / pct_pop_0_4_anos": "população residente na faixa etária '0 a 4 anos' (grupo etário SIDRA), absoluto e % da população total — proxy de primeira infância",
                "pop_60_anos_ou_mais / pct_pop_60_anos_ou_mais": "soma das faixas etárias de 60 anos ou mais em diante, absoluto e % da população total",
            },
            "grupos_etarios_usados": {
                "0_14": FAIXAS_0_14,
                "15_64": FAIXAS_15_64,
                "60_mais_2000_2010_2022": FAIXAS_60_MAIS,
                "65_mais_2000_2010_2022": FAIXAS_65_MAIS,
                "60_mais_1970_1980_1991": FAIXAS_60_MAIS_COARSE,
                "65_mais_1970_1980_1991": FAIXAS_65_MAIS_COARSE,
                "nota": (
                    "a tabela 200 só abre 80+ em faixas finas (80-84...100+) a partir do Censo 2000 — em "
                    "1970/1980/1991 essas faixas finas vêm vazias na fonte (confirmado por inspeção real dos "
                    "dados brutos) e usa-se a categoria agregada '80 anos ou mais' no lugar. O resultado agregado "
                    "de pop_60_anos_ou_mais é equivalente nos dois casos (mesma cobertura etária), só muda o "
                    "nível de detalhe interno — não afeta a comparabilidade do indicador entre os 6 censos."
                ),
            },
            "nivel_agregacao": (
                "municipal, não por setor censitário — a malha de setores muda de configuração a cada Censo, "
                "inviabilizando comparação espacial direta por setor entre pontos no tempo. O detalhe por setor "
                "censitário (só Censo 2022, malha atual) já existe em data/raw/vulnerabilidade-censo_ibge_2022.csv "
                "e não é duplicado aqui."
            ),
        },
    )


if __name__ == "__main__":
    main()
