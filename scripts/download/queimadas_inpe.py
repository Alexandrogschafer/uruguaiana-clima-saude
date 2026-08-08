"""Baixa focos de queimada do INPE (Programa Queimadas / BDQUEIMADAS) para
a área de estudo, de 2010 até o último ano completo disponível. Gera:

    data/raw/focos-queimada_inpe_2010-{ano_fim}_pontual.csv
    data/raw/focos-queimada_inpe_2010-{ano_fim}_pontual.json

Fonte e método
--------------
Servidor de dados abertos do INPE/CGIP/COIDS, um .zip por ano (todos os
satélites, Brasil inteiro) em
https://dataserver-coids.inpe.br/queimadas/queimadas/focos/csv/anual/Brasil_todos_sats/
focos_br_todos-sats_{ano}.zip — validado por HEAD real antes de codificar
(NÃO documentado como API formal; é um servidor de arquivos estáticos).
Cada .zip contém um único CSV nacional (~1-5 milhões de focos/ano em todo
o Brasil); o arquivo já vem com uma coluna `municipio`, mas o filtro usado
aqui é ESPACIAL (ponto dentro do polígono de
scripts/utils/recorte_municipio.carregar_area_estudo — o limite municipal
oficial, não o buffer estendido usado para bacias/terreno), para não
depender de correspondência exata de nome de município (maiúsculas,
acentuação) — validado comparando os dois métodos para 2023: resultado
idêntico (138 focos).

Dado pontual: mantém latitude/longitude de cada foco (por isso o sufixo
"_pontual" no nome do arquivo, diferente das séries "_municipal" do SIM/
SINAN/SIH, que são só contagens agregadas) — dá pra reabrir como pontos
em qualquer ferramenta de SIG.

Otimização: antes do point-in-polygon (`geopandas.sjoin`, que cria uma
geometria Shapely por linha), filtra por bounding box do município via
comparação numérica direta em latitude/longitude — evita instanciar
milhões de geometrias desnecessárias para descartar focos claramente fora
da área.

Janela: 2010 até o último ano com .zip anual publicado (tipicamente o
último ano civil fechado — o arquivo do ano corrente só é publicado
depois que o ano termina).

Idempotente: os .zip anuais nacionais ficam em cache local
(data/raw/cache_queimadas_inpe/); a saída final não é reprocessada se já
existir (a menos que --forcar seja usado).

Uso:
    python scripts/download/queimadas_inpe.py
    python scripts/download/queimadas_inpe.py --codigo-ibge 4314902 --forcar
"""

import argparse
import json
import logging
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import pandas as pd
import requests

sys.path.append(str(Path(__file__).resolve().parents[1] / "utils"))
from recorte_municipio import carregar_area_estudo  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

RAIZ = Path(__file__).resolve().parents[2]
CACHE_DIR = RAIZ / "data" / "raw" / "cache_queimadas_inpe"
CAMINHO_SAIDA_TEMPLATE = RAIZ / "data" / "raw" / "focos-queimada_inpe_{inicio}-{fim}_pontual.csv"

BASE_URL = "https://dataserver-coids.inpe.br/queimadas/queimadas/focos/csv/anual/Brasil_todos_sats/focos_br_todos-sats_{ano}.zip"
CODIGO_IBGE_DEFAULT = "4322400"
ANO_INICIO_DEFAULT = 2010
N_TENTATIVAS = 3
BACKOFF_BASE_S = 2.0
HEADERS = {"User-Agent": "Mozilla/5.0 (compativel; script-pesquisa-ClimaPampa/1.0)"}
URL_MUNICIPIO_IBGE = "https://servicodados.ibge.gov.br/api/v1/localidades/municipios/{codigo}"

COLUNAS_USO = [
    "latitude", "longitude", "data_pas", "satelite", "municipio", "bioma",
    "numero_dias_sem_chuva", "precipitacao", "risco_fogo", "frp",
]


