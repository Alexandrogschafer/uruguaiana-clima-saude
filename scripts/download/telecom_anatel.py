"""Baixa a cobertura de telefonia móvel (2G/3G/4G/5G, por operadora) POR
SETOR CENSITÁRIO para um município, a partir do pacote público da ANATEL.
Gera:

    data/raw/cobertura-movel_anatel_2021-{ultimo_periodo}_setor-censitario.csv
    data/raw/cobertura-movel_anatel_2021-{ultimo_periodo}_setor-censitario.json

Granularidade real (investigada antes de codificar) — mais fina do que o
esperado
------------------------------------------------------------------------------
O pedido original assumia só nível municipal ou por operadora/tecnologia
agregado. Investigação real encontrou algo melhor: a ANATEL publica
trimestralmente (desde 2021-11) um arquivo de cobertura móvel POR SETOR
CENSITÁRIO — % da população do setor coberta por tecnologia (2G/3G/4G/5G
e combinações), por operadora, usando o MESMO código de setor censitário
2022 (15 dígitos) já usado nas camadas de setores deste projeto
(setores_censitarios_historico.py / vulnerabilidade_censo.py). Decisão do
usuário (2026-08-11): baixar essa versão por setor (vira camada espacial
real por JOIN com a malha de setores já existente — não gera geometria
nova aqui, só a tabela de atributos, para não duplicar a malha).

Existe uma base ainda mais fina (estações/antenas com latitude/longitude
individual, ~662MB nacional) — NÃO baixada nesta rodada (decisão do
usuário): ~23% das coordenadas vêm mascaradas ("*") na versão pública, e
o dado por setor já é oficial/pré-agregado pela própria ANATEL (mais
confiável que inferir área de cobertura a partir de pontos de antena).

Fonte e método
----------------
https://www.anatel.gov.br/dadosabertos/paineis_de_dados/infraestrutura/cobertura_movel.zip
(~307MB, contém um CSV por trimestre desde 2021-11, nível setor E nível
município). Baixado uma vez só (cache local) — cada trimestre é extraído
e filtrado localmente por prefixo do código do setor (código IBGE do
município, 7 dígitos), sem re-baixar nada por período.

Uso:
    python scripts/download/telecom_anatel.py
    python scripts/download/telecom_anatel.py --codigo-ibge 4314902 --forcar
"""

import argparse
import json
import logging
import re
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

RAIZ = Path(__file__).resolve().parents[2]
CODIGO_IBGE_DEFAULT = "4322400"  # Uruguaiana, RS

URL_ZIP = "https://www.anatel.gov.br/dadosabertos/paineis_de_dados/infraestrutura/cobertura_movel.zip"
CAMINHO_CACHE_ZIP = RAIZ / "data" / "raw" / "cache_anatel_cobertura_movel.zip"
HEADERS = {"User-Agent": "Mozilla/5.0 (compativel; script-pesquisa-ClimaPampa/1.0)"}

COLUNAS = [
    "Período", "Operadora", "Código Setor Censitário", "Ano Censo",
    "Cobertura_2G", "Cobertura_3G", "Cobertura_4G", "Cobertura_5G",
    "Cobertura_3G4G5G", "Cobertura_4G5G", "Cobertura_Todas",
]


def baixar_zip_com_cache() -> Path:
    if CAMINHO_CACHE_ZIP.exists():
        logger.info("Usando ZIP em cache: %s", CAMINHO_CACHE_ZIP)
        return CAMINHO_CACHE_ZIP
    logger.info("Baixando %s (~307MB, só uma vez)...", URL_ZIP)
    CAMINHO_CACHE_ZIP.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(URL_ZIP, headers=HEADERS, timeout=300, stream=True) as resposta:
        resposta.raise_for_status()
        with open(CAMINHO_CACHE_ZIP, "wb") as f:
            for bloco in resposta.iter_content(chunk_size=1024 * 1024):
                f.write(bloco)
    logger.info("Baixado: %s", CAMINHO_CACHE_ZIP)
    return CAMINHO_CACHE_ZIP


