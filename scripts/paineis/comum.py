"""
Funções comuns dos geradores de dados dos painéis (data/paineis/).

Cada produto publicado é um JSON no esquema "climapampa-painel/1" com um
metadado irmão "<id>.json.json" (fonte, transformação, entradas com sha256 e
script). Regras aplicadas aqui, e só aqui:

    - autorização: recusa gravar produto cujo ativo não seja "livre" em
      config/paineis_ativos.csv nem tenha exceção por premissa registrada;
    - supressão de contagens pequenas (limiar 5): primária e secundária; o
      valor verdadeiro nunca vai para o JSON público; zero suprimido ou não
      conforme parâmetro; valor ausente (lacuna da fonte) fica null sem
      entrada em "suprimidos";
    - idempotência: não regrava se o conteúdo (sem a data de geração) e as
      entradas não mudaram, a menos de forcar=True.

CRS não se aplica: os produtos são tabulares.
"""

from __future__ import annotations

import copy
import csv
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
PASTA_PUBLICACAO = RAIZ / "data" / "paineis"
ARQ_ATIVOS = RAIZ / "config" / "paineis_ativos.csv"
ESQUEMA = "climapampa-painel/1"
LIMIAR_SUPRESSAO = 5
SELOS = {"conferido", "pendente", "em_confirmacao"}
TIPOS_AVISO = {"lacuna", "parcial", "preliminar", "em_confirmacao", "metodo", "evento_em_curso"}
CAMPOS_OBRIGATORIOS = [
    "esquema", "id", "codigo_ibge", "ativos", "titulo", "fonte_curta", "fonte",
    "unidade_espacial", "unidade_medida", "periodo", "dados_ate", "autorizacao",
    "selo", "avisos", "supressao", "gerado_em", "script", "colunas", "dados",
]
CAMPOS_OBRIGATORIOS_METADADO = [
    "produto", "esquema", "fonte", "transformacao_aplicada", "entradas", "script",
    "codigo_ibge", "ativos", "autorizacao", "selo", "data_processamento",
]


class ErroAutorizacao(ValueError):
    """Ativo sem autorização para publicação."""


class ErroProduto(ValueError):
    """Produto fora do esquema."""


# ---------------------------------------------------------------- utilidades

def agora_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_arquivo(caminho: Path) -> str:
    h = hashlib.sha256()
    with open(caminho, "rb") as f:
        for bloco in iter(lambda: f.read(1 << 20), b""):
            h.update(bloco)
    return h.hexdigest()


def sha256_texto(texto: str) -> str:
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def relativo(caminho: Path) -> str:
    """Caminho relativo à raiz do repositório (nunca grava caminho local)."""
    return Path(caminho).resolve().relative_to(RAIZ).as_posix()


def limpar_valor(v):
    """NaN e infinito viram null; tipos numpy viram tipos Python."""
    if v is None:
        return None
    if hasattr(v, "item"):  # numpy
        v = v.item()
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    return v


def limpar_linhas(dados: list[dict]) -> list[dict]:
    return [{k: limpar_valor(v) for k, v in linha.items()} for linha in dados]


# ---------------------------------------------------------------- autorização

def ler_autorizacoes(arquivo: Path | None = None) -> dict[str, dict]:
    with open(arquivo or ARQ_ATIVOS, encoding="utf-8", newline="") as f:
        return {r["id"]: r for r in csv.DictReader(f)}


def conferir_autorizacao(ativos: list[str], autorizacoes: dict | None = None) -> str:
    """Devolve "livre" ou "excecao: ..."; lança ErroAutorizacao se algum ativo não puder sair."""
    if not ativos:
        raise ErroAutorizacao("produto sem ativo de origem")
    autorizacoes = autorizacoes if autorizacoes is not None else ler_autorizacoes()
    excecoes = []
    for a in ativos:
        r = autorizacoes.get(a)
        if r is None:
            raise ErroAutorizacao(f"ativo {a} não está em config/paineis_ativos.csv")
        if r["autorizacao"] == "livre":
            continue
        if r.get("excecao_premissa", "").strip():
            excecoes.append(f"{a} ({r['excecao_premissa'].strip()})")
            continue
        raise ErroAutorizacao(f"ativo {a} com autorização '{r['autorizacao']}' e sem exceção registrada")
    return "livre" if not excecoes else "exceção por premissa: " + "; ".join(excecoes)


