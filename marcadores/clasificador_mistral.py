"""Clasificación con un modelo de lenguaje de Mistral, a partir del texto de cada página.

Mistral OCR lee primero las páginas escaneadas; aquí solo se clasifica. Reutiliza del
clasificador de Gemini los lotes, el ritmo de envío, las pausas ante el 429, el reparto de
lotes que fallan y la interpretación de la respuesta: solo cambia la llamada al modelo.
"""
from __future__ import annotations

import json
import time

from . import ocr_gemini as og
from . import ocr_mistral
from .ocr_gemini import AnalizadorGemini, Lote, RespuestaInvalida
from .ocr_mistral import ConfiguracionMistral, ErrorMistral, SERVIDOR_UE
from .registro import log

MODELO = "mistral-medium-latest"
TOKENS_BASE = 1500
TOKENS_POR_PAGINA = 800  # solo clasificación: una página ocupa ~110 tokens de respuesta


def _esquema_json(esquema: dict) -> dict:
    """El esquema de Gemini (tipos en mayúsculas) en JSON Schema estándar."""
    salida = {}
    for clave, valor in esquema.items():
        if clave == "type":
            salida["type"] = str(valor).lower()
        elif clave == "properties":
            salida["properties"] = {k: _esquema_json(v) for k, v in valor.items()}
        elif clave == "items":
            salida["items"] = _esquema_json(valor)
        else:
            salida[clave] = valor
    if salida.get("type") == "object":
        salida["additionalProperties"] = False
    return salida


ESQUEMA = {"type": "object", "additionalProperties": False, "required": ["paginas"],
           "properties": {"paginas": _esquema_json(og.ESQUEMA)}}
INSTRUCCIONES = og.INSTRUCCIONES + (
    "\n\nTodas las páginas llegan como texto ya reconocido. Responde únicamente con un objeto "
    'JSON de la forma {"paginas": [ ... ]}, con un elemento por página y los campos indicados.')


