"""Focos de queimada do INPE a partir dos CSV MENSAIS (rodada 121), para os meses que o
arquivo anual ainda não cobre. Gera:
    data/raw/focos-queimada_inpe-mensal_{inicio}-{fim}_pontual.csv   (inicio/fim = AAAAMM)
    data/raw/focos-queimada_inpe-mensal_{inicio}-{fim}_pontual.json

Fonte: https://dataserver-coids.inpe.br/queimadas/queimadas/focos/csv/mensal/Brasil/focos_mensal_br_AAAAMM.csv
(um CSV nacional por mês, todos os satélites, até ~137 MB). O cabeçalho é diferente do arquivo
anual (lat, lon, data_hora_gmt, satelite, municipio_id...). O INPE regrava meses já publicados
(revisão retroativa), por isso o Last-Modified de cada mês fica no metadado.

Método (o mesmo do anual em queimadas_inpe.py, para os números serem comparáveis):
    - download em blocos para um arquivo temporário, leitura por partes (pandas chunksize) e remoção do
      temporário logo depois: o CSV nacional não fica guardado;
    - filtro por caixa do município e depois ponto-no-polígono no limite oficial
      (scripts/utils/recorte_municipio.carregar_area_estudo);
    - todos os satélites (não só o de referência).
Cache por mês em data/raw/cache_queimadas_inpe/mensal/AAAAMM.csv (só os focos do município) com o
Last-Modified da fonte: o mês é baixado de novo só se a fonte o regravou (ou com --forcar).
Só meses completos: --mes-fim (AAAA-MM) é o último; o mês corrente não entra.

Uso:
    python scripts/download/queimadas_inpe_mensal.py --inicio 2025-01 --mes-fim 2026-10
"""

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import pandas as pd
import requests

sys.path.append(str(Path(__file__).resolve().parents[1] / "utils"))
from recorte_municipio import carregar_area_estudo  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

RAIZ = Path(__file__).resolve().parents[2]
URL = "https://dataserver-coids.inpe.br/queimadas/queimadas/focos/csv/mensal/Brasil/focos_mensal_br_{aaaamm}.csv"
CACHE = RAIZ / "data" / "raw" / "cache_queimadas_inpe" / "mensal"
HEADERS = {"User-Agent": "climapampa-geoportal (coleta de dados abertos)"}
COLUNAS = ["lat", "lon", "data_hora_gmt", "satelite", "municipio", "municipio_id", "bioma"]
CHUNK = 500_000
TENTATIVAS = 4


def meses(inicio: str, fim: str) -> list[str]:
    a, m = map(int, inicio.split("-"))
    fa, fm = map(int, fim.split("-"))
    out = []
    while (a, m) <= (fa, fm):
        out.append(f"{a}{m:02d}")
        a, m = (a + 1, 1) if m == 12 else (a, m + 1)
    return out


def last_modified(aaaamm: str) -> str | None:
    r = requests.head(URL.format(aaaamm=aaaamm), headers=HEADERS, timeout=60)
    if r.status_code != 200:
        return None
    return r.headers.get("Last-Modified")


