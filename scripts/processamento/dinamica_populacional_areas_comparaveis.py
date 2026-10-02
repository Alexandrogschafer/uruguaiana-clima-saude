"""
Tarefa 3 — mudança 2010–2022 dentro do município por ÁREAS COMPARÁVEIS.
Tarefa 4 — verificação da comparabilidade dos setores de 2000 (sem produto
se não for seguro).

Áreas comparáveis (método recomendado pelo IBGE no leia-me de comparabilidade
2010–2022): componentes conexos do grafo setor2010 <-> setor2022 montado com
o histórico OFICIAL de formação dos setores 2010–2022. Cada área reúne todos
os setores de 2010 e de 2022 que cobrem o mesmo território.
  - contagens: SOMENTE das tabelas do Censo (agregados por setor 2010 e 2022);
  - geometria: SOMENTE da malha 2022 (união dos setores 2022 da área);
  - NÃO se usa grade 2010 nem sobreposição geométrica entre malhas 2010/2022.

Tratamentos declarados:
  - setor de 2010 presente na malha e no histórico mas sem linha nos agregados:
    o IBGE omite das tabelas setores sem moradores; conta como 0 pessoas e 0
    domicílios (a soma dos setores com linha já fecha com o total municipal);
  - setor só com domicílio coletivo (pessoas > 0 e nenhum domicílio
    particular): população entra; domicílios = 0; razões por domicílio ficam
    ausentes se a área inteira não tiver domicílio particular;
  - sigilo "X" (2022): % 0–14 / % 60+ da área ficam ausentes se qualquer
    setor da área tiver parcela suprimida.

Classes de mudança da população (limiares fixos, declarados): perda forte
(< −20 %), perda (−20 a −5 %), estável (−5 a +5 %), ganho (+5 a +20 %), ganho
forte (> +20 %). Sensibilidade: quantas áreas mudam de classe com ±10 %.

Deslocamento: centro médio ponderado pela população (centróide de cada área,
geometria 2022, pesos 2010 e 2022), distância-padrão, população por faixa de
distância ao centro da cidade e por quadrante. Centro da cidade: centróide dos
setores 2022 do bairro oficial do IBGE informado em --bairro-centro (padrão
"Centro"), no distrito-sede.

Uso:
  python scripts/processamento/dinamica_populacional_areas_comparaveis.py --codigo-ibge 4322400
"""

from __future__ import annotations

import argparse
import json
import logging
import math

import geopandas as gpd
import networkx as nx
import numpy as np
import pandas as pd

import dinamica_populacional_comum as c

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

LIM = {"forte": 20.0, "fraco": 5.0}
FAIXAS_KM = [0, 1, 2, 3, 5, 10, 1e9]
ROT_FAIXAS = ["0–1 km", "1–2 km", "2–3 km", "3–5 km", "5–10 km", "> 10 km"]
CLASSES = ["perda forte", "perda", "estável", "ganho", "ganho forte"]


def classe(v: float, forte: float = LIM["forte"], fraco: float = LIM["fraco"]) -> str | None:
    if v is None or not np.isfinite(v):
        return None
    if v < -forte:
        return "perda forte"
    if v < -fraco:
        return "perda"
    if v <= fraco:
        return "estável"
    if v <= forte:
        return "ganho"
    return "ganho forte"


def direcao(dx: float, dy: float) -> str:
    ang = (math.degrees(math.atan2(dx, dy)) + 360) % 360  # azimute a partir do norte
    rot = ["N", "NE", "L", "SE", "S", "SO", "O", "NO"]
    return f"{rot[int((ang + 22.5) // 45) % 8]} (azimute {ang:.0f}°)"


def montar_areas(cod: str):
    h = c.historico_setores(cod)
    a, b = "GEOCODIGO_2022_DIVULGAÇÃO", "GEOCODIGO_2010"
    G = nx.Graph()
    for s22, s10 in zip(h[a], h[b]):
        G.add_edge("22_" + s22, "10_" + s10)
    linhas = []
    for comp in nx.connected_components(G):
        s10 = sorted(n[3:] for n in comp if n.startswith("10_"))
        s22 = sorted(n[3:] for n in comp if n.startswith("22_"))
        linhas.append({"s10": s10, "s22": s22})
    areas = pd.DataFrame(linhas)
    areas = areas.sort_values(by="s22", key=lambda s: s.str[0]).reset_index(drop=True)
    areas["id_area"] = [f"AC{i + 1:03d}" for i in range(len(areas))]
    areas["n_setores_2010"] = areas.s10.str.len()
    areas["n_setores_2022"] = areas.s22.str.len()
    areas["tipo"] = areas.n_setores_2010.astype(str) + ":" + areas.n_setores_2022.astype(str)
    return areas, h


