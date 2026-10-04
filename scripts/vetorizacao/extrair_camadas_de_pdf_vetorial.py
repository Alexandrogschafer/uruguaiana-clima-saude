#!/usr/bin/env python3
"""Extrai camadas de um PDF vetorial exportado de CAD e grava GeoPackage, GeoTIFF e metadados.

Serve para plantas que só existem em PDF mas guardam o desenho original: traços vetoriais
separados por camada (conteúdo opcional do PDF). Nada passa por imagem: as geometrias saem
dos próprios traços. Tudo o que é específico da planta (camadas, cores, rótulos de cota lidos,
transformação para coordenadas) fica no arquivo de parâmetros.

Produtos (definidos nos parâmetros):
  - "vetor":  camadas de linhas, de polígonos (com filtro pelo que a planta desenha dentro
              do contorno), de hachura convertida em polígono, de
              preenchimentos unidos, de símbolos reduzidos a ponto, de textos do PDF e de
              pontos lidos na planta; várias entradas com o mesmo nome formam uma camada só;
  - "curvas": curvas de nível com cota (mestras pelos rótulos lidos; intermediárias pela
              contagem de curvas entre duas mestras vizinhas) e, se pedido, modelo de terreno.

Requer: pikepdf, numpy, scipy, shapely (2 ou mais novo), geopandas, rasterio e matplotlib.

Uso:
  python extrair_camadas_de_pdf_vetorial.py --parametros P.json --pdf PLANTA.pdf --saida-dir DIR
  python extrair_camadas_de_pdf_vetorial.py --parametros P.json --pdf PLANTA.pdf \
         --conferir-georef VIAS.geojson        (só mede o ajuste contra uma malha de ruas)
"""
import argparse, collections, datetime, hashlib, json, re, sys
from pathlib import Path

import numpy as np
import geopandas as gpd
import pandas as pd
import pikepdf
import shapely
from scipy.spatial import cKDTree
from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import polygonize, unary_union
from shapely.strtree import STRtree

PINTURA = {"S": "traco", "s": "traco", "f": "fill", "F": "fill", "f*": "fill",
           "b": "traco+fill", "b*": "traco+fill", "B": "traco+fill", "B*": "traco+fill", "n": None}


# ---------------------------------------------------------------- leitura do PDF
def _bezier(p0, p1, p2, p3, n=8):
    t = np.linspace(0, 1, n + 1)[1:, None]
    P = np.array([p0, p1, p2, p3], float)
    return ((1 - t) ** 3 * P[0] + 3 * (1 - t) ** 2 * t * P[1] + 3 * (1 - t) * t ** 2 * P[2] + t ** 3 * P[3]).tolist()


def ler_pdf(caminho, pagina=1):
    """Devolve a lista de polilinhas da página, em pontos do PDF (origem embaixo, à esquerda).

    Cada item: camada, cor (traço), cor_fill, largura (pt), pintura, pts (N x 2), fechado.
    """
    pdf = pikepdf.open(caminho)
    pg = pdf.pages[pagina - 1]
    props = {str(k): str(v.get("/Name")) for k, v in (pg.Resources.get("/Properties") or {}).items()}
    I = np.eye(3)
    ctm, pilha = I.copy(), []
    est = {"cor": (0.0, 0.0, 0.0), "fill": (0.0, 0.0, 0.0), "w": 1.0}
    camadas = []                      # pilha de conteúdo marcado
    subs, cur, ini = [], [], None
    saida = []

    def aplica(x, y):
        return (ctm[0, 0] * x + ctm[1, 0] * y + ctm[2, 0], ctm[0, 1] * x + ctm[1, 1] * y + ctm[2, 1])

    def fecha_sub(fechar=False):
        nonlocal cur
        if len(cur) > 1:
            if fechar and cur[0] != cur[-1]:
                cur.append(cur[0])
            subs.append((cur, fechar))
        cur = []

    for operandos, op in pikepdf.parse_content_stream(pg):
        o = str(op)
        if o == "q":
            pilha.append((ctm.copy(), dict(est)))
        elif o == "Q":
            if pilha:
                ctm, est = pilha.pop()
        elif o == "cm":
            a, b, c, d, e, f = [float(v) for v in operandos]
            ctm = np.array([[a, b, 0], [c, d, 0], [e, f, 1]]) @ ctm
        elif o == "w":
            est["w"] = float(operandos[0])
        elif o == "RG":
            est["cor"] = tuple(round(float(v), 2) for v in operandos)
        elif o == "rg":
            est["fill"] = tuple(round(float(v), 2) for v in operandos)
        elif o == "G":
            est["cor"] = (round(float(operandos[0]), 2),) * 3
        elif o == "g":
            est["fill"] = (round(float(operandos[0]), 2),) * 3
        elif o == "BDC":
            nome = props.get(str(operandos[1]), "") if str(operandos[0]) == "/OC" else None
            camadas.append(nome)
        elif o == "BMC":
            camadas.append(None)
        elif o == "EMC":
            if camadas:
                camadas.pop()
        elif o == "m":
            fecha_sub()
            ini = aplica(float(operandos[0]), float(operandos[1]))
            cur = [ini]
        elif o == "l":
            cur.append(aplica(float(operandos[0]), float(operandos[1])))
        elif o in ("c", "v", "y"):
            v = [float(x) for x in operandos]
            p0 = cur[-1] if cur else aplica(v[0], v[1])
            if o == "c":
                p1, p2, p3 = aplica(v[0], v[1]), aplica(v[2], v[3]), aplica(v[4], v[5])
            elif o == "v":
                p1, p2, p3 = p0, aplica(v[0], v[1]), aplica(v[2], v[3])
            else:
                p1, p3 = aplica(v[0], v[1]), aplica(v[2], v[3])
                p2 = p3
            cur += [tuple(p) for p in _bezier(p0, p1, p2, p3)]
        elif o == "h":
            fecha_sub(fechar=True)
        elif o == "re":
            fecha_sub()
            x, y, w, h = [float(v) for v in operandos]
            subs.append(([aplica(x, y), aplica(x + w, y), aplica(x + w, y + h), aplica(x, y + h), aplica(x, y)], True))
        elif o in PINTURA:
            fecha_sub(fechar=o in ("s", "b", "b*"))
            pint = PINTURA[o]
            if pint:
                camada = next((c for c in reversed(camadas) if c is not None), "")
                esc = float(np.sqrt(abs(ctm[0, 0] * ctm[1, 1] - ctm[0, 1] * ctm[1, 0])))
                for pts, fechado in subs:
                    saida.append({"camada": camada,
                                  "cor": est["cor"] if "traco" in pint else None,
                                  "fill": est["fill"] if "fill" in pint else None,
                                  "largura": round(est["w"] * esc, 3), "pintura": pint,
                                  "pts": np.array(pts, float), "fechado": fechado})
            subs = []
    return saida


