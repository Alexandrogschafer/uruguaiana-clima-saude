"""Baixa a série mensal de precipitação para a área de estudo estendida
(buffer sobre o município, config/area_estudo_bacias.geojson) e gera a
média espacial de precipitação por mês:

    data/raw/precipitacao_{fonte-dominante}_{ano_inicio}-{ano_fim}_mensal.csv
    data/raw/precipitacao_{fonte-dominante}_{ano_inicio}-{ano_fim}_mensal.json

`{fonte-dominante}` é decidido DEPOIS de processar todos os meses (não dá
pra saber de antemão se o CHIRPS vai bloquear) — "chirps" se a maioria
dos meses veio do CHIRPS, "merge-inpe" se o fallback dominou (ver
CRITÉRIO DE NOMEAÇÃO DO ARQUIVO mais abaixo). Isso já aconteceu na
prática: a primeira coleta (2026-08-09) gerou
precipitacao_chirps_2010-2025_mensal.csv, mas 192/192 meses vieram do
fallback MERGE — nome corrigido/renomeado manualmente para
precipitacao_merge-inpe_2010-2025_mensal.csv, e a lógica abaixo evita
que isso aconteça de novo silenciosamente numa próxima coleta.

Fonte primária: CHIRPS v2.0
-----------------------------
CHIRPS v2.0 (Climate Hazards Group InfraRed Precipitation with Station
data, UCSB), grade global mensal ~0,05° (~5,5 km), distribuída como um
GeoTIFF (.tif.gz) por mês em
https://data.chc.ucsb.edu/products/CHIRPS-2.0/global_monthly/tifs/. Sem
API/autenticação — download HTTP público, arquivo por mês desde 1981.
Cada arquivo é lido em streaming via GDAL (/vsigzip//vsicurl/), sem
baixar o GeoTIFF global por completo.

Histórico do bloqueio de IP (2026-08-07 -> resolvido em 2026-08-09)
----------------------------------------------------------------------
Em 2026-08-07, data.chc.ucsb.edu bloqueou o IP (HTTP 403) depois de só
~3 meses processados em sequência rápida (~15 requisições em poucos
segundos) — 45s de espera não foi suficiente pra desbloquear na hora.
Testado de novo em 2026-08-09 (2 dias depois): bloqueio já não estava
mais ativo (3 requisições HEAD seguidas + 1 leitura real via GDAL, todas
OK) — era temporário, não permanente. Mesmo assim, este script agora:

1. Espaça as requisições (DELAY_ENTRE_MESES_S entre cada mês) para não
   provocar um novo bloqueio por rajada.
2. Salva cada mês incrementalmente no CSV de saída (append + flush +
   fsync), não só ao final do loop inteiro — se travar de novo, os
   meses já processados não se perdem; uma nova execução (sem --forcar)
   retoma de onde parou.
3. Se o CHIRPS voltar a falhar de forma persistente (3 meses seguidos
   com erro após as tentativas internas de retry — não confundir com
   "mês sem arquivo publicado", que é uma condição normal e não conta
   pra esse limite), troca automaticamente para a fonte alternativa
   (INPE/MERGE, ver abaixo) para os meses restantes da série, e
   documenta no metadado (campo `fontes_usadas_por_mes` e
   `fonte_dado` por linha no CSV) quais meses vieram de qual fonte.

Fonte alternativa: INPE/MERGE (CPTEC), acionada só se o CHIRPS bloquear
---------------------------------------------------------------------------
Produto MERGE (CPTEC/INPE) não tem uma grade MENSAL pronta — só diária,
em https://ftp.cptec.inpe.br/modelos/tempo/MERGE/GPM/DAILY/{ano}/{mes}/
MERGE_CPTEC_{ano}{mes}{dia}.grib2 (grade ~10km, GRIB2, lido também via
GDAL /vsicurl/, validado com uma leitura real antes de implementar:
2026-08-09, ~0,4s por dia). Este script soma as médias espaciais diárias
pra obter o total mensal (mesma grandeza física do "mean" do CHIRPS
mensal). min/max mensais são a SOMA dos mínimos/máximos diários (não o
mínimo/máximo do total acumulado por pixel — ficaria caro demais manter
a grade completa de 28-31 dias em memória só pra isso) — aproximação
documentada no metadado quando essa fonte é usada, não é o mesmo cálculo
exato do CHIRPS.

Janela temporal: 2010 até o último ano CALENDÁRIO COMPLETO disponível no
CHIRPS (mesmo critério usado no SIH/SINAN) — detectado verificando a
existência do arquivo de dezembro via HTTP HEAD, não hardcoded.

Uso:
    python scripts/download/precipitacao_chirps.py
    python scripts/download/precipitacao_chirps.py --codigo-ibge 4314902 --forcar
"""

