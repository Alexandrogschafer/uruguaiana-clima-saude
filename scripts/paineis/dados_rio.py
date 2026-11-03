"""
Produtos do rio (estação ANA 77150000) para as telas de síntese, de cheia e de clima, em data/paineis/:

    rio_ana-<estacao>_maximas-anuais          máxima de cada ano civil, do primeiro ano da série ao último ano completo
    rio_ana-<estacao>_ultimos-365-dias_diario média diária de nível e de vazão
    rio_ana-<estacao>_ultima-leitura          última leitura (ou só o aviso, com evento em curso)
    rio_ana-<estacao>_telemetria-recente_diario  telemetria recente, provisória: máxima e última leitura do dia,
                                              do início da janela da série diária até a última leitura (sem trava;
                                              no máximo DIAS_MAX_TELEMETRIA dias)

Dois tipos de produto (rodada 118):
    - consolidados (máximas anuais, média diária dos últimos 365 dias, última leitura): seguem a trava do
      evento em curso descrita em "Regras"; são os que entram em máximas anuais, séries históricas e em
      qualquer conta de recorrência;
    - telemetria recente: nível e vazão como a ANA os devolve, publicados todos os dias, inclusive durante o
      evento, com "provisorio": true, o aviso "dados de telemetria, não consolidados" e a data/hora da
      última leitura. A trava NÃO se aplica a ele. Traz a máxima do período só como leitura de telemetria
      (maior nível e maior vazão da janela, com data e hora); nenhum campo de duração, posição histórica
      ou tempo de retorno. Rodada 121: o campo fim_serie_ana é o último dia com cota na série histórica
      publicada pela ANA (dado bruto ou consistido); todo dia depois dele é provisório (só a telemetria).

Fontes (data/raw, com os metadados .json irmãos):
    - série histórica diária: nivel-rio_ana-serie-historica-<estacao>_1940-*_diario.csv
      (médias diárias e leituras das 7h e 17h, dado bruto e consistido, cota em cm); havendo mais de uma
      coleta da série inteira, vale a de data_acesso mais recente no .json irmão;
    - telemetria de 15 em 15 minutos (nível em cm e vazão em m³/s): os arquivos
      nivel-rio_ana-telemetria-<estacao>_*_15min.csv, concatenados sem repetir leitura.

Regras:
    - máxima anual = maior leitura das 7h ou 17h do ano na série; ano sem leitura = maior média diária
      (mesma regra do produto de conferência da régua; a telemetria não entra). Ano com menos de 80% dos
      dias com algum valor recebe o aviso "ano com lacuna"; ano sem nenhum valor fica null, nunca 0;
    - média diária = média das leituras de 15 min do dia, no fuso em que o serviço devolve (não informado);
      dia sem leitura fica fora da série (não vira 0);
    - evento em curso: leitura acima de LIMIAR_CM a partir de INICIO_BUSCA_EVENTO, ou última leitura acima
      de LIMIAR_CM. Nesse caso (regra do gerador, não da tela): a série diária termina no último dia do mês
      anterior ao do evento quando ele começa em MES_DO_EVENTO_COM_CORTE (30/09/2026 para a cheia de outubro
      de 2026), ou no dia anterior ao primeiro dia acima do limiar nos demais casos; as máximas anuais não incluem o ano do evento; a última
      leitura só traz o aviso, sem valor; nenhum campo de pico, duração ou posição histórica do evento.
      Essas regras valem só para os produtos consolidados; a telemetria recente não tem corte.
CRS não se aplica (séries tabulares).

Uso:
    python scripts/paineis/dados_rio.py [--codigo-ibge 4322400] [--forcar] [--so <id>]
"""

from __future__ import annotations

import argparse
import calendar
import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import comum  # noqa: E402

