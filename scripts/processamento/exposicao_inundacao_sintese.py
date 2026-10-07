"""
Síntese da exposição à inundação: quatro tabelas e quatro figuras que reúnem
a contagem por endereços, os três métodos de estimativa e os números
publicados pelas fontes, sobre os MESMOS polígonos.

Nada é recalculado por código próprio: as contagens e os métodos vêm das
funções dos estudos já existentes, aplicadas a polígonos montados aqui:
  - delimitações e números publicados: exposicao_inundacao_estimativas_oficiais
    (carregar_delimitacoes, aplicar_publicado); manchas por cota CUMULATIVAS;
  - B1/B2 (endereço dentro do polígono; população do setor repartida entre os
    endereços, grade como sensibilidade): exposicao_inundacao_cenarios.expor;
  - método por área do setor e método por uso do solo:
    exposicao_inundacao_estimativas_oficiais.populacao_por_area_e_uso_do_solo;
  - perfil dos expostos: exposicao_inundacao_enderecos.perfil_dos_expostos;
  - contornos das delimitações: exposicao_inundacao_estimativas_oficiais.figura.
A decomposição do número por setores inteiros e as frações da área urbanizada
são lidas das tabelas que esse último script grava.

Contagens próprias desta síntese:
  - cidade × resto do município: cada delimitação é cortada pela união dos
    setores censitários de 2022 de três partes — cidade (SITUACAO = "Urbana" e
    CD_DIST = distrito do setor mais populoso, a mesma regra de "urbana da
    sede" dos outros scripts), sede urbana de outro distrito (SITUACAO =
    "Urbana" e outro CD_DIST) e rural (SITUACAO = "Rural");
  - sensibilidade à posição dos pontos: a borda de cada polígono recuada e
    avançada de --borda-m metros (buffer negativo e positivo, no CRS do
    projeto) e a parcela de endereços com coordenada de nível 1 ou 2.

Saídas, todas em data/processed/exposicao_inundacao_sintese/ (fora do git),
cada uma com o .json irmão. Tabelas de síntese: CSV sem arredondar e .md para
leitura (arredondado a partir do valor não arredondado). Figuras: edição A4
(16 cm, 300 dpi, legenda abaixo), SEM título e SEM fonte dentro da imagem — os
dois ficam no .json (campos "titulo" e "fonte"). Nenhuma figura mostra ponto
de endereço. Os dois mapas trazem, como última linha da legenda, a projeção
cartográfica, lida do CRS do projeto. Nos rótulos das figuras a cota leva ponto
de milhar (leitura do mapa); nomes de arquivo, chaves e tabelas não mudam. O ano
de referência e o tempo de retorno que o documento da fonte dá para as manchas
por cota vêm de uma transcrição versionada (--manchas-documento: cota_cm,
tr_anos_documento, periodo_de_referencia, evento, documento, pagina); sem ela,
o campo fica vazio. Na legenda do mapa das cheias o tempo de retorno é o do
documento quando a cota está na transcrição e o atributo do serviço da fonte
quando não está; o .json da figura traz a regra e os dois valores. A figura da régua usa a série histórica diária em
todos os anos; as tabelas trazem a máxima da telemetria no evento datado. Antes de terminar, os números são conferidos com as tabelas dos
estudos de origem; se algum diferir, nada é copiado.

Uso:
  python scripts/processamento/exposicao_inundacao_sintese.py [--copia PASTA]
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import textwrap
from pathlib import Path

import geopandas as gpd
import matplotlib

matplotlib.use("Agg")
import matplotlib.patheffects as pe  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Patch, Rectangle  # noqa: E402

import dinamica_populacional_comum as c  # noqa: E402
import exposicao_inundacao_cenarios as ec  # noqa: E402
import exposicao_inundacao_enderecos as ee  # noqa: E402
import exposicao_inundacao_estimativas_oficiais as eo  # noqa: E402
import layout_mapa as lm  # noqa: E402
from dinamica_populacional_cnefe_mapas import Fundo  # noqa: E402
from dinamica_populacional_mapas import Base  # noqa: E402
from exposicao_inundacao_cenarios import num  # noqa: E402

logger = logging.getLogger(__name__)

SCRIPT = "scripts/processamento/exposicao_inundacao_sintese.py"
SAIDA = c.RAIZ / "data" / "processed" / "exposicao_inundacao_sintese"
ORIGEM = eo.SAIDA  # tabelas do estudo das delimitações oficiais (decomposição, frações, conferência)
TAB_ESTUDO = ee.TAB  # tabelas publicadas do estudo por endereços (conferência; bairros mais expostos)
ARQ_ESTACAO = c.RAW / "nivel-rio_ana_exemplo-consulta.json"  # inventário da estação: coordenadas da régua
PADRAO_SERIE = "nivel-rio_ana-serie-historica-*_diario.csv"
PADRAO_TELEMETRIA = "nivel-rio_ana-telemetria-*_15min.csv"
PADRAO_MANCHAS_DOC = "manchas-por-cota_*_transcricao.csv"  # em eo.PUBLICADOS (versionada); transcrição do documento da fonte das manchas
# de onde vem o tempo de retorno da legenda do mapa das cheias: (rótulo no .json, texto no título da legenda)
TR_DO_DOCUMENTO = ("documento", "tempo de retorno segundo o relatório da fonte")
TR_DO_SERVICO = ("serviço", "tempo de retorno segundo o atributo do serviço da fonte")
TR_MISTO = "tempo de retorno segundo o relatório da fonte; * = atributo do serviço da fonte"
NIVEIS_PRECISOS = ("1", "2")
PARTES = ("cidade", "sede urbana de outro distrito", "rural")
MUNICIPIO = "município"
N_BAIRROS_ROTULADOS = 5
# cheias: um só matiz, da mais frequente (escura) à mais rara (clara)
COR_CHEIAS = ["#3b1a6e", "#6a45a8", "#9b7dcb", "#c9b8e4"]
COR_BAIRRO, COR_REGUA = "#4d4c48", "#d7191c"
# gráfico das razões: cor E forma por série
SERIES = {"razao_area_sobre_enderecos": ("o", "#2a78d6", "método por área do setor"),
          "razao_uso_solo_sobre_enderecos": ("s", "#eb6834", "método por uso do solo"),
          "razao_publicado_sobre_contagem": ("D", "#1baf7a", "número oficial publicado")}  # a legenda acrescenta quem publica
LIM_RAZAO = (0.5, 1000)
INK2, GRADE = "#3a3a38", "#e3e2dc"
COR_HASTE, COR_DESTAQUE, COR_COTA = "#8496ab", "#1f3350", "#8a8984"
FONTES_META = [*eo.FONTES_META, "ANA — série histórica de cotas da estação fluviométrica; estimativa publicada para a área atingida",
               "IBGE — áreas urbanizadas (2019)", "OpenStreetMap — água e vias (só apresentação)"]


# ---------------------------------------------------------------- gravação
def meta(caminho: Path, **kw) -> None:
    c.gravar_meta(caminho, codigo_ibge=ARGS.codigo_ibge, crs=c.CRS_PADRAO, status=c.STATUS_CONFERENCIA, ligado_ao_portal=False, script=SCRIPT,
                  fontes=FONTES_META, fora_do_git="data/processed/ é ignorado; não publicar", **kw)


def gravar(t: pd.DataFrame, nome: str, descricao: str, **kw) -> Path:
    arq = SAIDA / f"{nome}.csv"
    t.to_csv(arq, index=False)
    meta(arq, descricao=descricao, **kw)
    logger.info("Tabela: %s (%d linhas)", arq.relative_to(c.RAIZ), len(t))
    return arq


def fmt(v, casas=0) -> str:
    """Número para leitura: vírgula decimal e ponto de milhar, arredondado a partir do valor não arredondado."""
    if v is None or (isinstance(v, float) and np.isnan(v)) or v is pd.NA:
        return "—"
    if isinstance(v, str):
        return v
    if casas == "ano":  # ano não leva ponto de milhar
        return str(int(v))
    if casas == "auto":  # inteiro como inteiro; o resto com duas casas
        casas = 0 if float(v).is_integer() else 2
    return num(float(v), casas)


def cota_no_rotulo(cota) -> str:
    """Cota para rótulo de figura: com ponto de milhar (cotas de três dígitos não mudam). Só leitura do mapa."""
    return num(float(cota))


def gravar_sintese(t: pd.DataFrame, nome: str, titulo: str, colunas: dict, nota: str, descricao: str, leitura: dict | None = None, **kw) -> Path:
    """Tabela de síntese: CSV sem arredondar + .md para leitura (colunas: {coluna: (cabeçalho, casas)}) + .json.

    leitura: {coluna: casas de cada linha}, quando as linhas de uma coluna pedem casas diferentes no .md."""
    leitura = leitura or {}
    arq = gravar(t, nome, descricao, formatos=["csv (valores não arredondados, ponto decimal)", "md (para leitura, arredondado a partir do CSV)"],
                 titulo=titulo, nota=nota, **kw)
    lin = [f"**{titulo}**", "", "| " + " | ".join(cab for cab, _ in colunas.values()) + " |", "|" + "|".join("---" if casas is None else "---:" for _, casas in colunas.values()) + "|"]
    for i, r in enumerate(t.itertuples(index=False)):
        lin.append("| " + " | ".join(fmt(getattr(r, col), leitura[col][i] if col in leitura else (casas if casas is not None else 0)).replace("|", "/")
                                     for col, (_, casas) in colunas.items()) + " |")
    arq.with_suffix(".md").write_text("\n".join(lin + ["", nota, ""]), encoding="utf-8")
    return arq


# ---------------------------------------------------------------- polígonos
def classes_de_setor(st: gpd.GeoDataFrame) -> tuple[pd.Series, str]:
    """Parte de cada setor de 2022 (cidade, sede urbana de outro distrito, rural) e o distrito-sede."""
    sede = st.loc[st["pop"].idxmax(), "CD_DIST"]  # distrito-sede = o do setor mais populoso, como nos outros scripts
    parte = np.where(st.SITUACAO == "Rural", PARTES[2], np.where(st.CD_DIST == sede, PARTES[0], PARTES[1]))
    return pd.Series(parte, index=st.CD_SETOR.values), sede


def montar_poligonos(base: gpd.GeoDataFrame, st: gpd.GeoDataFrame, parte_do_setor: pd.Series) -> gpd.GeoDataFrame:
    """Uma linha por polígono a contar: cada delimitação inteira, cortada por parte e com a borda recuada e avançada."""
    uniao = {p: st[st.CD_SETOR.map(parte_do_setor) == p].union_all() for p in PARTES}
    b, linhas = ARGS.borda_m, []
    for r in base.itertuples():
        comum = {"fonte": r.fonte, "delimitacao": r.delimitacao}
        linhas.append({**comum, "recorte": MUNICIPIO, "borda_m": 0.0, "geometry": r.geometry})
        linhas += [{**comum, "recorte": p, "borda_m": 0.0, "geometry": r.geometry.intersection(u)} for p, u in uniao.items()]
        for dist in (-b, b):  # o deslocamento é da borda da delimitação; o corte pela cidade vem depois dele
            g = r.geometry.buffer(dist)
            linhas.append({**comum, "recorte": MUNICIPIO, "borda_m": dist, "geometry": g})
            linhas.append({**comum, "recorte": PARTES[0], "borda_m": dist, "geometry": g.intersection(uniao[PARTES[0]])})
    p = gpd.GeoDataFrame(linhas, crs=c.CRS_PADRAO)
    p["cenario"] = p["valor"] = p.fonte + "|" + p.delimitacao + "|" + p.recorte + "|" + p.borda_m.map("{:+.0f}".format)
    p["area_km2"] = p.geometry.area / 1e6
    return p


def linhas_da_sintese(base: gpd.GeoDataFrame, tr: pd.Series) -> pd.DataFrame:
    """As linhas das tabelas, na ordem de leitura: cheias; área do evento datado (cidade e município); demais fontes."""
    nomes = {f["id"]: f["nome"].split(" — ") for f in eo.FONTES}
    regra = {"setorizacao-sgb": "setores de risco delimitados pela fonte em levantamento de campo",
             "areas-de-risco-ibge": "áreas de risco da fonte, associadas a setores censitários de 2010",
             eo.FONTE_COM_COTA: "área diretamente atingida pela cheia, parte dentro do limite municipal"}
    linhas = []
    for r in base[base.fonte == eo.CHEIAS].itertuples():
        t = float(tr.get(int(r.delimitacao), np.nan))
        linhas.append({"linha": f"cheia de {r.delimitacao} cm", "fonte": "SGB", "ano_de_referencia": np.nan, "fonte_id": r.fonte, "delimitacao": r.delimitacao, "recorte": MUNICIPIO,
                       "a_que_se_refere": f"mancha de inundação até a cota de {r.delimitacao} cm na régua; tempo de retorno de {num(t, 1)} anos", "cota_cm": float(r.delimitacao), "tr_anos": t})
    outras = [f for f in eo.FONTES if f["id"] == eo.FONTE_COM_COTA] + [f for f in eo.FONTES if f["id"] != eo.FONTE_COM_COTA]
    for f in outras:
        sigla, desc = nomes[f["id"]]
        for rec in ((PARTES[0], MUNICIPIO) if f["id"] == eo.FONTE_COM_COTA else (MUNICIPIO,)):
            sufixo = {PARTES[0]: ", na cidade", MUNICIPIO: ", no município"}[rec] if f["id"] == eo.FONTE_COM_COTA else ""
            linhas.append({"linha": desc + sufixo, "fonte": sigla, "ano_de_referencia": float(f["ano"]), "fonte_id": f["id"], "delimitacao": "total", "recorte": rec,
                           "a_que_se_refere": regra.get(f["id"], desc) + ("; só a parte nos setores urbanos do distrito-sede" if rec == PARTES[0] and sufixo else ""),
                           "cota_cm": np.nan, "tr_anos": np.nan})
    t = pd.DataFrame(linhas)
    t["chave"] = t.fonte_id + "|" + t.delimitacao + "|" + t.recorte
    return t


# ---------------------------------------------------------------- conferência com os estudos de origem
CONFERENCIA: list[dict] = []


def confere(item: str, origem: str, esperado: float, obtido: float, tol: float = 1e-6) -> None:
    ok = bool(np.isclose(float(esperado), float(obtido), rtol=tol, atol=tol))
    CONFERENCIA.append({"item": item, "tabela_de_origem": origem, "valor_de_origem": float(esperado), "valor_obtido": float(obtido), "confere": ok})
    if not ok:
        logger.error("NÃO confere: %s — origem %s, obtido %s (%s)", item, esperado, obtido, origem)


# ---------------------------------------------------------------- régua
def maximas_anuais(caminho: Path) -> tuple[pd.DataFrame, dict]:
    """Máxima anual da série histórica de cotas, do primeiro ano ao último ano completo do calendário.

    Máxima = maior valor entre as cotas do dia e a máxima do mês informada pela fonte, em qualquer nível de
    consistência. O ano em que a série termina antes de 31 de dezembro não entra.
    """
    t = pd.read_csv(caminho, parse_dates=["data"])
    fim = t.data.max()
    ultimo = fim.year if (fim.month, fim.day) == (12, 31) else fim.year - 1
    t["ano"] = t.data.dt.year
    t = t[t.ano <= ultimo]
    v = t.dropna(subset=["cota_cm"])
    a = pd.DataFrame({"maxima_cm": pd.concat([t.groupby("ano").cota_cm.max(), t.groupby("ano").maxima_do_mes_cm.max()], axis=1).max(axis=1),
                      "dias_com_cota": v.groupby("ano").data.nunique()}).reindex(range(int(t.ano.min()), ultimo + 1))
    a["dias_com_cota"] = a.dias_com_cota.fillna(0).astype(int)
    a["dias_do_ano"] = [366 if pd.Timestamp(year=y, month=12, day=31).dayofyear == 366 else 365 for y in a.index]
    a["tem_dado"] = a.maxima_cm.notna()
    a = a.rename_axis("ano").reset_index()
    parciais = a[a.tem_dado & (a.dias_com_cota < a.dias_do_ano)]
    info = {"serie": str(Path(caminho).relative_to(c.RAIZ)), "estacao": str(t.codigo_estacao.iloc[0]), "primeiro_ano": int(a.ano.min()), "ultimo_ano_completo": ultimo,
            "serie_termina_em": str(fim.date()), "anos_no_periodo": len(a), "anos_com_dado": int(a.tem_dado.sum()), "anos_sem_dado": int((~a.tem_dado).sum()),
            "quais_sem_dado": a[~a.tem_dado].ano.tolist(), "anos_com_dado_em_parte_dos_dias": {int(r.ano): int(r.dias_com_cota) for r in parciais.itertuples()},
            "maxima": "maior valor entre as cotas do dia e a máxima do mês informada pela fonte (dado bruto ou consistido)"}
    return a, info


def verificar_maxima(caminho: Path, ano: int, vizinhos: int = 3) -> dict:
    """Dia em que a série diária registra a máxima do ano, se ele e os dias vizinhos têm cota, meses sem cota e,
    quando há série telemétrica do ano em data/raw/, a máxima dela."""
    t = pd.read_csv(caminho, parse_dates=["data"])
    t = t[t.data.dt.year == ano]
    d = t.groupby("data").cota_cm.max()
    dia = d.idxmax()
    jan = pd.date_range(dia - pd.Timedelta(days=vizinhos), dia + pd.Timedelta(days=vizinhos))
    out = {"serie_diaria": str(Path(caminho).relative_to(c.RAIZ)), "maxima_das_cotas_do_dia_cm": float(d.max()), "dia_da_maxima": str(dia.date()),
           "maxima_do_mes_informada_pela_fonte_cm": float(t.loc[t.data.dt.month == dia.month, "maxima_do_mes_cm"].max()),
           f"cota_no_dia_e_nos_{vizinhos}_vizinhos_de_cada_lado_cm": {str(x.date()): (float(d[x]) if x in d.index and pd.notna(d[x]) else None) for x in jan},
           "dia_e_vizinhos_com_dado": bool(d.reindex(jan).notna().all()), "dias_com_cota_no_ano": int(d.notna().sum()),
           "meses_sem_cota": [m for m in range(1, 13) if not d[d.index.month == m].notna().any()]}
    for arq in sorted(c.RAW.glob(PADRAO_TELEMETRIA)):
        tel = pd.read_csv(arq, parse_dates=["data_hora"]).dropna(subset=["nivel_cm"])
        tel = tel[tel.data_hora.dt.year == ano]
        if len(tel):
            i = tel.nivel_cm.idxmax()
            out["telemetria"] = {"serie": str(arq.relative_to(c.RAIZ)), "maxima_cm": float(tel.nivel_cm[i]), "primeira_leitura_da_maxima": str(tel.data_hora[i]),
                                 "diferenca_para_a_serie_diaria_cm": float(tel.nivel_cm[i] - d.max())}
    return out


def manchas_no_documento() -> pd.DataFrame | None:
    """Transcrição do que o documento da fonte diz de cada mancha por cota (período, tempo de retorno, página); None se faltar."""
    arq = ARGS.manchas_documento or next(iter(sorted(eo.PUBLICADOS.glob(PADRAO_MANCHAS_DOC))), None)
    if arq is None or not Path(arq).exists():
        logger.warning("transcrição do documento das manchas por cota ausente: ano de referência e tempo de retorno do documento ficam vazios")
        return None
    t = pd.read_csv(arq, dtype={"cota_cm": str}).set_index("cota_cm")
    t.attrs["arquivo"] = str(Path(arq).resolve().relative_to(c.RAIZ))
    return t


def tempo_de_retorno_da_legenda(cotas: list[str], tr: pd.Series, doc: pd.DataFrame | None) -> dict:
    """Tempo de retorno de cada cota para a legenda: o do documento da fonte quando a cota está na transcrição;
    o atributo do serviço só quando não está. Devolve, por cota, os dois valores, o usado e a origem."""
    out = {}
    for k in cotas:
        servico = float(f"{float(tr[int(k)]):.6g}")  # o atributo vem em precisão simples (1,29999995 por 1,3)
        documento = float(doc.loc[k, "tr_anos_documento"]) if doc is not None and k in doc.index and pd.notna(doc.loc[k, "tr_anos_documento"]) else None
        out[k] = {"documento": documento, "servico": servico, "na_legenda": servico if documento is None else documento,
                  "origem": (TR_DO_SERVICO if documento is None else TR_DO_DOCUMENTO)[0]}
    return out


def posicao_da_regua() -> tuple[float, float] | None:
    if not ARGS.estacao.exists():
        logger.warning("%s ausente: a régua não é desenhada", ARGS.estacao.name)
        return None
    e = json.loads(ARGS.estacao.read_text(encoding="utf-8"))["estacao_selecionada"]
    p = gpd.GeoSeries(gpd.points_from_xy([float(e["longitude"])], [float(e["latitude"])]), crs="EPSG:4674").to_crs(c.CRS_PADRAO).iloc[0]  # inventário em graus; mapa no CRS do projeto
    return p.x, p.y


# ---------------------------------------------------------------- figuras
def meta_figura(caminho: Path, info: dict, descricao: str, **kw) -> dict:
    from PIL import Image

    with Image.open(caminho) as im:
        info = {**info, "largura_px": im.width, "altura_px": im.height}
    meta(caminho, descricao=descricao, texto_dentro_da_imagem="sem título e sem fonte: os dois estão nos campos 'titulo' e 'fonte'",
         aviso_fora_da_imagem="Produto de trabalho — pendente de conferência.", **info, **kw)
    logger.info("Figura: %s — %d × %d px", caminho.relative_to(c.RAIZ), info["largura_px"], info["altura_px"])
    return {"arquivo": caminho, **info}


def figura_cheias(cont: gpd.GeoDataFrame, tr: pd.Series, st: gpd.GeoDataFrame, doc: pd.DataFrame | None) -> dict:
    """Mapa das cheias cumulativas no recorte urbano: uma cor por cota, bairros, régua, encarte de localização."""
    base = Base(ARGS.codigo_ibge, agua=True, nome_rio=ARGS.nome_rio, modo_agua="enderecos")
    fundo = Fundo(base)
    ext = base.ext_urb
    lay = lm.LayoutA4([[ext]])
    ax = lay.eixos[0]
    lim = base.limite.union_all()
    cheias = cont[cont.fonte == eo.CHEIAS]
    hand = []
    for i, r in reversed(list(enumerate(cheias.itertuples()))):  # a maior primeiro; as menores por cima
        # mancha recortada no limite municipal só para exibição, como nos mapas do estudo
        gpd.GeoSeries([r.geometry.intersection(lim)], crs=c.CRS_PADRAO).plot(ax=ax, color=COR_CHEIAS[i], edgecolor="none", zorder=1.5 + 0.1 * (len(cheias) - i))
    tr_leg = tempo_de_retorno_da_legenda(list(cheias.delimitacao), tr, doc)
    origens = {v["origem"] for v in tr_leg.values()}
    misto = len(origens) > 1  # só então o item diz de onde vem o seu valor
    tr_titulo = TR_MISTO if misto else next(t for o, t in (TR_DO_DOCUMENTO, TR_DO_SERVICO) if o in origens)
    for i, r in enumerate(cheias.itertuples()):
        x = tr_leg[r.delimitacao]
        marca = "*" if misto and x["origem"] == TR_DO_SERVICO[0] else ""
        hand.append(Patch(facecolor=COR_CHEIAS[i], edgecolor="none", label=f"até {cota_no_rotulo(r.delimitacao)} cm (tempo de retorno de {num(x['na_legenda'], 1)}{marca} anos)"))
    bairros = gpd.read_file(eo.ARQ_BAIRROS).to_crs(c.CRS_PADRAO)
    bairros.boundary.plot(ax=ax, color=COR_BAIRRO, linewidth=0.45, zorder=5.5)
    hand.append(Line2D([], [], color=COR_BAIRRO, lw=0.45, label=f"bairro (nome: os {N_BAIRROS_ROTULADOS} com mais endereços expostos na maior cota)"))
    fundo.desenhar(ax, ext, "urbano", 1000, modo_agua="enderecos", escala_pos=ARGS.escala_pos)
    regua = posicao_da_regua()
    obst = []
    if regua:
        ax.plot(*regua, marker="^", ms=7.5, mfc=COR_REGUA, mec="#ffffff", mew=0.8, ls="", zorder=12)
        hand.append(Line2D([], [], marker="^", ms=7.5, mfc=COR_REGUA, mec="#ffffff", mew=0.8, ls="", label="régua (estação fluviométrica)"))
        obst.append(regua)
    # os bairros com mais endereços expostos na maior cota (tabela publicada do estudo por endereços)
    tb = pd.read_csv(TAB_ESTUDO / "bairros-expostos-por-cota_sgb-ibge-cnefe_2022_bairro.csv")
    tb = tb[(tb.cota_cm == tb.cota_cm.max()) & tb.bairro.isin(bairros.NM_BAIRRO)].sort_values("enderecos", ascending=False).head(N_BAIRROS_ROTULADOS)
    rot = bairros[bairros.NM_BAIRRO.isin(tb.bairro)]
    rio = base.agua[base.agua.principal].union_all()  # o bairro vai até o eixo do rio: o nome fica na parte em terra
    terra = [g.difference(rio) if not g.difference(rio).is_empty else g for g in rot.geometry]
    itens = [(g.representative_point().x, g.representative_point().y, n) for n, g in zip(rot.NM_BAIRRO, terra)]
    for (x, y, t), (dx, dy, chamada) in zip(itens, lm.posicionar_rotulos(ax, itens, lm.FS_ROTULO_MIN, obstaculos=obst)):
        ax.annotate(t, (x, y), xytext=(dx, dy), textcoords="offset points", fontsize=lm.FS_ROTULO_MIN, fontweight="bold", color=lm.INK, zorder=11,
                    path_effects=[pe.withStroke(linewidth=2.2, foreground="#ffffff")], arrowprops=dict(arrowstyle="-", color=lm.INK, lw=0.5) if chamada else None)
    # encarte: posição do recorte no município
    em = base.ext_mun
    larg = ARGS.encarte[2]
    enc = ax.inset_axes([ARGS.encarte[0], ARGS.encarte[1], larg, larg * (em[3] - em[2]) / (em[1] - em[0]) * (ext[1] - ext[0]) / (ext[3] - ext[2])])
    base.limite.plot(ax=enc, color="#efeee9", edgecolor=lm.INK, linewidth=0.5)
    enc.add_patch(Rectangle((ext[0], ext[2]), ext[1] - ext[0], ext[3] - ext[2], facecolor="none", edgecolor=COR_REGUA, linewidth=1.0))
    enc.set_xlim(em[0], em[1]); enc.set_ylim(em[2], em[3]); enc.set_aspect("equal"); enc.set_xticks([]); enc.set_yticks([])  # noqa: E702
    enc.set_facecolor("#ffffff")
    for s in enc.spines.values():
        s.set_color("#8f8e88"); s.set_linewidth(0.5)  # noqa: E702
    enc.set_zorder(13)
    hand.append(Patch(facecolor="none", edgecolor=COR_REGUA, linewidth=1.0, label="área do mapa (encarte: o município)"))
    maior = cheias.delimitacao.iloc[-1]
    caminho = SAIDA / "sintese-cheias-por-cota_sgb_atual_manchas_urbano.png"
    info = lm.finalizar_a4(lay, "Manchas de inundação por cota do rio na área urbana da sede", fundo.comum(hand),
                           "mancha de inundação por cota (SGB), cumulativa; escura = cheia mais frequente\n" + tr_titulo,
                           "Fontes: SGB (manchas de inundação por cota); IBGE (malha de bairros, Censo 2022); ANA (estação fluviométrica e hidrografia); OpenStreetMap (água, vias).",
                           caminho, origem=caminho, metodo="Manchas cumulativas (união das cotas ≤ a indicada), recortadas no limite municipal só para exibição.", texto_na_imagem=False,
                           legenda_titulo_em_linhas=True, linha_apos_legenda=lm.texto_da_projecao(c.CRS_PADRAO))
    return meta_figura(caminho, info, "as cheias por cota, cumulativas, no recorte urbano, com os bairros, a régua e um encarte de localização; sem pontos de endereço",
                       recorte="área urbana da sede", janela_m=[round(v) for v in ext], bairros_rotulados=tb.bairro.tolist(), criterio_dos_bairros=f"mais endereços expostos na cota de {maior} cm",
                       cores={r.delimitacao: COR_CHEIAS[i] for i, r in enumerate(cheias.itertuples())}, manchas_exibicao=ee.NOTA_RECORTE,
                       projecao_na_imagem="última linha da legenda; texto montado a partir do CRS do projeto (pyproj), campo 'linha_apos_legenda'",
                       cotas_nos_rotulos="com ponto de milhar, para leitura do mapa; nas chaves e nos nomes de arquivo, sem ponto",
                       tempo_de_retorno_na_legenda={"regra": "o valor do documento da fonte quando a cota está na transcrição do documento; o atributo do serviço da fonte só quando não está",
                                                    "transcricao": doc.attrs["arquivo"] if doc is not None else "ausente", "texto_no_titulo_da_legenda": tr_titulo, "anos_por_cota": tr_leg})


def figura_contornos(cont: gpd.GeoDataFrame, maior: str) -> dict:
    """A figura dos contornos do estudo das delimitações oficiais, pela mesma função, sem título e sem fonte na imagem."""
    padrao = lm.TITULO_E_FONTE_NA_IMAGEM, lm.LINHA_APOS_LEGENDA
    lm.TITULO_E_FONTE_NA_IMAGEM, lm.LINHA_APOS_LEGENDA = False, lm.texto_da_projecao(c.CRS_PADRAO)
    try:
        info = eo.figura(cont, maior, cota_no_rotulo=cota_no_rotulo(maior))  # grava em eo.SAIDA, que nesta execução é a pasta da síntese
    finally:
        lm.TITULO_E_FONTE_NA_IMAGEM, lm.LINHA_APOS_LEGENDA = padrao
    feito = info.pop("arquivo")
    caminho = feito.with_name("sintese-" + feito.name)
    feito.replace(caminho)
    feito.with_suffix(".json").unlink()
    return meta_figura(caminho, info, "contornos das delimitações oficiais e borda da maior mancha por cota, sobre a malha viária; sem pontos de endereço",
                       recorte="área urbana da sede", desenho="o mesmo da figura dos contornos do estudo das delimitações oficiais (mesma função); o título e a fonte saíram da imagem, "
                               "a legenda ganhou a linha da projeção e a cota do rótulo leva ponto de milhar",
                       projecao_na_imagem="última linha da legenda; texto montado a partir do CRS do projeto (pyproj), campo 'linha_apos_legenda'",
                       cotas_nos_rotulos="com ponto de milhar, para leitura do mapa; no título e no nome do arquivo, sem ponto", manchas_exibicao=ee.NOTA_RECORTE)


def pagina_grafico(montar, handles: list, caminho: Path, ncol: int) -> dict:
    """Página A4 de um gráfico, sem título e sem rodapé: quadro e legenda abaixo. A 1ª passada mede a altura; a 2ª monta nela."""
    W = lm.A4_LARGURA_CM * lm.CM

    def passada(H):
        fig = plt.figure(figsize=(W, H), dpi=lm.A4_DPI)
        ax = montar(fig, H)
        fig.canvas.draw()
        rend = fig.canvas.get_renderer()
        y = ax.get_tightbbox(rend).y0 / fig.dpi - 1.5 * lm.GAP_BLOCO
        for esp in (1.6, 1.0, 0.5):  # o espaço entre colunas só encolhe se a legenda não couber na largura da folha
            leg = fig.legend(handles=handles, loc="upper left", ncol=ncol, frameon=False, fontsize=lm.FS_LEGENDA, borderpad=0, borderaxespad=0, columnspacing=esp,
                             handlelength=2.0, handletextpad=0.6, labelspacing=0.4, bbox_to_anchor=(lm.MARGEM, y), bbox_transform=fig.dpi_scale_trans)
            fig.canvas.draw()
            if leg.get_window_extent(rend).x1 <= fig.bbox.width - lm.MARGEM * fig.dpi or esp == 0.5:
                break
            leg.remove()
        return fig, ax, leg, rend, H - (leg.get_window_extent(rend).y0 / fig.dpi - lm.BASE)

    fig, *_, usado = passada(lm.A4_ALTURA_MAX_CM * lm.CM)
    plt.close(fig)
    fig, ax, leg, rend, H = passada(usado)
    textos = [t for t in ax.texts + ax.get_xticklabels() + ax.get_yticklabels() + [ax.xaxis.label, ax.yaxis.label, leg] if not hasattr(t, "get_text") or t.get_text()]
    for t in textos:
        b = t.get_window_extent(rend)
        if b.x0 < -0.5 or b.y0 < -0.5 or b.x1 > fig.bbox.width + 0.5 or b.y1 > fig.bbox.height + 0.5:
            raise ValueError(f"{caminho.name}: texto fora da página: {getattr(t, 'get_text', lambda: 'legenda')()!r}")
    cx = [t.get_window_extent(rend) for t in ax.texts if t.get_text()]
    sobre = sum(a.x0 < b.x1 and a.x1 > b.x0 and a.y0 < b.y1 and a.y1 > b.y0 for i, a in enumerate(cx) for b in cx[i + 1:])
    if sobre:
        logger.warning("%s: %d pares de rótulos sobrepostos", caminho.name, sobre)
    fig.savefig(caminho, dpi=lm.A4_DPI, facecolor=lm.FUNDO)
    plt.close(fig)
    return {"layout": "a4", "largura_cm": lm.A4_LARGURA_CM, "altura_cm": round(H / lm.CM, 2), "dpi": lm.A4_DPI, "legenda_colunas": ncol, "rotulos_sobrepostos": int(sobre)}


def _eixo(fig, H, esq, dirt, alt, topo=lm.TOPO):
    W = lm.A4_LARGURA_CM * lm.CM
    ax = fig.add_axes([esq / W, (H - topo - alt) / H, (W - esq - dirt) / W, alt / H])
    ax.set_facecolor(lm.FUNDO)
    ax.tick_params(labelsize=lm.FS_LEGENDA, length=2.5, pad=2, colors=INK2)
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    for sp in ("left", "bottom"):
        ax.spines[sp].set_color("#c3c2b7")
    return ax


def figura_razoes(b2: pd.DataFrame, b3: pd.DataFrame) -> dict:
    """Razão entre cada estimativa e a contagem por endereços, por delimitação: um eixo logarítmico, uma linha por delimitação."""
    t = b2[["linha", "fonte", "chave", "fonte_id", "delimitacao", *list(SERIES)[:2]]].merge(b3.loc[b3.chave.notna(), ["chave", "razao"]].rename(columns={"razao": list(SERIES)[2]}), on="chave", how="left")
    desvio = dict(zip(SERIES, (-0.27, 0.0, 0.27)))  # cada série na sua faixa dentro da linha: marcadores próximos não se cobrem
    quem = b3[b3.chave.notna()].set_index("chave").fonte  # quem publica o número de cada linha (pode não ser a instituição da delimitação)
    siglas = list(dict.fromkeys(quem))
    de_quem = f" ({', '.join(siglas[:-1])} ou {siglas[-1]})" if len(siglas) > 1 else f" ({siglas[0]})"
    rotulos = {col: rot + (de_quem if col == list(SERIES)[2] else "") for col, (_, _, rot) in SERIES.items()}
    handles = [Line2D([], [], marker=m, ms=8.5, mfc=cor, mec=lm.FUNDO, mew=0.8, ls="", label=rotulos[col]) for col, (m, cor, _) in SERIES.items()]
    # rótulo de cada linha no eixo: o da tabela, com a cota das cheias com ponto de milhar (só na figura)
    t["rotulo_da_linha"] = [r.linha.replace(r.delimitacao, cota_no_rotulo(r.delimitacao)) if r.fonte_id == eo.CHEIAS else r.linha for r in t.itertuples()]
    fora = []

    def rotulo(v):
        return num(v, 2 if v < 10 else (1 if v < 100 else 0))

    def montar(fig, H):
        ax = _eixo(fig, H, 2.74, 0.24, 0.50 * len(t) + 0.1, topo=lm.TOPO + 0.16)
        ax.set_xscale("log")
        ax.set_xlim(*LIM_RAZAO)
        ax.set_ylim(len(t) - 0.5, -0.5)
        ax.axvline(1, color=INK2, lw=0.8, zorder=1.5)
        ax.text(1, -0.5, "igual à contagem", ha="center", va="bottom", fontsize=lm.FS_ROTULO_MIN, color=INK2)
        for col, (m, cor, _) in SERIES.items():
            for i, v in enumerate(t[col]):
                if pd.isna(v):
                    continue
                if not LIM_RAZAO[0] <= v <= LIM_RAZAO[1]:
                    fora.append((t.linha[i], col, float(v)))
                    continue
                y = i + desvio[col]
                ax.plot(v, y, marker=m, ms=8.5, mfc=cor, mec=lm.FUNDO, mew=0.8, ls="", zorder=3)
                esq = v < 1 or v > LIM_RAZAO[1] / 4  # à esquerda do marcador: abaixo de 1 (não cruza a linha de referência) e junto ao fim do eixo
                ax.annotate(rotulo(v), (v, y), xytext=(-7 if esq else 7, 0), textcoords="offset points", ha="right" if esq else "left", va="center_baseline",
                            fontsize=lm.FS_ROTULO_MIN, color=INK2, zorder=4, bbox=dict(fc=lm.FUNDO, ec="none", pad=0.6))
        pot = [10.0 ** k for k in range(int(np.ceil(np.log10(LIM_RAZAO[0]))), int(np.floor(np.log10(LIM_RAZAO[1]))) + 1)]
        ax.set_xticks(pot, [num(p, 0) for p in pot])
        ax.set_xticks([x for x in LIM_RAZAO if x not in pot], [num(x, 1) for x in LIM_RAZAO if x not in pot], minor=True)  # o fim do eixo, sem linha de grade
        ax.tick_params(axis="x", which="minor", labelsize=lm.FS_LEGENDA, length=2.5, pad=2, colors=INK2)
        ax.grid(axis="x", which="major", color=GRADE, lw=0.5)  # grade só nas potências de 10
        ax.set_axisbelow(True)
        ax.set_yticks(range(len(t)), ["\n".join(y for x in f"{r.rotulo_da_linha} — {r.fonte}".replace("), ", "),\n").split("\n") for y in textwrap.wrap(x, 44)) for r in t.itertuples()])
        ax.tick_params(axis="y", length=0)
        ax.spines["left"].set_visible(False)
        ax.set_xlabel("razão estimativa / contagem por endereços (escala logarítmica)", fontsize=lm.FS_LEGENDA, color=INK2, labelpad=3)
        return ax

    caminho = SAIDA / "sintese-razao-estimativa-sobre-contagem_sgb-ibge-fepam-ana-cnefe_2022_delimitacao.png"
    info = pagina_grafico(montar, handles, caminho, ncol=3)
    return meta_figura(caminho, info, "razão entre cada estimativa de população e a contagem por endereços no mesmo polígono, por delimitação; eixo horizontal logarítmico; linha de referência em 1",
                       titulo="Razão entre cada estimativa de população exposta e a contagem por endereços, por delimitação",
                       fonte="Fontes: SGB (manchas por cota; setorização de risco); IBGE (áreas de risco; CNEFE, setores e grade do Censo 2022); FEPAM (área atingida); ANA (estimativa publicada); MapBiomas (uso do solo).",
                       series={col: {"marcador": m, "cor": cor, "rotulo": rotulos[col]} for col, (m, cor, _) in SERIES.items()}, eixo_horizontal=list(LIM_RAZAO),
                       numero_publicado_de_quem={r.linha: (f"{quem[r.chave]}" + ("" if quem[r.chave] == r.fonte else f" — estimativa sobre a delimitação da {r.fonte}") if r.chave in quem.index
                                                           else "sem número publicado para esta linha") for r in t.itertuples()},
                       valores_fora_do_eixo=fora, tabelas=["sintese-tres-metodos", "sintese-publicado-x-contagem"],
                       rotulos_das_linhas={r.linha: f"{r.rotulo_da_linha} — {r.fonte}" for r in t.itertuples()},
                       cotas_nos_rotulos="com ponto de milhar, para leitura da figura; nas tabelas e nas chaves, sem ponto")


def figura_regua(anual: pd.DataFrame, info_serie: dict, cotas: list[int], destaque: list[int], verificacao: dict) -> dict:
    """Máxima anual da régua: uma haste por ano, as cotas das cheias mapeadas como linhas horizontais, anos em destaque;
    círculo vazado no topo da haste dos anos com cota em menos de --dias-minimos dias."""
    com = anual[anual.tem_dado]
    poucos = com[com.dias_com_cota < ARGS.dias_minimos]
    sem = anual[~anual.tem_dado]
    mx = com.set_index("ano").maxima_cm
    a0, a1 = int(anual.ano.min()), int(anual.ano.max())
    handles = [Line2D([], [], color=COR_HASTE, lw=1.5, label="máxima anual da régua"),
               Line2D([], [], color=COR_DESTAQUE, lw=1.9, label="ano em destaque: " + " e ".join(str(a) for a in destaque)),
               Line2D([], [], color=COR_COTA, lw=0.7, ls=(0, (5, 2)), label="cotas das cheias mapeadas (SGB)")]
    if len(poucos):
        handles.append(Line2D([], [], marker="o", ms=4.2, mfc=lm.FUNDO, mec=COR_HASTE, mew=1.0, ls="", label=f"ano com cota em menos de {ARGS.dias_minimos} dias"))
    if len(sem):
        handles.append(Line2D([], [], marker="x", ms=4.5, mew=1.0, color=INK2, ls="", label="ano sem dado"))

    def montar(fig, H):
        ax = _eixo(fig, H, 0.60, 0.58, 3.0, topo=lm.TOPO + 0.07)
        normais = com[~com.ano.isin(destaque)]
        ax.vlines(normais.ano, 0, normais.maxima_cm, color=COR_HASTE, lw=1.5, zorder=3)
        ax.vlines(destaque, 0, mx[destaque], color=COR_DESTAQUE, lw=1.9, zorder=4)
        for r in poucos.itertuples():  # a máxima desses anos é a dos dias que têm cota: contorno na cor da haste, miolo na cor do fundo
            ax.plot(r.ano, r.maxima_cm, marker="o", ms=4.2, mfc=lm.FUNDO, mec=COR_DESTAQUE if r.ano in destaque else COR_HASTE, mew=1.0, ls="", zorder=5)
        topo = float(np.ceil((mx.max() * 1.13) / 100) * 100)
        ax.set_xlim(a0 - 1, a1 + 1)
        ax.set_ylim(0, topo)
        for i, k in enumerate(cotas):
            ax.axhline(k, color=COR_COTA, lw=0.7, ls=(0, (5, 2)), zorder=2)
            # rótulo na margem direita; cotas próximas: uma abaixo e a outra acima da sua linha
            perto_acima = i + 1 < len(cotas) and cotas[i + 1] - k < 0.06 * topo
            perto_abaixo = i > 0 and k - cotas[i - 1] < 0.06 * topo
            va = "top" if perto_acima else ("bottom" if perto_abaixo else "center")
            ax.annotate(f"{num(k)} cm", (1, k), xycoords=("axes fraction", "data"), xytext=(3, 0), textcoords="offset points", ha="left", va=va,
                        fontsize=lm.FS_ROTULO_MIN, color=INK2, annotation_clip=False)
        if len(sem):
            ax.plot(sem.ano, np.zeros(len(sem)), marker="x", ms=4.5, mew=1.0, color=INK2, ls="", clip_on=False, zorder=6)
        vao = 0.11 * (a1 - a0)  # meia largura reservada ao rótulo, em anos
        for a in destaque:
            direita = a > a1 - vao
            x0, x1 = (a - 2 * vao, a + 1) if direita else (a - vao, a + vao)
            viz = mx[(mx.index >= x0) & (mx.index <= x1)].max()  # o rótulo fica acima das hastes vizinhas
            ax.annotate(f"{a} — {num(mx[a])} cm", (a, mx[a]), xytext=(a + 0.6 if direita else a, viz + 0.035 * topo), textcoords="data", ha="right" if direita else "center", va="bottom",
                        fontsize=lm.FS_ROTULO_MIN, fontweight="bold", color=lm.INK, zorder=7, bbox=dict(fc=lm.FUNDO, ec="none", pad=0.8),
                        arrowprops=dict(arrowstyle="-", color=COR_DESTAQUE, lw=0.5, shrinkA=0, shrinkB=1) if viz > mx[a] else None)
        ax.set_xticks([a for a in range(a0 - a0 % 10, a1 + 1, 10) if a >= a0])
        ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: num(v)))
        ax.grid(axis="y", color=GRADE, lw=0.5)
        ax.set_axisbelow(True)
        ax.set_ylabel("cota na régua (cm)", fontsize=lm.FS_LEGENDA, color=INK2, labelpad=3)
        return ax

    caminho = SAIDA / f"sintese-maxima-anual-da-regua_ana_{a0}-{a1}_anual.png"
    info = pagina_grafico(montar, handles, caminho, ncol=2)
    return meta_figura(caminho, info, "máxima anual da régua, uma haste por ano; cotas das cheias mapeadas como linhas horizontais; anos sem dado marcados no eixo, sem haste",
                       titulo=f"Máxima anual do nível do rio na régua, {a0}–{a1}, e cotas das cheias mapeadas",
                       fonte="Fontes: ANA (série histórica de cotas da estação fluviométrica); SGB (cotas das manchas de inundação).",
                       cotas_das_cheias_cm=cotas, anos_em_destaque={int(a): float(mx[a]) for a in destaque}, dias_minimos=ARGS.dias_minimos,
                       anos_com_cota_em_menos_dias_que_o_minimo={int(r.ano): int(r.dias_com_cota) for r in poucos.itertuples()},
                       valor_dos_rotulos="série histórica diária em todos os anos, também nos anos em destaque", verificacao_dos_anos_em_destaque=verificacao, **info_serie)


# ---------------------------------------------------------------- principal
def main() -> None:
    if SAIDA.exists() and any(SAIDA.iterdir()) and not ARGS.refazer:
        raise SystemExit(f"{SAIDA.relative_to(c.RAIZ)} já tem arquivos: a síntese já foi gerada (use --refazer para gerar de novo)")
    SAIDA.mkdir(parents=True, exist_ok=True)
    cod = ARGS.codigo_ibge
    eo.ARGS = ec.ARGS = ARGS
    eo.SAIDA = ec.SAIDA = SAIDA  # tudo o que as funções reaproveitadas gravam fica na pasta da síntese; os produtos dos estudos não são tocados
    limite = c.carregar_area_estudo()
    st = gpd.read_file(c.CAMADAS / "populacao-setores_ibge-censo_2022_setor.gpkg")
    parte_do_setor, sede = classes_de_setor(st)
    d = eo.aplicar_publicado(eo.carregar_delimitacoes(cod, limite, st), ARGS.publicado)
    cont = d[d.fonte != eo.CHEIAS_NC].reset_index(drop=True)  # manchas sempre cumulativas
    base = cont[(cont.fonte == eo.CHEIAS) | (cont.tipo == "total")].reset_index(drop=True)
    K = list(base[base.fonte == eo.CHEIAS].delimitacao)
    tr = ee.carregar_cotas()[0].tr_anos
    lin = linhas_da_sintese(base, tr)
    pol = montar_poligonos(base, st, parte_do_setor)
    serie = ARGS.serie_nivel or next(iter(sorted(c.RAW.glob(PADRAO_SERIE))), None)
    if serie is None:
        raise FileNotFoundError(f"série histórica do nível do rio ausente em data/raw/ ({PADRAO_SERIE}): rode scripts/download/nivel_rio_ana.py --serie-historica")
    anual, info_serie = maximas_anuais(serie)
    destaque = ARGS.anos_destaque or sorted({int(anual.loc[anual.maxima_cm.idxmax(), "ano"]), *(int(f["ano"]) for f in eo.FONTES if f["id"] == eo.FONTE_COM_COTA)})
    destaque = [a for a in destaque if a in set(anual[anual.tem_dado].ano)]
    verificacao = {int(a): verificar_maxima(serie, a) for a in destaque}

    # ---- contagem B1/B2 em todos os polígonos, pela função do estudo de cenários
    t, pts = ec.expor(pol, "sintese", origem={"delimitacoes": [str(f["arquivo"].relative_to(c.RAIZ)) for f in eo.FONTES], "cheias": str(ee.ARQ_COTAS.relative_to(c.RAIZ))})
    col = {r.cenario: f"exp_{i + 1:02d}" for i, r in enumerate(pol.itertuples())}
    for arq in (SAIDA / "enderecos-expostos-por-cenario_sintese-ibge-cnefe_2022_municipal.csv", SAIDA / "enderecos-exposicao-cenarios_sintese-ibge-cnefe_2022_pontos.gpkg"):
        meta(arq, descricao="saída direta da função de contagem para os polígonos da síntese (delimitação inteira, por parte e com a borda deslocada)", colunas_por_poligono=col,
             aviso="camada de pontos: não sai de data/ e não é publicada" if arq.suffix == ".gpkg" else None)
    nivel = gpd.read_file(ec.ARQ_PONTOS, columns=["COD_UNICO_ENDERECO", "NV_GEO_COORD"], ignore_geometry=True)
    if not (nivel.COD_UNICO_ENDERECO.values == pts.COD_UNICO_ENDERECO.values).all():
        raise ValueError("a camada de pontos mudou de ordem entre as leituras")
    pts["preciso"] = nivel.NV_GEO_COORD.astype(str).isin(NIVEIS_PRECISOS).values
    pts["parte"] = pts.setor_2022.map(parte_do_setor)
    pop_mun = float(st["pop"].sum())
    cnt = pol.drop(columns=["geometry", "valor"]).merge(t.rename(columns={"enderecos_total": "enderecos", "pop_estimada_setor_total": "pop_setor", "pop_estimada_grade_total": "pop_grade"})
                                                        [["cenario", "enderecos", "pop_setor", "pop_grade"]], on="cenario")
    cnt["enderecos_precisos"] = [int(pts.loc[pts[col[x]], "preciso"].sum()) for x in cnt.cenario]
    cnt["pct_enderecos_precisos"] = 100 * cnt.enderecos_precisos / cnt.enderecos.replace(0, np.nan)
    ch = lambda r, rec=None, borda=0.0: f"{r.fonte_id}|{r.delimitacao}|{rec or r.recorte}|{borda:+.0f}"  # noqa: E731
    C = cnt.set_index("cenario")

    # ---- A1: cidade × resto do município
    a1 = cnt[cnt.borda_m == 0].copy()
    a1["enderecos_pelo_setor_do_endereco"] = [int(pts[col[f"{r.fonte}|{r.delimitacao}|{MUNICIPIO}|+0"]].sum()) if r.recorte == MUNICIPIO
                                              else int((pts[col[f"{r.fonte}|{r.delimitacao}|{MUNICIPIO}|+0"]] & (pts.parte == r.recorte)).sum()) for r in a1.itertuples()]
    a1["pct_dos_enderecos_da_delimitacao"] = 100 * a1.enderecos / a1.groupby(["fonte", "delimitacao"]).enderecos.transform("first").replace(0, np.nan)
    a1 = a1[["fonte", "delimitacao", "recorte", "area_km2", "enderecos", "pop_setor", "pop_grade", "pct_dos_enderecos_da_delimitacao", "enderecos_pelo_setor_do_endereco"]]
    # o que a malha de bairros deixa de fora na área do evento datado, pela parte do setor do endereço
    bairros = gpd.read_file(eo.ARQ_BAIRROS).to_crs(c.CRS_PADRAO)
    jb = gpd.sjoin(pts[["geometry"]], bairros[["NM_BAIRRO", "geometry"]], predicate="within", how="left")
    pts["na_malha_de_bairros"] = jb[~jb.index.duplicated()].NM_BAIRRO.notna()
    pts["no_distrito_sede"] = pts.setor_2022.map(st.set_index("CD_SETOR").CD_DIST) == sede
    ev = pts[pts[col[f"{eo.FONTE_COM_COTA}|total|{MUNICIPIO}|+0"]]]
    fb = ev[~ev.na_malha_de_bairros]
    fora_bairros = {"enderecos_na_area": len(ev), "fora_da_malha_de_bairros": len(fb), "pop_fora_da_malha_de_bairros": float(fb.pop_est_setor.sum()),
                    "fora_da_malha_e_em_setor_da_cidade": int((fb.parte == PARTES[0]).sum()), "na_malha_e_fora_dos_setores_da_cidade": int((ev.na_malha_de_bairros & (ev.parte != PARTES[0])).sum()),
                    "em_setor_urbano_de_outro_distrito": int((fb.parte == PARTES[1]).sum()), "em_setor_rural_de_outro_distrito": int(((fb.parte == PARTES[2]) & ~fb.no_distrito_sede).sum()),
                    "em_setor_rural_do_distrito_sede": int(((fb.parte == PARTES[2]) & fb.no_distrito_sede).sum())}
    definicao = {"cidade": f"setores de 2022 com SITUACAO = 'Urbana' e CD_DIST = {sede} (distrito do setor mais populoso; a mesma regra de 'urbana da sede' dos outros scripts)",
                 PARTES[1]: "SITUACAO = 'Urbana' e outro CD_DIST", PARTES[2]: "SITUACAO = 'Rural'",
                 "setores_por_parte": parte_do_setor.value_counts().to_dict(), "corte": "polígono da delimitação ∩ união dos setores da parte; endereço contado pelo ponto dentro do polígono cortado"}
    gravar(a1, "apoio-cidade-e-resto-do-municipio_sgb-ibge-fepam-cnefe_2022_parte", "endereços de domicílio particular e população (por setor e pela grade) de cada delimitação, no município e em três partes",
           definicao_das_partes=definicao, area_do_evento_e_malha_de_bairros=fora_bairros,
           enderecos_pelo_setor_do_endereco="conferência: a mesma contagem pela parte do setor atribuído ao endereço, em vez do corte do polígono")

    # ---- A2 e três métodos: área do setor e uso do solo sobre os mesmos polígonos (funções do cálculo anterior)
    pm = pol[pol.cenario.isin([ch(r) for r in lin.itertuples()])]
    ar = eo.populacao_por_area_e_uso_do_solo(pm).set_index("cenario")
    b2 = lin.copy()
    for k_, v_ in (("pop_area_setor", ar.pop_area_setor), ("pop_uso_solo", ar.pop_uso_solo), ("pop_enderecos_setor", C.pop_setor), ("enderecos", C.enderecos), ("area_km2", C.area_km2)):
        b2[k_] = [float(v_[ch(r)]) for r in lin.itertuples()]
    b2["razao_area_sobre_enderecos"] = b2.pop_area_setor / b2.pop_enderecos_setor.replace(0, np.nan)
    b2["razao_uso_solo_sobre_enderecos"] = b2.pop_uso_solo / b2.pop_enderecos_setor.replace(0, np.nan)
    a2 = b2[b2.fonte_id == eo.FONTE_COM_COTA][["linha", "recorte", "area_km2", "enderecos", "pop_area_setor", "pop_enderecos_setor", "razao_area_sobre_enderecos"]]
    gravar(a2, f"apoio-area-do-setor-na-cidade_{eo.FONTE_COM_COTA}-ibge_2022_recorte", "método por área do setor e contagem por endereços na área do evento datado: só na cidade (polígono cortado pelos setores da cidade) e no município")

    # ---- A3: sensibilidade à posição dos pontos
    b = ARGS.borda_m
    a3 = []
    for r in lin.itertuples():
        m, rec, av = C.loc[ch(r)], C.loc[ch(r, borda=-b)], C.loc[ch(r, borda=b)]
        a3.append({"linha": r.linha, "fonte": r.fonte, "recorte": r.recorte, "enderecos": m.enderecos, "pop_setor": m.pop_setor,
                   "enderecos_borda_recuada": rec.enderecos, "pop_setor_borda_recuada": rec.pop_setor, "enderecos_borda_avancada": av.enderecos, "pop_setor_borda_avancada": av.pop_setor,
                   "var_pct_enderecos_recuada": 100 * (rec.enderecos / m.enderecos - 1) if m.enderecos else np.nan, "var_pct_enderecos_avancada": 100 * (av.enderecos / m.enderecos - 1) if m.enderecos else np.nan,
                   "enderecos_precisos": m.enderecos_precisos, "pct_enderecos_precisos": m.pct_enderecos_precisos})
    a3 = pd.DataFrame(a3)
    gravar(a3, "apoio-sensibilidade-a-posicao-dos-pontos_sgb-ibge-fepam-cnefe_2022_delimitacao", f"endereços e população com a borda de cada polígono recuada e avançada {b:g} m; endereços com coordenada de nível 1 ou 2",
           borda_m=b, metodo=f"buffer de −{b:g} m e de +{b:g} m no polígono da delimitação ({c.CRS_PADRAO}); na linha da cidade, o deslocamento é da borda da delimitação e o corte pelos setores vem depois",
           coordenada_precisa=f"NV_GEO_COORD em {list(NIVEIS_PRECISOS)}")

    # ---- A4 e perfil: a mesma função da tabela de perfil por cota do estudo por endereços
    s = st.set_index("CD_SETOR")
    ee.anexar_perfil_do_setor(pts, s)
    perfil = pd.DataFrame([{"chave": r.chave, **ee.perfil_dos_expostos(pts[pts[col[ch(r)]]], casas=None)} for r in lin.itertuples()])
    pub = pd.read_csv(TAB_ESTUDO / "perfil-expostos-por-cota_sgb-ibge_2022_municipal.csv").set_index("cota_cm")
    pub_ref = json.loads((TAB_ESTUDO / "perfil-expostos-por-cota_sgb-ibge_2022_municipal.json").read_text(encoding="utf-8"))["referencia"]["municipio"]
    for r in lin[lin.fonte_id == eo.CHEIAS].itertuples():
        arred = ee.perfil_dos_expostos(pts[pts[col[ch(r)]]])
        for v in pub.columns:
            confere(f"perfil, {r.linha}: {v}", "perfil-expostos-por-cota (estudo por endereços)", pub.loc[int(r.delimitacao), v], arred[v])
    mun = ee.perfil_do_municipio(s, pts, casas=None)
    for v, x in ee.perfil_do_municipio(s, pts).items():
        confere(f"perfil, município: {v}", "perfil-expostos-por-cota (.json, referência)", pub_ref[v], x)
    b4 = lin[lin.chave != f"{eo.FONTE_COM_COTA}|total|{MUNICIPIO}"].merge(perfil, on="chave")
    b4 = pd.concat([b4, pd.DataFrame([{"linha": "município", "fonte": "IBGE", "recorte": MUNICIPIO, "pop_estimada": pop_mun, "pct_60_mais": mun["pct_60_mais"], "pct_0_14": mun["pct_0_14"],
                                       "pct_dom_1_morador": mun["pct_dom_1_morador_media_enderecos"], "pop_est_sigilo_60": float(pts.loc[pts.pct60.isna(), "pop_est_setor"].sum()),
                                       "pop_est_sigilo_0_14": float(pts.loc[pts.pct014.isna(), "pop_est_setor"].sum()), "enderecos_sigilo_dom1": int(pts.pctdom1.isna().sum())}])], ignore_index=True)
    b4 = b4[["linha", "fonte", "recorte", "pop_estimada", "pct_0_14", "pct_60_mais", "pct_dom_1_morador", "pop_est_sigilo_0_14", "pop_est_sigilo_60", "setores_sigilo_0_14", "setores_sigilo_60",
             "enderecos_sigilo_dom1", "setores_sigilo_dom1"]]
    gravar(b4[~b4.linha.str.startswith("cheia") & (b4.linha != "município")], "apoio-perfil-nas-delimitacoes-sem-perfil_sgb-ibge-fepam_2022_delimitacao",
           "perfil da população estimada nas delimitações que ainda não o tinham, pela função da tabela de perfil por cota")

    # ---- tabela 1: delimitações e contagem
    b1 = lin.copy()
    for k_, v_ in (("area_km2", "area_km2"), ("enderecos", "enderecos"), ("pop_enderecos_setor", "pop_setor"), ("pop_enderecos_grade", "pop_grade"), ("pct_enderecos_precisos", "pct_enderecos_precisos")):
        b1[k_] = [float(C.loc[ch(r), v_]) for r in lin.itertuples()]
    b1["pct_pop_municipio"] = 100 * b1.pop_enderecos_setor / pop_mun
    b1["enderecos_borda_recuada"] = [float(C.loc[ch(r, borda=-b), "enderecos"]) for r in lin.itertuples()]
    b1["enderecos_borda_avancada"] = [float(C.loc[ch(r, borda=b), "enderecos"]) for r in lin.itertuples()]
    sint_origem = pd.read_csv(ORIGEM / "sintese_delimitacoes-oficiais-x-contagem_2022_delimitacao.csv")
    if "cota_maxima_cm" in sint_origem and sint_origem.cota_maxima_cm.notna().any():  # cota máxima do rio no evento datado, da tabela do estudo
        cm = sint_origem[sint_origem.cota_maxima_cm.notna()].iloc[0]
        m = b1.fonte_id == eo.FONTE_COM_COTA
        b1.loc[m, "cota_cm"] = cm.cota_maxima_cm
        b1.loc[m, "a_que_se_refere"] += f"; cota máxima na régua no período: {num(cm.cota_maxima_cm)} cm"
    # manchas por cota: o que o documento da fonte diz (período das cheias observadas, tempo de retorno, página), sem trocar o atributo do serviço
    b1["periodo_de_referencia"] = b1.ano_de_referencia.map(lambda v: "" if pd.isna(v) else str(int(v)))
    b1["tr_anos_documento"], b1["documento_e_pagina"] = np.nan, ""
    doc = manchas_no_documento()
    if doc is not None:
        for i, r in b1[b1.fonte_id == eo.CHEIAS].iterrows():
            if r.delimitacao in doc.index:
                x = doc.loc[r.delimitacao]
                b1.loc[i, ["periodo_de_referencia", "tr_anos_documento", "documento_e_pagina"]] = [x.periodo_de_referencia, float(x.tr_anos_documento), f"{x.documento}; {x.pagina}"]
    ano_ev = next(int(f["ano"]) for f in eo.FONTES if f["id"] == eo.FONTE_COM_COTA)
    maxima_do_evento = {"nas_tabelas": "máxima da telemetria (tabela do estudo das delimitações oficiais)", "na_figura_da_regua": "máxima da série histórica diária, como em todos os anos",
                        **verificacao.get(ano_ev, {})}
    b1 = b1[["linha", "fonte", "ano_de_referencia", "periodo_de_referencia", "a_que_se_refere", "cota_cm", "tr_anos", "tr_anos_documento", "documento_e_pagina", "recorte", "area_km2", "enderecos", "pop_enderecos_setor", "pop_enderecos_grade", "pct_pop_municipio",
             "enderecos_borda_recuada", "enderecos_borda_avancada", "pct_enderecos_precisos", "chave"]]
    nota_base = (f"Endereços de domicílio particular (CNEFE) e população do Censo 2022; população por setor = população do setor repartida igualmente entre os seus endereços (estimativa); "
                 f"manchas por cota cumulativas. Fontes: SGB, IBGE, FEPAM e ANA; anos de referência das delimitações na tabela.")
    gravar_sintese(b1.drop(columns="chave"), "sintese-delimitacoes-e-contagem_sgb-ibge-fepam-cnefe_2022_delimitacao", "Tabela 1 — Delimitações e contagem por endereços (2022)",
                   {"linha": ("Delimitação", None), "fonte": ("Fonte", None), "periodo_de_referencia": ("Ano de referência", None), "a_que_se_refere": ("A que se refere", None),
                    "tr_anos_documento": ("Tempo de retorno no documento (anos)", 1), "documento_e_pagina": ("Documento e página", None), "area_km2": ("Área (km²)", 2),
                    "enderecos": ("Endereços (2022)", 0), "pop_enderecos_setor": ("Pessoas (2022, por setor)", 0), "pop_enderecos_grade": ("Pessoas (pela grade)", 0), "pct_pop_municipio": ("% da população do município", 2),
                    "enderecos_borda_recuada": (f"Endereços, borda a −{b:g} m", 0), "enderecos_borda_avancada": (f"Endereços, borda a +{b:g} m", 0), "pct_enderecos_precisos": ("% de endereços com coordenada precisa", 1)},
                   f"Nota: {nota_base} Borda a −{b:g} m e a +{b:g} m: contagem com o polígono recuado e avançado (sensibilidade à posição dos pontos). Coordenada precisa: nível 1 ou 2 do CNEFE. "
                   f"População do município (soma dos setores, 2022): {num(pop_mun)}. Ano de referência das áreas de risco: o dos dados (Censo 2010); a publicação é de 2018. " + (
                       "Manchas por cota: período das cheias observadas que o documento da fonte informa (ele não dá a data de cada cota); o tempo de retorno em 'A que se refere' é o atributo do serviço da fonte "
                       "e o do documento fica na sua coluna, sem escolha entre os dois." if doc is not None else "Manchas por cota: o serviço da fonte não informa ano."),
                   "uma linha por delimitação: fonte, ano, a que se refere, área, endereços, população por setor e pela grade, % da população do município, sensibilidade da borda e coordenada precisa",
                   populacao_do_municipio=pop_mun, borda_m=b, definicao_de_cidade=definicao["cidade"], maxima_do_evento_datado=maxima_do_evento,
                   manchas_por_cota_no_documento=doc.attrs["arquivo"] if doc is not None else "transcrição ausente: campos vazios",
                   ano_de_referencia="coluna numérica, vazia nas manchas por cota (o documento dá um período, não um ano por cota); periodo_de_referencia traz o período ou o ano, para leitura")

    # ---- tabela 2: três métodos sobre os mesmos polígonos
    gravar_sintese(b2[["linha", "fonte", "recorte", "area_km2", "pop_area_setor", "pop_uso_solo", "pop_enderecos_setor", "razao_area_sobre_enderecos", "razao_uso_solo_sobre_enderecos"]],
                   "sintese-tres-metodos_sgb-ibge-fepam-cnefe_2022_delimitacao", "Tabela 2 — População de 2022 por três métodos sobre os mesmos polígonos",
                   {"linha": ("Delimitação", None), "fonte": ("Fonte", None), "pop_area_setor": ("Método por área do setor", 0), "pop_uso_solo": ("Método por uso do solo", 0),
                    "pop_enderecos_setor": ("Por endereços", 0), "razao_area_sobre_enderecos": ("Razão área/endereços", 2), "razao_uso_solo_sobre_enderecos": ("Razão uso do solo/endereços", 2)},
                   "Nota: população do Censo 2022 (IBGE). Método por área do setor: população do setor × fração da área do setor dentro do polígono. Método por uso do solo: o mesmo, com a fração da área urbanizada "
                   "(MapBiomas, 2024) do setor. Por endereços: população do setor repartida entre os endereços do CNEFE 2022 e somada nos endereços dentro do polígono. Manchas por cota cumulativas.",
                   "população de 2022 pelos métodos por área do setor, por uso do solo e por endereços, e as razões para a contagem por endereços")

    # ---- tabela 3: números publicados × contagem no mesmo polígono
    A = cont.set_index("cenario")
    dec = pd.read_csv(ORIGEM / f"decomposicao-ano-e-setor-inteiro_{eo.FONTE_SETOR_INTEIRO}_2010-2022_passos.csv")
    fr = pd.read_csv(ORIGEM / f"fracao-area-urbanizada-x-fracao-enderecos_{eo.FONTE_COM_COTA}-ibge_2019-2024_classe.csv").set_index("classe")
    nome = {f["id"]: f["nome"] for f in eo.FONTES}
    for arq in ARGS.publicado:  # número lido em publicação: quem publica é a instituição da transcrição, que pode não ser a da delimitação
        quem = Path(arq).name.split("_")[1].split("-")[0].upper()
        for fid in pd.read_csv(arq).fonte.unique():
            sigla, desc = nome[fid].split(" — ")
            if quem != sigla:
                nome[fid] = f"{quem} — estimativa sobre a {desc} da {sigla}"
    b3 = []
    for f in [x for x in eo.FONTES if x["id"] != eo.FONTE_COM_COTA] + [x for x in eo.FONTES if x["id"] == eo.FONTE_COM_COTA]:
        r = A.loc[f"{f['id']}|total"]
        contagem = float(C.loc[f"{f['id']}|total|{MUNICIPIO}|+0", "pop_setor"])
        if pd.isna(r.fonte_pessoas):
            continue
        regra = r.fonte_o_que_conta
        if pd.notna(r.fonte_pessoas_por_edificacao):
            regra += f"; {num(r.fonte_pessoas_por_edificacao, 2)} pessoas por edificação ({num(r.fonte_edificacoes)} edificações)"
        b3.append({"linha": nome[f["id"]] + (": a área inteira" if f["id"] == eo.FONTE_COM_COTA else ""), "chave": f"{f['id']}|total|{MUNICIPIO}", "documento": r.fonte_documento, "pagina": r.fonte_pagina, "regra": regra,
                   "publicado": float(r.fonte_pessoas), "contagem": contagem, "razao": float(r.fonte_pessoas) / contagem, "o_que_e_a_razao": "publicado / contagem por endereços no mesmo polígono"})
        confere(f"publicado, {f['id']}", "contagem-por-delimitacao", pd.read_csv(ORIGEM / "contagem-por-delimitacao_fontes-oficiais-ibge-cnefe_2022_delimitacao.csv").set_index("cenario").fonte_pessoas[f"{f['id']}|total"], r.fonte_pessoas)
        if f["id"] == eo.FONTE_SETOR_INTEIRO:
            p = dec.set_index("passo")
            i, ii, iii = p.iloc[0], p.iloc[2], p.iloc[3]
            confere("decomposição: contagem nas áreas inteiras", "decomposicao-ano-e-setor-inteiro", iii.pessoas, contagem)
            b3.append({"linha": "  … efeito do ano: os mesmos setores de 2010 inteiros, contados em 2022", "documento": r.fonte_documento, "pagina": r.fonte_pagina, "regra": "contagem de 2022 dentro da união dos setores de 2010 inteiros",
                       "publicado": float(i.pessoas), "contagem": float(ii.pessoas), "razao": float(ii.razao_pessoas), "o_que_e_a_razao": "contagem de 2022 / publicado, no mesmo polígono (efeito do ano)"})
            b3.append({"linha": "  … efeito do setor inteiro: dos setores inteiros às áreas inteiras", "documento": "", "pagina": "", "regra": "contagem de 2022 nas áreas inteiras sobre a contagem nos setores inteiros",
                       "publicado": float(ii.pessoas), "contagem": float(iii.pessoas), "razao": float(iii.razao_pessoas), "o_que_e_a_razao": "contagem nas áreas inteiras / contagem nos setores inteiros (as duas de 2022)"})
        if f["id"] == eo.FONTE_COM_COTA:
            for classe, rot in (("todas as classes", "só a área urbanizada atingida"), ("Densidade: Densa", "  … ocupação densa"), ("Densidade: Pouco densa", "  … ocupação pouco densa")):
                if classe not in fr.index:
                    continue
                x, todas = fr.loc[classe], classe == "todas as classes"
                b3.append({"linha": (nome[f["id"]] + ": " if todas else "") + rot, "documento": r.fonte_documento if todas else "", "pagina": r.fonte_pagina if todas else "",
                           "regra": "contagem por endereços só na área urbanizada (IBGE, 2019) dentro da área atingida" if todas else "a mesma área urbanizada, por classe de densidade",
                           "publicado": float(r.fonte_pessoas) if todas else np.nan, "contagem": float(x.pop_enderecos_setor_atingida), "razao": float(r.fonte_pessoas) / float(x.pop_enderecos_setor_atingida) if todas else np.nan,
                           "o_que_e_a_razao": "publicado / contagem por endereços na área urbanizada atingida" if todas else "",
                           "fracao_de_area_pct": float(x.fracao_de_area_pct), "fracao_de_enderecos_pct": float(x.fracao_de_enderecos_pct), "fracao_de_populacao_pct": float(x.fracao_de_populacao_pct)})
            x = fr.loc["todas as classes"]
            dif = 100 * (float(r.fonte_pessoas) - float(x.estimativa_fracao_de_area_x_pop_urbana)) / float(r.fonte_pessoas)
            b3.append({"linha": "  … conta refeita pela regra da fonte", "documento": "", "pagina": "",
                       "regra": f"fração da área urbanizada dentro da área atingida ({num(x.fracao_de_area_pct, 2)} %) × população urbana do município ({num(x.populacao_urbana_municipio)})",
                       "publicado": float(r.fonte_pessoas), "contagem": float(x.estimativa_fracao_de_area_x_pop_urbana), "razao": float(r.fonte_pessoas) / float(x.estimativa_fracao_de_area_x_pop_urbana),
                       "o_que_e_a_razao": f"publicado / conta refeita; diferença de {num(dif, 1)} % do publicado, não explicada (a coluna 'contagem' traz aqui a conta refeita, não uma contagem)",
                       "fracao_de_area_pct": float(x.fracao_de_area_pct), "diferenca_pct_nao_explicada": dif})
    b3 = pd.DataFrame(b3)
    b3.insert(1, "fonte", b3.linha.where(~b3.linha.str.startswith("  …")).ffill().str.split(" — ").str[0])
    gravar_sintese(b3.drop(columns="chave"), "sintese-publicado-x-contagem_sgb-ibge-ana-cnefe_2010-2024_delimitacao", "Tabela 3 — Números publicados e contagem por endereços no mesmo polígono",
                   {"linha": ("Delimitação e passo", None), "fonte": ("Fonte", None), "documento": ("Documento", None), "pagina": ("Página", None), "regra": ("Regra do número publicado", None),
                    "publicado": ("Publicado ou valor de partida (pessoas)", "auto"), "contagem": ("Contagem (pessoas, 2022)", 0), "razao": ("Razão", 3), "o_que_e_a_razao": ("O que é a razão", None),
                    "fracao_de_area_pct": ("Fração da área urbanizada (%)", 2), "fracao_de_enderecos_pct": ("Fração dos endereços (%)", 2), "fracao_de_populacao_pct": ("Fração da população (%)", 2)},
                   "Nota: contagem = população do Censo 2022 por endereços (CNEFE 2022) dentro do polígono de cada fonte. Números publicados: SGB (setorização de risco, 2014, atributo da geometria); "
                   "IBGE (população em áreas de risco, 2018, dados do Censo 2010); ANA (2025, estimativa sobre a área diretamente atingida de maio de 2024, da FEPAM). Linhas com '…' abrem a linha anterior. "
                   "Frações: parte da área urbanizada (IBGE, 2019), dos seus endereços e da sua população que fica dentro da área atingida. Na linha da conta refeita, a coluna de contagem traz a conta refeita; "
                   "a diferença para o número publicado fica declarada como não explicada.",
                   "números publicados pelas fontes ao lado da contagem por endereços no mesmo polígono; a fonte de setor inteiro aberta em efeito do ano e efeito do setor inteiro; a estimativa sobre a área do evento "
                   "datado aberta em área urbanizada (frações de área, de endereços e de população, por densidade) e na conta refeita",
                   tabelas_de_origem=[str((ORIGEM / n).relative_to(c.RAIZ)) for n in (f"decomposicao-ano-e-setor-inteiro_{eo.FONTE_SETOR_INTEIRO}_2010-2022_passos.csv",
                                                                                       f"fracao-area-urbanizada-x-fracao-enderecos_{eo.FONTE_COM_COTA}-ibge_2019-2024_classe.csv")],
                   transcricoes=[str(Path(x).relative_to(c.RAIZ)) for x in ARGS.publicado],
                   leitura={"publicado": [0 if x == y else "auto" for x, y in zip(b3.publicado, b3.contagem.shift())]})  # valor de partida que é a contagem da linha anterior: sem casas

    # ---- tabela 4: perfil dos expostos
    gravar_sintese(b4, "sintese-perfil-dos-expostos_sgb-ibge-fepam_2022_delimitacao", "Tabela 4 — Perfil da população estimada em cada delimitação (2022)",
                   {"linha": ("Delimitação", None), "fonte": ("Fonte", None), "pop_estimada": ("População estimada", 0), "pct_0_14": ("% de 0 a 14 anos", 1), "pct_60_mais": ("% de 60 anos ou mais", 1),
                    "pct_dom_1_morador": ("% de domicílios com um morador", 1), "pop_est_sigilo_0_14": ("Pop. em setores sob sigilo (0 a 14)", 0), "pop_est_sigilo_60": ("Pop. em setores sob sigilo (60 ou mais)", 0),
                    "enderecos_sigilo_dom1": ("Endereços em setores sob sigilo (um morador)", 0)},
                   "Nota: IBGE, Censo 2022 (agregados por setor) e CNEFE 2022. Porcentagens de idade do setor ponderadas pela população estimada dentro do polígono; % de domicílios com um morador ponderada pelos endereços; "
                   "setores sob sigilo ficam fora da média e a população estimada (ou os endereços) neles é contada à parte. Município: idade pela soma dos setores; um morador pela média dos endereços. Manchas por cota cumulativas.",
                   "perfil da população estimada por delimitação, pela função da tabela de perfil por cota do estudo por endereços; valores sem arredondar")

    # ---- conferência com as tabelas dos estudos de origem
    o_cont = pd.read_csv(ORIGEM / "contagem-por-delimitacao_fontes-oficiais-ibge-cnefe_2022_delimitacao.csv").set_index("cenario")
    o_met = pd.read_csv(ORIGEM / "populacao-tres-metodos_fontes-oficiais-sgb-ibge_2022_delimitacao.csv").set_index("cenario")
    o_cota = pd.read_csv(TAB_ESTUDO / "enderecos-populacao-por-cota_sgb-ibge-cnefe_2022_municipal.csv").set_index("cota_cm")
    for r in b1[b1.recorte == MUNICIPIO].itertuples():
        k = "|".join(r.chave.split("|")[:2])
        for v_, o_ in (("enderecos", "enderecos_domicilio_particular"), ("pop_enderecos_setor", "pop_enderecos_setor"), ("pop_enderecos_grade", "pop_enderecos_grade"), ("area_km2", "area_km2")):
            confere(f"{r.linha}: {v_}", "contagem-por-delimitacao", o_cont.loc[k, o_], getattr(r, v_))
        x = b2.set_index("chave").loc[r.chave]
        for v_ in ("pop_area_setor", "pop_uso_solo", "razao_area_sobre_enderecos", "razao_uso_solo_sobre_enderecos"):
            confere(f"{r.linha}: {v_}", "populacao-tres-metodos", o_met.loc[k, v_], x[v_])
        if r.chave.startswith(eo.CHEIAS):
            confere(f"{r.linha}: endereços com coordenada precisa", "enderecos-populacao-por-cota (estudo por endereços)", o_cota.loc[int(r.chave.split("|")[1]), "enderecos_precisos_niv1_2"],
                    C.loc[r.chave + "|+0", "enderecos_precisos"])
    for sit, partes in (("situação urbana", PARTES[:2]), ("situação rural", PARTES[2:])):  # o corte por situação do estudo de origem = soma das partes daqui
        if f"{eo.FONTE_COM_COTA}|{sit}" in o_cont.index:
            confere(f"área do evento datado, {sit}: endereços", "contagem-por-delimitacao", o_cont.loc[f"{eo.FONTE_COM_COTA}|{sit}", "enderecos_domicilio_particular"],
                    sum(C.loc[f"{eo.FONTE_COM_COTA}|total|{p}|+0", "enderecos"] for p in partes))
    conf = pd.DataFrame(CONFERENCIA)
    gravar(conf, "apoio-conferencia-com-os-estudos-de-origem_sgb-ibge-fepam-cnefe_2022_item", "cada número da síntese que também está numa tabela dos estudos de origem, lado a lado",
           itens=len(conf), nao_conferem=int((~conf.confere).sum()))
    logger.info("Conferência com os estudos de origem: %d itens, %d não conferem", len(conf), int((~conf.confere).sum()))

    # ---- figuras
    figs = [figura_cheias(cont, tr, st, doc), figura_contornos(cont, K[-1]), figura_razoes(b2, b3)]
    gravar(anual, f"apoio-maxima-anual-da-regua_ana_{info_serie['primeiro_ano']}-{info_serie['ultimo_ano_completo']}_anual", "máxima anual da régua e dias com cota em cada ano", **info_serie)
    figs.append(figura_regua(anual, info_serie, [int(k) for k in K], destaque, verificacao))

    pd.set_option("display.width", 250); pd.set_option("display.max_columns", 40); pd.set_option("display.max_colwidth", 50)  # noqa: E702
    for nome_, x in (("A1", a1), ("A2", a2), ("A3", a3), ("tabela 1", b1.drop(columns=["chave", "a_que_se_refere"])), ("tabela 2", b2.drop(columns=["chave", "a_que_se_refere"])),
                     ("tabela 3", b3.drop(columns=["chave", "documento", "pagina", "regra"])), ("tabela 4", b4)):
        print(f"\n== {nome_}\n{x.round(3).to_string(index=False)}")
    print(json.dumps({"definicao": definicao, "malha_de_bairros": fora_bairros, "regua": info_serie}, ensure_ascii=False, indent=1, default=str))

    if not conf.confere.all():
        raise SystemExit("números que não repetem os dos estudos de origem (ver a tabela de conferência): nada foi copiado")
    if ARGS.copia:  # cópia para conferência: só as tabelas de síntese (.csv e .md), as figuras (.png) e os .json irmãos
        ARGS.copia.mkdir(parents=True, exist_ok=True)
        for arq in sorted(SAIDA.glob("sintese-*")):
            if arq.suffix in (".csv", ".md", ".png", ".json"):
                shutil.copy2(arq, ARGS.copia / arq.name)
        logger.info("Cópia para conferência: %d arquivos", len(list(ARGS.copia.glob("sintese-*"))))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    _p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    _p.add_argument("--codigo-ibge", default=None, help="padrão: o código da área de estudo (config/area_estudo.geojson)")
    _p.add_argument("--nome-rio", default=c.NOME_RIO_DEFAULT, help="rótulo do rio principal na legenda")
    _p.add_argument("--publicado", type=Path, nargs="*", help="CSV com números publicados (padrão: as transcrições versionadas)")
    _p.add_argument("--borda-m", type=float, default=12.0, help="deslocamento da borda dos polígonos na sensibilidade à posição dos pontos (m)")
    _p.add_argument("--serie-nivel", type=Path, help="CSV da série histórica diária do nível do rio (padrão: a que estiver em data/raw/)")
    _p.add_argument("--estacao", type=Path, default=ARQ_ESTACAO, help=".json com as coordenadas da estação fluviométrica (campo estacao_selecionada)")
    _p.add_argument("--dias-minimos", type=int, default=330, help="na figura da régua, ano com cota em menos dias que isto ganha um círculo vazado no topo da haste")
    _p.add_argument("--manchas-documento", type=Path, help="CSV com a transcrição do documento da fonte das manchas por cota (padrão: a transcrição versionada, na pasta dos números publicados)")
    _p.add_argument("--anos-destaque", type=int, nargs="*", help="anos em destaque na figura da régua (padrão: o da maior cota da série e o do evento datado)")
    _p.add_argument("--escala-pos", type=float, nargs=2, default=(0.56, 0.05), help="posição da barra de escala no mapa das cheias (fração do quadro)")
    _p.add_argument("--encarte", type=float, nargs=3, default=(0.775, 0.02, 0.215), help="encarte de localização no mapa das cheias: x, y e largura (fração do quadro)")
    _p.add_argument("--copia", type=Path, help="pasta que recebe uma cópia das tabelas de síntese (.csv, .md), das figuras (.png) e dos .json irmãos, para conferência")
    _p.add_argument("--refazer", action="store_true", help="gera de novo mesmo se a pasta de saída já tiver arquivos")
    ARGS = _p.parse_args()
    ARGS.codigo_ibge = ARGS.codigo_ibge or eo.codigo_da_area_de_estudo()
    ARGS.nivel_rio = None
    if ARGS.publicado is None:
        ARGS.publicado = sorted(eo.PUBLICADOS.glob(eo.PADRAO_PUBLICADOS))
    main()
