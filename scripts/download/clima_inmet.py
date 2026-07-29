"""
Baixa dados climáticos do INMET (Instituto Nacional de Meteorologia) para a
estação automática mais relevante de um município e gera:

    data/raw/vetor/estacoes-clima_inmet_atual_vetorial.gpkg   (localização da(s) estação(ões))
    data/raw/clima_inmet_historico-completo_horario.parquet   (série temporal)

Fontes de dados
---------------
1. Lista de estações automáticas (sem autenticação):
   GET https://apitempo.inmet.gov.br/estacoes/T
   Usada apenas para identificar código/nome/coordenadas da estação — não é
   usada para baixar a série histórica (ver item 2).

2. Série histórica (sem autenticação): portal público de dados históricos
   GET https://portal.inmet.gov.br/uploads/dadoshistoricos/{ano}.zip
   Cada zip anual contém um CSV por estação meteorológica do Brasil inteiro
   (nome do arquivo contém o código da estação, ex. "..._A809_..."). Este
   script lê apenas os bytes necessários de cada zip via HTTP Range
   requests (o servidor anuncia `Accept-Ranges: bytes`), extraindo só o CSV
   da estação de interesse — evita baixar ~100 MB por ano quando o CSV de
   uma estação tem ~1 MB.

   Observação importante já verificada por engenharia reversa da API de
   dados "ao vivo" (apitempo.inmet.gov.br): os endpoints que servem séries
   históricas por ali (`/estacao/diaria/front/`, `/dado/estacao/`) exigem
   um token de reCAPTCHA resolvido no navegador, e o endpoint legado sem
   token (`/estacao/{inicio}/{fim}/{codigo}`) está desativado (sempre
   retorna 204). Por isso este script usa o portal de dados históricos
   (arquivos .zip), que é público e não exige nenhuma autenticação.

Granularidade real dos dados
-----------------------------
Os arquivos do portal são **horários** (não diários) — cada estação tem uma
linha por hora, em UTC. Não existe agregação diária pronta nesses arquivos;
por isso a saída deste script também é horária (ver nome do arquivo final).

Formato do CSV muda ao longo dos anos (confirmado testando os zips de 2006
a 2026): até ~2018 o cabeçalho é "DATA (YYYY-MM-DD)"/"HORA (UTC)" com data
"AAAA-MM-DD", hora "HH:MM" e valores faltantes como "-9999"; de 2019 em
diante o cabeçalho é "Data"/"Hora UTC" com data "AAAA/MM/DD", hora
"HHMM UTC" e valores faltantes em branco. As demais colunas (variáveis
meteorológicas) têm o mesmo significado e ordem em todos os anos — por
isso este script renomeia colunas por posição, não por nome exato, e trata
os dois formatos de data/hora e os dois marcadores de dado faltante.

Idempotência por ano
---------------------
Diferente dos outros scripts deste projeto, este é idempotente por ANO, não
por arquivo final: cada ano processado é cacheado em
`data/raw/cache_inmet_por_ano/{codigo_estacao}_{ano}.parquet`. Rodar o
script de novo não rebaixa anos já processados (o zip anual não é buscado
de novo); apenas concatena o cache existente com eventuais anos novos. Use
--forcar para ignorar o cache e reprocessar tudo.

Uso:
    python scripts/download/clima_inmet.py
    python scripts/download/clima_inmet.py --codigo-ibge 4314902 --forcar
"""

import argparse
import io
import json
import logging
import sys
import time
import unicodedata
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import requests
from pyproj import Geod
from shapely.geometry import Point

sys.path.append(str(Path(__file__).resolve().parents[1] / "utils"))
from recorte_municipio import CRS_PADRAO, carregar_area_estudo  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

CODIGO_IBGE_DEFAULT = "4322400"  # Uruguaiana, RS

URL_ESTACOES = "https://apitempo.inmet.gov.br/estacoes/T"
URL_MUNICIPIO_IBGE = "https://servicodados.ibge.gov.br/api/v1/localidades/municipios/{codigo}"
URL_ZIP_TEMPLATE = "https://portal.inmet.gov.br/uploads/dadoshistoricos/{ano}.zip"

