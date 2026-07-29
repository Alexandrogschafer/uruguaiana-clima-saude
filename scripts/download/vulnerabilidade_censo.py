"""
Baixa a malha de setores censitários (Censo 2022) de um município e os
principais indicadores socioeconômicos por setor, para compor a camada de
vulnerabilidade socioambiental do projeto. Gera:

    data/raw/vetor/setores-censitarios_ibge_2022_vetorial.gpkg
    data/raw/vulnerabilidade-censo_ibge_2022.csv

Fontes de dados (investigadas e validadas por consulta real antes de codificar)
--------------------------------------------------------------------------------
1. Malha de setores censitários: a API de malhas do IBGE
   (servicodados.ibge.gov.br/api/v3/malhas) NÃO aceita `intrarregiao=setor`
   (nem `distrito`) quando a malha de referência é um município — o erro
   retornado é "Quando a malha for um município do Brasil, o parâmetro
   intrarregiao NÃO aceita valores", e mesmo no nível de UF o parâmetro só
   aceita subdivisão até `municipio`. Setores censitários não são
   servidos por essa API; são distribuídos como GeoPackage por UF em:

       https://geoftp.ibge.gov.br/organizacao_do_territorio/malhas_territoriais/
       malhas_de_setores_censitarios__divisoes_intramunicipais/censo_2022/
       setores/gpkg/UF/{UF}/{UF}_setores_CD2022.gpkg

   Este script baixa (uma vez, cacheado) o gpkg da UF do município e filtra
   localmente pela coluna `CD_MUN` — mais rápido que filtrar via
   `/vsicurl/` remoto (testado: ~40s remoto vs. segundos localmente após
   cache).

2. Indicadores por setor: o produto "Censo 2022: Agregados por Setores
   Censitários" (ftp.ibge.gov.br/Censos/Censo_Demografico_2022/
   Agregados_por_Setores_Censitarios/Agregados_por_Setor_csv/) publica CSVs
   nacionais por tema (básico, demografia, características do domicílio,
   cor ou raça, alfabetização, parentesco, óbitos). Confirmado pelo
   dicionário de dados oficial (xlsx) que **rendimento domiciliar e forma
   de abastecimento de água/esgotamento sanitário NÃO estão neste produto**
   — não existem no nível de setor censitário para o Censo 2022. Por isso:
     - população, domicílios e faixas etárias vêm dos temas "básico" e
       "demografia" (nível de SETOR).
     - rendimento médio domiciliar per capita, % água inadequada e %
       esgoto inadequado vêm da API de agregados/SIDRA
       (servicodados.ibge.gov.br/api/v3/agregados), tabelas 10295, 6803 e
       6805 — validadas por consulta real —, disponíveis apenas até o
       nível de MUNICÍPIO (N6). Essas três colunas no CSV final são,
       portanto, o mesmo valor municipal repetido em todos os setores (o
       nome da coluna deixa isso explícito).
     - o Censo 2022 não libera faixa etária "0 a 5" nem "65 ou mais" no
       nível de setor — só "0 a 4" e "60 a 69"/"70 ou mais". Este script
       usa essas faixas como aproximação (documentado nos metadados e no
       nome das colunas).
     - % urbano/rural não é um percentual por setor (cada setor já é
       inteiramente Urbano OU Rural, atributo `SITUACAO` da própria malha)
       — o percentual agregado do município é calculado e registrado nos
       metadados para contexto.

Idempotência
------------
Os arquivos brutos nacionais/estaduais (mesh da UF, CSVs de tema) são
grandes (15-140 MB) e cobrem o Brasil inteiro; são cacheados em
`data/raw/cache_censo/` e não baixados de novo em execuções futuras, a
menos que --forcar seja usado.

Uso:
    python scripts/download/vulnerabilidade_censo.py
    python scripts/download/vulnerabilidade_censo.py --codigo-ibge 4314902 --forcar
"""

import argparse
import io
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
from recorte_municipio import CRS_PADRAO  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

CODIGO_IBGE_DEFAULT = "4322400"  # Uruguaiana, RS

