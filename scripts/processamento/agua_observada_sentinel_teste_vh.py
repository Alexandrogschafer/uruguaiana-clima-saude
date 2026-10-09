"""
Teste da polarização VH nas passagens de radar recusadas por rio sem contraste.

TESTE À PARTE: a regra principal (VV), os limiares e o controle de qualidade de
agua_observada_sentinel_area_urbana.py e de agua_observada_sentinel_cheias_novas.py
não mudam, nenhum produto deles é regravado e a curva principal não é substituída.

  A  regra do VH com a mesma forma da do VV — água = (queda em relação à referência
     de rio baixo da órbita E valor abaixo de um teto) OU (água na referência E
     valor abaixo do teto) —, no mesmo domínio, na mesma grade, com o mesmo leito de
     referência, as mesmas referências por órbita e a mesma mediana. Os dois
     limiares saem só das cenas de radar boas: os que dão a maior interseção/união
     média entre a água em terra do VH e a do VV. Validação cruzada por período e
     o mesmo controle de qualidade (fração do leito principal marcada como água).
  B  cada passagem recusada por rio sem contraste, com o VH: fração do leito, água,
     comparação com a cena boa vizinha (ou, sem vizinha, com a curva nível × área)
     e a classificação "aceita com ressalva (VH)" ou "continua recusada".
  C  a curva nível × área com as cenas boas mais as aceitas com ressalva, só como
     sensibilidade.
  D  para as aceitas com ressalva: imagens do VH (em --imagens, pasta à parte) e a
     água num GeoPackage.

Saídas em data/processed/agua_observada_sentinel_teste_vh/ (fora do git); figura só
em --figuras e imagens só em --imagens (fora do repositório). Sem rede: só lê os
brutos já gravados. Produtos DERIVADOS, para conferência.

Uso:
  python scripts/processamento/agua_observada_sentinel_teste_vh.py \
      [--mancha-extra COTA=ARQUIVO.gpkg:CAMADA] [--figuras PASTA] [--imagens PASTA] [--copia PASTA]
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
from rasterio.transform import from_origin  # noqa: E402
from rasterio.warp import Resampling  # noqa: E402

import agua_observada_sentinel as ao  # noqa: E402
import agua_observada_sentinel_area_urbana as au  # noqa: E402
import agua_observada_sentinel_cheias_novas as cn  # noqa: E402
import agua_observada_sentinel_curva_nivel_area as cna  # noqa: E402
import dinamica_populacional_comum as c  # noqa: E402
import exposicao_inundacao_cenarios as ec  # noqa: E402
import exposicao_inundacao_enderecos as ee  # noqa: E402
import imagens_sentinel_para_sig as im  # noqa: E402

logger = logging.getLogger(__name__)

SCRIPT = "scripts/processamento/agua_observada_sentinel_teste_vh.py"
MOTIVO = "teste da polarização VH nas passagens de radar recusadas por rio sem contraste"
PROC = c.RAIZ / "data" / "processed" / "agua_observada_sentinel_teste_vh"
PROC44, PROC45, PROC49 = au.PROC, cna.PROC, cn.PROC
RES, RADAR, OPTICO = au.RES, au.RADAR, au.OPTICO
NOME = {RADAR: "radar", OPTICO: "óptico"}
RESSALVA = "aceita com ressalva pelo teste do VH (rodada 51); recusada pela regra principal"
ACEITA, RECUSADA = "aceita com ressalva (VH)", "continua recusada"
LIMITACOES = ["teste à parte: a regra principal (VV) e o controle de qualidade não mudam",
              "os limiares do VH são ajustados para repetir a água em terra do VV nas cenas boas: o VH herda os erros do VV nessas cenas",
              "o VH tem menos sinal que o VV: perto do ruído do sensor, terreno liso e sombra também ficam escuros",
              "o leito principal e a água da referência usados na regra do VH são os do VV (o mesmo leito de referência)",
              "a vizinha de comparação é de outro dia e de outro nível (até a distância e a diferença de nível dos argumentos)",
              "a curva de sensibilidade não substitui a principal"]


# ---------------------------------------------------------------- gravação
def rel(p: Path) -> str:
    return str(Path(p).relative_to(c.RAIZ))


def meta(caminho: Path, **kw) -> None:
    c.gravar_meta(caminho, crs=c.CRS_PADRAO, status=c.STATUS_CONFERENCIA, ligado_ao_portal=False, script=SCRIPT, motivo=MOTIVO, fontes=FONTES, teste_a_parte=LIMITACOES[0],
                  fora_do_git="data/processed/ é ignorado; produto derivado, para conferência", limitacoes=LIMITACOES, avisos=AVISOS or None,
                  argumentos={k: v for k, v in vars(ARGS).items() if k not in ("figuras", "copia", "mancha_extra", "imagens")}, **kw)


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


def iou(a: np.ndarray, b: np.ndarray, dominio: np.ndarray) -> float:
    a, b = a & dominio, b & dominio
    uniao = (a | b).sum()
    return float((a & b).sum() / uniao) if uniao else np.nan


# ---------------------------------------------------------------- figura
def figura(arq: Path, boas: pd.DataFrame, testadas: pd.DataFrame, curvas: dict) -> None:
    fig, ax = plt.subplots(figsize=(11.5, 7))
    for sensor, forma, cor in ((RADAR, "o", "#2a78d6"), (OPTICO, "s", "#d95f02")):
        k = boas[boas.sensor == sensor]
        ax.scatter(k.nivel_regua_cm, k.a1_km2, marker=forma, s=30, color=cor, alpha=0.75, linewidth=0, zorder=3)
    ac, re = testadas[testadas.classificacao == ACEITA], testadas[testadas.classificacao != ACEITA]
    ax.scatter(ac.nivel_regua_cm, ac.inundacao_em_terra_vh_km2, marker="D", s=70, color="#008300", edgecolor="black", linewidth=0.8, zorder=5)
    ax.scatter(re.nivel_regua_cm, re.inundacao_em_terra_vh_km2, marker="D", s=60, facecolor="none", edgecolor="#333333", linewidth=1.2, zorder=4)
    for r in testadas[testadas.destaque].itertuples():
        ax.annotate(r.data_hora_local[:10], (r.nivel_regua_cm, r.inundacao_em_terra_vh_km2), textcoords="offset points", xytext=(8, -12), fontsize=8, color="#0b0b0b", arrowprops={"arrowstyle": "-", "color": "#898781", "lw": 0.6})
    for rot, (xs, ys), estilo, cor in (("principal", curvas["principal"], "-", "#333333"), ("sensibilidade", curvas["sensibilidade"], "--", "#008300")):
        ax.plot(xs, ys, ls=estilo, color=cor, lw=1.5, zorder=2)
    ax.set_xlabel("nível da régua no dia da cena (cm)", fontsize=9)
    ax.set_ylabel("água ligada ao rio, em terra, na área urbana — A1 (km²)", fontsize=9)
    ax.grid(color="#e3e2dc", lw=0.6)
    ax.set_axisbelow(True)
    for lado in ("top", "right"):
        ax.spines[lado].set_visible(False)
    ax.tick_params(labelsize=8, colors="#52514e")
    ax.set_title("Nível × água em terra: cenas boas, cenas recusadas vistas pelo VH e as duas curvas", fontsize=10.5, loc="left")
    ax.legend(handles=[Line2D([], [], marker="o", ls="", color="#2a78d6", label="radar, cena boa (VV)"), Line2D([], [], marker="s", ls="", color="#d95f02", label="óptico, cena boa"),
                       Line2D([], [], marker="D", ls="", color="#008300", mec="black", markersize=8, label="recusada pelo VV, aceita com ressalva (área do VH)"),
                       Line2D([], [], marker="D", ls="", mfc="none", color="#333333", markersize=8, label="recusada pelo VV, continua recusada (área do VH)"),
                       Line2D([], [], color="#333333", lw=1.5, label="curva principal (os dois sensores, cenas boas)"), Line2D([], [], color="#008300", lw=1.5, ls="--", label="curva de sensibilidade (boas + aceitas com ressalva)")],
              fontsize=8, frameon=False, loc="upper left")
    fig.text(0.01, 0.01, "Teste à parte: a regra principal (VV) não muda e a curva principal não é substituída. Produto derivado, para conferência.", fontsize=7, color="#555555")
    fig.tight_layout(rect=(0, 0.025, 1, 1))
    fig.savefig(arq, dpi=200)
    plt.close(fig)


# ---------------------------------------------------------------- principal
def main() -> None:
    global FONTES
    if PROC.exists() and any(PROC.iterdir()) and not ARGS.refazer:
        raise SystemExit(f"{rel(PROC)} já tem arquivos (use --refazer para gerar de novo)")
    PROC.mkdir(parents=True, exist_ok=True)
    m44 = json.loads((PROC44 / "controle-de-qualidade_sentinel_2017-2024_cena.json").read_text(encoding="utf-8"))
    arg44 = argparse.Namespace(**m44["argumentos"])
    au.ARGS = arg44  # a mediana do radar e os limiares do VV são os da classificação principal
    FONTES = m44["fontes"]
    brutos = cn.brutos_por_identificador()

    # ---- as passagens de radar das duas tabelas de controle de qualidade
    a = pd.read_csv(PROC44 / "controle-de-qualidade_sentinel_2017-2024_cena.csv")
    b = pd.read_csv(next(iter(sorted(PROC49.glob("controle-de-qualidade-das-cenas-novas_*_cena.csv")))))
    todas = pd.concat([
        pd.DataFrame({"sensor": a.sensor, "identificador": a.identificador, "data_hora_local": a.data_hora_local, "orbita_relativa": a.orbita_relativa, "nivel_regua_cm": a.nivel_regua_cm, "janela": a.janela, "boa": a.boa.astype(bool),
                      "motivo": a.motivo.fillna(""), "fracao_leito_vv": a.fracao_do_leito_principal_marcada_como_agua, "a1_km2": a.inundacao_em_terra_km2, "origem": "área urbana"}),
        pd.DataFrame({"sensor": b.sensor, "identificador": b.identificador, "data_hora_local": b.data_hora_local, "orbita_relativa": b.orbita_relativa, "nivel_regua_cm": b.regua_cm, "janela": b.episodio, "boa": b.boa.astype(bool),
                      "motivo": b.motivo.fillna(""), "fracao_leito_vv": b.fracao_do_leito_principal_marcada_como_agua, "a1_km2": b.inundacao_em_terra_km2, "origem": "cheias novas", "regua_cm_hora": b.regua_cm_hora, "fase": b.fase})],
        ignore_index=True)
    todas["chave"] = todas.sensor + "_" + pd.to_datetime(todas.data_hora_local).dt.strftime("%Y%m%dT%H%M")
    radar = todas[todas.sensor == RADAR]
    boas_radar, testadas = radar[radar.boa], radar[~radar.boa & (radar.motivo == ARGS.motivo_testado)].sort_values("data_hora_local").reset_index(drop=True)

    # ---- mesma grade, mesmo domínio, mesmo leito e mesmas referências de rio baixo
    urbano = gpd.read_file(PROC44 / "area-urbana_sgb_2023_retangulo.gpkg", layer="area_urbana").to_crs(c.CRS_PADRAO).geometry.iloc[0]
    x0, y0, x1, y1 = urbano.bounds
    gx0, gy1 = np.floor(x0 / RES) * RES, np.ceil(y1 / RES) * RES
    grade = {"transform": from_origin(gx0, gy1, RES, RES), "width": int(np.ceil((x1 - gx0) / RES)), "height": int(np.ceil((gy1 - y0) / RES))}
    forma, tr = (grade["height"], grade["width"]), grade["transform"]
    px_km2 = RES * RES / 1e6
    dentro = cna.rasterizar(urbano, forma, tr)
    dom = dentro & cna.rasterizar(c.carregar_area_estudo().to_crs(c.CRS_PADRAO).union_all(), forma, tr)
    leito = cna.rasterizar(gpd.read_file(PROC44 / "referencia-por-orbita_sentinel1-rtc_2017-2024_10m.gpkg", layer="leito_de_referencia_comum").geometry.union_all(), forma, tr)
    minimo_px = int(np.ceil(arg44.area_minima_ha * 1e4 / (RES * RES)))
    tab_ref = pd.read_csv(PROC44 / "referencia-por-orbita_sentinel1-rtc_2017-2024_orbita.csv")
    refs = {}
    for r in tab_ref[tab_ref.abaixo_do_nivel_maximo].itertuples():  # só as referências de rio baixo
        arqs = brutos[(RADAR, r.cena_de_referencia)]["arquivos"]
        vv, valido_vv = au.vv_em_db(arqs["vv"], grade)
        vh, valido_vh = au.vv_em_db(arqs["vh"], grade)  # a mesma função: potência -> dB e mediana, agora no VH
        agua = ao.tirar_grupos_pequenos(valido_vv & (vv <= arg44.vv_agua_referencia), minimo_px)
        refs[int(r.orbita_relativa)] = {"vv": {"db": vv, "valido": valido_vv, "agua": agua}, "vh": {"db": vh, "valido": valido_vh, "agua": agua}, "rio": au.maior_grupo(agua) & dentro, "data": r.data_hora_local[:10], "cena": r.cena_de_referencia}

    def agua_do_vh(db, valido, ref, mudanca, teto) -> np.ndarray:
        """A regra do VV, com os limiares do VH: queda em relação à referência e valor abaixo do teto, ou água na referência e valor abaixo do teto."""
        marca = ((db - ref["db"] <= mudanca) & (db <= teto)) | (ref["agua"] & (db <= teto))
        return ao.tirar_grupos_pequenos(marca & valido & ref["valido"], minimo_px)

    def ler(r) -> dict | None:
        """VV (regra principal) e VH (dB com a mediana) de uma passagem; None se faltar arquivo ou referência de rio baixo."""
        k = brutos.get((RADAR, r.identificador))
        ref = refs.get(int(r.orbita_relativa)) if pd.notna(r.orbita_relativa) else None
        if k is None or ref is None or not {"vv", "vh"} <= set(k["arquivos"]):
            return None
        vv, valido_vv = au.vv_em_db(k["arquivos"]["vv"], grade)
        vh, valido_vh = au.vv_em_db(k["arquivos"]["vh"], grade)
        agua_vv = au.agua_por_mudanca(vv, valido_vv, ref["vv"], arg44.mudanca, minimo_px)
        ligada = ao.ligada_ao(agua_vv, leito)
        return {"vv": vv, "vh": vh, "valido_vh": valido_vh, "ref": ref, "agua_vv": agua_vv, "terra_vv": ligada & ~leito, "dominio": dom & valido_vv & ref["vv"]["valido"] & valido_vh & ref["vh"]["valido"],
                "arquivos": k["arquivos"], "meta": k["meta"]}

    def camadas_vh(k: dict, mudanca: float, teto: float) -> dict:
        agua = agua_do_vh(k["vh"], k["valido_vh"], k["ref"]["vh"], mudanca, teto)
        ligada = ao.ligada_ao(agua, leito)
        return {"agua_total": agua, "agua_ligada_ao_rio": ligada, "inundacao_em_terra": ligada & ~leito}

    # ---- Parte A: limiares do VH escolhidos só nas cenas boas
    calib, fora = {}, []
    for r in boas_radar.itertuples():
        k = ler(r)
        if k is None:
            fora.append(f"{r.data_hora_local[:10]} (órbita {int(r.orbita_relativa)}): sem VH lido ou sem referência de rio baixo")
            continue
        if abs((k["terra_vv"] & dom & k["ref"]["vv"]["valido"]).sum() * px_km2 - r.a1_km2) > 0.02:
            AVISOS.append(f"{r.chave}: a água em terra do VV relida difere da gravada")
        calib[r.chave] = {**k, "ano": int(r.data_hora_local[:4]), "nivel": r.nivel_regua_cm}
    if fora:
        AVISOS.append("cenas boas fora da calibração: " + "; ".join(fora))
    mudancas, tetos = np.arange(ARGS.mudanca_vh[0], ARGS.mudanca_vh[1] + 1e-9, ARGS.mudanca_vh[2]), np.arange(ARGS.teto_vh[0], ARGS.teto_vh[1] + 1e-9, ARGS.teto_vh[2])
    chaves = list(calib)
    grade_iou = np.full((len(mudancas), len(tetos), len(chaves)), np.nan)
    for i, mud in enumerate(mudancas):
        for j, teto in enumerate(tetos):
            for n, ch in enumerate(chaves):
                k = calib[ch]
                grade_iou[i, j, n] = iou(camadas_vh(k, mud, teto)["inundacao_em_terra"], k["terra_vv"], k["dominio"])
        logger.info("Grade do VH: mudança %g dB feita", mud)
    anos = np.array([calib[ch]["ano"] for ch in chaves])
    media = np.nanmean(grade_iou, axis=2)
    i0, j0 = np.unravel_index(np.nanargmax(media), media.shape)
    MUD, TETO = float(mudancas[i0]), float(tetos[j0])
    a2 = pd.DataFrame([{"mudanca_db": float(m), "teto_db": float(t), "concordancia_media": float(media[i, j]), "concordancia_mediana": float(np.nanmedian(grade_iou[i, j])), "concordancia_minima": float(np.nanmin(grade_iou[i, j])),
                        "concordancia_maxima": float(np.nanmax(grade_iou[i, j])), "escolhido": bool(i == i0 and j == j0)} for i, m in enumerate(mudancas) for j, t in enumerate(tetos)])
    col_a2 = {"mudanca_db": "queda mínima do VH em relação à referência da órbita (dB)", "teto_db": "valor máximo do VH de um pixel de água (dB)", "concordancia_media": "média, nas cenas boas, da interseção/união entre a água em terra do VH e a do VV",
              "concordancia_mediana": "mediana", "concordancia_minima": "menor valor entre as cenas", "concordancia_maxima": "maior valor entre as cenas", "escolhido": "par de limiares escolhido (maior média)"}
    gravar(a2, "limiares-do-vh-grade-testada_sentinel1-rtc_2016-2026_limiar", "A2 — Grade de limiares do VH e concordância com o VV nas cenas boas", col_a2, casas={x: 3 for x in col_a2 if x.startswith("conc")},
           cenas_na_calibracao=len(chaves), cenas_boas_de_radar=int(len(boas_radar)), fora_da_calibracao=fora, limiares_do_vv={"mudanca_db": arg44.mudanca, "teto_db": arg44.vv_max})
    a3 = []
    for rot, treino in ((f"{anos.min()}–{ARGS.ano_de_corte - 1}", anos < ARGS.ano_de_corte), (f"{ARGS.ano_de_corte}–{anos.max()}", anos >= ARGS.ano_de_corte)):
        m = np.nanmean(grade_iou[:, :, treino], axis=2)
        i, j = np.unravel_index(np.nanargmax(m), m.shape)
        teste = grade_iou[i, j, ~treino]
        a3.append({"limiares_escolhidos_em": rot, "cenas_da_escolha": int(treino.sum()), "mudanca_db": float(mudancas[i]), "teto_db": float(tetos[j]), "concordancia_media_na_escolha": float(m[i, j]), "cenas_do_teste": int((~treino).sum()),
                   "concordancia_mediana_no_teste": float(np.nanmedian(teste)), "concordancia_media_no_teste": float(np.nanmean(teste)), "concordancia_minima_no_teste": float(np.nanmin(teste)),
                   "passa": bool(np.nanmedian(teste) >= ARGS.concordancia_min)})
    a3 = pd.DataFrame(a3)
    reproduz = bool(a3.passa.all())
    gravar(a3, "validacao-cruzada-do-vh_sentinel1-rtc_2016-2026_metade", "A3 — Validação cruzada dos limiares do VH por período", {
        "limiares_escolhidos_em": "período das cenas usadas para escolher os limiares", "cenas_da_escolha": "cenas boas desse período", "mudanca_db": "queda escolhida (dB)", "teto_db": "teto escolhido (dB)",
        "concordancia_media_na_escolha": "interseção/união média nas cenas da escolha", "cenas_do_teste": "cenas boas do outro período", "concordancia_mediana_no_teste": "interseção/união mediana no outro período",
        "concordancia_media_no_teste": "média no outro período", "concordancia_minima_no_teste": "menor valor no outro período", "passa": f"a mediana no teste é de pelo menos {ARGS.concordancia_min:g}"},
        casas={x: 3 for x in ("concordancia_media_na_escolha", "concordancia_mediana_no_teste", "concordancia_media_no_teste", "concordancia_minima_no_teste")}, o_vh_reproduz_o_vv=reproduz)
    if not reproduz:
        AVISOS.append("o VH não reproduz o VV na validação cruzada: segue só o diagnóstico, sem 'aceita com ressalva'")
    a4 = []
    for n, ch in enumerate(chaves):
        k = calib[ch]
        cam = camadas_vh(k, MUD, TETO)
        rio = k["ref"]["rio"]
        r = boas_radar[boas_radar.chave == ch].iloc[0]
        a4.append({"data_hora_local": r.data_hora_local, "orbita_relativa": int(r.orbita_relativa), "nivel_regua_cm": r.nivel_regua_cm, "fracao_leito_vv": r.fracao_leito_vv, "fracao_leito_vh": float((cam["agua_total"] & rio).sum() / rio.sum()),
                   "inundacao_em_terra_vv_km2": float((k["terra_vv"] & k["dominio"]).sum() * px_km2), "inundacao_em_terra_vh_km2": float((cam["inundacao_em_terra"] & k["dominio"]).sum() * px_km2), "concordancia": float(grade_iou[i0, j0, n]),
                   "vh_mediana_no_leito_db": float(np.nanmedian(k["vh"][rio])), "vv_mediana_no_leito_db": float(np.nanmedian(k["vv"][rio]))})
    a4 = pd.DataFrame(a4).sort_values("data_hora_local").reset_index(drop=True)
    a4["passa_no_controle_do_vh"] = a4.fracao_leito_vh >= arg44.fracao_minima_do_rio
    col_cena = {"data_hora_local": "data e hora local (UTC−3)", "orbita_relativa": "órbita relativa", "nivel_regua_cm": "nível da régua no dia", "fracao_leito_vv": "fração do leito principal marcada como água pelo VV (regra principal)",
                "fracao_leito_vh": "fração do leito principal marcada como água pelo VH", "inundacao_em_terra_vv_km2": "água ligada ao rio, em terra, pelo VV", "inundacao_em_terra_vh_km2": "o mesmo, pelo VH",
                "concordancia": "interseção/união entre a água em terra do VH e a do VV", "vh_mediana_no_leito_db": "mediana do VH no leito principal (dB)", "vv_mediana_no_leito_db": "mediana do VV no leito principal (dB)",
                "passa_no_controle_do_vh": f"fração do leito pelo VH de pelo menos {arg44.fracao_minima_do_rio:g}"}
    gravar(a4, "cenas-boas-vistas-pelo-vh_sentinel1-rtc_2016-2026_cena", f"A4 — Cenas boas de radar com a regra do VH (queda de {MUD:g} dB, teto de {TETO:g} dB)", col_cena,
           casas={"nivel_regua_cm": 1, "fracao_leito_vv": 3, "fracao_leito_vh": 3, "concordancia": 3}, limiares_do_vh={"mudanca_db": MUD, "teto_db": TETO}, passam=int(a4.passa_no_controle_do_vh.sum()), cenas=int(len(a4)))

    # ---- água em terra (A1) de todas as cenas boas, para a vizinha de comparação
    terra_boas = {ch: (k["terra_vv"], k["dominio"]) for ch, k in calib.items()}
    for sensor, arq in ((OPTICO, PROC44 / f"agua-observada-area-urbana_{OPTICO}_2017-2024_10m.gpkg"), (OPTICO, next(iter(sorted(PROC49.glob(f"agua-observada-cenas-novas_{OPTICO}_*_10m.gpkg"))), None)),
                        (RADAR, PROC44 / f"agua-observada-area-urbana_{RADAR}_2017-2024_10m.gpkg")):
        if arq is None or not Path(arq).exists():
            continue
        for g in gpd.read_file(arq, layer="inundacao_em_terra").itertuples():
            ch = todas[(todas.sensor == sensor) & (todas.identificador == g.identificador)].chave
            if len(ch) and ch.iloc[0] not in terra_boas:  # cena boa sem releitura (óptico; radar fora da calibração): a camada gravada, no domínio
                terra_boas[ch.iloc[0]] = (cna.rasterizar(g.geometry, forma, tr), dom)
    boas = todas[todas.boa].copy()
    quando = pd.to_datetime(boas.data_hora_local)
    curva49 = pd.read_csv(next(iter(sorted(PROC49.glob("curva-nivel-x-area-antes-e-depois_*_ponto.csv")))))
    curva49 = curva49[(curva49.conjunto == "depois") & (curva49.definicao == "A1")]
    na_curva = lambda sensor, nivel: float(np.interp(nivel, *curva49[curva49.sensor == sensor][["nivel_regua_cm", "area_ajustada_km2"]].to_numpy().T))  # noqa: E731

    # ---- Parte B: as passagens recusadas, com o VH
    b1, lidas = [], {}
    for r in testadas.itertuples():
        linha = {"data_hora_local": r.data_hora_local, "orbita_relativa": int(r.orbita_relativa), "janela": r.janela, "nivel_regua_cm": r.nivel_regua_cm, "fracao_leito_vv": r.fracao_leito_vv}
        k = ler(r)
        if k is None:
            b1.append({**linha, "classificacao": RECUSADA, "motivo_da_classificacao": "sem VH lido ou sem referência de rio baixo"})
            continue
        cam, rio, dominio = camadas_vh(k, MUD, TETO), k["ref"]["rio"], k["dominio"]
        fr = float((cam["agua_total"] & rio).sum() / rio.sum())
        a1 = float((cam["inundacao_em_terra"] & dominio).sum() * px_km2)
        linha.update(fracao_leito_vh=fr, vv_mediana_no_leito_db=float(np.nanmedian(k["vv"][rio])), vh_mediana_no_leito_db=float(np.nanmedian(k["vh"][rio])), vh_mediana_no_leito_da_referencia_db=float(np.nanmedian(k["ref"]["vh"]["db"][rio])),
                     agua_total_vh_km2=float((cam["agua_total"] & dominio).sum() * px_km2), agua_ligada_ao_rio_vh_km2=float((cam["agua_ligada_ao_rio"] & dominio).sum() * px_km2), inundacao_em_terra_vh_km2=a1,
                     agua_total_em_terra_vh_km2=float((cam["agua_total"] & ~leito & dominio).sum() * px_km2), inundacao_em_terra_vv_km2=float((k["terra_vv"] & dominio).sum() * px_km2))
        t = pd.Timestamp(r.data_hora_local)
        viz = boas.assign(dias=(quando - t).abs().dt.total_seconds() / 86400, dif_cm=(boas.nivel_regua_cm - r.nivel_regua_cm).abs())
        viz = viz[(viz.dias <= ARGS.vizinha_dias) & (viz.dif_cm <= ARGS.vizinha_cm) & viz.chave.isin(terra_boas)].sort_values(["dias", "dif_cm"])
        if len(viz):
            v = viz.iloc[0]
            terra_v, dom_v = terra_boas[v.chave]
            linha.update(vizinha=f"{v.data_hora_local[:10]} ({NOME[v.sensor]})", vizinha_dias=float(v.dias), vizinha_nivel_cm=float(v.nivel_regua_cm), vizinha_inundacao_em_terra_km2=float(v.a1_km2),
                         iou_com_vizinha=iou(cam["inundacao_em_terra"], terra_v, dominio & dom_v))
            confere, como = bool(linha["iou_com_vizinha"] >= ARGS.concordancia_min), f"interseção/união com a vizinha de {linha['iou_com_vizinha']:.3f}".replace(".", ",")
        else:
            esperado = {"radar": na_curva("radar", r.nivel_regua_cm), "os dois": na_curva("os dois", r.nivel_regua_cm)}
            dif = {s: 100 * (a1 - e) / e if e else np.nan for s, e in esperado.items()}
            linha.update(vizinha="", curva_radar_no_nivel_km2=esperado["radar"], diferenca_para_a_curva_radar_pct=dif["radar"], curva_os_dois_no_nivel_km2=esperado["os dois"], diferenca_para_a_curva_os_dois_pct=dif["os dois"])
            confere = bool(all(abs(x) <= ARGS.tolerancia_da_curva_pct for x in dif.values()))
            como = f"sem vizinha; área a {dif['radar']:+.0f} % da curva do radar e {dif['os dois']:+.0f} % da dos dois sensores".replace(".", ",")
        aceita = reproduz and fr >= arg44.fracao_minima_do_rio and confere
        porque = [] if aceita else ([f"fração do leito pelo VH de {fr:.3f}, abaixo de {arg44.fracao_minima_do_rio:g}".replace(".", ",")] if fr < arg44.fracao_minima_do_rio else []) + ([como + (", abaixo do mínimo" if len(viz) else ", fora da tolerância")] if not confere else []) \
            + ([] if reproduz else ["o VH não reproduz o VV na validação cruzada"])
        linha.update(classificacao=ACEITA if aceita else RECUSADA, motivo_da_classificacao=("fração do leito pelo VH de " + f"{fr:.3f}".replace(".", ",") + "; " + como) if aceita else "; ".join(porque))
        b1.append(linha)
        lidas[r.chave] = {**k, **cam, "linha": linha, "r": r}
    ordem = ["data_hora_local", "orbita_relativa", "janela", "nivel_regua_cm", "fracao_leito_vv", "fracao_leito_vh", "vv_mediana_no_leito_db", "vh_mediana_no_leito_db", "vh_mediana_no_leito_da_referencia_db", "agua_total_vh_km2",
             "agua_ligada_ao_rio_vh_km2", "inundacao_em_terra_vh_km2", "agua_total_em_terra_vh_km2", "inundacao_em_terra_vv_km2", "vizinha", "vizinha_dias", "vizinha_nivel_cm", "vizinha_inundacao_em_terra_km2", "iou_com_vizinha",
             "curva_radar_no_nivel_km2", "diferenca_para_a_curva_radar_pct", "curva_os_dois_no_nivel_km2", "diferenca_para_a_curva_os_dois_pct", "classificacao", "motivo_da_classificacao"]
    b1 = pd.DataFrame(b1).reindex(columns=ordem)
    b1["destaque"] = b1.data_hora_local.str[:10].isin(ARGS.destaques)
    col_b = {**col_cena, "janela": "período de busca ou episódio de cheia da cena", "vh_mediana_no_leito_da_referencia_db": "mediana do VH no leito principal na referência de rio baixo da órbita (dB)",
             "agua_total_vh_km2": "água da cena pelo VH, na área urbana, lado brasileiro", "agua_ligada_ao_rio_vh_km2": "água do VH em grupos que tocam o leito de referência", "agua_total_em_terra_vh_km2": "toda a água do VH fora do leito de referência (A2)",
             "inundacao_em_terra_vv_km2": "água em terra que o VV daria (a cena é recusada pela regra principal)", "vizinha": f"cena boa mais próxima no tempo, a até {ARGS.vizinha_dias:g} dias e {ARGS.vizinha_cm:g} cm",
             "vizinha_dias": "distância no tempo até a vizinha (dias)", "vizinha_nivel_cm": "nível da régua no dia da vizinha", "vizinha_inundacao_em_terra_km2": "água em terra da vizinha",
             "iou_com_vizinha": "interseção/união entre a água em terra do VH e a da vizinha", "curva_radar_no_nivel_km2": "sem vizinha: valor da curva nível × área do radar (A1) no nível da cena",
             "diferenca_para_a_curva_radar_pct": "sem vizinha: (área do VH − curva do radar) / curva, em %", "curva_os_dois_no_nivel_km2": "sem vizinha: valor da curva dos dois sensores (A1)",
             "diferenca_para_a_curva_os_dois_pct": "sem vizinha: diferença para a curva dos dois sensores, em %", "classificacao": f"'{ACEITA}' ou '{RECUSADA}'", "motivo_da_classificacao": "por quê", "destaque": "cena destacada"}
    gravar(b1, "passagens-recusadas-vistas-pelo-vh_sentinel1-rtc_2016-2026_cena", "B — Passagens recusadas por rio sem contraste, vistas pelo VH", col_b,
           casas={"nivel_regua_cm": 1, "vizinha_nivel_cm": 1, "fracao_leito_vv": 3, "fracao_leito_vh": 3, "iou_com_vizinha": 3, "diferenca_para_a_curva_radar_pct": 0, "diferenca_para_a_curva_os_dois_pct": 0},
           limiares_do_vh={"mudanca_db": MUD, "teto_db": TETO}, regra_da_classificacao=f"aceita: fração do leito pelo VH ≥ {arg44.fracao_minima_do_rio:g} e (interseção/união com a vizinha ≥ {ARGS.concordancia_min:g} ou, sem vizinha, "
           f"área a ±{ARGS.tolerancia_da_curva_pct:g} % das duas curvas); senão, continua recusada", o_vh_reproduz_o_vv=reproduz)
    aceitas = b1[b1.classificacao == ACEITA]

    # ---- Parte C: curva de sensibilidade (boas + aceitas com ressalva)
    antigas = pd.read_csv(next(iter(sorted(PROC45.glob("fase-da-cheia-por-cena_*_cena.csv")))))[["sensor", "nivel_regua_cm", "a1_km2", "a2_km2"]]
    nov = b[b.boa.astype(bool)]
    principal = pd.concat([antigas, pd.DataFrame({"sensor": nov.sensor, "nivel_regua_cm": nov.regua_cm, "a1_km2": nov.inundacao_em_terra_km2, "a2_km2": nov.agua_total_em_terra_km2})], ignore_index=True)
    sensib = pd.concat([principal, pd.DataFrame({"sensor": RADAR, "nivel_regua_cm": aceitas.nivel_regua_cm, "a1_km2": aceitas.inundacao_em_terra_vh_km2, "a2_km2": aceitas.agua_total_em_terra_vh_km2})], ignore_index=True)
    manchas = ec.carregar_cenarios(ee.ARQ_COTAS, ao.CAMADA_COTAS, ao.ATRIBUTO_COTAS)
    geo_m, extraidas = {int(k): g for k, g in zip(manchas.valor, manchas.geometry)}, {}
    for item in ARGS.mancha_extra or []:  # camada de fora do repositório, extraída de figura: só leitura, só comparação
        cota, resto = item.split("=", 1)
        caminho, _, camada = resto.rpartition(":") if resto.count(":") and not resto.endswith(".gpkg") else (resto, "", None)
        if not Path(caminho).exists():
            AVISOS.append(f"mancha de {cota} cm ausente: comparação feita sem ela")
            continue
        geo_m[int(cota)] = gpd.read_file(caminho, layer=camada).to_crs(c.CRS_PADRAO).geometry.buffer(0).union_all()
        extraidas[int(cota)] = f"{ao.fmt(int(cota))[:-3]}.{ao.fmt(int(cota))[-3:]} cm: extraída de figura, não conferida"
    nota_extra = ("; ".join(extraidas.values()) + ".") if extraidas else ""
    sgb = {k: float((cna.rasterizar(g, forma, tr) & ~leito & dom).sum() * px_km2) for k, g in sorted(geo_m.items())}
    c1, faixas, curvas = [], [], {}
    for nome in ARGS.sensores_da_curva:
        sens = cna.SENSORES[nome]
        for d in cna.DEFINICOES:
            col, fits = f"{d.lower()}_km2", {}
            for rot, t in (("principal", principal), ("sensibilidade", sensib)):
                k = t[t.sensor.isin(sens)]
                xs, ys, _ = cna.isotonica(k.nivel_regua_cm.to_numpy(), k[col].to_numpy())
                fits[rot] = (xs, ys, k)
            if nome == "os dois" and d == "A1":
                curvas = {rot: (v[0], v[1]) for rot, v in fits.items()}
            for cota, area in sgb.items():
                linha = {"cota_cm": cota, "mancha_em_terra_km2": area, "sensor": nome, "definicao": d}
                for rot, (xs, ys, k) in fits.items():
                    nivel, situacao = cna.nivel_em_que_atinge(xs, ys, area)
                    linha.update({f"cenas_{rot}": len(k), f"nivel_equivalente_{rot}_cm": nivel, f"situacao_{rot}": situacao})
                linha["mudanca_cm"] = linha["nivel_equivalente_sensibilidade_cm"] - linha["nivel_equivalente_principal_cm"]
                linha["marca"] = extraidas.get(cota, "")
                c1.append(linha)
            if d == "A1":
                for lo in np.arange(ARGS.faixa_inicio, ARGS.faixa_fim, ARGS.faixa_cm):
                    conta = {rot: int(((k.nivel_regua_cm >= lo) & (k.nivel_regua_cm < lo + ARGS.faixa_cm)).sum()) for rot, (_, _, k) in fits.items()}
                    faixas.append({"sensor": nome, "faixa_de_nivel_cm": f"{lo:g}–{lo + ARGS.faixa_cm:g}", "cenas_principal": conta["principal"], "cenas_sensibilidade": conta["sensibilidade"]})
    c1, faixas = pd.DataFrame(c1), pd.DataFrame(faixas)
    gravar(c1, "nivel-equivalente-das-manchas-principal-e-sensibilidade_sentinel-sgb_2016-2026_cota", "C1 — Nível equivalente de cada mancha do SGB: curva principal e curva de sensibilidade (com as aceitas com ressalva)", {
        "cota_cm": "cota da mancha acumulada do SGB", "mancha_em_terra_km2": "mancha em terra no domínio", "sensor": "cenas usadas na curva", "definicao": "A1 = água ligada ao rio; A2 = toda a água em terra",
        "cenas_principal": "cenas na curva principal", "nivel_equivalente_principal_cm": "nível em que a curva principal chega à área da mancha", "situacao_principal": "onde a área da mancha cai na curva principal",
        "cenas_sensibilidade": "cenas na curva de sensibilidade", "nivel_equivalente_sensibilidade_cm": "o mesmo, na curva de sensibilidade", "situacao_sensibilidade": "onde a área cai na curva de sensibilidade",
        "mudanca_cm": "sensibilidade menos principal", "marca": "ressalva sobre a mancha"}, casas={"nivel_equivalente_principal_cm": 0, "nivel_equivalente_sensibilidade_cm": 0, "mudanca_cm": 0}, nota=nota_extra,
        aviso="a curva de sensibilidade não substitui a principal", aceitas_com_ressalva=int(len(aceitas)))
    gravar(faixas, "cenas-por-faixa-de-nivel-principal-e-sensibilidade_sentinel-ana_2016-2026_faixa", f"C1 — Cenas por faixa de nível de {ARGS.faixa_cm:g} cm, a partir de {ARGS.faixa_inicio:g} cm", {
        "sensor": "cenas usadas", "faixa_de_nivel_cm": "faixa do nível da régua (limite de baixo incluído)", "cenas_principal": "cenas boas na faixa", "cenas_sensibilidade": "cenas boas mais as aceitas com ressalva"})
    if ARGS.figuras:
        ARGS.figuras.mkdir(parents=True, exist_ok=True)
        figura(ARGS.figuras / "nivel-x-area-com-o-teste-do-vh_sentinel-sgb_2016-2026.png", principal.assign(nivel_regua_cm=principal.nivel_regua_cm), b1.dropna(subset=["inundacao_em_terra_vh_km2"]), curvas)

    # ---- Parte D: água e imagens das aceitas com ressalva (à parte)
    geo = {k: [] for k in ao.CAMADAS}
    indice = []
    if len(aceitas) and ARGS.imagens:
        ARGS.imagens.mkdir(parents=True, exist_ok=True)
        im.ARGS = argparse.Namespace(piramides=ARGS.piramides)
        im.SCRIPT, im.MOTIVO = SCRIPT, MOTIVO  # o .json das imagens diz que elas vêm deste teste
        borda = int(round(ARGS.margem_m / RES))
        gradem = {"transform": from_origin(gx0 - borda * RES, gy1 + borda * RES, RES, RES), "width": grade["width"] + 2 * borda, "height": grade["height"] + 2 * borda}  # a grade das imagens para SIG: a mesma, com a margem
        cru = lambda arq: (lambda v: np.where(np.isfinite(v) & (v > 0), 10 * np.log10(np.where(v > 0, v, 1)), np.nan).astype("float32"))(ao.para_a_grade(arq, gradem, Resampling.nearest, np.nan))  # noqa: E731
        diaria, tel = ao.ler_serie_diaria(), ao.ler_telemetria()
        s45 = diaria.valor_do_dia_cm[diaria.index.year != 2015].asfreq("D")  # a série e a regra de fase da curva nível × área
        pico45 = s45.notna() & (s45 == s45.rolling(7, center=True, min_periods=1).max())
        ref_cru = {}
    for ch, k in lidas.items():
        if k["linha"]["classificacao"] != ACEITA:
            continue
        r, linha = k["r"], k["linha"]
        local = pd.Timestamp(r.data_hora_local)
        for cam in ao.CAMADAS:
            geo[cam].append({"identificador": r.identificador, "data": f"{local:%Y-%m-%d}", "hora_local": f"{local:%H:%M}", "nivel_regua_cm": r.nivel_regua_cm, "orbita_relativa": int(r.orbita_relativa),
                             "limiar": f"VH: mudança <= {MUD:g} dB e VH <= {TETO:g} dB, ou água na referência e VH <= {TETO:g} dB", "controle_de_qualidade": f"leito principal marcado como água pelo VH: {100 * linha['fracao_leito_vh']:.1f} %; {RESSALVA}",
                             "area_km2": float((k[cam] & k["dominio"]).sum() * px_km2), "geometry": ao.poligonos(k[cam] & dentro, grade)})
        if not ARGS.imagens:
            continue
        orb = int(r.orbita_relativa)
        if orb not in ref_cru:
            ref_cru[orb] = cru(cn.brutos_por_identificador()[(RADAR, k["ref"]["cena"])]["arquivos"]["vh"])
        vh = cru(k["arquivos"]["vh"])
        n = im.niveis(local, diaria, tel)
        fase = r.fase if isinstance(getattr(r, "fase", None), str) and r.fase else cna.fase_da_cheia(local.normalize(), s45, pico45)["fase"]
        hora = r.regua_cm_hora if pd.notna(getattr(r, "regua_cm_hora", np.nan)) else n["nivel_mais_proximo_da_hora_cm"]
        comum = {"situacao": RESSALVA, "fonte": "Microsoft Planetary Computer — coleção sentinel-1-rtc (dados Copernicus Sentinel-1)", "licenca": "CC-BY-4.0", "identificador": r.identificador, "data_hora_local": r.data_hora_local,
                 "orbita_relativa": orb, "nivel_media_diaria_cm": float(r.nivel_regua_cm), "nivel_mais_proximo_da_hora_cm": hora, "fracao_do_leito_pelo_vv": float(r.fracao_leito_vv), "fracao_do_leito_pelo_vh": linha["fracao_leito_vh"],
                 "vizinha_de_comparacao": linha.get("vizinha") or None, "intersecao_sobre_uniao_com_a_vizinha": linha.get("iou_com_vizinha"), "limiares_do_vh": {"mudanca_db": MUD, "teto_db": TETO},
                 "arquivos_de_origem": [rel(k["arquivos"]["vh"])]}
        for tipo, dados, bandas, corte in (("vh-db", vh, "VH, retroespalhamento gama zero corrigido do terreno, em dB (10·log10)", "nenhum: valores em dB; sugestão de exibição de −30 a −10 dB"),
                                           ("diferenca-vh-db", vh - ref_cru[orb], f"VH da cena menos VH da referência de rio baixo da órbita ({k['ref']['data']}), em dB (negativo = escureceu)", "nenhum: valores em dB; sugestão de exibição de −10 a +10 dB")):
            arq = ARGS.imagens / f"imagem-radar-{tipo}_sentinel1-rtc_{local:%Y-%m-%d}_10m.tif"
            im.gravar_tif(arq, np.where(np.isfinite(dados), dados, im.SEM_DADO_RADAR).astype("float32"), gradem, im.SEM_DADO_RADAR, bandas=[bandas], cortes_de_esticamento=corte, **comum)
            indice.append({"arquivo": arq.name, "tipo": tipo, "sensor": RADAR, "data_local": f"{local:%Y-%m-%d}", "hora_local": f"{local:%H:%M}", "orbita": orb, "janela": r.janela, "regua_cm": r.nivel_regua_cm, "regua_cm_hora": hora, "fase": fase,
                           "referencia": "não", "agua_em_terra_km2": linha["inundacao_em_terra_vh_km2"], "nuvem_sombra_pct": np.nan, "fracao_leito_vv": r.fracao_leito_vv, "fracao_leito_vh": linha["fracao_leito_vh"], "iou_com_vizinha": linha.get("iou_com_vizinha", np.nan)})
    if geo[ao.CAMADAS[0]]:
        arq = PROC / "agua-do-vh-aceitas-com-ressalva_sentinel1-rtc_2016-2026_10m.gpkg"
        for cam, linhas in geo.items():
            g = gpd.GeoDataFrame(linhas, crs=c.CRS_PADRAO)
            g[g.geometry.notna()].to_file(arq, driver="GPKG", layer=cam)
        meta(arq, descricao="água pelo VH das passagens aceitas com ressalva, na área urbana: três camadas, uma linha por cena; polígonos de pixel, sem simplificação", camadas=list(ao.CAMADAS), cenas=len(geo[ao.CAMADAS[0]]), situacao=RESSALVA,
             colunas={"identificador": "cena", "data": "data local", "hora_local": "hora local (UTC−3)", "nivel_regua_cm": "nível da régua no dia", "orbita_relativa": "órbita relativa", "limiar": "regra da água (VH)",
                      "controle_de_qualidade": "resultado do controle de qualidade do VH e a ressalva", "area_km2": "área da camada na área urbana, lado brasileiro"})
    if indice:
        arq = ARGS.imagens / "indice_imagens_ressalva.csv"
        pd.DataFrame(indice).sort_values(["tipo", "data_local"]).to_csv(arq, index=False)
        c.gravar_meta(arq, status=c.STATUS_CONFERENCIA, ligado_ao_portal=False, script=SCRIPT, motivo=MOTIVO, situacao=RESSALVA, imagens=len(indice), limiares_do_vh={"mudanca_db": MUD, "teto_db": TETO},
                      descricao="uma linha por imagem; mesmas colunas do índice das imagens para SIG, mais a fração do leito pelo VV e pelo VH e a interseção/união com a vizinha")
    if ARGS.copia:
        ARGS.copia.mkdir(parents=True, exist_ok=True)
        for arq in sorted(PROC.iterdir()):
            shutil.copy2(arq, ARGS.copia / arq.name)
    print(json.dumps({"cenas_boas_de_radar": int(len(boas_radar)), "na_calibracao": len(chaves), "fora_da_calibracao": fora, "testadas": int(len(testadas)), "limiares_do_vh": {"mudanca_db": MUD, "teto_db": TETO},
                      "concordancia": {"media": float(media[i0, j0]), "mediana": float(np.nanmedian(grade_iou[i0, j0])), "minima": float(np.nanmin(grade_iou[i0, j0])), "maxima": float(np.nanmax(grade_iou[i0, j0]))},
                      "validacao_cruzada": a3.to_dict("records"), "o_vh_reproduz_o_vv": reproduz, "boas_que_passam_no_controle_do_vh": int(a4.passa_no_controle_do_vh.sum()),
                      "classificacao": b1.classificacao.value_counts().to_dict(), "imagens": len(indice), "avisos": AVISOS}, ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", force=True)
    _p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    _p.add_argument("--motivo-testado", default="rio sem contraste", help="motivo de recusa das passagens de radar que entram no teste")
    _p.add_argument("--mudanca-vh", type=float, nargs=3, default=[-6.0, 3.0, 0.5], metavar=("DE", "ATE", "PASSO"), help="grade da queda do VH em relação à referência (dB; valor positivo deixa a queda sem efeito)")
    _p.add_argument("--teto-vh", type=float, nargs=3, default=[-27.0, -17.0, 1.0], metavar=("DE", "ATE", "PASSO"), help="grade do teto do VH de um pixel de água (dB)")
    _p.add_argument("--ano-de-corte", type=int, default=2023, help="validação cruzada: as cenas até o ano anterior a este formam uma metade; as demais, a outra")
    _p.add_argument("--concordancia-min", type=float, default=0.70, help="interseção/união mínima: na validação cruzada (mediana) e com a vizinha")
    _p.add_argument("--vizinha-dias", type=float, default=3.0, help="a vizinha de comparação fica a até estes dias")
    _p.add_argument("--vizinha-cm", type=float, default=30.0, help="a vizinha de comparação tem nível da régua a até isto (cm)")
    _p.add_argument("--tolerancia-da-curva-pct", type=float, default=20.0, help="sem vizinha: a área do VH tem de ficar a mais ou menos isto das curvas (%%)")
    _p.add_argument("--destaques", nargs="*", default=["2024-05-14", "2026-10-08"], metavar="AAAA-MM-DD", help="cenas destacadas na tabela e na figura")
    _p.add_argument("--sensores-da-curva", nargs="+", default=["radar", "os dois"], help="curvas da sensibilidade")
    _p.add_argument("--faixa-inicio", type=float, default=1000.0, help="início das faixas de nível da contagem (cm)")
    _p.add_argument("--faixa-fim", type=float, default=1300.0, help="fim das faixas de nível (cm)")
    _p.add_argument("--faixa-cm", type=float, default=50.0, help="largura das faixas de nível (cm)")
    _p.add_argument("--margem-m", type=float, default=500.0, help="margem das imagens em volta do retângulo da área urbana (m)")
    _p.add_argument("--piramides", type=int, nargs="+", default=[2, 4, 8], help="fatores das pirâmides internas das imagens")
    _p.add_argument("--mancha-extra", nargs="*", help="COTA=ARQUIVO.gpkg:CAMADA de mancha acumulada de fora do repositório (extraída de figura, não conferida): só leitura")
    _p.add_argument("--imagens", type=Path, help="pasta (fora do repositório, à parte) das imagens das aceitas com ressalva")
    _p.add_argument("--figuras", type=Path, help="pasta (fora do repositório) da figura")
    _p.add_argument("--copia", type=Path, help="pasta que recebe uma cópia dos produtos")
    _p.add_argument("--refazer", action="store_true", help="gera de novo mesmo se a pasta de saída já tiver arquivos")
    ARGS = _p.parse_args()
    FONTES, AVISOS = [], []
    for _pasta in (ARGS.figuras, ARGS.copia, ARGS.imagens):
        if _pasta is not None and c.RAIZ in _pasta.resolve().parents:
            raise SystemExit("--figuras, --copia e --imagens têm de ficar fora do repositório")
    main()
