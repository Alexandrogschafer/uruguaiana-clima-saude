"""Baixa os polígonos de imóveis rurais cadastrados no CAR (Cadastro
Ambiental Rural) para um município, via WFS público do SICAR. Gera:

    data/raw/vetor/estrutura-fundiaria_car-sicar_atual_vetorial.gpkg
    data/raw/vetor/estrutura-fundiaria_car-sicar_atual_vetorial.json

Escopo desta camada (leia antes de usar)
-------------------------------------------
Só o polígono do IMÓVEL + atributos básicos (área, status, tipo, módulos
fiscais, data de criação). NÃO inclui Reserva Legal, APP declarada nem
Área de Uso Consolidado — essas sub-camadas só existem no ZIP de download
por município do portal consultapublica.car.gov.br, que exige resolver um
reCAPTCHA manualmente antes de cada download (confirmado lendo o JS do
site, downloads.js — endpoint .../estados/downloadBase?...&ReCaptcha=...).
Este projeto não automatiza contorno de captcha, então essas sub-camadas
ficam de fora (decisão do usuário, 2026-08-11: usar só o que é acessível
sem captcha). Se precisar delas no futuro: baixar o ZIP manualmente e
escrever um script de parsing separado que leia o que estiver em
data/raw/ (mesmo padrão usado para outras indisponibilidades do projeto,
ver scripts/download/saneamento_snis.py).

Fonte e método
---------------
WFS público do SICAR (geoserver.car.gov.br/geoserver/sicar/wfs), camada
sicar:sicar_imoveis_{uf} — uma camada por estado. Filtro por município
feito NO PRÓPRIO SERVIÇO via CQL_FILTER cod_municipio_ibge=<código IBGE>
(sem hardcode de nome de município, sem precisar baixar o estado inteiro
e recortar localmente — o servidor já devolve só os imóveis do
município). Toda a resposta (1672 imóveis para Uruguaiana) veio numa
única requisição, sem paginação necessária (GeoServer não limitou por
padrão neste serviço — se algum município maior estourar timeout, seria o
próximo ajuste). CRS original EPSG:4674 (SIRGAS2000 geográfico),
reprojetado para EPSG:31981 (padrão do projeto).

UF do WFS resolvida a partir do código IBGE (via API de localidades do
IBGE), não hardcoded — parametrizável para outro município/estado.

Uso:
    python scripts/download/estrutura_fundiaria_car.py
    python scripts/download/estrutura_fundiaria_car.py --codigo-ibge 4314902 --forcar
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
from recorte_municipio import CRS_PADRAO, carregar_area_estudo  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

RAIZ = Path(__file__).resolve().parents[2]
CAMINHO_SAIDA = RAIZ / "data" / "raw" / "vetor" / "estrutura-fundiaria_car-sicar_atual_vetorial.gpkg"

CODIGO_IBGE_DEFAULT = "4322400"  # Uruguaiana, RS
CRS_ORIGEM_CAR = "EPSG:4674"
URL_WFS_BASE = "https://geoserver.car.gov.br/geoserver/sicar/wfs"
URL_MUNICIPIO_IBGE = "https://servicodados.ibge.gov.br/api/v1/localidades/municipios/{codigo}"
URL_CONSULTAPUBLICA = "https://consultapublica.car.gov.br/publico/municipios/downloads"

COLUNAS_RELEVANTES = {
    "cod_imovel": "codigo_imovel_car",
    "status_imovel": "status_imovel",
    "condicao": "condicao_analise",
    "dat_criacao": "data_criacao_cadastro",
    "area": "area_declarada_ha",
    "m_fiscal": "modulos_fiscais",
    "tipo_imovel": "tipo_imovel",
    "municipio": "municipio",
    "cod_municipio_ibge": "codigo_ibge_municipio",
}


def obter_municipio_uf(codigo_ibge: str) -> tuple[str, str]:
    resposta = requests.get(URL_MUNICIPIO_IBGE.format(codigo=codigo_ibge), timeout=30)
    resposta.raise_for_status()
    dados = resposta.json()
    return dados["nome"], dados["microrregiao"]["mesorregiao"]["UF"]["sigla"]


def baixar_imoveis_car(codigo_ibge: str, uf_sigla: str, timeout: int = 120) -> gpd.GeoDataFrame:
    typename = f"sicar:sicar_imoveis_{uf_sigla.lower()}"
    url = (
        f"{URL_WFS_BASE}?service=wfs&version=2.0.0&request=GetFeature"
        f"&typeName={typename}&CQL_FILTER=cod_municipio_ibge={codigo_ibge}"
        f"&outputFormat=application/json"
    )
    gdf = gpd.read_file(url)
    if gdf.empty:
        return gdf
    if gdf.crs is None:
        gdf = gdf.set_crs(CRS_ORIGEM_CAR)
    return gdf


def processar(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    colunas_presentes = [c for c in COLUNAS_RELEVANTES if c in gdf.columns]
    gdf = gdf[[*colunas_presentes, "geometry"]].rename(columns=COLUNAS_RELEVANTES)
    return gdf.to_crs(CRS_PADRAO)


def main() -> None:
    parser = argparse.ArgumentParser(description="Baixa polígonos de imóveis rurais do CAR (WFS público do SICAR) para um município.")
    parser.add_argument("--codigo-ibge", default=CODIGO_IBGE_DEFAULT, help="Código IBGE do município (default: Uruguaiana/RS)")
    parser.add_argument("--forcar", action="store_true", help="Baixa novamente mesmo se o arquivo já existir")
    args = parser.parse_args()

    if CAMINHO_SAIDA.exists() and not args.forcar:
        logger.info("%s já existe — nada a fazer (use --forcar para baixar de novo).", CAMINHO_SAIDA)
        return

    nome_municipio, uf_sigla = obter_municipio_uf(args.codigo_ibge)
    logger.info("Município: %s (%s) — código IBGE %s", nome_municipio, uf_sigla, args.codigo_ibge)

    gdf_bruto = baixar_imoveis_car(args.codigo_ibge, uf_sigla)
    if gdf_bruto.empty:
        logger.warning("Nenhum imóvel CAR encontrado para o código IBGE %s (camada sicar_imoveis_%s).", args.codigo_ibge, uf_sigla.lower())
        return
    logger.info("%d imóveis CAR baixados (WFS, camada sicar:sicar_imoveis_%s).", len(gdf_bruto), uf_sigla.lower())

    gdf = processar(gdf_bruto)
    n_invalidas = int((~gdf.geometry.is_valid).sum())
    if n_invalidas:
        logger.warning("%d geometria(s) inválida(s) após reprojeção — mantidas como estão (não corrigidas automaticamente).", n_invalidas)

    area_estudo = carregar_area_estudo()
    area_municipio_km2 = area_estudo.geometry.area.sum() / 1e6
    area_total_car_km2 = gdf["area_declarada_ha"].sum() / 100
    cobertura_pct = 100 * area_total_car_km2 / area_municipio_km2

    CAMINHO_SAIDA.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_file(CAMINHO_SAIDA, driver="GPKG", layer="imoveis_car")

    metadados = {
        "fonte": "CAR (Cadastro Ambiental Rural) / SICAR — SFB/MMA, WFS público (geoserver.car.gov.br)",
        "url_servico": URL_WFS_BASE,
        "camada_wfs": f"sicar:sicar_imoveis_{uf_sigla.lower()}",
        "codigo_ibge": args.codigo_ibge,
        "nome_municipio": nome_municipio,
        "uf": uf_sigla,
        "data_acesso": datetime.now(timezone.utc).date().isoformat(),
        "n_imoveis": len(gdf),
        "n_geometrias_invalidas": n_invalidas,
        "crs_original": CRS_ORIGEM_CAR,
        "crs_processado": CRS_PADRAO,
        "metodo": (
            "consulta direta ao WFS público do SICAR, filtrando por cod_municipio_ibge=<código IBGE> "
            "no próprio serviço (CQL_FILTER, sem hardcode de nome de município e sem precisar baixar "
            "o estado inteiro para recortar localmente); resposta única, sem paginação necessária"
        ),
        "campos_mantidos": list(COLUNAS_RELEVANTES.values()),
        "distribuicao_status_imovel": gdf["status_imovel"].value_counts().to_dict(),
        "distribuicao_tipo_imovel": gdf["tipo_imovel"].value_counts().to_dict(),
        "area_total_declarada_km2": round(area_total_car_km2, 2),
        "area_municipio_km2": round(area_municipio_km2, 2),
        "cobertura_pct_area_municipal": round(cobertura_pct, 1),
        "escopo_nao_incluido": (
            "Reserva Legal, APP declarada e Área de Uso Consolidado NÃO estão nesta camada — só existem "
            "no ZIP de download por município do portal consultapublica.car.gov.br/publico/municipios/"
            "downloads, que exige resolver reCAPTCHA manualmente antes de cada download (confirmado no "
            "JS do site, downloads.js). Automação de contorno de captcha não é feita neste projeto. Se "
            "precisar dessas sub-camadas: baixar o ZIP manualmente e escrever parsing separado."
        ),
        "url_portal_manual_para_subcamadas": URL_CONSULTAPUBLICA,
        "nota_sobreposicao": (
            "imóveis rurais no CAR podem se sobrepor entre si (autodeclaração, sujeita a análise/"
            "conflito) — área_total_declarada_km2 é a SOMA do atributo 'area' de cada polígono, não a "
            "área geométrica dissolvida (união); pode ultrapassar a área municipal se houver "
            "sobreposição ou imóveis que extrapolam o limite do município"
        ),
        "data_processamento": datetime.now(timezone.utc).isoformat(),
    }
    caminho_metadados = CAMINHO_SAIDA.with_suffix(".json")
    caminho_metadados.write_text(json.dumps(metadados, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

    logger.info("Salvo: %s (%d imóveis)", CAMINHO_SAIDA, len(gdf))
    logger.info("Área total declarada: %.1f km² (%.1f%% da área municipal, %.1f km²)", area_total_car_km2, cobertura_pct, area_municipio_km2)
    logger.info("Metadados salvos em %s", caminho_metadados)


if __name__ == "__main__":
    main()
