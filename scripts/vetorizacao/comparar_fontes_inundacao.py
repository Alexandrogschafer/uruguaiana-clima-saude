"""
Comparação entre fontes de mancha de inundação (vetor oficial e camadas
vetorizadas de figura) dentro de um DOMÍNIO COMUM.

Cada fonte mapeou uma área diferente; comparar áreas totais engana. O domínio
comum D é a terra coberta pelo modelo de terreno de uma fonte de base (sem o
leito do rio), cortada pelos quadros dos mapas das fontes listadas e pelo
município (config/area_estudo.geojson). Fontes, cenários, domínio e pares em
destaque vêm do arquivo de fontes (.json); nada de lugar ou de fonte no código.

Saídas em data/processed/vetorizado/ (fora do git: derivam de camadas não
oficiais), com .json irmão:
  - o domínio D (GeoPackage);
  - área em terra de cada cenário dentro de D (tabela longa);
  - concordância entre todos os pares de cenários dentro de D: índice de
    Jaccard (interseção sobre união, %) e diferença simétrica (km²), em formato
    longo e em matriz;
  - figura de comparação dos contornos (edição A4: 16 cm, 300 dpi, legenda
    abaixo), no recorte urbano.
Os valores ficam sem arredondar; toda exibição arredonda a partir deles.

Uso:
  python scripts/vetorizacao/comparar_fontes_inundacao.py --fontes scripts/vetorizacao/fontes_inundacao.json
"""

from __future__ import annotations

import argparse
import logging
import sys
from itertools import combinations
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1] / "processamento"))

import geopandas as gpd  # noqa: E402
import matplotlib  # noqa: E402

matplotlib.use("Agg")
import pandas as pd  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

import dinamica_populacional_comum as c  # noqa: E402
from exposicao_inundacao_cenarios import MARCA, cenarios_da_fonte, ler_fontes  # noqa: E402

logger = logging.getLogger(__name__)
SCRIPT = "scripts/vetorizacao/comparar_fontes_inundacao.py"
SAIDA = c.RAIZ / "data" / "processed" / "vetorizado"
ESTILOS = [("#d7191c", "-", 1.3), ("#0b0b0b", "-", 1.1), ("#e08214", "-", 1.2), ("#7b3294", "-", 1.2), ("#2a78d6", "--", 1.1), ("#2a78d6", "-", 1.3)]


def dominio_comum(cfg: dict):
    d, por_id = cfg["dominio"], cfg["por_id"]
    fb = por_id[d["base"]["fonte"]]
    g = gpd.read_file(c.RAIZ / fb["arquivo"], layer=d["base"]["camada"]).to_crs(c.CRS_PADRAO)
    geom = g[~g[d["base"]["atributo"]].isin(d["base"]["excluir"])].geometry.buffer(0).union_all()
    partes = {"base_terra_km2": geom.area / 1e6}
    for fid in d["quadros"]:  # reprojeta explicitamente antes de cada interseção
        q = gpd.read_file(c.RAIZ / por_id[fid]["arquivo"], layer=por_id[fid]["quadro"]).to_crs(c.CRS_PADRAO).union_all()
        geom = geom.intersection(q)
        partes[f"apos_quadro_{fid}_km2"] = geom.area / 1e6
    geom = geom.intersection(c.carregar_area_estudo().to_crs(c.CRS_PADRAO).union_all()).buffer(0)
    partes["apos_municipio_km2"] = geom.area / 1e6
    return geom, partes


