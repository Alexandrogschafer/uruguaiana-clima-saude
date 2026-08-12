"""Baixa indicadores municipais de assistência social (Cadastro Único e
Programa Bolsa Família) via a ferramenta "Relatório de Informações" do
MDS (aplicacoes.mds.gov.br/sagi/RIv3), sem necessidade de cadastro/API
key. Gera:

    data/raw/cadunico_mds-ri_2012-{ano_atual}_municipal.csv
    data/raw/cadunico_mds-ri_2012-{ano_atual}_municipal.json
    data/raw/bolsa-familia_mds-ri_atual_municipal.csv
    data/raw/bolsa-familia_mds-ri_atual_municipal.json

Duas fontes investigadas, decisão com o usuário (2026-08-11)
------------------------------------------------------------------
1. API oficial do Portal da Transparência (Bolsa Família por município,
   JSON limpo e documentado em Swagger) — descartada por exigir cadastro
   de e-mail manual do usuário para obter uma chave de API
   (portaldatransparencia.gov.br/api-de-dados/cadastrar-email), ação que
   não pode ser automatizada.
2. MDS "Relatório de Informações" (usada aqui) — sem cadastro/chave, mas
   os dados vêm em fragmentos de HTML/prosa (não JSON estruturado),
   exigindo extração por regex das frases em português. Também cobre
   Bolsa Família (o que a API do Portal da Transparência cobriria), então
   a fonte 1 acabou não fazendo falta.

Endpoint interno (sem documentação pública, investigado por engenharia
reversa do JS de aplicacoes.cidadania.gov.br/ri/pbfcad/)
--------------------------------------------------------------------------
GET https://aplicacoes.mds.gov.br/sagi/RIv3/geral/conteudo_modulo.php
    ?id=<id_modulo>&ibge=<codigo_ibge_6_digitos>&area=&ano=<AAAA>&mes=<M>
    &ct_captcha=RIPBFPATS&ctidr=

- `ibge` precisa do código IBGE de 6 DÍGITOS (sem o dígito verificador) —
  com 7 dígitos o serviço aceita a requisição mas devolve zeros
  silenciosamente para o município (achado real, não documentado; só
  os totais nacionais vinham certos, o texto do município aparecia com
  "0 famílias" até trocar para 6 dígitos).
- `ct_captcha=RIPBFPATS` é um token fixo que o próprio site público usa
  em todas as requisições (não é bypass de captcha real — é o valor que
  a aplicação legítima envia; confirmado lendo o JS-fonte do site).
- id_modulo 2109 = bloco "Cadastro Único" (famílias cadastradas, %
  atualização); id_modulo 2111 = bloco "Benefícios" (resumo do Bolsa
  Família do mês).

Cadastro Único: série anual 2012-{ano_atual} (dezembro de cada ano)
------------------------------------------------------------------------
Testado por consulta real (não suposição): meses anteriores a 2012
devolvem "0 famílias cadastradas" para Uruguaiana (a própria série do
Cadastro Único digital não tem dado municipal consistente antes disso
nesta ferramenta) — 2011-12 e 2010-12 testados, ambos zero; 2012-12
já retorna valor real. Amostragem em dezembro de cada ano (estoque de
cadastros muda devagar mês a mês, mesmo raciocínio de granularidade
usado em séries de contexto similares do projeto) + o mês mais recente
disponível.

Bolsa Família: só o mês mais recente (sem série histórica)
------------------------------------------------------------------------
Testado por consulta real: o módulo 2111 devolve "0 famílias atendidas"
para meses de 2015 (quando o programa "Bolsa Família" clássico
certamente tinha beneficiários em Uruguaiana) — o texto do módulo cita a
Lei 14.601/2023, que criou o "Novo Bolsa Família"; a leitura mais
provável é que este módulo específico só tem dado consistente a partir
do programa atual (2023+), não uma série histórica desde 2004/2012. Por
segurança, e porque cada consulta desse módulo é lenta (20-40s), este
script baixa só o mês mais recente disponível, documentado como
"situação atual", sem tentar reconstruir uma série histórica não
confiável.

Uso:
    python scripts/download/assistencia_social_cadunico.py
    python scripts/download/assistencia_social_cadunico.py --codigo-ibge 4314902 --forcar
"""

