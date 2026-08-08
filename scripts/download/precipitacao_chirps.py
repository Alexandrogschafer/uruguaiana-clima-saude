"""Baixa a série mensal de precipitação CHIRPS para a área de estudo
estendida (buffer sobre o município, config/area_estudo_bacias.geojson) e
gera a média espacial de precipitação por mês:

    data/raw/precipitacao_chirps_{ano_inicio}-{ano_fim}_mensal.csv
    data/raw/precipitacao_chirps_{ano_inicio}-{ano_fim}_mensal.json

Fonte e método
--------------
CHIRPS v2.0 (Climate Hazards Group InfraRed Precipitation with Station
data, UCSB), grade global mensal ~0,05° (~5,5 km), distribuída como um
GeoTIFF (.tif.gz) por mês em
https://data.chc.ucsb.edu/products/CHIRPS-2.0/global_monthly/tifs/. Sem
API/autenticação — download HTTP público, arquivo por mês desde 1981.

Cada arquivo é lido em streaming via GDAL (`/vsigzip//vsicurl/`, mesmo
princípio de scripts/download/uso-solo_mapbiomas.py e
scripts/download/terreno_anadem.py), sem baixar o GeoTIFF global (~14 MB
por mês, mas ~7200x2000 px cobrindo o planeta todo) por completo antes de
recortar — só a janela sobre a área de estudo é efetivamente decodificada.
A estatística por mês é a média espacial (rasterstats.zonal_stats) da
precipitação (mm) dentro da área de estudo ESTENDIDA (buffer de 18 km —
ver config/area_estudo_bacias.geojson), não o limite municipal estrito:
mais representativo da bacia de contribuição da região do que só o
polígono do município.

nodata: os arquivos CHIRPS não marcam nodata no header do GeoTIFF, mas
usam -9999 como valor de preenchimento (oceano/área sem dado) —
tratado explicitamente como nodata na extração zonal.

Janela temporal: 2010 até o último ano CALENDÁRIO COMPLETO disponível
(mesmo critério usado no SIH/SINAN — scripts/download/saude_sih_*.py) —
detectado verificando a existência do arquivo de dezembro de cada ano
candidato via HTTP HEAD, não hardcoded.

PROBLEMA CONHECIDO / NÃO RESOLVIDO (2026-08-07): data.chc.ucsb.edu aplica
bloqueio de IP (HTTP 403, sem corpo, sem header Retry-After) depois de só
~3 meses processados em sequência rápida — cada mês faz 1 HEAD + várias
requisições GET (range requests) do GDAL por trás do /vsigzip//vsicurl/,
então uma rajada de ~15 requisições em poucos segundos já é suficiente
pra disparar o bloqueio. Testado com 45s de espera entre requisições
depois do bloqueio: continuou 403 — não é rate-limit de curto prazo, é
bloqueio mais duradouro (duração exata desconhecida). Antes de rodar de
novo: (1) espaçar bem mais as requisições (ex. `time.sleep` de alguns
segundos entre meses, a implementar), e/ou (2) considerar alternativa
citada no pedido original (INPE/MERGE) se o bloqueio persistir, e/ou
(3) usar outro canal de distribuição do CHIRPS (ex. AWS Open Data
Registry, Google Earth Engine) em vez do data.chc.ucsb.edu direto.

Uso:
    python scripts/download/precipitacao_chirps.py
    python scripts/download/precipitacao_chirps.py --codigo-ibge 4314902 --forcar
"""

import argparse
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import pandas as pd
import requests
from rasterstats import zonal_stats

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

RAIZ = Path(__file__).resolve().parents[2]
CAMINHO_AREA_BACIAS_DEFAULT = RAIZ / "config" / "area_estudo_bacias.geojson"
CAMINHO_SAIDA_TEMPLATE = RAIZ / "data" / "raw" / "precipitacao_chirps_{inicio}-{fim}_mensal.csv"

BASE_URL_HTTP = "https://data.chc.ucsb.edu/products/CHIRPS-2.0/global_monthly/tifs/chirps-v2.0.{ano}.{mes:02d}.tif.gz"
NODATA_CHIRPS = -9999
ANO_INICIO_DEFAULT = 2010
N_TENTATIVAS = 3
BACKOFF_BASE_S = 2.0

