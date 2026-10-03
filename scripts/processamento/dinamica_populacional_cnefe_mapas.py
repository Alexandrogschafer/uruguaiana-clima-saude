"""
Rodada 02, Tarefa 3 — endereços residenciais do CNEFE 2022 (espécie 1,
domicílio particular): qualidade dos pontos e mapas de trabalho.

O que o ponto é: um ponto por endereço de domicílio particular cadastrado
pelo IBGE no Censo 2022. O que NÃO é: não traz número de moradores nem
ocupação; a população vem do setor censitário ou da grade estatística.

Entrada: camada data/processed/dinamica_populacional/enderecos-domicilios_
ibge-cnefe_2022_pontos.gpkg (fora do git; produzida por
dinamica_populacional_2022.py). Os pontos NÃO são copiados para docs/: só
os PNG e uma tabela agregada de qualidade (contagens, sem endereço).

Qualidade:
  - nível de geocodificação (NV_GEO_COORD) com o significado do dicionário
    oficial do CNEFE 2022 (Dicionario_CNEFE_Censo_2022.xls, baixado para o
    cache do CNEFE se ausente);
  - coordenada repetida: pontos com a mesma coordenada (arredondada a 1 cm em
    EPSG:31981) = locais com endereços empilhados (edifícios, vilas);
  - pontos fora do limite municipal (config/area_estudo.geojson) e fora de
    qualquer setor da malha 2022.

Mapas (área urbana da sede e município inteiro): pontos; pontos por nível de
geocodificação; contagem por hexágono de 200 m (distância entre lados
opostos; área ≈ 3,46 ha), para ler a concentração onde os pontos se
sobrepõem. Recortes de detalhe (escolhidos pelos dados): área comparável com
maior ganho absoluto de população 2010–2022, área com maior perda absoluta e
a margem do rio principal junto à área urbana (trecho com mais endereços
urbanos a até 300 m da MARGEM — polígono de água do OSM; rodada 03).
Rodada 03: a área de água (OSM) é desenhada por cima dos hexágonos e o
recorte da área de maior ganho é apertado nas quadras com endereços. Malha viária de fundo: OpenStreetMap (camada já existente no
repositório), em cinza claro, só como referência visual.

Rodada 10: --layout a4 grava só a edição A4 dos mapas (docs/dinamica_populacional/
mapas_a4/; legenda abaixo do mapa, 16 cm, 300 dpi), com os mapas de mapas_v2/
como origem; a tabela de qualidade não é regravada.

Uso:
  python scripts/processamento/dinamica_populacional_cnefe_mapas.py --codigo-ibge 4322400 --nome-rio "rio Uruguai"
  python scripts/processamento/dinamica_populacional_cnefe_mapas.py --codigo-ibge 4322400 --layout a4
"""

from __future__ import annotations

import argparse
import json
import logging
import math

import geopandas as gpd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import requests  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402
from shapely.geometry import Polygon, box  # noqa: E402

import dinamica_populacional_comum as c  # noqa: E402
from dinamica_populacional_mapas import AGUA, INK, MUTED, SEQ, Base  # noqa: E402
from layout_mapa import LAYOUTS, LayoutA4, finalizar_a4, pasta_a4  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

MAPAS_V2 = c.DOCS / "mapas_v2"
SCRIPT = "scripts/processamento/dinamica_populacional_cnefe_mapas.py"
URL_DIC = ("https://ftp.ibge.gov.br/Cadastro_Nacional_de_Enderecos_para_Fins_Estatisticos/Censo_Demografico_2022/"
           "Arquivos_CNEFE/CSV/Dicionario_CNEFE_Censo_2022.xls")
HEX_LARGURA = 200.0  # m entre lados opostos
DIST_MARGEM_M = 300  # recorte do rio: trecho com mais endereços urbanos a até esta distância da margem
MARGEM_GANHO_M = 150  # folga do enquadramento apertado do recorte da área de maior ganho
VIA, PONTO = "#c9c8c1", "#1f3a5f"
DESTAQUE = "#762a83"  # contorno da área comparável em destaque (roxo; distinto do limite municipal)
NIVEL_CORES = {"1": "#2166ac", "2": "#e08214", "3-5": "#b2182b"}  # azul / laranja / vermelho (legível p/ daltônicos)
FONTE = "Fonte: IBGE — CNEFE do Censo 2022 (espécie 1, domicílio particular); malha de setores 2022."
LAYOUT = "lateral"  # --layout (rodada 10)
METODO_A4 = "Um ponto por endereço, sem número de moradores."


