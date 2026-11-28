"""Agrupa páginas en documentos y aplica las palabras clave del usuario."""
from __future__ import annotations

from .clasificador_local import sin_tildes
from .modelos import Documento, PaginaInfo, limpiar

# En un expediente judicial, solo las resoluciones y comunicaciones del órgano judicial fijan
# plazos procesales; los de la prueba documental (facturas, contratos, acuerdos privados) o los
# de los escritos de parte no son plazos del procedimiento. En uno extrajudicial, en cambio,
# los plazos de los contratos, acuerdos y requerimientos son justo lo que interesa.
TIPOS_CON_PLAZOS = {"sentencia", "auto", "providencia", "decreto", "diligencia_ordenacion",
                    "diligencia", "notificacion", "oficio", "exhorto", "acta"}
# Si aparece alguno de estos, el expediente es judicial (una notificación suelta no basta:
# los documentos aportados a un pleito suelen llegar con la de LexNET delante).
TIPOS_JUDICIALES = {"sentencia", "auto", "providencia", "decreto", "diligencia_ordenacion",
                    "escrito_parte", "acta", "declaracion", "exhorto"}
AMBITOS = {"auto": "Detectar automáticamente", "judicial": "Judicial", "extrajudicial": "Extrajudicial"}


def decidir_ambito(docs: list[Documento], elegido: str = "auto") -> str:
    if elegido in ("judicial", "extrajudicial"):
        return elegido
    return "judicial" if any(d.tipo in TIPOS_JUDICIALES for d in docs) else "extrajudicial"


def ajustar_al_ambito(docs: list[Documento], ambito: str) -> None:
    """En un expediente judicial, retira los plazos y citas que no son del procedimiento."""
    if ambito != "judicial":
        return
    for d in docs:
        if d.tipo not in TIPOS_CON_PLAZOS:
            d.plazos = []
        if d.tipo == "documento":
            d.senalamientos = []


def segmentar(paginas: list[PaginaInfo]) -> list[Documento]:
    docs: list[Documento] = []
    textos: list[list[str]] = []
    for p in paginas:
        if not docs or (p.inicia_documento and not p.error):
            docs.append(Documento(
                numero=len(docs) + 1,
                tipo=p.tipo if not p.error else "otros",
                titulo=limpiar(p.titulo),
                fecha=limpiar(p.fecha),
                autor=limpiar(p.autor),
                pagina_inicio=p.pagina,
                pagina_fin=p.pagina,
                subtipo=limpiar(p.subtipo),
                parte=limpiar(p.parte),
                posicion=limpiar(p.posicion),
            ))
            textos.append([])
        d = docs[-1]
        d.pagina_fin = p.pagina
        if not d.fecha and p.fecha and not p.error:
            d.fecha = p.fecha  # la fecha suele estar al final (antefirma)
        if not d.autor and p.autor:
            d.autor = p.autor
        if d.tipo == "escrito_parte" and not d.parte and p.parte:
            d.parte, d.posicion = p.parte, p.posicion
        for origen, destino, clave in ((p.plazos, d.plazos, ("concepto", "plazo")),
                                      (p.senalamientos, d.senalamientos, ("acto", "fecha", "hora"))):
            for dato in origen:
                if not any(all(x.get(c) == dato.get(c) for c in clave) for x in destino):
                    destino.append(dict(dato, pagina=p.pagina))
        textos[-1].append(p.texto)
    for d, t in zip(docs, textos):
        d.texto = "\n".join(t)
    return docs


def leer_palabras_clave(texto: str) -> dict[str, list[str]]:
    """Formato: una línea por etiqueta ->  Etiqueta: palabra, otra frase"""
    reglas: dict[str, list[str]] = {}
    for linea in texto.splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#"):
            continue
        if ":" in linea:
            etiqueta, resto = linea.split(":", 1)
        else:
            etiqueta, resto = linea, linea
        palabras = [p.strip() for p in resto.replace(";", ",").split(",") if p.strip()]
        if etiqueta.strip() and palabras:
            reglas.setdefault(etiqueta.strip(), []).extend(palabras)
    return reglas


def _norma(s: str) -> str:
    return " ".join(sin_tildes(s).lower().split())


def aplicar_palabras_clave(docs: list[Documento], reglas: dict[str, list[str]]) -> None:
    for d in docs:
        texto = _norma(d.titulo + " " + d.texto)
        d.etiquetas = [e for e, palabras in reglas.items()
                       if any(_norma(p) in texto for p in palabras)]


def apariciones(paginas: list[PaginaInfo], reglas: dict[str, list[str]]) -> dict[str, list[tuple[int, str]]]:
    """Páginas concretas donde aparece cada etiqueta: {etiqueta: [(página, palabra hallada)]}."""
    salida: dict[str, list[tuple[int, str]]] = {}
    for etiqueta, palabras in reglas.items():
        for p in paginas:
            texto = _norma(p.texto)
            hallada = next((w for w in palabras if _norma(w) in texto), None)
            if hallada:
                salida.setdefault(etiqueta, []).append((p.pagina, hallada))
    return salida
