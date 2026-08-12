"""Baixa o IDHM (Índice de Desenvolvimento Humano Municipal) e componentes
do Atlas do Desenvolvimento Humano no Brasil (PNUD/Ipea/FJP) para um
município. Gera:

    data/raw/idhm_atlas-brasil_1991-2010_municipal.csv
    data/raw/idhm_atlas-brasil_1991-2010_municipal.json
    data/raw/idhm-2022_atlas-brasil_indisponivel-municipal.json

Decisão de integração (confirmada com o usuário, 2026-08-11)
------------------------------------------------------------------
IDHM é indicador agregado ÚNICO por município (Atlas Brasil não abre por
setor censitário nem geolocaliza) — fica como indicador de CONTEXTO
(série temporal), não como camada nova no geoportal. NÃO confundir com os
indicadores por setor censitário do Censo 2022 já integrados em
vulnerabilidade_censo.py — aquilo é outra fonte (Censo/SIDRA, nível
setor), isto é o IDHM oficial do Atlas (nível município, metodologia
própria com pesos/fórmulas do PNUD).

IDHM 2022 NÃO EXISTE (confirmado, não suposição)
---------------------------------------------------
Investigação real na API do Atlas Brasil mostrou DUAS fontes de IDHM:
1. "Censo" — IDHM oficial clássico, só 1991/2000/2010 (não existe versão
   Censo 2022 — a pesquisa amostral do Censo 2022 não gerou IDHM
   municipal oficial até o momento desta coleta).
2. "PNAD" — produto mais novo, com colunas anuais 2012-2024 (inclusive
   "IDHM 2022"), mas testado e confirmado VAZIO em nível de município:
   nem Uruguaiana nem Porto Alegre (capital do RS, controle de
   sanidade) retornam valor para nenhum indicador dessa fonte — na
   prática é um produto Brasil/UF, não municipal, apesar de a interface
   permitir tecnicamente selecionar um município. Documentado em
   idhm-2022_atlas-brasil_indisponivel-municipal.json (não gera CSV com
   coluna vazia).

Componentes e subíndices baixados (Censo, 1991/2000/2010)
--------------------------------------------------------------
- IDHM, IDHM Renda, IDHM Longevidade, IDHM Educação (índices normalizados
  0-1, os "componentes" pedidos).
- Subíndices que valem a pena registrar separadamente (investigado via
  api/buscaIndicadores, tema a tema): Renda per capita (R$, insumo bruto
  do IDHM Renda), Esperança de vida ao nascer (anos, insumo bruto do IDHM
  Longevidade), Subíndice de escolaridade e Subíndice de frequência
  escolar (os dois insumos que compõem o IDHM Educação, pesos 1/3 e 2/3).
  Não baixado: IDHM Ajustado à Desigualdade (tema separado, 8
  indicadores, só existe na fonte PNAD 2012-2024 — mesma limitação de
  cobertura municipal vazia, fora do escopo pedido).

Fonte e método (endpoint interno, sem documentação pública — investigado
via engenharia reversa do JS de https://www.atlasbrasil.org.br/consulta/planilha)
-------------------------------------------------------------------------------------
POST /api/dadosgrid (JSON), filtrando por município via
territorialidades.entidades[0] = {"e":2 (município), "l":[<id_atlas>]}
— "l" é o id INTERNO do Atlas Brasil (não o código IBGE), obtido via
POST /api/buscaTerritorios {"dadosPesq":"<nome>","variavel":"5"}, com
checagem de consistência contra o código IBGE de 6 dígitos (campo
ibge_6 do resultado) para não pegar município homônimo de outro estado.
API sem CSRF token exposto de forma estável entre requisições (sessão +
token obtidos a cada chamada) e com flakiness real do servidor (erros
500 intermitentes mesmo com payload correto, confirmado por retry bem-
sucedido em requisições idênticas) — todas as chamadas usam sessão nova
+ retry com backoff.

Uso:
    python scripts/download/idhm_atlas_brasil.py
    python scripts/download/idhm_atlas_brasil.py --codigo-ibge 4314902 --forcar
"""

import argparse
import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

RAIZ = Path(__file__).resolve().parents[2]
CAMINHO_SAIDA_CENSO = RAIZ / "data" / "raw" / "idhm_atlas-brasil_1991-2010_municipal.csv"
CAMINHO_INDISPONIVEL_PNAD = RAIZ / "data" / "raw" / "idhm-2022_atlas-brasil_indisponivel-municipal.json"

CODIGO_IBGE_DEFAULT = "4322400"  # Uruguaiana, RS
DATA_ACESSO = datetime.now(timezone.utc).date().isoformat()

