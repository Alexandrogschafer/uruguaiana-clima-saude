"""
Temperatura de superfície (LST) da área urbana — composição de verão, anomalia
e persistência (rodada 73; ajustes da rodada 74). Exposição ao calor urbano;
nada vai ao portal.

Reaproveita a AOI, o acesso ao Planetary Computer, a leitura por janela de COG
e as fórmulas de scripts/exploracao/lst_levantamento.py (rodada 72).

Etapas (subcomandos):
    selecao     Landsat 8 e 9 C2 L2, categoria T1, path/row 225/081, verão
                estendido dez–mar (verões 2013/14 a 2025/26). Fração de pixels
                limpos na AOI pelo QA_PIXEL (cache da rodada 72; lê o que faltar).
                Conjunto PRINCIPAL: ≥ 95 % limpos e rio abaixo da cota de alerta
                na data; SENSIBILIDADE: ≥ 80 % limpos (máscara por pixel), sem
                filtro do rio. Tabela de cenas com o motivo de cada decisão.
    recortes    para cada cena da sensibilidade (que contém a principal): janela
                da AOI + 150 m de ST_B10, QA_PIXEL e SR_B3–B6 (bruto, grade e CRS
                originais, .json com sha256) e os derivados a 30 m em EPSG:31981
                (LST °C, NDVI, NDBI, MNDWI) alinhados às bordas de pixel da cena.
    composicao  por pixel e por conjunto, no período inteiro e nas janelas de
                verões inteiros 2014–2018, 2019–2023 e 2024–2026 (verão rotulado
                pelo ano de janeiro: dez/2018 + jan–mar/2019 = verão 2019):
                lst_mediana, n_obs, anomalia_mediana, persistencia_p90 (+ medianas
                de NDVI, NDBI e MNDWI); versões "-terra" (água mascarada) de
                lst-mediana, anomalia-mediana e persistencia-p90 do principal.
    agregacao   grade de 250 m (mesma origem da grade de densidade de
                edificações), bairros IBGE 2022 e setores censitários urbanos 2022;
                correlações, rankings, estabilidade entre janelas, principal ×
                sensibilidade e figuras PNG.
    todos       as quatro etapas.

Regras (ver também os .json):
    - limpo = preenchido e bits 1–5 do QA_PIXEL zerados; índices fora de [−1, 1] = NaN.
    - filtro do rio: cota média diária da estação ANA 77150000 (série histórica
      diária, consistida antes da bruta; lacunas cobertas pela média diária da
      telemetria de 15 min) acima da cota de alerta do SAH Uruguai do SGB
      (750 cm em Uruguaiana, relatório SAH Uruguai 2023, tabela 2).
    - terra (estatísticas por cena) = pixel limpo fora dos polígonos de água do OSM
      e com MNDWI ≤ 0 na cena; área urbana = núcleo urbanizado IBGE 2019 da AOI.
    - anomalia da cena = LST − média da LST de terra urbana da mesma cena;
      persistencia_p90 = fração das observações válidas do pixel com LST acima
      do p90 da LST de terra urbana da cena, só no núcleo urbanizado fora da
      água do OSM (fora dele, NaN).
    - n_obs ≥ 10 por pixel (senão NaN); janela com < 5 cenas não é gerada.
    - terra (agregação) = pixel fora da água do OSM e com MNDWI mediano ≤ 0;
      unidade com < 50 % de pixels válidos de terra é suprimida (estatísticas
      vazias, geometria mantida com o motivo). Na grade, a fração é sobre todos
      os pixels da célula; em bairros e setores, só sobre a parte de terra do
      polígono (pixels fora da água do OSM), porque os ribeirinhos incluem o leito.
    - persistência agregada só quando ≥ 30 % dos pixels da unidade estão no
      núcleo urbanizado (senão vazia).

Saídas (fora do git): data/raw/raster/lst_landsat/ (recortes),
data/processed/lst/ (derivados por cena, composições, grade, bairros, setores,
tabelas e figuras), cada uma com .json irmão.

Uso:
    python scripts/processamento/lst_composicao.py todos --codigo-ibge 4322400 [--forcar]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import warnings
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import rasterio  # noqa: E402
from matplotlib.colors import BoundaryNorm  # noqa: E402
from rasterio.features import geometry_mask, rasterize  # noqa: E402
from rasterio.transform import Affine  # noqa: E402
from rasterio.warp import Resampling, reproject  # noqa: E402
from shapely.geometry import box, mapping, shape  # noqa: E402

RAIZ = Path(__file__).resolve().parents[2]
sys.path.append(str(RAIZ / "scripts" / "utils"))
sys.path.append(str(RAIZ / "scripts" / "download"))
sys.path.append(str(RAIZ / "scripts" / "exploracao"))
import lst_levantamento as lev  # noqa: E402
from recorte_municipio import CRS_PADRAO  # noqa: E402
from sentinel_planetary_computer import buscar, pedir, sha256  # noqa: E402

logger = logging.getLogger(__name__)

SCRIPT = "scripts/processamento/lst_composicao.py"
FONTE = f"USGS Landsat Collection 2 Level-2 via Microsoft Planetary Computer (coleção {lev.COL_LANDSAT})"
PROC = lev.PROCESSADO
BRUTO = lev.BRUTO_LANDSAT
PATH_ROW = "225/081"
VERAO_INI, VERAO_FIM = 2013, 2025  # verões 2013/14 … 2025/26 (ano de dezembro)
LIMIAR_PRINCIPAL, LIMIAR_SENS = 0.95, 0.80
MIN_OBS = 10
MIN_CENAS_JANELA = 5
# verões pelo ano de janeiro; o período inteiro mantém o nome "2013-2026" (dez/2013 a mar/2026)
JANELAS = {"2013-2026": (2014, 2026), "2014-2018": (2014, 2018), "2019-2023": (2019, 2023), "2024-2026": (2024, 2026)}
SUBJANELAS = ["2014-2018", "2019-2023", "2024-2026"]
MIN_FRAC_TERRA = 0.5
MIN_FRAC_NUCLEO = 0.3  # persistência agregada só em unidades com ≥ 30 % dos pixels no núcleo urbanizado
LADO_GRADE = 250  # mesma origem (múltiplos de 250 m em EPSG:31981) da grade de densidade
ESTACAO_RIO = "77150000"
COTA_ALERTA_CM = 750.0  # SGB, SAH Uruguai 2023, tabela 2 (Uruguaiana: inundação 850, alerta 750, atenção 650)
RIO = {
    "historica": RAIZ / "data/raw/nivel-rio_ana-serie-historica-77150000_1940-2026_diario.csv",
    "historica_recente": RAIZ / "data/raw/nivel-rio_ana-serie-historica-77150000_2024-10-01-a-2026-10-09_diario.csv",
    "telemetria": sorted((RAIZ / "data/raw").glob("nivel-rio_ana-telemetria-77150000_*_15min.csv")),
}
REF = {
    "densidade": RAIZ / "data/processed/edificacoes/densidade_edificacoes_250m_overture-2026-09-23.1_2026_vetorial.gpkg",
    "mapbiomas": RAIZ / "data/raw/raster/uso-solo_mapbiomas_2024_30m.tif",
    "setores": RAIZ / "data/processed/setores-censitarios_vulnerabilidade_unido.gpkg",
}
PERIODO = "verao-2013-2026"
TAB_CENAS = PROC / f"cenas-selecionadas_landsat-c2l2_{PERIODO}.csv"


# ---------------------------------------------------------------------------
# utilidades
# ---------------------------------------------------------------------------
def sidecar(caminho: Path, **campos) -> None:
    """Metadado irmão (.json); CSV/PNG/GPKG ganham <nome>.<ext>.json, TIF ganha <nome>.json."""
    meta = {"arquivo": caminho.name, "data_processamento": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "script": SCRIPT, "uso": "composição de verão da LST (rodadas 73–74); exploratório, fora do portal"}
    meta.update(campos)
    if caminho.exists() and caminho.is_file():
        meta["tamanho_bytes"] = caminho.stat().st_size
        meta["sha256"] = sha256(caminho)
    destino = caminho.with_suffix(".json") if caminho.suffix in (".tif", ".gpkg") else caminho.with_name(caminho.name + ".json")
    destino.write_text(json.dumps(meta, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def gravar_csv(df: pd.DataFrame, caminho: Path, **campos) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(caminho, index=False)
    sidecar(caminho, **campos)
    logger.info("gravado %s (%d linhas)", caminho.name, len(df))


def verao_de(d: pd.Timestamp) -> int:
    """Ano de dezembro do verão: jan–mar/2014 e dez/2013 pertencem ao verão 2013/14."""
    return d.year if d.month == 12 else d.year - 1


def verao_jan(d: pd.Timestamp) -> int:
    """Rótulo do verão pelo ano de janeiro: dez/2018 + jan–mar/2019 = verão 2019 (define as janelas)."""
    return d.year + 1 if d.month == 12 else d.year


# ---------------------------------------------------------------------------
# PARTE 1 — seleção de cenas
# ---------------------------------------------------------------------------
def cota_diaria() -> pd.DataFrame:
    """Cota média diária (cm) da estação, com a origem de cada valor."""
    partes = []
    for chave, prio in (("historica", 0), ("historica_recente", 0)):
        d = pd.read_csv(RIO[chave], dtype={"cota_cm": float})
        d = d[(d["media_diaria"] == 1) & d["cota_cm"].notna()]
        # consistido (2) antes do bruto (1)
        d = d.assign(prio=prio + (2 - d["nivel_consistencia"]), origem=np.where(d["nivel_consistencia"] == 2,
                     "ANA série histórica diária (consistida)", "ANA série histórica diária (bruta)"))
        partes.append(d[["data", "cota_cm", "prio", "origem"]])
    for arq in RIO["telemetria"]:
        t = pd.read_csv(arq, usecols=["data_hora", "nivel_cm"])
        t["data"] = t["data_hora"].str[:10]
        t = t.dropna(subset=["nivel_cm"]).groupby("data", as_index=False)["nivel_cm"].mean().rename(columns={"nivel_cm": "cota_cm"})
        partes.append(t.assign(prio=5, origem="ANA telemetria (média diária das leituras de 15 min)"))
    c = pd.concat(partes, ignore_index=True).sort_values(["data", "prio"]).drop_duplicates("data")
    return c.set_index("data")


def selecao(a: dict, forcar: bool) -> pd.DataFrame:
    if TAB_CENAS.exists() and not forcar:
        logger.info("seleção já existe: %s", TAB_CENAS.name)
        return pd.read_csv(TAB_CENAS)
    bbox = tuple(a["aoi_ll"].bounds)
    cache_arq = lev.PASTA / "qa_por_cena_cache.json"
    cache = json.loads(cache_arq.read_text(encoding="utf-8")) if cache_arq.exists() else {}
    itens = []
    for v in range(VERAO_INI, VERAO_FIM + 1):
        itens += buscar(lev.COL_LANDSAT, f"{v}-12-01/{v + 1}-03-31", bbox)
    alvo = {}
    for it in itens:
        p = it["properties"]
        if p["platform"] not in ("landsat-8", "landsat-9") or not shape(it["geometry"]).contains(a["aoi_ll"]):
            continue
        alvo[it["id"]] = it
    logger.info("%d itens L8/L9 de verão com pegada contendo a AOI", len(alvo))
    faltam = [i for i in alvo if i not in cache]
    for iid in faltam:  # poucas: o cache da rodada 72 cobre o inventário até out/2026
        try:
            cache[iid] = lev.fracao_limpa(alvo[iid], a["aoi_ll"])
        except Exception as e:
            logger.warning("QA falhou em %s: %s", iid, e)
    if faltam:
        cache_arq.write_text(json.dumps(cache), encoding="utf-8")
    cotas = cota_diaria()
    lon = a["aoi_ll"].centroid.x
    linhas = []
    for iid, it in alvo.items():
        d = lev.linha_item(it, lon)
        d.update(cache.get(iid, {"px_aoi": None, "frac_preenchida_aoi": None, "frac_limpa_aoi": None}))
        dt = pd.Timestamp(d["data"])
        d["verao"] = f"{verao_de(dt)}/{str(verao_de(dt) + 1)[2:]}"
        c = cotas["cota_cm"].get(d["data"])
        d["cota_rio_cm"] = None if c is None else round(float(c), 1)
        d["cota_rio_origem"] = cotas["origem"].get(d["data"])
        d["rio_acima_alerta"] = None if c is None else bool(c > COTA_ALERTA_CM)
        fl = d["frac_limpa_aoi"]
        motivos, princ, sens = [], False, False
        if d["categoria"] != "T1":
            motivos.append("categoria T2")
        elif d["wrs_path_row"] != PATH_ROW:
            motivos.append(f"path/row {d['wrs_path_row']}")
        elif fl is None:
            motivos.append("QA não lido")
        elif fl < LIMIAR_SENS:
            motivos.append(f"limpa {fl:.1%} < 80 %")
        else:
            sens = True
            if fl < LIMIAR_PRINCIPAL:
                motivos.append(f"limpa {fl:.1%} entre 80 % e 95 %: só sensibilidade")
            elif d["rio_acima_alerta"]:
                motivos.append(f"rio a {c:.0f} cm > cota de alerta {COTA_ALERTA_CM:.0f} cm: só sensibilidade")
            else:
                princ = True
                motivos.append("≥ 95 % limpa, T1, 225/081" + ("; cota do rio sem dado na data (sinalizado)" if c is None
                                                              else f"; rio a {c:.0f} cm ≤ alerta"))
        d.update(conjunto_principal=princ, conjunto_sensibilidade=sens, motivo="; ".join(motivos))
        linhas.append(d)
    df = pd.DataFrame(linhas).sort_values(["data", "plataforma"]).reset_index(drop=True)
    gravar_csv(df, TAB_CENAS, fonte=FONTE, aoi=lev.descrever_aoi(a),
               filtro=f"Landsat 8 e 9, pegada contendo a AOI, dez–mar dos verões {VERAO_INI}/{str(VERAO_INI + 1)[2:]} a "
                      f"{VERAO_FIM}/{str(VERAO_FIM + 1)[2:]}",
               regras={"principal": "T1, 225/081, ≥ 95 % de pixels limpos na AOI e rio ≤ cota de alerta na data (ou sem dado, sinalizado)",
                       "sensibilidade": "T1, 225/081, ≥ 80 % de pixels limpos na AOI (máscara por pixel), sem filtro do rio",
                       "mascara_qa": "limpo = preenchido e bits 1–5 do QA_PIXEL zerados"},
               rio={"estacao_ana": ESTACAO_RIO, "cota_alerta_cm": COTA_ALERTA_CM,
                    "fonte_cota_alerta": "SGB — relatório do SAH Uruguai 2023, tabela 2 (Uruguaiana: inundação 850, alerta 750, atenção 650 cm)",
                    "serie": "média diária da série histórica da ANA (consistida antes da bruta); lacunas pela média diária da telemetria"},
               status="extraido")
    return df


# ---------------------------------------------------------------------------
# PARTE 2 — recortes e derivados por cena
# ---------------------------------------------------------------------------
def item_stac(iid: str) -> dict:
    r = pedir("GET", f"{lev.STAC}/collections/{lev.COL_LANDSAT}/items/{iid}", timeout=60)
    r.raise_for_status()
    return r.json()


def nome_derivado(nome: str, plat: str, dia: str) -> Path:
    return PROC / f"{nome}_landsat-{plat}-c2l2_{dia}_30m.tif"


def recortes(a: dict, cenas: pd.DataFrame, forcar: bool) -> dict:
    """Recortes brutos + derivados por cena; devolve a grade comum."""
    PROC.mkdir(parents=True, exist_ok=True)
    # o levantamento grava o .json dos recortes com a própria função: aqui ela é trocada pela desta rodada
    lev.sidecar = sidecar
    grade = None
    sel = cenas[cenas["conjunto_sensibilidade"]]
    manifesto = []
    for k, r in enumerate(sel.itertuples(), 1):
        plat = r.plataforma.replace("-", "")
        prontos = all(nome_derivado(n, plat, r.data).exists() for n in ("lst", "ndvi", "ndbi", "mndwi"))
        brutos_ok = all((BRUTO / f"landsat-recorte-{b.lower().replace('_', '-')}_{plat}-c2l2_{r.data}_30m.tif").exists()
                        for b in lev.ATIVOS_TESTE.values())
        if prontos and brutos_ok and not forcar:
            brutos = {at: BRUTO / f"landsat-recorte-{b.lower().replace('_', '-')}_{plat}-c2l2_{r.data}_30m.tif"
                      for at, b in lev.ATIVOS_TESTE.items()}
        else:
            logger.info("[%d/%d] %s", k, len(sel), r.id)
            item = item_stac(r.id)
            brutos = lev.baixar_recortes(item, a, forcar)
        with rasterio.open(brutos["qa_pixel"]) as s:
            tr, w, h = lev.grade_alvo(s.transform, s.crs, a["aoi"])
        if grade is None:
            grade = (tr, w, h)
        elif (tr, w, h) != grade:
            raise RuntimeError(f"{r.id}: grade da cena difere da primeira ({tr} × {grade[0]}); ver alinhamento")
        for b in brutos.values():
            manifesto.append({"arquivo": b.name, "cena": r.id, "sha256": sha256(b), "tamanho_bytes": b.stat().st_size})
        if prontos and not forcar:
            continue
        arrs = derivar(brutos, tr, w, h, a)
        base = dict(fonte=FONTE, cena=r.id, plataforma=r.plataforma, data=f"{r.data}T{r.hora_utc}Z", crs=CRS_PADRAO, resolucao_m=30,
                    mascara="QA_PIXEL: só pixels preenchidos e sem nuvem dilatada, cirro, nuvem, sombra ou neve; fora da AOI = NaN; "
                            "índices fora de [−1, 1] = NaN", recortes_brutos=[x.name for x in brutos.values()],
                    grade="30 m em EPSG:31981 alinhada às bordas de pixel da cena; vizinho mais próximo (translação exata: entre "
                          "EPSG:32621 e EPSG:31981 muda só o falso norte)", status="extraido")
        formulas = {"lst": "LST em °C = ST_B10 × 0,00341802 + 149,0 − 273,15",
                    "ndvi": "NDVI = (SR_B5 − SR_B4)/(SR_B5 + SR_B4), SR = DN × 0,0000275 − 0,2",
                    "ndbi": "NDBI = (SR_B6 − SR_B5)/(SR_B6 + SR_B5), SR = DN × 0,0000275 − 0,2",
                    "mndwi": "MNDWI = (SR_B3 − SR_B6)/(SR_B3 + SR_B6), SR = DN × 0,0000275 − 0,2"}
        for nome, arr in arrs.items():
            lev.gravar_produto(arr, nome_derivado(nome, plat, r.data), tr, transformacao_aplicada=formulas[nome], **base)
    man = pd.DataFrame(manifesto)
    gravar_csv(man, BRUTO / f"manifesto-recortes_landsat-c2l2_{PERIODO}.csv", fonte=FONTE,
               conteudo="sha256 de cada recorte bruto (janela da AOI + 150 m, grade e CRS originais EPSG:32621) das cenas da sensibilidade",
               n_cenas=int(sel.shape[0]), status="extraido")
    return {"transform": grade[0], "w": grade[1], "h": grade[2]}


def derivar(brutos: dict, tr: Affine, w: int, h: int, a: dict) -> dict:
    b = {k: lev.ler_na_grade(v, tr, w, h) for k, v in brutos.items()}
    qa = b["qa_pixel"]
    dentro = ~geometry_mask([mapping(a["aoi"])], out_shape=(h, w), transform=tr)
    limpo = dentro & ((qa & 1) == 0) & ((qa & lev.QA_SUJO) == 0) & (b["lwir11"] > 0)
    lst = np.where(limpo, b["lwir11"] * lev.ST_ESCALA + lev.ST_DESLOC - 273.15, np.nan)
    sr = {k: np.where(limpo, b[k] * lev.SR_ESCALA + lev.SR_DESLOC, np.nan) for k in ("green", "red", "nir08", "swir16")}
    with np.errstate(divide="ignore", invalid="ignore"):
        ndvi = (sr["nir08"] - sr["red"]) / (sr["nir08"] + sr["red"])
        ndbi = (sr["swir16"] - sr["nir08"]) / (sr["swir16"] + sr["nir08"])
        mndwi = (sr["green"] - sr["swir16"]) / (sr["green"] + sr["swir16"])
    ndvi, ndbi, mndwi = (np.where(np.abs(x) <= 1, x, np.nan) for x in (ndvi, ndbi, mndwi))
    return {"lst": lst, "ndvi": ndvi, "ndbi": ndbi, "mndwi": mndwi}


def ler_tif(arq: Path) -> np.ndarray:
    with rasterio.open(arq) as s:
        return s.read(1)


# ---------------------------------------------------------------------------
# PARTE 3 — composição
# ---------------------------------------------------------------------------
def mascaras(a: dict, g: dict) -> dict:
    tr, w, h = g["transform"], g["w"], g["h"]
    dentro = ~geometry_mask([mapping(a["aoi"])], out_shape=(h, w), transform=tr)
    agua = rasterize([(mapping(a["agua"]), 1)], out_shape=(h, w), transform=tr, fill=0).astype(bool) & dentro
    urb = rasterize([(mapping(a["urbano"]), 1)], out_shape=(h, w), transform=tr, fill=0).astype(bool) & dentro & ~agua
    return {"dentro": dentro, "agua_osm": agua, "urbano": urb, "rural": dentro & ~agua & ~urb}


def nome_comp(var: str, conjunto: str, janela: str) -> Path:
    return PROC / f"{var}_landsat-c2l2-{conjunto}_verao-{janela}_30m.tif"


def composicao(a: dict, cenas: pd.DataFrame, g: dict, forcar: bool) -> pd.DataFrame:
    m = mascaras(a, g)
    resumo, por_cena = [], []
    pilhas = {}
    for conjunto, col in (("principal", "conjunto_principal"), ("sensibilidade", "conjunto_sensibilidade")):
        sel = cenas[cenas[col]].copy()
        for janela, (a0, a1) in JANELAS.items():
            ano = pd.to_datetime(sel["data"]).map(verao_jan)
            sj = sel[(ano >= a0) & (ano <= a1)]
            lin = {"conjunto": conjunto, "janela": janela, "n_cenas": int(len(sj)), "verao_ini": a0, "verao_fim": a1,
                   "primeira_cena": sj["data"].min() if len(sj) else None, "ultima_cena": sj["data"].max() if len(sj) else None}
            if len(sj) < MIN_CENAS_JANELA:
                lin["gerada"] = False
                lin["nota"] = f"menos de {MIN_CENAS_JANELA} cenas"
                resumo.append(lin)
                logger.warning("%s %s: só %d cenas — não gerada", conjunto, janela, len(sj))
                continue
            alvo = [nome_comp(v, conjunto, janela) for v in ("lst-mediana", "n-obs", "anomalia-mediana", "persistencia-p90")]
            if all(x.exists() for x in alvo) and not forcar:
                lin.update(gerada=True, pixels_validos=int((ler_tif(alvo[1]) >= MIN_OBS).sum()), nota="já existia")
                resumo.append(lin)
                continue
            lst, anom, acima, ndvi, ndbi, mndwi = [], [], [], [], [], []
            for r in sj.itertuples():
                plat = r.plataforma.replace("-", "")
                c = {n: ler_tif(nome_derivado(n, plat, r.data)) for n in ("lst", "ndvi", "ndbi", "mndwi")}
                terra = (np.isfinite(c["lst"]) & np.isfinite(c["ndvi"]) & np.isfinite(c["ndbi"]) & np.isfinite(c["mndwi"])
                         & ~m["agua_osm"] & (c["mndwi"] <= 0))
                ref = c["lst"][terra & m["urbano"]]
                media, p90 = float(ref.mean()), float(np.percentile(ref, 90))
                lst.append(c["lst"])
                anom.append(c["lst"] - media)
                acima.append(np.where(np.isfinite(c["lst"]), (c["lst"] > p90).astype("float32"), np.nan))
                ndvi.append(c["ndvi"]), ndbi.append(c["ndbi"]), mndwi.append(c["mndwi"])
                if janela == "2013-2026":
                    por_cena.append({"conjunto": conjunto, "cena": r.id, "data": r.data, "n_terra_urbana": int(ref.size),
                                     "media_terra_urbana_c": round(media, 3), "p90_terra_urbana_c": round(p90, 3),
                                     "mediana_terra_rural_c": round(float(np.median(c["lst"][terra & m["rural"]])), 3)})
            st = np.stack(lst)
            n_obs = np.isfinite(st).sum(0)
            ok = n_obs >= MIN_OBS
            with np.errstate(all="ignore"), warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)  # pilhas só com NaN em pixels fora da AOI
                saidas = {
                    "lst-mediana": np.where(ok, np.nanmedian(st, 0), np.nan),
                    "n-obs": n_obs.astype("float32"),
                    "anomalia-mediana": np.where(ok, np.nanmedian(np.stack(anom), 0), np.nan),
                    # persistência só no núcleo urbanizado fora da água do OSM (m["urbano"] já exclui a água)
                    "persistencia-p90": np.where(ok & m["urbano"], np.nansum(np.stack(acima), 0) / np.maximum(n_obs, 1), np.nan),
                    "ndvi-mediana": np.where(ok, np.nanmedian(np.stack(ndvi), 0), np.nan),
                    "ndbi-mediana": np.where(ok, np.nanmedian(np.stack(ndbi), 0), np.nan),
                    "mndwi-mediana": np.where(ok, np.nanmedian(np.stack(mndwi), 0), np.nan),
                }
            saidas["n-obs"][~m["dentro"]] = np.nan
            regras = {"n_obs_minimo": MIN_OBS, "lst_mediana": "mediana por pixel da LST (°C) das observações limpas",
                      "anomalia": "LST da cena − média da LST de terra urbana da mesma cena; mediana por pixel",
                      "persistencia_p90": "fração das observações válidas do pixel com LST > p90 da LST de terra urbana da cena; "
                                          "só no núcleo urbanizado IBGE 2019 fora da água do OSM (fora dele, NaN)",
                      "terra_urbana_da_cena": "pixel limpo no núcleo urbanizado IBGE 2019 da AOI, fora dos polígonos de água do OSM e com MNDWI ≤ 0",
                      "indices": "medianas por pixel de NDVI, NDBI e MNDWI das mesmas observações"}
            for var, arr in saidas.items():
                lev.gravar_produto(arr, nome_comp(var, conjunto, janela), g["transform"])
                sidecar(nome_comp(var, conjunto, janela), fonte=FONTE, conjunto=conjunto, janela_veroes=janela,
                        cenas_usadas=sj["id"].tolist(), n_cenas=int(len(sj)), regras=regras, crs=CRS_PADRAO, resolucao_m=30,
                        criterio_conjunto=("≥ 95 % limpas, T1, 225/081, rio ≤ cota de alerta (750 cm)" if conjunto == "principal"
                                           else "≥ 80 % limpas, T1, 225/081, máscara por pixel, sem filtro do rio"),
                        janela_regra=JANELA_REGRA, status="extraido")
            lin.update(gerada=True, pixels_validos=int(ok.sum()), nota="")
            resumo.append(lin)
            logger.info("%s %s: %d cenas, %d pixels com n_obs ≥ %d", conjunto, janela, len(sj), ok.sum(), MIN_OBS)
    terra_mascarada(m, cenas, forcar)
    if por_cena:
        gravar_csv(pd.DataFrame(por_cena), PROC / f"referencia-urbana-por-cena_landsat-c2l2_{PERIODO}.csv", fonte=FONTE,
                   conteudo="média e p90 da LST de terra urbana por cena (referências da anomalia e da persistência)")
    rs = pd.DataFrame(resumo)
    gravar_csv(rs, PROC / f"composicao-resumo_landsat-c2l2_{PERIODO}.csv", fonte=FONTE, janelas=JANELAS, janela_regra=JANELA_REGRA,
               minimo_cenas_janela=MIN_CENAS_JANELA)
    return rs


JANELA_REGRA = ("verões inteiros, rotulados pelo ano de janeiro (dez/2018 + jan–mar/2019 = verão 2019; a cena de dez/2013 "
                "é do verão 2014); mínimo de 5 cenas")


def terra_mascarada(m: dict, cenas: pd.DataFrame, forcar: bool) -> None:
    """Versões "-terra" do principal (período inteiro): NaN na água do OSM ou com MNDWI mediano > 0; os originais ficam."""
    mndwi_arq = nome_comp("mndwi-mediana", "principal", "2013-2026")
    if not mndwi_arq.exists():
        return
    mndwi = ler_tif(mndwi_arq)
    agua = m["agua_osm"] | (np.isfinite(mndwi) & (mndwi > 0))
    with rasterio.open(mndwi_arq) as s:
        tr = s.transform
    for var in ("lst-mediana", "anomalia-mediana", "persistencia-p90"):
        orig, dest = nome_comp(var, "principal", "2013-2026"), nome_comp(f"{var}-terra", "principal", "2013-2026")
        if dest.exists() and not forcar:
            continue
        arr = ler_tif(orig)
        arr = np.where(agua, np.nan, arr).astype("float32")
        lev.gravar_produto(arr, dest, tr)
        meta_orig = json.loads(orig.with_suffix(".json").read_text(encoding="utf-8"))
        sidecar(dest, fonte=FONTE, conjunto="principal", janela_veroes="2013-2026", origem=orig.name,
                cenas_usadas=meta_orig.get("cenas_usadas"), n_cenas=meta_orig.get("n_cenas"), regras=meta_orig.get("regras"),
                crs=CRS_PADRAO, resolucao_m=30,
                transformacao_aplicada="cópia do original com NaN onde há polígono de água do OSM ou MNDWI mediano (principal, "
                                       "período inteiro) > 0; o original segue sem essa máscara",
                pixels_mascarados_agua=int((agua & np.isfinite(ler_tif(orig))).sum()), status="extraido")
        logger.info("gravado %s", dest.name)


# ---------------------------------------------------------------------------
# PARTE 4 — agregação e exploração
# ---------------------------------------------------------------------------
def carregar_comp(conjunto: str, janela: str) -> dict | None:
    arqs = {v: nome_comp(v, conjunto, janela) for v in ("lst-mediana", "anomalia-mediana", "persistencia-p90",
                                                       "ndvi-mediana", "ndbi-mediana", "mndwi-mediana")}
    if not all(x.exists() for x in arqs.values()):
        return None
    return {k: ler_tif(v) for k, v in arqs.items()}


def stats_unidade(idx: np.ndarray, n_total: np.ndarray, comp: dict, terra_val: np.ndarray, nmax: int) -> pd.DataFrame:
    """Estatísticas por unidade (índice inteiro por pixel; −1 = fora) sobre pixels válidos de terra.
    n_total = denominador da fração válida (todos os pixels da célula ou a parte de terra do polígono)."""
    sel = (idx >= 0) & terra_val
    d = pd.DataFrame({"u": idx[sel], "lst": comp["lst-mediana"][sel], "anom": comp["anomalia-mediana"][sel],
                      "pers": comp["persistencia-p90"][sel], "ndvi": comp["ndvi-mediana"][sel], "ndbi": comp["ndbi-mediana"][sel]})
    gr = d.groupby("u")
    out = pd.DataFrame({"lst_mediana_c": gr["lst"].median(), "anomalia_mediana_c": gr["anom"].median(),
                        "persistencia_p90_media": gr["pers"].mean(), "lst_mediana_p90_c": gr["lst"].quantile(0.9),
                        "ndvi_mediano": gr["ndvi"].median(), "ndbi_mediano": gr["ndbi"].median(), "n_pixels_validos": gr.size()})
    out = out.reindex(range(nmax))
    out["n_pixels_validos"] = out["n_pixels_validos"].fillna(0).astype(int)
    out["n_pixels_unidade"] = n_total
    out["fracao_valida"] = np.where(n_total > 0, out["n_pixels_validos"] / np.maximum(n_total, 1), 0.0)
    return out


def fracao_nucleo(idx: np.ndarray, urbano: np.ndarray, nmax: int) -> np.ndarray:
    """Fração dos pixels da unidade (todos, inclusive água) no núcleo urbanizado fora da água do OSM."""
    n_all = np.bincount(idx[idx >= 0], minlength=nmax)
    n_urb = np.bincount(idx[(idx >= 0) & urbano], minlength=nmax)
    return np.where(n_all > 0, n_urb / np.maximum(n_all, 1), 0.0)


def rotular(gdf: gpd.GeoDataFrame, g: dict) -> np.ndarray:
    """Índice da unidade por pixel (centro do pixel dentro do polígono)."""
    shapes = [(mapping(geom), i) for i, geom in enumerate(gdf.geometry)]
    return rasterize(shapes, out_shape=(g["h"], g["w"]), transform=g["transform"], fill=-1, dtype="int32")


def montar_grade(a: dict, g: dict) -> tuple[gpd.GeoDataFrame, np.ndarray]:
    tr, w, h = g["transform"], g["w"], g["h"]
    xs = tr.c + (np.arange(w) + 0.5) * 30
    ys = tr.f - (np.arange(h) + 0.5) * 30
    X, Y = np.meshgrid(xs, ys)
    cx, cy = np.floor(X / LADO_GRADE).astype(int), np.floor(Y / LADO_GRADE).astype(int)
    m = mascaras(a, g)
    chaves = pd.DataFrame({"cx": cx[m["dentro"]], "cy": cy[m["dentro"]]}).drop_duplicates().sort_values(["cy", "cx"])
    chaves = chaves.reset_index(drop=True)
    # só células inteiras dentro da extensão da grade de pixels (as de borda têm menos pixels e saem pela supressão)
    pos = {(x, y): i for i, (x, y) in enumerate(zip(chaves["cx"], chaves["cy"]))}
    idx = np.vectorize(lambda x, y: pos.get((x, y), -1), otypes=[np.int32])(cx, cy)
    geoms = [box(x * LADO_GRADE, y * LADO_GRADE, (x + 1) * LADO_GRADE, (y + 1) * LADO_GRADE) for x, y in zip(chaves["cx"], chaves["cy"])]
    cel = gpd.GeoDataFrame({"x_min": chaves["cx"] * LADO_GRADE, "y_min": chaves["cy"] * LADO_GRADE}, geometry=geoms, crs=CRS_PADRAO)
    return cel, idx


def moda_mapbiomas(g: dict, idx: np.ndarray, dentro: np.ndarray, n: int) -> np.ndarray:
    dst = np.zeros((g["h"], g["w"]), dtype="uint8")
    with rasterio.open(REF["mapbiomas"]) as s:
        # 27,3 m → 30 m por vizinho mais próximo: classe categórica, sem mistura
        reproject(rasterio.band(s, 1), dst, dst_transform=g["transform"], dst_crs=CRS_PADRAO, resampling=Resampling.nearest,
                  src_nodata=s.nodata, dst_nodata=0)
    sel = (idx >= 0) & dentro & (dst > 0)
    d = pd.DataFrame({"u": idx[sel], "c": dst[sel]})
    moda = d.groupby("u")["c"].agg(lambda s: s.value_counts().idxmax())
    return moda.reindex(range(n)).to_numpy()


def agregacao(a: dict, cenas: pd.DataFrame, g: dict, forcar: bool) -> dict:
    m = mascaras(a, g)
    res = {}
    comps = {(c, j): carregar_comp(c, j) for c in ("principal", "sensibilidade") for j in JANELAS}
    comps = {k: v for k, v in comps.items() if v is not None}
    base = comps[("principal", "2013-2026")]

    def terra_valida(comp):  # terra pela MNDWI mediana do próprio conjunto/janela
        return (np.isfinite(comp["lst-mediana"]) & ~m["agua_osm"] & m["dentro"]
                & np.isfinite(comp["mndwi-mediana"]) & (comp["mndwi-mediana"] <= 0))

    agua_px = m["agua_osm"] | (np.isfinite(base["mndwi-mediana"]) & (base["mndwi-mediana"] > 0))
    meta_comum = dict(fonte=FONTE, crs=CRS_PADRAO,
                      terra="pixel com lst_mediana válida (n_obs ≥ 10), fora dos polígonos de água do OSM e com MNDWI mediano ≤ 0",
                      supressao=f"unidade com menos de {MIN_FRAC_TERRA:.0%} de pixels válidos de terra (pixels de 30 m cujo centro cai "
                                "na unidade): estatísticas vazias, geometria mantida; denominador = todos os pixels da célula (grade) ou "
                                "só a parte de terra do polígono, fora da água do OSM (bairros e setores)",
                      persistencia=f"pixels do núcleo urbanizado IBGE 2019 fora da água; agregada só quando ≥ {MIN_FRAC_NUCLEO:.0%} dos "
                                   "pixels da unidade (todos, inclusive água) estão no núcleo (coluna fracao_nucleo), senão vazia",
                      estatisticas="lst_mediana_c = mediana da lst_mediana; anomalia_mediana_c = mediana da anomalia_mediana; "
                                   "persistencia_p90_media = média da persistencia_p90 (núcleo); lst_mediana_p90_c = p90 da lst_mediana",
                      janela_regra=JANELA_REGRA, status="extraido")

    # --- 4.1 grade de 250 m
    cel, idx = montar_grade(a, g)
    n = len(cel)
    n_total = np.bincount(idx[idx >= 0], minlength=n)
    st = stats_unidade(idx, n_total, base, terra_valida(base), n)
    frac_agua = np.bincount(idx[(idx >= 0) & agua_px], minlength=n) / np.maximum(n_total, 1)
    grade = cel.join(st.reset_index(drop=True))
    grade["fracao_agua"] = frac_agua.round(3)
    grade["agua_predominante"] = frac_agua > 0.5
    grade["fracao_nucleo"] = fracao_nucleo(idx, m["urbano"], n).round(4)
    grade.loc[grade["fracao_nucleo"] < MIN_FRAC_NUCLEO, "persistencia_p90_media"] = np.nan
    grade["suprimida"] = grade["fracao_valida"] < MIN_FRAC_TERRA
    grade["motivo_supressao"] = np.where(~grade["suprimida"], None,
                                         np.where(grade["agua_predominante"], "água predominante", "menos de 50 % de pixels válidos de terra"))
    sens = comps.get(("sensibilidade", "2013-2026"))
    if sens is not None:
        grade["lst_mediana_c_sensibilidade"] = stats_unidade(idx, n_total, sens, terra_valida(sens), n)["lst_mediana_c"].to_numpy()
    for j in SUBJANELAS:
        cj = comps.get(("principal", j))
        if cj is not None:
            grade[f"lst_mediana_c_{j.replace('-', '_')}"] = stats_unidade(idx, n_total, cj, terra_valida(cj), n)["lst_mediana_c"].to_numpy()
    cols_est = [c for c in grade.columns if c.startswith(("lst_", "anomalia_", "persistencia_", "ndvi_", "ndbi_"))]
    grade.loc[grade["suprimida"], cols_est] = np.nan
    # junções
    den = gpd.read_file(REF["densidade"])[["n_edificacoes", "geometry"]]
    b = den.geometry.bounds
    den_k = pd.Series(den["n_edificacoes"].to_numpy(), index=pd.MultiIndex.from_arrays([b["minx"].round().astype(int), b["miny"].round().astype(int)]))
    chave = pd.MultiIndex.from_arrays([grade["x_min"].astype(int), grade["y_min"].astype(int)])
    grade["n_edificacoes"] = den_k.reindex(chave).to_numpy()
    grade.loc[grade["suprimida"], "n_edificacoes"] = np.nan
    agua_u = a["agua"]
    grade["distancia_agua_m"] = grade.geometry.centroid.distance(agua_u).round(0)
    grade["mapbiomas_2024_dominante"] = moda_mapbiomas(g, idx, m["dentro"], n)
    grade.insert(0, "id_celula", [f"{int(x)}_{int(y)}" for x, y in zip(grade["x_min"], grade["y_min"])])
    arq = PROC / f"lst-grade_landsat-c2l2-principal_{PERIODO}_250m.gpkg"
    if arq.exists():
        arq.unlink()
    grade.to_file(arq, layer="lst_grade_250m", driver="GPKG")
    sidecar(arq, **meta_comum, conjunto="principal", janela="2013-2026",
            grade="células de 250 m alinhadas a múltiplos de 250 m em EPSG:31981 (mesma origem da grade de densidade de edificações); "
                  "entram as células com algum pixel da AOI",
            juncoes={"n_edificacoes": f"{REF['densidade'].name} pela célula idêntica; vazio se a célula foi suprimida lá ou aqui",
                     "distancia_agua_m": "do centroide da célula aos polígonos de água do OSM na AOI",
                     "mapbiomas_2024_dominante": "classe mais frequente da Coleção MapBiomas (2024) nos pixels da célula, reamostrada a 30 m por vizinho",
                     "colunas_extra": "lst_mediana_c da sensibilidade e das janelas do conjunto principal"},
            agua_predominante="mais de 50 % dos pixels da célula em água do OSM ou com MNDWI mediano > 0",
            n_celulas=int(n), n_suprimidas=int(grade["suprimida"].sum()))
    res["grade"] = grade
    logger.info("grade: %d células, %d suprimidas", n, grade["suprimida"].sum())

    # --- 4.2 bairros e setores, em todos os conjuntos/janelas
    bai = a["bairros"].reset_index(drop=True)
    seto = gpd.read_file(REF["setores"]).to_crs(CRS_PADRAO)
    seto = seto[seto["CD_SETOR"].astype(str).str.startswith(str(a["codigo_ibge"]))]
    seto = seto[seto["SITUACAO"].astype(str).str.lower().str.startswith("urban")]
    seto = seto[seto.geometry.representative_point().within(a["aoi"])].reset_index(drop=True)
    unidades = {"bairro": (bai, ["CD_BAIRRO", "NM_BAIRRO"]), "setor": (seto, ["CD_SETOR", "NM_BAIRRO", "populacao_total"])}
    longas = {}
    for nome, (gdf, chaves_u) in unidades.items():
        idx_u = rotular(gdf, g)
        # fração válida sobre a parte de terra do polígono: os bairros ribeirinhos incluem o leito do rio
        nt = np.bincount(idx_u[(idx_u >= 0) & ~m["agua_osm"]], minlength=len(gdf))
        n_todos = np.bincount(idx_u[idx_u >= 0], minlength=len(gdf))
        fn = fracao_nucleo(idx_u, m["urbano"], len(gdf))
        partes = []
        for (c, j), comp in comps.items():
            s = stats_unidade(idx_u, nt, comp, terra_valida(comp), len(gdf))
            s = s.rename(columns={"n_pixels_unidade": "n_pixels_terra_osm"})
            s.insert(s.columns.get_loc("n_pixels_terra_osm"), "n_pixels_unidade", n_todos)
            s["fracao_nucleo"] = fn
            s.loc[s["fracao_nucleo"] < MIN_FRAC_NUCLEO, "persistencia_p90_media"] = np.nan
            s = pd.concat([gdf[chaves_u].reset_index(drop=True), s.reset_index(drop=True)], axis=1)
            s["suprimida"] = s["fracao_valida"] < MIN_FRAC_TERRA
            s.loc[s["suprimida"], [x for x in s.columns if x.startswith(("lst_", "anomalia_", "persistencia_", "ndvi_", "ndbi_"))]] = np.nan
            s.insert(0, "janela", j)
            s.insert(0, "conjunto", c)
            partes.append(s)
        longa = pd.concat(partes, ignore_index=True)
        for x in ("lst_mediana_c", "anomalia_mediana_c", "lst_mediana_p90_c"):
            longa[x] = longa[x].round(3)
        for x in ("persistencia_p90_media", "ndvi_mediano", "ndbi_mediano", "fracao_valida", "fracao_nucleo"):
            longa[x] = longa[x].round(4)
        rotulo = "bairros_ibge-2022" if nome == "bairro" else "setores-urbanos_ibge-2022"
        nota_pop = ("populacao_total = coluna agregada do setor já existente no projeto (setores-censitarios_vulnerabilidade_unido.gpkg)"
                    if nome == "setor" else "26 bairros do IBGE 2022")
        gravar_csv(longa, PROC / f"lst-{rotulo}_landsat-c2l2_{PERIODO}.csv", **meta_comum, unidade=nome, nota=nota_pop,
                   formato="uma linha por unidade × conjunto × janela")
        princ = longa[(longa["conjunto"] == "principal") & (longa["janela"] == "2013-2026")].drop(columns=["conjunto", "janela"])
        gp = gdf[chaves_u + ["geometry"]].merge(princ, on=chaves_u, how="left")
        arq = PROC / f"lst-{rotulo}_landsat-c2l2-principal_{PERIODO}_vetorial.gpkg"
        if arq.exists():
            arq.unlink()
        gpd.GeoDataFrame(gp, geometry="geometry", crs=CRS_PADRAO).to_file(arq, layer=rotulo.replace("-", "_"), driver="GPKG")
        sidecar(arq, **meta_comum, unidade=nome, nota=nota_pop, conjunto="principal", janela="2013-2026",
                n_unidades=int(len(gdf)), n_suprimidas=int(princ["suprimida"].sum()))
        longas[nome] = longa
    res["bairros"], res["setores"] = longas["bairro"], longas["setor"]

    # --- 4.3 exploração
    res.update(exploracao(grade, longas["bairro"], comps, m))
    figuras(a, g, base, grade, m)
    return res


def spearman(x: pd.Series, y: pd.Series) -> tuple[float, int]:
    ok = x.notna() & y.notna()
    return (round(float(x[ok].corr(y[ok], method="spearman")), 3) if ok.sum() > 2 else None), int(ok.sum())


def exploracao(grade: gpd.GeoDataFrame, bai: pd.DataFrame, comps: dict, m: dict) -> dict:
    out = {}
    v = grade[~grade["suprimida"]]
    linhas = []
    for alvo in ("lst_mediana_c", "anomalia_mediana_c"):
        for expl in ("ndvi_mediano", "ndbi_mediano", "n_edificacoes", "distancia_agua_m"):
            r, nn = spearman(v[alvo], v[expl])
            linhas.append({"variavel": alvo, "explicativa": expl, "spearman": r, "n_celulas": nn})
    # n_edificacoes também com zero nas células sem registro na grade de densidade (sem edificação ou abaixo do limiar lá)
    for alvo in ("lst_mediana_c", "anomalia_mediana_c"):
        r, nn = spearman(v[alvo], v["n_edificacoes"].fillna(0))
        linhas.append({"variavel": alvo, "explicativa": "n_edificacoes (vazio = 0)", "spearman": r, "n_celulas": nn})
    sp = pd.DataFrame(linhas)
    gravar_csv(sp, PROC / f"lst-spearman-grade-250m_landsat-c2l2-principal_{PERIODO}.csv", fonte=FONTE,
               conteudo="Spearman entre as estatísticas da grade de 250 m (células não suprimidas) e explicativas")
    out["spearman"] = sp

    p = bai[(bai["conjunto"] == "principal") & (bai["janela"] == "2013-2026") & ~bai["suprimida"]]
    rk = []
    for var in ("lst_mediana_c", "anomalia_mediana_c", "persistencia_p90_media"):
        top = p.dropna(subset=[var]).sort_values(var, ascending=False).head(10)
        for pos, r in enumerate(top.itertuples(), 1):
            rk.append({"variavel": var, "posicao": pos, "cd_bairro": r.CD_BAIRRO, "nm_bairro": r.NM_BAIRRO, "valor": round(float(getattr(r, var)), 4)})
    rk = pd.DataFrame(rk)
    gravar_csv(rk, PROC / f"lst-ranking-bairros_landsat-c2l2-principal_{PERIODO}.csv", fonte=FONTE,
               conteudo="10 bairros mais quentes por lst_mediana, anomalia_mediana e persistência média (principal, 2013–2026); "
                        "persistência só em bairros com ≥ 30 % dos pixels no núcleo urbanizado")
    out["ranking"] = rk

    est = []
    janelas = SUBJANELAS
    for conj in ("principal", "sensibilidade"):
        for i, j1 in enumerate(janelas):
            for j2 in janelas[i + 1:]:
                a1 = bai[(bai["conjunto"] == conj) & (bai["janela"] == j1)].set_index("CD_BAIRRO")
                a2 = bai[(bai["conjunto"] == conj) & (bai["janela"] == j2)].set_index("CD_BAIRRO")
                if a1.empty or a2.empty:
                    continue
                for var in ("lst_mediana_c", "anomalia_mediana_c", "persistencia_p90_media"):
                    r, nn = spearman(a1[var], a2[var].reindex(a1.index))
                    est.append({"conjunto": conj, "janela_a": j1, "janela_b": j2, "variavel": var, "spearman_ranking_bairros": r, "n_bairros": nn})
    est = pd.DataFrame(est)
    gravar_csv(est, PROC / f"lst-estabilidade-janelas-bairros_landsat-c2l2_{PERIODO}.csv", fonte=FONTE,
               conteudo="Spearman dos rankings de bairros entre janelas (equivale ao Spearman dos valores)")
    out["estabilidade"] = est

    dif = []
    pc, sc = comps.get(("principal", "2013-2026")), comps.get(("sensibilidade", "2013-2026"))
    if sc is not None:
        for var in ("lst-mediana", "anomalia-mediana", "persistencia-p90"):
            d = (sc[var] - pc[var])[m["urbano"]]
            d = d[np.isfinite(d)]
            dif.append({"nivel": "pixel (área urbana)", "variavel": var, "n": int(d.size), "mediana_dif": round(float(np.median(d)), 3),
                        "p5_dif": round(float(np.percentile(d, 5)), 3), "p95_dif": round(float(np.percentile(d, 95)), 3),
                        "media_abs_dif": round(float(np.abs(d).mean()), 3), "spearman": None})
        a1 = bai[(bai["conjunto"] == "principal") & (bai["janela"] == "2013-2026")].set_index("CD_BAIRRO")
        a2 = bai[(bai["conjunto"] == "sensibilidade") & (bai["janela"] == "2013-2026")].set_index("CD_BAIRRO").reindex(a1.index)
        for var in ("lst_mediana_c", "anomalia_mediana_c", "persistencia_p90_media"):
            d = (a2[var] - a1[var]).dropna()
            r, nn = spearman(a1[var], a2[var])
            dif.append({"nivel": "bairro", "variavel": var, "n": nn, "mediana_dif": round(float(d.median()), 3),
                        "p5_dif": round(float(d.quantile(0.05)), 3), "p95_dif": round(float(d.quantile(0.95)), 3),
                        "media_abs_dif": round(float(d.abs().mean()), 3), "spearman": r})
    dif = pd.DataFrame(dif)
    gravar_csv(dif, PROC / f"lst-principal-x-sensibilidade_landsat-c2l2_{PERIODO}.csv", fonte=FONTE,
               conteudo="sensibilidade − principal (período inteiro): pixels da área urbana e bairros")
    out["principal_x_sensibilidade"] = dif

    # resumo urbano/rural
    pc_l, pc_a = pc["lst-mediana"], pc["anomalia-mediana"]
    terra = np.isfinite(pc_l) & ~m["agua_osm"] & np.isfinite(pc["mndwi-mediana"]) & (pc["mndwi-mediana"] <= 0)
    out["zonas"] = {"lst_mediana_urbana_c": round(float(np.median(pc_l[terra & m["urbano"]])), 2),
                    "lst_mediana_rural_c": round(float(np.median(pc_l[terra & m["rural"]])), 2),
                    "anomalia_urbana_p5_c": round(float(np.percentile(pc_a[terra & m["urbano"]], 5)), 2),
                    "anomalia_urbana_p95_c": round(float(np.percentile(pc_a[terra & m["urbano"]], 95)), 2),
                    "anomalia_urbana_mediana_c": round(float(np.median(pc_a[terra & m["urbano"]])), 2)}
    (PROC / f"lst-resumo-zonas_landsat-c2l2-principal_{PERIODO}.json").write_text(
        json.dumps({**out["zonas"], "script": SCRIPT, "terra": "fora da água do OSM e MNDWI mediano ≤ 0",
                    "zonas": "urbano = núcleo urbanizado IBGE 2019 na AOI; rural = resto da AOI", "status": "extraido"},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def _mapa(ax, arr, g, a, cmap, titulo, rotulo, norm=None, vmin=None, vmax=None):
    tr = g["transform"]
    ext = (tr.c, tr.c + g["w"] * 30, tr.f - g["h"] * 30, tr.f)
    ax.set_facecolor("#d9d8d4")  # fundo neutro = sem dado / máscara
    im = ax.imshow(arr, extent=ext, cmap=cmap, norm=norm, vmin=vmin, vmax=vmax, interpolation="nearest")
    gpd.GeoSeries([a["aoi"]], crs=CRS_PADRAO).boundary.plot(ax=ax, color="#52514e", linewidth=0.6)
    a["bairros"].boundary.plot(ax=ax, color="#1f1f1f", linewidth=0.35, alpha=0.8)
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_title(titulo, fontsize=9, loc="left", color="#0b0b0b")
    return im, rotulo


def figuras(a: dict, g: dict, base: dict, grade: gpd.GeoDataFrame, m: dict) -> None:
    nota = ("Landsat 8/9 C2 L2 (USGS) via Planetary Computer; verões 2014–2026 (dez/2013–mar/2026), conjunto principal; "
            "contornos: AOI e bairros IBGE 2022; cinza = sem dado/máscara")
    lst = base["lst-mediana"]
    v = lst[np.isfinite(lst)]
    an = base["anomalia-mediana"]
    terra = m["dentro"] & ~m["agua_osm"] & (base["mndwi-mediana"] <= 0)
    lo, hi = (float(x) for x in np.nanpercentile(an[terra], [2, 98]))  # pela terra: o rio (≈ −10 °C) achataria o lado quente
    defs = [
        ("lst-mediana", lst, "OrRd", "Temperatura de superfície — mediana de verão (°C)", "LST (°C), escala p2–p98",
         dict(vmin=np.percentile(v, 2), vmax=np.percentile(v, 98))),
        ("anomalia-mediana", np.clip(an, lo, hi), "RdBu_r", "Anomalia em relação à média urbana da cena — mediana (°C)",
         "°C (0 = média da terra urbana; classes de p2 a p98 da terra)",
         dict(norm=BoundaryNorm(np.round(np.r_[np.linspace(lo, 0, 7), np.linspace(0, hi, 7)[1:]], 2), 256))),
        ("persistencia-p90", base["persistencia-p90"], "Purples", "Persistência: fração das cenas acima do p90 urbano (só núcleo urbanizado)",
         "fração das observações (0–1)", dict(vmin=0, vmax=1)),
    ]
    for var, arr, cmap, tit, rot, kw in defs:
        fig, ax = plt.subplots(figsize=(8, 6), dpi=150)
        im, _ = _mapa(ax, arr, g, a, cmap, tit, rot, **kw)
        cb = fig.colorbar(im, ax=ax, shrink=0.75)
        cb.set_label(rot, fontsize=8)
        fig.text(0.01, 0.01, nota, fontsize=6.3, color="#52514e")
        fig.tight_layout(rect=(0, 0.03, 1, 1))
        arq = PROC / f"{var}_landsat-c2l2-principal_{PERIODO}_30m_mapa.png"
        fig.savefig(arq, facecolor="#fcfcfb")
        plt.close(fig)
        sidecar(arq, fonte=FONTE, conteudo=f"{tit}; sem pontos de endereço", status="extraido")
    fig, ax = plt.subplots(figsize=(8, 6), dpi=150)
    ax.set_facecolor("#d9d8d4")
    ok = grade[~grade["suprimida"]]
    ok.plot(ax=ax, column="lst_mediana_c", cmap="OrRd", linewidth=0, legend=True,
            vmin=np.percentile(v, 2), vmax=np.percentile(v, 98), legend_kwds={"shrink": 0.75, "label": "mediana da LST (°C) na célula"})
    grade[grade["suprimida"]].plot(ax=ax, color="#bdbcb8", linewidth=0)
    gpd.GeoSeries([a["aoi"]], crs=CRS_PADRAO).boundary.plot(ax=ax, color="#52514e", linewidth=0.6)
    a["bairros"].boundary.plot(ax=ax, color="#1f1f1f", linewidth=0.35, alpha=0.8)
    tr = g["transform"]
    ax.set_xlim(tr.c, tr.c + g["w"] * 30)
    ax.set_ylim(tr.f - g["h"] * 30, tr.f)
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_title("Temperatura de superfície de verão em grade de 250 m (°C)", fontsize=9, loc="left")
    fig.text(0.01, 0.01, nota.replace("; cinza = sem dado/máscara", ";\ncinza claro = célula suprimida (< 50 % de pixels válidos de terra); cinza de fundo = fora da AOI"),
             fontsize=6.3, color="#52514e")
    fig.tight_layout(rect=(0, 0.05, 1, 1))
    arq = PROC / f"lst-grade_landsat-c2l2-principal_{PERIODO}_250m_mapa.png"
    fig.savefig(arq, facecolor="#fcfcfb")
    plt.close(fig)
    sidecar(arq, fonte=FONTE, conteudo="mediana da LST por célula de 250 m; sem pontos de endereço", status="extraido")


# ---------------------------------------------------------------------------
def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("etapa", choices=["selecao", "recortes", "composicao", "agregacao", "todos"])
    ap.add_argument("--codigo-ibge", default="4322400", help="código IBGE do município (filtra bairros e setores)")
    ap.add_argument("--forcar", action="store_true", help="refaz leituras e produtos já existentes")
    o = ap.parse_args()
    a = lev.montar_aoi(o.codigo_ibge)
    a["codigo_ibge"] = o.codigo_ibge
    try:
        cenas = selecao(a, o.forcar and o.etapa in ("selecao", "todos"))
        if o.etapa == "selecao":
            return
        g = recortes(a, cenas, o.forcar and o.etapa in ("recortes", "todos"))
        if o.etapa in ("composicao", "todos"):
            composicao(a, cenas, g, o.forcar)
        if o.etapa in ("agregacao", "todos"):
            res = agregacao(a, cenas, g, o.forcar)
            logger.info("zonas: %s", res["zonas"])
    except PermissionError as e:
        logger.error("acesso recusado: %s", e)
        sys.exit(2)


if __name__ == "__main__":
    main()