def _arial(b):
    """Texto de fonte Arial gravada por índice de glifo (Type0, Identity-H, sem tabela de Unicode).

    A ordem dos glifos é a padrão do Macintosh, sem "espaço não separável" e sem o símbolo da maçã.
    """
    s = ""
    for i in range(0, len(b) - 1, 2):
        g = b[i] * 256 + b[i + 1]
        if g >= 209:
            g += 2
        elif g >= 172:
            g += 1
        if g <= 97:
            s += chr(g + 29)
        elif g + 30 < 256:
            s += bytes([g + 30]).decode("mac_roman")
        else:
            s += "?"
    return s


def ler_textos(caminho, pagina=1):
    """Textos da página gravados como texto (não os desenhados como contorno): camada, texto, posição
    em pontos do PDF, ângulo e altura. Texto em fonte por índice de glifo só é lido se a fonte for Arial."""
    pdf = pikepdf.open(caminho)
    pg = pdf.pages[pagina - 1]
    props = {str(k): str(v.get("/Name")) for k, v in (pg.Resources.get("/Properties") or {}).items()}
    fontes = {str(k): (str(f.get("/Subtype")), str(f.get("/BaseFont"))) for k, f in (pg.Resources.get("/Font") or {}).items()}
    I = np.eye(3)
    ctm, pilha, camadas, saida = I.copy(), [], [], []
    fonte, tam, tm = None, 1.0, I.copy()
    for a, op in pikepdf.parse_content_stream(pg):
        o = str(op)
        if o == "q":
            pilha.append(ctm.copy())
        elif o == "Q":
            if pilha:
                ctm = pilha.pop()
        elif o == "cm":
            v = [float(x) for x in a]
            ctm = np.array([[v[0], v[1], 0], [v[2], v[3], 0], [v[4], v[5], 1]]) @ ctm
        elif o == "BDC":
            camadas.append(props.get(str(a[1]), "") if str(a[0]) == "/OC" else None)
        elif o == "BMC":
            camadas.append(None)
        elif o == "EMC":
            if camadas:
                camadas.pop()
        elif o == "Tf":
            fonte, tam = str(a[0]), float(a[1])
        elif o == "BT":
            tm = I.copy()
        elif o == "Tm":
            v = [float(x) for x in a]
            tm = np.array([[v[0], v[1], 0], [v[2], v[3], 0], [v[4], v[5], 1]])
        elif o == "Td":
            v = [float(x) for x in a]
            tm = np.array([[1, 0, 0], [0, 1, 0], [v[0], v[1], 1]]) @ tm
        elif o in ("Tj", "TJ"):
            b = bytes(a[0]) if o == "Tj" else b"".join(bytes(x) for x in a[0] if isinstance(x, pikepdf.String))
            sub, base = fontes.get(fonte, ("", ""))
            if sub == "/Type0":
                if "Arial" not in base:
                    continue
                txt = _arial(b)
            else:
                txt = b.decode("cp1252", errors="replace")
            m = tm @ ctm
            saida.append({"camada": next((c for c in reversed(camadas) if c is not None), ""), "texto": txt,
                          "x": float(m[2, 0]), "y": float(m[2, 1]),
                          "angulo": float(np.degrees(np.arctan2(m[0, 1], m[0, 0]))),
                          "altura_pt": tam * float(np.hypot(m[0, 0], m[0, 1]))})
    return saida


def cor_igual(c, alvo, tol=0.03):
    return c is not None and all(abs(a - b) <= tol for a, b in zip(c, alvo))


def selecionar(pl, f):
    """Filtra polilinhas por camada, cor do traço, cor do preenchimento e largura."""
    cam = f["camada_cad"] if isinstance(f["camada_cad"], list) else [f["camada_cad"]]
    out = []
    for p in pl:
        if p["camada"] not in cam:
            continue
        if "cor" in f and not cor_igual(p["cor"], f["cor"]):
            continue
        if "fill" in f and not cor_igual(p["fill"], f["fill"]):
            continue
        if "largura_min_pt" in f and not (p["largura"] >= f["largura_min_pt"]):
            continue
        if "largura_max_pt" in f and not (p["largura"] <= f["largura_max_pt"]):
            continue
        if "pintura" in f and p["pintura"] != f["pintura"]:
            continue
        out.append(p)
    return out


# ---------------------------------------------------------------- georreferenciamento
def transformador(g):
    """Semelhança (4 parâmetros) de pontos do PDF para a projeção: E = a.x - b.y + tx; N = b.x + a.y + ty."""
    a, b, tx, ty = g["a"], g["b"], g["tx"], g["ty"]
    return lambda pts: np.c_[a * pts[:, 0] - b * pts[:, 1] + tx, b * pts[:, 0] + a * pts[:, 1] + ty]


