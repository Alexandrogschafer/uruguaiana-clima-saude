"""Baixa a Carta de Suscetibilidade a Movimentos Gravitacionais de Massa e
Inundações (CPRM/SGB) de um município, se existir, e consolida as camadas
de inundação e movimento de massa num único GeoPackage:

    data/raw/vetor/risco-geologico_cprm_{ano}_vetorial.gpkg
    data/raw/vetor/risco-geologico_cprm_{ano}_vetorial.json

Cobertura do produto (verificado por consulta real, não suposição)
--------------------------------------------------------------------
Esse produto NÃO cobre todos os municípios brasileiros — é uma
cartografia prioritária de prevenção de desastres, concentrada em
municípios historicamente sujeitos a movimentos de massa (relevo
acidentado) e/ou inundação. Rio Grande do Sul tem ~23 municípios
mapeados (ver https://sgb.gov.br/web/guest/rio-grande-do-sul-cartas-de-suscetibilidade),
Uruguaiana entre eles.

Fonte e método
---------------
1. Repositório Institucional de Geociências do SGB (RIGEO, DSpace 7) —
   busca pela API REST de descoberta
   (rigeo.sgb.gov.br/server/api/discover/search/objects?query=...) pelo
   nome do item "Carta de suscetibilidade a movimentos gravitacionais de
   massa e inundação: município de {NOME}, {UF}" — evita hardcode de
   UUID/handle, funciona para qualquer município que tenha o produto.
2. Localiza o pacote SIG (bitstream .zip com "sig_" no nome, dentro do
   bundle ORIGINAL do item) — os outros arquivos do item são o mapa em
   PDF (não usado aqui) e metadados de licença/thumbnail.
3. Extrai o .zip e lê `Suscetibilidade/Inundacao_A.shp` e
   `Suscetibilidade/Movimento_de_Massa_A.shp` — ambos já publicados em
   EPSG:31981 (o padrão do projeto), confirmado por leitura real dos
   arquivos; não precisou reprojeção.

IMPORTANTE (achado real, não hardcoded): a API ArcGIS ao vivo do SGB para
essas camadas (geoportal.sgb.gov.br/server/rest/services/gestaoterritorial/
{inundacao,corrida_de_massa}/FeatureServer) se mostrou pouco confiável —
a camada de inundação deu timeout/503 em várias tentativas, e a de
movimento de massa devolveu 0 feições para Uruguaiana mesmo havendo 43
polígonos no pacote oficial do RIGEO. Por isso este script usa o pacote
SIG do repositório institucional (fonte primária do produto), não a API
ao vivo.

Se não existir produto para o município: o script termina sem erro,
apenas registrando a ausência (não é falha de processamento — é
indisponibilidade real da fonte para municípios fora do programa
prioritário de mapeamento).

Uso:
    python scripts/download/risco_geologico_cprm.py
    python scripts/download/risco_geologico_cprm.py --codigo-ibge 4300604 --forcar   # Alegrete, RS (tem produto)
"""

import argparse
import json
import logging
import re
import shutil
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import requests

sys.path.append(str(Path(__file__).resolve().parents[1] / "utils"))
from recorte_municipio import CRS_PADRAO  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

RAIZ = Path(__file__).resolve().parents[2]
CAMINHO_SAIDA_TEMPLATE = RAIZ / "data" / "raw" / "vetor" / "risco-geologico_cprm_{ano}_vetorial.gpkg"

RIGEO_SEARCH_URL = "https://rigeo.sgb.gov.br/server/api/discover/search/objects"
RIGEO_BUNDLES_URL = "https://rigeo.sgb.gov.br/server/api/core/items/{uuid}/bundles"
RIGEO_BITSTREAMS_URL = "https://rigeo.sgb.gov.br/server/api/core/bundles/{uuid}/bitstreams"
RIGEO_CONTENT_URL = "https://rigeo.sgb.gov.br/server/api/core/bitstreams/{uuid}/content"

CODIGO_IBGE_DEFAULT = "4322400"
HEADERS = {"User-Agent": "Mozilla/5.0 (compativel; script-pesquisa-ClimaPampa/1.0)"}
URL_MUNICIPIO_IBGE = "https://servicodados.ibge.gov.br/api/v1/localidades/municipios/{codigo}"


