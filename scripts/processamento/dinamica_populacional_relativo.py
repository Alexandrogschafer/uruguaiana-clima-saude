"""
Rodada 02, Tarefa 2 — mudança 2010–2022 por área comparável RELATIVA à
variação do município.

"Média do município" = variação % do município inteiro (soma das áreas, que
fecha com o total do SIDRA), não a média simples das variações das áreas.
Para cada área: dif_pp = variação % da área − variação % do município, em
pontos percentuais (p.p.). Mesma leitura para domicílios particulares.

Classes fixas (p.p.): muito abaixo da média (< −15), abaixo (−15 a −5),
perto da média (−5 a +5), acima (+5 a +15), muito acima (> +15).
Sensibilidade: quantas áreas mudam de classe com limiares ±10 / ±20 p.p. em
vez de ±5 / ±15 (ver LIMIARES).
Tratamentos declarados:
  - área sem população (ou sem domicílio) em 2010 e com valor em 2022: % é
    indefinido; entra em "muito acima da média" (mesma regra da rodada 01);
  - área sem domicílio particular nos dois anos (só domicílio coletivo):
    classe de domicílios ausente.

Acrescenta colunas à camada e à tabela CSV da mudança por área comparável
(sem remover as existentes; rodar depois de dinamica_populacional_areas_comparaveis.py)
e grava tabelas e mapas em docs/dinamica_populacional/ (tabelas/ e mapas_v2/).
Nada vai para o portal.

Rodada 10: --layout a4 grava só a edição A4 dos mapas (docs/dinamica_populacional/
mapas_a4/; legenda abaixo do mapa, 16 cm, 300 dpi); as classes são calculadas
em memória e nenhuma tabela, camada ou mapa atual é regravado.

Uso:
  python scripts/processamento/dinamica_populacional_relativo.py --codigo-ibge 4322400
  python scripts/processamento/dinamica_populacional_relativo.py --codigo-ibge 4322400 --layout a4
"""

from __future__ import annotations

import argparse
import json
import logging

import geopandas as gpd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

import dinamica_populacional_comum as c  # noqa: E402
from dinamica_populacional_mapas import AUSENTE, DIV5, INK, MUTED, AGUA, Base  # noqa: E402
from layout_mapa import LAYOUTS, LayoutA4, finalizar_a4, pasta_a4  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

MAPAS_V2 = c.DOCS / "mapas_v2"
CLASSES_REL = ["muito abaixo da média", "abaixo da média", "perto da média", "acima da média", "muito acima da média"]
LIMIARES = {"5_15": (5.0, 15.0), "10_20": (10.0, 20.0)}  # (perto, forte) — principal: ±5 / ±15
SCRIPT = "scripts/processamento/dinamica_populacional_relativo.py"


def classe_rel(v: float, perto: float, forte: float) -> str | None:
    if v is None or not np.isfinite(v):
        return None
    if v < -forte:
        return CLASSES_REL[0]
    if v < -perto:
        return CLASSES_REL[1]
    if v <= perto:
        return CLASSES_REL[2]
    if v <= forte:
        return CLASSES_REL[3]
    return CLASSES_REL[4]


def relativo(g: gpd.GeoDataFrame, tema: str) -> tuple[float, dict]:
    """Acrescenta dif_pp_{tema}, classe_rel_{tema} e variantes de limiar. Devolve a variação do município."""
    a10, a22 = g[f"{tema}_2010"], g[f"{tema}_2022"]
    var_mun = 100 * (a22.sum() - a10.sum()) / a10.sum()
    g[f"dif_pp_{tema}"] = g[f"var_{tema}_pct"] - var_mun
    novo = (a10 == 0) & (a22 > 0)
    for k, (perto, forte) in LIMIARES.items():
        col = f"classe_rel_{tema}" if k == "5_15" else f"classe_rel_{tema}_lim{k}"
        g[col] = g[f"dif_pp_{tema}"].map(lambda v: classe_rel(v, perto, forte))
        g.loc[novo, col] = CLASSES_REL[4]
    return var_mun, {"variacao_pct_municipio": var_mun, "areas_novas_sem_valor_2010": int(novo.sum()),
                     "areas_sem_valor_nos_dois_anos": int(((a10 == 0) & (a22 == 0)).sum())}


