"""Licencia: firma del testigo, estados y petición al servidor (sin red: servidor simulado)."""
import base64
import io
import json
import urllib.error
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from marcadores import licencia

AHORA = datetime(2026, 12, 1, 10, 0, tzinfo=timezone.utc)
CLAVE = "SUM-" + "0123456789abcdef" * 2


@pytest.fixture
def privada(monkeypatch):
    k = Ed25519PrivateKey.generate()
    publica = k.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    monkeypatch.setattr(licencia, "CLAVE_PUBLICA", base64.b64encode(publica).decode())
    return k


def firmar(k, **cambios):
    """Como el servidor: JSON con claves ordenadas, `<carga b64url>.<firma b64url>`."""
    carga = {"licencia": "lic-1", "estado": "prueba", "edicion": "completa", "puestos": 3,
             "macrosumarios_activos": 0, "perfiles_por_caso": 1, "saldo_disponible_cent": 0,
             "caracteristicas": [], "terminos_pendientes": False, "correo": "ana@ejemplo.test",
             "producto": "marcadores", "emitido": AHORA.isoformat(),
             "expira": (AHORA + timedelta(days=14)).isoformat()}
    carga.update(cambios)
    carga = {c: v for c, v in carga.items() if v is not None}
    cuerpo = json.dumps(dict(sorted(carga.items())), separators=(",", ":")).encode()
    b = lambda x: base64.urlsafe_b64encode(x).decode().rstrip("=")  # noqa: E731
    return b(cuerpo) + "." + b(k.sign(cuerpo))


def test_estados(privada):
    e = licencia.estado(firmar(privada), AHORA)
    assert e.puede_procesar and e.rotulo == "Prueba gratuita" and e.correo == "ana@ejemplo.test"
    assert licencia.estado(firmar(privada, estado="activa"), AHORA).puede_procesar
    for estado in ("solo_lectura", "baja"):
        assert not licencia.estado(firmar(privada, estado=estado), AHORA).puede_procesar
    caducado = licencia.estado(firmar(privada), AHORA + timedelta(days=15))
    assert not caducado.puede_procesar and caducado.rotulo == "Hay que renovar"
    assert not licencia.estado("", AHORA).puede_procesar


def test_rechaza_firma_ajena_y_otro_producto(privada):
    assert licencia.leer_testigo(firmar(privada, producto=None)) is None  # testigo de Sumario
    assert licencia.leer_testigo(firmar(privada, tipo="festivos")) is None
    otra = Ed25519PrivateKey.generate()
    assert licencia.leer_testigo(firmar(otra)) is None
    carga, firma = firmar(privada).split(".")
    manipulada = base64.urlsafe_b64encode(
        base64.urlsafe_b64decode(carga + "==").replace(b'"prueba"', b'"activa"')).decode().rstrip("=")
    assert licencia.leer_testigo(manipulada + "." + firma) is None
    assert licencia.leer_testigo("basura") is None


class Respuesta(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_pedir_testigo(privada):
    enviado = {}

    def abrir(peticion, timeout):
        enviado.update(json.loads(peticion.data), auth=peticion.headers["Authorization"])
        return Respuesta(json.dumps({"testigo": firmar(privada)}).encode())

    testigo = licencia.pedir_testigo(CLAVE, "huella-1", abrir=abrir)
    assert licencia.leer_testigo(testigo)["producto"] == "marcadores"
    assert enviado == {"huella": "huella-1", "producto": "marcadores", "auth": f"Bearer {CLAVE}"}


@pytest.mark.parametrize("codigo, texto", [(401, "no reconoce"), (409, "3 equipos")])
def test_errores_del_servidor(privada, codigo, texto):
    def abrir(peticion, timeout):
        raise urllib.error.HTTPError(peticion.full_url, codigo, "x", {}, io.BytesIO(b"{}"))

    with pytest.raises(licencia.ErrorLicencia, match=texto):
        licencia.pedir_testigo(CLAVE, "h", abrir=abrir)
    with pytest.raises(licencia.ErrorLicencia, match="SUM-"):
        licencia.pedir_testigo("clave-mal", "h", abrir=abrir)


def test_renovar_sin_red_conserva_lo_guardado(privada, monkeypatch):
    from marcadores.config import Config

    cfg = Config(licencia_clave=CLAVE, licencia_testigo=firmar(privada, expira=(
        datetime.now(timezone.utc) + timedelta(days=5)).isoformat()))
    guardados = []

    def sin_red(*a, **k):
        raise licencia.ErrorLicencia("sin conexión")

    monkeypatch.setattr(licencia, "pedir_testigo", sin_red)
    e = licencia.renovar(cfg, guardados.append)
    assert e.puede_procesar and cfg.licencia_huella and guardados  # se creó la huella y se guardó
