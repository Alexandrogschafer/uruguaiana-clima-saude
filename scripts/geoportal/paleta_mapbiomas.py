"""
Paleta oficial de cores do MapBiomas por código de classe (pixel value).

Fonte: catálogo de dados do Google Earth Engine para o MapBiomas Land Use
and Land Cover — Brazil (paleta "collection" atual, estável desde a
Coleção 6+), cruzada com os PDFs oficiais "Códigos das classes da legenda
e paleta de cores" (brasil.mapbiomas.org). Cobre todos os 13 códigos
observados na série 1985-2024 da área de estudo (ver
scripts/geoportal/gerar_png_uso_solo.py).

Código 0 = "Não observado" — nos rasters do projeto corresponde à área
fora do recorte do município (mask/crop em scripts/utils/recorte_municipio.py),
por isso é tratado como nodata (transparente), não como uma classe real.
"""

PALETA_HEX = {
    0: "#ffffff",  # Não observado / fora do recorte -> tratado como nodata (transparente)
    3: "#1f8d49",  # Formação Florestal
    9: "#7a5900",  # Floresta Plantada
    11: "#519799",  # Área Úmida (Wetland)
    12: "#d6bc74",  # Formação Campestre (Grassland)
    21: "#ffefc3",  # Mosaico de Usos
    24: "#d4271e",  # Área Urbana
    25: "#db4d4f",  # Outra Área não Vegetada
    29: "#ffaa5f",  # Afloramento Rochoso
    33: "#2532e4",  # Rio, Lago e Oceano
    39: "#f5b3c8",  # Soja
    40: "#c71585",  # Arroz
    41: "#f54ca9",  # Outras Culturas Temporárias
}

CODIGO_NODATA = 0


def hex_para_rgb(codigo_hex: str) -> tuple[int, int, int]:
    codigo_hex = codigo_hex.lstrip("#")
    return tuple(int(codigo_hex[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
