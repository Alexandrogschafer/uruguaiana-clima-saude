"""
Teste das regras de publicação do repositório público, sobre data/paineis/,
dashboards/, css/, js/, index.html (página inicial) e mapa.html (o mapa, até a rodada A2 em index.html).

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
       registrada em config/paineis_ativos.csv (as exceções em uso são listadas);
    l) cheia e saúde: ativos em conferência fora, sem pessoas, trava do evento em curso,
       Prisional fora da contagem, só mini-mapas como imagem;
    m) camada publicada das unidades (data/geoportal/unidades-saude-esf-ubs.geojson e o
       metadado irmão): nenhum campo de 2026 nem de soma ou total;
    n) nenhum arquivo do portal (mapa.html, index.html, js/, data/geoportal/) nem de data/paineis/ traz
       dado da cheia de outubro de 2026: nenhum valor de nível ou vazão (nem outro número) em
       linha datada de 01/10/2026 em diante, nenhum campo de pico, duração ou posição do
       evento, nenhuma data do evento como valor nas camadas do portal;
    o) os links dos painéis para o portal (mapa.html?camada=...) usam só chaves de camada
       que o portal aceita (lidas de js/url-estado.js e js/layers.js);
    p) id de série sem período (rodada 120): nenhum id de produto com ano no nome, fora
       das edições fixas de comum.EDICOES_FIXAS; "substitui" só com id antigo que não
       está publicado e sem repetição;
    q) nenhum JSON órfão em data/paineis/: todo produto da pasta é declarado em PRODUTOS
       por algum gerador e está no manifesto, e todo item do manifesto tem o arquivo;
    r) marcas (rodada A1): enquanto alguma marca de config/identidade.json estiver com
       marcas_liberadas false, nenhum arquivo de imagem de logo entra no repositório
       (png, svg, jpg, gif, webp versionados com nome de logo, ou com o termo no texto
       do svg ou nos metadados de texto do png/jpg);
    s) fontes e identidade (rodada A1): nenhum @import nem link a fonts.googleapis.com /
       fonts.gstatic.com em css, js ou html versionados; os woff2 de config/identidade.json
       existem com o sha256 registrado, junto do OFL.txt; os tokens de cor de
       config/identidade.json batem com os 3 blocos de css/paineis.css;
    t) CSV (rodada A2): cada data/paineis/csv/<id>.csv vem de um produto publicado (sha256 do JSON igual ao
       registrado no metadado irmão, mesmas linhas); sem coluna de coordenada fora dos ativos de ponto; sem
       contagem de 1 a 4 nas colunas suprimidas; linha datada de 01/10/2026 em diante sem número, fora a
       telemetria provisória;
    u) catálogo (rodada A2): catalogo_dados.json sem a coluna observacao, sem url, caminho de dado bruto nem
       script; toda linha ligada a produto ou camada publicados; downloads só para arquivos que existem;
    v) início (rodada A2): inicio_resumo.json sem nenhum produto do rio; index.html sem número na marcação
       (fora os <script>); textos de config/textos_inicio.json sem número fora de "Eixo 3".

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
import manifesto  # noqa: E402

RAIZ = comum.RAIZ
PASTA = comum.PASTA_PUBLICACAO
ALVOS = [RAIZ / "data" / "paineis", RAIZ / "dashboards", RAIZ / "css", RAIZ / "js", RAIZ / "index.html", RAIZ / "mapa.html",
         RAIZ / "config" / "textos_inicio.json", RAIZ / "config" / "catalogo_ligacao.csv"]
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
    # pirâmides: ativo próprio e livre (PD14); o PD03 (tabela pendente) não pode aparecer em produto
    if aut.get("PD14", {}).get("autorizacao") != "livre" or aut.get("PD03", {}).get("excecao_premissa", "").strip():
        falhas.append("PD14 deve ser livre e o PD03 não pode ter exceção")
    for p, d in prods:
        ativos = set(d.get("ativos", []))
        if "PD03" in ativos:
            falhas.append(f"{p.name}: usa o PD03, que depende de conferência")
        if p.name.startswith("demografia_") and p.name.endswith("_piramides.json") and ativos != {"PD14"}:
            falhas.append(f"{p.name}: as pirâmides devem usar o ativo PD14")
        if ativos & ATIVOS_FORA:
            falhas.append(f"{p.name}: ativo fora da publicação {sorted(ativos & ATIVOS_FORA)}")
        for a in sorted(ativos):
            r = aut.get(a, {})
            if r.get("autorizacao") != "livre":
                print(f"    · exceção em uso: {a} ({r.get('autorizacao')}) em {p.name}: {r.get('excecao_premissa', '').strip()[:90]}")
    return falhas


ATIVOS_ESPERA_CHEIA = {"IN09", "IN10", "IN11", "IN12", "IN15", "SA09", "SA11", "SA12", "SA13", "SA15", "SA16"}
TERMOS_PESSOAS = ("pessoa", "populac", "domicil", "endereco", "endereço", "morador", "expost", "habitante", "cnefe")
TERMOS_EVENTO = ("pico", "duracao", "duração", "posicao", "posição")


def _chaves(d: dict) -> set[str]:
    ch = {c["nome"].lower() for c in d.get("colunas", [])}
    ch |= {k.lower() for linha in d.get("dados", []) if isinstance(linha, dict) for k in linha}
    return ch | {k.lower() for k in d}


def regra_l(prods) -> list[str]:
    """Cheia e saúde: (a) ativos em conferência fora; (b) sem pessoas/endereços nos produtos de inundação;
    (c) com evento em curso, sem pico/duração/posição, sem valor de leitura, série cortada e sem o ano do evento;
    (d) Prisional fora da contagem; (e) imagens só mini-mapas, ≤ 300 kB, sem endereço entre as entradas."""
    falhas = []
    por = {p.name: d for p, d in prods}
    for p, d in prods:
        if set(d.get("ativos", [])) & ATIVOS_ESPERA_CHEIA:
            falhas.append(f"(a) {p.name}: ativo em conferência {sorted(set(d['ativos']) & ATIVOS_ESPERA_CHEIA)}")
        if p.name.startswith("inundacao_"):
            ruins = [k for k in _chaves(d) if any(s in k for s in TERMOS_PESSOAS)]
            if ruins:
                falhas.append(f"(b) {p.name}: campos de pessoas ou endereços {ruins}")
        if d.get("evento_em_curso"):
            ruins = [k for k in _chaves(d) if any(s in k for s in TERMOS_EVENTO)]
            if ruins:
                falhas.append(f"(c) {p.name}: campos de pico, duração ou posição {ruins}")
    ult = por.get("rio_ana-77150000_ultima-leitura.json")
    if ult and ult.get("evento_em_curso"):
        linha = ult["dados"][0]
        if any(linha.get(k) is not None for k in ("data_hora", "nivel_cm", "vazao_m3s", "idade_dias")):
            falhas.append("(c) última leitura com valor durante o evento em curso")
        dia = por.get("rio_ana-77150000_ultimos-365-dias_diario.json")
        mx = por.get("rio_ana-77150000_maximas-anuais.json")
        ano_ev = int(ult["periodo"]["fim"][:4])
        if dia and any(r["nivel_medio_cm"] > 800 for r in dia["dados"][-1:]):
            falhas.append("(c) série diária não termina antes do evento")
        if mx and any(r["ano"] >= ano_ev for r in mx["dados"]):
            falhas.append(f"(c) máximas anuais incluem {ano_ev}, ano do evento em curso")
    cotas, uni = por.get("inundacao_sgb_cotas.json"), por.get("inundacao_sgb_unidades-cotas.json")
    if cotas and uni:
        pris = [u for u in uni["dados"] if u["cnes"] == "4126947"]
        if not pris or not pris[0]["fora_das_comparacoes"]:
            falhas.append("(d) Equipe Prisional não marcada como fora das comparações")
        comp = [u for u in uni["dados"] if not u["fora_das_comparacoes"]]
        if len(comp) != 21:
            falhas.append(f"(d) {len(comp)} unidades comparáveis (esperado 21)")
        for r in cotas["dados"]:
            n = sum(1 for u in comp if u[f"dentro_{r['cota_cm']}"])
            if n != r["esf_ubs_dentro"]:
                falhas.append(f"(d) cota {r['cota_cm']}: {r['esf_ubs_dentro']} no produto, {n} pelas 21 ESF/UBS")
    for f in sorted((PASTA / "img").glob("*")) if (PASTA / "img").exists() else []:
        if not re.fullmatch(r"mini-mapa_cota-\d{3,4}\.png", f.name):
            falhas.append(f"(e) imagem inesperada: {f.name}")
        if f.stat().st_size > 300 * 1024:
            falhas.append(f"(e) {f.name}: {f.stat().st_size} bytes")
    if cotas:
        meta = json.loads((PASTA / "inundacao_sgb_cotas.json.json").read_text(encoding="utf-8"))
        ent = json.dumps(meta.get("entradas", meta), ensure_ascii=False).lower()
        if any(s in ent for s in ("cnefe", "endereco", "endereço")):
            falhas.append("(e) mini-mapas gerados com entrada de endereços")
    return falhas


GEO = RAIZ / "data" / "geoportal"
INICIO_EVENTO = "2026-10-01"
RE_DATA_ISO = re.compile(r"^(\d{4}-\d{2}-\d{2})")
# datas que só registram quando o arquivo foi gerado ou lido (não são dado do evento)
CHAVES_DATA_DE_REGISTRO = {"data_processamento", "gerado_em", "data_acesso", "data_consulta", "atualizado_em", "dados_ate"}
CAMPOS_DATA_LINHA = ("dia", "data", "data_hora", "inicio", "fim")
# rodada 118: a telemetria recente do rio é publicada todo dia, inclusive durante o evento, rotulada como
# provisória; é a única exceção à regra n, e só enquanto tiver "provisorio": true e o aviso de telemetria
TELEMETRIA_PROVISORIA = {"rio_ana-77150000_telemetria-recente_diario.json"}
AVISO_TELEMETRIA = "dados de telemetria, não consolidados"
TERMOS_RECORRENCIA = ("retorno", "recorr", "recorde", "histor")


def telemetria_provisoria_ok(nome: str, d: dict) -> list[str]:
    """Exceção da telemetria: provisória declarada, aviso presente, sem pico/duração/posição nem recorrência."""
    falhas = []
    if d.get("provisorio") is not True:
        falhas.append(f"{nome}: telemetria sem \"provisorio\": true")
    if AVISO_TELEMETRIA not in [a.get("texto") for a in d.get("avisos", [])]:
        falhas.append(f"{nome}: telemetria sem o aviso \"{AVISO_TELEMETRIA}\"")
    chaves = _chaves(d) | {k.lower() for k in (d.get("maxima_periodo") or {})}
    ruins = sorted(k for k in chaves if any(t in k for t in TERMOS_EVENTO + TERMOS_RECORRENCIA))
    if ruins:
        falhas.append(f"{nome}: campos de pico, duração, posição ou recorrência {ruins}")
    return falhas


def regra_m() -> list[str]:
    """Camada publicada das unidades: sem campo de 2026, sem soma nem total de população."""
    falhas = []
    camada = GEO / "unidades-saude-esf-ubs.geojson"
    if not camada.exists():
        return [f"{camada.name} ausente"]
    g = json.loads(camada.read_text(encoding="utf-8"))
    chaves = {k for f in g["features"] for k in f.get("properties", {})}
    meta = json.loads(camada.with_suffix(".json").read_text(encoding="utf-8"))
    chaves |= set(meta.get("colunas", []))
    ruins = sorted(k for k in chaves if "2026" in k or re.search(r"soma|total", k, re.I))
    if ruins:
        falhas.append(f"{camada.name}: campos de 2026, soma ou total {ruins}")
    if len(g["features"]) != 22:
        falhas.append(f"{camada.name}: {len(g['features'])} unidades (esperado 22)")
    return falhas


def _walk(obj, caminho=""):
    """Percorre um JSON e devolve (caminho, chave, valor) de cada folha."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _walk(v, f"{caminho}.{k}") if isinstance(v, (dict, list)) else [(f"{caminho}.{k}", k, v)]
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _walk(v, f"{caminho}[{i}]") if isinstance(v, (dict, list)) else [(f"{caminho}[{i}]", None, v)]


