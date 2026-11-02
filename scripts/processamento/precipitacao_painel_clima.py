"""
Precipitação do painel de clima: gera o produto anual e mensal do MERGE/INPE,
com a A809 só para comparação, e injeta os dados no HTML do painel.

Só o bloco entre os marcadores de comentário é substituído no HTML, então
nenhum valor de precipitação do painel é digitado à mão.

Também publica os produtos dos painéis em data/paineis/ (esquema
climapampa-painel/1, via scripts/paineis/comum.py):
    clima_precipitacao-merge_<inicio>-<fim>_anual  (com a A809 para comparação)
    clima_precipitacao-merge_<inicio>-<fim>_mensal (todos os meses)

Decisões:
    - o MERGE é a média de uma área com margem de 18 km em torno do município,
      não um ponto nem o limite municipal; CRS não se aplica (série tabular);
    - hora válida da estação = valor presente sobre as horas do calendário, em UTC;
    - a média de referência exclui o último ano da série.

Uso:
    python scripts/processamento/precipitacao_painel_clima.py --codigo-ibge 4322400 [--forcar]
"""

from __future__ import annotations

import argparse
import calendar
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ / "scripts" / "paineis"))
import comum  # noqa: E402

ENTRADA_MERGE = RAIZ / "data/raw/precipitacao_merge-inpe_2010-2025_mensal.csv"
ENTRADA_ESTACAO = RAIZ / "data/raw/clima_inmet_historico-completo_horario.parquet"
SAIDA = RAIZ / "data/processed/precipitacao_merge-inpe_2010-2025_mensal-anual.json"
PAINEL = RAIZ / "dashboards/painel-clima-ambiental.html"
FONTE_ESPERADA = "merge_inpe_cptec_diario_agregado"
LIMIAR_HORAS_VALIDAS = 90.0
MESES_NO_PAINEL = 60
MARCA_INICIO = "// <precipitacao-merge:inicio>"
MARCA_FIM = "// <precipitacao-merge:fim>"
SCRIPT = "scripts/processamento/precipitacao_painel_clima.py"
# período no nome do produto = período do arquivo de entrada
_PERIODO = re.search(r"_(\d{4}-\d{4})_mensal", ENTRADA_MERGE.name).group(1)
PRODUTOS = [f"clima_precipitacao-merge_{_PERIODO}_anual", f"clima_precipitacao-merge_{_PERIODO}_mensal"]
FONTE_RODAPE = "Precipitação — MERGE/INPE-CPTEC, média de área com margem de 18 km; a A809 aparece só na comparação anual"
AVISO_AREA = {"tipo": "metodo", "texto": "média de uma área com margem de 18 km em torno do município; não é um ponto nem o limite municipal"}


def ler_merge(codigo_ibge: str) -> pd.DataFrame:
    df = pd.read_csv(ENTRADA_MERGE, dtype={"codigo_ibge": str})
    df = df[df["codigo_ibge"] == codigo_ibge]
    if df.empty:
        raise ValueError(f"sem dados do MERGE para o código IBGE {codigo_ibge}")
    fontes = set(df["fonte_dado"])
    if fontes != {FONTE_ESPERADA}:
        raise ValueError(f"fonte inesperada no CSV: {sorted(fontes)}")
    if df["precipitacao_media_mm"].isna().any():
        raise ValueError("há mês sem valor no CSV do MERGE")
    return df.sort_values(["ano", "mes"])


def resumir_estacao() -> pd.DataFrame:
    """Total anual e percentual de horas válidas, sobre as horas do calendário em UTC."""
    df = pd.read_parquet(ENTRADA_ESTACAO, columns=["datetime_utc", "precipitacao_mm", "temp_ar_c"])
    ano = pd.to_datetime(df["datetime_utc"], utc=True).dt.year
    res = df.groupby(ano).agg(
        total=("precipitacao_mm", "sum"),
        validas_prec=("precipitacao_mm", "count"),
        validas_temp=("temp_ar_c", "count"),
    )
    res["horas"] = [(366 if calendar.isleap(a) else 365) * 24 for a in res.index]
    return res


