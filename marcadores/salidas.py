"""Genera el PDF con marcadores y el índice en Excel."""
from __future__ import annotations

import re
from pathlib import Path

import pymupdf as fitz

from .modelos import GRUPOS, PLURALES, Documento, PaginaInfo, fecha_es, limpiar



def grupos_de(docs: list[Documento]) -> list[tuple[str, list[Documento]]]:
    """Índices temáticos con contenido, en el orden del expediente."""
    salida = []
    for nombre, tipos in GRUPOS.items():
        lista = [d for d in docs if d.tipo in tipos]
        if lista:
            salida.append((nombre, lista))
    return salida


def subgrupos_de(nombre: str, lista: list[Documento]) -> list[tuple[str, list[Documento]]]:
    """Resoluciones por clase (sentencias, autos…) y escritos por parte que los presenta."""
    orden: dict[str, list[Documento]] = {}
    if nombre == "Resoluciones":
        for tipo in GRUPOS[nombre]:
            sub = [d for d in lista if d.tipo == tipo]
            if sub:
                orden[PLURALES[tipo]] = sub
    elif nombre == "Escritos de parte":
        for d in lista:
            orden.setdefault(d.emisor or "Parte no identificada", []).append(d)
    return list(orden.items())


def secciones_de(docs: list[Documento]) -> list[tuple[str, list[Documento]]]:
    """Secciones del índice: cada clase de resolución por separado y el resto de grupos."""
    salida = []
    for nombre, lista in grupos_de(docs):
        if nombre == "Resoluciones":
            salida += [(f"{t} ({len(l)})", l) for t, l in subgrupos_de(nombre, lista)]
        else:
            salida.append((f"{nombre} ({len(lista)})", lista))
    return salida


def senalamientos_de(docs: list[Documento]) -> list[tuple[Documento, dict]]:
    return [(d, x) for d in docs for x in d.senalamientos]


def plazos_de(docs: list[Documento]) -> list[tuple[Documento, dict]]:
    return [(d, x) for d in docs for x in d.plazos]


def cronologia_de(docs: list[Documento], ambito: str) -> list[dict]:
    """Hitos ordenados por fecha real (no por orden de escaneo).

    Judicial: escritos, resoluciones, notificaciones, actas…, sin la prueba documental, y los
    señalamientos en su propia fecha. Extrajudicial: todos los documentos (son los hechos).
    Lo que no tiene fecha va al final.
    """
    from datetime import date

    hoy = date.today().isoformat()
    hitos = []
    for d in docs:
        if ambito == "judicial" and d.tipo in ("documento", "otros"):
            continue
        hito = d.titulo or d.clase
        if d.subtipo and d.subtipo.lower() not in hito.lower():
            hito = f"{d.subtipo}: {hito}"
        for x in d.plazos:
            hito += f" · plazo: {x.get('plazo', '')} ({x.get('concepto', '')})"
        hitos.append({"fecha": d.fecha, "hora": "", "hito": hito, "quien": d.emisor,
                      "pagina": d.pagina_inicio, "doc": d, "pendiente": False})
        for x in d.senalamientos:
            fecha = x.get("fecha", "")
            acto = " · ".join(t for t in (x.get("acto", ""), x.get("lugar", "")) if t)
            pendiente = bool(fecha) and fecha >= hoy
            hitos.append({"fecha": fecha, "hora": x.get("hora", ""),
                          "hito": f"Señalamiento: {acto}" + (" (pendiente)" if pendiente else ""),
                          "quien": f"acordado en doc. {d.numero:03d}", "pagina": x["pagina"],
                          "doc": d, "pendiente": pendiente})
    con_fecha = sorted((h for h in hitos if fecha_es(h["fecha"])),
                       key=lambda h: (h["fecha"], h["hora"], h["pagina"]))
    return con_fecha + [h for h in hitos if not fecha_es(h["fecha"])]


def titulo_cronologia(ambito: str) -> str:
    return "Cronología procesal" if ambito == "judicial" else "Cronología de hechos"


def _cuando(s: dict) -> str:
    return " ".join(x for x in (fecha_es(s.get("fecha", "")) or s.get("fecha", ""), s.get("hora", "")) if x)


def etiquetas_de(docs: list[Documento]) -> list[tuple[str, list[Documento]]]:
    orden: dict[str, list[Documento]] = {}
    for d in docs:
        for e in d.etiquetas:
            orden.setdefault(e, []).append(d)
    return list(orden.items())


