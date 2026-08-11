"""
Baixa a malha de setores censitários (geometria) e os dados atributivos
(população residente e domicílios) dos Censos 2000 e 2010 para um
município, e calcula densidade demográfica por setor. Gera:

    data/raw/vetor/setores-censitarios_ibge_2000_vetorial.gpkg
    data/raw/vetor/setores-censitarios_ibge_2010_vetorial.gpkg
    (+ .json de metadado cada um)

ATENÇÃO — NÃO usar para série espacial contínua: a malha de setores muda de
configuração a cada Censo (2000, 2010, 2022), com desalinhamento de
fronteira reconhecido pelo próprio IBGE entre anos censitários. Cada ano é
uma "foto" independente da divisão territorial vigente naquele Censo — não
sobrepor/comparar geometricamente um mesmo setor entre anos diferentes. A
série temporal comparável está em nível MUNICIPAL, em
scripts/download/demografia_ibge_sidra.py. O detalhe espacial por setor de
2022 (malha atual) está em scripts/download/vulnerabilidade_censo.py.

Fontes e decisões metodológicas (investigadas por consulta real antes de codificar)
-------------------------------------------------------------------------------------
Malha 2000
    Distribuída em DOIS produtos separados por situação (não existe um
    arquivo único urbano+rural como em 2010/2022):
      - setor_urbano: geoftp.../censo_2000/setor_urbano/rs/{codigo}/{codigo}.zip
        (1 arquivo por município). CRS: o .PRJ é um formato legado do
        ArcView sem datum explícito ("PROJECTION UTM \n ZONE 21S \n
        PARAMETERS" — nenhum elipsoide/datum declarado); o GDAL, ao abrir
        direto, interpreta erradamente como WGS84/UTM zone 21 N (hemisfério
        e datum errados). Forçado para SAD69/UTM 21S = EPSG:29191, com base
        na documentação técnica oficial do produto ("advertências
        técnicas" da Malha Municipal Digital 2000), que declara elipsoide
        UGGI67 / Datum Horizontal SAD69 para toda a série.
      - setor_rural: geoftp.../censo_2000/setor_rural/projecao_geografica/
        censo_2000/e500_arcview_shp/uf/{uf}/{uf}_setores_censitarios.zip
        (1 arquivo por UF, sem QUALQUER .prj — coordenadas em graus
        decimais). Forçado para SAD69 geográfico = EPSG:4618, mesma
        referência documentada na nota acima (mesmo produto/época).
    Setores urbanos "ilha" dentro do arquivo rural (código com sufixo
    "-NNNN", situação urbana) que já existem como polígono próprio no
    arquivo urbano são descartados do lado rural para não duplicar
    geometria/população (ver `_filtrar_rural_sobreposto_ao_urbano`) —
    validado por contagem: 118 setores urbanos + 27 rurais únicos (28
    códigos-base do arquivo rural menos 1 sobreposto ao urbano) = 145,
    batendo exatamente com as 145 linhas da tabela de atributos do
    município.
    Atributos (Agregado_de_setores_2000_RS.zip, ftp.ibge.gov.br/Censos/
    Censo_Demografico_2000/Dados_do_Universo/Agregado_por_Setores_
    Censitarios/): planilhas Morador_RS.XLS (V0237 = população residente
    no setor) e Domicilio_RS.XLS (V0001 = domicílios totais, V0003 =
    domicílios particulares permanentes). Chave de junção: Cod_setor
    (15 dígitos) = ID_ (arquivo urbano) / GEOCODIGO truncado no primeiro
    hífen (arquivo rural). Validado: soma da população = 126.936, idêntico
    ao total do Censo 2000 já usado em
    populacao_ibge-sidra-tabela200_1970-2010_municipal.csv (Etapa A do
    estudo demográfico municipal).

Malha 2010
    Único arquivo por UF: geoftp.../censo_2010/setores_censitarios_shp/
    {uf}/{uf}_setores_censitarios.zip. CRS confirmado EXPLICITAMENTE no
    próprio .prj como SIRGAS 2000 geográfico = EPSG:4674 — sem necessidade
    de forçar/adivinhar, ao contrário de 2000.
    Atributos (ftp.ibge.gov.br/Censos/Censo_Demografico_2010/Resultados_do_
    Universo/Agregados_por_Setores_Censitarios/RS_*.zip): planilhas
    Pessoa03_RS.csv (V001 = pessoas residentes) e Domicilio01_RS.csv
    (V001 = domicílios totais, V002 = domicílios particulares
    permanentes). Chave de junção: Cod_setor = CD_GEOCODI. Validado: soma
    da população = 125.435, idêntico ao Censo 2010 já usado na Etapa A.
    2 dos 150 setores do município (432240030000008 e 432240035000001)
    não têm linha nas tabelas de atributos (população/domicílios ficam
    NaN) — não é bug de junção: essas 2 linhas simplesmente não existem
    nos CSVs de origem (confirmado por busca direta pelo código). Não
    preenchido com zero para não inventar dado; sinalizado na coluna
    `dados_atributivos_ausentes` e no metadado.

Idempotência: os arquivos brutos de UF (grandes) são cacheados em
data/raw/cache_setores_historico/ e não baixados de novo, a menos que
--forcar seja usado.

Uso:
    python scripts/download/setores_censitarios_historico.py
    python scripts/download/setores_censitarios_historico.py --codigo-ibge 4314902 --forcar
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
from recorte_municipio import CRS_PADRAO  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

CODIGO_IBGE_DEFAULT = "4322400"  # Uruguaiana, RS
HEADERS = {"User-Agent": "Mozilla/5.0 (compativel; script-pesquisa-ClimaPampa/1.0)"}
N_TENTATIVAS = 3
BACKOFF_BASE_S = 1.0

URL_MUNICIPIO_IBGE = "https://servicodados.ibge.gov.br/api/v1/localidades/municipios/{codigo}"

GEOFTP_BASE = "https://geoftp.ibge.gov.br/organizacao_do_territorio/malhas_territoriais/malhas_de_setores_censitarios__divisoes_intramunicipais"
URL_MALHA_2000_URBANA = GEOFTP_BASE + "/censo_2000/setor_urbano/{uf_lower}/{codigo}/{codigo}.zip"
URL_MALHA_2000_RURAL = GEOFTP_BASE + "/censo_2000/setor_rural/projecao_geografica/censo_2000/e500_arcview_shp/uf/{uf_lower}/{uf_lower}_setores_censitarios.zip"
URL_MALHA_2010 = GEOFTP_BASE + "/censo_2010/setores_censitarios_shp/{uf_lower}/{uf_lower}_setores_censitarios.zip"

URL_ATRIBUTOS_2000 = "https://ftp.ibge.gov.br/Censos/Censo_Demografico_2000/Dados_do_Universo/Agregado_por_Setores_Censitarios/Agregado_de_setores_2000_{uf_upper}.zip"
URL_ATRIBUTOS_2010 = "https://ftp.ibge.gov.br/Censos/Censo_Demografico_2010/Resultados_do_Universo/Agregados_por_Setores_Censitarios/{uf_upper}_20260615.zip"

# CRS de origem — ver notas metodológicas no topo do arquivo.
CRS_2000_URBANO = "EPSG:29191"  # SAD69 / UTM zone 21S — forçado (PRJ legado sem datum, GDAL interpreta errado)
CRS_2000_RURAL = "EPSG:4618"    # SAD69 geográfico — forçado (sem .prj algum na fonte)
CRS_2010 = "EPSG:4674"          # SIRGAS 2000 geográfico — confirmado no .prj da própria fonte

CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "raw" / "cache_setores_historico"
SAIDA_DIR = Path(__file__).resolve().parents[2] / "data" / "raw" / "vetor"

# Situação do setor: mesmo esquema de códigos em 2000 e 2010 (confirmado nas documentações oficiais de ambos).
CODIGOS_SITUACAO_URBANA = {"1", "2", "3"}
CODIGOS_SITUACAO_RURAL = {"4", "5", "6", "7", "8"}


def _situacao_de_codigo(codigo) -> str | None:
    codigo = str(codigo).strip()
    if codigo in CODIGOS_SITUACAO_URBANA:
        return "Urbana"
    if codigo in CODIGOS_SITUACAO_RURAL:
        return "Rural"
    return None


def _requisitar_com_retry(sessao: requests.Session, url: str, **kwargs) -> requests.Response:
    ultimo_erro = None
    for tentativa in range(1, N_TENTATIVAS + 1):
        try:
            resposta = sessao.get(url, headers=HEADERS, timeout=60, **kwargs)
            resposta.raise_for_status()
            return resposta
        except requests.RequestException as erro:
            ultimo_erro = erro
            if tentativa < N_TENTATIVAS:
                espera = BACKOFF_BASE_S * (2 ** (tentativa - 1))
                logger.warning("Falha em %s (tentativa %d/%d): %s — nova tentativa em %.0fs", url, tentativa, N_TENTATIVAS, erro, espera)
                time.sleep(espera)
    raise RuntimeError(f"Falha ao requisitar {url} após {N_TENTATIVAS} tentativas: {ultimo_erro}") from ultimo_erro


def _baixar_com_cache(sessao: requests.Session, url: str, destino: Path, forcar: bool) -> Path:
    if destino.exists() and not forcar:
        logger.info("Já em cache: %s", destino)
        return destino
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino_tmp = destino.with_suffix(destino.suffix + ".tmp")
    logger.info("Baixando %s -> %s", url, destino)
    ultimo_erro = None
    for tentativa in range(1, N_TENTATIVAS + 1):
        try:
            with sessao.get(url, headers=HEADERS, stream=True, timeout=120) as resposta:
                resposta.raise_for_status()
                with open(destino_tmp, "wb") as f:
                    for bloco in resposta.iter_content(chunk_size=1024 * 1024):
                        f.write(bloco)
            destino_tmp.rename(destino)
            return destino
        except requests.RequestException as erro:
            ultimo_erro = erro
            if tentativa < N_TENTATIVAS:
                espera = BACKOFF_BASE_S * (2 ** (tentativa - 1))
                logger.warning("Falha ao baixar %s (tentativa %d/%d): %s — nova tentativa em %.0fs", url, tentativa, N_TENTATIVAS, erro, espera)
                time.sleep(espera)
    raise RuntimeError(f"Falha ao baixar {url} após {N_TENTATIVAS} tentativas: {ultimo_erro}") from ultimo_erro


def obter_municipio_uf(codigo_ibge: str, sessao: requests.Session) -> tuple[str, str]:
    resposta = _requisitar_com_retry(sessao, URL_MUNICIPIO_IBGE.format(codigo=codigo_ibge))
    dados = resposta.json()
    return dados["nome"], dados["microrregiao"]["mesorregiao"]["UF"]["sigla"]


def _extrair_membro(caminho_zip: Path, sufixo_busca: str, destino_dir: Path) -> Path | None:
    """Extrai (cacheado) o primeiro membro do zip cujo nome termina com sufixo_busca."""
    destino_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(caminho_zip) as zf:
        candidatos = [n for n in zf.namelist() if n.lower().endswith(sufixo_busca.lower())]
        if not candidatos:
            return None
        membro = candidatos[0]
        nome_saida = Path(membro).name
        caminho_saida = destino_dir / nome_saida
        if not caminho_saida.exists():
            with zf.open(membro) as origem, open(caminho_saida, "wb") as saida:
                saida.write(origem.read())
        return caminho_saida


def _extrair_shapefile(caminho_zip: Path, destino_dir: Path, contains: str | None = None) -> Path:
    """Extrai todos os componentes de um shapefile (.shp/.shx/.dbf/.prj) do zip e retorna o .shp."""
    destino_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(caminho_zip) as zf:
        membros = zf.namelist()
        if contains:
            membros = [m for m in membros if contains.lower() in m.lower()]
        caminho_shp = None
        for membro in membros:
            ext = Path(membro).suffix.lower()
            if ext not in (".shp", ".shx", ".dbf", ".prj", ".cpg"):
                continue
            nome_saida = Path(membro).name
            caminho_saida = destino_dir / nome_saida
            if not caminho_saida.exists():
                with zf.open(membro) as origem, open(caminho_saida, "wb") as saida:
                    saida.write(origem.read())
            if ext == ".shp":
                caminho_shp = caminho_saida
        if caminho_shp is None:
            raise FileNotFoundError(f"Nenhum .shp encontrado em {caminho_zip} (filtro contains={contains!r})")
        return caminho_shp


# ---------------------------------------------------------------------------
# Censo 2000
# ---------------------------------------------------------------------------

def montar_malha_2000(codigo_ibge: str, uf: str, sessao: requests.Session, forcar: bool) -> gpd.GeoDataFrame:
    uf_lower = uf.lower()

    # --- malha urbana (per-municipio, CRS forçado: ver notas do módulo) ---
    zip_urbana = CACHE_DIR / f"2000_urbano_{codigo_ibge}.zip"
    _baixar_com_cache(sessao, URL_MALHA_2000_URBANA.format(uf_lower=uf_lower, codigo=codigo_ibge), zip_urbana, forcar)
    shp_urbana = _extrair_shapefile(zip_urbana, CACHE_DIR / f"2000_urbano_{codigo_ibge}")
    gdf_urbana = gpd.read_file(shp_urbana)
    gdf_urbana = gdf_urbana.set_crs(CRS_2000_URBANO, allow_override=True)
    gdf_urbana = gdf_urbana.rename(columns={"ID_": "cd_setor"})[["cd_setor", "geometry"]]
    logger.info("Malha urbana 2000: %d setores (CRS forçado %s)", len(gdf_urbana), CRS_2000_URBANO)

    # --- malha rural (UF inteira, filtrada pelo município; CRS forçado: sem .prj na fonte) ---
    zip_rural = CACHE_DIR / f"2000_rural_{uf_lower}.zip"
    _baixar_com_cache(sessao, URL_MALHA_2000_RURAL.format(uf_lower=uf_lower), zip_rural, forcar)
    shp_rural = _extrair_shapefile(zip_rural, CACHE_DIR / f"2000_rural_{uf_lower}")
    gdf_rural_uf = gpd.read_file(shp_rural)
    gdf_rural_uf = gdf_rural_uf.set_crs(CRS_2000_RURAL, allow_override=True)
    gdf_rural = gdf_rural_uf[gdf_rural_uf["GEOCODIGO"].astype(str).str.startswith(codigo_ibge)].copy()
    gdf_rural["cd_setor"] = gdf_rural["GEOCODIGO"].astype(str).str.split("-").str[0]

    # descarta do lado rural qualquer código-base já presente na malha urbana (evita duplicar geometria/população —
    # ver nota do módulo: "setores urbanos ilha dentro do arquivo rural")
    codigos_urbanos = set(gdf_urbana["cd_setor"])
    sobrepostos = sorted(set(gdf_rural["cd_setor"]) & codigos_urbanos)
    if sobrepostos:
        logger.info("Descartando %d setor(es) do arquivo rural já presentes na malha urbana: %s", len(sobrepostos), sobrepostos)
    gdf_rural = gdf_rural[~gdf_rural["cd_setor"].isin(codigos_urbanos)]

    # dissolve multi-partes (mesmo cd_setor em mais de uma linha) em uma única geometria por setor
    gdf_rural = gdf_rural.dissolve(by="cd_setor", as_index=False)[["cd_setor", "geometry"]]
    logger.info("Malha rural 2000: %d setores após filtro municipal e remoção de sobreposição com o urbano (CRS forçado %s)", len(gdf_rural), CRS_2000_RURAL)

    gdf_urbana_padrao = gdf_urbana.to_crs(CRS_PADRAO)
    gdf_rural_padrao = gdf_rural.to_crs(CRS_PADRAO)
    gdf = pd.concat([gdf_urbana_padrao.assign(_fonte_malha="urbana"), gdf_rural_padrao.assign(_fonte_malha="rural")], ignore_index=True)
    return gpd.GeoDataFrame(gdf, geometry="geometry", crs=CRS_PADRAO)


def carregar_atributos_2000(codigo_ibge: str, uf: str, sessao: requests.Session, forcar: bool) -> pd.DataFrame:
    zip_atributos = CACHE_DIR / f"2000_atributos_{uf.lower()}.zip"
    _baixar_com_cache(sessao, URL_ATRIBUTOS_2000.format(uf_upper=uf.upper()), zip_atributos, forcar)
    destino = CACHE_DIR / f"2000_atributos_{uf.lower()}"

    caminho_morador = _extrair_membro(zip_atributos, f"Morador_{uf.upper()}.XLS", destino)
    caminho_domicilio = _extrair_membro(zip_atributos, f"Domicilio_{uf.upper()}.XLS", destino)

    morador = pd.read_excel(caminho_morador, engine="xlrd", usecols=["Cod_setor", "Situacao", "V0237"])
    morador["Cod_setor"] = morador["Cod_setor"].astype(str)
    morador = morador[morador["Cod_setor"].str.startswith(codigo_ibge)]

    domicilio = pd.read_excel(caminho_domicilio, engine="xlrd", usecols=["Cod_setor", "V0001", "V0003"])
    domicilio["Cod_setor"] = domicilio["Cod_setor"].astype(str)
    domicilio = domicilio[domicilio["Cod_setor"].str.startswith(codigo_ibge)]

    df = morador.merge(domicilio, on="Cod_setor", how="outer")
    df["situacao"] = df["Situacao"].apply(_situacao_de_codigo)
    df = df.rename(columns={"Cod_setor": "cd_setor", "V0237": "populacao_total", "V0001": "domicilios_total", "V0003": "domicilios_particulares_permanentes"})
    return df[["cd_setor", "situacao", "populacao_total", "domicilios_total", "domicilios_particulares_permanentes"]]


# ---------------------------------------------------------------------------
# Censo 2010
# ---------------------------------------------------------------------------

def montar_malha_2010(codigo_ibge: str, uf: str, sessao: requests.Session, forcar: bool) -> gpd.GeoDataFrame:
    uf_lower = uf.lower()
    zip_malha = CACHE_DIR / f"2010_malha_{uf_lower}.zip"
    _baixar_com_cache(sessao, URL_MALHA_2010.format(uf_lower=uf_lower), zip_malha, forcar)
    shp = _extrair_shapefile(zip_malha, CACHE_DIR / f"2010_malha_{uf_lower}")
    gdf_uf = gpd.read_file(shp)
    if gdf_uf.crs is None:
        gdf_uf = gdf_uf.set_crs(CRS_2010, allow_override=True)
    gdf = gdf_uf[gdf_uf["CD_GEOCODM"] == codigo_ibge].copy()
    gdf = gdf.rename(columns={"CD_GEOCODI": "cd_setor"})[["cd_setor", "geometry"]]
    logger.info("Malha 2010: %d setores (CRS de origem %s)", len(gdf), gdf_uf.crs)
    return gdf.to_crs(CRS_PADRAO)


def carregar_atributos_2010(codigo_ibge: str, uf: str, sessao: requests.Session, forcar: bool) -> pd.DataFrame:
    zip_atributos = CACHE_DIR / f"2010_atributos_{uf.lower()}.zip"
    _baixar_com_cache(sessao, URL_ATRIBUTOS_2010.format(uf_upper=uf.upper()), zip_atributos, forcar)
    destino = CACHE_DIR / f"2010_atributos_{uf.lower()}"

    caminho_pessoa = _extrair_membro(zip_atributos, f"Pessoa03_{uf.upper()}.csv", destino)
    caminho_domicilio = _extrair_membro(zip_atributos, f"Domicilio01_{uf.upper()}.csv", destino)

    pessoa = pd.read_csv(caminho_pessoa, sep=";", encoding="latin1", dtype=str, usecols=["Cod_setor", "Situacao_setor", "V001"])
    pessoa["Cod_setor"] = pessoa["Cod_setor"].astype(str)
    pessoa = pessoa[pessoa["Cod_setor"].str.startswith(codigo_ibge)]
    pessoa["populacao_total"] = pd.to_numeric(pessoa["V001"], errors="coerce")

    domicilio = pd.read_csv(caminho_domicilio, sep=";", encoding="latin1", dtype=str, usecols=["Cod_setor", "V001", "V002"])
    domicilio["Cod_setor"] = domicilio["Cod_setor"].astype(str)
    domicilio = domicilio[domicilio["Cod_setor"].str.startswith(codigo_ibge)]
    domicilio["domicilios_total"] = pd.to_numeric(domicilio["V001"], errors="coerce")
    domicilio["domicilios_particulares_permanentes"] = pd.to_numeric(domicilio["V002"], errors="coerce")

    df = pessoa.merge(domicilio[["Cod_setor", "domicilios_total", "domicilios_particulares_permanentes"]], on="Cod_setor", how="outer")
    df["situacao"] = df["Situacao_setor"].apply(_situacao_de_codigo)
    df = df.rename(columns={"Cod_setor": "cd_setor"})
    return df[["cd_setor", "situacao", "populacao_total", "domicilios_total", "domicilios_particulares_permanentes"]]


# ---------------------------------------------------------------------------
# Comum aos dois anos
# ---------------------------------------------------------------------------

def juntar_e_calcular_densidade(gdf_malha: gpd.GeoDataFrame, df_atributos: pd.DataFrame) -> gpd.GeoDataFrame:
    gdf = gdf_malha.merge(df_atributos, on="cd_setor", how="left")
    gdf["dados_atributivos_ausentes"] = gdf["populacao_total"].isna()
    gdf["area_km2"] = gdf.geometry.area / 1_000_000
    gdf["densidade_demografica_hab_km2"] = (gdf["populacao_total"] / gdf["area_km2"]).where(gdf["area_km2"] > 0)
    return gdf


def calcular_metadados(gdf: gpd.GeoDataFrame, ano: int, codigo_ibge: str, nome_municipio: str, uf: str, crs_origem: dict) -> dict:
    n_total = len(gdf)
    n_sem_atributo = int(gdf["dados_atributivos_ausentes"].sum())
    return {
        "AVISO_NAO_COMPARAVEL_ENTRE_ANOS": (
            "Esta malha NÃO deve ser sobreposta ou comparada geometricamente com a de outros anos censitários "
            "(2000, 2010, 2022...). O IBGE reconhece desalinhamento de fronteira entre as malhas de setores de "
            "censos diferentes — os limites de um mesmo setor podem mudar, setores podem se fundir/dividir, e a "
            "própria contagem de setores no município muda a cada Censo. Trate cada ano como uma FOTO "
            "independente da divisão territorial vigente naquele Censo, não como um ponto de uma série espacial "
            "contínua. Para série temporal comparável, use a agregação MUNICIPAL em "
            "data/processed/demografia-indicadores_ibge-sidra_1970-2022_municipal.csv (scripts/processamento/"
            "demografia_indicadores.py). O detalhe espacial por setor do Censo 2022 (malha atual) está em "
            "data/raw/vulnerabilidade-censo_ibge_2022.csv (scripts/download/vulnerabilidade_censo.py)."
        ),
        "ano_censo": ano,
        "codigo_ibge": codigo_ibge,
        "nome_municipio": nome_municipio,
        "uf": uf,
        "crs_final": CRS_PADRAO,
        "crs_origem_e_tratamento": crs_origem,
        "n_setores": n_total,
        "n_setores_sem_dado_atributivo": n_sem_atributo,
        "colunas": {
            "cd_setor": "código do setor censitário (15 dígitos, UF+município+distrito+subdistrito+setor)",
            "situacao": "'Urbana' ou 'Rural', derivado do código de situação do setor na fonte de atributos (códigos 1-3 = urbana, 4-8 = rural — mesmo esquema em 2000 e 2010)",
            "populacao_total": "população residente no setor (fonte: ver notas do módulo)",
            "domicilios_total": "domicílios totais (particulares + coletivos) no setor",
            "domicilios_particulares_permanentes": "domicílios particulares permanentes no setor",
            "dados_atributivos_ausentes": "True quando o setor existe na malha (geometria) mas não tem linha correspondente nas tabelas de atributos da fonte — não preenchido com zero para não inventar dado",
            "area_km2": f"calculada a partir da geometria já reprojetada para {CRS_PADRAO}",
            "densidade_demografica_hab_km2": "populacao_total / area_km2",
        },
        "observacao_dados_atributivos_ausentes": (
            f"{n_sem_atributo} de {n_total} setores sem linha nas tabelas de atributos da fonte (população/"
            "domicílios ficam NaN) — confirmado por busca direta do código nos CSVs/XLS de origem, não é erro de "
            "junção; provavelmente setores sem população residente registrada à época (ex.: água, área "
            "non-residencial)."
        ) if n_sem_atributo else "todos os setores da malha têm dado atributivo correspondente na fonte.",
        "data_processamento": datetime.now(timezone.utc).isoformat(),
    }


def processar_ano(ano: int, codigo_ibge: str, nome_municipio: str, uf: str, sessao: requests.Session, forcar: bool) -> None:
    saida = SAIDA_DIR / f"setores-censitarios_ibge_{ano}_vetorial.gpkg"
    if saida.exists() and not forcar:
        logger.info("%s já existe — pulando (use --forcar para refazer).", saida)
        return

    if ano == 2000:
        gdf_malha = montar_malha_2000(codigo_ibge, uf, sessao, forcar)
        df_atributos = carregar_atributos_2000(codigo_ibge, uf, sessao, forcar)
        crs_origem = {
            "malha_urbana": f"{CRS_2000_URBANO} (SAD69 / UTM zone 21S) — FORÇADO: o .prj original é um formato legado do ArcView sem datum explícito e o GDAL o interpreta erradamente como WGS84/UTM 21N; corrigido com base na documentação técnica oficial da Malha Municipal Digital 2000 (elipsoide UGGI67, Datum SAD69)",
            "malha_rural": f"{CRS_2000_RURAL} (SAD69 geográfico) — FORÇADO: a fonte não distribui .prj algum para este produto; mesma referência SAD69 documentada oficialmente para a série",
        }
    elif ano == 2010:
        gdf_malha = montar_malha_2010(codigo_ibge, uf, sessao, forcar)
        df_atributos = carregar_atributos_2010(codigo_ibge, uf, sessao, forcar)
        crs_origem = {
            "malha": f"{CRS_2010} (SIRGAS 2000 geográfico) — CONFIRMADO explicitamente no .prj da própria fonte, sem necessidade de forçar/adivinhar",
        }
    else:
        raise ValueError(f"Ano não suportado: {ano}")

    gdf_final = juntar_e_calcular_densidade(gdf_malha, df_atributos)
    gdf_final = gdf_final.drop(columns=[c for c in ("_fonte_malha",) if c in gdf_final.columns])

    SAIDA_DIR.mkdir(parents=True, exist_ok=True)
    gdf_final.to_file(saida, driver="GPKG", layer="setores_censitarios")
    logger.info("Malha %d salva em %s (%d setores, CRS %s)", ano, saida, len(gdf_final), CRS_PADRAO)

    metadados = calcular_metadados(gdf_final, ano, codigo_ibge, nome_municipio, uf, crs_origem)
    caminho_json = saida.with_suffix(".json")
    caminho_json.write_text(json.dumps(metadados, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    logger.info("Metadados salvos em %s", caminho_json)

    logger.info(
        "%d — população total no setor censitário: %s (soma de populacao_total; comparar com o total do Censo em "
        "data/raw/populacao_ibge-sidra-tabela200_1970-2010_municipal.csv ou populacao-sexo-idade_ibge-sidra-"
        "tabela9514_2022_municipal.csv)",
        ano, int(gdf_final["populacao_total"].sum(skipna=True)),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Baixa a malha de setores censitários (geometria + atributos) dos Censos 2000 e 2010 para um município.")
    parser.add_argument("--codigo-ibge", default=CODIGO_IBGE_DEFAULT, help="Código IBGE do município (default: Uruguaiana/RS)")
    parser.add_argument("--anos", nargs="+", type=int, default=[2000, 2010], help="Anos censitários a processar (default: 2000 2010)")
    parser.add_argument("--forcar", action="store_true", help="Ignora caches e arquivos já existentes e refaz tudo")
    args = parser.parse_args()

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    sessao = requests.Session()
    sessao.headers.update(HEADERS)

    nome_municipio, uf = obter_municipio_uf(args.codigo_ibge, sessao)
    logger.info("Município: %s (%s) — código IBGE %s", nome_municipio, uf, args.codigo_ibge)

    for ano in args.anos:
        logger.info("=== Processando Censo %d ===", ano)
        processar_ano(ano, args.codigo_ibge, nome_municipio, uf, sessao, args.forcar)


if __name__ == "__main__":
    main()
