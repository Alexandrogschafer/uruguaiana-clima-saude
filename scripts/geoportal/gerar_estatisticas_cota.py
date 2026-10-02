"""
Consolida, por cota de inundação (cota_cm), o tempo de retorno e as unidades
de saúde (ESF/UBS) dentro da mancha, num único JSON para o painel do slider de
inundação do geoportal.

- Tempo de retorno: atributo TR da própria camada de cotas do SGB.
- Unidades de saúde: as 23 unidades da atenção primária (ESF e UBS) do
  cadastro revisado pela equipe do projeto (versão 4, 2026). Definição
  CUMULATIVA: unidade "na mancha" da cota X = dentro da união das manchas de
  cota <= X (as manchas do SGB não são perfeitamente aninhadas). Geometria
  das manchas reparada com buffer(0); ponto no polígono em EPSG:31981.

Histórico: 2026-10-02 — saiu a população exposta preliminar (por área do
setor) e, na mesma data, a contagem de estabelecimentos do CNES antigo
(saude-estabelecimentos-exposicao-inundacao_por-cota.csv), substituída por
esta contagem das unidades ESF/UBS.
"""

import json

import geopandas as gpd

from common import DIR_GEOPORTAL, RAIZ_PROJETO, logger

CRS_PADRAO = "EPSG:31981"
CAMINHO_COTAS = RAIZ_PROJETO / "data" / "raw" / "vetor" / "cotas-inundacao_sgb_atual_vetorial.gpkg"
CAMINHO_UNIDADES = RAIZ_PROJETO / "data" / "processed" / "saude" / "unidades-saude-esf-ubs_cnes-revisado-v4_2026_pontos.gpkg"
CAMINHO_SAIDA = DIR_GEOPORTAL / "estatisticas-por-cota.json"


def main() -> None:
    if CAMINHO_SAIDA.exists():
        logger.info("já existe, pulando: %s", CAMINHO_SAIDA.relative_to(RAIZ_PROJETO))
        return

    cotas = gpd.read_file(CAMINHO_COTAS).to_crs(CRS_PADRAO)
    cotas["geometry"] = cotas.geometry.buffer(0)  # self-intersection em 4 feições da fonte
    unidades = gpd.read_file(CAMINHO_UNIDADES).to_crs(CRS_PADRAO)

    por_cota: dict[str, dict] = {}
    lista_cotas = sorted(int(c) for c in cotas["cota_cm"].unique())
    for cota_cm in lista_cotas:
        grupo = cotas[cotas["cota_cm"] == cota_cm]
        uniao = cotas[cotas["cota_cm"] <= cota_cm].union_all()  # cumulativo
        dentro = unidades[unidades.within(uniao)].sort_values("rotulo")
        por_cota[str(cota_cm)] = {
            "cota_cm": cota_cm,
            "tr_anos": round(float(grupo["tr_anos"].iloc[0]), 1),
            "unidades_saude": {
                "n_na_mancha": int(len(dentro)),
                "unidades": [{"rotulo": r.rotulo, "nome": r.nome, "classe": r.classe} for r in dentro.itertuples()],
            },
        }

    saida = {
        "descricao": (
            "Tempo de retorno e unidades de saúde (ESF/UBS) dentro da mancha de inundação por cota (cota_cm), "
            "com definição cumulativa (união das manchas de cota <= X)."
        ),
        "fonte": {
            "cotas": str(CAMINHO_COTAS.relative_to(RAIZ_PROJETO)),
            "unidades_saude": str(CAMINHO_UNIDADES.relative_to(RAIZ_PROJETO)),
            "credito_unidades": ("CNES (Ministério da Saúde), revisado e corrigido pela equipe do projeto com informações "
                                 "dos profissionais de saúde do município — versão 4, 2026"),
        },
        "status": "pendente de conferência",
        "cotas_disponiveis_cm": lista_cotas,
        "por_cota": por_cota,
    }

    CAMINHO_SAIDA.parent.mkdir(parents=True, exist_ok=True)
    CAMINHO_SAIDA.write_text(json.dumps(saida, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("gerado: %s (%d cotas)", CAMINHO_SAIDA.relative_to(RAIZ_PROJETO), len(por_cota))


if __name__ == "__main__":
    main()
