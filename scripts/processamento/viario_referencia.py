"""
Viário de referência (rodada 69): uma linha por via do OSM (camada `vias` do
viário Geofabrik, osm_id real) e UMA COLUNA POR FONTE de nome — OSM, CNEFE
2022 (agregado), faces de logradouros do IBGE 2022 e rótulos do Plano Diretor
2013 —, sem harmonizar nem sobrescrever o original de cada fonte. Acrescenta a
concordância entre as fontes, as propostas da triangulação da rodada 68 (nome e
ref rural, lidas do produto existente) e campos vazios para a revisão.

Nada aqui altera a camada `vias`, as propostas da rodada 68 nem o portal.

Regras por fonte (as mesmas da rodada 68, agora para TODAS as vias):
    CNEFE urbano  endereço urbano → via mais próxima a ≤ 30 m (vias veiculares e
                  pedestres com trecho urbano); logradouro dominante aceito com
                  ≥ 60% de ≥ 5 endereços.
    CNEFE rural   endereço rural → via veicular rural mais próxima a ≤ 100 m
                  (casas recuadas da estrada); mesmo limiar. ref_cnefe = código
                  UR/BR/RS… dominante com ≥ 5 endereços e ≥ 60% (regra da 2B).
                  Vale só quando a regra urbana não deu nome.
    Faces IBGE    face alinhada (≤ 25 m, ≤ 20°, amostras a cada 10 m); nome
                  dominante aceito com ≥ 50% de cobertura e ≥ 60% de dominância.
    Plano 2013    rótulo a ≤ 100 m com eixo alinhado (≤ 30°) à via (mesma
                  atribuição da rodada 67); vale o rótulo mais próximo.
Nome provisório do recenseamento (SEM DENOMINACAO, RUA nn SETOR…) fica na
coluna da fonte, marcado provisório, e não conta como nome na concordância.

Privacidade (CNEFE): só o logradouro dominante e a contagem por via (1 a 4
suprimidos). Os pontos só servem a junções internas e não vão para a saída.

Uso:
    python scripts/processamento/viario_referencia.py --codigo-ibge 4322400 [--forcar]
"""

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

