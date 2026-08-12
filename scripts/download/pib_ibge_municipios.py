"""
Baixa a série do PIB dos Municípios (IBGE, Contas Nacionais/Regionais,
SIDRA tabela 5938) para um município: PIB total, impostos, Valor
Adicionado Bruto (VAB) total e por atividade econômica (agropecuária,
indústria, serviços, administração pública). Gera:

    data/raw/pib-municipal_ibge-sidra-tabela5938_2002-2023_municipal.csv
    data/raw/pib-municipal_ibge-sidra-tabela5938_2002-2023_municipal.json
    data/processed/pib-per-capita_ibge-sidra_2002-2023_municipal.csv
    data/processed/pib-per-capita_ibge-sidra_2002-2023_municipal.json

API/granularidade (investigado antes de codificar)
---------------------------------------------------
SIDRA tem consulta direta por código IBGE (N6), sem precisar baixar
planilha nacional — mesma API já usada em demografia_ibge_sidra.py e
agropecuaria_ibge_pam_ppm.py. Nível único municipal (sem abertura por
distrito/setor) — tratado como indicador de CONTEXTO, sem virar camada
espacial, mesma decisão já usada para PAM/PPM e IDHM.

Diferente de PAM/PPM e da tabela do IDHM, esta tabela NÃO usa uma
classificação para as atividades econômicas — cada atividade (VAB
agropecuária/indústria/serviços/administração) é uma VARIÁVEL separada
(37/496/498/513/516/517/520/6575/6574/525/528/543), não uma categoria
dentro de uma classificação comum. Confirmado consultando os metadados
da tabela antes de montar a query.

Defasagem real dos dados (confirmada por consulta, não suposição)
------------------------------------------------------------------------
O PIB total (variável 37) tem série até 2023, mas o detalhamento por
Valor Adicionado Bruto e atividade econômica (variáveis 498, 513, 517,
6575, 525 e as respectivas participações %) só vem preenchido até 2021
— 2022 e 2023 retornam "..." (sem dado) nessas colunas específicas,
mesmo com o PIB total já disponível para esses anos. Preservado como
NaN, não como zero.

PIB per capita: calculado localmente (NÃO é variável nativa da tabela 5938)
--------------------------------------------------------------------------------
A tabela 5938 não publica PIB per capita como variável própria. Calculado
aqui como PIB total (mil R$) / população residente do mesmo ano,
reaproveitando a série de população já baixada em
demografia_ibge_sidra.py (tabela 6579, estimativas anuais) e
complementada com os totais censitários de 2010 e 2022 (que a tabela
6579 não publica, por serem anos de Censo) consultados das tabelas
200 e 9514 respectivamente. Anos sem nenhuma fonte de população no
projeto (2007 e 2023 — Contagens da População não baixadas, mesma
lacuna documentada em demografia_ibge_sidra.py) ficam com
pib_per_capita_reais = NaN, não interpolados.

Uso:
    python scripts/download/pib_ibge_municipios.py
    python scripts/download/pib_ibge_municipios.py --codigo-ibge 4314902 --forcar
"""

import argparse
import json
import logging
import time
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

RAIZ = Path(__file__).resolve().parents[2]
CODIGO_IBGE_DEFAULT = "4322400"  # Uruguaiana, RS

URL_SIDRA_BASE = "https://servicodados.ibge.gov.br/api/v3/agregados"
URL_MUNICIPIO_IBGE = "https://servicodados.ibge.gov.br/api/v1/localidades/municipios/{codigo}"
HEADERS = {"User-Agent": "Mozilla/5.0 (compativel; script-pesquisa-ClimaPampa/1.0)"}
N_TENTATIVAS = 3
BACKOFF_BASE_S = 1.0

TABELA_PIB = 5938
PERIODO_INICIO = 2002
PERIODO_FIM = 2023

VARIAVEIS_PIB = {
    37: "PIB a preços correntes (mil R$)",
    496: "Participação do PIB no PIB do Brasil (%)",
    543: "Impostos líquidos de subsídios (mil R$)",
    498: "VAB total (mil R$)",
    513: "VAB agropecuária (mil R$)",
    516: "VAB agropecuária — % do VAB total do município",
    517: "VAB indústria (mil R$)",
    520: "VAB indústria — % do VAB total do município",
    6575: "VAB serviços, exclusive administração pública (mil R$)",
    6574: "VAB serviços — % do VAB total do município",
    525: "VAB administração, defesa, educação e saúde públicas (mil R$)",
    528: "VAB administração pública — % do VAB total do município",
}

RAW_DIR = RAIZ / "data" / "raw"
PROC_DIR = RAIZ / "data" / "processed"
CAMINHO_RAW = RAW_DIR / f"pib-municipal_ibge-sidra-tabela{TABELA_PIB}_{PERIODO_INICIO}-{PERIODO_FIM}_municipal.csv"
CAMINHO_PROC = PROC_DIR / f"pib-per-capita_ibge-sidra_{PERIODO_INICIO}-{PERIODO_FIM}_municipal.csv"
CAMINHO_POPULACAO_ESTIMADA = RAW_DIR / "populacao-estimada_ibge-sidra-tabela6579_2001-2025_municipal.csv"