def obter_nome_municipio(codigo_ibge: str) -> str:
    resposta = requests.get(URL_MUNICIPIO_IBGE.format(codigo=codigo_ibge), headers=HEADERS, timeout=30)
    resposta.raise_for_status()
    return resposta.json()["nome"]


def descobrir_ultimo_ano_completo() -> int:
    ano = datetime.now(timezone.utc).year - 1
    while ano >= 1998:
        resposta = requests.head(BASE_URL.format(ano=ano), headers=HEADERS, timeout=30)
        if resposta.status_code == 200:
            return ano
        ano -= 1
    raise RuntimeError("Não encontrou nenhum ano completo de focos de queimada — verifique conectividade/URL da fonte.")


def baixar_zip_ano(ano: int) -> Path:
    destino = CACHE_DIR / f"focos_br_todos-sats_{ano}.zip"
    if destino.exists():
        logger.info("Ano %d já em cache: %s", ano, destino)
        return destino

    destino.parent.mkdir(parents=True, exist_ok=True)
    destino_tmp = destino.with_suffix(".tmp")
    ultimo_erro: Exception | None = None
    for tentativa in range(1, N_TENTATIVAS + 1):
        try:
            with requests.get(BASE_URL.format(ano=ano), headers=HEADERS, stream=True, timeout=180) as resposta:
                resposta.raise_for_status()
                with open(destino_tmp, "wb") as f:
                    for bloco in resposta.iter_content(chunk_size=1024 * 1024):
                        f.write(bloco)
            destino_tmp.rename(destino)
            logger.info("Baixado ano %d -> %s", ano, destino)
            return destino
        except requests.RequestException as erro:
            ultimo_erro = erro
            destino_tmp.unlink(missing_ok=True)
            if tentativa < N_TENTATIVAS:
                espera = BACKOFF_BASE_S * (2 ** (tentativa - 1))
                logger.warning("Falha ao baixar ano %d (tentativa %d/%d): %s — nova tentativa em %.0fs", ano, tentativa, N_TENTATIVAS, erro, espera)
                time.sleep(espera)
    raise RuntimeError(f"Falha ao baixar focos de {ano} após {N_TENTATIVAS} tentativas: {ultimo_erro}")


def filtrar_ano(caminho_zip: Path, ano: int, area_estudo_4326: gpd.GeoDataFrame) -> pd.DataFrame:
    with zipfile.ZipFile(caminho_zip) as zf:
        nome_csv = next(n for n in zf.namelist() if n.endswith(".csv"))
        with zf.open(nome_csv) as f:
            df = pd.read_csv(f, usecols=COLUNAS_USO)

    minx, miny, maxx, maxy = area_estudo_4326.total_bounds
    candidatos = df[
        df["longitude"].between(minx, maxx) & df["latitude"].between(miny, maxy)
    ].copy()
    if candidatos.empty:
        logger.info("Ano %d: %d focos no Brasil, 0 candidatos no bbox do município", ano, len(df))
        return candidatos.assign(ano=ano)

    pontos = gpd.GeoDataFrame(
        candidatos, geometry=gpd.points_from_xy(candidatos["longitude"], candidatos["latitude"]), crs="EPSG:4326"
    )
    dentro = gpd.sjoin(pontos, area_estudo_4326[["geometry"]], predicate="within", how="inner").drop(columns=["index_right"])
    dentro = pd.DataFrame(dentro.drop(columns="geometry"))
    dentro["ano"] = ano
    logger.info("Ano %d: %d focos no Brasil, %d no município", ano, len(df), len(dentro))
    return dentro


