"""Baixa a localização geográfica e a infraestrutura das escolas de
educação básica de um município, combinando DUAS fontes oficiais do
INEP. Gera:

    data/raw/vetor/escolas_inep-catalogo-censo_atual_vetorial.gpkg
    data/raw/vetor/escolas_inep-catalogo-censo_atual_vetorial.json

Duas fontes combinadas (investigadas antes de codificar)
------------------------------------------------------------
1. Microdados do Censo Escolar (download.inep.gov.br/dados_abertos/
   microdados_censo_escolar_{ano}.zip) — 426 colunas de infraestrutura
   (água, energia, esgoto, acessibilidade, internet, equipamentos,
   matrículas...), filtráveis localmente por CO_MUNICIPIO (código IBGE
   de 7 dígitos, direto na coluna). MAS NÃO TEM latitude/longitude —
   confirmado inspecionando as 426 colunas do arquivo antes de escrever
   qualquer código.
2. Catálogo de Escolas (InepData, Oracle Analytics/OBIEE em
   anonymousdata.inep.gov.br) — tem Latitude/Longitude por escola +
   endereço/categoria/etapas, mas NÃO tem os indicadores de
   infraestrutura. Sempre reflete o Censo Escolar mais recente (não tem
   filtro de ano — é retrato atual, diferente dos microdados que são
   por ano).

As duas são unidas pelo código INEP da escola (CO_ENTIDADE nos
microdados = "Código INEP" no Catálogo — confirmado por amostragem real,
mesmos 8 dígitos, mesmo nome de escola).

Catálogo de Escolas: acesso via automação (sem API REST documentada)
------------------------------------------------------------------------
anonymousdata.inep.gov.br é a URL ATUAL (a documentação antiga apontava
para inepdata.inep.gov.br, que hoje redireciona para uma tela de login
quebrada — descoberto testando as duas). É uma aplicação Oracle
Analytics (OBIEE) com login de convidado automático (usuário
"oasuser1") — sem necessidade de credenciais. Os filtros (UF, Município)
são campos de autocomplete customizados (não <select> nativos); o botão
"Aplicar" real fica fora da largura de viewport padrão (motivo do
`viewport` largo usado aqui) e o texto "Aplicar"/"Exportar" aparece
repetido na página (instruções + rodapé), por isso os seletores usam
`.nth(0)`. O certificado TLS de anonymousdata.inep.gov.br (e de
download.inep.gov.br) falha verificação por CA incompleta no chain —
`ignore_https_errors=True` no Playwright e `verify=False` no requests
(mesmo problema nos dois hosts, ambos infraestrutura do INEP).

Uso:
    python scripts/download/escolas_inep.py
    python scripts/download/escolas_inep.py --codigo-ibge 4314902 --ano-microdados 2024 --forcar
"""

import argparse
import json
import logging
import re
import sys
import time
import zipfile
from datetime import date, datetime, timezone
from io import BytesIO, TextIOWrapper
from pathlib import Path

import geopandas as gpd
import pandas as pd
import requests
import urllib3
from playwright.sync_api import sync_playwright
from shapely.geometry import Point

sys.path.append(str(Path(__file__).resolve().parents[1] / "utils"))
from recorte_municipio import CRS_PADRAO  # noqa: E402

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

RAIZ = Path(__file__).resolve().parents[2]
CAMINHO_SAIDA = RAIZ / "data" / "raw" / "vetor" / "escolas_inep-catalogo-censo_atual_vetorial.gpkg"

CODIGO_IBGE_DEFAULT = "4322400"  # Uruguaiana, RS
ANO_MICRODADOS_DEFAULT = 2024
CRS_ORIGEM_CATALOGO = "EPSG:4674"  # SIRGAS2000 geográfico — padrão oficial brasileiro (não documentado explicitamente pela fonte, mas confirmado pela comunidade GIS que usa a mesma camada)

