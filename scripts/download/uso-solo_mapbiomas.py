"""
Baixa e recorta a série temporal de uso e cobertura do solo do MapBiomas
Brasil para a área de estudo do projeto. Gera, para cada ano processado:

    data/raw/raster/uso-solo_mapbiomas_<ano>_30m.tif
    data/raw/raster/uso-solo_mapbiomas_<ano>_30m.json

E, ao final, agrega os metadados de todos os anos disponíveis em disco numa
tabela resumo para visualizar a evolução das classes ao longo do tempo:

    data/processed/uso-solo_mapbiomas_serie-temporal_area-por-classe.csv

Investigação de fonte (feita antes de codificar)
--------------------------------------------------
1. Coleção usada: Collection 10 do MapBiomas Brasil (o site chama de
   "Coleção 10.1"), cobrindo 1985–2024. Confirmado em
   https://storage.googleapis.com/mapbiomas-public/initiatives/brasil/
   collection_10/lulc/coverage/brazil_coverage_2024.tif (HTTP 200, GeoTIFF,
   last-modified 2025-08-13). Coleções antigas (ex. collection_9, pasta
   "lclu" em vez de "lulc") continuam acessíveis, mas a 10 é a mais recente.

2. Earth Engine vs. Cloud Storage: o bucket público
   storage.googleapis.com/mapbiomas-public não exige autenticação (HTTP
   simples), ao contrário do Earth Engine (requer conta/token). Optou-se
   pelo bucket público.

3. Recorte por bioma/UF: listando o bucket
   (initiatives/brasil/collection_10/lulc/) só existem as pastas
   "ATBDs/", "coverage/" (raster nacional por ano), "maps/" e
   "statistics/" — NÃO há GeoTIFF pré-recortado por bioma (Pampa) ou UF
   (RS) disponível para download direto. O recorte territorial oficial do
   MapBiomas é feito via toolkit do Google Earth Engine, que exige
   autenticação — por isso não foi usado.

4. Estratégia adotada para evitar baixar ~750-800MB do Brasil inteiro POR
   ANO: o GeoTIFF nacional é um Cloud-Optimized GeoTIFF (tiled 256x256,
   LZW) e o bucket aceita HTTP Range requests. Usamos o driver `/vsicurl/`
   do GDAL para abrir o raster remoto e ler, via `recortar_raster()`,
   apenas a janela de pixels que cobre a área de estudo — o GDAL faz
   requisições HTTP parciais (range) e baixa só os blocos necessários
   (testado: ~1-2s e poucos MB por ano para o município, em vez do arquivo
   nacional inteiro). A mesma estratégia é repetida para cada ano da
   série, só trocando o ano na URL.

5. Legenda de classes: hardcoded a partir do CSV oficial "Códigos das
   classes da legenda da Coleção 10" (LEGENDA_CLASSES abaixo), baixado uma
   única vez de brasil.mapbiomas.org e conferido manualmente — é uma
   tabela de referência pequena e estável (mesmo espírito das camadas
   fixas em hidrologia_sgb.py), então não é buscada via rede a cada
   execução. A legenda vale para toda a série 1985-2024 (Coleção 10).

Série temporal
---------------
Por padrão, processa os anos a cada 5 anos desde o início da coleção até o
mais recente, sempre incluindo o ano mais recente mesmo que não caia no
passo de 5 (ANOS_SERIE_DEFAULT, construída por construir_anos_serie()):
1985, 1990, 1995, 2000, 2005, 2010, 2015, 2020, 2024.

Use --anos para processar um subconjunto específico (ex.: adicionar um ano
novo sem reprocessar a série inteira, ou forçar o refazimento de só alguns
anos com --forcar).

Idempotência
------------
Cada ano é independente: se o raster e o metadado daquele ano já existirem,
não é baixado/recortado de novo, a menos que --forcar seja usado (nesse
caso, combine com --anos para forçar só os anos desejados, não a série
toda).

A tabela resumo da série temporal é sempre regerada ao final da execução, a
partir de TODOS os metadados de ano encontrados em data/raw/raster/ (não
só os anos processados nesta chamada) — assim ela reflete o estado atual
completo dos dados baixados.

Uso:
    python scripts/download/uso-solo_mapbiomas.py
    python scripts/download/uso-solo_mapbiomas.py --anos 2023
    python scripts/download/uso-solo_mapbiomas.py --anos 1985,2024 --forcar
"""

