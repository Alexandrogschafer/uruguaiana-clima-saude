"""
Catálogo de dados publicado em data/paineis/catalogo_dados.json (lido por
dashboards/catalogo.html e pelo bloco "Dados desta tela" do rodapé).

Fonte: data/catalogo_fontes.csv, só as linhas ligadas a um produto publicado em
data/paineis/ ou a uma camada publicada em data/geoportal/. A ligação fica em
config/catalogo_ligacao.csv (tema do catálogo → produto ou camada), porque os
metadados dos produtos e das camadas registram os arquivos de origem, mas não a
linha do catálogo; este script confere cada ligação contra os metadados
publicados (produto declarado por um gerador e com metadado; camada com arquivo
em data/geoportal/) e descarta a que não confere.

Publica: tema, fonte, tipo, licença ("ver fonte" quando não identificável),
resolução espacial e temporal, data de acesso, selo, dimensões, telas, produtos e
camadas com os downloads. NÃO publica a coluna observacao, a url de acesso nem o
script responsável. Por produto: licença, data de coleta e método ("Sobre este
dado" no rodapé das telas). As linhas sem ligação e os produtos ou camadas sem
linha NÃO entram no JSON: saem só no terminal (--conferencia), para o relatório.

Uso:
    python scripts/paineis/dados_catalogo.py [--codigo-ibge 4322400] [--forcar]
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import comum  # noqa: E402
import exportar_csv  # noqa: E402

RAIZ = comum.RAIZ
PASTA = comum.PASTA_PUBLICACAO
GEO = RAIZ / "data" / "geoportal"
ARQ_CATALOGO = RAIZ / "data" / "catalogo_fontes.csv"
ARQ_LIGACAO = RAIZ / "config" / "catalogo_ligacao.csv"
SCRIPT = "scripts/paineis/dados_catalogo.py"
ID = "catalogo_dados"
PRODUTOS = [ID]
DERIVADOS = {"inicio_resumo", "catalogo_dados"}
ESPECIAIS = {"manifesto.json", "paineis_espera.json"}

# tela → dimensão do catálogo (filtro da tabela); camada do mapa → "mapa"
DIMENSOES = {"clima": "Clima", "saude": "Saúde", "populacao": "População", "territorio": "Território",
             "economia": "Economia", "riscos": "Riscos", "cheia-saude": "Cheia e saúde", "mapa": "Mapa"}
# licença que não identifica os termos de uso: a tela mostra "ver fonte"
RE_SEM_LICENCA = re.compile(r"^\s*$|^n/?a$|não informad|sem licença explícita|licença não informada", re.IGNORECASE)


def ler_csv(arq: Path) -> list[dict]:
    with arq.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def produtos_publicados() -> dict[str, dict]:
    """Produtos declarados por algum gerador, com arquivo e metadado irmão (sem os derivados desta rodada)."""
    import manifesto  # noqa: E402  (import tardio: manifesto carrega os geradores, inclusive este)
    out = {}
    for pid in manifesto.ids_declarados():
        p, m = comum.caminhos(pid)
        if pid in DERIVADOS or not (p.exists() and m.exists()):
            continue
        out[pid] = {"prod": json.loads(p.read_text(encoding="utf-8")), "meta": json.loads(m.read_text(encoding="utf-8"))}
    return out


def camada_publicada(cid: str) -> dict | None:
    """Camada do mapa: pasta (uso-solo/, terreno/, bacias/) ou arquivo .geojson/.json em data/geoportal/."""
    if cid.endswith("/"):
        d = GEO / cid.rstrip("/")
        return {"id": cid, "download": None} if d.is_dir() and any(d.iterdir()) else None
    g = GEO / f"{cid}.geojson"
    if g.exists():
        return {"id": cid, "download": f"data/geoportal/{g.name}"}
    j = GEO / f"{cid}.json"
    return {"id": cid, "download": None} if j.exists() else None


def selos_camadas() -> dict[str, str]:
    """Selo das camadas pelo arquivo de origem registrado em config/paineis_ativos.csv."""
    out = {}
    for r in ler_csv(comum.ARQ_ATIVOS):
        a = (r.get("arquivo_origem") or "").strip()
        if a.startswith("data/geoportal/") and (r.get("selo") or "").strip():
            out[Path(a).stem if not a.endswith("/") else a[len("data/geoportal/"):]] = r["selo"].strip()
    return out


def licenca(txt: str) -> str:
    return "ver fonte" if RE_SEM_LICENCA.search(txt or "") else txt.strip()


def gerar(codigo_ibge: str = "4322400", forcar: bool = False, so: str | None = None) -> dict[str, bool]:
    if so and so != ID:
        return {}
    cat = {r["tema"]: r for r in ler_csv(ARQ_CATALOGO)}
    lig = ler_csv(ARQ_LIGACAO)
    prods = produtos_publicados()
    selo_cam = selos_camadas()
    ruins = []
    por_tema: dict[str, dict] = {}
    for l in lig:
        tema, tipo, i = l["tema_catalogo"].strip(), l["tipo"].strip(), l["id"].strip()
        if tema not in cat:
            ruins.append(f"{tema}: tema fora de data/catalogo_fontes.csv")
            continue
        alvo = por_tema.setdefault(tema, {"produtos": [], "camadas": []})
        if tipo == "produto":
            if i in prods:
                alvo["produtos"].append(i)
            else:
                ruins.append(f"{tema} → produto {i}: não publicado")
        elif tipo == "camada":
            c = camada_publicada(i)
            if c:
                alvo["camadas"].append(c)
            else:
                ruins.append(f"{tema} → camada {i}: não publicada")
        else:
            ruins.append(f"{tema}: tipo de ligação desconhecido ({tipo})")

    dados = []
    for tema, r in cat.items():
        alvo = por_tema.get(tema)
        if not alvo or not (alvo["produtos"] or alvo["camadas"]):
            continue
        telas = sorted({t for pid in alvo["produtos"] for t in prods[pid]["prod"].get("telas", []) if t in DIMENSOES})
        dims = [DIMENSOES[t] for t in telas] + (["Mapa"] if alvo["camadas"] else [])
        selos = [prods[pid]["prod"]["selo"] for pid in alvo["produtos"]] + [selo_cam[c["id"].rstrip("/")] for c in alvo["camadas"] if c["id"].rstrip("/") in selo_cam]
        selo = min(selos, key=lambda s: comum.FORCA_SELO[s]) if selos else None
        dados.append({
            "tema": tema, "fonte": r["fonte"].strip(), "tipo": r["tipo"].strip(), "licenca": licenca(r["licenca"]),
            "resolucao_espacial": r["resolucao_espacial"].strip() or None, "resolucao_temporal": r["resolucao_temporal"].strip() or None,
            "data_acesso": r["data_acesso"].strip() or None, "selo": selo, "dimensoes": " · ".join(dict.fromkeys(dims)),
            "telas": " ".join(telas) or None,
            "produtos": " ".join(sorted(dict.fromkeys(alvo["produtos"]))) or None,
            "camadas": " ".join(c["id"] for c in alvo["camadas"]) or None,
            "downloads_camadas": " ".join(c["download"] for c in alvo["camadas"] if c["download"]) or None,
        })

    # por produto: o que o "Sobre este dado" do rodapé mostra além do próprio produto
    sobre = {}
    for pid, x in sorted(prods.items()):
        temas = [d for d in dados if d["produtos"] and pid in d["produtos"].split()]
        lic = sorted({d["licenca"] for d in temas}) or ["ver fonte"]
        acesso = sorted({d["data_acesso"] for d in temas if d["data_acesso"]})
        sobre[pid] = {"titulo": x["prod"]["titulo"], "licenca": "; ".join(lic), "data_coleta": acesso[-1] if acesso else None,
                      "metodo": x["meta"].get("transformacao_aplicada"), "fontes_catalogo": [d["tema"] for d in temas],
                      "csv": f"data/paineis/csv/{pid}.csv" if exportar_csv.tabular(x["prod"]) else None}
    # inicio_resumo e catalogo_dados também têm download (o CSV sai no fim do gerar_todos)
    for pid in sorted(DERIVADOS):
        sobre[pid] = {"titulo": None, "licenca": "ver as fontes de cada linha", "data_coleta": None, "metodo": None, "fontes_catalogo": [],
                      "csv": f"data/paineis/csv/{pid}.csv"}

    usados = {pid for d in dados if d["produtos"] for pid in d["produtos"].split()}
    global CONFERENCIA
    CONFERENCIA = {
        "linhas_sem_ligacao": sorted(t for t in cat if t not in {d["tema"] for d in dados}),
        "produtos_sem_linha": sorted(set(prods) - usados),
        "camadas_sem_linha": sorted(c for c in camadas_do_geoportal() if c not in {x["id"] for a in por_tema.values() for x in a["camadas"]}),
        "ligacoes_descartadas": ruins,
        "sem_licenca_identificavel": sorted(d["tema"] for d in dados if d["licenca"] == "ver fonte"),
    }
    colunas = [{"nome": n, "tipo": "texto"} for n in ["tema", "fonte", "tipo", "licenca", "resolucao_espacial", "resolucao_temporal",
                                                       "data_acesso", "selo", "dimensoes", "telas", "produtos", "camadas", "downloads_camadas"]]
    ativos = sorted({a for pid in usados for a in prods[pid]["prod"]["ativos"]})
    selo = min((prods[pid]["prod"]["selo"] for pid in usados), key=lambda s: comum.FORCA_SELO[s])
    acessos = sorted(d["data_acesso"] for d in dados if d["data_acesso"] and re.match(r"^\d{4}-\d{2}-\d{2}$", d["data_acesso"]))
    p = comum.montar_produto(
        id=ID, codigo_ibge=codigo_ibge, ativos=ativos, titulo="Catálogo de dados: fontes dos produtos e das camadas publicados",
        fonte_curta="catálogo", fonte="data/catalogo_fontes.csv (linhas ligadas a produto ou camada publicados)",
        unidade_espacial="não se aplica", unidade_medida="não se aplica",
        periodo={"inicio": acessos[0], "fim": acessos[-1]}, dados_ate=acessos[-1], selo=selo,
        avisos=[{"tipo": "metodo", "texto": "data de acesso é a da coleta registrada no catálogo; \"ver fonte\" quando a licença não está identificada"}],
        colunas=colunas, dados=dados, script=SCRIPT,
        extras={"fonte_rodape": "Catálogo de fontes do projeto (data/catalogo_fontes.csv)", "telas": ["catalogo"], "dimensoes": list(dict.fromkeys(DIMENSOES.values())),
                "limiar_supressao": comum.LIMIAR_SUPRESSAO, "sobre_produtos": sobre})
    entradas = [ARQ_CATALOGO, ARQ_LIGACAO, comum.ARQ_ATIVOS] + [comum.caminhos(pid)[0] for pid in sorted(prods)] + [comum.caminhos(pid)[1] for pid in sorted(prods)]
    gravou = comum.gravar_produto(p, transformacao="linhas de catalogo_fontes.csv ligadas (config/catalogo_ligacao.csv) a produto ou camada publicados; sem observacao, url e script",
                                  entradas=entradas, forcar=forcar)
    return {ID: gravou}


CONFERENCIA: dict = {}


def camadas_do_geoportal() -> list[str]:
    """Camadas publicadas (para a lista de camadas sem linha no catálogo): .geojson e as pastas de imagens."""
    out = [g.stem for g in sorted(GEO.glob("*.geojson"))]
    out += [f"{d.name}/" for d in sorted(GEO.iterdir()) if d.is_dir()]
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--codigo-ibge", default="4322400")
    ap.add_argument("--forcar", action="store_true")
    ap.add_argument("--so")
    ap.add_argument("--conferencia", action="store_true", help="lista linhas sem ligação, produtos e camadas sem linha (não publicado)")
    a = ap.parse_args()
    try:
        for pid, g in gerar(a.codigo_ibge, a.forcar, a.so).items():
            print(f"{pid}: {'gravado' if g else 'sem mudança'}")
        if a.conferencia:
            print(json.dumps(CONFERENCIA, ensure_ascii=False, indent=1))
    except (OSError, ValueError, KeyError) as erro:
        print(f"erro: {erro}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
