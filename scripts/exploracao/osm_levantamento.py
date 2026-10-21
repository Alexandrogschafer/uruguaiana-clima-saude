"""
Levantamento exploratório do OpenStreetMap para a área de estudo (rodada 65).

Não integra nada ao portal nem ao catálogo: baixa um extrato completo do OSM,
monta os grafos viários com osmnx e gera tabelas de diagnóstico (CSV) para
decidir depois o que adotar.

Etapas (subcomandos):
    extrair      Overpass em blocos (grade NxN) sobre a área de estudo com buffer
                 de 2 km; todos os nós com tags, todas as vias e as relações.
                 Grava data/raw/osm_levantamento/osm_completo_<data>.gpkg
                 (camadas pontos, linhas, poligonos, relacoes, relacoes_limite)
                 em EPSG:31981, com um .json irmão (timestamp do Overpass).
    grafo        osmnx.graph_from_polygon (drive, walk, all) a partir do
                 polígono (não do nome do lugar), com useful_tags_way ampliado.
                 Grava GraphML e GeoPackage em data/raw/osm_levantamento/.
    diagnostico  Tabelas A–I em --saida-tabelas (padrão:
                 data/raw/osm_levantamento/diagnostico/, fora do git).
    todos        As três etapas em sequência.

Regras:
    - Idempotente: blocos já salvos (json.gz) e grafos já gravados não são
      baixados de novo; use --forcar para refazer.
    - Polidez com a API: User-Agent do projeto, pausa entre consultas e até 3
      tentativas com espera crescente. Se uma consulta falhar 3 vezes, o
      script segue com o plano B: extrato regional do Geofabrik (PBF, Região
      Sul) recortado com ogr2ogr; os grafos são montados com as funções do
      próprio osmnx sobre as vias do PBF (nós identificados pela coordenada).
      Para ir direto ao plano B: --fonte geofabrik.
    - Dados pessoais: as tabelas só trazem CONTAGENS de tags de contato/
      endereço (phone, contact:*, email, operator, addr:*); nenhum valor.
      Endereços do CNEFE entram apenas como contagem por bairro (campo já
      agregado na camada de bairros).
    - Atribuição (ODbL): © contribuidores do OpenStreetMap.

Uso:
    python scripts/exploracao/osm_levantamento.py todos
    python scripts/exploracao/osm_levantamento.py todos --fonte geofabrik
    python scripts/exploracao/osm_levantamento.py diagnostico --saida-tabelas <pasta>
"""

import argparse
import gzip
import json
import logging
import re
import sys
import time
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import requests
from shapely import make_valid
from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import linemerge, polygonize, unary_union

RAIZ = Path(__file__).resolve().parents[2]
sys.path.append(str(RAIZ / "scripts" / "utils"))
from recorte_municipio import CRS_PADRAO, carregar_area_estudo  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

PASTA = RAIZ / "data" / "raw" / "osm_levantamento"
PASTA_BLOCOS = PASTA / "overpass_blocos"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
USER_AGENT = "ClimaPampa-PET-Saude-levantamento-osm/1.0 (projeto academico; uso nao comercial)"
ATRIBUICAO = "© contribuidores do OpenStreetMap (ODbL)"
CRS_OSM = "EPSG:4326"
BUFFER_M = 2000  # para não cortar vias que cruzam a divisa
PAUSA_S = 8
TENTATIVAS = 3

# referências do repositório (todas no CRS padrão)
REF = {
    "urbano": RAIZ / "data/raw/vetor/areas-urbanizadas_ibge_2019_recorte-municipio.gpkg",
    "quadras": RAIZ / "data/processed/vetorizado/base-urbana_plano-diretor-uruguaiana_2013_vetorial_extraido-de-pdf.gpkg",
    "toponimia": RAIZ / "data/processed/vetorizado/toponimia_plano-diretor-uruguaiana_2013_vetorial_extraido-de-pdf.gpkg",
    "hidro_plano": RAIZ / "data/processed/vetorizado/hidrografia_plano-diretor-uruguaiana_2013_vetorial_extraido-de-pdf.gpkg",
    "bho": RAIZ / "data/raw/vetor/rede-hidrografica_ana-bho_atual_vetorial.gpkg",
    "bairros": RAIZ / "data/processed/bairros/bairros_ibge_2022_vetorial.gpkg",
    "esf": RAIZ / "data/processed/saude/unidades-saude-esf-ubs_cnes-revisado-v4_2026_pontos.gpkg",
    "malha_antiga": RAIZ / "data/raw/vetor/malha-viaria_osm_atual_vetorial.gpkg",
    "campo": RAIZ / "docs/acessibilidade_inundacao/campo/pontes-travessias-campo_osm-bho-sgb_2026_pontos.geojson",
}

COLUNAS_PRINCIPAIS = [
    "name", "highway", "building", "landuse", "natural", "water", "waterway", "amenity", "shop", "office",
    "healthcare", "emergency", "leisure", "tourism", "railway", "bridge", "tunnel", "barrier", "place",
    "boundary", "admin_level", "power", "man_made", "public_transport", "route", "surface", "tracktype",
    "ref", "maxspeed", "layer",
]
CHAVES_CENSO = [
    "highway", "building", "landuse", "natural", "water", "waterway", "amenity", "shop", "office", "healthcare",
    "emergency", "leisure", "tourism", "railway", "bridge", "barrier", "place", "boundary", "admin_level", "power",
    "man_made", "public_transport", "route",
]
TAGS_PESSOAIS = ["phone", "contact:*", "email", "website", "name", "operator", "addr:*"]

# chaves que fazem de uma via fechada um polígono (regra simplificada da wiki do OSM)
CHAVES_AREA = {
    "building", "building:part", "landuse", "amenity", "leisure", "shop", "office", "tourism", "healthcare", "place",
    "water", "military", "historic", "aeroway", "craft", "emergency", "area:highway", "public_transport", "power",
    "man_made", "natural",
}
NAO_AREA = {
    "natural": {"coastline", "tree_row", "cliff", "ridge", "arete", "gully"},
    "man_made": {"embankment", "pipeline", "dyke", "breakwater", "groyne", "cutline", "levee"},
    "power": {"line", "minor_line", "cable"},
    "aeroway": {"runway", "taxiway"},
    "public_transport": {"stop_position"},
}

CLASSES_VIARIAS = [
    "motorway", "trunk", "primary", "secondary", "tertiary", "unclassified", "residential", "living_street",
    "service", "track", "path", "footway", "cycleway", "pedestrian", "steps", "road",
]
VEICULARES = {
    "motorway", "motorway_link", "trunk", "trunk_link", "primary", "primary_link", "secondary", "secondary_link",
    "tertiary", "tertiary_link", "unclassified", "residential", "living_street", "service", "track", "road",
}
WATERWAYS = ["river", "stream", "canal", "drain", "ditch"]

USEFUL_TAGS_WAY = [
    "name", "ref", "highway", "surface", "smoothness", "tracktype", "bridge", "tunnel", "layer", "maxspeed",
    "lanes", "oneway", "junction", "access", "foot", "bicycle", "cycleway", "lit", "width", "incline",
]


# ---------------------------------------------------------------------------
# utilidades
# ---------------------------------------------------------------------------
def area_consulta() -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """Município (CRS padrão) e área de consulta com buffer de 2 km."""
    mun = carregar_area_estudo()
    # buffer em metros: só faz sentido no CRS projetado (UTM 21S)
    buf = gpd.GeoDataFrame(geometry=[mun.union_all().buffer(BUFFER_M)], crs=CRS_PADRAO)
    return mun, buf


