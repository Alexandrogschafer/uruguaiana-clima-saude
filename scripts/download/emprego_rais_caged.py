"""Baixa indicadores de emprego formal (RAIS e Novo CAGED, Ministério do
Trabalho e Emprego) para um município. Gera:

    data/raw/caged-movimentacao_mte_2020-{ultimo_mes}_municipal.csv
    data/raw/caged-movimentacao_mte_2020-{ultimo_mes}_municipal.json
    data/raw/rais-vinculos_mte_{ano_rais}_municipal.csv
    data/raw/rais-vinculos_mte_{ano_rais}_municipal.json

Decisão de escopo (confirmada com o usuário, 2026-08-11)
------------------------------------------------------------
RAIS/CAGED NÃO têm API agregada por município (diferente de PIB/PAM/IDHM,
que são SIDRA) — o MTE só publica MICRODADOS NACIONAIS via FTP
(ftp.mtps.gov.br/pdet/microdados/), sem endpoint agregado. Indicador de
CONTEXTO municipal único (mesma decisão de PIB/PAM/PPM/IDHM), sem virar
camada espacial (não há geolocalização de estabelecimento na versão
pública).

Duas fontes, dois recortes de escopo diferentes (decisão do usuário):
1. Novo CAGED — série MENSAL COMPLETA desde 2020 (arquivos leves, ~45MB/
   mês comprimido): admissões, desligamentos e saldo por seção CNAE.
   Cobre o "fluxo" do mercado de trabalho formal.
2. RAIS — só o ANO MAIS RECENTE disponível: estoque de vínculos ativos
   por seção CNAE em 31/12. Arquivo de estabelecimentos nacional é
   gigante (~3GB descomprimido/ano) — baixar uma série histórica completa
   seria desproporcional; um ano dá a "foto" da composição setorial atual
   do emprego formal, que é o que a checagem de consistência com PIB/PAM
   pedida pelo usuário precisa.

QUEBRA METODOLÓGICA 2020 (Novo CAGED substituiu o CAGED antigo)
------------------------------------------------------------------
Confirmado na própria estrutura do FTP: a partir de 2020 o layout mudou
completamente (novo conjunto de colunas, nova forma de apuração — o Novo
CAGED usa e-Social/eSocial como fonte primária, diferente do formulário
declaratório do CAGED antigo). O CAGED anterior a 2020 fica FORA DE
ESCOPO nesta coleta (não baixado) — mesmo espírito da quebra já registrada
entre os Censos 2000/2010/2022 (não interpolar nem comparar diretamente
através da quebra sem ressalva). Documentado no metadado.

RAIS: estabelecimentos, não vínculos individuais
------------------------------------------------------------
Usa o arquivo RAIS_ESTAB_PUB (nível de estabelecimento, com Qtd Vínculos
Ativos já agregada por CNPJ/CEI) em vez dos arquivos RAIS_VINC_PUB
(nível de vínculo individual, por região do país, 10-20x maiores) —
suficiente para "vínculos formais por setor no município", sem precisar
baixar dados de trabalhador individual. RAIS_ESTAB_PUB tem só o código
CNAE (coluna chamada "Classe", mas com 7 dígitos — na prática subclasse
—, confirmado inspecionando o arquivo real), não a Seção (letra)
diretamente — mapeado para seção pelos 2 primeiros dígitos (divisão) via
API de CNAE do IBGE (servicodados.ibge.gov.br/api/v2/cnae/divisoes), sem
hardcode da tabela de correspondência.

Método (FTP público, sem autenticação, arquivos .7z)
---------------------------------------------------------
ftp.mtps.gov.br/pdet/microdados/ — sem login (usuário anônimo). Anos/
meses disponíveis descobertos dinamicamente listando o FTP (não
hardcoded). Arquivos vêm comprimidos em .7z (não .zip) — usa py7zr.
Filtro por município feito localmente após descompactar cada arquivo
(streaming em chunks via pandas, nunca lendo o CSV inteiro de uma vez na
memória) — o FTP não permite filtro remoto.

Uso:
    python scripts/download/emprego_rais_caged.py
    python scripts/download/emprego_rais_caged.py --codigo-ibge 4314902 --forcar
"""

import argparse
import json
import logging
import tempfile
import time
import urllib.request
from datetime import date, datetime, timezone
from ftplib import FTP
from io import BytesIO
from pathlib import Path

import pandas as pd
import py7zr
import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

RAIZ = Path(__file__).resolve().parents[2]
CODIGO_IBGE_DEFAULT = "4322400"  # Uruguaiana, RS
ANO_INICIO_CAGED = 2020

FTP_HOST = "ftp.mtps.gov.br"
CAMINHO_FTP_CAGED = "/pdet/microdados/NOVO CAGED"
CAMINHO_FTP_RAIS = "/pdet/microdados/RAIS"