URL_MICRODADOS = "https://download.inep.gov.br/dados_abertos/microdados_censo_escolar_{ano}.zip"
URL_MUNICIPIO_IBGE = "https://servicodados.ibge.gov.br/api/v1/localidades/municipios/{codigo}"
URL_CATALOGO_PORTAL = (
    "https://anonymousdata.inep.gov.br/analytics/saw.dll?Portal&PortalPath="
    "%2Fshared%2FCenso%20da%20Educa%C3%A7%C3%A3o%20B%C3%A1sica%2F_portal%2FCat%C3%A1logo%20de%20Escolas"
)

COLUNAS_INFRAESTRUTURA = [
    "TP_DEPENDENCIA", "TP_LOCALIZACAO", "TP_SITUACAO_FUNCIONAMENTO",
    "IN_AGUA_POTAVEL", "IN_AGUA_REDE_PUBLICA", "IN_AGUA_POCO_ARTESIANO", "IN_AGUA_CACIMBA", "IN_AGUA_FONTE_RIO", "IN_AGUA_INEXISTENTE", "IN_AGUA_CARRO_PIPA",
    "IN_ENERGIA_REDE_PUBLICA", "IN_ENERGIA_GERADOR_FOSSIL", "IN_ENERGIA_RENOVAVEL", "IN_ENERGIA_INEXISTENTE",
    "IN_ESGOTO_REDE_PUBLICA", "IN_ESGOTO_FOSSA_SEPTICA", "IN_ESGOTO_FOSSA_COMUM", "IN_ESGOTO_FOSSA", "IN_ESGOTO_INEXISTENTE",
    "IN_LIXO_SERVICO_COLETA", "IN_LIXO_QUEIMA", "IN_LIXO_ENTERRA", "IN_LIXO_DESCARTA_OUTRA_AREA",
    "IN_BANHEIRO", "IN_BANHEIRO_PNE", "IN_BIBLIOTECA", "IN_BIBLIOTECA_SALA_LEITURA", "IN_COZINHA",
    "IN_LABORATORIO_CIENCIAS", "IN_LABORATORIO_INFORMATICA", "IN_QUADRA_ESPORTES", "IN_QUADRA_ESPORTES_COBERTA", "IN_QUADRA_ESPORTES_DESCOBERTA",
    "IN_PATIO_COBERTO", "IN_PATIO_DESCOBERTO", "IN_PARQUE_INFANTIL", "IN_REFEITORIO",
    "IN_ACESSIBILIDADE_CORRIMAO", "IN_ACESSIBILIDADE_ELEVADOR", "IN_ACESSIBILIDADE_PISOS_TATEIS",
    "IN_ACESSIBILIDADE_RAMPAS", "IN_ACESSIBILIDADE_SINAL_SONORO", "IN_ACESSIBILIDADE_SINAL_TATIL",
    "IN_ACESSIBILIDADE_SINAL_VISUAL", "IN_ACESSIBILIDADE_INEXISTENTE",
    "IN_COMPUTADOR", "IN_INTERNET", "IN_INTERNET_ALUNOS", "IN_BANDA_LARGA",
    "QT_SALAS_UTILIZADAS", "QT_MAT_BAS", "QT_DOC_BAS",
    "IN_ALIMENTACAO",
]


def obter_municipio_uf(codigo_ibge: str) -> tuple[str, str]:
    resposta = requests.get(URL_MUNICIPIO_IBGE.format(codigo=codigo_ibge), timeout=30)
    resposta.raise_for_status()
    dados = resposta.json()
    return dados["nome"], dados["microrregiao"]["mesorregiao"]["UF"]["sigla"]


def _clicar_texto_visivel(page, texto: str, exato: bool = True, indice: int = 0, force: bool = False):
    locator = page.get_by_text(texto, exact=exato)
    locator.nth(indice).click(force=force)


