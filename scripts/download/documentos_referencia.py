"""
Guarda cópia local dos documentos que fundamentam um estudo (relatórios,
teses, artigos, leis, páginas oficiais): baixa, confere e registra cada um com
endereço, data, tamanho e sha256. Um documento da internet pode sair do ar.

Entrada:  docs/documentos/lista_de_fontes.csv (id, referência, endereço, nome
          do arquivo, modo, filtro, termos de conferência, sha256 das cópias
          que o responsável já tem).
Saídas:   data/raw/documentos/ (documentos e páginas; FORA do git) e
          data/raw/vetor/ (anexos espaciais: shp, kml... ou zip que os contenha; FORA do git),
          cada arquivo com .json irmão;
          docs/documentos/manifesto_documentos.csv (versionável).

Regras:
  - resposta que é PDF (cabeçalho ou primeiros bytes "%PDF"): grava;
  - resposta que é página: grava <arquivo>_pagina.html (prova do que estava
    publicado) e procura o arquivo — meta "citation_pdf_url"; arquivos do item
    pela API do repositório (DSpace: /server/api/); links /bitstream/ dos
    repositórios antigos; link de download do artigo nas revistas; links para
    PDF e anexos na página;
  - modo "todos": baixa todos os arquivos achados (com sufixos); "arquivo": um;
    "partes": todos os PDF do registro, na ordem dele, com sufixo _parte-N (texto
    dividido em vários arquivos); "local": copia de uma pasta de cópias locais,
    achando o arquivo pelo sha256;
  - coluna url_arquivo da lista (opcional): endereço direto do arquivo, pedido sem
    passar pela página do registro (vários endereços, separados por espaço, são
    partes: _parte-N). Se o endereço devolver uma página em vez do arquivo, a
    página é salva e a situação é a de situacao_fixa (ou "sem arquivo aberto");
  - confere cada PDF: abre, tem ao menos 1 página e os termos da referência
    aparecem nas 2 primeiras páginas (titulo_confere: sim / não / não verificado);
  - idempotente: arquivo igual (mesmo sha256) não é regravado; arquivo diferente
    NÃO é sobrescrito — o novo fica com o sufixo _baixado-AAAA-MM-DD;
  - nada de contornar bloqueio: 401/403/429, captcha, login ou página paga viram
    "recusado pelo site" e o download segue para o próximo;
  - educação com os servidores: User-Agent identificado, pausa de 2 s entre
    pedidos ao mesmo servidor, tempo limite de 60 s, uma nova tentativa só;
  - limites: arquivo acima de 500 MB não é baixado; acima de 3 GB na rodada, para.

Uso:
  python scripts/download/documentos_referencia.py
  python scripts/download/documentos_referencia.py --copias-locais PASTA   # modo "local"
  python scripts/download/documentos_referencia.py --so D013 D021 --forcar
  python scripts/download/documentos_referencia.py --so D052 --uma-tentativa        # sem nova tentativa
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import re
import shutil
import subprocess
import time
import unicodedata
from datetime import date, datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

RAIZ = Path(__file__).resolve().parents[2]
DOCS = RAIZ / "data" / "raw" / "documentos"
VETOR = RAIZ / "data" / "raw" / "vetor"
LISTA = RAIZ / "docs" / "documentos" / "lista_de_fontes.csv"
MANIFESTO = RAIZ / "docs" / "documentos" / "manifesto_documentos.csv"
USER_AGENT = "uruguaiana-clima-saude (pesquisa academica)"
PAUSA_S, TEMPO_LIMITE_S = 2.0, 60
MAX_ARQUIVO, MAX_RODADA = 500 * 1024**2, 3 * 1024**3
COLUNAS = ["id", "referencia", "tipo", "url_origem", "url_arquivo", "arquivo", "tamanho_bytes", "sha256", "paginas", "data_download",
           "situacao", "titulo_confere", "observacao"]
EXT_ESPACIAL = {".zip", ".shp", ".kml", ".kmz", ".gpkg", ".geojson", ".rar", ".7z"}
EXT_LINK = (".pdf", ".zip", ".rar", ".7z", ".xlsx", ".xls", ".docx", ".doc", ".kmz", ".kml", ".shp", ".gpkg", ".csv", ".ods", ".odt", ".dwg")  # o que se segue numa página
EXT_ARQUIVO = EXT_LINK + (".jpg", ".jpeg", ".png", ".tif")
MAX_ANEXOS = 60  # por documento, no modo "todos"
TIPOS = {"application/pdf": ".pdf", "application/zip": ".zip", "application/x-zip-compressed": ".zip", "text/html": ".html",
         "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx", "application/vnd.ms-excel": ".xls",
         "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx", "application/msword": ".doc",
         "application/xml": ".xml", "text/xml": ".xml", "application/json": ".json", "application/vnd.google-earth.kmz": ".kmz",
         "image/jpeg": ".jpg", "image/png": ".png", "application/x-rar-compressed": ".rar", "application/vnd.rar": ".rar"}
RECUSA = re.compile(r"captcha|cf-challenge|access denied|acesso negado|just a moment|attention required|fa[çc]a login para|verificando (seu navegador|conex)", re.I)


class Recusado(Exception):
    pass


class Rede:
    """Sessão com pausa por servidor, tempo limite e uma nova tentativa."""

    def __init__(self, tentativas: int = 2):
        self.tentativas = tentativas
        self.s = requests.Session()
        self.s.headers["User-Agent"] = USER_AGENT
        self.ultimo: dict[str, float] = {}
        self.baixado = 0

    def get(self, url: str, **kw) -> requests.Response:
        host = urlparse(url).netloc
        for tentativa in range(1, self.tentativas + 1):
            espera = PAUSA_S - (time.time() - self.ultimo.get(host, 0))
            if espera > 0:
                time.sleep(espera)
            try:
                r = self.s.get(url, timeout=TEMPO_LIMITE_S, stream=True, allow_redirects=True, **kw)
                self.ultimo[host] = time.time()
                if r.status_code in (401, 403, 407, 429, 451):
                    raise Recusado(f"HTTP {r.status_code}")
                if r.status_code >= 500 and tentativa < self.tentativas:
                    r.close()
                    continue
                return r
            except requests.RequestException as erro:
                self.ultimo[host] = time.time()
                if tentativa == self.tentativas:
                    raise
                logger.warning("falha em %s (%s) — uma nova tentativa", url, erro)
        return r

    def get_seguindo_meta(self, url: str) -> tuple[requests.Response, bytes]:
        """GET + corpo; se a resposta for só um redirecionamento em HTML (meta refresh), segue uma vez."""
        r = self.get(url)
        if r.status_code != 200:
            return r, b""
        c = self.corpo(r)
        m = re.search(rb"<meta[^>]+http-equiv=[\"']?refresh[^>]+url=([^\"'>\s]+)", c[:2000], re.I) if len(c) < 2000 else None
        if m:
            r = self.get(urljoin(r.url, m.group(1).decode("utf-8", "replace")))
            c = self.corpo(r) if r.status_code == 200 else b""
        return r, c

    def corpo(self, r: requests.Response) -> bytes:
        """Lê o corpo respeitando os limites de tamanho."""
        anunciado = int(r.headers.get("Content-Length") or 0)
        if anunciado > MAX_ARQUIVO:
            r.close()
            raise OverflowError(f"arquivo de {anunciado} bytes (acima de 500 MB)")
        partes, n = [], 0
        for bloco in r.iter_content(1 << 20):
            partes.append(bloco)
            n += len(bloco)
            if n > MAX_ARQUIVO:
                r.close()
                raise OverflowError("arquivo acima de 500 MB")
        self.baixado += n
        if self.baixado > MAX_RODADA:
            raise SystemExit("total da rodada acima de 3 GB — parado (ver o manifesto para o que falta)")
        return b"".join(partes)


# ---------------------------------------------------------------- utilidades
def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sem_acento(s: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKD", s) if not unicodedata.combining(ch)).lower()


def slug(s: str, n: int = 60) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", sem_acento(unquote(s))).strip("-")
    return s[:n].strip("-") or "arquivo"


def eh_pdf(b: bytes) -> bool:
    return b[:1024].lstrip()[:4] == b"%PDF"


def extensao(url: str, r: requests.Response, corpo: bytes) -> str:
    if eh_pdf(corpo):
        return ".pdf"
    cd = r.headers.get("Content-Disposition", "")
    m = re.search(r"filename\*?=(?:UTF-8'')?\"?([^\";]+)", cd)
    for nome in ([unquote(m.group(1))] if m else []) + [urlparse(r.url).path, urlparse(url).path]:
        e = Path(nome).suffix.lower()
        if e in EXT_ARQUIVO or e in (".html", ".htm", ".xml", ".json"):
            return ".html" if e == ".htm" else e
    if corpo[:4] == b"PK\x03\x04":
        return ".zip"
    return TIPOS.get(r.headers.get("Content-Type", "").split(";")[0].strip().lower(), ".bin")


def nome_original(url: str, r: requests.Response) -> str:
    m = re.search(r"filename\*?=(?:UTF-8'')?\"?([^\";]+)", r.headers.get("Content-Disposition", ""))
    return unquote(m.group(1)) if m else unquote(Path(urlparse(r.url).path).name)


class Links(HTMLParser):
    """Coleta links (<a>, <iframe>, <embed>) com o texto, e as metas citation_pdf_url."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links: list[list[str]] = []
        self.pdf_meta: list[str] = []
        self._a = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "meta" and (a.get("name") or "").lower() == "citation_pdf_url" and a.get("content"):
            self.pdf_meta.append(a["content"])
        elif tag == "a" and a.get("href"):
            alvo = a["href"]
            m = re.search(r"['\"]([^'\"]+\.(?:html?|pdf|zip|kmz))['\"]", a.get("onclick") or "")
            if alvo.strip() in ("#", "") and m:  # link que abre o alvo por script: o alvo está no onclick
                alvo = m.group(1)
            self._a = [alvo, (a.get("title") or "") + " "]
            self.links.append(self._a)
        elif tag in ("iframe", "embed", "object") and (a.get("src") or a.get("data")):
            self.links.append([a.get("src") or a.get("data"), ""])

    def handle_data(self, data):
        if self._a is not None:
            self._a[1] += data

    def handle_endtag(self, tag):
        if tag == "a":
            self._a = None