def regra_n(prods) -> list[str]:
    """Nada da cheia de outubro de 2026 no portal nem nos painéis."""
    falhas = []
    # painéis: linhas datadas de 01/10/2026 em diante não podem ter número; nenhum campo de pico/duração/posição
    for p, d in prods:
        if p.name in TELEMETRIA_PROVISORIA:
            falhas += telemetria_provisoria_ok(p.name, d)
            continue
        for i, linha in enumerate(d.get("dados", [])):
            if not isinstance(linha, dict):
                continue
            datas = [str(linha[c])[:10] for c in CAMPOS_DATA_LINHA if isinstance(linha.get(c), str) and RE_DATA_ISO.match(linha[c])]
            if any(x >= INICIO_EVENTO for x in datas):
                numeros = [k for k, v in linha.items() if isinstance(v, (int, float)) and not isinstance(v, bool)]
                if numeros:
                    falhas.append(f"{p.name}: linha {i} de {max(datas)} com valores {numeros}")
        if d.get("evento_em_curso") is not None or p.name.startswith("rio_"):
            ruins = sorted(k for k in _chaves(d) if any(t in k for t in TERMOS_EVENTO))
            if ruins:
                falhas.append(f"{p.name}: campos de pico, duração ou posição {ruins}")
    # portal: nenhuma data do evento como valor nas camadas (fora as datas de registro do arquivo)
    for f in sorted(GEO.rglob("*")):
        if re.search(r"2026-?10", f.name):
            falhas.append(f"data/geoportal: arquivo com data do evento no nome: {f.name}")
        if f.suffix not in (".json", ".geojson"):
            continue
        try:
            obj = json.loads(f.read_text(encoding="utf-8"))
        except (ValueError, UnicodeDecodeError):
            continue
        for caminho, chave, v in _walk(obj):
            if chave in CHAVES_DATA_DE_REGISTRO or not isinstance(v, str):
                continue
            m = RE_DATA_ISO.match(v)
            if m and m.group(1) >= INICIO_EVENTO:
                falhas.append(f"{f.relative_to(RAIZ)}: {caminho} = {v}")
                break
    # portal: textos do mapa sem números da cheia de outubro
    for f in [RAIZ / "mapa.html", RAIZ / "index.html", *sorted((RAIZ / "js").glob("*.js"))]:
        t = f.read_text(encoding="utf-8")
        if re.search(r"(cheia|evento|enchente)[^.\n]{0,40}outubro de 2026|outubro de 2026[^.\n]{0,40}\d+\s*(cm|m³/s)", t, re.I):
            falhas.append(f"{f.relative_to(RAIZ)}: texto sobre a cheia de outubro de 2026")
    return falhas