HEADERS = {"User-Agent": "Mozilla/5.0 (compativel; script-pesquisa-ClimaPampa/1.0)"}
N_TENTATIVAS = 3
BACKOFF_BASE_S = 1.0  # 1s, 2s, 4s
TAMANHO_CHUNK_CSV = 100_000  # linhas por chunk ao filtrar os CSVs nacionais (log de progresso)

URL_MUNICIPIO_IBGE = "https://servicodados.ibge.gov.br/api/v1/localidades/municipios/{codigo}"
URL_MESH_TEMPLATE = (
    "https://geoftp.ibge.gov.br/organizacao_do_territorio/malhas_territoriais/"
    "malhas_de_setores_censitarios__divisoes_intramunicipais/censo_2022/setores/gpkg/UF/{uf}/{uf}_setores_CD2022.gpkg"
)
BASE_URL_SETOR_CSV = (
    "https://ftp.ibge.gov.br/Censos/Censo_Demografico_2022/Agregados_por_Setores_Censitarios/Agregados_por_Setor_csv/"
)
TEMAS_SETOR_CSV = {
    "basico": ("Agregados_por_setores_basico_BR_20260520.zip", "Agregados_por_setores_basico_BR.csv"),
    "demografia": ("Agregados_por_setores_demografia_BR.zip", "Agregados_por_setores_demografia_BR.csv"),
}

URL_SIDRA_BASE = "https://servicodados.ibge.gov.br/api/v3/agregados"
# Tabelas validadas por consulta real (ver docstring): só existem a partir do nível N6 (município).
SIDRA_RENDIMENTO = {"agregado": 10295, "variavel": 13431, "classificacao": "2[6794]|86[95251]|58[95253]", "categoria_total": "Total"}
SIDRA_AGUA = {"agregado": 6803, "variavel": 381, "classificacao": "1821[72129,72144]", "categoria_total": "Total", "categoria_adequada": "Possui ligação à rede geral e a utiliza como forma principal"}
SIDRA_ESGOTO = {"agregado": 6805, "variavel": 381, "classificacao": "11558[46292,46290]", "categoria_total": "Total", "categoria_adequada": "Rede geral, rede pluvial ou fossa ligada à rede"}

CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "raw" / "cache_censo"
CAMINHO_MALHA_DEFAULT = Path(__file__).resolve().parents[2] / "data" / "raw" / "vetor" / "setores-censitarios_ibge_2022_vetorial.gpkg"
CAMINHO_INDICADORES_DEFAULT = Path(__file__).resolve().parents[2] / "data" / "raw" / "vulnerabilidade-censo_ibge_2022.csv"


def _requisitar_com_retry(sessao: requests.Session, metodo: str, url: str, **kwargs) -> requests.Response:
    """GET/HEAD com retry e backoff exponencial simples (1s, 2s, 4s)."""
    ultimo_erro = None
    for tentativa in range(1, N_TENTATIVAS + 1):
        try:
            resposta = sessao.request(metodo, url, timeout=120, **kwargs)
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


def _baixar_arquivo_com_retry(sessao: requests.Session, url: str, destino: Path) -> None:
    """Baixa um arquivo grande (streaming) com retry, salvando em arquivo temporário até concluir."""
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino_tmp = destino.with_suffix(destino.suffix + ".tmp")
    ultimo_erro = None
    for tentativa in range(1, N_TENTATIVAS + 1):
        try:
            with sessao.get(url, headers=HEADERS, stream=True, timeout=120) as resposta:
                resposta.raise_for_status()
                with open(destino_tmp, "wb") as f:
                    for bloco in resposta.iter_content(chunk_size=1024 * 1024):
                        f.write(bloco)
            destino_tmp.rename(destino)
            return
        except requests.RequestException as erro:
            ultimo_erro = erro
            if tentativa < N_TENTATIVAS:
                espera = BACKOFF_BASE_S * (2 ** (tentativa - 1))
                logger.warning(
                    "Falha ao baixar %s (tentativa %d/%d): %s — nova tentativa em %.0fs",
                    url, tentativa, N_TENTATIVAS, erro, espera,
                )
                time.sleep(espera)
    raise RuntimeError(f"Falha ao baixar {url} após {N_TENTATIVAS} tentativas: {ultimo_erro}") from ultimo_erro


