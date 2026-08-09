"""
Converte as camadas de "meio físico" (geologia, geomorfologia, pedologia,
vegetação — BDiA/IBGE; poços SIAGAS; APP hídrica calculada) para GeoJSON em
WGS84, para o novo grupo "Meio físico" do geoportal (versão preliminar de
revisão visual).

    geologia.geojson / geomorfologia.geojson / pedologia.geojson /
    vegetacao.geojson  — sem simplificação: já vêm em escala 1:250.000
                         (poucas centenas de feições, ~1-1,2 MB cada em
                         EPSG:31981, tamanho similar a outras camadas já
                         servidas no geoportal sem simplificação)
    pocos-agua-subterranea.geojson — pontos, sem simplificação
    app-hidrica.geojson — simplificada (tolerância 30m, igual à menor faixa
                          de APP calculada) porque o polígono dissolvido da
                          rede local tem ~445 mil vértices brutos (buffer de
                          milhares de trechos da BHO) — inviável para
                          fetch() direto no navegador sem simplificar
    app-hidrica-ocupacao.json — resumo não-espacial (% de ocupação
                          antrópica da APP por MapBiomas 2024), mesmo padrão
                          de indicadores-municipais.json: dado sem variação
                          espacial por feição, cartão/legenda fixa no painel
"""

import json
import re

import geopandas as gpd

from common import DIR_GEOPORTAL, RAIZ_PROJETO, logger, salvar_geojson_wgs84

CAMINHO_AREA_ESTUDO = RAIZ_PROJETO / "config" / "area_estudo.geojson"
DIR_RAW_VETOR = RAIZ_PROJETO / "data" / "raw" / "vetor"
DIR_PROCESSED = RAIZ_PROJETO / "data" / "processed"

TOLERANCIA_SIMPLIFICACAO_APP_M = 30

CAMADAS_BDIA_SIMPLES = {
    "geologia": ("geologia_ibge-bdia_atual_vetorial.gpkg", "nm_unidade"),
    "geomorfologia": ("geomorfologia_ibge-bdia_atual_vetorial.gpkg", "legenda"),
    "pedologia": ("pedologia_ibge-bdia_atual_vetorial.gpkg", "legenda"),
    "vegetacao": ("vegetacao_ibge-bdia_atual_vetorial.gpkg", "legenda"),
}


def converter_camada_bdia(tema: str, nome_arquivo: str, campo_classe: str) -> None:
    caminho = DIR_RAW_VETOR / nome_arquivo
    gdf = gpd.read_file(caminho)
    assert campo_classe in gdf.columns, f"coluna {campo_classe} não encontrada em {caminho}"
    salvar_geojson_wgs84(
        gdf,
        DIR_GEOPORTAL / f"{tema}.geojson",
        descricao=(
            f"Tema {tema.capitalize()} do Banco de Dados de Informações Ambientais do IBGE "
            f"(BDiA), escala 1:250.000 — leitura regional/contextual, NÃO para decisão em "
            f"nível de microárea. Campo de classe usado no geoportal: {campo_classe}. "
            f"ATENÇÃO: o atributo ar_poli_km de cada feição é a área do polígono completo na "
            f"fonte nacional (antes do recorte municipal) — não representa a área do fragmento "
            f"visível no mapa nem deve ser somado por classe (ver {tema}-classes.json para a "
            f"área por classe já corrigida, calculada da geometria recortada)."
        ),
        fonte={"caminho_origem": str(caminho.relative_to(RAIZ_PROJETO))},
        transformacao=f"reprojeção {gdf.crs} -> EPSG:4326, sem simplificação de geometria nem alteração de atributos",
    )


def _limpar_rotulo_classe(rotulo: str) -> str:
    """Remove o dígito de ordenação que alguns campos 'legenda' da fonte BDiA têm colado no
    início (ex. '5Corpo d'água continental') — mesma limpeza feita em js/layers.js
    (limparRotuloClasse), pra manter os rótulos usados na legenda idênticos aos do mapa."""
    return re.sub(r"^\d+", "", str(rotulo)).strip() or "Sem classificação"


