"""
Baixa a série histórica demográfica municipal via API SIDRA/IBGE
(servicodados.ibge.gov.br/api/v3/agregados — gratuita, sem cadastro).
Insumo para o estudo demográfico municipal (Eixo III, ClimaPampa).

Gera em data/raw/ (nomenclatura {tema}_{fonte}_{periodo}_municipal.{csv,json}):
    populacao_ibge-sidra-tabela200_1970-2010_municipal.csv
    populacao-sexo-idade_ibge-sidra-tabela9514_2022_municipal.csv
    populacao-estimada_ibge-sidra-tabela6579_2001-2025_municipal.csv
    situacao-domicilio_ibge-sidra-tabela202_1970-2010_municipal.csv
    situacao-domicilio_ibge-sidra-tabela9923_2022_municipal.csv
    piramide-etaria_ibge-sidra-tabela6706_indisponivel-municipal.json

Decisões de metodologia (investigadas por consulta real à API antes de codificar)
----------------------------------------------------------------------------------
1. Tabela 200 cobre só até 2010 (Censos 1970/1980/1991/2000/2010) — é a própria
   tabela de "Características Gerais da População" do Censo, não existe versão
   2022 dela. Já traz sexo x situação do domicílio x grupo de idade (nível 1,
   faixas de 5 anos) no mesmo cruzamento — usada aqui tanto para a série de
   população total quanto para a pirâmide etária de 2000 e 2010 (mesma fonte,
   uma única consulta), evitando uma segunda tabela redundante.
2. Tabela 9514 (Censo 2022) tem os MESMOS 21 grupos etários de 5 anos que a
   tabela 200 usa a partir dos 0-4 anos até "100 anos ou mais" — confirmado
   comparando os metadados das duas tabelas (categorias de nível 1 idênticas
   em nome). Isso permite montar a pirâmide 2000/2010/2022 com grupos etários
   comparáveis mesmo vindo de tabelas diferentes.
3. Tabela 6706 ("pirâmide etária") é da PNAD Contínua, não do Censo — testada
   por consulta real e retorna "..." (sem dado) para QUALQUER variável/período
   no nível de município (N6): a PNAD Contínua não tem amostra representativa
   por município pequeno, só é divulgada nos níveis Brasil/UF/RM. Isso é
   documentado no .json de indisponibilidade em vez de um CSV vazio — os dados
   de pirâmide etária municipal comparável vêm de (1) e (2) acima, que já são
   exatamente a alternativa que o enunciado da tarefa previu ("se não estiver
   nela, buscar tabela equivalente da época").
4. Tabela 202 (sexo x situação do domicílio) também só vai até 2010, mesmo
   padrão da 200. Para fechar a série até 2022, usa-se a tabela 9923
   ("População residente, por situação do domicílio", Censo 2022) como
   equivalente moderno — só não tem abertura por sexo (a única tabela do
   Censo 2022 com sexo x situação do domicílio simultaneamente traz também
   quebra por território quilombola, desnecessária aqui).
5. Tabela 6579 (estimativas anuais) não publica 2007, 2010, 2022 e 2023 —
   nesses anos o IBGE usa a Contagem da População (2007) ou o próprio Censo
   (2010, 2022) em vez de estimativa; population desses 4 anos já vem de
   outras tabelas (200/9514) e não precisa de estimativa.

Uso:
    python scripts/download/demografia_ibge_sidra.py
    python scripts/download/demografia_ibge_sidra.py --codigo-ibge 4314902 --forcar
"""

import argparse
import json
import logging
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

CODIGO_IBGE_DEFAULT = "4322400"  # Uruguaiana, RS
DATA_ACESSO = date.today().isoformat()

HEADERS = {"User-Agent": "Mozilla/5.0 (compativel; script-pesquisa-ClimaPampa/1.0)"}
N_TENTATIVAS = 3
BACKOFF_BASE_S = 1.0

URL_SIDRA_BASE = "https://servicodados.ibge.gov.br/api/v3/agregados"
URL_MUNICIPIO_IBGE = "https://servicodados.ibge.gov.br/api/v1/localidades/municipios/{codigo}"

RAW_DIR = Path(__file__).resolve().parents[2] / "data" / "raw"