RE_ANO_NO_ID = re.compile(r"_(19|20)\d{2}")


def regra_p(prods) -> list[str]:
    """Ids estáveis: nenhum ano no id fora das edições fixas; "substitui" coerente."""
    falhas = []
    ids = {d.get("id") for _, d in prods}
    vistos = {}
    for p, d in prods:
        pid = d.get("id", "")
        if pid != p.stem:
            falhas.append(f"{p.name}: id {pid} diferente do nome do arquivo")
        if RE_ANO_NO_ID.search(pid) and pid not in comum.EDICOES_FIXAS:
            falhas.append(f"{p.name}: id com período no nome (use serie ou atual; edições fixas em comum.EDICOES_FIXAS)")
        sub = d.get("substitui")
        if sub:
            if sub in ids:
                falhas.append(f"{p.name}: substitui {sub}, que ainda está publicado")
            if sub in vistos:
                falhas.append(f"{p.name}: substitui {sub}, já substituído por {vistos[sub]}")
            vistos[sub] = pid
            if comum.IDS_SUBSTITUIDOS.get(pid) != sub:
                falhas.append(f"{p.name}: substitui {sub} diferente de comum.IDS_SUBSTITUIDOS")
    return falhas


def regra_q(prods) -> list[str]:
    """Nenhum órfão: pasta, geradores e manifesto com os mesmos ids."""
    falhas = []
    declarados = set(manifesto.ids_declarados())
    na_pasta = {p.stem for p, _ in prods}
    man = json.loads((PASTA / "manifesto.json").read_text(encoding="utf-8"))
    no_manifesto = {i["id"] for i in man["produtos"]}
    for x in sorted(na_pasta - declarados):
        falhas.append(f"{x}.json: órfão (nenhum gerador o declara); rode gerar_todos.py --limpar")
    for x in sorted(declarados - na_pasta):
        falhas.append(f"{x}: declarado por um gerador, mas sem arquivo em data/paineis/")
    for x in sorted(no_manifesto ^ (na_pasta & declarados)):
        falhas.append(f"{x}: manifesto e pasta divergem")
    for item in man["produtos"]:
        sub = item.get("substitui")
        d = next((d for p, d in prods if p.stem == item["id"]), {})
        if sub != d.get("substitui"):
            falhas.append(f"{item['id']}: substitui no manifesto ({sub}) diferente do produto ({d.get('substitui')})")
    for f in sorted(PASTA.glob("*.json.json")):
        if not f.with_name(f.name[:-5]).exists():
            falhas.append(f"{f.name}: metadado sem produto")
    return falhas


