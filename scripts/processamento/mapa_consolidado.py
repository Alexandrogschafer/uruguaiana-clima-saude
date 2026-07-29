"""
Gera um mapa consolidado (PNG estático + HTML interativo) da situação de
risco de inundação de Uruguaiana, reunindo as principais camadas já
processadas pelo projeto: limite municipal, setores censitários coloridos
por população estimada exposta, cotas de inundação do SGB, malha viária
principal e estabelecimentos de saúde (CNES).

Entradas (todas já geradas por scripts anteriores):
    config/area_estudo.geojson
    data/raw/vetor/setores-censitarios_ibge_2022_vetorial.gpkg
    data/processed/setores-inundacao_intersecao.gpkg
    data/raw/vetor/cotas-inundacao_sgb_atual_vetorial.gpkg
    data/raw/vetor/malha-viaria_osm_atual_vetorial.gpkg
    data/raw/vetor/saude-cnes_datasus_atual_vetorial.gpkg

Saídas:
    docs/mapa-consolidado-risco-inundacao.png   (estático, 300dpi, p/ relatório)
    docs/mapa-interativo-risco-inundacao.html   (interativo, folium, p/ exploração)

Decisões de estilo (leia antes de mudar cores/camadas)
---------------------------------------------------------
- Cota de referência para colorir os setores: 833cm (TR 1.3 anos), a mais
  frequente (ver scripts/processamento/vulnerabilidade_inundacao.py).
  Usa populacao_estimada_area-proporcional (não o método ponderado por uso
  do solo) como valor "de manchete" — é o mais simples de explicar num
  mapa de uso geral; os dois métodos e suas limitações estão documentados
  nos metadados daquele script, não repetidos aqui.
- Paleta sequencial da população exposta: YlOrRd (convenção cartográfica
  padrão para mapas de risco/exposição, luminância monotônica → seguro
  para daltonismo mesmo sem checagem de matiz). Setores fora da cota
  833cm ficam em cinza claro — não é "população zero", é "sem estimativa
  para ESSA cota".
- Paleta das cotas de inundação: a mesma rampa sequencial azul (clara →
  escura por cota crescente) já usada em docs/cotas-inundacao_sgb.png,
  para manter a mesma linguagem visual entre os dois mapas do projeto.
  Duas rampas sequenciais no mesmo mapa (azul p/ cota, laranja-vermelho
  p/ população) evita que as duas disputem o mesmo matiz.
- Cotas desenhadas como contorno + hachura (facecolor='none', hatch),
  nunca preenchimento sólido — senão escondem o choropleth de população
  por baixo delas.
- Malha viária: só highway in {trunk, primary, secondary} (+ _link) — as
  vias "principais/secundárias" do OSM. 'residential'/'unclassified'
  (a maioria das 7609 vias baixadas) poluiria o mapa num recorte
  municipal inteiro e não agrega contexto de orientação.
- Estabelecimentos de saúde: círculo cinza para os fora de qualquer cota,
  triângulo vermelho maior para os dentro de alguma cota — símbolo E cor
  diferentes (não só cor), para a informação não depender de percepção de
  cor (ver skill de dataviz do projeto).
- O mapa interativo usa uma camada-base clara (CartoDB Positron) só para
  orientação geográfica; o mapa estático não tem basemap (seguindo o
  padrão de docs/cotas-inundacao_sgb.png, que já era só vetores).

Uso:
    python scripts/processamento/mapa_consolidado.py
"""

import logging
import sys
from pathlib import Path

import branca.colormap as bcm
import folium
import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.lines import Line2D
from matplotlib.patches import Patch

sys.path.append(str(Path(__file__).resolve().parents[1] / "utils"))
from recorte_municipio import CRS_PADRAO  # noqa: E402

