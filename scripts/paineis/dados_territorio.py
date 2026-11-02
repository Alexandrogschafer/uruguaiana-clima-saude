"""
Produtos da tela de território e meio físico (T4) em data/paineis/, a partir dos
mesmos arquivos que alimentavam o painel de território:

    - uso do solo: data/processed/uso-solo_mapbiomas_serie-temporal_area-por-classe.csv
    - meio físico (BDiA): resumos por classe nos metadados data/raw/vetor/{geologia,
      geomorfologia,vegetacao,pedologia}_ibge-bdia_atual_vetorial.json
    - poços: data/raw/vetor/pocos-agua-subterranea_siagas-cprm_atual_vetorial.gpkg
    - APP hídrica: data/processed/app-hidrica_calculado_atual_vetorial.json e
      data/processed/app-ocupacao_calculado_2024.json
    - áreas urbanizadas 2019 (IBGE): data/raw/vetor/areas-urbanizadas_ibge_2019_recorte-municipio.gpkg
      (produto gerado, ainda sem bloco na tela)

Textos de nota e indicadores vão nos campos "notas" e "kpis", com números
calculados aqui. Áreas em km² arredondadas a 2 casas, como no painel anterior;
"Outras" da pedologia = soma das áreas já arredondadas. Os rótulos curtos das
classes são só apresentação. CRS: as áreas vêm calculadas em EPSG:31981 pelos
scripts de origem; nenhuma geometria é publicada.

Uso:
    python scripts/paineis/dados_territorio.py [--codigo-ibge 4322400] [--forcar] [--so <id>]
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import comum  # noqa: E402
from comum import br  # noqa: E402

RAIZ = comum.RAIZ
SCRIPT = "scripts/paineis/dados_territorio.py"
VETOR = RAIZ / "data/raw/vetor"
E = {
    "uso": RAIZ / "data/processed/uso-solo_mapbiomas_serie-temporal_area-por-classe.csv",
    "geologia": VETOR / "geologia_ibge-bdia_atual_vetorial.json",
    "geomorfologia": VETOR / "geomorfologia_ibge-bdia_atual_vetorial.json",
    "vegetacao": VETOR / "vegetacao_ibge-bdia_atual_vetorial.json",
    "pedologia": VETOR / "pedologia_ibge-bdia_atual_vetorial.json",
    "pocos": VETOR / "pocos-agua-subterranea_siagas-cprm_atual_vetorial.gpkg",
    "app": RAIZ / "data/processed/app-hidrica_calculado_atual_vetorial.json",
    "app_ocup": RAIZ / "data/processed/app-ocupacao_calculado_2024.json",
    "urbanizadas": VETOR / "areas-urbanizadas_ibge_2019_recorte-municipio.gpkg",
}
CLASSES_SERIE = ["Formação Campestre", "Arroz", "Área Urbanizada", "Mosaico de Usos", "Outras Lavouras Temporárias", "Formação Florestal"]
TIPO_CLASSE = {"Formação Campestre": "natural", "Formação Florestal": "natural", "Campo Alagado e Área Pantanosa": "natural",
               "Afloramento Rochoso": "natural", "Rio, Lago e Oceano": "agua"}  # demais: antrópica
ROTULOS = {
    "geologia": {"Depósitos Aluvionares Holocênicos": "Dep. Aluv. Holocênicos", "Depósitos Aluvionares Antigos": "Dep. Aluv. Antigos",
                 "Corpo d'água continental": "Corpo d'água"},
    "geomorfologia": {"2Planalto da Campanha": "Planalto da Campanha", "1Planícies Alúvio-coluvionares": "Planícies Alúvio-coluv.",
                      "6Corpo d´água continental": "Corpo d'água"},
    "vegetacao": {"3Ag - Agropecuária": "Agropecuária", "3Vss - Vegetação Secundária sem palmeiras": "Veg. Secundária",
                  "5Corpo d'água continental": "Corpo d'água", "3Iu - Influência urbana": "Influência urbana"},
}
ROTULO_AQUIFERO = {"Serra geral": "Serra Geral", "Guarani": "Guarani"}  # demais e vazio: "Outros/sem dado"
PEDOLOGIA_TOPO = 7
# larguras de faixa da APP hídrica (premissas do cálculo em processamento/app_hidrica.py)
FAIXA_APP = {"rede_local": ("Rede local", 30), "rio_uruguai": ("Rio Uruguai", 200)}
FONTES = {
    "uso": "Uso do solo — MapBiomas Coleção 10",
    "bdia": "Geologia, geomorfologia, pedologia e vegetação nativa — Banco de Dados de Informações Ambientais do IBGE (BDiA), escala 1:250.000",
    "pocos": "Poços de água subterrânea — SIAGAS/CPRM",
    "app": "APP hídrica — calculada a partir da rede hidrográfica ANA/BHO (aproximação metodológica) cruzada com MapBiomas 2024",
    "urb": "Áreas urbanizadas — IBGE 2019",
}
LEITURA = ("**Leitura recomendada:** a classificação de suscetibilidade (risco geológico) é um produto de modelagem validado por campo, "
           "não medição direta — use como triagem territorial, não como laudo pontual. As camadas de meio físico do BDiA "
           "(geologia/geomorfologia/pedologia/vegetação) são leitura regional (1:250.000), não decisão em nível de microárea; a APP "
           "hídrica é uma aproximação exploratória, não uma delimitação técnica para fiscalização ou licenciamento.")


def _j(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def uso() -> tuple[list, list, list]:
    u = pd.read_csv(E["uso"])
    anos = sorted(int(a) for a in u.ano.unique())
    serie = []
    for a in anos:
        linha = {"ano": a}
        for c in CLASSES_SERIE:
            linha[c] = float(u[(u.ano == a) & (u.classe_nome_pt == c)].area_km2.sum())
        serie.append(linha)
    u24 = u[u.ano == anos[-1]].sort_values("area_km2", ascending=False)
    classes = [{"classe": r.classe_nome_pt, "area_km2": round(float(r.area_km2), 2), "tipo": TIPO_CLASSE.get(r.classe_nome_pt, "antropica")}
               for r in u24.itertuples()]
    return anos, serie, classes


def meio_fisico() -> list[dict]:
    linhas = []
    for tema in ("geologia", "geomorfologia", "vegetacao"):
        m = _j(E[tema])
        areas = m.get("area_km2_por_classe") or m["area_km2_por_classe_legenda"]
        for k, v in areas.items():
            linhas.append({"tema": tema, "rotulo": ROTULOS[tema].get(k, k), "classe": k, "area_km2": round(v, 2), "n_feicoes": None})
    m = _j(E["pedologia"])
    ped = sorted(m["area_km2_por_classe"].items(), key=lambda kv: -kv[1])
    for k, v in ped:
        linhas.append({"tema": "pedologia", "rotulo": "Corpo d'água" if k.startswith("Corpo") else k.split(" - ")[0], "classe": k,
                       "area_km2": round(v, 2), "n_feicoes": int(m["n_feicoes_por_classe"][k])})
    # gráfico da pedologia: as 7 maiores classes e o resto agrupado (soma das áreas já arredondadas)
    for k, v in ped[:PEDOLOGIA_TOPO]:
        linhas.append({"tema": "pedologia_grafico", "rotulo": "Corpo d'água" if k.startswith("Corpo") else k.split(" - ")[0], "classe": k,
                       "area_km2": round(v, 2), "n_feicoes": None})
    resto = ped[PEDOLOGIA_TOPO:]
    linhas.append({"tema": "pedologia_grafico", "rotulo": f"Outras ({len(resto)})", "classe": "outras",
                   "area_km2": round(sum(round(v, 2) for _, v in resto), 2), "n_feicoes": None})
    return linhas


def pocos() -> tuple[list, dict]:
    g = gpd.read_file(E["pocos"])
    rot = g["aquifero"].map(lambda a: ROTULO_AQUIFERO.get(a, "Outros/sem dado") if isinstance(a, str) else "Outros/sem dado")
    cont = rot.value_counts()
    ordem = [r for r in ("Serra Geral", "Guarani") if r in cont] + ["Outros/sem dado"]
    linhas = [{"aquifero": r, "pocos": int(cont.get(r, 0))} for r in ordem]
    prof = g["profundidade_m"]
    resumo = {"total": int(len(g)), "profundidade_media_m": round(float(prof.mean()), 2), "profundidade_mediana_m": float(prof.median()),
              "pct_sem_profundidade": round(float(prof.isna().mean() * 100), 1)}
    return linhas, resumo


def app() -> tuple[list, dict]:
    ah, ao = _j(E["app"]), _j(E["app_ocup"])
    linhas = [{"curso": k, "rotulo": f"{FAIXA_APP[k][0]} ({FAIXA_APP[k][1]}m)", "area_km2": v} for k, v in ah["area_km2_por_curso"].items()]
    resumo = {"area_total_km2": ao["area_total_app_km2"], "pct_antropica": ao["pct_area_app_ocupada_por_classes_antropicas"],
              "pct_antropica_excl_agua": ao["pct_area_app_antropica_sobre_area_terrestre_excl_agua"],
              "area_natural_km2": ao["area_natural_km2"], "area_antropica_km2": ao["area_antropica_km2"], "area_agua_km2": ao["area_agua_km2"]}
    return linhas, resumo


def urbanizadas() -> list[dict]:
    g = gpd.read_file(E["urbanizadas"])
    s = g.groupby(["Tipo", "Densidade"], dropna=False).area_km2.sum().reset_index()
    return [{"tipo": r.Tipo, "densidade": r.Densidade, "area_km2": round(float(r.area_km2), 2)} for r in s.itertuples()]


def notas_kpis(anos, serie, classes, meio, poc_l, poc_r, app_l, app_r):
    a24 = classes
    total = sum(c["area_km2"] for c in a24)
    soma = lambda t: sum(c["area_km2"] for c in a24 if c["tipo"] == t)
    dom = a24[0]
    ult = serie[-1]
    razao = ult["Formação Campestre"] / ult["Área Urbanizada"]
    ordem_grandeza = 10 ** round(math.log10(razao))
    n_tema = lambda t: sum(1 for r in meio if r["tema"] == t)
    top2 = poc_l[:2]
    notas = {
        "ter_meta_uso": f"{anos[0]}–{anos[-1]}",
        "ter_nota_uso": (f"Série MapBiomas (Coleção 10), anos disponíveis {anos[0]}–{anos[-1]}. Escalas independentes por classe — a Formação "
                         f"Campestre nativa é ~{ordem_grandeza}× maior que a área urbanizada, numa escala só a urbanização não apareceria."),
        "ter_nota_uso_classes": (f"As {len(a24)} classes da área de estudo em {anos[-1]}, não só as 3 em destaque acima — para dar visibilidade "
                                 "equivalente a lavoura, mosaico de usos e área urbana, não só vegetação nativa. O MapBiomas diferencia Soja e "
                                 "Arroz especificamente, mas agrupa os demais cultivos anuais em \"Outras Lavouras Temporárias\"; não há classe "
                                 "de pastagem isolada nesta série. \"Mosaico de Usos\" não é vegetação nem lavoura específica — é a classe do "
                                 "MapBiomas para áreas onde agricultura/pastagem/vegetação se misturam no pixel."),
        "ter_meta_uso_2024": f"{anos[-1]} · {len(a24)} classes",
        "ter_nota_meio": ("Geologia, geomorfologia, pedologia e vegetação nativa vêm do Banco de Dados de Informações Ambientais do IBGE (BDiA), "
                          "escala 1:250.000 — leitura regional/contextual, não decisão em nível de microárea; versão preliminar, ainda em "
                          "revisão. \"Vegetação nativa\" aqui é diferente do \"Uso do solo\" (MapBiomas) acima: classifica fitofisionomias "
                          "remanescentes, não uso genérico da terra — por isso aparece quase toda como \"Agropecuária\" (nenhuma fitofisionomia "
                          "nativa primária é detectável nessa escala dentro do município, coerente com um Pampa intensamente ocupado)."),
        "ter_meta_geologia": f"{n_tema('geologia')} classes",
        "ter_meta_geomorfologia": f"{n_tema('geomorfologia')} classes",
        "ter_meta_vegetacao": f"{n_tema('vegetacao')} classes",
        "ter_meta_pedologia": f"top {PEDOLOGIA_TOPO} de {n_tema('pedologia')} classes, resto agrupado em \"Outras\"",
        "ter_nota_pocos_app": ("Poços de água subterrânea (SIAGAS/CPRM) e Área de Preservação Permanente (APP) hídrica calculada a partir da "
                               "rede de drenagem (ANA/BHO) — a APP é uma **aproximação metodológica** (largura do rio Uruguai estimada, rede "
                               "local assumida <10m), útil para leitura exploratória, não para fiscalização/licenciamento."),
        "ter_meta_pocos": f"SIAGAS/CPRM · {poc_r['total']} poços",
        "ter_meta_app": "aproximação metodológica",
        "ter_resumo_pedologia": f"Ver pedologia completa em tabela ({n_tema('pedologia')} classes)",
        "ter_resumo_uso": "Ver uso do solo em tabela (km² por ano)",
        "ter_meta_masthead_uso": f"{anos[0]}–{anos[-1]}",
        "ter_meta_masthead_meio": "escala 1:250.000, versão preliminar",
        "ter_leitura": LEITURA,
    }
    kpis_uso = [
        {"rotulo": "Classe dominante", "valor": br(dom["area_km2"]), "unidade": "km²", "sub": f"{dom['classe']} · {br(100 * dom['area_km2'] / total, 1, 0)}% da área"},
        {"rotulo": "Uso antrópico (total)", "valor": br(100 * soma("antropica") / total, 1, 0), "unidade": "%",
         "sub": f"{br(soma('antropica'))} km² — mosaico de usos, lavouras, área urbana..."},
        {"rotulo": "Natural remanescente", "valor": br(100 * soma("natural") / total, 1, 0), "unidade": "%",
         "sub": f"{br(soma('natural'))} km² — campestre, florestal, alagado, afloramento"},
        {"rotulo": "Água", "valor": br(100 * soma("agua") / total, 1, 0), "unidade": "%", "sub": f"{br(soma('agua'))} km² — rio, lago e oceano"},
    ]
    app_rede = next(r for r in app_l if r["curso"] == "rede_local")["area_km2"]
    app_rio = next(r for r in app_l if r["curso"] == "rio_uruguai")["area_km2"]
    kpis_meio = [
        {"rotulo": "Poços de água subterrânea (SIAGAS)", "valor": br(poc_r["total"]), "unidade": "poços",
         "sub": f"{top2[0]['aquifero']} ({top2[0]['pocos']}) e {top2[1]['aquifero']} ({top2[1]['pocos']}) são os aquíferos predominantes"},
        {"rotulo": "Profundidade média dos poços", "valor": br(poc_r["profundidade_media_m"], 1, 0), "unidade": "m",
         "sub": f"mediana {br(poc_r['profundidade_mediana_m'])}m · {br(poc_r['pct_sem_profundidade'], 1, 0)}% sem esse dado"},
        {"rotulo": "APP hídrica — área total (aprox.)", "valor": br(app_r["area_total_km2"]), "unidade": "km²",
         "sub": f"{br(app_rede, 1, 0)} km² rede local + {br(app_rio, 1, 0)} km² rio Uruguai"},
        {"rotulo": "APP hídrica — ocupação antrópica", "valor": br(app_r["pct_antropica"], 1, 0), "unidade": "%",
         "sub": f"{br(app_r['pct_antropica_excl_agua'], 1, 0)}% excluindo a lâmina d'água · MapBiomas {anos[-1]}"},
    ]
    return notas, kpis_uso, kpis_meio


def ids() -> dict[str, str]:
    anos, _, _ = uso()
    return {"serie": f"uso-solo_mapbiomas_{anos[0]}-{anos[-1]}_classes", "classes": f"uso-solo_mapbiomas_{anos[-1]}_todas-classes",
            "meio": "meio-fisico_ibge-bdia_atual_classes", "pocos": "pocos_siagas_atual_aquifero", "app": "app-hidrica_ana-bho-mapbiomas_2024_curso",
            "urb": "areas-urbanizadas_ibge_2019_classe"}


def gerar(codigo_ibge: str = "4322400", forcar: bool = False, so: str | None = None) -> dict[str, bool]:
    anos, serie, classes = uso()
    meio = meio_fisico()
    poc_l, poc_r = pocos()
    app_l, app_r = app()
    notas, kpis_uso, kpis_meio = notas_kpis(anos, serie, classes, meio, poc_l, poc_r, app_l, app_r)
    n = ids()
    c = dict(codigo_ibge=codigo_ibge, script=SCRIPT, selo="conferido")
    defs = [
        (n["serie"], ["CA08"], dict(titulo="Uso do solo por classe (6 classes em destaque)", fonte_curta="MapBiomas", fonte="MapBiomas Coleção 10, área por classe no município",
                                    unidade_espacial="município", unidade_medida="km²", periodo={"inicio": str(anos[0]), "fim": str(anos[-1])}, dados_ate=str(anos[-1]),
                                    avisos=[], colunas=[{"nome": "ano", "tipo": "int"}] + [{"nome": k, "tipo": "float"} for k in CLASSES_SERIE], dados=serie,
                                    extras={"fonte_rodape": FONTES["uso"], "telas": ["territorio"], "notas": notas, "kpis_uso": kpis_uso, "kpis_meio": kpis_meio}),
         [E["uso"]], "área por classe e ano como no CSV de uso-solo_mapbiomas.py"),
        (n["classes"], ["CA08"], dict(titulo=f"Uso do solo em {anos[-1]}, todas as classes", fonte_curta="MapBiomas", fonte="MapBiomas Coleção 10",
                                      unidade_espacial="município", unidade_medida="km²", periodo={"inicio": str(anos[-1]), "fim": str(anos[-1])}, dados_ate=str(anos[-1]),
                                      avisos=[{"tipo": "metodo", "texto": "tipo: natural remanescente, uso antrópico ou água"}],
                                      colunas=[{"nome": "classe", "tipo": "texto"}, {"nome": "area_km2", "tipo": "float", "casas": 2}, {"nome": "tipo", "tipo": "texto"}],
                                      dados=classes, extras={"fonte_rodape": FONTES["uso"], "telas": ["territorio"]}),
         [E["uso"]], "classes do último ano em ordem de área, arredondadas a 2 casas"),
        (n["meio"], ["TB10", "TB11", "TB12", "TB13"], dict(titulo="Meio físico: área por classe (BDiA)", fonte_curta="IBGE BDiA",
                                                            fonte="IBGE, Banco de Dados de Informações Ambientais (BDiA), 1:250.000", unidade_espacial="município",
                                                            unidade_medida="km²", periodo={"inicio": "atual", "fim": "atual"}, dados_ate="2026-08-09",
                                                            avisos=[{"tipo": "metodo", "texto": "escala 1:250.000: leitura regional, não decisão em nível de microárea"}],
                                                            colunas=[{"nome": k, "tipo": t} for k, t in (("tema", "texto"), ("rotulo", "texto"), ("classe", "texto"), ("area_km2", "float"), ("n_feicoes", "int"))],
                                                            dados=meio, extras={"fonte_rodape": FONTES["bdia"], "telas": ["territorio"]}),
         [E["geologia"], E["geomorfologia"], E["vegetacao"], E["pedologia"]], "área por classe dos metadados de cada tema; pedologia_grafico = 7 maiores e o resto somado"),
        (n["pocos"], ["TB14"], dict(titulo="Poços de água subterrânea por aquífero", fonte_curta="SIAGAS/CPRM", fonte="SGB/CPRM, SIAGAS", unidade_espacial="município",
                                     unidade_medida="poços", periodo={"inicio": "atual", "fim": "atual"}, dados_ate="2026-08-09", avisos=[],
                                     colunas=[{"nome": "aquifero", "tipo": "texto"}, {"nome": "pocos", "tipo": "int"}], dados=poc_l,
                                     extras={"fonte_rodape": FONTES["pocos"], "telas": ["territorio"], "resumo": poc_r}),
         [E["pocos"]], "contagem por aquífero (vazio e classes raras = Outros/sem dado); profundidade média, mediana e % sem dado"),
        (n["app"], ["TB15"], dict(titulo="APP hídrica: área por curso d'água", fonte_curta="ANA/BHO + MapBiomas", fonte="APP calculada da rede ANA/BHO, ocupação pelo MapBiomas 2024",
                                   unidade_espacial="município", unidade_medida="km²", periodo={"inicio": "2024", "fim": "2024"}, dados_ate="2024",
                                   avisos=[{"tipo": "metodo", "texto": "aproximação metodológica; não serve para fiscalização ou licenciamento"}],
                                   colunas=[{"nome": "curso", "tipo": "texto"}, {"nome": "rotulo", "tipo": "texto"}, {"nome": "area_km2", "tipo": "float"}], dados=app_l,
                                   extras={"fonte_rodape": FONTES["app"], "telas": ["territorio"], "resumo": app_r}),
         [E["app"], E["app_ocup"]], "áreas por curso e ocupação lidas dos metadados de app_hidrica.py"),
        (n["urb"], ["TB16"], dict(titulo="Áreas urbanizadas por tipo e densidade (2019)", fonte_curta="IBGE", fonte="IBGE, Áreas Urbanizadas do Brasil 2019",
                                   unidade_espacial="município", unidade_medida="km²", periodo={"inicio": "2019", "fim": "2019"}, dados_ate="2019",
                                   avisos=[{"tipo": "metodo", "texto": "produto gerado; ainda sem bloco na tela"}],
                                   colunas=[{"nome": "tipo", "tipo": "texto"}, {"nome": "densidade", "tipo": "texto"}, {"nome": "area_km2", "tipo": "float"}], dados=urbanizadas(),
                                   extras={"fonte_rodape": FONTES["urb"], "telas": []}),
         [E["urbanizadas"]], "soma das áreas (km², EPSG:31981) por tipo e densidade"),
    ]
    out = {}
    for pid, ativos, kw, entradas, transf in defs:
        if so and so != pid:
            continue
        p = comum.montar_produto(id=pid, ativos=ativos, **c, **kw)
        out[pid] = comum.gravar_produto(p, transformacao=transf, entradas=entradas, forcar=forcar)
    return out


def _produtos() -> list[str]:
    try:
        return list(ids().values())
    except (OSError, ValueError):
        return []


PRODUTOS = _produtos()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--codigo-ibge", default="4322400")
    ap.add_argument("--forcar", action="store_true")
    ap.add_argument("--so")
    a = ap.parse_args()
    try:
        for pid, g in gerar(a.codigo_ibge, a.forcar, a.so).items():
            print(f"{pid}: {'gravado' if g else 'sem mudança'}")
    except (OSError, ValueError, KeyError) as erro:
        print(f"erro: {erro}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
