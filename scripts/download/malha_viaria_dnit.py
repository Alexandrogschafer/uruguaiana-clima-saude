"""Baixa a malha rodoviária FEDERAL oficial (SNV — Sistema Nacional de
Viação, DNIT) recortada pela área de estudo do município. Gera:

    data/raw/vetor/malha-viaria_dnit-snv_{versao}_vetorial.gpkg
    data/raw/vetor/malha-viaria_dnit-snv_{versao}_vetorial.json

Por que DNIT (não OSM) para esta camada
------------------------------------------
O projeto já tem malha viária via OpenStreetMap
(scripts/download/infraestrutura_osm.py) — mapeamento colaborativo, sem
hierarquia oficial nem classificação de pavimento confiável. O SNV é a
base OFICIAL do DNIT: cada trecho tem jurisdição (federal/estadual),
número da BR, extensão e classificação de superfície/situação
(Pavimentada, Duplicada, Planejada, Implantada, Em obras...) — hierarquia
que o OSM não tem de forma consistente. As duas camadas são
complementares (ver comparação com OSM na validação deste script), não
substitutas uma da outra.

Só DNIT (federal) — DAER-RS (estadual) investigado e descartado
------------------------------------------------------------------------
Decisão do usuário (2026-08-11): DAER-RS não tem download vetorial
programático — só um visualizador WMS (i3geo, mapa.daer.rs.gov.br)
que devolve apenas imagem PNG mesmo quando a requisição pede
SERVICE=WFS (o proxy do DAER ignora o parâmetro e força WMS sempre,
confirmado testando a requisição real) e relatórios "SRE" em XLS sem
geometria. Documentado como indisponível em
data/raw/malha-viaria_daer_indisponivel.json (mesmo padrão usado para
saneamento_snis.py) — não implementado aqui.

Fonte e método (WebDAV público do DNIT, sem API REST documentada)
------------------------------------------------------------------------
O SNV em shapefile fica num compartilhamento público do Nextcloud do
DNIT ("dnitcloud"), sem link direto documentado publicamente — descoberto
via PROPFIND WebDAV no link de compartilhamento (não há portal de
"clique para baixar" fácil de achar, mas o protocolo WebDAV padrão
funciona sem autenticação adicional além do token do link). A versão mais
recente é descoberta dinamicamente listando a pasta (não hardcoded),
então o script sempre pega a última publicação do DNIT.

Uso:
    python scripts/download/malha_viaria_dnit.py
    python scripts/download/malha_viaria_dnit.py --forcar
"""

import argparse
import json
import logging
import re
import sys
import zipfile
from datetime import date, datetime, timezone
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree

import geopandas as gpd
import requests

sys.path.append(str(Path(__file__).resolve().parents[1] / "utils"))
from recorte_municipio import CRS_PADRAO, carregar_area_estudo, recortar_vetor  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

RAIZ = Path(__file__).resolve().parents[2]
CODIGO_IBGE_DEFAULT = "4322400"  # Uruguaiana, RS — usado só pra resolver nome/UF do município no metadado

TOKEN_COMPARTILHAMENTO = "oTpPRmYs5AAdiNr"
URL_WEBDAV_BASE = "http://servicos.dnit.gov.br/dnitcloud/public.php/webdav"
PASTA_SNV_SHP = "SNV Bases Geométricas (2013-Atual) (SHP)"
NS_DAV = {"d": "DAV:"}
HEADERS = {"User-Agent": "Mozilla/5.0 (compativel; script-pesquisa-ClimaPampa/1.0)"}


def listar_webdav(caminho_pasta: str) -> list[str]:
    resposta = requests.request(
        "PROPFIND", f"{URL_WEBDAV_BASE}/{caminho_pasta}/",
        auth=(TOKEN_COMPARTILHAMENTO, ""), headers={**HEADERS, "Depth": "1"}, timeout=60,
    )
    resposta.raise_for_status()
    raiz = ElementTree.fromstring(resposta.content)
    hrefs = [el.text for el in raiz.findall(".//d:href", NS_DAV)]
    return [h for h in hrefs if h and not h.rstrip("/").endswith(caminho_pasta)]