# ---------------------------------------------------------------- supressão

def suprimir(dados: list[dict], campos: list[str], limiar: int = LIMIAR_SUPRESSAO,
             suprimir_zero: bool = False, grupos: list[list[int]] | None = None,
             vinculos: dict[str, list[str]] | None = None):
    """
    Suprime contagens pequenas. Devolve (dados_suprimidos, suprimidos, n_primarios, n_secundarios).

    - primária: 1 <= valor < limiar (e 0, se suprimir_zero);
    - vinculos: {campo: [campos derivados]} — se o campo é suprimido, os derivados
      da mesma linha também são (ex.: população estimada derivada de endereços);
    - grupos: listas de índices de linhas cujo total é publicado; se só uma linha
      do grupo foi suprimida num campo, suprime também a menor parte restante,
      para que nenhuma parte possa ser recuperada por subtração;
    - valor None (ausente) não é tocado nem listado.
    """
    vinculos = vinculos or {}
    saida = copy.deepcopy(dados)
    marcados: dict[tuple[int, str], str] = {}

    def pequeno(v) -> bool:
        if v is None:
            return False
        return (0 < v < limiar) or (v == 0 and suprimir_zero)

    for i, linha in enumerate(dados):
        for c in campos:
            if pequeno(linha.get(c)):
                marcados[(i, c)] = "primaria"
    for (i, c), _ in list(marcados.items()):
        for d in vinculos.get(c, []):
            if dados[i].get(d) is not None:
                marcados.setdefault((i, d), "primaria")
    for g in grupos or []:
        for c in campos + [d for c2 in campos for d in vinculos.get(c2, [])]:
            sup = [i for i in g if (i, c) in marcados]
            if len(sup) != 1:
                continue
            restantes = [i for i in g if (i, c) not in marcados and dados[i].get(c) is not None]
            if restantes:
                alvo = min(restantes, key=lambda i: dados[i][c])
                marcados[(alvo, c)] = "secundaria"
                for d in vinculos.get(c, []):
                    if dados[alvo].get(d) is not None:
                        marcados.setdefault((alvo, d), "secundaria")
    lista = []
    for (i, c), motivo in sorted(marcados.items()):
        saida[i][c] = None
        lista.append({"linha": i, "campo": c, "motivo": motivo})
    n_prim = sum(1 for s in lista if s["motivo"] == "primaria")
    return saida, lista, n_prim, len(lista) - n_prim


# ---------------------------------------------------------------- produto

def montar_produto(*, id: str, codigo_ibge: str, ativos: list[str], titulo: str, fonte_curta: str,
                   fonte: str, unidade_espacial: str, unidade_medida: str, periodo: dict,
                   dados_ate: str, selo: str, avisos: list[dict], colunas: list[dict],
                   dados: list[dict], script: str, campos_contagem: list[str] | None = None,
                   suprimir_zero: bool = False, grupos: list[list[int]] | None = None,
                   vinculos: dict | None = None, extras: dict | None = None,
                   autorizacoes: dict | None = None) -> dict:
    """Monta o produto já suprimido e conferido contra o esquema."""
    autorizacao = conferir_autorizacao(ativos, autorizacoes)
    dados = limpar_linhas(dados)
    campos_contagem = campos_contagem or []
    suprimidos = []
    n_prim = n_sec = 0
    if campos_contagem:
        dados, suprimidos, n_prim, n_sec = suprimir(dados, campos_contagem, suprimir_zero=suprimir_zero,
                                                    grupos=grupos, vinculos=vinculos)
    for col in colunas:
        if col["nome"] in campos_contagem:
            col["contagem"] = True
    prod = {
        "esquema": ESQUEMA,
        "id": id,
        "codigo_ibge": codigo_ibge,
        "ativos": ativos,
        "titulo": titulo,
        "fonte_curta": fonte_curta,
        "fonte": fonte,
        "unidade_espacial": unidade_espacial,
        "unidade_medida": unidade_medida,
        "periodo": periodo,
        "dados_ate": dados_ate,
        "autorizacao": autorizacao,
        "selo": selo,
        "avisos": avisos,
        "supressao": {"limiar": LIMIAR_SUPRESSAO, "campos": campos_contagem, "suprimir_zero": suprimir_zero,
                      "n_suprimidos": n_prim, "n_secundarios": n_sec},
        "suprimidos": suprimidos,
        "gerado_em": agora_iso(),
        "script": script,
        "colunas": colunas,
    }
    prod.update(extras or {})
    prod["dados"] = dados
    validar_produto(prod)
    return prod


