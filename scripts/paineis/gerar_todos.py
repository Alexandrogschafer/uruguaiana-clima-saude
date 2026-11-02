"""
Roda todos os geradores de dados dos painéis e, no fim, o manifesto.

Cada gerador é um módulo com PRODUTOS (ids que ele grava) e
gerar(codigo_ibge, forcar, so) -> {id: True se gravou | False se já estava igual}.
Idempotente: sem --forcar, nada é regravado se entradas e conteúdo não mudaram.

Uso:
    python scripts/paineis/gerar_todos.py [--codigo-ibge 4322400] [--forcar] [--so <id-do-produto>]
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

AQUI = Path(__file__).resolve().parent
RAIZ = AQUI.parents[1]
sys.path.insert(0, str(AQUI))
import manifesto  # noqa: E402

# Ordem importa: a tabela anual do clima lê o produto da precipitação.
GERADORES: list[Path] = []


def carregar(caminho: Path):
    spec = importlib.util.spec_from_file_location(caminho.stem, caminho)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--codigo-ibge", default="4322400")
    ap.add_argument("--forcar", action="store_true", help="regrava mesmo sem mudança")
    ap.add_argument("--so", help="gera só este produto (id)")
    args = ap.parse_args()
    try:
        modulos = [carregar(g) for g in GERADORES]
        todos = [p for m in modulos for p in m.PRODUTOS]
        if args.so and args.so not in todos:
            raise ValueError(f"produto desconhecido: {args.so}; conhecidos: {', '.join(todos)}")
        for m in modulos:
            if args.so and args.so not in m.PRODUTOS:
                continue
            for pid, gravou in m.gerar(args.codigo_ibge, args.forcar, args.so).items():
                print(f"{pid}: {'gravado' if gravou else 'sem mudança'}")
        mf, esp = manifesto.gerar(args.codigo_ibge)
        print(f"manifesto: {'gravado' if mf else 'sem mudança'}; espera: {'gravada' if esp else 'sem mudança'}")
    except (OSError, ValueError, KeyError) as erro:
        print(f"erro: {erro}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