# --- PDF -------------------------------------------------------------------
def _capa_texto(pdf: fitz.Document, paginas: list[PaginaInfo], giros: dict | None = None) -> None:
    """Pone el texto del OCR, invisible, encima de la imagen de cada página.

    Con los recuadros de línea que devuelve el modelo, cada línea queda sobre su sitio: se
    puede buscar, seleccionar y copiar como en un PDF con texto. Sin recuadros, el texto se
    añade en un bloque (buscable, pero sin posición).
    """
    for p in paginas:
        pagina = pdf[p.pagina - 1]
        if not p.texto.strip() or len(pagina.get_text().strip()) > 20:
            continue
        pagina.remove_rotation()
        ancho, alto = pagina.rect.width, pagina.rect.height
        # texto en vertical: hacia abajo si lo de arriba del folio quedó a la derecha, si no, hacia arriba
        vertical = 90 if (giros or {}).get(p.pagina) == 90 else 270
        colocadas = 0
        for linea in p.lineas:
            original = str(linea[0])
            texto = _latin(original)
            y0, x0, y1, x1 = (float(n) for n in linea[1:5])
            if not texto or y1 <= y0 or x1 <= x0:
                continue
            x0, x1, y0, y1 = x0 / 1000 * ancho, x1 / 1000 * ancho, y0 / 1000 * alto, y1 / 1000 * alto
            largo = fitz.get_text_length(texto, "helv", 1)
            if largo <= 0:
                continue
            if (y1 - y0) > 1.5 * (x1 - x0) and len(texto.strip()) > 3:
                # recuadro alto y estrecho: página escaneada de lado (el texto va en vertical).
                # Se escribe girado para que las palabras no se partan letra a letra.
                parrafo = "\n".join(_latin(l) for l in original.splitlines() if l.strip())
                for tam in (11, 9, 7, 6, 5, 4, 3, 2):
                    if pagina.insert_textbox(fitz.Rect(x0, y0, x1, y1), parrafo, fontsize=tam,
                                             fontname="helv", render_mode=3, rotate=vertical) >= 0:
                        colocadas += 1
                        break
                continue
            # tamaño de letra que hace que la línea ocupe justo el ancho de su recuadro
            cuerpo = max(2.0, min((x1 - x0) / largo, (y1 - y0) * 0.8, 60.0))
            if "\n" in original.strip() or (y1 - y0) > 2.5 * cuerpo:
                # párrafo (los bloques de Mistral): el texto fluye dentro de su recuadro
                parrafo = "\n".join(_latin(l) for l in original.splitlines() if l.strip())
                for tam in (11, 9, 7, 6, 5, 4, 3, 2):
                    if pagina.insert_textbox(fitz.Rect(x0, y0, x1, y1), parrafo, fontsize=tam,
                                             fontname="helv", render_mode=3) >= 0:
                        colocadas += 1
                        break
                continue
            pagina.insert_text((x0, y1 - (y1 - y0) * 0.22), texto, fontsize=cuerpo,
                               fontname="helv", render_mode=3)
            colocadas += 1
        if colocadas:
            continue
        texto = p.texto.encode("latin-1", "replace").decode("latin-1")
        caja = pagina.rect + (10, 10, -10, -10)
        for cuerpo in (8, 6, 4, 3, 2, 1):
            if pagina.insert_textbox(caja, texto, fontsize=cuerpo, fontname="helv",
                                     render_mode=3) >= 0:
                break


def _doc_de(docs: list[Documento], pagina: int) -> Documento:
    return next(d for d in docs if d.pagina_inicio <= pagina <= d.pagina_fin)


def _situar_palabras(pdf: fitz.Document, apariciones: dict) -> dict:
    """Localiza cada palabra clave en su página: {etiqueta: [(página, palabra, altura)]}.

    Si la página tiene texto propio se resalta la palabra en amarillo y el marcador
    apunta a su altura exacta; en páginas escaneadas apunta al comienzo de la página.
    """
    situadas: dict[str, list[tuple[int, str, float]]] = {}
    for etiqueta, lista in apariciones.items():
        for pagina, palabra in lista:
            hoja = pdf[pagina - 1]
            zonas = hoja.search_for(palabra)
            altura = 0.0
            if zonas:
                altura = max(0.0, min(z.y0 for z in zonas) - 20)
                try:
                    nota = hoja.add_highlight_annot(zonas)
                    nota.set_info(title="Marcadores PDF", content=f"Palabra clave: {etiqueta}")
                    nota.update()
                except Exception:
                    pass
            situadas.setdefault(etiqueta, []).append((pagina, palabra, altura))
    return situadas


# --- índice dentro del PDF -------------------------------------------------
ANCHO, ALTO, MARGEN = 595, 842, 40
COLUMNAS = [(40, 26), (68, 108), (178, 54), (234, 272), (512, 43)]  # (x, ancho)
AZUL = (0.05, 0.2, 0.55)


def _latin(texto: str) -> str:
    cambios = {"…": "...", "“": '"', "”": '"', "‘": "'", "’": "'", "—": "-", "–": "-", "→": ">"}
    for a, b in cambios.items():
        texto = texto.replace(a, b)
    return " ".join(texto.encode("latin-1", "replace").decode("latin-1").split())