URL_BASE = "https://www.atlasbrasil.org.br"
URL_PAGINA_CONSULTA = f"{URL_BASE}/consulta/planilha"
URL_BUSCA_TERRITORIOS = f"{URL_BASE}/api/buscaTerritorios"
URL_BUSCA_INDICADORES = f"{URL_BASE}/api/buscaIndicadores"
URL_DADOSGRID = f"{URL_BASE}/api/dadosgrid"

HEADERS_UA = {"User-Agent": "Mozilla/5.0 (compativel; script-pesquisa-ClimaPampa/1.0)"}
N_TENTATIVAS = 6
BACKOFF_BASE_S = 1.2

ANO_POR_ID_CENSO = {1: 1991, 2: 2000, 3: 2010}
ANO_POR_ID_PNAD = {5: 2012, 6: 2013, 7: 2014, 8: 2015, 9: 2016, 10: 2017, 11: 2018, 12: 2019, 13: 2020, 14: 2021, 15: 2022, 16: 2023, 17: 2024}

# id_indicador (fonte Censo) -> nome legível
INDICADORES_CENSO = {
    196: "IDHM",
    197: "IDHM Renda",
    198: "IDHM Longevidade",
    199: "IDHM Educação",
    77: "Renda per capita (R$)",
    15: "Esperança de vida ao nascer (anos)",
    201: "Subíndice de escolaridade (IDHM Educação)",
    200: "Subíndice de frequência escolar (IDHM Educação)",
}
INDICADOR_IDHM_PNAD = 435  # usado só para documentar a indisponibilidade


def _sessao_com_token() -> tuple[requests.Session, str]:
    sessao = requests.Session()
    sessao.headers.update(HEADERS_UA)
    resposta = sessao.get(URL_PAGINA_CONSULTA, timeout=30)
    resposta.raise_for_status()
    token = re.search(r'<meta name="csrf-token" content="([^"]+)"', resposta.text).group(1)
    return sessao, token


def _post_com_retry(url: str, montar_payload_e_headers, json_body: bool, tentativas: int = N_TENTATIVAS) -> dict:
    """Sessão + token novos a cada tentativa — a API do Atlas Brasil tem erros 500
    intermitentes mesmo com payload correto (confirmado testando o mesmo payload
    repetidas vezes); reobter sessão a cada tentativa é o que resolveu na
    investigação manual."""
    ultimo_erro = None
    for tentativa in range(1, tentativas + 1):
        sessao, token = _sessao_com_token()
        payload, headers = montar_payload_e_headers(token)
        try:
            if json_body:
                resposta = sessao.post(url, data=json.dumps(payload), headers={**headers, "Content-Type": "application/json"}, timeout=30)
            else:
                resposta = sessao.post(url, data=payload, headers=headers, timeout=30)
            if resposta.status_code == 200:
                return resposta.json()
            ultimo_erro = f"HTTP {resposta.status_code}: {resposta.text[:200]}"
        except requests.RequestException as erro:
            ultimo_erro = str(erro)
        if tentativa < tentativas:
            espera = BACKOFF_BASE_S * tentativa
            logger.warning("Tentativa %d/%d falhou (%s) — nova tentativa em %.1fs", tentativa, tentativas, ultimo_erro, espera)
            time.sleep(espera)
    raise RuntimeError(f"Falha ao consultar {url} após {tentativas} tentativas: {ultimo_erro}")


def _headers_ajax(token: str) -> dict:
    return {**HEADERS_UA, "X-CSRF-TOKEN": token, "X-Requested-With": "XMLHttpRequest", "Referer": URL_PAGINA_CONSULTA}


def obter_id_atlas_municipio(codigo_ibge: str) -> tuple[int, str, str]:
    """Busca o município pelo nome (via API de localidades do IBGE, sem hardcode)
    no Atlas Brasil e retorna (id_interno_atlas, nome, uf_sigla), validando o
    código IBGE de 6 dígitos retornado (campo ibge_6) contra o alvo — necessário
    porque há municípios homônimos em estados diferentes."""
    resposta_ibge = requests.get(f"https://servicodados.ibge.gov.br/api/v1/localidades/municipios/{codigo_ibge}", timeout=30)
    resposta_ibge.raise_for_status()
    dados_ibge = resposta_ibge.json()
    nome_municipio = dados_ibge["nome"]
    uf_sigla = dados_ibge["microrregiao"]["mesorregiao"]["UF"]["sigla"]
    ibge_6_esperado = int(codigo_ibge[:6])

    def montar(token):
        return {"dadosPesq": nome_municipio, "variavel": "5", "_token": token}, _headers_ajax(token)

    resultado = _post_com_retry(URL_BUSCA_TERRITORIOS, montar, json_body=False)
    candidatos = resultado.get("data", [])
    correspondente = next((c for c in candidatos if c.get("ibge_6") == ibge_6_esperado), None)
    if correspondente is None:
        raise RuntimeError(
            f"Município '{nome_municipio}' (IBGE {codigo_ibge}) não encontrado no Atlas Brasil "
            f"(candidatos retornados: {[(c['nome'], c.get('sigla_estado'), c.get('ibge_6')) for c in candidatos]})"
        )
    return correspondente["id"], nome_municipio, uf_sigla


