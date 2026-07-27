"""
Plot rápido (matplotlib) das 4 camadas de cota de inundação do rio Uruguai
em Uruguaiana, sobrepostas e coloridas por tempo de retorno (TR).

Lê o vetor consolidado gerado por scripts/download/hidrologia_sgb.py e salva
uma figura estática em docs/. Não é uma camada de produção — é uma checagem
visual rápida do dado baixado.

Uso:
    python scripts/processamento/plot_cotas_inundacao.py
"""

import logging
from pathlib import Path

import geopandas as gpd
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

RAIZ = Path(__file__).resolve().parents[2]
CAMINHO_COTAS = RAIZ / "data" / "raw" / "vetor" / "cotas-inundacao_sgb_atual_vetorial.gpkg"
CAMINHO_MUNICIPIO = RAIZ / "config" / "area_estudo.geojson"
CAMINHO_SAIDA = RAIZ / "docs" / "cotas-inundacao_sgb.png"

# Rampa sequencial de 1 hue (azul), passo ordinal claro->escuro — cada cota
# de inundação é uma categoria ordenada por magnitude (cota_cm / TR), não
# uma identidade sem ordem, por isso sequencial e não categórico.
RAMPA_SEQUENCIAL = ["#86b6ef", "#3987e5", "#1c5cab", "#0d366b"]

COR_SURFACE = "#fcfcfb"
COR_INK_PRIMARIA = "#0b0b0b"
COR_INK_SECUNDARIA = "#52514e"
COR_MUTED = "#898781"
COR_MUNICIPIO = "#c3c2b7"


def carregar_dados() -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame | None]:
    if not CAMINHO_COTAS.exists():
        raise FileNotFoundError(
            f"{CAMINHO_COTAS} não encontrado. Rode primeiro: python scripts/download/hidrologia_sgb.py"
        )
    gdf_cotas = gpd.read_file(CAMINHO_COTAS)

    gdf_municipio = None
    if CAMINHO_MUNICIPIO.exists():
        gdf_municipio = gpd.read_file(CAMINHO_MUNICIPIO)
        if gdf_municipio.crs != gdf_cotas.crs:
            gdf_municipio = gdf_municipio.to_crs(gdf_cotas.crs)
    return gdf_cotas, gdf_municipio


def plotar(gdf_cotas: gpd.GeoDataFrame, gdf_municipio: gpd.GeoDataFrame | None) -> None:
    cotas_ordenadas = sorted(gdf_cotas["cota_cm"].unique())
    cores = dict(zip(cotas_ordenadas, RAMPA_SEQUENCIAL))

    fig, ax = plt.subplots(figsize=(9, 8), facecolor=COR_SURFACE)
    ax.set_facecolor(COR_SURFACE)

    if gdf_municipio is not None:
        gdf_municipio.boundary.plot(ax=ax, color=COR_MUNICIPIO, linewidth=0.8, zorder=1)

    # Desenha da maior cota (mais escura, extensão maior) para a menor
    # (mais clara, extensão menor) — as cotas são aninhadas (cada TR maior
    # contém as áreas dos TR menores), então essa ordem revela os "anéis"
    # de cada faixa em vez de esconder as camadas menores por baixo.
    for i, cota_cm in enumerate(sorted(cotas_ordenadas, reverse=True)):
        subset = gdf_cotas[gdf_cotas["cota_cm"] == cota_cm]
        subset.plot(ax=ax, color=cores[cota_cm], edgecolor="white", linewidth=0.5, zorder=2 + i)

    tr_por_cota = gdf_cotas.drop_duplicates("cota_cm").set_index("cota_cm")["tr_anos"]
    legenda = [
        Patch(facecolor=cores[cota_cm], edgecolor="white", label=f"{cota_cm} cm (TR {tr_por_cota[cota_cm]:.1f} anos)")
        for cota_cm in cotas_ordenadas
    ]
    ax.legend(
        handles=legenda,
        title="Cota de inundação",
        loc="upper right",
        frameon=True,
        facecolor=COR_SURFACE,
        edgecolor=COR_MUTED,
        labelcolor=COR_INK_PRIMARIA,
        title_fontsize=10,
        fontsize=9,
    )

    fig.suptitle(
        "Cotas de inundação do rio Uruguai — Uruguaiana/RS",
        x=0.02, y=0.985, ha="left", fontsize=13, color=COR_INK_PRIMARIA,
    )
    fig.text(
        0.02, 0.955, "Fonte: SGB (Serviço Geológico do Brasil) — CRS: EPSG:31981",
        fontsize=8.5, color=COR_INK_SECUNDARIA,
    )

    ax.set_xlabel("Leste (m) — SIRGAS 2000 / UTM 21S", fontsize=8.5, color=COR_MUTED)
    ax.set_ylabel("Norte (m)", fontsize=8.5, color=COR_MUTED)
    ax.ticklabel_format(style="plain", useOffset=False, axis="both")
    ax.tick_params(colors=COR_MUTED, labelsize=7.5, rotation=0)
    plt.setp(ax.get_xticklabels(), rotation=25, ha="right")
    for spine in ax.spines.values():
        spine.set_color(COR_MUNICIPIO)
    ax.set_aspect("equal")

    minx, miny, maxx, maxy = gdf_cotas.total_bounds
    folga_x, folga_y = (maxx - minx) * 0.15, (maxy - miny) * 0.15
    ax.set_xlim(minx - folga_x, maxx + folga_x)
    ax.set_ylim(miny - folga_y, maxy + folga_y)

    fig.tight_layout(rect=[0, 0, 1, 0.93])
    CAMINHO_SAIDA.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(CAMINHO_SAIDA, dpi=200, facecolor=COR_SURFACE)
    plt.close(fig)
    logger.info("Figura salva em %s", CAMINHO_SAIDA)


def main() -> None:
    gdf_cotas, gdf_municipio = carregar_dados()
    plotar(gdf_cotas, gdf_municipio)


if __name__ == "__main__":
    main()
