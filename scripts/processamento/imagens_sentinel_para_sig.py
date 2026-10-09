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
          cortes fixos, iguais para todas as cenas: os percentis --percentis da
          cena de referência (a cena boa de menor nível da régua com nuvem ou
          sombra abaixo de --nuvem-max-referencia %), banda a banda.

GeoTIFF com compressão DEFLATE, blocos internos e pirâmides internas; .json irmão.
As imagens vão só para --saida e as figuras de controle para --figuras, as duas
fora do repositório. Só cenas boas daquele roteiro; data pedida que não for cena
boa do sensor é avisada e fica de fora, sem substituição.

Uso:
  python scripts/processamento/imagens_sentinel_para_sig.py --saida PASTA \
      --radar AAAA-MM-DD [...] --optico AAAA-MM-DD [...] [--figuras PASTA] [--sobrepor sensor:AAAA-MM-DD ...]
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
                           "bandas": src.count, "tipo": src.dtypes[0], "sem_dado": src.nodata, "piramides": "/".join(str(x) for x in src.overviews(1)), "compressao": src.compression.value if src.compression else "",
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


def figura_de_controle(arq: Path, itens: list[dict], ext) -> None:
    colunas = 6
    linhas = int(np.ceil(len(itens) / colunas))
    fig, eixos = plt.subplots(linhas, colunas, figsize=(3.6 * colunas, 3.6 * linhas), squeeze=False)
    for ax in eixos.ravel():
        ax.axis("off")
    for ax, it in zip(eixos.ravel(), itens):
        ax.axis("on")
        miniatura(ax, it["arquivo"], ext)
        ax.set_title(f"{it['data']} — {it['tipo']}\n{it['sensor']} — régua {ao.fmt(float(it['nivel']), 0)} cm", fontsize=8, loc="left")
    fig.suptitle("Imagens recortadas na área urbana (controle). Radar em dB de −25 (preto) a 0 (branco); diferença de −10 dB (vermelho) a +10 dB (azul); magenta = sem dado.", fontsize=9.5, x=0.01, ha="left")
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