RAIZ = Path(__file__).resolve().parents[2]
sys.path.append(str(RAIZ / "scripts" / "utils"))
sys.path.append(str(RAIZ / "scripts" / "exploracao"))
from recorte_municipio import CRS_PADRAO  # noqa: E402
from osm_levantamento import REF, VEICULARES, nome_normalizado  # noqa: E402
from fontes_complementares import MIN_CELULA, VIAS, angulo_via, base, carregar_cnefe, carregar_vias, dif_ang, suprimir  # noqa: E402
from faces_nomes_triangulacao import (  # noqa: E402
    PRIORIDADE, URBANAS, cnefe_por_via, codigo_ref, decidir, mesmo_nome, nome_face_por_via, provisorio,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

PASTA_VIARIO = RAIZ / "data" / "processed" / "viario"
DATA_PRODUTO = "2026-10-21"
SAIDA = PASTA_VIARIO / f"viario_referencia_{DATA_PRODUTO}.gpkg"
PROPOSTAS = PASTA_VIARIO / f"vias_nome_proposto_{DATA_PRODUTO}.gpkg"
ATRIB_FISICOS = ["surface", "smoothness", "tracktype", "maxspeed", "lanes", "oneway", "bridge", "tunnel", "layer",
                 "junction", "access", "foot", "bicycle", "width", "incline", "lit"]
SITUACAO = {"cnefe/ibge confirmam OSM": "osm_confirmado", "cnefe/ibge confirmam Plano": "plano_confirmado",
            "sem confirmação: prefeitura": "sem_confirmacao"}
SIM_NAO = {True: "sim", False: "não"}


# ---------------------------------------------------------------------------
# fontes
# ---------------------------------------------------------------------------
def cnefe_rural_por_via(rur: gpd.GeoDataFrame, cn: gpd.GeoDataFrame) -> pd.DataFrame:
    """Regra da 2B: endereço rural → via veicular rural mais próxima a ≤ 100 m; logradouro e código dominantes."""
    pts = cn[cn["logradouro"] != ""][["logradouro", "geometry"]].copy()
    pts["ref_cnefe"] = pts["logradouro"].map(codigo_ref)
    near = gpd.sjoin_nearest(pts, rur[["osm_id", "geometry"]], how="inner", max_distance=100)
    near = near[~near.index.duplicated()]
    g = near.groupby("osm_id")["logradouro"]
    out = pd.DataFrame({"cnefe_n_enderecos": g.size(), "cnefe_dominante": g.agg(lambda s: s.value_counts().index[0]),
                        "cnefe_n_dominante": g.agg(lambda s: int(s.value_counts().iloc[0]))})
    out["cnefe_pct_dominante"] = (100 * out["cnefe_n_dominante"] / out["cnefe_n_enderecos"]).round(1)
    ok = (out["cnefe_n_enderecos"] >= MIN_CELULA) & (out["cnefe_pct_dominante"] >= 60)
    out["nome_cnefe"] = out["cnefe_dominante"].where(ok)
    cod = near.dropna(subset=["ref_cnefe"]).groupby("osm_id")["ref_cnefe"]
    n_sup = cod.agg(lambda s: int(s.value_counts().iloc[0]))
    dom = cod.agg(lambda s: s.value_counts().index[0])
    ok_ref = (n_sup >= MIN_CELULA) & (100 * n_sup / out["cnefe_n_enderecos"].reindex(n_sup.index) >= 60)
    out["ref_cnefe"] = dom.where(ok_ref).reindex(out.index)
    return out


def plano_por_via(cand: gpd.GeoDataFrame) -> pd.DataFrame:
    """Atribuição dos rótulos do Plano (rodada 67): via alinhada (≤ 30°) mais próxima a ≤ 100 m; por via, o rótulo mais próximo."""
    topo = gpd.read_file(REF["toponimia"], layer="nomes_de_ruas").to_crs(CRS_PADRAO)
    idx = cand.sindex
    reg = []
    for r in topo.itertuples():
        pt, txt = r.geometry, r.texto
        ang_txt = float(r.angulo_graus) % 180 if pd.notna(r.angulo_graus) else None
        perto = cand.iloc[idx.query(pt, predicate="dwithin", distance=100)].copy()
        if perto.empty:
            continue
        perto["d"] = perto.distance(pt)
        perto["dang"] = [dif_ang(a, ang_txt) if a is not None and ang_txt is not None else 90
                         for a in (angulo_via(g, pt) for g in perto.geometry)]
        alinhadas = perto[perto["dang"] <= 30].sort_values("d")
        if alinhadas.empty:
            continue
        reg.append({"osm_id": int(alinhadas.iloc[0]["osm_id"]), "nome_plano": txt, "d": float(alinhadas.iloc[0]["d"])})
    r = pd.DataFrame(reg).sort_values(["osm_id", "d"])
    n = r.groupby("osm_id").size()
    distintos = r.assign(k=r["nome_plano"].map(nome_normalizado)).groupby("osm_id")["k"].nunique()
    out = r.drop_duplicates("osm_id").set_index("osm_id")
    out["plano_n_rotulos"] = n
    out["plano_rotulos_divergentes"] = distintos > 1
    return out


# ---------------------------------------------------------------------------
# concordância
# ---------------------------------------------------------------------------
def concordancia(osm: list[str], refs: list[str], outros: dict) -> dict:
    """Agrupa os nomes definitivos das fontes (o OSM vale pelo name, alt_name ou official_name; um nome que é só
    código de estrada — RODOVIA BR 290 — concorda com o OSM quando o código está no ref da via).
    todas = ≥ 2 fontes, um grupo; maioria = o maior grupo tem ≥ 2 e mais da metade; divergem = o resto com ≥ 2."""
    nomes = {}
    if osm:
        nomes["osm"] = osm
    for k, v in outros.items():
        if isinstance(v, str) and v.strip() and not provisorio(v):
            nomes[k] = [v]
    classes: list[list[str]] = []
    for k in PRIORIDADE:
        if k not in nomes:
            continue
        for c in classes:
            if any(mesmo_nome(a, b) for a in nomes[c[0]] for b in nomes[k]) or (
                    c[0] == "osm" and refs and codigo_ref(nomes[k][0]) in refs):
                c.append(k)
                break
        else:
            classes.append([k])
    n = len(nomes)
    maior = max((len(c) for c in classes), default=0)
    if n == 0:
        rot = "nenhuma"
    elif n == 1:
        rot = "unica"
    elif len(classes) == 1:
        rot = "todas"
    elif maior >= 2 and maior > n / 2:
        rot = "maioria"
    else:
        rot = "divergem"
    return {"n_fontes_com_nome": n, "n_fontes_concordantes": maior, "concordancia": rot}


def variantes_osm(r) -> list[str]:
    out = []
    for c in ("name", "alt_name", "official_name"):
        v = r[c]
        if isinstance(v, str) and v.strip():
            out += [x.strip() for x in v.split(";") if x.strip()]
    return out


def refs_osm(r) -> list[str]:
    return [x.strip() for x in r["ref"].split(";")] if isinstance(r["ref"], str) else []


def norm(s):
    return nome_normalizado(s) if isinstance(s, str) and s.strip() else None


# ---------------------------------------------------------------------------
# montagem
# ---------------------------------------------------------------------------
def montar(codigo: str) -> tuple[gpd.GeoDataFrame, dict]:
    _, g_mun, g_urb, bai = base()
    vias = carregar_vias().reset_index(drop=True)
    gf = PASTA_VIARIO / f"faces-logradouros_ibge-censo_2022_{codigo}_vetorial.gpkg"
    for arq in (gf, PROPOSTAS, VIAS):
        if not arq.exists():
            raise SystemExit(f"produto ausente: {arq.relative_to(RAIZ)} (rode as rodadas 66/68 antes)")
    faces = gpd.read_file(gf, layer="faces")
    cn = carregar_cnefe(g_urb, bai)

    # mesmos recortes da rodada 68: urbano = veiculares + pedestres com trecho urbano; rural = veiculares com trecho rural
    urb = vias[(vias["comprimento_urbano_m"] > 0) & vias["highway"].isin(URBANAS)]
    rur = vias[(vias["comprimento_rural_m"] > 0) & vias["highway"].isin(VEICULARES)]
    logger.info("Faces alinhadas: %d vias", len(vias))
    face_v = nome_face_por_via(vias, faces)
    logger.info("CNEFE urbano (%d vias) e rural (%d vias)", len(urb), len(rur))
    cn_u = cnefe_por_via(urb, cn[cn["zona"] == "urbano"])
    cn_r = cnefe_rural_por_via(rur, cn[cn["zona"] == "rural"])
    logger.info("Rótulos do Plano")
    pl = plano_por_via(urb)

    t = vias[["osm_id", "highway", "zona", "comprimento_m", "comprimento_urbano_m", "comprimento_rural_m", "grupo",
              *ATRIB_FISICOS, "geometry"]].copy()
    t["nome_osm"], t["nome_alt_osm"], t["nome_oficial_osm"], t["ref_osm"] = (
        vias["name"], vias["alt_name"], vias["official_name"], vias["ref"])

    # CNEFE: regra urbana primeiro; a rural só onde a urbana não deu nome
    u, r = cn_u.reindex(t["osm_id"]), cn_r.reindex(t["osm_id"])
    usa_rural = u["nome_cnefe"].isna().values & r["nome_cnefe"].notna().values
    sem_urb = u["cnefe_n_enderecos"].isna().values
    pega = usa_rural | sem_urb
    t["nome_cnefe"] = np.where(pega, r["nome_cnefe"].values, u["nome_cnefe"].values)
    n_end = np.where(pega, r["cnefe_n_enderecos"].values, u["cnefe_n_enderecos"].values)
    pct_dom = np.where(pega, r["cnefe_pct_dominante"].values, u["cnefe_pct_dominante"].values)
    n_end = pd.Series(n_end, index=t.index).fillna(0).astype(int)
    t["cnefe_regra"] = np.where(n_end == 0, None, np.where(pega, "rural ≤ 100 m", "urbana ≤ 30 m"))
    t["cnefe_n_enderecos"] = n_end.map(suprimir).astype("Int64")
    t["cnefe_pct_dominante"] = pd.Series(pct_dom, index=t.index).where(n_end >= MIN_CELULA)
    t["cnefe_provisorio"] = t["nome_cnefe"].map(lambda s: SIM_NAO[provisorio(s)] if isinstance(s, str) else None)
    ref_u = t["nome_cnefe"].map(lambda s: codigo_ref(s) if isinstance(s, str) else None)
    t["ref_cnefe"] = r["ref_cnefe"].values
    t["ref_cnefe"] = t["ref_cnefe"].where(t["ref_cnefe"].notna(), ref_u)

    f = face_v.reindex(t["osm_id"])
    t["nome_face_ibge"] = f["nome_face_ibge"].values
    t["face_pct_cobertura"] = f["face_cobertura_pct"].values
    t["face_pct_dominancia"] = f["face_dominancia_pct"].values
    t["face_provisorio"] = t["nome_face_ibge"].map(lambda s: SIM_NAO[provisorio(s)] if isinstance(s, str) else None)

    p = pl.reindex(t["osm_id"])
    t["nome_plano"] = p["nome_plano"].values
    t["plano_dist_m"] = p["d"].round(1).values
    t["plano_n_rotulos"] = p["plano_n_rotulos"].astype("Int64").values
    t["plano_nao_oficial"] = "sim"

    for c, o in [("nome_osm_norm", "nome_osm"), ("nome_cnefe_norm", "nome_cnefe"), ("nome_face_norm", "nome_face_ibge"),
                 ("nome_plano_norm", "nome_plano")]:
        t[c] = t[o].map(norm)

    conc = pd.DataFrame([concordancia(variantes_osm(v), refs_osm(v), {"face": a, "cnefe": b, "plano": c})
                         for (_, v), a, b, c in zip(vias.iterrows(), t["nome_face_ibge"], t["nome_cnefe"], t["nome_plano"])],
                        index=t.index)
    t = pd.concat([t, conc], axis=1)

    # propostas da rodada 68 (lidas do produto, não recalculadas)
    pn = gpd.read_file(PROPOSTAS, layer="vias_nome_proposto").drop(columns="geometry")
    pn = pn.merge(pd.DataFrame({"osm_id": vias["osm_id"]}), on="osm_id")
    # a via com dois rótulos divergentes do Plano aparece duas vezes: vale a linha cujo nome do Plano é o atribuído aqui
    pn["_igual_plano"] = [isinstance(a, str) and a == b for a, b in zip(pn["nome_plano"], pn["osm_id"].map(pl["nome_plano"]))]
    pn = pn.sort_values(["osm_id", "_igual_plano"], ascending=[True, False]).drop_duplicates("osm_id").set_index("osm_id")
    q = pn.reindex(t["osm_id"])
    t["caso_proposta"] = q["caso"].values
    t["caso_divergencia_plano_osm"] = q["situacao_divergencia"].map(SITUACAO).values
    t["nome_proposto"] = q["nome_proposto"].values
    t["fonte_proposta"] = q["fonte_proposta"].values
    t["confianca"] = q["confianca"].values
    t["proposto_repete_ref"] = q["proposto_repete_ref"].map(lambda v: SIM_NAO[bool(v)] if pd.notna(v) else None).values
    t["via_paralela"] = q["via_osm_vizinha_mesmo_nome"].map(lambda v: "sim" if v == "paralela" else "não").where(
        q["caso"].eq("sem_nome")).values
    rr = gpd.read_file(PROPOSTAS, layer="vias_rurais_ref_proposto").drop(columns="geometry").set_index("osm_id").reindex(t["osm_id"])
    t["ref_proposto"] = rr["ref_proposto"].values
    t["old_ref_proposto"] = rr["old_ref_proposto"].values
    t["ref_confianca"] = rr["confianca"].where(rr["proposta"].isin(["ref", "old_ref"])).values

    # revisão: vazias até o responsável decidir
    for c in ("decisao", "nome_final", "fonte_nome_final", "obs"):
        t[c] = pd.Series([None] * len(t), dtype="object")

    ordem = (["osm_id", "highway", "zona", "comprimento_m", "comprimento_urbano_m", "comprimento_rural_m", "grupo", *ATRIB_FISICOS,
              "nome_osm", "nome_alt_osm", "nome_oficial_osm", "ref_osm",
              "nome_cnefe", "cnefe_n_enderecos", "cnefe_pct_dominante", "cnefe_provisorio", "cnefe_regra", "ref_cnefe",
              "nome_face_ibge", "face_pct_cobertura", "face_pct_dominancia", "face_provisorio",
              "nome_plano", "plano_dist_m", "plano_n_rotulos", "plano_nao_oficial",
              "nome_osm_norm", "nome_cnefe_norm", "nome_face_norm", "nome_plano_norm",
              "n_fontes_com_nome", "n_fontes_concordantes", "concordancia", "caso_divergencia_plano_osm",
              "caso_proposta", "nome_proposto", "fonte_proposta", "confianca", "proposto_repete_ref", "via_paralela",
              "ref_proposto", "old_ref_proposto", "ref_confianca",
              "decisao", "nome_final", "fonte_nome_final", "obs", "geometry"])
    g = gpd.GeoDataFrame(t[ordem], geometry="geometry", crs=CRS_PADRAO)

    # conferências
    orig = vias.set_index("osm_id")
    gi = g.set_index("osm_id")
    iguais = {c_n: bool(gi[c_n].equals(orig[c_o].reindex(gi.index))) for c_n, c_o in
              [("nome_osm", "name"), ("ref_osm", "ref"), ("nome_alt_osm", "alt_name"), ("nome_oficial_osm", "official_name"),
               *[(c, c) for c in ATRIB_FISICOS + ["highway", "zona", "comprimento_m"]]]}
    iguais["geometria"] = bool(gi.geometry.geom_equals_exact(orig.geometry.reindex(gi.index), 0).all())
    # recalcula a decisão da rodada 68 com as colunas novas, para as vias que têm proposta (só conferência)
    sub = g[g["caso_proposta"].notna()]
    rec = [decidir({"osm": a, "face": b, "cnefe": c, "plano": d})["nome_proposto"]
           for a, b, c, d in zip(sub["nome_osm"], sub["nome_face_ibge"], sub["nome_cnefe"], sub["nome_plano"])]
    difere = int(sum(not (x == y or (pd.isna(x) and pd.isna(y))) for x, y in zip(rec, sub["nome_proposto"])))
    sem = g[g["caso_proposta"] == "sem_nome"]
    res = {"vias_n": len(g), "osm_id_unico": bool(g["osm_id"].is_unique), "campos_originais_iguais": iguais,
           "n_fontes_com_nome": g["n_fontes_com_nome"].value_counts().sort_index().to_dict(),
           "concordancia": g["concordancia"].value_counts().to_dict(),
           "concordancia_urbano": g.loc[g["zona"] == "urbano", "concordancia"].value_counts().to_dict(),
           "fontes_com_nome_n": {c: int(g[c].notna().sum()) for c in ("nome_osm", "nome_cnefe", "nome_face_ibge", "nome_plano")},
           "fontes_com_nome_provisorio_n": {"nome_cnefe": int((g["cnefe_provisorio"] == "sim").sum()),
                                            "nome_face_ibge": int((g["face_provisorio"] == "sim").sum())},
           "cnefe_regra": g["cnefe_regra"].value_counts().to_dict(),
           "ref_cnefe_n": int(g["ref_cnefe"].notna().sum()),
           "proposta_sem_nome_por_confianca": sem["confianca"].value_counts().to_dict(),
           "proposta_divergencia_por_confianca": g.loc[g["caso_proposta"] == "divergencia_plano_x_osm", "confianca"].value_counts().to_dict(),
           "caso_divergencia_plano_osm": g["caso_divergencia_plano_osm"].value_counts().to_dict(),
           "ref_proposto_n": int(g["ref_proposto"].notna().sum()),
           "ref_proposto_ur_n": int(g["ref_proposto"].fillna("").str.startswith("UR-").sum()),
           "ref_por_confianca": g.loc[g["ref_proposto"].notna(), "ref_confianca"].value_counts().to_dict(),
           "old_ref_proposto_n": int(g["old_ref_proposto"].notna().sum()),
           "decisao_r68_recalculada_difere_n": difere,
           "plano_vias_com_rotulo_n": int(g["nome_plano"].notna().sum()),
           "plano_vias_com_rotulos_divergentes_n": int(pl["plano_rotulos_divergentes"].sum())}
    return g, res


COLUNAS_DOC = {
    "osm_id": ("OSM", "id real do way (camada vias, rodada 66)"),
    "highway, zona, comprimento_m, comprimento_urbano_m, comprimento_rural_m, grupo": ("OSM / rodada 66", "copiados da camada vias, sem alteração"),
    "surface … lit (atributos físicos)": ("OSM", "copiados da camada vias, sem alteração"),
    "nome_osm, nome_alt_osm, nome_oficial_osm, ref_osm": ("OSM", "name, alt_name, official_name, ref — sem alteração"),
    "nome_cnefe": ("IBGE CNEFE 2022 (agregado)", "tipo + título + nome do logradouro dominante, como no CNEFE; urbano: endereço → via mais "
                   "próxima a ≤ 30 m; rural (só se a urbana não deu nome): via veicular rural mais próxima a ≤ 100 m; aceito com ≥ 60% de ≥ 5 endereços"),
    "cnefe_n_enderecos": ("IBGE CNEFE 2022", "endereços atribuídos à via pela regra usada; 1 a 4 suprimidos (nulo)"),
    "cnefe_pct_dominante": ("IBGE CNEFE 2022", "% do logradouro dominante; nulo com menos de 5 endereços"),
    "cnefe_provisorio": ("derivado", "sim = nome do recenseamento (SEM DENOMINACAO, SETOR nn, código de setor)"),
    "cnefe_regra": ("derivado", "regra que gerou nome_cnefe e a contagem: urbana ≤ 30 m ou rural ≤ 100 m"),
    "ref_cnefe": ("IBGE CNEFE 2022", "código UR/BR/RS/RSC/ERS/VRS nnn: rural = código dominante com ≥ 5 endereços e ≥ 60% (regra 2B); "
                  "urbano = código contido em nome_cnefe"),
    "nome_face_ibge": ("IBGE Faces de Logradouros 2022", "nome da face alinhada dominante (≤ 25 m, ≤ 20°, amostras a cada 10 m); aceito com "
                       "≥ 50% de cobertura e ≥ 60% de dominância"),
    "face_pct_cobertura, face_pct_dominancia": ("derivado", "% do comprimento da via com face alinhada; % da face dominante"),
    "face_provisorio": ("derivado", "sim = nome provisório do recenseamento"),
    "nome_plano": ("Plano Diretor 2013 (Mapa 13, extraído de PDF)", "rótulo a ≤ 100 m com eixo alinhado ≤ 30°; vale o mais próximo; "
                   "só vias com trecho urbano"),
    "plano_dist_m, plano_n_rotulos": ("derivado", "distância do rótulo atribuído; rótulos atribuídos à via"),
    "plano_nao_oficial": ("—", "sempre 'sim': os nomes do Plano não são denominação oficial"),
    "nome_*_norm": ("derivado", "nome normalizado (maiúsculas, sem acento, abreviações expandidas) — só para comparar"),
    "n_fontes_com_nome": ("derivado", "fontes (OSM, CNEFE, face, Plano) com nome definitivo; provisório não conta"),
    "n_fontes_concordantes": ("derivado", "tamanho do maior grupo de fontes com o mesmo nome (OSM vale por name/alt_name/official_name; "
                              "nome que é só código de estrada concorda com o OSM se o código está no ref)"),
    "concordancia": ("derivado", "todas (≥ 2 fontes, um nome) / maioria (maior grupo ≥ 2 e > metade) / divergem / unica / nenhuma"),
    "caso_divergencia_plano_osm": ("rodada 68", "nas 60 divergências Plano × OSM: osm_confirmado / plano_confirmado / sem_confirmacao"),
    "caso_proposta": ("rodada 68", "sem_nome (691 vias urbanas sem nome) ou divergencia_plano_x_osm"),
    "nome_proposto, fonte_proposta, confianca": ("rodada 68", "proposta da triangulação (maioria entre nomes definitivos; provisório não vence), "
                                                 "lida do produto, não recalculada; não oficial"),
    "proposto_repete_ref": ("rodada 68", "sim = o nome proposto só repete o ref da via"),
    "via_paralela": ("rodada 68", "sim = corre ao lado de via que já tem o nome (acesso/marginal): confiança rebaixada"),
    "ref_proposto, old_ref_proposto, ref_confianca": ("rodada 68 (2B)", "ref UR nnn proposto para via rural sem ref e sem nome; traçado antigo → old_ref"),
    "decisao, nome_final, fonte_nome_final, obs": ("revisão", "vazias; decisao = aceito / rejeitado / ajustar / sem_decisao"),
}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--codigo-ibge", default="4322400")
    ap.add_argument("--forcar", action="store_true", help="refaz a camada mesmo que já exista")
    a = ap.parse_args()
    cod_area = json.loads((RAIZ / "config/area_estudo.geojson").read_text(encoding="utf-8"))["features"][0]["properties"].get("codarea")
    if cod_area and str(cod_area) != a.codigo_ibge:
        raise SystemExit(f"--codigo-ibge {a.codigo_ibge} difere da área de estudo ({cod_area})")
    if SAIDA.exists() and not a.forcar:
        logger.info("%s já existe — use --forcar", SAIDA.name)
        print(json.dumps(json.loads(SAIDA.with_suffix(".json").read_text(encoding="utf-8"))["resumo"], ensure_ascii=False, indent=1))
        return
    try:
        g, res = montar(a.codigo_ibge)
        PASTA_VIARIO.mkdir(parents=True, exist_ok=True)
        SAIDA.unlink(missing_ok=True)
        g.to_file(SAIDA, layer="viario_referencia", driver="GPKG")
    except (OSError, ValueError) as e:
        logger.error("Falha: %s", e)
        raise SystemExit(1)
    meta = {"arquivo": SAIDA.name, "camada": "viario_referencia", "data_processamento": datetime.now(timezone.utc).isoformat(),
            "script": "scripts/processamento/viario_referencia.py", "codigo_ibge": a.codigo_ibge, "crs": CRS_PADRAO,
            "fonte": "derivado: viário OSM (Geofabrik 2026-10-20, camada vias) × CNEFE 2022 (agregado) × faces de logradouros IBGE 2022 × "
                     "toponímia do Plano Diretor 2013 (extraída de PDF) × propostas da rodada 68",
            "licenca": "ODbL 1.0 (deriva do OpenStreetMap); demais fontes públicas",
            "atribuicao": "© contribuidores do OpenStreetMap; IBGE",
            "uso": "referência para revisão: uma coluna por fonte, nada harmonizado nem sobrescrito; nomes do Plano e propostas são não oficiais",
            "transformacao_aplicada": "uma linha por via da camada vias; colunas de cada fonte lado a lado; regras de dominância e de nome "
                                      "provisório da rodada 68 aplicadas a todas as vias; propostas lidas de " + PROPOSTAS.name,
            "privacidade": "nenhum ponto, coordenada, número ou lista de endereços; contagens de 1 a 4 suprimidas",
            "colunas": {k: {"fonte": f, "regra": r} for k, (f, r) in COLUNAS_DOC.items()},
            "tamanho_mb": round(SAIDA.stat().st_size / 1e6, 2), "resumo": res}
    SAIDA.with_suffix(".json").write_text(json.dumps(meta, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(res, ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    main()