def chaves_camada_portal() -> set[str]:
    """Chaves que o portal aceita em ?camada= (as mesmas de js/url-estado.js; data-camada vem de layers.js)."""
    url = (RAIZ / "js" / "url-estado.js").read_text(encoding="utf-8")
    lay = (RAIZ / "js" / "layers.js").read_text(encoding="utf-8")
    chaves = set(re.findall(r"^\s+(\w+): (?:anoDensidade|porFiltro)\(", url, re.M))
    chaves |= set(re.findall(r'^\s+(\w+): "checkbox-', url, re.M))
    m = re.search(r'\[((?:"\w+",?\s*)+)\]\.forEach\(\(chave\)', url)
    if m:
        chaves |= set(re.findall(r'"(\w+)"', m.group(1)))
    chaves |= set(re.findall(r'\{ chave: "(\w+)", layer:', lay))
    return chaves


def regra_o() -> list[str]:
    """Links dos painéis ao portal só com chaves de camada que existem."""
    falhas = []
    validas = chaves_camada_portal()
    if len(validas) < 10:
        return [f"chaves do portal não encontradas em js/url-estado.js e js/layers.js ({sorted(validas)})"]
    n = 0
    for f in sorted((RAIZ / "dashboards").glob("*.html")):
        for lista in re.findall(r"mapa\.html\?[^\"'`\s]*?camada=([\w,]+)", f.read_text(encoding="utf-8")):
            n += 1
            ruins = [k for k in lista.split(",") if k not in validas]
            if ruins:
                falhas.append(f"dashboards/{f.name}: camada={lista} com chave inexistente {ruins}")
    if n == 0:
        falhas.append("nenhum link dos painéis ao portal com camada= encontrado")
    return falhas