import argparse
import json
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
import requests

sys.path.append(str(Path(__file__).resolve().parents[1] / "utils"))
from recorte_municipio import CRS_PADRAO, carregar_area_estudo, recortar_raster  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

CODIGO_IBGE_DEFAULT = "4322400"  # Uruguaiana, RS
COLECAO = "collection_10"  # MapBiomas Brasil — "Coleção 10.1" no site, pasta collection_10 no bucket
ANO_MIN, ANO_MAX = 1985, 2024
PASSO_SERIE_ANOS = 5

HEADERS = {"User-Agent": "Mozilla/5.0 (compativel; script-pesquisa-ClimaPampa/1.0)"}

URL_BASE = f"https://storage.googleapis.com/mapbiomas-public/initiatives/brasil/{COLECAO}/lulc/coverage"
URL_LEGENDA = "https://brasil.mapbiomas.org/wp-content/uploads/sites/4/2025/08/Codigos-da-legenda-colecao-10.zip"
URL_COLECAO_INFO = "https://brasil.mapbiomas.org/en/map/colecao-10/"

CAMINHO_SAIDA_DIR = Path(__file__).resolve().parents[2] / "data" / "raw" / "raster"
CAMINHO_TABELA_SERIE_DEFAULT = (
    Path(__file__).resolve().parents[2] / "data" / "processed" / "uso-solo_mapbiomas_serie-temporal_area-por-classe.csv"
)
PADRAO_NOME_RASTER = re.compile(r"^uso-solo_mapbiomas_(\d{4})_30m\.json$")

# Legenda oficial da Coleção 10 (Class_ID -> nomes EN/PT), extraída do CSV em URL_LEGENDA.
# Tabela de referência estável (vale para toda a série 1985-2024) — hardcoded
# para não depender de rede a cada execução.
LEGENDA_CLASSES = {
    1: ("Forest", "Floresta"),
    3: ("Forest Formation", "Formação Florestal"),
    4: ("Savanna Formation", "Formação Savânica"),
    5: ("Mangrove", "Mangue"),
    6: ("Floodable Forest", "Floresta Alagável"),
    49: ("Wooded Sandbank Vegetation", "Restinga Arbórea"),
    10: ("Herbaceous and Shrubby Vegetation", "Vegetação Herbácea e Arbustiva"),
    11: ("Wetland", "Campo Alagado e Área Pantanosa"),
    12: ("Grassland", "Formação Campestre"),
    32: ("Hypersaline Tidal Flat", "Apicum"),
    29: ("Rocky Outcrop", "Afloramento Rochoso"),
    50: ("Herbaceous Sandbank Vegetation", "Restinga Herbácea"),
    14: ("Farming", "Agropecuária"),
    15: ("Pasture", "Pastagem"),
    18: ("Agriculture", "Agricultura"),
    19: ("Temporary Crop", "Lavoura Temporária"),
    39: ("Soybean", "Soja"),
    20: ("Sugar cane", "Cana"),
    40: ("Rice", "Arroz"),
    62: ("Cotton (beta)", "Algodão (beta)"),
    41: ("Other Temporary Crops", "Outras Lavouras Temporárias"),
    36: ("Perennial Crop", "Lavoura Perene"),
    46: ("Coffee", "Café"),
    47: ("Citrus", "Citrus"),
    35: ("Palm Oil", "Dendê"),
    48: ("Other Perennial Crops", "Outras Lavouras Perenes"),
    9: ("Forest Plantation", "Silvicultura"),
    21: ("Mosaic of Uses", "Mosaico de Usos"),
    22: ("Non vegetated area", "Área não Vegetada"),
    23: ("Beach, Dune and Sand Spot", "Praia, Duna e Areal"),
    24: ("Urban Area", "Área Urbanizada"),
    30: ("Mining", "Mineração"),
    75: ("Photovoltaic Power Plant (beta)", "Usina Fotovoltaica (beta)"),
    25: ("Other non Vegetated Areas", "Outras Áreas não Vegetadas"),
    26: ("Water", "Corpo D'água"),
    33: ("River, Lake and Ocean", "Rio, Lago e Oceano"),
    31: ("Aquaculture", "Aquicultura"),
    27: ("Not Observed", "Não observado"),
}
# Valor 0 não é uma classe MapBiomas (a legenda começa em 1) — é o preenchimento
# padrão do rasterio.mask fora do polígono do município, mas dentro do bounding
# box recortado (o município não é retangular). Tratado à parte nas estatísticas.
CODIGO_FORA_POLIGONO = 0


