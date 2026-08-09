"""Calcula Áreas de Preservação Permanente (APP) hídricas ao longo dos
cursos d'água (Lei 12.651/2012 — Código Florestal, art. 4º, inciso I) a
partir da rede de drenagem já baixada (ANA/BHO), e cruza o resultado com
o uso e cobertura do solo (MapBiomas 2024) para estimar ocupação
antrópica dentro da APP:

    data/processed/app-hidrica_calculado_atual_vetorial.gpkg
    data/processed/app-hidrica_calculado_atual_vetorial.json
    data/processed/app-ocupacao_calculado_2024.csv
    data/processed/app-ocupacao_calculado_2024.json

IMPORTANTE — isto é uma APROXIMAÇÃO METODOLÓGICA, não uma delimitação
técnica de APP para fins de fiscalização/licenciamento
--------------------------------------------------------------------
A rede de drenagem da BHO (ANA) não tem campo de largura do curso
d'água. Por isso:

- Para toda a rede de drenagem local (arroios, sangas, córregos etc.),
  assume-se a premissa conservadora de largura < 10m -> faixa de APP de
  30m (art. 4º, I, "a" da Lei 12.651/2012). Isso é uma suposição, não uma
  medição trecho a trecho.
- Para o rio Uruguai (curso d'água principal, fronteiriço, claramente
  mais largo que os demais), a largura foi ESTIMADA por medição
  computacional de seções transversais contra o polígono de corpo d'água
  do tema Vegetação do BDiA/IBGE (vegetacao_ibge-bdia, escala
  1:250.000) — não é uma medição de campo nem um dado oficial de
  batimetria/largura, é uma aproximação a partir de outro dado
  aproximado. Ver função `medir_largura_rio_uruguai` e o metadado gerado
  para o método completo e as estatísticas da amostragem.

Delimitação técnica de APP para fins legais exigiria levantamento
topográfico/batimétrico trecho a trecho, não coberto por este script.

Uso:
    python scripts/processamento/app_hidrica.py
    python scripts/processamento/app_hidrica.py --forcar
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
import rasterio
from rasterstats import zonal_stats
from shapely.geometry import LineString
from shapely.ops import linemerge, unary_union

sys.path.append(str(Path(__file__).resolve().parents[1] / "utils"))
from recorte_municipio import CRS_PADRAO, carregar_area_estudo  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

RAIZ = Path(__file__).resolve().parents[2]
CAMINHO_REDE_HIDRICA = RAIZ / "data" / "raw" / "vetor" / "rede-hidrografica_ana-bho_atual_vetorial.gpkg"
CAMINHO_VEGETACAO_BDIA = RAIZ / "data" / "raw" / "vetor" / "vegetacao_ibge-bdia_atual_vetorial.gpkg"
CAMINHO_MAPBIOMAS_2024 = RAIZ / "data" / "raw" / "raster" / "uso-solo_mapbiomas_2024_30m.tif"

CAMINHO_SAIDA_APP = RAIZ / "data" / "processed" / "app-hidrica_calculado_atual_vetorial.gpkg"
CAMINHO_SAIDA_OCUPACAO = RAIZ / "data" / "processed" / "app-ocupacao_calculado_2024.csv"

NOME_RIO_PRINCIPAL = "Rio Uruguai"
LARGURA_DEFAULT_M = 8.0  # premissa: < 10m -> classe "a" do art. 4º, I

# Classes de APP conforme art. 4º, I da Lei 12.651/2012 (Código Florestal):
# (largura_max_do_curso_dagua_m, faixa_app_m, rótulo)
CLASSES_APP_CODIGO_FLORESTAL = [
    (10, 30, "< 10m -> 30m"),
    (50, 50, "10-50m -> 50m"),
    (200, 100, "50-200m -> 100m"),
    (600, 200, "200-600m -> 200m"),
    (float("inf"), 500, "> 600m -> 500m"),
]

# Classificação MapBiomas (Coleção 10, códigos observados na área de estudo — ver
# scripts/geoportal/paleta_mapbiomas.py) em natural / antrópica / água, para o indicador
# de ocupação da APP. "Floresta Plantada" (9, silvicultura) entra como antrópica: é uma
# conversão de vegetação nativa para monocultura, land use antrópico mesmo com dossel
# arbóreo. "Rio, Lago e Oceano" (33) é tratado à parte (nem vegetação nem "ocupação").
CLASSIFICACAO_MAPBIOMAS = {
    3: ("Formação Florestal", "natural"),
    9: ("Floresta Plantada", "antropica"),
    11: ("Área Úmida", "natural"),
    12: ("Formação Campestre", "natural"),
    21: ("Mosaico de Usos", "antropica"),
    24: ("Área Urbana", "antropica"),
    25: ("Outra Área não Vegetada", "antropica"),
    29: ("Afloramento Rochoso", "natural"),
    33: ("Rio, Lago e Oceano", "agua"),
    39: ("Soja", "antropica"),
    40: ("Arroz", "antropica"),
    41: ("Outras Culturas Temporárias", "antropica"),
}


def classe_app_por_largura(largura_m: float) -> tuple[str, int]:
    for largura_max, faixa_app, rotulo in CLASSES_APP_CODIGO_FLORESTAL:
        if largura_m < largura_max:
            return rotulo, faixa_app
    return CLASSES_APP_CODIGO_FLORESTAL[-1][2], CLASSES_APP_CODIGO_FLORESTAL[-1][1]


def medir_largura_rio_uruguai(trechos_rio: gpd.GeoDataFrame, passo_m: float = 300, meia_sonda_m: float = 2000) -> dict:
    """Estima a largura do rio Uruguai por seções transversais perpendiculares à linha
    de centro (trechos de drenagem da BHO), medindo o comprimento da interseção de cada
    seção com o polígono de corpo d'água do BDiA/IBGE (vegetacao_ibge-bdia).

    Método: amostra pontos ao longo da linha de centro a cada `passo_m`; em cada ponto,
    estima a tangente local por diferença finita (+-50m) e projeta uma sonda perpendicular
    de comprimento `2 * meia_sonda_m`; a largura no ponto é o comprimento do segmento da
    sonda que intersecta o polígono de água E contém o próprio ponto (evita contar ilhas/
    braços distantes na mesma sonda).
    """
    if not CAMINHO_VEGETACAO_BDIA.exists():
        raise FileNotFoundError(
            f"{CAMINHO_VEGETACAO_BDIA} não encontrado. Rode primeiro: "
            "python scripts/download/vegetacao_ibge_bdia.py"
        )
    vege = gpd.read_file(CAMINHO_VEGETACAO_BDIA, layer="vegetacao")
    agua = vege[vege["legenda"].str.contains("água", case=False, na=False)]
    if agua.empty:
        raise RuntimeError("Nenhum polígono de corpo d'água encontrado em vegetacao_ibge-bdia — não é possível medir a largura do rio Uruguai.")
    poligono_agua = unary_union(agua.geometry.values)

    linha = unary_union(trechos_rio.geometry.values)
    linha = linemerge(linha)
    partes = list(linha.geoms) if linha.geom_type == "MultiLineString" else [linha]

    larguras = []
    for parte in partes:
        comprimento = parte.length
        if comprimento < passo_m:
            continue
        distancia = 0.0
        while distancia < comprimento:
            ponto = parte.interpolate(distancia)
            p0 = parte.interpolate(max(0, distancia - 50))
            p1 = parte.interpolate(min(comprimento, distancia + 50))
            dx, dy = p1.x - p0.x, p1.y - p0.y
            norma = (dx**2 + dy**2) ** 0.5
            if norma == 0:
                distancia += passo_m
                continue
            perp = (-dy / norma, dx / norma)
            sonda = LineString([
                (ponto.x - perp[0] * meia_sonda_m, ponto.y - perp[1] * meia_sonda_m),
                (ponto.x + perp[0] * meia_sonda_m, ponto.y + perp[1] * meia_sonda_m),
            ])
            intersecao = sonda.intersection(poligono_agua)
            if not intersecao.is_empty:
                geoms = list(intersecao.geoms) if intersecao.geom_type.startswith("Multi") else [intersecao]
                comprimentos_validos = [
                    g.length for g in geoms if g.geom_type == "LineString" and g.distance(ponto) < 5
                ]
                if comprimentos_validos:
                    larguras.append(max(comprimentos_validos))
            distancia += passo_m

    larguras_arr = np.array(larguras)
    resultado = {
        "n_amostras": int(len(larguras_arr)),
        "largura_m_min": round(float(larguras_arr.min()), 1),
        "largura_m_mediana": round(float(np.median(larguras_arr)), 1),
        "largura_m_media": round(float(larguras_arr.mean()), 1),
        "largura_m_max": round(float(larguras_arr.max()), 1),
        "largura_m_p10": round(float(np.percentile(larguras_arr, 10)), 1),
        "largura_m_p90": round(float(np.percentile(larguras_arr, 90)), 1),
        "passo_amostragem_m": passo_m,
        "metodo": (
            "seções transversais perpendiculares à linha de centro (trechos de drenagem BHO "
            "classificados como 'Rio Uruguai'), medindo a interseção com o polígono de corpo "
            "d'água do BDiA/IBGE (vegetacao_ibge-bdia, escala 1:250.000)"
        ),
    }
    return resultado


def construir_geometrias_app(trechos: gpd.GeoDataFrame, largura_rio_uruguai_m: float) -> gpd.GeoDataFrame:
    trechos = trechos.copy()
    eh_rio_uruguai = trechos["nooriginal"] == NOME_RIO_PRINCIPAL

    classe_rio, faixa_rio = classe_app_por_largura(largura_rio_uruguai_m)
    classe_local, faixa_local = classe_app_por_largura(LARGURA_DEFAULT_M)

    trechos["largura_assumida_m"] = np.where(eh_rio_uruguai, largura_rio_uruguai_m, LARGURA_DEFAULT_M)
    trechos["classe_largura_codigo_florestal"] = np.where(eh_rio_uruguai, classe_rio, classe_local)
    trechos["faixa_app_m"] = np.where(eh_rio_uruguai, faixa_rio, faixa_local)
    trechos["curso_dagua"] = np.where(eh_rio_uruguai, "rio_uruguai", "rede_local")

    trechos["geometry"] = trechos.apply(lambda linha: linha.geometry.buffer(linha["faixa_app_m"]), axis=1)

    app_por_curso = (
        trechos[["curso_dagua", "classe_largura_codigo_florestal", "faixa_app_m", "geometry"]]
        .dissolve(by="curso_dagua", aggfunc={"faixa_app_m": "first", "classe_largura_codigo_florestal": "first"})
        .reset_index()
    )
    app_por_curso = gpd.GeoDataFrame(app_por_curso, geometry="geometry", crs=trechos.crs)
    app_por_curso["area_km2"] = app_por_curso.geometry.area / 1e6
    return app_por_curso


def calcular_ocupacao_mapbiomas(app_unificada: gpd.base.BaseGeometry) -> pd.DataFrame:
    if not CAMINHO_MAPBIOMAS_2024.exists():
        raise FileNotFoundError(
            f"{CAMINHO_MAPBIOMAS_2024} não encontrado. Rode primeiro: "
            "python scripts/download/uso-solo_mapbiomas.py --anos 2024"
        )
    with rasterio.open(CAMINHO_MAPBIOMAS_2024) as src:
        pixel_area_m2 = abs(src.transform.a * src.transform.e)

    # nodata=0: código 0 do MapBiomas ("Não observado") é usado neste projeto para a área fora do
    # recorte municipal (ver scripts/geoportal/paleta_mapbiomas.py) — tratamos como nodata real aqui
    # em vez do -999 default do rasterstats, para não contar esses pixels de borda como classe válida.
    stats = zonal_stats([app_unificada], str(CAMINHO_MAPBIOMAS_2024), categorical=True, nodata=0)
    contagens = stats[0] if stats else {}

    linhas = []
    for codigo, n_pixels in contagens.items():
        codigo_int = int(codigo)
        nome, tipo = CLASSIFICACAO_MAPBIOMAS.get(codigo_int, (f"Classe {codigo_int} (não catalogada)", "não_classificada"))
        linhas.append({
            "codigo_mapbiomas": codigo_int,
            "classe_mapbiomas": nome,
            "tipo": tipo,
            "area_km2": round(n_pixels * pixel_area_m2 / 1e6, 4),
        })
    df = pd.DataFrame(linhas).sort_values("area_km2", ascending=False).reset_index(drop=True)
    area_total_km2 = df["area_km2"].sum()
    df["pct_da_app_total"] = (df["area_km2"] / area_total_km2 * 100).round(2)
    return df


def main() -> None:
    parser = argparse.ArgumentParser(description="Calcula APP hídrica derivada da rede de drenagem (BHO) e sua ocupação por uso do solo (MapBiomas 2024).")
    parser.add_argument("--forcar", action="store_true", help="Recalcula mesmo se as saídas já existirem")
    args = parser.parse_args()

    saidas = [CAMINHO_SAIDA_APP, CAMINHO_SAIDA_APP.with_suffix(".json"), CAMINHO_SAIDA_OCUPACAO, CAMINHO_SAIDA_OCUPACAO.with_suffix(".json")]
    if all(p.exists() for p in saidas) and not args.forcar:
        logger.info("Saídas de APP hídrica já existem — nada a fazer (use --forcar para recalcular).")
        return

    if not CAMINHO_REDE_HIDRICA.exists():
        raise FileNotFoundError(f"{CAMINHO_REDE_HIDRICA} não encontrado. Rode primeiro: python scripts/download/hidrologia_bho.py")

    area_estudo = carregar_area_estudo()
    trechos = gpd.read_file(CAMINHO_REDE_HIDRICA, layer="trecho_drenagem")
    if trechos.crs.to_string() != CRS_PADRAO:
        trechos = trechos.to_crs(CRS_PADRAO)

    trechos_rio_uruguai = trechos[trechos["nooriginal"] == NOME_RIO_PRINCIPAL]
    logger.info("Trechos identificados como '%s': %d", NOME_RIO_PRINCIPAL, len(trechos_rio_uruguai))
    if trechos_rio_uruguai.empty:
        raise RuntimeError(f"Nenhum trecho com nooriginal == '{NOME_RIO_PRINCIPAL}' encontrado na rede de drenagem.")

    medicao_largura = medir_largura_rio_uruguai(trechos_rio_uruguai)
    largura_estimada = medicao_largura["largura_m_mediana"]
    logger.info("Largura estimada do rio Uruguai (mediana de %d amostras): %.1f m (min %.1f, max %.1f)",
                medicao_largura["n_amostras"], largura_estimada, medicao_largura["largura_m_min"], medicao_largura["largura_m_max"])

    app_por_curso = construir_geometrias_app(trechos, largura_estimada)
    app_por_curso = gpd.clip(app_por_curso, area_estudo)
    app_por_curso["area_km2"] = app_por_curso.geometry.area / 1e6

    CAMINHO_SAIDA_APP.parent.mkdir(parents=True, exist_ok=True)
    app_por_curso.to_file(CAMINHO_SAIDA_APP, driver="GPKG", layer="app_hidrica")
    logger.info("APP por curso d'água: %s", app_por_curso[["curso_dagua", "classe_largura_codigo_florestal", "faixa_app_m", "area_km2"]].to_dict("records"))

    classe_rio, faixa_rio = classe_app_por_largura(largura_estimada)
    metadados_app = {
        "fonte_base": "ANA — BHO 6 (rede-hidrografica_ana-bho_atual_vetorial.gpkg, camada trecho_drenagem)",
        "base_legal": "Lei 12.651/2012 (Código Florestal), art. 4º, inciso I — APP ao longo de cursos d'água naturais",
        "premissa_rede_local": (
            f"largura assumida < 10m ({LARGURA_DEFAULT_M}m) para toda a rede de drenagem exceto o rio "
            f"Uruguai -> faixa de APP de 30m. É uma PREMISSA conservadora, não uma medição — a BHO não "
            "tem campo de largura do curso d'água."
        ),
        "rio_uruguai": {
            "criterio_identificacao": "trecho_drenagem.nooriginal == 'Rio Uruguai' (sem hardcode geométrico)",
            "n_trechos": int(len(trechos_rio_uruguai)),
            "medicao_largura": medicao_largura,
            "largura_usada_m": largura_estimada,
            "classe_codigo_florestal": classe_rio,
            "faixa_app_aplicada_m": faixa_rio,
        },
        "crs": CRS_PADRAO,
        "recorte": "área de estudo municipal (config/area_estudo.geojson)",
        "area_km2_por_curso": app_por_curso.set_index("curso_dagua")["area_km2"].round(2).to_dict(),
        "aviso_metodologico": (
            "Esta é uma APROXIMAÇÃO METODOLÓGICA para análise exploratória (largura assumida/estimada, "
            "não medida trecho a trecho por levantamento topográfico ou batimétrico). NÃO deve ser usada "
            "para fins de fiscalização, licenciamento ambiental ou qualquer delimitação legal de APP, que "
            "exige delimitação técnica precisa por profissional habilitado."
        ),
        "data_processamento": datetime.now(timezone.utc).isoformat(),
    }
    CAMINHO_SAIDA_APP.with_suffix(".json").write_text(
        json.dumps(metadados_app, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )
    logger.info("Salvo: %s", CAMINHO_SAIDA_APP)

    app_unificada = unary_union(app_por_curso.geometry.values)
    df_ocupacao = calcular_ocupacao_mapbiomas(app_unificada)

    area_total_km2 = df_ocupacao["area_km2"].sum()
    area_antropica_km2 = df_ocupacao.loc[df_ocupacao["tipo"] == "antropica", "area_km2"].sum()
    area_natural_km2 = df_ocupacao.loc[df_ocupacao["tipo"] == "natural", "area_km2"].sum()
    area_agua_km2 = df_ocupacao.loc[df_ocupacao["tipo"] == "agua", "area_km2"].sum()
    area_nao_classificada_km2 = df_ocupacao.loc[df_ocupacao["tipo"] == "não_classificada", "area_km2"].sum()
    pct_antropica = round(100 * area_antropica_km2 / area_total_km2, 2) if area_total_km2 else 0.0
    area_terrestre_km2 = area_total_km2 - area_agua_km2
    pct_antropica_sobre_area_terrestre = (
        round(100 * area_antropica_km2 / area_terrestre_km2, 2) if area_terrestre_km2 else 0.0
    )

    CAMINHO_SAIDA_OCUPACAO.parent.mkdir(parents=True, exist_ok=True)
    df_ocupacao.to_csv(CAMINHO_SAIDA_OCUPACAO, index=False)
    logger.info("Ocupação da APP por classe MapBiomas 2024 salva em %s", CAMINHO_SAIDA_OCUPACAO)
    logger.info("%% de área de APP ocupada por classes antrópicas: %.2f%% (%.2f km² de %.2f km²)",
                pct_antropica, area_antropica_km2, area_total_km2)

    metadados_ocupacao = {
        "fonte_app": "data/processed/app-hidrica_calculado_atual_vetorial.gpkg (união de todas as classes de curso d'água, sem sobreposição dupla-contada)",
        "fonte_uso_solo": "MapBiomas Coleção 10, ano 2024 (data/raw/raster/uso-solo_mapbiomas_2024_30m.tif)",
        "metodo": "zonal_stats categórico (rasterstats) da grade MapBiomas 2024 sobre o polígono unificado da APP",
        "classificacao_classes": {str(k): {"nome": v[0], "tipo": v[1]} for k, v in CLASSIFICACAO_MAPBIOMAS.items()},
        "nota_classificacao": (
            "Floresta Plantada (9, silvicultura) classificada como antrópica — é conversão de vegetação "
            "nativa para monocultura arbórea, land use antrópico mesmo com dossel. Rio/Lago/Oceano (33) "
            "tratado à parte (nem vegetação nem 'ocupação' antrópica) — não entra no numerador nem no "
            "denominador do indicador de degradação, mas aparece na tabela para referência de área."
        ),
        "area_total_app_km2": round(area_total_km2, 2),
        "area_natural_km2": round(area_natural_km2, 2),
        "area_antropica_km2": round(area_antropica_km2, 2),
        "area_agua_km2": round(area_agua_km2, 2),
        "area_nao_classificada_km2": round(area_nao_classificada_km2, 2),
        "pct_area_app_ocupada_por_classes_antropicas": pct_antropica,
        "pct_area_app_antropica_sobre_area_terrestre_excl_agua": pct_antropica_sobre_area_terrestre,
        "nota_denominador": (
            "pct_area_app_ocupada_por_classes_antropicas usa a área TOTAL da APP como denominador "
            "(inclui água e a pequena fração 'não_classificada', pixels de borda com valor 0 do "
            "recorte municipal do raster MapBiomas — ~1-2% da área, efeito esperado de borda em "
            "recorte raster/vetor, não erro de processamento). "
            "pct_area_app_antropica_sobre_area_terrestre_excl_agua exclui a superfície de água do "
            "denominador — mais adequado para interpretar 'quanto da terra dentro da APP está "
            "antropizada', já que água não é 'ocupável'."
        ),
        "aviso_metodologico": (
            "indicador exploratório de degradação/ocupação irregular — depende da APROXIMAÇÃO "
            "metodológica da APP em si (ver app-hidrica_calculado_atual_vetorial.json) e da resolução "
            "de 30m do MapBiomas; não substitui vistoria de campo para fins de fiscalização"
        ),
        "data_processamento": datetime.now(timezone.utc).isoformat(),
    }
    CAMINHO_SAIDA_OCUPACAO.with_suffix(".json").write_text(
        json.dumps(metadados_ocupacao, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )
    logger.info("Metadados salvos em %s", CAMINHO_SAIDA_OCUPACAO.with_suffix(".json"))


if __name__ == "__main__":
    main()
