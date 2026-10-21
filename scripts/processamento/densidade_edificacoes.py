"""
Grade de densidade de edificações (rodada 70): conta as edificações do
Overture Maps (release 2026-09-23.1, camada derivada da rodada 68) por célula
de 250 m do município, sem expor nenhuma edificação.

Regras:
    exclusão    edificações com alerta "area<15m2" (o alerta só existe no rural:
                provável ruído de detecção) não entram na contagem
    célula      grade de 250 m alinhada a múltiplos de 250 m em EPSG:31981;
                entram as células que tocam o município, inteiras (sem recorte
                na borda: a área da célula é sempre 6,25 ha)
    contagem    pelo centroide da edificação, calculado só em memória; conta só
                o centroide dentro do município
    atributos   n_edificacoes, area_construida_m2 (soma das áreas planimétricas),
                densidade_ed_ha (n / 6,25 ha), predomina_zona (zona com mais
                edificações na célula; empate → urbano)
    supressão   limiar pela zona predominante da célula: urbano exige ≥ 5
                edificações e rural ≥ 10 (no rural, poucas edificações numa
                célula podem ser uma única sede de estabelecimento); as demais
                ficam fora da saída

Nenhuma coordenada de edificação vai para a saída: só o quadrado da célula e
os agregados.

Saídas:
    data/processed/edificacoes/densidade_edificacoes_250m_overture-2026-09-23.1_2026_vetorial.gpkg (+ .json)
    data/geoportal/densidade-edificacoes-250m.geojson (EPSG:4326, 6 casas, só o que o popup usa) (+ .json)

Uso:
    python scripts/processamento/densidade_edificacoes.py --codigo-ibge 4322400 [--forcar]
        [--limiar-urbano 5] [--limiar-rural 10]
"""

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely

RAIZ = Path(__file__).resolve().parents[2]
sys.path.append(str(RAIZ / "scripts" / "utils"))
from recorte_municipio import CRS_PADRAO, carregar_area_estudo  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

RELEASE = "2026-09-23.1"
PASTA = RAIZ / "data" / "processed" / "edificacoes"
ENTRADA = PASTA / f"edificacoes_overture-{RELEASE}_2026_vetorial.gpkg"
SAIDA = PASTA / f"densidade_edificacoes_250m_overture-{RELEASE}_2026_vetorial.gpkg"
PORTAL = RAIZ / "data" / "geoportal" / "densidade-edificacoes-250m.geojson"
LADO_M = 250
AREA_CELULA_HA = LADO_M * LADO_M / 10_000
LIMIAR_URBANO = 5
LIMIAR_RURAL = 10
ALERTA_EXCLUIDO = "area<15m2"
CAMPOS_PORTAL = ["n_edificacoes", "area_construida_m2", "densidade_ed_ha"]
ALVO_PORTAL_MB = 1.5

LICENCA = {
    "produto": "ODbL 1.0 (base de dados derivada de dados sob ODbL: compartilhar pela mesma licença, com atribuição)",
    "por_fonte": {
        "OpenStreetMap": "ODbL-1.0 (declarada no campo sources do release)",
        "Microsoft ML Buildings": "ODbL-1.0 (declarada no campo sources do release)",
        "Google Open Buildings": "sem licença no campo sources do release; o Google publica o conjunto sob CC BY 4.0 e ODbL 1.0",
    },
    "redistribuicao_derivado_agregado": "permitida pelas três, com atribuição",
}
ATRIBUICAO = "Edificações: Overture Maps Foundation (inclui © contribuidores do OpenStreetMap, Microsoft e Google)"


def texto_supressao(limiares: dict) -> str:
    return (f"pela zona predominante da célula: urbano com menos de {limiares['urbano']} e rural com menos de "
            f"{limiares['rural']} edificações não entram na saída")


