"""
Relação entre o nível da régua e a área inundada vista por satélite, e comparação
com as manchas por cota do SGB, na área urbana.

Parte dos produtos de agua_observada_sentinel_area_urbana.py: mesmas cenas boas,
mesmas camadas de água (lidas do GeoPackage; nada é reclassificado), mesmo
domínio (retângulo da área urbana, lado brasileiro, em terra = fora do leito de
referência comum, onde a cena tem dado). Das cenas brutas só se relê a máscara
"tem dado", com as funções daquele roteiro.

  A  três definições de água em terra por cena e a comparação com cada mancha:
       A1 "ligada ao rio" (a inundacao_em_terra); A2 "toda a água em terra"
       (agua_total menos o leito); A3 "água solta" (A2 menos A1);
  B  fase da cheia no dia da cena (subida, descida, pico) e área por faixa de nível;
  C  curva crescente da área em função do nível (regressão isotônica, feita com
     numpy: média de vizinhos violados) e o nível em que ela atinge a área de
     cada mancha;
  D  a parte de cada mancha nunca marcada como água (A2) nas cenas de nível igual
     ou maior ("só SGB"), com o uso do solo (o raster do método por uso do solo) e
     a distância aos cursos d'água (rede da ANA, a camada do portal);
  E  as três cenas de maior interseção/união com cada mancha, com A2 e com A1.

Saídas em data/processed/agua_observada_sentinel_comparacao/ (fora do git);
figuras só em --figuras (fora do repositório). Produtos DERIVADOS, para
conferência; não vão ao geoportal.

Uso:
  python scripts/processamento/agua_observada_sentinel_curva_nivel_area.py \
      [--mancha-extra COTA=ARQUIVO.gpkg:CAMADA] [--figuras PASTA] [--copia PASTA]
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
from pathlib import Path

import geopandas as gpd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import rasterio  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402
from rasterio import features  # noqa: E402
from rasterio.transform import from_origin  # noqa: E402
from rasterio.warp import Resampling, reproject  # noqa: E402
from scipy import ndimage  # noqa: E402
from shapely.geometry import box  # noqa: E402

import agua_observada_sentinel as ao  # noqa: E402
import agua_observada_sentinel_area_urbana as au  # noqa: E402
import dinamica_populacional_comum as c  # noqa: E402
import exposicao_inundacao_cenarios as ec  # noqa: E402
import exposicao_inundacao_enderecos as ee  # noqa: E402
import vulnerabilidade_inundacao as vi  # noqa: E402

logger = logging.getLogger(__name__)

SCRIPT = "scripts/processamento/agua_observada_sentinel_curva_nivel_area.py"
MOTIVO = "relação entre o nível da régua e a área inundada vista por satélite, e comparação com as manchas por cota"
PROC44 = au.PROC
PROC = c.RAIZ / "data" / "processed" / "agua_observada_sentinel_comparacao"
RES, RADAR, OPTICO = au.RES, au.RADAR, au.OPTICO
SENSORES = {"radar": [RADAR], "óptico": [OPTICO], "os dois": [RADAR, OPTICO]}
NOME_SENSOR = {RADAR: "radar", OPTICO: "óptico"}
DEFINICOES = {"A1": "ligada ao rio", "A2": "toda a água em terra"}
COR = {"radar": "#2a78d6", "óptico": "#d95f02", "os dois": "#333333"}
COR_VISTA, COR_SO_SGB, COR_DRENAGEM = "#2a78d6", "#d95f02", "#0d366b"
LIMITACOES = [*au.LIMITACOES, "as camadas de água são as do roteiro da área urbana: grupos menores que a área mínima já foram retirados lá, inclusive da água solta",
              "a curva nível × área é crescente por construção e não separa subida de descida", "uso do solo a 30 m, reamostrado por vizinho mais próximo para a grade de 10 m",
              "distância aos cursos d'água medida na grade de 10 m, até o eixo do curso (o rio principal entra pelo eixo, não pela margem)",
              "'só SGB' depende das cenas disponíveis: com poucas cenas no nível da cota, parte dela pode ser só falta de imagem"]


# ---------------------------------------------------------------- gravação
def rel(p: Path) -> str:
    return str(Path(p).relative_to(c.RAIZ))


def meta(caminho: Path, **kw) -> None:
    c.gravar_meta(caminho, crs=c.CRS_PADRAO, status=c.STATUS_CONFERENCIA, ligado_ao_portal=False, script=SCRIPT, motivo=MOTIVO, fontes=FONTES, origem=rel(PROC44),
                  fora_do_git="data/processed/ é ignorado; produto derivado, para conferência", limitacoes=LIMITACOES, avisos=AVISOS or None,
                  argumentos={k: v for k, v in vars(ARGS).items() if k not in ("figuras", "copia", "mancha_extra")}, **kw)


def gravar(t: pd.DataFrame, nome: str, titulo: str, colunas: dict, nota: str = "", casas: dict | None = None, **kw) -> Path:
    """Tabela: .csv (sem arredondar) + .md (leitura) + .json com a descrição de cada coluna."""
    faltam = [x for x in t.columns if x not in colunas]
    if faltam:
        raise KeyError(f"{nome}: coluna sem descrição: {faltam}")
    casas = casas or {}
    arq = PROC / f"{nome}.csv"
    t.to_csv(arq, index=False)
    lin = [f"**{titulo}**", "", "| " + " | ".join(t.columns) + " |", "|" + "|".join("---:" if pd.api.types.is_numeric_dtype(t[x]) and not pd.api.types.is_bool_dtype(t[x]) else "---" for x in t.columns) + "|"]
    lin += ["| " + " | ".join(ao.fmt(v, casas.get(col, 2)) for col, v in zip(t.columns, r)) + " |" for r in t.itertuples(index=False)]
    arq.with_suffix(".md").write_text("\n".join(lin + (["", nota] if nota else []) + [""]), encoding="utf-8")
    meta(arq, titulo=titulo, nota=nota or None, colunas={k: colunas[k] for k in t.columns}, **kw)
    logger.info("Tabela: %s (%d linhas)", rel(arq), len(t))
    return arq


# ---------------------------------------------------------------- contas
def isotonica(x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Regressão isotônica (crescente) pela média de vizinhos violados. Devolve nível, área ajustada e cenas em cada nível."""
    g = pd.DataFrame({"x": x, "y": y}).groupby("x").y.agg(["mean", "size"]).sort_index()
    blocos = []  # [média, peso, níveis no bloco]
    for media, peso in zip(g["mean"], g["size"]):
        blocos.append([float(media), int(peso), 1])
        while len(blocos) > 1 and blocos[-2][0] > blocos[-1][0]:
            m2, p2, n2 = blocos.pop()
            m1, p1, n1 = blocos.pop()
            blocos.append([(m1 * p1 + m2 * p2) / (p1 + p2), p1 + p2, n1 + n2])
    return g.index.to_numpy(dtype=float), np.concatenate([[m] * n for m, _, n in blocos]), g["size"].to_numpy()


def nivel_em_que_atinge(xs: np.ndarray, ys: np.ndarray, area: float) -> tuple[float, str]:
    """Inversão da curva: o menor nível em que a curva (reta entre os pontos ajustados) chega à área."""
    if area > ys[-1]:
        return np.nan, "acima da maior cena"
    i = int(np.argmax(ys >= area))
    if i == 0:
        return float(xs[0]), "no menor nível das cenas ou abaixo dele"
    return float(xs[i - 1] + (xs[i] - xs[i - 1]) * (area - ys[i - 1]) / (ys[i] - ys[i - 1])), "dentro da curva"


