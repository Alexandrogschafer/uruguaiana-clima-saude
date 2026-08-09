"""Baixa poços cadastrados no SIAGAS (Sistema de Informações de Águas
Subterrâneas, CPRM/SGB) de um município:

    data/raw/vetor/pocos-agua-subterranea_siagas-cprm_atual_vetorial.gpkg
    data/raw/vetor/pocos-agua-subterranea_siagas-cprm_atual_vetorial.json

Fonte e método
---------------
Camada oficial "Siagas Web" do SGB, publicada como ArcGIS REST Feature
Layer (nacional, EPSG:4326):
https://geoportal.sgb.gov.br/server/rest/services/hidrologia/SIAGAS_MODDAD/MapServer/0

O filtro por município é feito no próprio serviço (WHERE num_municipio=
<código IBGE>), sem precisar de nome hardcoded — o campo `num_municipio`
da camada já usa o código IBGE de 7 dígitos. Não foi necessário o portal
manual siagas.sgb.gov.br (que exige exportação por sessão/formulário);
essa camada ArcGIS REST é a mesma base, consultável por API, com
paginação via resultOffset (maxRecordCount=1000 por página).

Alternativa manual (documentada, não usada por não ser necessária): caso
este serviço fique indisponível no futuro, o portal
https://siagas.sgb.gov.br permite busca hierarquizada (UF > município)
com exportação em .xls/.dbf via interface web — processo manual, sem API.

Atributos mantidos (nomes originais da fonte entre parênteses): código do
poço (idt_ponto), nome/local do ponto, coordenadas, profundidade
(num_profundidade), nível estático (ne) e dinâmico (nd), vazão específica
(num_vazao_especifica — não confundir com vazão de explotação/bombeamento,
que não consta nesta camada), aquífero captado (str_aquifero), situação,
uso da água e datas de perfuração/cadastro.

Uso:
    python scripts/download/pocos_siagas.py
    python scripts/download/pocos_siagas.py --codigo-ibge 4314902 --forcar
"""

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import pandas as pd
import requests

sys.path.append(str(Path(__file__).resolve().parents[1] / "utils"))
from recorte_municipio import CRS_PADRAO  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

RAIZ = Path(__file__).resolve().parents[2]
CAMINHO_SAIDA = RAIZ / "data" / "raw" / "vetor" / "pocos-agua-subterranea_siagas-cprm_atual_vetorial.gpkg"

CODIGO_IBGE_DEFAULT = "4322400"  # Uruguaiana, RS
CRS_ORIGEM_SIAGAS = "EPSG:4326"
URL_SIAGAS_QUERY = "https://geoportal.sgb.gov.br/server/rest/services/hidrologia/SIAGAS_MODDAD/MapServer/0/query"
PAGE_SIZE = 1000  # maxRecordCount do serviço

URL_PORTAL_MANUAL = "https://siagas.sgb.gov.br"

# Colunas relevantes da camada de origem -> nomes normalizados de saída.
COLUNAS_RELEVANTES = {
    "idt_ponto": "codigo_poco",
    "str_nome_ponto": "nome_poco",
    "str_local_ponto": "local_poco",
    "num_municipio": "codigo_ibge_municipio",
    "str_municipio": "municipio",
    "str_uf": "uf",
    "num_latitude_decimal": "latitude_original",
    "num_longitude_decimal": "longitude_original",
    "num_profundidade": "profundidade_m",
    "ne": "nivel_estatico_m",
    "nd": "nivel_dinamico_m",
    "num_vazao_especifica": "vazao_especifica_m3h_m",
    "str_aquifero": "aquifero",
    "str_natureza_ponto": "natureza_ponto",
    "str_tipo_situacao": "situacao",
    "str_uso_agua": "uso_agua",
    "data_perfuracao": "data_perfuracao",
    "data_cadastro": "data_cadastro",
}


def contar_pocos(codigo_ibge: str, timeout: int = 60) -> int:
    resposta = requests.get(
        URL_SIAGAS_QUERY,
        params={"where": f"num_municipio={codigo_ibge}", "returnCountOnly": "true", "f": "json"},
        timeout=timeout,
    )
    resposta.raise_for_status()
    dados = resposta.json()
    if "error" in dados:
        raise RuntimeError(f"Erro na consulta ao SIAGAS: {dados['error']}")
    return dados["count"]


def baixar_pocos(codigo_ibge: str, total: int, timeout: int = 60) -> gpd.GeoDataFrame:
    """Baixa todos os poços do município, paginando via resultOffset (PAGE_SIZE por página)."""
    paginas = []
    offset = 0
    while offset < total:
        logger.info("Baixando poços SIAGAS %d-%d de %d...", offset, min(offset + PAGE_SIZE, total), total)
        resposta = requests.get(
            URL_SIAGAS_QUERY,
            params={
                "where": f"num_municipio={codigo_ibge}",
                "outFields": "*",
                "f": "geojson",
                "resultOffset": offset,
                "resultRecordCount": PAGE_SIZE,
            },
            timeout=timeout,
        )
        resposta.raise_for_status()
        geojson = resposta.json()
        if "error" in geojson:
            raise RuntimeError(f"Erro na consulta ao SIAGAS: {geojson['error']}")
        pagina = gpd.GeoDataFrame.from_features(geojson["features"], crs=CRS_ORIGEM_SIAGAS)
        paginas.append(pagina)
        offset += PAGE_SIZE

    gdf = gpd.GeoDataFrame(pd.concat(paginas, ignore_index=True), crs=CRS_ORIGEM_SIAGAS)
    return gdf


