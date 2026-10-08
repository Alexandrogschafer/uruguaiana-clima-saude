"""
Auditoria de robustez das estimativas de exposição à inundação.

Mede o quanto as estimativas de população exposta que já existem dependem de
escolhas do método, SEM mudar nenhum produto existente. Tudo é recalculado pelas
funções dos estudos de origem, importadas:
  - delimitações: exposicao_inundacao_estimativas_oficiais.carregar_delimitacoes;
  - linhas e polígonos (corte pela cidade, borda deslocada):
    exposicao_inundacao_sintese.linhas_da_sintese e montar_poligonos;
  - contagem por endereços: exposicao_inundacao_cenarios.expor (gravar=False:
    nenhuma camada de pontos vai para o disco);
  - métodos por área do setor e por uso do solo:
    exposicao_inundacao_estimativas_oficiais.populacao_por_area_e_uso_do_solo e
    vulnerabilidade_inundacao.calcular_exposicao_por_setor (limiar como argumento).

Partes:
  A  prova de partida: os números da síntese têm de se repetir (senão, para);
  B  universo da população: espécies do CNEFE, domicílios ocupados, população em
     domicílio particular, setores sem endereço;
  C  limiar de área urbanizada do método por uso do solo;
  D  deslocamento da borda, nível de geocodificação, corte pelo limite municipal;
  E  união cumulativa das cheias;
  F  diferenças absolutas entre os métodos;
  G  ficha de métodos (docs/exposicao_inundacao/ficha_metodos_exposicao.md).

Saídas em data/processed/exposicao_inundacao_robustez/ (fora do git, como toda
data/processed/): cada tabela em .csv (sem arredondar), .md (leitura) e .json.
Só tabelas agregadas; nenhuma figura; nenhum ponto.

Uso:
  python scripts/processamento/exposicao_inundacao_robustez.py [--copia PASTA]
"""

from __future__ import annotations

import argparse
import inspect
import json
import logging
import platform
import shutil
import warnings
from importlib import metadata
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

import dinamica_populacional_comum as c
import exposicao_inundacao_cenarios as ec
import exposicao_inundacao_enderecos as ee
import exposicao_inundacao_estimativas_oficiais as eo
import exposicao_inundacao_sintese as es
import vulnerabilidade_inundacao as vi

logger = logging.getLogger(__name__)

SCRIPT = "scripts/processamento/exposicao_inundacao_robustez.py"
SAIDA = c.RAIZ / "data" / "processed" / "exposicao_inundacao_robustez"
FICHA = c.RAIZ / "docs" / "exposicao_inundacao" / "ficha_metodos_exposicao.md"
MOTIVO = "auditoria de robustez das estimativas de exposição"
TOL = 1e-9
ARQ_PONTOS_BASE = c.CAMADAS / "enderecos-domicilios_ibge-cnefe_2022_pontos.gpkg"
ARQ_CATALOGO = c.RAIZ / "data" / "catalogo_fontes.csv"
# variáveis dos agregados por setor procuradas na Parte B: só entram as que existirem no extrato e no dicionário
VARS_BASICO = ["V0001", "V0003", "V0004", "V0005", "V0007"]
VARS_DOMICILIO = ["V00001", "V00002", "V00003", "V00005", "V00006", "V00007"]
BIBLIOTECAS = ["geopandas", "shapely", "pyproj", "pandas", "numpy", "rasterio", "rasterstats", "matplotlib", "pyogrio", "networkx", "openpyxl", "xlrd"]
TEMAS_CATALOGO = ["cnefe_2022", "agregados_setores_2022_temas", "setores_censitarios", "vulnerabilidade_censo", "setores_historico_formacao_2010_2022",
                  "grade_estatistica_2022", "uso_cobertura_solo", "cotas_inundacao", "manchas_inundacao_sgb_servicos", "setorizacao_risco_sgb",
                  "populacao_areas_de_risco_ibge", "area_diretamente_atingida_maio_2024", "areas_urbanizadas_ibge", "limite_municipal"]


# ---------------------------------------------------------------- gravação
def rel(p: Path) -> str:
    return str(Path(p).relative_to(c.RAIZ))


def em_md(t: pd.DataFrame, casas: dict | None = None) -> str:
    casas = casas or {}
    cab = "| " + " | ".join(t.columns) + " |"
    sep = "|" + "|".join("---:" if pd.api.types.is_numeric_dtype(t[col]) and not pd.api.types.is_bool_dtype(t[col]) else "---" for col in t.columns) + "|"
    lin = []
    for r in t.itertuples(index=False):
        cel = []
        for col, v in zip(t.columns, r):
            if isinstance(v, (bool, np.bool_)):
                cel.append("sim" if v else "não")
            elif isinstance(v, (int, np.integer)):
                cel.append(es.fmt(float(v), "ano" if col in casas and casas[col] == "ano" else 0))
            else:
                cel.append(es.fmt(v, casas.get(col, "auto")).replace("|", "/"))
        lin.append("| " + " | ".join(cel) + " |")
    return "\n".join([cab, sep] + lin)


def gravar(t: pd.DataFrame, nome: str, titulo: str, colunas: dict, nota: str = "", casas: dict | None = None, **kw) -> Path:
    """Tabela: CSV sem arredondar + .md para leitura + .json irmão (fonte, data, roteiro, argumentos, colunas)."""
    faltam = [x for x in t.columns if x not in colunas]
    if faltam:
        raise KeyError(f"{nome}: coluna sem descrição: {faltam}")
    arq = SAIDA / f"{nome}.csv"
    t.to_csv(arq, index=False)
    arq.with_suffix(".md").write_text(f"**{titulo}**\n\n{em_md(t, casas)}\n" + (f"\n{nota}\n" if nota else ""), encoding="utf-8")
    c.gravar_meta(arq, codigo_ibge=ARGS.codigo_ibge, crs=c.CRS_PADRAO, status=c.STATUS_CONFERENCIA, ligado_ao_portal=False, script=SCRIPT, motivo=MOTIVO,
                  fontes=es.FONTES_META, fora_do_git="data/processed/ é ignorado; não publicar", titulo=titulo, nota=nota or None,
                  formatos=["csv (valores não arredondados, ponto decimal)", "md (para leitura, arredondado a partir do CSV)"],
                  argumentos={"codigo_ibge": ARGS.codigo_ibge, "bordas_m": ARGS.bordas_m, "limiares": ARGS.limiares},
                  colunas={k: colunas[k] for k in t.columns}, **kw)
    logger.info("Tabela: %s (%d linhas)", rel(arq), len(t))
    return arq


def maiuscula(s: str) -> str:
    return s[:1].upper() + s[1:]


def pct(a, b):
    return 100 * (a / b - 1) if b else np.nan


# ---------------------------------------------------------------- entradas
def dicionario_agregados() -> dict:
    """Descrição de cada variável dos agregados de 2022, como está no dicionário que acompanha os arquivos."""
    arq = next(iter(sorted((c.CACHE_DP / "agregados_2022").glob("dicionario*.xlsx"))), None)
    if arq is None:
        return {}
    out = {}
    for _, folha in pd.read_excel(arq, sheet_name=None, header=None, dtype=str).items():
        for linha in folha.itertuples(index=False):
            cel = [str(x).strip() for x in linha if isinstance(x, str)]
            for i, x in enumerate(cel[:-1]):
                if x.upper().startswith("V") and x[1:].isdigit():
                    out.setdefault(x.upper(), {"descricao": cel[i + 1], "tema": cel[i - 1] if i else "", "dicionario": rel(arq)})
    return out


def categorias_cnefe(variavel: str) -> dict:
    """Categorias de uma variável do CNEFE (código -> rótulo), lidas do dicionário do CNEFE; vazio se não houver."""
    arq = next(iter(sorted((c.CACHE_DP / "cnefe_2022").glob("Dicionario*.xls*"))), None)
    if arq is None:
        return {}
    try:
        d = pd.read_excel(arq, header=None, dtype=str)
    except Exception as e:  # sem leitor de planilha: seguem os códigos
        logger.warning("dicionário do CNEFE ilegível (%s)", e)
        return {}
    out, dentro = {}, False
    for r in d.itertuples(index=False):
        nome = str(r[0]).strip() if isinstance(r[0], str) else ""
        if nome == variavel:
            dentro = True
        elif nome:
            dentro = False
        if dentro and isinstance(r[2], str) and "=" in r[2]:
            k, v = r[2].split("=", 1)
            out[k.strip()] = v.strip()
    return out


def agregados(tema: str, pedidas: list[str]) -> tuple[pd.DataFrame, list[str]]:
    """Variáveis pedidas que existem no extrato municipal do tema; as que faltam são devolvidas à parte."""
    tudo = c.agregados_2022(tema, ARGS.codigo_ibge)
    tem = [v for v in pedidas if v in tudo.columns]
    return tudo[["CD_SETOR"] + tem + [v + "_bruto" for v in tem]].set_index("CD_SETOR"), [v for v in pedidas if v not in tem]


