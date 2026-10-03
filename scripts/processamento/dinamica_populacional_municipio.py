"""
Tarefa 1 — município em 2000, 2010 e 2022: totais, distritos, pirâmides
etárias, indicadores etários e domicílios (tabelas CSV + gráficos PNG/SVG).

Fontes (universo do Censo; ver scripts/download/dinamica_populacional_sidra.py):
  população por sexo e situação: SIDRA 202 (2000, 2010) e 9923 + 9514 (2022);
  idade simples: SIDRA 1552 (2000, 2010) e 9514 (2022);
  domicílios: SIDRA 185 e 3451 (2000, 2010), 4712 (2022);
  conferência e itens sem tabela municipal: agregados por setor de cada censo.

Taxa geométrica anual: r = (P1/P0)^(1/t) − 1, com t = anos entre as datas de
referência (1º/ago/2000, 1º/ago/2010, 1º/ago/2022 → 10 e 12 anos).

Rodada 11: --layout a4 grava só a edição A4 das duas pirâmides em
docs/dinamica_populacional/figuras_a4/ (16 cm, 300 dpi, legenda abaixo, PNG +
.json irmão); tabelas e figuras atuais não são regravadas.

Uso:
  python scripts/processamento/dinamica_populacional_municipio.py --codigo-ibge 4322400
  python scripts/processamento/dinamica_populacional_municipio.py --codigo-ibge 4322400 --layout a4
"""

from __future__ import annotations

import argparse
import logging
import re

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import dinamica_populacional_comum as c  # noqa: E402
import layout_mapa as lm  # noqa: E402  (scripts/utils, via c)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

ANOS = [2000, 2010, 2022]
INTERVALOS = [(2000, 2010, 10.0), (2010, 2022, 12.0)]
GRUPOS = [f"{i} a {i + 4}" for i in range(0, 80, 5)] + ["80+"]

# Paleta (validada para daltonismo no conjunto de referência; ver docs do relatório)
AZUL, LARANJA = "#2a78d6", "#eb6834"
INK, INK2, MUTED, GRID = "#0b0b0b", "#52514e", "#898781", "#e1e0d9"
FONTE_TXT = "Fonte: IBGE, Censos Demográficos 2000, 2010 e 2022 (universo)."
FIGURAS_A4 = c.DOCS / "figuras_a4"


