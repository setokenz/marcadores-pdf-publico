"""Una sola ventana abierta a la vez.

El ejecutable tarda unos segundos en arrancar (primero se descomprime), y es fácil pulsar
el icono dos veces. Para que no se abran dos ventanas, la primera reserva un nombre en
Windows; si otra arranca y lo encuentra ocupado, trae al frente la que ya estaba y se cierra.
"""
from __future__ import annotations

import sys
import time

NOMBRE = "Local\\MarcadoresPDF-instancia"
_YA_EXISTE = 183  # ERROR_ALREADY_EXISTS
_reserva = None  # se mantiene mientras el programa esté abierto


def _reservar() -> bool | None:
    """True si la reserva es nuestra, False si otra ventana la tiene, None si no se pudo comprobar."""
    global _reserva
    try:
        import ctypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateMutexW.restype = ctypes.c_void_p
        h = k32.CreateMutexW(None, False, NOMBRE)
        if not h:
            return None
        if ctypes.get_last_error() == _YA_EXISTE:
            k32.CloseHandle(ctypes.c_void_p(h))
            return False
        _reserva = h
        return True
    except Exception:
        return None


def _traer_al_frente(prefijo: str) -> None:
    """Muestra la ventana ya abierta cuyo título empieza por `prefijo`."""
    try:
        import ctypes
        from ctypes import wintypes

        u32 = ctypes.WinDLL("user32")
        encontrada = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def mirar(hwnd, _):
            n = u32.GetWindowTextLengthW(hwnd)
            if n and u32.IsWindowVisible(hwnd):
                buf = ctypes.create_unicode_buffer(n + 1)
                u32.GetWindowTextW(hwnd, buf, n + 1)
                clase = ctypes.create_unicode_buffer(64)
                u32.GetClassNameW(hwnd, clase, 64)
                if buf.value.startswith(prefijo) and clase.value == "TkTopLevel":
                    encontrada.append(hwnd)
                    return False
            return True

        u32.EnumWindows(mirar, 0)
        if encontrada:
            u32.ShowWindow(encontrada[0], 9)  # SW_RESTORE
            u32.SetForegroundWindow(encontrada[0])
    except Exception:
        pass


def primera(prefijo_titulo: str, esperar: float = 0.0) -> bool:
    """Devuelve si esta es la única ventana. Si no, trae al frente la otra.

    `esperar` sirve tras una actualización: la versión antigua puede tardar unos segundos
    en cerrarse y no debe confundirse con una ventana abierta por el usuario.
    """
    if sys.platform != "win32":
        return True
    limite = time.monotonic() + esperar
    while True:
        r = _reservar()
        if r is None or r:
            return True
        if time.monotonic() >= limite:
            _traer_al_frente(prefijo_titulo)
            return False
        time.sleep(0.5)