RAIZ = comum.RAIZ
RAW = RAIZ / "data" / "raw"
SCRIPT = "scripts/paineis/dados_rio.py"
ESTACAO = "77150000"
LIMIAR_CM = 800.0
INICIO_BUSCA_EVENTO = "2026-10-01"
COBERTURA_MIN = 0.80
DIAS_SERIE = 365
DIAS_LEITURA_ANTIGA = 3
# teto da janela da telemetria recente: ~200 bytes por dia no JSON; 450 dias ficam abaixo de 100 kB (regra f).
# Enquanto a trava segura o consolidado, a janela cresce um dia por dia; passando do teto, o início anda.
DIAS_MAX_TELEMETRIA = 450
FONTE_RODAPE = "Nível e vazão do rio — ANA, estação 77150000 (série histórica diária e telemetria de 15 em 15 minutos)"
NOTA_SUPRESSAO = "não se aplica: medidas de nível e vazão, sem contagem de pessoas"
AVISO_PREPARACAO = "cheia de outubro de 2026: números em preparação"
AVISO_PROVISORIO = "dados de telemetria, não consolidados"
# Corte da série diária enquanto o evento estiver em curso: se o primeiro dia acima de LIMIAR_CM cair neste
# mês, a série termina no último dia do mês anterior (cheia de outubro de 2026 → série até 30/09/2026), para
# não publicar nenhum valor do mês do evento. Sai (vira None) quando os números do evento forem conferidos;
# com None, ou para evento em outro mês, vale a regra geral: a série termina no dia anterior ao início do evento.
MES_DO_EVENTO_COM_CORTE = "2026-10"


def data_acesso(arq: Path) -> str:
    """Data da coleta gravada no .json irmão do bruto (ISO 8601, ordenável como texto)."""
    return json.loads(arq.with_suffix(".json").read_text(encoding="utf-8"))["data_acesso"]


def serie_mais_recente(arqs: list[Path]) -> Path:
    """Entre as coletas da série inteira (desde 1940), a de data_acesso mais recente.

    Cada coleta nova da série inteira entra em arquivo próprio (o bruto anterior não é sobrescrito);
    a mais recente contém a anterior e os meses que a ANA publicou depois.
    """
    return max(arqs, key=data_acesso)


def entradas() -> dict:
    serie = sorted(RAW.glob(f"nivel-rio_ana-serie-historica-{ESTACAO}_1940-*_diario.csv"))
    tele = sorted(RAW.glob(f"nivel-rio_ana-telemetria-{ESTACAO}_*_15min.csv"))
    if not serie or not tele:
        raise ValueError("série histórica ou telemetria da estação não encontrada em data/raw")
    return {"serie": serie_mais_recente(serie), "telemetria": tele}


def telemetria(arqs: list[Path]) -> pd.DataFrame:
    t = pd.concat([pd.read_csv(a, dtype={"codigo_estacao": str}) for a in arqs]).drop_duplicates("data_hora").sort_values("data_hora")
    t = t[t["codigo_estacao"] == ESTACAO].dropna(subset=["nivel_cm"])
    t["dia"] = t["data_hora"].str[:10]
    return t


def evento(t: pd.DataFrame) -> dict:
    """Evento em curso e dia de início (primeiro dia acima do limiar a partir da busca)."""
    dia_max = t.groupby("dia")["nivel_cm"].max()
    acima = dia_max[(dia_max.index >= INICIO_BUSCA_EVENTO) & (dia_max > LIMIAR_CM)]
    ultima = t.iloc[-1]
    em_curso = bool(len(acima)) or float(ultima["nivel_cm"]) > LIMIAR_CM
    inicio = acima.index[0] if len(acima) else (ultima["dia"] if em_curso else None)
    if inicio:
        # se o rio já vinha acima do limiar antes da busca, o evento começa no primeiro dia da sequência
        dias = list(dia_max.index)
        j = dias.index(inicio)
        while j > 0 and dia_max.iloc[j - 1] > LIMIAR_CM:
            j -= 1
        inicio = dias[j]
    return {"em_curso": em_curso, "inicio": inicio}


def maximas(arq: Path, ano_excluido: int | None) -> tuple[list, int]:
    h = pd.read_csv(arq, dtype={"codigo_estacao": str})
    h = h[h["codigo_estacao"] == ESTACAO]
    h["ano"] = h["data"].str[:4].astype(int)
    v = h.dropna(subset=["cota_cm"])
    fim = pd.Timestamp(h["data"].max())
    ultimo_completo = fim.year if (fim.month, fim.day) == (12, 31) else fim.year - 1
    if ano_excluido is not None:
        ultimo_completo = min(ultimo_completo, ano_excluido - 1)
    linhas = []
    for a in range(int(h["ano"].min()), ultimo_completo + 1):
        va = v[v["ano"] == a]
        leit, med = va[va["media_diaria"] == 0], va[va["media_diaria"] == 1]
        if len(leit):
            m, origem = float(leit["cota_cm"].max()), "leitura das 7h ou 17h"
        elif len(med):
            m, origem = float(med["cota_cm"].max()), "maior média diária (ano sem leitura)"
        else:
            m, origem = None, "sem valor na fonte"
        cob = va["data"].nunique() / (366 if calendar.isleap(a) else 365)
        linhas.append({"ano": a, "maximo_cm": m, "origem": origem, "dias_com_valor_pct": round(cob * 100, 1), "lacuna": cob < COBERTURA_MIN})
    return linhas, ultimo_completo