def filtrar_mes(aaaamm: str, area_4326: gpd.GeoDataFrame) -> pd.DataFrame:
    """Lê o CSV nacional do mês em fluxo, por partes, e devolve só os focos dentro do município."""
    xmin, ymin, xmax, ymax = area_4326.total_bounds
    for tentativa in range(1, TENTATIVAS + 1):
        try:
            partes = []
            # o CSV nacional vai para um arquivo temporário (em blocos) e é apagado logo depois da leitura por partes
            tmp = CACHE / f"_{aaaamm}.csv.tmp"
            try:
                with requests.get(URL.format(aaaamm=aaaamm), headers=HEADERS, stream=True, timeout=300) as r:
                    r.raise_for_status()
                    with open(tmp, "wb") as f:
                        for pedaco in r.iter_content(chunk_size=1 << 20):
                            f.write(pedaco)
                for bloco in pd.read_csv(tmp, usecols=COLUNAS, chunksize=CHUNK, skipinitialspace=True):
                    bloco = bloco[(bloco["lon"] >= xmin) & (bloco["lon"] <= xmax) & (bloco["lat"] >= ymin) & (bloco["lat"] <= ymax)]
                    if len(bloco):
                        partes.append(bloco)
            finally:
                tmp.unlink(missing_ok=True)
            df = pd.concat(partes) if partes else pd.DataFrame(columns=COLUNAS)
            break
        except (requests.RequestException, OSError, ValueError) as erro:
            if tentativa == TENTATIVAS:
                raise
            espera = 5 * 2 ** (tentativa - 1)
            logger.warning("%s: %s; nova tentativa em %ds", aaaamm, erro, espera)
            time.sleep(espera)
    if df.empty:
        return df
    pts = gpd.GeoDataFrame(df, geometry=gpd.points_from_xy(df["lon"], df["lat"]), crs="EPSG:4326")
    dentro = gpd.sjoin(pts, area_4326[["geometry"]], predicate="within", how="inner")
    return pd.DataFrame(dentro[COLUNAS])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--codigo-ibge", default="4322400")
    ap.add_argument("--inicio", required=True, help="AAAA-MM do primeiro mês")
    ap.add_argument("--mes-fim", required=True, help="AAAA-MM do último mês completo")
    ap.add_argument("--forcar", action="store_true", help="baixa de novo todos os meses")
    a = ap.parse_args()

    area = carregar_area_estudo().to_crs("EPSG:4326")
    CACHE.mkdir(parents=True, exist_ok=True)
    registro = {}
    tabelas = []
    for aaaamm in meses(a.inicio, a.mes_fim):
        lm = last_modified(aaaamm)
        if lm is None:
            raise SystemExit(f"{aaaamm}: arquivo mensal não publicado")
        cache, cache_meta = CACHE / f"{aaaamm}.csv", CACHE / f"{aaaamm}.json"
        em_dia = cache.exists() and cache_meta.exists() and json.loads(cache_meta.read_text(encoding="utf-8")).get("last_modified") == lm
        if a.forcar or not em_dia:
            logger.info("%s: baixando (Last-Modified %s)", aaaamm, lm)
            df = filtrar_mes(aaaamm, area)
            df.to_csv(cache, index=False, encoding="utf-8")
            cache_meta.write_text(json.dumps({"last_modified": lm, "data_acesso": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                              "n_focos": len(df)}, ensure_ascii=False, indent=2), encoding="utf-8")
        else:
            logger.info("%s: em cache (Last-Modified %s)", aaaamm, lm)
            df = pd.read_csv(cache)
        logger.info("%s: %d focos no município", aaaamm, len(df))
        registro[aaaamm] = {"last_modified": lm, "n_focos": len(df)}
        tabelas.append(df)

    tabela = pd.concat(tabelas, ignore_index=True)
    tabela.insert(0, "codigo_ibge", a.codigo_ibge)
    ini, fim = a.inicio.replace("-", ""), a.mes_fim.replace("-", "")
    saida = RAIZ / "data" / "raw" / f"focos-queimada_inpe-mensal_{ini}-{fim}_pontual.csv"
    tabela.to_csv(saida, index=False, encoding="utf-8")
    agora = datetime.now(timezone.utc).isoformat(timespec="seconds")
    meta = {
        "arquivo": saida.name, "fonte": "INPE/CGIP/COIDS — servidor de dados abertos, CSV mensal por país (todos os satélites)",
        "url_padrao": URL.format(aaaamm="AAAAMM"), "codigo_ibge": a.codigo_ibge, "periodo": {"inicio": a.inicio, "fim": a.mes_fim},
        "meses": registro, "n_focos": len(tabela),
        "metodo": "download em blocos para arquivo temporário apagado após a leitura por partes; filtro por caixa e ponto-no-polígono no limite oficial (mesmo método do anual); todos os satélites",
        "colunas": {"lat": "latitude", "lon": "longitude", "data_hora_gmt": "data e hora da passagem (GMT)", "satelite": "satélite/sensor",
                    "municipio": "nome na fonte", "municipio_id": "código IBGE na fonte", "bioma": "bioma na fonte"},
        "aviso": "o INPE regrava meses já publicados; os números do mês corrente e dos anteriores podem mudar",
        "data_acesso": agora, "data_processamento": agora,
    }
    saida.with_suffix(".json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Salvo: %s (%d focos)", saida, len(tabela))


if __name__ == "__main__":
    main()