def fase_da_cheia(dia: pd.Timestamp, s: pd.Series, pico: pd.Series) -> dict:
    """s: nível por dia (índice diário contínuo); pico: dias que são o maior valor entre três antes e três depois."""
    n0, n2 = s.get(dia, np.nan), s.get(dia - pd.Timedelta(days=2), np.nan)
    tend = "sem dado" if pd.isna(n0) or pd.isna(n2) else "subida" if n0 > n2 else "descida" if n0 < n2 else "igual"
    e_pico = bool(pico.get(dia, False))
    antes = pico[:dia]
    ult = antes[antes].index.max() if antes.any() else pd.NaT
    j60 = s[dia - pd.Timedelta(days=60):dia].dropna()
    return {"nivel_dois_dias_antes_cm": n2, "variacao_em_dois_dias_cm": n0 - n2, "tendencia_em_dois_dias": tend, "e_pico": e_pico, "fase": "pico" if e_pico else tend,
            "data_do_ultimo_pico": f"{ult:%Y-%m-%d}" if pd.notna(ult) else "", "dias_desde_o_ultimo_pico": (dia - ult).days if pd.notna(ult) else np.nan, "nivel_do_ultimo_pico_cm": s.get(ult, np.nan) if pd.notna(ult) else np.nan,
            "maior_nivel_dos_60_dias_antes_cm": j60.max() if len(j60) else np.nan, "dias_desde_o_maior_nivel_dos_60_dias": (dia - j60.idxmax()).days if len(j60) else np.nan}


def rasterizar(geom, forma, transform) -> np.ndarray:
    if geom is None or geom.is_empty:
        return np.zeros(forma, dtype=bool)
    return features.rasterize([(geom, 1)], out_shape=forma, transform=transform, fill=0, dtype="uint8").astype(bool)


# ---------------------------------------------------------------- figuras
def _limpar(ax):
    ax.grid(color="#e3e2dc", lw=0.6)
    ax.set_axisbelow(True)
    for lado in ("top", "right"):
        ax.spines[lado].set_visible(False)
    ax.tick_params(labelsize=8, colors="#52514e")


def figura_nivel_area(arq: Path, cen: pd.DataFrame, curvas: pd.DataFrame, sgb: pd.DataFrame) -> None:
    fig, eixos = plt.subplots(1, 2, figsize=(14, 6.4), sharey=True)
    for ax, (d, nome) in zip(eixos, DEFINICOES.items()):
        col = f"{d.lower()}_km2"
        for r in cen.itertuples():
            cor, cheio = COR[NOME_SENSOR[r.sensor]], r.fase != "descida"
            ax.scatter([r.nivel_regua_cm], [getattr(r, col)], marker=au.FORMA[r.sensor], s=44, facecolor=cor if cheio else "none", edgecolor="black" if r.fase == "pico" else cor,
                       linewidth=1.3, alpha=0.4 if r.fase in ("igual", "sem dado") else 1, zorder=3)
        for s, estilo in (("radar", "-"), ("óptico", "-"), ("os dois", "--")):
            k = curvas[(curvas.sensor == s) & (curvas.definicao == d)]
            ax.plot(k.nivel_regua_cm, k.area_ajustada_km2, color=COR[s], lw=1.6 if s != "os dois" else 1.2, ls=estilo, zorder=2, alpha=0.85)
        for r in sgb.itertuples():
            ax.scatter([r.cota_cm], [r.mancha_em_terra_km2], marker="D", s=58, facecolor="none" if r.extraida_de_figura else "black", edgecolor="black", linewidth=1.4, zorder=4)
            ax.annotate(f"SGB {int(r.cota_cm)}" + (" (extraída)" if r.extraida_de_figura else ""), (r.cota_cm, r.mancha_em_terra_km2), textcoords="offset points", xytext=(-8, 5), ha="right", fontsize=7.5, color="#0b0b0b")
        ax.set_title(f"{d} — {nome}", fontsize=10, loc="left")
        ax.set_xlabel("nível da régua no dia da cena (cm)", fontsize=9)
        _limpar(ax)
    eixos[0].set_ylabel("água em terra na área urbana (km²)", fontsize=9)
    marc = lambda **kw: Line2D([], [], ls="", markersize=7, **kw)  # noqa: E731
    fig.legend(handles=[marc(marker="o", color=COR["radar"], label="radar (Sentinel-1)"), marc(marker="s", color=COR["óptico"], label="óptico (Sentinel-2)"),
                        marc(marker="o", color="#52514e", label="subida (preenchido)"), marc(marker="o", mfc="none", color="#52514e", label="descida (vazado)"),
                        marc(marker="o", mfc="#898781", mec="black", mew=1.3, label="pico (borda preta)"), marc(marker="o", color="#52514e", alpha=0.4, label="nível igual ao de dois dias antes"),
                        Line2D([], [], color=COR["radar"], lw=1.6, label="curva crescente: radar"), Line2D([], [], color=COR["óptico"], lw=1.6, label="curva crescente: óptico"),
                        Line2D([], [], color=COR["os dois"], lw=1.2, ls="--", label="curva crescente: os dois"), marc(marker="D", color="black", label="mancha do SGB, na própria cota"),
                        marc(marker="D", mfc="none", color="black", label="mancha extraída de figura, não conferida")], loc="lower center", ncol=4, fontsize=8, frameon=False)
    fig.suptitle("Nível da régua × água em terra na área urbana, por cena boa", fontsize=11, x=0.01, ha="left")
    fig.tight_layout(rect=(0, 0.11, 1, 0.96))
    fig.savefig(arq, dpi=200)
    plt.close(fig)


def figura_mapa(arq: Path, vista, so_sgb, leito, dom, drenagem, urbano, ext, titulo: str, nota: str) -> None:
    fig, ax = plt.subplots(figsize=(10, 8.2))
    fundo = np.full(dom.shape + (3,), 0.975)
    fundo[~dom] = 0.90
    fundo[leito] = (0.80, 0.86, 0.93)
    for masc, cor in ((vista, COR_VISTA), (so_sgb, COR_SO_SGB)):
        fundo[masc] = matplotlib.colors.to_rgb(cor)
    ax.imshow(fundo, extent=ext, interpolation="nearest")
    if len(drenagem):
        drenagem.plot(ax=ax, color=COR_DRENAGEM, linewidth=0.9)
    gpd.GeoSeries([urbano], crs=c.CRS_PADRAO).boundary.plot(ax=ax, color="#52514e", linewidth=0.8, linestyle=(0, (4, 3)))
    ax.set_xlim(ext[0], ext[1]); ax.set_ylim(ext[2], ext[3])  # noqa: E702
    ax.set_xticks([]); ax.set_yticks([])  # noqa: E702
    ax.set_title(titulo, fontsize=9.5, loc="left")
    fig.legend(handles=[Patch(facecolor=COR_VISTA, label="mancha vista como água em alguma cena"), Patch(facecolor=COR_SO_SGB, label="só SGB: nunca vista como água"), Patch(facecolor=(0.80, 0.86, 0.93), label="leito de referência"),
                       Patch(facecolor=(0.90, 0.90, 0.90), label="fora do domínio (outro lado da fronteira)"), Line2D([], [], color=COR_DRENAGEM, lw=1.2, label="cursos d'água (ANA)"),
                       Line2D([], [], color="#52514e", lw=1, ls=(0, (4, 3)), label="área urbana (SGB)")], loc="lower center", bbox_to_anchor=(0.5, 0.035), ncol=3, fontsize=8, frameon=False)
    fig.text(0.02, 0.012, nota + f" {c.CRS_PADRAO}. Produto derivado, para conferência.", fontsize=6.5, color="#555555")
    fig.subplots_adjust(left=0.02, right=0.98, top=0.94, bottom=0.13)
    fig.savefig(arq, dpi=200)
    plt.close(fig)


