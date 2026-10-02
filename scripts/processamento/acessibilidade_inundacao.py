"""
Acessibilidade às unidades de saúde da atenção primária (ESF/UBS) com vias
dentro das manchas de inundação por cota do rio (rodada 08). Só medir: nenhum
resultado afirma que um trecho é ou não transitável — as manchas do SGB dizem
ONDE alaga em cada cota, não a profundidade.

Entradas (todas no repositório; nada é baixado aqui):
  - malha viária do OpenStreetMap com pontes como trechos próprios
    (data/raw/vetor/malha-viaria-pontes-separadas_osm_atual_vetorial.gpkg,
    gerada por scripts/download/infraestrutura_osm.py --malha-pontes);
  - manchas por cota do SGB (cumulativas: união das cotas <= X);
  - endereços de domicílio particular do CNEFE 2022 com exposição e população
    estimada (data/processed/exposicao_inundacao/, rodadas 03/04);
  - 23 unidades de saúde (data/processed/saude/); destino = classes ESF e UBS.

Método (resumo; detalhes no .json de cada produto):
  - rede NÃO direcionada; medida = distância pela rede, em metros;
  - endereço e unidade ligados ao ponto mais próximo do trecho mais próximo
    (perpendicular entra na distância); a mais de --dist-max-via-m de qualquer
    via, ligados ao nó mais próximo (ressalva registrada);
  - trecho INTERROMPIDO na cota X quando >= --limiar-m dele fica dentro da
    mancha cumulativa (ou quando está todo dentro, se for mais curto que o
    limiar); no trecho interrompido, saem da rede só os pedaços (entre pontos
    de ligação) que tocam a mancha — a parte seca continua servindo quem mora nela;
  - cenário PESSIMISTA: todo trecho interrompido sai, pontes incluídas;
    cenário OTIMISTA: trechos marcados como ponte/viaduto no OSM ficam;
  - unidade dentro da mancha deixa de ser destino naquela cota;
  - classes do endereço, em ordem: exposto; isolado; com desvio; sem alteração.

Saídas:
  docs/acessibilidade_inundacao/tabelas/*.csv (+ .json)  — só agregados
  data/processed/acessibilidade_inundacao/*.gpkg (+ .json) — camadas (fora do git)
Os mapas ficam em scripts/processamento/acessibilidade_inundacao_mapas.py.

Uso:
  python scripts/processamento/acessibilidade_inundacao.py --codigo-ibge 4322400
"""

from __future__ import annotations

import argparse
import heapq
import json
import logging
from collections import defaultdict

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import LineString, Point
from shapely.ops import substring

import dinamica_populacional_comum as c

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

SCRIPT = "scripts/processamento/acessibilidade_inundacao.py"
DOCS = c.RAIZ / "docs" / "acessibilidade_inundacao"
TAB = DOCS / "tabelas"
CAMADAS = c.RAIZ / "data" / "processed" / "acessibilidade_inundacao"
ARQ_MALHA = c.RAW / "vetor" / "malha-viaria-pontes-separadas_osm_atual_vetorial.gpkg"
ARQ_MALHA_ORIGINAL = c.RAW / "vetor" / "malha-viaria_osm_atual_vetorial.gpkg"
ARQ_COTAS = c.RAW / "vetor" / "cotas-inundacao_sgb_atual_vetorial.gpkg"
ARQ_END = c.RAIZ / "data" / "processed" / "exposicao_inundacao" / "enderecos-exposicao-inundacao_sgb-ibge-cnefe_2022_pontos.gpkg"
ARQ_UNIDADES = c.RAIZ / "data" / "processed" / "saude" / "unidades-saude-esf-ubs_cnes-revisado-v4_2026_pontos.gpkg"
CLASSES_DESTINO = ("ESF", "UBS")
CENARIOS = ("pessimista", "otimista")
FAIXAS_BASE = [0, 500, 1000, 2000, np.inf]
ROT_BASE = ["até 500 m", "500 m a 1 km", "1 a 2 km", "mais de 2 km"]
FAIXAS_DESVIO = [0, 250, 500, 1000, 2000, np.inf]
ROT_DESVIO = ["até 250 m", "250 a 500 m", "500 m a 1 km", "1 a 2 km", "mais de 2 km"]
ORDEM_CLASSE = ["exposto", "isolado", "com desvio", "sem alteração", "sem caminho já na base"]
TOL_M = 0.5  # diferença de distância abaixo disto é arredondamento, não desvio
FONTES_META = ["OpenStreetMap — malha viária (network_type drive), pontes como trechos próprios (contribuidores do OpenStreetMap, ODbL)",
               "SGB — manchas de inundação por cota, serviço hidrologia/BACIA_DO_URUGUAI_URUGUAIANA (camada do repositório)",
               "IBGE — CNEFE do Censo 2022, espécie 1 (domicílio particular); agregados e malha de setores 2022",
               "CNES (Ministério da Saúde), revisado e corrigido pela equipe do projeto com informações dos profissionais de saúde do município — versão 4, 2026",
               "ANA — Base Hidrográfica Ottocodificada (curso d'água), para o cruzamento das pontes"]
NOTA_CENARIOS = ("A resposta real fica entre os dois cenários: o pessimista retira toda via com parte dentro da mancha, pontes incluídas; "
                 "o otimista mantém as pontes/viadutos do OpenStreetMap. Sem profundidade nem cota do tabuleiro, nenhum dos dois afirma que um trecho é ou não transitável.")


def meta(caminho, **kw):
    return c.gravar_meta(caminho, codigo_ibge=ARGS.codigo_ibge, crs=c.CRS_PADRAO, status=c.STATUS_CONFERENCIA, ligado_ao_portal=False,
                         script=SCRIPT, fontes=FONTES_META, parametros=PARAMS, **kw)


def salvar_tab(df: pd.DataFrame, nome: str, descricao: str, **kw) -> None:
    arq = TAB / nome
    df.to_csv(arq, index=False)
    meta(arq, descricao=descricao, **kw)
    logger.info("Tabela: %s (%d linhas)", arq.relative_to(c.RAIZ), len(df))


def wmean(v, w):
    ok = v.notna() & (w > 0)
    return float((v[ok] * w[ok]).sum() / w[ok].sum()) if ok.any() else np.nan


def q(s, p):
    s = s[np.isfinite(s)]
    return round(float(np.quantile(s, p)), 0) if len(s) else np.nan


