"""Piezas visuales de la ventana: colores, tipografía y controles dibujados con suavizado.

Tk no redondea esquinas ni suaviza curvas. Las tarjetas, botones, interruptores y casillas
se pintan con PyMuPDF a doble tamaño y se reducen (como los iconos del sistema); el resultado
se guarda en caché por tamaño y color. Todo lo demás son widgets normales de Tk.
"""
from __future__ import annotations

import base64
import tkinter as tk
from tkinter import font as tkfont

# --- colores (rediseño «Táctica Legal») ------------------------------------
ROJO = "#E8112D"  # marca y acción principal
ROJO_OSCURO = "#B80E24"  # pulsado
ROJO_SUAVE = "#FDE7EA"
TINTA = "#14171A"  # texto y panel de resumen
TINTA_2 = "#2A2F34"  # separadores dentro del panel oscuro
SECUNDARIO = "#4B5258"
APAGADO = "#8A9096"
FILETE = "#E4E6E8"
CAMPO = "#F3F4F5"  # fondo de campos y botones secundarios
FONDO = "#F3F4F5"  # fondo de la ventana
BLANCO = "#FFFFFF"
DESACTIVADO = "#C9CCCF"
AVISO_FONDO, AVISO_TEXTO = "#F6E9CF", "#7A4A0F"
BIEN_FONDO, BIEN_TEXTO = "#E3EEDF", "#2F5A3A"
AMARILLO = "#FFF3B0"


def familia(raiz) -> str:
    disponibles = set(tkfont.families(raiz))
    for f in ("Segoe UI Variable Text", "Segoe UI", "Inter", "Helvetica Neue", "DejaVu Sans"):
        if f in disponibles:
            return f
    return "TkDefaultFont"


def familia_titulos(raiz) -> str:
    disponibles = set(tkfont.families(raiz))
    for f in ("Segoe UI Variable Display", "Segoe UI Semibold", "Segoe UI", "Inter", "DejaVu Sans"):
        if f in disponibles:
            return f
    return familia(raiz)


def _rgb(color: str) -> tuple[float, float, float]:
    return tuple(int(color[i:i + 2], 16) / 255 for i in (1, 3, 5))


