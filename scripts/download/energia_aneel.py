"""Baixa os indicadores de continuidade do fornecimento de energia
elétrica (DEC e FEC) para os "conjuntos de unidades consumidoras" que
atendem um município, a partir da base de Dados Abertos da ANEEL. Gera:

    data/raw/continuidade-energia_aneel_2000-{ultimo_ano}_conjunto-consumidor.csv
    data/raw/continuidade-energia_aneel_2000-{ultimo_ano}_conjunto-consumidor.json

Distribuidora de Uruguaiana (confirmada, não suposição)
------------------------------------------------------------
RGE SUL (grupo CPFL/Grupo Recharge) — CEEE-D atende só 72 municípios da
região metropolitana de Porto Alegre; RGE atende os outros 381,
Uruguaiana incluída. Confirmado filtrando a própria base pelo nome do
município (SigAgente="RGE SUL", ver abaixo), não hardcoded a priori.

Granularidade real: "conjunto de unidades consumidoras", não município
puro nem distribuidora pura
------------------------------------------------------------------------------
Nem só município, nem só distribuidora (as duas opções que o pedido
original considerou) — a ANEEL apura DEC/FEC por "conjunto", uma unidade
administrativa da distribuidora que pode ser MAIS FINA que o município
(ex.: Uruguaiana tem pelo menos 2 conjuntos na série mais recente,
"Uruguaiana 1" e "Uruguaiana 7") ou dividida por zona urbana/rural em
anos mais antigos ("URUGUAIANA URB", "URUGUAIANA NURB", "URUGUAIANA
IBICUI/P.ALTO NURB" na década 2010-2019). Tratado como indicador de
CONTEXTO tabular (sem virar camada espacial — a ANEEL não publica
geometria dos conjuntos na base aberta), mas mantendo a granularidade de
conjunto (não agregado num único número por município), documentando a
reorganização dos conjuntos ao longo do tempo como quebra de
comparabilidade (mesmo espírito da quebra 2020 do CAGED e das quebras
censitárias já registradas no projeto) — filtro por texto "URUGUAIANA"
na descrição do conjunto, não por ID fixo (os IDs mudam entre
reorganizações).

Fonte e método
----------------
dadosabertos.aneel.gov.br (CKAN), dataset "indicadores-coletivos-de-
continuidade-dec-e-fec" — 3 arquivos Parquet nacionais (2000-2009,
2010-2019, 2020-2029), baixados e filtrados localmente pelo nome do
conjunto.

Uso:
    python scripts/download/energia_aneel.py
    python scripts/download/energia_aneel.py --municipio-busca "PORTO ALEGRE" --forcar
"""

import argparse
import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

RAIZ = Path(__file__).resolve().parents[2]
CODIGO_IBGE_DEFAULT = "4322400"  # Uruguaiana, RS
MUNICIPIO_BUSCA_DEFAULT = "URUGUAIANA"  # texto buscado em DscConjUndConsumidoras — não é o mesmo que --codigo-ibge

URL_PACKAGE = "https://dadosabertos.aneel.gov.br/api/3/action/package_show?id=indicadores-coletivos-de-continuidade-dec-e-fec"
RECURSOS_PARQUET = [
    "indicadores-continuidade-coletivos-2000-2009.parquet",
    "indicadores-continuidade-coletivos-2010-2019.parquet",
    "indicadores-continuidade-coletivos-2020-2029.parquet",
]
HEADERS = {"User-Agent": "Mozilla/5.0 (compativel; script-pesquisa-ClimaPampa/1.0)"}


def obter_urls_recursos() -> dict[str, str]:
    resposta = requests.get(URL_PACKAGE, headers=HEADERS, timeout=30)
    resposta.raise_for_status()
    recursos = resposta.json()["result"]["resources"]
    return {r["name"]: r["url"] for r in recursos if r["name"] in RECURSOS_PARQUET}


def baixar_e_filtrar(url: str, texto_busca: str) -> pd.DataFrame:
    logger.info("Baixando %s...", url)
    df = pd.read_parquet(url)
    filtrado = df[df["DscConjUndConsumidoras"].astype(str).str.contains(texto_busca, case=False, na=False)].copy()
    logger.info("  -> %d linhas (%d conjuntos distintos)", len(filtrado), filtrado["DscConjUndConsumidoras"].str.strip().nunique())
    return filtrado


