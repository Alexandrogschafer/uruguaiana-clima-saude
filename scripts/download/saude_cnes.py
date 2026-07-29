"""
Baixa os estabelecimentos de saúde cadastrados no CNES (Cadastro Nacional
de Estabelecimentos de Saúde / DATASUS) para um município, recortados pela
área de estudo do projeto (config/area_estudo.geojson), e gera:

    data/raw/vetor/saude-cnes_datasus_atual_vetorial.gpkg
    data/raw/vetor/saude-cnes_datasus_atual_vetorial.geojson

Fonte de dados
---------------
O CNES não expõe uma API pública oficial e documentada. Este script usa o
endpoint interno que o próprio site https://cnes.datasus.gov.br usa para
alimentar a página de consulta pública de estabelecimentos
(pages/estabelecimentos/consulta.jsp), identificado por engenharia reversa
do JavaScript da página (angular/estabelecimento.js):

    GET https://cnes.datasus.gov.br/services/estabelecimentos?municipio={codmun6}
        -> lista resumida (id, cnes, nome, natureza jurídica, atende SUS...)
    GET https://cnes.datasus.gov.br/services/estabelecimentos/{id}
        -> detalhe por estabelecimento (endereço, tipo de unidade,
           latitude/longitude, telefone...)

Por não ser uma API documentada, o endpoint pode mudar sem aviso — se
parar de funcionar, buscar manualmente em
https://cnes.datasus.gov.br/pages/estabelecimentos/consulta.jsp para
conferir a contagem de estabelecimentos e atualizar este script.

Observações confirmadas por teste manual (ver docstring como registro):
- O endpoint exige o header HTTP `Referer` apontando para a página de
  consulta; sem ele, responde 503 (bloqueio simples anti-scraping).
- O parâmetro `municipio` é o código IBGE de 6 dígitos (sem o dígito
  verificador), ou seja, os 6 primeiros dígitos do código de 7 dígitos.
- A consulta sem filtro de status retorna os estabelecimentos ativos
  (o filtro de status/desativados/expirados foi removido da interface
  pública, conforme comentário no HTML da página).

Idempotente: se os arquivos de saída já existirem, não baixa de novo (a
menos que --forcar seja usado). Loga fonte, data e tamanho do download.

Parametrizado por código IBGE para permitir reuso em outros municípios,
conforme meta de replicabilidade do projeto. Default: 4322400 (Uruguaiana, RS).

Uso:
    python scripts/download/saude_cnes.py
    python scripts/download/saude_cnes.py --codigo-ibge 4314902 --forcar
"""

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import pandas as pd
import requests
from shapely.geometry import Point

sys.path.append(str(Path(__file__).resolve().parents[1] / "utils"))
from recorte_municipio import CRS_PADRAO, carregar_area_estudo, recortar_vetor  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

CRS_ORIGEM = "EPSG:4674"  # SIRGAS 2000 geográfico — CRS padrão do CNES/IBGE
CODIGO_IBGE_DEFAULT = "4322400"  # Uruguaiana, RS

BASE_URL = "https://cnes.datasus.gov.br/services"
REFERER = "https://cnes.datasus.gov.br/pages/estabelecimentos/consulta.jsp"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatível; script-pesquisa-ClimaPampa/1.0)",
    "Accept": "application/json",
    "Referer": REFERER,  # exigido pelo servidor; sem ele a resposta é 503
}

# Limites aproximados do território brasileiro, usados apenas para descartar
# coordenadas claramente inválidas (ex.: "0", trocadas ou fora do país).
LAT_MIN, LAT_MAX = -34.0, 6.0
LON_MIN, LON_MAX = -75.0, -32.0

INTERVALO_ENTRE_REQUISICOES_S = 0.2  # uso respeitoso de um endpoint não documentado

CAMINHO_SAIDA_DEFAULT = (
    Path(__file__).resolve().parents[2] / "data" / "raw" / "vetor" / "saude-cnes_datasus_atual_vetorial"
)

