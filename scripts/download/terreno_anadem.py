"""
Baixa o(s) tile(s) ANADEM (modelo digital de terreno, 30 m) que cobrem a
área de estudo estendida, mosaica se necessário, recorta e reprojeta:

    data/raw/raster/mdt_anadem_atual_30m.tif

Fonte e versão
--------------
ANADEM v1.0 (atualizada em 2024-02-20) — modelo digital de terreno para a
América do Sul, desenvolvido pelo HGE/IPH-UFRGS em parceria com a ANA,
com remoção de viés de vegetação sobre o Copernicus GLO-30 (erro médio
reduzido de 9,6 m para 1,5 m). Licença MIT (repositório do algoritmo:
https://github.com/HGE-IPH/anadem). Citação: Laipelt, Andrade, Ruhoff,
Amorim, Collischonn e Paiva — ver https://www.mdpi.com/2072-4292/16/13/2321.
Página do projeto: https://www.ufrgs.br/hge/anadem-modelo-digital-de-terreno-mdt/

Os tiles são distribuídos em grade MGRS (Military Grid Reference System),
cada um cobrindo uma zona UTM inteira em GeoTIFF Cloud-Optimized (COG),
EPSG:4326, ~1-2 GB por tile. Este script:

1. Baixa a grade de tiles MGRS (shapefile oficial do projeto ANADEM,
   https://www.ufrgs.br/hge/wp-content/uploads/2024/04/anadem_mgrs.zip)
   para descobrir programaticamente QUAIS tiles cobrem a área de estudo
   estendida (config/area_estudo_bacias.geojson) — nada hardcoded: para
   outro município a lista de tiles pode ser outra, inclusive mais de um
   (nesse caso, mosaica antes do recorte final).
2. Lê cada tile remotamente via GDAL /vsicurl/ (streaming, mesmo princípio
   de scripts/download/uso-solo_mapbiomas.py) e usa
   scripts/utils/recorte_municipio.recortar_raster para recortar pela
   área de estudo estendida e reprojetar para o CRS padrão do projeto —
   sem nunca baixar o tile nacional inteiro (~1-2 GB) por completo.
3. Se mais de um tile intersectar a área, mosaica os recortes já
   reprojetados (rasterio.merge) antes de salvar a saída final. Como cada
   tile é reprojetado individualmente antes do mosaico, pode haver
   pequena descontinuidade de sub-pixel nas bordas de junção — aceitável
   para os produtos derivados deste projeto (declividade/hillshade em
   escala municipal), mas documentado no metadado.

Idempotente: se a saída já existir, não reprocessa (a menos que --forcar
seja usado).

Uso:
    python scripts/download/terreno_anadem.py
    python scripts/download/terreno_anadem.py --forcar
"""

import argparse
import json
import logging
import shutil
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlretrieve

import geopandas as gpd
import rasterio
from rasterio.merge import merge

sys.path.append(str(Path(__file__).resolve().parents[1] / "utils"))
from recorte_municipio import CRS_PADRAO, recortar_raster  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

RAIZ = Path(__file__).resolve().parents[2]
CAMINHO_AREA_BACIAS_DEFAULT = RAIZ / "config" / "area_estudo_bacias.geojson"
CAMINHO_SAIDA_DEFAULT = RAIZ / "data" / "raw" / "raster" / "mdt_anadem_atual_30m.tif"

URL_GRADE_MGRS = "https://www.ufrgs.br/hge/wp-content/uploads/2024/04/anadem_mgrs.zip"
BASE_URL_TILES = "https://metadados.snirh.gov.br/files/anadem_v1_tiles/anadem_v1_{tile}.tif"
VERSAO_ANADEM = "v1.0 (2024-02-20)"
URL_PAGINA_PROJETO = "https://www.ufrgs.br/hge/anadem-modelo-digital-de-terreno-mdt/"
URL_REPOSITORIO = "https://github.com/HGE-IPH/anadem"
LICENCA = "MIT (código); dado aberto — ver página do projeto para citação"
CITACAO = (
    "Laipelt, L.; Andrade, B. C.; Ruhoff, A.; Amorim, A.; Collischonn, W.; Paiva, R. C. D. — "
    "ANADEM: A Digital Terrain Model for South America. Remote Sensing 2024, 16, 2321. "
    "https://www.mdpi.com/2072-4292/16/13/2321"
)


def identificar_tiles(area_estudo_bacias: gpd.GeoDataFrame, tmpdir: Path) -> list[str]:
    """Baixa a grade oficial de tiles MGRS do ANADEM e retorna os códigos que intersectam a área de busca."""
    caminho_zip = tmpdir / "anadem_mgrs.zip"
    logger.info("Baixando grade de tiles MGRS do ANADEM — %s", URL_GRADE_MGRS)
    urlretrieve(URL_GRADE_MGRS, caminho_zip)
    with zipfile.ZipFile(caminho_zip) as zf:
        zf.extractall(tmpdir / "grade_mgrs")

    caminhos_shp = list((tmpdir / "grade_mgrs").rglob("*.shp"))
    if not caminhos_shp:
        raise RuntimeError("Grade MGRS do ANADEM baixada, mas nenhum .shp encontrado no zip.")
    grade = gpd.read_file(caminhos_shp[0])

    area_no_crs_grade = area_estudo_bacias.to_crs(grade.crs)
    tiles_intersectantes = gpd.sjoin(grade, area_no_crs_grade, predicate="intersects", how="inner")

    tiles = sorted(tiles_intersectantes["mgrs"].unique().tolist())
    if not tiles:
        raise RuntimeError("Nenhum tile MGRS do ANADEM intersecta a área de estudo estendida — verifique a grade/área.")
    logger.info("Tile(s) ANADEM necessário(s) para esta área: %s", tiles)
    return tiles