def gerar_classes_bdia(tema: str) -> None:
    """Gera {tema}-classes.json com área (km²) e nº de feições por classe, JÁ recortados pela
    área de estudo municipal — reaproveita os valores calculados no download (a partir da
    geometria em EPSG:31981, ver scripts/download/geociencias_ibge_bdia.py e
    vegetacao_ibge_bdia.py), em vez de somar o atributo ar_poli_km da fonte (que reflete o
    polígono inteiro antes do recorte e infla muito a área — problema real encontrado ao
    revisar visualmente a legenda no geoportal antes de trocar de abordagem)."""
    caminho_saida = DIR_GEOPORTAL / f"{tema}-classes.json"
    caminho_metadados = DIR_RAW_VETOR / f"{tema}_ibge-bdia_atual_vetorial.json"
    dados = json.loads(caminho_metadados.read_text(encoding="utf-8"))

    chave_area = "area_km2_por_classe" if "area_km2_por_classe" in dados else "area_km2_por_classe_legenda"
    chave_n = "n_feicoes_por_classe" if "n_feicoes_por_classe" in dados else "n_feicoes_por_classe_legenda"
    areas_brutas: dict = dados[chave_area]
    n_brutas: dict = dados.get(chave_n) or {}

    areas_limpas: dict[str, float] = {}
    n_limpas: dict[str, int] = {}
    for rotulo_bruto, area in areas_brutas.items():
        rotulo = _limpar_rotulo_classe(rotulo_bruto)
        areas_limpas[rotulo] = areas_limpas.get(rotulo, 0) + area
        n_limpas[rotulo] = n_limpas.get(rotulo, 0) + n_brutas.get(rotulo_bruto, 0)

    classes_ordenadas = sorted(areas_limpas, key=lambda r: areas_limpas[r], reverse=True)

    saida = {
        "descricao": (
            f"Área (km²) e nº de feições por classe do tema {tema.capitalize()} (BDiA/IBGE), já "
            "recortadas pela área de estudo municipal — calculadas a partir da geometria "
            "(EPSG:31981) no momento do download, não do atributo ar_poli_km da fonte (que é a "
            "área do polígono inteiro antes do recorte municipal)."
        ),
        "fonte": {"caminho_origem": str(caminho_metadados.relative_to(RAIZ_PROJETO))},
        "classes_ordenadas": classes_ordenadas,
        "area_km2_por_classe": {c: round(areas_limpas[c], 2) for c in classes_ordenadas},
        "n_feicoes_por_classe": {c: n_limpas[c] for c in classes_ordenadas},
    }
    caminho_saida.write_text(json.dumps(saida, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("gerado: %s", caminho_saida.relative_to(RAIZ_PROJETO))


def converter_pocos_siagas() -> None:
    caminho = DIR_RAW_VETOR / "pocos-agua-subterranea_siagas-cprm_atual_vetorial.gpkg"
    gdf = gpd.read_file(caminho)
    salvar_geojson_wgs84(
        gdf,
        DIR_GEOPORTAL / "pocos-agua-subterranea.geojson",
        descricao=(
            "Poços cadastrados no SIAGAS (SGB/CPRM) em Uruguaiana. Campos com alta taxa de "
            "dado faltante na fonte (vazão específica, nível dinâmico/estático, uso da água) "
            "são exibidos no popup como 'sem dado' — o poço não é ocultado por falta de algum "
            "atributo."
        ),
        fonte={"caminho_origem": str(caminho.relative_to(RAIZ_PROJETO))},
        transformacao=f"reprojeção {gdf.crs} -> EPSG:4326, sem alteração de geometria/atributos",
    )


def converter_app_hidrica() -> None:
    caminho = DIR_PROCESSED / "app-hidrica_calculado_atual_vetorial.gpkg"
    gdf = gpd.read_file(caminho)

    n_vertices_antes = sum(
        len(anel.exterior.coords)
        for geom in gdf.geometry
        for anel in (geom.geoms if geom.geom_type == "MultiPolygon" else [geom])
    )
    gdf["geometry"] = gdf.geometry.simplify(TOLERANCIA_SIMPLIFICACAO_APP_M, preserve_topology=True)
    n_vertices_depois = sum(
        len(anel.exterior.coords)
        for geom in gdf.geometry
        for anel in (geom.geoms if geom.geom_type == "MultiPolygon" else [geom])
    )
    logger.info("app-hidrica: %d -> %d vértices após simplify(%dm)", n_vertices_antes, n_vertices_depois, TOLERANCIA_SIMPLIFICACAO_APP_M)

    salvar_geojson_wgs84(
        gdf,
        DIR_GEOPORTAL / "app-hidrica.geojson",
        descricao=(
            "APP hídrica calculada (aproximação metodológica, não delimitação técnica — ver "
            "metadado de origem) — 2 feições dissolvidas por curso d'água: rede_local (faixa "
            "de 30m) e rio_uruguai (faixa de 200m, largura estimada por seção transversal)."
        ),
        fonte={"caminho_origem": str(caminho.relative_to(RAIZ_PROJETO))},
        transformacao=(
            f"simplify(tolerância={TOLERANCIA_SIMPLIFICACAO_APP_M}m, preserve_topology=True) — "
            f"necessário porque o polígono dissolvido bruto tem {n_vertices_antes} vértices "
            f"(buffer de milhares de trechos da BHO), inviável para fetch() direto; "
            f"reprojeção {gdf.crs} -> EPSG:4326"
        ),
    )


def gerar_resumo_ocupacao_app() -> None:
    caminho_saida = DIR_GEOPORTAL / "app-hidrica-ocupacao.json"
    if caminho_saida.exists():
        logger.info("já existe, pulando: %s", caminho_saida.relative_to(RAIZ_PROJETO))
        return

    caminho_origem = DIR_PROCESSED / "app-ocupacao_calculado_2024.json"
    dados_origem = json.loads(caminho_origem.read_text(encoding="utf-8"))

    resumo = {
        "descricao": (
            "Resumo não-espacial da ocupação da APP hídrica calculada por classes MapBiomas "
            "2024 (% de área antrópica) — dado agregado para todo o polígono da APP, sem "
            "variação por feição, por isso exibido como legenda/cartão fixo no painel, não "
            "como atributo de popup."
        ),
        "fonte": {"caminho_origem": str(caminho_origem.relative_to(RAIZ_PROJETO))},
        "area_total_app_km2": dados_origem["area_total_app_km2"],
        "area_natural_km2": dados_origem["area_natural_km2"],
        "area_antropica_km2": dados_origem["area_antropica_km2"],
        "area_agua_km2": dados_origem["area_agua_km2"],
        "pct_area_app_ocupada_por_classes_antropicas": dados_origem["pct_area_app_ocupada_por_classes_antropicas"],
        "pct_area_app_antropica_sobre_area_terrestre_excl_agua": dados_origem["pct_area_app_antropica_sobre_area_terrestre_excl_agua"],
        "aviso_metodologico": dados_origem["aviso_metodologico"],
    }
    caminho_saida.write_text(json.dumps(resumo, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("gerado: %s", caminho_saida.relative_to(RAIZ_PROJETO))


def main() -> None:
    for tema, (nome_arquivo, campo_classe) in CAMADAS_BDIA_SIMPLES.items():
        converter_camada_bdia(tema, nome_arquivo, campo_classe)
        gerar_classes_bdia(tema)
    converter_pocos_siagas()
    converter_app_hidrica()
    gerar_resumo_ocupacao_app()


if __name__ == "__main__":
    main()
