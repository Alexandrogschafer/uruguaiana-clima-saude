"""Baixa óbitos do SIM (Sistema de Informação sobre Mortalidade / DATASUS)
para um município, agrupados por causa (CID-10) e ano. Gera:

    data/raw/obitos_sim-datasus_{ano_inicio}-{ano_fim}_municipal.csv
    data/raw/obitos_sim-datasus_{ano_inicio}-{ano_fim}_municipal.json

Fonte e método (ver docstring de scripts/utils/datasus_ftp.py para o porquê
de baixar direto do FTP em vez da API de alto nível do pysus)
-------------------------------------------------------------------------
FTP público do DATASUS, um arquivo .dbc por UF/ano em
`/dissemin/publicos/SIM/CID10/DORES/DO{UF}{ano}.dbc`. Cada .dbc é
descomprimido e lido via `pysus.api.extensions` (que usa `pyreaddbc`) e
filtrado localmente pelos 6 primeiros dígitos do código IBGE do
município (campo CODMUNRES).

Nível de agregação: MUNICIPAL — contagem de óbitos por ano e grupo de
causa, não espacializado por bairro/setor (o campo do SIM é o município
de residência, não endereço).

Grupos de causa (CID-10, código em CAUSABAS, sem ponto)
--------------------------------------------------------
- respiratorias: capítulo X inteiro, prefixo "J" (J00-J99).
- calor_extremo: X30 (exposição a calor natural excessivo) + T67
  (efeitos do calor e da luz).
- afogamento_enchente: W65-W74 (afogamento e submersão acidentais) + X38
  (vítima de inundação).
Escolha de códigos documentada nos metadados junto com o CSV; é uma
aproximação (mortalidade "relacionada" a esses agravos é mais ampla que
a causa básica de óbito, mas causa básica é o único campo estruturado e
comparável ano a ano disponível no SIM).

Sigilo / dado ausente vs. zero
-------------------------------
O SIM distribui microdados individuais (registro a registro), diferente
do TABNET (tabulação pronta), que suprime células com poucos casos por
sigilo. Aqui não há supressão aplicada pela fonte: uma contagem pequena
(0, 1, 2...) é um valor real observado. "Sem dado disponível" (NA no
CSV) significa apenas que o arquivo daquele ano não foi encontrado no
FTP (ano ainda não consolidado/publicado) — nunca é usado para
representar zero.

Ambiente: precisa rodar com o Python do `.venv-pysus/` (ver
scripts/utils/datasus_ftp.py) — não é compatível com o `.venv/` padrão
do projeto (Python 3.14).

Uso:
    .venv-pysus/bin/python scripts/download/saude_sim_obitos.py
    .venv-pysus/bin/python scripts/download/saude_sim_obitos.py --codigo-ibge 4314902 --forcar
"""

import argparse
import json
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