HEADERS = {"User-Agent": "Mozilla/5.0 (compativel; script-pesquisa-ClimaPampa/1.0)"}

N_TENTATIVAS = 3
BACKOFF_BASE_S = 1.0  # 1s, 2s, 4s

CAMINHO_ESTACOES_DEFAULT = (
    Path(__file__).resolve().parents[2] / "data" / "raw" / "vetor" / "estacoes-clima_inmet_atual_vetorial"
)
CAMINHO_SERIE_DEFAULT = (
    Path(__file__).resolve().parents[2] / "data" / "raw" / "clima_inmet_historico-completo_horario"
)
CAMINHO_CACHE_ANOS = Path(__file__).resolve().parents[2] / "data" / "raw" / "cache_inmet_por_ano"

# Colunas do CSV do INMET, por posição (a 20ª coluna é vazia, criada pelo
# ";" final de cada linha, e é descartada). O texto exato das duas
# primeiras colunas e a caixa de "Kj"/"KJ" mudam entre eras do arquivo,
# por isso o mapeamento é posicional, não por nome.
COLUNAS_RENOMEADAS = [
    "data_raw", "hora_raw", "precipitacao_mm", "pressao_mb", "pressao_max_mb", "pressao_min_mb",
    "radiacao_kj_m2", "temp_ar_c", "temp_orvalho_c", "temp_max_c", "temp_min_c",
    "temp_orvalho_max_c", "temp_orvalho_min_c", "umidade_max_pct", "umidade_min_pct", "umidade_pct",
    "vento_direcao_gr", "vento_rajada_max_ms", "vento_velocidade_ms",
]
COLUNAS_NUMERICAS = COLUNAS_RENOMEADAS[2:]


class HTTPRangeFile(io.RawIOBase):
    """Arquivo remoto lido sob demanda via HTTP Range requests.

    Permite ao `zipfile` ler apenas o índice central do zip e os bytes do
    membro desejado, sem baixar o arquivo inteiro (~100 MB por ano).
    """

    def __init__(self, url: str, sessao: requests.Session):
        self.url = url
        self.sessao = sessao
        self.pos = 0
        resposta = _requisitar_com_retry(sessao, "HEAD", url)
        self.size = int(resposta.headers["Content-Length"])

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def seek(self, offset: int, whence: int = 0) -> int:
        if whence == 0:
            self.pos = offset
        elif whence == 1:
            self.pos += offset
        elif whence == 2:
            self.pos = self.size + offset
        return self.pos

    def tell(self) -> int:
        return self.pos

    def readinto(self, b) -> int:
        fim = min(self.pos + len(b), self.size) - 1
        if self.pos > fim:
            return 0
        resposta = _requisitar_com_retry(self.sessao, "GET", self.url, headers={"Range": f"bytes={self.pos}-{fim}"})
        dados = resposta.content
        b[: len(dados)] = dados
        self.pos += len(dados)
        return len(dados)


def _requisitar_com_retry(sessao: requests.Session, metodo: str, url: str, **kwargs) -> requests.Response:
    """GET/HEAD com retry e backoff exponencial simples (1s, 2s, 4s)."""
    ultimo_erro = None
    for tentativa in range(1, N_TENTATIVAS + 1):
        try:
            resposta = sessao.request(metodo, url, timeout=60, **kwargs)
            resposta.raise_for_status()
            return resposta
        except requests.RequestException as erro:
            ultimo_erro = erro
            if tentativa < N_TENTATIVAS:
                espera = BACKOFF_BASE_S * (2 ** (tentativa - 1))
                logger.warning(
                    "Falha na requisição %s %s (tentativa %d/%d): %s — nova tentativa em %.0fs",
                    metodo, url, tentativa, N_TENTATIVAS, erro, espera,
                )
                time.sleep(espera)
    raise RuntimeError(f"Falha ao requisitar {url} após {N_TENTATIVAS} tentativas: {ultimo_erro}") from ultimo_erro


