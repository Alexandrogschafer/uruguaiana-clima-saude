"""
Produtos de inundação para a tela "Cheia e acesso à saúde" em data/paineis/:

    inundacao_sgb_cotas            uma linha por cota: cota (cm), tempo de retorno (camada SGB e documento),
                                   área da mancha acumulada (km²), período de referência, fonte, número de
                                   ESF/UBS dentro da mancha, mini-mapa e texto alternativo
    inundacao_sgb_unidades-cotas   as 22 unidades do cadastro v4 com "dentro da mancha" por cota
                                   (a Equipe de Saúde Prisional vai marcada como fora das comparações)
    img/mini-mapa_cota-<cota>.png  limite municipal, mancha da cota e as ESF/UBS como pontos

Fontes: manchas SGB por cota (data/raw/vetor/cotas-inundacao_sgb_atual_vetorial.gpkg, a mesma origem da
camada do portal), transcrição do documento do SGB (tempo de retorno e período: data/processed/
conferencia_fontes_inundacao/manchas-por-cota_sgb_2023_transcricao.csv), unidades do cadastro revisado v4
(data/processed/saude/) e limite municipal (config/area_estudo.geojson, via scripts/utils/recorte_municipio.py).

Regras: mancha acumulada = união das manchas de cota menor ou igual (definição do produto de estatísticas
do portal); geometria corrigida com buffer(0); áreas e teste "dentro" em EPSG:31981 (SIRGAS 2000 / UTM 21S).
Contagem só das 21 ESF/UBS; a Prisional fica fora. NENHUM número de pessoas ou endereços (os ativos de
expostos e de acesso seguem em conferência). Mapa sem fundo baixado.

Uso:
    python scripts/paineis/dados_inundacao.py [--codigo-ibge 4322400] [--forcar] [--so <id>]
"""

from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

import geopandas as gpd
import matplotlib
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI))
sys.path.insert(0, str(AQUI.parent / "utils"))
import comum  # noqa: E402
from recorte_municipio import CRS_PADRAO, carregar_area_estudo  # noqa: E402

RAIZ = comum.RAIZ
SCRIPT = "scripts/paineis/dados_inundacao.py"
E = {
    "manchas": RAIZ / "data/raw/vetor/cotas-inundacao_sgb_atual_vetorial.gpkg",
    "transcricao": RAIZ / "data/processed/conferencia_fontes_inundacao/manchas-por-cota_sgb_2023_transcricao.csv",
    "unidades": RAIZ / "data/processed/saude/unidades-saude-esf-ubs_cnes-revisado-v4_2026_pontos.gpkg",
    "limite": RAIZ / "config/area_estudo.geojson",
}
PASTA_IMG = comum.PASTA_PUBLICACAO / "img"
LIMITE_PNG = 150 * 1024
FONTE_RODAPE = "Manchas de inundação por cota — SGB (Serviço Geológico do Brasil), relatório de 2023"
NOTA_SUPRESSAO = "não se aplica: sem contagem de pessoas; só número de unidades de saúde"
AVISO_CONFERENCIA = "pessoas expostas e acesso: em conferência"


def manchas_acumuladas() -> list[tuple[int, float, object]]:
    g = gpd.read_file(E["manchas"]).to_crs(CRS_PADRAO)
    g["geometry"] = g.geometry.buffer(0)
    cotas = sorted(int(c) for c in g["cota_cm"].unique())
    out, acum = [], None
    for c in cotas:
        uni = g[g["cota_cm"] == c].geometry.union_all()
        acum = uni if acum is None else acum.union(uni)
        tr = round(float(g[g["cota_cm"] == c]["tr_anos"].iloc[0]), 2)  # a camada guarda em float32 (1,29999995)
        out.append((c, tr, acum))
    return out


def unidades() -> gpd.GeoDataFrame:
    u = gpd.read_file(E["unidades"]).to_crs(CRS_PADRAO)
    if len(u) != 22:
        raise ValueError(f"esperadas 22 unidades no cadastro v4, há {len(u)}")
    u["comparavel"] = u["classe"].isin(["ESF", "UBS"])
    if int(u["comparavel"].sum()) != 21:
        raise ValueError("esperadas 21 ESF/UBS")
    return u


