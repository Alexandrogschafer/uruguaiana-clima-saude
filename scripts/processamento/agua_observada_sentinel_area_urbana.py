"""
Água das cheias na área urbana mapeada pelo SGB, em cenas Sentinel, e comparação
com as manchas de inundação por cota.

Difere de agua_observada_sentinel.py (que continua a reproduzir os produtos
dele) em três pontos:
  - domínio: um retângulo dado em graus (--retangulo-geografico, SIRGAS 2000),
    convertido para o CRS do projeto; tudo é medido dentro dele, do lado
    brasileiro (limite municipal) e em terra (fora do leito de referência);
  - radar por MUDANÇA, por órbita relativa: cada cena é comparada com a cena de
    referência da mesma órbita (a de menor nível da régua abaixo de
    --nivel-max-referencia cm; se não houver, a de menor nível, marcada):
      água = (VV_cena − VV_ref <= --mudanca dB e VV_cena <= --vv-max dB)
             OU (água na referência e VV_cena <= --vv-max dB)
      água da referência = VV_ref <= --vv-agua-referencia dB
    VV em dB, mediana 5×5; grupos menores que --area-minima-ha são retirados;
  - controle de qualidade: no radar, a cena tem de marcar como água pelo menos
    --fracao-minima-do-rio do leito principal (o maior grupo de água da
    referência da órbita); no óptico (MNDWI > 0, como no outro roteiro), nuvem ou
    sombra em mais de --nuvem-max % da área tira a cena das comparações.

Entradas: as janelas já lidas por scripts/download/sentinel_planetary_computer.py
(modo padrão e modo --fatias); nada é baixado aqui. Leito de referência comum
(para "em terra"): a água da referência de menor nível entre as órbitas.

Saídas em data/processed/agua_observada_sentinel_area_urbana/ (fora do git);
figuras só em --figuras (fora do repositório). Produtos DERIVADOS, para
conferência no mapa; não vão ao geoportal.

Uso:
  python scripts/processamento/agua_observada_sentinel_area_urbana.py \
      --retangulo-geografico LAT_SUL LAT_NORTE LON_OESTE LON_LESTE --fonte-do-retangulo "TEXTO" \
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
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402
from rasterio import features  # noqa: E402
from rasterio.transform import from_origin  # noqa: E402
from rasterio.warp import Resampling, transform_geom  # noqa: E402
from scipy import ndimage  # noqa: E402
from shapely.geometry import box, mapping, shape  # noqa: E402

import agua_observada_sentinel as ao  # noqa: E402
import dinamica_populacional_comum as c  # noqa: E402
import exposicao_inundacao_cenarios as ec  # noqa: E402
import exposicao_inundacao_enderecos as ee  # noqa: E402

logger = logging.getLogger(__name__)

SCRIPT = "scripts/processamento/agua_observada_sentinel_area_urbana.py"
MOTIVO = "mapear a água das cheias na área urbana mapeada pelo SGB e comparar com as manchas por cota"
PROC = c.RAIZ / "data" / "processed" / "agua_observada_sentinel_area_urbana"
ARQ_AREA_ATINGIDA = c.RAIZ / "data" / "processed" / "conferencia_fontes_inundacao" / "area-diretamente-atingida_fepam_2024-05_recorte-municipio-e-vizinhos.gpkg"
CRS_GEOGRAFICO = "EPSG:4674"
RES = ao.RES
SCL_NUVEM_OU_SOMBRA = (3, 8, 9, 10)
CAMADAS = ao.CAMADAS
RADAR, OPTICO = "sentinel1-rtc", "sentinel2-l2a"
LIMITACOES = [*ao.LIMITACOES[:2], "radar por mudança: o que já era água ou superfície lisa na referência só entra pela segunda regra; área alagada sob vegetação ou entre prédios continua sem ser vista",
              "órbita sem cena de rio baixo usa como referência a cena de menor nível que tem: a mudança fica subestimada (marcado na tabela das referências)",
              "o leito de referência inclui toda a água da cena de referência (rio, lagoas, açudes)", "no óptico, nuvem e sombra ficam sem dado e podem cortar a ligação da água com o leito"]
COR_ANO = ao.COR_ANO
FORMA = {RADAR: "o", OPTICO: "s"}
COR_MANCHA = {952: "#ff7f00", 1205: "#00a087", 1252: "#e7298a"}


# ---------------------------------------------------------------- gravação
def rel(p: Path) -> str:
    return str(Path(p).relative_to(c.RAIZ))


def meta(caminho: Path, **kw) -> None:
    c.gravar_meta(caminho, crs=c.CRS_PADRAO, status=c.STATUS_CONFERENCIA, ligado_ao_portal=False, script=SCRIPT, motivo=MOTIVO, fontes=FONTES, licencas=LICENCAS,
                  fora_do_git="data/processed/ é ignorado; produto derivado, para conferência", limitacoes=LIMITACOES, avisos=AVISOS or None,
                  argumentos={k: (v.name if isinstance(v, Path) else v) for k, v in vars(ARGS).items() if k not in ("figuras", "copia", "mancha_extra")}, **kw)


def gravar(t: pd.DataFrame, nome: str, titulo: str, colunas: dict, nota: str = "", casas: dict | None = None, **kw) -> Path:
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


# ---------------------------------------------------------------- raster
def vv_em_db(arq: Path, grade: dict) -> tuple[np.ndarray, np.ndarray]:
    """VV da janela -> grade de trabalho, em dB, com a mediana contra o speckle; e a máscara de onde há dado."""
    vv = ao.para_a_grade(arq, grade, Resampling.nearest, np.nan)
    valido = np.isfinite(vv) & (vv > 0)
    db = np.full(vv.shape, np.nan, dtype="float32")
    db[valido] = 10 * np.log10(vv[valido])
    if not valido.any():
        return db, valido
    return ndimage.median_filter(np.where(valido, db, np.nanmedian(db)), size=ARGS.mediana).astype("float32"), valido


def maior_grupo(mascara: np.ndarray) -> np.ndarray:
    rot, n = ndimage.label(mascara, structure=ao.VIZINHOS_8)
    if n == 0:
        return mascara
    tam = np.bincount(rot.ravel())
    tam[0] = 0
    return rot == tam.argmax()


def agua_por_mudanca(db: np.ndarray, valido: np.ndarray, ref: dict, mudanca: float, minimo_px: int) -> np.ndarray:
    comum = valido & ref["valido"]
    agua = ((db - ref["db"] <= mudanca) & (db <= ARGS.vv_max)) | (ref["agua"] & (db <= ARGS.vv_max))
    return ao.tirar_grupos_pequenos(agua & comum, minimo_px)


def comparar(terra: np.ndarray, mancha: np.ndarray, dominio: np.ndarray, px_km2: float) -> dict:
    a, b = terra & dominio, mancha & dominio
    inter, uniao = int((a & b).sum()), int((a | b).sum())
    return {"inundacao_em_terra_km2": a.sum() * px_km2, "mancha_em_terra_km2": b.sum() * px_km2, "intersecao_km2": inter * px_km2, "uniao_km2": uniao * px_km2,
            "intersecao_sobre_uniao": inter / uniao if uniao else np.nan, "fracao_da_agua_dentro_da_mancha": inter / a.sum() if a.sum() else np.nan,
            "fracao_da_mancha_coberta_pela_agua": inter / b.sum() if b.sum() else np.nan}


# ---------------------------------------------------------------- figuras
def _fundo(ax, fundo, radar, ext):
    if radar:
        ax.imshow(fundo, cmap="gray", vmin=-25, vmax=0, extent=ext, interpolation="nearest")
    else:
        ax.imshow(fundo, extent=ext, interpolation="nearest")
    ax.set_xlim(ext[0], ext[1]); ax.set_ylim(ext[2], ext[3])  # noqa: E702
    ax.set_xticks([]); ax.set_yticks([])  # noqa: E702


def _camada(ax, mascara, cor, ext):
    cam = np.zeros(mascara.shape + (4,), dtype="float32")
    cam[mascara] = cor
    ax.imshow(cam, extent=ext, interpolation="nearest")


def figura_cena(arq: Path, fundo, radar: bool, ligada, leito, contornos: dict, urbano, ext, titulo: str) -> None:
    fig, ax = plt.subplots(figsize=(9.2, 7.6))
    _fundo(ax, fundo, radar, ext)
    _camada(ax, ligada, ao.COR_AGUA, ext)
    ax.contour(leito.astype("uint8"), levels=[0.5], colors=ao.COR_LEITO, linewidths=0.5, extent=ext, origin="upper")
    for cota, g in contornos.items():
        gpd.GeoSeries([g], crs=c.CRS_PADRAO).boundary.plot(ax=ax, color=COR_MANCHA[cota], linewidth=1.0)
    gpd.GeoSeries([urbano], crs=c.CRS_PADRAO).boundary.plot(ax=ax, color="white", linewidth=0.8, linestyle=(0, (4, 3)))
    ax.set_title(titulo, fontsize=9.5, loc="left")
    ax.legend(handles=[Patch(facecolor=ao.COR_AGUA[:3], alpha=0.45, label="água ligada ao rio (esta cena)"), Line2D([], [], color=ao.COR_LEITO, lw=1, label="leito de referência"),
                       *[Line2D([], [], color=COR_MANCHA[k], lw=1.2, label=f"mancha do SGB até {k} cm") for k in contornos], Line2D([], [], color="#888888", lw=1, ls=(0, (4, 3)), label="área urbana (SGB)")],
              loc="lower center", bbox_to_anchor=(0.5, -0.085), ncol=3, fontsize=7.5, frameon=False)
    fig.text(0.01, 0.005, ("Fundo: VV em dB, de −25 (preto) a 0 (branco)." if radar else "Fundo: falsa cor (vermelho B11, verde B08, azul B03).") + f" {c.CRS_PADRAO}. Produto derivado, para conferência.",
             fontsize=6.5, color="#555555")
    fig.tight_layout(rect=(0, 0.035, 1, 1))
    fig.savefig(arq, dpi=200)
    plt.close(fig)


def figura_lado_a_lado(arq: Path, fundo, radar: bool, terra, mancha, leito, urbano, ext, titulo: str, cota: int, nota: str) -> None:
    fig, eixos = plt.subplots(1, 2, figsize=(15, 6.6))
    for ax, masc, cor, sub in ((eixos[0], terra, ao.COR_AGUA, "inundação em terra na cena"), (eixos[1], mancha, (0.91, 0.16, 0.54, 0.45), f"mancha do SGB até {cota} cm, em terra")):
        _fundo(ax, fundo, radar, ext)
        _camada(ax, masc, cor, ext)
        ax.contour(leito.astype("uint8"), levels=[0.5], colors=ao.COR_LEITO, linewidths=0.5, extent=ext, origin="upper")
        gpd.GeoSeries([urbano], crs=c.CRS_PADRAO).boundary.plot(ax=ax, color="white", linewidth=0.8, linestyle=(0, (4, 3)))
        ax.set_title(sub, fontsize=9.5, loc="left")
    fig.suptitle(titulo, fontsize=10.5, x=0.01, ha="left")
    fig.text(0.01, 0.01, nota + " Contorno amarelo: leito de referência. Tracejado: área urbana (SGB). Produto derivado, para conferência.", fontsize=7, color="#555555")
    fig.tight_layout(rect=(0, 0.03, 1, 0.95))
    fig.savefig(arq, dpi=200)
    plt.close(fig)


def figura_resumo(arq: Path, boas: pd.DataFrame, sgb: pd.DataFrame) -> None:
    fig, ax = plt.subplots(figsize=(8.8, 5.8))
    for (sensor, ano), g in boas.groupby(["sensor", boas.data_hora_local.str[:4].astype(int)]):
        ax.scatter(g.nivel_regua_cm, g.inundacao_em_terra_km2, marker=FORMA[sensor], s=36, color=COR_ANO.get(ano, "#333333"), edgecolor="white", linewidth=0.6, zorder=3)
    for r in sgb.itertuples():
        ax.scatter([r.cota_cm], [r.mancha_em_terra_km2], marker="D", s=52, facecolor="none" if r.extraida_de_figura else "black", edgecolor="black", linewidth=1.4, zorder=4)
        ax.annotate(f"SGB {int(r.cota_cm)}" + (" (extraída)" if r.extraida_de_figura else ""), (r.cota_cm, r.mancha_em_terra_km2), textcoords="offset points", xytext=(7, -3), fontsize=7.5)
    anos = sorted({int(x[:4]) for x in boas.data_hora_local})
    ax.legend(handles=[*[Line2D([], [], marker="o", ls="", color=COR_ANO.get(a, "#333333"), label=str(a)) for a in anos], Line2D([], [], marker="o", ls="", color="#999999", label="radar (Sentinel-1)"),
                       Line2D([], [], marker="s", ls="", color="#999999", label="óptico (Sentinel-2)"), Line2D([], [], marker="D", ls="", color="black", label="mancha do SGB (na própria cota)"),
                       Line2D([], [], marker="D", ls="", mfc="none", color="black", label="mancha extraída de figura, não conferida")], fontsize=7.5, frameon=False, loc="upper left")
    ax.set_xlabel("nível da régua no dia da cena (cm)", fontsize=9)
    ax.set_ylabel("inundação em terra na área urbana (km²)", fontsize=9)
    ax.grid(color="#e3e2dc", lw=0.6)
    for lado in ("top", "right"):
        ax.spines[lado].set_visible(False)
    ax.set_title("Nível da régua × inundação em terra na área urbana, por cena boa", fontsize=10, loc="left")
    fig.tight_layout()
    fig.savefig(arq, dpi=200)
    plt.close(fig)


# ---------------------------------------------------------------- principal
def main() -> None:
    global FONTES, LICENCAS
    if PROC.exists() and any(PROC.iterdir()) and not ARGS.refazer:
        raise SystemExit(f"{rel(PROC)} já tem arquivos (use --refazer para gerar de novo)")
    arq_cenas = next(iter(sorted(ao.BRUTO.glob("cenas_planetary-computer_*_cena.csv"))), None)
    if arq_cenas is None:
        raise FileNotFoundError(f"tabela de cenas ausente em {rel(ao.BRUTO)}: rode scripts/download/sentinel_planetary_computer.py")
    m_cenas = json.loads(arq_cenas.with_suffix(".json").read_text(encoding="utf-8"))
    LICENCAS = {k: {"licenca": v.get("licenca"), "endereco_da_licenca": v.get("endereco_da_licenca")} for k, v in m_cenas["colecoes"].items()}
    FONTES = ["Microsoft Planetary Computer — coleções " + " e ".join(v["colecao"] for v in m_cenas["colecoes"].values()) + " (dados Copernicus Sentinel)",
              "SGB — manchas de inundação por cota e área de estudo do relatório das manchas", "ANA — série diária e telemetria da estação fluviométrica", "IBGE — limite municipal"]
    PROC.mkdir(parents=True, exist_ok=True)
    if ARGS.figuras:
        ARGS.figuras.mkdir(parents=True, exist_ok=True)

    # ---- área urbana: retângulo em graus -> CRS do projeto (reprojeção explícita; lados adensados)
    sul, norte, oeste, leste = ARGS.retangulo_geografico
    lado = np.linspace(0, 1, 21)
    anel = [(oeste + (leste - oeste) * t, sul) for t in lado] + [(leste, sul + (norte - sul) * t) for t in lado] + [(leste - (leste - oeste) * t, norte) for t in lado] + [(oeste, norte - (norte - sul) * t) for t in lado]
    urbano = shape(transform_geom(CRS_GEOGRAFICO, c.CRS_PADRAO, {"type": "Polygon", "coordinates": [anel + [anel[0]]]}))
    x0, y0, x1, y1 = urbano.bounds
    gx0, gy1 = np.floor(x0 / RES) * RES, np.ceil(y1 / RES) * RES
    grade = {"transform": from_origin(gx0, gy1, RES, RES), "width": int(np.ceil((x1 - gx0) / RES)), "height": int(np.ceil((gy1 - y0) / RES))}
    forma = (grade["height"], grade["width"])
    ext = (gx0, gx0 + grade["width"] * RES, gy1 - grade["height"] * RES, gy1)
    px_km2 = RES * RES / 1e6
    limite = c.carregar_area_estudo()
    dentro = features.rasterize([(urbano, 1)], out_shape=forma, transform=grade["transform"], fill=0, dtype="uint8").astype(bool)
    brasil = features.rasterize([(limite.union_all(), 1)], out_shape=forma, transform=grade["transform"], fill=0, dtype="uint8").astype(bool)
    dom = dentro & brasil
    arq_area = PROC / "area-urbana_sgb_2023_retangulo.gpkg"
    gpd.GeoDataFrame({"descricao": ["área urbana que a fonte declara mapear"], "fonte": [ARGS.fonte_do_retangulo], "area_km2": [urbano.area / 1e6]}, geometry=[urbano], crs=c.CRS_PADRAO).to_file(arq_area, driver="GPKG", layer="area_urbana")
    info_area = {"retangulo_geografico": {"lat_sul": sul, "lat_norte": norte, "lon_oeste": oeste, "lon_leste": leste, "crs": CRS_GEOGRAFICO}, "limites_no_crs_do_projeto": [x0, y0, x1, y1],
                 "area_km2": urbano.area / 1e6, "area_do_lado_brasileiro_km2": float(dom.sum() * px_km2), "fonte": ARGS.fonte_do_retangulo}

    # ---- cenas: as da tabela do download (modo padrão) e as passagens em fatias
    cenas = pd.read_csv(arq_cenas, dtype={"versao_do_processamento": str}).fillna({"arquivos": "", "versao_do_processamento": ""})
    cenas = cenas[cenas.situacao.isin(["lida", "já existia"])].assign(origem="cena inteira (modo padrão do download)", fatias=1)
    arq_fat = next(iter(sorted(ao.BRUTO.glob("passagens-em-fatias_planetary-computer_*_passagem.csv"))), None)
    b1 = pd.DataFrame()
    if arq_fat is not None:
        b1 = pd.read_csv(arq_fat).fillna({"arquivos": ""})
        f = b1[b1.situacao.isin(["lida", "já existia"])]
        cenas = pd.concat([cenas, pd.DataFrame({"sensor": RADAR, "janela": f.janela, "identificador": f.identificadores, "data_hora_utc": f.data_hora_utc, "data_hora_local": f.data_hora_local,
                                                "orbita_relativa": f.orbita_relativa, "direcao": f.direcao, "arquivos": f.arquivos, "versao_do_processamento": "",
                                                "origem": np.where(f.fatias_lidas > 1, "passagem em fatias, unidas", "passagem em fatias: uma fatia cobre a área urbana"), "fatias": f.fatias_lidas})], ignore_index=True)
    diaria, tel = ao.ler_serie_diaria(), ao.ler_telemetria()
    cenas = pd.concat([cenas.reset_index(drop=True), pd.DataFrame([ao.nivel_na_cena(pd.Timestamp(x), diaria, tel) for x in cenas.data_hora_local])], axis=1)
    cenas["chave"] = cenas.sensor + "_" + pd.to_datetime(cenas.data_hora_local).dt.strftime("%Y%m%dT%H%M")
    cenas = cenas.sort_values("data_hora_utc").drop_duplicates("chave").reset_index(drop=True)
    arquivos = lambda r: {a.split("-")[1].split("_")[0]: ao.BRUTO / r.sensor / a for a in r.arquivos.split("; ")}  # noqa: E731
    minimo_px = int(np.ceil(ARGS.area_minima_ha * 1e4 / (RES * RES)))

    # ---- Parte A e radar em dB: cada cena recortada à grade da área urbana
    radar, optico, cob = {}, {}, {}
    for r in cenas.itertuples():
        if r.sensor == RADAR:
            db, valido = vv_em_db(arquivos(r)["vv"], grade)
            radar[r.chave] = {"db": db, "valido": valido}
        else:
            agua, valido, fundo, info = ao.agua_optico(arquivos(r), grade, float(r.versao_do_processamento or 0) >= 4)
            scl = ao.para_a_grade(arquivos(r)["scl"], grade, Resampling.nearest, 0)
            optico[r.chave] = {"agua": agua, "valido": valido, "fundo": fundo, "nuvem_ou_sombra_pct": float(100 * (np.isin(scl, SCL_NUVEM_OU_SOMBRA) & dentro).sum() / dentro.sum())}
        cob[r.chave] = float(100 * (valido & dentro).sum() / dentro.sum()) if r.sensor == RADAR else float(100 * ((scl != 0) & dentro).sum() / dentro.sum())
    cenas["cobertura_da_area_urbana_pct"] = cenas.chave.map(cob)
    comuns_desc = {"sensor": "sensor e produto", "identificador": "identificador da cena (das fatias, separados por ponto e vírgula)", "janela": "período de busca", "data_hora_utc": "data e hora UTC",
                   "data_hora_local": "data e hora local (UTC−3)", "orbita_relativa": "órbita relativa", "direcao": "direção da órbita", "nivel_regua_cm": "nível da régua no dia: média diária (consistida onde houver; senão a bruta)",
                   "nivel_consistencia": "se o nível é consistido ou bruto", "origem": "de onde vem a janela lida", "fatias": "fatias lidas"}
    a_tab = cenas[["sensor", "identificador", "origem", "fatias", "janela", "data_hora_utc", "data_hora_local", "orbita_relativa", "direcao", "nivel_regua_cm", "nivel_consistencia", "cobertura_da_area_urbana_pct"]]
    gravar(a_tab, "cenas-na-area-urbana_sentinel_2017-2024_cena", "A — Cenas recortadas à área urbana", {**comuns_desc, "cobertura_da_area_urbana_pct": "% da área urbana com dado na cena"},
           casas={"nivel_regua_cm": 1}, area_urbana=info_area)
    if len(b1):
        m_fat = json.loads(arq_fat.with_suffix(".json").read_text(encoding="utf-8"))
        gravar(b1, "passagens-de-radar-em-fatias_sentinel1-rtc_2017-2024_passagem", "B1 — Passagens de radar entregues em fatias", m_fat["campos"],
               casas={"cobertura_do_retangulo_pct": 2, "pixels_com_duas_fatias_pct": 4}, retangulo_lido=m_fat["retangulo_lido"], arquivos_novos=m_fat["arquivos_novos"], bytes_dos_arquivos=m_fat["bytes_dos_arquivos"],
               origem=rel(arq_fat))

    # ---- C1: referência por órbita
    rad = cenas[(cenas.sensor == RADAR) & (cenas.cobertura_da_area_urbana_pct >= ao.COBERTURA_MINIMA) & cenas.nivel_regua_cm.notna()]
    refs, c1 = {}, []
    for orb, g in rad.groupby("orbita_relativa"):
        baixo = g[g.nivel_regua_cm < ARGS.nivel_max_referencia]
        r = (baixo if len(baixo) else g).sort_values("nivel_regua_cm").iloc[0]
        db, valido = radar[r.chave]["db"], radar[r.chave]["valido"]
        agua = ao.tirar_grupos_pequenos(valido & (db <= ARGS.vv_agua_referencia), minimo_px)
        refs[orb] = {"chave": r.chave, "db": db, "valido": valido, "agua": agua, "rio": maior_grupo(agua) & dentro, "nivel": float(r.nivel_regua_cm), "abaixo": bool(len(baixo))}
        c1.append({"orbita_relativa": orb, "direcao": r.direcao, "cenas_da_orbita": len(g), "cena_de_referencia": r.identificador, "data_hora_local": r.data_hora_local, "nivel_regua_cm": float(r.nivel_regua_cm),
                   "abaixo_do_nivel_maximo": bool(len(baixo)), "marca": "" if len(baixo) else f"sem cena abaixo de {ARGS.nivel_max_referencia:g} cm: usada a de menor nível; a mudança fica subestimada",
                   "agua_da_referencia_km2": float((agua & dentro).sum() * px_km2), "leito_principal_km2": float(refs[orb]["rio"].sum() * px_km2)})
        if not len(baixo):
            AVISOS.append(f"órbita {orb}: referência com a régua em {r.nivel_regua_cm:.0f} cm")
    c1 = pd.DataFrame(c1)
    orb_leito = min(refs, key=lambda o: (not refs[o]["abaixo"], refs[o]["nivel"]))
    leito = refs[orb_leito]["agua"]
    c1["e_o_leito_de_referencia_comum"] = c1.orbita_relativa == orb_leito
    gravar(c1, "referencia-por-orbita_sentinel1-rtc_2017-2024_orbita", "C1 — Cena de referência de cada órbita do radar", {
        "orbita_relativa": "órbita relativa", "direcao": "direção", "cenas_da_orbita": "cenas da órbita com a área urbana coberta", "cena_de_referencia": "cena de menor nível da régua",
        "data_hora_local": "data e hora local da referência", "nivel_regua_cm": "nível da régua no dia da referência", "abaixo_do_nivel_maximo": f"a referência está abaixo de {ARGS.nivel_max_referencia:g} cm",
        "marca": "ressalva", "agua_da_referencia_km2": f"água da referência (VV <= {ARGS.vv_agua_referencia:g} dB) na área urbana", "leito_principal_km2": "maior grupo de água da referência, na área urbana",
        "e_o_leito_de_referencia_comum": "a água desta referência é o leito de referência comum (para 'em terra')"}, casas={"nivel_regua_cm": 1})
    arq_ref = PROC / "referencia-por-orbita_sentinel1-rtc_2017-2024_10m.gpkg"
    gpd.GeoDataFrame([{"orbita_relativa": o, "cena": cenas.loc[cenas.chave == v["chave"], "identificador"].iloc[0], "nivel_regua_cm": v["nivel"], "geometry": ao.poligonos(v["agua"], grade)} for o, v in refs.items()],
                     crs=c.CRS_PADRAO).to_file(arq_ref, driver="GPKG", layer="agua_da_referencia")
    gpd.GeoDataFrame([{"orbita_relativa": o, "geometry": ao.poligonos(v["rio"], grade)} for o, v in refs.items()], crs=c.CRS_PADRAO).to_file(arq_ref, driver="GPKG", layer="leito_principal")
    gpd.GeoDataFrame([{"orbita_relativa": orb_leito, "nivel_regua_cm": refs[orb_leito]["nivel"], "geometry": ao.poligonos(leito, grade)}], crs=c.CRS_PADRAO).to_file(arq_ref, driver="GPKG", layer="leito_de_referencia_comum")
    meta(arq_ref, descricao="água da cena de referência de cada órbita, o leito principal de cada uma e o leito de referência comum", camadas=["agua_da_referencia", "leito_principal", "leito_de_referencia_comum"],
         colunas={"orbita_relativa": "órbita relativa", "cena": "cena de referência", "nivel_regua_cm": "nível da régua no dia"}, referencias=c1.to_dict("records"))
    meta(arq_area, descricao="área urbana (domínio da rodada): retângulo em graus convertido para o CRS do projeto", **info_area, leito_de_referencia_comum=f"órbita {orb_leito}, régua em {refs[orb_leito]['nivel']:.0f} cm",
         area_em_terra_do_lado_brasileiro_km2=float((dom & ~leito).sum() * px_km2), colunas={"descricao": "o que é", "fonte": "de onde vêm as coordenadas", "area_km2": "área do polígono"})

    # ---- D1: manchas acumuladas, na área urbana, em terra
    manchas = ec.carregar_cenarios(ee.ARQ_COTAS, ao.CAMADA_COTAS, ao.ATRIBUTO_COTAS)  # geometry = união das manchas de cota <= X (função do repositório)
    geo_m = {int(k): g for k, g in zip(manchas.valor, manchas.geometry)}
    extraidas = {}
    for item in ARGS.mancha_extra or []:  # camada de fora do repositório, extraída de figura: só leitura, só comparação
        cota, resto = item.split("=", 1)
        caminho, _, camada = resto.rpartition(":") if resto.count(":") and not resto.endswith(".gpkg") else (resto, "", None)
        if not Path(caminho).exists():
            AVISOS.append(f"mancha de {cota} cm ausente: comparação feita sem ela")
            continue
        g = gpd.read_file(caminho, layer=camada).to_crs(c.CRS_PADRAO)
        geo_m[int(cota)] = g.geometry.buffer(0).union_all()
        extraidas[int(cota)] = f"{int(cota)} cm: extraída de figura, não conferida"
    cotas = sorted(geo_m)
    mancha_px = {k: features.rasterize([(geo_m[k], 1)], out_shape=forma, transform=grade["transform"], fill=0, dtype="uint8").astype(bool) for k in cotas}
    sgb = pd.DataFrame([{"cota_cm": k, "mancha_na_area_urbana_km2": float((mancha_px[k] & dom).sum() * px_km2), "mancha_em_terra_km2": float((mancha_px[k] & dom & ~leito).sum() * px_km2),
                         "extraida_de_figura": k in extraidas, "marca": extraidas.get(k, "")} for k in cotas])
    nota_extra = ("; ".join(extraidas.values()) + ".") if extraidas else ""

    # ---- C2 a C5 e D2: cada cena
    c3, d2, geo, guardadas = [], [], {RADAR: {k: [] for k in CAMADAS}, OPTICO: {k: [] for k in CAMADAS}}, {}
    for r in cenas.itertuples():
        e_radar = r.sensor == RADAR
        linha = {"sensor": r.sensor, "identificador": r.identificador, "janela": r.janela, "data_hora_local": r.data_hora_local, "orbita_relativa": r.orbita_relativa if e_radar else np.nan,
                 "nivel_regua_cm": r.nivel_regua_cm, "cobertura_da_area_urbana_pct": r.cobertura_da_area_urbana_pct, "fracao_do_leito_principal_marcada_como_agua": np.nan, "nuvem_ou_sombra_pct": np.nan,
                 "e_a_referencia_da_orbita": False, "boa": False, "motivo": ""}
        sens = {}
        if r.cobertura_da_area_urbana_pct < ao.COBERTURA_MINIMA or pd.isna(r.nivel_regua_cm):
            linha["motivo"] = "área urbana sem cobertura completa" if r.cobertura_da_area_urbana_pct < ao.COBERTURA_MINIMA else "sem nível da régua no dia"
            c3.append(linha)
            continue
        if e_radar:
            ref, x = refs[r.orbita_relativa], radar[r.chave]
            agua = agua_por_mudanca(x["db"], x["valido"], ref, ARGS.mudanca, minimo_px)
            valido, fundo = x["valido"] & ref["valido"], x["db"]
            fr = float((agua & ref["rio"]).sum() / ref["rio"].sum()) if ref["rio"].sum() else np.nan
            linha.update(fracao_do_leito_principal_marcada_como_agua=fr, e_a_referencia_da_orbita=r.chave == ref["chave"], boa=bool(fr >= ARGS.fracao_minima_do_rio),
                         motivo="" if fr >= ARGS.fracao_minima_do_rio else "rio sem contraste")
            for mud in ARGS.sensibilidade:
                a_s = agua_por_mudanca(x["db"], x["valido"], ref, mud, minimo_px)
                sens[f"inundacao_em_terra_com_{abs(mud):g}db_km2"] = float((ao.ligada_ao(a_s, leito) & ~leito & dom).sum() * px_km2)
        else:
            x = optico[r.chave]
            agua, valido, fundo = ao.tirar_grupos_pequenos(x["agua"], minimo_px), x["valido"], x["fundo"]
            linha.update(nuvem_ou_sombra_pct=x["nuvem_ou_sombra_pct"], boa=bool(x["nuvem_ou_sombra_pct"] <= ARGS.nuvem_max),
                         motivo="" if x["nuvem_ou_sombra_pct"] <= ARGS.nuvem_max else f"nuvem ou sombra acima de {ARGS.nuvem_max:g} %")
        ligada = ao.ligada_ao(agua, leito)
        terra = ligada & ~leito
        cam = {"agua_total": agua, "agua_ligada_ao_rio": ligada, "inundacao_em_terra": terra}
        dominio = dom & valido
        linha.update({f"{k}_km2": float((v & dominio).sum() * px_km2) for k, v in cam.items()}, **sens)
        c3.append(linha)
        if not linha["boa"]:
            continue
        guardadas[r.chave] = {"fundo": fundo, "ligada": ligada, "terra": terra & dominio, "radar": e_radar, "dominio": dominio}
        for k in cotas:
            d2.append({"sensor": r.sensor, "identificador": r.identificador, "chave": r.chave, "janela": r.janela, "data_hora_local": r.data_hora_local, "nivel_regua_cm": r.nivel_regua_cm,
                       "cota_cm": k, **comparar(terra, mancha_px[k] & ~leito, dominio, px_km2), "marca_da_mancha": extraidas.get(k, "")})
        for k, v in cam.items():
            hora = pd.Timestamp(r.data_hora_local)
            geo[r.sensor][k].append({"identificador": r.identificador, "data": f"{hora:%Y-%m-%d}", "hora_local": f"{hora:%H:%M}", "nivel_regua_cm": r.nivel_regua_cm, "orbita_relativa": r.orbita_relativa if e_radar else None,
                                     "limiar": f"mudança <= {ARGS.mudanca:g} dB e VV <= {ARGS.vv_max:g} dB, ou água na referência e VV <= {ARGS.vv_max:g} dB" if e_radar else "MNDWI > 0",
                                     "controle_de_qualidade": f"leito principal marcado como água: {100 * linha['fracao_do_leito_principal_marcada_como_agua']:.1f} %" if e_radar else f"nuvem ou sombra: {linha['nuvem_ou_sombra_pct']:.1f} %",
                                     "area_km2": float((v & dominio).sum() * px_km2), "geometry": ao.poligonos(v & dentro, grade)})
    c3, d2 = pd.DataFrame(c3), pd.DataFrame(d2)
    desc_sens = {f"inundacao_em_terra_com_{abs(m):g}db_km2": f"inundação em terra com o limiar de mudança de {m:g} dB (sensibilidade)" for m in ARGS.sensibilidade}
    c3 = c3[[x for x in c3.columns if x not in desc_sens] + [x for x in desc_sens if x in c3]]
    desc_c3 = {**comuns_desc, "cobertura_da_area_urbana_pct": "% da área urbana com dado", "fracao_do_leito_principal_marcada_como_agua": "radar: fração do leito principal da referência que a cena marca como água",
               "nuvem_ou_sombra_pct": "óptico: % da área urbana com nuvem ou sombra (SCL)", "e_a_referencia_da_orbita": "a cena é a referência da sua órbita", "boa": "entra nas comparações",
               "motivo": "por que ficou fora", "agua_total_km2": "água da cena na área urbana, lado brasileiro", "agua_ligada_ao_rio_km2": "água em grupos que tocam o leito de referência",
               "inundacao_em_terra_km2": f"água ligada ao rio fora do leito de referência (radar: mudança de {ARGS.mudanca:g} dB)", **desc_sens}
    gravar(c3, "controle-de-qualidade_sentinel_2017-2024_cena", "C3 — Controle de qualidade e áreas por cena", desc_c3, casas={"nivel_regua_cm": 1, "fracao_do_leito_principal_marcada_como_agua": 3},
           boas_por_sensor_e_ano=c3[c3.boa].groupby(["sensor", c3.data_hora_local.str[:4]]).size().rename("n").reset_index().to_dict("records"), referencias=c1.to_dict("records"))

    # ---- GeoPackages das cenas boas
    col_geo = {"identificador": "cena", "data": "data local", "hora_local": "hora local (UTC−3)", "nivel_regua_cm": "nível da régua no dia", "orbita_relativa": "órbita relativa (radar)", "limiar": "regra da água",
               "controle_de_qualidade": "resultado do controle de qualidade", "area_km2": "área da camada na área urbana, lado brasileiro"}
    for sensor, cams in geo.items():
        if not cams[CAMADAS[0]]:
            continue
        arq = PROC / f"agua-observada-area-urbana_{sensor}_2017-2024_10m.gpkg"
        for k, linhas in cams.items():
            g = gpd.GeoDataFrame(linhas, crs=c.CRS_PADRAO)
            g[g.geometry.notna()].to_file(arq, driver="GPKG", layer=k)
        meta(arq, descricao="água de cada cena boa na área urbana: três camadas, uma linha por cena; polígonos de pixel, sem simplificação", camadas=list(CAMADAS), cenas=len(cams[CAMADAS[0]]),
             colunas=col_geo, aviso="produto derivado, para conferência no mapa; não vai ao geoportal")

    # ---- D2, D3, D4, D5
    col_d2 = {"sensor": "sensor e produto", "identificador": "cena", "chave": "sensor e data-hora local, para ligar às figuras", "janela": "período de busca", "data_hora_local": "data e hora local (UTC−3)",
              "nivel_regua_cm": comuns_desc["nivel_regua_cm"], "cota_cm": "cota da mancha acumulada", "inundacao_em_terra_km2": "inundação em terra da cena (área urbana, lado brasileiro, onde a cena tem dado)",
              "mancha_em_terra_km2": "mancha fora do leito de referência (mesmo domínio)", "intersecao_km2": "área comum", "uniao_km2": "área de uma ou de outra", "intersecao_sobre_uniao": "interseção / união",
              "fracao_da_agua_dentro_da_mancha": "quanto da inundação da cena cai dentro da mancha", "fracao_da_mancha_coberta_pela_agua": "quanto da mancha a cena cobre",
              "marca_da_mancha": "ressalva sobre a mancha"}
    casas_d = {"nivel_regua_cm": 1, "intersecao_sobre_uniao": 3, "fracao_da_agua_dentro_da_mancha": 3, "fracao_da_mancha_coberta_pela_agua": 3}
    gravar(d2, "agua-x-manchas-area-urbana_sentinel-sgb_2017-2024_cena-cota", "D2 — Inundação em terra de cada cena boa × cada mancha acumulada, na área urbana", col_d2, casas=casas_d, nota=nota_extra, manchas=sgb.to_dict("records"))
    melhor = d2.loc[d2.groupby("chave").intersecao_sobre_uniao.idxmax().dropna()].sort_values("nivel_regua_cm").reset_index(drop=True)
    d3 = melhor.rename(columns={"cota_cm": "cota_de_maior_intersecao_sobre_uniao_cm"})
    gravar(d3, "resumo-cota-de-maior-sobreposicao-area-urbana_sentinel-sgb_2017-2024_cena", "D3 — Cota de maior interseção/união em cada cena boa, por ordem do nível da régua",
           {**col_d2, "cota_de_maior_intersecao_sobre_uniao_cm": "cota da mancha com a maior razão interseção/união para a cena"}, casas=casas_d, nota=nota_extra)
    d4 = d2.sort_values("intersecao_sobre_uniao", ascending=False).groupby("cota_cm").head(3).sort_values(["cota_cm", "intersecao_sobre_uniao"], ascending=[True, False]).copy()
    d4.insert(1, "posicao", d4.groupby("cota_cm").cumcount() + 1)
    d4["em_destaque"] = d4.data_hora_local.str[:7].isin(ARGS.meses_destaque)
    d4 = d4[["cota_cm", "posicao", "sensor", "data_hora_local", "nivel_regua_cm", "intersecao_sobre_uniao", "fracao_da_agua_dentro_da_mancha", "fracao_da_mancha_coberta_pela_agua", "inundacao_em_terra_km2",
             "mancha_em_terra_km2", "em_destaque", "marca_da_mancha", "identificador"]]
    gravar(d4, "tres-cenas-de-maior-sobreposicao-por-cota_sentinel-sgb_2017-2024_cota", "D4 — As três cenas de maior interseção/união para cada cota", {
        **col_d2, "posicao": "ordem pela interseção/união", "em_destaque": f"cena de {', '.join(ARGS.meses_destaque)}"}, casas=casas_d, nota=nota_extra)
    d5 = pd.DataFrame()
    if ARQ_AREA_ATINGIDA.exists():
        cod = str(limite["codarea"].iloc[0]) if "codarea" in limite else ""
        g = gpd.read_file(ARQ_AREA_ATINGIDA).to_crs(c.CRS_PADRAO)  # a fonte vem em coordenadas geográficas
        g = g[g["cd_mun"].astype(str).str.startswith(cod)] if cod and "cd_mun" in g else g
        atingida = features.rasterize([(g.geometry.buffer(0).union_all(), 1)], out_shape=forma, transform=grade["transform"], fill=0, dtype="uint8").astype(bool)
        linhas = []
        for r in cenas[cenas.chave.isin(guardadas) & cenas.data_hora_local.str.startswith(ARGS.mes_da_area_atingida)].itertuples():
            v = guardadas[r.chave]
            linhas.append({"sensor": r.sensor, "identificador": r.identificador, "data_hora_local": r.data_hora_local, "nivel_regua_cm": r.nivel_regua_cm,
                           **{k.replace("mancha", "area_atingida"): x for k, x in comparar(v["terra"], atingida & ~leito, v["dominio"], px_km2).items()}})
        d5 = pd.DataFrame(linhas)
        if len(d5):
            gravar(d5, "agua-x-area-atingida-area-urbana_sentinel-fepam_2024-05_cena", f"D5 — Cenas boas de {ARGS.mes_da_area_atingida} × área diretamente atingida, na área urbana", {
                **{k: v for k, v in col_d2.items()}, "area_atingida_em_terra_km2": "área diretamente atingida fora do leito de referência (mesmo domínio)",
                "fracao_da_agua_dentro_da_area_atingida": "quanto da inundação da cena cai dentro da área atingida", "fracao_da_area_atingida_coberta_pela_agua": "quanto da área atingida a cena cobre"},
                casas={"nivel_regua_cm": 1, "intersecao_sobre_uniao": 3, "fracao_da_agua_dentro_da_area_atingida": 3, "fracao_da_area_atingida_coberta_pela_agua": 3}, camada=rel(ARQ_AREA_ATINGIDA))

    # ---- figuras
    if ARGS.figuras:
        boas = c3[c3.boa].merge(cenas[["identificador", "sensor", "chave"]], on=["identificador", "sensor"])
        for r in boas.itertuples():
            v, hora = guardadas[r.chave], pd.Timestamp(r.data_hora_local)
            contornos = {k: geo_m[k] for k in (952, 1252) if k in geo_m} | ({1205: geo_m[1205]} if r.nivel_regua_cm > 1000 and 1205 in geo_m else {})
            figura_cena(ARGS.figuras / f"agua-na-area-urbana_{r.chave}_10m.png", v["fundo"], v["radar"], v["ligada"], leito, dict(sorted(contornos.items())), urbano, ext,
                        f"{'Sentinel-1 (radar, VV)' if v['radar'] else 'Sentinel-2 (óptico)'} — {hora:%d/%m/%Y}, {hora:%Hh%M} (hora local) — régua: {ao.fmt(float(r.nivel_regua_cm), 0)} cm")
        figura_resumo(ARGS.figuras / "resumo-nivel-x-inundacao-em-terra-area-urbana_sentinel-sgb.png", boas, sgb)
        pares = []
        alvo = boas[(boas.sensor == RADAR) & boas.data_hora_local.str.startswith(ARGS.cena_lado_a_lado)]
        if len(alvo) and max(cotas) in geo_m:
            pares.append((alvo.iloc[0], max(k for k in cotas if k not in extraidas)))
        if 952 in geo_m and len(boas):
            pares.append((boas.loc[(boas.nivel_regua_cm - 952).abs().idxmin()], 952))
        for r, cota in pares:
            v, hora = guardadas[r.chave], pd.Timestamp(r.data_hora_local)
            figura_lado_a_lado(ARGS.figuras / f"lado-a-lado_{r.chave}_x_mancha-{cota}cm.png", v["fundo"], v["radar"], v["terra"], mancha_px[cota] & ~leito & v["dominio"], leito, urbano, ext,
                               f"{'Sentinel-1 (radar)' if v['radar'] else 'Sentinel-2 (óptico)'} de {hora:%d/%m/%Y}, {hora:%Hh%M}, régua em {ao.fmt(float(r.nivel_regua_cm), 0)} cm × mancha do SGB até {cota} cm", cota,
                               "Fundo: VV em dB." if v["radar"] else "Fundo: falsa cor (B11, B08, B03).")
        (ARGS.figuras / "figuras_area-urbana.json").write_text(json.dumps({"script": SCRIPT, "motivo": MOTIVO, "fontes": FONTES, "licencas": LICENCAS, "figuras_por_cena": int(len(boas)), "limitacoes": LIMITACOES,
                                                                              "fundo_do_optico": "falsa cor: vermelho B11, verde B08, azul B03", "lado_a_lado": [f"{r.chave} × {k} cm" for r, k in pares],
                                                                              "manchas": sgb.to_dict("records"), "area_urbana": info_area}, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    if ARGS.copia:
        ARGS.copia.mkdir(parents=True, exist_ok=True)
        for arq in sorted(PROC.iterdir()):
            shutil.copy2(arq, ARGS.copia / arq.name)
    print(json.dumps({"area_urbana": info_area, "cenas": int(len(cenas)), "boas": c3[c3.boa].groupby("sensor").size().to_dict(), "referencias": c1.to_dict("records"), "manchas": sgb.to_dict("records"), "avisos": AVISOS},
                     ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", force=True)
    _p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    _p.add_argument("--retangulo-geografico", type=float, nargs=4, required=True, metavar=("LAT_SUL", "LAT_NORTE", "LON_OESTE", "LON_LESTE"), help="área urbana, em graus decimais (SIRGAS 2000)")
    _p.add_argument("--fonte-do-retangulo", required=True, help="de onde vêm as coordenadas (documento e página)")
    _p.add_argument("--mancha-extra", nargs="*", help="COTA=ARQUIVO.gpkg:CAMADA de mancha acumulada de fora do repositório (extraída de figura, não conferida): só leitura")
    _p.add_argument("--mediana", type=int, default=5, help="lado da janela da mediana do radar (pixels)")
    _p.add_argument("--mudanca", type=float, default=-3.0, help="mudança de VV em relação à referência que marca água (dB)")
    _p.add_argument("--sensibilidade", type=float, nargs="*", default=[-2.0, -4.0], help="outros limiares de mudança, só para a tabela de sensibilidade (dB)")
    _p.add_argument("--vv-max", type=float, default=-15.0, help="VV máximo de um pixel de água na cena (dB)")
    _p.add_argument("--vv-agua-referencia", type=float, default=-18.0, help="VV máximo de um pixel de água na referência (dB)")
    _p.add_argument("--nivel-max-referencia", type=float, default=300.0, help="a referência de cada órbita é a cena de menor nível abaixo disto (cm)")
    _p.add_argument("--fracao-minima-do-rio", type=float, default=0.90, help="fração do leito principal que a cena de radar tem de marcar como água")
    _p.add_argument("--nuvem-max", type=float, default=10.0, help="óptico: máximo de nuvem ou sombra na área urbana (%%)")
    _p.add_argument("--area-minima-ha", type=float, default=0.5, help="grupos de água menores que isto são retirados (ha)")
    _p.add_argument("--meses-destaque", nargs="*", default=[], help="meses (AAAA-MM) destacados na tabela das três melhores cenas por cota")
    _p.add_argument("--mes-da-area-atingida", default="2024-05", help="mês das cenas comparadas com a área diretamente atingida")
    _p.add_argument("--cena-lado-a-lado", default="2017-06-14", help="data (AAAA-MM-DD) da cena de radar da figura lado a lado com a maior mancha")
    _p.add_argument("--figuras", type=Path, help="pasta (fora do repositório) das figuras de conferência; sem ela, não há figura")
    _p.add_argument("--copia", type=Path, help="pasta que recebe uma cópia dos produtos")
    _p.add_argument("--refazer", action="store_true", help="gera de novo mesmo se a pasta de saída já tiver arquivos")
    ARGS = _p.parse_args()
    FONTES, LICENCAS, AVISOS = [], {}, []
    for _pasta in (ARGS.figuras, ARGS.copia):
        if _pasta is not None and c.RAIZ in _pasta.resolve().parents:
            raise SystemExit("--figuras e --copia têm de ficar fora do repositório")
    main()
