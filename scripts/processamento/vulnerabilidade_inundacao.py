"""
Cruza os setores censitários (com indicadores de vulnerabilidade do Censo
2022) e os estabelecimentos de saúde (CNES) com as cotas de inundação do
rio Uruguai (SGB), para estimar população e serviços de saúde expostos a
cada nível de cheia. Primeiro script de processamento/cruzamento do
projeto (os scripts anteriores em scripts/download/ só baixam dado bruto).

Entradas (todas já geradas por scripts de download existentes):
    data/raw/vetor/setores-censitarios_ibge_2022_vetorial.gpkg
    data/raw/vulnerabilidade-censo_ibge_2022.csv
    data/raw/vetor/cotas-inundacao_sgb_atual_vetorial.gpkg
    data/raw/vetor/saude-cnes_datasus_atual_vetorial.gpkg
    data/raw/raster/uso-solo_mapbiomas_2024_30m.tif

Saídas:
    data/processed/populacao-exposta-inundacao_por-cota.csv
        tabela consolidada, uma linha por cota de inundação.
    data/processed/setores-inundacao_intersecao.gpkg
        geometria de cada interseção setor x cota (para mapear depois).
    data/processed/saude-estabelecimentos-exposicao-inundacao_por-cota.csv
        contagem de estabelecimentos de saúde por cota x tipo de unidade.

Dois métodos de estimativa de população exposta (leia com atenção)
--------------------------------------------------------------------
O Censo não informa onde, dentro do setor, a população está concentrada.
Este script calcula DOIS métodos de estimativa, lado a lado, para a mesma
pergunta ("quantas pessoas do setor estão na área alagada?"):

1. populacao_estimada_area-proporcional (método original)
   Assume distribuição UNIFORME da população dentro do polígono do setor:

       populacao_total_do_setor * (área da interseção com a cota / área
       total do setor)

   Simplificação: setores raramente são uniformes (podem ter área rural
   vazia e um bairro denso), então tende a super/subestimar a exposição
   conforme a distribuição interna real da população no setor.

2. populacao_estimada_ponderada_uso-solo (método novo)
   Em vez de usar a área geométrica total do setor como proxy de "onde
   mora gente", usa a área classificada como "Área Urbanizada" (MapBiomas
   Coleção 10, class_id 24, ano 2024) dentro do setor:

       populacao_total_do_setor * (área urbanizada na interseção com a
       cota / área urbanizada total do setor)

   A hipótese inicial era que isso daria estimativas MAIORES nos setores
   urbanos ribeirinhos (a inundação cobre pouca ÁREA TOTAL do setor mas
   grande parte da área onde as pessoas realmente vivem). NA PRÁTICA, o
   resultado observado é o OPOSTO: para as 4 cotas, a estimativa ponderada
   por uso do solo é 58-82% MENOR que a área-proporcional (ver
   comparacao_metodos_cota_833cm no metadado .json). Investigando setor a
   setor, a maioria dos setores urbanos afetados tem 0% de área urbanizada
   dentro da própria interseção com a cota, mesmo cobrindo boa parte da
   área TOTAL do setor: a faixa de terreno mais próxima do rio — onde
   incide a inundação — quase não tem pixel classificado como "Área
   Urbanizada" pelo MapBiomas. Duas explicações prováveis, não
   excludentes: (a) um padrão real de ocupação, com o tecido urbano em
   cota mais alta, afastado da faixa mais sujeita a cheias frequentes; (b)
   uma limitação de classificação — terreno baixo/periodicamente alagado
   tende a virar "Campo Alagado"/"Rio, Lago e Oceano" em vez de "Área
   Urbanizada" no MapBiomas, mesmo havendo alguma ocupação ali. Na
   prática, os dois métodos funcionam melhor como limites de um intervalo
   de incerteza (área-proporcional tende a SUPERESTIMAR, uso-solo tende a
   SUBESTIMAR a exposição na faixa ribeirinha) do que como um substituindo
   o outro.

   Para setores predominantemente RURAIS (pouca ou nenhuma área
   classificada como urbanizada — população dispersa que o pixel de 30m
   de "Área Urbanizada" não capta), o método ponderado por uso do solo não
   é aplicável (proporção calculada sobre uma área urbana quase nula), e o
   script usa o método de área-proporcional como FALLBACK nesses casos. A
   coluna `metodo_estimativa_uso_solo` na interseção indica qual dos dois
   métodos foi efetivamente usado em cada setor.

   IMPORTANTE: "Mosaico de Usos" (class_id 21, a classe mais extensa no
   município — ver data/processed/uso-solo_mapbiomas_serie-temporal_area-
   por-classe.csv) foi deliberadamente EXCLUÍDO do peso urbano. É uma
   classe de mosaico agropecuário (lavoura/pastagem com estruturas rurais
   esparsas), não tecido residencial — incluí-la inundaria o sinal urbano
   com uma área enorme de terra agrícola e contradiria o objetivo do
   método (achar onde a população realmente se concentra).

Ver PREMISSAS_METODOLOGICAS abaixo e o metadado .json de cada saída para
o texto completo, incluindo a comparação numérica entre os dois métodos
para a cota mais frequente (833cm).

As 4 cotas do SGB (833/952/1205/1252 cm, TR 1.3/1.9/9.0/13.4 anos) têm
áreas crescentes mas NÃO são estritamente aninhadas (a cota menor não é um
subconjunto perfeito da maior — checado geometricamente: ~97,6% da área da
cota 833cm está dentro da cota 952cm, não 100%), possivelmente por
diferenças de digitalização entre as camadas de origem. Por isso cada cota
é processada de forma independente (overlay separado), sem assumir
contenção entre elas.

Uso:
    python scripts/processamento/vulnerabilidade_inundacao.py
    python scripts/processamento/vulnerabilidade_inundacao.py --forcar
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

sys.path.append(str(Path(__file__).resolve().parents[1] / "utils"))
from recorte_municipio import CRS_PADRAO  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

RAIZ = Path(__file__).resolve().parents[2]
CAMINHO_SETORES = RAIZ / "data" / "raw" / "vetor" / "setores-censitarios_ibge_2022_vetorial.gpkg"
CAMINHO_INDICADORES = RAIZ / "data" / "raw" / "vulnerabilidade-censo_ibge_2022.csv"
CAMINHO_COTAS = RAIZ / "data" / "raw" / "vetor" / "cotas-inundacao_sgb_atual_vetorial.gpkg"
CAMINHO_CNES = RAIZ / "data" / "raw" / "vetor" / "saude-cnes_datasus_atual_vetorial.gpkg"
CAMINHO_RASTER_USO_SOLO = RAIZ / "data" / "raw" / "raster" / "uso-solo_mapbiomas_2024_30m.tif"

CAMINHO_TABELA_POPULACAO = RAIZ / "data" / "processed" / "populacao-exposta-inundacao_por-cota.csv"
CAMINHO_INTERSECAO = RAIZ / "data" / "processed" / "setores-inundacao_intersecao.gpkg"
CAMINHO_TABELA_SAUDE = RAIZ / "data" / "processed" / "saude-estabelecimentos-exposicao-inundacao_por-cota.csv"

# Setor considerado "totalmente coberto" por uma cota se >= 99% da sua área
# está na interseção — uma folga pequena para diferenças de precisão de
# geometria (vértices arredondados, buffer(0) de correção topológica etc.),
# já que uma cobertura exatamente 100.000...% é rara na prática.
LIMIAR_COBERTURA_TOTAL = 0.99

# "Área Urbanizada" na legenda da Coleção 10 do MapBiomas (ver LEGENDA_CLASSES
# em scripts/download/uso-solo_mapbiomas.py). Usada como proxy de "onde a
# população se concentra" para o método de ponderação por uso do solo.
# "Mosaico de Usos" (21) foi deliberadamente excluído — ver docstring do
# módulo e a premissa 'exclusao_mosaico_de_usos_do_peso' abaixo.
CLASSE_AREA_URBANIZADA_MAPBIOMAS = 24

# Setor com menos que isso de % de área urbanizada (MapBiomas) é considerado
# "predominantemente rural" e usa o método de área-proporcional como
# fallback, em vez do método ponderado por uso do solo — abaixo desse
# patamar a proporção calculada sobre a área urbana já é pouco confiável
# (poucos pixels, alto ruído) e a premissa do método (gente concentrada em
# tecido urbano) não se aplica bem a população rural dispersa.
LIMIAR_PCT_AREA_URBANIZADA_SETOR = 0.05

# Categoria de estabelecimento usada para o cruzamento com as cotas: já é uma
# classificação agregada/normalizada do tipo_unidade (ver
# scripts/download/saude_cnes.py), mais estável para agrupar do que o
# tipo_unidade bruto (dezenas de rótulos granulares do cadastro do CNES).
COLUNA_TIPO_ESTABELECIMENTO = "tipo_unidade_categoria"

PREMISSAS_METODOLOGICAS = {
    "populacao_proporcional_a_area": (
        "Método 'area-proporcional': a população exposta a cada cota é "
        "estimada como populacao_total_do_setor * (área da interseção "
        "setor∩cota / área total do setor), isto é, assume-se distribuição "
        "UNIFORME da população dentro do polígono do setor censitário. É "
        "uma aproximação, não uma contagem real: o Censo não informa a "
        "distribuição interna da população num setor (ex.: um setor pode "
        "ter parte urbana densa e parte rural vazia), então a estimativa "
        "tende a superestimar a exposição em setores com grande área "
        "desabitada e subestimar onde a população real está concentrada "
        "perto do rio."
    ),
    "populacao_ponderada_uso_solo_urbanizado": (
        "Método 'ponderada_uso-solo': em vez da área geométrica total do "
        "setor, usa a área classificada como 'Área Urbanizada' (MapBiomas "
        "Coleção 10, class_id 24, ano 2024) como proxy de onde a população "
        "realmente mora: populacao_total_do_setor * (área urbanizada na "
        "interseção com a cota / área urbanizada total do setor). Deve "
        "aproximar melhor a exposição em setores com faixa urbana estreita "
        "perto do rio (a cheia cobre pouca área TOTAL do setor, mas boa "
        "parte da área efetivamente habitada). Calculado por zonal_stats "
        "(contagem categórica de pixels de 30m — ver rasterstats) sobre o "
        "raster já recortado/reprojetado por scripts/download/uso-solo_"
        "mapbiomas.py."
    ),
    "exclusao_mosaico_de_usos_do_peso": (
        "'Mosaico de Usos' (MapBiomas class_id 21) foi deliberadamente "
        "excluído do cálculo de área urbanizada, apesar de ser a classe "
        "mais extensa no município. É uma classe de mosaico agropecuário "
        "(lavoura/pastagem com estruturas rurais esparsas), não tecido "
        "residencial — incluí-la inundaria o sinal urbano com uma área "
        "enorme de terra agrícola sem relação com densidade populacional "
        "e contradiria o objetivo do método (achar onde a população "
        "realmente se concentra)."
    ),
    "fallback_setores_rurais_dispersos": (
        f"Setores com menos de {LIMIAR_PCT_AREA_URBANIZADA_SETOR * 100:.0f}% "
        "de sua área classificada como 'Área Urbanizada' são tratados como "
        "'predominantemente rurais': o método ponderado por uso do solo não "
        "se aplica bem a população rural dispersa (que o MapBiomas não "
        "classifica como pixel urbano), então esses setores usam o método "
        "de área-proporcional como fallback. A coluna "
        "metodo_estimativa_uso_solo (na interseção) e as colunas "
        "n_setores_metodo_uso_solo_urbanizado / n_setores_metodo_fallback_"
        "rural (na tabela por cota) indicam onde isso ocorreu; nesses "
        "setores populacao_estimada_ponderada_uso-solo é IGUAL a "
        "populacao_estimada_area-proporcional (não é uma segunda "
        "estimativa independente)."
    ),
    "limitacao_resolucao_pixel_mapbiomas": (
        "ACHADO EMPÍRICO, não só uma ressalva teórica: rodando o cruzamento, a "
        "maioria dos setores urbanos afetados tem 0% de área urbanizada "
        "dentro da própria interseção com a cota (ver "
        "n_setores_metodo_uso_solo_com_zero_area_urbana_na_intersecao no "
        "metadado), mesmo em setores com área urbanizada substancial no "
        "total. O MapBiomas tem resolução nativa de 30m (~27m após "
        "reprojeção para EPSG:31981, pixel de ~746 m² — ver scripts/"
        "download/uso-solo_mapbiomas.py); a faixa de terreno mais próxima "
        "do rio, onde incide a inundação, tende a ser classificada como "
        "'Campo Alagado'/'Rio, Lago e Oceano'/vegetação em vez de 'Área "
        "Urbanizada', mesmo havendo alguma ocupação real ali — por ser "
        "terreno baixo/periodicamente alagado (o que é justamente a razão "
        "de estar na cota de inundação) e por pixels de 30m raramente "
        "formarem um pixel 'urbano' pleno em faixas estreitas de ocupação "
        "ribeirinha. Isso faz o método ponderado por uso do solo tender a "
        "SUBESTIMAR a exposição real bem na faixa mais crítica (a mais "
        "próxima do rio), o oposto do que a hipótese inicial do método "
        "previa — ver 'populacao_ponderada_uso_solo_urbanizado' acima."
    ),
    "cotas_nao_estritamente_aninhadas": (
        "As 4 cotas de inundação (833/952/1205/1252cm) têm áreas "
        "crescentes mas não são um aninhamento perfeito entre si (checado "
        "geometricamente: ~97,6% da área da cota 833cm está contida na "
        "952cm, não 100%). Por isso cada cota é tratada de forma "
        "independente (overlay/spatial join separado), sem assumir que "
        "uma cota maior contém integralmente as menores."
    ),
    "correcao_topologica": (
        "4 das 21 feições originais de cotas-inundacao_sgb_atual_vetorial."
        "gpkg (fragmentos multipart de uma mesma cota) tinham geometria "
        "inválida (self-intersection), o que quebra a dissolução por cota "
        "e o overlay. Corrigidas com geometry.buffer(0) (técnica padrão "
        "para reparar geometria sem alterar a forma visível) antes de "
        "dissolver as cotas em um polígono por cota_cm."
    ),
    "indicadores_municipais_constantes": (
        "rendimento_medio_domiciliar_per_capita_reais_municipio, "
        "pct_domicilios_agua_inadequada_municipio e "
        "pct_domicilios_esgoto_inadequado_municipio vêm do SIDRA no nível "
        "de MUNICÍPIO (ver scripts/download/vulnerabilidade_censo.py) — "
        "são o mesmo valor em todos os setores, então a 'média' dessas "
        "colunas por cota é sempre igual ao valor municipal, não varia "
        "com a exposição. Só pct_populacao_0_a_4_anos e "
        "pct_populacao_60_anos_ou_mais variam por setor e são agregados "
        "de forma que reflita o perfil da população exposta (ver próxima "
        "premissa)."
    ),
    "media_ponderada_por_populacao_exposta": (
        "As médias de pct_populacao_0_a_4_anos e "
        "pct_populacao_60_anos_ou_mais por cota são ponderadas pela "
        "populacao_estimada_area-proporcional de cada setor (não uma "
        "média simples entre setores, e não pelo método ponderado por uso "
        "do solo), para refletir o perfil etário da população realmente "
        "exposta e não dar o mesmo peso a um setor pouco povoado e a um "
        "setor densamente povoado. Setores com a faixa etária suprimida "
        "por sigilo estatístico do IBGE (NaN — ver scripts/download/"
        "vulnerabilidade_censo.py) são excluídos só dessa média específica "
        "(não da população exposta total, que não depende dessa coluna)."
    ),
    "estabelecimentos_saude_ponto_no_poligono": (
        "Estabelecimentos de saúde (CNES) são geocodificados como ponto; "
        "'dentro da cota' é um teste ponto-em-polígono (predicate="
        "'within') contra o polígono dissolvido da cota. Um estabelecimento "
        "dentro de uma cota menor tende a aparecer também nas cotas "
        "maiores, já que as cotas maiores cobrem quase toda a área das "
        "menores (ver premissa de não aninhamento estrito acima) — a "
        "contagem de cada cota é sempre a exposição TOTAL àquele nível de "
        "cheia, não um incremento sobre a cota anterior."
    ),
}


def carregar_setores_com_indicadores() -> gpd.GeoDataFrame:
    """Carrega a malha de setores e junta os indicadores de vulnerabilidade (Censo 2022) pelo código do setor."""
    if not CAMINHO_SETORES.exists() or not CAMINHO_INDICADORES.exists():
        raise FileNotFoundError(
            f"{CAMINHO_SETORES} e/ou {CAMINHO_INDICADORES} não encontrados. "
            "Rode primeiro: python scripts/download/vulnerabilidade_censo.py"
        )

    gdf_setores = gpd.read_file(CAMINHO_SETORES)
    if gdf_setores.crs.to_string() != CRS_PADRAO:
        gdf_setores = gdf_setores.to_crs(CRS_PADRAO)

    df_indicadores = pd.read_csv(CAMINHO_INDICADORES, dtype={"cd_setor": str})

    gdf = gdf_setores.merge(df_indicadores, left_on="CD_SETOR", right_on="cd_setor", how="left")
    n_sem_indicador = gdf["populacao_total"].isna().sum()
    if n_sem_indicador:
        logger.warning(
            "%d de %d setores ficaram sem indicador do Censo após o join por CD_SETOR — "
            "serão tratados como população 0 na estimativa de exposição.",
            n_sem_indicador, len(gdf),
        )
        gdf["populacao_total"] = gdf["populacao_total"].fillna(0)

    # Área do setor recalculada da própria geometria (m²), em vez de usar o
    # atributo AREA_KM2 da malha do IBGE — garante consistência exata com a
    # área que o overlay vai calcular para a interseção (mesmo polígono,
    # mesmo CRS), evitando um denominador ligeiramente diferente do
    # numerador na hora de calcular o % de área coberta.
    gdf["area_setor_m2"] = gdf.geometry.area

    logger.info("Setores censitários carregados: %d (com indicadores do Censo 2022 juntados)", len(gdf))
    return gdf


def obter_pixel_area_m2(caminho_raster: Path) -> float:
    with rasterio.open(caminho_raster) as src:
        return abs(src.transform.a * src.transform.e)


def calcular_area_urbanizada_km2(geometrias: gpd.GeoSeries, caminho_raster: Path, pixel_area_m2: float) -> pd.Series:
    """Área (km²) de pixels classificados como 'Área Urbanizada' (MapBiomas) dentro de cada geometria."""
    stats = zonal_stats(geometrias, str(caminho_raster), categorical=True)
    contagens = pd.Series(
        [stats_poligono.get(CLASSE_AREA_URBANIZADA_MAPBIOMAS, 0) for stats_poligono in stats],
        index=geometrias.index, dtype=float,
    )
    return contagens * pixel_area_m2 / 1e6


def adicionar_area_urbanizada_setor(gdf_setores: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Calcula, por setor, a área urbanizada (MapBiomas 2024) e o % que ela representa da área total do setor."""
    if not CAMINHO_RASTER_USO_SOLO.exists():
        raise FileNotFoundError(
            f"{CAMINHO_RASTER_USO_SOLO} não encontrado. Rode primeiro: python scripts/download/uso-solo_mapbiomas.py"
        )

    gdf_setores = gdf_setores.copy()
    pixel_area_m2 = obter_pixel_area_m2(CAMINHO_RASTER_USO_SOLO)
    gdf_setores["area_urbanizada_setor_km2"] = calcular_area_urbanizada_km2(
        gdf_setores.geometry, CAMINHO_RASTER_USO_SOLO, pixel_area_m2
    )
    gdf_setores["pct_area_urbanizada_setor"] = (
        gdf_setores["area_urbanizada_setor_km2"] / (gdf_setores["area_setor_m2"] / 1e6)
    ).fillna(0)

    n_rural = int((gdf_setores["pct_area_urbanizada_setor"] < LIMIAR_PCT_AREA_URBANIZADA_SETOR).sum())
    logger.info(
        "Área urbanizada (MapBiomas 2024) calculada por setor — %d de %d setores são "
        "'predominantemente rurais' (< %.0f%% de área urbanizada) e usarão a área-proporcional "
        "como fallback no método ponderado por uso do solo.",
        n_rural, len(gdf_setores), LIMIAR_PCT_AREA_URBANIZADA_SETOR * 100,
    )
    return gdf_setores