def _ajustar(texto: str, ancho: float, cuerpo: float, fuente: str = "helv") -> str:
    texto = _latin(texto)
    if fitz.get_text_length(texto, fuente, cuerpo) <= ancho:
        return texto
    while texto and fitz.get_text_length(texto + "...", fuente, cuerpo) > ancho:
        texto = texto[:-1]
    return texto.rstrip() + "..."


AMARILLO = (1.0, 0.95, 0.6)
CAB_DOCS = ["Nº", "Clase", "Fecha", "Descripción", "Págs."]


def _fila_doc(d: Documento) -> list[str]:
    descripcion = d.titulo or d.tipo_nombre
    if d.tipo == "escrito_parte" and d.emisor:
        descripcion += f" - {d.emisor}"
    if d.etiquetas:
        descripcion = f"[{', '.join(d.etiquetas)}] " + descripcion
    return [f"{d.numero:03d}", d.clase, d.fecha_es, descripcion, d.paginas]


def _filas_docs(lista: list[Documento]) -> list[tuple]:
    # (valores, página destino, altura, resaltar por contener palabras clave)
    return [(_fila_doc(d), d.pagina_inicio, 0.0, bool(d.etiquetas)) for d in lista]


# Qué puede llevar el índice, además de la lista de todos los documentos (que va siempre).
# Se aplica igual al índice impreso y a los marcadores del PDF.
CONTENIDO_INDICE = ("cronologia", "grupos", "agenda", "remisiones", "palabras")

TINTA_IND = (0.11, 0.11, 0.12)
GRIS_IND = (0.43, 0.43, 0.45)
CEBRA = (0.965, 0.965, 0.97)
FONDO_CAB = (0.93, 0.93, 0.94)
FRANJA = (0.16, 0.18, 0.22)  # banda de cada bloque (resoluciones, declaraciones…)
NOMBRES_BLOQUE = {"Resoluciones": "Resoluciones judiciales"}


def _bloques(docs: list[Documento], situadas: dict, ambito: str, contenido, remisiones=()) -> list:
    """[(banda, [(subtítulo o None, cabeceras, filas, columnas[, página del subtítulo])])]."""
    bloques = []
    if "cronologia" in contenido:
        hitos = cronologia_de(docs, ambito)
        if hitos:
            bloques.append((f"{titulo_cronologia(ambito)} ({len(hitos)})", [(None,
                            ["Fecha", "Hito", "Quién", "Pág."],
                            [([" ".join(t for t in (fecha_es(h["fecha"]) or "s/f", h["hora"]) if t),
                               h["hito"], h["quien"], str(h["pagina"])], h["pagina"], 0.0, False)
                             for h in hitos],
                            [(40, 80), (120, 250), (380, 130), (512, 43)])]))
    if "grupos" in contenido:
        for nombre, lista in grupos_de(docs):
            banda = f"{NOMBRES_BLOQUE.get(nombre, nombre)} ({len(lista)})"
            if nombre == "Resoluciones":
                partes = [(f"{t} ({len(l)})", CAB_DOCS, _filas_docs(l), COLUMNAS)
                          for t, l in subgrupos_de(nombre, lista)]
            else:
                partes = [(None, CAB_DOCS, _filas_docs(lista), COLUMNAS)]
            bloques.append((banda, partes))
    if "agenda" in contenido:
        partes = []
        citas = senalamientos_de(docs)
        if citas:
            partes.append((f"Señalamientos ({len(citas)})",
                           ["Nº", "Día y hora", "Acto", "Lugar y resolución que lo acuerda", "Pág."],
                           [([f"{d.numero:03d}", _cuando(x), x.get("acto", ""),
                              " - ".join(t for t in (x.get("lugar", ""), d.titulo or d.clase) if t),
                              str(x["pagina"])], x["pagina"], 0.0, False) for d, x in citas],
                           [(40, 26), (68, 86), (156, 170), (328, 182), (512, 43)]))
        plazos = plazos_de(docs)
        if plazos:
            partes.append((f"Plazos ({len(plazos)})",
                           ["Nº", "Plazo", "Fecha doc." if ambito != "judicial" else "Fecha res.",
                            "Para qué (según el documento)" if ambito != "judicial"
                            else "Para qué (según la resolución)", "Pág."],
                           [([f"{d.numero:03d}", x.get("plazo", ""), d.fecha_es,
                              x.get("concepto", ""), str(x["pagina"])], x["pagina"], 0.0, False)
                            for d, x in plazos],
                           [(40, 26), (68, 96), (166, 54), (222, 288), (512, 43)]))
        if partes:
            bloques.append(("Señalamientos y plazos", partes))
    if "remisiones" in contenido and remisiones:
        from .remisiones import citado_por

        partes = []
        for citado, citas in citado_por(remisiones):
            filas = [([f"{c.origen.numero:03d}", c.origen.clase, c.origen.fecha_es,
                       f"«{c.frase}» en: {c.origen.titulo or c.origen.tipo_nombre}", str(c.pagina)],
                      c.pagina, 0.0, False) for c in citas]
            partes.append((f"{citado.rotulo} (pág. {citado.pagina_inicio}) · citado {len(citas)} "
                           f"{'vez' if len(citas) == 1 else 'veces'}", ["Nº", "Lo cita", "Fecha", "Cita", "Pág."],
                           filas, COLUMNAS, citado.pagina_inicio))
        if partes:
            bloques.append((f"Remisiones entre documentos ({sum(len(p[2]) for p in partes)})", partes))
    if "palabras" in contenido and situadas:
        partes = []
        for etiqueta, lista in situadas.items():
            filas = []
            for pagina, palabra, altura in lista:
                d = _doc_de(docs, pagina)
                filas.append(([f"{d.numero:03d}", d.clase, d.fecha_es,
                               f"«{palabra}» en: {d.titulo or d.tipo_nombre}", str(pagina)],
                              pagina, altura, True))
            partes.append((f"{etiqueta} ({len(lista)})", CAB_DOCS, filas, COLUMNAS))
        bloques.append(("Palabras clave", partes))
    bloques.append((f"Todos los documentos · orden del escaneo ({len(docs)})",
                    [(None, CAB_DOCS, _filas_docs(docs), COLUMNAS)]))
    return bloques