# Reaproveita o carregamento/correção de geometria já validado em
# vulnerabilidade_inundacao.py (mesmo diretório) em vez de duplicar a
# lógica de buffer(0) nas cotas e de CRS nos estabelecimentos de saúde.
from vulnerabilidade_inundacao import CAMINHO_CNES, carregar_cnes, carregar_cotas_dissolvidas  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

RAIZ = Path(__file__).resolve().parents[2]
CAMINHO_MUNICIPIO = RAIZ / "config" / "area_estudo.geojson"
CAMINHO_SETORES = RAIZ / "data" / "raw" / "vetor" / "setores-censitarios_ibge_2022_vetorial.gpkg"
CAMINHO_INTERSECAO = RAIZ / "data" / "processed" / "setores-inundacao_intersecao.gpkg"
CAMINHO_VIAS = RAIZ / "data" / "raw" / "vetor" / "malha-viaria_osm_atual_vetorial.gpkg"

CAMINHO_PNG = RAIZ / "docs" / "mapa-consolidado-risco-inundacao.png"
CAMINHO_HTML = RAIZ / "docs" / "mapa-interativo-risco-inundacao.html"

COTA_REFERENCIA = 833  # cota mais frequente (TR 1.3 anos) — usada para colorir os setores
COLUNA_POPULACAO = "populacao_estimada_area-proporcional"

# Vias "principais/secundárias" do OSM (highway=*) — exclui residential/
# unclassified/etc., que dominam a malha (só essas 3 classes + _link somam
# uma fração pequena das 7609 vias baixadas, o suficiente para orientação
# sem poluir o mapa).
CLASSES_VIA_PRINCIPAL = {
    "trunk", "trunk_link", "primary", "primary_link", "secondary", "secondary_link",
}

# Mesma rampa sequencial azul (clara→escura por cota crescente) de
# scripts/processamento/plot_cotas_inundacao.py — ver dataviz skill,
# references/palette.md ("Sequential hue", steps 250/400/550/700.
RAMPA_COTAS = ["#86b6ef", "#3987e5", "#1c5cab", "#0d366b"]

COR_SURFACE = "#fcfcfb"
COR_INK_PRIMARIA = "#0b0b0b"
COR_INK_SECUNDARIA = "#52514e"
COR_MUTED = "#898781"
COR_MUNICIPIO = "#c3c2b7"
COR_SEM_DADO = "#e1e0d9"
COR_SAUDE_EXPOSTA = "#d03b3b"  # status "critical" da paleta do projeto
COR_SAUDE_NAO_EXPOSTA = "#6b6a64"


