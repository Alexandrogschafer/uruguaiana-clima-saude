"""
Tarefa 2 — retrato de 2022 dentro do município (camadas GeoPackage).

  a) setores 2022: população, domicílios, densidade (hab/ha), moradores por
     domicílio, % 60+, % 0–14, % de domicílios com um só morador;
  b) grade estatística 2022 recortada ao município (pop. e domicílios por célula);
  c) CNEFE 2022: pontos dos endereços de domicílios particulares (espécie 1),
     com o código do setor, e contagem por setor conferida contra os agregados.

Decisões:
  - CRS de trabalho EPSG:31981; área do setor recalculada da geometria (ha).
  - Sigilo "X" = ausente (NaN), nunca zero. Indicador com qualquer parcela
    ausente fica ausente; o .json registra quantos setores e quanta população
    ficam sem valor em cada indicador.
  - Grade: célula entra se o CENTRÓIDE cai no município (células inteiras,
    sem fracionar contagens; as de borda são poucas e majoritariamente vazias).
  - CNEFE: o arquivo traz o código do setor da malha PRELIMINAR (sufixo "P").
    O setor da malha de divulgação vem da ligação OFICIAL preliminar ->
    divulgação (histórico de formação dos setores); só quando o setor
    preliminar foi dividido depois usa-se ponto-no-polígono para escolher a
    parte (ponto-no-polígono puro erra ~4 % dos pontos, que caem sobre a via
    que é limite entre setores). Na camada
    ficam só campos de localização e classificação (sem número/complemento).
  - Produtos com status "pendente de conferência"; fora de data/geoportal/.

Uso:
  python scripts/processamento/dinamica_populacional_2022.py --codigo-ibge 4322400
"""

from __future__ import annotations

import argparse
import logging
import zipfile

import geopandas as gpd
import numpy as np
import pandas as pd

import dinamica_populacional_comum as c

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

MALHA_2022 = c.RAW / "vetor" / "setores-censitarios_ibge_2022_vetorial.gpkg"
GRADE_ZIP = c.CACHE_DP / "grade_2022" / "grade_id13.zip"


def setores_2022(cod: str) -> tuple[gpd.GeoDataFrame, dict]:
    malha = gpd.read_file(MALHA_2022)
    malha = malha[malha.CD_MUN == cod].to_crs(c.CRS_PADRAO)
    cols = ["CD_SETOR", "SITUACAO", "CD_SIT", "CD_TIPO", "CD_DIST", "NM_DIST", "CD_BAIRRO", "NM_BAIRRO", "geometry"]
    g = malha[cols].copy()
    g["area_ha"] = g.geometry.area / 1e4

    b = c.agregados_2022("basico", cod, ["V0001", "V0002", "V0003", "V0007"])
    d = c.agregados_2022("demografia", cod, ["V01006"] + [f"V0{n}" for n in range(1031, 1042)])
    h = c.agregados_2022("caracteristicas_domicilio1", cod, ["V00001", "V00005", "V00017"])
    g = g.merge(b, on="CD_SETOR", how="left").merge(d, on="CD_SETOR", how="left").merge(h, on="CD_SETOR", how="left")

    g["pop"] = g.V0001
    g["dom_particulares"] = g.V0003          # DPPO + DPPV + DPPUO + DPIO (base de conferência do CNEFE)
    g["dom_ocupados"] = g.V0007             # DPPO + DPIO
    g["dppo"] = g.V00001
    g["dens_hab_ha"] = g["pop"] / g.area_ha
    g["mor_por_dom"] = g.V00005 / g.V00001  # moradores em DPPO / DPPO
    g["pop_0_14"] = g[["V01031", "V01032", "V01033"]].sum(axis=1, min_count=3)
    g.loc[g[["V01031", "V01032", "V01033"]].isna().any(axis=1), "pop_0_14"] = np.nan
    g["pop_60_mais"] = g[["V01040", "V01041"]].sum(axis=1, min_count=2)
    g.loc[g[["V01040", "V01041"]].isna().any(axis=1), "pop_60_mais"] = np.nan
    g["pct_0_14"] = 100 * g.pop_0_14 / g.V01006
    g["pct_60_mais"] = 100 * g.pop_60_mais / g.V01006
    g["dom_1_morador"] = g.V00017
    g["pct_dom_1_morador"] = 100 * g.V00017 / g.V00001
    # setores sem população: razões indefinidas -> NaN (não é sigilo)
    for col in ("mor_por_dom", "pct_0_14", "pct_60_mais", "pct_dom_1_morador"):
        g.loc[~np.isfinite(g[col]), col] = np.nan

    cobertura = {}
    for col, brutos in {
        "pop": ["V0001"], "dom_ocupados": ["V0007"], "dens_hab_ha": ["V0001"], "mor_por_dom": ["V00001", "V00005"],
        "pct_0_14": ["V01031", "V01032", "V01033"], "pct_60_mais": ["V01040", "V01041"], "pct_dom_1_morador": ["V00001", "V00017"],
    }.items():
        sem = g[col].isna()
        sigilo = g[[f"{x}_bruto" for x in brutos]].eq("X").any(axis=1)
        cobertura[col] = {
            "setores_sem_valor": int(sem.sum()),
            "dos_quais_por_sigilo_X": int((sem & sigilo).sum()),
            "dos_quais_setor_sem_moradores": int((sem & (g["pop"].fillna(0) == 0)).sum()),
            "populacao_nos_setores_sem_valor": float(g.loc[sem, "pop"].sum()),
            "pct_populacao_sem_valor": round(100 * float(g.loc[sem, "pop"].sum()) / float(g["pop"].sum()), 2),
        }
    manter = ["CD_SETOR", "SITUACAO", "CD_SIT", "CD_TIPO", "CD_DIST", "NM_DIST", "CD_BAIRRO", "NM_BAIRRO", "area_ha", "pop",
              "dom_particulares", "dom_ocupados", "dppo", "dens_hab_ha", "mor_por_dom", "pop_0_14", "pct_0_14",
              "pop_60_mais", "pct_60_mais", "dom_1_morador", "pct_dom_1_morador", "geometry"]
    return g[manter], cobertura