def validar_produto(prod: dict) -> None:
    faltam = [c for c in CAMPOS_OBRIGATORIOS if c not in prod]
    if faltam:
        raise ErroProduto(f"{prod.get('id')}: faltam campos {faltam}")
    if prod["esquema"] != ESQUEMA:
        raise ErroProduto(f"{prod['id']}: esquema {prod['esquema']}")
    if prod["selo"] not in SELOS:
        raise ErroProduto(f"{prod['id']}: selo {prod['selo']}")
    for a in prod["avisos"]:
        if a.get("tipo") not in TIPOS_AVISO or not a.get("texto"):
            raise ErroProduto(f"{prod['id']}: aviso inválido {a}")
    nomes = {c["nome"] for c in prod["colunas"]}
    for linha in prod["dados"]:
        extra = set(linha) - nomes
        if extra:
            raise ErroProduto(f"{prod['id']}: colunas não declaradas {sorted(extra)}")
    # allow_nan=False acusa NaN que tenha escapado da limpeza
    json.dumps(prod, allow_nan=False)


def _sem_data(d: dict, chaves: tuple[str, ...]) -> dict:
    return {k: v for k, v in d.items() if k not in chaves}


def caminhos(id: str, pasta: Path = PASTA_PUBLICACAO) -> tuple[Path, Path]:
    p = pasta / f"{id}.json"
    return p, p.with_name(p.name + ".json")


def entradas_com_sha(entradas: list[Path]) -> list[dict]:
    return [{"arquivo": relativo(e), "sha256": sha256_arquivo(e), "bytes": Path(e).stat().st_size} for e in entradas]


def produto_em_dia(id: str, entradas: list[Path], pasta: Path = PASTA_PUBLICACAO) -> bool:
    """True se produto e metadado existem e as entradas têm o mesmo sha256 registrado."""
    p, m = caminhos(id, pasta)
    if not (p.exists() and m.exists()):
        return False
    try:
        meta = json.loads(m.read_text(encoding="utf-8"))
        registradas = {e["arquivo"]: e["sha256"] for e in meta["entradas"]}
        atuais = {relativo(e): sha256_arquivo(e) for e in entradas}
    except (OSError, KeyError, ValueError):
        return False
    return registradas == atuais


def gravar_produto(prod: dict, *, transformacao: str, entradas: list[Path], fonte_metadado: str | None = None,
                   pasta: Path = PASTA_PUBLICACAO, forcar: bool = False, extras_metadado: dict | None = None) -> bool:
    """Grava produto e metadado irmão. Devolve True se gravou, False se já estava igual."""
    validar_produto(prod)
    conferir_autorizacao(prod["ativos"])
    p, m = caminhos(prod["id"], pasta)
    meta = {
        "produto": p.name,
        "esquema": ESQUEMA,
        "fonte": fonte_metadado or prod["fonte"],
        "transformacao_aplicada": transformacao,
        "entradas": entradas_com_sha(entradas),
        "script": prod["script"],
        "codigo_ibge": prod["codigo_ibge"],
        "ativos": prod["ativos"],
        "autorizacao": prod["autorizacao"],
        "selo": prod["selo"],
        "crs": "não se aplica (produto tabular)",
        "data_processamento": prod["gerado_em"],
    }
    meta.update(extras_metadado or {})
    faltam = [c for c in CAMPOS_OBRIGATORIOS_METADADO if c not in meta]
    if faltam:
        raise ErroProduto(f"{prod['id']}: metadado sem {faltam}")
    if not forcar and p.exists() and m.exists():
        try:
            antigo = json.loads(p.read_text(encoding="utf-8"))
            antigo_meta = json.loads(m.read_text(encoding="utf-8"))
        except ValueError:
            antigo = antigo_meta = None
        if (antigo is not None
                and _sem_data(antigo, ("gerado_em",)) == _sem_data(prod, ("gerado_em",))
                and _sem_data(antigo_meta, ("data_processamento",)) == _sem_data(meta, ("data_processamento",))):
            return False
    pasta.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(prod, ensure_ascii=False, indent=1, allow_nan=False) + "\n", encoding="utf-8")
    m.write_text(json.dumps(meta, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return True
