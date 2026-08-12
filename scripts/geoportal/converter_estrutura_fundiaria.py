"""
Converte o CAR (Cadastro Ambiental Rural / SICAR) para GeoJSON em WGS84 —
camada nova do geoportal, grupo próprio ("Estrutura fundiária"), toggle
único (não há múltiplas categorias que justifiquem checkboxes separados,
diferente do filtro de saúde).

Decisões:
- Sem simplificação de geometria: mesmo padrão das outras camadas de
  polígono já publicadas sem simplify (geologia/geomorfologia/pedologia/
  vegetação, BDiA) — 1.672 feições, ~42 vértices/polígono em média,
  complexidade comparável às camadas BDiA já no geoportal sem tratamento
  especial. Só app-hidrica usa simplify(30m), por ser buffer geométrico
  denso, caso diferente daqui.
- Colunas derivadas `status_imovel_legenda`/`tipo_imovel_legenda`: a fonte
  traz só os códigos (AT/PE/CA, IRU/AST) — versão legível adicionada para
  popup e estilo, mantendo a coluna original de código também (útil pra
  quem for cruzar com a documentação do SICAR).
"""

from pathlib import Path

import geopandas as gpd

from common import DIR_GEOPORTAL, RAIZ_PROJETO, logger, salvar_geojson_wgs84

CAMINHO_ORIGEM = RAIZ_PROJETO / "data" / "raw" / "vetor" / "estrutura-fundiaria_car-sicar_atual_vetorial.gpkg"

LEGENDA_STATUS = {"AT": "Ativo", "PE": "Pendente", "CA": "Cancelado"}
LEGENDA_TIPO = {"IRU": "Imóvel Rural", "AST": "Assentamento"}

COLUNAS = [
    "codigo_imovel_car",
    "status_imovel",
    "condicao_analise",
    "area_declarada_ha",
    "modulos_fiscais",
    "tipo_imovel",
    "geometry",
]


def main() -> None:
    caminho_saida = DIR_GEOPORTAL / "estrutura-fundiaria.geojson"
    if caminho_saida.exists():
        logger.info("já existe, pulando: %s", caminho_saida.relative_to(RAIZ_PROJETO))
        return

    gdf = gpd.read_file(CAMINHO_ORIGEM)
    n_original = len(gdf)

    gdf = gdf[COLUNAS].copy()
    gdf["status_imovel_legenda"] = gdf["status_imovel"].map(LEGENDA_STATUS).fillna(gdf["status_imovel"])
    gdf["tipo_imovel_legenda"] = gdf["tipo_imovel"].map(LEGENDA_TIPO).fillna(gdf["tipo_imovel"])

    salvar_geojson_wgs84(
        gdf,
        caminho_saida,
        descricao=(
            "CAR (Cadastro Ambiental Rural) — imóveis rurais cadastrados no SICAR. "
            "Camada nova, grupo próprio no geoportal, toggle único. Estilizada por status_imovel "
            "(Ativo/Pendente/Cancelado). Escopo limitado: só polígono do imóvel + atributos básicos "
            "(Reserva Legal/APP declarada/Uso Consolidado não disponíveis nesta fonte, ver catalogo_fontes.csv)."
        ),
        fonte={"caminho_origem": str(CAMINHO_ORIGEM.relative_to(RAIZ_PROJETO))},
        transformacao=(
            "colunas reduzidas a [codigo_imovel_car, status_imovel, condicao_analise, area_declarada_ha, "
            "modulos_fiscais, tipo_imovel] + colunas derivadas [status_imovel_legenda, tipo_imovel_legenda "
            "(mapeadas dos códigos AT/PE/CA e IRU/AST)]; sem simplificação de geometria; reprojeção -> EPSG:4326"
        ),
    )
    logger.info("estrutura fundiária (CAR): %d features de entrada -> %d exportadas", n_original, len(gdf))


if __name__ == "__main__":
    main()