def _normalizar_texto(texto: str) -> str:
    """Remove acentos e caixa para comparação robusta de nomes de município/estação."""
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    return sem_acento.strip().upper()


def obter_municipio_ibge(codigo_ibge: str) -> tuple[str | None, str | None]:
    """Retorna (nome, sigla_uf) do município via API de localidades do IBGE, ou (None, None) se falhar."""
    url = URL_MUNICIPIO_IBGE.format(codigo=codigo_ibge)
    try:
        resposta = _requisitar_com_retry(requests.Session(), "GET", url, headers=HEADERS)
        dados = resposta.json()
        nome = dados["nome"]
        uf = dados["microrregiao"]["mesorregiao"]["UF"]["sigla"]
        return nome, uf
    except (requests.RequestException, KeyError, RuntimeError) as erro:
        logger.warning("Não foi possível obter nome/UF do município %s via IBGE: %s", codigo_ibge, erro)
        return None, None


def buscar_estacoes_automaticas(sessao: requests.Session) -> list[dict]:
    logger.info("Buscando lista de estações automáticas do INMET — %s", URL_ESTACOES)
    resposta = _requisitar_com_retry(sessao, "GET", URL_ESTACOES, headers=HEADERS)
    estacoes = resposta.json()
    logger.info("Lista recebida: %d estações automáticas no Brasil", len(estacoes))
    return estacoes


def selecionar_estacoes(
    estacoes: list[dict], nome_municipio: str | None, uf: str | None, centroide_lon: float, centroide_lat: float
) -> list[dict]:
    """Prioriza estação(ões) cujo nome bate com o município; senão, a mais próxima do centróide.

    Restringe a busca à UF do município quando esta é conhecida (evita
    escolher uma estação de outro estado por coincidência de nome/distância
    em municípios de fronteira estadual). Se a UF não puder ser determinada,
    busca em todas as estações do Brasil.
    """
    candidatas = [e for e in estacoes if uf is None or e["SG_ESTADO"] == uf]
    if not candidatas:
        candidatas = estacoes  # fallback: UF sem estações automáticas cadastradas (improvável)

    geod = Geod(ellps="WGS84")
    for e in candidatas:
        lat_e, lon_e = float(e["VL_LATITUDE"]), float(e["VL_LONGITUDE"])
        _, _, distancia_m = geod.inv(centroide_lon, centroide_lat, lon_e, lat_e)
        e["_distancia_km"] = distancia_m / 1000

    if nome_municipio is not None:
        alvo = _normalizar_texto(nome_municipio)
        correspondentes = [e for e in candidatas if _normalizar_texto(e["DC_NOME"]) == alvo]
        if correspondentes:
            for e in correspondentes:
                e["_criterio_selecao"] = "estacao_no_proprio_municipio"
            logger.info(
                "Estação(ões) encontrada(s) no próprio município (%s): %s",
                nome_municipio, [e["CD_ESTACAO"] for e in correspondentes],
            )
            return correspondentes

    mais_proxima = min(candidatas, key=lambda e: e["_distancia_km"])
    mais_proxima["_criterio_selecao"] = "mais_proxima_por_distancia"
    logger.info(
        "Nenhuma estação com nome igual ao município — usando a mais próxima: %s (%s) a %.1f km",
        mais_proxima["CD_ESTACAO"], mais_proxima["DC_NOME"], mais_proxima["_distancia_km"],
    )
    return [mais_proxima]


def _normalizar_data_hora(df: pd.DataFrame) -> pd.Series:
    """Constrói datetime UTC a partir de data_raw/hora_raw, cobrindo os dois formatos históricos.

    Formato antigo (<=2018): data "AAAA-MM-DD", hora "HH:MM".
    Formato novo (>=2019): data "AAAA/MM/DD", hora "HHMM UTC".
    Em ambos os casos, extrair apenas dígitos da hora e normalizar para
    "HH:MM" resolve a diferença de forma robusta.
    """
    data_norm = df["data_raw"].astype(str).str.replace("/", "-", regex=False)
    hora_digitos = df["hora_raw"].astype(str).str.replace(r"\D", "", regex=True).str.zfill(4)
    hora_norm = hora_digitos.str[:2] + ":" + hora_digitos.str[2:4]
    return pd.to_datetime(data_norm + " " + hora_norm, format="%Y-%m-%d %H:%M", utc=True, errors="coerce")


