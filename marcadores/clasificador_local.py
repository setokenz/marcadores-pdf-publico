"""Modo básico sin IA: usa el texto que ya tenga el PDF y reglas por encabezamiento."""
from __future__ import annotations

import re
import unicodedata

import pymupdf as fitz

from .modelos import PaginaInfo

MESES = {m: i + 1 for i, m in enumerate(
    ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
     "septiembre", "octubre", "noviembre", "diciembre"])}
MESES["setiembre"] = 9


def sin_tildes(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


# (patrón sobre el encabezamiento en mayúsculas sin tildes, tipo, título)
REGLAS = [
    (r"^\W*SENTENCIA\b", "sentencia", "Sentencia"),
    (r"^\W*AUTO\b", "auto", "Auto"),
    (r"^\W*PROVIDENCIA\b", "providencia", "Providencia"),
    (r"^\W*DECRETO\b", "decreto", "Decreto"),
    (r"^\W*DILIGENCIA DE ORDENACION\b", "diligencia_ordenacion", "Diligencia de ordenación"),
    (r"^\W*DILIGENCIA\b", "diligencia", "Diligencia"),
    (r"^\W*(ACTA DE )?DECLARACION\b", "declaracion", "Declaración"),
    (r"^\W*(INTERROGATORIO|EXPLORACION)\b", "declaracion", "Declaración"),
    (r"^\W*ACTA\b", "acta", "Acta"),
    (r"^\W*OFICIO\b", "oficio", "Oficio"),
    (r"^\W*(EXHORTO|MANDAMIENTO)\b", "exhorto", "Exhorto / mandamiento"),
    (r"^\W*CEDULA DE (NOTIFICACION|CITACION|EMPLAZAMIENTO)\b", "notificacion", "Cédula"),
    (r"^\W*(INFORME|DICTAMEN) PERICIAL\b", "informe_pericial", "Informe pericial"),
    (r"^\W*ATESTADO\b", "atestado", "Atestado"),
    (r"^\W*(AL|A LA) (JUZGADO|TRIBUNAL|SECCION|AUDIENCIA|SALA)\b", "escrito_parte", "Escrito de parte"),
]
REGLAS = [(re.compile(p), t, n) for p, t, n in REGLAS]


def buscar_fecha(texto: str) -> str:
    t = sin_tildes(texto.lower())
    m = re.search(r"\b(\d{1,2})\s+de\s+([a-z]+)\s+de\s+(\d{4})\b", t)
    if m and m.group(2) in MESES and 1 <= int(m.group(1)) <= 31:
        return f"{int(m.group(3)):04d}-{MESES[m.group(2)]:02d}-{int(m.group(1)):02d}"
    m = re.search(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b", t)
    if m and 1 <= int(m.group(1)) <= 31 and 1 <= int(m.group(2)) <= 12:
        return f"{int(m.group(3)):04d}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
    return ""


RECURSO_INTERMEDIO = re.compile(r"reposicion|revision|reforma|suplica|queja")
RE_PLAZO = re.compile(r"(?:plazo|termino) de ((?:[a-z]+|\d+) (?:dias(?: habiles| naturales)?|audiencias?|meses|mes))")
RE_SENALA = re.compile(
    r"se (?:senala|convoca|cita)[^.]{0,200}?(\d{1,2} de [a-z]+ de \d{4})(?:[^.]{0,40}?a las (\d{1,2})[:.](\d{2}))?")


def buscar_plazos_y_senalamientos(texto: str) -> tuple[list[dict], list[dict]]:
    """Versión sencilla, por expresiones, para el modo sin IA."""
    plano = " ".join(texto.split())
    frases = re.split(r"(?<=[.;])\s+", plano)
    plazos, senalamientos = [], []
    for frase in frases:
        norma = sin_tildes(frase.lower())
        m = RE_PLAZO.search(norma)
        if m and not RECURSO_INTERMEDIO.search(norma):
            plazos.append({"concepto": frase[:160], "plazo": m.group(1)})
        m = RE_SENALA.search(norma)
        if m:
            hora = f"{int(m.group(2)):02d}:{m.group(3)}" if m.group(2) else ""
            senalamientos.append({"acto": frase[:120], "fecha": buscar_fecha(m.group(1)),
                                  "hora": hora, "lugar": ""})
    return plazos, senalamientos


class AnalizadorBasico:
    nombre = "basico"
    firma = "basico:2"
    paginas_por_lote = 50

    def analizar_lote(self, doc: fitz.Document, indices: list[int], contexto: str = "") -> list[PaginaInfo]:
        salida = []
        for i in indices:
            texto = doc[i].get_text()
            info = PaginaInfo(pagina=i + 1, texto=texto)
            lineas = [sin_tildes(l.strip()).upper() for l in texto.splitlines() if l.strip()]
            for linea in lineas[:12]:
                regla = next((r for r in REGLAS if r[0].search(linea)), None)
                if regla:
                    info.inicia_documento = True
                    info.tipo = regla[1]
                    info.titulo = regla[2]
                    info.subtipo = regla[2]
                    info.fecha = buscar_fecha(texto)
                    break
            info.plazos, info.senalamientos = buscar_plazos_y_senalamientos(texto)
            if not info.inicia_documento:
                # hoja de continuación: solo vale la data ("En Valencia, a 3 de enero de 2024")
                m = re.search(r",\s+a\s+(\d{1,2}\s+de\s+[a-z]+\s+de\s+\d{4})", sin_tildes(texto.lower()))
                info.fecha = buscar_fecha(m.group(1)) if m else ""
            salida.append(info)
        return salida