# ---------------------------------------------------------------- principal
def main() -> None:
    if SAIDA.exists() and any(SAIDA.iterdir()) and not ARGS.refazer:
        raise SystemExit(f"{rel(SAIDA)} já tem arquivos: a auditoria já foi gerada (use --refazer para gerar de novo)")
    cod = ARGS.codigo_ibge
    eo.ARGS = ec.ARGS = es.ARGS = ARGS
    limite = c.carregar_area_estudo()
    st = gpd.read_file(c.CAMADAS / "populacao-setores_ibge-censo_2022_setor.gpkg")
    parte_do_setor, sede = es.classes_de_setor(st)
    d = eo.aplicar_publicado(eo.carregar_delimitacoes(cod, limite, st), ARGS.publicado)
    cont = d[d.fonte != eo.CHEIAS_NC].reset_index(drop=True)
    base = cont[(cont.fonte == eo.CHEIAS) | (cont.tipo == "total")].reset_index(drop=True)  # as mesmas delimitações da síntese
    proprias = d[d.fonte == eo.CHEIAS_NC].set_index("delimitacao").geometry  # mancha de cada cota, sozinha
    K = list(base[base.fonte == eo.CHEIAS].delimitacao)
    lin = es.linhas_da_sintese(base, ee.carregar_cotas()[0].tr_anos)

    # polígonos: os da síntese (inteiro, por parte, borda deslocada) para cada distância; manchas sozinhas; parte de uma cota menor fora de uma maior
    partes = []
    for b in ARGS.bordas_m:
        ARGS.borda_m = float(b)  # montar_poligonos lê a distância do argumento da síntese
        partes.append(es.montar_poligonos(base, st, parte_do_setor))
    pol = pd.concat(partes, ignore_index=True).drop_duplicates("cenario").reset_index(drop=True)
    extras = [{"cenario": f"sozinha|{k}", "geometry": proprias[k]} for k in K]
    pares = [(a, b) for i, a in enumerate(K) for b in K[i + 1:]]
    extras += [{"cenario": f"fora|{a}|{b}", "geometry": proprias[a].difference(proprias[b])} for a, b in pares]
    ex = gpd.GeoDataFrame(extras, crs=c.CRS_PADRAO)
    ex["valor"], ex["area_km2"] = ex.cenario, ex.geometry.area / 1e6
    todos_pol = pd.concat([pol[["cenario", "valor", "area_km2", "geometry"]], ex], ignore_index=True)
    t, pts = ec.expor(todos_pol, "robustez", gravar=False)
    col = {r.cenario: f"exp_{i + 1:02d}" for i, r in enumerate(todos_pol.itertuples())}
    C = todos_pol.drop(columns=["geometry", "valor"]).merge(
        t.rename(columns={"enderecos_total": "enderecos", "pop_estimada_setor_total": "pop_setor", "pop_estimada_grade_total": "pop_grade"})
        [["cenario", "enderecos", "pop_setor", "pop_grade"]], on="cenario").set_index("cenario")
    ch = lambda r, borda=0.0: f"{r.fonte_id}|{r.delimitacao}|{r.recorte}|{borda:+.0f}"  # noqa: E731
    dentro = {r.linha: pts[col[ch(r)]] for r in lin.itertuples()}
    pm = pol[pol.cenario.isin([ch(r) for r in lin.itertuples()])]
    ar = eo.populacao_por_area_e_uso_do_solo(pm).set_index("cenario")

    # ================= Parte A: prova de partida
    s1 = pd.read_csv(next(es.SAIDA.glob("sintese-delimitacoes-e-contagem_*_delimitacao.csv")))
    s2 = pd.read_csv(next(es.SAIDA.glob("sintese-tres-metodos_*_delimitacao.csv")))
    if list(s1.linha) != list(lin.linha) or list(s2.linha) != list(lin.linha):
        raise SystemExit(f"Parte A: as linhas não são as da síntese.\n síntese: {list(s1.linha)}\n aqui:    {list(lin.linha)}")
    prova = []
    for i, r in enumerate(lin.itertuples()):
        k = ch(r)
        for nome, tabela, esperado, obtido, inteiro in (
                ("area_km2", "sintese-delimitacoes-e-contagem", s1.area_km2[i], C.area_km2[k], False),
                ("enderecos", "sintese-delimitacoes-e-contagem", s1.enderecos[i], C.enderecos[k], True),
                ("pop_enderecos_setor", "sintese-delimitacoes-e-contagem", s1.pop_enderecos_setor[i], C.pop_setor[k], False),
                ("pop_enderecos_grade", "sintese-delimitacoes-e-contagem", s1.pop_enderecos_grade[i], C.pop_grade[k], False),
                ("pop_area_setor", "sintese-tres-metodos", s2.pop_area_setor[i], ar.pop_area_setor[k], False),
                ("pop_uso_solo", "sintese-tres-metodos", s2.pop_uso_solo[i], ar.pop_uso_solo[k], False)):
            dif = float(obtido) - float(esperado)
            prova.append({"linha": r.linha, "recorte": r.recorte, "grandeza": nome, "tabela_da_sintese": tabela, "valor_da_sintese": float(esperado),
                          "valor_recalculado": float(obtido), "diferenca": dif, "confere": bool(dif == 0 if inteiro else abs(dif) < TOL)})
    prova = pd.DataFrame(prova)
    if not prova.confere.all():
        raise SystemExit("Parte A NÃO conferiu; nada foi gravado. Diferenças:\n" + prova[~prova.confere].to_string(index=False))
    logger.info("Parte A: %d valores conferem com a síntese", len(prova))
    SAIDA.mkdir(parents=True, exist_ok=True)
    gravar(prova, "robustez-prova-de-partida_sgb-ibge-fepam-cnefe_2022_delimitacao", "Parte A — Prova de partida: valores da síntese e valores recalculados",
           {"linha": "delimitação, com o nome da síntese", "recorte": "município ou cidade", "grandeza": "o que foi comparado", "tabela_da_sintese": "tabela de onde vem o valor de partida",
            "valor_da_sintese": "valor gravado na síntese", "valor_recalculado": "valor recalculado por este roteiro", "diferenca": "recalculado − síntese",
            "confere": f"inteiros iguais; decimais com diferença abaixo de {TOL:g}"},
           casas={"valor_da_sintese": 3, "valor_recalculado": 3, "diferenca": 12}, tolerancia=TOL)
    quadro = lin[["linha", "fonte", "recorte"]].copy()
    quadro["pop_enderecos"] = [float(C.pop_setor[ch(r)]) for r in lin.itertuples()]
    quadro["enderecos"] = [int(C.enderecos[ch(r)]) for r in lin.itertuples()]

    # ================= Parte B: universo da população
    # B1 espécies
    rot_esp = categorias_cnefe("COD_ESPECIE") or eo.ESPECIES
    todos = eo.enderecos_todas_especies(cod)
    esp = eo.contar_especies(pm, todos).set_index("cenario")
    b1 = quadro[["linha", "fonte", "recorte"]].copy()
    for k_ in eo.ESPECIES:
        b1[f"especie_{k_}"] = [int(esp.loc[ch(r), f"especie_{k_}"]) for r in lin.itertuples()]
    b1["total"] = [int(esp.loc[ch(r), "enderecos_todas_especies"]) for r in lin.itertuples()]
    b1["domicilios_coletivos"] = b1["especie_2"]
    b1["especie_1_igual_a_contagem_da_sintese"] = (b1["especie_1"] == quadro.enderecos).values
    mun_esp = todos.COD_ESPECIE.value_counts()
    gravar(b1, "robustez-universo-especies_cnefe_2022_delimitacao", "Parte B1 — Endereços do CNEFE 2022 por espécie dentro de cada delimitação",
           {"linha": "delimitação, com o nome da síntese", "fonte": "fonte da delimitação", "recorte": "município ou cidade",
            **{f"especie_{k_}": f"endereços da espécie {k_} ({rot_esp.get(k_, eo.ESPECIES[k_])})" for k_ in eo.ESPECIES},
            "total": "endereços de todas as espécies", "domicilios_coletivos": "endereços da espécie 2",
            "especie_1_igual_a_contagem_da_sintese": "a contagem da espécie 1, lida do arquivo do CNEFE, repete a contagem de endereços da síntese"},
           nota="Espécies: " + "; ".join(f"{k_} = {v}" for k_, v in rot_esp.items()) + ". Cada registro do CNEFE é uma espécie existente no endereço.",
           rotulos_das_especies=rot_esp, municipio={f"especie_{k_}": int(mun_esp.get(k_, 0)) for k_ in eo.ESPECIES} | {"total": int(len(todos))})

    # B2 variáveis que existem
    dic = dicionario_agregados()
    bas, falta_b = agregados("basico", VARS_BASICO)
    dom, falta_d = agregados("caracteristicas_domicilio1", VARS_DOMICILIO)
    ag = st.set_index("CD_SETOR")[["SITUACAO", "pop"]].join(bas).join(dom)
    b2 = pd.DataFrame([{"variavel": v, "tema": tema, "descricao_no_dicionario": dic.get(v, {}).get("descricao", "não está no dicionário"),
                        "existe_no_extrato": v in ag.columns,
                        "setores_com_valor": int(ag[v].notna().sum()) if v in ag else 0,
                        "setores_sob_sigilo_ou_vazios": int(ag[v].isna().sum()) if v in ag else 0,
                        "soma_no_municipio": float(ag[v].sum()) if v in ag and v != "V0005" else np.nan}
                       for tema, lista in (("basico", VARS_BASICO), ("caracteristicas_domicilio1", VARS_DOMICILIO)) for v in lista])
    gravar(b2, "apoio-variaveis-dos-agregados_ibge_2022_variavel", "Parte B2 — Variáveis dos agregados por setor de 2022 que separam domicílio particular e coletivo",
           {"variavel": "nome da variável no arquivo", "tema": "arquivo de agregados (tema)", "descricao_no_dicionario": "descrição, como está no dicionário que acompanha os agregados",
            "existe_no_extrato": "a variável está no extrato municipal do repositório", "setores_com_valor": "setores com valor numérico",
            "setores_sob_sigilo_ou_vazios": "setores com 'X' (sigilo) ou vazio", "soma_no_municipio": "soma nos setores com valor (não se aplica à média)"},
           casas={"soma_no_municipio": 0}, dicionario=next((v["dicionario"] for v in dic.values()), "dicionário ausente"), variaveis_ausentes=falta_b + falta_d,
           siglas="DPPO = domicílio particular permanente ocupado; DPIO = improvisado ocupado; DPPV = permanente vago; DPPUO = permanente de uso ocasional; "
                  "DCCM/DCSM = domicílio coletivo com/sem morador (folha de siglas do dicionário)")

    # B3 e B4: por setor e por delimitação
    n_set = pts.groupby("setor_2022").size()
    ag["enderecos_cnefe_especie_1"] = n_set.reindex(ag.index).fillna(0).astype(int)
    tem_part = {"V00005", "V00006"} <= set(ag.columns)
    tem_media = {"V0005", "V0007"} <= set(ag.columns)
    ag["pop_dom_particular"], ag["origem_pop_dom_particular"] = np.nan, "sem variável"
    if tem_part:
        v = ag.V00005 + ag.V00006
        ag.loc[v.notna(), "pop_dom_particular"] = v[v.notna()]
        ag.loc[v.notna(), "origem_pop_dom_particular"] = "V00005 + V00006"
    if tem_media:
        m = ag.pop_dom_particular.isna() & (ag.V0005 * ag.V0007).notna()
        ag.loc[m, "pop_dom_particular"] = (ag.V0005 * ag.V0007)[m]
        ag.loc[m, "origem_pop_dom_particular"] = "V0005 × V0007 (média arredondada pela fonte)"
    m = ag.pop_dom_particular.isna()
    ag.loc[m, "pop_dom_particular"] = ag.loc[m, "pop"]
    ag.loc[m, "origem_pop_dom_particular"] = "população total do setor (sem variável que separe)"
    pts["pop_est_particular"] = pts.setor_2022.map(ag.pop_dom_particular / n_set)
    b3, por_setor = [], []
    for r in lin.itertuples():
        e = pts[dentro[r.linha]]
        tocados = ag.loc[sorted(e.setor_2022.dropna().unique())]
        nd = e.groupby("setor_2022").size()
        linha = {"linha": r.linha, "fonte": r.fonte, "recorte": r.recorte, "setores_com_endereco_dentro": len(tocados),
                 "enderecos_cnefe_especie_1_nos_setores": int(tocados.enderecos_cnefe_especie_1.sum())}
        for v, nome in (("V0003", "dom_particulares_V0003"), ("V0007", "dom_particulares_ocupados_V0007"), ("V00001", "dom_part_permanentes_ocupados_V00001")):
            linha[nome] = float(tocados[v].sum()) if v in tocados else np.nan
        linha["setores_sem_V00001"] = int(tocados.V00001.isna().sum()) if "V00001" in tocados else len(tocados)
        linha["razao_enderecos_sobre_ocupados_V0007"] = linha["enderecos_cnefe_especie_1_nos_setores"] / linha["dom_particulares_ocupados_V0007"] if linha["dom_particulares_ocupados_V0007"] else np.nan
        linha["razao_enderecos_sobre_particulares_V0003"] = linha["enderecos_cnefe_especie_1_nos_setores"] / linha["dom_particulares_V0003"] if linha["dom_particulares_V0003"] else np.nan
        linha["pop_enderecos_pop_total"] = float(e.pop_est_setor.sum())
        linha["pop_enderecos_pop_dom_particular"] = float(e.pop_est_particular.sum())
        linha["diferenca_absoluta"] = linha["pop_enderecos_pop_dom_particular"] - linha["pop_enderecos_pop_total"]
        linha["diferenca_relativa_pct"] = pct(linha["pop_enderecos_pop_dom_particular"], linha["pop_enderecos_pop_total"])
        linha["setores_com_pop_particular_por_outra_via"] = int((tocados.origem_pop_dom_particular != "V00005 + V00006").sum())
        b3.append(linha)
        x = tocados.assign(linha=r.linha, enderecos_dentro=nd.reindex(tocados.index).fillna(0).astype(int).values).reset_index()
        por_setor.append(x)
    b3 = pd.DataFrame(b3)
    desc_var = lambda v: f"{v}: {dic.get(v, {}).get('descricao', 'sem descrição no dicionário')}"  # noqa: E731
    fech = {"pop_total_municipio_V0001": float(ag["pop"].sum()), "pop_dom_particular_municipio": float(ag.pop_dom_particular.sum()),
            "origem_por_setor": ag.origem_pop_dom_particular.value_counts().to_dict(),
            "moradores_em_domicilio_coletivo_V00007": float(ag.V00007.sum()) if "V00007" in ag else None,
            "setores_sem_V00007": int(ag.V00007.isna().sum()) if "V00007" in ag else None}
    gravar(b3, "robustez-universo_cnefe-ibge_2022_delimitacao", "Partes B3 e B4 — Domicílios ocupados, endereços do CNEFE e estimativa com a população em domicílio particular",
           {"linha": "delimitação, com o nome da síntese", "fonte": "fonte da delimitação", "recorte": "município ou cidade",
            "setores_com_endereco_dentro": "setores de 2022 com pelo menos um endereço de domicílio particular dentro da delimitação (os que entram na estimativa)",
            "enderecos_cnefe_especie_1_nos_setores": "endereços de domicílio particular do CNEFE em todo o setor, somados nesses setores (o denominador N_s)",
            "dom_particulares_V0003": desc_var("V0003"), "dom_particulares_ocupados_V0007": desc_var("V0007"), "dom_part_permanentes_ocupados_V00001": desc_var("V00001") + " (soma só dos setores com valor)",
            "setores_sem_V00001": "setores desses em que V00001 está sob sigilo", "razao_enderecos_sobre_ocupados_V0007": "endereços do CNEFE / domicílios particulares ocupados (V0007)",
            "razao_enderecos_sobre_particulares_V0003": "endereços do CNEFE / domicílios particulares (V0003)",
            "pop_enderecos_pop_total": "estimativa por endereços com a população total do setor (a da síntese)",
            "pop_enderecos_pop_dom_particular": "a mesma estimativa com a população em domicílio particular do setor no lugar da população total",
            "diferenca_absoluta": "pop. em domicílio particular − pop. total, em pessoas", "diferenca_relativa_pct": "a mesma diferença, em % da estimativa com a população total",
            "setores_com_pop_particular_por_outra_via": "setores desses em que a população em domicílio particular não veio de V00005 + V00006"},
           nota="População em domicílio particular do setor: V00005 + V00006 (moradores em domicílios particulares permanentes e improvisados ocupados); onde há sigilo, "
                "V0005 × V0007 (média de moradores × domicílios particulares ocupados); onde nenhuma existe, a população total. O CNEFE conta também domicílios vagos e de uso ocasional.",
           casas={"razao_enderecos_sobre_ocupados_V0007": 3, "razao_enderecos_sobre_particulares_V0003": 3, "pop_enderecos_pop_total": 3, "pop_enderecos_pop_dom_particular": 3,
                  "diferenca_absoluta": 3, "diferenca_relativa_pct": 3}, fechamento_no_municipio=fech)
    ps = pd.concat(por_setor, ignore_index=True)
    cols_ps = ["linha", "CD_SETOR", "SITUACAO", "pop", "pop_dom_particular", "origem_pop_dom_particular", "enderecos_cnefe_especie_1", "enderecos_dentro"] + [v for v in VARS_BASICO[1:] + VARS_DOMICILIO if v in ps]
    ps = ps[cols_ps]
    ps["razao_enderecos_sobre_ocupados_V0007"] = ps.enderecos_cnefe_especie_1 / ps.V0007.replace(0, np.nan) if "V0007" in ps else np.nan
    gravar(ps, "apoio-universo-por-setor_cnefe-ibge_2022_setor", "Parte B3 (apoio) — Setores com endereço dentro de cada delimitação",
           {"linha": "delimitação, com o nome da síntese", "CD_SETOR": "código do setor censitário de 2022", "SITUACAO": "situação do setor", "pop": "V0001, população total do setor",
            "pop_dom_particular": "população em domicílio particular usada na Parte B4", "origem_pop_dom_particular": "de onde veio esse valor",
            "enderecos_cnefe_especie_1": "endereços de domicílio particular do CNEFE no setor", "enderecos_dentro": "desses, os que estão dentro da delimitação",
            **{v: desc_var(v) for v in VARS_BASICO[1:] + VARS_DOMICILIO}, "razao_enderecos_sobre_ocupados_V0007": "endereços do CNEFE / V0007"},
           casas={"CD_SETOR": None, "razao_enderecos_sobre_ocupados_V0007": 3})

    # B5: setores com população e sem endereço de domicílio particular
    pop_mun = float(st["pop"].sum())
    sem = ag[(ag["pop"] > 0) & (ag.enderecos_cnefe_especie_1 == 0)].copy()
    geo = st.set_index("CD_SETOR").geometry
    sem["enderecos_cnefe_todas_especies_no_poligono"] = [int(todos.within(geo[s_]).sum()) for s_ in sem.index]
    sem["enderecos_de_domicilio_coletivo_no_poligono"] = [int((todos.within(geo[s_]) & (todos.COD_ESPECIE == "2")).sum()) for s_ in sem.index]
    b5 = sem.reset_index()[["CD_SETOR", "SITUACAO", "pop"] + [v for v in ("V0003", "V0004", "V0007", "V00007") if v in sem] +
                           ["enderecos_cnefe_todas_especies_no_poligono", "enderecos_de_domicilio_coletivo_no_poligono"]]
    alocada = float(pts.pop_est_setor.sum())
    fech5 = {"populacao_do_municipio_soma_dos_setores": pop_mun, "populacao_alocada_aos_enderecos": alocada, "diferenca": pop_mun - alocada,
             "setores_com_populacao_e_sem_endereco": len(sem), "populacao_nesses_setores": float(sem["pop"].sum()),
             "confere": bool(abs((pop_mun - alocada) - float(sem["pop"].sum())) < 1e-6)}
    gravar(b5, "apoio-setores-sem-endereco_ibge-cnefe_2022_setor", "Parte B5 — Setores com população e sem endereço de domicílio particular no CNEFE",
           {"CD_SETOR": "código do setor censitário de 2022", "SITUACAO": "situação do setor", "pop": "V0001, população total do setor", **{v: desc_var(v) for v in ("V0003", "V0004", "V0007", "V00007")},
            "enderecos_cnefe_todas_especies_no_poligono": "endereços do CNEFE, de qualquer espécie, com o ponto dentro do polígono do setor",
            "enderecos_de_domicilio_coletivo_no_poligono": "desses, os de domicílio coletivo (espécie 2)"},
           casas={"CD_SETOR": None}, fechamento=fech5,
           nota="A população desses setores não é alocada a endereço nenhum: a soma das estimativas por endereço fica abaixo da população do município exatamente nesse valor.")

    # ================= Parte C: limiar de área urbanizada
    fonte_vi = Path(inspect.getsourcefile(vi))
    linhas_vi = fonte_vi.read_text(encoding="utf-8").splitlines()
    onde = lambda trecho: next(i + 1 for i, x in enumerate(linhas_vi) if trecho in x)  # noqa: E731
    meta_raster = json.loads(vi.CAMINHO_RASTER_USO_SOLO.with_suffix(".json").read_text(encoding="utf-8")) if vi.CAMINHO_RASTER_USO_SOLO.with_suffix(".json").exists() else {}
    regra = {"arquivo": rel(fonte_vi), "constante": "LIMIAR_PCT_AREA_URBANIZADA_SETOR", "valor": vi.LIMIAR_PCT_AREA_URBANIZADA_SETOR,
             "linha_da_constante": onde("LIMIAR_PCT_AREA_URBANIZADA_SETOR = "), "funcao_que_aplica": "calcular_exposicao_por_setor",
             "linha_da_regra": onde('intersecao["pct_area_urbanizada_setor"] >= limiar_pct_area_urbanizada'),
             "o_que_faz": "em cada setor, pct_area_urbanizada_setor = área dos pixels de área urbanizada dentro do setor / área do setor; se for menor que o limiar, "
                          "a população do método por uso do solo nesse setor passa a ser a do método por área (população × fração da área do setor dentro do polígono)",
             "classe": vi.CLASSE_AREA_URBANIZADA_MAPBIOMAS, "raster": rel(vi.CAMINHO_RASTER_USO_SOLO), "pixel_area_m2": vi.obter_pixel_area_m2(vi.CAMINHO_RASTER_USO_SOLO),
             "metadado_do_raster": {k: meta_raster.get(k, "não registrado") for k in ("fonte", "colecao", "ano", "resolucao_espacial_nativa", "crs_processado")},
             "limiar_zero": "com limiar 0 todo setor usa o uso do solo; em setor sem pixel de área urbanizada a fração fica indefinida (0/0) e o código a troca por 0 "
                            "(np.where(area_urbanizada_setor > 0, …, nan) seguido de fillna(0)): o setor contribui com 0 pessoas, sem erro de divisão"}
    s_urb = vi.adicionar_area_urbanizada_setor(vi.carregar_setores_com_indicadores())
    g_vi = gpd.GeoDataFrame({"cota_cm": pm.cenario.values, "tr_anos": np.nan}, geometry=pm.geometry.values, crs=c.CRS_PADRAO)
    c3, c2, it_padrao = [], [], None
    for limiar in ARGS.limiares:
        it = vi.calcular_exposicao_por_setor(s_urb, g_vi, limiar_pct_area_urbanizada=limiar)
        if np.isclose(limiar, vi.LIMIAR_PCT_AREA_URBANIZADA_SETOR):
            it_padrao = it
        for r in lin.itertuples():
            x = it[it.cota_cm == ch(r)]
            fb = x[x.metodo_estimativa_uso_solo == "area_proporcional_fallback"]
            pe = float(C.pop_setor[ch(r)])
            c3.append({"linha": r.linha, "fonte": r.fonte, "recorte": r.recorte, "limiar_pct": 100 * limiar, "pop_uso_solo": float(x["populacao_estimada_ponderada_uso-solo"].sum()),
                       "pop_enderecos": pe, "razao_uso_solo_sobre_enderecos": float(x["populacao_estimada_ponderada_uso-solo"].sum()) / pe if pe else np.nan,
                       "setores_tocados": int(x.CD_SETOR.nunique()), "setores_na_recaida": int(fb.CD_SETOR.nunique()),
                       "pop_vinda_da_recaida": float(fb["populacao_estimada_ponderada_uso-solo"].sum())})
            if np.isclose(limiar, vi.LIMIAR_PCT_AREA_URBANIZADA_SETOR):
                c2.append({"linha": r.linha, "fonte": r.fonte, "recorte": r.recorte, "setores_tocados": int(x.CD_SETOR.nunique()), "setores_na_recaida": int(fb.CD_SETOR.nunique()),
                           "pop_uso_solo": float(x["populacao_estimada_ponderada_uso-solo"].sum()), "pop_vinda_da_recaida": float(fb["populacao_estimada_ponderada_uso-solo"].sum()),
                           "pct_da_estimativa_vinda_da_recaida": 100 * float(fb["populacao_estimada_ponderada_uso-solo"].sum()) / float(x["populacao_estimada_ponderada_uso-solo"].sum()),
                           "pop_total_dos_setores_na_recaida": float(fb.populacao_total.sum()), "setores_sem_area_urbanizada": int((x.area_urbanizada_setor_km2 == 0).sum())})
    c3, c2 = pd.DataFrame(c3), pd.DataFrame(c2)
    if it_padrao is None:
        raise SystemExit("o limiar padrão não está em --limiares: a linha de conferência com a síntese não pode ser feita")
    p5 = c3[np.isclose(c3.limiar_pct, 100 * vi.LIMIAR_PCT_AREA_URBANIZADA_SETOR)].reset_index(drop=True)
    dif5 = float((p5.pop_uso_solo - s2.pop_uso_solo).abs().max())
    if dif5 >= TOL:
        raise SystemExit(f"Parte C: a linha do limiar padrão não repete a síntese (maior diferença {dif5})")
    col_c = {"linha": "delimitação, com o nome da síntese", "fonte": "fonte da delimitação", "recorte": "município ou cidade",
             "limiar_pct": "limiar de área urbanizada do setor (% da área do setor) abaixo do qual o setor recai no método por área",
             "pop_uso_solo": "população pelo método por uso do solo", "pop_enderecos": "estimativa por endereços (população por setor)",
             "razao_uso_solo_sobre_enderecos": "método por uso do solo / estimativa por endereços", "setores_tocados": "setores com interseção com o polígono",
             "setores_na_recaida": "setores tocados com área urbanizada abaixo do limiar (método por área)", "pop_vinda_da_recaida": "pessoas que o método por uso do solo atribui a partir desses setores",
             "pct_da_estimativa_vinda_da_recaida": "essas pessoas, em % da estimativa por uso do solo", "pop_total_dos_setores_na_recaida": "população total desses setores",
             "setores_sem_area_urbanizada": "setores tocados sem nenhum pixel de área urbanizada"}
    gravar(c3, "robustez-limiar-uso-do-solo_mapbiomas-ibge_2022_delimitacao", "Parte C3 — Método por uso do solo com limiares de área urbanizada diferentes", col_c,
           casas={"limiar_pct": 0, "pop_uso_solo": 1, "pop_enderecos": 1, "pop_vinda_da_recaida": 1}, regra_no_codigo=regra, maior_diferenca_do_limiar_padrao_para_a_sintese=dif5,
           nota=f"A linha de {100 * vi.LIMIAR_PCT_AREA_URBANIZADA_SETOR:g} % repete a síntese (maior diferença: {dif5:g}). {maiuscula(regra['limiar_zero'])}.")
    gravar(c2, "apoio-recaida-no-metodo-por-area_mapbiomas-ibge_2022_delimitacao", f"Parte C2 — Setores que recaem no método por área (limiar de {100 * vi.LIMIAR_PCT_AREA_URBANIZADA_SETOR:g} %)", col_c,
           casas={"pop_uso_solo": 1, "pop_vinda_da_recaida": 1}, regra_no_codigo=regra)

    # ================= Parte D: borda
    d1 = []
    for r in lin.itertuples():
        m = C.loc[ch(r)]
        for b in ARGS.bordas_m:
            rec, av = C.loc[ch(r, -b)], C.loc[ch(r, b)]
            d1.append({"linha": r.linha, "fonte": r.fonte, "recorte": r.recorte, "distancia_m": float(b), "enderecos": int(m.enderecos), "pop_setor": float(m.pop_setor),
                       "enderecos_borda_recuada": int(rec.enderecos), "pop_setor_borda_recuada": float(rec.pop_setor), "enderecos_borda_avancada": int(av.enderecos), "pop_setor_borda_avancada": float(av.pop_setor),
                       "var_pct_enderecos_recuada": pct(rec.enderecos, m.enderecos), "var_pct_enderecos_avancada": pct(av.enderecos, m.enderecos),
                       "var_pct_pop_recuada": pct(rec.pop_setor, m.pop_setor), "var_pct_pop_avancada": pct(av.pop_setor, m.pop_setor)})
    d1 = pd.DataFrame(d1)
    s3 = next(iter(es.SAIDA.glob("apoio-sensibilidade-a-posicao-dos-pontos_*_delimitacao.csv")), None)
    conf12 = "a síntese não tem tabela de borda"
    if s3 is not None:
        s3t, b_s = pd.read_csv(s3), json.loads(s3.with_suffix(".json").read_text(encoding="utf-8")).get("borda_m")
        x = d1[d1.distancia_m == b_s].reset_index(drop=True)
        if len(x) == len(s3t):
            difs = {k: float((x[k] - s3t[k]).abs().max()) for k in ("enderecos_borda_recuada", "pop_setor_borda_recuada", "enderecos_borda_avancada", "pop_setor_borda_avancada")}
            if max(difs.values()) >= TOL:
                raise SystemExit(f"Parte D: as linhas de {b_s:g} m não repetem a síntese: {difs}")
            conf12 = f"linhas de {b_s:g} m iguais às da síntese (maior diferença: {max(difs.values()):g})"
        else:
            conf12 = f"a distância da síntese ({b_s} m) não está em --bordas-m"
    gravar(d1, "robustez-borda_sgb-ibge-fepam-cnefe_2022_delimitacao", "Parte D1 — Endereços e pessoas com a borda de cada polígono recuada e avançada",
           {"linha": "delimitação, com o nome da síntese", "fonte": "fonte da delimitação", "recorte": "município ou cidade", "distancia_m": f"deslocamento da borda, em metros ({c.CRS_PADRAO})",
            "enderecos": "endereços dentro do polígono original", "pop_setor": "pessoas (população por setor) no polígono original",
            "enderecos_borda_recuada": "endereços com a borda recuada (buffer negativo)", "pop_setor_borda_recuada": "pessoas com a borda recuada",
            "enderecos_borda_avancada": "endereços com a borda avançada (buffer positivo)", "pop_setor_borda_avancada": "pessoas com a borda avançada",
            "var_pct_enderecos_recuada": "variação % dos endereços, borda recuada", "var_pct_enderecos_avancada": "variação % dos endereços, borda avançada",
            "var_pct_pop_recuada": "variação % das pessoas, borda recuada", "var_pct_pop_avancada": "variação % das pessoas, borda avançada"},
           casas={"distancia_m": 0, "pop_setor": 1, "pop_setor_borda_recuada": 1, "pop_setor_borda_avancada": 1, "var_pct_enderecos_recuada": 1, "var_pct_enderecos_avancada": 1,
                  "var_pct_pop_recuada": 1, "var_pct_pop_avancada": 1}, conferencia_com_a_sintese=conf12,
           metodo="função montar_poligonos da síntese: buffer negativo e positivo no polígono da delimitação; na linha da cidade, o deslocamento é da borda da delimitação e o corte pelos setores vem depois",
           nota=f"Conferência: {conf12}.")

    # D2: nível de geocodificação
    nivel = gpd.read_file(ec.ARQ_PONTOS, columns=["COD_UNICO_ENDERECO", "NV_GEO_COORD"], ignore_geometry=True)
    if not (nivel.COD_UNICO_ENDERECO.values == pts.COD_UNICO_ENDERECO.values).all():
        raise ValueError("a camada de pontos mudou de ordem entre as leituras")
    pts["nivel"] = nivel.NV_GEO_COORD.astype(str).values
    rot_niv = categorias_cnefe("NV_GEO_COORD")
    codigos = sorted(rot_niv) if rot_niv else sorted(pts.nivel.unique())
    preciso = pts.nivel.isin(es.NIVEIS_PRECISOS)
    bp = 12.0 if 12.0 in [float(x) for x in ARGS.bordas_m] else float(ARGS.bordas_m[0])
    d2 = []
    for r in lin.itertuples():
        e = pts[dentro[r.linha]]
        n = e.nivel.value_counts()
        base_p = int((dentro[r.linha] & preciso).sum())
        rec_p, av_p = int((pts[col[ch(r, -bp)]] & preciso).sum()), int((pts[col[ch(r, bp)]] & preciso).sum())
        d2.append({"linha": r.linha, "fonte": r.fonte, "recorte": r.recorte, **{f"nivel_{k_}": int(n.get(k_, 0)) for k_ in codigos}, "total": len(e),
                   "niveis_1_e_2": base_p, "pct_niveis_1_e_2": 100 * base_p / len(e) if len(e) else np.nan,
                   "niveis_1_e_2_borda_recuada": rec_p, "niveis_1_e_2_borda_avancada": av_p,
                   "var_pct_niveis_1_e_2_recuada": pct(rec_p, base_p), "var_pct_niveis_1_e_2_avancada": pct(av_p, base_p),
                   "var_pct_todos_recuada": pct(C.enderecos[ch(r, -bp)], C.enderecos[ch(r)]), "var_pct_todos_avancada": pct(C.enderecos[ch(r, bp)], C.enderecos[ch(r)])})
    d2 = pd.DataFrame(d2)
    gravar(d2, "robustez-nivel-geocodificacao_cnefe_2022_delimitacao", "Parte D2 — Endereços por nível de geocodificação do CNEFE e variação da borda só com os níveis 1 e 2",
           {"linha": "delimitação, com o nome da síntese", "fonte": "fonte da delimitação", "recorte": "município ou cidade",
            **{f"nivel_{k_}": f"endereços de domicílio particular com nível {k_}" + (f" ({rot_niv[k_]})" if k_ in rot_niv else " (rótulo ausente do repositório)") for k_ in codigos},
            "total": "endereços de domicílio particular dentro da delimitação", "niveis_1_e_2": "endereços com nível 1 ou 2", "pct_niveis_1_e_2": "em % do total",
            "niveis_1_e_2_borda_recuada": f"endereços de nível 1 ou 2 com a borda recuada {bp:g} m", "niveis_1_e_2_borda_avancada": f"endereços de nível 1 ou 2 com a borda avançada {bp:g} m",
            "var_pct_niveis_1_e_2_recuada": "variação %, só níveis 1 e 2, borda recuada", "var_pct_niveis_1_e_2_avancada": "variação %, só níveis 1 e 2, borda avançada",
            "var_pct_todos_recuada": "variação % com todos os endereços, borda recuada (Parte D1)", "var_pct_todos_avancada": "variação % com todos os endereços, borda avançada (Parte D1)"},
           casas={"pct_niveis_1_e_2": 1, "var_pct_niveis_1_e_2_recuada": 1, "var_pct_niveis_1_e_2_avancada": 1, "var_pct_todos_recuada": 1, "var_pct_todos_avancada": 1},
           rotulos_dos_niveis=rot_niv or "rótulos ausentes do repositório: usados os códigos", distancia_m=bp,
           nota="Níveis: " + ("; ".join(f"{k_} = {v}" for k_, v in rot_niv.items()) if rot_niv else "rótulos ausentes do repositório") + ".")

    # D3: quanto do recuo vem do trecho cortado pelo limite municipal (delimitação recortada)
    d3 = []
    for f in [x for x in eo.FONTES if x.get("recortar")]:
        r = next(x for x in lin.itertuples() if x.fonte_id == f["id"] and x.recorte == es.MUNICIPIO)
        P = base.loc[(base.fonte == f["id"]) & (base.tipo == "total"), "geometry"].iloc[0]
        lim_borda = limite.union_all().boundary.buffer(0.5)  # 0,5 m: tolerância numérica para achar a borda que coincide com o limite
        cortado, propria = P.boundary.intersection(lim_borda), P.boundary.difference(lim_borda)
        bruto = gpd.read_file(f["arquivo"]).to_crs(c.CRS_PADRAO)
        inteiro = bruto.geometry.buffer(0).union_all()  # a fonte inteira (município e vizinhos), sem o corte
        for b in ARGS.bordas_m:
            perdidos = pts[dentro[r.linha] & ~pts[col[ch(r, -b)]]]
            perto = perdidos.geometry.distance(propria) <= b + 1e-6
            alt = inteiro.buffer(-b).intersection(P)
            so_alt = pts[dentro[r.linha] & ~pts.within(alt)]
            d3.append({"linha": r.linha, "distancia_m": float(b), "borda_total_km": P.boundary.length / 1e3, "borda_no_limite_municipal_km": cortado.length / 1e3,
                       "enderecos": int(dentro[r.linha].sum()), "enderecos_perdidos_no_recuo": len(perdidos), "pop_perdida_no_recuo": float(perdidos.pop_est_setor.sum()),
                       "perdidos_pela_borda_propria": int(perto.sum()), "pop_perdida_pela_borda_propria": float(perdidos.pop_est_setor[perto].sum()),
                       "perdidos_so_pelo_trecho_cortado": int((~perto).sum()), "pop_perdida_so_pelo_trecho_cortado": float(perdidos.pop_est_setor[~perto].sum()),
                       "pct_do_efeito_vindo_do_trecho_cortado": 100 * int((~perto).sum()) / len(perdidos) if len(perdidos) else np.nan,
                       "conferencia_perdidos_recuando_a_fonte_sem_corte": len(so_alt)})
    d3 = pd.DataFrame(d3)
    gravar(d3, "apoio-borda-no-corte-municipal_fepam-cnefe_2022_distancia", "Parte D3 — Efeito do recuo da borda na área atingida: borda própria × trecho cortado pelo limite municipal",
           {"linha": "delimitação, com o nome da síntese", "distancia_m": "recuo da borda, em metros", "borda_total_km": "comprimento da borda do polígono cortado",
            "borda_no_limite_municipal_km": "parte da borda que coincide com o limite municipal (trecho criado pelo corte)", "enderecos": "endereços no polígono original",
            "enderecos_perdidos_no_recuo": "endereços que saem com a borda recuada", "pop_perdida_no_recuo": "pessoas (por setor) nesses endereços",
            "perdidos_pela_borda_propria": "dos que saem, os que estão a até a distância do recuo da borda própria da área atingida", "pop_perdida_pela_borda_propria": "pessoas nesses endereços",
            "perdidos_so_pelo_trecho_cortado": "dos que saem, os que só estão perto do trecho cortado pelo limite municipal", "pop_perdida_so_pelo_trecho_cortado": "pessoas nesses endereços",
            "pct_do_efeito_vindo_do_trecho_cortado": "parcela dos endereços perdidos que vem só do trecho cortado",
            "conferencia_perdidos_recuando_a_fonte_sem_corte": "conferência por outro caminho: endereços que saem quando se recua a feição inteira da fonte (município e vizinhos, sem o corte) e só depois se corta"},
           casas={"distancia_m": 0, "pop_perdida_no_recuo": 1, "pop_perdida_pela_borda_propria": 1, "pop_perdida_so_pelo_trecho_cortado": 1, "pct_do_efeito_vindo_do_trecho_cortado": 1},
           metodo="borda própria = borda do polígono menos a faixa de 0,5 m em torno do limite municipal; endereço perdido é da borda própria quando sua distância a ela é menor ou igual ao recuo")

    # ================= Parte E: união cumulativa
    e1 = []
    for k in K:
        soz, cum = C.loc[f"sozinha|{k}"], C.loc[f"{eo.CHEIAS}|{k}|{es.MUNICIPIO}|+0"]
        e1.append({"linha": f"cheia de {k} cm", "cota_cm": int(k), "area_km2_mancha_sozinha": float(soz.area_km2), "area_km2_cumulativa": float(cum.area_km2),
                   "enderecos_mancha_sozinha": int(soz.enderecos), "enderecos_cumulativa": int(cum.enderecos), "pop_mancha_sozinha": float(soz.pop_setor), "pop_cumulativa": float(cum.pop_setor),
                   "dif_area_km2": float(cum.area_km2 - soz.area_km2), "dif_enderecos": int(cum.enderecos - soz.enderecos), "dif_pop": float(cum.pop_setor - soz.pop_setor)})
    e1 = pd.DataFrame(e1)
    e2 = pd.DataFrame([{"cota_menor_cm": int(a), "cota_maior_cm": int(b), "area_km2_da_menor_fora_da_maior": float(C.area_km2[f"fora|{a}|{b}"]),
                        "pct_da_area_da_menor": 100 * float(C.area_km2[f"fora|{a}|{b}"]) / float(C.area_km2[f"sozinha|{a}"]),
                        "enderecos_na_parte_de_fora": int(C.enderecos[f"fora|{a}|{b}"]), "pop_na_parte_de_fora": float(C.pop_setor[f"fora|{a}|{b}"])} for a, b in pares])
    como_une = {"funcao": "carregar_cenarios", "arquivo": ec.SCRIPT, "chamada_por": f"carregar_delimitacoes, em {eo.SCRIPT}",
                "o_que_faz": "corrige a geometria com buffer(0), dissolve as feições pela cota e, em ordem crescente de cota, acumula a união: a geometria da cota X é a união das manchas de cota menor ou igual a X; "
                             "a mancha da cota sozinha fica na coluna geom_propria"}
    gravar(e1, "robustez-uniao-cumulativa_sgb-cnefe_2022_cota", "Parte E — Mancha de cada cota sozinha e mancha cumulativa usada na síntese",
           {"linha": "delimitação, com o nome da síntese", "cota_cm": "cota na régua, em cm", "area_km2_mancha_sozinha": "área da mancha original da cota", "area_km2_cumulativa": "área da união com as manchas de cota menor",
            "enderecos_mancha_sozinha": "endereços na mancha original", "enderecos_cumulativa": "endereços na mancha cumulativa (síntese)", "pop_mancha_sozinha": "pessoas na mancha original",
            "pop_cumulativa": "pessoas na mancha cumulativa (síntese)", "dif_area_km2": "cumulativa − sozinha, área", "dif_enderecos": "cumulativa − sozinha, endereços", "dif_pop": "cumulativa − sozinha, pessoas"},
           casas={"cota_cm": "ano", "area_km2_mancha_sozinha": 3, "area_km2_cumulativa": 3, "dif_area_km2": 3, "pop_mancha_sozinha": 1, "pop_cumulativa": 1, "dif_pop": 1}, como_o_codigo_monta_a_uniao=como_une)
    gravar(e2, "apoio-manchas-nao-aninhadas_sgb-cnefe_2022_par", "Parte E (apoio) — Parte da mancha de cota menor que fica fora da mancha de cota maior",
           {"cota_menor_cm": "cota da mancha menor", "cota_maior_cm": "cota da mancha maior", "area_km2_da_menor_fora_da_maior": "área da mancha menor que não está dentro da maior (as duas sozinhas)",
            "pct_da_area_da_menor": "em % da área da mancha menor", "enderecos_na_parte_de_fora": "endereços nessa parte", "pop_na_parte_de_fora": "pessoas nessa parte"},
           casas={"cota_menor_cm": "ano", "cota_maior_cm": "ano", "area_km2_da_menor_fora_da_maior": 3, "pop_na_parte_de_fora": 1})

    # ================= Parte F: diferenças absolutas
    f1 = quadro[["linha", "fonte", "recorte"]].copy()
    f1["pop_enderecos"] = quadro.pop_enderecos
    f1["pop_area_setor"] = [float(ar.pop_area_setor[ch(r)]) for r in lin.itertuples()]
    f1["pop_uso_solo"] = [float(ar.pop_uso_solo[ch(r)]) for r in lin.itertuples()]
    f1["dif_area_menos_enderecos"] = f1.pop_area_setor - f1.pop_enderecos
    f1["dif_uso_solo_menos_enderecos"] = f1.pop_uso_solo - f1.pop_enderecos
    f1["razao_area_sobre_enderecos"] = f1.pop_area_setor / f1.pop_enderecos
    f1["razao_uso_solo_sobre_enderecos"] = f1.pop_uso_solo / f1.pop_enderecos
    r0 = next(lin.itertuples())  # a menor cheia: onde a razão é maior
    x = it_padrao[it_padrao.cota_cm == ch(r0)]
    nd0 = pts[dentro[r0.linha]].groupby("setor_2022").size()
    f2 = pd.DataFrame({"CD_SETOR": x.CD_SETOR.values, "SITUACAO": x.SITUACAO.values, "pop_do_setor": x.populacao_total.values, "pct_da_area_do_setor_na_mancha": 100 * x.pct_area_coberta.values,
                       "pop_metodo_area": x["populacao_estimada_area-proporcional"].values, "pop_metodo_uso_solo": x["populacao_estimada_ponderada_uso-solo"].values,
                       "metodo_no_uso_solo": x.metodo_estimativa_uso_solo.values})
    f2["enderecos_do_setor"] = f2.CD_SETOR.map(n_set).fillna(0).astype(int)
    f2["enderecos_dentro_da_mancha"] = f2.CD_SETOR.map(nd0).fillna(0).astype(int)
    f2 = f2.sort_values("pop_metodo_area", ascending=False).reset_index(drop=True)
    top = f2.head(5)
    nota_f = (f"Razão de {es.fmt(float(f1.razao_area_sobre_enderecos[0]), 0)} na {r0.linha}: o método por área atribui {es.fmt(float(f1.pop_area_setor[0]), 0)} pessoas e a estimativa por endereços, "
              f"{es.fmt(float(f1.pop_enderecos[0]), 3)} ({int(quadro.enderecos[0])} endereços). A mancha toca {len(f2)} setores; em {int((f2.enderecos_dentro_da_mancha == 0).sum())} deles não há nenhum endereço dentro dela, "
              f"e esses setores somam {es.fmt(float(f2.pop_metodo_area[f2.enderecos_dentro_da_mancha == 0].sum()), 0)} das pessoas do método por área. "
              f"Os cinco setores de maior contribuição somam {es.fmt(float(top.pop_metodo_area.sum()), 0)} pessoas (endereços dentro da mancha nesses cinco: {int(top.enderecos_dentro_da_mancha.sum())}): "
              f"o método reparte a população pela área do setor, e a área desses setores dentro da mancha é, na maior parte, sem endereço. "
              f"O denominador é pequeno ({es.fmt(float(f1.pop_enderecos[0]), 3)} pessoas), o que amplia a razão; a diferença absoluta é de {es.fmt(float(f1.dif_area_menos_enderecos[0]), 0)} pessoas.")
    gravar(f1, "robustez-diferencas-absolutas_sgb-ibge-fepam-cnefe_2022_delimitacao", "Parte F — Três métodos: diferenças absolutas e razões",
           {"linha": "delimitação, com o nome da síntese", "fonte": "fonte da delimitação", "recorte": "município ou cidade", "pop_enderecos": "estimativa por endereços, sem arredondar (no .md, três casas)",
            "pop_area_setor": "método por área do setor", "pop_uso_solo": "método por uso do solo", "dif_area_menos_enderecos": "método por área − endereços, em pessoas",
            "dif_uso_solo_menos_enderecos": "método por uso do solo − endereços, em pessoas", "razao_area_sobre_enderecos": "método por área / endereços, com a estimativa sem arredondar",
            "razao_uso_solo_sobre_enderecos": "método por uso do solo / endereços, com a estimativa sem arredondar"},
           casas={"pop_enderecos": 3, "pop_area_setor": 1, "pop_uso_solo": 1, "dif_area_menos_enderecos": 1, "dif_uso_solo_menos_enderecos": 1}, nota=nota_f)
    gravar(f2, "apoio-area-do-setor-na-menor-cheia_sgb-ibge-cnefe_2022_setor", f"Parte F (apoio) — Setores tocados pela {r0.linha}: método por área × endereços",
           {"CD_SETOR": "código do setor censitário de 2022", "SITUACAO": "situação do setor", "pop_do_setor": "população total do setor", "pct_da_area_do_setor_na_mancha": "% da área do setor dentro da mancha",
            "pop_metodo_area": "pessoas atribuídas pelo método por área", "pop_metodo_uso_solo": "pessoas atribuídas pelo método por uso do solo", "metodo_no_uso_solo": "uso do solo ou recaída no método por área",
            "enderecos_do_setor": "endereços de domicílio particular do setor", "enderecos_dentro_da_mancha": "desses, os que estão dentro da mancha"},
           casas={"CD_SETOR": None, "pop_metodo_area": 1, "pop_metodo_uso_solo": 1}, nota=nota_f)

    # ================= Parte G: ficha de métodos
    base_pts = gpd.read_file(ARQ_PONTOS_BASE, columns=["origem_setor_2022"]).to_crs(c.CRS_PADRAO)
    chave = pd.Series(list(zip(np.round(base_pts.geometry.x, 2), np.round(base_pts.geometry.y, 2))))
    repetidos = int((chave.map(chave.value_counts()) > 1).sum())
    cnefe_cols = pd.read_csv(next((c.CACHE_DP / "cnefe_2022").glob(f"{cod}_*.zip")), sep=";", dtype=str, usecols=["LATITUDE", "LONGITUDE", "COD_ESPECIE"])
    sem_coord = cnefe_cols.LATITUDE.fillna("").str.strip().eq("") | cnefe_cols.LONGITUDE.fillna("").str.strip().eq("")
    gr = gpd.read_file(c.CAMADAS / "populacao-grade_ibge-censo_2022_200m-1km.gpkg", ignore_geometry=True)
    fatos = {"enderecos_no_arquivo_do_cnefe": int(len(cnefe_cols)), "enderecos_sem_coordenada": int(sem_coord.sum()),
             "enderecos_de_domicilio_particular": int(len(base_pts)), "origem_do_setor_do_endereco": base_pts.origem_setor_2022.value_counts().to_dict(),
             "enderecos_em_coordenada_repetida": repetidos, "celulas_da_grade_por_resolucao": gr.resolucao.value_counts().to_dict(),
             "enderecos_fora_de_celula_da_grade": "não medido aqui; está no .json da tabela enderecos-populacao-por-cota do estudo por endereços (fechamento_grade)",
             "populacao_do_municipio": pop_mun, "populacao_alocada_aos_enderecos": alocada, "regra_do_limiar": regra, "uniao_cumulativa": como_une}
    versoes = {"python": platform.python_version()}
    for b_ in BIBLIOTECAS:
        try:
            versoes[b_] = metadata.version(b_)
        except metadata.PackageNotFoundError:
            versoes[b_] = "não instalada"
    cat = pd.read_csv(ARQ_CATALOGO, dtype=str).fillna("")
    cat = cat[cat.tema.isin(TEMAS_CATALOGO)].set_index("tema").reindex([x for x in TEMAS_CATALOGO if x in set(cat.tema)])
    bases = [{"tema": k_, "fonte": r_.fonte, "resolucao_espacial": r_.resolucao_espacial or "não registrado", "resolucao_temporal": r_.resolucao_temporal or "não registrado",
              "data_de_acesso": r_.data_acesso or "não registrado"} for k_, r_ in cat.iterrows()]
    metadados = {}
    for nome_, arq_ in (("CNEFE 2022 (arquivo do município)", next((c.CACHE_DP / "cnefe_2022").glob(f"{cod}_*.zip.json"), None)),
                        ("agregados por setor 2022, básico", c.CACHE_DP / "agregados_2022" / (c.ARQ_AGREGADOS_2022["basico"] + ".json")),
                        ("manchas por cota (SGB)", ee.ARQ_COTAS.with_suffix(".json")), ("uso do solo (MapBiomas)", vi.CAMINHO_RASTER_USO_SOLO.with_suffix(".json")),
                        *((f["nome"], f["arquivo"].with_suffix(".json")) for f in eo.FONTES),
                        ("setores 2022 com população", (c.CAMADAS / "populacao-setores_ibge-censo_2022_setor.json")), ("grade estatística 2022", (c.CAMADAS / "populacao-grade_ibge-censo_2022_200m-1km.json"))):
        if arq_ is not None and Path(arq_).exists():
            m_ = json.loads(Path(arq_).read_text(encoding="utf-8"))
            metadados[nome_] = {"metadado": rel(arq_), **{k_: m_[k_] if m_[k_] is not None else "não registrado"
                                                          for k_ in ("data_acesso", "data_download", "data_processamento", "last_modified_origem", "colecao", "ano", "versao", "url") if k_ in m_}}
        else:
            metadados[nome_] = {"metadado": "não registrado"}
    escrever_ficha(fatos, versoes, bases, metadados, rot_niv)

    if ARGS.copia:  # cópia das tabelas (.csv e .md) para conferência
        ARGS.copia.mkdir(parents=True, exist_ok=True)
        n = 0
        for arq in sorted(SAIDA.iterdir()):
            if arq.suffix in (".csv", ".md"):
                shutil.copy2(arq, ARGS.copia / arq.name)
                n += 1
        logger.info("Cópia para conferência: %d arquivos", n)
    print(json.dumps({"parte_a": f"{len(prova)} valores conferem", "b5": fech5, "b4_fechamento": fech, "c_limiar_padrao_x_sintese": dif5, "d_borda": conf12, "fatos": fatos},
                     ensure_ascii=False, indent=1, default=str))