def piramides_a4(pir: pd.DataFrame, xmax: float, cod: str, fonte_geral: dict) -> list:
    """Edição A4 (rodada 11) das duas pirâmides: mesmos dados, cores e grupos; só a página muda.

    Página de 16 cm montada em polegadas a partir do topo: título, pirâmide(s), legenda em uma linha
    abaixo, rodapé com a fonte (até 3 linhas). Duas passadas: medir a altura e montar nela.
    """
    from matplotlib.patches import Patch

    FIGURAS_A4.mkdir(parents=True, exist_ok=True)
    W = lm.A4_LARGURA_CM * lm.CM
    y = np.arange(len(GRUPOS))
    feitos = []

    def barras(ax, ano, rotulos_grupos):
        d = pir[pir.ano == ano].set_index(["sexo", "grupo"]).pct_pop_total
        ax.barh(y, [-d.get(("Homens", g), 0) for g in GRUPOS], color=AZUL, height=0.82, edgecolor="#fcfcfb", linewidth=0.6)
        ax.barh(y, [d.get(("Mulheres", g), 0) for g in GRUPOS], color=LARANJA, height=0.82, edgecolor="#fcfcfb", linewidth=0.6)
        ax.set_xlim(-xmax, xmax)
        ax.set_ylim(-0.6, len(GRUPOS) - 0.4)
        ax.set_yticks(y, GRUPOS if rotulos_grupos else [""] * len(GRUPOS))
        tk = np.arange(-xmax, xmax + 0.01, 2)
        ax.set_xticks(tk, [f"{abs(v):.0f}" for v in tk])
        ax.tick_params(labelsize=lm.FS_LEGENDA, length=2.5, pad=2)
        ax.axvline(0, color="#c3c2b7", lw=0.6)
        ax.grid(axis="x", color=GRID, lw=0.5)
        ax.set_axisbelow(True)
        for sp in ("top", "right"):
            ax.spines[sp].set_visible(False)
        ax.set_xlabel("% da população total", fontsize=lm.FS_LEGENDA, labelpad=2)

    def pagina(nome, titulo, montar, handles, rodape, h_max_cm, descricao, Hmax=None):
        medir = Hmax is None  # 1ª passada mede a altura usada; 2ª monta a página nessa altura (sem recorte)
        Hmax = Hmax or lm.A4_ALTURA_MAX_CM * lm.CM
        fig = plt.figure(figsize=(W, Hmax), dpi=lm.A4_DPI)
        fig.canvas.draw()
        rend = fig.canvas.get_renderer()
        util_pt = (W - 2 * lm.MARGEM) * 72
        lin_tit = titulo.split("\n")
        if len(lin_tit) > 2 or any(lm._largura_pt(fig, rend, x, lm.FS_TITULO) > util_pt for x in lin_tit):
            raise ValueError(f"título não cabe em 2 linhas de 16 cm: {titulo!r}")
        tr = fig.dpi_scale_trans
        yt = Hmax - lm.TOPO
        fig.text(lm.MARGEM, yt, titulo, transform=tr, ha="left", va="top", fontsize=lm.FS_TITULO, color=INK, linespacing=1.25)
        yt -= lm._altura_linhas_in(len(lin_tit), lm.FS_TITULO, 1.25) + lm.GAP_BLOCO
        eixos = montar(fig, yt, Hmax)
        fig.canvas.draw()
        y_base = min(ax.get_tightbbox(rend).y0 for ax in eixos) / fig.dpi  # polegadas, inclui rótulos e título do eixo x
        leg = fig.legend(handles=handles, loc="upper left", ncol=len(handles), frameon=False, fontsize=lm.FS_LEGENDA,
                         borderpad=0, borderaxespad=0, columnspacing=1.6, handlelength=1.8, handletextpad=0.6,
                         bbox_to_anchor=(lm.MARGEM, y_base - lm.GAP_BLOCO), bbox_transform=tr)
        fig.canvas.draw()
        bl = leg.get_window_extent(rend)
        if bl.width / fig.dpi > W - 2 * lm.MARGEM:
            raise ValueError("legenda não cabe em uma linha")
        y_leg = bl.y0 / fig.dpi
        lin_rod = lm.quebrar(fig, rend, rodape, util_pt, lm.FS_RODAPE)
        if len(lin_rod) > lm.MAX_LINHAS_RODAPE:
            raise ValueError("rodapé com mais de 3 linhas")
        fig.text(lm.MARGEM, y_leg - lm.GAP_BLOCO, "\n".join(lin_rod), transform=tr, ha="left", va="top", fontsize=lm.FS_RODAPE,
                 color=MUTED, linespacing=1.25)
        y_fim = y_leg - lm.GAP_BLOCO - lm._altura_linhas_in(len(lin_rod), lm.FS_RODAPE, 1.25) - lm.BASE
        H = Hmax - y_fim
        if medir:
            plt.close(fig)
            return pagina(nome, titulo, montar, handles, rodape, h_max_cm, descricao, Hmax=H)
        if H > h_max_cm * lm.CM + 1e-6:
            raise ValueError(f"{nome}: altura {H / lm.CM:.1f} cm passa de {h_max_cm} cm")
        fig.canvas.draw()  # nada fora da página: textos dentro de [0, W] na horizontal e acima do corte
        for t in fig.texts + [leg] + [tl for ax in eixos for tl in ax.get_yticklabels() + ax.get_xticklabels()]:
            b = t.get_window_extent(rend)
            if b.width and (b.x0 < -0.5 or b.x1 > fig.bbox.width + 0.5 or b.y0 < y_fim * fig.dpi - 0.5):
                raise ValueError(f"{nome}: texto fora da página: {t}")
        destino = FIGURAS_A4 / f"{nome}.png"
        fig.savefig(destino, dpi=lm.A4_DPI, facecolor=lm.FUNDO)
        plt.close(fig)
        lm.gravar_meta_a4(destino, c.FIGURAS / f"{nome}.png", layout="a4", largura_cm=lm.A4_LARGURA_CM, altura_cm=round(H / lm.CM, 2),
                          dpi=lm.A4_DPI, legenda_colunas=len(handles), descricao=descricao, formatos=["png"],
                          status=c.STATUS_CONFERENCIA, **fonte_geral)
        logger.info("A4: %s (%.1f × %.1f cm)", destino.relative_to(c.RAIZ), lm.A4_LARGURA_CM, H / lm.CM)
        feitos.append(destino)

    leg_sexo = [Patch(color=AZUL, label="Homens (esquerda)"), Patch(color=LARANJA, label="Mulheres (direita)")]
    tit = ("Pirâmides etárias — Uruguaiana (RS), Censos 2000, 2010 e 2022 (mesma escala)" if cod == "4322400" else
           f"Pirâmides etárias — município {cod}, Censos 2000, 2010 e 2022 (mesma escala)")

    def lado_a_lado(fig, yt, Hmax):
        esq, gap, alt = 0.50, 0.24, 2.95  # esq: espaço dos grupos de idade (só na primeira)
        larg = (W - 0.10 - esq - 2 * gap) / 3  # 0,10 pol à direita: o último rótulo do eixo x não sai da folha
        h_ano = 9 * 1.3 / 72
        eixos = []
        for i, ano in enumerate(ANOS):
            x = esq + i * (larg + gap)
            fig.text(x, yt, str(ano), transform=fig.dpi_scale_trans, ha="left", va="top", fontsize=9, color=INK)
            ax = fig.add_axes([x / W, (yt - h_ano - alt) / Hmax, larg / W, alt / Hmax])
            barras(ax, ano, rotulos_grupos=i == 0)
            eixos.append(ax)
        return eixos

    pagina("piramide-etaria-lado-a-lado_ibge-censo_2000-2022_municipal", tit, lado_a_lado, leg_sexo,
           FONTE_TXT + " Grupos quinquenais, 80+ aberto; barras em % da população total do ano.", 11,
           "três pirâmides lado a lado, mesma escala horizontal; grupos de idade só na primeira")

    def sobreposta(fig, yt, Hmax):
        esq, alt = 0.50, 3.60
        ax = fig.add_axes([esq / W, (yt - alt) / Hmax, (W - esq - 0.10) / W, alt / Hmax])
        barras(ax, 2022, rotulos_grupos=True)
        d00 = pir[pir.ano == 2000].set_index(["sexo", "grupo"]).pct_pop_total
        for vals in ([-d00.get(("Homens", g), 0) for g in GRUPOS], [d00.get(("Mulheres", g), 0) for g in GRUPOS]):
            xs, ys = [], []
            for yi, v in zip(y, vals):
                xs += [v, v]
                ys += [yi - 0.5, yi + 0.5]  # contorno em degraus = 2000
            ax.plot(xs, ys, color=INK, lw=1.2)
        return [ax]

    pagina("piramide-etaria-sobreposta_ibge-censo_2000-2022_municipal", "Pirâmide etária sobreposta: 2000 (contorno) × 2022 (barras)",
           sobreposta, [Patch(color=AZUL, label="Homens 2022"), Patch(color=LARANJA, label="Mulheres 2022"),
                        plt.Line2D([], [], color=INK, lw=1.2, label="2000 (contorno)")], FONTE_TXT, 12,
           "pirâmide 2022 em barras com contorno de 2000, mesma escala")
    return feitos


