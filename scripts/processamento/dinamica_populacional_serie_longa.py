"""
Rodada 13 — série longa da população do município (não espacial): Censos
anteriores a 2000 (1970, 1980, 1991) somados aos de 2000, 2010 e 2022.

Fontes (só a API de agregados do IBGE / SIDRA; nenhum número digitado):
  população por sexo e situação: 202 (1970–2010), 9923 e 9514 (2022);
  idade: 200 (1970–1991, grupos quinquenais, 80+ aberto) e, de 2000 em diante,
         o universo em idade simples já usado no estudo — 1552 e 9514;
  domicílios: 206 (domicílios particulares permanentes, 1970–1991), 207
         (moradores, 1991), 185 (um morador, 1991), 156 (particulares ocupados,
         1991); de 2000 em diante, 185, 207, 3451 e 4712.

Duas séries:
  "como publicado"       — o município com o território de cada Censo;
  "território constante" — o município somado aos municípios desmembrados dele
                           (--codigos-desmembrados), nos Censos em que estes
                           aparecem separados; nos demais é igual à publicada.

Idade em 1970 e 1980: a tabela 200 traz "idade ignorada"; percentuais e
indicadores etários usam a população com idade declarada. A idade mediana de
1970–1991 é interpolada dentro do grupo quinquenal (de 2000 em diante, na
idade simples, como na tabela atual).

Taxa geométrica anual: r = (P1/P0)^(1/t) − 1, t = anos entre as datas de
referência (1º/set em 1970, 1980 e 1991; 1º/ago em 2000, 2010 e 2022).

Regra de exibição: as tabelas guardam os valores sem arredondar (6 casas);
todo número escrito em figura ou texto é arredondado a partir deles.

Não regrava nenhum produto de 2000–2022: todos os nomes levam o período da
série (ex.: 1970-2022).

Uso:
  python scripts/processamento/dinamica_populacional_serie_longa.py \
      --codigo-ibge 4322400 --codigos-desmembrados 4301875
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import requests  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

import dinamica_populacional_comum as c  # noqa: E402
import dinamica_populacional_municipio as dm  # noqa: E402  (idade simples, mediana, paleta)
import layout_mapa as lm  # noqa: E402  (scripts/utils, via c)

sys.path.append(str(Path(__file__).resolve().parents[1] / "download"))
from dinamica_populacional_sidra import consultar  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

SCRIPT = "scripts/processamento/dinamica_populacional_serie_longa.py"
# mês de referência de cada Censo (para os anos entre Censos)
REFERENCIA = {1970: (1970, 9), 1980: (1980, 9), 1991: (1991, 9), 2000: (2000, 8), 2010: (2010, 8), 2022: (2022, 8)}
ANOS_ANTIGOS = [1970, 1980, 1991]
GRUPOS = dm.GRUPOS  # 0 a 4 … 75 a 79, 80+ (grupo aberto comum a todos os Censos)
SERIES = ("como publicado", "território constante")
VARS_POP = ("total", "urbana", "rural", "homens", "mulheres")
AZUL, LARANJA, NEUTRO = dm.AZUL, dm.LARANJA, "#cfcec6"
INK, INK2, MUTED, GRID = dm.INK, dm.INK2, dm.MUTED, dm.GRID
FIGURAS_A4 = c.DOCS / "figuras_a4"

# (chave, tabela, períodos, variáveis, classificações) — localidades: município + desmembrados (N6)
CONSULTAS = [
    ("202", 202, "1970|1980|1991|2000|2010", "93", "2[all]|1[all]"),
    ("200", 200, "1970|1980|1991|2000|2010", "93", "2[all]|1[0]|58[all]"),
    ("1552", 1552, "2000|2010", "93", "287[all]|2[all]|1[0]|286[0]"),
    ("9514", 9514, "2022", "93", "287[all]|2[all]|286[113635]"),
    ("9923", 9923, "2022", "93", "1[all]"),
    ("206", 206, "1970|1980|1991|2000|2010", "96", "1[all]|65[0]"),
    ("207", 207, "1991|2000|2010", "237", "1[all]|65[0]"),
    ("185", 185, "1991|2000|2010", "96", "1[0]|68[all]"),
    ("156", 156, "1991|2000|2010", "2048|134|619", None),
    ("205", 205, "1991", "93", "2[all]|58[all]"),  # só conferência da 200 em 1991
    ("3451", 3451, "2010", "96|137", "1[0]"),
    ("4712", 4712, "2022", "381|382", None),
]


# ---------------------------------------------------------------- download (idempotente, com registro)
def baixar(sessao: requests.Session, tabela: int, periodos: str, variaveis: str, classif: str | None,
           codigos: list[str], forcar: bool) -> pd.DataFrame:
    per = periodos.split("|")
    saida = c.SIDRA / f"sidra_tabela{tabela}_{per[0]}-{per[-1]}_serie-longa.csv"
    meta_p = saida.with_suffix(".json")
    locs = f"N6[{','.join(codigos)}]"
    if saida.exists() and meta_p.exists() and not forcar:
        if json.loads(meta_p.read_text(encoding="utf-8")).get("localidades") == locs:
            logger.info("Já baixada: %s", saida.name)
            return pd.read_csv(saida, dtype={"cod_localidade": str})
    df, url = consultar(sessao, tabela, periodos, variaveis, classif, locs)
    c.SIDRA.mkdir(parents=True, exist_ok=True)
    df.to_csv(saida, index=False)
    meta = {
        "fonte": f"IBGE — SIDRA, tabela {tabela} (Censo Demográfico)", "url_consulta": url, "localidades": locs,
        "data_acesso": datetime.now(timezone.utc).isoformat(timespec="seconds"), "n_linhas": len(df),
        "tamanho_bytes": saida.stat().st_size, "sha256": hashlib.sha256(saida.read_bytes()).hexdigest(),
        "tratamento": "formato longo, sem transformação de valores; '-' = 0; '...', '..' e 'X' = ausente (NaN) na coluna 'valor'",
    }
    meta_p.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Baixada: %s — %d linhas, %d bytes (%s)", saida.name, len(df), meta["tamanho_bytes"], meta["data_acesso"])
    return pd.read_csv(saida, dtype={"cod_localidade": str})


def valor(df: pd.DataFrame, cod: str, ano: int, **filtros) -> float:
    """Valor de uma célula (NaN quando ausente). filtros: coluna=valor."""
    m = (df.cod_localidade == cod) & (df.periodo == ano)
    for col, v in filtros.items():
        m &= df[col] == v
    s = df[m].valor
    return float(s.iloc[0]) if len(s) else float("nan")


def anos_entre(a0: int, a1: int) -> float:
    (y0, m0), (y1, m1) = REFERENCIA[a0], REFERENCIA[a1]
    return (y1 - y0) + (m1 - m0) / 12


def num(v: float, casas: int = 0) -> str:
    """Número em português (ponto de milhar, vírgula decimal), arredondado a partir do valor cheio."""
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "—"
    return f"{v:,.{casas}f}".replace(",", "§").replace(".", ",").replace("§", ".").replace("-", "−")


# ---------------------------------------------------------------- idade
def grupos_tabela200(t200: pd.DataFrame, cod: str, ano: int, sexo: str) -> tuple[list[float], float, float]:
    """(população dos 17 grupos, idade ignorada, total) da tabela 200 — 80+ lido ou somado de 80–84 … 100+."""
    d = t200[(t200.cod_localidade == cod) & (t200.periodo == ano) & (t200.Sexo == sexo)].set_index("Grupo de idade").valor
    pops = [d.get(f"{g} anos", np.nan) for g in GRUPOS[:-1]]
    v80 = d.get("80 anos ou mais", np.nan)
    if np.isnan(v80):
        v80 = d.reindex(["80 a 84 anos", "85 a 89 anos", "90 a 94 anos", "95 a 99 anos", "100 anos ou mais"]).sum(min_count=5)
    ign = d.get("Idade ignorada", np.nan)
    return pops + [v80], (0.0 if np.isnan(ign) else float(ign)), float(d.get("Total", np.nan))


def idades_simples(t1552: pd.DataFrame, t9514: pd.DataFrame, cod: str) -> pd.DataFrame:
    """ano × sexo × idade (0..100+) do universo, para uma localidade."""
    cols = ["periodo", "Sexo", "Idade", "valor"]
    df = pd.concat([t1552[t1552.cod_localidade == cod][cols], t9514[t9514.cod_localidade == cod][cols]])
    df["idade"] = df["Idade"].map(dm._idade_num)
    df = df.dropna(subset=["idade", "valor"])
    df["idade"] = df["idade"].astype(int)
    df["Sexo"] = df["Sexo"].replace({"Homem": "Homens", "Mulher": "Mulheres"})
    df = df[df.Sexo.isin(["Homens", "Mulheres"])]
    return df.rename(columns={"periodo": "ano", "Sexo": "sexo", "valor": "pop"})[["ano", "sexo", "idade", "pop"]]


def mediana_grupos(pops: list[float]) -> float:
    """Idade mediana interpolada dentro do grupo quinquenal [5i, 5i+5); NaN se cair no grupo aberto."""
    f = np.asarray(pops, dtype=float)
    cum = np.cumsum(f)
    meio = cum[-1] / 2
    i = int(np.searchsorted(cum, meio))
    if i >= len(f) - 1:
        return float("nan")
    return float(5 * i + 5 * (meio - (cum[i - 1] if i else 0)) / f[i])


# ---------------------------------------------------------------- página das figuras (edição atual e edição A4)
@dataclass
class Estilo:
    nome: str
    largura: float  # polegadas
    dpi: int
    fs_titulo: float
    fs_txt: float
    fs_rotulo: float
    fs_rodape: float
    pasta: Path
    formatos: tuple
    legenda_abaixo: bool

    @property
    def k(self) -> float:  # fator de escala das medidas em relação à folha de 16 cm
        return self.largura / (lm.A4_LARGURA_CM * lm.CM)


def estilos() -> list[Estilo]:
    return [
        # edição atual: mesma família, cores e proporções das figuras de 2000–2022 (PNG 200 dpi + SVG), legenda acima
        Estilo("lateral", 9.0, 200, 12, 9.5, 8.5, 8, c.FIGURAS, ("png", "svg"), False),
        # edição A4 (rodadas 10/11): 16 cm, 300 dpi, legenda abaixo, letras do padrão de layout_mapa
        Estilo("a4", lm.A4_LARGURA_CM * lm.CM, lm.A4_DPI, lm.FS_TITULO, lm.FS_LEGENDA, lm.FS_ROTULO_MIN, lm.FS_RODAPE,
               FIGURAS_A4, ("png",), True),
    ]


def _caixas_sobrepostas(a, b, folga: float = 0.5) -> bool:
    return a.x0 < b.x1 - folga and a.x1 > b.x0 + folga and a.y0 < b.y1 - folga and a.y1 > b.y0 + folga


def _marcas_visiveis(ax) -> list:
    """Rótulos das marcas dos eixos que caem dentro dos limites (os de fora não são desenhados)."""
    (x0, x1), (y0, y1) = sorted(ax.get_xlim()), sorted(ax.get_ylim())
    return ([t for t in ax.get_xticklabels() if x0 <= t.get_position()[0] <= x1]
            + [t for t in ax.get_yticklabels() if y0 <= t.get_position()[1] <= y1])


def rotular_pontos(ax, itens: list[tuple[float, float, str, tuple]], fs: float, cor: str = INK) -> int:
    """Escreve o valor junto de cada marcador sem cobrir linhas, marcadores nem outros rótulos.

    itens: (x, y, texto, ordem de preferência das posições). Devolve quantos ficaram sem posição livre.
    """
    fig = ax.figure
    fig.canvas.draw()
    rend = fig.canvas.get_renderer()
    pt = fig.dpi / 72  # px por ponto
    d = 5.0  # afastamento do marcador, em pontos
    posicoes = {"acima": ("center", "bottom", 0, d), "acima-esq": ("right", "bottom", 1, d), "acima-dir": ("left", "bottom", -1, d),
                "abaixo": ("center", "top", 0, -d), "abaixo-esq": ("right", "top", 1, -d), "abaixo-dir": ("left", "top", -1, -d)}
    obst = []  # pontos ao longo das linhas (em px)
    for ln in ax.lines:
        xy = ax.transData.transform(ln.get_xydata())
        for p0, p1 in zip(xy[:-1], xy[1:]):
            obst.extend(p0 + (p1 - p0) * t for t in np.linspace(0, 1, 60))
    obst = np.array(obst) if obst else np.zeros((0, 2))
    bb_ax = ax.get_window_extent(rend)
    feitas, sem_lugar = [], 0
    fp = matplotlib.font_manager.FontProperties(size=fs)
    for x, y, txt, pref in itens:
        px, py = ax.transData.transform((x, y))
        w, h, _ = rend.get_text_width_height_descent(txt, fp, ismath=False)
        escolha = None
        for nome in tuple(pref) + tuple(p for p in posicoes if p not in pref):
            ha, va, dx, dy = posicoes[nome]
            x0 = px + dx * pt - (w / 2 if ha == "center" else w if ha == "right" else 0)
            y0 = py + dy * pt - (h if va == "top" else 0)
            cx = matplotlib.transforms.Bbox.from_extents(x0, y0, x0 + w, y0 + h)
            if cx.x0 < bb_ax.x0 + 1 or cx.x1 > bb_ax.x1 - 1 or cx.y0 < bb_ax.y0 + 1 or cx.y1 > bb_ax.y1 - 1:
                continue
            if any(_caixas_sobrepostas(cx, o, -1.5 * pt) for o in feitas):
                continue
            f = 2.0 * pt  # folga entre texto e linha
            if len(obst) and np.any((obst[:, 0] > cx.x0 - f) & (obst[:, 0] < cx.x1 + f) & (obst[:, 1] > cx.y0 - f) & (obst[:, 1] < cx.y1 + f)):
                continue
            escolha = (nome, cx)
            break
        if escolha is None:
            sem_lugar += 1
            logger.warning("rótulo sem posição livre: %s (%s)", txt, x)
            escolha = (pref[0], None)
        ha, va, dx, dy = posicoes[escolha[0]]
        ax.annotate(txt, (x, y), xytext=(dx, dy), textcoords="offset points", ha=ha, va=va, fontsize=fs, color=cor,
                    bbox=dict(fc=ax.get_facecolor(), ec="none", pad=0.4), zorder=2.5)  # a grade não risca o número
        if escolha[1] is not None:
            feitas.append(escolha[1])
    return sem_lugar


def pagina(est: Estilo, nome: str, titulo: str, montar, handles: list, rodape: str, h_max_cm: float, meta: dict,
           Hmax: float | None = None) -> dict:
    """Monta a página de cima para baixo (título, [legenda], quadros, [legenda], rodapé) em duas passadas:
    a primeira mede a altura usada, a segunda monta a figura nessa altura (sem recorte)."""
    medir = Hmax is None
    k, W = est.k, est.largura
    Hmax = Hmax or lm.A4_ALTURA_MAX_CM * lm.CM * k
    fig = plt.figure(figsize=(W, Hmax), dpi=est.dpi)
    fig.canvas.draw()
    rend = fig.canvas.get_renderer()
    tr = fig.dpi_scale_trans
    margem, gap = lm.MARGEM * k, lm.GAP_BLOCO * k
    util_pt = (W - 2 * margem) * 72
    lin_tit = lm.quebrar(fig, rend, titulo, util_pt, est.fs_titulo)
    if len(lin_tit) > 2:
        raise ValueError(f"título não cabe em 2 linhas: {titulo!r}")
    yt = Hmax - lm.TOPO * k
    fig.text(margem, yt, "\n".join(lin_tit), transform=tr, ha="left", va="top", fontsize=est.fs_titulo, color=INK, linespacing=1.25)
    yt -= lm._altura_linhas_in(len(lin_tit), est.fs_titulo, 1.25) + gap

    def legenda(y):
        leg = fig.legend(handles=handles, loc="upper left", ncol=len(handles), frameon=False, fontsize=est.fs_txt, borderpad=0,
                         borderaxespad=0, columnspacing=1.6, handlelength=2.4, handletextpad=0.6, bbox_to_anchor=(margem, y),
                         bbox_transform=tr)
        fig.canvas.draw()
        bl = leg.get_window_extent(rend)
        if bl.width / fig.dpi > W - 2 * margem:
            raise ValueError(f"{nome}: legenda não cabe em uma linha")
        return leg, bl.y0 / fig.dpi

    leg = None
    if handles and not est.legenda_abaixo:
        leg, y_leg = legenda(yt)
        yt = y_leg - 1.6 * gap
    eixos, avisos = montar(fig, yt, Hmax, est)
    fig.canvas.draw()
    y_base = min(ax.get_tightbbox(rend).y0 for ax in eixos) / fig.dpi  # inclui rótulos e título do eixo x
    if handles and est.legenda_abaixo:
        leg, y_base = legenda(y_base - gap)
    lin_rod = lm.quebrar(fig, rend, rodape, util_pt, est.fs_rodape)
    if len(lin_rod) > lm.MAX_LINHAS_RODAPE:
        raise ValueError(f"{nome}: rodapé com mais de {lm.MAX_LINHAS_RODAPE} linhas")
    fig.text(margem, y_base - gap, "\n".join(lin_rod), transform=tr, ha="left", va="top", fontsize=est.fs_rodape, color=MUTED,
             linespacing=1.25)
    y_fim = y_base - gap - lm._altura_linhas_in(len(lin_rod), est.fs_rodape, 1.25) - lm.BASE * k
    H = Hmax - y_fim
    if medir:
        plt.close(fig)
        return pagina(est, nome, titulo, montar, handles, rodape, h_max_cm, meta, Hmax=H)
    if H > h_max_cm * lm.CM * k + 1e-6:
        raise ValueError(f"{nome}: altura {H / lm.CM / k:.1f} cm passa de {h_max_cm} cm")
    fig.canvas.draw()
    # conferência: nenhum texto fora da página nem sobreposto a outro texto do mesmo quadro
    textos = fig.texts + ([leg] if leg else []) + [t for ax in eixos for t in _marcas_visiveis(ax) + ax.texts + [ax.title, ax._left_title, ax.xaxis.label]]
    for t in textos:
        b = t.get_window_extent(rend)
        if b.width and t.get_visible() and (b.x0 < -0.5 or b.x1 > fig.bbox.width + 0.5 or b.y0 < -0.5 or b.y1 > fig.bbox.height + 0.5):
            raise ValueError(f"{nome}: texto fora da página: {t}")
    sobrepostos = 0
    for ax in eixos:
        cx = [t.get_window_extent(rend) for t in ax.texts if t.get_text()]
        sobrepostos += sum(_caixas_sobrepostas(a, b) for i, a in enumerate(cx) for b in cx[i + 1:])
    if sobrepostos or avisos:
        logger.warning("%s (%s): %d rótulos sobrepostos, %d sem posição livre", nome, est.nome, sobrepostos, avisos)
    est.pasta.mkdir(parents=True, exist_ok=True)
    for ext in est.formatos:
        fig.savefig(est.pasta / f"{nome}.{ext}", dpi=est.dpi, facecolor=lm.FUNDO)
    plt.close(fig)
    destino = est.pasta / f"{nome}.png"
    campos = dict(meta, formatos=list(est.formatos), status=c.STATUS_CONFERENCIA, rotulos_sobrepostos=sobrepostos,
                  rotulos_sem_posicao_livre=avisos)
    if est.nome == "a4":
        lm.gravar_meta_a4(destino, c.FIGURAS / f"{nome}.png", layout="a4", largura_cm=lm.A4_LARGURA_CM, altura_cm=round(H / lm.CM, 2),
                          dpi=lm.A4_DPI, legenda_colunas=len(handles), crs_do_mapa="não se aplica (figura sem mapa)",
                          edicao_a4="só apresentação: mesmos números e cores da figura de origem; legenda abaixo, 16 cm de largura", **campos)
    else:
        c.gravar_meta(destino, layout="atual", largura_px=round(W * est.dpi), altura_px=round(H * est.dpi), dpi=est.dpi, **campos)
    logger.info("Figura (%s): %s — %d × %d px", est.nome, destino.relative_to(c.RAIZ), round(W * est.dpi), round(H * est.dpi))
    return {"edicao": est.nome, "arquivo": destino.relative_to(c.RAIZ).as_posix(), "largura_px": round(W * est.dpi),
            "altura_px": round(H * est.dpi), "rotulos_sobrepostos": sobrepostos, "sem_posicao": avisos}


def _eixo_limpo(ax, est: Estilo, grade: str = "y") -> None:
    ax.tick_params(labelsize=est.fs_txt, length=2.5 * est.k, pad=2 * est.k)
    ax.grid(axis=grade, color=GRID, lw=0.5 * est.k)
    ax.set_axisbelow(True)
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)


# ---------------------------------------------------------------- figuras
def figura_populacao(est: Estilo, pop: pd.DataFrame, nome_mun: str, nomes_desm: list[str], periodo: str, fonte_geral: dict) -> dict:
    anos = pop.ano.tolist()
    difere = pop[(pop.total_constante - pop.total_publicado).abs() > 0].ano.tolist()

    def montar(fig, yt, Hmax, est):
        k, W = est.k, est.largura
        esq, dirt, alt, gap = 0.50 * k, 0.10 * k, 1.42 * k, 0.34 * k
        h_tit = est.fs_txt * 1.5 / 72
        eixos, avisos = [], 0
        for i, (col, rot) in enumerate((("total", "Total"), ("urbana", "Urbana"), ("rural", "Rural"))):
            topo = yt - h_tit - i * (alt + gap + h_tit)
            ax = fig.add_axes([esq / W, (topo - alt) / Hmax, (W - esq - dirt) / W, alt / Hmax])
            p, t = pop[f"{col}_publicado"].values, pop[f"{col}_constante"].values
            ax.plot(anos, p, "-o", color=AZUL, lw=1.4 * k, ms=4.2 * k, zorder=3)
            itens = []
            if difere:
                i0 = max(anos.index(difere[0]) - 1, 0)  # a tracejada parte do último Censo em que as séries coincidem
                ax.plot(anos[i0:], t[i0:], "--", color=AZUL, lw=1.1 * k, zorder=2)
                ax.plot(difere, [t[anos.index(a)] for a in difere], "o", color=AZUL, mfc=lm.FUNDO, ms=4.2 * k, mew=1.1 * k, zorder=3)
            for j, a in enumerate(anos):
                if a in difere:  # dois valores no mesmo ano: constante acima, publicado abaixo (ou o inverso se for menor)
                    cima = t[j] >= p[j]
                    itens.append((a, t[j], num(t[j]), ("acima", "acima-dir", "acima-esq") if cima else ("abaixo", "abaixo-dir", "abaixo-esq")))
                    itens.append((a, p[j], num(p[j]), ("abaixo", "abaixo-dir", "abaixo-esq") if cima else ("acima", "acima-dir", "acima-esq")))
                else:
                    itens.append((a, p[j], num(p[j]), ("acima", "acima-esq", "acima-dir", "abaixo-dir", "abaixo")))
            lo, hi = float(np.nanmin([p, t])), float(np.nanmax([p, t]))
            folga = 0.30 * (hi - lo)
            ax.set_ylim(lo - folga, hi + folga)
            ax.set_xlim(anos[0] - 5, anos[-1] + 5)
            ax.set_xticks(anos, [str(a) for a in anos] if i == 2 else [""] * len(anos))
            ax.yaxis.set_major_locator(matplotlib.ticker.MaxNLocator(6, steps=[1, 2, 4, 5, 10]))  # marcas inteiras (em mil)
            ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: num(v / 1000, 0)))
            ax.set_ylabel("mil habitantes", fontsize=est.fs_txt, labelpad=2 * k)
            _eixo_limpo(ax, est, "both")
            ax.set_title(rot, loc="left", fontsize=est.fs_txt + 0.5, color=INK, pad=3 * k)
            avisos += rotular_pontos(ax, itens, est.fs_rotulo)
            eixos.append(ax)
        return eixos, avisos

    handles = [Line2D([], [], color=AZUL, lw=1.4 * est.k, marker="o", ms=4.2 * est.k, label="Como publicado (território de cada Censo)")]
    nota = ""
    if difere:
        handles.append(Line2D([], [], color=AZUL, lw=1.1 * est.k, ls="--", marker="o", mfc=lm.FUNDO, ms=4.2 * est.k, mew=1.1 * est.k,
                              label="Território constante"))
        nota = (f" Linha tracejada: {nome_mun} somado a {', '.join(nomes_desm)}, município desmembrado depois de {max(a for a in anos if a < difere[0])}"
                f" — o território dos Censos de {anos[0]} a {max(a for a in anos if a < difere[0])}.")
    return pagina(est, f"populacao-total-urbana-rural_ibge-censo_{periodo}_municipal",
                  f"População total, urbana e rural — {nome_mun}, Censos {anos[0]} a {anos[-1]}", montar, handles,
                  f"Fonte: IBGE, Censos Demográficos {anos[0]} a {anos[-1]} (SIDRA 202, 9923 e 9514)." + nota + " Cada quadro tem escala vertical própria.",
                  18, dict(fonte_geral, descricao="linhas da população total, urbana e rural por Censo, com o valor em cada marcador; "
                           "tracejada = território constante, só onde difere da série publicada",
                           fonte=f"tabela populacao-sexo-situacao_ibge-censo_{periodo}_municipal.csv"))


def figura_piramides(est: Estilo, pir: pd.DataFrame, nome_mun: str, periodo: str, fonte_geral: dict, rodape_idade: str) -> dict:
    pir = pir[pir.serie == SERIES[0]]
    anos = sorted(pir.ano.unique())
    xmax = float(np.ceil(pir.pct_pop_total.max() + 0.5))
    y = np.arange(len(GRUPOS))
    ncol = 3

    def montar(fig, yt, Hmax, est):
        k, W = est.k, est.largura
        esq, gap_h, alt, gap_v, dirt = 0.52 * k, 0.20 * k, 2.55 * k, 0.42 * k, 0.10 * k
        larg = (W - dirt - esq - (ncol - 1) * gap_h) / ncol
        h_ano = (est.fs_txt + 1) * 1.5 / 72
        n_lin = int(np.ceil(len(anos) / ncol))
        eixos = []
        for i, ano in enumerate(anos):
            lin, col = divmod(i, ncol)
            x = esq + col * (larg + gap_h)
            topo = yt - h_ano - lin * (alt + gap_v + h_ano)
            ax = fig.add_axes([x / W, (topo - alt) / Hmax, larg / W, alt / Hmax])
            d = pir[pir.ano == ano].set_index(["sexo", "grupo"]).pct_pop_total
            ax.barh(y, [-d.get(("Homens", g), 0) for g in GRUPOS], color=AZUL, height=0.82, edgecolor=lm.FUNDO, linewidth=0.6 * k)
            ax.barh(y, [d.get(("Mulheres", g), 0) for g in GRUPOS], color=LARANJA, height=0.82, edgecolor=lm.FUNDO, linewidth=0.6 * k)
            ax.set_xlim(-xmax, xmax)
            ax.set_ylim(-0.6, len(GRUPOS) - 0.4)
            ax.set_yticks(y, GRUPOS if col == 0 else [""] * len(GRUPOS))  # grupos de idade só na primeira coluna
            tk = np.arange(-2 * (xmax // 2), xmax + 0.01, 2)
            ax.set_xticks(tk, [f"{abs(v):.0f}" for v in tk])
            ax.axvline(0, color="#c3c2b7", lw=0.6 * k)
            _eixo_limpo(ax, est, "x")
            ax.set_title(str(ano), loc="left", fontsize=est.fs_txt + 1, color=INK, pad=3 * k)
            if lin == n_lin - 1:
                ax.set_xlabel("% da população total", fontsize=est.fs_txt, labelpad=2 * k)
            eixos.append(ax)
        return eixos, 0

    return pagina(est, f"piramides-etarias_ibge-censo_{periodo}_municipal",
                  f"Pirâmides etárias — {nome_mun}, Censos {anos[0]} a {anos[-1]} (mesma escala)", montar,
                  [Patch(color=AZUL, label="Homens (esquerda)"), Patch(color=LARANJA, label="Mulheres (direita)")],
                  rodape_idade + " Grupos quinquenais, 80+ aberto; barras em % da população total do ano (com idade declarada).",
                  20, dict(fonte_geral, descricao=f"{len(anos)} pirâmides em {ncol} colunas, mesma escala horizontal; grupos de idade só na primeira coluna; série como publicada",
                           fonte=f"tabela piramide-etaria_ibge-censo_{periodo}_municipal.csv"))


def figura_grandes_grupos(est: Estilo, ind: pd.DataFrame, nome_mun: str, periodo: str, fonte_geral: dict, rodape_idade: str) -> dict:
    ind = ind[ind.serie == SERIES[0]]
    anos = ind.ano.tolist()
    partes = [("pct_0_14", "0 a 14 anos", AZUL, "#ffffff"), ("pct_15_59", "15 a 59 anos", NEUTRO, INK), ("pct_60_mais", "60 anos ou mais", LARANJA, INK)]

    def montar(fig, yt, Hmax, est):
        k, W = est.k, est.largura
        esq, dirt, alt = 0.42 * k, 0.16 * k, 0.36 * k * len(anos)
        ax = fig.add_axes([esq / W, (yt - alt) / Hmax, (W - esq - dirt) / W, alt / Hmax])
        yy = np.arange(len(anos))[::-1]  # Censo mais antigo no alto
        base = np.zeros(len(anos))
        for col, _, cor, cor_txt in partes:
            v = ind[col].values
            ax.barh(yy, v, left=base, color=cor, height=0.72, edgecolor=lm.FUNDO, linewidth=1.0 * k)
            for yi, b, vi in zip(yy, base, v):
                ax.text(b + vi / 2, yi, num(vi, 1), ha="center", va="center", fontsize=est.fs_rotulo, color=cor_txt)
            base = base + v
        ax.set_xlim(0, 100)
        ax.set_ylim(-0.55, len(anos) - 0.45)
        ax.set_yticks(yy, [str(a) for a in anos])
        ax.set_xticks(range(0, 101, 20))
        ax.set_xlabel("% da população", fontsize=est.fs_txt, labelpad=2 * k)
        _eixo_limpo(ax, est, "x")
        ax.spines["left"].set_visible(False)
        ax.tick_params(axis="y", length=0)
        return [ax], 0

    return pagina(est, f"estrutura-etaria-grandes-grupos_ibge-censo_{periodo}_municipal",
                  f"Estrutura etária em três grandes grupos — {nome_mun}, Censos {anos[0]} a {anos[-1]}", montar,
                  [Patch(color=cor, label=rot) for _, rot, cor, _ in partes],
                  rodape_idade + " Percentuais sobre a população com idade declarada.",
                  10, dict(fonte_geral, descricao="barras horizontais empilhadas (0–14, 15–59, 60 ou mais), uma por Censo, com os percentuais; série como publicada",
                           fonte=f"tabela indicadores-etarios_ibge-censo_{periodo}_municipal.csv"))


def figura_envelhecimento(est: Estilo, ind: pd.DataFrame, nome_mun: str, periodo: str, fonte_geral: dict, rodape_idade: str) -> dict:
    ind = ind[ind.serie == SERIES[0]]
    anos = ind.ano.tolist()
    por_grupo = ind.idade_mediana_metodo.str.startswith("grupo").values

    def montar(fig, yt, Hmax, est):
        k, W = est.k, est.largura
        esq, gap_h, dirt, alt = 0.40 * k, 0.50 * k, 0.10 * k, 2.05 * k
        larg = (W - esq - gap_h - dirt) / 2
        h_tit = est.fs_txt * 1.5 / 72
        eixos, avisos = [], 0
        quadros = (("indice_envelhecimento_60_por_0_14", "Índice de envelhecimento (60+ por 100 de 0–14)", 1, LARANJA),
                   ("idade_mediana_anos", "Idade mediana (anos)", 1, AZUL))
        for i, (col, rot, casas, cor) in enumerate(quadros):
            ax = fig.add_axes([(esq + i * (larg + gap_h)) / W, (yt - h_tit - alt) / Hmax, larg / W, alt / Hmax])
            v = ind[col].values
            ax.plot(anos, v, "-", color=cor, lw=1.4 * k, zorder=2)
            vazio = por_grupo if col == "idade_mediana_anos" else np.zeros(len(anos), bool)
            for a, vi, oco in zip(anos, v, vazio):
                ax.plot([a], [vi], "o", color=cor, mfc=lm.FUNDO if oco else cor, ms=4.4 * k, mew=1.1 * k, zorder=3)
            folga = 0.22 * (np.nanmax(v) - np.nanmin(v))
            ax.set_ylim(np.nanmin(v) - folga, np.nanmax(v) + folga)
            ax.set_xlim(anos[0] - 6, anos[-1] + 5)
            ax.set_xticks(anos, [str(a) for a in anos])
            ax.yaxis.set_major_locator(matplotlib.ticker.MaxNLocator(5, steps=[1, 2, 5, 10]))
            ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda x, _: num(x, 0)))
            _eixo_limpo(ax, est, "both")
            ax.set_title(rot, loc="left", fontsize=est.fs_txt, color=INK, pad=3 * k)
            avisos += rotular_pontos(ax, [(a, vi, num(vi, casas), ("acima-esq", "acima", "abaixo-dir", "abaixo")) for a, vi in zip(anos, v)],
                                     est.fs_rotulo)
            eixos.append(ax)
        return eixos, avisos

    k = est.k
    handles = []
    if por_grupo.any():
        handles = [Line2D([], [], color=AZUL, lw=0, marker="o", mfc=lm.FUNDO, ms=4.4 * k, mew=1.1 * k,
                          label=f"Mediana interpolada no grupo quinquenal ({anos[0]}–{max(a for a, g in zip(anos, por_grupo) if g)})"),
                   Line2D([], [], color=AZUL, lw=0, marker="o", ms=4.4 * k, label="Mediana interpolada na idade simples")]
    return pagina(est, f"envelhecimento-idade-mediana_ibge-censo_{periodo}_municipal",
                  f"Índice de envelhecimento e idade mediana — {nome_mun}, Censos {anos[0]} a {anos[-1]}", montar, handles, rodape_idade,
                  10, dict(fonte_geral, descricao="dois quadros: índice de envelhecimento (60+/0–14 ×100) e idade mediana por Censo; série como publicada",
                           fonte=f"tabela indicadores-etarios_ibge-censo_{periodo}_municipal.csv"))


# ---------------------------------------------------------------- principal
def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--codigo-ibge", default=c.CODIGO_IBGE_DEFAULT)
    p.add_argument("--codigos-desmembrados", default="",
                   help="códigos IBGE (separados por vírgula) dos municípios desmembrados deste depois dos Censos antigos; vazio = nenhum")
    p.add_argument("--forcar", action="store_true", help="baixa de novo as tabelas do SIDRA")
    a = p.parse_args()
    cod = a.codigo_ibge
    desm = [x.strip() for x in a.codigos_desmembrados.split(",") if x.strip()]
    c.garantir_pastas()

    sessao = requests.Session()
    t = {ch: baixar(sessao, tab, per, var, cl, [cod] + desm, a.forcar) for ch, tab, per, var, cl in CONSULTAS}
    nomes = {}
    for _, r in pd.concat([t["202"], t["9923"]])[["cod_localidade", "localidade"]].drop_duplicates().iterrows():
        nome, _, uf = r.localidade.partition(" - ")
        nomes[r.cod_localidade] = f"{nome} ({uf})" if uf else nome
    nome_mun, nomes_desm = nomes.get(cod, f"município {cod}"), [nomes.get(d, d) for d in desm]

    # ---------------- a) população por situação e sexo, nas duas séries
    def pop_local(codigo: str, ano: int) -> dict:
        if ano == 2022:
            sit = lambda s: valor(t["9923"], codigo, 2022, **{"Situação do domicílio": s})  # noqa: E731
            sx = lambda s: valor(t["9514"], codigo, 2022, Sexo=s, Idade="Total")  # noqa: E731
        else:
            sit = lambda s: valor(t["202"], codigo, ano, Sexo="Total", **{"Situação do domicílio": s})  # noqa: E731
            sx = lambda s: valor(t["202"], codigo, ano, Sexo=s, **{"Situação do domicílio": "Total"})  # noqa: E731
        return {"total": sit("Total"), "urbana": sit("Urbana"), "rural": sit("Rural"), "homens": sx("Homens"), "mulheres": sx("Mulheres")}

    linhas = []
    for ano in REFERENCIA:
        pub = pop_local(cod, ano)
        if np.isnan(pub["total"]):
            logger.warning("%d: sem população municipal na API — Censo fora da série", ano)
            continue
        partes = {d: pop_local(d, ano) for d in desm}
        somados = [d for d in desm if not np.isnan(partes[d]["total"])]  # desmembrados que aparecem separados neste Censo
        lin = {"ano": ano}
        for v in VARS_POP:
            lin[f"{v}_publicado"] = pub[v]
        for v in VARS_POP:
            lin[f"{v}_constante"] = pub[v] + sum(partes[d][v] for d in somados)
        lin["pct_urbana_publicado"] = 100 * pub["urbana"] / pub["total"]
        lin["pct_urbana_constante"] = 100 * lin["urbana_constante"] / lin["total_constante"]
        lin["razao_sexos_h100m_publicado"] = 100 * pub["homens"] / pub["mulheres"]
        lin["razao_sexos_h100m_constante"] = 100 * lin["homens_constante"] / lin["mulheres_constante"]
        lin["desmembrados_somados"] = ";".join(somados)
        lin["fonte"] = "SIDRA 9923 (total, situação) e 9514 (sexo)" if ano == 2022 else "SIDRA 202"
        linhas.append(lin)
    pop = pd.DataFrame(linhas)
    anos = pop.ano.tolist()
    if not any(x in anos for x in ANOS_ANTIGOS):
        raise SystemExit("Nenhum Censo anterior a 2000 na API para este município — nada a produzir.")
    periodo = f"{anos[0]}-{anos[-1]}"

    var = []
    for a0, a1 in zip(anos[:-1], anos[1:]):
        r0, r1, tt = pop.set_index("ano").loc[a0], pop.set_index("ano").loc[a1], anos_entre(a0, a1)
        for v in VARS_POP:
            lin = {"intervalo": f"{a0}-{a1}", "anos": tt, "variavel": v}
            for s in ("publicado", "constante"):
                i, f = r0[f"{v}_{s}"], r1[f"{v}_{s}"]
                lin.update({f"inicio_{s}": i, f"fim_{s}": f, f"variacao_abs_{s}": f - i, f"variacao_pct_{s}": 100 * (f / i - 1),
                            f"taxa_geom_anual_pct_{s}": 100 * ((f / i) ** (1 / tt) - 1)})
            var.append(lin)
    var = pd.DataFrame(var)

    # ---------------- b) pirâmides: grupos quinquenais comuns (80+ aberto), nas duas séries
    simples = {x: idades_simples(t["1552"], t["9514"], x) for x in [cod] + desm}
    blocos, ign, tot_sexo, ida_simples = [], {}, {}, {}
    for ano in anos:
        somados = [d for d in pop.set_index("ano").loc[ano, "desmembrados_somados"].split(";") if d]
        for serie in SERIES:
            locais = [cod] + (somados if serie == SERIES[1] else [])
            if ano in ANOS_ANTIGOS:  # tabela 200: grupos de idade por sexo (+ idade ignorada)
                for sexo in ("Homens", "Mulheres"):
                    g, i, tt = grupos_tabela200(t["200"], cod, ano, sexo)
                    blocos += [{"ano": ano, "serie": serie, "sexo": sexo, "grupo": gr, "pop": v, "fonte": "SIDRA 200"} for gr, v in zip(GRUPOS, g)]
                    ign[(ano, serie, sexo)], tot_sexo[(ano, serie, sexo)] = i, tt
            else:  # universo em idade simples (1552, 9514), agrupado
                d = pd.concat([simples[x][simples[x].ano == ano] for x in locais]).groupby(["sexo", "idade"], as_index=False)["pop"].sum()
                ida_simples[(ano, serie)] = d
                d = d.assign(grupo=d.idade.map(dm.grupo_quinquenal)).groupby(["sexo", "grupo"])["pop"].sum()
                for sexo in ("Homens", "Mulheres"):
                    blocos += [{"ano": ano, "serie": serie, "sexo": sexo, "grupo": gr, "pop": float(d.get((sexo, gr), 0)),
                                "fonte": "SIDRA 9514" if ano == 2022 else "SIDRA 1552"} for gr in GRUPOS]
                    ign[(ano, serie, sexo)], tot_sexo[(ano, serie, sexo)] = 0.0, float(d[sexo].sum())
    pir = pd.DataFrame(blocos)
    pir["pct_pop_total"] = 100 * pir["pop"] / pir.groupby(["ano", "serie"])["pop"].transform("sum")
    pir = pir[["ano", "serie", "sexo", "grupo", "pop", "pct_pop_total", "fonte"]]

    # ---------------- c) indicadores etários
    ind = []
    for (ano, serie), d in pir.groupby(["ano", "serie"], sort=False):
        g = d.groupby("grupo")["pop"].sum().reindex(GRUPOS).values
        T, j, adu, ido, i80 = g.sum(), g[:3].sum(), g[3:12].sum(), g[12:].sum(), g[16]
        h, m = tot_sexo[(ano, serie, "Homens")], tot_sexo[(ano, serie, "Mulheres")]
        med_g = mediana_grupos(list(g))
        if (ano, serie) in ida_simples:
            s = ida_simples[(ano, serie)].groupby("idade")["pop"].sum()
            med, metodo = dm.mediana(pd.Series(s.index), s.reset_index(drop=True)), "idade simples (interpolação na classe de 1 ano)"
        else:
            med, metodo = med_g, "grupo quinquenal (interpolação na classe de 5 anos)"
        ind.append({"ano": ano, "serie": serie, "pop_idade_declarada": T, "pop_idade_ignorada": ign[(ano, serie, "Homens")] + ign[(ano, serie, "Mulheres")],
                    "pct_0_14": 100 * j / T, "pct_15_59": 100 * adu / T, "pct_60_mais": 100 * ido / T, "pct_80_mais": 100 * i80 / T,
                    "indice_envelhecimento_60_por_0_14": 100 * ido / j, "razao_dependencia_total": 100 * (j + ido) / adu,
                    "razao_dependencia_jovem": 100 * j / adu, "razao_dependencia_idosa": 100 * ido / adu, "razao_sexos_h100m": 100 * h / m,
                    "idade_mediana_anos": med, "idade_mediana_metodo": metodo, "idade_mediana_grupo_quinquenal_anos": med_g})
    ind = pd.DataFrame(ind)

    # ---------------- d) domicílios (série como publicada e território constante de 2000 em diante)
    def dom_local(codigo: str, ano: int) -> dict:
        v = lambda ch, **f: valor(t[ch], codigo, ano, **f)  # noqa: E731
        if ano == 2022:
            return {"dom": v("4712", variavel_id=381), "mor": v("4712", variavel_id=382), "um": np.nan,
                    "f_dom": "SIDRA 4712", "f_mor": "SIDRA 4712"}
        if ano in (1991, 2000, 2010):
            dom, f_dom = v("185", **{"Número de moradores": "Total"}), "SIDRA 185"
            um = v("185", **{"Número de moradores": "1 morador"})
        else:
            dom, f_dom, um = v("206", **{"Situação do domicílio": "Total"}), "SIDRA 206", np.nan
        mor, f_mor = (v("3451", variavel_id=137), "SIDRA 3451") if ano == 2010 else (v("207", **{"Situação do domicílio": "Total"}), "SIDRA 207")
        return {"dom": dom, "mor": mor, "um": um, "f_dom": f_dom, "f_mor": f_mor if not np.isnan(mor) else "sem tabela municipal na API"}

    dom = []
    for ano in anos:
        x = dom_local(cod, ano)
        somados = [d for d in pop.set_index("ano").loc[ano, "desmembrados_somados"].split(";") if d]
        ex = [dom_local(d, ano) for d in somados]
        dc, mc = x["dom"] + sum(e["dom"] for e in ex), x["mor"] + sum(e["mor"] for e in ex)
        dom.append({"ano": ano, "conceito": "domicílios particulares permanentes ocupados" if ano == 2022 else "domicílios particulares permanentes (ocupados)",
                    "domicilios_publicado": x["dom"], "moradores_publicado": x["mor"], "moradores_por_domicilio_publicado": x["mor"] / x["dom"],
                    "domicilios_urbanos_publicado": valor(t["206"], cod, ano, **{"Situação do domicílio": "Urbana"}) if ano in ANOS_ANTIGOS else np.nan,
                    "domicilios_rurais_publicado": valor(t["206"], cod, ano, **{"Situação do domicílio": "Rural"}) if ano in ANOS_ANTIGOS else np.nan,
                    "dom_1_morador_publicado": x["um"], "pct_dom_1_morador_publicado": 100 * x["um"] / x["dom"],
                    "domicilios_constante": dc, "moradores_constante": mc, "moradores_por_domicilio_constante": mc / dc,
                    "fonte_domicilios": x["f_dom"], "fonte_moradores": x["f_mor"],
                    "fonte_1_morador": "SIDRA 185" if not np.isnan(x["um"]) else "fora desta tabela (ver a tabela de 2000–2022) ou sem tabela municipal na API"})
    dom = pd.DataFrame(dom)

    # ---------------- conferências
    conf = []

    def confere(grupo, ano, item, va, fa, vb, fb):
        conf.append({"conferencia": grupo, "ano": ano, "item": item, "valor_a": va, "fonte_a": fa, "valor_b": vb, "fonte_b": fb,
                     "dif_a_menos_b": va - vb, "dif_pct_de_b": 100 * (va - vb) / vb if vb else np.nan})

    pir_pub = pir[pir.serie == SERIES[0]]
    for ano in [x for x in (2000, 2010) if x in anos]:  # tabela 200 (rotulada "Amostra") × universo (1552), por grupo de idade
        am = np.array(grupos_tabela200(t["200"], cod, ano, "Total")[0])
        un = pir_pub[pir_pub.ano == ano].groupby("grupo")["pop"].sum().reindex(GRUPOS).values
        for gr, x, y in zip(GRUPOS, am, un):
            confere("tabela 200 × universo, por grupo de idade", ano, gr, x, "SIDRA 200", y, "SIDRA 1552")
        confere("tabela 200 × universo, por grupo de idade", ano, "total", valor(t["200"], cod, ano, Sexo="Total", **{"Grupo de idade": "Total"}),
                "SIDRA 200", valor(t["202"], cod, ano, Sexo="Total", **{"Situação do domicílio": "Total"}), "SIDRA 202")
    ip = ind[ind.serie == SERIES[0]].set_index("ano")
    for ano in anos:
        tot = pop.set_index("ano").loc[ano, "total_publicado"]
        confere("soma das idades × total", ano, "idade declarada + ignorada", ip.loc[ano, "pop_idade_declarada"] + ip.loc[ano, "pop_idade_ignorada"],
                "SIDRA 200" if ano in ANOS_ANTIGOS else "SIDRA 1552/9514", tot, "SIDRA 202/9923")
        confere("homens + mulheres × total", ano, "população", pop.set_index("ano").loc[ano, "homens_publicado"] + pop.set_index("ano").loc[ano, "mulheres_publicado"],
                "SIDRA 202/9514", tot, "SIDRA 202/9923")
        confere("urbana + rural × total", ano, "população", pop.set_index("ano").loc[ano, "urbana_publicado"] + pop.set_index("ano").loc[ano, "rural_publicado"],
                "SIDRA 202/9923", tot, "SIDRA 202/9923")
    if 1991 in anos:  # idade em 1991: tabela 200 × tabela 205 (população por sexo e grupos de idade, 1991)
        d205 = t["205"][(t["205"].cod_localidade == cod) & (t["205"].Sexo == "Total")].set_index("Grupo de idade").valor
        for gr, x in zip(GRUPOS, grupos_tabela200(t["200"], cod, 1991, "Total")[0]):
            confere("tabela 200 × tabela 205, por grupo de idade", 1991, gr, x, "SIDRA 200", float(d205.get("80 anos ou mais" if gr == "80+" else f"{gr} anos", np.nan)), "SIDRA 205")
    if 1991 in anos:  # domicílios de 1991: duas tabelas
        confere("domicílios 1991", 1991, "domicílios particulares permanentes", valor(t["206"], cod, 1991, **{"Situação do domicílio": "Total"}), "SIDRA 206",
                valor(t["185"], cod, 1991, **{"Número de moradores": "Total"}), "SIDRA 185")
    # produtos atuais (2000–2022): os valores comuns têm de sair iguais
    atuais = {"indicadores": c.TABELAS / "indicadores-etarios_ibge-censo_2000-2022_municipal.csv",
              "populacao": c.TABELAS / "populacao-sexo-situacao_ibge-censo_2000-2022_municipal.csv",
              "piramide": c.TABELAS / "piramide-etaria_ibge-censo_2000-2022_municipal.csv",
              "domicilios": c.TABELAS / "domicilios_ibge-censo_2000-2022_municipal.csv"}
    if all(x.exists() for x in atuais.values()):
        at = pd.read_csv(atuais["indicadores"]).set_index("ano")
        for ano in [x for x in at.index if x in anos]:
            for col in [x for x in at.columns if x in ip.columns]:
                confere("série longa × tabela atual de indicadores (2 casas)", ano, col, round(float(ip.loc[ano, col]), 2), "série longa",
                        float(at.loc[ano, col]), atuais["indicadores"].name)
            confere("série longa × tabela atual de indicadores (2 casas)", ano, "pop_soma_idades", float(ip.loc[ano, "pop_idade_declarada"]), "série longa",
                    float(at.loc[ano, "pop_soma_idades"]), atuais["indicadores"].name)
        at = pd.read_csv(atuais["populacao"]).set_index("ano")
        for ano in [x for x in at.index if x in anos]:
            for v in VARS_POP:
                confere("série longa × tabela atual de população", ano, v, pop.set_index("ano").loc[ano, f"{v}_publicado"], "série longa", float(at.loc[ano, v]),
                        atuais["populacao"].name)
        at = pd.read_csv(atuais["piramide"]).merge(pir_pub, on=["ano", "sexo", "grupo"], suffixes=("_atual", "_longa"))
        for ano, d in at.groupby("ano"):
            confere("série longa × tabela atual da pirâmide", ano, f"maior diferença absoluta em {len(d)} células", float((d.pop_longa - d.pop_atual).abs().max()),
                    "série longa − tabela atual", 0.0, atuais["piramide"].name)
        at = pd.read_csv(atuais["domicilios"]).set_index("ano")
        for ano in [x for x in at.index if x in anos]:
            for col, col_l in (("domicilios", "domicilios_publicado"), ("moradores", "moradores_publicado")):
                confere("série longa × tabela atual de domicílios", ano, col, dom.set_index("ano").loc[ano, col_l], "série longa", float(at.loc[ano, col]),
                        atuais["domicilios"].name)
    else:
        logger.warning("Tabelas atuais de 2000–2022 ausentes: conferência com elas não feita")
    conf = pd.DataFrame(conf)

    # ---------------- gravação das tabelas (valores sem arredondar: 6 casas)
    fonte_geral = {"codigo_ibge": cod, "codigos_desmembrados": desm, "script": SCRIPT, "status": c.STATUS_CONFERENCIA}
    series_txt = ("séries: 'publicado' = município com o território de cada Censo; 'constante' = município somado aos desmembrados "
                  "nos Censos em que estes aparecem separados (coluna desmembrados_somados); nos demais as duas séries coincidem")
    idade_txt = ("grupos quinquenais comuns a todos os Censos, grupo aberto 80+; 1970–1991: SIDRA 200 (idade ignorada fora dos percentuais); "
                 "de 2000 em diante: universo em idade simples (SIDRA 1552 e 9514), agrupado")
    saidas = {
        f"populacao-sexo-situacao_ibge-censo_{periodo}_municipal.csv": (pop, "População residente total, urbana, rural, homens e mulheres; % urbana; razão de sexos — duas séries lado a lado; " + series_txt, "SIDRA 202 (1970–2010), 9923 e 9514 (2022)"),
        f"populacao-variacao_ibge-censo_{periodo}_municipal.csv": (var, "Variação absoluta, % e taxa geométrica anual entre Censos, nas duas séries; anos = intervalo entre as datas de referência (1º/set até 1991, 1º/ago de 2000 em diante); " + series_txt, f"derivado da tabela populacao-sexo-situacao_ibge-censo_{periodo}_municipal.csv"),
        f"piramide-etaria_ibge-censo_{periodo}_municipal.csv": (pir, "População por sexo e grupo quinquenal, em número e % da população (com idade declarada) do ano, nas duas séries; " + idade_txt, "SIDRA 200 (1970–1991), 1552 (2000, 2010) e 9514 (2022)"),
        f"indicadores-etarios_ibge-censo_{periodo}_municipal.csv": (ind, "Indicadores etários nas duas séries; índice de envelhecimento = 60+/0–14 ×100; razões de dependência por 100 pessoas de 15–59; razão de sexos = H/M ×100 (população total, com idade ignorada); idade_mediana_anos: 1970–1991 interpolada DENTRO DO GRUPO QUINQUENAL, de 2000 em diante na idade simples (coluna idade_mediana_metodo); idade_mediana_grupo_quinquenal_anos = mesmo método em todos os Censos; " + idade_txt, "SIDRA 200, 1552 e 9514"),
        f"domicilios_ibge-censo_{periodo}_municipal.csv": (dom, "Domicílios particulares permanentes, moradores e moradores por domicílio; 1970 e 1980: só o número de domicílios (sem tabela municipal de moradores na API); um morador: só 1991–2010 (SIDRA 185); 2022 fica na tabela de 2000–2022; " + series_txt, "SIDRA 206 (1970, 1980), 185 e 207 (1991, 2000), 185 e 3451 (2010), 4712 (2022)"),
        f"conferencia-serie-longa_ibge-censo_{periodo}_municipal.csv": (conf, "Conferências: tabela 200 × universo por grupo de idade (2000, 2010); soma das idades, dos sexos e das situações × total; série longa × tabelas atuais de 2000–2022", "SIDRA e tabelas atuais do estudo"),
    }
    for nome, (df, desc, fonte) in saidas.items():
        caminho = c.TABELAS / nome
        df.round(6).to_csv(caminho, index=False)
        c.gravar_meta(caminho, descricao=desc, fonte=fonte, **fonte_geral, municipio=nome_mun, desmembrados=nomes_desm,
                      transformacao="seleção das categorias da tabela, soma e razões; indisponível = ausente, nunca zero; valores sem arredondar (6 casas): toda exibição arredonda a partir deles")
        logger.info("Tabela: %s", caminho.relative_to(c.RAIZ))

    # ---------------- figuras (edição atual e edição A4)
    plt.rcParams.update({"font.family": "DejaVu Sans", "axes.edgecolor": "#c3c2b7", "axes.labelcolor": INK2, "xtick.color": INK2,
                         "ytick.color": INK2, "svg.fonttype": "path"})
    antigos = [x for x in anos if x in ANOS_ANTIGOS]
    rodape_idade = (f"Fonte: IBGE, Censos Demográficos {anos[0]} a {anos[-1]} (SIDRA 200 para {antigos[0]}–{antigos[-1]}; universo, SIDRA 1552 e 9514, de 2000 em diante). "
                    "Território de cada Censo.")
    registro = []
    for est in estilos():
        registro.append(figura_populacao(est, pop, nome_mun, nomes_desm, periodo, fonte_geral))
        registro.append(figura_piramides(est, pir, nome_mun, periodo, fonte_geral, rodape_idade))
        registro.append(figura_grandes_grupos(est, ind, nome_mun, periodo, fonte_geral, rodape_idade))
        registro.append(figura_envelhecimento(est, ind, nome_mun, periodo, fonte_geral, rodape_idade))

    # ---------------- resumo no terminal (números arredondados a partir dos valores cheios)
    pd.set_option("display.width", 250)
    pd.set_option("display.max_columns", 40)
    print("\n| Censo | Total (publicado) | Urbana | Rural | % urbana | Total (constante) | Urbana (const.) | Rural (const.) |")
    print("|---|---:|---:|---:|---:|---:|---:|---:|")
    for r in pop.itertuples():
        print(f"| {r.ano} | {num(r.total_publicado)} | {num(r.urbana_publicado)} | {num(r.rural_publicado)} | {num(r.pct_urbana_publicado, 1)} | "
              f"{num(r.total_constante)} | {num(r.urbana_constante)} | {num(r.rural_constante)} |")
    print("\n| Intervalo | anos | Δ publicado | % | % a.a. | Δ constante | % | % a.a. |\n|---|---:|---:|---:|---:|---:|---:|---:|")
    for r in var[var.variavel == "total"].itertuples():
        print(f"| {r.intervalo} | {num(r.anos, 2)} | {num(r.variacao_abs_publicado)} | {num(r.variacao_pct_publicado, 1)} | {num(r.taxa_geom_anual_pct_publicado, 2)} | "
              f"{num(r.variacao_abs_constante)} | {num(r.variacao_pct_constante, 1)} | {num(r.taxa_geom_anual_pct_constante, 2)} |")
    print(var[var.variavel.isin(["urbana", "rural"])].round(2).to_string())
    print(ind.round(2).T.to_string())
    print(dom.round(2).T.to_string())
    print(conf.round(3).to_string())
    print(pd.DataFrame(registro).to_string())


if __name__ == "__main__":
    main()