def grade_2022(cod: str, area: gpd.GeoDataFrame) -> tuple[gpd.GeoDataFrame, dict]:
    bbox = tuple(area.to_crs("EPSG:4674").total_bounds)
    gr = gpd.read_file(f"zip://{GRADE_ZIP}!grade_id13.shp", bbox=bbox).to_crs(c.CRS_PADRAO)
    if gr.empty:
        raise RuntimeError("Grade vazia no recorte — conferir --grade-id do download")
    cent = gr.geometry.centroid
    dentro = cent.within(area.union_all())
    g = gr[dentro].copy()
    g["resolucao"] = np.where(g.ID_UNICO.str.startswith("200M"), "200 m", "1 km")
    g = g.rename(columns={"TOTAL": "pop", "TOTAL_DOM": "dom"})[["ID_UNICO", "resolucao", "pop", "dom", "geometry"]]
    g["pop"] = pd.to_numeric(g["pop"], errors="coerce")
    g["dom"] = pd.to_numeric(g["dom"], errors="coerce")
    fora = gr[~dentro]
    info = {"celulas_no_recorte_bbox": len(gr), "celulas_no_municipio": len(g),
            "celulas_por_resolucao": g.resolucao.value_counts().to_dict(),
            "pop_total_celulas": float(g["pop"].sum()), "dom_total_celulas": float(g["dom"].sum()),
            "celulas_fora_com_pop_que_tocam_o_municipio": int(((fora["TOTAL"].astype(float) > 0) & fora.intersects(area.union_all())).sum()),
            "pop_em_celulas_fora_que_tocam_o_municipio": float(fora.loc[fora.intersects(area.union_all()), "TOTAL"].astype(float).sum())}
    return g, info