def _idade_num(rotulo: str) -> int | None:
    """'Menos de 1 ano' -> 0; 'N anos'/'1 ano' -> N; '100 anos ou mais' -> 100; grupos -> None."""
    if rotulo == "Menos de 1 ano":
        return 0
    if rotulo == "100 anos ou mais":
        return 100
    m = re.fullmatch(r"(\d+) anos?", rotulo)
    return int(m.group(1)) if m else None


def idades_simples(codigo: str) -> pd.DataFrame:
    """Tabela longa ano x sexo x idade (0..100+) — universo."""
    a = c.sidra("sidra_tabela1552_2000-2010_municipio.csv")
    a = a[(a["Situação do domicílio"] == "Total")]
    b = c.sidra("sidra_tabela9514_2022_municipio.csv")
    df = pd.concat([a[["periodo", "Sexo", "Idade", "valor"]], b[["periodo", "Sexo", "Idade", "valor"]]])
    df["idade"] = df["Idade"].map(_idade_num)
    df = df.dropna(subset=["idade"])
    df["idade"] = df["idade"].astype(int)
    # tabela 1552 rotula "Homem"/"Mulher"; 9514 rotula "Homens"/"Mulheres"
    df["Sexo"] = df["Sexo"].replace({"Homem": "Homens", "Mulher": "Mulheres"})
    return df.rename(columns={"periodo": "ano", "Sexo": "sexo", "valor": "pop"})[["ano", "sexo", "idade", "pop"]]


def grupo_quinquenal(idade: int) -> str:
    return "80+" if idade >= 80 else f"{idade // 5 * 5} a {idade // 5 * 5 + 4}"


