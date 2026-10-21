"""
Converte os dados hidrográficos da BHO (ANA) para GeoJSON do geoportal,
recortando pela área de estudo MUNICIPAL oficial (config/area_estudo.geojson)
— não pela área estendida com buffer usada só na etapa de busca/download
(scripts/download/hidrologia_bho.py, config/area_estudo_bacias.geojson).

    bacias/nivel{1..7}.geojson   — ottobacias por nível Otto Pfafstetter
    rede-hidrografica.geojson    — cursos d'água (camada curso_dagua do gpkg
                                   multi-layer; trecho_drenagem/ponto_drenagem
                                   não usados aqui por serem granularidade
                                   técnica demais para uma camada de contexto)

Nome dos cursos d'água
----------------------
curso_dagua não tem nome; os nomes da cartografia estão em trecho_drenagem
(noriocomp = nogenerico + noligacao + noespecif, igual a nooriginal nesta
área). O campo `nome` de cada curso parte do noriocomp do trecho nomeado mais
próximo da foz do curso, isto é, com o menor nudistcdag (distância, ao longo
do curso, do ponto de jusante do trecho à foz do curso d'água: dicionário de
dados da Nota Técnica nº 5/2024/CCOGI/SHE da ANA). O nome só é atribuído se os
trechos com esse nome somarem >= 25% do comprimento dos trechos do curso; se
não somarem, passa-se ao próximo nome nomeado rio acima (nudistcdag crescente)
e fica o primeiro que atingir 25%; se nenhum atingir, ou sem trecho nomeado,
nome nulo. Usa todos os trechos do curso
no bruto (área de busca), não só os do município: a foz e a cobertura se
referem à parte do curso presente no bruto (nucompcda, o curso inteiro, não
serve de denominador para rios que saem muito da área, como o rio Uruguai).

Simplificação de geometria
---------------------------
Mesmo critério de converter_malha_viaria.py: camadas de contexto (não usadas
em cálculo de área/estatística) não precisam de precisão métrica. O nível 7
sem simplificação teria ~36 MB (1.585 feições) — pesado demais pra servir
via fetch() no navegador. Tolerância aplicada em EPSG:31981 (CRS métrico),
antes de reprojetar para 4326.
"""

import argparse
import json

import geopandas as gpd
import pyogrio

from common import DIR_GEOPORTAL, RAIZ_PROJETO, logger, salvar_geojson_wgs84

CAMINHO_AREA_ESTUDO = RAIZ_PROJETO / "config" / "area_estudo.geojson"
DIR_RAW_VETOR = RAIZ_PROJETO / "data" / "raw" / "vetor"
DIR_BACIAS_SAIDA = DIR_GEOPORTAL / "bacias"

NIVEIS_OTTO = range(1, 8)
TOLERANCIA_SIMPLIFICACAO_BACIAS_M = 10
TOLERANCIA_SIMPLIFICACAO_REDE_M = 7.5