import argparse
import json
import logging
import re
import time
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

RAIZ = Path(__file__).resolve().parents[2]
CODIGO_IBGE_DEFAULT = "4322400"  # Uruguaiana, RS
ANO_INICIO_CADUNICO = 2012

URL_MODULO = "https://aplicacoes.mds.gov.br/sagi/RIv3/geral/conteudo_modulo.php"
MODULO_CADUNICO = 2109
MODULO_BOLSA_FAMILIA = 2111
CT_CAPTCHA_APP = "RIPBFPATS"  # valor fixo enviado pelo próprio app público, ver docstring

HEADERS = {"User-Agent": "Mozilla/5.0 (compativel; script-pesquisa-ClimaPampa/1.0)"}
N_TENTATIVAS = 4
BACKOFF_BASE_S = 2.0

MESES_PT = {
    "janeiro": 1, "fevereiro": 2, "março": 3, "abril": 4, "maio": 5, "junho": 6,
    "julho": 7, "agosto": 8, "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12,
}


def _numero_ptbr(texto: str) -> float:
    """'21.162' -> 21162.0 ; '86,7' -> 86.7 ; 'R$ 5.419.371,00' -> 5419371.0"""
    limpo = texto.replace("R$", "").strip().replace(".", "").replace(",", ".")
    return float(limpo)


def _requisitar_modulo(codigo_ibge_6: str, id_modulo: int, ano: int | None, mes: int | None, timeout: int) -> str:
    params = {
        "id": id_modulo, "ibge": codigo_ibge_6, "area": "",
        "ano": ano or "", "mes": mes or "",
        "ct_captcha": CT_CAPTCHA_APP, "ctidr": "",
    }
    ultimo_erro = None
    for tentativa in range(1, N_TENTATIVAS + 1):
        try:
            resposta = requests.get(URL_MODULO, params=params, headers=HEADERS, timeout=timeout)
            resposta.raise_for_status()
            return resposta.text
        except requests.RequestException as erro:
            ultimo_erro = erro
            if tentativa < N_TENTATIVAS:
                espera = BACKOFF_BASE_S * tentativa
                logger.warning("Falha ao consultar módulo %d (ano=%s, mes=%s), tentativa %d/%d: %s — nova tentativa em %.0fs",
                                id_modulo, ano, mes, tentativa, N_TENTATIVAS, erro, espera)
                time.sleep(espera)
    raise RuntimeError(f"Falha ao consultar módulo {id_modulo} após {N_TENTATIVAS} tentativas: {ultimo_erro}")


def _texto_limpo(html: str) -> str:
    html_sem_estilo = re.sub(r"<style.*?</style>", "", html, flags=re.S)
    texto = re.sub(r"<[^>]+>", " ", html_sem_estilo)
    return re.sub(r"\s+", " ", texto).strip()


def extrair_cadunico(html: str) -> dict | None:
    texto = _texto_limpo(html)
    m_municipio = re.search(
        r"tem um total de\s*([\d.]+)\s*famílias cadastradas.*?"
        r"([\d.]+)\s*famílias com cadastro atualizado.*?"
        r"taxa de atualização de\s*([\d,]+)%",
        texto,
    )
    if not m_municipio:
        return None
    m_brasil = re.search(
        r"Em todo o Brasil são\s*([\d.]+)\s*famílias cadastradas.*?"
        r"([\d.]+)\s*atualizaram seus cadastros.*?"
        r"média nacional de atualização\s*em\s*([\d,]+)%",
        texto,
    )
    return {
        "familias_cadastradas": _numero_ptbr(m_municipio.group(1)),
        "familias_atualizadas_2anos": _numero_ptbr(m_municipio.group(2)),
        "taxa_atualizacao_pct": _numero_ptbr(m_municipio.group(3)),
        "brasil_familias_cadastradas": _numero_ptbr(m_brasil.group(1)) if m_brasil else None,
        "brasil_taxa_atualizacao_pct": _numero_ptbr(m_brasil.group(3)) if m_brasil else None,
    }