def baixar_e_processar_ano(codigo_estacao: str, ano: int, sessao: requests.Session) -> pd.DataFrame | None:
    """Lê (via HTTP Range) o CSV da estação dentro do zip anual e retorna um DataFrame processado.

    Retorna None se o zip do ano não contiver a estação (ex.: estação ainda
    não existia ou já havia sido desativada naquele ano).
    """
    url = URL_ZIP_TEMPLATE.format(ano=ano)
    arquivo_remoto = HTTPRangeFile(url, sessao)
    with zipfile.ZipFile(arquivo_remoto) as zf:
        membros = [n for n in zf.namelist() if codigo_estacao in n]
        if not membros:
            return None
        bruto = zf.read(membros[0])

    df = pd.read_csv(
        io.BytesIO(bruto), sep=";", decimal=",", encoding="latin1", skiprows=8, header=0,
    )
    if len(df.columns) < len(COLUNAS_RENOMEADAS):
        logger.warning(
            "Ano %d: CSV da estação %s tem %d colunas (esperado >= %d) — pulando ano.",
            ano, codigo_estacao, len(df.columns), len(COLUNAS_RENOMEADAS),
        )
        return None

    df = df.iloc[:, : len(COLUNAS_RENOMEADAS)].copy()
    df.columns = COLUNAS_RENOMEADAS
    df[COLUNAS_NUMERICAS] = df[COLUNAS_NUMERICAS].replace(-9999, np.nan)
    df["datetime_utc"] = _normalizar_data_hora(df)
    df = df.drop(columns=["data_raw", "hora_raw"])
    df["codigo_estacao"] = codigo_estacao
    return df


def montar_serie_estacao(estacao: dict, ano_inicio: int, ano_final: int, sessao: requests.Session, forcar: bool) -> tuple[pd.DataFrame, list[int]]:
    """Monta a série horária completa de uma estação, usando cache por ano em disco."""
    codigo = estacao["CD_ESTACAO"]
    CAMINHO_CACHE_ANOS.mkdir(parents=True, exist_ok=True)

    dfs_ano = []
    anos_ausentes = []
    for ano in range(ano_inicio, ano_final + 1):
        caminho_cache = CAMINHO_CACHE_ANOS / f"{codigo}_{ano}.parquet"
        if caminho_cache.exists() and not forcar:
            dfs_ano.append(pd.read_parquet(caminho_cache))
            continue

        logger.info("Estação %s: baixando/processando ano %d...", codigo, ano)
        df_ano = baixar_e_processar_ano(codigo, ano, sessao)
        if df_ano is None:
            logger.warning("Estação %s: arquivo não encontrado no zip de %d (estação inativa nesse período?) — ano pulado.", codigo, ano)
            anos_ausentes.append(ano)
            continue

        df_ano.to_parquet(caminho_cache, index=False)
        dfs_ano.append(df_ano)

    if not dfs_ano:
        return pd.DataFrame(columns=[*COLUNAS_RENOMEADAS[2:], "datetime_utc", "codigo_estacao"]), anos_ausentes

    serie = pd.concat(dfs_ano, ignore_index=True).sort_values("datetime_utc")
    return serie, anos_ausentes


