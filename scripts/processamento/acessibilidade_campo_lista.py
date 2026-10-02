"""
Lista de campo das pontes e travessias junto às manchas de inundação
(rodada 09). Lê as camadas já calculadas por
scripts/processamento/acessibilidade_inundacao.py — nada é recalculado:
  - pontes do OpenStreetMap a até 300 m da maior mancha (P01–P05);
  - cruzamentos de via com curso d'água da BHO SEM ponte marcada no OSM,
    numerados T01–Tnn de oeste para leste.

Saídas em docs/acessibilidade_inundacao/campo/ (cada uma com .json irmão):
  - planilha CSV e GeoJSON (WGS 84) com as colunas em branco para o campo;
  - roteiro em Markdown, em ordem de visita (vizinho mais próximo a partir do
    ponto mais a oeste), com link de mapa por coordenada;
  - mapa de localização dos pontos na área urbana da sede.
Nenhum dado pessoal.

Uso:
  python scripts/processamento/acessibilidade_campo_lista.py --codigo-ibge 4322400
"""

from __future__ import annotations

import argparse
import csv
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
from dinamica_populacional_cnefe_mapas import Fundo  # noqa: E402
from dinamica_populacional_mapas import INK, MUTED, Base  # noqa: E402
from exposicao_inundacao_enderecos import SIMB_UNIDADE, TAM_UNIDADE  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

SCRIPT = "scripts/processamento/acessibilidade_campo_lista.py"
CAM = c.RAIZ / "data" / "processed" / "acessibilidade_inundacao"
SAIDA = c.RAIZ / "docs" / "acessibilidade_inundacao" / "campo"
ARQ_COTAS = c.RAW / "vetor" / "cotas-inundacao_sgb_atual_vetorial.gpkg"
ARQ_UNIDADES = c.RAIZ / "data" / "processed" / "saude" / "unidades-saude-esf-ubs_cnes-revisado-v4_2026_pontos.gpkg"
NOME_BASE = "pontes-travessias-campo_osm-bho-sgb_2026"
CAMPOS_CAMPO = ["o_que_existe (ponte/bueiro/passagem molhada/nada)", "material", "extensao_aprox_m", "altura_tabuleiro_ou_via_sobre_leito_m",
                "largura_curso_dagua_m", "transitavel_veiculo (sim/nao)", "transitavel_a_pe (sim/nao)", "foto_arquivo", "data", "observacao"]
FONTE = ("Fonte: OpenStreetMap — vias e pontes (contribuidores do OpenStreetMap); ANA — Base Hidrográfica Ottocodificada (curso d'água); "
         "SGB — manchas de inundação por cota; IBGE — bairros (malha de setores 2022).")


def meta(caminho, **kw):
    c.gravar_meta(caminho, codigo_ibge=ARGS.codigo_ibge, crs=c.CRS_PADRAO, status=c.STATUS_CONFERENCIA, ligado_ao_portal=False, script=SCRIPT,
                  fonte=FONTE, camadas_de_entrada="data/processed/acessibilidade_inundacao/ (pontes e cruzamentos; fora do git)",
                  dado_pessoal="nenhum", **kw)


