"""
Exposição à inundação pelos ENDEREÇOS residenciais (CNEFE 2022) nas manchas
de inundação por cota do rio já existentes no repositório (SGB).

Entradas (todas já no repositório; nada é baixado aqui):
  - manchas por cota: data/raw/vetor/cotas-inundacao_sgb_atual_vetorial.gpkg
    (geometria reparada com buffer(0) e dissolvida por cota, como no cálculo
    existente de scripts/processamento/vulnerabilidade_inundacao.py);
  - endereços de domicílio particular do CNEFE 2022 (camada fora do git);
  - setores 2022 com população e indicadores (agregados por setor);
  - grade estatística 2022 (sensibilidade);
  - áreas comparáveis 2010–2022 (classes de mudança já calculadas).

Método:
  B1  endereço exposto à cota X = ponto dentro da UNIÃO das manchas de cota
      ≤ X (definição CUMULATIVA, rodada 04: as manchas não são perfeitamente
      aninhadas, e quem é atingido por uma cheia menor conta como atingido
      pelas maiores). As colunas exp_{cota} da rodada 03 (mancha daquela cota
      só) continuam na camada; as tabelas não cumulativas ficaram com o sufixo
      _nao-cumulativo. "Menor cota que atinge" dá as faixas.
  B2  população ESTIMADA: a população de cada setor 2022 é repartida
      igualmente entre os endereços de domicílio particular do setor; soma-se
      nos endereços expostos. Sensibilidade: o mesmo pela grade estatística
      (população da célula repartida entre os endereços da célula). O Censo não
      publica moradores por endereço — é estimativa.
  B4  perfil: % 60+ e % 0–14 do setor, ponderados pela população estimada
      exposta (setores sob sigilo ficam fora da média); % de domicílios com um
      morador ponderado pelos endereços expostos; bairro = bairro IBGE 2022 do
      setor do endereço.
  B5  população exposta por classe de mudança 2010–2022 da área comparável.

Uma estimativa preliminar por área do setor (vulnerabilidade_inundacao.py)
foi descartada: os setores da beira do rio vão até o eixo do rio e as casas
ficam na parte alta (rodada 04).

Saídas:
  docs/exposicao_inundacao/tabelas/*.csv (+ .json)    — só agregados
  docs/exposicao_inundacao/mapas_v2/*.png (+ .json)   — rodada 04 (mapas/ da rodada 03 fica intacta)
  data/processed/exposicao_inundacao/*.gpkg (+ .json) — pontos (fora do git)

Rodada 10: --layout a4 grava só a edição A4 dos mapas em
docs/exposicao_inundacao/mapas_a4/ (legenda abaixo do mapa, 16 cm, 300 dpi),
lendo a camada de pontos já gravada (data/processed/exposicao_inundacao/);
nada é recalculado e nenhuma tabela, camada ou mapa atual é regravado.

Uso:
  python scripts/processamento/exposicao_inundacao_enderecos.py --codigo-ibge 4322400
  python scripts/processamento/exposicao_inundacao_enderecos.py --codigo-ibge 4322400 --layout a4
"""

from __future__ import annotations

import argparse
import json
import logging

import geopandas as gpd
import matplotlib

matplotlib.use("Agg")
import matplotlib.patheffects as pe  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402
from shapely.geometry import box  # noqa: E402

import dinamica_populacional_comum as c  # noqa: E402
from dinamica_populacional_cnefe_mapas import Fundo, hexagonos, mil  # noqa: E402
from dinamica_populacional_mapas import INK, MUTED, SEQ, Base  # noqa: E402
from layout_mapa import LAYOUTS, LayoutA4, finalizar_a4, fonte_rotulo, pasta_a4  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

SCRIPT = "scripts/processamento/exposicao_inundacao_enderecos.py"
DOCS = c.RAIZ / "docs" / "exposicao_inundacao"
TAB = DOCS / "tabelas"
MAPAS = DOCS / "mapas_v2"  # rodada 04; docs/exposicao_inundacao/mapas/ (rodada 03) fica intacta
CAMADAS = c.RAIZ / "data" / "processed" / "exposicao_inundacao"
ARQ_COTAS = c.RAW / "vetor" / "cotas-inundacao_sgb_atual_vetorial.gpkg"
# unidades de saúde ESF/UBS (cadastro revisado, versão 4, 2026) — substitui o CNES antigo nos mapas (rodada 06)
ARQ_UNIDADES = c.RAIZ / "data" / "processed" / "saude" / "unidades-saude-esf-ubs_cnes-revisado-v4_2026_pontos.gpkg"
CREDITO_SAUDE = ("CNES (Ministério da Saúde), revisado e corrigido pela equipe do projeto com informações dos "
                 "profissionais de saúde do município — versão 4, 2026")
# símbolo por classe: forma + cor (a forma garante a leitura em tons de cinza e para daltônicos)
SIMB_UNIDADE = {"ESF": ("o", "#1b7837"), "UBS": ("s", "#2166ac"), "a confirmar": ("D", "#f2c14e"), "sem classe": ("X", "#bdbdbd")}
TAM_UNIDADE = {"ESF": 46, "UBS": 46, "a confirmar": 30, "sem classe": 46}  # losango menor: a ESF 21 fica a 63 m da UDM
# deslocamento do rótulo (pontos): ESF 21 e UDM têm o mesmo endereço e quase se sobrepõem
OFFSET_ROTULO = {"21": (-15, 4), "UDM": (-30, -12)}
# barra de escala no alto à esquerda (sobre o rio) nos recortes, onde o canto inferior esquerdo tem unidades
ESCALA_POS = {"municipio": (0.05, 0.04), "urbano": (0.05, 0.04), "ribeirinha": (0.05, 0.93)}
# cotas de referência dos hexágonos: 1205 cm (onde a exposição salta: ~10x a de 952 cm) e 1252 cm (maior mancha);
# 833 e 952 cm atingem poucos endereços (4 e 85) e dariam mapas quase vazios
COTAS_HEX = (1205, 1252)
# rampa sequencial (escura = cota mais baixa = cheia mais frequente); legível para daltônicos (luminância ordenada)
COR_PONTO = ["#2c115f", "#721f81", "#b73779", "#f1605d", "#feb078"]
COR_MANCHA = ["#7b3f8f", "#b06aa8", "#dba3c4", "#f6d7e3", "#fbeef2"]
FONTE_MAPA = ("Fonte: SGB — manchas de inundação por cota (Uruguaiana); IBGE — CNEFE e agregados por setor do Censo 2022; "
              "área de água e vias: OpenStreetMap.\nUnidades de saúde: CNES (Ministério da Saúde), revisado e corrigido pela equipe do projeto "
              "com informações dos profissionais de saúde do município — versão 4, 2026.")
FONTES_META = ["SGB — manchas de inundação por cota, serviço hidrologia/BACIA_DO_URUGUAI_URUGUAIANA (camada do repositório)",
               "IBGE — CNEFE do Censo 2022, espécie 1 (domicílio particular)",
               "IBGE — agregados por setor e malha de setores do Censo 2022; grade estatística 2022",
               "IBGE — histórico de formação dos setores 2010–2022 (áreas comparáveis)"]