def api_dspace(rede: Rede, url: str) -> list[tuple[str, str]]:
    """Arquivos do item pela API do próprio repositório (DSpace 7+). Lista de (endereço, nome)."""
    u = urlparse(url)
    m_item = re.search(r"/items/([0-9a-f-]{36})", u.path)
    m_hdl = re.search(r"/handle/(.+?)/?$", u.path)
    if not (m_item or m_hdl):
        return []
    for raiz in (f"{u.scheme}://{u.netloc}/server/api", f"{u.scheme}://{u.netloc}/bduserver/api"):
        try:
            if m_item:
                r = rede.get(f"{raiz}/core/items/{m_item.group(1)}", headers={"Accept": "application/json"})
            else:
                r = rede.get(f"{raiz}/pid/find", params={"id": m_hdl.group(1)}, headers={"Accept": "application/json"})
            if r.status_code != 200 or "json" not in r.headers.get("Content-Type", ""):
                r.close()
                continue
            item = json.loads(rede.corpo(r))
            r = rede.get(item["_links"]["bundles"]["href"], params={"embed": "bitstreams", "size": 50}, headers={"Accept": "application/json"})
            achados = []
            for b in json.loads(rede.corpo(r)).get("_embedded", {}).get("bundles", []):
                if b.get("name") != "ORIGINAL":
                    continue
                for bs in b.get("_embedded", {}).get("bitstreams", {}).get("_embedded", {}).get("bitstreams", []):
                    achados.append((bs["_links"]["content"]["href"], bs.get("name", "")))
            if achados:
                return achados
        except Recusado:
            raise
        except (requests.RequestException, ValueError, KeyError) as erro:
            logger.info("API do repositório sem resposta em %s (%s)", raiz, erro)
    return []


