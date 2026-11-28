"""OCR y clasificación página a página con Gemini en Vertex AI (región europea)."""
from __future__ import annotations

import json
import threading
import time

import pymupdf as fitz

from .modelos import TIPOS, PaginaInfo, limpiar
from .registro import log

ESPERA_MAXIMA = 300  # segundos por consulta; pasado ese tiempo se reintenta con menos páginas
# Niveles de razonamiento, del más rápido al más lento. Si el modelo rechaza uno, se pasa al siguiente.
# gemini-3.8-flash rechaza MINIMAL en Vertex ("Thinking level is unsupported"), así que se empieza en LOW.
NIVELES = ("LOW", None)
# Esperas, en segundos, entre reintentos cuando Google responde 429 (exceso de uso).
# El 429 de Vertex en Standard PayGo es un límite de tokens por minuto (TPM): se recupera solo en
# cuanto pasa el minuto, así que se espera y se sigue; solo se abandona tras ~10 min sin conseguirlo.
ESPERAS_CUOTA = (15, 30, 60, 90, 120, 120, 120)
# Separación mínima entre el envío de dos consultas: Google pide evitar ráfagas en el mismo segundo.
INTERVALO_MINIMO = 1.5
# Límite de respuesta por consulta. Una página normal ocupa como mucho ~3.000 tokens (medido en
# expedientes reales); a veces el modelo entra en bucle y escribe hasta 65.000, lo que costaba ~5 min
# por página. Con este tope el bucle se corta en segundos y la página se repite.
TOKENS_BASE = 1500
TOKENS_POR_IMAGEN = 4500
TOKENS_POR_TEXTO = 600
# Tras un 429 se recuperan las consultas simultáneas poco a poco.
EXITOS_PARA_SUBIR = 6
RECUPERAR_TRAS = 60  # segundos desde la última reducción


def texto_util(texto: str) -> bool:
    """Cierto si la página ya trae texto aprovechable (no solo un sello o ruido de OCR)."""
    limpio = "".join(texto.split())
    if len(limpio) < 100:
        return False
    return sum(c.isalnum() for c in limpio) / len(limpio) >= 0.5


class Lote(list):
    """Partes que se envían al modelo, con el texto de las páginas que ya lo traían."""

    def __init__(self):
        super().__init__()
        self.textos: dict[int, str] = {}
        self.lineas: dict[int, list] = {}  # posiciones que trae el OCR externo (Mistral)