def carregar_camadas() -> dict:
    """Carrega e prepara (mesmo CRS, colunas derivadas) todas as camadas do mapa."""
    for caminho, script in [
        (CAMINHO_MUNICIPIO, "scripts/download/vetor_ibge.py"),
        (CAMINHO_SETORES, "scripts/download/vulnerabilidade_censo.py"),
        (CAMINHO_INTERSECAO, "scripts/processamento/vulnerabilidade_inundacao.py"),
        (CAMINHO_VIAS, "scripts/download/infraestrutura_osm.py"),
        (CAMINHO_CNES, "scripts/download/saude_cnes.py"),
    ]:
        if not caminho.exists():
            raise FileNotFoundError(f"{caminho} não encontrado. Rode primeiro: python {script}")

    gdf_municipio = gpd.read_file(CAMINHO_MUNICIPIO)
    gdf_setores = gpd.read_file(CAMINHO_SETORES)
    gdf_intersecao = gpd.read_file(CAMINHO_INTERSECAO)
    gdf_vias = gpd.read_file(CAMINHO_VIAS)
    for gdf in (gdf_municipio, gdf_setores, gdf_intersecao, gdf_vias):
        if gdf.crs.to_string() != CRS_PADRAO:
            gdf.to_crs(CRS_PADRAO, inplace=True)

    gdf_cotas = carregar_cotas_dissolvidas()  # já em CRS_PADRAO, geometria corrigida
    gdf_cnes = carregar_cnes()  # já em CRS_PADRAO

    # Setores + população exposta na cota de referência (join à esquerda:
    # setor sem linha na interseção = não afetado por essa cota = NaN, não 0).
    populacao_833 = gdf_intersecao.loc[
        gdf_intersecao["cota_cm"] == COTA_REFERENCIA, ["CD_SETOR", COLUNA_POPULACAO]
    ]
    gdf_setores_pop = gdf_setores.merge(populacao_833, on="CD_SETOR", how="left")

    gdf_vias_principais = gdf_vias[gdf_vias["highway"].isin(CLASSES_VIA_PRINCIPAL)].copy()

    # Estabelecimento "exposto" = cai dentro de QUALQUER uma das 4 cotas
    # (união geométrica das 4, não só a cota de referência) — a pergunta
    # relevante para risco de interrupção de serviço é "inunda em algum
    # nível de cheia registrado", não só no mais frequente.
    uniao_cotas = gdf_cotas.geometry.union_all()
    gdf_cnes = gdf_cnes.copy()
    gdf_cnes["exposto_inundacao"] = gdf_cnes.geometry.within(uniao_cotas)

    logger.info(
        "Camadas carregadas: %d setores (%d com população exposta na cota %dcm), %d vias principais, "
        "%d estabelecimentos de saúde (%d expostos a alguma cota)",
        len(gdf_setores_pop), gdf_setores_pop[COLUNA_POPULACAO].notna().sum(), COTA_REFERENCIA,
        len(gdf_vias_principais), len(gdf_cnes), int(gdf_cnes["exposto_inundacao"].sum()),
    )

    return {
        "municipio": gdf_municipio,
        "setores": gdf_setores_pop,
        "cotas": gdf_cotas,
        "vias": gdf_vias_principais,
        "cnes": gdf_cnes,
    }


def desenhar_escala(ax) -> None:
    """Barra de escala simples (linha + rótulo) em metros, escolhendo um comprimento redondo. Usa o extent atual do eixo (chamar depois de set_xlim/set_ylim).

    Posicionada no canto inferior DIREITO — a legenda ocupa o inferior
    esquerdo (ver gerar_mapa_estatico).
    """
    minx, maxx = ax.get_xlim()
    miny, _ = ax.get_ylim()
    extensao = maxx - minx
    candidatos_m = [200, 500, 1000, 2000, 5000, 10000, 20000, 50000]
    comprimento = max((c for c in candidatos_m if c <= extensao * 0.3), default=candidatos_m[0])

    x1 = maxx - extensao * 0.04
    x0 = x1 - comprimento
    y0 = miny + extensao * 0.03
    ax.plot([x0, x1], [y0, y0], color=COR_INK_PRIMARIA, linewidth=2.5, solid_capstyle="butt", zorder=20)
    rotulo = f"{comprimento / 1000:.0f} km" if comprimento >= 1000 else f"{comprimento} m"
    ax.text((x0 + x1) / 2, y0, rotulo, ha="center", va="bottom", fontsize=8, color=COR_INK_PRIMARIA, zorder=20)


def desenhar_norte(ax) -> None:
    """Seta de norte simples — CRS projetado (EPSG:31981) tem eixo Y apontando para o norte geográfico."""
    ax.annotate(
        "N", xy=(0.96, 0.90), xytext=(0.96, 0.80), xycoords="axes fraction",
        ha="center", va="center", fontsize=11, fontweight="bold", color=COR_INK_PRIMARIA,
        arrowprops=dict(arrowstyle="-|>", color=COR_INK_PRIMARIA, lw=1.5),
    )