def extrair_bolsa_familia(html: str) -> dict | None:
    texto = _texto_limpo(html)
    m = re.search(
        r"No mês de ([a-zç]+ de \d{4}), o município de \S+ teve\s*([\d.]+)\s*famílias atendidas pelo Programa Bolsa Família,"
        r"\s*com\s*([\d.]+)\s*pessoas beneficiadas,\s*e totalizando um investimento de\s*R\$\s*([\d.,]+)"
        r"\s*e um benefício médio de\s*R\$\s*([\d.,]+)",
        texto,
    )
    if not m:
        return None
    nome_mes, ano_str = m.group(1).split(" de ")
    return {
        "mes_referencia": MESES_PT.get(nome_mes, None),
        "ano_referencia": int(ano_str),
        "familias_atendidas": _numero_ptbr(m.group(2)),
        "pessoas_beneficiadas": _numero_ptbr(m.group(3)),
        "investimento_total_reais": _numero_ptbr(m.group(4)),
        "beneficio_medio_reais": _numero_ptbr(m.group(5)),
    }


def obter_municipio_uf(codigo_ibge: str) -> tuple[str, str]:
    resposta = requests.get(f"https://servicodados.ibge.gov.br/api/v1/localidades/municipios/{codigo_ibge}", timeout=30)
    resposta.raise_for_status()
    dados = resposta.json()
    return dados["nome"], dados["microrregiao"]["mesorregiao"]["UF"]["sigla"]


