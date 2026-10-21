"""
Fontes complementares ao OpenStreetMap: nomes oficiais (CNEFE 2022), faces de
logradouros do IBGE, partes novas da cidade pelos endereços, Overture Maps e
nomes do Plano Diretor para vias sem nome (rodada 67). Levantamento e
diagnóstico; nada é adotado no acervo nem no portal.

Etapas (subcomandos):
    overture   baixa do Overture Maps (versão estável mais recente) as camadas
               buildings, transportation/segment, places, divisions/division e
               divisions/division_area no retângulo da área de estudo + 2 km.
               Usa duckdb numa venv FORA do repositório (--python-overture ou a
               variável OVERTURE_PYTHON). Para se uma camada passar de 500 MB.
    faces      faces de logradouros do Censo 2022: registra o que existe no FTP
               do IBGE; com --faces-zip <arquivo>, descreve e compara com o OSM.
    nomes      parte 1 (CNEFE × OSM) e parte 5 (rótulos do Plano × vias sem nome).
    partes     parte 3 (endereços fora das quadras do Plano; grade de 250 m).
    analise    parte 4 (Overture × OSM × CNEFE × ESF/UBS × bairros IBGE).
    todos      faces, nomes, partes, overture e analise.

Privacidade (CNEFE): só agregados em qualquer saída — nomes de logradouro e
contagens. Nenhum ponto, coordenada, número ou lista de endereços; células de
grade (e setores) com menos de 5 endereços são suprimidas. Places do Overture
podem ter telefone/e-mail: ficam só no bruto (data/raw, fora do git) e só
entram em contagens.

Uso:
    python scripts/exploracao/fontes_complementares.py todos --saida-tabelas <pasta>
"""

import argparse
import json
import logging
import os
import re
import subprocess
import sys
import zipfile
from collections import Counter
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import requests
import shapely
from shapely.geometry import box
from shapely.ops import polygonize, unary_union