def main() -> None:
    parser = argparse.ArgumentParser(description="Baixa focos de queimada do INPE para a área de estudo.")
    parser.add_argument("--codigo-ibge", default=CODIGO_IBGE_DEFAULT)
    parser.add_argument("--ano-inicio", type=int, default=ANO_INICIO_DEFAULT)
    parser.add_argument("--ano-fim", type=int, default=None, help="Default: último ano com .zip anual publicado na fonte")
    parser.add_argument("--forcar", action="store_true")
    args = parser.parse_args()

    ano_fim = args.ano_fim or descobrir_ultimo_ano_completo()
    caminho_saida = Path(str(CAMINHO_SAIDA_TEMPLATE).format(inicio=args.ano_inicio, fim=ano_fim))
    if caminho_saida.exists() and not args.forcar:
        logger.info("%s já existe — nada a fazer (use --forcar para refazer).", caminho_saida)
        return

    nome_municipio = obter_nome_municipio(args.codigo_ibge)
    area_estudo = carregar_area_estudo()  # EPSG:31981 (padrão do projeto)
    area_estudo_4326 = area_estudo.to_crs("EPSG:4326")
    logger.info("Município: %s — código IBGE %s — janela: %d-%d", nome_municipio, args.codigo_ibge, args.ano_inicio, ano_fim)

    partes = []
    for ano in range(args.ano_inicio, ano_fim + 1):
        caminho_zip = baixar_zip_ano(ano)
        partes.append(filtrar_ano(caminho_zip, ano, area_estudo_4326))

    tabela = pd.concat(partes, ignore_index=True) if partes else pd.DataFrame(columns=[*COLUNAS_USO, "ano"])
    tabela.insert(0, "codigo_ibge", args.codigo_ibge)
    tabela.insert(1, "nome_municipio", nome_municipio)
    tabela = tabela.sort_values("data_pas").reset_index(drop=True)

    caminho_saida.parent.mkdir(parents=True, exist_ok=True)
    tabela.to_csv(caminho_saida, index=False, encoding="utf-8")
    logger.info("Salvo: %s (%d focos)", caminho_saida, len(tabela))

    focos_por_ano = tabela.groupby("ano").size().to_dict()
    metadados = {
        "fonte": "INPE — Programa Queimadas (BDQUEIMADAS), dados abertos anuais 'todos os satélites' — https://dataserver-coids.inpe.br/queimadas/queimadas/focos/csv/anual/Brasil_todos_sats/",
        "licenca": "dados abertos (INPE)",
        "metodo": (
            "download do .zip anual nacional (todos os satélites), filtro espacial (ponto dentro do "
            "polígono de config/area_estudo.geojson, limite municipal oficial — não o buffer estendido "
            "usado para bacias/terreno), com pré-filtro por bounding box antes do point-in-polygon para "
            "performance; validado comparando com filtro por nome de município (coluna 'municipio' da "
            "própria fonte) para 2023 — resultado idêntico (138 focos)"
        ),
        "codigo_ibge": args.codigo_ibge,
        "nome_municipio": nome_municipio,
        "periodo_coberto": f"{args.ano_inicio}-{ano_fim}",
        "criterio_ano_fim": "último ano com .zip anual publicado na fonte (arquivo do ano corrente só sai depois que o ano termina)",
        "nivel_agregacao": "pontual (um registro por foco detectado, com latitude/longitude) — dado espacializável, não agregado",
        "colunas": {
            "data_pas": "data/hora de passagem do satélite (UTC)",
            "satelite": "satélite/sensor que detectou o foco (arquivo 'todos os satélites', não só o de referência)",
            "numero_dias_sem_chuva": "dias consecutivos sem chuva no ponto até a detecção",
            "risco_fogo": "índice de risco de fogo (0-1) calculado pelo INPE",
            "frp": "potência radiativa do fogo (Fire Radiative Power, MW) — proxy de intensidade",
        },
        "n_focos_por_ano": focos_por_ano,
        "data_processamento": datetime.now(timezone.utc).isoformat(),
    }
    caminho_metadados = caminho_saida.with_suffix(".json")
    caminho_metadados.write_text(json.dumps(metadados, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    logger.info("Metadados salvos em %s", caminho_metadados)


if __name__ == "__main__":
    main()
