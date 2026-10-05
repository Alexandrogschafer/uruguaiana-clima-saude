"""
Camada de bairros do geoportal, a partir da malha de bairros do Censo 2022
(IBGE) já recortada para o município de referência:
data/processed/bairros/bairros_ibge_2022_vetorial.gpkg
(gerado por scripts/download/bairros_ibge.py)

    bairros.geojson — um polígono por bairro, com população, área, densidade,
                      número de setores e endereços de domicílio particular,
                      mais o ponto onde o portal escreve o nome do bairro
                      (rotulo_lon, rotulo_lat)

Sem simplificação de geometria: o contorno dos bairros tem de coincidir com o
dos setores censitários que o portal já mostra (o bairro é a união dos setores
com o mesmo código de bairro). O arquivo é pequeno o bastante para ir inteiro.

Só a sede tem bairros na malha do IBGE; os setores dos demais distritos, e
parte dos da sede, não pertencem a bairro nenhum.

Ponto do nome
-------------
O nome fica na parte habitada do bairro, e não no centro geométrico (vários
bairros da margem incluem um trecho largo do rio): média, ponderada pela
população de 2022, dos pontos interiores dos setores censitários do bairro.
Se a média cair fora do bairro, vale o ponto interior do setor mais populoso
dele. O cálculo é feito em EPSG:31981; o ponto vai para o GeoJSON em graus,
com 6 casas.

Uso:
  python scripts/geoportal/converter_bairros.py            (não regrava se já existe)
  python scripts/geoportal/converter_bairros.py --forcar
"""

import argparse
import json

import geopandas as gpd
from shapely.geometry import Point

from common import DIR_GEOPORTAL, RAIZ_PROJETO, logger, salvar_geojson_wgs84

CAMINHO_ORIGEM = RAIZ_PROJETO / "data" / "processed" / "bairros" / "bairros_ibge_2022_vetorial.gpkg"
CAMINHO_SETORES = (
    RAIZ_PROJETO / "data" / "processed" / "dinamica_populacional" / "populacao-setores_ibge-censo_2022_setor.gpkg"
)
CAMINHO_SAIDA = DIR_GEOPORTAL / "bairros.geojson"

COLUNAS = [
    "CD_BAIRRO",
    "NM_BAIRRO",
    "populacao_2022",
    "area_km2",
    "densidade_hab_km2",
    "setores_2022",
    "enderecos_domicilio_particular",
    "rotulo_lon",
    "rotulo_lat",
    "geometry",
]
LICENCA = "dados abertos (IBGE)"
NOTA_NOMES = (
    "Os 26 nomes são os da Lei municipal nº 2.889/1999; os limites são os da malha do IBGE "
    "e não foram conferidos contra a descrição da lei."
)


def nota_fora_de_bairro() -> str:
    """Quantos setores e habitantes ficam fora de bairro, lidos do metadado do produto de origem."""
    meta = json.loads(CAMINHO_ORIGEM.with_suffix(".json").read_text(encoding="utf-8"))
    sem = meta["setores_sem_bairro"]
    habitantes = f"{sem['populacao']:,}".replace(",", ".")
    return (
        f"Só a sede tem bairros na malha do IBGE: {sem['setores']} setores, "
        f"com {habitantes} habitantes, ficam fora de bairro."
    )


def pontos_do_nome(bairros: gpd.GeoDataFrame) -> tuple[gpd.GeoSeries, dict]:
    """Ponto do nome de cada bairro (no CRS dos bairros) e quantos saíram de cada regra."""
    setores = gpd.read_file(CAMINHO_SETORES, columns=["CD_BAIRRO", "pop"]).to_crs(bairros.crs)
    setores = setores[setores["CD_BAIRRO"].notna()].copy()
    setores["CD_BAIRRO"] = setores["CD_BAIRRO"].astype(str)
    setores["interior"] = setores.geometry.representative_point()  # sempre dentro do setor
    pontos, regras = [], {"media_ponderada": 0, "setor_mais_populoso": 0}
    for _, bairro in bairros.iterrows():
        s = setores[setores["CD_BAIRRO"] == str(bairro["CD_BAIRRO"])]
        if s.empty:
            raise ValueError(f"bairro {bairro['NM_BAIRRO']} sem setor em {CAMINHO_SETORES.name}")
        peso = s["pop"].fillna(0)
        ponto = None
        if peso.sum() > 0:
            ponto = Point((s["interior"].x * peso).sum() / peso.sum(), (s["interior"].y * peso).sum() / peso.sum())
        if ponto is None or not ponto.within(bairro.geometry):
            ponto = s.loc[peso.idxmax(), "interior"]
            regras["setor_mais_populoso"] += 1
        else:
            regras["media_ponderada"] += 1
        pontos.append(ponto)
    return gpd.GeoSeries(pontos, index=bairros.index, crs=bairros.crs), regras