def mil(n) -> str:
    """Inteiro com ponto de milhar (pt-BR)."""
    return f"{float(n):,.0f}".replace(",", ".")


def dicionario_niveis() -> dict:
    """Significado de NV_GEO_COORD lido do dicionário oficial (baixado se ausente)."""
    arq = c.CACHE_DP / "cnefe_2022" / "Dicionario_CNEFE_Censo_2022.xls"
    if not arq.exists():
        r = requests.get(URL_DIC, timeout=60)
        r.raise_for_status()
        arq.write_bytes(r.content)
        logger.info("Dicionário CNEFE baixado: %s (%d bytes)", arq.name, len(r.content))
    meta = arq.with_name(arq.name + ".json")
    if not meta.exists():
        c.gravar_meta(arq, fonte="IBGE — CNEFE Censo 2022, dicionário de variáveis", url=URL_DIC, tamanho_bytes=arq.stat().st_size)
        arq.with_suffix(".json").rename(meta)
    d = pd.read_excel(arq, header=None).fillna("")
    i0 = d.index[d[0].astype(str).str.strip() == "NV_GEO_COORD"][0]
    # categorias podem vir numa célula com quebras de linha ou em linhas seguintes (coluna 0 vazia)
    cel = [str(d.loc[i0, 2])]
    for i in range(i0 + 1, len(d)):
        if str(d.loc[i, 0]).strip():
            break
        cel.append(str(d.loc[i, 2]))
    niveis = {}
    for item in "\n".join(cel).split("\n"):
        if "=" in item:
            k, v = item.split("=", 1)
            niveis[k.strip()] = v.strip().rstrip("²³ ").strip()
    return niveis