# edição A4 (rodada 10): rodapé só com a fonte (compacta) e uma linha de método; o resto fica no .json
FONTE_A4 = ("Fonte: SGB (manchas por cota); IBGE (CNEFE e setores, Censo 2022); OpenStreetMap (água, vias); unidades: CNES (Ministério "
            "da Saúde), revisado e corrigido pela equipe do projeto com informações dos profissionais de saúde do município (v. 4, 2026).")
METODO_A4 = "Ponto = endereço, sem número de moradores. Exposição cumulativa (união das manchas de cota ≤ X)."
RETIRADO_A4 = "Produto de trabalho — pendente de conferência. EPSG:31981."


def layout() -> str:
    return getattr(ARGS, "layout", "lateral")


def fmt_cota(k) -> str:
    return f"{int(k)} cm"


def meta(caminho, **kw):
    return c.gravar_meta(caminho, codigo_ibge=ARGS.codigo_ibge, crs=c.CRS_PADRAO, status=c.STATUS_CONFERENCIA,
                         ligado_ao_portal=False, script=SCRIPT, fontes=FONTES_META, **kw)


def carregar_cotas() -> tuple[gpd.GeoDataFrame, dict]:
    g = gpd.read_file(ARQ_COTAS).to_crs(c.CRS_PADRAO)
    inval = int((~g.geometry.is_valid).sum())
    g["geometry"] = g.geometry.buffer(0)  # mesma correção do cálculo existente (self-intersection)
    d = g.dissolve(by="cota_cm", aggfunc={"tr_anos": "first"}).sort_index()
    d["area_km2"] = d.geometry.area / 1e6
    return d, {"feicoes_originais": len(g), "geometrias_invalidas_reparadas": inval}