def figura(cfg: dict, cens: dict, D, base_meta: dict) -> Path:
    import layout_mapa as lm
    from dinamica_populacional_cnefe_mapas import Fundo
    from dinamica_populacional_mapas import Base

    base = Base(ARGS.codigo_ibge, agua=True, nome_rio=ARGS.nome_rio, modo_agua="enderecos")
    fundo = Fundo(base)
    ext = base.ext_urb
    lay = lm.LayoutA4([[ext]])
    ax = lay.eixos[0]
    fundo.desenhar(ax, ext, "urbano", 1000, escala_pos=(0.03, 0.93))
    gpd.GeoSeries([D], crs=c.CRS_PADRAO).plot(ax=ax, facecolor="#f3e9c9", edgecolor="none", alpha=0.55, zorder=0.5)
    gpd.GeoSeries([D], crs=c.CRS_PADRAO).boundary.plot(ax=ax, color="#8a6d1b", linewidth=0.7, linestyle=":", zorder=6)
    hand = []
    todos = pd.concat(cens.values())
    situ = {f["id"]: f["situacao"] for f in cfg["fontes"]}
    for nome, (cor, ls, lw) in zip(cfg["figura_contornos"], ESTILOS):
        r = todos[todos.cenario == nome].iloc[0]
        linha = r.geometry.boundary
        if cfg["por_id"][r.fonte].get("quadro"):  # tira do contorno o que é só a moldura do mapa de origem (ou a caixa de legenda)
            f = cfg["por_id"][r.fonte]
            q = gpd.read_file(c.RAIZ / f["arquivo"], layer=f["quadro"]).to_crs(c.CRS_PADRAO).union_all()
            linha = linha.difference(q.boundary.buffer(30))
        gpd.GeoSeries([linha], crs=c.CRS_PADRAO).plot(ax=ax, color=cor, linewidth=lw, linestyle=ls, zorder=7)
        hand.append(Line2D([], [], color=cor, lw=lw + 0.3, ls=ls, label=nome + (" (preliminar)" if situ[r.fonte] == "extraído" else "")))
    hand.append(Patch(facecolor="#f3e9c9", edgecolor="#8a6d1b", linestyle=":", linewidth=0.7, alpha=0.8, label="domínio comum D (terra coberta por todas as fontes)"))
    destino = SAIDA / "comparacao-contornos_fontes-inundacao_2026_urbano.png"
    info = lm.finalizar_a4(lay, "Manchas de inundação segundo a fonte: contornos de seis cenários\nárea urbana da sede", fundo.comum(hand), "contorno da mancha, por fonte e cenário",
                           "Fontes: SGB (manchas por cota, vetor oficial); V001 a V004: camadas extraídas de figura, não oficiais (ver docs/vetorizacao/inventario.csv); "
                           "OpenStreetMap (água, vias).", destino, origem=destino,
                           metodo="As manchas de V001 incluem o leito do rio. Fora do quadro de cada mapa de origem não há dado; a moldura não é desenhada.")
    c.gravar_meta(destino, descricao="contornos de seis cenários de inundação de fontes diferentes sobre a malha viária, com o domínio comum marcado; recorte urbano",
                  cenarios=cfg["figura_contornos"], janela_m=[round(v) for v in ext], crs=c.CRS_PADRAO, **info, **base_meta)
    return destino


