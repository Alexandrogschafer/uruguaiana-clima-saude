"""
Exposição por ENDEREÇOS em cenários de inundação de qualquer fonte de manchas
(vetor oficial ou camada vetorizada a partir de figura).

Mesmo método do estudo atual (exposicao_inundacao_enderecos.py):
  B1  endereço exposto ao cenário X = ponto dentro da união das manchas dos
      cenários até X (contagem CUMULATIVA, na ordem dos cenários);
  B2  população ESTIMADA: população do setor 2022 repartida igualmente entre
      os endereços de domicílio particular do setor; sensibilidade pela grade.
As estimativas por endereço NÃO são recalculadas aqui: vêm da camada de pontos
que o estudo atual grava (data/processed/exposicao_inundacao/), fora do git.

Dois modos:
  1) uma fonte — recebe o arquivo das manchas, a camada, o atributo do cenário
     e um nome curto da fonte:
       python scripts/processamento/exposicao_inundacao_cenarios.py \
           --manchas ARQUIVO.gpkg --camada CAMADA --atributo ATRIBUTO --fonte NOME \
           [--cenarios v1 v2 ...] [--dominio DOMINIO.gpkg]
  2) todas as fontes de um arquivo de fontes e a síntese (tabela longa, tabela
     cruzada, observado × calculado, figura):
       python scripts/processamento/exposicao_inundacao_cenarios.py \
           --fontes scripts/vetorizacao/fontes_inundacao.json

Tudo é gravado em data/processed/exposicao_inundacao_cenarios/ (fora do git):
deriva de camadas vetorizadas de figura, que não são dado oficial. Fontes na
situação "extraído" (pendentes de conferência) levam a marca "preliminar".
Os valores ficam sem arredondar; toda exibição arredonda a partir deles.
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

import dinamica_populacional_comum as c
import exposicao_inundacao_enderecos as ee  # caminhos das entradas do estudo atual (pontos, unidades)

logger = logging.getLogger(__name__)

SCRIPT = "scripts/processamento/exposicao_inundacao_cenarios.py"
SAIDA = c.RAIZ / "data" / "processed" / "exposicao_inundacao_cenarios"
ARQ_PONTOS = ee.CAMADAS / "enderecos-exposicao-inundacao_sgb-ibge-cnefe_2022_pontos.gpkg"
MARCA = {"extraído": "preliminar (camada pendente de conferência)", "conferido": "camada conferida; extraída de figura, não oficial",
         "oficial": "vetor oficial"}


# ---------------------------------------------------------------- fontes e cenários
def ler_fontes(caminho: Path) -> dict:
    cfg = json.loads(Path(caminho).read_text(encoding="utf-8"))
    cfg["por_id"] = {f["id"]: f for f in cfg["fontes"]}
    return cfg


def carregar_cenarios(arquivo: Path, camada: str, atributo: str, valores: list | None = None, atributo_tr: str | None = None) -> gpd.GeoDataFrame:
    """Uma linha por cenário, na ordem pedida, com a geometria CUMULATIVA (união dos cenários até ele).

    Geometria corrigida com buffer(0) e dissolvida pelo atributo, como no estudo atual. Sem 'valores', entram
    todos os cenários da camada: em ordem crescente, se o atributo for numérico; senão, na ordem do arquivo.
    """
    g = gpd.read_file(c.RAIZ / arquivo if not Path(arquivo).is_absolute() else arquivo, layer=camada).to_crs(c.CRS_PADRAO)
    g["geometry"] = g.geometry.buffer(0)
    ordem_arquivo = list(dict.fromkeys(g[atributo].tolist()))
    d = g.dissolve(by=atributo, aggfunc="first")
    numerico = pd.api.types.is_numeric_dtype(g[atributo])
    ordem = sorted(ordem_arquivo) if numerico else ordem_arquivo
    acum, linhas = None, []
    for v in ordem:  # a união segue a ordem completa da camada, mesmo que só alguns cenários sejam pedidos
        acum = d.loc[v, "geometry"] if acum is None else acum.union(d.loc[v, "geometry"])
        tr = float(v) if atributo_tr == atributo else (float(d.loc[v, atributo_tr]) if atributo_tr else np.nan)
        linhas.append({"valor": v, "tr_atributo": tr, "geom_propria": d.loc[v, "geometry"], "geometry": acum})  # geom_propria: só a mancha do cenário
    out = gpd.GeoDataFrame(linhas, crs=c.CRS_PADRAO)
    if valores is not None:
        chave = out.valor.astype(str)
        faltam = [v for v in valores if str(v) not in set(chave)]
        if faltam:
            raise ValueError(f"cenário(s) ausente(s) em {arquivo}:{camada}: {faltam}")
        out = out.set_index(chave).loc[[str(v) for v in valores]].reset_index(drop=True)
    return out


def cenarios_da_fonte(f: dict) -> gpd.GeoDataFrame:
    """Cenários de uma fonte do arquivo de fontes, com rótulo, tempo de retorno, cota e altitude."""
    g = carregar_cenarios(f["arquivo"], f["camada"], f["atributo"], [x["valor"] for x in f["cenarios"]], f.get("atributo_tr"))
    for col in ("rotulo", "cota_cm", "altitude_m"):
        g[col] = [x.get(col) for x in f["cenarios"]]
    g["tr_anos"] = [x.get("tr_anos", t if not np.isnan(t) else None) for x, t in zip(f["cenarios"], g.tr_atributo)]
    g["cenario"] = [f"{f['id']} {r}" for r in g.rotulo]
    g["fonte"] = f["id"]
    return g


# ---------------------------------------------------------------- exposição de uma fonte
def expor(cen: gpd.GeoDataFrame, fonte: str, dominio=None, origem: dict | None = None, marca: str = "", gravar: bool = True) -> tuple[pd.DataFrame, gpd.GeoDataFrame]:
    """Tabela por cenário e camada de pontos (uma coluna por cenário) de uma fonte; grava as duas.

    gravar=False só devolve a tabela e os pontos, sem gravar nada (nenhuma camada de pontos vai para o disco)."""
    if gravar:
        SAIDA.mkdir(parents=True, exist_ok=True)
    if not ARQ_PONTOS.exists():
        raise FileNotFoundError(f"{ARQ_PONTOS} ausente — rode scripts/processamento/exposicao_inundacao_enderecos.py")
    pts = gpd.read_file(ARQ_PONTOS, columns=["COD_UNICO_ENDERECO", "setor_2022", "bairro", "situacao", "pop_est_setor", "pop_est_grade"]).to_crs(c.CRS_PADRAO)
    unid = gpd.read_file(ee.ARQ_UNIDADES).to_crs(c.CRS_PADRAO)
    pts["dentro_do_dominio"] = pts.within(dominio) if dominio is not None else True
    n_mun = len(pts)
    pts["menor_cenario"] = None
    cols = []
    for i, r in enumerate(cen.itertuples()):
        col = f"exp_{i + 1:02d}"
        pts[col] = pts.within(r.geometry)  # geometria já cumulativa
        cols.append(col)
    for col, r in reversed(list(zip(cols, cen.itertuples()))):
        pts.loc[pts[col], "menor_cenario"] = r.cenario if "cenario" in cen.columns else str(r.valor)
    linhas = []
    for col, r in zip(cols, cen.itertuples()):
        nome = r.cenario if "cenario" in cen.columns else str(r.valor)
        e = pts[pts[col]]
        ed = e[e.dentro_do_dominio]
        u = unid[unid.within(r.geometry)]
        linhas.append({"fonte": fonte, "cenario": nome, "area_mancha_km2": r.geometry.area / 1e6,
                       "enderecos_dominio": len(ed), "pop_estimada_setor_dominio": float(ed.pop_est_setor.sum()), "pop_estimada_grade_dominio": float(ed.pop_est_grade.sum()),
                       "enderecos_total": len(e), "pop_estimada_setor_total": float(e.pop_est_setor.sum()), "pop_estimada_grade_total": float(e.pop_est_grade.sum()),
                       "pct_enderecos_municipio_dominio": 100 * len(ed) / n_mun, "pct_enderecos_municipio_total": 100 * len(e) / n_mun,
                       "enderecos_menor_cenario_este_total": int((pts.menor_cenario == nome).sum()),
                       "unidades_saude_na_mancha": len(u), "unidades_saude_nomes": "; ".join(f"{x.rotulo} {x.nome}" for x in u.itertuples()) or "nenhuma",
                       "marca": marca})
    t = pd.DataFrame(linhas)
    if not gravar:
        return t, pts
    base = dict(codigo_ibge=ARGS.codigo_ibge, crs=c.CRS_PADRAO, status=c.STATUS_CONFERENCIA, ligado_ao_portal=False, script=SCRIPT, fonte_das_manchas=origem or fonte,
                marca=marca or None, enderecos_do_municipio=n_mun, fora_do_git="data/processed/ é ignorado; deriva de camada não oficial — não publicar",
                metodo="B1 cumulativo (ponto dentro da união das manchas dos cenários até X); B2 população do setor 2022 repartida entre os endereços do setor "
                       "(estimativa), com a grade como sensibilidade — valores por endereço lidos da camada do estudo atual",
                dominio="colunas *_dominio: só endereços dentro do domínio comum das fontes; *_total: toda a mancha" if dominio is not None else "sem domínio: *_dominio = *_total")
    arq = SAIDA / f"enderecos-expostos-por-cenario_{fonte}-ibge-cnefe_2022_municipal.csv"
    t.round(6).to_csv(arq, index=False)
    c.gravar_meta(arq, descricao="endereços de domicílio particular (CNEFE 2022) e população estimada expostos a cada cenário da fonte; unidades de saúde (ESF/UBS) dentro da mancha",
                  cenarios={col: (r.cenario if "cenario" in cen.columns else str(r.valor)) for col, r in zip(cols, cen.itertuples())}, **base)
    out = SAIDA / f"enderecos-exposicao-cenarios_{fonte}-ibge-cnefe_2022_pontos.gpkg"
    pts.to_file(out, driver="GPKG", layer="enderecos_exposicao")
    c.gravar_meta(out, descricao="endereços com a exposição (cumulativa) a cada cenário da fonte, menor cenário que atinge e população estimada",
                  cenarios={col: (r.cenario if "cenario" in cen.columns else str(r.valor)) for col, r in zip(cols, cen.itertuples())}, **base)
    logger.info("%s: %d cenários -> %s", fonte, len(cen), arq.relative_to(c.RAIZ))
    return t, pts


# ---------------------------------------------------------------- síntese das fontes
def num(v, casas: int = 0) -> str:
    return f"{v:,.{casas}f}".replace(",", "§").replace(".", ",").replace("§", ".").replace("-", "−")


def sintese(cfg: dict, tabs: dict, pontos: dict, cens: dict) -> None:
    base = dict(codigo_ibge=ARGS.codigo_ibge, status=c.STATUS_CONFERENCIA, ligado_ao_portal=False, script=SCRIPT, arquivo_de_fontes=str(ARGS.fontes),
                fora_do_git="data/processed/ é ignorado; deriva de camadas não oficiais — não publicar",
                marca="fontes na situação 'extraído' são preliminares (pendentes de conferência): " + ", ".join(f["id"] for f in cfg["fontes"] if f["situacao"] == "extraído"))
    # a) tabela longa
    partes = []
    for f in cfg["fontes"]:
        t, g = tabs[f["id"]].copy(), cens[f["id"]]
        t.insert(1, "tipo", f["tipo"])
        t.insert(2, "situacao_da_camada", f["situacao"])
        for col in ("tr_anos", "cota_cm", "altitude_m"):
            t[col] = pd.to_numeric(pd.Series(g[col].values), errors="coerce").values
        partes.append(t)
    ta = pd.concat(partes, ignore_index=True)
    arq = SAIDA / "enderecos-expostos-por-cenario_fontes-inundacao_2022_municipal.csv"
    ta.round(6).to_csv(arq, index=False)
    c.gravar_meta(arq, descricao="tabela longa: uma linha por fonte e cenário — endereços expostos e população estimada (por setor e pela grade) dentro do domínio comum e no total da mancha; "
                  "% dos endereços do município; unidades de saúde dentro da mancha", **base)

    # b) tabela cruzada dentro do domínio: menor cenário da fonte das linhas × menor cenário da fonte das colunas
    fl, fc = cfg["cruzamento_enderecos"]["linhas"], cfg["cruzamento_enderecos"]["colunas"]
    a, b = pontos[fl], pontos[fc]
    d = pd.DataFrame({"linha": a.menor_cenario.fillna(f"{fl} nenhum"), "coluna": b.menor_cenario.fillna(f"{fc} nenhum"),
                      "pop": a.pop_est_setor, "dom": a.dentro_do_dominio})[lambda x: x.dom]
    ol, oc = list(cens[fl].cenario) + [f"{fl} nenhum"], list(cens[fc].cenario) + [f"{fc} nenhum"]
    tb = (d.groupby(["linha", "coluna"]).agg(enderecos=("pop", "size"), pop_estimada_setor=("pop", "sum"))
          .reindex(pd.MultiIndex.from_product([ol, oc], names=["linha", "coluna"]), fill_value=0).reset_index())
    tb = tb.rename(columns={"linha": f"menor_cenario_{fl}", "coluna": f"menor_cenario_{fc}"})
    arq = SAIDA / f"enderecos-cruzamento-menor-cenario_{fl.lower()}-x-{fc.lower()}_2022_dominio-comum.csv"
    tb.round(6).to_csv(arq, index=False)
    c.gravar_meta(arq, descricao=f"endereços dentro do domínio comum pelo menor cenário de {fl} que os atinge × menor cenário de {fc} que os atinge (contagem e população estimada pelo setor)", **base)

    # c) observado × calculado, dentro do domínio, por bairro
    def flag(nome):
        fid = next(i for i in cens if nome in list(cens[i].cenario))
        k = list(cens[fid].cenario).index(nome)
        return pontos[fid][f"exp_{k + 1:02d}"]

    obs_nome = cfg["observado_x_calculado"]["observado"]
    obs, ref = flag(obs_nome), pontos[fl]
    lin_c, lin_b = [], []
    for calc_nome in cfg["observado_x_calculado"]["calculados"]:
        cal = flag(calc_nome)
        for rot, m in ((f"em {obs_nome} e fora de {calc_nome}", obs & ~cal), (f"em {calc_nome} e fora de {obs_nome}", cal & ~obs), (f"em {obs_nome} e em {calc_nome}", obs & cal)):
            md = m & ref.dentro_do_dominio
            lin_c.append({"comparacao": f"{obs_nome} × {calc_nome}", "grupo": rot, "enderecos_dominio": int(md.sum()), "pop_estimada_setor_dominio": float(ref.pop_est_setor[md].sum()),
                          "enderecos_total": int(m.sum())})
            bb = ref[md].groupby("bairro").agg(enderecos=("pop_est_setor", "size"), pop_estimada_setor=("pop_est_setor", "sum")).sort_values("enderecos", ascending=False)
            lin_b.append(bb.reset_index().assign(comparacao=f"{obs_nome} × {calc_nome}", grupo=rot))
    tc, tcb = pd.DataFrame(lin_c), pd.concat(lin_b)[["comparacao", "grupo", "bairro", "enderecos", "pop_estimada_setor"]]
    for t, nome, desc in ((tc, "enderecos-observado-x-calculado_fontes-inundacao_2022_dominio-comum.csv", "endereços atingidos pela cheia observada e fora do cenário calculado, e o contrário"),
                          (tcb, "enderecos-observado-x-calculado_fontes-inundacao_2022_bairro.csv", "o mesmo, por bairro (IBGE 2022, atributo do setor do endereço), dentro do domínio comum")):
        arq = SAIDA / nome
        t.round(6).to_csv(arq, index=False)
        c.gravar_meta(arq, descricao=desc, **base)

    # d) figura: endereços expostos dentro do domínio × tempo de retorno
    figura_tr(cfg, ta, base)
    pd.set_option("display.width", 250)
    pd.set_option("display.max_columns", 30)
    pd.set_option("display.max_colwidth", 60)
    print(ta.drop(columns=["marca"]).round(1).to_string(index=False))
    print(tb.pivot(index=tb.columns[0], columns=tb.columns[1], values="enderecos").reindex(index=ol, columns=oc).to_string())
    print(tb.pivot(index=tb.columns[0], columns=tb.columns[1], values="pop_estimada_setor").reindex(index=ol, columns=oc).round(0).to_string())
    print(tc.round(1).to_string(index=False))
    print(tcb.round(1).to_string(index=False))


def figura_tr(cfg: dict, ta: pd.DataFrame, base: dict) -> None:
    """Edição A4 (16 cm, 300 dpi, legenda abaixo): endereços expostos dentro do domínio por tempo de retorno (escala log)."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D

    import dinamica_populacional_serie_longa as sl  # página A4 e rótulos sem sobreposição
    import layout_mapa as lm

    plt.rcParams.update({"font.family": "DejaVu Sans", "axes.edgecolor": "#c3c2b7", "axes.labelcolor": sl.INK2, "xtick.color": sl.INK2, "ytick.color": sl.INK2})
    est = sl.Estilo("a4-cenarios", lm.A4_LARGURA_CM * lm.CM, lm.A4_DPI, lm.FS_TITULO, lm.FS_LEGENDA, lm.FS_ROTULO_MIN, lm.FS_RODAPE, SAIDA, ("png",), True)
    cores = ["#2a78d6", "#eb6834", "#1b7837", "#7b3294"]
    marc = ["o", "s", "^", "D"]
    com_tr = [f for f in cfg["fontes"] if f["na_comparacao"] and ta[(ta.fonte == f["id"])].tr_anos.notna().all()]
    sem_tr = [f for f in cfg["fontes"] if f["na_comparacao"] and f not in com_tr]
    prel = lambda f: " (preliminar)" if f["situacao"] == "extraído" else ""  # noqa: E731
    handles = []

    def montar(fig, yt, Hmax, est):
        W = est.largura
        esq, dirt, alt = 0.62, 0.12, 3.3
        ax = fig.add_axes([esq / W, (yt - alt) / Hmax, (W - esq - dirt) / W, alt / Hmax])
        ax.set_xscale("log")
        ax.set_yscale("log")  # contagens de 4 a mais de 10 mil: em escala linear os pontos de TR baixo ficam uns sobre os outros
        itens = []
        for f, cor, mk in zip(com_tr, cores, marc):
            d = ta[ta.fonte == f["id"]].sort_values("tr_anos")
            ax.plot(d.tr_anos, d.enderecos_dominio, "-", color=cor, lw=1.3, marker=mk, ms=4.4, zorder=3)
            itens += [(x, y, num(y), ("acima-esq", "abaixo-dir", "acima", "abaixo", "abaixo-esq", "acima-dir")) for x, y in zip(d.tr_anos, d.enderecos_dominio)]
            handles.append(Line2D([], [], color=cor, lw=1.3, marker=mk, ms=4.4, label=f"{f['id']} — {f['tipo']}{prel(f)}"))
        for f, ls in zip(sem_tr, ("--", ":")):
            for r in ta[ta.fonte == f["id"]].itertuples():
                ax.axhline(r.enderecos_dominio, color=sl.INK2, lw=1.0, ls=ls, zorder=2)
                handles.append(Line2D([], [], color=sl.INK2, lw=1.0, ls=ls, label=f"{r.cenario}: {num(r.enderecos_dominio)} endereços{prel(f)}"))
        trs = sorted(ta[ta.fonte.isin([f["id"] for f in com_tr])].tr_anos.unique())
        ax.set_xlim(min(trs) / 1.5, max(trs) * 1.6)
        vals = ta[ta.fonte.isin([f["id"] for f in com_tr + sem_tr]) & (ta.enderecos_dominio > 0)].enderecos_dominio
        ax.set_ylim(vals.min() / 2.2, vals.max() * 2.2)
        ax.yaxis.set_minor_locator(matplotlib.ticker.NullLocator())
        marcas = [1, 2, 5, 10, 25, 50, 100, 200, 500, 1000]
        ax.set_xticks(marcas, [num(m) for m in marcas])
        ax.xaxis.set_minor_locator(matplotlib.ticker.NullLocator())
        ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: num(v)))
        ax.set_xlabel("tempo de retorno (anos, escala logarítmica)", fontsize=est.fs_txt, labelpad=2)
        ax.set_ylabel("endereços expostos dentro do domínio comum", fontsize=est.fs_txt, labelpad=3)
        sl._eixo_limpo(ax, est, "both")
        return [ax], sl.rotular_pontos(ax, itens, est.fs_rotulo)

    nome = "enderecos-expostos-por-tempo-de-retorno_fontes-inundacao_2022_dominio-comum"
    titulo = "Endereços expostos por tempo de retorno, segundo a fonte da mancha de inundação"
    rodape = ("Fontes: manchas — SGB (vetor oficial) e camadas extraídas de figura (V001 a V004, não oficiais); endereços — IBGE, CNEFE do Censo 2022. "
              "Contagem cumulativa, só dentro do domínio comum às fontes (terra, na sede). Os dois eixos em escala logarítmica.")

    def pagina(Hmax=None):
        """Título, quadro, legenda em duas colunas abaixo e rodapé; a 1ª passada mede a altura, a 2ª monta nela."""
        medir, W = Hmax is None, est.largura
        Hmax = Hmax or lm.A4_ALTURA_MAX_CM * lm.CM
        handles.clear()
        fig = plt.figure(figsize=(W, Hmax), dpi=est.dpi)
        fig.canvas.draw()
        rend, tr = fig.canvas.get_renderer(), fig.dpi_scale_trans
        util_pt = (W - 2 * lm.MARGEM) * 72
        yt = Hmax - lm.TOPO
        fig.text(lm.MARGEM, yt, "\n".join(lm.quebrar(fig, rend, titulo, util_pt, lm.FS_TITULO)), transform=tr, ha="left", va="top", fontsize=lm.FS_TITULO, color=sl.INK, linespacing=1.25)
        yt -= lm._altura_linhas_in(len(lm.quebrar(fig, rend, titulo, util_pt, lm.FS_TITULO)), lm.FS_TITULO, 1.25) + 2 * lm.GAP_BLOCO
        eixos, sem_lugar = montar(fig, yt, Hmax, est)
        fig.canvas.draw()
        y = min(ax.get_tightbbox(rend).y0 for ax in eixos) / fig.dpi - lm.GAP_BLOCO
        for ncol in (2, 1):  # duas colunas se couber na folha; senão, uma
            leg = fig.legend(handles=handles, loc="upper left", ncol=ncol, frameon=False, fontsize=lm.FS_LEGENDA, borderpad=0, borderaxespad=0, columnspacing=1.4,
                             handlelength=2.4, handletextpad=0.6, labelspacing=0.3, bbox_to_anchor=(lm.MARGEM, y), bbox_transform=tr)
            fig.canvas.draw()
            bl = leg.get_window_extent(rend)
            if bl.x1 <= fig.bbox.width:
                break
            leg.remove()
        else:
            raise ValueError("legenda não cabe na largura da folha")
        lin = lm.quebrar(fig, rend, rodape, util_pt, lm.FS_RODAPE)
        y = bl.y0 / fig.dpi - lm.GAP_BLOCO
        fig.text(lm.MARGEM, y, "\n".join(lin), transform=tr, ha="left", va="top", fontsize=lm.FS_RODAPE, color=sl.MUTED, linespacing=1.25)
        H = Hmax - (y - lm._altura_linhas_in(len(lin), lm.FS_RODAPE, 1.25) - lm.BASE)
        if medir:
            plt.close(fig)
            return pagina(H)
        cx = [t.get_window_extent(rend) for ax in eixos for t in ax.texts]
        sobre = sum(sl._caixas_sobrepostas(a, b) for i, a in enumerate(cx) for b in cx[i + 1:])
        fig.savefig(SAIDA / f"{nome}.png", dpi=est.dpi, facecolor=lm.FUNDO)
        plt.close(fig)
        return {"largura_px": round(W * est.dpi), "altura_px": round(H * est.dpi), "altura_cm": round(H / lm.CM, 2), "rotulos_sobrepostos": sobre, "rotulos_sem_posicao_livre": sem_lugar}

    info = pagina()
    c.gravar_meta(SAIDA / f"{nome}.png", descricao="endereços de domicílio particular expostos dentro do domínio comum (eixo vertical) por tempo de retorno (eixo horizontal, escala logarítmica), "
                  "uma linha por fonte com tempo de retorno; linhas horizontais = fontes sem tempo de retorno", layout="a4", largura_cm=lm.A4_LARGURA_CM, dpi=lm.A4_DPI,
                  fonte="tabela enderecos-expostos-por-cenario_fontes-inundacao_2022_municipal.csv", **info, **base)
    logger.info("Figura: %s — %d × %d px", nome, info["largura_px"], info["altura_px"])


