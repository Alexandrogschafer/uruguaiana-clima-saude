"""
Converte as camadas vetoriais "simples" do projeto para GeoJSON em WGS84
(EPSG:4326), formato exigido pelo Leaflet, a partir dos dados já
processados/baixados em EPSG:31981 (CRS padrão do projeto).

Nenhuma dessas camadas precisa de simplificação de geometria ou remoção
de colunas — são exportadas com todos os atributos originais, só
reprojetadas. A malha viária (que precisa de simplificação) é tratada
separadamente em converter_malha_viaria.py.

Uso:
    python scripts/geoportal/converter_camadas_vetoriais.py [--so limite|unidades|inmet] [--forcar]
"""

import argparse
from pathlib import Path

import geopandas as gpd

from common import DIR_GEOPORTAL, RAIZ_PROJETO, salvar_geojson_wgs84

CAMINHO_AREA_ESTUDO = RAIZ_PROJETO / "config" / "area_estudo.geojson"
DIR_PROCESSED = RAIZ_PROJETO / "data" / "processed"
DIR_RAW_VETOR = RAIZ_PROJETO / "data" / "raw" / "vetor"


def converter_limite_municipal() -> None:
    gdf = gpd.read_file(CAMINHO_AREA_ESTUDO)
    salvar_geojson_wgs84(
        gdf,
        DIR_GEOPORTAL / "limite-municipal.geojson",
        descricao="Limite do município de referência do projeto (área de estudo).",
        fonte={"caminho_origem": str(CAMINHO_AREA_ESTUDO.relative_to(RAIZ_PROJETO))},
        transformacao=f"reprojeção {gdf.crs} -> EPSG:4326, sem alteração de geometria/atributos",
    )


def converter_setores_inundacao() -> None:
    caminho = DIR_PROCESSED / "setores-inundacao_intersecao.gpkg"
    gdf = gpd.read_file(caminho)
    assert "cota_cm" in gdf.columns, f"campo cota_cm não encontrado em {caminho}"
    salvar_geojson_wgs84(
        gdf,
        DIR_GEOPORTAL / "setores-inundacao.geojson",
        descricao=(
            "Interseção de setores censitários com cotas de inundação (SGB), "
            "com população/estabelecimentos de saúde estimados expostos por cota_cm."
        ),
        fonte={"caminho_origem": str(caminho.relative_to(RAIZ_PROJETO))},
        transformacao=f"reprojeção {gdf.crs} -> EPSG:4326, sem alteração de geometria/atributos; campo cota_cm preservado para o slider",
    )


def converter_saude_cnes() -> None:
    caminho = DIR_RAW_VETOR / "saude-cnes_datasus_atual_vetorial.gpkg"
    gdf = gpd.read_file(caminho)
    for col in ("tipo_unidade_categoria", "tipo_unidade"):
        assert col in gdf.columns, f"coluna {col} não encontrada em {caminho}"
    salvar_geojson_wgs84(
        gdf,
        DIR_GEOPORTAL / "saude-cnes.geojson",
        descricao=(
            "Estabelecimentos de saúde (CNES/DATASUS). Filtro de tipo no geoportal usa "
            "tipo_unidade_categoria (categorização normalizada, confirmada na etapa 0); "
            "tipo_unidade (bruto CNES) mantido para exibição em popup."
        ),
        fonte={"caminho_origem": str(caminho.relative_to(RAIZ_PROJETO))},
        transformacao=f"reprojeção {gdf.crs} -> EPSG:4326, sem alteração de geometria/atributos",
    )


def converter_saude_osm() -> None:
    caminho = DIR_RAW_VETOR / "saude-estabelecimentos_osm_atual_vetorial.gpkg"
    gdf = gpd.read_file(caminho)
    salvar_geojson_wgs84(
        gdf,
        DIR_GEOPORTAL / "saude-osm.geojson",
        descricao="Estabelecimentos de saúde mapeados no OpenStreetMap (complementar ao CNES).",
        fonte={"caminho_origem": str(caminho.relative_to(RAIZ_PROJETO))},
        transformacao=f"reprojeção {gdf.crs} -> EPSG:4326, sem alteração de geometria/atributos",
    )