def achar_arquivos(rede: Rede, url: str, html: str, nivel: int = 0, filtro=None) -> list[tuple[str, str]]:
    """Endereços de arquivos anunciados por uma página, em ordem de confiança. Lista de (endereço, texto ou nome)."""
    achados = api_dspace(rede, url)
    if achados:  # o repositório listou os arquivos do item: não é preciso garimpar a página
        return achados
    p = Links()
    try:
        p.feed(html)
    except Exception:  # página malformada: fica com o que já leu
        pass
    achados += [(urljoin(url, x), "citation_pdf_url") for x in p.pdf_meta]
    links = [(urljoin(url, h.strip()), " ".join(t.split())) for h, t in p.links if h and not h.strip().lower().startswith(("javascript:", "mailto:", "#"))]
    # endereços de arquivo que aparecem só no código da página (ou num metadado em XML/JSON)
    links += [(x.replace("&amp;", "&"), "") for x in re.findall(r"https?://[^\s\"'<>\\]+", html)]
    for h, t in links:  # repositórios antigos
        if "/bitstream/" in h and not re.search(r"\.(jpg|jpeg|png|gif|txt)(\?|$)", h, re.I) and "license" not in h.lower():
            achados.append((h, t))
    for h, t in links:  # revistas (OJS): página do PDF -> endereço de download
        m = re.search(r"(.*/article)/(?:view|download)/(\d+)/(\d+)(?:/\d+)?/?$", h)
        if m:
            achados.append((f"{m.group(1)}/download/{m.group(2)}/{m.group(3)}", t or "pdf do artigo"))
    for h, t in links:  # links diretos para arquivos e anexos
        caminho = urlparse(h).path.lower()
        if caminho.endswith(EXT_LINK) or "/anexos/" in caminho or re.search(r"/download/|/@@download/|arquivo\.php|/bitstreams/", h, re.I):
            achados.append((h, t))
    if filtro is not None:  # com filtro, vale qualquer link que case com ele (inclusive páginas, que são salvas)
        achados += [(h, t) for h, t in links if filtro.search(unquote(h)) or filtro.search(t)]
    if nivel == 0:  # bibliotecas de teses: seguem para o registro do repositório de origem
        for h, t in links:
            if re.search(r"/handle/\d+/\d+|/items/[0-9a-f-]{36}", h) and urlparse(h).netloc != urlparse(url).netloc:
                try:
                    r = rede.get(h)
                    corpo = rede.corpo(r)
                    if eh_pdf(corpo):
                        achados.append((h, t))
                    else:
                        achados += achar_arquivos(rede, r.url, corpo.decode(r.encoding or "utf-8", "replace"), nivel=1)
                except (Recusado, requests.RequestException, OverflowError) as erro:
                    logger.info("registro de origem não aberto: %s (%s)", h, erro)
                break
    vistos, unicos = set(), []
    for h, t in achados:
        if h not in vistos:
            vistos.add(h)
            unicos.append((h, t))
    return unicos


