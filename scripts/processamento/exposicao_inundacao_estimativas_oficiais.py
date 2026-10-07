"""
Endereços e população de 2022 DENTRO das delimitações oficiais de área sujeita
a inundação, ao lado do número que cada fonte publica e de três métodos de
estimativa sobre os MESMOS polígonos.

Medida de referência: a contagem por endereços do estudo atual
(exposicao_inundacao_enderecos.py), sem mudar nada nele:
  B1  endereço de domicílio particular (CNEFE 2022) dentro do polígono;
  B2  população do setor 2022 repartida igualmente entre os endereços do
      setor; sensibilidade pela grade estatística.
Os valores por endereço são lidos da camada de pontos que o estudo grava; a
contagem dentro de cada polígono é feita por exposicao_inundacao_cenarios.expor.
Os métodos por área do setor e por uso do solo vêm das funções de
vulnerabilidade_inundacao.py, aplicadas aos mesmos polígonos.

Delimitações (todas já no repositório; nada é baixado aqui), filtradas pelo
código do município lido de config/area_estudo.geojson:
  - cheias por cota (SGB), CUMULATIVAS: união das manchas de cota <= X;
  - setorização de risco (SGB): uma linha por setor de risco e o total;
  - áreas de risco (IBGE, 2018, com dados do Censo 2010): uma linha por área
    e o total;
  - área diretamente atingida em maio de 2024 (FEPAM): a feição do município,
    cortada pelo limite municipal, no total e separada em urbana e rural pela
    situação do setor censitário de 2022.
"Total" de uma fonte = união dos seus polígonos (o que se sobrepõe conta uma vez).

Número publicado que não é atributo da geometria (lido em publicação) vem dos
CSV numeros-publicados_*.csv de docs/exposicao_inundacao/numeros_publicados/
(ou dos dados em --publicado), com as colunas: fonte, delimitacao, edificacoes, domicilios,
pessoas, o_que_conta, documento, pagina.

Efeito do ano e efeito do setor inteiro (rodada 34): quando o número de uma
fonte é a soma de setores censitários INTEIROS de 2010, a contagem de 2022 é
repetida dentro da união desses setores. A diferença para o número publicado,
sobre o mesmo polígono, é o efeito do ano; a diferença entre essa contagem e a
das áreas inteiras é a parte das áreas que o número publicado não cobre.

--nivel-rio recebe a série do nível do rio já baixada (scripts/download/
nivel_rio_ana.py) e põe a cota máxima do período na linha da área atingida.

Fração de área urbanizada (rodada 35): com as áreas urbanizadas do IBGE (2019)
já baixadas (scripts/download/areas_urbanizadas_ibge.py), a fração da ÁREA
urbanizada dentro da área atingida é posta ao lado da fração dos ENDEREÇOS da
área urbanizada que estão dentro dela; a diferença entre as duas é o erro de
supor a população distribuída por igual na área urbanizada.

Área atingida e maior mancha (rodada 35): os endereços que só uma das duas
cobre são descritos por bairro, distância à borda da outra e proximidade de
curso d'água que não é o rio principal; figura com o que só uma cobre.

Saídas, todas em data/processed/exposicao_inundacao_oficiais/ (fora do git),
cada uma com o .json irmão; os valores ficam sem arredondar.

Uso:
  python scripts/processamento/exposicao_inundacao_estimativas_oficiais.py [--publicado ARQUIVO.csv ...] [--nivel-rio SERIE.csv]
"""

from __future__ import annotations

import argparse
import logging
import re
import zipfile
from pathlib import Path

import geopandas as gpd
import matplotlib

matplotlib.use("Agg")
import matplotlib.patheffects as pe  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

import dinamica_populacional_comum as c  # noqa: E402
import exposicao_inundacao_cenarios as ec  # noqa: E402
import exposicao_inundacao_enderecos as ee  # noqa: E402
import layout_mapa as lm  # noqa: E402
import vulnerabilidade_inundacao as vi  # noqa: E402
from dinamica_populacional_cnefe_mapas import Fundo  # noqa: E402
from dinamica_populacional_mapas import INK, Base  # noqa: E402
from recorte_municipio import CAMINHO_AREA_ESTUDO_PADRAO  # noqa: E402

logger = logging.getLogger(__name__)

SCRIPT = "scripts/processamento/exposicao_inundacao_estimativas_oficiais.py"
SAIDA = c.RAIZ / "data" / "processed" / "exposicao_inundacao_oficiais"
CONFERENCIA = c.RAIZ / "data" / "processed" / "conferencia_fontes_inundacao"
ARQ_SETORES_2010 = c.RAW / "vetor" / "setores-censitarios_ibge_2010_vetorial.gpkg"
ARQ_POP_MUNICIPIO = c.RAIZ / "data" / "processed" / "populacao-serie-temporal_ibge-sidra_1970-2025_municipal.csv"
PUBLICADOS = c.RAIZ / "docs" / "exposicao_inundacao" / "numeros_publicados"  # transcrições versionadas (entrada padrão)
PADRAO_PUBLICADOS = "numeros-publicados_*.csv"  # a pasta guarda também transcrições de outro assunto, com outras colunas
ARQ_URBANIZADAS = c.RAW / "vetor" / "areas-urbanizadas_ibge_2019_recorte-municipio.gpkg"
ARQ_SITUACAO = c.RAW / "situacao-domicilio_ibge-sidra-tabela9923_2022_municipal.csv"  # população urbana do município, Censo 2022
CLASSES_URBANIZADAS = ("Tipo", "Densidade")  # atributos de classe da camada de áreas urbanizadas
FAIXAS_DISTANCIA_M = (50, 200, 500)
DISTANCIA_CURSO_DAGUA_M = 100
COR_SO_AREA, COR_SO_MANCHA, COR_AMBAS = "#e66101", "#5e3c99", "#c9c8c0"  # laranja × roxo: distinguem-se também para daltônicos
ARQ_BAIRROS = c.RAIZ / "data" / "processed" / "bairros" / "bairros_ibge_2022_vetorial.gpkg"
CAMADA_COTAS, ATRIBUTO_COTAS = "cotas_inundacao", "cota_cm"
CHEIAS, CHEIAS_NC = "cheias-sgb", "cheias-sgb-nao-cumulativo"

# campo_municipio: atributo com o código do município; atributo: identificador de cada polígono;
# campos: atributos da própria geometria com o número que a fonte publica
FONTES = (
    {"id": "setorizacao-sgb", "nome": "SGB — setorização de risco", "ano": 2014, "prefixo": "S", "nome_parte": "setor de risco",
     "arquivo": c.RAW / "vetor" / "setorizacao-risco_sgb_atual_vetorial.geojson", "campo_municipio": "cd_geocmu", "atributo": "num_setor",
     "campos": {"edificacoes": "num_edif", "domicilios": "num_domi", "pessoas": "num_pess"},
     "o_que_conta": "pessoas nos setores de risco, estimativa da fonte no levantamento de campo (atributo num_pess)"},
    {"id": "areas-de-risco-ibge", "nome": "IBGE — áreas de risco (2018)", "ano": 2010, "prefixo": "A", "nome_parte": "área de risco",
     "arquivo": CONFERENCIA / "populacao-areas-de-risco_ibge_2018_recorte-municipio.gpkg", "campo_municipio": "GEO_MUN", "atributo": "GEO_BATER",
     "campos": {}, "o_que_conta": ""},
    {"id": "area-atingida-fepam", "nome": "FEPAM — área diretamente atingida (maio de 2024)", "ano": 2024, "prefixo": "", "nome_parte": "",
     "arquivo": CONFERENCIA / "area-diretamente-atingida_fepam_2024-05_recorte-municipio-e-vizinhos.gpkg", "campo_municipio": "cd_mun", "atributo": "cd_mun",
     "campos": {}, "o_que_conta": "", "recortar": True, "por_situacao": True},
)
# pares (fonte A, fonte B) para o exame de coincidência das geometrias; fonte cujas áreas podem ser setores censitários inteiros
FONTE_COM_COTA = "area-atingida-fepam"  # delimitação de um evento datado: recebe a cota máxima de --nivel-rio
PARES_SOBREPOSICAO = (("areas-de-risco-ibge", "setorizacao-sgb"),)
FONTE_SETOR_INTEIRO = "areas-de-risco-ibge"
LIMIAR_SETOR_INTEIRO = 0.99  # fração da área do setor de 2010 dentro da área de risco para contá-lo como inteiro
ESPECIES = {"1": "domicílio particular", "2": "domicílio coletivo", "3": "estabelecimento agropecuário", "4": "estabelecimento de ensino",
            "5": "estabelecimento de saúde", "6": "estabelecimento de outras finalidades", "7": "edificação em construção ou reforma",
            "8": "estabelecimento religioso"}
