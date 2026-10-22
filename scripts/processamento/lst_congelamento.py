"""
Congelamento dos produtos de temperatura de superfície (LST) conferidos pelo
responsável (rodada 75). Nada vai ao portal.

    congelar   marca status "conferido" (conferido_em, conferido_por) nos .json
               irmãos das composições (principal e sensibilidade, todas as janelas,
               versões -terra), da grade de 250 m, dos bairros e dos setores; guarda
               uma cópia de referência dos vetoriais/tabelas conferidos em
               data/processed/lst/_conferidos_<data>/ e grava o manifesto com o
               sha256 de cada produto congelado.
    regerados  depois de regerar a agregação: compara cada produto vetorial/tabela
               com a cópia conferida (colunas alteradas), volta o status a
               "conferido" com regerado_em e a lista de colunas alteradas, e
               refaz no manifesto só as linhas dos produtos cujo sha256 mudou.
    copia-qgis md5 dos atributos (e da geometria) da cópia da grade em
               07_qgis_uruguaiana/dados_lst/ contra a cópia conferida: o QGIS
               regrava bytes internos do GeoPackage, então o sha256 não serve.

Uso:
    python scripts/processamento/lst_congelamento.py congelar --conferido-em 2026-10-21
    python scripts/processamento/lst_congelamento.py regerados --conferido-em 2026-10-21
    python scripts/processamento/lst_congelamento.py copia-qgis --pasta-qgis <.../07_qgis_uruguaiana>
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import pandas as pd

RAIZ = Path(__file__).resolve().parents[2]
PROC = RAIZ / "data" / "processed" / "lst"
SCRIPT = "scripts/processamento/lst_congelamento.py"
CONFERIDO_POR = "responsável"
PERIODO = "verao-2013-2026"
VARS_COMP = ("lst-mediana", "n-obs", "anomalia-mediana", "persistencia-p90", "ndvi-mediana", "ndbi-mediana", "mndwi-mediana")
# vetoriais e tabelas regerados pela agregação (mudam quando muda a regra de agregação)
AGREGADOS = [f"lst-grade_landsat-c2l2-principal_{PERIODO}_250m.gpkg",
             f"lst-bairros_ibge-2022_landsat-c2l2-principal_{PERIODO}_vetorial.gpkg",
             f"lst-bairros_ibge-2022_landsat-c2l2_{PERIODO}.csv",
             f"lst-setores-urbanos_ibge-2022_landsat-c2l2-principal_{PERIODO}_vetorial.gpkg",
             f"lst-setores-urbanos_ibge-2022_landsat-c2l2_{PERIODO}.csv"]

logger = logging.getLogger(__name__)


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for bloco in iter(lambda: f.read(1 << 20), b""):
            h.update(bloco)
    return h.hexdigest()


def irmao(p: Path) -> Path:
    """Mesma convenção do lst_composicao.py: TIF/GPKG → <nome>.json; CSV → <nome>.csv.json."""
    return p.with_suffix(".json") if p.suffix in (".tif", ".gpkg") else p.with_name(p.name + ".json")


def produtos() -> list[Path]:
    comp = sorted(p for p in PROC.glob("*_landsat-c2l2-*_verao-*_30m.tif")
                  if p.name.split("_")[0] in VARS_COMP or p.name.split("_")[0].endswith("-terra"))
    return comp + [PROC / n for n in AGREGADOS]


def tipo(p: Path) -> str:
    if p.suffix == ".tif":
        return "composição -terra" if "-terra_" in p.name else "composição"
    return {"lst-grade": "grade 250 m", "lst-bairros": "bairros", "lst-setores-urbanos": "setores"}[p.name.split("_")[0]]


def marcar(p: Path, conferido_em: str, extra: dict | None = None) -> dict:
    j = irmao(p)
    meta = json.loads(j.read_text(encoding="utf-8"))
    meta.update(status="conferido", conferido_em=conferido_em, conferido_por=CONFERIDO_POR,
                congelamento={"script": SCRIPT, "registrado_em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                              "manifesto": f"manifesto-congelamento_{conferido_em}.csv"})
    if extra:
        meta.update(extra)
    j.write_text(json.dumps(meta, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return meta


def linha_manifesto(p: Path, meta: dict, conferido_em: str) -> dict:
    s = sha256(p)
    return {"produto": p.name, "tipo": tipo(p), "sha256": s, "tamanho_bytes": p.stat().st_size,
            "sha256_igual_ao_json": s == meta.get("sha256"), "status": "conferido", "conferido_em": conferido_em,
            "conferido_por": CONFERIDO_POR, "regerado_em": meta.get("regerado_em"), "sha256_conferido": None, "nota": ""}


def gravar_manifesto(df: pd.DataFrame, arq: Path, conferido_em: str, nota: str) -> None:
    df.to_csv(arq, index=False)
    meta = {"arquivo": arq.name, "data_processamento": datetime.now(timezone.utc).isoformat(timespec="seconds"), "script": SCRIPT,
            "conteudo": "sha256 de cada produto de LST congelado (conferido pelo responsável no mapa)",
            "conferido_em": conferido_em, "n_produtos": int(len(df)), "nota": nota, "status": "conferido",
            "tamanho_bytes": arq.stat().st_size, "sha256": sha256(arq)}
    irmao(arq).write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("manifesto %s (%d produtos)", arq.name, len(df))


def congelar(conferido_em: str, forcar: bool) -> None:
    arq = PROC / f"manifesto-congelamento_{conferido_em}.csv"
    if arq.exists() and not forcar:
        logger.info("manifesto já existe: %s (use --forcar)", arq.name)
        return
    ps = produtos()
    faltam = [p.name for p in ps if not p.exists()]
    if faltam:
        raise FileNotFoundError(f"produtos ausentes: {faltam}")
    # cópia de referência do que foi conferido (os agregados mudam se a regra de agregação mudar)
    ref = PROC / f"_conferidos_{conferido_em}"
    ref.mkdir(exist_ok=True)
    for n in AGREGADOS:
        for p in (PROC / n, irmao(PROC / n)):
            if not (ref / p.name).exists() or forcar:
                shutil.copy2(p, ref / p.name)
    linhas = [linha_manifesto(p, marcar(p, conferido_em), conferido_em) for p in ps]
    df = pd.DataFrame(linhas)
    if not df["sha256_igual_ao_json"].all():
        logger.warning("sha256 diferente do .json em: %s", df.loc[~df["sha256_igual_ao_json"], "produto"].tolist())
    gravar_manifesto(df, arq, conferido_em, f"cópia de referência dos agregados conferidos em data/processed/lst/{ref.name}/")


def colunas_alteradas(atual: Path, conferido: Path) -> list[str]:
    if atual.suffix == ".gpkg":
        a, b = gpd.read_file(atual), gpd.read_file(conferido)
    else:
        a, b = pd.read_csv(atual), pd.read_csv(conferido)
    if len(a) != len(b) or list(a.columns) != list(b.columns):
        return ["(estrutura diferente)"]
    out = []
    for c in a.columns:
        if c == "geometry":
            if not a.geometry.geom_equals_exact(b.geometry, 1e-6).all():
                out.append(c)
        elif not a[c].equals(b[c]):
            out.append(c)
    return out


def regerados(conferido_em: str, motivo: str) -> None:
    arq = PROC / f"manifesto-congelamento_{conferido_em}.csv"
    ref = PROC / f"_conferidos_{conferido_em}"
    man = pd.read_csv(arq).astype({"regerado_em": object, "sha256_conferido": object, "nota": object})
    hoje = datetime.now(timezone.utc).date().isoformat()
    for n in AGREGADOS:
        p = PROC / n
        i = man.index[man["produto"] == n][0]
        s = sha256(p)
        if s == man.at[i, "sha256"]:
            continue
        alt = colunas_alteradas(p, ref / n)
        meta = marcar(p, conferido_em, {"regerado_em": hoje, "motivo_regeracao": motivo, "colunas_alteradas_apos_conferencia": alt,
                                        "versao_conferida": f"data/processed/lst/{ref.name}/{n}"})
        if pd.isna(man.at[i, "sha256_conferido"]):  # primeira regeração: guarda o sha256 da versão conferida
            man.at[i, "sha256_conferido"] = man.at[i, "sha256"]
        man.loc[i, ["sha256", "tamanho_bytes", "sha256_igual_ao_json", "regerado_em", "nota"]] = [
            s, p.stat().st_size, s == meta.get("sha256"), hoje, "regerado; colunas alteradas: " + ", ".join(alt)]
        logger.info("%s: regerado, colunas alteradas %s", n, alt)
    gravar_manifesto(man, arq, conferido_em, f"agregados regerados: {motivo}; versão conferida em data/processed/lst/{ref.name}/")


def md5_atributos(p: Path) -> tuple[str, str, int]:
    g = gpd.read_file(p)
    attrs = g.drop(columns="geometry")
    h_a = hashlib.md5(attrs.to_csv(index=False, float_format="%.10g").encode("utf-8")).hexdigest()
    h_g = hashlib.md5(b"".join(g.geometry.normalize().to_wkb())).hexdigest()
    return h_a, h_g, len(g)


def copia_qgis(pasta_qgis: Path, conferido_em: str) -> None:
    n = AGREGADOS[0]
    copia = pasta_qgis / "dados_lst" / n
    conferido = PROC / f"_conferidos_{conferido_em}" / n
    a, b = md5_atributos(copia), md5_atributos(conferido)
    res = {"produto": n, "copia": f"07_qgis_uruguaiana/dados_lst/{n}", "sha256_copia": sha256(copia), "sha256_conferido": sha256(conferido),
           "md5_atributos_copia": a[0], "md5_atributos_conferido": b[0], "md5_geometria_copia": a[1], "md5_geometria_conferido": b[1],
           "n_feicoes": [a[2], b[2]], "conteudo_igual": a[:2] == b[:2],
           "regra": "md5 do CSV dos atributos (sem geometria, floats com 10 dígitos significativos) e md5 do WKB das geometrias normalizadas; "
                    "o sha256 do arquivo difere porque o QGIS regravou bytes internos do GeoPackage",
           "script": SCRIPT, "data_processamento": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    arq = PROC / f"manifesto-congelamento_{conferido_em}_copia-qgis.json"
    arq.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("cópia do QGIS: conteúdo igual = %s", res["conteudo_igual"])


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("etapa", choices=["congelar", "regerados", "copia-qgis"])
    ap.add_argument("--conferido-em", default="2026-10-21")
    ap.add_argument("--motivo", default="fracao_nucleo passou a ser medida sobre a parte de terra da unidade (fora da água do OSM)")
    ap.add_argument("--pasta-qgis", type=Path, help="pasta 07_qgis_uruguaiana (só leitura)")
    ap.add_argument("--forcar", action="store_true")
    o = ap.parse_args()
    try:
        if o.etapa == "congelar":
            congelar(o.conferido_em, o.forcar)
        elif o.etapa == "regerados":
            regerados(o.conferido_em, o.motivo)
        else:
            if o.pasta_qgis is None:
                ap.error("--pasta-qgis é obrigatório em copia-qgis")
            copia_qgis(o.pasta_qgis, o.conferido_em)
    except (FileNotFoundError, KeyError, IndexError) as e:
        logger.error("%s", e)
        sys.exit(2)


if __name__ == "__main__":
    main()
