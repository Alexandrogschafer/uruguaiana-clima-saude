"""
Produtos do painel de clima em data/paineis/ (esquema climapampa-painel/1).

Lê os mesmos arquivos que alimentavam o painel até a rodada 109:
    - temperatura, calor e geada: série horária INMET (estação automática),
      data/raw/clima_inmet_historico-completo_horario.parquet;
    - focos de queimada: data/raw/focos-queimada_inpe_*_pontual.csv;
    - precipitação da tabela anual: data/processed/precipitacao_merge-inpe_*_mensal-anual.json, gerado por
      scripts/processamento/precipitacao_painel_clima.py.
    Havendo mais de um arquivo que casa com o padrão, vale o de coleta mais recente (comum.bruto_mais_recente).

Grava (ids estáveis, sem período; período e dados_ate ficam dentro do JSON):
    clima_inmet-<estacao>_serie_anual
    queimadas_inpe_serie_anual
    clima_tabela_serie_anual
A amostra de 2 dias do rio (rio_ana-77150000_2026-08-06-a-2026-08-07_15min) deixou de ser gerada na rodada 120:
nenhuma tela a lia; a série do rio é de scripts/paineis/dados_rio.py.

Decisões (as mesmas do painel publicado; os números são conferidos contra ele):
    - dia e ano em UTC, como vêm da estação;
    - temperatura média anual = média das médias diárias de temp_ar_c;
      máxima e mínima anuais = extremos de temp_max_c e temp_min_c;
    - dia de calor extremo: máxima do dia >= 35 °C; dia de geada: mínima do dia <= 0 °C;
    - o primeiro ano sai se a série começa depois de janeiro (ano incompleto
      no início); o último ano fica, com aviso de "dados até";
    - hora válida = temp_ar_c presente, sobre as horas do calendário; ano
      (exceto o último) com menos de 90% recebe aviso de lacuna.
CRS não se aplica (séries tabulares).

Uso:
    python scripts/paineis/dados_clima.py [--codigo-ibge 4322400] [--forcar] [--so <id>]
"""

from __future__ import annotations

import argparse
import calendar
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import comum  # noqa: E402

RAIZ = comum.RAIZ
ENTRADA_ESTACAO = RAIZ / "data/raw/clima_inmet_historico-completo_horario.parquet"
PADRAO_QUEIMADAS = "focos-queimada_inpe_*_pontual.csv"
PADRAO_PRECIP = "precipitacao_merge-inpe_*_mensal-anual.json"
# estação do INMET no id (minúscula); o gerador confere que o arquivo é desta estação
ESTACAO_INMET = "A809"
SCRIPT = "scripts/paineis/dados_clima.py"
LIMIAR_HORAS_VALIDAS = 90.0
LIMIAR_CALOR_C = 35.0
LIMIAR_GEADA_C = 0.0


def _fmt_pct(v: float) -> str:
    return f"{v:.1f}".replace(".", ",") + "%"


# ---------------------------------------------------------------- leitura

def entrada_queimadas() -> Path:
    return comum.bruto_mais_recente(RAIZ / "data/raw", PADRAO_QUEIMADAS)


def entrada_precip() -> Path:
    return comum.bruto_mais_recente(RAIZ / "data/processed", PADRAO_PRECIP)


def ler_estacao() -> pd.DataFrame:
    df = pd.read_parquet(ENTRADA_ESTACAO, columns=["codigo_estacao", "datetime_utc", "temp_ar_c", "temp_max_c", "temp_min_c"])
    df["dt"] = pd.to_datetime(df["datetime_utc"], utc=True)
    estacoes = df["codigo_estacao"].dropna().unique()
    if len(estacoes) != 1:
        raise ValueError(f"esperada uma estação no arquivo, há {list(estacoes)}")
    return df


def ids(codigo_ibge: str = "4322400") -> dict[str, str]:
    """Ids estáveis dos produtos (convenção {tema}_{fonte}_{serie|atual}_{resolucao}, ver comum.py)."""
    return {
        "anual": f"clima_inmet-{ESTACAO_INMET.lower()}_serie_anual",
        "queimadas": "queimadas_inpe_serie_anual",
        "tabela": "clima_tabela_serie_anual",
    }


def anos_completos(df: pd.DataFrame) -> list[int]:
    anos = sorted(df["dt"].dt.year.unique().tolist())
    inicio = df["dt"].min()
    if inicio.month > 1:  # série começa no meio do ano: ano incompleto no início sai
        anos = anos[1:]
    return [int(a) for a in anos]


# ---------------------------------------------------------------- cálculo

