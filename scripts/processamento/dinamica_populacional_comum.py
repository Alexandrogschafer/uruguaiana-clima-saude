"""
Funções comuns da caracterização da dinâmica populacional (Censos 2000,
2010 e 2022): caminhos, leitura dos agregados por setor já filtrados para o
município (com cache de extrato), leitura do histórico de formação dos
setores 2010–2022 e gravação do .json irmão de cada produto.

Regras de dado:
  - sigilo do IBGE ("X") e células sem valor viram NaN — nunca zero;
  - "-" (zero absoluto em tabelas do IBGE) vira 0.
"""

from __future__ import annotations

import json
import logging
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

sys.path.append(str(Path(__file__).resolve().parents[1] / "utils"))
from recorte_municipio import CRS_PADRAO, carregar_area_estudo  # noqa: E402,F401

logger = logging.getLogger(__name__)

RAIZ = Path(__file__).resolve().parents[2]
CODIGO_IBGE_DEFAULT = "4322400"  # Uruguaiana, RS — sempre sobrescrevível por --codigo-ibge

RAW = RAIZ / "data" / "raw"
CACHE_DP = RAW / "cache_dinamica_populacional"
CACHE_HIST = RAW / "cache_setores_historico"
EXTRATOS = CACHE_DP / "extratos"
SIDRA = CACHE_DP / "sidra"

# Produtos: tabelas/figuras/mapas (leves, versionáveis) em docs/; camadas em data/processed/
DOCS = RAIZ / "docs" / "dinamica_populacional"
TABELAS = DOCS / "tabelas"
FIGURAS = DOCS / "figuras"
MAPAS = DOCS / "mapas"
CAMADAS = RAIZ / "data" / "processed" / "dinamica_populacional"

STATUS_CONFERENCIA = "pendente de conferência"


def garantir_pastas() -> None:
    for p in (EXTRATOS, TABELAS, FIGURAS, MAPAS, CAMADAS):
        p.mkdir(parents=True, exist_ok=True)


