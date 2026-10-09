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

Série de um período e série histórica (opcionais)
---------------------------------------------------
- `--inicio AAAA-MM-DD --fim AAAA-MM-DD` grava as leituras telemétricas do
  período (pedidas mês a mês) em
  data/raw/nivel-rio_ana-telemetria-{estação}_{início}-a-{fim}_15min.csv;
- `--serie-historica` grava a série de cotas da operação
  `HidroSerieHistorica` (tipoDados=1), inteira, em
  data/raw/nivel-rio_ana-serie-historica-{estação}_{ano}-{ano}_diario.csv
  (uma linha por dia e por tipo de registro: média diária ou leitura em
  horário fixo; dado bruto e consistido).
As duas são idempotentes (não baixam de novo se o arquivo e o .json irmão
existem; `--forcar` refaz) e gravam o .json irmão com fonte, endereço
consultado, data, tamanho e sha256. `--estacao` informa o código da
estação e dispensa a busca pelo município. Sem essas opções o script faz
só o exemplo de consulta, como antes.

Uso:
    python scripts/download/nivel_rio_ana.py
    python scripts/download/nivel_rio_ana.py --inicio 2024-04-01 --fim 2024-06-30 --serie-historica
    python scripts/download/nivel_rio_ana.py --codigo-ibge 4314902 --nome-rio "GUAIBA"