def gerar(codigo_ibge: str) -> dict:
    merge = ler_merge(codigo_ibge)
    estacao = resumir_estacao()
    mensal = [
        {"periodo": f"{int(r.ano)}-{int(r.mes):02d}", "precipitacao_mm": round(float(r.precipitacao_media_mm), 2)}
        for r in merge.itertuples()
    ]
    anual = []
    for ano, grupo in merge.groupby("ano"):
        if len(grupo) != 12:
            raise ValueError(f"{ano} tem {len(grupo)} meses no MERGE")
        e = estacao.loc[int(ano)]
        pct = e["validas_prec"] / e["horas"] * 100
        anual.append({
            "ano": int(ano),
            "merge_mm": round(float(grupo["precipitacao_media_mm"].sum()), 2),
            "a809_mm": round(float(e["total"]), 1),
            "a809_horas_validas_precipitacao_pct": round(float(pct), 1),
            "a809_horas_validas_temperatura_pct": round(float(e["validas_temp"] / e["horas"] * 100), 1),
            "a809_abaixo_de_90_pct": bool(pct < LIMIAR_HORAS_VALIDAS),
        })
    referencia = anual[:-1]
    return {
        "codigo_ibge": codigo_ibge,
        "mensal": mensal,
        "anual": anual,
        "media_referencia": {
            "ano_inicio": referencia[0]["ano"],
            "ano_fim": referencia[-1]["ano"],
            "precipitacao_mm": round(sum(a["merge_mm"] for a in referencia) / len(referencia), 2),
        },
    }


def conferir(dados: dict) -> None:
    """Interrompe se a soma dos meses de algum ano divergir do total anual."""
    for a in dados["anual"]:
        soma = sum(m["precipitacao_mm"] for m in dados["mensal"] if m["periodo"].startswith(f"{a['ano']}-"))
        if abs(soma - a["merge_mm"]) > 0.005:
            raise ValueError(f"{a['ano']}: soma dos meses {soma:.2f} difere do total {a['merge_mm']:.2f}")