def baixar_catalogo_escolas(nome_municipio: str, uf_sigla: str, n_tentativas: int = 3) -> pd.DataFrame:
    """Automação do Catálogo de Escolas (OBIEE) — sem API REST, ver docstring do
    módulo para o porquê de cada passo. Único ponto do script que precisa de
    navegador (Playwright); o resto é HTTP puro."""
    ultimo_erro = None
    for tentativa in range(1, n_tentativas + 1):
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch()
                context = browser.new_context(ignore_https_errors=True, viewport={"width": 1800, "height": 900}, accept_downloads=True)
                page = context.new_page()
                page.goto(URL_CATALOGO_PORTAL, wait_until="networkidle", timeout=45000)
                page.wait_for_timeout(2000)

                inputs = page.query_selector_all("input.promptTextField")
                uf_input = inputs[1]
                uf_input.click(force=True)
                page.wait_for_timeout(500)
                page.keyboard.type(uf_sigla, delay=150)
                page.wait_for_timeout(1200)
                page.keyboard.press("ArrowDown")
                page.wait_for_timeout(300)
                page.keyboard.press("Enter")
                page.wait_for_timeout(4000)
                page.wait_for_load_state("networkidle", timeout=15000)

                valor_uf = page.query_selector_all("input.promptTextField")[1].input_value()
                if valor_uf.strip().upper() != uf_sigla.upper():
                    raise RuntimeError(f"UF não confirmada no campo (esperado '{uf_sigla}', campo mostra '{valor_uf}')")

                inputs2 = page.query_selector_all("input.promptTextField")
                mun_input = inputs2[2]
                mun_input.click(force=True)
                page.wait_for_timeout(500)
                mun_input.click(click_count=3)
                page.wait_for_timeout(300)
                page.keyboard.type(nome_municipio, delay=150)
                page.wait_for_timeout(1500)
                page.keyboard.press("ArrowDown")
                page.wait_for_timeout(300)
                page.keyboard.press("Enter")
                page.wait_for_timeout(1500)

                valor_mun = page.query_selector_all("input.promptTextField")[2].input_value()
                if valor_mun.strip().lower() != nome_municipio.strip().lower():
                    raise RuntimeError(f"Município não confirmado no campo (esperado '{nome_municipio}', campo mostra '{valor_mun}')")

                _clicar_texto_visivel(page, "Aplicar")
                page.wait_for_timeout(3000)
                page.wait_for_load_state("networkidle", timeout=20000)

                texto_resultado = page.locator("text=/Foram selecionadas/").inner_text(timeout=10000)
                match_contagem = re.search(r"Foram selecionadas\s+(\d+)\s+escolas", texto_resultado)
                if not match_contagem:
                    raise RuntimeError(f"Não encontrei a contagem de escolas no texto de resultado: {texto_resultado!r}")
                n_escolas_relatado = int(match_contagem.group(1))
                logger.info("Catálogo de Escolas: %d escolas selecionadas.", n_escolas_relatado)
                if n_escolas_relatado > 5000:
                    raise RuntimeError(f"Contagem suspeita ({n_escolas_relatado} escolas) — filtro de UF/Município provavelmente não aplicou; abortando esta tentativa")

                with page.expect_download(timeout=60000) as dl_info:
                    _clicar_texto_visivel(page, "Exportar", force=True)
                download = dl_info.value
                caminho_temp = RAIZ / "data" / "raw" / "cache_inep_catalogo.csv"
                download.save_as(caminho_temp)
                browser.close()

            df = pd.read_csv(caminho_temp, encoding="utf-8-sig")
            caminho_temp.unlink()
            if len(df) > 5000:
                raise RuntimeError(f"Arquivo exportado tem {len(df)} linhas — filtro não aplicou, descartando resultado")
            return df
        except Exception as erro:  # noqa: BLE001 — automação de navegador é frágil, qualquer falha vale retry
            ultimo_erro = erro
            logger.warning("Catálogo de Escolas: tentativa %d/%d falhou (%s)", tentativa, n_tentativas, erro)
            time.sleep(3)
    raise RuntimeError(f"Falha ao baixar o Catálogo de Escolas após {n_tentativas} tentativas: {ultimo_erro}")