# figura: contorno de cada fonte (cor + traço diferentes: lê-se também em tons de cinza)
ESTILO = {"setorizacao-sgb": ("#d95f02", "-", 1.5), "areas-de-risco-ibge": ("#1b7837", "--", 1.3), "area-atingida-fepam": ("#c51b7d", "-.", 1.2)}
COR_MANCHA = "#54278f"
ESCALA_POS = (0.74, 0.14)  # barra de escala à direita, em área livre: o canto esquerdo é cruzado pelos contornos
FONTES_META = ["SGB — manchas de inundação por cota e setorização de risco", "IBGE — população em áreas de risco (2018), base territorial com dados do Censo 2010",
               "FEPAM — área diretamente atingida, maio de 2024", "IBGE — CNEFE do Censo 2022; agregados e malha de setores de 2022 e de 2010; grade estatística 2022",
               "MapBiomas — uso e cobertura do solo (método por uso do solo)"]


def codigo_da_area_de_estudo() -> str:
    """Código IBGE do município, lido da área de estudo (não fica fixo no código)."""
    a = gpd.read_file(CAMINHO_AREA_ESTUDO_PADRAO, ignore_geometry=True)
    return str(a["codarea"].iloc[0])


def meta(caminho: Path, **kw) -> None:
    c.gravar_meta(caminho, codigo_ibge=ARGS.codigo_ibge, crs=c.CRS_PADRAO, status=c.STATUS_CONFERENCIA, ligado_ao_portal=False, script=SCRIPT,
                  fontes=FONTES_META, fora_do_git="data/processed/ é ignorado; não publicar",
                  anos_de_referencia="as fontes têm anos diferentes (2010, 2014, 2024); a contagem de endereços e a população são de 2022", **kw)


def gravar(t: pd.DataFrame, nome: str, descricao: str, **kw) -> Path:
    arq = SAIDA / nome
    t.to_csv(arq, index=False)
    meta(arq, descricao=descricao, **kw)
    logger.info("Tabela: %s (%d linhas)", arq.relative_to(c.RAIZ), len(t))
    return arq


def rotulo_curto(prefixo: str, valor) -> str:
    """Rótulo de mapa a partir do último grupo de dígitos do identificador (ex.: prefixo S e '..._04_...' -> S4)."""
    grupos = re.findall(r"\d+", str(valor))
    return f"{prefixo}{int(grupos[-1][-4:])}" if grupos else f"{prefixo}{valor}"


