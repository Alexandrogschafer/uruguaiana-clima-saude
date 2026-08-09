"""Baixa o tema Vegetação do Banco de Dados de Informações Ambientais do
IBGE (BDiA) recortado pela área de estudo:

    data/raw/vetor/vegetacao_ibge-bdia_atual_vetorial.gpkg
    data/raw/vetor/vegetacao_ibge-bdia_atual_vetorial.json

Fonte e método
---------------
Camada `BDIA:vege_area` do GeoServer do IBGE (WFS), mesmo acervo (BDiA)
usado para geologia/geomorfologia/pedologia em outros projetos — aqui
confirmado por leitura real do GetCapabilities do workspace BDIA
(camadas geol_area, geom_area, pedo_area, vege_area, todas polígonos de
mapeamento temático na mesma escala). Scripts de geologia/geomorfologia/
pedologia NÃO existem neste repositório (verificado no catálogo e no
histórico do git) — só a vegetação foi processada aqui.

Endpoint: https://geoservicos.ibge.gov.br/geoserver/BDIA/ows (WFS 2.0.0)
— usar o endpoint do workspace diretamente (BDIA/ows), não o genérico
(/geoserver/ows com typeNames=BDIA:...), que devolveu erro
"InvalidParameterValue" (namespace) em teste real.

Escala: levantamento na escala 1:250.000 (mapeamento nacional de baixa
resolução espacial, mesma escala do RADAMBRASIL/mapeamento sistemático
do IBGE) — adequado para análise regional/exploratória, não para
delimitação de remanescentes em nível de propriedade ou licenciamento.

Recorte: bbox da área de estudo (WFS BBOX, EPSG:4674) seguido de recorte
exato pelo polígono municipal (gpd.clip via recorte_municipio), porque o
BBOX é retangular e traz feições fora do limite real do município.

IMPORTANTE — distinção conceitual com uso-solo_mapbiomas (já processado
neste projeto): este tema (BDiA/vege_area) é uma classificação de
FITOFISIONOMIAS NATIVAS (vegetação natural remanescente e sua
classificação fitogeográfica — ex. Estepe Gramíneo-Lenhosa, campo,
floresta-de-galeria), com classes antrópicas tratadas apenas de forma
genérica ("Agropecuária", "Influência urbana", "Pecuária"). Já o
MapBiomas é um mapeamento de USO E COBERTURA DA TERRA, com resolução
espacial muito maior (30m vs escala 1:250.000) e granularidade fina nas
classes antrópicas (lavoura, pastagem, área urbanizada, etc.), mas trata
a vegetação nativa de forma mais genérica (ex. "Formação Campestre" sem
diferenciar fitofisionomias). Os dois são complementares, não
substitutos um do outro — não usar um no lugar do outro sem checar qual
pergunta cada um responde.

Idempotente: se o arquivo já existir, não baixa de novo (--forcar força).

Uso:
    python scripts/download/vegetacao_ibge_bdia.py
    python scripts/download/vegetacao_ibge_bdia.py --forcar
"""

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import requests

sys.path.append(str(Path(__file__).resolve().parents[1] / "utils"))
from recorte_municipio import CRS_PADRAO, carregar_area_estudo, recortar_vetor  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

RAIZ = Path(__file__).resolve().parents[2]
CAMINHO_SAIDA = RAIZ / "data" / "raw" / "vetor" / "vegetacao_ibge-bdia_atual_vetorial.gpkg"

CRS_ORIGEM_BDIA = "EPSG:4674"  # SIRGAS 2000 geográfico (confirmado por srsName aceito no WFS)
URL_WFS_BDIA = "https://geoservicos.ibge.gov.br/geoserver/BDIA/ows"
CAMADA_WFS = "BDIA:vege_area"
ESCALA_FONTE = "1:250.000"

NOTA_DIFERENCA_MAPBIOMAS = (
    "vegetacao_ibge-bdia classifica FITOFISIONOMIAS NATIVAS remanescentes (vegetação natural e sua "
    "classificação fitogeográfica, ex. Estepe Gramíneo-Lenhosa, floresta-de-galeria), na escala "
    "1:250.000, com classes antrópicas tratadas de forma genérica (Agropecuária/Influência urbana/"
    "Pecuária). uso-solo_mapbiomas é um mapeamento de USO E COBERTURA DA TERRA a 30m de resolução, com "
    "granularidade fina nas classes antrópicas mas tratamento genérico da vegetação nativa (ex. "
    "'Formação Campestre' sem diferenciar fitofisionomias). São complementares — não usar um no lugar "
    "do outro sem checar qual pergunta cada um responde."
)


