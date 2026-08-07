"""
Gera uma área de estudo ESTENDIDA por buffer sobre a área de estudo oficial
do município, para uso exclusivo como recorte inicial de dados hidrográficos
(bacias/rede de drenagem) e de terreno (MDT) que podem se estender além do
limite municipal:

    config/area_estudo_bacias.geojson  (EPSG:31981)

Esta área NÃO substitui config/area_estudo.geojson como limite oficial do
projeto — é usada apenas nas etapas de busca/recorte de bacias hidrográficas
(scripts/download/hidrologia_bho.py, scripts/download/terreno_anadem.py).
Produtos de análise (declividade, hipsometria etc.) voltam a ser recortados
pela área de estudo municipal original.

Escolha do buffer (default 18 km, ajustável via --buffer-km)
--------------------------------------------------------------
Bacias hidrográficas raramente coincidem com limites administrativos: um
trecho de drenagem ou uma ottobacia que desagua dentro do município pode ter
sua cabeceira ou seu divisor de águas vários km fora dele. Um buffer de
15-20 km é uma folga pragmática para capturar essas bacias "de borda" nos
níveis Otto Pfafstetter mais finos (5-7, que são as unidades hidrográficas
de escala mais próxima do município) sem chegar a puxar bacias de nível
1-3 inteiras — essas são continentais/estaduais (a bacia do rio Uruguai
cobre 3 estados e 2 países) e buscar toda a sua extensão aqui seria
desproporcional ao uso pretendido (plataforma de vigilância em saúde
municipal). Se no futuro for necessário analisar uma bacia específica por
inteiro, o recorte deve ser feito pela própria geometria da bacia (após
identificá-la na ETAPA 2), não aumentando este buffer.

Idempotente: se a saída já existir, não reprocessa (a menos que --forcar
seja usado).

Uso:
    python scripts/processamento/area_estudo_bacias.py
    python scripts/processamento/area_estudo_bacias.py --buffer-km 20 --forcar
"""

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd

sys.path.append(str(Path(__file__).resolve().parents[1] / "utils"))
from recorte_municipio import CRS_PADRAO, carregar_area_estudo  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

RAIZ = Path(__file__).resolve().parents[2]
CAMINHO_ENTRADA_DEFAULT = RAIZ / "config" / "area_estudo.geojson"
CAMINHO_SAIDA_DEFAULT = RAIZ / "config" / "area_estudo_bacias.geojson"
BUFFER_KM_DEFAULT = 18.0


def gerar_area_bacias(area_estudo: gpd.GeoDataFrame, buffer_km: float) -> gpd.GeoDataFrame:
    """Aplica buffer métrico (CRS já é projetado/métrico) e dissolve para um único polígono."""
    buffer_m = buffer_km * 1000
    geom_bufferizada = area_estudo.geometry.buffer(buffer_m).union_all()
    gdf = gpd.GeoDataFrame({"buffer_km": [buffer_km]}, geometry=[geom_bufferizada], crs=area_estudo.crs)
    return gdf


def salvar(gdf: gpd.GeoDataFrame, area_original: gpd.GeoDataFrame, caminho_saida: Path, buffer_km: float,
           caminho_entrada: Path) -> None:
    caminho_saida.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_file(caminho_saida, driver="GeoJSON")
    logger.info("Área de estudo estendida salva em %s (CRS: %s)", caminho_saida, gdf.crs)

    area_original_km2 = float(area_original.geometry.area.sum() / 1e6)
    area_bacias_km2 = float(gdf.geometry.area.sum() / 1e6)
    bounds = gdf.total_bounds.tolist()

    metadados = {
        "descricao": (
            "Área de estudo estendida por buffer sobre o limite municipal, usada apenas como área de "
            "busca/recorte inicial de dados hidrográficos (BHO/ANA) e de terreno (ANADEM) — não é um novo "
            "limite oficial do projeto."
        ),
        "script_gerador": "scripts/processamento/area_estudo_bacias.py",
        "entrada": str(caminho_entrada.relative_to(RAIZ)),
        "crs": str(gdf.crs),
        "buffer_km": buffer_km,
        "justificativa_buffer": (
            "15-20 km cobre bacias hidrográficas 'de borda' (cabeceira ou divisor de águas fora do município) "
            "nos níveis Otto Pfafstetter mais finos (5-7), sem puxar bacias de nível 1-3 inteiras, que são "
            "continentais/estaduais (bacia do rio Uruguai cobre 3 estados e 2 países) — desproporcional ao uso "
            "de vigilância em saúde municipal pretendido pelo projeto."
        ),
        "area_estudo_original_km2": round(area_original_km2, 2),
        "area_estudo_bacias_km2": round(area_bacias_km2, 2),
        "bbox_epsg31981": bounds,
        "data_processamento": datetime.now(timezone.utc).isoformat(),
        "transformacao_aplicada": f"buffer de {buffer_km} km sobre {caminho_entrada.name}, dissolvido para 1 polígono",
        "uso_pretendido": [
            "scripts/download/hidrologia_bho.py (recorte inicial de bacias e rede de drenagem)",
            "scripts/download/terreno_anadem.py (recorte inicial do MDT)",
        ],
    }
    caminho_metadados = caminho_saida.with_suffix(".json")
    caminho_metadados.write_text(json.dumps(metadados, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("Metadados salvos em %s", caminho_metadados)
    logger.info(
        "Área original: %.2f km² -> área estendida (buffer %.1f km): %.2f km²",
        area_original_km2, buffer_km, area_bacias_km2,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Gera área de estudo estendida (buffer) para recorte inicial de bacias/MDT."
    )
    parser.add_argument("--entrada", type=Path, default=CAMINHO_ENTRADA_DEFAULT, help="Área de estudo oficial de entrada")
    parser.add_argument("--saida", type=Path, default=CAMINHO_SAIDA_DEFAULT, help="Caminho de saída do GeoJSON")
    parser.add_argument("--buffer-km", type=float, default=BUFFER_KM_DEFAULT, help="Buffer em km (default: 18)")
    parser.add_argument("--forcar", action="store_true", help="Reprocessa mesmo se a saída já existir")
    args = parser.parse_args()

    if args.saida.exists() and not args.forcar:
        logger.info("Área de estudo estendida já existe em %s — nada a fazer (use --forcar para refazer).", args.saida)
        return

    area_estudo = carregar_area_estudo(args.entrada)
    if area_estudo.crs.to_string() != CRS_PADRAO:
        area_estudo = area_estudo.to_crs(CRS_PADRAO)

    gdf_bacias = gerar_area_bacias(area_estudo, args.buffer_km)
    salvar(gdf_bacias, area_estudo, args.saida, args.buffer_km, args.entrada)


if __name__ == "__main__":
    main()
