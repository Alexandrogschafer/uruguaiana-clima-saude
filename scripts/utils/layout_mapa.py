"""
Layout das figuras de mapa (PNG) dos estudos de população, inundação e
acessibilidade.

Dois modos:
  - "lateral" (rodadas 01–09): legenda à direita do mapa, título no alto,
    rodapé com fonte + aviso de produto de trabalho + EPSG; figura recortada
    pelo conteúdo (bbox_inches="tight");
  - "a4" (rodada 10): edição para o relatório em folha A4 retrato. Figura de
    16 cm de largura (área útil com margens de 2,5 cm), altura até 23 cm,
    300 dpi; o quadro do mapa ocupa a largura da figura; de cima para baixo:
    título, mapa(s), legenda (2 a 4 colunas, sem moldura), quadro de notas,
    rodapé (só fonte e, quando indispensável, uma linha de método; até 3
    linhas). O aviso "pendente de conferência" e o EPSG ficam no .json irmão.

Só apresentação: os scripts continuam desenhando o conteúdo do mapa (classes,
cores, símbolos, enquadramento); este módulo só monta a página.

Uso típico no modo a4:
    lay = LayoutA4([[ext]])                 # uma linha com um quadro
    ax = lay.eixos[0]
    ... desenhar no ax ...
    finalizar_a4(lay, titulo, handles, legenda_titulo, fonte, caminho, origem)
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties
from matplotlib.patches import FancyBboxPatch

logger = logging.getLogger(__name__)

LAYOUTS = ("lateral", "a4")
RAIZ = Path(__file__).resolve().parents[2]

# ---------------------------------------------------------------- padrão A4
CM = 1 / 2.54
A4_LARGURA_CM = 16.0  # 21,0 cm − 2 × 2,5 cm de margem
A4_ALTURA_MAX_CM = 23.0
A4_DPI = 300
FUNDO = "#fcfcfb"
INK, MUTED = "#0b0b0b", "#898781"
# tamanhos de letra no tamanho final (16 cm de largura)
FS_TITULO = 10
FS_LEGENDA = 8
FS_LEGENDA_TITULO = 8.5
FS_ROTULO_MIN = 7  # rótulos dentro do mapa (unidades, P01–P05, T01–T33, trechos)
FS_RODAPE = 6.5
FS_NOTAS = 8
FS_NOTAS_LISTA = 7.5  # itens em colunas no quadro de notas (lista de trechos)
FS_QUADRO = 8  # título de cada quadro nos painéis (cota e totais; "detalhe A")
MAX_LINHAS_RODAPE = 3
# espaços em polegadas
MARGEM = 0.03  # folga lateral mínima: a moldura do quadro não é cortada na borda da imagem
GAP_H = 0.12  # entre quadros lado a lado
GAP_LINHA = 0.10  # entre linhas de quadros
GAP_BLOCO = 0.07  # entre blocos (título, mapa, legenda, notas, rodapé)
TOPO, BASE = 0.04, 0.03
# título e rodapé (fonte, método) dentro da imagem; False: a imagem sai sem eles e os textos vão para o .json irmão
TITULO_E_FONTE_NA_IMAGEM = True

# pastas da edição A4 (mesmo nome de arquivo do mapa de origem)
PASTAS_A4 = {
    "docs/dinamica_populacional/mapas_v2": "docs/dinamica_populacional/mapas_a4",
    "docs/exposicao_inundacao/mapas_v2": "docs/exposicao_inundacao/mapas_a4",
    "docs/acessibilidade_inundacao/mapas": "docs/acessibilidade_inundacao/mapas_a4",
    "docs/acessibilidade_inundacao/campo": "docs/acessibilidade_inundacao/campo/a4",
}

REGISTRO: list[dict] = []  # um item por PNG A4 gravado nesta execução (para o relatório)


def pasta_a4(pasta_origem: Path) -> Path:
    rel = Path(pasta_origem).resolve().relative_to(RAIZ).as_posix()
    if rel not in PASTAS_A4:
        raise ValueError(f"pasta sem edição A4 definida: {rel}")
    p = RAIZ / PASTAS_A4[rel]
    p.mkdir(parents=True, exist_ok=True)
    return p


def fonte_rotulo(fs: float, layout: str) -> float:
    """Tamanho dos rótulos dentro do mapa: no modo a4, no mínimo FS_ROTULO_MIN."""
    return max(fs, FS_ROTULO_MIN) if layout == "a4" else fs


# ---------------------------------------------------------------- modo lateral
def finalizar_lateral(fig, ax, titulo: str, handles: list, legenda_titulo: str, rodape: str, caminho: Path, dpi: int = 180,
                      fs_legenda: float = 7.5, fs_legenda_titulo: float = 8, fs_rodape: float = 6.5, alinhamento: str | None = None) -> Path:
    """Layout das rodadas 01–09: legenda à direita, título, rodapé abaixo do mapa, figura recortada pelo conteúdo."""
    kw = {"alignment": alinhamento} if alinhamento else {}
    ax.legend(handles=handles, title=legenda_titulo, loc="upper left", bbox_to_anchor=(1.01, 1), frameon=False, fontsize=fs_legenda,
              title_fontsize=fs_legenda_titulo, **kw)
    ax.set_title(titulo, loc="left", fontsize=10, color=INK)
    ax.annotate(rodape, xy=(0, -0.015), xycoords="axes fraction", va="top", fontsize=fs_rodape, color=MUTED)
    fig.savefig(caminho, dpi=dpi, facecolor=FUNDO, bbox_inches="tight")
    plt.close(fig)
    return caminho


# ---------------------------------------------------------------- modo a4
class LayoutA4:
    """Figura de 16 cm com quadros de mapa empilhados em linhas.

    linhas: lista de linhas; cada linha é uma lista de extensões (x0, x1, y0, y1)
    ou um dict {"exts": [...], "largura": fração da largura útil (padrão 1)}.
    Os quadros de uma linha têm a mesma largura; a altura segue a proporção da
    extensão (o enquadramento não muda). O tamanho de cada quadro em polegadas
    é fixado aqui, antes do desenho (rótulos posicionados em pontos dependem
    dele); a posição vertical final é calculada em finalizar_a4.
    """

    def __init__(self, linhas: list):
        self.W = A4_LARGURA_CM * CM
        H0 = A4_ALTURA_MAX_CM * CM
        self.fig = plt.figure(figsize=(self.W, H0), dpi=A4_DPI)  # medidas de texto na resolução final
        self.linhas = []  # [(ax, x_in, w_in, h_in), ...] por linha
        self.eixos = []
        util = self.W - 2 * MARGEM
        y = H0
        for linha in linhas:
            if isinstance(linha, dict):
                exts, frac = linha["exts"], linha.get("largura", 1.0)
            else:
                exts, frac = linha, 1.0
            n = len(exts)
            w = (util * frac - (n - 1) * GAP_H) / n
            itens = []
            hmax = 0
            for i, e in enumerate(exts):
                h = w * (e[3] - e[2]) / (e[1] - e[0])
                hmax = max(hmax, h)
                x = MARGEM + i * (w + GAP_H)
                ax = self.fig.add_axes([x / self.W, (y - h) / H0, w / self.W, h / H0])
                itens.append((ax, x, w, h))
                self.eixos.append(ax)
            y -= hmax + GAP_LINHA
            self.linhas.append(itens)

    @property
    def largura_quadro_in(self) -> float:
        return self.linhas[0][0][2]


def _px_para_pt(fig, v: float) -> float:
    return v * 72 / fig.dpi


def _largura_pt(fig, renderer, texto: str, fs: float, negrito: bool = False) -> float:
    fp = FontProperties(size=fs, weight="bold" if negrito else "normal")
    w, _, _ = renderer.get_text_width_height_descent(texto, fp, ismath=False)
    return _px_para_pt(fig, w)


def quebrar(fig, renderer, texto: str, max_pt: float, fs: float) -> list[str]:
    """Quebra por palavras para caber em max_pt; quebras de linha existentes são mantidas."""
    out = []
    texto = texto.replace("≈ ", "≈\u00a0")  # "≈ n" não se separa na quebra
    for seg in texto.split("\n"):
        palavras = seg.split(" ")
        atual = ""
        for p in palavras:
            cand = p if not atual else f"{atual} {p}"
            if atual and _largura_pt(fig, renderer, cand, fs) > max_pt:
                out.append(atual)
                atual = p
            else:
                atual = cand
        out.append(atual)
    return out


def _altura_linhas_in(n: int, fs: float, entrelinha: float = 1.2) -> float:
    return n * fs * entrelinha / 72


def _legenda(fig, handles, rotulos, titulo, ncol):
    return fig.legend(handles=handles, labels=rotulos, title=titulo, loc="upper left", ncol=ncol, frameon=False,
                      fontsize=FS_LEGENDA, title_fontsize=FS_LEGENDA_TITULO, alignment="left", borderpad=0, borderaxespad=0,
                      columnspacing=1.2, handlelength=1.8, handletextpad=0.6, labelspacing=0.3)


def escolher_legenda(lay: LayoutA4, handles: list, titulo: str, renderer):
    """Testa 2, 3 e 4 colunas; fica com a legenda mais baixa que cabe na largura do mapa.

    Rótulo longo quebra em até 2 linhas (ou nas quebras que já tinha, se forem mais);
    a ordem dos itens é a da lista, lida por coluna (preenchimento por coluna do matplotlib).
    """
    fig = lay.fig
    larg_pt = (lay.W - 2 * MARGEM) * 72
    tit = titulo.replace("\n", " ")
    if _largura_pt(fig, renderer, tit, FS_LEGENDA_TITULO) > larg_pt:
        raise ValueError(f"título da legenda não cabe em uma linha: {tit!r}")
    rot_orig = [h.get_label() for h in handles]
    candidatos = []
    for ncol in (2, 3, 4):
        # variante 1: rótulos como estão; variante 2: rótulos longos quebrados na largura de coluna igual
        txt_pt = (larg_pt - (ncol - 1) * 1.2 * FS_LEGENDA) / ncol - (1.8 + 0.6) * FS_LEGENDA - 2
        rot_q, ok = [], True
        for r in rot_orig:
            linhas = quebrar(fig, renderer, r, txt_pt, FS_LEGENDA)
            if len(linhas) > r.count("\n") + 1 and "\n" not in r and " (" in r:
                # preferir quebrar antes do parêntese, quando as duas partes cabem
                i = r.index(" (")
                alt = [r[:i], r[i + 1:]]
                if all(_largura_pt(fig, renderer, x, FS_LEGENDA) <= txt_pt for x in alt):
                    linhas = alt
            if (len(linhas) > max(2, r.count("\n") + 1) and ncol > 2) or any(_largura_pt(fig, renderer, x, FS_LEGENDA) > txt_pt for x in linhas):
                ok = False
            rot_q.append("\n".join(linhas))
        for rot in ([rot_orig] + ([rot_q] if ok and rot_q != rot_orig else [])):
            leg = _legenda(fig, handles, rot, tit, ncol)
            fig.canvas.draw()
            bb = leg.get_window_extent(renderer)
            w_pt, h_pt = _px_para_pt(fig, bb.width), _px_para_pt(fig, bb.height)
            leg.remove()
            if w_pt <= larg_pt + 0.5:
                candidatos.append((round(h_pt, 1), ncol, rot, w_pt))
    if not candidatos:
        raise ValueError("legenda não cabe na largura do mapa em 2 a 4 colunas")
    logger.debug("legenda: candidatos %s", [(h, n, round(w)) for h, n, _, w in candidatos])
    # a mais baixa; empate: menos colunas
    h_pt, ncol, rot, w_pt = min(candidatos, key=lambda t: (t[0], t[1]))
    return ncol, rot, tit, h_pt / 72


def finalizar_a4(lay: LayoutA4, titulo: str, handles: list, legenda_titulo: str, fonte: str, caminho: Path, origem: Path,
                 metodo: str | None = None, notas: str | None = None, notas_lista: list[str] | None = None, notas_colunas: int = 1,
                 meta_extra: dict | None = None, texto_retirado: str | None = None, texto_na_imagem: bool | None = None) -> dict:
    """Monta a página A4 (título, quadros, legenda, notas, rodapé), grava o PNG e o .json irmão.

    fonte: texto da fonte (rodapé); metodo: uma linha de método (opcional).
    notas: texto do quadro de notas; notas_lista: itens que podem ser distribuídos em notas_colunas.
    origem: PNG do mapa de origem (modo lateral); o .json dele é a base do .json da edição A4.
    texto_retirado: texto do rodapé lateral que saiu da imagem (fica no .json).
    texto_na_imagem: False grava a imagem sem título e sem rodapé; título, fonte e método vão para o .json
    (campos "titulo", "fonte" e "metodo") e para o dicionário devolvido. Padrão: TITULO_E_FONTE_NA_IMAGEM.
    """
    na_imagem = TITULO_E_FONTE_NA_IMAGEM if texto_na_imagem is None else texto_na_imagem
    fig = lay.fig
    fig.canvas.draw()
    rend = fig.canvas.get_renderer()
    W = lay.W
    util_pt = (W - 2 * MARGEM) * 72

    # título: até 2 linhas, cada uma dentro da largura do mapa
    lin_tit = titulo.split("\n") if na_imagem else []
    if len(lin_tit) > 2:
        raise ValueError(f"título com mais de 2 linhas: {titulo!r}")
    for x in lin_tit:
        if _largura_pt(fig, rend, x, FS_TITULO) > util_pt:
            raise ValueError(f"linha do título não cabe na largura do mapa: {x!r}")
    h_tit = _altura_linhas_in(len(lin_tit), FS_TITULO, 1.25)

    # títulos dos quadros (painéis): o set_title do script vira texto da página, quebrado na largura do quadro
    tit_q = {}
    for itens in lay.linhas:
        for ax, x, w, h in itens:
            t = ax.get_title(loc="left")
            if t:
                ax.set_title("", loc="left")
                tit_q[ax] = quebrar(fig, rend, t, w * 72 - 1, FS_QUADRO)
    h_tq = [max([_altura_linhas_in(len(tit_q[ax]), FS_QUADRO) + 0.03 for ax, *_ in itens if ax in tit_q] or [0]) for itens in lay.linhas]

    # legenda
    ncol, rot, tit_leg, h_leg = escolher_legenda(lay, handles, legenda_titulo, rend)

    # notas
    pad = 0.05
    blocos_notas = []
    h_notas = 0
    if notas or notas_lista:
        larg_txt = util_pt - 2 * pad * 72
        cab = quebrar(fig, rend, notas, larg_txt, FS_NOTAS) if notas else []
        cols = []
        if notas_lista:
            n = notas_colunas
            per = -(-len(notas_lista) // n)
            larg_col = (larg_txt - (n - 1) * 10) / n
            for i in range(n):
                itens = notas_lista[i * per:(i + 1) * per]
                cols.append(sum((quebrar(fig, rend, it, larg_col, FS_NOTAS_LISTA) for it in itens), []))
        h_notas = _altura_linhas_in(len(cab), FS_NOTAS) + (_altura_linhas_in(max(len(c) for c in cols), FS_NOTAS_LISTA) if cols else 0) + 2 * pad
        blocos_notas = (cab, cols)

    # rodapé: fonte (+ método), até 3 linhas
    lin_rod = quebrar(fig, rend, fonte, util_pt, FS_RODAPE) if na_imagem else []
    metodo_fora = None
    if metodo and na_imagem:
        lm = quebrar(fig, rend, metodo, util_pt, FS_RODAPE)
        if len(lin_rod) + len(lm) <= MAX_LINHAS_RODAPE:
            lin_rod += lm
        else:
            metodo_fora = metodo
            logger.warning("linha de método fora do rodapé (passaria de %d linhas): %s", MAX_LINHAS_RODAPE, caminho.name)
    if len(lin_rod) > MAX_LINHAS_RODAPE:
        raise ValueError(f"rodapé com {len(lin_rod)} linhas (máx. {MAX_LINHAS_RODAPE}): {caminho.name}")
    h_rod = _altura_linhas_in(len(lin_rod), FS_RODAPE, 1.25)

    # altura total
    h_quadros = sum(h_tq[i] + max(h for *_, h in itens) for i, itens in enumerate(lay.linhas)) + GAP_LINHA * (len(lay.linhas) - 1)
    gap_tit, gap_rod = (GAP_BLOCO, GAP_BLOCO) if na_imagem else (0, 0)  # sem título e sem rodapé, os blocos e os seus espaços somem
    H = TOPO + h_tit + gap_tit + h_quadros + GAP_BLOCO + h_leg + (GAP_BLOCO + h_notas if h_notas else 0) + gap_rod + h_rod + BASE
    if H > A4_ALTURA_MAX_CM * CM + 1e-6:
        raise ValueError(f"altura {H / CM:.1f} cm passa de {A4_ALTURA_MAX_CM} cm: {caminho.name}")
    fig.set_size_inches(W, H)
    tr = fig.dpi_scale_trans  # coordenadas em polegadas a partir do canto inferior esquerdo

    y = H - TOPO
    if na_imagem:
        fig.text(MARGEM, y, titulo, transform=tr, ha="left", va="top", fontsize=FS_TITULO, color=INK, linespacing=1.25)
    y -= h_tit + gap_tit
    for i, itens in enumerate(lay.linhas):
        for ax, x, w, h in itens:
            if ax in tit_q:
                fig.text(x, y, "\n".join(tit_q[ax]), transform=tr, ha="left", va="top", fontsize=FS_QUADRO, color=INK)
            ax.set_position([x / W, (y - h_tq[i] - h) / H, w / W, h / H])
        y -= h_tq[i] + max(h for *_, h in itens) + GAP_LINHA
    y += GAP_LINHA - GAP_BLOCO
    leg = _legenda(fig, handles, rot, tit_leg, ncol)
    leg.set_bbox_to_anchor((MARGEM, y), transform=tr)
    y -= h_leg
    if h_notas:
        y -= GAP_BLOCO
        cab, cols = blocos_notas
        fig.patches.append(FancyBboxPatch((MARGEM, y - h_notas), W - 2 * MARGEM, h_notas, boxstyle="round,pad=0,rounding_size=0.04",
                                          transform=tr, facecolor="#ffffff", edgecolor="#8f8e88", linewidth=0.6, figure=fig))
        yt = y - pad
        if cab:
            fig.text(MARGEM + pad, yt, "\n".join(cab), transform=tr, ha="left", va="top", fontsize=FS_NOTAS, color=INK, linespacing=1.2)
            yt -= _altura_linhas_in(len(cab), FS_NOTAS)
        if cols:
            larg_col_in = ((W - 2 * MARGEM - 2 * pad) - (len(cols) - 1) * 10 / 72) / len(cols)
            for i, col in enumerate(cols):
                fig.text(MARGEM + pad + i * (larg_col_in + 10 / 72), yt, "\n".join(col), transform=tr, ha="left", va="top",
                         fontsize=FS_NOTAS_LISTA, color=INK, linespacing=1.2)
        y -= h_notas
    y -= gap_rod
    if na_imagem:
        fig.text(MARGEM, y, "\n".join(lin_rod), transform=tr, ha="left", va="top", fontsize=FS_RODAPE, color=MUTED, linespacing=1.25)

    # rótulos dentro do mapa que passariam da borda direita/esquerda da figura: espelhados para o outro lado do ponto
    fig.canvas.draw()
    rend = fig.canvas.get_renderer()
    from matplotlib.text import Annotation
    for ax in lay.eixos:
        for t in [c for c in ax.get_children() if isinstance(c, Annotation)]:
            bb = t.get_window_extent(rend)
            if bb.x1 > fig.bbox.width - 1 or bb.x0 < 1:
                dx, dy = t.xyann
                larg = bb.width * 72 / fig.dpi
                t.xyann = (-dx - larg, dy) if bb.x1 > fig.bbox.width - 1 else (-dx, dy)
                logger.info("rótulo espelhado para caber na folha: %s (%s)", t.get_text(), caminho.name)

    # conferência: nenhum texto da página fora da figura
    fig.canvas.draw()
    rend = fig.canvas.get_renderer()
    for ax in lay.eixos:
        for t in [c for c in ax.get_children() if isinstance(c, Annotation)]:
            bb = t.get_window_extent(rend)
            if bb.x1 > fig.bbox.width + 0.5 or bb.x0 < -0.5:
                raise ValueError(f"rótulo fora da figura em {caminho.name}: {t.get_text()!r}")
    Wpx, Hpx = fig.bbox.width, fig.bbox.height
    for t in fig.texts + [leg]:
        bb = t.get_window_extent(rend)
        if bb.x0 < -0.5 or bb.y0 < -0.5 or bb.x1 > Wpx + 0.5 or bb.y1 > Hpx + 0.5:
            raise ValueError(f"texto fora da figura em {caminho.name}: {getattr(t, 'get_text', lambda: 'legenda')()[:60]!r}")

    caminho = Path(caminho)
    fig.savefig(caminho, dpi=A4_DPI, facecolor=FUNDO)
    plt.close(fig)
    info = {"layout": "a4", "largura_cm": A4_LARGURA_CM, "altura_cm": round(H / CM, 2), "dpi": A4_DPI, "legenda_colunas": ncol}
    if not na_imagem:
        info.update(titulo=titulo.replace("\n", " — "), fonte=fonte, **({"metodo": metodo} if metodo else {}))
    extra = dict(meta_extra or {})
    if metodo_fora:
        extra["metodo_fora_do_rodape"] = metodo_fora
    if texto_retirado:
        extra["texto_retirado_da_imagem"] = texto_retirado
    gravar_meta_a4(caminho, origem, **info, **extra)
    REGISTRO.append({"arquivo": caminho.name, **info})
    logger.info("A4: %s (%.1f × %.1f cm, legenda em %d colunas)", caminho.relative_to(RAIZ), A4_LARGURA_CM, H / CM, ncol)
    return info


def gravar_meta_a4(caminho: Path, origem: Path, **campos) -> Path:
    """.json da edição A4: os campos do .json do mapa de origem + layout, largura, dpi e caminho do mapa de origem.

    O status do produto não muda; o aviso e o CRS que saíram da imagem ficam registrados aqui.
    """
    origem = Path(origem)
    j_orig = origem.with_suffix(".json")
    meta = json.loads(j_orig.read_text(encoding="utf-8")) if j_orig.exists() else {}
    meta.update({
        "produto": caminho.name,
        "data_processamento": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mapa_de_origem": origem.resolve().relative_to(RAIZ).as_posix(),
        "aviso_fora_da_imagem": "Produto de trabalho — pendente de conferência.",
        "crs_do_mapa": "EPSG:31981 (SIRGAS 2000 / UTM 21S)",
        "edicao_a4": "só apresentação: legenda abaixo do mapa, mapa na largura da folha; números, classes, cores, símbolos, recorte e enquadramento iguais aos do mapa de origem",
        **campos,
    })
    if not j_orig.exists():
        meta["aviso_origem"] = "mapa de origem sem .json irmão"
    destino = caminho.with_suffix(".json")
    destino.write_text(json.dumps(meta, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return destino


def posicionar_rotulos(ax, itens: list[tuple[float, float, str]], fs: float, obstaculos: list[tuple[float, float]] = (),
                       raio_obst_pt: float = 5.0, negrito: bool = True) -> list[tuple[float, float, bool]]:
    """Desloca rótulos (em pontos tipográficos) para não cobrir outros rótulos, marcadores nem sair do quadro.

    itens: (x, y, texto) em coordenadas do mapa; obstaculos: (x, y) dos marcadores. Testa anéis de posições
    em volta do ponto, do mais perto ao mais longe; devolve o deslocamento (dx, dy) do canto inferior esquerdo
    do texto, em pontos, e se o rótulo pede linha de chamada (afastado, ou mais perto de outro marcador que do seu).
    """
    import math

    fig = ax.figure
    fig.canvas.draw()
    rend = fig.canvas.get_renderer()
    k = 72 / fig.dpi
    bb_ax = ax.get_window_extent(rend)
    x0a, y0a, x1a, y1a = bb_ax.x0 * k, bb_ax.y0 * k, bb_ax.x1 * k, bb_ax.y1 * k
    obst = [tuple(ax.transData.transform(p) * k) for p in obstaculos]
    caixas = []
    saida = []
    alt = fs * 0.95
    for x, y, txt in itens:
        px, py = ax.transData.transform((x, y)) * k
        larg = _largura_pt(fig, rend, txt, fs, negrito) + 1
        escolhido = None
        for dist in (5, 9, 13, 18, 24, 31, 39):
            for ang in range(0, 360, 20):
                a = math.radians(ang)
                cx, cy = px + dist * math.cos(a), py + dist * math.sin(a)  # centro do rótulo
                bx0, by0, bx1, by1 = cx - larg / 2, cy - alt / 2, cx + larg / 2, cy + alt / 2
                if bx0 < x0a + 2 or bx1 > x1a - 2 or by0 < y0a + 2 or by1 > y1a - 2:
                    continue
                if any(bx0 < c[2] + 1 and bx1 > c[0] - 1 and by0 < c[3] + 1 and by1 > c[1] - 1 for c in caixas):
                    continue
                if any(bx0 - raio_obst_pt < ox < bx1 + raio_obst_pt and by0 - raio_obst_pt < oy < by1 + raio_obst_pt for ox, oy in obst):
                    continue
                escolhido = (bx0, by0, bx1, by1)
                break
            if escolhido:
                break
        if escolhido is None:  # sem lugar livre: à direita, como no modo lateral
            escolhido = (px + 5, py + 3, px + 5 + larg, py + 3 + alt)
            logger.warning("rótulo sem posição livre: %s", txt)
        caixas.append(escolhido)
        cx, cy = (escolhido[0] + escolhido[2]) / 2, (escolhido[1] + escolhido[3]) / 2
        d_proprio = math.hypot(cx - px, cy - py)
        d_outro = min([math.hypot(cx - ox, cy - oy) for ox, oy in obst if math.hypot(ox - px, oy - py) > 0.5] or [1e9])
        saida.append((escolhido[0] - px, escolhido[1] - py, d_proprio > 15 or d_outro < d_proprio))
    return saida
