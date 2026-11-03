"""
Contraste (WCAG 2.x) dos tokens de texto e de elemento gráfico das telas contra as
superfícies (cartão e página), no claro e no escuro, lidos de css/paineis.css.

Limites: texto >= 4,5; texto grande (marca em texto, 20 px a 800) e elemento gráfico
(borda de aviso, seta, contorno) >= 3. As cores de série, status, grade e dica não
entram aqui: são conferidas pelo validador de paleta (sem mudança na rodada A1).

Uso:
    python scripts/paineis/test_contraste.py [--css <arquivo>] [--tabela]
--css permite medir outra versão do arquivo (ex.: a anterior, tirada do git);
--tabela imprime a tabela em Markdown. Sai com código 1 se algum par ficar abaixo.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]

# (rótulo, token da frente, token do fundo, limite)
PARES = [
    ("texto principal", "--ink", "--surface-card", 4.5),
    ("texto principal", "--ink", "--page", 4.5),
    ("texto secundário", "--ink-secondary", "--surface-card", 4.5),
    ("texto secundário", "--ink-secondary", "--page", 4.5),
    ("texto apagado", "--ink-muted", "--surface-card", 4.5),
    ("texto apagado", "--ink-muted", "--page", 4.5),
    ("link / selo (destaque)", "--accent", "--surface-card", 4.5),
    ("link / selo (destaque)", "--accent", "--page", 4.5),
    ("etiqueta (eyebrow)", "--rotulo", "--surface-card", 4.5),
    ("texto sobre fundo suave", "--ink", "--accent-soft", 4.5),
    ("texto secundário sobre fundo suave", "--ink-secondary", "--accent-soft", 4.5),
    ("botão ativo (texto sobre destaque)", "--accent-ink", "--accent", 4.5),
    ("marca: Clima (texto grande)", "--marca-1", "--page", 3.0),
    ("marca: Pampa (texto grande)", "--marca-2", "--page", 3.0),
    ("elemento gráfico (destaque)", "--accent", "--surface-card", 3.0),
]


def lin(c: float) -> float:
    c /= 255
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def lum(h: str) -> float:
    h = h.lstrip("#")
    if len(h) == 3:
        h = "".join(x * 2 for x in h)
    r, g, b = (lin(int(h[i:i + 2], 16)) for i in (0, 2, 4))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def razao(a: str, b: str) -> float:
    x, y = sorted([lum(a), lum(b)])
    return (y + 0.05) / (x + 0.05)


def bloco(css: str, seletor_re: str) -> dict[str, str]:
    m = re.search(seletor_re + r"\s*\{([^{}]*)\}", css)
    if not m:
        return {}
    return {k: v.split("/*")[0].strip() for k, v in re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", m.group(1))}


def modos(css: str) -> dict[str, dict[str, str]]:
    claro = bloco(css, r"(?m)^:root")
    escuro = {**claro, **bloco(css, r':root\[data-theme="dark"\]')}
    return {"claro": claro, "escuro": escuro}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--css", default=str(RAIZ / "css" / "paineis.css"))
    ap.add_argument("--tabela", action="store_true")
    a = ap.parse_args()
    css = Path(a.css).read_text(encoding="utf-8")
    ms = modos(css)
    falhas = 0
    linhas = []
    for rot, fr, fu, lim in PARES:
        vals = []
        for modo, t in ms.items():
            if fr not in t or fu not in t or not t[fr].startswith("#") or not t[fu].startswith("#"):
                vals.append((modo, None, t.get(fr), t.get(fu)))
                continue
            vals.append((modo, razao(t[fr], t[fu]), t[fr], t[fu]))
        linhas.append((rot, fr, fu, lim, vals))
        for modo, v, *_ in vals:
            if v is not None and v < lim:
                falhas += 1
    if a.tabela:
        print("| Par | Frente / fundo | Limite | Claro | Escuro |")
        print("|---|---|---|---|---|")
        for rot, fr, fu, lim, vals in linhas:
            cel = []
            for modo, v, c1, c2 in vals:
                cel.append("token ausente" if v is None else f"{v:.2f}".replace(".", ",") + f" ({c1} / {c2})" + ("" if v >= lim else " **abaixo**"))
            print(f"| {rot} | `{fr}` / `{fu}` | {lim:.1f}".replace(".", ",", 1) + f" | {cel[0]} | {cel[1]} |")
    else:
        for rot, fr, fu, lim, vals in linhas:
            for modo, v, c1, c2 in vals:
                if v is None:
                    print(f"[--] {rot} ({modo}): token ausente")
                else:
                    print(f"[{'ok' if v >= lim else 'FALHA'}] {rot} ({modo}): {v:.2f} (mín. {lim}) {c1} sobre {c2}")
    print(f"\n{falhas} par(es) abaixo do limite" if falhas else "\ntodos os pares no limite ou acima")
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(main())