import argparse
import calendar
import csv
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import pandas as pd
import requests
from rasterstats import zonal_stats

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

RAIZ = Path(__file__).resolve().parents[2]
CAMINHO_AREA_BACIAS_DEFAULT = RAIZ / "config" / "area_estudo_bacias.geojson"

# nome do CSV/JSON final segue a convenção {tema}_{fonte}_... do projeto — mas qual fonte
# predominou só se sabe ao FIM do processamento (o CHIRPS pode bloquear a qualquer momento).
# Enquanto roda, usa um nome de trabalho neutro (sem fonte no nome); ao terminar, decide o
# rótulo pela contagem de meses por fonte e renomeia pro nome final (ver `nomear_saida_final`).
# Retomada (resume) busca primeiro por um arquivo já nomeado (de uma execução anterior
# concluída) e, achando, continua escrevendo nele — só troca de nome nesta mesma execução se a
# maioria mudar de lado até o fim.
PADRAO_SAIDA_TRABALHO = "data/raw/precipitacao_pendente_{inicio}-{fim}_mensal.csv"
PADRAO_GLOB_SAIDA_QUALQUER_FONTE = "data/raw/precipitacao_*_{inicio}-{fim}_mensal.csv"
ROTULO_FONTE_NO_NOME = {"chirps": "chirps", "merge_inpe_cptec_diario_agregado": "merge-inpe"}

BASE_URL_CHIRPS = "https://data.chc.ucsb.edu/products/CHIRPS-2.0/global_monthly/tifs/chirps-v2.0.{ano}.{mes:02d}.tif.gz"
NODATA_CHIRPS = -9999
URL_MERGE_DIARIO = "https://ftp.cptec.inpe.br/modelos/tempo/MERGE/GPM/DAILY/{ano}/{mes:02d}/MERGE_CPTEC_{ano}{mes:02d}{dia:02d}.grib2"

ANO_INICIO_DEFAULT = 2010
N_TENTATIVAS = 3
BACKOFF_BASE_S = 2.0
DELAY_ENTRE_MESES_S = 3.0
DELAY_ENTRE_DIAS_MERGE_S = 0.3
LIMITE_FALHAS_CONSECUTIVAS_CHIRPS = 3

CAMPOS_CSV = [
    "ano", "mes", "codigo_ibge", "nome_municipio", "fonte_dado",
    "precipitacao_media_mm", "precipitacao_min_mm", "precipitacao_max_mm", "n_pixels",
]

HEADERS = {"User-Agent": "Mozilla/5.0 (compativel; script-pesquisa-ClimaPampa/1.0)"}
URL_MUNICIPIO_IBGE = "https://servicodados.ibge.gov.br/api/v1/localidades/municipios/{codigo}"


def obter_uf_sigla(codigo_ibge: str) -> tuple[str, str]:
    resposta = requests.get(URL_MUNICIPIO_IBGE.format(codigo=codigo_ibge), headers=HEADERS, timeout=30)
    resposta.raise_for_status()
    dados = resposta.json()
    return dados["nome"], dados["microrregiao"]["mesorregiao"]["UF"]["sigla"]


