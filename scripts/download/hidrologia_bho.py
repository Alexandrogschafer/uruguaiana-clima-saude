"""
Baixa bacias hidrográficas (ottobacias, todos os níveis Otto Pfafstetter
1-7) e a rede de drenagem (cursos d'água, trechos de drenagem e pontos de
drenagem) da Base Hidrográfica Ottocodificada Multiescalas 6 (BHO 6) da ANA.

    data/raw/vetor/bacias-hidrograficas_ana-bho_nivel{1..7}_vetorial.gpkg
    data/raw/vetor/rede-hidrografica_ana-bho_atual_vetorial.gpkg
        (camadas: curso_dagua, trecho_drenagem, ponto_drenagem)

Fonte e citação
----------------
ANA — Base Hidrográfica Ottocodificada Multiescalas 6 (BHO 6), versão
6.2.4, publicada em 2022-10-20. Ficha de metadados:
https://metadados.snirh.gov.br/geonetwork/srv/api/records/32e309da-a8c1-443f-90ac-0cd79ce6a33d
Acesso livre. Arquivos completos (nacionais) em GeoPackage, hospedados em
metadados.snirh.gov.br/files/<id-do-registro>/<tema>.gpkg — e são GRANDES:
trecho_drenagem ~4 GB, curso_dagua ~2,9 GB, área de contribuição combinada
(todos os níveis) ~20 GB. Este script NUNCA baixa esses arquivos por
inteiro: usa leitura remota com filtro espacial (GDAL /vsicurl/ + ogr2ogr
-spat), que aproveita o índice espacial (R-tree) do GeoPackage para buscar
só as páginas do arquivo remoto que intersectam a área de estudo
estendida — o mesmo princípio de streaming HTTP-range já usado em
scripts/download/uso-solo_mapbiomas.py para raster, aplicado aqui a vetor.

Área de busca/recorte: config/area_estudo_bacias.geojson (buffer sobre a
área de estudo oficial — ver scripts/processamento/area_estudo_bacias.py),
não o limite municipal em si, porque bacias hidrográficas se estendem além
dele.

Níveis Otto Pfafstetter: a BHO 6 já distribui a área de contribuição
separada por nível (um .gpkg por nível, 1 a 7), então não é necessário
derivar o nível a partir do código — mas o campo
`wts_cd_pfafstetterbasincodelevel` (nível declarado pela ANA) é conferido
contra o número de algarismos de `wts_cd_pfafstetterbasin` como checagem de
sanidade, e o resultado da checagem entra no metadado.

ATENÇÃO — cobertura transfronteiriça: Uruguaiana faz fronteira com a
Argentina (Paso de los Libres) pelo rio Uruguai, mas a BHO cobre apenas o
território brasileiro — a porção argentina da bacia não está representada
nestes dados. Se uma visão hidrográfica binacional for necessária no
futuro, HydroBASINS/HydroSHEDS (WWF) é uma fonte complementar
transfronteiriça (não baixada por este script — ver observação no
catálogo de fontes do projeto).

Idempotente: se todos os arquivos de saída já existirem, não baixa de novo
(a menos que --forcar seja usado).

Uso:
    python scripts/download/hidrologia_bho.py
    python scripts/download/hidrologia_bho.py --forcar
"""

import argparse
import json
import logging
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd

sys.path.append(str(Path(__file__).resolve().parents[1] / "utils"))
from recorte_municipio import CRS_PADRAO  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

RAIZ = Path(__file__).resolve().parents[2]
CAMINHO_AREA_BACIAS_DEFAULT = RAIZ / "config" / "area_estudo_bacias.geojson"
CAMINHO_SAIDA_BACIAS_DIR = RAIZ / "data" / "raw" / "vetor"
CAMINHO_SAIDA_DRENAGEM = RAIZ / "data" / "raw" / "vetor" / "rede-hidrografica_ana-bho_atual_vetorial.gpkg"

CRS_ORIGEM_BHO = "EPSG:4674"  # SIRGAS 2000 geográfico (confirmado via ogrinfo em todos os temas)

ID_REGISTRO_BHO6 = "32e309da-a8c1-443f-90ac-0cd79ce6a33d"
BASE_URL_BHO = f"https://metadados.snirh.gov.br/files/{ID_REGISTRO_BHO6}"
URL_FICHA_METADADOS = f"https://metadados.snirh.gov.br/geonetwork/srv/api/records/{ID_REGISTRO_BHO6}"
VERSAO_BHO = "6.2.4"
DATA_PUBLICACAO_BHO = "2022-10-20"