def mini_mapa(cota: int, mancha, limite: gpd.GeoDataFrame, u: gpd.GeoDataFrame) -> bytes:
    """PNG pequeno: enquadra a mancha da maior cota e as unidades urbanas; sem mapa de fundo."""
    fig, ax = plt.subplots(figsize=(5.2, 4.0), dpi=100)
    limite.boundary.plot(ax=ax, color="#7c8f8c", linewidth=0.8)
    gpd.GeoSeries([mancha], crs=CRS_PADRAO).plot(ax=ax, color="#2a78d6", alpha=0.55, linewidth=0)
    comp = u[u["comparavel"]]
    comp.plot(ax=ax, color="#d95926", markersize=14, edgecolor="white", linewidth=0.6, zorder=3)
    ax.set_xlim(*EXTENSAO[0]); ax.set_ylim(*EXTENSAO[1])
    ax.set_aspect("equal"); ax.set_xticks([]); ax.set_yticks([])
    for s in ax.spines.values():
        s.set_color("#c7cec9")
    ax.legend(handles=[Patch(color="#2a78d6", alpha=0.55, label=f"mancha da cota {cota} cm"),
                       Line2D([], [], marker="o", color="w", markerfacecolor="#d95926", markersize=6, label="ESF/UBS"),
                       Line2D([], [], color="#7c8f8c", linewidth=0.8, label="limite municipal")],
              loc="upper right", fontsize=7, frameon=True, framealpha=0.9)
    ax.text(0.01, 0.01, "Manchas: SGB · Unidades: CNES revisado v4", transform=ax.transAxes, fontsize=6.5, color="#4b5f5c")
    fig.tight_layout(pad=0.3)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=100, metadata={"Software": None})  # sem versão no arquivo: PNG estável entre execuções
    plt.close(fig)
    return buf.getvalue()


EXTENSAO = None


def ids() -> dict:
    return {"cotas": "inundacao_sgb_cotas", "unidades": "inundacao_sgb_unidades-cotas"}


