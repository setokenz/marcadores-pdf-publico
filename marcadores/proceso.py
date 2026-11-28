"""Orquesta el proceso completo de un expediente."""
from __future__ import annotations

import json
import queue
import threading
import time
from collections import deque
from pathlib import Path
from typing import Callable

from . import salidas
from .clasificar import (ajustar_al_ambito, apariciones, aplicar_palabras_clave, decidir_ambito,
                         leer_palabras_clave, segmentar)
from .entrada import Expediente, unir
from .modelos import Documento, PaginaInfo
from .nombres import Plantillas
from .ocr_gemini import ConsultaCancelada, RespuestaInvalida, contexto_de
from .registro import log

Aviso = Callable[[str, float], None]  # (mensaje, fracción 0-1)
LATIDO = 30  # segundos sin novedades tras los que se avisa de que se sigue esperando a Google
REINTENTOS_PAGINA = 2  # veces que se repite una sola página antes de darla por no leída


class Cancelado(Exception):
    pass


def _cargar_cache(ruta: Path, huella: str, firma: str) -> dict[int, PaginaInfo]:
    try:
        datos = json.loads(ruta.read_text(encoding="utf-8"))
        if datos.get("huella") != huella or datos.get("firma") != firma:
            return {}
        return {p["pagina"]: PaginaInfo.de_dict(p) for p in datos["paginas"]}
    except Exception:
        return {}


def _guardar_cache(ruta: Path, huella: str, firma: str, hechas: dict[int, PaginaInfo]) -> None:
    datos = {"huella": huella, "firma": firma,
             "paginas": [hechas[k].a_dict() for k in sorted(hechas)]}
    ruta.write_text(json.dumps(datos, ensure_ascii=False), encoding="utf-8")


def _trozos(paginas: list[int], tamano: int) -> list[list[int]]:
    """Agrupa páginas consecutivas en lotes de hasta «tamano»; un hueco abre un lote nuevo."""
    lotes: list[list[int]] = []
    for i in paginas:
        if not lotes or i != lotes[-1][-1] + 1 or len(lotes[-1]) >= tamano:
            lotes.append([])
        lotes[-1].append(i)
    return lotes


