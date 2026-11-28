"""Acceso directo en el escritorio de Windows.

El programa no tiene instalador: es un único ejecutable. La primera vez que se abre crea
un acceso directo «Marcadores PDF» en el escritorio y, como el nombre del ejecutable cambia
con cada versión, lo vuelve a apuntar al nuevo tras cada actualización. Si el usuario borra
el acceso, no se vuelve a crear.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

NOMBRE = "Marcadores PDF.lnk"

_GUION = r"""
$carpeta = $env:MP_CARPETA
if (-not $carpeta) { $carpeta = [Environment]::GetFolderPath('Desktop') }
$enlace = Join-Path $carpeta $env:MP_NOMBRE
if ($env:MP_FORZAR -eq '1' -or (Test-Path $enlace)) {
  $a = (New-Object -ComObject WScript.Shell).CreateShortcut($enlace)
  $a.TargetPath = $env:MP_EXE
  $a.WorkingDirectory = Split-Path $env:MP_EXE
  $a.IconLocation = "$($env:MP_EXE),0"
  $a.Description = 'Marcadores PDF - clasificación de expedientes'
  $a.Save()
}
"""


def crear(exe: Path, forzar: bool, carpeta: Path | None = None) -> bool:
    """Crea el acceso (forzar) o lo actualiza solo si ya existe. Devuelve si fue bien."""
    if sys.platform != "win32":
        return False
    entorno = dict(os.environ, MP_EXE=str(exe), MP_NOMBRE=NOMBRE, MP_FORZAR="1" if forzar else "0",
                   MP_CARPETA=str(carpeta) if carpeta else "")
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-Command", _GUION],
            env=entorno, capture_output=True, timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def mantener(cfg, guardar) -> None:
    """Se llama al arrancar el ejecutable: crea el acceso la primera vez y lo actualiza después."""
    if not getattr(sys, "frozen", False) or sys.platform != "win32":
        return
    exe = Path(sys.executable)
    if cfg.acceso_directo == str(exe):
        return
    if crear(exe, forzar=not cfg.acceso_directo):
        cfg.acceso_directo = str(exe)
        try:
            guardar(cfg)
        except OSError:
            pass