def obter_municipio_uf(codigo_ibge: str, sessao: requests.Session) -> tuple[str, str]:
    url = URL_MUNICIPIO_IBGE.format(codigo=codigo_ibge)
    resposta = _requisitar_com_retry(sessao, "GET", url, headers=HEADERS)
    dados = resposta.json()
    return dados["nome"], dados["microrregiao"]["mesorregiao"]["UF"]["sigla"]


def baixar_malha_setores(codigo_ibge: str, uf: str, sessao: requests.Session, forcar: bool) -> gpd.GeoDataFrame:
    """Baixa (cacheado por UF) o GeoPackage de setores censitários e filtra pelo município."""
    cache_path = CACHE_DIR / f"{uf}_setores_CD2022.gpkg"
    if not cache_path.exists() or forcar:
        url = URL_MESH_TEMPLATE.format(uf=uf)
        logger.info("Baixando malha de setores censitários da UF %s (cache: %s) — %s", uf, cache_path, url)
        _baixar_arquivo_com_retry(sessao, url, cache_path)
    else:
        logger.info("Usando malha de setores da UF %s já em cache: %s", uf, cache_path)

    gdf = gpd.read_file(cache_path, where=f"CD_MUN = '{codigo_ibge}'")
    logger.info("Malha filtrada: %d setores censitários no município %s", len(gdf), codigo_ibge)
    return gdf


def baixar_tema_setor_csv(tema: str, sessao: requests.Session, forcar: bool) -> Path:
    """Baixa (cacheado) o zip nacional de um tema de 'Agregados por Setor' e extrai o CSV."""
    nome_zip, nome_csv = TEMAS_SETOR_CSV[tema]
    cache_csv = CACHE_DIR / nome_csv
    if cache_csv.exists() and not forcar:
        logger.info("Tema '%s' já em cache: %s", tema, cache_csv)
        return cache_csv

    url = BASE_URL_SETOR_CSV + nome_zip
    cache_zip = CACHE_DIR / nome_zip
    logger.info("Baixando tema '%s' (Brasil inteiro, será filtrado depois) — %s", tema, url)
    _baixar_arquivo_com_retry(sessao, url, cache_zip)

    with zipfile.ZipFile(cache_zip) as zf:
        membros = zf.namelist()
        with zf.open(membros[0]) as origem, open(cache_csv, "wb") as destino:
            destino.write(origem.read())
    cache_zip.unlink()
    return cache_csv


def filtrar_tema_por_municipio(caminho_csv: Path, codigo_ibge: str, tema: str) -> pd.DataFrame:
    """Lê o CSV nacional de um tema em chunks e retorna só as linhas do município (por prefixo do CD_SETOR)."""
    partes = []
    total_lido = 0
    for i, chunk in enumerate(
        pd.read_csv(caminho_csv, sep=";", decimal=",", encoding="latin1", dtype=str, chunksize=TAMANHO_CHUNK_CSV)
    ):
        chunk.columns = chunk.columns.str.lower()
        coluna_setor = "cd_setor"
        filtrado = chunk[chunk[coluna_setor].str[:7] == codigo_ibge]
        if len(filtrado):
            partes.append(filtrado)
        total_lido += len(chunk)
        logger.info("Tema '%s': %d linhas processadas (%d chunk(s)), %d setores do município encontrados até agora",
                     tema, total_lido, i + 1, sum(len(p) for p in partes))

    if not partes:
        return pd.DataFrame(columns=["cd_setor"])
    df = pd.concat(partes, ignore_index=True)

    colunas_numericas = [c for c in df.columns if c not in ("cd_setor",)]
    for coluna in colunas_numericas:
        df[coluna] = pd.to_numeric(df[coluna], errors="coerce")
    return df