def _crear_indice(nombre: str, docs: list[Documento], situadas: dict, total: int,
                  ambito: str = "judicial", detectado: bool | str = False, enderezadas: int = 0,
                  retiradas: list[int] | None = None, contenido=CONTENIDO_INDICE, remisiones=()):
    """Devuelve (PDF del índice, enlaces [(hoja del índice, rectángulo, página destino, altura)]).

    Cada bloque (cronología, resoluciones judiciales, declaraciones…) abre con una banda oscura
    a todo lo ancho, para que el cambio de contexto se vea al pasar las hojas; dentro, los
    subapartados (sentencias, autos…) llevan su rótulo y las filas van en cebra.
    """
    bloques = _bloques(docs, situadas, ambito, contenido, remisiones)
    indice = fitz.open()
    enlaces = []
    estado = {"hoja": None, "y": 0.0, "fila": 0}
    ancho_util = ANCHO - 2 * MARGEN

    def nueva_hoja():
        estado["hoja"] = indice.new_page(width=ANCHO, height=ALTO)
        estado["y"] = MARGEN + 10

    def cabecera(titulos, columnas):
        hoja, y = estado["hoja"], estado["y"]
        hoja.draw_rect(fitz.Rect(MARGEN, y - 9, ANCHO - MARGEN, y + 4), color=None, fill=FONDO_CAB)
        for (x, _), t in zip(columnas, titulos):
            hoja.insert_text((x, y), _latin(t), fontname="hebo", fontsize=7.5, color=GRIS_IND)
        estado["y"] += 15
        estado["fila"] = 0

    def banda(texto: str):
        hoja, y = estado["hoja"], estado["y"]
        hoja.draw_rect(fitz.Rect(MARGEN, y, ANCHO - MARGEN, y + 22), color=None, fill=FRANJA)
        hoja.insert_text((MARGEN + 10, y + 15), _ajustar(texto.upper(), ancho_util - 20, 10.5, "hebo"),
                         fontname="hebo", fontsize=10.5, color=(1, 1, 1))
        estado["y"] += 22 + 18

    def subtitulo(texto: str, destino: int | None = None):
        hoja, y = estado["hoja"], estado["y"]
        hoja.insert_text((MARGEN, y), _ajustar(texto, ancho_util, 9.5, "hebo"), fontname="hebo",
                         fontsize=9.5, color=AZUL)
        if destino:  # el rótulo lleva al documento (en «Remisiones», al documento citado)
            enlaces.append((hoja.number, fitz.Rect(MARGEN, y - 10, ANCHO - MARGEN, y + 4), destino, 0.0))
        hoja.draw_line((MARGEN, y + 4), (ANCHO - MARGEN, y + 4), color=AZUL, width=0.6)
        estado["y"] += 17

    nueva_hoja()
    estado["hoja"].insert_text((MARGEN, estado["y"] + 6),
                               _ajustar(f"Índice del expediente: {nombre}", ancho_util, 15, "hebo"),
                               fontname="hebo", fontsize=15, color=TINTA_IND)
    estado["y"] += 24
    explicacion = {"lote": " (decidido para todos los archivos procesados juntos: alguno es judicial)",
                   True: " (detectado automáticamente; se puede elegir en el programa)"}
    notas = [f"Expediente {ambito}" + explicacion.get(detectado, ""),
             f"{len(docs)} documentos · {total} páginas · orden original del escaneo. "
             "Pulse sobre cualquier línea para ir al documento."]
    if enderezadas:
        notas.append(f"Se han enderezado {enderezadas} páginas escaneadas de lado o boca abajo.")
    if retiradas:
        lista = ", ".join(str(n) for n in retiradas[:40]) + (" ..." if len(retiradas) > 40 else "")
        notas.append(f"Retiradas {len(retiradas)} páginas en blanco (numeración del original): {lista}.")
    if any(d.etiquetas for d in docs):
        notas.append("En amarillo, los documentos que contienen palabras clave.")
    if "agenda" in contenido and plazos_de(docs):
        notas.append("Los plazos se transcriben " + ("de las resoluciones" if ambito == "judicial"
                     else "de los documentos") + "; no se calcula su vencimiento.")
    for nota in notas:
        estado["hoja"].insert_text((MARGEN, estado["y"]), _latin(nota), fontname="helv",
                                   fontsize=8.5, color=GRIS_IND)
        estado["y"] += 12
    estado["y"] += 8
    for titulo_banda, partes in bloques:
        # la banda no se queda sola al pie: con ella van al menos el rótulo, la cabecera y dos filas
        if estado["y"] + 22 + 18 + (17 if partes[0][0] else 0) + 15 + 26 > ALTO - MARGEN:
            nueva_hoja()
        banda(titulo_banda)
        for n, (sub, titulos, filas, columnas, *resto) in enumerate(partes):
            if n and estado["y"] + 17 + 15 + 26 + 10 > ALTO - MARGEN:
                nueva_hoja()
            elif n:
                estado["y"] += 10
            if sub:
                subtitulo(sub, resto[0] if resto else None)
            cabecera(titulos, columnas)
            for valores, destino, altura, resaltar in filas:
                if estado["y"] + 13 > ALTO - MARGEN:
                    nueva_hoja()
                    seguido = f"{titulo_banda} · {sub}" if sub else titulo_banda
                    estado["hoja"].insert_text((MARGEN, estado["y"]), _latin(f"{seguido} (continuación)"),
                                               fontname="helv", fontsize=8, color=GRIS_IND)
                    estado["y"] += 14
                    cabecera(titulos, columnas)
                hoja, y = estado["hoja"], estado["y"]
                zona = fitz.Rect(MARGEN, y - 9, ANCHO - MARGEN, y + 4)
                if resaltar:
                    hoja.draw_rect(zona, color=None, fill=AMARILLO)
                elif estado["fila"] % 2:
                    hoja.draw_rect(zona, color=None, fill=CEBRA)
                for (x, ancho), valor in zip(columnas, valores):
                    hoja.insert_text((x, y), _ajustar(valor, ancho - 4, 8.5), fontname="helv",
                                     fontsize=8.5, color=AZUL if x == columnas[-1][0] else TINTA_IND)
                enlaces.append((hoja.number, zona, destino, altura))
                estado["y"] += 13
                estado["fila"] += 1
        estado["y"] += 14
    return indice, enlaces