def main() -> None:
    for p in (TAB, MAPAS, CAMADAS):
        p.mkdir(parents=True, exist_ok=True)
    cotas, info_cotas = carregar_cotas()
    K = list(cotas.index)
    limite = c.carregar_area_estudo()
    if layout() == "a4":
        # edição A4: lê a camada de pontos já calculada (exposição, menor cota, população estimada); nada é regravado
        pts = gpd.read_file(CAMADAS / "enderecos-exposicao-inundacao_sgb-ibge-cnefe_2022_pontos.gpkg").to_crs(c.CRS_PADRAO)
        st = gpd.read_file(c.CAMADAS / "populacao-setores_ibge-censo_2022_setor.gpkg")
        mapas(pts, cotas, K, limite, st)
        mapas_localizacao_unidades(st)
        return

    pts = gpd.read_file(c.CAMADAS / "enderecos-domicilios_ibge-cnefe_2022_pontos.gpkg",
                        columns=["COD_UNICO_ENDERECO", "setor_2022", "NV_GEO_COORD"]).to_crs(c.CRS_PADRAO)
    st = gpd.read_file(c.CAMADAS / "populacao-setores_ibge-censo_2022_setor.gpkg")
    gr = gpd.read_file(c.CAMADAS / "populacao-grade_ibge-censo_2022_200m-1km.gpkg")
    ac = gpd.read_file(c.CAMADAS / "populacao-mudanca_ibge-censo_2010-2022_area-comparavel.gpkg")

    # ---------------- atributos por endereço
    pts["preciso"] = pts.NV_GEO_COORD.isin(["1", "2"])
    chave = pd.Series(list(zip(np.round(pts.geometry.x, 2), np.round(pts.geometry.y, 2))), index=pts.index)
    pts["n_no_local"] = chave.map(chave.value_counts())
    pts["local"] = chave.astype(str)
    s = st.set_index("CD_SETOR")
    pts["bairro"] = pts.setor_2022.map(s.NM_BAIRRO).fillna("(sem bairro — setor rural)")
    pts["situacao"] = pts.setor_2022.map(s.SITUACAO)
    for k in K:
        pts[f"exp_{k}"] = pts.within(cotas.loc[k, "geometry"])
    exp_cols = [f"exp_{k}" for k in K]
    # cumulativo: dentro da união das manchas de cota <= k (ponto em algum polígono = ponto na união)
    for i, k in enumerate(K):
        pts[f"exp_cum_{k}"] = pts[[f"exp_{j}" for j in K[: i + 1]]].any(axis=1)
    cum_cols = [f"exp_cum_{k}" for k in K]
    pts["menor_cota"] = np.nan
    for k in reversed(K):
        pts.loc[pts[f"exp_{k}"], "menor_cota"] = k

    # ---------------- B2: população estimada por endereço
    n_set = pts.groupby("setor_2022").size()
    pts["pop_est_setor"] = pts.setor_2022.map(s["pop"] / n_set)
    pop_set_sem_end = s[(s["pop"] > 0) & ~s.index.isin(n_set.index)]
    fech_setor = {"pop_municipio_setores": float(s["pop"].sum()), "pop_alocada_aos_enderecos": float(pts.pop_est_setor.sum()),
                  "diferenca": float(pts.pop_est_setor.sum() - s["pop"].sum()),
                  "setores_com_pop_e_sem_endereco": len(pop_set_sem_end), "pop_nesses_setores": float(pop_set_sem_end["pop"].sum()),
                  "setores_com_endereco_e_sem_pop": int(((s["pop"].reindex(n_set.index).fillna(0)) == 0).sum())}
    # sensibilidade pela grade (célula de 200 m na área urbana, 1 km na rural)
    jg = gpd.sjoin(pts[["geometry"]], gr[["ID_UNICO", "pop", "geometry"]], predicate="within", how="left")
    jg = jg[~jg.index.duplicated()]
    pts["celula"] = jg.ID_UNICO
    n_cel = pts.groupby("celula").size()
    pcel = gr.set_index("ID_UNICO")["pop"]
    pts["pop_est_grade"] = pts.celula.map(pcel / n_cel).fillna(0)
    cel_sem_end = pcel[(pcel > 0) & ~pcel.index.isin(n_cel.index)]
    fech_grade = {"pop_grade_total": float(pcel.sum()), "pop_alocada_aos_enderecos": float(pts.pop_est_grade.sum()),
                  "diferenca_para_pop_municipio": float(pts.pop_est_grade.sum() - s["pop"].sum()),
                  "celulas_com_pop_e_sem_endereco": len(cel_sem_end), "pop_nessas_celulas": float(cel_sem_end.sum()),
                  "enderecos_fora_de_celula": int(pts.celula.isna().sum())}

    # ---------------- B1/B2: tabela por cota
    pop_mun = float(s["pop"].sum())
    linhas = []
    for k in K:
        e = pts[pts[f"exp_cum_{k}"]]
        linhas.append({"cota_cm": k, "tr_anos_atributo_sgb": float(cotas.loc[k, "tr_anos"]), "area_mancha_km2": round(cotas.loc[k, "area_km2"], 2),
                       "enderecos": len(e), "enderecos_precisos_niv1_2": int(e.preciso.sum()), "enderecos_aproximados_niv3_5": int((~e.preciso).sum()),
                       "enderecos_em_coordenada_repetida": int((e.n_no_local > 1).sum()),
                       "locais_com_enderecos_empilhados": int(e[e.n_no_local > 1].local.nunique()),
                       "enderecos_urbanos": int((e.situacao == "Urbana").sum()), "enderecos_rurais": int((e.situacao == "Rural").sum()),
                       "enderecos_menor_cota_esta": int((pts.menor_cota == k).sum()),
                       "pop_estimada_setor": round(float(e.pop_est_setor.sum()), 1), "pop_estimada_grade": round(float(e.pop_est_grade.sum()), 1),
                       "pct_pop_municipio_setor": round(100 * e.pop_est_setor.sum() / pop_mun, 2)})
    t1 = pd.DataFrame(linhas)
    t1["dif_grade_menos_setor_pct"] = (100 * (t1.pop_estimada_grade - t1.pop_estimada_setor) / t1.pop_estimada_setor.replace(0, np.nan)).round(1)
    t1["pop_estimada_menor_cota_esta"] = [round(float(pts.loc[pts.menor_cota == k, "pop_est_setor"].sum()), 1) for k in K]
    # não aninhamento: endereços dentro de uma cota menor e fora de uma maior
    fora_maior = {f"{a}_fora_de_{b}": int((pts[f"exp_{a}"] & ~pts[f"exp_{b}"]).sum()) for i, a in enumerate(K) for b in K[i + 1:]}
    # diferença para a rodada 03 (não cumulativa), guardada com o sufixo _nao-cumulativo
    nc = TAB / "enderecos-populacao-por-cota_sgb-ibge-cnefe_2022_municipal_nao-cumulativo.csv"
    if nc.exists():
        r3 = pd.read_csv(nc).set_index("cota_cm")
        t1["enderecos_rodada03_nao_cumulativo"] = t1.cota_cm.map(r3.enderecos)
        t1["dif_enderecos_vs_rodada03"] = t1.enderecos - t1.enderecos_rodada03_nao_cumulativo
        t1["pop_estimada_setor_rodada03"] = t1.cota_cm.map(r3.pop_estimada_setor)
        t1["dif_pop_setor_vs_rodada03"] = (t1.pop_estimada_setor - t1.pop_estimada_setor_rodada03).round(1)
    arq = TAB / "enderecos-populacao-por-cota_sgb-ibge-cnefe_2022_municipal.csv"
    t1.to_csv(arq, index=False)
    meta(arq, descricao="endereços de domicílio particular (CNEFE 2022) expostos a cada cota e população estimada; "
         "CUMULATIVO: exposto à cota X = dentro da união das manchas de cota <= X; 'menor_cota_esta' = faixa pela menor cota que atinge o endereço; "
         "colunas *_rodada03 = definição não cumulativa da rodada 03",
         definicao_exposicao="cumulativa (união das manchas de cota <= X), rodada 04",
         metodo_populacao="população do setor 2022 repartida igualmente entre os endereços do setor (ESTIMATIVA); sensibilidade: população da célula da grade 2022 repartida entre os endereços da célula",
         fechamento_setor=fech_setor, fechamento_grade=fech_grade, enderecos_em_cota_menor_fora_da_maior=fora_maior, cotas=info_cotas)
    print(t1.to_string(index=False)); print(json.dumps({"setor": fech_setor, "grade": fech_grade, "fora_maior": fora_maior}, ensure_ascii=False))

    # ---------------- B4: perfil e bairros
    pts["pct60"] = pts.setor_2022.map(s.pct_60_mais)
    pts["pct014"] = pts.setor_2022.map(s.pct_0_14)
    pts["pctdom1"] = pts.setor_2022.map(s.pct_dom_1_morador)
    perfil, bairros = [], []

    def wmean(v, w):
        ok = v.notna() & (w > 0)
        return float((v[ok] * w[ok]).sum() / w[ok].sum()) if ok.any() else np.nan

    for k in K:
        e = pts[pts[f"exp_cum_{k}"]]
        sig60 = e[e.pct60.isna()]
        sig014 = e[e.pct014.isna()]
        sigd = e[e.pctdom1.isna()]
        perfil.append({"cota_cm": k, "pop_estimada": round(float(e.pop_est_setor.sum()), 1),
                       "pct_60_mais": round(wmean(e.pct60, e.pop_est_setor), 1), "pct_0_14": round(wmean(e.pct014, e.pop_est_setor), 1),
                       "pct_dom_1_morador": round(wmean(e.pctdom1, pd.Series(1.0, index=e.index)), 1),
                       "setores_sigilo_60": int(sig60.setor_2022.nunique()), "pop_est_sigilo_60": round(float(sig60.pop_est_setor.sum()), 1),
                       "setores_sigilo_0_14": int(sig014.setor_2022.nunique()), "pop_est_sigilo_0_14": round(float(sig014.pop_est_setor.sum()), 1),
                       "setores_sigilo_dom1": int(sigd.setor_2022.nunique()), "enderecos_sigilo_dom1": len(sigd)})
        b = e.groupby("bairro").agg(enderecos=("pop_est_setor", "size"), pop_estimada=("pop_est_setor", "sum")).sort_values("enderecos", ascending=False)
        b["pct_dos_enderecos_expostos"] = 100 * b.enderecos / len(e)
        b["enderecos_do_bairro"] = b.index.map(pts.groupby("bairro").size())
        b["pct_do_bairro_exposto"] = 100 * b.enderecos / b.enderecos_do_bairro
        bairros.append(b.reset_index().assign(cota_cm=k, posicao=range(1, len(b) + 1)))
    ref = {"municipio": {"pct_60_mais": round(100 * s.pop_60_mais.sum() / s["pop"].sum(), 1), "pct_0_14": round(100 * s.pop_0_14.sum() / s["pop"].sum(), 1),
                         "pct_dom_1_morador_media_enderecos": round(wmean(pts.pctdom1, pd.Series(1.0, index=pts.index)), 1)}}
    tp = pd.DataFrame(perfil)
    arq = TAB / "perfil-expostos-por-cota_sgb-ibge_2022_municipal.csv"
    tp.to_csv(arq, index=False)
    meta(arq, definicao_exposicao="cumulativa (união das manchas de cota <= X), rodada 04", descricao="perfil da população estimada exposta por cota; % 60+ e % 0–14 do setor ponderados pela população estimada exposta "
         "(setores sob sigilo fora da média); % de domicílios com um morador do setor ponderado pelos endereços expostos", referencia=ref)
    tb = pd.concat(bairros)[["cota_cm", "posicao", "bairro", "enderecos", "pop_estimada", "pct_dos_enderecos_expostos", "enderecos_do_bairro", "pct_do_bairro_exposto"]].round(1)
    arq = TAB / "bairros-expostos-por-cota_sgb-ibge-cnefe_2022_bairro.csv"
    tb.to_csv(arq, index=False)
    meta(arq, definicao_exposicao="cumulativa (união das manchas de cota <= X), rodada 04", descricao="bairros (IBGE 2022, atributo do setor do endereço) por número de endereços expostos em cada cota")
    print(tp.to_string(index=False)); print(ref); print(tb[tb.posicao <= 6].to_string(index=False))

    # ---------------- B5: dinâmica 2010–2022
    mapa_ac = {s22: r.id_area for r in ac.itertuples() for s22 in r.setores_2022.split(";")}
    pts["id_area"] = pts.setor_2022.map(mapa_ac)
    a = ac.set_index("id_area")
    pts["classe_abs"] = pts.id_area.map(a.classe_mudanca)
    pts["classe_rel"] = pts.id_area.map(a.classe_rel_pop)
    pts["ac_ganhou"] = pts.id_area.map(a.var_pop_abs > 0)
    din, din_cls = [], []
    for k in K:
        e = pts[pts[f"exp_cum_{k}"]]
        acs = a.loc[e.id_area.dropna().unique()]
        w = e.groupby("id_area").pop_est_setor.sum()
        din.append({"cota_cm": k, "pop_estimada_exposta": round(float(e.pop_est_setor.sum()), 1),
                    "pop_exposta_em_areas_que_ganharam": round(float(e.loc[e.ac_ganhou == True, "pop_est_setor"].sum()), 1),  # noqa: E712
                    "pop_exposta_em_areas_que_perderam": round(float(e.loc[e.ac_ganhou == False, "pop_est_setor"].sum()), 1),  # noqa: E712
                    "areas_comparaveis_com_endereco_exposto": len(acs), "dessas_ganharam": int((acs.var_pop_abs > 0).sum()),
                    "pop_2010_dessas_areas": float(acs.pop_2010.sum()), "pop_2022_dessas_areas": float(acs.pop_2022.sum()),
                    "var_pct_dessas_areas": round(100 * (acs.pop_2022.sum() / acs.pop_2010.sum() - 1), 2),
                    "var_pct_ponderada_pela_exposicao": round(float((acs.var_pop_pct * w.reindex(acs.index)).sum() / w.sum()), 2)})
        for tipo, col in (("absoluta", "classe_abs"), ("relativa", "classe_rel")):
            for cl, v in e.groupby(col).pop_est_setor.sum().items():
                din_cls.append({"cota_cm": k, "leitura": tipo, "classe": cl, "pop_estimada_exposta": round(float(v), 1),
                                "pct_da_pop_exposta": round(100 * float(v) / e.pop_est_setor.sum(), 1)})
    td = pd.DataFrame(din)
    td["pct_exposta_em_areas_que_perderam"] = (100 * td.pop_exposta_em_areas_que_perderam / td.pop_estimada_exposta).round(1)
    tdc = pd.DataFrame(din_cls)
    arq = TAB / "expostos-dinamica-2010-2022-por-cota_sgb-ibge_2010-2022_area-comparavel.csv"
    td.to_csv(arq, index=False)
    meta(arq, definicao_exposicao="cumulativa (união das manchas de cota <= X), rodada 04", descricao="população estimada exposta por cota segundo a mudança 2010–2022 da área comparável onde está o endereço; "
         "variação das áreas comparáveis com endereço exposto (simples e ponderada pela população exposta)",
         variacao_pct_municipio=round(100 * (ac.pop_2022.sum() / ac.pop_2010.sum() - 1), 2))
    arq = TAB / "expostos-classes-mudanca-por-cota_sgb-ibge_2010-2022_area-comparavel.csv"
    tdc.to_csv(arq, index=False)
    meta(arq, definicao_exposicao="cumulativa (união das manchas de cota <= X), rodada 04", descricao="população estimada exposta por cota e classe de mudança 2010–2022 (absoluta: limiares ±5/±20 %; relativa à variação do município: ±5/±15 p.p.)")
    print(td.to_string(index=False)); print(tdc.to_string(index=False))

    # ---------------- unidades de saúde (ESF/UBS) e as cotas (rodada 06)
    unidades_e_cotas(cotas, K)

    # ---------------- camada de pontos (fora do git)
    out = CAMADAS / "enderecos-exposicao-inundacao_sgb-ibge-cnefe_2022_pontos.gpkg"
    cols = ["COD_UNICO_ENDERECO", "setor_2022", "bairro", "situacao", "NV_GEO_COORD", "preciso", "n_no_local", "menor_cota", *exp_cols, *cum_cols,
            "pop_est_setor", "pop_est_grade", "id_area", "classe_abs", "classe_rel", "geometry"]
    pts[cols].to_file(out, driver="GPKG", layer="enderecos_exposicao")
    meta(out, descricao="endereços de domicílio particular (CNEFE 2022) com a exposição a cada cota, população estimada (setor e grade) e área comparável",
         fora_do_git="data/processed/ é ignorado; não publicar")

    # ---------------- B6: mapas
    mapas(pts, cotas, K, limite, st)
    mapas_localizacao_unidades(st)


