"""
Levantamento exploratório de temperatura de superfície (LST) da área urbana
(exposição ao calor urbano), rodada 72.

Não integra nada ao portal: inventaria cenas, testa o cálculo em 3 cenas e
consulta outras fontes, para decidir depois o desenho do método.

Área de interesse (AOI): malha urbanizada já usada no projeto — áreas
urbanizadas do IBGE (2019) a até 2 km dos bairros do IBGE (2022), unidas aos
próprios bairros (cobre as partes novas da cidade) — com buffer de 2 km,
recortada pelo município (config/area_estudo.geojson). O lado argentino do rio
fica de fora para não entrar como "rural" nas estatísticas.

Etapas (subcomandos):
    inventario  STAC público do Planetary Computer (coleção landsat-c2-l2, token
                anônimo): Landsat 8 e 9 (2013 até hoje) cuja pegada contém a
                AOI, com a fração de pixels limpos na AOI pelo QA_PIXEL (lido só
                na janela da AOI); Landsat 5 e 7 apenas como inventário (sem
                leitura de pixels). Tabelas, resumo por ano e estação e gráfico.
    teste       3 cenas de verão (dez–mar) com ≥ 95 % de pixels limpos, em anos
                diferentes: recortes brutos (data/raw/raster/lst_landsat/), LST
                em °C, NDVI, NDBI e MNDWI a 30 m em EPSG:31981
                (data/processed/lst/), estatísticas na AOI, por zona e por
                bairro (agregadas), sanidade, correlações e um PNG por cena.
    modis       MOD11A1/MYD11A1 v6.1 (coleção modis-11A1-061): série diária
                2015–hoje numa janela 3×3 (1 km) no centro urbano, dia e noite.
    fontes      ECOSTRESS (metadados no CMR da NASA, sem baixar), Sentinel-3
                SLSTR (só acesso e resolução) e estação do INMET (série horária
                já baixada pelo projeto, comparada nas datas das cenas de teste).
    todos       As quatro etapas em sequência.

Regras:
    - Só leitura por janela de COG; nenhuma cena inteira é baixada. O token
      anônimo fica só na memória (nunca em arquivo, log ou metadado).
    - Idempotente: QA por cena e leituras do MODIS ficam em cache
      (data/raw/lst_levantamento/); recortes e produtos existentes não são
      refeitos. --forcar refaz tudo.
    - Nenhum ponto de endereço em produto algum; bairros só em tabela agregada.
    - Tabelas de levantamento vão para --saida-tabelas (padrão:
      data/raw/lst_levantamento/tabelas/, fora do git).

Uso:
    python scripts/exploracao/lst_levantamento.py todos --codigo-ibge 4322400
    python scripts/exploracao/lst_levantamento.py teste --cenas <id1> <id2> <id3>
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import geopandas as gpd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import rasterio  # noqa: E402
from rasterio.features import geometry_mask, rasterize  # noqa: E402
from rasterio.transform import Affine  # noqa: E402
from rasterio.warp import Resampling, reproject, transform_bounds  # noqa: E402
from rasterio.windows import Window, from_bounds  # noqa: E402
from shapely.geometry import mapping, shape  # noqa: E402

RAIZ = Path(__file__).resolve().parents[2]
sys.path.append(str(RAIZ / "scripts" / "utils"))
sys.path.append(str(RAIZ / "scripts" / "download"))
from recorte_municipio import CRS_PADRAO, carregar_area_estudo  # noqa: E402
from sentinel_planetary_computer import buscar, licenca, pedir, sem_token, sha256  # noqa: E402

logger = logging.getLogger(__name__)

SCRIPT = "scripts/exploracao/lst_levantamento.py"
STAC = "https://planetarycomputer.microsoft.com/api/stac/v1"
SAS = "https://planetarycomputer.microsoft.com/api/sas/v1/token"
CMR = "https://cmr.earthdata.nasa.gov/search"
COL_LANDSAT = "landsat-c2-l2"
COL_MODIS = "modis-11A1-061"
COL_SLSTR = "sentinel-3-slstr-lst-l2-netcdf"
REF = {
    "urbano": RAIZ / "data/raw/vetor/areas-urbanizadas_ibge_2019_recorte-municipio.gpkg",
    "bairros": RAIZ / "data/processed/bairros/bairros_ibge_2022_vetorial.gpkg",
    "agua": RAIZ / "data/raw/vetor/hidrografia-area-agua_osm_atual_vetorial.gpkg",
    "inmet_serie": RAIZ / "data/raw/clima_inmet_historico-completo_horario.parquet",
    "inmet_estacoes": RAIZ / "data/raw/vetor/estacoes-clima_inmet_atual_vetorial.gpkg",
}
PASTA = RAIZ / "data" / "raw" / "lst_levantamento"
BRUTO_LANDSAT = RAIZ / "data" / "raw" / "raster" / "lst_landsat"
BRUTO_MODIS = RAIZ / "data" / "raw" / "modis"
PROCESSADO = RAIZ / "data" / "processed" / "lst"
BUFFER_M = 2000.0  # buffer da malha urbana que define a AOI
DIST_NUCLEO_M = 2000.0  # áreas urbanizadas a até esta distância dos bairros formam o núcleo urbano
# fatores da Coleção 2 nível 2 (USGS)
ST_ESCALA, ST_DESLOC = 0.00341802, 149.0  # ST_B10 -> kelvin
SR_ESCALA, SR_DESLOC = 0.0000275, -0.2  # SR_Bx -> reflectância
# QA_PIXEL: bit 0 preenchimento; 1 nuvem dilatada; 2 cirro; 3 nuvem; 4 sombra de nuvem; 5 neve
QA_SUJO = (1 << 1) | (1 << 2) | (1 << 3) | (1 << 4) | (1 << 5)
ATIVOS_TESTE = {"lwir11": "ST_B10", "qa_pixel": "QA_PIXEL", "green": "SR_B3", "red": "SR_B4", "nir08": "SR_B5", "swir16": "SR_B6"}
ESTACAO = {12: "DJF", 1: "DJF", 2: "DJF", 3: "MAM", 4: "MAM", 5: "MAM", 6: "JJA", 7: "JJA", 8: "JJA", 9: "SON", 10: "SON", 11: "SON"}
MESES_VERAO = (12, 1, 2, 3)
AREA_RIO_HA = 500.0  # polígonos de água maiores que isto = rio principal (teste de sanidade)
EROSAO_RIO_M = 90.0  # 3 pixels de 30 m longe da margem, sem mistura com terra
_TOKENS: dict[str, tuple[str, float]] = {}


# ---------------------------------------------------------------------------
# utilidades
# ---------------------------------------------------------------------------
def token_href(href: str) -> str:
    """Token anônimo de leitura da conta/contêiner do arquivo (só na memória).

    O token por coleção não serve para todas (o MODIS fica em outra conta de
    armazenamento); por isso é pedido por conta/contêiner, tirados do endereço.
    """
    m = re.match(r"https://([^.]+)\.blob\.core\.windows\.net/([^/]+)/", href)
    if not m:
        raise ValueError(f"endereço fora do armazenamento do Planetary Computer: {sem_token(href)}")
    chave = f"{m.group(1)}/{m.group(2)}"
    tk, validade = _TOKENS.get(chave, ("", 0.0))
    if time.time() > validade - 300:
        r = pedir("GET", f"{SAS}/{chave}", timeout=60)
        if r.status_code in (401, 403):
            raise PermissionError(f"o serviço de token recusou o acesso anônimo a {chave} (HTTP {r.status_code})")
        r.raise_for_status()
        j = r.json()
        tk = j["token"]
        validade = datetime.fromisoformat(j["msft:expiry"].replace("Z", "+00:00")).timestamp()
        _TOKENS[chave] = (tk, validade)
    return tk


def abrir(href: str):
    return rasterio.open(f"{href}?{token_href(href)}")


def com_tentativas(func, *args, n: int = 3):
    for t in range(n):
        try:
            return func(*args)
        except Exception as e:  # a mensagem do GDAL traz o endereço: nunca é propagada com o token
            if t == n - 1:
                raise RuntimeError(sem_token(e)) from None
            time.sleep(4 * (t + 1))


def sidecar(caminho: Path, **campos) -> None:
    """Metadado irmão (.json); CSV/PNG ganham <nome>.<ext>.json."""
    meta = {"arquivo": caminho.name, "data_processamento": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "script": SCRIPT, "uso": "levantamento (rodada 72); não adotado"}
    meta.update(campos)
    if caminho.exists() and caminho.is_file():
        meta["tamanho_bytes"] = caminho.stat().st_size
        meta["sha256"] = sha256(caminho)
    destino = caminho.with_suffix(".json") if caminho.suffix == ".tif" else caminho.with_name(caminho.name + ".json")
    destino.write_text(json.dumps(meta, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def gravar_csv(df: pd.DataFrame, caminho: Path, **campos) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(caminho, index=False)
    sidecar(caminho, **campos)
    logger.info("gravado %s (%d linhas)", caminho.name, len(df))


def hora_solar(dt_utc: datetime, lon: float) -> str:
    """Hora solar verdadeira aproximada: UTC + longitude/15 + equação do tempo."""
    n = dt_utc.timetuple().tm_yday
    b = 2 * math.pi * (n - 81) / 364
    eqt_min = 9.87 * math.sin(2 * b) - 7.53 * math.cos(b) - 1.5 * math.sin(b)
    local = dt_utc + timedelta(minutes=lon * 4 + eqt_min)
    return local.strftime("%H:%M")


# ---------------------------------------------------------------------------
# área de interesse e zonas
# ---------------------------------------------------------------------------
def montar_aoi(codigo_ibge: str) -> dict:
    """AOI (EPSG:31981), núcleo urbano, zonas e bairros do município."""
    mun = carregar_area_estudo()
    g_mun = mun.union_all()
    bai = gpd.read_file(REF["bairros"]).to_crs(CRS_PADRAO)
    bai = bai[bai["CD_BAIRRO"].astype(str).str.startswith(str(codigo_ibge))].copy()
    if bai.empty:
        raise ValueError(f"nenhum bairro do IBGE com código iniciado em {codigo_ibge} em {REF['bairros'].name}")
    urb = gpd.read_file(REF["urbano"]).to_crs(CRS_PADRAO)
    g_bai = bai.union_all()
    urb_nucleo = urb[urb.distance(g_bai) <= DIST_NUCLEO_M]  # descarta núcleos urbanizados isolados do interior
    nucleo = urb_nucleo.union_all().union(g_bai)
    aoi = nucleo.buffer(BUFFER_M).intersection(g_mun)
    agua = gpd.read_file(REF["agua"]).to_crs(CRS_PADRAO)
    agua = agua[agua.intersects(aoi)]
    g_agua = agua.union_all().intersection(aoi)
    rio = agua[agua["area_ha"] > AREA_RIO_HA].union_all().intersection(aoi).buffer(-EROSAO_RIO_M)
    g_urb = urb_nucleo.union_all().intersection(aoi).difference(g_agua)
    return {"aoi": aoi, "nucleo": nucleo, "urbano": g_urb, "agua": g_agua, "rio": rio, "bairros": bai,
            "aoi_ll": gpd.GeoSeries([aoi], crs=CRS_PADRAO).to_crs("EPSG:4326").iloc[0]}


def descrever_aoi(a: dict) -> dict:
    b = a["aoi"].bounds
    bl = a["aoi_ll"].bounds
    c = a["aoi_ll"].centroid
    return {"area_km2": round(a["aoi"].area / 1e6, 2), "area_nucleo_urbano_km2": round(a["nucleo"].area / 1e6, 2),
            "area_zona_urbana_km2": round(a["urbano"].area / 1e6, 2), "area_agua_km2": round(a["agua"].area / 1e6, 2),
            "bbox_epsg31981": [round(v, 1) for v in b], "bbox_epsg4326": [round(v, 5) for v in bl],
            "centroide_lonlat": [round(c.x, 5), round(c.y, 5)], "bairros": int(len(a["bairros"])),
            "definicao": f"áreas urbanizadas IBGE 2019 a até {DIST_NUCLEO_M:.0f} m dos bairros IBGE 2022 ∪ bairros; buffer de "
                         f"{BUFFER_M:.0f} m; recorte pelo município (lado argentino do rio fora)"}


# ---------------------------------------------------------------------------
# PARTE 1 — inventário
# ---------------------------------------------------------------------------
def buscar_anos(colecao: str, ano_ini: int, ano_fim: int, bbox_ll: tuple) -> list[dict]:
    itens = []
    for ano in range(ano_ini, ano_fim + 1):
        itens += buscar(colecao, f"{ano}-01-01/{ano}-12-31", bbox_ll)
    return itens


def fracao_limpa(item: dict, aoi_ll) -> dict:
    """Lê o QA_PIXEL só na janela da AOI e conta pixels limpos dentro dela."""
    href = item["assets"]["qa_pixel"]["href"]

    def ler():
        with abrir(href) as src:
            g = gpd.GeoSeries([aoi_ll], crs="EPSG:4326").to_crs(src.crs).iloc[0]
            jan = from_bounds(*g.bounds, transform=src.transform).round_offsets().round_lengths()
            jan = jan.intersection(Window(0, 0, src.width, src.height))
            qa = src.read(1, window=jan)
            dentro = ~geometry_mask([mapping(g)], out_shape=qa.shape, transform=src.window_transform(jan))
            return qa, dentro

    qa, dentro = com_tentativas(ler)
    n = int(dentro.sum())
    preenchido = dentro & ((qa & 1) == 0)
    limpo = preenchido & ((qa & QA_SUJO) == 0)
    return {"px_aoi": n, "frac_preenchida_aoi": round(preenchido.sum() / n, 4), "frac_limpa_aoi": round(limpo.sum() / n, 4)}


def linha_item(item: dict, lon: float) -> dict:
    p = item["properties"]
    dt = datetime.fromisoformat(p["datetime"].replace("Z", "+00:00"))
    return {"id": item["id"], "plataforma": p["platform"], "data": dt.date().isoformat(), "ano": dt.year, "mes": dt.month,
            "estacao": ESTACAO[dt.month], "hora_utc": dt.strftime("%H:%M"), "hora_solar_local_aprox": hora_solar(dt, lon),
            "wrs_path_row": f"{p.get('landsat:wrs_path')}/{p.get('landsat:wrs_row')}", "categoria": p.get("landsat:collection_category"),
            "cloud_cover_cena": p.get("eo:cloud_cover"), "cloud_cover_terra": p.get("landsat:cloud_cover_land"),
            "elevacao_solar": round(p.get("view:sun_elevation", float("nan")), 2)}


def inventario(a: dict, saida: Path, forcar: bool) -> pd.DataFrame:
    bbox = tuple(a["aoi_ll"].bounds)
    lon = a["aoi_ll"].centroid.x
    hoje = date.today().year
    PASTA.mkdir(parents=True, exist_ok=True)
    cache_arq = PASTA / "qa_por_cena_cache.json"
    cache = {} if forcar or not cache_arq.exists() else json.loads(cache_arq.read_text(encoding="utf-8"))

    logger.info("buscando Landsat 2013–%d no STAC", hoje)
    novos = buscar_anos(COL_LANDSAT, 2013, hoje, bbox)
    alvo = [i for i in novos if i["properties"]["platform"] in ("landsat-8", "landsat-9")
            and shape(i["geometry"]).contains(a["aoi_ll"])]
    logger.info("%d itens L8/L9 com pegada contendo a AOI (de %d no retângulo)", len(alvo), len(novos))
    faltam = [i for i in alvo if i["id"] not in cache]
    logger.info("QA a ler: %d (em cache: %d)", len(faltam), len(alvo) - len(faltam))

    def tratar(it):
        try:
            return it["id"], fracao_limpa(it, a["aoi_ll"])
        except Exception as e:
            logger.warning("QA falhou em %s: %s", it["id"], e)
            return it["id"], None

    with ThreadPoolExecutor(12) as ex:
        for k, (iid, res) in enumerate(ex.map(tratar, faltam), 1):
            if res is not None:
                cache[iid] = res
            if k % 100 == 0:
                logger.info("  %d/%d", k, len(faltam))
                cache_arq.write_text(json.dumps(cache), encoding="utf-8")
    cache_arq.write_text(json.dumps(cache), encoding="utf-8")

    linhas = []
    for it in alvo:
        d = linha_item(it, lon)
        d.update(cache.get(it["id"], {"px_aoi": None, "frac_preenchida_aoi": None, "frac_limpa_aoi": None}))
        linhas.append(d)
    inv = pd.DataFrame(linhas).sort_values(["data", "plataforma"]).reset_index(drop=True)
    comum = dict(fonte=f"Microsoft Planetary Computer — STAC {STAC}, coleção {COL_LANDSAT} (USGS Landsat Collection 2 Level-2)",
                 aoi=descrever_aoi(a), mascara_qa="limpo = preenchido e bits 1–5 do QA_PIXEL zerados (nuvem dilatada, cirro, nuvem, "
                 "sombra, neve)", hora_solar="UTC + longitude do centroide da AOI/15 + equação do tempo (aproximada)")
    gravar_csv(inv, saida / "cenas_inventario.csv", **comum,
               filtro="Landsat 8 e 9, 2013 até hoje, pegada do item contendo a AOI inteira (path/row 225/081)")

    # Landsat 5 e 7: só inventário (metadados do catálogo)
    antigos = buscar_anos(COL_LANDSAT, 1984, 2012, bbox) + [i for i in novos if i["properties"]["platform"] == "landsat-7"]
    antigos = [i for i in antigos if i["properties"]["platform"] in ("landsat-5", "landsat-7")]
    la = []
    for it in antigos:
        d = linha_item(it, lon)
        d["pegada_contem_aoi"] = bool(shape(it["geometry"]).contains(a["aoi_ll"]))
        la.append(d)
    inv57 = pd.DataFrame(la).sort_values(["data", "plataforma"]).reset_index(drop=True) if la else pd.DataFrame()
    gravar_csv(inv57, saida / "cenas_inventario_landsat5_7.csv", **comum,
               filtro="Landsat 5 e 7 que tocam o retângulo da AOI; só metadados do catálogo (sem leitura de pixels)")

    # resumo por ano e estação (ano civil da cena; DJF = dez, jan e fev do mesmo ano civil)
    r = inv.assign(ge95=inv["frac_limpa_aoi"] >= 0.95, ge80=inv["frac_limpa_aoi"] >= 0.80)
    resumo = r.groupby(["ano", "estacao"]).agg(n_cenas=("id", "size"), n_limpas_ge95=("ge95", "sum"),
                                              n_limpas_ge80=("ge80", "sum")).reset_index()
    resumo["estacao"] = pd.Categorical(resumo["estacao"], ["DJF", "MAM", "JJA", "SON"], ordered=True)
    resumo = resumo.sort_values(["ano", "estacao"])
    gravar_csv(resumo, saida / "resumo_por_ano_e_estacao.csv", **comum,
               nota="ano civil da cena; DJF = dezembro, janeiro e fevereiro do mesmo ano civil; Landsat 8 e 9 juntos")
    grafico_inventario(r, saida / "cenas_limpas_por_ano.png")
    return inv


def grafico_inventario(r: pd.DataFrame, caminho: Path) -> None:
    por_ano = r.groupby("ano").agg(ge95=("ge95", "sum"), ge80=("ge80", "sum"), n=("id", "size"))
    fig, ax = plt.subplots(figsize=(9, 4.2), dpi=150)
    x = np.arange(len(por_ano))
    w = 0.38
    ax.bar(x - w / 2 - 0.01, por_ano["ge95"], w, color="#2a78d6", label="≥ 95 % de pixels limpos na AOI")
    ax.bar(x + w / 2 + 0.01, por_ano["ge80"], w, color="#eb6834", label="≥ 80 % de pixels limpos na AOI")
    ax.set_xticks(x, por_ano.index.astype(str))
    ax.set_ylabel("cenas Landsat 8/9")
    ax.set_title("Cenas limpas por ano sobre a área urbana (Landsat 8 e 9, path/row 225/081)", fontsize=10, loc="left", color="#0b0b0b")
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.grid(axis="y", color="#e6e5e1", linewidth=0.8)
    ax.set_axisbelow(True)
    ax.legend(frameon=False, fontsize=8, loc="upper left")
    nota = "2013 e o último ano são parciais (L8 desde abr/2013; L9 desde nov/2021)."
    fig.text(0.01, 0.01, nota, fontsize=7, color="#52514e")
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    fig.savefig(caminho, facecolor="#fcfcfb")
    plt.close(fig)
    sidecar(caminho, conteudo="nº de cenas L8/L9 por ano com ≥ 95 % e ≥ 80 % de pixels limpos na AOI")


# ---------------------------------------------------------------------------
# PARTE 2 — teste em 3 cenas
# ---------------------------------------------------------------------------
def escolher_cenas(inv: pd.DataFrame, n: int = 3) -> list[str]:
    """Melhores cenas de verão: ≥ 95 % limpas, T1, mais limpas e de pico do verão (jan/fev); anos e verões distintos."""
    v = inv[inv["mes"].isin(MESES_VERAO) & (inv["frac_limpa_aoi"] >= 0.95)].copy()
    v["verao"] = np.where(v["mes"] == 12, v["ano"] + 1, v["ano"])  # dez entra no verão do ano seguinte
    v["pico"] = v["mes"].isin((1, 2)).astype(int)
    v["t1"] = (v["categoria"] == "T1").astype(int)
    v = v.sort_values(["frac_limpa_aoi", "t1", "pico", "elevacao_solar"], ascending=False)
    escolha, anos, veroes = [], set(), set()
    for _, r in v.iterrows():
        if r["ano"] in anos or r["verao"] in veroes:
            continue
        escolha.append(r["id"])
        anos.add(r["ano"])
        veroes.add(r["verao"])
        if len(escolha) == n:
            break
    return escolha


def grade_alvo(src_transform: Affine, src_crs, aoi) -> tuple[Affine, int, int]:
    """Grade de 30 m em EPSG:31981 alinhada às bordas de pixel da cena.

    A cena vem em WGS 84 / UTM 21N (EPSG:32621, norte negativo) e o projeto usa
    SIRGAS 2000 / UTM 21S: mesma projeção, só muda o falso norte (10.000 km) e o
    datum (deslocamento nulo no PROJ). Com as bordas alinhadas, a reprojeção por
    vizinho mais próximo é uma translação exata, sem reamostrar valores.
    """
    x0 = src_transform.c
    y0 = src_transform.f + 10_000_000 if str(src_crs).endswith("32621") else src_transform.f
    xmin, ymin, xmax, ymax = aoi.bounds
    xmin = x0 + math.floor((xmin - x0) / 30) * 30
    xmax = x0 + math.ceil((xmax - x0) / 30) * 30
    ymax = y0 + math.ceil((ymax - y0) / 30) * 30
    ymin = y0 + math.floor((ymin - y0) / 30) * 30
    return Affine(30, 0, xmin, 0, -30, ymax), int(round((xmax - xmin) / 30)), int(round((ymax - ymin) / 30))


def baixar_recortes(item: dict, a: dict, forcar: bool) -> dict:
    """Janela de cada ativo (grade e CRS originais da cena) + .json com sha256."""
    BRUTO_LANDSAT.mkdir(parents=True, exist_ok=True)
    p = item["properties"]
    dia = p["datetime"][:10]
    plat = p["platform"].replace("-", "")
    caminhos = {}
    for ativo, banda in ATIVOS_TESTE.items():
        arq = BRUTO_LANDSAT / f"landsat-recorte-{banda.lower().replace('_', '-')}_{plat}-c2l2_{dia}_30m.tif"
        caminhos[ativo] = arq
        meta_arq = arq.with_suffix(".json")
        if arq.exists() and meta_arq.exists() and not forcar:
            m = json.loads(meta_arq.read_text(encoding="utf-8"))
            if m.get("cena") == item["id"] and m.get("sha256") == sha256(arq):
                continue
        href = item["assets"][ativo]["href"]

        def ler():
            with abrir(href) as src:
                g = gpd.GeoSeries([a["aoi_ll"]], crs="EPSG:4326").to_crs(src.crs).iloc[0]
                b = g.buffer(150).bounds  # 5 pixels de folga
                jan = from_bounds(*b, transform=src.transform).round_offsets().round_lengths()
                jan = jan.intersection(Window(0, 0, src.width, src.height))
                d = src.read(1, window=jan)
                perfil = {"driver": "GTiff", "dtype": src.dtypes[0], "count": 1, "crs": src.crs, "transform": src.window_transform(jan),
                          "width": int(jan.width), "height": int(jan.height), "nodata": src.nodata, "compress": "deflate",
                          "predictor": 2, "tiled": True, "blockxsize": 256, "blockysize": 256}
                return d, perfil

        d, perfil = com_tentativas(ler)
        with rasterio.open(arq, "w", **perfil) as dst:
            dst.write(d, 1)
        sidecar(arq, fonte=f"USGS Landsat Collection 2 Level-2 via Microsoft Planetary Computer (coleção {COL_LANDSAT})",
                cena=item["id"], plataforma=p["platform"], data=p["datetime"], banda=banda, ativo_stac=ativo,
                endereco=href, wrs_path_row=f"{p.get('landsat:wrs_path')}/{p.get('landsat:wrs_row')}",
                transformacao_aplicada="nenhuma: janela do COG que cobre a AOI + 150 m, na grade e no CRS originais (EPSG:32621)",
                fatores=("ST: K = DN × 0,00341802 + 149,0" if banda == "ST_B10" else
                         "QA_PIXEL: bits" if banda == "QA_PIXEL" else "SR: ρ = DN × 0,0000275 − 0,2"),
                licenca="dados Landsat do USGS, domínio público (sem restrição de uso; citação recomendada)", status="extraido")
    return caminhos


def ler_na_grade(arq: Path, transform: Affine, w: int, h: int) -> np.ndarray:
    with rasterio.open(arq) as src:
        dst = np.zeros((h, w), dtype=src.dtypes[0])
        reproject(rasterio.band(src, 1), dst, dst_transform=transform, dst_crs=CRS_PADRAO, resampling=Resampling.nearest,
                  src_nodata=src.nodata, dst_nodata=0)
    return dst


def gravar_produto(arr: np.ndarray, caminho: Path, transform: Affine, **meta) -> None:
    perfil = {"driver": "GTiff", "dtype": "float32", "count": 1, "crs": CRS_PADRAO, "transform": transform, "width": arr.shape[1],
              "height": arr.shape[0], "nodata": np.nan, "compress": "deflate", "predictor": 3, "tiled": True,
              "blockxsize": 256, "blockysize": 256}
    with rasterio.open(caminho, "w", **perfil) as dst:
        dst.write(arr.astype("float32"), 1)
    sidecar(caminho, **meta)


def estat(v: np.ndarray) -> dict:
    v = v[np.isfinite(v)]
    if v.size == 0:
        return {"n_pixels": 0, "media": None, "mediana": None, "p5": None, "p95": None}
    return {"n_pixels": int(v.size), "media": round(float(v.mean()), 2), "mediana": round(float(np.median(v)), 2),
            "p5": round(float(np.percentile(v, 5)), 2), "p95": round(float(np.percentile(v, 95)), 2)}


def teste(a: dict, inv: pd.DataFrame | None, ids: list[str] | None, saida: Path, forcar: bool) -> dict:
    if not ids:
        if inv is None:
            inv = pd.read_csv(saida / "cenas_inventario.csv")
        ids = escolher_cenas(inv)
    logger.info("cenas de teste: %s", ids)
    PROCESSADO.mkdir(parents=True, exist_ok=True)
    itens = []
    for iid in ids:
        r = pedir("GET", f"{STAC}/collections/{COL_LANDSAT}/items/{iid}", timeout=60)
        r.raise_for_status()
        itens.append(r.json())

    linhas_cena, linhas_zona, linhas_bairro, linhas_san = [], [], [], []
    for item in itens:
        p = item["properties"]
        dia = p["datetime"][:10]
        plat = p["platform"].replace("-", "")
        brutos = baixar_recortes(item, a, forcar)
        with rasterio.open(brutos["qa_pixel"]) as s:
            tr, w, h = grade_alvo(s.transform, s.crs, a["aoi"])
        b = {k: ler_na_grade(v, tr, w, h) for k, v in brutos.items()}
        qa = b["qa_pixel"]
        dentro = ~geometry_mask([mapping(a["aoi"])], out_shape=(h, w), transform=tr)
        limpo = dentro & ((qa & 1) == 0) & ((qa & QA_SUJO) == 0) & (b["lwir11"] > 0)
        lst = np.where(limpo, b["lwir11"] * ST_ESCALA + ST_DESLOC - 273.15, np.nan)
        sr = {k: np.where(limpo, b[k] * SR_ESCALA + SR_DESLOC, np.nan) for k in ("green", "red", "nir08", "swir16")}
        with np.errstate(divide="ignore", invalid="ignore"):
            ndvi = (sr["nir08"] - sr["red"]) / (sr["nir08"] + sr["red"])
            ndbi = (sr["swir16"] - sr["nir08"]) / (sr["swir16"] + sr["nir08"])
            mndwi = (sr["green"] - sr["swir16"]) / (sr["green"] + sr["swir16"])
        # reflectância negativa (sombra, água escura) gera razões fora de [−1, 1]: viram NaN
        ndvi, ndbi, mndwi = (np.where(np.abs(x) <= 1, x, np.nan) for x in (ndvi, ndbi, mndwi))
        base_meta = dict(fonte=f"USGS Landsat Collection 2 Level-2 via Microsoft Planetary Computer ({COL_LANDSAT})", cena=item["id"],
                         plataforma=p["platform"], data=p["datetime"], crs=CRS_PADRAO, resolucao_m=30,
                         mascara="QA_PIXEL: só pixels preenchidos e sem nuvem dilatada, cirro, nuvem, sombra ou neve; fora da AOI = NaN; "
                                 "índices fora de [−1, 1] = NaN",
                         recortes_brutos=[x.name for x in brutos.values()],
                         grade="30 m em EPSG:31981 alinhada às bordas de pixel da cena; vizinho mais próximo (translação exata, ver roteiro)",
                         aoi=descrever_aoi(a), status="extraido")
        prods = {
            "lst": (lst, "LST em °C = ST_B10 × 0,00341802 + 149,0 − 273,15"),
            "ndvi": (ndvi, "NDVI = (SR_B5 − SR_B4)/(SR_B5 + SR_B4), SR = DN × 0,0000275 − 0,2"),
            "ndbi": (ndbi, "NDBI = (SR_B6 − SR_B5)/(SR_B6 + SR_B5), SR = DN × 0,0000275 − 0,2"),
            "mndwi": (mndwi, "MNDWI = (SR_B3 − SR_B6)/(SR_B3 + SR_B6), SR = DN × 0,0000275 − 0,2"),
        }
        for nome, (arr, formula) in prods.items():
            arq = PROCESSADO / f"{nome}_landsat-{plat}-c2l2_{dia}_30m.tif"
            if arq.exists() and not forcar:
                continue
            gravar_produto(arr, arq, tr, transformacao_aplicada=formula, fatores={"ST": [ST_ESCALA, ST_DESLOC], "SR": [SR_ESCALA, SR_DESLOC]},
                           **base_meta)

        # zonas: água (polígonos OSM), urbano (núcleo urbanizado IBGE 2019), rural (o resto da AOI)
        m_agua = rasterize([(mapping(a["agua"]), 1)], out_shape=(h, w), transform=tr, fill=0).astype(bool) & dentro
        m_urb = rasterize([(mapping(a["urbano"]), 1)], out_shape=(h, w), transform=tr, fill=0).astype(bool) & dentro & ~m_agua
        m_rur = dentro & ~m_agua & ~m_urb
        m_rio = rasterize([(mapping(a["rio"]), 1)], out_shape=(h, w), transform=tr, fill=0).astype(bool) & dentro
        dt = datetime.fromisoformat(p["datetime"].replace("Z", "+00:00"))
        cab = {"cena": item["id"], "data": dia, "plataforma": p["platform"], "hora_utc": dt.strftime("%H:%M")}
        e = estat(lst)
        e["frac_limpa_aoi"] = round(float(limpo.sum() / dentro.sum()), 4)
        e["lst_min"] = round(float(np.nanmin(lst)), 2)
        e["lst_max"] = round(float(np.nanmax(lst)), 2)
        # terra = fora dos polígonos de água e sem água espectral (MNDWI > 0: alagados e lavouras inundadas fora do OSM)
        base = np.isfinite(lst) & np.isfinite(ndvi) & np.isfinite(ndbi) & np.isfinite(mndwi) & ~m_agua
        terra = base & (mndwi <= 0)
        e["frac_mndwi_pos_fora_agua_osm"] = round(float((base & (mndwi > 0)).sum() / base.sum()), 4)
        e["pearson_lst_ndvi_sem_excluir_mndwi"] = round(float(np.corrcoef(lst[base], ndvi[base])[0, 1]), 3)
        e["pearson_lst_ndvi"] = round(float(np.corrcoef(lst[terra], ndvi[terra])[0, 1]), 3)
        e["pearson_lst_ndbi"] = round(float(np.corrcoef(lst[terra], ndbi[terra])[0, 1]), 3)
        e["spearman_lst_ndvi"] = round(float(pd.Series(lst[terra]).corr(pd.Series(ndvi[terra]), method="spearman")), 3)
        e["spearman_lst_ndbi"] = round(float(pd.Series(lst[terra]).corr(pd.Series(ndbi[terra]), method="spearman")), 3)
        linhas_cena.append({**cab, **e})
        for zona, m in (("urbano", m_urb), ("rural", m_rur), ("agua", m_agua)):
            linhas_zona.append({**cab, "zona": zona, **estat(np.where(m, lst, np.nan))})
        for _, r in a["bairros"].iterrows():
            mb = ~geometry_mask([mapping(r.geometry)], out_shape=(h, w), transform=tr) & dentro
            linhas_bairro.append({**cab, "cd_bairro": r["CD_BAIRRO"], "nm_bairro": r["NM_BAIRRO"],
                                  "frac_limpa": round(float((mb & limpo).sum() / max(mb.sum(), 1)), 3),
                                  **estat(np.where(mb, lst, np.nan))})
        # sanidade: rio × solo nu × superfície impermeável urbana (proxies espectrais, ver .json)
        rio_v = np.where(m_rio, lst, np.nan)
        solo = np.where(m_rur & (ndvi < 0.2) & (mndwi < 0), lst, np.nan)
        asf = np.where(m_urb & (ndvi < 0.15) & (mndwi < 0), lst, np.nan)
        s_rio, s_solo, s_asf = estat(rio_v), estat(solo), estat(asf)
        linhas_san.append({**cab, "rio_mediana": s_rio["mediana"], "rio_n": s_rio["n_pixels"], "solo_nu_mediana": s_solo["mediana"],
                           "solo_nu_n": s_solo["n_pixels"], "impermeavel_urbano_mediana": s_asf["mediana"], "impermeavel_urbano_n": s_asf["n_pixels"],
                           "rio_menor_que_solo_nu": bool(s_rio["mediana"] is not None and s_solo["mediana"] is not None and s_rio["mediana"] < s_solo["mediana"]),
                           "rio_menor_que_impermeavel": bool(s_rio["mediana"] is not None and s_asf["mediana"] is not None and s_rio["mediana"] < s_asf["mediana"]),
                           "faixa_plausivel_p5_p95_entre_10_e_65C": bool(e["p5"] > 10 and e["p95"] < 65)})
        mapa_png(lst, tr, a, PROCESSADO / f"lst_landsat-{plat}-c2l2_{dia}_30m_mapa.png", f"{dia} · {p['platform']} · {cab['hora_utc']} UTC",
                 base_meta, saida)

    comum = dict(fonte=f"USGS Landsat Collection 2 Level-2 via Microsoft Planetary Computer ({COL_LANDSAT})", cenas=ids, aoi=descrever_aoi(a),
                 zonas="água = polígonos de água do OSM (hidrografia-area-agua_osm); urbano = núcleo urbanizado IBGE 2019 na AOI, sem água; "
                       "rural = resto da AOI", correlacoes="pixels limpos de terra na AOI: fora da zona água e com MNDWI ≤ 0")
    gravar_csv(pd.DataFrame(linhas_cena), saida / "teste_lst_por_cena.csv", **comum)
    gravar_csv(pd.DataFrame(linhas_zona), saida / "teste_lst_por_zona.csv", **comum)
    gravar_csv(pd.DataFrame(linhas_bairro), saida / "teste_lst_por_bairro.csv", **comum,
               nota="tabela agregada por bairro do IBGE 2022 (estatísticas de pixels; sem dado pessoal)")
    gravar_csv(pd.DataFrame(linhas_san), saida / "teste_sanidade.csv", **comum,
               proxies=f"rio = polígonos de água > {AREA_RIO_HA:.0f} ha erodidos {EROSAO_RIO_M:.0f} m; solo nu = rural com NDVI < 0,2 e "
                       "MNDWI < 0; impermeável urbano (proxy de asfalto/telhado) = urbano com NDVI < 0,15 e MNDWI < 0")
    for nome in ("teste_lst_por_cena.csv", "teste_lst_por_zona.csv", "teste_lst_por_bairro.csv", "teste_sanidade.csv"):
        (PROCESSADO / nome).write_bytes((saida / nome).read_bytes())
        (PROCESSADO / f"{nome}.json").write_bytes((saida / f"{nome}.json").read_bytes())
    return {"ids": ids, "itens": itens}


def mapa_png(lst: np.ndarray, tr: Affine, a: dict, caminho: Path, titulo: str, meta: dict, saida: Path) -> None:
    h, w = lst.shape
    ext = (tr.c, tr.c + w * 30, tr.f - h * 30, tr.f)
    fig, ax = plt.subplots(figsize=(8, 6), dpi=150)
    ax.set_facecolor("#d9d8d4")  # fundo neutro
    v = lst[np.isfinite(lst)]
    vmin, vmax = np.percentile(v, 2), np.percentile(v, 98)
    im = ax.imshow(lst, extent=ext, cmap="inferno", vmin=vmin, vmax=vmax, interpolation="nearest")
    gpd.GeoSeries([a["aoi"]], crs=CRS_PADRAO).boundary.plot(ax=ax, color="#52514e", linewidth=0.6)
    a["bairros"].boundary.plot(ax=ax, color="#ffffff", linewidth=0.35, alpha=0.7)
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_title(f"Temperatura de superfície (°C) — {titulo}", fontsize=9, loc="left")
    cb = fig.colorbar(im, ax=ax, shrink=0.75)
    cb.set_label("LST (°C), escala p2–p98", fontsize=8)
    fig.text(0.01, 0.01, "Landsat C2 L2 (USGS) via Planetary Computer; contornos: AOI e bairros IBGE 2022; cinza = sem dado/máscara",
             fontsize=6.5, color="#52514e")
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    fig.savefig(caminho, facecolor="#fcfcfb")
    plt.close(fig)
    sidecar(caminho, **{k: v for k, v in meta.items() if k != "aoi"}, conteudo="LST em °C sobre fundo neutro; sem pontos de endereço")
    (saida / caminho.name).write_bytes(caminho.read_bytes())


# ---------------------------------------------------------------------------
# PARTE 3a — MODIS
# ---------------------------------------------------------------------------
def modis(a: dict, saida: Path, forcar: bool, ano_ini: int = 2015) -> None:
    centro = a["nucleo"].centroid  # centro do núcleo urbano
    c_ll = gpd.GeoSeries([centro], crs=CRS_PADRAO).to_crs("EPSG:4326").iloc[0]
    bbox = (c_ll.x - 0.001, c_ll.y - 0.001, c_ll.x + 0.001, c_ll.y + 0.001)
    BRUTO_MODIS.mkdir(parents=True, exist_ok=True)
    cache_arq = PASTA / "modis_cache.jsonl"
    cache = {}
    if forcar:
        cache_arq.unlink(missing_ok=True)
    if cache_arq.exists():
        for ln in cache_arq.read_text(encoding="utf-8").splitlines():
            d = json.loads(ln)
            cache[d["id"]] = d
    logger.info("buscando %s %d–hoje", COL_MODIS, ano_ini)
    itens = buscar_anos(COL_MODIS, ano_ini, date.today().year, bbox)
    faltam = [i for i in itens if i["id"] not in cache]
    logger.info("MODIS: %d itens, %d a ler", len(itens), len(faltam))
    rc = {}

    def ler(it):
        pr = it["properties"]
        out = {"id": it["id"], "plataforma": pr.get("platform"), "data": (pr.get("datetime") or pr["start_datetime"])[:10]}  # MODIS: datetime nulo
        for ativo in ("LST_Day_1km", "QC_Day", "LST_Night_1km", "QC_Night"):
            href = it["assets"][ativo]["href"]

            def um():
                with abrir(href) as s:
                    if "rc" not in rc:
                        from pyproj import Transformer
                        x, y = Transformer.from_crs("EPSG:4326", s.crs, always_xy=True).transform(c_ll.x, c_ll.y)
                        rc["rc"] = s.index(x, y)
                    r, c = rc["rc"]
                    return s.read(1, window=((r - 1, r + 2), (c - 1, c + 2))).astype(int).tolist()

            out[ativo] = com_tentativas(um)
        return out

    with open(cache_arq, "a", encoding="utf-8") as f, ThreadPoolExecutor(24) as ex:
        futuros = [ex.submit(ler, it) for it in faltam]
        for k, fu in enumerate(futuros, 1):
            try:
                d = fu.result()
                cache[d["id"]] = d
                f.write(json.dumps(d) + "\n")
            except Exception as e:
                logger.warning("MODIS falhou: %s", e)
            if k % 500 == 0:
                logger.info("  %d/%d", k, len(faltam))
                f.flush()

    if not cache:
        raise RuntimeError("nenhuma leitura do MODIS concluída")
    linhas = []
    for d in cache.values():
        # o catálogo deixa platform vazio em parte dos itens: o prefixo do produto resolve (MOD = Terra, MYD = Aqua)
        lin = {"data": d["data"], "plataforma": d["plataforma"] or {"MOD": "terra", "MYD": "aqua"}[d["id"][:3]], "id": d["id"]}
        for per, lst_k, qc_k in (("dia", "LST_Day_1km", "QC_Day"), ("noite", "LST_Night_1km", "QC_Night")):
            m = np.array(d[lst_k])
            q = np.array(d[qc_k])
            cv = m[1, 1]
            lin[f"lst_{per}_centro_c"] = round(cv * 0.02 - 273.15, 2) if cv > 0 else None
            lin[f"qc_{per}_centro"] = int(q[1, 1])
            # bits 6–7 do QC = erro estimado da LST (00 ≤ 1 K, 01 ≤ 2 K, 10 ≤ 3 K, 11 > 3 K); bits 0–1 = 01 é o caso comum na v6.1
            lin[f"erro_lst_ate_2k_{per}"] = bool(cv > 0 and ((q[1, 1] >> 6) & 3) <= 1)
            ok = m > 0
            lin[f"lst_{per}_janela3x3_media_c"] = round(float(m[ok].mean() * 0.02 - 273.15), 2) if ok.any() else None
            lin[f"n_validos_janela_{per}"] = int(ok.sum())
        linhas.append(lin)
    df = pd.DataFrame(linhas).sort_values(["data", "plataforma", "id"])
    # o catálogo tem mais de uma versão de produção para algumas datas: fica a mais recente (sufixo do id)
    n_dup = int(df.duplicated(["data", "plataforma"], keep="last").sum())
    df = df.drop_duplicates(["data", "plataforma"], keep="last").reset_index(drop=True)
    anos = f"{df['data'].min()[:4]}-{df['data'].max()[:4]}"
    arq = BRUTO_MODIS / f"lst-diaria_modis-11a1-061_{anos}_1km-janela3x3.csv"
    lic = licenca(COL_MODIS)
    gravar_csv(df, arq, fonte=f"NASA LP DAAC — MOD11A1/MYD11A1 v6.1 via Microsoft Planetary Computer (coleção {COL_MODIS})",
               licenca=lic, ponto_centro_lonlat=[round(c_ll.x, 5), round(c_ll.y, 5)],
               transformacao_aplicada="janela 3×3 (pixels de ~926 m, sinusoidal) em torno do pixel que contém o centroide do núcleo urbano; "
                                      "LST °C = DN × 0,02 − 273,15; DN 0 = sem retrieval (nuvem); erro_lst_ate_2k = bits 6–7 do QC ≤ 01; "
                                      f"{n_dup} itens duplicados (versões de produção anteriores) descartados; "
                                      "passagens nominais: Terra ≈ 10h30/22h30, Aqua ≈ 13h30/1h30 (hora solar local)", status="extraido")
    df["d"] = pd.to_datetime(df["data"])
    df["ano"], df["mes"] = df["d"].dt.year, df["d"].dt.month
    resumo = df.groupby(["ano", "plataforma"]).agg(
        n_dias=("data", "size"), validos_dia=("lst_dia_centro_c", "count"), validos_noite=("lst_noite_centro_c", "count"),
        erro_ate_2k_dia=("erro_lst_ate_2k_dia", "sum"), erro_ate_2k_noite=("erro_lst_ate_2k_noite", "sum")).reset_index()
    gravar_csv(resumo, saida / "modis_dias_validos_por_ano.csv", fonte=COL_MODIS, nota="pixel central; válido = LST > 0")
    ciclo = df.groupby(["mes", "plataforma"]).agg(
        lst_dia_media=("lst_dia_centro_c", "mean"), lst_dia_p90=("lst_dia_centro_c", lambda s: s.quantile(0.9)),
        n_dia=("lst_dia_centro_c", "count"), lst_noite_media=("lst_noite_centro_c", "mean"),
        lst_noite_p10=("lst_noite_centro_c", lambda s: s.quantile(0.1)), n_noite=("lst_noite_centro_c", "count")).round(2).reset_index()
    gravar_csv(ciclo, saida / "modis_ciclo_anual.csv", fonte=COL_MODIS, nota="média mensal do pixel central, todos os anos")
    grafico_modis(ciclo, saida / "modis_ciclo_anual.png")


def grafico_modis(ciclo: pd.DataFrame, caminho: Path) -> None:
    fig, axs = plt.subplots(1, 2, figsize=(10, 4), dpi=150, sharey=True)
    cores = {"terra": "#2a78d6", "aqua": "#eb6834"}
    for ax, per in zip(axs, ("dia", "noite")):
        for plat, g in ciclo.groupby("plataforma"):
            ax.plot(g["mes"], g[f"lst_{per}_media"], color=cores.get(plat, "#52514e"), lw=2, marker="o", ms=4,
                    label={"terra": "Terra (≈10h30 / 22h30)", "aqua": "Aqua (≈13h30 / 1h30)"}.get(plat, plat))
        ax.set_title(f"LST média mensal — {per}", fontsize=9, loc="left")
        ax.set_xticks(range(1, 13))
        ax.grid(color="#e6e5e1", lw=0.8)
        for s in ("top", "right"):
            ax.spines[s].set_visible(False)
    axs[0].set_ylabel("°C (pixel 1 km do centro urbano)")
    axs[0].legend(frameon=False, fontsize=8)
    fig.text(0.01, 0.01, "MOD11A1/MYD11A1 v6.1 (NASA LP DAAC) via Planetary Computer; dias sem nuvem apenas.", fontsize=7, color="#52514e")
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    fig.savefig(caminho, facecolor="#fcfcfb")
    plt.close(fig)
    sidecar(caminho, conteudo="ciclo anual da LST MODIS (dia e noite) no centro urbano")


# ---------------------------------------------------------------------------
# PARTE 3b–d — outras fontes
# ---------------------------------------------------------------------------
def fontes(a: dict, saida: Path, ids_teste: list[str] | None) -> dict:
    res = {}
    bl = a["aoi_ll"].bounds
    caixa = ",".join(f"{v:.5f}" for v in bl)
    # ECOSTRESS: metadados no CMR (sem login); o arquivo em si passa pelo Earthdata Login
    eco = {}
    for sn in ("ECO_L2T_LSTE", "ECO2LSTE"):
        try:
            r = pedir("GET", f"{CMR}/granules.json", params={"short_name": sn, "bounding_box": caixa, "page_size": 1,
                                                              "sort_key": "-start_date"}, timeout=60)
            hits = int(r.headers.get("CMR-Hits", -1))
            ent = r.json().get("feed", {}).get("entry", [])
            info = {"granulos_sobre_aoi": hits}
            if ent:
                info["mais_recente"] = ent[0].get("time_start")
                info["granulo_mais_recente"] = ent[0].get("producer_granule_id") or ent[0].get("title")
                links = [x["href"] for x in ent[0].get("links", []) if x.get("href", "").startswith("https") and
                         x.get("rel", "").endswith("/data#")]
                if links:
                    h = pedir("HEAD", links[0], allow_redirects=False, timeout=60)  # só o cabeçalho; nada é baixado
                    loc = h.headers.get("Location", "")
                    info["acesso_ao_arquivo"] = (f"HTTP {h.status_code} → redireciona para {loc.split('?')[0].split('/')[2]}"
                                                 if loc else f"HTTP {h.status_code}")
                    if loc:  # segundo salto (URL assinada pelo LP DAAC), também só cabeçalho
                        h2 = pedir("HEAD", loc, allow_redirects=False, timeout=60)
                        info["acesso_ao_arquivo"] += (f" → HTTP {h2.status_code} ({h2.headers.get('Content-Type')}, "
                                                      f"{h2.headers.get('Content-Length')} bytes; não baixado)")
                        h = h2
                        loc = loc.split("?")[0] + " " + h2.headers.get("Location", "")
                    info["exige_login"] = "urs.earthdata.nasa.gov" in loc or h.status_code in (401, 403)
            r2 = pedir("GET", f"{CMR}/granules.json", params={"short_name": sn, "bounding_box": caixa, "page_size": 1,
                                                               "sort_key": "start_date"}, timeout=60)
            e2 = r2.json().get("feed", {}).get("entry", [])
            if e2:
                info["mais_antigo"] = e2[0].get("time_start")
            eco[sn] = info
        except Exception as e:
            eco[sn] = {"erro": sem_token(e)}
    res["ecostress"] = eco
    # Sentinel-3 SLSTR: só acesso e resolução
    try:
        it = buscar(COL_SLSTR, "2024-01-01/2024-01-31", tuple(bl))
        res["sentinel3_slstr"] = {"colecao_planetary_computer": COL_SLSTR, "itens_jan_2024_sobre_aoi": len(it),
                                  "resolucao": "LST a 1 km (canais térmicos S7–S9); netCDF por granulo (não é COG)",
                                  "acesso": "STAC público + token anônimo (Planetary Computer); também no Copernicus Data Space (exige conta)",
                                  "licenca": licenca(COL_SLSTR)}
    except Exception as e:
        res["sentinel3_slstr"] = {"erro": sem_token(e)}
    # INMET: estação e série horária já baixadas pelo projeto
    try:
        est = gpd.read_file(REF["inmet_estacoes"]).to_crs(CRS_PADRAO)
        e0 = est.iloc[0]
        ll = gpd.GeoSeries([e0.geometry], crs=CRS_PADRAO).to_crs("EPSG:4326").iloc[0]
        serie = pd.read_parquet(REF["inmet_serie"])
        serie["datetime_utc"] = pd.to_datetime(serie["datetime_utc"], utc=True)
        extra = complementar_inmet(e0["codigo_estacao"], serie, saida, ids_teste)
        if extra is not None:
            serie = pd.concat([serie, extra], ignore_index=True)
        dist_aoi = float(e0.geometry.distance(a["aoi"]))
        res["inmet"] = {"codigo": e0["codigo_estacao"], "nome": e0["nome_estacao"], "lon": round(ll.x, 5), "lat": round(ll.y, 5),
                        "altitude_m": float(e0["altitude_m"]), "inicio_operacao": e0["data_inicio_operacao"],
                        "serie_no_projeto": [str(serie["datetime_utc"].min()), str(serie["datetime_utc"].max())],
                        "variaveis": [c for c in serie.columns if c not in ("codigo_estacao", "datetime_utc")],
                        "distancia_da_aoi_m": round(dist_aoi, 0), "dentro_da_aoi": dist_aoi == 0,
                        "acesso": "portal.inmet.gov.br/dadoshistoricos (zips anuais, sem login; lidos por HTTP Range por "
                                  "scripts/download/clima_inmet.py); apitempo.inmet.gov.br exige reCAPTCHA para séries"}
        if ids_teste:
            cmp_ = []
            cena = pd.read_csv(saida / "teste_lst_por_cena.csv")
            zona = pd.read_csv(saida / "teste_lst_por_zona.csv")
            for _, c in cena.iterrows():
                t = pd.Timestamp(f"{c['data']} {c['hora_utc']}", tz="UTC")
                janela = serie[(serie["datetime_utc"] >= t.floor("h") - pd.Timedelta(hours=1)) &
                               (serie["datetime_utc"] <= t.ceil("h") + pd.Timedelta(hours=1))]
                antes = serie[serie["datetime_utc"] == t.floor("h")]["temp_ar_c"]
                depois = serie[serie["datetime_utc"] == t.ceil("h")]["temp_ar_c"]
                urb = zona[(zona["cena"] == c["cena"]) & (zona["zona"] == "urbano")]
                rur = zona[(zona["cena"] == c["cena"]) & (zona["zona"] == "rural")]
                cmp_.append({"cena": c["cena"], "data": c["data"], "hora_utc": c["hora_utc"],
                             "temp_ar_hora_anterior_c": antes.iloc[0] if len(antes) else None,
                             "temp_ar_hora_seguinte_c": depois.iloc[0] if len(depois) else None,
                             "temp_max_dia_utc_c": serie[serie["datetime_utc"].dt.date.astype(str) == c["data"]]["temp_max_c"].max(),
                             "registros_janela": len(janela),
                             "lst_media_urbano_c": urb["media"].iloc[0] if len(urb) else None,
                             "lst_media_rural_c": rur["media"].iloc[0] if len(rur) else None})
            gravar_csv(pd.DataFrame(cmp_), saida / "inmet_x_lst_cenas_teste.csv",
                       fonte="INMET A809 (série horária já no projeto) × LST Landsat das cenas de teste",
                       nota="temperatura do ar às horas cheias que cercam a passagem (UTC); LST ≠ temperatura do ar")
    except Exception as e:
        res["inmet"] = {"erro": str(e)}
    (saida / "outras_fontes.json").write_text(json.dumps(res, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return res


def complementar_inmet(codigo: str, serie: pd.DataFrame, saida: Path, ids_teste: list[str] | None) -> pd.DataFrame | None:
    """Ano de cena de teste além da série do projeto: lê só o CSV da estação no zip anual público (HTTP Range)."""
    if not ids_teste or not (saida / "teste_lst_por_cena.csv").exists():
        return None
    anos = sorted({int(d[:4]) for d in pd.read_csv(saida / "teste_lst_por_cena.csv")["data"]})
    anos = [x for x in anos if x > serie["datetime_utc"].max().year]
    if not anos:
        return None
    import requests
    from clima_inmet import URL_ZIP_TEMPLATE, baixar_e_processar_ano
    partes = []
    for ano in anos:
        arq = RAIZ / "data" / "raw" / f"clima_inmet-{codigo.lower()}_{ano}-parcial_horario.csv"
        if arq.exists():
            partes.append(pd.read_csv(arq, parse_dates=["datetime_utc"]))
            continue
        df = baixar_e_processar_ano(codigo, ano, requests.Session())
        if df is None:
            continue
        df.to_csv(arq, index=False)
        sidecar(arq, fonte="INMET — portal de dados históricos (zip anual público, sem login), só o CSV da estação via HTTP Range",
                url=URL_ZIP_TEMPLATE.format(ano=ano), estacao=codigo, periodo=[str(df["datetime_utc"].min()), str(df["datetime_utc"].max())],
                transformacao_aplicada="mesma leitura de scripts/download/clima_inmet.py (colunas por posição, UTC); ano parcial",
                motivo="complementar a série do projeto (até o fim do ano anterior) nas datas das cenas de teste", status="extraido")
        partes.append(df)
    if not partes:
        return None
    out = pd.concat(partes, ignore_index=True)
    out["datetime_utc"] = pd.to_datetime(out["datetime_utc"], utc=True)
    return out


# ---------------------------------------------------------------------------
def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("etapa", choices=["inventario", "teste", "modis", "fontes", "todos"])
    ap.add_argument("--codigo-ibge", default="4322400", help="código IBGE do município (filtra os bairros)")
    ap.add_argument("--saida-tabelas", type=Path, default=PASTA / "tabelas", help="pasta das tabelas do levantamento")
    ap.add_argument("--cenas", nargs="*", help="ids das cenas de teste (sem isso: escolha automática)")
    ap.add_argument("--forcar", action="store_true", help="refaz leituras e produtos já existentes")
    a_ = ap.parse_args()
    saida = a_.saida_tabelas
    saida.mkdir(parents=True, exist_ok=True)
    a = montar_aoi(a_.codigo_ibge)
    (saida / "aoi.json").write_text(json.dumps(descrever_aoi(a), ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("AOI: %s", descrever_aoi(a))
    inv, ids = None, a_.cenas
    try:
        if a_.etapa in ("inventario", "todos"):
            inv = inventario(a, saida, a_.forcar)
        if a_.etapa in ("teste", "todos"):
            ids = teste(a, inv, ids, saida, a_.forcar)["ids"]
        if a_.etapa in ("modis", "todos"):
            modis(a, saida, a_.forcar)
        if a_.etapa in ("fontes", "todos"):
            if not ids and (saida / "teste_lst_por_cena.csv").exists():
                ids = pd.read_csv(saida / "teste_lst_por_cena.csv")["cena"].tolist()
            logger.info("outras fontes: %s", json.dumps(fontes(a, saida, ids), ensure_ascii=False, default=str)[:2000])
    except PermissionError as e:
        logger.error("acesso recusado: %s", e)
        sys.exit(2)


if __name__ == "__main__":
    main()
