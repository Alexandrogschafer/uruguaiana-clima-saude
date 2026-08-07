"""
Consolida as duas tabelas de exposição por cota de inundação (população e
estabelecimentos de saúde) num único JSON indexado por cota_cm, para o
painel do slider de inundação do geoportal (evita o front-end ter que
fazer join de dois CSVs em JS).

As cotas disponíveis nestes CSVs (833/952/1205/1252 cm) já foram
confirmadas como idênticas às de setores-inundacao.geojson e
cotas-inundacao.geojson — não há cota "órfã" em nenhum dos arquivos.
"""

import json

import pandas as pd

from common import DIR_GEOPORTAL, RAIZ_PROJETO, logger

CAMINHO_POPULACAO = RAIZ_PROJETO / "data" / "processed" / "populacao-exposta-inundacao_por-cota.csv"
CAMINHO_SAUDE = RAIZ_PROJETO / "data" / "processed" / "saude-estabelecimentos-exposicao-inundacao_por-cota.csv"
CAMINHO_SAIDA = DIR_GEOPORTAL / "estatisticas-por-cota.json"


def main() -> None:
    if CAMINHO_SAIDA.exists():
        logger.info("já existe, pulando: %s", CAMINHO_SAIDA.relative_to(RAIZ_PROJETO))
        return

    df_pop = pd.read_csv(CAMINHO_POPULACAO)
    df_saude = pd.read_csv(CAMINHO_SAUDE)

    por_cota: dict[str, dict] = {}
    for _, linha in df_pop.iterrows():
        cota = str(int(linha["cota_cm"]))
        campos_populacao = linha.drop(labels=["cota_cm", "tr_anos"]).to_dict()
        por_cota[cota] = {
            "cota_cm": int(linha["cota_cm"]),
            "tr_anos": float(linha["tr_anos"]),
            "populacao": campos_populacao,
            "saude": {"n_estabelecimentos_total": 0, "por_tipo": {}},
        }

    for _, linha in df_saude.iterrows():
        cota = str(int(linha["cota_cm"]))
        if cota not in por_cota:
            logger.warning("cota %s presente em saúde mas ausente em população, ignorando", cota)
            continue
        n = int(linha["n_estabelecimentos"])
        por_cota[cota]["saude"]["por_tipo"][linha["tipo_unidade_categoria"]] = n
        por_cota[cota]["saude"]["n_estabelecimentos_total"] += n

    saida = {
        "descricao": (
            "Estatísticas de população e estabelecimentos de saúde expostos por cota de "
            "inundação (cota_cm), consolidadas de populacao-exposta-inundacao_por-cota.csv "
            "e saude-estabelecimentos-exposicao-inundacao_por-cota.csv."
        ),
        "fonte": {
            "populacao": str(CAMINHO_POPULACAO.relative_to(RAIZ_PROJETO)),
            "saude": str(CAMINHO_SAUDE.relative_to(RAIZ_PROJETO)),
        },
        "cotas_disponiveis_cm": sorted(int(c) for c in por_cota),
        "por_cota": por_cota,
    }

    CAMINHO_SAIDA.parent.mkdir(parents=True, exist_ok=True)
    CAMINHO_SAIDA.write_text(json.dumps(saida, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("gerado: %s (%d cotas)", CAMINHO_SAIDA.relative_to(RAIZ_PROJETO), len(por_cota))


if __name__ == "__main__":
    main()