HEADERS = {"User-Agent": "Mozilla/5.0 (compativel; script-pesquisa-ClimaPampa/1.0)"}
URL_MUNICIPIO_IBGE = "https://servicodados.ibge.gov.br/api/v1/localidades/municipios/{codigo}"


def obter_uf_sigla(codigo_ibge: str) -> tuple[str, str]:
    resposta = requests.get(URL_MUNICIPIO_IBGE.format(codigo=codigo_ibge), headers=HEADERS, timeout=30)
    resposta.raise_for_status()
    dados = resposta.json()
    return dados["nome"], dados["microrregiao"]["mesorregiao"]["UF"]["sigla"]


def arquivo_existe(ano: int, mes: int) -> bool:
    resposta = requests.head(BASE_URL_HTTP.format(ano=ano, mes=mes), headers=HEADERS, timeout=30)
    return resposta.status_code == 200


def descobrir_ultimo_ano_completo() -> int:
    """Verifica (via HTTP HEAD) o dezembro de anos candidatos, do mais recente para trás."""
    ano = datetime.now(timezone.utc).year - 1
    while ano >= 1981:
        if arquivo_existe(ano, 12):
            return ano
        ano -= 1
    raise RuntimeError("Não encontrou nenhum ano completo de CHIRPS — verifique conectividade/URL da fonte.")