class Pintor:
    """Genera imágenes suavizadas y las guarda (Tk las pierde si nadie las referencia)."""

    def __init__(self, raiz):
        self.raiz = raiz
        self.escala = max(1.0, raiz.winfo_fpixels("1i") / 96)
        self._cache: dict = {}

    def px(self, valor: float) -> int:
        return max(1, round(valor * self.escala))

    def _pintar(self, ancho: int, alto: int, dibujar, fondo: str):
        import pymupdf as fitz

        doc = fitz.open()
        hoja = doc.new_page(width=ancho, height=alto)
        hoja.draw_rect(hoja.rect, color=None, fill=_rgb(fondo))
        dibujar(hoja, fitz)
        grande = hoja.get_pixmap(matrix=fitz.Matrix(3, 3), alpha=False)
        fino = fitz.Pixmap(grande, ancho, alto, None)  # reducción con promedio: bordes suaves
        doc.close()
        return tk.PhotoImage(master=self.raiz, data=base64.b64encode(fino.tobytes("png")))

    def caja(self, ancho: int, alto: int, radio: float, relleno: str, fondo: str,
             borde: str | None = None, grosor: float = 1.0):
        """Rectángulo redondeado (tarjetas, botones, campos)."""
        clave = ("caja", ancho, alto, radio, relleno, fondo, borde, grosor)
        if clave not in self._cache:
            def dibujar(hoja, fitz):
                g = grosor if borde else 0
                r = fitz.Rect(g / 2, g / 2, ancho - g / 2, alto - g / 2)
                rel = min(0.5, radio / max(1, min(r.width, r.height)))
                hoja.draw_rect(r, color=_rgb(borde) if borde else None, fill=_rgb(relleno),
                               width=g, radius=rel)
            self._cache[clave] = self._pintar(ancho, alto, dibujar, fondo)
        return self._cache[clave]

    def interruptor(self, encendido: bool, fondo: str, activo: bool = True):
        ancho, alto = self.px(42), self.px(26)
        clave = ("int", encendido, fondo, activo, ancho)
        if clave not in self._cache:
            def dibujar(hoja, fitz):
                pista = ROJO if encendido else DESACTIVADO
                if not activo:
                    pista = "#E6E8EA"
                hoja.draw_rect(fitz.Rect(0, 0, ancho, alto), color=None, fill=_rgb(pista), radius=0.5)
                d = alto - self.px(4)
                x = ancho - self.px(2) - d if encendido else self.px(2)
                hoja.draw_circle((x + d / 2, alto / 2 + 0.4), d / 2 + 0.3, color=None,
                                 fill=(0.82, 0.83, 0.84))  # sombra leve
                hoja.draw_circle((x + d / 2, alto / 2), d / 2, color=None, fill=(1, 1, 1))
            self._cache[clave] = self._pintar(ancho, alto, dibujar, fondo)
        return self._cache[clave]

    def radio(self, marcado: bool, fondo: str):
        lado = self.px(18)
        clave = ("radio", marcado, fondo, lado)
        if clave not in self._cache:
            def dibujar(hoja, fitz):
                c = lado / 2
                hoja.draw_circle((c, c), c - self.px(1), color=_rgb(ROJO if marcado else APAGADO),
                                 fill=(1, 1, 1), width=self.px(1.5))
                if marcado:
                    hoja.draw_circle((c, c), lado * 0.24, color=None, fill=_rgb(ROJO))
            self._cache[clave] = self._pintar(lado, lado, dibujar, fondo)
        return self._cache[clave]

    def paso(self, numero: int, estado: str, fondo: str = FONDO):
        """Círculo numerado de la barra de pasos: actual (rojo), hecho (oscuro, con visto) o pendiente."""
        lado = self.px(26)
        clave = ("paso", numero, estado, fondo, lado)
        if clave not in self._cache:
            def dibujar(hoja, fitz):
                c = lado / 2
                relleno = {"actual": ROJO, "hecho": TINTA, "pendiente": BLANCO}[estado]
                hoja.draw_circle((c, c), c - 1, color=_rgb(FILETE if estado == "pendiente" else relleno),
                                 fill=_rgb(relleno), width=1)
                tinta = _rgb(SECUNDARIO if estado == "pendiente" else BLANCO)
                if estado == "hecho":
                    s = lado / 16
                    hoja.draw_polyline([(4.5 * s, 8.3 * s), (7 * s, 10.8 * s), (11.5 * s, 5.6 * s)], color=tinta,
                                       width=1.5 * s, lineCap=1, lineJoin=1)
                else:
                    texto = str(numero)
                    cuerpo = lado * 0.5
                    ancho = fitz.get_text_length(texto, "hebo", cuerpo)
                    hoja.insert_text(((lado - ancho) / 2, c + cuerpo * 0.36), texto, fontname="hebo",
                                     fontsize=cuerpo, color=tinta)
            self._cache[clave] = self._pintar(lado, lado, dibujar, fondo)
        return self._cache[clave]

    def icono(self, nombre: str, color: str, fondo: str, lado: float = 16):
        """Iconos lineales sencillos (archivo, carpeta, flechas, aspa, aviso, visto)."""
        lado = self.px(lado)
        clave = ("ico", nombre, color, fondo, lado)
        if clave not in self._cache:
            def dibujar(hoja, fitz):
                s = lado / 16
                c = _rgb(color)
                w = 1.4 * s

                def linea(*pts):
                    hoja.draw_polyline([(x * s, y * s) for x, y in pts], color=c, width=w,
                                       lineCap=1, lineJoin=1)
                if nombre == "archivo":
                    linea((4, 1.5), (9.5, 1.5), (12.5, 4.5), (12.5, 14.5), (4, 14.5), (4, 1.5))
                    linea((9.5, 1.5), (9.5, 4.5), (12.5, 4.5))
                elif nombre == "archivo+":
                    linea((4, 1.5), (9.5, 1.5), (12.5, 4.5), (12.5, 14.5), (4, 14.5), (4, 1.5))
                    linea((8.25, 7), (8.25, 11.5))
                    linea((6, 9.25), (10.5, 9.25))
                elif nombre == "carpeta":
                    linea((1.5, 3.5), (6, 3.5), (7.5, 5), (14.5, 5), (14.5, 13), (1.5, 13), (1.5, 3.5))
                elif nombre == "arriba":
                    linea((8, 13), (8, 3))
                    linea((4, 7), (8, 3), (12, 7))
                elif nombre == "abajo":
                    linea((8, 3), (8, 13))
                    linea((4, 9), (8, 13), (12, 9))
                elif nombre == "aspa":
                    linea((4, 4), (12, 12))
                    linea((12, 4), (4, 12))
                elif nombre == "flecha":
                    linea((3, 8), (13, 8))
                    linea((9, 4), (13, 8), (9, 12))
                elif nombre == "visto":
                    linea((3, 8.5), (6.5, 12), (13, 4.5))
                elif nombre == "aviso":
                    hoja.draw_circle((8 * s, 8 * s), 6 * s, color=c, width=w)
                    linea((8, 4.8), (8, 8.8))
                    hoja.draw_circle((8 * s, 11.2 * s), 0.8 * s, color=None, fill=c)
                elif nombre == "registro":
                    linea((3.5, 1.5), (12.5, 1.5), (12.5, 14.5), (3.5, 14.5), (3.5, 1.5))
                    for y in (5, 8, 11):
                        linea((6, y), (10, y))
                elif nombre == "curso":
                    for i in range(8):
                        import math
                        a = i * math.pi / 4
                        x, y = 8 + 5.2 * math.cos(a), 8 + 5.2 * math.sin(a)
                        hoja.draw_circle((x * s, y * s), 1.05 * s, color=None, fill=c)
            self._cache[clave] = self._pintar(lado, lado, dibujar, fondo)
        return self._cache[clave]


