"""
Inventário das passagens Sentinel nos dias de cheia, para escolher o que baixar.

SÓ CONSULTA AO CATÁLOGO: a busca STAC do Microsoft Planetary Computer (data, hora,
órbita, pegada e nuvem declarada da quadrícula). Nenhum arquivo de imagem é lido,
nem por janela, e nenhum token de leitura é pedido.

  A  episódios de cheia na série diária da régua (média diária BRUTA; onde ela
     falta, a consistida, marcada): sequências de dias com nível igual ou maior
     que --limiar-cm; trechos separados por até --intervalo-dias dias abaixo do
     limiar contam como um episódio. À parte, os episódios com pico entre
     --limiar-menor-cm e --limiar-cm.
  B  passagens das coleções sentinel-1-rtc e sentinel-2-l2a que tocam o domínio
     (o retângulo da área urbana de agua_observada_sentinel_area_urbana.py, com
     --margem-m) nos dias de cada episódio e --folga-dias antes e depois. Radar:
     as fatias da mesma passagem são somadas. Óptico: uma linha por imageamento
     (a quadrícula de maior cobertura do domínio e o processamento mais recente).
     Triagem "provável": radar com cobertura >= --cobertura-min %; óptico com
     isso e nuvem declarada da quadrícula < --nuvem-max %.
  C  resumos para a decisão: por episódio, por faixa de nível, custo estimado de
     baixar as prováveis novas e uma lista curta sugerida. Nada é baixado.

Saídas em data/processed/inventario_sentinel_cheias/ (fora do git); a figura só
em --figuras (fora do repositório).

Uso:
  python scripts/download/sentinel_inventario_cheias.py [--figuras PASTA] [--copia PASTA]
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
from matplotlib.patches import Patch  # noqa: E402
from shapely.geometry import box, shape  # noqa: E402
from shapely.ops import unary_union  # noqa: E402

sys.path.append(str(Path(__file__).resolve().parents[1] / "processamento"))
import agua_observada_sentinel as ao  # noqa: E402
import agua_observada_sentinel_area_urbana as au  # noqa: E402
import agua_observada_sentinel_curva_nivel_area as cna  # noqa: E402
import dinamica_populacional_comum as c  # noqa: E402
import sentinel_planetary_computer as spc  # noqa: E402

logger = logging.getLogger(__name__)

SCRIPT = "scripts/download/sentinel_inventario_cheias.py"
MOTIVO = "inventário das passagens Sentinel nos dias de cheia, para escolher o que baixar"
PROC = c.RAIZ / "data" / "processed" / "inventario_sentinel_cheias"
PROC44 = au.PROC
RADAR, OPTICO = au.RADAR, au.OPTICO
NOME = {RADAR: "radar", OPTICO: "óptico"}
COLECAO_GRD = "sentinel-1-grd"  # só para o período anterior ao início da coleção RTC, se houver
ATIVOS_A_BAIXAR = {RADAR: ["vv", "vh"], OPTICO: ["b02", "b03", "b04", "b08", "b11", "scl"]}
COR = {RADAR: "#2a78d6", OPTICO: "#d95f02"}
LIMITACOES = ["a nuvem do óptico é a declarada para a quadrícula inteira (cerca de 110 km de lado), não a da área urbana: pode haver nuvem sobre a cidade com a quadrícula limpa, e o contrário",
              "a cobertura é a da pegada do catálogo sobre o domínio; a cena pode ter faixas sem dado dentro da pegada",
              "'provável' é só triagem: o controle de qualidade (contraste do rio no radar, nuvem e sombra no óptico) só existe depois de baixar",
              "o nível é a média diária da régua no dia local da passagem, não o nível na hora da cena",
              "a fase da cheia e o pico seguem a regra de três dias antes e três depois, que pega também oscilações pequenas"]


# ---------------------------------------------------------------- gravação
def rel(p: Path) -> str:
    return str(Path(p).relative_to(c.RAIZ))


def meta(caminho: Path, **kw) -> None:
    c.gravar_meta(caminho, status=c.STATUS_CONFERENCIA, ligado_ao_portal=False, script=SCRIPT, motivo=MOTIVO, fontes=FONTES, licencas_declaradas_no_catalogo=LICENCAS, data_da_consulta=CONSULTA,
                  fora_do_git="data/processed/ é ignorado; produto derivado, para decisão", limitacoes=LIMITACOES, avisos=AVISOS or None,
                  argumentos={k: v for k, v in vars(ARGS).items() if k not in ("figuras", "copia")}, **kw)


def gravar(t: pd.DataFrame, nome: str, titulo: str, colunas: dict, nota: str = "", casas: dict | None = None, **kw) -> Path:
    """Tabela: .csv (sem arredondar) + .md (leitura) + .json com a descrição de cada coluna."""
    faltam = [x for x in t.columns if x not in colunas]
    if faltam:
        raise KeyError(f"{nome}: coluna sem descrição: {faltam}")
    casas = casas or {}
    arq = PROC / f"{nome}.csv"
    t.to_csv(arq, index=False)
    lin = [f"**{titulo}**", "", "| " + " | ".join(t.columns) + " |", "|" + "|".join("---:" if pd.api.types.is_numeric_dtype(t[x]) and not pd.api.types.is_bool_dtype(t[x]) else "---" for x in t.columns) + "|"]
    lin += ["| " + " | ".join(ao.fmt(v, casas.get(col, 1)) for col, v in zip(t.columns, r)) + " |" for r in t.itertuples(index=False)]
    arq.with_suffix(".md").write_text("\n".join(lin + (["", nota] if nota else []) + [""]), encoding="utf-8")
    meta(arq, titulo=titulo, nota=nota or None, colunas={k: colunas[k] for k in t.columns}, **kw)
    logger.info("Tabela: %s (%d linhas)", rel(arq), len(t))
    return arq


# ---------------------------------------------------------------- régua
def episodios(s: pd.Series, limiar: float, intervalo: int) -> pd.DataFrame:
    """Sequências de dias com nível >= limiar; trechos separados por até `intervalo` dias abaixo dele são um episódio só."""
    dias = s.index[s >= limiar]
    grupos, atual = [], []
    for d in dias:
        if atual and (d - atual[-1]).days - 1 > intervalo:
            grupos.append(atual)
            atual = []
        atual.append(d)
    if atual:
        grupos.append(atual)
    out = []
    for g in grupos:
        trecho = s[g[0]:g[-1]]
        out.append({"inicio": g[0], "fim": g[-1], "duracao_dias": (g[-1] - g[0]).days + 1, "dias_no_limiar_ou_acima": len(g), "data_do_pico": trecho.idxmax(), "pico_cm": float(trecho.max())})
    return pd.DataFrame(out)


# ---------------------------------------------------------------- catálogo
def colecao(nome: str) -> dict:
    """Metadados da coleção (sem token): extensão temporal declarada e licença."""
    r = spc.pedir("GET", f"{spc.STAC}/collections/{nome}", tentativas=ARGS.tentativas, timeout=60)
    r.raise_for_status()
    j = r.json()
    ini, fim = j["extent"]["temporal"]["interval"][0]
    return {"colecao": nome, "titulo": j.get("title"), "inicio_declarado": ini, "fim_declarado": fim or "em aberto", "licenca": j.get("license"),
            "endereco_da_licenca": [x["href"] for x in j.get("links", []) if x.get("rel") == "license"]}


def buscar(nome: str, ini: pd.Timestamp, fim: pd.Timestamp, bbox: tuple) -> list[dict]:
    """Itens da coleção que tocam o retângulo entre as duas datas (UTC), com paginação; só a busca do catálogo."""
    corpo = {"collections": [nome], "bbox": list(bbox), "datetime": f"{ini:%Y-%m-%d}T00:00:00Z/{fim:%Y-%m-%d}T23:59:59Z", "limit": 200}
    url, itens = f"{spc.STAC}/search", []
    while True:
        r = spc.pedir("POST", url, tentativas=ARGS.tentativas, json=corpo, timeout=120)
        r.raise_for_status()
        j = r.json()
        itens += j.get("features", [])
        seguinte = [x for x in j.get("links", []) if x.get("rel") == "next"]
        if not seguinte:
            return itens
        url, corpo = seguinte[0]["href"], seguinte[0].get("body", corpo)


def passagens(itens: list[dict], sensor: str, nome_colecao: str, dominio, dominio_proj) -> list[dict]:
    """Itens do catálogo -> uma linha por passagem, com a pegada recortada no domínio (CRS do projeto)."""
    grupos: dict[tuple, list[dict]] = {}
    for i in {i["id"]: i for i in itens}.values():
        p = i["properties"]
        chave = (p.get("platform"), p.get("sat:absolute_orbit")) if sensor == RADAR else (p.get("platform"), p["datetime"][:16])
        grupos.setdefault(chave, []).append(i)
    out = []
    for grupo in grupos.values():
        pegadas = gpd.GeoSeries([shape(i["geometry"]).buffer(0) for i in grupo], crs="EPSG:4326")
        toca = [k for k, g in enumerate(pegadas) if g.intersects(dominio)]
        if not toca:
            continue
        # a cobertura é medida em metros: as pegadas vêm em graus e vão para o CRS do projeto antes de qualquer conta
        proj = pegadas.to_crs(c.CRS_PADRAO).buffer(0).intersection(dominio_proj)
        if sensor == RADAR:
            usar, geom = toca, unary_union([proj.iloc[k] for k in toca])
        else:  # a quadrícula que mais cobre o domínio; entre processamentos do mesmo imageamento, o mais recente
            melhor = max(toca, key=lambda k: (round(proj.iloc[k].area, 0), grupo[k]["id"]))
            usar, geom = [melhor], proj.iloc[melhor]
        itens_usados = sorted((grupo[k] for k in usar), key=lambda i: i["properties"]["datetime"])
        p = itens_usados[0]["properties"]
        quando = datetime.fromisoformat(p["datetime"].replace("Z", "+00:00"))
        local = quando.astimezone(spc.FUSO_LOCAL)
        plataforma = (p.get("platform") or "").upper().replace("SENTINEL-", "S")
        out.append({"sensor": sensor, "colecao": nome_colecao, "data_hora_utc": f"{quando:%Y-%m-%d %H:%M:%S}", "data_hora_local": f"{local:%Y-%m-%d %H:%M:%S}", "data_local": pd.Timestamp(local.date()),
                    "plataforma": plataforma, "orbita_relativa": p.get("sat:relative_orbit"), "direcao": p.get("sat:orbit_state"), "quadricula": p.get("s2:mgrs_tile", "") if sensor == OPTICO else "",
                    "itens_no_catalogo": len(grupo), "itens_que_tocam_o_dominio": len(toca), "cobertura_do_dominio_pct": float(100 * geom.area / dominio_proj.area),
                    "nuvem_da_quadricula_pct": p.get("eo:cloud_cover", np.nan) if sensor == OPTICO else np.nan, "identificadores": "; ".join(i["id"] for i in itens_usados), "geometry": geom})
    return out


# ---------------------------------------------------------------- figura
def figura(arq: Path, s: pd.Series, ep: pd.DataFrame, janelas: dict, pas: pd.DataFrame) -> None:
    fig, ax = plt.subplots(figsize=(17, 7.2))
    topo = float(np.nanmax(s)) * 1.04
    for r in ep.itertuples():
        ax.axvspan(r.inicio - pd.Timedelta(days=1), r.fim + pd.Timedelta(days=1), color="#f0c9a8", alpha=0.75, lw=0, zorder=1)
        ax.annotate(str(r.episodio), (r.data_do_pico, r.pico_cm), textcoords="offset points", xytext=(0, 4), ha="center", fontsize=7, color="#0b0b0b")
    ax.plot(s.index, s.values, color="#333333", lw=0.7, zorder=3)
    ax.axhline(ARGS.limiar_cm, color="#d03b3b", lw=0.9, ls="--", zorder=2)
    for nome, periodos in janelas.items():
        for ini, fim in periodos:
            ax.plot([ini, fim], [topo, topo], color="#008300", lw=5, solid_capstyle="butt", zorder=4)
            ax.annotate(nome, (ini + (fim - ini) / 2, topo), textcoords="offset points", xytext=(0, 5), ha="center", fontsize=7.5, color="#0b0b0b")
    prov = pas[pas.provavel]
    for sensor, y in ((RADAR, -70), (OPTICO, -160)):
        for tem, cheio in ((True, True), (False, False)):
            k = prov[(prov.sensor == sensor) & (prov.ja_temos == tem)]
            ax.scatter(k.data_local, np.full(len(k), y), marker="|" if not cheio else "s", s=70 if not cheio else 16, color=COR[sensor], linewidth=0.9, zorder=5)
    ax.set_ylim(-220, topo * 1.06)
    ax.set_xlim(pd.Timestamp(ARGS.inicio) - pd.Timedelta(days=30), s.index.max() + pd.Timedelta(days=30))
    ax.set_ylabel("nível da régua, média diária (cm)", fontsize=9)
    ax.set_yticks(np.arange(0, topo, 200))
    ax.grid(axis="y", color="#e3e2dc", lw=0.6)
    for lado in ("top", "right"):
        ax.spines[lado].set_visible(False)
    ax.tick_params(labelsize=8, colors="#52514e")
    ax.xaxis.set_major_locator(matplotlib.dates.YearLocator())
    ax.set_title("Régua, episódios de cheia e passagens Sentinel prováveis", fontsize=11, loc="left")
    ax.legend(handles=[Line2D([], [], color="#333333", lw=1, label="média diária da régua"), Line2D([], [], color="#d03b3b", lw=1, ls="--", label=f"{ARGS.limiar_cm:g} cm"),
                       Patch(facecolor="#f0c9a8", label="episódio de cheia (número no pico)"), Line2D([], [], color="#008300", lw=5, label="janelas já usadas"),
                       Line2D([], [], marker="s", ls="", color=COR[RADAR], markersize=5, label="radar provável: já temos (quadrado cheio)"), Line2D([], [], marker="|", ls="", color=COR[RADAR], markersize=10, label="radar provável: nova (traço)"),
                       Line2D([], [], marker="s", ls="", color=COR[OPTICO], markersize=5, label="óptico provável: já temos (quadrado cheio)"), Line2D([], [], marker="|", ls="", color=COR[OPTICO], markersize=10, label="óptico provável: nova (traço)")],
              loc="lower center", bbox_to_anchor=(0.5, -0.2), ncol=4, fontsize=8, frameon=False)
    fig.text(0.01, 0.01, "Linha de cima das marcas: radar; linha de baixo: óptico. 'Provável' é triagem pelo catálogo (cobertura da pegada e nuvem da quadrícula inteira). Produto derivado, para decisão.", fontsize=7, color="#555555")
    fig.tight_layout(rect=(0, 0.02, 1, 1))
    fig.savefig(arq, dpi=200)
    plt.close(fig)


# ---------------------------------------------------------------- principal
def main() -> None:
    global FONTES, LICENCAS, CONSULTA
    if PROC.exists() and any(PROC.iterdir()) and not ARGS.refazer:
        raise SystemExit(f"{rel(PROC)} já tem arquivos (use --refazer para gerar de novo)")
    PROC.mkdir(parents=True, exist_ok=True)

    # ---- série: média diária bruta; onde falta, a consistida (marcada)
    diaria = ao.ler_serie_diaria()
    if diaria is None:
        raise FileNotFoundError("série diária da régua ausente: rode scripts/download/nivel_rio_ana.py")
    d = diaria[pd.Timestamp(ARGS.inicio):].copy()
    d["nivel_cm"] = d.media_bruta_cm.where(d.media_bruta_cm.notna(), d.media_consistida_cm)
    d["origem"] = np.where(d.media_bruta_cm.notna(), "bruta", np.where(d.media_consistida_cm.notna(), "consistida", ""))
    s, origem = d.nivel_cm.asfreq("D"), d.origem
    ultima = s.dropna().index.max()
    pico_dia = s.notna() & (s == s.rolling(7, center=True, min_periods=1).max())  # a mesma regra de pico da curva nível × área
    info_serie = {"arquivo": diaria.attrs.get("arquivo"), "inicio_da_analise": ARGS.inicio, "ultima_data": f"{ultima:%Y-%m-%d}", "dias_com_valor": int(s.notna().sum()), "dias_sem_valor": int(s.isna().sum()),
                  "dias_com_a_consistida_no_lugar_da_bruta": int((origem == "consistida").sum()), "maximo_cm": float(s.max()), "data_do_maximo": f"{s.idxmax():%Y-%m-%d}"}

    # ---- janelas já usadas: as do download que aparecem entre as cenas do controle de qualidade da área urbana
    cq = pd.read_csv(PROC44 / "controle-de-qualidade_sentinel_2017-2024_cena.csv")
    m_cenas = json.loads(next(iter(sorted(ao.BRUTO.glob("cenas_planetary-computer_*_cena.json")))).read_text(encoding="utf-8"))
    janelas = {k: [tuple(pd.Timestamp(x) for x in p.split("/")) for p in v] for k, v in sorted(m_cenas["janelas"].items()) if k in set(cq.janela)}

    def nas_janelas(ini, fim) -> str:
        return ", ".join(k for k, ps in janelas.items() if any(a <= fim and ini <= b for a, b in ps))

    # ---- Parte A
    ep = episodios(s, ARGS.limiar_cm, ARGS.intervalo_dias)
    ep.insert(0, "episodio", range(1, len(ep) + 1))
    ep["janelas_ja_usadas"] = [nas_janelas(r.inicio, r.fim) for r in ep.itertuples()]
    ep["inteiro_dentro_de_janela"] = [any(a <= r.inicio and r.fim <= b for ps in janelas.values() for a, b in ps) for r in ep.itertuples()]
    ep["pico_com_a_consistida"] = [origem.get(x, "") == "consistida" for x in ep.data_do_pico]
    menores = episodios(s, ARGS.limiar_menor_cm, ARGS.intervalo_dias)
    menores = menores[menores.pico_cm < ARGS.limiar_cm].reset_index(drop=True)
    menores.insert(0, "episodio_menor", range(1, len(menores) + 1))
    menores["janelas_ja_usadas"] = [nas_janelas(r.inicio, r.fim) for r in menores.itertuples()]
    dia = lambda t, cols: t.assign(**{k: t[k].dt.strftime("%Y-%m-%d") for k in cols})  # noqa: E731

    # ---- Parte B: só a busca do catálogo
    urbano = gpd.read_file(PROC44 / "area-urbana_sgb_2023_retangulo.gpkg", layer="area_urbana").to_crs(c.CRS_PADRAO).geometry.iloc[0]
    dominio_proj = box(*urbano.bounds).buffer(ARGS.margem_m, join_style="mitre")  # retângulo com margem, no CRS do projeto (metros)
    dominio = gpd.GeoSeries([dominio_proj], crs=c.CRS_PADRAO).to_crs("EPSG:4326").iloc[0]  # em graus só para a busca e para cruzar com as pegadas
    CONSULTA = datetime.now(timezone.utc).isoformat(timespec="seconds")
    cols = {RADAR: colecao(spc.SENSORES[RADAR]["colecao"]), OPTICO: colecao(spc.SENSORES[OPTICO]["colecao"])}
    LICENCAS = {k: {x: v[x] for x in ("colecao", "licenca", "endereco_da_licenca")} for k, v in cols.items()}
    FONTES = ["Microsoft Planetary Computer — catálogo STAC, coleções " + " e ".join(v["colecao"] for v in cols.values()) + " (dados Copernicus Sentinel); só metadados",
              "ANA — série diária da estação fluviométrica (média diária bruta)"]
    inicio_rtc = pd.Timestamp(cols[RADAR]["inicio_declarado"]).tz_localize(None)
    usar_grd = inicio_rtc > pd.Timestamp(ARGS.inicio)
    if usar_grd:
        cols["grd"] = colecao(COLECAO_GRD)
    linhas = []
    for r in ep.itertuples():
        ini, fim = r.inicio - pd.Timedelta(days=ARGS.folga_dias), r.fim + pd.Timedelta(days=ARGS.folga_dias)
        achadas = []
        for sensor in (RADAR, OPTICO):
            nome_col = cols[sensor]["colecao"]
            itens = buscar(nome_col, ini, fim + pd.Timedelta(days=1), dominio.bounds)  # um dia a mais em UTC: o dia local termina às 3h UTC do seguinte
            achadas += passagens(itens, sensor, nome_col, dominio, dominio_proj)
            if sensor == RADAR and usar_grd and ini < inicio_rtc:  # período anterior à coleção RTC: a GRD, só para dizer o que existe
                antes = buscar(COLECAO_GRD, ini, min(fim + pd.Timedelta(days=1), inicio_rtc), dominio.bounds)
                achadas += passagens(antes, RADAR, COLECAO_GRD, dominio, dominio_proj)
        achadas = [x for x in achadas if ini <= x["data_local"] <= fim]
        logger.info("Episódio %d (%s a %s): %d passagens", r.episodio, f"{ini:%Y-%m-%d}", f"{fim:%Y-%m-%d}", len(achadas))
        linhas += [{"episodio": r.episodio, **x} for x in achadas]
    pas = pd.DataFrame(linhas).sort_values(["data_hora_utc", "sensor"]).reset_index(drop=True)
    pico = ep.set_index("episodio")
    pas["nivel_regua_cm"] = [s.get(x, np.nan) for x in pas.data_local]
    pas["origem_do_nivel"] = [origem.get(x, "") for x in pas.data_local]
    pas["fase"] = [cna.fase_da_cheia(x, s, pico_dia)["fase"] for x in pas.data_local]
    pas["dias_ate_o_pico"] = [(x - pico.data_do_pico[e]).days for x, e in zip(pas.data_local, pas.episodio)]
    pas["cm_abaixo_do_pico"] = [pico.pico_cm[e] - n for n, e in zip(pas.nivel_regua_cm, pas.episodio)]
    situacao = {i: ("boa" if b else f"recusada: {m}") for ids, b, m in zip(cq.identificador, cq.boa, cq.motivo.fillna("")) for i in ids.split("; ")}
    pas["situacao_no_controle_de_qualidade"] = [next((situacao[i] for i in ids.split("; ") if i in situacao), "") for ids in pas.identificadores]
    pas["ja_temos"] = pas.situacao_no_controle_de_qualidade != ""
    pas["provavel"] = (pas.cobertura_do_dominio_pct >= ARGS.cobertura_min) & ((pas.sensor == RADAR) | (pas.nuvem_da_quadricula_pct < ARGS.nuvem_max)) & (pas.colecao != COLECAO_GRD)
    sem_par = sorted(set(situacao) - {i for ids in pas.identificadores for i in ids.split("; ")})
    conhecidas = int(pas.ja_temos.sum())

    col_ep = {"episodio": "número do episódio, em ordem de data", "inicio": "primeiro dia no limiar ou acima", "fim": "último dia no limiar ou acima", "duracao_dias": "dias do início ao fim, contando os dois",
              "dias_no_limiar_ou_acima": "dias do episódio com nível no limiar ou acima", "data_do_pico": "dia do maior nível", "pico_cm": "maior média diária", "janelas_ja_usadas": "janelas já usadas que tocam o episódio",
              "inteiro_dentro_de_janela": "o episódio cabe inteiro numa janela já usada", "pico_com_a_consistida": "o pico vem da série consistida (falta a bruta)", "episodio_menor": "número do episódio menor, em ordem de data"}
    gravar(dia(ep, ["inicio", "fim", "data_do_pico"]), "episodios-de-cheia_ana_2014-2026_episodio", f"A2 — Episódios de cheia (média diária ≥ {ARGS.limiar_cm:g} cm)", col_ep, serie=info_serie,
           janelas_ja_usadas={k: [f"{a:%Y-%m-%d}/{b:%Y-%m-%d}" for a, b in v] for k, v in janelas.items()}, regra=f"trechos separados por até {ARGS.intervalo_dias} dias abaixo do limiar contam como um episódio")
    gravar(dia(menores, ["inicio", "fim", "data_do_pico"]), "episodios-menores_ana_2014-2026_episodio", f"A3 — Episódios com pico entre {ARGS.limiar_menor_cm:g} e {ARGS.limiar_cm:g} cm", col_ep, serie=info_serie)

    col_b = {"episodio": col_ep["episodio"], "sensor": "sensor e produto", "colecao": "coleção do catálogo", "data_hora_utc": "início da passagem, UTC", "data_hora_local": "o mesmo, em UTC−3", "data_local": "dia local",
             "plataforma": "satélite", "orbita_relativa": "órbita relativa", "direcao": "direção da órbita", "quadricula": "quadrícula do óptico usada na linha", "itens_no_catalogo": "itens da passagem no catálogo (fatias do radar; quadrículas e processamentos do óptico)",
             "itens_que_tocam_o_dominio": "dos quais, com pegada que toca o domínio", "cobertura_do_dominio_pct": "% do domínio coberta pela pegada (radar: fatias somadas; óptico: a quadrícula que mais cobre)",
             "nuvem_da_quadricula_pct": "nuvem declarada no catálogo para a quadrícula INTEIRA (não é a da área urbana)", "identificadores": "identificadores dos itens usados na linha", "nivel_regua_cm": "média diária da régua no dia local",
             "origem_do_nivel": "série bruta ou consistida", "fase": "subida, descida ou pico (regra da curva nível × área)", "dias_ate_o_pico": "dia da passagem menos o dia do pico do episódio", "cm_abaixo_do_pico": "pico do episódio menos o nível do dia",
             "situacao_no_controle_de_qualidade": "se a cena já está entre as do controle de qualidade da área urbana: boa ou recusada (vazio = não está)", "ja_temos": "a cena está entre as do controle de qualidade",
             "provavel": f"triagem: cobertura ≥ {ARGS.cobertura_min:g} % e, no óptico, nuvem da quadrícula < {ARGS.nuvem_max:g} %"}
    tab_b = dia(pas.drop(columns="geometry"), ["data_local"])
    gravar(tab_b, "passagens-nos-episodios_planetary-computer_2014-2026_passagem", "B3 — Passagens Sentinel que tocam o domínio nos episódios de cheia", col_b, colecoes=cols,
           dominio={"crs": c.CRS_PADRAO, "limites": list(dominio_proj.bounds), "margem_m": ARGS.margem_m, "limites_geograficos_da_busca": list(dominio.bounds)},
           cenas_do_controle_de_qualidade={"total": int(len(cq)), "achadas_entre_as_passagens": conhecidas, "itens_sem_par_no_inventario": len(sem_par)})
    arq_g = PROC / "pegadas-das-passagens_planetary-computer_2014-2026_vetorial.gpkg"
    g = gpd.GeoDataFrame(tab_b, geometry=pas.geometry.values, crs=c.CRS_PADRAO)
    g[~g.geometry.is_empty].to_file(arq_g, driver="GPKG", layer="pegadas_no_dominio")
    meta(arq_g, crs=c.CRS_PADRAO, descricao="pegada de cada passagem (catálogo) recortada no domínio; radar: união das fatias", camadas=["pegadas_no_dominio"], colunas=col_b)

    # ---- Parte C
    prov = pas[pas.provavel]
    perto = lambda t: t.assign(_d=t.dias_ate_o_pico.abs(), _s=np.where((t.sensor == OPTICO) & (t.nuvem_da_quadricula_pct >= ARGS.nuvem_preferir_radar), 1, 0)).sort_values(["_d", "_s", "cm_abaixo_do_pico"])  # noqa: E731
    c1 = []
    for r in ep.itertuples():
        k = prov[prov.episodio == r.episodio]
        linha = {"episodio": r.episodio, "data_do_pico": f"{r.data_do_pico:%Y-%m-%d}", "pico_cm": r.pico_cm, "janelas_ja_usadas": r.janelas_ja_usadas, "passagens": int((pas.episodio == r.episodio).sum()),
                 "provaveis_de_radar": int((k.sensor == RADAR).sum()), "provaveis_de_optico": int((k.sensor == OPTICO).sum()), "provaveis_novas": int((~k.ja_temos).sum())}
        if len(k):
            m = perto(k).iloc[0]
            linha.update(mais_perto_data=m.data_hora_local[:10], mais_perto_sensor=NOME[m.sensor], mais_perto_nivel_cm=m.nivel_regua_cm, mais_perto_dias_ate_o_pico=int(m.dias_ate_o_pico), mais_perto_cm_abaixo_do_pico=m.cm_abaixo_do_pico,
                         mais_perto_e_nova=not m.ja_temos)
        c1.append(linha)
    c1 = pd.DataFrame(c1)
    gravar(c1, "resumo-por-episodio_planetary-computer_2014-2026_episodio", "C1 — Passagens prováveis por episódio", {
        **col_ep, "passagens": "passagens que tocam o domínio no episódio e na folga", "provaveis_de_radar": "passagens prováveis de radar", "provaveis_de_optico": "passagens prováveis de óptico",
        "provaveis_novas": "prováveis que não estão entre as cenas do controle de qualidade", "mais_perto_data": "passagem provável mais perto do pico: dia local", "mais_perto_sensor": "idem: sensor",
        "mais_perto_nivel_cm": "idem: nível da régua no dia", "mais_perto_dias_ate_o_pico": "idem: dias até o pico (negativo = antes)", "mais_perto_cm_abaixo_do_pico": "idem: pico menos o nível do dia",
        "mais_perto_e_nova": "idem: não está entre as cenas do controle de qualidade"})
    topo = int(np.ceil((ep.pico_cm.max() + 1) / ARGS.faixa_cm) * ARGS.faixa_cm) if len(ep) else int(ARGS.limiar_cm)
    c2 = []
    for lo in [None, *range(int(ARGS.limiar_cm), topo, int(ARGS.faixa_cm))]:
        k = prov[prov.nivel_regua_cm < ARGS.limiar_cm] if lo is None else prov[(prov.nivel_regua_cm >= lo) & (prov.nivel_regua_cm < lo + ARGS.faixa_cm)]
        c2.append({"faixa_de_nivel_cm": f"abaixo de {ARGS.limiar_cm:g} (dias de folga)" if lo is None else f"{lo}–{lo + int(ARGS.faixa_cm)}", **{f"{NOME[sn]}_{rot}".replace("óptico", "optico"): int(((k.sensor == sn) & (k.ja_temos == tem)).sum())
                   for sn in (RADAR, OPTICO) for rot, tem in (("ja_temos", True), ("novas", False))}, "total_ja_temos": int(k.ja_temos.sum()), "total_novas": int((~k.ja_temos).sum())})
    c2 = pd.DataFrame(c2)
    gravar(c2, "provaveis-por-faixa-de-nivel_planetary-computer_2014-2026_faixa", f"C2 — Passagens prováveis por faixa de nível de {ARGS.faixa_cm:g} cm", {
        "faixa_de_nivel_cm": "faixa da média diária no dia da passagem (limite de baixo incluído)", "radar_ja_temos": "radar: já entre as cenas do controle de qualidade", "radar_novas": "radar: novas",
        "optico_ja_temos": "óptico: já entre as cenas do controle de qualidade", "optico_novas": "óptico: novas", "total_ja_temos": "soma das que já temos", "total_novas": "soma das novas"})

    # custo: tamanho médio por arquivo das janelas da área urbana já gravadas (lê só o tamanho dos arquivos)
    def retangulo_pequeno(a: Path) -> bool:
        """O arquivo bruto foi lido na janela da área urbana (e não na da área de estudo inteira), pelo retângulo gravado no .json dele."""
        m = json.loads(a.with_suffix(".json").read_text(encoding="utf-8"))
        lim = (m.get("retangulo_lido") or m.get("retangulo_da_area_de_estudo") or {}).get("limites")
        return bool(lim) and (lim[2] - lim[0]) * (lim[3] - lim[1]) < ARGS.folga_da_janela * dominio_proj.area

    tam = {}
    for ativo in sorted({x for v in ATIVOS_A_BAIXAR.values() for x in v}):
        todos = [a for a in ao.BRUTO.glob(f"*/*-{ativo}_*.tif") if a.with_suffix(".json").exists()]
        peq = [a.stat().st_size for a in todos if retangulo_pequeno(a)]
        tam[ativo] = {"na_janela_da_area_urbana": peq, "na_janela_inteira": [a.stat().st_size for a in todos if not retangulo_pequeno(a)]}
    media = lambda v: float(np.mean(v)) if len(v) else np.nan  # noqa: E731
    base = next((a for a in ("b02", "b04") if tam[a]["na_janela_da_area_urbana"]), None)
    c3 = []
    for sensor, ativos in ATIVOS_A_BAIXAR.items():
        n = int(((prov.sensor == sensor) & ~prov.ja_temos).sum())
        for ativo in ativos:
            mb, como = media(tam[ativo]["na_janela_da_area_urbana"]) / 1e6, f"média de {len(tam[ativo]['na_janela_da_area_urbana'])} arquivos já gravados na janela da área urbana"
            if np.isnan(mb) and base and tam[ativo]["na_janela_inteira"] and tam["b03"]["na_janela_inteira"]:  # banda sem arquivo na janela pequena: proporção da janela inteira
                mb = media(tam[base]["na_janela_da_area_urbana"]) / 1e6 * media(tam[ativo]["na_janela_inteira"]) / media(tam["b03"]["na_janela_inteira"])
                como = f"estimado: tamanho de {base.upper()} na janela da área urbana × razão {ativo.upper()}/B03 na janela inteira"
            c3.append({"sensor": NOME[sensor], "ativo": ativo.upper(), "passagens_provaveis_novas": n, "arquivos": n, "mb_por_arquivo": mb, "mb": n * mb, "como_foi_estimado": como})
    c3 = pd.DataFrame(c3)
    c3 = pd.concat([c3, pd.DataFrame([{"sensor": "total", "ativo": "", "passagens_provaveis_novas": int((~prov.ja_temos).sum()), "arquivos": int(c3.arquivos.sum()), "mb_por_arquivo": np.nan, "mb": c3.mb.sum(), "como_foi_estimado": ""}])], ignore_index=True)
    gravar(c3, "custo-de-baixar-as-novas_planetary-computer_2014-2026_ativo", "C3 — Estimativa do que custaria baixar as passagens prováveis novas, na janela da área urbana", {
        "sensor": "sensor", "ativo": "arquivo por passagem", "passagens_provaveis_novas": "passagens prováveis que não temos", "arquivos": "arquivos a baixar (radar em fatias pode pedir a leitura de duas fatias para um arquivo)",
        "mb_por_arquivo": "tamanho médio por arquivo", "mb": "tamanho estimado", "como_foi_estimado": "de onde vem o tamanho médio"}, casas={"mb_por_arquivo": 2})

    c4 = []
    for r in ep.itertuples():
        k = prov[(prov.episodio == r.episodio) & ~prov.ja_temos]
        escolhas = []
        if len(k):
            escolhas.append(("mais perto do pico", perto(k).iloc[0]))
            for fase in ("subida", "descida"):
                resto = k[(k.fase == fase) & ~k.identificadores.isin([e[1].identificadores for e in escolhas])]
                if len(resto):
                    escolhas.append((f"na {fase}", perto(resto).iloc[0]))
        for papel, m in escolhas:
            c4.append({"episodio": r.episodio, "data_do_pico": f"{r.data_do_pico:%Y-%m-%d}", "pico_cm": r.pico_cm, "episodio_fora_das_janelas": r.janelas_ja_usadas == "", "papel": papel, "sensor": NOME[m.sensor], "data_hora_local": m.data_hora_local,
                       "plataforma": m.plataforma, "orbita_relativa": m.orbita_relativa, "nivel_regua_cm": m.nivel_regua_cm, "fase": m.fase, "dias_ate_o_pico": int(m.dias_ate_o_pico), "cm_abaixo_do_pico": m.cm_abaixo_do_pico,
                       "cobertura_do_dominio_pct": m.cobertura_do_dominio_pct, "nuvem_da_quadricula_pct": m.nuvem_da_quadricula_pct, "identificadores": m.identificadores})
    c4 = pd.DataFrame(c4)
    gravar(c4, "lista-curta-sugerida_planetary-computer_2014-2026_passagem", "C4 — Lista curta sugerida: até 3 passagens prováveis novas por episódio", {
        **{k: v for k, v in col_b.items()}, "data_do_pico": col_ep["data_do_pico"], "pico_cm": col_ep["pico_cm"], "episodio_fora_das_janelas": "o episódio não toca nenhuma janela já usada",
        "papel": "por que entrou: mais perto do pico, na subida ou na descida"},
        regra=f"a mais próxima do pico em dias; no empate, radar antes de óptico com nuvem da quadrícula ≥ {ARGS.nuvem_preferir_radar:g} %; depois, se houver, uma na subida e uma na descida; só prováveis que não temos",
        nota="Sugestão; nada foi baixado.")

    resumo = pas.groupby(["episodio", "sensor"]).agg(passagens=("provavel", "size"), provaveis=("provavel", "sum"), ja_temos=("ja_temos", "sum")).reset_index()
    if ARGS.figuras:
        ARGS.figuras.mkdir(parents=True, exist_ok=True)
        figura(ARGS.figuras / "regua-episodios-e-passagens-provaveis_sentinel-ana_2014-2026.png", s, ep, janelas, pas)
    if ARGS.copia:
        ARGS.copia.mkdir(parents=True, exist_ok=True)
        for arq in sorted(PROC.iterdir()):
            shutil.copy2(arq, ARGS.copia / arq.name)
    print(json.dumps({"serie": info_serie, "colecoes": cols, "consultou_a_grd": bool(usar_grd), "episodios": len(ep), "episodios_menores": len(menores), "passagens": len(pas), "provaveis": int(pas.provavel.sum()),
                      "provaveis_novas": int((pas.provavel & ~pas.ja_temos).sum()), "cenas_do_controle_de_qualidade_achadas": conhecidas, "itens_do_controle_sem_par": sem_par,
                      "por_episodio_e_sensor": resumo.to_dict("records"), "avisos": AVISOS}, ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", force=True)
    _p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    _p.add_argument("--inicio", default="2014-10-01", help="primeiro dia da série a examinar (AAAA-MM-DD)")
    _p.add_argument("--limiar-cm", type=float, default=800.0, help="nível a partir do qual o dia é de cheia (cm)")
    _p.add_argument("--limiar-menor-cm", type=float, default=700.0, help="os episódios com pico entre isto e o limiar são só contados (cm)")
    _p.add_argument("--intervalo-dias", type=int, default=3, help="trechos separados por até estes dias abaixo do limiar contam como um episódio")
    _p.add_argument("--folga-dias", type=int, default=5, help="dias antes do início e depois do fim de cada episódio que entram na busca")
    _p.add_argument("--margem-m", type=float, default=500.0, help="margem em volta do retângulo da área urbana (m)")
    _p.add_argument("--cobertura-min", type=float, default=95.0, help="triagem: cobertura mínima do domínio pela pegada (%%)")
    _p.add_argument("--nuvem-max", type=float, default=30.0, help="triagem do óptico: nuvem declarada da quadrícula abaixo disto (%%)")
    _p.add_argument("--nuvem-preferir-radar", type=float, default=10.0, help="na lista curta, no empate de dias, o radar passa à frente do óptico com nuvem da quadrícula a partir disto (%%)")
    _p.add_argument("--faixa-cm", type=float, default=100.0, help="largura das faixas de nível do resumo (cm)")
    _p.add_argument("--folga-da-janela", type=float, default=1.2, help="no custo, conta como janela da área urbana o arquivo bruto lido num retângulo de até isto vezes a área do domínio")
    _p.add_argument("--tentativas", type=int, default=4, help="pedidos ao catálogo: a primeira tentativa e as repetições com espera")
    _p.add_argument("--figuras", type=Path, help="pasta (fora do repositório) da figura; sem ela, não há figura")
    _p.add_argument("--copia", type=Path, help="pasta que recebe uma cópia dos produtos")
    _p.add_argument("--refazer", action="store_true", help="gera de novo mesmo se a pasta de saída já tiver arquivos")
    ARGS = _p.parse_args()
    FONTES, LICENCAS, AVISOS, CONSULTA = [], {}, [], ""
    for _pasta in (ARGS.figuras, ARGS.copia):
        if _pasta is not None and c.RAIZ in _pasta.resolve().parents:
            raise SystemExit("--figuras e --copia têm de ficar fora do repositório")
    main()
