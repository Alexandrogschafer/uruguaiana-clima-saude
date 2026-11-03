"""
Produto das unidades de saúde da tela de saúde em data/paineis/
(esquema climapampa-painel/1), a partir da camada de trabalho do cadastro
revisado (versão 4):
    data/processed/saude/unidades-saude-esf-ubs_cnes-revisado-v4_<ano>_pontos.gpkg

Grava saude_unidades-esf-ubs_atual (id estável; o ano do cadastro fica no JSON): as 21 unidades ESF/UBS e a Equipe de
Saúde Prisional (classe "sem classe"), com CNES, nome, classe, zona, bairro e a
população informada pelos profissionais de saúde em 2017 e 2023.

Decisões (rodada 111; premissas P5 e P6 do relatório de especificação):
    - sem coordenadas (a localização está no portal), sem o ano de 2026, sem
      soma e sem ordenação por população: a ordem é a do cadastro (ESF pelo
      número, depois UBS, depois a Prisional);
    - população vazia fica null ("—"), nunca 0;
    - a Equipe de Saúde Prisional vai com fora_das_comparacoes = true e sem
      população: não entra em contagens, rankings, somas nem comparações;
    - selo "em_confirmacao": o conceito da população (cadastrada, adscrita ou
      atendida) está pendente.
CRS: a camada está em EPSG:31981, mas nenhuma coordenada é publicada.

Uso:
    python scripts/paineis/dados_saude_unidades.py [--codigo-ibge 4322400] [--forcar]
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import geopandas as gpd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import comum  # noqa: E402

RAIZ = comum.RAIZ
PASTA_SAUDE = RAIZ / "data" / "processed" / "saude"
SCRIPT = "scripts/paineis/dados_saude_unidades.py"
CLASSES_COMPARAVEIS = {"ESF", "UBS"}
ANOS_POPULACAO = ["2017", "2023"]  # 2026 não é publicado (queda geral ainda sem explicação)
AVISO_POPULACAO = ("Número informado pelos profissionais de saúde. O conceito (cadastrada, adscrita ou atendida) "
                   "está em confirmação. Não somar nem comparar com o Censo.")
FONTE = ("CNES (Ministério da Saúde), revisado e corrigido pela equipe do projeto com informações dos profissionais "
         "de saúde do município — versão 4")


def entrada() -> Path:
    """Camada v4 de coleta mais recente (comum.bruto_mais_recente); o ano do cadastro fica no JSON, não no id."""
    return comum.bruto_mais_recente(PASTA_SAUDE, "unidades-saude-esf-ubs_cnes-revisado-v4_*_pontos.gpkg")


def ano_cadastro(arq: Path) -> str:
    return re.search(r"-v4_(\d{4})_", arq.name).group(1)


def id_produto() -> str:
    """Id estável (retrato substituído a cada revisão do cadastro): convenção de comum.py."""
    return "saude_unidades-esf-ubs_atual"


def calcular(arq: Path) -> list[dict]:
    g = gpd.read_file(arq)
    obrig = {"cnes", "nome", "classe", "zona", "bairro"} | {f"populacao{a}" for a in ANOS_POPULACAO}
    faltam = obrig - set(g.columns)
    if faltam:
        raise ValueError(f"colunas ausentes na camada de unidades: {sorted(faltam)}")
    if len(g) != 22:
        raise ValueError(f"esperadas 22 unidades no cadastro v4, há {len(g)}")
    linhas = []
    for r in g.itertuples():
        comparavel = r.classe in CLASSES_COMPARAVEIS
        linha = {"cnes": str(r.cnes), "nome": r.nome, "classe": r.classe, "zona": r.zona,
                 "bairro": comum.limpar_valor(r.bairro), "fora_das_comparacoes": not comparavel}
        for a in ANOS_POPULACAO:
            v = comum.limpar_valor(getattr(r, f"populacao{a}"))
            # Prisional: sem números de população (não tem área de abrangência)
            linha[f"populacao_informada_{a}"] = None if (v is None or not comparavel) else int(v)
        linhas.append(linha)
    n_comp = sum(not l["fora_das_comparacoes"] for l in linhas)
    if n_comp != 21:
        raise ValueError(f"esperadas 21 unidades ESF/UBS, há {n_comp}")
    # comparáveis primeiro, na ordem do cadastro; a Prisional na última linha
    return [l for l in linhas if not l["fora_das_comparacoes"]] + [l for l in linhas if l["fora_das_comparacoes"]]


def gerar(codigo_ibge: str = "4322400", forcar: bool = False, so: str | None = None) -> dict[str, bool]:
    arq = entrada()
    pid = id_produto()
    if so and so != pid:
        return {}
    dados = calcular(arq)
    ano = ano_cadastro(arq)
    p = comum.montar_produto(
        id=pid, codigo_ibge=codigo_ibge, ativos=["SA01", "SA02"],
        titulo="Unidades de saúde da atenção primária (ESF/UBS) e população informada",
        fonte_curta="CNES revisado v4", fonte=f"{FONTE} ({ano})", unidade_espacial="unidade de saúde",
        unidade_medida="pessoas (população informada)", periodo={"inicio": ANOS_POPULACAO[0], "fim": ANOS_POPULACAO[-1]},
        dados_ate=f"{ano}", selo="em_confirmacao",
        avisos=[{"tipo": "em_confirmacao", "texto": AVISO_POPULACAO},
                {"tipo": "metodo", "texto": "Equipe de Saúde Prisional: sem classe, fora das comparações e sem números de população"}],
        colunas=[{"nome": "cnes", "tipo": "texto"}, {"nome": "nome", "tipo": "texto"},
                 {"nome": "classe", "tipo": "texto", "valores": ["ESF", "UBS", "sem classe"]},
                 {"nome": "zona", "tipo": "texto"}, {"nome": "bairro", "tipo": "texto"},
                 {"nome": "fora_das_comparacoes", "tipo": "bool"}]
                + [{"nome": f"populacao_informada_{a}", "tipo": "int"} for a in ANOS_POPULACAO],
        dados=dados, script=SCRIPT, campos_contagem=[f"populacao_informada_{a}" for a in ANOS_POPULACAO],
        extras={"fonte_rodape": f"Unidades de saúde — {FONTE} ({ano})", "telas": ["saude"], "comparacao": False},
    )
    return {pid: comum.gravar_produto(
        p, transformacao="seleção de CNES, nome, classe, zona, bairro e população informada de 2017 e 2023; sem coordenadas, "
                         "sem 2026 e sem soma; Prisional sem população e com fora_das_comparacoes = true",
        entradas=[arq], forcar=forcar)}


PRODUTOS = [id_produto()]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--codigo-ibge", default="4322400")
    ap.add_argument("--forcar", action="store_true")
    args = ap.parse_args()
    try:
        for pid, gravou in gerar(args.codigo_ibge, args.forcar).items():
            print(f"{pid}: {'gravado' if gravou else 'sem mudança'}")
    except (OSError, ValueError, KeyError) as erro:
        print(f"erro: {erro}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
