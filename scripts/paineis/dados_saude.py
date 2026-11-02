"""
Produtos da tela de saúde em data/paineis/ (esquema climapampa-painel/1),
a partir dos arquivos de data/raw gravados pelos scripts de download do DATASUS:

    - SIM:   data/raw/obitos_sim-datasus_<periodo>_municipal.csv (anual)
    - SIH:   data/raw/internacoes-respiratorias_sih-datasus_<periodo>_municipal.csv (mensal)
    - SINAN: data/raw/arboviroses_sinan-datasus_<periodo>_municipal.csv (mensal, anos fechados)
             + data/raw/arboviroses_sinan_<ano>_preliminar.csv (ano em curso)

Grava:
    saude_sim_<inicio>-<fim>_anual                  óbitos totais e respiratórios
    saude_sim-afogamento-enchente_<inicio>-<fim>_quinquenal
                                                    óbitos por afogamento ou enchente somados por 5 anos
    saude_sih_<inicio>-<fim>_anual                  internações totais e respiratórias
    saude_sinan_<inicio>-<fim>_anual                dengue, chikungunya e zika (último ano preliminar)

Decisões (rodada 111; D-B3 e D-B4 do relatório de especificação):
    - séries municipais oficiais anuais vão como a fonte publica: zero como 0,
      sem supressão primária (são microdados públicos já divulgados pelo DATASUS);
    - a única série com contagens pequenas frequentes, óbitos por afogamento ou
      enchente, vai só em quinquênios; a série anual desse agravo NÃO é gravada em
      data/paineis/; quinquênio com 1 a 4 óbitos passa pela supressão de comum.py;
    - ano em que todos os meses vêm sem valor na fonte (série ainda inexistente
      no SINAN) fica null ("—"), nunca 0;
    - ano civil, mês de notificação (SINAN) e de internação (SIH), como na fonte.
CRS não se aplica (séries tabulares).

Uso:
    python scripts/paineis/dados_saude.py [--codigo-ibge 4322400] [--forcar] [--so <id>]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import comum  # noqa: E402

RAIZ = comum.RAIZ
RAW = RAIZ / "data" / "raw"
SCRIPT = "scripts/paineis/dados_saude.py"
FONTE_RODAPE = "SIM, SINAN e SIH — DATASUS/Ministério da Saúde (FTP público, microdados individuais)"
NOTA_SUPRESSAO = ("supressão primária não aplicada: série municipal anual oficial, de microdados públicos já "
                  "divulgados pelo DATASUS; contagens pequenas são valores reais da fonte. A única série com "
                  "contagens pequenas frequentes (óbitos por afogamento ou enchente) vai somada por quinquênio.")
AVISO_QUINQUENIO = "somado por 5 anos para não destacar contagens pequenas"
AGRAVOS = ["dengue", "chikungunya", "zika"]
TAMANHO_QUINQUENIO = 5


def _unico(padrao: str) -> Path:
    achados = sorted(RAW.glob(padrao))
    if len(achados) != 1:
        raise ValueError(f"esperado 1 arquivo para {padrao}, há {len(achados)}")
    return achados[0]


def entradas() -> dict[str, Path]:
    return {
        "sim": _unico("obitos_sim-datasus_*_municipal.csv"),
        "sih": _unico("internacoes-respiratorias_sih-datasus_*_municipal.csv"),
        "sinan": _unico("arboviroses_sinan-datasus_*_municipal.csv"),
        "sinan_prelim": _unico("arboviroses_sinan_*_preliminar.csv"),
        "sinan_prelim_meta": _unico("arboviroses_sinan_*_preliminar.json"),
    }


def _filtrar(df: pd.DataFrame, codigo_ibge: str, nome: str) -> pd.DataFrame:
    df = df[df["codigo_ibge"].astype(str) == codigo_ibge]
    if df.empty:
        raise ValueError(f"{nome}: sem linhas para o código IBGE {codigo_ibge}")
    return df


def soma_ou_nulo(s: pd.Series):
    """Soma dos meses; null se todos os meses vêm sem valor; erro se só parte vier sem valor."""
    if s.isna().all():
        return None
    if s.isna().any():
        raise ValueError("ano com parte dos meses sem valor na fonte; tratar antes de publicar")
    return int(s.sum())


# ---------------------------------------------------------------- cálculo

def calcular_sim(arq: Path, codigo_ibge: str) -> pd.DataFrame:
    df = _filtrar(pd.read_csv(arq), codigo_ibge, "SIM").sort_values("ano")
    if not df["arquivo_disponivel"].all():
        raise ValueError("SIM: há ano sem arquivo disponível na fonte")
    if df["ano"].duplicated().any():
        raise ValueError("SIM: ano repetido")
    return df


def quinquenios(df: pd.DataFrame, coluna: str) -> list[dict]:
    """Blocos de 5 anos a partir do primeiro ano; o último bloco só entra se estiver completo."""
    anos = df["ano"].tolist()
    out = []
    for i in range(0, len(anos) - TAMANHO_QUINQUENIO + 1, TAMANHO_QUINQUENIO):
        bloco = df.iloc[i:i + TAMANHO_QUINQUENIO]
        if bloco["ano"].tolist() != list(range(bloco["ano"].iloc[0], bloco["ano"].iloc[0] + TAMANHO_QUINQUENIO)):
            raise ValueError("anos não consecutivos no quinquênio")
        out.append({"periodo": f"{int(bloco['ano'].iloc[0])}–{int(bloco['ano'].iloc[-1])}",
                    "ano_inicio": int(bloco["ano"].iloc[0]), "ano_fim": int(bloco["ano"].iloc[-1]),
                    "obitos": int(bloco[coluna].sum())})
    return out


def calcular_sih(arq: Path, codigo_ibge: str) -> list[dict]:
    df = _filtrar(pd.read_csv(arq), codigo_ibge, "SIH")
    if not df["arquivo_disponivel"].all():
        raise ValueError("SIH: há mês sem arquivo disponível na fonte")
    meses = df.groupby("ano").size()
    if (meses != 12).any():
        raise ValueError(f"SIH: ano incompleto {meses[meses != 12].to_dict()}")
    g = df.groupby("ano")
    return [{"ano": int(a), "internacoes_total": soma_ou_nulo(r["internacoes_total"]),
             "internacoes_respiratorias": soma_ou_nulo(r["internacoes_respiratorias"])} for a, r in g]


def calcular_sinan(arq: Path, arq_prelim: Path, codigo_ibge: str) -> tuple[list[dict], dict]:
    df = _filtrar(pd.read_csv(arq), codigo_ibge, "SINAN")
    pre = _filtrar(pd.read_csv(arq_prelim), codigo_ibge, "SINAN preliminar")
    linhas = []
    for ano, g in df.groupby("ano"):
        r = {"ano": int(ano), "preliminar": False}
        for ag in AGRAVOS:
            r[ag] = soma_ou_nulo(g.loc[g["agravo"] == ag, "notificacoes"])
        linhas.append(r)
    inicio_serie = {}
    for ag in AGRAVOS:
        com_valor = df[(df["agravo"] == ag) & df["notificacoes"].notna()]["ano"]
        inicio_serie[ag] = int(com_valor.min())
    for ano, g in pre.groupby("ano"):
        if int(ano) in {r["ano"] for r in linhas}:
            raise ValueError(f"ano preliminar {ano} repetido na série fechada")
        r = {"ano": int(ano), "preliminar": True}
        for ag in AGRAVOS:
            # no ano em curso, mês ainda não decorrido vem sem valor: soma só dos meses com valor
            v = g.loc[g["agravo"] == ag, "notificacoes"]
            r[ag] = None if v.isna().all() else int(v.sum())
        linhas.append(r)
    return sorted(linhas, key=lambda r: r["ano"]), inicio_serie


# ---------------------------------------------------------------- produtos

def ids(codigo_ibge: str = "4322400") -> dict[str, str]:
    e = entradas()
    sim = calcular_sim(e["sim"], codigo_ibge)
    a0, a1 = int(sim["ano"].min()), int(sim["ano"].max())
    q = quinquenios(sim, "obitos_afogamento_enchente")
    sih = calcular_sih(e["sih"], codigo_ibge)
    sinan, _ = calcular_sinan(e["sinan"], e["sinan_prelim"], codigo_ibge)
    return {
        "sim": f"saude_sim_{a0}-{a1}_anual",
        "afogamento": f"saude_sim-afogamento-enchente_{q[0]['ano_inicio']}-{q[-1]['ano_fim']}_quinquenal",
        "sih": f"saude_sih_{sih[0]['ano']}-{sih[-1]['ano']}_anual",
        "sinan": f"saude_sinan_{sinan[0]['ano']}-{sinan[-1]['ano']}_anual",
    }


def gerar(codigo_ibge: str = "4322400", forcar: bool = False, so: str | None = None) -> dict[str, bool]:
    e = entradas()
    nomes = ids(codigo_ibge)
    out: dict[str, bool] = {}
    comuns = dict(codigo_ibge=codigo_ibge, unidade_espacial="município (residência)", selo="conferido", script=SCRIPT)
    extras = {"fonte_rodape": FONTE_RODAPE, "telas": ["saude"], "supressao_nota": NOTA_SUPRESSAO}
    meta_extra = {"supressao_nota": NOTA_SUPRESSAO}

    sim = calcular_sim(e["sim"], codigo_ibge)
    a0, a1 = int(sim["ano"].min()), int(sim["ano"].max())
    if not so or so == nomes["sim"]:
        dados = [{"ano": int(r.ano), "obitos_total": int(r.obitos_total), "obitos_respiratorios": int(r.obitos_respiratorios)}
                 for r in sim.itertuples()]
        p = comum.montar_produto(
            id=nomes["sim"], ativos=["SA03"], titulo="Óbitos por ano: total e por causas respiratórias",
            fonte_curta="SIM/DATASUS", fonte="SIM (Sistema de Informação sobre Mortalidade), DATASUS, microdados por município de residência",
            unidade_medida="óbitos", periodo={"inicio": str(a0), "fim": str(a1)}, dados_ate=f"{a1}-12-31",
            avisos=[{"tipo": "metodo", "texto": "agrupamento por causa básica (CID-10) sujeito a validação pela equipe de saúde"}],
            colunas=[{"nome": "ano", "tipo": "int"}, {"nome": "obitos_total", "tipo": "int"}, {"nome": "obitos_respiratorios", "tipo": "int"}],
            dados=dados, extras=extras, **comuns)
        out[p["id"]] = comum.gravar_produto(
            p, transformacao="seleção das colunas de óbitos totais e respiratórios (CID-10 J00-J99) por ano; "
                             "sem a coluna de afogamento ou enchente, que vai em produto quinquenal",
            entradas=[e["sim"]], forcar=forcar, extras_metadado=meta_extra)

    if not so or so == nomes["afogamento"]:
        q = quinquenios(sim, "obitos_afogamento_enchente")
        p = comum.montar_produto(
            id=nomes["afogamento"], ativos=["SA03"], titulo="Óbitos por afogamento ou relacionados a enchente, por quinquênio",
            fonte_curta="SIM/DATASUS", fonte="SIM (Sistema de Informação sobre Mortalidade), DATASUS; causa básica W65-W74 ou X38",
            unidade_medida="óbitos", periodo={"inicio": str(q[0]["ano_inicio"]), "fim": str(q[-1]["ano_fim"])},
            dados_ate=f"{q[-1]['ano_fim']}-12-31",
            avisos=[{"tipo": "metodo", "texto": AVISO_QUINQUENIO},
                    {"tipo": "metodo", "texto": "causa básica W65–W74 (afogamento e submersão acidentais) ou X38 (vítima de inundação)"}],
            colunas=[{"nome": "periodo", "tipo": "texto"}, {"nome": "ano_inicio", "tipo": "int"}, {"nome": "ano_fim", "tipo": "int"},
                     {"nome": "obitos", "tipo": "int"}],
            # sem supressão secundária: nenhum total do agravo é publicado, então nenhum quinquênio sai por subtração
            dados=q, campos_contagem=["obitos"],
            extras={"fonte_rodape": FONTE_RODAPE, "telas": ["saude"]}, **comuns)
        out[p["id"]] = comum.gravar_produto(
            p, transformacao="soma dos óbitos por afogamento ou enchente em blocos de 5 anos; a série anual não é publicada; "
                             "supressão de comum.py (limiar 5) aplicada aos quinquênios",
            entradas=[e["sim"]], forcar=forcar)

    sih = calcular_sih(e["sih"], codigo_ibge)
    if not so or so == nomes["sih"]:
        p = comum.montar_produto(
            id=nomes["sih"], ativos=["SA05"], titulo="Internações e internações respiratórias por ano",
            fonte_curta="SIH/DATASUS", fonte="SIH (Sistema de Informações Hospitalares), DATASUS, AIH por município de residência",
            unidade_medida="internações", periodo={"inicio": str(sih[0]["ano"]), "fim": str(sih[-1]["ano"])},
            dados_ate=f"{sih[-1]['ano']}-12-31", avisos=[],
            colunas=[{"nome": "ano", "tipo": "int"}, {"nome": "internacoes_total", "tipo": "int"}, {"nome": "internacoes_respiratorias", "tipo": "int"}],
            dados=sih, extras=extras, **comuns)
        out[p["id"]] = comum.gravar_produto(p, transformacao="soma dos 12 meses por ano", entradas=[e["sih"]], forcar=forcar,
                                            extras_metadado=meta_extra)

    sinan, inicio = calcular_sinan(e["sinan"], e["sinan_prelim"], codigo_ibge)
    if not so or so == nomes["sinan"]:
        prelim = [r["ano"] for r in sinan if r["preliminar"]]
        meta_pre = json.loads(e["sinan_prelim_meta"].read_text(encoding="utf-8"))
        datas = [d for ag in meta_pre.get("data_maxima_notificacao_no_arquivo_por_agravo", {}).values() for d in ag.values()]
        ate = max(datas) if datas else f"{sinan[-1]['ano']}-12-31"
        avisos = []
        for a in prelim:
            avisos.append({"tipo": "preliminar", "onde": {"ano": a},
                           "texto": f"{a} preliminar: notificações até {pd.Timestamp(ate):%d/%m/%Y}, sujeitas a revisão e a notificação atrasada"})
        tardios = {ag: ini for ag, ini in inicio.items() if ini > sinan[0]["ano"]}
        if tardios:
            avisos.append({"tipo": "lacuna", "onde": {"inicio_serie": tardios},
                           "texto": " e ".join(f"{ag} só tem arquivo próprio no SINAN a partir de {ini}" for ag, ini in tardios.items())
                                    + "; antes disso não há série (—), o que não é contagem zero"})
        p = comum.montar_produto(
            id=nomes["sinan"], ativos=["SA04"], titulo="Notificações de arboviroses por ano",
            fonte_curta="SINAN/DATASUS", fonte="SINAN (Sistema de Informação de Agravos de Notificação), DATASUS; todas as notificações (suspeitos, descartados e confirmados)",
            unidade_medida="notificações", periodo={"inicio": str(sinan[0]["ano"]), "fim": str(sinan[-1]["ano"])},
            dados_ate=ate, avisos=avisos,
            colunas=[{"nome": "ano", "tipo": "int"}, {"nome": "preliminar", "tipo": "bool"}]
                    + [{"nome": ag, "tipo": "int"} for ag in AGRAVOS],
            dados=sinan, extras={**extras, "inicio_serie": inicio}, **comuns)
        out[p["id"]] = comum.gravar_produto(
            p, transformacao="soma dos meses por ano e agravo; ano sem nenhum mês com valor = null; ano em curso "
                             "(arquivo preliminar) somado só nos meses com valor e marcado como preliminar",
            entradas=[e["sinan"], e["sinan_prelim"], e["sinan_prelim_meta"]], forcar=forcar, extras_metadado=meta_extra)
    return out


def _produtos() -> list[str]:
    try:
        return list(ids().values())
    except (OSError, ValueError):
        return []


PRODUTOS = _produtos()


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
