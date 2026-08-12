"""Baixa o histórico de Reconhecimentos Federais de Situação de Emergência
(SE) e Estado de Calamidade Pública (ECP) do S2ID (Sistema Integrado de
Informações sobre Desastres / SEDEC-MIDR) para o estado do município alvo,
e filtra localmente pelo código IBGE. Gera:

    data/raw/desastres_s2id_{inicio}-{fim}_estadual.csv   (como baixado, nível UF)
    data/raw/desastres_s2id_{inicio}-{fim}_estadual.json
    data/processed/desastres_s2id_{inicio}-{fim}_municipal.csv  (filtrado pelo município)
    data/processed/desastres_s2id_{inicio}-{fim}_municipal.json

Por que Playwright em vez de requests puro
--------------------------------------------
O S2ID é uma aplicação JSF/PrimeFaces legada (sessão com jsessionid +
ViewState, sem endpoint REST). Duas rotas foram investigadas:

1. Página "Série Histórica" (/paginas/series/): expõe um botão de
   exportação por UF/ano, mas os dados aí ficam CONGELADOS em 2016 (dado
   real da fonte, confirmado consultando Brasil inteiro 2003-2026 — nenhum
   registro depois de 2016 nesse módulo). Inútil para histórico recente.
2. Página "Relatórios" > "Reconhecimento Federal" > "Relatório Gerencial -
   Reconhecimentos realizados" (/paginas/relatorios/): formulário com
   filtro de período (máx. 365 dias), tipologia de desastre e UF, com
   export nativo em CSV/XLS/PDF — e ESTE está atualizado (confirmado: RS
   2023 traz as enchentes de set-nov/2023, incluindo Uruguaiana). É a rota
   usada aqui.

Mesmo essa rota exige simular a navegação (não há URL de export por
querystring — o botão dispara um submit de formulário JSF), daí o uso do
Playwright em vez de requests/httpx. Decisão registrada com o usuário
(2026-08-11): optou por automação via Playwright em vez de pular a fonte
ou depender só do retrato atual (ver alternativa abaixo).

Granularidade
--------------
Eventual/por registro: 1 linha = 1 reconhecimento (Código IBGE do
município, tipo de desastre, data do decreto, portaria, D.O.U.). Column
"Código IBGE" já vem pronta na fonte — filtro local é exato, sem
necessidade de casar por nome de município.

Por que baixar por UF (não filtra por município na própria fonte)
--------------------------------------------------------------------
O formulário só filtra por Estado, não por município — baixa-se o
estado inteiro do município alvo (derivado do código IBGE via API de
localidades do IBGE, não hardcoded) e filtra-se localmente pelo Código
IBGE. O CSV bruto por UF fica em data/raw/ (nível estadual, útil para
comparar Uruguaiana com o cenário regional).

Período de 365 dias e anos bissextos
---------------------------------------
O formulário rejeita períodos > 365 dias. Um ano bissexto (366 dias)
inteiro (01/01-31/12) excede esse limite, então anos bissextos dentro do
intervalo são baixados em 2 sub-períodos (semestres). O ano corrente é
truncado na data de hoje.

Tempo de execução
-------------------
Cada exportação leva de dezenas de segundos a ~2min no servidor (visto em
testes manuais) — rodar a série completa 2013-hoje pode levar
20-40 minutos. O cache por sub-período (data/raw/cache_s2id/) torna
reruns e interrupções seguras: só falta o que ainda não foi baixado.

Alternativa não seguida (Base dos Dados / BigQuery)
--------------------------------------------------------
A tabela basedosdados.br_sedec_desastres.reconhecimentos_vigentes tem
id_municipio (IBGE) pronto, mas é só um RETRATO do que está vigente hoje
(1 data de extração), não série histórica — não serve sozinha para
"histórico de decretações". Poderia complementar no futuro (rodar
periodicamente para acumular o que for ficando vigente), mas não
substitui esta fonte agora.

Uso:
    python scripts/download/desastres_s2id.py
    python scripts/download/desastres_s2id.py --codigo-ibge 4314902 --ano-inicio 2013 --forcar
"""