# --- botones de navegación sobre las páginas -------------------------------
FONDO_BOTON = (0.95, 0.96, 0.97)
TINTA_BOTON = (0.16, 0.18, 0.22)


def _boton(hoja, x: float, y: float, texto: str, pagina: int, altura: float = 0.0,
           a_la_derecha: bool = False) -> float:
    """Botón pequeño que salta a otra página. Devuelve su ancho.

    Es una anotación, no tinta sobre la página: el documento original no se toca, el botón
    se ve en pantalla y NO se imprime (va sin la marca de impresión). Encima lleva el enlace.
    """
    texto = _latin(texto)
    ancho = fitz.get_text_length(texto, "helv", 7) + 10
    if a_la_derecha:
        x -= ancho
    zona = fitz.Rect(x, y, x + ancho, y + 12) * hoja.derotation_matrix  # la página puede estar girada
    zona.normalize()
    try:
        nota = hoja.add_freetext_annot(zona, texto, fontsize=7, fontname="helv", text_color=TINTA_BOTON,
                                       fill_color=FONDO_BOTON, align=fitz.TEXT_ALIGN_CENTER,
                                       rotate=hoja.rotation)
        nota.set_info(title="Marcadores PDF", content=texto)
        nota.set_flags(fitz.PDF_ANNOT_IS_READ_ONLY | fitz.PDF_ANNOT_IS_LOCKED)  # sin PRINT
        nota.update()
        hoja.insert_link({"kind": fitz.LINK_GOTO, "from": zona, "page": pagina, "to": fitz.Point(0, altura)})
    except Exception:
        return 0.0  # una página rara no puede impedir que se genere el PDF
    return ancho