def processar(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    colunas_presentes = [c for c in COLUNAS_RELEVANTES if c in gdf.columns]
    gdf = gdf[[*colunas_presentes, "geometry"]].rename(columns=COLUNAS_RELEVANTES)
    return gdf.to_crs(CRS_PADRAO)


def calcular_completude(gdf: gpd.GeoDataFrame) -> dict:
    """% de dado faltante por campo — para sinalizar no metadado quais atributos têm baixa cobertura."""
    n = len(gdf)
    completude = {}
    for coluna in gdf.columns:
        if coluna == "geometry":
            continue
        n_faltante = gdf[coluna].isna().sum()
        completude[coluna] = round(100 * n_faltante / n, 1) if n else None
    return completude


def main() -> None:
    parser = argparse.ArgumentParser(description="Baixa poços SIAGAS (CPRM/SGB) de um município via ArcGIS REST.")
    parser.add_argument("--codigo-ibge", default=CODIGO_IBGE_DEFAULT, help="Código IBGE do município (default: Uruguaiana/RS)")
    parser.add_argument("--forcar", action="store_true", help="Baixa novamente mesmo se o arquivo já existir")
    args = parser.parse_args()

    if CAMINHO_SAIDA.exists() and not args.forcar:
        logger.info("%s já existe — nada a fazer (use --forcar para baixar de novo).", CAMINHO_SAIDA)
        return

    total = contar_pocos(args.codigo_ibge)
    logger.info("%d poço(s) SIAGAS encontrados para o código IBGE %s.", total, args.codigo_ibge)

    if total == 0:
        logger.warning(
            "Nenhum poço encontrado para o código IBGE %s. Isso pode significar ausência real de poços "
            "cadastrados no SIAGAS para o município, ou instabilidade do serviço. Alternativa manual: "
            "buscar por UF/município em %s e exportar .xls/.dbf pela interface web.",
            args.codigo_ibge, URL_PORTAL_MANUAL,
        )
        return

    gdf_bruto = baixar_pocos(args.codigo_ibge, total)
    gdf = processar(gdf_bruto)

    completude = calcular_completude(gdf)
    campos_alta_falta = {k: v for k, v in completude.items() if v is not None and v >= 30}

    CAMINHO_SAIDA.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_file(CAMINHO_SAIDA, driver="GPKG", layer="pocos_agua_subterranea")

    metadados = {
        "fonte": "SGB/CPRM — SIAGAS (Sistema de Informações de Águas Subterrâneas), camada 'Siagas Web'",
        "url_servico": URL_SIAGAS_QUERY,
        "url_portal_consulta_manual": URL_PORTAL_MANUAL,
        "codigo_ibge": args.codigo_ibge,
        "data_acesso": datetime.now(timezone.utc).date().isoformat(),
        "n_pocos_encontrados": total,
        "crs_original": CRS_ORIGEM_SIAGAS,
        "crs_processado": CRS_PADRAO,
        "metodo": (
            "consulta direta ao ArcGIS REST FeatureServer/MapServer do SGB, filtrando por "
            "num_municipio=<código IBGE> no próprio serviço (sem hardcode de nome de município), "
            "paginada via resultOffset (limite de 1000 feições por página do serviço)"
        ),
        "campos_mantidos": list(COLUNAS_RELEVANTES.values()),
        "completude_por_campo_pct_faltante": completude,
        "campos_alta_taxa_faltante_30pct_ou_mais": campos_alta_falta,
        "nota_vazao": (
            "a camada disponibiliza apenas vazão específica (num_vazao_especifica, m³/h por metro de "
            "rebaixamento), não a vazão de explotação/bombeamento (que não consta neste serviço) — "
            "avaliar necessidade de complementar com o portal manual se a vazão de explotação for "
            "necessária para alguma análise futura"
        ),
        "alternativa_manual_se_servico_indisponivel": (
            f"portal {URL_PORTAL_MANUAL} permite busca hierarquizada por UF/município/bacia/coordenadas "
            "e exportação em .xls/.dbf pela interface web — processo manual, sem API, não usado aqui "
            "por não ter sido necessário (serviço ArcGIS REST funcionou normalmente)"
        ),
        "data_processamento": datetime.now(timezone.utc).isoformat(),
    }
    caminho_metadados = CAMINHO_SAIDA.with_suffix(".json")
    caminho_metadados.write_text(json.dumps(metadados, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

    logger.info("Salvo: %s (%d poços)", CAMINHO_SAIDA, total)
    logger.info("Metadados salvos em %s", caminho_metadados)
    logger.info("Campos com >=30%% de dado faltante: %s", campos_alta_falta)


if __name__ == "__main__":
    main()