def baixar_microdados_municipio(ano: int, codigo_ibge: str) -> pd.DataFrame:
    """Baixa o ZIP nacional do Censo Escolar (~30-40MB) e lê, em streaming, só as
    linhas do município alvo — o CSV interno (~200MB descomprimido) nunca é
    escrito em disco por inteiro."""
    url = URL_MICRODADOS.format(ano=ano)
    logger.info("Baixando microdados do Censo Escolar %d (~30-40MB)...", ano)
    headers = {"User-Agent": "Mozilla/5.0 (compativel; script-pesquisa-ClimaPampa/1.0)"}
    resposta = None
    ultimo_erro = None
    for tentativa in range(1, 4):
        try:
            resposta = requests.get(url, timeout=180, verify=False, headers=headers)  # noqa: S501 — CA incompleta no host, ver docstring
            resposta.raise_for_status()
            break
        except requests.RequestException as erro:
            ultimo_erro = erro
            logger.warning("Download dos microdados: tentativa %d/3 falhou (%s)", tentativa, erro)
            time.sleep(3 * tentativa)
    else:
        raise RuntimeError(f"Falha ao baixar microdados após 3 tentativas: {ultimo_erro}")

    with zipfile.ZipFile(BytesIO(resposta.content)) as z:
        nome_csv = next(n for n in z.namelist() if n.endswith(f"microdados_ed_basica_{ano}.csv"))
        partes = []
        with z.open(nome_csv) as f:
            leitor = pd.read_csv(TextIOWrapper(f, encoding="latin1"), sep=";", chunksize=200_000, dtype=str, low_memory=False)
            for bloco in leitor:
                filtrado = bloco[bloco["CO_MUNICIPIO"] == codigo_ibge]
                if not filtrado.empty:
                    partes.append(filtrado)

    if not partes:
        return pd.DataFrame(columns=["CO_ENTIDADE", *COLUNAS_INFRAESTRUTURA])
    df = pd.concat(partes, ignore_index=True)
    colunas_presentes = [c for c in ["CO_ENTIDADE", "NO_ENTIDADE", *COLUNAS_INFRAESTRUTURA] if c in df.columns]
    return df[colunas_presentes]