def tabela_classes(g, tema: str) -> pd.DataFrame:
    col = f"classe_rel_{tema}"
    t = g.groupby(col).agg(areas=("id_area", "size"), pop_2010=("pop_2010", "sum"), pop_2022=("pop_2022", "sum"),
                           dom_2010=("dom_2010", "sum"), dom_2022=("dom_2022", "sum"),
                           areas_com_ganho_absoluto=(f"var_{tema}_abs", lambda s: int((s > 0).sum())))
    t = t.reindex(CLASSES_REL).fillna(0)
    sem = g[g[col].isna()]
    if len(sem):
        t.loc["sem valor"] = [len(sem), sem.pop_2010.sum(), sem.pop_2022.sum(), sem.dom_2010.sum(), sem.dom_2022.sum(), 0]
    t = t.astype({"areas": int, "areas_com_ganho_absoluto": int})
    t["pct_pop_2022"] = 100 * t.pop_2022 / g.pop_2022.sum()
    t.index.name = "classe"
    return t.reset_index()


def mapa_rel(base: Base, g, tema: str, var_mun: float, titulo: str, nome: str, fonte: str, perto=5, forte=15, layout: str = "lateral") -> list:
    col = f"classe_rel_{tema}"
    labs = [f"muito abaixo (< −{forte} p.p.)", f"abaixo (−{forte} a −{perto} p.p.)", f"perto da média (−{perto} a +{perto} p.p.)",
            f"acima (+{perto} a +{forte} p.p.)", f"muito acima (> +{forte} p.p.)"]
    # contorno: população -> áreas que GANHARAM gente; domicílios -> áreas que PERDERAM domicílios
    # (quase todas as áreas ganharam domicílios; o contorno marca a exceção — rodada 03)
    if tema == "pop":
        contorno = g[g.var_pop_abs > 0]
        rot_contorno = "área com ganho absoluto\n(população 2022 > 2010)"
        desc_contorno = "áreas com ganho absoluto de população 2010–2022"
    else:
        contorno = g[g.var_dom_abs < 0]
        rot_contorno = "área com PERDA absoluta\n(domicílios 2022 < 2010)"
        desc_contorno = "áreas com perda absoluta de domicílios 2010–2022"
    caminhos = []
    for rec in ("municipio", "urbano"):
        ext = base.ext_urb if rec == "urbano" else base.ext_mun
        if layout == "a4":
            lay = LayoutA4([[ext]])
            fig, ax = lay.fig, lay.eixos[0]
        else:
            fig, ax = plt.subplots(figsize=(7.2, 7.2 * (ext[3] - ext[2]) / (ext[1] - ext[0]) + 0.6))
        sem = g[g[col].isna()]
        if len(sem):
            sem.plot(ax=ax, color=AUSENTE, hatch="///", edgecolor="#b5b4ad", linewidth=0.2, zorder=1)
        for k, cor in zip(CLASSES_REL, DIV5):
            sub = g[g[col] == k]
            if len(sub):
                sub.plot(ax=ax, color=cor, edgecolor="#fcfcfb", linewidth=0.25 if rec == "urbano" else 0.1, zorder=2)
        # contorno destacado: áreas com ganho ABSOLUTO (a classe é relativa; o contorno diz quem cresceu de fato)
        contorno.boundary.plot(ax=ax, color=INK, linewidth=1.3 if rec == "urbano" else 0.8, zorder=4)
        base.desenhar(ax, rec)
        sinal = f"{var_mun:+.2f}".replace(".", ",").replace("-", "−")
        hand = [Patch(facecolor=cor, edgecolor="#b5b4ad", linewidth=0.3, label=l) for cor, l in zip(DIV5, labs)]
        if len(sem):
            hand.append(Patch(facecolor=AUSENTE, hatch="///", edgecolor="#b5b4ad", label="sem valor (sem domicílio particular)"))
        hand += [Patch(facecolor="none", edgecolor=INK, linewidth=1.3, label=rot_contorno)] + base.legenda_agua() + [
                 Line2D([], [], color=AGUA, lw=1, label="hidrografia (BHO/ANA)"), Line2D([], [], color=INK, lw=1, label="limite municipal")]
        leg_tit = (f"variação da área − variação do município\n(município: {sinal} %, 2010–2022)\n"
                   "\"média\" = variação do município inteiro,\nnão a média das áreas")
        sufixo = "município inteiro" if rec == "municipio" else "área urbana da sede"
        if layout == "a4":
            # título da legenda em uma linha; a definição de "média" vai para a linha de método do rodapé
            caminho = MAPAS_V2 / f"{nome}_{rec}.png"
            destino = pasta_a4(MAPAS_V2) / caminho.name
            finalizar_a4(lay, f"{titulo}\n{sufixo}", hand, f"variação da área − variação do município (município: {sinal} %, 2010–2022)", fonte,
                         destino, origem=caminho, metodo="\"Média\" = variação do município inteiro, não a média das áreas.",
                         texto_retirado="Produto de trabalho — pendente de conferência. EPSG:31981.")
            caminhos.append(destino)
            continue
        ax.legend(handles=hand, title=leg_tit, loc="upper left", bbox_to_anchor=(1.01, 1), frameon=False, fontsize=7.5, title_fontsize=7.5,
                  alignment="left")
        sufixo = "município inteiro" if rec == "municipio" else "área urbana da sede"
        ax.set_title(f"{titulo}\n{sufixo}", loc="left", fontsize=10, color=INK)
        ax.annotate(f"{fonte}\nProduto de trabalho — pendente de conferência. EPSG:31981.", xy=(0, -0.015), xycoords="axes fraction",
                    va="top", fontsize=6.5, color=MUTED)
        caminho = MAPAS_V2 / f"{nome}_{rec}.png"
        fig.savefig(caminho, dpi=180, facecolor="#fcfcfb", bbox_inches="tight")
        plt.close(fig)
        c.gravar_meta(caminho, status=c.STATUS_CONFERENCIA, ligado_ao_portal=False, codigo_ibge=base.cod, variavel=col,
                      variacao_pct_municipio=var_mun, limiares_pp={"perto": perto, "forte": forte}, rotulos=labs, cores=DIV5,
                      contorno=desc_contorno, n_areas_contornadas=len(contorno), recorte=sufixo, fonte=fonte, script=SCRIPT,
                      sem_simbolo_do_centro=True, sem_marcadores_centro_medio=True,
                      area_de_agua="OpenStreetMap (data/raw/vetor/hidrografia-area-agua_osm_atual_vetorial.gpkg), só apresentação", regra_outras_aguas="tematico (só o rio)")
        caminhos.append(caminho)
    return caminhos


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--codigo-ibge", default=c.CODIGO_IBGE_DEFAULT)
    p.add_argument("--nome-rio", default=c.NOME_RIO_DEFAULT, help="rótulo do rio principal na legenda")
    p.add_argument("--layout", default="lateral", choices=LAYOUTS, help="lateral (padrão, recalcula e grava tudo) ou a4 (só a edição A4 dos mapas)")
    a = p.parse_args()
    MAPAS_V2.mkdir(parents=True, exist_ok=True)
    camada = c.CAMADAS / "populacao-mudanca_ibge-censo_2010-2022_area-comparavel.gpkg"
    csv = c.TABELAS / "populacao-mudanca_ibge-censo_2010-2022_area-comparavel.csv"
    if not camada.exists():
        raise SystemExit(f"{camada} ausente — rode dinamica_populacional_areas_comparaveis.py")
    g = gpd.read_file(camada)
    novas = [k for k in g.columns if k.startswith(("dif_pp_", "classe_rel_", "ganho_absoluto_"))]
    g = g.drop(columns=novas)  # idempotente: recalcula as colunas desta etapa
    cols_orig = [k for k in g.columns if k != "geometry"]

    var_pop, info_pop = relativo(g, "pop")
    var_dom, info_dom = relativo(g, "dom")
    g["ganho_absoluto_pop"] = g.var_pop_abs > 0
    g["ganho_absoluto_dom"] = (g.var_dom_abs > 0).astype("boolean")
    sem_dom = (g.dom_2010 == 0) & (g.dom_2022 == 0)
    g.loc[sem_dom, "ganho_absoluto_dom"] = pd.NA
    novas = ["dif_pp_pop", "classe_rel_pop", "classe_rel_pop_lim10_20", "ganho_absoluto_pop",
             "dif_pp_dom", "classe_rel_dom", "classe_rel_dom_lim10_20", "ganho_absoluto_dom"]
    g = g[cols_orig + novas + ["geometry"]]

    def mapas(layout):
        base = Base(a.codigo_ibge, agua=True, nome_rio=a.nome_rio)
        fm = "Fonte: IBGE — histórico de formação dos setores 2010–2022; agregados por setor 2010 e 2022; geometria da malha 2022."
        out = mapa_rel(base, g, "pop", var_pop, "Variação da população 2010–2022 relativa à do município, por área comparável",
                       "populacao-variacao-relativa_ibge-censo_2010-2022_area-comparavel", fm, layout=layout)
        out += mapa_rel(base, g, "dom", var_dom, "Variação dos domicílios 2010–2022 relativa à do município, por área comparável",
                        "domicilios-variacao-relativa_ibge-censo_2010-2022_area-comparavel", fm, layout=layout)
        return out

    if a.layout == "a4":  # edição A4: só os mapas, a partir das classes calculadas em memória (nada é gravado fora de mapas_a4/)
        for f in mapas("a4"):
            logger.info("Mapa A4: %s", f.relative_to(c.RAIZ))
        return

    # ----- sensibilidade
    sens = {}
    for tema in ("pop", "dom"):
        base_cl = g[f"classe_rel_{tema}"]
        alt = g[f"classe_rel_{tema}_lim10_20"]
        ok = base_cl.notna()
        sens[tema] = {"limiares_principais_pp": "±5 / ±15", "limiares_alternativos_pp": "±10 / ±20",
                      "areas_que_mudam_de_classe": int((base_cl[ok] != alt[ok]).sum()),
                      "transicoes": {f"{x} -> {y}": int(n) for (x, y), n in pd.crosstab(base_cl[ok], alt[ok]).stack().items() if x != y and n > 0},
                      "contagem_alternativa": alt.value_counts().reindex(CLASSES_REL).fillna(0).astype(int).to_dict()}

    # ----- tabelas
    base_meta = {"codigo_ibge": a.codigo_ibge, "crs": c.CRS_PADRAO, "status": c.STATUS_CONFERENCIA, "ligado_ao_portal": False, "script": SCRIPT}
    fonte = ("IBGE — histórico de formação dos setores 2010–2022; agregados por setor 2010 e 2022 "
             "(camada populacao-mudanca_ibge-censo_2010-2022_area-comparavel)")
    defin = {"media_do_municipio": "variação % do município inteiro (soma das áreas = total do SIDRA), não a média das áreas",
             "dif_pp": "variação % da área − variação % do município, em pontos percentuais",
             "classes_pp": {"muito abaixo da média": "< -15", "abaixo da média": "-15 a -5", "perto da média": "-5 a +5",
                            "acima da média": "+5 a +15", "muito acima da média": "> +15"},
             "nota": "área sem valor em 2010 e com valor em 2022 = muito acima da média; sem domicílio particular nos dois anos = sem valor"}
    feitos = []
    for tema, var_mun, info in (("pop", var_pop, info_pop), ("dom", var_dom, info_dom)):
        t = tabela_classes(g, tema)
        nome = {"pop": "populacao", "dom": "domicilios"}[tema]
        arq = c.TABELAS / f"{nome}-variacao-relativa-classes_ibge-censo_2010-2022_area-comparavel.csv"
        t.to_csv(arq, index=False)
        c.gravar_meta(arq, **base_meta, fonte=fonte, **defin, **info, sensibilidade=sens[tema])
        feitos.append(arq)
        print(f"\n== {tema}: município {var_mun:+.3f} %  {info}")
        print(t.round(1).to_string(index=False))
        print(json.dumps(sens[tema], ensure_ascii=False, indent=1))

    # cruzamento população × domicílios
    def sentido(s):
        return np.where(s > 0, "ganhou", np.where(s < 0, "perdeu", "igual"))
    cx = pd.crosstab(pd.Series(sentido(g.var_pop_abs), name="populacao"), pd.Series(np.where(sem_dom, "sem domicílio particular", sentido(g.var_dom_abs)), name="domicilios"))
    cr = pd.crosstab(g.classe_rel_pop.rename("classe_rel_pop"), g.classe_rel_dom.fillna("sem valor").rename("classe_rel_dom")).reindex(index=CLASSES_REL).fillna(0).astype(int)
    cr = cr[[k for k in CLASSES_REL + ["sem valor"] if k in cr.columns]]
    arq = c.TABELAS / "populacao-domicilios-cruzamento_ibge-censo_2010-2022_area-comparavel.csv"
    with arq.open("w", encoding="utf-8") as f:
        f.write("# bloco 1: sentido da variação absoluta (áreas)\n")
        cx.to_csv(f)
        f.write("# bloco 2: classe relativa da população (linhas) x classe relativa dos domicílios (colunas) (áreas)\n")
        cr.to_csv(f)
    perde_ganha = g[(g.var_pop_abs < 0) & (g.var_dom_abs > 0)]
    cruz = {"areas_perdem_pop_e_ganham_dom": len(perde_ganha), "pop_2022_dessas_areas": float(perde_ganha.pop_2022.sum()),
            "areas_ganham_pop_e_ganham_dom": int(((g.var_pop_abs > 0) & (g.var_dom_abs > 0)).sum()),
            "areas_perdem_pop_e_perdem_dom": int(((g.var_pop_abs < 0) & (g.var_dom_abs < 0)).sum())}
    c.gravar_meta(arq, **base_meta, fonte=fonte, descricao="cruzamento população × domicílios por área comparável, 2010–2022: "
                  "sentido da variação absoluta e classes relativas à variação do município", **cruz,
                  variacao_pct_municipio={"populacao": var_pop, "domicilios": var_dom})
    feitos.append(arq)
    print("\n== cruzamento\n", cx.to_string(), "\n", cr.to_string(), "\n", cruz)

    # ----- camada e CSV (colunas acrescentadas)
    g.to_file(camada, driver="GPKG", layer="areas_comparaveis_2010_2022")
    meta_camada = json.loads(camada.with_suffix(".json").read_text(encoding="utf-8"))
    meta_camada["rodada_02_leitura_relativa"] = {"colunas_acrescentadas": novas, "script": SCRIPT, **defin,
                                                 "variacao_pct_municipio": {"populacao": var_pop, "domicilios": var_dom},
                                                 "data": pd.Timestamp.now(tz="UTC").isoformat(timespec="seconds")}
    camada.with_suffix(".json").write_text(json.dumps(meta_camada, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    antigo = pd.read_csv(csv, dtype=str)
    novo = g.drop(columns="geometry")
    assert list(antigo.id_area) == list(novo.id_area.astype(str)), "ordem das áreas difere do CSV existente"
    antigo = antigo[[k for k in antigo.columns if k not in novas]]
    for k in novas:
        antigo[k] = novo[k].values
    antigo.to_csv(csv, index=False)
    meta_csv = json.loads(csv.with_suffix(".json").read_text(encoding="utf-8"))
    meta_csv["rodada_02_leitura_relativa"] = meta_camada["rodada_02_leitura_relativa"]
    csv.with_suffix(".json").write_text(json.dumps(meta_csv, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    # ----- mapas
    feitos += mapas("lateral")
    for f in feitos:
        logger.info("Produto: %s", f.relative_to(c.RAIZ))


if __name__ == "__main__":
    main()