def _enlazar_remisiones(pdf: fitz.Document, remisiones, hojas_indice: int) -> int:
    """Convierte cada cita localizada («auto de 3 de marzo de 2024») en un enlace al documento citado.

    Si la frase se encuentra en el texto de la página, se subraya en azul y se enlaza ahí mismo.
    En páginas escaneadas cuyo texto no tiene posición fiable, queda un botón en el margen
    superior («→ Auto 03/03/2024»). Devuelve cuántas se han enlazado.
    """
    hechas = 0
    botones: dict[int, float] = {}  # página -> x del siguiente botón de la segunda fila
    for r in remisiones:
        if r.destino is None:
            continue
        hoja = pdf[r.pagina - 1 + hojas_indice]
        destino = r.destino.pagina_inicio - 1 + hojas_indice
        rotulo = f"Remite a: {r.destino.rotulo} (pág. {r.destino.pagina_inicio})"
        try:
            cuadros = hoja.search_for(r.frase, quads=True)
        except Exception:
            cuadros = []
        if cuadros:
            try:
                nota = hoja.add_underline_annot(cuadros)
                nota.set_colors(stroke=AZUL)
                nota.set_info(title="Marcadores PDF", content=rotulo)
                nota.update()
                for c in cuadros:
                    hoja.insert_link({"kind": fitz.LINK_GOTO, "from": c.rect, "page": destino,
                                      "to": fitz.Point(0, 0)})
                hechas += 1
                continue
            except Exception:
                pass
        x = botones.get(r.pagina, 8.0)
        ancho = _boton(hoja, x, 21, f"-> {r.destino.tipo_nombre} {r.destino.fecha_es}", destino)
        if ancho:
            botones[r.pagina] = x + ancho + 4
            hechas += 1
    return hechas


def _navegacion(pdf: fitz.Document, docs: list[Documento], situadas: dict, hojas_indice: int,
                filas_indice: dict[int, tuple[int, float]]) -> None:
    """Botones en el margen superior de cada página:

    - «Índice» (todas las páginas): vuelve a la fila del documento en el índice;
    - «« Auto» y «Auto »» (primera página de cada documento): documento anterior y siguiente
      de la misma clase (autos con autos, providencias con providencias…);
    - «« Costas» y «Costas »» (páginas con una palabra clave): aparición anterior y siguiente.
    """
    por_clase: dict[str, list[Documento]] = {}
    for d in docs:
        por_clase.setdefault(d.tipo, []).append(d)
    vecinos = {}
    for lista in por_clase.values():
        for i, d in enumerate(lista):
            vecinos[d.numero] = (lista[i - 1] if i else None, lista[i + 1] if i + 1 < len(lista) else None)
    palabras: dict[int, list[tuple[str, int | None, int | None]]] = {}
    for etiqueta, lista in situadas.items():
        hojas = sorted({pagina for pagina, _, _ in lista})
        for i, pagina in enumerate(hojas):
            palabras.setdefault(pagina, []).append(
                (etiqueta, hojas[i - 1] if i else None, hojas[i + 1] if i + 1 < len(hojas) else None))
    for d in docs:
        for pagina in range(d.pagina_inicio, d.pagina_fin + 1):
            hoja = pdf[pagina - 1 + hojas_indice]
            x, y = hoja.rect.width - 8, 6
            if d.numero in filas_indice:
                hoja_indice, altura = filas_indice[d.numero]
                x -= _boton(hoja, x, y, "Índice", hoja_indice, altura, a_la_derecha=True) + 4
            if pagina == d.pagina_inicio:
                anterior, siguiente = vecinos[d.numero]
                nombre = d.tipo_nombre
                if siguiente:
                    x -= _boton(hoja, x, y, f"{nombre} »", siguiente.pagina_inicio - 1 + hojas_indice,
                                a_la_derecha=True) + 4
                if anterior:
                    _boton(hoja, x, y, f"« {nombre}", anterior.pagina_inicio - 1 + hojas_indice,
                           a_la_derecha=True)
            x = 8.0
            for etiqueta, antes, despues in palabras.get(pagina, []):
                if antes:
                    x += _boton(hoja, x, y, f"« {etiqueta}", antes - 1 + hojas_indice) + 4
                if despues:
                    x += _boton(hoja, x, y, f"{etiqueta} »", despues - 1 + hojas_indice) + 4