"""

import argparse
import hashlib
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
    return consultar_leituras(codigo_estacao, inicio, fim)


def consultar_leituras(codigo_estacao: str, inicio, fim, timeout: int = 30) -> pd.DataFrame:
    """Leituras telemétricas (nível, vazão, chuva) entre duas datas, como o serviço devolve."""
    params = {
        "codEstacao": codigo_estacao,
        "dataInicio": inicio.strftime("%d/%m/%Y"),
        "dataFim": fim.strftime("%d/%m/%Y"),
    }
    resposta = requests.get(f"{BASE_URL_ANA}/DadosHidrometeorologicos", headers=HEADERS, params=params, timeout=timeout)
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


def gravar_bruto(df: pd.DataFrame, caminho: Path, **campos) -> None:
    """Grava o CSV e o .json irmão (fonte, endereço consultado, data, tamanho, sha256)."""
    caminho.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(caminho, index=False, encoding="utf-8")
    metadados = {
        "arquivo": caminho.name,
        "fonte": "ANA — Web Service legado telemetriaws1.ana.gov.br/ServiceANA.asmx, sem autenticação",
        "data_acesso": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "tamanho_bytes": caminho.stat().st_size,
        "sha256": hashlib.sha256(caminho.read_bytes()).hexdigest(),
        "n_linhas": len(df),
        **campos,
    }
    caminho.with_suffix(".json").write_text(json.dumps(metadados, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    logger.info("%d linhas salvas em %s", len(df), caminho)


def ja_baixado(caminho: Path, forcar: bool) -> bool:
    if caminho.exists() and caminho.with_suffix(".json").exists() and not forcar:
        logger.info("%s já existe — nada a baixar (use --forcar para refazer).", caminho.name)
        return True
    return False


def baixar_periodo(codigo_estacao: str, inicio: datetime, fim: datetime, forcar: bool = False) -> Path:
    """Leituras telemétricas do período, pedidas mês a mês (pedidos longos demoram e podem ser cortados)."""
    caminho = RAIZ / "data" / "raw" / f"nivel-rio_ana-telemetria-{codigo_estacao}_{inicio:%Y-%m-%d}-a-{fim:%Y-%m-%d}_15min.csv"
    if ja_baixado(caminho, forcar):
        return caminho
    partes, pedidos, a = [], [], inicio
    while a <= fim:
        b = min((a.replace(day=1) + timedelta(days=32)).replace(day=1) - timedelta(days=1), fim)
        df = consultar_leituras(codigo_estacao, a, b, timeout=120)
        pedidos.append({"dataInicio": f"{a:%d/%m/%Y}", "dataFim": f"{b:%d/%m/%Y}", "leituras": len(df)})
        logger.info("Telemetria %s a %s: %d leituras", pedidos[-1]["dataInicio"], pedidos[-1]["dataFim"], len(df))
        partes.append(df)
        a = b + timedelta(days=1)
    serie = pd.concat(partes, ignore_index=True)
    if serie.empty:
        logger.warning("O serviço não devolveu leituras para o período pedido; nada gravado.")
        return caminho
    # o serviço não informa o fuso de DataHora: a coluna fica com o nome neutro "data_hora"
    serie = serie.rename(columns={"data_hora_utc": "data_hora"}).drop_duplicates(["codigo_estacao", "data_hora"]).sort_values("data_hora")
    gravar_bruto(serie, caminho, endereco_consultado=f"{BASE_URL_ANA}/DadosHidrometeorologicos", pedidos=pedidos, codigo_estacao=codigo_estacao,
                 periodo={"inicio": f"{inicio:%Y-%m-%d}", "fim": f"{fim:%Y-%m-%d}"},
                 campos={"data_hora": "data e hora da leitura como o serviço devolve (fuso não informado pelo serviço)", "nivel_cm": "cota do rio em cm",
                         "vazao_m3s": "vazão estimada em m³/s", "chuva_mm": "chuva no intervalo, em mm"},
                 transformacao="pedidos mensais concatenados; leituras repetidas retiradas; ordem crescente de data e hora; valores como vieram")
    return caminho


def baixar_serie_historica(codigo_estacao: str, forcar: bool = False, desde: datetime | None = None) -> Path | None:
    """Série histórica de cotas (HidroSerieHistorica, tipoDados=1), em formato longo (uma linha por dia).

    Sem `desde`: a série inteira. Com `desde`: só dessa data até hoje, em arquivo próprio
    (…_{desde}-a-{hoje}_diario.csv); a série inteira já gravada não é tocada.
    """
    pasta = RAIZ / "data" / "raw"
    params = {"codEstacao": codigo_estacao, "dataInicio": "", "dataFim": "", "tipoDados": "1", "nivelConsistencia": ""}
    if desde is not None:
        hoje = datetime.now()
        params.update(dataInicio=f"{desde:%d/%m/%Y}", dataFim=f"{hoje:%d/%m/%Y}")
        caminho_do_trecho = pasta / f"nivel-rio_ana-serie-historica-{codigo_estacao}_{desde:%Y-%m-%d}-a-{hoje:%Y-%m-%d}_diario.csv"
        if ja_baixado(caminho_do_trecho, forcar):
            return caminho_do_trecho
    else:
        existentes = sorted(pasta.glob(f"nivel-rio_ana-serie-historica-{codigo_estacao}_????-????_diario.csv"))
        if existentes and ja_baixado(existentes[-1], forcar):
            return existentes[-1]
    resposta = requests.get(f"{BASE_URL_ANA}/HidroSerieHistorica", headers=HEADERS, params=params, timeout=600)
    resposta.raise_for_status()
    linhas = []
    for t in ET.fromstring(resposta.text).iter("SerieHistorica"):
        mes = pd.Timestamp((t.findtext("DataHora") or "").strip())
        for dia in range(1, mes.days_in_month + 1):
            cota = t.findtext(f"Cota{dia:02d}")
            linhas.append({
                "codigo_estacao": t.findtext("EstacaoCodigo"),
                "data": f"{mes.year:04d}-{mes.month:02d}-{dia:02d}",
                "hora_do_registro": f"{mes:%H:%M}",
                "media_diaria": t.findtext("MediaDiaria"),
                "nivel_consistencia": t.findtext("NivelConsistencia"),
                "cota_cm": cota if cota not in (None, "") else None,
                "status": t.findtext(f"Cota{dia:02d}Status"),
                "maxima_do_mes_cm": t.findtext("Maxima"),
            })
    serie = pd.DataFrame(linhas)
    if serie.empty:
        logger.warning("O serviço não devolveu série histórica de cotas para a estação %s; nada gravado.", codigo_estacao)
        return None
    serie = serie.sort_values(["data", "nivel_consistencia", "media_diaria", "hora_do_registro"])
    caminho = caminho_do_trecho if desde is not None else pasta / f"nivel-rio_ana-serie-historica-{codigo_estacao}_{serie.data.min()[:4]}-{serie.data.max()[:4]}_diario.csv"
    gravar_bruto(serie, caminho, endereco_consultado=f"{BASE_URL_ANA}/HidroSerieHistorica", parametros=params, codigo_estacao=codigo_estacao,
                 periodo={"inicio": serie.data.min(), "fim": serie.data.max()},
                 campos={"hora_do_registro": "hora do registro mensal de origem (00:00 nas médias diárias; hora da leitura nos demais)",
                         "media_diaria": "1 = média diária; 0 = leitura em horário fixo", "nivel_consistencia": "1 = dado bruto; 2 = dado consistido",
                         "cota_cm": "cota do dia em cm (vazio = sem valor na fonte)", "status": "situação da cota na fonte (código da ANA)",
                         "maxima_do_mes_cm": "máxima do mês informada pela fonte no mesmo registro"},
                 transformacao="registros mensais (Cota01..Cota31) abertos em uma linha por dia; valores como vieram")
    return caminho


def main() -> None:
    parser = argparse.ArgumentParser(description="Verifica viabilidade e exemplifica consulta de nível de rio via ANA (telemetria).")
    parser.add_argument("--codigo-ibge", default=CODIGO_IBGE_DEFAULT)
    parser.add_argument("--nome-rio", default=None, help="Filtro opcional por nome do rio (ex. URUGUAI) quando o município tem mais de uma estação")
    parser.add_argument("--estacao", default=None, help="Código da estação (dispensa a busca pelo município) para --inicio/--fim e --serie-historica")
    parser.add_argument("--inicio", default=None, help="Início do período (AAAA-MM-DD): grava a série telemétrica do período em data/raw/")
    parser.add_argument("--fim", default=None, help="Fim do período (AAAA-MM-DD)")
    parser.add_argument("--serie-historica", action="store_true", help="Grava a série histórica de cotas da estação, inteira, em data/raw/")
    parser.add_argument("--serie-historica-desde", default=None, metavar="AAAA-MM-DD", help="Grava só o trecho da série histórica de cotas dessa data até hoje, em arquivo próprio (a série inteira já gravada não é tocada)")
    parser.add_argument("--forcar", action="store_true", help="Baixa de novo mesmo se os arquivos do período ou da série histórica já existirem")
    args = parser.parse_args()
    if bool(args.inicio) != bool(args.fim):
        parser.error("--inicio e --fim vão juntos")
    so_series = bool(args.inicio or args.serie_historica or args.serie_historica_desde)

    if so_series and args.estacao:
        baixar_series(args.estacao, args)
        return

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

    if so_series:  # período e/ou série histórica: o exemplo de consulta não é regravado
        baixar_series(estacao["codigo"], args)
        return

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


def baixar_series(codigo_estacao: str, args) -> None:
    """Série do período e série histórica; a falha de uma não impede a outra."""
    if args.inicio:
        try:
            baixar_periodo(codigo_estacao, datetime.strptime(args.inicio, "%Y-%m-%d"), datetime.strptime(args.fim, "%Y-%m-%d"), args.forcar)
        except (requests.RequestException, ET.ParseError) as erro:
            logger.error("Série do período não baixada: %s", erro)
    if args.serie_historica:
        try:
            baixar_serie_historica(codigo_estacao, args.forcar)
        except (requests.RequestException, ET.ParseError) as erro:
            logger.error("Série histórica não baixada: %s", erro)
    if args.serie_historica_desde:
        try:
            baixar_serie_historica(codigo_estacao, args.forcar, desde=datetime.strptime(args.serie_historica_desde, "%Y-%m-%d"))
        except (requests.RequestException, ET.ParseError) as erro:
            logger.error("Trecho da série histórica não baixado: %s", erro)


if __name__ == "__main__":
    main()