def main() -> None:
    parser = argparse.ArgumentParser(description="Gera a camada de bairros do geoportal.")
    parser.add_argument("--forcar", action="store_true", help="regrava mesmo que o arquivo já exista")
    args = parser.parse_args()

    if not CAMINHO_ORIGEM.exists():
        raise FileNotFoundError(
            f"{CAMINHO_ORIGEM.relative_to(RAIZ_PROJETO)} ausente — rode scripts/download/bairros_ibge.py"
        )
    if not CAMINHO_SETORES.exists():
        raise FileNotFoundError(
            f"{CAMINHO_SETORES.relative_to(RAIZ_PROJETO)} ausente — rode scripts/processamento/dinamica_populacional_2022.py"
        )
    ja_existia = CAMINHO_SAIDA.exists()

    gdf = gpd.read_file(CAMINHO_ORIGEM)
    # área calculada no CRS de trabalho (EPSG:31981) pelo script de origem; a densidade usa essa área
    gdf["densidade_hab_km2"] = (gdf["populacao_2022"] / gdf["area_km2"]).round(1)
    # ponto do nome: calculado no CRS de trabalho e só então levado para graus
    pontos, regras = pontos_do_nome(gdf)
    pontos_wgs84 = pontos.to_crs("EPSG:4326")
    gdf["rotulo_lon"] = pontos_wgs84.x.round(6)
    gdf["rotulo_lat"] = pontos_wgs84.y.round(6)
    gdf = gdf[COLUNAS].sort_values("NM_BAIRRO").reset_index(drop=True)

    salvar_geojson_wgs84(
        gdf,
        CAMINHO_SAIDA,
        descricao=(
            "Bairros do Censo Demográfico 2022 (IBGE) no município de referência, com população, "
            "área, densidade, número de setores censitários e endereços de domicílio particular; "
            "rotulo_lon e rotulo_lat dão o ponto onde o portal escreve o nome."
        ),
        fonte={
            "caminho_origem": str(CAMINHO_ORIGEM.relative_to(RAIZ_PROJETO)),
            "malha": "IBGE — Malha de Bairros do Censo Demográfico 2022",
            "populacao": "soma dos setores censitários de 2022 com o código do bairro (Censo 2022)",
            "enderecos": "IBGE — CNEFE do Censo 2022, endereços de domicílio particular dentro do bairro",
            "setores_para_o_ponto_do_nome": str(CAMINHO_SETORES.relative_to(RAIZ_PROJETO)),
        },
        transformacao=(
            "densidade_hab_km2 = populacao_2022 / area_km2 (arredondado a 1 casa); "
            "rotulo_lon/rotulo_lat = média, ponderada pela população de 2022, dos pontos interiores dos "
            "setores do bairro (ou o ponto interior do setor mais populoso, se a média cair fora do bairro), "
            f"calculada em {gdf.crs} e gravada em graus com 6 casas; "
            f"sem simplificação de geometria; reprojeção {gdf.crs} -> EPSG:4326"
        ),
        forcar=args.forcar,
    )

    if ja_existia and not args.forcar:
        return  # nada foi regravado: o metadado fica como está
    # licença e nota entram no .json irmão, que salvar_geojson_wgs84 acabou de gravar
    caminho_meta = CAMINHO_SAIDA.with_suffix(".json")
    meta = json.loads(caminho_meta.read_text(encoding="utf-8"))
    meta["licenca"] = LICENCA
    meta["nota"] = nota_fora_de_bairro()
    meta["nota_nomes"] = NOTA_NOMES
    meta["ponto_do_nome"] = {
        "campos": "rotulo_lon e rotulo_lat (EPSG:4326, 6 casas); não são atributo do bairro, só posição do nome no mapa",
        "bairros_pela_media_ponderada": regras["media_ponderada"],
        "bairros_pelo_setor_mais_populoso": regras["setor_mais_populoso"],
    }
    caminho_meta.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("metadado completado (licença e nota): %s", caminho_meta.relative_to(RAIZ_PROJETO))


if __name__ == "__main__":
    main()