sys.path.append(str(Path(__file__).resolve().parents[1] / "utils"))
from datasus_ftp import (  # noqa: E402
    CODIGO_IBGE_DEFAULT,
    codigo_municipio_6_digitos,
    baixar_dbc,
    ler_dbc_como_dataframe,
    listar_diretorio,
    obter_uf_sigla,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

DIRETORIO_REMOTO_SIM = "/dissemin/publicos/SIM/CID10/DORES"
ANO_INICIO_DEFAULT = 2010
CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "raw" / "cache_datasus" / "sim"
CAMINHO_SAIDA_TEMPLATE = Path(__file__).resolve().parents[2] / "data" / "raw" / "obitos_sim-datasus_{inicio}-{fim}_municipal.csv"

GRUPOS_CID10 = {
    "obitos_respiratorios": lambda causa: causa.startswith("J"),
    "obitos_calor_extremo": lambda causa: causa.startswith("X30") or causa.startswith("T67"),
    "obitos_afogamento_enchente": lambda causa: causa[:3] in {
        "W65", "W66", "W67", "W68", "W69", "W70", "W71", "W72", "W73", "W74",
    } or causa.startswith("X38"),
}


def mapear_arquivos_por_ano(uf: str) -> dict[int, str]:
    """Lista o diretório do SIM uma vez e mapeia ano -> nome exato do arquivo da UF.

    A extensão varia de capitalização entre arquivos (ex. "DORS2010.DBC"
    vs "DORS2013.dbc"), por isso listamos em vez de montar o nome no chute.
    """
    nomes = listar_diretorio(DIRETORIO_REMOTO_SIM)
    padrao = re.compile(rf"^DO{uf}(\d{{4}})\.dbc$", re.IGNORECASE)
    return {int(m.group(1)): nome for nome in nomes if (m := padrao.match(nome))}


def baixar_e_filtrar_ano(nome_arquivo: str, ano: int, codigo_municipio_6: str) -> pd.DataFrame:
    """Baixa o .dbc do ano (cacheado) e retorna as linhas do município."""
    destino = CACHE_DIR / f"{Path(nome_arquivo).stem}.dbc"
    caminho_remoto = f"{DIRETORIO_REMOTO_SIM}/{nome_arquivo}"

    baixado = baixar_dbc(caminho_remoto, destino)
    if baixado is None:
        raise RuntimeError(f"Arquivo listado no FTP mas falhou ao baixar: {caminho_remoto}")

    df = ler_dbc_como_dataframe(baixado)
    df["CODMUNRES"] = df["CODMUNRES"].astype(str).str.strip()
    filtrado = df[df["CODMUNRES"] == codigo_municipio_6].copy()
    logger.info("Ano %d: %d óbitos na UF, %d no município", ano, len(df), len(filtrado))
    return filtrado


def contar_por_grupo(df_municipio: pd.DataFrame, ano: int, codigo_ibge: str, nome_municipio: str) -> dict:
    causas = df_municipio["CAUSABAS"].astype(str).str.strip().str.upper()
    linha = {
        "ano": ano,
        "codigo_ibge": codigo_ibge,
        "nome_municipio": nome_municipio,
        "obitos_total": len(df_municipio),
        "arquivo_disponivel": True,
    }
    for nome_grupo, teste in GRUPOS_CID10.items():
        linha[nome_grupo] = int(causas.apply(teste).sum())
    return linha


def linha_sem_dado(ano: int, codigo_ibge: str, nome_municipio: str) -> dict:
    linha = {
        "ano": ano,
        "codigo_ibge": codigo_ibge,
        "nome_municipio": nome_municipio,
        "obitos_total": pd.NA,
        "arquivo_disponivel": False,
    }
    for nome_grupo in GRUPOS_CID10:
        linha[nome_grupo] = pd.NA
    return linha


def main() -> None:
    parser = argparse.ArgumentParser(description="Baixa óbitos do SIM/DATASUS por causa e ano para um município.")
    parser.add_argument("--codigo-ibge", default=CODIGO_IBGE_DEFAULT, help="Código IBGE do município (7 dígitos)")
    parser.add_argument("--ano-inicio", type=int, default=ANO_INICIO_DEFAULT)
    parser.add_argument("--ano-fim", type=int, default=None, help="Default: último ano disponível no FTP")
    parser.add_argument("--forcar", action="store_true", help="Ignora o cache de .dbc e rebaixa tudo")
    args = parser.parse_args()

    if args.forcar:
        import shutil
        shutil.rmtree(CACHE_DIR, ignore_errors=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    nome_municipio, uf = obter_uf_sigla(args.codigo_ibge)
    codigo_municipio_6 = codigo_municipio_6_digitos(args.codigo_ibge)
    logger.info("Município: %s (%s) — código IBGE %s / 6 dígitos %s", nome_municipio, uf, args.codigo_ibge, codigo_municipio_6)

    arquivos_por_ano = mapear_arquivos_por_ano(uf)
    ano_fim = args.ano_fim or max(a for a in arquivos_por_ano if a >= args.ano_inicio)
    logger.info("Janela: %d-%d", args.ano_inicio, ano_fim)

    linhas = []
    anos_sem_arquivo = []
    for ano in range(args.ano_inicio, ano_fim + 1):
        nome_arquivo = arquivos_por_ano.get(ano)
        if nome_arquivo is None:
            anos_sem_arquivo.append(ano)
            linhas.append(linha_sem_dado(ano, args.codigo_ibge, nome_municipio))
            continue
        df_municipio = baixar_e_filtrar_ano(nome_arquivo, ano, codigo_municipio_6)
        linhas.append(contar_por_grupo(df_municipio, ano, args.codigo_ibge, nome_municipio))

    tabela = pd.DataFrame(linhas)
    caminho_saida = Path(str(CAMINHO_SAIDA_TEMPLATE).format(inicio=args.ano_inicio, fim=ano_fim))
    caminho_saida.parent.mkdir(parents=True, exist_ok=True)
    tabela.to_csv(caminho_saida, index=False, encoding="utf-8")
    logger.info("Salvo: %s (%d anos, %d sem arquivo disponível)", caminho_saida, len(tabela), len(anos_sem_arquivo))

    metadados = {
        "fonte": "SIM (Sistema de Informação sobre Mortalidade) / DATASUS — FTP público ftp.datasus.gov.br/dissemin/publicos/SIM/CID10/DORES/",
        "metodo": (
            "download direto dos .dbc por UF/ano via FTP (não pela API de alto nível do pysus — "
            "ver docstring de scripts/utils/datasus_ftp.py para as limitações encontradas nela), "
            "descompressão via pyreaddbc (dependência do pysus), filtro local pelos 6 primeiros "
            "dígitos do código IBGE (campo CODMUNRES)"
        ),
        "codigo_ibge": args.codigo_ibge,
        "nome_municipio": nome_municipio,
        "uf": uf,
        "periodo_coberto": f"{args.ano_inicio}-{ano_fim}",
        "nivel_agregacao": "municipal (contagem por ano) — NÃO espacializado por bairro/setor/microárea; SIM registra só o município de residência",
        "anos_sem_arquivo_disponivel": anos_sem_arquivo,
        "grupos_cid10": {
            "obitos_respiratorios": "CAUSABAS inicia com 'J' (capítulo X da CID-10, doenças do aparelho respiratório, J00-J99)",
            "obitos_calor_extremo": "CAUSABAS = X30 (exposição a calor natural excessivo) ou T67 (efeitos do calor e da luz)",
            "obitos_afogamento_enchente": "CAUSABAS em W65-W74 (afogamento e submersão acidentais) ou X38 (vítima de inundação)",
            "_observacao": "aproximação por causa básica de óbito (CAUSABAS); mortalidade associada a esses agravos pode ter causa básica registrada de outra forma (ex. complicação secundária) e não ser capturada aqui",
        },
        "sigilo_e_dado_ausente": (
            "microdados individuais do SIM, sem supressão por sigilo aplicada pela fonte (diferente do "
            "TABNET, que oculta células com poucos casos); contagens pequenas (0, 1, 2...) são valores "
            "reais observados. 'arquivo_disponivel'=False (obitos_total e colunas de grupo = NA) significa "
            "que o ano ainda não tem arquivo publicado no FTP no momento da coleta — não deve ser lido como zero"
        ),
        "data_processamento": datetime.now(timezone.utc).isoformat(),
    }
    caminho_metadados = caminho_saida.with_suffix(".json")
    caminho_metadados.write_text(json.dumps(metadados, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    logger.info("Metadados salvos em %s", caminho_metadados)


if __name__ == "__main__":
    main()