def cnefe_2022(cod: str, setores: gpd.GeoDataFrame) -> tuple[gpd.GeoDataFrame, pd.DataFrame, dict]:
    z = [p for p in (c.CACHE_DP / "cnefe_2022").glob(f"{cod}_*.zip")][0]
    with zipfile.ZipFile(z) as zf:
        df = pd.read_csv(zf.open([m for m in zf.namelist() if m.lower().endswith(".csv")][0]), sep=";", dtype=str)
    n_total = len(df)
    df = df[df.COD_ESPECIE == "1"].copy()  # 1 = domicílio particular
    df["setor_preliminar"] = df.COD_SETOR.str[:15]
    pts = gpd.GeoDataFrame(
        df[["COD_UNICO_ENDERECO", "setor_preliminar", "NUM_QUADRA", "NUM_FACE", "NOM_TIPO_SEGLOGR", "NOM_TITULO_SEGLOGR",
            "NOM_SEGLOGR", "DSC_LOCALIDADE", "NV_GEO_COORD", "COD_ESPECIE"]],
        geometry=gpd.points_from_xy(df.LONGITUDE.astype(float), df.LATITUDE.astype(float)), crs="EPSG:4674",
    ).to_crs(c.CRS_PADRAO)
    # ligação oficial preliminar -> divulgação (histórico de formação dos setores)
    h = c.historico_setores(cod)[["GEOCODIGO_2022_PRELIMINAR", "GEOCODIGO_2022_DIVULGAÇÃO"]].drop_duplicates()
    lig = h.groupby("GEOCODIGO_2022_PRELIMINAR")["GEOCODIGO_2022_DIVULGAÇÃO"].apply(set).to_dict()
    pip = gpd.sjoin(pts[["geometry"]], setores[["CD_SETOR", "geometry"]], how="left", predicate="within")
    pip = pip[~pip.index.duplicated()].CD_SETOR
    geo = setores.set_index("CD_SETOR").geometry
    destino, origem = [], []
    for i, (prel, ponto) in enumerate(zip(pts.setor_preliminar, pts.geometry)):
        cand = lig.get(prel, set())
        if len(cand) == 1:  # preliminar mantido (ou só renumerado): destino oficial único
            destino.append(next(iter(cand))); origem.append("codigo_oficial")
        elif len(cand) > 1:  # preliminar dividido: escolhe a parte que contém o ponto (ou a mais próxima)
            s_pip = pip.iloc[i]
            if s_pip in cand:
                destino.append(s_pip); origem.append("divisao_ponto_no_poligono")
            else:
                destino.append(min(cand, key=lambda s_: geo[s_].distance(ponto))); origem.append("divisao_parte_mais_proxima")
        else:
            destino.append(pip.iloc[i]); origem.append("sem_ligacao_ponto_no_poligono")
    pts["setor_2022"] = destino
    pts["origem_setor_2022"] = origem
    pts["setor_ponto_no_poligono"] = pip.values
    pts["ponto_dentro_do_setor_atribuido"] = pts.setor_2022 == pts.setor_ponto_no_poligono

    cont = pts.groupby("setor_2022").size().rename("cnefe_dom_particulares")
    conf = setores[["CD_SETOR", "SITUACAO", "dom_particulares", "dom_ocupados"]].merge(cont, left_on="CD_SETOR", right_index=True, how="left")
    conf["cnefe_dom_particulares"] = conf.cnefe_dom_particulares.fillna(0)
    conf["dif_cnefe_menos_agregado"] = conf.cnefe_dom_particulares - conf.dom_particulares
    conf["dif_pct"] = 100 * conf.dif_cnefe_menos_agregado / conf.dom_particulares.replace(0, np.nan)
    info = {
        "registros_cnefe_total": n_total, "enderecos_especie_1": len(pts),
        "nivel_geocodificacao_especie_1": pts.NV_GEO_COORD.value_counts().sort_index().to_dict(),
        "origem_do_setor_2022": pts.origem_setor_2022.value_counts().to_dict(),
        "sem_setor": int(pts.setor_2022.isna().sum()),
        "pontos_fora_do_poligono_do_setor_atribuido": int((~pts.ponto_dentro_do_setor_atribuido).sum()),
        "soma_cnefe": int(conf.cnefe_dom_particulares.sum()), "soma_agregados_V0003": float(conf.dom_particulares.sum()),
        "setores_com_contagem_identica": int((conf.dif_cnefe_menos_agregado == 0).sum()),
        "setores_com_dif_ate_5pct": int((conf.dif_pct.abs() <= 5).sum()),
        "setores_com_dif_maior_10pct": int((conf.dif_pct.abs() > 10).sum()),
        "dif_absoluta_mediana_por_setor": float(conf.dif_cnefe_menos_agregado.abs().median()),
    }
    # conferência exata no nível dos grupos preliminar<->divulgação (componentes)
    import networkx as nx
    G = nx.Graph()
    for p, ss in lig.items():
        for s in ss:
            G.add_edge("P" + p, "D" + s)
    grupo = {n[1:]: i for i, comp in enumerate(nx.connected_components(G)) for n in comp if n.startswith("D")}
    pts_g = pts.setor_preliminar.map(lambda p: grupo.get(next(iter(lig.get(p, {""}))), -1))
    cg = pd.DataFrame({"cnefe": pts_g.value_counts()}).join(
        setores.assign(gr=setores.CD_SETOR.map(grupo)).groupby("gr").dom_particulares.sum().rename("agregado"), how="outer").fillna(0)
    info["grupos_preliminar_divulgacao"] = len(cg)
    info["grupos_com_contagem_identica_pelo_codigo_preliminar"] = int((cg.cnefe == cg.agregado).sum())
    keep = ["COD_UNICO_ENDERECO", "setor_preliminar", "setor_2022", "origem_setor_2022", "ponto_dentro_do_setor_atribuido",
            "NUM_QUADRA", "NUM_FACE", "NOM_TIPO_SEGLOGR", "NOM_TITULO_SEGLOGR", "NOM_SEGLOGR", "DSC_LOCALIDADE", "NV_GEO_COORD", "geometry"]
    return pts[keep], conf, info


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--codigo-ibge", default=c.CODIGO_IBGE_DEFAULT)
    a = p.parse_args()
    cod = a.codigo_ibge
    c.garantir_pastas()
    area = c.carregar_area_estudo()
    base = {"codigo_ibge": cod, "crs": c.CRS_PADRAO, "status": c.STATUS_CONFERENCIA,
            "script": "scripts/processamento/dinamica_populacional_2022.py", "ligado_ao_portal": False}

    st, cob = setores_2022(cod)
    out = c.CAMADAS / "populacao-setores_ibge-censo_2022_setor.gpkg"
    st.to_file(out, driver="GPKG", layer="setores_2022")
    c.gravar_meta(out, **base, fonte="IBGE — malha de setores 2022 (data/raw/vetor) + Agregados por Setores 2022 (básico, demografia, características do domicílio 1)",
                  variaveis={"pop": "V0001", "dom_particulares": "V0003", "dom_ocupados": "V0007", "dppo": "V00001",
                             "mor_por_dom": "V00005/V00001 (moradores em DPPO por DPPO)", "pct_0_14": "(V01031+V01032+V01033)/V01006",
                             "pct_60_mais": "(V01040+V01041)/V01006", "pct_dom_1_morador": "V00017/V00001", "dens_hab_ha": "V0001/área da geometria em EPSG:31981"},
                  n_setores=len(st), populacao_total=float(st["pop"].sum()), cobertura_por_indicador=cob,
                  transformacao="junção por CD_SETOR; sigilo 'X' -> ausente; indicador com parcela ausente fica ausente")
    st.drop(columns="geometry").to_csv(c.TABELAS / "populacao-setores_ibge-censo_2022_setor.csv", index=False)
    c.gravar_meta(c.TABELAS / "populacao-setores_ibge-censo_2022_setor.csv", **base, descricao="atributos da camada de setores 2022, sem geometria", cobertura_por_indicador=cob)

    gr, ginfo = grade_2022(cod, area)
    out = c.CAMADAS / "populacao-grade_ibge-censo_2022_200m-1km.gpkg"
    gr.to_file(out, driver="GPKG", layer="grade_2022")
    c.gravar_meta(out, **base, fonte="IBGE — Grade Estatística, Censo 2022 (quadrante grade_id13), campos TOTAL e TOTAL_DOM",
                  url="https://geoftp.ibge.gov.br/recortes_para_fins_estatisticos/grade_estatistica/censo_2022/grade_estatistica/grade_id13.zip",
                  transformacao="leitura pelo envelope do município, reprojeção EPSG:4674 -> 31981, seleção das células com centróide no município",
                  conferencia=ginfo)

    pts, conf, cinfo = cnefe_2022(cod, st)
    out = c.CAMADAS / "enderecos-domicilios_ibge-cnefe_2022_pontos.gpkg"
    pts.to_file(out, driver="GPKG", layer="cnefe_2022_especie1")
    c.gravar_meta(out, **base, fonte="IBGE — CNEFE, Censo Demográfico 2022, arquivo CSV do município",
                  url="https://ftp.ibge.gov.br/Cadastro_Nacional_de_Enderecos_para_Fins_Estatisticos/Censo_Demografico_2022/Arquivos_CNEFE/CSV/Municipio/43_RS/4322400_URUGUAIANA.zip",
                  transformacao="filtro COD_ESPECIE=1 (domicílio particular); pontos de LATITUDE/LONGITUDE (EPSG:4674) -> 31981; setor de divulgação pelo código preliminar via ligação oficial do histórico de formação dos setores; nos setores preliminares divididos, a parte que contém o ponto (ou a mais próxima); número e complementos do endereço não são levados para a camada",
                  conferencia=cinfo, uso_previsto="base para a etapa futura de exposição a inundação; aqui só produzida e conferida")
    conf.to_csv(c.TABELAS / "conferencia-cnefe-agregados_ibge-censo_2022_setor.csv", index=False)
    c.gravar_meta(c.TABELAS / "conferencia-cnefe-agregados_ibge-censo_2022_setor.csv", **base,
                  descricao="contagem de endereços CNEFE espécie 1 por setor × domicílios particulares (V0003) dos agregados", resumo=cinfo)

    import json
    print(json.dumps({"cobertura": cob, "grade": ginfo, "cnefe": cinfo}, ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    main()
