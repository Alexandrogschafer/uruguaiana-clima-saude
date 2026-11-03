"""
CSV de cada produto tabular de data/paineis/ em data/paineis/csv/<id>.csv, com o
metadado irmão <id>.csv.json.

Derivado só do JSON já publicado: a supressão e a autorização já estão aplicadas
(valor suprimido = null = célula vazia). Produto tabular = "dados" é uma lista de
registros com valores simples (texto, número, lógico ou nulo); os demais ficam só
com o JSON e são listados por esta função. Cabeçalho em português (os nomes das
colunas do produto), ponto decimal, UTF-8, separador vírgula; lógico vira
"sim"/"não". CSV de produto que deixou de existir é apagado.

Idempotente: só regrava se o conteúdo mudou. A data do metadado é a do produto
(gerado_em), não a da execução.

Uso:
    python scripts/paineis/exportar_csv.py [--forcar]
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import comum  # noqa: E402

PASTA = comum.PASTA_PUBLICACAO
PASTA_CSV = PASTA / "csv"
SCRIPT = "scripts/paineis/exportar_csv.py"
ESPECIAIS = {"manifesto.json", "paineis_espera.json"}
PRODUTOS: list[str] = []  # não declara produto JSON: grava só os CSV derivados


def produtos_json() -> list[Path]:
    return sorted(p for p in PASTA.glob("*.json") if not p.name.endswith(".json.json") and p.name not in ESPECIAIS)


def tabular(prod: dict) -> bool:
    d = prod.get("dados")
    return (isinstance(d, list) and len(d) > 0 and all(isinstance(r, dict) for r in d)
            and all(v is None or isinstance(v, (str, int, float, bool)) for r in d for v in r.values()))


def colunas(prod: dict) -> list[str]:
    """Ordem das colunas declarada no produto; coluna presente nos dados e não declarada vai ao fim."""
    decl = [c["nome"] for c in prod.get("colunas", [])]
    extra = [k for r in prod["dados"] for k in r if k not in decl]
    return decl + list(dict.fromkeys(extra))


def celula(v) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "sim" if v else "não"
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() else repr(v)  # ponto decimal; 757.0 vira 757
    return str(v)


def texto_csv(prod: dict) -> str:
    cols = colunas(prod)
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(cols)
    for r in prod["dados"]:
        w.writerow([celula(r.get(c)) for c in cols])
    return buf.getvalue()


def caminho_csv(id_: str) -> Path:
    return PASTA_CSV / f"{id_}.csv"


def gerar(codigo_ibge: str = "4322400", forcar: bool = False, so: str | None = None) -> dict[str, bool]:
    PASTA_CSV.mkdir(parents=True, exist_ok=True)
    out = {}
    vivos = set()
    for p in produtos_json():
        prod = json.loads(p.read_text(encoding="utf-8"))
        if so and so != prod["id"]:
            continue
        if not tabular(prod):
            continue
        c = caminho_csv(prod["id"])
        vivos.add(c.name)
        txt = texto_csv(prod)
        meta = {
            "produto": c.name, "formato": "CSV (UTF-8, separador vírgula, ponto decimal, célula vazia = sem valor ou suprimido)",
            "fonte": f"data/paineis/{p.name}", "sha256_fonte": comum.sha256_arquivo(p),
            "transformacao_aplicada": "registros de \"dados\" do produto JSON publicado, uma linha por registro, colunas na ordem declarada; lógico como sim/não",
            "colunas": colunas(prod), "n_linhas": len(prod["dados"]), "script": SCRIPT, "codigo_ibge": prod["codigo_ibge"],
            "selo": prod["selo"], "autorizacao": prod["autorizacao"], "data_processamento": prod["gerado_em"],
        }
        mtxt = json.dumps(meta, ensure_ascii=False, indent=2) + "\n"
        m = c.with_name(c.name + ".json")
        mudou = forcar or not (c.exists() and m.exists() and c.read_text(encoding="utf-8") == txt and m.read_text(encoding="utf-8") == mtxt)
        if mudou:
            c.write_text(txt, encoding="utf-8")
            m.write_text(mtxt, encoding="utf-8")
        out[f"csv/{c.name}"] = mudou
    if not so:
        # CSV de produto que deixou de existir ou deixou de ser tabular
        for f in sorted(PASTA_CSV.glob("*.csv")):
            if f.name not in vivos:
                f.unlink()
                f.with_name(f.name + ".json").unlink(missing_ok=True)
                out[f"csv/{f.name} (apagado)"] = True
    return out


def nao_tabulares() -> list[str]:
    return [json.loads(p.read_text(encoding="utf-8"))["id"] for p in produtos_json()
            if not tabular(json.loads(p.read_text(encoding="utf-8")))]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--codigo-ibge", default="4322400")
    ap.add_argument("--forcar", action="store_true")
    ap.add_argument("--so")
    a = ap.parse_args()
    try:
        for k, g in gerar(a.codigo_ibge, a.forcar, a.so).items():
            print(f"{k}: {'gravado' if g else 'sem mudança'}")
        for i in nao_tabulares():
            print(f"{i}: não tabular, só JSON")
    except (OSError, ValueError, KeyError) as erro:
        print(f"erro: {erro}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