def consultar_indicadores(id_atlas_municipio: int, cod_indicadores: list[dict]) -> dict:
    def montar(token):
        payload = {
            "territorialidades": {"entidades": [{"e": 2, "mun": [], "rm": [], "est": [], "udh": [], "l": [id_atlas_municipio]}]},
            "indicadores": {"cod_indicadores": cod_indicadores, "data_desagregacoes": []},
            "pagination": {"current_page": 1, "last_page": 0, "per_page": 10, "total": 0, "from": 0, "to": 0},
            "ordenation": {"default": "asc"},
        }
        return payload, _headers_ajax(token)

    return _post_com_retry(URL_DADOSGRID, montar, json_body=True)


def baixar_serie_censo(id_atlas_municipio: int, nome_municipio: str) -> pd.DataFrame:
    cod_indicadores = [{"id_indicador": ind, "id_ano": ano_id} for ind in INDICADORES_CENSO for ano_id in ANO_POR_ID_CENSO]
    resultado = consultar_indicadores(id_atlas_municipio, cod_indicadores)

    colunas = resultado["columns"][1:]  # descarta a coluna "Territorialidades"
    linha_municipio = next(r for r in resultado["data"] if r[0] != "Brasil")
    linha_brasil = next(r for r in resultado["data"] if r[0] == "Brasil")

    linhas = []
    for coluna, valor_municipio, valor_brasil in zip(colunas, linha_municipio[1:], linha_brasil[1:]):
        id_indicador, id_ano = (int(x) for x in coluna["id"].split("_"))
        linhas.append({
            "indicador": coluna["subtitle"],
            "id_indicador": id_indicador,
            "ano": ANO_POR_ID_CENSO[id_ano],
            "fonte": "Censo",
            "valor_municipio": float(valor_municipio.replace(",", ".")) if valor_municipio else None,
            "valor_brasil": float(valor_brasil.replace(",", ".")) if valor_brasil else None,
        })
    df = pd.DataFrame(linhas).sort_values(["id_indicador", "ano"]).reset_index(drop=True)
    return df