def conferir_georef(pl, par, vias, epsg):
    """Casa o centro das quadras do PDF com as quadras formadas pela malha de ruas e mede o resíduo."""
    f = par["georreferenciamento"]["conferencia"]
    tr = transformador(par["georreferenciamento"])
    qs = [Polygon(tr(p["pts"])) for p in selecionar(pl, f["quadras"]) if len(p["pts"]) >= 4]
    qs = [q for q in qs if q.is_valid and q.area > f.get("area_min_m2", 1000)]
    c_pdf = np.array([[q.centroid.x, q.centroid.y] for q in qs])
    v = gpd.read_file(vias).to_crs(epsg)
    v = v[v.intersects(box(*np.r_[c_pdf.min(0) - 1500, c_pdf.max(0) + 1500]))]
    bl = [b for b in polygonize(unary_union(v.geometry.values)) if 2000 < b.area < 80000]
    c_ref = np.array([[b.centroid.x, b.centroid.y] for b in bl])
    d, j = cKDTree(c_ref).query(c_pdf)
    m = d < f.get("limiar_m", 8.0)
    res = (c_pdf - c_ref[j])[m]
    return {"quadras_no_pdf": int(len(qs)), "quadras_na_referencia": int(len(bl)), "pares": int(m.sum()),
            "residuo_mediano_m": round(float(np.median(d[m])), 2), "rms_m": round(float(np.sqrt((d[m] ** 2).mean())), 2),
            "residuo_medio_E_m": round(float(res[:, 0].mean()), 2), "residuo_medio_N_m": round(float(res[:, 1].mean()), 2)}


# ---------------------------------------------------------------- produto "vetor"
def _partes(u, amin=0.0, furo_min=0.0):
    """Polígonos de uma união, sem os menores que amin e sem os furos menores que furo_min."""
    out = []
    for x in (u.geoms if hasattr(u, "geoms") else [u]):
        if x.geom_type != "Polygon" or x.area < amin:
            continue
        if furo_min:
            x = Polygon(x.exterior, [r for r in x.interiors if Polygon(r).area >= furo_min])
        out.append(x)
    return out