def escribir_pdf(pdf: fitz.Document, docs: list[Documento], paginas: list[PaginaInfo],
                 destino: Path, capa_texto: bool = True, apariciones: dict | None = None,
                 indice_en_pdf: bool = True, nombre: str = "", ambito: str = "judicial",
                 detectado: bool | str = False, giros: dict | None = None,
                 retiradas: list[int] | None = None, contenido=CONTENIDO_INDICE,
                 navegacion: bool = True, remisiones=()) -> None:
    total = pdf.page_count
    contenido = set(contenido)
    if capa_texto:
        _capa_texto(pdf, paginas, giros)
    situadas = _situar_palabras(pdf, apariciones or {})
    for numero, giro in (giros or {}).items():  # enderezar: solo la marca de giro, la imagen no cambia
        hoja = pdf[numero - 1]
        hoja.set_rotation((hoja.rotation + giro) % 360)

    hojas_indice = 0
    filas_indice: dict[int, tuple[int, float]] = {}
    if indice_en_pdf and docs:
        indice, enlaces = _crear_indice(nombre or destino.stem, docs, situadas, total, ambito, detectado,
                                        len(giros or {}), retiradas or [], contenido, remisiones)
        hojas_indice = indice.page_count
        pdf.insert_pdf(indice, start_at=0)
        indice.close()
        # el último bloque del índice es la lista de todos los documentos, en su orden:
        # ahí vuelve el botón «Índice» de cada página
        for d, (hoja, rect, _, _) in zip(docs, enlaces[-len(docs):]):
            filas_indice[d.numero] = (hoja, max(0.0, rect.y0 - 60))
        for hoja, rect, pagina, altura in enlaces:
            pdf[hoja].insert_link({"kind": fitz.LINK_GOTO, "from": rect,
                                   "page": pagina - 1 + hojas_indice, "to": fitz.Point(0, altura)})
        # el visor numera el índice aparte (i, ii…) y el expediente sigue empezando en la página 1
        pdf.set_page_labels([
            {"startpage": 0, "prefix": "Índice ", "style": "r", "firstpagenum": 1},
            {"startpage": hojas_indice, "prefix": "", "style": "D", "firstpagenum": 1},
        ])

    if navegacion and docs:
        _navegacion(pdf, docs, situadas, hojas_indice, filas_indice)
        _enlazar_remisiones(pdf, remisiones, hojas_indice)

    def entrada(nivel: int, titulo: str, pagina: int, altura: float = 0.0):
        real = pagina + hojas_indice
        return [nivel, titulo, real, {"kind": fitz.LINK_GOTO, "page": real - 1,
                                      "to": fitz.Point(0, altura)}]

    toc = []
    if hojas_indice:
        toc.append([1, "Índice", 1])
    if docs:
        hitos = cronologia_de(docs, ambito) if "cronologia" in contenido else []
        if hitos:
            toc.append(entrada(1, f"{titulo_cronologia(ambito)} ({len(hitos)})", hitos[0]["pagina"]))
            toc += [entrada(2, f"{fecha_es(h['fecha']) or 's/f'} · {h['hito'][:110]}", h["pagina"])
                    for h in hitos]
        toc.append(entrada(1, "Documentos (orden del escaneo)", docs[0].pagina_inicio))
        toc += [entrada(2, d.rotulo, d.pagina_inicio) for d in docs]
        for titulo, lista in (grupos_de(docs) if "grupos" in contenido else []):
            toc.append(entrada(1, f"{titulo} ({len(lista)})", lista[0].pagina_inicio))
            subgrupos = subgrupos_de(titulo, lista)
            if subgrupos:
                for nombre_sub, sub in subgrupos:
                    toc.append(entrada(2, f"{nombre_sub} ({len(sub)})", sub[0].pagina_inicio))
                    toc += [entrada(3, d.rotulo, d.pagina_inicio) for d in sub]
            else:
                toc += [entrada(2, d.rotulo, d.pagina_inicio) for d in lista]
        citas = senalamientos_de(docs) if "agenda" in contenido else []
        if citas:
            toc.append(entrada(1, f"Señalamientos ({len(citas)})", citas[0][1]["pagina"]))
            toc += [entrada(2, f"{_cuando(x)} · {x.get('acto', '')} (pág. {x['pagina']})", x["pagina"])
                    for _, x in citas]
        plazos = plazos_de(docs) if "agenda" in contenido else []
        if plazos:
            toc.append(entrada(1, f"Plazos ({len(plazos)})", plazos[0][1]["pagina"]))
            toc += [entrada(2, f"{x.get('plazo', '')} · {x.get('concepto', '')[:90]} (pág. {x['pagina']})",
                            x["pagina"]) for _, x in plazos]
        if remisiones and "remisiones" in contenido:
            from .remisiones import citado_por

            grupos = citado_por(remisiones)
            if grupos:
                toc.append(entrada(1, f"Remisiones ({sum(len(c) for _, c in grupos)})", grupos[0][0].pagina_inicio))
                for citado, citas in grupos:
                    toc.append(entrada(2, f"{citado.rotulo} · citado por {len(citas)}", citado.pagina_inicio))
                    toc += [entrada(3, f"pág. {c.pagina} · {c.origen.rotulo}", c.pagina) for c in citas]
        if situadas and "palabras" in contenido:
            primera = min(l[0][0] for l in situadas.values())
            toc.append(entrada(1, "Palabras clave", primera))
            for etiqueta, lista in situadas.items():
                toc.append(entrada(2, f"{etiqueta} ({len(lista)})", lista[0][0], lista[0][2]))
                for pagina, palabra, altura in lista:
                    d = _doc_de(docs, pagina)
                    toc.append(entrada(3, f"pág. {pagina} · «{palabra}» · {d.rotulo}", pagina, altura))
    pdf.set_toc(toc, collapse=1)
    # que el lector abra el PDF con el panel de marcadores visible
    pdf.xref_set_key(pdf.pdf_catalog(), "PageMode", "/UseOutlines")
    pdf.save(destino, garbage=3, deflate=True)


