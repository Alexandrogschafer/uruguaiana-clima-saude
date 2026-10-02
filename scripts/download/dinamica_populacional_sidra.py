"""
Tabelas do SIDRA/IBGE (API de agregados v3) para a dinâmica populacional
municipal e distrital nos Censos 2000, 2010 e 2022.

Por que estas tabelas (e não a 200 já baixada):
  - a tabela 200 é da AMOSTRA ("Características Gerais da População"); aqui
    a estrutura etária vem do UNIVERSO, com idade simples, o que permite
    idade mediana e grupos quinquenais exatos:
      1552 — 2000 e 2010: população por situação, sexo e idade (universo)
      9514 — 2022: população por sexo e idade (universo)
  - totais e situação urbana/rural, também por distrito (nível N10):
      202  — 2000 e 2010: população por sexo e situação
      9923 — 2022: população por situação
  - domicílios:
      185  — 2000 e 2010: domicílios particulares permanentes por número de
             moradores e situação (inclui "1 morador")
      3451 — 2010: domicílios particulares permanentes e moradores
      4712 — 2022: domicílios particulares permanentes ocupados e moradores

Saída (formato longo, um CSV por tabela, + .json irmão):
  data/raw/cache_dinamica_populacional/sidra/sidra_tabela{N}_{periodos}.csv
Idempotente: tabela já baixada não é consultada de novo (use --forcar).

Uso:
  python scripts/download/dinamica_populacional_sidra.py --codigo-ibge 4322400
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

CODIGO_IBGE_DEFAULT = "4322400"  # Uruguaiana, RS
URL_SIDRA = "https://servicodados.ibge.gov.br/api/v3/agregados/{tabela}/periodos/{periodos}/variaveis/{variaveis}"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; download-dados-publicos/1.0)"}
DESTINO = Path(__file__).resolve().parents[2] / "data" / "raw" / "cache_dinamica_populacional" / "sidra"

# (tabela, períodos, variáveis, classificações, níveis) — {mun} é trocado pelo código IBGE.
# N6 = município; N10 = distritos do município.
CONSULTAS = [
    (1552, "2000|2010", "93", "287[all]|2[all]|1[all]|286[0]", "N6[{mun}]"),
    (1552, "2000|2010", "93", "287[all]|2[all]|1[all]|286[0]", "N10[N6[{mun}]]"),
    (9514, "2022", "93", "287[all]|2[all]|286[113635]", "N6[{mun}]"),
    (202, "2000|2010", "93", "2[all]|1[all]", "N6[{mun}]|N10[N6[{mun}]]"),
    (9923, "2022", "93", "1[all]", "N6[{mun}]|N10[N6[{mun}]]"),
    (185, "2000|2010", "96", "1[all]|68[all]", "N6[{mun}]|N10[N6[{mun}]]"),
    (3451, "2010", "96|137|246", "1[all]", "N6[{mun}]|N10[N6[{mun}]]"),
    (4712, "2022", "381|382|5930", None, "N6[{mun}]"),
]


def consultar(sessao: requests.Session, tabela: int, periodos: str, variaveis: str, classif: str | None, locs: str) -> pd.DataFrame:
    params = {"localidades": locs}
    if classif:
        params["classificacao"] = classif
    url = URL_SIDRA.format(tabela=tabela, periodos=periodos, variaveis=variaveis)
    for tentativa in range(1, 4):
        try:
            r = sessao.get(url, params=params, headers=HEADERS, timeout=180)
            r.raise_for_status()
            dados = r.json()
            break
        except (requests.RequestException, ValueError) as erro:
            logger.warning("SIDRA %s falhou (tentativa %d/3): %s", tabela, tentativa, erro)
            time.sleep(2 ** tentativa)
    else:
        raise RuntimeError(f"SIDRA {tabela}: sem resposta válida")
    linhas = []
    for var in dados:
        for res in var["resultados"]:
            cats = {}
            for c in res["classificacoes"]:
                (cid, cnome), = c["categoria"].items()
                cats[c["nome"]] = cnome
                cats[c["nome"] + " (id)"] = cid
            for serie in res["series"]:
                for per, valor in serie["serie"].items():
                    linhas.append({
                        "tabela": tabela, "variavel_id": var["id"], "variavel": var["variavel"],
                        "unidade": var.get("unidade"), "nivel": serie["localidade"]["nivel"]["id"],
                        "cod_localidade": serie["localidade"]["id"], "localidade": serie["localidade"]["nome"],
                        "periodo": int(per), **cats, "valor_bruto": valor,
                    })
    df = pd.DataFrame(linhas)
    # '-' = zero absoluto; '...'/'X'/'..' = indisponível/sigilo -> NaN (não zero)
    df["valor"] = pd.to_numeric(df["valor_bruto"].replace({"-": "0"}), errors="coerce")
    return df, r.url


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--codigo-ibge", default=CODIGO_IBGE_DEFAULT)
    p.add_argument("--forcar", action="store_true")
    a = p.parse_args()
    DESTINO.mkdir(parents=True, exist_ok=True)
    sessao = requests.Session()
    for tabela, periodos, variaveis, classif, locs in CONSULTAS:
        nivel = "distrito" if locs.startswith("N10") else ("municipio-distrito" if "N10" in locs else "municipio")
        saida = DESTINO / f"sidra_tabela{tabela}_{periodos.replace('|', '-')}_{nivel}.csv"
        if saida.exists() and not a.forcar:
            logger.info("Já baixada: %s", saida.name)
            continue
        df, url = consultar(sessao, tabela, periodos, variaveis, classif, locs.format(mun=a.codigo_ibge))
        df.to_csv(saida, index=False)
        meta = {
            "fonte": f"IBGE — SIDRA, tabela {tabela} (Censo Demográfico)",
            "url_consulta": url,
            "codigo_ibge": a.codigo_ibge,
            "data_acesso": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "n_linhas": len(df),
            "tamanho_bytes": saida.stat().st_size,
            "sha256": hashlib.sha256(saida.read_bytes()).hexdigest(),
            "tratamento": "formato longo, sem transformação de valores; '-' convertido para 0 e símbolos de sigilo/indisponível para NaN na coluna 'valor' (o texto original fica em 'valor_bruto')",
        }
        saida.with_suffix(".json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("%s: %d linhas", saida.name, len(df))


if __name__ == "__main__":
    main()