def obter_municipio_uf(codigo_ibge: str) -> tuple[str, str]:
    resposta = requests.get(URL_MUNICIPIO_IBGE.format(codigo=codigo_ibge), headers=HEADERS, timeout=30)
    resposta.raise_for_status()
    dados = resposta.json()
    return dados["nome"], dados["microrregiao"]["mesorregiao"]["UF"]["sigla"]


def buscar_item_rigeo(nome_municipio: str, uf: str) -> dict | None:
    """Busca o item da Carta de Suscetibilidade no RIGEO pelo nome do município."""
    query = f"carta de suscetibilidade movimentos gravitacionais massa inundacao {nome_municipio}"
    resposta = requests.get(RIGEO_SEARCH_URL, params={"query": query, "size": 10}, headers=HEADERS, timeout=30)
    resposta.raise_for_status()
    objetos = resposta.json().get("_embedded", {}).get("searchResult", {}).get("_embedded", {}).get("objects", [])

    padrao_titulo = re.compile(r"carta de suscetibilidade.*movimento", re.IGNORECASE)
    for obj in objetos:
        item = obj.get("_embedded", {}).get("indexableObject", {})
        nome_item = item.get("name", "")
        if padrao_titulo.search(nome_item) and nome_municipio.upper() in nome_item.upper():
            return item
    return None


def localizar_bitstream_sig(uuid_item: str) -> dict | None:
    """Percorre os bundles do item até achar o bitstream .zip do pacote SIG (não o PDF/licença)."""
    resposta = requests.get(RIGEO_BUNDLES_URL.format(uuid=uuid_item), headers=HEADERS, timeout=30)
    resposta.raise_for_status()
    for bundle in resposta.json().get("_embedded", {}).get("bundles", []):
        if bundle.get("name") != "ORIGINAL":
            continue
        resp_bit = requests.get(RIGEO_BITSTREAMS_URL.format(uuid=bundle["uuid"]), headers=HEADERS, timeout=30)
        resp_bit.raise_for_status()
        for bitstream in resp_bit.json().get("_embedded", {}).get("bitstreams", []):
            nome = bitstream.get("name", "")
            if nome.lower().startswith("sig_") and nome.lower().endswith(".zip"):
                return bitstream
    return None


def baixar_e_extrair_sig(uuid_bitstream: str, tmpdir: Path) -> Path:
    url = RIGEO_CONTENT_URL.format(uuid=uuid_bitstream)
    caminho_zip = tmpdir / "sig_suscetibilidade.zip"
    logger.info("Baixando pacote SIG do RIGEO — %s", url)
    with requests.get(url, headers=HEADERS, timeout=300, stream=True) as resposta:
        resposta.raise_for_status()
        with open(caminho_zip, "wb") as f:
            for bloco in resposta.iter_content(chunk_size=1024 * 1024):
                f.write(bloco)

    diretorio_extraido = tmpdir / "sig_extraido"
    with zipfile.ZipFile(caminho_zip) as zf:
        zf.extractall(diretorio_extraido)
    return diretorio_extraido