def calcular_anual(df: pd.DataFrame) -> tuple[list[dict], list[dict], str]:
    anos = anos_completos(df)
    df = df[df["dt"].dt.year.isin(anos)].copy()
    df["dia"] = df["dt"].dt.floor("D")
    df["ano"] = df["dt"].dt.year
    dia = df.groupby(["ano", "dia"]).agg(media=("temp_ar_c", "mean"), tmax=("temp_max_c", "max"), tmin=("temp_min_c", "min"))
    g = df.groupby("ano")
    res = pd.DataFrame({
        "temp_media_anual": dia["media"].groupby("ano").mean().round(2),
        "temp_max_anual": g["temp_max_c"].max(),
        "temp_min_anual": g["temp_min_c"].min(),
        "dias_calor_extremo": dia["tmax"].ge(LIMIAR_CALOR_C).groupby("ano").sum(),
        "dias_geada": dia["tmin"].le(LIMIAR_GEADA_C).groupby("ano").sum(),
    })
    validas = g["temp_ar_c"].count()
    linhas = [{"ano": int(a), "temp_media_anual": float(r.temp_media_anual), "temp_max_anual": float(r.temp_max_anual),
               "temp_min_anual": float(r.temp_min_anual), "dias_calor_extremo": int(r.dias_calor_extremo),
               "dias_geada": int(r.dias_geada)} for a, r in res.iterrows()]

    avisos = []
    ultimo = anos[-1]
    ult_valido = df.loc[df["temp_ar_c"].notna(), "dt"].max()
    if ult_valido < pd.Timestamp(f"{ultimo}-12-31", tz="UTC"):
        avisos.append({"tipo": "parcial", "onde": {"ano": ultimo}, "texto": f"dados até {ult_valido:%d/%m/%Y}"})
    for a in anos[:-1]:
        pct = validas[a] / ((366 if calendar.isleap(a) else 365) * 24) * 100
        if pct < LIMIAR_HORAS_VALIDAS:
            avisos.append({"tipo": "lacuna", "onde": {"ano": int(a)}, "texto": f"{_fmt_pct(round(pct, 1))} das horas válidas em {a}"})
    return linhas, avisos, f"{ult_valido:%Y-%m-%d}"


def calcular_queimadas(codigo_ibge: str) -> list[dict]:
    q = pd.read_csv(entrada_queimadas(), usecols=["codigo_ibge", "ano"], dtype={"codigo_ibge": str})
    q = q[q["codigo_ibge"] == codigo_ibge]
    if q.empty:
        raise ValueError(f"sem focos de queimada para o código IBGE {codigo_ibge}")
    c = q.groupby("ano").size()
    return [{"ano": int(a), "focos": int(n)} for a, n in c.items()]


# ---------------------------------------------------------------- produtos