def gerar_mapa_estatico(camadas: dict) -> None:
    fig, ax = plt.subplots(figsize=(11, 10), facecolor=COR_SURFACE)
    ax.set_facecolor(COR_SURFACE)

    camadas["municipio"].boundary.plot(ax=ax, color=COR_MUNICIPIO, linewidth=1.0, zorder=1)

    camadas["setores"].plot(
        column=COLUNA_POPULACAO, cmap="YlOrRd", ax=ax, zorder=2,
        edgecolor="white", linewidth=0.25,
        missing_kwds={"color": COR_SEM_DADO, "edgecolor": "white", "linewidth": 0.25},
        legend=True,
        legend_kwds={"label": f"População estimada exposta — cota {COTA_REFERENCIA}cm (hab.)", "shrink": 0.55},
    )

    cotas_ordenadas = sorted(camadas["cotas"]["cota_cm"].unique())
    cores_cota = dict(zip(cotas_ordenadas, RAMPA_COTAS))
    for i, cota_cm in enumerate(sorted(cotas_ordenadas, reverse=True)):
        subset = camadas["cotas"][camadas["cotas"]["cota_cm"] == cota_cm]
        subset.plot(
            ax=ax, facecolor="none", edgecolor=cores_cota[cota_cm],
            hatch="///", linewidth=1.0, alpha=0.85, zorder=3 + i,
        )

    camadas["vias"].plot(ax=ax, color=COR_MUTED, linewidth=0.4, alpha=0.7, zorder=7)

    gdf_cnes = camadas["cnes"]
    gdf_cnes[~gdf_cnes["exposto_inundacao"]].plot(
        ax=ax, marker="o", color=COR_SAUDE_NAO_EXPOSTA, markersize=12,
        edgecolor="white", linewidth=0.4, zorder=8,
    )
    gdf_cnes[gdf_cnes["exposto_inundacao"]].plot(
        ax=ax, marker="^", color=COR_SAUDE_EXPOSTA, markersize=45,
        edgecolor="white", linewidth=0.5, zorder=9,
    )

    tr_por_cota = camadas["cotas"].drop_duplicates("cota_cm").set_index("cota_cm")["tr_anos"]
    legenda = [
        Patch(facecolor="none", edgecolor=cores_cota[c], hatch="///", label=f"Cota {c} cm (TR {tr_por_cota[c]:.1f} anos)")
        for c in cotas_ordenadas
    ] + [
        Patch(facecolor=COR_SEM_DADO, edgecolor="white", label=f"Setor fora da cota {COTA_REFERENCIA}cm"),
        Line2D([0], [0], marker="^", color="none", markerfacecolor=COR_SAUDE_EXPOSTA, markeredgecolor="white",
               markersize=11, label="Saúde — dentro de alguma cota"),
        Line2D([0], [0], marker="o", color="none", markerfacecolor=COR_SAUDE_NAO_EXPOSTA, markeredgecolor="white",
               markersize=8, label="Saúde — fora de todas as cotas"),
        Line2D([0], [0], color=COR_MUTED, linewidth=1.2, label="Via principal/secundária (OSM)"),
    ]
    ax.legend(
        handles=legenda, loc="lower left", frameon=True, facecolor=COR_SURFACE, edgecolor=COR_MUTED,
        labelcolor=COR_INK_PRIMARIA, fontsize=8, title="Legenda", title_fontsize=9,
    )

    fig.suptitle(
        "Risco de inundação e exposição populacional — Uruguaiana/RS",
        x=0.02, y=0.985, ha="left", fontsize=14, color=COR_INK_PRIMARIA,
    )
    fig.text(
        0.02, 0.955,
        f"Setores coloridos por população estimada exposta na cota {COTA_REFERENCIA}cm (TR "
        f"{tr_por_cota[COTA_REFERENCIA]:.1f} anos, a mais frequente) — método área-proporcional",
        fontsize=9, color=COR_INK_SECUNDARIA,
    )
    fig.text(
        0.02, 0.012,
        "Fontes: IBGE (setores censitários, Censo 2022) · SGB (cotas de inundação) · OpenStreetMap/osmnx "
        "(malha viária) · CNES/DATASUS (estabelecimentos de saúde) — CRS: EPSG:31981 (SIRGAS 2000 / UTM 21S)",
        fontsize=7.5, color=COR_MUTED,
    )

    ax.set_xlabel("Leste (m) — SIRGAS 2000 / UTM 21S", fontsize=8.5, color=COR_MUTED)
    ax.set_ylabel("Norte (m)", fontsize=8.5, color=COR_MUTED)
    ax.ticklabel_format(style="plain", useOffset=False, axis="both")
    ax.tick_params(colors=COR_MUTED, labelsize=7.5)
    plt.setp(ax.get_xticklabels(), rotation=25, ha="right")
    for spine in ax.spines.values():
        spine.set_color(COR_MUNICIPIO)
    ax.set_aspect("equal")

    # Enquadramento pela extensão das COTAS (não do município inteiro): a área
    # urbana/de risco é uma fração minúscula do município (~13km x ~13km
    # contra ~113km x ~89km do território todo — checado com
    # total_bounds), então recortar pelo limite municipal deixaria setores,
    # cotas e estabelecimentos ilegíveis, espremidos num canto do mapa
    # (mesmo critério já usado em plot_cotas_inundacao.py, com folga maior
    # aqui para caber a malha viária e os setores urbanos ao redor).
    minx, miny, maxx, maxy = camadas["cotas"].total_bounds
    folga_x, folga_y = (maxx - minx) * 0.45, (maxy - miny) * 0.45
    ax.set_xlim(minx - folga_x, maxx + folga_x)
    ax.set_ylim(miny - folga_y, maxy + folga_y)

    desenhar_escala(ax)
    desenhar_norte(ax)

    fig.tight_layout(rect=[0, 0.02, 1, 0.93])
    CAMINHO_PNG.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(CAMINHO_PNG, dpi=300, facecolor=COR_SURFACE)
    plt.close(fig)
    logger.info("Mapa estático salvo em %s", CAMINHO_PNG)