class ChirpsBloqueadoError(Exception):
    """HTTP 403 do CHIRPS — indica possível bloqueio de IP, diferente de arquivo genuinamente
    ausente (404): tratar os dois casos como a mesma coisa foi um bug real encontrado ao testar
    este script (2026-08-09) — um HEAD que retornava 403 (bloqueio já reativado depois de só
    ~15 requisições de teste) era silenciosamente interpretado como "mês sem arquivo", e a
    troca automática de fonte (baseada em RuntimeError) nunca disparava."""


def verificar_chirps(ano: int, mes: int) -> bool:
    """True se o arquivo existe (200). False se genuinamente ausente (404/outros). Levanta
    ChirpsBloqueadoError em 403 — não deve ser confundido com ausência real do arquivo."""
    resposta = requests.head(BASE_URL_CHIRPS.format(ano=ano, mes=mes), headers=HEADERS, timeout=30)
    if resposta.status_code == 403:
        raise ChirpsBloqueadoError(f"HTTP 403 ao checar CHIRPS {ano}-{mes:02d} (possível bloqueio de IP)")
    return resposta.status_code == 200


def descobrir_ultimo_ano_completo() -> int:
    """Verifica (via HTTP HEAD) o dezembro de anos candidatos, do mais recente para trás. Se o
    bloqueio estiver ativo (403), tenta com backoff antes de desistir — nunca confunde bloqueio
    com "ano incompleto" (o que erradamente jogaria ano_fim pra trás)."""
    ano = datetime.now(timezone.utc).year - 1
    while ano >= 1981:
        for tentativa in range(1, N_TENTATIVAS + 1):
            try:
                if verificar_chirps(ano, 12):
                    return ano
                break  # 404 genuíno pra este ano — tenta o anterior, sem retry
            except ChirpsBloqueadoError as erro:
                if tentativa == N_TENTATIVAS:
                    raise RuntimeError(
                        f"CHIRPS bloqueado (HTTP 403) ao tentar descobrir o último ano completo — "
                        f"não é possível determinar ano_fim com segurança. Tente de novo mais tarde, "
                        f"ou informe --ano-fim manualmente. Último erro: {erro}"
                    ) from erro
                espera = BACKOFF_BASE_S * (2 ** (tentativa - 1))
                logger.warning("CHIRPS bloqueado ao checar %d-12 (tentativa %d/%d) — nova tentativa em %.0fs", ano, tentativa, N_TENTATIVAS, espera)
                time.sleep(espera)
        ano -= 1
    raise RuntimeError("Não encontrou nenhum ano completo de CHIRPS — verifique conectividade/URL da fonte.")


def extrair_precipitacao_mes_chirps(ano: int, mes: int, area_4326: gpd.GeoDataFrame) -> dict | None:
    """Extrai estatísticas zonais de precipitação (CHIRPS) para um mês, com retry.
    None se o mês genuinamente não existir na fonte (404 — condição normal, não é bloqueio).
    Levanta RuntimeError se as tentativas se esgotarem por 403/erro de rede/leitura (possível
    bloqueio) — o chamador decide se isso conta pra troca de fonte."""
    url_vsi = "/vsigzip//vsicurl/" + BASE_URL_CHIRPS.format(ano=ano, mes=mes)

    ultimo_erro: Exception | None = None
    for tentativa in range(1, N_TENTATIVAS + 1):
        try:
            if not verificar_chirps(ano, mes):
                logger.warning("CHIRPS %d-%02d: arquivo não encontrado na fonte (404).", ano, mes)
                return None
            stats = zonal_stats(area_4326, url_vsi, stats=["mean", "min", "max", "count"], nodata=NODATA_CHIRPS)
            return stats[0]
        except Exception as erro:  # noqa: BLE001 — inclui ChirpsBloqueadoError e qualquer falha de rede/leitura
            ultimo_erro = erro
            if tentativa < N_TENTATIVAS:
                espera = BACKOFF_BASE_S * (2 ** (tentativa - 1))
                logger.warning(
                    "Falha ao ler CHIRPS %d-%02d (tentativa %d/%d): %s — nova tentativa em %.0fs",
                    ano, mes, tentativa, N_TENTATIVAS, erro, espera,
                )
                time.sleep(espera)
    raise RuntimeError(f"Falha ao ler CHIRPS {ano}-{mes:02d} após {N_TENTATIVAS} tentativas: {ultimo_erro}")