def encontrar_snv_mais_recente() -> tuple[str, str]:
    """Retorna (nome_arquivo, versao) do .zip mais recente na pasta de bases SHP —
    nomes seguem o padrão SNV_AAAAMMx.zip, ordenação alfabética já é cronológica."""
    hrefs = listar_webdav(PASTA_SNV_SHP)
    zips = sorted(h for h in hrefs if h.lower().endswith(".zip"))
    if not zips:
        raise RuntimeError(f"Nenhum .zip encontrado em '{PASTA_SNV_SHP}' no WebDAV do DNIT.")
    ultimo = zips[-1]
    nome_arquivo = ultimo.rstrip("/").split("/")[-1]
    versao = re.search(r"(\d{6}[A-Z]?)", nome_arquivo).group(1)
    return nome_arquivo, versao


def baixar_e_extrair_snv(nome_arquivo: str) -> gpd.GeoDataFrame:
    url = f"{URL_WEBDAV_BASE}/{PASTA_SNV_SHP}/{nome_arquivo}"
    logger.info("Baixando %s (~70MB)...", url)
    resposta = requests.get(url, auth=(TOKEN_COMPARTILHAMENTO, ""), headers=HEADERS, timeout=180)
    resposta.raise_for_status()

    with zipfile.ZipFile(BytesIO(resposta.content)) as z:
        nome_shp = next(n for n in z.namelist() if n.lower().endswith(".shp"))
        base = nome_shp[:-4]
        extensoes_necessarias = [".shp", ".shx", ".dbf", ".prj", ".cpg"]
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            for ext in extensoes_necessarias:
                membro = base + ext
                if membro in z.namelist():
                    z.extract(membro, tmpdir)
            gdf = gpd.read_file(Path(tmpdir) / (base + ".shp"))
    return gdf


def obter_municipio_uf(codigo_ibge: str) -> tuple[str, str]:
    resposta = requests.get(f"https://servicodados.ibge.gov.br/api/v1/localidades/municipios/{codigo_ibge}", timeout=30)
    resposta.raise_for_status()
    dados = resposta.json()
    return dados["nome"], dados["microrregiao"]["mesorregiao"]["UF"]["sigla"]


