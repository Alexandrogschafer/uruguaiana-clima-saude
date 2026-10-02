"""
Mapas da rodada 08 — acessibilidade às unidades de saúde ESF/UBS com vias
dentro das manchas de inundação (lê as camadas gravadas por
scripts/processamento/acessibilidade_inundacao.py; nada é recalculado aqui).

Mapas (área urbana da sede e faixa ribeirinha), em docs/acessibilidade_inundacao/mapas/:
  a) base: distância pela rede até a unidade mais próxima, por endereço;
  b) painel 2 x 2 das quatro cotas, um por cenário: mancha, trechos
     interrompidos, pontes, endereços pela classe, unidades;
  c) cotas 1205 e 1252 cm, nos dois cenários: acréscimo de distância por
     endereço (faixas) e ilhas contornadas;
  d) pontes: inventário, numeradas como na tabela;
  e) trechos críticos (cotas 1205 e 1252 cm).
Manchas cumulativas recortadas no limite municipal só para exibição.
Produtos "pendente de conferência"; não vão para o portal.

Uso:
  python scripts/processamento/acessibilidade_inundacao_mapas.py --codigo-ibge 4322400
"""

from __future__ import annotations

import argparse
import logging

import geopandas as gpd
import matplotlib

matplotlib.use("Agg")
import matplotlib.patheffects as pe  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402
from shapely.geometry import box  # noqa: E402

import dinamica_populacional_comum as c  # noqa: E402
from dinamica_populacional_cnefe_mapas import Fundo, mil  # noqa: E402
from dinamica_populacional_mapas import INK, MUTED, Base  # noqa: E402
from exposicao_inundacao_enderecos import ESCALA_POS, SIMB_UNIDADE, TAM_UNIDADE, rotular_unidades  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

SCRIPT = "scripts/processamento/acessibilidade_inundacao_mapas.py"
MAPAS = c.RAIZ / "docs" / "acessibilidade_inundacao" / "mapas"
CAM = c.RAIZ / "data" / "processed" / "acessibilidade_inundacao"
ARQ_COTAS = c.RAW / "vetor" / "cotas-inundacao_sgb_atual_vetorial.gpkg"
ARQ_UNIDADES = c.RAIZ / "data" / "processed" / "saude" / "unidades-saude-esf-ubs_cnes-revisado-v4_2026_pontos.gpkg"
CENARIOS = {"pes": "pessimista", "oti": "otimista"}
DESC_CEN = {"pes": "cenário PESSIMISTA — todo trecho com parte na mancha interrompido, pontes incluídas",
            "oti": "cenário OTIMISTA — pontes/viadutos do OpenStreetMap continuam passáveis"}
# cores (Okabe-Ito e rampas de luminância ordenada; a forma separa isolado de desvio em tons de cinza)
COR_MANCHA, COR_BORDA = "#9e9ac8", "#54278f"
COR_INTERROMP = "#7a0177"
COR_PONTE = "#ffffff"  # branco com contorno preto (símbolo de ponte); o amarelo confundia com o losango da UDM
COR_CLASSE = {"exposto": "#d55e00", "isolado": "#000000", "com desvio": "#0072b2", "sem alteração": "#cfcfcb", "sem caminho já na base": "#8c8c8c"}
MK_CLASSE = {"exposto": "o", "isolado": "s", "com desvio": "o", "sem alteração": "o", "sem caminho já na base": "x"}
FAIXAS_BASE = [0, 500, 1000, 2000, np.inf]
ROT_BASE = ["até 500 m", "500 m a 1 km", "1 a 2 km", "mais de 2 km"]
COR_BASE = ["#c6dbef", "#6baed6", "#2171b5", "#08306b"]
FAIXAS_ACR = [0, 250, 500, 1000, 2000, np.inf]
ROT_ACR = ["até 250 m", "250 a 500 m", "500 m a 1 km", "1 a 2 km", "mais de 2 km"]
COR_ACR = ["#c6dbef", "#6baed6", "#2171b5", "#08519c", "#08306b"]  # azuis: não confunde com o vermelhão dos expostos
FONTE = ("Fonte: OpenStreetMap — malha viária e pontes (contribuidores do OpenStreetMap); SGB — manchas de inundação por cota; "
         "IBGE — CNEFE e agregados por setor do Censo 2022;\nunidades de saúde: CNES (Ministério da Saúde), revisado e corrigido pela equipe do projeto "
         "com informações dos profissionais de saúde do município — versão 4, 2026.")
