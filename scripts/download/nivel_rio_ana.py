"""ETAPA D do pedido de novas fontes: verifica se existe estação
telemétrica da ANA no rio de referência de um município e documenta como
consultar o nível (cota) atual. NÃO implementa ingestão contínua/tempo
real — só prova a viabilidade e salva um exemplo de consulta:

    data/raw/nivel-rio_ana_exemplo-consulta.csv
    data/raw/nivel-rio_ana_exemplo-consulta.json

Resultado para Uruguaiana (validado por consulta real, 2026-08-07)
--------------------------------------------------------------------
SIM, existe: estação **77150000** ("URUGUAIANA"), no próprio município,
no Rio Uruguai, operada pelo SGB-CPRM sob responsabilidade da ANA, em
operação desde 1939, com telemetria ativa (`TipoEstacaoTelemetrica=1`) e
régua/escala fluviométrica (`TipoEstacaoEscala=1`). A consulta de teste
devolveu leituras a cada 15 minutos (nível em cm, vazão em m³/s, chuva em
mm) até o instante da coleta.

Dos 22 pontos de monitoramento cadastrados no município, só esse tem rio
(RioNome preenchido) + telemetria + escala fluviométrica ao mesmo tempo —
os outros "telemétricos" do município (ex. 2956007, 2957001, 2957003)
não têm RioNome, ou seja, são pluviômetros automáticos, não estações de
nível de rio.

API usada: Web Service legado da ANA (telemetriaws1.ana.gov.br,
SOAP/ASMX com binding HTTP GET simples, resposta em XML/DataSet .NET),
SEM autenticação — validado com uma chamada real, não documentação lida.
Operações relevantes:

- `HidroInventario` — busca estações por município/rio/estado/bacia
  (parâmetros de texto, não código IBGE — por isso este script resolve
  --codigo-ibge para o NOME do município via API do IBGE antes de
  consultar). Devolve metadados: código, nome, rio, coordenadas, se é
  telemétrica, se tem escala/registrador de nível/descarga líquida.
- `DadosHidrometeorologicos?codEstacao=...&dataInicio=DD/MM/AAAA&dataFim=DD/MM/AAAA`
  — série de leituras de Nivel (cm), Vazao (m³/s) e Chuva (mm) a cada 15
  min, dentro do intervalo de datas pedido (aqui usado só para os
  últimos dias, como prova de "nível atual").

Risco de robustez / trabalho futuro
-------------------------------------
A ANA está migrando os serviços para uma API nova
(hidrowebservice.ana.gov.br, ver
https://www.ana.gov.br/hidrowebservice/swagger-ui.html), que EXIGE
cadastro (usuário/senha) e token OAuth (`/EstacoesTelemetricas/OAUth/v1`,
token válido por 60 min) — testado sem credenciais e retorna 401
("Token de Autenticação da API Inexistente ou mal Formatado"). O serviço
legado usado aqui (telemetriaws1.ana.gov.br) ainda funciona sem
autenticação no momento da coleta, mas pode ser desativado no futuro; se
isso acontecer, será necessário cadastro em https://www.ana.gov.br/hidrowebservice
e implementar o fluxo OAuth para continuar consultando.

Uso:
    python scripts/download/nivel_rio_ana.py
    python scripts/download/nivel_rio_ana.py --codigo-ibge 4314902 --nome-rio "GUAIBA"
"""

import argparse
import json
import logging
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

RAIZ = Path(__file__).resolve().parents[2]
CAMINHO_SAIDA = RAIZ / "data" / "raw" / "nivel-rio_ana_exemplo-consulta.csv"

BASE_URL_ANA = "https://telemetriaws1.ana.gov.br/ServiceANA.asmx"
CODIGO_IBGE_DEFAULT = "4322400"
HEADERS = {"User-Agent": "Mozilla/5.0 (compativel; script-pesquisa-ClimaPampa/1.0)"}
URL_MUNICIPIO_IBGE = "https://servicodados.ibge.gov.br/api/v1/localidades/municipios/{codigo}"


def obter_nome_municipio(codigo_ibge: str) -> str:
    resposta = requests.get(URL_MUNICIPIO_IBGE.format(codigo=codigo_ibge), headers=HEADERS, timeout=30)
    resposta.raise_for_status()
    return resposta.json()["nome"]


def buscar_estacoes_por_municipio(nome_municipio: str) -> list[dict]:
    """Consulta HidroInventario por nome de município (a API da ANA não aceita código IBGE)."""
    params = {
        "codEstDE": "", "codEstATE": "", "tpEst": "", "nmEst": "", "nmRio": "",
        "codSubBacia": "", "codBacia": "", "nmMunicipio": nome_municipio, "nmEstado": "",
        "sgResp": "", "sgOper": "", "telemetrica": "",
    }
    resposta = requests.get(f"{BASE_URL_ANA}/HidroInventario", headers=HEADERS, params=params, timeout=30)
    resposta.raise_for_status()
    root = ET.fromstring(resposta.text)
    return [
        {
            "codigo": t.findtext("Codigo"),
            "nome": t.findtext("Nome"),
            "rio": t.findtext("RioNome") or "",
            "municipio": t.findtext("nmMunicipio"),
            "latitude": t.findtext("Latitude"),
            "longitude": t.findtext("Longitude"),
            "telemetrica": t.findtext("TipoEstacaoTelemetrica") == "1",
            "tem_escala_fluviometrica": t.findtext("TipoEstacaoEscala") == "1",
        }
        for t in root.iter("Table")
    ]