def main() -> None:
    SAIDA.mkdir(parents=True, exist_ok=True)
    cg = gpd.read_file(ARQ_COTAS).to_crs(c.CRS_PADRAO)
    cg["geometry"] = cg.geometry.buffer(0)
    d = cg.dissolve(by="cota_cm").sort_index()
    K = [int(k) for k in d.index]
    manchas = {k: d.loc[K[: i + 1], "geometry"].union_all() for i, k in enumerate(K)}
    st = gpd.read_file(c.CAMADAS / "populacao-setores_ibge-censo_2022_setor.gpkg")[["NM_BAIRRO", "SITUACAO", "geometry"]].to_crs(c.CRS_PADRAO)

    pontes = gpd.read_file(CAM / "pontes-inventario_osm-sgb-bho_atual_linhas.gpkg").to_crs(c.CRS_PADRAO)
    cruz = gpd.read_file(CAM / "cruzamentos-via-curso-dagua_osm-bho_atual_pontos.gpkg").to_crs(c.CRS_PADRAO)
    sem = cruz[~cruz.com_ponte.astype(bool)].copy()
    sem = sem.assign(_x=sem.geometry.x).sort_values("_x").reset_index(drop=True)  # T01.. de oeste para leste

    def menor_cota(g):
        return next((str(k) for k in K if g.intersects(manchas[k])), "fora")

    lin = []
    for p in pontes.itertuples():
        ordem = p.curso_dagua.split("(ordem ")[1].rstrip(")") if "(ordem " in p.curso_dagua else "sem curso d'água mapeado a até 40 m"
        lin.append({"codigo": p.ponte, "tipo": "ponte do OpenStreetMap", "via": p.via, "geom_ref": p.geometry, "ponto": p.geometry.interpolate(0.5, normalized=True),
                    "menor_cota_cm": menor_cota(p.geometry), "ordem_curso_dagua": ordem, "comprimento_osm_m": p.comprimento_m})
    for i, r in sem.iterrows():
        lin.append({"codigo": f"T{i + 1:02d}", "tipo": "travessia sem ponte marcada", "via": r.via, "geom_ref": r.geometry, "ponto": r.geometry,
                    "menor_cota_cm": menor_cota(r.geometry), "ordem_curso_dagua": str(int(r.ordem)), "comprimento_osm_m": None})
    g = gpd.GeoDataFrame(lin, geometry="ponto", crs=c.CRS_PADRAO)
    j = gpd.sjoin(g[["ponto"]], st, predicate="within", how="left")
    j = j[~j.index.duplicated()]
    g["bairro"] = np.where(j.SITUACAO == "Rural", "(área rural)", j.NM_BAIRRO.fillna("(sem bairro)"))
    w = g.to_crs("EPSG:4326")
    g["latitude"] = w.geometry.y.round(6)
    g["longitude"] = w.geometry.x.round(6)

    # ordem de visita: vizinho mais próximo a partir do ponto mais a oeste (distância em linha reta)
    xy = np.c_[g.geometry.x, g.geometry.y]
    falta = set(range(len(g)))
    atual = int(np.argmin(xy[:, 0]))
    ordem = []
    while falta:
        ordem.append(atual)
        falta.discard(atual)
        if falta:
            atual = min(falta, key=lambda k: np.hypot(*(xy[k] - xy[atual])))
    g["ordem_visita"] = 0
    g.loc[ordem, "ordem_visita"] = range(1, len(g) + 1)
    perc_km = sum(np.hypot(*(xy[b] - xy[a])) for a, b in zip(ordem[:-1], ordem[1:])) / 1000

    cols = ["codigo", "tipo", "via", "bairro", "latitude", "longitude", "menor_cota_cm", "ordem_curso_dagua", "comprimento_osm_m", "ordem_visita"]
    tab = g[cols].copy()
    for cpo in CAMPOS_CAMPO:
        tab[cpo] = ""
    arq = SAIDA / f"{NOME_BASE}_pontos.csv"
    with open(arq, "w", newline="\n", encoding="utf-8") as f:
        wr = csv.writer(f, lineterminator="\n")
        wr.writerow(tab.columns)
        for row in tab.itertuples(index=False):
            wr.writerow(["" if (v is None or (isinstance(v, float) and np.isnan(v))) else v for v in row])
    meta(arq, descricao=f"planilha de campo: {len(pontes)} pontes do OSM e {len(sem)} travessias sem ponte marcada, com colunas em branco para o campo",
         menor_cota="menor cota cuja mancha cumulativa do SGB alcança o ponto (ponte: a linha da ponte); 'fora' = nenhuma das quatro",
         numeracao="P01–P05 como na tabela de pontes da rodada 08; T01–Tnn de oeste para leste", colunas_campo=CAMPOS_CAMPO)
    gj = gpd.GeoDataFrame(tab, geometry=gpd.points_from_xy(tab.longitude, tab.latitude), crs="EPSG:4326")
    arq = SAIDA / f"{NOME_BASE}_pontos.geojson"
    gj.to_file(arq, driver="GeoJSON")
    meta(arq, descricao="os mesmos pontos da planilha, em WGS 84 (EPSG:4326) para GPS/celular", crs_arquivo="EPSG:4326")

    # roteiro de uma página
    rot = g.sort_values("ordem_visita")
    km = f"{perc_km:.1f}".replace(".", ",")
    linhas = ["# Roteiro de campo — pontes e travessias junto às manchas de inundação", "",
              f"{len(rot)} pontos ({len(pontes)} pontes do OpenStreetMap, P01–P{len(pontes):02d}; {len(sem)} travessias de via com curso d'água "
              f"sem ponte marcada, T01–T{len(sem):02d}). Ordem de visita por proximidade, a partir do ponto mais a oeste "
              f"(cerca de {km} km em linha reta). Planilha: `{NOME_BASE}_pontos.csv`; para GPS: `{NOME_BASE}_pontos.geojson`.",
              "", f"Mapa de localização (área urbana da sede, com quadros de detalhe): [`{NOME_BASE}_localizacao_urbano.png`]({NOME_BASE}_localizacao_urbano.png).",
              "", "| # | Código | Via | Bairro | Mancha desde | Mapa |", "|---|---|---|---|---|---|"]
    for r in rot.itertuples():
        url = f"https://www.openstreetmap.org/?mlat={r.latitude}&mlon={r.longitude}#map=18/{r.latitude}/{r.longitude}"
        cota = f"{r.menor_cota_cm} cm" if r.menor_cota_cm != "fora" else "fora"
        linhas.append(f"| {r.ordem_visita} | {r.codigo} | {r.via} | {r.bairro} | {cota} | [{r.latitude}, {r.longitude}]({url}) |")
    linhas += ["", "**Em cada ponto, anotar na planilha:** o que existe (ponte, bueiro, passagem molhada ou nada); material; extensão "
               "aproximada (m); altura do tabuleiro ou da via sobre o leito (m, estimada); largura do curso d'água (m); se é transitável "
               "por veículo e a pé; nome do arquivo da foto; data; observação. Fotografar de montante e de jusante.", "",
               "Travessia = lugar onde a via cruza um curso d'água da base hidrográfica da ANA sem ponte marcada no OpenStreetMap: pode ser "
               "bueiro, ponte não mapeada ou desalinhamento da base (nesse caso, anotar \"nada\" e o que houver por perto). "
               "\"Mancha desde\" = menor cota do rio cuja mancha do SGB alcança o ponto. Produto de trabalho — pendente de conferência."]
    arq = SAIDA / f"{NOME_BASE}_roteiro.md"
    arq.write_text("\n".join(linhas) + "\n", encoding="utf-8")
    meta(arq, descricao="roteiro de campo de uma página, em ordem de visita, com link de mapa (OpenStreetMap) por coordenada",
         ordem_visita="vizinho mais próximo em linha reta a partir do ponto mais a oeste", percurso_linha_reta_km=round(perc_km, 1))

    # mapa de localização (estilo da rodada 08): área urbana + quadros de detalhe nos aglomerados de pontos
    base = Base(ARGS.codigo_ibge, agua=True, nome_rio=ARGS.nome_rio, modo_agua="enderecos")
    fundo = Fundo(base)
    lim = base.limite.union_all()
    disp = {k: manchas[k].intersection(lim) for k in K}
    unid = gpd.read_file(ARQ_UNIDADES).to_crs(c.CRS_PADRAO)
    cores = ["#7b3f8f", "#b06aa8", "#dba3c4", "#f6d7e3"]
    ext = base.ext_urb
    # aglomerados: ligação simples a até 400 m; os de 4 pontos ou mais ganham quadro de detalhe (A, B, ...)
    grupo = list(range(len(g)))
    for a in range(len(g)):
        for b in range(a + 1, len(g)):
            if np.hypot(*(xy[a] - xy[b])) <= 400 and grupo[a] != grupo[b]:
                ga, gb = grupo[a], grupo[b]
                grupo = [ga if x == gb else x for x in grupo]
    g["_grupo"] = grupo
    det = []
    for _, idx in g.groupby("_grupo").groups.items():
        if len(idx) >= 4:
            x0, y0, x1, y1 = g.loc[idx].total_bounds
            m = 150
            w_, h_ = x1 - x0 + 2 * m, y1 - y0 + 2 * m
            lado = max(w_, h_ * 1.3)  # quadro mais largo que alto
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            det.append((cx - lado / 2, cx + lado / 2, cy - lado / 2.6, cy + lado / 2.6, set(g.loc[idx, "codigo"])))
    det.sort(key=lambda e: e[0])
    nomes_det = [chr(65 + i) for i in range(len(det))]

    def desenhar(ax, e, rec, escala, fs, rotular):
        for i, k in reversed(list(enumerate(K))):
            gpd.GeoSeries([disp[k]], crs=c.CRS_PADRAO).plot(ax=ax, color=cores[i], alpha=0.5, edgecolor="none", zorder=1.5 + 0.1 * (len(K) - i))
        fundo.desenhar(ax, e, rec, escala, modo_agua="enderecos", escala_pos=(0.04, 0.05))
        u = unid[unid.within(box(e[0], e[2], e[1], e[3]))]
        for classe, (mk, cor) in SIMB_UNIDADE.items():
            x = u[u.classe == classe]
            ax.scatter(x.geometry.x, x.geometry.y, marker=mk, s=TAM_UNIDADE[classe] * 0.6, color=cor, edgecolor=INK, linewidth=0.7, zorder=8, alpha=0.8)
        pp = g[g.tipo.str.startswith("ponte")]
        tt = g[~g.tipo.str.startswith("ponte")]
        f = 1.0 if rec == "urbano" else 1.6
        ax.scatter(tt.geometry.x, tt.geometry.y, marker="X", s=30 * f, color="#ffffff", edgecolor=INK, linewidth=0.9, zorder=9)
        ax.scatter(pp.geometry.x, pp.geometry.y, marker="^", s=42 * f, color="#ffffff", edgecolor=INK, linewidth=1.0, zorder=9)
        halo = [pe.withStroke(linewidth=2.2, foreground="#ffffff")]
        usados = []
        bx = box(e[0], e[2], e[1], e[3])
        for r in g.sort_values("codigo").itertuples():
            if r.codigo not in rotular or not bx.contains(r.ponto):
                continue
            px, py = ax.transData.transform((r.ponto.x, r.ponto.y)) * 72 / ax.figure.dpi
            for dx, dy in ((5, 3), (5, -10), (-22, 3), (-22, -10), (5, 12), (-22, 12), (5, -19), (-22, -19), (16, -3), (-34, -3)):
                if all(abs(px + dx - ux) > 19 or abs(py + dy - uy) > 9 for ux, uy in usados):
                    break
            usados.append((px + dx, py + dy))
            longe = abs(dx) > 20 or abs(dy) > 14  # rótulo afastado: linha de chamada até o ponto
            ax.annotate(r.codigo, (r.ponto.x, r.ponto.y), xytext=(dx, dy), textcoords="offset points", fontsize=fs, fontweight="bold", color=INK,
                        zorder=11, path_effects=halo, arrowprops=dict(arrowstyle="-", color=INK, lw=0.5, shrinkA=0, shrinkB=3) if longe else None)
        return pp, tt

    nos_det = set().union(*[d_[4] for d_ in det]) if det else set()
    larg = 7.2
    h_main = larg * (ext[3] - ext[2]) / (ext[1] - ext[0])
    ncol = 2  # quadros de detalhe em 2 colunas (escala maior que numa fileira só)
    nlin = -(-len(det) // ncol) if det else 0
    w_det = larg / ncol
    h_det = nlin * ((w_det - 0.2) / 1.3 + 0.3)
    fig = plt.figure(figsize=(larg + 3.2, h_main + h_det + 1.4))
    ax = fig.add_axes([0.02, (h_det + 1.0) / (h_main + h_det + 1.4), larg / (larg + 3.2), h_main / (h_main + h_det + 1.4)])
    pp, tt = desenhar(ax, ext, "urbano", 1000, 5.8, set(g.codigo) - nos_det)
    halo = [pe.withStroke(linewidth=2.4, foreground="#ffffff")]
    for nm, (a0, a1, b0, b1, _) in zip(nomes_det, det):
        gpd.GeoSeries([box(a0, b0, a1, b1).boundary], crs=c.CRS_PADRAO).plot(ax=ax, color=INK, linewidth=0.9, zorder=10)
        ax.annotate(nm, (a0, b1), xytext=(2, 3), textcoords="offset points", fontsize=9, fontweight="bold", color=INK, zorder=11, path_effects=halo)
    H = h_main + h_det + 1.4
    hq = (w_det - 0.2) / 1.3
    for i, (nm, (a0, a1, b0, b1, cods)) in enumerate(zip(nomes_det, det)):
        lin_, col_ = divmod(i, ncol)
        y_ax = 0.75 + (nlin - 1 - lin_) * (hq + 0.3)
        axd = fig.add_axes([0.02 + col_ * w_det / (larg + 3.2), y_ax / H, (w_det - 0.2) / (larg + 3.2), hq / H])
        lado = a1 - a0
        desenhar(axd, (a0, a1, b0, b1), "detalhe", 100 if lado < 1500 else 250, 6.8, cods)
        axd.set_title(f"detalhe {nm}", loc="left", fontsize=8, color=INK, pad=2)
    hand = [Line2D([], [], marker="^", ls="", mfc="#ffffff", mec=INK, ms=7, label=f"ponte do OpenStreetMap ({len(pp)})"),
            Line2D([], [], marker="X", ls="", mfc="#ffffff", mec=INK, ms=7, label=f"travessia sem ponte marcada ({len(tt)})")]
    hand += [Patch(facecolor=cores[i], alpha=0.5, edgecolor="none", label=f"mancha até a cota {k} cm") for i, k in enumerate(K)]
    hand += [Line2D([], [], marker=mk, ls="", mfc=cor, mec=INK, ms=5.5, alpha=0.8, label=f"unidade de saúde: {cl}")
             for cl, (mk, cor) in SIMB_UNIDADE.items() if (unid.classe == cl).any() and cl in ("ESF", "a confirmar")]
    hand.append(Line2D([], [], color=INK, lw=0.9, label="quadro de detalhe (A, B, ...)"))
    ax.legend(handles=fundo.comum(hand), title="pontos para conferência em campo\n(código = planilha e roteiro)", loc="upper left",
              bbox_to_anchor=(1.01, 1), frameon=False, fontsize=7.3, title_fontsize=8, alignment="left")
    ax.set_title("Pontes e travessias para conferência em campo — área urbana da sede", loc="left", fontsize=10, color=INK)
    fora = [r.codigo for r in g.itertuples() if not box(ext[0], ext[2], ext[1], ext[3]).contains(r.ponto)]
    fig.text(0.02, 0.05 / (h_main + h_det + 1.4), f"{FONTE}\nManchas cumulativas (união das cotas ≤ X), recortadas no limite municipal só para exibição. "
             f"Fora deste mapa: {', '.join(fora) or 'nenhum'} (coordenadas na planilha). Produto de trabalho — pendente de conferência. EPSG:31981.",
             va="bottom", fontsize=6.0, color=MUTED)
    arq = SAIDA / f"{NOME_BASE}_localizacao_urbano.png"
    fig.savefig(arq, dpi=200, facecolor="#fcfcfb", bbox_inches="tight")
    plt.close(fig)
    meta(arq, recorte="área urbana da sede, com quadros de detalhe dos aglomerados (ligação a até 400 m, 4 pontos ou mais)", pontos=len(g),
         fora_do_enquadramento=fora, quadros_de_detalhe={nm: sorted(d_[4]) for nm, d_ in zip(nomes_det, det)})
    print(tab[cols].to_string(index=False))
    logger.info("Percurso em linha reta: %.1f km; fora do mapa: %s", perc_km, fora)


if __name__ == "__main__":
    _p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    _p.add_argument("--codigo-ibge", default=c.CODIGO_IBGE_DEFAULT)
    _p.add_argument("--nome-rio", default=c.NOME_RIO_DEFAULT)
    ARGS = _p.parse_args()
    main()