def gravar_meta(caminho_produto: Path, **campos) -> Path:
    """Grava o .json irmão (mesmo nome, extensão .json) com fonte e transformação."""
    meta = {
        "produto": caminho_produto.name,
        "data_processamento": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        **campos,
    }
    destino = caminho_produto.with_suffix(".json")
    destino.write_text(json.dumps(meta, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return destino


def para_numero(s: pd.Series) -> pd.Series:
    """'X' (sigilo) e vazios -> NaN; '-' -> 0; vírgula decimal -> ponto."""
    s = s.astype(str).str.strip().replace({"-": "0"})
    s = s.str.replace(",", ".", regex=False)
    return pd.to_numeric(s, errors="coerce")


# ---------------------------------------------------------------- 2022
ARQ_AGREGADOS_2022 = {
    "basico": "Agregados_por_setores_basico_BR_20260520.zip",
    "demografia": "Agregados_por_setores_demografia_BR.zip",
    "caracteristicas_domicilio1": "Agregados_por_setores_caracteristicas_domicilio1_BR.zip",
}


def agregados_2022(tema: str, codigo_ibge: str, colunas: list[str] | None = None) -> pd.DataFrame:
    """Extrato municipal de um tema dos agregados por setor 2022 (valores brutos em texto)."""
    EXTRATOS.mkdir(parents=True, exist_ok=True)
    extrato = EXTRATOS / f"agregados2022_{tema}_{codigo_ibge}.csv"
    if not extrato.exists():
        z = CACHE_DP / "agregados_2022" / ARQ_AGREGADOS_2022[tema]
        if not z.exists():
            raise FileNotFoundError(f"{z} ausente — rode scripts/download/dinamica_populacional_ibge.py")
        with zipfile.ZipFile(z) as zf:
            membro = [m for m in zf.namelist() if m.lower().endswith(".csv")][0]
            partes = []
            with zf.open(membro) as f:
                for bloco in pd.read_csv(f, sep=";", dtype=str, encoding="latin1", chunksize=200_000):
                    col = [c for c in bloco.columns if c.upper() == "CD_SETOR"][0]
                    partes.append(bloco[bloco[col].str.startswith(codigo_ibge)])
        df = pd.concat(partes)
        df.to_csv(extrato, index=False)
        logger.info("Extrato 2022 '%s': %d setores -> %s", tema, len(df), extrato.name)
    df = pd.read_csv(extrato, dtype=str)
    df = df.rename(columns={c: "CD_SETOR" for c in df.columns if c.upper() == "CD_SETOR"})
    df.columns = [c if c in ("CD_SETOR",) or not c.lower().startswith("v") else c.upper() for c in df.columns]
    if colunas:
        df = df[["CD_SETOR"] + colunas]
    for c in df.columns:
        if c.startswith("V"):
            df[c + "_bruto"] = df[c]
            df[c] = para_numero(df[c])
    return df


# ---------------------------------------------------------------- 2010
def agregados_2010(planilha: str, codigo_ibge: str, colunas: list[str]) -> pd.DataFrame:
    """Planilha CSV dos agregados por setor 2010 (já extraída em cache_setores_historico)."""
    caminho = CACHE_HIST / "2010_atributos_rs" / f"{planilha}_RS.csv"
    if not caminho.exists():
        z = CACHE_HIST / "2010_atributos_rs.zip"
        with zipfile.ZipFile(z) as zf:
            membro = [m for m in zf.namelist() if m.endswith(f"/CSV/{planilha}_RS.csv")][0]
            caminho.write_bytes(zf.read(membro))
    df = pd.read_csv(caminho, sep=";", dtype=str, encoding="latin1")
    df = df[df["Cod_setor"].str.startswith(codigo_ibge)].copy()
    for c in colunas:
        df[c] = para_numero(df[c])
    return df[["Cod_setor"] + colunas].rename(columns={"Cod_setor": "CD_SETOR"})


# ---------------------------------------------------------------- 2000
def agregados_2000(planilha: str, codigo_ibge: str, colunas: list[str]) -> pd.DataFrame:
    """Planilha XLS dos agregados por setor 2000 (zip do RS em cache_setores_historico)."""
    EXTRATOS.mkdir(parents=True, exist_ok=True)
    extrato = EXTRATOS / f"agregados2000_{planilha}_{codigo_ibge}.csv"
    if not extrato.exists():
        caminho = CACHE_HIST / "2000_atributos_rs" / f"{planilha}_RS.XLS"
        if not caminho.exists():
            with zipfile.ZipFile(CACHE_HIST / "2000_atributos_rs.zip") as zf:
                membro = [m for m in zf.namelist() if m.endswith(f"/{planilha}_RS.XLS")][0]
                caminho.write_bytes(zf.read(membro))
        df = pd.read_excel(caminho, engine="xlrd", dtype=str)
        df = df[df["Cod_setor"].str.startswith(codigo_ibge)]
        df.to_csv(extrato, index=False)
    df = pd.read_csv(extrato, dtype=str)
    for c in colunas:
        df[c] = para_numero(df[c])
    return df[["Cod_setor"] + colunas].rename(columns={"Cod_setor": "CD_SETOR"})


# ---------------------------------------------------------------- histórico 2010-2022
def historico_setores(codigo_ibge: str) -> pd.DataFrame:
    """Linhas do histórico de formação dos setores 2010–2022 do município.

    Cada linha liga um setor de 2022 (GEOCODIGO_2022_DIVULGAÇÃO) a um setor de
    2010 (GEOCODIGO_2010) passando pelas malhas intermediárias. A planilha
    nacional tem ~490 mil linhas; lida em modo streaming e cacheada em extrato.
    """
    EXTRATOS.mkdir(parents=True, exist_ok=True)
    extrato = EXTRATOS / f"historico_setores_2010_2022_{codigo_ibge}.csv"
    if not extrato.exists():
        import openpyxl

        xlsx = CACHE_DP / "setores_2010_2022" / "Historico_formacao_Setores_Censitarios_2010_2022.xlsx"
        if not xlsx.exists():
            raise FileNotFoundError(f"{xlsx} ausente — rode scripts/download/dinamica_populacional_ibge.py")
        wb = openpyxl.load_workbook(xlsx, read_only=True)
        linhas, cab = [], None
        for i, row in enumerate(wb.active.iter_rows(values_only=True)):
            if i == 0:
                cab = list(row)
                continue
            if any(v is not None and str(v).startswith(codigo_ibge) for v in (row[0], row[-1])):
                linhas.append(row)
        pd.DataFrame(linhas, columns=cab).to_csv(extrato, index=False)
        logger.info("Histórico 2010–2022: %d linhas do município -> %s", len(linhas), extrato.name)
    return pd.read_csv(extrato, dtype=str)


def sidra(nome: str) -> pd.DataFrame:
    caminho = SIDRA / nome
    if not caminho.exists():
        raise FileNotFoundError(f"{caminho} ausente — rode scripts/download/dinamica_populacional_sidra.py")
    return pd.read_csv(caminho)


# ---------------------------------------------------------------- água (apoio cartográfico, rodada 03)
ARQ_AREA_AGUA = RAW / "vetor" / "hidrografia-area-agua_osm_atual_vetorial.gpkg"
ARQ_BHO = RAW / "vetor" / "rede-hidrografica_ana-bho_atual_vetorial.gpkg"
NOME_RIO_DEFAULT = "rio Uruguai"  # só rótulo de legenda; o rio é escolhido pelos dados (ver rio_principal_linha)


def rio_principal_linha(limite):
    """Eixo do curso d'água de MAIOR área de contribuição (BHO/ANA) junto ao município."""
    import geopandas as gpd

    h = gpd.read_file(ARQ_BHO, layer="curso_dagua").to_crs(CRS_PADRAO)
    h = h[h.intersects(limite.buffer(5000).union_all())]
    return h.loc[h.nuareabacc.idxmax()].geometry


def area_agua(limite, folga_m: float = 3000):
    """Polígonos de água (OSM) perto do município, com a coluna 'principal' = rio principal.

    Rio principal = polígonos water=river que o eixo BHO do rio principal atravessa.
    Só apresentação/distância à margem; nenhuma contagem usa esta camada.
    """
    import geopandas as gpd

    if not ARQ_AREA_AGUA.exists():
        raise FileNotFoundError(f"{ARQ_AREA_AGUA} ausente — rode scripts/download/hidrografia_area_agua_osm.py")
    w = gpd.read_file(ARQ_AREA_AGUA).to_crs(CRS_PADRAO)
    w = w[w.intersects(limite.buffer(folga_m).union_all())].copy()
    eixo = rio_principal_linha(limite)
    w["principal"] = (w.water == "river") & (w.geometry.intersection(eixo.buffer(50)).length > 1000)
    return w


# ---------------------------------------------------------------- regra de exibição das outras águas (rodada 04)
# O rio principal é sempre desenhado como área de água. As OUTRAS águas (açudes, lagoas,
# reservatórios) dependem do tipo de mapa:
#   "tematico"  — coropléticos (densidade, idade, variações, hexágonos de densidade): não aparecem;
#   "enderecos" — mapas de endereços e de inundação: azul bem claro, sem contorno, por baixo de
#                 pontos, vias e manchas, com área mínima (ha) pela escala do mapa;
#   "r03"       — como na rodada 03 (só para as amostras de comparação);
#   "sem"       — nenhuma outra água (só para as amostras de comparação).
AGUA_EXIBICAO = {
    "tematico": None,
    "sem": None,
    "enderecos": {"municipio": 50.0, "urbano": 5.0, "detalhe": 0.0},
    "r03": {"municipio": 20.0, "urbano": 0.0, "detalhe": 0.0},
}


def escala_do_mapa(ext) -> str:
    """'municipio' (> 30 km de largura), 'urbano' (> 8 km) ou 'detalhe'."""
    largura_km = (ext[1] - ext[0]) / 1000
    return "municipio" if largura_km > 30 else ("urbano" if largura_km > 8 else "detalhe")


def outras_aguas_visiveis(agua, ext, modo: str):
    """Polígonos de água que NÃO são o rio principal e que devem aparecer no mapa."""
    from shapely.geometry import box

    regra = AGUA_EXIBICAO[modo]
    if regra is None or agua is None:
        return agua.iloc[0:0] if agua is not None else None
    b = box(ext[0], ext[2], ext[1], ext[3])
    w = agua[~agua.principal & agua.intersects(b)]
    return w[w.geometry.area / 1e4 >= regra[escala_do_mapa(ext)]]