def salvar_estacoes(estacoes_selecionadas: list[dict], caminho_base: Path) -> None:
    registros = [
        {
            "codigo_estacao": e["CD_ESTACAO"],
            "nome_estacao": e["DC_NOME"],
            "uf": e["SG_ESTADO"],
            "situacao": e["CD_SITUACAO"],
            "tipo_estacao": e["TP_ESTACAO"],
            "altitude_m": float(e["VL_ALTITUDE"]),
            "data_inicio_operacao": e["DT_INICIO_OPERACAO"],
            "data_fim_operacao": e["DT_FIM_OPERACAO"],
            "distancia_centroide_km": round(e["_distancia_km"], 3),
            "criterio_selecao": e["_criterio_selecao"],
            "geometry": Point(float(e["VL_LONGITUDE"]), float(e["VL_LATITUDE"])),
        }
        for e in estacoes_selecionadas
    ]
    gdf = gpd.GeoDataFrame(registros, geometry="geometry", crs="EPSG:4326").to_crs(CRS_PADRAO)

    caminho_base.parent.mkdir(parents=True, exist_ok=True)
    caminho_gpkg = caminho_base.with_suffix(".gpkg")
    gdf.to_file(caminho_gpkg, driver="GPKG", layer="estacoes_clima_inmet")
    logger.info("Estações salvas em %s (CRS: %s, %d estação(ões))", caminho_gpkg, CRS_PADRAO, len(gdf))


def salvar_serie(serie: pd.DataFrame, caminho_base: Path) -> None:
    caminho_base.parent.mkdir(parents=True, exist_ok=True)
    caminho_parquet = caminho_base.with_suffix(".parquet")
    colunas_ordenadas = ["codigo_estacao", "datetime_utc", *COLUNAS_NUMERICAS]
    serie[colunas_ordenadas].to_parquet(caminho_parquet, index=False)
    logger.info("Série temporal salva em %s (%d registros)", caminho_parquet, len(serie))