# ------------------------------------------------------------------ identidade (rodada A1)
IDENTIDADE = RAIZ / "config" / "identidade.json"
EXT_IMAGEM = {".png", ".svg", ".jpg", ".jpeg", ".gif", ".webp"}
# pastas que o teste nunca lista nem abre (dados de campo e auditoria fora do escopo público)
FORA_DA_VARREDURA = [":!data/campo", ":!data/auditoria_cnes"]
# termos de logo: genéricos (valem enquanto qualquer marca estiver bloqueada) e por marca
TERMOS_LOGO_GERAIS = [r"logo", r"logotipo", r"bras[aã]o", r"emblema"]
TERMOS_LOGO_MARCA = {
    "pet_saude_clima": [r"pet[-_ ]?sa[uú]de", r"climapampa"],
    "unipampa": [r"unipampa"],
    "ministerio_saude_sus": [r"minist[eé]rio", r"\bsus\b"],
    "secretaria_municipal": [r"secretaria", r"\bsms\b", r"prefeitura"],
}


def arquivos_versionados(*padroes: str) -> list[Path]:
    """Arquivos no índice do git (versionados ou já adicionados), sem as pastas fora da varredura."""
    import subprocess
    saida = subprocess.run(["git", "-C", str(RAIZ), "ls-files", "-z", "--cached", "--", *padroes, *FORA_DA_VARREDURA],
                           capture_output=True, check=True).stdout.decode("utf-8")
    return [RAIZ / x for x in saida.split("\0") if x and (RAIZ / x).is_file()]