import argparse
import calendar
import json
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests
from playwright.sync_api import sync_playwright

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

RAIZ = Path(__file__).resolve().parents[2]
CACHE_DIR = RAIZ / "data" / "raw" / "cache_s2id"
CAMINHO_RAW_TEMPLATE = RAIZ / "data" / "raw" / "desastres_s2id_{inicio}-{fim}_estadual.csv"
CAMINHO_PROCESSADO_TEMPLATE = RAIZ / "data" / "processed" / "desastres_s2id_{inicio}-{fim}_municipal.csv"

CODIGO_IBGE_DEFAULT = "4322400"  # Uruguaiana, RS
ANO_INICIO_DEFAULT = 2013  # a própria fonte documenta a série como "desde 2013"
URL_MUNICIPIO_IBGE = "https://servicodados.ibge.gov.br/api/v1/localidades/municipios/{codigo}"
URL_RELATORIOS = "https://s2id.mi.gov.br/paginas/relatorios/"

TITULO_ACCORDION = "Relatório Gerencial - Reconhecimentos realizados"
SEL_DATA_INICIO = "#abas\\:sanfonas\\:j_idt101_input"
SEL_DATA_FIM = "#abas\\:sanfonas\\:dt_final_realizados_input"
SEL_TODOS_DESASTRES = "#abas\\:sanfonas\\:selecionar_todos_cobrades"
SEL_BOTAO_CSV = 'button[id="abas:sanfonas:btnExportarCsv"]'

COLUNAS_ESPERADAS = [
    "Nº", "UF", "Código IBGE", "Município", "Nº do Decreto", "Data do Decreto",
    "Desastre", "SE/ECP", "Nº da Portaria", "Data da Portaria", "Nº do D.O.U.",
    "Data do D.O.U.", "Rito", "Processo",
]


def obter_municipio_uf(codigo_ibge: str) -> tuple[str, str, str]:
    """Consulta a API de localidades do IBGE. Retorna (nome_municipio, uf_sigla, uf_nome)."""
    resposta = requests.get(URL_MUNICIPIO_IBGE.format(codigo=codigo_ibge), timeout=30)
    resposta.raise_for_status()
    dados = resposta.json()
    uf = dados["microrregiao"]["mesorregiao"]["UF"]
    return dados["nome"], uf["sigla"], uf["nome"]


def periodos_do_ano(ano: int, hoje: date) -> list[tuple[date, date]]:
    """Divide um ano em 1 ou 2 sub-períodos que respeitam o limite de 365 dias do
    formulário (anos bissextos têm 366 dias, então viram 2 semestres). O ano
    corrente é truncado na data de hoje (sem baixar período futuro)."""
    inicio_ano = date(ano, 1, 1)
    fim_ano = date(ano, 12, 31)
    if fim_ano > hoje:
        fim_ano = hoje
    if inicio_ano > fim_ano:
        return []

    if calendar.isleap(ano) and (fim_ano - inicio_ano).days > 365:
        meio = date(ano, 6, 30)
        return [(inicio_ano, meio), (date(ano, 7, 1), fim_ano)]
    return [(inicio_ano, fim_ano)]


def _preencher_data(page, seletor: str, valor: str) -> None:
    """Os campos de período são <p:calendar> readonly (só aceitam clique no
    calendário) — setar o value via JS e disparar change/blur é mais confiável
    e mais rápido que simular a navegação no calendário popup."""
    page.eval_on_selector(
        seletor,
        "(el, val) => { el.value = val; "
        "el.dispatchEvent(new Event('input', {bubbles:true})); "
        "el.dispatchEvent(new Event('change', {bubbles:true})); "
        "el.dispatchEvent(new Event('blur', {bubbles:true})); }",
        valor,
    )