CACHE_DIR_CAGED = RAIZ / "data" / "raw" / "cache_caged"
CAMINHO_MUNICIPIO_IBGE = "https://servicodados.ibge.gov.br/api/v1/localidades/municipios/{codigo}"
URL_CNAE_DIVISOES = "https://servicodados.ibge.gov.br/api/v2/cnae/divisoes"

N_TENTATIVAS = 3
BACKOFF_BASE_S = 3.0


def _ftp_conectar() -> FTP:
    ftp = FTP(FTP_HOST, timeout=60, encoding="latin-1")
    ftp.login()
    return ftp


def listar_ftp(caminho: str) -> list[str]:
    ftp = _ftp_conectar()
    try:
        ftp.cwd(caminho)
        return ftp.nlst()
    finally:
        ftp.quit()


def baixar_ftp_com_retry(caminho_remoto: str) -> bytes:
    url = "ftp://" + FTP_HOST + urllib.request.quote(caminho_remoto)
    ultimo_erro = None
    for tentativa in range(1, N_TENTATIVAS + 1):
        try:
            with urllib.request.urlopen(url, timeout=180) as resposta:
                return resposta.read()
        except Exception as erro:  # noqa: BLE001
            ultimo_erro = erro
            if tentativa < N_TENTATIVAS:
                espera = BACKOFF_BASE_S * tentativa
                logger.warning("Falha ao baixar %s (tentativa %d/%d): %s — nova tentativa em %.0fs", caminho_remoto, tentativa, N_TENTATIVAS, erro, espera)
                time.sleep(espera)
    raise RuntimeError(f"Falha ao baixar {caminho_remoto} após {N_TENTATIVAS} tentativas: {ultimo_erro}")


def extrair_7z_para_dataframe_filtrado(conteudo_7z: bytes, coluna_municipio: str, codigo_municipio_6: str, colunas_uteis: list[str] | None = None, encoding: str = "utf-8", sep: str = ";") -> pd.DataFrame:
    """py7zr 1.1.3 não tem leitura em memória (sem .read()/.readall()) — extrai para um
    diretório temporário (apagado ao final) e lê o .txt de lá em streaming.

    Encoding/separador variam por arquivo do MTE: CAGEDMOV vem em UTF-8 com ';'; o
    RAIS_ESTAB_PUB do ano corrente (não-Legado) vem em latin-1 com ',' e cabeçalho
    entre aspas com sufixo " - Código" nas colunas categóricas (confirmado inspecionando
    o arquivo real — layout diferente do RAIS_ESTAB_PUB de anos "Legado", que usa ';')."""
    with tempfile.TemporaryDirectory() as tmpdir:
        with py7zr.SevenZipFile(BytesIO(conteudo_7z), mode="r") as z:
            z.extractall(path=tmpdir)
            nome_txt = z.getnames()[0]
        caminho_txt = Path(tmpdir) / nome_txt

        partes = []
        leitor = pd.read_csv(caminho_txt, sep=sep, chunksize=300_000, dtype=str, encoding=encoding)
        for bloco in leitor:
            filtrado = bloco[bloco[coluna_municipio] == codigo_municipio_6]
            if not filtrado.empty:
                partes.append(filtrado[colunas_uteis] if colunas_uteis else filtrado)
    return pd.concat(partes, ignore_index=True) if partes else pd.DataFrame(columns=colunas_uteis or [])


def obter_municipio_uf(codigo_ibge: str) -> tuple[str, str]:
    resposta = requests.get(CAMINHO_MUNICIPIO_IBGE.format(codigo=codigo_ibge), timeout=30)
    resposta.raise_for_status()
    dados = resposta.json()
    return dados["nome"], dados["microrregiao"]["mesorregiao"]["UF"]["sigla"]


def descobrir_meses_caged_disponiveis() -> list[str]:
    anos = sorted(a for a in listar_ftp(CAMINHO_FTP_CAGED) if a.isdigit() and int(a) >= ANO_INICIO_CAGED)
    meses_disponiveis = []
    for ano in anos:
        meses = sorted(m for m in listar_ftp(f"{CAMINHO_FTP_CAGED}/{ano}") if m.isdigit())
        meses_disponiveis.extend(meses)
    return meses_disponiveis


