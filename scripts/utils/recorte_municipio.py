"""
Utilitários de recorte espacial pela área de estudo do projeto.

A área de estudo é sempre lida a partir de um único arquivo de referência
(config/area_estudo.geojson), gerado por scripts/download/vetor_ibge.py.
Isso evita que cada script "reinvente" o polígono do município.

CRS padrão do projeto: EPSG:31982 (SIRGAS 2000 / UTM 22S).
"""

from pathlib import Path

import geopandas as gpd

CRS_PADRAO = "EPSG:31982"
CAMINHO_AREA_ESTUDO_PADRAO = Path(__file__).resolve().parents[2] / "config" / "area_estudo.geojson"


def carregar_area_estudo(caminho: Path = CAMINHO_AREA_ESTUDO_PADRAO) -> gpd.GeoDataFrame:
    """Carrega a área de estudo de referência, já no CRS padrão do projeto.

    Levanta FileNotFoundError com mensagem orientativa se o arquivo ainda
    não existir (isto é, se vetor_ibge.py ainda não foi executado).
    """
    if not caminho.exists():
        raise FileNotFoundError(
            f"Área de estudo não encontrada em {caminho}. "
            "Rode primeiro: python scripts/download/vetor_ibge.py --codigo-ibge <codigo>"
        )
    gdf = gpd.read_file(caminho)
    if gdf.crs is None:
        raise ValueError(f"{caminho} não possui CRS definido — verifique a geração do arquivo.")
    if gdf.crs.to_string() != CRS_PADRAO:
        gdf = gdf.to_crs(CRS_PADRAO)
    return gdf


def recortar_vetor(gdf: gpd.GeoDataFrame, area_estudo: gpd.GeoDataFrame | None = None) -> gpd.GeoDataFrame:
    """Recorta um GeoDataFrame pela área de estudo (clip), reprojetando se necessário."""
    if area_estudo is None:
        area_estudo = carregar_area_estudo()
    if gdf.crs is None:
        raise ValueError("GeoDataFrame de entrada não possui CRS definido.")
    if gdf.crs.to_string() != CRS_PADRAO:
        gdf = gdf.to_crs(CRS_PADRAO)
    return gpd.clip(gdf, area_estudo)


def recortar_raster(caminho_raster: Path, caminho_saida: Path, area_estudo: gpd.GeoDataFrame | None = None) -> Path:
    """Recorta um raster pela área de estudo (bounding geometry) e reprojeta para o CRS padrão.

    Requer rasterio; import feito localmente para não obrigar a dependência
    em contextos que só usam vetor.
    """
    import rasterio
    from rasterio.mask import mask
    from rasterio.warp import Resampling, calculate_default_transform, reproject

    if area_estudo is None:
        area_estudo = carregar_area_estudo()

    with rasterio.open(caminho_raster) as src:
        # Reprojeta a área de estudo para o CRS do raster de origem antes do recorte
        area_no_crs_origem = area_estudo.to_crs(src.crs)
        geometrias = area_no_crs_origem.geometry.values
        imagem_recortada, transform_recorte = mask(src, geometrias, crop=True)
        perfil = src.profile.copy()
        perfil.update(
            height=imagem_recortada.shape[1],
            width=imagem_recortada.shape[2],
            transform=transform_recorte,
        )

        caminho_saida.parent.mkdir(parents=True, exist_ok=True)
        # Etapa intermediária já recortada, mas ainda no CRS original
        caminho_tmp = caminho_saida.with_suffix(".tmp.tif")
        with rasterio.open(caminho_tmp, "w", **perfil) as dst:
            dst.write(imagem_recortada)

    # Reprojeta o recorte para o CRS padrão do projeto
    with rasterio.open(caminho_tmp) as src:
        transform_final, largura_final, altura_final = calculate_default_transform(
            src.crs, CRS_PADRAO, src.width, src.height, *src.bounds
        )
        perfil_final = src.profile.copy()
        perfil_final.update(crs=CRS_PADRAO, transform=transform_final, width=largura_final, height=altura_final)

        with rasterio.open(caminho_saida, "w", **perfil_final) as dst:
            for banda in range(1, src.count + 1):
                reproject(
                    source=rasterio.band(src, banda),
                    destination=rasterio.band(dst, banda),
                    src_transform=src.transform,
                    src_crs=src.crs,
                    dst_transform=transform_final,
                    dst_crs=CRS_PADRAO,
                    resampling=Resampling.nearest,
                )
    caminho_tmp.unlink(missing_ok=True)
    return caminho_saida
