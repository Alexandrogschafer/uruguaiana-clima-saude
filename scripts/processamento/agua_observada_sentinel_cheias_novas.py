"""
Cheias fora das janelas já usadas e lacunas da série da régua.

Duas etapas, com a leitura das cenas entre uma e outra:

  --etapa regua   junta à série diária da régua (média diária bruta) o trecho novo
                  da série histórica e a telemetria (média do dia, quando o dia tem
                  pelo menos --leituras-min leituras), sem alterar a série antiga;
                  diz o que cada lacuna ganhou, compara telemetria e série nos dias
                  em que as duas existem, refaz a detecção de episódios de cheia só
                  no período novo ou antes sem valor e, para episódio novo, consulta
                  o catálogo (só metadados) com a mesma triagem e a mesma regra de
                  lista curta de scripts/download/sentinel_inventario_cheias.py.
                  Grava a lista das passagens a ler (a lista curta do inventário,
                  menos as de --excluir, mais as dos episódios novos).
  --etapa agua    classifica cada passagem lida com as funções de
                  agua_observada_sentinel_area_urbana.py (mesmas referências de
                  órbita, mesmos limiares, mesmo leito de referência, mesmo domínio,
                  mesmo controle de qualidade), compara as cenas boas com as manchas
                  por cota e refaz a curva nível × área de
                  agua_observada_sentinel_curva_nivel_area.py com as cenas antigas
                  mais as novas. À parte, uma cena recusada pode ser só olhada
                  (--olhar): figura e a área que a regra daria; ela continua recusada.

As cenas são achadas em data/raw/sentinel/ pelo identificador gravado no .json de
cada arquivo (scripts/download/sentinel_planetary_computer.py --bandas-extras).
Saídas em data/processed/agua_observada_sentinel_cheias_novas/ (fora do git);
figuras só em --figuras (fora do repositório). Produtos DERIVADOS, para conferência.

Uso:
  python scripts/processamento/agua_observada_sentinel_cheias_novas.py --etapa regua [--excluir SENSOR:AAAA-MM-DD ...]
  python scripts/processamento/agua_observada_sentinel_cheias_novas.py --etapa agua \
      [--mancha-extra COTA=ARQUIVO.gpkg:CAMADA] [--olhar AAAA-MM-DD] [--figuras PASTA] [--copia PASTA]
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
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
from rasterio import features  # noqa: E402
from rasterio.transform import from_origin  # noqa: E402
from rasterio.warp import Resampling  # noqa: E402
from shapely.geometry import box  # noqa: E402

sys.path.append(str(Path(__file__).resolve().parents[1] / "download"))
import agua_observada_sentinel as ao  # noqa: E402
import agua_observada_sentinel_area_urbana as au  # noqa: E402
import agua_observada_sentinel_curva_nivel_area as cna  # noqa: E402
import dinamica_populacional_comum as c  # noqa: E402
import exposicao_inundacao_cenarios as ec  # noqa: E402
import exposicao_inundacao_enderecos as ee  # noqa: E402
import imagens_sentinel_para_sig as im  # noqa: E402
import sentinel_inventario_cheias as inv  # noqa: E402

logger = logging.getLogger(__name__)

SCRIPT = "scripts/processamento/agua_observada_sentinel_cheias_novas.py"
MOTIVO = "cheias fora das janelas já usadas e lacunas da série da régua"
PROC = c.RAIZ / "data" / "processed" / "agua_observada_sentinel_cheias_novas"
PROC44, PROC45, INV = au.PROC, cna.PROC, inv.PROC
RES, RADAR, OPTICO = au.RES, au.RADAR, au.OPTICO
NOME = {RADAR: "radar", OPTICO: "óptico"}
PADRAO_TRECHO = "nivel-rio_ana-serie-historica-*_*-a-*_diario.csv"  # trecho novo da série histórica (a série inteira tem outro padrão de nome)
ARQ_LISTA = "passagens-a-ler_planetary-computer_2014-2026_passagem"
LIMITACOES = [*au.LIMITACOES, "a média diária tirada da telemetria é a média das leituras do dia, no fuso em que o serviço as devolve (não informado); não é a média diária da série histórica",
              "a curva nível × área é crescente por construção e mistura subida e descida", "nas cenas depois do fim da série histórica, o nível do dia é o da telemetria"]


# ---------------------------------------------------------------- gravação
def rel(p: Path) -> str:
    return str(Path(p).relative_to(c.RAIZ))


def meta(caminho: Path, **kw) -> None:
    c.gravar_meta(caminho, crs=c.CRS_PADRAO, status=c.STATUS_CONFERENCIA, ligado_ao_portal=False, script=SCRIPT, motivo=MOTIVO, fontes=FONTES,
                  fora_do_git="data/processed/ é ignorado; produto derivado, para conferência", limitacoes=LIMITACOES, avisos=AVISOS or None,
                  argumentos={k: v for k, v in vars(ARGS).items() if k not in ("figuras", "copia", "mancha_extra", "imagens")}, **kw)


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
def series_da_regua() -> dict:
    """Série antiga (bruta; onde falta, a consistida), trecho novo da série histórica e média diária da telemetria; e a série junta."""
    diaria = ao.ler_serie_diaria()
    d = diaria[pd.Timestamp(ARGS.inicio):]
    antiga = d.media_bruta_cm.where(d.media_bruta_cm.notna(), d.media_consistida_cm).dropna()
    partes, arqs_trecho = [], sorted(c.RAW.glob(PADRAO_TRECHO))
    for arq in arqs_trecho:
        b = pd.read_csv(arq, dtype=str)
        b = b[(b.media_diaria == "1") & (b.nivel_consistencia == "1")]
        partes.append(pd.Series(pd.to_numeric(b.cota_cm, errors="coerce").values, index=pd.to_datetime(b["data"])).dropna())
    trecho = pd.concat(partes).groupby(level=0).first() if partes else pd.Series(dtype=float)
    tel = ao.ler_telemetria()
    if tel is None:
        por_dia = pd.DataFrame(columns=["media", "leituras"])
    else:
        por_dia = tel.groupby(tel.dh.dt.normalize()).nivel_cm.agg(media="mean", leituras="size")
    tel_dia = por_dia.media[por_dia.leituras >= ARGS.leituras_min]
    fim = max([x.index.max() for x in (antiga, trecho, tel_dia) if len(x)])
    dias = pd.date_range(ARGS.inicio, fim, freq="D")
    junta = antiga.reindex(dias).combine_first(trecho.reindex(dias)).combine_first(tel_dia.reindex(dias))
    origem = pd.Series(np.where(antiga.reindex(dias).notna(), "série antiga", np.where(trecho.reindex(dias).notna(), "série histórica (trecho novo)", np.where(tel_dia.reindex(dias).notna(), "telemetria (média do dia)", ""))), index=dias)
    return {"antiga": antiga, "trecho": trecho, "telemetria_por_dia": por_dia, "telemetria": tel_dia, "junta": junta, "origem": origem, "diaria": diaria, "tel": tel,
            "arquivos": {"serie_antiga": diaria.attrs.get("arquivo"), "trecho_novo": [rel(a) for a in arqs_trecho], "telemetria": (tel.attrs.get("arquivos") if tel is not None else [])}}


def etapa_regua() -> None:
    global FONTES
    FONTES = ["ANA — série histórica de cotas e telemetria da estação fluviométrica (serviço público, sem conta)", "Microsoft Planetary Computer — catálogo STAC (só metadados)"]
    r = series_da_regua()
    antiga, trecho, tel, junta, origem = r["antiga"], r["trecho"], r["telemetria"], r["junta"], r["origem"]
    fim_antiga = antiga.index.max()

    # ---- A1: cada trecho sem valor da série antiga (e o período depois do fim dela)
    falta = antiga.reindex(junta.index).isna()
    blocos = (falta != falta.shift()).cumsum()[falta]
    a1 = []
    for _, g in falta[falta].groupby(blocos):
        ini, fim = g.index[0], g.index[-1]
        o = origem[ini:fim]
        a1.append({"inicio": f"{ini:%Y-%m-%d}", "fim": f"{fim:%Y-%m-%d}", "o_que_e": "depois do fim da série antiga" if ini > fim_antiga else "lacuna da série antiga", "dias": len(g),
                   "dias_com_a_serie_historica_nova": int((o == "série histórica (trecho novo)").sum()), "dias_com_a_telemetria": int((o == "telemetria (média do dia)").sum()), "dias_ainda_sem_valor": int((o == "").sum()),
                   "dias_com_telemetria_incompleta": int(((r["telemetria_por_dia"].leituras.reindex(g.index) < ARGS.leituras_min)).sum()), "maior_nivel_cm": float(junta[ini:fim].max()) if junta[ini:fim].notna().any() else np.nan})
    a1 = pd.DataFrame(a1)
    a1 = a1[(a1.dias >= ARGS.lacuna_min_dias) | (a1.o_que_e != "lacuna da série antiga")].reset_index(drop=True)
    gravar(a1, "lacunas-da-regua_ana_2014-2026_trecho", "A1 — Lacunas da série da régua e o que o dado novo fechou", {
        "inicio": "primeiro dia sem valor na série antiga", "fim": "último dia", "o_que_e": "lacuna no meio da série antiga ou período depois do fim dela", "dias": "dias do trecho",
        "dias_com_a_serie_historica_nova": "dias que o trecho novo da série histórica (média diária bruta) preenche", "dias_com_a_telemetria": f"dias preenchidos pela média do dia da telemetria (dias com pelo menos {ARGS.leituras_min} leituras)",
        "dias_ainda_sem_valor": "dias que continuam sem valor", "dias_com_telemetria_incompleta": f"dias com telemetria, mas com menos de {ARGS.leituras_min} leituras (não usados)", "maior_nivel_cm": "maior média diária do trecho, com o dado novo"},
        casas={"maior_nivel_cm": 1}, arquivos=r["arquivos"], fim_da_serie_antiga=f"{fim_antiga:%Y-%m-%d}", fim_da_serie_junta=f"{junta.dropna().index.max():%Y-%m-%d}",
        trecho_novo_da_serie_historica={"dias_com_valor": int(len(trecho)), "inicio": f"{trecho.index.min():%Y-%m-%d}" if len(trecho) else None, "fim": f"{trecho.index.max():%Y-%m-%d}" if len(trecho) else None})

    # ---- A2: telemetria (média do dia) e trecho novo × série antiga, nos dias em que as duas existem
    a2 = []
    for rotulo, nova in (("telemetria (média do dia)", tel), ("série histórica (trecho novo)", trecho)):
        k = pd.concat([antiga.rename("a"), nova.rename("n")], axis=1).dropna()
        for ano, g in [*k.groupby(k.index.year), ("todos", k)]:
            if len(g):
                dif = g.n - g.a
                a2.append({"fonte_nova": rotulo, "ano": str(ano), "dias_em_comum": len(g), "diferenca_media_cm": float(dif.mean()), "diferenca_media_em_modulo_cm": float(dif.abs().mean()), "diferenca_maxima_em_modulo_cm": float(dif.abs().max()),
                           "dia_da_diferenca_maxima": f"{dif.abs().idxmax():%Y-%m-%d}", "dias_com_mais_de_10_cm": int((dif.abs() > 10).sum())})
    a2 = pd.DataFrame(a2)
    gravar(a2, "telemetria-x-serie-da-regua_ana_2015-2026_ano", "A2 — Fonte nova × série antiga da régua (bruta), nos dias em que as duas existem", {
        "fonte_nova": "fonte comparada com a série antiga", "ano": "ano dos dias comparados", "dias_em_comum": "dias com valor nas duas", "diferenca_media_cm": "média de (fonte nova − série antiga)",
        "diferenca_media_em_modulo_cm": "média do módulo da diferença", "diferenca_maxima_em_modulo_cm": "maior módulo da diferença", "dia_da_diferenca_maxima": "dia dessa maior diferença",
        "dias_com_mais_de_10_cm": "dias com diferença maior que 10 cm"})

    # ---- A3: episódios só onde a série antiga não tinha valor
    antes = pd.read_csv(next(iter(sorted(INV.glob("episodios-de-cheia_*_episodio.csv")))), parse_dates=["inicio", "fim", "data_do_pico"])
    ep = inv.episodios(junta, ARGS.limiar_cm, ARGS.intervalo_dias)
    ep = ep[[not any((a.inicio <= r_.fim) and (r_.inicio <= a.fim) for a in antes.itertuples()) for r_ in ep.itertuples()]].reset_index(drop=True)
    ep.insert(0, "episodio", range(int(antes.episodio.max()) + 1, int(antes.episodio.max()) + 1 + len(ep)))
    ep["origem_do_pico"] = [origem[x] for x in ep.data_do_pico]
    ep["em_aberto"] = ep.fim >= junta.dropna().index.max()
    dia = lambda t, cols: t.assign(**{k: t[k].dt.strftime("%Y-%m-%d") for k in cols})  # noqa: E731
    col_ep = {"episodio": "número do episódio, continuando a numeração do inventário", "inicio": "primeiro dia no limiar ou acima", "fim": "último dia no limiar ou acima", "duracao_dias": "dias do início ao fim",
              "dias_no_limiar_ou_acima": "dias do episódio no limiar ou acima", "data_do_pico": "dia do maior nível", "pico_cm": "maior média diária", "origem_do_pico": "de onde vem o nível do pico",
              "em_aberto": "o episódio vai até o último dia com dado (pode não ter terminado)"}
    gravar(dia(ep, ["inicio", "fim", "data_do_pico"]), "episodios-novos_ana_2024-2026_episodio", f"A3 — Episódios de cheia (média diária ≥ {ARGS.limiar_cm:g} cm) no período novo ou antes sem valor", col_ep, casas={"pico_cm": 1})

    # ---- passagens dos episódios novos: só a busca do catálogo, com a triagem e a lista curta do inventário
    pico_dia = junta.notna() & (junta == junta.rolling(7, center=True, min_periods=1).max())
    novas, curta = pd.DataFrame(), pd.DataFrame()
    if len(ep):
        inv.ARGS = argparse.Namespace(tentativas=ARGS.tentativas)
        urbano = gpd.read_file(PROC44 / "area-urbana_sgb_2023_retangulo.gpkg", layer="area_urbana").to_crs(c.CRS_PADRAO).geometry.iloc[0]
        dominio_proj = box(*urbano.bounds).buffer(ARGS.margem_m, join_style="mitre")
        dominio = gpd.GeoSeries([dominio_proj], crs=c.CRS_PADRAO).to_crs("EPSG:4326").iloc[0]  # em graus só para a busca
        linhas = []
        for r_ in ep.itertuples():
            ini, fim = r_.inicio - pd.Timedelta(days=ARGS.folga_dias), r_.fim + pd.Timedelta(days=ARGS.folga_dias)
            for sensor in (RADAR, OPTICO):
                col = inv.spc.SENSORES[sensor]["colecao"]
                achadas = inv.passagens(inv.buscar(col, ini, fim + pd.Timedelta(days=1), dominio.bounds), sensor, col, dominio, dominio_proj)
                linhas += [{"episodio": r_.episodio, **x} for x in achadas if ini <= x["data_local"] <= fim]
        novas = pd.DataFrame(linhas).drop(columns="geometry").sort_values(["data_hora_utc", "sensor"]).reset_index(drop=True) if linhas else pd.DataFrame()
    if len(novas):
        pico = ep.set_index("episodio")
        novas["nivel_regua_cm"] = [junta.get(x, np.nan) for x in novas.data_local]
        novas["origem_do_nivel"] = [origem.get(x, "") for x in novas.data_local]
        novas["fase"] = [cna.fase_da_cheia(x, junta, pico_dia)["fase"] for x in novas.data_local]
        novas["dias_ate_o_pico"] = [(x - pico.data_do_pico[e]).days for x, e in zip(novas.data_local, novas.episodio)]
        novas["cm_abaixo_do_pico"] = [pico.pico_cm[e] - n for n, e in zip(novas.nivel_regua_cm, novas.episodio)]
        novas["provavel"] = (novas.cobertura_do_dominio_pct >= ARGS.cobertura_min) & ((novas.sensor == RADAR) | (novas.nuvem_da_quadricula_pct < ARGS.nuvem_max))
        novas["data_local"] = novas.data_local.dt.strftime("%Y-%m-%d")
        perto = lambda t: t.assign(_d=t.dias_ate_o_pico.abs(), _s=np.where((t.sensor == OPTICO) & (t.nuvem_da_quadricula_pct >= ARGS.nuvem_preferir_radar), 1, 0)).sort_values(["_d", "_s", "cm_abaixo_do_pico"])  # noqa: E731
        escolhas = []
        for e in ep.episodio:
            k = novas[(novas.episodio == e) & novas.provavel]
            if not len(k):
                continue
            feitas = [("mais perto do pico", perto(k).iloc[0])]
            for fase in ("subida", "descida"):
                resto = k[(k.fase == fase) & ~k.identificadores.isin([x[1].identificadores for x in feitas])]
                if len(resto):
                    feitas.append((f"na {fase}", perto(resto).iloc[0]))
            escolhas += [{"episodio": e, "papel": papel, **{x: m[x] for x in ("sensor", "data_hora_utc", "data_hora_local", "plataforma", "orbita_relativa", "direcao", "nivel_regua_cm", "fase", "dias_ate_o_pico", "cm_abaixo_do_pico",
                                                                             "cobertura_do_dominio_pct", "nuvem_da_quadricula_pct", "identificadores")}} for papel, m in feitas]
        curta = pd.DataFrame(escolhas)
        col_b = {"episodio": col_ep["episodio"], "sensor": "sensor e produto", "colecao": "coleção do catálogo", "data_hora_utc": "início da passagem, UTC", "data_hora_local": "o mesmo, em UTC−3", "data_local": "dia local",
                 "plataforma": "satélite", "orbita_relativa": "órbita relativa", "direcao": "direção da órbita", "quadricula": "quadrícula do óptico", "itens_no_catalogo": "itens da passagem no catálogo",
                 "itens_que_tocam_o_dominio": "dos quais, com pegada que toca o domínio", "cobertura_do_dominio_pct": "% do domínio coberta pela pegada", "nuvem_da_quadricula_pct": "nuvem declarada da quadrícula INTEIRA",
                 "identificadores": "identificadores dos itens", "nivel_regua_cm": "média diária da régua no dia local", "origem_do_nivel": "de onde vem o nível", "fase": "subida, descida ou pico",
                 "dias_ate_o_pico": "dia da passagem menos o dia do pico", "cm_abaixo_do_pico": "pico menos o nível do dia", "provavel": "triagem pelo catálogo", "papel": "por que entrou na lista curta"}
        gravar(novas, "passagens-dos-episodios-novos_planetary-computer_2026_passagem", "A3 — Passagens Sentinel nos episódios novos", col_b, casas={"nivel_regua_cm": 1}, data_da_consulta=datetime.now(timezone.utc).isoformat(timespec="seconds"))
        if len(curta):
            gravar(curta, "lista-curta-dos-episodios-novos_planetary-computer_2026_passagem", "A3 — Lista curta dos episódios novos (até 3 passagens prováveis por episódio)", col_b, casas={"nivel_regua_cm": 1})

    # ---- a lista do que ler: a lista curta do inventário, menos as excluídas, mais a dos episódios novos
    base = pd.read_csv(next(iter(sorted(INV.glob("lista-curta-sugerida_*_passagem.csv")))))
    base["data_hora_utc"] = (pd.to_datetime(base.data_hora_local) + pd.Timedelta(hours=3)).dt.strftime("%Y-%m-%d %H:%M:%S")
    fora = {tuple(x.split(":")) for x in ARGS.excluir}
    tirar = [(("radar" if s == "radar" else "optico"), d[:10]) in fora for s, d in zip(base.sensor.map({"radar": "radar", "óptico": "optico"}), base.data_hora_local)]
    base["sensor"] = base.sensor.map({"radar": RADAR, "óptico": OPTICO})
    cols = ["episodio", "papel", "sensor", "data_hora_utc", "data_hora_local", "plataforma", "orbita_relativa", "nivel_regua_cm", "fase", "dias_ate_o_pico", "cm_abaixo_do_pico", "nuvem_da_quadricula_pct", "identificadores"]
    lista = pd.concat([base[~np.array(tirar)][cols].assign(origem="lista curta do inventário"), curta[cols].assign(origem="episódio novo") if len(curta) else None], ignore_index=True)
    prioridade = lambda t: np.where(t.episodio.isin(ARGS.episodios_primeiro), 0, np.where(t.papel == "mais perto do pico", 1, 2))  # noqa: E731
    lista.insert(0, "prioridade", prioridade(lista))
    lista["arquivos_a_ler"] = lista.sensor.map({RADAR: len(ARGS.ativos_radar), OPTICO: len(ARGS.ativos_optico)})
    lista = lista.sort_values(["prioridade", "episodio", "data_hora_utc"]).reset_index(drop=True)
    lista["dentro_do_teto"] = lista.arquivos_a_ler.cumsum() <= ARGS.teto_de_arquivos
    gravar(lista, ARQ_LISTA, "B — Passagens a ler (lista curta do inventário, menos as excluídas, mais as dos episódios novos)", {
        "prioridade": "0 = episódios de --episodios-primeiro; 1 = mais perto do pico; 2 = as demais", "episodio": "episódio de cheia", "papel": "por que entrou na lista", "sensor": "sensor e produto", "data_hora_utc": "início da passagem, UTC",
        "data_hora_local": "o mesmo, em UTC−3", "plataforma": "satélite", "orbita_relativa": "órbita relativa", "nivel_regua_cm": "média diária da régua no dia", "fase": "fase da cheia pelo inventário", "dias_ate_o_pico": "dia da passagem menos o dia do pico",
        "cm_abaixo_do_pico": "pico menos o nível do dia", "nuvem_da_quadricula_pct": "nuvem declarada da quadrícula inteira (óptico)", "identificadores": "identificadores dos itens no catálogo", "origem": "de que lista veio",
        "arquivos_a_ler": "arquivos brutos da passagem", "dentro_do_teto": f"cabe no teto de {ARGS.teto_de_arquivos} arquivos, pela ordem de prioridade"}, casas={"nivel_regua_cm": 1},
        excluidas=sorted(":".join(x) for x in fora), total_de_arquivos=int(lista.arquivos_a_ler.sum()))
    print(json.dumps({"fim_da_serie_antiga": f"{fim_antiga:%Y-%m-%d}", "fim_da_serie_junta": f"{junta.dropna().index.max():%Y-%m-%d}", "lacunas": a1.to_dict("records"), "comparacao": a2.to_dict("records"),
                      "episodios_novos": dia(ep, ["inicio", "fim", "data_do_pico"]).to_dict("records"), "passagens_nos_episodios_novos": len(novas), "lista_curta_nova": curta.drop(columns=["identificadores"]).to_dict("records") if len(curta) else [],
                      "passagens_a_ler": lista.groupby(["sensor", "dentro_do_teto"]).size().rename("n").reset_index().to_dict("records"), "arquivos": int(lista.arquivos_a_ler.sum()), "avisos": AVISOS}, ensure_ascii=False, indent=1, default=str))


# ---------------------------------------------------------------- água
def brutos_por_identificador() -> dict:
    """identificador(es) da cena -> {ativo: caminho} e dados gravados no .json de cada arquivo bruto."""
    out: dict[tuple, dict] = {}
    for sensor in (RADAR, OPTICO):
        for arq in sorted((ao.BRUTO / sensor).glob("*.json")):
            m = json.loads(arq.read_text(encoding="utf-8"))
            ident = "; ".join(m["identificadores_das_cenas"]) if "identificadores_das_cenas" in m else m.get("identificador_da_cena")
            if ident and arq.with_suffix(".tif").exists():
                x = out.setdefault((sensor, ident), {"arquivos": {}, "meta": {}})
                x["arquivos"][m["ativo"].lower()] = arq.with_suffix(".tif")
                x["meta"].update({k: v for k, v in m.items() if k in ("plataforma", "orbita_relativa", "direcao", "versao_do_processamento", "data_hora_da_cena_utc") and v not in (None, "")})
    return out


def figura_olhar(arq: Path, paineis: list[dict], contornos: list[tuple], ext) -> None:
    fig, eixos = plt.subplots(2, 2, figsize=(15, 12.4))
    for ax, p in zip(eixos.ravel(), paineis):
        if p["tipo"] == "rgb":
            ax.imshow(np.dstack(p["dados"]), extent=ext, interpolation="nearest")
        else:
            ax.imshow(p["dados"], cmap="gray" if p["tipo"] == "db" else "RdBu", vmin=p["faixa"][0], vmax=p["faixa"][1], extent=ext, interpolation="nearest")
        if p.get("marca") is not None and p["marca"].any():
            cam = np.zeros(p["marca"].shape + (4,), dtype="float32")
            cam[p["marca"]] = (1.0, 0.25, 0.1, 0.9)
            ax.imshow(cam, extent=ext, interpolation="nearest")
        for geom, cor, estilo in contornos:
            gpd.GeoSeries([geom], crs=c.CRS_PADRAO).boundary.plot(ax=ax, color=cor, linewidth=0.7, linestyle=estilo)
        ax.set_xlim(ext[0], ext[1]); ax.set_ylim(ext[2], ext[3])  # noqa: E702
        ax.set_xticks([]); ax.set_yticks([])  # noqa: E702
        ax.set_title(p["titulo"], fontsize=9.5, loc="left")
    fig.legend(handles=[Line2D([], [], color=cor, lw=1.4, ls=estilo, label=rot) for (_, cor, estilo), rot in zip(contornos, [x["rotulo"] for x in CONTORNOS_ROTULOS])]
               + [Line2D([], [], marker="s", ls="", color=(1.0, 0.25, 0.1), label="leito principal da referência NÃO marcado como água na cena olhada")], loc="lower center", ncol=2, fontsize=8.5, frameon=True, facecolor="#8f8f8f", edgecolor="none", labelcolor="white")
    fig.suptitle("Cena de radar recusada, só para olhar, entre as duas cenas boas vizinhas", fontsize=11, x=0.01, ha="left")
    fig.tight_layout(rect=(0, 0.06, 1, 0.97), h_pad=2.5)
    fig.savefig(arq, dpi=200)
    plt.close(fig)


CONTORNOS_ROTULOS: list[dict] = []


def etapa_agua() -> None:
    global FONTES
    m44 = json.loads((PROC44 / "controle-de-qualidade_sentinel_2017-2024_cena.json").read_text(encoding="utf-8"))
    arg44 = argparse.Namespace(**m44["argumentos"])
    au.ARGS = arg44  # as funções do roteiro da área urbana leem os limiares de lá: os mesmos da classificação das cenas antigas
    FONTES = [*m44["fontes"], "ANA — telemetria da estação fluviométrica (nível das cenas depois do fim da série histórica)"]
    lista = pd.read_csv(PROC / f"{ARQ_LISTA}.csv")
    brutos = brutos_por_identificador()
    reg = series_da_regua()
    junta, origem = reg["junta"], reg["origem"]
    pico_dia = junta.notna() & (junta == junta.rolling(7, center=True, min_periods=1).max())

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
        """As três camadas de água de uma cena, com as regras e o controle de qualidade do roteiro da área urbana."""
        b = brutos[(sensor, ident)]
        out = {"orbita": b["meta"].get("orbita_relativa"), "plataforma": (b["meta"].get("plataforma") or "").upper().replace("SENTINEL-", "S"), "fracao_do_leito": np.nan, "nuvem_ou_sombra_pct": np.nan, "boa": False, "motivo": ""}
        if sensor == RADAR:
            db, valido = au.vv_em_db(b["arquivos"]["vv"], grade)
            out["cobertura_pct"] = float(100 * (valido & dentro).sum() / dentro.sum())
            ref = refs.get(int(out["orbita"])) if out["orbita"] is not None else None
            if ref is None:
                return {**out, "motivo": "órbita sem referência de rio baixo"}
            agua = au.agua_por_mudanca(db, valido, ref, arg44.mudanca, minimo_px)
            valido = valido & ref["valido"]
            fr = float((agua & ref["rio"]).sum() / ref["rio"].sum())
            out.update(fracao_do_leito=fr, boa=bool(fr >= arg44.fracao_minima_do_rio), motivo="" if fr >= arg44.fracao_minima_do_rio else "rio sem contraste", fora_do_leito=ref["rio"] & ~agua, db=db, ref=ref)
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

    # ---- C1 e C2: cada passagem lida
    c2, cenas, geo = [], {}, {RADAR: {k: [] for k in ao.CAMADAS}, OPTICO: {k: [] for k in ao.CAMADAS}}
    for r in lista.itertuples():
        linha = {"episodio": f"E{int(r.episodio):02d}", "sensor": r.sensor, "identificador": r.identificadores, "data_hora_utc": r.data_hora_utc, "data_hora_local": r.data_hora_local, "orbita_relativa": r.orbita_relativa, "plataforma": r.plataforma}
        if (r.sensor, r.identificadores) not in brutos or not ({"vv"} if r.sensor == RADAR else {"b03", "b11", "b08", "scl"}) <= set(brutos[(r.sensor, r.identificadores)]["arquivos"]):
            c2.append({**linha, "boa": False, "motivo": "não lida (fora do teto de arquivos ou falha na leitura)"})
            continue
        k = classificar(r.sensor, r.identificadores)
        local = pd.Timestamp(r.data_hora_local)
        n = im.niveis(local, reg["diaria"], reg["tel"])
        if pd.isna(n["nivel_media_diaria_cm"]):  # depois do fim da série histórica: a média do dia da telemetria
            n.update(nivel_media_diaria_cm=junta.get(local.normalize(), np.nan), nivel_consistencia=origem.get(local.normalize(), ""))
        fase = cna.fase_da_cheia(local.normalize(), junta, pico_dia)
        linha.update(regua_cm=n["nivel_media_diaria_cm"], origem_do_nivel=n["nivel_consistencia"], regua_cm_hora=n["nivel_mais_proximo_da_hora_cm"], origem_do_nivel_da_hora=n["origem_do_nivel_mais_proximo"], **{"fase": fase["fase"]},
                     variacao_em_dois_dias_cm=fase["variacao_em_dois_dias_cm"], cobertura_da_area_urbana_pct=k["cobertura_pct"], fracao_do_leito_principal_marcada_como_agua=k["fracao_do_leito"], nuvem_ou_sombra_pct=k["nuvem_ou_sombra_pct"],
                     boa=k["boa"], motivo=k["motivo"])
        if "agua_total" in k:
            linha.update({f"{cam}_km2": float((k[cam] & k["dominio"]).sum() * px_km2) for cam in ao.CAMADAS})
            linha["agua_total_em_terra_km2"] = float((k["agua_total"] & ~leito & k["dominio"]).sum() * px_km2)
        b = brutos[(r.sensor, r.identificadores)]
        linha.update(direcao=b["meta"].get("direcao", ""), versao_do_processamento=k.get("versao", ""), arquivos="; ".join(a.name for a in b["arquivos"].values()))
        c2.append(linha)
        if not k["boa"]:
            continue
        chave = r.sensor + "_" + local.strftime("%Y%m%dT%H%M")
        cenas[chave] = {**k, "linha": linha}
        for cam in ao.CAMADAS:
            geo[r.sensor][cam].append({"identificador": r.identificadores, "data": f"{local:%Y-%m-%d}", "hora_local": f"{local:%H:%M}", "nivel_regua_cm": linha["regua_cm"], "orbita_relativa": r.orbita_relativa if r.sensor == RADAR else None,
                                       "limiar": f"mudança <= {arg44.mudanca:g} dB e VV <= {arg44.vv_max:g} dB, ou água na referência e VV <= {arg44.vv_max:g} dB" if r.sensor == RADAR else "MNDWI > 0",
                                       "controle_de_qualidade": f"leito principal marcado como água: {100 * k['fracao_do_leito']:.1f} %" if r.sensor == RADAR else f"nuvem ou sombra: {k['nuvem_ou_sombra_pct']:.1f} %",
                                       "area_km2": float((k[cam] & k["dominio"]).sum() * px_km2), "geometry": ao.poligonos(k[cam] & dentro, grade)})
    c2 = pd.DataFrame(c2)
    for col in ("regua_cm", "regua_cm_hora", "agua_total_km2", "agua_ligada_ao_rio_km2", "inundacao_em_terra_km2", "agua_total_em_terra_km2"):
        c2[col] = pd.to_numeric(c2.get(col), errors="coerce")
    col_c2 = {"episodio": "episódio de cheia do inventário (E07, E25…)", "sensor": "sensor e produto", "identificador": "identificador da cena (das fatias, separados por ponto e vírgula)", "data_hora_utc": "data e hora UTC",
              "data_hora_local": "data e hora local (UTC−3)", "orbita_relativa": "órbita relativa", "plataforma": "satélite", "regua_cm": "nível da régua no dia: média diária", "origem_do_nivel": "consistido, bruto ou telemetria",
              "regua_cm_hora": "nível mais próximo da hora da cena", "origem_do_nivel_da_hora": "telemetria ou leitura das 7h ou 17h", "fase": "subida, descida ou pico (regra da curva nível × área)",
              "variacao_em_dois_dias_cm": "nível do dia menos o de dois dias antes", "cobertura_da_area_urbana_pct": "% da área urbana com dado", "fracao_do_leito_principal_marcada_como_agua": "radar: fração do leito principal da referência marcada como água",
              "nuvem_ou_sombra_pct": "óptico: % da área urbana com nuvem ou sombra (SCL)", "boa": "passa no controle de qualidade", "motivo": "por que não passa", "agua_total_km2": "água da cena na área urbana, lado brasileiro",
              "agua_ligada_ao_rio_km2": "água em grupos que tocam o leito de referência", "inundacao_em_terra_km2": "água ligada ao rio fora do leito de referência (A1)", "agua_total_em_terra_km2": "toda a água fora do leito de referência (A2)",
              "direcao": "direção da órbita", "versao_do_processamento": "óptico: versão do processamento", "arquivos": "arquivos brutos da cena"}
    gravar(c2, "controle-de-qualidade-das-cenas-novas_sentinel_2016-2026_cena", "C2 — Cenas novas: controle de qualidade e áreas", col_c2, casas={"regua_cm": 1, "regua_cm_hora": 1, "fracao_do_leito_principal_marcada_como_agua": 3},
           regras={k: getattr(arg44, k) for k in ("mudanca", "vv_max", "vv_agua_referencia", "fracao_minima_do_rio", "nuvem_max", "area_minima_ha", "mediana")}, referencias={o: v["data"] for o, v in refs.items()})
    col_geo = {"identificador": "cena", "data": "data local", "hora_local": "hora local (UTC−3)", "nivel_regua_cm": "nível da régua no dia", "orbita_relativa": "órbita relativa (radar)", "limiar": "regra da água",
               "controle_de_qualidade": "resultado do controle de qualidade", "area_km2": "área da camada na área urbana, lado brasileiro"}
    for sensor, cams in geo.items():
        if cams[ao.CAMADAS[0]]:
            arq = PROC / f"agua-observada-cenas-novas_{sensor}_2016-2026_10m.gpkg"
            for cam, linhas in cams.items():
                g = gpd.GeoDataFrame(linhas, crs=c.CRS_PADRAO)
                g[g.geometry.notna()].to_file(arq, driver="GPKG", layer=cam)
            meta(arq, descricao="água de cada cena nova boa na área urbana: três camadas, uma linha por cena; polígonos de pixel, sem simplificação", camadas=list(ao.CAMADAS), cenas=len(cams[ao.CAMADAS[0]]), colunas=col_geo)

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
        extraidas[int(cota)] = f"{ao.fmt(int(cota))[:-3]}.{ao.fmt(int(cota))[-3:]} cm: extraída de figura, não conferida"
    cotas = sorted(geo_m)
    mancha = {k: cna.rasterizar(geo_m[k], forma, tr) & ~leito for k in cotas}
    nota_extra = ("; ".join(extraidas.values()) + ".") if extraidas else ""
    c3 = []
    for chave, k in sorted(cenas.items(), key=lambda x: x[1]["linha"]["regua_cm"]):
        for cota in cotas:
            c3.append({"episodio": k["linha"]["episodio"], "sensor": k["linha"]["sensor"], "data_hora_local": k["linha"]["data_hora_local"], "nivel_regua_cm": k["linha"]["regua_cm"], "cota_cm": cota,
                       **au.comparar(k["inundacao_em_terra"], mancha[cota], k["dominio"], px_km2), "marca_da_mancha": extraidas.get(cota, "")})
    c3 = pd.DataFrame(c3)
    if len(c3):
        gravar(c3, "agua-x-manchas-cenas-novas_sentinel-sgb_2016-2026_cena-cota", "C3 — Inundação em terra de cada cena nova boa × cada mancha acumulada, na área urbana", {
            "episodio": col_c2["episodio"], "sensor": "sensor e produto", "data_hora_local": "data e hora local", "nivel_regua_cm": "nível da régua no dia", "cota_cm": "cota da mancha acumulada",
            "inundacao_em_terra_km2": "inundação em terra da cena (onde a cena tem dado)", "mancha_em_terra_km2": "mancha fora do leito de referência (mesmo domínio)", "intersecao_km2": "área comum", "uniao_km2": "área de uma ou de outra",
            "intersecao_sobre_uniao": "interseção / união", "fracao_da_agua_dentro_da_mancha": "quanto da inundação da cena cai dentro da mancha", "fracao_da_mancha_coberta_pela_agua": "quanto da mancha a cena cobre", "marca_da_mancha": "ressalva sobre a mancha"},
            casas={"nivel_regua_cm": 1, "intersecao_sobre_uniao": 3, "fracao_da_agua_dentro_da_mancha": 3, "fracao_da_mancha_coberta_pela_agua": 3}, nota=nota_extra)

    # ---- C4: curva nível × área com as cenas antigas e com as antigas mais as novas
    antigas = pd.read_csv(next(iter(sorted(PROC45.glob("fase-da-cheia-por-cena_*_cena.csv")))))[["sensor", "data_hora_local", "nivel_regua_cm", "a1_km2", "a2_km2"]].assign(conjunto="antiga")
    boas = c2[c2.boa]
    novas = pd.DataFrame({"sensor": boas.sensor, "data_hora_local": boas.data_hora_local, "nivel_regua_cm": boas.regua_cm, "a1_km2": boas.inundacao_em_terra_km2, "a2_km2": boas.agua_total_em_terra_km2, "conjunto": "nova"})
    todas = pd.concat([antigas, novas], ignore_index=True)
    sgb = pd.DataFrame([{"cota_cm": k, "mancha_em_terra_km2": float((mancha[k] & dom).sum() * px_km2), "marca": extraidas.get(k, "")} for k in cotas])
    antes45 = pd.read_csv(next(iter(sorted(PROC45.glob("nivel-equivalente-das-manchas_*_cota.csv")))))
    pontos, equiv, faixas = [], [], []
    for nome, sens in cna.SENSORES.items():
        for d in cna.DEFINICOES:
            col = f"{d.lower()}_km2"
            curvas = {}
            for rot, k in (("antes", antigas[antigas.sensor.isin(sens)]), ("depois", todas[todas.sensor.isin(sens)])):
                xs, ys, n = cna.isotonica(k.nivel_regua_cm.to_numpy(), k[col].to_numpy())
                curvas[rot] = (xs, ys, k)
                pontos += [{"sensor": nome, "definicao": d, "conjunto": rot, "nivel_regua_cm": x, "cenas_no_nivel": int(q), "area_ajustada_km2": float(y)} for x, y, q in zip(xs, ys, n)]
            for r in sgb.itertuples():
                linha = {"cota_cm": r.cota_cm, "mancha_em_terra_km2": r.mancha_em_terra_km2, "sensor": nome, "definicao": d}
                for rot, (xs, ys, k) in curvas.items():
                    nivel, situacao = cna.nivel_em_que_atinge(xs, ys, r.mancha_em_terra_km2)
                    linha.update({f"cenas_{rot}": len(k), f"nivel_equivalente_{rot}_cm": nivel, f"situacao_{rot}": situacao, f"maior_valor_da_curva_{rot}_km2": float(ys[-1])})
                gravado = antes45[(antes45.cota_cm == r.cota_cm) & (antes45.sensor == nome) & (antes45.definicao == d)].nivel_equivalente_cm
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
        AVISOS.append(f"o 'antes' recalculado difere do gravado pela curva nível × área (maior diferença {dif_antes:.4f} cm)")
    gravar(pontos, "curva-nivel-x-area-antes-e-depois_sentinel-ana_2016-2026_ponto", "C4 — Pontos da curva crescente, antes (cenas antigas) e depois (antigas mais novas)", {
        "sensor": "cenas usadas: radar, óptico ou os dois", "definicao": "A1 = água ligada ao rio; A2 = toda a água em terra", "conjunto": "antes ou depois", "nivel_regua_cm": "nível da régua", "cenas_no_nivel": "cenas com esse nível",
        "area_ajustada_km2": "valor da curva crescente"}, casas={"nivel_regua_cm": 1})
    col_eq = {"cota_cm": "cota da mancha acumulada do SGB", "mancha_em_terra_km2": "mancha em terra no domínio", "sensor": "cenas usadas na curva", "definicao": "A1 ou A2", "cenas_antes": "cenas na curva antes", "cenas_depois": "cenas na curva depois",
              "nivel_equivalente_antes_cm": "nível em que a curva antiga chega à área da mancha", "nivel_equivalente_depois_cm": "o mesmo, com as cenas novas", "situacao_antes": "onde a área da mancha cai na curva antiga",
              "situacao_depois": "o mesmo, na curva nova", "maior_valor_da_curva_antes_km2": "valor da curva antiga no maior nível", "maior_valor_da_curva_depois_km2": "valor da curva nova no maior nível",
              "nivel_equivalente_gravado_antes_cm": "o valor de antes como está gravado pela curva nível × área (conferência)", "mudanca_cm": "depois menos antes", "marca": "ressalva sobre a mancha"}
    equiv = equiv[["cota_cm", "mancha_em_terra_km2", "sensor", "definicao", "cenas_antes", "cenas_depois", "nivel_equivalente_antes_cm", "nivel_equivalente_depois_cm", "mudanca_cm", "situacao_antes", "situacao_depois",
                   "maior_valor_da_curva_antes_km2", "maior_valor_da_curva_depois_km2", "nivel_equivalente_gravado_antes_cm", "marca"]]
    gravar(equiv, "nivel-equivalente-das-manchas-antes-e-depois_sentinel-sgb_2016-2026_cota", "C4 — Nível equivalente de cada mancha do SGB, antes e depois das cenas novas", col_eq,
           casas={"nivel_equivalente_antes_cm": 0, "nivel_equivalente_depois_cm": 0, "nivel_equivalente_gravado_antes_cm": 0, "mudanca_cm": 0}, nota=nota_extra, maior_diferenca_do_antes_para_o_gravado_cm=dif_antes)
    gravar(faixas, "cenas-e-curva-por-faixa-de-nivel-antes-e-depois_sentinel-ana_2016-2026_faixa", f"C4 — Cenas e curva por faixa de nível de {ARGS.faixa_cm:g} cm, antes e depois", {
        "sensor": "cenas usadas", "definicao": "A1 ou A2", "faixa_de_nivel_cm": "faixa do nível da régua (limite de baixo incluído)", "cenas_antes": "cenas boas na faixa antes", "cenas_depois": "cenas boas na faixa depois",
        "curva_no_meio_da_faixa_antes_km2": "valor da curva antiga no meio da faixa", "curva_no_meio_da_faixa_depois_km2": "valor da curva nova no meio da faixa", "diferenca_km2": "depois menos antes",
        "mudou": f"a curva mudou mais de {ARGS.mudanca_minima_km2:g} km² no meio da faixa"})

    # ---- D: cena recusada, só para olhar (continua recusada; nada dela vai para a pasta das imagens)
    saida = {"cenas": {"lidas": int(c2.motivo.fillna("").ne("não lida (fora do teto de arquivos ou falha na leitura)").sum()), "boas": c2[c2.boa].groupby("sensor").size().to_dict(),
                       "recusadas": c2[~c2.boa].groupby(["sensor", "motivo"]).size().rename("n").reset_index().to_dict("records")}, "conferencia_do_antes_cm": dif_antes}
    cq = pd.read_csv(PROC44 / "controle-de-qualidade_sentinel_2017-2024_cena.csv")
    for data in ARGS.olhar:
        alvo = cq[(cq.sensor == RADAR) & cq.data_hora_local.str.startswith(data)]
        if alvo.empty or (RADAR, alvo.identificador.iloc[0]) not in brutos:
            AVISOS.append(f"{data}: cena de radar não encontrada para olhar")
            continue
        a = alvo.iloc[0]
        brutos[(RADAR, a.identificador)]["meta"].setdefault("orbita_relativa", int(a.orbita_relativa))
        k = classificar(RADAR, a.identificador)
        rio, fora = k["ref"]["rio"], k["fora_do_leito"]
        col_px = np.arange(forma[1])[None, :].repeat(forma[0], 0)
        tercos = np.quantile(col_px[rio], [1 / 3, 2 / 3])
        trechos = [("terço oeste (jusante)", col_px <= tercos[0]), ("terço do meio", (col_px > tercos[0]) & (col_px <= tercos[1])), ("terço leste (montante)", col_px > tercos[1])]
        vizinhas = cq[(cq.sensor == RADAR) & cq.boa & (cq.orbita_relativa != a.orbita_relativa)].assign(d=lambda t: (pd.to_datetime(t.data_hora_local) - pd.Timestamp(a.data_hora_local)).abs()).sort_values("d").head(1)
        d = {"data": data, "identificador": a.identificador, "orbita_relativa": int(a.orbita_relativa), "nivel_regua_cm": float(a.nivel_regua_cm), "fracao_do_leito_recalculada": k["fracao_do_leito"],
             "fracao_do_leito_gravada": float(a.fracao_do_leito_principal_marcada_como_agua), "leito_principal_km2": float(rio.sum() * px_km2), "leito_nao_marcado_km2": float(fora.sum() * px_km2),
             "por_trecho": [{"trecho": rot, "leito_km2": float((rio & m).sum() * px_km2), "fracao_marcada_como_agua": float(1 - (fora & m).sum() / max((rio & m).sum(), 1)), "vv_mediana_db_na_cena": float(np.nanmedian(k["db"][rio & m])),
                             "vv_mediana_db_na_referencia": float(np.nanmedian(k["ref"]["db"][rio & m]))} for rot, m in trechos],
             "vv_mediana_no_leito_db": float(np.nanmedian(k["db"][rio])), "vv_mediana_no_leito_nao_marcado_db": float(np.nanmedian(k["db"][fora])) if fora.any() else np.nan,
             "pct_do_leito_acima_do_vv_maximo": float(100 * (k["db"][rio] > arg44.vv_max).mean()),
             "se_nao_fosse_recusada": {cam + "_km2": float((k[cam] & k["dominio"]).sum() * px_km2) for cam in ao.CAMADAS} | {"agua_total_em_terra_km2": float((k["agua_total"] & ~leito & k["dominio"]).sum() * px_km2)},
             "gravado_no_controle_de_qualidade": {x: float(a[x]) for x in ("agua_total_km2", "agua_ligada_ao_rio_km2", "inundacao_em_terra_km2")}}
        if len(vizinhas):  # a mesma medida numa cena boa vizinha, para comparar
            v = vizinhas.iloc[0]
            dbv, _ = au.vv_em_db(brutos[(RADAR, v.identificador)]["arquivos"]["vv"], grade)
            d["cena_boa_vizinha"] = {"data": v.data_hora_local[:10], "orbita_relativa": int(v.orbita_relativa), "vv_mediana_no_leito_db": float(np.nanmedian(dbv[rio])), "pct_do_leito_acima_do_vv_maximo": float(100 * (dbv[rio] > arg44.vv_max).mean())}
        saida.setdefault("olhar", []).append(d)
        if ARGS.figuras:
            ARGS.figuras.mkdir(parents=True, exist_ok=True)
            cru = lambda ident: (lambda vv: np.where(np.isfinite(vv) & (vv > 0), 10 * np.log10(np.where(vv > 0, vv, 1)), np.nan))(ao.para_a_grade(brutos[(RADAR, ident)]["arquivos"]["vv"], grade, Resampling.nearest, np.nan))  # noqa: E731
            antes_ = cq[(cq.sensor == RADAR) & cq.boa & (cq.data_hora_local < a.data_hora_local)].sort_values("data_hora_local").iloc[-1]
            depois_ = cq[(cq.sensor == OPTICO) & cq.boa & (cq.data_hora_local > a.data_hora_local)].sort_values("data_hora_local").iloc[0]
            agua_de = lambda sensor, ident: gpd.read_file(PROC44 / f"agua-observada-area-urbana_{sensor}_2017-2024_10m.gpkg", layer="agua_total").pipe(lambda g: g[g.identificador == ident].geometry.iloc[0])  # noqa: E731
            cortes = None
            if ARGS.imagens and (ARGS.imagens / "indice_imagens_sentinel.json").exists():
                cortes = json.loads((ARGS.imagens / "indice_imagens_sentinel.json").read_text(encoding="utf-8"))["cortes_de_esticamento"].get("falsacor-b11b08b03", {}).get("refletancia")
            bo = brutos[(OPTICO, depois_.identificador)]
            versao = next((x.versao_do_processamento for x in pd.read_csv(next(iter(sorted(ao.BRUTO.glob("cenas_planetary-computer_*_cena.csv")))), dtype=str).itertuples() if x.identificador == depois_.identificador), "")
            bandas = [im.refletancia(bo["arquivos"][n], grade, float(versao or 0) >= 4, Resampling.bilinear if n == "b11" else Resampling.nearest) for n in ("b11", "b08", "b03")]
            lim = [tuple(cortes[n.upper()]) for n in ("b11", "b08", "b03")] if cortes else [tuple(np.nanpercentile(v, [2, 98])) for v in bandas]
            rgb = [np.clip((v - lo) / (hi - lo), 0, 1) for v, (lo, hi) in zip(bandas, lim)]
            db_alvo = cru(a.identificador)
            CONTORNOS_ROTULOS[:] = [{"rotulo": f"água da cena boa de {antes_.data_hora_local[:10]} (radar)"}, {"rotulo": f"água da cena boa de {depois_.data_hora_local[:10]} (óptico)"}, {"rotulo": "leito de referência"}]
            contornos = [(agua_de(RADAR, antes_.identificador), "#ffd400", "-"), (agua_de(OPTICO, depois_.identificador), "#00e5ff", "-"), (leito_geom, "#ffffff", (0, (3, 2)))]
            figura_olhar(ARGS.figuras / f"olhar-radar-recusado_{data}.png", [
                {"tipo": "db", "dados": cru(antes_.identificador), "faixa": (-25, 0), "titulo": f"VV em dB — {antes_.data_hora_local[:10]} (boa, órbita {int(antes_.orbita_relativa)}, régua {ao.fmt(float(antes_.nivel_regua_cm), 0)} cm)"},
                {"tipo": "db", "dados": db_alvo, "faixa": (-25, 0), "marca": fora, "titulo": f"VV em dB — {data} (RECUSADA, órbita {int(a.orbita_relativa)}, régua {ao.fmt(float(a.nivel_regua_cm), 0)} cm)"},
                {"tipo": "dif", "dados": db_alvo - cru(k["ref"]["cena"]), "faixa": (-10, 10), "titulo": f"Diferença de VV — {data} menos a referência da órbita ({k['ref']['data']}); −10 dB vermelho, +10 dB azul"},
                {"tipo": "rgb", "dados": rgb, "titulo": f"Falsa cor B11-B08-B03 — {depois_.data_hora_local[:10]} (boa, régua {ao.fmt(float(depois_.nivel_regua_cm), 0)} cm)"}], contornos, ext)
    if saida.get("olhar"):
        (PROC / "cena-recusada-so-para-olhar_sentinel1-rtc_medidas.json").write_text(json.dumps({"script": SCRIPT, "motivo": MOTIVO, "aviso": "a cena continua recusada; medidas só informativas", "cenas": saida["olhar"]}, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    if ARGS.copia:
        ARGS.copia.mkdir(parents=True, exist_ok=True)
        for arq in sorted(PROC.iterdir()):
            shutil.copy2(arq, ARGS.copia / arq.name)
    saida["avisos"] = AVISOS
    print(json.dumps(saida, ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", force=True)
    _p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    _p.add_argument("--etapa", required=True, choices=["regua", "agua"], help="regua: lacunas, episódios novos e lista do que ler; agua: classificação, manchas e curva (depois de ler as cenas)")
    _p.add_argument("--inicio", default="2014-10-01", help="primeiro dia da série a examinar (AAAA-MM-DD)")
    _p.add_argument("--leituras-min", type=int, default=48, help="a média do dia da telemetria só vale com pelo menos estas leituras no dia")
    _p.add_argument("--lacuna-min-dias", type=int, default=5, help="lacunas da série antiga menores que isto não entram na tabela")
    _p.add_argument("--limiar-cm", type=float, default=800.0, help="nível a partir do qual o dia é de cheia (cm)")
    _p.add_argument("--intervalo-dias", type=int, default=3, help="trechos separados por até estes dias abaixo do limiar contam como um episódio")
    _p.add_argument("--folga-dias", type=int, default=5, help="dias antes e depois de cada episódio que entram na busca")
    _p.add_argument("--margem-m", type=float, default=500.0, help="margem em volta do retângulo da área urbana, na busca (m)")
    _p.add_argument("--cobertura-min", type=float, default=95.0, help="triagem: cobertura mínima do domínio pela pegada (%%)")
    _p.add_argument("--nuvem-max", type=float, default=30.0, help="triagem do óptico: nuvem declarada da quadrícula abaixo disto (%%)")
    _p.add_argument("--nuvem-preferir-radar", type=float, default=10.0, help="na lista curta, no empate de dias, o radar passa à frente do óptico com nuvem da quadrícula a partir disto (%%)")
    _p.add_argument("--tentativas", type=int, default=4, help="pedidos ao catálogo: a primeira tentativa e as repetições com espera")
    _p.add_argument("--excluir", nargs="*", default=[], metavar="SENSOR:AAAA-MM-DD", help="passagens da lista curta do inventário que não são lidas (radar:DATA ou optico:DATA, dia local)")
    _p.add_argument("--episodios-primeiro", type=int, nargs="*", default=[], help="episódios com prioridade quando o teto de arquivos não bastar")
    _p.add_argument("--teto-de-arquivos", type=int, default=130, help="teto de arquivos brutos novos")
    _p.add_argument("--ativos-radar", nargs="+", default=["vv", "vh"], help="arquivos por passagem de radar")
    _p.add_argument("--ativos-optico", nargs="+", default=["SCL", "B02", "B03", "B04", "B08", "B11"], help="arquivos por cena óptica")
    _p.add_argument("--mancha-extra", nargs="*", help="COTA=ARQUIVO.gpkg:CAMADA de mancha acumulada de fora do repositório (extraída de figura, não conferida): só leitura")
    _p.add_argument("--faixa-inicio", type=float, default=800.0, help="início das faixas de nível (cm)")
    _p.add_argument("--faixa-fim", type=float, default=1300.0, help="fim das faixas de nível (cm)")
    _p.add_argument("--faixa-cm", type=float, default=50.0, help="largura das faixas de nível (cm)")
    _p.add_argument("--mudanca-minima-km2", type=float, default=0.05, help="a curva 'mudou' numa faixa se o valor no meio dela variar mais que isto (km²)")
    _p.add_argument("--olhar", nargs="*", default=[], metavar="AAAA-MM-DD", help="cenas de radar recusadas só para olhar: figura e a área que a regra daria (continuam recusadas)")
    _p.add_argument("--imagens", type=Path, help="pasta das imagens para SIG, só para ler os cortes da falsa cor usados na figura de --olhar")
    _p.add_argument("--figuras", type=Path, help="pasta (fora do repositório) das figuras")
    _p.add_argument("--copia", type=Path, help="pasta que recebe uma cópia dos produtos")
    ARGS = _p.parse_args()
    FONTES, AVISOS = [], []
    for _pasta in (ARGS.figuras, ARGS.copia):
        if _pasta is not None and c.RAIZ in _pasta.resolve().parents:
            raise SystemExit("--figuras e --copia têm de ficar fora do repositório")
    PROC.mkdir(parents=True, exist_ok=True)
    (etapa_regua if ARGS.etapa == "regua" else etapa_agua)()