def fazer_camada(pl, c, tr, textos=None):
    geo = c.get("geometria", "linha")
    extra, duplicadas = {}, 0
    if geo == "texto":
        # textos gravados como texto no PDF; o ponto é a origem do texto (canto inferior esquerdo)
        cam = c["camada_cad"] if isinstance(c["camada_cad"], list) else [c["camada_cad"]]
        esc = float(np.hypot(*(tr(np.array([[1.0, 0.0]]))[0] - tr(np.array([[0.0, 0.0]]))[0])))
        g, txt, ang, alt = [], [], [], []
        for t in textos or []:
            limpo = re.sub(r"\s+", " ", t["texto"]).strip()
            if t["camada"] not in cam or not limpo:
                continue
            if not (c.get("altura_min_pt", 0) <= t["altura_pt"] <= c.get("altura_max_pt", 1e9)):
                continue
            if "regex" in c and not re.search(c["regex"], limpo):
                continue
            if "excluir_regex" in c and re.search(c["excluir_regex"], limpo):
                continue
            g.append(Point(tr(np.array([[t["x"], t["y"]]]))[0]))
            txt.append(c.get("corrigir", {}).get(limpo, limpo))
            ang.append(round(t["angulo"], 1))
            alt.append(round(t["altura_pt"] * esc, 1))
        extra = {"texto": txt, "angulo_graus": ang, "altura_m": alt}
    elif geo == "pontos_lidos":
        # rótulos desenhados como contorno, lidos na planta: [x, y, texto], em pontos do PDF
        g = [Point(tr(np.array([[x, y]]))[0]) for x, y, _ in c["pontos"]]
        extra = {"texto": [t for _, _, t in c["pontos"]]}
    else:
        sel = selecionar(pl, c)
        if "extensao_min_m" in c:
            sel = [p for p in sel if np.ptp(tr(p["pts"]), axis=0).max() >= c["extensao_min_m"]]
        if "extensao_max_m" in c:
            sel = [p for p in sel if np.ptp(tr(p["pts"]), axis=0).max() <= c["extensao_max_m"]]
    if geo in ("texto", "pontos_lidos"):
        pass
    elif geo == "linha":
        g = [LineString(tr(p["pts"])) for p in sel if len(p["pts"]) >= 2]
        g = [x for x in g if x.length > 0]
        if c.get("sem_duplicadas"):
            # a planta às vezes traz a mesma linha desenhada duas vezes: fica a primeira
            vistas, unicas = set(), []
            for x in g:
                if x.wkb not in vistas:
                    vistas.add(x.wkb)
                    unicas.append(x)
            duplicadas, g = len(g) - len(unicas), unicas
    elif geo == "faces":
        # contornos desenhados em vários traços: as faces fechadas pela rede de linhas
        u = unary_union([LineString(tr(p["pts"])) for p in sel if len(p["pts"]) >= 2])
        g = [x for x in polygonize(u) if c.get("area_min_m2", 0) <= x.area <= c.get("area_max_m2", np.inf)]
    elif geo == "poligono":
        g = []
        for p in sel:
            t = tr(p["pts"])
            if len(t) < 3:
                continue
            if not np.allclose(t[0], t[-1]):
                if np.hypot(*(t[0] - t[-1])) > c.get("fechar_ate_m", 5.0) and not p["fechado"]:
                    continue
                t = np.vstack([t, t[0]])
            if len(t) < 4:
                continue
            po = Polygon(t)
            if not po.is_valid:
                po = po.buffer(0)
            if po.area >= c.get("area_min_m2", 0):
                g.append(po)
        # filtro pelo que a planta desenha dentro do contorno (pontilhado, hachura, preenchimento):
        # conta os traços de outra seleção cujo centro cai dentro de cada polígono
        for chave, passa in (("com_tracos_de", lambda k: k >= c.get("tracos_min", 1)),
                             ("sem_tracos_de", lambda k: k <= c.get("tracos_max", 0))):
            if chave in c and g:
                fs = c[chave] if isinstance(c[chave], list) else [c[chave]]
                centros = [tr(p["pts"]).mean(0) for f in fs for p in selecionar(pl, f)]
                arv = STRtree(shapely.points(np.array(centros))) if centros else None
                g = [po for po in g if passa(0 if arv is None else len(arv.query(po, predicate="contains")))]
    elif geo == "uniao":
        # área desenhada como muitos preenchimentos pequenos: une tudo e separa as partes
        ps = []
        for p in sel:
            t = tr(p["pts"])
            if len(t) >= 3:
                po = Polygon(t if np.allclose(t[0], t[-1]) else np.vstack([t, t[0]])) if len(t) >= 4 or not np.allclose(t[0], t[-1]) else None
                if po is not None and not po.is_empty:
                    ps.append(po if po.is_valid else po.buffer(0))
        d = c.get("fechar_m", 0.5)
        u = unary_union([x.buffer(d) for x in ps]).buffer(-d).buffer(0) if ps else Polygon()
        g = _partes(u, c.get("area_min_m2", 0), c.get("tapar_furos_ate_m2", 0))
    elif geo == "hachura":
        # hachura (linhas, cruzes ou pontos) -> polígono: engrossa cada traço, une e desfaz o engrossamento
        r = c["meia_distancia_m"]
        ls = [LineString(tr(p["pts"])) for p in sel if len(p["pts"]) >= 2]
        if c.get("pontas", "retas") == "redondas":
            # pontas redondas (para pontilhado e cruzes); arcos com poucos segmentos, para caber na memória
            u = shapely.union_all(shapely.buffer(np.array(ls, dtype=object), r, quad_segs=3, cap_style="round"))
            u = shapely.buffer(shapely.buffer(u, r * 0.5, quad_segs=3), -r * 1.5 + 0.01, quad_segs=3).buffer(0)
        else:
            u = unary_union([x.buffer(r, cap_style=2) for x in ls])
            u = u.buffer(r * 0.5).buffer(-r * 1.5 + 0.01).buffer(0)
        g = _partes(u, c.get("area_min_m2", 0), c.get("tapar_furos_ate_m2", 0))
    elif geo == "simbolo":
        # símbolo desenhado com vários traços: um ponto no centro de cada grupo de traços próximos
        d = c.get("juntar_ate_m", 20.0)
        gs = []
        for p in sel:
            t = tr(p["pts"])
            gs.append(LineString(t) if len(t) >= 2 else Point(t[0]))
        u = unary_union([x.buffer(d / 2) for x in gs]) if gs else Polygon()
        partes = _partes(u)
        arv = STRtree(gs) if gs else None
        g, n = [], []
        for pa in partes:
            dentro = [gs[k] for k in arv.query(pa) if gs[k].intersects(pa)]
            if len(dentro) < c.get("tracos_min", 1):
                continue
            g.append(unary_union(dentro).envelope.centroid)
            n.append(len(dentro))
        extra = {"tracos": n}
    else:
        raise SystemExit(f"geometria desconhecida: {geo}")
    df = gpd.GeoDataFrame({"camada_cad": [", ".join(c["camada_cad"]) if isinstance(c["camada_cad"], list) else c["camada_cad"]] * len(g),
                           "rotulo_no_mapa": [c.get("rotulo_no_mapa", "")] * len(g), **extra}, geometry=g)
    for x0, y0, x1, y1 in c.get("excluir_caixas_pdf", []):
        # amostra da legenda desenhada na mesma camada: sai o que tem o centro dentro da caixa (pontos do PDF)
        cx = Polygon(tr(np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]])))
        df = df[~df.representative_point().within(cx)].reset_index(drop=True)
    for k, v in c.get("atributos", {}).items():
        df[k] = v
    if "atributo_por_ponto" in c and len(df):
        # valor lido na planta para o polígono que contém o ponto (x e y em pontos do PDF)
        ap = c["atributo_por_ponto"]
        if ap["campo"] not in df.columns:
            df[ap["campo"]] = None
        for x, y, v in ap["pontos"]:
            pt = Point(tr(np.array([[x, y]]))[0])
            if df.geom_type.iloc[0] == "Point":
                # camada de pontos: o valor vai para o ponto mais próximo, dentro do limite
                d = df.distance(pt)
                if d.min() <= ap.get("ate_m", 30.0):
                    df.loc[d.idxmin(), ap["campo"]] = v
            else:
                df.loc[df.contains(pt), ap["campo"]] = v
    if geo == "linha":
        # vértices muito espaçados indicam traçado esquemático; densos, linha levantada
        df["comprimento_m"] = df.length.round(1)
        df["vertices_a_cada_m"] = [round(float(np.median(np.hypot(*np.diff(np.array(x.coords), axis=0).T))), 1) for x in df.geometry]
    elif geo not in ("texto", "pontos_lidos", "simbolo"):
        df["area_m2"] = df.area.round(1)
    df.attrs["duplicadas_retiradas"] = duplicadas
    return df


