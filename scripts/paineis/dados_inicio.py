"""
Resumo da página inicial (index.html) em data/paineis/inicio_resumo.json.

Lê só produtos já publicados em data/paineis/ e repete os indicadores que cada
tela mostra no topo (mesma lógica dos cartões da antiga síntese): 2 ou 3 por
dimensão, com período, fonte, selo, aviso (parcial, preliminar, em conferência)
e a data dos dados. O cartão da cheia e saúde traz as cotas e a faixa de tempo
de retorno de inundacao_sgb_cotas.

Nenhum número do rio entra aqui: a faixa do rio da página inicial lê, no
navegador, a telemetria recente (rio_ana-77150000_telemetria-recente_diario), a
única exceção da regra n. As contagens (grupos de camadas do mapa, telas,
conjuntos de dados e fontes) vêm de mapa.html, das telas, dos geradores e do
catálogo (data/paineis/catalogo_dados.json, quando já gerado).

Uso:
    python scripts/paineis/dados_inicio.py [--codigo-ibge 4322400] [--forcar]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import comum  # noqa: E402
from comum import br  # noqa: E402

RAIZ = comum.RAIZ
PASTA = comum.PASTA_PUBLICACAO
SCRIPT = "scripts/paineis/dados_inicio.py"
ID = "inicio_resumo"
PRODUTOS = [ID]

# produtos lidos (ids estáveis); nenhum produto do rio
FONTES = {
    "precip": "clima_precipitacao-merge_serie_anual",
    "inmet": "clima_inmet-a809_serie_anual",
    "queim": "queimadas_inpe_serie_anual",
    "sim": "saude_sim_serie_anual",
    "sih": "saude_sih_serie_anual",
    "sinan": "saude_sinan_serie_anual",
    "pop": "populacao_ibge_serie_anual",
    "uso": "uso-solo_mapbiomas_serie_classes",
    "pocos": "pocos_siagas_atual_aquifero",
    "censo": "censo_ibge_2022_situacao-setor",
    "pib": "pib_ibge_serie_anual",
    "idhm": "idhm_atlas_censos",
    "cadunico": "cadunico_mds_serie_anual",
    "s2id": "desastres_s2id_serie_anual",
    "aneel": "energia_aneel_serie_anual",
    "cotas": "inundacao_sgb_cotas",
}
# produtos derivados desta rodada: não contam como "conjunto de dados" na página inicial
DERIVADOS = {"inicio_resumo", "catalogo_dados"}


def ler(id_: str) -> dict:
    return json.loads((PASTA / f"{id_}.json").read_text(encoding="utf-8"))


def periodo(p: dict) -> str:
    i, f = str(p["periodo"]["inicio"]), str(p["periodo"]["fim"])
    return i if i == f else f"{i}–{f}"


def linha(dim: str, ordem: int, p: dict, rotulo: str, valor: str, unidade: str = "", sub: str = "",
          prefixo: str = "", aviso: str | None = None) -> dict:
    return {"dimensao": dim, "ordem": ordem, "rotulo": rotulo, "prefixo": prefixo, "valor": valor, "unidade": unidade,
            "sub": sub, "aviso": aviso, "produto": p["id"], "fonte_curta": p["fonte_curta"], "periodo": periodo(p),
            "selo": p["selo"], "dados_ate": p["dados_ate"]}


def de_kpi(dim: str, ordem: int, p: dict, k: dict) -> dict:
    """Indicador já calculado pelo gerador da tela (campos kpis*): copiado sem recalcular."""
    return linha(dim, ordem, p, k["rotulo"], k["valor"], (k.get("unidade") or "").strip(), k.get("sub", ""), k.get("prefixo", ""))


def num(v, casas: int = 0) -> str:
    return "—" if v is None else br(v, casas, 0)


def clima(f) -> list[dict]:
    pr, im, qu = f["precip"], f["inmet"], f["queim"]
    out = []
    u, ref = pr["dados"][-1], pr["media_referencia"]
    out.append(linha("clima", 1, pr, f"Precipitação total · {u['ano']}", num(u["merge_mm"]), "mm",
                     f"{pr['fonte_curta']} · média {ref['ano_inicio']}–{ref['ano_fim']}: {num(ref['precipitacao_mm'])} mm"))
    u = im["dados"][-1]
    ant = im["dados"][:-1]
    rec = max(r["dias_calor_extremo"] for r in ant)
    ano_rec = next(r["ano"] for r in ant if r["dias_calor_extremo"] == rec)
    parcial = next((a["texto"] for a in im["avisos"] if a["tipo"] == "parcial" and (a.get("onde") or {}).get("ano") == u["ano"]), None)
    out.append(linha("clima", 2, im, f"Dias de calor ≥35°C · {u['ano']}", num(u["dias_calor_extremo"]), "dias",
                     f"recorde: {rec} dias ({ano_rec})", aviso=parcial and f"parcial: {parcial}"))
    u = qu["dados"][-1]
    pico = max(r["focos"] for r in qu["dados"])
    ano_pico = next(r["ano"] for r in qu["dados"] if r["focos"] == pico)
    out.append(linha("clima", 3, qu, f"Focos de queimada · {u['ano']}", num(u["focos"]), "focos", f"pico {ano_pico}: {num(pico)}"))
    return out


def saude(f) -> list[dict]:
    sim, sih, sn = f["sim"], f["sih"], f["sinan"]
    out = []
    # rodada 121: último ano consolidado; o preliminar (SIM) e o parcial (SIH) vão no aviso
    fech = [r for r in sim["dados"] if not r.get("preliminar")]
    u = fech[-1]
    pre = next((r for r in reversed(sim["dados"]) if r.get("preliminar")), None)
    out.append(linha("saude", 1, sim, f"Óbitos totais · {u['ano']}", num(u["obitos_total"]), "",
                     f"dos quais {num(u['obitos_respiratorios'])} respiratórios",
                     aviso=pre and f"{pre['ano']} preliminar: {num(pre['obitos_total'])}"))
    fech_h = [r for r in sih["dados"] if not r.get("parcial")]
    par = next((r for r in reversed(sih["dados"]) if r.get("parcial")), None)
    u = fech_h[-1]
    pct = None if not u.get("internacoes_total") or u.get("internacoes_respiratorias") is None else 100 * u["internacoes_respiratorias"] / u["internacoes_total"]
    aviso_par = None
    if par:
        txt = next((a["texto"] for a in sih["avisos"] if a["tipo"] == "parcial" and (a.get("onde") or {}).get("ano") == par["ano"]), f"{par['ano']} parcial")
        aviso_par = f"{txt.split(';')[0]}: {num(par['internacoes_respiratorias'])}"
    out.append(linha("saude", 2, sih, f"Internações respiratórias · {u['ano']}", num(u["internacoes_respiratorias"]), "",
                     f"{num(pct, 1)}% das internações" if pct is not None else "", aviso=aviso_par))
    fech = [r for r in sn["dados"] if not r.get("preliminar")]
    u = fech[-1]
    pre = next((r for r in sn["dados"] if r.get("preliminar")), None)
    out.append(linha("saude", 3, sn, f"Notificações de dengue · {u['ano']}", num(u["dengue"]), "",
                     f"{fech[-2]['ano']}: {num(fech[-2]['dengue'])}",
                     aviso=pre and f"{pre['ano']} preliminar: {num(pre['dengue'])}"))
    return out


def populacao(f) -> list[dict]:
    k = f["pop"]["kpis"]
    return [de_kpi("populacao", i + 1, f["pop"], k[i]) for i in range(3)]


def territorio(f) -> list[dict]:
    p = f["uso"]
    return [de_kpi("territorio", 1, p, p["kpis_uso"][0]), de_kpi("territorio", 2, p, p["kpis_uso"][2]),
            # o indicador dos poços fica nos kpis do produto do uso do solo; período, fonte e selo são do SIAGAS
            de_kpi("territorio", 3, f["pocos"], p["kpis_meio"][0])]


def economia(f) -> list[dict]:
    return [de_kpi("economia", 1, f["pib"], f["pib"]["kpis_economia"][0]), de_kpi("economia", 2, f["censo"], f["censo"]["kpis_censo"][1]),
            # o indicador do CadÚnico fica nos kpis do produto do IDHM, mas período, selo e data são do produto do CadÚnico
            de_kpi("economia", 3, f["cadunico"], f["idhm"]["kpis_vulnerabilidade"][1])]


def riscos(f) -> list[dict]:
    return [de_kpi("riscos", 1, f["s2id"], f["s2id"]["kpis_s2id"][0]), de_kpi("riscos", 2, f["s2id"], f["s2id"]["kpis_s2id"][1]),
            de_kpi("riscos", 3, f["aneel"], f["aneel"]["kpis_aneel"][0])]


def cheia(f) -> list[dict]:
    """As cotas e a faixa de tempo de retorno, com a regra da tela da cheia ("cerca de N" quando a camada e o
    documento diferem menos que dif_tr_cerca_anos)."""
    c = f["cotas"]
    d, lim = c["dados"], c["dif_tr_cerca_anos"]

    def tr(r):
        a, k = r["tr_anos_camada"], r["tr_anos_documento"]
        if k is not None and a != k and abs(a - k) < lim:
            return f"cerca de {num(round((a + k) / 2))}"
        return br(a, 1)

    ordem = sorted(d, key=lambda r: r["tr_anos_camada"])
    cotas = [num(r["cota_cm"]) for r in d]
    lista = ", ".join(cotas[:-1]) + " e " + cotas[-1] if len(cotas) > 1 else cotas[0]
    conf = next((a["texto"] for a in c["avisos"] if a["tipo"] == "em_confirmacao"), None)
    return [linha("cheia", 1, c, "Manchas de inundação do SGB", num(len(d)), "cotas", f"{lista} cm"),
            linha("cheia", 2, c, "Tempo de retorno", f"{tr(ordem[0])} a {tr(ordem[-1])}", "anos", "da menor à maior cota", aviso=conf)]


def contagens() -> dict:
    """Contagens da página inicial, sempre lidas dos arquivos (nunca escritas no HTML)."""
    mapa = (RAIZ / "mapa.html").read_text(encoding="utf-8")
    grupos = [g for g in re.findall(r'class="grupo[^"]*" data-grupo="([^"]+)"', mapa) if g != "mapa-base"]
    # tela = página de dashboards/ com o cabeçalho comum (os redirecionamentos não têm)
    telas = sorted(p.stem for p in (RAIZ / "dashboards").glob("*.html")
                   if 'id="cabecalho-comum"' in p.read_text(encoding="utf-8") and p.stem != "catalogo")
    import manifesto  # noqa: E402  (import tardio: manifesto importa os geradores)
    conjuntos = [i for i in manifesto.ids_declarados() if i not in DERIVADOS]
    cat = PASTA / "catalogo_dados.json"
    fontes = len(json.loads(cat.read_text(encoding="utf-8"))["dados"]) if cat.exists() else None
    return {"grupos_camadas": len(grupos), "telas": len(telas), "conjuntos_dados": len(conjuntos), "fontes": fontes}


def gerar(codigo_ibge: str = "4322400", forcar: bool = False, so: str | None = None) -> dict[str, bool]:
    if so and so != ID:
        return {}
    f = {k: ler(v) for k, v in FONTES.items()}
    dados = clima(f) + saude(f) + populacao(f) + territorio(f) + economia(f) + riscos(f) + cheia(f)
    usados = sorted({r["produto"] for r in dados})
    ativos = sorted({a for k in usados for a in ler(k)["ativos"]})
    # o selo do resumo é o mais fraco dos produtos usados (cada linha traz o seu)
    selo = min((ler(k)["selo"] for k in usados), key=lambda s: comum.FORCA_SELO[s])
    cont = contagens()
    # rodapé da página inicial: as fontes dos produtos usados, sem repetir (mesmo campo das telas)
    rod = []
    for k in usados:
        m = ler(k)
        x = (m["fonte_rodape"] if "fonte_rodape" in m else m.get("fonte", "")).strip().rstrip(".")
        if x and x not in rod:
            rod.append(x)
    colunas = [{"nome": n, "tipo": "int" if n == "ordem" else "texto"} for n in
               ["dimensao", "ordem", "rotulo", "prefixo", "valor", "unidade", "sub", "aviso", "produto", "fonte_curta", "periodo", "selo", "dados_ate"]]
    datas = sorted(r["dados_ate"] for r in dados)
    p = comum.montar_produto(
        id=ID, codigo_ibge=codigo_ibge, ativos=ativos, titulo="Resumo da página inicial: indicadores por dimensão",
        fonte_curta="vários", fonte="produtos publicados em data/paineis/ (ver a coluna produto de cada linha)",
        unidade_espacial="município", unidade_medida="vários (ver cada linha)",
        periodo={"inicio": datas[0], "fim": datas[-1]}, dados_ate=datas[-1], selo=selo,
        avisos=[{"tipo": "metodo", "texto": "cópia dos indicadores do topo de cada tela, com o selo e o aviso do produto de origem; nenhum número do rio"}],
        colunas=colunas, dados=dados, script=SCRIPT,
        extras={"fonte_rodape": ". ".join(rod), "telas": ["inicio"], "contagens": cont, "produtos_usados": usados})
    entradas = [PASTA / f"{k}.json" for k in usados] + [RAIZ / "mapa.html"]
    if (PASTA / "catalogo_dados.json").exists():
        entradas.append(PASTA / "catalogo_dados.json")
    gravou = comum.gravar_produto(p, transformacao="indicadores do topo das telas copiados dos produtos publicados; contagens de mapa.html, das telas, dos geradores e do catálogo",
                                  entradas=entradas, forcar=forcar)
    return {ID: gravou}


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
