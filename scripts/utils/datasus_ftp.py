"""Utilitários para baixar e ler arquivos .dbc do FTP público do DATASUS
(SIM, SINAN, SIH), usados pelos scripts de saúde do projeto ClimaPampa.

Por que baixar direto do FTP em vez de usar a API de alto nível do pysus
(`pysus.sim()`, `pysus.sinan()`, `pysus.sih()`)
-------------------------------------------------------------------------
O pysus 2.x (versão disponível no PyPI em 2026, bem diferente da API 0.x
documentada na maior parte dos tutoriais) baixa os arquivos de um espelho
próprio em nuvem (catálogo "DuckLake"), não do FTP original. Validado por
consulta real antes de codificar:

- Esse espelho está incompleto para SIM e SIH: faltam anos inteiros (ex.
  SIM/RS/2013 não aparece no catálogo, mas existe no FTP oficial) e, no
  caso do SIH, a maioria dos meses de "RD" (AIH Reduzida) simplesmente não
  está indexada (ex. RS 2010-2024: só 35 dos ~180 arquivos mensais
  esperados apareceram no catálogo).
- O filtro `group=` da função de alto nível não funciona para SIM/SIH
  nesse catálogo (os registros estão sem grupo associado — um bug do lado
  do pysus, não algo controlável por parâmetro).
- Para SINAN, o catálogo publica o mesmo ano em dois arquivos duplicados
  (`DENGBR23.parquet` e `DENGBR23.csv.parquet`, ambos com as mesmas
  1.645.956 linhas) e a função de alto nível baixa e concatena os dois,
  duplicando todos os registros silenciosamente.

Por isso, este módulo baixa os `.dbc` originais direto do FTP oficial
(ftp.datasus.gov.br) — fonte completa e sem duplicação — e usa só o
conversor DBC→Parquet do próprio pysus (`pysus.api.extensions`, que por
sua vez usa `pyreaddbc` para descomprimir o formato proprietário DATASUS)
para ler os dados. Continua sendo "usar a biblioteca pysus", só que a
parte que de fato precisa de código nativo (descompressão do .dbc), não a
camada de catálogo/download que se mostrou não confiável para estes três
sistemas nesta versão.

Ambiente
--------
`pyreaddbc` e as versões de `numpy`/`pandas` que o pysus exige têm
extensões nativas sem wheel para Python 3.14 (o Python padrão deste
projeto — ver `.venv/`). Por isso, estes scripts rodam num venv separado,
`.venv-pysus/` (Python 3.11, criado via `uv python install 3.11` +
`uv venv --python 3.11 .venv-pysus`, sem precisar de privilégios de
root), que não interfere com o `.venv/` principal do projeto:

    uv venv --python 3.11 .venv-pysus
    uv pip install --python .venv-pysus/bin/python pysus
    .venv-pysus/bin/python scripts/download/saude_sim_obitos.py

CRS: não se aplica — dados tabulares sem geometria (ver CLAUDE.md).
"""

from __future__ import annotations

import asyncio
import logging
import time
from ftplib import FTP, error_perm
from pathlib import Path

import pandas as pd
import requests

logger = logging.getLogger(__name__)

FTP_HOST = "ftp.datasus.gov.br"
CODIGO_IBGE_DEFAULT = "4322400"  # Uruguaiana, RS
N_TENTATIVAS = 3
BACKOFF_BASE_S = 2.0

HEADERS = {"User-Agent": "Mozilla/5.0 (compativel; script-pesquisa-ClimaPampa/1.0)"}
URL_MUNICIPIO_IBGE = "https://servicodados.ibge.gov.br/api/v1/localidades/municipios/{codigo}"


def codigo_municipio_6_digitos(codigo_ibge: str) -> str:
    """Converte o código IBGE de 7 dígitos (com DV) para o de 6 usado por SIM/SIH.

    O 7º dígito do código IBGE é um dígito verificador; SIM e SIH (mas não
    o SINAN, que usa o código de 7 dígitos) identificam município pelos 6
    primeiros dígitos.
    """
    if len(codigo_ibge) != 7:
        raise ValueError(f"Código IBGE deveria ter 7 dígitos, recebido: {codigo_ibge!r}")
    return codigo_ibge[:6]