def consultar_sidra_variavel(config: dict, codigo_ibge: str, sessao: requests.Session) -> dict[str, float]:
    """Consulta uma variável do SIDRA/agregados no nível de município (N6) para as categorias pedidas."""
    url = f"{URL_SIDRA_BASE}/{config['agregado']}/periodos/2022/variaveis/{config['variavel']}"
    params = {"localidades": f"N6[{codigo_ibge}]", "classificacao": config["classificacao"]}
    resposta = _requisitar_com_retry(sessao, "GET", url, params=params, headers=HEADERS)
    dados = resposta.json()

    resultado = {}
    for bloco in dados[0]["resultados"]:
        categoria_nome = list(bloco["classificacoes"][0]["categoria"].values())[0]
        valor_str = bloco["series"][0]["serie"].get("2022")
        resultado[categoria_nome] = None if valor_str in (None, "...", "-", "X") else float(valor_str)
    return resultado


def calcular_indicadores_municipio(codigo_ibge: str, sessao: requests.Session) -> dict:
    """Indicadores só disponíveis no nível de município (renda, água, esgoto) via SIDRA."""
    logger.info("Consultando indicadores de nível municipal via SIDRA (rendimento, água, esgoto)...")
    renda = consultar_sidra_variavel(SIDRA_RENDIMENTO, codigo_ibge, sessao)
    agua = consultar_sidra_variavel(SIDRA_AGUA, codigo_ibge, sessao)
    esgoto = consultar_sidra_variavel(SIDRA_ESGOTO, codigo_ibge, sessao)

    rendimento_medio = renda.get(SIDRA_RENDIMENTO["categoria_total"])

    agua_total = agua.get(SIDRA_AGUA["categoria_total"])
    agua_adequada = agua.get(SIDRA_AGUA["categoria_adequada"])
    pct_agua_inadequada = (
        round(100 * (1 - agua_adequada / agua_total), 2) if agua_total and agua_adequada is not None else None
    )

    esgoto_total = esgoto.get(SIDRA_ESGOTO["categoria_total"])
    esgoto_adequado = esgoto.get(SIDRA_ESGOTO["categoria_adequada"])
    pct_esgoto_inadequado = (
        round(100 * (1 - esgoto_adequado / esgoto_total), 2) if esgoto_total and esgoto_adequado is not None else None
    )

    return {
        "rendimento_medio_domiciliar_per_capita_reais_municipio": rendimento_medio,
        "pct_domicilios_agua_inadequada_municipio": pct_agua_inadequada,
        "pct_domicilios_esgoto_inadequado_municipio": pct_esgoto_inadequado,
        "_domicilios_total_agua": agua_total,
        "_domicilios_total_esgoto": esgoto_total,
    }


def montar_tabela_indicadores(
    gdf_setores: gpd.GeoDataFrame, df_basico: pd.DataFrame, df_demografia: pd.DataFrame, indicadores_municipio: dict
) -> pd.DataFrame:
    setores = gdf_setores[["CD_SETOR", "SITUACAO", "AREA_KM2"]].copy()
    setores.columns = ["cd_setor", "situacao", "area_km2"]

    df = setores.merge(df_basico[["cd_setor", "v0001", "v0007"]], on="cd_setor", how="left")
    df = df.merge(df_demografia[["cd_setor", "v01031", "v01018", "v01019"]], on="cd_setor", how="left")

    df = df.rename(columns={"v0001": "populacao_total", "v0007": "domicilios_particulares_ocupados"})
    df["densidade_demografica_hab_km2"] = (df["populacao_total"] / df["area_km2"]).where(df["area_km2"] > 0)
    df["pct_populacao_0_a_4_anos"] = (100 * df["v01031"] / df["populacao_total"]).round(2)
    df["pct_populacao_60_anos_ou_mais"] = (100 * (df["v01018"] + df["v01019"]) / df["populacao_total"]).round(2)
    df = df.drop(columns=["v01031", "v01018", "v01019"])

    for coluna, valor in indicadores_municipio.items():
        if coluna.startswith("_"):
            continue
        df[coluna] = valor

    return df