# Categorização simplificada do TP_UNIDADE (texto retornado em dsTpUnidade)
# em grandes grupos de interesse do projeto. Nomenclatura oficial e estável
# do CNES — não é específica de Uruguaiana, por isso é mantida como
# constante do módulo (não fere a regra de não hardcodear dados do
# município).
CATEGORIAS_TP_UNIDADE = {
    "hospital": ["HOSPITAL GERAL", "HOSPITAL ESPECIALIZADO", "HOSPITAL/DIA"],
    "ubs_esf": ["POSTO DE SAUDE", "CENTRO DE SAUDE", "UNIDADE BASICA", "UNIDADE MISTA"],
    "clinica_ambulatorio": [
        "POLICLINICA", "CLINICA", "CONSULTORIO ISOLADO", "CENTRO DE ATENCAO PSICOSSOCIAL",
        "CENTRO DE APOIO A SAUDE DA FAMILIA", "PRONTO ATENDIMENTO", "PRONTO SOCORRO",
        "POLO ACADEMIA DA SAUDE", "CENTRO DE PARTO NORMAL",
    ],
    "farmacia": ["FARMACIA"],
    "laboratorio_apoio_diagnostico": [
        "LABORATORIO", "UNIDADE DE APOIO DIAGNOSE E TERAPIA", "SADT",
    ],
    "vigilancia_saude": ["UNIDADE DE VIGILANCIA"],
}


def categorizar_tp_unidade(dstpunidade: str | None) -> str:
    """Mapeia o texto livre `dsTpUnidade` do CNES para uma categoria simplificada."""
    if not dstpunidade:
        return "nao_informado"
    texto = dstpunidade.upper()
    for categoria, palavras_chave in CATEGORIAS_TP_UNIDADE.items():
        if any(palavra in texto for palavra in palavras_chave):
            return categoria
    return "outro"


def buscar_lista_estabelecimentos(municipio_cnes: str, sessao: requests.Session) -> list[dict]:
    """Busca a lista resumida de estabelecimentos ativos do município (services/estabelecimentos)."""
    url = f"{BASE_URL}/estabelecimentos?municipio={municipio_cnes}"
    logger.info("Buscando lista de estabelecimentos do CNES (município %s) — %s", municipio_cnes, url)
    try:
        resposta = sessao.get(url, timeout=30)
        resposta.raise_for_status()
    except requests.RequestException as erro:
        raise RuntimeError(
            f"Falha ao consultar a lista de estabelecimentos do CNES para o município {municipio_cnes}: {erro}"
        ) from erro

    lista = resposta.json()
    logger.info("Lista recebida: %d estabelecimento(s)", len(lista))
    return lista


def buscar_detalhes_estabelecimentos(lista_resumida: list[dict], sessao: requests.Session) -> list[dict]:
    """Busca o detalhe (endereço, tipo de unidade, lat/long) de cada estabelecimento da lista.

    O campo `atendeSus` só existe na lista resumida (não no detalhe), por
    isso é copiado para o registro de detalhe aqui.
    """
    detalhes = []
    total = len(lista_resumida)
    for i, item in enumerate(lista_resumida, start=1):
        coid = item["id"]
        url = f"{BASE_URL}/estabelecimentos/{coid}"
        try:
            resposta = sessao.get(url, timeout=30)
            resposta.raise_for_status()
            detalhe = resposta.json()
        except requests.RequestException as erro:
            logger.warning("Falha ao buscar detalhe do estabelecimento %s (%s): %s — registro ignorado", coid, url, erro)
            continue

        detalhe["atendeSus"] = item.get("atendeSus")
        detalhes.append(detalhe)
        if i % 50 == 0 or i == total:
            logger.info("Detalhes obtidos: %d/%d", i, total)
        time.sleep(INTERVALO_ENTRE_REQUISICOES_S)

    return detalhes


def _to_float_coordenada(valor) -> float | None:
    """Converte nuLatitude/nuLongitude (string) para float, ou None se ausente/inválido."""
    if valor in (None, "", "0", "0.0"):
        return None
    try:
        return float(valor)
    except (TypeError, ValueError):
        return None


