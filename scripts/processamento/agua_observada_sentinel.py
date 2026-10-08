"""
Água observada em cenas Sentinel nas datas de cheia e comparação com as manchas
de inundação por cota do SGB.

Entradas (nada é baixado aqui):
  - cenas lidas por scripts/download/sentinel_planetary_computer.py
    (data/raw/sentinel/, com a tabela de cenas e o retângulo da área de estudo);
  - manchas por cota do SGB, acumuladas pela função do repositório
    (exposicao_inundacao_cenarios.carregar_cenarios);
  - limite municipal (config/area_estudo.geojson) — "lado brasileiro";
  - série diária e telemetria do nível do rio (scripts/download/nivel_rio_ana.py).

Método:
  B1  radar (Sentinel-1, VV): potência -> dB; mediana 5×5; limiar de Otsu na área
      de estudo de cada cena; fora de --otsu-min a --otsu-max dB, vale --limiar-fixo
      e a cena é marcada. Água = VV abaixo do limiar.
  B2  óptico (Sentinel-2): MNDWI = (B03 − B11) / (B03 + B11), B11 reamostrada a
      10 m; água = MNDWI > 0; nuvem, sombra e neve (SCL) ficam "sem dado".
  B3  grupos de água menores que --area-minima-ha são retirados.
  B4  leito de referência: a água da cena de radar do período de rio baixo
      (--janela-leito) com o menor nível da régua.
  B5  três camadas por cena: agua_total; agua_ligada_ao_rio (grupos, com 8
      vizinhos, que tocam o leito); inundacao_em_terra (a anterior menos o leito).
  C   cada cena × cada mancha acumulada: interseção, união e frações, só do lado
      brasileiro e dentro da área de estudo.

Limitações (não corrigidas): o radar não vê água sob vegetação nem entre prédios
(o reflexo das paredes clareia a área alagada) e vê como água superfícies lisas
(pistas, telhados planos); o leito de referência é de um só dia; o leito inclui
toda a água daquele dia (rio, lagoas, açudes), e a água ligada a qualquer uma
delas conta como "ligada ao rio".

Tudo sai no CRS do projeto, numa grade de 10 m alinhada ao retângulo da área de
estudo. Produtos em data/processed/agua_observada_sentinel/ (fora do git);
figuras só em --figuras (fora do repositório). Produtos DERIVADOS, para
conferência no mapa; não vão ao geoportal.

Uso:
  python scripts/processamento/agua_observada_sentinel.py [--figuras PASTA] [--copia PASTA]
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
from datetime import datetime
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
from shapely.geometry import MultiPolygon, box, shape  # noqa: E402

import dinamica_populacional_comum as c  # noqa: E402
import exposicao_inundacao_cenarios as ec  # noqa: E402
import exposicao_inundacao_enderecos as ee  # noqa: E402

logger = logging.getLogger(__name__)

SCRIPT = "scripts/processamento/agua_observada_sentinel.py"
MOTIVO = "mapear a água de cheias observadas em imagens Sentinel e comparar com as manchas por cota do SGB"
BRUTO = c.RAW / "sentinel"
PROC = c.RAIZ / "data" / "processed" / "agua_observada_sentinel"
PADRAO_SERIE, PADRAO_TELEMETRIA = "nivel-rio_ana-serie-historica-*_diario.csv", "nivel-rio_ana-telemetria-*_15min.csv"
CAMADA_COTAS, ATRIBUTO_COTAS = "cotas_inundacao", "cota_cm"
RES = 10.0  # grade de trabalho, em metros (a resolução do radar e das bandas B03 e B08)
SCL_SEM_DADO = (0, 1, 3, 8, 9, 10, 11)  # sem dado, saturado, sombra de nuvem, nuvem média e alta, cirrus, neve
CAMADAS = ("agua_total", "agua_ligada_ao_rio", "inundacao_em_terra")
VIZINHOS_8 = np.ones((3, 3), dtype=bool)
COBERTURA_MINIMA = 99.0
LIMITACOES = ["o radar não vê água sob vegetação nem entre prédios (o reflexo das paredes clareia a área alagada)",
              "o radar vê como água superfícies lisas (pistas, telhados planos)", "o leito de referência é de um só dia, do período de rio baixo",
              "o leito de referência inclui toda a água daquele dia (rio, lagoas, açudes): a água ligada a qualquer uma delas conta como ligada ao rio",
              "no óptico, nuvem e sombra ficam sem dado e podem cortar a ligação da água com o leito"]
# figuras: cor por ano (ordem fixa) e forma por sensor
COR_ANO = {2015: "#7b3294", 2016: "#7b3294", 2017: "#d95f02", 2019: "#1b9e77", 2020: "#666666", 2023: "#2a78d6", 2024: "#c51b7d"}
FORMA = {"sentinel1-rtc": "o", "sentinel2-l2a": "s"}
COR_AGUA, COR_LEITO, COR_MANCHA = (0.10, 0.45, 0.95, 0.45), "#ffd400", {952: "#ff7f00", 1252: "#e7298a"}


# ---------------------------------------------------------------- gravação
def rel(p: Path) -> str:
    return str(Path(p).relative_to(c.RAIZ))


def meta(caminho: Path, **kw) -> None:
    c.gravar_meta(caminho, crs=c.CRS_PADRAO, status=c.STATUS_CONFERENCIA, ligado_ao_portal=False, script=SCRIPT, motivo=MOTIVO, fontes=FONTES,
                  licencas=LICENCAS, fora_do_git="data/processed/ é ignorado; produto derivado, para conferência", limitacoes=LIMITACOES,
                  argumentos={k: (str(v) if isinstance(v, Path) else v) for k, v in vars(ARGS).items() if k not in ("figuras", "copia")}, **kw)


def fmt(v, casas=2) -> str:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "—"
    if isinstance(v, (bool, np.bool_)):
        return "sim" if v else "não"
    if isinstance(v, (int, np.integer)):
        return str(int(v))
    if isinstance(v, (float, np.floating)):
        return f"{v:.{0 if float(v).is_integer() and casas != 3 else casas}f}".replace(".", ",")
    return str(v).replace("|", "/")


def gravar(t: pd.DataFrame, nome: str, titulo: str, colunas: dict, nota: str = "", casas: dict | None = None, **kw) -> Path:
    """Tabela: .csv (sem arredondar) + .md (leitura) + .json com a descrição de cada coluna."""
    faltam = [x for x in t.columns if x not in colunas]
    if faltam:
        raise KeyError(f"{nome}: coluna sem descrição: {faltam}")
    casas = casas or {}
    arq = PROC / f"{nome}.csv"
    t.to_csv(arq, index=False)
    lin = [f"**{titulo}**", "", "| " + " | ".join(t.columns) + " |", "|" + "|".join("---:" if pd.api.types.is_numeric_dtype(t[x]) and not pd.api.types.is_bool_dtype(t[x]) else "---" for x in t.columns) + "|"]
    lin += ["| " + " | ".join(fmt(v, casas.get(col, 2)) for col, v in zip(t.columns, r)) + " |" for r in t.itertuples(index=False)]
    arq.with_suffix(".md").write_text("\n".join(lin + (["", nota] if nota else []) + [""]), encoding="utf-8")
    meta(arq, titulo=titulo, nota=nota or None, colunas={k: colunas[k] for k in t.columns}, **kw)
    logger.info("Tabela: %s (%d linhas)", rel(arq), len(t))
    return arq


# ---------------------------------------------------------------- régua
def ler_serie_diaria() -> pd.DataFrame | None:
    """Uma linha por dia: média diária bruta e consistida, leituras de 7h e 17h; valor do dia = consistida onde houver."""
    arq = next(iter(sorted(c.RAW.glob(PADRAO_SERIE))), None)
    if arq is None:
        return None
    b = pd.read_csv(arq, dtype=str)
    b["v"], b["data"] = pd.to_numeric(b.cota_cm, errors="coerce"), pd.to_datetime(b["data"])

    def sel(md, nc, hora=None):
        s = b[(b.media_diaria == md) & (b.nivel_consistencia == nc)]
        s = s[s.hora_do_registro == hora] if hora else s
        return s.drop_duplicates("data").set_index("data").v

    d = pd.DataFrame({"media_bruta_cm": sel("1", "1"), "media_consistida_cm": sel("1", "2"), "leitura_07h_cm": sel("0", "1", "07:00"), "leitura_17h_cm": sel("0", "1", "17:00")})
    d["valor_do_dia_cm"] = d.media_consistida_cm.where(d.media_consistida_cm.notna(), d.media_bruta_cm)
    d["nivel_consistencia"] = np.where(d.media_consistida_cm.notna(), "consistido", np.where(d.media_bruta_cm.notna(), "bruto", ""))
    d.attrs["arquivo"] = rel(arq)
    return d


def ler_telemetria() -> pd.DataFrame | None:
    arqs = sorted(c.RAW.glob(PADRAO_TELEMETRIA))
    if not arqs:
        return None
    t = pd.concat([pd.read_csv(a) for a in arqs])
    t["dh"] = pd.to_datetime(t.data_hora)
    t = t.dropna(subset=["nivel_cm"]).sort_values("dh").reset_index(drop=True)
    t.attrs["arquivos"] = [rel(a) for a in arqs]
    return t


def nivel_na_cena(quando_local: pd.Timestamp, diaria: pd.DataFrame | None, tel: pd.DataFrame | None) -> dict:
    out = {"nivel_regua_cm": np.nan, "nivel_consistencia": "", "media_bruta_cm": np.nan, "media_consistida_cm": np.nan, "leitura_07h_cm": np.nan, "leitura_17h_cm": np.nan,
           "nivel_interpolado_na_hora_cm": np.nan, "telemetria_mais_proxima_cm": np.nan, "telemetria_hora_da_leitura": ""}
    dia = quando_local.normalize()
    if diaria is not None and dia in diaria.index:
        r = diaria.loc[dia]
        out.update(nivel_regua_cm=r.valor_do_dia_cm, nivel_consistencia=r.nivel_consistencia, media_bruta_cm=r.media_bruta_cm, media_consistida_cm=r.media_consistida_cm,
                   leitura_07h_cm=r.leitura_07h_cm, leitura_17h_cm=r.leitura_17h_cm)
        h = quando_local.hour + quando_local.minute / 60 + quando_local.second / 3600
        if 7 <= h <= 17 and pd.notna(r.leitura_07h_cm) and pd.notna(r.leitura_17h_cm):  # estimativa: reta entre as duas leituras do dia
            out["nivel_interpolado_na_hora_cm"] = float(r.leitura_07h_cm + (r.leitura_17h_cm - r.leitura_07h_cm) * (h - 7) / 10)
    if tel is not None and tel.dh.min() <= quando_local <= tel.dh.max():
        i = (tel.dh - quando_local).abs().idxmin()
        if abs(tel.dh[i] - quando_local) <= pd.Timedelta(hours=1):
            out.update(telemetria_mais_proxima_cm=float(tel.nivel_cm[i]), telemetria_hora_da_leitura=f"{tel.dh[i]:%Y-%m-%d %H:%M}")
    return out


# ---------------------------------------------------------------- raster
def para_a_grade(arq: Path, grade: dict, metodo: Resampling, sem_dado_saida) -> np.ndarray:
    """Recorte bruto -> grade de trabalho (CRS do projeto, 10 m). Reprojeção explícita: a cena vem no CRS UTM do sensor."""
    with rasterio.open(arq) as src:
        dados = src.read(1)
        dest = np.full((grade["height"], grade["width"]), sem_dado_saida, dtype="float32")
        reproject(dados.astype("float32"), dest, src_transform=src.transform, src_crs=src.crs, src_nodata=src.nodata,
                  dst_transform=grade["transform"], dst_crs=c.CRS_PADRAO, dst_nodata=sem_dado_saida, resampling=metodo)
    return dest


def otsu(valores: np.ndarray, caixas: int = 512) -> float:
    """Limiar de Otsu (máxima variância entre classes), só com numpy."""
    h, bordas = np.histogram(valores, bins=caixas)
    centros = (bordas[:-1] + bordas[1:]) / 2
    p = h / h.sum()
    w0 = np.cumsum(p)
    m = np.cumsum(p * centros)
    with np.errstate(divide="ignore", invalid="ignore"):
        var = (m[-1] * w0 - m) ** 2 / (w0 * (1 - w0))
    return float(centros[np.nanargmax(var)])


def tirar_grupos_pequenos(mascara: np.ndarray, minimo_px: int) -> np.ndarray:
    rot, n = ndimage.label(mascara, structure=VIZINHOS_8)
    if n == 0:
        return mascara
    tam = np.bincount(rot.ravel())
    manter = tam >= minimo_px
    manter[0] = False
    return manter[rot]


def ligada_ao(mascara: np.ndarray, semente: np.ndarray) -> np.ndarray:
    """Grupos de água (8 vizinhos) que tocam a semente."""
    rot, n = ndimage.label(mascara, structure=VIZINHOS_8)
    if n == 0:
        return mascara
    tocam = np.unique(rot[semente & mascara])
    manter = np.zeros(n + 1, dtype=bool)
    manter[tocam[tocam > 0]] = True
    return manter[rot]


def agua_radar(arq_vv: Path, grade: dict) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict]:
    vv = para_a_grade(arq_vv, grade, Resampling.nearest, np.nan)
    valido = np.isfinite(vv) & (vv > 0)
    db = np.full(vv.shape, np.nan, dtype="float32")
    db[valido] = 10 * np.log10(vv[valido])
    cheio = np.where(valido, db, np.nanmedian(db))
    filtrado = ndimage.median_filter(cheio, size=ARGS.mediana)  # contra o ruído de speckle
    lim = otsu(filtrado[valido])
    info = {"limiar_otsu_db": lim, "limiar_usado_db": lim, "limiar_fora_da_faixa": False}
    if not ARGS.otsu_min <= lim <= ARGS.otsu_max:
        info.update(limiar_usado_db=ARGS.limiar_fixo, limiar_fora_da_faixa=True)
    return valido & (filtrado < info["limiar_usado_db"]), valido, db, info


def agua_optico(arqs: dict, grade: dict, com_deslocamento: bool) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict]:
    b03 = para_a_grade(arqs["b03"], grade, Resampling.nearest, np.nan)
    b08 = para_a_grade(arqs["b08"], grade, Resampling.nearest, np.nan)
    b11 = para_a_grade(arqs["b11"], grade, Resampling.bilinear, np.nan)  # 20 m -> 10 m
    scl = para_a_grade(arqs["scl"], grade, Resampling.nearest, 0)
    if com_deslocamento:  # versão de processamento 04.00 em diante: os valores trazem +1000
        b03, b08, b11 = (np.clip(x - 1000, 0, None) for x in (b03, b08, b11))
    valido = ~np.isin(scl, SCL_SEM_DADO) & np.isfinite(b03) & np.isfinite(b11) & ((b03 + b11) > 0)
    with np.errstate(divide="ignore", invalid="ignore"):
        mndwi = (b03 - b11) / (b03 + b11)
    fundo = np.clip(np.dstack([b11, b08, b03]) / 10000 / 0.35, 0, 1)  # falsa cor (B11, B08, B03): as bandas de cor natural não são lidas
    return valido & (mndwi > 0), valido, fundo, {"deslocamento_1000_retirado": bool(com_deslocamento)}


def poligonos(mascara: np.ndarray, grade: dict):
    """Pixels -> polígonos, sem simplificar (só a conversão de pixel em polígono)."""
    geoms = [shape(g) for g, v in features.shapes(mascara.astype("uint8"), mask=mascara, transform=grade["transform"]) if v == 1]
    return MultiPolygon(geoms) if geoms else None


# ---------------------------------------------------------------- figuras
def figura_cena(pasta: Path, nome: str, fundo: np.ndarray, radar: bool, ligada: np.ndarray, leito: np.ndarray, manchas: gpd.GeoDataFrame, grade: dict, titulo: str) -> None:
    x0, y1 = grade["transform"].c, grade["transform"].f
    ext = (x0, x0 + grade["width"] * RES, y1 - grade["height"] * RES, y1)
    fig, ax = plt.subplots(figsize=(8.2, 8.4))
    if radar:
        ax.imshow(fundo, cmap="gray", vmin=-25, vmax=0, extent=ext, interpolation="nearest")
    else:
        ax.imshow(fundo, extent=ext, interpolation="nearest")
    cam = np.zeros(ligada.shape + (4,), dtype="float32")
    cam[ligada] = COR_AGUA
    ax.imshow(cam, extent=ext, interpolation="nearest")
    ax.contour(leito.astype("uint8"), levels=[0.5], colors=COR_LEITO, linewidths=0.35, extent=ext, origin="upper")
    for cota, cor in COR_MANCHA.items():
        if cota in manchas.index:
            gpd.GeoSeries([manchas.loc[cota, "geometry"]], crs=c.CRS_PADRAO).boundary.plot(ax=ax, color=cor, linewidth=0.8)
    ax.set_xlim(ext[0], ext[1]); ax.set_ylim(ext[2], ext[3])  # noqa: E702
    ax.set_xticks([]); ax.set_yticks([])  # noqa: E702
    ax.set_title(titulo, fontsize=9.5, loc="left")
    ax.legend(handles=[Patch(facecolor=COR_AGUA[:3], alpha=0.45, label="água ligada ao rio (esta cena)"), Line2D([], [], color=COR_LEITO, lw=1, label="leito de referência"),
                       *[Line2D([], [], color=cor, lw=1.2, label=f"mancha do SGB até {cota} cm") for cota, cor in COR_MANCHA.items()]],
              loc="lower center", bbox_to_anchor=(0.5, -0.075), ncol=4, fontsize=7.5, frameon=False)
    fig.text(0.01, 0.005, ("Fundo: VV em dB, de −25 (preto) a 0 (branco)." if radar else "Fundo: falsa cor (B11, B08, B03).") + f" {c.CRS_PADRAO}. Produto derivado, para conferência.",
             fontsize=6.5, color="#555555")
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    fig.savefig(pasta / f"{nome}.png", dpi=200)
    plt.close(fig)


def figura_resumo(pasta: Path, b5: pd.DataFrame, sgb: pd.DataFrame) -> Path:
    fig, ax = plt.subplots(figsize=(8.5, 5.6))
    for (sensor, ano), g in b5.groupby(["sensor", b5.data_hora_local.str[:4].astype(int)]):
        ax.scatter(g.nivel_regua_cm, g.inundacao_em_terra_lado_brasileiro_km2, marker=FORMA[sensor], s=34, color=COR_ANO.get(ano, "#333333"), edgecolor="white", linewidth=0.6, zorder=3)
    ax.scatter(sgb.cota_cm, sgb.mancha_em_terra_km2, marker="D", s=46, color="black", zorder=4)
    for r in sgb.itertuples():
        ax.annotate(f"SGB {int(r.cota_cm)}", (r.cota_cm, r.mancha_em_terra_km2), textcoords="offset points", xytext=(6, -3), fontsize=7.5)
    anos = sorted({int(x[:4]) for x in b5.data_hora_local})
    ax.legend(handles=[*[Line2D([], [], marker="o", ls="", color=COR_ANO.get(a, "#333333"), label=str(a)) for a in anos],
                       Line2D([], [], marker="o", ls="", color="#999999", label="radar (Sentinel-1)"), Line2D([], [], marker="s", ls="", color="#999999", label="óptico (Sentinel-2)"),
                       Line2D([], [], marker="D", ls="", color="black", label="mancha do SGB (na própria cota)")], fontsize=7.5, frameon=False, loc="upper left")
    ax.set_xlabel("nível da régua no dia da cena (cm)", fontsize=9)
    ax.set_ylabel("inundação em terra, lado brasileiro (km²)", fontsize=9)
    ax.grid(color="#e3e2dc", lw=0.6)
    for lado in ("top", "right"):
        ax.spines[lado].set_visible(False)
    ax.set_title("Nível da régua × área de inundação em terra, por cena", fontsize=10, loc="left")
    fig.tight_layout()
    arq = pasta / "resumo-nivel-x-inundacao-em-terra_sentinel-sgb.png"
    fig.savefig(arq, dpi=200)
    plt.close(fig)
    return arq


# ---------------------------------------------------------------- principal
def main() -> None:
    global FONTES, LICENCAS
    if PROC.exists() and any(PROC.iterdir()) and not ARGS.refazer:
        raise SystemExit(f"{rel(PROC)} já tem arquivos (use --refazer para gerar de novo)")
    arq_cenas = next(iter(sorted(BRUTO.glob("cenas_planetary-computer_*_cena.csv"))), None)
    if arq_cenas is None:
        raise FileNotFoundError(f"tabela de cenas ausente em {rel(BRUTO)}: rode scripts/download/sentinel_planetary_computer.py")
    m_cenas = json.loads(arq_cenas.with_suffix(".json").read_text(encoding="utf-8"))
    LICENCAS = {k: {"licenca": v.get("licenca"), "endereco_da_licenca": v.get("endereco_da_licenca")} for k, v in m_cenas["colecoes"].items()}
    FONTES = ["Microsoft Planetary Computer — coleções " + " e ".join(v["colecao"] for v in m_cenas["colecoes"].values()) + " (dados Copernicus Sentinel)",
              "SGB — manchas de inundação por cota", "ANA — série diária e telemetria da estação fluviométrica", "IBGE — limite municipal"]
    cenas = pd.read_csv(arq_cenas, dtype={"versao_do_processamento": str}).fillna({"arquivos": "", "versao_do_processamento": ""})
    PROC.mkdir(parents=True, exist_ok=True)
    if ARGS.figuras:
        ARGS.figuras.mkdir(parents=True, exist_ok=True)

    # ---- área de estudo e grade de trabalho
    x0, y0, x1, y1 = m_cenas["area_de_estudo"]["limites"]
    ret = box(x0, y0, x1, y1)
    gx0, gy1 = np.floor(x0 / RES) * RES, np.ceil(y1 / RES) * RES
    grade = {"transform": from_origin(gx0, gy1, RES, RES), "width": int(np.ceil((x1 - gx0) / RES)), "height": int(np.ceil((gy1 - y0) / RES))}
    px_km2 = RES * RES / 1e6
    arq_area = PROC / "area-de-estudo_sgb_atual_retangulo.gpkg"
    gpd.GeoDataFrame({"descricao": ["retângulo envolvente das manchas por cota do SGB, com margem"], "margem_m": [m_cenas["area_de_estudo"]["margem_m"]]}, geometry=[ret], crs=c.CRS_PADRAO).to_file(arq_area, driver="GPKG", layer="area_de_estudo")
    meta(arq_area, descricao="área de estudo da rodada: retângulo envolvente da união das manchas por cota, no CRS do projeto, com margem", limites=[x0, y0, x1, y1], area_km2=ret.area / 1e6,
         grade_de_trabalho={"origem_x": gx0, "origem_y_topo": gy1, "resolucao_m": RES, "largura": grade["width"], "altura": grade["height"]})

    # ---- lado brasileiro e manchas acumuladas, na grade
    limite = c.carregar_area_estudo()
    brasil = features.rasterize([(limite.union_all(), 1)], out_shape=(grade["height"], grade["width"]), transform=grade["transform"], fill=0, dtype="uint8").astype(bool)
    manchas = ec.carregar_cenarios(ee.ARQ_COTAS, CAMADA_COTAS, ATRIBUTO_COTAS)  # geometry = união das manchas de cota <= X (função do repositório)
    manchas["cota"] = manchas.valor.astype(int)
    manchas = manchas.set_index("cota")
    mancha_px = {k: features.rasterize([(g, 1)], out_shape=brasil.shape, transform=grade["transform"], fill=0, dtype="uint8").astype(bool) for k, g in manchas.geometry.items()}

    # ---- nível da régua em cada cena (tabela A4: todas as cenas do catálogo, lidas ou não)
    diaria, tel = ler_serie_diaria(), ler_telemetria()
    niveis = pd.DataFrame([nivel_na_cena(pd.Timestamp(x), diaria, tel) for x in cenas.data_hora_local])
    a4 = pd.concat([cenas.drop(columns=["colecao", "plataforma", "arquivos"]), niveis], axis=1).sort_values(["data_hora_utc", "sensor"]).reset_index(drop=True)
    a4.insert(a4.columns.get_loc("telemetria_hora_da_leitura") + 1, "fuso_da_telemetria", np.where(a4.telemetria_hora_da_leitura != "", "não informado pelo serviço; comparada com a hora local da cena", ""))
    desc_nivel = {"nivel_regua_cm": "nível da régua usado para a cena: média diária do dia (consistida onde houver; senão a bruta)", "nivel_consistencia": "se o nível usado é consistido ou bruto",
                  "media_bruta_cm": "média diária bruta do dia", "media_consistida_cm": "média diária consistida do dia", "leitura_07h_cm": "leitura das 7h (dado bruto)", "leitura_17h_cm": "leitura das 17h (dado bruto)",
                  "nivel_interpolado_na_hora_cm": "ESTIMATIVA: reta entre as leituras de 7h e 17h, na hora local da cena (só quando a cena cai entre as duas)",
                  "telemetria_mais_proxima_cm": "leitura da telemetria de 15 min mais próxima da hora local da cena (quando há)", "telemetria_hora_da_leitura": "hora da leitura, no relógio do arquivo da telemetria",
                  "fuso_da_telemetria": "o serviço não informa o fuso da telemetria"}
    gravar(a4, "cenas-e-nivel-da-regua_sentinel-ana_2015-2024_cena", "A4 — Cenas encontradas e nível da régua em cada uma", {**m_cenas["campos"], **desc_nivel},
           casas={k: 1 for k in desc_nivel}, serie_diaria=diaria.attrs["arquivo"] if diaria is not None else "ausente", telemetria=tel.attrs["arquivos"] if tel is not None else "ausente",
           nota="Hora local = UTC−3. Em 2015 a média bruta e a consistida divergem: as duas estão lado a lado.")

    usar = cenas[cenas.situacao.isin(["lida", "já existia"]) & (cenas.cobertura_da_area_pct >= COBERTURA_MINIMA)].reset_index(drop=True)
    usar = pd.concat([usar, pd.DataFrame([nivel_na_cena(pd.Timestamp(x), diaria, tel) for x in usar.data_hora_local])], axis=1)

    def arquivos(r) -> dict:
        return {a.split("-")[1].split("_")[0]: BRUTO / r.sensor / a for a in r.arquivos.split("; ")}

    # ---- B4: leito de referência
    cand = usar[(usar.janela == ARGS.janela_leito) & usar.nivel_regua_cm.notna()]
    cand = cand[cand.sensor.str.startswith("sentinel1")] if cand.sensor.str.startswith("sentinel1").any() else cand
    if cand.empty:
        raise SystemExit(f"sem cena utilizável no período de rio baixo ({ARGS.janela_leito}): leito de referência não definido")
    ref = cand.loc[cand.nivel_regua_cm.idxmin()]
    minimo_px = int(np.ceil(ARGS.area_minima_ha * 1e4 / (RES * RES)))
    if ref.sensor.startswith("sentinel1"):
        leito, _, _, info_ref = agua_radar(arquivos(ref)["vv"], grade)
    else:
        leito, _, _, info_ref = agua_optico(arquivos(ref), grade, float(ref.versao_do_processamento or 0) >= 4)
    leito = tirar_grupos_pequenos(leito, minimo_px)
    rot_l, n_l = ndimage.label(leito, structure=VIZINHOS_8)
    info_leito = {"cena": ref.identificador, "sensor": ref.sensor, "data_hora_local": ref.data_hora_local, "nivel_regua_cm": float(ref.nivel_regua_cm), "nivel_consistencia": ref.nivel_consistencia,
                  "area_km2": float(leito.sum() * px_km2), "area_lado_brasileiro_km2": float((leito & brasil).sum() * px_km2), "grupos_de_agua": int(n_l),
                  "area_do_maior_grupo_km2": float(np.bincount(rot_l.ravel())[1:].max() * px_km2) if n_l else 0.0, **info_ref}
    arq_leito = PROC / f"leito-de-referencia_{ref.sensor}_{ref.data_hora_local[:4]}_10m.gpkg"
    gpd.GeoDataFrame({"identificador": [ref.identificador], "data_hora_local": [ref.data_hora_local], "nivel_regua_cm": [float(ref.nivel_regua_cm)], "area_km2": [info_leito["area_km2"]]},
                     geometry=[poligonos(leito, grade)], crs=c.CRS_PADRAO).to_file(arq_leito, driver="GPKG", layer="leito_de_referencia")
    meta(arq_leito, descricao="leito de referência: a água da cena do período de rio baixo com o menor nível da régua", **info_leito,
         colunas={"identificador": "cena", "data_hora_local": "data e hora local da cena (UTC−3)", "nivel_regua_cm": "nível da régua no dia", "area_km2": "área da água"})
    logger.info("Leito de referência: %s, %.0f cm, %.2f km²", ref.identificador, ref.nivel_regua_cm, info_leito["area_km2"])
    sgb = pd.DataFrame([{"cota_cm": k, "mancha_acumulada_km2": float(manchas.loc[k, "geometry"].area / 1e6),
                         "mancha_em_terra_km2": float((mancha_px[k] & ~leito & brasil).sum() * px_km2)} for k in manchas.index])

    # ---- cada cena: B1–B5, C2, polígonos e figura
    b5, c2, geo = [], [], {s: {k: [] for k in CAMADAS} for s in usar.sensor.unique()}
    for r in usar.sort_values("data_hora_utc").itertuples():
        radar = r.sensor.startswith("sentinel1")
        arqs = arquivos(r)
        if radar:
            agua, valido, fundo, info = agua_radar(arqs["vv"], grade)
        else:
            agua, valido, fundo, info = agua_optico(arqs, grade, float(r.versao_do_processamento or 0) >= 4)
        agua = tirar_grupos_pequenos(agua, minimo_px)
        ligada = ligada_ao(agua, leito)
        terra = ligada & ~leito
        cam = {"agua_total": agua, "agua_ligada_ao_rio": ligada, "inundacao_em_terra": terra}
        comum = {"sensor": r.sensor, "identificador": r.identificador, "janela": r.janela, "data_hora_utc": r.data_hora_utc, "data_hora_local": r.data_hora_local, "nivel_regua_cm": r.nivel_regua_cm,
                 "nivel_consistencia": r.nivel_consistencia}
        b5.append({**comum, "media_bruta_cm": r.media_bruta_cm, "media_consistida_cm": r.media_consistida_cm,
                   **{f"{k}_km2": float(v.sum() * px_km2) for k, v in cam.items()}, **{f"{k}_lado_brasileiro_km2": float((v & brasil).sum() * px_km2) for k, v in cam.items()},
                   "sem_dado_pct": float(100 * (~valido).mean()), "sem_dado_lado_brasileiro_pct": float(100 * (~valido & brasil).sum() / brasil.sum()),
                   "limiar_otsu_db": info.get("limiar_otsu_db", np.nan), "limiar_usado_db": info.get("limiar_usado_db", np.nan), "limiar_fora_da_faixa": info.get("limiar_fora_da_faixa", False),
                   "e_o_leito_de_referencia": r.identificador == ref.identificador})
        dominio = brasil & valido  # só do lado brasileiro e onde a cena tem dado
        for k in manchas.index:
            a, b = terra & dominio, mancha_px[k] & ~leito & dominio
            inter, uniao = int((a & b).sum()), int((a | b).sum())
            c2.append({**comum, "cota_cm": k, "inundacao_em_terra_km2": a.sum() * px_km2, "mancha_sgb_em_terra_km2": b.sum() * px_km2, "intersecao_km2": inter * px_km2, "uniao_km2": uniao * px_km2,
                       "intersecao_sobre_uniao": inter / uniao if uniao else np.nan, "fracao_da_agua_dentro_da_mancha": inter / a.sum() if a.sum() else np.nan,
                       "fracao_da_mancha_coberta_pela_agua": inter / b.sum() if b.sum() else np.nan})
        for k, v in cam.items():
            geo[r.sensor][k].append({"identificador": r.identificador, "data": r.data_hora_local[:10], "data_hora_local": r.data_hora_local, "nivel_regua_cm": r.nivel_regua_cm,
                                     "area_km2": float(v.sum() * px_km2), "geometry": poligonos(v, grade)})
        if ARGS.figuras:
            hora = pd.Timestamp(r.data_hora_local)
            nome = f"agua-ligada-ao-rio_{r.sensor}_{hora:%Y%m%dT%H%M}_10m"
            figura_cena(ARGS.figuras, nome, fundo if not radar else ndimage.median_filter(np.nan_to_num(fundo, nan=-30), size=3), radar, ligada, leito, manchas, grade,
                        f"{'Sentinel-1 (radar, VV)' if radar else 'Sentinel-2 (óptico)'} — {hora:%d/%m/%Y}, {hora:%Hh%M} (hora local) — régua: {fmt(float(r.nivel_regua_cm), 0)} cm ({r.nivel_consistencia})")
            (ARGS.figuras / f"{nome}.json").write_text(json.dumps({"produto": f"{nome}.png", "script": SCRIPT, "motivo": MOTIVO, "identificador_da_cena": r.identificador, "sensor": r.sensor,
                                                                     "data_hora_local": r.data_hora_local, "nivel_regua_cm": float(r.nivel_regua_cm), "fontes": FONTES, "licencas": LICENCAS,
                                                                     "data_processamento": datetime.now().astimezone().isoformat(timespec="seconds"), "leito_de_referencia": ref.identificador,
                                                                     "limitacoes": LIMITACOES, "fundo": "VV em dB (mediana 3×3 só para exibição)" if radar else "falsa cor B11, B08, B03"},
                                                                    ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("%s %s: água %.2f km², ligada %.2f, em terra %.2f", r.sensor, r.data_hora_local, agua.sum() * px_km2, ligada.sum() * px_km2, terra.sum() * px_km2)
    b5, c2 = pd.DataFrame(b5), pd.DataFrame(c2)

    # ---- GeoPackages: as três camadas de todas as cenas, por sensor
    col_geo = {"identificador": "cena", "data": "data local", "data_hora_local": "data e hora local (UTC−3)", "nivel_regua_cm": "nível da régua no dia da cena", "area_km2": "área da camada na cena"}
    for sensor, cams in geo.items():
        arq = PROC / f"agua-observada_{sensor}_{usar.data_hora_local.str[:4].min()}-{usar.data_hora_local.str[:4].max()}_10m.gpkg"
        if arq.exists():
            arq.unlink()
        for k, linhas in cams.items():
            g = gpd.GeoDataFrame(linhas, crs=c.CRS_PADRAO)
            g[g.geometry.notna()].to_file(arq, driver="GPKG", layer=k)
        meta(arq, descricao="água observada em cada cena: três camadas (agua_total, agua_ligada_ao_rio, inundacao_em_terra), uma linha por cena; polígonos de pixel, sem simplificação",
             camadas=list(CAMADAS), cenas=len(cams[CAMADAS[0]]), colunas=col_geo, leito_de_referencia=info_leito, aviso="produto derivado, para conferência no mapa; não vai ao geoportal")
        logger.info("GeoPackage: %s (%.1f MB)", rel(arq), arq.stat().st_size / 1e6)

    # ---- tabelas B5, C2, C3, C4
    comuns = {"sensor": "sensor e produto", "identificador": "cena", "janela": "período de busca", "data_hora_utc": "data e hora UTC", "data_hora_local": "data e hora local (UTC−3)",
              "nivel_regua_cm": desc_nivel["nivel_regua_cm"], "nivel_consistencia": desc_nivel["nivel_consistencia"]}
    gravar(b5, "agua-por-cena_sentinel_2015-2024_cena", "B5 — Área de água em cada cena", {
        **comuns, "media_bruta_cm": desc_nivel["media_bruta_cm"], "media_consistida_cm": desc_nivel["media_consistida_cm"],
        "agua_total_km2": "água da cena na área de estudo", "agua_ligada_ao_rio_km2": "água em grupos que tocam o leito de referência", "inundacao_em_terra_km2": "água ligada ao rio fora do leito de referência",
        "agua_total_lado_brasileiro_km2": "água da cena, só dentro do limite municipal", "agua_ligada_ao_rio_lado_brasileiro_km2": "água ligada ao rio, só dentro do limite municipal",
        "inundacao_em_terra_lado_brasileiro_km2": "inundação em terra, só dentro do limite municipal", "sem_dado_pct": "% da área de estudo sem dado na cena (nuvem, sombra, borda)",
        "sem_dado_lado_brasileiro_pct": "o mesmo, só do lado brasileiro", "limiar_otsu_db": "limiar de Otsu do radar, em dB", "limiar_usado_db": "limiar aplicado",
        "limiar_fora_da_faixa": "o limiar de Otsu saiu da faixa aceita e foi trocado pelo fixo", "e_o_leito_de_referencia": "esta é a cena do leito de referência"},
        casas={"nivel_regua_cm": 1, "media_bruta_cm": 1, "media_consistida_cm": 1}, leito_de_referencia=info_leito, area_minima_ha=ARGS.area_minima_ha,
        nota=f"Leito de referência: {ref.identificador} ({ref.data_hora_local}), régua em {fmt(float(ref.nivel_regua_cm), 1)} cm, {fmt(info_leito['area_km2'])} km².")
    col_c2 = {**comuns, "cota_cm": "cota da mancha acumulada do SGB", "inundacao_em_terra_km2": "inundação em terra da cena (lado brasileiro, onde a cena tem dado)",
              "mancha_sgb_em_terra_km2": "mancha do SGB fora do leito de referência (mesmo domínio)", "intersecao_km2": "área comum", "uniao_km2": "área de uma ou de outra",
              "intersecao_sobre_uniao": "interseção / união", "fracao_da_agua_dentro_da_mancha": "quanto da inundação em terra da cena cai dentro da mancha",
              "fracao_da_mancha_coberta_pela_agua": "quanto da mancha a inundação em terra da cena cobre"}
    gravar(c2, "agua-x-manchas-sgb_sentinel-sgb_2015-2024_cena-cota", "C2 — Inundação em terra de cada cena × cada mancha acumulada do SGB", col_c2,
           casas={"nivel_regua_cm": 1, "intersecao_sobre_uniao": 3, "fracao_da_agua_dentro_da_mancha": 3, "fracao_da_mancha_coberta_pela_agua": 3}, manchas_do_sgb=sgb.to_dict("records"),
           dominio="lado brasileiro (limite municipal), dentro da área de estudo, onde a cena tem dado")
    melhor = c2.loc[c2.groupby("identificador").intersecao_sobre_uniao.idxmax().dropna()]
    c3 = melhor[["sensor", "identificador", "janela", "data_hora_local", "nivel_regua_cm", "nivel_consistencia", "inundacao_em_terra_km2", "cota_cm", "mancha_sgb_em_terra_km2", "intersecao_sobre_uniao",
                 "fracao_da_agua_dentro_da_mancha", "fracao_da_mancha_coberta_pela_agua"]].rename(columns={"cota_cm": "cota_de_maior_intersecao_sobre_uniao_cm"})
    c3 = c3.merge(b5[["identificador", "media_bruta_cm", "sem_dado_lado_brasileiro_pct"]], on="identificador").sort_values("nivel_regua_cm").reset_index(drop=True)
    gravar(c3, "resumo-cota-de-maior-sobreposicao_sentinel-sgb_2015-2024_cena", "C3 — Cota do SGB de maior interseção/união em cada cena, por ordem do nível da régua",
           {**col_c2, "cota_de_maior_intersecao_sobre_uniao_cm": "cota da mancha acumulada do SGB com a maior razão interseção/união para a cena", "media_bruta_cm": desc_nivel["media_bruta_cm"],
            "sem_dado_lado_brasileiro_pct": "% do lado brasileiro sem dado na cena"},
           casas={"nivel_regua_cm": 1, "media_bruta_cm": 1, "intersecao_sobre_uniao": 3, "fracao_da_agua_dentro_da_mancha": 3, "fracao_da_mancha_coberta_pela_agua": 3})

    # C4: a área de cada cena de --janelas-c4 cabe na relação nível × área das cenas de outros anos (mesmo sensor)?
    c4 = []
    for r in b5[b5.janela.isin(ARGS.janelas_c4)].itertuples():
        outros = b5[(b5.sensor == r.sensor) & (b5.data_hora_local.str[:4] != r.data_hora_local[:4]) & b5.nivel_regua_cm.notna() & (b5.sem_dado_lado_brasileiro_pct < 5)].sort_values("nivel_regua_cm")
        linha = {"sensor": r.sensor, "identificador": r.identificador, "janela": r.janela, "data_hora_local": r.data_hora_local, "media_consistida_cm": r.media_consistida_cm, "media_bruta_cm": r.media_bruta_cm,
                 "inundacao_em_terra_lado_brasileiro_km2": r.inundacao_em_terra_lado_brasileiro_km2, "sem_dado_lado_brasileiro_pct": r.sem_dado_lado_brasileiro_pct, "cenas_de_comparacao": len(outros),
                 "area_esperada_no_nivel_consistido_km2": np.nan, "area_esperada_no_nivel_bruto_km2": np.nan, "nivel_de_area_igual_nas_outras_cenas_cm": np.nan, "leitura": "sem cenas de comparação"}
        if len(outros) >= 2:
            x, y = outros.nivel_regua_cm.to_numpy(), outros.inundacao_em_terra_lado_brasileiro_km2.to_numpy()
            dentro = lambda v: pd.notna(v) and x.min() <= v <= x.max()  # noqa: E731
            ec_, eb_ = (float(np.interp(v, x, y)) if dentro(v) else np.nan for v in (r.media_consistida_cm, r.media_bruta_cm))
            o = np.argsort(y)
            linha.update(area_esperada_no_nivel_consistido_km2=ec_, area_esperada_no_nivel_bruto_km2=eb_,
                         nivel_de_area_igual_nas_outras_cenas_cm=float(np.interp(r.inundacao_em_terra_lado_brasileiro_km2, y[o], x[o])) if y.min() <= r.inundacao_em_terra_lado_brasileiro_km2 <= y.max() else np.nan)
            obs = r.inundacao_em_terra_lado_brasileiro_km2
            if pd.isna(r.media_bruta_cm) or pd.isna(r.media_consistida_cm) or abs(r.media_bruta_cm - r.media_consistida_cm) < 10:
                linha["leitura"] = "bruta e consistida iguais (ou a menos de 10 cm): a cena não separa as duas"
            elif np.isnan(ec_) or np.isnan(eb_):
                linha["leitura"] = "um dos dois níveis fica fora da faixa de níveis das cenas de comparação: sem área esperada para comparar"
            else:
                linha["leitura"] = "área mais próxima da esperada no nível " + ("consistido" if abs(obs - ec_) < abs(obs - eb_) else "bruto")
        c4.append(linha)
    c4 = pd.DataFrame(c4, columns=["sensor", "identificador", "janela", "data_hora_local", "media_consistida_cm", "media_bruta_cm", "inundacao_em_terra_lado_brasileiro_km2", "sem_dado_lado_brasileiro_pct",
                                   "cenas_de_comparacao", "area_esperada_no_nivel_consistido_km2", "area_esperada_no_nivel_bruto_km2", "nivel_de_area_igual_nas_outras_cenas_cm", "leitura"])
    gravar(c4, "compatibilidade-nivel-x-area_sentinel-ana_2015-2017_cena", "C4 — Área de água das cenas de 2015 a 2017 × relação nível–área das cenas de outros anos", {
        "sensor": "sensor e produto", "identificador": "cena", "janela": "período de busca", "data_hora_local": "data e hora local (UTC−3)", "media_consistida_cm": desc_nivel["media_consistida_cm"],
        "media_bruta_cm": desc_nivel["media_bruta_cm"], "inundacao_em_terra_lado_brasileiro_km2": "inundação em terra da cena, lado brasileiro", "sem_dado_lado_brasileiro_pct": "% do lado brasileiro sem dado na cena",
        "cenas_de_comparacao": "cenas do mesmo sensor, de outros anos, com menos de 5 % sem dado", "area_esperada_no_nivel_consistido_km2": "área das outras cenas, interpolada no nível consistido desta",
        "area_esperada_no_nivel_bruto_km2": "área das outras cenas, interpolada no nível bruto desta", "nivel_de_area_igual_nas_outras_cenas_cm": "nível em que as outras cenas têm a área desta (interpolado)",
        "leitura": "o que os números permitem dizer"}, casas={"media_consistida_cm": 1, "media_bruta_cm": 1, "nivel_de_area_igual_nas_outras_cenas_cm": 0},
        nota="Interpolação linear entre as cenas de comparação, ordenadas pelo nível; fora da faixa de níveis delas não há área esperada.")

    if ARGS.figuras:
        arq = figura_resumo(ARGS.figuras, b5, sgb)
        arq.with_suffix(".json").write_text(json.dumps({"produto": arq.name, "script": SCRIPT, "motivo": MOTIVO, "fontes": FONTES, "licencas": LICENCAS, "manchas_do_sgb": sgb.to_dict("records"),
                                                        "eixo_x": "nível da régua no dia da cena (cm)", "eixo_y": "inundação em terra, lado brasileiro (km²)", "limitacoes": LIMITACOES},
                                                       ensure_ascii=False, indent=2), encoding="utf-8")
    if ARGS.copia:  # cópia para conferência: GeoPackages, tabelas e os .json
        ARGS.copia.mkdir(parents=True, exist_ok=True)
        for arq in sorted(PROC.iterdir()):
            shutil.copy2(arq, ARGS.copia / arq.name)
        logger.info("Cópia para conferência: %d arquivos", len(list(PROC.iterdir())))
    print(json.dumps({"area_de_estudo": [x0, y0, x1, y1], "cenas_processadas": int(len(b5)), "leito": info_leito, "manchas_sgb": sgb.to_dict("records")}, ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", force=True)
    _p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    _p.add_argument("--mediana", type=int, default=5, help="lado da janela do filtro de mediana do radar (pixels)")
    _p.add_argument("--otsu-min", type=float, default=-24.0, help="menor limiar de Otsu aceito (dB)")
    _p.add_argument("--otsu-max", type=float, default=-12.0, help="maior limiar de Otsu aceito (dB)")
    _p.add_argument("--limiar-fixo", type=float, default=-18.0, help="limiar usado quando o de Otsu sai da faixa (dB)")
    _p.add_argument("--area-minima-ha", type=float, default=0.5, help="grupos de água menores que isto são retirados (ha)")
    _p.add_argument("--janela-leito", default="J0", help="período de rio baixo, de onde sai o leito de referência")
    _p.add_argument("--janelas-c4", nargs="*", default=["J2", "J5"], help="períodos examinados na comparação nível × área")
    _p.add_argument("--figuras", type=Path, help="pasta (fora do repositório) que recebe as figuras de conferência; sem ela, não há figura")
    _p.add_argument("--copia", type=Path, help="pasta que recebe uma cópia dos produtos, para conferência")
    _p.add_argument("--refazer", action="store_true", help="gera de novo mesmo se a pasta de saída já tiver arquivos")
    ARGS = _p.parse_args()
    FONTES, LICENCAS = [], {}
    for _pasta in (ARGS.figuras, ARGS.copia):
        if _pasta is not None and c.RAIZ in _pasta.resolve().parents:
            raise SystemExit("--figuras e --copia têm de ficar fora do repositório")
    main()
