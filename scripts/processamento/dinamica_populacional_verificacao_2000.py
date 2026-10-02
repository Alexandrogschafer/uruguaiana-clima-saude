"""
Tarefa 4 — verificação (sem produto de mudança) da comparabilidade dos
setores de 2000 com os de 2010 dentro do município.

Verifica:
  1. se há correspondência OFICIAL do IBGE setor 2000 <-> setor 2010 nos
     arquivos públicos disponíveis (o histórico de formação começa em 2010; o
     pacote de agregados de 2000 só traz compatibilização 2000–2001 de
     municípios novos);
  2. qualidade da malha 2000 já no repositório: CRS de origem, cobertura do
     território municipal, sobreposições/lacunas;
  3. encaixe com a malha 2010: códigos em comum e, só como DIAGNÓSTICO de
     qualidade (não para medir mudança), concordância geométrica dos setores
     com o mesmo código.
A comparação 2000–2010 por área comparável só seria produzida se houvesse
correspondência oficial e encaixe adequado; caso contrário, 2000 fica nos
níveis município e distrito (Tarefa 1).

Uso:
  python scripts/processamento/dinamica_populacional_verificacao_2000.py --codigo-ibge 4322400
"""

from __future__ import annotations

import argparse
import json
import zipfile

import geopandas as gpd
import numpy as np
import pandas as pd

import dinamica_populacional_comum as c


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--codigo-ibge", default=c.CODIGO_IBGE_DEFAULT)
    a = p.parse_args()
    cod = a.codigo_ibge
    c.garantir_pastas()
    area = c.carregar_area_estudo()
    a_mun = float(area.area.sum())

    m00 = gpd.read_file(c.RAW / "vetor" / "setores-censitarios_ibge_2000_vetorial.gpkg").to_crs(c.CRS_PADRAO)
    m10 = gpd.read_file(c.RAW / "vetor" / "setores-censitarios_ibge_2010_vetorial.gpkg").to_crs(c.CRS_PADRAO)
    meta00 = json.loads((c.RAW / "vetor" / "setores-censitarios_ibge_2000_vetorial.json").read_text(encoding="utf-8"))

    # 1. correspondência oficial
    hist = c.historico_setores(cod)
    cols_anos = [col for col in hist.columns if col.startswith("GEOCODIGO_")]
    with zipfile.ZipFile(c.CACHE_HIST / "2000_atributos_rs.zip") as zf:
        compat = [n for n in zf.namelist() if "Compatibiliza" in n]
        df_comp = pd.read_excel(zf.open(compat[0]), dtype=str) if compat else pd.DataFrame()
    comp_mun = int(df_comp.apply(lambda col: col.astype(str).str.contains(cod[:6])).any(axis=1).sum()) if len(df_comp) else 0

    # 2. qualidade da malha 2000
    def cobertura(m):
        invalidas = int((~m.is_valid).sum())
        m = m.assign(geometry=m.geometry.make_valid())  # só para medir cobertura; contagem de inválidas registrada antes
        u = m.union_all()
        return {"n_setores": len(m), "area_uniao_km2": u.area / 1e6, "area_municipio_km2": a_mun / 1e6,
                "pct_municipio_coberto": 100 * u.intersection(area.union_all()).area / a_mun,
                "area_fora_do_municipio_km2": u.difference(area.union_all()).area / 1e6,
                "soma_areas_menos_uniao_km2 (sobreposição interna)": (m.area.sum() - u.area) / 1e6,
                "geometrias_invalidas_na_fonte": invalidas}
    q00, q10 = cobertura(m00), cobertura(m10)

    # 3. encaixe com 2010 (diagnóstico)
    comuns = sorted(set(m00.cd_setor) & set(m10.cd_setor))
    a00 = m00.set_index("cd_setor").geometry
    a10 = m10.set_index("cd_setor").geometry
    iou = []
    for s in comuns:
        g0, g1 = a00[s].buffer(0), a10[s].buffer(0)  # buffer(0) corrige anel inválido
        iou.append(g0.intersection(g1).area / g0.union(g1).area if g0.union(g1).area > 0 else np.nan)
    iou = pd.Series(iou, index=comuns)
    # deslocamento sistemático entre as malhas: desvio dos centróides dos setores de mesmo código
    d = pd.DataFrame({"dx": [a10[s].centroid.x - a00[s].centroid.x for s in comuns],
                      "dy": [a10[s].centroid.y - a00[s].centroid.y for s in comuns]}, index=comuns)
    urb00 = m00[m00.situacao == "Urbana"]
    enc = {"codigos_2000": len(m00), "codigos_2010": len(m10), "codigos_em_comum": len(comuns),
           "nota_codigos": "código igual NÃO garante mesmo território: até 2010 o IBGE reaproveitava o código de um setor dividido para uma das partes",
           "iou_setores_mesmo_codigo": {"mediana": float(iou.median()), "p10": float(iou.quantile(0.1)),
                                        "n_iou_maior_0_9": int((iou > 0.9).sum()), "n_iou_menor_0_5": int((iou < 0.5).sum())},
           "deslocamento_centroide_mesmo_codigo_m": {"mediana_dx": float(d.dx.median()), "mediana_dy": float(d.dy.median()),
                                                     "mediana_distancia": float(np.hypot(d.dx, d.dy).median())},
           "setores_urbanos_2000": len(urb00)}
    resultado = {
        "codigo_ibge": cod, "script": "scripts/processamento/dinamica_populacional_verificacao_2000.py",
        "correspondencia_oficial_2000_2010": {
            "historico_formacao_2010_2022_colunas_de_ano": [col.replace("GEOCODIGO_", "") for col in cols_anos],
            "historico_cobre_2000": any("2000" in col for col in cols_anos),
            "arquivo_compatibilizacao_no_pacote_2000": compat, "linhas_do_municipio_na_compatibilizacao": comp_mun,
            "ftp_censo_2010_malhas": "só setores_censitarios_shp/ e _kmz/ (sem tabela de correspondência)",
            "conclusao": "não foi encontrada correspondência oficial setor 2000 <-> setor 2010 publicada pelo IBGE",
        },
        "malha_2000": {"crs_origem": meta00.get("crs_origem_e_tratamento"), **q00},
        "malha_2010": q10,
        "encaixe_2000_2010_diagnostico": enc,
    }
    out = c.TABELAS / "verificacao-setores-2000_ibge-censo_2000-2010_setor.json"
    out.write_text(json.dumps(resultado, ensure_ascii=False, indent=2, default=float), encoding="utf-8")
    print(json.dumps(resultado, ensure_ascii=False, indent=1, default=float))


if __name__ == "__main__":
    main()
