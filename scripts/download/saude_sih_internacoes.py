"""Baixa internações hospitalares por causas respiratórias do SIH
(Sistema de Informação Hospitalar / DATASUS) para um município, por ano e
mês. Gera:

    data/raw/internacoes-respiratorias_sih-datasus_{ano_inicio}-{ano_fim}_municipal.csv
    data/raw/internacoes-respiratorias_sih-datasus_{ano_inicio}-{ano_fim}_municipal.json

Fonte e método (ver docstring de scripts/utils/datasus_ftp.py para o porquê
de baixar direto do FTP em vez da API de alto nível do pysus)
-------------------------------------------------------------------------
FTP público do DATASUS, um arquivo .dbc por UF/ano/mês do grupo "RD" (AIH
Reduzida — a internação em si, com diagnóstico) em
`/dissemin/publicos/SIHSUS/200801_/Dados/RD{UF}{aa}{mm}.dbc`. Validado por
consulta real ao FTP antes de codificar: o catálogo em nuvem que a API de
alto nível do pysus usa está muito incompleto para SIH/RD (ex. Rio Grande
do Sul 2010-2024: só 35 dos ~180 arquivos mensais esperados apareciam
indexados lá, com anos inteiros como 2018 e 2023 zerados) — o FTP
original tem a série completa. Cada .dbc é descomprimido e lido só nas
colunas necessárias (MUNIC_RES, DIAG_PRINC) via pyreaddbc (dependência do
pysus), e filtrado localmente pelos 6 primeiros dígitos do código IBGE.

Nível de agregação: MUNICIPAL (contagem por ano/mês) — não espacializado
por bairro/setor; SIH registra só o município de residência do paciente.

Definição de "causas respiratórias": DIAG_PRINC (diagnóstico principal da
internação, CID-10) inicia com "J" (capítulo X, doenças do aparelho
respiratório, J00-J99) — mesmo critério usado no script do SIM
(scripts/download/saude_sim_obitos.py), para permitir comparação.

Sigilo / dado ausente vs. zero: mesma lógica do SIM/SINAN — microdados
individuais, sem supressão por sigilo aplicada pela fonte; "sem arquivo
disponível" (mês sem publicação no FTP) é NA, nunca zero.

Ambiente: rodar com `.venv-pysus/bin/python` (ver
scripts/utils/datasus_ftp.py).

Uso:
    .venv-pysus/bin/python scripts/download/saude_sih_internacoes.py
    .venv-pysus/bin/python scripts/download/saude_sih_internacoes.py --codigo-ibge 4314902 --forcar
"""

import argparse
import json
import logging
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