class Tarjeta(tk.Frame):
    """Marco con fondo redondeado y filete. El contenido va en `self.cuerpo`."""

    def __init__(self, padre, pintor: Pintor, fondo_padre: str = FONDO, relleno: str = BLANCO,
                 borde: str | None = FILETE, radio: float = 14, margen: tuple = (28, 26, 28, 26),
                 grosor: float = 1.0):
        super().__init__(padre, background=fondo_padre, borderwidth=0, highlightthickness=0)
        self.pintor, self.fondo_padre, self.relleno = pintor, fondo_padre, relleno
        self.borde, self.radio, self.grosor = borde, radio, grosor
        self._fondo = tk.Label(self, borderwidth=0, background=fondo_padre)
        self._fondo.place(x=0, y=0, relwidth=1, relheight=1)
        izq, arr, der, aba = (pintor.px(m) for m in margen)
        self.cuerpo = tk.Frame(self, background=relleno)
        self.cuerpo.pack(fill="both", expand=True, padx=(izq, der), pady=(arr, aba))
        self._tam = (0, 0)
        self._pendiente = None
        self.bind("<Configure>", self._al_cambiar)
        # una tarjeta oculta (pack_forget) no recibe <Configure> si cambia de ancho mientras
        # tanto: al volver a mostrarse se repinta con su tamaño real
        self.bind("<Map>", lambda e: self._programar())

    def _programar(self):
        if self._pendiente:
            self.after_cancel(self._pendiente)
        self._pendiente = self.after(15, self._redibujar)

    def colores(self, borde: str | None = None, grosor: float | None = None):
        self.borde = borde
        if grosor is not None:
            self.grosor = grosor
        self._redibujar()

    def _al_cambiar(self, evento):
        if (evento.width, evento.height) != self._tam:
            self._programar()

    def _redibujar(self):
        self._pendiente = None
        ancho, alto = self.winfo_width(), self.winfo_height()
        self._tam = (ancho, alto)
        if ancho < 4 or alto < 4:
            return
        p = self.pintor
        imagen = p.caja(ancho, alto, p.px(self.radio), self.relleno, self.fondo_padre,
                        self.borde, p.px(self.grosor) if self.borde else 0)
        self._fondo.configure(image=imagen)
        self._fondo.lower()


