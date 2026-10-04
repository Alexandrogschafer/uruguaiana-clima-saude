"""Vetorização de mapa publicado só como figura (página de PDF): extrai classes de cor como polígonos.

Passos: (1) renderiza a página do PDF na resolução dos parâmetros, ou extrai a imagem embutida na página,
sem reamostrar ("origem": "imagem_embutida"); (2) georreferencia por ajuste afim
(mínimos quadrados) nas marcas da grade de coordenadas medidas na imagem; (3) classifica os pixels pela
distância às cores das classes, lidas na legenda do próprio mapa; (4) limpa (fecha traços finos, tapa
buracos pequenos, remove ilhas); (5) grava polígonos em GeoPackage, com metadado .json irmão e uma imagem
de conferência (figura de origem + contornos extraídos).

Tudo o que é específico de um mapa fica no arquivo de parâmetros (.json): página, resolução, moldura,
marcas da grade, cores, limpeza, textos de fonte e status. O script não tem nome de lugar nem de fonte.

O produto é DERIVADO DE FIGURA: a precisão é a da figura, não a do dado original.

Uso:
    python extrair_classes_de_figura.py --parametros PARAMETROS.json --pdf DOCUMENTO.pdf --saida-dir PASTA
"""
import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

import geopandas as gpd
import numpy as np
from affine import Affine
from PIL import Image
from rasterio import features
from scipy import ndimage as ndi
from shapely.geometry import Polygon, shape
from shapely.ops import unary_union

Image.MAX_IMAGE_PIXELS = None


def sha256(caminho: Path) -> str:
    h = hashlib.sha256()
    with open(caminho, "rb") as f:
        for bloco in iter(lambda: f.read(1 << 20), b""):
            h.update(bloco)
    return h.hexdigest()


def renderizar(pdf: Path, pagina: int, dpi: int, renderizador: str):
    """Devolve (imagem RGB, nome do renderizador). 'auto' usa pdftoppm se existir; senão, pypdfium2."""
    if renderizador in ("auto", "pdftoppm") and shutil.which("pdftoppm"):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "pagina"
            subprocess.run(["pdftoppm", "-png", "-r", str(dpi), "-f", str(pagina), "-l", str(pagina),
                            "-singlefile", str(pdf), str(base)], check=True)
            img = Image.open(base.with_suffix(".png")).convert("RGB")
            img.load()
        versao = subprocess.run(["pdftoppm", "-v"], capture_output=True, text=True).stderr.splitlines()[0]
        return img, versao.strip()
    if renderizador == "pdftoppm":
        sys.exit("pdftoppm não encontrado")
    import pypdfium2 as pdfium                      # só é necessário sem o pdftoppm
    doc = pdfium.PdfDocument(str(pdf))
    img = doc[pagina - 1].render(scale=dpi / 72).to_pil().convert("RGB")
    return img, "pypdfium2 " + str(pdfium.PYPDFIUM_INFO)


def imagem_embutida(pdf: Path, pagina: int, indice: int, renderizador: str):
    """Devolve a imagem de índice 'indice' (ordem na página, começando em 0) tal como está no PDF."""
    if renderizador in ("auto", "pdftoppm") and shutil.which("pdfimages"):
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["pdfimages", "-png", "-f", str(pagina), "-l", str(pagina), str(pdf),
                            str(Path(tmp) / "im")], check=True)
            arqs = sorted(Path(tmp).glob("im-*.png"))
            img = Image.open(arqs[indice]).convert("RGB")
            img.load()
        versao = subprocess.run(["pdfimages", "-v"], capture_output=True, text=True).stderr.splitlines()[0]
        return img, versao.strip()
    import pypdfium2 as pdfium
    import pypdfium2.raw as raw
    objs = [o for o in pdfium.PdfDocument(str(pdf))[pagina - 1].get_objects(filter=[raw.FPDF_PAGEOBJ_IMAGE], max_depth=3)]
    return objs[indice].get_bitmap(render=False).to_pil().convert("RGB"), "pypdfium2 " + str(pdfium.PYPDFIUM_INFO)


