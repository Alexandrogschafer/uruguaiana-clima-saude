"""
Cheia de outubro de 2026: régua, passagens Sentinel e água observada.

Três etapas, com a leitura das cenas entre a segunda e a terceira:

  --etapa regua       série diária da régua com a telemetria nova (leituras repetidas
                      entre arquivos de telemetria contam uma vez só; vale a do arquivo
                      consultado por último), diferença da telemetria nova para a já
                      gravada nos horários em comum, o episódio de cheia em curso pela
                      regra do inventário (média diária no limiar ou acima, junção com
                      até --intervalo-dias abaixo), a tendência das últimas horas e a
                      posição do pico entre os máximos anuais da série (maior leitura).
  --etapa passagens   consulta o catálogo (só metadados) de radar e óptico sobre a área
                      urbana com margem, diz o nível da régua de cada passagem e se ela
                      já foi lida, e grava a lista do que ler (radar só das órbitas com
                      referência de rio baixo, mais uma passagem de rio baixo antes do
                      episódio em cada órbita; óptico com nuvem declarada até
                      --nuvem-max), com prioridade e teto de arquivos. Figura da régua.
  --etapa agua        classifica cada passagem lida com as funções, os limiares, as
                      referências de órbita, o leito de referência, o domínio e o
                      controle de qualidade de agua_observada_sentinel_area_urbana.py,
                      como em agua_observada_sentinel_cheias_novas.py (nada muda na
                      regra), compara as cenas boas com as manchas por cota e refaz a
                      curva nível × área com as cenas boas já existentes mais as novas.
                      Opcional: grava a tabela das cenas boas já existentes e das novas
                      para scripts/processamento/imagens_sentinel_para_sig.py
                      (--cenas-extras), fora do repositório.

As cenas são achadas em data/raw/sentinel/ pelo identificador gravado no .json de
cada arquivo (scripts/download/sentinel_planetary_computer.py --bandas-extras).
Saídas em data/processed/agua_observada_sentinel_cheia_2026_10/ (fora do git);
figuras só em --figuras (fora do repositório). Produtos DERIVADOS, para conferência.

Uso:
  python scripts/processamento/agua_observada_sentinel_cheia_2026_10.py --etapa regua
  python scripts/processamento/agua_observada_sentinel_cheia_2026_10.py --etapa passagens [--figuras PASTA]
  python scripts/processamento/agua_observada_sentinel_cheia_2026_10.py --etapa agua \
      [--mancha-extra COTA=ARQUIVO.gpkg:CAMADA] [--figuras PASTA] [--imagens PASTA] [--tabela-para-imagens ARQUIVO.csv]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402
from rasterio.transform import from_origin  # noqa: E402
from rasterio.warp import Resampling  # noqa: E402
from shapely.geometry import box  # noqa: E402

sys.path.append(str(Path(__file__).resolve().parents[1] / "download"))
import agua_observada_sentinel as ao  # noqa: E402
import agua_observada_sentinel_area_urbana as au  # noqa: E402
import agua_observada_sentinel_cheias_novas as cn  # noqa: E402
import agua_observada_sentinel_curva_nivel_area as cna  # noqa: E402
import dinamica_populacional_comum as c  # noqa: E402
import exposicao_inundacao_cenarios as ec  # noqa: E402
import exposicao_inundacao_enderecos as ee  # noqa: E402
import imagens_sentinel_para_sig as im  # noqa: E402
import sentinel_inventario_cheias as inv  # noqa: E402

logger = logging.getLogger(__name__)

SCRIPT = "scripts/processamento/agua_observada_sentinel_cheia_2026_10.py"
MOTIVO = "cheia de outubro de 2026"
PROC = c.RAIZ / "data" / "processed" / "agua_observada_sentinel_cheia_2026_10"
PROC44, PROC45, PROC49 = au.PROC, cna.PROC, cn.PROC
RES, RADAR, OPTICO = au.RES, au.RADAR, au.OPTICO
NOME = {RADAR: "radar", OPTICO: "óptico"}
ARQ_PASSAGENS = "passagens-no-catalogo_planetary-computer_2026-09-2026-10_passagem"
ARQ_LISTA = "passagens-a-ler_planetary-computer_2026-09-2026-10_passagem"
ARQ_C2 = "controle-de-qualidade-das-cenas-da-cheia_sentinel_2026-09-2026-10_cena"
ARQ_C2_49 = "controle-de-qualidade-das-cenas-novas_sentinel_2016-2026_cena"
SGB_FIGURA = {833: "#1b9e77", 952: "#ff7f00", 1053: "#e7298a", 1205: "#7570b3"}  # cotas desenhadas na figura da régua
LIMITACOES = [*cn.LIMITACOES, "o fuso das leituras da telemetria não é informado pelo serviço",
              "a última passagem disponível depende do atraso de processamento do catálogo; passagens posteriores ainda não existiam na consulta"]
NAO_GRAVAR = ("figuras", "imagens", "mancha_extra", "tabela_para_imagens")  # caminhos locais não vão para os metadados


# ---------------------------------------------------------------- gravação
def rel(p: Path) -> str:
    return str(Path(p).relative_to(c.RAIZ))


def meta(caminho: Path, **kw) -> None:
    c.gravar_meta(caminho, crs=c.CRS_PADRAO, status=c.STATUS_CONFERENCIA, ligado_ao_portal=False, script=SCRIPT, motivo=MOTIVO, fontes=FONTES,
                  fora_do_git="data/processed/ é ignorado; produto derivado, para conferência", limitacoes=LIMITACOES, avisos=AVISOS or None,
                  argumentos={k: v for k, v in vars(ARGS).items() if k not in NAO_GRAVAR}, **kw)


def gravar(t: pd.DataFrame, nome: str, titulo: str, colunas: dict, nota: str = "", casas: dict | None = None, **kw) -> Path:
    """Tabela: .csv (sem arredondar) + .md (leitura) + .json com a descrição de cada coluna."""
    faltam = [x for x in t.columns if x not in colunas]
    if faltam:
        raise KeyError(f"{nome}: coluna sem descrição: {faltam}")
    casas = casas or {}
    arq = PROC / f"{nome}.csv"
    t.to_csv(arq, index=False)
    lin = [f"**{titulo}**", "", "| " + " | ".join(t.columns) + " |", "|" + "|".join("---:" if pd.api.types.is_numeric_dtype(t[x]) and not pd.api.types.is_bool_dtype(t[x]) else "---" for x in t.columns) + "|"]
    lin += ["| " + " | ".join(ao.fmt(v, casas.get(col, 2)) for col, v in zip(t.columns, r)) + " |" for r in t.itertuples(index=False)]
    arq.with_suffix(".md").write_text("\n".join(lin + (["", nota] if nota else []) + [""]), encoding="utf-8")
    meta(arq, titulo=titulo, nota=nota or None, colunas={k: colunas[k] for k in t.columns}, **kw)
    logger.info("Tabela: %s (%d linhas)", rel(arq), len(t))
    return arq


# ---------------------------------------------------------------- régua
def telemetria() -> pd.DataFrame:
    """Todas as leituras de 15 min gravadas; horário repetido entre arquivos conta uma vez (vale o arquivo consultado por último)."""
    arqs = sorted(c.RAW.glob(ao.PADRAO_TELEMETRIA))
    acesso = {a: json.loads(a.with_suffix(".json").read_text(encoding="utf-8")).get("data_acesso", "") if a.with_suffix(".json").exists() else "" for a in arqs}
    arqs = sorted(arqs, key=lambda a: (acesso[a], a.name))
    t = pd.concat([pd.read_csv(a).assign(arquivo=a.name) for a in arqs], ignore_index=True)
    t["dh"] = pd.to_datetime(t.data_hora)
    t = t.dropna(subset=["nivel_cm"]).drop_duplicates("dh", keep="last").sort_values("dh").reset_index(drop=True)
    t.attrs["arquivos"] = [rel(a) for a in arqs]
    return t


def series_da_regua() -> dict:
    """Série antiga (bruta; onde falta, a consistida) completada pela média do dia da telemetria (dias com leituras suficientes)."""
    diaria = ao.ler_serie_diaria()
    d = diaria[pd.Timestamp(ARGS.inicio):]
    antiga = d.media_bruta_cm.where(d.media_bruta_cm.notna(), d.media_consistida_cm).dropna()
    tel = telemetria()
    por_dia = tel.groupby(tel.dh.dt.normalize()).nivel_cm.agg(media="mean", leituras="size", maxima="max")
    tel_dia = por_dia.media[por_dia.leituras >= ARGS.leituras_min]
    dias = pd.date_range(ARGS.inicio, max(antiga.index.max(), tel_dia.index.max()), freq="D")
    junta = antiga.reindex(dias).combine_first(tel_dia.reindex(dias))
    origem = pd.Series(np.where(antiga.reindex(dias).notna(), "série antiga", np.where(tel_dia.reindex(dias).notna(), "telemetria (média do dia)", "")), index=dias)
    pico_dia = junta.notna() & (junta == junta.rolling(7, center=True, min_periods=1).max())
    return {"diaria": diaria, "tel": tel, "por_dia": por_dia, "junta": junta, "origem": origem, "pico_dia": pico_dia}


def nivel_na_hora(quando_local: pd.Timestamp, tel: pd.DataFrame) -> tuple[float, str]:
    """Leitura de 15 min mais próxima da hora (até 1 h), como em agua_observada_sentinel.nivel_na_cena."""
    if tel.dh.min() <= quando_local <= tel.dh.max() + pd.Timedelta(hours=1):
        i = (tel.dh - quando_local).abs().idxmin()
        if abs(tel.dh[i] - quando_local) <= pd.Timedelta(hours=1):
            return float(tel.nivel_cm[i]), f"telemetria das {tel.dh[i]:%H:%M}"
    return np.nan, ""


def episodio_em_curso(r: dict) -> dict:
    """O episódio da regra do inventário que contém --episodio-desde (ou o primeiro depois dele)."""
    junta, tel = r["junta"], r["tel"]
    ep = inv.episodios(junta.dropna(), ARGS.limiar_cm, ARGS.intervalo_dias)
    ep = ep[ep.fim >= pd.Timestamp(ARGS.episodio_desde)].sort_values("inicio")
    if ep.empty:
        raise SystemExit(f"nenhum episódio com média diária ≥ {ARGS.limiar_cm:g} cm desde {ARGS.episodio_desde}")
    e = ep.iloc[0]
    trecho = junta[e.inicio:e.fim]
    leituras = tel[(tel.dh >= e.inicio) & (tel.dh < e.fim + pd.Timedelta(days=1))]
    imax = leituras.nivel_cm.idxmax()
    ultima = tel.iloc[-1]
    return {"inicio": e.inicio, "fim": e.fim, "duracao_dias": int(e.duracao_dias), "dias_no_limiar_ou_acima": int(e.dias_no_limiar_ou_acima), "dias_a_850_cm_ou_acima": int((trecho >= ARGS.limiar_alto_cm).sum()),
            "data_do_pico_media_do_dia": trecho.idxmax(), "pico_media_do_dia_cm": float(trecho.max()), "leituras_no_dia_do_pico": int(r["por_dia"].leituras.get(trecho.idxmax(), 0)),
            "maior_leitura_cm": float(leituras.nivel_cm[imax]), "data_hora_da_maior_leitura": leituras.dh[imax], "ultima_leitura_cm": float(ultima.nivel_cm), "data_hora_da_ultima_leitura": ultima.dh,
            "em_aberto": bool(ultima.nivel_cm >= ARGS.limiar_cm or e.fim >= junta.dropna().index.max())}


def maximos_anuais(r: dict) -> pd.DataFrame:
    """Máximo de cada ano pela maior leitura (rodada 41): leituras das 7h e 17h da série e leituras da telemetria; ano sem leitura fica com a maior média diária, marcado."""
    d, tel = r["diaria"], r["tel"]
    leit = pd.concat([d.leitura_07h_cm.dropna(), d.leitura_17h_cm.dropna()])
    out = []
    for ano in range(int(d.index.year.min()), int(max(d.index.year.max(), tel.dh.dt.year.max())) + 1):
        ls = leit[leit.index.year == ano]
        ts = tel[tel.dh.dt.year == ano]
        cand = []
        if len(ls):
            cand.append((float(ls.max()), f"{ls.idxmax():%Y-%m-%d}", "leitura das 7h ou 17h (série)"))
        if len(ts):
            i = ts.nivel_cm.idxmax()
            cand.append((float(ts.nivel_cm[i]), f"{ts.dh[i]:%Y-%m-%d %H:%M}", "leitura de 15 min (telemetria)"))
        if not cand:
            v = d.valor_do_dia_cm[d.index.year == ano].dropna()
            if len(v):
                cand.append((float(v.max()), f"{v.idxmax():%Y-%m-%d}", "sem leitura no ano: maior média diária"))
        if cand:
            m = max(cand, key=lambda x: x[0])
            out.append({"ano": ano, "maximo_cm": m[0], "quando": m[1], "origem": m[2]})
    t = pd.DataFrame(out)
    t["posicao"] = t.maximo_cm.rank(ascending=False, method="min").astype(int)
    return t.sort_values("posicao").reset_index(drop=True)


def etapa_regua() -> None:
    global FONTES
    FONTES = ["ANA — série histórica de cotas e telemetria da estação fluviométrica (serviço público, sem conta)"]
    r = series_da_regua()
    tel = r["tel"]

    # ---- A2: telemetria nova × telemetria já gravada, nos horários em comum
    arqs = sorted(c.RAW.glob(ao.PADRAO_TELEMETRIA))
    nova = c.RAW / ARGS.telemetria_nova
    if not nova.exists():
        raise SystemExit(f"telemetria nova ausente: {ARGS.telemetria_nova}")
    n = pd.read_csv(nova).assign(dh=lambda t: pd.to_datetime(t.data_hora)).dropna(subset=["nivel_cm"])
    a2 = []
    for a in arqs:
        if a == nova:
            continue
        o = pd.read_csv(a).assign(dh=lambda t: pd.to_datetime(t.data_hora)).dropna(subset=["nivel_cm"])
        k = n.merge(o, on="dh", suffixes=("_nova", "_antes"))
        if not len(k):
            continue
        dif = k.nivel_cm_nova - k.nivel_cm_antes
        dn = n.groupby(n.dh.dt.normalize()).nivel_cm.mean()
        do = o.groupby(o.dh.dt.normalize()).nivel_cm.agg(["mean", "size"])
        dd = pd.concat([dn.rename("n"), do.rename(columns={"mean": "o", "size": "q"})], axis=1).dropna()
        dd = dd[dd.q >= ARGS.leituras_min]
        a2.append({"arquivo_ja_gravado": a.name, "primeiro_horario_em_comum": f"{k.dh.min():%Y-%m-%d %H:%M}", "ultimo_horario_em_comum": f"{k.dh.max():%Y-%m-%d %H:%M}", "horarios_em_comum": len(k),
                   "diferenca_media_cm": float(dif.mean()), "diferenca_maxima_em_modulo_cm": float(dif.abs().max()), "horarios_com_diferenca": int((dif != 0).sum()),
                   "horarios_so_no_ja_gravado": int(len(set(o.dh[(o.dh >= n.dh.min()) & (o.dh <= n.dh.max())]) - set(n.dh))),
                   "dias_em_comum": len(dd), "diferenca_media_da_media_do_dia_cm": float((dd.n - dd.o).mean()) if len(dd) else np.nan,
                   "diferenca_maxima_da_media_do_dia_cm": float((dd.n - dd.o).abs().max()) if len(dd) else np.nan})
    a2 = pd.DataFrame(a2)
    gravar(a2, "telemetria-nova-x-ja-gravada_ana_2026_arquivo", "A2 — Telemetria nova × telemetria já gravada, nos horários em comum", {
        "arquivo_ja_gravado": "arquivo de telemetria gravado antes", "primeiro_horario_em_comum": "primeiro horário presente nos dois", "ultimo_horario_em_comum": "último horário presente nos dois",
        "horarios_em_comum": "leituras de 15 min presentes nos dois", "diferenca_media_cm": "média de (nova − já gravada)", "diferenca_maxima_em_modulo_cm": "maior módulo da diferença",
        "horarios_com_diferenca": "horários com valor diferente", "horarios_so_no_ja_gravado": "horários do período da nova que só existem no já gravado",
        "dias_em_comum": f"dias com pelo menos {ARGS.leituras_min} leituras nos dois", "diferenca_media_da_media_do_dia_cm": "média de (média do dia nova − média do dia já gravada)",
        "diferenca_maxima_da_media_do_dia_cm": "maior módulo da diferença das médias do dia"}, casas={"diferenca_media_cm": 2, "diferenca_media_da_media_do_dia_cm": 2},
        fuso_declarado_pelo_servico="não declarado")

    # ---- A3: episódio em curso, tendência e posição do pico entre os máximos anuais
    e = episodio_em_curso(r)
    fim = tel.dh.max()
    ult = tel[tel.dh >= fim - pd.Timedelta(hours=ARGS.horas_tendencia)]
    horas = (ult.dh - ult.dh.min()).dt.total_seconds() / 86400
    inclinacao = float(np.polyfit(horas, ult.nivel_cm, 1)[0]) if len(ult) > 2 else np.nan
    max_anuais = maximos_anuais(r)
    ano = int(e["data_hora_da_maior_leitura"].year)
    pos = max_anuais[max_anuais.ano == ano].iloc[0]
    e.update(tendencia_das_ultimas_horas_cm_por_dia=inclinacao, horas_da_tendencia=ARGS.horas_tendencia, variacao_nas_ultimas_horas_cm=float(ult.nivel_cm.iloc[-1] - ult.nivel_cm.iloc[0]),
             maximo_do_ano_cm=float(pos.maximo_cm), posicao_do_maximo_do_ano=int(pos.posicao), anos_com_maximo=len(max_anuais), maximo_do_ano_e_o_pico_do_episodio=bool(pos.maximo_cm == e["maior_leitura_cm"]))
    a3 = pd.DataFrame([{k: (f"{v:%Y-%m-%d %H:%M}" if isinstance(v, pd.Timestamp) and (v.hour or v.minute) else f"{v:%Y-%m-%d}" if isinstance(v, pd.Timestamp) else v) for k, v in e.items()}])
    a3 = a3.melt(var_name="medida", value_name="valor").assign(valor=lambda t: t.valor.astype(str))
    gravar(a3, "episodio-da-cheia-de-outubro_ana_2026_medida", f"A3 — Episódio da cheia de outubro de 2026 (média diária ≥ {ARGS.limiar_cm:g} cm, junção com até {ARGS.intervalo_dias} dias abaixo)",
           {"medida": "medida do episódio", "valor": "valor (cm, dias, data e hora local do serviço, ou sim/não)"},
           nota=f"Tendência: inclinação da reta ajustada às leituras de 15 min das últimas {ARGS.horas_tendencia} h. Máximos anuais pela maior leitura; ano sem leitura entra com a maior média diária.")
    gravar(max_anuais, "maximos-anuais-maior-leitura_ana_1940-2026_ano", "A3 — Máximos anuais da régua pela maior leitura, em ordem", {
        "ano": "ano", "maximo_cm": "maior leitura do ano (cm)", "quando": "dia (e hora, na telemetria) da maior leitura", "origem": "de onde vem o valor", "posicao": "posição entre os anos (1 = maior; empate pega a mesma posição)"},
        casas={"maximo_cm": 0}, nota="O ano corrente está incompleto.")
    dias = r["junta"][pd.Timestamp(ARGS.figura_desde):].to_frame("media_do_dia_cm").join(r["por_dia"][["leituras", "maxima"]]).join(r["origem"].rename("origem_da_media"))
    dias = dias.reset_index(names="dia").assign(dia=lambda t: t.dia.dt.strftime("%Y-%m-%d"))
    gravar(dias.rename(columns={"maxima": "maior_leitura_cm"}), "regua-diaria_ana_2026-09-2026-10_dia", "A — Régua dia a dia desde o início da figura", {
        "dia": "dia", "media_do_dia_cm": "média diária (série antiga ou média das leituras do dia)", "leituras": "leituras de 15 min no dia", "maior_leitura_cm": "maior leitura de 15 min do dia",
        "origem_da_media": "de onde vem a média"}, casas={"media_do_dia_cm": 1})
    print(json.dumps({"a2": a2.to_dict("records"), "episodio": {k: str(v) for k, v in e.items()}, "maximos_anuais_top": max_anuais.head(12).to_dict("records"), "avisos": AVISOS}, ensure_ascii=False, indent=1, default=str))


# ---------------------------------------------------------------- passagens
def dominio_da_busca():
    urbano = gpd.read_file(PROC44 / "area-urbana_sgb_2023_retangulo.gpkg", layer="area_urbana").to_crs(c.CRS_PADRAO).geometry.iloc[0]
    dominio_proj = box(*urbano.bounds).buffer(ARGS.margem_m, join_style="mitre")
    return dominio_proj, gpd.GeoSeries([dominio_proj], crs=c.CRS_PADRAO).to_crs("EPSG:4326").iloc[0]  # em graus só para a busca


def colunas_das_passagens() -> dict:
    return {"sensor": "sensor e produto", "colecao": "coleção do catálogo", "data_hora_utc": "início da passagem, UTC", "data_hora_local": "o mesmo, em UTC−3", "data_local": "dia local", "plataforma": "satélite",
             "orbita_relativa": "órbita relativa", "direcao": "direção da órbita", "quadricula": "quadrícula do óptico", "itens_no_catalogo": "itens da passagem no catálogo", "itens_que_tocam_o_dominio": "dos quais, com pegada que toca o domínio",
             "cobertura_do_dominio_pct": "% do domínio (área urbana com margem) coberta pela pegada", "nuvem_da_quadricula_pct": "nuvem declarada da quadrícula INTEIRA (óptico)", "identificadores": "identificadores dos itens",
             "busca": "principal (desde o início da busca) ou só para a passagem de rio baixo", "nivel_do_dia_cm": "média do dia da régua", "nivel_na_hora_cm": "leitura de 15 min mais próxima da hora da passagem (até 1 h)",
             "origem_do_nivel_na_hora": "hora da leitura usada", "fase": "subida, descida ou pico (regra da curva nível × área)", "ja_lida_antes": "os arquivos da passagem já estavam em data/raw/sentinel/",
             "arquivos_que_faltam": "arquivos brutos a ler", "motivo": "por que não foi selecionada (ou papel especial)", "selecionada": "entra na lista do que ler", "papel": "por que entrou", "prioridade": "0 = régua ≥ prioridade; 1 = subida; 2 = demais",
             "dentro_do_teto": f"cabe no teto de {ARGS.teto_de_arquivos} arquivos novos, pela ordem de prioridade", "lida": "arquivos presentes depois da leitura"}


def figura_regua(arq: Path, r: dict, pas: pd.DataFrame) -> None:
    tel, junta = r["tel"], r["junta"]
    ini = pd.Timestamp(ARGS.figura_desde)
    t = tel[tel.dh >= ini]
    fig, ax = plt.subplots(figsize=(12, 6.2))
    ax.plot(t.dh, t.nivel_cm, color="#333333", lw=0.9, label="régua, leitura de 15 min")
    d = junta[ini:].dropna()
    ax.plot(d.index + pd.Timedelta(hours=12), d.values, color="#2a78d6", lw=0, marker=".", ms=4, label="média do dia")
    for v, estilo in ((650, ":"), (750, "--"), (850, "-.")):
        ax.axhline(v, color="#999999", lw=0.8, ls=estilo)
        ax.text(t.dh.max() + pd.Timedelta(hours=10), v, f"{v} cm", va="center", fontsize=8, color="#666666")
    for cota, cor in SGB_FIGURA.items():
        ax.axhline(cota, color=cor, lw=1.0)
        rot = f"mancha SGB {cota:,} cm".replace(",", ".") + (" (extraída de figura, não conferida)" if cota == 1053 else "")
        ax.text(ini + pd.Timedelta(hours=6), cota - 6 if cota == 833 else cota + 6, rot, fontsize=8, color=cor, va="top" if cota == 833 else "bottom")
    estilo = {RADAR: ("^", "#d62728", "passagem de radar"), OPTICO: ("s", "#ff9f1c", "passagem óptica")}
    for sensor, (marca, cor, rot) in estilo.items():
        k = pas[(pas.sensor == sensor) & pas.nivel_na_hora_cm.notna()]
        if len(k):
            lida = k.lida.astype(bool)
            ax.scatter(pd.to_datetime(k.data_hora_local[lida]), k.nivel_na_hora_cm[lida], marker=marca, s=46, color=cor, edgecolor="black", lw=0.5, zorder=5, label=f"{rot} lida")
            ax.scatter(pd.to_datetime(k.data_hora_local[~lida]), k.nivel_na_hora_cm[~lida], marker=marca, s=40, facecolor="white", edgecolor=cor, lw=1.1, zorder=5, label=f"{rot} não lida")
    ax.set_ylim(400, 1260)
    ax.set_xlim(ini, t.dh.max() + pd.Timedelta(days=2.2))
    ax.set_ylabel("nível da régua (cm)")
    ax.set_title(f"Régua do rio de {ini:%d/%m/%Y} a {t.dh.max():%d/%m/%Y %H:%M} e passagens Sentinel", fontsize=11, loc="left")
    ax.grid(axis="y", color="#eeeeee")
    ax.legend(loc="upper left", fontsize=8, frameon=True, ncol=2, bbox_to_anchor=(0.0, 0.93))
    fig.text(0.01, 0.01, "Fonte: ANA, telemetria da estação fluviométrica (hora como o serviço devolve; fuso não declarado). Passagens: catálogo do Planetary Computer, régua na leitura mais próxima da hora.", fontsize=7, color="#555555")
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    fig.savefig(arq, dpi=200)
    plt.close(fig)


def etapa_passagens() -> None:
    global FONTES
    FONTES = ["ANA — telemetria da estação fluviométrica (serviço público, sem conta)", "Microsoft Planetary Computer — catálogo STAC (só metadados)"]
    r = series_da_regua()
    junta, tel, pico_dia = r["junta"], r["tel"], r["pico_dia"]
    e = episodio_em_curso(r)
    inv.ARGS = argparse.Namespace(tentativas=ARGS.tentativas)
    dominio_proj, dominio = dominio_da_busca()
    hoje = pd.Timestamp(datetime.now().date())
    linhas = []
    for sensor in (RADAR, OPTICO):
        col = inv.spc.SENSORES[sensor]["colecao"]
        achadas = inv.passagens(inv.buscar(col, pd.Timestamp(ARGS.busca_desde), hoje + pd.Timedelta(days=1), dominio.bounds), sensor, col, dominio, dominio_proj)
        linhas += [{**x, "busca": "principal"} for x in achadas if x["data_local"] >= pd.Timestamp(ARGS.busca_desde)]
    # passagem de rio baixo antes do episódio: busca à parte, só radar, antes do início da busca principal
    col = inv.spc.SENSORES[RADAR]["colecao"]
    achadas = inv.passagens(inv.buscar(col, pd.Timestamp(ARGS.rio_baixo_desde), pd.Timestamp(ARGS.busca_desde), dominio.bounds), RADAR, col, dominio, dominio_proj)
    linhas += [{**x, "busca": "rio baixo antes do episódio"} for x in achadas if pd.Timestamp(ARGS.rio_baixo_desde) <= x["data_local"] < pd.Timestamp(ARGS.busca_desde)]
    pas = pd.DataFrame(linhas).drop(columns="geometry").sort_values(["data_hora_utc", "sensor"]).reset_index(drop=True)
    brutos = cn.brutos_por_identificador()
    precisa = {RADAR: {"vv", "vh"}, OPTICO: {a.lower() for a in ARGS.ativos_optico}}
    pas["nivel_do_dia_cm"] = [junta.get(x, np.nan) for x in pas.data_local]
    hora = [nivel_na_hora(pd.Timestamp(x), tel) for x in pas.data_hora_local]
    pas["nivel_na_hora_cm"], pas["origem_do_nivel_na_hora"] = [h[0] for h in hora], [h[1] for h in hora]
    pas["fase"] = [cna.fase_da_cheia(x, junta, pico_dia)["fase"] for x in pas.data_local]
    pas["ja_lida_antes"] = [(s, i) in brutos and precisa[s] <= set(brutos[(s, i)]["arquivos"]) for s, i in zip(pas.sensor, pas.identificadores)]
    pas["arquivos_que_faltam"] = [0 if j else len(precisa[s]) for s, j in zip(pas.sensor, pas.ja_lida_antes)]
    com_ref = pas.orbita_relativa.isin(ARGS.orbitas_com_referencia)
    principal = pas.busca == "principal"
    pas["motivo"] = np.select([(pas.sensor == RADAR) & ~com_ref, (pas.cobertura_do_dominio_pct < ARGS.cobertura_min),
                               (pas.sensor == OPTICO) & (pas.nuvem_da_quadricula_pct > ARGS.nuvem_max), ~principal],
                              ["órbita sem referência de rio baixo: não baixada", f"pegada cobre menos de {ARGS.cobertura_min:g} % do domínio", f"nuvem declarada acima de {ARGS.nuvem_max:g} %", "fora da busca principal"], "")
    pas["selecionada"] = pas.motivo == ""
    for orb in ARGS.orbitas_com_referencia:  # uma passagem de rio baixo antes do episódio, em cada órbita: a mais recente abaixo do limiar
        k = pas[(pas.sensor == RADAR) & (pas.orbita_relativa == orb) & (pas.data_local < e["inicio"]) & (pas.nivel_do_dia_cm < ARGS.rio_baixo_cm) & (pas.cobertura_do_dominio_pct >= ARGS.cobertura_min)]
        if len(k):
            i = k.data_hora_utc.idxmax()
            pas.loc[i, ["selecionada", "motivo"]] = [True, f"rio baixo antes do episódio (régua abaixo de {ARGS.rio_baixo_cm:g} cm)"]
        else:
            AVISOS.append(f"órbita {orb}: nenhuma passagem com régua abaixo de {ARGS.rio_baixo_cm:g} cm antes do episódio, de {ARGS.rio_baixo_desde} em diante")
    pas["papel"] = np.where(pas.motivo.str.startswith("rio baixo"), "rio baixo antes do episódio", np.where(pas.selecionada, "cheia de outubro de 2026", ""))
    pas["prioridade"] = np.where(~pas.selecionada, np.nan, np.where(pas.nivel_do_dia_cm >= ARGS.prioridade_cm, 0, np.where(pas.fase == "subida", 1, 2)))
    sel = pas[pas.selecionada].sort_values(["prioridade", "data_hora_utc"])
    pas["dentro_do_teto"] = False
    pas.loc[sel.index, "dentro_do_teto"] = sel.arquivos_que_faltam.cumsum() <= ARGS.teto_de_arquivos
    pas["lida"] = pas.ja_lida_antes  # atualizado depois da leitura, na etapa da água
    pas["data_local"] = pas.data_local.dt.strftime("%Y-%m-%d")
    mais_recente = pas[principal].sort_values("data_hora_utc").iloc[-1] if principal.any() else None
    col_p = colunas_das_passagens()
    gravar(pas, ARQ_PASSAGENS, "B1 — Passagens Sentinel no catálogo sobre a área urbana", col_p, casas={"nivel_do_dia_cm": 1, "nivel_na_hora_cm": 0, "cobertura_do_dominio_pct": 1},
           data_da_consulta=datetime.now(timezone.utc).isoformat(timespec="seconds"), passagem_mais_recente=None if mais_recente is None else mais_recente.data_hora_local,
           dias_de_atraso=None if mais_recente is None else int((hoje - pd.Timestamp(mais_recente.data_local)).days))
    lista = pas[pas.selecionada & pas.dentro_do_teto].copy()
    gravar(lista, ARQ_LISTA, "B2 — Passagens a ler", col_p, casas={"nivel_do_dia_cm": 1, "nivel_na_hora_cm": 0, "cobertura_do_dominio_pct": 1}, total_de_arquivos_novos=int(lista.arquivos_que_faltam.sum()))
    if ARGS.figuras:
        ARGS.figuras.mkdir(parents=True, exist_ok=True)
        figura_regua(ARGS.figuras / "regua-e-passagens_2026-09-2026-10.png", r, pas)
    faltam = lista[lista.arquivos_que_faltam > 0]
    print(json.dumps({"passagens": len(pas), "por_sensor": pas.groupby(["busca", "sensor", "selecionada"]).size().rename("n").reset_index().to_dict("records"),
                      "mais_recente": None if mais_recente is None else mais_recente.data_hora_local, "a_ler": {s: faltam[faltam.sensor == s].identificadores.tolist() for s in (RADAR, OPTICO)},
                      "arquivos_novos": int(faltam.arquivos_que_faltam.sum()), "fora_do_teto": pas[pas.selecionada & ~pas.dentro_do_teto][["sensor", "data_hora_local"]].to_dict("records"), "avisos": AVISOS}, ensure_ascii=False, indent=1, default=str))


# ---------------------------------------------------------------- água
def figura_nivel_area(arq: Path, todas: pd.DataFrame, curvas: dict, sgb: pd.DataFrame, extraidas: dict) -> None:
    fig, eixos = plt.subplots(1, 2, figsize=(14, 6.4), sharey=False)
    for ax, d in zip(eixos, ("A1", "A2")):
        col = f"{d.lower()}_km2"
        for conj, cor, rot in (("antiga", "#9a9a9a", "cenas boas já existentes"), ("nova", "#d62728", "cenas boas da cheia de outubro de 2026")):
            for sensor, marca in ((RADAR, "o"), (OPTICO, "s")):
                k = todas[(todas.conjunto == conj) & (todas.sensor == sensor)]
                ax.scatter(k.nivel_regua_cm, k[col], marker=marca, s=26 if conj == "antiga" else 46, facecolor=cor if conj == "nova" else "none", edgecolor=cor if conj == "antiga" else "black", lw=0.8 if conj == "antiga" else 0.6, zorder=3 if conj == "nova" else 2)
        xs, ys = curvas[d]["antes"]
        ax.plot(xs, ys, color="#555555", lw=1.0, ls="--", drawstyle="default", label="curva crescente antes (os dois sensores)")
        xs, ys = curvas[d]["depois"]
        ax.plot(xs, ys, color="#111111", lw=1.4, label="curva crescente depois (os dois sensores)")
        for s in sgb.itertuples():
            cor = SGB_FIGURA.get(int(s.cota_cm), "#bbbbbb")
            ax.axhline(s.mancha_em_terra_km2, color=cor, lw=0.8, ls=":")
            ax.text(430, s.mancha_em_terra_km2 + 0.12, f"mancha {int(s.cota_cm):,} cm".replace(",", ".") + (" *" if int(s.cota_cm) in extraidas else ""), fontsize=7.5, color=cor, ha="left")
        ax.set_xlim(420, 1270)
        ax.set_ylim(0, 18.5)
        ax.set_xlabel("nível da régua no dia (cm)")
        ax.set_ylabel("km² na área urbana, lado brasileiro")
        ax.set_title(f"{d}: {cna.DEFINICOES[d]}", fontsize=10, loc="left")
        ax.grid(color="#eeeeee")
    leg = [Line2D([], [], marker="o", ls="", mfc="none", mec="#9a9a9a", label="radar, já existentes"), Line2D([], [], marker="s", ls="", mfc="none", mec="#9a9a9a", label="óptico, já existentes"),
           Line2D([], [], marker="o", ls="", mfc="#d62728", mec="black", label="radar, cheia de outubro de 2026"), Line2D([], [], marker="s", ls="", mfc="#d62728", mec="black", label="óptico, cheia de outubro de 2026"),
           Line2D([], [], color="#555555", ls="--", label="curva crescente antes"), Line2D([], [], color="#111111", label="curva crescente depois")]
    fig.legend(handles=leg, loc="lower center", ncol=6, fontsize=8, frameon=False, bbox_to_anchor=(0.5, 0.035))
    fig.suptitle("Nível da régua × área de água em terra vista por satélite", fontsize=11, x=0.01, ha="left")
    fig.text(0.01, 0.005, "* mancha extraída de figura, não conferida. Linhas pontilhadas: área em terra de cada mancha do SGB no mesmo domínio. Produto derivado, para conferência.", fontsize=7, color="#555555")
    fig.tight_layout(rect=(0, 0.09, 1, 0.96))
    fig.savefig(arq, dpi=200)
    plt.close(fig)


def figura_cena(arq: Path, fundo, tipo: str, terra, leito_geom, contornos: list, urbano, ext, titulo: str) -> None:
    fig, ax = plt.subplots(figsize=(12, 9.6))
    if tipo == "rgb":
        ax.imshow(np.dstack(fundo), extent=ext, interpolation="nearest")
    else:
        ax.imshow(fundo, cmap="gray", vmin=-25, vmax=0, extent=ext, interpolation="nearest")
    cam = np.zeros(terra.shape + (4,), dtype="float32")
    cam[terra] = (0.10, 0.45, 0.95, 0.55)
    ax.imshow(cam, extent=ext, interpolation="nearest")
    gpd.GeoSeries([leito_geom], crs=c.CRS_PADRAO).boundary.plot(ax=ax, color="#ffffff", linewidth=0.5, linestyle=(0, (3, 2)))
    for geom, cor, estilo, _ in contornos:
        gpd.GeoSeries([geom], crs=c.CRS_PADRAO).boundary.plot(ax=ax, color=cor, linewidth=1.1, linestyle=estilo)
    gpd.GeoSeries([urbano], crs=c.CRS_PADRAO).boundary.plot(ax=ax, color="#ffd400", linewidth=0.8, linestyle=(0, (6, 3)))
    ax.set_xlim(ext[0], ext[1]); ax.set_ylim(ext[2], ext[3])  # noqa: E702
    ax.set_xticks([]); ax.set_yticks([])  # noqa: E702
    ax.set_title(titulo, fontsize=10, loc="left")
    leg = [Patch(color=(0.10, 0.45, 0.95, 0.55), label="água em terra da cena (A1, ligada ao rio)")] + [Line2D([], [], color=cor, lw=1.6, ls=estilo, label=rot) for _, cor, estilo, rot in contornos]
    leg += [Line2D([], [], color="#ffffff", lw=1.0, ls=(0, (3, 2)), label="leito de referência"), Line2D([], [], color="#ffd400", lw=1.0, ls=(0, (6, 3)), label="área urbana (retângulo)")]
    ax.legend(handles=leg, loc="lower right", fontsize=8.5, frameon=True, facecolor="#5a5a5a", edgecolor="none", labelcolor="white")
    fig.text(0.01, 0.01, "Produto derivado, para conferência. Contém dados Copernicus Sentinel modificados.", fontsize=7, color="#555555")
    fig.tight_layout(rect=(0, 0.02, 1, 1))
    fig.savefig(arq, dpi=200)
    plt.close(fig)


def etapa_agua() -> None:
    global FONTES
    m44 = json.loads((PROC44 / "controle-de-qualidade_sentinel_2017-2024_cena.json").read_text(encoding="utf-8"))
    arg44 = argparse.Namespace(**m44["argumentos"])
    au.ARGS = arg44  # as funções do roteiro da área urbana leem os limiares de lá: os mesmos da classificação das cenas antigas
    FONTES = [*m44["fontes"], "ANA — telemetria da estação fluviométrica (nível das cenas)"]
    pas = pd.read_csv(PROC / f"{ARQ_PASSAGENS}.csv")
    lista = pd.read_csv(PROC / f"{ARQ_LISTA}.csv")
    brutos = cn.brutos_por_identificador()
    reg = series_da_regua()
    junta, origem, tel, pico_dia = reg["junta"], reg["origem"], reg["tel"], reg["pico_dia"]
    e = episodio_em_curso(reg)
    precisa = {RADAR: {"vv", "vh"}, OPTICO: {a.lower() for a in ARGS.ativos_optico}}
    pas["lida"] = [(s, i) in brutos and precisa[s] <= set(brutos[(s, i)]["arquivos"]) for s, i in zip(pas.sensor, pas.identificadores)]
    m_pas = json.loads((PROC / f"{ARQ_PASSAGENS}.json").read_text(encoding="utf-8"))
    gravar(pas, ARQ_PASSAGENS, "B1 — Passagens Sentinel no catálogo sobre a área urbana", colunas_das_passagens(), casas={"nivel_do_dia_cm": 1, "nivel_na_hora_cm": 0, "cobertura_do_dominio_pct": 1},
           **{k: m_pas.get(k) for k in ("data_da_consulta", "passagem_mais_recente", "dias_de_atraso")})
    if ARGS.figuras:
        ARGS.figuras.mkdir(parents=True, exist_ok=True)
        figura_regua(ARGS.figuras / "regua-e-passagens_2026-09-2026-10.png", reg, pas)

    # ---- mesma grade, mesmo domínio, mesmo leito e mesmas referências do roteiro da área urbana
    urbano = gpd.read_file(PROC44 / "area-urbana_sgb_2023_retangulo.gpkg", layer="area_urbana").to_crs(c.CRS_PADRAO).geometry.iloc[0]
    x0, y0, x1, y1 = urbano.bounds
    gx0, gy1 = np.floor(x0 / RES) * RES, np.ceil(y1 / RES) * RES
    grade = {"transform": from_origin(gx0, gy1, RES, RES), "width": int(np.ceil((x1 - gx0) / RES)), "height": int(np.ceil((gy1 - y0) / RES))}
    forma, tr = (grade["height"], grade["width"]), grade["transform"]
    ext = (gx0, gx0 + grade["width"] * RES, gy1 - grade["height"] * RES, gy1)
    px_km2 = RES * RES / 1e6
    dentro = cna.rasterizar(urbano, forma, tr)
    dom = dentro & cna.rasterizar(c.carregar_area_estudo().to_crs(c.CRS_PADRAO).union_all(), forma, tr)
    g_ref = PROC44 / "referencia-por-orbita_sentinel1-rtc_2017-2024_10m.gpkg"
    leito_geom = gpd.read_file(g_ref, layer="leito_de_referencia_comum").geometry.union_all()
    leito = cna.rasterizar(leito_geom, forma, tr)
    minimo_px = int(np.ceil(arg44.area_minima_ha * 1e4 / (RES * RES)))
    tab_ref = pd.read_csv(PROC44 / "referencia-por-orbita_sentinel1-rtc_2017-2024_orbita.csv")
    refs = {}
    for r in tab_ref[tab_ref.abaixo_do_nivel_maximo].itertuples():  # só as referências de rio baixo
        db, valido = au.vv_em_db(brutos[(RADAR, r.cena_de_referencia)]["arquivos"]["vv"], grade)
        agua = ao.tirar_grupos_pequenos(valido & (db <= arg44.vv_agua_referencia), minimo_px)
        refs[int(r.orbita_relativa)] = {"db": db, "valido": valido, "agua": agua, "rio": au.maior_grupo(agua) & dentro, "cena": r.cena_de_referencia, "data": r.data_hora_local[:10]}

    def classificar(sensor: str, ident: str) -> dict:
        """As três camadas de água de uma cena, com as regras e o controle de qualidade do roteiro da área urbana (como na rodada das cheias novas)."""
        b = brutos[(sensor, ident)]
        out = {"orbita": b["meta"].get("orbita_relativa"), "fracao_do_leito": np.nan, "nuvem_ou_sombra_pct": np.nan, "boa": False, "motivo": ""}
        if sensor == RADAR:
            db, valido = au.vv_em_db(b["arquivos"]["vv"], grade)
            out["cobertura_pct"] = float(100 * (valido & dentro).sum() / dentro.sum())
            ref = refs.get(int(out["orbita"])) if out["orbita"] is not None else None
            if ref is None:
                return {**out, "motivo": "órbita sem referência de rio baixo"}
            agua = au.agua_por_mudanca(db, valido, ref, arg44.mudanca, minimo_px)
            valido = valido & ref["valido"]
            fr = float((agua & ref["rio"]).sum() / ref["rio"].sum())
            out.update(fracao_do_leito=fr, boa=bool(fr >= arg44.fracao_minima_do_rio), motivo="" if fr >= arg44.fracao_minima_do_rio else "rio sem contraste", db=db, ref=ref)
        else:
            versao = b["meta"].get("versao_do_processamento")
            arqs = b["arquivos"]
            agua, valido, _, _ = ao.agua_optico(arqs, grade, float(versao or 0) >= 4)
            scl = ao.para_a_grade(arqs["scl"], grade, Resampling.nearest, 0)
            nuvem = float(100 * (np.isin(scl, au.SCL_NUVEM_OU_SOMBRA) & dentro).sum() / dentro.sum())
            agua = ao.tirar_grupos_pequenos(agua, minimo_px)
            out.update(cobertura_pct=float(100 * ((scl != 0) & dentro).sum() / dentro.sum()), nuvem_ou_sombra_pct=nuvem, boa=bool(nuvem <= arg44.nuvem_max), motivo="" if nuvem <= arg44.nuvem_max else f"nuvem ou sombra acima de {arg44.nuvem_max:g} %",
                       versao=versao or "")
        if out["cobertura_pct"] < ao.COBERTURA_MINIMA:
            out.update(boa=False, motivo="área urbana sem cobertura completa")
        ligada = ao.ligada_ao(agua, leito)
        return {**out, "agua_total": agua, "agua_ligada_ao_rio": ligada, "inundacao_em_terra": ligada & ~leito, "dominio": dom & valido}

    def areas(k: dict) -> dict:
        x = {f"{cam}_km2": float((k[cam] & k["dominio"]).sum() * px_km2) for cam in ao.CAMADAS}
        x["agua_total_em_terra_km2"] = float((k["agua_total"] & ~leito & k["dominio"]).sum() * px_km2)
        return x

    # ---- conferência do método: cenas da rodada das cheias novas reclassificadas aqui dão as mesmas áreas
    c49 = pd.read_csv(PROC49 / f"{ARQ_C2_49}.csv", dtype={"versao_do_processamento": str})
    conf = []
    for r in c49[c49.boa & c49.episodio.isin(ARGS.conferir_episodios)].itertuples():
        if (r.sensor, r.identificador) in brutos:
            k = classificar(r.sensor, r.identificador)
            a = areas(k)
            conf.append({"sensor": r.sensor, "data_hora_local": r.data_hora_local, **{f"{x}_aqui": a[x] for x in ("agua_total_km2", "inundacao_em_terra_km2")},
                         **{f"{x}_gravado": getattr(r, x) for x in ("agua_total_km2", "inundacao_em_terra_km2")}, "maior_diferenca_km2": max(abs(a[x] - getattr(r, x)) for x in ("agua_total_km2", "inundacao_em_terra_km2", "agua_ligada_ao_rio_km2"))})
    if any(x["maior_diferenca_km2"] > 1e-9 for x in conf):
        AVISOS.append("a reclassificação das cenas de conferência não reproduz as áreas gravadas")

    # ---- C1 e C2: cada passagem da lista
    c2, cenas, geo = [], {}, {RADAR: {k: [] for k in ao.CAMADAS}, OPTICO: {k: [] for k in ao.CAMADAS}}
    ja_classificadas = set(c49.identificador)  # passagens que a rodada das cheias novas já classificou: entram na tabela, não entre as cenas novas
    for r in lista.itertuples():
        linha = {"episodio": "E28" if r.papel != "rio baixo antes do episódio" else "", "papel": r.papel, "sensor": r.sensor, "identificador": r.identificadores, "data_hora_utc": r.data_hora_utc,
                 "data_hora_local": r.data_hora_local, "orbita_relativa": r.orbita_relativa, "plataforma": r.plataforma, "ja_classificada_antes": r.identificadores in ja_classificadas}
        if (r.sensor, r.identificadores) not in brutos or not ({"vv"} if r.sensor == RADAR else {"b03", "b11", "b08", "scl"}) <= set(brutos[(r.sensor, r.identificadores)]["arquivos"]):
            c2.append({**linha, "boa": False, "motivo": "não lida (falha na leitura)"})
            continue
        k = classificar(r.sensor, r.identificadores)
        local = pd.Timestamp(r.data_hora_local)
        dia = local.normalize()
        perto, de_onde = nivel_na_hora(local, tel)
        f = cna.fase_da_cheia(dia, junta, pico_dia)
        regua = junta.get(dia, np.nan)
        no_patamar = bool(e["inicio"] <= dia <= pd.Timestamp(e["data_hora_da_ultima_leitura"]).normalize() and regua >= e["pico_media_do_dia_cm"] - ARGS.patamar_cm)
        fase4 = "pico" if f["fase"] == "pico" else "patamar" if no_patamar else f["fase"]
        linha.update(regua_cm=regua, origem_do_nivel=origem.get(dia, ""), regua_cm_hora=perto, origem_do_nivel_da_hora=de_onde, fase_na_cheia=fase4, **{"fase": f["fase"]},
                     variacao_em_dois_dias_cm=f["variacao_em_dois_dias_cm"], cm_abaixo_do_pico=e["pico_media_do_dia_cm"] - regua, cobertura_da_area_urbana_pct=k["cobertura_pct"],
                     fracao_do_leito_principal_marcada_como_agua=k["fracao_do_leito"], nuvem_ou_sombra_pct=k["nuvem_ou_sombra_pct"], boa=k["boa"], motivo=k["motivo"])
        if "agua_total" in k:
            linha.update(areas(k))
        b = brutos[(r.sensor, r.identificadores)]
        linha.update(direcao=b["meta"].get("direcao", ""), versao_do_processamento=k.get("versao", ""), arquivos="; ".join(a.name for a in b["arquivos"].values()))
        c2.append(linha)
        if not k["boa"] or linha["ja_classificada_antes"]:
            continue
        cenas[r.sensor + "_" + local.strftime("%Y%m%dT%H%M")] = {**k, "linha": linha}
        for cam in ao.CAMADAS:
            geo[r.sensor][cam].append({"identificador": r.identificadores, "data": f"{local:%Y-%m-%d}", "hora_local": f"{local:%H:%M}", "nivel_regua_cm": regua, "orbita_relativa": r.orbita_relativa if r.sensor == RADAR else None,
                                       "limiar": f"mudança <= {arg44.mudanca:g} dB e VV <= {arg44.vv_max:g} dB, ou água na referência e VV <= {arg44.vv_max:g} dB" if r.sensor == RADAR else "MNDWI > 0",
                                       "controle_de_qualidade": f"leito principal marcado como água: {100 * k['fracao_do_leito']:.1f} %" if r.sensor == RADAR else f"nuvem ou sombra: {k['nuvem_ou_sombra_pct']:.1f} %",
                                       "area_km2": float((k[cam] & k["dominio"]).sum() * px_km2), "geometry": ao.poligonos(k[cam] & dentro, grade)})
    c2 = pd.DataFrame(c2).sort_values("data_hora_utc").reset_index(drop=True)
    for col in ("regua_cm", "regua_cm_hora", "agua_total_km2", "agua_ligada_ao_rio_km2", "inundacao_em_terra_km2", "agua_total_em_terra_km2"):
        c2[col] = pd.to_numeric(c2.get(col), errors="coerce")
    col_c2 = {"episodio": "episódio de cheia (E28 = cheia de outubro de 2026; vazio = rio baixo antes dele)", "papel": "por que a passagem foi lida", "sensor": "sensor e produto",
              "ja_classificada_antes": "a passagem já foi classificada na rodada das cheias novas (fica fora das cenas novas, da curva e das imagens)",
              "identificador": "identificador da cena (das fatias, separados por ponto e vírgula)", "data_hora_utc": "data e hora UTC", "data_hora_local": "data e hora local (UTC−3)", "orbita_relativa": "órbita relativa",
              "plataforma": "satélite", "regua_cm": "nível da régua no dia: média diária", "origem_do_nivel": "de onde vem a média", "regua_cm_hora": "leitura de 15 min mais próxima da hora da cena",
              "origem_do_nivel_da_hora": "hora da leitura usada", "fase_na_cheia": f"subida, pico, patamar (régua do dia a até {ARGS.patamar_cm:g} cm do pico do episódio) ou descida",
              "fase": "subida, descida ou pico pela regra da curva nível × área (a usada no índice das imagens)", "variacao_em_dois_dias_cm": "nível do dia menos o de dois dias antes",
              "cm_abaixo_do_pico": "pico do episódio (média do dia) menos a régua do dia", "cobertura_da_area_urbana_pct": "% da área urbana com dado",
              "fracao_do_leito_principal_marcada_como_agua": "radar: fração do leito principal da referência marcada como água", "nuvem_ou_sombra_pct": "óptico: % da área urbana com nuvem ou sombra (SCL)",
              "boa": "passa no controle de qualidade", "motivo": "por que não passa", "agua_total_km2": "água da cena na área urbana, lado brasileiro", "agua_ligada_ao_rio_km2": "água em grupos que tocam o leito de referência",
              "inundacao_em_terra_km2": "água ligada ao rio fora do leito de referência (A1)", "agua_total_em_terra_km2": "toda a água fora do leito de referência (A2)", "direcao": "direção da órbita",
              "versao_do_processamento": "óptico: versão do processamento", "arquivos": "arquivos brutos da cena"}
    gravar(c2, ARQ_C2, "C2 — Cenas da cheia de outubro de 2026: controle de qualidade e áreas", col_c2, casas={"regua_cm": 1, "regua_cm_hora": 0, "fracao_do_leito_principal_marcada_como_agua": 3, "cm_abaixo_do_pico": 1},
           regras={k: getattr(arg44, k) for k in ("mudanca", "vv_max", "vv_agua_referencia", "fracao_minima_do_rio", "nuvem_max", "area_minima_ha", "mediana")}, referencias={o: v["data"] for o, v in refs.items()},
           conferencia_do_metodo=conf)
    col_geo = {"identificador": "cena", "data": "data local", "hora_local": "hora local (UTC−3)", "nivel_regua_cm": "nível da régua no dia", "orbita_relativa": "órbita relativa (radar)", "limiar": "regra da água",
               "controle_de_qualidade": "resultado do controle de qualidade", "area_km2": "área da camada na área urbana, lado brasileiro"}
    for sensor, cams in geo.items():
        if cams[ao.CAMADAS[0]]:
            arq = PROC / f"agua-observada-cenas-da-cheia_{sensor}_2026-09-2026-10_10m.gpkg"
            for cam, linhas in cams.items():
                g = gpd.GeoDataFrame(linhas, crs=c.CRS_PADRAO)
                g[g.geometry.notna()].to_file(arq, driver="GPKG", layer=cam)
            meta(arq, descricao="água de cada cena boa da cheia de outubro de 2026 na área urbana: três camadas, uma linha por cena; polígonos de pixel, sem simplificação", camadas=list(ao.CAMADAS), cenas=len(cams[ao.CAMADAS[0]]), colunas=col_geo)

    # ---- C3: cenas novas boas × manchas acumuladas
    manchas = ec.carregar_cenarios(ee.ARQ_COTAS, ao.CAMADA_COTAS, ao.ATRIBUTO_COTAS)
    geo_m, extraidas = {int(k): g for k, g in zip(manchas.valor, manchas.geometry)}, {}
    for item in ARGS.mancha_extra or []:  # camada de fora do repositório, extraída de figura: só leitura, só comparação
        cota, resto = item.split("=", 1)
        caminho, _, camada = resto.rpartition(":") if resto.count(":") and not resto.endswith(".gpkg") else (resto, "", None)
        if not Path(caminho).exists():
            AVISOS.append(f"mancha de {cota} cm ausente: comparação feita sem ela")
            continue
        geo_m[int(cota)] = gpd.read_file(caminho, layer=camada).to_crs(c.CRS_PADRAO).geometry.buffer(0).union_all()
        extraidas[int(cota)] = f"{int(cota):,} cm: extraída de figura, não conferida".replace(",", ".")
    cotas = sorted(geo_m)
    mancha = {k: cna.rasterizar(geo_m[k], forma, tr) & ~leito for k in cotas}
    nota_extra = ("; ".join(extraidas.values()) + ".") if extraidas else ""
    e28 = {k: v for k, v in cenas.items() if v["linha"]["episodio"] == "E28"}
    mais_alta = max(e28, key=lambda k: (e28[k]["linha"]["regua_cm"], e28[k]["linha"]["regua_cm_hora"])) if e28 else None
    c3 = []
    for chave, k in sorted(cenas.items(), key=lambda x: (x[1]["linha"]["regua_cm"], x[1]["linha"]["data_hora_local"])):
        for cota in cotas:
            c3.append({"episodio": k["linha"]["episodio"], "sensor": k["linha"]["sensor"], "data_hora_local": k["linha"]["data_hora_local"], "nivel_regua_cm": k["linha"]["regua_cm"], "cota_cm": cota,
                       **au.comparar(k["inundacao_em_terra"], mancha[cota], k["dominio"], px_km2), "cena_boa_de_regua_mais_alta": chave == mais_alta, "marca_da_mancha": extraidas.get(cota, "")})
    c3 = pd.DataFrame(c3)
    if len(c3):
        gravar(c3, "agua-x-manchas-cenas-da-cheia_sentinel-sgb_2026-09-2026-10_cena-cota", "C3 — Inundação em terra de cada cena boa nova × cada mancha acumulada, na área urbana", {
            "episodio": col_c2["episodio"], "sensor": "sensor e produto", "data_hora_local": "data e hora local", "nivel_regua_cm": "nível da régua no dia", "cota_cm": "cota da mancha acumulada",
            "inundacao_em_terra_km2": "inundação em terra da cena (onde a cena tem dado)", "mancha_em_terra_km2": "mancha fora do leito de referência (mesmo domínio)", "intersecao_km2": "área comum", "uniao_km2": "área de uma ou de outra",
            "intersecao_sobre_uniao": "interseção / união", "fracao_da_agua_dentro_da_mancha": "quanto da inundação da cena cai dentro da mancha", "fracao_da_mancha_coberta_pela_agua": "quanto da mancha a cena cobre",
            "cena_boa_de_regua_mais_alta": "a cena boa do E28 de régua mais alta", "marca_da_mancha": "ressalva sobre a mancha"},
            casas={"nivel_regua_cm": 1, "intersecao_sobre_uniao": 3, "fracao_da_agua_dentro_da_mancha": 3, "fracao_da_mancha_coberta_pela_agua": 3}, nota=nota_extra)

    # ---- C4: curva nível × área com as cenas boas já existentes (rodadas da curva e das cheias novas) e com elas mais as novas
    antigas = pd.read_csv(next(iter(sorted(PROC45.glob("fase-da-cheia-por-cena_*_cena.csv")))))[["sensor", "data_hora_local", "nivel_regua_cm", "a1_km2", "a2_km2"]]
    b49 = c49[c49.boa]
    antigas = pd.concat([antigas, pd.DataFrame({"sensor": b49.sensor, "data_hora_local": b49.data_hora_local, "nivel_regua_cm": b49.regua_cm, "a1_km2": b49.inundacao_em_terra_km2, "a2_km2": b49.agua_total_em_terra_km2})],
                        ignore_index=True).assign(conjunto="antiga")
    boas = c2[c2.boa & ~c2.ja_classificada_antes]
    novas = pd.DataFrame({"sensor": boas.sensor, "data_hora_local": boas.data_hora_local, "nivel_regua_cm": boas.regua_cm, "a1_km2": boas.inundacao_em_terra_km2, "a2_km2": boas.agua_total_em_terra_km2, "conjunto": "nova"})
    todas = pd.concat([antigas, novas], ignore_index=True)
    sgb = pd.DataFrame([{"cota_cm": k, "mancha_em_terra_km2": float((mancha[k] & dom).sum() * px_km2), "marca": extraidas.get(k, "")} for k in cotas])
    antes49 = pd.read_csv(PROC49 / "nivel-equivalente-das-manchas-antes-e-depois_sentinel-sgb_2016-2026_cota.csv")
    pontos, equiv, faixas, curvas_fig = [], [], [], {}
    for nome, sens in cna.SENSORES.items():
        for d in cna.DEFINICOES:
            col = f"{d.lower()}_km2"
            curvas = {}
            for rot, k in (("antes", antigas[antigas.sensor.isin(sens)]), ("depois", todas[todas.sensor.isin(sens)])):
                xs, ys, n = cna.isotonica(k.nivel_regua_cm.to_numpy(), k[col].to_numpy())
                curvas[rot] = (xs, ys, k)
                pontos += [{"sensor": nome, "definicao": d, "conjunto": rot, "nivel_regua_cm": x, "cenas_no_nivel": int(q), "area_ajustada_km2": float(y)} for x, y, q in zip(xs, ys, n)]
            if nome == "os dois":
                curvas_fig[d] = {rot: (v[0], v[1]) for rot, v in curvas.items()}
            for r in sgb.itertuples():
                linha = {"cota_cm": r.cota_cm, "mancha_em_terra_km2": r.mancha_em_terra_km2, "sensor": nome, "definicao": d}
                for rot, (xs, ys, k) in curvas.items():
                    nivel, situacao = cna.nivel_em_que_atinge(xs, ys, r.mancha_em_terra_km2)
                    linha.update({f"cenas_{rot}": len(k), f"nivel_equivalente_{rot}_cm": nivel, f"situacao_{rot}": situacao, f"maior_valor_da_curva_{rot}_km2": float(ys[-1])})
                gravado = antes49[(antes49.cota_cm == r.cota_cm) & (antes49.sensor == nome) & (antes49.definicao == d)].nivel_equivalente_depois_cm
                linha["nivel_equivalente_gravado_antes_cm"] = float(gravado.iloc[0]) if len(gravado) else np.nan
                linha["mudanca_cm"] = linha["nivel_equivalente_depois_cm"] - linha["nivel_equivalente_antes_cm"]
                linha["marca"] = r.marca
                equiv.append(linha)
            for lo in np.arange(ARGS.faixa_inicio, ARGS.faixa_fim, ARGS.faixa_cm):
                meio = lo + ARGS.faixa_cm / 2
                val = {rot: float(np.interp(meio, xs, ys)) for rot, (xs, ys, _) in curvas.items()}
                conta = {rot: int(((k.nivel_regua_cm >= lo) & (k.nivel_regua_cm < lo + ARGS.faixa_cm)).sum()) for rot, (_, _, k) in curvas.items()}
                faixas.append({"sensor": nome, "definicao": d, "faixa_de_nivel_cm": f"{lo:g}–{lo + ARGS.faixa_cm:g}", "cenas_antes": conta["antes"], "cenas_depois": conta["depois"], "curva_no_meio_da_faixa_antes_km2": val["antes"],
                               "curva_no_meio_da_faixa_depois_km2": val["depois"], "diferenca_km2": val["depois"] - val["antes"], "mudou": bool(abs(val["depois"] - val["antes"]) > ARGS.mudanca_minima_km2)})
    pontos, equiv, faixas = pd.DataFrame(pontos), pd.DataFrame(equiv), pd.DataFrame(faixas)
    confere = equiv[["nivel_equivalente_antes_cm", "nivel_equivalente_gravado_antes_cm"]].dropna()
    dif_antes = float((confere.nivel_equivalente_antes_cm - confere.nivel_equivalente_gravado_antes_cm).abs().max()) if len(confere) else np.nan
    if len(confere) and dif_antes > 1e-6:
        AVISOS.append(f"o 'antes' recalculado difere do gravado na rodada das cheias novas (maior diferença {dif_antes:.4f} cm)")
    gravar(pontos, "curva-nivel-x-area-antes-e-depois_sentinel-ana_2016-2026_ponto", "C4 — Pontos da curva crescente, antes (cenas boas já existentes) e depois (mais as da cheia de outubro de 2026)", {
        "sensor": "cenas usadas: radar, óptico ou os dois", "definicao": "A1 = água ligada ao rio; A2 = toda a água em terra", "conjunto": "antes ou depois", "nivel_regua_cm": "nível da régua", "cenas_no_nivel": "cenas com esse nível",
        "area_ajustada_km2": "valor da curva crescente"}, casas={"nivel_regua_cm": 1})
    col_eq = {"cota_cm": "cota da mancha acumulada do SGB", "mancha_em_terra_km2": "mancha em terra no domínio", "sensor": "cenas usadas na curva", "definicao": "A1 ou A2", "cenas_antes": "cenas na curva antes", "cenas_depois": "cenas na curva depois",
              "nivel_equivalente_antes_cm": "nível em que a curva antiga chega à área da mancha", "nivel_equivalente_depois_cm": "o mesmo, com as cenas novas", "situacao_antes": "onde a área da mancha cai na curva antiga",
              "situacao_depois": "o mesmo, na curva nova", "maior_valor_da_curva_antes_km2": "valor da curva antiga no maior nível", "maior_valor_da_curva_depois_km2": "valor da curva nova no maior nível",
              "nivel_equivalente_gravado_antes_cm": "o valor 'depois' gravado na rodada das cheias novas (conferência do 'antes')", "mudanca_cm": "depois menos antes", "marca": "ressalva sobre a mancha"}
    equiv = equiv[["cota_cm", "mancha_em_terra_km2", "sensor", "definicao", "cenas_antes", "cenas_depois", "nivel_equivalente_antes_cm", "nivel_equivalente_depois_cm", "mudanca_cm", "situacao_antes", "situacao_depois",
                   "maior_valor_da_curva_antes_km2", "maior_valor_da_curva_depois_km2", "nivel_equivalente_gravado_antes_cm", "marca"]]
    gravar(equiv, "nivel-equivalente-das-manchas-antes-e-depois_sentinel-sgb_2016-2026_cota", "C4 — Nível equivalente de cada mancha do SGB, antes e depois das cenas da cheia de outubro de 2026", col_eq,
           casas={"nivel_equivalente_antes_cm": 0, "nivel_equivalente_depois_cm": 0, "nivel_equivalente_gravado_antes_cm": 0, "mudanca_cm": 0}, nota=nota_extra, maior_diferenca_do_antes_para_o_gravado_cm=dif_antes)
    gravar(faixas, "cenas-e-curva-por-faixa-de-nivel-antes-e-depois_sentinel-ana_2016-2026_faixa", f"C4 — Cenas e curva por faixa de nível de {ARGS.faixa_cm:g} cm, antes e depois", {
        "sensor": "cenas usadas", "definicao": "A1 ou A2", "faixa_de_nivel_cm": "faixa do nível da régua (limite de baixo incluído)", "cenas_antes": "cenas boas na faixa antes", "cenas_depois": "cenas boas na faixa depois",
        "curva_no_meio_da_faixa_antes_km2": "valor da curva antiga no meio da faixa", "curva_no_meio_da_faixa_depois_km2": "valor da curva nova no meio da faixa", "diferenca_km2": "depois menos antes",
        "mudou": f"a curva mudou mais de {ARGS.mudanca_minima_km2:g} km² no meio da faixa"})
    pat = c2[c2.boa & ~c2.ja_classificada_antes & (c2.fase_na_cheia.isin(["patamar", "pico"])) & (c2.cm_abaixo_do_pico <= ARGS.patamar_cm)]
    resumo_pat = []
    for nome, sens in cna.SENSORES.items():
        k = pat[pat.sensor.isin(sens)]
        for d, col in (("A1", "inundacao_em_terra_km2"), ("A2", "agua_total_em_terra_km2")):
            resumo_pat.append({"sensor": nome, "definicao": d, "cenas": len(k), "regua_minima_cm": k.regua_cm.min() if len(k) else np.nan, "regua_maxima_cm": k.regua_cm.max() if len(k) else np.nan,
                               "area_minima_km2": k[col].min() if len(k) else np.nan, "area_mediana_km2": k[col].median() if len(k) else np.nan, "area_maxima_km2": k[col].max() if len(k) else np.nan,
                               "amplitude_km2": (k[col].max() - k[col].min()) if len(k) else np.nan})
    gravar(pd.DataFrame(resumo_pat), "cenas-do-patamar_sentinel-ana_2026-10_sensor", f"C4 — Cenas boas do patamar (régua do dia a até {ARGS.patamar_cm:g} cm do pico do episódio)", {
        "sensor": "cenas usadas", "definicao": "A1 ou A2", "cenas": "cenas boas no patamar", "regua_minima_cm": "menor régua do dia entre elas", "regua_maxima_cm": "maior régua do dia entre elas",
        "area_minima_km2": "menor área", "area_mediana_km2": "mediana", "area_maxima_km2": "maior área", "amplitude_km2": "maior menos menor"}, casas={"regua_minima_cm": 1, "regua_maxima_cm": 1}, pico_media_do_dia_cm=e["pico_media_do_dia_cm"])

    # ---- C5: figuras
    if ARGS.figuras:
        ARGS.figuras.mkdir(parents=True, exist_ok=True)
        figura_nivel_area(ARGS.figuras / "nivel-x-area-com-a-cheia-de-outubro.png", todas, curvas_fig, sgb, extraidas)
        if mais_alta:
            k = cenas[mais_alta]
            ln = k["linha"]
            contornos = [(geo_m[952], "#ff7f00", "-", "mancha SGB 952 cm")]
            if 1053 in geo_m:
                contornos.append((geo_m[1053], "#ff4fd8", (0, (4, 2)), "mancha SGB 1.053 cm (extraída de figura, não conferida)"))
            if ln["sensor"] == RADAR:
                fundo, tipo = (lambda vv: np.where(np.isfinite(vv) & (vv > 0), 10 * np.log10(np.where(vv > 0, vv, 1)), np.nan))(ao.para_a_grade(brutos[(RADAR, ln["identificador"])]["arquivos"]["vv"], grade, Resampling.nearest, np.nan)), "db"
                rot_fundo = "VV em dB (−25 a 0)"
            else:
                cortes = None
                if ARGS.imagens and (ARGS.imagens / "indice_imagens_sentinel.json").exists():
                    cortes = json.loads((ARGS.imagens / "indice_imagens_sentinel.json").read_text(encoding="utf-8"))["cortes_de_esticamento"].get("falsacor-b11b08b03", {}).get("refletancia")
                bo = brutos[(OPTICO, ln["identificador"])]
                bandas = [im.refletancia(bo["arquivos"][n], grade, float(ln["versao_do_processamento"] or 0) >= 4, Resampling.bilinear if n == "b11" else Resampling.nearest) for n in ("b11", "b08", "b03")]
                lim = [tuple(cortes[n.upper()]) for n in ("b11", "b08", "b03")] if cortes else [tuple(np.nanpercentile(v, [2, 98])) for v in bandas]
                fundo, tipo = [np.nan_to_num(np.clip((v - lo) / (hi - lo), 0, 1)) for v, (lo, hi) in zip(bandas, lim)], "rgb"
                rot_fundo = "falsa cor B11-B08-B03"
            figura_cena(ARGS.figuras / "cena-mais-alta-da-cheia-de-outubro-com-manchas.png", fundo, tipo, k["inundacao_em_terra"] & dentro, leito_geom, contornos, urbano, ext,
                        f"{NOME[ln['sensor']].capitalize()} de {ln['data_hora_local'][:16]} (órbita {ln['orbita_relativa']}, régua {ao.fmt(float(ln['regua_cm']), 0)} cm no dia, "
                        f"{ao.fmt(float(ln['regua_cm_hora']), 0)} cm na hora) — {rot_fundo}; a cena boa de régua mais alta da cheia de outubro de 2026")

    # ---- tabela para o roteiro das imagens: as cenas boas da rodada das cheias novas e as novas, nas colunas que ele lê
    if ARGS.tabela_para_imagens:
        cols = ["episodio", "sensor", "identificador", "data_hora_utc", "data_hora_local", "orbita_relativa", "direcao", "versao_do_processamento", "arquivos", "boa", "fase", "regua_cm", "origem_do_nivel", "regua_cm_hora",
                "origem_do_nivel_da_hora", "nuvem_ou_sombra_pct", "inundacao_em_terra_km2"]
        novas_im = c2[c2.boa & ~c2.ja_classificada_antes].assign(episodio="E28")  # na coluna janela do índice, todas as cenas novas levam o episódio
        pd.concat([c49[cols], novas_im[cols]], ignore_index=True).to_csv(ARGS.tabela_para_imagens, index=False)
    saida = {"conferencia_do_metodo": conf, "cenas": {"lidas": int((c2.motivo.fillna("") != "não lida (falha na leitura)").sum()), "boas": c2[c2.boa].groupby("sensor").size().to_dict(), "boas_novas": int((c2.boa & ~c2.ja_classificada_antes).sum()),
             "recusadas": c2[~c2.boa].groupby(["sensor", "motivo"]).size().rename("n").reset_index().to_dict("records")}, "mais_alta": mais_alta, "conferencia_do_antes_cm": dif_antes, "avisos": AVISOS}
    print(json.dumps(saida, ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", force=True)
    _p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    _p.add_argument("--etapa", required=True, choices=["regua", "passagens", "agua"], help="regua: episódio e tendência; passagens: catálogo e lista do que ler; agua: classificação, manchas e curva (depois de ler as cenas)")
    _p.add_argument("--inicio", default="2014-10-01", help="primeiro dia da série diária montada (AAAA-MM-DD)")
    _p.add_argument("--telemetria-nova", default="nivel-rio_ana-telemetria-77150000_2026-09-01-a-2026-10-18_15min.csv", help="arquivo de telemetria desta consulta, em data/raw/")
    _p.add_argument("--leituras-min", type=int, default=48, help="a média do dia da telemetria só vale com pelo menos estas leituras no dia")
    _p.add_argument("--limiar-cm", type=float, default=800.0, help="nível a partir do qual o dia é de cheia (cm)")
    _p.add_argument("--limiar-alto-cm", type=float, default=850.0, help="segundo limiar para contar dias (cm)")
    _p.add_argument("--intervalo-dias", type=int, default=3, help="trechos separados por até estes dias abaixo do limiar contam como um episódio")
    _p.add_argument("--episodio-desde", default="2026-10-01", help="o episódio examinado é o primeiro que termina nesta data ou depois")
    _p.add_argument("--horas-tendencia", type=int, default=72, help="horas finais usadas na tendência")
    _p.add_argument("--figura-desde", default="2026-09-01", help="início da figura da régua")
    _p.add_argument("--busca-desde", default="2026-09-25", help="início da busca principal no catálogo (dia local)")
    _p.add_argument("--rio-baixo-desde", default="2026-09-01", help="início da busca à parte da passagem de rio baixo antes do episódio")
    _p.add_argument("--rio-baixo-cm", type=float, default=600.0, help="a passagem de rio baixo tem régua do dia abaixo disto (cm)")
    _p.add_argument("--orbitas-com-referencia", type=int, nargs="+", default=[68, 170], help="órbitas de radar com cena de referência de rio baixo")
    _p.add_argument("--margem-m", type=float, default=500.0, help="margem em volta do retângulo da área urbana, na busca (m)")
    _p.add_argument("--cobertura-min", type=float, default=95.0, help="a pegada tem de cobrir pelo menos isto do domínio (%%)")
    _p.add_argument("--nuvem-max", type=float, default=40.0, help="óptico: nuvem declarada da quadrícula até isto (%%)")
    _p.add_argument("--prioridade-cm", type=float, default=950.0, help="prioridade 0: régua do dia a partir disto (cm)")
    _p.add_argument("--teto-de-arquivos", type=int, default=80, help="teto de arquivos brutos novos")
    _p.add_argument("--ativos-optico", nargs="+", default=["SCL", "B02", "B03", "B04", "B08", "B11"], help="arquivos por cena óptica")
    _p.add_argument("--tentativas", type=int, default=4, help="pedidos ao catálogo: a primeira tentativa e as repetições com espera")
    _p.add_argument("--patamar-cm", type=float, default=30.0, help="cena do patamar: régua do dia a até isto do pico do episódio (cm)")
    _p.add_argument("--conferir-episodios", nargs="*", default=["E28", "E26"], help="episódios da rodada das cheias novas cujas cenas boas são reclassificadas para conferir o método")
    _p.add_argument("--mancha-extra", nargs="*", help="COTA=ARQUIVO.gpkg:CAMADA de mancha acumulada de fora do repositório (extraída de figura, não conferida): só leitura")
    _p.add_argument("--faixa-inicio", type=float, default=800.0, help="início das faixas de nível (cm)")
    _p.add_argument("--faixa-fim", type=float, default=1300.0, help="fim das faixas de nível (cm)")
    _p.add_argument("--faixa-cm", type=float, default=50.0, help="largura das faixas de nível (cm)")
    _p.add_argument("--mudanca-minima-km2", type=float, default=0.05, help="a curva 'mudou' numa faixa se o valor no meio dela variar mais que isto (km²)")
    _p.add_argument("--imagens", type=Path, help="pasta das imagens para SIG, só para ler os cortes da falsa cor")
    _p.add_argument("--figuras", type=Path, help="pasta (fora do repositório) das figuras")
    _p.add_argument("--tabela-para-imagens", type=Path, help="arquivo (fora do repositório) com as cenas boas para imagens_sentinel_para_sig.py --cenas-extras")
    ARGS = _p.parse_args()
    FONTES, AVISOS = [], []
    for _pasta in (ARGS.figuras, ARGS.tabela_para_imagens):
        if _pasta is not None and c.RAIZ in _pasta.resolve().parents:
            raise SystemExit("--figuras e --tabela-para-imagens têm de ficar fora do repositório")
    PROC.mkdir(parents=True, exist_ok=True)
    {"regua": etapa_regua, "passagens": etapa_passagens, "agua": etapa_agua}[ARGS.etapa]()