INSTRUCCIONES = """Eres un oficial de un despacho de abogados español que folia y clasifica \
expedientes judiciales escaneados. Recibes páginas CONSECUTIVAS de un expediente, \
cada una precedida de su número de página. El expediente mezcla, uno detrás de otro, \
resoluciones judiciales, escritos de las partes, oficios, actas, declaraciones, notificaciones \
y documentos aportados.

Para CADA página devuelve:
- pagina: el número indicado antes de la imagen.
- lineas: transcripción literal y completa del texto de la página (OCR), línea a línea y en \
orden de lectura. No resumas ni corrijas. Cada elemento es UNA línea impresa, con "t" (el texto \
de esa línea) y "c" (su recuadro en la imagen: cuatro enteros [ymin, xmin, ymax, xmax] en una \
escala de 0 a 1000, donde 0,0 es la esquina superior izquierda de la página). Incluye sellos, \
encabezados, pies y texto manuscrito legible. Página en blanco: lista vacía.
- inicia_documento: true SOLO si en esa página EMPIEZA un documento distinto del anterior \
(encabezamiento de una resolución, de un escrito, de un oficio, de un acta, primera hoja de un \
documento aportado...). Las páginas de continuación, las firmas, las hojas en blanco y los \
reversos son false.
- tipo: tipo del documento al que pertenece la página. Valores:
  sentencia, auto, providencia, decreto, diligencia_ordenacion (del Letrado de la Administración \
de Justicia), diligencia (de constancia, de comunicación, de entrega y otras), acta (de vista, \
de juicio, de comparecencia sin declaración), declaracion (de investigado, testigo, perjudicado, \
perito, interrogatorio de parte, exploración), oficio, exhorto (auxilio judicial, mandamientos), \
notificacion (cédulas, acuses, justificantes LexNET), escrito_parte (demanda, contestación, \
querella, denuncia, recursos, alegaciones y cualquier escrito de abogado o procurador), \
informe_pericial, atestado (atestados e informes policiales), documento (contratos, facturas, \
correos, certificados y demás prueba documental), otros.
- titulo: en las páginas que inician documento, título breve y descriptivo (máximo 15 palabras), \
por ejemplo "Auto de admisión a trámite de la demanda", "Declaración del testigo Juan Pérez \
García", "Escrito de contestación a la demanda". En las demás, cadena vacía.
- fecha: fecha del documento en formato AAAA-MM-DD si consta en la página; si no, cadena vacía.
- autor: órgano judicial, parte o persona que emite o firma el documento, si consta; si no, \
cadena vacía.
- subtipo: en las páginas que inician documento, clase concreta del documento en pocas palabras \
(máximo 7). Resoluciones: "Auto de admisión de la demanda", "Auto de medidas cautelares", \
"Auto de sobreseimiento provisional", "Sentencia", "Decreto de admisión", "Decreto de tasación \
de costas", "Providencia de señalamiento", "Diligencia de ordenación de traslado"... Escritos de \
parte: "Demanda", "Contestación a la demanda", "Reconvención", "Querella", "Denuncia", "Escrito \
de acusación", "Escrito de defensa", "Recurso de apelación", "Oposición al recurso", \
"Proposición de prueba", "Escrito de conclusiones", "Personación", "Solicitud de diligencias"... \
En las demás páginas, cadena vacía.
- parte: SOLO en escritos de parte, nombre de la parte en cuyo nombre se presenta (la persona o \
entidad representada, no su abogado ni su procurador). En otro caso, cadena vacía.
- posicion: SOLO en escritos de parte, posición procesal de esa parte: demandante, demandado, \
querellante, denunciante, acusación particular, acusación popular, Ministerio Fiscal, \
investigado, acusado, actor civil, responsable civil, ejecutante, ejecutado, recurrente, \
recurrido, tercero. En otro caso, cadena vacía.
- plazos: plazos que la página CONCEDE o FIJA a alguien para realizar una actuación. En \
resoluciones y comunicaciones judiciales: contestar, subsanar, alegar, aportar, impugnar, formular \
escrito de acusación o defensa... Incluye el plazo para recurrir una sentencia u otra resolución \
que ponga fin al procedimiento o a la instancia. NO incluyas los recursos intermedios: el pie de \
recurso de reposición, revisión, reforma, súplica o queja, ni la apelación contra autos, \
decretos o providencias que no ponen fin al procedimiento. No incluyas plazos que solo se citan \
o se argumentan en un escrito de parte. En documentos (contratos, acuerdos, requerimientos, \
burofaxes, facturas, cartas) incluye los plazos o términos que el documento fija para cumplir \
una obligación. Cada uno con "concepto" (qué hay que hacer y quién, en una frase breve) y \
"plazo" (tal como figura: "veinte días hábiles", "cinco días", "seis meses"). Si no hay, lista vacía.
- senalamientos: actos señalados o convocados en la página con día cierto (audiencia previa, \
vista, juicio oral, declaración, comparecencia, exploración, reconocimiento, subasta, \
deliberación...). Cada uno con "acto", "fecha" (AAAA-MM-DD), "hora" (HH:MM o cadena vacía) y \
"lugar" (sala o sede si consta, o cadena vacía). Si no hay, lista vacía.
- remisiones: referencias que la página hace a OTROS documentos concretos de este mismo \
procedimiento (una resolución anterior, un escrito de otra parte, un oficio, un informe, un \
acta...). Cada una con "cita" (las palabras EXACTAS de la página con las que se nombra, copiadas \
tal cual, entre 3 y 15 palabras seguidas: "el auto de 20 de marzo de 2024", "la resolución \
recurrida", "su escrito de 3 de abril"), "clase" (el tipo del documento citado, con los mismos \
valores que «tipo») y "fecha" (la del documento citado en formato AAAA-MM-DD si figura en la cita \
o se deduce sin ninguna duda de esta misma página; si no, cadena vacía). NO incluyas leyes, \
artículos, jurisprudencia ni documentos ajenos al procedimiento, ni la mención del documento a \
sí mismo. Si dudas de a qué documento se refiere, no la incluyas. Si no hay, lista vacía.

Cada página llega como imagen o, si el PDF ya tenía texto reconocido, como «TEXTO YA RECONOCIDO». \
En esas páginas NO transcribas: devuelve «lineas» como lista vacía y usa ese texto para decidir \
todos los demás campos.

Devuelve exactamente un elemento por cada página recibida, en el mismo orden. No inventes datos."""