def montar_geodataframe(detalhes: list[dict]) -> tuple[gpd.GeoDataFrame, int, int]:
    """Converte os detalhes brutos em GeoDataFrame de pontos (CRS_ORIGEM).

    Retorna também a contagem de registros com coordenadas válidas e a
    contagem de registros descartados por falta/invalidade de lat/long.
    """
    registros = []
    n_descartados_sem_coordenadas = 0

    for d in detalhes:
        lat = _to_float_coordenada(d.get("nuLatitude"))
        lon = _to_float_coordenada(d.get("nuLongitude"))

        coordenada_valida = (
            lat is not None and lon is not None
            and LAT_MIN <= lat <= LAT_MAX and LON_MIN <= lon <= LON_MAX
        )
        if not coordenada_valida:
            n_descartados_sem_coordenadas += 1
            continue

        endereco = ", ".join(
            str(parte) for parte in [d.get("noLogradouro"), d.get("nuEndereco"), d.get("noComplemento"), d.get("bairro")]
            if parte
        )

        registros.append(
            {
                "cnes": d.get("cnes"),
                "nome_fantasia": d.get("noFantasia"),
                "nome_empresarial": d.get("noEmpresarial"),
                "tipo_unidade": d.get("dsTpUnidade"),
                "tipo_unidade_categoria": categorizar_tp_unidade(d.get("dsTpUnidade")),
                "tipo_estabelecimento": d.get("dsTipoEstabelecimento"),
                "endereco": endereco or None,
                "cep": d.get("cep"),
                "municipio": d.get("noMunicipio"),
                "uf": d.get("uf"),
                "telefone": d.get("nuTelefone"),
                "atende_sus": d.get("atendeSus"),
                "gestao": d.get("tpGestao"),
                "geometry": Point(lon, lat),
            }
        )

    gdf = gpd.GeoDataFrame(registros, geometry="geometry", crs=CRS_ORIGEM)
    return gdf, len(registros), n_descartados_sem_coordenadas