def clarear(cor: str, f: float) -> str:
    """Mistura a cor com branco (f = fração de branco): tom claro da mesma família."""
    r, g, b = (int(cor[i:i + 2], 16) for i in (1, 3, 5))
    return "#" + "".join(f"{round(v + (255 - v) * f):02x}" for v in (r, g, b))


COR_PONTO4 = COR_PONTO[:4]
COR_MANCHA_FAMILIA = [clarear(x, 0.72) for x in COR_PONTO4]  # mancha de cada cota no tom claro da cor dos seus pontos
COR_SERIE_MANCHA, COR_SERIE_PONTO = "#9e9ac8", "#e66101"  # série por cota: mancha roxa clara × ponto laranja (contraste p/ daltônicos)
NOTA_RECORTE = "Manchas recortadas no limite municipal só para exibição; o dado original do SGB cobre também a outra margem do rio."


def rotular_unidades(ax, u, escala: str, fs: float, fs_nota: float = 6.8) -> None:
    """Rótulos das unidades de saúde com regras anti-sobreposição (rodada 06):
    - município: só as unidades do interior; as 18 urbanas recebem uma nota única;
    - área urbana: ESF 21 e UDM (mesmo endereço, 63 m) num rótulo só, "21 · UDM";
    - recortes de detalhe: cada unidade com o seu rótulo (21 e UDM com deslocamentos próprios)."""
    halo = [pe.withStroke(linewidth=2.2, foreground="#ffffff")]
    alvo = u if escala != "municipio" else u[u.zona == "interior"]
    juntar = escala == "urbano" and {"21", "UDM"} <= set(alvo.rotulo)
    for r in alvo.itertuples():
        if juntar and r.rotulo == "UDM":
            continue
        txt, off = r.rotulo, OFFSET_ROTULO.get(r.rotulo, (4, 3))
        if juntar and r.rotulo == "21":
            txt, off = "21 · UDM", (-44, 5)
        ax.annotate(txt, (r.geometry.x, r.geometry.y), xytext=off, textcoords="offset points", fontsize=fs, fontweight="bold",
                    color=INK, zorder=11, path_effects=halo)
    if escala == "municipio":
        urb = u[u.zona == "urbana da sede"]
        if len(urb):
            ax.annotate(f"{len(urb)} unidades na área urbana da sede\n(rótulos no mapa da área urbana)", (urb.geometry.x.mean(), urb.geometry.y.mean()),
                        xytext=(48, 22), textcoords="offset points", fontsize=fs_nota, color=INK, zorder=11, ha="left",  # à direita: abaixo fica a Prisional
                        arrowprops=dict(arrowstyle="-", color=INK, lw=0.6),
                        bbox=dict(boxstyle="round,pad=0.25", facecolor="#ffffff", edgecolor="#8f8e88", linewidth=0.5, alpha=0.92))


