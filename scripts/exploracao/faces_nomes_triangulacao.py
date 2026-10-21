"""
Faces de logradouros do IBGE (Censo 2022) e nomes de vias por triangulação de
fontes (rodada 68). Proposta para revisão: nada altera a camada `vias` do
viário OSM nem o portal.

Etapas (subcomandos):
    faces    baixa o arquivo de faces da UF (só se faltar, ou com --forcar),
             filtra o município, descreve e compara com o viário OSM
             (distância ≤ 25 m e direção ≤ 20°, amostras a cada 10 m).
    nomes    triangulação para as vias urbanas OSM sem nome e para as
             divergências Plano × OSM da rodada 67: nome OSM, logradouro
             dominante do CNEFE, nome da face do IBGE e rótulo do Plano.
    rurais   ref proposto (UR nnn, BR/RS) para vias rurais sem ref e sem nome,
             pelo logradouro dominante dos endereços do CNEFE.
    todos    as três, nessa ordem.

Regra de decisão (nomes): maioria entre os nomes definitivos; nome provisório
do recenseamento (SEM DENOMINACAO, RUA H SETOR…, RUA nn + código de setor)
nunca vence nome definitivo e, sozinho, não gera proposta.

Privacidade (CNEFE): só agregados (nomes de logradouro e contagens; contagens
de 1 a 4 suprimidas). Nenhum ponto, coordenada, número ou lista de endereços
vai para as saídas; os pontos só servem a junções internas.

Uso:
    python scripts/exploracao/faces_nomes_triangulacao.py todos --codigo-ibge 4322400 --saida-tabelas <pasta>
"""

import argparse
import hashlib
import json
import logging
import re
import sys
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import requests
import shapely