def extrair_precipitacao_mes_merge(ano: int, mes: int, area_4326: gpd.GeoDataFrame) -> dict | None:
    """Fallback: soma as médias espaciais diárias do MERGE/INPE (CPTEC) pra obter o total do
    mês. None se nenhum dia do mês tiver arquivo disponível na fonte."""
    n_dias_no_mes = calendar.monthrange(ano, mes)[1]
    medias_diarias, minimos_diarios, maximos_diarios = [], [], []
    n_pixels = None

    for dia in range(1, n_dias_no_mes + 1):
        url_vsi = "/vsicurl/" + URL_MERGE_DIARIO.format(ano=ano, mes=mes, dia=dia)
        try:
            stats = zonal_stats(area_4326, url_vsi, stats=["mean", "min", "max", "count"], nodata=-999)[0]
        except Exception as erro:  # noqa: BLE001 — dia sem arquivo/erro de leitura: pula, não aborta o mês inteiro
            logger.warning("MERGE %d-%02d-%02d indisponível (%s) — dia ignorado na soma do mês.", ano, mes, dia, erro)
            time.sleep(DELAY_ENTRE_DIAS_MERGE_S)
            continue
        if stats.get("mean") is not None:
            medias_diarias.append(stats["mean"])
            minimos_diarios.append(stats["min"])
            maximos_diarios.append(stats["max"])
            n_pixels = stats["count"]
        time.sleep(DELAY_ENTRE_DIAS_MERGE_S)

    if not medias_diarias:
        logger.warning("MERGE %d-%02d: nenhum dia com dado disponível.", ano, mes)
        return None

    logger.info("MERGE %d-%02d: %d/%d dias com dado.", ano, mes, len(medias_diarias), n_dias_no_mes)
    return {
        "mean": round(sum(medias_diarias), 2),
        "min": round(sum(minimos_diarios), 2),
        "max": round(sum(maximos_diarios), 2),
        "count": n_pixels,
        "n_dias_com_dado": len(medias_diarias),
        "n_dias_no_mes": n_dias_no_mes,
    }


def localizar_arquivo_trabalho(ano_inicio: int, ano_fim: int, forcar: bool) -> Path:
    """Acha um CSV já existente (de execução anterior, concluída ou interrompida, com
    qualquer rótulo de fonte no nome) pra retomar; senão usa o nome de trabalho neutro. Com
    --forcar, apaga qualquer candidato encontrado e recomeça do zero."""
    padrao = PADRAO_GLOB_SAIDA_QUALQUER_FONTE.format(inicio=ano_inicio, fim=ano_fim)
    candidatos = sorted(RAIZ.glob(padrao))
    if forcar:
        for candidato in candidatos:
            candidato.unlink()
            candidato.with_suffix(".json").unlink(missing_ok=True)
        candidatos = []
    if candidatos:
        return candidatos[0]
    return RAIZ / PADRAO_SAIDA_TRABALHO.format(inicio=ano_inicio, fim=ano_fim)