def unidades_e_cotas(cotas, K):
    """Tabelas da rodada 06: menor cota (cumulativa) que atinge cada unidade ESF/UBS e unidades por cota."""
    unid = gpd.read_file(ARQ_UNIDADES).to_crs(c.CRS_PADRAO)
    uniao = {k: cotas.loc[K[: i + 1], "geometry"].union_all() for i, k in enumerate(K)}
    maior = uniao[K[-1]]
    unid["menor_cota_cm"] = None
    for k in reversed(K):
        unid.loc[unid.within(uniao[k]), "menor_cota_cm"] = k
    unid["situacao"] = np.where(unid.menor_cota_cm.isna(), "fora das manchas", "dentro")
    unid["dist_borda_maior_mancha_m"] = unid.geometry.apply(lambda g: round(float(g.distance(maior.boundary)), 0))
    cols = ["rotulo", "cnes", "nome", "classe", "zona", "bairro", "menor_cota_cm", "situacao", "dist_borda_maior_mancha_m"]
    t = unid[cols].sort_values("dist_borda_maior_mancha_m")
    arq = TAB / "unidades-saude-cotas-inundacao_sgb-cnes-revisado_2026_unidade.csv"
    t.to_csv(arq, index=False)
    meta(arq, descricao="unidades de saúde ESF/UBS: menor cota cuja mancha (cumulativa: união das manchas de cota <= X) atinge a unidade, "
         "ou 'fora das manchas', e distância até a borda da maior mancha (cota 1252 cm, união de todas)", credito_unidades=CREDITO_SAUDE,
         definicao_exposicao="cumulativa (união das manchas de cota <= X)")
    linhas = []
    for k in K:
        d = unid[unid.within(uniao[k])]
        linhas.append({"cota_cm": k, "tr_anos": float(cotas.loc[k, "tr_anos"]), "unidades_na_mancha": len(d),
                       "quais": "; ".join(f"{r.rotulo} ({r.classe})" for r in d.itertuples()) or "nenhuma"})
    tc = pd.DataFrame(linhas)
    arq2 = TAB / "unidades-saude-cotas-inundacao_sgb-cnes-revisado_2026_municipal.csv"
    tc.to_csv(arq2, index=False)
    meta(arq2, descricao="número e lista das unidades de saúde ESF/UBS dentro da mancha de cada cota (cumulativa)", credito_unidades=CREDITO_SAUDE,
         definicao_exposicao="cumulativa (união das manchas de cota <= X)",
         substitui="data/processed/saude-estabelecimentos-exposicao-inundacao_por-cota.csv (CNES antigo), movida para data/processed/_substituidos/")
    print(t.to_string(index=False)); print(tc.to_string(index=False))


def mapas_localizacao_unidades(st):
    """Rodada 06, Tarefa 4b: localização das 23 unidades sobre a densidade de 2022 em tons claros (para conferência da posição)."""
    base = Base(ARGS.codigo_ibge, agua=True, nome_rio=ARGS.nome_rio, modo_agua="tematico")
    fundo = Fundo(base)
    unid = gpd.read_file(ARQ_UNIDADES).to_crs(c.CRS_PADRAO)
    claros = [clarear(x, 0.55) for x in SEQ]  # densidade em tons claros: os símbolos das unidades ficam por cima
    lim = [0, 5, 25, 50, 75, 100, np.inf]
    rot = ["0 a < 5", "5 a < 25", "25 a < 50", "50 a < 75", "75 a < 100", "≥ 100"]
    st = st.copy()
    st["_cl"] = np.searchsorted(lim[1:-1], st.dens_hab_ha.fillna(-1), side="right")
    for rec, ext, esc, fs in (("municipio", base.ext_mun, 20000, 6.5), ("urbano", base.ext_urb, 1000, 7)):
        a4 = layout() == "a4"
        if a4:
            lay = LayoutA4([[ext]])
            fig, ax = lay.fig, lay.eixos[0]
        else:
            fig, ax = plt.subplots(figsize=(7.2, 7.2 * (ext[3] - ext[2]) / (ext[1] - ext[0]) + 0.6))
        for i, cor in enumerate(claros):
            st[st._cl == i].plot(ax=ax, color=cor, edgecolor="#ffffff", linewidth=0.15, zorder=0.5)
        fundo.desenhar(ax, ext, rec, esc, modo_agua="tematico")
        b = box(ext[0], ext[2], ext[1], ext[3])
        u = unid[unid.within(b)]
        for classe, (mk, cor) in SIMB_UNIDADE.items():
            x = u[u.classe == classe]
            ax.scatter(x.geometry.x, x.geometry.y, marker=mk, s=TAM_UNIDADE[classe] + 6, color=cor, edgecolor=INK, linewidth=0.9, zorder=9)
        rotular_unidades(ax, u, rec, fonte_rotulo(fs, layout()), fs_nota=fonte_rotulo(6.8, layout()))
        hand = [Line2D([], [], marker=mk, ls="", mfc=cor, mec=INK, ms=7, label=f"{classe} ({int((unid.classe == classe).sum())})")
                for classe, (mk, cor) in SIMB_UNIDADE.items()]
        hand += [Patch(facecolor=cor, edgecolor="#b5b4ad", linewidth=0.3, label=l) for cor, l in zip(claros, rot)]
        if a4:
            sub = "município inteiro" if rec == "municipio" else "área urbana da sede"
            origem = MAPAS / f"unidades-saude-esf-ubs-localizacao_cnes-revisado-v4_2026_pontos_{rec}.png"
            finalizar_a4(lay, f"Unidades de saúde da atenção primária (ESF e UBS) — localização\n{sub}", fundo.comum(hand),
                         "unidade de saúde (ESF/UBS) por classe, rótulo = número; densidade 2022 (hab/ha), tons claros",
                         f"Unidades: {CREDITO_SAUDE}. Densidade: IBGE, Censo 2022 — malha e agregados por setor. Vias e água: OpenStreetMap.",
                         pasta_a4(MAPAS) / origem.name, origem=origem,
                         texto_retirado="Mapa para conferência da posição das unidades — pendente de conferência. EPSG:31981.")
            continue
        ax.legend(handles=fundo.comum(hand), title="unidade de saúde (ESF/UBS), por classe\n— rótulo = número da unidade —\ndensidade 2022 (hab/ha), tons claros",
                  loc="upper left", bbox_to_anchor=(1.01, 1), frameon=False, fontsize=7.5, title_fontsize=8, alignment="left")
        sub = "município inteiro" if rec == "municipio" else "área urbana da sede"
        ax.set_title(f"Unidades de saúde da atenção primária (ESF e UBS) — localização\n{sub}", loc="left", fontsize=10, color=INK)
        ax.annotate(f"Unidades: {CREDITO_SAUDE}.\nDensidade: IBGE, Censo 2022 — malha e agregados por setor. Vias e água: OpenStreetMap.\n"
                    "Mapa para conferência da posição das unidades — pendente de conferência. EPSG:31981.",
                    xy=(0, -0.015), xycoords="axes fraction", va="top", fontsize=6.3, color=MUTED)
        caminho = MAPAS / f"unidades-saude-esf-ubs-localizacao_cnes-revisado-v4_2026_pontos_{rec}.png"
        fig.savefig(caminho, dpi=190, facecolor="#fcfcfb", bbox_inches="tight")
        plt.close(fig)
        meta(caminho, recorte=sub, tema="localizacao_unidades_saude", credito_unidades=CREDITO_SAUDE, unidades_no_enquadramento=len(u),
             fundo="densidade demográfica por setor 2022 em tons claros")
        logger.info("Mapa: %s", caminho.relative_to(c.RAIZ))