def baixar_serie_cadunico(codigo_ibge_6: str, ano_fim: int, mes_fim: int) -> pd.DataFrame:
    linhas = []
    for ano in range(ANO_INICIO_CADUNICO, ano_fim):
        html = _requisitar_modulo(codigo_ibge_6, MODULO_CADUNICO, ano, 12, timeout=30)
        extraido = extrair_cadunico(html)
        if extraido is None:
            logger.warning("Dezembro/%d: não foi possível extrair dados (município provavelmente sem cadastro consolidado ainda nesse período).", ano)
            continue
        linhas.append({"ano": ano, "mes": 12, **extraido})
        logger.info("Dezembro/%d: %.0f famílias cadastradas, %.1f%% taxa de atualização.", ano, extraido["familias_cadastradas"], extraido["taxa_atualizacao_pct"])

    html_atual = _requisitar_modulo(codigo_ibge_6, MODULO_CADUNICO, ano_fim, mes_fim, timeout=30)
    extraido_atual = extrair_cadunico(html_atual)
    if extraido_atual:
        linhas.append({"ano": ano_fim, "mes": mes_fim, **extraido_atual})
        logger.info("%d/%d (mais recente): %.0f famílias cadastradas, %.1f%% taxa de atualização.", mes_fim, ano_fim, extraido_atual["familias_cadastradas"], extraido_atual["taxa_atualizacao_pct"])

    return pd.DataFrame(linhas).sort_values(["ano", "mes"]).reset_index(drop=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Baixa indicadores de Cadastro Único e Bolsa Família (MDS RI) para um município.")
    parser.add_argument("--codigo-ibge", default=CODIGO_IBGE_DEFAULT, help="Código IBGE do município (default: Uruguaiana/RS)")
    parser.add_argument("--forcar", action="store_true", help="Ignora arquivos já existentes e baixa tudo de novo")
    args = parser.parse_args()

    caminho_cadunico = RAIZ / "data" / "raw" / f"cadunico_mds-ri_{ANO_INICIO_CADUNICO}-{date.today().year}_municipal.csv"
    caminho_bolsa_familia = RAIZ / "data" / "raw" / "bolsa-familia_mds-ri_atual_municipal.csv"

    if caminho_cadunico.exists() and caminho_bolsa_familia.exists() and not args.forcar:
        logger.info("Arquivos já existem — nada a fazer (use --forcar para baixar de novo).")
        return

    nome_municipio, uf_sigla = obter_municipio_uf(args.codigo_ibge)
    codigo_ibge_6 = args.codigo_ibge[:6]
    hoje = date.today()
    # o mês corrente costuma não ter fechamento consolidado ainda (testado: agosto/2026
    # devolveu 0 famílias, julho/2026 já vinha com dado real) — usa o mês anterior como "mais recente"
    ano_fim, mes_fim = (hoje.year, hoje.month - 1) if hoje.month > 1 else (hoje.year - 1, 12)
    logger.info("Município: %s (%s) — código IBGE %s (6 dígitos: %s)", nome_municipio, uf_sigla, args.codigo_ibge, codigo_ibge_6)

    df_cadunico = baixar_serie_cadunico(codigo_ibge_6, ano_fim, mes_fim)
    caminho_cadunico.parent.mkdir(parents=True, exist_ok=True)
    df_cadunico.to_csv(caminho_cadunico, index=False, encoding="utf-8")
    metadados_cadunico = {
        "fonte": "MDS — Cadastro Único para Programas Sociais, via 'Relatório de Informações' (aplicacoes.mds.gov.br/sagi/RIv3)",
        "url_endpoint": URL_MODULO,
        "id_modulo": MODULO_CADUNICO,
        "codigo_ibge": args.codigo_ibge,
        "nome_municipio": nome_municipio,
        "uf": uf_sigla,
        "periodo_coberto": f"{ANO_INICIO_CADUNICO}-{hoje.year}",
        "amostragem": "dezembro de cada ano (estoque de cadastros muda devagar mês a mês) + mês mais recente disponível",
        "nivel_agregacao": "municipal — contagem de famílias cadastradas/atualizadas no Cadastro Único, sem abertura por bairro/setor/faixa de renda nesta consulta",
        "limitacao_periodo": f"anos anteriores a {ANO_INICIO_CADUNICO} testados (2010, 2011) e confirmados sem dado consolidado nesta ferramenta para o município (retornam 0)",
        "colunas": {
            "familias_cadastradas": "total de famílias com cadastro no Cadastro Único no município, na data de referência",
            "familias_atualizadas_2anos": "dessas, quantas atualizaram o cadastro nos últimos 2 anos",
            "taxa_atualizacao_pct": "familias_atualizadas_2anos / familias_cadastradas * 100",
            "brasil_familias_cadastradas / brasil_taxa_atualizacao_pct": "valores de referência Brasil no mesmo mês (linha de comparação já trazida pela própria fonte)",
        },
        "metodo": "extração por regex de fragmento HTML/prosa (endpoint interno sem API JSON documentada) — ver docstring do script para o achado do parâmetro ibge de 6 dígitos",
        "data_acesso": date.today().isoformat(),
        "data_processamento": datetime.now(timezone.utc).isoformat(),
    }
    caminho_cadunico.with_suffix(".json").write_text(json.dumps(metadados_cadunico, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    logger.info("Salvo: %s (%d linhas)", caminho_cadunico, len(df_cadunico))

    logger.info("Baixando situação atual do Bolsa Família (módulo lento, ~20-40s)...")
    html_bf = _requisitar_modulo(codigo_ibge_6, MODULO_BOLSA_FAMILIA, None, None, timeout=60)
    extraido_bf = extrair_bolsa_familia(html_bf)
    if extraido_bf is None:
        logger.warning("Não foi possível extrair dados do Bolsa Família — texto do módulo pode ter mudado de formato.")
    else:
        df_bf = pd.DataFrame([{"codigo_ibge": args.codigo_ibge, "nome_municipio": nome_municipio, **extraido_bf}])
        df_bf.to_csv(caminho_bolsa_familia, index=False, encoding="utf-8")
        metadados_bf = {
            "fonte": "MDS — Programa Bolsa Família, via 'Relatório de Informações' (aplicacoes.mds.gov.br/sagi/RIv3)",
            "url_endpoint": URL_MODULO,
            "id_modulo": MODULO_BOLSA_FAMILIA,
            "codigo_ibge": args.codigo_ibge,
            "nome_municipio": nome_municipio,
            "uf": uf_sigla,
            "cobertura": "SÓ o mês mais recente disponível — sem série histórica (ver docstring: módulo testado com 0 famílias atendidas em 2015, texto do módulo referencia a Lei 14.601/2023 que criou o 'Novo Bolsa Família'; não é confiável como série antes disso)",
            "nivel_agregacao": "municipal — famílias atendidas, pessoas beneficiadas, investimento total e benefício médio no mês",
            "metodo": "extração por regex de fragmento HTML/prosa",
            "data_acesso": date.today().isoformat(),
            "data_processamento": datetime.now(timezone.utc).isoformat(),
        }
        caminho_bolsa_familia.with_suffix(".json").write_text(json.dumps(metadados_bf, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        logger.info("Salvo: %s", caminho_bolsa_familia)


if __name__ == "__main__":
    main()