def grade(area: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Células de 250 m alinhadas à origem do UTM que tocam o município (inteiras)."""
    minx, miny, maxx, maxy = area.total_bounds
    xs = np.arange(np.floor(minx / LADO_M) * LADO_M, maxx, LADO_M)
    ys = np.arange(np.floor(miny / LADO_M) * LADO_M, maxy, LADO_M)
    xx, yy = np.meshgrid(xs, ys)
    xx, yy = xx.ravel(), yy.ravel()
    caixas = shapely.box(xx, yy, xx + LADO_M, yy + LADO_M)
    poligono = area.union_all()
    shapely.prepare(poligono)
    toca = shapely.intersects(poligono, caixas)
    ids = [f"E{int(x)}N{int(y)}" for x, y in zip(xx[toca], yy[toca])]
    return gpd.GeoDataFrame({"id_celula": ids}, geometry=caixas[toca], crs=CRS_PADRAO)


def montar(codigo_ibge: str, limiares: dict) -> tuple[gpd.GeoDataFrame, dict]:
    area = carregar_area_estudo()
    ed = gpd.read_file(ENTRADA, columns=["zona", "area_m2", "alerta"])
    if ed.crs is None or ed.crs.to_string() != CRS_PADRAO:
        ed = ed.to_crs(CRS_PADRAO)  # a contagem por célula exige o mesmo CRS da grade
    n_total = len(ed)
    excluir = ed["alerta"].fillna("").str.split(";").apply(lambda a: ALERTA_EXCLUIDO in a)
    ed = ed[~excluir]

    # centroide só em memória; a geometria da edificação não sai daqui
    pts = gpd.GeoDataFrame(ed[["zona", "area_m2"]], geometry=ed.geometry.centroid, crs=CRS_PADRAO)
    dentro = pts.within(area.union_all())
    pts = pts[dentro]

    cel = grade(area)
    junta = gpd.sjoin(pts, cel, how="inner", predicate="within")
    g = junta.groupby("id_celula")
    agg = pd.DataFrame({
        "n_edificacoes": g.size(),
        "area_construida_m2": g["area_m2"].sum().round(0).astype(int),
        "n_urbano": g["zona"].apply(lambda z: int((z == "urbano").sum())),
    })
    agg["predomina_zona"] = np.where(agg["n_urbano"] * 2 >= agg["n_edificacoes"], "urbano", "rural")
    agg["densidade_ed_ha"] = (agg["n_edificacoes"] / AREA_CELULA_HA).round(2)

    fica = agg["n_edificacoes"] >= agg["predomina_zona"].map(limiares)
    suprimidas = agg[~fica]
    agg = agg[fica]
    saida = cel.merge(agg.drop(columns="n_urbano"), left_on="id_celula", right_index=True, how="inner")
    saida = saida[["id_celula", "n_edificacoes", "area_construida_m2", "densidade_ed_ha", "predomina_zona", "geometry"]]
    saida = saida.sort_values("id_celula").reset_index(drop=True)

    dens = saida["densidade_ed_ha"]
    res = {
        "edificacoes_no_arquivo": n_total,
        "excluidas_area_menor_15m2": int(excluir.sum()),
        "centroide_fora_do_municipio": int((~dentro).sum()),
        "edificacoes_contadas": int(len(junta)),
        "celulas_que_tocam_o_municipio": int(len(cel)),
        "celulas_com_edificacao": int(len(agg) + len(suprimidas)),
        "celulas_na_saida": int(len(saida)),
        "limiares": limiares,
        "celulas_suprimidas": int(len(suprimidas)),
        "edificacoes_nas_celulas_suprimidas": int(suprimidas["n_edificacoes"].sum()),
        "suprimidas_por_zona": {z: {"celulas": int(len(d)), "edificacoes": int(d["n_edificacoes"].sum())}
                                for z, d in suprimidas.groupby("predomina_zona")},
        "edificacoes_na_saida": int(saida["n_edificacoes"].sum()),
        "celulas_por_zona": saida["predomina_zona"].value_counts().to_dict(),
        "edificacoes_por_zona": saida.groupby("predomina_zona")["n_edificacoes"].sum().astype(int).to_dict(),
        "densidade_ed_ha": {"min": float(dens.min()), "mediana": float(dens.median()), "max": float(dens.max()),
                            "quintis": [float(q) for q in dens.quantile([0.2, 0.4, 0.6, 0.8]).round(2)]},
    }
    return saida, res


def gravar_portal(saida: gpd.GeoDataFrame, res: dict, codigo_ibge: str) -> float:
    """GeoJSON do portal: EPSG:4326, 6 casas, só os campos do popup (a grade não é simplificada)."""
    portal = saida[CAMPOS_PORTAL + ["geometry"]].to_crs("EPSG:4326")
    PORTAL.parent.mkdir(parents=True, exist_ok=True)
    PORTAL.unlink(missing_ok=True)
    portal.to_file(PORTAL, driver="GeoJSON", COORDINATE_PRECISION=6, RFC7946="YES")
    mb = PORTAL.stat().st_size / 1e6
    if mb > ALVO_PORTAL_MB:
        logger.warning("GeoJSON do portal com %.2f MB (alvo ≤ %.1f MB)", mb, ALVO_PORTAL_MB)
    meta = {
        "descricao": "Densidade de edificações por célula de 250 m (contagem, área construída e edificações por hectare); "
                     f"células com menos de {res['limiares']['urbano']} (urbano) ou {res['limiares']['rural']} (rural) edificações suprimidas. "
                     "Produto provisório, ainda não conferido.",
        "fonte": {"caminho_origem": str(SAIDA.relative_to(RAIZ)), "edificacoes": f"Overture Maps Foundation — buildings, release {RELEASE}"},
        "crs_saida": "EPSG:4326",
        "transformacao_aplicada": "reprojeção EPSG:31981 -> EPSG:4326; coordenadas com 6 casas; sem simplificação da grade; "
                                  "só n_edificacoes, area_construida_m2 e densidade_ed_ha",
        "n_features": int(len(portal)), "colunas": CAMPOS_PORTAL, "codigo_ibge": codigo_ibge,
        "data_processamento": datetime.now(timezone.utc).isoformat(),
        "licenca": LICENCA["produto"], "atribuicao": ATRIBUICAO, "status": "extraido",
        "supressao": texto_supressao(res["limiares"]), "limiares": res["limiares"],
        "quintis_densidade_ed_ha": res["densidade_ed_ha"]["quintis"], "tamanho_mb": round(mb, 3),
    }
    PORTAL.with_suffix(".json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return mb


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--codigo-ibge", default="4322400")
    ap.add_argument("--forcar", action="store_true", help="refaz a grade mesmo que já exista")
    ap.add_argument("--limiar-urbano", type=int, default=LIMIAR_URBANO, help="mínimo de edificações na célula urbana")
    ap.add_argument("--limiar-rural", type=int, default=LIMIAR_RURAL, help="mínimo de edificações na célula rural")
    a = ap.parse_args()
    cod_area = json.loads((RAIZ / "config/area_estudo.geojson").read_text(encoding="utf-8"))["features"][0]["properties"].get("codarea")
    if cod_area and str(cod_area) != a.codigo_ibge:
        raise SystemExit(f"--codigo-ibge {a.codigo_ibge} difere da área de estudo ({cod_area})")
    if SAIDA.exists() and PORTAL.exists() and not a.forcar:
        logger.info("%s já existe — use --forcar", SAIDA.name)
        print(json.dumps(json.loads(SAIDA.with_suffix(".json").read_text(encoding="utf-8"))["resumo"], ensure_ascii=False, indent=1))
        return
    if not ENTRADA.exists():
        raise SystemExit(f"entrada ausente: {ENTRADA.relative_to(RAIZ)} (rode scripts/download/edificacoes_overture.py)")
    try:
        limiares = {"urbano": a.limiar_urbano, "rural": a.limiar_rural}
        saida, res = montar(a.codigo_ibge, limiares)
        SAIDA.unlink(missing_ok=True)
        saida.to_file(SAIDA, layer="densidade_edificacoes_250m", driver="GPKG")
        res["geojson_portal_mb"] = round(gravar_portal(saida, res, a.codigo_ibge), 3)
    except (OSError, ValueError) as e:
        logger.error("Falha: %s", e)
        raise SystemExit(1)
    meta = {"arquivo": SAIDA.name, "camada": "densidade_edificacoes_250m", "data_processamento": datetime.now(timezone.utc).isoformat(),
            "script": "scripts/processamento/densidade_edificacoes.py", "codigo_ibge": a.codigo_ibge, "crs": CRS_PADRAO,
            "fonte": f"derivado: Overture Maps Foundation — buildings, release {RELEASE} ({ENTRADA.name})",
            "release": RELEASE, "licenca": LICENCA, "atribuicao": ATRIBUICAO, "status": "extraido",
            "transformacao_aplicada": f"exclui alerta {ALERTA_EXCLUIDO}; centroide (só em memória) dentro do município; contagem por "
                                      f"célula de {LADO_M} m alinhada em EPSG:31981 (células inteiras que tocam o município); "
                                      "soma de area_m2; densidade = n / 6,25 ha; predomina_zona = zona com mais edificações (empate → urbano)",
            "supressao": texto_supressao(limiares), "limiares": limiares,
            "privacidade": "nenhum ponto, centroide ou polígono de edificação na saída; só o quadrado da célula e agregados",
            "uso": "portal (camada provisória, desligada por padrão) e acervo; aguarda conferência no mapa pelo responsável",
            "tamanho_mb": round(SAIDA.stat().st_size / 1e6, 2), "resumo": res}
    SAIDA.with_suffix(".json").write_text(json.dumps(meta, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(res, ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    main()