def _leer_en_paralelo(pdf, analizador, hechas: dict, total: int, lote: int, cancelar, aviso,
                      guardar) -> None:
    """Lee el expediente con varias consultas a la vez.

    Las imágenes se preparan en el hilo principal (PyMuPDF no admite varios hilos) y solo la
    espera a Gemini, que es lo lento, ocurre en paralelo. Los hilos son «daemon» para que
    Cancelar y cerrar la ventana no esperen a consultas en curso.
    """
    resultados: queue.Queue = queue.Queue()
    trabajo = deque(_trozos([i for i in range(total) if (i + 1) not in hechas], lote))
    en_curso = 0
    fallos: dict[int, int] = {}  # página suelta -> veces que ha fallado
    desde: dict[int, float] = {}  # primera página del lote -> instante de envío
    ultimo_aviso = time.monotonic()

    def lanzar(indices: list[int]) -> None:
        anteriores = [hechas[n] for n in range(max(1, indices[0] - 3), indices[0] + 1) if n in hechas]
        partes = analizador.preparar(pdf, indices, contexto_de(anteriores))

        def correr() -> None:
            try:
                resultados.put((indices, analizador.procesar_partes(partes, indices), None))
            except BaseException as e:  # se devuelve al hilo principal, que decide
                resultados.put((indices, None, e))

        threading.Thread(target=correr, daemon=True).start()

    while trabajo or en_curso:
        if cancelar is not None and cancelar.is_set():
            raise Cancelado()
        while trabajo and en_curso < analizador.paralelo:
            indices = trabajo.popleft()
            tramo = (f"página {indices[0] + 1}" if len(indices) == 1
                     else f"páginas {indices[0] + 1}-{indices[-1] + 1}")
            aviso(f"Leyendo {tramo} de {total} ({en_curso + 1} consultas a la vez)…",
                  0.9 * len(hechas) / total)
            if cancelar is not None and cancelar.is_set():
                raise Cancelado()
            lanzar(indices)
            desde[indices[0]] = ultimo_aviso = time.monotonic()
            en_curso += 1
        try:
            indices, resultado, error = resultados.get(timeout=0.5)
        except queue.Empty:
            ahora = time.monotonic()
            if desde and ahora - ultimo_aviso >= LATIDO:
                ultimo_aviso = ahora
                espera = int(ahora - min(desde.values()))
                pausa = int(getattr(analizador, "pausa_restante", lambda: 0)())
                servicio = {"claude": "Anthropic", "mistral": "Mistral"}.get(
                    getattr(analizador, "nombre", ""), "Google")
                if pausa > 0:
                    mensaje = (f"Pausa por el límite de uso por minuto de {servicio}: se sigue en {pausa} s "
                               f"({en_curso} consulta(s) pendientes)…")
                else:
                    mensaje = (f"Esperando respuesta de {servicio}: {en_curso} consulta(s) en curso, "
                               f"la más antigua desde hace {espera} s…")
                log.info(mensaje)
                aviso(mensaje, 0.9 * len(hechas) / total)
            continue
        en_curso -= 1
        desde.pop(indices[0], None)
        ultimo_aviso = time.monotonic()
        if isinstance(error, ConsultaCancelada):
            raise Cancelado() from None
        if isinstance(error, RespuestaInvalida):
            tramo = f"{indices[0] + 1}-{indices[-1] + 1}"
            log.warning("Lote %s no válido: %s", tramo, error)
            if len(indices) == 1:
                pagina = indices[0] + 1
                fallos[pagina] = fallos.get(pagina, 0) + 1
                if fallos[pagina] <= REINTENTOS_PAGINA:
                    # el fallo suele ser un bucle puntual del modelo: repetir la página basta
                    aviso(f"Página {pagina}: {error}. Se repite ({fallos[pagina]} de "
                          f"{REINTENTOS_PAGINA})…", 0.9 * len(hechas) / total)
                    trabajo.appendleft(indices)
                    continue
                aviso(f"No se ha podido leer la página {pagina} ({error}); se continúa.",
                      0.9 * len(hechas) / total)
                resultado = [PaginaInfo(pagina=pagina, error=True)]
            else:
                # Solo se parte el lote que ha fallado: el problema suele ser una página concreta,
                # no el tamaño del lote, así que el resto del expediente sigue con lotes normales.
                mitad = (len(indices) + 1) // 2
                aviso(f"Sin resultado para las páginas {tramo} ({error}). Se repite en lotes de {mitad}.",
                      0.9 * len(hechas) / total)
                trabajo.extendleft(reversed(_trozos(indices, mitad)))
                continue
        elif error is not None:
            raise error
        for info in resultado:
            hechas[info.pagina] = info
        guardar()
        aviso(f"Leídas {len(hechas)} de {total} páginas…", 0.9 * len(hechas) / total)