def carregar_cotas_dissolvidas() -> gpd.GeoDataFrame:
    """Carrega as cotas de inundação do SGB, corrige geometria inválida e dissolve em 1 polígono por cota."""
    if not CAMINHO_COTAS.exists():
        raise FileNotFoundError(f"{CAMINHO_COTAS} não encontrado. Rode primeiro: python scripts/download/hidrologia_sgb.py")

    gdf = gpd.read_file(CAMINHO_COTAS)
    if gdf.crs.to_string() != CRS_PADRAO:
        gdf = gdf.to_crs(CRS_PADRAO)

    n_invalidas = (~gdf.geometry.is_valid).sum()
    if n_invalidas:
        logger.warning(
            "%d de %d feições de cota de inundação com geometria inválida — corrigindo com buffer(0).",
            n_invalidas, len(gdf),
        )
        gdf["geometry"] = gdf.geometry.buffer(0)

    # tr_anos vem do dado de origem com imprecisão de ponto flutuante (ex.:
    # 1.29999995 em vez de 1.3, já presente em cotas-inundacao_sgb_atual_
    # vetorial.gpkg) — arredondado para 1 casa decimal só para exibição nas
    # saídas deste script, sem alterar o dado bruto.
    gdf["tr_anos"] = gdf["tr_anos"].round(1)
    gdf_dissolvido = gdf.dissolve(by=["cota_cm", "tr_anos"], as_index=False)[["cota_cm", "tr_anos", "geometry"]]
    gdf_dissolvido = gdf_dissolvido.sort_values("cota_cm").reset_index(drop=True)

    logger.info(
        "Cotas de inundação carregadas: %d (%s cm)",
        len(gdf_dissolvido), ", ".join(str(c) for c in gdf_dissolvido["cota_cm"]),
    )
    return gdf_dissolvido


