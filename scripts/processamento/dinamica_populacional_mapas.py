"""
Mapas de trabalho (PNG) da caracterização da dinâmica populacional —
Tarefas 2 e 3. Todos "pendente de conferência": não vão para data/geoportal/.

Para cada tema: município inteiro e recorte da área urbana da sede, com
limite municipal (config/area_estudo.geojson) e hidrografia (BHO/ANA em
data/raw/vetor). Paletas legíveis para daltônicos:
  - sequencial (um só matiz, azul claro -> escuro) para densidade, idade,
    domicílios unipessoais e população na grade;
  - divergente centrada em zero (vermelho = perda, cinza = estável,
    azul = ganho) para a mudança 2010–2022.
Classes e limiares fixos, escritos na legenda. Valor ausente (sigilo) em
cinza-claro hachurado, nunca pintado como zero.

Opções da rodada 02 (o padrão continua produzindo os mapas originais em
docs/dinamica_populacional/mapas/):
  --sem-centro  não desenha o símbolo do centro da cidade (o centro segue só
                como parâmetro de cálculo das faixas de distância);
  --saida DIR   pasta de saída dos PNG e .json (padrão: mapas/).
Opções da rodada 03:
  --sem-centro-medio  não desenha os marcadores do centro médio da população
                      (2010 e 2022); o deslocamento fica no texto e nas tabelas;
  --com-agua          desenha a área de água (OSM) por cima dos polígonos
                      temáticos, com a margem, e o limite municipal por cima de
                      tudo — só apresentação: nenhuma área ou contagem muda;
  --nome-rio TEXTO    rótulo do rio principal na legenda (padrão "rio Uruguai").

Uso:
  python scripts/processamento/dinamica_populacional_mapas.py --codigo-ibge 4322400
  python scripts/processamento/dinamica_populacional_mapas.py --codigo-ibge 4322400 \
      --sem-centro --sem-centro-medio --com-agua --saida docs/dinamica_populacional/mapas_v2
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import geopandas as gpd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

import dinamica_populacional_comum as c  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

SEQ = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#104281"]
DIV7 = ["#b2182b", "#ef8a62", "#fddbc7", "#f0efec", "#d1e5f0", "#67a9cf", "#2166ac"]
DIV5 = ["#b2182b", "#ef8a62", "#f0efec", "#67a9cf", "#2166ac"]
AUSENTE = "#e1e0d9"
# área de água em azul-esverdeado claro (distinto da rampa azul das paletas sequenciais) e linha da margem
AGUA_AREA, AGUA_MARGEM = "#c4e6e3", "#2f7f7a"
INK, MUTED, AGUA = "#0b0b0b", "#898781", "#5b8fb9"


def classificar(v, limites):
    """Índice da classe [l_i, l_{i+1}); NaN -> -1."""
    out = np.full(len(v), -1)
    vv = np.asarray(v, dtype=float)
    ok = np.isfinite(vv)
    out[ok] = np.clip(np.searchsorted(limites[1:-1], vv[ok], side="right"), 0, len(limites) - 2)
    return out


def rotulos(limites, fmt="{:g}", unidade=""):
    r = []
    for i in range(len(limites) - 1):
        a, b = limites[i], limites[i + 1]
        if not np.isfinite(a):
            r.append(f"< {fmt.format(b)}{unidade}")
        elif not np.isfinite(b):
            r.append(f"≥ {fmt.format(a)}{unidade}")
        else:
            r.append(f"{fmt.format(a)} a < {fmt.format(b)}{unidade}")
    return [x.replace("-", "−") for x in r]


class Base:
    def __init__(self, cod: str, agua: bool = False, nome_rio: str = c.NOME_RIO_DEFAULT, modo_agua: str = "tematico"):
        self.limite = c.carregar_area_estudo()
        self.agua = c.area_agua(self.limite) if agua else None
        self.nome_rio = nome_rio
        self.modo_agua = modo_agua  # regra de exibição das outras águas (c.AGUA_EXIBICAO)
        hid = gpd.read_file(c.RAW / "vetor" / "rede-hidrografica_ana-bho_atual_vetorial.gpkg", layer="curso_dagua").to_crs(c.CRS_PADRAO)
        buf = self.limite.buffer(5000).union_all()
        self.hid = hid[hid.intersects(buf)]
        st = gpd.read_file(c.CAMADAS / "populacao-setores_ibge-censo_2022_setor.gpkg")
        sede = st.loc[st["pop"].idxmax(), "CD_DIST"]
        urb = st[(st.SITUACAO == "Urbana") & (st.CD_DIST == sede)]
        x0, y0, x1, y1 = urb.total_bounds
        m = 600
        self.ext_urb = (x0 - m, x1 + m, y0 - m, y1 + m)
        x0, y0, x1, y1 = self.limite.total_bounds
        self.ext_mun = (x0 - 2000, x1 + 2000, y0 - 2000, y1 + 2000)
        self.cod = cod

    def desenhar(self, ax, recorte: str):
        ext = self.ext_urb if recorte == "urbano" else self.ext_mun
        hid = self.hid if recorte == "urbano" else self.hid[self.hid.nuordemcda.astype(float) <= 4]
        hid.plot(ax=ax, color=AGUA, linewidth=0.9 if recorte == "urbano" else 0.5, zorder=3)
        if self.agua is not None:
            self.desenhar_agua(ax, ext)
        self.limite.boundary.plot(ax=ax, color=INK, linewidth=1.0, zorder=10 if self.agua is not None else 4)
        ax.set_xlim(ext[0], ext[1])
        ax.set_ylim(ext[2], ext[3])
        ax.set_aspect("equal")
        ax.set_xticks([])
        ax.set_yticks([])
        for s in ax.spines.values():
            s.set_color("#c3c2b7")
        # barra de escala
        L = 1000 if recorte == "urbano" else 20000
        x = ext[0] + 0.05 * (ext[1] - ext[0])
        y = ext[2] + 0.04 * (ext[3] - ext[2])
        ax.plot([x, x + L], [y, y], color=INK, lw=2, zorder=6)
        ax.text(x + L / 2, y + 0.012 * (ext[3] - ext[2]), f"{L // 1000} km", ha="center", fontsize=7, color=INK, zorder=6)
        ax.annotate("N", xy=(0.95, 0.95), xytext=(0.95, 0.88), xycoords="axes fraction", ha="center", fontsize=9,
                    arrowprops=dict(arrowstyle="-|>", color=INK, lw=1), color=INK)


AGUA_OUTRAS = "#dcebf6"  # outras águas no modo "enderecos": azul bem claro, sem contorno


def _desenhar_agua(self, ax, ext, modo: str | None = None):
    """Rio principal como área de água por cima dos polígonos temáticos (zorder 4.5), com a margem.

    Outras águas (açudes, lagoas) seguem a regra de exibição c.AGUA_EXIBICAO (rodada 04):
    no modo "enderecos" ficam por baixo de pontos, vias e manchas (zorder 0.8), sem contorno.
    """
    from shapely.geometry import box

    modo = modo or self.modo_agua
    b = box(ext[0], ext[2], ext[1], ext[3]).buffer(500)
    w = self.agua[self.agua.intersects(b)]
    w[w.principal].plot(ax=ax, color=AGUA_AREA, edgecolor=AGUA_MARGEM, linewidth=0.7, zorder=4.5)
    outras = c.outras_aguas_visiveis(self.agua, ext, modo)
    self._outras_no_mapa = (modo, len(outras), c.escala_do_mapa(ext))
    if len(outras):
        if modo == "r03":
            outras.plot(ax=ax, color=AGUA_AREA, edgecolor=AGUA_MARGEM, linewidth=0.35 if c.escala_do_mapa(ext) == "municipio" else 0.5, zorder=4.5)
        else:
            outras.plot(ax=ax, color=AGUA_OUTRAS, edgecolor="none", linewidth=0, zorder=0.8)


def _legenda_agua(self) -> list:
    if self.agua is None:
        return []
    hand = [Patch(facecolor=AGUA_AREA, edgecolor=AGUA_MARGEM, linewidth=0.6, label=f"{self.nome_rio} (área de água)")]
    modo, n, escala = getattr(self, "_outras_no_mapa", (None, 0, None))
    if n:  # só quando aparecem no mapa
        lim = c.AGUA_EXIBICAO[modo][escala]
        txt = f"outras áreas de água (açudes, lagoas{'; ≥ ' + format(lim, 'g') + ' ha' if lim else ''})\nfonte: OpenStreetMap"
        if modo == "r03":
            hand.append(Patch(facecolor=AGUA_AREA, edgecolor=AGUA_MARGEM, linewidth=0.3, label=txt))
        else:
            hand.append(Patch(facecolor=AGUA_OUTRAS, edgecolor="none", label=txt))
    return hand


Base.desenhar_agua = _desenhar_agua
Base.legenda_agua = _legenda_agua


def mapa(base: Base, gdf, col, limites, cores, titulo, legenda_titulo, nome, fonte, fmt="{:g}", unidade="", extras=None, recortes=("municipio", "urbano"), labs=None,
         saida: Path | None = None, meta_extra: dict | None = None):
    gdf = gdf.copy()
    gdf["_cl"] = classificar(gdf[col], limites)
    labs = labs or rotulos(limites, fmt, unidade)
    caminhos = []
    for rec in recortes:
        ext = base.ext_urb if rec == "urbano" else base.ext_mun
        fig, ax = plt.subplots(figsize=(7.2, 7.2 * (ext[3] - ext[2]) / (ext[1] - ext[0]) + 0.6))
        sem = gdf[gdf._cl < 0]
        if len(sem):
            sem.plot(ax=ax, color=AUSENTE, hatch="///", edgecolor="#b5b4ad", linewidth=0.2, zorder=1)
        for i, cor in enumerate(cores):
            sub = gdf[gdf._cl == i]
            if len(sub):
                sub.plot(ax=ax, color=cor, edgecolor="#fcfcfb", linewidth=0.25 if rec == "urbano" else 0.1, zorder=2)
        if extras:
            extras(ax, rec)
        base.desenhar(ax, rec)
        hand = [Patch(facecolor=cor, edgecolor="#b5b4ad", linewidth=0.3, label=l) for cor, l in zip(cores, labs)]
        if len(sem):
            hand.append(Patch(facecolor=AUSENTE, hatch="///", edgecolor="#b5b4ad", label="sem valor (sigilo / sem moradores)"))
        hand += base.legenda_agua()
        hand += [Line2D([], [], color=AGUA, lw=1, label="hidrografia (BHO/ANA)"), Line2D([], [], color=INK, lw=1, label="limite municipal")]
        if extras and hasattr(extras, "legenda"):
            hand += extras.legenda
        ax.legend(handles=hand, title=legenda_titulo, loc="upper left", bbox_to_anchor=(1.01, 1), frameon=False, fontsize=7.5, title_fontsize=8)
        sufixo = "município inteiro" if rec == "municipio" else "área urbana da sede"
        ax.set_title(f"{titulo}\n{sufixo}", loc="left", fontsize=10, color=INK)
        ax.annotate(f"{fonte}\nProduto de trabalho — pendente de conferência. EPSG:31981.", xy=(0, -0.015), xycoords="axes fraction",
                    va="top", fontsize=6.5, color=MUTED)
        caminho = (saida or c.MAPAS) / f"{nome}_{rec}.png"
        fig.savefig(caminho, dpi=180, facecolor="#fcfcfb", bbox_inches="tight")
        plt.close(fig)
        c.gravar_meta(caminho, status=c.STATUS_CONFERENCIA, ligado_ao_portal=False, codigo_ibge=base.cod, variavel=col,
                      limites_classes=[float(x) for x in limites], rotulos=labs, cores=cores, recorte=sufixo, fonte=fonte,
                      script="scripts/processamento/dinamica_populacional_mapas.py", **(meta_extra or {}))
        caminhos.append(caminho)
    return caminhos


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--codigo-ibge", default=c.CODIGO_IBGE_DEFAULT)
    p.add_argument("--sem-centro", action="store_true", help="não desenha o símbolo do centro da cidade")
    p.add_argument("--saida", type=Path, default=None, help="pasta de saída (padrão: docs/dinamica_populacional/mapas)")
    p.add_argument("--sem-centro-medio", action="store_true", help="não desenha os marcadores do centro médio da população")
    p.add_argument("--com-agua", action="store_true", help="desenha a área de água (OSM) por cima dos polígonos temáticos")
    p.add_argument("--nome-rio", default=c.NOME_RIO_DEFAULT, help="rótulo do rio principal na legenda")
    p.add_argument("--modo-agua", default="tematico", choices=sorted(c.AGUA_EXIBICAO), help="regra de exibição das outras águas (padrão: só o rio)")
    a = p.parse_args()
    c.garantir_pastas()
    saida = (a.saida if a.saida is None or a.saida.is_absolute() else c.RAIZ / a.saida)
    if saida is not None:
        saida.mkdir(parents=True, exist_ok=True)
    meta_extra = {}
    if a.sem_centro:
        meta_extra["sem_simbolo_do_centro"] = True
    if a.sem_centro_medio:
        meta_extra["sem_marcadores_centro_medio"] = True
    if a.com_agua:
        meta_extra["area_de_agua"] = "OpenStreetMap (data/raw/vetor/hidrografia-area-agua_osm_atual_vetorial.gpkg), só apresentação"
        meta_extra["regra_outras_aguas"] = a.modo_agua
    meta_extra = meta_extra or None
    opc = dict(saida=saida, meta_extra=meta_extra)
    base = Base(a.codigo_ibge, agua=a.com_agua, nome_rio=a.nome_rio, modo_agua=a.modo_agua)
    st = gpd.read_file(c.CAMADAS / "populacao-setores_ibge-censo_2022_setor.gpkg")
    gr = gpd.read_file(c.CAMADAS / "populacao-grade_ibge-censo_2022_200m-1km.gpkg")
    ac = gpd.read_file(c.CAMADAS / "populacao-mudanca_ibge-censo_2010-2022_area-comparavel.gpkg")
    f22 = "Fonte: IBGE, Censo 2022 — malha e agregados por setor."
    inf = np.inf
    feitos = []
    feitos += mapa(base, st, "dens_hab_ha", [0, 5, 25, 50, 75, 100, inf], SEQ, "Densidade demográfica por setor, 2022", "habitantes por hectare", "densidade-populacional_ibge-censo_2022_setor", f22, **opc)
    feitos += mapa(base, st, "pct_60_mais", [0, 10, 15, 20, 25, inf], SEQ[1:], "Pessoas com 60 anos ou mais, por setor, 2022", "% da população do setor", "idosos-60-mais_ibge-censo_2022_setor", f22, unidade=" %", **opc)
    feitos += mapa(base, st, "pct_0_14", [0, 15, 20, 25, 30, inf], SEQ[1:], "Pessoas de 0 a 14 anos, por setor, 2022", "% da população do setor", "criancas-0-14_ibge-censo_2022_setor", f22, unidade=" %", **opc)
    feitos += mapa(base, st, "pct_dom_1_morador", [0, 10, 15, 20, 25, inf], SEQ[1:], "Domicílios com um só morador, por setor, 2022", "% dos domicílios particulares permanentes ocupados", "domicilios-unipessoais_ibge-censo_2022_setor", f22, unidade=" %", **opc)
    grv = gr[gr["pop"] > 0]
    feitos += mapa(base, grv, "pop", [1, 11, 51, 101, 201, inf], SEQ[1:], "População por célula da grade estatística, 2022", "pessoas por célula\n(200 m urbano; 1 km rural;\ncélulas vazias não pintadas)", "populacao-grade_ibge-censo_2022_200m-1km", "Fonte: IBGE, Grade Estatística do Censo 2022.",
                  labs=["1 a 10", "11 a 50", "51 a 100", "101 a 200", "mais de 200"], **opc)

    import json
    desl = json.loads((c.TABELAS / "deslocamento-centro-medio_ibge-censo_2010-2022_municipal.json").read_text(encoding="utf-8"))["deslocamento"]

    def centros(ax, rec):
        if not a.sem_centro:
            cc = desl["centro_da_cidade"]
            ax.scatter([cc["x"]], [cc["y"]], marker="*", s=90, color=INK, zorder=7)
        if a.sem_centro_medio:
            return
        chave = "{}_so_areas_urbanas" if rec == "urbano" else "{}"
        for ano, mk in (("2010", "o"), ("2022", "s")):
            d = desl[chave.format(ano)]
            ax.scatter([d["centro_medio_x"]], [d["centro_medio_y"]], marker=mk, s=40, facecolor="#fcfcfb", edgecolor=INK, linewidth=1.4, zorder=8)
    centros.legenda = [] if a.sem_centro_medio else ([] if a.sem_centro else [Line2D([], [], marker="*", ls="", color=INK, ms=9, label="centro da cidade (bairro Centro)")]) + [
                       Line2D([], [], marker="o", ls="", mfc="#fcfcfb", mec=INK, ms=6, label="centro médio da população 2010"),
                       Line2D([], [], marker="s", ls="", mfc="#fcfcfb", mec=INK, ms=6, label="centro médio da população 2022")]
    fac = "Fonte: IBGE — histórico de formação dos setores 2010–2022; agregados por setor 2010 e 2022; geometria da malha 2022."
    feitos += mapa(base, ac, "var_pop_abs", [-inf, -250, -100, -25, 25, 100, 250, inf], DIV7, "Variação absoluta da população, 2010–2022, por área comparável", "pessoas (neutro: −25 a +25)", "populacao-variacao-absoluta_ibge-censo_2010-2022_area-comparavel", fac, extras=centros, **opc)
    ac2 = ac.copy()
    ac2["cl"] = ac2.classe_mudanca.map({k: i for i, k in enumerate(["perda forte", "perda", "estável", "ganho", "ganho forte"])})
    feitos += mapa(base, ac2, "cl", [-0.5, 0.5, 1.5, 2.5, 3.5, inf], DIV5, "Classes de mudança da população, 2010–2022, por área comparável", "variação % (limiares fixos)", "populacao-variacao-classes_ibge-censo_2010-2022_area-comparavel", fac, extras=centros,
                  labs=["perda forte (< −20 %)", "perda (−20 a −5 %)", "estável (−5 a +5 %)", "ganho (+5 a +20 %)", "ganho forte (> +20 %)"], **opc)
    for f in feitos:
        logger.info("Mapa: %s", f.relative_to(c.RAIZ))


if __name__ == "__main__":
    main()