def eh_espacial(ext: str, conteudo: bytes) -> bool:
    """Anexo espacial vai para data/raw/vetor/: shp, kml, gpkg..., ou zip que contenha um deles (zip só de figuras ou PDFs é documento)."""
    if ext != ".zip":
        return ext in EXT_ESPACIAL
    try:
        import io
        import zipfile

        nomes = [n.lower() for n in zipfile.ZipFile(io.BytesIO(conteudo)).namelist()]
    except Exception:  # zip que não abre: fica com os documentos
        return False
    return any(n.endswith((".shp", ".kml", ".kmz", ".gpkg", ".geojson", ".tif", ".gdb", ".dwg")) or ".gdb/" in n for n in nomes)


def conferir_pdf(caminho: Path, termos: str) -> tuple[int | None, str]:
    """(páginas, titulo_confere). Sem texto nas 2 primeiras páginas (imagem): 'não verificado'."""
    try:
        info = subprocess.run(["pdfinfo", str(caminho)], capture_output=True, text=True, timeout=60).stdout
        pag = int(re.search(r"Pages:\s+(\d+)", info).group(1))
        txt = subprocess.run(["pdftotext", "-l", "2", str(caminho), "-"], capture_output=True, text=True, timeout=60).stdout
    except (AttributeError, OSError, subprocess.SubprocessError):
        return None, "não verificado"
    lista = [sem_acento(t.strip()) for t in termos.split(";") if t.strip()]
    if len(txt.strip()) < 30 or not lista:
        return pag, "não verificado"
    texto = " ".join(sem_acento(txt).split())
    return pag, "sim" if any(t in texto for t in lista) else "não"