def mediana(idades: pd.Series, pops: pd.Series) -> float:
    """Idade mediana interpolada em idade simples (classe [x, x+1))."""
    o = np.argsort(idades.values)
    x, f = idades.values[o], pops.values[o]
    cum = np.cumsum(f)
    meio = cum[-1] / 2
    i = int(np.searchsorted(cum, meio))
    abaixo = cum[i - 1] if i > 0 else 0
    return float(x[i] + (meio - abaixo) / f[i])


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--codigo-ibge", default=c.CODIGO_IBGE_DEFAULT)
    p.add_argument("--layout", default="lateral", choices=lm.LAYOUTS, help="lateral (padrão: tabelas e figuras atuais) ou a4 (só as pirâmides A4)")
    a = p.parse_args()
    cod = a.codigo_ibge
    c.garantir_pastas()

    # ---------------- a) totais por sexo e situação + distritos
    t202 = c.sidra("sidra_tabela202_2000-2010_municipio-distrito.csv")
    t9923 = c.sidra("sidra_tabela9923_2022_municipio-distrito.csv")
    t9514 = c.sidra("sidra_tabela9514_2022_municipio.csv")
    mun202 = t202[t202.nivel == "N6"]
    linhas = []
    for ano in (2000, 2010):
        s = mun202[mun202.periodo == ano]
        v = lambda sx, st: float(s[(s.Sexo == sx) & (s["Situação do domicílio"] == st)].valor.iloc[0])  # noqa: E731
        linhas.append({"ano": ano, "total": v("Total", "Total"), "urbana": v("Total", "Urbana"), "rural": v("Total", "Rural"),
                       "homens": v("Homens", "Total"), "mulheres": v("Mulheres", "Total"),
                       "fonte": "SIDRA 202"})
    s = t9923[t9923.nivel == "N6"]
    tt = t9514[(t9514.Idade == "Total")]
    linhas.append({"ano": 2022, "total": float(s[s["Situação do domicílio"] == "Total"].valor.iloc[0]),
                   "urbana": float(s[s["Situação do domicílio"] == "Urbana"].valor.iloc[0]),
                   "rural": float(s[s["Situação do domicílio"] == "Rural"].valor.iloc[0]),
                   "homens": float(tt[tt.Sexo == "Homens"].valor.iloc[0]),
                   "mulheres": float(tt[tt.Sexo == "Mulheres"].valor.iloc[0]),
                   "fonte": "SIDRA 9923 (total, situação) e 9514 (sexo)"})
    tot = pd.DataFrame(linhas)
    tot["pct_urbana"] = (100 * tot.urbana / tot.total).round(2)
    tot["razao_sexos_h100m"] = (100 * tot.homens / tot.mulheres).round(1)

    var = []
    for a0, a1, t in INTERVALOS:
        r0, r1 = tot.set_index("ano").loc[a0], tot.set_index("ano").loc[a1]
        for col in ("total", "urbana", "rural", "homens", "mulheres"):
            var.append({"intervalo": f"{a0}-{a1}", "anos": t, "serie": col, "inicio": r0[col], "fim": r1[col],
                        "variacao_abs": r1[col] - r0[col], "variacao_pct": round(100 * (r1[col] / r0[col] - 1), 2),
                        "taxa_geom_anual_pct": round(100 * ((r1[col] / r0[col]) ** (1 / t) - 1), 3)})
    var = pd.DataFrame(var)

    dist = []
    for _, r in t202[(t202.nivel == "N10") & (t202.Sexo == "Total")].iterrows():
        dist.append({"cod_distrito": r.cod_localidade, "distrito": r.localidade.split(" - ")[0], "ano": r.periodo,
                     "situacao": r["Situação do domicílio"], "pop": r.valor})
    for _, r in t9923[t9923.nivel == "N10"].iterrows():
        dist.append({"cod_distrito": r.cod_localidade, "distrito": r.localidade.split(" - ")[0], "ano": r.periodo,
                     "situacao": r["Situação do domicílio"], "pop": r.valor})
    dist = pd.DataFrame(dist).pivot_table(index=["cod_distrito", "distrito", "situacao"], columns="ano", values="pop").reset_index()
    dist.columns = [str(x) for x in dist.columns]
    dist["var_pct_2000_2010"] = (100 * (dist["2010"] / dist["2000"] - 1)).round(2)
    dist["var_pct_2010_2022"] = (100 * (dist["2022"] / dist["2010"] - 1)).round(2)
    dist["tgca_pct_2010_2022"] = (100 * ((dist["2022"] / dist["2010"]) ** (1 / 12) - 1)).round(3)

    # ---------------- b) pirâmides (grupos quinquenais, 80+ aberto)
    ida = idades_simples(cod)
    ida = ida[ida.sexo.isin(["Homens", "Mulheres"])]
    ida["grupo"] = ida.idade.map(grupo_quinquenal)
    pir = ida.groupby(["ano", "sexo", "grupo"], as_index=False)["pop"].sum()
    pir["pct_pop_total"] = pir.groupby("ano")["pop"].transform(lambda s: 100 * s / s.sum()).round(3)
    pir["ordem"] = pir.grupo.map({g: i for i, g in enumerate(GRUPOS)})
    pir = pir.sort_values(["ano", "sexo", "ordem"]).drop(columns="ordem")

    # ---------------- c) indicadores
    ind = []
    for ano in ANOS:
        d = ida[ida.ano == ano]
        T = d["pop"].sum()
        j = d[d.idade <= 14]["pop"].sum()
        adu = d[(d.idade >= 15) & (d.idade <= 59)]["pop"].sum()
        ido = d[d.idade >= 60]["pop"].sum()
        i80 = d[d.idade >= 80]["pop"].sum()
        tot_idade = d.groupby("idade")["pop"].sum()
        h, m = d[d.sexo == "Homens"]["pop"].sum(), d[d.sexo == "Mulheres"]["pop"].sum()
        ind.append({"ano": ano, "pop_soma_idades": T, "pct_0_14": 100 * j / T, "pct_15_59": 100 * adu / T,
                    "pct_60_mais": 100 * ido / T, "pct_80_mais": 100 * i80 / T,
                    "indice_envelhecimento_60_por_0_14": 100 * ido / j,
                    "razao_dependencia_total": 100 * (j + ido) / adu, "razao_dependencia_jovem": 100 * j / adu,
                    "razao_dependencia_idosa": 100 * ido / adu, "razao_sexos_h100m": 100 * h / m,
                    "idade_mediana_anos": mediana(pd.Series(tot_idade.index), tot_idade.reset_index(drop=True))})
    ind = pd.DataFrame(ind).round(2)

    # ---------------- d) domicílios
    t185 = c.sidra("sidra_tabela185_2000-2010_municipio-distrito.csv")
    t185 = t185[(t185.nivel == "N6") & (t185["Situação do domicílio"] == "Total") & (t185.variavel_id == 96)]
    t4712 = c.sidra("sidra_tabela4712_2022_municipio.csv")
    m2000 = c.agregados_2000("Morador", cod, ["V0239"])
    t3451 = c.sidra("sidra_tabela3451_2010_municipio-distrito.csv")
    t3451 = t3451[(t3451.nivel == "N6") & (t3451["Situação do domicílio"] == "Total")]
    d1_22 = c.agregados_2022("caracteristicas_domicilio1", cod, ["V00001", "V00005", "V00017"])
    dom = []
    for ano in (2000, 2010):
        x = t185[t185.periodo == ano]
        dpp = float(x[x["Número de moradores"] == "Total"].valor.iloc[0])
        um = float(x[x["Número de moradores"] == "1 morador"].valor.iloc[0])
        if ano == 2000:
            mor, fmor = float(m2000.V0239.sum()), "agregados por setor 2000, Morador V0239 (soma dos setores)"
        else:
            mor, fmor = float(t3451[t3451.variavel_id == 137].valor.iloc[0]), "SIDRA 3451"
        dom.append({"ano": ano, "conceito": "domicílios particulares permanentes (ocupados)", "domicilios": dpp,
                    "moradores": mor, "moradores_por_domicilio": round(mor / dpp, 2), "dom_1_morador": um,
                    "pct_dom_1_morador": round(100 * um / dpp, 2),
                    "fonte_domicilios": "SIDRA 185", "fonte_moradores": fmor, "fonte_1_morador": "SIDRA 185"})
    dpp22 = float(t4712[t4712.variavel_id == 381].valor.iloc[0])
    mor22 = float(t4712[t4712.variavel_id == 382].valor.iloc[0])
    um22 = float(d1_22.V00017.sum())
    n_sig = int(d1_22.V00017_bruto.eq("X").sum())
    dom.append({"ano": 2022, "conceito": "domicílios particulares permanentes ocupados", "domicilios": dpp22, "moradores": mor22,
                "moradores_por_domicilio": round(mor22 / dpp22, 2), "dom_1_morador": um22,
                "pct_dom_1_morador": round(100 * um22 / dpp22, 2), "fonte_domicilios": "SIDRA 4712", "fonte_moradores": "SIDRA 4712",
                "fonte_1_morador": f"agregados por setor 2022, V00017 (soma; {n_sig} setores com sigilo 'X' ficam fora — valor mínimo)"})
    dom = pd.DataFrame(dom)

    # ---------------- conferência: SIDRA x soma dos setores x soma das idades
    b22 = c.agregados_2022("basico", cod, ["V0001", "V0007"])
    p10 = c.agregados_2010("Pessoa03", cod, ["V001"])
    b10 = c.agregados_2010("Basico", cod, ["V001", "V002"])
    m00 = c.agregados_2000("Morador", cod, ["V0237"])
    d00 = c.agregados_2000("Domicilio", cod, ["V0003"])
    conf = []
    sid = tot.set_index("ano")
    for ano, setor, fonte_setor in ((2000, m00.V0237.sum(), "Morador V0237"), (2010, p10.V001.sum(), "Pessoa03 V001"),
                                    (2022, b22.V0001.sum(), "basico V0001")):
        conf.append({"ano": ano, "item": "população residente", "sidra": sid.loc[ano, "total"], "soma_setores": setor,
                     "fonte_setores": fonte_setor, "dif_setores_menos_sidra": setor - sid.loc[ano, "total"]})
        conf.append({"ano": ano, "item": "população (soma das idades simples)", "sidra": sid.loc[ano, "total"],
                     "soma_setores": ind.set_index("ano").loc[ano, "pop_soma_idades"], "fonte_setores": "SIDRA 1552/9514 (idades)",
                     "dif_setores_menos_sidra": ind.set_index("ano").loc[ano, "pop_soma_idades"] - sid.loc[ano, "total"]})
        conf.append({"ano": ano, "item": "homens + mulheres", "sidra": sid.loc[ano, "total"],
                     "soma_setores": sid.loc[ano, "homens"] + sid.loc[ano, "mulheres"], "fonte_setores": "SIDRA (sexo)",
                     "dif_setores_menos_sidra": sid.loc[ano, "homens"] + sid.loc[ano, "mulheres"] - sid.loc[ano, "total"]})
    for ano, setor, fonte_setor in ((2000, d00.V0003.sum(), "Domicilio V0003"), (2010, b10.V001.sum(), "Basico V001"),
                                    (2022, d1_22.V00001.sum(), "caracteristicas_domicilio1 V00001")):
        conf.append({"ano": ano, "item": "domicílios particulares permanentes (ocupados)",
                     "sidra": dom.set_index("ano").loc[ano, "domicilios"], "soma_setores": setor, "fonte_setores": fonte_setor,
                     "dif_setores_menos_sidra": setor - dom.set_index("ano").loc[ano, "domicilios"]})
    conf.append({"ano": 2010, "item": "moradores em DPP", "sidra": dom.set_index("ano").loc[2010, "moradores"],
                 "soma_setores": b10.V002.sum(), "fonte_setores": "Basico V002",
                 "dif_setores_menos_sidra": b10.V002.sum() - dom.set_index("ano").loc[2010, "moradores"]})
    conf.append({"ano": 2022, "item": "moradores em DPPO", "sidra": mor22, "soma_setores": d1_22.V00005.sum(),
                 "fonte_setores": "caracteristicas_domicilio1 V00005", "dif_setores_menos_sidra": d1_22.V00005.sum() - mor22})
    conf = pd.DataFrame(conf)
    for d in (dist,):
        for ano in ("2000", "2010", "2022"):
            soma = d[d.situacao == "Total"][ano].sum()
            conf = pd.concat([conf, pd.DataFrame([{"ano": int(ano), "item": "soma dos distritos (SIDRA N10)", "sidra": sid.loc[int(ano), "total"],
                                                   "soma_setores": soma, "fonte_setores": "SIDRA 202/9923 nível distrito",
                                                   "dif_setores_menos_sidra": soma - sid.loc[int(ano), "total"]}])])

    # ---------------- gravação das tabelas
    fonte_geral = {"codigo_ibge": cod, "script": "scripts/processamento/dinamica_populacional_municipio.py"}
    saidas = {
        "populacao-sexo-situacao_ibge-censo_2000-2022_municipal.csv": (tot, "População residente total, urbana, rural, homens e mulheres; % urbana; razão de sexos", "SIDRA 202 (2000, 2010), 9923 e 9514 (2022)"),
        "populacao-variacao_ibge-censo_2000-2022_municipal.csv": (var, "Variação absoluta, % e taxa geométrica anual por intervalo intercensitário (10 e 12 anos)", "derivado da tabela populacao-sexo-situacao"),
        "populacao-distritos_ibge-censo_2000-2022_distrito.csv": (dist, "População por distrito e situação; variação % e taxa geométrica anual", "SIDRA 202 (N10) e 9923 (N10)"),
        "piramide-etaria_ibge-censo_2000-2022_municipal.csv": (pir, "População por sexo e grupo quinquenal (80+ aberto), em número e % da população total do ano", "SIDRA 1552 (2000, 2010; universo, idade simples) e 9514 (2022)"),
        "indicadores-etarios_ibge-censo_2000-2022_municipal.csv": (ind, "Indicadores etários; índice de envelhecimento = 60+/0–14 ×100; razões de dependência por 100 pessoas de 15–59; razão de sexos = H/M ×100; idade mediana interpolada na idade simples", "SIDRA 1552 e 9514"),
        "domicilios_ibge-censo_2000-2022_municipal.csv": (dom, "Domicílios particulares permanentes ocupados, moradores, moradores por domicílio, domicílios com um morador", "SIDRA 185, 3451, 4712; agregados por setor quando indicado"),
        "conferencia-totais_ibge-censo_2000-2022_municipal.csv": (conf, "Conferência dos totais: SIDRA × soma dos setores × soma das idades × soma dos distritos", "SIDRA e agregados por setor de cada censo"),
    }
    if a.layout == "a4":  # edição A4: só as duas pirâmides, a partir das tabelas calculadas em memória
        plt.rcParams.update({"font.family": "DejaVu Sans", "axes.edgecolor": "#c3c2b7", "axes.labelcolor": INK2,
                             "xtick.color": INK2, "ytick.color": INK2})
        piramides_a4(pir, float(np.ceil(pir.pct_pop_total.max() + 0.5)), cod, fonte_geral)
        return
    for nome, (df, desc, fonte) in saidas.items():
        caminho = c.TABELAS / nome
        df.to_csv(caminho, index=False)
        c.gravar_meta(caminho, descricao=desc, fonte=fonte, **fonte_geral,
                      transformacao="seleção das categorias da tabela, soma e razões; sigilo/indisponível = ausente, nunca zero")
        logger.info("Tabela: %s", caminho.relative_to(c.RAIZ))

    # ---------------- gráficos
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 9, "axes.edgecolor": "#c3c2b7",
                         "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK2})
    xmax = float(np.ceil(pir.pct_pop_total.max() + 0.5))

    def eixo_piramide(ax, ano, titulo=True):
        d = pir[pir.ano == ano].set_index(["sexo", "grupo"]).pct_pop_total
        y = np.arange(len(GRUPOS))
        h = [d.get(("Homens", g), 0) for g in GRUPOS]
        m = [d.get(("Mulheres", g), 0) for g in GRUPOS]
        ax.barh(y, [-v for v in h], color=AZUL, height=0.82, edgecolor="#fcfcfb", linewidth=1)
        ax.barh(y, m, color=LARANJA, height=0.82, edgecolor="#fcfcfb", linewidth=1)
        ax.set_xlim(-xmax, xmax)
        ax.set_yticks(y, GRUPOS)
        tk = np.arange(-xmax, xmax + 0.01, 2)
        ax.set_xticks(tk, [f"{abs(v):.0f}" for v in tk])
        ax.axvline(0, color="#c3c2b7", lw=0.8)
        ax.grid(axis="x", color=GRID, lw=0.6)
        ax.set_axisbelow(True)
        for s in ("top", "right"):
            ax.spines[s].set_visible(False)
        if titulo:
            ax.set_title(str(ano), color=INK, fontsize=11, loc="left")
        ax.set_xlabel("% da população total")

    fig, axs = plt.subplots(1, 3, figsize=(12, 5.2), sharey=True)
    for ax, ano in zip(axs, ANOS):
        eixo_piramide(ax, ano)
    from matplotlib.patches import Patch
    fig.legend(handles=[Patch(color=AZUL, label="Homens (esquerda)"), Patch(color=LARANJA, label="Mulheres (direita)")],
               loc="upper right", ncol=2, frameon=False, bbox_to_anchor=(0.99, 0.985))
    fig.suptitle("Pirâmides etárias — Uruguaiana (RS), Censos 2000, 2010 e 2022 (mesma escala)" if cod == "4322400" else
                 f"Pirâmides etárias — município {cod}, Censos 2000, 2010 e 2022 (mesma escala)", x=0.01, ha="left", color=INK, fontsize=12)
    fig.text(0.01, 0.01, FONTE_TXT + " Grupos quinquenais, 80+ aberto; barras em % da população total do ano.", color=MUTED, fontsize=8)
    fig.tight_layout(rect=(0, 0.04, 1, 0.95))
    nome = "piramide-etaria-lado-a-lado_ibge-censo_2000-2022_municipal"
    for ext in ("png", "svg"):
        fig.savefig(c.FIGURAS / f"{nome}.{ext}", dpi=200, facecolor="#fcfcfb")
    plt.close(fig)
    c.gravar_meta(c.FIGURAS / f"{nome}.png", descricao="três pirâmides lado a lado, mesma escala horizontal", fonte="tabela piramide-etaria_ibge-censo_2000-2022_municipal.csv", **fonte_geral, formatos=["png", "svg"])

    fig, ax = plt.subplots(figsize=(6.5, 5.6))
    d00 = pir[pir.ano == 2000].set_index(["sexo", "grupo"]).pct_pop_total
    eixo_piramide(ax, 2022, titulo=False)
    y = np.arange(len(GRUPOS))
    h0 = [-d00.get(("Homens", g), 0) for g in GRUPOS]
    m0 = [d00.get(("Mulheres", g), 0) for g in GRUPOS]
    # contorno em degraus = 2000; barras cheias = 2022
    for vals in (h0, m0):
        xs, ys = [], []
        for yi, v in zip(y, vals):
            xs += [v, v]
            ys += [yi - 0.5, yi + 0.5]  # limites contíguos -> degraus horizontais
        ax.plot(xs, ys, color=INK, lw=1.6, drawstyle="default")
    ax.set_title("Pirâmide etária sobreposta: 2000 (contorno) × 2022 (barras)", loc="left", color=INK, fontsize=11, pad=24)
    from matplotlib.patches import Patch
    ax.legend(handles=[Patch(color=AZUL, label="Homens 2022"), Patch(color=LARANJA, label="Mulheres 2022"),
                       plt.Line2D([], [], color=INK, lw=1.6, label="2000 (contorno)")],
              loc="lower left", bbox_to_anchor=(0, 1.0), ncol=3, frameon=False, fontsize=8.5)
    fig.text(0.01, 0.01, FONTE_TXT, color=MUTED, fontsize=8)
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    nome = "piramide-etaria-sobreposta_ibge-censo_2000-2022_municipal"
    for ext in ("png", "svg"):
        fig.savefig(c.FIGURAS / f"{nome}.{ext}", dpi=200, facecolor="#fcfcfb")
    plt.close(fig)
    c.gravar_meta(c.FIGURAS / f"{nome}.png", descricao="pirâmide 2022 em barras com contorno de 2000, mesma escala", fonte="tabela piramide-etaria_ibge-censo_2000-2022_municipal.csv", **fonte_geral, formatos=["png", "svg"])

    # população urbana e rural (barras empilhadas, 2 px de separação)
    fig, ax = plt.subplots(figsize=(6.5, 3.6))
    x = np.arange(3)
    ax.bar(x, tot.urbana / 1000, color=AZUL, width=0.5, edgecolor="#fcfcfb", linewidth=2, label="Urbana")
    ax.bar(x, tot.rural / 1000, bottom=tot.urbana / 1000, color=LARANJA, width=0.5, edgecolor="#fcfcfb", linewidth=2, label="Rural")
    for xi, r in zip(x, tot.itertuples()):
        ax.text(xi, r.total / 1000 + 1.5, f"{r.total:,.0f}".replace(",", "."), ha="center", color=INK, fontsize=9)
    ax.set_xticks(x, [str(a_) for a_ in ANOS])
    ax.set_ylabel("mil habitantes")
    ax.set_ylim(0, tot.total.max() / 1000 * 1.15)
    ax.grid(axis="y", color=GRID, lw=0.6)
    ax.set_axisbelow(True)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.legend(frameon=False, loc="upper right", ncol=2)
    ax.set_title("População residente por situação do domicílio", loc="left", color=INK, fontsize=11)
    fig.text(0.01, 0.01, "Fonte: IBGE, SIDRA 202 e 9923.", color=MUTED, fontsize=8)
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    nome = "populacao-situacao_ibge-censo_2000-2022_municipal"
    for ext in ("png", "svg"):
        fig.savefig(c.FIGURAS / f"{nome}.{ext}", dpi=200, facecolor="#fcfcfb")
    plt.close(fig)
    c.gravar_meta(c.FIGURAS / f"{nome}.png", descricao="barras empilhadas urbana/rural com total rotulado", fonte="tabela populacao-sexo-situacao", **fonte_geral, formatos=["png", "svg"])

    print(tot.to_string()); print(var.to_string()); print(dist.to_string()); print(ind.T.to_string()); print(dom.to_string()); print(conf.to_string())


if __name__ == "__main__":
    main()