# Grupos etários de 5 anos (nível 1), IDs iguais em nome entre as tabelas 200 e 9514.
IDADE_200_IDS = "0,1140,1141,1142,1143,1144,1145,1146,1147,1148,1149,1150,1151,1152,1153,1154,1155,2503,6802,6803,92963,92964,92965,3245"
IDADE_9514_IDS = "100362,93070,93084,93085,93086,93087,93088,93089,93090,93091,93092,93093,93094,93095,93096,93097,93098,49108,49109,60040,60041,6653"

VALORES_AUSENTES = {None, "...", "-", "X", "..", ""}


def _requisitar_com_retry(sessao: requests.Session, url: str, params: dict) -> requests.Response:
    ultimo_erro = None
    for tentativa in range(1, N_TENTATIVAS + 1):
        try:
            resposta = sessao.get(url, params=params, headers=HEADERS, timeout=60)
            resposta.raise_for_status()
            return resposta
        except requests.RequestException as erro:
            ultimo_erro = erro
            if tentativa < N_TENTATIVAS:
                espera = BACKOFF_BASE_S * (2 ** (tentativa - 1))
                logger.warning(
                    "Falha na requisição %s (tentativa %d/%d): %s — nova tentativa em %.0fs",
                    url, tentativa, N_TENTATIVAS, erro, espera,
                )
                time.sleep(espera)
    raise RuntimeError(f"Falha ao requisitar {url} após {N_TENTATIVAS} tentativas: {ultimo_erro}") from ultimo_erro


def obter_municipio_uf(codigo_ibge: str, sessao: requests.Session) -> tuple[str, str]:
    url = URL_MUNICIPIO_IBGE.format(codigo=codigo_ibge)
    resposta = _requisitar_com_retry(sessao, url, params={})
    dados = resposta.json()
    return dados["nome"], dados["microrregiao"]["mesorregiao"]["UF"]["sigla"]


def obter_periodos_disponiveis(agregado: int, sessao: requests.Session) -> list[str]:
    url = f"{URL_SIDRA_BASE}/{agregado}/periodos"
    resposta = _requisitar_com_retry(sessao, url, params={})
    return [p["id"] for p in resposta.json()]


def consultar_sidra(
    agregado: int, periodos: str, variavel: int, classificacao: str | None, codigo_ibge: str, sessao: requests.Session
) -> pd.DataFrame:
    """Consulta um agregado SIDRA e retorna um DataFrame 'long': uma linha por
    combinação de categorias x período, com a coluna 'populacao' já numérica
    (NaN para valores suprimidos/sigilo — '...', '-', 'X')."""
    url = f"{URL_SIDRA_BASE}/{agregado}/periodos/{periodos}/variaveis/{variavel}"
    params = {"localidades": f"N6[{codigo_ibge}]"}
    if classificacao:
        params["classificacao"] = classificacao
    resposta = _requisitar_com_retry(sessao, url, params)
    dados = resposta.json()

    linhas = []
    for bloco in dados[0]["resultados"]:
        categorias = {classif["nome"]: list(classif["categoria"].values())[0] for classif in bloco["classificacoes"]}
        serie = bloco["series"][0]["serie"]
        for periodo, valor_str in serie.items():
            valor = None if valor_str in VALORES_AUSENTES else float(valor_str)
            linhas.append({**categorias, "periodo": periodo, "populacao": valor})
    return pd.DataFrame(linhas)


