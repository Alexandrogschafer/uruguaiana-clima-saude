"""
Utilitários compartilhados dos scripts de preparação do geoportal.

O Leaflet exige geometrias em WGS84 (EPSG:4326) — diferente do CRS padrão
de processamento do projeto (EPSG:31981). Estes scripts sempre trabalham
com o polígono/geometria de origem no CRS em que o dado já está, aplicam
qualquer transformação métrica necessária (simplificação, buffer) nesse
CRS, e só reprojetam para 4326 no momento da exportação final.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd

RAIZ_PROJETO = Path(__file__).resolve().parents[2]
DIR_GEOPORTAL = RAIZ_PROJETO / "data" / "geoportal"
CRS_LEAFLET = "EPSG:4326"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("geoportal")


def salvar_geojson_wgs84(
    gdf: gpd.GeoDataFrame,
    caminho_saida: Path,
    descricao: str,
    fonte: dict,
    transformacao: str,
    forcar: bool = False,
) -> Path:
    """Reprojeta para EPSG:4326 e exporta GeoJSON + .json de metadados irmão.

    Idempotente: se o arquivo já existe e `forcar` é False, só loga e sai.
    """
    caminho_saida.parent.mkdir(parents=True, exist_ok=True)
    if caminho_saida.exists() and not forcar:
        logger.info("já existe, pulando: %s", caminho_saida.relative_to(RAIZ_PROJETO))
        return caminho_saida

    if gdf.crs is None:
        raise ValueError(f"GeoDataFrame sem CRS definido antes de exportar para {caminho_saida}")
    gdf_wgs84 = gdf.to_crs(CRS_LEAFLET) if gdf.crs.to_string() != CRS_LEAFLET else gdf

    gdf_wgs84.to_file(caminho_saida, driver="GeoJSON")

    metadados = {
        "descricao": descricao,
        "fonte": fonte,
        "crs_saida": CRS_LEAFLET,
        "transformacao_aplicada": transformacao,
        "n_features": int(len(gdf_wgs84)),
        "colunas": [c for c in gdf_wgs84.columns if c != "geometry"],
        "data_processamento": datetime.now(timezone.utc).isoformat(),
    }
    caminho_saida.with_suffix(".json").write_text(
        json.dumps(metadados, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    tamanho_kb = caminho_saida.stat().st_size / 1024
    logger.info(
        "gerado: %s (%d features, %.1f KB)",
        caminho_saida.relative_to(RAIZ_PROJETO),
        len(gdf_wgs84),
        tamanho_kb,
    )
    return caminho_saida