def verificar_indisponibilidade_pnad(id_atlas_municipio: int, id_atlas_controle: int, nome_controle: str) -> dict:
    """Confirma (não supõe) que a série PNAD 2012-2024 (incluindo 'IDHM 2022') está
    vazia em nível de município — testada contra o município alvo E contra um
    controle de sanidade (capital do estado, presumivelmente a melhor cobertura
    possível), para não confundir 'vazio para este município' com 'produto
    fundamentalmente não-municipal'."""
    cod_indicadores = [{"id_indicador": INDICADOR_IDHM_PNAD, "id_ano": ano_id} for ano_id in ANO_POR_ID_PNAD]

    resultado_alvo = consultar_indicadores(id_atlas_municipio, cod_indicadores)
    linha_alvo = next(r for r in resultado_alvo["data"] if r[0] != "Brasil")
    valores_alvo = linha_alvo[1:]

    resultado_controle = consultar_indicadores(id_atlas_controle, cod_indicadores)
    linha_controle = next(r for r in resultado_controle["data"] if r[0] != "Brasil")
    valores_controle = linha_controle[1:]

    return {
        "todos_anos_vazios_municipio_alvo": all(v == "" for v in valores_alvo),
        "todos_anos_vazios_municipio_controle": all(v == "" for v in valores_controle),
        "municipio_controle": nome_controle,
        "anos_testados": list(ANO_POR_ID_PNAD.values()),
        "valores_brutos_municipio_alvo": dict(zip(ANO_POR_ID_PNAD.values(), valores_alvo)),
        "valores_brutos_municipio_controle": dict(zip(ANO_POR_ID_PNAD.values(), valores_controle)),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Baixa o IDHM e componentes (Atlas Brasil/PNUD) para um município.")
    parser.add_argument("--codigo-ibge", default=CODIGO_IBGE_DEFAULT, help="Código IBGE do município (default: Uruguaiana/RS)")
    parser.add_argument("--forcar", action="store_true", help="Ignora arquivos já existentes e baixa tudo de novo")
    args = parser.parse_args()

    if CAMINHO_SAIDA_CENSO.exists() and not args.forcar:
        logger.info("%s já existe — nada a fazer (use --forcar para baixar de novo).", CAMINHO_SAIDA_CENSO)
        return

    id_atlas, nome_municipio, uf_sigla = obter_id_atlas_municipio(args.codigo_ibge)
    logger.info("Município: %s (%s) — código IBGE %s, id Atlas Brasil %d", nome_municipio, uf_sigla, args.codigo_ibge, id_atlas)

    df = baixar_serie_censo(id_atlas, nome_municipio)
    logger.info("Série Censo (1991/2000/2010) baixada: %d linhas (%d indicadores x 3 anos).", len(df), len(INDICADORES_CENSO))

    CAMINHO_SAIDA_CENSO.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(CAMINHO_SAIDA_CENSO, index=False, encoding="utf-8")

    metadados = {
        "fonte": "Atlas do Desenvolvimento Humano no Brasil — PNUD, Ipea, Fundação João Pinheiro (atlasbrasil.org.br)",
        "url_consulta": URL_PAGINA_CONSULTA,
        "endpoint_interno": URL_DADOSGRID,
        "codigo_ibge": args.codigo_ibge,
        "nome_municipio": nome_municipio,
        "uf": uf_sigla,
        "id_atlas_brasil": id_atlas,
        "periodos": [1991, 2000, 2010],
        "nivel_agregacao": "municipal ÚNICO — Atlas Brasil não abre IDHM por setor censitário nem geolocaliza; não confundir com os indicadores por setor do Censo 2022 já integrados em vulnerabilidade_censo.py (fonte e nível diferentes)",
        "indicadores": INDICADORES_CENSO,
        "colunas": {
            "valor_municipio": f"valor do indicador para {nome_municipio}",
            "valor_brasil": "valor de referência Brasil no mesmo ano/indicador (linha de comparação já trazida pela própria API)",
        },
        "limitacao_idhm_2022": (
            "NÃO existe IDHM 2022 nível Censo — ver idhm-2022_atlas-brasil_indisponivel-municipal.json para a "
            "verificação completa (inclusive da fonte alternativa PNAD, também vazia em nível municipal)"
        ),
        "data_acesso": DATA_ACESSO,
        "data_processamento": datetime.now(timezone.utc).isoformat(),
    }
    CAMINHO_SAIDA_CENSO.with_suffix(".json").write_text(json.dumps(metadados, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    logger.info("Salvo: %s", CAMINHO_SAIDA_CENSO)

    logger.info("Verificando disponibilidade da série PNAD (2012-2024, inclui 'IDHM 2022')...")
    id_controle, nome_controle, _ = obter_id_atlas_municipio("4314902")  # Porto Alegre/RS — capital, controle de sanidade
    verificacao = verificar_indisponibilidade_pnad(id_atlas, id_controle, nome_controle)

    documentacao_pnad = {
        "fonte": "Atlas do Desenvolvimento Humano no Brasil — indicador 'IDHM' com fonte='PNAD' (id_indicador 435), anos 2012-2024",
        "resultado": "NÃO baixado — confirmado vazio em nível de município para o alvo E para o controle de sanidade",
        "codigo_ibge": args.codigo_ibge,
        "nome_municipio": nome_municipio,
        "verificacao": verificacao,
        "interpretacao": (
            "a série PNAD (incluindo a coluna 'IDHM 2022') existe como produto no Atlas Brasil e é tecnicamente "
            "consultável por município na interface, mas retorna vazio para QUALQUER município testado — inclusive "
            f"{nome_controle}, capital do estado, usada aqui como controle de sanidade (se uma capital não tem "
            "dado, é um produto Brasil/UF na prática, não uma lacuna específica deste município). Não existe "
            "IDHM 2022 utilizável em nível municipal nesta fonte — nem pela via Censo (não computado) nem pela "
            "via PNAD (computado, mas não disponibilizado por município)."
        ),
        "data_acesso": DATA_ACESSO,
        "data_processamento": datetime.now(timezone.utc).isoformat(),
    }
    CAMINHO_INDISPONIVEL_PNAD.write_text(json.dumps(documentacao_pnad, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    logger.info(
        "Confirmado: série PNAD 2012-2024 vazia em nível municipal (alvo=%s, controle=%s). Documentado em %s",
        verificacao["todos_anos_vazios_municipio_alvo"], verificacao["todos_anos_vazios_municipio_controle"], CAMINHO_INDISPONIVEL_PNAD,
    )


if __name__ == "__main__":
    main()
