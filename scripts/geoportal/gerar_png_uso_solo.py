"""
Gera PNGs coloridos (paleta oficial MapBiomas) para cada ano da série de
uso do solo, para uso com L.imageOverlay no geoportal.

Decisão de CRS: L.imageOverlay exige que a imagem seja um retângulo
alinhado a paralelos/meridianos em WGS84 — não basta reprojetar só os
cantos do bounding box do raster original (EPSG:31981/UTM), pois isso
introduziria distorção geométrica entre o conteúdo da imagem e o
bounding box declarado. Por isso o raster inteiro é reprojetado
(warp, resampling nearest para preservar os códigos de classe categóricos)
para EPSG:4326 antes de colorir e exportar; o bounding box salvo em
bounds.json é o do raster já reprojetado, portanto geometricamente
consistente com a imagem.

Código de classe 0 ("Não observado") é tratado como nodata (alpha=0) —
na área de estudo corresponde aos pixels fora do recorte do município.
"""

import json
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image
from rasterio.warp import Resampling, calculate_default_transform, reproject

from common import DIR_GEOPORTAL, RAIZ_PROJETO, logger
from paleta_mapbiomas import CODIGO_NODATA, PALETA_HEX, hex_para_rgb

DIR_RASTER_ORIGEM = RAIZ_PROJETO / "data" / "raw" / "raster"
DIR_SAIDA = DIR_GEOPORTAL / "uso-solo"
CRS_LEAFLET = "EPSG:4326"
ANOS = [1985, 1990, 1995, 2000, 2005, 2010, 2015, 2020, 2024]


def reprojetar_para_wgs84(caminho_raster: Path) -> tuple[np.ndarray, rasterio.coords.BoundingBox]:
    with rasterio.open(caminho_raster) as src:
        transform_dst, largura_dst, altura_dst = calculate_default_transform(
            src.crs, CRS_LEAFLET, src.width, src.height, *src.bounds
        )
        destino = np.zeros((altura_dst, largura_dst), dtype=src.dtypes[0])
        reproject(
            source=rasterio.band(src, 1),
            destination=destino,
            src_transform=src.transform,
            src_crs=src.crs,
            dst_transform=transform_dst,
            dst_crs=CRS_LEAFLET,
            resampling=Resampling.nearest,  # dado categórico: nunca usar resampling contínuo
            dst_nodata=CODIGO_NODATA,
        )
        bounds_dst = rasterio.transform.array_bounds(altura_dst, largura_dst, transform_dst)
        # array_bounds retorna (left, bottom, right, top)
        bbox = rasterio.coords.BoundingBox(*bounds_dst)
    return destino, bbox


def colorir(arr_classes: np.ndarray) -> Image.Image:
    altura, largura = arr_classes.shape
    rgba = np.zeros((altura, largura, 4), dtype=np.uint8)

    codigos_presentes = set(np.unique(arr_classes).tolist())
    codigos_sem_paleta = codigos_presentes - set(PALETA_HEX)
    if codigos_sem_paleta:
        logger.warning("códigos sem paleta oficial (marcados em magenta opaco): %s", codigos_sem_paleta)

    for codigo, cor_hex in PALETA_HEX.items():
        mascara = arr_classes == codigo
        if codigo == CODIGO_NODATA:
            continue  # permanece (0,0,0,0) -> transparente
        r, g, b = hex_para_rgb(cor_hex)
        rgba[mascara] = (r, g, b, 255)

    for codigo in codigos_sem_paleta:
        mascara = arr_classes == codigo
        rgba[mascara] = (255, 0, 255, 255)

    return Image.fromarray(rgba, mode="RGBA")


def main() -> None:
    DIR_SAIDA.mkdir(parents=True, exist_ok=True)
    caminho_bounds = DIR_SAIDA / "bounds.json"

    bounds_por_ano: dict[str, dict] = {}
    if caminho_bounds.exists():
        bounds_por_ano = json.loads(caminho_bounds.read_text(encoding="utf-8")).get("bounds", {})

    for ano in ANOS:
        caminho_png = DIR_SAIDA / f"{ano}.png"
        caminho_raster = DIR_RASTER_ORIGEM / f"uso-solo_mapbiomas_{ano}_30m.tif"

        if caminho_png.exists() and str(ano) in bounds_por_ano:
            logger.info("já existe, pulando: %s", caminho_png.relative_to(RAIZ_PROJETO))
            continue

        if not caminho_raster.exists():
            logger.warning("raster não encontrado, pulando ano %s: %s", ano, caminho_raster)
            continue

        arr_wgs84, bbox = reprojetar_para_wgs84(caminho_raster)
        imagem = colorir(arr_wgs84)
        imagem.save(caminho_png)

        bounds_por_ano[str(ano)] = {
            "south": bbox.bottom,
            "west": bbox.left,
            "north": bbox.top,
            "east": bbox.right,
        }
        logger.info("gerado: %s (%dx%d px, %.1f KB)", caminho_png.relative_to(RAIZ_PROJETO), imagem.width, imagem.height, caminho_png.stat().st_size / 1024)

    caminho_bounds.write_text(
        json.dumps(
            {
                "descricao": (
                    "Bounding box em WGS84 (EPSG:4326) de cada PNG de uso do solo MapBiomas, "
                    "para uso direto com L.imageOverlay(url, L.latLngBounds([south,west],[north,east]))."
                ),
                "paleta_fonte": "MapBiomas LULC Brazil - Earth Engine Data Catalog (paleta oficial por código de classe)",
                "anos_disponiveis": sorted(int(a) for a in bounds_por_ano),
                "bounds": bounds_por_ano,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    logger.info("bounds.json atualizado com %d anos", len(bounds_por_ano))


if __name__ == "__main__":
    main()
