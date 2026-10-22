"""
Exposição da população ao calor de superfície (rodada 75): cruza a composição
de verão da temperatura de superfície (LST Landsat C2 L2, conjunto principal,
verões 2014–2026) com a população do Censo 2022 por setor e com a distância às
unidades ESF/UBS. Exploratório; nada vai ao portal; sem nenhum ponto de
endereço nos produtos.

Etapas (subcomandos):
    exposicao   setores urbanos (146) e bairros (26): classes por quartis da
                anomalia mediana e da lst_mediana; população total, por idade e
                domicílios por classe; razões Q4 × demais; Spearman.
    acesso      por setor, distância pela rede à ESF/UBS mais próxima a partir do
                produto de acessibilidade do projeto (agregado por setor);
                população dos setores Q4 a mais de X m.
    dasimetria  população por célula da grade de 250 m da LST pela fração dos
                domicílios do CNEFE 2022 do setor em cada célula (só contagens;
                células com < 5 domicílios suprimidas).
    validacao   LST urbana e rural por cena (conjunto principal) × temperatura
                do ar da estação INMET A809 (passagem, máxima e média do dia).
    figuras     população por classe, mapa de setores por quartil com as ESF/UBS
                como marcadores de unidade, dispersão por bairro, dispersão LST × ar.
    todos       todas as etapas.

Regras principais (ver os .json):
    - classes: quartis (pd.qcut) da anomalia mediana entre os setores urbanos não
      suprimidos (Q1 mais fresco … Q4 mais quente); idem para lst_mediana e para
      os bairros entre os 26 bairros;
    - idade: Agregados por Setores 2022 (demografia): 0–4 = V01031, 5–14 =
      V01032 + V01033, 60+ = V01040 + V01041, 70+ = V01041 (as faixas publicadas
      não têm 65+ nem 75+); sigilo "X" → vazio (nunca zero) e o setor sai do
      numerador e do denominador da razão daquela faixa;
    - contagens de endereços/domicílios do CNEFE de 1 a 4 são suprimidas em toda
      tabela por unidade.

Uso:
    python scripts/processamento/exposicao_calor_urbano.py todos --codigo-ibge 4322400 [--forcar]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import shutil
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import rasterio  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

RAIZ = Path(__file__).resolve().parents[2]
sys.path.append(str(RAIZ / "scripts" / "utils"))
sys.path.append(str(RAIZ / "scripts" / "processamento"))
sys.path.append(str(RAIZ / "scripts" / "exploracao"))
sys.path.append(str(RAIZ / "scripts" / "download"))
import dinamica_populacional_comum as dp  # noqa: E402
from recorte_municipio import CRS_PADRAO  # noqa: E402

logger = logging.getLogger(__name__)

SCRIPT = "scripts/processamento/exposicao_calor_urbano.py"
PERIODO = "verao-2013-2026"
LST = RAIZ / "data" / "processed" / "lst"
OUT = RAIZ / "data" / "processed" / "lst_exposicao"
ARQ = {
    "lst_setores": LST / f"lst-setores-urbanos_ibge-2022_landsat-c2l2-principal_{PERIODO}_vetorial.gpkg",
    "lst_bairros": LST / f"lst-bairros_ibge-2022_landsat-c2l2-principal_{PERIODO}_vetorial.gpkg",
    "lst_grade": LST / f"lst-grade_landsat-c2l2-principal_{PERIODO}_250m.gpkg",
    "cenas": LST / f"cenas-selecionadas_landsat-c2l2_{PERIODO}.csv",
    "pop_setores": RAIZ / "data/processed/dinamica_populacional/populacao-setores_ibge-censo_2022_setor.gpkg",
    "cnefe": RAIZ / "data/processed/dinamica_populacional/enderecos-domicilios_ibge-cnefe_2022_pontos.gpkg",
    "acesso": RAIZ / "data/processed/acessibilidade_inundacao/enderecos-acessibilidade-inundacao_osm-sgb-cnes-ibge_2022_pontos.gpkg",
    "unidades": RAIZ / "data/processed/saude/unidades-saude-esf-ubs_cnes-revisado-v4_2026_pontos.gpkg",
    "inmet": RAIZ / "data/raw/clima_inmet_historico-completo_horario.parquet",
    "inmet_2026": RAIZ / "data/raw/clima_inmet-a809_2026-parcial_horario.csv",
}
FONTE_LST = "USGS Landsat Collection 2 Level-2 via Microsoft Planetary Computer — composição de verão (conjunto principal, 62 cenas)"
FONTE_POP = "IBGE — Censo 2022, Agregados por Setores Censitários (básico e demografia) e malha de setores 2022"
FONTE_UNID = "CNES (Ministério da Saúde), revisado pela equipe do projeto com os profissionais de saúde do município — versão 4, 2026"
FONTE_INMET = "INMET — estação automática A809 (Uruguaiana), dados horários"
ESTACAO = "A809"
# faixas de distância pela rede já usadas no estudo de acessibilidade (scripts/processamento/acessibilidade_inundacao.py,
# FAIXAS_BASE = 500, 1000, 2000 m); 1 km é o corte principal aqui, 500 m e 2 km como sensibilidade
LIMIAR_ACESSO_M = 1000
LIMIARES_ACESSO_SENS = (500, 2000)
MIN_CONTAGEM = 5  # contagens do CNEFE de 1 a 4 são suprimidas
LADO_GRADE = 250
FUSO_LOCAL_H = -3  # dia civil local (UTC−3, sem horário de verão desde 2019) para máxima e média diárias
MIN_HORAS_DIA = 20
CLASSES = ["Q1", "Q2", "Q3", "Q4"]
ROTULO_CLASSE = {"Q1": "Q1 (mais fresco)", "Q2": "Q2", "Q3": "Q3", "Q4": "Q4 (mais quente)"}
# sequencial de uma só matiz (magnitude ordinal: Q1 claro → Q4 escuro)
COR_CLASSE = {"Q1": "#fdd49e", "Q2": "#fc8d59", "Q3": "#d7301f", "Q4": "#7f0000"}
FUNDO = "#fcfcfb"
TINTA, TINTA_2 = "#1f1f1f", "#52514e"
IDADES = {"pop_0_4": ["V01031"], "pop_5_14": ["V01032", "V01033"], "pop_0_14": ["V01031", "V01032", "V01033"],
          "pop_60_mais": ["V01040", "V01041"], "pop_70_mais": ["V01041"]}
PROD = {
    "setores": OUT / f"exposicao-calor-setores_ibge-censo-landsat-c2l2_2022-{PERIODO}_setor.gpkg",
    "setores_csv": OUT / f"exposicao-calor-setores_ibge-censo-landsat-c2l2_2022-{PERIODO}_setor.csv",
    "bairros": OUT / f"exposicao-calor-bairros_ibge-censo-landsat-c2l2_2022-{PERIODO}_bairro.gpkg",
    "bairros_csv": OUT / f"exposicao-calor-bairros_ibge-censo-landsat-c2l2_2022-{PERIODO}_bairro.csv",
    "resumo": OUT / f"exposicao-calor-resumo-por-classe_ibge-censo-landsat-c2l2_2022-{PERIODO}_classe.csv",
    "razoes": OUT / f"exposicao-calor-razoes-q4_ibge-censo-landsat-c2l2_2022-{PERIODO}_classe.csv",
    "spearman": OUT / f"exposicao-calor-spearman_ibge-censo-landsat-c2l2_2022-{PERIODO}_setor.csv",
    "acesso": OUT / f"exposicao-calor-acesso-esf-ubs_osm-cnes-ibge-landsat-c2l2_2022-{PERIODO}_classe.csv",
    "grade": OUT / f"exposicao-calor-grade-dasimetrica_ibge-cnefe-landsat-c2l2_2022-{PERIODO}_250m.gpkg",
    "validacao": OUT / f"validacao-lst-ar_landsat-c2l2-inmet-a809_{PERIODO}_cena.csv",
    "validacao_resumo": OUT / f"validacao-lst-ar-resumo_landsat-c2l2-inmet-a809_{PERIODO}.csv",
}
FIG = {
    "pop_classe": OUT / f"exposicao-calor-populacao-por-classe_ibge-censo-landsat-c2l2_2022-{PERIODO}_grafico.png",
    "mapa": OUT / f"exposicao-calor-setores-quartil-anomalia_ibge-cnes-landsat-c2l2_2022-{PERIODO}_mapa.png",
    "disp_bairros": OUT / f"exposicao-calor-bairros-lst-x-populacao_ibge-censo-landsat-c2l2_2022-{PERIODO}_dispersao.png",
    "disp_ar": OUT / f"validacao-lst-ar_landsat-c2l2-inmet-a809_{PERIODO}_dispersao.png",
}


# ---------------------------------------------------------------------------
# utilidades
# ---------------------------------------------------------------------------
def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for bloco in iter(lambda: f.read(1 << 20), b""):
            h.update(bloco)
    return h.hexdigest()


def sidecar(caminho: Path, **campos) -> None:
    """Metadado irmão: GPKG ganha <nome>.json; CSV/PNG ganham <nome>.<ext>.json (convenção da pasta lst/)."""
    meta = {"arquivo": caminho.name, "data_processamento": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "script": SCRIPT, "uso": "exposição da população ao calor de superfície (rodada 75); exploratório, fora do portal",
            "status": "extraido"}
    meta.update(campos)
    if caminho.exists():
        meta["tamanho_bytes"] = caminho.stat().st_size
        meta["sha256"] = sha256(caminho)
    destino = caminho.with_suffix(".json") if caminho.suffix == ".gpkg" else caminho.with_name(caminho.name + ".json")
    destino.write_text(json.dumps(meta, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def gravar_csv(df: pd.DataFrame, caminho: Path, **campos) -> None:
    df.to_csv(caminho, index=False)
    sidecar(caminho, **campos)
    logger.info("gravado %s (%d linhas)", caminho.name, len(df))


def gravar_gpkg(gdf: gpd.GeoDataFrame, caminho: Path, camada: str, **campos) -> None:
    if caminho.exists():
        caminho.unlink()
    gdf.to_file(caminho, layer=camada, driver="GPKG")
    sidecar(caminho, **campos)
    logger.info("gravado %s (%d feições)", caminho.name, len(gdf))


def prontos(*arqs: Path) -> bool:
    return all(a.exists() for a in arqs)


def suprimir(n: pd.Series) -> pd.Series:
    """Contagem de 1 a 4 → vazio (zero e ≥ 5 ficam)."""
    return n.where(~n.between(1, MIN_CONTAGEM - 1))


def quartis(v: pd.Series) -> pd.Series:
    out = pd.Series(None, index=v.index, dtype="object")  # None vira nulo no GPKG (pd.NA viraria o texto "<NA>")
    ok = v.notna()
    out[ok] = pd.qcut(v[ok], 4, labels=CLASSES).astype(str)
    return out


def spearman(x: pd.Series, y: pd.Series) -> tuple[float | None, int]:
    ok = x.notna() & y.notna()
    return (round(float(x[ok].corr(y[ok], method="spearman")), 3) if ok.sum() > 2 else None), int(ok.sum())


# ---------------------------------------------------------------------------
# população
# ---------------------------------------------------------------------------
def populacao(codigo: str) -> pd.DataFrame:
    """População, domicílios e faixas etárias por setor (179 setores do município)."""
    p = gpd.read_file(ARQ["pop_setores"], ignore_geometry=True)
    p = p[p["CD_SETOR"].astype(str).str.startswith(codigo)]
    dem = dp.agregados_2022("demografia", codigo, ["V01006"] + sorted({v for vs in IDADES.values() for v in vs}))
    for nome, vs in IDADES.items():
        dem[nome] = dem[vs].sum(axis=1, min_count=len(vs)).where(dem[vs].notna().all(axis=1))  # parcela em sigilo → vazio
    dem = dem.rename(columns={"V01006": "pop_demografia"})
    cols = ["CD_SETOR", "SITUACAO", "CD_BAIRRO", "NM_BAIRRO", "area_ha", "pop", "dom_particulares", "dom_ocupados", "dens_hab_ha"]
    return p[cols].merge(dem[["CD_SETOR", "pop_demografia", *IDADES]], on="CD_SETOR", how="left")


def descrever_idades(pop: pd.DataFrame) -> dict:
    d = {"faixas_publicadas": "0–4, 5–9, 10–14, 15–19, 20–24, 25–29, 30–39, 40–49, 50–59, 60–69, 70 ou mais (V01031–V01041)",
         "sem_65_mais_e_75_mais": "não existem nas faixas publicadas; usadas 60+ e 70+",
         "pop_demografia_igual_pop_basico": bool((pop["pop_demografia"] - pop["pop"]).abs().max() == 0)}
    for nome in IDADES:
        s = pop[nome].isna()
        d[nome] = {"setores_em_sigilo": int(s.sum()), "populacao_desses_setores": float(pop.loc[s, "pop"].sum())}
    return d


# ---------------------------------------------------------------------------
# exposição por setor e bairro
# ---------------------------------------------------------------------------
def exposicao(codigo: str, forcar: bool) -> dict:
    alvos = (PROD["setores"], PROD["bairros"], PROD["resumo"], PROD["razoes"], PROD["spearman"])
    if prontos(*alvos) and not forcar:
        logger.info("exposição já existe (use --forcar)")
        return {}
    OUT.mkdir(parents=True, exist_ok=True)
    pop = populacao(codigo)
    lst = gpd.read_file(ARQ["lst_setores"])
    cols_lst = ["CD_SETOR", "lst_mediana_c", "anomalia_mediana_c", "persistencia_p90_media", "fracao_nucleo", "suprimida", "geometry"]
    se = lst[cols_lst].merge(pop.drop(columns=["NM_BAIRRO"]), on="CD_SETOR", how="left").merge(
        lst[["CD_SETOR", "NM_BAIRRO"]], on="CD_SETOR")
    se["classe_anomalia"] = quartis(se["anomalia_mediana_c"].where(~se["suprimida"]))
    se["classe_lst"] = quartis(se["lst_mediana_c"].where(~se["suprimida"]))
    for nome in ("pop_0_4", "pop_5_14", "pop_0_14", "pop_60_mais", "pop_70_mais"):
        se[f"pct_{nome[4:]}"] = (100 * se[nome] / se["pop"]).round(2)
    se = gpd.GeoDataFrame(se, geometry="geometry", crs=CRS_PADRAO)
    pop_urbana_mun = float(pop.loc[pop["SITUACAO"].str.lower().str.startswith("urban"), "pop"].sum())

    # --- resumo por classe (setores)
    resumo = []
    validos = se[se["classe_anomalia"].notna()]
    tot = float(validos["pop"].sum())
    for var in ("classe_anomalia", "classe_lst"):
        for q in CLASSES:
            s = validos[validos[var] == q]
            lin = {"unidade": "setor", "classificacao": var.replace("classe_", ""), "classe": q, "n_unidades": len(s),
                   "faixa_min": round(float(s[var.replace("classe_", "") + "_mediana_c"].min()), 3),
                   "faixa_max": round(float(s[var.replace("classe_", "") + "_mediana_c"].max()), 3),
                   "pop": int(s["pop"].sum()), "pct_pop_setores_classificados": round(100 * s["pop"].sum() / tot, 2),
                   "pct_pop_urbana_municipio": round(100 * s["pop"].sum() / pop_urbana_mun, 2),
                   "dom_particulares": int(s["dom_particulares"].sum()), "dom_ocupados": int(s["dom_ocupados"].sum())}
            for nome in IDADES:
                lin[nome] = int(s[nome].sum())
                lin[f"setores_sigilo_{nome[4:]}"] = int(s[nome].isna().sum())
            lin["persistencia_media_setores"] = round(float(s["persistencia_p90_media"].mean()), 4)
            resumo.append(lin)
    # --- bairros
    ba = gpd.read_file(ARQ["lst_bairros"])[["CD_BAIRRO", "NM_BAIRRO", "lst_mediana_c", "anomalia_mediana_c",
                                            "persistencia_p90_media", "fracao_nucleo", "suprimida", "geometry"]]
    pb = pop.groupby("CD_BAIRRO").agg(pop=("pop", "sum"), dom_particulares=("dom_particulares", "sum"),
                                      dom_ocupados=("dom_ocupados", "sum"), area_ha_setores=("area_ha", "sum"),
                                      n_setores=("CD_SETOR", "size"),
                                      setores_sigilo_idade=("pop_60_mais", lambda x: int(x.isna().sum())),
                                      **{n: (n, "sum") for n in IDADES})  # setores em sigilo ficam fora da soma (contados à parte)
    ba["CD_BAIRRO"] = ba["CD_BAIRRO"].astype(str)
    pb.index = pb.index.astype(str)
    ba = ba.merge(pb, left_on="CD_BAIRRO", right_index=True, how="left")
    ba["dens_hab_ha"] = (ba["pop"] / (ba.geometry.area / 1e4)).round(2)
    for nome in ("pop_0_14", "pop_60_mais"):
        ba[f"pct_{nome[4:]}"] = (100 * ba[nome] / ba["pop"]).round(2)
    ba["classe_anomalia"] = quartis(ba["anomalia_mediana_c"].where(~ba["suprimida"]))
    ba["classe_lst"] = quartis(ba["lst_mediana_c"].where(~ba["suprimida"]))
    for var in ("classe_anomalia", "classe_lst"):
        for q in CLASSES:
            s = ba[ba[var] == q]
            resumo.append({"unidade": "bairro", "classificacao": var.replace("classe_", ""), "classe": q, "n_unidades": len(s),
                           "faixa_min": round(float(s[var.replace("classe_", "") + "_mediana_c"].min()), 3),
                           "faixa_max": round(float(s[var.replace("classe_", "") + "_mediana_c"].max()), 3),
                           "pop": int(s["pop"].sum()), "pct_pop_setores_classificados": round(100 * s["pop"].sum() / ba["pop"].sum(), 2),
                           "pct_pop_urbana_municipio": None, "dom_particulares": int(s["dom_particulares"].sum()),
                           "dom_ocupados": int(s["dom_ocupados"].sum()),
                           **{n: int(s[n].sum()) for n in IDADES},
                           "persistencia_media_setores": None})
    resumo = pd.DataFrame(resumo)

    # --- razões Q4 × demais (setores; só setores com a faixa sem sigilo)
    raz = []
    for var in ("classe_anomalia", "classe_lst"):
        q4 = validos[var] == "Q4"
        for nome in IDADES:
            ok = validos[nome].notna()
            a, b = validos[q4 & ok], validos[~q4 & ok]
            pa, pb_ = a[nome].sum() / a["pop"].sum(), b[nome].sum() / b["pop"].sum()
            raz.append({"classificacao": var.replace("classe_", ""), "faixa": nome, "pct_q4": round(100 * pa, 2),
                        "pct_demais": round(100 * pb_, 2), "razao_q4_sobre_demais": round(pa / pb_, 3),
                        "pop_q4_setores_com_dado": int(a["pop"].sum()), "pop_demais_setores_com_dado": int(b["pop"].sum()),
                        "setores_excluidos_por_sigilo": int((~ok).sum())})
    raz = pd.DataFrame(raz)

    # --- Spearman (só coeficientes)
    sp = []
    for alvo in ("anomalia_mediana_c", "lst_mediana_c"):
        for expl in ("dens_hab_ha", "pct_60_mais", "pct_70_mais", "pct_0_4", "pct_0_14"):
            r, n = spearman(validos[alvo], validos[expl])
            sp.append({"unidade": "setor", "variavel": alvo, "explicativa": expl, "spearman": r, "n": n})
        for expl in ("dens_hab_ha", "pct_60_mais", "pct_0_14"):
            v = ba[~ba["suprimida"]]
            r, n = spearman(v[alvo], v[expl])
            sp.append({"unidade": "bairro", "variavel": alvo, "explicativa": expl, "spearman": r, "n": n})
    sp = pd.DataFrame(sp)

    regras = {"classe_anomalia": "quartis (pd.qcut) da anomalia_mediana_c entre as unidades não suprimidas; Q1 mais fresco, Q4 mais quente",
              "classe_lst": "quartis da lst_mediana_c entre as unidades não suprimidas",
              "anomalia_mediana_c": "mediana por unidade da anomalia (LST da cena − média da LST de terra urbana da cena), conjunto principal",
              "persistencia_p90_media": "média da persistência acima do p90 urbano (só núcleo urbanizado; vazia se < 30 % da terra da unidade no núcleo — regra da rodada 75)",
              "pop": "V0001 (Agregados básico 2022)", "dom_particulares": "V0003", "dom_ocupados": "V0007",
              "pop_0_4": "V01031", "pop_5_14": "V01032 + V01033", "pop_0_14": "V01031 + V01032 + V01033",
              "pop_60_mais": "V01040 + V01041", "pop_70_mais": "V01041",
              "pct_*": "100 × faixa / pop do setor", "dens_hab_ha": "pop / área do polígono do setor (EPSG:31981)",
              "sigilo": "valor 'X' do IBGE → vazio; no bairro, os setores em sigilo ficam fora da soma da faixa (coluna setores_sigilo_idade, "
                        "pela faixa 60+) e o percentual do bairro fica levemente subestimado"}
    base_meta = dict(fontes=[FONTE_LST, FONTE_POP], crs=CRS_PADRAO, regras=regras,
                     populacao_status_origem="populacao-setores_ibge-censo_2022_setor.gpkg está 'pendente de conferência' no projeto",
                     idades=descrever_idades(pop), pop_urbana_municipio=pop_urbana_mun,
                     nota="nenhum ponto de endereço; só agregados por setor/bairro")
    se_out = se.drop(columns=["SITUACAO"])
    gravar_gpkg(se_out, PROD["setores"], "exposicao_calor_setores", **base_meta, n_setores=int(len(se)),
                n_suprimidos=int(se["suprimida"].sum()))
    gravar_csv(pd.DataFrame(se_out.drop(columns="geometry")), PROD["setores_csv"], **base_meta)
    gravar_gpkg(ba, PROD["bairros"], "exposicao_calor_bairros", **base_meta, n_bairros=int(len(ba)),
                nota_bairro="população do bairro = soma dos setores 2022 com o mesmo CD_BAIRRO (urbanos e rurais)")
    gravar_csv(pd.DataFrame(ba.drop(columns="geometry")), PROD["bairros_csv"], **base_meta)
    gravar_csv(resumo, PROD["resumo"], **base_meta, conteudo="população, faixas etárias e domicílios por classe (setores e bairros)")
    gravar_csv(raz, PROD["razoes"], **base_meta, conteudo="percentual de cada faixa etária nos setores Q4 e nos demais e a razão entre eles")
    gravar_csv(sp, PROD["spearman"], **base_meta, conteudo="coeficientes de Spearman (só coeficientes; sem teste)")
    return {"setores": se, "bairros": ba, "resumo": resumo, "razoes": raz, "spearman": sp, "pop_urbana_mun": pop_urbana_mun}


# ---------------------------------------------------------------------------
# acesso às ESF/UBS
# ---------------------------------------------------------------------------
def acesso(forcar: bool) -> pd.DataFrame | None:
    if prontos(PROD["acesso"]) and not forcar:
        logger.info("acesso já existe (use --forcar)")
        return None
    se = gpd.read_file(PROD["setores"])
    # só as colunas necessárias: nenhum ponto nem código de endereço sai daqui
    e = gpd.read_file(ARQ["acesso"], columns=["setor_2022", "pop", "dist_base_m"], ignore_geometry=True)
    e["dist_base_m"] = e["dist_base_m"].where(np.isfinite(e["dist_base_m"]))
    limiares = (LIMIAR_ACESSO_M, *LIMIARES_ACESSO_SENS)
    g = e.groupby("setor_2022")
    ag = pd.DataFrame({"n_enderecos": g.size(), "dist_rede_mediana_m": g["dist_base_m"].median().round(0),
                       "dist_rede_p90_m": g["dist_base_m"].quantile(0.9).round(0),
                       "enderecos_sem_caminho": g["dist_base_m"].apply(lambda s: int(s.isna().sum()))})
    for x in limiares:
        acima = e["dist_base_m"] > x
        ag[f"n_enderecos_acima_{x}m"] = acima.groupby(e["setor_2022"]).sum()
        ag[f"pop_estimada_acima_{x}m"] = e["pop"].where(acima, 0).groupby(e["setor_2022"]).sum().round(1)
    se = se.merge(ag, left_on="CD_SETOR", right_index=True, how="left")
    linhas = []
    for x in limiares:
        for q in CLASSES + ["todas"]:
            s = se[se["classe_anomalia"] == q] if q != "todas" else se[se["classe_anomalia"].notna()]
            n_ac, p_ac = s[f"n_enderecos_acima_{x}m"].sum(), s[f"pop_estimada_acima_{x}m"].sum()
            linhas.append({"limiar_m": x, "classe_anomalia": q, "n_setores": len(s), "pop": int(s["pop"].sum()),
                           "pop_estimada_acima_limiar": None if 0 < n_ac < MIN_CONTAGEM else round(float(p_ac), 0),
                           "pct_pop_acima_limiar": None if 0 < n_ac < MIN_CONTAGEM else round(100 * p_ac / s["pop"].sum(), 2),
                           "enderecos_acima_limiar": None if 0 < n_ac < MIN_CONTAGEM else int(n_ac),
                           "dist_mediana_dos_setores_m": round(float(s["dist_rede_mediana_m"].median()), 0)})
    tab = pd.DataFrame(linhas)
    meta = dict(fontes=[FONTE_LST, FONTE_POP, FONTE_UNID, "OpenStreetMap — malha viária (ODbL)", "IBGE — CNEFE 2022 (domicílios, só contagens)"],
                produto_acesso_origem="data/processed/acessibilidade_inundacao/enderecos-acessibilidade-inundacao_osm-sgb-cnes-ibge_2022_pontos.gpkg "
                                      "(scripts/processamento/acessibilidade_inundacao.py; status 'pendente de conferência')",
                medida="distância pela rede viária OSM não direcionada, sem inundação (coluna dist_base_m), de cada endereço de domicílio à unidade "
                       "ESF/UBS mais próxima (destinos: 18 ESF + 3 UBS; a Equipe de Saúde Prisional, sem classe, não é destino); sem tempo de viagem no produto",
                limiar=f"{LIMIAR_ACESSO_M} m (fronteira das faixas 500 m–1 km / 1–2 km do estudo de acessibilidade); sensibilidade {LIMIARES_ACESSO_SENS}",
                pop_estimada="população do setor repartida igualmente entre os endereços de domicílio do CNEFE do setor (regra do produto de acesso)",
                supressao="contagens de endereços de 1 a 4 (e a população estimada correspondente) ficam vazias",
                nota="unidade mais próxima pela rede NÃO é o território oficial da equipe; nenhum ponto de endereço no produto")
    gravar_csv(tab, PROD["acesso"], **meta, conteudo="população e endereços a mais de X m da ESF/UBS mais próxima, por classe de anomalia")
    # colunas de acesso no gpkg/csv de setores (contagens 1–4 suprimidas)
    for x in limiares:
        n = se[f"n_enderecos_acima_{x}m"]
        se.loc[n.between(1, MIN_CONTAGEM - 1), f"pop_estimada_acima_{x}m"] = np.nan
        se[f"n_enderecos_acima_{x}m"] = suprimir(n)
    se["n_enderecos"] = suprimir(se["n_enderecos"])
    se["enderecos_sem_caminho"] = suprimir(se["enderecos_sem_caminho"])
    meta_se = json.loads(PROD["setores"].with_suffix(".json").read_text(encoding="utf-8"))
    for k in ("arquivo", "data_processamento", "tamanho_bytes", "sha256", "script", "uso", "status"):
        meta_se.pop(k, None)
    meta_se["acesso"] = meta
    gravar_gpkg(se, PROD["setores"], "exposicao_calor_setores", **meta_se)
    gravar_csv(pd.DataFrame(se.drop(columns="geometry")), PROD["setores_csv"], **meta_se)
    return tab


# ---------------------------------------------------------------------------
# dasimetria a 250 m
# ---------------------------------------------------------------------------
def dasimetria(codigo: str, forcar: bool) -> dict | None:
    if prontos(PROD["grade"]) and not forcar:
        logger.info("grade dasimétrica já existe (use --forcar)")
        return None
    grade = gpd.read_file(ARQ["lst_grade"])[["id_celula", "x_min", "y_min", "lst_mediana_c", "anomalia_mediana_c",
                                             "persistencia_p90_media", "suprimida", "geometry"]]
    pts = gpd.read_file(ARQ["cnefe"], columns=["setor_2022"])
    # contagem por célula × setor; o ponto não sai daqui
    cel = pd.DataFrame({"x_min": (np.floor(pts.geometry.x / LADO_GRADE) * LADO_GRADE).astype(int),
                        "y_min": (np.floor(pts.geometry.y / LADO_GRADE) * LADO_GRADE).astype(int), "setor": pts["setor_2022"]})
    del pts
    cont = cel.groupby(["x_min", "y_min", "setor"]).size().rename("n").reset_index()
    cont["frac"] = cont["n"] / cont.groupby("setor")["n"].transform("sum")
    pop = populacao(codigo).set_index("CD_SETOR")
    cols = ["pop", "pop_0_14", "pop_60_mais", "dom_particulares"]
    cont = cont.join(pop[cols], on="setor")
    for c in cols:
        cont[f"{c}_cel"] = cont[c] * cont["frac"]
    porcel = cont.groupby(["x_min", "y_min"]).agg(n_domicilios_cnefe=("n", "sum"), **{f"{c}_estimada": (f"{c}_cel", "sum") for c in cols[:3]},
                                                  n_setores=("setor", "nunique"))
    grade = grade.merge(porcel, left_on=["x_min", "y_min"], right_index=True, how="left")
    grade["n_domicilios_cnefe"] = grade["n_domicilios_cnefe"].fillna(0)
    alocada_total = float(cont["pop_cel"].sum())
    na_grade = float(grade["pop_estimada"].sum())
    sup = grade["n_domicilios_cnefe"].between(1, MIN_CONTAGEM - 1)
    pop_sup = float(grade.loc[sup, "pop_estimada"].sum())
    for c in ("n_domicilios_cnefe", "pop_estimada", "pop_0_14_estimada", "pop_60_mais_estimada", "n_setores"):
        grade.loc[sup, c] = np.nan
    for c in ("pop_estimada", "pop_0_14_estimada", "pop_60_mais_estimada"):
        grade[c] = grade[c].round(1)
    grade["suprimida_por_contagem"] = sup
    sem_pontos = pop.index.difference(cont["setor"].unique())
    info = {"pop_municipio": float(pop["pop"].sum()), "pop_alocada": round(alocada_total, 1),
            "pop_alocada_nas_celulas_da_grade": round(na_grade, 1), "pop_em_celulas_suprimidas": round(pop_sup, 1),
            "celulas_suprimidas_por_contagem": int(sup.sum()),
            "setores_sem_domicilio_no_cnefe": int(len(sem_pontos)), "pop_desses_setores_nao_alocada": float(pop.loc[sem_pontos, "pop"].sum())}
    gravar_gpkg(grade, PROD["grade"], "exposicao_calor_grade_250m", fontes=[FONTE_LST, FONTE_POP, "IBGE — CNEFE 2022 (domicílios particulares, espécie 1)"],
                crs=CRS_PADRAO, regra="população da célula = Σ (população do setor × fração dos domicílios do CNEFE do setor com ponto na célula); "
                "idem para 0–14 e 60+; células com 1 a 4 domicílios: contagem e população suprimidas (vazias); nenhum ponto de endereço",
                grade="mesma grade de 250 m da LST (múltiplos de 250 m em EPSG:31981)", totais=info,
                nota="estimativa dasimétrica, não contagem; a população dos setores sem domicílio no CNEFE não é alocada")
    return info


# ---------------------------------------------------------------------------
# validação com a temperatura do ar (INMET A809)
# ---------------------------------------------------------------------------
def serie_ar() -> pd.DataFrame:
    h = pd.read_parquet(ARQ["inmet"])
    h = h[h["codigo_estacao"] == ESTACAO]
    p = pd.read_csv(ARQ["inmet_2026"], parse_dates=["datetime_utc"])
    p = p[p["codigo_estacao"] == ESTACAO]
    s = pd.concat([h, p], ignore_index=True)
    s["datetime_utc"] = pd.to_datetime(s["datetime_utc"], utc=True)
    return s.sort_values("datetime_utc").drop_duplicates("datetime_utc", keep="last").set_index("datetime_utc")


def validacao(codigo: str, forcar: bool) -> pd.DataFrame | None:
    if prontos(PROD["validacao"], PROD["validacao_resumo"]) and not forcar:
        logger.info("validação já existe (use --forcar)")
        return None
    import lst_composicao as lc  # mesma AOI, máscaras e grade da composição
    import lst_levantamento as lev
    a = lev.montar_aoi(codigo)
    cenas = pd.read_csv(ARQ["cenas"])
    cenas = cenas[cenas["conjunto_principal"]]
    with rasterio.open(lc.nome_derivado("lst", cenas.iloc[0]["plataforma"].replace("-", ""), cenas.iloc[0]["data"])) as s:
        g = {"transform": s.transform, "w": s.width, "h": s.height}
    m = lc.mascaras(a, g)
    ar = serie_ar()
    linhas = []
    for r in cenas.itertuples():
        plat = r.plataforma.replace("-", "")
        c = {n: lc.ler_tif(lc.nome_derivado(n, plat, r.data)) for n in ("lst", "ndvi", "ndbi", "mndwi")}
        # terra = mesma regra da composição (limpo, fora da água do OSM, MNDWI ≤ 0)
        terra = (np.isfinite(c["lst"]) & np.isfinite(c["ndvi"]) & np.isfinite(c["ndbi"]) & np.isfinite(c["mndwi"])
                 & ~m["agua_osm"] & (c["mndwi"] <= 0))
        urb, rur = c["lst"][terra & m["urbano"]], c["lst"][terra & m["rural"]]
        t = pd.Timestamp(f"{r.data}T{r.hora_utc}:00", tz="UTC")
        h0 = t.floor("h")
        v0, v1 = ar["temp_ar_c"].get(h0, np.nan), ar["temp_ar_c"].get(h0 + pd.Timedelta(hours=1), np.nan)
        f = (t - h0).total_seconds() / 3600
        ar_pass = v0 + f * (v1 - v0) if np.isfinite(v0) and np.isfinite(v1) else np.nan
        # dia civil local: 03 UTC do dia a 02 UTC do dia seguinte
        ini = pd.Timestamp(r.data, tz="UTC") - pd.Timedelta(hours=FUSO_LOCAL_H)
        dia = ar.loc[ini:ini + pd.Timedelta(hours=23)]
        n_h = int(dia["temp_ar_c"].notna().sum())
        tmax = float(dia["temp_max_c"].max()) if dia["temp_max_c"].notna().sum() >= MIN_HORAS_DIA else np.nan
        tmed = float(dia["temp_ar_c"].mean()) if n_h >= MIN_HORAS_DIA else np.nan
        lu = float(urb.mean())
        linhas.append({"data": r.data, "hora_utc": r.hora_utc, "plataforma": r.plataforma, "lst_urbana_media_c": round(lu, 2),
                       "lst_rural_media_c": round(float(rur.mean()), 2), "lst_rural_mediana_c": round(float(np.median(rur)), 2),
                       "dif_urbano_rural_c": round(lu - float(rur.mean()), 2),
                       "ar_13utc_c": v0, "ar_14utc_c": v1, "ar_passagem_c": round(ar_pass, 2) if np.isfinite(ar_pass) else np.nan,
                       "ar_max_dia_c": tmax, "ar_media_24h_c": round(tmed, 2) if np.isfinite(tmed) else np.nan, "horas_com_dado_no_dia": n_h,
                       "lst_menos_ar_passagem_c": round(lu - ar_pass, 2) if np.isfinite(ar_pass) else np.nan,
                       "lst_menos_ar_max_c": round(lu - tmax, 2) if np.isfinite(tmax) else np.nan,
                       "lst_menos_ar_media_c": round(lu - tmed, 2) if np.isfinite(tmed) else np.nan,
                       "n_pixels_terra_urbana": int(urb.size), "n_pixels_terra_rural": int(rur.size)})
    df = pd.DataFrame(linhas)
    res = [{"estatistica": "cenas no conjunto principal", "valor": len(df), "n": len(df)}]
    for ref in ("passagem", "max", "media"):
        col = {"passagem": "ar_passagem_c", "max": "ar_max_dia_c", "media": "ar_media_24h_c"}[ref]
        d = df[f"lst_menos_ar_{ref}_c"].dropna()
        ok = df[col].notna()
        res += [{"estatistica": f"cenas com ar ({ref})", "valor": int(ok.sum()), "n": len(df)},
                {"estatistica": f"LST urbana − ar ({ref}): média (°C)", "valor": round(float(d.mean()), 2), "n": int(d.size)},
                {"estatistica": f"LST urbana − ar ({ref}): desvio padrão (°C)", "valor": round(float(d.std()), 2), "n": int(d.size)},
                {"estatistica": f"Pearson LST urbana × ar ({ref})", "valor": round(float(df.loc[ok, "lst_urbana_media_c"].corr(df.loc[ok, col])), 3), "n": int(ok.sum())},
                {"estatistica": f"Spearman LST urbana × ar ({ref})", "valor": round(float(df.loc[ok, "lst_urbana_media_c"].corr(df.loc[ok, col], method="spearman")), 3), "n": int(ok.sum())}]
    du = df["dif_urbano_rural_c"]
    res += [{"estatistica": "LST urbana − rural por cena: média (°C)", "valor": round(float(du.mean()), 2), "n": int(du.size)},
            {"estatistica": "LST urbana − rural por cena: desvio padrão (°C)", "valor": round(float(du.std()), 2), "n": int(du.size)},
            {"estatistica": "LST urbana − rural por cena: mínimo (°C)", "valor": round(float(du.min()), 2), "n": int(du.size)},
            {"estatistica": "LST urbana − rural por cena: máximo (°C)", "valor": round(float(du.max()), 2), "n": int(du.size)},
            {"estatistica": "cenas com urbano > rural", "valor": int((du > 0).sum()), "n": int(du.size)}]
    res = pd.DataFrame(res)
    meta = dict(fontes=[FONTE_LST, FONTE_INMET], arquivos_ar=["data/raw/clima_inmet_historico-completo_horario.parquet (até 2025)",
                                                              "data/raw/clima_inmet-a809_2026-parcial_horario.csv (2026, parcial)"],
                regras={"lst_urbana_media_c": "média da LST da cena na terra urbana (núcleo urbanizado IBGE 2019 na AOI, fora da água do OSM, MNDWI ≤ 0, pixel limpo) — "
                                              "a mesma referência da anomalia na composição",
                        "lst_rural_media_c": "idem no resto da AOI (fora do núcleo urbanizado)",
                        "ar_passagem_c": "temp_ar_c (instantânea da hora cheia UTC) interpolada linearmente entre 13 e 14 UTC no minuto da passagem",
                        "ar_max_dia_c": f"máximo de temp_max_c no dia civil local (UTC{FUSO_LOCAL_H}, 03 UTC a 02 UTC), só com ≥ {MIN_HORAS_DIA} horas com dado",
                        "ar_media_24h_c": f"média de temp_ar_c no dia civil local, só com ≥ {MIN_HORAS_DIA} horas com dado",
                        "correlacoes": "Pearson e Spearman só entre cenas com o dado de ar"},
                nota="LST (superfície) e temperatura do ar a ~2 m são grandezas diferentes: a comparação mostra coerência temporal, não equivalência")
    gravar_csv(df, PROD["validacao"], **meta, conteudo="uma linha por cena do conjunto principal")
    gravar_csv(res, PROD["validacao_resumo"], **meta, conteudo="estatísticas de LST − ar e correlações")
    return df


# ---------------------------------------------------------------------------
# figuras (agregadas; sem pontos de endereço)
# ---------------------------------------------------------------------------
def _estilo(ax):
    for lado in ("top", "right"):
        ax.spines[lado].set_visible(False)
    for lado in ("left", "bottom"):
        ax.spines[lado].set_color("#b5b4b0")
    ax.tick_params(colors=TINTA_2, labelsize=8)
    ax.grid(axis="y", color="#e6e5e1", linewidth=0.6)
    ax.set_axisbelow(True)


def figuras(codigo: str) -> None:
    se = gpd.read_file(PROD["setores"])
    ba = gpd.read_file(PROD["bairros"])
    resumo = pd.read_csv(PROD["resumo"])
    r = resumo[(resumo["unidade"] == "setor") & (resumo["classificacao"] == "anomalia")].set_index("classe").loc[CLASSES]
    nota = ("Fontes: Landsat 8/9 C2 L2 (USGS) via Planetary Computer, verões 2014–2026, conjunto principal; IBGE, Censo 2022 "
            "(agregados por setor). Quartis da anomalia mediana entre 145 setores urbanos.")

    # 1) população por classe: total e por faixa (pequenos múltiplos, um eixo cada)
    paineis = [("pop", "População total"), ("pop_0_4", "Crianças de 0 a 4 anos"), ("pop_5_14", "Crianças de 5 a 14 anos"),
               ("pop_60_mais", "Pessoas de 60 anos ou mais"), ("pop_70_mais", "Pessoas de 70 anos ou mais")]
    fig, axs = plt.subplots(1, 5, figsize=(13, 3.8), dpi=150)
    for ax, (col, tit) in zip(axs, paineis):
        v = r[col].to_numpy(float)
        ax.bar(range(4), v, color=[COR_CLASSE[q] for q in CLASSES], width=0.72, edgecolor=FUNDO, linewidth=2)
        for i, x in enumerate(v):
            ax.text(i, x, f"{x:,.0f}".replace(",", "."), ha="center", va="bottom", fontsize=7, color=TINTA)
        ax.set_xticks(range(4), CLASSES)
        ax.set_title(tit, fontsize=9, loc="left", color=TINTA)
        ax.set_ylim(0, v.max() * 1.15)
        ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda x, _: f"{x:,.0f}".replace(",", ".")))
        _estilo(ax)
    fig.suptitle("População dos setores urbanos por classe de anomalia de temperatura de superfície (Q1 mais fresco, Q4 mais quente)",
                 fontsize=10, x=0.01, ha="left", color=TINTA)
    fig.text(0.01, 0.01, nota + " Faixas sem 65+/75+: o IBGE publica 60–69 e 70+.", fontsize=6.5, color=TINTA_2)
    fig.tight_layout(rect=(0, 0.05, 1, 0.94))
    fig.savefig(FIG["pop_classe"], facecolor=FUNDO)
    plt.close(fig)
    sidecar(FIG["pop_classe"], fontes=[FONTE_LST, FONTE_POP], conteudo="barras: população por classe de anomalia, total e por faixa etária; sem pontos")

    # 2) mapa dos setores por quartil, com ESF/UBS como marcadores de unidade
    un = gpd.read_file(ARQ["unidades"])[["classe", "geometry"]].to_crs(CRS_PADRAO)  # só classe e posição; sem nome nem endereço
    un = un[un["classe"].isin(["ESF", "UBS"])]
    fig, ax = plt.subplots(figsize=(10.5, 6.6), dpi=150)
    ax.set_facecolor(FUNDO)
    for q in CLASSES:
        se[se["classe_anomalia"] == q].plot(ax=ax, color=COR_CLASSE[q], edgecolor="white", linewidth=0.4)
    se[se["classe_anomalia"].isna()].plot(ax=ax, color="#d9d8d4", edgecolor="white", linewidth=0.4, hatch="///")
    ba.boundary.plot(ax=ax, color=TINTA, linewidth=0.5, alpha=0.7)
    x0, y0, x1, y1 = se.total_bounds
    dentro = un.cx[x0:x1, y0:y1]
    for cl, mk in (("ESF", "o"), ("UBS", "s")):
        u = dentro[dentro["classe"] == cl]
        if len(u):
            ax.scatter(u.geometry.x, u.geometry.y, marker=mk, s=46, facecolor="white", edgecolor=TINTA, linewidth=1.3, zorder=5)
    ax.set_xlim(x0 - 300, x1 + 300)
    ax.set_ylim(y0 - 300, y1 + 300)
    ax.set_xticks([])
    ax.set_yticks([])
    for s in ax.spines.values():
        s.set_visible(False)
    leg = [Patch(facecolor=COR_CLASSE[q], label=ROTULO_CLASSE[q]) for q in CLASSES]
    leg += [Patch(facecolor="#d9d8d4", hatch="///", label="sem classe (setor suprimido)"),
            Line2D([], [], marker="o", ls="", mfc="white", mec=TINTA, mew=1.3, ms=7, label="unidade ESF (posição da unidade)")]
    if (dentro["classe"] == "UBS").any():
        leg.append(Line2D([], [], marker="s", ls="", mfc="white", mec=TINTA, mew=1.3, ms=7, label="unidade UBS"))
    # legenda fora do mapa: dentro dela cobriria setores da borda
    ax.legend(handles=leg, loc="upper left", bbox_to_anchor=(1.0, 1.0), fontsize=7.5, frameon=False,
              title="Anomalia mediana de verão (quartis)", title_fontsize=8, alignment="left")
    ax.set_title("Setores urbanos por quartil de anomalia da temperatura de superfície e unidades ESF/UBS", fontsize=9.5, loc="left", color=TINTA)
    fig.text(0.01, 0.01, textwrap.fill(nota + " Contornos: bairros IBGE 2022. Unidades: CNES revisado (v4, 2026). Sem pontos de endereço.", 190),
             fontsize=6.3, color=TINTA_2)
    fig.tight_layout(rect=(0, 0.05, 1, 1))
    fig.savefig(FIG["mapa"], facecolor=FUNDO)
    plt.close(fig)
    sidecar(FIG["mapa"], fontes=[FONTE_LST, FONTE_POP, FONTE_UNID], conteudo="mapa de setores por quartil de anomalia; marcadores = posição das unidades ESF/UBS, não de pessoas")

    # 3) dispersão por bairro: LST mediana × população
    v = ba[~ba["suprimida"]].copy()
    fig, ax = plt.subplots(figsize=(8, 5.5), dpi=150)
    for q in CLASSES:
        s = v[v["classe_anomalia"] == q]
        ax.scatter(s["pop"], s["lst_mediana_c"], s=58, color=COR_CLASSE[q], edgecolor="white", linewidth=1.5, label=ROTULO_CLASSE[q], zorder=3)
    for t in pd.concat([v.nlargest(4, "lst_mediana_c"), v.nsmallest(3, "lst_mediana_c"), v.nlargest(3, "pop")]).drop_duplicates("CD_BAIRRO").itertuples():
        ax.annotate(t.NM_BAIRRO, (t.pop, t.lst_mediana_c), xytext=(5, 3), textcoords="offset points", fontsize=7, color=TINTA_2)
    ax.set_xlabel("População do bairro (Censo 2022)", fontsize=8.5, color=TINTA_2)
    ax.set_ylabel("Mediana da temperatura de superfície de verão (°C)", fontsize=8.5, color=TINTA_2)
    ax.xaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda x, _: f"{x:,.0f}".replace(",", ".")))
    _estilo(ax)
    ax.grid(axis="x", color="#e6e5e1", linewidth=0.6)
    ax.legend(title="Quartil da anomalia (bairros)", fontsize=7.5, title_fontsize=8, frameon=False, loc="lower right")
    ax.set_title("Bairros: temperatura de superfície de verão × população", fontsize=10, loc="left", color=TINTA)
    fig.text(0.01, 0.01, textwrap.fill(nota.replace("entre 145 setores urbanos", "entre os 26 bairros"), 175), fontsize=6.3, color=TINTA_2)
    fig.tight_layout(rect=(0, 0.06, 1, 1))
    fig.savefig(FIG["disp_bairros"], facecolor=FUNDO)
    plt.close(fig)
    sidecar(FIG["disp_bairros"], fontes=[FONTE_LST, FONTE_POP], conteudo="dispersão por bairro (LST mediana × população)")

    # 4) dispersão LST urbana × ar na passagem
    if PROD["validacao"].exists():
        d = pd.read_csv(PROD["validacao"]).dropna(subset=["ar_passagem_c"])
        fig, ax = plt.subplots(figsize=(6.5, 5.5), dpi=150)
        ax.scatter(d["ar_passagem_c"], d["lst_urbana_media_c"], s=40, color="#d7301f", edgecolor="white", linewidth=1.2, zorder=3)
        lo, hi = min(d["ar_passagem_c"].min(), d["lst_urbana_media_c"].min()) - 1, max(d["ar_passagem_c"].max(), d["lst_urbana_media_c"].max()) + 1
        ax.plot([lo, hi], [lo, hi], color="#9a9995", linewidth=1, linestyle="--", zorder=2)
        ax.text(hi, hi, "  LST = ar", fontsize=7, color=TINTA_2, va="bottom", ha="right")
        ax.set_xlabel("Temperatura do ar na passagem, INMET A809 (°C)", fontsize=8.5, color=TINTA_2)
        ax.set_ylabel("LST média da terra urbana na cena (°C)", fontsize=8.5, color=TINTA_2)
        r_p = d["lst_urbana_media_c"].corr(d["ar_passagem_c"])
        ax.set_title(f"Cenas de verão: LST urbana × temperatura do ar (n = {len(d)}, Pearson = {r_p:.2f})".replace(".", ","), fontsize=9.5, loc="left", color=TINTA)
        _estilo(ax)
        ax.grid(axis="x", color="#e6e5e1", linewidth=0.6)
        fig.text(0.01, 0.01, "Landsat 8/9 C2 L2, conjunto principal; ar interpolado entre 13 e 14 UTC no minuto da passagem.",
                 fontsize=6.3, color=TINTA_2)
        fig.tight_layout(rect=(0, 0.04, 1, 1))
        fig.savefig(FIG["disp_ar"], facecolor=FUNDO)
        plt.close(fig)
        sidecar(FIG["disp_ar"], fontes=[FONTE_LST, FONTE_INMET], conteudo="dispersão LST urbana × ar na passagem, por cena")


def copiar_qgis(pasta: Path) -> None:
    """Cópia NOVA do gpkg de setores para a pasta do QGIS (nenhum .qgz é criado ou alterado)."""
    destino = pasta / "dados_lst"
    for p in (PROD["setores"], PROD["setores"].with_suffix(".json")):
        alvo = destino / p.name
        if alvo.exists() and sha256(alvo) == sha256(p):
            continue
        shutil.copy2(p, alvo)
        logger.info("copiado para %s", alvo.name)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("etapa", choices=["exposicao", "acesso", "dasimetria", "validacao", "figuras", "todos"])
    ap.add_argument("--codigo-ibge", default=dp.CODIGO_IBGE_DEFAULT)
    ap.add_argument("--forcar", action="store_true", help="refaz produtos já existentes")
    ap.add_argument("--pasta-qgis", type=Path, help="se dada, copia o gpkg de setores com exposição para <pasta>/dados_lst/")
    o = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    try:
        todos = o.etapa == "todos"
        if todos or o.etapa == "exposicao":
            r = exposicao(o.codigo_ibge, o.forcar)
            # setores regerados sem as colunas de acesso: o acesso precisa ser refeito
            o.forcar = o.forcar or bool(r)
        if todos or o.etapa == "acesso":
            acesso(o.forcar)
        if todos or o.etapa == "dasimetria":
            logger.info("dasimetria: %s", dasimetria(o.codigo_ibge, o.forcar))
        if todos or o.etapa == "validacao":
            validacao(o.codigo_ibge, o.forcar)
        if todos or o.etapa == "figuras":
            if prontos(*FIG.values()) and not o.forcar:
                logger.info("figuras já existem (use --forcar)")
            else:
                figuras(o.codigo_ibge)
        if o.pasta_qgis:
            copiar_qgis(o.pasta_qgis)
    except (FileNotFoundError, KeyError) as e:
        logger.error("%s", e)
        sys.exit(2)


if __name__ == "__main__":
    main()