def baixar_periodo(page, uf_sigla: str, inicio: date, fim: date, destino: Path) -> None:
    page.goto(URL_RELATORIOS, wait_until="networkidle", timeout=30000)
    page.get_by_text(TITULO_ACCORDION, exact=True).click()
    page.wait_for_timeout(800)

    _preencher_data(page, SEL_DATA_INICIO, inicio.strftime("%d/%m/%Y"))
    _preencher_data(page, SEL_DATA_FIM, fim.strftime("%d/%m/%Y"))
    page.check(SEL_TODOS_DESASTRES)
    page.wait_for_timeout(300)
    page.check(f'input[name="abas:sanfonas:estados"][value="{uf_sigla}"]')
    page.wait_for_timeout(300)

    # a geração do CSV no servidor é lenta (visto até ~2min em testes manuais
    # para RS num ano de muitos registros, ex. 2023) — timeout generoso
    with page.expect_download(timeout=180000) as dl_info:
        page.locator(SEL_BOTAO_CSV).click(no_wait_after=True)
    dl_info.value.save_as(destino)


def ler_csv_s2id(caminho: Path) -> pd.DataFrame:
    """O export do S2ID vem em ISO-8859-1, ';'-separado, com 3 linhas de título
    antes do cabeçalho real e 2 linhas de totais no final — descartadas aqui."""
    df = pd.read_csv(caminho, sep=";", encoding="latin1", skiprows=3, dtype=str)
    df = df[df["UF"].notna() & df["UF"].str.len().eq(2)].copy()
    return df[COLUNAS_ESPERADAS]