NIVEIS_OTTO = [1, 2, 3, 4, 5, 6, 7]

TEMAS_DRENAGEM = {
    "curso_dagua": "geoft_bho_curso_dagua",
    "trecho_drenagem": "geoft_bho_trecho_drenagem",
    "ponto_drenagem": "geoft_bho_ponto_drenagem",
}

NOTA_ARGENTINA = (
    "A BHO cobre apenas o território brasileiro. Uruguaiana faz fronteira com a Argentina (Paso de los "
    "Libres) pelo rio Uruguai — a porção argentina da(s) bacia(s) não está representada nestes dados. "
    "Fonte complementar para visão transfronteiriça (não baixada aqui): HydroBASINS/HydroSHEDS (WWF), "
    "https://www.hydrosheds.org/products/hydrobasins"
)

# Variáveis de ambiente GDAL para leitura remota eficiente via /vsicurl/, aproveitando o índice espacial
# (R-tree) do GeoPackage para não baixar os arquivos nacionais (2,9-20 GB) por inteiro. Nas camadas
# maiores (trecho_drenagem, ~5,5M feições nacionais) observamos conexões que travam sem erro (nem
# completam nem derrubam a requisição) — GDAL_HTTP_LOW_SPEED_LIMIT/TIME derruba uma conexão parada e
# GDAL_HTTP_MAX_RETRY faz o GDAL reemitir a requisição em vez de travar indefinidamente.
ENV_VSICURL = {
    **os.environ,
    "GDAL_HTTP_MULTIRANGE": "YES",
    "GDAL_DISABLE_READDIR_ON_OPEN": "YES",
    "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".gpkg",
    "GDAL_HTTP_TIMEOUT": "60",
    "GDAL_HTTP_LOW_SPEED_LIMIT": "1000",
    "GDAL_HTTP_LOW_SPEED_TIME": "60",
    "GDAL_HTTP_MAX_RETRY": "5",
    "GDAL_HTTP_RETRY_DELAY": "2",
}


def url_tema(nome_arquivo: str) -> str:
    return f"{BASE_URL_BHO}/{nome_arquivo}.gpkg"


def extrair_subset_remoto(url: str, bbox: tuple, caminho_tmp: Path, camada: str | None = None) -> Path:
    """Extrai um recorte retangular (bbox) de um GeoPackage remoto via ogr2ogr -spat.

    Usa /vsicurl/ + índice espacial do GeoPackage: busca só as páginas do arquivo remoto
    que intersectam o bbox (HTTP range requests), sem baixar o arquivo nacional inteiro.
    """
    minx, miny, maxx, maxy = bbox
    comando = [
        "ogr2ogr",
        "-spat", str(minx), str(miny), str(maxx), str(maxy),
        "-spat_srs", CRS_ORIGEM_BHO,
        str(caminho_tmp),
        f"/vsicurl/{url}",
    ]
    logger.info("Extraindo recorte remoto de %s (bbox %s)", url, bbox)
    resultado = subprocess.run(comando, env=ENV_VSICURL, capture_output=True, text=True, timeout=1800)
    if resultado.returncode != 0:
        raise RuntimeError(f"ogr2ogr falhou para {url}: {resultado.stderr}")
    return caminho_tmp