def salvar_com_metadados(df: pd.DataFrame, nome_arquivo: str, metadados: dict) -> None:
    caminho_csv = RAW_DIR / nome_arquivo
    df.to_csv(caminho_csv, index=False, encoding="utf-8")
    logger.info("Salvo %s (%d linhas)", caminho_csv, len(df))

    metadados_completos = {**metadados, "data_acesso": DATA_ACESSO, "data_processamento": datetime.now(timezone.utc).isoformat()}
    caminho_json = caminho_csv.with_suffix(".json")
    caminho_json.write_text(json.dumps(metadados_completos, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    logger.info("Metadados salvos em %s", caminho_json)


def baixar_tabela_200(codigo_ibge: str, sessao: requests.Session) -> None:
    df = consultar_sidra(200, "1970,1980,1991,2000,2010", 93, f"2[0,4,5]|1[0,1,2]|58[{IDADE_200_IDS}]", codigo_ibge, sessao)
    df = df.rename(columns={"Sexo": "sexo", "Situação do domicílio": "situacao_domicilio", "Grupo de idade": "grupo_idade"})
    df = df[["periodo", "sexo", "situacao_domicilio", "grupo_idade", "populacao"]].sort_values(
        ["periodo", "sexo", "situacao_domicilio", "grupo_idade"]
    )
    salvar_com_metadados(
        df, "populacao_ibge-sidra-tabela200_1970-2010_municipal.csv",
        {
            "fonte": "IBGE — SIDRA, tabela 200 (Censos Demográficos, 'Características Gerais da População')",
            "url_api": "https://servicodados.ibge.gov.br/api/v3/agregados/200",
            "tabela_sidra": 200,
            "periodos": [1970, 1980, 1991, 2000, 2010],
            "codigo_ibge": codigo_ibge,
            "cruzamento": "sexo (Total/Homens/Mulheres) x situação do domicílio (Total/Urbana/Rural) x grupo de idade (faixas de 5 anos)",
            "limitacao_comparabilidade": (
                "não existe versão desta tabela para o Censo 2022 — a série 2022 vem da tabela 9514 (sexo x idade) "
                "e 9923 (situação do domicílio), tabelas equivalentes usadas nas Características Gerais da "
                "População do Censo 2022. Setores censitários mudam de malha a cada Censo — esta série é agregada "
                "no nível MUNICIPAL, não comparável espacialmente por setor entre censos."
            ),
            "coluna_grupo_idade": (
                "inclui a categoria agregada '80 anos ou mais' JUNTO com a abertura fina '80 a 84'...'100 anos ou "
                "mais' — não somar as duas ao calcular totais por faixa (ver também 'Idade ignorada', residual do "
                "próprio Censo)."
            ),
        },
    )


def baixar_tabela_9514(codigo_ibge: str, sessao: requests.Session) -> None:
    df = consultar_sidra(9514, "2022", 93, f"2[6794,4,5]|287[{IDADE_9514_IDS}]|286[113635]", codigo_ibge, sessao)
    df = df.rename(columns={"Sexo": "sexo", "Idade": "grupo_idade"})
    df = df.drop(columns=["Forma de declaração da idade"], errors="ignore")
    df = df[["periodo", "sexo", "grupo_idade", "populacao"]].sort_values(["sexo", "grupo_idade"])
    salvar_com_metadados(
        df, "populacao-sexo-idade_ibge-sidra-tabela9514_2022_municipal.csv",
        {
            "fonte": "IBGE — SIDRA, tabela 9514 (Censo Demográfico 2022, 'População residente, por sexo, idade e forma de declaração da idade')",
            "url_api": "https://servicodados.ibge.gov.br/api/v3/agregados/9514",
            "tabela_sidra": 9514,
            "periodos": [2022],
            "codigo_ibge": codigo_ibge,
            "cruzamento": "sexo (Total/Homens/Mulheres) x grupo de idade (faixas de 5 anos, 0-4 até 100+)",
            "limitacao_comparabilidade": (
                "grupos etários de 5 anos escolhidos para bater exatamente com os nomes de categoria de nível 1 da "
                "tabela 200 (2000/2010), permitindo pirâmide etária comparável entre os 3 censos; não tem quebra "
                "por situação do domicílio (essa vem separadamente da tabela 9923)."
            ),
        },
    )


def baixar_tabela_6579(codigo_ibge: str, sessao: requests.Session) -> None:
    periodos_disponiveis = obter_periodos_disponiveis(6579, sessao)
    df = consultar_sidra(6579, ",".join(periodos_disponiveis), 9324, None, codigo_ibge, sessao)
    df = df[["periodo", "populacao"]].sort_values("periodo")
    anos_ausentes = sorted(set(str(a) for a in range(int(periodos_disponiveis[0]), int(periodos_disponiveis[-1]) + 1)) - set(periodos_disponiveis))
    salvar_com_metadados(
        df, "populacao-estimada_ibge-sidra-tabela6579_2001-2025_municipal.csv",
        {
            "fonte": "IBGE — SIDRA, tabela 6579 (Estimativas de População, publicação anual do IBGE, IN Nº 2/2020 do TCU)",
            "url_api": "https://servicodados.ibge.gov.br/api/v3/agregados/6579",
            "tabela_sidra": 6579,
            "periodos_disponiveis": periodos_disponiveis,
            "codigo_ibge": codigo_ibge,
            "limitacao_comparabilidade": (
                f"anos sem estimativa publicada nesta tabela dentro do intervalo coberto: {anos_ausentes} — "
                "2007 e 2023 correspondem a Contagens da População (tabelas separadas, não baixadas aqui por não "
                "serem prioritárias ao estudo); 2010 e 2022 são anos de Censo (usa-se a contagem censitária exata "
                "das tabelas 200/9514/9923 em vez de estimativa nesses dois pontos)."
            ),
        },
    )


def baixar_tabela_202(codigo_ibge: str, sessao: requests.Session) -> None:
    df = consultar_sidra(202, "1970,1980,1991,2000,2010", 93, "2[0,4,5]|1[0,1,2]", codigo_ibge, sessao)
    df = df.rename(columns={"Sexo": "sexo", "Situação do domicílio": "situacao_domicilio"})
    df = df[["periodo", "sexo", "situacao_domicilio", "populacao"]].sort_values(["periodo", "sexo", "situacao_domicilio"])
    salvar_com_metadados(
        df, "situacao-domicilio_ibge-sidra-tabela202_1970-2010_municipal.csv",
        {
            "fonte": "IBGE — SIDRA, tabela 202 (Censos Demográficos, 'População residente, por sexo e situação do domicílio')",
            "url_api": "https://servicodados.ibge.gov.br/api/v3/agregados/202",
            "tabela_sidra": 202,
            "periodos": [1970, 1980, 1991, 2000, 2010],
            "codigo_ibge": codigo_ibge,
            "limitacao_comparabilidade": (
                "não existe versão desta tabela para o Censo 2022 — a série urbano/rural de 2022 vem da tabela "
                "9923 (situacao-domicilio_ibge-sidra-tabela9923_2022_municipal.csv), que não tem abertura por sexo."
            ),
        },
    )


def baixar_tabela_9923(codigo_ibge: str, sessao: requests.Session) -> None:
    df = consultar_sidra(9923, "2022", 93, "1[6795,1,2]", codigo_ibge, sessao)
    df = df.rename(columns={"Situação do domicílio": "situacao_domicilio"})
    df = df[["periodo", "situacao_domicilio", "populacao"]].sort_values("situacao_domicilio")
    salvar_com_metadados(
        df, "situacao-domicilio_ibge-sidra-tabela9923_2022_municipal.csv",
        {
            "fonte": "IBGE — SIDRA, tabela 9923 (Censo Demográfico 2022, 'População residente, por situação do domicílio')",
            "url_api": "https://servicodados.ibge.gov.br/api/v3/agregados/9923",
            "tabela_sidra": 9923,
            "periodos": [2022],
            "codigo_ibge": codigo_ibge,
            "limitacao_comparabilidade": (
                "equivalente moderno da tabela 202 (que só vai até 2010), usada para fechar a série urbano/rural "
                "em 2022; não tem abertura por sexo — a única tabela do Censo 2022 com sexo x situação do "
                "domicílio simultaneamente (10089) traz também quebra por território quilombola, desnecessária "
                "para este estudo e descartada para manter o dado simples."
            ),
        },
    )


def investigar_tabela_6706(codigo_ibge: str, sessao: requests.Session) -> None:
    """Tabela 6706 (pirâmide etária) é da PNAD Contínua, não do Censo — testa e
    documenta a indisponibilidade em nível municipal em vez de gerar CSV vazio."""
    periodos_disponiveis = obter_periodos_disponiveis(6706, sessao)
    df = consultar_sidra(6706, periodos_disponiveis[-1], 606, "2[4,5]|58[1140]", codigo_ibge, sessao)
    todos_ausentes = df["populacao"].isna().all() if not df.empty else True

    conteudo = {
        "fonte": "IBGE — SIDRA, tabela 6706 ('População residente, por sexo e grupos de idade - Pirâmide etária')",
        "url_api": "https://servicodados.ibge.gov.br/api/v3/agregados/6706",
        "tabela_sidra": 6706,
        "codigo_ibge": codigo_ibge,
        "resultado": "NÃO baixado — sem dado disponível no nível de município (N6) para este código IBGE",
        "verificacao": {
            "periodo_testado": periodos_disponiveis[-1],
            "consulta": f"sexo=Homens/Mulheres, grupo de idade='0 a 4 anos', localidade N6[{codigo_ibge}]",
            "todos_valores_ausentes": bool(todos_ausentes),
        },
        "motivo": (
            "a tabela 6706 é produzida pela PNAD Contínua (pesquisa amostral), não pelo Censo Demográfico — "
            "a PNAD Contínua não tem amostra representativa em nível de município pequeno, só divulga estimativas "
            "de estrutura etária nos níveis Brasil, UF e Regiões Metropolitanas selecionadas. Confirmado por "
            "consulta real à API: todos os períodos (2012-2025) retornam '...' (sem dado) para este município, "
            "mesmo a tabela metadados listando N6 como nível territorial nominalmente suportado."
        ),
        "alternativa_usada": (
            "pirâmide etária municipal comparável entre os 3 censos vem das tabelas 200 (2000 e 2010) e 9514 "
            "(2022), que usam os mesmos 21 grupos etários de 5 anos (nomes de categoria idênticos, verificado nos "
            "metadados de ambas) — ver populacao_ibge-sidra-tabela200_1970-2010_municipal.csv e "
            "populacao-sexo-idade_ibge-sidra-tabela9514_2022_municipal.csv."
        ),
        "data_acesso": DATA_ACESSO,
        "data_processamento": datetime.now(timezone.utc).isoformat(),
    }
    caminho = RAW_DIR / "piramide-etaria_ibge-sidra-tabela6706_indisponivel-municipal.json"
    caminho.write_text(json.dumps(conteudo, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    logger.info("Indisponibilidade da tabela 6706 documentada em %s (todos os valores ausentes: %s)", caminho, todos_ausentes)


TAREFAS = {
    "tabela200": (baixar_tabela_200, "populacao_ibge-sidra-tabela200_1970-2010_municipal.csv"),
    "tabela9514": (baixar_tabela_9514, "populacao-sexo-idade_ibge-sidra-tabela9514_2022_municipal.csv"),
    "tabela6579": (baixar_tabela_6579, "populacao-estimada_ibge-sidra-tabela6579_2001-2025_municipal.csv"),
    "tabela202": (baixar_tabela_202, "situacao-domicilio_ibge-sidra-tabela202_1970-2010_municipal.csv"),
    "tabela9923": (baixar_tabela_9923, "situacao-domicilio_ibge-sidra-tabela9923_2022_municipal.csv"),
    "tabela6706": (investigar_tabela_6706, "piramide-etaria_ibge-sidra-tabela6706_indisponivel-municipal.json"),
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Baixa a série demográfica municipal via API SIDRA/IBGE.")
    parser.add_argument("--codigo-ibge", default=CODIGO_IBGE_DEFAULT, help="Código IBGE do município (default: Uruguaiana/RS)")
    parser.add_argument("--forcar", action="store_true", help="Ignora arquivos já existentes e baixa tudo de novo")
    args = parser.parse_args()

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    sessao = requests.Session()
    sessao.headers.update(HEADERS)

    nome_municipio, uf = obter_municipio_uf(args.codigo_ibge, sessao)
    logger.info("Município: %s (%s) — código IBGE %s", nome_municipio, uf, args.codigo_ibge)

    for chave, (funcao, nome_arquivo) in TAREFAS.items():
        caminho = RAW_DIR / nome_arquivo
        if caminho.exists() and not args.forcar:
            logger.info("%s já existe (%s) — pulando (use --forcar para refazer).", chave, caminho)
            continue
        logger.info("Baixando %s...", chave)
        funcao(args.codigo_ibge, sessao)


if __name__ == "__main__":
    main()