def gerar_mapa_interativo(camadas: dict) -> None:
    # folium/leaflet exigem WGS84 (lat/lon) — reprojeta só para exibição.
    municipio_wgs84 = camadas["municipio"].to_crs(epsg=4326)
    setores_wgs84 = camadas["setores"].to_crs(epsg=4326)
    cotas_wgs84 = camadas["cotas"].to_crs(epsg=4326)
    vias_wgs84 = camadas["vias"].to_crs(epsg=4326)
    cnes_wgs84 = camadas["cnes"].to_crs(epsg=4326)

    centro = municipio_wgs84.geometry.union_all().centroid
    mapa = folium.Map(location=[centro.y, centro.x], zoom_start=11, tiles="cartodbpositron")

    folium.GeoJson(
        municipio_wgs84, name="Limite municipal",
        style_function=lambda _: {"fillOpacity": 0, "color": "#898781", "weight": 2},
        tooltip="Uruguaiana/RS — limite municipal",
    ).add_to(mapa)

    valores_pop = setores_wgs84[COLUNA_POPULACAO].dropna()
    mapa_cor_pop = bcm.linear.YlOrRd_09.scale(valores_pop.min(), valores_pop.max())
    mapa_cor_pop.caption = f"População estimada exposta — cota {COTA_REFERENCIA}cm (hab.)"

    def estilo_setor(feicao: dict) -> dict:
        valor = feicao["properties"].get(COLUNA_POPULACAO)
        cor = mapa_cor_pop(valor) if valor is not None else COR_SEM_DADO
        return {"fillColor": cor, "color": "white", "weight": 0.4, "fillOpacity": 0.85}

    grupo_setores = folium.FeatureGroup(name="Setores censitários (população exposta)", show=True)
    folium.GeoJson(
        setores_wgs84[["CD_SETOR", "SITUACAO", COLUNA_POPULACAO, "geometry"]],
        style_function=estilo_setor,
        tooltip=folium.GeoJsonTooltip(
            fields=["CD_SETOR", "SITUACAO", COLUNA_POPULACAO],
            aliases=["Setor:", "Situação:", f"Pop. estimada exposta (cota {COTA_REFERENCIA}cm):"],
            localize=True,
        ),
    ).add_to(grupo_setores)
    grupo_setores.add_to(mapa)
    mapa_cor_pop.add_to(mapa)

    cotas_ordenadas = sorted(cotas_wgs84["cota_cm"].unique())
    cores_cota = dict(zip(cotas_ordenadas, RAMPA_COTAS))
    tr_por_cota = cotas_wgs84.drop_duplicates("cota_cm").set_index("cota_cm")["tr_anos"]
    for cota_cm in cotas_ordenadas:
        subset = cotas_wgs84[cotas_wgs84["cota_cm"] == cota_cm]
        grupo = folium.FeatureGroup(name=f"Cota {cota_cm}cm (TR {tr_por_cota[cota_cm]:.1f} anos)", show=True)
        folium.GeoJson(
            subset, style_function=lambda _, cor=cores_cota[cota_cm]: {
                "fillOpacity": 0.08, "fillColor": cor, "color": cor, "weight": 2,
            },
            tooltip=f"Cota {cota_cm} cm — TR {tr_por_cota[cota_cm]:.1f} anos",
        ).add_to(grupo)
        grupo.add_to(mapa)

    grupo_vias = folium.FeatureGroup(name="Malha viária principal", show=False)
    folium.GeoJson(
        vias_wgs84[["name", "highway", "geometry"]].fillna({"name": "(sem nome)"}),
        style_function=lambda _: {"color": "#6b6a64", "weight": 1.5, "opacity": 0.7},
        tooltip=folium.GeoJsonTooltip(fields=["name", "highway"], aliases=["Via:", "Classificação OSM:"]),
    ).add_to(grupo_vias)
    grupo_vias.add_to(mapa)

    grupo_saude_exposta = folium.FeatureGroup(name="Saúde — dentro de alguma cota", show=True)
    grupo_saude_fora = folium.FeatureGroup(name="Saúde — fora de todas as cotas", show=True)
    for _, linha in cnes_wgs84.iterrows():
        popup = folium.Popup(
            f"<b>{linha['nome_fantasia']}</b><br>Tipo: {linha['tipo_unidade_categoria']}<br>"
            f"{'⚠️ Dentro de alguma cota de inundação' if linha['exposto_inundacao'] else 'Fora de todas as cotas'}",
            max_width=280,
        )
        if linha["exposto_inundacao"]:
            folium.CircleMarker(
                location=[linha.geometry.y, linha.geometry.x], radius=7, color="white", weight=1,
                fill=True, fill_color=COR_SAUDE_EXPOSTA, fill_opacity=0.95, popup=popup,
            ).add_to(grupo_saude_exposta)
        else:
            folium.CircleMarker(
                location=[linha.geometry.y, linha.geometry.x], radius=4, color="white", weight=0.5,
                fill=True, fill_color=COR_SAUDE_NAO_EXPOSTA, fill_opacity=0.85, popup=popup,
            ).add_to(grupo_saude_fora)
    grupo_saude_exposta.add_to(mapa)
    grupo_saude_fora.add_to(mapa)

    folium.LayerControl(collapsed=False).add_to(mapa)

    CAMINHO_HTML.parent.mkdir(parents=True, exist_ok=True)
    mapa.save(str(CAMINHO_HTML))
    logger.info("Mapa interativo salvo em %s", CAMINHO_HTML)


def main() -> None:
    camadas = carregar_camadas()
    gerar_mapa_estatico(camadas)
    gerar_mapa_interativo(camadas)
    logger.info("Concluído.")


if __name__ == "__main__":
    main()