def carregar_e_recortar_preciso(caminho_tmp: Path, area_estudo_bacias_origem: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Carrega o recorte retangular local e aplica o recorte exato pelo polígono da área de busca."""
    gdf = gpd.read_file(caminho_tmp)
    if gdf.empty:
        return gdf
    gdf = gpd.clip(gdf, area_estudo_bacias_origem)
    return gdf.to_crs(CRS_PADRAO)


def processar_ottobacia_nivel(nivel: int, bbox: tuple, area_estudo_bacias_origem: gpd.GeoDataFrame,
                               tmpdir: Path) -> tuple[gpd.GeoDataFrame, dict]:
    nome_arquivo = f"geoft_bho_ach_otto_nivel_{nivel:02d}"
    url = url_tema(nome_arquivo)
    caminho_tmp = tmpdir / f"{nome_arquivo}.gpkg"
    extrair_subset_remoto(url, bbox, caminho_tmp)
    gdf = carregar_e_recortar_preciso(caminho_tmp, area_estudo_bacias_origem)

    checagem_nivel_ok = True
    if not gdf.empty:
        gdf = gdf.rename(columns={
            "wts_cd_pfafstetterbasin": "codigo_otto",
            "wts_cd_pfafstetterbasincodelevel": "nivel_otto_declarado",
            "wts_gm_area": "area_m2_bho",
        })
        gdf["nivel_otto"] = nivel
        # Checagem de sanidade: nível declarado pela ANA deve bater com o nº de algarismos do código Otto.
        nivel_por_digitos = gdf["codigo_otto"].astype(str).str.len()
        checagem_nivel_ok = bool((gdf["nivel_otto_declarado"] == nivel_por_digitos).all())
        if not checagem_nivel_ok:
            logger.warning("Nível %d: nível declarado difere do nº de algarismos do código Otto em algumas feições.", nivel)

    logger.info("Ottobacias nível %d: %d feição(ões) na área de busca", nivel, len(gdf))
    info = {
        "nivel_otto": nivel,
        "url": url,
        "n_feicoes_area_busca": len(gdf),
        "checagem_nivel_via_digitos_codigo_otto_ok": checagem_nivel_ok,
    }
    return gdf, info


def salvar_ottobacias(gdf: gpd.GeoDataFrame, nivel: int, info: dict) -> Path:
    caminho_saida = CAMINHO_SAIDA_BACIAS_DIR / f"bacias-hidrograficas_ana-bho_nivel{nivel}_vetorial.gpkg"
    caminho_saida.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_file(caminho_saida, driver="GPKG", layer=f"ottobacias_nivel_{nivel}")

    metadados = {
        "fonte": "ANA — Base Hidrográfica Ottocodificada Multiescalas 6 (BHO 6)",
        "url_ficha_metadados": URL_FICHA_METADADOS,
        "url_arquivo_nacional": info["url"],
        "versao": VERSAO_BHO,
        "data_publicacao_fonte": DATA_PUBLICACAO_BHO,
        "data_acesso": datetime.now(timezone.utc).date().isoformat(),
        "nivel_otto_pfafstetter": nivel,
        "checagem_nivel_via_digitos_codigo_otto_ok": info["checagem_nivel_via_digitos_codigo_otto_ok"],
        "n_feicoes": len(gdf),
        "area_busca": "config/area_estudo_bacias.geojson (buffer sobre a área de estudo municipal)",
        "crs_original": CRS_ORIGEM_BHO,
        "crs_processado": CRS_PADRAO,
        "metodo_download": (
            "leitura remota via GDAL /vsicurl/ com filtro espacial (ogr2ogr -spat) sobre o índice espacial "
            "(R-tree) do GeoPackage nacional — não baixa o arquivo nacional inteiro (~54 MB a ~5,6 GB "
            "conforme o nível) — seguido de recorte exato pelo polígono da área de busca e reprojeção"
        ),
        "nota_cobertura_transfronteiriça": NOTA_ARGENTINA,
        "data_processamento": datetime.now(timezone.utc).isoformat(),
    }
    caminho_saida.with_suffix(".json").write_text(
        json.dumps(metadados, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    logger.info("Ottobacias nível %d salvas em %s", nivel, caminho_saida)
    return caminho_saida


def processar_rede_drenagem(bbox: tuple, area_estudo_bacias_origem: gpd.GeoDataFrame, tmpdir: Path) -> dict:
    contagens = {}
    CAMINHO_SAIDA_DRENAGEM.parent.mkdir(parents=True, exist_ok=True)
    if CAMINHO_SAIDA_DRENAGEM.exists():
        CAMINHO_SAIDA_DRENAGEM.unlink()

    for camada, nome_arquivo in TEMAS_DRENAGEM.items():
        url = url_tema(nome_arquivo)
        caminho_tmp = tmpdir / f"{nome_arquivo}.gpkg"
        extrair_subset_remoto(url, bbox, caminho_tmp)
        gdf = carregar_e_recortar_preciso(caminho_tmp, area_estudo_bacias_origem)
        gdf.to_file(CAMINHO_SAIDA_DRENAGEM, driver="GPKG", layer=camada)
        contagens[camada] = {"url": url, "n_feicoes": len(gdf), "geom_type": gdf.geom_type.unique().tolist() if len(gdf) else []}
        logger.info("Camada %s: %d feição(ões) na área de busca", camada, len(gdf))

    tamanho_kb = CAMINHO_SAIDA_DRENAGEM.stat().st_size / 1024
    metadados = {
        "fonte": "ANA — Base Hidrográfica Ottocodificada Multiescalas 6 (BHO 6)",
        "url_ficha_metadados": URL_FICHA_METADADOS,
        "versao": VERSAO_BHO,
        "data_publicacao_fonte": DATA_PUBLICACAO_BHO,
        "data_acesso": datetime.now(timezone.utc).date().isoformat(),
        "camadas": contagens,
        "area_busca": "config/area_estudo_bacias.geojson (buffer sobre a área de estudo municipal)",
        "crs_original": CRS_ORIGEM_BHO,
        "crs_processado": CRS_PADRAO,
        "tamanho_gpkg_kb": round(tamanho_kb, 1),
        "metodo_download": (
            "leitura remota via GDAL /vsicurl/ com filtro espacial (ogr2ogr -spat) sobre o índice espacial "
            "(R-tree) dos GeoPackages nacionais (curso_dagua ~2,9 GB, trecho_drenagem ~4 GB, ponto_drenagem "
            "~926 MB) — não baixa os arquivos nacionais inteiros — seguido de recorte exato pelo polígono "
            "da área de busca e reprojeção"
        ),
        "nota_cobertura_transfronteiriça": NOTA_ARGENTINA,
        "data_processamento": datetime.now(timezone.utc).isoformat(),
    }
    CAMINHO_SAIDA_DRENAGEM.with_suffix(".json").write_text(
        json.dumps(metadados, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    logger.info("Rede de drenagem salva em %s", CAMINHO_SAIDA_DRENAGEM)
    return contagens


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Baixa bacias hidrográficas (ottobacias, níveis 1-7) e rede de drenagem da BHO/ANA."
    )
    parser.add_argument("--area-bacias", type=Path, default=CAMINHO_AREA_BACIAS_DEFAULT,
                         help="Área de busca/recorte (buffer) — default: config/area_estudo_bacias.geojson")
    parser.add_argument("--forcar", action="store_true", help="Baixa novamente mesmo se as saídas já existirem")
    args = parser.parse_args()

    saidas_esperadas = [
        CAMINHO_SAIDA_BACIAS_DIR / f"bacias-hidrograficas_ana-bho_nivel{n}_vetorial.gpkg" for n in NIVEIS_OTTO
    ] + [CAMINHO_SAIDA_DRENAGEM, CAMINHO_SAIDA_DRENAGEM.with_suffix(".json")]
    if all(p.exists() for p in saidas_esperadas) and not args.forcar:
        logger.info("Todas as saídas da BHO já existem — nada a fazer (use --forcar para baixar de novo).")
        return

    if not args.area_bacias.exists():
        raise FileNotFoundError(
            f"{args.area_bacias} não encontrado. Rode primeiro: "
            "python scripts/processamento/area_estudo_bacias.py"
        )

    area_estudo_bacias = gpd.read_file(args.area_bacias)
    area_estudo_bacias_origem = area_estudo_bacias.to_crs(CRS_ORIGEM_BHO)
    bbox = tuple(area_estudo_bacias_origem.total_bounds)
    logger.info("Área de busca (bbox, %s): %s", CRS_ORIGEM_BHO, bbox)

    with tempfile.TemporaryDirectory(prefix="bho_") as tmpdir_str:
        tmpdir = Path(tmpdir_str)

        resumo_niveis = {}
        for nivel in NIVEIS_OTTO:
            caminho_nivel = CAMINHO_SAIDA_BACIAS_DIR / f"bacias-hidrograficas_ana-bho_nivel{nivel}_vetorial.gpkg"
            if caminho_nivel.exists() and caminho_nivel.with_suffix(".json").exists() and not args.forcar:
                logger.info("Nível %d já existe em %s — pulando (use --forcar para refazer).", nivel, caminho_nivel)
                continue
            gdf, info = processar_ottobacia_nivel(nivel, bbox, area_estudo_bacias_origem, tmpdir)
            if gdf.empty:
                logger.warning("Nível %d: nenhuma feição na área de busca — pulando salvamento.", nivel)
                continue
            salvar_ottobacias(gdf, nivel, info)
            resumo_niveis[nivel] = len(gdf)

        if CAMINHO_SAIDA_DRENAGEM.exists() and CAMINHO_SAIDA_DRENAGEM.with_suffix(".json").exists() and not args.forcar:
            logger.info("Rede de drenagem já existe em %s — pulando (use --forcar para refazer).", CAMINHO_SAIDA_DRENAGEM)
            resumo_drenagem = {}
        else:
            resumo_drenagem = processar_rede_drenagem(bbox, area_estudo_bacias_origem, tmpdir)

    logger.info("Concluído. Ottobacias por nível: %s", resumo_niveis)
    logger.info("Rede de drenagem: %s", {k: v["n_feicoes"] for k, v in resumo_drenagem.items()})


if __name__ == "__main__":
    main()
