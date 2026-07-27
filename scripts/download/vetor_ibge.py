"""
Baixa o limite municipal (malha territorial) via API de Malhas do IBGE e
gera o arquivo de referência único da área de estudo do projeto:

    config/area_estudo.geojson  (EPSG:31982 — SIRGAS 2000 / UTM 22S)

Idempotente: se o arquivo já existir, não baixa de novo (a menos que
--forcar seja usado). Loga fonte, data e tamanho do download.

Parametrizado por código IBGE para permitir reuso em outros municípios
(ex.: outros municípios de fronteira/ribeirinhos), conforme meta de
replicabilidade do projeto. Default: 4322400 (Uruguaiana, RS).

Uso:
    python scripts/download/vetor_ibge.py
    python scripts/download/vetor_ibge.py --codigo-ibge 4314902 --forcar
"""

import argparse
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

CRS_PADRAO = "EPSG:31982"
CODIGO_IBGE_DEFAULT = "4322400"  # Uruguaiana, RS
CAMINHO_SAIDA_DEFAULT = Path(__file__).resolve().parents[2] / "config" / "area_estudo.geojson"

# API de Malhas Territoriais do IBGE — retorna a malha do município em GeoJSON.
# Documentação: https://servicodados.ibge.gov.br/api/docs/malhas
URL_MALHA_IBGE = "https://servicodados.ibge.gov.br/api/v3/malhas/municipios/{codigo}"


def baixar_malha_municipal(codigo_ibge: str, timeout: int = 60) -> dict:
    """Consulta a API de Malhas do IBGE e retorna o GeoJSON bruto do município."""
    parametros = {"formato": "application/vnd.geo+json", "qualidade": "maxima"}
    url = URL_MALHA_IBGE.format(codigo=codigo_ibge)
    logger.info("Baixando malha municipal do IBGE (código %s) — %s", codigo_ibge, url)

    try:
        resposta = requests.get(url, params=parametros, timeout=timeout)
        resposta.raise_for_status()
    except requests.RequestException as erro:
        raise RuntimeError(
            f"Falha ao baixar malha municipal do IBGE para o código {codigo_ibge}: {erro}"
        ) from erro

    geojson_bruto = resposta.json()
    tamanho_kb = len(resposta.content) / 1024
    logger.info("Download concluído: %.1f KB", tamanho_kb)
    return geojson_bruto


def salvar_area_estudo(geojson_bruto: dict, caminho_saida: Path, codigo_ibge: str) -> None:
    """Reprojeta para o CRS padrão do projeto e salva o GeoJSON + metadados irmãos."""
    gdf = gpd.GeoDataFrame.from_features(geojson_bruto.get("features", geojson_bruto), crs="EPSG:4674")
    # A malha do IBGE normalmente vem em SIRGAS 2000 geográfico (EPSG:4674);
    # reprojetamos para o CRS de trabalho do projeto.
    gdf = gdf.to_crs(CRS_PADRAO)

    caminho_saida.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_file(caminho_saida, driver="GeoJSON")
    logger.info("Área de estudo salva em %s (CRS: %s)", caminho_saida, CRS_PADRAO)

    metadados = {
        "fonte": "IBGE — API de Malhas Territoriais",
        "url": URL_MALHA_IBGE.format(codigo=codigo_ibge),
        "codigo_ibge": codigo_ibge,
        "crs_original": "EPSG:4674",
        "crs_processado": CRS_PADRAO,
        "data_processamento": datetime.now(timezone.utc).isoformat(),
        "transformacao_aplicada": "reprojeção para EPSG:31982 (SIRGAS 2000 / UTM 22S)",
    }
    caminho_metadados = caminho_saida.with_suffix(".json")
    caminho_metadados.write_text(json.dumps(metadados, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("Metadados salvos em %s", caminho_metadados)


def main() -> None:
    parser = argparse.ArgumentParser(description="Baixa o limite municipal (IBGE) e gera a área de estudo do projeto.")
    parser.add_argument("--codigo-ibge", default=CODIGO_IBGE_DEFAULT, help="Código IBGE do município (default: Uruguaiana/RS)")
    parser.add_argument("--saida", type=Path, default=CAMINHO_SAIDA_DEFAULT, help="Caminho de saída do GeoJSON")
    parser.add_argument("--forcar", action="store_true", help="Baixa novamente mesmo se o arquivo já existir")
    args = parser.parse_args()

    if args.saida.exists() and not args.forcar:
        logger.info("Área de estudo já existe em %s — nada a fazer (use --forcar para baixar de novo).", args.saida)
        return

    geojson_bruto = baixar_malha_municipal(args.codigo_ibge)
    salvar_area_estudo(geojson_bruto, args.saida, args.codigo_ibge)


if __name__ == "__main__":
    main()
