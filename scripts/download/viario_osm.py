"""
Viário do OpenStreetMap com nomes, cruzamentos via × curso d'água e cotejo de
bairros (rodada 66).

Reaproveita o extrato e os grafos do levantamento exploratório
(scripts/exploracao/osm_levantamento.py), já em data/raw/osm_levantamento/:
extrato Geofabrik (Brasil Sul) de 2026-10-20T20:23:47Z e grafos osmnx montados
a partir dele. NÃO baixa nada: se o PBF ou o extrato não estiverem lá, para.

Etapas (subcomandos):
    vias         data/raw/vetor/viario_osm-geofabrik_2026-10-20_vetorial.gpkg
                 camadas `vias` (uma feição por via OSM, pelo id real do way,
                 recortada pelo município), `arestas_drive` e `arestas_walk`
                 (dos GraphML; nós com id sintético = coordenada).
    cruzamentos  data/processed/viario/cruzamentos-via-curso-dagua_osm_2026-10-20.gpkg
                 (um ponto por cruzamento, agregado a 20 m) e candidatas_campo.csv
                 (candidatas sem marcação × travessias T01–T33 da lista de campo).
    bairros      cotejo dos limites admin_level 10 do OSM com os bairros do
                 IBGE (Censo 2022): CSV de conferência, não adotado.
    todos        as três etapas.

Atribuição (ODbL): © contribuidores do OpenStreetMap.
Todo produto tem .json irmão. Idempotente: não refaz o que já existe sem --forcar.

Uso:
    python scripts/download/viario_osm.py todos --codigo-ibge 4322400
    python scripts/download/viario_osm.py bairros --saida-cotejo <pasta>
"""

import argparse
import json
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.ops import unary_union

