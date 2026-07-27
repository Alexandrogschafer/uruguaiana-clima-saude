"""
Baixa as cotas de inundação do rio Uruguai em Uruguaiana via REST API do
Serviço Geológico do Brasil (SGB, ex-CPRM) — ArcGIS MapServer — e gera um
único vetor consolidado das 4 cotas de referência.

    data/raw/vetor/cotas-inundacao_sgb_atual_vetorial.gpkg
    data/raw/vetor/cotas-inundacao_sgb_atual_vetorial.geojson

Idempotente: se os arquivos de saída já existirem, não baixa de novo (a
menos que --forcar seja usado). Loga fonte, data e tamanho do download.

Camadas fixas do MapServer BACIA_DO_URUGUAI_URUGUAIANA (cota em cm e tempo
de retorno em anos são atributos do serviço, não parametrizáveis por
código IBGE — específicos desta estação/seção do rio Uruguai):

    layer_id  cota_cm  tr_anos
    3         833      1.3
    4         952      1.9
    5         1205     9.2
    6         1252     13.4

Uso:
    python scripts/download/hidrologia_sgb.py
    python scripts/download/hidrologia_sgb.py --forcar
"""

import argparse
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import pandas as pd
import shapely

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

CRS_PADRAO = "EPSG:31981"
CRS_ORIGEM = "EPSG:4674"  # SIRGAS 2000 geográfico — solicitado via outSR na consulta

BASE_URL = "https://geoportal.sgb.gov.br/server/rest/services/hidrologia/BACIA_DO_URUGUAI_URUGUAIANA/MapServer"

CAMADAS = [
    {"layer_id": 3, "cota_cm": 833, "tr_anos": 1.3},
    {"layer_id": 4, "cota_cm": 952, "tr_anos": 1.9},
    {"layer_id": 5, "cota_cm": 1205, "tr_anos": 9.2},
    {"layer_id": 6, "cota_cm": 1252, "tr_anos": 13.4},
]

CAMINHO_SAIDA_DEFAULT = (
    Path(__file__).resolve().parents[2] / "data" / "raw" / "vetor" / "cotas-inundacao_sgb_atual_vetorial"
)


def montar_url_camada(layer_id: int) -> str:
    return f"{BASE_URL}/{layer_id}/query?where=1%3D1&outFields=*&f=geojson&outSR=4674"


def baixar_camada(layer_id: int, cota_cm: int, tr_anos: float) -> gpd.GeoDataFrame:
    """Baixa uma camada de cota via query REST do MapServer (geopandas lê a URL direto)."""
    url = montar_url_camada(layer_id)
    logger.info("Baixando camada %d (cota %d cm, TR %.1f anos) — %s", layer_id, cota_cm, tr_anos, url)

    try:
        gdf = gpd.read_file(url)
    except Exception as erro:  # geopandas/pyogrio pode levantar vários tipos de erro de I/O
        raise RuntimeError(f"Falha ao baixar a camada {layer_id} do SGB: {erro}") from erro

    if gdf.empty:
        raise RuntimeError(f"Camada {layer_id} do SGB retornou sem feições — verifique o serviço.")

    # O serviço deveria honrar outSR=4674; garante o CRS caso o retorno venha sem definição.
    if gdf.crs is None:
        gdf = gdf.set_crs(CRS_ORIGEM)

    # O serviço já retorna COTA_cm/TR/BACIA/MUNICIPIO/ESTADO — padroniza nomes para
    # snake_case (convenção do projeto) em vez de duplicar os atributos.
    gdf = gdf.rename(
        columns={"COTA_cm": "cota_cm", "TR": "tr_anos", "BACIA": "bacia", "MUNICIPIO": "municipio", "ESTADO": "estado"}
    ).drop(columns=["FID"], errors="ignore")
    gdf["layer_id"] = layer_id

    cota_servico = gdf["cota_cm"].iloc[0] if "cota_cm" in gdf.columns else None
    if cota_servico is not None and cota_servico != cota_cm:
        logger.warning(
            "Cota informada (%d cm) difere da retornada pelo serviço na camada %d (%s cm)",
            cota_cm, layer_id, cota_servico,
        )

    logger.info("Camada %d: %d feição(ões) baixada(s)", layer_id, len(gdf))
    return gdf


