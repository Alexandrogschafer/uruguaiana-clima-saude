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
      entradas não mudaram, a menos de forcar=True;
    - id estável (rodada 120): o id do produto não carrega o período. Convenção
      {tema}_{fonte}_{serie|atual}_{resolucao}: "serie" para série temporal que
      cresce a cada coleta, "atual" para retrato que é substituído. O período e
      o dados_ate ficam dentro do JSON. Edições fixas (o ano é a edição da fonte,
      não o período: censo 2022, BATER 2018, CPRM 2021, setorização 2014,
      áreas urbanizadas 2019) e os produtos do rio mantêm o id. Produto que
      mudou de id leva o campo "substitui" com o id antigo (IDS_SUBSTITUIDOS),
      e as telas resolvem o id antigo por ele;
    - bruto mais recente (rodada 120): quando mais de um arquivo casa com o
      padrão de entrada, vale o de data_acesso mais recente no .json irmão
      (bruto_mais_recente); o bruto antigo nunca é apagado.

CRS não se aplica: os produtos são tabulares.
"""

from __future__ import annotations

import copy
import csv
import hashlib
import json
import math
from decimal import ROUND_HALF_UP, Decimal
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


def br(v, casas: int = 0, minimo: int | None = None) -> str:
    """Número no formato pt-BR (1.234,5) para os textos de nota gerados por script."""
    minimo = casas if minimo is None else minimo
    # arredonda como o Intl/toLocaleString do navegador: meio para cima sobre o valor binário exato
    q = Decimal(v).quantize(Decimal(1).scaleb(-casas), rounding=ROUND_HALF_UP)
    s = f"{q:,.{casas}f}"
    inteiro, _, frac = s.partition(".")
    frac = frac.rstrip("0")
    if len(frac) < minimo:
        frac = frac + "0" * (minimo - len(frac))
    inteiro = inteiro.replace(",", ".")
    return inteiro + ("," + frac if frac else "")


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


# ordem de força do selo: um produto não pode ter selo mais forte que o de qualquer ativo de origem
FORCA_SELO = {"pendente": 0, "em_confirmacao": 1, "conferido": 2}


def conferir_selo(selo: str, ativos: list[str], autorizacoes: dict | None = None) -> None:
    """Lança ErroProduto se o selo do produto for mais forte que o selo registrado de algum ativo."""
    autorizacoes = autorizacoes if autorizacoes is not None else ler_autorizacoes()
    for a in ativos:
        registrado = (autorizacoes.get(a) or {}).get("selo", "").strip()
        if registrado and FORCA_SELO[selo] > FORCA_SELO[registrado]:
            raise ErroProduto(f"selo '{selo}' mais forte que o selo '{registrado}' registrado para {a}")


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


# ---------------------------------------------------------------- ids e entradas

# id estável → id antigo (com período no nome), publicado no campo "substitui" do produto
IDS_SUBSTITUIDOS = {
    "clima_inmet-a809_serie_anual": "clima_inmet-a809_2007-2025_anual",
    "clima_tabela_serie_anual": "clima_tabela-anual_2007-2025",
    "clima_precipitacao-merge_serie_anual": "clima_precipitacao-merge_2010-2025_anual",
    "clima_precipitacao-merge_serie_mensal": "clima_precipitacao-merge_2010-2025_mensal",
    "queimadas_inpe_serie_anual": "queimadas_inpe_2010-2025_anual",
    "saude_sim_serie_anual": "saude_sim_2010-2024_anual",
    "saude_sim-afogamento-enchente_serie_quinquenal": "saude_sim-afogamento-enchente_2010-2024_quinquenal",
    "saude_sih_serie_anual": "saude_sih_2010-2025_anual",
    "saude_sinan_serie_anual": "saude_sinan_2010-2026_anual",
    "saude_unidades-esf-ubs_atual": "saude_unidades-esf-ubs_2026",
    "populacao_ibge_serie_anual": "populacao_ibge_1970-2025_anual",
    "demografia_ibge_serie_indicadores": "demografia_ibge_1970-2022_indicadores",
    "demografia_ibge_censos_piramides": "demografia_ibge_2000-2010-2022_piramides",
    "pib_ibge_serie_anual": "pib_ibge_2002-2023_anual",
    "agropecuaria_ibge_serie_anual": "agropecuaria_ibge_1974-2024_anual",
    "emprego_caged_serie_mensal": "emprego_caged_2020-2026_mensal",
    "emprego_rais_atual_secao": "emprego_rais_2025_secao",
    "cadunico_mds_serie_anual": "cadunico_mds_2012-2026_anual",
    "bolsa-familia_mds_atual_mes": "bolsa-familia_mds_2026-07_mes",
    "despesas_siconfi_serie_anual": "despesas_siconfi_2015-2026_anual",
    "idhm_atlas_censos": "idhm_atlas_1991-2010_censos",
    "desastres_s2id_serie_anual": "desastres_s2id_2014-2025_anual",
    "energia_aneel_serie_anual": "energia_aneel_2000-2026_anual",
    "uso-solo_mapbiomas_serie_classes": "uso-solo_mapbiomas_1985-2024_classes",
    "uso-solo_mapbiomas_atual_todas-classes": "uso-solo_mapbiomas_2024_todas-classes",
    "app-hidrica_ana-bho-mapbiomas_atual_curso": "app-hidrica_ana-bho-mapbiomas_2024_curso",
}
# edições fixas: o ano no id é a edição da fonte, não o período da série
EDICOES_FIXAS = {
    "censo_ibge_2022_situacao-setor", "areas-de-risco_ibge_2018_bater", "risco-geologico_cprm_2021_classe",
    "setorizacao-risco_sgb_2014_setor", "areas-urbanizadas_ibge_2019_classe",
}


def _metadado_irmao(arq: Path) -> Path:
    """Metadado do bruto: x.csv → x.json; x.json → x.json.json; x.csv.json (já é metadado) não se aplica."""
    irmao = arq.with_suffix(".json")
    return irmao if irmao != arq else arq.with_name(arq.name + ".json")


def data_da_coleta(arq: Path) -> str:
    """data_acesso do .json irmão; sem ela, data_processamento (brutos antigos não gravavam data_acesso)."""
    meta = json.loads(_metadado_irmao(arq).read_text(encoding="utf-8"))
    quando = meta.get("data_acesso") or meta.get("data_processamento")
    if not quando:
        raise ValueError(f"{arq.name}: metadado irmão sem data_acesso nem data_processamento")
    return str(quando)


def bruto_mais_recente(pasta: Path, padrao: str) -> Path:
    """Entre os arquivos que casam com o padrão, o de coleta mais recente (um só: ele mesmo).

    Cada coleta nova grava arquivo próprio e o bruto anterior não é apagado; com mais de um candidato vale o
    de data_acesso mais recente no .json irmão (ISO 8601, ordenável como texto).
    """
    achados = sorted(p for p in Path(pasta).glob(padrao) if not p.name.endswith(".json.json") or padrao.endswith(".json.json"))
    if not achados:
        raise ValueError(f"nenhum arquivo {padrao} em {relativo(Path(pasta))}")
    if len(achados) == 1:
        return achados[0]
    return max(achados, key=data_da_coleta)


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
    if selo in SELOS:
        conferir_selo(selo, ativos, autorizacoes)
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
        **({"substitui": IDS_SUBSTITUIDOS[id]} if id in IDS_SUBSTITUIDOS else {}),
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
