"""Uso por línea de comandos (sin ventana)."""
from __future__ import annotations

import argparse
from pathlib import Path

from . import config
from .entrada import recoger
from .nombres import Plantillas
from .proceso import carpeta_de, crear_analizador, procesar_varios, resumen_fallos


def main(argv: list[str]) -> int:
    from . import registro

    registro.iniciar()
    cfg = config.cargar()
    ap = argparse.ArgumentParser(prog="MarcadoresPDF", description="Clasifica expedientes judiciales.")
    ap.add_argument("entradas", nargs="+", help="PDF, imágenes o carpetas")
    ap.add_argument("--salida", help="carpeta de salida (por defecto, Documentos\\Marcadores PDF)")
    ap.add_argument("--modo", choices=["gemini", "basico"], default=cfg.modo)
    ap.add_argument("--proyecto", default=cfg.proyecto)
    ap.add_argument("--ubicacion", default=cfg.ubicacion)
    ap.add_argument("--modelo", default=cfg.modelo)
    ap.add_argument("--credenciales", default=cfg.credenciales)
    ap.add_argument("--palabras", help="archivo de texto con las palabras clave")
    ap.add_argument("--separar", action="store_true", help="exportar cada documento en su propio PDF")
    ap.add_argument("--tipo", choices=["auto", "judicial", "extrajudicial"], default=cfg.tipo_expediente,
                    help="tipo de expediente para la cronología (por defecto, se detecta)")
    ap.add_argument("--quitar-blancas", action="store_true", default=cfg.quitar_blancas,
                    help="retirar las páginas en blanco")
    ap.add_argument("--sin-enderezar", action="store_true", help="no enderezar las páginas giradas")
    ap.add_argument("--unir-todo", action="store_true", help="tratar todas las entradas como un solo expediente")
    a = ap.parse_args(argv)
    for campo in ("modo", "proyecto", "ubicacion", "modelo", "credenciales"):
        setattr(cfg, campo, getattr(a, campo))
    palabras = Path(a.palabras).read_text(encoding="utf-8") if a.palabras else cfg.palabras_clave
    expedientes = recoger(a.entradas, a.unir_todo)
    if not expedientes:
        print("No se han encontrado PDF ni imágenes.")
        return 1
    analizador = crear_analizador(cfg)
    fallidos = procesar_varios(
        expedientes, lambda exp: carpeta_de(exp, a.salida or cfg.carpeta_salida, Plantillas.desde(cfg)),
        analizador, plantillas=Plantillas.desde(cfg),
        aviso=lambda m, f: print(f"[{f:4.0%}] {m}"), palabras_clave=palabras,
        capa_texto=cfg.capa_texto, separar=a.separar, indice_en_pdf=cfg.indice_en_pdf,
        tipo_expediente=a.tipo, enderezar=cfg.enderezar and not a.sin_enderezar,
        quitar_blancas=a.quitar_blancas, exportar_rag=cfg.exportar_rag)
    if fallidos:
        print(resumen_fallos(fallidos, len(expedientes)))
        return 1
    return 0