def fim_da_serie(ev: dict) -> str | None:
    """Primeiro dia que NÃO entra na série diária (None = sem corte)."""
    if not ev["em_curso"]:
        return None
    if MES_DO_EVENTO_COM_CORTE and ev["inicio"][:7] == MES_DO_EVENTO_COM_CORTE:
        return f"{MES_DO_EVENTO_COM_CORTE}-01"
    return ev["inicio"]


def aviso_corte(fim_excl: str) -> str:
    ultimo = pd.Timestamp(fim_excl) - pd.Timedelta(days=1)
    return f"série até {ultimo:%d/%m/%Y}; {AVISO_PREPARACAO}"


def diario(t: pd.DataFrame, fim_excl: str | None) -> list:
    if fim_excl:
        t = t[t["dia"] < fim_excl]
    g = t.groupby("dia").agg(nivel=("nivel_cm", "mean"), vazao=("vazao_m3s", "mean"), n=("nivel_cm", "size"))
    ult = pd.Timestamp(g.index.max())
    ini = (ult - pd.Timedelta(days=DIAS_SERIE - 1)).strftime("%Y-%m-%d")
    g = g[g.index >= ini]
    return [{"dia": d, "nivel_medio_cm": round(float(r.nivel), 1), "vazao_media_m3s": round(float(r.vazao), 1), "n_leituras": int(r.n)} for d, r in g.iterrows()]


def aviso_tempo_real(t: pd.DataFrame) -> dict:
    return {"tipo": "metodo", "texto": f"não é dado em tempo real; atualizado em {pd.Timestamp(t.iloc[-1]['data_hora']):%d/%m}"}


def ultima_leitura(t: pd.DataFrame, ev: dict, hoje: date) -> tuple[dict, list]:
    """Linha e avisos da última leitura. Com evento em curso: só o aviso, nenhum valor."""
    ult = t.iloc[-1]
    aviso_rt = aviso_tempo_real(t)
    if ev["em_curso"]:
        linha = {"estado": "evento_em_curso", "data_hora": None, "nivel_cm": None, "vazao_m3s": None, "idade_dias": None}
        return linha, [{"tipo": "evento_em_curso", "texto": AVISO_PREPARACAO}, aviso_rt]
    idade = (pd.Timestamp(hoje) - pd.Timestamp(ult["data_hora"]).normalize()).days
    linha = {"estado": "normal", "data_hora": str(ult["data_hora"]), "nivel_cm": float(ult["nivel_cm"]), "vazao_m3s": float(ult["vazao_m3s"]), "idade_dias": int(idade)}
    return linha, [aviso_rt] + ([{"tipo": "parcial", "texto": "leitura antiga"}] if idade > DIAS_LEITURA_ANTIGA else [])


def serie_historica_ate(arq: Path) -> str | None:
    """Último dia com cota na série histórica publicada pela ANA (dado bruto ou consistido). Depois dele só há
    telemetria, que é provisória (rodada 121: antes valia só o dado consistido, que hoje para em 2023)."""
    h = pd.read_csv(arq, dtype={"codigo_estacao": str}, usecols=["codigo_estacao", "data", "cota_cm"])
    h = h[h["codigo_estacao"] == ESTACAO].dropna(subset=["cota_cm"])
    return str(h["data"].max()) if len(h) else None


def _num(v) -> float | None:
    return None if pd.isna(v) else round(float(v), 1)


def telemetria_recente(t: pd.DataFrame, inicio: str) -> list:
    """Máxima e última leitura de cada dia, de `inicio` até a última leitura, sem corte de evento."""
    t = t[t["dia"] >= inicio]
    linhas = []
    for dia, g in t.groupby("dia"):
        u = g.iloc[-1]
        linhas.append({"dia": dia, "nivel_max_cm": _num(g["nivel_cm"].max()), "nivel_ultimo_cm": _num(u["nivel_cm"]),
                       "vazao_max_m3s": _num(g["vazao_m3s"].max()), "vazao_ultima_m3s": _num(u["vazao_m3s"]),
                       "hora_ultima": str(u["data_hora"])[11:16], "n_leituras": int(len(g))})
    return linhas