def sidecar(caminho: Path, **campos) -> None:
    meta = {"arquivo": caminho.name, "atribuicao": ATRIBUICAO, "data_processamento": datetime.now(timezone.utc).isoformat(),
            "script": "scripts/exploracao/osm_levantamento.py", "uso": "levantamento exploratório (rodada 65); não adotado no acervo"}
    meta.update(campos)
    if caminho.exists():
        meta["tamanho_mb"] = round(caminho.stat().st_size / 1e6, 2)
    caminho.with_suffix(".json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def overpass(consulta: str) -> dict:
    """POST no Overpass com espera crescente; levanta RuntimeError após 3 falhas."""
    ultimo = None
    for t in range(1, TENTATIVAS + 1):
        try:
            r = requests.post(OVERPASS_URL, data={"data": consulta}, headers={"User-Agent": USER_AGENT}, timeout=900)
            if r.status_code == 200:
                return r.json()
            ultimo = f"HTTP {r.status_code}: {r.text[:200]}"
        except (requests.RequestException, ValueError) as e:
            ultimo = repr(e)
        espera = 30 * 2 ** (t - 1)
        logger.warning("Overpass falhou (tentativa %d/%d): %s — aguardando %ds", t, TENTATIVAS, ultimo, espera)
        time.sleep(espera)
    raise RuntimeError(f"Overpass falhou {TENTATIVAS} vezes ({ultimo}). Plano B: extrato do Geofabrik "
                       "(https://download.geofabrik.de/south-america/brazil/sul.html) recortado pelo polígono.")


def baixar_bloco(nome: str, consulta: str, forcar: bool) -> dict:
    arq = PASTA_BLOCOS / f"{nome}.json.gz"
    if arq.exists() and not forcar:
        logger.info("Bloco %s já salvo — pulando download", nome)
        with gzip.open(arq, "rt", encoding="utf-8") as f:
            return json.load(f)
    logger.info("Baixando bloco %s", nome)
    dados = overpass(consulta)
    PASTA_BLOCOS.mkdir(parents=True, exist_ok=True)
    with gzip.open(arq, "wt", encoding="utf-8") as f:
        json.dump(dados, f)
    logger.info("Bloco %s: %d elementos, %.1f MB (gz), timestamp %s", nome, len(dados.get("elements", [])),
                arq.stat().st_size / 1e6, dados.get("osm3s", {}).get("timestamp_osm_base"))
    time.sleep(PAUSA_S)
    return dados


def eh_area(tags: dict) -> bool:
    if tags.get("area") == "no":
        return False
    if tags.get("area") == "yes":
        return True
    for k in CHAVES_AREA:
        if k in tags and tags[k] not in NAO_AREA.get(k, set()):
            return True
    return tags.get("waterway") in {"riverbank", "dock", "boatyard"}


def coords(geom: list) -> list:
    return [(p["lon"], p["lat"]) for p in geom if p]


def montar_multipoligono(rel: dict):
    """Poligoniza os membros outer/inner de uma relação multipolygon/boundary."""
    externos, internos = [], []
    for m in rel.get("members", []):
        if m.get("type") != "way" or not m.get("geometry"):
            continue
        c = coords(m["geometry"])
        if len(c) < 2:
            continue
        (internos if m.get("role") == "inner" else externos).append(LineString(c))
    if not externos:
        return None
    pe = list(polygonize(unary_union(externos)))
    if not pe:
        return None
    g = unary_union(pe)
    if internos:
        pi = list(polygonize(unary_union(internos)))
        if pi:
            g = g.difference(unary_union(pi))
    return make_valid(g) if not g.is_valid else g


def linha_registro(el: dict, geom) -> dict:
    tags = el.get("tags", {})
    reg = {"osm_tipo": el["type"], "osm_id": el["id"], "tags": json.dumps(tags, ensure_ascii=False)}
    for c in COLUNAS_PRINCIPAIS:
        reg[c] = tags.get(c)
    reg["geometry"] = geom
    return reg


# ---------------------------------------------------------------------------
# PARTE 1 — extração
# ---------------------------------------------------------------------------
def extrair(n_grade: int, forcar: bool) -> Path:
    data = datetime.now().strftime("%Y-%m-%d")
    saida = PASTA / f"osm_completo_{data}.gpkg"
    existentes = sorted(PASTA.glob("osm_completo_*.gpkg"))
    if existentes and not forcar:
        logger.info("Extrato já existe (%s) — use --forcar para refazer", existentes[-1].name)
        return existentes[-1]

    mun, buf = area_consulta()
    buf_ll = buf.to_crs(CRS_OSM)
    poly_ll = buf_ll.geometry.iloc[0]
    w, s, e, n = poly_ll.bounds
    xs, ys = np.linspace(w, e, n_grade + 1), np.linspace(s, n, n_grade + 1)

    elementos, timestamps = {}, []
    for i in range(n_grade):
        for j in range(n_grade):
            bb = box(xs[i], ys[j], xs[i + 1], ys[j + 1])
            if not bb.intersects(poly_ll):
                continue  # bloco fora da área (grade retangular sobre polígono irregular)
            b = f"{ys[j]:.5f},{xs[i]:.5f},{ys[j + 1]:.5f},{xs[i + 1]:.5f}"
            # só nós com tags (os vértices vêm dentro da geometria das vias)
            q = f"[out:json][timeout:600][maxsize:1073741824];(node({b})(if:count_tags()>0);way({b}););out geom;"
            d = baixar_bloco(f"bloco_{i}_{j}", q, forcar)
            timestamps.append(d.get("osm3s", {}).get("timestamp_osm_base"))
            for el in d.get("elements", []):
                elementos[(el["type"], el["id"])] = el  # chave (tipo, id) elimina duplicatas entre blocos

    bbox = f"{s:.5f},{w:.5f},{n:.5f},{e:.5f}"
    consultas_rel = {
        # multipolígonos locais (landuse, edificações, água) com geometria completa
        "rel_multipoligono": f"[out:json][timeout:600];rel({bbox})[type=multipolygon];out geom;",
        # limites administrativos de município para baixo (8+) e equivalentes argentinos (5-7)
        "rel_limites": f'[out:json][timeout:600];rel({bbox})[boundary=administrative][admin_level~"^([5-9]|10|11)$"];out geom;',
        # todas as relações, só tags e membros (rotas, limites grandes etc.)
        "rel_todas": f"[out:json][timeout:600];rel({bbox});out body;",
    }
    rels = {}
    for nome, q in consultas_rel.items():
        d = baixar_bloco(nome, q, forcar)
        timestamps.append(d.get("osm3s", {}).get("timestamp_osm_base"))
        rels[nome] = d.get("elements", [])

    pontos, linhas, poligonos = [], [], []
    for (tipo, _), el in elementos.items():
        tags = el.get("tags", {})
        if tipo == "node":
            pontos.append(linha_registro(el, Point(el["lon"], el["lat"])))
        elif tipo == "way" and el.get("geometry"):
            c = coords(el["geometry"])
            if len(c) < 2:
                continue
            fechada = len(c) >= 4 and c[0] == c[-1]
            if fechada and eh_area(tags):
                g = Polygon(c)
                poligonos.append(linha_registro(el, g if g.is_valid else make_valid(g)))
            else:
                linhas.append(linha_registro(el, LineString(c)))

    for el in rels["rel_multipoligono"]:
        g = montar_multipoligono(el)
        if g is not None and not g.is_empty:
            poligonos.append(linha_registro(el, g))

    limites = []
    for el in rels["rel_limites"]:
        g = montar_multipoligono(el)
        limites.append(linha_registro(el, g))

    tab_rel = []
    for el in rels["rel_todas"]:
        r = linha_registro(el, None)
        mem = Counter(m["type"] for m in el.get("members", []))
        r.update(n_membros=sum(mem.values()), membros_way=mem.get("way", 0), membros_node=mem.get("node", 0),
                 membros_rel=mem.get("relation", 0), rel_type=el.get("tags", {}).get("type"))
        tab_rel.append(r)

    poly_proj = buf.geometry.iloc[0]
    PASTA.mkdir(parents=True, exist_ok=True)
    tmp = saida.with_suffix(".tmp.gpkg")
    tmp.unlink(missing_ok=True)
    contagens = {}
    for camada, regs in [("pontos", pontos), ("linhas", linhas), ("poligonos", poligonos), ("relacoes_limite", limites)]:
        gdf = gpd.GeoDataFrame(regs, geometry="geometry", crs=CRS_OSM).to_crs(CRS_PADRAO)
        if camada != "relacoes_limite":
            # mantém feições inteiras que tocam a área com buffer (sem cortar a geometria)
            gdf = gdf[gdf.intersects(poly_proj)]
        gdf.to_file(tmp, layer=camada, driver="GPKG")
        contagens[camada] = len(gdf)
        logger.info("Camada %s: %d feições", camada, len(gdf))
    rel_df = pd.DataFrame(tab_rel)
    gpd.GeoDataFrame(rel_df, geometry=[None] * len(rel_df), crs=CRS_PADRAO).to_file(tmp, layer="relacoes", driver="GPKG")
    contagens["relacoes"] = len(rel_df)
    tmp.rename(saida)

    ts = sorted(t for t in timestamps if t)
    sidecar(saida, fonte="OpenStreetMap via Overpass API", url_api=OVERPASS_URL,
            timestamp_osm_base_min=ts[0] if ts else None, timestamp_osm_base_max=ts[-1] if ts else None,
            data_acesso=datetime.now(timezone.utc).isoformat(), area="área de estudo (config/area_estudo.geojson) + buffer de 2 km",
            grade_blocos=f"{n_grade}x{n_grade}", crs=CRS_PADRAO, feicoes=contagens,
            transformacao_aplicada="nós com tags e todas as vias por bloco (out geom), deduplicados por (tipo, id); vias fechadas "
                                   "com chave de área viram polígono; relações multipolygon e limites administrativos poligonizados; "
                                   "demais relações só com tags e contagem de membros; reprojeção para EPSG:31981; mantidas as "
                                   "feições que tocam a área com buffer (sem corte). Campo 'tags' = todas as tags originais em JSON.",
            aviso="pode conter tags de contato/endereço (dado pessoal possível); bruto fora do git, não publicar valores")
    logger.info("Extrato gravado: %s (%.1f MB)", saida, saida.stat().st_size / 1e6)
    return saida


GEOFABRIK_URL = "https://download.geofabrik.de/south-america/brazil/sul-latest.osm.pbf"
GEOFABRIK_STATE = "https://download.geofabrik.de/south-america/brazil/sul-updates/state.txt"
# polígonos além do padrão do GDAL (água, saúde, energia, estruturas)
POLIGONOS_EXTRA = "water,healthcare,emergency,power=substation,power=plant,power=generator,man_made=bridge,man_made=wastewater_plant," \
                  "man_made=water_works,man_made=reservoir_covered,waterway=riverbank,waterway=dock,area:highway"


def parse_hstore(s) -> dict:
    """Converte o campo all_tags do GDAL ('"k"=>"v",...') em dicionário."""
    if not isinstance(s, str) or not s:
        return {}
    pares = re.findall(r'"((?:[^"\\]|\\.)*)"=>"((?:[^"\\]|\\.)*)"', s)
    return {k.replace('\\"', '"').replace("\\\\", "\\"): v.replace('\\"', '"').replace("\\\\", "\\") for k, v in pares}


def extrair_geofabrik(forcar: bool) -> Path:
    """Plano B: extrato regional do Geofabrik (PBF) recortado pela área com buffer via GDAL/ogr2ogr."""
    import shutil
    import subprocess

    pasta_g = PASTA / "geofabrik"
    pasta_g.mkdir(parents=True, exist_ok=True)
    pbf = pasta_g / "sul-latest.osm.pbf"
    if not pbf.exists() or forcar:
        logger.info("Baixando %s", GEOFABRIK_URL)
        with requests.get(GEOFABRIK_URL, headers={"User-Agent": USER_AGENT}, stream=True, timeout=600) as r:
            r.raise_for_status()
            with open(pbf.with_suffix(".part"), "wb") as f:
                shutil.copyfileobj(r.raw, f)
        pbf.with_suffix(".part").rename(pbf)
        (pasta_g / "state.txt").write_text(requests.get(GEOFABRIK_STATE, headers={"User-Agent": USER_AGENT}, timeout=60).text)
    state = (pasta_g / "state.txt").read_text() if (pasta_g / "state.txt").exists() else ""
    m = re.search(r"timestamp=(\S+)", state)
    ts = m.group(1).replace("\\:", ":") if m else None
    logger.info("PBF: %.1f MB, timestamp do extrato %s", pbf.stat().st_size / 1e6, ts)

    data = datetime.now().strftime("%Y-%m-%d")
    saida = PASTA / f"osm_completo_{data}.gpkg"
    if saida.exists() and not forcar:
        logger.info("Extrato já existe (%s)", saida.name)
        return saida

    # configuração do driver OSM: todas as tags num campo único (all_tags) + polígonos extras
    conf_padrao = Path(subprocess.run(["gdal-config", "--datadir"], capture_output=True, text=True).stdout.strip() or "/usr/share/gdal") / "osmconf.ini"
    if not conf_padrao.exists():
        conf_padrao = Path("/usr/share/gdal/osmconf.ini")
    conf = conf_padrao.read_text()
    conf = re.sub(r"^closed_ways_are_polygons=(.*)$", lambda mm: f"closed_ways_are_polygons={mm.group(1)},{POLIGONOS_EXTRA}", conf, flags=re.M)
    conf = re.sub(r"^#?\s*other_tags=.*$", "other_tags=no", conf, flags=re.M)
    conf = re.sub(r"^#?\s*all_tags=.*$", "all_tags=yes", conf, flags=re.M)
    if "all_tags=yes" not in conf:
        conf = re.sub(r"^(\[(points|lines|multipolygons|multilinestrings|other_relations)\])$", r"\1\nall_tags=yes\nother_tags=no", conf, flags=re.M)
    arq_conf = pasta_g / "osmconf_levantamento.ini"
    arq_conf.write_text(conf)

    mun, buf = area_consulta()
    w, s, e, n = buf.to_crs(CRS_OSM).total_bounds
    bruto = pasta_g / "recorte_bbox.gpkg"
    if not bruto.exists() or forcar:
        bruto.unlink(missing_ok=True)
        cmd = ["ogr2ogr", "-f", "GPKG", str(bruto), str(pbf), "-spat", f"{w}", f"{s}", f"{e}", f"{n}",
               "-oo", f"CONFIG_FILE={arq_conf}", "--config", "OSM_MAX_TMPFILE_SIZE", "2000", "-gt", "65536"]
        logger.info("Recortando o PBF pelo retângulo da área com buffer (ogr2ogr)")
        subprocess.run(cmd, check=True)

    poly = buf.geometry.iloc[0]
    camadas_gdal = {"points": "pontos", "lines": "linhas", "multipolygons": "poligonos",
                    "multilinestrings": "relacoes_rota", "other_relations": "relacoes"}
    tmp = saida.with_suffix(".tmp.gpkg")
    tmp.unlink(missing_ok=True)
    contagens = {}
    for lg, nome in camadas_gdal.items():
        g = gpd.read_file(bruto, layer=lg)
        tags = g["all_tags"].map(parse_hstore)
        if lg == "multipolygons":
            tipo = np.where(g["osm_id"].notna(), "relation", "way")
            ids = g["osm_id"].fillna(g["osm_way_id"])
        else:
            tipo = {"points": "node", "lines": "way"}.get(lg, "relation")
            ids = g["osm_id"]
        out = pd.DataFrame({"osm_tipo": tipo, "osm_id": pd.to_numeric(ids, errors="coerce").astype("Int64"),
                            "tags": tags.map(lambda t: json.dumps(t, ensure_ascii=False))})
        for c in COLUNAS_PRINCIPAIS:
            out[c] = tags.map(lambda t, c=c: t.get(c))
        gdf = gpd.GeoDataFrame(out, geometry=g.geometry.values, crs=g.crs).to_crs(CRS_PADRAO)
        if nome != "relacoes":
            gdf = gdf[gdf.geometry.notna() & gdf.intersects(poly)]  # feições inteiras que tocam a área
        gdf.to_file(tmp, layer=nome, driver="GPKG")
        contagens[nome] = len(gdf)
        logger.info("Camada %s: %d feições", nome, len(gdf))
        if nome == "poligonos":
            lim = gdf[gdf["boundary"] == "administrative"]
            lim.to_file(tmp, layer="relacoes_limite", driver="GPKG")
            contagens["relacoes_limite"] = len(lim)
    tmp.rename(saida)
    sidecar(saida, fonte="OpenStreetMap — extrato regional do Geofabrik (Brasil, Região Sul), plano B após 3 falhas do Overpass",
            url=GEOFABRIK_URL, timestamp_osm_base_max=ts, timestamp_extrato=ts, pbf_mb=round(pbf.stat().st_size / 1e6, 1),
            data_acesso=datetime.now(timezone.utc).isoformat(), area="área de estudo (config/area_estudo.geojson) + buffer de 2 km",
            crs=CRS_PADRAO, feicoes=contagens,
            limitacao="o extrato do Geofabrik é recortado pelo limite do Brasil Sul com uma faixa estreita além da fronteira: "
                      "a parte do buffer em território argentino/uruguaio pode estar incompleta (não afeta medidas dentro do município)",
            transformacao_aplicada="ogr2ogr (driver OSM do GDAL) com -spat no retângulo da área com buffer e osmconf com all_tags; "
                                   "camadas points/lines/multipolygons/multilinestrings/other_relations → pontos/linhas/poligonos/"
                                   "relacoes_rota/relacoes; relacoes_limite = polígonos boundary=administrative; reprojeção para "
                                   "EPSG:31981; mantidas as feições que tocam a área com buffer (sem corte). Campo 'tags' = todas as "
                                   "tags originais em JSON.",
            aviso="pode conter tags de contato/endereço (dado pessoal possível); bruto fora do git, não publicar valores")
    sidecar(pbf, fonte="Geofabrik — OpenStreetMap Brasil Sul", url=GEOFABRIK_URL, timestamp_extrato=ts,
            data_acesso=datetime.now(timezone.utc).isoformat(), transformacao_aplicada="nenhuma (arquivo como baixado)")
    logger.info("Extrato gravado: %s (%.1f MB)", saida, saida.stat().st_size / 1e6)
    return saida


# ---------------------------------------------------------------------------
# PARTE 1.3 — grafos osmnx
# ---------------------------------------------------------------------------
def grafos(forcar: bool) -> dict:
    import osmnx as ox

    ox.settings.cache_folder = str(RAIZ / "data" / "raw" / "cache_osmnx")
    ox.settings.http_user_agent = USER_AGENT
    ox.settings.useful_tags_way = USEFUL_TAGS_WAY
    ox.settings.requests_timeout = 600
    ox.settings.overpass_rate_limit = True
    _, buf = area_consulta()
    poly_ll = buf.to_crs(CRS_OSM).geometry.iloc[0]
    caminhos = {}
    for tipo in ["drive", "walk", "all"]:
        gml = PASTA / f"grafo_{tipo}.graphml"
        caminhos[tipo] = gml
        if gml.exists() and not forcar:
            logger.info("Grafo %s já existe — pulando", tipo)
            continue
        logger.info("Baixando grafo %s (osmnx, polígono com buffer)", tipo)
        for t in range(1, TENTATIVAS + 1):
            try:
                G = ox.graph_from_polygon(poly_ll, network_type=tipo, simplify=True, retain_all=True, truncate_by_edge=True)
                break
            except Exception as e:  # noqa: BLE001 — osmnx propaga erros variados de rede
                if t == TENTATIVAS:
                    raise RuntimeError(f"osmnx falhou 3 vezes no grafo {tipo}: {e}") from e
                logger.warning("osmnx falhou (%d/%d): %s", t, TENTATIVAS, e)
                time.sleep(30 * 2 ** (t - 1))
        ox.save_graphml(G, gml)
        Gp = ox.project_graph(G, to_crs=CRS_PADRAO)
        nos, arestas = ox.graph_to_gdfs(Gp)
        gp = PASTA / f"grafo_{tipo}.gpkg"
        gp.unlink(missing_ok=True)
        for col in arestas.columns:  # listas (trechos fundidos na simplificação) viram texto
            if arestas[col].map(lambda v: isinstance(v, list)).any():
                arestas[col] = arestas[col].map(lambda v: ";".join(map(str, v)) if isinstance(v, list) else v)
        for col in nos.columns:
            if nos[col].map(lambda v: isinstance(v, list)).any():
                nos[col] = nos[col].map(lambda v: ";".join(map(str, v)) if isinstance(v, list) else v)
        arestas.reset_index().to_file(gp, layer="arestas", driver="GPKG")
        nos.reset_index().to_file(gp, layer="nos", driver="GPKG")
        sidecar(gml, fonte="OpenStreetMap via osmnx/Overpass", network_type=tipo, simplify=True, retain_all=True,
                useful_tags_way=USEFUL_TAGS_WAY, data_acesso=datetime.now(timezone.utc).isoformat(),
                nos=G.number_of_nodes(), arestas=G.number_of_edges(), crs_graphml=CRS_OSM, crs_gpkg=CRS_PADRAO,
                transformacao_aplicada="graph_from_polygon (área de estudo + 2 km), simplificado, todas as componentes")
        time.sleep(PAUSA_S)
    return caminhos


def filtro_osmnx(tipo: str):
    """Converte o filtro Overpass do osmnx (["k"!~"a|b"]) numa função sobre o dicionário de tags."""
    from osmnx import _overpass

    regras = re.findall(r'\["([^"]+)"(?:(!~)"([^"]*)")?\]', _overpass._get_network_filter(tipo))

    def ok(t: dict) -> bool:
        for k, op, vals in regras:
            if not op:
                if k not in t:
                    return False
            elif k in t and re.search(vals, t[k]):
                return False
        return True
    return ok


def grafos_geofabrik(forcar: bool) -> dict:
    """Plano B dos grafos: mesma montagem do osmnx (create_graph → truncate → simplify) sobre as vias do PBF.

    O driver OSM do GDAL não traz o id dos nós; os nós são identificados pela coordenada (7 casas decimais,
    a precisão nativa do OSM), o que reproduz a topologia: vias se ligam quando compartilham o mesmo nó.
    """
    import osmnx as ox
    from osmnx import graph as oxg
    from osmnx import truncate

    ox.settings.useful_tags_way = USEFUL_TAGS_WAY  # sem isso o osmnx descarta surface, tracktype etc.
    bruto = PASTA / "geofabrik" / "recorte_bbox.gpkg"
    linhas = gpd.read_file(bruto, layer="lines")
    linhas = linhas[linhas["highway"].notna()].copy()
    linhas["t"] = linhas["all_tags"].map(parse_hstore)
    _, buf = area_consulta()
    # mesmo critério do osmnx: buffer extra de 500 m para baixar, truncar e simplificar, depois truncar no polígono
    poly_ll = buf.to_crs(CRS_OSM).geometry.iloc[0]
    poly_buff = buf.buffer(500).to_crs(CRS_OSM).geometry.iloc[0]
    meta_pbf = json.loads((PASTA / "geofabrik" / "sul-latest.osm.json").read_text(encoding="utf-8"))
    caminhos = {}
    for tipo in ["drive", "walk", "all"]:
        gml = PASTA / f"grafo_{tipo}_geofabrik.graphml"
        caminhos[tipo] = gml
        if gml.exists() and not forcar:
            logger.info("Grafo %s (Geofabrik) já existe — pulando", tipo)
            continue
        ok = filtro_osmnx(tipo)
        sel = linhas[linhas["t"].map(ok)]
        ids_no, nos, vias = {}, [], []
        for _, r in sel.iterrows():
            geoms = getattr(r.geometry, "geoms", [r.geometry])
            for g in geoms:
                refs = []
                for x, y in g.coords:
                    k = (round(x, 7), round(y, 7))
                    if k not in ids_no:
                        ids_no[k] = len(ids_no) + 1
                        nos.append({"type": "node", "id": ids_no[k], "lat": k[1], "lon": k[0]})
                    refs.append(ids_no[k])
                vias.append({"type": "way", "id": int(r.osm_id), "nodes": refs, "tags": r.t})
        G = oxg._create_graph([{"elements": nos + vias}], bidirectional=tipo in ox.settings.bidirectional_network_types)
        G = truncate.truncate_graph_polygon(G, poly_buff, truncate_by_edge=True)
        G = ox.simplify_graph(G)
        G = truncate.truncate_graph_polygon(G, poly_ll, truncate_by_edge=True)
        spn = ox.stats.count_streets_per_node(G)
        import networkx as nx
        nx.set_node_attributes(G, values=spn, name="street_count")
        ox.save_graphml(G, gml)
        Gp = ox.project_graph(G, to_crs=CRS_PADRAO)
        nos_g, arestas = ox.graph_to_gdfs(Gp)
        for df in (nos_g, arestas):
            for col in df.columns:
                if df[col].map(lambda v: isinstance(v, list)).any():
                    df[col] = df[col].map(lambda v: ";".join(map(str, v)) if isinstance(v, list) else v)
        gp = gml.with_suffix(".gpkg")
        gp.unlink(missing_ok=True)
        arestas.reset_index().to_file(gp, layer="arestas", driver="GPKG")
        nos_g.reset_index().to_file(gp, layer="nos", driver="GPKG")
        sidecar(gml, fonte="OpenStreetMap — extrato do Geofabrik (plano B: Overpass indisponível para o osmnx)",
                timestamp_extrato=meta_pbf.get("timestamp_extrato"), network_type=tipo, filtro_osmnx="osmnx._overpass._get_network_filter",
                simplify=True, retain_all=True, useful_tags_way=USEFUL_TAGS_WAY, nos=G.number_of_nodes(),
                arestas=G.number_of_edges(), crs_graphml=CRS_OSM, crs_gpkg=CRS_PADRAO,
                transformacao_aplicada="vias do PBF filtradas com o filtro de rede do osmnx; nós identificados pela coordenada "
                                       "(id sintético, não o id do OSM); osmnx._create_graph, truncate (polígono +500 m), "
                                       "simplify_graph, truncate (área de estudo + 2 km)")
        logger.info("Grafo %s (Geofabrik): %d nós, %d arestas", tipo, G.number_of_nodes(), G.number_of_edges())
    return caminhos


# ---------------------------------------------------------------------------
# PARTE 2 — diagnóstico
# ---------------------------------------------------------------------------
def carregar_extrato() -> tuple[dict, Path]:
    arq = sorted(PASTA.glob("osm_completo_*.gpkg"))[-1]
    camadas = {c: gpd.read_file(arq, layer=c) for c in ["pontos", "linhas", "poligonos", "relacoes", "relacoes_limite"]}
    for gdf in camadas.values():
        gdf["tags_d"] = gdf["tags"].map(json.loads)
    return camadas, arq


def recortar(gdf: gpd.GeoDataFrame, geom) -> gpd.GeoDataFrame:
    """Recorte geométrico (para medir km/km² dentro de um polígono)."""
    sub = gdf[gdf.intersects(geom)].copy()
    sub["geometry"] = sub.geometry.intersection(geom)
    return sub[~sub.geometry.is_empty]


def pct(num, den):
    return round(100 * num / den, 1) if den else None


def norm_txt(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().upper()
    return re.sub(r"\s+", " ", re.sub(r"[^A-Z0-9 ]", " ", s)).strip()


ABREV = {
    "AV": "AVENIDA", "R": "RUA", "TV": "TRAVESSA", "TRAV": "TRAVESSA", "DR": "DOUTOR", "CEL": "CORONEL",
    "GAL": "GENERAL", "GEN": "GENERAL", "PROF": "PROFESSOR", "PROFA": "PROFESSORA", "PRES": "PRESIDENTE",
    "MAL": "MARECHAL", "CAP": "CAPITAO", "TEN": "TENENTE", "STO": "SANTO", "STA": "SANTA", "SGT": "SARGENTO",
    "SGTO": "SARGENTO", "ENG": "ENGENHEIRO", "PE": "PADRE", "VER": "VEREADOR", "DES": "DESEMBARGADOR",
    "BRIG": "BRIGADEIRO", "ALM": "ALMIRANTE", "EST": "ESTRADA", "ESTR": "ESTRADA", "PCA": "PRACA", "AL": "ALAMEDA",
    "SEN": "SENADOR", "DEP": "DEPUTADO", "MAJ": "MAJOR", "CB": "CABO", "GOV": "GOVERNADOR", "CONS": "CONSELHEIRO",
    "MONS": "MONSENHOR", "ROD": "RODOVIA", "VISC": "VISCONDE", "BR": "BR", "DONA": "DONA",
}
TIPOS_LOGR = {"RUA", "AVENIDA", "TRAVESSA", "ESTRADA", "RODOVIA", "PRACA", "ALAMEDA", "BECO", "PASSAGEM", "VIA",
              "ACESSO", "LARGO", "VIELA", "CAMINHO", "PRACA"}
TITULOS = {"DOUTOR", "CORONEL", "GENERAL", "PROFESSOR", "PROFESSORA", "PRESIDENTE", "MARECHAL", "CAPITAO", "TENENTE",
           "SARGENTO", "ENGENHEIRO", "PADRE", "VEREADOR", "DESEMBARGADOR", "BRIGADEIRO", "ALMIRANTE", "SENADOR",
           "DEPUTADO", "MAJOR", "CABO", "GOVERNADOR", "CONSELHEIRO", "MONSENHOR", "VISCONDE", "DONA", "DOM", "SAO"}
PREPOS = {"DE", "DA", "DO", "DAS", "DOS", "E"}
RE_ABREV = re.compile(r"\b(Av|R|Tv|Trav|Dr|Dra|Cel|Gal|Gen|Prof|Profa|Pres|Mal|Cap|Ten|Sto|Sta|Sgt|Sgto|Eng|Pe|Ver|Des|"
                      r"Brig|Alm|Est|Estr|Pça|Pca|Sen|Dep|Maj|Cb|Gov|Cons|Mons|Rod|Visc)\.?(?=\s)", re.IGNORECASE)


def nome_normalizado(s: str) -> str:
    toks = [ABREV.get(t, t) for t in norm_txt(s).split()]
    return " ".join(toks)


def nome_nucleo(s: str) -> str:
    toks = nome_normalizado(s).split()
    while toks and toks[0] in TIPOS_LOGR:
        toks = toks[1:]
    return " ".join(t for t in toks if t not in TITULOS and t not in PREPOS)


def compat_iniciais(a: str, b: str) -> bool:
    """'GREGORIO B FILHO' x 'GREGORIO BARBOSA FILHO': mesmas palavras, iniciais aceitas."""
    ta, tb = a.split(), b.split()
    if len(ta) != len(tb) or not ta:
        return False
    return all(x == y or (len(x) == 1 and y.startswith(x)) or (len(y) == 1 and x.startswith(y)) for x, y in zip(ta, tb))


def diagnostico(saida: Path) -> dict:
    saida.mkdir(parents=True, exist_ok=True)
    cam, arq = carregar_extrato()
    meta_extrato = json.loads(arq.with_suffix(".json").read_text(encoding="utf-8"))
    mun, _ = area_consulta()
    g_mun = mun.union_all()
    urb = gpd.read_file(REF["urbano"]).to_crs(CRS_PADRAO)
    g_urb = urb.union_all().intersection(g_mun)
    g_rur = g_mun.difference(g_urb)
    res = {"extrato": arq.name, "timestamp_osm_base": meta_extrato.get("timestamp_osm_base_max"),
           "tamanho_gpkg_mb": round(arq.stat().st_size / 1e6, 1),
           "tamanho_blocos_gz_mb": round(sum(p.stat().st_size for p in PASTA_BLOCOS.glob("*.json.gz")) / 1e6, 1)}

    pts, lin, pol = cam["pontos"], cam["linhas"], cam["poligonos"]
    # elementos dentro do município (intersecta), e versões recortadas para medir
    pts_m = pts[pts.within(g_mun)].copy()
    lin_m = recortar(lin, g_mun)
    lin_m["km"] = lin_m.length / 1000
    pol_m = recortar(pol, g_mun)
    pol_m["km2"] = pol_m.area / 1e6
    rel = cam["relacoes"]

    # A. censo por chave -----------------------------------------------------
    linhas_censo, linhas_val = [], []
    geoms = [("ponto", pts_m, None), ("linha", lin_m, "km"), ("poligono", pol_m, "km2"), ("relacao", rel, None)]
    for chave in CHAVES_CENSO + ["addr:*"]:
        for nome_g, gdf, medida in geoms:
            if chave == "addr:*":
                mask = gdf["tags_d"].map(lambda t: any(k.startswith("addr:") for k in t))
                vals = pd.Series(["(qualquer addr:*)"] * mask.sum(), index=gdf.index[mask])
            else:
                vals = gdf["tags_d"].map(lambda t, c=chave: t.get(c))
                mask = vals.notna()
                vals = vals[mask]
            n = int(mask.sum())
            if not n:
                continue
            linhas_censo.append({"chave": chave, "geometria": nome_g, "n_elementos": n,
                                 "km": round(gdf.loc[mask, "km"].sum(), 1) if medida == "km" else None,
                                 "km2": round(gdf.loc[mask, "km2"].sum(), 3) if medida == "km2" else None,
                                 "valores_distintos": int(vals.nunique())})
            top = vals.value_counts().head(20)
            for v, c in top.items():
                idx = vals.index[vals == v]
                linhas_val.append({"chave": chave, "geometria": nome_g, "valor": v, "n": int(c),
                                   "km": round(gdf.loc[idx, "km"].sum(), 1) if medida == "km" else None,
                                   "km2": round(gdf.loc[idx, "km2"].sum(), 3) if medida == "km2" else None})
    pd.DataFrame(linhas_censo).to_csv(saida / "A_censo_por_chave.csv", index=False)
    pd.DataFrame(linhas_val).to_csv(saida / "A_censo_top20_valores.csv", index=False)
    if any(k.startswith("addr:") for t in pd.concat([pts_m.tags_d, lin_m.tags_d, pol_m.tags_d]) for k in t):
        addr = Counter(k for t in pd.concat([pts_m.tags_d, lin_m.tags_d, pol_m.tags_d]) for k in t if k.startswith("addr:"))
        pd.DataFrame(sorted(addr.items(), key=lambda x: -x[1]), columns=["tag", "n_elementos"]).to_csv(
            saida / "A_censo_addr.csv", index=False)
    # tags com possível dado pessoal: só contagens
    pess = []
    todos = pd.concat([pts_m[["tags_d"]], lin_m[["tags_d"]], pol_m[["tags_d"]]])
    nao_viario = todos["tags_d"].map(lambda t: "highway" not in t and "waterway" not in t)
    for tg in TAGS_PESSOAIS:
        if tg.endswith(":*"):
            pre = tg[:-1]
            n = todos["tags_d"].map(lambda t, p=pre: any(k.startswith(p) for k in t)).sum()
        else:
            n = todos["tags_d"].map(lambda t, k=tg: k in t).sum()
        pess.append({"tag": tg, "n_elementos": int(n),
                     "n_fora_de_vias_e_cursos": int(todos.loc[nao_viario, "tags_d"].map(
                         lambda t, k=tg: any(x.startswith(k[:-1]) for x in t) if k.endswith(":*") else k in t).sum())})
    pd.DataFrame(pess).to_csv(saida / "A_tags_dado_pessoal_contagem.csv", index=False)
    res["A"] = {"pontos": len(pts_m), "linhas": len(lin_m), "poligonos": len(pol_m), "relacoes_bbox": len(rel),
                "tags_pessoais": {p["tag"]: p["n_elementos"] for p in pess}}

    # B. viário por classe --------------------------------------------------
    vias = lin_m[lin_m["highway"].notna()].copy()
    vias["zona"] = np.where(vias.geometry.representative_point().within(g_urb), "urbano", "rural")
    # trecho que cruza a borda urbana: corta de verdade para não atribuir km errado
    vias_u = recortar(vias, g_urb).assign(zona="urbano")
    vias_r = recortar(vias, g_rur).assign(zona="rural")
    vias = pd.concat([vias_u, vias_r])
    vias["km"] = vias.length / 1000
    vias["classe"] = vias["highway"].str.replace("_link", "", regex=False)
    atrib = ["name", "ref", "surface", "maxspeed", "lanes", "oneway", "bridge", "tunnel", "tracktype", "smoothness", "lit"]
    for a in atrib:
        vias[f"tem_{a}"] = vias["tags_d"].map(lambda t, a=a: a in t and t[a] not in ("no",) if a in ("bridge", "tunnel") else a in t)
    linhas_b = []
    for zona in ["urbano", "rural", "total"]:
        sub = vias if zona == "total" else vias[vias.zona == zona]
        for cl, g in sub.groupby("classe"):
            km = g.km.sum()
            reg = {"zona": zona, "classe": cl, "n_trechos": len(g), "km": round(km, 2)}
            for a in atrib:
                reg[f"pct_km_{a}"] = pct(g.loc[g[f"tem_{a}"], "km"].sum(), km)
            linhas_b.append(reg)
    tb = pd.DataFrame(linhas_b)
    tb["ordem"] = tb.classe.map(lambda c: CLASSES_VIARIAS.index(c) if c in CLASSES_VIARIAS else 99)
    tb.sort_values(["zona", "ordem", "km"], ascending=[True, True, False]).drop(columns="ordem").to_csv(
        saida / "B_viario_por_classe.csv", index=False)
    trk = vias[vias.classe == "track"].copy()
    trk["tracktype"] = trk["tags_d"].map(lambda t: t.get("tracktype", "(sem)"))
    trk["surface_v"] = trk["tags_d"].map(lambda t: t.get("surface", "(sem)"))
    trk.groupby(["zona", "tracktype", "surface_v"]).km.sum().round(2).reset_index().sort_values("km", ascending=False).to_csv(
        saida / "B_track_tracktype_surface.csv", index=False)
    veic = vias[vias.highway.isin(VEICULARES)]
    res["B"] = {z: {"km_todas": round(vias[vias.zona == z].km.sum(), 1), "km_veiculares": round(veic[veic.zona == z].km.sum(), 1),
                    "pct_km_nome_veiculares": pct(veic[(veic.zona == z) & veic.tem_name].km.sum(), veic[veic.zona == z].km.sum()),
                    "pct_km_nome_todas": pct(vias[(vias.zona == z) & vias.tem_name].km.sum(), vias[vias.zona == z].km.sum())}
                for z in ["urbano", "rural"]}
    res["B"]["track_km"] = round(trk.km.sum(), 1)
    res["B"]["track_pct_km_tracktype"] = pct(trk[trk.tracktype != "(sem)"].km.sum(), trk.km.sum())
    res["B"]["track_pct_km_surface"] = pct(trk[trk.surface_v != "(sem)"].km.sum(), trk.km.sum())

    # C. nomes de ruas -------------------------------------------------------
    nomeadas = vias[vias.tem_name].copy()
    linhas_c = []
    for zona in ["urbano", "rural", "total"]:
        sub = vias if zona == "total" else vias[vias.zona == zona]
        subv = sub[sub.highway.isin(VEICULARES)]
        linhas_c.append({"zona": zona, "km_vias": round(sub.km.sum(), 1), "km_nomeadas": round(sub[sub.tem_name].km.sum(), 1),
                         "pct_km_nomeadas": pct(sub[sub.tem_name].km.sum(), sub.km.sum()),
                         "pct_km_nomeadas_veiculares_sem_track": pct(subv[subv.tem_name & (subv.classe != "track")].km.sum(),
                                                                       subv[subv.classe != "track"].km.sum()),
                         "n_trechos_nomeados": int(sub.tem_name.sum()), "nomes_unicos": int(sub[sub.tem_name].name.nunique())})
    # nome "duplicado": mesmo nome em grupos de trechos separados por mais de 200 m
    grupos = []
    for nm, g in nomeadas.groupby("name"):
        partes = list(unary_union(g.geometry.buffer(100)).geoms) if g.geometry.buffer(100).union_all().geom_type == "MultiPolygon" else [1]
        grupos.append((nm, len(partes)))
    dup = [g for g in grupos if g[1] > 1]
    pd.DataFrame(linhas_c).to_csv(saida / "C_nomes_resumo.csv", index=False)
    pd.DataFrame(sorted(dup, key=lambda x: -x[1]), columns=["nome", "n_grupos_separados_200m"]).to_csv(
        saida / "C_nomes_duplicados.csv", index=False)
    variantes = {k: int(vias["tags_d"].map(lambda t, k=k: k in t).sum()) for k in
                 ["name", "official_name", "alt_name", "old_name", "name:pt", "short_name", "loc_name", "reg_name"]}
    primeiro = Counter(str(n).split()[0] for n in nomeadas.name.unique())
    abrevs = Counter(m.group(0).rstrip(".").capitalize() + "." for n in nomeadas.name.unique() for m in RE_ABREV.finditer(str(n) + " "))
    pd.DataFrame([{"tipo": "variante_tag", "item": k, "n_trechos": v} for k, v in variantes.items()]
                 + [{"tipo": "primeira_palavra", "item": k, "n_trechos": v} for k, v in primeiro.most_common(25)]
                 + [{"tipo": "abreviacao", "item": k, "n_trechos": v} for k, v in abrevs.most_common()]).rename(
        columns={"n_trechos": "n"}).to_csv(saida / "C_nomes_variantes_abreviacoes.csv", index=False)
    # cruzamento com os 236 nomes do Plano Diretor (só o campo de nome)
    topo = gpd.read_file(REF["toponimia"], layer="nomes_de_ruas").to_crs(CRS_PADRAO)
    nomes_osm = set(vias.name.dropna()) | {t[k] for t in vias.tags_d for k in ("official_name", "alt_name", "old_name") if k in t}
    osm_exato = set(nomes_osm)
    osm_norm = {nome_normalizado(n) for n in nomes_osm}
    osm_nuc = {nome_nucleo(n) for n in nomes_osm} - {""}
    linhas_x = []
    for _, r in topo.iterrows():
        t = r.texto
        nn, nu = nome_normalizado(t), nome_nucleo(t)
        exato = t in osm_exato or t.title() in osm_exato
        caseless = norm_txt(t) in {norm_txt(n) for n in nomes_osm}
        normal = nn in osm_norm
        nucleo = nu in osm_nuc if nu else False
        aprox = nucleo or (bool(nu) and any(compat_iniciais(nu, o) or SequenceMatcher(None, nu, o).ratio() >= 0.88 for o in osm_nuc))
        # perto: alguma via OSM a até 150 m do rótulo com o mesmo núcleo/aproximado
        perto = vias[vias.distance(r.geometry) <= 150]
        perto_ok = any(nome_nucleo(n) == nu or (nu and SequenceMatcher(None, nu, nome_nucleo(n)).ratio() >= 0.88)
                       for n in perto.name.dropna()) if nu else False
        linhas_x.append({"texto_plano": t, "exato": exato, "sem_acento_caixa": caseless, "normalizado_abrev": normal,
                         "nucleo_sem_tipo_titulo": nucleo, "aproximado": aprox, "via_osm_a_150m_com_mesmo_nome": perto_ok,
                         "generico": bool(re.fullmatch(r"RUA (\d+|[A-Z]|UM|DOIS|TRES|SETE|SEM SAIDA)", norm_txt(t)))})
    tx = pd.DataFrame(linhas_x)
    tx.to_csv(saida / "C_cruzamento_nomes_plano_x_osm.csv", index=False)
    txu = tx.drop_duplicates("texto_plano")
    res["C"] = {"resumo": linhas_c, "nomes_duplicados_grupos": len(dup), "variantes": variantes,
                "abreviacoes": dict(abrevs.most_common(10)),
                "plano_rotulos": len(tx), "plano_nomes_unicos": len(txu),
                **{f"plano_pct_{c}": pct(txu[c].sum(), len(txu)) for c in
                   ["exato", "sem_acento_caixa", "normalizado_abrev", "nucleo_sem_tipo_titulo", "aproximado",
                    "via_osm_a_150m_com_mesmo_nome"]},
                "plano_genericos": int(txu.generico.sum())}

    # D. quadras e áreas novas ----------------------------------------------
    quadras = gpd.read_file(REF["quadras"], layer="quadras").to_crs(CRS_PADRAO)
    # envoltória das quadras do Plano: união com 30 m (cobre a caixa das ruas) e fechamento de 15 m
    env_plano = quadras.union_all().buffer(30).buffer(-15)
    partes_novas = g_urb.difference(env_plano)
    # as quadras extraídas não fecham toda a planta (há traços soltos): a diferença acima mistura área nova com
    # lacuna de extração; o casco convexo das quadras dá a estimativa conservadora (expansão além da área desenhada)
    partes_novas_casco = g_urb.difference(quadras.union_all().convex_hull)
    res_d = {"quadras_plano": len(quadras), "area_quadras_plano_km2": round(quadras.area.sum() / 1e6, 3),
             "envoltoria_plano_km2": round(env_plano.area / 1e6, 3), "area_urbanizada_ibge_km2": round(g_urb.area / 1e6, 3),
             "partes_novas_urbanizado_fora_do_plano_km2": round(partes_novas.area / 1e6, 3)}
    lu = pol_m[pol_m["landuse"].notna()]
    linhas_d = []
    for v, g in lu.groupby("landuse"):
        linhas_d.append({"landuse": v, "n": len(g), "km2_municipio": round(g.km2.sum(), 3),
                         "km2_urbano_ibge": round(g.geometry.intersection(g_urb).area.sum() / 1e6, 3),
                         "km2_partes_novas": round(g.geometry.intersection(partes_novas).area.sum() / 1e6, 3)})
    pd.DataFrame(linhas_d).sort_values("km2_municipio", ascending=False).to_csv(saida / "D_landuse_areas.csv", index=False)
    urb_lu = lu[lu.landuse.isin(["residential", "commercial", "industrial", "retail"])]
    edif = pol_m[pol_m["building"].notna()].copy()
    edif_c = edif.geometry.representative_point()
    res_d["landuse_urbano_km2_municipio"] = round(urb_lu.km2.sum(), 3)
    res_d["landuse_urbano_km2_partes_novas"] = round(urb_lu.union_all().intersection(partes_novas).area / 1e6, 3) if len(urb_lu) else 0
    res_d["pct_partes_novas_cobertas_landuse"] = pct(res_d["landuse_urbano_km2_partes_novas"], res_d["partes_novas_urbanizado_fora_do_plano_km2"])
    res_d["edificacoes_partes_novas"] = int(edif_c.within(partes_novas).sum())
    res_d["edificacoes_dentro_envoltoria_plano"] = int(edif_c.within(env_plano).sum())
    # vias OSM dentro das partes novas (km)
    res_d["km_vias_partes_novas"] = round(recortar(vias, partes_novas).length.sum() / 1000, 1)
    lugares = pd.concat([pts_m, pol_m])
    res_d["place"] = lugares["place"].value_counts().to_dict()
    lim = cam["relacoes_limite"]
    res_d["limites_admin_level"] = lim["admin_level"].value_counts().to_dict()
    lim_mun = lim[lim.geometry.notna() & lim.intersects(g_mun.buffer(-500))] if len(lim) else lim
    res_d["limites_admin_level_no_municipio"] = lim_mun["admin_level"].value_counts().to_dict() if len(lim_mun) else {}
    # poligonização da malha urbana (quadras derivadas das vias), sem salvar como produto
    malha = vias[(vias.zona == "urbano") & vias.highway.isin(VEICULARES - {"track"})]
    malha = malha[~malha["tags_d"].map(lambda t: t.get("service") in {"parking_aisle", "driveway", "drive-through"})]
    linhas_rede = list(malha.geometry.explode(index_parts=False)) + [g_urb.boundary]
    blocos = gpd.GeoSeries(list(polygonize(unary_union(linhas_rede))), crs=CRS_PADRAO)
    blocos = blocos[blocos.within(g_urb.buffer(1))]
    blocos = blocos[(blocos.area >= 1000) & (blocos.area <= 300_000)]
    res_d["quadras_derivadas_n"] = len(blocos)
    res_d["quadras_derivadas_km2"] = round(blocos.area.sum() / 1e6, 3)
    res_d["quadras_derivadas_partes_novas_n"] = int(blocos.representative_point().within(partes_novas).sum())
    res_d["partes_novas_fora_do_casco_plano_km2"] = round(partes_novas_casco.area / 1e6, 3)
    res_d["landuse_urbano_km2_fora_casco"] = round(urb_lu.union_all().intersection(partes_novas_casco).area / 1e6, 3) if len(urb_lu) else 0
    res_d["pct_fora_casco_coberto_landuse"] = pct(res_d["landuse_urbano_km2_fora_casco"], res_d["partes_novas_fora_do_casco_plano_km2"])
    res_d["edificacoes_fora_casco"] = int(edif_c.within(partes_novas_casco).sum())
    res_d["km_vias_fora_casco"] = round(recortar(vias, partes_novas_casco).length.sum() / 1000, 1)
    res_d["quadras_derivadas_fora_casco_n"] = int(blocos.representative_point().within(partes_novas_casco).sum())
    res_d["quadras_derivadas_area_mediana_m2"] = round(float(blocos.area.median()), 0) if len(blocos) else None
    pd.DataFrame([res_d]).T.to_csv(saida / "D_quadras_partes_novas_resumo.csv", header=["valor"])
    res["D"] = res_d

    # E. edificações ---------------------------------------------------------
    tp = edif["tags_d"]
    res_e = {"n": len(edif), "area_km2": round(edif.km2.sum(), 3),
             "n_urbano": int(edif_c.within(g_urb).sum()), "n_rural": int((~edif_c.within(g_urb)).sum()),
             "pct_building_levels": pct(tp.map(lambda t: "building:levels" in t).sum(), len(edif)),
             "pct_addr": pct(tp.map(lambda t: any(k.startswith("addr:") for k in t)).sum(), len(edif)),
             "pct_name": pct(tp.map(lambda t: "name" in t).sum(), len(edif)),
             "tipos_top": edif["building"].value_counts().head(15).to_dict()}
    bai = gpd.read_file(REF["bairros"]).to_crs(CRS_PADRAO)
    pc = gpd.GeoDataFrame(geometry=edif_c.values, crs=CRS_PADRAO)
    j = gpd.sjoin(pc, bai[["NM_BAIRRO", "geometry"]], predicate="within")
    cont = j.groupby("NM_BAIRRO").size()
    tb_e = bai[["NM_BAIRRO", "enderecos_domicilio_particular"]].copy()
    tb_e["edificacoes_osm"] = tb_e.NM_BAIRRO.map(cont).fillna(0).astype(int)
    tb_e["razao_edif_osm_por_endereco_domicilio"] = (tb_e.edificacoes_osm / tb_e.enderecos_domicilio_particular).round(3)
    tb_e.sort_values("razao_edif_osm_por_endereco_domicilio").to_csv(saida / "E_edificacoes_por_bairro_vs_cnefe.csv", index=False)
    res_e["bairros_edif_osm"] = int(tb_e.edificacoes_osm.sum())
    res_e["bairros_enderecos_domicilio"] = int(tb_e.enderecos_domicilio_particular.sum())
    res_e["razao_geral_bairros"] = round(res_e["bairros_edif_osm"] / res_e["bairros_enderecos_domicilio"], 3)
    res_e["razao_bairros_mediana"] = round(float(tb_e.razao_edif_osm_por_endereco_domicilio.median()), 3)
    res_e["bairros_com_razao_menor_0_25"] = int((tb_e.razao_edif_osm_por_endereco_domicilio < 0.25).sum())
    pd.DataFrame([res_e]).T.to_csv(saida / "E_edificacoes_resumo.csv", header=["valor"])
    res["E"] = res_e

    # F. hidrografia e travessias -------------------------------------------
    ww = lin_m[lin_m["waterway"].isin(WATERWAYS)].copy()
    ww["km"] = ww.length / 1000
    ww["nomeado"] = ww["name"].notna()
    ww["zona"] = np.where(ww.geometry.representative_point().within(g_urb), "urbano", "rural")
    ww.groupby(["waterway", "nomeado"]).agg(n=("km", "size"), km=("km", "sum")).round(2).reset_index().to_csv(
        saida / "F_waterway_por_tipo.csv", index=False)
    bho = gpd.read_file(REF["bho"], layer="curso_dagua").to_crs(CRS_PADRAO)
    bho_km = recortar(bho, g_mun).length.sum() / 1000
    hp = gpd.read_file(REF["hidro_plano"], layer="linhas_de_agua").to_crs(CRS_PADRAO)
    res_f = {"waterway_km": round(ww.km.sum(), 1), "waterway_n": len(ww),
             "waterway_km_nomeado": round(ww[ww.nomeado].km.sum(), 1),
             "por_tipo_km": ww.groupby("waterway").km.sum().round(1).to_dict(),
             "bho_curso_dagua_km_municipio": round(bho_km, 2),
             "pct_osm_sobre_bho_km": pct(ww.km.sum(), bho_km),
             "plano_linhas_dagua_km": round(hp.length.sum() / 1000, 1),
             "osm_waterway_km_na_envoltoria_plano": round(recortar(ww, env_plano.buffer(500)).length.sum() / 1000, 1),
             "plano_linhas_dagua_km_na_envoltoria": round(recortar(hp, env_plano.buffer(500)).length.sum() / 1000, 1)}
    # BHO a até 50 m de algum curso OSM (concordância grosseira de traçado)
    bho_m = recortar(bho, g_mun)
    bho_seg = bho_m.geometry.explode(index_parts=False)
    ww_buf = ww.geometry.buffer(50).union_all()
    res_f["pct_km_bho_a_50m_de_waterway_osm"] = pct(bho_seg.intersection(ww_buf).length.sum(), bho_seg.length.sum())
    # pontes
    br = vias[vias["tags_d"].map(lambda t: t.get("bridge") not in (None, "no"))]
    br_mm = pol_m[pol_m["man_made"] == "bridge"]
    res_f.update(pontes_trechos=len(br), pontes_km=round(br.km.sum(), 2), pontes_com_nome=int(br.tem_name.sum()),
                 pontes_nome_tag_bridge_name=int(br["tags_d"].map(lambda t: "bridge:name" in t).sum()),
                 pontes_man_made_bridge=len(br_mm), pontes_por_classe=br.classe.value_counts().to_dict())
    # cruzamentos via × curso d'água
    vv = vias[vias.highway.isin(VEICULARES | {"path", "footway", "cycleway", "pedestrian", "bridleway"})]
    vv = vv[["osm_id", "name", "highway", "classe", "zona", "tags_d", "geometry"]].reset_index(drop=True)
    wl = ww[["osm_id", "name", "waterway", "tags_d", "geometry"]].reset_index(drop=True)
    pares = gpd.sjoin(vv, wl, predicate="intersects", lsuffix="via", rsuffix="ww")
    fords = pts_m[pts_m["tags_d"].map(lambda t: t.get("ford") not in (None, "no"))].geometry
    fords_u = fords.buffer(15).union_all() if len(fords) else None
    cr = []
    for _, p in pares.iterrows():
        inter = p.geometry.intersection(wl.geometry.iloc[p.index_ww])
        for pt in getattr(inter, "geoms", [inter]):
            if pt.geom_type != "Point":
                pt = pt.representative_point()
            tv, tw = p.tags_d_via, p.tags_d_ww
            marcado = (tv.get("bridge") not in (None, "no") or tv.get("tunnel") not in (None, "no")
                       or tw.get("tunnel") in ("culvert", "yes") or tv.get("ford") not in (None, "no")
                       or (fords_u is not None and pt.within(fords_u)))
            tipo = ("ponte" if tv.get("bridge") not in (None, "no") else "bueiro/tunel" if (tw.get("tunnel") or tv.get("tunnel"))
                    else "vau" if (tv.get("ford") or (fords_u is not None and pt.within(fords_u))) else "sem marcacao")
            cr.append({"geometry": pt, "via_classe": p.classe, "via_nome": p.name_via, "via_osm_id": p.osm_id_via,
                       "curso_tipo": p.waterway, "curso_nome": p.name_ww, "curso_osm_id": p.osm_id_ww, "zona": p.zona,
                       "estrutura_marcada": marcado, "tipo_marcacao": tipo})
    crg = gpd.GeoDataFrame(cr, crs=CRS_PADRAO)
    # agrega pontos a menos de 20 m (mesma travessia em trechos vizinhos/pistas duplas)
    if len(crg):
        cl = gpd.GeoSeries(list(unary_union(crg.geometry.buffer(10)).geoms) if len(crg) > 1 else [crg.geometry.iloc[0].buffer(10)],
                           crs=CRS_PADRAO)
        crg["grupo"] = gpd.sjoin(crg[["geometry"]], gpd.GeoDataFrame(geometry=cl), predicate="within")["index_right"].groupby(level=0).first()
        agg = crg.sort_values("estrutura_marcada", ascending=False).groupby("grupo").first()
        agg = gpd.GeoDataFrame(agg, geometry="geometry", crs=CRS_PADRAO)
    else:
        agg = crg
    cand = agg[~agg.estrutura_marcada].copy()
    ll = cand.to_crs(CRS_OSM)
    cand["latitude"], cand["longitude"] = ll.geometry.y.round(6), ll.geometry.x.round(6)
    cand = cand.sort_values(["zona", "via_classe"])
    cand.insert(0, "codigo", [f"OSM-T{i + 1:03d}" for i in range(len(cand))])
    cand.drop(columns=["geometry", "estrutura_marcada"]).to_csv(saida / "F_travessias_candidatas_sem_ponte.csv", index=False)
    agg.groupby(["zona", "tipo_marcacao"]).size().rename("n").reset_index().to_csv(saida / "F_cruzamentos_resumo.csv", index=False)
    res_f.update(cruzamentos_pontos_brutos=len(crg), cruzamentos_n=len(agg),
                 cruzamentos_por_tipo=agg.tipo_marcacao.value_counts().to_dict(),
                 cruzamentos_urbano=int((agg.zona == "urbano").sum()), cruzamentos_rural=int((agg.zona == "rural").sum()),
                 candidatos_sem_marcacao=len(cand),
                 candidatos_veiculares_sem_track=int((cand.via_classe.isin({c.replace("_link", "") for c in VEICULARES} - {"track"})).sum()),
                 candidatos_urbano=int((cand.zona == "urbano").sum()))
    # comparação com a lista de campo (P01–P05, T01–T33)
    campo = gpd.read_file(REF["campo"]).to_crs(CRS_PADRAO)
    br_u = br.geometry.buffer(30).union_all() if len(br) else None
    camp = []
    for _, r in campo.iterrows():
        d_cand = cand.distance(r.geometry).min() if len(cand) else None
        camp.append({"codigo": r.codigo, "tipo": r.tipo,
                     "ponte_osm_a_30m": bool(br_u is not None and r.geometry.within(br_u)),
                     "cruzamento_osm_a_50m": bool(len(agg) and agg.distance(r.geometry).min() <= 50),
                     "candidato_sem_marcacao_a_50m": bool(d_cand is not None and d_cand <= 50)})
    tc = pd.DataFrame(camp)
    tc.to_csv(saida / "F_lista_campo_x_osm_novo.csv", index=False)
    res_f["campo_P_ainda_ponte"] = int(tc[tc.codigo.str.startswith("P")].ponte_osm_a_30m.sum())
    res_f["campo_T_agora_com_ponte"] = int(tc[tc.codigo.str.startswith("T")].ponte_osm_a_30m.sum())
    res_f["campo_T_coincide_cruzamento_osm"] = int(tc[tc.codigo.str.startswith("T")].cruzamento_osm_a_50m.sum())
    # cruzamentos com a BHO (método da lista de campo), para comparação
    bho_tr = recortar(gpd.read_file(REF["bho"], layer="trecho_drenagem").to_crs(CRS_PADRAO), g_mun)
    vveic = vias[vias.highway.isin(VEICULARES)]
    pb = gpd.sjoin(vveic[["geometry"]], bho_tr[["geometry"]], predicate="intersects")
    res_f["cruzamentos_via_veicular_x_bho_pares"] = len(pb)
    pd.DataFrame([res_f]).T.to_csv(saida / "F_hidrografia_travessias_resumo.csv", header=["valor"])
    res["F"] = res_f

    # G. grafos --------------------------------------------------------------
    res["G"] = estat_grafos(saida, g_mun, g_urb)

    # H. equipamentos --------------------------------------------------------
    elem = pd.concat([pts_m.assign(geo="ponto"), pol_m.assign(geo="poligono")])
    elem = gpd.GeoDataFrame(elem, geometry="geometry", crs=CRS_PADRAO)
    cats = {
        "saude_hospital": lambda t: t.get("amenity") == "hospital" or t.get("healthcare") == "hospital",
        "saude_clinic": lambda t: t.get("amenity") == "clinic" or t.get("healthcare") in ("clinic", "centre"),
        "saude_doctors": lambda t: t.get("amenity") == "doctors" or t.get("healthcare") == "doctor",
        "saude_pharmacy": lambda t: t.get("amenity") == "pharmacy" or t.get("healthcare") == "pharmacy",
        "saude_dentist": lambda t: t.get("amenity") == "dentist" or t.get("healthcare") == "dentist",
        "saude_outros_healthcare": lambda t: "healthcare" in t and t.get("healthcare") not in
        ("hospital", "clinic", "centre", "doctor", "pharmacy", "dentist"),
        "educacao_school": lambda t: t.get("amenity") == "school",
        "educacao_kindergarten": lambda t: t.get("amenity") in ("kindergarten", "childcare"),
        "educacao_superior": lambda t: t.get("amenity") in ("university", "college"),
        "seguranca_police": lambda t: t.get("amenity") == "police",
        "seguranca_fire_station": lambda t: t.get("amenity") == "fire_station",
        "emergencia_emergency": lambda t: "emergency" in t and t.get("emergency") not in ("no",),
        "social_facility": lambda t: t.get("amenity") == "social_facility",
        "community_centre": lambda t: t.get("amenity") == "community_centre",
        "shelter": lambda t: t.get("amenity") == "shelter",
        "combustivel": lambda t: t.get("amenity") == "fuel",
        "energia_power": lambda t: "power" in t,
        "transporte_bus_stop": lambda t: t.get("highway") == "bus_stop" or t.get("public_transport") in ("platform", "stop_position"),
        "transporte_bus_station": lambda t: t.get("amenity") == "bus_station" or t.get("public_transport") == "station",
        "transporte_railway": lambda t: "railway" in t,
        "templo": lambda t: t.get("amenity") == "place_of_worship",
        "agua_potavel": lambda t: t.get("amenity") in ("drinking_water", "water_point"),
    }
    linhas_h = []
    for c, f in cats.items():
        m = elem["tags_d"].map(f)
        sub = elem[m]
        linhas_h.append({"categoria": c, "n": int(m.sum()), "n_ponto": int((sub.geo == "ponto").sum()),
                         "n_poligono": int((sub.geo == "poligono").sum()),
                         "n_urbano": int(sub.geometry.representative_point().within(g_urb).sum()),
                         "pct_com_nome": pct(sub["tags_d"].map(lambda t: "name" in t).sum(), len(sub))})
    power_lin = lin_m[lin_m["power"].isin(["line", "minor_line"])]
    linhas_h.append({"categoria": "energia_linhas_transmissao_km", "n": round(power_lin.km.sum(), 1)})
    rail_lin = lin_m[lin_m["railway"].notna()]
    linhas_h.append({"categoria": "ferrovia_km", "n": round(rail_lin.km.sum(), 1)})
    pd.DataFrame(linhas_h).to_csv(saida / "H_equipamentos.csv", index=False)
    esf = gpd.read_file(REF["esf"]).to_crs(CRS_PADRAO)
    saude_basica = elem[elem["tags_d"].map(lambda t: t.get("amenity") in ("clinic", "doctors", "hospital")
                                            or t.get("healthcare") in ("clinic", "centre", "doctor", "hospital"))]
    sb_pt = saude_basica.geometry.representative_point()
    d_esf = esf.geometry.map(lambda g: sb_pt.distance(g).min() if len(sb_pt) else np.inf)
    d_osm = sb_pt.map(lambda g: esf.distance(g).min())
    nome_ubs = saude_basica["tags_d"].map(lambda t: bool(re.search(r"\b(ESF|UBS|USF|POSTO|UNIDADE BASICA|SAUDE DA FAMILIA|CAPS)\b",
                                                                   norm_txt(t.get("name", "")))))
    res["H"] = {r["categoria"]: r["n"] for r in linhas_h}
    res["H"].update(esf_ubs_cadastro=len(esf), esf_com_unidade_osm_a_150m=int((d_esf <= 150).sum()),
                    esf_sem_unidade_osm_a_150m=int((d_esf > 150).sum()),
                    osm_saude_basica_hosp_n=len(saude_basica),
                    osm_saude_nome_tipo_ubs_esf=int(nome_ubs.sum()),
                    osm_saude_nome_ubs_esf_fora_cadastro_150m=int((nome_ubs & (d_osm > 150)).sum()),
                    osm_saude_fora_cadastro_150m=int((d_osm > 150).sum()))

    # I. mudança no tempo ----------------------------------------------------
    res["I"] = mudanca(saida, g_mun)

    (saida / "resumo_diagnostico.json").write_text(json.dumps(res, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    logger.info("Diagnóstico gravado em %s", saida)
    return res


def arquivo_grafo(tipo: str):
    for nome in (f"grafo_{tipo}.graphml", f"grafo_{tipo}_geofabrik.graphml"):
        if (PASTA / nome).exists():
            return PASTA / nome
    return None


def estat_grafos(saida: Path, g_mun, g_urb) -> dict:
    import networkx as nx
    import osmnx as ox

    out, linhas = {}, []
    for tipo in ["drive", "walk", "all"]:
        gml = arquivo_grafo(tipo)
        if gml is None:
            logger.warning("Grafo %s ausente — rode a etapa 'grafo'", tipo)
            continue
        G = ox.load_graphml(gml)
        Gp = ox.project_graph(G, to_crs=CRS_PADRAO)
        Gu = ox.convert.to_undirected(Gp)
        nos, ar = ox.graph_to_gdfs(Gu)
        # mede só o que está dentro do município (o grafo foi baixado com buffer)
        dentro = ar.geometry.representative_point().within(g_mun)
        comps = sorted(nx.connected_components(Gu), key=len, reverse=True)
        principal = comps[0]
        ar = ar.assign(km=ar["length"] / 1000)
        ar_m = ar[dentro]
        uv = ar_m.index.get_level_values(0)
        no_princ = pd.Series(uv).isin(principal).values
        km_m = ar_m.km.sum()
        nos_m = nos[nos.geometry.within(g_mun)]
        urb_m = ar_m.geometry.representative_point().within(g_urb).values
        isol = ar_m[~no_princ & urb_m]
        tem = lambda c: ar_m[c].notna().values if c in ar_m else np.zeros(len(ar_m), bool)  # noqa: E731
        reg = {"rede": tipo, "origem": gml.name, "nos_total_buffer": Gu.number_of_nodes(), "arestas_total_buffer": Gu.number_of_edges(),
               "nos_municipio": len(nos_m), "arestas_municipio": len(ar_m), "km_municipio": round(km_m, 1),
               "componentes": len(comps), "maior_componente_pct_nos": pct(len(principal), Gu.number_of_nodes()),
               "maior_componente_pct_km_municipio": pct(ar_m[no_princ].km.sum(), km_m),
               "componentes_com_1_aresta": sum(1 for c in comps if len(c) == 2),
               "vias_sem_saida_nos_grau1_municipio": int((nos_m["street_count"] == 1).sum()) if "street_count" in nos_m else None,
               "pct_arestas_com_nome": pct(tem("name").sum(), len(ar_m)),
               "pct_km_com_nome": pct(ar_m.km[tem("name")].sum(), km_m),
               "pct_arestas_com_maxspeed": pct(tem("maxspeed").sum(), len(ar_m)),
               "pct_arestas_com_surface": pct(tem("surface").sum(), len(ar_m)),
               "pct_km_com_surface": pct(ar_m.km[tem("surface")].sum(), km_m),
               "urbano_arestas_fora_componente_principal": len(isol),
               "urbano_km_fora_componente_principal": round(isol.km.sum(), 2),
               "urbano_componentes_isolados": len({c_id for c_id, c in enumerate(comps[1:]) if
                                                   any(u in c for u in isol.index.get_level_values(0))}) if len(isol) else 0}
        linhas.append(reg)
        out[tipo] = reg
    pd.DataFrame(linhas).to_csv(saida / "G_grafos_estatisticas.csv", index=False)
    return out


def mudanca(saida: Path, g_mun) -> dict:
    import osmnx as ox

    antiga = gpd.read_file(REF["malha_antiga"]).to_crs(CRS_PADRAO)
    meta_ant = json.loads(REF["malha_antiga"].with_suffix(".json").read_text(encoding="utf-8"))
    gml = arquivo_grafo("drive")
    if gml is None:
        return {}
    nova = ox.graph_to_gdfs(ox.project_graph(ox.load_graphml(gml), to_crs=CRS_PADRAO), nodes=False).reset_index()
    nova = recortar(nova, g_mun)
    antiga = recortar(antiga, g_mun)
    hw = lambda v: v if isinstance(v, str) and not v.startswith("[") else "(misto)"  # noqa: E731
    for g in (antiga, nova):
        g["classe"] = g["highway"].map(hw)
        g["km"] = g.length / 1000
        g["nomeado"] = g["name"].notna()
    a = antiga.groupby("classe").agg(n_antigo=("km", "size"), km_antigo=("km", "sum"))
    n = nova.groupby("classe").agg(n_novo=("km", "size"), km_novo=("km", "sum"))
    t = a.join(n, how="outer").fillna(0)
    t["dif_km"] = t.km_novo - t.km_antigo
    t.loc["TOTAL"] = t.sum()
    t.round(2).reset_index().to_csv(saida / "I_mudanca_malha_drive.csv", index=False)
    # nota: o grafo antigo foi gravado com arestas nos dois sentidos (dirigido); o novo também (drive)
    return {"data_antiga": meta_ant.get("data_processamento"), "km_antigo": round(antiga.km.sum(), 1),
            "km_novo": round(nova.km.sum(), 1), "n_antigo": len(antiga), "n_novo": len(nova),
            "pct_km_nome_antigo": pct(antiga[antiga.nomeado].km.sum(), antiga.km.sum()),
            "pct_km_nome_novo": pct(nova[nova.nomeado].km.sum(), nova.km.sum())}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("etapa", choices=["extrair", "grafo", "diagnostico", "todos"])
    ap.add_argument("--grade", type=int, default=4, help="blocos por lado da grade de consulta (padrão 4)")
    ap.add_argument("--forcar", action="store_true", help="baixa de novo mesmo se já houver arquivo salvo")
    ap.add_argument("--saida-tabelas", type=Path, default=PASTA / "diagnostico")
    ap.add_argument("--fonte", choices=["overpass", "geofabrik"], default="overpass",
                    help="origem do extrato completo; com 'overpass', cai para o Geofabrik após 3 falhas")
    a = ap.parse_args()
    try:
        if a.etapa in ("extrair", "todos"):
            if a.fonte == "geofabrik":
                extrair_geofabrik(a.forcar)
            else:
                try:
                    extrair(a.grade, a.forcar)
                except RuntimeError as e:
                    logger.warning("%s — seguindo com o Geofabrik", e)
                    extrair_geofabrik(a.forcar)
        if a.etapa in ("grafo", "todos"):
            if a.fonte == "geofabrik":
                grafos_geofabrik(a.forcar)
            else:
                try:
                    grafos(a.forcar)
                except RuntimeError as e:
                    logger.warning("%s — montando os grafos a partir do Geofabrik", e)
                    grafos_geofabrik(a.forcar)
        if a.etapa in ("diagnostico", "todos"):
            diagnostico(a.saida_tabelas)
    except RuntimeError as e:
        logger.error("%s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
