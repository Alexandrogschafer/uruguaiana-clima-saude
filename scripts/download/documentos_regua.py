"""
Documentos públicos para a conferência das séries bruta e consistida da régua de
Uruguaiana em 2015: informes diários de alerta hidrológico do INA (Argentina) e
relatórios do repositório institucional do SGB.

O que baixa (sem conta, sem chave; nada é contornado):
  - INA: um informe por dia de cada período pedido, no endereço
    https://www.ina.gob.ar/archivos/alerta/hidroAA_MM_DD.pdf (um pedido por dia;
    dia sem informe fica registrado como "não existe");
  - SGB: os PDF de registros do repositório institucional (rigeo.sgb.gov.br),
    pedidos pelo identificador do registro (--rigeo NOME=UUID), pela interface de
    programação do próprio repositório.

Cada arquivo vai para data/raw/documentos/regua_2015/ (fora do git), com .json
irmão: endereço de origem, data do download, tamanho, sha256, tipo e páginas.
Idempotente: arquivo já presente, com o sha256 do seu .json, não é pedido de
novo. Pausa de --pausa segundos entre pedidos ao mesmo servidor e teto de
--max-pedidos no total. Servidor que recusa (401, 403, 429) é abandonado.

A lista do que foi feito fica em
data/raw/documentos/regua_2015/documentos-regua_lista.csv (+ .json).

Uso:
  python scripts/download/documentos_regua.py
  python scripts/download/documentos_regua.py --periodos J1=2015-07-01/2015-08-15 --rigeo estudo=UUID
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import logging
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd
import requests

RAIZ = Path(__file__).resolve().parents[2]
logger = logging.getLogger(__name__)

SCRIPT = "scripts/download/documentos_regua.py"
MOTIVO = "conferência das séries bruta e consistida da régua de Uruguaiana em 2015"
DESTINO = RAIZ / "data" / "raw" / "documentos" / "regua_2015"
HEADERS = {"User-Agent": "Mozilla/5.0 (compativel; script-pesquisa-ClimaPampa/1.0)"}
URL_INA = "https://www.ina.gob.ar/archivos/alerta/hidro{d:%y_%m_%d}.pdf"
API_RIGEO = "https://rigeo.sgb.gov.br/server/api/core"
# períodos padrão: as duas cheias de 2015 em exame e um período de controle em que as duas séries concordam
PERIODOS_PADRAO = {"J1": "2015-07-01/2015-08-15", "J2": "2015-11-15/2016-01-31", "J0": "2017-05-15/2017-07-05"}
RECUSA = (401, 403, 429)


class Rede:
    """Pedidos com pausa por servidor, teto total e abandono do servidor que recusa."""

    def __init__(self, pausa: float, teto: int):
        self.pausa, self.teto = pausa, teto
        self.ultimo: dict[str, float] = {}
        self.pedidos: dict[str, int] = {}
        self.abandonados: dict[str, str] = {}

    def pedir(self, url: str, **kw) -> requests.Response | None:
        servidor = urlparse(url).netloc
        if servidor in self.abandonados:
            return None
        if sum(self.pedidos.values()) >= self.teto:
            raise RuntimeError(f"teto de {self.teto} pedidos atingido")
        espera = self.pausa - (time.time() - self.ultimo.get(servidor, 0))
        if espera > 0:
            time.sleep(espera)
        self.pedidos[servidor] = self.pedidos.get(servidor, 0) + 1
        try:
            r = requests.get(url, headers=HEADERS, timeout=120, **kw)
        except requests.RequestException as e:
            logger.warning("sem resposta de %s: %s", servidor, type(e).__name__)
            return None
        finally:
            self.ultimo[servidor] = time.time()
        if r.status_code in RECUSA:
            self.abandonados[servidor] = f"HTTP {r.status_code}"
            logger.warning("%s recusou o pedido (HTTP %d): servidor abandonado", servidor, r.status_code)
            return None
        return r


def sha256(dados: bytes) -> str:
    return hashlib.sha256(dados).hexdigest()


def paginas(dados: bytes) -> int | None:
    try:
        from pypdf import PdfReader
        return len(PdfReader(io.BytesIO(dados)).pages)
    except Exception:  # sem leitor de PDF ou arquivo que ele não abre: o número de páginas fica vazio
        return None


def ja_existe(arq: Path) -> dict | None:
    meta = arq.with_suffix(".json")
    if arq.exists() and meta.exists():
        m = json.loads(meta.read_text(encoding="utf-8"))
        if m.get("sha256") == sha256(arq.read_bytes()):
            return m
    return None


def guardar(rede: Rede, url: str, arq: Path, fonte: str, referencia: str, **extra) -> dict:
    """Baixa um PDF e grava com o .json irmão; devolve a linha da lista."""
    linha = {"arquivo": arq.name, "fonte": fonte, "referencia": referencia, "endereco_de_origem": url, **extra}
    m = ja_existe(arq)
    if m:
        return {**linha, "situacao": "já existia", "tamanho_bytes": m["tamanho_bytes"], "sha256": m["sha256"], "paginas": m.get("paginas"), "data_do_download": m["data_do_download"]}
    r = rede.pedir(url)
    if r is None:
        return {**linha, "situacao": "sem resposta ou servidor abandonado"}
    if r.status_code == 404:
        return {**linha, "situacao": "não existe (404)"}
    if r.status_code != 200 or not r.content.startswith(b"%PDF"):
        return {**linha, "situacao": f"resposta sem PDF (HTTP {r.status_code}, {r.headers.get('content-type', 'tipo não informado')})"}
    arq.parent.mkdir(parents=True, exist_ok=True)
    arq.write_bytes(r.content)
    m = {"arquivo": arq.name, "fonte": fonte, "referencia": referencia, "endereco_de_origem": url, "data_do_download": datetime.now(timezone.utc).isoformat(timespec="seconds"),
         "tamanho_bytes": len(r.content), "sha256": sha256(r.content), "tipo": r.headers.get("content-type", "application/pdf"), "paginas": paginas(r.content),
         "script": SCRIPT, "motivo": MOTIVO, **extra}
    arq.with_suffix(".json").write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("baixado: %s (%d bytes)", arq.name, len(r.content))
    return {**linha, "situacao": "baixado", "tamanho_bytes": m["tamanho_bytes"], "sha256": m["sha256"], "paginas": m["paginas"], "data_do_download": m["data_do_download"]}


def informes_ina(rede: Rede, periodos: dict) -> list[dict]:
    linhas = []
    for nome, periodo in periodos.items():
        ini, fim = (date.fromisoformat(x) for x in periodo.split("/"))
        d = ini
        while d <= fim:
            arq = DESTINO / f"informe-alerta-hidrologico_ina_{d:%Y-%m-%d}.pdf"
            linhas.append(guardar(rede, URL_INA.format(d=d), arq, "INA — Instituto Nacional del Agua (Argentina), Sistema de Información y Alerta Hidrológico de la Cuenca del Plata",
                                  f"INA. Informe diário de alerta hidrológico da bacia do Prata, {d:%d/%m/%Y}", periodo=nome, data_do_documento=d.isoformat()))
            d += timedelta(days=1)
    return linhas


def registros_rigeo(rede: Rede, registros: dict) -> list[dict]:
    """PDF do pacote ORIGINAL de cada registro, na ordem do repositório."""
    linhas = []
    for nome, uuid in registros.items():
        r = rede.pedir(f"{API_RIGEO}/items/{uuid}", params={"embed": "bundles/bitstreams"})
        if r is None or r.status_code != 200:
            linhas.append({"arquivo": "", "fonte": "SGB — repositório institucional", "referencia": nome, "endereco_de_origem": f"{API_RIGEO}/items/{uuid}",
                           "situacao": "registro não lido" if r is None else f"registro não lido (HTTP {r.status_code})"})
            continue
        item = r.json()
        titulo = (item.get("metadata", {}).get("dc.title") or [{}])[0].get("value", nome)
        ano = ((item.get("metadata", {}).get("dc.date.issued") or [{}])[0].get("value", "") or "")[:4]
        pdfs = [b for pac in item.get("_embedded", {}).get("bundles", {}).get("_embedded", {}).get("bundles", []) if pac.get("name") == "ORIGINAL"
                for b in pac.get("_embedded", {}).get("bitstreams", {}).get("_embedded", {}).get("bitstreams", []) if b.get("name", "").lower().endswith(".pdf")]
        if not pdfs:
            linhas.append({"arquivo": "", "fonte": "SGB — repositório institucional", "referencia": f"SGB. {titulo} ({ano})", "endereco_de_origem": f"https://rigeo.sgb.gov.br/handle/{item.get('handle', '')}",
                           "situacao": "registro sem PDF aberto"})
        for n, b in enumerate(pdfs, 1):
            arq = DESTINO / (f"{nome}_sgb_{ano}" + (f"_parte-{n}" if len(pdfs) > 1 else "") + ".pdf")
            linhas.append(guardar(rede, f"{API_RIGEO}/bitstreams/{b['uuid']}/content", arq, "SGB — repositório institucional", f"SGB. {titulo} ({ano})",
                                  pagina_do_registro=f"https://rigeo.sgb.gov.br/handle/{item.get('handle', '')}", nome_no_repositorio=b.get("name"), data_do_documento=ano))
    return linhas


def main() -> None:
    DESTINO.mkdir(parents=True, exist_ok=True)
    rede = Rede(ARGS.pausa, ARGS.max_pedidos)
    linhas: list[dict] = []
    try:
        linhas += registros_rigeo(rede, ARGS.rigeo)
        linhas += informes_ina(rede, ARGS.periodos)
    except RuntimeError as e:  # teto de pedidos: grava a lista do que foi feito até aqui
        logger.error("%s", e)
    t = pd.DataFrame(linhas)
    arq = DESTINO / "documentos-regua_lista.csv"
    t.to_csv(arq, index=False)
    resumo = {"arquivo": arq.name, "script": SCRIPT, "motivo": MOTIVO, "data_acesso": datetime.now(timezone.utc).isoformat(timespec="seconds"), "periodos": ARGS.periodos, "registros_do_repositorio": ARGS.rigeo,
              "pedidos_por_servidor": rede.pedidos, "servidores_abandonados": rede.abandonados, "situacoes": t.situacao.value_counts().to_dict() if len(t) else {},
              "pausa_s": ARGS.pausa, "teto_de_pedidos": ARGS.max_pedidos,
              "campos": {"arquivo": "nome do arquivo gravado", "fonte": "quem publica", "referencia": "referência do documento", "endereco_de_origem": "endereço pedido",
                         "periodo": "período de busca", "data_do_documento": "data ou ano do documento", "situacao": "o que aconteceu com o pedido", "tamanho_bytes": "tamanho",
                         "sha256": "sha256 do arquivo", "paginas": "páginas do PDF", "data_do_download": "data e hora do download (UTC)"}}
    arq.with_suffix(".json").write_text(json.dumps(resumo, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Lista: %s (%d linhas). Pedidos: %s. Situações: %s", arq.relative_to(RAIZ), len(t), rede.pedidos, resumo["situacoes"])


def _pares(valores: list[str] | None, padrao: dict) -> dict:
    return dict(v.split("=", 1) for v in valores) if valores is not None else padrao


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    _p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    _p.add_argument("--periodos", nargs="*", help="períodos NOME=AAAA-MM-DD/AAAA-MM-DD dos informes diários (padrão: os três do roteiro; lista vazia = nenhum)")
    _p.add_argument("--rigeo", nargs="*", help="registros do repositório do SGB, NOME=UUID (padrão: nenhum)")
    _p.add_argument("--pausa", type=float, default=2.0, help="segundos entre pedidos ao mesmo servidor")
    _p.add_argument("--max-pedidos", type=int, default=300, help="teto de pedidos desta execução")
    ARGS = _p.parse_args()
    ARGS.periodos = _pares(ARGS.periodos, PERIODOS_PADRAO)
    ARGS.rigeo = _pares(ARGS.rigeo, {})
    main()