def gravar(conteudo: bytes, destino: Path, forcar_nome: bool = False) -> tuple[Path, str]:
    """Grava sem sobrescrever conteúdo diferente (F5). Devolve (caminho, 'novo' | 'igual' | 'diferente')."""
    destino.parent.mkdir(parents=True, exist_ok=True)
    if destino.exists():
        if sha256(destino.read_bytes()) == sha256(conteudo):
            return destino, "igual"
        destino = destino.with_name(f"{destino.stem}_baixado-{date.today().isoformat()}{destino.suffix}")
        destino.write_bytes(conteudo)
        return destino, "diferente"
    destino.write_bytes(conteudo)
    return destino, "novo"


def irma(caminho: Path) -> Path:
    """.json irmão; quando o próprio arquivo é .json (resposta de uma API), o irmão é .meta.json."""
    return caminho.with_suffix(".meta.json" if caminho.suffix == ".json" else ".json")


def meta_irma(caminho: Path, **campos) -> None:
    irma(caminho).write_text(json.dumps({"arquivo": caminho.name, **campos}, ensure_ascii=False, indent=2), encoding="utf-8")


def linha(e: dict, **kw) -> dict:
    base = {c: "" for c in COLUNAS}
    base.update({"id": e["id"], "referencia": e["referencia"], "tipo": e["tipo"], "url_origem": e["url_origem"], "observacao": e.get("observacao", "")})
    base.update(kw)
    return base


def registrar(e: dict, conteudo: bytes, destino: Path, url_arquivo: str, tipo_conteudo: str, situacao: str, nota: str = "") -> dict:
    """Grava o arquivo (F5), confere e devolve a linha do manifesto."""
    caminho, estado = gravar(conteudo, destino)
    pag, confere = (conferir_pdf(caminho, e.get("conferir", "")) if caminho.suffix == ".pdf" else (None, "não se aplica"))
    hoje = datetime.now(timezone.utc).isoformat(timespec="seconds")
    obs = [e.get("observacao", ""), nota]
    if estado == "diferente":
        obs.append(f"já havia arquivo com esse nome e outro sha256: o novo foi gravado como {caminho.name}")
    if estado == "igual" and irma(caminho).exists():  # não regrava: mantém data e situação do primeiro registro
        antigo = json.loads(irma(caminho).read_text(encoding="utf-8"))
        hoje, situacao = antigo.get("data_download", antigo.get("data_copia", hoje)), antigo.get("situacao", "já tínhamos")
    elif estado == "igual":
        situacao = "já tínhamos"
    if estado != "igual" or not irma(caminho).exists():  # arquivo que já estava registrado: o .json dele não é regravado
        meta_irma(caminho, id=e["id"], referencia=e["referencia"], url_origem=e["url_origem"], url_arquivo=url_arquivo, data_download=hoje,
                  tamanho_bytes=len(conteudo), sha256=sha256(conteudo), paginas=pag, tipo_de_conteudo_do_servidor=tipo_conteudo, situacao=situacao,
                  titulo_confere=confere, pasta=caminho.parent.relative_to(RAIZ).as_posix(), observacao="; ".join(x for x in obs if x))
    logger.info("%s: %s — %d bytes (%s)", e["id"], caminho.name, len(conteudo), estado)
    return linha(e, url_arquivo=url_arquivo, arquivo=caminho.relative_to(RAIZ / "data" / "raw").as_posix(), tamanho_bytes=len(conteudo), sha256=sha256(conteudo),
                 paginas=pag if pag is not None else "", data_download=hoje[:10], situacao=situacao, titulo_confere=confere, observacao="; ".join(x for x in obs if x))


