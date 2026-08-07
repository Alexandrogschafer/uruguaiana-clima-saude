"""
Gera os PNGs de terreno para o geoportal (hipsometria colorida + relevo
sombreado em escala de cinza), a partir dos rasters derivados do MDT ANADEM
em data/processed/ (scripts/processamento/terreno_derivados.py).

Mesma estratégia de reprojeção de gerar_png_uso_solo.py: o raster inteiro é
reprojetado para WGS84 antes de colorir, não só os cantos do bounding box —
L.imageOverlay exige um retângulo alinhado a paralelos/meridianos
geometricamente consistente com o conteúdo da imagem. Resampling nearest
nos dois casos: hipsometria é dado categórico (classe de altitude, nunca
resampling contínuo); hillshade é contínuo mas nearest evita halo escuro nas
bordas que apareceria com bilinear misturando pixels válidos com nodata.

hipsometria.png: paleta verde->amarelo->marrom interpolada entre as 11
classes de altitude reais da área (34,5-230,8 m, ver data/processed/
hipsometria_anadem_atual_30m.json), nodata (255) -> transparente.

hillshade.png: escala de cinza direta (DN do gdaldem hillshade), nodata (0,
pixels fora do recorte municipal) -> transparente. A "transparência parcial"
pra sobrepor a hipsometria é aplicada em runtime via opção `opacity` do
L.imageOverlay no front-end (~0.4), não pré-misturada no PNG — aqui só o
nodata vira transparente, o resto fica opaco.

Os dois PNGs compartilham um único bounds.json: vêm do mesmo raster-base
recortado (mesmo shape/transform/CRS em EPSG:31981), então a reprojeção
produz exatamente o mesmo bounding box em WGS84 para ambos.
"""

import json

import numpy as np
import rasterio
from PIL import Image
from rasterio.warp import Resampling, calculate_default_transform, reproject

from common import DIR_GEOPORTAL, RAIZ_PROJETO, logger

DIR_PROCESSED = RAIZ_PROJETO / "data" / "processed"
CAMINHO_HIPSOMETRIA_TIF = DIR_PROCESSED / "hipsometria_anadem_atual_30m.tif"
CAMINHO_HIPSOMETRIA_JSON = DIR_PROCESSED / "hipsometria_anadem_atual_30m.json"
CAMINHO_HILLSHADE_TIF = DIR_PROCESSED / "hillshade_anadem_atual_30m.tif"

DIR_SAIDA = DIR_GEOPORTAL / "terreno"
CRS_LEAFLET = "EPSG:4326"

# paleta hipsométrica padrão: verde (baixo) -> amarelo (médio) -> marrom (alto)
PONTOS_RAMPA_HIPSOMETRICA = [
    (0.0, (46, 110, 64)),
    (0.5, (216, 196, 84)),
    (1.0, (140, 89, 51)),
]


def _interpolar_cor(fracao: float, pontos: list) -> tuple:
    for (f0, cor0), (f1, cor1) in zip(pontos, pontos[1:]):
        if f0 <= fracao <= f1:
            t = 0 if f1 == f0 else (fracao - f0) / (f1 - f0)
            return tuple(round(cor0[i] + t * (cor1[i] - cor0[i])) for i in range(3))
    return pontos[-1][1]


def _rampa_hipsometrica(n_classes: int) -> list:
    if n_classes == 1:
        return [_interpolar_cor(0.0, PONTOS_RAMPA_HIPSOMETRICA)]
    return [_interpolar_cor(i / (n_classes - 1), PONTOS_RAMPA_HIPSOMETRICA) for i in range(n_classes)]


def _reprojetar_para_wgs84(caminho_raster, nodata_saida: int):
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
            src_nodata=src.nodata,
            dst_nodata=nodata_saida,
            resampling=Resampling.nearest,
        )
        bounds_dst = rasterio.transform.array_bounds(altura_dst, largura_dst, transform_dst)
        bbox = rasterio.coords.BoundingBox(*bounds_dst)
    return destino, bbox


def gerar_hipsometria() -> rasterio.coords.BoundingBox:
    legenda = json.loads(CAMINHO_HIPSOMETRIA_JSON.read_text(encoding="utf-8"))["legenda_classes"]
    rampa = _rampa_hipsometrica(len(legenda))

    arr, bbox = _reprojetar_para_wgs84(CAMINHO_HIPSOMETRIA_TIF, nodata_saida=255)

    rgba = np.zeros((*arr.shape, 4), dtype=np.uint8)
    for classe, cor in enumerate(rampa):
        rgba[arr == classe] = (*cor, 255)
    # arr == 255 (nodata) permanece (0,0,0,0) -> transparente

    caminho_png = DIR_SAIDA / "hipsometria.png"
    Image.fromarray(rgba, mode="RGBA").save(caminho_png)
    logger.info("gerado: %s (%d classes, %.1f KB)", caminho_png.relative_to(RAIZ_PROJETO), len(rampa), caminho_png.stat().st_size / 1024)
    return bbox


def gerar_hillshade() -> rasterio.coords.BoundingBox:
    arr, bbox = _reprojetar_para_wgs84(CAMINHO_HILLSHADE_TIF, nodata_saida=0)

    rgba = np.zeros((*arr.shape, 4), dtype=np.uint8)
    mascara_valida = arr > 0
    for banda in range(3):
        rgba[..., banda][mascara_valida] = arr[mascara_valida]
    rgba[..., 3][mascara_valida] = 255
    # arr == 0 (nodata, fora do recorte municipal) permanece (0,0,0,0) -> transparente

    caminho_png = DIR_SAIDA / "hillshade.png"
    Image.fromarray(rgba, mode="RGBA").save(caminho_png)
    logger.info("gerado: %s (%.1f KB)", caminho_png.relative_to(RAIZ_PROJETO), caminho_png.stat().st_size / 1024)
    return bbox


def main() -> None:
    DIR_SAIDA.mkdir(parents=True, exist_ok=True)
    caminho_bounds = DIR_SAIDA / "bounds.json"

    if (DIR_SAIDA / "hipsometria.png").exists() and (DIR_SAIDA / "hillshade.png").exists() and caminho_bounds.exists():
        logger.info("terreno já gerado, pulando: %s", DIR_SAIDA.relative_to(RAIZ_PROJETO))
        return

    bbox_hipsometria = gerar_hipsometria()
    bbox_hillshade = gerar_hillshade()

    # mesmo shape/transform/CRS de origem -> bounds reprojetados devem ser
    # idênticos; checagem defensiva caso os rasters de origem um dia divirjam
    assert bbox_hipsometria == bbox_hillshade, "bounds de hipsometria e hillshade divergiram — checar rasters de origem"

    caminho_bounds.write_text(
        json.dumps(
            {
                "descricao": (
                    "Bounding box em WGS84 (EPSG:4326), compartilhado por hipsometria.png e "
                    "hillshade.png (mesmo raster-base recortado), para uso com "
                    "L.imageOverlay(url, L.latLngBounds([south,west],[north,east]))."
                ),
                "fonte": {
                    "hipsometria": str(CAMINHO_HIPSOMETRIA_TIF.relative_to(RAIZ_PROJETO)),
                    "hillshade": str(CAMINHO_HILLSHADE_TIF.relative_to(RAIZ_PROJETO)),
                },
                "south": bbox_hipsometria.bottom,
                "west": bbox_hipsometria.left,
                "north": bbox_hipsometria.top,
                "east": bbox_hipsometria.right,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    logger.info("bounds.json gerado em %s", caminho_bounds.relative_to(RAIZ_PROJETO))


if __name__ == "__main__":
    main()