sys.path.append(str(Path(__file__).resolve().parents[1] / "utils"))
from datasus_ftp import (  # noqa: E402
    CODIGO_IBGE_DEFAULT,
    baixar_dbc,
    codigo_municipio_6_digitos,
    ler_dbc_como_dataframe,
    listar_diretorio,
    obter_uf_sigla,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

DIRETORIO_REMOTO_SIH = "/dissemin/publicos/SIHSUS/200801_/Dados"
COLUNAS_NECESSARIAS = ["MUNIC_RES", "DIAG_PRINC"]
ANO_INICIO_DEFAULT = 2010
CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "raw" / "cache_datasus" / "sih"
CAMINHO_SAIDA_TEMPLATE = Path(__file__).resolve().parents[2] / "data" / "raw" / "internacoes-respiratorias_sih-datasus_{inicio}-{fim}_municipal.csv"


def mapear_arquivos_rd(uf: str) -> dict[tuple[int, int], str]:
    """Lista o diretório do SIH uma vez e mapeia (ano, mês) -> nome exato do arquivo RD da UF."""
    nomes = listar_diretorio(DIRETORIO_REMOTO_SIH)
    padrao = re.compile(rf"^RD{uf}(\d{{2}})(\d{{2}})\.dbc$", re.IGNORECASE)
    mapa = {}
    for nome in nomes:
        m = padrao.match(nome)
        if m:
            ano = 2000 + int(m.group(1))
            mes = int(m.group(2))
            mapa[(ano, mes)] = nome
    return mapa


def baixar_e_filtrar_mes(nome_arquivo: str, codigo_municipio_6: str) -> pd.DataFrame:
    destino = CACHE_DIR / f"{Path(nome_arquivo).stem}.dbc"
    caminho_remoto = f"{DIRETORIO_REMOTO_SIH}/{nome_arquivo}"
    baixado = baixar_dbc(caminho_remoto, destino)
    if baixado is None:
        raise RuntimeError(f"Arquivo listado no FTP mas falhou ao baixar: {caminho_remoto}")

    df = ler_dbc_como_dataframe(baixado, colunas=COLUNAS_NECESSARIAS)
    df["MUNIC_RES"] = df["MUNIC_RES"].astype(str).str.strip()
    return df[df["MUNIC_RES"] == codigo_municipio_6].copy()


def linha_do_mes(df_municipio: pd.DataFrame, ano: int, mes: int, codigo_ibge: str, nome_municipio: str) -> dict:
    diag = df_municipio["DIAG_PRINC"].astype(str).str.strip().str.upper()
    return {
        "ano": ano,
        "mes": mes,
        "codigo_ibge": codigo_ibge,
        "nome_municipio": nome_municipio,
        "internacoes_total": len(df_municipio),
        "internacoes_respiratorias": int(diag.str.startswith("J").sum()),
        "arquivo_disponivel": True,
    }


def linha_sem_dado(ano: int, mes: int, codigo_ibge: str, nome_municipio: str) -> dict:
    return {
        "ano": ano,
        "mes": mes,
        "codigo_ibge": codigo_ibge,
        "nome_municipio": nome_municipio,
        "internacoes_total": pd.NA,
        "internacoes_respiratorias": pd.NA,
        "arquivo_disponivel": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Baixa internações respiratórias do SIH/DATASUS por ano/mês para um município.")
    parser.add_argument("--codigo-ibge", default=CODIGO_IBGE_DEFAULT)
    parser.add_argument("--ano-inicio", type=int, default=ANO_INICIO_DEFAULT)
    parser.add_argument("--ano-fim", type=int, default=None, help="Default: último ano com todos os 12 meses publicados no FTP")
    parser.add_argument("--forcar", action="store_true")
    args = parser.parse_args()

    if args.forcar:
        shutil.rmtree(CACHE_DIR, ignore_errors=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    nome_municipio, uf = obter_uf_sigla(args.codigo_ibge)
    codigo_municipio_6 = codigo_municipio_6_digitos(args.codigo_ibge)
    logger.info("Município: %s (%s) — código IBGE %s / 6 dígitos %s", nome_municipio, uf, args.codigo_ibge, codigo_municipio_6)

    mapa = mapear_arquivos_rd(uf)
    if args.ano_fim:
        ano_fim = args.ano_fim
    else:
        anos_completos = sorted({
            ano for ano in {a for a, _ in mapa}
            if all((ano, m) in mapa for m in range(1, 13))
        })
        ano_fim = anos_completos[-1] if anos_completos else max(a for a, _ in mapa)
    logger.info("Janela: %d-%d", args.ano_inicio, ano_fim)

    linhas = []
    meses_sem_arquivo = []
    for ano in range(args.ano_inicio, ano_fim + 1):
        for mes in range(1, 13):
            nome_arquivo = mapa.get((ano, mes))
            if nome_arquivo is None:
                meses_sem_arquivo.append(f"{ano}-{mes:02d}")
                linhas.append(linha_sem_dado(ano, mes, args.codigo_ibge, nome_municipio))
                continue
            df_municipio = baixar_e_filtrar_mes(nome_arquivo, codigo_municipio_6)
            linhas.append(linha_do_mes(df_municipio, ano, mes, args.codigo_ibge, nome_municipio))
        logger.info("Ano %d processado.", ano)

    tabela = pd.DataFrame(linhas)
    caminho_saida = Path(str(CAMINHO_SAIDA_TEMPLATE).format(inicio=args.ano_inicio, fim=ano_fim))
    caminho_saida.parent.mkdir(parents=True, exist_ok=True)
    tabela.to_csv(caminho_saida, index=False, encoding="utf-8")
    logger.info("Salvo: %s (%d meses, %d sem arquivo disponível)", caminho_saida, len(tabela), len(meses_sem_arquivo))

    metadados = {
        "fonte": "SIH (Sistema de Informação Hospitalar), grupo RD (AIH Reduzida) / DATASUS — FTP público ftp.datasus.gov.br/dissemin/publicos/SIHSUS/200801_/Dados/",
        "metodo": (
            "download direto dos .dbc por UF/ano/mês via FTP (não pela API de alto nível do pysus — "
            "ver docstring de scripts/utils/datasus_ftp.py: o catálogo do pysus está muito incompleto "
            "para SIH/RD, faltando a maioria dos meses), descompressão via pyreaddbc, leitura só das "
            "colunas necessárias (MUNIC_RES, DIAG_PRINC), filtro local pelos 6 primeiros dígitos do "
            "código IBGE (campo MUNIC_RES)"
        ),
        "codigo_ibge": args.codigo_ibge,
        "nome_municipio": nome_municipio,
        "uf": uf,
        "periodo_coberto": f"{args.ano_inicio}-{ano_fim}",
        "nivel_agregacao": "municipal (contagem por ano/mês) — NÃO espacializado por bairro/setor/microárea",
        "definicao_internacoes_respiratorias": "DIAG_PRINC (diagnóstico principal, CID-10) inicia com 'J' (capítulo X, doenças do aparelho respiratório, J00-J99) — mesmo critério do script do SIM",
        "meses_sem_arquivo_disponivel": meses_sem_arquivo,
        "sigilo_e_dado_ausente": (
            "microdados individuais do SIH, sem supressão por sigilo aplicada pela fonte; contagens "
            "pequenas (0, 1, 2...) são valores reais observados. arquivo_disponivel=False significa que "
            "o mês não foi encontrado no FTP no momento da coleta — nunca deve ser lido como zero"
        ),
        "data_processamento": datetime.now(timezone.utc).isoformat(),
    }
    caminho_metadados = caminho_saida.with_suffix(".json")
    caminho_metadados.write_text(json.dumps(metadados, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    logger.info("Metadados salvos em %s", caminho_metadados)


if __name__ == "__main__":
    main()