def ajuste_afim(marcas_e, marcas_n):
    """E = a·x + b·y + c e N = d·x + e·y + f, cada um ajustado nas suas marcas (x, y em pixels da página)."""
    A = np.array([[x, y, 1.0] for x, y, _ in marcas_e]); e = np.array([v for *_, v in marcas_e], float)
    B = np.array([[x, y, 1.0] for x, y, _ in marcas_n]); n = np.array([v for *_, v in marcas_n], float)
    ce, *_ = np.linalg.lstsq(A, e, rcond=None)
    cn, *_ = np.linalg.lstsq(B, n, rcond=None)
    # as marcas foram medidas no índice do pixel (centro); o raster usa o canto do pixel, que fica meio
    # pixel ANTES do centro: o canto da coluna c corresponde ao índice c - 0,5
    T = Affine(ce[0], ce[1], ce[2], cn[0], cn[1], cn[2]) * Affine.translation(-0.5, -0.5)
    return T, ce, cn, A @ ce - e, B @ cn - n


def limpar(m, lim, area_px, estrut):
    if lim.get("fechamento_iteracoes", 0):
        m = ndi.binary_closing(m, structure=estrut, iterations=int(lim["fechamento_iteracoes"]))
    if lim.get("buraco_min_m2", 0):                 # tapa buracos pequenos (símbolos, texto)
        rot, n = ndi.label(~m)
        tam = ndi.sum(~m, rot, range(1, n + 1))
        m = m | np.isin(rot, 1 + np.where(tam * area_px < lim["buraco_min_m2"])[0])
    if lim.get("ilha_min_m2", 0):                   # remove ilhas minúsculas
        rot, n = ndi.label(m)
        tam = ndi.sum(m, rot, range(1, n + 1))
        m = np.isin(rot, 1 + np.where(tam * area_px >= lim["ilha_min_m2"])[0])
    return m