def baixar_vege_area(bbox: tuple, timeout: int = 120) -> gpd.GeoDataFrame:
    minx, miny, maxx, maxy = bbox
    parametros = {
        "service": "WFS",
        "version": "2.0.0",
        "request": "GetFeature",
        "typeNames": CAMADA_WFS,
        "outputFormat": "application/json",
        "srsName": CRS_ORIGEM_BDIA,
        "bbox": f"{minx},{miny},{maxx},{maxy},{CRS_ORIGEM_BDIA}",
    }
    logger.info("Consultando WFS do BDiA (%s) — bbox %s", CAMADA_WFS, bbox)
    resposta = requests.get(URL_WFS_BDIA, params=parametros, timeout=timeout)
    resposta.raise_for_status()
    geojson = resposta.json()
    if "features" not in geojson:
        raise RuntimeError(f"Resposta do WFS do BDiA sem 'features': {geojson}")
    gdf = gpd.GeoDataFrame.from_features(geojson["features"], crs=CRS_ORIGEM_BDIA)
    logger.info("%d feição(ões) na área de busca (bbox retangular, antes do recorte exato).", len(gdf))
    return gdf


def main() -> None:
    parser = argparse.ArgumentParser(description="Baixa o tema Vegetação do BDiA/IBGE recortado pela área de estudo.")
    parser.add_argument("--forcar", action="store_true", help="Baixa novamente mesmo se o arquivo já existir")
    args = parser.parse_args()

    if CAMINHO_SAIDA.exists() and not args.forcar:
        logger.info("%s já existe — nada a fazer (use --forcar para baixar de novo).", CAMINHO_SAIDA)
        return

    area_estudo = carregar_area_estudo()
    area_estudo_origem = area_estudo.to_crs(CRS_ORIGEM_BDIA)
    bbox = tuple(area_estudo_origem.total_bounds)

    gdf_bruto = baixar_vege_area(bbox)
    gdf = recortar_vetor(gdf_bruto, area_estudo)
    logger.info("%d feição(ões) após recorte exato pela área de estudo.", len(gdf))

    if gdf.empty:
        raise RuntimeError("Nenhuma feição de vegetação restou após o recorte — verifique bbox/serviço.")

    gdf.columns = [c.strip().lower() for c in gdf.columns]

    campo_classe = "legenda" if "legenda" in gdf.columns else None
    area_por_classe = None
    n_por_classe = None
    if campo_classe:
        gdf["_area_km2"] = gdf.geometry.area / 1e6
        area_por_classe = gdf.groupby(campo_classe)["_area_km2"].sum().round(3).sort_values(ascending=False).to_dict()
        n_por_classe = gdf[campo_classe].value_counts().to_dict()
        gdf = gdf.drop(columns="_area_km2")

    CAMINHO_SAIDA.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_file(CAMINHO_SAIDA, driver="GPKG", layer="vegetacao")

    metadados = {
        "fonte": "IBGE — Banco de Dados de Informações Ambientais (BDiA), tema Vegetação",
        "url_wfs": URL_WFS_BDIA,
        "camada_origem": CAMADA_WFS,
        "escala_fonte": ESCALA_FONTE,
        "data_acesso": datetime.now(timezone.utc).date().isoformat(),
        "n_feicoes": len(gdf),
        "n_feicoes_por_classe_legenda": n_por_classe,
        "area_km2_por_classe_legenda": area_por_classe,
        "area_km2_total": round(gdf.geometry.area.sum() / 1e6, 2),
        "crs_original": CRS_ORIGEM_BDIA,
        "crs_processado": CRS_PADRAO,
        "metodo": (
            "consulta WFS (GetFeature) à camada BDIA:vege_area filtrada por bbox retangular da área de "
            "estudo, seguida de recorte exato pelo polígono municipal (gpd.clip)"
        ),
        "precedente_no_repositorio": (
            "os temas geologia_ibge-bdia, geomorfologia_ibge-bdia e pedologia_ibge-bdia citados como "
            "acervo irmão NÃO existem neste repositório (nem no catálogo, nem em data/raw, nem no "
            "histórico do git) — confirmados como reais na fonte (workspace BDIA do GeoServer do IBGE: "
            "geol_area, geom_area, pedo_area), mas não processados aqui. Apenas vegetação foi baixada "
            "nesta tarefa."
        ),
        "aviso_escala": (
            f"levantamento na escala {ESCALA_FONTE} (mapeamento sistemático nacional) — adequado para "
            "análise regional/exploratória, não para delimitação de remanescentes em nível de "
            "propriedade, licenciamento ambiental ou fiscalização"
        ),
        "distincao_conceitual_uso_solo_mapbiomas": NOTA_DIFERENCA_MAPBIOMAS,
        "data_processamento": datetime.now(timezone.utc).isoformat(),
    }
    caminho_metadados = CAMINHO_SAIDA.with_suffix(".json")
    caminho_metadados.write_text(json.dumps(metadados, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

    logger.info("Salvo: %s (%d feições)", CAMINHO_SAIDA, len(gdf))
    logger.info("Metadados salvos em %s", caminho_metadados)
    logger.info("Área (km²) por classe: %s", area_por_classe)


if __name__ == "__main__":
    main()
