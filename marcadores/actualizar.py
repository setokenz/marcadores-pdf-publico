"""Detección e instalación de versiones nuevas publicadas en GitHub."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import __version__

# Primero el repositorio público de descargas (sin token); si aún no existe, el privado.
REPOSITORIO_PUBLICO = "setokenz/marcadores-pdf-publico"
REPOSITORIO = "setokenz/marcadores-pdf"
REPOSITORIOS = (REPOSITORIO_PUBLICO, REPOSITORIO)


def _api(repositorio: str) -> str:
    return f"https://api.github.com/repos/{repositorio}/releases/latest"


API = _api(REPOSITORIO)
NOMBRE_EXE = "MarcadoresPDF.exe"
ARG_BORRAR = "--borrar-anterior"


class ErrorActualizacion(Exception):
    pass


@dataclass
class Version:
    numero: str
    url: str  # dirección de la API del archivo publicado
    tamano: int
    notas: str = ""
    nombre: str = NOMBRE_EXE  # nombre del archivo publicado
    privado: bool = True  # si viene del repositorio privado (hace falta el token para descargarla)


def _tupla(v: str) -> tuple[int, ...]:
    partes = []
    for trozo in v.strip().lstrip("vV").split("."):
        digitos = "".join(c for c in trozo if c.isdigit())
        partes.append(int(digitos) if digitos else 0)
    return tuple(partes)


def es_mas_nueva(candidata: str, actual: str = __version__) -> bool:
    return _tupla(candidata) > _tupla(actual)


def _cabeceras(token: str, tipo: str) -> dict:
    c = {"Accept": tipo, "User-Agent": "MarcadoresPDF", "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        c["Authorization"] = f"Bearer {token}"
    return c


class _SinRedireccion(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def buscar(token: str = "", abrir=urllib.request.urlopen) -> Version | None:
    """Devuelve la versión publicada si es más nueva que la instalada; si no, None."""
    ultimo_error = None
    for repositorio in REPOSITORIOS:
        publico = repositorio == REPOSITORIO_PUBLICO
        try:
            pet = urllib.request.Request(_api(repositorio), headers=_cabeceras(
                "" if publico else token, "application/vnd.github+json"))
            with abrir(pet, timeout=15) as r:
                datos = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code in (401, 403, 404):
                ultimo_error = ErrorActualizacion(
                    "No se puede consultar la última versión. Si el repositorio de GitHub es privado, "
                    "indique un token de lectura en «Configuración» o hágalo público.")
                continue  # el público aún no existe, o el privado necesita token
            raise ErrorActualizacion(f"GitHub respondió con el error {e.code}.") from e
        except Exception as e:
            raise ErrorActualizacion(f"Sin conexión con GitHub ({type(e).__name__}).") from e
        return _version(datos, repositorio, privado=not publico)
    raise ultimo_error


def _version(datos: dict, repositorio: str, privado: bool) -> Version | None:
    numero = str(datos.get("tag_name", ""))
    if not es_mas_nueva(numero):
        return None
    limpio = numero.lstrip("vV")
    archivos = {str(x.get("name")): x for x in datos.get("assets", [])}
    # se prefiere el ejecutable que lleva la versión en el nombre
    for nombre in (f"MarcadoresPDF-{limpio}.exe", NOMBRE_EXE):
        archivo = archivos.get(nombre)
        if archivo:
            url = str(archivo.get("url", ""))
            if not url.startswith(f"https://api.github.com/repos/{repositorio}/"):
                raise ErrorActualizacion("La dirección de descarga no es la esperada.")
            return Version(limpio, url, int(archivo.get("size", 0)),
                           str(datos.get("body") or ""), nombre, privado)
    return None


REINTENTOS_DESCARGA = 5
ESPERA_LECTURA = 60  # segundos sin recibir datos antes de dar el trozo por perdido


def _abrir_descarga(version: Version, token: str, desde: int):
    """Abre la descarga (siguiendo la redirección de GitHub), a partir del byte `desde`."""
    cabeceras_api = _cabeceras(token, "application/octet-stream")
    if desde:
        cabeceras_api["Range"] = f"bytes={desde}-"
    pet = urllib.request.Request(version.url, headers=cabeceras_api)
    abridor = urllib.request.build_opener(_SinRedireccion)
    try:
        respuesta = abridor.open(pet, timeout=ESPERA_LECTURA)
    except urllib.error.HTTPError as e:
        if e.code not in (301, 302, 303, 307, 308) or not e.headers.get("Location"):
            raise
        # GitHub redirige a su almacén de archivos; allí no debe enviarse el token.
        cabeceras = {"User-Agent": "MarcadoresPDF"}
        if desde:
            cabeceras["Range"] = f"bytes={desde}-"
        respuesta = urllib.request.urlopen(urllib.request.Request(e.headers["Location"], headers=cabeceras),
                                           timeout=ESPERA_LECTURA)
    return respuesta


def descargar(version: Version, destino: Path, token: str = "",
              progreso: Callable[[float], None] = lambda f: None, esperar=None) -> None:
    """Descarga el ejecutable. Si la conexión se corta, reintenta y continúa donde se quedó."""
    import time

    from .registro import log

    esperar = esperar or time.sleep
    token = token if version.privado else ""  # el token nunca se envía al repositorio público
    leido = 0
    ultimo_error = None
    destino.unlink(missing_ok=True)
    for intento in range(1, REINTENTOS_DESCARGA + 1):
        try:
            respuesta = _abrir_descarga(version, token, leido)
            continua = leido and getattr(respuesta, "status", 200) == 206
            if leido and not continua:  # el servidor no admite continuar: se empieza de cero
                leido = 0
            with respuesta, open(destino, "ab" if continua else "wb") as f:
                while True:
                    bloque = respuesta.read(1 << 18)
                    if not bloque:
                        break
                    f.write(bloque)
                    leido += len(bloque)
                    if version.tamano:
                        progreso(min(1.0, leido / version.tamano))
            if not version.tamano or leido >= version.tamano:
                break
            ultimo_error = ErrorActualizacion(f"la conexión se cerró en {leido} de {version.tamano} bytes")
        except Exception as e:  # corte de red, tiempo de espera, error del servidor…
            ultimo_error = e
        log.warning("Actualización %s: descarga interrumpida (%s); %d de %s bytes; intento %d de %d",
                    version.numero, ultimo_error, leido, version.tamano or "?", intento, REINTENTOS_DESCARGA)
        if intento < REINTENTOS_DESCARGA:
            esperar(min(30, 2 ** intento))
    else:
        destino.unlink(missing_ok=True)
        raise ErrorActualizacion(f"No se pudo descargar la actualización tras {REINTENTOS_DESCARGA} "
                                 f"intentos ({type(ultimo_error).__name__}: {ultimo_error}).")
    with open(destino, "rb") as f:
        cabecera = f.read(2)
    if cabecera != b"MZ" or (version.tamano and destino.stat().st_size != version.tamano):
        destino.unlink(missing_ok=True)
        raise ErrorActualizacion("La descarga está incompleta o dañada. Inténtelo de nuevo.")
    log.info("Actualización %s descargada (%d bytes)", version.numero, leido)


def es_ejecutable() -> bool:
    return bool(getattr(sys, "frozen", False)) and sys.platform == "win32"


def instalar(version: Version, token: str = "",
             progreso: Callable[[float], None] = lambda f: None, exe: Path | None = None) -> Path:
    """Descarga la versión nueva junto al programa actual y devuelve su ruta.

    Si el archivo publicado lleva la versión en el nombre, se guarda con ese nombre y el
    programa antiguo se borra al arrancar el nuevo. Si se llama igual que el actual, se
    sustituye: Windows permite renombrar un programa en marcha, así que el actual pasa a
    «.old» y el nuevo ocupa su sitio. Si algo falla, todo queda como estaba.
    """
    exe = exe or Path(sys.executable)
    destino = exe.with_name(version.nombre)
    if destino.name.lower() != exe.name.lower():
        try:
            descargar(version, destino, token, progreso)
        except PermissionError as e:
            raise ErrorActualizacion(f"No hay permiso para escribir en {exe.parent}.") from e
        return destino
    nuevo = exe.with_name(exe.stem + ".new.exe")
    viejo = exe.with_name(exe.stem + ".old.exe")
    try:
        descargar(version, nuevo, token, progreso)
    except PermissionError as e:
        raise ErrorActualizacion(f"No hay permiso para escribir en {exe.parent}.") from e
    try:
        viejo.unlink(missing_ok=True)
        os.replace(exe, viejo)
        try:
            os.replace(nuevo, exe)
        except OSError:
            os.replace(viejo, exe)  # deshacer
            raise
    except OSError as e:
        nuevo.unlink(missing_ok=True)
        raise ErrorActualizacion(f"No se pudo sustituir el programa ({e}).") from e
    return exe


def reiniciar(nuevo: Path, actual: Path | None = None) -> None:
    """Arranca la versión nueva; si es otro archivo, le pide que borre el antiguo."""
    actual = actual or Path(sys.executable)
    orden = [str(nuevo)]
    if nuevo.resolve() != actual.resolve():
        orden += [ARG_BORRAR, str(actual)]
    entorno = dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1")
    subprocess.Popen(orden, env=entorno, close_fds=True,
                     creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))


def borrar_anterior(ruta: str, exe: Path | None = None, intentos: int = 20, espera: float = 1.0) -> bool:
    """Borra el ejecutable de la versión anterior (espera a que se haya cerrado).

    Por seguridad solo borra un MarcadoresPDF*.exe de la misma carpeta que el programa.
    """
    import time

    exe = exe or Path(sys.executable)
    viejo = Path(ruta)
    valido = (viejo.parent.resolve() == exe.parent.resolve() and viejo.resolve() != exe.resolve()
              and viejo.name.lower().startswith("marcadorespdf") and viejo.suffix.lower() == ".exe")
    if not valido:
        return False
    for _ in range(intentos):
        try:
            viejo.unlink(missing_ok=True)
            return True
        except OSError:
            time.sleep(espera)
    return False


def limpiar_restos() -> None:
    """Borra el ejecutable antiguo que queda tras una actualización."""
    if es_ejecutable():
        exe = Path(sys.executable)
        for resto in (exe.with_name(exe.stem + ".old.exe"), exe.with_name(exe.stem + ".new.exe")):
            try:
                resto.unlink(missing_ok=True)
            except OSError:
                pass