COBERTURA_MINIMA_NOME = 0.25
REGRA_NOME = (
    "noriocomp de trecho_drenagem (ligado por cocursodag): candidato é o nome do trecho nomeado "
    "mais próximo da foz do curso (menor nudistcdag, a distância do ponto de jusante do trecho à "
    "foz do curso d'água), atribuído se os trechos com esse nome somarem >= 25% do comprimento "
    "(nucomptrec) dos trechos do curso presentes no bruto (área de busca); se não, passa ao próximo "
    "nome rio acima (nudistcdag crescente) e fica o primeiro que atingir 25%; se nenhum atingir, ou "
    "sem trecho nomeado, nulo"
)
NOTA_NOME = (
    "o mesmo nome pode aparecer em mais de um curso da BHO quando o rio da carta os atravessa"
)
# escala lida na ficha de metadados do SNIRH e na Nota Técnica nº 5/2024/CCOGI/SHE (ANA)
ESCALA_MAPEAMENTO_BHO = {
    "escala_equivalente_declarada": "1:100.000",
    "fonte_da_escala": "ficha de metadados do SNIRH (registro 32e309da-a8c1-443f-90ac-0cd79ce6a33d), campo spatialResolution/equivalentScale, valor único para o conjunto; lida em 2026-10-20",
    "linhagem": "trechos de drenagem de cartografia na escala 1:250.000 ou maior; a ficha lista a escala por região hidrográfica, mas não lista a Região Hidrográfica do Uruguai, onde fica o município",
    "escala_local": {
        "valor": "1:50.000",
        "origem": "no Rio Grande do Sul, a BHO 6 usa a hidrografia do mapeamento sistemático terrestre do Exército Brasileiro em escala 1:50.000",
        "fonte": "Nota Técnica nº 5/2024/CCOGI/SHE da ANA (9/9/2024), § 36, p. 9; anexo da ficha de metadados do SNIRH; lida em 2026-10-20",
        "ressalva": "a nota não cita a Região Hidrográfica do Uruguai pelo nome na seção da BHO 6; a escala vem do estado (RS)",
    },
}


def _area_estudo_municipal() -> gpd.GeoDataFrame:
    return gpd.read_file(CAMINHO_AREA_ESTUDO)


def _nome_por_curso(caminho) -> dict:
    """cocursodag -> nome, pela REGRA_NOME (só atributos, sem geometria)."""
    trechos = pyogrio.read_dataframe(
        caminho, layer="trecho_drenagem",
        columns=["cocursodag", "noriocomp", "nucomptrec", "nudistcdag"], read_geometry=False,
    )
    km_curso = trechos.groupby("cocursodag")["nucomptrec"].sum()
    nomeados = trechos[trechos["noriocomp"].notna() & (trechos["noriocomp"].str.strip() != "")]
    # nomes de cada curso na ordem em que aparecem a partir da foz (menor nudistcdag primeiro;
    # empate improvável: ordem alfabética)
    da_foz_para_cima = (
        nomeados.sort_values(["cocursodag", "nudistcdag", "noriocomp"])
        .drop_duplicates(["cocursodag", "noriocomp"])
    )
    km_nome = nomeados.groupby(["cocursodag", "noriocomp"])["nucomptrec"].sum()
    nomes = {}
    for curso, nome in zip(da_foz_para_cima["cocursodag"], da_foz_para_cima["noriocomp"]):
        if curso in nomes:
            continue
        if km_nome[(curso, nome)] / km_curso[curso] >= COBERTURA_MINIMA_NOME:
            nomes[curso] = nome
    return nomes


def converter_bacias_hidrograficas(forcar: bool = False) -> None:
    area_estudo = _area_estudo_municipal()

    for nivel in NIVEIS_OTTO:
        caminho = DIR_RAW_VETOR / f"bacias-hidrograficas_ana-bho_nivel{nivel}_vetorial.gpkg"
        gdf = gpd.read_file(caminho)
        assert "codigo_otto" in gdf.columns, f"coluna codigo_otto não encontrada em {caminho}"

        gdf_recortado = gpd.clip(gdf, area_estudo)
        gdf_recortado["geometry"] = gdf_recortado.geometry.simplify(
            TOLERANCIA_SIMPLIFICACAO_BACIAS_M, preserve_topology=True
        )

        salvar_geojson_wgs84(
            gdf_recortado,
            DIR_BACIAS_SAIDA / f"nivel{nivel}.geojson",
            descricao=(
                f"Ottobacias nível Otto Pfafstetter {nivel} (BHO 6.2.4, ANA), recortadas pela "
                "área de estudo municipal oficial (não a área estendida com buffer usada na "
                "busca) — nível 1 = bacia mais abrangente, nível 7 = mais detalhado. ATENÇÃO: "
                "o atributo area_m2_bho é a área da bacia completa a montante (escala nacional, "
                "conforme calculada pela própria ANA/BHO), não a área do fragmento recortado "
                "pelo município nem está em m² apesar do nome do campo — comparação com "
                "geometry.area indica que já está em km²; não usado no geoportal por ambiguidade."
            ),
            fonte={"caminho_origem": str(caminho.relative_to(RAIZ_PROJETO))},
            forcar=forcar,
            transformacao=(
                f"recorte (gpd.clip) por {CAMINHO_AREA_ESTUDO.relative_to(RAIZ_PROJETO)} "
                f"(área municipal, não a estendida); "
                f"simplify(tolerância={TOLERANCIA_SIMPLIFICACAO_BACIAS_M}m, preserve_topology=True); "
                f"reprojeção {gdf.crs} -> EPSG:4326"
            ),
        )
        logger.info("bacias nível %d: %d feições originais -> %d após recorte", nivel, len(gdf), len(gdf_recortado))