# ------------------------------------------------------------------ grafo
class Rede:
    """Grafo não direcionado: nós originais do OSM + nós de ligação (pontos projetados nos trechos) + nós das unidades."""

    def __init__(self, trechos: gpd.GeoDataFrame, nos: gpd.GeoDataFrame):
        self.trechos = trechos  # não direcionados, índice = eid
        self.no_idx = {osm: i for i, osm in enumerate(nos.osmid)}
        self.xy = [(g.x, g.y) for g in nos.geometry]
        self.sub = []  # sub-trechos: (a, b, comprimento, eid, t0, t1)  eid = -1 para ligação (nunca sai)

    def novo_no(self, x, y) -> int:
        self.xy.append((x, y))
        return len(self.xy) - 1

    def montar(self, ligacoes: dict[int, list[float]]) -> dict[tuple[int, float], int]:
        """Parte cada trecho nos pontos de ligação; devolve {(eid, t): nó}."""
        no_em = {}
        for eid, r in self.trechos.iterrows():
            a, b = self.no_idx[r.u], self.no_idx[r.v]
            L = r.geometry.length
            ts = sorted({round(t, 2) for t in ligacoes.get(eid, []) if 0.01 < round(t, 2) < L - 0.01})
            cadeia, pos = [a], [0.0]
            for t in ts:
                p = r.geometry.interpolate(t)
                n = self.novo_no(p.x, p.y)
                no_em[(eid, t)] = n
                cadeia.append(n)
                pos.append(t)
            cadeia.append(b)
            pos.append(L)
            for i in range(len(cadeia) - 1):
                self.sub.append((cadeia[i], cadeia[i + 1], max(pos[i + 1] - pos[i], 1e-6), eid, pos[i], pos[i + 1]))
            no_em[(eid, 0.0)] = a
            no_em[(eid, "L")] = b
        return no_em

    def ligar(self, a: int, b: int, w: float) -> int:
        self.sub.append((a, b, max(w, 1e-6), -1, 0.0, w))
        return len(self.sub) - 1

    def finalizar(self):
        self.n = len(self.xy)
        S = np.array([(a, b, w, e) for a, b, w, e, _, _ in self.sub], dtype=float)
        self.sa, self.sb = S[:, 0].astype(int), S[:, 1].astype(int)
        self.sw, self.se = S[:, 2], S[:, 3].astype(int)
        self.adj = [[] for _ in range(self.n)]
        for k, (a, b, w) in enumerate(zip(self.sa, self.sb, self.sw)):
            self.adj[a].append((b, w, k))
            self.adj[b].append((a, w, k))

    def dijkstra(self, fontes: list[int], fora: np.ndarray | None = None, limite: float = np.inf):
        """Multi-origem: distância à fonte mais próxima, qual fonte e predecessor (nó, sub-trecho). `fora` = sub-trechos retirados."""
        dist = np.full(self.n, np.inf)
        orig = np.full(self.n, -1)
        pred = np.full(self.n, -1)
        psub = np.full(self.n, -1)
        h = []
        for f in fontes:
            dist[f] = 0.0
            orig[f] = f
            h.append((0.0, f))
        heapq.heapify(h)
        while h:
            d, x = heapq.heappop(h)
            if d > dist[x] or d > limite:
                continue
            for y, w, k in self.adj[x]:
                if fora is not None and fora[k]:
                    continue
                nd = d + w
                if nd < dist[y]:
                    dist[y], orig[y], pred[y], psub[y] = nd, orig[x], x, k
                    heapq.heappush(h, (nd, y))
        return dist, orig, pred, psub

    def componentes(self, fora: np.ndarray | None = None) -> np.ndarray:
        comp = np.full(self.n, -1)
        cid = 0
        for s in range(self.n):
            if comp[s] >= 0:
                continue
            pilha = [s]
            comp[s] = cid
            while pilha:
                x = pilha.pop()
                for y, _, k in self.adj[x]:
                    if comp[y] < 0 and not (fora is not None and fora[k]):
                        comp[y] = cid
                        pilha.append(y)
            cid += 1
        return comp


def carregar_trechos() -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame, dict]:
    """Trechos NÃO direcionados (via de mão dupla vem duplicada u->v e v->u; fica uma) e nós."""
    t = gpd.read_file(ARQ_MALHA, layer="trechos").to_crs(c.CRS_PADRAO)
    nos = gpd.read_file(ARQ_MALHA, layer="nos").to_crs(c.CRS_PADRAO)
    t["a"] = t[["u", "v"]].min(axis=1)
    t["b"] = t[["u", "v"]].max(axis=1)
    t["L"] = t.geometry.length.round(1)
    n_dir = len(t)
    t = t.drop_duplicates(["a", "b", "L"]).reset_index(drop=True)
    t["ponte"] = t.bridge.notna()
    t["highway"] = t.highway.astype(str)
    t["nome_via"] = t.name.fillna("(sem nome no OSM)")
    t.index.name = "eid"
    info = {"trechos_direcionados": n_dir, "trechos_nao_direcionados": len(t), "nos": len(nos),
            "km_nao_direcionado": round(float(t.geometry.length.sum() / 1000), 1), "trechos_ponte": int(t.ponte.sum()),
            "atributos": {"nome": int(t.name.notna().sum()), "ponte (bridge)": int(t.ponte.sum()), "tunel (tunnel)": int(t.tunnel.notna().sum()),
                          "mao_unica (oneway, ignorada: rede não direcionada)": int(t.oneway.astype(str).isin(["True", "1", "true"]).sum()),
                          "layer": int(t.layer.notna().sum())},
            "laços_u_igual_v": int((t.u == t.v).sum())}
    return t, nos, info


def pontos_na_rede(pts: gpd.GeoDataFrame, trechos: gpd.GeoDataFrame, nos: gpd.GeoDataFrame, dist_max: float) -> pd.DataFrame:
    """Trecho mais próximo e posição ao longo dele; acima de dist_max, nó mais próximo."""
    j = gpd.sjoin_nearest(pts[["geometry"]], trechos[["geometry"]], how="left", distance_col="dist_via")
    j = j[~j.index.duplicated()]
    col = "eid" if "eid" in j.columns else "index_right"
    out = pd.DataFrame({"eid": j[col].astype(int), "dist_via": j.dist_via}, index=pts.index)
    out["t"] = [trechos.geometry.iat[e].project(g) for e, g in zip(out.eid, pts.geometry)]
    longe = out.dist_via > dist_max
    out["ligado_no"] = longe
    if longe.any():
        jn = gpd.sjoin_nearest(pts.loc[longe, ["geometry"]], nos[["osmid", "geometry"]], how="left", distance_col="dist_no")
        jn = jn[~jn.index.duplicated()]
        out.loc[longe, "osmid_no"] = jn.osmid
        out.loc[longe, "dist_no"] = jn.dist_no
    return out


def interrompidos(trechos, dentro_m: dict, limiar: float) -> dict:
    """{cota: série booleana por eid} — >= limiar m dentro (ou trecho todo dentro); limiar 0 = qualquer extensão > 0."""
    out = {}
    L = trechos.geometry.length
    for k, dentro in dentro_m.items():
        if limiar <= 0:
            out[k] = dentro > 0
        else:
            out[k] = (dentro >= limiar) | (dentro >= 0.99 * L) & (dentro > 0)
    return out