RAIZ = Path(__file__).resolve().parents[2]
sys.path.append(str(RAIZ / "scripts" / "utils"))
sys.path.append(str(RAIZ / "scripts" / "exploracao"))
from recorte_municipio import CAMINHO_AREA_ESTUDO_PADRAO, CRS_PADRAO, carregar_area_estudo  # noqa: E402
from osm_levantamento import (  # noqa: E402
    PASTA, REF, VEICULARES, WATERWAYS, nome_normalizado, norm_txt, pct, recortar,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

ATRIBUICAO = "© contribuidores do OpenStreetMap (ODbL)"
EXTRATO = PASTA / "osm_completo_2026-10-21.gpkg"
PBF = PASTA / "geofabrik" / "sul-latest.osm.pbf"
SAIDA_VIAS = RAIZ / "data/raw/vetor/viario_osm-geofabrik_2026-10-20_vetorial.gpkg"
PASTA_PROC = RAIZ / "data/processed/viario"
SAIDA_CRUZ = PASTA_PROC / "cruzamentos-via-curso-dagua_osm_2026-10-20.gpkg"
SAIDA_CAND = PASTA_PROC / "candidatas_campo.csv"

ATRIB_VIA = ["highway", "name", "alt_name", "official_name", "ref", "surface", "smoothness", "tracktype", "maxspeed",
             "lanes", "oneway", "bridge", "tunnel", "layer", "junction", "access", "foot", "bicycle", "width",
             "incline", "lit"]
PEDESTRES = {"footway", "path", "pedestrian", "steps", "cycleway", "bridleway", "corridor"}
TIPO_BONITO = {"RUA": "Rua", "AVENIDA": "Avenida", "TRAVESSA": "Travessa", "ESTRADA": "Estrada", "RODOVIA": "Rodovia",
               "PRACA": "Praça", "ALAMEDA": "Alameda", "BECO": "Beco", "PASSAGEM": "Passagem", "VIA": "Via",
               "ACESSO": "Acesso", "LARGO": "Largo", "VIELA": "Viela", "CAMINHO": "Caminho"}
# números da rodada 65 (diagnóstico), para conferência dentro de 0,5%
R65 = {"km_vias": 2454.6, "km_urbano": 423.6, "pct_nome_urbano": 75.7, "km_rural": 2031.0, "pct_nome_rural": 7.7,
       "drive_nos": 2832, "drive_arestas": 4145, "drive_pct_principal": 95.2, "cruzamentos": 906, "candidatas": 103}


def meta_extrato() -> dict:
    return json.loads(EXTRATO.with_suffix(".json").read_text(encoding="utf-8"))


def sidecar(caminho: Path, **campos) -> None:
    m = meta_extrato()
    meta = {"arquivo": caminho.name, "fonte": "OpenStreetMap — extrato regional do Geofabrik (Brasil, Região Sul)",
            "atribuicao": ATRIBUICAO, "licenca": "ODbL 1.0", "url": "https://download.geofabrik.de/south-america/brazil/sul.html",
            "timestamp_extrato": m.get("timestamp_extrato"), "data_acesso": "2026-10-21",
            "data_processamento": datetime.now(timezone.utc).isoformat(), "crs": CRS_PADRAO,
            "script": "scripts/download/viario_osm.py"}
    meta.update(campos)
    if caminho.exists():
        meta["tamanho_mb"] = round(caminho.stat().st_size / 1e6, 2)
    caminho.with_suffix(".json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def conferir_entradas(codigo_ibge: str) -> None:
    for p in (PBF, EXTRATO):
        if not p.exists():
            raise FileNotFoundError(f"{p.relative_to(RAIZ)} ausente — esta rodada não baixa dados; "
                                    "rode antes scripts/exploracao/osm_levantamento.py extrair --fonte geofabrik")
    cod = gpd.read_file(CAMINHO_AREA_ESTUDO_PADRAO).get("codarea")
    if cod is not None and str(cod.iloc[0]) != str(codigo_ibge):
        raise ValueError(f"área de estudo é do código {cod.iloc[0]}, não {codigo_ibge}: gere-a com vetor_ibge.py")


def geometrias_base():
    mun = carregar_area_estudo()
    g_mun = mun.union_all()
    urb = gpd.read_file(REF["urbano"]).to_crs(CRS_PADRAO)  # áreas urbanizadas IBGE 2019
    g_urb = urb.union_all().intersection(g_mun)
    return g_mun, g_urb


def tipo_logradouro(nome) -> str | None:
    if not isinstance(nome, str) or not nome.strip():
        return None
    primeiro = nome_normalizado(nome).split()[0]
    return TIPO_BONITO.get(primeiro, "outro")


# ---------------------------------------------------------------------------
# PARTE 1 — vias e arestas
# ---------------------------------------------------------------------------
def gerar_vias(forcar: bool) -> dict:
    if SAIDA_VIAS.exists() and not forcar:
        logger.info("%s já existe — use --forcar", SAIDA_VIAS.name)
        return json.loads(SAIDA_VIAS.with_suffix(".json").read_text(encoding="utf-8")).get("conferencia_rodada_65", {})
    g_mun, g_urb = geometrias_base()
    lin = gpd.read_file(EXTRATO, layer="linhas")
    lin = lin[lin["highway"].notna()].copy()
    tags = lin["tags"].map(json.loads)
    # recorte só pelo polígono do município (o extrato tem as vias inteiras que tocam o buffer de 2 km)
    lin = lin[lin.intersects(g_mun)].copy()
    tags = tags.loc[lin.index]
    lin["geometry"] = lin.geometry.intersection(g_mun)
    lin = lin[~lin.geometry.is_empty]
    tags = tags.loc[lin.index]

    vias = gpd.GeoDataFrame({"osm_id": lin["osm_id"].astype("int64")}, geometry=lin.geometry.values, crs=CRS_PADRAO)
    for a in ATRIB_VIA:
        vias[a] = tags.map(lambda t, a=a: t.get(a)).values
    vias["comprimento_m"] = vias.length.round(1)
    # zona pela maior parte do comprimento; os comprimentos por zona ficam à parte (a via pode cruzar a borda)
    vias["comprimento_urbano_m"] = vias.geometry.intersection(g_urb).length.round(1)
    vias["comprimento_rural_m"] = (vias["comprimento_m"] - vias["comprimento_urbano_m"]).clip(lower=0).round(1)
    vias["zona"] = np.where(vias["comprimento_urbano_m"] >= vias["comprimento_rural_m"], "urbano", "rural")
    vias["grupo"] = np.select([vias["highway"].isin(VEICULARES), vias["highway"].isin(PEDESTRES)],
                              ["veicular", "pedestre"], "outro")
    vias["nome_norm"] = vias["name"].map(lambda n: nome_normalizado(n) if isinstance(n, str) else None)
    vias["tipo_logradouro"] = vias["name"].map(tipo_logradouro)
    vias["tags"] = tags.map(lambda t: json.dumps(t, ensure_ascii=False)).values
    vias = vias[["osm_id", *ATRIB_VIA, "comprimento_m", "comprimento_urbano_m", "comprimento_rural_m", "zona", "grupo",
                 "nome_norm", "tipo_logradouro", "tags", "geometry"]]

    ad, cd = arestas_grafo("drive", g_mun)
    aw, cw = arestas_grafo("walk", g_mun)

    SAIDA_VIAS.parent.mkdir(parents=True, exist_ok=True)
    tmp = SAIDA_VIAS.with_suffix(".tmp.gpkg")
    tmp.unlink(missing_ok=True)
    vias.to_file(tmp, layer="vias", driver="GPKG")
    ad.to_file(tmp, layer="arestas_drive", driver="GPKG")
    aw.to_file(tmp, layer="arestas_walk", driver="GPKG")
    tmp.replace(SAIDA_VIAS)

    # conferência com a rodada 65
    km = vias["comprimento_m"].sum() / 1000
    ku, kr = vias["comprimento_urbano_m"].sum() / 1000, vias["comprimento_rural_m"].sum() / 1000
    nomeada = vias["name"].notna()
    conf = {"km_vias": round(km, 1), "km_urbano": round(ku, 1), "km_rural": round(kr, 1),
            "pct_nome_urbano": pct(vias.loc[nomeada, "comprimento_urbano_m"].sum(), vias["comprimento_urbano_m"].sum()),
            "pct_nome_rural": pct(vias.loc[nomeada, "comprimento_rural_m"].sum(), vias["comprimento_rural_m"].sum()),
            "drive_nos": cd["nos_municipio"], "drive_arestas": cd["arestas_municipio"],
            "drive_pct_principal": cd["pct_nos_componente_principal"],
            "walk_nos": cw["nos_municipio"], "walk_arestas": cw["arestas_municipio"]}
    dif = {k: round(100 * (conf[k] - R65[k]) / R65[k], 2) for k in R65 if k in conf}
    conf_tudo = {"valores": conf, "rodada_65": {k: v for k, v in R65.items() if k in conf}, "diferenca_pct": dif,
                 "dentro_de_0_5pct": all(abs(v) <= 0.5 for v in dif.values())}
    sidecar(SAIDA_VIAS,
            camadas={"vias": len(vias), "arestas_drive": len(ad), "arestas_walk": len(aw)},
            escopo="município (config/area_estudo.geojson); vias para veículos e pedestres (todas as vias com highway "
                   "no extrato, inclusive 'proposed' e 'raceway', marcadas como grupo 'outro')",
            transformacao_aplicada="vias: linhas com highway do extrato (osm_completo_2026-10-21.gpkg, GDAL/OSM), uma "
                                   "feição por way OSM (id real), recortadas só pelo polígono do município; zona pela "
                                   "maior parte do comprimento dentro/fora das áreas urbanizadas IBGE 2019, com "
                                   "comprimento_urbano_m e comprimento_rural_m; nome_norm = sem acento e caixa, "
                                   "abreviações expandidas; tipo_logradouro pela primeira palavra do nome normalizado. "
                                   "arestas_drive/arestas_walk: grafos osmnx (simplificados, todas as componentes) "
                                   "em forma não dirigida, uma aresta por trecho entre interseções, reprojetados.",
            nos_sinteticos="os grafos foram montados a partir do PBF lido pelo GDAL, que não traz o id dos nós; cada nó "
                           "recebeu um id sintético por coordenada (7 casas decimais, a precisão do OSM). u e v NÃO "
                           "são ids de nós do OSM; osmid das arestas é o id real do way (lista se o trecho funde ways)",
            colunas_arestas={"u, v, key": "nós (id sintético) e chave do multigrafo", "osmid": "id(s) do way OSM",
                             "nome": "name", "classe": "highway", "comprimento_m": "length do osmnx",
                             "sentido": "'mão única' (oneway, no sentido u→v) ou 'mão dupla'",
                             "dentro_municipio": "ponto representativo dentro do município",
                             "componente": "0 = componente principal; demais numerados por tamanho"},
            conferencia_rodada_65=conf_tudo)
    logger.info("Vias: %d feições; conferência: %s", len(vias), json.dumps(conf_tudo, ensure_ascii=False))
    return conf_tudo


def arestas_grafo(tipo: str, g_mun) -> tuple[gpd.GeoDataFrame, dict]:
    import networkx as nx
    import osmnx as ox

    gml = PASTA / f"grafo_{tipo}_geofabrik.graphml"
    if not gml.exists():
        raise FileNotFoundError(f"{gml.relative_to(RAIZ)} ausente — rode osm_levantamento.py grafo --fonte geofabrik")
    G = ox.project_graph(ox.load_graphml(gml), to_crs=CRS_PADRAO)
    Gu = ox.convert.to_undirected(G)
    comps = sorted(nx.connected_components(Gu), key=len, reverse=True)
    comp_de = {n: i for i, c in enumerate(comps) for n in c}
    nos, ar = ox.graph_to_gdfs(Gu)
    ar = ar.reset_index()
    lista = lambda v: ";".join(map(str, v)) if isinstance(v, list) else (None if v is None or v != v else str(v))  # noqa: E731
    out = gpd.GeoDataFrame({
        "u": ar["u"].astype("int64"), "v": ar["v"].astype("int64"), "key": ar["key"].astype("int64"),
        "osmid": ar["osmid"].map(lista), "nome": ar.get("name", pd.Series(index=ar.index)).map(lista),
        "classe": ar["highway"].map(lista), "comprimento_m": ar["length"].round(1),
        "sentido": np.where(ar["oneway"].astype(str).str.lower().isin(["true", "1"]), "mão única", "mão dupla"),
        "dentro_municipio": ar.geometry.representative_point().within(g_mun).values,
        "componente": ar["u"].map(comp_de).astype("int64"),
    }, geometry=ar.geometry.values, crs=CRS_PADRAO)
    nos_m = nos[nos.geometry.within(g_mun)]
    estat = {"nos_municipio": len(nos_m), "arestas_municipio": int(out["dentro_municipio"].sum()),
             "pct_nos_componente_principal": pct(len(comps[0]), Gu.number_of_nodes()), "componentes": len(comps)}
    return out, estat


# ---------------------------------------------------------------------------
# PARTE 2 — cruzamentos via × curso d'água
# ---------------------------------------------------------------------------
def nao(v) -> bool:
    return v in (None, "no")


def gerar_cruzamentos(forcar: bool) -> dict:
    if SAIDA_CRUZ.exists() and SAIDA_CAND.exists() and not forcar:
        logger.info("%s já existe — use --forcar", SAIDA_CRUZ.name)
        return json.loads(SAIDA_CRUZ.with_suffix(".json").read_text(encoding="utf-8")).get("contagens", {})
    if not SAIDA_VIAS.exists():
        raise FileNotFoundError("rode antes a etapa 'vias'")
    g_mun, g_urb = geometrias_base()
    vias = gpd.read_file(SAIDA_VIAS, layer="vias")
    vias["t"] = vias["tags"].map(json.loads)
    vv = vias[vias["highway"].isin(VEICULARES | {"path", "footway", "cycleway", "pedestrian", "bridleway"})].reset_index(drop=True)
    lin = gpd.read_file(EXTRATO, layer="linhas")
    ww = recortar(lin[lin["waterway"].isin(WATERWAYS)], g_mun).reset_index(drop=True)
    ww["t"] = ww["tags"].map(json.loads)
    pts = gpd.read_file(EXTRATO, layer="pontos")
    pts = pts[pts.within(g_mun)]
    fords = pts[pts["tags"].map(lambda s: not nao(json.loads(s).get("ford")))].geometry
    fords_u = fords.buffer(15).union_all() if len(fords) else None  # vau como nó isolado perto do cruzamento

    pares = gpd.sjoin(vv[["osm_id", "name", "highway", "t", "geometry"]], ww[["osm_id", "name", "waterway", "t", "geometry"]],
                      predicate="intersects", lsuffix="via", rsuffix="ww")
    regs = []
    for _, p in pares.iterrows():
        inter = p.geometry.intersection(ww.geometry.iloc[p["index_ww"]])
        for pt in getattr(inter, "geoms", [inter]):
            if pt.geom_type != "Point":
                pt = pt.representative_point()
            tv, tw = p["t_via"], p["t_ww"]
            perto_vau = fords_u is not None and pt.within(fords_u)
            if not nao(tv.get("bridge")):
                tipo = "ponte"
            elif tw.get("tunnel") in ("culvert", "yes") or not nao(tv.get("tunnel")):
                tipo = "bueiro/culvert"
            elif not nao(tv.get("ford")) or perto_vau:
                tipo = "vau"
            else:
                tipo = "sem marcação"
            regs.append({"tipo": tipo, "via_classe": p["highway"].replace("_link", ""), "via_nome": p["name_via"],
                         "via_osm_id": int(p["osm_id_via"]), "curso_tipo": p["waterway"], "curso_nome": p["name_ww"],
                         "curso_osm_id": int(p["osm_id_ww"]), "geometry": pt})
    cr = gpd.GeoDataFrame(regs, crs=CRS_PADRAO)
    # agrega pontos a menos de 20 m (mesma travessia em ways vizinhos ou pista dupla); prevalece a marcação
    grupos = gpd.GeoDataFrame(geometry=list(unary_union(cr.geometry.buffer(10)).geoms), crs=CRS_PADRAO)
    cr["grupo"] = gpd.sjoin(cr[["geometry"]], grupos, predicate="within")["index_right"].groupby(level=0).first()
    ordem = {"ponte": 0, "bueiro/culvert": 1, "vau": 2, "sem marcação": 3}
    cr["_o"] = cr["tipo"].map(ordem)
    cr["n_pontos_agregados"] = cr.groupby("grupo")["tipo"].transform("size")
    agg = gpd.GeoDataFrame(cr.sort_values("_o").groupby("grupo").first(), geometry="geometry", crs=CRS_PADRAO)
    agg = agg.drop(columns="_o").reset_index(drop=True)
    agg["zona"] = np.where(agg.within(g_urb), "urbano", "rural")
    ll = agg.to_crs("EPSG:4326")
    agg["latitude"], agg["longitude"] = ll.geometry.y.round(6), ll.geometry.x.round(6)
    # id das candidatas: oeste → leste (estável entre execuções)
    sem = agg[agg["tipo"] == "sem marcação"].sort_values("longitude").index
    agg["id_candidata"] = None
    agg.loc[sem, "id_candidata"] = [f"C{i + 1:03d}" for i in range(len(sem))]
    agg = agg[["id_candidata", "tipo", "via_classe", "via_nome", "via_osm_id", "curso_tipo", "curso_nome", "curso_osm_id",
               "zona", "n_pontos_agregados", "latitude", "longitude", "geometry"]]

    PASTA_PROC.mkdir(parents=True, exist_ok=True)
    SAIDA_CRUZ.unlink(missing_ok=True)
    agg.to_file(SAIDA_CRUZ, layer="cruzamentos", driver="GPKG")

    # candidatas × lista de campo (T01–T33); a lista de campo não é alterada
    campo = gpd.read_file(REF["campo"]).to_crs(CRS_PADRAO)
    trav = campo[campo["codigo"].str.startswith("T")].sort_values("codigo")
    cand = agg[agg["tipo"] == "sem marcação"].sort_values("id_candidata")
    tab = cand[["id_candidata", "via_classe", "via_nome", "curso_tipo", "curso_nome", "zona", "latitude", "longitude"]].copy()
    dist = np.column_stack([cand.distance(g).round(0).values for g in trav.geometry])
    for j, cod in enumerate(trav["codigo"]):
        tab[f"dist_{cod}_m"] = dist[:, j].astype(int)
    tab.insert(8, "travessia_mais_proxima", trav["codigo"].values[dist.argmin(axis=1)])
    tab.insert(9, "dist_mais_proxima_m", dist.min(axis=1).astype(int))
    tab.to_csv(SAIDA_CAND, index=False, encoding="utf-8")

    cont = {"cruzamentos": len(agg), "pontos_brutos": len(cr), "por_tipo": agg["tipo"].value_counts().to_dict(),
            "por_zona": agg["zona"].value_counts().to_dict(), "candidatas": len(cand),
            "candidatas_a_ate_500m_de_alguma_T": int((tab["dist_mais_proxima_m"] <= 500).sum()),
            "dist_mediana_candidata_T_mais_proxima_m": int(tab["dist_mais_proxima_m"].median()) if len(tab) else None}
    sidecar(SAIDA_CRUZ, produto="derivado", contagens=cont,
            entradas=["data/raw/vetor/viario_osm-geofabrik_2026-10-20_vetorial.gpkg (vias)",
                      "data/raw/osm_levantamento/osm_completo_2026-10-21.gpkg (linhas waterway, pontos ford)"],
            transformacao_aplicada="interseção das vias (veiculares, inclusive track/service, e de pedestres) com os "
                                   "cursos d'água OSM (river, stream, canal, drain, ditch) recortados pelo município; tipo: "
                                   "ponte (bridge na via), bueiro/culvert (tunnel=culvert no curso ou tunnel na via), vau "
                                   "(ford na via ou nó ford a até 15 m), sem marcação; pontos a menos de 20 m agregados "
                                   "(prevalece a marcação mais forte); zona pelo ponto nas áreas urbanizadas IBGE 2019; "
                                   "id_candidata C001… de oeste para leste só para os sem marcação",
            observacao="a lista de campo atual (P01–P05, T01–T33, docs/acessibilidade_inundacao/campo/) não foi alterada; "
                       "a comparação está em candidatas_campo.csv")
    sidecar(SAIDA_CAND, produto="derivado (tabela de conferência)",
            colunas="id_candidata e atributos da candidata; travessia_mais_proxima e dist_mais_proxima_m; dist_T01_m … "
                    "dist_T33_m = distância em linha reta (m, EPSG:31981) a cada travessia da lista de campo",
            dado_pessoal="nenhum (nomes de via e de curso d'água são topônimos públicos)")
    logger.info("Cruzamentos: %s", json.dumps(cont, ensure_ascii=False))
    return cont


# ---------------------------------------------------------------------------
# PARTE 3 — cotejo de bairros (conferência)
# ---------------------------------------------------------------------------
def cotejar_bairros(saida: Path) -> dict:
    g_mun, _ = geometrias_base()
    osm = gpd.read_file(EXTRATO, layer="relacoes_limite")
    osm = osm[(osm["admin_level"] == "10") & osm.intersects(g_mun.buffer(-500))].copy()
    ibge = gpd.read_file(REF["bairros"]).to_crs(CRS_PADRAO)
    osm["chave"] = osm["name"].map(norm_txt)
    ibge["chave"] = ibge["NM_BAIRRO"].map(norm_txt)
    linhas = []
    usados = set()
    for _, b in ibge.iterrows():
        cand = osm[osm["chave"] == b["chave"]]
        criterio = "nome normalizado" if len(cand) else None
        if len(cand) and (osm.loc[cand.index, "name"] == b["NM_BAIRRO"]).any():
            criterio = "nome exato"
        if not len(cand):  # sem nome igual: maior sobreposição
            ov = osm.geometry.intersection(b.geometry).area
            cand = osm.loc[[ov.idxmax()]] if ov.max() > 0 else cand
            criterio = "maior sobreposição" if len(cand) else "sem par"
        reg = {"bairro_ibge": b["NM_BAIRRO"], "cd_bairro_ibge": b["CD_BAIRRO"], "criterio_par": criterio,
               "area_ibge_km2": round(b.geometry.area / 1e6, 3)}
        if len(cand):
            o = cand.iloc[0]
            usados.add(o["osm_id"])
            inter = o.geometry.intersection(b.geometry).area
            uni = o.geometry.union(b.geometry).area
            fronteira_o, fronteira_b = o.geometry.boundary, b.geometry.boundary
            reg.update(bairro_osm=o["name"], osm_relation_id=int(o["osm_id"]),
                       nome_exato=o["name"] == b["NM_BAIRRO"], nome_normalizado_igual=o["chave"] == b["chave"],
                       area_osm_km2=round(o.geometry.area / 1e6, 3), area_intersecao_km2=round(inter / 1e6, 3),
                       iou=round(inter / uni, 3) if uni else None,
                       area_so_ibge_km2=round((b.geometry.area - inter) / 1e6, 3),
                       area_so_osm_km2=round((o.geometry.area - inter) / 1e6, 3),
                       divisa_dist_max_m=round(fronteira_o.hausdorff_distance(fronteira_b), 0),
                       divisa_pct_ibge_a_ate_20m_da_osm=pct(fronteira_b.intersection(fronteira_o.buffer(20)).length,
                                                            fronteira_b.length))
        linhas.append(reg)
    tab = pd.DataFrame(linhas).sort_values("iou")
    sobra = osm[~osm["osm_id"].isin(usados)]
    saida.mkdir(parents=True, exist_ok=True)
    arq = saida / "cotejo_bairros_osm-admin10_x_ibge-2022.csv"
    tab.to_csv(arq, index=False, encoding="utf-8")
    sidecar(arq, produto="conferência (não adotado)",
            entradas=["OSM relacoes_limite admin_level=10 (extrato Geofabrik 2026-10-20)",
                      "data/processed/bairros/bairros_ibge_2022_vetorial.gpkg (IBGE, Censo 2022)"],
            transformacao_aplicada="par por nome (exato; normalizado sem acento/caixa; senão maior sobreposição); áreas "
                                   "em EPSG:31981; IoU = interseção ÷ união; divisa_dist_max_m = distância de Hausdorff "
                                   "entre os contornos; divisa_pct = % do contorno IBGE a até 20 m do contorno OSM",
            osm_sem_par=sobra["name"].tolist())
    res = {"n_osm": len(osm), "n_ibge": len(ibge), "pares": int(tab["iou"].notna().sum()),
           "iou_ge_0_9": int((tab["iou"] >= 0.9).sum()), "iou_mediana": round(float(tab["iou"].median()), 3),
           "nome_exato": int(tab["nome_exato"].fillna(False).sum()),
           "nome_normalizado": int(tab["nome_normalizado_igual"].fillna(False).sum()),
           "mais_divergentes": tab.head(3)[["bairro_ibge", "bairro_osm", "iou", "divisa_dist_max_m"]].to_dict("records"),
           "osm_sem_par": sobra["name"].tolist(), "arquivo": arq.name}
    logger.info("Cotejo de bairros: %s", json.dumps(res, ensure_ascii=False))
    return res


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("etapa", choices=["vias", "cruzamentos", "bairros", "todos"])
    ap.add_argument("--codigo-ibge", default="4322400", help="código IBGE do município da área de estudo")
    ap.add_argument("--forcar", action="store_true", help="refaz produtos já existentes")
    ap.add_argument("--saida-cotejo", type=Path, default=PASTA_PROC / "conferencia",
                    help="pasta do CSV de cotejo dos bairros (fora do git)")
    a = ap.parse_args()
    try:
        conferir_entradas(a.codigo_ibge)
        if a.etapa in ("vias", "todos"):
            gerar_vias(a.forcar)
        if a.etapa in ("cruzamentos", "todos"):
            gerar_cruzamentos(a.forcar)
        if a.etapa in ("bairros", "todos"):
            cotejar_bairros(a.saida_cotejo)
    except (FileNotFoundError, ValueError) as e:
        logger.error("%s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