# ---------------------------------------------------------------- produto "curvas"
def _juntar(linhas, tol):
    """Une pedaços cujos extremos ficam a menos de tol; devolve o grupo de cada pedaço."""
    n = len(linhas)
    pai = list(range(n))

    def f(i):
        while pai[i] != i:
            pai[i] = pai[pai[i]]
            i = pai[i]
        return i
    ext = np.array([c for l in linhas for c in (l.coords[0], l.coords[-1])])
    if n:
        for i, j in cKDTree(ext).query_pairs(tol):
            if i // 2 != j // 2:
                pai[f(i // 2)] = f(j // 2)
    raiz = [f(i) for i in range(n)]
    ren = {r: k for k, r in enumerate(sorted(set(raiz)))}
    return [ren[r] for r in raiz]


def _raio(arv, linhas, p, d, L, proprio):
    r = LineString([p, (p[0] + d[0] * L, p[1] + d[1] * L)])
    hits = []
    for j in arv.query(r):
        if j == proprio:
            continue
        x = r.intersection(linhas[j])
        if x.is_empty:
            continue
        for g in ([x] if x.geom_type == "Point" else getattr(x, "geoms", [])):
            if g.geom_type == "Point":
                hits.append((float(np.hypot(g.x - p[0], g.y - p[1])), int(j)))
    hits.sort()
    return hits


def _amostras(l, passo):
    n = max(6, int(l.length / passo))
    for t in np.linspace(0.03, 0.97, n):
        p = l.interpolate(t, normalized=True)
        q = l.interpolate(min(t + 0.004, 1.0), normalized=True)
        tx, ty = q.x - p.x, q.y - p.y
        nn = np.hypot(tx, ty)
        if nn > 0:
            yield (p.x, p.y), (-ty / nn, tx / nn)


def cotar_curvas(pl, c, tr):
    """Devolve GeoDataFrame das curvas com tipo, cota_m e como a cota foi obtida."""
    emin = c.get("extensao_minima_curva_m", 20.0)
    Lraio = c.get("comprimento_do_raio_m", 2300.0)
    passo = c.get("passo_das_amostras_m", 80.0)
    eq, mest = c.get("equidistancia_m", 1), c.get("mestra_a_cada_m", 5)
    cmin = c.get("concordancia_minima", 0.8)

    def linhas(f):
        out = []
        for p in selecionar(pl, f):
            t = tr(p["pts"])
            if len(t) >= 2 and np.ptp(t, axis=0).max() >= emin:
                out.append(LineString(t))
        return out
    M, Itm = linhas(c["mestra"]), linhas(c["intermediaria"])
    nM = len(M)
    grp = _juntar(M, c.get("juntar_mestras_ate_m", 16.0))
    gi = _juntar(Itm, c.get("juntar_intermediarias_ate_m", 3.0))
    grp = grp + [g + (max(grp) + 1 if grp else 0) for g in gi]
    z, como = {}, {}
    # curvas que a planta destaca com outro traço e nomeia na legenda (cota conhecida);
    # só entram os desenhos com vértices densos, isto é, curva do levantamento e não traçado esquemático
    for f in c.get("curvas_de_cota_fixa", []):
        for l in linhas(f):
            seg = np.hypot(*np.diff(np.array(l.coords), axis=0).T)
            if np.median(seg) <= f.get("vertices_a_cada_ate_m", np.inf):
                g = max(grp) + 1
                Itm.append(l)
                grp.append(g)
                z[g], como[g] = int(f["cota_m"]), f.get("origem", "legenda da planta")
    todas = M + Itm
    eh_mestra = {g: i < nM for i, g in enumerate(grp)}
    # 1) mestras: rótulos lidos na planta (posição em pontos do PDF, valor)
    arvM = STRtree(M)
    conflitos, longe = [], []
    for x, y, v in c["rotulos"]:
        p = Point(tr(np.array([[x, y]]))[0])
        j = int(arvM.nearest(p))
        if M[j].distance(p) > c.get("rotulo_ate_m", 16.0):
            longe.append([x, y, v, grp[j]])
            continue
        g = grp[j]
        if g in z and z[g] != v:
            conflitos.append((g, z[g], v))
        z.setdefault(g, v)
        como[g] = "rótulo da planta"
    # raios perpendiculares a cada curva: a sequência de curvas cruzadas de cada lado.
    # O raio para ao reencontrar a própria curva; amostra que cruza duas vezes a mesma vizinha é descartada.
    arv = STRtree(todas)
    seqs = collections.defaultdict(list)          # grupo -> [(lado A, lado B), ...]
    for i, l in enumerate(todas):
        g = grp[i]
        for p, n in _amostras(l, passo):
            par = []
            for d in (n, (-n[0], -n[1])):
                s, fim, d1 = [], "nada", np.inf
                for dist, j in _raio(arv, todas, p, d, Lraio, -1):
                    if dist < 0.5:
                        continue
                    if grp[j] == g:
                        fim = "própria"
                        break
                    if grp[j] in s:
                        fim = "repetida"
                        break
                    if not s:
                        d1 = dist
                    s.append(grp[j])
                    if len(s) > mest // eq + 1:
                        fim = "limite"
                        break
                par.append((s, fim, d1))
            seqs[g].append(tuple(par))

    def ate_mestra(s):
        """Número de curvas até a primeira mestra já cotada, e a cota dela."""
        for k, g in enumerate(s[0]):
            if eh_mestra[g]:
                return (k, z[g]) if g in z and k <= mest // eq - 1 else None
        return None

    def decide(g, votos, origem, minimo=2):
        if not votos:
            return False
        val, n = votos.most_common(1)[0]
        if n < minimo or n / sum(votos.values()) < cmin or (val % mest == 0) != eh_mestra[g]:
            return False
        z[g], como[g] = int(val), origem
        return True
    # 2) intermediárias: contagem de curvas entre duas mestras vizinhas
    #    (para as curvas de cota fixa, a contagem serve de conferência independente)
    fixas = []
    for g in [g for g in seqs if not eh_mestra[g]]:
        v = collections.Counter()
        for sa, sb in seqs[g]:
            a, b = ate_mestra(sa), ate_mestra(sb)
            if a and b and abs(a[1] - b[1]) == mest and a[0] + b[0] == mest // eq - 2:
                v[a[1] + (a[0] + 1) * eq * (1 if b[1] > a[1] else -1)] += 1
        if g in z:
            if v:
                val, n = v.most_common(1)[0]
                fixas.append({"cota_da_legenda_m": z[g], "cota_pela_contagem_m": int(val), "amostras": int(sum(v.values())),
                              "concordancia": round(n / sum(v.values()), 3)})
            continue
        decide(g, v, "contagem entre mestras", 1)
    # 3) o que sobrou, até não mudar mais:
    #    a) curva entre duas vizinhas já cotadas que diferem de duas equidistâncias -> a média;
    #    b) curva com vizinhas cotadas de um lado só (margem do desenho, topo ou fundo fechado):
    #       continua a sequência das duas vizinhas desse lado. É suposição: fica marcada na origem.
    mudou = True
    while mudou:
        mudou = False
        for regra in ("a", "b"):
            for g in list(seqs):
                if g in z:
                    continue
                v = collections.Counter()
                for (sa, fa, _), (sb, fb, _) in seqs[g]:
                    if regra == "a":
                        if sa and sb and sa[0] in z and sb[0] in z and abs(z[sa[0]] - z[sb[0]]) == 2 * eq:
                            v[(z[sa[0]] + z[sb[0]]) // 2] += 1
                    else:
                        for s, o, fo in ((sa, sb, fb), (sb, sa, fa)):
                            if len(s) >= 2 and s[0] in z and s[1] in z and abs(z[s[0]] - z[s[1]]) == eq \
                                    and ((not o and fo != "repetida") or (o and o[0] not in z)):
                                v[2 * z[s[0]] - z[s[1]]] += 1
                if decide(g, v, "entre vizinhas já cotadas" if regra == "a" else "sequência das vizinhas (suposição)"):
                    mudou = True
            if mudou:
                break
    # 4) conferência: curvas vizinhas não podem diferir de mais de uma equidistância
    saltos, pares = [], 0
    for g in seqs:
        if g not in z:
            continue
        for par in seqs[g]:
            for s, _, d1 in par:
                if s and s[0] in z and d1 <= c.get("conferencia_ate_m", 500.0):
                    pares += 1
                    if abs(z[s[0]] - z[g]) > eq:
                        saltos.append((g, s[0]))
    df = gpd.GeoDataFrame({"tipo": ["mestra"] * nM + ["intermediaria"] * len(Itm),
                           "cota_m": [z.get(g) for g in grp],
                           "origem_da_cota": [como.get(g, "sem cota") for g in grp],
                           "curva": grp}, geometry=todas)
    df["cota_m"] = df["cota_m"].astype("Int64")
    df["comprimento_m"] = df.length.round(1)
    tot, com = float(df.length.sum()), float(df[df.cota_m.notna()].length.sum())
    info = {"pedacos_mestra": nM, "pedacos_intermediaria": len(Itm), "curvas_distintas": len(set(grp)),
            "rotulos_usados": len(c["rotulos"]) - len(longe), "rotulos_sem_curva_perto": longe,
            "conflitos_de_rotulo": [list(map(int, x)) for x in conflitos],
            "comprimento_total_km": round(tot / 1000, 2),
            "comprimento_com_cota_km": round(com / 1000, 2),
            "comprimento_com_cota_pct": round(100 * com / tot, 2),
            "km_por_origem_da_cota": {k: round(float(v) / 1000, 2) for k, v in df.groupby("origem_da_cota").comprimento_m.sum().items()},
            "km_por_cota": {int(k): round(float(v) / 1000, 2) for k, v in df[df.cota_m.notna()].groupby("cota_m").comprimento_m.sum().items()},
            "conferencia_das_curvas_de_cota_fixa": fixas,
            "cota_minima_m": int(df.cota_m.min()), "cota_maxima_m": int(df.cota_m.max()),
            "conferencia_vizinhas": {"pares": pares, "saltos_maiores_que_a_equidistancia": len(saltos),
                                     "curvas_com_salto": sorted({int(a) for a, _ in saltos})}}
    if c.get("descartar_curvas_sem_cota"):
        # o que ficou sem cota não entra no produto (decisão registrada nos parâmetros)
        fora = df[df.cota_m.isna()]
        info["curvas_sem_cota_descartadas"] = {"curvas": int(fora.curva.nunique()), "pedacos": int(len(fora)),
                                               "comprimento_km": round(float(fora.length.sum()) / 1000, 2),
                                               "maior_m": round(float(fora.length.max()), 1) if len(fora) else 0.0}
        df = df[df.cota_m.notna()].reset_index(drop=True)
        info["comprimento_no_produto_km"] = round(float(df.length.sum()) / 1000, 2)
        info["pedacos_no_produto"] = int(len(df))
    return df, info


def fazer_mdt(curvas, m, destino, epsg):
    """Modelo de terreno por triangulação linear dos pontos das curvas cotadas."""
    import rasterio
    from rasterio.transform import from_origin
    from rasterio.features import rasterize
    from scipy.interpolate import LinearNDInterpolator
    from scipy.ndimage import distance_transform_edt
    cel = m.get("celula_m", 5.0)
    c = curvas[curvas.cota_m.notna()]
    pts, zs = [], []
    for g, zc in zip(c.geometry, c.cota_m):
        n = max(2, int(g.length / m.get("passo_dos_pontos_m", 5.0)))
        for t in np.linspace(0, 1, n):
            p = g.interpolate(t, normalized=True)
            pts.append((p.x, p.y))
            zs.append(float(zc))
    pts, zs = np.array(pts), np.array(zs)
    x0, y0, x1, y1 = c.total_bounds
    x0, y0 = np.floor(x0 / cel) * cel, np.floor(y0 / cel) * cel
    nx, ny = int(np.ceil((x1 - x0) / cel)), int(np.ceil((y1 - y0) / cel))
    T = from_origin(x0, y0 + ny * cel, cel, cel)
    gx = x0 + (np.arange(nx) + 0.5) * cel
    gy = y0 + ny * cel - (np.arange(ny) + 0.5) * cel
    X, Y = np.meshgrid(gx, gy)
    Z = LinearNDInterpolator(pts, zs)(X, Y)
    marca = rasterize([(g, 1) for g in c.geometry], out_shape=(ny, nx), transform=T, fill=0, dtype="uint8")
    dist = distance_transform_edt(marca == 0) * cel
    Z[(dist > m.get("distancia_maxima_da_curva_m", 150.0)) | ~np.isfinite(Z)] = np.nan
    nod = -9999.0
    with rasterio.open(destino, "w", driver="GTiff", height=ny, width=nx, count=1, dtype="float32",
                       crs=f"EPSG:{epsg}", transform=T, nodata=nod, compress="deflate", predictor=3, tiled=True) as ds:
        ds.write(np.where(np.isfinite(Z), Z, nod).astype("float32"), 1)
    v = Z[np.isfinite(Z)]
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(13, 9), dpi=110)
    im = ax.imshow(Z, extent=[x0, x0 + nx * cel, y0, y0 + ny * cel], cmap="terrain", vmin=float(v.min()) - 2, vmax=float(v.max()) + 3)
    cb = fig.colorbar(im, ax=ax, orientation="horizontal", fraction=0.035, pad=0.03)
    cb.set_label("Altitude (m) - modelo de terreno interpolado das curvas (extraído de PDF vetorial; não oficial)", fontsize=8)
    ax.set_xticks([]); ax.set_yticks([])
    fig.savefig(str(destino)[:-4] + "_conferencia.png", facecolor="white", bbox_inches="tight")
    plt.close(fig)
    return {"celula_m": cel, "colunas": nx, "linhas": ny, "pontos_usados": int(len(pts)),
            "area_com_dado_km2": round(float(v.size * cel * cel / 1e6), 3),
            "minimo_m": round(float(v.min()), 2), "maximo_m": round(float(v.max()), 2), "media_m": round(float(v.mean()), 3),
            "metodo": "triangulação linear dos pontos das curvas (um ponto a cada 5 m); sem dado além de 150 m de uma curva"}


# ---------------------------------------------------------------- conferência visual
def imagem(camadas, destino, titulo, quadras=None, categorias=None):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import cm, colors
    from matplotlib.collections import LineCollection
    fig, ax = plt.subplots(figsize=(14, 9.2), dpi=120)
    if quadras is not None and len(quadras):
        quadras.plot(ax=ax, facecolor="#eeeeee", edgecolor="#c4c4c4", linewidth=0.2, zorder=1)
    paleta = ["#b2182b", "#000000", "#1b7837", "#2166ac", "#762a83", "#e08214"]
    leg = []
    for k, (nome, df) in enumerate(camadas.items()):
        campo = (categorias or {}).get(nome)
        if campo and campo in df.columns and len(df):
            # uma cor por valor do campo (sigla, classe, tipo)
            vals = sorted(df[campo].dropna().astype(str).unique())
            cmap = matplotlib.colormaps["tab20"]
            for i, v in enumerate(vals):
                sub, cor = df[df[campo].astype(str) == v], cmap(i % 20)
                if sub.geom_type.iloc[0].endswith("Polygon"):
                    sub.plot(ax=ax, facecolor=cor, alpha=0.6, edgecolor="#333333", linewidth=0.3, zorder=2)
                    leg.append(plt.Rectangle((0, 0), 1, 1, fc=cor, alpha=0.6, label=v))
                elif sub.geom_type.iloc[0] == "Point":
                    sub.plot(ax=ax, color=cor, markersize=8, zorder=6)
                    leg.append(plt.Line2D([0], [0], color=cor, marker="o", lw=0, label=v))
                else:
                    sub.plot(ax=ax, color=cor, linewidth=1.4, zorder=5)
                    leg.append(plt.Line2D([0], [0], color=cor, lw=1.5, label=v))
        elif "cota_m" in df.columns:
            norm, cmap = colors.Normalize(float(df.cota_m.min()) - 2, float(df.cota_m.max()) + 2), matplotlib.colormaps["YlOrBr"]
            c = df[df.cota_m.notna()]
            ax.add_collection(LineCollection([np.array(g.coords) for g in c.geometry], colors=[cmap(norm(float(v))) for v in c.cota_m],
                                             linewidths=[1.0 if t == "mestra" else 0.45 for t in c.tipo], zorder=3))
            s = df[df.origem_da_cota.str.contains("suposição")]
            ax.add_collection(LineCollection([np.array(g.coords) for g in s.geometry], colors="#1b7837", linewidths=0.6, zorder=4))
            s = df[df.cota_m.isna()]
            ax.add_collection(LineCollection([np.array(g.coords) for g in s.geometry], colors="#c51b7d", linewidths=1.2, zorder=5))
            cb = fig.colorbar(cm.ScalarMappable(norm=norm, cmap=cmap), ax=ax, orientation="horizontal", fraction=0.03, pad=0.02, aspect=50)
            cb.set_label("Cota da curva (m). Verde: cota por suposição (sequência das vizinhas)."
                         + (" Rosa: curva sem cota." if df.cota_m.isna().any() else ""), fontsize=8)
        elif len(df) and df.geom_type.iloc[0] in ("Polygon", "MultiPolygon"):
            df.plot(ax=ax, facecolor=paleta[k % 6], alpha=0.18, edgecolor=paleta[k % 6], linewidth=0.5, zorder=2)
            leg.append(plt.Rectangle((0, 0), 1, 1, fc=paleta[k % 6], alpha=0.3, label=nome))
        elif len(df) and df.geom_type.iloc[0] == "Point":
            df.plot(ax=ax, color=paleta[k % 6], markersize=6, zorder=6)
            leg.append(plt.Line2D([0], [0], color=paleta[k % 6], marker="o", lw=0, label=nome))
        elif len(df):
            lw = 1.3 if len(df) < 50 else 0.35
            df.plot(ax=ax, color=paleta[k % 6], linewidth=lw, zorder=5)
            leg.append(plt.Line2D([0], [0], color=paleta[k % 6], lw=1.5, label=nome))
    ax.set_aspect("equal")
    ax.autoscale()
    ax.set_xticks([]); ax.set_yticks([])
    ax.set_title(titulo, fontsize=10, loc="left")
    if leg:
        ax.legend(handles=leg, loc="upper center", bbox_to_anchor=(0.5, -0.02), ncol=3 if len(leg) <= 6 else 6, fontsize=8, frameon=False)
    fig.savefig(destino, facecolor="white", bbox_inches="tight")
    plt.close(fig)


# ---------------------------------------------------------------- principal
def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--parametros", required=True)
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--saida-dir")
    ap.add_argument("--conferir-georef", metavar="VIAS", help="malha de ruas de referência; só mede o ajuste e sai")
    ap.add_argument("--forcar", action="store_true")
    a = ap.parse_args()
    if int(shapely.__version__.split(".")[0]) < 2:
        sys.exit(f"é preciso shapely 2 ou mais novo (instalado: {shapely.__version__})")
    par = json.loads(Path(a.parametros).read_text(encoding="utf-8"))
    doc, epsg = par["documento"], par["epsg"]
    h = sha256(a.pdf)
    if doc.get("sha256") and h != doc["sha256"]:
        sys.exit(f"sha256 do PDF não confere com os parâmetros: {h}")
    pl = ler_pdf(a.pdf, doc.get("pagina", 1))
    print(f"{len(pl)} polilinhas lidas; {len(set(p['camada'] for p in pl))} camadas")
    textos = None
    if any(c.get("geometria") == "texto" for prod in par["produtos"] for c in prod.get("camadas", [])):
        textos = ler_textos(a.pdf, doc.get("pagina", 1))
        print(f"{len(textos)} textos lidos")
    if a.conferir_georef:
        print(json.dumps(conferir_georef(pl, par, a.conferir_georef, epsg), ensure_ascii=False, indent=1))
        return
    if not a.saida_dir:
        sys.exit("faltou --saida-dir")
    out = Path(a.saida_dir)
    out.mkdir(parents=True, exist_ok=True)
    tr = transformador(par["georreferenciamento"])
    atr = par.get("atributos", {})
    quadras = None
    for prod in par["produtos"]:
        base = out / prod["nome_saida"]
        gpkg, meta = base.with_suffix(".gpkg"), base.with_suffix(".json")
        if gpkg.exists() and not a.forcar:
            print(f"já existe (use --forcar para refazer): {gpkg.name}")
            continue
        if gpkg.exists():
            gpkg.unlink()
        info = {"produto": prod["nome_saida"], "tipo": prod["tipo"], "documento": {**doc, "sha256": h},
                "epsg": epsg, "georreferenciamento": par["georreferenciamento"], **atr,
                "observacoes": prod.get("observacoes", ""),
                "script": "extrair_camadas_de_pdf_vetorial.py", "gerado_em": datetime.date.today().isoformat(), "camadas": {}}
        figs = {}
        if prod["tipo"] == "vetor":
            nomes = list(dict.fromkeys(c["nome"] for c in prod["camadas"]))
            for nome in nomes:
                ent = [c for c in prod["camadas"] if c["nome"] == nome]
                partes = [fazer_camada(pl, c, tr, textos) for c in ent]
                duplicadas = sum(x.attrs.get("duplicadas_retiradas", 0) for x in partes)
                df = partes[0] if len(partes) == 1 else gpd.GeoDataFrame(pd.concat(partes, ignore_index=True), geometry="geometry")
                df = df.set_crs(epsg)
                for k, v in atr.items():
                    df[k] = v
                df.to_file(gpkg, layer=nome, driver="GPKG")
                c = ent[0]
                d = {"feicoes": int(len(df)), "geometria": c.get("geometria", "linha"),
                     "camada_cad": c["camada_cad"] if len(ent) == 1 else sorted({x if isinstance(x, str) else ", ".join(x) for x in (e["camada_cad"] for e in ent)})}
                if len(df) and df.geom_type.iloc[0].endswith("Polygon"):
                    d["area_km2"] = round(float(df.area.sum()) / 1e6, 4)
                elif len(df) and df.geom_type.iloc[0].endswith("LineString"):
                    d["comprimento_km"] = round(float(df.length.sum()) / 1000, 3)
                if duplicadas:
                    d["linhas_duplicadas_retiradas"] = int(duplicadas)
                for campo in prod.get("resumir_por", {}).get(nome, []):
                    if campo in df.columns:
                        if len(df) and df.geom_type.iloc[0].endswith("Polygon"):
                            d[f"area_km2_por_{campo}"] = {str(k): round(float(v) / 1e6, 4) for k, v in df.groupby(campo).apply(lambda x: x.area.sum()).items()}
                        elif len(df) and df.geom_type.iloc[0].endswith("LineString"):
                            d[f"comprimento_km_por_{campo}"] = {str(k): round(float(v) / 1000, 3) for k, v in df.groupby(campo).apply(lambda x: x.length.sum()).items()}
                        d[f"feicoes_por_{campo}"] = {str(k): int(v) for k, v in df.groupby(campo).size().items()}
                info["camadas"][nome] = d
                figs[nome] = df
                if nome == "quadras":
                    quadras = df
                print(f"  {prod['nome_saida']} / {nome}: {d}")
        elif prod["tipo"] == "curvas":
            df, d = cotar_curvas(pl, prod, tr)
            df = df.set_crs(epsg)
            for k, v in atr.items():
                df[k] = v
            df.to_file(gpkg, layer="curvas_de_nivel", driver="GPKG")
            info["camadas"]["curvas_de_nivel"] = d
            figs["curvas de nível"] = df
            print(f"  {prod['nome_saida']}: {d}")
            if "mdt" in prod:
                tif = out / (prod["mdt"]["nome_saida"] + ".tif")
                dm = fazer_mdt(df, prod["mdt"], tif, epsg)
                (out / (prod["mdt"]["nome_saida"] + ".json")).write_text(json.dumps(
                    {"produto": prod["mdt"]["nome_saida"], "tipo": "modelo de terreno", "derivado_de": prod["nome_saida"],
                     "documento": {**doc, "sha256": h}, "epsg": epsg, **atr, **dm,
                     "observacoes": prod["mdt"].get("observacoes", ""),
                     "gerado_em": datetime.date.today().isoformat()}, ensure_ascii=False, indent=1), encoding="utf-8")
                print(f"  {prod['mdt']['nome_saida']}: {dm}")
        else:
            sys.exit(f"tipo de produto desconhecido: {prod['tipo']}")
        meta.write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
        imagem(figs, str(base) + "_conferencia.png", f"{doc.get('titulo', '')} — {prod['nome_saida']} (extraído de PDF vetorial; não oficial)", quadras,
               {n: v[0] for n, v in prod.get("resumir_por", {}).items() if v})


if __name__ == "__main__":
    main()