VALORES_AUSENTES = {None, "...", "-", "X", ".."}


def _requisitar_com_retry(url: str, params: dict) -> requests.Response:
    ultimo_erro = None
    for tentativa in range(1, N_TENTATIVAS + 1):
        try:
            resposta = requests.get(url, params=params, headers=HEADERS, timeout=60)
            resposta.raise_for_status()
            return resposta
        except requests.RequestException as erro:
            ultimo_erro = erro
            if tentativa < N_TENTATIVAS:
                espera = BACKOFF_BASE_S * (2 ** (tentativa - 1))
                logger.warning("Falha na requisição %s (tentativa %d/%d): %s — nova tentativa em %.0fs", url, tentativa, N_TENTATIVAS, erro, espera)
                time.sleep(espera)
    raise RuntimeError(f"Falha ao requisitar {url} após {N_TENTATIVAS} tentativas: {ultimo_erro}") from ultimo_erro


def obter_municipio_uf(codigo_ibge: str) -> tuple[str, str]:
    resposta = _requisitar_com_retry(URL_MUNICIPIO_IBGE.format(codigo=codigo_ibge), {})
    dados = resposta.json()
    return dados["nome"], dados["microrregiao"]["mesorregiao"]["UF"]["sigla"]


def obter_populacao_total(tabela: int, periodo: int, variavel: int, classificacao: str, codigo_ibge: str) -> float | None:
    url = f"{URL_SIDRA_BASE}/{tabela}/periodos/{periodo}/variaveis/{variavel}"
    resposta = _requisitar_com_retry(url, {"localidades": f"N6[{codigo_ibge}]", "classificacao": classificacao})
    dados = resposta.json()
    valor_str = dados[0]["resultados"][0]["series"][0]["serie"].get(str(periodo))
    return None if valor_str in VALORES_AUSENTES else float(valor_str)


def baixar_pib(codigo_ibge: str) -> pd.DataFrame:
    ids_variaveis = ",".join(str(v) for v in VARIAVEIS_PIB)
    url = f"{URL_SIDRA_BASE}/{TABELA_PIB}/periodos/{PERIODO_INICIO}-{PERIODO_FIM}/variaveis/{ids_variaveis}"
    resposta = _requisitar_com_retry(url, {"localidades": f"N6[{codigo_ibge}]"})
    dados = resposta.json()

    linhas = []
    for bloco in dados:
        id_variavel = int(bloco["id"])
        serie = bloco["resultados"][0]["series"][0]["serie"]
        for periodo, valor_str in serie.items():
            valor = None if valor_str in VALORES_AUSENTES else float(valor_str)
            linhas.append({"ano": int(periodo), "id_variavel": id_variavel, "variavel": VARIAVEIS_PIB[id_variavel], "valor": valor})
    return pd.DataFrame(linhas).sort_values(["id_variavel", "ano"]).reset_index(drop=True)


def montar_serie_populacao(codigo_ibge: str) -> dict[int, float]:
    """Reaproveita a série de estimativas já baixada (tabela 6579) e completa 2010/2022
    (anos de Censo, ausentes daquela tabela) com consulta direta às tabelas 200 e 9514 —
    mesmas tabelas/categorias já usadas em demografia_ibge_sidra.py."""
    populacao_por_ano: dict[int, float] = {}
    if CAMINHO_POPULACAO_ESTIMADA.exists():
        df_pop = pd.read_csv(CAMINHO_POPULACAO_ESTIMADA)
        populacao_por_ano = dict(zip(df_pop["periodo"].astype(int), df_pop["populacao"]))
    else:
        logger.warning("%s não encontrado — rode demografia_ibge_sidra.py primeiro para uma série de população mais completa.", CAMINHO_POPULACAO_ESTIMADA)

    if 2010 not in populacao_por_ano:
        populacao_por_ano[2010] = obter_populacao_total(200, 2010, 93, "2[0]|1[0]|58[0]", codigo_ibge)
    if 2022 not in populacao_por_ano:
        populacao_por_ano[2022] = obter_populacao_total(9514, 2022, 93, "2[6794]|287[100362]|286[113635]", codigo_ibge)
    return populacao_por_ano


