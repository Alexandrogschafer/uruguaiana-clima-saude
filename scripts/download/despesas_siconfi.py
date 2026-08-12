"""Baixa a despesa municipal executada por função (Saúde, Assistência
Social, Saneamento) via a API pública do SICONFI (Secretaria do Tesouro
Nacional). Gera:

    data/raw/despesas-por-funcao_siconfi_2015-{ultimo_ano}_municipal.csv
    data/raw/despesas-por-funcao_siconfi_2015-{ultimo_ano}_municipal.json

API confirmada (não exige download manual de RREO/RGF)
------------------------------------------------------------
apidatalake.tesouro.gov.br/ords/siconfi/tt/rreo — REST pública, sem
autenticação, filtrável direto por `id_ente=<código IBGE>` (confirmado
por chamada real). Indicador de CONTEXTO municipal único (mesma decisão
de PIB/PAM-PPM/IDHM/RAIS-CAGED) — o RREO não abre despesa por
bairro/setor, só por função orçamentária no nível do ente.

Anexo, coluna e período usados
--------------------------------
"RREO-Anexo 02" = Demonstrativo da Execução das Despesas por Função/
Subfunção. Coluna "DESPESAS LIQUIDADAS ATÉ O BIMESTRE (d)" = despesa
executada acumulada no ano até aquele bimestre (medida padrão de "quanto
foi gasto de fato", mais robusta que DOTAÇÃO INICIAL/ATUALIZADA, que são
só orçamento previsto/autorizado, não gasto real). Para anos fechados usa
o bimestre 6 (acumulado do ano inteiro); para o ano corrente usa o último
bimestre com dado publicado (descoberto dinamicamente, não hardcoded —
há defasagem de publicação de ~30-60 dias após o fim do bimestre,
confirmado testando: em 11/08/2026 o bimestre 4/2026, que fecha em
agosto, ainda não estava publicado, só o 3/2026, até junho).

Série: 2015 (primeiro ano com dado nesta API, 2014 testado e vazio) até
o ano corrente.

Achado real: linha duplicada "intra-orçamentária" em alguns anos
------------------------------------------------------------------------
Em 2018-2021, "Saúde" e "Assistência Social" apareceram DUAS vezes no
retorno bruto da API com valores diferentes — uma linha
`cod_conta=RREO2TotalDespesas` (despesa exceto intra-orçamentária, a
figura padrão) e outra `cod_conta=RREO2TotalDespesasIntra` (transferência
interna entre orçamento fiscal e da seguridade social, tecnicamente uma
categoria à parte, não despesa externa nova). Sem filtrar por
`cod_conta`, essas duas linhas se somariam e inflariam o valor. O script
usa só `RREO2TotalDespesas`, coerente com o que o próprio "TOTAL (III)"
da fonte usa (confirmado comparando os dois).

Achado real sobre "Saneamento" (não é bug, é dado real)
------------------------------------------------------------
A função orçamentária "Saneamento" (parte do pedido original) aparece
com despesa ZERO/AUSENTE em Uruguaiana nos anos verificados — bastante
comum em municípios gaúchos, onde o serviço de água/esgoto é prestado
por uma concessionária estadual (CORSAN) fora do orçamento municipal
direto, não por uma falha da consulta. Documentado no metadado; se a
função aparecer com valor em algum ano, o script já captura
normalmente (não é um filtro que exclui a categoria, só reflete o que a
fonte devolve).

Uso:
    python scripts/download/despesas_siconfi.py
    python scripts/download/despesas_siconfi.py --codigo-ibge 4314902 --forcar
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
ANO_INICIO = 2015  # 2014 testado por consulta real e confirmado sem dado nesta API

URL_RREO = "https://apidatalake.tesouro.gov.br/ords/siconfi/tt/rreo"
NO_ANEXO = "RREO-Anexo 02"
COLUNA_DESPESA_EXECUTADA = "DESPESAS LIQUIDADAS ATÉ O BIMESTRE (d)"
COD_CONTA_DESPESA_PRINCIPAL = "RREO2TotalDespesas"  # exclui RREO2TotalDespesasIntra (intra-orçamentária, ver comentário abaixo)
FUNCOES_INTERESSE = ["Saúde", "Assistência Social", "Saneamento", "TOTAL (III) = (I + II)"]

HEADERS = {"User-Agent": "Mozilla/5.0 (compativel; script-pesquisa-ClimaPampa/1.0)"}
N_TENTATIVAS = 3
BACKOFF_BASE_S = 2.0


def _requisitar_com_retry(params: dict) -> list[dict]:
    ultimo_erro = None
    for tentativa in range(1, N_TENTATIVAS + 1):
        try:
            resposta = requests.get(URL_RREO, params=params, headers=HEADERS, timeout=60)
            resposta.raise_for_status()
            return resposta.json()["items"]
        except requests.RequestException as erro:
            ultimo_erro = erro
            if tentativa < N_TENTATIVAS:
                espera = BACKOFF_BASE_S * tentativa
                logger.warning("Falha na requisição (tentativa %d/%d): %s — nova tentativa em %.0fs", tentativa, N_TENTATIVAS, erro, espera)
                time.sleep(espera)
    raise RuntimeError(f"Falha ao requisitar RREO após {N_TENTATIVAS} tentativas: {ultimo_erro}") from ultimo_erro


def consultar_periodo(codigo_ibge: str, ano: int, periodo: int) -> list[dict]:
    return _requisitar_com_retry({
        "an_exercicio": ano, "nr_periodo": periodo, "co_tipo_demonstrativo": "RREO",
        "no_anexo": NO_ANEXO, "id_ente": codigo_ibge,
    })


def descobrir_ultimo_periodo_disponivel(codigo_ibge: str, ano: int) -> int | None:
    """Bimestres vão de 1 a 6; tenta do mais recente pro mais antigo e para no
    primeiro que tiver dado — evita assumir que o ano está fechado (bimestre 6)
    quando na verdade só os primeiros bimestres já foram publicados."""
    for periodo in range(6, 0, -1):
        itens = consultar_periodo(codigo_ibge, ano, periodo)
        if itens:
            return periodo
    return None


def obter_municipio_uf(codigo_ibge: str) -> tuple[str, str]:
    resposta = requests.get(f"https://servicodados.ibge.gov.br/api/v1/localidades/municipios/{codigo_ibge}", timeout=30)
    resposta.raise_for_status()
    dados = resposta.json()
    return dados["nome"], dados["microrregiao"]["mesorregiao"]["UF"]["sigla"]


def main() -> None:
    parser = argparse.ArgumentParser(description="Baixa despesa municipal por função (SICONFI/RREO) para um município.")
    parser.add_argument("--codigo-ibge", default=CODIGO_IBGE_DEFAULT, help="Código IBGE do município (default: Uruguaiana/RS)")
    parser.add_argument("--forcar", action="store_true", help="Ignora arquivo já existente e baixa tudo de novo")
    args = parser.parse_args()

    ano_atual = date.today().year
    caminho_saida_glob = list((RAIZ / "data" / "raw").glob("despesas-por-funcao_siconfi_*_municipal.csv"))
    if caminho_saida_glob and not args.forcar:
        logger.info("%s já existe — nada a fazer (use --forcar para baixar de novo).", caminho_saida_glob[0])
        return

    nome_municipio, uf_sigla = obter_municipio_uf(args.codigo_ibge)
    logger.info("Município: %s (%s) — código IBGE %s", nome_municipio, uf_sigla, args.codigo_ibge)

    linhas = []
    periodos_por_ano = {}
    for ano in range(ANO_INICIO, ano_atual + 1):
        periodo = descobrir_ultimo_periodo_disponivel(args.codigo_ibge, ano)
        if periodo is None:
            logger.warning("%d: nenhum bimestre publicado ainda — pulando.", ano)
            continue
        periodos_por_ano[ano] = periodo
        itens = consultar_periodo(args.codigo_ibge, ano, periodo)
        populacao = itens[0]["populacao"] if itens else None
        for item in itens:
            # RREO2TotalDespesas = despesa exceto intra-orçamentária (figura padrão de
            # despesa por função). RREO2TotalDespesasIntra é uma linha SEPARADA (transferência
            # interna entre orçamento fiscal e da seguridade social) que reaparece com o MESMO
            # nome de função em alguns anos — sem este filtro por cod_conta ela soma em cima da
            # linha principal e duplica o valor (achado real, confirmado comparando as duas
            # linhas de "Saúde"/"Assistência Social" em 2018-2021 com o total esperado).
            if (
                item["conta"] in FUNCOES_INTERESSE
                and item["coluna"] == COLUNA_DESPESA_EXECUTADA
                and item["cod_conta"] == COD_CONTA_DESPESA_PRINCIPAL
            ):
                linhas.append({
                    "ano": ano, "bimestre_referencia": periodo, "funcao": item["conta"],
                    "valor_reais": item["valor"], "populacao": populacao,
                })
        logger.info("%d (até bimestre %d): %d linhas de função de interesse capturadas.", ano, periodo, sum(1 for l in linhas if l["ano"] == ano))

    tabela = pd.DataFrame(linhas).sort_values(["ano", "funcao"]).reset_index(drop=True)
    tabela["funcao"] = tabela["funcao"].replace({"TOTAL (III) = (I + II)": "Total (todas as funções)"})

    ano_min, ano_max = tabela["ano"].min(), tabela["ano"].max()
    caminho_saida = RAIZ / "data" / "raw" / f"despesas-por-funcao_siconfi_{ano_min}-{ano_max}_municipal.csv"
    caminho_saida.parent.mkdir(parents=True, exist_ok=True)
    tabela.to_csv(caminho_saida, index=False, encoding="utf-8")

    funcoes_sem_dado = [f for f in FUNCOES_INTERESSE if f not in tabela["funcao"].values and f != "TOTAL (III) = (I + II)"]
    metadados = {
        "fonte": "SICONFI (Sistema de Informações Contábeis e Fiscais do Setor Público Brasileiro) — Secretaria do Tesouro Nacional, API pública",
        "url_api": URL_RREO,
        "anexo": NO_ANEXO,
        "coluna_usada": COLUNA_DESPESA_EXECUTADA,
        "codigo_ibge": args.codigo_ibge,
        "nome_municipio": nome_municipio,
        "uf": uf_sigla,
        "periodo_coberto": f"{ano_min}-{ano_max}",
        "bimestre_de_referencia_por_ano": periodos_por_ano,
        "nivel_agregacao": "municipal ÚNICO — RREO não abre despesa por bairro/setor; indicador de contexto, sem camada espacial (mesma decisão de PIB/PAM-PPM/IDHM/RAIS-CAGED)",
        "metodo": (
            "consulta direta à API filtrando por id_ente=código IBGE (sem hardcode de nome de "
            "município); para cada ano, o último bimestre publicado é descoberto testando do "
            "bimestre 6 pro 1 (não assume ano fechado); 2014 testado e confirmado sem dado nesta "
            "API — série começa em 2015"
        ),
        "funcoes_sem_dado_no_periodo": funcoes_sem_dado,
        "nota_saneamento": (
            "achado real, não erro de consulta: a função 'Saneamento' aparece sem despesa "
            "registrada no orçamento municipal de Uruguaiana nos anos verificados — comum em "
            "municípios gaúchos onde o serviço de água/esgoto é prestado por concessionária "
            "estadual (CORSAN) fora do orçamento municipal direto, não incluído nesta série"
        ) if "Saneamento" in funcoes_sem_dado else None,
        "colunas": {
            "bimestre_referencia": "último bimestre publicado no ano (6 = ano fechado; ano corrente pode ter bimestre menor)",
            "populacao": "população usada pelo próprio SICONFI naquele exercício (vem da fonte, útil para per capita sem precisar cruzar com outra tabela)",
        },
        "data_acesso": date.today().isoformat(),
        "data_processamento": datetime.now(timezone.utc).isoformat(),
    }
    caminho_saida.with_suffix(".json").write_text(json.dumps(metadados, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

    logger.info("Salvo: %s (%d linhas, %d-%d)", caminho_saida, len(tabela), ano_min, ano_max)
    logger.info("Metadados salvos em %s", caminho_saida.with_suffix(".json"))


if __name__ == "__main__":
    main()