# ---------------------------------------------------------------- um documento
def baixar_diretos(e: dict, rede: Rede, enderecos: list[str], anteriores: list[dict]) -> list[dict]:
    """Endereço direto do arquivo (coluna url_arquivo): pede sem passar pela página do registro. Vários endereços são partes, na ordem."""
    # páginas salvas em rodadas anteriores continuam no manifesto (são a prova do que estava publicado)
    linhas = [r for r in anteriores if "_pagina." in r["arquivo"] and (RAIZ / "data" / "raw" / r["arquivo"]).exists()]
    sem = e.get("situacao_fixa") or "sem arquivo aberto"
    for n, h in enumerate(enderecos, 1):
        sufixo = f"_parte-{n}" if len(enderecos) > 1 else ""
        try:
            r = rede.get(h)
            if r.status_code != 200:
                linhas.append(linha(e, url_arquivo=h, situacao="não localizado" if r.status_code == 404 else sem, titulo_confere="não se aplica",
                                    observacao=f"HTTP {r.status_code} no endereço direto"))
                r.close()
                continue
            corpo, tipo = rede.corpo(r), r.headers.get("Content-Type", "")
        except Recusado as erro:
            linhas.append(linha(e, url_arquivo=h, situacao="recusado pelo site", titulo_confere="não se aplica", observacao=f"{erro} no endereço direto"))
            continue
        except OverflowError as erro:
            linhas.append(linha(e, url_arquivo=h, situacao="sem arquivo aberto", titulo_confere="não se aplica", observacao=f"não baixado: {erro}"))
            continue
        except requests.RequestException as erro:
            linhas.append(linha(e, url_arquivo=h, situacao="não localizado", titulo_confere="não se aplica",
                                observacao=f"sem resposta do servidor no endereço direto em {date.today().isoformat()}: {type(erro).__name__}; baixar à mão"))
            continue
        ext = extensao(h, r, corpo)
        if ext in (".html", ".bin", ".xml", ".json") and not eh_pdf(corpo):  # veio uma página, não o arquivo
            if len(corpo) < 20000 and RECUSA.search(corpo.decode(r.encoding or "utf-8", "replace")):
                linhas.append(linha(e, url_arquivo=h, situacao="recusado pelo site", titulo_confere="não se aplica",
                                    observacao="página de bloqueio (captcha, verificação de navegador ou acesso negado) no endereço direto"))
                continue
            p = registrar(e, corpo, DOCS / f"{e['arquivo']}{sufixo}_endereco-direto_pagina.html", h, tipo, sem,
                          nota="o endereço direto devolveu uma página, não o arquivo; página salva como prova do que estava publicado")
            p["situacao"] = sem
            linhas.append(p)
            continue
        en = e if n == 1 else dict(e, conferir="")  # o título só aparece na primeira parte
        nota = f"nome no servidor: {nome_original(h, r)}" + (f"; parte {n} de {len(enderecos)}" if sufixo else "")
        linhas.append(registrar(en, corpo, (VETOR if eh_espacial(ext, corpo) else DOCS) / (e["arquivo"] + sufixo + ext), h, tipo, "baixado", nota=nota))
    return linhas