def main() -> None:
    parser = argparse.ArgumentParser(description="Baixa a série do PIB dos Municípios (IBGE/SIDRA) para um município e calcula PIB per capita.")
    parser.add_argument("--codigo-ibge", default=CODIGO_IBGE_DEFAULT, help="Código IBGE do município (default: Uruguaiana/RS)")
    parser.add_argument("--forcar", action="store_true", help="Ignora arquivos já existentes e baixa tudo de novo")
    args = parser.parse_args()

    if CAMINHO_RAW.exists() and CAMINHO_PROC.exists() and not args.forcar:
        logger.info("Arquivos já existem — nada a fazer (use --forcar para baixar de novo).")
        return

    nome_municipio, uf_sigla = obter_municipio_uf(args.codigo_ibge)
    logger.info("Município: %s (%s) — código IBGE %s", nome_municipio, uf_sigla, args.codigo_ibge)

    df_pib = baixar_pib(args.codigo_ibge)
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    df_pib.to_csv(CAMINHO_RAW, index=False, encoding="utf-8")

    ultimo_ano_com_vab = int(df_pib[(df_pib.id_variavel == 498) & df_pib.valor.notna()]["ano"].max())
    ultimo_ano_com_pib_total = int(df_pib[(df_pib.id_variavel == 37) & df_pib.valor.notna()]["ano"].max())
    logger.info("PIB total disponível até %d; detalhamento por VAB/atividade econômica disponível até %d.", ultimo_ano_com_pib_total, ultimo_ano_com_vab)

    metadados_raw = {
        "fonte": "IBGE — SIDRA, tabela 5938 (Produto Interno Bruto dos Municípios, Referência 2010)",
        "url_api": f"{URL_SIDRA_BASE}/{TABELA_PIB}",
        "tabela_sidra": TABELA_PIB,
        "codigo_ibge": args.codigo_ibge,
        "nome_municipio": nome_municipio,
        "uf": uf_sigla,
        "periodo_coberto": f"{PERIODO_INICIO}-{PERIODO_FIM}",
        "nivel_agregacao": "municipal ÚNICO — sem abertura por distrito/setor; tratado como indicador de contexto, sem camada espacial (mesma decisão de PAM/PPM e IDHM)",
        "variaveis": VARIAVEIS_PIB,
        "limitacao_defasagem": (
            f"PIB total (variável 37) disponível até {ultimo_ano_com_pib_total}, mas o detalhamento por Valor "
            f"Adicionado Bruto e atividade econômica só vem preenchido até {ultimo_ano_com_vab} — anos "
            "posteriores retornam NaN nessas colunas (não é zero, é ausência de dado nesta tabela SIDRA "
            "no momento da coleta)"
        ),
        "estrutura_tabela": "cada atividade econômica é uma VARIÁVEL separada nesta tabela (não uma classificação/categoria compartilhada) — diferente do padrão usado em PAM/PPM",
        "data_acesso": date.today().isoformat(),
        "data_processamento": datetime.now(timezone.utc).isoformat(),
    }
    CAMINHO_RAW.with_suffix(".json").write_text(json.dumps(metadados_raw, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    logger.info("Salvo: %s (%d linhas)", CAMINHO_RAW, len(df_pib))

    populacao_por_ano = montar_serie_populacao(args.codigo_ibge)
    pib_total_por_ano = dict(zip(df_pib[df_pib.id_variavel == 37]["ano"], df_pib[df_pib.id_variavel == 37]["valor"]))

    linhas_percapita = []
    anos_sem_populacao = []
    for ano, pib_mil_reais in pib_total_por_ano.items():
        populacao = populacao_por_ano.get(ano)
        if populacao is None:
            anos_sem_populacao.append(ano)
            per_capita = None
        else:
            per_capita = (pib_mil_reais * 1000) / populacao if pib_mil_reais is not None else None
        linhas_percapita.append({"ano": ano, "pib_mil_reais": pib_mil_reais, "populacao": populacao, "pib_per_capita_reais": per_capita})

    df_percapita = pd.DataFrame(linhas_percapita).sort_values("ano").reset_index(drop=True)
    PROC_DIR.mkdir(parents=True, exist_ok=True)
    df_percapita.to_csv(CAMINHO_PROC, index=False, encoding="utf-8")

    metadados_proc = {
        "fonte": "Derivado — PIB total (IBGE/SIDRA tabela 5938) / população residente (IBGE/SIDRA tabelas 6579, 200, 9514)",
        "metodo": "pib_per_capita_reais = (pib_mil_reais * 1000) / populacao do mesmo ano — PIB per capita NÃO é variável nativa da tabela 5938, calculado localmente",
        "codigo_ibge": args.codigo_ibge,
        "nome_municipio": nome_municipio,
        "uf": uf_sigla,
        "fonte_populacao": (
            "tabela 6579 (estimativas anuais) para a maioria dos anos; 2010 e 2022 (anos de Censo, ausentes "
            "da 6579) completados com as tabelas 200 e 9514 respectivamente — mesmas fontes já usadas em "
            "demografia_ibge_sidra.py"
        ),
        "anos_sem_populacao_disponivel": anos_sem_populacao,
        "nota_anos_sem_populacao": (
            "2007 e 2023 não têm nenhuma fonte de população neste projeto (Contagens da População desses "
            "anos não foram baixadas, mesma lacuna documentada em demografia_ibge_sidra.py) — "
            "pib_per_capita_reais fica NaN nesses anos, não interpolado"
        ),
        "data_processamento": datetime.now(timezone.utc).isoformat(),
    }
    CAMINHO_PROC.with_suffix(".json").write_text(json.dumps(metadados_proc, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    logger.info("Salvo: %s (%d linhas, %d anos sem população disponível: %s)", CAMINHO_PROC, len(df_percapita), len(anos_sem_populacao), anos_sem_populacao)


if __name__ == "__main__":
    main()
