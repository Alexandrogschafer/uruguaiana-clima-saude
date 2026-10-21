"""
Edificações do Overture Maps no acervo (rodada 68): camada vetorial derivada,
sem portal.

Usa o parquet do tema buildings já baixado em
data/raw/fontes_complementares/overture_<versão>/buildings.parquet (retângulo
da área de estudo + 2 km). Só baixa se faltar (ou com --rebaixar), pela mesma
consulta duckdb da rodada 67 (venv FORA do repositório: --python-overture ou
a variável OVERTURE_PYTHON).

Produto: data/processed/edificacoes/edificacoes_overture-<versão>_2026_vetorial.gpkg
(EPSG:31981), camada `edificacoes`: id, fonte (Google Open Buildings /
Microsoft ML / OSM), area_m2, zona (urbano/rural, áreas urbanizadas IBGE 2019),
bairro (IBGE 2022, ou 'fora') e alerta. Nada é apagado: candidatos a falso
positivo no rural (área < 15 m², > 5.000 m², sobre água ou mata) só ganham
texto na coluna `alerta`. Nenhum atributo de nome ou contato é copiado.

Uso:
    python scripts/download/edificacoes_overture.py --codigo-ibge 4322400 --saida-tabelas <pasta>
"""

import argparse
import json
import logging
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import rasterio
import shapely

RAIZ = Path(__file__).resolve().parents[2]
sys.path.append(str(RAIZ / "scripts" / "utils"))
sys.path.append(str(RAIZ / "scripts" / "exploracao"))
from recorte_municipio import CAMINHO_AREA_ESTUDO_PADRAO, CRS_PADRAO, carregar_area_estudo  # noqa: E402
from osm_levantamento import REF  # noqa: E402
from fontes_complementares import SQL_OVERTURE  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

VERSAO = "2026-09-23.1"
PASTA_OVERTURE = RAIZ / "data" / "raw" / "fontes_complementares" / f"overture_{VERSAO}"
PARQUET = PASTA_OVERTURE / "buildings.parquet"
SAIDA = RAIZ / "data" / "processed" / "edificacoes" / f"edificacoes_overture-{VERSAO}_2026_vetorial.gpkg"
MAPBIOMAS = RAIZ / "data" / "raw" / "raster" / "uso-solo_mapbiomas_2024_30m.tif"
EXTRATO_OSM = RAIZ / "data" / "raw" / "osm_levantamento" / "osm_completo_2026-10-21.gpkg"
FONTES = {"Google Open Buildings": "Google Open Buildings", "Microsoft ML Buildings": "Microsoft ML", "OpenStreetMap": "OSM"}
ATRIBUICAO = "Overture Maps Foundation; © contribuidores do OpenStreetMap; Google Open Buildings; Microsoft"
AREA_MIN, AREA_MAX = 15.0, 5000.0
MB_AGUA = {33, 31}  # MapBiomas col. 10: rio/lago/oceano; aquicultura
MB_MATA = {3, 4, 5, 6, 9, 49}  # formação florestal, savânica, mangue, floresta alagável, silvicultura, restinga arbórea