class Boton(tk.Canvas):
    """Botón con esquinas redondeadas, icono opcional y respuesta inmediata al pulsar.

    Clases: principal (rojo), secundario (gris claro), oscuro, chip (píldora con filete),
    enlace (solo texto rojo), icono (cuadrado sin texto).
    """

    def __init__(self, padre, pintor: Pintor, texto: str, orden, clase: str = "secundario",
                 icono: str | None = None, fondo: str = BLANCO, fuente=None, ancho: int | None = None,
                 alto: float = 40, icono_derecha: bool = False, alinear: str = "centro"):
        super().__init__(padre, borderwidth=0, highlightthickness=0, background=fondo, cursor="hand2")
        self.alinear = alinear
        self.pintor, self.orden, self.clase, self.fondo = pintor, orden, clase, fondo
        self.texto, self.icono_nombre, self.icono_derecha = texto, icono, icono_derecha
        self.alto, self.ancho_fijo = alto, ancho
        self.fuente = tkfont.Font(font=fuente) if fuente else tkfont.nametofont("TkDefaultFont")
        self.activo = True
        self._estado = "normal"
        self.bind("<Enter>", lambda e: self._pintar("encima"))
        self.bind("<Leave>", lambda e: self._pintar("normal"))
        self.bind("<ButtonPress-1>", lambda e: self._pintar("pulsado"))
        self.bind("<ButtonRelease-1>", self._soltar)
        self._pintar("normal")

    def _colores(self, estado: str):
        c = self.clase
        if not self.activo:
            return {"principal": ("#F4A3AE", BLANCO, None), "enlace": (self.fondo, DESACTIVADO, None),
                    "icono": (self.fondo, DESACTIVADO, None), "fila": (self.fondo, "#A6ACB1", None),
                    "oscuro": (TINTA, "#5D646A", "#2A2F34")}.get(c, ("#F7F8F9", "#A6ACB1", None))
        if c == "nav":
            return {"normal": self.fondo, "encima": "#E9EBED", "pulsado": FILETE}[estado], SECUNDARIO, None
        if c == "nav_activo":
            return BLANCO, TINTA, FILETE
        if c == "fila":
            return {"normal": self.fondo, "encima": CAMPO, "pulsado": FILETE}[estado], TINTA, None
        if c == "principal":
            return {"normal": ROJO, "encima": "#D60F29", "pulsado": ROJO_OSCURO}[estado], BLANCO, None
        if c == "oscuro":
            return {"normal": TINTA, "encima": "#1E2226", "pulsado": "#262B30"}[estado], BLANCO, "#3A4046"
        if c == "chip":
            return {"normal": BLANCO, "encima": CAMPO, "pulsado": FILETE}[estado], TINTA, FILETE
        if c == "enlace":
            return self.fondo, ROJO_OSCURO if estado != "normal" else ROJO, None
        if c == "icono":
            return ({"normal": self.fondo, "encima": CAMPO, "pulsado": FILETE}[estado],
                    SECUNDARIO if estado == "normal" else TINTA, None)
        return {"normal": CAMPO, "encima": "#E9EBED", "pulsado": FILETE}[estado], TINTA, None

    def _medidas(self):
        p = self.pintor
        texto = self.fuente.measure(self.texto) if self.texto else 0
        icono = p.px(16) if self.icono_nombre else 0
        hueco = p.px(8) if (icono and texto) else 0
        lateral = p.px({"chip": 14, "enlace": 0, "icono": 0, "nav": 14, "nav_activo": 14,
                        "fila": 14}.get(self.clase, 18))
        alto = p.px(self.alto)
        if self.ancho_fijo:
            ancho = p.px(self.ancho_fijo)
        elif self.clase == "icono":
            ancho = alto
        else:
            ancho = texto + icono + hueco + 2 * lateral
        return ancho, alto, texto, icono, hueco

    def _pintar(self, estado: str):
        self._estado = estado
        relleno, tinta, borde = self._colores(estado)
        p = self.pintor
        ancho, alto, texto, icono, hueco = self._medidas()
        self.configure(width=ancho, height=alto, background=self.fondo)
        self.delete("all")
        if self.clase != "enlace":
            radio = alto / 2 if self.clase == "chip" else p.px(10 if self.clase != "icono" else 8)
            self._img = p.caja(ancho, alto, radio, relleno, self.fondo, borde, p.px(1) if borde else 0)
            self.create_image(0, 0, image=self._img, anchor="nw")
        x = (ancho - texto - icono - hueco) / 2
        if self.alinear == "izquierda":
            x = p.px(14)
        if self.icono_nombre and not self.icono_derecha:
            self._ico = p.icono(self.icono_nombre, tinta, relleno)
            self.create_image(x, alto / 2, image=self._ico, anchor="w")
            x += icono + hueco
        if self.texto:
            self.create_text(x, alto / 2, text=self.texto, fill=tinta, font=self.fuente, anchor="w")
            x += texto + hueco
        if self.icono_nombre and self.icono_derecha:
            self._ico = p.icono(self.icono_nombre, tinta, relleno)
            self.create_image(x, alto / 2, image=self._ico, anchor="w")

    def _soltar(self, evento):
        dentro = 0 <= evento.x <= self.winfo_width() and 0 <= evento.y <= self.winfo_height()
        self._pintar("encima" if dentro else "normal")
        if dentro and self.activo and self.orden:
            self.orden()

    def activar(self, activo: bool):
        self.activo = activo
        self.configure(cursor="hand2" if activo else "arrow")
        self._pintar("normal")

    def rotular(self, texto: str):
        self.texto = texto
        self._pintar(self._estado)