RAIZ = Path(__file__).resolve().parents[2]
sys.path.append(str(RAIZ / "scripts" / "utils"))
sys.path.append(str(RAIZ / "scripts" / "exploracao"))
from recorte_municipio import CAMINHO_AREA_ESTUDO_PADRAO, CRS_PADRAO, carregar_area_estudo  # noqa: E402
from osm_levantamento import (  # noqa: E402
    REF, VEICULARES, compat_iniciais, nome_normalizado, nome_nucleo, norm_txt, pct, recortar,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

PASTA = RAIZ / "data" / "raw" / "fontes_complementares"
VIAS = RAIZ / "data/raw/vetor/viario_osm-geofabrik_2026-10-20_vetorial.gpkg"
EXTRATO_OSM = RAIZ / "data/raw/osm_levantamento/osm_completo_2026-10-21.gpkg"
CNEFE_ZIP = RAIZ / "data/raw/cache_dinamica_populacional/cnefe_2022/4322400_URUGUAIANA.zip"
CNEFE_DOM = RAIZ / "data/processed/dinamica_populacional/enderecos-domicilios_ibge-cnefe_2022_pontos.gpkg"
FACES_URL = ("https://geoftp.ibge.gov.br/recortes_para_fins_estatisticos/malha_de_setores_censitarios/censo_2022/"
             "base_de_faces_de_logradouros_versao_2022_censo_demografico/")
OVERTURE_S3 = "s3://overturemaps-us-west-2/release"
OVERTURE_STAC = "https://stac.overturemaps.org/catalog.json"
CAMADAS_OVERTURE = {  # nome local: (tema, tipo, licença declarada pelo Overture)
    "buildings": ("buildings", "building", "ODbL 1.0 (fontes OSM, Microsoft, Google, Esri etc. — ver campo sources)"),
    "segment": ("transportation", "segment", "ODbL 1.0"),
    "places": ("places", "place", "CDLA Permissive 2.0 (places)"),
    "division": ("divisions", "division", "ODbL 1.0"),
    "division_area": ("divisions", "division_area", "ODbL 1.0"),
}
LIMITE_MB = 500
MIN_CELULA = 5  # supressão de células/setores com menos de 5 endereços
UA = "ClimaPampa-PET-Saude-levantamento/1.0 (projeto academico; uso nao comercial)"


# ---------------------------------------------------------------------------
# utilidades
# ---------------------------------------------------------------------------
def sidecar(caminho: Path, **campos) -> None:
    meta = {"arquivo": caminho.name, "data_processamento": datetime.now(timezone.utc).isoformat(),
            "script": "scripts/exploracao/fontes_complementares.py", "uso": "levantamento (rodada 67); não adotado"}
    meta.update(campos)
    if caminho.exists() and caminho.is_file():
        meta["tamanho_mb"] = round(caminho.stat().st_size / 1e6, 2)
    destino = caminho.with_name(caminho.name + ".json") if caminho.suffix in (".parquet", ".csv") else caminho.with_suffix(".json")
    destino.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def base():
    mun = carregar_area_estudo()
    g_mun = mun.union_all()
    g_urb = gpd.read_file(REF["urbano"]).to_crs(CRS_PADRAO).union_all().intersection(g_mun)
    bai = gpd.read_file(REF["bairros"]).to_crs(CRS_PADRAO)
    return mun, g_mun, g_urb, bai


def suprimir(n):
    """Contagem de endereços: 1 a 4 vira None (supressão); 0 fica 0."""
    return None if 0 < n < MIN_CELULA else int(n)


def carregar_vias() -> gpd.GeoDataFrame:
    v = gpd.read_file(VIAS, layer="vias")
    return v


def nome_logradouro(r) -> str:
    return " ".join(str(x) for x in (r["NOM_TIPO_SEGLOGR"], r["NOM_TITULO_SEGLOGR"], r["NOM_SEGLOGR"])
                    if isinstance(x, str) and x.strip())


def carregar_cnefe(g_urb, bai) -> gpd.GeoDataFrame:
    """CNEFE do município, todas as espécies; pontos só para junções internas (nunca vão para saída)."""
    z = zipfile.ZipFile(CNEFE_ZIP)
    cols = ["COD_SETOR", "DSC_LOCALIDADE", "NOM_TIPO_SEGLOGR", "NOM_TITULO_SEGLOGR", "NOM_SEGLOGR", "LATITUDE",
            "LONGITUDE", "COD_ESPECIE"]
    d = pd.read_csv(z.open(z.namelist()[0]), sep=";", dtype=str, usecols=cols, encoding="utf-8")
    d["logradouro"] = d.apply(nome_logradouro, axis=1)
    # coordenadas do CNEFE em SIRGAS 2000 geográfico (EPSG:4674) → UTM 21S
    g = gpd.GeoDataFrame(d, geometry=gpd.points_from_xy(d["LONGITUDE"].astype(float), d["LATITUDE"].astype(float)),
                         crs="EPSG:4674").to_crs(CRS_PADRAO).drop(columns=["LATITUDE", "LONGITUDE"])
    g["zona"] = np.where(g.within(g_urb), "urbano", "rural")
    j = gpd.sjoin(g, bai[["NM_BAIRRO", "geometry"]], predicate="within", how="left")
    g["bairro"] = j["NM_BAIRRO"].groupby(level=0).first().fillna("(fora dos bairros)")
    return g


# ---------------------------------------------------------------------------
# PARTE 1 — CNEFE × OSM; PARTE 5 — Plano × vias sem nome
# ---------------------------------------------------------------------------
def casar_nome(nome: str, osm: dict) -> tuple[str, str | None]:
    """Nível de correspondência de um nome com o conjunto de nomes OSM."""
    up = nome.upper().strip()
    if up in osm["upper"]:
        return "exato", osm["upper"][up]
    nn = nome_normalizado(nome)
    if nn in osm["norm"]:
        return "normalizado", osm["norm"][nn]
    nu = nome_nucleo(nome)
    if nu and nu in osm["ref"]:  # estradas e rodovias pelo código (ESTRADA UR 204 × ref UR-204)
        return "codigo_ref", osm["ref"][nu]
    if nu and nu in osm["nucleo"]:
        return "aproximado", osm["nucleo"][nu]
    if nu:
        melhor, nome_m = 0.0, None
        for k, v in osm["nucleo"].items():
            if compat_iniciais(nu, k):
                return "aproximado", v
            r = SequenceMatcher(None, nu, k).ratio()
            if r > melhor:
                melhor, nome_m = r, v
        if melhor >= 0.88:
            return "aproximado", nome_m
    return "nenhum", None


def indice_osm(vias: gpd.GeoDataFrame) -> dict:
    nomes = set(vias["name"].dropna())
    for c in ("alt_name", "official_name"):
        nomes |= set(vias[c].dropna())
    refs = {r.strip() for v in vias["ref"].dropna() for r in str(v).split(";") if r.strip()}
    return {"upper": {n.upper(): n for n in nomes}, "norm": {nome_normalizado(n): n for n in nomes},
            "nucleo": {nome_nucleo(n): n for n in nomes if nome_nucleo(n)},
            "ref": {norm_txt(r): r for r in refs}}


def parte_nomes(saida: Path) -> dict:
    _, g_mun, g_urb, bai = base()
    vias = carregar_vias()
    cn = carregar_cnefe(g_urb, bai)
    osm = indice_osm(vias)
    res = {}

    # 1. lista de logradouros distintos (tipo + título + nome)
    cn_l = cn[cn["logradouro"] != ""]
    grp = cn_l.groupby("logradouro")
    tab = pd.DataFrame({
        "n_enderecos": grp.size(),
        "n_domicilios_particulares": grp["COD_ESPECIE"].apply(lambda s: int((s == "1").sum())),
        "n_bairros": grp["bairro"].nunique(),
        "bairros_principais": grp["bairro"].apply(lambda s: "; ".join(s.value_counts().head(3).index)),
        "pct_urbano": grp["zona"].apply(lambda s: round(100 * (s == "urbano").mean(), 1)),
    }).reset_index()
    tab["tipo"] = tab["logradouro"].map(lambda n: cn_l.loc[cn_l["logradouro"] == n, "NOM_TIPO_SEGLOGR"].iloc[0])
    tab["nome_norm"] = tab["logradouro"].map(nome_normalizado)
    casados = tab["logradouro"].map(lambda n: casar_nome(n, osm))
    tab["correspondencia"] = casados.map(lambda x: x[0])
    tab["nome_osm"] = casados.map(lambda x: x[1])
    # o nome casado está perto dos endereços? (evita homônimos como "Rua Um" em outro bairro)
    vias_nome = vias[vias["name"].notna() | vias["alt_name"].notna()]
    dist = []
    for _, r in tab.iterrows():
        if r["nome_osm"] is None:
            dist.append(None)
            continue
        alvo = vias_nome[(vias_nome["name"] == r["nome_osm"]) | (vias_nome["alt_name"] == r["nome_osm"]) |
                         (vias_nome["official_name"] == r["nome_osm"])].union_all()
        pts = cn_l.loc[cn_l["logradouro"] == r["logradouro"]].geometry
        dist.append(round(float(pts.distance(alvo).median()), 0))
    tab["dist_mediana_enderecos_via_osm_m"] = dist
    # nível espacial: a via OSM nomeada mais próxima da maioria dos endereços (até 50 m) partilha uma palavra
    # significativa do núcleo do nome (ex.: RUA MARECHAL FLORIANO PEIXOTO × Rua Floriano Peixoto)
    viz = gpd.sjoin_nearest(cn_l[["logradouro", "geometry"]], vias_nome[["name", "geometry"]], how="inner",
                            max_distance=50)
    viz = viz[~viz.index.duplicated()]
    maioria = viz.groupby("logradouro")["name"].agg(lambda s: s.value_counts().index[0])
    genericas = {"RUA", "BECO", "SEM", "DENOMINACAO", "SETOR", "PROJETADA", "ACESSO", "VILA", "ESTRADA"}
    for i, r in tab[tab["correspondencia"] == "nenhum"].iterrows():
        nm = maioria.get(r["logradouro"])
        if nm is None:
            continue
        ta = {t for t in nome_nucleo(r["logradouro"]).split() if len(t) >= 4 and t not in genericas}
        tb = {t for t in nome_nucleo(nm).split() if len(t) >= 4 and t not in genericas}
        if ta & tb:
            tab.loc[i, ["correspondencia", "nome_osm"]] = ["aproximado_espacial", nm]
            pts = cn_l.loc[cn_l["logradouro"] == r["logradouro"]].geometry
            alvo = vias_nome[vias_nome["name"] == nm].union_all()
            tab.loc[i, "dist_mediana_enderecos_via_osm_m"] = round(float(pts.distance(alvo).median()), 0)
    tab["casado_e_perto_300m"] = tab["correspondencia"].ne("nenhum") & (pd.to_numeric(tab["dist_mediana_enderecos_via_osm_m"]) <= 300)
    tab = tab.sort_values("n_enderecos", ascending=False)
    # nomes provisórios do recenseamento (SEM DENOMINACAO, RUA H SETOR 0500..., PROJETADA): não são nome oficial
    tab["nome_provisorio"] = tab["logradouro"].str.contains(r"SEM DENOMINA|SEM NOME|\bSETOR \d|PROJETAD|\d{5,}|\b43 22400\b", regex=True)
    tab[["tipo", "logradouro", "nome_norm", "n_enderecos", "n_domicilios_particulares", "n_bairros", "bairros_principais",
         "pct_urbano", "correspondencia", "nome_osm", "dist_mediana_enderecos_via_osm_m", "casado_e_perto_300m",
         "nome_provisorio"]].to_csv(
        saida / "P1_logradouros_cnefe_x_osm.csv", index=False)

    # resumo por nível (cumulativo), simples e ponderado por endereços
    niveis = ["exato", "normalizado", "codigo_ref", "aproximado", "aproximado_espacial"]
    linhas = []
    for zona, sub in [("todos", tab), ("predominante urbano", tab[tab["pct_urbano"] >= 50]),
                      ("predominante rural", tab[tab["pct_urbano"] < 50])]:
        for i, nv in enumerate(niveis):
            m = sub["correspondencia"].isin(niveis[: i + 1])
            linhas.append({"recorte": zona, "nivel_cumulativo": nv, "n_logradouros": len(sub), "n_casados": int(m.sum()),
                           "pct_logradouros": pct(m.sum(), len(sub)),
                           "pct_ponderado_enderecos": pct(sub.loc[m, "n_enderecos"].sum(), sub["n_enderecos"].sum()),
                           "pct_casados_e_perto_300m": pct((m & sub["casado_e_perto_300m"]).sum(), len(sub))})
    pd.DataFrame(linhas).to_csv(saida / "P1_resumo_correspondencia.csv", index=False)
    sem = tab[tab["correspondencia"] == "nenhum"]
    sem[["tipo", "logradouro", "n_enderecos", "n_domicilios_particulares", "bairros_principais", "pct_urbano",
         "nome_provisorio"]].to_csv(
        saida / "P1_logradouros_cnefe_sem_via_osm.csv", index=False)

    # nomes OSM ausentes do CNEFE (por nome normalizado e núcleo)
    cn_norm = set(tab["nome_norm"])
    cn_nuc = {nome_nucleo(n) for n in tab["logradouro"]} - {""}
    vn = vias[vias["name"].notna()].copy()
    vn["km"] = vn["comprimento_m"] / 1000
    on = vn.groupby("name").agg(km=("km", "sum"), zona=("zona", lambda s: s.mode().iloc[0]), classe=("highway", lambda s: s.mode().iloc[0]))
    on["no_cnefe"] = [nome_normalizado(n) in cn_norm or nome_nucleo(n) in cn_nuc or
                      any(SequenceMatcher(None, nome_nucleo(n), c).ratio() >= 0.88 for c in cn_nuc) for n in on.index]
    on[~on["no_cnefe"]].sort_values("km", ascending=False).round(2).reset_index().rename(columns={"name": "nome_osm"}).to_csv(
        saida / "P1_nomes_osm_ausentes_do_cnefe.csv", index=False)

    # o que falta nomear: vias urbanas sem nome × logradouro CNEFE dominante dos endereços a até 30 m
    urb = vias[(vias["comprimento_urbano_m"] > 0) & vias["highway"].isin(VEICULARES | {"pedestrian", "footway", "path"})].copy()
    pts = cn_l[cn_l["zona"] == "urbano"][["logradouro", "geometry"]]
    near = gpd.sjoin_nearest(pts, urb[["osm_id", "geometry"]], how="inner", max_distance=30, distance_col="d")
    near = near[~near.index.duplicated()]
    dom = near.groupby("osm_id")["logradouro"].agg(lambda s: s.value_counts().index[0])
    n_dom = near.groupby("osm_id")["logradouro"].agg(lambda s: int(s.value_counts().iloc[0]))
    n_tot = near.groupby("osm_id").size()
    sem_nome = urb[urb["name"].isna()].copy()
    sem_nome["km_urbano"] = sem_nome["comprimento_urbano_m"] / 1000
    sem_nome["logradouro_cnefe_dominante"] = sem_nome["osm_id"].map(dom)
    sem_nome["n_enderecos"] = sem_nome["osm_id"].map(n_tot).fillna(0).astype(int)
    sem_nome["pct_dominante"] = (100 * sem_nome["osm_id"].map(n_dom) / sem_nome["n_enderecos"].replace(0, np.nan)).round(1)
    sem_nome["proposta_cnefe"] = (sem_nome["n_enderecos"] >= MIN_CELULA) & (sem_nome["pct_dominante"] >= 60)
    sem_nome["nome_dominante_provisorio"] = sem_nome["logradouro_cnefe_dominante"].map(
        tab.set_index("logradouro")["nome_provisorio"]).fillna(False).astype(bool)
    sem_nome["n_enderecos_publicavel"] = sem_nome["n_enderecos"].map(suprimir)
    sem_nome[["osm_id", "highway", "km_urbano", "logradouro_cnefe_dominante", "n_enderecos_publicavel", "pct_dominante",
              "proposta_cnefe", "nome_dominante_provisorio"]].sort_values("km_urbano", ascending=False).round(3).to_csv(
        saida / "P1_vias_urbanas_sem_nome_x_cnefe.csv", index=False)
    km_urb = urb["comprimento_urbano_m"].sum() / 1000
    km_sem = sem_nome["km_urbano"].sum()
    res["P1"] = {
        "cnefe_registros": len(cn), "cnefe_logradouros_distintos": len(tab),
        "resumo": linhas, "sem_correspondente_n": len(sem), "sem_correspondente_enderecos": int(sem["n_enderecos"].sum()),
        "provisorios_n": int(tab["nome_provisorio"].sum()), "provisorios_enderecos": int(tab.loc[tab["nome_provisorio"], "n_enderecos"].sum()),
        "sem_correspondente_provisorios_n": int(sem["nome_provisorio"].sum()),
        "sem_correspondente_nome_definitivo_n": int((~sem["nome_provisorio"]).sum()),
        "sem_correspondente_nome_definitivo_enderecos": int(sem.loc[~sem["nome_provisorio"], "n_enderecos"].sum()),
        "sem_correspondente_definitivo_top10": sem[~sem["nome_provisorio"]].head(10)[["logradouro", "n_enderecos", "bairros_principais"]].to_dict("records"),
        "sem_correspondente_top10": sem.head(10)[["logradouro", "n_enderecos", "bairros_principais"]].to_dict("records"),
        "osm_nomes": len(on), "osm_nomes_ausentes_cnefe": int((~on["no_cnefe"]).sum()),
        "vias_urbanas_km": round(km_urb, 1), "vias_urbanas_sem_nome_km": round(km_sem, 1),
        "vias_urbanas_sem_nome_pct_km": pct(km_sem, km_urb),
        "sem_nome_com_enderecos_km": round(sem_nome.loc[sem_nome["n_enderecos"] > 0, "km_urbano"].sum(), 1),
        "sem_nome_nomeaveis_pelo_cnefe_km": round(sem_nome.loc[sem_nome["proposta_cnefe"], "km_urbano"].sum(), 1),
        "sem_nome_nomeaveis_pelo_cnefe_n": int(sem_nome["proposta_cnefe"].sum()),
        "sem_nome_nomeaveis_cnefe_nome_definitivo_km": round(sem_nome.loc[sem_nome["proposta_cnefe"] & ~sem_nome["nome_dominante_provisorio"], "km_urbano"].sum(), 1),
        "sem_nome_nomeaveis_cnefe_nome_definitivo_n": int((sem_nome["proposta_cnefe"] & ~sem_nome["nome_dominante_provisorio"]).sum()),
        "sem_nome_sem_endereco_km": round(sem_nome.loc[sem_nome["n_enderecos"] == 0, "km_urbano"].sum(), 1),
        "sem_nome_por_classe_km": sem_nome.groupby("highway")["km_urbano"].sum().round(1).to_dict(),
    }

    # PARTE 5 — rótulos do Plano Diretor para vias urbanas sem nome
    res["P5"] = proposta_plano(saida, vias, g_urb)
    return res


GENERICOS = {"UM", "DOIS", "TRES", "QUATRO", "CINCO", "SEIS", "SETE", "OITO", "NOVE", "DEZ", "A", "B", "C", "D", "E", "F",
             "G", "H", "1", "2", "3", "4", "5", "6", "7", "8", "9", "10"}


def angulo_via(geom, pt) -> float | None:
    """Direção (graus, 0–180) do trecho da via mais próximo do ponto."""
    linhas = list(getattr(geom, "geoms", [geom]))
    lin = min(linhas, key=lambda g: g.distance(pt))
    s = lin.project(pt)
    a, b = lin.interpolate(max(s - 10, 0)), lin.interpolate(min(s + 10, lin.length))
    if a.equals(b):
        return None
    return float(np.degrees(np.arctan2(b.y - a.y, b.x - a.x)) % 180)


def dif_ang(a, b) -> float:
    d = abs(a - b) % 180
    return min(d, 180 - d)


def proposta_plano(saida: Path, vias: gpd.GeoDataFrame, g_urb) -> dict:
    topo = gpd.read_file(REF["toponimia"], layer="nomes_de_ruas").to_crs(CRS_PADRAO)
    cand = vias[(vias["comprimento_urbano_m"] > 0) & vias["highway"].isin(VEICULARES | {"pedestrian", "footway", "path"})]
    sem_nome = cand[cand["name"].isna()]
    nomeadas = cand[cand["name"].notna()]
    props, divergencias = [], []
    for _, r in topo.iterrows():
        pt, txt = r.geometry, r["texto"]
        ang_txt = float(r["angulo_graus"]) % 180 if pd.notna(r["angulo_graus"]) else None
        perto = cand[cand.distance(pt) <= 100].copy()
        if perto.empty:
            continue
        perto["d"] = perto.distance(pt)
        perto["ang"] = perto.geometry.map(lambda g: angulo_via(g, pt))
        perto["dang"] = perto["ang"].map(lambda a: dif_ang(a, ang_txt) if a is not None and ang_txt is not None else 90)
        alinhadas = perto[perto["dang"] <= 30].sort_values("d")
        if alinhadas.empty:
            continue
        alvo = alinhadas.iloc[0]
        if pd.notna(alvo["name"]):  # o rótulo pertence a uma via que já tem nome: registra se o nome diverge
            nu_t, nu_o = nome_nucleo(txt), nome_nucleo(alvo["name"])
            igual = nu_t == nu_o or compat_iniciais(nu_t, nu_o) or SequenceMatcher(None, nu_t, nu_o).ratio() >= 0.88
            outros = [n for n in (alvo["alt_name"], alvo["official_name"]) if isinstance(n, str)]
            igual = igual or any(nome_nucleo(n) == nu_t for n in outros)
            divergencias.append({"osm_id": int(alvo["osm_id"]), "nome_osm": alvo["name"], "nome_plano": txt,
                                 "distancia_m": round(float(alvo["d"]), 1), "mesmo_nome": bool(igual),
                                 "generico_plano": bool(re.fullmatch(r"(RUA|TRAVESSA|BECO) \S+", norm_txt(txt)) and
                                                        len(norm_txt(txt).split()) == 2 and
                                                        norm_txt(txt).split()[1] in GENERICOS)})
            continue
        nn = nome_normalizado(txt)
        # conflito: via nomeada alinhada a até 100 m com nome diferente e mais perto que 1,5× a distância do alvo
        viz = alinhadas[alinhadas["name"].notna() & (alinhadas["d"] <= 1.5 * alvo["d"] + 10)]
        conflito = any(nome_nucleo(n) != nome_nucleo(txt) for n in viz["name"])
        mesmo_nome_perto = (nomeadas[nomeadas.distance(pt) <= 300]["name"].map(nome_normalizado) == nn).any()
        generico = bool(re.fullmatch(r"(RUA|TRAVESSA|BECO) (\d+|[A-Z]|UM|DOIS|TRES|QUATRO|CINCO|SEIS|SETE|OITO|NOVE|DEZ|SEM SAIDA)",
                                     norm_txt(txt)))
        if conflito:
            conf = "baixa"
        elif alvo["d"] <= 30 and alvo["dang"] <= 10:
            conf = "alta"
        elif alvo["d"] <= 60 and alvo["dang"] <= 20:
            conf = "média"
        else:
            conf = "baixa"
        if generico and conf == "alta":
            conf = "média"
        props.append({"osm_id": int(alvo["osm_id"]), "highway": alvo["highway"], "nome_proposto": txt, "nome_proposto_norm": nn,
                      "distancia_m": round(float(alvo["d"]), 1), "dif_angulo_graus": round(float(alvo["dang"]), 1),
                      "confianca": conf, "conflito_via_nomeada_vizinha": conflito, "generico": generico,
                      "mesmo_nome_em_via_osm_a_300m": bool(mesmo_nome_perto),
                      "km_urbano_via": round(alvo["comprimento_urbano_m"] / 1000, 3),
                      "fonte": "Plano Diretor 2013 (Mapa 13), não oficial"})
    p = pd.DataFrame(props)
    ordem = {"alta": 0, "média": 1, "baixa": 2}
    if len(p):
        # uma proposta por via: a de maior confiança e menor distância
        p = p.sort_values(["osm_id", "confianca", "distancia_m"], key=lambda s: s.map(ordem) if s.name == "confianca" else s)
        n_rotulos = p.groupby("osm_id").size()
        nomes_div = p.groupby("osm_id")["nome_proposto_norm"].nunique()
        p = p.drop_duplicates("osm_id").copy()
        p["n_rotulos_na_via"] = p["osm_id"].map(n_rotulos)
        p["rotulos_divergentes"] = p["osm_id"].map(nomes_div) > 1
        p.loc[p["rotulos_divergentes"] & (p["confianca"] == "alta"), "confianca"] = "média"
    p.to_csv(saida / "P5_proposta_nomes_plano_vias_sem_nome.csv", index=False)
    dv = pd.DataFrame(divergencias).drop_duplicates(["osm_id", "nome_plano"])
    dv[~dv["mesmo_nome"]].sort_values("nome_osm").to_csv(saida / "P5_divergencias_nome_plano_x_osm.csv", index=False)
    km_sem = sem_nome["comprimento_urbano_m"].sum() / 1000
    return {"rotulos_plano": len(topo), "vias_urbanas_sem_nome": len(sem_nome), "vias_urbanas_sem_nome_km": round(km_sem, 1),
            "vias_com_proposta": len(p),
            "por_confianca_n": p["confianca"].value_counts().to_dict() if len(p) else {},
            "por_confianca_km": p.groupby("confianca")["km_urbano_via"].sum().round(2).to_dict() if len(p) else {},
            "km_total_proposto": round(p["km_urbano_via"].sum(), 2) if len(p) else 0,
            "pct_km_sem_nome_coberto": pct(p["km_urbano_via"].sum(), km_sem) if len(p) else 0,
            "rotulos_sobre_via_ja_nomeada": len(dv), "rotulos_com_nome_igual_ao_osm": int(dv["mesmo_nome"].sum()),
            "rotulos_com_nome_divergente": int((~dv["mesmo_nome"]).sum()),
            "divergentes_genericos_no_plano": int((~dv["mesmo_nome"] & dv["generico_plano"]).sum())}


# ---------------------------------------------------------------------------
# PARTE 2 — faces de logradouros do IBGE
# ---------------------------------------------------------------------------
def listar(url: str) -> list[str]:
    html = requests.get(url, headers={"User-Agent": UA}, timeout=60).text
    return [h for h in re.findall(r'href="([^"?]+)"', html) if not h.startswith(("http", "/"))]


def parte_faces(saida: Path, faces_zip: Path | None) -> dict:
    if faces_zip is None:
        arquivos = []
        for sub in listar(FACES_URL):
            for f in listar(FACES_URL + sub):
                h = requests.head(FACES_URL + sub + f, headers={"User-Agent": UA}, timeout=60)
                arquivos.append({"pasta": sub, "arquivo": f, "mb": round(int(h.headers.get("Content-Length", 0)) / 1e6, 1),
                                 "modificado": h.headers.get("Last-Modified")})
        tab = pd.DataFrame(arquivos)
        tab.to_csv(saida / "P2_faces_ibge_ftp_listagem.csv", index=False)
        mun = [a for a in arquivos if "4322400" in a["arquivo"] or "URUGUAIANA" in a["arquivo"].upper()]
        rs = tab[tab["arquivo"].str.startswith("RS_")].to_dict("records")
        res = {"url_consultada": FACES_URL, "existe_arquivo_municipal": bool(mun), "arquivos_rs": rs,
               "n_arquivos": len(arquivos), "recorte": "só por UF (json e shp)",
               "situacao": "não baixado: não há arquivo só do município; a autorização da rodada cobre só o arquivo municipal"}
        (saida / "P2_faces_ibge_consulta.json").write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
        return res
    # com o arquivo da UF já em disco: filtra o município e compara (executado só se o responsável autorizar)
    _, g_mun, g_urb, _ = base()
    faces = gpd.read_file(f"/vsizip/{faces_zip}")
    col_mun = next(c for c in faces.columns if c.upper() in ("CD_MUN", "CD_MUNICIPIO", "COD_MUN"))
    faces = faces[faces[col_mun].astype(str) == "4322400"].to_crs(CRS_PADRAO)
    vias = carregar_vias()
    urb = vias[vias["comprimento_urbano_m"] > 0]
    buf = faces.geometry.buffer(25).union_all()
    res = {"feicoes": len(faces), "colunas": list(faces.columns), "km_faces": round(faces.length.sum() / 1000, 1),
           "pct_km_vias_osm_urbanas_a_25m_de_face": pct(urb.geometry.intersection(buf).length.sum(), urb.length.sum())}
    (saida / "P2_faces_ibge_descricao.json").write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    return res


# ---------------------------------------------------------------------------
# PARTE 3 — partes novas pelos endereços
# ---------------------------------------------------------------------------
def quadras_derivadas(vias: gpd.GeoDataFrame, g_urb) -> gpd.GeoSeries:
    """Mesma poligonização da rodada 65: malha veicular urbana (sem track e sem acessos de estacionamento)."""
    t = vias["tags"].map(json.loads)
    m = vias["highway"].isin(VEICULARES - {"track"}) & ~t.map(lambda x: x.get("service") in {"parking_aisle", "driveway", "drive-through"})
    malha = vias[m].geometry.intersection(g_urb)
    malha = malha[~malha.is_empty]
    blocos = gpd.GeoSeries(list(polygonize(unary_union(list(malha.explode(index_parts=False)) + [g_urb.boundary]))), crs=CRS_PADRAO)
    blocos = blocos[blocos.within(g_urb.buffer(1))]
    return blocos[(blocos.area >= 1000) & (blocos.area <= 300_000)]


def parte_partes_novas(saida: Path) -> dict:
    _, g_mun, g_urb, bai = base()
    quadras = gpd.read_file(REF["quadras"], layer="quadras").to_crs(CRS_PADRAO)
    env = quadras.union_all().buffer(30).buffer(-15)  # envoltória da rodada 65
    casco = quadras.union_all().convex_hull
    dom = gpd.read_file(CNEFE_DOM, columns=["setor_2022"]).to_crs(CRS_PADRAO)  # espécie 1: domicílio particular
    dom["fora_env"] = ~dom.within(env)
    dom["fora_casco"] = ~dom.within(casco)
    dom["urbano"] = dom.within(g_urb)
    j = gpd.sjoin(dom, bai[["NM_BAIRRO", "geometry"]], predicate="within", how="left")
    dom["bairro"] = j["NM_BAIRRO"].groupby(level=0).first().fillna("(fora dos bairros)")
    total = len(dom)

    por_b = dom.groupby("bairro").agg(n_domicilios=("fora_env", "size"), n_fora_envoltoria=("fora_env", "sum"),
                                      n_fora_casco=("fora_casco", "sum"))
    por_b["pct_bairro_fora_envoltoria"] = (100 * por_b["n_fora_envoltoria"] / por_b["n_domicilios"]).round(1)
    por_b["pct_do_total_fora_envoltoria"] = (100 * por_b["n_fora_envoltoria"] / dom["fora_env"].sum()).round(1)
    por_b = por_b.sort_values("n_fora_envoltoria", ascending=False)
    for c in ("n_domicilios", "n_fora_envoltoria", "n_fora_casco"):
        por_b[c] = por_b[c].map(suprimir)
    por_b.reset_index().to_csv(saida / "P3_enderecos_fora_plano_por_bairro.csv", index=False)

    por_s = dom.groupby("setor_2022").agg(n_domicilios=("fora_env", "size"), n_fora_envoltoria=("fora_env", "sum"),
                                          n_fora_casco=("fora_casco", "sum"))
    por_s = por_s[por_s["n_fora_envoltoria"] > 0].sort_values("n_fora_envoltoria", ascending=False)
    for c in por_s.columns:
        por_s[c] = por_s[c].map(suprimir)
    por_s.reset_index().to_csv(saida / "P3_enderecos_fora_plano_por_setor.csv", index=False)

    # grade de 250 m (alinhada a múltiplos de 250 m em UTM), só células com ≥ 5 endereços
    x, y = dom.geometry.x, dom.geometry.y
    dom["cx"], dom["cy"] = (np.floor(x / 250) * 250).astype(int), (np.floor(y / 250) * 250).astype(int)
    gr = dom.groupby(["cx", "cy"]).agg(n_domicilios=("fora_env", "size"), n_fora_envoltoria=("fora_env", "sum"),
                                       n_fora_casco=("fora_casco", "sum")).reset_index()
    suprimidas = int((gr["n_domicilios"] < MIN_CELULA).sum())
    gr = gr[gr["n_domicilios"] >= MIN_CELULA].copy()
    for c in ("n_fora_envoltoria", "n_fora_casco"):
        gr[c] = gr[c].map(suprimir)
    gr["id_celula"] = gr["cx"].astype(str) + "_" + gr["cy"].astype(str)
    gg = gpd.GeoDataFrame(gr.drop(columns=["cx", "cy"]), geometry=[box(a, b, a + 250, b + 250) for a, b in zip(gr["cx"], gr["cy"])],
                          crs=CRS_PADRAO)
    gpkg = saida / "P3_grade_250m_domicilios.gpkg"
    gpkg.unlink(missing_ok=True)
    gg.to_file(gpkg, layer="grade_250m", driver="GPKG")
    gg.drop(columns="geometry").to_csv(saida / "P3_grade_250m_domicilios.csv", index=False)
    sidecar(gpkg, fonte="IBGE — CNEFE 2022 (domicílios particulares) agregado em grade de 250 m", crs=CRS_PADRAO,
            supressao=f"células com menos de {MIN_CELULA} endereços removidas ({suprimidas}); subcontagens de 1 a 4 em branco",
            transformacao_aplicada="contagem de endereços por célula (origem em múltiplos de 250 m, EPSG:31981); fora_envoltoria "
                                   "= fora da união das quadras do Plano + 30 m − 15 m; fora_casco = fora do casco convexo das quadras")

    # cobertura das partes novas (urbano, fora da envoltória) pelo OSM
    vias = carregar_vias()
    blocos = quadras_derivadas(vias, g_urb)
    pol = gpd.read_file(EXTRATO_OSM, layer="poligonos")
    lu = pol[pol["landuse"].isin(["residential", "commercial", "industrial", "retail"])].union_all()
    alvo = dom[dom["fora_env"] & dom["urbano"]]
    alvo_c = dom[dom["fora_casco"] & dom["urbano"]]
    u_blocos = blocos.union_all()
    res = {"domicilios_total": total,
           "fora_envoltoria_n": int(dom["fora_env"].sum()), "fora_envoltoria_pct": pct(dom["fora_env"].sum(), total),
           "fora_casco_n": int(dom["fora_casco"].sum()), "fora_casco_pct": pct(dom["fora_casco"].sum(), total),
           "fora_envoltoria_urbano_n": len(alvo), "fora_envoltoria_urbano_pct": pct(len(alvo), total),
           "fora_casco_urbano_n": len(alvo_c), "fora_casco_urbano_pct": pct(len(alvo_c), total),
           "rural_n": int((~dom["urbano"]).sum()),
           "bairros_top5_fora_envoltoria": por_b.head(5)[["n_fora_envoltoria", "pct_do_total_fora_envoltoria"]].reset_index().to_dict("records"),
           "quadras_derivadas_n": len(blocos),
           "partes_novas_em_quadra_derivada_pct": pct(alvo.within(u_blocos).sum(), len(alvo)),
           "partes_novas_em_landuse_osm_pct": pct(alvo.within(lu).sum(), len(alvo)),
           "fora_casco_em_quadra_derivada_pct": pct(alvo_c.within(u_blocos).sum(), len(alvo_c)),
           "fora_casco_em_landuse_osm_pct": pct(alvo_c.within(lu).sum(), len(alvo_c)),
           "grade_celulas_publicadas": len(gg), "grade_celulas_suprimidas": suprimidas}
    pd.DataFrame([res]).T.to_csv(saida / "P3_resumo.csv", header=["valor"])
    return res


# ---------------------------------------------------------------------------
# PARTE 4 — Overture Maps
# ---------------------------------------------------------------------------
SQL_OVERTURE = r"""
import duckdb, json, sys
rel, w, s, e, n, saida = sys.argv[1], *map(float, sys.argv[2:6]), sys.argv[6]
camadas = json.loads(sys.argv[7])
c = duckdb.connect()
c.sql("INSTALL httpfs; LOAD httpfs; INSTALL spatial; LOAD spatial; SET s3_region='us-west-2'; SET enable_progress_bar=false;")
out = {}
for nome, (tema, tipo) in camadas.items():
    src = f"s3://overturemaps-us-west-2/release/{rel}/theme={tema}/type={tipo}/*"
    filtro = f"bbox.xmin <= {e} AND bbox.xmax >= {w} AND bbox.ymin <= {n} AND bbox.ymax >= {s}"
    q = f"SELECT * EXCLUDE (geometry), ST_AsWKB(geometry) AS geom_wkb FROM read_parquet('{src}', hive_partitioning=1) WHERE {filtro}"
    arq = f"{saida}/{nome}.parquet"
    c.sql(f"COPY ({q}) TO '{arq}' (FORMAT parquet, COMPRESSION zstd)")
    out[nome] = c.sql(f"SELECT count(*) FROM read_parquet('{arq}')").fetchone()[0]
print(json.dumps(out))
"""


def parte_overture(forcar: bool, python_ext: str) -> dict:
    rel = requests.get(OVERTURE_STAC, headers={"User-Agent": UA}, timeout=60).json()["latest"]
    pasta = PASTA / f"overture_{rel}"
    pasta.mkdir(parents=True, exist_ok=True)
    falta = {k: v[:2] for k, v in CAMADAS_OVERTURE.items() if forcar or not (pasta / f"{k}.parquet").exists()}
    mun = carregar_area_estudo()
    w, s, e, n = mun.buffer(2000).to_crs("EPSG:4326").total_bounds
    if falta:
        logger.info("Overture %s: baixando %s (bbox %.4f %.4f %.4f %.4f)", rel, list(falta), w, s, e, n)
        r = subprocess.run([python_ext, "-c", SQL_OVERTURE, rel, str(w), str(s), str(e), str(n), str(pasta), json.dumps(falta)],
                           capture_output=True, text=True, check=True)
        logger.info("Feições: %s", r.stdout.strip())
    else:
        logger.info("Overture %s já baixado — use --forcar", rel)
    res = {"release": rel, "bbox_4326": [round(v, 5) for v in (w, s, e, n)], "camadas": {}}
    for k, (tema, tipo, lic) in CAMADAS_OVERTURE.items():
        arq = pasta / f"{k}.parquet"
        mb = arq.stat().st_size / 1e6
        if mb > LIMITE_MB:
            arq.unlink()
            raise RuntimeError(f"camada {k} passou de {LIMITE_MB} MB ({mb:.0f} MB): apagada, relatar")
        sidecar(arq, fonte=f"Overture Maps Foundation — tema {tema}, tipo {tipo}", versao=rel,
                url=f"{OVERTURE_S3}/{rel}/theme={tema}/type={tipo}/", licenca=lic, data_acesso="2026-10-21",
                area="retângulo da área de estudo + 2 km (EPSG:4326)", bbox=res["bbox_4326"], crs="EPSG:4326 (geom_wkb)",
                atribuicao="© Overture Maps Foundation; contém dados © contribuidores do OpenStreetMap e de outras fontes "
                           "indicadas no campo sources",
                transformacao_aplicada="consulta duckdb ao S3 público filtrando pelo bbox (sem recorte geométrico); geometria em WKB",
                aviso="places pode ter telefone, e-mail e endereço de estabelecimentos: só em contagens" if k == "places" else None)
        res["camadas"][k] = round(mb, 2)
    return res


def ler_overture(nome: str) -> gpd.GeoDataFrame:
    import pyarrow.parquet as pq

    arq = sorted(PASTA.glob(f"overture_*/{nome}.parquet"))[-1]
    df = pq.read_table(arq).to_pandas()
    g = gpd.GeoDataFrame(df.drop(columns=["geom_wkb"]), geometry=shapely.from_wkb(df["geom_wkb"]), crs="EPSG:4326")
    return g.to_crs(CRS_PADRAO)


def nome_primario(v):
    return v.get("primary") if isinstance(v, dict) else None


def parte_analise(saida: Path) -> dict:
    _, g_mun, g_urb, bai = base()
    res = {}
    # edificações
    b = ler_overture("buildings")
    b = b[b.intersects(g_mun)].copy()
    b["fontes"] = b["sources"].map(lambda ss: sorted({x.get("dataset") for x in ss}) if ss is not None else [])
    c = b.geometry.representative_point()
    fontes = Counter(f for fs in b["fontes"] for f in fs)
    fonte_unica = Counter("+".join(fs) for fs in b["fontes"])
    jb = gpd.sjoin(gpd.GeoDataFrame(geometry=c.values, crs=CRS_PADRAO), bai[["NM_BAIRRO", "geometry"]], predicate="within")
    cont = jb.groupby("NM_BAIRRO").size()
    tb = bai[["NM_BAIRRO", "enderecos_domicilio_particular"]].copy()
    tb["edificacoes_overture"] = tb["NM_BAIRRO"].map(cont).fillna(0).astype(int)
    tb["razao_overture_por_endereco_domicilio"] = (tb["edificacoes_overture"] / tb["enderecos_domicilio_particular"]).round(3)
    tb.drop(columns="enderecos_domicilio_particular").sort_values("razao_overture_por_endereco_domicilio").to_csv(
        saida / "P4_overture_edificacoes_por_bairro_razao_cnefe.csv", index=False)
    res["buildings"] = {"n_municipio": len(b), "area_km2": round(b.area.sum() / 1e6, 3),
                        "n_urbano": int(c.within(g_urb).sum()), "n_rural": int((~c.within(g_urb)).sum()),
                        "fontes": dict(fontes), "fontes_combinadas": dict(fonte_unica.most_common(6)),
                        "pct_height": pct(b["height"].notna().sum(), len(b)), "pct_num_floors": pct(b["num_floors"].notna().sum(), len(b)),
                        "pct_class": pct(b["class"].notna().sum(), len(b)),
                        "bairros_edificacoes": int(tb["edificacoes_overture"].sum()),
                        "bairros_enderecos_domicilio": int(tb["enderecos_domicilio_particular"].sum()),
                        "razao_geral_bairros": round(tb["edificacoes_overture"].sum() / tb["enderecos_domicilio_particular"].sum(), 3),
                        "razao_mediana_bairros": round(float(tb["razao_overture_por_endereco_domicilio"].median()), 3),
                        "razao_min_max": [float(tb["razao_overture_por_endereco_domicilio"].min()),
                                          float(tb["razao_overture_por_endereco_domicilio"].max())]}
    # transporte
    sg = ler_overture("segment")
    sg = recortar(sg[sg["subtype"] == "road"], g_mun)
    sg["km"] = sg.length / 1000
    sg["nome"] = sg["names"].map(nome_primario)
    sg["fontes"] = sg["sources"].map(lambda ss: "+".join(sorted({x.get("dataset") for x in ss})) if ss is not None else "")
    sg_u, sg_r = recortar(sg, g_urb), recortar(sg, g_mun.difference(g_urb))
    vias = carregar_vias()
    linhas = []
    for zona, sub in [("urbano", sg_u), ("rural", sg_r), ("total", sg)]:
        km = sub.length.sum() / 1000
        linhas.append({"fonte": "Overture", "zona": zona, "km": round(km, 1),
                       "pct_km_nome": pct(sub[sub["nome"].notna()].length.sum() / 1000, km)})
    for zona, col in [("urbano", "comprimento_urbano_m"), ("rural", "comprimento_rural_m"), ("total", "comprimento_m")]:
        km = vias[col].sum() / 1000
        linhas.append({"fonte": "OSM (viario_osm)", "zona": zona, "km": round(km, 1),
                       "pct_km_nome": pct(vias.loc[vias["name"].notna(), col].sum() / 1000, km)})
    pd.DataFrame(linhas).to_csv(saida / "P4_overture_transporte_x_osm.csv", index=False)
    # nomes do Overture em vias sem nome no OSM (o que ele traz a mais)
    osm_sem = vias[vias["name"].isna()]
    sg_nom = sg[sg["nome"].notna()]
    extra = gpd.sjoin(sg_nom[["nome", "geometry"]], gpd.GeoDataFrame(geometry=osm_sem.geometry.buffer(5), crs=CRS_PADRAO),
                      predicate="within")
    res["transportation"] = {"linhas": linhas, "fontes_km": sg.groupby("fontes")["km"].sum().round(1).to_dict(),
                             "classes_km": sg.groupby("class")["km"].sum().round(1).sort_values(ascending=False).head(12).to_dict(),
                             "segmentos_com_nome_sobre_via_osm_sem_nome": int(extra.index.nunique())}
    # places
    pl = ler_overture("places")
    pl = pl[pl.within(g_mun)].copy()
    pl["cat"] = pl["taxonomy"].map(lambda t: t.get("primary") if isinstance(t, dict) else None)
    pl["basic"] = pl["basic_category"]
    saude_re = r"hospital|clinic|health|medical|doctor|pharmac|dentist|urgent|emergency_room|community_health|physician"
    sau = pl[pl["cat"].fillna("").str.contains(saude_re) | pl["basic"].fillna("").str.contains(saude_re)]
    esf = gpd.read_file(REF["esf"]).to_crs(CRS_PADRAO)
    d_esf = esf.geometry.map(lambda g: sau.distance(g).min() if len(sau) else np.inf)
    nome_ubs = sau["names"].map(nome_primario).fillna("").map(lambda n: bool(re.search(
        r"\b(ESF|UBS|USF|POSTO DE SAUDE|UNIDADE BASICA|SAUDE DA FAMILIA|ESTRATEGIA)\b", norm_txt(n))))
    d_sau = sau.geometry.map(lambda g: esf.distance(g).min())
    contato = {k: int(pl[k].map(lambda v: v is not None and len(v) > 0).sum()) for k in ("phones", "emails", "websites", "socials")}
    res["places"] = {"n_municipio": len(pl), "n_urbano": int(pl.within(g_urb).sum()),
                     "categorias_top": pl["basic"].value_counts().head(15).to_dict(),
                     "saude_n": len(sau), "saude_categorias": sau["basic"].value_counts().head(10).to_dict(),
                     "esf_com_place_saude_a_150m": int((d_esf <= 150).sum()), "esf_sem_place_saude_a_150m": int((d_esf > 150).sum()),
                     "places_nome_ubs_esf": int(nome_ubs.sum()),
                     "places_nome_ubs_esf_fora_cadastro_150m": int((nome_ubs & (d_sau > 150)).sum()),
                     "places_saude_fora_cadastro_150m": int((d_sau > 150).sum()),
                     "confianca_mediana": round(float(pl["confidence"].median()), 2), "com_contato_contagens": contato}
    pd.DataFrame([{"categoria": k, "n": v} for k, v in pl["basic"].value_counts().items()]).to_csv(
        saida / "P4_overture_places_categorias.csv", index=False)
    # divisões
    da = ler_overture("division_area")
    da = da[da.intersects(g_mun)].copy()
    da["nome"] = da["names"].map(nome_primario)
    dv = ler_overture("division")
    dv = dv[dv.within(g_mun)].copy()
    dv["nome"] = dv["names"].map(nome_primario)
    bai_k = {norm_txt(n): n for n in bai["NM_BAIRRO"]}
    lin_d = []
    for _, r in da.iterrows():
        k = norm_txt(r["nome"] or "")
        par = bai_k.get(k)
        iou = None
        if par is not None:
            gb = bai.loc[bai["NM_BAIRRO"] == par].geometry.iloc[0]
            inter = r.geometry.intersection(gb).area
            iou = round(inter / r.geometry.union(gb).area, 3)
        lin_d.append({"tipo": "division_area", "subtype": r["subtype"], "class": r.get("class"), "nome": r["nome"],
                      "area_km2_no_municipio": round(r.geometry.intersection(g_mun).area / 1e6, 3),
                      "bairro_ibge_mesmo_nome": par, "iou_com_bairro_ibge": iou})
    for _, r in dv.iterrows():
        par = bai_k.get(norm_txt(r["nome"] or ""))
        lin_d.append({"tipo": "division (ponto)", "subtype": r["subtype"], "class": r.get("class"), "nome": r["nome"],
                      "area_km2_no_municipio": None, "bairro_ibge_mesmo_nome": par, "iou_com_bairro_ibge": None})
    td = pd.DataFrame(lin_d)
    td.to_csv(saida / "P4_overture_divisions_x_bairros_ibge.csv", index=False)
    res["divisions"] = {"areas_por_subtype": da["subtype"].value_counts().to_dict(),
                        "pontos_por_subtype": dv["subtype"].value_counts().to_dict(),
                        "areas_com_nome_de_bairro_ibge": int(td[td["tipo"] == "division_area"]["bairro_ibge_mesmo_nome"].notna().sum()),
                        "pontos_com_nome_de_bairro_ibge": int(td[td["tipo"] != "division_area"]["bairro_ibge_mesmo_nome"].notna().sum()),
                        "iou_mediana": float(td["iou_com_bairro_ibge"].median()) if td["iou_com_bairro_ibge"].notna().any() else None}
    return res


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("etapa", choices=["overture", "faces", "nomes", "partes", "analise", "todos"])
    ap.add_argument("--codigo-ibge", default="4322400")
    ap.add_argument("--forcar", action="store_true")
    ap.add_argument("--saida-tabelas", type=Path, default=PASTA / "diagnostico")
    ap.add_argument("--faces-zip", type=Path, default=None, help="arquivo de faces da UF já baixado (opcional)")
    ap.add_argument("--python-overture", default=os.environ.get("OVERTURE_PYTHON", "python3"),
                    help="python de uma venv fora do repositório com duckdb instalado")
    a = ap.parse_args()
    cod = gpd.read_file(CAMINHO_AREA_ESTUDO_PADRAO).get("codarea")
    if cod is not None and str(cod.iloc[0]) != str(a.codigo_ibge):
        sys.exit(f"área de estudo é do código {cod.iloc[0]}, não {a.codigo_ibge}")
    if str(a.codigo_ibge) not in CNEFE_ZIP.name:
        sys.exit("o arquivo do CNEFE em cache não é do município pedido")
    a.saida_tabelas.mkdir(parents=True, exist_ok=True)
    PASTA.mkdir(parents=True, exist_ok=True)
    res_arq = a.saida_tabelas / "resumo_fontes_complementares.json"
    res = json.loads(res_arq.read_text(encoding="utf-8")) if res_arq.exists() else {}
    try:
        if a.etapa in ("faces", "todos"):
            res["P2"] = parte_faces(a.saida_tabelas, a.faces_zip)
        if a.etapa in ("nomes", "todos"):
            res.update(parte_nomes(a.saida_tabelas))
        if a.etapa in ("partes", "todos"):
            res["P3"] = parte_partes_novas(a.saida_tabelas)
        if a.etapa in ("overture", "todos"):
            res["P4_download"] = parte_overture(a.forcar, a.python_overture)
        if a.etapa in ("analise", "todos"):
            res["P4"] = parte_analise(a.saida_tabelas)
    except (RuntimeError, subprocess.CalledProcessError, FileNotFoundError) as e:
        logger.error("%s", getattr(e, "stderr", None) or e)
        sys.exit(1)
    finally:
        res_arq.write_text(json.dumps(res, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    logger.info("Resumo em %s", res_arq)


if __name__ == "__main__":
    main()