def extrair_precipitacao_mes(ano: int, mes: int, area_4326: gpd.GeoDataFrame) -> dict | None:
    """Extrai estatísticas zonais de precipitação para um mês, com retry. None se o mês não existir na fonte."""
    if not arquivo_existe(ano, mes):
        logger.warning("CHIRPS %d-%02d: arquivo não encontrado na fonte.", ano, mes)
        return None

    url_vsi = "/vsigzip//vsicurl/" + BASE_URL_HTTP.format(ano=ano, mes=mes)

    ultimo_erro: Exception | None = None
    for tentativa in range(1, N_TENTATIVAS + 1):
        try:
            stats = zonal_stats(area_4326, url_vsi, stats=["mean", "min", "max", "count"], nodata=NODATA_CHIRPS)
            return stats[0]
        except Exception as erro:  # noqa: BLE001 — qualquer falha de rede/leitura remota entra no retry
            ultimo_erro = erro
            if tentativa < N_TENTATIVAS:
                espera = BACKOFF_BASE_S * (2 ** (tentativa - 1))
                logger.warning(
                    "Falha ao ler CHIRPS %d-%02d (tentativa %d/%d): %s — nova tentativa em %.0fs",
                    ano, mes, tentativa, N_TENTATIVAS, erro, espera,
                )
                time.sleep(espera)
    raise RuntimeError(f"Falha ao ler CHIRPS {ano}-{mes:02d} após {N_TENTATIVAS} tentativas: {ultimo_erro}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Baixa série mensal de precipitação CHIRPS para a área de estudo estendida.")
    parser.add_argument("--codigo-ibge", default="4322400", help="Código IBGE do município (só para rótulo/metadado — a área usada é --area-bacias)")
    parser.add_argument("--area-bacias", type=Path, default=CAMINHO_AREA_BACIAS_DEFAULT)
    parser.add_argument("--ano-inicio", type=int, default=ANO_INICIO_DEFAULT)
    parser.add_argument("--ano-fim", type=int, default=None, help="Default: último ano calendário completo disponível na fonte")
    parser.add_argument("--forcar", action="store_true")
    args = parser.parse_args()

    caminho_saida_provisorio = None
    if not args.forcar:
        # sem saber ano_fim ainda, procura qualquer saída já existente que comece com o ano_inicio pedido
        existentes = list(RAIZ.glob(f"data/raw/precipitacao_chirps_{args.ano_inicio}-*_mensal.csv"))
        if existentes:
            logger.info("Já existe %s — nada a fazer (use --forcar para refazer).", existentes[0])
            return

    if not args.area_bacias.exists():
        raise FileNotFoundError(
            f"{args.area_bacias} não encontrado. Rode primeiro: python scripts/processamento/area_estudo_bacias.py"
        )

    nome_municipio, uf = obter_uf_sigla(args.codigo_ibge)
    area_estudo_bacias = gpd.read_file(args.area_bacias)

    ano_fim = args.ano_fim or descobrir_ultimo_ano_completo()
    logger.info("Município de referência: %s (%s) — janela: %d-%d", nome_municipio, uf, args.ano_inicio, ano_fim)

    # a área só precisa ser reprojetada uma vez (CHIRPS é sempre EPSG:4326)
    area_4326 = area_estudo_bacias.to_crs("EPSG:4326")

    linhas = []
    meses_sem_arquivo = []
    for ano in range(args.ano_inicio, ano_fim + 1):
        for mes in range(1, 13):
            stats = extrair_precipitacao_mes(ano, mes, area_4326)
            if stats is None or stats.get("mean") is None:
                meses_sem_arquivo.append(f"{ano}-{mes:02d}")
                linhas.append({
                    "ano": ano, "mes": mes, "codigo_ibge": args.codigo_ibge, "nome_municipio": nome_municipio,
                    "precipitacao_media_mm": pd.NA, "precipitacao_min_mm": pd.NA, "precipitacao_max_mm": pd.NA,
                    "n_pixels": pd.NA,
                })
                continue
            linhas.append({
                "ano": ano, "mes": mes, "codigo_ibge": args.codigo_ibge, "nome_municipio": nome_municipio,
                "precipitacao_media_mm": round(stats["mean"], 2),
                "precipitacao_min_mm": round(stats["min"], 2),
                "precipitacao_max_mm": round(stats["max"], 2),
                "n_pixels": stats["count"],
            })
        logger.info("Ano %d processado.", ano)

    tabela = pd.DataFrame(linhas)
    caminho_saida = Path(str(CAMINHO_SAIDA_TEMPLATE).format(inicio=args.ano_inicio, fim=ano_fim))
    caminho_saida.parent.mkdir(parents=True, exist_ok=True)
    tabela.to_csv(caminho_saida, index=False, encoding="utf-8")
    logger.info("Salvo: %s (%d meses, %d sem arquivo disponível)", caminho_saida, len(tabela), len(meses_sem_arquivo))

    metadados = {
        "fonte": "CHIRPS v2.0 (Climate Hazards Group InfraRed Precipitation with Station data, UCSB) — https://data.chc.ucsb.edu/products/CHIRPS-2.0/global_monthly/tifs/",
        "licenca": "domínio público / uso livre (Climate Hazards Center, UCSB)",
        "resolucao_espacial_original": "~0,05° (~5,5 km no equador)",
        "resolucao_temporal": "mensal",
        "metodo": (
            "leitura em streaming via GDAL (/vsigzip//vsicurl/) de um GeoTIFF global por mês, sem "
            "baixar o arquivo inteiro; média espacial (rasterstats.zonal_stats) da precipitação (mm) "
            "dentro da área de estudo ESTENDIDA (buffer de 18 km sobre o limite municipal — "
            "config/area_estudo_bacias.geojson), reprojetada para EPSG:4326 (CRS nativo do CHIRPS)"
        ),
        "codigo_ibge_referencia": args.codigo_ibge,
        "nome_municipio_referencia": nome_municipio,
        "uf": uf,
        "area_usada": "config/area_estudo_bacias.geojson (buffer de 18 km sobre area_estudo.geojson — NÃO é o limite municipal estrito)",
        "periodo_coberto": f"{args.ano_inicio}-{ano_fim}",
        "criterio_ano_fim": "último ano calendário com os 12 meses publicados na fonte no momento da coleta (verificado via HTTP HEAD do arquivo de dezembro)",
        "nivel_agregacao": "média espacial sobre a área de estudo estendida, por mês — não há série por pixel/sub-área neste CSV (os GeoTIFFs de origem não são salvos localmente, só lidos em streaming)",
        "nodata": f"{NODATA_CHIRPS} (não vem marcado no header do GeoTIFF; tratado explicitamente na extração zonal)",
        "meses_sem_arquivo_disponivel": meses_sem_arquivo,
        "data_processamento": datetime.now(timezone.utc).isoformat(),
    }
    caminho_metadados = caminho_saida.with_suffix(".json")
    caminho_metadados.write_text(json.dumps(metadados, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    logger.info("Metadados salvos em %s", caminho_metadados)


if __name__ == "__main__":
    main()