ESQUEMA = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {
            "pagina": {"type": "INTEGER"},
            "lineas": {"type": "ARRAY", "items": {
                "type": "OBJECT",
                "properties": {"t": {"type": "STRING"},
                               "c": {"type": "ARRAY", "items": {"type": "INTEGER"}}},
                "required": ["t", "c"]}},
            "inicia_documento": {"type": "BOOLEAN"},
            "tipo": {"type": "STRING", "enum": list(TIPOS)},
            "titulo": {"type": "STRING"},
            "fecha": {"type": "STRING"},
            "autor": {"type": "STRING"},
            "subtipo": {"type": "STRING"},
            "parte": {"type": "STRING"},
            "posicion": {"type": "STRING"},
            "plazos": {"type": "ARRAY", "items": {
                "type": "OBJECT",
                "properties": {"concepto": {"type": "STRING"}, "plazo": {"type": "STRING"}},
                "required": ["concepto", "plazo"]}},
            "senalamientos": {"type": "ARRAY", "items": {
                "type": "OBJECT",
                "properties": {"acto": {"type": "STRING"}, "fecha": {"type": "STRING"},
                               "hora": {"type": "STRING"}, "lugar": {"type": "STRING"}},
                "required": ["acto", "fecha", "hora", "lugar"]}},
            "remisiones": {"type": "ARRAY", "items": {
                "type": "OBJECT",
                "properties": {"cita": {"type": "STRING"}, "clase": {"type": "STRING", "enum": list(TIPOS)},
                               "fecha": {"type": "STRING"}},
                "required": ["cita", "clase", "fecha"]}},
        },
        "required": ["pagina", "lineas", "inicia_documento", "tipo", "titulo", "fecha", "autor",
                     "subtipo", "parte", "posicion", "plazos", "senalamientos", "remisiones"],
    },
}


