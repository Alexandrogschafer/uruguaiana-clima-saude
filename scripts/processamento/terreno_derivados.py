"""
Gera os produtos derivados do MDT ANADEM (declividade, relevo sombreado e
mapa hipsométrico), recortados pela área de estudo MUNICIPAL (não a
estendida usada só para buscar o MDT/BHO — ver scripts/download/
terreno_anadem.py e scripts/processamento/area_estudo_bacias.py):

    data/processed/declividade_anadem_atual_30m.tif   (2 bandas: graus, %)
    data/processed/hillshade_anadem_atual_30m.tif
    data/processed/hipsometria_anadem_atual_30m.tif   (classes de altitude)

Entrada: data/raw/raster/mdt_anadem_atual_30m.tif (gerado por
scripts/download/terreno_anadem.py).

Declividade e hillshade via `gdaldem`
--------------------------------------
Calculados com `gdaldem slope`/`gdaldem hillshade` (algoritmo de Horn, o
padrão de facto em SIG) em vez de reimplementar o gradiente em numpy — é a
implementação de referência, já testada, e evita divergência sutil de
convenção de borda/vizinhança em relação ao que qualquer usuário do
projeto obteria abrindo o mesmo MDT no QGIS.

Intervalo hipsométrico
-----------------------
A amplitude altimétrica real da área (confirmada no MDT recortado) é da
ordem de 30-300 m — região de planície/pampa, sem relevo acentuado.
Intervalo escolhido: 20 m (ajustável via --intervalo-m). Um intervalo
maior (50 m) resultaria em poucas classes (~5-6) e esconderia a variação
sutil de cota que é justamente o que importa aqui — inclusive para leitura
cruzada com as cotas de inundação do SGB (cotas_inundacao no catálogo do
projeto), onde diferenças de poucos metros mudam a área afetada.

Idempotente: se todas as saídas já existirem, não reprocessa (a menos que
--forcar seja usado).

Uso:
    python scripts/processamento/terreno_derivados.py
    python scripts/processamento/terreno_derivados.py --intervalo-m 25 --forcar
"""

import argparse
import json
import logging
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import rasterio

sys.path.append(str(Path(__file__).resolve().parents[1] / "utils"))
from recorte_municipio import CRS_PADRAO, carregar_area_estudo, recortar_raster  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

RAIZ = Path(__file__).resolve().parents[2]
CAMINHO_MDT = RAIZ / "data" / "raw" / "raster" / "mdt_anadem_atual_30m.tif"
CAMINHO_DECLIVIDADE = RAIZ / "data" / "processed" / "declividade_anadem_atual_30m.tif"
CAMINHO_HILLSHADE = RAIZ / "data" / "processed" / "hillshade_anadem_atual_30m.tif"
CAMINHO_HIPSOMETRIA = RAIZ / "data" / "processed" / "hipsometria_anadem_atual_30m.tif"

INTERVALO_M_DEFAULT = 20

PARAMS_HILLSHADE = {"azimute": 315, "altitude": 45, "fator_z": 1}


def recortar_mdt_municipal(tmpdir: Path) -> Path:
    if not CAMINHO_MDT.exists():
        raise FileNotFoundError(f"{CAMINHO_MDT} não encontrado. Rode primeiro: python scripts/download/terreno_anadem.py")
    area_estudo = carregar_area_estudo()
    caminho_mdt_municipal = tmpdir / "mdt_municipal.tif"
    recortar_raster(CAMINHO_MDT, caminho_mdt_municipal, area_estudo)
    return caminho_mdt_municipal


def calcular_declividade(caminho_mdt_municipal: Path, tmpdir: Path) -> Path:
    caminho_graus = tmpdir / "slope_graus.tif"
    caminho_pct = tmpdir / "slope_pct.tif"

    subprocess.run(
        ["gdaldem", "slope", str(caminho_mdt_municipal), str(caminho_graus), "-compute_edges"],
        check=True, capture_output=True, text=True,
    )
    subprocess.run(
        ["gdaldem", "slope", str(caminho_mdt_municipal), str(caminho_pct), "-p", "-compute_edges"],
        check=True, capture_output=True, text=True,
    )

    with rasterio.open(caminho_graus) as src_graus, rasterio.open(caminho_pct) as src_pct:
        perfil = src_graus.profile.copy()
        perfil.update(count=2)
        if not perfil.get("tiled"):
            perfil.pop("blockxsize", None)
            perfil.pop("blockysize", None)
        CAMINHO_DECLIVIDADE.parent.mkdir(parents=True, exist_ok=True)
        with rasterio.open(CAMINHO_DECLIVIDADE, "w", **perfil) as dst:
            dst.write(src_graus.read(1), 1)
            dst.write(src_pct.read(1), 2)
            dst.set_band_description(1, "declividade_graus")
            dst.set_band_description(2, "declividade_percentual")

    logger.info("Declividade salva em %s (banda 1: graus, banda 2: %%)", CAMINHO_DECLIVIDADE)
    return CAMINHO_DECLIVIDADE


def calcular_hillshade(caminho_mdt_municipal: Path) -> Path:
    CAMINHO_HILLSHADE.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "gdaldem", "hillshade", str(caminho_mdt_municipal), str(CAMINHO_HILLSHADE),
            "-az", str(PARAMS_HILLSHADE["azimute"]), "-alt", str(PARAMS_HILLSHADE["altitude"]),
            "-z", str(PARAMS_HILLSHADE["fator_z"]), "-compute_edges",
        ],
        check=True, capture_output=True, text=True,
    )
    logger.info("Hillshade salvo em %s", CAMINHO_HILLSHADE)
    return CAMINHO_HILLSHADE


