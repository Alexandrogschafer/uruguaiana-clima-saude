"""Baixa notificações de arboviroses (dengue, chikungunya, zika) do SINAN
(Sistema de Informação de Agravos de Notificação / DATASUS) para um
município, por ano e mês. Gera:

    data/raw/arboviroses_sinan-datasus_{ano_inicio}-{ano_fim}_municipal.csv
    data/raw/arboviroses_sinan-datasus_{ano_inicio}-{ano_fim}_municipal.json
    data/raw/arboviroses_sinan_2026_preliminar.csv   (se houver ano corrente em PRELIM)
    data/raw/arboviroses_sinan_2026_preliminar.json

Série principal = só SINAN/DADOS/FINAIS (ano fechado)
-------------------------------------------------------
Para manter o mesmo critério de "ano completo" usado no SIH
(scripts/download/saude_sih_internacoes.py), a série principal usa só
anos consolidados em FINAIS — por padrão até o último ano em que TODOS os
agravos têm arquivo em FINAIS. Anos ainda em PRELIM (tipicamente o ano
corrente, com poucos meses publicados) NÃO entram na série principal:
saem num arquivo separado (`..._{ano}_preliminar.csv`), com o ano exato
detectado automaticamente a partir do que estiver em PRELIM no momento da
coleta — não fica hardcoded em "2026".

Fonte e método (ver docstring de scripts/utils/datasus_ftp.py para o porquê
de baixar direto do FTP em vez da API de alto nível do pysus)
-------------------------------------------------------------------------
FTP público do DATASUS, um arquivo .dbc por agravo/ano — NACIONAL, não por
UF — em `/dissemin/publicos/SINAN/DADOS/FINAIS/{AGRAVO}BR{aa}.dbc` (ano com
2 dígitos) para a série principal, e `.../PRELIM/{AGRAVO}BR{aa}.dbc` para o
arquivo preliminar separado. Cada arquivo nacional é descomprimido e,
diferente do script do SIM, lido só nas colunas necessárias (ID_MN_RESI,
DT_NOTIFIC, CLASSI_FIN) via `ler_dbc_como_dataframe(..., colunas=[...])` —
os arquivos de dengue chegam a >1,6 milhão de registros/ano nacionalmente
(ex. 2024, ano de epidemia: arquivo de 287MB compactado), então ler as
~120 colunas originais para descartar quase tudo seria um desperdício de
memória e tempo desnecessário.

Cobertura por agravo: dengue é de notificação compulsória desde antes de
2010; chikungunya e zika só passaram a ter formulário/arquivo SINAN
próprio a partir de 2014 e 2015, respectivamente (primeiros arquivos
`CHIKBR14.dbc` e `ZIKABR15.dbc` no FTP). Anos anteriores a isso NÃO são
"dado ausente" — são anteriores ao início da série disponível para aquele
agravo — e são marcados como tal nos metadados e no CSV (não confundir
com anos realmente sem notificação, que teriam o arquivo mas contagem 0).

Contagem = notificações (não só casos confirmados)
----------------------------------------------------
O campo CLASSI_FIN (classificação final: descartado/confirmado/
inconclusivo) muda de codificação entre agravos e ao longo dos anos no
SINAN; para não introduzir um recorte de "confirmado" sujeito a erro de
interpretação, este script conta TODAS as notificações registradas
(equivalente ao total do TABNET sem filtro de classificação), que é a
mesma convenção usada pela vigilância para acompanhar tendência. Fica
documentado nos metadados para quem quiser refinar depois.

Mês: extraído de DT_NOTIFIC (data de notificação, sempre preenchida) —
não DT_SIN_PRI (data dos primeiros sintomas), que é o padrão para curva
epidemiológica mas pode ficar em branco em notificações incompletas.

Meses futuros no arquivo PRELIM do ano corrente (correção 2026-08-09)
------------------------------------------------------------------------
`contar_por_mes` sempre gera 12 linhas/ano; para o ano corrente (PRELIM),
o arquivo do DATASUS só tem notificações até uma data de corte real
(ex. até 02/08 quando extraído em 09/08) — sem tratamento especial, os
meses depois dessa data apareciam como notificacoes=0, indistinguível de
um mês decorrido sem casos. Corrigido: `contar_por_mes` recebe a data
máxima de notificação do arquivo NACIONAL (antes do filtro por
município, calculada em `baixar_e_filtrar`) e marca meses inteiramente
posteriores a ela como notificacoes=NA / situacao="mes_ainda_nao_decorrido",
e o mês em que a data de corte cai no meio como
situacao="preliminar_mes_incompleto" (contagem real, mas mês ainda não
fechado no arquivo). Ver `situacoes_possiveis` no metadado gerado.

Ambiente: rodar com `.venv-pysus/bin/python` (ver
scripts/utils/datasus_ftp.py).

Uso:
    .venv-pysus/bin/python scripts/download/saude_sinan_arboviroses.py
    .venv-pysus/bin/python scripts/download/saude_sinan_arboviroses.py --codigo-ibge 4314902 --forcar
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

DIRETORIO_FINAIS = "/dissemin/publicos/SINAN/DADOS/FINAIS"
DIRETORIO_PRELIM = "/dissemin/publicos/SINAN/DADOS/PRELIM"
AGRAVOS = {"DENG": "dengue", "CHIK": "chikungunya", "ZIKA": "zika"}
COLUNAS_NECESSARIAS = ["ID_MN_RESI", "DT_NOTIFIC", "CLASSI_FIN"]
ANO_INICIO_DEFAULT = 2010
CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "raw" / "cache_datasus" / "sinan"
CAMINHO_SAIDA_TEMPLATE = Path(__file__).resolve().parents[2] / "data" / "raw" / "arboviroses_sinan-datasus_{inicio}-{fim}_municipal.csv"
CAMINHO_PRELIMINAR_TEMPLATE = Path(__file__).resolve().parents[2] / "data" / "raw" / "arboviroses_sinan_{ano}_preliminar.csv"


def mapear_arquivos_por_ano(diretorio: str, prefixo_agravo: str) -> dict[int, str]:
    """Mapeia ano (4 dígitos) -> nome do arquivo, para um único diretório (FINAIS ou PRELIM)."""
    padrao = re.compile(rf"^{prefixo_agravo}BR(\d{{2}})\.dbc$", re.IGNORECASE)
    mapa: dict[int, str] = {}
    for nome in listar_diretorio(diretorio):
        m = padrao.match(nome)
        if m:
            mapa[2000 + int(m.group(1))] = nome
    return mapa


def baixar_e_filtrar(diretorio: str, nome_arquivo: str, prefixo_agravo: str, codigo_municipio_6: str) -> tuple[pd.DataFrame, pd.Timestamp]:
    destino = CACHE_DIR / f"{Path(nome_arquivo).stem}.dbc"
    caminho_remoto = f"{diretorio}/{nome_arquivo}"
    baixado = baixar_dbc(caminho_remoto, destino)
    if baixado is None:
        raise RuntimeError(f"Arquivo listado no FTP mas falhou ao baixar: {caminho_remoto}")

    df = ler_dbc_como_dataframe(baixado, colunas=COLUNAS_NECESSARIAS)
    # data máxima de notificação no arquivo NACIONAL (antes do filtro por município) — usada
    # como "até quando este arquivo realmente foi consultado" para a série preliminar (ver
    # contar_por_mes): o arquivo PRELIM do ano corrente é um retrato parcial do DATASUS, então
    # meses depois dessa data não foram consultados de verdade, só ausentes do arquivo — não
    # dá pra usar a data máxima já filtrada por município pra isso, porque um município sem
    # notificação recente teria uma data máxima artificialmente mais cedo que o corte real do
    # arquivo (achado durante a investigação do arquivo preliminar 2026, ver ETAPA A do pedido
    # que motivou este ajuste)
    data_maxima_arquivo = pd.to_datetime(df["DT_NOTIFIC"], format="%Y%m%d", errors="coerce").max()

    # ID_MN_RESI no .dbc bruto vem em 6 dígitos, sem o dígito verificador do
    # código IBGE de 7 dígitos (diferente do parquet do catálogo do pysus,
    # que aplica add_dv=True por padrão e reintroduz o 7º dígito)
    df["ID_MN_RESI"] = df["ID_MN_RESI"].astype(str).str.strip()
    filtrado = df[df["ID_MN_RESI"] == codigo_municipio_6].copy()
    logger.info("%s / %s: %d notificações no Brasil, %d no município", prefixo_agravo, nome_arquivo, len(df), len(filtrado))
    return filtrado, data_maxima_arquivo


def contar_por_mes(
    df_municipio: pd.DataFrame, agravo: str, ano: int, codigo_ibge: str, nome_municipio: str,
    preliminar: bool, data_maxima_arquivo: pd.Timestamp | None = None,
) -> list[dict]:
    """Gera as 12 linhas mensais do ano. Para a série preliminar (`data_maxima_arquivo`
    informado), meses inteiramente posteriores à data máxima de notificação do arquivo NÃO
    foram consultados de verdade (arquivo do DATASUS ainda não chegou lá) — ficam com
    notificacoes=NA e situacao='mes_ainda_nao_decorrido', em vez de 0 (achado real: sem essa
    checagem, meses futuros do ano corrente apareciam como '0 notificações', indistinguível de
    um mês decorrido com zero casos). O mês que contém a própria data máxima (arquivo cortado
    no meio dele) é marcado 'preliminar_mes_incompleto' — a contagem é real, mas parcial."""
    dt = pd.to_datetime(df_municipio["DT_NOTIFIC"], format="%Y%m%d", errors="coerce")
    meses = dt.dt.month
    contagem = meses.value_counts().to_dict()
    linhas = []
    for mes in range(1, 13):
        situacao = "preliminar" if preliminar else "final"
        notificacoes: object = int(contagem.get(mes, 0))

        if preliminar and data_maxima_arquivo is not None and pd.notna(data_maxima_arquivo):
            inicio_mes = pd.Timestamp(year=ano, month=mes, day=1)
            fim_mes = inicio_mes + pd.offsets.MonthEnd(0)
            if inicio_mes > data_maxima_arquivo:
                notificacoes = pd.NA
                situacao = "mes_ainda_nao_decorrido"
            elif fim_mes > data_maxima_arquivo:
                situacao = "preliminar_mes_incompleto"

        linhas.append({
            "ano": ano,
            "mes": mes,
            "codigo_ibge": codigo_ibge,
            "nome_municipio": nome_municipio,
            "agravo": AGRAVOS[agravo],
            "notificacoes": notificacoes,
            "situacao": situacao,
        })
    return linhas


def linhas_serie_indisponivel(agravo: str, ano: int, codigo_ibge: str, nome_municipio: str, motivo: str) -> list[dict]:
    return [{
        "ano": ano,
        "mes": mes,
        "codigo_ibge": codigo_ibge,
        "nome_municipio": nome_municipio,
        "agravo": AGRAVOS[agravo],
        "notificacoes": pd.NA,
        "situacao": motivo,
    } for mes in range(1, 13)]


def processar_serie(mapas: dict[str, dict[int, str]], ano_inicio: int, ano_fim: int, diretorio: str, codigo_ibge: str, codigo_municipio_6: str, nome_municipio: str, preliminar: bool) -> tuple[pd.DataFrame, dict, dict]:
    """Baixa e agrega um intervalo de anos de um único diretório (FINAIS ou PRELIM).

    Retorna (tabela, limitacoes, datas_maximas_arquivo) — o terceiro item só é preenchido
    quando `preliminar=True` (data máxima de notificação encontrada no arquivo nacional bruto
    de cada agravo/ano, usada em contar_por_mes para não confundir "mês futuro, não consultado"
    com "mês decorrido, zero notificações")."""
    todas_linhas = []
    limitacoes = {agravo: {} for agravo in AGRAVOS}
    datas_maximas_arquivo: dict[str, dict[int, str | None]] = {agravo: {} for agravo in AGRAVOS}
    for agravo, mapa in mapas.items():
        primeiro_ano_disponivel = min(mapa) if mapa else None
        for ano in range(ano_inicio, ano_fim + 1):
            if ano not in mapa:
                if primeiro_ano_disponivel and ano < primeiro_ano_disponivel:
                    motivo = f"anterior_ao_inicio_da_serie_disponivel_no_sinan (primeiro arquivo: {primeiro_ano_disponivel})"
                else:
                    motivo = "arquivo_nao_encontrado_no_ftp"
                todas_linhas.extend(linhas_serie_indisponivel(agravo, ano, codigo_ibge, nome_municipio, motivo))
                limitacoes[agravo][ano] = motivo
                continue

            df_municipio, data_maxima_arquivo = baixar_e_filtrar(diretorio, mapa[ano], agravo, codigo_municipio_6)
            todas_linhas.extend(contar_por_mes(
                df_municipio, agravo, ano, codigo_ibge, nome_municipio, preliminar,
                data_maxima_arquivo if preliminar else None,
            ))
            if preliminar:
                datas_maximas_arquivo[agravo][ano] = (
                    data_maxima_arquivo.date().isoformat() if pd.notna(data_maxima_arquivo) else None
                )

    tabela = pd.DataFrame(todas_linhas).sort_values(["agravo", "ano", "mes"]).reset_index(drop=True)
    return tabela, limitacoes, datas_maximas_arquivo


def main() -> None:
    parser = argparse.ArgumentParser(description="Baixa notificações de arboviroses do SINAN/DATASUS por ano/mês para um município.")
    parser.add_argument("--codigo-ibge", default=CODIGO_IBGE_DEFAULT)
    parser.add_argument("--ano-inicio", type=int, default=ANO_INICIO_DEFAULT)
    parser.add_argument("--ano-fim", type=int, default=None, help="Default: último ano em que todos os agravos têm arquivo consolidado (FINAIS)")
    parser.add_argument("--forcar", action="store_true")
    args = parser.parse_args()

    if args.forcar:
        shutil.rmtree(CACHE_DIR, ignore_errors=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    nome_municipio, uf = obter_uf_sigla(args.codigo_ibge)
    codigo_municipio_6 = codigo_municipio_6_digitos(args.codigo_ibge)
    logger.info("Município: %s (%s) — código IBGE %s / 6 dígitos %s", nome_municipio, uf, args.codigo_ibge, codigo_municipio_6)

    mapas_finais = {agravo: mapear_arquivos_por_ano(DIRETORIO_FINAIS, agravo) for agravo in AGRAVOS}
    # dengue é a referência de "ano completo": está disponível todo ano desde antes de 2010,
    # então o último ano dela em FINAIS é o mesmo critério de "ano fechado" usado no SIH
    ano_fim = args.ano_fim or max(mapas_finais["DENG"])
    logger.info("Série principal (FINAIS): %d-%d", args.ano_inicio, ano_fim)

    tabela, limitacoes, _ = processar_serie(
        mapas_finais, args.ano_inicio, ano_fim, DIRETORIO_FINAIS,
        args.codigo_ibge, codigo_municipio_6, nome_municipio, preliminar=False,
    )
    caminho_saida = Path(str(CAMINHO_SAIDA_TEMPLATE).format(inicio=args.ano_inicio, fim=ano_fim))
    caminho_saida.parent.mkdir(parents=True, exist_ok=True)
    tabela.to_csv(caminho_saida, index=False, encoding="utf-8")
    logger.info("Salvo: %s (%d linhas)", caminho_saida, len(tabela))

    metadados = {
        "fonte": "SINAN (Sistema de Informação de Agravos de Notificação) / DATASUS — FTP público ftp.datasus.gov.br/dissemin/publicos/SINAN/DADOS/FINAIS/",
        "metodo": (
            "download direto dos .dbc nacionais por agravo/ano via FTP (não pela API de alto nível do "
            "pysus — ver docstring de scripts/utils/datasus_ftp.py: o catálogo do pysus publica cada ano "
            "em 2 arquivos duplicados com as mesmas linhas, e a função de conveniência concatena os dois, "
            "dobrando as contagens), descompressão via pyreaddbc, leitura só das colunas necessárias "
            "(ID_MN_RESI, DT_NOTIFIC, CLASSI_FIN), filtro local por ID_MN_RESI = código IBGE de 6 dígitos "
            "(sem o dígito verificador — assim que o campo vem no .dbc bruto; o parquet do catálogo do "
            "pysus reintroduz o 7º dígito via um parâmetro add_dv que não é aplicado aqui)"
        ),
        "codigo_ibge": args.codigo_ibge,
        "nome_municipio": nome_municipio,
        "uf": uf,
        "periodo_coberto": f"{args.ano_inicio}-{ano_fim}",
        "nivel_agregacao": "municipal (contagem por ano/mês) — NÃO espacializado por bairro/setor/microárea",
        "escopo": (
            "só anos consolidados em SINAN/DADOS/FINAIS (mesmo critério de 'ano completo' usado no SIH); "
            "o ano corrente, ainda em SINAN/DADOS/PRELIM, fica de fora desta série e sai separado em "
            "arboviroses_sinan_{ano}_preliminar.csv — ver esse arquivo para dado parcial do ano em curso"
        ),
        "definicao_contagem": (
            "notificacoes = todos os registros do SINAN para o agravo/ano com ID_MN_RESI do município "
            "(inclui suspeitos, descartados e confirmados) — NÃO filtrado por CLASSI_FIN (classificação "
            "final), porque a codificação desse campo muda entre agravos e ao longo dos anos no SINAN; "
            "mês extraído de DT_NOTIFIC (data de notificação), não da data de primeiros sintomas"
        ),
        "limitacoes_por_agravo": limitacoes,
        "sigilo_e_dado_ausente": (
            "microdados individuais do SINAN, sem supressão por sigilo aplicada pela fonte; contagens "
            "pequenas (0, 1, 2...) são valores reais observados. notificacoes=NA com situacao != 'final' "
            "significa que não há arquivo para aquele agravo/ano (ver limitacoes_por_agravo) — nunca deve "
            "ser lido como zero"
        ),
        "data_processamento": datetime.now(timezone.utc).isoformat(),
    }
    caminho_metadados = caminho_saida.with_suffix(".json")
    caminho_metadados.write_text(json.dumps(metadados, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    logger.info("Metadados salvos em %s", caminho_metadados)

    # série preliminar separada: anos que já aparecem em PRELIM além do que a série principal cobriu
    mapas_prelim = {agravo: mapear_arquivos_por_ano(DIRETORIO_PRELIM, agravo) for agravo in AGRAVOS}
    anos_preliminares = sorted({ano for mapa in mapas_prelim.values() for ano in mapa if ano > ano_fim})
    if not anos_preliminares:
        logger.info("Nenhum ano preliminar (PRELIM) além da série principal — nada a gerar.")
        return

    for ano_prelim in anos_preliminares:
        tabela_prelim, limitacoes_prelim, datas_maximas_prelim = processar_serie(
            mapas_prelim, ano_prelim, ano_prelim, DIRETORIO_PRELIM,
            args.codigo_ibge, codigo_municipio_6, nome_municipio, preliminar=True,
        )
        caminho_prelim = Path(str(CAMINHO_PRELIMINAR_TEMPLATE).format(ano=ano_prelim))
        tabela_prelim.to_csv(caminho_prelim, index=False, encoding="utf-8")
        logger.info("Salvo (preliminar): %s (%d linhas)", caminho_prelim, len(tabela_prelim))

        metadados_prelim = {
            "fonte": "SINAN / DATASUS — FTP público ftp.datasus.gov.br/dissemin/publicos/SINAN/DADOS/PRELIM/",
            "aviso": (
                f"dado PRELIMINAR do ano {ano_prelim} — arquivo ainda não consolidado pelo DATASUS "
                "(normalmente cobre só os primeiros meses do ano em curso e está sujeito a revisão, "
                "inclusão de notificações atrasadas e reclassificação de casos); NÃO faz parte da série "
                "principal (arboviroses_sinan-datasus_*_municipal.csv) para não misturar ano fechado com "
                "ano em andamento. Contagens de meses passados são reais (checadas contra o microdado "
                "bruto), mas ainda sujeitas a revisão por atraso de notificação — comum em vigilância "
                "epidemiológica — não tratar como contagem final."
            ),
            "codigo_ibge": args.codigo_ibge,
            "nome_municipio": nome_municipio,
            "uf": uf,
            "ano": ano_prelim,
            "nivel_agregacao": "municipal (contagem por mês) — NÃO espacializado por bairro/setor/microárea",
            "definicao_contagem": "mesma da série principal (ver arboviroses_sinan-datasus_*_municipal.json): todas as notificações, mês por DT_NOTIFIC",
            "situacoes_possiveis": {
                "preliminar": "mês inteiramente coberto pelo arquivo PRELIM na data de extração — contagem real, mas sujeita a revisão/atraso de notificação",
                "preliminar_mes_incompleto": (
                    "mês em que a data máxima de notificação do arquivo cai no meio dele — contagem real "
                    "do que já foi notificado até ali, mas o mês ainda não fechou dentro do arquivo (mais "
                    "notificações desse mês provavelmente ainda vão aparecer numa atualização futura do "
                    "PRELIM)"
                ),
                "mes_ainda_nao_decorrido": (
                    "mês inteiramente posterior à data máxima de notificação do arquivo — NÃO foi "
                    "consultado (não existe no arquivo PRELIM ainda), notificacoes=null; ANTES desta "
                    "correção esses meses apareciam incorretamente como notificacoes=0, indistinguível de "
                    "um mês decorrido sem casos (achado durante auditoria de qualidade de dados, "
                    "2026-08-09) — não interpretar null como zero"
                ),
            },
            "limitacoes_por_agravo": limitacoes_prelim,
            "data_maxima_notificacao_no_arquivo_por_agravo": datas_maximas_prelim,
            "data_processamento": datetime.now(timezone.utc).isoformat(),
        }
        caminho_metadados_prelim = caminho_prelim.with_suffix(".json")
        caminho_metadados_prelim.write_text(json.dumps(metadados_prelim, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        logger.info("Metadados (preliminar) salvos em %s", caminho_metadados_prelim)


if __name__ == "__main__":
    main()
