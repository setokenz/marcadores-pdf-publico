"""Recogida de archivos de entrada y unión en un único PDF por expediente."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

import pymupdf as fitz

EXT_IMAGEN = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".gif", ".webp"}
EXT_VALIDAS = EXT_IMAGEN | {".pdf"}


_ROMANOS = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}
# «Tomo II», «Pieza-IV»… : el número romano detrás de estas palabras se ordena como número
_CON_ROMANO = re.compile(r"(?i)(tomo|pieza|parte|volumen|vol|anexo|libro|caja|legajo)([\s_.-]*)"
                         r"([ivxlcdm]+)(?![a-z])")


def _romano(texto: str) -> int:
    total, anterior = 0, 0
    for letra in reversed(texto.lower()):
        valor = _ROMANOS[letra]
        total += -valor if valor < anterior else valor
        anterior = max(anterior, valor)
    return total


def _orden_natural(ruta: Path):
    """Clave de orden por nombre: «Tomo 2» antes que «Tomo 10» y «Tomo II» antes que «Tomo IX»."""
    nombre = _CON_ROMANO.sub(lambda m: f"{m.group(1)}{m.group(2)}{_romano(m.group(3))}", ruta.name)
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", nombre)]


@dataclass
class Expediente:
    nombre: str
    rutas: list[Path]
    carpeta: Path  # carpeta de origen (para la salida por defecto)

    def huella(self) -> str:
        h = hashlib.sha1()
        for r in self.rutas:
            with open(r, "rb") as f:
                for bloque in iter(lambda: f.read(1 << 20), b""):
                    h.update(bloque)
        return h.hexdigest()


def recoger(entradas: list[str | Path], unir_todo: bool = False) -> list[Expediente]:
    """Convierte lo que el usuario ha soltado (archivos y carpetas) en expedientes.

    Por defecto cada PDF es un expediente y las imágenes sueltas de una misma
    carpeta forman otro. Con unir_todo, todo se une en un único expediente.
    """
    archivos: list[Path] = []
    for e in entradas:
        p = Path(e)
        if p.is_dir():
            hijos = [h for h in p.rglob("*") if h.is_file() and h.suffix.lower() in EXT_VALIDAS]
            archivos.extend(sorted(hijos, key=lambda h: (str(h.parent).lower(), _orden_natural(h))))
        elif p.is_file() and p.suffix.lower() in EXT_VALIDAS:
            archivos.append(p)
    vistos, unicos = set(), []
    for a in archivos:
        clave = a.resolve()
        if clave not in vistos:
            vistos.add(clave)
            unicos.append(a)
    if not unicos:
        return []
    if unir_todo:
        primera = Path(entradas[0])
        nombre = primera.name if primera.is_dir() else primera.stem
        return [Expediente(nombre, unicos, unicos[0].parent)]
    expedientes: list[Expediente] = []
    grupos_img: dict[Path, Expediente] = {}
    for a in unicos:
        if a.suffix.lower() == ".pdf":
            expedientes.append(Expediente(a.stem, [a], a.parent))
        else:
            if a.parent not in grupos_img:
                grupos_img[a.parent] = Expediente(a.parent.name or "imagenes", [], a.parent)
                expedientes.append(grupos_img[a.parent])
            grupos_img[a.parent].rutas.append(a)
    return expedientes


def unir(rutas: list[Path]) -> fitz.Document:
    """Devuelve un PDF en memoria con todas las páginas, en el orden recibido."""
    salida = fitz.open()
    for r in rutas:
        if r.suffix.lower() == ".pdf":
            with fitz.open(r) as origen:
                if origen.needs_pass:
                    raise ValueError(f"El PDF está protegido con contraseña: {r.name}")
                salida.insert_pdf(origen)
        else:
            with fitz.open(r) as img:
                with fitz.open("pdf", img.convert_to_pdf()) as como_pdf:
                    salida.insert_pdf(como_pdf)
    return salida