def listar_membros_por_setor(caminho_zip: Path) -> list[str]:
    with zipfile.ZipFile(caminho_zip) as z:
        nomes = z.namelist()
    padrao = re.compile(r"^Cobertura_(\d{4})_(\d{2})_Setores\.csv$")
    membros = [n for n in nomes if padrao.match(n)]
    # os 2 mais recentes (2026_03, 2026_06) vieram sem sufixo "_Setores" no
    # nome de arquivo além do já listado; a versão "_Setores" já cobre
    # esses períodos também (confirmado no índice do zip), então basta o padrão acima
    return sorted(membros)


def extrair_e_filtrar_periodo(caminho_zip: Path, nome_membro: str, codigo_ibge: str) -> pd.DataFrame:
    """Períodos mais antigos (antes do 5G chegar ao Brasil) não têm todas as colunas
    de tecnologia — colunas ausentes viram NaN em vez de quebrar (achado real, não
    suposição: o primeiro período do pacote, 2021-11, não tem Cobertura_5G/3G4G5G/4G5G)."""
    with zipfile.ZipFile(caminho_zip) as z:
        with z.open(nome_membro) as f:
            leitor = pd.read_csv(f, sep=";", chunksize=300_000, dtype=str, encoding="utf-8-sig")
            partes = []
            for bloco in leitor:
                for coluna in COLUNAS:
                    if coluna not in bloco.columns:
                        bloco[coluna] = pd.NA
                filtrado = bloco[bloco["Código Setor Censitário"].str.startswith(codigo_ibge, na=False)]
                if not filtrado.empty:
                    partes.append(filtrado[COLUNAS])
    return pd.concat(partes, ignore_index=True) if partes else pd.DataFrame(columns=COLUNAS)


def obter_municipio_uf(codigo_ibge: str) -> tuple[str, str]:
    resposta = requests.get(f"https://servicodados.ibge.gov.br/api/v1/localidades/municipios/{codigo_ibge}", timeout=30)
    resposta.raise_for_status()
    dados = resposta.json()
    return dados["nome"], dados["microrregiao"]["mesorregiao"]["UF"]["sigla"]