def separar_documentos(pdf: fitz.Document, docs: list[Documento], carpeta: Path, nombrar=None) -> None:
    """Un PDF por documento. «nombrar» da el nombre de cada archivo; si se repite, se numera."""
    from .nombres import Plantillas

    nombrar = nombrar or Plantillas().de_documento
    carpeta.mkdir(parents=True, exist_ok=True)
    usados: set[str] = set()
    for d in docs:
        nombre = nombrar(d)
        base, n = nombre[:-4], 2
        while nombre.lower() in usados:
            nombre = f"{base} ({n}).pdf"
            n += 1
        usados.add(nombre.lower())
        with fitz.open() as trozo:
            trozo.insert_pdf(pdf, from_page=d.pagina_inicio - 1, to_page=d.pagina_fin - 1)
            trozo.save(carpeta / nombre, garbage=3, deflate=True)


# --- Excel -----------------------------------------------------------------
def escribir_excel(docs: list[Documento], destino: Path, ambito: str = "judicial", remisiones=()) -> None:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    amarillo = PatternFill("solid", fgColor="FFF299")
    libro = Workbook()

    def hoja(titulo, cabeceras, anchos, filas, primera=False):
        h = libro.active if primera else libro.create_sheet()
        h.title = titulo
        h.append(cabeceras)
        for c in h[1]:
            c.font = Font(bold=True)
        for valores, resaltar in filas:
            h.append([limpiar(v) if isinstance(v, str) else v for v in valores])
            if resaltar:
                for c in h[h.max_row]:
                    c.fill = amarillo
        for n, ancho in enumerate(anchos):
            h.column_dimensions[chr(65 + n)].width = ancho
        h.freeze_panes = "A2"
        h.auto_filter.ref = h.dimensions

    hoja("Índice",
         ["Nº", "Tipo", "Clase", "Fecha", "Descripción", "Órgano / autor", "Parte", "Posición",
          "Pág. inicio", "Pág. fin", "Nº págs.", "Palabras clave"],
         [6, 24, 32, 12, 60, 36, 30, 18, 11, 9, 9, 30],
         [([d.numero, d.tipo_nombre, d.subtipo, d.fecha_es, d.titulo or d.tipo_nombre, d.autor,
            d.parte, d.posicion, d.pagina_inicio, d.pagina_fin,
            d.pagina_fin - d.pagina_inicio + 1, ", ".join(d.etiquetas)], bool(d.etiquetas))
          for d in docs], primera=True)
    hoja("Cronología", ["Fecha", "Hora", "Hito", "Quién", "Clase", "Nº doc.", "Pág.", "Pendiente"],
         [12, 8, 80, 36, 30, 9, 8, 11],
         [([fecha_es(h["fecha"]) or "s/f", h["hora"], h["hito"], h["quien"], h["doc"].clase,
            h["doc"].numero, h["pagina"], "sí" if h["pendiente"] else ""], h["pendiente"])
          for h in cronologia_de(docs, ambito)])
    hoja("Señalamientos", ["Fecha", "Hora", "Acto", "Lugar", "Acordado en", "Nº doc.", "Pág."],
         [12, 8, 40, 30, 60, 9, 8],
         [([fecha_es(x.get("fecha", "")) or x.get("fecha", ""), x.get("hora", ""), x.get("acto", ""),
            x.get("lugar", ""), d.titulo or d.clase, d.numero, x["pagina"]], False)
          for d, x in senalamientos_de(docs)])
    hoja("Plazos", ["Plazo", "Para qué", "Resolución" if ambito == "judicial" else "Documento",
                    "Fecha resolución" if ambito == "judicial" else "Fecha documento", "Nº doc.", "Pág."],
         [22, 70, 50, 16, 9, 8],
         [([x.get("plazo", ""), x.get("concepto", ""), d.titulo or d.clase, d.fecha_es, d.numero,
            x["pagina"]], False) for d, x in plazos_de(docs)])
    if remisiones:
        hoja("Remisiones", ["Nº doc. que cita", "Documento que cita", "Pág.", "Cita", "Nº doc. citado",
                            "Documento citado", "Pág. del citado", "Observaciones", "Detectada por"],
             [10, 50, 8, 44, 10, 50, 10, 46, 12],
             [([r.origen.numero, r.origen.titulo or r.origen.clase, r.pagina, r.frase,
                r.destino.numero if r.destino else "", (r.destino.titulo or r.destino.clase) if r.destino else "",
                r.destino.pagina_inicio if r.destino else "",
                "" if r.destino else f"Sin localizar: {r.motivo}", r.fuente], r.destino is None)
              for r in remisiones])
    libro.save(destino)