def mapas(pts, cotas, K, limite, st):
    """Mapas da rodada 04 em docs/exposicao_inundacao/mapas_v2/ (exposição CUMULATIVA)."""
    base = Base(ARGS.codigo_ibge, agua=True, nome_rio=ARGS.nome_rio, modo_agua="enderecos")
    fundo = Fundo(base)
    unid = gpd.read_file(ARQ_UNIDADES).to_crs(c.CRS_PADRAO)
    lim = limite.union_all()
    # manchas cumulativas (união das cotas <= k), recortadas no limite municipal só para exibição
    uniao = {}
    for i, k in enumerate(K):
        uniao[k] = gpd.GeoSeries([cotas.loc[K[: i + 1], "geometry"].union_all()], crs=c.CRS_PADRAO).iloc[0]
    disp = {k: uniao[k].intersection(lim) for k in K}
    # janela ribeirinha: a mesma da rodada 03 (quantis 5–95 % dos urbanos na mancha de 1252 cm, + 350 m)
    exp_max = pts[pts[f"exp_{K[-1]}"] & (pts.situacao == "Urbana")]
    x0, x1 = exp_max.geometry.x.quantile([0.05, 0.95])
    y0, y1 = exp_max.geometry.y.quantile([0.05, 0.95])
    m = 350
    exts = {"municipio": base.ext_mun, "urbano": base.ext_urb, "ribeirinha": (x0 - m, x1 + m, y0 - m, y1 + m)}
    esc = {"municipio": 20000, "urbano": 1000, "ribeirinha": 250}
    sub = {"municipio": "município inteiro", "urbano": "área urbana da sede",
           "ribeirinha": "faixa ribeirinha da área urbana (onde se concentram os endereços urbanos expostos à maior cota)"}
    rot_cota = {k: f"cota {fmt_cota(k)} (TR {cotas.loc[k, 'tr_anos']:g} anos)".replace(".", ",") for k in K}  # vírgula decimal
    feitos = []

    def rec_fundo(rec):
        return rec if rec != "ribeirinha" else "detalhe"

    a4 = layout() == "a4"
    SUB_A4 = {"municipio": "município inteiro", "urbano": "área urbana da sede", "ribeirinha": "faixa ribeirinha da área urbana"}

    def saude(ax, ext, k, fs=6.5, esc_simb=1.0):
        """Todas as unidades ESF/UBS no enquadramento, símbolo por classe, rótulo com o número;
        anel vermelho nas que ficam dentro da mancha (cumulativa) da cota k do mapa."""
        b = box(ext[0], ext[2], ext[1], ext[3])
        u = unid[unid.within(b)]
        dentro = u[u.within(uniao[k])]
        for classe, (mk, cor) in SIMB_UNIDADE.items():
            x = u[u.classe == classe]
            ax.scatter(x.geometry.x, x.geometry.y, marker=mk, s=TAM_UNIDADE[classe] * esc_simb, color=cor, edgecolor=INK,
                       linewidth=0.9 * min(1, esc_simb ** 0.5), zorder=9)
        if len(dentro):
            ax.scatter(dentro.geometry.x, dentro.geometry.y, marker="o", s=190 * esc_simb, facecolor="none", edgecolor="#d7191c",
                       linewidth=1.8 * min(1, esc_simb ** 0.5), zorder=9.5)
        rotular_unidades(ax, u, c.escala_do_mapa(ext), fonte_rotulo(fs, layout()), fs_nota=fonte_rotulo(6.8, layout()))
        hand = [Line2D([], [], marker=mk, ls="", mfc=cor, mec=INK, ms=6.5, label=f"unidade de saúde (ESF/UBS): {classe}")
                for classe, (mk, cor) in SIMB_UNIDADE.items() if (u.classe == classe).any()]
        if len(dentro):
            hand.append(Line2D([], [], marker="o", ls="", mfc="none", mec="#d7191c", mew=1.8, ms=11, label="unidade dentro da mancha desta cota"))
        return hand

    def plot_geom(ax, geom, **kw):
        gpd.GeoSeries([geom], crs=c.CRS_PADRAO).plot(ax=ax, **kw)

    def rodape(ax):
        ax.annotate(f"{FONTE_MAPA}\n{NOTA_RECORTE}\nPonto = endereço, sem número de moradores. Exposição cumulativa (união das manchas de cota ≤ X). "
                    "Produto de trabalho — pendente de conferência. EPSG:31981.", xy=(0, -0.015), xycoords="axes fraction", va="top", fontsize=6.3, color=MUTED)

    def salvar(fig, ax, nome, titulo, rec, hand, leg_tit, extra, quadro_k=None, leg_a4=None):
        if a4:  # cota e totais no quadro de notas, abaixo da legenda
            origem = MAPAS / f"{nome}_{rec}.png"
            notas = None
            if quadro_k is not None:
                e = pts[pts[f"exp_cum_{quadro_k}"]]
                notas = f"{rot_cota[quadro_k]} · {mil(len(e))} endereços expostos · ≈ {mil(e.pop_est_setor.sum())} pessoas (estimativa)"
            destino = pasta_a4(MAPAS) / origem.name
            finalizar_a4(fig._layout_a4, f"{titulo}\n{SUB_A4[rec]}", fundo.comum(hand), leg_a4 or leg_tit.replace("\n", " "), FONTE_A4, destino,
                         origem=origem, metodo=METODO_A4, notas=notas, texto_retirado=f"{NOTA_RECORTE} {RETIRADO_A4}",
                         meta_extra={"recorte_completo": sub[rec]})
            feitos.append(destino)
            return
        leg = ax.legend(handles=fundo.comum(hand), title=leg_tit, loc="upper left", bbox_to_anchor=(1.01, 1), frameon=False, fontsize=7.5,
                        title_fontsize=8, alignment="left")
        if quadro_k is not None:  # quadro da cota logo abaixo da legenda, medida no desenho (não cobre a legenda nem o mapa)
            fig.canvas.draw()
            y_leg = ax.transAxes.inverted().transform(leg.get_window_extent(fig.canvas.get_renderer()))[0][1]
            quadro(ax, quadro_k, fs=8.5, y_topo=y_leg - 0.03)
        ax.set_title(f"{titulo}\n{sub[rec]}", loc="left", fontsize=10, color=INK)
        rodape(ax)
        caminho = MAPAS / f"{nome}_{rec}.png"
        fig.savefig(caminho, dpi=180, facecolor="#fcfcfb", bbox_inches="tight")
        plt.close(fig)
        meta(caminho, recorte=sub[rec], janela_m=[round(v) for v in exts[rec]], definicao_exposicao="cumulativa (união das manchas de cota <= X)",
             manchas_exibicao=NOTA_RECORTE, area_de_agua="OpenStreetMap, só apresentação; outras águas pela regra c.AGUA_EXIBICAO['enderecos']",
             unidades_saude=f"23 unidades ESF/UBS ({CREDITO_SAUDE}); todas as que caem no enquadramento, com destaque para as que ficam dentro da mancha da cota do mapa", **extra)
        feitos.append(caminho)

    def fig_ext(ext, largura=7.2):
        if a4:
            lay = LayoutA4([[ext]])
            lay.fig._layout_a4 = lay
            return lay.fig, lay.eixos[0]
        return plt.subplots(figsize=(largura, largura * (ext[3] - ext[2]) / (ext[1] - ext[0]) + 0.6))

    def quadro(ax, k, fs=8, no_titulo=False, y_topo=1.0):
        """Cota, TR e totais FORA da área do mapa, para não cobrir unidade de saúde, rótulo nem endereço (rodada 07):
        nos mapas avulsos, num quadro à direita, abaixo da legenda; no painel 2 x 2, como título de cada mapa."""
        e = pts[pts[f"exp_cum_{k}"]]
        n, popk = mil(len(e)), mil(e.pop_est_setor.sum())
        if no_titulo:
            ax.set_title(f"{rot_cota[k]} · {n} endereços expostos · ≈ {popk} pessoas (estimativa)", loc="left", fontsize=fs, color=INK, pad=4)
            return
        ax.text(1.02, y_topo, f"{rot_cota[k]}\n{n} endereços expostos\n≈ {popk} pessoas (estimativa)", transform=ax.transAxes,
                ha="left", va="top", fontsize=fs, color=INK, zorder=12, clip_on=False,
                bbox=dict(boxstyle="round,pad=0.4", facecolor="#ffffff", edgecolor="#8f8e88", linewidth=0.6))

    def desenhar_serie(ax, k, rec, ms_fora, ms_exp, esc_simb=1.0):
        ext = exts[rec]
        plot_geom(ax, disp[k], color=COR_SERIE_MANCHA, alpha=0.45, edgecolor="none", zorder=1.5)
        plot_geom(ax, disp[k].boundary, color="#54278f", linewidth=0.5, zorder=1.6)
        fora = pts[~pts[f"exp_cum_{k}"]]
        e = pts[pts[f"exp_cum_{k}"]]
        ax.scatter(fora.geometry.x, fora.geometry.y, s=ms_fora, color="#bdbcb5", alpha=0.6, linewidths=0, zorder=5)
        ax.scatter(e.geometry.x, e.geometry.y, s=ms_exp, color=COR_SERIE_PONTO, edgecolor="#3a1500", linewidths=0.25, zorder=7)
        fundo.desenhar(ax, ext, rec_fundo(rec), esc[rec], modo_agua="enderecos", escala_pos=ESCALA_POS[rec])
        return saude(ax, ext, k, esc_simb=esc_simb)

    hand_serie = [Patch(facecolor=COR_SERIE_MANCHA, alpha=0.45, edgecolor="#54278f", linewidth=0.5, label="mancha de inundação até a cota\n(união das cotas ≤ X)"),
                  Line2D([], [], marker="o", ls="", mfc=COR_SERIE_PONTO, mec="#3a1500", ms=5, label="endereço exposto"),
                  Line2D([], [], marker="o", ls="", color="#bdbcb5", ms=4, label="endereço fora da mancha")]

    # (a) série por cota + painel 2 x 2
    for rec in ("urbano", "ribeirinha"):
        ext = exts[rec]
        ms_fora, ms_exp = (0.4, 4) if rec == "urbano" else (2, 10)
        for k in K:
            fig, ax = fig_ext(ext)
            hs = desenhar_serie(ax, k, rec, ms_fora, ms_exp)
            salvar(fig, ax, f"enderecos-expostos-cota{k}_sgb-ibge-cnefe_2022_pontos", f"Endereços expostos à inundação até a cota {fmt_cota(k)}", rec,
                   hand_serie + hs, "mesma legenda e enquadramento\nnos 4 mapas da série", {"tema": "serie_por_cota", "cota_cm": k,
                   "enderecos_expostos": int(pts[f"exp_cum_{k}"].sum()), "pop_estimada": round(float(pts.loc[pts[f"exp_cum_{k}"], "pop_est_setor"].sum()), 1)}, quadro_k=k)
        if a4:
            # painel A4: 2 x 2 quadros de meia largura (≈ 0,57 do quadro lateral): pontos e símbolos reduzidos na mesma proporção
            lay = LayoutA4([[ext, ext], [ext, ext]])
            hs_painel = []
            for ax, k in zip(lay.eixos, K):
                hs = desenhar_serie(ax, k, rec, ms_fora * 0.7 * 0.45, ms_exp * 0.6 * 0.45, esc_simb=0.5)
                if len(hs) > len(hs_painel):
                    hs_painel = hs
                quadro(ax, k, fs=8, no_titulo=True)
            origem = MAPAS / f"enderecos-expostos-4-cotas-painel_sgb-ibge-cnefe_2022_pontos_{rec}.png"
            destino = pasta_a4(MAPAS) / origem.name
            finalizar_a4(lay, f"Endereços expostos à inundação, cota a cota\n{SUB_A4[rec]}", fundo.comum(hand_serie + hs_painel),
                         "mesma legenda e enquadramento nos 4 quadros", FONTE_A4, destino, origem=origem, metodo=METODO_A4,
                         texto_retirado=f"{NOTA_RECORTE} {RETIRADO_A4}", meta_extra={"recorte_completo": sub[rec]})
            feitos.append(destino)
            continue
        h = (ext[3] - ext[2]) / (ext[1] - ext[0])
        # layout explícito (o tight_layout cortava os quadros da linha de cima no recorte urbano):
        # 2 x 2 mapas de largura fixa, faixa de legenda e faixa de rodapé com altura própria (polegadas)
        larg_ax, leg_h, rod_h, tit_h, sub_h = 5.4, 0.95, 0.55, 0.45, 0.3  # sub_h: título de cada mapa (cota e totais)
        alt_ax = larg_ax * h
        W, H = 2 * larg_ax + 0.35, 2 * alt_ax + 0.15 + 2 * sub_h + leg_h + rod_h + tit_h
        fig, axs = plt.subplots(2, 2, figsize=(W, H))
        fig.subplots_adjust(left=0.1 / W, right=1 - 0.1 / W, top=1 - (tit_h + sub_h) / H, bottom=(leg_h + rod_h) / H,
                            wspace=0.15 / larg_ax, hspace=(0.15 + sub_h) / alt_ax)
        hs_painel = []
        for ax, k in zip(axs.flat, K):
            hs = desenhar_serie(ax, k, rec, ms_fora * 0.7, ms_exp * 0.6)
            if len(hs) > len(hs_painel):  # legenda do painel com todas as classes que aparecem
                hs_painel = hs
            quadro(ax, k, fs=8, no_titulo=True)
        fig.legend(handles=fundo.comum(hand_serie + hs_painel), loc="upper center", ncol=4, frameon=False, fontsize=7.5,
                   bbox_to_anchor=(0.5, (leg_h + rod_h - 0.08) / H))
        fig.suptitle(f"Endereços expostos à inundação, cota a cota — {sub[rec]}", x=0.01, y=1 - 0.12 / H, ha="left", va="top", fontsize=11, color=INK)
        fig.text(0.01, 0.06 / H, f"{FONTE_MAPA}\n{NOTA_RECORTE} Exposição cumulativa. Produto de trabalho — pendente de conferência. EPSG:31981.",
                 fontsize=6.5, color=MUTED, va="bottom")
        caminho = MAPAS / f"enderecos-expostos-4-cotas-painel_sgb-ibge-cnefe_2022_pontos_{rec}.png"
        fig.savefig(caminho, dpi=170, facecolor="#fcfcfb", bbox_inches="tight")
        plt.close(fig)
        meta(caminho, recorte=sub[rec], tema="painel_4_cotas", definicao_exposicao="cumulativa", manchas_exibicao=NOTA_RECORTE)
        feitos.append(caminho)

    # (b) menor cota que atinge, com as 4 manchas ao fundo no tom claro da cor dos pontos
    for rec in ("urbano", "ribeirinha"):
        ext = exts[rec]
        fig, ax = fig_ext(ext)
        for i, k in reversed(list(enumerate(K))):  # maior primeiro; a menor por cima
            plot_geom(ax, disp[k], color=COR_MANCHA_FAMILIA[i], edgecolor="none", zorder=1.5 + 0.1 * (len(K) - i))
        nao = pts[pts.menor_cota.isna()]
        ax.scatter(nao.geometry.x, nao.geometry.y, s=0.5 if rec == "urbano" else 2, color="#bdbcb5", alpha=0.5, linewidths=0, zorder=5)
        hand = []
        for i, k in reversed(list(enumerate(K))):
            e = pts[pts.menor_cota == k]
            ax.scatter(e.geometry.x, e.geometry.y, s=4 if rec == "urbano" else 11, color=COR_PONTO4[i], edgecolor="#ffffff", linewidths=0.2,
                       zorder=6 + 0.1 * (len(K) - i))
        for i, k in enumerate(K):
            hand.append(Line2D([], [], marker="o", ls="", color=COR_PONTO4[i], ms=5, label=f"{rot_cota[k]} — {mil((pts.menor_cota == k).sum())}"))
        hand.append(Line2D([], [], marker="o", ls="", color="#bdbcb5", ms=4, label="endereço fora das manchas"))
        hand += [Patch(facecolor=COR_MANCHA_FAMILIA[i], edgecolor="none", label=f"mancha até {fmt_cota(k)}") for i, k in enumerate(K)]
        fundo.desenhar(ax, ext, rec_fundo(rec), esc[rec], modo_agua="enderecos", escala_pos=ESCALA_POS[rec])
        hand += saude(ax, ext, K[-1])
        salvar(fig, ax, "enderecos-menor-cota-inundacao_sgb-ibge-cnefe_2022_pontos", "Endereços residenciais pela menor cota de inundação que os atinge", rec, hand,
               "menor cota que atinge o endereço\n(mancha ao fundo no tom claro da mesma cor)", {"tema": "menor_cota"})

    # (c) hexágonos de endereços expostos, cotas de referência, com a mancha ao fundo
    lim_hex = [1, 6, 21, 51, 101, 201, np.inf]
    lab_hex = ["1 a 5", "6 a 20", "21 a 50", "51 a 100", "101 a 200", "mais de 200"]
    for k in COTAS_HEX:
        hx = hexagonos(pts[pts[f"exp_cum_{k}"]])
        hx["cl"] = np.searchsorted(lim_hex[1:-1], hx.enderecos, side="right")
        for rec in ("urbano", "ribeirinha"):
            ext = exts[rec]
            fig, ax = fig_ext(ext)
            plot_geom(ax, disp[k], color=COR_SERIE_MANCHA, alpha=0.42, edgecolor="none", zorder=1.2)
            for i, cor in enumerate(SEQ):
                if (hx.cl == i).any():
                    hx[hx.cl == i].plot(ax=ax, color=cor, alpha=0.85, edgecolor="#ffffff", linewidth=0.3, zorder=2)
            plot_geom(ax, disp[k].boundary, color="#54278f", linewidth=0.9, linestyle="--", zorder=5)
            fundo.desenhar(ax, ext, rec_fundo(rec), esc[rec], modo_agua="enderecos", escala_pos=ESCALA_POS[rec])
            hs = saude(ax, ext, k)
            hand = [Patch(facecolor=cor, alpha=0.85, edgecolor="#b5b4ad", linewidth=0.3, label=l) for cor, l in zip(SEQ, lab_hex)]
            hand += [Patch(facecolor=COR_SERIE_MANCHA, alpha=0.42, edgecolor="#54278f", linestyle="--", linewidth=0.9, label=f"mancha até a {rot_cota[k]}")] + hs
            salvar(fig, ax, f"enderecos-expostos-hexagono-cota{k}_sgb-ibge-cnefe_2022_hex200m", f"Endereços expostos até a cota {fmt_cota(k)} por hexágono de 200 m", rec, hand,
                   "endereços expostos por hexágono\n(200 m entre lados opostos; vazios não pintados)",
                   {"tema": "hexagono_expostos", "cota_cm": k, "hexagonos": len(hx), "maximo_por_hexagono": int(hx.enderecos.max()) if len(hx) else 0})

    # (d) visão geral: manchas + endereços, recortadas no limite municipal
    hand_manchas = [Patch(facecolor=COR_MANCHA[i], edgecolor="#8f6f86", linewidth=0.3, label=f"até a {rot_cota[k]}") for i, k in enumerate(K)]
    for rec in ("municipio", "urbano", "ribeirinha"):
        ext = exts[rec]
        fig, ax = fig_ext(ext)
        for i, k in reversed(list(enumerate(K))):
            plot_geom(ax, disp[k], color=COR_MANCHA[i], edgecolor="#8f6f86", linewidth=0.3, zorder=1.5 + 0.1 * (len(K) - i))
        ms = {"municipio": 0.5, "urbano": 0.6, "ribeirinha": 3}[rec]
        ax.scatter(pts.geometry.x, pts.geometry.y, s=ms, color="#3a3a3a", alpha=0.45, linewidths=0, zorder=6)
        fundo.desenhar(ax, ext, rec_fundo(rec), esc[rec], modo_agua="enderecos", escala_pos=ESCALA_POS[rec])
        hs = saude(ax, ext, K[-1])
        hand = hand_manchas + [Line2D([], [], marker="o", ls="", color="#3a3a3a", alpha=0.6, ms=3, label="endereço de domicílio particular (CNEFE 2022)")] + hs
        salvar(fig, ax, "manchas-inundacao-enderecos_sgb-ibge-cnefe_2022_pontos", "Manchas de inundação por cota do rio e endereços residenciais", rec, hand,
               "mancha de inundação por cota (SGB)\nescura = cota mais baixa (cheia mais frequente)", {"tema": "manchas_e_enderecos"},
               leg_a4="mancha de inundação por cota (SGB); escura = cota mais baixa (cheia mais frequente)")
    for f in feitos:
        logger.info("Mapa: %s", f.relative_to(c.RAIZ))


if __name__ == "__main__":
    _p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    _p.add_argument("--codigo-ibge", default=c.CODIGO_IBGE_DEFAULT)
    _p.add_argument("--nome-rio", default=c.NOME_RIO_DEFAULT, help="rótulo do rio principal na legenda")
    _p.add_argument("--layout", default="lateral", choices=LAYOUTS, help="lateral (padrão, recalcula e grava tudo) ou a4 (só a edição A4 dos mapas)")
    ARGS = _p.parse_args()
    main()