def gerar(codigo_ibge: str = "4322400", forcar: bool = False, so: str | None = None) -> dict[str, bool]:
    global EXTENSAO
    mm = manchas_acumuladas()
    u = unidades()
    tr_doc = pd.read_csv(E["transcricao"]).set_index("cota_cm")
    limite = carregar_area_estudo(E["limite"]).to_crs(CRS_PADRAO)
    lim_geom = limite.union_all()
    # enquadramento comum: maior mancha e unidades da sede, com margem de 1 km
    urb = u[u["zona"] == "urbana da sede"]
    x0, y0, x1, y1 = gpd.GeoSeries([mm[-1][2]] + list(urb.geometry), crs=CRS_PADRAO).total_bounds
    EXTENSAO = ((x0 - 1000, x1 + 1000), (y0 - 1000, y1 + 1000))
    PASTA_IMG.mkdir(parents=True, exist_ok=True)
    linhas, dentro = [], {}
    for cota, tr, geom in mm:
        dentro[cota] = u.geometry.within(geom)
        n_dentro = int((dentro[cota] & u["comparavel"]).sum())
        img = PASTA_IMG / f"mini-mapa_cota-{cota}.png"
        png = mini_mapa(cota, geom, limite, u)
        if len(png) > LIMITE_PNG:
            raise ValueError(f"{img.name} com {len(png)} bytes (> 150 kB)")
        if not img.exists() or img.read_bytes() != png:
            img.write_bytes(png)
        linhas.append({"cota_cm": cota, "tr_anos_camada": tr, "tr_anos_documento": float(tr_doc.loc[cota, "tr_anos_documento"]),
                       "area_km2": round(geom.area / 1e6, 2), "area_no_municipio_km2": round(geom.intersection(lim_geom).area / 1e6, 2),
                       "periodo_referencia": tr_doc.loc[cota, "periodo_de_referencia"],
                       "esf_ubs_dentro": n_dentro, "imagem": f"img/{img.name}",
                       "alt": (f"Mapa da mancha de inundação da cota {cota} cm (SGB) sobre o limite de Uruguaiana, com as 21 ESF/UBS "
                               f"como pontos laranja: {n_dentro} dentro da mancha.")})
    if any(l["esf_ubs_dentro"] for l in linhas):
        raise ValueError("cálculo deu ESF/UBS dentro de mancha; parar e conferir (esperado: 0 em todas as cotas)")
    dif_tr = [l["cota_cm"] for l in linhas if abs(l["tr_anos_camada"] - l["tr_anos_documento"]) > 0.01]
    notas = {
        "cs_nota_unidades": (f"Nenhuma das 21 ESF/UBS fica dentro das manchas do SGB em nenhuma das {len(linhas)} cotas "
                             f"({', '.join(str(l['cota_cm']) for l in linhas)} cm). A Equipe de Saúde Prisional fica fora da contagem. "
                             "Estar fora da mancha não garante acesso pelas ruas: o cálculo de acesso está em conferência."),
        "cs_frase_unidades": f"{max(l['esf_ubs_dentro'] for l in linhas)} de 21 ESF/UBS dentro da mancha em todas as cotas",
        "cs_nota_tr": (f"Tempo de retorno: o da camada do SGB; {'na cota' if len(dif_tr) == 1 else 'nas cotas'} "
                       f"{', '.join(map(str, dif_tr))} cm, o documento do SGB traz outro valor (coluna à direita)."
                       if dif_tr else "Tempo de retorno da camada do SGB, igual ao do documento."),
    }
    c = dict(codigo_ibge=codigo_ibge, script=SCRIPT, unidade_espacial="mancha de inundação (município)", selo="em_confirmacao")
    p1 = comum.montar_produto(
        id=ids()["cotas"], ativos=["IN01", "IN03", "SA01"], titulo="Manchas de inundação por cota e ESF/UBS dentro delas",
        fonte_curta="SGB", fonte="SGB, manchas de inundação por cota (relatório de 2023); unidades do CNES revisado v4",
        unidade_medida="cm; anos; km²; unidades", periodo={"inicio": "2015", "fim": "2023"}, dados_ate="2023",
        avisos=[{"tipo": "em_confirmacao", "texto": AVISO_CONFERENCIA},
                {"tipo": "metodo", "texto": "mancha acumulada: união das manchas de cota menor ou igual; área em SIRGAS 2000 / UTM 21S, da mancha inteira como o SGB publica (inclui rio e margem fora do município) e só da parte dentro do município"},
                {"tipo": "metodo", "texto": "Equipe de Saúde Prisional fora da contagem de unidades"}],
        colunas=[{"nome": "cota_cm", "tipo": "int"}, {"nome": "tr_anos_camada", "tipo": "float"}, {"nome": "tr_anos_documento", "tipo": "float"},
                 {"nome": "area_km2", "tipo": "float"}, {"nome": "area_no_municipio_km2", "tipo": "float"},
                 {"nome": "periodo_referencia", "tipo": "texto"}, {"nome": "esf_ubs_dentro", "tipo": "int"},
                 {"nome": "imagem", "tipo": "texto"}, {"nome": "alt", "tipo": "texto"}],
        dados=linhas, extras={"fonte_rodape": FONTE_RODAPE, "telas": ["sintese", "cheia-saude"], "notas": notas, "comparacao": False}, **c)
    lu = []
    for i, r in u.reset_index(drop=True).iterrows():
        linha = {"cnes": str(r["cnes"]), "nome": r["nome"], "classe": r["classe"], "fora_das_comparacoes": not bool(r["comparavel"])}
        for cota in dentro:
            linha[f"dentro_{cota}"] = bool(dentro[cota].iloc[i])
        lu.append(linha)
    lu = [l for l in lu if not l["fora_das_comparacoes"]] + [l for l in lu if l["fora_das_comparacoes"]]
    p2 = comum.montar_produto(
        id=ids()["unidades"], ativos=["SA01", "IN01"], titulo="ESF/UBS em relação às manchas de inundação", fonte_curta="SGB; CNES revisado v4",
        fonte="SGB (manchas por cota) e CNES revisado pela equipe do projeto (versão 4)", unidade_medida="sim/não", periodo={"inicio": "2023", "fim": "2026"},
        dados_ate="2026", avisos=[{"tipo": "metodo", "texto": "Equipe de Saúde Prisional fora das comparações"}],
        colunas=[{"nome": k, "tipo": "texto" if k in ("cnes", "nome", "classe") else "bool"} for k in lu[0]],
        dados=lu, extras={"fonte_rodape": FONTE_RODAPE, "telas": ["cheia-saude"], "comparacao": False}, **c)
    out = {}
    ent = [E["manchas"], E["transcricao"], E["unidades"], E["limite"]]
    for p, transf in ((p1, "união acumulada das manchas por cota em EPSG:31981; área; teste ponto-dentro das 21 ESF/UBS; mini-mapas em matplotlib"),
                      (p2, "teste ponto-dentro de cada unidade contra a mancha acumulada de cada cota")):
        if so and so != p["id"]:
            continue
        out[p["id"]] = comum.gravar_produto(p, transformacao=transf, entradas=ent, forcar=forcar, extras_metadado={"supressao_nota": NOTA_SUPRESSAO})
    return out


PRODUTOS = list(ids().values())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--codigo-ibge", default="4322400")
    ap.add_argument("--forcar", action="store_true")
    ap.add_argument("--so")
    a = ap.parse_args()
    try:
        for pid, g in gerar(a.codigo_ibge, a.forcar, a.so).items():
            print(f"{pid}: {'gravado' if g else 'sem mudança'}")
    except (OSError, ValueError, KeyError) as erro:
        print(f"erro: {erro}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
