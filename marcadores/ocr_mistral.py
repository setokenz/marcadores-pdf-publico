"""OCR de las páginas escaneadas con Mistral OCR.

Mistral convierte cada página en texto; después Gemini solo clasifica a partir de ese texto,
que es mucho más rápido que pedirle que transcriba imágenes.

El formato de los recuadros por bloque que devuelve Mistral no está documentado con detalle
(no se ha podido verificar), así que se interpreta de forma tolerante: si no se reconocen, el
texto se añade al PDF sin posición (sigue siendo buscable).
"""
from __future__ import annotations

import base64
import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

import pymupdf as fitz

from .modelos import limpiar
from .registro import log

SERVIDOR_UE = "https://api.eu.mistral.ai"
SERVIDOR_GLOBAL = "https://api.mistral.ai"
MODELO = "mistral-ocr-latest"
# Modelo anterior de OCR: en la tabla oficial de Mistral cuesta la mitad por página que el
# actual (consultado el 28-11-2026). «Comparar modelos de OCR» permite ver si lee igual de bien.
MODELO_ANTERIOR = "mistral-ocr-2512"
PAGINAS_POR_PETICION = 50  # Mistral admite lotes de 50 páginas (comprobado por el usuario)
# Tamaño máximo por petición: no verificado en la documentación. Si Mistral responde que es
# demasiado grande (413), la petición se parte en dos y se repite.
BYTES_POR_PETICION = 20_000_000
ESPERA_MAXIMA = 300
ESPERAS = (5, 15, 30, 60)  # reintentos ante saturación o fallos pasajeros

# Modo económico: API de lotes de Mistral (documentación oficial, «Batch Processing»: admite
# /v1/ocr, un 50 % más barato, asíncrono y sin plazo garantizado; el trabajo caduca a las
# `timeout_hours`, 24 por defecto y menos de 7 días). Las peticiones van dentro de la orden
# («inline batching», para menos de 10.000), sin subir los documentos como archivos.
INTERVALO_CONSULTA = 30  # segundos entre cada comprobación del estado
HORAS_MAXIMAS = 24
ESTADOS_FIN = {"SUCCESS", "FAILED", "TIMEOUT_EXCEEDED", "CANCELLED"}
NOMBRE_ESTADO = {"QUEUED": "en cola", "RUNNING": "en curso", "CANCELLATION_REQUESTED": "cancelándose"}


class ConfiguracionMistral(ValueError):
    """Clave o servidor incorrectos: fallaría igual en todos los expedientes, así que detiene el lote."""

    def __init__(self, codigo: int, mensaje: str):
        super().__init__(mensaje)
        self.code = codigo


class ErrorMistral(Exception):
    def __init__(self, codigo: int | None, mensaje: str):
        super().__init__(mensaje)
        self.code = codigo


def _enviar(url: str, clave: str, cuerpo: dict, tiempo: int = ESPERA_MAXIMA) -> dict:
    """Petición de OCR a Mistral. Aparte para poder sustituirla en las pruebas."""
    return _http("POST", url, clave, cuerpo, tiempo)