def figura_uso_do_solo(arq: Path, d2: pd.DataFrame, paineis: list[tuple[int, str]], nota: str) -> None:
    fig, eixos = plt.subplots(1, len(paineis), figsize=(7 * len(paineis), 5.6), squeeze=False)
    for ax, (cota, criterio) in zip(eixos[0], paineis):
        k = d2[(d2.cota_cm == cota) & (d2.criterio == criterio)]
        t = k.pivot_table(index="classe", columns="parte", values="pct_da_parte", fill_value=0.0).reindex(columns=["parte vista", "só SGB"], fill_value=0.0)
        t = t.loc[t.max(axis=1).sort_values().index]
        t = t[t.max(axis=1) >= 0.5]
        y = np.arange(len(t))
        for desloc, parte, cor in ((0.2, "parte vista", COR_VISTA), (-0.2, "só SGB", COR_SO_SGB)):
            ax.barh(y + desloc, t[parte], height=0.36, color=cor, label=f"{parte} ({k[k.parte == parte].area_km2.sum():.2f} km²)".replace(".", ","))
            for yi, v in zip(y + desloc, t[parte]):
                ax.text(v + 0.8, yi, f"{v:.1f}".replace(".", ","), va="center", fontsize=7.5, color="#52514e")
        ax.set_yticks(y, t.index, fontsize=8.5)
        ax.set_xlim(0, max(10.0, float(t.to_numpy().max()) * 1.15))
        ax.set_xlabel("% da área de cada parte", fontsize=9)
        ax.set_title(f"Mancha do SGB até {cota} cm — cenas de {criterio}", fontsize=9.5, loc="left")
        ax.legend(fontsize=8, frameon=False, loc="lower right")
        _limpar(ax)
        ax.grid(axis="y", visible=False)
    fig.suptitle("Uso e cobertura do solo na parte da mancha vista como água e na parte só SGB", fontsize=11, x=0.01, ha="left")
    fig.text(0.01, 0.01, nota, fontsize=7, color="#555555")
    fig.tight_layout(rect=(0, 0.03, 1, 0.95))
    fig.savefig(arq, dpi=200)
    plt.close(fig)