def salvar_saida(
    gdf: gpd.GeoDataFrame,
    caminho_base: Path,
    codigo_ibge: str,
    municipio_cnes: str,
    n_lista: int,
    n_validas: int,
    n_descartados_coord: int,
    n_descartados_area: int,
) -> None:
    caminho_base.parent.mkdir(parents=True, exist_ok=True)
    caminho_gpkg = caminho_base.with_suffix(".gpkg")
    caminho_geojson = caminho_base.with_suffix(".geojson")
    caminho_metadados = caminho_base.with_suffix(".json")

    gdf.to_file(caminho_gpkg, driver="GPKG", layer="saude_cnes")
    logger.info("Vetor salvo em %s (CRS: %s, %d feições)", caminho_gpkg, CRS_PADRAO, len(gdf))

    gdf.to_file(caminho_geojson, driver="GeoJSON")
    logger.info("Vetor salvo em %s", caminho_geojson)

    tamanho_kb = caminho_gpkg.stat().st_size / 1024
    contagem_tipo_unidade = gdf["tipo_unidade"].value_counts(dropna=True).to_dict()
    contagem_categoria = gdf["tipo_unidade_categoria"].value_counts(dropna=True).to_dict()

    comparacao_osm = {
        "observacao": (
            "scripts/download/infraestrutura_osm.py baixou 7 estabelecimentos de saúde via OSM "
            "(2 hospitais, 3 clínicas, 1 farmácia, 1 healthcare sem amenity) na mesma área de estudo. "
            "O CNES é o cadastro oficial (obrigatório para prestadores SUS e a maioria dos privados) e "
            "tende a ter cobertura muito mais completa, incluindo consultórios isolados, UBS/ESF e "
            "farmácias que raramente são mapeados no OSM; a diferença de contagem reflete a defasagem "
            "típica do mapeamento colaborativo do OSM em relação ao cadastro administrativo do CNES."
        ),
    }
    try:
        caminho_osm_metadados = (
            Path(__file__).resolve().parents[2] / "data" / "raw" / "vetor" / "saude-estabelecimentos_osm_atual_vetorial.json"
        )
        if caminho_osm_metadados.exists():
            osm_meta = json.loads(caminho_osm_metadados.read_text(encoding="utf-8"))
            comparacao_osm["n_feicoes_osm"] = osm_meta.get("n_feicoes_total")
            comparacao_osm["n_feicoes_cnes"] = len(gdf)
            comparacao_osm["diferenca_cnes_menos_osm"] = len(gdf) - osm_meta.get("n_feicoes_total", 0)
    except (OSError, json.JSONDecodeError) as erro:
        logger.warning("Não foi possível ler metadados do OSM para comparação: %s", erro)

    metadados = {
        "fonte": (
            "CNES/DATASUS — endpoint interno usado por "
            "https://cnes.datasus.gov.br/pages/estabelecimentos/consulta.jsp (não é uma API pública documentada)"
        ),
        "url_lista": f"{BASE_URL}/estabelecimentos?municipio={municipio_cnes}",
        "url_detalhe_template": f"{BASE_URL}/estabelecimentos/{{id}}",
        "observacao_endpoint": "requer header HTTP Referer apontando para a página de consulta; sem ele retorna 503",
        "codigo_ibge": codigo_ibge,
        "codigo_municipio_cnes": municipio_cnes,
        "n_estabelecimentos_lista": n_lista,
        "n_com_coordenadas_validas": n_validas,
        "n_descartados_sem_coordenadas_validas": n_descartados_coord,
        "n_descartados_fora_area_estudo": n_descartados_area,
        "n_feicoes_final": len(gdf),
        "contagem_por_tipo_unidade": contagem_tipo_unidade,
        "contagem_por_categoria_simplificada": contagem_categoria,
        "comparacao_com_osm": comparacao_osm,
        "tamanho_gpkg_kb": round(tamanho_kb, 1),
        "crs_original": CRS_ORIGEM,
        "crs_processado": CRS_PADRAO,
        "data_processamento": datetime.now(timezone.utc).isoformat(),
        "transformacao_aplicada": (
            "download da lista + detalhe de cada estabelecimento via endpoint interno do CNES, "
            "construção de pontos a partir de nuLatitude/nuLongitude, descarte de coordenadas ausentes/"
            f"fora do Brasil, reprojeção de {CRS_ORIGEM} para {CRS_PADRAO} e recorte pela área de estudo do projeto"
        ),
    }
    caminho_metadados.write_text(json.dumps(metadados, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("Metadados salvos em %s", caminho_metadados)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Baixa os estabelecimentos de saúde do CNES/DATASUS para um município e gera um vetor de pontos."
    )
    parser.add_argument("--codigo-ibge", default=CODIGO_IBGE_DEFAULT, help="Código IBGE do município (default: Uruguaiana/RS)")
    parser.add_argument("--saida", type=Path, default=CAMINHO_SAIDA_DEFAULT, help="Caminho base de saída (sem extensão)")
    parser.add_argument("--forcar", action="store_true", help="Baixa novamente mesmo se os arquivos já existirem")
    args = parser.parse_args()

    caminho_gpkg = args.saida.with_suffix(".gpkg")
    if caminho_gpkg.exists() and not args.forcar:
        logger.info("Estabelecimentos de saúde (CNES) já existem em %s — nada a fazer (use --forcar para baixar de novo).", caminho_gpkg)
        return

    municipio_cnes = args.codigo_ibge[:6]  # CNES usa o código IBGE sem o dígito verificador

    sessao = requests.Session()
    sessao.headers.update(HEADERS)

    lista_resumida = buscar_lista_estabelecimentos(municipio_cnes, sessao)
    detalhes = buscar_detalhes_estabelecimentos(lista_resumida, sessao)

    gdf, n_validas, n_descartados_coord = montar_geodataframe(detalhes)
    n_antes_recorte = len(gdf)

    area_estudo = carregar_area_estudo()
    gdf = gdf.to_crs(CRS_PADRAO)
    gdf = recortar_vetor(gdf, area_estudo)
    n_descartados_area = n_antes_recorte - len(gdf)

    salvar_saida(
        gdf, args.saida, args.codigo_ibge, municipio_cnes,
        n_lista=len(lista_resumida), n_validas=n_validas,
        n_descartados_coord=n_descartados_coord, n_descartados_area=n_descartados_area,
    )


if __name__ == "__main__":
    main()