def gravar_metadado(dados: dict) -> None:
    anos = [a["ano"] for a in dados["anual"]]
    meta = {
        "fonte": "MERGE (CPTEC/INPE), grade diária agregada para total mensal",
        "fonte_comparacao": "INMET, estação automática A809, série horária (só para comparação)",
        "area": "média de uma área com margem de 18 km em torno do município (config/area_estudo_bacias.geojson); não é um ponto nem o limite municipal",
        "periodo": f"{anos[0]}-{anos[-1]}",
        "data_processamento": datetime.now(timezone.utc).isoformat(),
        "arquivos_entrada": [str(ENTRADA_MERGE.relative_to(RAIZ)), str(ENTRADA_ESTACAO.relative_to(RAIZ))],
        "transformacao_aplicada": "soma dos meses do MERGE por ano; média de referência sem o último ano; total anual da A809 e percentual de horas válidas sobre as horas do calendário, em UTC",
        "crs": "não se aplica (série tabular)",
        "script": "scripts/processamento/precipitacao_painel_clima.py",
    }
    SAIDA.with_name(SAIDA.name + ".json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def injetar(dados: dict, painel: Path) -> bool:
    bloco = {
        "anual": dados["anual"],
        "mensal": dados["mensal"][-MESES_NO_PAINEL:],
        "media_referencia": dados["media_referencia"],
    }
    texto = painel.read_text(encoding="utf-8")
    padrao = re.compile(re.escape(MARCA_INICIO) + r".*?" + re.escape(MARCA_FIM), re.S)
    if len(padrao.findall(texto)) != 1:
        raise ValueError(f"marcadores de precipitação ausentes ou repetidos em {painel.name}")
    novo_bloco = f"{MARCA_INICIO}\nconst PRECIP = {json.dumps(bloco, ensure_ascii=False)};\n{MARCA_FIM}"
    novo = padrao.sub(lambda _: novo_bloco, texto)
    if novo == texto:
        return False
    painel.write_text(novo, encoding="utf-8", newline="")
    return True


def publicar_paineis(dados: dict, forcar: bool = False, so: str | None = None) -> dict[str, bool]:
    """Grava os produtos anual e mensal em data/paineis/ (idempotente)."""
    anual, mensal = dados["anual"], dados["mensal"]
    a0, a1 = anual[0]["ano"], anual[-1]["ano"]
    abaixo = [a["ano"] for a in anual if a["a809_abaixo_de_90_pct"]]
    avisos_anual = [AVISO_AREA]
    if abaixo:
        avisos_anual.append({"tipo": "lacuna", "onde": {"anos": abaixo},
                             "texto": "A809 com menos de 90% das horas válidas em " + " e ".join(map(str, abaixo))})
    comuns = dict(codigo_ibge=dados["codigo_ibge"], fonte_curta="MERGE/INPE",
                  fonte="MERGE (CPTEC/INPE), grade diária agregada para total mensal",
                  unidade_espacial="área (município + margem de 18 km)", unidade_medida="mm",
                  dados_ate=f"{a1}-12-31", selo="conferido", script=SCRIPT)
    entradas = [ENTRADA_MERGE, ENTRADA_ESTACAO]
    prod_anual = comum.montar_produto(
        id=f"clima_precipitacao-merge_{a0}-{a1}_anual", ativos=["CA04", "CA01"],
        titulo="Precipitação total por ano (MERGE) e comparação com a A809",
        periodo={"inicio": str(a0), "fim": str(a1)}, avisos=avisos_anual,
        colunas=[{"nome": "ano", "tipo": "int"}, {"nome": "merge_mm", "tipo": "float", "casas": 2},
                 {"nome": "a809_mm", "tipo": "float", "casas": 1},
                 {"nome": "a809_horas_validas_precipitacao_pct", "tipo": "float", "casas": 1},
                 {"nome": "a809_horas_validas_temperatura_pct", "tipo": "float", "casas": 1},
                 {"nome": "a809_abaixo_de_90_pct", "tipo": "bool"}],
        dados=anual,
        extras={"fonte_rodape": FONTE_RODAPE, "fonte_curta_comparacao": "A809",
                "media_referencia": dados["media_referencia"], "telas": ["clima"]},
        **comuns)
    prod_mensal = comum.montar_produto(
        id=f"clima_precipitacao-merge_{a0}-{a1}_mensal", ativos=["CA04"],
        titulo="Precipitação mensal (MERGE)",
        periodo={"inicio": mensal[0]["periodo"], "fim": mensal[-1]["periodo"]}, avisos=[AVISO_AREA],
        colunas=[{"nome": "periodo", "tipo": "texto"}, {"nome": "precipitacao_mm", "tipo": "float", "casas": 2}],
        dados=mensal, extras={"fonte_rodape": FONTE_RODAPE, "telas": ["clima"]}, **comuns)
    transf = ("soma dos meses do MERGE por ano; média de referência sem o último ano; total anual da A809 e "
              "percentual de horas válidas sobre as horas do calendário, em UTC")
    return {p["id"]: comum.gravar_produto(p, transformacao=transf, entradas=entradas, forcar=forcar)
            for p in (prod_anual, prod_mensal) if not so or p["id"] == so}


def obter_dados(codigo_ibge: str, forcar: bool) -> dict:
    if SAIDA.exists() and not forcar:
        dados = json.loads(SAIDA.read_text(encoding="utf-8"))
        if dados.get("codigo_ibge") != codigo_ibge:
            raise ValueError("o produto existente é de outro município; use --forcar")
        print(f"produto já existe, sem recalcular: {SAIDA.name}")
    else:
        dados = gerar(codigo_ibge)
        SAIDA.parent.mkdir(parents=True, exist_ok=True)
        SAIDA.write_text(json.dumps(dados, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        gravar_metadado(dados)
        print(f"gerado: {SAIDA.name} ({SAIDA.stat().st_size} bytes)")
    conferir(dados)
    return dados


def gerar_paineis(codigo_ibge: str = "4322400", forcar: bool = False, so: str | None = None) -> dict[str, bool]:
    """Entrada usada por scripts/paineis/gerar_todos.py."""
    return publicar_paineis(obter_dados(codigo_ibge, forcar), forcar, so)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--codigo-ibge", default="4322400")
    ap.add_argument("--forcar", action="store_true", help="recalcula mesmo se o produto já existir")
    args = ap.parse_args()
    try:
        dados = obter_dados(args.codigo_ibge, args.forcar)
        print("painel atualizado" if injetar(dados, PAINEL) else "painel já estava atualizado")
        for pid, gravou in publicar_paineis(dados, args.forcar).items():
            print(f"{pid}: {'gravado' if gravou else 'sem mudança'}")
    except (OSError, ValueError, KeyError) as erro:
        print(f"erro: {erro}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
