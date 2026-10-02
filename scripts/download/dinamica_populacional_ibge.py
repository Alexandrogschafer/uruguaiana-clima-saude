"""
Download das fontes IBGE da caracterização da dinâmica populacional
(Censos 2000, 2010 e 2022) que ainda não estão em data/raw.

Fontes (todas produtos padrão e públicos do IBGE):
  1. Histórico de formação dos setores censitários 2010–2022 (planilha
     nacional) + leia-me de comparabilidade 2010–2022. É a correspondência
     OFICIAL entre setores de 2010 e de 2022, base das "áreas comparáveis".
  2. Censo 2022 — Agregados por Setores Censitários (CSV nacionais por tema)
     + dicionário de dados.
  3. Censo 2022 — Grade Estatística (quadrante grade_idNN; células de 200 m no
     urbano e 1 km no rural).
  4. Censo 2022 — CNEFE (Cadastro Nacional de Endereços para Fins
     Estatísticos), arquivo CSV do município.

O que NÃO baixa (já está em data/raw/cache_setores_historico, gerado por
scripts/download/setores_censitarios_historico.py): agregados por setor e
malhas de 2000 e 2010 do RS.

Padrão do repositório:
  - idempotente: arquivo já presente no destino não é baixado de novo (o
    sha256 é recalculado e registrado do mesmo jeito);
  - cada arquivo ganha um .json irmão (url, bytes, sha256, data de acesso,
    Last-Modified da origem) e tudo vai para um manifesto único;
  - User-Agent genérico; nenhuma credencial; nenhum dado pessoal (o CNEFE é
    cadastro de endereços, sem nome de morador).
  - arquivos acima de LIMITE_AVISO_MB são anunciados no log antes do download.

Uso:
  python scripts/download/dinamica_populacional_ibge.py --codigo-ibge 4322400
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

CODIGO_IBGE_DEFAULT = "4322400"  # Uruguaiana, RS
# Quadrante da grade estatística que contém o município (índice oficial da
# grade do IBGE, quadrantes de 500 km). Parâmetro: conferir para outro município.
GRADE_ID_DEFAULT = "13"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; download-dados-publicos/1.0)"}
N_TENTATIVAS = 3
LIMITE_AVISO_MB = 300

RAIZ = Path(__file__).resolve().parents[2]
DESTINO = RAIZ / "data" / "raw" / "cache_dinamica_populacional"

URL_MUNICIPIO_IBGE = "https://servicodados.ibge.gov.br/api/v1/localidades/municipios/{codigo}"
GEOFTP_SETORES_2022 = (
    "https://geoftp.ibge.gov.br/organizacao_do_territorio/malhas_territoriais/"
    "malhas_de_setores_censitarios__divisoes_intramunicipais/censo_2022/"
)
FTP_AGREGADOS_2022 = "https://ftp.ibge.gov.br/Censos/Censo_Demografico_2022/Agregados_por_Setores_Censitarios/"
URL_GRADE_2022 = (
    "https://geoftp.ibge.gov.br/recortes_para_fins_estatisticos/grade_estatistica/"
    "censo_2022/grade_estatistica/grade_id{grade_id}.zip"
)
URL_CNEFE_2022 = (
    "https://ftp.ibge.gov.br/Cadastro_Nacional_de_Enderecos_para_Fins_Estatisticos/"
    "Censo_Demografico_2022/Arquivos_CNEFE/CSV/Municipio/{uf_cod}_{uf}/{codigo}_{nome}.zip"
)

# Temas dos agregados por setor 2022 usados na caracterização:
#   basico      — pessoas, domicílios, moradores por domicílio (V0001..V0007)
#   demografia  — sexo x grupos de idade (V01006..V01041)
#   caracteristicas_domicilio1 — domicílios por número de moradores
TEMAS_AGREGADOS_2022 = {
    "basico": "Agregados_por_setores_basico_BR_20260520.zip",
    "demografia": "Agregados_por_setores_demografia_BR.zip",
    "caracteristicas_domicilio1": "Agregados_por_setores_caracteristicas_domicilio1_BR.zip",
}
DICIONARIO_2022 = "dicionario_de_dados_agregados_por_setores_censitarios_20260520.xlsx"
URL_DOC_AGREGADOS_2010 = (
    "https://ftp.ibge.gov.br/Censos/Censo_Demografico_2010/Resultados_do_Universo/"
    "Agregados_por_Setores_Censitarios/Documentacao_Agregado_dos_Setores_2010_20231030.zip"
)


def _sha256(caminho: Path) -> str:
    h = hashlib.sha256()
    with open(caminho, "rb") as f:
        for bloco in iter(lambda: f.read(1 << 20), b""):
            h.update(bloco)
    return h.hexdigest()


def _sem_acento_maiusculo(texto: str) -> str:
    """Nome do município no padrão de arquivo do CNEFE (ex.: 'SAO_PAULO')."""
    s = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return s.upper().replace(" ", "_").replace("'", "")


def baixar(sessao: requests.Session, url: str, destino: Path, forcar: bool = False) -> dict:
    """Baixa url -> destino (se ainda não existir) e devolve o registro do manifesto."""
    destino.parent.mkdir(parents=True, exist_ok=True)
    last_modified = None
    if destino.exists() and not forcar:
        logger.info("Já presente, não baixa de novo: %s", destino.name)
        origem = "arquivo já presente no destino (não baixado nesta execução)"
    else:
        cab = sessao.head(url, headers=HEADERS, timeout=60, allow_redirects=True)
        cab.raise_for_status()
        tamanho = int(cab.headers.get("Content-Length", 0))
        last_modified = cab.headers.get("Last-Modified")
        if tamanho > LIMITE_AVISO_MB * 1e6:
            logger.warning("Arquivo grande (%.0f MB > %d MB): %s — seguindo com o download", tamanho / 1e6, LIMITE_AVISO_MB, url)
        ultimo_erro = None
        for tentativa in range(1, N_TENTATIVAS + 1):
            try:
                tmp = destino.with_suffix(destino.suffix + ".parcial")
                with sessao.get(url, headers=HEADERS, stream=True, timeout=180) as r:
                    r.raise_for_status()
                    with open(tmp, "wb") as f:
                        for bloco in r.iter_content(1 << 20):
                            f.write(bloco)
                tmp.replace(destino)
                break
            except requests.RequestException as erro:
                ultimo_erro = erro
                logger.warning("Falha em %s (tentativa %d/%d): %s", url, tentativa, N_TENTATIVAS, erro)
                time.sleep(2 ** tentativa)
        else:
            raise RuntimeError(f"Falha ao baixar {url}: {ultimo_erro}")
        origem = "baixado nesta execução"
    registro = {
        "url": url,
        "arquivo": str(destino.relative_to(RAIZ)),
        "tamanho_bytes": destino.stat().st_size,
        "sha256": _sha256(destino),
        "data_acesso": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "last_modified_origem": last_modified,
        "origem_nesta_execucao": origem,
        "fonte": "IBGE (arquivo público, como veio)",
        "licenca": "dados públicos do IBGE",
    }
    # .json irmão: preserva data de acesso anterior se o arquivo não foi rebaixado
    irmao = destino.with_name(destino.name + ".json")
    if irmao.exists() and "não baixado" in origem:
        anterior = json.loads(irmao.read_text(encoding="utf-8"))
        if anterior.get("sha256") == registro["sha256"]:
            registro["data_acesso"] = anterior.get("data_acesso", registro["data_acesso"])
            registro["last_modified_origem"] = anterior.get("last_modified_origem")
    irmao.write_text(json.dumps(registro, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("%s — %d bytes — sha256 %s", destino.name, registro["tamanho_bytes"], registro["sha256"])
    return registro


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--codigo-ibge", default=CODIGO_IBGE_DEFAULT)
    p.add_argument("--grade-id", default=GRADE_ID_DEFAULT, help="quadrante da grade estatística 2022")
    p.add_argument("--forcar", action="store_true", help="baixa de novo mesmo se o arquivo existir")
    a = p.parse_args()

    sessao = requests.Session()
    try:
        info = sessao.get(URL_MUNICIPIO_IBGE.format(codigo=a.codigo_ibge), headers=HEADERS, timeout=60).json()
        nome = info["nome"]
        uf = info["microrregiao"]["mesorregiao"]["UF"]["sigla"]
        uf_cod = str(info["microrregiao"]["mesorregiao"]["UF"]["id"])
    except (requests.RequestException, KeyError, TypeError) as erro:
        raise SystemExit(f"Não foi possível obter nome/UF do município {a.codigo_ibge}: {erro}")

    registros = []
    for arq in ("Historico_formacao_Setores_Censitarios_2010_2022.xlsx", "Leia_me_Comparabilidade_2010_2022.pdf"):
        registros.append(baixar(sessao, GEOFTP_SETORES_2022 + arq, DESTINO / "setores_2010_2022" / arq, a.forcar))
    registros.append(baixar(sessao, FTP_AGREGADOS_2022 + DICIONARIO_2022, DESTINO / "agregados_2022" / DICIONARIO_2022, a.forcar))
    # documentação dos agregados por setor de 2010 (dicionário de variáveis; os dados já estão em cache)
    registros.append(baixar(sessao, URL_DOC_AGREGADOS_2010, DESTINO / "agregados_2010" / URL_DOC_AGREGADOS_2010.rsplit("/", 1)[1], a.forcar))
    for arq in TEMAS_AGREGADOS_2022.values():
        url = FTP_AGREGADOS_2022 + "Agregados_por_Setor_csv/" + arq
        registros.append(baixar(sessao, url, DESTINO / "agregados_2022" / arq, a.forcar))
    registros.append(baixar(sessao, URL_GRADE_2022.format(grade_id=a.grade_id),
                            DESTINO / "grade_2022" / f"grade_id{a.grade_id}.zip", a.forcar))
    nome_arq = f"{a.codigo_ibge}_{_sem_acento_maiusculo(nome)}"
    url_cnefe = URL_CNEFE_2022.format(uf_cod=uf_cod, uf=uf, codigo=a.codigo_ibge, nome=_sem_acento_maiusculo(nome))
    registros.append(baixar(sessao, url_cnefe, DESTINO / "cnefe_2022" / f"{nome_arq}.zip", a.forcar))

    manifesto = DESTINO / "manifesto_downloads.json"
    manifesto.write_text(json.dumps({
        "codigo_ibge": a.codigo_ibge, "municipio": nome, "uf": uf,
        "script": "scripts/download/dinamica_populacional_ibge.py",
        "gerado_em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "arquivos": registros,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Manifesto: %s", manifesto)


if __name__ == "__main__":
    main()
