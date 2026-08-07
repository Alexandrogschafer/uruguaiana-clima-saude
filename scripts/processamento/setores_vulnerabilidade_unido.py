"""
Une, num único GeoPackage, a malha de setores censitários (geometria) e os
indicadores de vulnerabilidade socioeconômica do Censo 2022 (tabela), para
evitar que o join precise ser refeito manualmente no QGIS a cada uso.

Entradas (já geradas por scripts/download/vulnerabilidade_censo.py):
    data/raw/vetor/setores-censitarios_ibge_2022_vetorial.gpkg
    data/raw/vulnerabilidade-censo_ibge_2022.csv

Saída:
    data/processed/setores-censitarios_vulnerabilidade_unido.gpkg

Chave de join
-------------
A malha usa a coluna `CD_SETOR` (str, 15 dígitos); o CSV de indicadores usa
`cd_setor` (também str — já lido com dtype=str, sem risco de perder zeros à
esquerda). Os nomes diferem em maiúscula/minúscula, mas o conteúdo e o tipo
são compatíveis; nenhuma conversão de tipo é necessária, só o rename. Isso é
verificado por comparação de conjuntos (não assumido) antes do merge — ver
`validar_join`.

`situacao` e `area_km2` existem tanto na malha (`SITUACAO`/`AREA_KM2`) quanto
no CSV de indicadores (colunas `situacao`/`area_km2`, que
vulnerabilidade_censo.py já copiou da própria malha ao montar a tabela — ver
`montar_tabela_indicadores` naquele script). São o mesmo valor duplicado; o
CSV é a fonte de verdade quando os dois divergirem, mas mantemos apenas as
versões da malha para não duplicar colunas.

Uso:
    python scripts/processamento/setores_vulnerabilidade_unido.py
    python scripts/processamento/setores_vulnerabilidade_unido.py --forcar
"""

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import pandas as pd

sys.path.append(str(Path(__file__).resolve().parents[1] / "utils"))
from recorte_municipio import CRS_PADRAO  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

RAIZ = Path(__file__).resolve().parents[2]
CAMINHO_MALHA = RAIZ / "data" / "raw" / "vetor" / "setores-censitarios_ibge_2022_vetorial.gpkg"
CAMINHO_INDICADORES = RAIZ / "data" / "raw" / "vulnerabilidade-censo_ibge_2022.csv"
CAMINHO_SAIDA = RAIZ / "data" / "processed" / "setores-censitarios_vulnerabilidade_unido.gpkg"

# Colunas da malha mantidas na saída (o restante — CD_SIT, CD_TIPO, CD_REGIAO,
# CD_RGINT etc. — é metadado hierárquico do IBGE não usado no projeto; quem
# precisar pode voltar ao gpkg bruto da malha).
COLUNAS_MALHA = [
    "CD_SETOR", "SITUACAO", "AREA_KM2", "NM_MUN", "NM_BAIRRO", "NM_DIST", "geometry",
]

# Colunas do CSV de indicadores que já existem na malha (ver docstring do
# módulo) — excluídas do merge para não duplicar.
COLUNAS_INDICADORES_DUPLICADAS_NA_MALHA = ["situacao", "area_km2"]


def carregar_malha() -> gpd.GeoDataFrame:
    if not CAMINHO_MALHA.exists():
        raise FileNotFoundError(
            f"{CAMINHO_MALHA} não encontrado. Rode primeiro: python scripts/download/vulnerabilidade_censo.py"
        )
    gdf = gpd.read_file(CAMINHO_MALHA)
    if gdf.crs.to_string() != CRS_PADRAO:
        gdf = gdf.to_crs(CRS_PADRAO)
    logger.info("Malha de setores carregada: %d setores (colunas: %s)", len(gdf), list(gdf.columns))
    return gdf


def carregar_indicadores() -> pd.DataFrame:
    if not CAMINHO_INDICADORES.exists():
        raise FileNotFoundError(
            f"{CAMINHO_INDICADORES} não encontrado. Rode primeiro: python scripts/download/vulnerabilidade_censo.py"
        )
    df = pd.read_csv(CAMINHO_INDICADORES, dtype={"cd_setor": str})
    logger.info("Indicadores de vulnerabilidade carregados: %d setores (colunas: %s)", len(df), list(df.columns))
    return df