def hexagonos(pts: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Conta pontos por hexágono (topo plano a plano: largura HEX_LARGURA). Só hexágonos ocupados."""
    R = HEX_LARGURA / math.sqrt(3)  # raio circunscrito, hexágono "pointy-top"
    x, y = pts.geometry.x.values, pts.geometry.y.values
    q = (math.sqrt(3) / 3 * x - y / 3) / R
    r = (2 / 3 * y) / R
    # arredondamento de coordenadas axiais (cubo)
    s = -q - r
    rq, rr, rs = np.round(q), np.round(r), np.round(s)
    dq, dr, ds = np.abs(rq - q), np.abs(rr - r), np.abs(rs - s)
    m1 = (dq > dr) & (dq > ds)
    m2 = ~m1 & (dr > ds)
    rq[m1] = -rr[m1] - rs[m1]
    rr[m2] = -rq[m2] - rs[m2]
    cont = pd.DataFrame({"q": rq.astype(int), "r": rr.astype(int)}).value_counts().rename("enderecos").reset_index()
    cx = R * math.sqrt(3) * (cont.q + cont.r / 2)
    cy = R * 1.5 * cont.r
    ang = np.radians(np.arange(6) * 60 + 30)
    geoms = [Polygon(zip(a + R * np.cos(ang), b + R * np.sin(ang))) for a, b in zip(cx, cy)]
    return gpd.GeoDataFrame(cont, geometry=geoms, crs=pts.crs)


def qualidade(pts, limite, st, niveis) -> tuple[pd.DataFrame, dict]:
    tab = pts.NV_GEO_COORD.value_counts().sort_index().rename("enderecos").to_frame()
    tab["pct"] = 100 * tab.enderecos / len(pts)
    tab["significado_dicionario_ibge"] = tab.index.map(lambda k: niveis.get(str(k), ""))
    sit = pts.setor_2022.map(st.set_index("CD_SETOR").SITUACAO)
    tab = tab.join(pd.crosstab(pts.NV_GEO_COORD, sit).add_prefix("situacao_"))
    tab.index.name = "nivel_geocodificacao"
    chave = pd.Series(list(zip(np.round(pts.geometry.x, 2), np.round(pts.geometry.y, 2))), index=pts.index)
    n_por_local = chave.map(chave.value_counts())
    rep = n_por_local > 1
    tab["em_coordenada_repetida"] = rep.groupby(pts.NV_GEO_COORD).sum()
    dentro_lim = pts.within(limite.union_all())
    em_setor = gpd.sjoin(pts[["geometry"]], st[["CD_SETOR", "geometry"]], predicate="intersects", how="left")
    em_setor = em_setor[~em_setor.index.duplicated()].CD_SETOR.notna()
    locais = chave[rep].nunique()
    dist = n_por_local[rep].groupby(chave[rep]).first()
    info = {
        "enderecos_total": len(pts),
        "coordenada_repetida": {"locais_com_mais_de_um_endereco": int(locais), "enderecos_nesses_locais": int(rep.sum()),
                                "pct_dos_enderecos": round(100 * rep.sum() / len(pts), 1),
                                "maximo_de_enderecos_num_local": int(n_por_local.max()),
                                "locais_por_tamanho": {"2": int((dist == 2).sum()), "3-5": int(dist.between(3, 5).sum()),
                                                       "6-20": int(dist.between(6, 20).sum()), "21+": int((dist > 20).sum())},
                                "criterio": "mesma coordenada arredondada a 1 cm em EPSG:31981"},
        "fora_do_limite_municipal": int((~dentro_lim).sum()),
        "fora_de_qualquer_setor_2022": int((~em_setor).sum()),
        "fora_do_poligono_do_setor_atribuido": int((~pts.ponto_dentro_do_setor_atribuido.astype(bool)).sum()),
        "situacao_do_setor": sit.value_counts().to_dict(),
    }
    return tab.reset_index(), info


class Fundo:
    def __init__(self, base: Base):
        self.base = base
        vias = gpd.read_file(c.RAW / "vetor" / "malha-viaria_osm_atual_vetorial.gpkg").to_crs(c.CRS_PADRAO)
        self.vias = vias[vias.intersects(base.limite.buffer(3000).union_all())]

    def desenhar(self, ax, ext, rec, escala_m, modo_agua: str = "enderecos", escala_pos=(0.05, 0.04)):
        b = box(ext[0], ext[2], ext[1], ext[3])
        self.vias[self.vias.intersects(b)].plot(ax=ax, color=VIA, linewidth=0.35 if rec != "municipio" else 0.25, zorder=1)
        hid = self.base.hid if rec != "municipio" else self.base.hid[self.base.hid.nuordemcda.astype(float) <= 4]
        hid[hid.intersects(b)].plot(ax=ax, color=AGUA, linewidth=1.0 if rec != "municipio" else 0.5, zorder=3)
        # rio como área de água por cima dos hexágonos; outras águas pela regra de exibição (c.AGUA_EXIBICAO)
        self.base.desenhar_agua(ax, ext, modo_agua)
        self.base.limite.boundary.plot(ax=ax, color=INK, linewidth=1.0, zorder=10)
        ax.set_xlim(ext[0], ext[1])
        ax.set_ylim(ext[2], ext[3])
        ax.set_aspect("equal")
        ax.set_xticks([])
        ax.set_yticks([])
        for s in ax.spines.values():
            s.set_color("#c3c2b7")
        x = ext[0] + escala_pos[0] * (ext[1] - ext[0])  # posição da barra de escala (fração do quadro)
        y = ext[2] + escala_pos[1] * (ext[3] - ext[2])
        ax.plot([x, x + escala_m], [y, y], color=INK, lw=2, zorder=8)
        rot = f"{escala_m // 1000:g} km" if escala_m >= 1000 else f"{escala_m:g} m"
        ax.text(x + escala_m / 2, y + 0.012 * (ext[3] - ext[2]), rot, ha="center", fontsize=7, color=INK, zorder=8)
        ax.annotate("N", xy=(0.95, 0.95), xytext=(0.95, 0.88), xycoords="axes fraction", ha="center", fontsize=9, zorder=9,
                    arrowprops=dict(arrowstyle="-|>", color=INK, lw=1), color=INK,
                    bbox=dict(boxstyle="round,pad=0.15", facecolor="#ffffff", edgecolor="none", alpha=0.85))

    def comum(self, extra_leg=()):
        return list(extra_leg) + [Line2D([], [], color=VIA, lw=1, label="malha viária (OpenStreetMap)"),
                                  *self.base.legenda_agua(),
                                  Line2D([], [], color=AGUA, lw=1, label="hidrografia (BHO/ANA)"),
                                  Line2D([], [], color=INK, lw=1, label="limite municipal")]


def salvar(fig, ax, nome, titulo, sub, hand, leg_tit, meta, nota="", leg_a4=None, sub_a4=None):
    if LAYOUT == "a4":
        # leg_a4 = (título da legenda em uma linha, complemento para a linha de método), quando o título lateral é longo
        tit_a4, compl = leg_a4 if leg_a4 else (leg_tit.replace("\n", " "), "")
        origem = MAPAS_V2 / f"{nome}.png"
        finalizar_a4(fig._layout_a4, f"{titulo}\n{sub_a4 or sub}", hand, tit_a4, FONTE + nota, pasta_a4(MAPAS_V2) / origem.name, origem=origem,
                     metodo=(METODO_A4 + (" " + compl if compl else "")),
                     texto_retirado="Um ponto por endereço, sem número de moradores. Produto de trabalho — pendente de conferência. EPSG:31981.")
        return pasta_a4(MAPAS_V2) / origem.name
    ax.legend(handles=hand, title=leg_tit, loc="upper left", bbox_to_anchor=(1.01, 1), frameon=False, fontsize=7.5, title_fontsize=8,
              alignment="left")
    ax.set_title(f"{titulo}\n{sub}", loc="left", fontsize=10, color=INK)
    ax.annotate(f"{FONTE}{nota}\nUm ponto por endereço, sem número de moradores. Produto de trabalho — pendente de conferência. EPSG:31981.",
                xy=(0, -0.015), xycoords="axes fraction", va="top", fontsize=6.5, color=MUTED)
    caminho = MAPAS_V2 / f"{nome}.png"
    fig.savefig(caminho, dpi=180, facecolor="#fcfcfb", bbox_inches="tight")
    plt.close(fig)
    c.gravar_meta(caminho, status=c.STATUS_CONFERENCIA, ligado_ao_portal=False, script=SCRIPT, sem_simbolo_do_centro=True,
                  fonte="IBGE — CNEFE Censo 2022 (espécie 1); malha de setores 2022; hidrografia BHO/ANA; malha viária OpenStreetMap (fundo)",
                  sem_marcadores_centro_medio=True,
                  area_de_agua="OpenStreetMap (data/raw/vetor/hidrografia-area-agua_osm_atual_vetorial.gpkg), só apresentação; outras águas pela regra c.AGUA_EXIBICAO (rodada 04)",
                  pontos_fora_do_git="a camada de pontos fica em data/processed/ (ignorada); aqui só a imagem", **meta)
    return caminho


def fig_ext(ext, largura=7.2):
    if LAYOUT == "a4":  # quadro na largura da folha A4, mesma extensão
        lay = LayoutA4([[ext]])
        lay.fig._layout_a4 = lay
        return lay.fig, lay.eixos[0]
    return plt.subplots(figsize=(largura, largura * (ext[3] - ext[2]) / (ext[1] - ext[0]) + 0.6))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--codigo-ibge", default=c.CODIGO_IBGE_DEFAULT)
    p.add_argument("--nome-rio", default=c.NOME_RIO_DEFAULT, help="rótulo do curso d'água principal (maior área de contribuição na BHO)")
    p.add_argument("--layout", default="lateral", choices=LAYOUTS, help="lateral (padrão) ou a4 (só a edição A4 dos mapas)")
    a = p.parse_args()
    global LAYOUT
    LAYOUT = a.layout
    MAPAS_V2.mkdir(parents=True, exist_ok=True)
    arq_pts = c.CAMADAS / "enderecos-domicilios_ibge-cnefe_2022_pontos.gpkg"
    if not arq_pts.exists():
        raise SystemExit(f"{arq_pts} ausente — rode dinamica_populacional_2022.py")
    pts = gpd.read_file(arq_pts, columns=["setor_2022", "NV_GEO_COORD", "ponto_dentro_do_setor_atribuido"]).to_crs(c.CRS_PADRAO)
    st = gpd.read_file(c.CAMADAS / "populacao-setores_ibge-censo_2022_setor.gpkg")
    ac = gpd.read_file(c.CAMADAS / "populacao-mudanca_ibge-censo_2010-2022_area-comparavel.gpkg")
    base = Base(a.codigo_ibge, agua=True, nome_rio=a.nome_rio)
    fundo = Fundo(base)
    niveis = dicionario_niveis()

    # ----- qualidade
    tab, info = qualidade(pts, base.limite, st, niveis)
    arq_q = c.TABELAS / "enderecos-qualidade_ibge-cnefe_2022_municipal.csv"
    if LAYOUT == "lateral":  # edição A4 não regrava a tabela
        tab.to_csv(arq_q, index=False)
        c.gravar_meta(arq_q, codigo_ibge=a.codigo_ibge, crs=c.CRS_PADRAO, status=c.STATUS_CONFERENCIA, ligado_ao_portal=False, script=SCRIPT,
                      fonte="IBGE — CNEFE Censo 2022 (espécie 1) e dicionário de variáveis do CNEFE 2022", url_dicionario=URL_DIC,
                      niveis_dicionario=niveis, **info, nota="só contagens agregadas; nenhum endereço é publicado")
    print(tab.to_string(index=False))
    print(json.dumps(info, ensure_ascii=False, indent=1))

    pts["grupo_nivel"] = np.where(pts.NV_GEO_COORD == "1", "1", np.where(pts.NV_GEO_COORD == "2", "2", "3-5"))
    hx = hexagonos(pts)
    lim_hex = [1, 6, 21, 51, 101, 201, np.inf]
    lab_hex = ["1 a 5", "6 a 20", "21 a 50", "51 a 100", "101 a 200", "mais de 200"]
    hx["cl"] = np.searchsorted(lim_hex[1:-1], hx.enderecos, side="right")
    print("hexágonos ocupados:", len(hx), "máx:", int(hx.enderecos.max()), hx.cl.value_counts().sort_index().to_dict())

    rot_niv = {"1": f"1 — {niveis.get('1', 'coordenada original')}",
               "2": f"2 — {niveis.get('2', 'coordenada modificada')}",
               "3-5": "3 a 5 — aproximada (3 estimada, 4 face de quadra, 5 localidade)"}
    rot_niv = {k: v.replace("Endereço - ", "").replace(" (", "\n(") for k, v in rot_niv.items()}
    feitos = []
    for rec in ("urbano", "municipio"):
        ext = base.ext_urb if rec == "urbano" else base.ext_mun
        sub = "área urbana da sede" if rec == "urbano" else "município inteiro (inclui os pontos rurais)"
        esc = 1000 if rec == "urbano" else 20000
        ms = 0.6 if rec == "urbano" else 0.8
        # (1) pontos
        fig, ax = fig_ext(ext)
        fundo.desenhar(ax, ext, rec, esc)
        ax.scatter(pts.geometry.x, pts.geometry.y, s=ms, color=PONTO, alpha=0.35, linewidths=0, zorder=5)
        hand = fundo.comum([Line2D([], [], marker="o", ls="", color=PONTO, alpha=0.6, ms=3, label=f"endereço de domicílio particular\n({mil(len(pts))} pontos)")])
        feitos.append(salvar(fig, ax, f"enderecos-pontos_ibge-cnefe_2022_pontos_{rec}", "Endereços de domicílios particulares, CNEFE 2022", sub, hand,
                             "ponto pequeno e semitransparente:\nonde escurece, há pontos sobrepostos", {"recorte": sub, "tema": "pontos"}))
        # (2) nível de geocodificação
        fig, ax = fig_ext(ext)
        fundo.desenhar(ax, ext, rec, esc)
        hand = []
        for k, cor in NIVEL_CORES.items():
            s = pts[pts.grupo_nivel == k]
            ax.scatter(s.geometry.x, s.geometry.y, s=ms if k == "1" else ms * 6, color=cor, alpha=0.35 if k == "1" else 0.85, linewidths=0,
                       zorder={"1": 5, "2": 6, "3-5": 7}[k])
            hand.append(Line2D([], [], marker="o", ls="", color=cor, ms=4, label=f"{rot_niv[k]} — {mil(len(s))}"))
        feitos.append(salvar(fig, ax, f"enderecos-nivel-geocodificacao_ibge-cnefe_2022_pontos_{rec}", "Endereços por nível de geocodificação, CNEFE 2022", sub,
                             fundo.comum(hand), "nível de geocodificação\n(dicionário do CNEFE 2022)\n1 = preciso; 2 = posição ajustada;\n3 a 5 = aproximado",
                             {"recorte": sub, "tema": "nivel_geocodificacao", "niveis": niveis},
                             leg_a4=("nível de geocodificação (dicionário do CNEFE 2022)", "Nível 1 = preciso; 2 = posição ajustada; 3 a 5 = aproximado.")))
        # (3) hexágonos
        fig, ax = fig_ext(ext)
        cores = SEQ
        for i, cor in enumerate(cores):
            hx[hx.cl == i].plot(ax=ax, color=cor, edgecolor="none", zorder=2)
        fundo.desenhar(ax, ext, rec, esc, modo_agua="tematico")  # hexágono de densidade = mapa temático: só o rio
        hand = fundo.comum([Patch(facecolor=cor, edgecolor="#b5b4ad", linewidth=0.3, label=l) for cor, l in zip(cores, lab_hex)])
        feitos.append(salvar(fig, ax, f"enderecos-densidade-hexagono_ibge-cnefe_2022_hex200m_{rec}", "Endereços de domicílios particulares por hexágono de 200 m, CNEFE 2022", sub,
                             hand, "endereços por hexágono\n(200 m entre lados opostos, ≈ 3,5 ha;\nhexágonos vazios não pintados)",
                             {"recorte": sub, "tema": "hexagono", "hex_largura_m": HEX_LARGURA, "limites_classes": lim_hex[:-1], "rotulos": lab_hex,
                              "hexagonos_ocupados": len(hx), "maximo_por_hexagono": int(hx.enderecos.max())}))

    # ----- recortes de detalhe (escolhidos pelos dados)
    urb = ac[ac.situacao_2022 == "Urbana"]
    a_ganho = urb.loc[urb.var_pop_abs.idxmax()]
    a_perda = urb.loc[urb.var_pop_abs.idxmin()]
    # rio: distância à MARGEM (polígono de água do rio principal, OSM), não ao eixo (rodada 03)
    margem = base.agua[base.agua.principal].union_all()
    pts_urb = pts[pts.setor_2022.map(st.set_index("CD_SETOR").SITUACAO) == "Urbana"]
    d_margem = pts_urb.distance(margem)
    perto = pts_urb[d_margem <= DIST_MARGEM_M]
    # trecho com mais endereços a até DIST_MARGEM_M da margem: célula de 1 km mais cheia
    ij = pd.DataFrame({"i": (perto.geometry.x // 1000).astype(int), "j": (perto.geometry.y // 1000).astype(int)}, index=perto.index)
    ci, cj = ij.value_counts().index[0]
    sel = perto[(ij.i == ci) & (ij.j == cj)]
    cx_r, cy_r = sel.geometry.x.mean(), sel.geometry.y.mean()
    info_margem = {"distancia_endereco_mais_proximo_da_margem_m": round(float(d_margem.min()), 1),
                   "enderecos_urbanos_ate_100m_da_margem": int((d_margem <= 100).sum()),
                   f"enderecos_urbanos_ate_{DIST_MARGEM_M}m_da_margem": len(perto),
                   "enderecos_urbanos_ate_500m_da_margem": int((d_margem <= 500).sum()),
                   "enderecos_dentro_de_qualquer_area_de_agua": int(gpd.sjoin(pts[["geometry"]], base.agua[["geometry"]], predicate="within").index.nunique()),
                   "enderecos_dentro_do_rio_principal": int(pts.within(margem).sum())}
    print(json.dumps(info_margem, ensure_ascii=False))
    detalhes = []
    for chave, area, rot in (("ganho", a_ganho, "área com maior ganho absoluto de população 2010–2022"),
                             ("perda", a_perda, "área com maior perda absoluta de população 2010–2022")):
        dentro = pts[pts.within(area.geometry)]
        if chave == "ganho" and len(dentro):
            # enquadramento apertado nas quadras com endereços: quantis 1–99 % (descarta pontos isolados) + ~150 m
            x0, x1 = dentro.geometry.x.quantile([0.01, 0.99])
            y0, y1 = dentro.geometry.y.quantile([0.01, 0.99])
            m = MARGEM_GANHO_M
            ext = (x0 - m, x1 + m, y0 - m, y1 + m)
        else:
            # janela quadrada sobre a extensão dos endereços da área, mín. 1,5 km de lado
            x0, y0, x1, y1 = dentro.total_bounds if len(dentro) else area.geometry.bounds
            mx, my, lado = (x0 + x1) / 2, (y0 + y1) / 2, max(x1 - x0, y1 - y0, 1500) + 400
            ext = (mx - lado / 2, mx + lado / 2, my - lado / 2, my + lado / 2)
        detalhes.append({"chave": chave, "rotulo": rot, "ext": ext, "area": area})
    lado = 2000
    detalhes.append({"chave": "rio", "rotulo": f"margem do {a.nome_rio} junto à área urbana (trecho com mais endereços a até {DIST_MARGEM_M} m da margem)",
                     "ext": (cx_r - lado / 2, cx_r + lado / 2, cy_r - lado / 2, cy_r + lado / 2), "area": None})
    info_det = []
    for d in detalhes:
        ext = d["ext"]
        b = box(ext[0], ext[2], ext[1], ext[3])
        fig, ax = fig_ext(ext)
        ac[ac.intersects(b)].boundary.plot(ax=ax, color="#8f8e88", linewidth=0.6, linestyle="--", zorder=2)
        hand = []
        if d["area"] is not None:
            gpd.GeoSeries([d["area"].geometry], crs=c.CRS_PADRAO).boundary.plot(ax=ax, color=DESTAQUE, linewidth=2.0, zorder=6)
            v = d["area"]
            hand.append(Line2D([], [], color=DESTAQUE, lw=2.0, label=f"área comparável {v.id_area}\n({v.bairros_2022.split('/')[0]}; população {mil(v.pop_2010)} → {mil(v.pop_2022)})"))
        s = pts[pts.within(b)]
        for k, cor in NIVEL_CORES.items():
            ss = s[s.grupo_nivel == k]
            ax.scatter(ss.geometry.x, ss.geometry.y, s=4 if k == "1" else 9, color=cor, alpha=0.6 if k == "1" else 0.9, linewidths=0, zorder=7)
            hand.append(Line2D([], [], marker="o", ls="", color=cor, ms=4, label=f"endereço — nível {k} ({mil(len(ss))})"))
        hand.append(Line2D([], [], color="#8f8e88", lw=0.8, ls="--", label="limites das áreas comparáveis"))
        fundo.desenhar(ax, ext, "detalhe", 250)
        bairros = sorted(set(st[st.intersects(b) & (st.SITUACAO == "Urbana")].NM_BAIRRO.dropna()))
        sub = f"detalhe: {d['rotulo']}"
        nome = f"enderecos-detalhe-{d['chave']}_ibge-cnefe_2022_pontos_urbano"
        info_det.append({"recorte": d["chave"], "descricao": d["rotulo"], "enderecos_no_recorte": len(s),
                         "enderecos_dentro_da_area": None if d["area"] is None else int(pts.within(d["area"].geometry).sum()), "bairros_ibge_no_recorte": bairros,
                         "area_comparavel": None if d["area"] is None else {k: (d["area"][k] if not isinstance(d["area"][k], float) else round(d["area"][k], 1))
                                                                             for k in ("id_area", "bairros_2022", "pop_2010", "pop_2022", "var_pop_abs", "var_pop_pct", "dom_2010", "dom_2022")},
                         "janela_m": [round(x) for x in ext], **(info_margem if d["chave"] == "rio" else {})})
        feitos.append(salvar(fig, ax, nome, "Endereços de domicílios particulares sobre a malha viária, CNEFE 2022", sub,
                             fundo.comum(hand), "pontos: um por endereço\n(cor = nível de geocodificação)",
                             {"recorte": sub, "tema": "detalhe", **info_det[-1]},
                             sub_a4=f"detalhe: margem do {a.nome_rio} junto à área urbana" if d["chave"] == "rio" else None))
    print(json.dumps(info_det, ensure_ascii=False, indent=1, default=str))
    for f in feitos:
        logger.info("Mapa: %s", f.relative_to(c.RAIZ))


if __name__ == "__main__":
    main()