def main() -> None:
    if ARGS.fontes:
        cfg = ler_fontes(ARGS.fontes)
        dom_arq = c.RAIZ / "data" / "processed" / "vetorizado" / (cfg["dominio"]["nome_saida"] + ".gpkg")
        if not dom_arq.exists():
            raise FileNotFoundError(f"{dom_arq} ausente — rode scripts/vetorizacao/comparar_fontes_inundacao.py")
        dominio = gpd.read_file(dom_arq).to_crs(c.CRS_PADRAO).union_all()
        tabs, pontos, cens = {}, {}, {}
        for f in cfg["fontes"]:
            cens[f["id"]] = cenarios_da_fonte(f)
            tabs[f["id"]], pontos[f["id"]] = expor(cens[f["id"]], f["nome_curto"], dominio, origem={k: f[k] for k in ("id", "arquivo", "camada", "atributo", "situacao")},
                                                   marca=MARCA.get(f["situacao"], ""))
            tabs[f["id"]]["fonte"] = f["id"]
        sintese(cfg, tabs, pontos, cens)
        return
    if not (ARGS.manchas and ARGS.camada and ARGS.atributo and ARGS.fonte):
        raise SystemExit("informe --fontes ARQUIVO.json, ou --manchas, --camada, --atributo e --fonte")
    cen = carregar_cenarios(ARGS.manchas, ARGS.camada, ARGS.atributo, ARGS.cenarios)
    dominio = gpd.read_file(ARGS.dominio).to_crs(c.CRS_PADRAO).union_all() if ARGS.dominio else None
    t, _ = expor(cen, ARGS.fonte, dominio, origem={"arquivo": str(ARGS.manchas), "camada": ARGS.camada, "atributo": ARGS.atributo}, marca=ARGS.marca)
    print(t.round(1).to_string(index=False))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    _p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    _p.add_argument("--codigo-ibge", default=c.CODIGO_IBGE_DEFAULT)
    _p.add_argument("--manchas", type=Path, help="arquivo vetorial das manchas")
    _p.add_argument("--camada", help="camada do arquivo")
    _p.add_argument("--atributo", help="atributo que identifica o cenário (cota, tempo de retorno, classe)")
    _p.add_argument("--fonte", help="nome curto da fonte (entra no nome dos arquivos de saída)")
    _p.add_argument("--cenarios", nargs="*", help="valores do atributo a usar, na ordem (padrão: todos)")
    _p.add_argument("--dominio", type=Path, help="camada do domínio comum (opcional)")
    _p.add_argument("--marca", default="", help="marca gravada na tabela (ex.: preliminar)")
    _p.add_argument("--fontes", type=Path, help="arquivo de fontes (.json): roda todas as fontes e a síntese")
    ARGS = _p.parse_args()
    main()