def construir_anos_serie(ano_inicio: int = ANO_MIN, ano_fim: int = ANO_MAX, passo: int = PASSO_SERIE_ANOS) -> list[int]:
    """Anos a cada `passo`, de `ano_inicio` até `ano_fim`, sempre incluindo `ano_fim`."""
    anos = set(range(ano_inicio, ano_fim, passo))
    anos.add(ano_fim)
    return sorted(anos)


ANOS_SERIE_DEFAULT = construir_anos_serie()


def parse_anos(valor: str) -> list[int]:
    """Converte '1985,1990,2024' em [1985, 1990, 2024], validando o formato."""
    try:
        anos = sorted({int(item.strip()) for item in valor.split(",") if item.strip()})
    except ValueError as erro:
        raise argparse.ArgumentTypeError(
            f"--anos deve ser uma lista de anos separados por vírgula (ex.: 1985,1990,2024): {erro}"
        ) from erro
    if not anos:
        raise argparse.ArgumentTypeError("--anos não pode ser vazio")
    return anos


def montar_url(ano: int) -> str:
    return f"{URL_BASE}/brazil_coverage_{ano}.tif"


def verificar_ano_disponivel(ano: int, sessao: requests.Session) -> None:
    """Confirma via HEAD que o raster do ano/coleção pedido existe no bucket público."""
    if not (ANO_MIN <= ano <= ANO_MAX):
        raise ValueError(f"Ano {ano} fora do intervalo disponível na {COLECAO} ({ANO_MIN}-{ANO_MAX}).")
    url = montar_url(ano)
    try:
        resposta = sessao.head(url, headers=HEADERS, timeout=30)
    except requests.RequestException as erro:
        raise RuntimeError(f"Falha ao verificar disponibilidade do raster MapBiomas em {url}: {erro}") from erro
    if resposta.status_code != 200:
        raise RuntimeError(
            f"Raster MapBiomas não encontrado em {url} (HTTP {resposta.status_code}) — "
            "a coleção/ano pode ter mudado de local; confira https://brasil.mapbiomas.org/downloads/"
        )
    logger.info(
        "Raster confirmado: %s (%.1f MB no bucket, ano %d, %s)",
        url, int(resposta.headers.get("content-length", 0)) / 1e6, ano, COLECAO,
    )


def obter_nome_municipio(codigo_ibge: str, sessao: requests.Session) -> str:
    """Nome do município via API do IBGE — usado só para os metadados (a geometria vem sempre de area_estudo.geojson)."""
    url = f"https://servicodados.ibge.gov.br/api/v1/localidades/municipios/{codigo_ibge}"
    try:
        resposta = sessao.get(url, headers=HEADERS, timeout=30)
        resposta.raise_for_status()
        return resposta.json()["nome"]
    except requests.RequestException as erro:
        logger.warning("Não foi possível obter o nome do município (código %s) no IBGE: %s", codigo_ibge, erro)
        return ""