def _texto_metadados_imagem(f: Path) -> str:
    """Texto que uma imagem carrega além dos pixels: o svg inteiro; no png, os blocos tEXt/iTXt/zTXt;
    no jpg/gif/webp, os primeiros 64 kB (cabeçalho, EXIF, XMP). Não procura nos pixels comprimidos."""
    import struct
    import zlib
    b = f.read_bytes()
    suf = f.suffix.lower()
    if suf == ".svg":
        return b.decode("utf-8", errors="ignore")
    if suf == ".png" and b[:8] == b"\x89PNG\r\n\x1a\n":
        partes, i = [], 8
        while i + 8 <= len(b):
            n, tipo = struct.unpack(">I4s", b[i:i + 8])
            dado = b[i + 8:i + 8 + n]
            if tipo in (b"tEXt", b"iTXt"):
                partes.append(dado.decode("latin-1", errors="ignore"))
            elif tipo == b"zTXt":
                chave, _, resto = dado.partition(b"\0")
                try:
                    partes.append(chave.decode("latin-1") + " " + zlib.decompress(resto[1:]).decode("latin-1", errors="ignore"))
                except zlib.error:
                    partes.append(chave.decode("latin-1"))
            if tipo == b"IEND":
                break
            i += 12 + n
        return "\n".join(partes)
    return b[:65536].decode("latin-1", errors="ignore")


def regra_r() -> list[str]:
    """Nenhum arquivo de imagem de logo enquanto alguma marca estiver bloqueada."""
    if not IDENTIDADE.exists():
        return ["config/identidade.json ausente"]
    liberadas = json.loads(IDENTIDADE.read_text(encoding="utf-8")).get("marcas_liberadas", {})
    if set(liberadas) != set(TERMOS_LOGO_MARCA):
        return [f"marcas_liberadas com chaves inesperadas: {sorted(liberadas)}"]
    bloqueadas = [m for m, v in liberadas.items() if v is not True]
    if not bloqueadas:
        return []
    termos = TERMOS_LOGO_GERAIS + [t for m in bloqueadas for t in TERMOS_LOGO_MARCA[m]]
    rx = re.compile("|".join(termos), re.IGNORECASE)
    falhas = []
    for f in arquivos_versionados(*[f"*{e}" for e in EXT_IMAGEM], *[f"*{e.upper()}" for e in EXT_IMAGEM]):
        rel = f.relative_to(RAIZ).as_posix()
        if rx.search(f.name):
            falhas.append(f"{rel}: nome de logo (marcas bloqueadas: {', '.join(bloqueadas)})")
            continue
        m = rx.search(_texto_metadados_imagem(f))
        if m:
            falhas.append(f"{rel}: termo de logo no conteúdo (\"{m.group(0)}\")")
    return falhas


