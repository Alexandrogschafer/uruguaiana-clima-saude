"""
Converte as escolas do INEP (Catálogo + Censo Escolar) para GeoJSON em
WGS84 — camada nova do geoportal, grupo "Educação", com filtro por
dependência administrativa no mesmo padrão de UI do filtro de saúde
(checkboxes com swatch + "marcar/desmarcar todos", controlado por
js/filtro-educacao.js, não pelo L.control.layers genérico).

Decisões:
- Só as 48 escolas GEOCODIFICADAS do arquivo de origem entram aqui (as 44
  sem coordenada já ficam de fora desde a aquisição, ver
  scripts/download/escolas_inep.py) — a nota sobre essa limitação vai no
  texto de ajuda do grupo "Educação" em index.html, não só no metadado.
- 47 colunas IN_* (infraestrutura bruta do Censo Escolar) resumidas em 4
  campos derivados legíveis (água/energia/esgoto/internet) em vez de
  expor tudo cru no popup — cada um combina as fontes/situações ativas
  daquela coluna em uma frase, ou "Inexistente" quando a fonte marca
  explicitamente ausência.
- Dependência administrativa mantida com os valores já legíveis da fonte
  ("Municipal"/"Estadual"/"Privada"/"Federal") — diferente do CAR/DNIT,
  aqui não há códigos brutos pra mapear.
"""

from pathlib import Path

import geopandas as gpd
import pandas as pd

from common import DIR_GEOPORTAL, RAIZ_PROJETO, logger, salvar_geojson_wgs84

CAMINHO_ORIGEM = RAIZ_PROJETO / "data" / "raw" / "vetor" / "escolas_inep-catalogo-censo_atual_vetorial.gpkg"

RENOMEIA_COLUNAS = {
    "Escola": "escola",
    "codigo_inep": "codigo_inep",
    "Dependência Administrativa": "dependencia_administrativa",
    "Localização": "localizacao",
    "Endereço": "endereco",
    "Telefone": "telefone",
    "Porte da Escola": "porte_escola",
    "Etapas e Modalidade de Ensino Oferecidas": "etapas_ensino",
    "QT_MAT_BAS": "matriculas",
    "QT_DOC_BAS": "docentes",
    "QT_SALAS_UTILIZADAS": "salas_utilizadas",
}


def _ativo(valor) -> bool:
    """As colunas IN_* do Censo Escolar vêm como STRING ("1"/"0"), não inteiro
    — comparar direto com `== 1` falha silenciosamente (sempre False). Aceita
    também 1/True pra ficar robusto a uma eventual leitura futura já tipada."""
    return valor in ("1", 1, True)


def _resumir(row: pd.Series, coluna_inexistente: str, fontes: list[tuple[str, str]]) -> str:
    """Combina colunas IN_* binárias de uma mesma dimensão (água/energia/esgoto)
    numa frase legível; `fontes` é [(coluna, rótulo), ...]."""
    if _ativo(row.get(coluna_inexistente)):
        return "Inexistente"
    ativos = [rotulo for coluna, rotulo in fontes if _ativo(row.get(coluna))]
    return ", ".join(ativos) if ativos else "Sem dado"


def _resumir_internet(row: pd.Series) -> str:
    if not _ativo(row.get("IN_INTERNET")):
        return "Não"
    detalhes = []
    if _ativo(row.get("IN_INTERNET_ALUNOS")):
        detalhes.append("com acesso para alunos")
    if _ativo(row.get("IN_BANDA_LARGA")):
        detalhes.append("banda larga")
    return "Sim" + (f" ({', '.join(detalhes)})" if detalhes else "")


def main() -> None:
    caminho_saida = DIR_GEOPORTAL / "escolas-inep.geojson"
    if caminho_saida.exists():
        logger.info("já existe, pulando: %s", caminho_saida.relative_to(RAIZ_PROJETO))
        return

    gdf = gpd.read_file(CAMINHO_ORIGEM)
    n_original = len(gdf)

    gdf["abastecimento_agua"] = gdf.apply(
        lambda r: _resumir(
            r,
            "IN_AGUA_INEXISTENTE",
            [
                ("IN_AGUA_REDE_PUBLICA", "Rede pública"),
                ("IN_AGUA_POCO_ARTESIANO", "Poço artesiano"),
                ("IN_AGUA_CACIMBA", "Cacimba/poço raso"),
                ("IN_AGUA_FONTE_RIO", "Fonte/rio/riacho"),
                ("IN_AGUA_CARRO_PIPA", "Carro-pipa"),
            ],
        ),
        axis=1,
    )
    gdf["fornecimento_energia"] = gdf.apply(
        lambda r: _resumir(
            r,
            "IN_ENERGIA_INEXISTENTE",
            [
                ("IN_ENERGIA_REDE_PUBLICA", "Rede pública"),
                ("IN_ENERGIA_GERADOR_FOSSIL", "Gerador fóssil"),
                ("IN_ENERGIA_RENOVAVEL", "Fonte renovável"),
            ],
        ),
        axis=1,
    )
    gdf["esgotamento_sanitario"] = gdf.apply(
        lambda r: _resumir(
            r,
            "IN_ESGOTO_INEXISTENTE",
            [
                ("IN_ESGOTO_REDE_PUBLICA", "Rede pública"),
                ("IN_ESGOTO_FOSSA_SEPTICA", "Fossa séptica"),
                ("IN_ESGOTO_FOSSA_COMUM", "Fossa comum"),
                ("IN_ESGOTO_FOSSA", "Fossa (tipo não especificado)"),
            ],
        ),
        axis=1,
    )
    gdf["internet"] = gdf.apply(_resumir_internet, axis=1)

    gdf = gdf.rename(columns=RENOMEIA_COLUNAS)
    colunas_finais = list(RENOMEIA_COLUNAS.values()) + [
        "abastecimento_agua",
        "fornecimento_energia",
        "esgotamento_sanitario",
        "internet",
        "geometry",
    ]
    gdf = gdf[colunas_finais].copy()

    salvar_geojson_wgs84(
        gdf,
        caminho_saida,
        descricao=(
            "Escolas do INEP (Catálogo de Escolas + Censo Escolar 2024) geocodificadas — camada nova "
            "do geoportal, grupo 'Educação', filtro por dependência administrativa (mesmo padrão de UI "
            "do filtro de saúde, controlado por js/filtro-educacao.js). Só as 48 escolas com coordenada "
            "válida na fonte entram aqui — 44 escolas do Censo Escolar 2024 sem coordenada geocodificada "
            "ficam de fora (ver data/raw/vetor/escolas_inep-catalogo-censo_atual_vetorial.json)."
        ),
        fonte={"caminho_origem": str(CAMINHO_ORIGEM.relative_to(RAIZ_PROJETO))},
        transformacao=(
            "colunas renomeadas/reduzidas a [escola, codigo_inep, dependencia_administrativa, localizacao, "
            "endereco, telefone, porte_escola, etapas_ensino, matriculas, docentes, salas_utilizadas] + "
            "4 colunas derivadas (abastecimento_agua, fornecimento_energia, esgotamento_sanitario, internet, "
            "resumidas a partir das 47 colunas IN_* binárias do Censo Escolar); sem simplificação de geometria "
            "(pontos); reprojeção -> EPSG:4326"
        ),
    )
    logger.info("escolas INEP: %d features de entrada -> %d exportadas", n_original, len(gdf))


if __name__ == "__main__":
    main()