def procesar(exp: Expediente, carpeta_salida: Path, analizador, palabras_clave: str = "",
             capa_texto: bool = True, separar: bool = False, indice_en_pdf: bool = True,
             plantillas: Plantillas | None = None, tipo_expediente: str = "auto",
             enderezar: bool = True, quitar_blancas: bool = False, exportar_rag: bool = True,
             contenido_indice: tuple[str, ...] = salidas.CONTENIDO_INDICE,
             navegacion: bool = True,
             aviso: Aviso = lambda m, f: None,
             cancelar: threading.Event | None = None) -> list[Documento]:
    plantillas = plantillas or Plantillas()
    destino_pdf = carpeta_salida / plantillas.de_pdf(exp.nombre)
    destino_excel = carpeta_salida / plantillas.de_excel(exp.nombre)
    originales = {r.resolve() for r in exp.rutas}
    if destino_pdf.resolve() in originales or destino_excel.resolve() in originales:
        raise ValueError(f"El resultado de «{exp.nombre}» se guardaría encima del archivo original. "
                         "Cambie la carpeta de resultados o los nombres en «Nombres…».")
    carpeta_salida.mkdir(parents=True, exist_ok=True)
    aviso(f"Abriendo «{exp.nombre}»…", 0.0)
    pdf = unir(exp.rutas)
    total = pdf.page_count
    if total == 0:
        raise ValueError("El expediente no tiene páginas.")

    # El resultado del OCR se guarda para no repetirlo (ni pagarlo) dos veces.
    ruta_cache = carpeta_salida / f"{exp.nombre}.ocr.json"
    huella = exp.huella()
    hechas = _cargar_cache(ruta_cache, huella, analizador.firma)
    if not hechas:  # lecturas de versiones anteriores, guardadas en otra carpeta
        import glob

        # también en carpetas hermanas: si se cambió la plantilla de nombre de la carpeta
        hermanas = sorted(carpeta_salida.parent.glob(f"*/{glob.escape(ruta_cache.name)}"))[:50]
        for vieja in (exp.carpeta / carpeta_salida.name / ruta_cache.name,
                      exp.carpeta / "Clasificado" / ruta_cache.name, *hermanas):
            hechas = hechas or _cargar_cache(vieja, huella, analizador.firma)
    hechas = {k: v for k, v in hechas.items() if not v.error and k <= total}
    if hechas:
        aviso(f"Recuperadas {len(hechas)} páginas ya leídas en una pasada anterior.", 0.0)

    ocr = getattr(analizador, "ocr_externo", None)
    if ocr is not None:
        from .ocr_gemini import ConsultaCancelada, texto_util

        # páginas sin texto aprovechable (o todas, si no se quiere aprovechar el del PDF)
        sin_texto = [i for i in range(total) if (i + 1) not in hechas and (
            not analizador.usar_texto or not texto_util(pdf[i].get_text()))]
        if sin_texto:
            log.info("«%s»: OCR con Mistral de %d páginas", exp.nombre, len(sin_texto))
            try:
                analizador.textos_externos = ocr.leer(
                    pdf, sin_texto, carpeta_salida / f"{exp.nombre}.mistral.json", huella,
                    aviso=lambda m: aviso(m, 0.3 * len(analizador.textos_externos) / total),
                    cancelar=cancelar,
                    respaldo=getattr(analizador, "respaldo_ocr", "irán a Gemini como imagen"))
            except ConsultaCancelada:
                raise Cancelado() from None
    elif hasattr(analizador, "textos_externos"):
        analizador.textos_externos = {}

    lote = analizador.paginas_por_lote
    analizador.avisar = lambda mensaje: aviso(mensaje, 0.9 * len(hechas) / total)
    analizador.cancelar = cancelar
    log.info("Expediente «%s»: %d páginas, %d ya leídas, lotes de %d, analizador %s",
             exp.nombre, total, len(hechas), lote, analizador.firma)
    if hasattr(analizador, "preparar"):  # Gemini: siempre por este camino, aunque sea de una en una
        _leer_en_paralelo(pdf, analizador, hechas, total, lote, cancelar, aviso,
                          lambda: _guardar_cache(ruta_cache, huella, analizador.firma, hechas))
    pendientes = [i for i in range(total) if (i + 1) not in hechas]
    while pendientes:
        if cancelar is not None and cancelar.is_set():
            raise Cancelado()
        # lote de páginas consecutivas
        indices = [pendientes[0]]
        for i in pendientes[1:lote]:
            if i != indices[-1] + 1:
                break
            indices.append(i)
        anteriores = [hechas[n] for n in range(max(1, indices[0] - 3), indices[0] + 1) if n in hechas]
        tramo = (f"página {indices[0] + 1}" if len(indices) == 1
                 else f"páginas {indices[0] + 1}-{indices[-1] + 1}")
        aviso(f"Leyendo {tramo} de {total}…", 0.9 * len(hechas) / total)
        try:
            resultado = analizador.analizar_lote(pdf, indices, contexto_de(anteriores))
        except ConsultaCancelada:
            raise Cancelado() from None
        except RespuestaInvalida as e:
            log.warning("Lote %s no válido: %s", tramo, e)
            if len(indices) == 1:
                aviso(f"No se ha podido leer la página {indices[0] + 1} ({e}); se continúa.",
                      0.9 * len(hechas) / total)
                resultado = [PaginaInfo(pagina=indices[0] + 1, error=True)]
            else:
                # se repite con la mitad de páginas, y así se sigue en el resto del expediente
                lote = max(1, len(indices) // 2)
                aviso(f"Sin resultado para las {tramo} ({e}). Se repite en lotes de {lote}.",
                      0.9 * len(hechas) / total)
                continue
        for info in resultado:
            hechas[info.pagina] = info
        _guardar_cache(ruta_cache, huella, analizador.firma, hechas)
        pendientes = [i for i in range(total) if (i + 1) not in hechas]

    paginas = [hechas[n] for n in range(1, total + 1)]
    fallidas = [p.pagina for p in paginas if p.error]
    if fallidas:
        aviso(f"AVISO: no se pudieron leer las páginas {fallidas}; quedan unidas al documento "
              "anterior. Vuelva a procesar para reintentarlas.", 0.9)

    from .paginas import es_blanca, giro_de

    giros: dict[int, int] = {}
    if enderezar:
        giros = {p.pagina: g for p in paginas if (g := giro_de(pdf[p.pagina - 1], p))}
        if giros:
            log.info("«%s»: páginas giradas %s", exp.nombre, giros)
    retiradas: list[int] = []
    if quitar_blancas:
        blancas = [p.pagina for p in paginas if es_blanca(pdf[p.pagina - 1], p)]
        if blancas and len(blancas) < total:
            paginas, giros = _retirar_paginas(pdf, paginas, giros, blancas)
            retiradas = blancas
            log.info("«%s»: páginas en blanco retiradas %s", exp.nombre, blancas)
            aviso(f"Retiradas {len(blancas)} páginas en blanco.", 0.91)

    docs = segmentar(paginas)
    # «lote:judicial»: tipo decidido para todos los expedientes procesados juntos
    en_lote = tipo_expediente.startswith("lote:")
    elegido = tipo_expediente.split(":", 1)[1] if en_lote else tipo_expediente
    ambito = decidir_ambito(docs, elegido)
    ajustar_al_ambito(docs, ambito)
    detectado = "lote" if en_lote else elegido not in ("judicial", "extrajudicial")
    log.info("«%s»: expediente %s%s", exp.nombre, ambito,
             {"lote": " (decidido para el lote)", True: " (detectado)"}.get(detectado, ""))
    reglas = leer_palabras_clave(palabras_clave)
    aplicar_palabras_clave(docs, reglas)

    aviso("Generando PDF con marcadores e índices…", 0.92)
    log.info("«%s»: generando resultados (%d documentos)…", exp.nombre, len(docs))
    inicio = time.monotonic()
    if separar:
        salidas.separar_documentos(pdf, docs, carpeta_salida / "documentos",
                                   lambda d: plantillas.de_documento(d, exp.nombre))
    from .remisiones import resolver

    remisiones = resolver(docs, paginas)
    if remisiones:
        log.info("«%s»: %d remisiones entre documentos (%d localizadas)", exp.nombre, len(remisiones),
                 sum(1 for r in remisiones if r.destino))
    salidas.escribir_excel(docs, destino_excel, ambito, remisiones)
    if exportar_rag:
        from .rag import escribir_rag

        escribir_rag(docs, paginas, carpeta_salida, exp.nombre, ambito,
                     jsonl=carpeta_salida / f"{exp.nombre} - rag.jsonl")
    salidas.escribir_pdf(pdf, docs, paginas, destino_pdf,
                         capa_texto=capa_texto, apariciones=apariciones(paginas, reglas),
                         indice_en_pdf=indice_en_pdf, nombre=exp.nombre, ambito=ambito,
                         detectado=detectado, giros=giros, retiradas=retiradas,
                         contenido=contenido_indice, navegacion=navegacion,
                         remisiones=remisiones)
    pdf.close()
    log.info("«%s»: resultados generados en %.0f s", exp.nombre, time.monotonic() - inicio)
    aviso(f"Terminado «{exp.nombre}»: {len(docs)} documentos en {total} páginas. "
          f"Resultados en: {carpeta_salida}", 1.0)
    return docs


def _retirar_paginas(pdf, paginas: list[PaginaInfo], giros: dict[int, int],
                     blancas: list[int]) -> tuple[list[PaginaInfo], dict[int, int]]:
    """Quita del PDF las páginas en blanco y renumera las demás (el original no se toca).

    Si una página retirada marcaba el comienzo de un documento, esa marca pasa a la
    siguiente que queda, para no unir dos documentos.
    """
    from dataclasses import replace

    quitar = set(blancas)
    nuevas: list[PaginaInfo] = []
    nuevos_giros: dict[int, int] = {}
    pendiente = None
    for p in paginas:
        if p.pagina in quitar:
            if p.inicia_documento and pendiente is None:
                pendiente = p
            continue
        q = replace(p, pagina=len(nuevas) + 1)
        if pendiente is not None:
            if not q.inicia_documento:
                q = replace(q, inicia_documento=True, tipo=pendiente.tipo,
                            titulo=pendiente.titulo or q.titulo, fecha=q.fecha or pendiente.fecha,
                            autor=q.autor or pendiente.autor, subtipo=pendiente.subtipo,
                            parte=pendiente.parte, posicion=pendiente.posicion)
            pendiente = None
        if p.pagina in giros:
            nuevos_giros[q.pagina] = giros[p.pagina]
        nuevas.append(q)
    pdf.delete_pages(sorted(n - 1 for n in quitar))
    return nuevas, nuevos_giros


def procesar_varios(expedientes: list[Expediente], destino_de, analizador,
                    aviso: Aviso = lambda m, f: None, cancelar: threading.Event | None = None,
                    **opciones) -> list[tuple[Expediente, Exception]]:
    """Procesa varios expedientes seguidos. Si uno falla, lo anota y sigue con los demás.

    Solo detienen el lote los problemas que afectarían a todos (cancelación, cuota agotada,
    modelo no disponible, credenciales rechazadas). Devuelve los expedientes que fallaron.
    """
    from .ocr_gemini import CuotaAgotada, ModeloNoDisponible

    fallidos: list[tuple[Expediente, Exception]] = []
    total = len(expedientes)
    _encargar_todos(expedientes, destino_de, analizador, aviso, cancelar)
    detectados: dict[int, str] = {}  # expediente -> tipo detectado (en modo automático)
    automatico = opciones.get("tipo_expediente", "auto") not in ("judicial", "extrajudicial")
    for n, exp in enumerate(expedientes):
        if cancelar is not None and cancelar.is_set():
            raise Cancelado()
        try:
            docs = procesar(exp, destino_de(exp), analizador, cancelar=cancelar,
                            aviso=lambda m, f, n=n: aviso(m, (n + f) / total), **opciones)
            if automatico:
                detectados[n] = decidir_ambito(docs, "auto")
        except (Cancelado, CuotaAgotada, ModeloNoDisponible):
            raise
        except Exception as e:
            if getattr(e, "code", None) in (400, 401, 402, 403, 404):
                raise  # credenciales o configuración: fallaría igual en todos
            log.exception("Ha fallado «%s»; se sigue con los demás expedientes", exp.nombre)
            aviso(f"ERROR en «{exp.nombre}» ({type(e).__name__}). Se sigue con los demás.",
                  (n + 1) / total)
            fallidos.append((exp, e))
    _unificar_tipo(expedientes, detectados, destino_de, analizador, aviso, cancelar, opciones)
    return fallidos


def _unificar_tipo(expedientes, detectados, destino_de, analizador, aviso, cancelar, opciones) -> None:
    """Varios archivos procesados juntos suelen ser piezas del mismo asunto: si alguno es
    judicial, todos lo son. Los que salieron como extrajudiciales se rehacen como judiciales,
    con lo ya leído (sin consultas nuevas ni coste)."""
    if len(detectados) < 2 or "judicial" not in detectados.values():
        return
    rehacer = [n for n, tipo in detectados.items() if tipo != "judicial"]
    if not rehacer:
        return
    log.info("Tipo de expediente decidido para el lote: judicial; se rehacen %d archivos", len(rehacer))
    for k, n in enumerate(rehacer):
        if cancelar is not None and cancelar.is_set():
            raise Cancelado()
        exp = expedientes[n]
        aviso(f"El lote es judicial: rehaciendo «{exp.nombre}» con la cronología procesal "
              f"({k + 1} de {len(rehacer)})…", 0.99)
        try:
            procesar(exp, destino_de(exp), analizador, cancelar=cancelar,
                     aviso=lambda m, f: None, **dict(opciones, tipo_expediente="lote:judicial"))
        except Cancelado:
            raise
        except Exception:
            log.exception("No se pudo rehacer «%s» como judicial; queda como estaba", exp.nombre)


def _encargar_todos(expedientes, destino_de, analizador, aviso, cancelar) -> None:
    """Modo económico con varios expedientes: encarga a Mistral el OCR de todos antes de
    esperar al primero, para que los procese a la vez y no uno tras otro."""
    ocr = getattr(analizador, "ocr_externo", None)
    if ocr is None or not getattr(ocr, "economico", False) or len(expedientes) < 2:
        return
    from .ocr_gemini import ConsultaCancelada, texto_util

    for n, exp in enumerate(expedientes):
        if cancelar is not None and cancelar.is_set() or not ocr.economico:
            return
        try:
            carpeta = destino_de(exp)
            carpeta.mkdir(parents=True, exist_ok=True)
            huella = exp.huella()
            leidas = _cargar_cache(carpeta / f"{exp.nombre}.ocr.json", huella, analizador.firma)
            with unir(exp.rutas) as pdf:
                sin_texto = [i for i in range(pdf.page_count) if (i + 1) not in leidas and (
                    not analizador.usar_texto or not texto_util(pdf[i].get_text()))]
                if sin_texto:
                    aviso(f"Modo económico: encargando el OCR de «{exp.nombre}» "
                          f"({n + 1} de {len(expedientes)})…", 0.0)
                    ocr.encargar(pdf, sin_texto, carpeta / f"{exp.nombre}.mistral.json", huella,
                                 aviso=lambda m: aviso(m, 0.0), cancelar=cancelar)
        except ConsultaCancelada:
            raise Cancelado() from None
        except Exception as e:
            if getattr(e, "code", None) in (401, 403):
                raise
            log.warning("Modo económico: no se pudo encargar «%s» por adelantado: %s", exp.nombre, e)


def resumen_fallos(fallidos: list[tuple[Expediente, Exception]], total: int) -> str:
    lineas = [f"Terminado, pero {len(fallidos)} de {total} expedientes no se han podido completar:"]
    lineas += [f"- {exp.nombre}: {type(e).__name__}: {str(e)[:150]}" for exp, e in fallidos]
    lineas.append("Los demás se han procesado bien. El detalle está en «Ver registro»; lo ya leído "
                  "de los que fallaron queda guardado y no se vuelve a pagar al repetirlos.")
    return "\n".join(lineas)


def carpeta_de(exp: Expediente, salida: str | Path | None, plantillas: Plantillas | None = None) -> Path:
    """Cada expediente tiene su propia carpeta con todo lo que se genera."""
    from .config import carpeta_por_defecto

    base = Path(salida) if salida else carpeta_por_defecto()
    return base / (plantillas or Plantillas()).de_carpeta(exp.nombre)


def crear_analizador(cfg):
    if cfg.modo == "basico":
        from .clasificador_local import AnalizadorBasico

        return AnalizadorBasico()
    if cfg.motor_clasificacion == "claude":
        from .clasificador_claude import AnalizadorClaude

        analizador = AnalizadorClaude(cfg.claude_clave, cfg.claude_modelo, cfg.paginas_por_lote,
                                      paralelo=cfg.consultas_paralelas, usar_texto=cfg.usar_texto_existente)
        if cfg.mistral_clave:  # las páginas escaneadas las lee Mistral OCR
            from .ocr_mistral import OCRMistral

            analizador.ocr_externo = OCRMistral(cfg.mistral_clave, cfg.mistral_servidor, cfg.mistral_modelo,
                                                economico=cfg.mistral_economico)
        return analizador
    if cfg.motor_clasificacion == "mistral":
        from .clasificador_mistral import AnalizadorMistral
        from .ocr_mistral import OCRMistral

        analizador = AnalizadorMistral(cfg.mistral_clave, cfg.mistral_servidor,
                                       cfg.mistral_modelo_clasificacion, cfg.paginas_por_lote,
                                       paralelo=cfg.consultas_paralelas,
                                       usar_texto=cfg.usar_texto_existente)
        # sin Gemini, las páginas escaneadas solo las puede leer Mistral OCR
        analizador.ocr_externo = OCRMistral(cfg.mistral_clave, cfg.mistral_servidor, cfg.mistral_modelo,
                                            economico=cfg.mistral_economico)
        return analizador
    from .ocr_gemini import AnalizadorGemini

    analizador = AnalizadorGemini(cfg.proyecto, cfg.ubicacion, cfg.modelo, cfg.credenciales,
                                  cfg.paginas_por_lote, cfg.ppp, paralelo=cfg.consultas_paralelas,
                                  usar_texto=cfg.usar_texto_existente)
    if cfg.motor_ocr == "mistral":
        from .ocr_mistral import OCRMistral

        analizador.ocr_externo = OCRMistral(cfg.mistral_clave, cfg.mistral_servidor, cfg.mistral_modelo,
                                            economico=cfg.mistral_economico)
    return analizador