def _tokens_bloco(css: str, seletor_re: str) -> dict[str, str]:
    m = re.search(seletor_re + r"\s*\{([^{}]*)\}", css)
    return {k: v.strip().lower() for k, v in re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", m.group(1))} if m else {}


def regra_s() -> list[str]:
    """Nenhuma fonte de serviço externo; arquivos da fonte íntegros; tokens da identidade iguais aos do CSS."""
    import hashlib
    falhas = []
    rx_externo = re.compile(r"fonts\.(googleapis|gstatic)\.com", re.IGNORECASE)
    for f in arquivos_versionados("*.css", "*.js", "*.html", "*.htm", ":!node_modules"):
        if rx_externo.search(f.read_text(encoding="utf-8", errors="ignore")):
            falhas.append(f"{f.relative_to(RAIZ).as_posix()}: chamada a serviço externo de fontes")
    if not IDENTIDADE.exists():
        return falhas + ["config/identidade.json ausente"]
    ident = json.loads(IDENTIDADE.read_text(encoding="utf-8"))
    fonte = ident.get("fonte", {})
    for a in fonte.get("arquivos", []):
        p = RAIZ / a["arquivo"]
        if not p.is_file():
            falhas.append(f"{a['arquivo']}: arquivo da fonte ausente")
        elif hashlib.sha256(p.read_bytes()).hexdigest() != a["sha256"]:
            falhas.append(f"{a['arquivo']}: sha256 diferente do registrado em config/identidade.json")
    lic = RAIZ / fonte.get("arquivo_licenca", "")
    if not lic.is_file() or "Open Font License" not in lic.read_text(encoding="utf-8", errors="ignore"):
        falhas.append("licença OFL da fonte ausente")
    css = (RAIZ / "css" / "paineis.css").read_text(encoding="utf-8")
    blocos = {
        "claro (:root)": ("claro", _tokens_bloco(css, r"(?m)^:root")),
        "escuro (prefers-color-scheme)": ("escuro", _tokens_bloco(css, r':root:not\(\[data-theme="light"\]\)')),
        "escuro ([data-theme=\"dark\"])": ("escuro", _tokens_bloco(css, r':root\[data-theme="dark"\]')),
    }
    for nome, (modo, tokens) in blocos.items():
        if not tokens:
            falhas.append(f"css/paineis.css: bloco {nome} não encontrado")
            continue
        for k, v in ident["cores"][modo].items():
            obtido = tokens.get(k, "").split("/*")[0].strip()
            if obtido != v.lower():
                falhas.append(f"css/paineis.css bloco {nome}: {k} = {obtido or 'ausente'} (identidade: {v})")
    return falhas


# ------------------------------------------------------------------ início, catálogo e CSV (rodada A2)
def regra_t(prods) -> list[str]:
    import csv as _csv
    falhas = []
    por = {p.stem: (p, d) for p, d in prods}
    pasta = PASTA / "csv"
    for f in sorted(pasta.glob("*.csv")) if pasta.exists() else []:
        pid = f.stem
        if pid not in por:
            falhas.append(f"csv/{f.name}: sem produto publicado")
            continue
        p, d = por[pid]
        m = f.with_name(f.name + ".json")
        if not m.exists():
            falhas.append(f"csv/{f.name}: sem metadado irmão")
            continue
        meta = json.loads(m.read_text(encoding="utf-8"))
        if meta.get("sha256_fonte") != comum.sha256_arquivo(p):
            falhas.append(f"csv/{f.name}: derivado de outra versão do JSON (rode exportar_csv.py)")
        with f.open(encoding="utf-8", newline="") as fh:
            linhas = list(_csv.DictReader(fh))
        if len(linhas) != len(d.get("dados", [])):
            falhas.append(f"csv/{f.name}: {len(linhas)} linhas, o JSON tem {len(d.get('dados', []))}")
        cols = {c.lower() for c in (linhas[0].keys() if linhas else [])}
        if cols & CAMPOS_COORDENADA and not set(d.get("ativos", [])) <= ATIVOS_PONTO_PERMITIDOS:
            falhas.append(f"csv/{f.name}: coluna de coordenada {sorted(cols & CAMPOS_COORDENADA)}")
        for c in d.get("supressao", {}).get("campos", []):
            ruins = [r[c] for r in linhas if r.get(c) not in (None, "") and re.fullmatch(r"[1-4](\.0)?", r[c])]
            if ruins:
                falhas.append(f"csv/{f.name}: contagem de 1 a 4 em {c}")
        if f"{pid}.json" not in TELEMETRIA_PROVISORIA:
            for i, r in enumerate(linhas):
                datas = [str(r.get(c, ""))[:10] for c in CAMPOS_DATA_LINHA if RE_DATA_ISO.match(str(r.get(c) or ""))]
                if any(x >= INICIO_EVENTO for x in datas) and any(re.fullmatch(r"-?\d+(\.\d+)?", v or "") for k, v in r.items() if k not in CAMPOS_DATA_LINHA):
                    falhas.append(f"csv/{f.name}: linha {i} de {max(datas)} com número")
                    break
    return falhas


def regra_u() -> list[str]:
    falhas = []
    arq = PASTA / "catalogo_dados.json"
    if not arq.exists():
        return ["catalogo_dados.json ausente"]
    cat = json.loads(arq.read_text(encoding="utf-8"))
    nomes = {c["nome"] for c in cat["colunas"]}
    if "observacao" in nomes or any("observacao" in r for r in cat["dados"]):
        falhas.append("catálogo publica a coluna observacao")
    texto = json.dumps(cat["dados"], ensure_ascii=False)
    for padrao, o_que in [(r"https?://", "url"), (r"data/(raw|processed)/", "caminho de dado bruto"), (r"scripts/", "script")]:
        if re.search(padrao, texto):
            falhas.append(f"catálogo publica {o_que}")
    publicados = {i["id"] for i in json.loads((PASTA / "manifesto.json").read_text(encoding="utf-8"))["produtos"]}
    for r in cat["dados"]:
        prods = (r.get("produtos") or "").split()
        if not prods and not r.get("camadas"):
            falhas.append(f"catálogo: {r['tema']} sem produto nem camada")
        for pid in prods:
            if pid not in publicados:
                falhas.append(f"catálogo: {r['tema']} → {pid} não publicado")
        for arqc in (r.get("downloads_camadas") or "").split():
            if not (RAIZ / arqc).exists():
                falhas.append(f"catálogo: download inexistente {arqc}")
    for pid, s in cat.get("sobre_produtos", {}).items():
        if s.get("csv") and not (RAIZ / "data" / "paineis" / s["csv"].split("data/paineis/")[-1]).exists():
            falhas.append(f"catálogo: CSV inexistente para {pid}")
    return falhas


def regra_v() -> list[str]:
    falhas = []
    arq = PASTA / "inicio_resumo.json"
    if not arq.exists():
        return ["inicio_resumo.json ausente"]
    d = json.loads(arq.read_text(encoding="utf-8"))
    rio = sorted({r["produto"] for r in d["dados"] if r["produto"].startswith("rio_")} | {x for x in d.get("produtos_usados", []) if x.startswith("rio_")})
    if rio:
        falhas.append(f"inicio_resumo.json com produto do rio {rio}")
    html = (RAIZ / "index.html").read_text(encoding="utf-8")
    marcacao = re.sub(r"<script\b.*?</script>|<!--.*?-->", "", html, flags=re.S)
    numeros = re.findall(r"\d+", re.sub(r"<[^>]+>", " ", marcacao))
    if numeros:
        falhas.append(f"index.html: número na marcação {numeros[:5]}")
    tx = json.loads((RAIZ / "config" / "textos_inicio.json").read_text(encoding="utf-8"))
    tx.pop("descricao", None)  # comentário do arquivo, não aparece na página
    textos = json.dumps(tx, ensure_ascii=False)
    sobra = re.findall(r"\d+", re.sub(r"https?://[^\"]+|Eixo 3", "", textos))
    if sobra:
        falhas.append(f"config/textos_inicio.json: número fora de \"Eixo 3\" {sobra[:5]}")
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
        "k IN15 fora; pirâmides no PD14 e PD03 fora; ativo não livre só com exceção registrada": regra_k(prods),
        "l cheia e saúde: conferência fora, sem pessoas, trava do evento, Prisional fora, imagens": regra_l(prods),
        "m camada das unidades no portal sem campos de 2026 nem soma": regra_m(),
        "n nenhum dado da cheia de outubro de 2026 no portal nem nos painéis": regra_n(prods),
        "o links dos painéis ao portal com chaves de camada que existem": regra_o(),
        "p id de série sem período, fora das edições listadas": regra_p(prods),
        "q nenhum JSON órfão em data/paineis/": regra_q(prods),
        "r nenhuma imagem de logo enquanto as marcas não forem liberadas": regra_r(),
        "s fontes sem serviço externo; fonte íntegra; tokens da identidade iguais aos do CSS": regra_s(),
        "t CSV derivados dos JSON publicados, sem coordenada, supressão mantida, sem número do evento": regra_t(prods),
        "u catálogo sem observacao, url, caminho bruto nem script; só linhas publicadas": regra_u(),
        "v início sem produto do rio e sem número na marcação": regra_v(),
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