def gerar(codigo_ibge: str = "4322400", forcar: bool = False, so: str | None = None) -> dict[str, bool]:
    est = ler_estacao()
    cod_est = str(est["codigo_estacao"].iloc[0])
    if cod_est.upper() != ESTACAO_INMET:
        raise ValueError(f"arquivo do INMET é da estação {cod_est}, esperada {ESTACAO_INMET}")
    nome_est = f"INMET {cod_est}"
    fonte_inmet = f"INMET, estação automática {cod_est} (portal de dados históricos), série horária"
    rodape_inmet = f"Temperatura — INMET, estação automática {cod_est} (portal de dados históricos)"
    nomes = ids(codigo_ibge)
    out: dict[str, bool] = {}

    anual, avisos_anual, ate = calcular_anual(est)
    a0, a1 = anual[0]["ano"], anual[-1]["ano"]
    transf_anual = ("média das médias diárias (dia em UTC) de temp_ar_c; extremos de temp_max_c e temp_min_c; "
                    f"dias com máxima >= {LIMIAR_CALOR_C:g} °C e com mínima <= {LIMIAR_GEADA_C:g} °C; "
                    f"aviso de lacuna em ano com menos de {LIMIAR_HORAS_VALIDAS:g}% das horas válidas")
    if not so or so == nomes["anual"]:
        p = comum.montar_produto(
            id=nomes["anual"], codigo_ibge=codigo_ibge, ativos=["CA01"],
            titulo="Temperatura, calor extremo e geada por ano", fonte_curta=nome_est, fonte=fonte_inmet,
            unidade_espacial="estação", unidade_medida="°C; dias", periodo={"inicio": str(a0), "fim": str(a1)},
            dados_ate=ate, selo="conferido", avisos=avisos_anual,
            colunas=[{"nome": "ano", "tipo": "int"}, {"nome": "temp_media_anual", "tipo": "float", "casas": 2},
                     {"nome": "temp_max_anual", "tipo": "float", "casas": 1}, {"nome": "temp_min_anual", "tipo": "float", "casas": 1},
                     {"nome": "dias_calor_extremo", "tipo": "int"}, {"nome": "dias_geada", "tipo": "int"}],
            dados=anual, script=SCRIPT, extras={"fonte_rodape": rodape_inmet, "telas": ["clima"]})
        out[p["id"]] = comum.gravar_produto(p, transformacao=transf_anual, entradas=[ENTRADA_ESTACAO], forcar=forcar)

    queim = calcular_queimadas(codigo_ibge)
    if not so or so == nomes["queimadas"]:
        p = comum.montar_produto(
            id=nomes["queimadas"], codigo_ibge=codigo_ibge, ativos=["CA06"],
            titulo="Focos de queimada por ano", fonte_curta="INPE/BDQUEIMADAS",
            fonte="INPE, BDQueimadas, focos de calor de todos os satélites dentro do limite municipal",
            unidade_espacial="município", unidade_medida="focos",
            periodo={"inicio": str(queim[0]["ano"]), "fim": str(queim[-1]["ano"])},
            dados_ate=f"{queim[-1]['ano']}-12-31", selo="conferido",
            avisos=[{"tipo": "metodo", "texto": "todos os satélites, não só o de referência"}],
            colunas=[{"nome": "ano", "tipo": "int"}, {"nome": "focos", "tipo": "int"}],
            dados=queim, script=SCRIPT, extras={"fonte_rodape": "Focos de queimada — INPE/BDQUEIMADAS", "telas": ["clima"]})
        out[p["id"]] = comum.gravar_produto(p, transformacao="contagem de focos por ano", entradas=[entrada_queimadas()], forcar=forcar)

    if not so or so == nomes["tabela"]:
        precip = {a["ano"]: a["merge_mm"] for a in json.loads(entrada_precip().read_text(encoding="utf-8"))["anual"]}
        focos = {q["ano"]: q["focos"] for q in queim}
        nota_ano = {a["onde"]["ano"]: a["tipo"] for a in avisos_anual if "ano" in a.get("onde", {})}
        tabela = [{"ano": r["ano"], "precipitacao_merge_mm": precip.get(r["ano"]),
                   "temp_media_anual": r["temp_media_anual"], "temp_max_anual": r["temp_max_anual"],
                   "temp_min_anual": r["temp_min_anual"], "dias_calor_extremo": r["dias_calor_extremo"],
                   "dias_geada": r["dias_geada"], "focos": focos.get(r["ano"]), "nota": nota_ano.get(r["ano"])}
                  for r in anual]
        p = comum.montar_produto(
            id=nomes["tabela"], codigo_ibge=codigo_ibge, ativos=["CA01", "CA04", "CA06"],
            titulo="Dados climáticos anuais em tabela", fonte_curta=f"{nome_est}; MERGE/INPE; INPE/BDQUEIMADAS",
            fonte=f"{fonte_inmet}; MERGE (CPTEC/INPE); INPE, BDQueimadas",
            unidade_espacial="estação; área; município", unidade_medida="mm; °C; dias; focos",
            periodo={"inicio": str(a0), "fim": str(a1)}, dados_ate=ate, selo="conferido",
            avisos=avisos_anual + [{"tipo": "lacuna", "texto": f"precipitação pelo MERGE, que começa em {min(precip)}"}],
            colunas=[{"nome": "ano", "tipo": "int"}, {"nome": "precipitacao_merge_mm", "tipo": "float", "casas": 2},
                     {"nome": "temp_media_anual", "tipo": "float", "casas": 2}, {"nome": "temp_max_anual", "tipo": "float", "casas": 1},
                     {"nome": "temp_min_anual", "tipo": "float", "casas": 1}, {"nome": "dias_calor_extremo", "tipo": "int"},
                     {"nome": "dias_geada", "tipo": "int"}, {"nome": "focos", "tipo": "int"},
                     {"nome": "nota", "tipo": "texto", "valores": ["parcial", "lacuna"]}],
            dados=tabela, script=SCRIPT, extras={"fonte_rodape": "", "telas": ["clima"]})
        out[p["id"]] = comum.gravar_produto(
            p, transformacao="junção por ano dos produtos anual da estação, precipitação do MERGE e focos de queimada; "
                             "nota = tipo do aviso do ano (parcial ou lacuna)",
            entradas=[ENTRADA_ESTACAO, entrada_precip(), entrada_queimadas()], forcar=forcar)
    return out


PRODUTOS = list(ids().values())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--codigo-ibge", default="4322400")
    ap.add_argument("--forcar", action="store_true")
    ap.add_argument("--so", help="gera só este produto (id)")
    args = ap.parse_args()
    try:
        for pid, gravou in gerar(args.codigo_ibge, args.forcar, args.so).items():
            print(f"{pid}: {'gravado' if gravou else 'sem mudança'}")
    except (OSError, ValueError, KeyError) as erro:
        print(f"erro: {erro}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