def maxima_periodo(t: pd.DataFrame, inicio: str) -> dict:
    """Maior nível e maior vazão da janela, cada um com a data/hora da leitura (só leitura de telemetria)."""
    t = t[t["dia"] >= inicio].reset_index(drop=True)  # índice repetido depois do concat das coletas

    def maior(col: str) -> tuple:
        v = t.dropna(subset=[col])
        if not len(v):
            return None, None
        r = v.loc[v[col].idxmax()]
        return _num(r[col]), str(r["data_hora"])[:16]

    (n, n_dh), (q, q_dh) = maior("nivel_cm"), maior("vazao_m3s")
    return {"nivel_cm": n, "nivel_data_hora": n_dh, "vazao_m3s": q, "vazao_data_hora": q_dh}


def ids() -> dict:
    return {"max": f"rio_ana-{ESTACAO}_maximas-anuais", "dia": f"rio_ana-{ESTACAO}_ultimos-365-dias_diario", "ult": f"rio_ana-{ESTACAO}_ultima-leitura",
            "tel": f"rio_ana-{ESTACAO}_telemetria-recente_diario"}


def gerar(codigo_ibge: str = "4322400", forcar: bool = False, so: str | None = None, hoje: date | None = None) -> dict[str, bool]:
    e = entradas()
    t = telemetria(e["telemetria"])
    ev = evento(t)
    hoje = hoje or date.today()
    ano_ev = int(ev["inicio"][:4]) if ev["em_curso"] else None
    mx, ultimo_completo = maximas(e["serie"], ano_ev)
    fim_excl = fim_da_serie(ev)
    dd = diario(t, fim_excl)
    dia_ult = pd.Timestamp(t.iloc[-1]["data_hora"])
    aviso_rt = aviso_tempo_real(t)
    avisos_evento = [{"tipo": "evento_em_curso", "texto": AVISO_PREPARACAO}] if ev["em_curso"] else []
    n = ids()
    c = dict(codigo_ibge=codigo_ibge, script=SCRIPT, unidade_espacial="estação fluviométrica", selo="conferido")
    meta_x = {"supressao_nota": NOTA_SUPRESSAO, "evento_em_curso": ev["em_curso"]}
    lacunas = [r["ano"] for r in mx if r["lacuna"]]
    defs = []
    defs.append((n["max"], ["IN04"], dict(
        titulo="Máxima anual da régua do rio", fonte_curta=f"ANA {ESTACAO}", fonte=f"ANA, série histórica diária da estação {ESTACAO} (leituras das 7h e 17h e médias diárias)",
        unidade_medida="cm", periodo={"inicio": str(mx[0]["ano"]), "fim": str(mx[-1]["ano"])}, dados_ate=f"{ultimo_completo}-12-31",
        avisos=[{"tipo": "lacuna", "onde": {"anos": lacunas}, "texto": "ano com lacuna: menos de 80% dos dias com valor na série"}] + avisos_evento,
        colunas=[{"nome": "ano", "tipo": "int"}, {"nome": "maximo_cm", "tipo": "float"}, {"nome": "origem", "tipo": "texto"},
                 {"nome": "dias_com_valor_pct", "tipo": "float"}, {"nome": "lacuna", "tipo": "bool"}],
        dados=mx, extras={"fonte_rodape": FONTE_RODAPE, "telas": ["cheia-saude"], "evento_em_curso": ev["em_curso"]}),
        [e["serie"]], "maior leitura das 7h ou 17h por ano civil; ano sem leitura = maior média diária; ano do evento em curso fora"))
    defs.append((n["dia"], ["IN05"], dict(
        titulo="Nível e vazão do rio: média diária dos últimos 365 dias", fonte_curta=f"ANA {ESTACAO}", fonte=f"ANA, telemetria da estação {ESTACAO} (leituras de 15 em 15 minutos)",
        unidade_medida="cm; m³/s", periodo={"inicio": dd[0]["dia"], "fim": dd[-1]["dia"]}, dados_ate=dd[-1]["dia"],
        avisos=[aviso_rt, {"tipo": "metodo", "texto": "média das leituras do dia, no fuso em que o serviço as devolve (não informado); dia sem leitura fica fora"}] + (
            [{"tipo": "evento_em_curso", "texto": aviso_corte(fim_excl)}] if ev["em_curso"] else []),
        colunas=[{"nome": "dia", "tipo": "texto"}, {"nome": "nivel_medio_cm", "tipo": "float"}, {"nome": "vazao_media_m3s", "tipo": "float"}, {"nome": "n_leituras", "tipo": "int"}],
        dados=dd, extras={"fonte_rodape": FONTE_RODAPE, "telas": ["cheia-saude"], "evento_em_curso": ev["em_curso"]}),
        e["telemetria"], "média diária das leituras de 15 min, últimos 365 dias; com evento em curso, até o fim do mês anterior ao mês do evento (MES_DO_EVENTO_COM_CORTE) ou até o dia anterior ao evento"))
    linha, av_ult = ultima_leitura(t, ev, hoje)
    defs.append((n["ult"], ["IN05"], dict(
        titulo="Última leitura do rio", fonte_curta=f"ANA {ESTACAO}", fonte=f"ANA, telemetria da estação {ESTACAO}", unidade_medida="cm; m³/s",
        periodo={"inicio": f"{dia_ult:%Y-%m-%d}", "fim": f"{dia_ult:%Y-%m-%d}"}, dados_ate=f"{dia_ult:%Y-%m-%d}", avisos=av_ult,
        colunas=[{"nome": k, "tipo": "texto" if k in ("estado", "data_hora") else ("int" if k == "idade_dias" else "float")} for k in linha],
        dados=[linha], extras={"fonte_rodape": FONTE_RODAPE, "telas": [], "evento_em_curso": ev["em_curso"],
                               "limiar_cm": LIMIAR_CM, "atualizado_em": f"{dia_ult:%Y-%m-%d}"}),
        e["telemetria"], "última leitura da telemetria; com evento em curso, só o aviso"))
    # telemetria recente: sem a trava; começa onde começa a janela da série diária consolidada (01/10/2025 com o
    # corte de 30/09/2026), limitada a DIAS_MAX_TELEMETRIA, e vai até a última leitura, para cobrir o trecho que o
    # consolidado ainda não publica
    ini_tel = max(dd[0]["dia"], (dia_ult.normalize() - pd.Timedelta(days=DIAS_MAX_TELEMETRIA - 1)).strftime("%Y-%m-%d"))
    tel = telemetria_recente(t, ini_tel)
    cons = serie_historica_ate(e["serie"])
    ult_dh = str(t.iloc[-1]["data_hora"])[:16]
    defs.append((n["tel"], ["IN05"], dict(
        titulo="Nível e vazão do rio: telemetria recente (provisória)", fonte_curta=f"ANA {ESTACAO}",
        fonte=f"ANA, telemetria da estação {ESTACAO} (leituras de 15 em 15 minutos, não consolidadas)",
        unidade_medida="cm; m³/s", periodo={"inicio": tel[0]["dia"], "fim": tel[-1]["dia"]}, dados_ate=tel[-1]["dia"],
        avisos=[{"tipo": "preliminar", "texto": AVISO_PROVISORIO}, aviso_rt,
                {"tipo": "metodo", "texto": "máxima e última leitura de cada dia, no fuso em que o serviço as devolve (não informado); dia sem leitura fica fora"}],
        colunas=[{"nome": "dia", "tipo": "texto"}, {"nome": "nivel_max_cm", "tipo": "float"}, {"nome": "nivel_ultimo_cm", "tipo": "float"},
                 {"nome": "vazao_max_m3s", "tipo": "float"}, {"nome": "vazao_ultima_m3s", "tipo": "float"}, {"nome": "hora_ultima", "tipo": "texto"},
                 {"nome": "n_leituras", "tipo": "int"}],
        dados=tel, extras={"fonte_rodape": FONTE_RODAPE, "telas": ["clima", "cheia-saude", "inicio"], "provisorio": True, "fonte_tipo": "ANA telemetria",
                           "ultima_leitura": ult_dh, "fim_serie_ana": cons, "maxima_periodo": maxima_periodo(t, ini_tel)}),
        e["telemetria"] + [e["serie"]], "máxima e última leitura de cada dia da telemetria de 15 min, do início da janela da série diária (no máximo 450 dias) até a última leitura; sem a trava do evento (produto provisório)"))
    out = {}
    for pid, ativos, kw, ent, transf in defs:
        if so and so != pid:
            continue
        p = comum.montar_produto(id=pid, ativos=ativos, **c, **kw)
        out[pid] = comum.gravar_produto(p, transformacao=transf, entradas=list(ent) if isinstance(ent, list) else [ent], forcar=forcar, extras_metadado=meta_x)
    return out


PRODUTOS = list(ids().values())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--codigo-ibge", default="4322400")
    ap.add_argument("--forcar", action="store_true")
    ap.add_argument("--so")
    a = ap.parse_args()
    try:
        for pid, g in gerar(a.codigo_ibge, a.forcar, a.so).items():
            print(f"{pid}: {'gravado' if g else 'sem mudança'}")
    except (OSError, ValueError, KeyError) as erro:
        print(f"erro: {erro}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