def baixar_serie_caged(codigo_municipio_6: str) -> pd.DataFrame:
    CACHE_DIR_CAGED.mkdir(parents=True, exist_ok=True)
    colunas_uteis = ["competênciamov", "município", "seção", "saldomovimentação"]

    meses = descobrir_meses_caged_disponiveis()
    logger.info("Novo CAGED: %d meses disponíveis (%s a %s).", len(meses), meses[0], meses[-1])

    for mes in meses:
        caminho_cache = CACHE_DIR_CAGED / f"{mes}.csv"
        if caminho_cache.exists():
            continue
        ano = mes[:4]
        caminho_remoto = f"{CAMINHO_FTP_CAGED}/{ano}/{mes}/CAGEDMOV{mes}.7z"
        logger.info("Baixando %s...", caminho_remoto)
        conteudo = baixar_ftp_com_retry(caminho_remoto)
        df_mes = extrair_7z_para_dataframe_filtrado(conteudo, "município", codigo_municipio_6, colunas_uteis)
        df_mes.to_csv(caminho_cache, index=False, encoding="utf-8")
        logger.info("  -> %d movimentações em %s", len(df_mes), mes)

    partes = [pd.read_csv(CACHE_DIR_CAGED / f"{mes}.csv", dtype=str) for mes in meses if (CACHE_DIR_CAGED / f"{mes}.csv").exists()]
    bruto = pd.concat(partes, ignore_index=True)
    bruto["saldomovimentação"] = bruto["saldomovimentação"].astype(int)
    bruto = bruto.rename(columns={"competênciamov": "competencia", "seção": "secao_cnae"})

    resumo = (
        bruto.groupby(["competencia", "secao_cnae"])["saldomovimentação"]
        .agg(admissoes=lambda s: (s == 1).sum(), desligamentos=lambda s: (s == -1).sum(), saldo=lambda s: s.sum())
        .reset_index()
    )
    return resumo.sort_values(["competencia", "secao_cnae"]).reset_index(drop=True)


def obter_mapa_divisao_cnae_para_secao() -> dict[str, str]:
    """Seção CNAE depende só da DIVISÃO (2 primeiros dígitos do código, qualquer nível
    classe/subclasse) — usa o endpoint de divisões (87 itens) em vez do de classes
    (mais granular e desnecessário aqui), evitando depender do formato exato
    (classe de 5 dígitos vs subclasse de 7) que a coluna da RAIS realmente usa."""
    resposta = requests.get(URL_CNAE_DIVISOES, timeout=60)
    resposta.raise_for_status()
    return {item["id"]: item["secao"]["id"] for item in resposta.json()}


def baixar_rais(codigo_municipio_6: str) -> tuple[pd.DataFrame, int, int]:
    anos = sorted(a for a in listar_ftp(CAMINHO_FTP_RAIS) if a.isdigit())
    ano_rais = int(anos[-1])
    caminho_remoto = f"{CAMINHO_FTP_RAIS}/{ano_rais}/RAIS_ESTAB_PUB.7z"
    logger.info("RAIS %d: baixando %s (~130-150MB comprimido)...", ano_rais, caminho_remoto)
    conteudo = baixar_ftp_com_retry(caminho_remoto)

    coluna_cnae = "CNAE 2.0 Classe - Código"
    colunas_uteis = ["Município - Código", coluna_cnae, "Qtd Vínculos Ativos"]
    df = extrair_7z_para_dataframe_filtrado(
        conteudo, "Município - Código", codigo_municipio_6, colunas_uteis, encoding="latin-1", sep=","
    )
    logger.info("RAIS %d: %d estabelecimentos encontrados para o município.", ano_rais, len(df))

    df["Qtd Vínculos Ativos"] = pd.to_numeric(df["Qtd Vínculos Ativos"], errors="coerce").fillna(0).astype(int)

    # a coluna, apesar do nome "Classe", traz o código com 7 dígitos (subclasse) neste
    # layout — os 2 primeiros dígitos (divisão) bastam para achar a seção
    df["divisao_cnae"] = df[coluna_cnae].astype(str).str.strip().str.zfill(7).str[:2]
    mapa_divisao = obter_mapa_divisao_cnae_para_secao()
    df["secao_cnae"] = df["divisao_cnae"].map(mapa_divisao)
    n_sem_secao = df["secao_cnae"].isna().sum()

    resumo = df.groupby("secao_cnae", dropna=False)["Qtd Vínculos Ativos"].sum().reset_index()
    resumo.columns = ["secao_cnae", "vinculos_ativos"]
    return resumo.sort_values("vinculos_ativos", ascending=False).reset_index(drop=True), ano_rais, n_sem_secao