# ---------------------------------------------------------------- delimitações
def carregar_delimitacoes(cod: str, limite, setores_2022: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Uma linha por polígono a contar: cheias cumulativas, partes e total de cada fonte oficial."""
    linhas = []
    cheias = ec.carregar_cenarios(ee.ARQ_COTAS, CAMADA_COTAS, ATRIBUTO_COTAS)  # geometry = união das cotas <= X
    for r in cheias.itertuples():
        linhas.append({"fonte": CHEIAS, "delimitacao": str(r.valor), "tipo": "cheia", "ano_fonte": np.nan, "geometry": r.geometry})
        linhas.append({"fonte": CHEIAS_NC, "delimitacao": str(r.valor), "tipo": "cheia", "ano_fonte": np.nan, "geometry": r.geom_propria})
    lim = limite.union_all()
    for f in FONTES:
        if not f["arquivo"].exists():
            raise FileNotFoundError(f"{f['arquivo']} ausente")
        g = gpd.read_file(f["arquivo"]).to_crs(c.CRS_PADRAO)  # a fonte vem em coordenadas geográficas; contagem e área no CRS do projeto
        g = g[g[f["campo_municipio"]].astype(str).str.startswith(cod)].copy()
        if g.empty:
            raise ValueError(f"{f['id']}: nenhuma feição do município {cod}")
        g["geometry"] = g.geometry.buffer(0)
        if f.get("recortar"):
            g["geometry"] = g.geometry.intersection(lim)  # só a parte dentro do limite municipal
        g = g.sort_values(f["atributo"])
        attrs = {k: pd.to_numeric(g[v], errors="coerce") for k, v in f["campos"].items()}
        if len(g) > 1:
            for i, r in enumerate(g.itertuples()):
                linhas.append({"fonte": f["id"], "delimitacao": str(getattr(r, f["atributo"])), "tipo": "parte", "ano_fonte": f["ano"], "geometry": r.geometry,
                               "rotulo": rotulo_curto(f["prefixo"], getattr(r, f["atributo"])), "local_na_fonte": getattr(r, "local", None),
                               "origem_na_fonte": getattr(r, "ORIGEM", None), **{f"fonte_{k}": float(v.iloc[i]) for k, v in attrs.items()}})
        total = g.geometry.union_all()
        linhas.append({"fonte": f["id"], "delimitacao": "total", "tipo": "total", "ano_fonte": f["ano"], "geometry": total,
                       **{f"fonte_{k}": float(v.sum()) for k, v in attrs.items()}})
        if f.get("por_situacao"):
            for sit in sorted(setores_2022.SITUACAO.dropna().unique()):
                parte = total.intersection(setores_2022[setores_2022.SITUACAO == sit].union_all())
                linhas.append({"fonte": f["id"], "delimitacao": f"situação {sit.lower()}", "tipo": "situacao", "ano_fonte": f["ano"], "geometry": parte})
    d = gpd.GeoDataFrame(linhas, crs=c.CRS_PADRAO)
    d["cenario"] = d.fonte + "|" + d.delimitacao
    d["valor"] = d.cenario
    d["area_km2"] = d.geometry.area / 1e6
    return d


def aplicar_publicado(d: gpd.GeoDataFrame, caminho: list[Path] | None) -> gpd.GeoDataFrame:
    """Número publicado: o atributo da geometria, quando existe; senão, o que foi lido na publicação (--publicado)."""
    d = d.copy()
    for col in ("fonte_edificacoes", "fonte_domicilios", "fonte_pessoas"):
        if col not in d:
            d[col] = np.nan
    d["fonte_o_que_conta"] = d.fonte.map({f["id"]: f["o_que_conta"] for f in FONTES}).fillna("")
    d["fonte_documento"], d["fonte_pagina"] = "atributo da geometria", ""
    d.loc[d[["fonte_edificacoes", "fonte_domicilios", "fonte_pessoas"]].isna().all(axis=1), "fonte_documento"] = ""
    for arq in (caminho or []):
        p = pd.read_csv(arq, dtype=str).fillna("")
        for r in p.itertuples():
            m = (d.fonte == r.fonte) & (d.delimitacao == r.delimitacao)
            if not m.any():
                raise ValueError(f"--publicado: linha sem delimitação correspondente: {r.fonte}|{r.delimitacao}")
            for col in ("edificacoes", "domicilios", "pessoas"):
                if getattr(r, col):
                    d.loc[m, f"fonte_{col}"] = float(getattr(r, col))
            d.loc[m, ["fonte_o_que_conta", "fonte_documento", "fonte_pagina"]] = [r.o_que_conta, r.documento, r.pagina]
    d["fonte_pessoas_por_edificacao"] = d.fonte_pessoas / d.fonte_edificacoes
    d["fonte_pessoas_por_domicilio"] = d.fonte_pessoas / d.fonte_domicilios
    return d


# ---------------------------------------------------------------- contagens
def enderecos_todas_especies(cod: str) -> gpd.GeoDataFrame:
    """Todos os endereços do CNEFE 2022 (todas as espécies), só coordenada e espécie; nada é gravado."""
    z = next((c.CACHE_DP / "cnefe_2022").glob(f"{cod}_*.zip"))
    with zipfile.ZipFile(z) as zf:
        df = pd.read_csv(zf.open([m for m in zf.namelist() if m.lower().endswith(".csv")][0]), sep=";", dtype=str,
                         usecols=["LATITUDE", "LONGITUDE", "COD_ESPECIE"])
    return gpd.GeoDataFrame(df[["COD_ESPECIE"]], geometry=gpd.points_from_xy(df.LONGITUDE.astype(float), df.LATITUDE.astype(float)),
                            crs="EPSG:4674").to_crs(c.CRS_PADRAO)


def contar_especies(d: gpd.GeoDataFrame, todos: gpd.GeoDataFrame) -> pd.DataFrame:
    linhas = []
    for r in d.itertuples():
        n = todos[todos.within(r.geometry)].COD_ESPECIE.value_counts()
        linhas.append({"cenario": r.cenario, "enderecos_todas_especies": int(n.sum()), **{f"especie_{k}": int(n.get(k, 0)) for k in ESPECIES}})
    return pd.DataFrame(linhas)


def populacao_por_area_e_uso_do_solo(d: gpd.GeoDataFrame) -> pd.DataFrame:
    """Métodos (1) área do setor e (2) uso do solo, pelas funções do cálculo anterior, sobre os polígonos dados."""
    s = vi.adicionar_area_urbanizada_setor(vi.carregar_setores_com_indicadores())
    g = gpd.GeoDataFrame({"cota_cm": d.cenario.values, "tr_anos": np.nan}, geometry=d.geometry.values, crs=c.CRS_PADRAO)
    it = vi.calcular_exposicao_por_setor(s, g)
    t = it.groupby("cota_cm").agg(pop_area_setor=("populacao_estimada_area-proporcional", "sum"), pop_uso_solo=("populacao_estimada_ponderada_uso-solo", "sum"),
                                  setores_tocados=("CD_SETOR", "nunique"))
    return t.reindex(d.cenario).fillna(0).rename_axis("cenario").reset_index()


def sobreposicao(d: gpd.GeoDataFrame, fa: str, fb: str) -> pd.DataFrame:
    """Área em comum de cada par de polígonos de duas fontes, sobre a área de cada um."""
    a, b = d[(d.fonte == fa) & (d.tipo == "parte")], d[(d.fonte == fb) & (d.tipo == "parte")]
    linhas = []
    for x in a.itertuples():
        for y in b.itertuples():
            i = x.geometry.intersection(y.geometry).area / 1e6
            linhas.append({"fonte_a": fa, "delimitacao_a": x.delimitacao, "area_a_km2": x.area_km2, "fonte_b": fb, "delimitacao_b": y.delimitacao,
                           "area_b_km2": y.area_km2, "area_comum_km2": i, "pct_da_area_a": 100 * i / x.area_km2, "pct_da_area_b": 100 * i / y.area_km2})
    return pd.DataFrame(linhas)


def ler_setores_2010(cod: str) -> gpd.GeoDataFrame | None:
    """Malha de setores de 2010 com domicílios (V001) e moradores (V002) da planilha Básico; None se a malha faltar."""
    if not ARQ_SETORES_2010.exists():
        return None
    s = gpd.read_file(ARQ_SETORES_2010).to_crs(c.CRS_PADRAO)
    try:  # Básico 2010: V001 = domicílios particulares permanentes (ocupados); V002 = moradores em domicílios particulares permanentes
        s = s.merge(c.agregados_2010("Basico", cod, ["V001", "V002"]), left_on="cd_setor", right_on="CD_SETOR", how="left")
    except Exception as e:  # sem a planilha, seguem só os atributos da malha
        logger.warning("agregados de 2010 indisponíveis (%s); usando só os atributos da malha de 2010", e)
        s["V001"], s["V002"] = np.nan, np.nan
    return s


def unioes_de_setores_inteiros_2010(d: gpd.GeoDataFrame, cod: str) -> gpd.GeoDataFrame | None:
    """União dos setores de 2010 inteiros de cada área (e de todas), com os domicílios e moradores de 2010, para contar 2022 dentro deles."""
    s = ler_setores_2010(cod)
    if s is None:
        return None
    linhas, todos = [], []
    for r in d[(d.fonte == FONTE_SETOR_INTEIRO) & (d.tipo == "parte")].itertuples():
        inteiros = s[s.geometry.intersection(r.geometry).area / s.geometry.area >= LIMIAR_SETOR_INTEIRO]
        if len(inteiros):
            todos.append(inteiros)
            linhas.append({"delimitacao": r.delimitacao, "setores_2010": len(inteiros), "domicilios_2010": float(inteiros.V001.sum()),
                           "moradores_2010": float(inteiros.V002.sum()), "geometry": inteiros.geometry.union_all()})
    if not linhas:
        return None
    t = pd.concat(todos).drop_duplicates("cd_setor")
    linhas.append({"delimitacao": "total", "setores_2010": len(t), "domicilios_2010": float(t.V001.sum()), "moradores_2010": float(t.V002.sum()),
                   "geometry": t.geometry.union_all()})
    u = gpd.GeoDataFrame(linhas, crs=c.CRS_PADRAO)
    u["fonte"] = FONTE_SETOR_INTEIRO
    u["cenario"] = u["valor"] = "setores-2010-inteiros|" + u.delimitacao
    u["area_km2"] = u.geometry.area / 1e6
    return u


def variacao_do_municipio() -> dict | None:
    """População do município nos Censos de 2010 e 2022, da série que o repositório já tem (contexto do efeito do ano)."""
    if not ARQ_POP_MUNICIPIO.exists():
        return None
    p = pd.read_csv(ARQ_POP_MUNICIPIO)
    p = p[p.fonte == "censo"].set_index("ano").populacao_total
    if not {2010, 2022} <= set(p.index):
        return None
    return {"populacao_2010": float(p[2010]), "populacao_2022": float(p[2022]), "razao_2022_sobre_2010": float(p[2022] / p[2010])}


def cota_maxima(caminho: Path | None) -> dict:
    """Maior cota da série dada e a primeira data e hora em que ela foi lida."""
    if not caminho:
        return {}
    t = pd.read_csv(caminho).dropna(subset=["nivel_cm"])
    i = t.nivel_cm.idxmax()
    return {"cota_maxima_cm": float(t.nivel_cm[i]), "cota_maxima_data_hora": str(t.data_hora[i]), "cota_maxima_serie": str(caminho)}


def ano_e_setor_inteiro(cont: gpd.GeoDataFrame, a: pd.DataFrame, cod: str) -> pd.DataFrame | None:
    """Rodada 34: contagem de 2022 dentro dos setores de 2010 inteiros e a decomposição publicado -> ano -> setor inteiro."""
    u = unioes_de_setores_inteiros_2010(cont, cod)
    if u is None:
        logger.warning("sem setores de 2010 inteiros (ou sem a malha de 2010): decomposição não feita")
        return None
    t, _ = ec.expor(u, "setores-2010-inteiros", origem={"setores_2010": str(ARQ_SETORES_2010.relative_to(c.RAIZ)), "areas": FONTE_SETOR_INTEIRO})
    for nome, desc in (("enderecos-expostos-por-cenario_setores-2010-inteiros-ibge-cnefe_2022_municipal.csv", "saída direta da função de contagem para a união dos setores de 2010 inteiros"),
                       ("enderecos-exposicao-cenarios_setores-2010-inteiros-ibge-cnefe_2022_pontos.gpkg", "endereços com uma coluna por união de setores de 2010 inteiros (dentro/fora) e a população estimada")):
        meta(SAIDA / nome, descricao=desc, colunas={f"exp_{i + 1:02d}": v for i, v in enumerate(u.cenario)})
    t1 = u.drop(columns=["geometry", "valor"]).merge(t.rename(columns={"enderecos_total": "enderecos_domicilio_particular_2022", "pop_estimada_setor_total": "pop_enderecos_setor_2022",
                                                                        "pop_estimada_grade_total": "pop_enderecos_grade_2022"})[["cenario", "enderecos_domicilio_particular_2022", "pop_enderecos_setor_2022", "pop_enderecos_grade_2022"]], on="cenario")
    t1["razao_enderecos_2022_sobre_domicilios_2010"] = t1.enderecos_domicilio_particular_2022 / t1.domicilios_2010
    t1["razao_pop_2022_sobre_moradores_2010"] = t1.pop_enderecos_setor_2022 / t1.moradores_2010
    t1["moradores_por_domicilio_2010"] = t1.moradores_2010 / t1.domicilios_2010
    t1["moradores_por_domicilio_2022"] = t1.pop_enderecos_setor_2022 / t1.enderecos_domicilio_particular_2022
    gravar(t1.drop(columns="cenario"), f"setores-2010-inteiros-contagem-2022_{FONTE_SETOR_INTEIRO}_2010-2022_delimitacao.csv",
           "setores de 2010 inteiros de cada área de risco e no total: área, domicílios e moradores de 2010 e endereços e população de 2022 dentro da união desses setores",
           criterio=f"setor inteiro = {LIMIAR_SETOR_INTEIRO:.0%} ou mais da sua área dentro da área de risco")
    # decomposição: (i) publicado; (ii) mesmos setores, 2022; (iii) áreas inteiras, 2022
    areas = a[a.fonte == FONTE_SETOR_INTEIRO]
    tot, ii = areas[areas.tipo == "total"].iloc[0], t1[t1.delimitacao == "total"].iloc[0]
    sem = areas[(areas.tipo == "parte") & ~areas.delimitacao.isin(t1.delimitacao)]  # áreas sem nenhum setor inteiro (sem dado associado)
    mun = variacao_do_municipio()
    dif_e, dif_p = tot.enderecos_domicilio_particular - ii.enderecos_domicilio_particular_2022, tot.pop_enderecos_setor - ii.pop_enderecos_setor_2022
    passos = [
        {"passo": "(i) publicado: setores de 2010 inteiros, Censo 2010", "area_km2": ii.area_km2, "domicilios_ou_enderecos": tot.fonte_domicilios, "pessoas": tot.fonte_pessoas},
        {"passo": "(i') os mesmos setores, soma dos agregados de 2010 (conferência)", "area_km2": ii.area_km2, "domicilios_ou_enderecos": ii.domicilios_2010, "pessoas": ii.moradores_2010},
        {"passo": "(ii) os mesmos setores, contagem de 2022", "area_km2": ii.area_km2, "domicilios_ou_enderecos": ii.enderecos_domicilio_particular_2022, "pessoas": ii.pop_enderecos_setor_2022,
         "pessoas_pela_grade": ii.pop_enderecos_grade_2022, "razao_enderecos": ii.enderecos_domicilio_particular_2022 / tot.fonte_domicilios, "razao_pessoas": ii.pop_enderecos_setor_2022 / tot.fonte_pessoas, "razao_de": "(ii)/(i) = efeito do ano"},
        {"passo": "(iii) as áreas inteiras, contagem de 2022", "area_km2": tot.area_km2, "domicilios_ou_enderecos": tot.enderecos_domicilio_particular, "pessoas": tot.pop_enderecos_setor,
         "pessoas_pela_grade": tot.pop_enderecos_grade, "razao_enderecos": tot.enderecos_domicilio_particular / ii.enderecos_domicilio_particular_2022, "razao_pessoas": tot.pop_enderecos_setor / ii.pop_enderecos_setor_2022,
         "razao_de": "(iii)/(ii) = efeito do setor inteiro"},
        {"passo": "(iii) − (ii): parte das áreas fora dos setores inteiros", "area_km2": tot.area_km2 - ii.area_km2, "domicilios_ou_enderecos": dif_e, "pessoas": dif_p},
        {"passo": "  … na(s) área(s) sem dado associado", "area_km2": sem.area_km2.sum(), "domicilios_ou_enderecos": sem.enderecos_domicilio_particular.sum(), "pessoas": sem.pop_enderecos_setor.sum(),
         "razao_enderecos": sem.enderecos_domicilio_particular.sum() / dif_e, "razao_pessoas": sem.pop_enderecos_setor.sum() / dif_p, "razao_de": "parte de (iii) − (ii)"},
        {"passo": "  … nos pedaços de setores parciais das outras áreas", "area_km2": tot.area_km2 - ii.area_km2 - sem.area_km2.sum(), "domicilios_ou_enderecos": dif_e - sem.enderecos_domicilio_particular.sum(),
         "pessoas": dif_p - sem.pop_enderecos_setor.sum(), "razao_enderecos": 1 - sem.enderecos_domicilio_particular.sum() / dif_e, "razao_pessoas": 1 - sem.pop_enderecos_setor.sum() / dif_p,
         "razao_de": "parte de (iii) − (ii)"},
        {"passo": "(iii)/(i): publicado para a contagem nas áreas inteiras", "razao_enderecos": tot.enderecos_domicilio_particular / tot.fonte_domicilios, "razao_pessoas": tot.pop_enderecos_setor / tot.fonte_pessoas,
         "razao_de": "(iii)/(i) = efeito do ano × efeito do setor inteiro"},
    ]
    if mun:
        passos.append({"passo": "contexto: população do município, Censo 2010 -> Censo 2022", "pessoas": mun["populacao_2022"], "razao_pessoas": mun["razao_2022_sobre_2010"],
                       "razao_de": f"2022/2010 (2010: {mun['populacao_2010']:.0f})"})
    gravar(pd.DataFrame(passos), f"decomposicao-ano-e-setor-inteiro_{FONTE_SETOR_INTEIRO}_2010-2022_passos.csv",
           "do número publicado à contagem de 2022 nas áreas inteiras: (ii)/(i) é o efeito do ano sobre o mesmo polígono; (iii)/(ii) é a parte das áreas que o número publicado não cobre",
           municipio=mun, nota="domicílios de 2010 = particulares permanentes ocupados (V001); endereços de 2022 = domicílio particular do CNEFE, ocupado ou não: a razão de endereços mistura o ano com essa diferença de definição")
    return pd.DataFrame(passos)


def setores_inteiros_2010(d: gpd.GeoDataFrame, cod: str) -> pd.DataFrame | None:
    """A área corresponde a setores censitários inteiros de 2010? Soma domicílios e moradores de 2010 desses setores.

    Duas somas: a dos setores INTEIROS (LIMIAR_SETOR_INTEIRO da área do setor dentro da área de risco) e, como
    sensibilidade, a dos setores com a maior parte dentro. As malhas de anos diferentes não se alinham com
    exatidão: daí a folga do limiar.
    """
    s = ler_setores_2010(cod)
    if s is None:
        return None
    linhas = []
    for r in d[(d.fonte == FONTE_SETOR_INTEIRO) & (d.tipo == "parte")].itertuples():
        frac = s.geometry.intersection(r.geometry).area / s.geometry.area
        inteiros, maioria = s[frac >= LIMIAR_SETOR_INTEIRO], s[frac >= 0.5]
        uni = inteiros.geometry.union_all() if len(inteiros) else None
        linhas.append({"delimitacao": r.delimitacao, "origem_na_fonte": getattr(r, "origem_na_fonte", None), "area_km2": r.area_km2,
                       "setores_2010_inteiros": len(inteiros), "setores_2010_inteiros_sem_dado": int(inteiros.V002.isna().sum()),
                       "setores_2010_parciais": int(((frac > 0.01) & (frac < LIMIAR_SETOR_INTEIRO)).sum()),
                       "area_dos_setores_inteiros_km2": uni.area / 1e6 if uni else 0.0,
                       "pct_da_area_coberta_por_setores_inteiros": 100 * uni.intersection(r.geometry).area / r.geometry.area if uni else 0.0,
                       "domicilios_2010_setores_inteiros": float(inteiros.V001.sum()), "moradores_2010_setores_inteiros": float(inteiros.V002.sum()),
                       "populacao_total_2010_setores_inteiros": float(inteiros.populacao_total.sum()),
                       "setores_2010_com_maioria_dentro": len(maioria), "domicilios_2010_maioria_dentro": float(maioria.V001.sum()),
                       "moradores_2010_maioria_dentro": float(maioria.V002.sum()), "codigos_setores_2010_inteiros": ";".join(inteiros.cd_setor)})
    return pd.DataFrame(linhas)


# ---------------------------------------------------------------- fração de área urbanizada (rodada 35)
def fracao_de_area_urbanizada(cont: gpd.GeoDataFrame, a: pd.DataFrame) -> pd.DataFrame | None:
    """Fração da área urbanizada e fração dos endereços da área urbanizada dentro da área atingida, no todo e por classe."""
    if not ARQ_URBANIZADAS.exists():
        logger.warning("%s ausente (rode scripts/download/areas_urbanizadas_ibge.py): fração de área urbanizada não calculada", ARQ_URBANIZADAS.name)
        return None
    u = gpd.read_file(ARQ_URBANIZADAS).to_crs(c.CRS_PADRAO)
    atingida = cont[(cont.fonte == FONTE_COM_COTA) & (cont.tipo == "total")].geometry.iloc[0]
    grupos = {"todas as classes": u}
    for col in CLASSES_URBANIZADAS:
        if col in u:
            grupos.update({f"{col}: {v}": u[u[col] == v] for v in sorted(u[col].dropna().unique())})
    linhas = []
    for nome, g in grupos.items():
        geom = g.geometry.union_all()
        linhas.append({"classe": nome, "parte": "no município", "poligonos": len(g), "geometry": geom})
        linhas.append({"classe": nome, "parte": "dentro da área atingida", "poligonos": int(g.intersects(atingida).sum()), "geometry": geom.intersection(atingida)})
    r = gpd.GeoDataFrame(linhas, crs=c.CRS_PADRAO)
    r["cenario"] = r["valor"] = "areas-urbanizadas|" + r.classe + "|" + r.parte
    t, _ = ec.expor(r, "areas-urbanizadas", origem={"areas_urbanizadas": str(ARQ_URBANIZADAS.relative_to(c.RAIZ)), "area_atingida": FONTE_COM_COTA})
    for nome, desc in (("enderecos-expostos-por-cenario_areas-urbanizadas-ibge-cnefe_2022_municipal.csv", "saída direta da função de contagem para a área urbanizada, no município e dentro da área atingida"),
                       ("enderecos-exposicao-cenarios_areas-urbanizadas-ibge-cnefe_2022_pontos.gpkg", "endereços com uma coluna por classe de área urbanizada, no município e dentro da área atingida")):
        meta(SAIDA / nome, descricao=desc, colunas={f"exp_{i + 1:02d}": v for i, v in enumerate(r.cenario)})
    r = r.assign(area_km2=r.geometry.area / 1e6).drop(columns=["geometry", "valor"]).merge(t[["cenario", "enderecos_total", "pop_estimada_setor_total", "pop_estimada_grade_total"]], on="cenario")
    m, d = (r[r.parte == p].set_index("classe") for p in ("no município", "dentro da área atingida"))
    f = pd.DataFrame({"poligonos": m.poligonos, "area_km2": m.area_km2, "area_atingida_km2": d.area_km2, "fracao_de_area_pct": 100 * d.area_km2 / m.area_km2,
                      "enderecos": m.enderecos_total, "enderecos_atingidos": d.enderecos_total, "fracao_de_enderecos_pct": 100 * d.enderecos_total / m.enderecos_total.replace(0, np.nan),
                      "pop_enderecos_setor": m.pop_estimada_setor_total, "pop_enderecos_setor_atingida": d.pop_estimada_setor_total,
                      "fracao_de_populacao_pct": 100 * d.pop_estimada_setor_total / m.pop_estimada_setor_total.replace(0, np.nan),
                      "pop_enderecos_grade_atingida": d.pop_estimada_grade_total}).reset_index()
    f["razao_fracao_area_sobre_fracao_enderecos"] = f.fracao_de_area_pct / f.fracao_de_enderecos_pct
    extra = {}
    if ARQ_SITUACAO.exists():  # população urbana do município (a base que multiplica a fração de área)
        sit = pd.read_csv(ARQ_SITUACAO)
        urb = float(sit[sit.situacao_domicilio == "Urbana"].sort_values("periodo").populacao.iloc[-1])
        pub = a[(a.fonte == FONTE_COM_COTA) & (a.tipo == "total")].fonte_pessoas.iloc[0]
        f["populacao_urbana_municipio"] = urb
        f["estimativa_fracao_de_area_x_pop_urbana"] = f.fracao_de_area_pct / 100 * urb
        f["estimativa_fracao_de_enderecos_x_pop_urbana"] = f.fracao_de_enderecos_pct / 100 * urb
        f["numero_publicado_para_a_area_atingida"] = pub
        f["fracao_implicita_no_numero_publicado_pct"] = 100 * pub / urb
        extra = {"populacao_urbana_municipio": urb, "numero_publicado": None if pd.isna(pub) else float(pub)}
    gravar(f, f"fracao-area-urbanizada-x-fracao-enderecos_{FONTE_COM_COTA}-ibge_2019-2024_classe.csv",
           "área urbanizada (IBGE, 2019) no município e dentro da área atingida, no todo e por classe: fração de área, fração dos endereços de domicílio particular e da população por endereços; "
           "estimativa pela fração de área × população urbana ao lado do número publicado", **extra)
    return f


# ---------------------------------------------------------------- área atingida × maior mancha (rodada 35)
def outros_cursos_dagua(limite):
    """Cursos d'água da rede hidrográfica junto ao município, sem o rio principal (o de maior área de contribuição)."""
    h = gpd.read_file(c.ARQ_BHO, layer="curso_dagua").to_crs(c.CRS_PADRAO)
    h = h[h.intersects(limite.buffer(5000).union_all())]
    return h.drop(index=h.nuareabacc.idxmax())


def so_uma_cobre(cont: gpd.GeoDataFrame, pts: gpd.GeoDataFrame, col: dict, maior: str, limite) -> None:
    """Endereços que só a área atingida ou só a maior mancha cobre: por bairro, distância à borda da outra e proximidade de curso d'água."""
    c_area, c_mancha = f"{FONTE_COM_COTA}|total", f"{CHEIAS}|{maior}"
    geo = cont.set_index("cenario").geometry
    cursos = outros_cursos_dagua(limite).geometry.union_all()
    resumo, bairros = [], []
    for grupo, dentro, fora in ((f"na área atingida e fora da mancha de {maior} cm", c_area, c_mancha), (f"na mancha de {maior} cm e fora da área atingida", c_mancha, c_area)):
        e = pts[pts[col[dentro]] & ~pts[col[fora]]]
        dist = e.geometry.distance(geo[fora].boundary)  # distância à borda da delimitação que NÃO cobre o endereço
        perto = e.geometry.distance(cursos) <= DISTANCIA_CURSO_DAGUA_M
        linha = {"grupo": grupo, "enderecos": len(e), "pop_enderecos_setor": float(e.pop_est_setor.sum()), "distancia_a_borda_de": fora,
                 "distancia_mediana_m": float(dist.median()), "distancia_maxima_m": float(dist.max())}
        lim = (0, *FAIXAS_DISTANCIA_M, np.inf)
        for x0, x1 in zip(lim[:-1], lim[1:]):
            nome = f"ate_{x1}_m" if x0 == 0 else (f"mais_de_{x0}_m" if np.isinf(x1) else f"de_{x0}_a_{x1}_m")
            linha[f"enderecos_{nome}"] = int(((dist > x0) & (dist <= x1)).sum() + (int((dist == 0).sum()) if x0 == 0 else 0))
        linha[f"enderecos_a_ate_{DISTANCIA_CURSO_DAGUA_M}_m_de_outro_curso_dagua"] = int(perto.sum())
        resumo.append(linha)
        b = e.assign(dist=dist, perto=perto).groupby("bairro_malha").agg(enderecos=("dist", "size"), pop_enderecos_setor=("pop_est_setor", "sum"), distancia_mediana_m=("dist", "median"),
                                                                        perto_de_outro_curso_dagua=("perto", "sum")).sort_values("enderecos", ascending=False).reset_index()
        bairros.append(b.assign(grupo=grupo))
    nota = dict(distancia="do endereço à borda da delimitação que não o cobre (a mancha é a cumulativa; a área atingida é a parte dentro do limite municipal)",
                curso_dagua=f"rede hidrográfica do repositório (BHO/ANA), sem o rio principal; perto = até {DISTANCIA_CURSO_DAGUA_M} m do eixo",
                bairro="polígono da malha de bairros que contém o ponto")
    gravar(pd.DataFrame(resumo), f"enderecos-so-area-atingida-ou-so-mancha-cota{maior}_{FONTE_COM_COTA}-sgb_2022_grupo.csv",
           "endereços que só a área atingida ou só a maior mancha por cota cobre: total, distância à borda da outra (mediana e faixas) e proximidade de curso d'água que não é o rio principal", **nota)
    gravar(pd.concat(bairros)[["grupo", "bairro_malha", "enderecos", "pop_enderecos_setor", "distancia_mediana_m", "perto_de_outro_curso_dagua"]],
           f"enderecos-so-area-atingida-ou-so-mancha-cota{maior}_{FONTE_COM_COTA}-sgb_2022_bairro.csv", "o mesmo, por bairro", **nota)


def figura_so_uma_cobre(d: gpd.GeoDataFrame, maior: str) -> dict:
    """Edição A4, recorte urbano: o que a área atingida e a maior mancha cobrem juntas e o que só uma delas cobre. Sem pontos de endereço."""
    base = Base(ARGS.codigo_ibge, agua=True, nome_rio=ARGS.nome_rio, modo_agua="enderecos")
    fundo = Fundo(base)
    ext = base.ext_urb
    lay = lm.LayoutA4([[ext]])
    ax = lay.eixos[0]
    lim = base.limite.union_all()
    area = d[(d.fonte == FONTE_COM_COTA) & (d.tipo == "total")].geometry.iloc[0]
    mancha = d[(d.fonte == CHEIAS) & (d.delimitacao == maior)].geometry.iloc[0].intersection(lim)  # recortada no limite municipal só para exibição
    sigla = next(f["nome"] for f in FONTES if f["id"] == FONTE_COM_COTA).split(" — ")[0]
    hand = []
    for geom, cor, rot in ((area.intersection(mancha), COR_AMBAS, "as duas cobrem"), (area.difference(mancha), COR_SO_AREA, f"só a área atingida de maio de 2024 ({sigla})"),
                           (mancha.difference(area), COR_SO_MANCHA, f"só a mancha da cota de {maior} cm (SGB)")):
        gpd.GeoSeries([geom], crs=c.CRS_PADRAO).plot(ax=ax, color=cor, alpha=0.6, edgecolor="none", zorder=1.5)
        hand.append(Patch(facecolor=cor, alpha=0.6, edgecolor="none", label=rot))
    fundo.desenhar(ax, ext, "urbano", 1000, modo_agua="enderecos", escala_pos=ESCALA_POS)
    caminho = SAIDA / f"area-atingida-x-mancha-cota{maior}_fepam-sgb_2024_diferenca_urbano.png"
    info = lm.finalizar_a4(lay, f"Área diretamente atingida em maio de 2024 e mancha da cota de {maior} cm\nárea urbana da sede", fundo.comum(hand),
                           "o que as duas delimitações cobrem e o que só uma delas cobre",
                           "Fontes: FEPAM (área diretamente atingida, maio de 2024); SGB (manchas por cota); OpenStreetMap (água, vias); ANA (hidrografia).",
                           caminho, origem=caminho, metodo="Mancha cumulativa (união das cotas ≤ a indicada), recortada no limite municipal só para exibição.")
    meta(caminho, descricao="área diretamente atingida e maior mancha por cota: parte comum e parte que só uma cobre, sobre a malha viária e a hidrografia; sem pontos de endereço",
         recorte="área urbana da sede", janela_m=[round(v) for v in ext], aviso_fora_da_imagem="Produto de trabalho — pendente de conferência.", manchas_exibicao=ee.NOTA_RECORTE, **info)
    return {"arquivo": caminho, **info}


# ---------------------------------------------------------------- figura
def figura(d: gpd.GeoDataFrame, maior: str, cota_no_rotulo: str | None = None) -> dict:
    """Edição A4 (16 cm, 300 dpi, legenda abaixo), recorte urbano: só contornos, sem pontos de endereço.

    cota_no_rotulo: a cota como aparece no rótulo da legenda (leitura do mapa); padrão: como está em `maior`."""
    base = Base(ARGS.codigo_ibge, agua=True, nome_rio=ARGS.nome_rio, modo_agua="enderecos")
    fundo = Fundo(base)
    ext = base.ext_urb
    lay = lm.LayoutA4([[ext]])
    ax = lay.eixos[0]
    hand, itens = [], []
    for f in FONTES:
        cor, traco, esp = ESTILO[f["id"]]
        partes = d[(d.fonte == f["id"]) & (d.tipo == "parte")]
        g = partes if len(partes) else d[(d.fonte == f["id"]) & (d.tipo == "total")]
        g.boundary.plot(ax=ax, color=cor, linestyle=traco, linewidth=esp, zorder=6)
        n = f" ({len(partes)}; {f['prefixo']}1 a {f['prefixo']}{len(partes)})" if len(partes) else ""
        hand.append(Line2D([], [], color=cor, ls=traco, lw=esp, label=f"{f['nome']}{n}"))
        itens += [(r.geometry.representative_point().x, r.geometry.representative_point().y, r.rotulo, cor) for r in partes.itertuples()]
    # mancha recortada no limite municipal só para exibição (o dado cobre também a outra margem do rio), como nos mapas do estudo
    mancha = d[(d.fonte == CHEIAS) & (d.delimitacao == maior)].geometry.intersection(base.limite.union_all())
    mancha.boundary.plot(ax=ax, color=COR_MANCHA, linewidth=0.8, zorder=5)
    hand.append(Line2D([], [], color=COR_MANCHA, lw=0.8, label=f"SGB — borda da mancha da cota de {cota_no_rotulo or maior} cm"))
    fundo.desenhar(ax, ext, "urbano", 1000, modo_agua="enderecos", escala_pos=ESCALA_POS)
    # rótulos sem sobreposição entre si (áreas de fontes diferentes podem coincidir)
    pos = lm.posicionar_rotulos(ax, [(x, y, t) for x, y, t, _ in itens], lm.FS_ROTULO_MIN)
    for (x, y, t, cor), (dx, dy, chamada) in zip(itens, pos):
        ax.annotate(t, (x, y), xytext=(dx, dy), textcoords="offset points", fontsize=lm.FS_ROTULO_MIN, fontweight="bold", color=cor, zorder=11,
                    path_effects=[pe.withStroke(linewidth=2.2, foreground="#ffffff")],
                    arrowprops=dict(arrowstyle="-", color=cor, lw=0.5) if chamada else None)
    caminho = SAIDA / f"delimitacoes-oficiais-e-mancha-cota{maior}_sgb-ibge-fepam_2010-2024_contornos_urbano.png"
    info = lm.finalizar_a4(lay, f"Delimitações oficiais de área sujeita a inundação e mancha da cota de {maior} cm\nárea urbana da sede", fundo.comum(hand),
                           "contorno de cada delimitação (anos de referência diferentes)",
                           "Fontes: SGB (setorização de risco, 2014; manchas por cota); IBGE (população em áreas de risco, 2018, base do Censo 2010); "
                           "FEPAM (área diretamente atingida, maio de 2024); OpenStreetMap (água, vias); ANA (hidrografia).",
                           caminho, origem=caminho, metodo="Só contornos; a área de maio de 2024 segue para fora do recorte e cobre também área rural.")
    meta(caminho, descricao="contornos das delimitações oficiais e borda da maior mancha por cota, sobre a malha viária; sem pontos de endereço",
         recorte="área urbana da sede", janela_m=[round(v) for v in ext], rotulos={r.rotulo: f"{r.fonte}|{r.delimitacao}" for r in d[d.tipo == "parte"].itertuples()},
         aviso_fora_da_imagem="Produto de trabalho — pendente de conferência.", manchas_exibicao=ee.NOTA_RECORTE, **info)
    return {"arquivo": caminho, **info}


# ---------------------------------------------------------------- principal
def main() -> None:
    SAIDA.mkdir(parents=True, exist_ok=True)
    cod = ARGS.codigo_ibge
    ec.ARGS, ec.SAIDA = ARGS, SAIDA  # a contagem reaproveita expor(); tudo o que ela grava fica na pasta desta rodada
    limite = c.carregar_area_estudo()
    st = gpd.read_file(c.CAMADAS / "populacao-setores_ibge-censo_2022_setor.gpkg")
    d = aplicar_publicado(carregar_delimitacoes(cod, limite, st), ARGS.publicado)
    cont = d[d.fonte != CHEIAS_NC].reset_index(drop=True)  # as manchas não cumulativas só entram na conferência dos métodos por área
    K = list(cont[cont.fonte == CHEIAS].delimitacao)
    maior = K[-1]

    # ---- B1/B2 dentro de cada polígono (função do estudo de cenários)
    t, pts = ec.expor(cont, "delimitacoes-oficiais", origem={"delimitacoes": [str(f["arquivo"].relative_to(c.RAIZ)) for f in FONTES], "cheias": str(ee.ARQ_COTAS.relative_to(c.RAIZ))})
    col = {r.cenario: f"exp_{i + 1:02d}" for i, r in enumerate(cont.itertuples())}
    for nome, desc in ((f"enderecos-expostos-por-cenario_delimitacoes-oficiais-ibge-cnefe_2022_municipal.csv", "endereços de domicílio particular e população estimada dentro de cada delimitação (saída direta da função de contagem)"),
                       (f"enderecos-exposicao-cenarios_delimitacoes-oficiais-ibge-cnefe_2022_pontos.gpkg", "endereços de domicílio particular com uma coluna por delimitação (dentro/fora) e a população estimada")):
        meta(SAIDA / nome, descricao=desc, colunas_por_delimitacao=col, metodo="B1: ponto dentro do polígono; B2: população do setor 2022 repartida entre os endereços do setor (grade como sensibilidade)")

    # ---- tabela A: contagem por delimitação, com todas as espécies e os números da fonte
    esp = contar_especies(cont, enderecos_todas_especies(cod))
    a = cont.drop(columns=["geometry", "valor"]).merge(t.rename(columns={"enderecos_total": "enderecos_domicilio_particular", "pop_estimada_setor_total": "pop_enderecos_setor",
                                                                         "pop_estimada_grade_total": "pop_enderecos_grade"})[["cenario", "enderecos_domicilio_particular", "pop_enderecos_setor", "pop_enderecos_grade"]], on="cenario").merge(esp, on="cenario")
    a["moradores_por_domicilio"] = a.pop_enderecos_setor / a.enderecos_domicilio_particular.replace(0, np.nan)
    a["razao_publicado_sobre_contagem"] = a.fonte_pessoas / a.pop_enderecos_setor.replace(0, np.nan)
    gravar(a, "contagem-por-delimitacao_fontes-oficiais-ibge-cnefe_2022_delimitacao.csv",
           "por delimitação: área, endereços de domicílio particular, todos os endereços por espécie, população por endereços (setor e grade), moradores por domicílio e os números da fonte",
           especies=ESPECIES, total="união dos polígonos da fonte", publicado=[str(x) for x in ARGS.publicado] if ARGS.publicado else "só atributos da geometria")

    # ---- A2: coincidência das geometrias e setores inteiros de 2010
    for fa, fb in PARES_SOBREPOSICAO:
        gravar(sobreposicao(cont, fa, fb), f"sobreposicao-geometrias_{fa}-x-{fb}_delimitacao.csv", "área em comum de cada par de polígonos das duas fontes, sobre a área de cada um")
    s10 = setores_inteiros_2010(cont, cod)
    if s10 is None:
        logger.warning("malha de setores de 2010 ausente: exame de setores inteiros não feito")
    else:
        gravar(s10, f"setores-inteiros-2010_{FONTE_SETOR_INTEIRO}_2010_delimitacao.csv",
               "setores censitários de 2010 inteiros dentro de cada área de risco, parte da área que eles cobrem e soma de domicílios e moradores de 2010 (Básico: V001 e V002)",
               criterio=f"setor inteiro = {LIMIAR_SETOR_INTEIRO:.0%} ou mais da sua área dentro da área de risco; sensibilidade: 50 % ou mais")

    # ---- A4: cruzamento com as cheias
    linhas = []
    for r in cont[cont.fonte != CHEIAS].itertuples():
        m = pts[col[r.cenario]]
        linha = {"fonte": r.fonte, "delimitacao": r.delimitacao, "enderecos_na_delimitacao": int(m.sum())}
        for k in K:
            mk = m & pts[col[f"{CHEIAS}|{k}"]]
            linha[f"enderecos_na_mancha_{k}"], linha[f"pop_na_mancha_{k}"] = int(mk.sum()), float(pts.pop_est_setor[mk].sum())
        fora = m & ~pts[col[f"{CHEIAS}|{maior}"]]
        linha["enderecos_em_nenhuma_mancha"], linha["pop_em_nenhuma_mancha"] = int(fora.sum()), float(pts.pop_est_setor[fora].sum())
        linhas.append(linha)
    gravar(pd.DataFrame(linhas), "enderecos-delimitacoes-x-cheias_fontes-oficiais-sgb_2022_delimitacao.csv",
           "dos endereços dentro de cada delimitação, quantos estão dentro da mancha (cumulativa) de cada cota e quantos em nenhuma")
    # o contrário: endereços da maior mancha fora de cada delimitação, por bairro (ponto dentro do polígono da malha de bairros)
    bairros = gpd.read_file(ARQ_BAIRROS).to_crs(c.CRS_PADRAO)
    jb = gpd.sjoin(pts[["geometry"]], bairros[["NM_BAIRRO", "geometry"]], predicate="within", how="left")
    pts["bairro_malha"] = jb[~jb.index.duplicated()].NM_BAIRRO.fillna("(fora da malha de bairros)")
    na_maior = pts[col[f"{CHEIAS}|{maior}"]]
    partes = []
    for r in cont[(cont.fonte != CHEIAS) & (cont.tipo == "total")].itertuples():
        g = pts[na_maior].assign(fora=lambda x, cc=col[r.cenario]: ~x[cc])
        g["pop_fora"] = g.pop_est_setor.where(g.fora, 0.0)
        b = g.groupby("bairro_malha").agg(enderecos_na_mancha=("fora", "size"), enderecos_fora_da_delimitacao=("fora", "sum"),
                                          pop_fora_da_delimitacao=("pop_fora", "sum")).reset_index()
        partes.append(b.assign(fonte=r.fonte, cota_cm=maior))
    tb = pd.concat(partes)[["fonte", "cota_cm", "bairro_malha", "enderecos_na_mancha", "enderecos_fora_da_delimitacao", "pop_fora_da_delimitacao"]]
    gravar(tb, f"enderecos-mancha-cota{maior}-fora-das-delimitacoes_fontes-oficiais-sgb_2022_bairro.csv",
           "endereços dentro da maior mancha por cota e fora de cada delimitação oficial (total da fonte), por bairro da malha de bairros",
           bairro="polígono da malha de bairros que contém o ponto (no estudo atual, o bairro é o atributo do setor do endereço)",
           enderecos_na_mancha_com_bairro_diferente_do_atributo_do_setor=int((pts.bairro_malha[na_maior] != pts.bairro[na_maior]).sum()))

    # ---- B: três métodos sobre os mesmos polígonos
    ar = populacao_por_area_e_uso_do_solo(d)
    b = d.drop(columns=["geometry", "valor"])[["cenario", "fonte", "delimitacao", "tipo", "area_km2"]].merge(ar, on="cenario").merge(
        a[["cenario", "enderecos_domicilio_particular", "pop_enderecos_setor", "pop_enderecos_grade"]], on="cenario", how="left")
    b["razao_area_sobre_enderecos"] = b.pop_area_setor / b.pop_enderecos_setor.replace(0, np.nan)
    b["razao_uso_solo_sobre_enderecos"] = b.pop_uso_solo / b.pop_enderecos_setor.replace(0, np.nan)
    b["manchas"] = np.where(b.fonte == CHEIAS, "cumulativas", np.where(b.fonte == CHEIAS_NC, "não cumulativas (só conferência dos métodos 1 e 2)", "polígono da fonte"))
    gravar(b, "populacao-tres-metodos_fontes-oficiais-sgb-ibge_2022_delimitacao.csv",
           "população de 2022 por três métodos sobre os mesmos polígonos: (1) área do setor dentro do polígono; (2) uso do solo; (3) endereços (setor; grade como sensibilidade)",
           metodos_1_e_2="recalculados pelas funções de vulnerabilidade_inundacao.py (calcular_exposicao_por_setor), com as manchas CUMULATIVAS nas linhas de cheia",
           populacao_municipio_metodo_1=float(vi.carregar_setores_com_indicadores().populacao_total.sum()), populacao_municipio_metodo_3=float(st["pop"].sum()))

    # ---- C1: síntese
    s = a[(a.fonte == CHEIAS) | (a.tipo == "total")][["fonte", "delimitacao", "ano_fonte", "area_km2", "fonte_pessoas", "fonte_o_que_conta", "fonte_documento", "fonte_pagina",
                                                    "enderecos_domicilio_particular", "pop_enderecos_setor", "pop_enderecos_grade", "razao_publicado_sobre_contagem"]]
    # rodada 34: a linha da fonte de setor inteiro abre em três passos; a do evento datado recebe a cota máxima
    s.insert(2, "passo", "")
    dec = ano_e_setor_inteiro(cont, a, cod)
    if dec is not None:
        m = (s.fonte == FONTE_SETOR_INTEIRO).values
        base, p = s[m].iloc[0], dec.set_index("passo")
        i, ii = p.iloc[0], p.iloc[2]
        abertas = pd.DataFrame([
            {**base, "passo": p.index[0], "area_km2": i.area_km2, "enderecos_domicilio_particular": np.nan, "pop_enderecos_setor": np.nan, "pop_enderecos_grade": np.nan, "razao_publicado_sobre_contagem": np.nan},
            {**base, "passo": p.index[2], "area_km2": ii.area_km2, "enderecos_domicilio_particular": ii.domicilios_ou_enderecos, "pop_enderecos_setor": ii.pessoas, "pop_enderecos_grade": ii.pessoas_pela_grade,
             "razao_publicado_sobre_contagem": base.fonte_pessoas / ii.pessoas},
            {**base, "passo": p.index[3]}])
        s = pd.concat([s[~m & (np.arange(len(s)) < np.flatnonzero(m)[0])], abertas, s[~m & (np.arange(len(s)) > np.flatnonzero(m)[0])]], ignore_index=True)
    for k, v in cota_maxima(ARGS.nivel_rio).items():
        s[k] = np.where(s.fonte == FONTE_COM_COTA, v, np.nan if k == "cota_maxima_cm" else "")
    gravar(s, "sintese_delimitacoes-oficiais-x-contagem_2022_delimitacao.csv",
           "uma linha por delimitação: ano de referência, área, número publicado (pessoas) e o que ele conta, endereços e população de 2022 por endereços, razão publicado/contagem; "
           "a fonte de setor inteiro aberta em três passos; a área do evento datado com a cota máxima do rio no período",
           versao="rodada 34; a da rodada 33 está no arquivo de sufixo _rodada-33")

    # ---- rodada 35: fração de área urbanizada e o que só a área atingida ou só a maior mancha cobre
    fracao_de_area_urbanizada(cont, a)
    so_uma_cobre(cont, pts, col, maior, limite)
    info2 = figura_so_uma_cobre(cont, maior)
    logger.info("Figura: %s — %.1f × %.1f cm", info2["arquivo"].relative_to(c.RAIZ), info2["largura_cm"], info2["altura_cm"])

    info = figura(cont, maior)
    pd.set_option("display.width", 250); pd.set_option("display.max_columns", 40); pd.set_option("display.max_colwidth", 40)  # noqa: E702
    print(a.drop(columns=["cenario", "fonte_o_que_conta", "fonte_documento"]).round(2).to_string(index=False))
    print(b.drop(columns=["cenario"]).round(2).to_string(index=False))
    print(s.round(2).to_string(index=False))
    logger.info("Figura: %s — %.1f × %.1f cm", info["arquivo"].relative_to(c.RAIZ), info["largura_cm"], info["altura_cm"])


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    _p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    _p.add_argument("--codigo-ibge", default=None, help="padrão: o código da área de estudo (config/area_estudo.geojson)")
    _p.add_argument("--nome-rio", default=c.NOME_RIO_DEFAULT, help="rótulo do rio principal na legenda")
    _p.add_argument("--publicado", type=Path, nargs="*", help="CSV com números publicados que não são atributo da geometria (padrão: os de docs/exposicao_inundacao/numeros_publicados/)")
    _p.add_argument("--nivel-rio", type=Path, help="CSV da série do nível do rio (colunas data_hora e nivel_cm) do período do evento")
    ARGS = _p.parse_args()
    ARGS.codigo_ibge = ARGS.codigo_ibge or codigo_da_area_de_estudo()
    if ARGS.publicado is None:
        ARGS.publicado = sorted(PUBLICADOS.glob(PADRAO_PUBLICADOS))
    main()