class Interruptor(tk.Label):
    """Interruptor tipo iOS ligado a un BooleanVar."""

    def __init__(self, padre, pintor: Pintor, variable: tk.BooleanVar, fondo: str = BLANCO):
        super().__init__(padre, borderwidth=0, background=fondo, cursor="hand2")
        self.pintor, self.variable, self.fondo = pintor, variable, fondo
        self.activo = True
        self.bind("<Button-1>", lambda e: self.activo and self.variable.set(not self.variable.get()))
        variable.trace_add("write", lambda *_: self._pintar())
        self._pintar()

    def _pintar(self):
        try:
            self.configure(image=self.pintor.interruptor(bool(self.variable.get()), self.fondo, self.activo))
        except tk.TclError:
            pass

    def activar(self, activo: bool):
        self.activo = activo
        self.configure(cursor="hand2" if activo else "arrow")
        self._pintar()


def desplazable(padre, fondo: str = FONDO):
    """Marco con desplazamiento vertical (rueda del ratón) y barra discreta."""
    from tkinter import ttk

    lienzo = tk.Canvas(padre, background=fondo, highlightthickness=0, borderwidth=0)
    barra = ttk.Scrollbar(padre, orient="vertical", command=lienzo.yview, style="Fina.Vertical.TScrollbar")
    interior = tk.Frame(lienzo, background=fondo)
    interior.bind("<Configure>", lambda e: lienzo.configure(scrollregion=lienzo.bbox("all")))
    hueco = lienzo.create_window((0, 0), window=interior, anchor="nw")
    lienzo.bind("<Configure>", lambda e: lienzo.itemconfigure(hueco, width=e.width))
    lienzo.configure(yscrollcommand=barra.set)
    barra.pack(side="right", fill="y")
    lienzo.pack(side="left", fill="both", expand=True)

    def rueda(evento):
        if lienzo.bbox("all") and lienzo.bbox("all")[3] > lienzo.winfo_height():
            lienzo.yview_scroll(int(-evento.delta / 120) or (-1 if evento.delta > 0 else 1), "units")

    lienzo.bind("<Enter>", lambda e: lienzo.bind_all("<MouseWheel>", rueda))
    lienzo.bind("<Leave>", lambda e: lienzo.unbind_all("<MouseWheel>"))
    interior.lienzo = lienzo
    return interior