def _http(metodo: str, url: str, clave: str, cuerpo: dict | None = None, tiempo: int = 120,
          crudo: bool = False):
    """Petición HTTP a Mistral: devuelve el JSON de la respuesta (o el texto, si `crudo`)."""
    datos = json.dumps(cuerpo).encode("utf-8") if cuerpo is not None else None
    cabeceras = {"Authorization": f"Bearer {clave}", "Accept": "application/json",
                 "User-Agent": "MarcadoresPDF"}
    if datos is not None:
        cabeceras["Content-Type"] = "application/json"
    peticion = urllib.request.Request(url, data=datos, method=metodo, headers=cabeceras)
    try:
        with urllib.request.urlopen(peticion, timeout=tiempo) as r:
            texto = r.read().decode("utf-8")
            if crudo:
                return texto
            return json.loads(texto) if texto.strip() else {}
    except urllib.error.HTTPError as e:
        try:
            detalle = e.read().decode("utf-8", "replace")[:500]
        except Exception:
            detalle = ""
        raise ErrorMistral(e.code, f"HTTP {e.code}: {detalle}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise ErrorMistral(None, f"{type(e).__name__}: {e}") from e


# --- interpretación de la respuesta ----------------------------------------
_IMAGEN_MD = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_SEPARADOR_TABLA = re.compile(r"^\s*\|?\s*:?-{3,}.*$")


def markdown_a_texto(md: str) -> str:
    """Texto llano a partir del markdown de Mistral (sin imágenes ni marcas de formato)."""
    lineas = []
    for linea in (md or "").splitlines():
        if _SEPARADOR_TABLA.match(linea):
            continue
        linea = _IMAGEN_MD.sub("", linea)
        linea = re.sub(r"^\s{0,3}#{1,6}\s*", "", linea)
        linea = linea.replace("**", "").replace("__", "")
        if linea.strip().startswith("|"):
            linea = " | ".join(c.strip() for c in linea.strip().strip("|").split("|"))
        lineas.append(linea.rstrip())
    return limpiar(re.sub(r"\n{3,}", "\n\n", "\n".join(lineas)).strip())


def _numero(valor):
    try:
        return float(valor)
    except (TypeError, ValueError):
        return None


def _caja(bloque: dict) -> tuple[float, float, float, float] | None:
    """(x0, y0, x1, y1) en las unidades que traiga el bloque, si se reconoce el formato."""
    for fuente in (bloque.get("bbox"), bloque.get("bounding_box"), bloque.get("box"), bloque):
        if isinstance(fuente, (list, tuple)) and len(fuente) == 4:
            v = [_numero(x) for x in fuente]
            if None not in v:
                return v[0], v[1], v[2], v[3]
        if isinstance(fuente, dict):
            v = [_numero(fuente.get(k)) for k in ("top_left_x", "top_left_y", "bottom_right_x", "bottom_right_y")]
            if None not in v:
                return v[0], v[1], v[2], v[3]
            v = [_numero(fuente.get(k)) for k in ("x0", "y0", "x1", "y1")]
            if None not in v:
                return v[0], v[1], v[2], v[3]
            v = [_numero(fuente.get(k)) for k in ("x", "y", "width", "height")]
            if None not in v:
                return v[0], v[1], v[0] + v[2], v[1] + v[3]
    return None


def lineas_de_bloques(pagina: dict) -> list[list]:
    """Bloques de Mistral -> [[texto, ymin, xmin, ymax, xmax]] en milésimas de página."""
    bloques = pagina.get("blocks")
    if not isinstance(bloques, list):
        return []
    dim = pagina.get("dimensions") or {}
    ancho, alto = _numero(dim.get("width")), _numero(dim.get("height"))
    salida = []
    for b in bloques:
        if not isinstance(b, dict):
            continue
        texto = next((b[k] for k in ("text", "content", "markdown") if isinstance(b.get(k), str)), "")
        texto = markdown_a_texto(texto)
        caja = _caja(b)
        if not texto or not caja:
            continue
        x0, y0, x1, y1 = caja
        if max(x0, y0, x1, y1) <= 1.0:  # coordenadas normalizadas 0-1
            ancho_u, alto_u = 1.0, 1.0
        elif ancho and alto:  # píxeles de la imagen de la página
            ancho_u, alto_u = ancho, alto
        else:
            continue
        fila = [texto, round(y0 / alto_u * 1000), round(x0 / ancho_u * 1000),
                round(y1 / alto_u * 1000), round(x1 / ancho_u * 1000)]
        if fila[3] > fila[1] and fila[4] > fila[2]:
            salida.append([fila[0]] + [max(0, min(1000, n)) for n in fila[1:]])
    return salida


def _trozo(pdf: fitz.Document, grupo: list[int]) -> bytes:
    with fitz.open() as trozo:  # el PyMuPDF se usa solo en este hilo
        for i in grupo:
            trozo.insert_pdf(pdf, from_page=i, to_page=i)
        return trozo.tobytes(garbage=3, deflate=True)


def _anotar(respuesta: dict, grupo: list[int], hechas: dict) -> None:
    """Guarda en `hechas` el texto de cada página de una respuesta de OCR."""
    paginas = respuesta.get("pages") or []
    primera = next((p for p in paginas if isinstance(p, dict) and p.get("blocks")), None)
    if primera:  # para comprobar el formato real en el registro (sin texto de los documentos)
        log.info("Mistral: claves de página %s; claves de bloque %s", sorted(primera),
                 sorted(primera["blocks"][0]) if isinstance(primera["blocks"][0], dict) else "?")
    for n, pagina in enumerate(paginas):
        if not isinstance(pagina, dict):
            continue
        pos = pagina.get("index", n)
        if not isinstance(pos, int) or not 0 <= pos < len(grupo):
            pos = n
        if pos < len(grupo):
            hechas[grupo[pos] + 1] = (markdown_a_texto(pagina.get("markdown", "")),
                                      lineas_de_bloques(pagina))


def _dormir(segundos: float, cancelar) -> None:
    fin = time.monotonic() + segundos
    while True:
        if cancelar is not None and cancelar.is_set():
            from .ocr_gemini import ConsultaCancelada

            raise ConsultaCancelada()
        if time.monotonic() >= fin:
            return
        time.sleep(min(1.0, max(0.0, fin - time.monotonic())))


# --- lectura ---------------------------------------------------------------
class OCRMistral:
    def __init__(self, clave: str, servidor: str = SERVIDOR_UE, modelo: str = MODELO,
                 economico: bool = False, horas: int = HORAS_MAXIMAS):
        if not clave:
            raise ValueError("Falta la clave de API de Mistral (Configurar Vertex… → OCR).")
        self.clave = clave
        self.servidor = servidor.rstrip("/")
        self.url = self.servidor + "/v1/ocr"
        self.economico = economico
        self.horas = max(1, min(167, int(horas or HORAS_MAXIMAS)))
        self.modelo = modelo or MODELO
        self.con_bloques = True
        self.firma = f"mistral:{self.modelo}"

    def _cuerpo(self, pdf_bytes: bytes) -> dict:
        documento = "data:application/pdf;base64," + base64.b64encode(pdf_bytes).decode("ascii")
        cuerpo = {"include_image_base64": False,
                  "document": {"type": "document_url", "document_url": documento}}
        if self.con_bloques:
            cuerpo["include_blocks"] = True
        return cuerpo

    def _pedir(self, pdf_bytes: bytes, paginas: str, cancelar=None, aviso=lambda m: None) -> dict:
        documento = "data:application/pdf;base64," + base64.b64encode(pdf_bytes).decode("ascii")
        intento = 0
        while True:
            if cancelar is not None and cancelar.is_set():
                from .ocr_gemini import ConsultaCancelada

                raise ConsultaCancelada()
            cuerpo = {"model": self.modelo, "include_image_base64": False,
                      "document": {"type": "document_url", "document_url": documento}}
            if self.con_bloques:
                cuerpo["include_blocks"] = True
            inicio = time.monotonic()
            try:
                respuesta = _enviar(self.url, self.clave, cuerpo)
                log.info("Mistral, páginas %s: respuesta en %.0f s", paginas, time.monotonic() - inicio)
                return respuesta
            except ErrorMistral as e:
                log.warning("Mistral, páginas %s: fallo a los %.0f s: %s", paginas,
                            time.monotonic() - inicio, str(e)[:300])
                if e.code in (400, 422) and self.con_bloques and "block" in str(e).lower():
                    self.con_bloques = False  # el servidor no admite los recuadros: sin ellos
                    log.info("Mistral no admite include_blocks; se sigue sin posiciones")
                    continue
                if e.code in (401, 403):
                    raise ConfiguracionMistral(e.code, "Mistral rechaza la clave de API (error "
                                               f"{e.code}). Revísela en «Configurar Vertex…».") from e
                if e.code == 404:
                    raise ConfiguracionMistral(404, f"El servidor {self.url} no ofrece el OCR de Mistral "
                                               "(404). Pruebe el servidor global en «Configurar Vertex…».") from e
                if intento >= len(ESPERAS) or (e.code is not None and e.code < 500 and e.code != 429):
                    raise
                espera = ESPERAS[intento]
                intento += 1
                aviso(f"Mistral, páginas {paginas}: {str(e)[:80]}. Reintento {intento} de "
                      f"{len(ESPERAS)} en {espera} s…")
                for _ in range(espera):
                    if cancelar is not None and cancelar.is_set():
                        break
                    time.sleep(1)

    def leer(self, pdf: fitz.Document, indices: list[int], cache: Path, huella: str,
             aviso=lambda m: None, cancelar=None,
             respaldo: str = "irán a Gemini como imagen") -> dict[int, tuple[str, list]]:
        """Devuelve {página (1..n): (texto, líneas)} y guarda lo leído para no pagarlo dos veces."""
        hechas, trabajos = self._cargar(cache, huella)
        pendientes = [i for i in indices if (i + 1) not in hechas]
        if hechas and pendientes != indices:
            aviso(f"OCR con Mistral: {len(indices) - len(pendientes)} páginas ya leídas en una pasada anterior.")

        def guardar():
            self._guardar(cache, huella, hechas, trabajos)

        if self.economico or trabajos:  # también recoge lo encargado antes en modo económico
            if self.economico:
                self._encargar(pdf, pendientes, hechas, trabajos, guardar, aviso, cancelar)
            self._recoger(hechas, trabajos, guardar, aviso, cancelar, len(indices))
            pendientes = [i for i in indices if (i + 1) not in hechas]
            if pendientes:
                aviso(f"Mistral (modo económico) no ha devuelto {len(pendientes)} páginas; "
                      "se leen ahora en modo normal.")

        grupos = [pendientes[n:n + PAGINAS_POR_PETICION]
                  for n in range(0, len(pendientes), PAGINAS_POR_PETICION)]
        while grupos:
            grupo = grupos.pop(0)
            contenido = _trozo(pdf, grupo)
            if len(contenido) > BYTES_POR_PETICION and len(grupo) > 1:
                mitad = len(grupo) // 2
                grupos[:0] = [grupo[:mitad], grupo[mitad:]]
                continue
            tramo = f"{grupo[0] + 1}-{grupo[-1] + 1}" if len(grupo) > 1 else str(grupo[0] + 1)
            aviso(f"OCR con Mistral: páginas {tramo} ({len(hechas)} de {len(indices)} leídas)…")
            try:
                respuesta = self._pedir(contenido, tramo, cancelar, aviso)
            except ErrorMistral as e:
                demasiado = e.code == 413 or "too large" in str(e).lower()
                if demasiado and len(grupo) > 1:
                    mitad = len(grupo) // 2
                    log.info("Mistral: petición de %d páginas demasiado grande; se parte en dos", len(grupo))
                    grupos[:0] = [grupo[:mitad], grupo[mitad:]]
                    continue
                # Fallo persistente: estas páginas las leerá Gemini como imagen.
                aviso(f"Mistral no ha podido leer las páginas {tramo} ({str(e)[:80]}); {respaldo}.")
                continue
            _anotar(respuesta, grupo, hechas)
            guardar()
        return {k: v for k, v in hechas.items() if (k - 1) in indices}

    # --- caché -------------------------------------------------------------
    def _cargar(self, cache: Path, huella: str) -> tuple[dict[int, tuple[str, list]], list[dict]]:
        try:
            datos = json.loads(cache.read_text(encoding="utf-8"))
            if datos.get("huella") == huella and datos.get("firma") == self.firma:
                hechas = {int(k): (v["texto"], v["lineas"]) for k, v in datos["paginas"].items()}
                trabajos = [t for t in datos.get("trabajos", [])
                            if isinstance(t, dict) and t.get("id") and t.get("paginas")]
                return hechas, trabajos
        except Exception:
            pass
        return {}, []

    def _guardar(self, cache: Path, huella: str, hechas: dict, trabajos: list[dict]) -> None:
        cache.write_text(json.dumps({"huella": huella, "firma": self.firma, "trabajos": trabajos,
                                     "paginas": {str(k): {"texto": t, "lineas": l}
                                                 for k, (t, l) in sorted(hechas.items())}},
                                    ensure_ascii=False), encoding="utf-8")

    # --- modo económico (lotes) -------------------------------------------
    def encargar(self, pdf: fitz.Document, indices: list[int], cache: Path, huella: str,
                 aviso=lambda m: None, cancelar=None) -> int:
        """Solo encarga los trabajos (sin esperar): así, con varios expedientes, Mistral los
        procesa todos a la vez en lugar de uno tras otro. Devuelve cuántos hay encargados."""
        hechas, trabajos = self._cargar(cache, huella)
        pendientes = [i for i in indices if (i + 1) not in hechas]
        self._encargar(pdf, pendientes, hechas, trabajos,
                       lambda: self._guardar(cache, huella, hechas, trabajos), aviso, cancelar)
        return len(trabajos)

    def _encargar(self, pdf, pendientes, hechas, trabajos, guardar, aviso, cancelar) -> None:
        encargadas = {i for t in trabajos for i in t["paginas"]}
        pendientes = [i for i in pendientes if i not in encargadas]
        grupos = [pendientes[n:n + PAGINAS_POR_PETICION]
                  for n in range(0, len(pendientes), PAGINAS_POR_PETICION)]
        while grupos and self.economico:
            if cancelar is not None and cancelar.is_set():
                from .ocr_gemini import ConsultaCancelada

                raise ConsultaCancelada()
            grupo = grupos.pop(0)
            contenido = _trozo(pdf, grupo)
            if len(contenido) > BYTES_POR_PETICION and len(grupo) > 1:
                mitad = len(grupo) // 2
                grupos[:0] = [grupo[:mitad], grupo[mitad:]]
                continue
            tramo = f"{grupo[0] + 1}-{grupo[-1] + 1}" if len(grupo) > 1 else str(grupo[0] + 1)
            aviso(f"Modo económico: encargando a Mistral las páginas {tramo}…")
            try:
                ident = self._crear_trabajo(contenido, tramo, cancelar, aviso)
            except ErrorMistral as e:
                if (e.code == 413 or "too large" in str(e).lower()) and len(grupo) > 1:
                    mitad = len(grupo) // 2
                    grupos[:0] = [grupo[:mitad], grupo[mitad:]]
                    continue
                aviso(f"Mistral no ha aceptado el encargo de las páginas {tramo} ({str(e)[:80]}); "
                      "se leerán en modo normal.")
                continue
            if ident:
                trabajos.append({"id": ident, "paginas": grupo, "creado": int(time.time())})
                guardar()

    def _crear_trabajo(self, contenido: bytes, tramo: str, cancelar, aviso) -> str | None:
        orden = {"model": self.modelo, "endpoint": "/v1/ocr", "timeout_hours": self.horas,
                 "metadata": {"programa": "MarcadoresPDF", "paginas": tramo},
                 "requests": [{"custom_id": "0", "body": self._cuerpo(contenido)}]}
        intento = 0
        while True:
            try:
                trabajo = _http("POST", self.servidor + "/v1/batch/jobs", self.clave, orden, ESPERA_MAXIMA)
                ident = trabajo.get("id")
                log.info("Mistral (lotes), páginas %s: trabajo %s, estado %s", tramo, ident,
                         trabajo.get("status"))
                return ident
            except ErrorMistral as e:
                log.warning("Mistral (lotes), páginas %s: fallo al encargar: %s", tramo, str(e)[:300])
                if e.code in (401, 403):
                    raise ConfiguracionMistral(e.code, "Mistral rechaza la clave de API (error "
                                               f"{e.code}). Revísela en «Configurar Vertex…».") from e
                if e.code == 404:  # este servidor no ofrece lotes: se sigue en modo normal
                    self.economico = False
                    aviso("El servidor de Mistral elegido no ofrece el modo económico; "
                          "se sigue en modo normal.")
                    return None
                if intento >= len(ESPERAS) or (e.code is not None and e.code < 500 and e.code != 429):
                    raise
                espera = ESPERAS[intento]
                intento += 1
                aviso(f"Mistral, encargo de las páginas {tramo}: {str(e)[:80]}. Reintento en {espera} s…")
                _dormir(espera, cancelar)

    def _recoger(self, hechas, trabajos, guardar, aviso, cancelar, total: int) -> None:
        """Espera a que terminen los trabajos encargados y guarda lo que devuelvan."""
        if not trabajos:
            return
        inicio = time.monotonic()
        limite = inicio + (self.horas + 1) * 3600
        while trabajos:
            estados = []
            for t in list(trabajos):
                try:
                    trabajo = _http("GET", f"{self.servidor}/v1/batch/jobs/{t['id']}?inline=true",
                                    self.clave)
                except ErrorMistral as e:
                    if e.code in (401, 403):
                        raise ConfiguracionMistral(e.code, "Mistral rechaza la clave de API (error "
                                                   f"{e.code}). Revísela en «Configurar Vertex…».") from e
                    if e.code == 404:  # el trabajo ya no existe: esas páginas, en modo normal
                        log.warning("Mistral (lotes): el trabajo %s ya no existe", t["id"])
                        trabajos.remove(t)
                        guardar()
                    else:
                        log.warning("Mistral (lotes): no se pudo consultar %s: %s", t["id"], str(e)[:200])
                        estados.append("sin respuesta")
                    continue
                estado = str(trabajo.get("status", "")).upper()
                if estado not in ESTADOS_FIN:
                    estados.append(NOMBRE_ESTADO.get(estado, estado.lower() or "?"))
                    continue
                log.info("Mistral (lotes): trabajo %s terminado: %s (%s de %s peticiones bien)",
                         t["id"], estado, trabajo.get("succeeded_requests"), trabajo.get("total_requests"))
                if estado == "SUCCESS":
                    for respuesta in self._resultados(trabajo):
                        _anotar(respuesta, t["paginas"], hechas)
                self._borrar_archivos(trabajo)
                trabajos.remove(t)
                guardar()
            if not trabajos:
                break
            if time.monotonic() > limite:
                log.warning("Mistral (lotes): se deja de esperar tras %d h", self.horas + 1)
                trabajos.clear()
                guardar()
                break
            minutos = int((time.monotonic() - inicio) // 60)
            resumen = ", ".join(f"{estados.count(e)} {e}" for e in dict.fromkeys(estados))
            aviso(f"Modo económico: {len(trabajos)} encargos esperando a Mistral ({resumen}); "
                  f"{len(hechas)} de {total} páginas listas; {minutos} min de espera. Puede cerrar el "
                  "programa: al volver a procesar el expediente se recoge lo hecho.")
            _dormir(INTERVALO_CONSULTA, cancelar)

    def _resultados(self, trabajo: dict) -> list[dict]:
        """Respuestas de OCR de un trabajo terminado (formato tolerante: no verificado al detalle)."""
        filas = trabajo.get("outputs")
        if not isinstance(filas, list) or not filas:
            filas = []
            archivo = trabajo.get("output_file")
            if archivo:
                try:
                    texto = _http("GET", f"{self.servidor}/v1/files/{archivo}/content", self.clave,
                                  crudo=True, tiempo=ESPERA_MAXIMA)
                    filas = [json.loads(linea) for linea in texto.splitlines() if linea.strip()]
                except (ErrorMistral, ValueError) as e:
                    log.warning("Mistral (lotes): no se pudo descargar el resultado: %s", str(e)[:200])
        if filas and isinstance(filas[0], dict):
            log.info("Mistral (lotes): claves del resultado %s", sorted(filas[0]))
        salida = []
        for fila in filas:
            if not isinstance(fila, dict):
                continue
            respuesta = fila.get("response") if isinstance(fila.get("response"), dict) else {}
            if respuesta.get("status_code") not in (None, 200):
                log.warning("Mistral (lotes): petición fallida (%s)", respuesta.get("status_code"))
                continue
            cuerpo = respuesta.get("body") or fila.get("body") or (fila if "pages" in fila else None)
            if isinstance(cuerpo, dict) and isinstance(cuerpo.get("pages"), list):
                salida.append(cuerpo)
        return salida

    def _borrar_archivos(self, trabajo: dict) -> None:
        """Retira de Mistral los archivos de resultados, que contienen el texto de los documentos."""
        for clave in ("output_file", "error_file"):
            archivo = trabajo.get(clave)
            if archivo:
                try:
                    _http("DELETE", f"{self.servidor}/v1/files/{archivo}", self.clave)
                except ErrorMistral as e:
                    log.info("Mistral (lotes): no se pudo borrar %s: %s", archivo, str(e)[:120])

    def probar_lotes(self) -> str:
        """Comprueba que el servidor ofrece la API de lotes (sin encargar nada)."""
        try:
            _http("GET", f"{self.servidor}/v1/batch/jobs?page_size=1", self.clave)
            return "Modo económico disponible en este servidor."
        except ErrorMistral as e:
            if e.code == 404:
                return "Este servidor no ofrece el modo económico: se usará el modo normal."
            return f"No se pudo comprobar el modo económico ({str(e)[:100]})."

    def probar(self) -> str:
        """Lee una página de prueba y dice si funciona y si devuelve posiciones."""
        with fitz.open() as prueba:
            pagina = prueba.new_page()
            pagina.insert_text((72, 100), "Prueba de OCR - Marcadores PDF", fontsize=16)
            contenido = prueba.tobytes()
        respuesta = self._pedir(contenido, "prueba")
        paginas = respuesta.get("pages") or [{}]
        texto = markdown_a_texto(paginas[0].get("markdown", ""))
        posiciones = bool(lineas_de_bloques(paginas[0]))
        return (f"Funciona: Mistral ha leído «{texto[:60]}».\n"
                + ("Devuelve la posición de cada bloque: el texto se colocará en su sitio."
                   if posiciones else
                   "No devuelve posiciones que el programa entienda: el texto será buscable, "
                   "pero sin colocar sobre cada párrafo (el detalle queda en «Ver registro»)."))


def comparar_modelos(clave: str, servidor: str, ruta_pdf: Path, carpeta: Path, modelos: tuple[str, str],
                     maximo: int = 10) -> str:
    """Lee las mismas páginas escaneadas con dos modelos de OCR y dice cuánto se parecen.

    Toma hasta `maximo` páginas sin texto propio, repartidas por todo el PDF. Guarda en
    `carpeta` un .txt por modelo con lo leído, para cotejarlo a la vista con el original.
    Devuelve un informe breve. Cuesta lo que cuesten esas páginas en cada modelo.
    """
    import difflib

    with fitz.open(ruta_pdf) as pdf:
        escaneadas = [i for i in range(pdf.page_count) if len(pdf[i].get_text().strip()) < 20]
        if not escaneadas:
            return "Este PDF no tiene páginas escaneadas (todas traen ya su texto): elija otro para comparar."
        paso = max(1, len(escaneadas) // maximo)
        grupo = escaneadas[::paso][:maximo]
        contenido = _trozo(pdf, grupo)
    carpeta.mkdir(parents=True, exist_ok=True)
    leido, datos = {}, {}
    for modelo in modelos:
        ocr = OCRMistral(clave, servidor, modelo)
        inicio = time.monotonic()
        try:
            respuesta = ocr._pedir(contenido, f"comparación ({modelo})")
        except (ErrorMistral, ConfiguracionMistral) as e:
            return f"El modelo «{modelo}» no ha respondido: {str(e)[:200]}"
        hechas: dict = {}
        _anotar(respuesta, grupo, hechas)
        leido[modelo] = hechas
        palabras = sum(len(t.split()) for t, _ in hechas.values())
        datos[modelo] = (len(hechas), palabras, sum(1 for _, lineas in hechas.values() if lineas),
                         time.monotonic() - inicio, str(respuesta.get("model") or modelo))
        destino = carpeta / f"{ruta_pdf.stem} - OCR {modelo}.txt"
        destino.write_text("\n\n".join(f"===== PÁGINA {n} =====\n{hechas[n][0]}" for n in sorted(hechas)),
                           encoding="utf-8")
    a, b = modelos
    parecidos = []
    for n in sorted(set(leido[a]) & set(leido[b])):
        uno, otro = leido[a][n][0].split(), leido[b][n][0].split()
        parecidos.append((difflib.SequenceMatcher(None, uno, otro, autojunk=False).ratio(), n))
    media = sum(r for r, _ in parecidos) / len(parecidos) if parecidos else 0.0
    lineas = [f"Comparación de OCR sobre {len(grupo)} páginas escaneadas de «{ruta_pdf.name}» "
              f"(páginas {', '.join(str(i + 1) for i in grupo)}).", ""]
    for modelo in modelos:
        paginas, palabras, situadas, segundos, real = datos[modelo]
        lineas.append(f"· {modelo}" + (f" (responde {real})" if real != modelo else "") +
                      f": {paginas} páginas, {palabras} palabras, {situadas} con posición del texto, "
                      f"{segundos:.0f} s.")
    lineas += ["", f"Coincidencia media del texto, palabra a palabra: {media * 100:.1f} %."]
    peores = sorted(parecidos)[:3]
    if peores and peores[0][0] < 0.97:
        lineas.append("Páginas que más difieren: " + ", ".join(f"{n} ({r * 100:.0f} %)" for r, n in peores) + ".")
    lineas += ["", "La coincidencia dice cuánto se parecen, no cuál acierta: abra los dos .txt junto al "
                   f"original y revise esas páginas. Guardados en: {carpeta}"]
    informe = "\n".join(lineas)
    log.info("Comparación de OCR: %s", " | ".join(l for l in lineas if l.startswith("·") or "media" in l))
    return informe
