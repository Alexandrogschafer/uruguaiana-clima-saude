"""
Imagens de satélite das cheias recortadas na área urbana, para conferência visual
em SIG (por baixo da água classificada pelos roteiros agua_observada_sentinel_*).

Nada é reclassificado: o roteiro só recorta, reprojeta para o CRS do projeto e
estica para visualização. Domínio: o retângulo da área urbana gravado por
agua_observada_sentinel_area_urbana.py, com --margem-m de margem, na mesma grade
de 10 m (as margens são múltiplos do pixel).

  radar   VV em dB (float32), sem a mediana usada na classificação, e a diferença
          VV(cena) − VV(referência da mesma órbita), em dB; a referência de cada
          órbita é a daquele roteiro e sai só com o VV em dB;
  óptico  falsa cor B11, B08, B03 (vermelho, verde, azul) e, se as bandas B02 e
          B04 tiverem sido lidas (scripts/download/sentinel_planetary_computer.py
          --bandas-extras B02 B04), cor natural B04, B03, B02; uint8, 0 = sem dado;
          cortes fixos, iguais para todas as cenas: os percentis --percentis,
          banda a banda, da cena de referência (a cena boa de menor nível da
          régua com nuvem ou sombra abaixo de --nuvem-max-referencia %) ou, nas
          composições de --cortes-da-uniao, da união dos pixels válidos de todas
          as cenas ópticas boas.

GeoTIFF com compressão DEFLATE, blocos internos e pirâmides internas; .json irmão.
As imagens vão só para --saida e as figuras de controle para --figuras, as duas
fora do repositório. Só cenas boas daquele roteiro; data pedida que não for cena
boa do sensor é avisada e fica de fora, sem substituição. Com --todas-as-boas
entram todas. A referência de rio baixo leva "_referencia" no nome; a diferença
só existe para órbita com referência de rio baixo.

Imagem que já existe na pasta fica como está, salvo os tipos de --regravar. No
fim, o índice indice_imagens_sentinel.csv (uma linha por imagem, com a régua, a
fase da cheia e a água em terra da cena) é gravado de novo.

Uso:
  python scripts/processamento/imagens_sentinel_para_sig.py --saida PASTA \
      --radar AAAA-MM-DD [...] --optico AAAA-MM-DD [...] [--figuras PASTA] [--sobrepor sensor:AAAA-MM-DD ...]
  python scripts/processamento/imagens_sentinel_para_sig.py --saida PASTA --todas-as-boas \
      [--regravar falsacor] [--cortes-da-uniao falsacor] [--antes PASTA] [--figuras PASTA]
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import geopandas as gpd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import rasterio  # noqa: E402
from rasterio import features  # noqa: E402
from rasterio.enums import Resampling as Reamostragem  # noqa: E402
from rasterio.transform import from_origin  # noqa: E402
from rasterio.warp import Resampling  # noqa: E402

import agua_observada_sentinel as ao  # noqa: E402
import agua_observada_sentinel_area_urbana as au  # noqa: E402
import dinamica_populacional_comum as c  # noqa: E402

logger = logging.getLogger(__name__)

SCRIPT = "scripts/processamento/imagens_sentinel_para_sig.py"
MOTIVO = "imagens de satélite das cheias recortadas na área urbana, para conferência visual em SIG"
PROC44 = au.PROC
PROC45 = c.RAIZ / "data" / "processed" / "agua_observada_sentinel_comparacao"
RES, RADAR, OPTICO = au.RES, au.RADAR, au.OPTICO
SEM_DADO_RADAR = -9999.0
ATRIBUICAO = "contém dados Copernicus Sentinel modificados; uso livre com atribuição"
COMPOSICOES = {"falsacor-b11b08b03": ("b11", "b08", "b03"), "cornatural-b04b03b02": ("b04", "b03", "b02")}
FAIXA_DB, FAIXA_DIFERENCA = (-25.0, 0.0), (-10.0, 10.0)  # só para as miniaturas de controle


# ---------------------------------------------------------------- leitura
def tabela_de_arquivos() -> dict:
    """(sensor, identificador) -> arquivos e dados da cena, pela tabela de cenas e pela das passagens em fatias."""
    bruto = pd.read_csv(next(iter(sorted(ao.BRUTO.glob("cenas_planetary-computer_*_cena.csv")))), dtype=str)
    bruto = bruto[bruto.situacao.isin(["lida", "já existia"])]
    out = {(r.sensor, r.identificador): {"arquivos": r.arquivos, "versao": r.versao_do_processamento, "utc": r.data_hora_utc, "orbita": r.orbita_relativa, "direcao": r.direcao} for r in bruto.itertuples()}
    arq_fat = next(iter(sorted(ao.BRUTO.glob("passagens-em-fatias_planetary-computer_*_passagem.csv"))), None)
    if arq_fat is not None:
        fat = pd.read_csv(arq_fat, dtype=str)
        for r in fat[fat.situacao.isin(["lida", "já existia"])].itertuples():
            out.setdefault((RADAR, r.identificadores), {"arquivos": r.arquivos, "versao": "", "utc": r.data_hora_utc, "orbita": r.orbita_relativa, "direcao": r.direcao})
    return out


def caminhos(sensor: str, info: dict) -> dict:
    return {a.split("-")[1].split("_")[0]: ao.BRUTO / sensor / a for a in info["arquivos"].split("; ")}


def refletancia(arq: Path, grade: dict, com_deslocamento: bool, metodo: Resampling) -> np.ndarray:
    """Banda óptica -> grade de trabalho, em refletância (valor / 10000, tirado o deslocamento de 1000 das versões 04.00 em diante)."""
    v = ao.para_a_grade(arq, grade, metodo, np.nan)
    v = np.where(v > 0, v, np.nan)  # 0 é o sem dado da cena
    return (np.clip(v - 1000, 0, None) if com_deslocamento else v) / 10000


def niveis(quando_local: pd.Timestamp, diaria, tel) -> dict:
    n = ao.nivel_na_cena(quando_local, diaria, tel)
    h = quando_local.hour + quando_local.minute / 60
    if pd.notna(n["telemetria_mais_proxima_cm"]):
        perto, de_onde = n["telemetria_mais_proxima_cm"], f"telemetria das {n['telemetria_hora_da_leitura'][-5:]}"
    else:
        hora, perto = min(((7, n["leitura_07h_cm"]), (17, n["leitura_17h_cm"])), key=lambda x: (pd.isna(x[1]), abs(x[0] - h)))
        de_onde = f"leitura das {hora:02d}h" if pd.notna(perto) else ""
    return {"nivel_media_diaria_cm": n["nivel_regua_cm"], "nivel_consistencia": n["nivel_consistencia"], "nivel_mais_proximo_da_hora_cm": perto, "origem_do_nivel_mais_proximo": de_onde}


# ---------------------------------------------------------------- gravação
def gravar_tif(arq: Path, dados: np.ndarray, grade: dict, sem_dado, **meta) -> None:
    """GeoTIFF com DEFLATE, blocos internos de 256 e pirâmides internas (2, 4, 8) + .json irmão."""
    dados = dados if dados.ndim == 3 else dados[None]
    perfil = {"driver": "GTiff", "dtype": dados.dtype.name, "count": dados.shape[0], "crs": c.CRS_PADRAO, "transform": grade["transform"], "width": grade["width"], "height": grade["height"],
              "nodata": sem_dado, "compress": "deflate", "tiled": True, "blockxsize": 256, "blockysize": 256, "predictor": 3 if dados.dtype.kind == "f" else 2}
    if dados.shape[0] == 3:
        perfil["photometric"] = "RGB"
    with rasterio.Env(COMPRESS_OVERVIEW="DEFLATE"):
        with rasterio.open(arq, "w", **perfil) as dst:
            dst.write(dados)
        with rasterio.open(arq, "r+") as dst:
            dst.build_overviews(ARGS.piramides, Reamostragem.average)
    c.gravar_meta(arq, crs=c.CRS_PADRAO, resolucao_m=RES, status=c.STATUS_CONFERENCIA, ligado_ao_portal=False, script=SCRIPT, motivo=MOTIVO, atribuicao=ATRIBUICAO, sem_dado=sem_dado,
                  formato="GeoTIFF, compressão DEFLATE, blocos internos de 256, pirâmides internas " + ", ".join(str(x) for x in ARGS.piramides),
                  transformacao="recorte na área urbana com margem, reprojeção para o CRS do projeto na grade de 10 m e esticamento para visualização; nada é reclassificado",
                  argumentos={k: v for k, v in vars(ARGS).items() if k not in ("saida", "figuras")}, **meta)
    logger.info("Imagem: %s", arq.name)


def para_uint8(bandas: list[np.ndarray], cortes: list[tuple[float, float]]) -> np.ndarray:
    """Esticamento linear entre os cortes, de 1 a 255; 0 fica para sem dado."""
    valido = np.all([np.isfinite(b) for b in bandas], axis=0)
    out = np.zeros((len(bandas),) + bandas[0].shape, dtype="uint8")
    for i, (b, (lo, hi)) in enumerate(zip(bandas, cortes)):
        out[i][valido] = (1 + np.round(254 * np.clip((b[valido] - lo) / (hi - lo), 0, 1))).astype("uint8")
    return out


# ---------------------------------------------------------------- conferência
def conferir(arqs: list[Path], grade: dict) -> pd.DataFrame:
    linhas = []
    for arq in arqs:
        with rasterio.open(arq) as src:
            dados = src.read(1, masked=True)
            linhas.append({"arquivo": arq.name, "crs": str(src.crs), "pixel_m": src.res[0], "largura": src.width, "altura": src.height, "mesma_grade": bool(src.transform == grade["transform"] and src.shape == (grade["height"], grade["width"])),
                           "bandas": src.count, "tipo_do_dado": src.dtypes[0], "sem_dado": src.nodata, "piramides": "/".join(str(x) for x in src.overviews(1)), "compressao": src.compression.value if src.compression else "",
                           "bloco": "×".join(str(x) for x in src.block_shapes[0]), "com_dado_pct": float(100 * (~np.ma.getmaskarray(dados)).mean()), "tamanho_mb": arq.stat().st_size / 1e6})
    return pd.DataFrame(linhas)


def miniatura(ax, arq: Path, ext) -> None:
    with rasterio.open(arq) as src:
        d = src.read(masked=True)
    if d.shape[0] == 3:
        rgba = np.dstack([*(d[i].filled(0) for i in range(3)), np.where(np.ma.getmaskarray(d[0]), 0, 255)]).astype("uint8")
        ax.imshow(rgba, extent=ext, interpolation="nearest")
    elif "diferenca" in arq.name:
        ax.imshow(d[0], cmap="RdBu", vmin=FAIXA_DIFERENCA[0], vmax=FAIXA_DIFERENCA[1], extent=ext, interpolation="nearest")
    else:
        ax.imshow(d[0], cmap="gray", vmin=FAIXA_DB[0], vmax=FAIXA_DB[1], extent=ext, interpolation="nearest")
    ax.set_xticks([]); ax.set_yticks([])  # noqa: E702
    ax.set_facecolor("#ff00ff")  # sem dado aparece em magenta


def figura_de_controle(arq: Path, itens: list[dict], ext, titulo: str = "Imagens recortadas na área urbana (controle)") -> None:
    colunas = 6
    linhas = int(np.ceil(len(itens) / colunas))
    fig, eixos = plt.subplots(linhas, colunas, figsize=(3.6 * colunas, 3.6 * linhas), squeeze=False)
    for ax in eixos.ravel():
        ax.axis("off")
    for ax, it in zip(eixos.ravel(), itens):
        ax.axis("on")
        miniatura(ax, it["arquivo"], ext)
        ax.set_title(f"{it['data']} — {it['tipo']}\n{it['sensor']} — régua {ao.fmt(float(it['nivel']), 0)} cm", fontsize=8, loc="left")
    fig.suptitle(titulo + ". Radar em dB de −25 (preto) a 0 (branco); diferença de −10 dB (vermelho) a +10 dB (azul); magenta = sem dado.", fontsize=9.5, x=0.01, ha="left")
    fig.tight_layout(rect=(0, 0, 1, 0.96), h_pad=2.2)
    fig.savefig(arq, dpi=150)
    plt.close(fig)


def figura_sobreposta(arq: Path, imagem: Path, agua_geom, urbano, ext, titulo: str) -> None:
    fig, ax = plt.subplots(figsize=(11, 8.6))
    miniatura(ax, imagem, ext)
    gpd.GeoSeries([agua_geom], crs=c.CRS_PADRAO).boundary.plot(ax=ax, color="#ffd400", linewidth=0.6)
    gpd.GeoSeries([urbano], crs=c.CRS_PADRAO).boundary.plot(ax=ax, color="white", linewidth=0.8, linestyle=(0, (4, 3)))
    ax.set_xlim(ext[0], ext[1]); ax.set_ylim(ext[2], ext[3])  # noqa: E702
    ax.set_title(titulo, fontsize=9.5, loc="left")
    fig.text(0.02, 0.012, "Contorno amarelo: água em terra (inundacao_em_terra) da mesma cena. Tracejado: área urbana. Produto derivado, para conferência.", fontsize=7, color="#555555")
    fig.subplots_adjust(left=0.02, right=0.98, top=0.95, bottom=0.04)
    fig.savefig(arq, dpi=150)
    plt.close(fig)


def figura_antes_e_depois(arq: Path, pares: list[tuple[Path, Path, str]], ext) -> None:
    fig, eixos = plt.subplots(2, len(pares), figsize=(4.4 * len(pares), 7.6), squeeze=False)
    for k, (antes, depois, rotulo) in enumerate(pares):
        for ax, a, quando in ((eixos[0][k], antes, "antes"), (eixos[1][k], depois, "depois")):
            miniatura(ax, a, ext)
            ax.set_title(f"{rotulo} — {quando}", fontsize=8.5, loc="left")
    fig.suptitle("Falsa cor B11-B08-B03 regravada: cortes da cena de referência (antes) e da união das cenas ópticas boas (depois)", fontsize=10, x=0.01, ha="left")
    fig.tight_layout(rect=(0, 0, 1, 0.96), h_pad=3.0)
    fig.savefig(arq, dpi=150)
    plt.close(fig)


def pct_no_maximo(arq: Path, banda: int) -> float:
    with rasterio.open(arq) as src:
        v = src.read(banda)
    return float(100 * (v[v > 0] == 255).mean())


# ---------------------------------------------------------------- principal
def main() -> None:
    ARGS.saida.mkdir(parents=True, exist_ok=True)
    m_cenas = json.loads(next(iter(sorted(ao.BRUTO.glob("cenas_planetary-computer_*_cena.json")))).read_text(encoding="utf-8"))
    licencas = {s: {"colecao": v.get("colecao"), "licenca": v.get("licenca"), "endereco_da_licenca": v.get("endereco_da_licenca")} for s, v in m_cenas["colecoes"].items()}

    # ---- grade: o retângulo da área urbana (já no CRS do projeto), mais a margem, alinhada à grade de 10 m daquele roteiro
    urbano = gpd.read_file(PROC44 / "area-urbana_sgb_2023_retangulo.gpkg", layer="area_urbana").to_crs(c.CRS_PADRAO).geometry.iloc[0]
    x0, y0, x1, y1 = urbano.bounds
    borda = int(round(ARGS.margem_m / RES))
    gx0, gy1 = np.floor(x0 / RES) * RES - borda * RES, np.ceil(y1 / RES) * RES + borda * RES
    grade = {"transform": from_origin(gx0, gy1, RES, RES), "width": int(np.ceil((x1 - gx0) / RES)) + borda, "height": int(np.ceil((gy1 - y0) / RES)) + borda}
    ext = (gx0, gx0 + grade["width"] * RES, gy1 - grade["height"] * RES, gy1)
    info_grade = {"limites": [ext[0], ext[2], ext[1], ext[3]], "largura": grade["width"], "altura": grade["height"], "margem_m": ARGS.margem_m}

    # ---- cenas boas: todas (--todas-as-boas) ou as das datas pedidas
    cq = pd.read_csv(PROC44 / "controle-de-qualidade_sentinel_2017-2024_cena.csv")
    boas = cq[cq.boa].assign(data=lambda t: t.data_hora_local.str[:10]).sort_values("data_hora_local").reset_index(drop=True)
    repetidas = set(map(tuple, boas[boas.duplicated(["sensor", "data"], keep=False)][["sensor", "data"]].to_numpy()))  # duas cenas do sensor no mesmo dia: a órbita entra no nome
    arqs, diaria, tel = tabela_de_arquivos(), ao.ler_serie_diaria(), ao.ler_telemetria()
    refs = pd.read_csv(PROC44 / "referencia-por-orbita_sentinel1-rtc_2017-2024_orbita.csv")
    arq_fase = next(iter(sorted(PROC45.glob("fase-da-cheia-por-cena_*_cena.csv"))), None)  # a fase já calculada pela curva nível × área
    fases = {} if arq_fase is None else {(r.sensor, r.identificador): r.fase for r in pd.read_csv(arq_fase).itertuples()}
    if arq_fase is None:
        AVISOS.append("tabela da fase da cheia ausente: a coluna fase do índice fica vazia")

    def cena(r, papel: str = "cena") -> dict:
        info = arqs[(r.sensor, r.identificador)]
        orbita = int(float(info["orbita"]))
        return {"sensor": r.sensor, "papel": papel, "data": r.data, "nome": r.data + (f"_o{orbita:03d}" if (r.sensor, r.data) in repetidas else ""), "identificador": r.identificador, "data_hora_utc": info["utc"],
                "data_hora_local": r.data_hora_local, "orbita_relativa": orbita, "direcao": info["direcao"], **niveis(pd.Timestamp(r.data_hora_local), diaria, tel), "nuvem_ou_sombra_pct": r.nuvem_ou_sombra_pct,
                "janela": r.janela, "agua_em_terra_km2": r.inundacao_em_terra_km2, "fase": fases.get((r.sensor, r.identificador), ""), "versao": info["versao"], "caminhos": caminhos(sensor=r.sensor, info=info),
                "arquivos_de_origem": [str(p.relative_to(c.RAIZ)) for p in caminhos(r.sensor, info).values()]}

    def pedidas(sensor: str, datas: list[str]) -> list[dict]:
        k = boas[boas.sensor == sensor]
        if not ARGS.todas_as_boas:
            for d in datas:
                if d not in set(k.data):
                    AVISOS.append(f"{d}: não está entre as cenas boas de {sensor}; ficou de fora, sem substituição")
            k = k[k.data.isin(datas)]
        return [cena(r) for r in k.itertuples() if (r.sensor, r.identificador) in arqs]

    radar, optico = pedidas(RADAR, ARGS.radar), pedidas(OPTICO, ARGS.optico)
    ref_radar = {}
    for orb in sorted({x["orbita_relativa"] for x in radar}):
        k = boas[boas.identificador.isin(refs[refs.orbita_relativa == orb].cena_de_referencia) & boas.janela.isin(ARGS.janelas_de_rio_baixo)]
        ref_radar[orb] = cena(k.iloc[0], "referência de rio baixo da órbita") if len(k) else None
    cand = boas[(boas.sensor == OPTICO) & (boas.nuvem_ou_sombra_pct < ARGS.nuvem_max_referencia)].sort_values("nivel_regua_cm")
    ref_optico = cena(cand.iloc[0], "referência de rio baixo") if len(cand) else None
    e_ref = {x["identificador"] for x in [*ref_radar.values(), ref_optico] if x}
    radar, optico = [x for x in radar if x["identificador"] not in e_ref], [x for x in optico if x["identificador"] not in e_ref]
    comum = lambda x: {k: v for k, v in x.items() if k not in ("caminhos", "versao", "nome")}  # noqa: E731
    previstos = len(radar) * 2 + len([v for v in ref_radar.values() if v]) + (len(optico) + 1) * len(COMPOSICOES)
    if previstos * ARGS.mb_por_imagem / 1000 > ARGS.limite_gb:
        raise SystemExit(f"{previstos} imagens a gravar, cerca de {previstos * ARGS.mb_por_imagem / 1000:.1f} GB: acima do limite de {ARGS.limite_gb:g} GB; nada foi gravado")
    produtos, feitos = [], {"gravadas": 0, "regravadas": 0, "mantidas": 0}

    def fazer(arq: Path, tipo: str) -> bool:
        """Imagem que já existe fica como está, salvo se o tipo estiver em --regravar."""
        if arq.exists() and tipo not in ARGS.regravar:
            feitos["mantidas"] += 1
            return False
        feitos["regravadas" if arq.exists() else "gravadas"] += 1
        return True

    # ---- radar: VV em dB e diferença para a referência de rio baixo da órbita
    def vv_db(x: dict) -> np.ndarray:
        vv = ao.para_a_grade(x["caminhos"]["vv"], grade, Resampling.nearest, np.nan)
        return np.where(np.isfinite(vv) & (vv > 0), 10 * np.log10(np.where(vv > 0, vv, 1)), np.nan).astype("float32")

    base_radar = {"fonte": f"Microsoft Planetary Computer — coleção {licencas[RADAR]['colecao']} (dados Copernicus Sentinel-1)", "licenca": licencas[RADAR], "grade_do_recorte": info_grade}
    vv_meta = {"bandas": ["VV, retroespalhamento gama zero corrigido do terreno, em dB (10·log10)"], "cortes_de_esticamento": "nenhum: valores em dB; sugestão de exibição de −25 a 0 dB"}
    db_ref = {}
    for orb, x in ref_radar.items():
        if x is None:
            continue
        db_ref[orb] = vv_db(x)
        arq = ARGS.saida / f"imagem-radar-vv-db_sentinel1-rtc_{x['nome']}_referencia_10m.tif"
        if fazer(arq, "vv-db"):
            gravar_tif(arq, np.where(np.isfinite(db_ref[orb]), db_ref[orb], SEM_DADO_RADAR).astype("float32"), grade, SEM_DADO_RADAR, **base_radar, **vv_meta, **comum(x))
        produtos.append({**x, "arquivo": arq, "tipo": "vv-db", "referencia": True})
    for x in radar:
        ref = ref_radar.get(x["orbita_relativa"])
        arq_vv, arq_dif = (ARGS.saida / f"imagem-radar-{t}_sentinel1-rtc_{x['nome']}_10m.tif" for t in ("vv-db", "diferenca-vv-db"))
        f_vv, f_dif = fazer(arq_vv, "vv-db"), ref is not None and fazer(arq_dif, "diferenca-vv-db")
        db = vv_db(x) if f_vv or f_dif else None
        if f_vv:
            gravar_tif(arq_vv, np.where(np.isfinite(db), db, SEM_DADO_RADAR).astype("float32"), grade, SEM_DADO_RADAR, **base_radar, **vv_meta, **comum(x))
        produtos.append({**x, "arquivo": arq_vv, "tipo": "vv-db", "referencia": False})
        if ref is None:
            AVISOS.append(f"{x['data']}: órbita {x['orbita_relativa']} sem referência de rio baixo; só o VV")
            continue
        if f_dif:
            dif = db - db_ref[x["orbita_relativa"]]
            gravar_tif(arq_dif, np.where(np.isfinite(dif), dif, SEM_DADO_RADAR).astype("float32"), grade, SEM_DADO_RADAR, bandas=["VV da cena menos VV da referência da mesma órbita, em dB (negativo = escureceu)"],
                       cortes_de_esticamento="nenhum: valores em dB; sugestão de exibição de −10 a +10 dB", referencia={k: ref[k] for k in ("identificador", "data_hora_local", "orbita_relativa", "nivel_media_diaria_cm")},
                       **base_radar, **comum(x))
        produtos.append({**x, "arquivo": arq_dif, "tipo": "diferenca-vv-db", "referencia": False})

    # ---- óptico: falsa cor e, havendo as bandas, cor natural; cortes da cena de referência ou da união das cenas boas
    def bandas(x: dict, nomes: tuple) -> list[np.ndarray] | None:
        cam = {**x["caminhos"], **{b: Path(str(x["caminhos"]["b03"]).replace("-b03_", f"-{b}_")) for b in nomes if b not in x["caminhos"]}}
        if not all(cam[b].exists() for b in nomes):
            return None
        desloc = float(x["versao"] if isinstance(x["versao"], str) and x["versao"] else 0) >= 4
        return [refletancia(cam[b], grade, desloc, Resampling.bilinear if b == "b11" else Resampling.nearest) for b in nomes]

    cortes, regra_dos_cortes = {}, {}
    for comp, nomes in COMPOSICOES.items():
        tipo = comp.split("-")[0]
        if tipo in ARGS.cortes_da_uniao:  # percentis da união dos pixels válidos de TODAS as cenas ópticas boas, e não só das pedidas
            amostra, usadas = [[] for _ in nomes], 0
            for r in boas[boas.sensor == OPTICO].itertuples():
                b = bandas(cena(r), nomes) if (r.sensor, r.identificador) in arqs else None
                if b is None:
                    continue
                valido = np.all([np.isfinite(v) for v in b], axis=0)
                for k, v in enumerate(b):
                    amostra[k].append(v[valido][::ARGS.passo_da_amostra])
                usadas += 1
            if not usadas:
                AVISOS.append(f"{comp}: nenhuma cena com as bandas {', '.join(nomes)}; composição não gerada")
                continue
            cortes[comp] = [tuple(float(q) for q in np.percentile(np.concatenate(a), ARGS.percentis)) for a in amostra]
            regra_dos_cortes[comp] = {"regra": f"percentis {ARGS.percentis[0]:g} e {ARGS.percentis[1]:g}, banda a banda, da união dos pixels válidos das {usadas} cenas ópticas boas; linear de 1 a 255; 0 = sem dado",
                                      "pixels_na_amostra": int(sum(len(a) for a in amostra[0])), "passo_da_amostra": ARGS.passo_da_amostra}
        else:
            b = bandas(ref_optico, nomes) if ref_optico else None
            if b is None:
                AVISOS.append(f"{comp}: a cena de referência não tem as bandas {', '.join(nomes)}; composição não gerada")
                continue
            valido = np.all([np.isfinite(v) for v in b], axis=0)
            cortes[comp] = [tuple(float(q) for q in np.percentile(v[valido], ARGS.percentis)) for v in b]
            regra_dos_cortes[comp] = {"regra": f"percentis {ARGS.percentis[0]:g} e {ARGS.percentis[1]:g} da cena de referência ({ref_optico['data']}), banda a banda; linear de 1 a 255; 0 = sem dado"}
        for x in [*optico, *([ref_optico] if ref_optico else [])]:
            ref = x is ref_optico
            arq = ARGS.saida / f"imagem-{comp}_sentinel2-l2a_{x['nome']}{'_referencia' if ref else ''}_10m.tif"
            if not arq.exists() or tipo in ARGS.regravar:
                b = bandas(x, nomes)
                if b is None:
                    AVISOS.append(f"{x['data']}: sem as bandas {', '.join(nomes)}; {comp} não gerada")
                    continue
            if fazer(arq, tipo):
                pasta = Path(x["arquivos_de_origem"][0]).parent
                origem = [str(pasta / Path(str(x["caminhos"]["b03"]).replace("-b03_", f"-{n}_")).name) if n not in x["caminhos"] else str(x["caminhos"][n].relative_to(c.RAIZ)) for n in nomes]
                gravar_tif(arq, para_uint8(b, cortes[comp]), grade, 0, fonte=f"Microsoft Planetary Computer — coleção {licencas[OPTICO]['colecao']} (dados Copernicus Sentinel-2)", licenca=licencas[OPTICO],
                           bandas=[f"{cor}: {n.upper()}" for cor, n in zip(("vermelho", "verde", "azul"), nomes)], grade_do_recorte=info_grade,
                           cortes_de_esticamento={**regra_dos_cortes[comp], "refletancia": {n.upper(): {"minimo": lo, "maximo": hi} for n, (lo, hi) in zip(nomes, cortes[comp])}},
                           **{**comum(x), "arquivos_de_origem": origem})
            produtos.append({**x, "arquivo": arq, "tipo": tipo, "referencia": ref})

    # ---- índice: uma linha por imagem da pasta
    hora = lambda x: x["data_hora_local"][11:16]  # noqa: E731
    indice = pd.DataFrame([{"arquivo": p["arquivo"].name, "tipo": p["tipo"], "sensor": p["sensor"], "data_local": p["data"], "hora_local": hora(p), "orbita": p["orbita_relativa"], "janela": p["janela"],
                            "regua_cm": p["nivel_media_diaria_cm"], "regua_cm_hora": p["nivel_mais_proximo_da_hora_cm"], "fase": p["fase"], "referencia": "sim" if p["referencia"] else "não",
                            "agua_em_terra_km2": p["agua_em_terra_km2"], "nuvem_sombra_pct": p["nuvem_ou_sombra_pct"] if p["sensor"] == OPTICO else np.nan} for p in produtos if p["arquivo"].exists()])
    indice = indice.sort_values(["sensor", "tipo", "data_local", "arquivo"]).reset_index(drop=True)
    arq_indice = ARGS.saida / "indice_imagens_sentinel.csv"
    indice.to_csv(arq_indice, index=False)
    c.gravar_meta(arq_indice, status=c.STATUS_CONFERENCIA, ligado_ao_portal=False, script=SCRIPT, motivo=MOTIVO, atribuicao=ATRIBUICAO, descricao="uma linha por imagem da pasta", imagens=int(len(indice)), grade_do_recorte=info_grade,
                  crs=c.CRS_PADRAO, cortes_de_esticamento={k: {**regra_dos_cortes[k], "refletancia": {n.upper(): v for n, v in zip(COMPOSICOES[k], cs)}} for k, cs in cortes.items()},
                  argumentos={k: v for k, v in vars(ARGS).items() if k not in ("saida", "figuras", "antes")}, avisos=AVISOS or None,
                  colunas={"arquivo": "nome do GeoTIFF", "tipo": "vv-db, diferenca-vv-db, falsacor ou cornatural", "sensor": "sensor e produto", "data_local": "dia local da cena", "hora_local": "hora local (UTC−3)",
                           "orbita": "órbita relativa", "janela": "período de busca em que a cena foi lida", "regua_cm": "nível da régua: média diária", "regua_cm_hora": "nível da régua mais próximo da hora da cena (telemetria ou leitura das 7h ou 17h)",
                           "fase": "subida, descida ou pico (da curva nível × área)", "referencia": "a cena é a referência de rio baixo (da órbita, no radar; dos cortes da cor natural, no óptico)",
                           "agua_em_terra_km2": "água ligada ao rio, em terra, na área urbana (do controle de qualidade da área urbana)", "nuvem_sombra_pct": "óptico: % da área urbana com nuvem ou sombra"})
    sobram = sorted({a.name for a in ARGS.saida.glob("*.tif")} - set(indice.arquivo))
    if sobram:
        AVISOS.append(f"imagens na pasta que não estão no índice: {', '.join(sobram)}")
    conf = conferir(sorted(ARGS.saida.glob("*.tif")), grade)
    saida = {"cenas": {"radar": len(radar), "referencias_de_radar": len(db_ref), "optico": len(optico), "referencia_optica": ref_optico["data"] if ref_optico else None}, "imagens": feitos,
             "por_tipo": indice.groupby(["sensor", "tipo"]).size().rename("n").reset_index().to_dict("records"), "por_janela_sensor_tipo": indice.groupby(["janela", "sensor", "tipo"]).size().rename("n").reset_index().to_dict("records"),
             "cortes": {k: {**regra_dos_cortes[k], "refletancia": {n.upper(): v for n, v in zip(COMPOSICOES[k], cs)}} for k, cs in cortes.items()}, "grade": info_grade, "tamanho_da_pasta_mb": float(sum(a.stat().st_size for a in ARGS.saida.iterdir()) / 1e6),
             "conferencia_por_tipo": conf.assign(tipo=conf.arquivo.str.split("_").str[0]).groupby(["tipo", "crs", "pixel_m", "largura", "altura", "mesma_grade", "bandas", "tipo_do_dado", "sem_dado", "piramides", "compressao", "bloco"])
             .agg(arquivos=("arquivo", "size"), com_borda_sem_dado=("com_dado_pct", lambda v: int((v < 100).sum())), menor_com_dado_pct=("com_dado_pct", "min"), mb=("tamanho_mb", "sum")).reset_index().to_dict("records")}

    # ---- figuras de controle (fora do repositório)
    if ARGS.figuras:
        ARGS.figuras.mkdir(parents=True, exist_ok=True)
        conf.to_csv(ARGS.figuras / "conferencia-dos-geotiffs_sentinel_arquivo.csv", index=False)
        existem = [p for p in produtos if p["arquivo"].exists()]
        for janela in sorted({p["janela"] for p in existem}):
            for tipo, rotulo in (("vv-db", "VV em dB"), ("falsacor", "falsa cor B11-B08-B03"), ("cornatural", "cor natural B04-B03-B02")):
                k = sorted((p for p in existem if p["janela"] == janela and p["tipo"] == tipo), key=lambda p: p["data_hora_local"])
                if k:
                    figura_de_controle(ARGS.figuras / f"controle_{janela}_{tipo}.png", [{"arquivo": p["arquivo"], "data": p["data"], "tipo": rotulo + (" (referência)" if p["referencia"] else ""),
                                                                                     "sensor": "radar" if p["sensor"] == RADAR else "óptico", "nivel": p["nivel_media_diaria_cm"]} for p in k], ext, f"Janela {janela} — {rotulo}")
        if ARGS.antes:
            pares = [(ARGS.antes / p["arquivo"].name, p["arquivo"], p["data"]) for p in existem if (ARGS.antes / p["arquivo"].name).exists()]
            if pares:
                figura_antes_e_depois(ARGS.figuras / "falsacor-antes-e-depois.png", pares, ext)
                saida["antes_e_depois"] = [{"arquivo": d.name, **{f"pct_no_maximo_{cor}_{q}": pct_no_maximo(a, b) for b, cor in ((1, "vermelho"), (2, "verde"), (3, "azul")) for q, a in (("antes", a0), ("depois", d))}} for a0, d, _ in pares]
        forma = (grade["height"], grade["width"])
        sobre = []
        for item in ARGS.sobrepor:
            nome, data = item.split(":")
            sensor = RADAR if nome == "radar" else OPTICO
            p = next((p for p in existem if p["sensor"] == sensor and p["data"] == data and p["tipo"] in ("vv-db", "falsacor")), None)
            if p is None:
                AVISOS.append(f"sobreposição {item}: imagem não gerada")
                continue
            gpkg = PROC44 / f"agua-observada-area-urbana_{sensor}_2017-2024_10m.gpkg"
            terra, total = (g[g.identificador == p["identificador"]].geometry.iloc[0] for g in (gpd.read_file(gpkg, layer=cam) for cam in ("inundacao_em_terra", "agua_total")))
            figura_sobreposta(ARGS.figuras / f"sobreposicao-agua-em-terra_{nome}_{data}.png", p["arquivo"], terra, urbano, ext,
                              f"{'Sentinel-1, VV em dB' if sensor == RADAR else 'Sentinel-2, falsa cor B11-B08-B03'} de {data} (régua: {ao.fmt(float(p['nivel_media_diaria_cm']), 0)} cm) e o contorno da água em terra da mesma cena")
            # medida de apoio: a banda em que a água é escura (VV no radar; B11 no óptico), dentro da água em terra e na área urbana sem água
            na_agua, com_agua = (features.rasterize([(g, 1)], out_shape=forma, transform=grade["transform"], fill=0, dtype="uint8").astype(bool) for g in (terra, total))
            dentro = features.rasterize([(urbano, 1)], out_shape=forma, transform=grade["transform"], fill=0, dtype="uint8").astype(bool)
            with rasterio.open(p["arquivo"]) as src:
                v = src.read(1, masked=True).astype("float32").filled(np.nan)
            seco = dentro & ~com_agua & np.isfinite(v)
            sobre.append({"sensor": nome, "data": data, "banda": "VV (dB)" if sensor == RADAR else "B11 (valor de 1 a 255 na imagem)", "pixels_de_agua_em_terra": int(na_agua.sum()), "mediana_na_agua_em_terra": float(np.nanmedian(v[na_agua])),
                          "mediana_na_area_urbana_sem_agua": float(np.nanmedian(v[seco])), "pct_da_agua_mais_escura_que_a_mediana_sem_agua": float(100 * np.nanmean(v[na_agua] < np.nanmedian(v[seco])))})
        if sobre:
            saida["sobreposicao"] = sobre
            pd.DataFrame(sobre).to_csv(ARGS.figuras / "sobreposicao-agua-em-terra_medidas.csv", index=False)
    saida["avisos"] = AVISOS
    print(json.dumps(saida, ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", force=True)
    _p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    _p.add_argument("--saida", type=Path, required=True, help="pasta (fora do repositório) que recebe as imagens, os .json e o índice")
    _p.add_argument("--todas-as-boas", action="store_true", help="todas as cenas boas, em vez das datas de --radar e --optico")
    _p.add_argument("--radar", nargs="*", default=[], metavar="AAAA-MM-DD", help="datas locais das cenas de radar")
    _p.add_argument("--optico", nargs="*", default=[], metavar="AAAA-MM-DD", help="datas locais das cenas ópticas")
    _p.add_argument("--regravar", nargs="*", default=[], choices=["vv-db", "diferenca-vv-db", "falsacor", "cornatural"], help="tipos de imagem gravados de novo mesmo se o arquivo já existir (os demais ficam como estão)")
    _p.add_argument("--cortes-da-uniao", nargs="*", default=[], choices=["falsacor", "cornatural"], help="composições com cortes tirados da união das cenas ópticas boas, e não da cena de referência")
    _p.add_argument("--passo-da-amostra", type=int, default=1, help="com --cortes-da-uniao: entra um pixel válido a cada tantos (1 = todos)")
    _p.add_argument("--janelas-de-rio-baixo", nargs="*", default=["J0"], help="janelas de busca de onde sai a referência de rio baixo de cada órbita do radar")
    _p.add_argument("--margem-m", type=float, default=500.0, help="margem em volta do retângulo da área urbana (m; múltiplo do pixel)")
    _p.add_argument("--nuvem-max-referencia", type=float, default=5.0, help="a referência óptica é a cena boa de menor nível com nuvem ou sombra abaixo disto (%%)")
    _p.add_argument("--percentis", type=float, nargs=2, default=[2.0, 98.0], help="percentis que viram os cortes do esticamento do óptico")
    _p.add_argument("--piramides", type=int, nargs="+", default=[2, 4, 8], help="fatores das pirâmides internas")
    _p.add_argument("--mb-por-imagem", type=float, default=4.5, help="tamanho por imagem usado para estimar o total antes de gravar (MB)")
    _p.add_argument("--limite-gb", type=float, default=1.5, help="acima deste total estimado nada é gravado (GB)")
    _p.add_argument("--sobrepor", nargs="*", default=[], metavar="SENSOR:AAAA-MM-DD", help="cenas (radar:DATA ou optico:DATA) com figura do contorno da água em terra sobre a imagem")
    _p.add_argument("--antes", type=Path, help="pasta com as versões anteriores das imagens regravadas, para a figura de antes e depois")
    _p.add_argument("--figuras", type=Path, help="pasta (fora do repositório) das figuras e tabelas de controle")
    ARGS = _p.parse_args()
    AVISOS: list[str] = []
    for _pasta in (ARGS.saida, ARGS.figuras):
        if _pasta is not None and c.RAIZ in _pasta.resolve().parents:
            raise SystemExit("--saida e --figuras têm de ficar fora do repositório")
    main()
