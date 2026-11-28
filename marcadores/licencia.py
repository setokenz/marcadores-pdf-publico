"""Licencia de uso de Marcadores PDF (suscripción en el servidor de licencias de Táctica Legal).

Funcionamiento:
1. El usuario pega la clave de su cuenta (`SUM-` y 32 caracteres) en Configuración → Licencia.
   Es la misma cuenta que la de Sumario; la licencia de Marcadores es aparte.
2. El programa pide al servidor un «testigo»: un justificante firmado (Ed25519) que dice el
   estado de la licencia y vale 14 días sin conexión. La primera vez que una cuenta lo pide
   para Marcadores, el servidor abre la prueba gratuita (30 días, 3 equipos).
3. El testigo se guarda en la configuración y se renueva solo al abrir el programa. Con el
   testigo vigente y la licencia en prueba o activa, se puede procesar.

La firma se comprueba sobre los bytes exactos que firmó el servidor, con su clave pública
(la misma que usa Sumario). El programa es software libre (AGPL): esta comprobación no
pretende ser inviolable; lo que se cobra es la versión oficial con sus actualizaciones.
"""
from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

SERVIDOR = os.environ.get("MARCADORES_URL_LICENCIAS", "https://licencias.abogadoen.casa").rstrip("/")
PORTAL = SERVIDOR + "/portal"
PRODUCTO = "marcadores"
# Clave pública Ed25519 del servidor de licencias (SPKI en base64; no es secreta).
CLAVE_PUBLICA = "MCowBQYDK2VwAyEAShvuEqLuqLo7zKaBATvZGTXsM4FCBBkqy8Q7uKw3zO8="
FORMATO_CLAVE = "SUM-"
ESTADOS_VALIDOS = ("prueba", "activa")


class ErrorLicencia(Exception):
    """Error al pedir la licencia, con un texto para el usuario."""


@dataclass
class Estado:
    puede_procesar: bool
    rotulo: str  # «Prueba gratuita», «Activa», «Sin licencia»…
    detalle: str  # una frase para el usuario
    estado: str = ""  # el del servidor: prueba, activa, solo_lectura, baja
    expira: datetime | None = None
    correo: str = ""
    puestos: int = 0


def nueva_huella() -> str:
    """Identificador de este equipo para el control de puestos (aleatorio, una vez por instalación)."""
    return str(uuid.uuid4())


def clave_valida(clave: str) -> bool:
    clave = clave.strip()
    return clave.startswith(FORMATO_CLAVE) and len(clave) == 36 and all(
        c in "0123456789abcdef" for c in clave[4:])


def _b64(texto: str) -> bytes:
    return base64.urlsafe_b64decode(texto + "=" * (-len(texto) % 4))


def leer_testigo(testigo: str, clave_publica: str | None = None) -> dict | None:
    """Devuelve la carga si la firma es buena y es un testigo de Marcadores; si no, None."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.serialization import load_der_public_key

    try:
        carga_b64, firma_b64 = testigo.strip().split(".")
        cuerpo, firma = _b64(carga_b64), _b64(firma_b64)
        load_der_public_key(base64.b64decode(clave_publica or CLAVE_PUBLICA)).verify(firma, cuerpo)
        carga = json.loads(cuerpo)
    except (ValueError, InvalidSignature, TypeError):
        return None
    if not isinstance(carga, dict) or "tipo" in carga or carga.get("producto") != PRODUCTO:
        return None
    return carga


def _fecha(texto: str) -> datetime | None:
    try:
        return datetime.fromisoformat(texto.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def estado(testigo: str, ahora: datetime | None = None, clave_publica: str | None = None) -> Estado:
    """Qué permite el testigo guardado ahora mismo."""
    ahora = ahora or datetime.now(timezone.utc)
    if not testigo:
        return Estado(False, "Sin licencia", "Active la licencia con la clave de su cuenta para procesar.")
    carga = leer_testigo(testigo, clave_publica)
    if carga is None:
        return Estado(False, "Sin licencia", "La licencia guardada no es válida. Vuelva a activarla.")
    expira = _fecha(carga.get("expira", ""))
    datos = dict(estado=carga.get("estado", ""), expira=expira, correo=carga.get("correo", ""),
                 puestos=int(carga.get("puestos") or 0))
    if expira is None or expira <= ahora:
        return Estado(False, "Hay que renovar", "Han pasado más de 14 días sin comprobar la licencia. "
                      "Conéctese a internet y pulse «Comprobar».", **datos)
    nombre = {"prueba": "Prueba gratuita", "activa": "Activa", "solo_lectura": "Prueba terminada",
              "baja": "Suscripción cancelada"}.get(datos["estado"], "Sin licencia")
    if datos["estado"] in ESTADOS_VALIDOS:
        detalle = f"Válida sin conexión hasta el {expira.astimezone():%d/%m/%Y}."
        if datos["estado"] == "prueba":
            detalle = "Prueba de 30 días para 3 equipos. " + detalle
        return Estado(True, nombre, detalle, **datos)
    return Estado(False, nombre, "Para seguir procesando, suscríbase desde el portal de su cuenta.", **datos)


def pedir_testigo(clave: str, huella: str, abrir=urllib.request.urlopen, tiempo: float = 20) -> str:
    """Pide un testigo nuevo al servidor. Lanza ErrorLicencia con un texto claro si no se puede."""
    clave = clave.strip()
    if not clave_valida(clave):
        raise ErrorLicencia("La clave de la cuenta empieza por «SUM-» y tiene 36 caracteres. "
                            "Cópiela del portal de su cuenta.")
    peticion = urllib.request.Request(
        SERVIDOR + "/v1/testigo", method="POST",
        data=json.dumps({"huella": huella, "producto": PRODUCTO}).encode(),
        headers={"Authorization": f"Bearer {clave}", "Content-Type": "application/json",
                 "User-Agent": "MarcadoresPDF"})
    try:
        with abrir(peticion, timeout=tiempo) as r:
            testigo = json.loads(r.read().decode()).get("testigo", "")
    except urllib.error.HTTPError as e:
        textos = {401: "El servidor no reconoce esa clave. Compruébela en el portal de su cuenta.",
                  409: "La licencia ya está en uso en 3 equipos. Libere uno desde el portal de su cuenta.",
                  429: "Demasiados intentos seguidos. Espere un minuto y vuelva a probar."}
        raise ErrorLicencia(textos.get(e.code, f"El servidor de licencias ha respondido con un error ({e.code}).")) from e
    except (urllib.error.URLError, OSError, TimeoutError, ValueError) as e:
        raise ErrorLicencia("No se ha podido conectar con el servidor de licencias. "
                            "Compruebe la conexión a internet.") from e
    if leer_testigo(testigo) is None:
        raise ErrorLicencia("El servidor ha devuelto una licencia que no se puede verificar.")
    return testigo


def renovar(cfg, guardar) -> Estado:
    """Al arrancar: si hay clave, pide un testigo nuevo; si falla la red, sigue valiendo el guardado."""
    if not cfg.licencia_huella:
        cfg.licencia_huella = nueva_huella()
        guardar(cfg)
    if cfg.licencia_clave:
        try:
            cfg.licencia_testigo = pedir_testigo(cfg.licencia_clave, cfg.licencia_huella)
            guardar(cfg)
        except ErrorLicencia:
            pass
    return estado(cfg.licencia_testigo)