def baixar_recortar_tile(tile: str, area_estudo_bacias: gpd.GeoDataFrame, caminho_saida_tile: Path) -> Path:
    url_tile = BASE_URL_TILES.format(tile=tile)
    url_vsicurl = "/vsicurl/" + url_tile
    logger.info("Recortando tile %s remotamente (streaming /vsicurl/) — %s", tile, url_tile)
    recortar_raster(url_vsicurl, caminho_saida_tile, area_estudo_bacias)
    return caminho_saida_tile


def mosaicar(caminhos_tiles: list[Path], caminho_saida: Path) -> None:
    fontes = [rasterio.open(p) for p in caminhos_tiles]
    try:
        mosaico, transform_mosaico = merge(fontes)
        perfil = fontes[0].profile.copy()
        perfil.update(height=mosaico.shape[1], width=mosaico.shape[2], transform=transform_mosaico)
        with rasterio.open(caminho_saida, "w", **perfil) as dst:
            dst.write(mosaico)
    finally:
        for f in fontes:
            f.close()


def salvar_metadados(caminho_saida: Path, tiles: list[str], area_estudo_bacias: gpd.GeoDataFrame) -> None:
    with rasterio.open(caminho_saida) as src:
        info_raster = {
            "crs": str(src.crs),
            "resolucao_m": list(src.res),
            "bandas": src.count,
            "dtype": src.dtypes[0],
            "largura_px": src.width,
            "altura_px": src.height,
            "bounds": list(src.bounds),
        }

    metadados = {
        "fonte": "ANADEM — Modelo Digital de Terreno para a América do Sul (HGE/IPH-UFRGS + ANA)",
        "url_pagina_projeto": URL_PAGINA_PROJETO,
        "url_repositorio_codigo": URL_REPOSITORIO,
        "versao": VERSAO_ANADEM,
        "licenca": LICENCA,
        "citacao": CITACAO,
        "data_acesso": datetime.now(timezone.utc).date().isoformat(),
        "tiles_mgrs_usados": tiles,
        "url_tiles": [BASE_URL_TILES.format(tile=t) for t in tiles],
        "metodo_mosaico_recorte": (
            "tile único cobre a área de busca — sem mosaico necessário" if len(tiles) == 1 else
            f"{len(tiles)} tiles reprojetados individualmente via streaming /vsicurl/ e mosaicados "
            "(rasterio.merge) após reprojeção — possível pequena descontinuidade de sub-pixel na "
            "junção entre tiles"
        ),
        "area_busca": "config/area_estudo_bacias.geojson (buffer sobre a área de estudo municipal)",
        "resolucao_original_graus": 0.000269494585236,  # ~30 m no equador; confirmado via gdalinfo do tile
        "resolucao_original_aprox_m": 30,
        "crs_original": "EPSG:4326",
        "crs_processado": CRS_PADRAO,
        "resolucao_processada_m": info_raster["resolucao_m"],
        "raster_final": info_raster,
        "data_processamento": datetime.now(timezone.utc).isoformat(),
        "transformacao_aplicada": (
            f"leitura em streaming via GDAL /vsicurl/ dos tiles {tiles} (COG, ~1-2 GB cada, "
            "nunca baixados por inteiro), recorte pela área de estudo estendida e reprojeção para "
            f"{CRS_PADRAO}"
        ),
    }
    caminho_saida.with_suffix(".json").write_text(
        json.dumps(metadados, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    logger.info("Metadados salvos em %s", caminho_saida.with_suffix(".json"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Baixa, mosaica e recorta o MDT ANADEM para a área de estudo estendida.")
    parser.add_argument("--area-bacias", type=Path, default=CAMINHO_AREA_BACIAS_DEFAULT,
                         help="Área de busca/recorte (buffer) — default: config/area_estudo_bacias.geojson")
    parser.add_argument("--saida", type=Path, default=CAMINHO_SAIDA_DEFAULT, help="Caminho de saída do GeoTIFF")
    parser.add_argument("--forcar", action="store_true", help="Reprocessa mesmo se a saída já existir")
    args = parser.parse_args()

    if args.saida.exists() and not args.forcar:
        logger.info("MDT ANADEM já existe em %s — nada a fazer (use --forcar para refazer).", args.saida)
        return

    if not args.area_bacias.exists():
        raise FileNotFoundError(
            f"{args.area_bacias} não encontrado. Rode primeiro: "
            "python scripts/processamento/area_estudo_bacias.py"
        )

    area_estudo_bacias = gpd.read_file(args.area_bacias)
    args.saida.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="anadem_") as tmpdir_str:
        tmpdir = Path(tmpdir_str)
        tiles = identificar_tiles(area_estudo_bacias, tmpdir)

        caminhos_recortados = [
            baixar_recortar_tile(tile, area_estudo_bacias, tmpdir / f"anadem_{tile}_recortado.tif")
            for tile in tiles
        ]

        if len(caminhos_recortados) == 1:
            shutil.copy(caminhos_recortados[0], args.saida)
        else:
            logger.info("Mosaicando %d tiles recortados...", len(caminhos_recortados))
            mosaicar(caminhos_recortados, args.saida)

    salvar_metadados(args.saida, tiles, area_estudo_bacias)
    logger.info("MDT ANADEM salvo em %s", args.saida)


if __name__ == "__main__":
    main()
