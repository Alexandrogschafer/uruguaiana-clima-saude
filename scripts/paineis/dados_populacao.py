"""
Produtos da tela de população em data/paineis/ (esquema climapampa-painel/1),
a partir dos mesmos arquivos que alimentavam o painel de demografia:

    - data/processed/populacao-serie-temporal_ibge-sidra_<periodo>_municipal.csv
      (demografia_ibge_sidra.py: censos + estimativas anuais)
    - data/processed/demografia-indicadores_ibge-sidra_<periodo>_municipal.csv
      (processamento/demografia_indicadores.py)
    - data/raw/populacao_ibge-sidra-tabela200_1970-2010_municipal.csv e
      data/raw/populacao-sexo-idade_ibge-sidra-tabela9514_2022_municipal.csv
      (pirâmides de 2000, 2010 e 2022)

Grava:
    populacao_ibge_serie_anual                 população total, censo ou estimativa
    demografia_ibge_serie_indicadores         crescimento, envelhecimento, dependência,
                                               urbano e rural, crianças 0-4, idosos 60+
    demografia_ibge_censos_piramides           pirâmides etárias dos 3 censos com 80+ aberto (ativo PD14:
                                               pirâmides etárias oficiais do IBGE, SIDRA 200 e 9514; o PD03, tabela
                                               do estudo de dinâmica populacional, segue pendente e fora das telas)

Os textos das notas e dos indicadores vão no campo "notas"/"kpis" do produto,
com os números calculados aqui a partir dos dados (nenhum número no HTML).
Séries municipais oficiais: sem supressão (nota nos metadados). Na tabela 200
do SIDRA, célula vazia é o "-" do IBGE (zero): o total de cada grupo confere com
a soma de homens e mulheres. CRS não se aplica (séries tabulares).

Uso:
    python scripts/paineis/dados_populacao.py [--codigo-ibge 4322400] [--forcar] [--so <id>]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import comum  # noqa: E402
from comum import br  # noqa: E402

RAIZ = comum.RAIZ
SCRIPT = "scripts/paineis/dados_populacao.py"
ANOS_PIRAMIDE = [2000, 2010, 2022]
# nome legível das colunas que viram série de gráfico com legenda (campo "rotulo" de colunas)
ROTULOS_SERIE = {"pct_urbano": "Urbana", "pct_rural": "Rural"}
INCONSISTENCIAS: list[str] = []
NOTA_SUPRESSAO = ("supressão primária não aplicada: séries municipais oficiais do IBGE, já publicadas pela fonte; "
                  "nenhum agregado abaixo do município")
FONTE_RODAPE = ("IBGE — API SIDRA/Agregados, tabelas 200 (Censos 1970-2010, sexo × situação do domicílio × grupo de idade), "
                "9514 (Censo 2022, sexo × idade), 202 (situação do domicílio 1970-2010), 9923 (situação do domicílio 2022) e "
                "6579 (estimativas populacionais anuais). A tabela 6706 (\"pirâmide etária\" nominal do SIDRA) é da PNAD Contínua, "
                "uma pesquisa amostral sem representatividade em nível de município pequeno — testada e confirmada sem dado para "
                "Uruguaiana em nenhum período; por isso a pirâmide etária aqui vem das tabelas de Censo (200 e 9514), que têm os "
                "mesmos 21 grupos etários de 5 anos nos 3 pontos comparados")


def _um(pasta: Path, padrao: str) -> Path:
    """Bruto de coleta mais recente entre os que casam com o padrão (comum.bruto_mais_recente)."""
    return comum.bruto_mais_recente(pasta, padrao)


def entradas() -> dict[str, Path]:
    return {
        "serie": _um(RAIZ / "data/processed", "populacao-serie-temporal_ibge-sidra_*_municipal.csv"),
        "indicadores": _um(RAIZ / "data/processed", "demografia-indicadores_ibge-sidra_*_municipal.csv"),
        "t200": _um(RAIZ / "data/raw", "populacao_ibge-sidra-tabela200_*_municipal.csv"),
        "t9514": _um(RAIZ / "data/raw", "populacao-sexo-idade_ibge-sidra-tabela9514_*_municipal.csv"),
    }


def calcular(e: dict) -> tuple[list, list, list]:
    s = pd.read_csv(e["serie"])
    serie = [{"ano": int(r.ano), "populacao": float(r.populacao_total), "fonte": r.fonte} for r in s.itertuples()]
    i = pd.read_csv(e["indicadores"])
    ind = []
    for r in i.itertuples():
        tc = r.taxa_crescimento_geometrico_anual_pct
        ind.append({"periodo": int(r.periodo), "populacao_total": float(r.populacao_total),
                    "taxa_crescimento": None if pd.isna(tc) else float(tc),
                    "indice_envelhecimento": float(r.indice_envelhecimento), "razao_dependencia": float(r.razao_dependencia),
                    "pct_urbano": float(r.pct_urbano), "pct_rural": float(r.pct_rural),
                    "pop_0_4": float(r.pop_0_4_anos), "pct_pop_0_4": float(r.pct_pop_0_4_anos),
                    "pop_60_mais": float(r.pop_60_anos_ou_mais), "pct_pop_60_mais": float(r.pct_pop_60_anos_ou_mais)})
    t200, t9514 = pd.read_csv(e["t200"]), pd.read_csv(e["t9514"])
    grupos = t9514[(t9514.sexo == "Homens")]["grupo_idade"].tolist()
    # ordem crescente de idade (0 a 4, 5 a 9, ..., 100 ou mais)
    grupos = sorted({g for g in grupos if g.split()[0].isdigit()}, key=lambda g: int(g.split()[0]))
    pir = []
    INCONSISTENCIAS.clear()
    for ano in ANOS_PIRAMIDE:
        d = t9514[t9514.periodo == ano] if ano == 2022 else t200[(t200.periodo == ano) & (t200.situacao_domicilio == "Total")]
        if d.empty:
            raise ValueError(f"sem dados de pirâmide para {ano}")
        for g in grupos:
            h = d[(d.sexo == "Homens") & (d.grupo_idade == g)].populacao
            m = d[(d.sexo == "Mulheres") & (d.grupo_idade == g)].populacao
            t = d[(d.sexo == "Total") & (d.grupo_idade == g)].populacao if "Total" in set(d.sexo) else None
            hv, mv = float(h.fillna(0).sum()), float(m.fillna(0).sum())
            if t is not None and len(t) and not pd.isna(t.iloc[0]) and abs(t.iloc[0] - hv - mv) > 0.5:
                # a própria tabela do SIDRA tem diferenças pequenas entre o total e homens + mulheres;
                # a pirâmide usa homens e mulheres, e a diferença fica registrada no metadado
                INCONSISTENCIAS.append(f"{ano}, {g}: total {int(t.iloc[0])}, homens + mulheres {int(hv + mv)}")
            pir.append({"ano": ano, "grupo_idade": g, "homens": hv, "mulheres": mv})
    return serie, ind, pir


def notas_e_kpis(serie, ind) -> tuple[dict, list]:
    censos = [r["ano"] for r in serie if r["fonte"] == "censo"]
    est = [r["ano"] for r in serie if r["fonte"] == "estimativa"]
    e0, e1 = min(est), max(est)
    excecoes = [a for a in range(e0, e1 + 1) if a not in est]
    n = len(censos)
    antes = [c for c in censos if c < e0]
    i22 = ind[-1]
    sinal = "população vem caindo" if (i22["taxa_crescimento"] or 0) < 0 else "população vem crescendo"
    notas = {
        "pop_apresentacao": (f"População, estrutura etária, urbanização e indicadores de envelhecimento e dependência — série municipal "
                             f"dos {n} Censos Demográficos ({censos[0]} a {censos[-1]}) via API SIDRA/IBGE, com estimativas anuais "
                             f"preenchendo o intervalo entre {e0} e {e1}."),
        "pop_meta_censos": " · ".join(map(str, censos)),
        "pop_meta_estimativas": f"{e0}–{e1} (exceto {'/'.join(map(str, excecoes))})",
        "pop_nota_serie": (f"Marcadores maiores = ano de Censo (contagem exata); linha fina entre {e0} e {e1} = estimativas anuais do IBGE. "
                           f"Entre {censos[0]} e {antes[-1]} só existem os {len(antes) - 1} pontos de Censo — não há estimativa anual "
                           f"publicada nesse trecho, então a linha reta entre eles é uma referência visual, não uma interpolação real."),
        "pop_meta_serie": f"{serie[0]['ano']}–{serie[-1]['ano']}",
        "pop_nota_piramide": (f"{', '.join(map(str, ANOS_PIRAMIDE[:-1]))} e {ANOS_PIRAMIDE[-1]} — os únicos {len(ANOS_PIRAMIDE)} censos com "
                              f"abertura fina da faixa 80+ (necessária para uma pirâmide com o mesmo nível de detalhe nos "
                              f"{len(ANOS_PIRAMIDE)} pontos). Mesma escala horizontal nos {len(ANOS_PIRAMIDE)} gráficos, para comparação "
                              f"direta do formato: da pirâmide de base larga em {ANOS_PIRAMIDE[0]} ao formato já quase retangular "
                              f"(envelhecido) em {ANOS_PIRAMIDE[-1]}."),
        "pop_nota_envelhecimento": ("Índice de envelhecimento = (60 anos ou mais / 0-14 anos) × 100. Razão de dependência = "
                                    "((0-14 anos + 65 anos ou mais) / 15-64 anos) × 100 — quanto da população em idade não-produtiva "
                                    "(crianças + idosos) cada 100 pessoas em idade produtiva sustentam. "
                                    f"Os {n} pontos são todos de ano de Censo; eixo horizontal em escala real de tempo (os intervalos "
                                    "entre censos não são iguais)."),
        "pop_meta_envelhecimento": "60+ / 0-14 × 100",
        "pop_meta_dependencia": "(0-14 + 65+) / 15-64 × 100",
        "pop_nota_urbano": (f"% da população total em cada situação de domicílio, nos {n} censos. {censos[0]}–{censos[-2]}: SIDRA tabela 202. "
                            f"{censos[-1]}: tabela 9923 (202 não tem versão {censos[-1]})."),
        "pop_nota_vulneravel": ("Crianças de 0-4 anos e idosos de 60 anos ou mais — grupos etários de maior atenção em eventos climáticos "
                                f"extremos (calor, enchente, estiagem). Absoluto e % da população total, nos {n} censos."),
        "pop_resumo_tabela": f"Ver todos os indicadores em tabela ({n} censos)",
        "pop_nota_agregacao": ("**Nível de agregação — por que municipal, não por setor:** a malha de setores censitários do IBGE muda de "
                               f"configuração a cada Censo (os limites de setor de {', '.join(map(str, ANOS_PIRAMIDE[:-1]))} e "
                               f"{ANOS_PIRAMIDE[-1]} não são o mesmo desenho geográfico) — comparar um setor específico ao longo do tempo "
                               "compara, na prática, áreas diferentes. Por isso toda a série temporal deste painel é agregada no nível "
                               f"**municipal**, o único nível estável para comparação entre os {n} censos. O detalhe espacial por setor "
                               f"censitário — só para o Censo {censos[-1]}, na malha atual — está no [Painel de Economia](economia.html) "
                               f"(seção \"Perfil censitário\") e no arquivo `data/raw/vulnerabilidade-censo_ibge_{censos[-1]}.csv` / "
                               "`setores-censitarios_vulnerabilidade_unido.gpkg`."),
        "pop_leitura": (f"**Leitura recomendada:** {censos[0]}-{censos[2]} e {'/'.join(map(str, ANOS_PIRAMIDE))} usam o mesmo cálculo de "
                        f"indicadores, mas {'/'.join(map(str, censos[:3]))} só têm a faixa 80+ agregada na fonte (não abrem "
                        "80-84/85-89/.../100+ como os censos mais recentes) — isso não muda os totais de \"60 anos ou mais\" usados aqui, "
                        f"só o nível de detalhe interno da faixa mais idosa. A queda populacional de {censos[-2]} para {censos[-1]} é uma "
                        "contagem censitária real, não um artefato de metodologia."),
    }
    kpis = [
        {"rotulo": f"População (Censo {i22['periodo']})", "valor": br(i22["populacao_total"], 1, 0), "unidade": "hab",
         "sub": f"{br(i22['pct_urbano'], 1, 0)}% urbana"},
        {"rotulo": f"Taxa de crescimento {ind[-2]['periodo']}–{i22['periodo']}",
         "valor": ("+" if i22["taxa_crescimento"] > 0 else "") + br(i22["taxa_crescimento"], 1, 0), "unidade": "%/ano",
         "sub": f"geométrica anual — {sinal}"},
        {"rotulo": "Índice de envelhecimento", "valor": br(i22["indice_envelhecimento"], 1, 0), "unidade": "",
         "sub": "60+ por 100 crianças de 0-14 anos"},
        {"rotulo": "% população urbana", "valor": br(i22["pct_urbano"], 1, 0), "unidade": "%", "sub": f"{br(i22['pct_rural'], 1, 0)}% rural"},
    ]
    return notas, kpis


def ids(codigo_ibge: str = "4322400") -> dict[str, str]:
    """Ids estáveis (convenção {tema}_{fonte}_{serie|atual}_{resolucao}, ver comum.py); o período fica no JSON."""
    return {"serie": "populacao_ibge_serie_anual", "indicadores": "demografia_ibge_serie_indicadores",
            "piramides": "demografia_ibge_censos_piramides"}


def gerar(codigo_ibge: str = "4322400", forcar: bool = False, so: str | None = None) -> dict[str, bool]:
    e = entradas()
    serie, ind, pir = calcular(e)
    nomes = ids(codigo_ibge)
    notas, kpis = notas_e_kpis(serie, ind)
    comuns = dict(codigo_ibge=codigo_ibge, unidade_espacial="município", selo="conferido", script=SCRIPT)
    out = {}
    if not so or so == nomes["serie"]:
        p = comum.montar_produto(
            id=nomes["serie"], ativos=["PD01"], titulo="População total: censos e estimativas anuais", fonte_curta="IBGE",
            fonte="IBGE, Censos Demográficos (SIDRA tabelas 200 e 9514) e estimativas anuais (tabela 6579)",
            unidade_medida="habitantes", periodo={"inicio": str(serie[0]["ano"]), "fim": str(serie[-1]["ano"])},
            dados_ate=f"{serie[-1]['ano']}", avisos=[{"tipo": "metodo", "texto": "anos sem estimativa anual publicada ficam fora da série"}],
            colunas=[{"nome": "ano", "tipo": "int"}, {"nome": "populacao", "tipo": "float"}, {"nome": "fonte", "tipo": "texto", "valores": ["censo", "estimativa"]}],
            dados=serie, extras={"fonte_rodape": FONTE_RODAPE, "telas": ["populacao"], "notas": notas, "kpis": kpis,
                                 "supressao_nota": NOTA_SUPRESSAO}, **comuns)
        out[p["id"]] = comum.gravar_produto(p, transformacao="série como gravada por demografia_ibge_sidra.py; textos e indicadores calculados dos dados",
                                            entradas=[e["serie"], e["indicadores"]], forcar=forcar, extras_metadado={"supressao_nota": NOTA_SUPRESSAO})
    if not so or so == nomes["indicadores"]:
        p = comum.montar_produto(
            id=nomes["indicadores"], ativos=["PD02"], titulo="Indicadores demográficos dos censos", fonte_curta="IBGE/SIDRA",
            fonte="IBGE, Censos Demográficos via SIDRA (indicadores calculados por processamento/demografia_indicadores.py)",
            unidade_medida="% ; índice; habitantes", periodo={"inicio": str(ind[0]["periodo"]), "fim": str(ind[-1]["periodo"])},
            dados_ate=f"{ind[-1]['periodo']}", avisos=[],
            colunas=[{"nome": k, "tipo": "float" if k != "periodo" else "int", **({"rotulo": ROTULOS_SERIE[k]} if k in ROTULOS_SERIE else {})} for k in ind[0]],
            dados=ind, extras={"fonte_rodape": FONTE_RODAPE, "telas": ["populacao"], "supressao_nota": NOTA_SUPRESSAO}, **comuns)
        out[p["id"]] = comum.gravar_produto(p, transformacao="indicadores como gravados por demografia_indicadores.py, colunas renomeadas",
                                            entradas=[e["indicadores"]], forcar=forcar, extras_metadado={"supressao_nota": NOTA_SUPRESSAO})
    if not so or so == nomes["piramides"]:
        p = comum.montar_produto(
            id=nomes["piramides"], ativos=["PD14"], titulo="Pirâmides etárias dos censos", fonte_curta="IBGE, Censo",
            fonte="IBGE, Censos Demográficos: SIDRA tabela 200 (2000 e 2010) e tabela 9514 (2022)", unidade_medida="habitantes",
            periodo={"inicio": str(ANOS_PIRAMIDE[0]), "fim": str(ANOS_PIRAMIDE[-1])}, dados_ate=str(ANOS_PIRAMIDE[-1]),
            avisos=[{"tipo": "metodo", "texto": "célula vazia na tabela 200 do SIDRA é o \"-\" do IBGE (zero); conferido contra o total do grupo"}],
            colunas=[{"nome": "ano", "tipo": "int"}, {"nome": "grupo_idade", "tipo": "texto"}, {"nome": "homens", "tipo": "float", "rotulo": "Homens"}, {"nome": "mulheres", "tipo": "float", "rotulo": "Mulheres"}],
            dados=pir, extras={"fonte_rodape": FONTE_RODAPE, "telas": ["populacao"], "supressao_nota": NOTA_SUPRESSAO}, **comuns)
        out[p["id"]] = comum.gravar_produto(p, transformacao="homens e mulheres por grupo de idade (situação Total na tabela 200)",
                                            entradas=[e["t200"], e["t9514"]], forcar=forcar,
                                            extras_metadado={"supressao_nota": NOTA_SUPRESSAO, "inconsistencias_da_fonte": list(INCONSISTENCIAS)})
    return out


PRODUTOS = list(ids().values())


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