def nomear_saida_final(caminho_trabalho: Path, ano_inicio: int, ano_fim: int, n_meses_por_fonte: dict[str, int]) -> Path:
    """Renomeia o CSV de trabalho pro nome final {tema}_{fonte}_... (convenção do projeto),
    decidido pela fonte que forneceu mais meses — só se sabe ao fim do processamento, porque o
    CHIRPS pode bloquear a qualquer momento (aconteceu na prática: a 1ª coleta, 2026-08-09,
    saiu 100% MERGE apesar do nome de trabalho não indicar isso)."""
    fonte_dominante = max(n_meses_por_fonte, key=n_meses_por_fonte.get) if any(n_meses_por_fonte.values()) else None
    rotulo = ROTULO_FONTE_NO_NOME.get(fonte_dominante, "pendente")
    caminho_final = RAIZ / "data" / "raw" / f"precipitacao_{rotulo}_{ano_inicio}-{ano_fim}_mensal.csv"
    if caminho_final != caminho_trabalho:
        if caminho_final.exists():
            caminho_final.unlink()
        caminho_trabalho.rename(caminho_final)
        logger.info("Renomeado para refletir a fonte dominante (%s): %s -> %s", rotulo, caminho_trabalho.name, caminho_final.name)
    return caminho_final


def carregar_meses_ja_processados(caminho_csv: Path) -> set[tuple[int, int]]:
    if not caminho_csv.exists():
        return set()
    df = pd.read_csv(caminho_csv, usecols=["ano", "mes"])
    return set(zip(df["ano"].tolist(), df["mes"].tolist()))


def escrever_linha_incremental(caminho_csv: Path, linha: dict, escrever_header: bool) -> None:
    with open(caminho_csv, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CAMPOS_CSV)
        if escrever_header:
            writer.writeheader()
        writer.writerow(linha)
        f.flush()
        os.fsync(f.fileno())