RAIZ = Path(__file__).resolve().parents[2]
sys.path.append(str(RAIZ / "scripts" / "utils"))
sys.path.append(str(RAIZ / "scripts" / "exploracao"))
from recorte_municipio import CRS_PADRAO  # noqa: E402
from osm_levantamento import REF, VEICULARES, compat_iniciais, nome_normalizado, nome_nucleo, norm_txt, pct  # noqa: E402
from fontes_complementares import (  # noqa: E402
    FACES_URL, MIN_CELULA, UA, base, carregar_cnefe, carregar_vias, suprimir,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

PASTA_FACES = RAIZ / "data" / "raw" / "faces_logradouros_ibge_2022"
PASTA_VIARIO = RAIZ / "data" / "processed" / "viario"
DATA_PRODUTO = "2026-10-21"
DIST_M, DANG_GRAUS, PASSO_M = 25.0, 20.0, 10.0  # face "equivalente": perto, alinhada; amostragem das linhas
RE_PROVISORIO = re.compile(r"SEM DENOMINA|SEM NOME|SEM SAIDA|\bSETOR \d|PROJETAD|\d{5,}|\b43 22400\b")
RE_COD_SETOR = re.compile(r"\bSETOR \d+\b|\b\d{5,}\b|\b43 22400\b(?: \d+)*")
RE_CODIGO = re.compile(r"\b(UR|BR|RS|RSC|ERS|VRS)[ -]?(\d{2,3})\b")
URBANAS = VEICULARES | {"pedestrian", "footway", "path"}  # mesmo recorte da rodada 67 (102,8 km sem nome)


def provisorio(nome) -> bool:
    return isinstance(nome, str) and bool(RE_PROVISORIO.search(norm_txt(nome)))


def sidecar(caminho: Path, **campos) -> None:
    meta = {"arquivo": caminho.name, "data_processamento": datetime.now(timezone.utc).isoformat(),
            "script": "scripts/exploracao/faces_nomes_triangulacao.py"}
    meta.update(campos)
    if caminho.is_file():
        meta["tamanho_mb"] = round(caminho.stat().st_size / 1e6, 2)
    destino = caminho.with_name(caminho.name + ".json") if caminho.suffix in (".zip", ".csv") else caminho.with_suffix(".json")
    destino.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# geometria: segmentos, amostras e pares alinhados
# ---------------------------------------------------------------------------
def segmentos(geoms: gpd.GeoSeries) -> pd.DataFrame:
    """Cada par de vértices consecutivos vira um segmento, com o índice da feição de origem e a direção (0–180°)."""
    linhas = geoms.explode(index_parts=False)
    xy, idx = shapely.get_coordinates(linhas.values, return_index=True)
    orig = linhas.index.values[idx]
    mesmo = idx[1:] == idx[:-1]
    s = pd.DataFrame({"i": orig[:-1][mesmo], "x0": xy[:-1, 0][mesmo], "y0": xy[:-1, 1][mesmo],
                      "x1": xy[1:, 0][mesmo], "y1": xy[1:, 1][mesmo]})
    s["L"] = np.hypot(s["x1"] - s["x0"], s["y1"] - s["y0"])
    s = s[s["L"] > 0].reset_index(drop=True)
    s["ang"] = np.degrees(np.arctan2(s["y1"] - s["y0"], s["x1"] - s["x0"])) % 180
    return s


def amostras(seg: pd.DataFrame, passo: float = PASSO_M) -> pd.DataFrame:
    """Pontos no centro de pedaços de até `passo` m; peso = comprimento representado."""
    n = np.maximum(1, np.ceil(seg["L"] / passo)).astype(int)
    r = seg.loc[seg.index.repeat(n)].copy()
    k = r.groupby(level=0).cumcount().values
    t = (k + 0.5) / np.repeat(n.values, n.values)
    r["x"] = r["x0"] + t * (r["x1"] - r["x0"])
    r["y"] = r["y0"] + t * (r["y1"] - r["y0"])
    r["peso"] = r["L"] / np.repeat(n.values, n.values)
    return r[["i", "x", "y", "ang", "peso"]].reset_index(drop=True)


def casar_alinhado(amo: pd.DataFrame, seg_alvo: pd.DataFrame) -> pd.DataFrame:
    """Para cada amostra, o segmento-alvo mais próximo a ≤ DIST_M com diferença de direção ≤ DANG_GRAUS."""
    linhas = shapely.linestrings(np.stack([seg_alvo[["x0", "y0"]].values, seg_alvo[["x1", "y1"]].values], axis=1))
    pts = shapely.points(amo["x"].values, amo["y"].values)
    ip, iseg = shapely.STRtree(linhas).query(pts, predicate="dwithin", distance=DIST_M)
    d = shapely.distance(pts[ip], linhas[iseg])
    da = np.abs(amo["ang"].values[ip] - seg_alvo["ang"].values[iseg]) % 180
    da = np.minimum(da, 180 - da)
    p = pd.DataFrame({"amostra": ip, "alvo": seg_alvo["i"].values[iseg], "d": d, "dang": da})
    p = p[p["dang"] <= DANG_GRAUS].sort_values("d").drop_duplicates("amostra")
    return p.set_index("amostra")


# ---------------------------------------------------------------------------
# nomes
# ---------------------------------------------------------------------------
NUM_UNID = {"UM": 1, "UMA": 1, "DOIS": 2, "TRES": 3, "QUATRO": 4, "CINCO": 5, "SEIS": 6, "SETE": 7, "OITO": 8, "NOVE": 9}
NUM_DEZ = {"DEZ": 10, "ONZE": 11, "DOZE": 12, "TREZE": 13, "QUATORZE": 14, "CATORZE": 14, "QUINZE": 15, "DEZESSEIS": 16,
           "DEZESSETE": 17, "DEZOITO": 18, "DEZENOVE": 19}
NUM_DEZENAS = {"VINTE": 20, "TRINTA": 30, "QUARENTA": 40, "CINQUENTA": 50}


def chave_nome(s: str) -> str:
    """Núcleo do nome com números por extenso em algarismos (TREZE DE MAIO → 13 MAIO; VINTE E OITO → 28),
    sem o código de setor dos nomes provisórios (RUA 8 SETOR 05000102 → 8)."""
    base_nome = RE_COD_SETOR.sub(" ", norm_txt(s))
    toks, out, i = nome_nucleo(base_nome).split(), [], 0
    while i < len(toks):
        t = toks[i]
        if t in NUM_DEZENAS:
            v = NUM_DEZENAS[t]
            if i + 1 < len(toks) and toks[i + 1] in NUM_UNID:
                v += NUM_UNID[toks[i + 1]]
                i += 1
            out.append(str(v))
        elif t in NUM_DEZ:
            out.append(str(NUM_DEZ[t]))
        elif t in NUM_UNID:
            out.append(str(NUM_UNID[t]))
        else:
            out.append(t)
        i += 1
    return " ".join(out)


def nivel_nome(a: str, b: str) -> str:
    """exato / normalizado / aproximado / diverge. Números têm de coincidir (RUA 10 ≠ RUA 14).
    Aproximado inclui: núcleo igual (com números por extenso), iniciais, semelhança ≥ 0,88 e nome contido no
    outro (MARECHAL FLORIANO × MARECHAL FLORIANO PEIXOTO) quando o menor tem palavra de ≥ 5 letras e o maior
    acrescenta no máximo duas palavras."""
    if a.upper().strip() == b.upper().strip():
        return "exato"
    if nome_normalizado(a) == nome_normalizado(b):
        return "normalizado"
    na, nb = chave_nome(a), chave_nome(b)
    if sorted(re.findall(r"\d+", na)) != sorted(re.findall(r"\d+", nb)):
        return "diverge"
    if na and (na == nb or compat_iniciais(na, nb) or SequenceMatcher(None, na, nb).ratio() >= 0.88):
        return "aproximado"
    ta, tb = set(na.split()), set(nb.split())
    menor, maior = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    if menor and menor <= maior and any(len(t) >= 5 for t in menor) and len(maior - menor) <= 2:
        return "aproximado"
    return "diverge"


def mesmo_nome(a, b) -> bool:
    return isinstance(a, str) and isinstance(b, str) and nivel_nome(a, b) != "diverge"


def nome_face(r) -> str | None:
    s = " ".join(x for x in (r["NM_TIP_LOG"], r["NM_TIT_LOG"], r["NM_LOG"]) if isinstance(x, str) and x.strip())
    return s or None


# ---------------------------------------------------------------------------
# PARTE 1 — faces de logradouros
# ---------------------------------------------------------------------------
def baixar_faces(uf: str, forcar: bool) -> Path:
    PASTA_FACES.mkdir(parents=True, exist_ok=True)
    nome = f"{uf}_faces_de_logradouros_2022_shp.zip"
    arq, url = PASTA_FACES / nome, FACES_URL + "shp/" + nome
    if arq.exists() and not forcar:
        logger.info("Faces %s já baixadas — use --forcar", nome)
    else:
        logger.info("Baixando %s", url)
        with requests.get(url, headers={"User-Agent": UA}, timeout=600, stream=True) as r:
            r.raise_for_status()
            with open(arq, "wb") as f:
                for bloco in r.iter_content(1 << 20):
                    f.write(bloco)
    sha = hashlib.sha256(arq.read_bytes()).hexdigest()
    sidecar(arq, fonte="IBGE — Base de Faces de Logradouros, versão Censo Demográfico 2022", url=url,
            data_acesso=DATA_PRODUTO, sha256=sha, tamanho_bytes=arq.stat().st_size,
            licenca="dados públicos do IBGE (uso livre com citação da fonte)",
            atribuicao="Fonte: IBGE, Censo Demográfico 2022 — Faces de Logradouros",
            conteudo="um shapefile por município da UF (RS/<código>_faces_de_logradouros_2022.shp), EPSG:4674",
            transformacao_aplicada="nenhuma (arquivo como baixado)")
    return arq


def carregar_faces(arq: Path, codigo: str, g_urb) -> gpd.GeoDataFrame:
    shp = f"/vsizip/{arq}/{arq.name[:2]}/{codigo}_faces_de_logradouros_2022.shp"
    f = gpd.read_file(shp).to_crs(CRS_PADRAO)  # SIRGAS 2000 geográfico → UTM 21S
    f["nome_face"] = f.apply(nome_face, axis=1)
    f["nome_provisorio"] = f["nome_face"].map(provisorio)
    f["zona"] = np.where(f.geometry.interpolate(0.5, normalized=True).within(g_urb), "urbano", "rural")
    f["comprimento_m"] = f.length.round(1)
    return f.reset_index(drop=True)


def parte_faces(saida: Path, codigo: str, forcar: bool) -> dict:
    tab = saida / "faces_ibge"
    tab.mkdir(parents=True, exist_ok=True)
    _, g_mun, g_urb, _ = base()
    arq = baixar_faces("RS", forcar)
    f = carregar_faces(arq, codigo, g_urb)
    vias = carregar_vias()

    # descrição
    attrs = []
    for c in ["CD_SETOR", "CD_QUADRA", "CD_FACE", "NM_TIP_LOG", "NM_TIT_LOG", "NM_LOG", "TOT_RES", "TOT_GERAL"]:
        attrs.append({"atributo": c, "n_preenchido": int(f[c].notna().sum()), "pct_preenchido": pct(f[c].notna().sum(), len(f)),
                      "n_distintos": int(f[c].nunique())})
    pd.DataFrame(attrs).to_csv(tab / "F1_atributos.csv", index=False)
    pd.DataFrame(f["NM_TIP_LOG"].fillna("(vazio)").value_counts()).reset_index().rename(columns={"count": "n_faces"}).assign(
        km=lambda d: d["NM_TIP_LOG"].map(f.assign(t=f["NM_TIP_LOG"].fillna("(vazio)")).groupby("t")["comprimento_m"].sum() / 1000).round(1)
    ).to_csv(tab / "F1_faces_por_tipo.csv", index=False)
    zonas = []
    for z, sub in [("todas", f), ("urbano", f[f["zona"] == "urbano"]), ("rural", f[f["zona"] == "rural"])]:
        com = sub["nome_face"].notna()
        zonas.append({"zona": z, "n_faces": len(sub), "km": round(sub["comprimento_m"].sum() / 1000, 1),
                      "pct_com_nome": pct(com.sum(), len(sub)), "pct_com_nome_definitivo": pct((com & ~sub["nome_provisorio"]).sum(), len(sub)),
                      "pct_km_com_nome": pct(sub.loc[com, "comprimento_m"].sum(), sub["comprimento_m"].sum()),
                      "n_logradouros_distintos": int(sub["nome_face"].nunique()), "n_setores": int(sub["CD_SETOR"].nunique()),
                      "n_quadras": int(sub.dropna(subset=["CD_QUADRA"]).groupby(["CD_SETOR", "CD_QUADRA"]).ngroups)})
    pd.DataFrame(zonas).to_csv(tab / "F1_resumo_por_zona.csv", index=False)

    # OSM → faces: amostras das vias e face alinhada mais próxima
    seg_f = segmentos(f.geometry)
    vias = vias.reset_index(drop=True)
    seg_v = segmentos(vias.geometry)
    amo = amostras(seg_v)
    amo["urbano"] = shapely.within(shapely.points(amo["x"].values, amo["y"].values), g_urb)
    quadras = gpd.read_file(REF["quadras"], layer="quadras").to_crs(CRS_PADRAO)
    casco = quadras.union_all().convex_hull  # mesmo casco conservador da rodada 67
    amo["parte_nova"] = amo["urbano"] & ~shapely.within(shapely.points(amo["x"].values, amo["y"].values), casco)
    par = casar_alinhado(amo, seg_f)
    amo["face"] = par["alvo"].reindex(amo.index)
    amo["tem_face"] = amo["face"].notna()
    amo["osm_id"] = vias["osm_id"].values[amo["i"].values]
    amo["grupo"] = vias["grupo"].values[amo["i"].values]
    amo["nome_osm"] = vias["name"].values[amo["i"].values]
    amo["nome_face"] = f["nome_face"].reindex(amo["face"].values).values

    cob = []
    for rec, m in [("município", np.ones(len(amo), bool)), ("urbano", amo["urbano"].values), ("rural", ~amo["urbano"].values),
                   ("urbano veicular", amo["urbano"].values & (amo["grupo"] == "veicular").values),
                   ("partes novas (urbano fora do casco das quadras do Plano)", amo["parte_nova"].values)]:
        s = amo[m]
        cob.append({"recorte": rec, "km_osm": round(s["peso"].sum() / 1000, 1),
                    "km_osm_com_face": round(s.loc[s["tem_face"], "peso"].sum() / 1000, 1),
                    "pct_km_com_face": pct(s.loc[s["tem_face"], "peso"].sum(), s["peso"].sum())})
    pd.DataFrame(cob).to_csv(tab / "F2_cobertura_osm_por_faces.csv", index=False)

    # concordância de nome, por km (amostras com nome nos dois lados)
    ambos = amo[amo["tem_face"] & amo["nome_osm"].notna() & amo["nome_face"].notna()].copy()
    pares = ambos[["nome_osm", "nome_face"]].drop_duplicates()
    pares["nivel"] = [nivel_nome(a, b) for a, b in zip(pares["nome_osm"], pares["nome_face"])]
    ambos = ambos.merge(pares, on=["nome_osm", "nome_face"], how="left")
    fprov = ambos["nome_face"].map(provisorio)
    ambos.loc[fprov & ambos["nivel"].eq("diverge"), "nivel"] = "diverge (face provisória)"
    ambos.loc[fprov & ambos["nivel"].isin(["exato", "normalizado", "aproximado"]), "nivel"] = "aproximado (face provisória)"
    conc = []
    for rec, s in [("município", ambos), ("urbano", ambos[ambos["urbano"]]), ("rural", ambos[~ambos["urbano"]])]:
        tot = s["peso"].sum()
        acum = 0.0
        for nv in ["exato", "normalizado", "aproximado", "aproximado (face provisória)", "diverge", "diverge (face provisória)"]:
            km = s.loc[s["nivel"] == nv, "peso"].sum()
            acum += km if not nv.startswith("diverge") else 0
            conc.append({"recorte": rec, "nivel": nv, "km": round(km / 1000, 1), "pct_km": pct(km, tot),
                         "pct_km_cumulativo_concorda": pct(acum, tot) if not nv.startswith("diverge") else None})
    pd.DataFrame(conc).to_csv(tab / "F3_concordancia_nome_faces_x_osm.csv", index=False)
    # divergências por via (nome OSM × nome de face dominante na via)
    dv = ambos[ambos["nivel"].str.startswith("diverge")].groupby(["osm_id", "nome_osm", "nome_face"])["peso"].sum().reset_index()
    dv = dv[dv["peso"] >= 30].sort_values("peso", ascending=False).rename(columns={"peso": "m_divergente"})
    dv["m_divergente"] = dv["m_divergente"].round(0)
    dv["face_provisoria"] = dv["nome_face"].map(provisorio)
    dv.to_csv(tab / "F3_divergencias_nome_faces_x_osm_por_via.csv", index=False)

    # faces → OSM: faces sem via OSM alinhada (cobertura < 50% do comprimento)
    amo_f = amostras(seg_f)
    par_f = casar_alinhado(amo_f, seg_v)
    amo_f["tem_via"] = par_f["alvo"].reindex(amo_f.index).notna()
    cobf = amo_f.groupby("i").apply(lambda s: s.loc[s["tem_via"], "peso"].sum() / s["peso"].sum(), include_groups=False)
    f["pct_coberta_por_via_osm"] = (100 * cobf.reindex(f.index).fillna(0)).round(1)
    f["sem_via_osm"] = f["pct_coberta_por_via_osm"] < 50
    sfv = f[f["sem_via_osm"]].assign(tipo=lambda d: d["NM_TIP_LOG"].fillna("(vazio)"))
    sfv.groupby(["zona", "tipo"]).agg(n_faces=("tipo", "size"), km=("comprimento_m", lambda s: round(s.sum() / 1000, 2))).reset_index().sort_values(
        "km", ascending=False).to_csv(tab / "F4_faces_sem_via_osm_por_tipo.csv", index=False)
    # vias OSM sem face (cobertura < 50%), por zona e classe; zona da via = maioria das amostras
    cv = amo.groupby("i").agg(peso=("peso", "sum"), urb=("urbano", "mean"))
    cv["pct_face"] = amo[amo["tem_face"]].groupby("i")["peso"].sum().reindex(cv.index).fillna(0) / cv["peso"]
    vias["pct_com_face"] = (100 * cv["pct_face"].reindex(vias.index).fillna(0)).round(1)
    vias["zona_amostras"] = np.where(cv["urb"].reindex(vias.index).fillna(0) >= 0.5, "urbano", "rural")
    vsf = vias[vias["pct_com_face"] < 50]
    vsf.groupby(["zona_amostras", "highway"]).agg(n_vias=("osm_id", "size"), km=("comprimento_m", lambda s: round(s.sum() / 1000, 1))).reset_index().sort_values(
        ["zona_amostras", "km"], ascending=[False, False]).to_csv(tab / "F4_vias_osm_sem_face_por_classe.csv", index=False)

    # produto: faces do município com a correspondência (derivado, fora do git)
    PASTA_VIARIO.mkdir(parents=True, exist_ok=True)
    gpkg = PASTA_VIARIO / f"faces-logradouros_ibge-censo_2022_{codigo}_vetorial.gpkg"
    gpkg.unlink(missing_ok=True)
    f.to_file(gpkg, layer="faces", driver="GPKG")
    sidecar(gpkg, fonte="IBGE — Base de Faces de Logradouros, Censo 2022 (arquivo da UF)", arquivo_origem=arq.name,
            licenca="dados públicos do IBGE (uso livre com citação da fonte)", crs=CRS_PADRAO, codigo_ibge=codigo,
            transformacao_aplicada=("filtro do shapefile do município dentro do zip da UF; reprojeção EPSG:4674 → EPSG:31981; "
                                    "nome_face = tipo + título + nome; nome_provisorio (SEM DENOMINACAO, SETOR nn, código de setor); "
                                    "zona pelo ponto médio nas áreas urbanizadas IBGE 2019; pct_coberta_por_via_osm = % do "
                                    f"comprimento com via OSM a ≤ {DIST_M:.0f} m e ≤ {DANG_GRAUS:.0f}° (amostras a cada {PASSO_M:.0f} m)"),
            uso="derivado para comparação (rodada 68); não está no portal")

    km_f = f["comprimento_m"].sum() / 1000
    res = {"arquivo": arq.name, "faces_n": len(f), "faces_km": round(km_f, 1),
           "faces_pct_com_nome": pct(f["nome_face"].notna().sum(), len(f)),
           "faces_pct_nome_definitivo": pct((f["nome_face"].notna() & ~f["nome_provisorio"]).sum(), len(f)),
           "faces_pct_provisorio": pct(f["nome_provisorio"].sum(), len(f)),
           "faces_urbano_n": int((f["zona"] == "urbano").sum()), "faces_rural_n": int((f["zona"] == "rural").sum()),
           "zonas": zonas, "cobertura": cob, "concordancia": conc,
           "faces_sem_via_osm_n": int(f["sem_via_osm"].sum()), "faces_sem_via_osm_km": round(f.loc[f["sem_via_osm"], "comprimento_m"].sum() / 1000, 1),
           "vias_osm_sem_face_urbano_n": int(((vias["pct_com_face"] < 50) & (vias["zona_amostras"] == "urbano")).sum()),
           "vias_osm_sem_face_urbano_km": round(vsf.loc[vsf["zona_amostras"] == "urbano", "comprimento_m"].sum() / 1000, 1),
           "divergencias_por_via_n": len(dv), "divergencias_por_via_nao_provisorias_n": int((~dv["face_provisoria"]).sum())}
    (tab / "F0_resumo_faces.json").write_text(json.dumps(res, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return res


# ---------------------------------------------------------------------------
# PARTE 2 — triangulação de nomes
# ---------------------------------------------------------------------------
PRIORIDADE = ["osm", "face", "cnefe", "plano"]  # forma do nome proposto e desempate (fontes oficiais antes do Plano)


def decidir(fontes: dict) -> dict:
    """Maioria entre nomes definitivos; provisório nunca vence definitivo e não gera proposta sozinho."""
    defs = {k: v for k, v in fontes.items() if isinstance(v, str) and v.strip() and not provisorio(v)}
    provs = [k for k, v in fontes.items() if isinstance(v, str) and v.strip() and provisorio(v)]
    classes: list[list[str]] = []
    for k in PRIORIDADE:
        if k not in defs:
            continue
        for c in classes:
            if mesmo_nome(defs[c[0]], defs[k]):
                c.append(k)
                break
        else:
            classes.append([k])
    if not classes:
        return {"nome_proposto": None, "fonte_proposta": "só provisório" if provs else "nenhuma", "n_fontes_concordantes": 0,
                "n_fontes_definitivas": 0, "confianca": None, "empate": False}
    classes.sort(key=len, reverse=True)  # sort estável: no empate fica a classe da fonte de maior prioridade
    melhor = classes[0]
    empate = len(classes) > 1 and len(classes[1]) == len(melhor)
    n_conc, dissid = len(melhor), len(defs) - len(melhor)
    if n_conc >= 2 and dissid == 0:
        conf = "alta"
    elif n_conc >= 2 and not empate:
        conf = "média"
    elif n_conc == 1 and dissid == 0 and melhor[0] in ("face", "cnefe"):
        conf = "média"  # uma fonte oficial sozinha, sem contradição
    else:
        conf = "baixa"
    return {"nome_proposto": defs[melhor[0]], "fonte_proposta": "+".join(melhor), "n_fontes_concordantes": n_conc,
            "n_fontes_definitivas": len(defs), "confianca": conf, "empate": empate}


def nome_face_por_via(vias: gpd.GeoDataFrame, faces: gpd.GeoDataFrame) -> pd.DataFrame:
    """Nome de face dominante (por comprimento) ao longo de cada via; aceito com ≥ 50% de cobertura e ≥ 60% de dominância."""
    vias = vias.reset_index(drop=True)
    amo = amostras(segmentos(vias.geometry))
    par = casar_alinhado(amo, segmentos(faces.geometry))
    amo["nome_face"] = faces["nome_face"].reindex(par["alvo"].reindex(amo.index).values).values
    tot = amo.groupby("i")["peso"].sum()
    com = amo[amo["nome_face"].notna()]
    por = com.groupby(["i", "nome_face"])["peso"].sum().reset_index().sort_values("peso", ascending=False).drop_duplicates("i").set_index("i")
    out = pd.DataFrame({"osm_id": vias["osm_id"].values}, index=vias.index)
    out["face_cobertura_pct"] = (100 * com.groupby("i")["peso"].sum().reindex(out.index).fillna(0) / tot.reindex(out.index)).round(1)
    out["face_nome_dominante"] = por["nome_face"].reindex(out.index)
    out["face_dominancia_pct"] = (100 * por["peso"].reindex(out.index) / com.groupby("i")["peso"].sum().reindex(out.index)).round(1)
    out["nome_face_ibge"] = out["face_nome_dominante"].where((out["face_cobertura_pct"] >= 50) & (out["face_dominancia_pct"] >= 60))
    return out.set_index("osm_id")


def cnefe_por_via(vias: gpd.GeoDataFrame, cn: gpd.GeoDataFrame) -> pd.DataFrame:
    """Logradouro dominante dos endereços a ≤ 30 m (cada endereço vai para a via mais próxima); aceito com ≥ 60% de ≥ 5."""
    pts = cn[cn["logradouro"] != ""][["logradouro", "geometry"]]
    near = gpd.sjoin_nearest(pts, vias[["osm_id", "geometry"]], how="inner", max_distance=30)
    near = near[~near.index.duplicated()]
    g = near.groupby("osm_id")["logradouro"]
    out = pd.DataFrame({"cnefe_n_enderecos": g.size(), "cnefe_dominante": g.agg(lambda s: s.value_counts().index[0]),
                        "cnefe_n_dominante": g.agg(lambda s: int(s.value_counts().iloc[0]))})
    out["cnefe_pct_dominante"] = (100 * out["cnefe_n_dominante"] / out["cnefe_n_enderecos"]).round(1)
    ok = (out["cnefe_n_enderecos"] >= MIN_CELULA) & (out["cnefe_pct_dominante"] >= 60)
    out["nome_cnefe"] = out["cnefe_dominante"].where(ok)
    return out


def parte_nomes(saida: Path, codigo: str, tabelas_r67: Path) -> dict:
    rev = saida / "revisao_nomes"
    rev.mkdir(parents=True, exist_ok=True)
    _, g_mun, g_urb, bai = base()
    vias = carregar_vias()
    gf = PASTA_VIARIO / f"faces-logradouros_ibge-censo_2022_{codigo}_vetorial.gpkg"
    if not gf.exists():
        raise SystemExit(f"faces não processadas ({gf.name}): rode a etapa faces antes")
    faces = gpd.read_file(gf, layer="faces")
    cn = carregar_cnefe(g_urb, bai)

    urb = vias[(vias["comprimento_urbano_m"] > 0) & vias["highway"].isin(URBANAS)].copy()
    face_v = nome_face_por_via(urb, faces)
    cn_v = cnefe_por_via(urb, cn[cn["zona"] == "urbano"])
    plano = pd.read_csv(tabelas_r67 / "P5_proposta_nomes_plano_vias_sem_nome.csv").drop_duplicates("osm_id").set_index("osm_id")
    div = pd.read_csv(tabelas_r67 / "P5_divergencias_nome_plano_x_osm.csv")

    def montar(sub: gpd.GeoDataFrame, caso: str, nome_plano: pd.Series) -> gpd.GeoDataFrame:
        t = sub[["osm_id", "highway", "name", "comprimento_urbano_m", "geometry"]].rename(columns={"name": "nome_osm"}).copy()
        t["caso"] = caso
        t["nome_cnefe"] = t["osm_id"].map(cn_v["nome_cnefe"])
        t["cnefe_dominante_qualquer"] = t["osm_id"].map(cn_v["cnefe_dominante"])
        t["cnefe_n_enderecos"] = t["osm_id"].map(cn_v["cnefe_n_enderecos"]).fillna(0).astype(int)
        t["cnefe_pct_dominante"] = t["osm_id"].map(cn_v["cnefe_pct_dominante"])
        t["nome_face_ibge"] = t["osm_id"].map(face_v["nome_face_ibge"])
        t["face_nome_dominante"] = t["osm_id"].map(face_v["face_nome_dominante"])
        t["face_cobertura_pct"] = t["osm_id"].map(face_v["face_cobertura_pct"])
        t["face_dominancia_pct"] = t["osm_id"].map(face_v["face_dominancia_pct"])
        t["nome_plano"] = nome_plano.values
        dec = pd.DataFrame([decidir({"osm": r.nome_osm, "face": r.nome_face_ibge, "cnefe": r.nome_cnefe, "plano": r.nome_plano})
                            for r in t.itertuples()], index=t.index)
        t = pd.concat([t, dec], axis=1)
        # matriz par a par (só onde as duas fontes têm nome)
        for a, b in [("osm", "cnefe"), ("osm", "face_ibge"), ("osm", "plano"), ("cnefe", "face_ibge"), ("cnefe", "plano"), ("face_ibge", "plano")]:
            ca, cb = f"nome_{a}", f"nome_{b}"
            t[f"concorda_{a}_{b}"] = [None if not (isinstance(x, str) and isinstance(y, str)) else mesmo_nome(x, y)
                                      for x, y in zip(t[ca], t[cb])]
        t["km_urbano"] = (t["comprimento_urbano_m"] / 1000).round(3)
        t["status_revisao"] = "pendente"
        return t

    sem = urb[urb["name"].isna()]
    t_sem = montar(sem, "sem_nome", sem["osm_id"].map(plano["nome_proposto"]))
    # salvaguardas: (a) o nome proposto só repete o ref da via (ESTRADA BR 472 × ref BR-472);
    # (b) a via corre ao lado (> 50% do comprimento a ≤ 20 m) de uma via OSM que já tem esse nome (ou ref): acesso/marginal,
    #     não a própria rua → confiança baixa; se só encosta nas pontas, é continuação (informativo).
    t_sem["ref_osm"] = t_sem["osm_id"].map(urb.set_index("osm_id")["ref"])
    t_sem["proposto_repete_ref"] = [isinstance(n, str) and isinstance(r, str) and codigo_ref(n) in [x.strip() for x in r.split(";")]
                                    for n, r in zip(t_sem["nome_proposto"], t_sem["ref_osm"])]
    nomeadas = vias[vias["name"].notna() | vias["ref"].notna()]
    rel = []
    for r in t_sem.itertuples():
        if not isinstance(r.nome_proposto, str):
            rel.append(None)
            continue
        viz = nomeadas.iloc[nomeadas.sindex.query(r.geometry, predicate="dwithin", distance=30)]
        cod = codigo_ref(r.nome_proposto)
        viz = viz[[mesmo_nome(n, r.nome_proposto) or (cod is not None and isinstance(rf, str) and cod in [x.strip() for x in rf.split(";")])
                   for n, rf in zip(viz["name"], viz["ref"])]]
        if viz.empty:
            rel.append(None)
            continue
        frac = r.geometry.intersection(viz.union_all().buffer(20)).length / r.geometry.length
        rel.append("paralela" if frac > 0.5 else "continuação")
    t_sem["via_osm_vizinha_mesmo_nome"] = rel
    t_sem.loc[t_sem["via_osm_vizinha_mesmo_nome"].eq("paralela"), "confianca"] = "baixa"
    dvv = div.merge(urb[["osm_id", "highway", "name", "comprimento_urbano_m", "geometry"]], on="osm_id", how="inner")
    dvv = gpd.GeoDataFrame(dvv, geometry="geometry", crs=urb.crs)
    t_div = montar(dvv, "divergencia_plano_x_osm", dvv["nome_plano"])
    t_div["situacao_divergencia"] = np.select(
        [t_div["fonte_proposta"].str.contains("osm") & (t_div["n_fontes_concordantes"] >= 2) & ~t_div["empate"],
         t_div["fonte_proposta"].str.contains("plano") & (t_div["n_fontes_concordantes"] >= 2) & ~t_div["empate"]],
        ["cnefe/ibge confirmam OSM", "cnefe/ibge confirmam Plano"], "sem confirmação: prefeitura")

    cols = ["osm_id", "caso", "highway", "km_urbano", "nome_osm", "nome_cnefe", "nome_face_ibge", "nome_plano", "nome_proposto",
            "fonte_proposta", "n_fontes_concordantes", "n_fontes_definitivas", "empate", "confianca", "status_revisao",
            "ref_osm", "proposto_repete_ref", "via_osm_vizinha_mesmo_nome",
            "cnefe_dominante_qualquer", "cnefe_pct_dominante", "face_nome_dominante", "face_cobertura_pct", "face_dominancia_pct",
            "concorda_osm_cnefe", "concorda_osm_face_ibge", "concorda_osm_plano", "concorda_cnefe_face_ibge",
            "concorda_cnefe_plano", "concorda_face_ibge_plano"]
    todos = pd.concat([t_sem, t_div], ignore_index=True)
    todos["situacao_divergencia"] = todos.get("situacao_divergencia")
    # contagem de endereços só na versão publicável (1 a 4 suprimidos)
    todos["cnefe_n_enderecos_publicavel"] = todos["cnefe_n_enderecos"].map(suprimir)
    g = gpd.GeoDataFrame(todos[cols + ["situacao_divergencia", "cnefe_n_enderecos_publicavel", "geometry"]], geometry="geometry", crs=urb.crs)
    gpkg = PASTA_VIARIO / f"vias_nome_proposto_{DATA_PRODUTO}.gpkg"
    gpkg.unlink(missing_ok=True)
    g.to_file(gpkg, layer="vias_nome_proposto", driver="GPKG")

    # CSVs de revisão (sem geometria e sem coordenadas)
    ordem = {"alta": 0, "média": 1, "baixa": 2}
    csv = g.drop(columns="geometry").assign(o=lambda d: d["confianca"].map(ordem).fillna(3)).sort_values(["caso", "o", "km_urbano"], ascending=[False, True, False]).drop(columns="o")
    csv[csv["caso"] == "sem_nome"].drop(columns=["situacao_divergencia"]).to_csv(rev / "R1_vias_sem_nome_proposta.csv", index=False)
    csv[csv["caso"] != "sem_nome"].to_csv(rev / "R2_divergencias_plano_x_osm.csv", index=False)
    mat = []
    for caso, s in [("sem_nome", t_sem), ("divergencia_plano_x_osm", t_div)]:
        for c in [c for c in s.columns if c.startswith("concorda_")]:
            v = s[c].dropna().astype(bool)
            mat.append({"caso": caso, "par": c.replace("concorda_", ""), "n_com_os_dois_nomes": len(v), "n_concorda": int(v.sum()),
                        "pct_concorda": pct(v.sum(), len(v)) if len(v) else None,
                        "km_com_os_dois_nomes": round(s.loc[v.index, "km_urbano"].sum(), 2)})
    pd.DataFrame(mat).to_csv(rev / "R0_matriz_concordancia.csv", index=False)

    km_tot = t_sem["km_urbano"].sum()
    prov = t_sem[t_sem["fonte_proposta"] == "só provisório"]
    res = {"vias_sem_nome_n": len(t_sem), "vias_sem_nome_km": round(km_tot, 1),
           "fontes_disponiveis_n": {k: int(t_sem[k].notna().sum()) for k in ("nome_cnefe", "nome_face_ibge", "nome_plano")},
           "fontes_disponiveis_km": {k: round(t_sem.loc[t_sem[k].notna(), "km_urbano"].sum(), 1) for k in ("nome_cnefe", "nome_face_ibge", "nome_plano")},
           "proposta_por_confianca_n": t_sem["confianca"].value_counts().to_dict(),
           "proposta_por_confianca_km": t_sem.groupby("confianca")["km_urbano"].sum().round(1).to_dict(),
           "proposta_por_fonte": t_sem["fonte_proposta"].value_counts().to_dict(),
           "proposta_km_total": round(t_sem.loc[t_sem["nome_proposto"].notna(), "km_urbano"].sum(), 1),
           "proposta_pct_km": pct(t_sem.loc[t_sem["nome_proposto"].notna(), "km_urbano"].sum(), km_tot),
           "proposta_repete_ref_n": int(t_sem["proposto_repete_ref"].sum()),
           "proposta_repete_ref_km": round(t_sem.loc[t_sem["proposto_repete_ref"], "km_urbano"].sum(), 1),
           "proposta_por_confianca_km_sem_repetir_ref": t_sem[~t_sem["proposto_repete_ref"]].groupby("confianca")["km_urbano"].sum().round(1).to_dict(),
           "proposta_por_confianca_n_sem_repetir_ref": t_sem.loc[~t_sem["proposto_repete_ref"], "confianca"].value_counts().to_dict(),
           "vizinha_mesmo_nome": t_sem["via_osm_vizinha_mesmo_nome"].value_counts().to_dict(),
           "so_provisorio_continuam_sem_nome_n": len(prov), "so_provisorio_km": round(prov["km_urbano"].sum(), 1),
           "sem_fonte_n": int((t_sem["fonte_proposta"] == "nenhuma").sum()),
           "sem_fonte_km": round(t_sem.loc[t_sem["fonte_proposta"] == "nenhuma", "km_urbano"].sum(), 1),
           "divergencias_n": len(t_div), "divergencias_situacao": t_div["situacao_divergencia"].value_counts().to_dict(),
           "divergencias_vias_distintas": int(t_div["osm_id"].nunique()),
           "divergencias_plano_generico": int(t_div.merge(div[["osm_id", "nome_plano", "generico_plano"]], on=["osm_id", "nome_plano"])["generico_plano"].sum()),
           "matriz": mat}
    sidecar(gpkg, fonte="derivado: viário OSM (Geofabrik 2026-10-20) × CNEFE 2022 (agregado) × faces de logradouros IBGE 2022 × "
                         "toponímia do Plano Diretor 2013 (extraída de PDF)",
            licenca="ODbL 1.0 (deriva do OpenStreetMap); demais fontes públicas", atribuicao="© contribuidores do OpenStreetMap; IBGE",
            crs=CRS_PADRAO, uso="proposta para revisão (rodada 68): NÃO altera a camada vias nem o portal",
            transformacao_aplicada=("vias urbanas OSM sem nome (highway veicular + pedestrian/footway/path, comprimento urbano > 0) e "
                                    "divergências Plano × OSM da rodada 67; nome_cnefe = logradouro com ≥ 60% de ≥ 5 endereços a ≤ 30 m "
                                    "(endereço → via mais próxima); nome_face_ibge = face alinhada dominante (≤ 25 m, ≤ 20°, ≥ 50% do "
                                    "comprimento, ≥ 60% de dominância); decisão por maioria entre nomes definitivos, provisório não vence; "
                                    "confiança: alta = ≥ 2 concordam e nenhuma diverge; média = ≥ 2 concordam com dissidência ou uma fonte "
                                    "oficial (CNEFE ou face) sozinha; baixa = empate, só Plano ou só OSM contra outras"),
            privacidade="nenhuma coordenada de endereço; contagem de endereços por via com 1 a 4 suprimidos (cnefe_n_enderecos_publicavel)",
            resumo=res)
    (rev / "R_resumo.json").write_text(json.dumps(res, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return res


# ---------------------------------------------------------------------------
# PARTE 2B — estradas rurais UR nnn / BR / RS
# ---------------------------------------------------------------------------
def codigo_ref(logradouro: str) -> str | None:
    m = RE_CODIGO.search(norm_txt(logradouro))
    return f"{m.group(1)}-{int(m.group(2))}" if m else None


def parte_rurais(saida: Path) -> dict:
    rev = saida / "revisao_nomes"
    rev.mkdir(parents=True, exist_ok=True)
    _, g_mun, g_urb, bai = base()
    vias = carregar_vias()
    cn = carregar_cnefe(g_urb, bai)
    rur = vias[(vias["comprimento_rural_m"] > 0) & vias["highway"].isin(VEICULARES)].copy()
    pts = cn[(cn["zona"] == "rural") & (cn["logradouro"] != "")][["logradouro", "geometry"]].copy()
    pts["ref_cnefe"] = pts["logradouro"].map(codigo_ref)
    # endereço rural → via veicular rural mais próxima a ≤ 100 m (casas de campo ficam recuadas da estrada)
    near = gpd.sjoin_nearest(pts, rur[["osm_id", "geometry"]], how="inner", max_distance=100)
    near = near[~near.index.duplicated()]
    g = near.groupby("osm_id")
    est = pd.DataFrame({"n_enderecos": g.size(), "n_com_codigo": g["ref_cnefe"].count()})
    cod = near.dropna(subset=["ref_cnefe"]).groupby("osm_id")["ref_cnefe"]
    est["codigo_cnefe"] = cod.agg(lambda s: s.value_counts().index[0])
    est["n_suporte"] = cod.agg(lambda s: int(s.value_counts().iloc[0]))
    est["logradouro_cnefe"] = near.dropna(subset=["ref_cnefe"]).groupby(["osm_id", "ref_cnefe"])["logradouro"].agg(
        lambda s: s.value_counts().index[0]).reset_index().set_index("osm_id").loc[lambda d: ~d.index.duplicated()]["logradouro"]
    est["pct_suporte"] = (100 * est["n_suporte"] / est["n_enderecos"]).round(1)
    alvo = rur[rur["ref"].isna() & rur["name"].isna()].set_index("osm_id")
    p = est.join(alvo[["highway", "comprimento_rural_m", "geometry"]], how="inner")
    p = p[(p["n_suporte"] >= MIN_CELULA) & (p["pct_suporte"] >= 60)].copy()
    # continuidade: o mesmo ref já existe numa via OSM? distância até ela
    refs = vias.dropna(subset=["ref"]).assign(r=lambda d: d["ref"].str.split(";")).explode("r")
    refs["r"] = refs["r"].str.strip()
    p["ref_existe_no_osm"] = p["codigo_cnefe"].isin(set(refs["r"]))
    p["dist_via_osm_mesmo_ref_m"] = [round(float(refs.loc[refs["r"] == rp].distance(geo).min()), 0) if ok else None
                                     for rp, geo, ok in zip(p["codigo_cnefe"], p["geometry"], p["ref_existe_no_osm"])]
    p["km_rural"] = (p["comprimento_rural_m"] / 1000).round(3)
    # tipo de proposta: código municipal (UR) → ref; traçado antigo → old_ref; rodovia federal/estadual em uso → nada
    # (a pista já tem ref no OSM; os endereços "RODOVIA BR 290" caem em acessos e marginais, que não recebem o ref)
    antigo = p["logradouro_cnefe"].map(lambda s: "ANTIG" in norm_txt(s))
    municipal = p["codigo_cnefe"].str.startswith("UR-")
    p["proposta"] = np.select([antigo, municipal], ["old_ref", "ref"], "nenhuma (acesso/marginal de rodovia)")
    perto = p["dist_via_osm_mesmo_ref_m"].fillna(np.inf) <= 50
    leve = p["highway"].isin(["service", "track"])
    p["confianca"] = np.where(p["proposta"] == "nenhuma (acesso/marginal de rodovia)", None,
                              np.where(perto & ~leve, "alta", np.where(leve, "baixa", "média")))
    p["observacao"] = np.where(leve & (p["proposta"] != "nenhuma (acesso/marginal de rodovia)"),
                               "classe service/track: pode ser acesso de propriedade", "")
    p["ref_proposto"] = p["codigo_cnefe"].where(p["proposta"] == "ref")
    p["old_ref_proposto"] = p["codigo_cnefe"].where(p["proposta"] == "old_ref")
    p["status_revisao"] = "pendente"
    p = p.reset_index().sort_values(["codigo_cnefe", "km_rural"], ascending=[True, False])
    out = p[["osm_id", "highway", "km_rural", "codigo_cnefe", "logradouro_cnefe", "proposta", "ref_proposto", "old_ref_proposto",
             "confianca", "n_suporte", "n_enderecos", "pct_suporte", "ref_existe_no_osm", "dist_via_osm_mesmo_ref_m", "observacao",
             "status_revisao"]].copy()
    out["n_suporte"] = out["n_suporte"].map(suprimir)
    out["n_enderecos"] = out["n_enderecos"].map(suprimir)
    out.rename(columns={"n_suporte": "n_enderecos_suporte", "n_enderecos": "n_enderecos_proximos"}).to_csv(
        rev / "R3_vias_rurais_ref_proposto.csv", index=False)
    gpkg = PASTA_VIARIO / f"vias_nome_proposto_{DATA_PRODUTO}.gpkg"
    gp = gpd.GeoDataFrame(out.rename(columns={"n_suporte": "n_enderecos_suporte", "n_enderecos": "n_enderecos_proximos"}),
                          geometry=p["geometry"].values, crs=CRS_PADRAO)
    gp.to_file(gpkg, layer="vias_rurais_ref_proposto", driver="GPKG", mode="w")
    por_ref = p.groupby(["codigo_cnefe", "proposta"]).agg(n_vias=("osm_id", "size"), km=("km_rural", "sum"), suporte=("n_suporte", "sum"),
                                                          existe_no_osm=("ref_existe_no_osm", "first")).round(2).reset_index()
    por_ref["suporte"] = por_ref["suporte"].map(suprimir)
    por_ref.rename(columns={"suporte": "n_enderecos_suporte"}).to_csv(rev / "R3_resumo_por_ref.csv", index=False)
    # quantos endereços rurais têm código e caem em via que já tem o mesmo ref (validação da regra)
    nr = near.dropna(subset=["ref_cnefe"]).merge(vias[["osm_id", "ref"]], on="osm_id")
    ja = nr[nr["ref"].notna()]
    acerto = ja.apply(lambda r: r["ref_cnefe"] in [x.strip() for x in str(r["ref"]).split(";")], axis=1)
    res = {"enderecos_rurais_com_logradouro": len(pts), "enderecos_rurais_com_codigo": int(pts["ref_cnefe"].notna().sum()),
           "codigos_distintos": int(pts["ref_cnefe"].nunique()), "atribuidos_a_via_100m": len(near),
           "validacao_vias_com_ref_n": len(ja), "validacao_ref_igual_pct": pct(acerto.sum(), len(ja)),
           "vias_com_codigo_dominante_n": len(p), "vias_com_codigo_dominante_km": round(p["km_rural"].sum(), 1),
           "por_proposta_n": p["proposta"].value_counts().to_dict(),
           "por_proposta_km": p.groupby("proposta")["km_rural"].sum().round(1).to_dict(),
           "ref_por_confianca_n": p.loc[p["proposta"] == "ref", "confianca"].value_counts().to_dict(),
           "ref_por_confianca_km": p[p["proposta"] == "ref"].groupby("confianca")["km_rural"].sum().round(1).to_dict(),
           "refs_propostos_distintos": int(p["ref_proposto"].nunique()),
           "refs_propostos_ja_existentes_no_osm": int(p.loc[p["proposta"] == "ref"].drop_duplicates("ref_proposto")["ref_existe_no_osm"].sum()),
           "por_ref": por_ref.to_dict("records")}
    (rev / "R3_resumo.json").write_text(json.dumps(res, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    meta = json.loads(gpkg.with_suffix(".json").read_text(encoding="utf-8")) if gpkg.with_suffix(".json").exists() else {}
    meta["camada_vias_rurais_ref_proposto"] = {
        "transformacao_aplicada": ("vias veiculares rurais OSM sem ref e sem nome; endereço rural do CNEFE → via rural mais próxima a "
                                   "≤ 100 m; código dominante (UR/BR/RS/RSC/ERS/VRS nnn) com ≥ 5 endereços e ≥ 60% dos endereços "
                                   "atribuídos à via; UR → ref; traçado antigo → old_ref; BR/RS em uso → sem proposta (acesso/marginal); "
                                   "confiança alta = via com o mesmo ref a ≤ 50 m e classe não service/track; baixa = service/track"),
        "resumo": {k: v for k, v in res.items() if k != "por_ref"}}
    gpkg.with_suffix(".json").write_text(json.dumps(meta, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return res


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("etapa", choices=["faces", "nomes", "rurais", "todos"])
    ap.add_argument("--codigo-ibge", default="4322400")
    ap.add_argument("--saida-tabelas", type=Path, required=True, help="pasta das tabelas de conferência (fora do git)")
    ap.add_argument("--tabelas-rodada-67", type=Path, default=None,
                    help="pasta com P5_*.csv da rodada 67 (padrão: a própria --saida-tabelas)")
    ap.add_argument("--forcar", action="store_true", help="baixa de novo o arquivo de faces")
    a = ap.parse_args()
    cod_area = json.loads((RAIZ / "config/area_estudo.geojson").read_text(encoding="utf-8"))["features"][0]["properties"].get("codarea")
    if cod_area and str(cod_area) != a.codigo_ibge:
        raise SystemExit(f"--codigo-ibge {a.codigo_ibge} difere da área de estudo ({cod_area})")
    a.saida_tabelas.mkdir(parents=True, exist_ok=True)
    res = {}
    try:
        if a.etapa in ("faces", "todos"):
            res["faces"] = parte_faces(a.saida_tabelas, a.codigo_ibge, a.forcar)
        if a.etapa in ("nomes", "todos"):
            res["nomes"] = parte_nomes(a.saida_tabelas, a.codigo_ibge, a.tabelas_rodada_67 or a.saida_tabelas)
        if a.etapa in ("rurais", "todos"):
            res["rurais"] = parte_rurais(a.saida_tabelas)
    except (requests.RequestException, OSError) as e:
        logger.error("Falha: %s", e)
        raise SystemExit(1)
    print(json.dumps({k: {kk: vv for kk, vv in v.items() if not isinstance(vv, list)} for k, v in res.items()},
                     ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    main()