def baixar(python_ext: str | None) -> None:
    """Mesma consulta da rodada 67 (bbox do município + 2 km), só para o tema buildings."""
    if not python_ext:
        raise SystemExit("parquet ausente: informe --python-overture (venv com duckdb, fora do repositório)")
    mun = carregar_area_estudo()
    w, s, e, n = mun.buffer(2000).to_crs("EPSG:4326").total_bounds
    PASTA_OVERTURE.mkdir(parents=True, exist_ok=True)
    logger.info("Overture %s: baixando buildings (bbox %.4f %.4f %.4f %.4f)", VERSAO, w, s, e, n)
    subprocess.run([python_ext, "-c", SQL_OVERTURE, VERSAO, str(w), str(s), str(e), str(n), str(PASTA_OVERTURE),
                    json.dumps({"buildings": ["buildings", "building"]})], check=True, capture_output=True, text=True)
    meta = {"arquivo": PARQUET.name, "data_processamento": datetime.now(timezone.utc).isoformat(),
            "script": "scripts/download/edificacoes_overture.py", "fonte": "Overture Maps Foundation — tema buildings, tipo building",
            "versao": VERSAO, "url": f"s3://overturemaps-us-west-2/release/{VERSAO}/theme=buildings/type=building/",
            "licenca": "ODbL 1.0 (fontes OSM, Microsoft, Google — ver campo sources)", "bbox": [round(v, 5) for v in (w, s, e, n)],
            "atribuicao": ATRIBUICAO, "transformacao_aplicada": "consulta duckdb ao S3 público filtrando pelo bbox; geometria em WKB",
            "tamanho_mb": round(PARQUET.stat().st_size / 1e6, 2)}
    PARQUET.with_name(PARQUET.name + ".json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def carregar(g_mun) -> gpd.GeoDataFrame:
    # só id, fontes e geometria: o tema buildings pode ter nomes; não são copiados
    t = pq.read_table(PARQUET, columns=["id", "sources", "geom_wkb"]).to_pandas()
    g = gpd.GeoDataFrame(t.drop(columns=["geom_wkb"]), geometry=shapely.from_wkb(t["geom_wkb"]), crs="EPSG:4326").to_crs(CRS_PADRAO)
    g = g[g.intersects(g_mun)].copy()  # edificação inteira se tocar o município (não corta polígonos na divisa)
    g["fonte"] = g["sources"].map(lambda ss: FONTES.get(ss[0]["dataset"], ss[0]["dataset"]) if ss is not None and len(ss) else "desconhecida")
    return g.drop(columns=["sources"]).reset_index(drop=True)


def classes_mapbiomas(pts: gpd.GeoSeries) -> np.ndarray:
    with rasterio.open(MAPBIOMAS) as r:  # raster já em EPSG:31981
        return np.array([v[0] for v in r.sample(zip(pts.x, pts.y))])


def processar(saida_tab: Path, codigo: str) -> dict:
    mun = carregar_area_estudo()
    g_mun = mun.union_all()
    g_urb = gpd.read_file(REF["urbano"]).to_crs(CRS_PADRAO).union_all().intersection(g_mun)
    bai = gpd.read_file(REF["bairros"]).to_crs(CRS_PADRAO)
    b = carregar(g_mun)
    b["area_m2"] = b.area.round(1)
    pt = b.geometry.representative_point()
    b["zona"] = np.where(shapely.within(pt.values, g_urb), "urbano", "rural")
    j = gpd.sjoin(gpd.GeoDataFrame(geometry=pt, crs=CRS_PADRAO), bai[["NM_BAIRRO", "geometry"]], predicate="within", how="left")
    b["bairro"] = j["NM_BAIRRO"].groupby(level=0).first().reindex(b.index).fillna("fora")

    # alertas (só rural): área extrema; ponto interno sobre água/mata no MapBiomas 2024; ≥ 50% sobre água/mata do OSM
    pol = gpd.read_file(EXTRATO_OSM, layer="poligonos", columns=["natural", "landuse", "water"])
    agua = pol[pol["natural"].isin(["water", "wetland"]) | pol["water"].notna() | pol["landuse"].isin(["reservoir", "basin"])]
    mata = pol[pol["natural"].isin(["wood", "scrub"]) | pol["landuse"].isin(["forest"])]
    rur = b["zona"] == "rural"
    cls = pd.Series(classes_mapbiomas(pt[rur]), index=b.index[rur])
    al = {i: [] for i in b.index[rur]}

    def marca(mask, texto):
        for i in mask[mask].index:
            al[i].append(texto)

    br = b[rur]
    marca(br["area_m2"] < AREA_MIN, f"area<{AREA_MIN:.0f}m2")
    marca(br["area_m2"] > AREA_MAX, f"area>{AREA_MAX:.0f}m2")
    marca(cls.isin(MB_AGUA), "agua_mapbiomas")
    marca(cls.isin(MB_MATA), "mata_mapbiomas")
    for camada, texto in [(agua, "agua_osm"), (mata, "mata_osm")]:
        if camada.empty:
            continue
        u = camada.union_all()
        cand = br[br.intersects(u)]
        frac = cand.intersection(u).area / cand.area
        marca(pd.Series(frac >= 0.5, index=cand.index).reindex(br.index, fill_value=False), texto)
    b["alerta"] = b.index.map(lambda i: ";".join(al.get(i, [])) or None)
    b["mapbiomas_2024"] = cls.reindex(b.index).astype("Int64")

    SAIDA.parent.mkdir(parents=True, exist_ok=True)
    SAIDA.unlink(missing_ok=True)
    b[["id", "fonte", "area_m2", "zona", "bairro", "alerta", "mapbiomas_2024", "geometry"]].to_file(SAIDA, layer="edificacoes", driver="GPKG")

    # estatísticas
    saida_tab.mkdir(parents=True, exist_ok=True)
    pz = b.pivot_table(index="zona", columns="fonte", values="id", aggfunc="count", fill_value=0)
    pz["total"] = pz.sum(axis=1)
    pz["area_media_m2"] = b.groupby("zona")["area_m2"].mean().round(1)
    pz["area_mediana_m2"] = b.groupby("zona")["area_m2"].median().round(1)
    pz["n_com_alerta"] = b.groupby("zona")["alerta"].count()
    pz.loc["município"] = pz.sum(numeric_only=True)
    pz.loc["município", ["area_media_m2", "area_mediana_m2"]] = [round(b["area_m2"].mean(), 1), round(b["area_m2"].median(), 1)]
    conts = [c for c in pz.columns if c not in ("area_media_m2", "area_mediana_m2")]
    pz[conts] = pz[conts].astype(int)
    pz.reset_index().to_csv(saida_tab / "E1_edificacoes_por_zona.csv", index=False)
    pb = b.pivot_table(index="bairro", columns="fonte", values="id", aggfunc="count", fill_value=0)
    pb["total"] = pb.sum(axis=1)
    pb["area_total_m2"] = b.groupby("bairro")["area_m2"].sum().round(0)
    dom = bai.set_index("NM_BAIRRO")["enderecos_domicilio_particular"] if "enderecos_domicilio_particular" in bai else None
    if dom is not None:
        pb["enderecos_domicilio_cnefe"] = dom.reindex(pb.index)
        pb["razao_edificacoes_por_domicilio"] = (pb["total"] / pb["enderecos_domicilio_cnefe"]).round(3)
    pb.sort_values("total", ascending=False).reset_index().to_csv(saida_tab / "E2_edificacoes_por_bairro.csv", index=False)
    alr = b.loc[rur, "alerta"].dropna().str.split(";").explode().value_counts()
    alr.rename_axis("alerta").reset_index(name="n_rural").to_csv(saida_tab / "E3_alertas_rural.csv", index=False)

    # OSM (extrato da rodada 65) × Overture
    osm_ext = gpd.read_file(EXTRATO_OSM, layer="poligonos", columns=["building"])
    osm_ext = osm_ext[osm_ext["building"].notna() & osm_ext.intersects(g_mun)]
    ov_osm = b[b["fonte"] == "OSM"]
    sobre = gpd.sjoin(osm_ext[["geometry"]], b[b["fonte"] != "OSM"][["geometry"]], predicate="intersects").index.unique()
    comp = {"osm_extrato_2026_10_20": len(osm_ext), "overture_fonte_osm": len(ov_osm), "overture_total": len(b),
            "overture_google": int((b["fonte"] == "Google Open Buildings").sum()), "overture_microsoft": int((b["fonte"] == "Microsoft ML").sum()),
            "razao_overture_por_osm": round(len(b) / max(len(osm_ext), 1), 1),
            "osm_extrato_sobreposto_a_google_ou_microsoft": len(sobre),
            "nota": "o Overture deduplica: onde há edificação OSM, ela prevalece sobre Google/Microsoft (conflação da Overture)"}
    pd.DataFrame([comp]).T.to_csv(saida_tab / "E4_osm_x_overture.csv", header=["valor"])

    res = {"n_edificacoes": len(b), "por_fonte": b["fonte"].value_counts().to_dict(), "por_zona": b["zona"].value_counts().to_dict(),
           "area_km2": round(b["area_m2"].sum() / 1e6, 3), "rural_com_alerta": int(b.loc[rur, "alerta"].notna().sum()),
           "rural_n": int(rur.sum()), "rural_pct_com_alerta": round(100 * b.loc[rur, "alerta"].notna().mean(), 1),
           "alertas_rural": alr.to_dict(), "fora_dos_bairros": int((b["bairro"] == "fora").sum()), "osm_x_overture": comp}
    meta = {"arquivo": SAIDA.name, "data_processamento": datetime.now(timezone.utc).isoformat(),
            "script": "scripts/download/edificacoes_overture.py", "codigo_ibge": codigo,
            "fonte": "Overture Maps Foundation — tema buildings (tipo building)", "versao": VERSAO,
            "url": f"s3://overturemaps-us-west-2/release/{VERSAO}/theme=buildings/type=building/",
            "arquivo_origem": str(PARQUET.relative_to(RAIZ)),
            "licenca": ("ODbL 1.0 (tema buildings do Overture; registros OSM e Microsoft ML declaram ODbL-1.0; os do Google Open "
                        "Buildings vêm sem licença no campo sources — o Google os publica sob CC BY 4.0 e ODbL)"),
            "atribuicao": ATRIBUICAO, "crs": CRS_PADRAO,
            "transformacao_aplicada": ("edificações que tocam o município (polígono inteiro, sem corte); reprojeção EPSG:4326 → "
                                       "EPSG:31981; fonte = dataset do primeiro registro em sources; area_m2 planimétrica em UTM; "
                                       "zona e bairro pelo ponto interno (áreas urbanizadas IBGE 2019; bairros IBGE 2022, 'fora' se "
                                       "nenhum); alerta só no rural: área < 15 m² ou > 5.000 m², ponto interno em água (33, 31) ou "
                                       "mata (3, 4, 5, 6, 9, 49) no MapBiomas 2024 (30 m), ≥ 50% sobre água/mata do OSM; nada apagado"),
            "campos_excluidos": "names e todos os demais atributos do Overture (sem nome nem contato)",
            "uso": "acervo (derivado); não está no portal", "resumo": res}
    SAIDA.with_suffix(".json").write_text(json.dumps(meta, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    (saida_tab / "E0_resumo_edificacoes.json").write_text(json.dumps(res, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return res


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--codigo-ibge", default="4322400")
    ap.add_argument("--saida-tabelas", type=Path, required=True, help="pasta das estatísticas (fora do git)")
    ap.add_argument("--python-overture", default=os.environ.get("OVERTURE_PYTHON"), help="python com duckdb (só se precisar baixar)")
    ap.add_argument("--forcar", action="store_true", help="refaz o produto a partir do parquet já baixado")
    ap.add_argument("--rebaixar", action="store_true", help="baixa o parquet de novo (exige --python-overture)")
    a = ap.parse_args()
    cod = gpd.read_file(CAMINHO_AREA_ESTUDO_PADRAO).get("codarea")
    if cod is not None and str(cod.iloc[0]) != str(a.codigo_ibge):
        raise SystemExit(f"área de estudo é do código {cod.iloc[0]}, não {a.codigo_ibge}: gere-a com vetor_ibge.py")
    try:
        if not PARQUET.exists() or a.rebaixar:
            baixar(a.python_overture)
        if SAIDA.exists() and not a.forcar:
            logger.info("%s já existe — use --forcar para refazer", SAIDA.name)
            return
        res = processar(a.saida_tabelas, a.codigo_ibge)
    except (subprocess.CalledProcessError, OSError) as e:
        logger.error("Falha: %s", e)
        raise SystemExit(1)
    print(json.dumps(res, ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    main()