def baixar_e_recortar(ano: int, caminho_saida: Path) -> None:
    """Recorta a área de estudo diretamente do raster remoto via streaming /vsicurl/.

    Usa recortar_raster() (scripts/utils/recorte_municipio.py) para o recorte
    (mask+crop) e reprojeção ao CRS padrão — igual às demais fontes raster do
    projeto. Não baixa o arquivo nacional inteiro: o GDAL só busca, via HTTP
    range requests, os blocos que cobrem a área de estudo.
    """
    url_vsicurl = "/vsicurl/" + montar_url(ano)
    area_estudo = carregar_area_estudo()

    logger.info("Recortando uso do solo MapBiomas %d pela área de estudo (streaming, sem baixar o raster nacional)...", ano)
    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR"):
        try:
            recortar_raster(url_vsicurl, caminho_saida, area_estudo)
        except Exception as erro:
            raise RuntimeError(f"Falha ao recortar o raster MapBiomas remoto ({url_vsicurl}): {erro}") from erro

    logger.info("Raster recortado e reprojetado (%s) salvo em %s", CRS_PADRAO, caminho_saida)


def calcular_classes_presentes(caminho_raster: Path) -> dict:
    """Lê o raster já recortado/reprojetado e calcula área (km²) por classe MapBiomas."""
    with rasterio.open(caminho_raster) as src:
        dados = src.read(1)
        pixel_area_m2 = abs(src.transform.a * src.transform.e)

    codigos, contagens = np.unique(dados, return_counts=True)
    contagem_por_codigo = dict(zip(codigos.tolist(), contagens.tolist()))

    area_fora_poligono_km2 = round(contagem_por_codigo.pop(CODIGO_FORA_POLIGONO, 0) * pixel_area_m2 / 1e6, 4)

    classes = []
    for codigo, contagem in sorted(contagem_por_codigo.items(), key=lambda item: item[1], reverse=True):
        nome_en, nome_pt = LEGENDA_CLASSES.get(codigo, (f"Classe desconhecida ({codigo})", f"Classe desconhecida ({codigo})"))
        classes.append({
            "class_id": codigo,
            "nome_en": nome_en,
            "nome_pt": nome_pt,
            "area_km2": round(contagem * pixel_area_m2 / 1e6, 4),
        })

    return {
        "classes_presentes": classes,
        "area_fora_poligono_bbox_km2": area_fora_poligono_km2,
        "pixel_area_m2": round(pixel_area_m2, 2),
    }


def montar_metadados(ano: int, codigo_ibge: str, nome_municipio: str, estatisticas_classes: dict) -> dict:
    return {
        "fonte": "MapBiomas Brasil — Coleção 10 (Land Use and Land Cover, produto 'coverage')",
        "colecao": f"{COLECAO} (chamada de 'Coleção 10.1' no site oficial; série 1985-2024)",
        "ano": ano,
        "url_raster_original": montar_url(ano),
        "url_pagina_colecao": URL_COLECAO_INFO,
        "url_legenda_classes": URL_LEGENDA,
        "metodo_acesso": (
            "leitura em streaming via GDAL /vsicurl/ (HTTP range requests) do GeoTIFF nacional "
            "(Cloud-Optimized GeoTIFF, tiled 256x256, LZW), sem autenticação — evita baixar o "
            "arquivo inteiro do Brasil (~750-800MB) para cada ano; não há recorte por bioma "
            "(Pampa) ou UF (RS) disponibilizado para download direto no bucket público (verificado "
            "em 2026-07-27 listando initiatives/brasil/collection_10/lulc/: apenas ATBDs/, "
            "coverage/, maps/, statistics/); o recorte territorial oficial do MapBiomas exige o "
            "toolkit do Google Earth Engine (autenticação de conta), por isso não foi usado"
        ),
        "resolucao_espacial_nativa": "30m (raster original em WGS84 geográfico, EPSG:4326)",
        "codigo_ibge": codigo_ibge,
        "nome_municipio": nome_municipio,
        "crs_processado": CRS_PADRAO,
        "classes_presentes_area_estudo": estatisticas_classes["classes_presentes"],
        "area_fora_poligono_bbox_km2": estatisticas_classes["area_fora_poligono_bbox_km2"],
        "observacao_area_fora_poligono": (
            "o município não é retangular; a área recortada (bounding box) inclui cantos fora do "
            "polígono municipal, preenchidos com o valor 0 pelo rasterio.mask — 0 não é uma classe "
            "MapBiomas válida (a legenda começa em 1) e foi excluído da tabela de classes"
        ),
        "pixel_area_m2_pos_reprojecao": estatisticas_classes["pixel_area_m2"],
        "data_processamento": datetime.now(timezone.utc).isoformat(),
        "transformacao_aplicada": (
            f"recorte (mask+crop) pela área de estudo e reprojeção para {CRS_PADRAO} via "
            "recortar_raster(), a partir de leitura remota em streaming do raster nacional"
        ),
        "serie_temporal": {
            "anos_padrao_da_serie": ANOS_SERIE_DEFAULT,
            "tabela_resumo_area_por_classe": (
                "data/processed/uso-solo_mapbiomas_serie-temporal_area-por-classe.csv — agrega este "
                "e os demais anos disponíveis em data/raw/raster/, gerada por "
                "scripts/download/uso-solo_mapbiomas.py"
            ),
        },
    }