class AnalizadorMistral(AnalizadorGemini):
    nombre = "mistral"
    respaldo_ocr = "se clasificarán con el texto que se tenga"

    def __init__(self, clave: str, servidor: str = SERVIDOR_UE, modelo: str = MODELO,
                 paginas_por_lote: int = 8, paralelo: int = 4, usar_texto: bool = True):
        if not clave:
            raise ValueError("Falta la clave de API de Mistral (Configurar Vertex… → OCR y clasificación).")
        self._iniciar_comun(modelo or MODELO, paginas_por_lote, 150, paralelo, usar_texto)
        self.firma = f"mistral:{self.modelo}:3"
        self.clave = clave
        self.url = servidor.rstrip("/") + "/v1/chat/completions"
        self._estricto = True  # pedir que la respuesta siga el esquema; si no lo admite, JSON simple

    def preparar(self, doc, indices: list[int], contexto: str = "") -> list:
        """Solo texto: el de Mistral OCR o el que ya traía el PDF."""
        partes = Lote()
        if contexto:
            partes.append(contexto)
        for i in indices:
            externo = self.textos_externos.get(i + 1)
            if externo and externo[0].strip():
                texto, lineas = externo
            else:
                texto, lineas = doc[i].get_text(), []
            partes.textos[i + 1], partes.lineas[i + 1] = texto, lineas
            partes.append(f"PÁGINA {i + 1} · TEXTO YA RECONOCIDO (no transcribir):\n"
                          f"{texto.strip() or '[página sin texto legible]'}")
        return partes

    def _consultar(self, partes: list, indices: list[int]) -> list[dict]:
        paginas = f"{indices[0] + 1}-{indices[-1] + 1}"
        limite = TOKENS_BASE + TOKENS_POR_PAGINA * len(indices)
        intento = cuota = 0
        while True:
            if self.cancelar is not None and self.cancelar.is_set():
                raise og.ConsultaCancelada()
            formato = ({"type": "json_schema", "json_schema": {"name": "paginas", "schema": ESQUEMA,
                                                               "strict": True}}
                       if self._estricto else {"type": "json_object"})
            cuerpo = {"model": self.modelo, "temperature": 0, "max_tokens": limite,
                      "response_format": formato,
                      "messages": [{"role": "system", "content": INSTRUCCIONES},
                                   {"role": "user", "content": "\n\n".join(partes)}]}
            self._turno()
            inicio = time.monotonic()
            try:
                resp = ocr_mistral._enviar(self.url, self.clave, cuerpo, og.ESPERA_MAXIMA)
            except ErrorMistral as e:
                codigo, texto = e.code, str(e).lower()
                log.warning("Mistral, páginas %s: fallo a los %.0f s: %s", paginas,
                            time.monotonic() - inicio, str(e)[:300])
                if codigo in (400, 422) and self._estricto and ("response_format" in texto
                                                                 or "schema" in texto):
                    self._estricto = False
                    log.info("El modelo no admite el esquema estricto; se pide JSON simple")
                    continue
                if codigo in (401, 403):
                    raise ConfiguracionMistral(codigo, f"Mistral rechaza la clave de API (error {codigo}). "
                                                       "Revísela en «Configurar Vertex…».") from e
                if codigo == 404:
                    raise ConfiguracionMistral(404, f"Mistral no ofrece el modelo «{self.modelo}» en "
                                                    f"{self.url} (404). Revise el modelo en «Configurar Vertex…».") from e
                if codigo == 429:
                    self._menos_paralelo()
                    if cuota >= len(og.ESPERAS_CUOTA):
                        raise og.CuotaAgotada(og.MENSAJE_CUOTA) from e
                    espera = og.ESPERAS_CUOTA[cuota]
                    cuota += 1
                    self._pausar_todas(espera)
                    self.avisar(f"Páginas {paginas}: Mistral limita el uso (429). Pausa de {espera} s "
                                f"y se sigue (reintento {cuota} de {len(og.ESPERAS_CUOTA)})…")
                    continue
                if codigo is None and ("timed out" in texto or "timeout" in texto):
                    raise RespuestaInvalida(f"sin respuesta en {og.ESPERA_MAXIMA} s") from e
                if codigo is not None and codigo < 500:
                    raise
                intento += 1
                if intento > 3:
                    raise
                espera = 3 * intento ** 2
                self.avisar(f"Páginas {paginas}: {str(e)[:80]}. Reintento {intento} de 3 en {espera} s…")
                self._esperar(espera)
                continue
            try:
                eleccion = resp["choices"][0]
                motivo = str(eleccion.get("finish_reason") or "")
                contenido = eleccion["message"]["content"]
            except (KeyError, IndexError, TypeError) as e:
                raise RespuestaInvalida("respuesta ilegible") from e
            uso = resp.get("usage") or {}
            log.info("Páginas %s (Mistral %s): respuesta en %.0f s, fin=%s, tokens entrada=%s salida=%s",
                     paginas, self.modelo, time.monotonic() - inicio, motivo,
                     uso.get("prompt_tokens", "?"), uso.get("completion_tokens", "?"))
            if motivo == "length":
                raise RespuestaInvalida("respuesta cortada por longitud (posible bucle)")
            if isinstance(contenido, list):  # algunos modelos devuelven trozos
                contenido = "".join(str(c.get("text", "")) if isinstance(c, dict) else str(c)
                                    for c in contenido)
            try:
                datos = json.loads(contenido or "")
            except (json.JSONDecodeError, TypeError) as e:
                raise RespuestaInvalida("respuesta ilegible") from e
            if isinstance(datos, dict):
                datos = datos.get("paginas")
            if not isinstance(datos, list):
                raise RespuestaInvalida("respuesta sin lista de páginas")
            self._mas_paralelo()
            return datos

    def probar(self) -> str:
        """Clasifica una página de prueba para comprobar la clave, el modelo y el formato."""
        partes = Lote()
        partes.textos[1], partes.lineas[1] = "AUTO\nEn Valencia, a 20 de marzo de 2024.", []
        partes.append("PÁGINA 1 · TEXTO YA RECONOCIDO (no transcribir):\nAUTO\n"
                      "En Valencia, a 20 de marzo de 2024. Se admite a trámite la demanda.")
        info = self.procesar_partes(partes, [0])[0]
        modo = "esquema estricto" if self._estricto else "JSON simple"
        return (f"Clasificación con {self.modelo}: funciona ({modo}). "
                f"La página de prueba sale como «{info.tipo}» con fecha {info.fecha or 'sin fecha'}.")
