"""
Entrada dos estabelecimentos de saúde REVISADOS (versão 4, 2026): cadastro do
CNES (Ministério da Saúde) conferido e corrigido pela equipe do projeto com
informações dos profissionais de saúde do município.

Não há API: o arquivo é entregue pela equipe (GeoJSON local, passado em
--origem). O script é idempotente (não regrava se as saídas existirem, a menos
de --forcar), confere o sha256 esperado e grava:

  a) data/raw/vetor/saude-estabelecimentos_cnes-revisado-v4_2026_vetorial.gpkg (+ .json)
     — arquivo completo interno (66 estabelecimentos), SEM as colunas "path" e
       "layer" (caminho de pasta pessoal) e SEM o registro "Consultório na Rua"
       (CNES 7129440), que guardava a soma das populações das unidades, não uma
       unidade (decisão do responsável, 2026-10-02). Telefone institucional fica só aqui.
       CRS original (EPSG:4326, CRS84 no arquivo).
  b) data/processed/saude/unidades-saude-esf-ubs_cnes-revisado-v4_2026_pontos.gpkg (+ .json)
     — camada de trabalho das 23 unidades da atenção primária (ESF e UBS),
       EPSG:31981, com classe, rótulo, zona e bairro (malha de setores 2022).

Seleção e classe (decisão do responsável, 2026-10-02):
  - tipo_unidade "ESF" e categoria "APS" (18)                   -> "ESF"
  - UBS do interior, CNES 2247356, 2247348, 2247321 (3)         -> "UBS"
  - Unidade Dispensadora de Medicação, CNES 7265220 (1)         -> "a confirmar"
  - Equipe de Saúde Prisional, CNES 4126947 (1)                 -> "sem classe"
  Ficam de fora o Consultório na Rua (CNES 7129440) e os demais.

As colunas populacao2017/2023/2026 são números informados diretamente pelos
profissionais de saúde do município. O arquivo não define o conceito
(população cadastrada, adscrita ou atendida): pendente de confirmação. Valor
ausente fica nulo (nunca zero). Nada é somado nem comparado com o Censo.

Uso:
  python scripts/download/saude_unidades_revisadas.py --origem <arquivo .geojson> --codigo-ibge 4322400
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

sys.path.append(str(Path(__file__).resolve().parents[1] / "utils"))
from recorte_municipio import CRS_PADRAO, carregar_area_estudo  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

RAIZ = Path(__file__).resolve().parents[2]
SHA256_ESPERADO = "a72c69c8d661eab34d7d0b9630501198e78aba91b8d5de8e9e23571a4d8c5557"
SAIDA_RAW = RAIZ / "data" / "raw" / "vetor" / "saude-estabelecimentos_cnes-revisado-v4_2026_vetorial.gpkg"
SAIDA_PROC = RAIZ / "data" / "processed" / "saude" / "unidades-saude-esf-ubs_cnes-revisado-v4_2026_pontos.gpkg"
CREDITO = ("CNES (Ministério da Saúde), revisado e corrigido pela equipe do projeto com informações dos "
           "profissionais de saúde do município — versão 4, 2026")
CAMPOS_RETIRADOS = ["path", "layer"]  # trazem o caminho de uma pasta pessoal: nunca entram no repositório
UBS_INTERIOR = {"2247356", "2247348", "2247321"}
# registro que não é uma unidade: guardava a SOMA das populações das outras (sai de todas as cópias)
CNES_RETIRADOS = {"7129440": "registro retirado: guardava a soma das populações das unidades, não uma unidade (Consultório na Rua)"}
CNES_UDM, CNES_PRISIONAL = "7265220", "4126947"
ROTULO_FIXO = {CNES_UDM: "UDM", CNES_PRISIONAL: "Prisional"}
POP = ["populacao2017", "populacao2023", "populacao2026"]
NOTA_POP = ("População da unidade informada pelos profissionais de saúde do município (2017, 2023, 2026). "
            "O arquivo não define o conceito (população cadastrada, adscrita ou atendida): PENDENTE DE CONFIRMAÇÃO. "
            "Valor ausente = não informado (nulo, nunca zero). Não somar nem comparar com o Censo.")
N_ESPERADO = 23


def sha256(caminho: Path) -> str:
    h = hashlib.sha256()
    with caminho.open("rb") as f:
        for bloco in iter(lambda: f.read(1 << 20), b""):
            h.update(bloco)
    return h.hexdigest()


def classificar(g: gpd.GeoDataFrame) -> pd.Series:
    classe = pd.Series(pd.NA, index=g.index, dtype="string")
    classe[(g.tipo_unidade == "ESF") & (g.categoria == "APS")] = "ESF"
    classe[g.cnes.isin(UBS_INTERIOR)] = "UBS"
    classe[g.cnes == CNES_UDM] = "a confirmar"
    classe[g.cnes == CNES_PRISIONAL] = "sem classe"
    return classe


def rotulo(cnes: str, nome: str) -> str:
    if cnes in ROTULO_FIXO:
        return ROTULO_FIXO[cnes]
    m = re.search(r"\d+", nome or "")
    if not m:
        raise ValueError(f"sem número no nome da unidade {cnes}: {nome}")
    return m.group(0)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--origem", type=Path, required=True, help="GeoJSON revisado (versão 4) entregue pela equipe")
    p.add_argument("--codigo-ibge", default="4322400")
    p.add_argument("--forcar", action="store_true")
    a = p.parse_args()
    if SAIDA_RAW.exists() and SAIDA_PROC.exists() and not a.forcar:
        logger.info("Já existem %s e %s — nada a fazer (use --forcar)", SAIDA_RAW.name, SAIDA_PROC.name)
        return
    if not a.origem.exists():
        raise SystemExit(f"Origem não encontrada: {a.origem}")
    soma = sha256(a.origem)
    if soma != SHA256_ESPERADO:
        raise SystemExit(f"sha256 da origem diferente do esperado: {soma}")

    g = gpd.read_file(a.origem)
    if g.crs is None:
        g = g.set_crs("EPSG:4326")  # CRS84 declarado no arquivo
    n_orig = len(g)
    g = g.drop(columns=[c for c in CAMPOS_RETIRADOS if c in g.columns])
    retirados = g[g.cnes.isin(CNES_RETIRADOS)]
    if len(retirados) != len(CNES_RETIRADOS):
        raise SystemExit(f"Registros a retirar não encontrados: {sorted(set(CNES_RETIRADOS) - set(retirados.cnes))}")
    g = g[~g.cnes.isin(CNES_RETIRADOS)].copy()
    for c in POP:
        g[c] = pd.to_numeric(g[c], errors="coerce")

    # ---------- a) arquivo completo interno
    SAIDA_RAW.parent.mkdir(parents=True, exist_ok=True)
    g.to_file(SAIDA_RAW, driver="GPKG", layer="estabelecimentos_saude_v4")
    meta_raw = {
        "fonte": CREDITO,
        "origem": {"arquivo": a.origem.name, "sha256": soma, "n_feicoes": n_orig, "crs_origem": "CRS84 (EPSG:4326)",
                   "entrega": "arquivo local entregue pela equipe do projeto (sem API)"},
        "campos_retirados": CAMPOS_RETIRADOS,
        "motivo_retirada": "caminho de pasta pessoal; não entram em nenhuma cópia no repositório",
        "registros_retirados": CNES_RETIRADOS,
        "n_feicoes": len(g),
        "campos": [c for c in g.columns if c != "geometry"],
        "nota_populacao": NOTA_POP,
        "nota_telefone": "telefone institucional mantido só neste arquivo completo interno (fora do git)",
        "crs": "EPSG:4326 (como na origem)",
        "data_processamento": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "script": "scripts/download/saude_unidades_revisadas.py",
        "tamanho_kb": round(SAIDA_RAW.stat().st_size / 1024, 1),
    }
    SAIDA_RAW.with_suffix(".json").write_text(json.dumps(meta_raw, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Arquivo completo: %s (%d feições, sem %s)", SAIDA_RAW.name, len(g), CAMPOS_RETIRADOS)

    # ---------- b) camada de trabalho das 23 unidades
    g["classe"] = classificar(g)
    u = g[g.classe.notna()].copy()
    if len(u) != N_ESPERADO:
        raise SystemExit(f"Seleção deu {len(u)} unidades, esperado {N_ESPERADO}: {sorted(u.cnes)}")
    u = u.to_crs(CRS_PADRAO)  # EPSG:31981 para distâncias e cruzamentos
    u["rotulo"] = [rotulo(c, n) for c, n in zip(u.cnes, u.nome)]
    st = gpd.read_file(RAIZ / "data" / "processed" / "dinamica_populacional" / "populacao-setores_ibge-censo_2022_setor.gpkg").to_crs(CRS_PADRAO)
    sede = st.loc[st["pop"].idxmax(), "CD_DIST"]
    j = gpd.sjoin(u[["geometry"]], st[["CD_SETOR", "SITUACAO", "CD_DIST", "NM_BAIRRO", "geometry"]], predicate="within", how="left")
    j = j[~j.index.duplicated()]
    u["setor_2022"] = j.CD_SETOR
    u["zona"] = np.where((j.SITUACAO == "Urbana") & (j.CD_DIST == sede), "urbana da sede", "interior")
    u["bairro"] = j.NM_BAIRRO
    cols = ["cnes", "nome", "classe", "rotulo", "endereco", *POP, "zona", "bairro", "setor_2022", "geometry"]
    u = u[cols].sort_values(["classe", "rotulo"]).reset_index(drop=True)

    # conferências
    limite = carregar_area_estudo().union_all()
    agua = gpd.read_file(RAIZ / "data" / "raw" / "vetor" / "hidrografia-area-agua_osm_atual_vetorial.gpkg").to_crs(CRS_PADRAO)
    vias = gpd.read_file(RAIZ / "data" / "raw" / "vetor" / "malha-viaria_osm_atual_vetorial.gpkg").to_crs(CRS_PADRAO)
    u["dentro_do_municipio"] = u.within(limite)
    u["dentro_de_area_de_agua"] = u.geometry.apply(lambda pt: bool(agua.sindex.query(pt, predicate="within").size))
    idx = vias.sindex.nearest(u.geometry, return_all=False)[1]
    u["dist_via_osm_m"] = [round(float(pt.distance(vias.geometry.iloc[i])), 1) for pt, i in zip(u.geometry, idx)]
    pares = []
    for i in range(len(u)):
        for k in range(i + 1, len(u)):
            d = u.geometry.iloc[i].distance(u.geometry.iloc[k])
            if d < 30:
                pares.append({"a": f"{u.rotulo.iloc[i]} ({u.cnes.iloc[i]})", "b": f"{u.rotulo.iloc[k]} ({u.cnes.iloc[k]})", "distancia_m": round(d, 1),
                              "endereco_a": u.endereco.iloc[i], "endereco_b": u.endereco.iloc[k]})
    # mesmo endereço (normalizado: sem "AV"/"AVENIDA"/"RUA" e sem espaços extras), com a distância entre os pontos
    norm = u.endereco.fillna("").str.upper().str.replace(r"^(AVENIDA|AV|RUA)\s+", "", regex=True).str.split().str.join(" ")
    mesmo_end = [{"a": f"{u.rotulo.iloc[i]} ({u.cnes.iloc[i]})", "b": f"{u.rotulo.iloc[k]} ({u.cnes.iloc[k]})", "endereco": norm.iloc[i],
                  "distancia_m": round(u.geometry.iloc[i].distance(u.geometry.iloc[k]), 1)}
                 for i in range(len(u)) for k in range(i + 1, len(u)) if norm.iloc[i] and norm.iloc[i] == norm.iloc[k]]
    conf = {
        "n_unidades": len(u), "por_classe": u.classe.value_counts().to_dict(), "por_zona": u.zona.value_counts().to_dict(),
        "populacao_preenchida": {c: int(u[c].notna().sum()) for c in POP},
        "populacao_soma_so_registro": {c: float(u[c].sum()) for c in POP},
        "todas_dentro_do_municipio": bool(u.dentro_do_municipio.all()),
        "unidades_em_area_de_agua": int(u.dentro_de_area_de_agua.sum()),
        "pares_a_menos_de_30m": pares,
        "pares_com_mesmo_endereco": mesmo_end,
        "coordenadas_repetidas": int(u.geometry.apply(lambda g: (round(g.x, 2), round(g.y, 2))).duplicated().sum()),
        "dist_via_osm_m": {"max": float(u.dist_via_osm_m.max()), "mediana": float(u.dist_via_osm_m.median())},
    }
    SAIDA_PROC.parent.mkdir(parents=True, exist_ok=True)
    u.to_file(SAIDA_PROC, driver="GPKG", layer="unidades_saude_esf_ubs")
    meta = {
        "produto": SAIDA_PROC.name, "fonte": CREDITO, "status": "pendente de conferência", "ligado_ao_portal": False,
        "registros_retirados_da_origem": CNES_RETIRADOS,
        "codigo_ibge": a.codigo_ibge, "crs": CRS_PADRAO, "origem_sha256": soma, "campos_retirados": CAMPOS_RETIRADOS,
        "selecao": {"ESF": "tipo_unidade ESF e categoria APS (18)", "UBS": sorted(UBS_INTERIOR),
                    "a confirmar": [CNES_UDM, "Unidade Dispensadora de Medicação (assistência farmacêutica; no arquivo como ESF)"],
                    "sem classe": [CNES_PRISIONAL, "Equipe de Saúde Prisional"],
                    "fora": "os demais estabelecimentos; o Consultório na Rua (CNES 7129440) foi retirado de todas as cópias (guardava a soma das populações)"},
        "rotulo": "número da unidade tirado do nome; UDM e Prisional para as duas sem número",
        "zona_bairro": "malha de setores 2022 (dinâmica populacional): 'urbana da sede' = setor urbano do distrito-sede; bairro = atributo do setor",
        "nota_populacao": NOTA_POP, "conferencias": conf,
        "data_processamento": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "script": "scripts/download/saude_unidades_revisadas.py",
    }
    SAIDA_PROC.with_suffix(".json").write_text(json.dumps(meta, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    logger.info("Camada de trabalho: %s (%d unidades)", SAIDA_PROC.name, len(u))
    pd.set_option("display.width", 250)
    print(u.drop(columns="geometry").to_string(index=False))
    print(json.dumps(conf, ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    main()