def processar_ano(ano: int, codigo_ibge: str, nome_municipio: str, sessao: requests.Session, forcar: bool) -> None:
    """Baixa/recorta e gera os metadados de um ano da série, se ainda não existir (ou se --forcar)."""
    caminho_saida = CAMINHO_SAIDA_DIR / f"uso-solo_mapbiomas_{ano}_30m.tif"
    caminho_metadados = caminho_saida.with_suffix(".json")

    if caminho_saida.exists() and caminho_metadados.exists() and not forcar:
        logger.info("Uso do solo MapBiomas %d já existe em %s — pulando (use --forcar para refazer).", ano, caminho_saida)
        return

    verificar_ano_disponivel(ano, sessao)
    caminho_saida.parent.mkdir(parents=True, exist_ok=True)
    baixar_e_recortar(ano, caminho_saida)

    estatisticas_classes = calcular_classes_presentes(caminho_saida)
    logger.info(
        "%d classes MapBiomas presentes na área de estudo (%d, %s) — %.2f km² fora do polígono (bbox)",
        len(estatisticas_classes["classes_presentes"]), ano, nome_municipio or codigo_ibge,
        estatisticas_classes["area_fora_poligono_bbox_km2"],
    )

    metadados = montar_metadados(ano, codigo_ibge, nome_municipio, estatisticas_classes)
    caminho_metadados.write_text(json.dumps(metadados, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    logger.info("Metadados de %d salvos em %s", ano, caminho_metadados)


def descobrir_metadados_disponiveis(diretorio: Path) -> list[tuple[int, Path]]:
    """Lista (ano, caminho_json) de todos os anos MapBiomas já baixados em `diretorio`."""
    encontrados = []
    for caminho in diretorio.glob("uso-solo_mapbiomas_*_30m.json"):
        correspondencia = PADRAO_NOME_RASTER.match(caminho.name)
        if correspondencia:
            encontrados.append((int(correspondencia.group(1)), caminho))
    return sorted(encontrados)


def gerar_tabela_serie_temporal(diretorio_raster: Path, caminho_saida_csv: Path) -> pd.DataFrame:
    """Agrega os metadados de todos os anos disponíveis numa tabela longa (ano x classe x área).

    Lê TODOS os uso-solo_mapbiomas_<ano>_30m.json encontrados em disco — não só
    os anos processados nesta execução — para que a tabela resumo sempre
    reflita o estado completo dos dados já baixados.
    """
    metadados_disponiveis = descobrir_metadados_disponiveis(diretorio_raster)
    if not metadados_disponiveis:
        raise RuntimeError(
            f"Nenhum metadado de uso do solo MapBiomas encontrado em {diretorio_raster} "
            "para montar a tabela da série temporal."
        )

    linhas = []
    for ano, caminho_json in metadados_disponiveis:
        metadados = json.loads(caminho_json.read_text(encoding="utf-8"))
        for classe in metadados["classes_presentes_area_estudo"]:
            linhas.append({
                "ano": ano,
                "class_id": classe["class_id"],
                "classe_nome_pt": classe["nome_pt"],
                "classe_nome_en": classe["nome_en"],
                "area_km2": classe["area_km2"],
            })

    tabela = pd.DataFrame(linhas).sort_values(["ano", "area_km2"], ascending=[True, False]).reset_index(drop=True)

    caminho_saida_csv.parent.mkdir(parents=True, exist_ok=True)
    tabela.to_csv(caminho_saida_csv, index=False, encoding="utf-8")

    anos_incluidos = sorted({ano for ano, _ in metadados_disponiveis})
    metadados_tabela = {
        "fonte": "MapBiomas Brasil — Coleção 10 (Land Use and Land Cover, produto 'coverage')",
        "colecao": f"{COLECAO} (chamada de 'Coleção 10.1' no site oficial; série 1985-2024)",
        "descricao": (
            "Área (km²) por classe de uso e cobertura do solo, por ano, na área de estudo — "
            "agregado dos metadados por ano gerados por scripts/download/uso-solo_mapbiomas.py"
        ),
        "anos_incluidos": anos_incluidos,
        "anos_padrao_da_serie": ANOS_SERIE_DEFAULT,
        "formato": (
            "tabela longa (tidy): uma linha por combinação ano x classe presente naquele ano "
            "(classes ausentes num ano não geram linha, o que equivale a área 0); pivote por "
            "'ano' para obter a série temporal por classe (ex.: pandas.pivot_table)"
        ),
        "script_gerador": "scripts/download/uso-solo_mapbiomas.py",
        "data_processamento": datetime.now(timezone.utc).isoformat(),
        "transformacao_aplicada": (
            "concatenação das tabelas 'classes_presentes_area_estudo' de cada "
            "uso-solo_mapbiomas_<ano>_30m.json disponível em data/raw/raster/, ordenada por ano "
            "e área decrescente"
        ),
    }
    caminho_saida_csv.with_suffix(".json").write_text(
        json.dumps(metadados_tabela, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )
    logger.info(
        "Tabela da série temporal (%d ano(s): %s; %d linhas) salva em %s",
        len(anos_incluidos), anos_incluidos, len(tabela), caminho_saida_csv,
    )
    return tabela


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Baixa (via streaming) e recorta a série temporal de uso e cobertura do solo do MapBiomas Brasil."
    )
    parser.add_argument("--codigo-ibge", default=CODIGO_IBGE_DEFAULT, help="Código IBGE do município (default: Uruguaiana/RS) — usado só nos metadados")
    parser.add_argument(
        "--anos", type=parse_anos, default=None,
        help=(
            "Anos a processar, separados por vírgula (default: série padrão a cada "
            f"{PASSO_SERIE_ANOS} anos, {ANOS_SERIE_DEFAULT[0]}-{ANOS_SERIE_DEFAULT[-1]}: "
            f"{','.join(map(str, ANOS_SERIE_DEFAULT))})"
        ),
    )
    parser.add_argument(
        "--forcar", action="store_true",
        help="Baixa/recorta novamente os anos desta execução mesmo se já existirem (combine com --anos para forçar só anos específicos)",
    )
    parser.add_argument(
        "--tabela-saida", type=Path, default=CAMINHO_TABELA_SERIE_DEFAULT,
        help="Caminho da tabela resumo da série temporal (default: data/processed/uso-solo_mapbiomas_serie-temporal_area-por-classe.csv)",
    )
    args = parser.parse_args()

    anos = args.anos or ANOS_SERIE_DEFAULT

    sessao = requests.Session()
    sessao.headers.update(HEADERS)
    nome_municipio = obter_nome_municipio(args.codigo_ibge, sessao)

    for ano in anos:
        processar_ano(ano, args.codigo_ibge, nome_municipio, sessao, args.forcar)

    gerar_tabela_serie_temporal(CAMINHO_SAIDA_DIR, args.tabela_saida)


if __name__ == "__main__":
    main()