# ---------------------------------------------------------------- principal
def main() -> None:
    global FONTES
    if PROC.exists() and any(PROC.iterdir()) and not ARGS.refazer:
        raise SystemExit(f"{rel(PROC)} já tem arquivos (use --refazer para gerar de novo)")
    m44 = json.loads((PROC44 / "controle-de-qualidade_sentinel_2017-2024_cena.json").read_text(encoding="utf-8"))
    arg44 = m44["argumentos"]
    au.ARGS = argparse.Namespace(**arg44)  # as funções do roteiro da área urbana leem os argumentos de lá (mediana do radar)
    meta_uso = json.loads(vi.CAMINHO_RASTER_USO_SOLO.with_suffix(".json").read_text(encoding="utf-8"))
    FONTES = [*m44["fontes"], f"{meta_uso['fonte']}, ano {meta_uso['ano']} — uso e cobertura do solo", "ANA — Base Hidrográfica Ottocodificada, cursos d'água"]
    PROC.mkdir(parents=True, exist_ok=True)
    if ARGS.figuras:
        ARGS.figuras.mkdir(parents=True, exist_ok=True)

    # ---- mesma grade e mesmo domínio do roteiro da área urbana (o retângulo gravado lá já está no CRS do projeto)
    urbano = gpd.read_file(PROC44 / "area-urbana_sgb_2023_retangulo.gpkg", layer="area_urbana").to_crs(c.CRS_PADRAO).geometry.iloc[0]
    x0, y0, x1, y1 = urbano.bounds
    gx0, gy1 = np.floor(x0 / RES) * RES, np.ceil(y1 / RES) * RES
    grade = {"transform": from_origin(gx0, gy1, RES, RES), "width": int(np.ceil((x1 - gx0) / RES)), "height": int(np.ceil((gy1 - y0) / RES))}
    forma, tr = (grade["height"], grade["width"]), grade["transform"]
    ext = (gx0, gx0 + grade["width"] * RES, gy1 - grade["height"] * RES, gy1)
    px_km2 = RES * RES / 1e6
    limite = c.carregar_area_estudo()
    dentro = rasterizar(urbano, forma, tr)
    dom = dentro & rasterizar(limite.to_crs(c.CRS_PADRAO).union_all(), forma, tr)
    arq_ref = PROC44 / "referencia-por-orbita_sentinel1-rtc_2017-2024_10m.gpkg"
    leito = rasterizar(gpd.read_file(arq_ref, layer="leito_de_referencia_comum").geometry.union_all(), forma, tr)
    terra_dom = dom & ~leito

    # ---- cenas boas e as camadas de água já gravadas
    cq = pd.read_csv(PROC44 / "controle-de-qualidade_sentinel_2017-2024_cena.csv")
    boas = cq[cq.boa].sort_values(["nivel_regua_cm", "data_hora_local"]).reset_index(drop=True)
    boas["chave"] = boas.sensor + "_" + pd.to_datetime(boas.data_hora_local).dt.strftime("%Y%m%dT%H%M")
    agua = {}
    for sensor in (RADAR, OPTICO):
        arq = PROC44 / f"agua-observada-area-urbana_{sensor}_2017-2024_10m.gpkg"
        for camada in ("agua_total", "inundacao_em_terra"):
            for r in gpd.read_file(arq, layer=camada).itertuples():
                agua[(sensor, r.identificador, camada)] = rasterizar(r.geometry, forma, tr)

    # ---- onde cada cena tem dado: relido das janelas brutas com as funções do roteiro da área urbana
    bruto = pd.read_csv(next(iter(sorted(ao.BRUTO.glob("cenas_planetary-computer_*_cena.csv")))), dtype=str)
    bruto = bruto[bruto.situacao.isin(["lida", "já existia"])]
    arqs = {(r.sensor, r.identificador): (r.arquivos, r.versao_do_processamento) for r in bruto.itertuples()}
    arq_fat = next(iter(sorted(ao.BRUTO.glob("passagens-em-fatias_planetary-computer_*_passagem.csv"))), None)
    if arq_fat is not None:
        fat = pd.read_csv(arq_fat, dtype=str)
        for r in fat[fat.situacao.isin(["lida", "já existia"])].itertuples():
            arqs.setdefault((RADAR, r.identificadores), (r.arquivos, ""))
    caminhos = lambda sensor, ident: {a.split("-")[1].split("_")[0]: ao.BRUTO / sensor / a for a in arqs[(sensor, ident)][0].split("; ")}  # noqa: E731
    refs = pd.read_csv(PROC44 / "referencia-por-orbita_sentinel1-rtc_2017-2024_orbita.csv")
    valido_ref = {int(r.orbita_relativa): au.vv_em_db(caminhos(RADAR, r.cena_de_referencia)["vv"], grade)[1] for r in refs.itertuples()}
    cenas = {}
    for r in boas.itertuples():
        if r.sensor == RADAR:
            valido = au.vv_em_db(caminhos(RADAR, r.identificador)["vv"], grade)[1] & valido_ref[int(r.orbita_relativa)]
        else:
            versao = arqs[(OPTICO, r.identificador)][1]
            valido = ao.agua_optico(caminhos(OPTICO, r.identificador), grade, float(versao if pd.notna(versao) and versao else 0) >= 4)[1]
        dominio = dom & valido
        a1 = agua.get((r.sensor, r.identificador, "inundacao_em_terra"), np.zeros(forma, dtype=bool)) & dominio  # cena sem inundação em terra não tem linha na camada
        a2 = agua[(r.sensor, r.identificador, "agua_total")] & ~leito & dominio
        cenas[r.chave] = {"a1": a1, "a2": a2, "a3": a2 & ~a1, "dominio": dominio}
        if abs(a1.sum() * px_km2 - r.inundacao_em_terra_km2) > 1e-6:  # conferência: tem de dar a área já gravada
            AVISOS.append(f"{r.chave}: A1 relida ({a1.sum() * px_km2:.4f} km²) difere da gravada ({r.inundacao_em_terra_km2:.4f} km²)")
        if (a1 & ~a2).any():
            AVISOS.append(f"{r.chave}: há água ligada ao rio fora de A2")

    # ---- manchas acumuladas (função do repositório) e a extraída de figura (só leitura)
    manchas = ec.carregar_cenarios(ee.ARQ_COTAS, ao.CAMADA_COTAS, ao.ATRIBUTO_COTAS)
    geo_m, extraidas = {int(k): g for k, g in zip(manchas.valor, manchas.geometry)}, {}
    for item in ARGS.mancha_extra or []:
        cota, resto = item.split("=", 1)
        caminho, _, camada = resto.rpartition(":") if resto.count(":") and not resto.endswith(".gpkg") else (resto, "", None)
        if not Path(caminho).exists():
            AVISOS.append(f"mancha de {cota} cm ausente: comparação feita sem ela")
            continue
        geo_m[int(cota)] = gpd.read_file(caminho, layer=camada).to_crs(c.CRS_PADRAO).geometry.buffer(0).union_all()
        extraidas[int(cota)] = f"{ao.fmt(int(cota))[:-3]}.{ao.fmt(int(cota))[-3:]} cm: extraída de figura, não conferida"
    cotas = sorted(geo_m)
    mancha = {k: rasterizar(geo_m[k], forma, tr) & terra_dom for k in cotas}
    sgb = pd.DataFrame([{"cota_cm": k, "mancha_em_terra_km2": float(mancha[k].sum() * px_km2), "extraida_de_figura": k in extraidas, "marca": extraidas.get(k, "")} for k in cotas])
    nota_extra = ("; ".join(extraidas.values()) + ".") if extraidas else ""
    d2_44 = pd.read_csv(PROC44 / "agua-x-manchas-area-urbana_sentinel-sgb_2017-2024_cena-cota.csv")
    comuns = {"sensor": "sensor e produto", "identificador": "cena", "data_hora_local": "data e hora local (UTC−3)", "nivel_regua_cm": "nível da régua no dia: média diária (consistida onde houver; senão a bruta)",
              "cota_cm": "cota da mancha acumulada do SGB", "marca": "ressalva sobre a mancha", "definicao": "A1 = água ligada ao rio; A2 = toda a água em terra", "mancha_em_terra_km2": "mancha em terra no domínio"}

    # ---- Parte A
    linhas = []
    for r in boas.itertuples():
        v = cenas[r.chave]
        for k in cotas:
            m = mancha[k] & v["dominio"]
            ca, cb = au.comparar(v["a1"], m, v["dominio"], px_km2), au.comparar(v["a2"], m, v["dominio"], px_km2)
            a3_dentro = float((v["a3"] & m).sum() * px_km2)
            linhas.append({"sensor": r.sensor, "identificador": r.identificador, "chave": r.chave, "data_hora_local": r.data_hora_local, "nivel_regua_cm": r.nivel_regua_cm, "cota_cm": k,
                           "a1_km2": ca["inundacao_em_terra_km2"], "a2_km2": cb["inundacao_em_terra_km2"], "a3_km2": float(v["a3"].sum() * px_km2), "mancha_em_terra_km2": ca["mancha_em_terra_km2"],
                           **{f"{d}_{nome}": x[orig] for d, x in (("a1", ca), ("a2", cb)) for nome, orig in (("intersecao_km2", "intersecao_km2"), ("uniao_km2", "uniao_km2"), ("intersecao_sobre_uniao", "intersecao_sobre_uniao"),
                                                                                                            ("fracao_da_agua_dentro_da_mancha", "fracao_da_agua_dentro_da_mancha"), ("fracao_da_mancha_coberta", "fracao_da_mancha_coberta_pela_agua"))},
                           "a3_dentro_da_mancha_km2": a3_dentro, "a3_dentro_da_mancha_pct": 100 * a3_dentro / v["a3"].sum() / px_km2 if v["a3"].any() else np.nan, "marca": extraidas.get(k, "")})
    ta = pd.DataFrame(linhas)
    conf = ta.merge(d2_44[["chave", "cota_cm", "mancha_em_terra_km2", "intersecao_sobre_uniao"]].rename(columns={"mancha_em_terra_km2": "mancha_em_terra_km2_r44", "intersecao_sobre_uniao": "intersecao_sobre_uniao_r44"}), on=["chave", "cota_cm"])
    dif = max(float((conf.mancha_em_terra_km2 - conf.mancha_em_terra_km2_r44).abs().max()), float((conf.a1_intersecao_sobre_uniao - conf.intersecao_sobre_uniao_r44).abs().max()))
    if len(conf) != len(ta) or dif > 1e-6:
        AVISOS.append(f"A1 × manchas não reproduz a tabela do roteiro da área urbana (maior diferença {dif:.6f}; linhas casadas {len(conf)} de {len(ta)})")
    frac = {"a1_intersecao_sobre_uniao": 3, "a1_fracao_da_agua_dentro_da_mancha": 3, "a1_fracao_da_mancha_coberta": 3, "a2_intersecao_sobre_uniao": 3, "a2_fracao_da_agua_dentro_da_mancha": 3, "a2_fracao_da_mancha_coberta": 3}
    col_a = {**comuns, "chave": "sensor e data-hora local", "a1_km2": "A1: água ligada ao rio, em terra (a inundacao_em_terra)", "a2_km2": "A2: toda a água em terra (agua_total menos o leito de referência)",
             "a3_km2": "A3: água solta (A2 menos A1)", "mancha_em_terra_km2": "mancha em terra no domínio, onde a cena tem dado",
             **{f"{d}_{k}": f"{d.upper()} × mancha: {t}" for d in ("a1", "a2") for k, t in (("intersecao_km2", "área comum"), ("uniao_km2", "área de uma ou de outra"), ("intersecao_sobre_uniao", "interseção / união"),
                                                                                           ("fracao_da_agua_dentro_da_mancha", "quanto da água cai dentro da mancha"), ("fracao_da_mancha_coberta", "quanto da mancha a água cobre"))},
             "a3_dentro_da_mancha_km2": "água solta dentro da mancha", "a3_dentro_da_mancha_pct": "% da água solta da cena que cai dentro da mancha"}
    gravar(ta, "agua-em-terra-tres-definicoes-x-manchas_sentinel-sgb_2017-2024_cena-cota", "A — Três definições de água em terra em cada cena boa × cada mancha acumulada", col_a, casas={"nivel_regua_cm": 1, **frac},
           nota=nota_extra, manchas=sgb.to_dict("records"), conferencia_com_a_tabela_de_origem={"linhas_casadas": int(len(conf)), "maior_diferenca": dif})
    res = []
    for k in cotas:
        t = ta[ta.cota_cm == k]
        p = t[(t.nivel_regua_cm - k).abs() <= ARGS.vizinhanca_cm]
        res.append({"cota_cm": k, "mancha_em_terra_km2": float(mancha[k].sum() * px_km2), "cenas": len(t), "soma_de_a3_km2": t.a3_km2.sum(), "soma_de_a3_dentro_da_mancha_km2": t.a3_dentro_da_mancha_km2.sum(),
                    "a3_dentro_da_mancha_pct": 100 * t.a3_dentro_da_mancha_km2.sum() / t.a3_km2.sum(), "cenas_perto_da_cota": len(p), "a1_perto_min_km2": p.a1_km2.min(), "a1_perto_max_km2": p.a1_km2.max(),
                    "a2_perto_min_km2": p.a2_km2.min(), "a2_perto_max_km2": p.a2_km2.max(), "a3_dentro_perto_media_km2": p.a3_dentro_da_mancha_km2.mean(),
                    "a1_fracao_dentro_perto_mediana": p.a1_fracao_da_agua_dentro_da_mancha.median(), "a2_fracao_dentro_perto_mediana": p.a2_fracao_da_agua_dentro_da_mancha.median(),
                    "a1_fracao_da_mancha_coberta_perto_max": p.a1_fracao_da_mancha_coberta.max(), "a2_fracao_da_mancha_coberta_perto_max": p.a2_fracao_da_mancha_coberta.max(), "marca": extraidas.get(k, "")})
    res = pd.DataFrame(res)
    perto = f"cenas com nível a até {ARGS.vizinhanca_cm:g} cm da cota"
    gravar(res, "agua-em-terra-tres-definicoes-x-manchas_sentinel-sgb_2017-2024_cota", "A (resumo) — Água em terra × manchas, por cota", {
        **comuns, "cenas": "cenas boas", "soma_de_a3_km2": "soma da água solta de todas as cenas boas (a mesma área conta uma vez por cena)", "soma_de_a3_dentro_da_mancha_km2": "idem, só a parte dentro da mancha",
        "a3_dentro_da_mancha_pct": "% da soma da água solta que cai dentro da mancha", "cenas_perto_da_cota": perto, "a1_perto_min_km2": f"menor A1 entre as {perto}", "a1_perto_max_km2": "maior A1 entre elas",
        "a2_perto_min_km2": "menor A2 entre elas", "a2_perto_max_km2": "maior A2 entre elas", "a3_dentro_perto_media_km2": "média da água solta dentro da mancha, entre elas",
        "a1_fracao_dentro_perto_mediana": "mediana da fração de A1 dentro da mancha, entre elas", "a2_fracao_dentro_perto_mediana": "mediana da fração de A2 dentro da mancha, entre elas",
        "a1_fracao_da_mancha_coberta_perto_max": "maior fração da mancha coberta por A1, entre elas", "a2_fracao_da_mancha_coberta_perto_max": "maior fração da mancha coberta por A2, entre elas"},
        casas={"a1_fracao_dentro_perto_mediana": 3, "a2_fracao_dentro_perto_mediana": 3, "a1_fracao_da_mancha_coberta_perto_max": 3, "a2_fracao_da_mancha_coberta_perto_max": 3}, nota=nota_extra)

    # ---- Parte B: fase da cheia pela série diária (2015 fica fora)
    diaria = ao.ler_serie_diaria()
    s = diaria.valor_do_dia_cm[diaria.index.year != ARGS.ano_fora_da_serie].asfreq("D")
    pico = s.notna() & (s == s.rolling(7, center=True, min_periods=1).max())
    cen = ta.drop_duplicates("chave")[["sensor", "identificador", "chave", "data_hora_local", "nivel_regua_cm", "a1_km2", "a2_km2", "a3_km2"]].reset_index(drop=True)
    cen = pd.concat([cen, pd.DataFrame([fase_da_cheia(pd.Timestamp(x).normalize(), s, pico) for x in cen.data_hora_local])], axis=1)
    col_b1 = {**comuns, "chave": col_a["chave"], "a1_km2": col_a["a1_km2"], "a2_km2": col_a["a2_km2"], "a3_km2": col_a["a3_km2"], "nivel_dois_dias_antes_cm": "nível da régua dois dias antes",
              "variacao_em_dois_dias_cm": "nível do dia menos o de dois dias antes", "tendencia_em_dois_dias": "subida, descida ou igual, pela variação em dois dias",
              "e_pico": "o nível do dia é o maior entre três dias antes e três depois", "fase": "pico, se for; senão a tendência em dois dias", "data_do_ultimo_pico": "último dia, até o da cena, que é pico pela mesma regra",
              "dias_desde_o_ultimo_pico": "dias entre esse pico e a cena", "nivel_do_ultimo_pico_cm": "nível da régua nesse pico", "maior_nivel_dos_60_dias_antes_cm": "maior nível da régua nos 60 dias até a cena",
              "dias_desde_o_maior_nivel_dos_60_dias": "dias entre esse maior nível e a cena"}
    gravar(cen, "fase-da-cheia-por-cena_sentinel-ana_2017-2024_cena", "B1 — Fase da cheia no dia de cada cena boa", col_b1, casas={"nivel_regua_cm": 1}, serie=diaria.attrs.get("arquivo"),
           nota="A regra do pico pega também oscilações pequenas na descida; as duas últimas colunas dão a distância ao maior nível dos 60 dias anteriores.")
    faixas = np.arange(ARGS.faixa_inicio, ARGS.faixa_fim, ARGS.faixa_cm)
    b2 = []
    for d in DEFINICOES:
        for lo in faixas:
            k = cen[(cen.nivel_regua_cm >= lo) & (cen.nivel_regua_cm < lo + ARGS.faixa_cm)]
            linha = {"definicao": d, "faixa_de_nivel_cm": f"{lo:g}–{lo + ARGS.faixa_cm:g}", "cenas": len(k)}
            for f in ("subida", "descida", "pico"):
                linha.update({f"cenas_na_{f}" if f != "pico" else "cenas_no_pico": int((k.fase == f).sum()), f"area_media_na_{f}_km2" if f != "pico" else "area_media_no_pico_km2": k.loc[k.fase == f, f"{d.lower()}_km2"].mean()})
            linha["outras_cenas"] = int((~k.fase.isin(["subida", "descida", "pico"])).sum())
            linha["tem_subida_e_descida"] = bool(linha["cenas_na_subida"] and linha["cenas_na_descida"])
            b2.append(linha)
    b2 = pd.DataFrame(b2)
    gravar(b2, "area-por-faixa-de-nivel-e-fase_sentinel-ana_2017-2024_faixa", f"B2 — Área média por faixa de nível de {ARGS.faixa_cm:g} cm, na subida e na descida", {
        "definicao": comuns["definicao"], "faixa_de_nivel_cm": "faixa do nível da régua (limite de baixo incluído, o de cima não)", "cenas": "cenas boas na faixa", "cenas_na_subida": "cenas na subida",
        "area_media_na_subida_km2": "área média na subida", "cenas_na_descida": "cenas na descida", "area_media_na_descida_km2": "área média na descida", "cenas_no_pico": "cenas no pico",
        "area_media_no_pico_km2": "área média no pico", "outras_cenas": "cenas com nível igual ao de dois dias antes ou sem dado", "tem_subida_e_descida": "há cenas das duas fases na faixa"})

    # ---- Parte C: curva crescente e nível equivalente de cada mancha
    c1, c3 = [], []
    for nome, sens in SENSORES.items():
        k = cen[cen.sensor.isin(sens)]
        for d in DEFINICOES:
            col = f"{d.lower()}_km2"
            xs, ys, n = isotonica(k.nivel_regua_cm.to_numpy(), k[col].to_numpy())
            media = k.groupby("nivel_regua_cm")[col].mean().sort_index().to_numpy()
            c1 += [{"sensor": nome, "definicao": d, "nivel_regua_cm": x, "cenas_no_nivel": int(q), "area_observada_media_km2": float(o), "area_ajustada_km2": float(y)} for x, y, q, o in zip(xs, ys, n, media)]
            maior = k.loc[k[col].idxmax()]
            for r in sgb.itertuples():
                nivel, situacao = nivel_em_que_atinge(xs, ys, r.mancha_em_terra_km2)
                f = k[(k[col] >= (1 - ARGS.faixa_da_area) * r.mancha_em_terra_km2) & (k[col] <= (1 + ARGS.faixa_da_area) * r.mancha_em_terra_km2)]
                c3.append({"cota_cm": r.cota_cm, "mancha_em_terra_km2": r.mancha_em_terra_km2, "sensor": nome, "definicao": d, "cenas_usadas": len(k), "nivel_equivalente_cm": nivel, "situacao": situacao,
                           "cenas_na_faixa_da_area": len(f), "menor_nivel_na_faixa_cm": f.nivel_regua_cm.min(), "maior_nivel_na_faixa_cm": f.nivel_regua_cm.max(), "maior_valor_da_curva_km2": float(ys[-1]),
                           "area_da_maior_cena_km2": float(maior[col]), "nivel_da_maior_cena_cm": float(maior.nivel_regua_cm), "data_da_maior_cena": maior.data_hora_local[:10], "marca": r.marca})
    c1, c3 = pd.DataFrame(c1), pd.DataFrame(c3)
    gravar(c1, "curva-nivel-x-area_sentinel-ana_2017-2024_ponto", "C1 — Pontos da curva crescente da área em função do nível", {
        "sensor": "cenas usadas na curva: radar, óptico ou os dois", "definicao": comuns["definicao"], "nivel_regua_cm": "nível da régua", "cenas_no_nivel": "cenas com esse nível",
        "area_observada_media_km2": "média da área das cenas desse nível", "area_ajustada_km2": "valor da curva crescente"}, casas={"nivel_regua_cm": 1},
        metodo="regressão isotônica pela média de vizinhos violados, feita com numpy (a biblioteca scikit-learn não está no ambiente)")
    gravar(c3, "nivel-equivalente-das-manchas_sentinel-sgb_2017-2024_cota", "C3 — Nível em que a curva atinge a área de cada mancha do SGB", {
        **comuns, "sensor": "cenas usadas na curva", "cenas_usadas": "cenas na curva", "nivel_equivalente_cm": "menor nível em que a curva chega à área da mancha (reta entre os pontos)", "situacao": "onde a área da mancha cai na curva",
        "cenas_na_faixa_da_area": f"cenas com área a ±{100 * ARGS.faixa_da_area:g} % da área da mancha", "menor_nivel_na_faixa_cm": "menor nível entre essas cenas", "maior_nivel_na_faixa_cm": "maior nível entre essas cenas",
        "maior_valor_da_curva_km2": "valor da curva no maior nível", "area_da_maior_cena_km2": "maior área entre as cenas", "nivel_da_maior_cena_cm": "nível da régua na cena de maior área", "data_da_maior_cena": "data da cena de maior área"},
        casas={"nivel_equivalente_cm": 0, "menor_nivel_na_faixa_cm": 1, "maior_nivel_na_faixa_cm": 1, "nivel_da_maior_cena_cm": 1}, nota=nota_extra)

    # ---- Parte D: a parte da mancha nunca vista como água
    with rasterio.open(vi.CAMINHO_RASTER_USO_SOLO) as src:  # 30 m -> grade de 10 m, vizinho mais próximo; o raster já está no CRS do projeto, e a reprojeção é explícita mesmo assim
        uso = np.zeros(forma, dtype="uint8")
        reproject(src.read(1), uso, src_transform=src.transform, src_crs=src.crs, dst_transform=tr, dst_crs=c.CRS_PADRAO, resampling=Resampling.nearest)
    nomes = {int(x["class_id"]): x["nome_pt"] for x in meta_uso["classes_presentes_area_estudo"]}
    folga = int(np.ceil(ARGS.folga_drenagem_m / RES))  # a grade é alargada para que um curso logo fora do retângulo conte na distância
    tr_larga, forma_larga = from_origin(gx0 - folga * RES, gy1 + folga * RES, RES, RES), (forma[0] + 2 * folga, forma[1] + 2 * folga)
    rede = gpd.read_file(c.ARQ_BHO, layer="curso_dagua").to_crs(c.CRS_PADRAO)
    rede = rede[rede.intersects(box(*urbano.bounds).buffer(ARGS.folga_drenagem_m))]
    if len(rede):
        eixo = features.rasterize([(g, 1) for g in rede.geometry], out_shape=forma_larga, transform=tr_larga, fill=0, all_touched=True, dtype="uint8").astype(bool)
        dist = (ndimage.distance_transform_edt(~eixo) * RES)[folga:-folga, folga:-folga]
    else:
        AVISOS.append("nenhum curso d'água da rede perto da área urbana: distâncias sem valor")
        dist = np.full(forma, np.nan)
    faixas_dist = [(f"até {ARGS.distancias_m[0]:g} m", dist <= ARGS.distancias_m[0]), (f"{ARGS.distancias_m[0]:g}–{ARGS.distancias_m[1]:g} m", (dist > ARGS.distancias_m[0]) & (dist <= ARGS.distancias_m[1])),
                   (f"mais de {ARGS.distancias_m[1]:g} m", dist > ARGS.distancias_m[1])]
    d1, d2, d3, geo, partes = [], [], [], {"so_sgb": [], "parte_vista": []}, {}
    for k in cotas:
        tem = bool((boas.nivel_regua_cm >= k).any())
        criterios = [(f"nível ≥ {k} cm", k, "regra principal")] if tem else []
        criterios.append((f"nível ≥ {k - ARGS.recuo_cm:g} cm", k - ARGS.recuo_cm, "variante: cota menos o recuo" if tem else "regra principal: sem cena boa no nível da cota"))
        criterios += [(f"nível ≥ {m:g} cm", m, "nível mínimo pedido para o mapa") for q, m in ARGS.mapas if q == k and f"nível ≥ {m:g} cm" not in [x[0] for x in criterios]]
        for criterio, minimo, papel in criterios:
            usadas = boas[boas.nivel_regua_cm >= minimo]
            visto, com_dado = np.zeros(forma, dtype=bool), np.zeros(forma, dtype=bool)
            for ch in usadas.chave:
                visto |= cenas[ch]["a2"]
                com_dado |= cenas[ch]["dominio"]
            vista, so = mancha[k] & visto, mancha[k] & ~visto
            partes[(k, criterio)] = (vista, so)
            base = {"cota_cm": k, "criterio": criterio, "papel_do_criterio": papel}
            d1.append({**base, "cenas_usadas": len(usadas), "cenas_de_radar": int((usadas.sensor == RADAR).sum()), "cenas_opticas": int((usadas.sensor == OPTICO).sum()), "mancha_em_terra_km2": float(mancha[k].sum() * px_km2),
                       "parte_vista_km2": float(vista.sum() * px_km2), "so_sgb_km2": float(so.sum() * px_km2), "so_sgb_fracao_da_mancha": float(so.sum() / mancha[k].sum()),
                       "so_sgb_sem_dado_em_nenhuma_cena_km2": float((so & ~com_dado).sum() * px_km2), "marca": extraidas.get(k, "")})
            for parte, masc in (("parte vista", vista), ("só SGB", so)):
                cont = np.bincount(uso[masc], minlength=256)
                d2 += [{**base, "parte": parte, "codigo_da_classe": int(cod), "classe": nomes.get(int(cod), "sem classe (fora do recorte do raster)" if cod == 0 else f"classe {cod}"), "area_km2": float(q * px_km2),
                        "pct_da_parte": float(100 * q / masc.sum()), "marca": extraidas.get(k, "")} for cod, q in sorted(enumerate(cont), key=lambda x: -x[1]) if q]
                d3 += [{**base, "parte": parte, "distancia_ao_curso_dagua": rot, "area_km2": float((masc & f).sum() * px_km2), "pct_da_parte": float(100 * (masc & f).sum() / masc.sum()) if masc.any() else np.nan,
                        "marca": extraidas.get(k, "")} for rot, f in faixas_dist]
                geo["parte_vista" if parte == "parte vista" else "so_sgb"].append({**base, "cenas_usadas": len(usadas), "area_km2": float(masc.sum() * px_km2), "marca": extraidas.get(k, ""), "geometry": ao.poligonos(masc, grade)})
    d1, d2, d3 = pd.DataFrame(d1), pd.DataFrame(d2), pd.DataFrame(d3)
    col_d = {**comuns, "criterio": "cenas boas que entram: as de nível igual ou maior que este", "papel_do_criterio": "regra principal ou variante", "parte": "parte vista (marcada como água, A2, em alguma cena) ou só SGB (em nenhuma)",
             "area_km2": "área", "pct_da_parte": "% da área da parte"}
    gravar(d1, "mancha-nunca-vista-como-agua_sentinel-sgb_2017-2024_cota", "D1 — Parte de cada mancha do SGB nunca marcada como água (A2) nas cenas boas do critério", {
        **col_d, "cenas_usadas": "cenas boas do critério", "cenas_de_radar": "das quais, de radar", "cenas_opticas": "das quais, ópticas", "parte_vista_km2": "mancha marcada como água em alguma cena",
        "so_sgb_km2": "mancha não marcada como água em nenhuma cena", "so_sgb_fracao_da_mancha": "só SGB / mancha", "so_sgb_sem_dado_em_nenhuma_cena_km2": "parte de só SGB em que nenhuma cena do critério tem dado"},
        casas={"so_sgb_fracao_da_mancha": 3}, nota=nota_extra)
    gravar(d2, "uso-do-solo-na-mancha-vista-e-nao-vista_mapbiomas-sgb_2024_classe", "D2 — Uso e cobertura do solo na parte vista e em só SGB", {
        **col_d, "codigo_da_classe": "código da classe no raster", "classe": "nome da classe, como nos metadados do raster"}, nota=nota_extra, raster=rel(vi.CAMINHO_RASTER_USO_SOLO), colecao=meta_uso.get("colecao"), ano=meta_uso.get("ano"))
    gravar(d3, "distancia-aos-cursos-dagua-na-mancha-vista-e-nao-vista_ana-sgb_atual_faixa", "D3 — Distância aos cursos d'água na parte vista e em só SGB", {
        **col_d, "distancia_ao_curso_dagua": "faixa de distância ao eixo do curso d'água mais próximo"}, nota=nota_extra, rede=rel(c.ARQ_BHO), camada="curso_dagua", cursos_perto_da_area=int(len(rede)))
    arq_g = PROC / "mancha-vista-e-nao-vista-como-agua_sentinel-sgb_2017-2024_10m.gpkg"
    for camada, lin in geo.items():
        g = gpd.GeoDataFrame(lin, crs=c.CRS_PADRAO)
        g[g.geometry.notna()].to_file(arq_g, driver="GPKG", layer=camada)
    meta(arq_g, descricao="cada mancha do SGB (em terra, na área urbana, lado brasileiro) dividida em parte vista como água em alguma cena boa do critério e parte nunca vista (só SGB); polígonos de pixel, sem simplificação",
         camadas=list(geo), colunas={"cota_cm": comuns["cota_cm"], "criterio": col_d["criterio"], "papel_do_criterio": col_d["papel_do_criterio"], "cenas_usadas": "cenas boas do critério", "area_km2": "área", "marca": comuns["marca"]},
         nota=nota_extra or None, aviso="produto derivado, para conferência no mapa; não vai ao geoportal")

    # ---- Parte E: as três cenas de maior interseção/união, com A2 e com A1
    e1, e2 = [], []
    destaque = {x: i for i, x in enumerate(ARGS.cenas_destaque)}
    ta["data"] = ta.data_hora_local.str[:10]
    for k in cotas:
        for conj, t in (("todas as cenas boas", ta[ta.cota_cm == k]), (f"cenas até {ARGS.data_limite}", ta[(ta.cota_cm == k) & (ta.data <= ARGS.data_limite)])):
            o2, o1 = t.sort_values("a2_intersecao_sobre_uniao", ascending=False).reset_index(drop=True), t.sort_values("a1_intersecao_sobre_uniao", ascending=False).reset_index(drop=True)
            for i in range(min(3, len(t))):
                e1.append({"cota_cm": k, "conjunto": conj, "posicao": i + 1, **{f"{d}_{n}": v for d, o in (("a2", o2), ("a1", o1)) for n, v in (
                    ("data", o.data[i]), ("sensor", NOME_SENSOR[o.sensor[i]]), ("nivel_regua_cm", o.nivel_regua_cm[i]), ("intersecao_sobre_uniao", o[f"{d}_intersecao_sobre_uniao"][i]), ("em_destaque", o.data[i] in destaque))},
                    "marca": extraidas.get(k, "")})
            for dia in destaque:
                for d, o in (("A2", o2), ("A1", o1)):
                    p = o.index[o.data == dia]
                    if len(p):
                        e2.append({"data": dia, "sensor": NOME_SENSOR[o.sensor[p[0]]], "nivel_regua_cm": o.nivel_regua_cm[p[0]], "cota_cm": k, "conjunto": conj, "definicao": d,
                                   "intersecao_sobre_uniao": o[f"{d.lower()}_intersecao_sobre_uniao"][p[0]], "posicao": int(p[0]) + 1, "cenas_no_conjunto": len(o), "marca": extraidas.get(k, "")})
    e1, e2 = pd.DataFrame(e1), pd.DataFrame(e2)
    col_e = {**comuns, "conjunto": "cenas que concorrem", "posicao": "ordem pela interseção/união", "data": "data local da cena", "intersecao_sobre_uniao": "interseção / união com a mancha", "cenas_no_conjunto": "cenas que concorrem",
             **{f"{d}_{n}": f"{d.upper()}: {t}" for d in ("a1", "a2") for n, t in (("data", "data local da cena"), ("sensor", "sensor"), ("nivel_regua_cm", "nível da régua no dia"), ("intersecao_sobre_uniao", "interseção / união com a mancha"),
                                                                                   ("em_destaque", "cena em destaque (" + ", ".join(destaque) + ")"))}}
    gravar(e1, "tres-cenas-de-maior-sobreposicao-por-cota_sentinel-sgb_2017-2024_cota-definicao", "E1 — As três cenas de maior interseção/união para cada cota, com A2 e, ao lado, com A1", col_e,
           casas={"a1_intersecao_sobre_uniao": 3, "a2_intersecao_sobre_uniao": 3, "a1_nivel_regua_cm": 1, "a2_nivel_regua_cm": 1}, nota=nota_extra, origem_de_a1=rel(PROC44))
    if len(e2):
        gravar(e2, "cenas-em-destaque-por-cota_sentinel-sgb_2017-2024_cena-cota", "E2 — Posição das cenas em destaque em cada cota", col_e, casas={"intersecao_sobre_uniao": 3, "nivel_regua_cm": 1}, nota=nota_extra)

    # ---- figuras (fora do repositório)
    if ARGS.figuras:
        figs = {}
        arq = ARGS.figuras / "nivel-x-area-a1-e-a2_sentinel-sgb_2017-2024.png"
        figura_nivel_area(arq, cen, c1, sgb)
        figs[arq.name] = "F1: nível × área com A1 e A2, fase da cheia, curvas crescentes e manchas do SGB na própria cota"
        paineis = []
        for k, minimo in ARGS.mapas:
            criterio = f"nível ≥ {minimo:g} cm"
            if (k, criterio) not in partes:
                AVISOS.append(f"mapa da mancha de {k} cm com cenas de {criterio}: combinação não calculada")
                continue
            vista, so = partes[(k, criterio)]
            n = int(d1[(d1.cota_cm == k) & (d1.criterio == criterio)].cenas_usadas.iloc[0])
            arq = ARGS.figuras / f"mancha-{k}cm-vista-e-so-sgb_cenas-a-partir-de-{minimo:g}cm.png"
            figura_mapa(arq, vista, so, leito, dom, rede, urbano, ext, f"Mancha do SGB até {k} cm: parte vista como água em alguma cena boa de {criterio} ({n} cenas)\ne parte só SGB",
                        f"Parte vista: {ao.fmt(float(vista.sum() * px_km2))} km². Só SGB: {ao.fmt(float(so.sum() * px_km2))} km². Água = toda a água em terra (A2).")
            figs[arq.name] = f"F2: mapa da mancha de {k} cm, cenas de {criterio}"
            paineis.append((k, criterio))
        if paineis:
            arq = ARGS.figuras / "uso-do-solo-na-parte-vista-e-so-sgb_mapbiomas-sgb_2024.png"
            figura_uso_do_solo(arq, d2, paineis, f"{meta_uso['fonte']}, ano {meta_uso['ano']}, 30 m. Classes com menos de 0,5 % nas duas partes não aparecem. Produto derivado, para conferência.")
            figs[arq.name] = "F3: uso do solo em só SGB × parte vista"
        (ARGS.figuras / "figuras_curva-nivel-x-area.json").write_text(json.dumps({"script": SCRIPT, "motivo": MOTIVO, "fontes": FONTES, "figuras": figs, "limitacoes": LIMITACOES, "manchas": sgb.to_dict("records"), "avisos": AVISOS},
                                                                                 ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    if ARGS.copia:
        ARGS.copia.mkdir(parents=True, exist_ok=True)
        for arq in sorted(PROC.iterdir()):
            shutil.copy2(arq, ARGS.copia / arq.name)
    print(json.dumps({"cenas_boas": boas.groupby("sensor").size().to_dict(), "manchas": sgb.to_dict("records"), "conferencia_com_a_origem": {"linhas_casadas": int(len(conf)), "maior_diferenca": dif}, "avisos": AVISOS},
                     ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", force=True)
    _p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    _p.add_argument("--mancha-extra", nargs="*", help="COTA=ARQUIVO.gpkg:CAMADA de mancha acumulada de fora do repositório (extraída de figura, não conferida): só leitura")
    _p.add_argument("--vizinhanca-cm", type=float, default=50.0, help="no resumo da parte A, cenas 'perto da cota' são as de nível a até isto da cota (cm)")
    _p.add_argument("--ano-fora-da-serie", type=int, default=2015, help="ano da série diária que não entra na fase da cheia")
    _p.add_argument("--faixa-inicio", type=float, default=800.0, help="início das faixas de nível (cm)")
    _p.add_argument("--faixa-fim", type=float, default=1300.0, help="fim das faixas de nível (cm)")
    _p.add_argument("--faixa-cm", type=float, default=50.0, help="largura das faixas de nível (cm)")
    _p.add_argument("--faixa-da-area", type=float, default=0.10, help="cenas 'com a área da mancha' são as de área a mais ou menos esta fração dela")
    _p.add_argument("--recuo-cm", type=float, default=50.0, help="sem cena boa de nível igual ou maior que a cota, entram as de nível até isto abaixo dela (cm)")
    _p.add_argument("--distancias-m", type=float, nargs=2, default=[100.0, 300.0], help="limites das faixas de distância aos cursos d'água (m)")
    _p.add_argument("--folga-drenagem-m", type=float, default=1000.0, help="largura, em volta da área urbana, em que os cursos d'água ainda contam para a distância (m)")
    _p.add_argument("--data-limite", default="2023-09-20", help="última data (AAAA-MM-DD) do conjunto restrito de cenas da parte E")
    _p.add_argument("--cenas-destaque", nargs="*", default=["2023-09-15", "2017-06-14"], help="datas (AAAA-MM-DD) das cenas destacadas na parte E")
    _p.add_argument("--mapas", type=lambda x: tuple(int(v) for v in x.split(":")), nargs="*", default=[(1252, 1200), (833, 833)], help="COTA:NÍVEL_MÍNIMO de cada mapa de parte vista × só SGB")
    _p.add_argument("--figuras", type=Path, help="pasta (fora do repositório) das figuras; sem ela, não há figura")
    _p.add_argument("--copia", type=Path, help="pasta que recebe uma cópia dos produtos")
    _p.add_argument("--refazer", action="store_true", help="gera de novo mesmo se a pasta de saída já tiver arquivos")
    ARGS = _p.parse_args()
    FONTES, AVISOS = [], []
    for _pasta in (ARGS.figuras, ARGS.copia):
        if _pasta is not None and c.RAIZ in _pasta.resolve().parents:
            raise SystemExit("--figuras e --copia têm de ficar fora do repositório")
    main()