def escrever_ficha(fatos: dict, versoes: dict, bases: list, metadados: dict, rot_niv: dict) -> None:
    """Ficha de métodos: o que o código faz em cada estimador, com arquivo e função; versões lidas do ambiente."""
    FICHA.parent.mkdir(parents=True, exist_ok=True)
    reg, n = fatos["regra_do_limiar"], es.fmt
    org = fatos["origem_do_setor_do_endereco"]
    t = f"""# Ficha de métodos — exposição à inundação por endereços

Documento gerado por `{SCRIPT}` ({MOTIVO}). Descreve o que o código faz; cada item cita o arquivo e a função. Município: código IBGE {ARGS.codigo_ibge}. CRS de trabalho: {c.CRS_PADRAO}.

## 1. Estimadores

### 1.1 Estimativa por endereços

    E(H) = Σ_s P_s · n_s(H) / N_s

- **P_s**: população total do setor censitário s de 2022 (variável V0001 dos agregados por setor, coluna `pop` da camada de setores; `dinamica_populacional_2022.py`, função `setores_2022`).
- **N_s**: número de endereços de domicílio particular (CNEFE 2022, espécie 1) atribuídos ao setor s (`exposicao_inundacao_enderecos.py`, função `main`: `pts.groupby("setor_2022").size()`).
- **n_s(H)**: desses endereços, os que têm o ponto dentro do polígono H (`exposicao_inundacao_cenarios.py`, função `expor`).
- No código, cada endereço recebe `pop_est_setor = P_s / N_s` e a estimativa é a soma nos endereços dentro de H.
- **De onde vem o setor do endereço**: do código de setor que o CNEFE traz (os 15 primeiros dígitos de `COD_SETOR`, setor preliminar), ligado ao setor de divulgação pelo histórico de formação dos setores do IBGE (`dinamica_populacional_2022.py`, função `cnefe_2022`). Só quando o setor preliminar foi dividido em mais de um setor de divulgação é que entra a posição do ponto: fica a parte que contém o ponto ou, se nenhuma contém, a mais próxima. Contagem no município: {"; ".join(f"{k} = {n(float(v), 0)}" for k, v in org.items())}.
- A soma das estimativas por endereço é {n(fatos["populacao_alocada_aos_enderecos"], 0)}; a população do município é {n(fatos["populacao_do_municipio"], 0)}. A diferença é a população dos setores sem endereço de domicílio particular.

### 1.2 Método por área do setor

    A(H) = Σ_s P_s · área(s ∩ H) / área(s)

- `vulnerabilidade_inundacao.py`, função `calcular_exposicao_por_setor` (interseção setor × polígono; a fração é limitada a 1), chamada por `exposicao_inundacao_estimativas_oficiais.py`, função `populacao_por_area_e_uso_do_solo`. Área do setor calculada da geometria, no CRS de trabalho.

### 1.3 Método por uso do solo

    U(H) = Σ_s P_s · urb(s ∩ H) / urb(s)      se urb(s) / área(s) ≥ limiar
           Σ_s P_s · área(s ∩ H) / área(s)    caso contrário (recaída no método por área)

- **urb(·)**: área dos pixels da classe {reg["classe"]} (área urbanizada) do raster `{reg["raster"]}` dentro da geometria, contados por estatística zonal (`calcular_area_urbanizada_km2`); pixel de {n(reg["pixel_area_m2"], 1)} m².
- **Limiar**: constante `{reg["constante"]}` = {reg["valor"]} ({100 * reg["valor"]:g} % da área do setor), `{reg["arquivo"]}`, linha {reg["linha_da_constante"]}; aplicada na função `{reg["funcao_que_aplica"]}`, linha {reg["linha_da_regra"]}. Setor com área urbanizada abaixo do limiar é tratado como rural disperso e usa o método por área.
- Setor acima do limiar e sem pixel urbanizado dentro de H contribui com zero. {maiuscula(reg["limiar_zero"])}.
- Raster, segundo o metadado: {"; ".join(f"{k}: {v}" for k, v in reg["metadado_do_raster"].items())}.

### 1.4 Versão pela grade estatística

    G(H) = Σ_c P_c · n_c(H) / N_c

- Células da grade estatística do Censo 2022: 200 m na área urbana e 1 km na rural ({"; ".join(f"{k}: {n(float(v), 0)} células" for k, v in fatos["celulas_da_grade_por_resolucao"].items())}); `dinamica_populacional_2022.py`, função `grade_2022`.
- **P_c** é a população da célula (campo TOTAL), repartida igualmente entre os endereços de domicílio particular dentro da célula (**N_c**).
- O endereço é associado à célula por junção espacial (ponto dentro da célula; `exposicao_inundacao_enderecos.py`, função `main`); endereço fora de toda célula fica com zero.

### 1.5 Manchas cumulativas

- {maiuscula(fatos["uniao_cumulativa"]["o_que_faz"])} (`{fatos["uniao_cumulativa"]["arquivo"]}`, função `{fatos["uniao_cumulativa"]["funcao"]}`).

## 2. Contagem espacial

- **Predicado**: `within` (ponto dentro do polígono), em `exposicao_inundacao_cenarios.py`, função `expor`. Um ponto exatamente sobre a borda **não** é contado: `within` exige que o ponto esteja no interior.
- **Coordenada repetida**: todos os endereços são contados, um por registro. No município, {n(float(fatos["enderecos_em_coordenada_repetida"]), 0)} dos {n(float(fatos["enderecos_de_domicilio_particular"]), 0)} endereços de domicílio particular dividem a coordenada (arredondada a 1 cm) com outro; o estudo por endereços só os conta à parte, não os funde.
- **Endereço sem coordenada**: {n(float(fatos["enderecos_sem_coordenada"]), 0)} dos {n(float(fatos["enderecos_no_arquivo_do_cnefe"]), 0)} registros do arquivo do CNEFE do município. O código não tem tratamento próprio para esse caso (converte latitude e longitude diretamente em ponto).
- **Nível de geocodificação** (variável NV_GEO_COORD): {"; ".join(f"{k} = {v}" for k, v in rot_niv.items()) if rot_niv else "rótulos ausentes do repositório"}. "Coordenada precisa" nas tabelas = níveis 1 e 2.

## 3. Versões

### 3.1 Ambiente

| Componente | Versão |
|---|---|
""" + "\n".join(f"| {k} | {v} |" for k, v in versoes.items()) + """

### 3.2 Bases, como estão em `data/catalogo_fontes.csv`

| Tema | Fonte | Resolução espacial | Referência temporal | Data de acesso |
|---|---|---|---|---|
""" + "\n".join(f"| {b['tema']} | {b['fonte']} | {b['resolucao_espacial']} | {b['resolucao_temporal']} | {b['data_de_acesso']} |".replace("\n", " ") for b in bases) + """

### 3.3 Bases, como estão nos metadados dos arquivos

| Base | Metadado | O que registra |
|---|---|---|
""" + "\n".join(f"| {k} | {v['metadado']} | " + ("; ".join(f"{a}: {b}" for a, b in v.items() if a != "metadado") or "não registrado") + " |" for k, v in metadados.items()) + "\n\nO que não aparece acima não está registrado no repositório.\n"
    FICHA.write_text(t, encoding="utf-8")
    c.gravar_meta(FICHA, codigo_ibge=ARGS.codigo_ibge, crs=c.CRS_PADRAO, status=c.STATUS_CONFERENCIA, script=SCRIPT, motivo=MOTIVO,
                  descricao="ficha de métodos dos estimadores de exposição: fórmulas, predicado espacial e versões", fatos=fatos, versoes=versoes,
                  bases_no_catalogo=bases, bases_nos_metadados=metadados)
    logger.info("Ficha de métodos: %s", rel(FICHA))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    warnings.simplefilter("ignore", pd.errors.PerformanceWarning)  # a função de contagem acrescenta uma coluna por polígono
    _p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    _p.add_argument("--codigo-ibge", default=None, help="padrão: o código da área de estudo (config/area_estudo.geojson)")
    _p.add_argument("--publicado", type=Path, nargs="*", help="CSV com números publicados (padrão: as transcrições versionadas)")
    _p.add_argument("--bordas-m", type=float, nargs="+", default=[6.0, 12.0, 24.0], help="deslocamentos da borda dos polígonos (m)")
    _p.add_argument("--limiares", type=float, nargs="+", default=[0.0, 0.02, 0.05, 0.10, 0.20], help="limiares de área urbanizada do setor (fração)")
    _p.add_argument("--copia", type=Path, help="pasta que recebe uma cópia das tabelas (.csv e .md), para conferência")
    _p.add_argument("--refazer", action="store_true", help="gera de novo mesmo se a pasta de saída já tiver arquivos")
    ARGS = _p.parse_args()
    ARGS.codigo_ibge = ARGS.codigo_ibge or eo.codigo_da_area_de_estudo()
    ARGS.nivel_rio = None
    if ARGS.publicado is None:
        ARGS.publicado = sorted(eo.PUBLICADOS.glob(eo.PADRAO_PUBLICADOS))
    main()
