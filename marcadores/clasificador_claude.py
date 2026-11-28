"""Clasificación con Claude (Anthropic), a partir del texto de cada página.

Mistral OCR lee primero las páginas escaneadas; aquí solo se clasifica. Como el clasificador
de Mistral, reutiliza del de Gemini los lotes, el ritmo de envío, las pausas ante el 429 y el
reparto de los lotes que fallan: solo cambia la llamada al modelo.

Se usa la API directa de Anthropic, que es donde se aplican los créditos mensuales incluidos en
los planes Max, Ultra y Team. Esa API no ofrece procesamiento solo en la UE: el texto de las
páginas puede tratarse fuera de Europa (se avisa en la configuración).
"""
from __future__ import annotations

import json
import time

from . import ocr_gemini as og
from .clasificador_mistral import ESQUEMA, INSTRUCCIONES
from .ocr_gemini import AnalizadorGemini, Lote, RespuestaInvalida
from .registro import log

MODELO = "claude-haiku-5-5"
TOKENS_BASE = 3000  # margen para el razonamiento breve del modelo
TOKENS_POR_PAGINA = 800


class ConfiguracionClaude(ValueError):
    """Clave, modelo o saldo: fallaría igual en todos los expedientes, así que detiene el lote."""

    def __init__(self, codigo: int, mensaje: str):
        super().__init__(mensaje)
        self.code = codigo


def _cliente(clave: str):
    import anthropic

    # Los reintentos los gobierna el programa (pausas compartidas ante el 429), no la librería.
    return anthropic.Anthropic(api_key=clave, max_retries=0, timeout=og.ESPERA_MAXIMA)


class AnalizadorClaude(AnalizadorGemini):
    nombre = "claude"
    respaldo_ocr = "se clasificarán con el texto que se tenga"

    def __init__(self, clave: str, modelo: str = MODELO, paginas_por_lote: int = 8,
                 paralelo: int = 4, usar_texto: bool = True, cliente=None):
        if not clave and cliente is None:
            raise ValueError("Falta la clave de API de Anthropic (Configurar Vertex… → OCR y clasificación).")
        self._iniciar_comun(modelo or MODELO, paginas_por_lote, 150, paralelo, usar_texto)
        self.firma = f"claude:{self.modelo}:3"
        self.cliente = cliente or _cliente(clave)

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

    def _pedir(self, partes: list, limite: int):
        return self.cliente.messages.create(
            model=self.modelo,
            max_tokens=limite,
            # instrucciones fijas en caché: en un lote largo se cobran a una décima parte
            system=[{"type": "text", "text": INSTRUCCIONES, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": "\n\n".join(partes)}],
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": ESQUEMA}},
        )

    def _consultar(self, partes: list, indices: list[int]) -> list[dict]:
        import anthropic

        paginas = f"{indices[0] + 1}-{indices[-1] + 1}"
        limite = TOKENS_BASE + TOKENS_POR_PAGINA * len(indices)
        intento = cuota = 0
        while True:
            if self.cancelar is not None and self.cancelar.is_set():
                raise og.ConsultaCancelada()
            self._turno()
            inicio = time.monotonic()
            try:
                resp = self._pedir(partes, limite)
            except anthropic.APIStatusError as e:
                codigo, texto = e.status_code, str(e).lower()
                log.warning("Claude, páginas %s: fallo a los %.0f s: %s", paginas,
                            time.monotonic() - inicio, str(e)[:300])
                if codigo in (401, 403):
                    raise ConfiguracionClaude(codigo, f"Anthropic rechaza la clave de API (error {codigo}). "
                                                      "Revísela en «Configurar Vertex…».") from e
                if codigo == 404:
                    raise ConfiguracionClaude(404, f"Anthropic no ofrece el modelo «{self.modelo}» (404). "
                                                   "Revíselo en «Configurar Vertex…».") from e
                if codigo in (400, 402) and ("credit" in texto or "balance" in texto or "billing" in texto
                                             or "spend" in texto):
                    raise ConfiguracionClaude(402, "La cuenta de Anthropic no tiene saldo o créditos "
                                                   "disponibles. Compruebe en claude.ai (Ajustes → Facturación) "
                                                   "que los créditos del plan están vinculados a la "
                                                   "organización de la clave.") from e
                if codigo == 429:
                    self._menos_paralelo()
                    if cuota >= len(og.ESPERAS_CUOTA):
                        raise og.CuotaAgotada(og.MENSAJE_CUOTA) from e
                    espera = og.ESPERAS_CUOTA[cuota]
                    cuota += 1
                    self._pausar_todas(espera)
                    self.avisar(f"Páginas {paginas}: Anthropic limita el uso (429). Pausa de {espera} s "
                                f"y se sigue (reintento {cuota} de {len(og.ESPERAS_CUOTA)})…")
                    continue
                if codigo < 500:
                    raise
                error = e
            except anthropic.APITimeoutError as e:
                raise RespuestaInvalida(f"sin respuesta en {og.ESPERA_MAXIMA} s") from e
            except anthropic.APIConnectionError as e:
                log.warning("Claude, páginas %s: sin conexión: %s", paginas, str(e)[:200])
                error = e
            else:
                return self._leer(resp, paginas, inicio)
            intento += 1
            if intento > 3:
                raise error
            espera = 3 * intento ** 2
            self.avisar(f"Páginas {paginas}: {str(error)[:80]}. Reintento {intento} de 3 en {espera} s…")
            self._esperar(espera)

    def _leer(self, resp, paginas: str, inicio: float) -> list[dict]:
        uso = getattr(resp, "usage", None)
        log.info("Páginas %s (Claude %s): respuesta en %.0f s, fin=%s, tokens entrada=%s salida=%s "
                 "caché=%s", paginas, self.modelo, time.monotonic() - inicio, resp.stop_reason,
                 getattr(uso, "input_tokens", "?"), getattr(uso, "output_tokens", "?"),
                 getattr(uso, "cache_read_input_tokens", "?"))
        if resp.stop_reason == "max_tokens":
            raise RespuestaInvalida("respuesta cortada por longitud")
        if resp.stop_reason == "refusal":
            raise RespuestaInvalida("el modelo ha declinado responder a este lote")
        texto = next((b.text for b in resp.content if getattr(b, "type", "") == "text"), "")
        try:
            datos = json.loads(texto)
        except (json.JSONDecodeError, TypeError) as e:
            raise RespuestaInvalida("respuesta ilegible") from e
        if isinstance(datos, dict):
            datos = datos.get("paginas")
        if not isinstance(datos, list):
            raise RespuestaInvalida("respuesta sin lista de páginas")
        self._mas_paralelo()
        return datos

    def probar(self) -> str:
        """Clasifica una página de prueba para comprobar la clave, el modelo y los créditos."""
        partes = Lote()
        partes.textos[1], partes.lineas[1] = "AUTO\nEn Valencia, a 20 de marzo de 2024.", []
        partes.append("PÁGINA 1 · TEXTO YA RECONOCIDO (no transcribir):\nAUTO\n"
                      "En Valencia, a 20 de marzo de 2024. Se admite a trámite la demanda.")
        info = self.procesar_partes(partes, [0])[0]
        return (f"Clasificación con {self.modelo}: funciona. La página de prueba sale como "
                f"«{info.tipo}» con fecha {info.fecha or 'sin fecha'}.")
