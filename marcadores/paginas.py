"""Páginas giradas y páginas en blanco.

Giro: se deduce de lo que ya devolvió el OCR, sin consultas nuevas. En una página escaneada de
lado, los recuadros de texto son altos y estrechos, y el orden de lectura avanza en horizontal:
de derecha a izquierda si la parte de arriba del folio quedó a la derecha, o al revés. En una
página boca abajo, la lectura avanza de abajo arriba. Si la página ya traía texto, se usa la
dirección de ese texto. Ante la duda, no se gira.

La página se endereza con la marca de giro del PDF, sin tocar la imagen escaneada.

En blanco: casi sin tinta y sin texto útil (se admite un número de folio suelto). El umbral es
prudente: una hoja con una firma o un sello no se considera en blanco.
"""
from __future__ import annotations

import re

import pymupdf as fitz

from .modelos import PaginaInfo

MINIMO_RECUADROS = 3
PROPORCION_VERTICAL = 0.6
TINTA_MAXIMA = 0.001  # 0,1 % de la superficie útil (una firma suelta suele superar esto)
LETRAS_MAXIMAS = 6


def _correlacion(xs: list[float], ys: list[float]) -> float:
    """Correlación de rangos (Spearman, sin empates) entre dos listas."""
    n = len(xs)
    if n < 2:
        return 0.0

    def rangos(v):
        orden = sorted(range(n), key=lambda i: v[i])
        r = [0] * n
        for pos, i in enumerate(orden):
            r[i] = pos
        return r

    rx, ry = rangos(xs), rangos(ys)
    d2 = sum((a - b) ** 2 for a, b in zip(rx, ry))
    return 1 - 6 * d2 / (n * (n * n - 1))


def giro_por_recuadros(lineas: list) -> int:
    """Giro (0, 90, 180, 270) que endereza la página, según los recuadros del OCR en su orden."""
    cajas = []
    for linea in lineas:
        try:
            texto = str(linea[0]).strip()
            y0, x0, y1, x1 = (float(n) for n in linea[1:5])
        except (TypeError, ValueError, IndexError):
            continue
        if len(texto) > 3 and y1 > y0 and x1 > x0:
            cajas.append((y0, x0, y1, x1))
    if len(cajas) < MINIMO_RECUADROS:
        return 0
    verticales = [c for c in cajas if (c[2] - c[0]) > 1.5 * (c[3] - c[1])]
    orden = list(range(len(cajas)))
    if len(verticales) >= PROPORCION_VERTICAL * len(cajas):
        centros_x = [(c[1] + c[3]) / 2 for c in cajas]
        r = _correlacion(orden, centros_x)
        if r <= -0.5:
            return 270  # se lee de derecha a izquierda: lo de arriba quedó a la derecha
        if r >= 0.5:
            return 90
        return 0
    if len(verticales) == 0 and len(cajas) >= 4:
        centros_y = [(c[0] + c[2]) / 2 for c in cajas]
        if _correlacion(orden, centros_y) <= -0.7:
            return 180  # se lee de abajo arriba: boca abajo
    return 0


def giro_por_texto(pagina: fitz.Page) -> int:
    """Giro según la dirección del texto que ya trae el PDF (solo si no lleva giro propio)."""
    if pagina.rotation:
        return 0
    votos = {0: 0, 90: 0, 180: 0, 270: 0}
    for bloque in pagina.get_text("dict").get("blocks", []):
        for linea in bloque.get("lines", []):
            dx, dy = linea.get("dir", (1, 0))
            largo = sum(len(s.get("text", "").strip()) for s in linea.get("spans", []))
            if abs(dx) >= abs(dy):
                votos[0 if dx > 0 else 180] += largo
            else:
                votos[270 if dy > 0 else 90] += largo
    total = sum(votos.values())
    if total < 40:
        return 0
    giro, peso = max(votos.items(), key=lambda kv: kv[1])
    return giro if giro and peso > 0.7 * total else 0


def giro_de(pagina: fitz.Page, info: PaginaInfo) -> int:
    if info.lineas:
        return giro_por_recuadros(info.lineas)
    return giro_por_texto(pagina)


def tinta(pagina: fitz.Page) -> float:
    """Proporción de puntos oscuros de la página (sin los bordes, donde hay sombras y taladros)."""
    r = pagina.rect
    zona = fitz.Rect(r.x0 + r.width * 0.07, r.y0 + r.height * 0.05,
                     r.x1 - r.width * 0.07, r.y1 - r.height * 0.05)
    pix = pagina.get_pixmap(dpi=40, colorspace=fitz.csGRAY, clip=zona, alpha=False)
    datos = pix.samples
    if not datos:
        return 0.0
    oscuros = sum(1 for b in datos if b < 140)
    return oscuros / len(datos)


def es_blanca(pagina: fitz.Page, info: PaginaInfo) -> bool:
    letras = len(re.findall(r"[^\W\d_]", info.texto or ""))
    if letras > LETRAS_MAXIMAS or info.error:
        return False
    return tinta(pagina) <= TINTA_MAXIMA