def main() -> None:
    parser = argparse.ArgumentParser(description="Baixa histórico de reconhecimentos federais (S2ID) por UF e filtra por município.")
    parser.add_argument("--codigo-ibge", default=CODIGO_IBGE_DEFAULT)
    parser.add_argument("--ano-inicio", type=int, default=ANO_INICIO_DEFAULT)
    parser.add_argument("--ano-fim", type=int, default=None, help="Default: ano corrente")
    parser.add_argument("--forcar", action="store_true", help="Ignora o cache e baixa tudo de novo")
    args = parser.parse_args()

    hoje = date.today()
    ano_fim = args.ano_fim or hoje.year

    nome_municipio, uf_sigla, uf_nome = obter_municipio_uf(args.codigo_ibge)
    logger.info("Município: %s (%s / %s) — código IBGE %s", nome_municipio, uf_sigla, uf_nome, args.codigo_ibge)

    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    tarefas: list[tuple[int, date, date, Path]] = []
    for ano in range(args.ano_inicio, ano_fim + 1):
        for idx, (inicio, fim) in enumerate(periodos_do_ano(ano, hoje)):
            destino = CACHE_DIR / f"reconhecimentos_{uf_sigla}_{ano}_p{idx}.csv"
            tarefas.append((ano, inicio, fim, destino))

    pendentes = [t for t in tarefas if args.forcar or not t[3].exists()]
    logger.info("%d sub-períodos no total, %d pendentes de download", len(tarefas), len(pendentes))

    if pendentes:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            for i, (ano, inicio, fim, destino) in enumerate(pendentes, start=1):
                logger.info("[%d/%d] Baixando %s a %s (UF=%s)...", i, len(pendentes), inicio, fim, uf_sigla)
                try:
                    baixar_periodo(page, uf_sigla, inicio, fim, destino)
                    logger.info("  -> salvo em %s", destino)
                except Exception:
                    logger.exception("  -> falhou %s a %s — será tentado novamente na próxima execução (cache não gravado)", inicio, fim)
            browser.close()

    tabelas = [ler_csv_s2id(t[3]) for t in tarefas if t[3].exists()]
    if not tabelas:
        raise RuntimeError("Nenhum sub-período baixado com sucesso — nada a processar.")

    completos = len(tabelas)
    if completos < len(tarefas):
        logger.warning("%d/%d sub-períodos ausentes (falha de download) — série incompleta nesta execução, rode de novo para completar.", len(tarefas) - completos, len(tarefas))

    bruto = pd.concat(tabelas, ignore_index=True).drop_duplicates()
    bruto["Data do Decreto"] = pd.to_datetime(bruto["Data do Decreto"], format="%d/%m/%Y", errors="coerce")
    bruto = bruto.sort_values("Data do Decreto").reset_index(drop=True)

    caminho_raw = Path(str(CAMINHO_RAW_TEMPLATE).format(inicio=args.ano_inicio, fim=ano_fim))
    caminho_raw.parent.mkdir(parents=True, exist_ok=True)
    bruto.to_csv(caminho_raw, index=False, encoding="utf-8")
    logger.info("Bruto (nível %s): %s (%d linhas)", uf_sigla, caminho_raw, len(bruto))

    metadados_raw = {
        "fonte": "S2ID (Sistema Integrado de Informações sobre Desastres) / SEDEC-MIDR — https://s2id.mi.gov.br/paginas/relatorios/ (Relatório Gerencial - Reconhecimentos realizados)",
        "metodo": (
            "automação via Playwright do formulário de relatórios (sem endpoint REST/CSV por "
            "querystring disponível) — período máximo de 365 dias por consulta, anos bissextos "
            "baixados em 2 semestres; ver docstring do script para a comparação com a página "
            "'Série Histórica' (descartada: dados congelados em 2016)"
        ),
        "uf": uf_sigla,
        "uf_nome": uf_nome,
        "periodo_coberto": f"{args.ano_inicio}-{ano_fim}",
        "nivel_agregacao": "estadual — todos os reconhecimentos do estado do município alvo, não só o município",
        "sub_periodos_totais": len(tarefas),
        "sub_periodos_baixados_com_sucesso": completos,
        "data_processamento": datetime.now(timezone.utc).isoformat(),
    }
    caminho_raw.with_suffix(".json").write_text(json.dumps(metadados_raw, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

    municipal = bruto[bruto["Código IBGE"] == args.codigo_ibge].copy()
    caminho_proc = Path(str(CAMINHO_PROCESSADO_TEMPLATE).format(inicio=args.ano_inicio, fim=ano_fim))
    caminho_proc.parent.mkdir(parents=True, exist_ok=True)
    municipal.to_csv(caminho_proc, index=False, encoding="utf-8")
    logger.info("Filtrado (município %s): %s (%d linhas)", nome_municipio, caminho_proc, len(municipal))

    metadados_proc = {
        "fonte": metadados_raw["fonte"],
        "metodo": f"filtro local de {caminho_raw.name} por Código IBGE = {args.codigo_ibge}",
        "codigo_ibge": args.codigo_ibge,
        "nome_municipio": nome_municipio,
        "uf": uf_sigla,
        "periodo_coberto": f"{args.ano_inicio}-{ano_fim}",
        "nivel_agregacao": "municipal (evento) — 1 linha por reconhecimento federal (SE ou ECP)",
        "colunas": {
            "Código IBGE": "código IBGE do município (7 dígitos)",
            "Nº do Decreto": "número do decreto municipal/estadual que originou o pedido",
            "Data do Decreto": "data do decreto de origem (não a data da portaria de reconhecimento federal)",
            "Desastre": "tipologia COBRADE do desastre",
            "SE/ECP": "tipo de reconhecimento: Situação de Emergência ou Estado de Calamidade Pública",
            "Nº da Portaria / Data da Portaria": "portaria federal (SEDEC/MIDR) que reconheceu o pedido",
        },
        "sub_periodos_baixados_com_sucesso": f"{completos}/{len(tarefas)}",
        "data_processamento": datetime.now(timezone.utc).isoformat(),
    }
    caminho_proc.with_suffix(".json").write_text(json.dumps(metadados_proc, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    logger.info("Concluído.")


if __name__ == "__main__":
    main()