def validar_join(gdf_malha: gpd.GeoDataFrame, df_indicadores: pd.DataFrame) -> dict:
    """Confirma que a chave de join casa 1:1 antes de fazer o merge de verdade.

    Levanta erro se houver setor duplicado em qualquer lado (o merge
    silenciosamente multiplicaria linhas). Loga (não levanta erro) setores
    sem correspondência — pode ser legítimo (ex.: setor de água/sem
    população, filtro de sigilo), mas precisa ficar visível e documentado.
    """
    dup_malha = gdf_malha["CD_SETOR"][gdf_malha["CD_SETOR"].duplicated()].tolist()
    dup_csv = df_indicadores["cd_setor"][df_indicadores["cd_setor"].duplicated()].tolist()
    if dup_malha or dup_csv:
        raise ValueError(
            f"Chave de join duplicada — malha: {dup_malha}, CSV de indicadores: {dup_csv}. "
            "Um merge aqui multiplicaria linhas; investigue a origem antes de prosseguir."
        )

    setores_malha = set(gdf_malha["CD_SETOR"])
    setores_csv = set(df_indicadores["cd_setor"])
    so_na_malha = sorted(setores_malha - setores_csv)
    so_no_csv = sorted(setores_csv - setores_malha)

    if so_na_malha:
        logger.warning(
            "%d setor(es) da malha SEM indicador correspondente no CSV (ficarão com colunas de indicador em "
            "branco): %s", len(so_na_malha), so_na_malha,
        )
    if so_no_csv:
        logger.warning(
            "%d linha(s) do CSV de indicadores SEM geometria correspondente na malha (não entram no arquivo "
            "final, que é georreferenciado): %s", len(so_no_csv), so_no_csv,
        )
    if not so_na_malha and not so_no_csv:
        logger.info("Join validado: os %d setores da malha e os %d do CSV de indicadores casam 1:1, sem sobras.",
                     len(gdf_malha), len(df_indicadores))

    return {
        "n_setores_malha": len(gdf_malha),
        "n_setores_csv_indicadores": len(df_indicadores),
        "n_setores_so_na_malha_sem_indicador": len(so_na_malha),
        "setores_so_na_malha_sem_indicador": so_na_malha,
        "n_linhas_so_no_csv_sem_geometria": len(so_no_csv),
        "linhas_so_no_csv_sem_geometria": so_no_csv,
    }


def unir_malha_e_indicadores(gdf_malha: gpd.GeoDataFrame, df_indicadores: pd.DataFrame) -> gpd.GeoDataFrame:
    """Left join (a partir da malha, para preservar geometria) entre setores e indicadores."""
    colunas_indicadores = [c for c in df_indicadores.columns if c not in COLUNAS_INDICADORES_DUPLICADAS_NA_MALHA]
    gdf = gdf_malha[COLUNAS_MALHA].merge(
        df_indicadores[colunas_indicadores], left_on="CD_SETOR", right_on="cd_setor", how="left",
    )
    gdf = gdf.drop(columns=["cd_setor"])  # redundante com CD_SETOR após o merge
    return gdf