def main() -> None:
    parser = argparse.ArgumentParser(description="Baixa localização + infraestrutura das escolas (INEP) de um município.")
    parser.add_argument("--codigo-ibge", default=CODIGO_IBGE_DEFAULT, help="Código IBGE do município (default: Uruguaiana/RS)")
    parser.add_argument("--ano-microdados", type=int, default=ANO_MICRODADOS_DEFAULT, help="Ano do Censo Escolar para os dados de infraestrutura (default: último disponível)")
    parser.add_argument("--forcar", action="store_true", help="Baixa novamente mesmo se o arquivo já existir")
    args = parser.parse_args()

    if CAMINHO_SAIDA.exists() and not args.forcar:
        logger.info("%s já existe — nada a fazer (use --forcar para baixar de novo).", CAMINHO_SAIDA)
        return

    nome_municipio, uf_sigla = obter_municipio_uf(args.codigo_ibge)
    logger.info("Município: %s (%s) — código IBGE %s", nome_municipio, uf_sigla, args.codigo_ibge)

    df_catalogo = baixar_catalogo_escolas(nome_municipio, uf_sigla)
    logger.info("Catálogo de Escolas: %d escolas encontradas para %s/%s.", len(df_catalogo), nome_municipio, uf_sigla)

    df_micro = baixar_microdados_municipio(args.ano_microdados, args.codigo_ibge)
    logger.info("Microdados %d: %d escolas encontradas para o código IBGE %s.", args.ano_microdados, len(df_micro), args.codigo_ibge)

    df_catalogo["codigo_inep"] = df_catalogo["Código INEP"].astype(str)
    df_micro["codigo_inep"] = df_micro["CO_ENTIDADE"].astype(str)

    df_unido = df_catalogo.merge(df_micro.drop(columns=["NO_ENTIDADE"], errors="ignore"), on="codigo_inep", how="left")
    n_sem_infraestrutura = df_unido["TP_DEPENDENCIA"].isna().sum() if "TP_DEPENDENCIA" in df_unido.columns else None

    df_unido["Latitude"] = pd.to_numeric(df_unido["Latitude"].astype(str).str.strip(), errors="coerce")
    df_unido["Longitude"] = pd.to_numeric(df_unido["Longitude"].astype(str).str.strip(), errors="coerce")
    n_sem_coordenada = df_unido["Latitude"].isna().sum()

    com_coordenada = df_unido[df_unido["Latitude"].notna() & df_unido["Longitude"].notna()].copy()
    geometria = [Point(lon, lat) for lat, lon in zip(com_coordenada["Latitude"], com_coordenada["Longitude"])]
    gdf = gpd.GeoDataFrame(com_coordenada.drop(columns=["Latitude", "Longitude"]), geometry=geometria, crs=CRS_ORIGEM_CATALOGO)
    gdf = gdf.to_crs(CRS_PADRAO)

    CAMINHO_SAIDA.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_file(CAMINHO_SAIDA, driver="GPKG", layer="escolas")

    metadados = {
        "fontes": {
            "catalogo_de_escolas": {
                "descricao": "Catálogo de Escolas (InepData / Oracle Analytics) — localização geográfica e informações administrativas, sempre retrato do Censo Escolar mais recente",
                "url": URL_CATALOGO_PORTAL,
                "metodo": "automação de navegador (Playwright) — sem API REST documentada, ver docstring do script",
            },
            "microdados_censo_escolar": {
                "descricao": "Microdados do Censo Escolar — infraestrutura, matrículas, docentes",
                "url": URL_MICRODADOS.format(ano=args.ano_microdados),
                "ano": args.ano_microdados,
                "metodo": "download do ZIP nacional (~30-40MB), filtrado localmente por CO_MUNICIPIO em streaming (CSV de ~200MB nunca gravado por inteiro em disco)",
            },
        },
        "codigo_ibge": args.codigo_ibge,
        "nome_municipio": nome_municipio,
        "uf": uf_sigla,
        "chave_de_juncao": "Código INEP (Catálogo de Escolas) = CO_ENTIDADE (microdados) — 8 dígitos, confirmado por amostragem real",
        "n_escolas_catalogo": len(df_catalogo),
        "n_escolas_microdados": len(df_micro),
        "n_escolas_sem_infraestrutura_no_ano_pedido": None if n_sem_infraestrutura is None else int(n_sem_infraestrutura),
        "n_escolas_sem_coordenada": int(n_sem_coordenada),
        "n_escolas_na_camada_espacial": len(gdf),
        "nota_escolas_sem_coordenada": (
            "escolas sem Latitude/Longitude no Catálogo (não geocodificadas pela fonte) ficam FORA da "
            "camada espacial — não é possível plotá-las sem inventar coordenada; contagem documentada "
            "acima para não serem esquecidas em análises de cobertura"
        ),
        "crs_original": f"{CRS_ORIGEM_CATALOGO} (SIRGAS2000 geográfico — assumido pelo padrão oficial brasileiro, não documentado explicitamente pela fonte)",
        "crs_processado": CRS_PADRAO,
        "colunas_infraestrutura": COLUNAS_INFRAESTRUTURA,
        "data_acesso": date.today().isoformat(),
        "data_processamento": datetime.now(timezone.utc).isoformat(),
    }
    CAMINHO_SAIDA.with_suffix(".json").write_text(json.dumps(metadados, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

    logger.info("Salvo: %s (%d escolas com coordenada, de %d no catálogo total)", CAMINHO_SAIDA, len(gdf), len(df_catalogo))
    logger.info("Metadados salvos em %s", CAMINHO_SAIDA.with_suffix(".json"))


if __name__ == "__main__":
    main()