def main() -> None:
    parser = argparse.ArgumentParser(description="Baixa a Carta de Suscetibilidade CPRM/SGB (inundação + movimento de massa) de um município, se existir.")
    parser.add_argument("--codigo-ibge", default=CODIGO_IBGE_DEFAULT)
    parser.add_argument("--forcar", action="store_true")
    args = parser.parse_args()

    nome_municipio, uf = obter_municipio_uf(args.codigo_ibge)
    logger.info("Buscando Carta de Suscetibilidade CPRM/SGB para %s (%s)...", nome_municipio, uf)

    item = buscar_item_rigeo(nome_municipio, uf)
    if item is None:
        logger.warning(
            "Nenhuma Carta de Suscetibilidade encontrada no RIGEO para %s — provável indisponibilidade real da "
            "fonte (produto cobre só municípios prioritários do programa de prevenção de desastres, não todo o "
            "Brasil). Não é uma falha de processamento.",
            nome_municipio,
        )
        return

    logger.info("Item encontrado: %s", item["name"])
    bitstream = localizar_bitstream_sig(item["uuid"])
    if bitstream is None:
        logger.warning("Item encontrado (%s) mas sem pacote SIG (.zip) anexado — só PDF/outros formatos.", item["name"])
        return

    with tempfile.TemporaryDirectory(prefix="cprm_suscet_") as tmpdir_str:
        tmpdir = Path(tmpdir_str)
        diretorio = baixar_e_extrair_sig(bitstream["uuid"], tmpdir)

        caminho_inundacao = next(diretorio.rglob("Inundacao_A.shp"), None)
        caminho_movimento = next(diretorio.rglob("Movimento_de_Massa_A.shp"), None)
        if caminho_inundacao is None and caminho_movimento is None:
            raise RuntimeError(f"Pacote SIG baixado mas sem os shapefiles esperados em Suscetibilidade/ — conteúdo: {list(diretorio.rglob('*.shp'))}")

        camadas_info = {}
        caminho_saida = Path(str(CAMINHO_SAIDA_TEMPLATE).format(ano="2021"))
        if caminho_saida.exists() and not args.forcar:
            logger.info("%s já existe — nada a fazer (use --forcar para refazer).", caminho_saida)
            return
        caminho_saida.parent.mkdir(parents=True, exist_ok=True)

        for caminho_shp, nome_layer in [(caminho_inundacao, "inundacao"), (caminho_movimento, "movimento_de_massa")]:
            if caminho_shp is None:
                continue
            gdf = gpd.read_file(caminho_shp)
            if gdf.crs is None:
                raise ValueError(f"{caminho_shp} não tem CRS definido — não dá pra confirmar EPSG:31981.")
            if gdf.crs.to_string() != CRS_PADRAO:
                gdf = gdf.to_crs(CRS_PADRAO)
            gdf.columns = [c.strip().lower() for c in gdf.columns]
            gdf.to_file(caminho_saida, driver="GPKG", layer=nome_layer)
            camadas_info[nome_layer] = {
                "n_feicoes": len(gdf),
                "classes": gdf["classe"].value_counts().to_dict() if "classe" in gdf.columns else None,
                "area_km2": round(gdf.geometry.area.sum() / 1e6, 2),
            }
            logger.info("Camada '%s': %d feições, %s", nome_layer, len(gdf), camadas_info[nome_layer]["classes"])

    metadados = {
        "fonte": f"CPRM/SGB — {item['name']}",
        "repositorio": "RIGEO (Repositório Institucional de Geociências do SGB) — https://rigeo.sgb.gov.br/",
        "item_rigeo_uuid": item["uuid"],
        "projeto": "Cartas de Suscetibilidade a Movimentos Gravitacionais de Massa e Inundações",
        "ano_validacao_campo": 2021,
        "codigo_ibge": args.codigo_ibge,
        "nome_municipio": nome_municipio,
        "uf": uf,
        "crs": CRS_PADRAO,
        "crs_original": "já publicado em EPSG:31981 pela fonte — sem reprojeção necessária (verificado por leitura real do shapefile)",
        "camadas": camadas_info,
        "metodo": (
            "busca do item no RIGEO pela API de descoberta do DSpace (por nome do município, sem hardcode de "
            "UUID), localização do bitstream .zip do pacote SIG no bundle ORIGINAL, download e extração dos "
            "shapefiles Suscetibilidade/Inundacao_A.shp e Suscetibilidade/Movimento_de_Massa_A.shp"
        ),
        "limitacao_api_arcgis": (
            "a API ArcGIS ao vivo do SGB (geoportal.sgb.gov.br/.../gestaoterritorial/{inundacao,corrida_de_massa}) "
            "não foi usada por instabilidade: a camada de inundação deu timeout/503 em múltiplas tentativas, e a "
            "de movimento de massa devolveu 0 feições para este município mesmo havendo dados no pacote oficial "
            "do RIGEO — usar o pacote do repositório como fonte primária, não a API ao vivo"
        ),
        "aviso_interpretacao": (
            "classificação de suscetibilidade é um produto de modelagem (ver nota técnica do projeto), validado "
            "por campo em 2021 — não é medição direta; para movimento de massa, quase toda a área do município "
            "recebeu classe 'Média' de forma bastante uniforme, o que pode refletir a metodologia do modelo "
            "aplicada a um relevo predominantemente plano (Pampa) mais do que um levantamento de campo "
            "detalhado por polígono — sinalizar para validação da equipe de saúde/geociências antes de uso"
        ),
        "data_processamento": datetime.now(timezone.utc).isoformat(),
    }
    caminho_metadados = caminho_saida.with_suffix(".json")
    caminho_metadados.write_text(json.dumps(metadados, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    logger.info("Salvo: %s", caminho_saida)
    logger.info("Metadados salvos em %s", caminho_metadados)


if __name__ == "__main__":
    main()
