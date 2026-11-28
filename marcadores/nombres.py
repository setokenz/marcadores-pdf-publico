"""Plantillas para los nombres de las carpetas y archivos que se generan.

Cada plantilla es un texto con huecos entre llaves que se rellenan al procesar, por ejemplo
«{nombre} - marcadores». El resultado se limpia para que sea un nombre válido en Windows.
"""
from __future__ import annotations

import datetime
import re
from dataclasses import dataclass

from .modelos import Documento, limpiar

POR_DEFECTO = {
    "carpeta": "{nombre} - clasificado",
    "pdf": "{nombre} - marcadores",
    "excel": "{nombre} - índice",
    "documento": "{num} - {tipo} - {fecha} - {titulo}",
}

# Huecos disponibles, para la ayuda de la ventana
HUECOS_GENERALES = "{nombre} nombre del archivo original · {hoy} fecha de hoy (AAAA-MM-DD)"
HUECOS_DOCUMENTO = ("{num} número de orden · {tipo} tipo · {clase} clase concreta · {fecha} fecha "
                    "(DD-MM-AAAA) · {titulo} descripción · {parte} órgano o parte · {paginas} páginas")

_HUECO = re.compile(r"\{(\w+)\}")
_PROHIBIDOS = re.compile(r'[\\/:*?"<>|\r\n\t]+')
_RESERVADOS = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
               *(f"LPT{i}" for i in range(1, 10))}
LARGO_MAXIMO = 120


def rellenar(plantilla: str, valores: dict, defecto: str) -> str:
    """Rellena la plantilla y devuelve un nombre válido; si queda vacío, usa la de por defecto."""
    texto = _HUECO.sub(lambda m: str(valores.get(m.group(1), m.group(0))), plantilla or "")
    texto = _PROHIBIDOS.sub(" ", limpiar(texto))
    texto = re.sub(r"\s+", " ", texto)
    texto = re.sub(r"\s-(\s*-)+\s", " - ", texto)  # separadores juntos por un hueco vacío
    texto = texto.strip(" -")[:LARGO_MAXIMO].rstrip(" .")
    if not texto or texto.upper() in _RESERVADOS:
        if plantilla != defecto:
            return rellenar(defecto, valores, defecto)
        return "resultado"
    return texto


@dataclass
class Plantillas:
    carpeta: str = POR_DEFECTO["carpeta"]
    pdf: str = POR_DEFECTO["pdf"]
    excel: str = POR_DEFECTO["excel"]
    documento: str = POR_DEFECTO["documento"]

    @classmethod
    def desde(cls, cfg) -> "Plantillas":
        return cls(cfg.nombre_carpeta, cfg.nombre_pdf, cfg.nombre_excel, cfg.nombre_documento)

    @staticmethod
    def _base(nombre: str) -> dict:
        return {"nombre": nombre, "hoy": datetime.date.today().isoformat()}

    def de_carpeta(self, nombre: str) -> str:
        return rellenar(self.carpeta, self._base(nombre), POR_DEFECTO["carpeta"])

    def de_pdf(self, nombre: str) -> str:
        return rellenar(self.pdf, self._base(nombre), POR_DEFECTO["pdf"]) + ".pdf"

    def de_excel(self, nombre: str) -> str:
        return rellenar(self.excel, self._base(nombre), POR_DEFECTO["excel"]) + ".xlsx"

    def de_documento(self, d: Documento, nombre: str = "") -> str:
        titulo = d.titulo if d.titulo.strip().lower() != d.tipo_nombre.lower() else ""
        valores = dict(self._base(nombre), num=f"{d.numero:03d}", tipo=d.tipo_nombre, clase=d.clase,
                       fecha=d.fecha_es.replace("/", "-"), titulo=titulo, parte=d.emisor,
                       paginas=d.paginas)
        return rellenar(self.documento, valores, POR_DEFECTO["documento"]) + ".pdf"


EJEMPLO = Documento(numero=7, tipo="auto", titulo="Auto de admisión de la demanda", fecha="2025-03-20",
                    autor="Tribunal de Instancia de Valencia", pagina_inicio=12, pagina_fin=13,
                    subtipo="Auto de admisión")