class AnalizadorGemini:
    nombre = "gemini"

    def __init__(self, proyecto: str, ubicacion: str, modelo: str, credenciales: str = "",
                 paginas_por_lote: int = 6, ppp: int = 150, cliente=None,
                 paralelo: int = 4, usar_texto: bool = True):
        self._iniciar_comun(modelo, paginas_por_lote, ppp, paralelo, usar_texto)
        self.firma = f"gemini:{modelo}:3"  # el 3 identifica el formato de datos
        if cliente is not None:  # para pruebas
            self.cliente = cliente
            return
        if not proyecto:
            raise ValueError("Falta el ID del proyecto de Google Cloud (Configuración de Vertex).")
        from google import genai

        cred = None
        if credenciales:
            from google.oauth2 import service_account

            cred = service_account.Credentials.from_service_account_file(
                credenciales, scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
        self.ubicacion = ubicacion
        from google.genai import types

        self.cliente = genai.Client(
            vertexai=True, project=proyecto, location=ubicacion, credentials=cred,
            http_options=types.HttpOptions(timeout=ESPERA_MAXIMA * 1000))

    def _iniciar_comun(self, modelo: str, paginas_por_lote: int, ppp: int, paralelo: int,
                       usar_texto: bool) -> None:
        """Estado compartido por los clasificadores (Gemini y Mistral): lotes, ritmo, pausas."""
        self.modelo = modelo
        self.paginas_por_lote = max(1, int(paginas_por_lote))
        self.paralelo = max(1, int(paralelo))  # consultas simultáneas
        self.usar_texto = usar_texto  # enviar el texto que ya trae el PDF en vez de la imagen
        self.ocr_externo = None  # OCR de las páginas escaneadas (Mistral); None = lo hace Gemini
        self.textos_externos: dict[int, tuple[str, list]] = {}  # página -> (texto, líneas)
        self.ppp = ppp
        self.avisar = lambda mensaje: None  # lo sustituye el proceso para informar al usuario
        self.cancelar = None  # threading.Event opcional
        self._nivel = 0  # posición en NIVELES
        self._ultima_bajada = -1e9  # instante de la última reducción de consultas simultáneas
        self._paralelo_max = self.paralelo
        self._cerrojo = threading.Lock()
        self._pausa_hasta = 0.0  # tras un 429, ninguna consulta sale antes de este instante
        self._siguiente_envio = 0.0
        self._exitos = 0
        self._resolucion = True  # pedir resolución media de imagen

    def _mas_paralelo(self) -> None:
        """Tras varias respuestas seguidas sin 429, recupera una consulta simultánea."""
        self._exitos += 1
        if (self.paralelo < self._paralelo_max and self._exitos >= EXITOS_PARA_SUBIR
                and time.monotonic() - self._ultima_bajada >= RECUPERAR_TRAS):
            self._exitos = 0
            self.paralelo += 1
            log.info("Sin exceso de uso: se suben las consultas simultáneas a %d", self.paralelo)

    def _menos_paralelo(self) -> None:
        """Ante un 429, baja a la mitad las consultas simultáneas (como mucho una vez cada 15 s)."""
        ahora = time.monotonic()
        self._exitos = 0
        if self.paralelo > 1 and ahora - self._ultima_bajada > 15:
            self._ultima_bajada = ahora
            self.paralelo = max(1, self.paralelo // 2)
            log.info("Exceso de uso: se bajan las consultas simultáneas a %d", self.paralelo)
            self.avisar(f"Se reduce a {self.paralelo} consulta(s) a la vez por el exceso de uso.")

    def _esperar(self, segundos: float) -> None:
        """Espera por tramos de un segundo como mucho, para que Cancelar surta efecto enseguida."""
        resto = float(segundos)
        while resto > 0:
            if self.cancelar is not None and self.cancelar.is_set():
                raise ConsultaCancelada()
            tramo = min(1.0, resto)
            time.sleep(tramo)
            resto -= tramo

    def _turno(self) -> None:
        """Espera el momento de enviar: respeta la pausa común tras un 429 y separa los envíos."""
        with self._cerrojo:
            ahora = time.monotonic()
            salida = max(ahora, self._pausa_hasta, self._siguiente_envio)
            self._siguiente_envio = salida + INTERVALO_MINIMO
        if salida > ahora:
            self._esperar(salida - ahora)
        # si mientras se esperaba otra consulta recibió un 429, también se respeta esa pausa
        resto = self._pausa_hasta - time.monotonic()
        if resto > 0:
            self._esperar(resto)

    def _pausar_todas(self, segundos: float) -> None:
        with self._cerrojo:
            self._pausa_hasta = max(self._pausa_hasta, time.monotonic() + segundos)

    def pausa_restante(self) -> float:
        return max(0.0, self._pausa_hasta - time.monotonic())

    def probar(self) -> None:
        """Consulta mínima; lanza una excepción si el modelo no responde en esta ubicación."""
        self.cliente.models.generate_content(model=self.modelo, contents="Responde solo: OK")

    # --- llamada al modelo -------------------------------------------------
    def preparar(self, doc: fitz.Document, indices: list[int], contexto: str = "") -> list:
        """Convierte las páginas en imágenes para enviarlas.

        PyMuPDF no admite varios hilos a la vez: esto se llama siempre desde el hilo principal
        y solo la consulta al modelo (procesar_partes) se hace en paralelo.
        """
        from google.genai import types

        partes = Lote()
        if contexto:
            partes.append(types.Part.from_text(text=contexto))
        for i in indices:
            externo = self.textos_externos.get(i + 1)
            if externo and externo[0].strip():
                # leída ya por el OCR externo: Gemini solo clasifica
                partes.textos[i + 1], partes.lineas[i + 1] = externo
                partes.append(types.Part.from_text(
                    text=f"PÁGINA {i + 1} · TEXTO YA RECONOCIDO (no transcribir):\n{externo[0]}"))
                continue
            if self.usar_texto:
                texto = doc[i].get_text()
                if texto_util(texto):
                    # sin imagen ni transcripción: la respuesta es mucho más corta y rápida
                    partes.textos[i + 1] = texto
                    partes.append(types.Part.from_text(
                        text=f"PÁGINA {i + 1} · TEXTO YA RECONOCIDO (no transcribir):\n{texto}"))
                    continue
            pix = doc[i].get_pixmap(dpi=self.ppp)
            partes.append(types.Part.from_text(text=f"PÁGINA {i + 1}"))
            partes.append(types.Part.from_bytes(data=pix.tobytes("jpeg", jpg_quality=80),
                                                mime_type="image/jpeg"))
        return partes

    def _consultar(self, partes: list, indices: list[int]) -> list[dict]:
        from google.genai import types

        def configuracion(nivel: int):
            extra = {}
            razonamiento = NIVELES[min(nivel, len(NIVELES) - 1)]
            if razonamiento:  # menos espera y menos tokens gastados en razonar
                extra["thinking_config"] = types.ThinkingConfig(thinking_level=razonamiento)
            if self._resolucion:  # 560 tokens por página: suficiente para texto impreso
                extra["media_resolution"] = types.MediaResolution.MEDIA_RESOLUTION_MEDIUM
            return types.GenerateContentConfig(
                # sin «temperature»: Google pide dejar el valor por defecto (1.0) en Gemini 3,
                # porque bajarlo puede provocar bucles; con 0 se veían respuestas de 65.000 tokens
                system_instruction=INSTRUCCIONES,
                response_mime_type="application/json", response_schema=ESQUEMA,
                max_output_tokens=limite, **extra)

        con_texto = len(getattr(partes, "textos", {}))
        limite = min(65535, TOKENS_BASE + TOKENS_POR_IMAGEN * (len(indices) - con_texto)
                     + TOKENS_POR_TEXTO * con_texto)

        paginas = f"{indices[0] + 1}-{indices[-1] + 1}"
        ultimo = None
        intento = 0
        cuota = 0
        while intento < 4:
            if self.cancelar is not None and self.cancelar.is_set():
                raise ConsultaCancelada()
            self._turno()
            inicio = time.monotonic()
            nivel = self._nivel
            try:
                resp = self.cliente.models.generate_content(
                    model=self.modelo, contents=partes, config=configuracion(nivel))
            except Exception as e:  # errores de red, cuota, servicio
                ultimo = e
                codigo = getattr(e, "code", None)
                segundos = time.monotonic() - inicio
                log.warning("Páginas %s: fallo a los %.0f s (intento %d): %s: %s", paginas,
                            segundos, intento + 1, type(e).__name__, str(e)[:500])
                if codigo == 404:
                    raise ModeloNoDisponible(
                        f"El modelo «{self.modelo}» no está disponible en la ubicación "
                        f"«{getattr(self, 'ubicacion', '')}» para su proyecto. Abra «Configurar "
                        "Vertex…» y pulse «Probar conexión» para encontrar una combinación "
                        "que funcione.") from e
                texto_error = str(e).lower()
                if codigo == 400 and "think" in texto_error and nivel < len(NIVELES) - 1:
                    if self._nivel == nivel:  # varios hilos pueden fallar a la vez: avanzar solo una vez
                        self._nivel = nivel + 1
                        log.info("El modelo no admite razonamiento %s; se prueba %s", NIVELES[nivel],
                                 NIVELES[self._nivel] or "el predeterminado")
                    continue
                if codigo == 400 and "resolution" in texto_error and self._resolucion:
                    self._resolucion = False  # este modelo no admite el ajuste: sin él
                    log.info("El modelo no admite media_resolution; se continúa sin él")
                    continue
                if codigo in (400, 401, 403):
                    raise
                if codigo == 429 or "resource_exhausted" in texto_error or "resource exhausted" in texto_error:
                    self._menos_paralelo()
                    if cuota >= len(ESPERAS_CUOTA):
                        raise CuotaAgotada(MENSAJE_CUOTA) from e
                    espera = ESPERAS_CUOTA[cuota]
                    cuota += 1
                    # todas las consultas paran a la vez: si siguieran, agotarían de nuevo el minuto
                    self._pausar_todas(espera)
                    self.avisar(f"Páginas {paginas}: Google limita el uso por minuto (429). Pausa de "
                                f"{espera} s y se sigue (reintento {cuota} de {len(ESPERAS_CUOTA)})…")
                    continue
                # Google corta la consulta a los ~5 min con 504 DEADLINE_EXCEEDED: el lote es demasiado
                # grande. Repetirlo igual solo gastaría otros 5 min por intento; hay que partirlo.
                if (codigo in (408, 504) or "deadline" in texto_error
                        or "timeout" in type(e).__name__.lower() or "timed out" in texto_error):
                    raise RespuestaInvalida(f"sin respuesta en {ESPERA_MAXIMA} s") from e
                intento += 1
                if intento < 4:
                    espera = 3 * intento ** 2
                    self.avisar(f"Páginas {paginas}: {explicar(e)}. Reintento {intento} de 3 "
                                f"en {espera} s…")
                    time.sleep(espera)
                continue
            motivo = ""
            try:
                motivo = str(resp.candidates[0].finish_reason or "").split(".")[-1]
            except Exception:
                pass
            uso = getattr(resp, "usage_metadata", None)
            log.info("Páginas %s (%d con texto, %d como imagen): respuesta en %.0f s, fin=%s, "
                     "razonamiento=%s, tokens entrada=%s salida=%s pensados=%s",
                     paginas, con_texto, len(indices) - con_texto, time.monotonic() - inicio, motivo,
                     NIVELES[min(nivel, len(NIVELES) - 1)],
                     getattr(uso, "prompt_token_count", "?"),
                     getattr(uso, "candidates_token_count", "?"),
                     getattr(uso, "thoughts_token_count", "?"))
            if motivo and motivo not in ("STOP", "FINISH_REASON_UNSPECIFIED"):
                raise RespuestaInvalida({"MAX_TOKENS": "respuesta cortada por longitud (posible bucle)",
                                         "RECITATION": "respuesta bloqueada por el modelo",
                                         "SAFETY": "respuesta bloqueada por el modelo"}
                                        .get(motivo, f"respuesta incompleta ({motivo})"))
            try:
                datos = json.loads(resp.text or "")
            except (json.JSONDecodeError, TypeError) as e:
                raise RespuestaInvalida("respuesta ilegible") from e
            if not isinstance(datos, list):
                raise RespuestaInvalida("respuesta sin lista de páginas")
            self._mas_paralelo()
            return datos
        raise ultimo

    def analizar_lote(self, doc: fitz.Document, indices: list[int], contexto: str = "") -> list[PaginaInfo]:
        """Analiza un lote de forma secuencial (prepara las imágenes y consulta)."""
        return self.procesar_partes(self.preparar(doc, indices, contexto), indices)

    def procesar_partes(self, partes: list, indices: list[int]) -> list[PaginaInfo]:
        """Consulta al modelo con las imágenes ya preparadas. Se puede llamar desde varios hilos.

        Lanza RespuestaInvalida si conviene repetir el lote con menos páginas.
        """
        datos = self._consultar(partes, indices)
        textos = getattr(partes, "textos", {})
        por_pagina = {int(d.get("pagina", 0)): d for d in datos if isinstance(d, dict)}
        if any((i + 1) not in por_pagina for i in indices):
            raise RespuestaInvalida("faltan páginas en la respuesta")
        salida = []
        for i in indices:
            d = por_pagina[i + 1]
            tipo = d.get("tipo") if d.get("tipo") in TIPOS else "otros"
            if (i + 1) in textos:  # texto del propio PDF o del OCR externo; Gemini no lo transcribe
                lineas, texto = getattr(partes, "lineas", {}).get(i + 1, []), textos[i + 1]
            else:
                lineas = _lineas(d.get("lineas"))
                texto = "\n".join(x[0] for x in lineas) if lineas else str(d.get("texto") or "")
            salida.append(PaginaInfo(
                pagina=i + 1,
                texto=texto,
                lineas=lineas,
                inicia_documento=bool(d.get("inicia_documento")),
                tipo=tipo,
                titulo=limpiar(str(d.get("titulo") or "")).strip(),
                fecha=limpiar(str(d.get("fecha") or "")).strip(),
                autor=limpiar(str(d.get("autor") or "")).strip(),
                subtipo=limpiar(str(d.get("subtipo") or "")).strip(),
                parte=limpiar(str(d.get("parte") or "")).strip(),
                posicion=limpiar(str(d.get("posicion") or "")).strip(),
                plazos=_lista(d.get("plazos"), ("concepto", "plazo")),
                senalamientos=_lista(d.get("senalamientos"), ("acto", "fecha", "hora", "lugar")),
                remisiones=_lista(d.get("remisiones"), ("cita", "clase", "fecha")),
            ))
        return salida


def _lineas(valor) -> list[list]:
    """Normaliza las líneas a [texto, ymin, xmin, ymax, xmax]; sin recuadro válido, ceros."""
    salida = []
    for x in valor if isinstance(valor, list) else []:
        if not isinstance(x, dict) or not str(x.get("t") or "").strip():
            continue
        c = x.get("c")
        try:
            caja = [max(0, min(1000, int(n))) for n in c] if isinstance(c, list) and len(c) == 4 else []
        except (TypeError, ValueError):
            caja = []
        if not caja or caja[2] <= caja[0] or caja[3] <= caja[1]:
            caja = [0, 0, 0, 0]
        salida.append([limpiar(str(x["t"])).strip()] + caja)
    return salida


def _lista(valor, campos: tuple[str, ...]) -> list[dict]:
    salida = []
    for x in valor if isinstance(valor, list) else []:
        if isinstance(x, dict):
            d = {c: limpiar(str(x.get(c) or "")).strip() for c in campos}
            if d[campos[0]]:
                salida.append(d)
    return salida


class RespuestaInvalida(Exception):
    """La consulta no ha dado un resultado utilizable; se repite con menos páginas."""


class ConsultaCancelada(Exception):
    pass


MENSAJE_CUOTA = (
    "Google rechaza las consultas por exceso de uso (error 429) aunque se han repetido durante unos "
    "10 minutos. Su proyecto tiene un límite de tokens por minuto que Google fija según el gasto de "
    "los últimos 30 días; por encima de él solo atiende si le sobra capacidad.\n\nQué probar:\n"
    "1. Esperar unos minutos y volver a pulsar Procesar: lo ya leído está guardado.\n"
    "2. En «Configurar Vertex…», bajar «Consultas simultáneas» a 1.\n"
    "3. Probar otra ubicación (por ejemplo europe-west4) o el modelo gemini-3.5-flash.\n"
    "4. Comprobar que no queda otro MarcadoresPDF abierto en segundo plano "
    "(Administrador de tareas) consumiendo la cuota.\n"
    "5. Revisar la cuota del proyecto en la consola de Google Cloud "
    "(IAM y administración → Cuotas, buscando Vertex AI)."
)


class CuotaAgotada(Exception):
    """Google sigue respondiendo 429 tras varios reintentos espaciados."""


class ModeloNoDisponible(Exception):
    pass


def es_fallo_de_conexion(e: Exception) -> bool:
    """Cierto si no se ha llegado a hablar con Google (no hay código de respuesta)."""
    return getattr(e, "code", None) is None and not isinstance(e, (ValueError, FileNotFoundError))


def explicar(e: Exception) -> str:
    codigo = getattr(e, "code", None)
    if codigo == 404:
        return "no disponible"
    if codigo in (401, 403):
        return "sin permiso (revise el rol «Usuario de Vertex AI» y que la API esté activada)"
    if codigo == 429:
        return "cuota agotada"
    return f"{type(e).__name__}: {str(e)[:150]}"


def diagnostico_conexion() -> list[str]:
    """Comprueba por separado si este programa puede conectar con los servidores de Google."""
    import urllib.error
    import urllib.request

    lineas = []
    for sitio in ("oauth2.googleapis.com", "aiplatform.googleapis.com"):
        url = f"https://{sitio}/"
        try:
            urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=15)
            a = "conecta"
        except urllib.error.HTTPError:
            a = "conecta"  # cualquier respuesta del servidor vale
        except Exception as e:
            a = f"NO conecta ({type(e).__name__}: {str(e)[:90]})"
        try:
            import requests

            requests.head(url, timeout=15)
            b = "conecta"
        except Exception as e:
            b = f"NO conecta ({type(e).__name__}: {str(e)[:90]})"
        lineas.append(f"{sitio} → conexión de Windows: {a}; conexión de Google: {b}")
    return lineas


def buscar_combinacion(proyecto: str, credenciales: str, ubicacion: str, modelo: str,
                       ubicaciones: list[str], modelos: list[str], fabrica=None):
    """Prueba primero lo configurado y, si falla, otras combinaciones europeas.

    Devuelve ((ubicacion, modelo) que funciona o None, lista de líneas de informe).
    """
    fabrica = fabrica or (lambda u, m: AnalizadorGemini(proyecto, u, m, credenciales))
    pares = [(ubicacion, modelo)]
    pares += [(u, m) for m in [modelo] + modelos for u in [ubicacion] + ubicaciones]
    informe, vistos = [], set()
    for u, m in pares:
        if not u or not m or (u, m) in vistos:
            continue
        vistos.add((u, m))
        try:
            fabrica(u, m).probar()
            informe.append(f"✔ {m} en {u}: funciona")
            return (u, m), informe
        except Exception as e:
            if getattr(e, "code", None) in (401, 403):
                informe.append(f"✘ {m} en {u}: {explicar(e)}")
                return None, informe  # problema de permisos: no tiene sentido seguir
            if es_fallo_de_conexion(e):
                # No es cuestión de modelo ni de región: el programa no llega a Google.
                informe.append("✘ No se ha podido conectar con Google. No es un problema del "
                               "modelo ni de la región.")
                informe.append(f"Detalle: {explicar(e)}")
                informe += diagnostico_conexion()
                informe.append("Causa habitual: el antivirus o el cortafuegos bloquea la salida a "
                               "Internet de este programa, o hay un proxy. Autorice "
                               "MarcadoresPDF.exe en el antivirus y vuelva a probar.")
                return None, informe
            informe.append(f"✘ {m} en {u}: {explicar(e)}")
    return None, informe


def contexto_de(paginas: list[PaginaInfo]) -> str:
    """Frase de contexto para que el siguiente lote sepa qué documento estaba abierto."""
    validas = [p for p in paginas if not p.error]
    if not validas:
        return ""
    ultima = validas[-1]
    inicio = next((p for p in reversed(validas) if p.inicia_documento), None)
    titulo = inicio.titulo if inicio else ""
    final = " ".join(ultima.texto.split())[-300:]
    return (
        f"CONTEXTO: la página anterior ({ultima.pagina}) pertenece a un documento de tipo "
        f"'{ultima.tipo}'" + (f" titulado '{titulo}'" if titulo else "") +
        f". Su texto termina así: «{final}». Decide si la primera página que recibes ahora "
        "continúa ese documento o inicia uno nuevo."
    )