def calcular_metadados(gdf_unido: gpd.GeoDataFrame, validacao: dict) -> dict:
    colunas_indicador = [
        "populacao_total", "domicilios_particulares_ocupados", "densidade_demografica_hab_km2",
        "pct_populacao_0_a_4_anos", "pct_populacao_60_anos_ou_mais",
        "rendimento_medio_domiciliar_per_capita_reais_municipio",
        "pct_domicilios_agua_inadequada_municipio", "pct_domicilios_esgoto_inadequado_municipio",
    ]
    colunas_indicador_presentes = [c for c in colunas_indicador if c in gdf_unido.columns]
    n_completos = int(gdf_unido[colunas_indicador_presentes].notna().all(axis=1).sum())
    n_total = len(gdf_unido)

    return {
        "descricao": (
            "Malha de setores censitários do Censo 2022 já unida (join espacial-tabular por código do setor) "
            "com os indicadores de vulnerabilidade socioeconômica, num único GeoPackage — evita repetir o join "
            "manualmente no QGIS a cada uso."
        ),
        "script_gerador": "scripts/processamento/setores_vulnerabilidade_unido.py",
        "crs": CRS_PADRAO,
        "entradas": {
            "malha_setores": {
                "caminho": str(CAMINHO_MALHA.relative_to(RAIZ)),
                "coluna_chave_join": "CD_SETOR",
                "gerado_por": "scripts/download/vulnerabilidade_censo.py",
                "colunas_usadas_na_saida": [c for c in COLUNAS_MALHA if c != "geometry"],
            },
            "indicadores_vulnerabilidade": {
                "caminho": str(CAMINHO_INDICADORES.relative_to(RAIZ)),
                "coluna_chave_join": "cd_setor",
                "gerado_por": "scripts/download/vulnerabilidade_censo.py",
                "colunas_usadas_na_saida": [
                    c for c in colunas_indicador if c in gdf_unido.columns
                ],
                "colunas_descartadas_por_duplicidade_com_malha": COLUNAS_INDICADORES_DUPLICADAS_NA_MALHA,
            },
        },
        "join": {
            "tipo": "left join (malha -> indicadores), preserva todas as geometrias da malha",
            "chave": "CD_SETOR (malha) == cd_setor (CSV) — mesmo conteúdo/tipo (str), apenas nomes diferentes",
            **validacao,
        },
        "n_setores_total": n_total,
        "n_setores_indicadores_completos": n_completos,
        "n_setores_indicadores_incompletos": n_total - n_completos,
        "observacao_indicadores_incompletos": (
            "setores com indicador incompleto são majoritariamente sigilo estatístico do IBGE (célula de faixa "
            "etária com poucos casos, marcada 'X' na fonte e convertida para NaN), não ausência de todo o dado — "
            "ver scripts/download/vulnerabilidade_censo.py"
        ),
        "colunas_indicador_nivel_municipio_replicadas_em_todos_os_setores": [
            "rendimento_medio_domiciliar_per_capita_reais_municipio",
            "pct_domicilios_agua_inadequada_municipio",
            "pct_domicilios_esgoto_inadequado_municipio",
        ],
        "data_processamento": datetime.now(timezone.utc).isoformat(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Une a malha de setores censitários e os indicadores de vulnerabilidade num único GeoPackage."
    )
    parser.add_argument("--forcar", action="store_true", help="Reprocessa mesmo se a saída já existir")
    args = parser.parse_args()

    if CAMINHO_SAIDA.exists() and CAMINHO_SAIDA.with_suffix(".json").exists() and not args.forcar:
        logger.info("Saída já existe (%s) — nada a fazer (use --forcar para refazer).", CAMINHO_SAIDA)
        return

    gdf_malha = carregar_malha()
    df_indicadores = carregar_indicadores()

    validacao = validar_join(gdf_malha, df_indicadores)
    gdf_unido = unir_malha_e_indicadores(gdf_malha, df_indicadores)

    CAMINHO_SAIDA.parent.mkdir(parents=True, exist_ok=True)
    gdf_unido.to_file(CAMINHO_SAIDA, driver="GPKG", layer="setores_censitarios_vulnerabilidade")
    logger.info("GeoPackage unido salvo em %s (%d setores)", CAMINHO_SAIDA, len(gdf_unido))

    metadados = calcular_metadados(gdf_unido, validacao)
    CAMINHO_SAIDA.with_suffix(".json").write_text(
        json.dumps(metadados, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )
    logger.info("Metadados salvos em %s", CAMINHO_SAIDA.with_suffix(".json"))
    logger.info(
        "Concluído: %d setores no arquivo final, %d com indicadores completos, %d com indicador incompleto.",
        len(gdf_unido), metadados["n_setores_indicadores_completos"], metadados["n_setores_indicadores_incompletos"],
    )


if __name__ == "__main__":
    main()