def main() -> None:
    parser = argparse.ArgumentParser(description="Baixa série mensal de precipitação (CHIRPS, com fallback INPE/MERGE) para a área de estudo estendida.")
    parser.add_argument("--codigo-ibge", default="4322400", help="Código IBGE do município (só para rótulo/metadado — a área usada é --area-bacias)")
    parser.add_argument("--area-bacias", type=Path, default=CAMINHO_AREA_BACIAS_DEFAULT)
    parser.add_argument("--ano-inicio", type=int, default=ANO_INICIO_DEFAULT)
    parser.add_argument("--ano-fim", type=int, default=None, help="Default: último ano calendário completo disponível na fonte")
    parser.add_argument("--forcar", action="store_true", help="Ignora progresso incremental salvo e recomeça do zero")
    args = parser.parse_args()

    if not args.area_bacias.exists():
        raise FileNotFoundError(
            f"{args.area_bacias} não encontrado. Rode primeiro: python scripts/processamento/area_estudo_bacias.py"
        )

    nome_municipio, uf = obter_uf_sigla(args.codigo_ibge)
    area_estudo_bacias = gpd.read_file(args.area_bacias)
    area_4326 = area_estudo_bacias.to_crs("EPSG:4326")

    ano_fim = args.ano_fim or descobrir_ultimo_ano_completo()
    logger.info("Município de referência: %s (%s) — janela: %d-%d", nome_municipio, uf, args.ano_inicio, ano_fim)

    caminho_saida = localizar_arquivo_trabalho(args.ano_inicio, ano_fim, args.forcar)
    caminho_saida.parent.mkdir(parents=True, exist_ok=True)

    ja_processados = carregar_meses_ja_processados(caminho_saida)
    if ja_processados:
        logger.info("Retomando: %d mês(es) já processado(s) em %s — pulando esses.", len(ja_processados), caminho_saida)
    escrever_header = not caminho_saida.exists()

    usando_merge = False
    falhas_consecutivas_chirps = 0
    meses_sem_arquivo: list[str] = []
    meses_por_fonte: dict[str, list[str]] = {"chirps": [], "merge_inpe_cptec_diario_agregado": []}
    detalhes_merge: dict[str, dict] = {}

    for ano in range(args.ano_inicio, ano_fim + 1):
        for mes in range(1, 13):
            if (ano, mes) in ja_processados:
                continue

            rotulo_mes = f"{ano}-{mes:02d}"
            stats = None
            fonte = None

            if not usando_merge:
                try:
                    stats = extrair_precipitacao_mes_chirps(ano, mes, area_4326)
                    falhas_consecutivas_chirps = 0
                    if stats is not None:
                        fonte = "chirps"
                except RuntimeError as erro:
                    falhas_consecutivas_chirps += 1
                    logger.error(
                        "CHIRPS %s falhou (%d falha(s) consecutiva(s) de %d): %s",
                        rotulo_mes, falhas_consecutivas_chirps, LIMITE_FALHAS_CONSECUTIVAS_CHIRPS, erro,
                    )
                    if falhas_consecutivas_chirps >= LIMITE_FALHAS_CONSECUTIVAS_CHIRPS:
                        usando_merge = True
                        logger.warning(
                            "Bloqueio persistente detectado no CHIRPS (%d falhas seguidas) — trocando para "
                            "INPE/MERGE a partir de %s.", falhas_consecutivas_chirps, rotulo_mes,
                        )

            if usando_merge and stats is None:
                stats_merge = extrair_precipitacao_mes_merge(ano, mes, area_4326)
                if stats_merge is not None:
                    fonte = "merge_inpe_cptec_diario_agregado"
                    detalhes_merge[rotulo_mes] = {
                        "n_dias_com_dado": stats_merge.pop("n_dias_com_dado"),
                        "n_dias_no_mes": stats_merge.pop("n_dias_no_mes"),
                    }
                    stats = stats_merge

            if stats is None or stats.get("mean") is None:
                meses_sem_arquivo.append(rotulo_mes)
                linha = {
                    "ano": ano, "mes": mes, "codigo_ibge": args.codigo_ibge, "nome_municipio": nome_municipio,
                    "fonte_dado": pd.NA,
                    "precipitacao_media_mm": pd.NA, "precipitacao_min_mm": pd.NA, "precipitacao_max_mm": pd.NA,
                    "n_pixels": pd.NA,
                }
            else:
                meses_por_fonte[fonte].append(rotulo_mes)
                linha = {
                    "ano": ano, "mes": mes, "codigo_ibge": args.codigo_ibge, "nome_municipio": nome_municipio,
                    "fonte_dado": fonte,
                    "precipitacao_media_mm": round(stats["mean"], 2),
                    "precipitacao_min_mm": round(stats["min"], 2),
                    "precipitacao_max_mm": round(stats["max"], 2),
                    "n_pixels": stats["count"],
                }

            escrever_linha_incremental(caminho_saida, linha, escrever_header)
            escrever_header = False
            time.sleep(DELAY_ENTRE_MESES_S)

        logger.info("Ano %d processado.", ano)

    n_meses_por_fonte = {fonte: len(meses) for fonte, meses in meses_por_fonte.items()}
    caminho_saida = nomear_saida_final(caminho_saida, args.ano_inicio, ano_fim, n_meses_por_fonte)

    tabela = pd.read_csv(caminho_saida)
    logger.info(
        "Concluído: %s (%d meses no total; %d via CHIRPS, %d via MERGE/INPE, %d sem arquivo em nenhuma fonte)",
        caminho_saida, len(tabela), len(meses_por_fonte["chirps"]), len(meses_por_fonte["merge_inpe_cptec_diario_agregado"]),
        len(meses_sem_arquivo),
    )

    metadados = {
        "fonte_primaria": "CHIRPS v2.0 (Climate Hazards Group InfraRed Precipitation with Station data, UCSB) — https://data.chc.ucsb.edu/products/CHIRPS-2.0/global_monthly/tifs/",
        "fonte_alternativa_se_chirps_bloquear": "MERGE (CPTEC/INPE), grade diária agregada para total mensal — https://ftp.cptec.inpe.br/modelos/tempo/MERGE/GPM/DAILY/",
        "licenca": "CHIRPS: domínio público/uso livre (Climate Hazards Center, UCSB). MERGE/INPE: dados abertos do CPTEC/INPE.",
        "resolucao_espacial_original": "CHIRPS ~0,05° (~5,5 km); MERGE ~0,1° (~10 km)",
        "resolucao_temporal": "mensal (CHIRPS nativo; MERGE agregado de diário para mensal por este script)",
        "metodo": (
            "CHIRPS: leitura em streaming via GDAL (/vsigzip//vsicurl/) de um GeoTIFF global por mês, sem "
            "baixar o arquivo inteiro; média espacial (rasterstats.zonal_stats) da precipitação (mm) dentro "
            "da área de estudo ESTENDIDA (buffer de 18 km sobre o limite municipal — "
            "config/area_estudo_bacias.geojson). MERGE (só se acionado como fallback): soma das médias "
            "espaciais diárias (mesma área) para obter o total mensal; min/max mensais são a SOMA dos "
            "mínimos/máximos diários (aproximação — não é o mínimo/máximo real do total acumulado por "
            "pixel, ver detalhes_merge_por_mes para quantos dias de cada mês tinham dado)."
        ),
        "delay_entre_requisicoes_s": DELAY_ENTRE_MESES_S,
        "salvamento_incremental": (
            "cada mês é gravado no CSV assim que processado (append+flush+fsync), não só ao final do "
            "loop — uma execução interrompida (ex. novo bloqueio) preserva os meses já processados; "
            "rodar de novo sem --forcar retoma dos meses faltantes"
        ),
        "criterio_troca_de_fonte": (
            f"CHIRPS -> MERGE/INPE após {LIMITE_FALHAS_CONSECUTIVAS_CHIRPS} falhas consecutivas do CHIRPS "
            "por erro de rede/leitura (possível bloqueio) — mês sem arquivo publicado na fonte não conta "
            "para esse limite, é tratado à parte (ver meses_sem_arquivo_disponivel)"
        ),
        "criterio_nomeacao_arquivo": (
            "o nome do CSV/JSON (precipitacao_{fonte}_{...}) segue a convenção {tema}_{fonte}_... do "
            "projeto, decidido pela fonte que forneceu mais meses (ver n_meses_por_fonte) — só é "
            "possível saber ao final do processamento, já que o CHIRPS pode bloquear a qualquer "
            "momento; o arquivo é renomeado automaticamente ao fim de cada execução se a fonte "
            "dominante mudar"
        ),
        "codigo_ibge_referencia": args.codigo_ibge,
        "nome_municipio_referencia": nome_municipio,
        "uf": uf,
        "area_usada": "config/area_estudo_bacias.geojson (buffer de 18 km sobre area_estudo.geojson — NÃO é o limite municipal estrito)",
        "periodo_coberto": f"{args.ano_inicio}-{ano_fim}",
        "criterio_ano_fim": "último ano calendário com os 12 meses publicados no CHIRPS no momento da coleta (verificado via HTTP HEAD do arquivo de dezembro)",
        "nivel_agregacao": "média espacial sobre a área de estudo estendida, por mês — não há série por pixel/sub-área neste CSV",
        "nodata": f"CHIRPS: {NODATA_CHIRPS} (não vem marcado no header do GeoTIFF, tratado explicitamente); MERGE: -999",
        "n_meses_por_fonte": n_meses_por_fonte,
        "meses_por_fonte": meses_por_fonte,
        "detalhes_merge_por_mes": detalhes_merge,
        "meses_sem_arquivo_disponivel": meses_sem_arquivo,
        "data_processamento": datetime.now(timezone.utc).isoformat(),
    }
    caminho_metadados = caminho_saida.with_suffix(".json")
    caminho_metadados.write_text(
        json.dumps(metadados, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )
    logger.info("Metadados salvos em %s", caminho_metadados)


if __name__ == "__main__":
    main()
