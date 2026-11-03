"""
Produtos da tela de riscos, desastres e infraestrutura (T6) em data/paineis/:

    reconhecimentos federais  data/processed/desastres_s2id_2013-2026_municipal.csv (+ metadado)
    suscetibilidade CPRM      resumo por classe no metadado data/raw/vetor/risco-geologico_cprm_2021_vetorial.json
    continuidade de energia   data/raw/continuidade-energia_aneel_2000-2026_conjunto-consumidor.csv
    setorização de risco SGB  data/raw/vetor/setorizacao-risco_sgb_atual_vetorial.geojson (2014)
    áreas de risco IBGE 2018  data/raw/vetor/populacao-areas-de-risco_ibge_2018_bater.zip (só as áreas; os totais de população
                              publicados ficam fora porque a transcrição do documento está pendente de conferência)
    indisponíveis             data/raw/saneamento_snis_indisponivel.json, data/raw/malha-viaria_daer_indisponivel.json

Regras: ano do reconhecimento = ano do decreto (sem decreto, ano do D.O.U.); DEC e
FEC anuais = soma dos meses da média ponderada pelo número de unidades
consumidoras dos conjuntos do município; ano com menos de 12 meses é parcial e fica
fora dos gráficos. Contagens de pessoas, domicílios e edificações por setor de risco
passam pela supressão de comum.py. Textos e indicadores em "notas"/"kpis_*".
CRS não se aplica (nenhuma geometria é publicada).

Uso:
    python scripts/paineis/dados_riscos.py [--codigo-ibge 4322400] [--forcar] [--so <id>]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import comum  # noqa: E402
from comum import br  # noqa: E402

RAIZ = comum.RAIZ
SCRIPT = "scripts/paineis/dados_riscos.py"


def _recente(pasta: Path, padrao: str) -> Path:
    """Bruto de coleta mais recente (comum.bruto_mais_recente); sem nenhum, o caminho do padrão (falha na leitura)."""
    try:
        return comum.bruto_mais_recente(pasta, padrao)
    except ValueError:
        return pasta / padrao


_S2ID = _recente(RAIZ / "data/processed", "desastres_s2id_*_municipal.csv")
E = {
    "s2id": _S2ID,
    "s2id_meta": _S2ID.with_suffix(".json"),
    "cprm": RAIZ / "data/raw/vetor/risco-geologico_cprm_2021_vetorial.json",
    "aneel": _recente(RAIZ / "data/raw", "continuidade-energia_aneel_*_conjunto-consumidor.csv"),
    "setor": RAIZ / "data/raw/vetor/setorizacao-risco_sgb_atual_vetorial.geojson",
    "bater": RAIZ / "data/raw/vetor/populacao-areas-de-risco_ibge_2018_bater.zip",
    "snis": RAIZ / "data/raw/saneamento_snis_indisponivel.json",
    "daer": RAIZ / "data/raw/malha-viaria_daer_indisponivel.json",
}
CATEGORIAS = ["Inundação", "Estiagem", "Tempestade local/convectiva", "Doença infecciosa viral", "Enxurrada"]
CAT_FONTE = {"INUNDAÇÕES": "Inundação", "ESTIAGEM": "Estiagem", "DOENÇAS INFECCIOSAS VIRAIS": "Doença infecciosa viral", "ENXURRADAS": "Enxurrada"}
ORDEM_CLASSE = ["Baixa", "Média", "Alta"]
NOTA_SUPRESSAO = "séries municipais oficiais sem supressão; contagens por setor de risco passam pela supressão de comum.py (limiar 5)"
# texto que não pôde ser regerado a partir dos dados: os conjuntos antigos aparecem em 2011–2018 na fonte
TRANSCRITO_ANEEL = "a década 2010–2019 tinha nomes diferentes dos atuais \"Uruguaiana 1\"/\"Uruguaiana 7\""
FONTES = {
    "s2id": "Desastres — S2ID (Sistema Integrado de Informações sobre Desastres), SEDEC-MIDR",
    "cprm": "Risco geológico — CPRM/SGB, Carta de Suscetibilidade a Movimentos Gravitacionais de Massa e Inundações (repositório RIGEO)",
    "aneel": "Continuidade de energia — ANEEL, indicadores DEC e FEC por conjunto consumidor",
    "setor": "Setorização de risco — SGB, 2014",
    "bater": "Áreas de risco — IBGE, População em áreas de risco no Brasil (2018), base BATER",
    "indisp": "Saneamento (SNIS) e malha estadual (DAER-RS) — indisponíveis para download programático",
}
LEITURA = ("**Leitura recomendada:** a classificação de suscetibilidade (risco geológico) é um produto de modelagem validado por campo, "
           "não medição direta — use como triagem territorial, não como laudo pontual. As camadas de meio físico do BDiA "
           "(geologia/geomorfologia/pedologia/vegetação) são leitura regional (1:250.000), não decisão em nível de microárea; a APP "
           "hídrica é uma aproximação exploratória, não uma delimitação técnica para fiscalização ou licenciamento.")


def _j(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def s2id():
    s = pd.read_csv(E["s2id"])
    ano = pd.to_datetime(s["Data do Decreto"], errors="coerce").dt.year
    ano = ano.fillna(pd.to_datetime(s["Data do D.O.U."], dayfirst=True, errors="coerce").dt.year).astype(int)
    cat = s["Desastre"].map(lambda d: "Tempestade local/convectiva" if d.startswith("TEMPESTADE LOCAL") else CAT_FONTE[d])
    anos = list(range(int(ano.min()), int(ano.max()) + 1))
    linhas = [{"ano": a, **{c: int(((ano == a) & (cat == c)).sum()) for c in CATEGORIAS}} for a in anos]
    ecp = s["SE/ECP"] == "ECP"
    extra = {"n_ecp": int(ecp.sum()), "anos_ecp": sorted(set(int(x) for x in ano[ecp])), "categorias_ecp": sorted(set(cat[ecp])),
             "inicio_fonte": int(_j(E["s2id_meta"])["periodo_coberto"][:4])}
    return linhas, extra


def cprm():
    m = _j(E["cprm"])["camadas"]
    linhas = []
    for camada, rot in (("inundacao", "Inundação"), ("movimento_de_massa", "Movimento de massa")):
        for cl in ORDEM_CLASSE:
            if cl in m[camada]["area_km2_por_classe"]:
                linhas.append({"camada": rot, "classe": cl, "area_km2": m[camada]["area_km2_por_classe"][cl], "n_poligonos": m[camada]["n_feicoes_por_classe"][cl]})
    totais = {"inundacao_km2": m["inundacao"]["area_km2_total"], "movimento_km2": m["movimento_de_massa"]["area_km2_total"]}
    return linhas, totais


def aneel():
    e = pd.read_csv(E["aneel"])
    w = e.pivot_table(index=["ano", "mes", "conjunto"], columns="indicador", values="valor", aggfunc="first").reset_index().dropna(subset=["DEC", "FEC", "NumCon"])
    w["dw"], w["fw"] = w.DEC * w.NumCon, w.FEC * w.NumCon
    mm = w.groupby(["ano", "mes"]).agg(dw=("dw", "sum"), fw=("fw", "sum"), n=("NumCon", "sum"))
    mm["dec"], mm["fec"] = mm.dw / mm.n, mm.fw / mm.n
    yy = mm.groupby("ano").agg(dec=("dec", "sum"), fec=("fec", "sum"), meses=("dec", "size"))
    linhas = [{"ano": int(a), "dec_horas": round(float(r.dec), 2), "fec_interrupcoes": round(float(r.fec), 2), "meses": int(r.meses), "parcial": int(r.meses) < 12}
              for a, r in yy.iterrows()]
    return linhas, str(e.distribuidora.unique()[0])


def setorizacao():
    g = gpd.read_file(E["setor"])
    ano = int(pd.to_datetime(g["data_setor"].iloc[0], unit="ms").year)
    linhas = [{"setor": r.num_setor, "local": r.local, "tipologia": r.tipolo_g1, "grau_risco": r.grau_risco, "grau_vulnerabilidade": r.grau_vulne,
               "edificacoes": int(r.num_edif), "domicilios": int(r.num_domi), "pessoas": int(r.num_pess)} for r in g.sort_values("num_setor").itertuples()]
    return linhas, ano


def bater(codigo_ibge):
    b = gpd.read_file(f"zip://{E['bater']}")
    b = b[b.GEO_MUN.astype(str) == codigo_ibge]
    return [{"area_bater": str(r.GEO_BATER), "origem": None if pd.isna(r.ORIGEM) else r.ORIGEM, "observacao": None if pd.isna(r.OBS) else r.OBS}
            for r in b.sort_values("GEO_BATER").itertuples()]


def indisponiveis():
    # "motivo" é o campo "resultado" registrado na coleta (metadado de indisponibilidade)
    return [{"tema": "saneamento", "fonte": _j(E["snis"])["fonte"], "situacao": "não baixado: sem download programático sem login gov.br",
             "motivo": _j(E["snis"])["resultado"]},
            {"tema": "malha estadual", "fonte": _j(E["daer"])["fonte"], "situacao": "não baixado: sem download vetorial programático",
             "motivo": _j(E["daer"])["resultado"]}]


def notas_kpis(s_l, s_x, c_l, c_t, a_l, distrib):
    anos = [r["ano"] for r in s_l]
    tot = {c: sum(r[c] for r in s_l) for c in CATEGORIAS}
    total = sum(tot.values())
    freq = sorted(CATEGORIAS, key=lambda c: -tot[c])
    sem = [r["ano"] for r in s_l if sum(r[c] for c in CATEGORIAS) == 0]
    com = len(anos) - len(sem)
    fech = [r for r in a_l if not r["parcial"]]
    parc = [r for r in a_l if r["parcial"]]
    d0, d1 = fech[0], fech[-1]
    var = 100 * (d1["dec_horas"] - d0["dec_horas"]) / d0["dec_horas"]
    ecp_ano = s_x["anos_ecp"][0]
    notas = {
        "ris_meta_masthead": f"Risco geológico 2021 · Desastres (S2ID) {anos[0]}–{anos[-1]}",
        "ris_nota_risco": ("Suscetibilidade mapeada (CPRM/SGB, validada em campo em 2021) e histórico de desastres reconhecidos oficialmente pela "
                           "União (S2ID/SEDEC-MIDR). Barras de suscetibilidade por **área**, não por número de polígonos — um único polígono grande "
                           "pesa mais que muitos pequenos, e usar contagem aqui contaria a história errada (ver tabela para os dois números)."),
        "ris_meta_inund": f"{round(c_t['inundacao_km2'])} km² mapeados",  # sem separador de milhar, como no painel anterior
        "ris_meta_mov": f"{round(c_t['movimento_km2'])} km² mapeados",
        "ris_nota_s2id": (f"Reconhecimentos federais de Situação de Emergência (SE) ou Estado de Calamidade Pública (ECP) — série {anos[0]}–{anos[-1]} "
                          f"(nenhum decreto em {s_x['inicio_fonte']} na fonte para o município), levantada no Sistema Integrado de Informações sobre "
                          "Desastres (S2ID)."),
        "ris_meta_s2id": f"S2ID · {anos[0]}–{anos[-1]} · {total} reconhecimentos",
        "ris_nota_s2id_2": (f"{com} dos {len(anos)} anos da série tiveram pelo menos 1 reconhecimento (só {'/'.join(map(str, sem))} sem registro). "
                            f"{freq[0]} é o tipo mais frequente ({tot[freq[0]]} de {total}, {br(100 * tot[freq[0]] / total)}%), seguido de "
                            f"{freq[1].lower()} ({tot[freq[1]]}). As {s_x['n_ecp']} doenças infecciosas virais ({ecp_ano}) são Estado de Calamidade "
                            f"Pública (ECP, situação mais grave) — os demais {total - s_x['n_ecp']} reconhecimentos são Situação de Emergência (SE); "
                            f"ambas as declarações de {ecp_ano} referem-se à pandemia de covid-19. Sem geometria própria por evento na fonte (é "
                            "reconhecimento administrativo por decreto/portaria, não mapeamento de área afetada) — por isso fica como indicador de "
                            "contexto, não camada no geoportal."),
        "ris_resumo_risco": "Ver risco geológico em tabela (área e nº de polígonos)",
        "ris_nota_aneel": (f"Indicadores coletivos de continuidade (DEC — duração, FEC — frequência) da distribuidora {distrib}, agregados por média "
                           "ponderada pelo número de unidades consumidoras entre os \"conjuntos\" que atendem o município (a fonte reorganizou essa "
                           "divisão interna ao longo do tempo — ver nota abaixo) — sem geometria publicada pela ANEEL, por isso fica como indicador "
                           "de contexto, não camada no geoportal."),
        "ris_meta_dec": "acumulado anual · horas",
        "ris_meta_fec": "acumulado anual · nº de interrupções",
        "ris_nota_aneel_2": (f"{parc[0]['ano']} (parcial, só até {['', 'janeiro', 'fevereiro', 'março', 'abril', 'maio', 'junho', 'julho', 'agosto', 'setembro', 'outubro', 'novembro'][parc[0]['meses']]}) "
                             f"fica fora do gráfico pelo mesmo motivo do SICONFI acima — DEC de {br(parc[0]['dec_horas'], 2)}h/FEC de "
                             f"{br(parc[0]['fec_interrupcoes'], 2)} acumulados em {parc[0]['meses']} meses pareceriam uma melhora dramática frente a um "
                             "ano fechado, mas é só metade do período. A quebra de comparabilidade real na divisão interna de \"conjuntos\" ao longo "
                             f"do tempo ({TRANSCRITO_ANEEL}) não afeta este agregado — ele soma todas as unidades consumidoras do município em cada "
                             "mês, independente de como a distribuidora dividiu internamente naquele período."),
        "ris_leitura": LEITURA,
    }
    kpis_s2id = [
        {"rotulo": "Reconhecimentos federais", "valor": br(total), "unidade": "", "sub": f"{anos[0]}–{anos[-1]} · Situação de Emergência ou Calamidade"},
        {"rotulo": "Tipo mais frequente", "valor": freq[0], "unidade": "", "sub": f"{br(tot[freq[0]])} de {br(total)} ({br(100 * tot[freq[0]] / total, 1, 0)}%)"},
        {"rotulo": "Anos com reconhecimento", "valor": f"{com} de {len(anos)}", "unidade": "", "sub": f"só {'/'.join(map(str, sem))} sem registro na série"},
        {"rotulo": "Estado de Calamidade Pública (ECP)", "valor": br(s_x["n_ecp"]), "unidade": "",
         "sub": f"{ecp_ano} · pandemia de covid-19 · os demais são Situação de Emergência"},
    ]
    kpis_aneel = [
        {"rotulo": "DEC — duração de interrupção", "valor": br(d1["dec_horas"], 1, 0) + " h", "unidade": "", "sub": f"{d1['ano']} · acumulado no ano"},
        {"rotulo": "FEC — frequência de interrupção", "valor": br(d1["fec_interrupcoes"], 1, 0), "unidade": "", "sub": f"{d1['ano']} · interrupções por unidade consumidora"},
        {"rotulo": f"DEC — variação desde {d0['ano']}", "valor": br(var, 1, 0) + "%", "unidade": "",
         "sub": f"{d0['ano']} ({br(d0['dec_horas'], 1, 0)}h) → {d1['ano']} ({br(d1['dec_horas'], 1, 0)}h)"},
        {"rotulo": "Distribuidora", "valor": distrib, "unidade": "", "sub": "grupo CPFL · única distribuidora do município"},
    ]
    return notas, kpis_s2id, kpis_aneel


def ids(codigo_ibge: str = "4322400") -> dict[str, str]:
    """Ids estáveis (convenção de comum.py). CPRM 2021, setorização 2014 e BATER 2018 são edições fixas da fonte."""
    return {"s2id": "desastres_s2id_serie_anual", "cprm": "risco-geologico_cprm_2021_classe",
            "aneel": "energia_aneel_serie_anual", "setor": "setorizacao-risco_sgb_2014_setor",
            "bater": "areas-de-risco_ibge_2018_bater", "indisp": "infraestrutura_indisponivel_atual"}


def gerar(codigo_ibge: str = "4322400", forcar: bool = False, so: str | None = None) -> dict[str, bool]:
    s_l, s_x = s2id()
    c_l, c_t = cprm()
    a_l, distrib = aneel()
    st_l, ano_setor = setorizacao()
    bat_l = bater(codigo_ibge)
    notas, kpis_s2id, kpis_aneel = notas_kpis(s_l, s_x, c_l, c_t, a_l, distrib)
    n = ids(codigo_ibge)
    c = dict(codigo_ibge=codigo_ibge, script=SCRIPT, selo="conferido")
    col = lambda linhas: [{"nome": k, "tipo": "texto" if isinstance(v, str) or v is None else ("bool" if isinstance(v, bool) else ("int" if isinstance(v, int) else "float"))}
                          for k, v in linhas[0].items()]
    ext = lambda f, **kw: {"fonte_rodape": FONTES[f], "telas": ["riscos"], "supressao_nota": NOTA_SUPRESSAO, **kw}
    defs = [
        (n["s2id"], ["RD01"], dict(titulo="Reconhecimentos federais de desastre por ano e tipo", fonte_curta="S2ID", fonte="S2ID, SEDEC-MIDR (relatório gerencial de reconhecimentos)",
                                    unidade_espacial="município", unidade_medida="reconhecimentos", periodo={"inicio": str(s_l[0]["ano"]), "fim": str(s_l[-1]["ano"])},
                                    dados_ate=str(s_l[-1]["ano"]), avisos=[{"tipo": "metodo", "texto": "ano do decreto; sem decreto, ano do D.O.U."}],
                                    colunas=col(s_l), dados=s_l, extras=ext("s2id", categorias=CATEGORIAS, notas=notas, kpis_s2id=kpis_s2id, **s_x)),
         [E["s2id"], E["s2id_meta"]], "contagem de reconhecimentos por ano e categoria (tipologia agrupada)"),
        (n["cprm"], ["RD02"], dict(titulo="Suscetibilidade a inundação e movimento de massa: área e polígonos por classe", fonte_curta="CPRM/SGB",
                                    fonte="CPRM/SGB, carta de suscetibilidade (RIGEO), validação de campo em 2021", unidade_espacial="município", unidade_medida="km²; polígonos",
                                    periodo={"inicio": "2021", "fim": "2021"}, dados_ate="2021", avisos=[{"tipo": "metodo", "texto": "modelagem validada em campo; triagem territorial, não laudo"}],
                                    colunas=col(c_l), dados=c_l, extras=ext("cprm", totais=c_t)),
         [E["cprm"]], "área e número de polígonos por classe, do metadado de risco_geologico_cprm.py"),
        (n["aneel"], ["IF01"], dict(titulo="DEC e FEC anuais do município", fonte_curta="ANEEL", fonte=f"ANEEL, indicadores de continuidade da distribuidora {distrib}",
                                     unidade_espacial="município (conjuntos consumidores)", unidade_medida="horas; interrupções", periodo={"inicio": str(a_l[0]["ano"]), "fim": str(a_l[-1]["ano"])},
                                     dados_ate=str(a_l[-1]["ano"]), avisos=[{"tipo": "parcial", "texto": "ano com menos de 12 meses é parcial e fica fora dos gráficos"},
                                             {"tipo": "metodo", "texto": "valor transcrito do painel anterior; fonte de origem ainda não regerada por script: " + TRANSCRITO_ANEEL}],
                                     colunas=col(a_l), dados=a_l, extras=ext("aneel", kpis_aneel=kpis_aneel, distribuidora=distrib)),
         [E["aneel"]], "por mês, média de DEC e FEC ponderada por NumCon dos conjuntos; soma dos meses no ano"),
        (n["setor"], ["RD03"], dict(titulo=f"Setores de risco do SGB ({ano_setor})", fonte_curta="SGB", fonte="SGB, setorização de risco (camada pública)", unidade_espacial="setor de risco",
                                     unidade_medida="edificações; domicílios; pessoas", periodo={"inicio": str(ano_setor), "fim": str(ano_setor)}, dados_ate=str(ano_setor),
                                     avisos=[{"tipo": "metodo", "texto": "contagens do levantamento de campo do SGB por setor; valores de 1 a 4 aparecem como <5"}],
                                     colunas=col(st_l), dados=st_l, campos_contagem=["edificacoes", "domicilios", "pessoas"],
                                     extras=ext("setor", notas={
                                         "ris_meta_setor": f"SGB · {ano_setor} · {len(st_l)} setores",
                                         "ris_nota_setor": (f"Setores de risco mapeados pelo SGB em {ano_setor} na área urbana, com tipologia, grau de risco e de "
                                                            "vulnerabilidade e as contagens de edificações, domicílios e pessoas do levantamento. Contagens de 1 "
                                                            "a 4 aparecem como \"<5\"."),
                                         "ris_nota_bater": (f"Áreas de risco da base territorial do IBGE (BATER, 2018) no município: {len(bat_l)} áreas. Os totais de "
                                                            "população publicados no documento de origem estão em conferência e não aparecem aqui."),
                                         "ris_nota_indisp": ("Saneamento (SNIS) e malha rodoviária estadual (DAER-RS) não puderam ser baixados por script; o motivo "
                                                             "registrado na coleta aparece abaixo.")})),
         [E["setor"]], "atributos por setor, sem geometria; supressão de comum.py nas contagens"),
        (n["bater"], ["RD04"], dict(titulo="Áreas de risco do IBGE (BATER 2018) no município", fonte_curta="IBGE", fonte="IBGE, População em áreas de risco no Brasil (2018), base BATER",
                                     unidade_espacial="área de risco", unidade_medida="áreas", periodo={"inicio": "2018", "fim": "2018"}, dados_ate="2018",
                                     avisos=[{"tipo": "em_confirmacao", "texto": "só a lista das áreas; os totais de população publicados no documento do IBGE ficam fora até a conferência da transcrição"}],
                                     colunas=[{"nome": "area_bater", "tipo": "texto"}, {"nome": "origem", "tipo": "texto"}, {"nome": "observacao", "tipo": "texto"}],
                                     dados=bat_l, extras=ext("bater")),
         [E["bater"]], "áreas BATER do município (sem geometria)"),
        (n["indisp"], ["IF09", "IF10"], dict(titulo="Saneamento e malha estadual: fontes indisponíveis", fonte_curta="SNIS; DAER-RS", fonte="SNIS e DAER-RS (situação de acesso)",
                                              unidade_espacial="município", unidade_medida="—", periodo={"inicio": "atual", "fim": "atual"}, dados_ate="2026-08-12",
                                              avisos=[{"tipo": "lacuna", "texto": "dados não disponíveis para download programático"}],
                                              colunas=[{"nome": "tema", "tipo": "texto"}, {"nome": "fonte", "tipo": "texto"}, {"nome": "situacao", "tipo": "texto"},
                                                       {"nome": "motivo", "tipo": "texto"}],
                                              dados=indisponiveis(), extras=ext("indisp")),
         [E["snis"], E["daer"]], "situação registrada nos metadados de indisponibilidade"),
    ]
    out = {}
    for pid, ativos, kw, ent, transf in defs:
        if so and so != pid:
            continue
        p = comum.montar_produto(id=pid, ativos=ativos, **c, **kw)
        out[pid] = comum.gravar_produto(p, transformacao=transf, entradas=ent, forcar=forcar, extras_metadado={"supressao_nota": NOTA_SUPRESSAO})
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
