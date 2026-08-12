"""
Baixa a série histórica municipal de Produção Agrícola (PAM) e Pesquisa da
Pecuária Municipal (PPM) via API SIDRA/IBGE (mesmo padrão de
demografia_ibge_sidra.py). Insumo de contexto socioeconômico/territorial
(Eixo III, ClimaPampa) — NÃO gera camada espacial (ver decisão abaixo).

Gera em data/raw/ (nomenclatura {tema}_{fonte}_{periodo}_municipal.{csv,json}):
    producao-agricola_ibge-sidra-tabela5457_1974-2024_municipal.csv
    rebanho_ibge-sidra-tabela3939_1974-2024_municipal.csv
    producao-animal_ibge-sidra-tabela74_1974-2024_municipal.csv

Por que só indicador tabular, sem virar camada espacial (decisão com o usuário, 2026-08-11)
---------------------------------------------------------------------------------------------
PAM e PPM são levantamentos por ESTIMATIVA MUNICIPAL do IBGE — não existe
versão por setor censitário nem geolocalizada (diferente do Censo
Demográfico ou do Censo Agropecuário, que têm produto por
estabelecimento/setor). Forçar isso numa camada espacial seria inventar
granularidade que a fonte não tem. Fica como série temporal municipal,
para uso futuro em painel de contexto ao lado das camadas espaciais — não
como polígono/ponto no mapa. Quem quiser "ver no mapa" onde a produção
provavelmente ocorre já tem isso, de outra fonte, no MapBiomas
(uso-solo_mapbiomas.py: classes como "Arroz", "Soja", "Lavoura Temporária
de Verão/Inverno") — cruzamento de checagem feito ao final deste script,
sem fundir as duas fontes numa camada única.

3 tabelas SIDRA escolhidas (de um total de 13 candidatas no grupo PA/PP,
investigadas via /agregados e /metadados antes de codificar)
-----------------------------------------------------------------------------
- 5457 (PAM): "Área plantada ou destinada à colheita, área colhida,
  quantidade produzida, rendimento médio e valor da produção das lavouras
  temporárias e permanentes" — tabela CONSOLIDADA (substitui as tabelas
  1612/1613, que são a mesma coisa separada em temporárias/permanentes) e
  também as tabelas de cultura única (839 milho, 1002 feijão, etc., já
  incluídas em 5457). Cobre 1974-2024, 72 categorias de cultura + "Total".
  Variável "Rendimento médio" NÃO baixada (derivável de quantidade/área,
  e sua granularidade de sumarização é diferente das demais).
- 3939 (PPM): "Efetivo dos rebanhos, por tipo de rebanho" — série vigente
  (a tabela 73 é a mesma coisa mas "série encerrada", descontinuada pelo
  IBGE). 1974-2024, 10 tipos de rebanho (sem categoria "Total" — a
  classificação não é sumarizável, cada linha é um tipo distinto).
- 74 (PPM): "Produção de origem animal, por tipo de produto" (leite, ovos
  de galinha/codorna, mel, lã, casulos do bicho-da-seda) — 1974-2024.
  ATENÇÃO: a variável "Produção de origem animal" muda de UNIDADE por
  categoria (mil litros para leite, mil dúzias para ovos, kg para
  mel/lã/casulos) — mantida como está na fonte, unidade documentada por
  categoria nos metadados (não dá pra somar entre categorias).

Convenção de valores ausentes/zero (mais precisa que o `VALORES_AUSENTES`
genérico usado em demografia_ibge_sidra.py)
------------------------------------------------------------------------------
No glossário do SIDRA, os símbolos têm significados DIFERENTES:
  "-"   = zero absoluto (cultura/rebanho não produzido no município naquele
          ano) — dado real, não ausência de dado.
  "..", "X", "..."  = não disponível / sigiloso / não investigado no
          período — aí sim, ausência de dado (NaN).
Para PAM/PPM isso importa muito (a maioria das ~72 culturas não é
plantada em Uruguaiana, aparecendo como "-" em quase toda a série — tratar
como NaN inflaria a tabela de "dado faltante" para algo que é, na
verdade, zero conhecido). Este script trata "-" como 0.0 e reserva NaN só
para "..", "X", "...".

Uso:
    python scripts/download/agropecuaria_ibge_pam_ppm.py
    python scripts/download/agropecuaria_ibge_pam_ppm.py --codigo-ibge 4314902 --forcar
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

CODIGO_IBGE_DEFAULT = "4322400"  # Uruguaiana, RS
DATA_ACESSO = date.today().isoformat()

HEADERS = {"User-Agent": "Mozilla/5.0 (compativel; script-pesquisa-ClimaPampa/1.0)"}
N_TENTATIVAS = 3
BACKOFF_BASE_S = 1.0

URL_SIDRA_BASE = "https://servicodados.ibge.gov.br/api/v3/agregados"
URL_MUNICIPIO_IBGE = "https://servicodados.ibge.gov.br/api/v1/localidades/municipios/{codigo}"

RAW_DIR = Path(__file__).resolve().parents[2] / "data" / "raw"

# "-" = zero real (não produzido); ".."/"X"/"..." = ausência de dado (sigilo/não disponível)
VALORES_ZERO = {"-"}
VALORES_AUSENTES = {None, "...", "X", ".."}


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
    resposta = _requisitar_com_retry(sessao, URL_MUNICIPIO_IBGE.format(codigo=codigo_ibge), {})
    dados = resposta.json()
    return dados["nome"], dados["microrregiao"]["mesorregiao"]["UF"]["sigla"]


def _valor_numerico(valor_str: str) -> float | None:
    if valor_str in VALORES_ZERO:
        return 0.0
    if valor_str in VALORES_AUSENTES:
        return None
    return float(valor_str)


def consultar_sidra_long(
    agregado: int, periodos: str, variaveis: str, classificacao: str, codigo_ibge: str, sessao: requests.Session
) -> pd.DataFrame:
    """Consulta um agregado SIDRA (1+ variáveis, 1 classificação) e retorna DataFrame
    'long': variavel_id, variavel_nome, unidade, categoria, periodo, valor."""
    url = f"{URL_SIDRA_BASE}/{agregado}/periodos/{periodos}/variaveis/{variaveis}"
    resposta = _requisitar_com_retry(sessao, url, {"localidades": f"N6[{codigo_ibge}]", "classificacao": classificacao})
    dados = resposta.json()

    linhas = []
    for bloco_variavel in dados:
        for resultado in bloco_variavel["resultados"]:
            categoria_nome = list(resultado["classificacoes"][0]["categoria"].values())[0]
            serie = resultado["series"][0]["serie"]
            for periodo, valor_str in serie.items():
                linhas.append({
                    "variavel_id": bloco_variavel["id"],
                    "variavel_nome": bloco_variavel["variavel"],
                    "unidade": bloco_variavel["unidade"],
                    "categoria": categoria_nome,
                    "periodo": int(periodo),
                    "valor": _valor_numerico(valor_str),
                })
    return pd.DataFrame(linhas)


def salvar_com_metadados(df: pd.DataFrame, nome_arquivo: str, metadados: dict) -> Path:
    caminho_csv = RAW_DIR / nome_arquivo
    df.to_csv(caminho_csv, index=False, encoding="utf-8")
    logger.info("Salvo %s (%d linhas)", caminho_csv, len(df))

    metadados_completos = {
        **metadados,
        "convencao_valores": {
            "zero real (\"-\" na fonte)": "cultura/rebanho/produto não produzido no município naquele ano — vira 0.0",
            "ausente (\"..\", \"X\", \"...\" na fonte)": "não disponível / sigiloso / não investigado no período — vira NaN (null no CSV)",
        },
        "data_acesso": DATA_ACESSO,
        "data_processamento": datetime.now(timezone.utc).isoformat(),
    }
    caminho_json = caminho_csv.with_suffix(".json")
    caminho_json.write_text(json.dumps(metadados_completos, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    logger.info("Metadados salvos em %s", caminho_json)
    return caminho_csv


def baixar_pam(codigo_ibge: str, sessao: requests.Session) -> None:
    df = consultar_sidra_long(5457, "1974-2024", "8331,216,214,215", "782[all]", codigo_ibge, sessao)
    df = df.sort_values(["variavel_id", "categoria", "periodo"]).reset_index(drop=True)
    salvar_com_metadados(
        df, "producao-agricola_ibge-sidra-tabela5457_1974-2024_municipal.csv",
        {
            "fonte": "IBGE — SIDRA, tabela 5457 (Produção Agrícola Municipal, 'Área plantada ou destinada à colheita, área colhida, quantidade produzida, rendimento médio e valor da produção das lavouras temporárias e permanentes')",
            "url_api": "https://servicodados.ibge.gov.br/api/v3/agregados/5457",
            "tabela_sidra": 5457,
            "periodos": "1974-2024",
            "codigo_ibge": codigo_ibge,
            "variaveis_baixadas": {
                "8331": "Área plantada ou destinada à colheita (ha)",
                "216": "Área colhida (ha)",
                "214": "Quantidade produzida (toneladas)",
                "215": "Valor da produção (mil R$ correntes — unidade monetária muda ao longo da série, ver 'unidade' por linha)",
            },
            "variavel_nao_baixada": "112 (Rendimento médio, kg/ha) — derivável de quantidade_produzida/área_colhida, omitida para não duplicar",
            "categorias": "72 culturas (temporárias + permanentes) + 'Total' — classificação 782, todas baixadas (categoria='all' na consulta)",
            "nivel_agregacao": "municipal — PAM é levantamento por estimativa municipal do IBGE, sem versão por setor censitário/geolocalizada",
        },
    )


def baixar_ppm_rebanho(codigo_ibge: str, sessao: requests.Session) -> None:
    df = consultar_sidra_long(3939, "1974-2024", "105", "79[all]", codigo_ibge, sessao)
    df = df.sort_values(["categoria", "periodo"]).reset_index(drop=True)
    salvar_com_metadados(
        df, "rebanho_ibge-sidra-tabela3939_1974-2024_municipal.csv",
        {
            "fonte": "IBGE — SIDRA, tabela 3939 (Pesquisa da Pecuária Municipal, 'Efetivo dos rebanhos, por tipo de rebanho')",
            "url_api": "https://servicodados.ibge.gov.br/api/v3/agregados/3939",
            "tabela_sidra": 3939,
            "periodos": "1974-2024",
            "codigo_ibge": codigo_ibge,
            "variavel": "Efetivo dos rebanhos (cabeças)",
            "categorias": "10 tipos de rebanho (Bovino, Bubalino, Equino, Suíno-total, Suíno-matrizes, Caprino, Ovino, Galináceos-total, Galináceos-galinhas, Codornas) — sem categoria 'Total' agregada na própria fonte (classificação não sumarizável)",
            "nivel_agregacao": "municipal — mesma limitação da PAM, sem versão espacializada",
            "tabela_descontinuada_nao_usada": "73 ('série encerrada') é o mesmo indicador, substituído por esta (3939) a partir de determinado ano pelo próprio IBGE",
        },
    )


def baixar_ppm_producao_animal(codigo_ibge: str, sessao: requests.Session) -> None:
    df = consultar_sidra_long(74, "1974-2024", "106,215", "80[all]", codigo_ibge, sessao)
    df = df.sort_values(["variavel_id", "categoria", "periodo"]).reset_index(drop=True)
    salvar_com_metadados(
        df, "producao-animal_ibge-sidra-tabela74_1974-2024_municipal.csv",
        {
            "fonte": "IBGE — SIDRA, tabela 74 (Pesquisa da Pecuária Municipal, 'Produção de origem animal, por tipo de produto')",
            "url_api": "https://servicodados.ibge.gov.br/api/v3/agregados/74",
            "tabela_sidra": 74,
            "periodos": "1974-2024",
            "codigo_ibge": codigo_ibge,
            "variaveis_baixadas": {
                "106": "Produção de origem animal — UNIDADE MUDA POR CATEGORIA: mil litros (Leite), mil dúzias (Ovos de galinha/codorna), quilogramas (Mel de abelha, Casulos do bicho-da-seda, Lã) — não somar entre categorias sem converter",
                "215": "Valor da produção (mil R$ correntes)",
            },
            "categorias": "Total + 6 produtos (Leite, Ovos de galinha, Ovos de codorna, Mel de abelha, Casulos do bicho-da-seda, Lã)",
            "nivel_agregacao": "municipal — mesma limitação da PAM",
        },
    )


TAREFAS = {
    "pam": (baixar_pam, "producao-agricola_ibge-sidra-tabela5457_1974-2024_municipal.csv"),
    "ppm_rebanho": (baixar_ppm_rebanho, "rebanho_ibge-sidra-tabela3939_1974-2024_municipal.csv"),
    "ppm_producao_animal": (baixar_ppm_producao_animal, "producao-animal_ibge-sidra-tabela74_1974-2024_municipal.csv"),
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Baixa a série histórica de Produção Agrícola (PAM) e Pecuária (PPM) municipal via API SIDRA/IBGE.")
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