def consolidar_camadas(camadas: list[dict]) -> gpd.GeoDataFrame:
    """Baixa todas as camadas e concatena em um único GeoDataFrame no CRS padrão do projeto."""
    gdfs = [baixar_camada(c["layer_id"], c["cota_cm"], c["tr_anos"]) for c in camadas]

    gdf = gpd.GeoDataFrame(pd.concat(gdfs, ignore_index=True), geometry="geometry", crs=gdfs[0].crs)

    # Remove eventual componente Z (algumas geometrias de serviços ArcGIS vêm 3D) —
    # geometria 2D evita problemas de compatibilidade em operações espaciais futuras.
    gdf["geometry"] = shapely.force_2d(gdf.geometry.values)

    gdf = gdf.to_crs(CRS_PADRAO)
    return gdf


def calcular_areas_por_cota(gdf: gpd.GeoDataFrame) -> dict:
    """Área (km²) por cota, calculada no CRS métrico padrão do projeto."""
    areas_km2 = gdf.geometry.area.groupby(gdf["cota_cm"]).sum() / 1e6
    return {str(cota_cm): round(area, 4) for cota_cm, area in areas_km2.items()}


def salvar_saida(gdf: gpd.GeoDataFrame, caminho_base: Path, camadas: list[dict]) -> None:
    caminho_base.parent.mkdir(parents=True, exist_ok=True)
    caminho_gpkg = caminho_base.with_suffix(".gpkg")
    caminho_geojson = caminho_base.with_suffix(".geojson")
    caminho_metadados = caminho_base.with_suffix(".json")

    gdf.to_file(caminho_gpkg, driver="GPKG", layer="cotas_inundacao")
    logger.info("Vetor salvo em %s (CRS: %s)", caminho_gpkg, CRS_PADRAO)

    gdf.to_file(caminho_geojson, driver="GeoJSON")
    logger.info("Vetor salvo em %s (CRS: %s)", caminho_geojson, CRS_PADRAO)

    tamanho_kb = caminho_gpkg.stat().st_size / 1024
    areas_km2 = calcular_areas_por_cota(gdf)

    metadados = {
        "fonte": "SGB (Serviço Geológico do Brasil) — hidrologia, cotas de inundação",
        "url_base": BASE_URL,
        "camadas": [
            {
                "layer_id": c["layer_id"],
                "cota_cm": c["cota_cm"],
                "tr_anos": c["tr_anos"],
                "url_consulta": montar_url_camada(c["layer_id"]),
                "area_km2": areas_km2.get(str(c["cota_cm"])),
            }
            for c in camadas
        ],
        "n_feicoes_total": len(gdf),
        "tamanho_gpkg_kb": round(tamanho_kb, 1),
        "crs_original": CRS_ORIGEM,
        "crs_processado": CRS_PADRAO,
        "data_processamento": datetime.now(timezone.utc).isoformat(),
        "transformacao_aplicada": (
            "concatenação das 4 camadas de cota, geometria forçada para 2D, "
            f"reprojeção para {CRS_PADRAO} (SIRGAS 2000 / UTM 21S)"
        ),
    }
    caminho_metadados.write_text(json.dumps(metadados, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("Metadados salvos em %s", caminho_metadados)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Baixa as cotas de inundação do rio Uruguai em Uruguaiana (SGB) e gera um vetor consolidado."
    )
    parser.add_argument("--saida", type=Path, default=CAMINHO_SAIDA_DEFAULT, help="Caminho base de saída (sem extensão)")
    parser.add_argument("--forcar", action="store_true", help="Baixa novamente mesmo se os arquivos já existirem")
    args = parser.parse_args()

    caminho_gpkg = args.saida.with_suffix(".gpkg")
    if caminho_gpkg.exists() and not args.forcar:
        logger.info("Cotas de inundação já existem em %s — nada a fazer (use --forcar para baixar de novo).", caminho_gpkg)
        return

    gdf = consolidar_camadas(CAMADAS)
    salvar_saida(gdf, args.saida, CAMADAS)


if __name__ == "__main__":
    main()
