"""Tenta baixar a malha rodoviária ESTADUAL (DAER-RS) via API/Python. NÃO
PRODUZ dado vetorial — as rotas investigadas (validadas por requisição
real, não suposição) têm uma barreira que impede automação limpa.
Documenta a limitação:

    data/raw/malha-viaria_daer_indisponivel.json

Rotas investigadas e por que nenhuma virou um download automatizável
-----------------------------------------------------------------------
1. Visualizador i3geo (mapa.daer.rs.gov.br/i3geo/interface/ol.htm): tem
   camadas relevantes ("estaduais", "municipais", "rod_sre") servidas via
   um proxy PHP próprio (classesphp/mapa_openlayers.php) que só devolve
   imagem PNG — confirmado testando a requisição real com
   SERVICE=WFS&REQUEST=GetFeature: o proxy ignora o parâmetro SERVICE e
   sempre responde como WMS (Content-Type: image/png), mesmo pedindo
   GML/SHAPE-ZIP explicitamente. Sem WFS/download vetorial exposto.
2. Páginas institucionais (daer.rs.gov.br/composicao-da-malha,
   /sistema-rodoviario-estadual): só têm relatórios "SRE" (Sistema
   Rodoviário Estadual) em planilha XLS, SEM geometria — lista de trechos
   com atributos tabulares, não uma base vetorial.

Comparar com o DNIT (que teve sucesso nesta mesma rodada)
------------------------------------------------------------
O DNIT publica o SNV (rodovias federais) em shapefile de verdade via um
compartilhamento WebDAV público (ver malha_viaria_dnit.py) — o DAER não
tem um equivalente descoberto nesta investigação.

Alternativa não seguida (decisão do usuário, 2026-08-11)
-------------------------------------------------------------
Usar o OSM (já integrado via infraestrutura_osm.py) filtrado por
ref=RS-* como aproximação da malha estadual — descartado por ora: não é
fonte oficial do DAER, e o pedido original era especificamente por
hierarquia OFICIAL (federal/estadual/municipal) que o OSM não garante de
forma consistente.

Se alguém quiser revisitar: os relatórios SRE (XLS) têm a lista completa
de rodovias estaduais com quilometragem e situação — dá pra usar como
indicador tabular de contexto (sem geometria) se for útil no futuro, ou
pedir a alguém com acesso ao DAER um shapefile por ofício/LAI.

Uso:
    python scripts/download/malha_viaria_daer_indisponivel.py
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

RAIZ = Path(__file__).resolve().parents[2]
CAMINHO_SAIDA = RAIZ / "data" / "raw" / "malha-viaria_daer_indisponivel.json"
HEADERS = {"User-Agent": "Mozilla/5.0 (compativel; script-pesquisa-ClimaPampa/1.0)"}

URL_I3GEO = "https://mapa.daer.rs.gov.br/i3geo/interface/ol.htm"
URL_WFS_TESTE = "https://mapa.daer.rs.gov.br/i3geo/classesphp/mapa_openlayers.php?layer=estaduais&SERVICE=WFS&VERSION=1.1.0&REQUEST=GetFeature&TYPENAME=estaduais&OUTPUTFORMAT=GML2&MAXFEATURES=2"
URL_COMPOSICAO_MALHA = "https://www.daer.rs.gov.br/sistema-rodoviario-estadual"


def verificar_i3geo_forca_wms() -> dict:
    resposta = requests.get(URL_WFS_TESTE, headers=HEADERS, timeout=30)
    return {
        "url_testada": URL_WFS_TESTE,
        "status_code": resposta.status_code,
        "content_type_recebido": resposta.headers.get("Content-Type"),
        "ignorou_service_wfs_forcou_wms": resposta.headers.get("Content-Type") == "image/png",
    }


def verificar_pagina_institucional_sem_geometria() -> dict:
    resposta = requests.get(URL_COMPOSICAO_MALHA, headers=HEADERS, timeout=30)
    tem_apenas_xls = ".xls" in resposta.text.lower() and not any(
        ext in resposta.text.lower() for ext in [".shp", ".geojson", ".kml"]
    )
    return {
        "url_testada": URL_COMPOSICAO_MALHA,
        "status_code": resposta.status_code,
        "so_tem_relatorios_xls_sem_geometria": tem_apenas_xls,
    }


def main() -> None:
    logger.info("Verificando as 2 rotas de acesso ao DAER-RS (chamadas reais, não suposição)...")

    verificacao_i3geo = verificar_i3geo_forca_wms()
    logger.info("i3geo: pediu WFS, recebeu Content-Type=%s (forçou WMS=%s)", verificacao_i3geo["content_type_recebido"], verificacao_i3geo["ignorou_service_wfs_forcou_wms"])

    verificacao_pagina = verificar_pagina_institucional_sem_geometria()
    logger.info("Página institucional: só relatórios XLS sem geometria=%s", verificacao_pagina["so_tem_relatorios_xls_sem_geometria"])

    documentacao = {
        "fonte": "DAER-RS (Departamento Autônomo de Estradas de Rodagem do Rio Grande do Sul)",
        "resultado": "NÃO baixado — nenhuma rota investigada permite download vetorial programático",
        "rotas_investigadas": {
            "1_visualizador_i3geo": {
                "url": URL_I3GEO,
                "camadas_relevantes_encontradas": ["estaduais", "municipais", "rod_sre"],
                "verificacao": verificacao_i3geo,
                "resultado": "proxy PHP do i3geo só serve WMS (imagem) — ignora SERVICE=WFS e sempre devolve PNG, mesmo pedindo GML/SHAPE-ZIP explicitamente",
            },
            "2_paginas_institucionais_sre": {
                "url": URL_COMPOSICAO_MALHA,
                "verificacao": verificacao_pagina,
                "resultado": "só relatórios 'SRE' em planilha XLS (lista de trechos com atributos), sem geometria",
            },
        },
        "comparacao_com_dnit": (
            "o DNIT (rodovias federais) TEVE sucesso nesta mesma rodada — publica o SNV em shapefile "
            "de verdade via WebDAV público (ver malha_viaria_dnit.py) — o DAER não tem equivalente "
            "encontrado"
        ),
        "alternativa_nao_seguida": (
            "usar o OSM (já integrado via infraestrutura_osm.py) filtrado por ref=RS-* como "
            "aproximação — descartado por decisão do usuário (2026-08-11): não é fonte oficial, e o "
            "pedido original era especificamente por hierarquia OFICIAL do DAER"
        ),
        "recomendacao": (
            "se a malha estadual oficial for essencial no futuro, os caminhos mais realistas são: "
            "(a) pedir por ofício/LAI ao DAER um shapefile, ou (b) usar os relatórios SRE (XLS, "
            "atualizados com frequência, ver daer.rs.gov.br/sistema-rodoviario-estadual) como "
            "indicador TABULAR de contexto (rodovia, extensão, situação), sem geometria"
        ),
        "data_verificacao": datetime.now(timezone.utc).isoformat(),
    }

    CAMINHO_SAIDA.parent.mkdir(parents=True, exist_ok=True)
    CAMINHO_SAIDA.write_text(json.dumps(documentacao, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    logger.info("Documentação da limitação salva em %s", CAMINHO_SAIDA)


if __name__ == "__main__":
    main()
