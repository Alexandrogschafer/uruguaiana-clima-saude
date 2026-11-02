"""
Produtos da tela de economia e vulnerabilidade (T5) em data/paineis/, a partir dos
mesmos arquivos que alimentavam o painel de território:

    perfil censitário  data/raw/vulnerabilidade-censo_ibge_2022.csv
    PIB e VAB          data/processed/pib-per-capita_ibge-sidra_*.csv, data/raw/pib-municipal_ibge-sidra-tabela5938_*.csv
    PAM e PPM          data/raw/producao-agricola_ibge-sidra-tabela5457_*.csv, data/raw/rebanho_ibge-sidra-tabela3939_*.csv
    CAGED e RAIS       data/raw/caged-movimentacao_mte_*.csv, data/raw/rais-vinculos_mte_*.csv
    IDHM               data/raw/idhm_atlas-brasil_*.csv
    CadÚnico e BF      data/raw/cadunico_mds-ri_*.csv, data/raw/bolsa-familia_mds-ri_atual_municipal.csv
    SICONFI            data/raw/despesas-por-funcao_siconfi_*.csv (+ metadado)

Textos de nota e indicadores vão em "notas" e "kpis_*", com números calculados aqui.
Séries municipais oficiais: sem supressão (nota nos metadados); a população por
situação do setor (urbana/rural) passa pela supressão de comum.py (agregado de setores).
Ano parcial (CAGED, SICONFI) fica marcado e fora dos gráficos, como antes.
CRS não se aplica (tabular).

Uso:
    python scripts/paineis/dados_economia.py [--codigo-ibge 4322400] [--forcar] [--so <id>]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import comum  # noqa: E402
from comum import br  # noqa: E402

RAIZ = comum.RAIZ
RAW, PROC = RAIZ / "data/raw", RAIZ / "data/processed"
SCRIPT = "scripts/paineis/dados_economia.py"
NOTA_SUPRESSAO = "supressão primária não aplicada: séries municipais oficiais, já publicadas pela fonte; nenhum agregado abaixo do município"
MES = ["", "jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]
MES_LONGO = ["", "janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"]
# seções da CNAE 2.0 (rótulo curto do painel)
CNAE = {"A": "Agropecuária", "B": "Ind. extrativa", "C": "Ind. transformação", "D": "Eletricidade/gás", "E": "Água/esgoto/resíduos",
        "F": "Construção", "G": "Comércio", "H": "Transporte", "I": "Alojamento/alimentação", "J": "Informação/comunicação",
        "K": "Ativ. financeiras", "L": "Ativ. imobiliárias", "M": "Ativ. profissionais/técnicas", "N": "Ativ. administrativas",
        "O": "Administração pública", "P": "Educação", "Q": "Saúde/ass. social", "R": "Artes/cultura", "S": "Outros serviços",
        "T": "Serviços domésticos", "U": "Organismos internacionais"}
RAIS_TOPO = 8
FAIXAS_IDHM = [(0.800, "muito alto", "0,800–1"), (0.700, "alto", "0,700–0,799"), (0.600, "médio", "0,600–0,699"), (0.500, "baixo", "0,500–0,599"), (0.0, "muito baixo", "0–0,499")]
FONTES = {
    "censo": "Censo — IBGE 2022 (Agregados por Setores Censitários + SIDRA)",
    "pib": "PIB dos Municípios — IBGE/SIDRA (tabela 5938), PIB per capita calculado localmente",
    "pam": "Produção agrícola e efetivo de rebanho — IBGE/SIDRA, PAM (tabela 5457) e PPM (tabela 3939)",
    "emprego": "Emprego formal — RAIS e Novo CAGED, Ministério do Trabalho e Emprego (microdados via FTP público)",
    "idhm": "IDHM — Atlas do Desenvolvimento Humano no Brasil (PNUD/Ipea/FJP)",
    "mds": "CadÚnico e Bolsa Família — Ministério do Desenvolvimento Social (MDS), Relatório de Informações (RIv3)",
    "siconfi": "Despesas por função — SICONFI, Tesouro Nacional (RREO-Anexo 02)",
}


def _um(pasta: Path, padrao: str) -> Path:
    a = sorted(pasta.glob(padrao))
    if len(a) != 1:
        raise ValueError(f"esperado 1 arquivo {padrao}, há {len(a)}")
    return a[0]


def entradas() -> dict[str, Path]:
    return {"censo": RAW / "vulnerabilidade-censo_ibge_2022.csv", "pib_pc": _um(PROC, "pib-per-capita_ibge-sidra_*_municipal.csv"),
            "pib": _um(RAW, "pib-municipal_ibge-sidra-tabela5938_*_municipal.csv"), "pam": _um(RAW, "producao-agricola_ibge-sidra-tabela5457_*_municipal.csv"),
            "rebanho": _um(RAW, "rebanho_ibge-sidra-tabela3939_*_municipal.csv"), "caged": _um(RAW, "caged-movimentacao_mte_*_municipal.csv"),
            "rais": _um(RAW, "rais-vinculos_mte_*_municipal.csv"), "rais_meta": _um(RAW, "rais-vinculos_mte_*_municipal.json"),
            "idhm": _um(RAW, "idhm_atlas-brasil_*_municipal.csv"), "cadunico": _um(RAW, "cadunico_mds-ri_*_municipal.csv"),
            "bf": RAW / "bolsa-familia_mds-ri_atual_municipal.csv", "siconfi": _um(RAW, "despesas-por-funcao_siconfi_*_municipal.csv"),
            "siconfi_meta": _um(RAW, "despesas-por-funcao_siconfi_*_municipal.json")}


def calcular(e: dict) -> dict:
    d = {}
    cs = pd.read_csv(e["censo"])
    d["censo"] = [{"situacao": s, "setores": int((cs.situacao == s).sum()), "populacao": int(cs[cs.situacao == s].populacao_total.sum())} for s in ("Urbana", "Rural")]
    d["censo_mun"] = {"populacao_total": int(cs.populacao_total.sum()), "renda_media": float(cs.rendimento_medio_domiciliar_per_capita_reais_municipio.iloc[0]),
                      "pct_agua_inadequada": float(cs.pct_domicilios_agua_inadequada_municipio.iloc[0]),
                      "pct_esgoto_inadequado": float(cs.pct_domicilios_esgoto_inadequado_municipio.iloc[0]), "n_setores": int(len(cs))}
    pp = pd.read_csv(e["pib_pc"]).set_index("ano")
    p = pd.read_csv(e["pib"])
    v = lambda i: p[p.id_variavel == i].dropna(subset=["valor"]).set_index("ano").valor
    comp = pd.DataFrame({"a": v(516), "i": v(520), "ad": v(528), "s": v(6574)}).dropna()
    pib_tot = v(37)
    anos = sorted(set(pp.index) | set(pib_tot.index))
    nn = lambda x: None if pd.isna(x) else round(float(x), 2)
    d["pib"] = [{"ano": int(a), "pib_mil_reais": None if a not in pib_tot.index else float(pib_tot[a]),
                 "pib_per_capita": nn(pp.pib_per_capita_reais.get(a)),
                 "vab_agropecuaria_pct": nn(comp.a.get(a)), "vab_industria_pct": nn(comp.i.get(a)),
                 "vab_administracao_publica_pct": nn(comp.ad.get(a)), "vab_servicos_pct": nn(comp.s.get(a))} for a in anos]
    pam, reb = pd.read_csv(e["pam"]), pd.read_csv(e["rebanho"])
    ar = lambda c: pam[(pam.variavel_id == 216) & (pam.categoria == c)].set_index("periodo").valor
    rb = lambda c: reb[reb.categoria == c].set_index("periodo").valor
    anos_pam = sorted(int(x) for x in ar("Arroz (em casca)").index)
    d["pam"] = [{"ano": a, "arroz_area_colhida_ha": float(ar("Arroz (em casca)")[a]), "soja_area_colhida_ha": float(ar("Soja (em grão)")[a]),
                 "area_colhida_total_ha": float(ar("Total")[a]), "bovino_cabecas": int(rb("Bovino")[a]), "ovino_cabecas": int(rb("Ovino")[a])} for a in anos_pam]
    c = pd.read_csv(e["caged"])
    m = c.groupby("competencia")[["admissoes", "desligamentos", "saldo"]].sum()
    acum = m.saldo.cumsum()
    d["caged"] = [{"competencia": int(k), "rotulo": f"{str(k)[4:]}/{str(k)[2:4]}", "admissoes": int(r.admissoes), "desligamentos": int(r.desligamentos),
                   "saldo": int(r.saldo), "saldo_acumulado": int(acum[k])} for k, r in m.iterrows()]
    c["ano"] = c.competencia // 100
    ult_ano = int(c.ano.max())
    d["caged_setor_ult_ano"] = c[c.ano == ult_ano].groupby("secao_cnae").saldo.sum().sort_values().to_dict()
    ra = pd.read_csv(e["rais"]).sort_values("vinculos_ativos", ascending=False)
    d["rais"] = [{"secao": s, "rotulo": f"{CNAE[s]} ({s})", "vinculos": int(n)} for s, n in zip(ra.secao_cnae, ra.vinculos_ativos)]
    d["rais_ano"] = int(json.loads(e["rais_meta"].read_text(encoding="utf-8"))["ano"])
    i = pd.read_csv(e["idhm"])
    iv = lambda n, col: [float(x) for x in i[i.indicador == n].sort_values("ano")[col]]
    anos_i = sorted(int(x) for x in i.ano.unique())
    cols = {"idhm": ("IDHM", "valor_municipio"), "idhm_brasil": ("IDHM", "valor_brasil"), "idhm_renda": ("IDHM Renda", "valor_municipio"),
            "idhm_longevidade": ("IDHM Longevidade", "valor_municipio"), "idhm_educacao": ("IDHM Educação", "valor_municipio")}
    series = {k: iv(*v_) for k, v_ in cols.items()}
    d["idhm"] = [{"ano": a, **{k: series[k][j] for k in cols}} for j, a in enumerate(anos_i)]
    cu = pd.read_csv(e["cadunico"])
    d["cadunico"] = [{"ano": int(r.ano), "mes": int(r.mes), "rotulo": str(int(r.ano)) if r.mes == 12 else f"{MES[int(r.mes)]}/{str(int(r.ano))[2:]}",
                      "familias_cadastradas": int(r.familias_cadastradas), "taxa_atualizacao_pct": float(r.taxa_atualizacao_pct)} for r in cu.itertuples()]
    bf = pd.read_csv(e["bf"]).iloc[0]
    d["bf"] = [{"mes_referencia": int(bf.mes_referencia), "ano_referencia": int(bf.ano_referencia), "familias_atendidas": int(bf.familias_atendidas),
                "pessoas_beneficiadas": int(bf.pessoas_beneficiadas), "investimento_total_reais": int(bf.investimento_total_reais),
                "beneficio_medio_reais": float(bf.beneficio_medio_reais)}]
    sc = pd.read_csv(e["siconfi"])
    sm = json.loads(e["siconfi_meta"].read_text(encoding="utf-8"))
    linhas = []
    for a in sorted(sc.ano.unique()):
        x = sc[sc.ano == a].set_index("funcao")
        bim = int(x.bimestre_referencia.iloc[0])
        sa, asx = x.loc["Saúde"], x.loc["Assistência Social"]
        pct = round(float(sa.valor_reais / (pib_tot[a] * 1000) * 100), 2) if (a in pib_tot.index and bim == 6) else None
        linhas.append({"ano": int(a), "bimestre": bim, "parcial": bim != 6, "saude_per_capita_reais": round(float(sa.valor_reais / sa.populacao), 2),
                       "assistencia_social_per_capita_reais": round(float(asx.valor_reais / asx.populacao), 2), "saude_pct_pib": pct})
    d["siconfi"] = linhas
    d["siconfi_meta"] = sm
    return d


def notas_kpis(d: dict):
    cm, pib, pam, cg, rais, idh, cu, bf, sic = d["censo_mun"], d["pib"], d["pam"], d["caged"], d["rais"], d["idhm"], d["cadunico"], d["bf"][0], d["siconfi"]
    pc = [r for r in pib if r["pib_per_capita"] is not None]
    cp = [r for r in pib if r["vab_agropecuaria_pct"] is not None]
    sem_pc = [r["ano"] for r in pib if r["pib_per_capita"] is None and pc[0]["ano"] < r["ano"] < pc[-1]["ano"]]
    sem_comp = [r["ano"] for r in pib if r["pib_mil_reais"] is not None and r["vab_agropecuaria_pct"] is None]
    agro = max(cp, key=lambda r: r["vab_agropecuaria_pct"])
    agro_min = min(r["vab_agropecuaria_pct"] for r in cp)
    p0, p1 = pam[0], pam[-1]
    pct_arroz = 100 * p1["arroz_area_colhida_ha"] / p1["area_colhida_total_ha"]
    mi = lambda v: f"{br(v / 1e6, 2)} milhão" if v >= 1e6 else f"{br(v / 1e3, 1)} mil"
    c0, c1 = cg[0], cg[-1]
    ult_ano_cg = c1["competencia"] // 100
    setor_neg = min(d["caged_setor_ult_ano"], key=d["caged_setor_ult_ano"].get)
    total_rais = sum(r["vinculos"] for r in rais)
    agro_rais = next(r["vinculos"] for r in rais if r["secao"] == "A")
    rot_c = lambda k: f"{str(k)[:4]}-{str(k)[4:]}"
    zeros = [r["ano"] for r in cu if r["taxa_atualizacao_pct"] == 0]
    cu_ult = cu[-1]
    fech = [r for r in sic if not r["parcial"]]
    parc = [r for r in sic if r["parcial"]]
    pctp = [r for r in fech if r["saude_pct_pib"] is not None]
    lo = int(min(r["saude_pct_pib"] for r in pctp) * 10) / 10
    hi = int(max(r["saude_pct_pib"] for r in pctp) * 10) / 10
    cresc = 100 * (fech[-1]["saude_per_capita_reais"] - fech[0]["saude_per_capita_reais"]) / fech[0]["saude_per_capita_reais"]
    i0, i1 = idh[0], idh[-1]
    faixa = next(f for f in FAIXAS_IDHM if i1["idhm"] >= f[0])
    anos_sic = sorted(int(a) for a in d["siconfi_meta"]["bimestre_de_referencia_por_ano"])
    bf_mes = f"{bf['mes_referencia']:02d}/{bf['ano_referencia']}"
    notas = {
        "eco_meta_censo": "2022",
        "eco_meta_economia": f"PIB {pc[0]['ano']}–{pc[-1]['ano']} · PAM/PPM {p0['ano']}–{p1['ano']} · CAGED {c0['competencia'] // 100}–{ult_ano_cg}",
        "eco_meta_vulnerabilidade": f"IDHM {i0['ano']}–{i1['ano']} · CadÚnico {cu[0]['ano']}–{cu_ult['ano']}",
        "eco_nota_censo": (f"Censo 2022 ({cm['n_setores']} setores censitários). Renda, água e esgoto só existem no nível municipal para este censo — "
                           "não variam por setor no produto do IBGE."),
        "eco_nota_economia": ("PIB municipal e composição setorial (IBGE/SIDRA), produção agropecuária (PAM/PPM) e emprego formal (RAIS/Novo CAGED) — "
                              "indicadores de contexto socioeconômico municipal, sem granularidade espacial abaixo do município na fonte (ao contrário "
                              "das demais camadas deste painel, por isso não aparecem no geoportal)."),
        "eco_meta_pib_pc": f"IBGE/SIDRA · {pc[0]['ano']}–{pc[-1]['ano']}",
        "eco_meta_vab": f"% do VAB total · {cp[0]['ano']}–{cp[-1]['ano']}",
        "eco_nota_pib": (f"{'/'.join(map(str, sem_pc))} não tem PIB per capita (sem fonte de população nesse ano) e {'/'.join(map(str, sem_comp))} não têm "
                         f"detalhamento setorial (a fonte só publica VAB por atividade até {cp[-1]['ano']}, mesmo com o PIB total mais recente). O pico da "
                         f"agropecuária no VAB em {agro['ano']} ({br(agro['vab_agropecuaria_pct'], 1, 0)}%) é um choque de **preço** do arroz, não de "
                         "volume — a área colhida (abaixo) fica estável no mesmo período."),
        "eco_meta_pam": f"PAM · {p0['ano']}–{p1['ano']} · hectares",
        "eco_meta_ppm": f"PPM · {p0['ano']}–{p1['ano']} · cabeças",
        "eco_nota_pam": (f"Arroz irrigado domina a lavoura do município (~{br(pct_arroz)}% da área colhida em {p1['ano']}); soja começou a crescer a "
                         "partir de meados dos anos 2010, ainda pequena. Rebanho ovino em queda estrutural de longo prazo (de "
                         f"{mi(p0['ovino_cabecas'])} de cabeças em {p0['ano']} para {mi(p1['ovino_cabecas'])} em {p1['ano']}), mesmo padrão já visto em "
                         "outros municípios do Pampa gaúcho — bovino oscila, sem tendência clara. PAM/PPM não têm versão por setor censitário nem "
                         "geolocalizada na fonte (decisão confirmada com o usuário) — por isso ficam só como indicador tabular, sem virar camada no geoportal."),
        "eco_meta_caged_fluxo": f"mensal · {rot_c(c0['competencia'])} a {rot_c(c1['competencia'])}",
        "eco_meta_caged_saldo": f"acumulado desde {rot_c(c0['competencia'])}",
        "eco_nota_caged": ("Novo CAGED (via eSocial, a partir de 2020) — período anterior fica fora de escopo por quebra metodológica com o CAGED antigo "
                           "(formulário declaratório), mesmo espírito da quebra já documentada entre Censos do IBGE. "
                           f"Saldo de {ult_ano_cg} (parcial, até {MES_LONGO[c1['competencia'] % 100]}) puxado para baixo pelo setor {CNAE[setor_neg]} — "
                           "sinal a acompanhar, não uma reversão de tendência confirmada."),
        "eco_meta_rais": f"retrato em 31/12/{d['rais_ano']} · {br(total_rais)} vínculos",
        "eco_nota_rais": (f"RAIS não tem série histórica baixada nesta rodada (só o ano mais recente — o arquivo de estabelecimentos nacional tem ~3GB "
                          "por ano) — funciona como retrato do estoque atual ao lado da série mensal de fluxo do CAGED acima, não como tendência "
                          f"própria. Participação da Agropecuária no emprego formal ({br(100 * agro_rais / total_rais, 1, 0)}%) é menor que sua "
                          f"participação no PIB ({br(agro_min)}–{br(agro['vab_agropecuaria_pct'])}%), padrão esperado: agricultura "
                          "mecanizada é intensiva em capital, não em mão de obra."),
        "eco_resumo_rais": f"Ver RAIS {d['rais_ano']} completa por seção CNAE ({len(rais)} seções)",
        "eco_nota_vulnerabilidade": ("Desenvolvimento humano (IDHM, Atlas Brasil/PNUD) e cobertura de assistência social (CadÚnico e Bolsa Família, "
                                     "MDS) — indicadores de contexto municipal único, sem granularidade espacial na fonte, por isso fora do geoportal."),
        "eco_meta_idhm": f"Atlas Brasil · {i0['ano']}–{i1['ano']}",
        "eco_meta_idhm_brasil": f"{i0['ano']}–{i1['ano']}",
        "eco_nota_idhm": ("IDHM 2022 não existe em nível municipal na fonte (nem via Censo, não computado; nem via PNAD Contínua, testada e confirmada "
                          f"vazia para município — inclusive Porto Alegre, usada como controle de sanidade) — a série para em {i1['ano']}. Evolução "
                          f"acima da média do Brasil nos {len(idh)} censos, puxada principalmente pela Longevidade ({br(i0['idhm_longevidade'], 3)}→"
                          f"{br(i1['idhm_longevidade'], 3)}) e por uma forte recuperação da Educação ({br(i0['idhm_educacao'], 3)}→"
                          f"{br(i1['idhm_educacao'], 3)}, o componente que mais cresceu proporcionalmente)."),
        "eco_meta_cadunico": f"dezembro de cada ano · {cu[0]['ano']}–{cu_ult['ano']}",
        "eco_meta_cadunico_taxa": f"% das famílias · {cu[0]['ano']}–{cu_ult['ano']}",
        "eco_nota_cadunico": (f"Taxa de atualização 0% em {zeros[0]}–{zeros[-1]} é valor real da fonte (métrica aparentemente não rastreada pelo MDS "
                              f"nesse período), não falha de extração. Último ponto ({cu_ult['ano']}) é {MES_LONGO[cu_ult['mes']]}, não dezembro como os "
                              "demais — comparar com cautela, ano ainda não fechado. Bolsa Família (ver KPIs acima) não tem série histórica confiável "
                              "nesta ferramenta antes de 2023 (o módulo consultado referencia especificamente o \"Novo Bolsa Família\", Lei 14.601/2023), "
                              "por isso não vira gráfico de série temporal, só o retrato do mês mais recente."),
        "eco_nota_siconfi": ("Execução orçamentária por função (RREO-Anexo 02, Tesouro Nacional) — despesas liquidadas acumuladas no ano, indicador de "
                             "contexto municipal único, sem granularidade espacial na fonte."),
        "eco_nota_saneamento": ("**Sobre o KPI \"Saneamento\" acima:** a função \"Saneamento\" não aparecer no orçamento por função NÃO significa ausência "
                                "de investimento no serviço — água e esgoto de Uruguaiana são operados pela CORSAN (concessionária estadual), fora do "
                                "orçamento municipal direto. O gasto existe, só não passa por essa peça orçamentária específica."),
        "eco_meta_siconfi_saude": f"{fech[0]['ano']}–{fech[-1]['ano']} · R$/hab",
        "eco_meta_siconfi_assist": f"{fech[0]['ano']}–{fech[-1]['ano']} · R$/hab",
        "eco_meta_siconfi_pct": f"{pctp[0]['ano']}–{pctp[-1]['ano']} · anos com PIB disponível",
        "eco_nota_siconfi_parcial": (f"{parc[0]['ano']} (parcial, só bimestre {parc[0]['bimestre']} publicado até agora) fica fora dos gráficos — R$ "
                                     f"{br(parc[0]['saude_per_capita_reais'], 2)}/hab em Saúde e R$ {br(parc[0]['assistencia_social_per_capita_reais'], 2)}/hab "
                                     f"em Assistência Social acumulados até {MES_LONGO[parc[0]['bimestre'] * 2]} não são comparáveis a um ano fechado; incluir "
                                     f"sugeriria uma queda de investimento que não é real. % do PIB só existe até {pctp[-1]['ano']} (mesma lacuna já documentada "
                                     "na seção de Economia — a fonte do PIB não publica detalhamento mais recente). Despesa em Saúde per capita cresce de forma "
                                     f"monotônica desde {fech[0]['ano']} (+{br(cresc, 1)}% entre {fech[0]['ano']} e {fech[-1]['ano']}), estável entre "
                                     f"{br(lo, 1)}% e {br(hi, 1)}% do PIB (esperado — o piso constitucional de 15% da EC 29/86 é sobre receita própria municipal, "
                                     "não sobre o PIB)."),
    }
    kpis = {
        "kpis_censo": [
            {"rotulo": "População total", "valor": br(cm["populacao_total"]), "unidade": "hab",
             "sub": f"{br(d['censo'][0]['populacao'])} urbana · {br(d['censo'][1]['populacao'])} rural"},
            {"rotulo": "Renda média domiciliar per capita", "prefixo": "R$ ", "valor": br(cm["renda_media"], 1, 0), "unidade": "", "sub": "nível municipal (Censo 2022)"},
            {"rotulo": "Domicílios com água inadequada", "valor": br(cm["pct_agua_inadequada"], 1, 0), "unidade": "%", "sub": "sem ligação à rede geral"},
            {"rotulo": "Domicílios com esgoto inadequado", "valor": br(cm["pct_esgoto_inadequado"], 1, 0), "unidade": "%", "sub": "fora de rede/fossa ligada"},
        ],
        "kpis_economia": [
            {"rotulo": "PIB per capita", "prefixo": "R$ ", "valor": br(pc[-1]["pib_per_capita"], 1, 0), "unidade": "", "sub": f"{pc[-1]['ano']} · mais recente disponível"},
            {"rotulo": "Agropecuária no VAB (pico)", "valor": br(agro["vab_agropecuaria_pct"], 1, 0), "unidade": " %", "sub": f"{agro['ano']} · choque de preço do arroz, não de volume"},
            {"rotulo": "Saldo Novo CAGED acumulado", "valor": br(c1["saldo_acumulado"]), "unidade": " vínculos",
             "sub": f"{rot_c(c0['competencia'])} a {rot_c(c1['competencia'])} ({len(cg)} meses)"},
            {"rotulo": "Vínculos formais ativos (RAIS)", "valor": br(total_rais), "unidade": " vínculos", "sub": f"retrato em 31/12/{d['rais_ano']}"},
        ],
        "kpis_vulnerabilidade": [
            {"rotulo": "IDHM", "valor": br(i1["idhm"], 3), "unidade": "", "sub": f"{i1['ano']} · classificação \"{faixa[1]}\" ({faixa[2]})"},
            {"rotulo": "Famílias no CadÚnico", "valor": br(cu_ult["familias_cadastradas"]), "unidade": "", "sub": f"{cu_ult['rotulo']} · mais recente disponível"},
            {"rotulo": "Famílias atendidas — Bolsa Família", "valor": br(bf["familias_atendidas"]), "unidade": "",
             "sub": f"{bf_mes} · {br(bf['pessoas_beneficiadas'])} pessoas beneficiadas"},
            {"rotulo": "Benefício médio — Bolsa Família", "prefixo": "R$ ", "valor": br(bf["beneficio_medio_reais"], 1, 0), "unidade": "",
             "sub": f"{bf_mes} · R$ {br(bf['investimento_total_reais'])} investidos no mês"},
        ],
        "kpis_siconfi": [
            {"rotulo": "Despesa em Saúde per capita", "prefixo": "R$ ", "valor": br(fech[-1]["saude_per_capita_reais"], 1, 0), "unidade": "",
             "sub": f"{fech[-1]['ano']} · +{br(cresc, 1, 0)}% desde {fech[0]['ano']}"},
            {"rotulo": "Despesa em Assistência Social per capita", "prefixo": "R$ ", "valor": br(fech[-1]["assistencia_social_per_capita_reais"], 1, 0), "unidade": "",
             "sub": f"{fech[-1]['ano']} · mais recente (ano fechado)"},
            {"rotulo": "Saúde — % do PIB municipal", "valor": br(pctp[-1]["saude_pct_pib"], 1, 0) + "%", "unidade": "",
             "sub": f"{pctp[-1]['ano']} · estável {br(lo, 1)}–{br(hi, 1)}% no período"},
            {"rotulo": "Função \"Saneamento\" no orçamento", "valor": "ausente" if "Saneamento" in d["siconfi_meta"].get("funcoes_sem_dado_no_periodo", []) else "presente",
             "unidade": "", "sub": f"em todos os {len(anos_sic)} anos — serviço prestado pela CORSAN"},
        ],
    }
    return notas, kpis


def ids(codigo_ibge: str = "4322400") -> dict[str, str]:
    d = calcular(entradas())
    a = lambda k, c="ano": (d[k][0][c], d[k][-1][c])
    return {"censo": "censo_ibge_2022_situacao-setor", "pib": "pib_ibge_%s-%s_anual" % a("pib"), "pam": "agropecuaria_ibge_%s-%s_anual" % a("pam"),
            "caged": "emprego_caged_%s-%s_mensal" % (d["caged"][0]["competencia"] // 100, d["caged"][-1]["competencia"] // 100),
            "rais": f"emprego_rais_{d['rais_ano']}_secao", "idhm": "idhm_atlas_%s-%s_censos" % a("idhm"),
            "cadunico": "cadunico_mds_%s-%s_anual" % a("cadunico"), "bf": "bolsa-familia_mds_%s-%02d_mes" % (d["bf"][0]["ano_referencia"], d["bf"][0]["mes_referencia"]),
            "siconfi": "despesas_siconfi_%s-%s_anual" % a("siconfi")}


def gerar(codigo_ibge: str = "4322400", forcar: bool = False, so: str | None = None) -> dict[str, bool]:
    e = entradas()
    d = calcular(e)
    notas, kpis = notas_kpis(d)
    n = ids(codigo_ibge)
    c = dict(codigo_ibge=codigo_ibge, script=SCRIPT, selo="conferido", unidade_espacial="município")
    ext = lambda f, **kw: {"fonte_rodape": FONTES[f], "telas": ["economia"], "supressao_nota": NOTA_SUPRESSAO, **kw}
    col = lambda linhas: [{"nome": k, "tipo": "texto" if isinstance(v, str) else ("bool" if isinstance(v, bool) else ("int" if isinstance(v, int) else "float"))}
                          for k, v in linhas[0].items()]
    rais_graf = d["rais"][:RAIS_TOPO] + [{"secao": "outras", "rotulo": f"Outras ({len(d['rais']) - RAIS_TOPO} seções)",
                                           "vinculos": sum(r["vinculos"] for r in d["rais"][RAIS_TOPO:])}]
    defs = [
        (n["censo"], ["EV08", "TB03"], dict(titulo="População e setores por situação (Censo 2022)", fonte_curta="IBGE, Censo 2022", fonte="IBGE, Censo 2022, agregados por setor",
                                             unidade_medida="habitantes; setores", periodo={"inicio": "2022", "fim": "2022"}, dados_ate="2022", avisos=[],
                                             colunas=col(d["censo"]), dados=d["censo"], campos_contagem=["populacao"], grupos=[[0, 1]],
                                             extras=ext("censo", notas=notas, kpis_censo=kpis["kpis_censo"], municipal=d["censo_mun"])),
         [e["censo"]], "soma da população e contagem de setores por situação; indicadores municipais do mesmo arquivo; supressão de comum.py nas populações"),
        (n["pib"], ["EV01"], dict(titulo="PIB per capita e composição do VAB", fonte_curta="IBGE/SIDRA", fonte="IBGE, PIB dos Municípios (SIDRA tabela 5938)",
                                   unidade_medida="R$; % do VAB", periodo={"inicio": str(d["pib"][0]["ano"]), "fim": str(d["pib"][-1]["ano"])}, dados_ate=str(d["pib"][-1]["ano"]),
                                   avisos=[{"tipo": "lacuna", "texto": "ano sem população não tem PIB per capita; VAB por atividade só até o último ano publicado"}],
                                   colunas=col(d["pib"]), dados=d["pib"], extras=ext("pib", kpis_economia=kpis["kpis_economia"])),
         [e["pib_pc"], e["pib"]], "PIB per capita do CSV processado; percentuais do VAB da tabela 5938; null onde a fonte não publica"),
        (n["pam"], ["EV02"], dict(titulo="Área colhida (arroz, soja) e efetivo de rebanho (bovino, ovino)", fonte_curta="IBGE PAM/PPM", fonte="IBGE, PAM (tabela 5457) e PPM (tabela 3939)",
                                   unidade_medida="hectares; cabeças", periodo={"inicio": str(d["pam"][0]["ano"]), "fim": str(d["pam"][-1]["ano"])}, dados_ate=str(d["pam"][-1]["ano"]),
                                   avisos=[], colunas=col(d["pam"]), dados=d["pam"], extras=ext("pam")),
         [e["pam"], e["rebanho"]], "área colhida (variável 216) de arroz, soja e total; efetivo bovino e ovino"),
        (n["caged"], ["EV03"], dict(titulo="Admissões, desligamentos e saldo acumulado (Novo CAGED)", fonte_curta="MTE/Novo CAGED", fonte="Ministério do Trabalho e Emprego, Novo CAGED",
                                     unidade_medida="vínculos", periodo={"inicio": str(d["caged"][0]["competencia"]), "fim": str(d["caged"][-1]["competencia"])},
                                     dados_ate=str(d["caged"][-1]["competencia"]), avisos=[{"tipo": "parcial", "texto": "último ano parcial"}],
                                     colunas=col(d["caged"]), dados=d["caged"], extras=ext("emprego")),
         [e["caged"]], "soma das seções CNAE por competência; saldo acumulado desde o primeiro mês"),
        (n["rais"], ["EV03"], dict(titulo="Vínculos formais ativos por seção CNAE (RAIS)", fonte_curta="MTE/RAIS", fonte="Ministério do Trabalho e Emprego, RAIS (estabelecimentos)",
                                    unidade_medida="vínculos", periodo={"inicio": str(d["rais_ano"]), "fim": str(d["rais_ano"])}, dados_ate=f"{d['rais_ano']}-12-31", avisos=[],
                                    colunas=col(d["rais"]), dados=d["rais"], extras=ext("emprego", grafico=rais_graf, total=sum(r["vinculos"] for r in d["rais"]))),
         [e["rais"], e["rais_meta"]], "vínculos por seção em ordem decrescente; gráfico com as 8 maiores e o resto somado"),
        (n["idhm"], ["EV04"], dict(titulo="IDHM e componentes", fonte_curta="Atlas Brasil", fonte="Atlas do Desenvolvimento Humano no Brasil (PNUD/Ipea/FJP)",
                                    unidade_medida="índice", periodo={"inicio": str(d["idhm"][0]["ano"]), "fim": str(d["idhm"][-1]["ano"])}, dados_ate=str(d["idhm"][-1]["ano"]),
                                    avisos=[{"tipo": "lacuna", "texto": "IDHM 2022 não existe em nível municipal"}], colunas=col(d["idhm"]), dados=d["idhm"],
                                    extras=ext("idhm", kpis_vulnerabilidade=kpis["kpis_vulnerabilidade"])),
         [e["idhm"]], "IDHM e componentes do município e IDHM do Brasil por censo"),
        (n["cadunico"], ["EV05"], dict(titulo="Famílias no CadÚnico e taxa de atualização", fonte_curta="MDS", fonte="MDS, Relatório de Informações (RIv3)",
                                        unidade_medida="famílias; %", periodo={"inicio": str(d["cadunico"][0]["ano"]), "fim": str(d["cadunico"][-1]["ano"])},
                                        dados_ate=f"{d['cadunico'][-1]['ano']}-{d['cadunico'][-1]['mes']:02d}", avisos=[{"tipo": "parcial", "texto": "último ponto não é dezembro"}],
                                        colunas=col(d["cadunico"]), dados=d["cadunico"], extras=ext("mds")),
         [e["cadunico"]], "dezembro de cada ano; último ponto no mês mais recente publicado"),
        (n["bf"], ["EV06"], dict(titulo="Bolsa Família no mês mais recente", fonte_curta="MDS", fonte="MDS, Relatório de Informações (RIv3)", unidade_medida="famílias; pessoas; R$",
                                  periodo={"inicio": f"{d['bf'][0]['ano_referencia']}-{d['bf'][0]['mes_referencia']:02d}", "fim": f"{d['bf'][0]['ano_referencia']}-{d['bf'][0]['mes_referencia']:02d}"}, dados_ate=f"{d['bf'][0]['ano_referencia']}-{d['bf'][0]['mes_referencia']:02d}",
                                  avisos=[], colunas=col(d["bf"]), dados=d["bf"], extras=ext("mds")),
         [e["bf"]], "linha única do mês de referência"),
        (n["siconfi"], ["EV07"], dict(titulo="Despesas em Saúde e Assistência Social por habitante", fonte_curta="SICONFI", fonte="Tesouro Nacional, SICONFI, RREO-Anexo 02 (despesas liquidadas)",
                                       unidade_medida="R$/hab; % do PIB", periodo={"inicio": str(d["siconfi"][0]["ano"]), "fim": str(d["siconfi"][-1]["ano"])},
                                       dados_ate=str(d["siconfi"][-1]["ano"]), avisos=[{"tipo": "parcial", "texto": "ano com bimestre menor que 6 é parcial e fica fora dos gráficos"}],
                                       colunas=col(d["siconfi"]), dados=d["siconfi"], extras=ext("siconfi", kpis_siconfi=kpis["kpis_siconfi"])),
         [e["siconfi"], e["siconfi_meta"], e["pib"]], "valor da função / população do SICONFI; % do PIB com o PIB a preços correntes do mesmo ano"),
    ]
    out = {}
    for pid, ativos, kw, ent, transf in defs:
        if so and so != pid:
            continue
        p = comum.montar_produto(id=pid, ativos=ativos, **c, **kw)
        out[pid] = comum.gravar_produto(p, transformacao=transf, entradas=ent, forcar=forcar, extras_metadado={"supressao_nota": NOTA_SUPRESSAO})
    return out


def _produtos() -> list[str]:
    try:
        return list(ids().values())
    except (OSError, ValueError, KeyError):
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