def processar(e: dict, rede: Rede, locais: dict[str, Path], sha_de: dict[str, str], anteriores: list[dict] | None = None) -> list[dict]:
    modo = e.get("modo") or "arquivo"
    if modo == "nenhum" or not (e["url_origem"] or e.get("url_arquivo") or modo == "local"):
        return [linha(e, situacao=e.get("situacao_fixa") or "não localizado", titulo_confere="não se aplica")]
    if modo == "local":
        alvo, ja = e["sha256_local"], None
        for f in sorted(DOCS.glob(e["arquivo"] + ".*")):
            if f.suffix != ".json" and sha256(f.read_bytes()) == alvo:
                ja = f
        origem = ja or locais.get(alvo)
        if origem is None:
            return [linha(e, situacao="não localizado", titulo_confere="não se aplica",
                          observacao="sha256 não encontrado na pasta de cópias locais do responsável nem em data/raw/documentos/")]
        conteudo = origem.read_bytes()
        r = registrar(e, conteudo, DOCS / (e["arquivo"] + origem.suffix.lower()), "cópia local do responsável", "cópia local", "já tínhamos")
        r["situacao"] = "já tínhamos"
        return [r]

    e = dict(e, arquivo=e["arquivo"].replace("{data}", date.today().isoformat()))  # nome com a data do download, quando pedido
    if (e.get("url_arquivo") or "").split():
        return baixar_diretos(e, rede, e["url_arquivo"].split(), anteriores or [])
    linhas, filtro = [], re.compile(e["filtro"]) if e.get("filtro") else None
    try:
        r = rede.get(e["url_origem"])
        if r.status_code == 404:
            return [linha(e, situacao="não localizado", titulo_confere="não se aplica", observacao="HTTP 404 no endereço de origem")]
        if r.status_code != 200:
            return [linha(e, situacao="sem arquivo aberto", titulo_confere="não se aplica", observacao=f"HTTP {r.status_code} no endereço de origem")]
        corpo, tipo = rede.corpo(r), r.headers.get("Content-Type", "")
    except Recusado as erro:
        return [linha(e, situacao="recusado pelo site", titulo_confere="não se aplica", observacao=str(erro))]
    except OverflowError as erro:
        return [linha(e, situacao="sem arquivo aberto", titulo_confere="não se aplica", observacao=f"não baixado: {erro}")]
    except requests.RequestException as erro:
        return [linha(e, situacao="não localizado", titulo_confere="não se aplica", observacao=f"sem resposta do servidor: {type(erro).__name__}")]

    ext = extensao(e["url_origem"], r, corpo)
    if ext not in (".html", ".xml", ".json", ".bin") or eh_pdf(corpo):  # a resposta já é o arquivo
        return [registrar(e, corpo, DOCS / (e["arquivo"] + ext), r.url, tipo, "baixado")]

    texto = corpo.decode(r.encoding or "utf-8", "replace")
    if len(corpo) < 20000 and RECUSA.search(texto):
        return [linha(e, situacao="recusado pelo site", titulo_confere="não se aplica", observacao="página de bloqueio (captcha, verificação de navegador ou acesso negado)")]
    pagina = registrar(e, corpo, DOCS / f"{e['arquivo']}_pagina{'.html' if ext == '.bin' else ext}", r.url, tipo, "baixado", nota="página salva como prova do que estava publicado")
    achados = achar_arquivos(rede, r.url, texto, filtro=filtro if modo == "todos" else None)
    if filtro:
        achados = [(h, t) for h, t in achados if filtro.search(unquote(h)) or filtro.search(t)]
    if modo == "partes":  # ordem dos links do registro (a meta citation_pdf_url pode apontar para uma parte do meio); um endereço por nome de arquivo
        nomes, unicos = set(), []
        for h, t in [x for x in achados if x[1] != "citation_pdf_url"] + [x for x in achados if x[1] == "citation_pdf_url"]:
            nome_h = unquote(Path(urlparse(h).path).name).lower()
            if nome_h.endswith(".pdf") and nome_h not in nomes:
                nomes.add(nome_h)
                unicos.append((h, t))
        achados = unicos
    achados = achados[:6] if modo == "arquivo" else achados[:MAX_ANEXOS]  # "arquivo": o primeiro PDF que abrir
    n_ok, n_partes, vistos = 0, 0, set()
    for i, (h, t) in enumerate(achados, 1):
        try:
            ra, ca = rede.get_seguindo_meta(h)
            if ra.status_code != 200:
                ra.close()
                continue
        except Recusado as erro:
            linhas.append(linha(e, url_arquivo=h, situacao="recusado pelo site", titulo_confere="não se aplica", observacao=str(erro)))
            continue
        except OverflowError as erro:
            linhas.append(linha(e, url_arquivo=h, situacao="sem arquivo aberto", titulo_confere="não se aplica", observacao=f"não baixado: {erro}"))
            continue
        except requests.RequestException as erro:
            logger.info("%s: anexo sem resposta: %s (%s)", e["id"], h, erro)
            continue
        ea = extensao(h, ra, ca)
        if ea in (".html", ".bin", ".xml", ".json") and not eh_pdf(ca):
            if not (filtro and modo == "todos" and ea == ".html" and urlparse(h)._replace(fragment="").geturl() != urlparse(r.url)._replace(fragment="").geturl()):
                continue  # outra página, não um arquivo (só é salva quando o filtro a escolheu)
        if modo in ("arquivo", "partes") and ea != ".pdf":
            continue
        igual_a = next((i_ for i_, s_ in sha_de.items() if s_ == sha256(ca) and i_ != e["id"]), None)
        if igual_a:  # o mesmo arquivo de outro documento já guardado: registra e não grava de novo
            if sha256(ca) not in vistos:
                linhas.append(linha(e, url_arquivo=h, sha256=sha256(ca), tamanho_bytes=len(ca), situacao="já tínhamos", titulo_confere="não se aplica",
                                    data_download=date.today().isoformat(), observacao=f"arquivo idêntico (sha256) a {igual_a}; não foi gravado de novo"))
            vistos.add(sha256(ca))
            n_ok += 1
            continue
        if sha256(ca) in vistos:  # o mesmo arquivo por outro endereço
            continue
        vistos.add(sha256(ca))
        pasta = VETOR if eh_espacial(ea, ca) else DOCS
        nome = e["arquivo"] if modo == "arquivo" else f"{e['arquivo']}_{i:02d}-{slug(Path(nome_original(h, ra)).stem or t)}"
        nota = f"nome no servidor: {nome_original(h, ra)}" + (f"; DIFERENTE de {e['comparar_com']} (sha256): os dois ficam guardados" if e.get("comparar_com") and ea == ".pdf" else "")
        en = e
        if modo == "partes":  # partes numeradas na ordem do registro; o título só aparece na primeira
            n_partes += 1
            nome, nota = f"{e['arquivo']}_parte-{n_partes}", f"{nota}; parte {n_partes} de {len(achados)}"
            en = e if n_partes == 1 else dict(e, conferir="")
        linhas.append(registrar(en, ca, pasta / (nome + ea), h, ra.headers.get("Content-Type", ""), "baixado", nota=nota))
        n_ok += 1
        if modo == "arquivo":
            break
    if n_ok == 0 and not linhas:
        pagina["situacao"] = "sem arquivo aberto" if modo == "arquivo" or not filtro else "não localizado"
        pagina["observacao"] = (pagina["observacao"] + "; página salva, mas nenhum arquivo foi achado nela").strip("; ")
    return [pagina] + linhas


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--lista", type=Path, default=LISTA)
    p.add_argument("--manifesto", type=Path, default=MANIFESTO)
    p.add_argument("--copias-locais", type=Path, help="pasta com as cópias que o responsável já tem (localizadas pelo sha256)")
    p.add_argument("--so", nargs="*", help="só estes ids")
    p.add_argument("--forcar", action="store_true", help="pede de novo ao servidor os que já estão resolvidos no manifesto")
    p.add_argument("--uma-tentativa", action="store_true", help="não repete o pedido que falhar (padrão: uma nova tentativa)")
    a = p.parse_args()

    with open(a.lista, encoding="utf-8", newline="") as f:
        entradas = list(csv.DictReader(f))
    anteriores: dict[str, list[dict]] = {}
    if a.manifesto.exists():
        with open(a.manifesto, encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                anteriores.setdefault(r["id"], []).append(r)
    locais = {}
    if a.copias_locais:
        locais = {sha256(f.read_bytes()): f for f in sorted(a.copias_locais.iterdir()) if f.is_file()}
        logger.info("cópias locais: %d arquivos", len(locais))
    sha_de = {e["id"]: e["sha256_local"] for e in entradas if e.get("sha256_local")}
    rede, saida = Rede(tentativas=1 if a.uma_tentativa else 2), []
    for e in entradas:
        resolvido = e["id"] in anteriores and all(r["situacao"] in ("baixado", "já tínhamos", "pago") or not e["url_origem"] for r in anteriores[e["id"]])
        if (a.so and e["id"] not in a.so) or (resolvido and not a.forcar and not (a.so and e["id"] in a.so)):
            saida += anteriores.get(e["id"], [linha(e, situacao="não localizado", observacao="ainda não processado")])
            continue
        try:
            saida += processar(e, rede, locais, sha_de, anteriores.get(e["id"], []))
        except SystemExit:
            saida.append(linha(e, situacao="sem arquivo aberto", observacao="rodada parada no limite de 3 GB"))
            raise
        finally:
            a.manifesto.parent.mkdir(parents=True, exist_ok=True)
            feitos = {r["id"] for r in saida}
            resto = [r for e2 in entradas if e2["id"] not in feitos for r in anteriores.get(e2["id"], [])]
            with open(a.manifesto, "w", encoding="utf-8", newline="") as f:  # grava a cada documento: nada se perde se a rodada parar
                w = csv.DictWriter(f, fieldnames=COLUNAS)
                w.writeheader()
                w.writerows(saida + resto)
    logger.info("manifesto: %s — %d linhas; baixado nesta execução: %.1f MB", a.manifesto.relative_to(RAIZ), len(saida), rede.baixado / 1024**2)


if __name__ == "__main__":
    main()