def calcular_metadados(
    serie: pd.DataFrame,
    estacoes_selecionadas: list[dict],
    codigo_ibge: str,
    nome_municipio: str | None,
    uf: str | None,
    centroide_lon: float,
    centroide_lat: float,
    ano_inicio_solicitado: int,
    ano_final: int,
    anos_ausentes_por_estacao: dict,
    caminho_parquet: Path,
) -> dict:
    pct_faltantes = (serie[COLUNAS_NUMERICAS].isna().mean() * 100).round(2).to_dict() if len(serie) else {}

    return {
        "fonte": (
            "INMET — lista de estações via apitempo.inmet.gov.br/estacoes/T (sem token) "
            "e série histórica via portal.inmet.gov.br/dadoshistoricos (zips anuais públicos)"
        ),
        "url_lista_estacoes": URL_ESTACOES,
        "url_zip_template": URL_ZIP_TEMPLATE,
        "observacao_metodo": (
            "os zips anuais (~100MB, todas as estações do Brasil) não são baixados por inteiro: "
            "o script lê apenas os bytes da estação de interesse via HTTP Range requests"
        ),
        "granularidade": "horária (UTC) — não há dado diário agregado nos arquivos de origem",
        "fuso_horario": "UTC conforme publicado pelo INMET (Uruguaiana é UTC-3; subtrair 3h para hora local)",
        "codigo_ibge": codigo_ibge,
        "nome_municipio": nome_municipio,
        "uf": uf,
        "centroide_area_estudo": {"lon": round(centroide_lon, 6), "lat": round(centroide_lat, 6)},
        "estacoes_usadas": [
            {
                "codigo": e["CD_ESTACAO"],
                "nome": e["DC_NOME"],
                "distancia_centroide_km": round(e["_distancia_km"], 3),
                "criterio_selecao": e["_criterio_selecao"],
                "data_inicio_operacao": e["DT_INICIO_OPERACAO"],
                "anos_sem_dados_no_periodo": anos_ausentes_por_estacao.get(e["CD_ESTACAO"], []),
            }
            for e in estacoes_selecionadas
        ],
        "periodo_solicitado": {"ano_inicio": ano_inicio_solicitado, "ano_final": ano_final},
        "periodo_coberto_real": {
            "inicio": serie["datetime_utc"].min().isoformat() if len(serie) else None,
            "fim": serie["datetime_utc"].max().isoformat() if len(serie) else None,
        },
        "n_registros_total": len(serie),
        "pct_dados_faltantes_por_variavel": pct_faltantes,
        "tamanho_parquet_kb": round(caminho_parquet.stat().st_size / 1024, 1) if caminho_parquet.exists() else None,
        "data_processamento": datetime.now(timezone.utc).isoformat(),
        "transformacao_aplicada": (
            "extração seletiva (HTTP Range) do CSV da estação em cada zip anual, normalização de "
            "cabeçalhos (posicional, cobrindo os dois formatos de data/hora usados pelo INMET ao "
            "longo dos anos), conversão de marcador de dado faltante ('-9999') para NaN, construção "
            "de datetime UTC e concatenação em uma única série horária ordenada por estação e tempo"
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Baixa estações e série histórica horária do INMET para um município."
    )
    parser.add_argument("--codigo-ibge", default=CODIGO_IBGE_DEFAULT, help="Código IBGE do município (default: Uruguaiana/RS)")
    parser.add_argument("--ano-final", type=int, default=None, help="Último ano completo a incluir (default: ano atual - 1)")
    parser.add_argument("--estacoes-saida", type=Path, default=CAMINHO_ESTACOES_DEFAULT, help="Caminho base de saída das estações (sem extensão)")
    parser.add_argument("--serie-saida", type=Path, default=CAMINHO_SERIE_DEFAULT, help="Caminho base de saída da série temporal (sem extensão)")
    parser.add_argument("--forcar", action="store_true", help="Ignora o cache por ano e reprocessa tudo")
    args = parser.parse_args()

    ano_final = args.ano_final if args.ano_final is not None else datetime.now(timezone.utc).year - 1

    sessao = requests.Session()
    sessao.headers.update(HEADERS)

    nome_municipio, uf = obter_municipio_ibge(args.codigo_ibge)

    area_estudo = carregar_area_estudo()
    centroide = area_estudo.to_crs("EPSG:4326").union_all().centroid
    centroide_lon, centroide_lat = centroide.x, centroide.y

    estacoes = buscar_estacoes_automaticas(sessao)
    estacoes_selecionadas = selecionar_estacoes(estacoes, nome_municipio, uf, centroide_lon, centroide_lat)

    salvar_estacoes(estacoes_selecionadas, args.estacoes_saida)

    series = []
    anos_ausentes_por_estacao = {}
    ano_inicio_solicitado = None
    for e in estacoes_selecionadas:
        ano_inicio_estacao = int(e["DT_INICIO_OPERACAO"][:4])
        ano_inicio_solicitado = ano_inicio_estacao if ano_inicio_solicitado is None else min(ano_inicio_solicitado, ano_inicio_estacao)
        serie_estacao, anos_ausentes = montar_serie_estacao(e, ano_inicio_estacao, ano_final, sessao, args.forcar)
        anos_ausentes_por_estacao[e["CD_ESTACAO"]] = anos_ausentes
        series.append(serie_estacao)

    serie_final = pd.concat(series, ignore_index=True).sort_values(["codigo_estacao", "datetime_utc"]) if series else pd.DataFrame()

    salvar_serie(serie_final, args.serie_saida)

    metadados = calcular_metadados(
        serie_final, estacoes_selecionadas, args.codigo_ibge, nome_municipio, uf,
        centroide_lon, centroide_lat, ano_inicio_solicitado or ano_final, ano_final,
        anos_ausentes_por_estacao, args.serie_saida.with_suffix(".parquet"),
    )
    caminho_metadados_serie = args.serie_saida.with_suffix(".json")
    caminho_metadados_serie.write_text(json.dumps(metadados, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    logger.info("Metadados da série salvos em %s", caminho_metadados_serie)

    caminho_metadados_estacoes = args.estacoes_saida.with_suffix(".json")
    caminho_metadados_estacoes.write_text(json.dumps(metadados, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    logger.info("Metadados das estações salvos em %s", caminho_metadados_estacoes)


if __name__ == "__main__":
    main()
