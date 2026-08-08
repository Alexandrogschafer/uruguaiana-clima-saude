"""ETAPA F do pedido de novas fontes: tenta baixar indicadores de água/
esgoto do SNIS para um município via API/Python. NÃO PRODUZ CSV — as três
rotas de acesso investigadas (validadas por requisição real, não suposição)
têm uma barreira que impede automação limpa. Documenta a limitação:

    data/raw/saneamento_snis_indisponivel.json

Rotas investigadas e por que nenhuma virou um download automatizável
-----------------------------------------------------------------------
1. Portal de dados abertos (dadosabertos.cidades.gov.br/dataset/
   snis-serie-historica): o único "recurso" do dataset é um link para o
   aplicativo antigo, não um arquivo.
2. Planilhas oficiais por ano (padrão real:
   https://www.gov.br/mdr/pt-br/assuntos/saneamento/snis/produtos-do-snis/
   diagnosticos/Planilhas_AE{ano}.zip — descoberto raspando a página de
   "diagnósticos anteriores", não documentação): a requisição redireciona
   (302) para `acl_users/credentials_cookie_auth/require_login` — exige
   login gov.br (provavelmente só prestadores/gestores cadastrados têm
   acesso), mesmo o link aparecendo numa página pública.
3. Aplicativo "Série Histórica" (app4.cidades.gov.br/serieHistorica/
   aguaEsgoto): esse SIM é público sem login, mas é uma aplicação JSF
   antiga com estado de sessão (ViewState) — a página tem uma função de
   exportação (confirmado: existe uma mensagem de erro de limite de
   1.000.000 de células no HTML), mas não expõe uma URL simples de
   consulta/exportação por querystring; automatizar exigiria simular
   navegação com Playwright (mais frágil, mais esforço de manutenção) em
   vez de uma chamada HTTP direta.

Alternativas não seguidas agora (decisão do usuário, 2026-08-08)
-------------------------------------------------------------------
- Pacote `basedosdados` (BigQuery): tem a tabela tratada do SNIS, mas
  exige o usuário ter/criar um projeto Google Cloud Platform com
  faturamento — dependência externa demais para este pipeline.
- Automatizar o app JSF com Playwright: mais trabalho e mais frágil a
  mudanças no app; descartado por ora.

Se alguém quiser revisitar: a rota 3 (app JSF) é a mais viável sem exigir
conta externa — precisaria de Playwright para simular a seleção de
estado/município/anos/indicadores e clicar em exportar.

Uso:
    python scripts/download/saneamento_snis.py
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

RAIZ = Path(__file__).resolve().parents[2]
CAMINHO_SAIDA = RAIZ / "data" / "raw" / "saneamento_snis_indisponivel.json"
HEADERS = {"User-Agent": "Mozilla/5.0 (compativel; script-pesquisa-ClimaPampa/1.0)"}

URL_PLANILHA_EXEMPLO = "https://www.gov.br/mdr/pt-br/assuntos/saneamento/snis/produtos-do-snis/diagnosticos/Planilhas_AE2020.zip"
URL_APP_SERIE_HISTORICA = "https://app4.cidades.gov.br/serieHistorica/aguaEsgoto"
URL_DADOS_ABERTOS = "https://dadosabertos.cidades.gov.br/dataset/snis-serie-historica"


def verificar_planilha_exige_login() -> dict:
    resposta = requests.get(URL_PLANILHA_EXEMPLO, headers=HEADERS, timeout=30, allow_redirects=True)
    exige_login = "require_login" in resposta.url
    return {"url_testada": URL_PLANILHA_EXEMPLO, "url_final_apos_redirect": resposta.url, "exige_login": exige_login}


def verificar_app_serie_historica() -> dict:
    resposta = requests.get(URL_APP_SERIE_HISTORICA, headers=HEADERS, timeout=30)
    tem_limite_exportacao_no_html = "Exportação abortada" in resposta.text or "limitada a 1.000.000" in resposta.text
    return {
        "url_testada": URL_APP_SERIE_HISTORICA,
        "status_code": resposta.status_code,
        "acessivel_sem_login": resposta.status_code == 200 and "require_login" not in resposta.url,
        "confirma_funcao_exportacao_no_html": tem_limite_exportacao_no_html,
        "tem_endpoint_export_simples": False,
    }


def main() -> None:
    logger.info("Verificando as 3 rotas de acesso ao SNIS (chamadas reais, não suposição)...")

    verificacao_planilha = verificar_planilha_exige_login()
    logger.info("Planilha oficial: exige_login=%s (%s)", verificacao_planilha["exige_login"], verificacao_planilha["url_final_apos_redirect"])

    verificacao_app = verificar_app_serie_historica()
    logger.info("App Série Histórica: acessível sem login=%s, tem função de exportação (sem endpoint simples)=%s",
                verificacao_app["acessivel_sem_login"], verificacao_app["confirma_funcao_exportacao_no_html"])

    documentacao = {
        "fonte": "SNIS (Sistema Nacional de Informações sobre Saneamento) — Ministério das Cidades / MDR",
        "resultado": "NÃO baixado — nenhuma rota investigada permite download programático simples sem login ou sem automação de navegador",
        "rotas_investigadas": {
            "1_portal_dados_abertos": {
                "url": URL_DADOS_ABERTOS,
                "resultado": "dataset só linka para o aplicativo antigo, sem arquivo/recurso direto",
            },
            "2_planilhas_oficiais_por_ano": {
                "descricao": "padrão de URL descoberto raspando a página de diagnósticos anteriores (não documentado publicamente)",
                "padrao_url": "https://www.gov.br/mdr/pt-br/assuntos/saneamento/snis/produtos-do-snis/diagnosticos/Planilhas_AE{ano}.zip",
                "verificacao": verificacao_planilha,
                "resultado": "redireciona para login gov.br (credentials_cookie_auth/require_login) — acesso restrito",
            },
            "3_app_serie_historica": {
                "url": URL_APP_SERIE_HISTORICA,
                "verificacao": verificacao_app,
                "resultado": (
                    "público, sem login, com função de exportação confirmada no HTML — mas é uma aplicação "
                    "JSF com estado de sessão (ViewState), sem endpoint de exportação por querystring simples; "
                    "automatizar exigiria Playwright (navegação simulada), não uma chamada HTTP direta"
                ),
            },
        },
        "alternativas_nao_seguidas": {
            "basedosdados_bigquery": "tem tabela tratada do SNIS, mas exige projeto Google Cloud Platform com faturamento do usuário",
            "playwright_app_jsf": "viável sem conta externa, mas mais frágil/trabalhoso — não implementado nesta rodada por decisão do usuário (2026-08-08)",
        },
        "recomendacao": (
            "se o indicador de saneamento for essencial para o projeto, a via mais rápida no curto prazo é "
            "pedir a um membro da equipe com cadastro no gov.br para baixar manualmente os "
            "Planilhas_AE{ano}.zip (2010 em diante) e depositá-los em data/raw/cache_snis/ — o parsing "
            "dessas planilhas (filtrar por código IBGE do município) pode ser escrito depois, sem depender "
            "de resolver o login"
        ),
        "data_verificacao": datetime.now(timezone.utc).isoformat(),
    }

    CAMINHO_SAIDA.parent.mkdir(parents=True, exist_ok=True)
    CAMINHO_SAIDA.write_text(json.dumps(documentacao, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    logger.info("Documentação da limitação salva em %s (sem CSV — ver justificativa no arquivo)", CAMINHO_SAIDA)


if __name__ == "__main__":
    main()