def main() -> None:
    cfg = ler_fontes(ARGS.fontes)
    SAIDA.mkdir(parents=True, exist_ok=True)
    base_meta = dict(codigo_ibge=ARGS.codigo_ibge, status=c.STATUS_CONFERENCIA, ligado_ao_portal=False, script=SCRIPT, arquivo_de_fontes=str(ARGS.fontes),
                     fora_do_git="data/processed/ é ignorado; deriva de camadas não oficiais — não publicar",
                     marca="fontes na situação 'extraído' são preliminares (pendentes de conferência): " + ", ".join(f["id"] for f in cfg["fontes"] if f["situacao"] == "extraído"))
    D, partes = dominio_comum(cfg)
    arq = SAIDA / (cfg["dominio"]["nome_saida"] + ".gpkg")
    gpd.GeoDataFrame([{"descricao": cfg["dominio"]["descricao"], "area_km2": D.area / 1e6, "geometry": D}], crs=c.CRS_PADRAO).to_file(arq, driver="GPKG", layer="dominio_comum")
    c.gravar_meta(arq, descricao=cfg["dominio"]["descricao"], area_km2=D.area / 1e6, construcao_km2=partes, crs=c.CRS_PADRAO, **base_meta)
    logger.info("Domínio comum: %.4f km² %s", D.area / 1e6, {k: round(v, 3) for k, v in partes.items()})

    cens = {f["id"]: cenarios_da_fonte(f) for f in cfg["fontes"] if f["na_comparacao"]}
    situ = {f["id"]: f for f in cfg["fontes"]}
    todos = pd.concat(cens.values(), ignore_index=True)
    todos["em_d"] = [g.intersection(D) for g in todos.geometry]
    ta = pd.DataFrame([{"fonte": r.fonte, "cenario": r.cenario, "tipo": situ[r.fonte]["tipo"], "situacao_da_camada": situ[r.fonte]["situacao"],
                        "cota_cm": r.cota_cm, "altitude_m": r.altitude_m, "tr_anos": r.tr_anos, "area_terra_no_dominio_km2": r.em_d.area / 1e6,
                        "pct_do_dominio": 100 * r.em_d.area / D.area, "area_terra_no_dominio_nao_cumulativa_km2": r.geom_propria.intersection(D).area / 1e6,
                        "area_total_da_mancha_km2": r.geometry.area / 1e6,
                        "marca": MARCA.get(situ[r.fonte]["situacao"], "")} for r in todos.itertuples()])
    arq = SAIDA / "areas-por-cenario_fontes-inundacao_2026_dominio-comum.csv"
    ta.round(6).to_csv(arq, index=False)
    c.gravar_meta(arq, descricao="uma linha por fonte e cenário: tipo, cota (cm), altitude (m) e tempo de retorno quando houver; área em terra dentro do domínio comum D (cumulativa: união das manchas dos cenários até este, como na exposição por endereços; a coluna _nao_cumulativa traz só a mancha do cenário)", area_dominio_km2=D.area / 1e6, **base_meta)

    pares = []
    for a, b in combinations(todos.itertuples(), 2):
        inter, uni = a.em_d.intersection(b.em_d).area, a.em_d.union(b.em_d).area
        pares.append({"cenario_a": a.cenario, "cenario_b": b.cenario, "area_a_km2": a.em_d.area / 1e6, "area_b_km2": b.em_d.area / 1e6, "intersecao_km2": inter / 1e6,
                      "uniao_km2": uni / 1e6, "jaccard_pct": 100 * inter / uni if uni else float("nan"), "diferenca_simetrica_km2": (uni - inter) / 1e6,
                      "so_em_a_km2": (a.em_d.area - inter) / 1e6, "so_em_b_km2": (b.em_d.area - inter) / 1e6})
    tp = pd.DataFrame(pares)
    dest = {frozenset(p) for p in cfg["pares_destacados"]}
    tp["destacado"] = [frozenset((r.cenario_a, r.cenario_b)) in dest for r in tp.itertuples()]
    arq = SAIDA / "concordancia-pares_fontes-inundacao_2026_dominio-comum.csv"
    tp.round(6).to_csv(arq, index=False)
    c.gravar_meta(arq, descricao="todos os pares de cenários dentro do domínio comum: índice de Jaccard (interseção sobre união, %) e diferença simétrica (km²)", area_dominio_km2=D.area / 1e6, **base_meta)
    nomes = list(todos.cenario)
    for col, rot in (("jaccard_pct", "jaccard-pct"), ("diferenca_simetrica_km2", "diferenca-simetrica-km2")):
        m = pd.DataFrame(index=nomes, columns=nomes, dtype=float)
        for r in tp.itertuples():
            m.loc[r.cenario_a, r.cenario_b] = m.loc[r.cenario_b, r.cenario_a] = getattr(r, col)
        for n in nomes:
            m.loc[n, n] = 100.0 if col == "jaccard_pct" else 0.0
        arq = SAIDA / f"concordancia-matriz-{rot}_fontes-inundacao_2026_dominio-comum.csv"
        m.round(6).to_csv(arq, index_label="cenario")
        c.gravar_meta(arq, descricao=f"matriz de concordância entre cenários dentro do domínio comum: {col}", area_dominio_km2=D.area / 1e6, **base_meta)
    fig = figura(cfg, cens, D, base_meta)
    logger.info("Figura: %s", fig.relative_to(c.RAIZ))

    pd.set_option("display.width", 250)
    pd.set_option("display.max_columns", 30)
    print(ta.drop(columns=["marca"]).round(3).to_string(index=False))
    print(tp[tp.destacado].drop(columns=["destacado"]).round(3).to_string(index=False))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    _p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    _p.add_argument("--fontes", type=Path, required=True, help="arquivo de fontes e cenários (.json)")
    _p.add_argument("--codigo-ibge", default=c.CODIGO_IBGE_DEFAULT)
    _p.add_argument("--nome-rio", default=c.NOME_RIO_DEFAULT, help="rótulo do rio principal na legenda")
    ARGS = _p.parse_args()
    main()