def main() -> None:
    parser = argparse.ArgumentParser(description="Baixa indicadores de continuidade DEC/FEC (ANEEL) para os conjuntos de um município.")
    parser.add_argument("--codigo-ibge", default=CODIGO_IBGE_DEFAULT, help="Código IBGE do município, só para nomear o arquivo de saída e o metadado (default: Uruguaiana/RS)")
    parser.add_argument("--municipio-busca", default=MUNICIPIO_BUSCA_DEFAULT, help="Texto buscado no nome do conjunto de unidades consumidoras (default: URUGUAIANA)")
    parser.add_argument("--forcar", action="store_true", help="Ignora arquivo já existente e baixa tudo de novo")
    args = parser.parse_args()

    caminho_saida_glob = list((RAIZ / "data" / "raw").glob("continuidade-energia_aneel_*_conjunto-consumidor.csv"))
    if caminho_saida_glob and not args.forcar:
        logger.info("%s já existe — nada a fazer (use --forcar para baixar de novo).", caminho_saida_glob[0])
        return

    urls = obter_urls_recursos()
    logger.info("Recursos encontrados: %s", list(urls.keys()))

    partes = [baixar_e_filtrar(url, args.municipio_busca) for url in urls.values()]
    bruto = pd.concat(partes, ignore_index=True)

    bruto["DscConjUndConsumidoras"] = bruto["DscConjUndConsumidoras"].str.strip()
    bruto["SigAgente"] = bruto["SigAgente"].str.strip()
    distribuidoras = sorted(bruto["SigAgente"].unique())
    conjuntos = sorted(bruto["DscConjUndConsumidoras"].unique())

    tabela = bruto.rename(columns={
        "DscConjUndConsumidoras": "conjunto", "SigAgente": "distribuidora", "SigIndicador": "indicador",
        "AnoIndice": "ano", "NumPeriodoIndice": "mes", "VlrIndiceEnviado": "valor",
    })[["ano", "mes", "distribuidora", "conjunto", "indicador", "valor"]].sort_values(["ano", "mes", "conjunto", "indicador"])

    ano_min, ano_max = int(tabela["ano"].min()), int(tabela["ano"].max())
    caminho_saida = RAIZ / "data" / "raw" / f"continuidade-energia_aneel_{ano_min}-{ano_max}_conjunto-consumidor.csv"
    caminho_saida.parent.mkdir(parents=True, exist_ok=True)
    tabela.to_csv(caminho_saida, index=False, encoding="utf-8")

    metadados = {
        "fonte": "ANEEL — Dados Abertos, 'Indicadores Coletivos de Continuidade (DEC e FEC)'",
        "url_dataset": "https://dadosabertos.aneel.gov.br/dataset/indicadores-coletivos-de-continuidade-dec-e-fec",
        "codigo_ibge": args.codigo_ibge,
        "texto_busca_conjunto": args.municipio_busca,
        "distribuidoras_encontradas": distribuidoras,
        "periodo_coberto": f"{ano_min}-{ano_max}",
        "nivel_agregacao": (
            "conjunto de unidades consumidoras (unidade administrativa da distribuidora) — mais fino "
            "que município simples, mas sem geometria publicada pela fonte; indicador de CONTEXTO "
            "tabular, sem camada espacial"
        ),
        "conjuntos_encontrados": conjuntos,
        "quebra_comparabilidade_conjuntos": (
            "os CONJUNTOS foram reorganizados ao longo do tempo pela distribuidora — a década "
            "2010-2019 tem conjuntos 'URUGUAIANA URB'/'URUGUAIANA NURB'/'URUGUAIANA IBICUI/P.ALTO "
            "NURB'/'URUGUAIANA 1'/'URUGUAIANA 4', diferentes dos conjuntos atuais 'Uruguaiana 1'/"
            "'Uruguaiana 7' — não somar/comparar diretamente a mesma 'Uruguaiana N' através dos anos "
            "sem checar se é o mesmo conjunto (nome pode ter sido reaproveitado para área diferente); "
            "filtrado por texto no nome, não por ID fixo, para não perder nenhum período"
        ),
        "colunas": {
            "indicador": "DEC (Duração Equivalente de Interrupção por Unidade Consumidora, horas) ou FEC (Frequência Equivalente, nº de interrupções)",
            "mes": "mês de apuração (1-12) — indicador mensal, não só anual",
            "valor": "valor do indicador enviado pela distribuidora no período",
        },
        "data_acesso": date.today().isoformat(),
        "data_processamento": datetime.now(timezone.utc).isoformat(),
    }
    caminho_saida.with_suffix(".json").write_text(json.dumps(metadados, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

    logger.info("Salvo: %s (%d linhas, %d-%d, conjuntos: %s)", caminho_saida, len(tabela), ano_min, ano_max, conjuntos)
    logger.info("Metadados salvos em %s", caminho_saida.with_suffix(".json"))


if __name__ == "__main__":
    main()