def main() -> None:
    for p in (TAB, CAMADAS):
        p.mkdir(parents=True, exist_ok=True)
    limite = c.carregar_area_estudo()
    trechos, nos, info_rede = carregar_trechos()
    orig = gpd.read_file(ARQ_MALHA_ORIGINAL)
    info_rede["malha_original_2026_07_27"] = {"trechos_direcionados": len(orig), "km_direcionado": round(float(orig.geometry.length.sum() / 1000), 1),
                                             "pontes_fundidas_com_ruas": int(orig.bridge.notna().sum()),
                                             "comprimento_mediano_trecho_ponte_m": round(float(orig[orig.bridge.notna()].geometry.length.median()), 0)}
    info_rede["comprimento_mediano_trecho_ponte_m"] = round(float(trechos[trechos.ponte].geometry.length.median()), 0)

    # ---------------- manchas cumulativas (análise sem recorte; recorte no limite só para exibição, nos mapas)
    cg = gpd.read_file(ARQ_COTAS).to_crs(c.CRS_PADRAO)
    cg["geometry"] = cg.geometry.buffer(0)
    d = cg.dissolve(by="cota_cm", aggfunc={"tr_anos": "first"}).sort_index()
    K = [int(k) for k in d.index]
    TR = {int(k): float(d.loc[k, "tr_anos"]) for k in d.index}
    manchas = {k: d.loc[K[: i + 1], "geometry"].union_all() for i, k in enumerate(K)}

    # ---------------- endereços, zona e unidades
    end = gpd.read_file(ARQ_END).to_crs(c.CRS_PADRAO)
    st = gpd.read_file(c.CAMADAS / "populacao-setores_ibge-censo_2022_setor.gpkg").set_index("CD_SETOR")
    sede = st.loc[st["pop"].idxmax(), "CD_DIST"]
    end["zona"] = np.where((end.setor_2022.map(st.SITUACAO) == "Urbana") & (end.setor_2022.map(st.CD_DIST) == sede), "urbana da sede", "interior")
    end["pct60"] = end.setor_2022.map(st.pct_60_mais)
    end["pct014"] = end.setor_2022.map(st.pct_0_14)
    end["pop"] = end.pop_est_setor
    unid = gpd.read_file(ARQ_UNIDADES).to_crs(c.CRS_PADRAO).reset_index(drop=True)
    unid["destino"] = unid.classe.isin(CLASSES_DESTINO)

    # ---------------- Tarefa 0c: ligação dos pontos à rede
    le = pontos_na_rede(end, trechos, nos, ARGS.dist_max_via_m)
    lu = pontos_na_rede(unid, trechos, nos, ARGS.dist_max_via_m)
    end["dist_via_m"] = le.dist_via.round(1)
    end["ligado_ao_no"] = le.ligado_no
    unid["dist_via_m"] = lu.dist_via.round(1)
    logger.info("Endereços a > %g m de via: %d; unidades: %d", ARGS.dist_max_via_m, le.ligado_no.sum(), lu.ligado_no.sum())

    # ---------------- grafo com pontos de ligação
    rede = Rede(trechos, nos)
    lig = defaultdict(list)
    for df in (le, lu):
        for e, t, ln in zip(df.eid, df.t, df.ligado_no):
            if not ln:
                lig[e].append(t)
    no_em = rede.montar(lig)

    def no_do_ponto(r, L):
        if r.ligado_no:
            return rede.no_idx[int(r.osmid_no)], float(r.dist_no)
        t = round(r.t, 2)
        if t <= 0.01:
            return no_em[(r.eid, 0.0)], float(r.dist_via)
        if t >= L - 0.01:
            return no_em[(r.eid, "L")], float(r.dist_via)
        return no_em[(r.eid, t)], float(r.dist_via)

    Lt = trechos.geometry.length
    ne = [no_do_ponto(r, Lt.iat[r.eid]) for r in le.itertuples()]
    end["_no"] = [a for a, _ in ne]
    end["_lig_m"] = [b for _, b in ne]
    # unidade = nó próprio ligado ao ponto do trecho (ligação nunca sai da rede)
    nu = [no_do_ponto(r, Lt.iat[r.eid]) for r in lu.itertuples()]
    unid["no_g"] = [rede.novo_no(g.x, g.y) for g in unid.geometry]
    for (a, w), un in zip(nu, unid["no_g"]):
        rede.ligar(un, a, w)
    unid["_no_rede"] = [a for a, _ in nu]
    rede.finalizar()
    logger.info("Grafo: %d nós, %d sub-trechos", rede.n, len(rede.sub))
    sub_eid = rede.se

    # geometria dos sub-trechos dos trechos (para retirar só a parte que toca a mancha)
    sub_geom = [substring(trechos.geometry.iat[e], t0, t1) if e >= 0 else None for (_, _, _, e, t0, t1) in rede.sub]

    # ---------------- interrupção por cota, cenário e limiar
    def sub_fora(intr: pd.Series, k: int, cenario: str) -> np.ndarray:
        """Sub-trechos retirados: pedaços que tocam a mancha dentro de trechos interrompidos (cenário otimista mantém pontes)."""
        alvo = intr.copy()
        if cenario == "otimista":
            alvo &= ~trechos.ponte
        fora = np.zeros(len(rede.sub), bool)
        ids = np.where(np.isin(sub_eid, np.where(alvo.values)[0]))[0]
        m = manchas[k]
        for s in ids:
            g = sub_geom[s]
            if g is not None and g.intersects(m) and g.intersection(m).length > 0:
                fora[s] = True
        return fora

    dentro_m = {k: trechos.geometry.intersection(manchas[k]).length for k in K}
    INT = {lim: interrompidos(trechos, dentro_m, lim) for lim in sorted({ARGS.limiar_m, *ARGS.limiares_sensibilidade})}
    intr = INT[ARGS.limiar_m]

    # ---------------- Tarefa 1: base
    fontes_destino = list(unid.loc[unid.destino, "no_g"])
    no2rot = dict(zip(unid.no_g, unid.rotulo))
    D0, O0, P0, PS0 = rede.dijkstra(fontes_destino)
    comp0 = rede.componentes()
    end["dist_base_m"] = D0[end._no.values] + end._lig_m.values
    end["unid_base"] = [no2rot.get(o) for o in O0[end._no.values]]
    principal0 = pd.Series(comp0[end._no.values]).value_counts().idxmax()
    end["comp_principal_base"] = comp0[end._no.values] == principal0

    # ---------------- Tarefa 3: cenários
    res = {}
    fora_sets = {}

    def classificar(k, fora, unid_fora):
        """Distância no cenário e classe de cada endereço."""
        fontes = [n for n, ok in zip(unid.no_g, unid.destino & ~unid_fora) if ok]
        D, O, _, _ = rede.dijkstra(fontes, fora)
        dist = D[end._no.values] + end._lig_m.values
        cls = np.where(end[f"exp_cum_{k}"], "exposto",
                       np.where(~np.isfinite(end.dist_base_m), "sem caminho já na base",
                                np.where(~np.isfinite(dist), "isolado",
                                         np.where(dist > end.dist_base_m + TOL_M, "com desvio", "sem alteração"))))
        un = np.array([no2rot.get(o) for o in O[end._no.values]], dtype=object)
        return dist, cls, un

    unid_na_mancha = {k: unid.within(manchas[k]) for k in K}
    for k in K:
        for cen in CENARIOS:
            fora = sub_fora(intr[k], k, cen)
            fora_sets[(k, cen)] = fora
            dist, cls, un = classificar(k, fora, unid_na_mancha[k])
            res[(k, cen)] = {"dist": dist, "cls": cls, "un": un}
            end[f"cls_{k}_{cen[:3]}"] = cls
            end[f"acr_{k}_{cen[:3]}"] = np.where(cls == "com desvio", dist - end.dist_base_m, np.nan)
            end[f"unid_{k}_{cen[:3]}"] = np.where(np.isfinite(dist) & (cls != "exposto"), un, None)
            logger.info("cota %d %s: %s", k, cen, pd.Series(cls).value_counts().to_dict())

    # ---------------- conferências obrigatórias
    conf = {}
    Dc, _, _, _ = rede.dijkstra(fontes_destino, np.zeros(len(rede.sub), bool))
    d_chk = Dc[end._no.values] + end._lig_m.values
    conf["sem_trechos_retirados_igual_tarefa1"] = bool(np.array_equal(np.nan_to_num(d_chk, posinf=-1), np.nan_to_num(end.dist_base_m.values, posinf=-1)))
    viol_cen = {}
    sev = {"sem alteração": 0, "com desvio": 1, "isolado": 2, "exposto": 3, "sem caminho já na base": 4}
    for k in K:
        dp, do = res[(k, "pessimista")]["dist"], res[(k, "otimista")]["dist"]
        pior = (np.nan_to_num(do, posinf=1e12) > np.nan_to_num(dp, posinf=1e12) + TOL_M)
        cls_pior = pd.Series(res[(k, "otimista")]["cls"]).map(sev).values > pd.Series(res[(k, "pessimista")]["cls"]).map(sev).values
        viol_cen[k] = {"enderecos_otimista_mais_longe": int(pior.sum()), "enderecos_otimista_classe_pior": int(cls_pior.sum())}
    conf["otimista_nunca_pior_que_pessimista"] = viol_cen
    mono = {}
    for cen in CENARIOS:
        seq = []
        for k in K:
            cl = pd.Series(res[(k, cen)]["cls"])
            seq.append({"cota": k, "isolados": int((cl == "isolado").sum()), "expostos": int((cl == "exposto").sum()),
                        "isolados_mais_expostos": int(cl.isin(["isolado", "exposto"]).sum())})
        s = pd.DataFrame(seq)
        mono[cen] = {"serie": seq, "expostos_nao_diminuem": bool(s.expostos.is_monotonic_increasing),
                     "isolados_nao_diminuem": bool(s.isolados.is_monotonic_increasing),
                     "isolados_mais_expostos_nao_diminuem": bool(s.isolados_mais_expostos.is_monotonic_increasing)}
        # por endereço: quem é isolado numa cota e deixa de ser na seguinte vira o quê
        trans = {}
        for a, b in zip(K[:-1], K[1:]):
            ca, cb = pd.Series(res[(a, cen)]["cls"]), pd.Series(res[(b, cen)]["cls"])
            m = (ca == "isolado") & (cb != "isolado")
            trans[f"{a}->{b}"] = cb[m].value_counts().to_dict()
        mono[cen]["isolados_que_deixam_de_ser_isolados_na_cota_seguinte"] = trans
    conf["isolados_e_expostos_nao_diminuem_com_a_cota"] = mono
    conf["nenhuma_distancia_de_cenario_menor_que_a_base"] = {
        f"{k}_{cen}": int((np.isfinite(end.dist_base_m.values) & (res[(k, cen)]["dist"] < end.dist_base_m.values - TOL_M)).sum()) for k in K for cen in CENARIOS}
    conf["trechos_interrompidos_aninhados_por_cota"] = bool(all((intr[a] <= intr[b]).all() for a, b in zip(K[:-1], K[1:])))
    logger.info("Conferências: %s", json.dumps(conf, ensure_ascii=False, default=str)[:1500])

    # ================================================================ TABELAS
    Z = {"urbana da sede": end.zona == "urbana da sede", "interior": end.zona == "interior"}

    # ---- Tarefa 0: rede e cobertura
    t0 = []
    for z, m in [("municipio", pd.Series(True, index=end.index)), *Z.items()]:
        e = end[m]
        longe = e[e.ligado_ao_no]
        t0.append({"zona": z, "enderecos": len(e), "pop_estimada": round(e["pop"].sum(), 1), "dist_via_mediana_m": round(e.dist_via_m.median(), 1),
                   "dist_via_p90_m": round(e.dist_via_m.quantile(0.9), 1), "dist_via_max_m": round(e.dist_via_m.max(), 1),
                   f"enderecos_a_mais_de_{ARGS.dist_max_via_m:g}m": len(longe), f"pop_a_mais_de_{ARGS.dist_max_via_m:g}m": round(longe["pop"].sum(), 1)})
    salvar_tab(pd.DataFrame(t0), "cobertura-rede-enderecos_osm-ibge-cnefe_2022_municipal.csv",
               "distância de cada endereço de domicílio particular ao trecho de via mais próximo (OSM, rede 'drive'); acima do limiar o endereço entra ligado ao nó mais próximo")
    tb = end[end.ligado_ao_no].groupby(["zona", "bairro"]).agg(enderecos=("pop", "size"), pop_estimada=("pop", "sum"),
                                                               dist_via_mediana_m=("dist_via_m", "median")).round(1).reset_index().sort_values("enderecos", ascending=False)
    salvar_tab(tb, "cobertura-rede-enderecos-longe-da-via_osm-ibge-cnefe_2022_bairro.csv",
               f"endereços a mais de {ARGS.dist_max_via_m:g} m de qualquer via do OSM, por zona e bairro IBGE 2022 (ligados ao nó mais próximo — ressalva)")
    rede_urb = trechos.geometry.intersection(st[(st.SITUACAO == "Urbana") & (st.CD_DIST == sede)].union_all())
    info_rede["km_nao_direcionado_area_urbana_da_sede"] = round(float(rede_urb.length.sum() / 1000), 1)
    info_rede["km_por_tipo_highway"] = (trechos.geometry.length.groupby(trechos.highway).sum() / 1000).round(1).sort_values(ascending=False).to_dict()
    info_rede["componentes_conexas_base"] = int(len(set(comp0[: len(nos)])))
    info_rede["enderecos_fora_da_componente_principal"] = int((~end.comp_principal_base).sum())
    info_rede["pop_fora_da_componente_principal"] = round(float(end.loc[~end.comp_principal_base, "pop"].sum()), 1)
    info_rede["unidades_ligadas_ao_no"] = int(lu.ligado_no.sum())
    info_rede["unidades_dist_via_max_m"] = round(float(lu.dist_via.max()), 1)

    # ---- Tarefa 1: base
    t1, t1f = [], []
    for z, m in Z.items():
        e = end[m]
        d = e.dist_base_m
        t1.append({"zona": z, "enderecos": len(e), "pop_estimada": round(e["pop"].sum(), 1), "mediana_m": q(d, 0.5), "p90_m": q(d, 0.9),
                   "max_m": round(float(d[np.isfinite(d)].max()), 0), "enderecos_sem_caminho": int((~np.isfinite(d)).sum()),
                   "pop_sem_caminho": round(float(e.loc[~np.isfinite(d), "pop"].sum()), 1)})
        fx = pd.cut(d, FAIXAS_BASE, labels=ROT_BASE, right=True)
        for f in ROT_BASE:
            sel = fx == f
            t1f.append({"zona": z, "faixa": f, "enderecos": int(sel.sum()), "pop_estimada": round(float(e.loc[sel, "pop"].sum()), 1),
                        "pct_pop_zona": round(100 * e.loc[sel, "pop"].sum() / e["pop"].sum(), 1)})
    salvar_tab(pd.DataFrame(t1), "acessibilidade-base-resumo_osm-cnes-ibge_2022_zona.csv",
               "distância pela rede (OSM, não direcionada) de cada endereço à unidade ESF/UBS mais próxima, sem inundação", nota="unidade mais próxima pela rede NÃO é o território oficial da equipe")
    salvar_tab(pd.DataFrame(t1f), "acessibilidade-base-faixas_osm-cnes-ibge_2022_zona.csv",
               "população estimada por faixa de distância pela rede até a unidade ESF/UBS mais próxima, sem inundação")
    tu = end.groupby("unid_base").agg(enderecos=("pop", "size"), pop_estimada=("pop", "sum"), dist_mediana_m=("dist_base_m", "median")).round(1)
    tu = unid.set_index("rotulo")[["classe", "zona", "bairro"]].join(tu, how="left").reset_index().fillna({"enderecos": 0, "pop_estimada": 0})
    salvar_tab(tu, "acessibilidade-base-por-unidade_osm-cnes-ibge_2022_unidade.csv",
               "endereços e população estimada que cada unidade recebe como 'mais próxima pela rede' (sem inundação); UDM e Prisional não são destino",
               nota="NÃO é o território oficial nem a população adscrita da equipe")

    # ---- Tarefa 2a: trechos interrompidos por cota
    t2, t2tipo = [], []
    for lim, I in INT.items():
        for k in K:
            s = I[k]
            t2.append({"limiar_m": lim, "cota_cm": k, "trechos_interrompidos": int(s.sum()), "km_trechos_interrompidos": round(float(trechos.geometry.length[s].sum() / 1000), 2),
                       "km_dentro_da_mancha": round(float(dentro_m[k][s].sum() / 1000), 2), "pontes_interrompidas": int((s & trechos.ponte).sum())})
            if lim == ARGS.limiar_m:
                g = pd.DataFrame({"tipo": trechos.highway, "km": trechos.geometry.length / 1000})[s].groupby("tipo").agg(trechos=("km", "size"), km=("km", "sum"))
                for tipo, r in g.iterrows():
                    t2tipo.append({"cota_cm": k, "tipo_via_osm": tipo, "trechos": int(r.trechos), "km": round(r.km, 2)})
    t2 = pd.DataFrame(t2)
    base_lim = t2[t2.limiar_m == ARGS.limiar_m].set_index("cota_cm")
    t2["dif_trechos_vs_limiar_padrao"] = t2.trechos_interrompidos - t2.cota_cm.map(base_lim.trechos_interrompidos)
    salvar_tab(t2, "trechos-interrompidos-por-cota_osm-sgb_atual_municipal.csv",
               f"trechos de via (não direcionados) com pelo menos L m dentro da mancha cumulativa; limiar padrão {ARGS.limiar_m:g} m e sensibilidade",
               regra="interrompido = extensão dentro da mancha >= limiar, ou trecho todo dentro quando mais curto que o limiar; limiar 0 = qualquer extensão > 0")
    salvar_tab(pd.DataFrame(t2tipo), "trechos-interrompidos-por-tipo_osm-sgb_atual_municipal.csv", f"trechos interrompidos (limiar {ARGS.limiar_m:g} m) por tipo de via (highway do OSM) e cota")

    # ---- Tarefa 2b: pontes
    pontes = inventario_pontes(trechos, manchas, K, intr, dentro_m, limite)
    salvar_tab(pontes.drop(columns="geometry"), "pontes-inventario-manchas_osm-sgb-bho_atual_ponte.csv",
               f"pontes/viadutos do OSM (trechos 'bridge' contíguos agrupados) a até {ARGS.raio_pontes_m:g} m da maior mancha; por cota, se a ponte e as ruas de chegada ficam dentro da mancha — lista para conferência em campo")
    cruz = cruzamentos_sem_ponte(trechos, manchas[K[-1]], limite)

    # ---- Tarefa 2c: acessos das unidades
    tac = []
    for r in unid.itertuples():
        D, _, _, _ = rede.dijkstra([r.no_g], limite=ARGS.raio_acesso_unidade_m)
        alc = D <= ARGS.raio_acesso_unidade_m
        ks = np.where((alc[rede.sa] | alc[rede.sb]) & (rede.se >= 0))[0]
        eids = sorted(set(rede.se[ks]))
        lin = {"rotulo": r.rotulo, "classe": r.classe, "zona": r.zona, "bairro": r.bairro, "trechos_de_acesso": len(eids)}
        for k in K:
            ii = [e for e in eids if intr[k].iat[e]]
            lin[f"unidade_na_mancha_{k}"] = bool(unid_na_mancha[k].iat[r.Index])
            lin[f"acessos_na_mancha_{k}"] = len(ii)
            lin[f"acessos_ponte_na_mancha_{k}"] = int(sum(trechos.ponte.iat[e] for e in ii))
        tac.append(lin)
    tac = pd.DataFrame(tac)
    salvar_tab(tac, "unidades-saude-acessos-na-mancha_osm-sgb-cnes_2026_unidade.csv",
               f"para cada uma das 23 unidades: trechos de via a até {ARGS.raio_acesso_unidade_m:g} m pela rede a partir do ponto e quantos ficam interrompidos (limiar {ARGS.limiar_m:g} m) em cada cota")

    # ---- Tarefa 3: tabela principal e detalhamentos
    t3, perf, bai, por_un, ilhas_t, un_isol = [], [], [], [], [], []
    ilhas_geo = []
    for k in K:
        for cen in CENARIOS:
            cl = pd.Series(res[(k, cen)]["cls"], index=end.index)
            acr = end[f"acr_{k}_{cen[:3]}"]
            for z, m in Z.items():
                for classe in ORDEM_CLASSE:
                    sel = m & (cl == classe)
                    t3.append({"cota_cm": k, "tr_anos": TR[k], "cenario": cen, "zona": z, "classe": classe, "enderecos": int(sel.sum()),
                               "pop_estimada": round(float(end.loc[sel, "pop"].sum()), 1)})
                fx = pd.cut(acr[m & (cl == "com desvio")], FAIXAS_DESVIO, labels=ROT_DESVIO, right=True)
                for f in ROT_DESVIO:
                    idx = fx.index[fx == f]
                    t3.append({"cota_cm": k, "tr_anos": TR[k], "cenario": cen, "zona": z, "classe": f"com desvio: {f}", "enderecos": len(idx),
                               "pop_estimada": round(float(end.loc[idx, "pop"].sum()), 1)})
                muda = m & (cl == "com desvio") & (end[f"unid_{k}_{cen[:3]}"] != end.unid_base)
                t3.append({"cota_cm": k, "tr_anos": TR[k], "cenario": cen, "zona": z, "classe": "com desvio: muda a unidade mais próxima",
                           "enderecos": int(muda.sum()), "pop_estimada": round(float(end.loc[muda, "pop"].sum()), 1)})
                for grupo, sel in (("isolados", m & (cl == "isolado")), ("desvio > 500 m", m & (cl == "com desvio") & (acr > 500)),
                                   ("zona inteira", m)):
                    e = end[sel]
                    perf.append({"cota_cm": k, "cenario": cen, "zona": z, "grupo": grupo, "enderecos": len(e), "pop_estimada": round(float(e["pop"].sum()), 1),
                                 "pct_60_mais": round(wmean(e.pct60, e["pop"]), 1), "pct_0_14": round(wmean(e.pct014, e["pop"]), 1),
                                 "pop_em_setor_sob_sigilo": round(float(e.loc[e.pct60.isna(), "pop"].sum()), 1)})
            # bairro
            for (z, b), g in end.assign(cl=cl).groupby(["zona", "bairro"]):
                lin = {"cota_cm": k, "cenario": cen, "zona": z, "bairro": b, "enderecos_bairro": len(g)}
                for classe in ORDEM_CLASSE[:3]:
                    lin[f"pop_{classe.replace(' ', '_')}"] = round(float(g.loc[g.cl == classe, "pop"].sum()), 1)
                    lin[f"end_{classe.replace(' ', '_')}"] = int((g.cl == classe).sum())
                lin["pop_desvio_mais_500m"] = round(float(g.loc[(g.cl == "com desvio") & (acr[g.index] > 500), "pop"].sum()), 1)
                if lin["end_exposto"] + lin["end_isolado"] + lin["end_com_desvio"]:
                    bai.append(lin)
            # unidade: quem cada unidade recebe como mais próxima
            un = end[f"unid_{k}_{cen[:3]}"]
            for r in unid[unid.destino].itertuples():
                p0 = float(end.loc[end.unid_base == r.rotulo, "pop"].sum())
                p1 = float(end.loc[un == r.rotulo, "pop"].sum())
                perde = float(end.loc[(end.unid_base == r.rotulo) & (un != r.rotulo), "pop"].sum())
                recebe = float(end.loc[(end.unid_base != r.rotulo) & (un == r.rotulo), "pop"].sum())
                por_un.append({"cota_cm": k, "cenario": cen, "rotulo": r.rotulo, "classe": r.classe, "zona": r.zona, "pop_base": round(p0, 1),
                               "pop_cenario": round(p1, 1), "perde": round(perde, 1), "recebe": round(recebe, 1), "saldo": round(p1 - p0, 1),
                               "unidade_na_mancha": bool(unid_na_mancha[k].iat[r.Index])})
            # ilhas e unidades fora da componente principal
            fora = fora_sets[(k, cen)]
            comp = rede.componentes(fora)
            seco = ~end[f"exp_cum_{k}"] & end.comp_principal_base
            ce = pd.Series(comp[end._no.values], index=end.index)
            principal = ce[seco].value_counts().idxmax() if seco.any() else -1
            cu = pd.Series(comp[unid.no_g.values], index=unid.index)
            for r in unid.itertuples():
                if cu.iat[r.Index] != principal:
                    un_isol.append({"cota_cm": k, "cenario": cen, "rotulo": r.rotulo, "classe": r.classe, "unidade_na_mancha": bool(unid_na_mancha[k].iat[r.Index]),
                                    "pop_seca_ligada_a_ela": round(float(end.loc[seco & (ce == cu.iat[r.Index]), "pop"].sum()), 1)})
            ilh = ce[seco & (ce != principal)]
            n_osm = len(rede.no_idx)  # nós 0..n_osm-1 = nós do OSM (cruzamentos/pontas); os demais são pontos de ligação
            com_cruz = set(comp[:n_osm][comp[:n_osm] >= 0])
            for cid, idx in ilh.groupby(ilh).groups.items():
                us = unid[(cu == cid) & unid.destino & ~unid_na_mancha[k]]
                tipo = "parte da rede (com cruzamento ou ponta de via)" if cid in com_cruz else "pedaço de rua seco entre partes alagadas da mesma rua"
                ilhas_t.append({"cota_cm": k, "cenario": cen, "ilha": int(cid), "tipo": tipo, "enderecos": len(idx), "pop_estimada": round(float(end.loc[idx, "pop"].sum()), 1),
                                "zona_predominante": end.loc[idx, "zona"].mode().iat[0], "bairros": "; ".join(sorted(end.loc[idx, "bairro"].unique()))[:200],
                                "unidade_dentro": "; ".join(us.rotulo) or "nenhuma"})
                nos_i = np.where(comp == cid)[0]
                subs = np.where(np.isin(rede.sa, nos_i) & ~fora & (rede.se >= 0))[0]
                geoms = [sub_geom[s] for s in subs if sub_geom[s] is not None]
                pts_i = list(end.loc[idx, "geometry"])
                contorno = gpd.GeoSeries(geoms + pts_i, crs=c.CRS_PADRAO).buffer(25).union_all().buffer(15)
                ilhas_geo.append({"cota_cm": k, "cenario": cen, "ilha": int(cid), "tipo": tipo, "enderecos": len(idx), "pop_estimada": round(float(end.loc[idx, "pop"].sum()), 1),
                                  "unidade_dentro": "; ".join(us.rotulo) or "nenhuma", "geometry": contorno})
    t3 = pd.DataFrame(t3)
    salvar_tab(t3, "acessibilidade-classes-por-cota-cenario_osm-sgb-cnes-ibge_2022_zona.csv",
               "TABELA PRINCIPAL: endereços e população estimada por classe (exposto, isolado, com desvio, sem alteração), cota, cenário e zona; faixas do acréscimo de distância",
               nota_cenarios=NOTA_CENARIOS)
    salvar_tab(pd.DataFrame(perf), "acessibilidade-perfil-isolados-desvio_osm-sgb-ibge_2022_zona.csv",
               "perfil (% 60+ e % 0–14 do setor, ponderados pela população estimada; setores sob sigilo fora da média) dos isolados e dos com desvio > 500 m")
    salvar_tab(pd.DataFrame(bai), "acessibilidade-classes-por-bairro_osm-sgb-ibge_2022_bairro.csv", "população estimada e endereços por classe, bairro IBGE 2022, cota e cenário (só bairros com algum afetado)")
    salvar_tab(pd.DataFrame(por_un), "acessibilidade-por-unidade-cota-cenario_osm-sgb-cnes_2026_unidade.csv",
               "população estimada que cada unidade ESF/UBS tem como 'mais próxima pela rede' na base e no cenário; perde/recebe", nota="NÃO é a população adscrita")
    salvar_tab(pd.DataFrame(ilhas_t), "acessibilidade-ilhas-por-cota-cenario_osm-sgb-ibge_2022_ilha.csv",
               "ilhas: partes da rede fora da mancha que perdem a ligação com o restante (componente principal) — endereços, população e unidade dentro")
    salvar_tab(pd.DataFrame(un_isol, columns=["cota_cm", "cenario", "rotulo", "classe", "unidade_na_mancha", "pop_seca_ligada_a_ela"]),
               "unidades-fora-da-rede-principal-por-cota_osm-sgb-cnes_2026_unidade.csv", "unidades que ficam fora da componente principal da rede em cada cota e cenário")

    # ---- diferença entre cenários e pontes que a explicam (retirada de uma ponte por vez do otimista)
    dif, resp = [], []
    for k in K:
        cp, co = res[(k, "pessimista")]["cls"], res[(k, "otimista")]["cls"]
        mud = cp != co
        for z, m in Z.items():
            sel = m.values & mud
            tr = pd.DataFrame({"de": cp[sel], "para": co[sel], "pop": end["pop"].values[sel]}).groupby(["de", "para"]).agg(enderecos=("pop", "size"), pop_estimada=("pop", "sum"))
            for (de, para), r in tr.iterrows():
                dif.append({"cota_cm": k, "zona": z, "pessimista": de, "otimista": para, "enderecos": int(r.enderecos), "pop_estimada": round(r.pop_estimada, 1)})
            sel2 = m.values & ~mud & (cp == "com desvio")
            ganho = end.loc[sel2, "dist_base_m"].values * 0 + (res[(k, "pessimista")]["dist"][sel2] - res[(k, "otimista")]["dist"][sel2])
            dif.append({"cota_cm": k, "zona": z, "pessimista": "com desvio", "otimista": "com desvio, mais curto",
                        "enderecos": int((ganho > TOL_M).sum()), "pop_estimada": round(float(end.loc[sel2, "pop"].values[ganho > TOL_M].sum()), 1)})
        candidatas = pontes[pontes[f"ponte_interrompida_{k}"]]
        for p in candidatas.itertuples():
            eids = [int(x) for x in str(p.eids).split(";")]
            intr_k = intr[k].copy()
            fora = fora_sets[(k, "otimista")].copy()
            sub_p = np.where(np.isin(sub_eid, eids))[0]
            m = manchas[k]
            for s in sub_p:
                g = sub_geom[s]
                if intr_k.iat[sub_eid[s]] and g is not None and g.intersection(m).length > 0:
                    fora[s] = True
            dist, cls, _ = classificar(k, fora, unid_na_mancha[k])
            piora = pd.Series(cls).map(sev).values > pd.Series(co).map(sev).values
            mais_longe = np.nan_to_num(dist, posinf=1e12) > np.nan_to_num(res[(k, "otimista")]["dist"], posinf=1e12) + TOL_M
            resp.append({"cota_cm": k, "ponte": p.ponte, "via": p.via, "curso_dagua": p.curso_dagua,
                         "pop_muda_de_classe_sem_ela": round(float(end.loc[piora, "pop"].sum()), 1), "enderecos_muda_de_classe": int(piora.sum()),
                         "pop_urbana_muda_de_classe": round(float(end.loc[piora & Z["urbana da sede"].values, "pop"].sum()), 1),
                         "pop_caminho_mais_longo_sem_ela": round(float(end.loc[mais_longe, "pop"].sum()), 1)})
    salvar_tab(pd.DataFrame(dif), "acessibilidade-diferenca-cenarios_osm-sgb-ibge_2022_zona.csv",
               "endereços e população que mudam de classe do cenário pessimista para o otimista, por cota e zona", nota_cenarios=NOTA_CENARIOS)
    resp = pd.DataFrame(resp, columns=["cota_cm", "ponte", "via", "curso_dagua", "pop_muda_de_classe_sem_ela", "enderecos_muda_de_classe",
                                       "pop_urbana_muda_de_classe", "pop_caminho_mais_longo_sem_ela"])
    salvar_tab(resp, "pontes-que-decidem-cenarios_osm-sgb-ibge_2022_ponte.csv",
               "para cada ponte interrompida na cota: população que piora de classe (e que passa a ter caminho mais longo) se SÓ essa ponte for retirada do cenário otimista",
               metodo="retirada de uma ponte por vez; efeitos não somam quando duas pontes servem a mesma área")

    # ---- Tarefa 4: trechos críticos (fluxo do caminho de base)
    crit = trechos_criticos(rede, end, trechos, intr, K, D0, P0, PS0, pontes)
    salvar_tab(crit, "trechos-criticos-cotas-1205-1252_osm-sgb-ibge_2022_trecho.csv",
               f"os {ARGS.n_criticos} trechos interrompidos por onde passava o caminho de base (sem inundação) de mais pessoas até a unidade mais próxima, nas cotas {ARGS.cotas_criticas}",
               metodo="árvore de caminhos mínimos da base; população do trecho = soma das pessoas cujo caminho usa qualquer parte dele (pedaços entre pontos de ligação)")

    # ================================================================ CAMADAS (fora do git)
    cols = ["COD_UNICO_ENDERECO", "setor_2022", "bairro", "zona", "pop", "pct60", "pct014", "dist_via_m", "ligado_ao_no", "dist_base_m", "unid_base",
            *[f"exp_cum_{k}" for k in K], *[x for k in K for cen in CENARIOS for x in (f"cls_{k}_{cen[:3]}", f"acr_{k}_{cen[:3]}", f"unid_{k}_{cen[:3]}")], "geometry"]
    out = CAMADAS / "enderecos-acessibilidade-inundacao_osm-sgb-cnes-ibge_2022_pontos.gpkg"
    end[cols].to_file(out, driver="GPKG", layer="enderecos")
    meta(out, descricao="endereços com distância de base à unidade ESF/UBS mais próxima e classe por cota e cenário (pes/oti)", fora_do_git="data/processed/ é ignorado; não publicar")
    tr = trechos[["u", "v", "highway", "nome_via", "ponte", "geometry"]].copy()
    tr["eid"] = tr.index
    tr = tr.reset_index(drop=True)
    for k in K:
        tr[f"int_{k}"] = intr[k].values
        tr[f"dentro_m_{k}"] = dentro_m[k].round(1).values
    tr["cota_entra"] = np.select([intr[k].values for k in K], K, default=0)
    tr["ponte_id"] = tr.eid.map({int(e): p.ponte for p in pontes.itertuples() for e in str(p.eids).split(";")})
    out = CAMADAS / "trechos-interrompidos-cotas_osm-sgb_atual_linhas.gpkg"
    tr.to_file(out, driver="GPKG", layer="trechos")
    meta(out, descricao="trechos de via não direcionados com a interrupção por cota (limiar padrão) e a extensão dentro da mancha", fora_do_git="sim")
    out = CAMADAS / "pontes-inventario_osm-sgb-bho_atual_linhas.gpkg"
    pontes.to_file(out, driver="GPKG", layer="pontes")
    meta(out, descricao="pontes do OSM perto das manchas (inventário para conferência em campo)", fora_do_git="sim")
    out = CAMADAS / "ilhas-rede-cotas_osm-sgb_atual_poligonos.gpkg"
    gpd.GeoDataFrame(ilhas_geo, crs=c.CRS_PADRAO, columns=["cota_cm", "cenario", "ilha", "tipo", "enderecos", "pop_estimada", "unidade_dentro", "geometry"]).to_file(out, driver="GPKG", layer="ilhas")
    meta(out, descricao="contorno (buffer de 40 m) das ilhas da rede por cota e cenário", fora_do_git="sim")
    out = CAMADAS / "trechos-criticos_osm-sgb_atual_linhas.gpkg"
    gpd.GeoDataFrame(crit.merge(tr[["eid", "geometry"]], on="eid"), crs=c.CRS_PADRAO).to_file(out, driver="GPKG", layer="criticos")
    meta(out, descricao="trechos críticos (Tarefa 4)", fora_do_git="sim")
    cruz[0].to_file(CAMADAS / "cruzamentos-via-curso-dagua_osm-bho_atual_pontos.gpkg", driver="GPKG", layer="cruzamentos")
    meta(CAMADAS / "cruzamentos-via-curso-dagua_osm-bho_atual_pontos.gpkg", descricao="cruzamentos de via (não ponte) com curso d'água da BHO perto das manchas; com_ponte = ponte do OSM a até 60 m", fora_do_git="sim")

    resumo = {"rede": info_rede, "conferencias": conf, "cruzamentos_sem_ponte": cruz[1], "cotas": K, "tr_anos": TR,
              "unidades_destino": list(unid.loc[unid.destino, "rotulo"]), "unidades_na_mancha": {k: list(unid.loc[unid_na_mancha[k], "rotulo"]) for k in K},
              "sede_distrito": sede}
    arq = TAB / "acessibilidade-resumo-e-conferencias_osm-sgb-cnes-ibge_2022_municipal.json"
    arq.write_text(json.dumps({"produto": arq.name, "status": c.STATUS_CONFERENCIA, "script": SCRIPT, "parametros": PARAMS, **resumo}, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(resumo, ensure_ascii=False, indent=1, default=str))


def inventario_pontes(trechos, manchas, K, intr, dentro_m, limite) -> gpd.GeoDataFrame:
    """Agrupa trechos 'bridge' contíguos numa ponte; curso d'água cruzado pela BHO e pela área de água do OSM; ruas de chegada."""
    import networkx as nx

    b = trechos[trechos.ponte]
    G = nx.Graph()
    for e, r in b.iterrows():
        G.add_edge(r.u, r.v, eid=e)
    perto = manchas[K[-1]].buffer(ARGS.raio_pontes_m)
    hid = gpd.read_file(c.ARQ_BHO, layer="curso_dagua").to_crs(c.CRS_PADRAO)
    eixo = c.rio_principal_linha(limite)
    agua = c.area_agua(limite)
    lin = []
    for comp in nx.connected_components(G):
        eids = sorted({d["eid"] for _, _, d in G.subgraph(comp).edges(data=True)})
        geom = trechos.geometry.loc[eids].union_all()
        if not geom.intersects(perto):
            continue
        nomes = trechos.loc[eids, "name"].dropna()
        # curso d'água: BHO a até 40 m; área de água do OSM com nome; rio principal pelo eixo
        h = hid[hid.intersects(geom.buffer(40))]
        w = agua[agua.intersects(geom)]
        if geom.buffer(40).intersects(eixo) or w.principal.any():
            curso = "rio principal"
        elif len(w) and w.name.notna().any():
            curso = "; ".join(sorted(w.name.dropna().unique()))
        elif len(h):
            curso = "curso d'água BHO " + "; ".join(f"{r.cocursodag} (ordem {int(float(r.nuordemcda))})" for r in h.itertuples())
        elif len(w):
            curso = "área de água do OSM sem nome"
        else:
            curso = "nenhum curso d'água mapeado a até 40 m (viaduto, ou drenagem fora da BHO/OSM)"
        # ruas de chegada: trechos não-ponte que tocam os nós da ponte
        chegada = trechos[~trechos.ponte & (trechos.u.isin(list(comp)) | trechos.v.isin(list(comp)))].index
        r = {"eids": ";".join(map(str, eids)), "via": nomes.mode().iat[0] if len(nomes) else "(sem nome no OSM)",
             "tipo_via": trechos.loc[eids, "highway"].mode().iat[0], "comprimento_m": round(float(trechos.geometry.loc[eids].length.sum()), 0),
             "curso_dagua": curso, "dist_maior_mancha_m": round(float(geom.distance(manchas[K[-1]])), 0), "ruas_de_chegada": len(chegada), "geometry": geom}
        for k in K:
            r[f"ponte_dentro_m_{k}"] = round(float(dentro_m[k].loc[eids].sum()), 1)
            r[f"ponte_interrompida_{k}"] = bool(intr[k].loc[eids].any())
            r[f"chegadas_interrompidas_{k}"] = int(intr[k].loc[chegada].sum())
        lin.append(r)
    p = gpd.GeoDataFrame(lin, crs=c.CRS_PADRAO)
    # numeração estável: menor cota que interrompe a ponte, depois de oeste para leste
    p["_k"] = [next((k for k in K if r[f"ponte_interrompida_{k}"]), 99999) for _, r in p.iterrows()]
    p["_x"] = p.geometry.centroid.x
    p = p.sort_values(["_k", "dist_maior_mancha_m", "_x"]).drop(columns=["_k", "_x"]).reset_index(drop=True)
    p.insert(0, "ponte", [f"P{i + 1:02d}" for i in range(len(p))])
    return p


def cruzamentos_sem_ponte(trechos, mancha_max, limite):
    """Cruzamentos de via (não ponte) com curso d'água da BHO a até raio_pontes_m da maior mancha; com ponte do OSM a até 60 m?"""
    hid = gpd.read_file(c.ARQ_BHO, layer="curso_dagua").to_crs(c.CRS_PADRAO)
    perto = mancha_max.buffer(ARGS.raio_pontes_m).intersection(limite.union_all())
    hid = hid[hid.intersects(perto)]
    eixo = c.rio_principal_linha(limite)
    vias = trechos[~trechos.ponte & trechos.intersects(perto)]
    pts = []
    for e, r in vias.iterrows():
        for h in hid[hid.intersects(r.geometry)].itertuples():
            x = r.geometry.intersection(h.geometry)
            for g in getattr(x, "geoms", [x]):
                if g.geom_type == "Point" and g.within(perto):
                    pts.append({"eid": e, "via": r.nome_via, "tipo_via": r.highway, "curso_bho": h.cocursodag, "ordem": int(float(h.nuordemcda)),
                                "rio_principal": bool(h.geometry.distance(eixo) < 1), "geometry": g})
    g = gpd.GeoDataFrame(pts, crs=c.CRS_PADRAO, columns=["eid", "via", "tipo_via", "curso_bho", "ordem", "rio_principal", "geometry"])
    # cruzamentos a menos de 30 m entre si = o mesmo (vias duplicadas ou nós)
    if len(g):
        cl = g.buffer(15).union_all()
        cl = list(getattr(cl, "geoms", [cl]))
        g["grupo"] = [next(i for i, poly in enumerate(cl) if poly.contains(p)) for p in g.geometry]
        g = g.drop_duplicates("grupo").drop(columns="grupo")
        pontes = trechos[trechos.ponte].geometry.union_all()
        g["com_ponte"] = g.geometry.distance(pontes) <= 60
    info = {"area": f"até {ARGS.raio_pontes_m:g} m da maior mancha, dentro do município", "cruzamentos_via_curso_dagua_bho": len(g),
            "com_ponte_osm_a_ate_60m": int(g.com_ponte.sum()) if len(g) else 0, "sem_ponte_marcada": int((~g.com_ponte).sum()) if len(g) else 0,
            "sem_ponte_por_ordem_do_curso": g[~g.com_ponte].ordem.value_counts().sort_index().to_dict() if len(g) else {},
            "ressalva": "a BHO é derivada de modelo de terreno e não coincide exatamente com o traçado real; cruzamento sem ponte marcada pode ser bueiro, ponte não mapeada no OSM ou desalinhamento da BHO"}
    return g, info


def trechos_criticos(rede, end, trechos, intr, K, D0, P0, PS0, pontes) -> pd.DataFrame:
    """Fluxo de pessoas na árvore de caminhos mínimos da base, por trecho original (união dos pedaços)."""
    n = rede.n
    pop_no = np.zeros(n)
    np.add.at(pop_no, end._no.values, end["pop"].values)
    ordem = np.argsort(-np.nan_to_num(D0, posinf=-1))
    sub = pop_no.copy()
    for x in ordem:
        if np.isfinite(D0[x]) and P0[x] >= 0:
            sub[P0[x]] += sub[x]
    filhos = defaultdict(set)
    for x in range(n):
        if P0[x] >= 0 and rede.se[PS0[x]] >= 0:
            filhos[rede.se[PS0[x]]].add(x)

    def fluxo(eid):
        C = filhos.get(eid, set())
        return float(sum(sub[x] for x in C if P0[x] not in C))

    cota_entra = pd.Series(np.select([intr[k].values for k in K], K, default=0), index=trechos.index)
    ponte_de = {int(e): p.ponte for p in pontes.itertuples() for e in str(p.eids).split(";")}
    lin = []
    for k in ARGS.cotas_criticas:
        cand = trechos.index[intr[k]]
        f = pd.Series({e: fluxo(e) for e in cand}).sort_values(ascending=False).head(ARGS.n_criticos)
        for pos, (e, v) in enumerate(f.items(), 1):
            lin.append({"cota_cm": k, "posicao": pos, "eid": int(e), "via": trechos.nome_via.iat[e], "tipo_via": trechos.highway.iat[e],
                        "cota_em_que_entra_na_mancha": int(cota_entra.iat[e]), "ponte": bool(trechos.ponte.iat[e]), "ponte_id": ponte_de.get(int(e), ""),
                        "comprimento_m": round(float(trechos.geometry.iat[e].length), 0), "pop_caminho_base": round(v, 1)})
    return pd.DataFrame(lin)


if __name__ == "__main__":
    _p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    _p.add_argument("--codigo-ibge", default=c.CODIGO_IBGE_DEFAULT)
    _p.add_argument("--limiar-m", type=float, default=5.0, help="extensão mínima dentro da mancha para o trecho contar como interrompido")
    _p.add_argument("--limiares-sensibilidade", type=float, nargs="*", default=[0.0, 20.0])
    _p.add_argument("--dist-max-via-m", type=float, default=100.0, help="acima disto o endereço entra ligado ao nó mais próximo")
    _p.add_argument("--raio-acesso-unidade-m", type=float, default=200.0)
    _p.add_argument("--raio-pontes-m", type=float, default=300.0)
    _p.add_argument("--cotas-criticas", type=int, nargs="*", default=[1205, 1252])
    _p.add_argument("--n-criticos", type=int, default=15)
    ARGS = _p.parse_args()
    PARAMS = {k: v for k, v in vars(ARGS).items()}
    main()