CREDITO_SAUDE = ("CNES (Ministério da Saúde), revisado e corrigido pela equipe do projeto com informações dos "
                 "profissionais de saúde do município — versão 4, 2026")


def converter_unidades_saude(forcar: bool = False) -> None:
    """Camada única de saúde do portal: as 22 unidades da atenção primária (ESF e UBS), 2026-10-02.

    2026-11-02: a população informada de 2026 (populacao2026) deixou de ser publicada; ficam 2017 e 2023,
    sem nenhuma soma. O gpkg de origem não muda: só a seleção de colunas da camada publicada.
    """
    caminho = DIR_PROCESSED / "saude" / "unidades-saude-esf-ubs_cnes-revisado-v4_2026_pontos.gpkg"
    gdf = gpd.read_file(caminho)
    colunas = ["cnes", "nome", "classe", "endereco", "populacao2017", "populacao2023"]
    for col in colunas:
        assert col in gdf.columns, f"coluna {col} não encontrada em {caminho}"
    salvar_geojson_wgs84(
        gdf[colunas + ["geometry"]],
        DIR_GEOPORTAL / "unidades-saude-esf-ubs.geojson",
        descricao=(
            "Unidades de saúde da atenção primária (ESF e UBS), 22 unidades, com classe (ESF, UBS, "
            "sem classe). populacao2017/2023: população da unidade informada pelos profissionais de saúde do "
            "município; o conceito (cadastrada, adscrita ou atendida) está pendente de confirmação; ausente = não informado. "
            "A população de 2026 não é publicada, e nenhuma soma de população."
        ),
        fonte={"caminho_origem": str(caminho.relative_to(RAIZ_PROJETO)), "credito": CREDITO_SAUDE},
        transformacao=(f"reprojeção {gdf.crs} -> EPSG:4326; só as colunas {colunas} "
                       "(populacao2026 retirada da camada publicada em 2026-11-02; o gpkg de origem não foi alterado)"),
        forcar=forcar,
    )


def converter_clima_inmet() -> None:
    caminho = DIR_RAW_VETOR / "estacoes-clima_inmet_atual_vetorial.gpkg"
    gdf = gpd.read_file(caminho)
    salvar_geojson_wgs84(
        gdf,
        DIR_GEOPORTAL / "estacoes-clima.geojson",
        descricao="Estações climatológicas INMET relevantes para a área de estudo.",
        fonte={"caminho_origem": str(caminho.relative_to(RAIZ_PROJETO))},
        transformacao=f"reprojeção {gdf.crs} -> EPSG:4326, sem alteração de geometria/atributos",
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--so", choices=["limite", "unidades", "inmet"], help="gera só esta camada")
    ap.add_argument("--forcar", action="store_true", help="regrava mesmo que o arquivo já exista")
    args = ap.parse_args()
    if args.so == "unidades":
        converter_unidades_saude(forcar=args.forcar)
        return
    if args.so == "limite":
        converter_limite_municipal()
        return
    if args.so == "inmet":
        converter_clima_inmet()
        return
    converter_limite_municipal()
    # converter_setores_inundacao() — retirado em 2026-10-02: a camada setores × manchas trazia a
    # população exposta preliminar (por área do setor), substituída pelo cálculo por endereços
    # (scripts/processamento/exposicao_inundacao_enderecos.py), pendente de conferência
    # converter_saude_cnes() e converter_saude_osm() — retirados em 2026-10-02: o portal passa a ter uma só
    # camada de saúde, a das unidades ESF/UBS revisadas (converter_unidades_saude)
    converter_unidades_saude(forcar=args.forcar)
    converter_clima_inmet()


if __name__ == "__main__":
    main()