def selecionar_estacao_nivel_rio(estacoes: list[dict], filtro_nome_rio: str | None) -> dict | None:
    """Filtra para estações fluviométricas telemétricas de verdade (com rio associado).

    Estações "telemétricas" sem RioNome preenchido são pluviômetros
    automáticos, não medem nível de rio — descartadas aqui.
    """
    candidatas = [e for e in estacoes if e["telemetrica"] and e["tem_escala_fluviometrica"] and e["rio"]]
    if filtro_nome_rio:
        candidatas = [e for e in candidatas if filtro_nome_rio.upper() in e["rio"].upper()]
    return candidatas[0] if candidatas else None


def consultar_nivel_recente(codigo_estacao: str, dias: int = 2) -> pd.DataFrame:
    fim = datetime.now(timezone.utc)
    inicio = fim - timedelta(days=dias)
    params = {
        "codEstacao": codigo_estacao,
        "dataInicio": inicio.strftime("%d/%m/%Y"),
        "dataFim": fim.strftime("%d/%m/%Y"),
    }
    resposta = requests.get(f"{BASE_URL_ANA}/DadosHidrometeorologicos", headers=HEADERS, params=params, timeout=30)
    resposta.raise_for_status()
    root = ET.fromstring(resposta.text)
    linhas = [
        {
            "codigo_estacao": t.findtext("CodEstacao"),
            "data_hora_utc": (t.findtext("DataHora") or "").strip(),
            "nivel_cm": t.findtext("Nivel"),
            "vazao_m3s": t.findtext("Vazao"),
            "chuva_mm": t.findtext("Chuva"),
        }
        for t in root.iter("DadosHidrometereologicos")
    ]
    return pd.DataFrame(linhas)


def main() -> None:
    parser = argparse.ArgumentParser(description="Verifica viabilidade e exemplifica consulta de nível de rio via ANA (telemetria).")
    parser.add_argument("--codigo-ibge", default=CODIGO_IBGE_DEFAULT)
    parser.add_argument("--nome-rio", default=None, help="Filtro opcional por nome do rio (ex. URUGUAI) quando o município tem mais de uma estação")
    args = parser.parse_args()

    nome_municipio = obter_nome_municipio(args.codigo_ibge)
    logger.info("Buscando estações da ANA em %s (código IBGE %s)...", nome_municipio, args.codigo_ibge)

    estacoes = buscar_estacoes_por_municipio(nome_municipio)
    logger.info("%d ponto(s) de monitoramento cadastrados no município (todos os tipos).", len(estacoes))

    estacao = selecionar_estacao_nivel_rio(estacoes, args.nome_rio)
    if estacao is None:
        logger.warning(
            "Nenhuma estação telemétrica de NÍVEL DE RIO encontrada para %s%s. "
            "Estação mais próxima: verificar manualmente em https://www.snirh.gov.br/hidroweb/apresentacao "
            "(este script busca só dentro do próprio município, por nome).",
            nome_municipio, f" (filtro rio={args.nome_rio})" if args.nome_rio else "",
        )
        return

    logger.info(
        "Estação encontrada: %s (%s) — rio %s, lat/lon %s/%s",
        estacao["codigo"], estacao["nome"], estacao["rio"], estacao["latitude"], estacao["longitude"],
    )

    serie = consultar_nivel_recente(estacao["codigo"])
    if serie.empty:
        logger.warning("Estação existe mas não devolveu leituras recentes (pode estar temporariamente fora do ar).")
        return

    CAMINHO_SAIDA.parent.mkdir(parents=True, exist_ok=True)
    serie.to_csv(CAMINHO_SAIDA, index=False, encoding="utf-8")
    logger.info("Exemplo de %d leituras salvo em %s (mais recente: %s, nível %s cm)",
                len(serie), CAMINHO_SAIDA, serie.iloc[0]["data_hora_utc"], serie.iloc[0]["nivel_cm"])

    metadados = {
        "fonte": "ANA — estação telemétrica (operada por SGB-CPRM), Web Service legado telemetriaws1.ana.gov.br/ServiceANA.asmx",
        "viabilidade": "confirmada por consulta real — ver docstring deste script para detalhes e para o risco de migração para a API nova (hidrowebservice.ana.gov.br, com OAuth)",
        "codigo_ibge": args.codigo_ibge,
        "nome_municipio": nome_municipio,
        "estacao_selecionada": estacao,
        "n_estacoes_no_municipio": len(estacoes),
        "operacao_inventario": f"{BASE_URL_ANA}/HidroInventario?nmMunicipio={nome_municipio}",
        "operacao_dados": f"{BASE_URL_ANA}/DadosHidrometeorologicos?codEstacao={estacao['codigo']}&dataInicio=DD/MM/AAAA&dataFim=DD/MM/AAAA",
        "granularidade": "~15 minutos (telemetria)",
        "campos_disponiveis": {"nivel_cm": "cota do rio em cm", "vazao_m3s": "vazão estimada em m³/s", "chuva_mm": "chuva acumulada no intervalo, em mm"},
        "escopo_deste_script": "só um exemplo de consulta pontual (últimos 2 dias) — NÃO implementa ingestão contínua/agendada",
        "data_processamento": datetime.now(timezone.utc).isoformat(),
    }
    caminho_metadados = CAMINHO_SAIDA.with_suffix(".json")
    caminho_metadados.write_text(json.dumps(metadados, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    logger.info("Metadados salvos em %s", caminho_metadados)


if __name__ == "__main__":
    main()
