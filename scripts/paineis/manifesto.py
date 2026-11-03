"""
Gera data/paineis/manifesto.json (lista dos produtos publicados, com data,
sha256, tamanho e telas) e data/paineis/paineis_espera.json (ativos que não
podem ir às telas: dependem de conferência ou estão bloqueados), cada um com
o metadado irmão .json.json.

Idempotente: o manifesto usa a maior data de geração dos produtos, não a hora
da execução, então só muda quando algum produto muda.

Desde a rodada 120 o manifesto lista só os ids declarados em PRODUTOS pelos
geradores (GERADORES, abaixo); um JSON da pasta que nenhum gerador declara é
órfão: fica fora do manifesto e gerar_todos.py avisa (ou, com --limpar, move
para data/paineis/_orfaos/). Cada item traz "substitui" quando o produto
mudou de id, para as telas resolverem o id antigo (js/paineis/dados.js).

Uso:
    python scripts/paineis/manifesto.py [--codigo-ibge 4322400]
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import comum  # noqa: E402

ESQUEMA_MANIFESTO = "climapampa-manifesto/1"
ESQUEMA_ESPERA = "climapampa-espera/1"
ESPECIAIS = {"manifesto.json", "paineis_espera.json"}
AQUI = Path(__file__).resolve().parent
PASTA_ORFAOS = "_orfaos"  # pasta iniciada por "_": o Jekyll do GitHub Pages (modo clássico) não a publica

# (script, função de entrada). Ordem importa: a tabela anual do clima lê o produto da precipitação.
GERADORES: list[tuple[Path, str]] = [
    (comum.RAIZ / "scripts" / "processamento" / "precipitacao_painel_clima.py", "gerar_paineis"),
    (AQUI / "dados_clima.py", "gerar"),
    (AQUI / "dados_saude.py", "gerar"),
    (AQUI / "dados_saude_unidades.py", "gerar"),
    (AQUI / "dados_populacao.py", "gerar"),
    (AQUI / "dados_territorio.py", "gerar"),
    (AQUI / "dados_economia.py", "gerar"),
    (AQUI / "dados_riscos.py", "gerar"),
    (AQUI / "dados_rio.py", "gerar"),
    (AQUI / "dados_inundacao.py", "gerar"),
]


def carregar(caminho: Path):
    spec = importlib.util.spec_from_file_location(caminho.stem, caminho)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def ids_declarados() -> list[str]:
    """Ids que algum gerador declara em PRODUTOS (constantes desde a rodada 120)."""
    return [pid for g, _ in GERADORES for pid in carregar(g).PRODUTOS]


def orfaos(pasta: Path = comum.PASTA_PUBLICACAO, declarados: list[str] | None = None) -> list[Path]:
    """JSONs de produto na pasta que nenhum gerador declara (com o metadado irmão, se houver)."""
    dec = set(ids_declarados() if declarados is None else declarados)
    return [p for p in produtos_publicados(pasta) if p.stem not in dec]


def produtos_publicados(pasta: Path = comum.PASTA_PUBLICACAO) -> list[Path]:
    return sorted(p for p in pasta.glob("*.json") if not p.name.endswith(".json.json") and p.name not in ESPECIAIS)


def _gravar_se_mudou(caminho: Path, conteudo: dict, meta: dict) -> bool:
    texto = json.dumps(conteudo, ensure_ascii=False, indent=1) + "\n"
    texto_meta = json.dumps(meta, ensure_ascii=False, indent=2) + "\n"
    m = caminho.with_name(caminho.name + ".json")
    if caminho.exists() and m.exists() and caminho.read_text(encoding="utf-8") == texto \
            and m.read_text(encoding="utf-8") == texto_meta:
        return False
    caminho.write_text(texto, encoding="utf-8")
    m.write_text(texto_meta, encoding="utf-8")
    return True


def gerar(codigo_ibge: str = "4322400", pasta: Path = comum.PASTA_PUBLICACAO,
          declarados: list[str] | None = None) -> tuple[bool, bool]:
    pasta.mkdir(parents=True, exist_ok=True)
    dec = set(ids_declarados() if declarados is None else declarados)
    itens = []
    for p in produtos_publicados(pasta):
        if p.stem not in dec:
            continue  # órfão: fora do manifesto (gerar_todos.py avisa)
        prod = json.loads(p.read_text(encoding="utf-8"))
        comum.validar_produto(prod)
        itens.append({
            "id": prod["id"], **({"substitui": prod["substitui"]} if prod.get("substitui") else {}), "titulo": prod["titulo"], "ativos": prod["ativos"], "selo": prod["selo"],
            "autorizacao": prod["autorizacao"], "dados_ate": prod["dados_ate"], "gerado_em": prod["gerado_em"],
            "telas": prod.get("telas", []), "bytes": p.stat().st_size, "sha256": comum.sha256_arquivo(p),
        })
    gerado_em = max((i["gerado_em"] for i in itens), default=None)
    manifesto = {"esquema": ESQUEMA_MANIFESTO, "codigo_ibge": codigo_ibge, "gerado_em": gerado_em, "produtos": itens}
    meta_manifesto = {
        "produto": "manifesto.json", "esquema": ESQUEMA_MANIFESTO,
        "fonte": "produtos publicados em data/paineis/ declarados em PRODUTOS pelos geradores",
        "transformacao_aplicada": "lista dos produtos com id, id substituído, título, ativos, selo, autorização, datas, telas, tamanho e sha256; JSON órfão fica fora",
        "script": "scripts/paineis/manifesto.py", "codigo_ibge": codigo_ibge, "data_processamento": gerado_em,
    }

    aut = comum.ler_autorizacoes()
    espera = [{"id": i, "nome": r.get("nome", ""), "motivo": r["autorizacao"]} for i, r in aut.items()
              if r["autorizacao"] != "livre" and not r.get("excecao_premissa", "").strip()]
    lista_espera = {"esquema": ESQUEMA_ESPERA, "codigo_ibge": codigo_ibge, "ativos": espera}
    meta_espera = {
        "produto": "paineis_espera.json", "esquema": ESQUEMA_ESPERA,
        "fonte": "config/paineis_ativos.csv",
        "transformacao_aplicada": "ativos sem autorização 'livre' e sem exceção por premissa; id, nome e motivo",
        "script": "scripts/paineis/manifesto.py", "codigo_ibge": codigo_ibge,
        "data_processamento": None, "sha256_entrada": comum.sha256_arquivo(comum.ARQ_ATIVOS),
    }
    return (_gravar_se_mudou(pasta / "manifesto.json", manifesto, meta_manifesto),
            _gravar_se_mudou(pasta / "paineis_espera.json", lista_espera, meta_espera))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--codigo-ibge", default="4322400")
    args = ap.parse_args()
    try:
        m, e = gerar(args.codigo_ibge)
    except (OSError, ValueError) as erro:
        print(f"erro: {erro}", file=sys.stderr)
        return 1
    print(f"manifesto {'gravado' if m else 'sem mudança'}; lista de espera {'gravada' if e else 'sem mudança'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
