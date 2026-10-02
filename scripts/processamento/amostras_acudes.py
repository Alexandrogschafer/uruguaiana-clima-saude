"""
Amostras para o responsável escolher a regra de exibição dos açudes e outras
águas (rodada 04, Tarefa 2e). Gera o mapa de densidade por setor e o mapa de
pontos de endereços (área urbana e município), cada um em três versões:
  sem-acudes       — só o rio principal como área de água;
  acudes-discretos — regra "enderecos" de c.AGUA_EXIBICAO (azul bem claro, sem
                     contorno, por baixo, com limiar de área pela escala);
  acudes-rodada03  — como na rodada 03 (mesma cor do rio, com contorno, por cima).
Só apresentação; nenhum dado é recalculado.

Saída: docs/exposicao_inundacao/mapas_v2/amostras_acudes/*.png (+ .json)

Uso:
  python scripts/processamento/amostras_acudes.py --codigo-ibge 4322400
"""

from __future__ import annotations

import argparse
import logging

import geopandas as gpd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402

import dinamica_populacional_comum as c  # noqa: E402
from dinamica_populacional_cnefe_mapas import PONTO, Fundo, mil  # noqa: E402
from dinamica_populacional_mapas import INK, MUTED, SEQ, Base, mapa  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

SAIDA = c.RAIZ / "docs" / "exposicao_inundacao" / "mapas_v2" / "amostras_acudes"
VERSOES = {"sem-acudes": "sem", "acudes-discretos": "enderecos", "acudes-rodada03": "r03"}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--codigo-ibge", default=c.CODIGO_IBGE_DEFAULT)
    p.add_argument("--nome-rio", default=c.NOME_RIO_DEFAULT)
    a = p.parse_args()
    SAIDA.mkdir(parents=True, exist_ok=True)
    st = gpd.read_file(c.CAMADAS / "populacao-setores_ibge-censo_2022_setor.gpkg")
    pts = gpd.read_file(c.CAMADAS / "enderecos-domicilios_ibge-cnefe_2022_pontos.gpkg", columns=["setor_2022"]).to_crs(c.CRS_PADRAO)
    feitos = []
    for versao, modo in VERSOES.items():
        base = Base(a.codigo_ibge, agua=True, nome_rio=a.nome_rio, modo_agua=modo)
        meta = {"amostra": versao, "regra_outras_aguas": modo, "sem_simbolo_do_centro": True,
                "nota": "amostra para escolha da regra de exibição dos açudes; só apresentação"}
        feitos += mapa(base, st, "dens_hab_ha", [0, 5, 25, 50, 75, 100, float("inf")], SEQ, f"Densidade demográfica por setor, 2022 — amostra: {versao}",
                       "habitantes por hectare", f"amostra-{versao}_densidade-populacional_ibge-censo_2022_setor",
                       "Fonte: IBGE, Censo 2022 — malha e agregados por setor.", saida=SAIDA, meta_extra=meta)
        fundo = Fundo(base)
        for rec, ext, esc, ms in (("urbano", base.ext_urb, 1000, 0.6), ("municipio", base.ext_mun, 20000, 0.8)):
            fig, ax = plt.subplots(figsize=(7.2, 7.2 * (ext[3] - ext[2]) / (ext[1] - ext[0]) + 0.6))
            fundo.desenhar(ax, ext, rec, esc, modo_agua=modo)
            ax.scatter(pts.geometry.x, pts.geometry.y, s=ms, color=PONTO, alpha=0.35, linewidths=0, zorder=5)
            hand = fundo.comum([Line2D([], [], marker="o", ls="", color=PONTO, alpha=0.6, ms=3, label=f"endereço de domicílio particular\n({mil(len(pts))} pontos)")])
            ax.legend(handles=hand, title=f"amostra: {versao}", loc="upper left", bbox_to_anchor=(1.01, 1), frameon=False, fontsize=7.5, title_fontsize=8,
                      alignment="left")
            sub = "área urbana da sede" if rec == "urbano" else "município inteiro"
            ax.set_title(f"Endereços de domicílios particulares, CNEFE 2022 — amostra: {versao}\n{sub}", loc="left", fontsize=10, color=INK)
            ax.annotate("Fonte: IBGE — CNEFE do Censo 2022; água e vias: OpenStreetMap.\nAmostra de exibição — pendente de conferência. EPSG:31981.",
                        xy=(0, -0.015), xycoords="axes fraction", va="top", fontsize=6.5, color=MUTED)
            caminho = SAIDA / f"amostra-{versao}_enderecos-pontos_ibge-cnefe_2022_pontos_{rec}.png"
            fig.savefig(caminho, dpi=180, facecolor="#fcfcfb", bbox_inches="tight")
            plt.close(fig)
            c.gravar_meta(caminho, status=c.STATUS_CONFERENCIA, ligado_ao_portal=False, codigo_ibge=a.codigo_ibge, recorte=sub,
                          script="scripts/processamento/amostras_acudes.py", **meta)
            feitos.append(caminho)
    for f in feitos:
        logger.info("Amostra: %s", f.relative_to(c.RAIZ))


if __name__ == "__main__":
    main()