def obter_uf_sigla(codigo_ibge: str) -> tuple[str, str]:
    """Consulta a API de localidades do IBGE e retorna (nome_municipio, sigla_uf)."""
    url = URL_MUNICIPIO_IBGE.format(codigo=codigo_ibge)
    resposta = requests.get(url, headers=HEADERS, timeout=30)
    resposta.raise_for_status()
    dados = resposta.json()
    return dados["nome"], dados["microrregiao"]["mesorregiao"]["UF"]["sigla"]


def _conectar_ftp() -> FTP:
    ftp = FTP(FTP_HOST, timeout=120)
    ftp.login()
    return ftp


def listar_diretorio(caminho_remoto: str) -> list[str]:
    """Lista os nomes de arquivo de um diretório do FTP do DATASUS.

    Usado para descobrir o nome exato do arquivo (a capitalização da
    extensão varia entre arquivos — ex. "DORS2010.DBC" vs "DORS2013.dbc" —
    então é mais confiável listar e casar por regex do que montar o nome
    "no chute").
    """
    ftp = _conectar_ftp()
    try:
        ftp.cwd(caminho_remoto)
        return ftp.nlst()
    finally:
        ftp.quit()


def baixar_dbc(caminho_remoto: str, destino: Path) -> Path | None:
    """Baixa um .dbc do FTP do DATASUS, com retry e cache (idempotente).

    Retorna None (sem levantar erro) se o arquivo não existir no servidor
    — condição esperada para anos/meses/agravos ainda não publicados, que
    os scripts chamadores devem tratar como "sem dado disponível".
    """
    if destino.exists():
        logger.info("Já em cache: %s", destino)
        return destino

    destino.parent.mkdir(parents=True, exist_ok=True)
    destino_tmp = destino.with_suffix(destino.suffix + ".tmp")

    ultimo_erro: Exception | None = None
    for tentativa in range(1, N_TENTATIVAS + 1):
        try:
            ftp = _conectar_ftp()
            try:
                with open(destino_tmp, "wb") as f:
                    ftp.retrbinary(f"RETR {caminho_remoto}", f.write)
            finally:
                ftp.quit()
            destino_tmp.rename(destino)
            logger.info("Baixado: %s -> %s", caminho_remoto, destino)
            return destino
        except error_perm as erro:
            # 550 = arquivo não encontrado no servidor: não é falha transitória
            destino_tmp.unlink(missing_ok=True)
            if "550" in str(erro):
                logger.info("Não encontrado no FTP (sem dado disponível): %s", caminho_remoto)
                return None
            ultimo_erro = erro
        except Exception as erro:  # noqa: BLE001 — queremos capturar qualquer falha de rede/FTP para o retry
            ultimo_erro = erro
            destino_tmp.unlink(missing_ok=True)

        if tentativa < N_TENTATIVAS:
            espera = BACKOFF_BASE_S * (2 ** (tentativa - 1))
            logger.warning(
                "Falha ao baixar %s (tentativa %d/%d): %s — nova tentativa em %.0fs",
                caminho_remoto, tentativa, N_TENTATIVAS, ultimo_erro, espera,
            )
            time.sleep(espera)

    raise RuntimeError(f"Falha ao baixar {caminho_remoto} após {N_TENTATIVAS} tentativas: {ultimo_erro}")


def ler_dbc_como_dataframe(caminho_dbc: Path, colunas: list[str] | None = None) -> pd.DataFrame:
    """Descomprime e lê um .dbc do DATASUS como DataFrame, via pysus (pyreaddbc).

    Cacheia o .parquet convertido ao lado do .dbc (mesmo nome, outra
    extensão) para não reprocessar em execuções futuras.

    Se `colunas` for informado, o Parquet convertido é lido só com essas
    colunas (via pyarrow) — importante para os arquivos nacionais do
    SINAN, que passam de 1 milhão de linhas por ano: ler todas as ~120
    colunas originais para depois descartar quase tudo desperdiça memória
    à toa quando só precisamos de município/data/classificação.
    """
    from pysus.api.extensions import ExtensionFactory

    async def _converter_para_parquet() -> Path:
        dbc = await ExtensionFactory.instantiate(caminho_dbc)
        parquet = await dbc.to_parquet()
        return parquet.path

    caminho_parquet = asyncio.run(_converter_para_parquet())
    if colunas is None:
        return pd.read_parquet(caminho_parquet)
    return pd.read_parquet(caminho_parquet, columns=colunas)