# ---------------------------------------------------------------- principal
def main() -> None:
    if ARGS.saida.exists() and any(ARGS.saida.iterdir()) and not ARGS.refazer:
        raise SystemExit("a pasta de saída já tem arquivos (use --refazer para gerar de novo)")
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

    # ---- cenas pedidas, entre as boas
    cq = pd.read_csv(PROC44 / "controle-de-qualidade_sentinel_2017-2024_cena.csv")
    boas = cq[cq.boa].assign(data=lambda t: t.data_hora_local.str[:10])
    arqs, diaria, tel = tabela_de_arquivos(), ao.ler_serie_diaria(), ao.ler_telemetria()
    refs = pd.read_csv(PROC44 / "referencia-por-orbita_sentinel1-rtc_2017-2024_orbita.csv")

    def cena(sensor: str, data: str, papel: str = "cena") -> dict | None:
        k = boas[(boas.sensor == sensor) & (boas.data == data)]
        if k.empty or (sensor, k.identificador.iloc[0]) not in arqs:
            AVISOS.append(f"{data}: não está entre as cenas boas de {sensor}; ficou de fora, sem substituição")
            return None
        r, info = k.iloc[0], arqs[(sensor, k.identificador.iloc[0])]
        local = pd.Timestamp(r.data_hora_local)
        return {"sensor": sensor, "papel": papel, "data": data, "identificador": r.identificador, "data_hora_utc": info["utc"], "data_hora_local": r.data_hora_local, "orbita_relativa": int(float(info["orbita"])),
                "direcao": info["direcao"], **niveis(local, diaria, tel), "nuvem_ou_sombra_pct": r.nuvem_ou_sombra_pct, "versao": info["versao"], "caminhos": caminhos(sensor, info),
                "arquivos_de_origem": [str(p.relative_to(c.RAIZ)) for p in caminhos(sensor, info).values()]}

    radar = [x for x in (cena(RADAR, d) for d in ARGS.radar) if x]
    optico = [x for x in (cena(OPTICO, d) for d in ARGS.optico) if x]
    ref_radar = {}
    for orb in sorted({x["orbita_relativa"] for x in radar}):
        r = refs[refs.orbita_relativa == orb]
        ref_radar[orb] = cena(RADAR, r.data_hora_local.iloc[0][:10], "referência de rio baixo da órbita") if len(r) else None
    cand = boas[(boas.sensor == OPTICO) & (boas.nuvem_ou_sombra_pct < ARGS.nuvem_max_referencia)].sort_values("nivel_regua_cm")
    ref_optico = cena(OPTICO, cand.data.iloc[0], "referência de rio baixo") if len(cand) else None
    comum = lambda x: {k: v for k, v in x.items() if k not in ("caminhos", "versao")}  # noqa: E731
    produtos, resumo = [], []

    # ---- radar: VV em dB e diferença para a referência da órbita
    def vv_db(x: dict) -> np.ndarray:
        vv = ao.para_a_grade(x["caminhos"]["vv"], grade, Resampling.nearest, np.nan)
        return np.where(np.isfinite(vv) & (vv > 0), 10 * np.log10(np.where(vv > 0, vv, 1)), np.nan).astype("float32")

    db_ref = {}
    for orb, x in ref_radar.items():
        if x is None:
            continue
        db_ref[orb] = vv_db(x)
        arq = ARGS.saida / f"imagem-radar-vv-db_sentinel1-rtc_{x['data']}_referencia_10m.tif"
        gravar_tif(arq, np.where(np.isfinite(db_ref[orb]), db_ref[orb], SEM_DADO_RADAR).astype("float32"), grade, SEM_DADO_RADAR, fonte=f"Microsoft Planetary Computer — coleção {licencas[RADAR]['colecao']} (dados Copernicus Sentinel-1)",
                   licenca=licencas[RADAR], bandas=["VV, retroespalhamento gama zero corrigido do terreno, em dB (10·log10)"], cortes_de_esticamento="nenhum: valores em dB; sugestão de exibição de −25 a 0 dB", grade_do_recorte=info_grade, **comum(x))
        produtos.append({**x, "arquivo": arq, "tipo": "VV em dB (referência)"})
    for x in radar:
        db = vv_db(x)
        base = {"fonte": f"Microsoft Planetary Computer — coleção {licencas[RADAR]['colecao']} (dados Copernicus Sentinel-1)", "licenca": licencas[RADAR], "grade_do_recorte": info_grade, **comum(x)}
        arq = ARGS.saida / f"imagem-radar-vv-db_sentinel1-rtc_{x['data']}_10m.tif"
        gravar_tif(arq, np.where(np.isfinite(db), db, SEM_DADO_RADAR).astype("float32"), grade, SEM_DADO_RADAR, bandas=["VV, retroespalhamento gama zero corrigido do terreno, em dB (10·log10)"],
                   cortes_de_esticamento="nenhum: valores em dB; sugestão de exibição de −25 a 0 dB", **base)
        produtos.append({**x, "arquivo": arq, "tipo": "VV em dB"})
        ref = ref_radar.get(x["orbita_relativa"])
        if ref is None:
            AVISOS.append(f"{x['data']}: órbita {x['orbita_relativa']} sem referência; sem imagem de diferença")
            continue
        dif = db - db_ref[x["orbita_relativa"]]
        arq = ARGS.saida / f"imagem-radar-diferenca-vv-db_sentinel1-rtc_{x['data']}_10m.tif"
        gravar_tif(arq, np.where(np.isfinite(dif), dif, SEM_DADO_RADAR).astype("float32"), grade, SEM_DADO_RADAR, bandas=["VV da cena menos VV da referência da mesma órbita, em dB (negativo = escureceu)"],
                   cortes_de_esticamento="nenhum: valores em dB; sugestão de exibição de −10 a +10 dB", referencia={k: ref[k] for k in ("identificador", "data_hora_local", "orbita_relativa", "nivel_media_diaria_cm")}, **base)
        produtos.append({**x, "arquivo": arq, "tipo": "diferença de VV (dB)"})

    # ---- óptico: falsa cor e, havendo as bandas, cor natural; cortes da cena de referência
    def bandas(x: dict, nomes: tuple) -> list[np.ndarray] | None:
        cam = {**x["caminhos"], **{b: Path(str(x["caminhos"]["b03"]).replace("-b03_", f"-{b}_")) for b in nomes if b not in x["caminhos"]}}
        if not all(cam[b].exists() for b in nomes):
            return None
        desloc = float(x["versao"] if isinstance(x["versao"], str) and x["versao"] else 0) >= 4
        return [refletancia(cam[b], grade, desloc, Resampling.bilinear if b == "b11" else Resampling.nearest) for b in nomes]

    cortes = {}
    for comp, nomes in COMPOSICOES.items():
        b = bandas(ref_optico, nomes) if ref_optico else None
        if b is None:
            AVISOS.append(f"{comp}: a cena de referência não tem as bandas {', '.join(nomes)}; composição não gerada")
            continue
        valido = np.all([np.isfinite(v) for v in b], axis=0)
        cortes[comp] = [tuple(float(q) for q in np.percentile(v[valido], ARGS.percentis)) for v in b]
        for x in [*optico, ref_optico]:
            b = bandas(x, nomes)
            if b is None:
                AVISOS.append(f"{x['data']}: sem as bandas {', '.join(nomes)}; {comp} não gerada")
                continue
            e_ref = x is ref_optico
            arq = ARGS.saida / f"imagem-{comp}_sentinel2-l2a_{x['data']}{'_referencia' if e_ref else ''}_10m.tif"
            origem = sorted({*x["arquivos_de_origem"], *(str(Path(x["arquivos_de_origem"][0]).parent / Path(str(x["caminhos"]["b03"]).replace("-b03_", f"-{n}_")).name) for n in nomes)})
            gravar_tif(arq, para_uint8(b, cortes[comp]), grade, 0, fonte=f"Microsoft Planetary Computer — coleção {licencas[OPTICO]['colecao']} (dados Copernicus Sentinel-2)", licenca=licencas[OPTICO],
                       bandas=[f"{cor}: {n.upper()}" for cor, n in zip(("vermelho", "verde", "azul"), nomes)], grade_do_recorte=info_grade,
                       cortes_de_esticamento={"regra": f"percentis {ARGS.percentis[0]:g} e {ARGS.percentis[1]:g} da cena de referência ({ref_optico['data']}), banda a banda; linear de 1 a 255; 0 = sem dado",
                                              "refletancia": {n.upper(): {"minimo": lo, "maximo": hi} for n, (lo, hi) in zip(nomes, cortes[comp])}},
                       **{**comum(x), "arquivos_de_origem": [o for o in origem if any(f"-{n}_" in o for n in nomes)]})
            produtos.append({**x, "arquivo": arq, "tipo": ("falsa cor B11-B08-B03" if comp.startswith("falsa") else "cor natural B04-B03-B02") + (" (referência)" if e_ref else "")})

    # ---- tabela das cenas e conferência dos arquivos
    for x in [*radar, *[v for v in ref_radar.values() if v], *optico, *([ref_optico] if ref_optico else [])]:
        resumo.append({k: x[k] for k in ("sensor", "papel", "data", "data_hora_utc", "data_hora_local", "orbita_relativa", "direcao", "nivel_media_diaria_cm", "nivel_consistencia", "nivel_mais_proximo_da_hora_cm",
                                         "origem_do_nivel_mais_proximo", "nuvem_ou_sombra_pct", "identificador")} | {"arquivos_de_origem": "; ".join(Path(o).name for o in x["arquivos_de_origem"])})
    resumo = pd.DataFrame(resumo)
    conf = conferir([p["arquivo"] for p in produtos], grade)
    saida = {"cenas": resumo.to_dict("records"), "cortes_de_esticamento_refletancia": {k: {n.upper(): v for n, v in zip(COMPOSICOES[k], cs)} for k, cs in cortes.items()}, "grade": info_grade,
             "conferencia": conf.to_dict("records"), "avisos": AVISOS}

    # ---- figuras de controle (fora do repositório)
    if ARGS.figuras:
        ARGS.figuras.mkdir(parents=True, exist_ok=True)
        resumo.to_csv(ARGS.figuras / "cenas-das-imagens_sentinel_cena.csv", index=False)
        conf.to_csv(ARGS.figuras / "conferencia-dos-geotiffs_sentinel_arquivo.csv", index=False)
        ordem = sorted(produtos, key=lambda p: (p["sensor"], "referência" in p["tipo"], p["tipo"], p["data"]))
        figura_de_controle(ARGS.figuras / "controle-miniaturas_imagens-sentinel.png", [{"arquivo": p["arquivo"], "data": p["data"], "tipo": p["tipo"], "sensor": "radar" if p["sensor"] == RADAR else "óptico", "nivel": p["nivel_media_diaria_cm"]}
                                                                                         for p in ordem], ext)
        forma = (grade["height"], grade["width"])
        sobre = []
        for item in ARGS.sobrepor:
            nome, data = item.split(":")
            sensor = RADAR if nome == "radar" else OPTICO
            p = next((p for p in produtos if p["sensor"] == sensor and p["data"] == data and p["tipo"] in ("VV em dB", "falsa cor B11-B08-B03")), None)
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
        saida["sobreposicao"] = sobre
        pd.DataFrame(sobre).to_csv(ARGS.figuras / "sobreposicao-agua-em-terra_medidas.csv", index=False)
    saida["avisos"] = AVISOS
    print(json.dumps(saida, ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", force=True)
    _p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    _p.add_argument("--saida", type=Path, required=True, help="pasta (fora do repositório) que recebe as imagens e os .json")
    _p.add_argument("--radar", nargs="*", default=[], metavar="AAAA-MM-DD", help="datas locais das cenas de radar")
    _p.add_argument("--optico", nargs="*", default=[], metavar="AAAA-MM-DD", help="datas locais das cenas ópticas")
    _p.add_argument("--margem-m", type=float, default=500.0, help="margem em volta do retângulo da área urbana (m; múltiplo do pixel)")
    _p.add_argument("--nuvem-max-referencia", type=float, default=5.0, help="a referência óptica é a cena boa de menor nível com nuvem ou sombra abaixo disto (%%)")
    _p.add_argument("--percentis", type=float, nargs=2, default=[2.0, 98.0], help="percentis da cena de referência que viram os cortes do esticamento do óptico")
    _p.add_argument("--piramides", type=int, nargs="+", default=[2, 4, 8], help="fatores das pirâmides internas")
    _p.add_argument("--sobrepor", nargs="*", default=[], metavar="SENSOR:AAAA-MM-DD", help="cenas (radar:DATA ou optico:DATA) com figura do contorno da água em terra sobre a imagem")
    _p.add_argument("--figuras", type=Path, help="pasta (fora do repositório) das figuras e tabelas de controle")
    _p.add_argument("--refazer", action="store_true", help="gera de novo mesmo se a pasta de saída já tiver arquivos")
    ARGS = _p.parse_args()
    AVISOS: list[str] = []
    for _pasta in (ARGS.saida, ARGS.figuras):
        if _pasta is not None and c.RAIZ in _pasta.resolve().parents:
            raise SystemExit("--saida e --figuras têm de ficar fora do repositório")
    main()