NOTA = ("Manchas cumulativas (união das cotas ≤ X), recortadas no limite municipal só para exibição. Via 'alagada' = trecho com ≥ 5 m dentro da mancha; "
        "a mancha não informa profundidade.\nA resposta real fica entre os cenários pessimista e otimista. 'Unidade mais próxima pela rede' não é o território da equipe. "
        "Produto de trabalho — pendente de conferência. EPSG:31981.")


def meta(caminho, **kw):
    c.gravar_meta(caminho, codigo_ibge=ARGS.codigo_ibge, crs=c.CRS_PADRAO, status=c.STATUS_CONFERENCIA, ligado_ao_portal=False, script=SCRIPT,
                  fonte=FONTE.replace("\n", " "), camadas_de_entrada="data/processed/acessibilidade_inundacao/ (fora do git)", **kw)


def main() -> None:
    MAPAS.mkdir(parents=True, exist_ok=True)
    base = Base(ARGS.codigo_ibge, agua=True, nome_rio=ARGS.nome_rio, modo_agua="enderecos")
    fundo = Fundo(base)
    lim = base.limite.union_all()
    cg = gpd.read_file(ARQ_COTAS).to_crs(c.CRS_PADRAO)
    cg["geometry"] = cg.geometry.buffer(0)
    d = cg.dissolve(by="cota_cm", aggfunc={"tr_anos": "first"}).sort_index()
    K = [int(k) for k in d.index]
    TR = {int(k): float(d.loc[k, "tr_anos"]) for k in d.index}
    disp = {k: d.loc[K[: i + 1], "geometry"].union_all().intersection(lim) for i, k in enumerate(K)}
    rot_cota = {k: f"cota {k} cm (TR {TR[k]:g} anos)".replace(".", ",") for k in K}

    end = gpd.read_file(CAM / "enderecos-acessibilidade-inundacao_osm-sgb-cnes-ibge_2022_pontos.gpkg")
    tr = gpd.read_file(CAM / "trechos-interrompidos-cotas_osm-sgb_atual_linhas.gpkg")
    pontes = gpd.read_file(CAM / "pontes-inventario_osm-sgb-bho_atual_linhas.gpkg")
    ilhas = gpd.read_file(CAM / "ilhas-rede-cotas_osm-sgb_atual_poligonos.gpkg")
    crit = gpd.read_file(CAM / "trechos-criticos_osm-sgb_atual_linhas.gpkg")
    cruz = gpd.read_file(CAM / "cruzamentos-via-curso-dagua_osm-bho_atual_pontos.gpkg")
    resp = pd.read_csv(c.RAIZ / "docs" / "acessibilidade_inundacao" / "tabelas" / "pontes-que-decidem-cenarios_osm-sgb-ibge_2022_ponte.csv")
    pontes_decidem = set(resp.loc[resp.pop_muda_de_classe_sem_ela > 0, "ponte"])
    unid = gpd.read_file(ARQ_UNIDADES).to_crs(c.CRS_PADRAO)
    todas_pontes = tr[tr.ponte]

    # janela ribeirinha: a mesma das rodadas 04–07 (quantis 5–95 % dos urbanos expostos à maior cota, + 350 m)
    exp_max = end[end[f"exp_cum_{K[-1]}"] & (end.zona == "urbana da sede")]
    x0, x1 = exp_max.geometry.x.quantile([0.05, 0.95])
    y0, y1 = exp_max.geometry.y.quantile([0.05, 0.95])
    exts = {"urbano": base.ext_urb, "ribeirinha": (x0 - 350, x1 + 350, y0 - 350, y1 + 350)}
    esc = {"urbano": 1000, "ribeirinha": 250}
    sub = {"urbano": "área urbana da sede", "ribeirinha": "faixa ribeirinha da área urbana (onde se concentram os endereços urbanos expostos à maior cota)"}
    ms = {"urbano": 0.5, "ribeirinha": 3.0}
    feitos = []

    def fig_ext(ext, largura=7.2):
        return plt.subplots(figsize=(largura, largura * (ext[3] - ext[2]) / (ext[1] - ext[0]) + 0.6))

    def plot_geom(ax, geom, **kw):
        gpd.GeoSeries([geom], crs=c.CRS_PADRAO).plot(ax=ax, **kw)

    def desenhar_fundo(ax, rec):
        fundo.desenhar(ax, exts[rec], "detalhe" if rec == "ribeirinha" else rec, esc[rec], modo_agua="enderecos", escala_pos=ESCALA_POS[rec])

    def saude(ax, rec, fs=6.5):
        ext = exts[rec]
        u = unid[unid.within(box(ext[0], ext[2], ext[1], ext[3]))]
        for classe, (mk, cor) in SIMB_UNIDADE.items():
            x = u[u.classe == classe]
            ax.scatter(x.geometry.x, x.geometry.y, marker=mk, s=TAM_UNIDADE[classe] + 4, color=cor, edgecolor=INK, linewidth=0.9, zorder=9)
        rotular_unidades(ax, u, c.escala_do_mapa(ext), fs)
        return [Line2D([], [], marker=mk, ls="", mfc=cor, mec=INK, ms=6.5,
                       label=f"unidade de saúde: {classe}" + (" (destino)" if classe in ("ESF", "UBS") else " (não é destino)"))
                for classe, (mk, cor) in SIMB_UNIDADE.items() if (u.classe == classe).any()]

    def interrompidos(ax, k, cen, lw=1.5):
        t = tr[tr[f"int_{k}"]]
        if cen == "oti":
            plot_lines(ax, t[t.ponte], color=COR_INTERROMP, lw=lw * 0.6, ls=(0, (2, 1.5)), z=6.2)
            t = t[~t.ponte]
        plot_lines(ax, t, color=COR_INTERROMP, lw=lw, z=6.1)

    def plot_lines(ax, g, color, lw, z, ls="-"):
        if len(g):
            g.plot(ax=ax, color=color, linewidth=lw, linestyle=ls, zorder=z)

    def marcar_pontes(ax, rec, rotulo=True, fs=6.5):
        ext = exts[rec]
        b = box(ext[0], ext[2], ext[1], ext[3])
        tp = todas_pontes[todas_pontes.intersects(b)]
        if len(tp):
            tp.plot(ax=ax, color=INK, linewidth=4.2, zorder=7.0)
            tp.plot(ax=ax, color=COR_PONTE, linewidth=2.6, zorder=7.1)
            # pontes têm 10–200 m: marcador no centro para ficarem visíveis na escala da área urbana
            cen = tp.geometry.centroid
            ax.scatter(cen.x, cen.y, marker="^", s=16 if rec == "urbano" else 30, color=COR_PONTE, edgecolor=INK, linewidth=0.8, zorder=7.2)
        if rotulo:
            halo = [pe.withStroke(linewidth=2.2, foreground="#ffffff")]
            # P01 e P03 ficam a ~150 m da ESF 04: na escala da área urbana, rótulos deslocados para não cobrir o "04"
            off = {"P01": (-48, 16), "P03": (30, -34)} if rec == "urbano" else {}
            for p in pontes[pontes.intersects(b)].itertuples():
                cx = p.geometry.centroid
                ax.annotate(p.ponte, (cx.x, cx.y), xytext=off.get(p.ponte, (5, -9)), textcoords="offset points", fontsize=fs, fontweight="bold", color=INK, zorder=11,
                            path_effects=halo, arrowprops=dict(arrowstyle="-", color=INK, lw=0.6) if p.ponte in off else None)
        return [Line2D([], [], color=COR_PONTE, lw=2.6, marker="^", mfc=COR_PONTE, mec=INK, ms=5,
                       path_effects=[pe.withStroke(linewidth=4.2, foreground=INK)], label="ponte/viaduto marcado no OpenStreetMap")]

    def rodape(ax, extra="", y=-0.015):
        ax.annotate(f"{FONTE}\n{NOTA}{extra}", xy=(0, y), xycoords="axes fraction", va="top", fontsize=6.0, color=MUTED)

    def salvar(fig, ax, nome, titulo, rec, hand, leg_tit, extra_meta, quadro=None, rod_extra=""):
        leg = ax.legend(handles=fundo.comum(hand), title=leg_tit, loc="upper left", bbox_to_anchor=(1.01, 1), frameon=False, fontsize=7.3,
                        title_fontsize=8, alignment="left")
        fig.canvas.draw()
        inv = ax.transAxes.inverted()
        y_leg = inv.transform(leg.get_window_extent(fig.canvas.get_renderer()))[0][1]
        y_min = y_leg
        if quadro:
            t = ax.text(1.02, y_leg - 0.03, quadro, transform=ax.transAxes, ha="left", va="top", fontsize=7.8, color=INK, zorder=12, clip_on=False,
                    bbox=dict(boxstyle="round,pad=0.4", facecolor="#ffffff", edgecolor="#8f8e88", linewidth=0.6))
            fig.canvas.draw()
            y_min = inv.transform(t.get_window_extent(fig.canvas.get_renderer()))[0][1]
        ax.set_title(f"{titulo}\n{sub[rec]}", loc="left", fontsize=10, color=INK)
        # rodapé abaixo do mapa e, se a legenda/quadro descerem além dele, abaixo deles (não sobrepõe)
        rodape(ax, rod_extra, y=min(-0.015, y_min - 0.03))
        caminho = MAPAS / f"{nome}_{rec}.png"
        fig.savefig(caminho, dpi=180, facecolor="#fcfcfb", bbox_inches="tight")
        plt.close(fig)
        meta(caminho, recorte=sub[rec], janela_m=[round(v) for v in exts[rec]], manchas_exibicao="cumulativas, recortadas no limite municipal só para exibição", **extra_meta)
        feitos.append(caminho)

    def contagem(k, cen, zona="urbana da sede"):
        e = end[end.zona == zona]
        cl = e[f"cls_{k}_{cen}"]
        out = {}
        for classe in ("exposto", "isolado", "com desvio"):
            out[classe] = (int((cl == classe).sum()), float(e.loc[cl == classe, "pop"].sum()))
        return out

    def texto_contagem(k, cen, curto=False):
        ct = contagem(k, cen)
        if curto:
            return " · ".join(f"{cl} ≈ {mil(p)}" for cl, (_, p) in ct.items())
        return "\n".join(f"{cl}: {mil(n)} end. · ≈ {mil(p)} pessoas" for cl, (n, p) in ct.items())

    # ---------------- (a) base
    for rec in exts:
        fig, ax = fig_ext(exts[rec])
        cl = np.searchsorted(FAIXAS_BASE[1:-1], end.dist_base_m.fillna(np.inf), side="right")
        for i, cor in enumerate(COR_BASE):
            x = end[(cl == i) & np.isfinite(end.dist_base_m)]
            ax.scatter(x.geometry.x, x.geometry.y, s=ms[rec] * 1.4, color=cor, linewidths=0, zorder=5 + 0.1 * i)
        desenhar_fundo(ax, rec)
        hand = [Line2D([], [], marker="o", ls="", color=cor, ms=5, label=f"{r} — {mil(((cl == i) & (end.zona == 'urbana da sede')).sum())} end. urbanos")
                for i, (cor, r) in enumerate(zip(COR_BASE, ROT_BASE))]
        hand += saude(ax, rec)
        salvar(fig, ax, "acessibilidade-base-distancia-rede_osm-cnes-ibge_2022_pontos", "Distância pela rede viária até a unidade ESF/UBS mais próxima — sem inundação",
               rec, hand, "distância pela rede (OSM, não direcionada),\npor endereço de domicílio particular", {"tema": "base"})

    # ---------------- (b) painel 2 x 2 por cenário
    hand_cls = [Patch(facecolor=COR_MANCHA, alpha=0.45, edgecolor=COR_BORDA, linewidth=0.5, label="mancha de inundação até a cota"),
                Line2D([], [], color=COR_INTERROMP, lw=1.5, label="trecho de via interrompido (≥ 5 m na mancha)")]
    hand_cls += [Line2D([], [], marker=MK_CLASSE[k], ls="", color=COR_CLASSE[k], mec="#ffffff" if k == "isolado" else COR_CLASSE[k], ms=5,
                        label=f"endereço {k}") for k in ("exposto", "isolado", "com desvio", "sem alteração")]
    for cen in CENARIOS:
        for rec in exts:
            ext = exts[rec]
            h = (ext[3] - ext[2]) / (ext[1] - ext[0])
            larg_ax, leg_h, rod_h, tit_h, sub_h = 5.4, 0.85, 0.6, 0.6, 0.3
            alt_ax = larg_ax * h
            W, H = 2 * larg_ax + 0.35, 2 * alt_ax + 0.15 + 2 * sub_h + leg_h + rod_h + tit_h
            fig, axs = plt.subplots(2, 2, figsize=(W, H))
            fig.subplots_adjust(left=0.1 / W, right=1 - 0.1 / W, top=1 - (tit_h + sub_h) / H, bottom=(leg_h + rod_h) / H,
                                wspace=0.15 / larg_ax, hspace=(0.15 + sub_h) / alt_ax)
            hs = []
            for ax, k in zip(axs.flat, K):
                plot_geom(ax, disp[k], color=COR_MANCHA, alpha=0.45, edgecolor="none", zorder=1.5)
                interrompidos(ax, k, cen, lw=1.2)
                f = 0.6
                for classe in ("sem alteração", "com desvio", "exposto", "isolado"):
                    x = end[end[f"cls_{k}_{cen}"] == classe]
                    s = ms[rec] * (1 if classe == "sem alteração" else 4) * f
                    ax.scatter(x.geometry.x, x.geometry.y, s=s, marker=MK_CLASSE[classe], color=COR_CLASSE[classe],
                               edgecolor="#ffffff" if classe == "isolado" else "none", linewidths=0.2, zorder={"sem alteração": 5, "com desvio": 6.5, "exposto": 6.6, "isolado": 6.8}[classe])
                desenhar_fundo(ax, rec)
                hp = marcar_pontes(ax, rec, rotulo=rec == "ribeirinha", fs=5.5)
                hs = saude(ax, rec, fs=5.5)
                ax.set_title(f"{rot_cota[k]} · {texto_contagem(k, cen, curto=True)} (área urbana, pessoas, estimativa)", loc="left", fontsize=6.6, color=INK, pad=4)
            fig.legend(handles=fundo.comum(hand_cls + hp + hs), loc="upper center", ncol=4, frameon=False, fontsize=7.2,
                       bbox_to_anchor=(0.5, (leg_h + rod_h - 0.05) / H))
            fig.suptitle(f"Acesso às unidades ESF/UBS com vias dentro da mancha, cota a cota — {sub[rec]}\n{DESC_CEN[cen]}", x=0.01, y=1 - 0.08 / H,
                         ha="left", va="top", fontsize=10.5, color=INK)
            fig.text(0.01, 0.06 / H, f"{FONTE}\n{NOTA}", fontsize=6.2, color=MUTED, va="bottom")
            caminho = MAPAS / f"acessibilidade-4-cotas-painel-{CENARIOS[cen]}_osm-sgb-cnes-ibge_2022_pontos_{rec}.png"
            fig.savefig(caminho, dpi=170, facecolor="#fcfcfb", bbox_inches="tight")
            plt.close(fig)
            meta(caminho, recorte=sub[rec], tema="painel_4_cotas", cenario=CENARIOS[cen])
            feitos.append(caminho)

    # ---------------- (c) cotas de referência: acréscimo de distância e ilhas
    for k in ARGS.cotas_mapa:
        for cen in CENARIOS:
            for rec in exts:
                fig, ax = fig_ext(exts[rec])
                plot_geom(ax, disp[k], color=COR_MANCHA, alpha=0.4, edgecolor="none", zorder=1.5)
                plot_geom(ax, disp[k].boundary, color=COR_BORDA, linewidth=0.4, zorder=1.6)
                interrompidos(ax, k, cen, lw=1.4 if rec == "ribeirinha" else 1.1)
                cls = end[f"cls_{k}_{cen}"]
                acr = end[f"acr_{k}_{cen}"]
                x = end[cls == "sem alteração"]
                ax.scatter(x.geometry.x, x.geometry.y, s=ms[rec], color=COR_CLASSE["sem alteração"], linewidths=0, zorder=5)
                x = end[cls == "exposto"]
                ax.scatter(x.geometry.x, x.geometry.y, s=ms[rec] * 2.5, color=COR_CLASSE["exposto"], linewidths=0, zorder=5.5)
                fx = np.searchsorted(FAIXAS_ACR[1:-1], acr.fillna(-1), side="right")
                hand = [Patch(facecolor=COR_MANCHA, alpha=0.4, edgecolor=COR_BORDA, linewidth=0.4, label=f"mancha até a {rot_cota[k]}"),
                        Line2D([], [], color=COR_INTERROMP, lw=1.4, label="trecho de via interrompido (≥ 5 m na mancha)")]
                if cen == "oti":
                    hand.append(Line2D([], [], color=COR_INTERROMP, lw=0.9, ls=(0, (2, 1.5)), label="ponte na mancha mantida (otimista)"))
                for i, (cor, r) in enumerate(zip(COR_ACR, ROT_ACR)):
                    sel = (cls == "com desvio") & (fx == i)
                    x = end[sel]
                    ax.scatter(x.geometry.x, x.geometry.y, s=ms[rec] * 4.5, color=cor, edgecolor="#ffffff", linewidths=0.15, zorder=6 + 0.05 * i)
                    hand.append(Line2D([], [], marker="o", ls="", color=cor, ms=5, label=f"desvio {r} — {mil((sel & (end.zona == 'urbana da sede')).sum())} end. urbanos"))
                x = end[cls == "isolado"]
                ax.scatter(x.geometry.x, x.geometry.y, s=ms[rec] * 5, marker="s", color=COR_CLASSE["isolado"], edgecolor="#ffffff", linewidths=0.3, zorder=6.8)
                il = ilhas[(ilhas.cota_cm == k) & (ilhas.cenario == CENARIOS[cen])]
                il_rede = il[il.tipo.str.startswith("parte")]
                il_peda = il[~il.tipo.str.startswith("parte")]
                if len(il_rede):
                    il_rede.boundary.plot(ax=ax, color=INK, linewidth=1.1, linestyle=(0, (3, 1.5)), zorder=6.9)
                hand += [Line2D([], [], marker="s", ls="", color=COR_CLASSE["isolado"], mec="#ffffff", ms=5, label="endereço isolado (sem caminho até unidade)"),
                         Line2D([], [], marker="o", ls="", color=COR_CLASSE["exposto"], ms=4, label="endereço exposto (dentro da mancha)"),
                         Line2D([], [], marker="o", ls="", color=COR_CLASSE["sem alteração"], ms=4, label="endereço sem alteração"),
                         Line2D([], [], color=INK, lw=1.1, ls=(0, (3, 1.5)), label=f"ilha: parte da rede sem ligação ({len(il_rede)})"),
                         Line2D([], [], color="none", label=f"+ {len(il_peda)} pedaços de rua secos entre partes\nalagadas da mesma rua (sem contorno)")]
                desenhar_fundo(ax, rec)
                hand += marcar_pontes(ax, rec, rotulo=rec == "ribeirinha")
                hand += saude(ax, rec)
                quadro = f"{rot_cota[k]}\n{CENARIOS[cen]}\nárea urbana da sede (estimativa):\n{texto_contagem(k, cen)}"
                salvar(fig, ax, f"acessibilidade-acrescimo-distancia-cota{k}-{CENARIOS[cen]}_osm-sgb-cnes-ibge_2022_pontos",
                       f"Acréscimo de distância até a unidade ESF/UBS mais próxima — cota {k} cm, {CENARIOS[cen]}", rec, hand,
                       "classe do endereço e acréscimo de distância\npela rede (em relação à situação sem inundação)",
                       {"tema": "acrescimo_distancia", "cota_cm": k, "cenario": CENARIOS[cen], "ilhas": len(il)}, quadro=quadro,
                       rod_extra="\n" + DESC_CEN[cen] + ".")

    # ---------------- (d) pontes
    for rec in exts:
        ext = exts[rec]
        fig, ax = fig_ext(ext)
        for i, k in reversed(list(enumerate(K))):
            plot_geom(ax, disp[k], color=["#7b3f8f", "#b06aa8", "#dba3c4", "#f6d7e3"][i], alpha=0.55, edgecolor="none", zorder=1.5 + 0.1 * (len(K) - i))
        b = box(ext[0], ext[2], ext[1], ext[3])
        cz = cruz[cruz.within(b)]
        desenhar_fundo(ax, rec)
        hand = [Patch(facecolor=cor, alpha=0.55, edgecolor="none", label=f"mancha até a {rot_cota[k]}") for cor, k in zip(["#7b3f8f", "#b06aa8", "#dba3c4", "#f6d7e3"], K)]
        hand += marcar_pontes(ax, rec, rotulo=True, fs=7.5)
        dec = pontes[pontes.ponte.isin(pontes_decidem) & pontes.intersects(b)]
        if len(dec):
            dec.buffer(25).boundary.plot(ax=ax, color="#d7191c", linewidth=1.6, zorder=7.5)
        hand.append(Line2D([], [], color="#d7191c", lw=1.6, label=f"ponte que muda o resultado entre cenários ({len(pontes_decidem)} no total)"))
        sem = cz[~cz.com_ponte.astype(bool)]
        if len(sem):
            ax.scatter(sem.geometry.x, sem.geometry.y, marker="X", s=40 if rec == "ribeirinha" else 22, color="#ffffff", edgecolor=INK, linewidth=0.9, zorder=8)
        hand.append(Line2D([], [], marker="X", ls="", mfc="#ffffff", mec=INK, ms=7, label="via cruza curso d'água (BHO) sem ponte marcada no OSM"))
        hand += saude(ax, rec)
        n_inv = len(pontes)
        quadro = (f"{n_inv} pontes do OSM a até 300 m da maior mancha\n(P01–P{n_inv:02d}, como na tabela);\n"
                  f"{len(pontes_decidem)} muda(m) o resultado entre os cenários:\nnas pontes dentro da mancha, as ruas de\nchegada também ficam dentro da mancha.")
        salvar(fig, ax, "pontes-inventario-manchas_osm-sgb-bho_atual_linhas", "Pontes e viadutos do OpenStreetMap junto às manchas de inundação — lista para conferência em campo",
               rec, hand, "pontes, cruzamentos e manchas por cota\n(manchas cumulativas)", {"tema": "pontes", "pontes_inventario": n_inv,
               "pontes_que_decidem": sorted(pontes_decidem), "cruzamentos_sem_ponte_no_quadro": len(sem)}, quadro=quadro,
               rod_extra="\nCurso d'água: BHO/ANA (derivada de modelo de terreno; pode não coincidir com o traçado real). Cruzamento sem ponte marcada = bueiro, ponte não mapeada ou desalinhamento.")

    # ---------------- (e) trechos críticos
    for k in ARGS.cotas_mapa:
        ck = crit[crit.cota_cm == k].sort_values("posicao")
        for rec in exts:
            ext = exts[rec]
            fig, ax = fig_ext(ext)
            plot_geom(ax, disp[k], color=COR_MANCHA, alpha=0.35, edgecolor="none", zorder=1.5)
            t = tr[tr[f"int_{k}"]]
            plot_lines(ax, t, color="#c994c7", lw=1.0, z=5.5)
            vmax = ck.pop_caminho_base.max()
            for r in ck.itertuples():
                w = 1.5 + 4.5 * r.pop_caminho_base / vmax
                gpd.GeoSeries([r.geometry], crs=c.CRS_PADRAO).plot(ax=ax, color=COR_INTERROMP, linewidth=w, zorder=6.5)
            desenhar_fundo(ax, rec)
            halo = [pe.withStroke(linewidth=2.4, foreground="#ffffff")]
            b = box(ext[0], ext[2], ext[1], ext[3])
            usados = []
            jan = box(exts["ribeirinha"][0], exts["ribeirinha"][2], exts["ribeirinha"][1], exts["ribeirinha"][3])
            if rec == "urbano":  # na área urbana os trechos da faixa ribeirinha ficam juntos demais: numerados no mapa da faixa
                gpd.GeoSeries([jan.boundary], crs=c.CRS_PADRAO).plot(ax=ax, color=INK, linewidth=0.7, linestyle=(0, (4, 2)), zorder=8)
            for r in ck.itertuples():
                if not r.geometry.intersects(b) or (rec == "urbano" and r.geometry.within(jan)):
                    continue
                p = r.geometry.interpolate(0.5, normalized=True)
                # afasta rótulos que cairiam um sobre o outro: testa posições em pixels até achar uma livre
                px, py = ax.transData.transform((p.x, p.y)) * 72 / fig.dpi  # em pontos tipográficos
                for dx, dy in ((4, 4), (4, -12), (-16, 4), (-16, -12), (10, 14), (-8, 16), (14, -4), (-22, -4)):
                    if all(abs(px + dx - ux) > 13 or abs(py + dy - uy) > 10 for ux, uy in usados):
                        break
                usados.append((px + dx, py + dy))
                ax.annotate(str(r.posicao), (p.x, p.y), xytext=(dx, dy), textcoords="offset points", fontsize=7 if rec == "ribeirinha" else 6,
                            fontweight="bold", color=INK, zorder=11, path_effects=halo)
            fora = [str(r.posicao) for r in ck.itertuples() if not r.geometry.intersects(b)]
            hand = [Patch(facecolor=COR_MANCHA, alpha=0.35, edgecolor="none", label=f"mancha até a {rot_cota[k]}"),
                    Line2D([], [], color="#c994c7", lw=1.0, label="outro trecho interrompido"),
                    Line2D([], [], color=COR_INTERROMP, lw=4, label=f"{len(ck)} trechos críticos (largura ∝ pessoas;\nnúmero = posição na tabela)")]
            hand += marcar_pontes(ax, rec, rotulo=False)
            hand += saude(ax, rec)
            linhas = [f"{r.posicao:>2}. {r.via[:36]}{' (ponte ' + r.ponte_id + ')' if r.ponte else ''} — ≈ {mil(r.pop_caminho_base)}" for r in ck.itertuples()]
            quadro = f"{rot_cota[k]} — pessoas cujo caminho\nde base passava pelo trecho:\n" + "\n".join(linhas)
            if fora:
                quadro += f"\nfora deste enquadramento: {', '.join(fora)}"
            if rec == "urbano":
                hand.append(Line2D([], [], color=INK, lw=0.7, ls=(0, (4, 2)), label="faixa ribeirinha: trechos numerados\nno mapa da faixa"))
            salvar(fig, ax, f"trechos-criticos-cota{k}_osm-sgb-ibge_2022_linhas", f"Trechos interrompidos por onde passava o caminho de mais pessoas até a unidade — cota {k} cm",
                   rec, hand, "trechos críticos (interrompidos no cenário\npessimista; caminho de base sem inundação)", {"tema": "trechos_criticos", "cota_cm": k, "fora_do_quadro": fora},
                   quadro=quadro)
    for f in feitos:
        logger.info("Mapa: %s", f.relative_to(c.RAIZ))


if __name__ == "__main__":
    _p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    _p.add_argument("--codigo-ibge", default=c.CODIGO_IBGE_DEFAULT)
    _p.add_argument("--nome-rio", default=c.NOME_RIO_DEFAULT)
    _p.add_argument("--cotas-mapa", type=int, nargs="*", default=[1205, 1252])
    ARGS = _p.parse_args()
    main()