def main() -> None:
    parser = argparse.ArgumentParser(description="Baixa cobertura móvel por setor censitário (ANATEL) para um município.")
    parser.add_argument("--codigo-ibge", default=CODIGO_IBGE_DEFAULT, help="Código IBGE do município (default: Uruguaiana/RS)")
    parser.add_argument("--forcar", action="store_true", help="Ignora arquivos já existentes e baixa/processa tudo de novo")
    args = parser.parse_args()

    nome_municipio, uf_sigla = obter_municipio_uf(args.codigo_ibge)
    logger.info("Município: %s (%s) — código IBGE %s", nome_municipio, uf_sigla, args.codigo_ibge)

    caminho_zip = baixar_zip_com_cache()
    membros = listar_membros_por_setor(caminho_zip)
    periodos = [re.search(r"(\d{4}_\d{2})", m).group(1).replace("_", "-") for m in membros]
    logger.info("%d períodos disponíveis (%s a %s).", len(membros), periodos[0], periodos[-1])

    caminho_saida = RAIZ / "data" / "raw" / f"cobertura-movel_anatel_{periodos[0][:4]}-{periodos[-1]}_setor-censitario.csv"
    if caminho_saida.exists() and not args.forcar:
        logger.info("%s já existe — nada a fazer (use --forcar para reprocessar).", caminho_saida)
        return

    partes = []
    for membro, periodo in zip(membros, periodos):
        df_periodo = extrair_e_filtrar_periodo(caminho_zip, membro, args.codigo_ibge)
        partes.append(df_periodo)
        logger.info("  %s: %d linhas (setor x operadora)", periodo, len(df_periodo))

    bruto = pd.concat(partes, ignore_index=True)
    bruto = bruto.rename(columns={
        "Período": "periodo", "Operadora": "operadora", "Código Setor Censitário": "codigo_setor",
        "Ano Censo": "ano_censo", "Cobertura_2G": "cobertura_2g_pct", "Cobertura_3G": "cobertura_3g_pct",
        "Cobertura_4G": "cobertura_4g_pct", "Cobertura_5G": "cobertura_5g_pct",
        "Cobertura_3G4G5G": "cobertura_3g4g5g_pct", "Cobertura_4G5G": "cobertura_4g5g_pct",
        "Cobertura_Todas": "cobertura_todas_tecnologias_pct",
    })
    for col in [c for c in bruto.columns if c.startswith("cobertura_")]:
        bruto[col] = pd.to_numeric(bruto[col].str.replace(",", ".", regex=False), errors="coerce")

    caminho_saida.parent.mkdir(parents=True, exist_ok=True)
    bruto.to_csv(caminho_saida, index=False, encoding="utf-8")

    n_setores = bruto["codigo_setor"].nunique()
    operadoras = sorted(bruto["operadora"].unique())
    anos_censo_por_periodo = bruto.groupby("periodo")["ano_censo"].unique().apply(lambda a: sorted(int(x) for x in a)).to_dict()
    metadados = {
        "fonte": "ANATEL — Painel de Dados de Infraestrutura, Cobertura da Telefonia Móvel",
        "url_zip": URL_ZIP,
        "codigo_ibge": args.codigo_ibge,
        "nome_municipio": nome_municipio,
        "uf": uf_sigla,
        "periodos_cobertos": periodos,
        "nivel_agregacao": (
            "setor censitário (código de 15 dígitos, Censo 2022) x operadora x trimestre — "
            "granularidade MAIOR que a assumida originalmente (município); não gera geometria "
            "nova aqui, junta por 'codigo_setor' com a malha de setores já existente no projeto "
            "(setores_censitarios_historico.py / vulnerabilidade_censo.py, ano 2022)"
        ),
        "n_setores_censitarios": int(n_setores),
        "operadoras": operadoras,
        "colunas_cobertura": "percentual (0-100) da população do setor coberta pela tecnologia indicada; 'cobertura_todas_tecnologias_pct' é a união de todas; operadora='Todas' é a união entre operadoras",
        "AVISO_CRITICO_ano_censo_por_periodo": anos_censo_por_periodo,
        "quebra_comparabilidade_malha_setores": (
            "achado real (não suposição): a ANATEL usa códigos de setor censitário do CENSO 2010 até "
            "o período 09-2024 e só passa a usar os códigos do CENSO 2022 a partir de 12-2024 (ver "
            "'AVISO_CRITICO_ano_censo_por_periodo' acima) — os dois esquemas de código NÃO são "
            "compatíveis entre si. A malha de setores já carregada neste projeto "
            "(setores_censitarios_historico.py / vulnerabilidade_censo.py) é a de 2022 — só os "
            "períodos 03-2025 em diante (ano_censo=2022) fazem JOIN direto e correto com ela; os "
            "períodos 11-2021 a 09-2024 (ano_censo=2010) precisariam da malha de setores 2010 (também "
            "já baixada em setores_censitarios_historico.py) para um join espacial correto — NÃO usar "
            "o código do período 2010 contra a malha 2022 (ou vice-versa) sem converter, mesmo aviso "
            "já registrado para as malhas de setores do IBGE neste projeto"
        ),
        "colunas_cobertura_ausentes_em_periodos_antigos": "Cobertura_5G, Cobertura_3G4G5G e Cobertura_4G5G não existiam no período 11-2021 (antes do 5G comercial no Brasil) — ficam NaN nesse período, não zero",
        "escopo_nao_incluido": (
            "base de estações/antenas com latitude/longitude individual (~662MB nacional, "
            "~23% das coordenadas mascaradas na versão pública) NÃO baixada — decisão do usuário, "
            "dado por setor já é oficial/pré-agregado e mais confiável para análise espacial"
        ),
        "data_acesso": date.today().isoformat(),
        "data_processamento": datetime.now(timezone.utc).isoformat(),
    }
    caminho_saida.with_suffix(".json").write_text(json.dumps(metadados, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

    logger.info("Salvo: %s (%d linhas, %d setores censitários, operadoras: %s)", caminho_saida, len(bruto), n_setores, operadoras)
    logger.info("Metadados salvos em %s", caminho_saida.with_suffix(".json"))


if __name__ == "__main__":
    main()