def dados_2010(cod: str) -> pd.DataFrame:
    p3 = c.agregados_2010("Pessoa03", cod, ["V001"]).rename(columns={"V001": "pop"})
    b = c.agregados_2010("Basico", cod, ["V001", "V002"]).rename(columns={"V001": "dpp", "V002": "mor_dpp"})
    ida = [f"V{n:03d}" for n in [22] + list(range(35, 49)) + list(range(94, 135))]
    p13 = c.agregados_2010("Pessoa13", cod, ida)
    p13["pop_0_14"] = p13[[f"V{n:03d}" for n in [22] + list(range(35, 49))]].sum(axis=1, min_count=15)
    p13["pop_60_mais"] = p13[[f"V{n:03d}" for n in range(94, 135)]].sum(axis=1, min_count=41)
    d = p3.merge(b, on="CD_SETOR", how="left").merge(p13[["CD_SETOR", "pop_0_14", "pop_60_mais"]], on="CD_SETOR", how="left")
    d["so_coletivo"] = (d["pop"] > 0) & d.dpp.isna()
    d[["dpp", "mor_dpp"]] = d[["dpp", "mor_dpp"]].fillna(0)  # sem domicílio particular -> 0 (soma fecha com SIDRA)
    return d


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--codigo-ibge", default=c.CODIGO_IBGE_DEFAULT)
    p.add_argument("--bairro-centro", default="Centro", help="nome do bairro IBGE 2022 usado como centro da cidade")
    a = p.parse_args()
    cod = a.codigo_ibge
    c.garantir_pastas()
    base = {"codigo_ibge": cod, "crs": c.CRS_PADRAO, "status": c.STATUS_CONFERENCIA, "ligado_ao_portal": False,
            "script": "scripts/processamento/dinamica_populacional_areas_comparaveis.py"}

    areas, hist = montar_areas(cod)
    st22 = gpd.read_file(c.CAMADAS / "populacao-setores_ibge-censo_2022_setor.gpkg")
    malha10 = gpd.read_file(c.RAW / "vetor" / "setores-censitarios_ibge_2010_vetorial.gpkg")
    d10 = dados_2010(cod)

    # checagens de cobertura do histórico
    set22, set10 = set(st22.CD_SETOR), set(malha10.cd_setor)
    hist22, hist10 = set(hist["GEOCODIGO_2022_DIVULGAÇÃO"]), set(hist.GEOCODIGO_2010)
    sem_linha_2010 = sorted(set10 - set(d10.CD_SETOR))
    cob = {"setores_2022_malha": len(set22), "setores_2022_no_historico": len(set22 & hist22),
           "setores_2010_malha": len(set10), "setores_2010_no_historico": len(set10 & hist10),
           "codigos_do_historico_fora_das_malhas": sorted((hist22 - set22) | (hist10 - set10)),
           "setores_2010_sem_linha_nos_agregados": sem_linha_2010,
           "setores_2010_so_domicilio_coletivo": d10.loc[d10.so_coletivo, "CD_SETOR"].tolist()}

    s10 = d10.set_index("CD_SETOR")
    s22 = st22.set_index("CD_SETOR")
    s22_sig = (s22.pop_0_14.isna() | s22.pop_60_mais.isna()) & (s22["pop"] > 0)
    reg = []
    for r in areas.itertuples():
        x10 = s10.reindex(r.s10)  # setores sem linha -> NaN -> 0 pessoas (ver docstring)
        x22 = s22.loc[r.s22]
        pop10, pop22 = float(x10["pop"].fillna(0).sum()), float(x22["pop"].sum())
        dom10, dom22 = float(x10.dpp.fillna(0).sum()), float(x22.dppo.fillna(0).sum())
        i10 = x10[x10["pop"].fillna(0) > 0]
        i22 = x22[x22["pop"] > 0]
        reg.append({
            "id_area": r.id_area, "tipo": r.tipo, "n_setores_2010": r.n_setores_2010, "n_setores_2022": r.n_setores_2022,
            "setores_2010": ";".join(r.s10), "setores_2022": ";".join(r.s22),
            "pop_2010": pop10, "pop_2022": pop22, "dom_2010": dom10, "dom_2022": dom22,
            "pop_0_14_2010": i10.pop_0_14.sum(min_count=1) if i10.pop_0_14.notna().all() else np.nan,
            "pop_60_2010": i10.pop_60_mais.sum(min_count=1) if i10.pop_60_mais.notna().all() else np.nan,
            "pop_0_14_2022": i22.pop_0_14.sum(min_count=1) if i22.pop_0_14.notna().all() else np.nan,
            "pop_60_2022": i22.pop_60_mais.sum(min_count=1) if i22.pop_60_mais.notna().all() else np.nan,
            "setores_2010_sem_linha": sum(s in sem_linha_2010 for s in r.s10),
            "setores_2010_so_coletivo": int(x10.so_coletivo.fillna(False).sum()),
            "setores_2022_com_sigilo_idade": int(s22_sig.loc[r.s22].sum()),
            "situacao_2022": "/".join(sorted(set(x22.SITUACAO))), "distrito_2022": "/".join(sorted(set(x22.NM_DIST))),
            "bairros_2022": "/".join(x22.groupby("NM_BAIRRO", dropna=True)["pop"].sum().sort_values(ascending=False).index.tolist()),
        })
    df = pd.DataFrame(reg)
    geo = st22.dissolve(by=st22.CD_SETOR.map({s: r.id_area for r in areas.itertuples() for s in r.s22}))[["geometry"]]
    g = gpd.GeoDataFrame(df.merge(geo, left_on="id_area", right_index=True), geometry="geometry", crs=c.CRS_PADRAO)
    g["area_ha"] = g.geometry.area / 1e4
    g["var_pop_abs"] = g.pop_2022 - g.pop_2010
    g["var_pop_pct"] = 100 * g.var_pop_abs / g.pop_2010.replace(0, np.nan)
    g["var_dom_abs"] = g.dom_2022 - g.dom_2010
    g["var_dom_pct"] = 100 * g.var_dom_abs / g.dom_2010.replace(0, np.nan)
    g["dens_2010_hab_ha"] = g.pop_2010 / g.area_ha
    g["dens_2022_hab_ha"] = g.pop_2022 / g.area_ha
    for ano in ("2010", "2022"):
        g[f"pct_0_14_{ano}"] = 100 * g[f"pop_0_14_{ano}"] / g[f"pop_{ano}"].replace(0, np.nan)
        g[f"pct_60_{ano}"] = 100 * g[f"pop_60_{ano}"] / g[f"pop_{ano}"].replace(0, np.nan)
    g["classe_mudanca"] = g.var_pop_pct.map(classe)
    g["classe_mudanca_lim10"] = g.var_pop_pct.map(lambda v: classe(v, forte=20.0, fraco=10.0))
    # sem população em 2010 -> % indefinido; classe ganho forte se há população em 2022
    novo = (g.pop_2010 == 0) & (g.pop_2022 > 0)
    g.loc[novo, ["classe_mudanca", "classe_mudanca_lim10"]] = "ganho forte"
    g.loc[(g.pop_2010 == 0) & (g.pop_2022 == 0), ["classe_mudanca", "classe_mudanca_lim10"]] = "sem população"

    # fechamento
    fech = {"pop_2010_soma_areas": float(g.pop_2010.sum()), "pop_2022_soma_areas": float(g.pop_2022.sum()),
            "dom_2010_soma_areas": float(g.dom_2010.sum()), "dom_2022_soma_areas": float(g.dom_2022.sum())}
    sid = pd.read_csv(c.TABELAS / "populacao-sexo-situacao_ibge-censo_2000-2022_municipal.csv").set_index("ano")
    domt = pd.read_csv(c.TABELAS / "domicilios_ibge-censo_2000-2022_municipal.csv").set_index("ano")
    fech.update({"pop_2010_sidra": float(sid.loc[2010, "total"]), "pop_2022_sidra": float(sid.loc[2022, "total"]),
                 "dom_2010_sidra": float(domt.loc[2010, "domicilios"]), "dom_2022_sidra": float(domt.loc[2022, "domicilios"])})
    for k in ("pop_2010", "pop_2022", "dom_2010", "dom_2022"):
        fech[f"dif_{k}"] = fech[f"{k}_soma_areas"] - fech[f"{k}_sidra"]

    # ----- deslocamento
    cen = g.geometry.centroid
    g["cen_x"], g["cen_y"] = cen.x, cen.y
    centro_cidade = st22[(st22.NM_BAIRRO == a.bairro_centro) & (st22.CD_DIST == st22.loc[st22["pop"].idxmax(), "CD_DIST"])]
    if centro_cidade.empty:
        raise SystemExit(f"Bairro '{a.bairro_centro}' não encontrado nos setores 2022")
    cc = centro_cidade.union_all().centroid
    desl = {"centro_da_cidade": {"definicao": f"centróide dos setores 2022 do bairro IBGE '{a.bairro_centro}' no distrito-sede",
                                 "x": cc.x, "y": cc.y}}
    for ano in ("2010", "2022"):
        w = g[f"pop_{ano}"]
        mx, my = (g.cen_x * w).sum() / w.sum(), (g.cen_y * w).sum() / w.sum()
        sd = math.sqrt((w * ((g.cen_x - mx) ** 2 + (g.cen_y - my) ** 2)).sum() / w.sum())
        desl[ano] = {"centro_medio_x": mx, "centro_medio_y": my, "distancia_padrao_m": sd,
                     "dist_centro_medio_ao_centro_cidade_m": math.hypot(mx - cc.x, my - cc.y)}
    dx = desl["2022"]["centro_medio_x"] - desl["2010"]["centro_medio_x"]
    dy = desl["2022"]["centro_medio_y"] - desl["2010"]["centro_medio_y"]
    desl["deslocamento_2010_2022"] = {"distancia_m": math.hypot(dx, dy), "direcao": direcao(dx, dy), "dx_m": dx, "dy_m": dy}
    # repete só com a área urbana do distrito-sede (o rural, com áreas enormes, pesa no centróide)
    urb = g[g.situacao_2022.str.contains("Urbana") & ~g.situacao_2022.str.contains("Rural")]
    for ano in ("2010", "2022"):
        w = urb[f"pop_{ano}"]
        mx, my = (urb.cen_x * w).sum() / w.sum(), (urb.cen_y * w).sum() / w.sum()
        desl[f"{ano}_so_areas_urbanas"] = {"centro_medio_x": mx, "centro_medio_y": my,
                                           "distancia_padrao_m": math.sqrt((w * ((urb.cen_x - mx) ** 2 + (urb.cen_y - my) ** 2)).sum() / w.sum())}
    dxu = desl["2022_so_areas_urbanas"]["centro_medio_x"] - desl["2010_so_areas_urbanas"]["centro_medio_x"]
    dyu = desl["2022_so_areas_urbanas"]["centro_medio_y"] - desl["2010_so_areas_urbanas"]["centro_medio_y"]
    desl["deslocamento_2010_2022_so_areas_urbanas"] = {"distancia_m": math.hypot(dxu, dyu), "direcao": direcao(dxu, dyu)}

    g["dist_centro_km"] = np.hypot(g.cen_x - cc.x, g.cen_y - cc.y) / 1000
    g["faixa_dist"] = pd.cut(g.dist_centro_km, FAIXAS_KM, labels=ROT_FAIXAS, right=False)
    ang = (np.degrees(np.arctan2(g.cen_x - cc.x, g.cen_y - cc.y)) + 360) % 360
    g["quadrante"] = np.select([ang < 90, ang < 180, ang < 270], ["NE", "SE", "SO"], "NO")
    g["direcao_do_centro"] = [direcao(x - cc.x, y - cc.y) for x, y in zip(g.cen_x, g.cen_y)]
    por_faixa = g.groupby("faixa_dist", observed=False)[["pop_2010", "pop_2022"]].sum()
    por_quad = g.groupby("quadrante")[["pop_2010", "pop_2022"]].sum()
    for t in (por_faixa, por_quad):
        t["var_abs"] = t.pop_2022 - t.pop_2010
        t["var_pct"] = 100 * t.var_abs / t.pop_2010
        t["pct_do_total_2010"] = 100 * t.pop_2010 / t.pop_2010.sum()
        t["pct_do_total_2022"] = 100 * t.pop_2022 / t.pop_2022.sum()
    dist_med = {ano: float((g.dist_centro_km * g[f"pop_{ano}"]).sum() / g[f"pop_{ano}"].sum()) for ano in ("2010", "2022")}
    desl["distancia_media_ao_centro_km_ponderada"] = dist_med

    # ----- nome de referência: bairro IBGE dominante + logradouro mais frequente no CNEFE (pontos da área)
    cnefe = gpd.read_file(c.CAMADAS / "enderecos-domicilios_ibge-cnefe_2022_pontos.gpkg", columns=["setor_2022", "NOM_TIPO_SEGLOGR", "NOM_TITULO_SEGLOGR", "NOM_SEGLOGR"])
    cnefe["logradouro"] = cnefe[["NOM_TIPO_SEGLOGR", "NOM_TITULO_SEGLOGR", "NOM_SEGLOGR"]].fillna("").agg(" ".join, axis=1).str.split().str.join(" ")
    mapa_area = {s: r.id_area for r in areas.itertuples() for s in r.s22}
    cnefe["id_area"] = cnefe.setor_2022.map(mapa_area)
    # descarta nomes provisórios com código numérico longo (ex.: "RUA 6 43 22400 05 00 0055")
    valido = (cnefe.logradouro != "") & ~cnefe.logradouro.str.contains(r"\d{5}|\d+ \d+ \d{4,}", regex=True)
    logr = cnefe[valido].groupby("id_area").logradouro.agg(lambda s: s.value_counts().index[0])
    g["logradouro_principal_cnefe"] = g.id_area.map(logr)
    nome_base = g.bairros_2022.replace("", np.nan).str.split("/").str[0].fillna("distrito " + g.distrito_2022)
    g["referencia"] = nome_base + np.where(g.logradouro_principal_cnefe.notna(), " — " + g.logradouro_principal_cnefe.fillna(""), "")

    # ----- gravação
    col_out = ["id_area", "tipo", "n_setores_2010", "n_setores_2022", "setores_2010", "setores_2022", "situacao_2022", "distrito_2022",
               "bairros_2022", "referencia", "logradouro_principal_cnefe", "area_ha", "pop_2010", "pop_2022", "var_pop_abs", "var_pop_pct",
               "dom_2010", "dom_2022", "var_dom_abs", "var_dom_pct", "dens_2010_hab_ha", "dens_2022_hab_ha", "pct_0_14_2010", "pct_0_14_2022",
               "pct_60_2010", "pct_60_2022", "classe_mudanca", "classe_mudanca_lim10", "setores_2010_sem_linha", "setores_2010_so_coletivo",
               "setores_2022_com_sigilo_idade", "dist_centro_km", "faixa_dist", "quadrante", "direcao_do_centro", "geometry"]
    g = g[col_out]
    g["faixa_dist"] = g.faixa_dist.astype(str)
    out = c.CAMADAS / "populacao-mudanca_ibge-censo_2010-2022_area-comparavel.gpkg"
    g.to_file(out, driver="GPKG", layer="areas_comparaveis_2010_2022")
    resumo_tipos = g.tipo.value_counts().to_dict()
    cls = g.classe_mudanca.value_counts().reindex(CLASSES + ["sem população"]).fillna(0).astype(int).to_dict()
    cls10 = g.classe_mudanca_lim10.value_counts().reindex(CLASSES + ["sem população"]).fillna(0).astype(int).to_dict()
    muda = int((g.classe_mudanca != g.classe_mudanca_lim10).sum())
    meta_comum = dict(**base,
                      fonte="IBGE — Histórico de formação dos setores censitários 2010–2022 (correspondência oficial); Agregados por setor 2010 (Pessoa03 V001, Basico V001, Pessoa13) e 2022 (básico, demografia, características do domicílio 1); malha de setores 2022",
                      metodo="componentes conexos setor2010<->setor2022 do histórico oficial; contagens das tabelas; geometria = união dos setores 2022; sem grade 2010 e sem sobreposição entre malhas",
                      n_areas=len(g), tipos_setores2010_setores2022=resumo_tipos, cobertura_historico=cob, fechamento_com_total_municipal=fech,
                      limiares_classes={"perda forte": "< -20 %", "perda": "-20 a -5 %", "estável": "-5 a +5 %", "ganho": "+5 a +20 %", "ganho forte": "> +20 %",
                                        "nota": "área sem população em 2010 e com população em 2022 = ganho forte; % indefinido"},
                      contagem_por_classe=cls, contagem_por_classe_limiar_10=cls10, areas_que_mudam_de_classe_com_10pct=muda)
    c.gravar_meta(out, **meta_comum, deslocamento=desl)
    g.drop(columns="geometry").to_csv(c.TABELAS / "populacao-mudanca_ibge-censo_2010-2022_area-comparavel.csv", index=False)
    c.gravar_meta(c.TABELAS / "populacao-mudanca_ibge-censo_2010-2022_area-comparavel.csv", **meta_comum)
    por_faixa.reset_index().to_csv(c.TABELAS / "populacao-faixa-distancia-centro_ibge-censo_2010-2022_area-comparavel.csv", index=False)
    c.gravar_meta(c.TABELAS / "populacao-faixa-distancia-centro_ibge-censo_2010-2022_area-comparavel.csv", **base,
                  descricao="população por faixa de distância (centróide da área comparável) ao centro da cidade", centro=desl["centro_da_cidade"])
    por_quad.reset_index().to_csv(c.TABELAS / "populacao-quadrante_ibge-censo_2010-2022_area-comparavel.csv", index=False)
    c.gravar_meta(c.TABELAS / "populacao-quadrante_ibge-censo_2010-2022_area-comparavel.csv", **base,
                  descricao="população por quadrante (NE/SE/SO/NO) em relação ao centro da cidade, pelo centróide da área comparável", centro=desl["centro_da_cidade"])
    (c.TABELAS / "deslocamento-centro-medio_ibge-censo_2010-2022_municipal.json").write_text(
        json.dumps({**base, "deslocamento": desl}, ensure_ascii=False, indent=2), encoding="utf-8")

    top = g.dropna(subset=["var_pop_abs"])
    cols_top = ["id_area", "tipo", "referencia", "situacao_2022", "pop_2010", "pop_2022", "var_pop_abs", "var_pop_pct", "classe_mudanca", "dist_centro_km", "direcao_do_centro"]
    ganho = top.sort_values("var_pop_abs", ascending=False).head(10)[cols_top]
    perda = top.sort_values("var_pop_abs").head(10)[cols_top]
    pd.concat([ganho.assign(lista="10 maiores ganhos"), perda.assign(lista="10 maiores perdas")]).to_csv(
        c.TABELAS / "populacao-mudanca-top10_ibge-censo_2010-2022_area-comparavel.csv", index=False)
    c.gravar_meta(c.TABELAS / "populacao-mudanca-top10_ibge-censo_2010-2022_area-comparavel.csv", **base,
                  descricao="10 áreas comparáveis com maior ganho e 10 com maior perda absoluta de população; referência = bairro IBGE 2022 dominante + logradouro mais frequente no CNEFE 2022")

    print(json.dumps({"cob": cob, "tipos": resumo_tipos, "fech": fech, "classes": cls, "classes10": cls10, "muda": muda, "desl": desl}, ensure_ascii=False, indent=1, default=str))
    print(por_faixa.round(1).to_string()); print(por_quad.round(1).to_string())
    print(ganho.round(1).to_string()); print(perda.round(1).to_string())
    print(g.groupby("tipo")["area_ha"].describe().round(1).to_string())
    print(g[["setores_2010_sem_linha", "setores_2010_so_coletivo", "setores_2022_com_sigilo_idade"]].sum().to_dict())
    print("areas sem % idade 2010:", int(g.pct_60_2010.isna().sum()), " 2022:", int(g.pct_60_2022.isna().sum()), int(g.pct_0_14_2022.isna().sum()))


if __name__ == "__main__":
    main()