def converter_rede_hidrografica(forcar: bool = False) -> None:
    caminho = DIR_RAW_VETOR / "rede-hidrografica_ana-bho_atual_vetorial.gpkg"
    gdf = gpd.read_file(caminho, layer="curso_dagua")

    area_estudo = _area_estudo_municipal()
    gdf_recortado = gpd.clip(gdf, area_estudo)
    gdf_recortado["geometry"] = gdf_recortado.geometry.simplify(
        TOLERANCIA_SIMPLIFICACAO_REDE_M, preserve_topology=True
    )
    gdf_recortado["nome"] = gdf_recortado["cocursodag"].map(_nome_por_curso(caminho))

    saida = DIR_GEOPORTAL / "rede-hidrografica.geojson"
    ja_existia = saida.exists()
    salvar_geojson_wgs84(
        gdf_recortado,
        saida,
        descricao=(
            "Cursos d'água (camada curso_dagua da BHO 6.2.4, ANA), recortados pela área de "
            "estudo municipal oficial (não a área estendida com buffer usada na busca)."
        ),
        fonte={"caminho_origem": f"{caminho.relative_to(RAIZ_PROJETO)} (layer=curso_dagua)"},
        transformacao=(
            f"recorte (gpd.clip) por {CAMINHO_AREA_ESTUDO.relative_to(RAIZ_PROJETO)} "
            f"(área municipal, não a estendida); "
            f"simplify(tolerância={TOLERANCIA_SIMPLIFICACAO_REDE_M}m, preserve_topology=True); "
            f"reprojeção {gdf.crs} -> EPSG:4326; campo nome a partir de trecho_drenagem"
        ),
        forcar=forcar,
    )
    if ja_existia and not forcar:
        return
    # metadados próprios da rede, além dos que salvar_geojson_wgs84 grava
    caminho_meta = saida.with_suffix(".json")
    metadados = json.loads(caminho_meta.read_text(encoding="utf-8"))
    metadados["regra_nome"] = REGRA_NOME
    metadados["n_cursos_com_nome"] = int(gdf_recortado["nome"].notna().sum())
    metadados["nota_nome"] = NOTA_NOME
    metadados["escala_mapeamento"] = ESCALA_MAPEAMENTO_BHO
    caminho_meta.write_text(json.dumps(metadados, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("rede hidrográfica: %d de %d cursos com nome", metadados["n_cursos_com_nome"], len(gdf_recortado))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--forcar", action="store_true", help="regrava mesmo que o arquivo já exista")
    parser.add_argument(
        "--camada", choices=["todas", "bacias", "rede"], default="todas",
        help="qual saída gerar (padrão: todas)",
    )
    args = parser.parse_args()
    if args.camada in ("todas", "bacias"):
        converter_bacias_hidrograficas(forcar=args.forcar)
    if args.camada in ("todas", "rede"):
        converter_rede_hidrografica(forcar=args.forcar)


if __name__ == "__main__":
    main()