def calcular_metadados(
    df: pd.DataFrame, codigo_ibge: str, nome_municipio: str, uf: str, indicadores_municipio: dict
) -> dict:
    colunas_setor = [
        "populacao_total", "domicilios_particulares_ocupados", "densidade_demografica_hab_km2",
        "pct_populacao_0_a_4_anos", "pct_populacao_60_anos_ou_mais",
    ]
    n_completos = int(df[colunas_setor].notna().all(axis=1).sum())
    n_total = len(df)

    pct_domicilios_urbano = None
    if "domicilios_particulares_ocupados" in df.columns and df["domicilios_particulares_ocupados"].sum() > 0:
        dom_urbano = df.loc[df["situacao"] == "Urbana", "domicilios_particulares_ocupados"].sum()
        pct_domicilios_urbano = round(100 * dom_urbano / df["domicilios_particulares_ocupados"].sum(), 2)

    return {
        "fonte_malha": (
            "IBGE — Malha de Setores Censitários (Divisões Intramunicipais), Censo 2022 — GeoPackage por UF em "
            "geoftp.ibge.gov.br/organizacao_do_territorio/malhas_territoriais/"
            "malhas_de_setores_censitarios__divisoes_intramunicipais/censo_2022/setores/gpkg/UF/"
        ),
        "fonte_indicadores_setor": (
            "IBGE — Censo 2022: Agregados por Setores Censitários (temas 'básico' e 'demografia'), "
            "ftp.ibge.gov.br/Censos/Censo_Demografico_2022/Agregados_por_Setores_Censitarios/Agregados_por_Setor_csv/"
        ),
        "fonte_indicadores_municipio": "IBGE — API de Agregados/SIDRA (servicodados.ibge.gov.br/api/v3/agregados), tabelas 10295 (rendimento), 6803 (água) e 6805 (esgoto)",
        "codigo_ibge": codigo_ibge,
        "nome_municipio": nome_municipio,
        "uf": uf,
        "n_setores": n_total,
        "indicadores_nivel_setor": {
            "populacao_total": "V0001 do tema 'básico'",
            "domicilios_particulares_ocupados": "V0007 do tema 'básico'",
            "densidade_demografica_hab_km2": "calculado: populacao_total / AREA_KM2 da malha",
            "situacao": "atributo SITUACAO da própria malha (Urbana/Rural) — não é percentual, cada setor é 100% urbano ou 100% rural",
            "pct_populacao_0_a_4_anos": "aproximação de 'infantil 0-5 anos': Censo 2022 só libera a faixa '0 a 4 anos' (V01031) no nível de setor, não '0 a 5'",
            "pct_populacao_60_anos_ou_mais": "aproximação de 'idosos 65+': Censo 2022 só libera '60 a 69' (V01018) e '70 ou mais' (V01019) no nível de setor, não corte em 65 anos",
        },
        "indicadores_nivel_municipio": {
            "rendimento_medio_domiciliar_per_capita_reais_municipio": "SIDRA tabela 10295, variável 13431 — não existe no produto de setores censitários do Censo 2022",
            "pct_domicilios_agua_inadequada_municipio": "SIDRA tabela 6803, variável 381 — inadequado = não possui ligação à rede geral como forma principal; não existe no nível de setor",
            "pct_domicilios_esgoto_inadequado_municipio": "SIDRA tabela 6805, variável 381 — inadequado = tipo de esgotamento diferente de rede geral/pluvial/fossa ligada à rede; não existe no nível de setor",
            "_domicilios_total_agua_municipio": indicadores_municipio.get("_domicilios_total_agua"),
            "_domicilios_total_esgoto_municipio": indicadores_municipio.get("_domicilios_total_esgoto"),
        },
        "pct_domicilios_urbano_municipio": pct_domicilios_urbano,
        "n_setores_dados_completos": n_completos,
        "n_setores_dados_incompletos": n_total - n_completos,
        "pct_setores_dados_completos": round(100 * n_completos / n_total, 2) if n_total else None,
        "observacao_dados_incompletos": (
            "setores incompletos são majoritariamente por sigilo estatístico do IBGE: células de faixa etária "
            "com poucos casos vêm marcadas como 'X' na fonte e são convertidas para NaN (não são setores sem "
            "dado algum, mas com alguma faixa etária/variável especificamente suprimida por privacidade)"
        ),
        "data_processamento": datetime.now(timezone.utc).isoformat(),
        "transformacao_aplicada": (
            "filtragem da malha de setores da UF por CD_MUN, filtragem dos CSVs nacionais de indicadores por setor "
            "(prefixo do CD_SETOR = código IBGE do município), cálculo de densidade demográfica e percentuais de "
            "faixa etária por setor, e junção de indicadores municipais (SIDRA) replicados em todos os setores"
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Baixa a malha de setores censitários e indicadores do Censo 2022 para um município."
    )
    parser.add_argument("--codigo-ibge", default=CODIGO_IBGE_DEFAULT, help="Código IBGE do município (default: Uruguaiana/RS)")
    parser.add_argument("--malha-saida", type=Path, default=CAMINHO_MALHA_DEFAULT, help="Caminho de saída da malha de setores (.gpkg)")
    parser.add_argument("--indicadores-saida", type=Path, default=CAMINHO_INDICADORES_DEFAULT, help="Caminho de saída dos indicadores (.csv)")
    parser.add_argument("--forcar", action="store_true", help="Ignora os caches e reprocessa tudo")
    args = parser.parse_args()

    if args.malha_saida.exists() and args.indicadores_saida.exists() and not args.forcar:
        logger.info(
            "Malha de setores e indicadores já existem (%s, %s) — nada a fazer (use --forcar para refazer).",
            args.malha_saida, args.indicadores_saida,
        )
        return

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    sessao = requests.Session()
    sessao.headers.update(HEADERS)

    nome_municipio, uf = obter_municipio_uf(args.codigo_ibge, sessao)
    logger.info("Município: %s (%s) — código IBGE %s", nome_municipio, uf, args.codigo_ibge)

    gdf_setores = baixar_malha_setores(args.codigo_ibge, uf, sessao, args.forcar)
    gdf_setores_padrao = gdf_setores.to_crs(CRS_PADRAO)

    args.malha_saida.parent.mkdir(parents=True, exist_ok=True)
    gdf_setores_padrao.to_file(args.malha_saida, driver="GPKG", layer="setores_censitarios")
    logger.info("Malha de setores salva em %s (CRS: %s, %d setores)", args.malha_saida, CRS_PADRAO, len(gdf_setores_padrao))

    caminho_basico = baixar_tema_setor_csv("basico", sessao, args.forcar)
    caminho_demografia = baixar_tema_setor_csv("demografia", sessao, args.forcar)
    df_basico = filtrar_tema_por_municipio(caminho_basico, args.codigo_ibge, "basico")
    df_demografia = filtrar_tema_por_municipio(caminho_demografia, args.codigo_ibge, "demografia")

    indicadores_municipio = calcular_indicadores_municipio(args.codigo_ibge, sessao)

    tabela = montar_tabela_indicadores(gdf_setores, df_basico, df_demografia, indicadores_municipio)

    args.indicadores_saida.parent.mkdir(parents=True, exist_ok=True)
    tabela.to_csv(args.indicadores_saida, index=False, encoding="utf-8")
    logger.info("Indicadores salvos em %s (%d setores)", args.indicadores_saida, len(tabela))

    metadados = calcular_metadados(tabela, args.codigo_ibge, nome_municipio, uf, indicadores_municipio)

    caminho_metadados_malha = args.malha_saida.with_suffix(".json")
    caminho_metadados_malha.write_text(json.dumps(metadados, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    logger.info("Metadados da malha salvos em %s", caminho_metadados_malha)

    caminho_metadados_indicadores = args.indicadores_saida.with_suffix(".json")
    caminho_metadados_indicadores.write_text(json.dumps(metadados, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    logger.info("Metadados dos indicadores salvos em %s", caminho_metadados_indicadores)


if __name__ == "__main__":
    main()