def main() -> None:
    parser = argparse.ArgumentParser(description="Baixa a malha rodoviária federal (SNV/DNIT) recortada pela área de estudo.")
    parser.add_argument("--codigo-ibge", default=CODIGO_IBGE_DEFAULT, help="Código IBGE do município (default: Uruguaiana/RS) — usado só para o metadado, o recorte é pela área de estudo do projeto")
    parser.add_argument("--forcar", action="store_true", help="Baixa novamente mesmo se o arquivo já existir")
    args = parser.parse_args()

    nome_municipio, uf_sigla = obter_municipio_uf(args.codigo_ibge)
    logger.info("Município: %s (%s)", nome_municipio, uf_sigla)

    nome_arquivo, versao = encontrar_snv_mais_recente()
    caminho_saida = RAIZ / "data" / "raw" / "vetor" / f"malha-viaria_dnit-snv_{versao}_vetorial.gpkg"
    if caminho_saida.exists() and not args.forcar:
        logger.info("%s já existe — nada a fazer (use --forcar para baixar de novo).", caminho_saida)
        return

    logger.info("Versão mais recente do SNV encontrada: %s (%s)", versao, nome_arquivo)
    gdf_nacional = baixar_e_extrair_snv(nome_arquivo)
    logger.info("SNV nacional: %d trechos, CRS original %s", len(gdf_nacional), gdf_nacional.crs)

    if gdf_nacional.crs.to_string() != CRS_PADRAO:
        gdf_nacional = gdf_nacional.to_crs(CRS_PADRAO)

    area_estudo = carregar_area_estudo()
    gdf_municipio = recortar_vetor(gdf_nacional, area_estudo)
    logger.info("Trechos DNIT dentro da área de estudo: %d", len(gdf_municipio))

    n_invalidas = int((~gdf_municipio.geometry.is_valid).sum())

    # vl_extensa é a extensão do trecho OFICIAL completo (referência linear nacional do SNV),
    # que costuma continuar em municípios vizinhos — não a parte que cai dentro da área de
    # estudo. km_dentro_area_estudo (geometria já recortada, EPSG:31981 métrico) é a extensão
    # real dentro do município — achado real comparando as duas (diferença de até ~4x em
    # alguns trechos), não suposição. Mantido vl_extensa como veio da fonte, adicionada a coluna
    # derivada para não confundir as duas métricas.
    gdf_municipio["km_dentro_area_estudo"] = (gdf_municipio.geometry.length / 1000).round(2)

    caminho_saida.parent.mkdir(parents=True, exist_ok=True)
    gdf_municipio.to_file(caminho_saida, driver="GPKG", layer="malha_viaria_federal")

    resumo_classificacao = (
        gdf_municipio.groupby(["vl_br", "ds_legenda"])["km_dentro_area_estudo"].sum().round(1).to_dict()
        if "vl_br" in gdf_municipio.columns and "ds_legenda" in gdf_municipio.columns else {}
    )
    resumo_classificacao_str = {f"BR-{k[0]} ({k[1]})": v for k, v in resumo_classificacao.items()}

    metadados = {
        "fonte": "DNIT — Sistema Nacional de Viação (SNV), base geométrica oficial das rodovias federais",
        "url_origem": f"{URL_WEBDAV_BASE}/{PASTA_SNV_SHP}/{nome_arquivo}",
        "versao_snv": versao,
        "codigo_ibge": args.codigo_ibge,
        "nome_municipio": nome_municipio,
        "uf": uf_sigla,
        "nivel_agregacao": "trecho rodoviário federal (segmento entre marcos de km) — hierarquia oficial (BR, jurisdição, situação/pavimento), diferente do OSM já integrado (sem essa classificação oficial)",
        "n_trechos_municipio": len(gdf_municipio),
        "n_geometrias_invalidas": n_invalidas,
        "extensao_km_por_br_e_classificacao": resumo_classificacao_str,
        "colunas_principais": {
            "vl_br": "número da rodovia (ex.: 472 = BR-472)",
            "ds_jurisdi": "jurisdição (Federal/Estadual — trechos com jurisdição estadual mas numeração BR também aparecem no SNV)",
            "ds_legenda / ds_sup_fed": "situação/classificação de pavimento: Pavimentada, Duplicada, Planejada, Implantada, etc.",
            "vl_km_inic / vl_km_fina / vl_extensa": "marcos de quilometragem e EXTENSÃO DO TRECHO OFICIAL COMPLETO (referência linear nacional do SNV) — pode ir muito além do município, NÃO usar para 'quantos km passam por Uruguaiana'",
            "km_dentro_area_estudo": "coluna DERIVADA (calculada aqui, não da fonte) — comprimento real da geometria já recortada pela área de estudo; esta é a métrica certa para 'km de rodovia federal no município'",
        },
        "escopo_dnit_apenas": (
            "só rodovias FEDERAIS (DNIT/SNV) — DAER-RS (estadual) investigado e descartado por não "
            "ter download vetorial programático (só WMS de imagem, sem WFS; confirmado testando a "
            "requisição real — o proxy do DAER ignora SERVICE=WFS e sempre devolve PNG); documentado "
            "separadamente em data/raw/malha-viaria_daer_indisponivel.json"
        ),
        "data_acesso": date.today().isoformat(),
        "data_processamento": datetime.now(timezone.utc).isoformat(),
    }
    caminho_saida.with_suffix(".json").write_text(json.dumps(metadados, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

    logger.info("Salvo: %s (%d trechos, versão SNV %s)", caminho_saida, len(gdf_municipio), versao)
    logger.info("Metadados salvos em %s", caminho_saida.with_suffix(".json"))


if __name__ == "__main__":
    main()