def calcular_hipsometria(caminho_mdt_municipal: Path, intervalo_m: float) -> tuple[Path, list]:
    with rasterio.open(caminho_mdt_municipal) as src:
        mdt = src.read(1, masked=True)
        perfil = src.profile.copy()

    elev_min = float(np.floor(mdt.min() / intervalo_m) * intervalo_m)
    elev_max = float(np.ceil(mdt.max() / intervalo_m) * intervalo_m)
    limites = np.arange(elev_min, elev_max + intervalo_m, intervalo_m)

    classes = np.digitize(mdt.filled(np.nan), limites[1:-1], right=False).astype("uint8")
    classes = np.ma.masked_array(classes, mask=mdt.mask)

    legenda = [
        {"classe": i, "altitude_min_m": round(float(limites[i]), 1), "altitude_max_m": round(float(limites[i + 1]), 1)}
        for i in range(len(limites) - 1)
    ]

    perfil.update(dtype="uint8", nodata=255, count=1)
    CAMINHO_HIPSOMETRIA.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(CAMINHO_HIPSOMETRIA, "w", **perfil) as dst:
        dst.write(classes.filled(255), 1)

    logger.info("Hipsometria salva em %s (%d classes de %d m, %.1f-%.1f m)",
                CAMINHO_HIPSOMETRIA, len(legenda), intervalo_m, elev_min, elev_max)
    return CAMINHO_HIPSOMETRIA, legenda


def montar_metadados_base(descricao: str, transformacao: str, extra: dict | None = None) -> dict:
    metadados = {
        "descricao": descricao,
        "script_gerador": "scripts/processamento/terreno_derivados.py",
        "entrada": str(CAMINHO_MDT.relative_to(RAIZ)),
        "entrada_gerada_por": "scripts/download/terreno_anadem.py",
        "area_recorte": "config/area_estudo.geojson (área de estudo municipal — não a estendida)",
        "crs": CRS_PADRAO,
        "transformacao_aplicada": transformacao,
        "data_processamento": datetime.now(timezone.utc).isoformat(),
    }
    if extra:
        metadados.update(extra)
    return metadados


def salvar_metadados(caminho_raster: Path, metadados: dict) -> None:
    caminho_raster.with_suffix(".json").write_text(
        json.dumps(metadados, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    logger.info("Metadados salvos em %s", caminho_raster.with_suffix(".json"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Gera declividade, hillshade e hipsometria a partir do MDT ANADEM.")
    parser.add_argument("--intervalo-m", type=float, default=INTERVALO_M_DEFAULT,
                         help="Intervalo (m) das classes hipsométricas (default: 20)")
    parser.add_argument("--forcar", action="store_true", help="Reprocessa mesmo se as saídas já existirem")
    args = parser.parse_args()

    saidas = [CAMINHO_DECLIVIDADE, CAMINHO_HILLSHADE, CAMINHO_HIPSOMETRIA]
    if all(p.exists() for p in saidas) and not args.forcar:
        logger.info("Produtos derivados do MDT já existem — nada a fazer (use --forcar para refazer).")
        return

    with tempfile.TemporaryDirectory(prefix="terreno_derivados_") as tmpdir_str:
        tmpdir = Path(tmpdir_str)
        caminho_mdt_municipal = recortar_mdt_municipal(tmpdir)

        with rasterio.open(caminho_mdt_municipal) as src:
            mdt_stats = src.read(1, masked=True)
            elev_min, elev_max = float(mdt_stats.min()), float(mdt_stats.max())

        calcular_declividade(caminho_mdt_municipal, tmpdir)
        salvar_metadados(CAMINHO_DECLIVIDADE, montar_metadados_base(
            descricao="Declividade (slope) derivada do MDT ANADEM, em graus (banda 1) e percentual (banda 2).",
            transformacao=(
                "gdaldem slope (algoritmo de Horn, -compute_edges) sobre o MDT recortado pela área municipal; "
                "banda 1 em graus (default), banda 2 em percentual (-p), combinadas num único GeoTIFF de 2 bandas"
            ),
            extra={"algoritmo": "Horn (gdaldem slope, GDAL 3.12)", "bandas": {"1": "declividade_graus", "2": "declividade_percentual"}},
        ))

        calcular_hillshade(caminho_mdt_municipal)
        salvar_metadados(CAMINHO_HILLSHADE, montar_metadados_base(
            descricao="Relevo sombreado (hillshade) derivado do MDT ANADEM.",
            transformacao=f"gdaldem hillshade (-compute_edges) sobre o MDT recortado pela área municipal, parâmetros {PARAMS_HILLSHADE}",
            extra={"parametros": PARAMS_HILLSHADE},
        ))

        _, legenda = calcular_hipsometria(caminho_mdt_municipal, args.intervalo_m)
        salvar_metadados(CAMINHO_HIPSOMETRIA, montar_metadados_base(
            descricao=(
                "Mapa hipsométrico (classes de altitude) derivado do MDT ANADEM. Intervalo de "
                f"{args.intervalo_m} m escolhido pela pequena amplitude altimétrica real da área "
                f"({elev_min:.1f}-{elev_max:.1f} m — região de planície/pampa)."
            ),
            transformacao=f"classificação por faixas de altitude fixas de {args.intervalo_m} m (numpy.digitize) sobre o MDT recortado pela área municipal",
            extra={
                "intervalo_m": args.intervalo_m,
                "elevacao_min_m": round(elev_min, 1),
                "elevacao_max_m": round(elev_max, 1),
                "n_classes": len(legenda),
                "legenda_classes": legenda,
                "nodata": 255,
            },
        ))

    logger.info("Produtos derivados do MDT concluídos: %s, %s, %s", CAMINHO_DECLIVIDADE, CAMINHO_HILLSHADE, CAMINHO_HIPSOMETRIA)


if __name__ == "__main__":
    main()