def poligonos(m, transform, simplificacao):
    geoms = [shape(g) for g, v in features.shapes(m.astype(np.uint8), mask=m, transform=transform) if v == 1]
    g = unary_union(geoms)
    if simplificacao:
        g = g.simplify(simplificacao)
    return g.buffer(0)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--parametros", required=True, type=Path)
    ap.add_argument("--pdf", required=True, type=Path)
    ap.add_argument("--saida-dir", required=True, type=Path)
    ap.add_argument("--renderizador", default="auto", choices=["auto", "pdftoppm", "pypdfium2"])
    ap.add_argument("--forcar", action="store_true", help="regrava mesmo que a saída já exista")
    a = ap.parse_args()

    p = json.loads(a.parametros.read_text(encoding="utf-8"))
    doc = p["documento"]
    saida = a.saida_dir / (p["nome_saida"] + ".gpkg")
    if saida.exists() and not a.forcar:
        print("já existe (use --forcar para regravar):", saida)
        return
    soma = sha256(a.pdf)
    if doc.get("sha256") and soma != doc["sha256"]:
        sys.exit("o PDF não é o dos parâmetros: sha256 %s, esperado %s" % (soma, doc["sha256"]))

    if doc.get("origem", "pagina") == "imagem_embutida":
        img, render = imagem_embutida(a.pdf, doc["pagina"], doc.get("indice_imagem", 0), a.renderizador)
    else:
        img, render = renderizar(a.pdf, doc["pagina"], doc["dpi"], a.renderizador)
    if doc.get("dimensao_px") and list(img.size) != list(doc["dimensao_px"]):
        sys.exit("imagem com %s px; as marcas foram medidas em %s px" % (img.size, doc["dimensao_px"]))
    if doc.get("rotacao_horaria_graus"):               # mapa deitado na página: gira antes de medir e classificar
        img = img.rotate(-int(doc["rotacao_horaria_graus"]), expand=True)
    px = np.asarray(img).astype(np.int16)

    T, ce, cn, res_e, res_n = ajuste_afim(p["marcas_e"], p["marcas_n"])
    area_px = abs(ce[0] * cn[1] - ce[1] * cn[0])
    q = p["moldura_px"]; X0, X1, Y0, Y1 = q["x0"], q["x1"], q["y0"], q["y1"]
    sub = px[Y0:Y1, X0:X1]
    Tsub = T * Affine.translation(X0, Y0)
    print("renderizador:", render)
    print("pixel (m): x = %.3f, y = %.3f | resíduo máximo (m): E %.1f, N %.1f"
          % (ce[0], -cn[1], np.abs(res_e).max(), np.abs(res_n).max()))

    classes = p["classes"]                           # na ordem do arquivo (em modo cumulativo: da menor para a maior)
    # classes por COR (distância à cor da legenda) e classes por REGRA (hachuras: diferença entre dois canais
    # da imagem suavizada, que separa um padrão fino colorido do fundo branco). A cor tem precedência.
    estrut = ndi.generate_binary_structure(2, 2)
    i_cor = [i for i, c in enumerate(classes) if "cor" in c]
    cls = np.zeros(sub.shape[:2], np.uint8)
    if i_cor:
        dist = np.stack([np.sqrt(((sub - np.array(classes[i]["cor"])) ** 2).sum(axis=2)) for i in i_cor])
        cls = np.where(dist.min(axis=0) <= p["tolerancia_cor"], np.array(i_cor)[dist.argmin(axis=0)] + 1, 0).astype(np.uint8)
    for i, c in enumerate(classes):
        if "regra" not in c:
            continue
        rg = c["regra"]
        suave = [ndi.uniform_filter(sub[..., k].astype(float), size=rg.get("suavizacao_px", 9)) for k in rg["canais"]]
        dif = suave[0] - suave[1]
        mr = (dif >= rg["min"]) & (dif <= rg.get("max", 255)) & (cls == 0)
        if rg.get("fechamento_px", 0):               # emenda a hachura cortada por linhas de outra cor
            r_fe = int(rg["fechamento_px"])
            yy, xx = np.ogrid[-r_fe:r_fe + 1, -r_fe:r_fe + 1]
            mr = ndi.binary_closing(np.pad(mr, r_fe), structure=(xx * xx + yy * yy) <= r_fe * r_fe)[r_fe:-r_fe, r_fe:-r_fe]
            mr &= (cls == 0)
        if rg.get("abertura_iteracoes", 0):          # tira faixas finas (linhas coloridas isoladas no fundo branco)
            r_ab = int(rg["abertura_iteracoes"])     # elemento circular, para não deixar cantos retos na borda
            yy, xx = np.ogrid[-r_ab:r_ab + 1, -r_ab:r_ab + 1]
            mr = ndi.binary_opening(mr, structure=(xx * xx + yy * yy) <= r_ab * r_ab)
        if rg.get("erosao_px", 0):                   # compensa o halo da suavização (cerca de metade da janela)
            r_er = int(rg["erosao_px"])
            yy, xx = np.ogrid[-r_er:r_er + 1, -r_er:r_er + 1]
            mr = ndi.binary_erosion(mr, structure=(xx * xx + yy * yy) <= r_er * r_er)
        cls[mr & (cls == 0)] = i + 1
    fora = np.zeros(cls.shape, bool)                 # caixas de legenda, encartes: retângulos sem dado (px da imagem)
    for ex0, ey0, ex1, ey1 in p.get("excluir_px", []):
        fora[max(ey0 - Y0, 0):max(ey1 - Y0, 0), max(ex0 - X0, 0):max(ex1 - X0, 0)] = True
    cls[fora] = 0

    lim = p.get("limpeza", {})
    atr, base, modo = p["atributo_classe"], p.get("atributos", {}), p.get("modo", "categorico")
    nomes = p.get("camadas", {})
    regs, anterior = [], None
    for i, c in enumerate(classes):
        m = ((cls >= 1) & (cls <= i + 1)) if modo == "cumulativo" else (cls == i + 1)
        m = limpar(m, lim, area_px, estrut) & ~fora
        if modo == "cumulativo" and anterior is not None:
            m = m | anterior                         # garante o aninhamento
        anterior = m
        g = poligonos(m, Tsub, lim.get("simplificacao_m", 0))
        if modo == "cumulativo" and regs:            # a simplificação é feita polígono a polígono e pode tirar
            g = g.union(regs[-1]["geometry"]).buffer(0)   # a classe anterior de dentro da seguinte: repõe o aninhamento
        r = {atr: c["valor"], "area_km2": round(g.area / 1e6, 3), **base, "geometry": g}
        if c.get("descricao") or p.get("descricao_modelo"):
            r["descricao"] = c.get("descricao") or p["descricao_modelo"].format(valor=c["valor"])
        regs.append(r)

    a.saida_dir.mkdir(parents=True, exist_ok=True)
    if saida.exists():
        saida.unlink()
    epsg = p["epsg"]
    principal = nomes.get("principal", "classes_cumulativas" if modo == "cumulativo" else "classes")
    gdf = gpd.GeoDataFrame(regs, crs=epsg)
    gdf.to_file(saida, layer=principal, driver="GPKG")
    if modo == "cumulativo":                         # faixas sem sobreposição: o que cada classe acrescenta
        fx = []
        for k, r in enumerate(regs):
            g = r["geometry"] if k == 0 else r["geometry"].difference(regs[k - 1]["geometry"])
            fx.append({atr: r[atr], "area_km2": round(g.area / 1e6, 3), **base, "geometry": g})
        gpd.GeoDataFrame(fx, crs=epsg).to_file(saida, layer=nomes.get("faixas", "faixas"), driver="GPKG")
    cantos = [Tsub * (0, 0), Tsub * (X1 - X0, 0), Tsub * (X1 - X0, Y1 - Y0), Tsub * (0, Y1 - Y0)]
    quadro = Polygon(cantos)
    for ex0, ey0, ex1, ey1 in p.get("excluir_px", []):
        quadro = quadro.difference(Polygon([Tsub * (ex0 - X0, ey0 - Y0), Tsub * (ex1 - X0, ey0 - Y0),
                                            Tsub * (ex1 - X0, ey1 - Y0), Tsub * (ex0 - X0, ey1 - Y0)]))
    gpd.GeoDataFrame([{"descricao": "quadro do mapa de origem (fora dele não há dado)", **base, "geometry": quadro}],
                     crs=epsg).to_file(saida, layer=nomes.get("quadro", "quadro_do_mapa"), driver="GPKG")

    meta = dict(base, epsg=epsg, parametros=a.parametros.name, documento=dict(doc, sha256=soma),
                renderizador=render, modo=modo, atributo_classe=atr,
                pixel_m=[round(float(ce[0]), 3), round(float(-cn[1]), 3)],
                residuos_e_m=[round(float(v), 1) for v in res_e], residuos_n_m=[round(float(v), 1) for v in res_n],
                quadro_e_n=[round(v) for v in quadro.bounds],
                pixels_por_classe={str(c["valor"]): int((cls == i + 1).sum()) for i, c in enumerate(classes)},
                areas_km2={str(r[atr]): r["area_km2"] for r in regs},
                camadas=[principal] + ([nomes.get("faixas", "faixas")] if modo == "cumulativo" else [])
                + [nomes.get("quadro", "quadro_do_mapa")],
                data_extracao=date.today().isoformat())
    saida.with_suffix(".json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    # imagem de conferência: a figura de origem, na sua grade, com os contornos extraídos por cima
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(11, 11 * (Y1 - Y0) * abs(cn[1]) / ((X1 - X0) * ce[0])))
    x_a, y_a = Tsub * (0, 0); x_b, y_b = Tsub * (X1 - X0, Y1 - Y0)
    ax.imshow(sub.astype(np.uint8), extent=(x_a, x_b, y_b, y_a))
    cores = ["#ff00ff", "#ffff00", "#ff0000"]
    mostrar = sorted({0, len(regs) // 2, len(regs) - 1})
    for cor, k in zip(cores, mostrar):
        gpd.GeoSeries([regs[k]["geometry"]], crs=epsg).boundary.plot(ax=ax, color=cor, linewidth=0.5)
    ax.set_title("figura de origem + contornos extraídos (%s: %s)" % (
        atr, ", ".join("%s = %s" % (regs[k][atr], n) for k, n in zip(mostrar, ["magenta", "amarelo", "vermelho"]))),
        fontsize=9)
    ax.ticklabel_format(style="plain"); ax.tick_params(labelsize=7)
    fig.savefig(saida.with_name(p["nome_saida"] + "_conferencia.png"), dpi=150, bbox_inches="tight")

    print(gdf[[atr, "area_km2"]].to_string(index=False))
    print("gravado:", saida)


if __name__ == "__main__":
    main()
