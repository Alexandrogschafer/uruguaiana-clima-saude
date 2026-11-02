"""
Teste das regras de publicação do repositório público, sobre data/paineis/,
dashboards/, css/, js/ e index.html.

Regras:
    a) termos proibidos: regex lidos do arquivo local .git/info/padroes_proibidos.txt
       (fora do git; os termos nunca são escritos aqui). Sem o arquivo, o teste
       falha com "lista local ausente", em vez de passar sem varrer;
    b) caminho local de usuário (padrão importado da trava pré-commit);
    c) nenhum ponto de endereço: produtos de ativos sensíveis (PD12, SA10, IN13)
       nunca publicados; campos de coordenada só em produtos de ativos de ponto
       permitidos (unidades de saúde, estação, pontes e travessias);
    d) todo ativo dos produtos é "livre" ou tem exceção em config/paineis_ativos.csv;
    e) supressão: colunas de contagem sem valor de 1 a 4 (e sem 0 se suprimir_zero);
       cada item de "suprimidos" aponta para null;
    f) JSON <= 100 kB e PNG <= 300 kB em data/paineis/;
    g) todo .json de data/paineis/ tem o metadado irmão .json.json com os campos
       obrigatórios (e o produto, os campos do esquema climapampa-painel/1);
    h) nenhum produto de LST (ativos CA09 a CA16);
    i) premissas: Equipe de Saúde Prisional (CNES 4126947) fora de produtos de
       comparação; população por unidade (SA02) com selo "em_confirmacao" e sem soma;
    j) saúde: nenhuma série anual de óbitos por afogamento ou enchente; nenhum campo
       de soma ou total de população por unidade; produto de unidades sem 2026 e com a
       Prisional fora das comparações e sem população; selo em_confirmacao;
    k) IN15 (publicado × contagem) nunca publicado; ativo não livre só com exceção
       registrada em config/paineis_ativos.csv (as exceções em uso são listadas).

Uso:
    python scripts/paineis/test_regras_publicas.py
Sai com código 1 se alguma regra falhar.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import re
import sys
from pathlib import Path

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI))
import comum  # noqa: E402

RAIZ = comum.RAIZ
PASTA = comum.PASTA_PUBLICACAO
ALVOS = [RAIZ / "data" / "paineis", RAIZ / "dashboards", RAIZ / "css", RAIZ / "js", RAIZ / "index.html"]
EXT_TEXTO = {".html", ".js", ".css", ".json", ".geojson", ".csv", ".md", ".txt"}
ARQ_PADROES = RAIZ / ".git" / "info" / "padroes_proibidos.txt"
HOOK = RAIZ / "scripts" / "git_hooks" / "pre-commit"
ATIVOS_SENSIVEIS = {"PD12", "SA10", "IN13"}
ATIVOS_PONTO_PERMITIDOS = {"SA01", "CA05", "IN24"}
CAMPOS_COORDENADA = {"lat", "lon", "lng", "latitude", "longitude", "x", "y", "geometry", "coordinates"}
ATIVOS_LST = {f"CA{i:02d}" for i in range(9, 17)}
CNES_PRISIONAL = "4126947"
LIMITE_JSON = 100 * 1024
LIMITE_PNG = 300 * 1024
ESPECIAIS = {"manifesto.json", "paineis_espera.json"}
CAMPOS_METADADO_ESPECIAL = ["produto", "esquema", "fonte", "transformacao_aplicada", "script"]


def regex_caminho_local() -> re.Pattern:
    """O mesmo padrão da regra b da trava pré-commit, lido do próprio hook (sem repeti-lo aqui)."""
    loader = importlib.machinery.SourceFileLoader("trava_pre_commit", str(HOOK))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod.RE_CAMINHO_USUARIO


RE_CAMINHO_LOCAL = regex_caminho_local()


def arquivos_alvo() -> list[Path]:
    out = []
    for a in ALVOS:
        if a.is_file():
            out.append(a)
        elif a.is_dir():
            out.extend(p for p in sorted(a.rglob("*")) if p.is_file())
    return out


def produtos() -> list[tuple[Path, dict]]:
    out = []
    for p in sorted(PASTA.glob("*.json")):
        if p.name.endswith(".json.json") or p.name in ESPECIAIS:
            continue
        out.append((p, json.loads(p.read_text(encoding="utf-8"))))
    return out


def regra_a(arqs: list[Path]) -> list[str]:
    if not ARQ_PADROES.exists():
        return ["lista local ausente (.git/info/padroes_proibidos.txt); rode scripts/git_hooks/instalar.sh e preencha"]
    padroes = [l.strip() for l in ARQ_PADROES.read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")]
    if not padroes:
        return ["lista local vazia (.git/info/padroes_proibidos.txt)"]
    regs = [re.compile(p, re.IGNORECASE) for p in padroes]
    falhas = []
    for f in arqs:
        rel = f.relative_to(RAIZ).as_posix()
        texto = f.read_text(encoding="utf-8", errors="ignore") if f.suffix.lower() in EXT_TEXTO else ""
        for i, r in enumerate(regs, 1):
            if r.search(rel) or (texto and r.search(texto)):
                falhas.append(f"{rel}: casa o padrão local nº {i}")  # sem mostrar o termo
    return falhas


def regra_b(arqs: list[Path]) -> list[str]:
    return [f"{f.relative_to(RAIZ).as_posix()}: caminho local de usuário" for f in arqs
            if f.suffix.lower() in EXT_TEXTO and RE_CAMINHO_LOCAL.search(f.read_text(encoding="utf-8", errors="ignore"))]


def regra_c(prods) -> list[str]:
    falhas = []
    for p, d in prods:
        ativos = set(d.get("ativos", []))
        if ativos & ATIVOS_SENSIVEIS:
            falhas.append(f"{p.name}: ativo sensível {sorted(ativos & ATIVOS_SENSIVEIS)}")
        chaves = {k.lower() for linha in d.get("dados", []) if isinstance(linha, dict) for k in linha}
        coord = chaves & CAMPOS_COORDENADA
        if coord and not ativos <= ATIVOS_PONTO_PERMITIDOS:
            falhas.append(f"{p.name}: campos de coordenada {sorted(coord)} em produto de ativos {sorted(ativos)}")
        if d.get("type") in ("FeatureCollection", "Feature"):
            falhas.append(f"{p.name}: GeoJSON não é formato de produto de painel")
    return falhas


def regra_d(prods) -> list[str]:
    aut = comum.ler_autorizacoes()
    falhas = []
    for p, d in prods:
        try:
            comum.conferir_autorizacao(d.get("ativos", []), aut)
        except comum.ErroAutorizacao as e:
            falhas.append(f"{p.name}: {e}")
    return falhas


def regra_e(prods) -> list[str]:
    falhas = []
    for p, d in prods:
        sup = d.get("supressao", {})
        zero = sup.get("suprimir_zero", False)
        contagem = [c["nome"] for c in d.get("colunas", []) if c.get("contagem")]
        for i, linha in enumerate(d.get("dados", [])):
            for c in contagem:
                v = linha.get(c)
                if v is not None and ((0 < v < comum.LIMIAR_SUPRESSAO) or (v == 0 and zero)):
                    falhas.append(f"{p.name}: linha {i}, {c} = valor pequeno publicado")
        for s in d.get("suprimidos", []):
            try:
                if d["dados"][s["linha"]][s["campo"]] is not None:
                    falhas.append(f"{p.name}: suprimido na linha {s['linha']} ({s['campo']}) não é null")
            except (IndexError, KeyError):
                falhas.append(f"{p.name}: item de 'suprimidos' aponta para linha ou campo inexistente")
        if d.get("suprimidos") and not contagem:
            falhas.append(f"{p.name}: há suprimidos sem coluna marcada como contagem")
    return falhas


def regra_f() -> list[str]:
    falhas = []
    for f in sorted(PASTA.rglob("*")):
        if f.suffix == ".json" and f.stat().st_size > LIMITE_JSON:
            falhas.append(f"{f.name}: {f.stat().st_size} bytes (> 100 kB)")
        if f.suffix.lower() == ".png" and f.stat().st_size > LIMITE_PNG:
            falhas.append(f"{f.name}: {f.stat().st_size} bytes (> 300 kB)")
    return falhas


def regra_g() -> list[str]:
    falhas = []
    for p in sorted(PASTA.glob("*.json")):
        if p.name.endswith(".json.json"):
            if not p.with_name(p.name[:-5]).exists():
                falhas.append(f"{p.name}: metadado sem produto")
            continue
        m = p.with_name(p.name + ".json")
        if not m.exists():
            falhas.append(f"{p.name}: sem metadado irmão .json.json")
            continue
        meta = json.loads(m.read_text(encoding="utf-8"))
        obrig = CAMPOS_METADADO_ESPECIAL if p.name in ESPECIAIS else comum.CAMPOS_OBRIGATORIOS_METADADO
        faltam = [c for c in obrig if c not in meta]
        if faltam:
            falhas.append(f"{m.name}: faltam {faltam}")
        if p.name not in ESPECIAIS:
            try:
                comum.validar_produto(json.loads(p.read_text(encoding="utf-8")))
            except comum.ErroProduto as e:
                falhas.append(str(e))
    return falhas


def regra_h(prods) -> list[str]:
    return [f"{p.name}: produto de LST {sorted(set(d['ativos']) & ATIVOS_LST)}" for p, d in prods if set(d.get("ativos", [])) & ATIVOS_LST]


def regra_i(prods) -> list[str]:
    falhas = []
    for p, d in prods:
        texto = json.dumps(d.get("dados", []), ensure_ascii=False)
        if d.get("comparacao") and CNES_PRISIONAL in texto:
            falhas.append(f"{p.name}: Equipe de Saúde Prisional num produto de comparação")
        if "SA02" in d.get("ativos", []):
            if d.get("selo") != "em_confirmacao":
                falhas.append(f"{p.name}: população por unidade sem selo em_confirmacao")
            if any(re.search(r"total|soma", c["nome"]) for c in d.get("colunas", [])):
                falhas.append(f"{p.name}: população por unidade com coluna de soma")
    return falhas


ATIVOS_SAUDE = {"SA01", "SA02", "SA03", "SA04", "SA05"}
RE_AFOGAMENTO = re.compile(r"afogamento|enchente", re.IGNORECASE)
RE_SOMA = re.compile(r"soma|total", re.IGNORECASE)


def regra_j(prods) -> list[str]:
    """Saúde: (a) sem série anual de afogamento/enchente; (b) sem soma de população por unidade;
    (c) unidades sem 2026 e Prisional fora das comparações; (d) população com selo em_confirmacao."""
    falhas = []
    for p, d in prods:
        ativos = set(d.get("ativos", []))
        if not (ativos & ATIVOS_SAUDE or d.get("id", "").startswith("saude_")):
            continue
        colunas = [c["nome"] for c in d.get("colunas", [])]
        chaves = {k for linha in d.get("dados", []) for k in linha}
        # (a) série anual do agravo: coluna "ano" junto com coluna de afogamento, ou produto do agravo com "ano"
        if "ano" in chaves and (any(RE_AFOGAMENTO.search(c) for c in chaves) or RE_AFOGAMENTO.search(d.get("id", ""))):
            falhas.append(f"{p.name}: série anual de óbitos por afogamento ou enchente")
        if ativos & {"SA01", "SA02"}:
            # (b) soma ou total de população
            if any(RE_SOMA.search(c) for c in colunas + list(chaves)) or any(RE_SOMA.search(k) and "popul" in k for k in d):
                falhas.append(f"{p.name}: campo de soma ou total de população por unidade")
            # (c) sem 2026; Prisional fora das comparações e sem população
            if any("2026" in c for c in colunas + list(chaves)):
                falhas.append(f"{p.name}: campo de 2026 na população por unidade")
            if d.get("comparacao"):
                falhas.append(f"{p.name}: produto de unidades marcado como comparação")
            for linha in d.get("dados", []):
                if str(linha.get("cnes")) == CNES_PRISIONAL:
                    pops = [v for k, v in linha.items() if k.startswith("populacao")]
                    if linha.get("fora_das_comparacoes") is not True or any(v is not None for v in pops):
                        falhas.append(f"{p.name}: Equipe de Saúde Prisional sem fora_das_comparacoes ou com população")
            # (d) selo
            if "SA02" in ativos and d.get("selo") != "em_confirmacao":
                falhas.append(f"{p.name}: população por unidade sem selo em_confirmacao")
    # nenhuma chave anual do agravo em nenhum arquivo publicado (inclui metadados)
    for f in sorted(PASTA.glob("*.json")):
        if "obitos_afogamento_enchente" in f.read_text(encoding="utf-8"):
            falhas.append(f"{f.name}: contém a coluna anual obitos_afogamento_enchente")
    return falhas


ATIVOS_FORA = {"IN15"}  # publicado × contagem: fora por decisão


def regra_k(prods) -> list[str]:
    """Telas novas: IN15 nunca publicado; ativo não livre só com exceção registrada (as exceções em uso são listadas)."""
    falhas = []
    aut = comum.ler_autorizacoes()
    for p, d in prods:
        ativos = set(d.get("ativos", []))
        if ativos & ATIVOS_FORA:
            falhas.append(f"{p.name}: ativo fora da publicação {sorted(ativos & ATIVOS_FORA)}")
        for a in sorted(ativos):
            r = aut.get(a, {})
            if r.get("autorizacao") != "livre":
                print(f"    · exceção em uso: {a} ({r.get('autorizacao')}) em {p.name}: {r.get('excecao_premissa', '').strip()[:90]}")
    return falhas


def main() -> int:
    arqs = arquivos_alvo()
    prods = produtos()
    regras = {
        "a termos proibidos (lista local)": regra_a(arqs),
        "b caminho local de usuário": regra_b(arqs),
        "c pontos de endereço": regra_c(prods),
        "d autorização dos ativos": regra_d(prods),
        "e supressão e null": regra_e(prods),
        "f tamanho": regra_f(),
        "g metadado irmão": regra_g(),
        "h LST fora": regra_h(prods),
        "i premissas P5 e P6": regra_i(prods),
        "j saúde: afogamento só por quinquênio, sem soma, sem 2026, Prisional fora": regra_j(prods),
        "k IN15 fora; ativo não livre só com exceção registrada": regra_k(prods),
    }
    print(f"arquivos varridos: {len(arqs)}; produtos: {len(prods)}")
    ok = True
    for nome, falhas in regras.items():
        print(f"[{'ok' if not falhas else 'FALHA'}] {nome}")
        for f in falhas:
            print(f"    - {f}")
        ok = ok and not falhas
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