def main() -> None:
    parser = argparse.ArgumentParser(description="Baixa indicadores de emprego formal (RAIS/Novo CAGED, MTE) para um município.")
    parser.add_argument("--codigo-ibge", default=CODIGO_IBGE_DEFAULT, help="Código IBGE do município (default: Uruguaiana/RS)")
    parser.add_argument("--forcar", action="store_true", help="Ignora cache/arquivos já existentes e baixa tudo de novo")
    args = parser.parse_args()

    if args.forcar:
        import shutil
        shutil.rmtree(CACHE_DIR_CAGED, ignore_errors=True)

    codigo_municipio_6 = args.codigo_ibge[:6]
    nome_municipio, uf_sigla = obter_municipio_uf(args.codigo_ibge)
    logger.info("Município: %s (%s) — código IBGE %s (6 dígitos: %s)", nome_municipio, uf_sigla, args.codigo_ibge, codigo_municipio_6)

    resumo_caged = baixar_serie_caged(codigo_municipio_6)
    ultimo_mes = resumo_caged["competencia"].max()
    caminho_caged = RAIZ / "data" / "raw" / f"caged-movimentacao_mte_{ANO_INICIO_CAGED}-{ultimo_mes}_municipal.csv"
    resumo_caged.to_csv(caminho_caged, index=False, encoding="utf-8")

    metadados_caged = {
        "fonte": "Novo CAGED (Cadastro Geral de Empregados e Desempregados) — Ministério do Trabalho e Emprego, via FTP público",
        "url_ftp": f"ftp://{FTP_HOST}{CAMINHO_FTP_CAGED}",
        "codigo_ibge": args.codigo_ibge,
        "nome_municipio": nome_municipio,
        "uf": uf_sigla,
        "periodo_coberto": f"{ANO_INICIO_CAGED}01-{ultimo_mes}",
        "nivel_agregacao": "municipal ÚNICO — sem geolocalização de estabelecimento na versão pública; indicador de contexto, sem camada espacial",
        "quebra_metodologica_2020": (
            "o CAGED anterior a 2020 (formulário declaratório antigo) NÃO está incluído — o Novo CAGED "
            "(2020+) usa fonte primária diferente (eSocial) e layout de colunas totalmente distinto; "
            "não deve ser comparado/interpolado através dessa quebra sem ressalva, mesmo espírito da "
            "quebra já registrada entre os Censos do IBGE"
        ),
        "colunas": {
            "competencia": "ano+mês de referência da movimentação (AAAAMM)",
            "secao_cnae": "seção CNAE 2.0 (letra) do estabelecimento",
            "admissoes": "contagem de movimentações de admissão (saldomovimentação=1)",
            "desligamentos": "contagem de movimentações de desligamento (saldomovimentação=-1)",
            "saldo": "admissoes - desligamentos",
        },
        "metodo": "download mensal via FTP (arquivos .7z, ~45MB/mês), filtro local por código de município de 6 dígitos, agregado por competência x seção CNAE",
        "data_acesso": date.today().isoformat(),
        "data_processamento": datetime.now(timezone.utc).isoformat(),
    }
    caminho_caged.with_suffix(".json").write_text(json.dumps(metadados_caged, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    logger.info("Salvo: %s (%d linhas, até %s)", caminho_caged, len(resumo_caged), ultimo_mes)

    resumo_rais, ano_rais, n_sem_secao = baixar_rais(codigo_municipio_6)
    caminho_rais = RAIZ / "data" / "raw" / f"rais-vinculos_mte_{ano_rais}_municipal.csv"
    resumo_rais.to_csv(caminho_rais, index=False, encoding="utf-8")

    metadados_rais = {
        "fonte": "RAIS (Relação Anual de Informações Sociais) — Ministério do Trabalho e Emprego, arquivo de estabelecimentos, via FTP público",
        "url_ftp": f"ftp://{FTP_HOST}{CAMINHO_FTP_RAIS}/{ano_rais}/RAIS_ESTAB_PUB.7z",
        "codigo_ibge": args.codigo_ibge,
        "nome_municipio": nome_municipio,
        "uf": uf_sigla,
        "ano": ano_rais,
        "escopo": "só o ano mais recente disponível (estoque de vínculos ativos em 31/12) — decisão do usuário, não é série histórica",
        "nivel_agregacao": "municipal ÚNICO, agregado a partir de estabelecimentos (não vínculo individual) — sem geolocalização; indicador de contexto, sem camada espacial",
        "colunas": {
            "secao_cnae": "seção CNAE 2.0 (letra), mapeada pelos 2 primeiros dígitos (divisão) do código CNAE do estabelecimento via API de CNAE do IBGE (servicodados.ibge.gov.br/api/v2/cnae/divisoes)",
            "vinculos_ativos": "soma de 'Qtd Vínculos Ativos' de todos os estabelecimentos do município naquela seção CNAE, em 31/12",
        },
        "n_estabelecimentos_sem_secao_mapeada": int(n_sem_secao),
        "metodo": "download do arquivo nacional de estabelecimentos via FTP (.7z, ~130-150MB comprimido), filtro local por código de município de 6 dígitos, agregação por seção CNAE",
        "data_acesso": date.today().isoformat(),
        "data_processamento": datetime.now(timezone.utc).isoformat(),
    }
    caminho_rais.with_suffix(".json").write_text(json.dumps(metadados_rais, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    logger.info("Salvo: %s (%d seções CNAE, ano %d)", caminho_rais, len(resumo_rais), ano_rais)


if __name__ == "__main__":
    main()