def carregar_cnes() -> gpd.GeoDataFrame:
    """Carrega os estabelecimentos de saúde (CNES) já geocodificados."""
    if not CAMINHO_CNES.exists():
        raise FileNotFoundError(f"{CAMINHO_CNES} não encontrado. Rode primeiro: python scripts/download/saude_cnes.py")

    gdf = gpd.read_file(CAMINHO_CNES)
    if gdf.crs.to_string() != CRS_PADRAO:
        gdf = gdf.to_crs(CRS_PADRAO)

    logger.info("Estabelecimentos de saúde (CNES) carregados: %d", len(gdf))
    return gdf


def calcular_exposicao_por_setor(gdf_setores: gpd.GeoDataFrame, gdf_cotas: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Overlay (interseção) setor x cota e as duas estimativas de população exposta (área-proporcional e ponderada por uso do solo)."""
    colunas_setor = [
        "CD_SETOR", "SITUACAO", "area_setor_m2", "area_urbanizada_setor_km2", "pct_area_urbanizada_setor",
        "populacao_total", "pct_populacao_0_a_4_anos", "pct_populacao_60_anos_ou_mais",
        "rendimento_medio_domiciliar_per_capita_reais_municipio",
        "pct_domicilios_agua_inadequada_municipio", "pct_domicilios_esgoto_inadequado_municipio",
        "geometry",
    ]
    intersecao = gpd.overlay(
        gdf_setores[colunas_setor], gdf_cotas[["cota_cm", "tr_anos", "geometry"]],
        how="intersection", keep_geom_type=True,
    )

    # --- Método 1: área-proporcional (distribuição uniforme no setor) ---
    intersecao["area_intersecao_m2"] = intersecao.geometry.area
    intersecao["pct_area_coberta"] = (intersecao["area_intersecao_m2"] / intersecao["area_setor_m2"]).clip(upper=1.0)
    intersecao["populacao_estimada_area-proporcional"] = intersecao["populacao_total"] * intersecao["pct_area_coberta"]

    # --- Método 2: ponderado pela área urbanizada (MapBiomas 2024) ---
    pixel_area_m2 = obter_pixel_area_m2(CAMINHO_RASTER_USO_SOLO)
    intersecao["area_urbanizada_intersecao_km2"] = calcular_area_urbanizada_km2(
        intersecao.geometry, CAMINHO_RASTER_USO_SOLO, pixel_area_m2
    )
    intersecao["pct_area_urbanizada_coberta"] = np.where(
        intersecao["area_urbanizada_setor_km2"] > 0,
        (intersecao["area_urbanizada_intersecao_km2"] / intersecao["area_urbanizada_setor_km2"]).clip(upper=1.0),
        np.nan,
    )
    intersecao["metodo_estimativa_uso_solo"] = np.where(
        intersecao["pct_area_urbanizada_setor"] >= LIMIAR_PCT_AREA_URBANIZADA_SETOR,
        "uso_solo_urbanizado", "area_proporcional_fallback",
    )
    intersecao["populacao_estimada_ponderada_uso-solo"] = np.where(
        intersecao["metodo_estimativa_uso_solo"] == "uso_solo_urbanizado",
        intersecao["populacao_total"] * intersecao["pct_area_urbanizada_coberta"].fillna(0),
        intersecao["populacao_estimada_area-proporcional"],
    )

    intersecao["area_setor_km2"] = intersecao["area_setor_m2"] / 1e6
    intersecao["area_intersecao_km2"] = intersecao["area_intersecao_m2"] / 1e6
    intersecao = intersecao.drop(columns=["area_setor_m2", "area_intersecao_m2"])

    n_uso_solo = (intersecao.drop_duplicates("CD_SETOR")["metodo_estimativa_uso_solo"] == "uso_solo_urbanizado").sum()
    logger.info(
        "Interseção setor x cota: %d combinações (%d setores distintos afetados; %d usam o método ponderado "
        "por uso do solo, %d usam área-proporcional como fallback rural)",
        len(intersecao), intersecao["CD_SETOR"].nunique(), n_uso_solo,
        intersecao["CD_SETOR"].nunique() - n_uso_solo,
    )
    return intersecao


def media_ponderada(valores: pd.Series, pesos: pd.Series) -> float:
    """Média ponderada, ignorando setores com valor ausente (sigilo estatístico do IBGE em faixas etárias).

    Sem isso, um único setor com NaN (ex.: faixa etária suprimida por sigilo,
    ver scripts/download/vulnerabilidade_censo.py) propagaria NaN para a
    cota inteira via np.average. Retorna NaN só se não sobrar nenhum setor
    válido ou se a soma dos pesos restantes for zero (nenhuma população
    exposta nos setores com dado disponível).
    """
    mascara_valido = valores.notna()
    valores_validos, pesos_validos = valores[mascara_valido], pesos[mascara_valido]
    if pesos_validos.sum() <= 0:
        return float("nan")
    return float(np.average(valores_validos, weights=pesos_validos))


def montar_tabela_populacao_por_cota(intersecao: gpd.GeoDataFrame, populacao_total_municipio: float) -> pd.DataFrame:
    """Consolida a interseção numa linha por cota: as duas estimativas de população, setores afetados e médias de vulnerabilidade."""
    linhas = []
    for (cota_cm, tr_anos), grupo in intersecao.groupby(["cota_cm", "tr_anos"], sort=True):
        pop_area_proporcional = grupo["populacao_estimada_area-proporcional"].sum()
        pop_uso_solo = grupo["populacao_estimada_ponderada_uso-solo"].sum()
        n_cobertura_total = int((grupo["pct_area_coberta"] >= LIMIAR_COBERTURA_TOTAL).sum())
        setores_por_metodo = grupo.drop_duplicates("CD_SETOR")["metodo_estimativa_uso_solo"].value_counts()

        linhas.append({
            "cota_cm": cota_cm,
            "tr_anos": tr_anos,
            "n_setores_afetados": int(grupo["CD_SETOR"].nunique()),
            "n_setores_cobertura_total": n_cobertura_total,
            "n_setores_cobertura_parcial": int(grupo["CD_SETOR"].nunique()) - n_cobertura_total,
            "n_setores_metodo_uso_solo_urbanizado": int(setores_por_metodo.get("uso_solo_urbanizado", 0)),
            "n_setores_metodo_fallback_rural": int(setores_por_metodo.get("area_proporcional_fallback", 0)),
            "populacao_estimada_area-proporcional": round(pop_area_proporcional, 1),
            "populacao_estimada_ponderada_uso-solo": round(pop_uso_solo, 1),
            "dif_pct_uso-solo_vs_area-proporcional": (
                round(100 * (pop_uso_solo - pop_area_proporcional) / pop_area_proporcional, 2)
                if pop_area_proporcional else None
            ),
            "pct_populacao_municipio_exposta_area-proporcional": (
                round(100 * pop_area_proporcional / populacao_total_municipio, 2) if populacao_total_municipio else None
            ),
            "pct_populacao_municipio_exposta_ponderada_uso-solo": (
                round(100 * pop_uso_solo / populacao_total_municipio, 2) if populacao_total_municipio else None
            ),
            "pct_populacao_0_a_4_anos_media_pop_exposta": round(
                media_ponderada(grupo["pct_populacao_0_a_4_anos"], grupo["populacao_estimada_area-proporcional"]), 2
            ),
            "pct_populacao_60_anos_ou_mais_media_pop_exposta": round(
                media_ponderada(grupo["pct_populacao_60_anos_ou_mais"], grupo["populacao_estimada_area-proporcional"]), 2
            ),
            "rendimento_medio_domiciliar_per_capita_reais_municipio": grupo[
                "rendimento_medio_domiciliar_per_capita_reais_municipio"
            ].iloc[0],
            "pct_domicilios_agua_inadequada_municipio": grupo["pct_domicilios_agua_inadequada_municipio"].iloc[0],
            "pct_domicilios_esgoto_inadequado_municipio": grupo["pct_domicilios_esgoto_inadequado_municipio"].iloc[0],
        })

    tabela = pd.DataFrame(linhas).sort_values("cota_cm").reset_index(drop=True)
    return tabela


def calcular_estabelecimentos_por_cota(gdf_cnes: gpd.GeoDataFrame, gdf_cotas: gpd.GeoDataFrame) -> pd.DataFrame:
    """Estabelecimentos de saúde (ponto) dentro de cada cota (polígono), por tipo de unidade."""
    cruzamento = gpd.sjoin(
        gdf_cnes[["cnes", COLUNA_TIPO_ESTABELECIMENTO, "geometry"]],
        gdf_cotas[["cota_cm", "tr_anos", "geometry"]],
        how="inner", predicate="within",
    )

    tabela = (
        cruzamento.groupby(["cota_cm", "tr_anos", COLUNA_TIPO_ESTABELECIMENTO])
        .size()
        .reset_index(name="n_estabelecimentos")
        .sort_values(["cota_cm", "n_estabelecimentos"], ascending=[True, False])
        .reset_index(drop=True)
    )
    return tabela


def montar_metadados(descricao: str, campos_especificos: dict) -> dict:
    return {
        "fontes": [
            "IBGE — Malha de Setores Censitários (Divisões Intramunicipais), Censo 2022",
            "IBGE — Censo 2022: Agregados por Setores Censitários + API de Agregados/SIDRA (via scripts/download/vulnerabilidade_censo.py)",
            "SGB (Serviço Geológico do Brasil) — cotas de inundação do rio Uruguai (via scripts/download/hidrologia_sgb.py)",
            "CNES/DATASUS — estabelecimentos de saúde (via scripts/download/saude_cnes.py)",
            "MapBiomas Brasil — Coleção 10, uso e cobertura do solo 2024 (via scripts/download/uso-solo_mapbiomas.py)",
        ],
        "descricao": descricao,
        "script_gerador": "scripts/processamento/vulnerabilidade_inundacao.py",
        "crs": CRS_PADRAO,
        "premissas_metodologicas": PREMISSAS_METODOLOGICAS,
        "data_processamento": datetime.now(timezone.utc).isoformat(),
        **campos_especificos,
    }


def montar_comparacao_cota_833(tabela_populacao: pd.DataFrame, intersecao: gpd.GeoDataFrame) -> dict | None:
    """Compara os dois métodos de estimativa para a cota mais frequente (833cm, TR 1.3 anos), para os metadados."""
    linhas_833 = tabela_populacao[tabela_populacao["cota_cm"] == 833]
    if linhas_833.empty:
        return None
    linha = linhas_833.iloc[0]

    grupo_833 = intersecao[intersecao["cota_cm"] == 833]
    grupo_uso_solo = grupo_833[grupo_833["metodo_estimativa_uso_solo"] == "uso_solo_urbanizado"]
    n_uso_solo = len(grupo_uso_solo)
    n_zero_cobertura_urbana = int((grupo_uso_solo["pct_area_urbanizada_coberta"] == 0).sum())

    return {
        "cota_cm": 833,
        "tr_anos": float(linha["tr_anos"]),
        "populacao_estimada_area-proporcional": float(linha["populacao_estimada_area-proporcional"]),
        "populacao_estimada_ponderada_uso-solo": float(linha["populacao_estimada_ponderada_uso-solo"]),
        "diferenca_percentual_uso-solo_vs_area-proporcional": linha["dif_pct_uso-solo_vs_area-proporcional"],
        "n_setores_metodo_uso_solo_urbanizado": int(linha["n_setores_metodo_uso_solo_urbanizado"]),
        "n_setores_metodo_fallback_rural": int(linha["n_setores_metodo_fallback_rural"]),
        "n_setores_metodo_uso_solo_com_zero_area_urbana_na_intersecao": n_zero_cobertura_urbana,
        "achado_empirico_e_interpretacao": (
            f"A hipótese inicial era que o método ponderado por uso do solo daria uma estimativa "
            f"MAIOR que a área-proporcional nos setores urbanos ribeirinhos. O resultado observado "
            f"foi o OPOSTO: {linha['populacao_estimada_ponderada_uso-solo']:.0f} pessoas estimadas "
            f"pelo método ponderado vs. {linha['populacao_estimada_area-proporcional']:.0f} pelo "
            f"método original ({linha['dif_pct_uso-solo_vs_area-proporcional']:.1f}%). Motivo: dos "
            f"{n_uso_solo} setores que usaram o método ponderado por uso do solo (não caíram no "
            f"fallback rural), {n_zero_cobertura_urbana} têm 0% de área urbanizada dentro da própria "
            "interseção com a cota — a faixa de terreno mais próxima do rio, onde a inundação incide, "
            "quase não tem pixel classificado como 'Área Urbanizada' pelo MapBiomas, mesmo em setores "
            "com bastante área urbanizada no total. Duas explicações não excludentes: (a) padrão real "
            "de ocupação, com o tecido urbano em cota mais alta, afastado da faixa mais sujeita a "
            "cheias frequentes; (b) limitação de classificação — terreno baixo/periodicamente alagado "
            "tende a virar 'Campo Alagado'/'Rio, Lago e Oceano' no MapBiomas em vez de 'Área "
            "Urbanizada', mesmo havendo alguma ocupação real ali. Na prática, os dois métodos "
            "funcionam melhor como limites de um intervalo de incerteza — área-proporcional tende a "
            "SUPERESTIMAR e uso-solo tende a SUBESTIMAR a exposição real na faixa mais próxima do rio "
            "— do que como um substituindo o outro."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Cruza setores censitários (vulnerabilidade) e estabelecimentos de saúde com cotas de inundação do SGB."
    )
    parser.add_argument("--forcar", action="store_true", help="Reprocessa mesmo se as saídas já existirem")
    args = parser.parse_args()

    saidas = [CAMINHO_TABELA_POPULACAO, CAMINHO_INTERSECAO, CAMINHO_TABELA_SAUDE]
    if all(caminho.exists() and caminho.with_suffix(".json").exists() for caminho in saidas) and not args.forcar:
        logger.info("Saídas de vulnerabilidade x inundação já existem — nada a fazer (use --forcar para refazer).")
        return

    gdf_setores = carregar_setores_com_indicadores()
    gdf_setores = adicionar_area_urbanizada_setor(gdf_setores)
    gdf_cotas = carregar_cotas_dissolvidas()
    gdf_cnes = carregar_cnes()

    intersecao = calcular_exposicao_por_setor(gdf_setores, gdf_cotas)
    populacao_total_municipio = gdf_setores["populacao_total"].sum()
    tabela_estabelecimentos = calcular_estabelecimentos_por_cota(gdf_cnes, gdf_cotas)
    n_estabelecimentos_por_cota = tabela_estabelecimentos.groupby("cota_cm")["n_estabelecimentos"].sum()

    tabela_populacao = montar_tabela_populacao_por_cota(intersecao, populacao_total_municipio)
    tabela_populacao["n_estabelecimentos_saude_total"] = (
        tabela_populacao["cota_cm"].map(n_estabelecimentos_por_cota).fillna(0).astype(int)
    )

    CAMINHO_TABELA_POPULACAO.parent.mkdir(parents=True, exist_ok=True)
    tabela_populacao.to_csv(CAMINHO_TABELA_POPULACAO, index=False, encoding="utf-8")
    logger.info("Tabela consolidada por cota salva em %s", CAMINHO_TABELA_POPULACAO)

    colunas_intersecao_saida = [
        "CD_SETOR", "SITUACAO", "cota_cm", "tr_anos", "area_setor_km2", "area_intersecao_km2",
        "pct_area_coberta", "area_urbanizada_setor_km2", "area_urbanizada_intersecao_km2",
        "pct_area_urbanizada_coberta", "metodo_estimativa_uso_solo",
        "populacao_total", "populacao_estimada_area-proporcional", "populacao_estimada_ponderada_uso-solo",
        "pct_populacao_0_a_4_anos", "pct_populacao_60_anos_ou_mais",
        "rendimento_medio_domiciliar_per_capita_reais_municipio",
        "pct_domicilios_agua_inadequada_municipio", "pct_domicilios_esgoto_inadequado_municipio",
        "geometry",
    ]
    intersecao[colunas_intersecao_saida].to_file(CAMINHO_INTERSECAO, driver="GPKG", layer="setores_inundacao_intersecao")
    logger.info("Geometria de interseção salva em %s (%d feições)", CAMINHO_INTERSECAO, len(intersecao))

    CAMINHO_TABELA_SAUDE.parent.mkdir(parents=True, exist_ok=True)
    tabela_estabelecimentos.to_csv(CAMINHO_TABELA_SAUDE, index=False, encoding="utf-8")
    logger.info("Tabela de estabelecimentos de saúde por cota salva em %s", CAMINHO_TABELA_SAUDE)

    comparacao_833 = montar_comparacao_cota_833(tabela_populacao, intersecao)

    metadados_populacao = montar_metadados(
        "Tabela consolidada, uma linha por cota de inundação do SGB: as duas estimativas de "
        "população exposta (área-proporcional e ponderada por uso do solo urbanizado), número de "
        "setores afetados (total/parcialmente cobertos, e por método de estimativa usado), médias "
        "de indicadores de vulnerabilidade nos setores afetados e total de estabelecimentos de "
        "saúde dentro da cota.",
        {
            "populacao_total_municipio_referencia": int(populacao_total_municipio),
            "n_cotas": len(tabela_populacao),
            "limiar_cobertura_total": LIMIAR_COBERTURA_TOTAL,
            "limiar_pct_area_urbanizada_setor": LIMIAR_PCT_AREA_URBANIZADA_SETOR,
            "comparacao_metodos_cota_833cm": comparacao_833,
        },
    )
    CAMINHO_TABELA_POPULACAO.with_suffix(".json").write_text(
        json.dumps(metadados_populacao, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )

    metadados_intersecao = montar_metadados(
        "Geometria de cada interseção entre um setor censitário e uma cota de inundação do SGB, "
        "com as duas estimativas de população exposta (área-proporcional e ponderada por uso do "
        "solo), o método efetivamente usado por setor e os indicadores de vulnerabilidade daquele "
        "setor — uma feição por combinação setor x cota, útil para mapear a exposição no espaço.",
        {"n_feicoes": len(intersecao), "n_setores_distintos_afetados": int(intersecao["CD_SETOR"].nunique())},
    )
    CAMINHO_INTERSECAO.with_suffix(".json").write_text(
        json.dumps(metadados_intersecao, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )

    metadados_saude = montar_metadados(
        "Contagem de estabelecimentos de saúde (CNES) dentro de cada cota de inundação do SGB, "
        f"por '{COLUNA_TIPO_ESTABELECIMENTO}' (categoria agregada do tipo de unidade) — tabela longa "
        "(uma linha por combinação cota x tipo).",
        {
            "coluna_tipo_estabelecimento": COLUNA_TIPO_ESTABELECIMENTO,
            "n_estabelecimentos_total_cnes": len(gdf_cnes),
        },
    )
    CAMINHO_TABELA_SAUDE.with_suffix(".json").write_text(
        json.dumps(metadados_saude, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )

    logger.info("Concluído.")


if __name__ == "__main__":
    main()
