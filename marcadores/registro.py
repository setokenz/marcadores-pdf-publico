"""Registro de errores en un archivo de texto del equipo.

Anota qué se hizo y el detalle técnico de cada fallo. Nunca guarda el texto de los
documentos: solo números de página, tiempos y mensajes de error.
"""
from __future__ import annotations

import logging
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path

from . import __version__
from .config import ruta_config

log = logging.getLogger("marcadores")


def ruta_registro() -> Path:
    return ruta_config().with_name("registro.log")


def iniciar() -> None:
    if log.handlers:
        return
    try:
        ruta = ruta_registro()
        ruta.parent.mkdir(parents=True, exist_ok=True)
        gestor = RotatingFileHandler(ruta, maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    except OSError:
        return
    gestor.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(gestor)
    log.setLevel(logging.INFO)
    log.info("=== Marcadores PDF %s · Python %s · %s ===", __version__,
             sys.version.split()[0], sys.platform)

    def no_capturado(tipo, valor, traza):
        log.error("Error no capturado", exc_info=(tipo, valor, traza))

    sys.excepthook = no_capturado
    threading.excepthook = lambda a: log.error(
        "Error no capturado en segundo plano", exc_info=(a.exc_type, a.exc_value, a.exc_traceback))
