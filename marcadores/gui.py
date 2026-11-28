"""Ventana principal para Windows (diseño «Táctica Legal»: tarjetas blancas, rojo de marca)."""
from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import __version__, acceso, actualizar, config, instancia, licencia, registro
from .entrada import EXT_VALIDAS, _orden_natural, recoger
from .estilo import (AVISO_FONDO, AVISO_TEXTO, BIEN_FONDO, BIEN_TEXTO, BLANCO, CAMPO, FILETE, FONDO,
                     ROJO, SECUNDARIO, TINTA, TINTA_2, Boton, Interruptor, Pintor, Tarjeta,
                     desplazable, familia, familia_titulos)
from .nombres import EJEMPLO, HUECOS_DOCUMENTO, HUECOS_GENERALES, POR_DEFECTO, Plantillas
from .proceso import Cancelado, carpeta_de, crear_analizador, procesar_varios, resumen_fallos

try:  # arrastrar y soltar (opcional)
    from tkinterdnd2 import DND_FILES, TkinterDnD

    Base = TkinterDnD.Tk
except Exception:  # pragma: no cover
    DND_FILES = None
    Base = tk.Tk

# Botones «+ …» de la tarjeta de palabras clave: dejan la etiqueta escrita para completarla.
SUGERENCIAS_PALABRAS = (
    ("Cliente", "Cliente: "),
    ("Fecha", "Fecha clave: "),
    ("Situación", "Situación: "),
    ("Contraparte", "Contraparte: "),
    ("Bien o finca", "Bien: "),
)
AUTOR = "Eduardo Goig Alique · Táctica Legal"
CODIGO = "github.com/setokenz/marcadores-pdf-publico"
FASES = ("Lectura", "Clasificación", "Índice", "Exportación")
PASOS = ("Documentos", "Trabajo", "Resultados")  # el asistente de la pestaña Procesar


def _nitidez():
    """Texto nítido en pantallas de alta densidad (Windows 10 y 11)."""
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass


def ruta_icono() -> Path:
    import sys

    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base / "assets" / "icono.ico"


def poner_icono(ventana) -> None:
    """Icono de la ventana y de la barra de tareas; también lo heredan los diálogos."""
    try:
        ventana.iconbitmap(default=str(ruta_icono()))
    except Exception:
        pass  # sin icono se usa el predeterminado


def aplicar_estilo(raiz) -> dict:
    """Estilos ttk de campos y desplegables; devuelve las fuentes de la ventana."""
    fam, fam_t = familia(raiz), familia_titulos(raiz)
    fuentes = {
        "base": (fam, 10), "peq": (fam, 9), "mini": (fam, 8), "caps": (fam, 7, "bold"),
        "fuerte": (fam, 10, "bold"), "num": (fam, 11, "bold"), "tarjeta": (fam_t, 15, "bold"),
        "app": (fam_t, 20, "bold"), "panel": (fam_t, 19, "bold"), "pct": (fam_t, 30, "bold"),
        "mono": ("Consolas", 9), "pestana": (fam, 10), "pestana_activa": (fam, 10, "bold"),
    }
    raiz.configure(background=FONDO)
    raiz.option_add("*Font", fuentes["base"])
    raiz.option_add("*TCombobox*Listbox.background", BLANCO)
    raiz.option_add("*TCombobox*Listbox.selectBackground", ROJO)
    raiz.option_add("*TCombobox*Listbox.selectForeground", BLANCO)
    raiz.option_add("*TCombobox*Listbox.font", fuentes["base"])
    e = ttk.Style(raiz)
    e.theme_use("clam")
    e.configure(".", background=BLANCO, foreground=TINTA, font=fuentes["base"], bordercolor=FILETE,
                lightcolor=BLANCO, darkcolor=BLANCO, focuscolor=ROJO, troughcolor=FILETE)
    e.configure("TFrame", background=BLANCO)
    e.configure("TLabel", background=BLANCO, foreground=TINTA)
    e.configure("Nota.TLabel", foreground=SECUNDARIO, font=fuentes["peq"])
    for clase in ("TEntry", "TCombobox"):
        e.configure(clase, fieldbackground=CAMPO, background=CAMPO, foreground=TINTA,
                    bordercolor=CAMPO, lightcolor=CAMPO, darkcolor=CAMPO, padding=(10, 7),
                    insertcolor=TINTA, arrowcolor=SECUNDARIO, arrowsize=14)
        e.map(clase, bordercolor=[("focus", ROJO)], lightcolor=[("focus", CAMPO)],
              fieldbackground=[("readonly", CAMPO), ("disabled", "#F7F8F9")],
              foreground=[("disabled", "#A6ACB1")])
    e.configure("TCheckbutton", background=BLANCO, foreground=TINTA, indicatorcolor=BLANCO,
                indicatorforeground=BLANCO, upperbordercolor="#C9CCCF", lowerbordercolor="#C9CCCF")
    e.map("TCheckbutton", background=[("active", BLANCO)], indicatorcolor=[("selected", ROJO)],
          upperbordercolor=[("selected", ROJO)], lowerbordercolor=[("selected", ROJO)])
    e.configure("TButton", background=CAMPO, foreground=TINTA, bordercolor=CAMPO, lightcolor=CAMPO,
                darkcolor=CAMPO, padding=(14, 7))
    e.map("TButton", background=[("active", FILETE)], lightcolor=[("active", FILETE)],
          darkcolor=[("active", FILETE)], bordercolor=[("active", FILETE)])
    e.configure("Principal.TButton", background=ROJO, foreground=BLANCO, bordercolor=ROJO,
                lightcolor=ROJO, darkcolor=ROJO, font=fuentes["fuerte"])
    e.map("Principal.TButton", background=[("active", "#B80E24")], lightcolor=[("active", "#B80E24")],
          darkcolor=[("active", "#B80E24")], bordercolor=[("active", "#B80E24")])
    # barra de desplazamiento fina, sin flechas
    e.layout("Fina.Vertical.TScrollbar", [("Vertical.Scrollbar.trough", {"sticky": "ns", "children": [
        ("Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})]})])
    e.configure("Fina.Vertical.TScrollbar", troughcolor=FONDO, background="#D5D8DB", bordercolor=FONDO,
                lightcolor="#D5D8DB", darkcolor="#D5D8DB", gripcount=0, arrowsize=8)
    e.map("Fina.Vertical.TScrollbar", background=[("active", "#B9BDC1")])
    return fuentes


def _envolver(rotulo: tk.Label, maximo: int = 0) -> None:
    """El texto se parte en líneas al ancho que tenga en cada momento (ventanas estrechas)."""
    def ajustar(evento):
        ancho = evento.width if not maximo else min(maximo, evento.width)
        if ancho > 40 and abs(int(rotulo.cget("wraplength") or 0) - ancho) > 4:
            rotulo.configure(wraplength=ancho)
    rotulo.bind("<Configure>", ajustar)


class ListaExpedientes:
    """Tabla de expedientes con N.º, nombre y botones subir, bajar y quitar en cada fila.

    Imita lo que se usaba de un Listbox (get, insert, delete, size) para no tocar el resto.
    """

    def __init__(self, ventana: "Ventana", padre: tk.Frame, al_cambiar):
        self.v, self.rutas, self.al_cambiar = ventana, [], al_cambiar
        self.filas = tk.Frame(padre, background=BLANCO)
        self.filas.pack(fill="x")
        self._pintar()

    # interfaz de Listbox
    def get(self, a, b=None):
        return tuple(self.rutas) if b is not None else self.rutas[a]

    def insert(self, pos, valor):
        self.rutas.insert(len(self.rutas) if pos == "end" else int(pos), valor)
        self._pintar()

    def delete(self, a, b=None):
        if b is not None:
            del self.rutas[int(a):]
        else:
            del self.rutas[int(a)]
        self._pintar()

    def size(self) -> int:
        return len(self.rutas)

    def mover(self, i: int, paso: int):
        j = i + paso
        if 0 <= j < len(self.rutas):
            self.rutas[i], self.rutas[j] = self.rutas[j], self.rutas[i]
            self._pintar()

    def _pintar(self):
        v, p = self.v, self.v.pintor
        for w in self.filas.winfo_children():
            w.destroy()
        if not self.rutas:
            vacio = tk.Label(self.filas, background=BLANCO, foreground=SECUNDARIO, font=v.F["base"],
                             text="Arrastre aquí PDF, imágenes o carpetas, o use «Añadir archivos».",
                             pady=p.px(26))
            vacio.pack(fill="x")
            v._admitir_soltar(vacio)
        for n, ruta in enumerate(self.rutas):
            if n:
                tk.Frame(self.filas, background=FILETE, height=1).pack(fill="x")
            fila = tk.Frame(self.filas, background=BLANCO)
            fila.pack(fill="x")
            fila.columnconfigure(2, weight=1)
            tk.Label(fila, text=f"{n + 1:02d}", background=BLANCO, foreground=SECUNDARIO,
                     font=v.F["peq"], width=4, anchor="w").grid(row=0, column=0, rowspan=2,
                                                                 padx=(p.px(16), 0), pady=p.px(10))
            camino = Path(ruta)
            partes = ruta.replace("\\", "/").rstrip("/").rsplit("/", 1)
            nombre, carpeta = partes[-1], (partes[0] if len(partes) == 2 else "")
            tk.Label(fila, image=p.icono("carpeta" if camino.is_dir() else "archivo", ROJO, BLANCO),
                     background=BLANCO).grid(row=0, column=1, rowspan=2, padx=(0, p.px(12)))
            tk.Label(fila, text=nombre or ruta, background=BLANCO, foreground=TINTA,
                     font=v.F["base"], anchor="w").grid(row=0, column=2, sticky="sw", pady=(p.px(9), 0))
            sub = "Carpeta" if camino.is_dir() else carpeta.replace("/", "\\")
            tk.Label(fila, text=sub, background=BLANCO, foreground=SECUNDARIO, font=v.F["mini"],
                     anchor="w").grid(row=1, column=2, sticky="nw", pady=(0, p.px(9)))
            for col, (icono, orden, activo) in enumerate((
                    ("arriba", lambda i=n: self.mover(i, -1), n > 0),
                    ("abajo", lambda i=n: self.mover(i, 1), n < len(self.rutas) - 1),
                    ("aspa", lambda i=n: self.delete(i), True))):
                b = Boton(fila, p, "", orden, clase="icono", icono=icono, alto=32)
                b.grid(row=0, column=3 + col, rowspan=2, padx=(0, p.px(4) if col < 2 else p.px(12)))
                if not activo:
                    b.activar(False)
            for w in (fila, *fila.winfo_children()):
                v._admitir_soltar(w)
        self.al_cambiar()


class Ventana(Base):
    def __init__(self):
        _nitidez()
        super().__init__()
        self.title(f"Marcadores PDF {__version__}")
        alto = max(640, min(920, self.winfo_screenheight() - 80))
        ancho = max(1040, min(1280, self.winfo_screenwidth() - 60))
        self.geometry(f"{ancho}x{alto}")
        self.minsize(1000, 620)
        self.F = aplicar_estilo(self)
        self.pintor = Pintor(self)
        poner_icono(self)
        self.cfg = config.cargar()
        self.cola: queue.Queue = queue.Queue()
        self.cancelar = threading.Event()
        self.hilo: threading.Thread | None = None
        self.ultima_salida: Path | None = None
        self.licencia = licencia.estado(self.cfg.licencia_testigo)
        self._montar()
        self.protocol("WM_DELETE_WINDOW", self._cerrar)
        self.after(150, self._atender_cola)
        actualizar.limpiar_restos()
        threading.Thread(target=acceso.mantener, args=(self.cfg, config.guardar), daemon=True).start()
        threading.Thread(target=lambda: self.cola.put(("licencia", licencia.renovar(self.cfg, config.guardar), 0.0)),
                         daemon=True).start()
        self._reintento_actualizacion = False
        if self.cfg.buscar_actualizaciones and actualizar.es_ejecutable():
            self.after(1500, lambda: self._buscar_actualizacion(avisar_siempre=False))
        if not self.cfg.ocultar_presentacion and not (self.cfg.mistral_clave and self.cfg.claude_clave
                                                      and self.cfg.licencia_clave):
            self.after(400, self._presentacion)

    # --- piezas comunes ------------------------------------------------------
    def px(self, valor: float) -> int:
        return self.pintor.px(valor)

    def _tarjeta(self, padre, titulo: str = "", numero: int | None = None, subtitulo: str = "",
                 extra: str = "", fondo: str = FONDO):
        """Tarjeta blanca con cabecera «01  Título» y subtítulo. Devuelve (cuerpo, cabecera)."""
        t = Tarjeta(padre, self.pintor, fondo_padre=fondo)
        t.pack(fill="x", pady=(0, self.px(16)))
        c = t.cuerpo
        cab = tk.Frame(c, background=BLANCO)
        if titulo:
            cab.pack(fill="x", pady=(0, self.px(18)))
            cab.columnconfigure(1, weight=1)
            if numero is not None:
                cab.columnconfigure(0, minsize=self.px(36))
                tk.Label(cab, text=f"{numero:02d}", background=BLANCO, foreground=ROJO,
                         font=self.F["num"]).grid(row=0, column=0, sticky="w")
            linea = tk.Frame(cab, background=BLANCO)
            linea.grid(row=0, column=1, sticky="w")
            tk.Label(linea, text=titulo, background=BLANCO, foreground=TINTA,
                     font=self.F["tarjeta"]).pack(side="left")
            if extra:
                tk.Label(linea, text=" · " + extra, background=BLANCO, foreground=SECUNDARIO,
                         font=self.F["peq"]).pack(side="left", pady=(self.px(5), 0))
            if subtitulo:
                sub = tk.Label(cab, text=subtitulo, background=BLANCO, foreground=SECUNDARIO,
                               font=self.F["base"], justify="left", anchor="w")
                sub.grid(row=1, column=1, sticky="we", pady=(self.px(2), 0))
                _envolver(sub)
            cab.acciones = tk.Frame(cab, background=BLANCO)
            cab.acciones.grid(row=0, column=2, rowspan=2, sticky="ne")
        t.cuerpo.cabecera = cab
        t.cuerpo.marco = t
        return c

    def _rotulo(self, padre, texto: str, fondo: str = BLANCO):
        return tk.Label(padre, text=texto, background=fondo, foreground=TINTA, font=self.F["peq"],
                        anchor="w")

    def _interruptor(self, padre, variable, titulo: str, descripcion: str = "", fondo: str = BLANCO):
        """Interruptor con título y explicación debajo (como en Ajustes de macOS/iOS)."""
        f = tk.Frame(padre, background=fondo)
        i = Interruptor(f, self.pintor, variable, fondo)
        i.grid(row=0, column=0, rowspan=2, sticky="n", padx=(0, self.px(12)), pady=(self.px(1), 0))
        t = tk.Label(f, text=titulo, background=fondo, foreground=TINTA, font=self.F["base"], anchor="w",
                     cursor="hand2")
        t.grid(row=0, column=1, sticky="w")
        t.bind("<Button-1>", lambda e: i.activo and variable.set(not variable.get()))
        if descripcion:
            tk.Label(f, text=descripcion, background=fondo, foreground=SECUNDARIO, font=self.F["mini"],
                     anchor="w", justify="left", wraplength=self.px(300)).grid(row=1, column=1, sticky="w")
        f.interruptor = i
        return f

    def _aviso(self, padre, texto: str, clase: str = "aviso", envolver: int = 640):
        fondo, tinta = (AVISO_FONDO, AVISO_TEXTO) if clase == "aviso" else (BIEN_FONDO, BIEN_TEXTO)
        f = tk.Frame(padre, background=fondo)
        tk.Label(f, image=self.pintor.icono("aviso" if clase == "aviso" else "visto", tinta, fondo, 14),
                 background=fondo).pack(side="left", anchor="n", padx=(self.px(10), self.px(6)),
                                        pady=self.px(7))
        rotulo = tk.Label(f, text=texto, background=fondo, foreground=tinta, font=self.F["peq"],
                          justify="left", anchor="w", wraplength=self.px(envolver))
        rotulo.pack(side="left", fill="x", padx=(0, self.px(10)), pady=self.px(6))
        f.rotulo = rotulo
        return f

    def _admitir_soltar(self, widget):
        if DND_FILES:
            try:
                widget.drop_target_register(DND_FILES)
                widget.dnd_bind("<<Drop>>", self._soltar)
            except Exception:
                pass

    # --- estructura ----------------------------------------------------------
    def _montar(self):
        # cabecera blanca con franja roja, marca y pestañas
        cab = tk.Frame(self, background=BLANCO)
        cab.pack(fill="x")
        tk.Frame(cab, background=ROJO, height=self.px(3)).pack(fill="x")
        fila = tk.Frame(cab, background=BLANCO)
        fila.pack(fill="x", padx=self.px(40), pady=(self.px(20), 0))
        tk.Label(fila, text="Marcadores PDF", background=BLANCO, foreground=TINTA,
                 font=self.F["app"]).pack(side="left")
        tk.Label(fila, text=f"v{__version__}", background=BLANCO, foreground=SECUNDARIO,
                 font=self.F["mini"]).pack(side="left", anchor="s", padx=(self.px(10), 0), pady=(0, self.px(6)))
        marca = tk.Frame(fila, background=BLANCO)
        marca.pack(side="right")
        tk.Label(marca, text="T Á C T I C A   L E G A L", background=BLANCO, foreground=ROJO,
                 font=(self.F["caps"][0], 8, "bold")).pack(anchor="e")
        tk.Label(marca, text="N E G O C I O S   E N   L I B E R T A D", background=BLANCO,
                 foreground=SECUNDARIO, font=self.F["caps"][:2]).pack(anchor="e")
        barra = tk.Frame(cab, background=BLANCO)
        barra.pack(fill="x", padx=self.px(40), pady=(self.px(14), 0))
        self.pestanas = {}
        for clave, texto in (("procesar", "Procesar"), ("config", "Configuración"), ("registro", "Registro")):
            p = tk.Frame(barra, background=BLANCO, cursor="hand2")
            p.pack(side="left", padx=(0, self.px(28)))
            r = tk.Label(p, text=texto, background=BLANCO, foreground=SECUNDARIO, font=self.F["pestana"],
                         cursor="hand2")
            r.pack(pady=(0, self.px(10)))
            raya = tk.Frame(p, background=BLANCO, height=self.px(2))
            raya.pack(fill="x")
            for w in (p, r):
                w.bind("<Button-1>", lambda e, c=clave: self._ir(c))
            self.pestanas[clave] = (r, raya)
        tk.Frame(self, background=FILETE, height=1).pack(fill="x")

        # pie: estado y actualizaciones (siempre visible)
        tk.Frame(self, background=FILETE, height=1).pack(side="bottom", fill="x")
        pie = tk.Frame(self, background=BLANCO)
        pie.pack(side="bottom", fill="x")
        tk.Frame(self, background=FILETE, height=1).pack(side="bottom", fill="x")
        self.pie_icono = tk.Label(pie, background=BLANCO)
        self.pie_icono.pack(side="left", padx=(self.px(40), self.px(8)), pady=self.px(12))
        self.pie_texto = tk.Label(pie, text="", background=BLANCO, foreground=SECUNDARIO, font=self.F["peq"],
                                  anchor="w")
        self.pie_texto.pack(side="left", fill="x", expand=True)
        self.b_actualizar = Boton(pie, self.pintor, "Buscar actualizaciones",
                                  lambda: self._buscar_actualizacion(avisar_siempre=True), clase="enlace",
                                  fondo=BLANCO, fuente=self.F["peq"], alto=24)
        self.b_actualizar.pack(side="right", padx=self.px(40))

        self.cuerpo = tk.Frame(self, background=FONDO)
        self.cuerpo.pack(fill="both", expand=True)
        self.paginas = {c: tk.Frame(self.cuerpo, background=FONDO) for c in self.pestanas}
        self.p_procesar, self.p_config = self.paginas["procesar"], self.paginas["config"]

        self._variables()
        self._montar_procesar(self.paginas["procesar"])
        self._montar_registro(self.paginas["registro"])
        self._configurar(self.paginas["config"])
        self._ir("procesar")
        self._estado_pie("Listo.", None)

    def _variables(self):
        from .clasificar import AMBITOS

        c = self.cfg
        self.modo = tk.StringVar(value=c.modo)
        self.capa = tk.BooleanVar(value=c.capa_texto)
        self.separar = tk.BooleanVar(value=c.separar)
        self.unir = tk.BooleanVar(value=c.unir_todo)
        self.indice = tk.BooleanVar(value=c.indice_en_pdf)
        self.enderezar = tk.BooleanVar(value=c.enderezar)
        self.blancas = tk.BooleanVar(value=c.quitar_blancas)
        self.rag = tk.BooleanVar(value=c.exportar_rag)
        self.nav = tk.BooleanVar(value=c.navegacion)
        self.contenido = {k: tk.BooleanVar(value=getattr(c, f"indice_{k}"))
                          for k in ("cronologia", "grupos", "agenda", "remisiones", "palabras")}
        self.ambito = tk.StringVar(value=AMBITOS.get(c.tipo_expediente, AMBITOS["auto"]))
        self.salida = tk.StringVar(value=c.carpeta_salida or str(config.carpeta_por_defecto()))

    def _ir(self, clave: str):
        for c, (rotulo, raya) in self.pestanas.items():
            activa = c == clave
            rotulo.configure(foreground=TINTA if activa else SECUNDARIO,
                             font=self.F["pestana_activa" if activa else "pestana"])
            raya.configure(background=ROJO if activa else BLANCO)
            self.paginas[c].pack_forget()
        self.paginas[clave].pack(fill="both", expand=True)

    def _estado_pie(self, texto: str, clase: str | None = "bien"):
        linea = texto.strip().splitlines()[0] if texto.strip() else ""
        self.pie_texto.configure(text=linea[:160])
        if clase is None:
            self.pie_icono.configure(image="", width=0)
        else:
            color = {"bien": "#2F7A45", "error": ROJO, "aviso": AVISO_TEXTO}[clase]
            self.pie_icono.configure(image=self.pintor.icono("visto" if clase == "bien" else "aviso",
                                                             color, BLANCO, 14))

    # --- Procesar ------------------------------------------------------------
    def _montar_procesar(self, pagina):
        p = self.px
        self.v_formulario = tk.Frame(pagina, background=FONDO)
        self.v_formulario.pack(fill="both", expand=True)
        lateral = tk.Frame(self.v_formulario, background=FONDO, width=p(300))
        lateral.pack(side="right", fill="y", padx=(0, p(40)), pady=(p(32), 0))
        izquierda = tk.Frame(self.v_formulario, background=FONDO)
        izquierda.pack(side="left", fill="both", expand=True, padx=(p(40), p(24)), pady=(p(24), 0))
        # asistente en tres pasos: una sola cosa a la vez, con «Siguiente» como única acción
        self.paso = 1
        self.pasos_barra = tk.Frame(izquierda, background=FONDO)
        self.pasos_barra.pack(fill="x", pady=(0, p(16)))
        self.pasos_rotulos = []
        for n, texto in enumerate(PASOS, start=1):
            if n > 1:
                tk.Frame(self.pasos_barra, background=FILETE, height=1, width=p(36)).pack(side="left", padx=p(10))
            marca = tk.Label(self.pasos_barra, background=FONDO, cursor="hand2")
            marca.pack(side="left")
            rotulo = tk.Label(self.pasos_barra, text=texto, background=FONDO, cursor="hand2", font=self.F["base"])
            rotulo.pack(side="left", padx=(p(8), 0))
            for w in (marca, rotulo):
                w.bind("<Button-1>", lambda e, k=n: self._ir_a_paso(k))
            self.pasos_rotulos.append((marca, rotulo))
        col = desplazable(izquierda)
        self.pasos_tarjetas = {1: [], 2: [], 3: []}

        # paso 1 · Documentos
        c = self._tarjeta(col, "Documentos del expediente", None,
                          "Añada los PDF, imágenes o carpetas. Se procesan en el orden de la lista.")
        self.pasos_tarjetas[1].append(c.marco)
        acciones = c.cabecera.acciones
        for texto, icono, orden in (("Añadir archivos", "archivo+", self._anadir_archivos),
                                    ("Añadir carpeta", "carpeta", self._anadir_carpeta),
                                    ("A–Z", None, self._ordenar)):
            Boton(acciones, self.pintor, texto, orden, icono=icono, fuente=self.F["base"]).pack(
                side="left", padx=(p(8), 0))
        tabla = Tarjeta(c, self.pintor, fondo_padre=BLANCO, radio=10, margen=(2, 7, 2, 7))
        tabla.pack(fill="x")
        t = tabla.cuerpo
        enc = tk.Frame(t, background=BLANCO)
        enc.pack(fill="x", padx=p(1), pady=(p(5), p(10)))
        for texto, lado, pad in (("N.º", "left", (p(16), p(34))), ("ARCHIVO", "left", (0, 0)),
                                 ("ORDEN", "right", (0, p(16)))):
            tk.Label(enc, text=texto, background=BLANCO, foreground=SECUNDARIO,
                     font=self.F["caps"]).pack(side=lado, padx=pad)
        tk.Frame(t, background=FILETE, height=1).pack(fill="x", padx=p(1))
        cuerpo_tabla = tk.Frame(t, background=BLANCO)
        cuerpo_tabla.pack(fill="x", padx=p(1))
        self.lista = ListaExpedientes(self, cuerpo_tabla, self._resumir)
        tk.Frame(t, background=FILETE, height=1).pack(fill="x", padx=p(1))
        pie_tabla = tk.Frame(t, background=BLANCO)
        pie_tabla.pack(fill="x", padx=p(1), pady=(p(8), p(3)))
        tk.Label(pie_tabla, text="También puede arrastrar PDF o carpetas sobre la lista.", background=BLANCO,
                 foreground=SECUNDARIO, font=self.F["mini"]).pack(side="left", padx=(p(16), 0))
        Boton(pie_tabla, self.pintor, "Quitar todos", self._quitar, clase="enlace", fuente=self.F["mini"],
              alto=22).pack(side="right", padx=(0, p(16)))
        for w in (c, tabla, t, enc, cuerpo_tabla, pie_tabla):
            self._admitir_soltar(w)

        # paso 3 · Dónde guardar
        c = self._tarjeta(col, "Dónde guardar los resultados", None,
                          "Se crea una subcarpeta por expediente. El archivo original nunca se modifica.")
        self.pasos_tarjetas[3].append(c.marco)
        self._rotulo(c, "Ruta de destino").pack(anchor="w", pady=(0, p(6)))
        f = tk.Frame(c, background=BLANCO)
        f.pack(fill="x")
        ttk.Entry(f, textvariable=self.salida).pack(side="left", fill="x", expand=True, ipady=p(3))
        Boton(f, self.pintor, "Elegir…", self._elegir_salida).pack(side="left", padx=(p(8), 0))
        Boton(f, self.pintor, "Nombres…", self._nombres).pack(side="left", padx=(p(8), 0))

        # 03 · Lectura y clasificación
        c = self._tarjeta(col, "Qué trabajo quiere", None,
                          "Lo habitual ya viene marcado: si no sabe qué elegir, pulse «Siguiente».")
        self.pasos_tarjetas[2].append(c.marco)
        Boton(c.cabecera.acciones, self.pintor, "Ajustes avanzados", lambda: self._ir("config"),
              clase="enlace", icono="flecha", icono_derecha=True, fuente=self.F["peq"], alto=24).pack()
        opciones = tk.Frame(c, background=BLANCO)
        opciones.pack(fill="x")
        opciones.columnconfigure((0, 1), weight=1, uniform="op")
        self.opciones_modo = {}
        for n, (valor, titulo) in enumerate((("gemini", "Con IA"), ("basico", "Básico, sin IA"))):
            t = Tarjeta(opciones, self.pintor, fondo_padre=BLANCO, radio=12, margen=(18, 16, 18, 16))
            t.grid(row=0, column=n, sticky="nsew", padx=(0, p(8)) if n == 0 else (p(8), 0))
            fila = tk.Frame(t.cuerpo, background=BLANCO)
            fila.pack(fill="x")
            marca = tk.Label(fila, background=BLANCO)
            marca.pack(side="left", padx=(0, p(10)))
            tk.Label(fila, text=titulo, background=BLANCO, foreground=TINTA,
                     font=self.F["fuerte"]).pack(side="left")
            desc = tk.Label(t.cuerpo, background=BLANCO, foreground=SECUNDARIO, font=self.F["peq"],
                            justify="left", anchor="w", wraplength=p(300))
            desc.pack(fill="x", padx=(p(28), 0), pady=(p(6), p(8)))
            if valor == "basico":
                desc.configure(text="Solo PDF que ya tienen texto. Nada sale del equipo.")
                chip = self._aviso(t.cuerpo, "Procesamiento local", "bien", 260)
            else:
                chip = self._aviso(t.cuerpo, "", "aviso", 260)
            chip.pack(anchor="w", padx=(p(28), 0))
            self.opciones_modo[valor] = (t, marca, desc, chip)
            for w in (t, t.cuerpo, fila, marca, desc, *fila.winfo_children(), chip, *chip.winfo_children()):
                w.bind("<Button-1>", lambda e, x=valor: self.modo.set(x))
                w.configure(cursor="hand2")
        self.modo.trace_add("write", lambda *_: (self._pintar_modo(), self._resumir()))
        self._rotulo_gemini()

        f = tk.Frame(c, background=BLANCO)
        f.pack(fill="x", pady=(p(20), 0))
        self._rotulo(f, "Tipo de expediente").grid(row=0, column=0, sticky="w", pady=(0, p(6)))
        from .clasificar import AMBITOS

        ttk.Combobox(f, textvariable=self.ambito, values=list(AMBITOS.values()), state="readonly",
                     width=28).grid(row=1, column=0, sticky="w", ipady=p(3))
        self._interruptor(f, self.unir, "Unir todo en un único PDF").grid(row=1, column=1, sticky="w",
                                                                         padx=(p(24), 0))
        self.ambito.trace_add("write", lambda *_: self._resumir())
        self.unir.trace_add("write", lambda *_: self._resumir())

        # 04 · Palabras clave
        c = self._tarjeta(col, "Búsquedas adicionales", None,
                          "Nombres, fechas o temas que quiera localizar: se resaltan en amarillo y tienen "
                          "apartado propio en el índice.", extra="opcional")
        self.pasos_tarjetas[2].append(c.marco)
        f = tk.Frame(c, background=BLANCO)
        f.pack(fill="x", pady=(0, p(16)))
        tk.Label(f, text="Añadir tema:", background=BLANCO, foreground=SECUNDARIO,
                 font=self.F["peq"]).pack(side="left", padx=(0, p(6)))
        for texto, linea in SUGERENCIAS_PALABRAS:
            Boton(f, self.pintor, f"+ {texto}", lambda l=linea: self._sugerir_palabra(l), clase="chip",
                  fuente=self.F["peq"], alto=34).pack(side="left", padx=(p(4), 0))
        Boton(f, self.pintor, "Vaciar", lambda: (self.palabras.delete("1.0", "end"), self._resumir()),
              clase="enlace", fuente=self.F["peq"], alto=24).pack(side="right")
        self._rotulo(c, "Temas (uno por línea)").pack(anchor="w", pady=(0, p(6)))
        self.palabras = tk.Text(c, height=4, wrap="word", padx=p(12), pady=p(10), relief="flat",
                                borderwidth=0, highlightthickness=1, highlightbackground=CAMPO,
                                highlightcolor=ROJO, background=CAMPO, foreground=TINTA,
                                insertbackground=TINTA, selectbackground=ROJO, selectforeground=BLANCO,
                                font=self.F["base"])
        self.palabras.pack(fill="x")
        self.palabras.insert("1.0", self.cfg.palabras_clave)
        self.palabras.bind("<KeyRelease>", lambda e: self._resumir())
        f = tk.Frame(c, background=BLANCO)
        f.pack(fill="x", pady=(p(10), 0))
        tk.Label(f, text="Formato:", background=BLANCO, foreground=SECUNDARIO,
                 font=self.F["mini"]).pack(side="left")
        tk.Label(f, text="Contraparte: Nombre S.L., su administrador", background=CAMPO, foreground=SECUNDARIO,
                 font=self.F["mono"], padx=p(6)).pack(side="left", padx=(p(6), 0))
        # paso 3 · Qué va a obtener: para qué sirve cada archivo
        c = self._tarjeta(col, "Qué va a obtener", None, "En la carpeta de cada expediente encontrará:")
        self.pasos_tarjetas[3].append(c.marco)
        for n, (titulo, archivo, uso, variable) in enumerate((
                ("PDF con marcadores", "«Expediente - marcadores.pdf»",
                 "El expediente completo, para trabajar: índice al inicio, marcadores a la izquierda y "
                 "enlaces entre documentos. Es el que abrirá a diario.", None),
                ("Índice en Excel", "«Expediente - índice.xlsx»",
                 "La lista de documentos, la cronología, los señalamientos, los plazos y las remisiones, "
                 "en hojas que puede filtrar y ordenar.", None),
                ("Archivos para su asistente de IA", "carpeta «RAG» y «Expediente - rag.jsonl»",
                 "El texto del expediente preparado para una IA. Suba la carpeta «RAG» a su asistente "
                 "(un proyecto de Claude, NotebookLM, AnythingLLM…) y pregúntele por el asunto: cada "
                 "respuesta citará el documento y la página. El .jsonl es lo mismo en un solo archivo, "
                 "para sistemas automáticos.", self.rag),
                ("Un PDF por documento", "carpeta «documentos»",
                 "Cada resolución o escrito en su propio PDF, para adjuntar uno suelto a un escrito o "
                 "enviarlo por correo.", self.separar))):
            if n:
                tk.Frame(c, background=FILETE, height=1).pack(fill="x", pady=p(12))
            fila = tk.Frame(c, background=BLANCO)
            fila.pack(fill="x")
            fila.columnconfigure(0, weight=1)
            cab = tk.Frame(fila, background=BLANCO)
            cab.grid(row=0, column=0, sticky="w")
            tk.Label(cab, text=titulo, background=BLANCO, foreground=TINTA, font=self.F["fuerte"]).pack(side="left")
            tk.Label(cab, text=archivo, background=BLANCO, foreground=SECUNDARIO,
                     font=self.F["mini"]).pack(side="left", padx=(p(8), 0))
            texto = tk.Label(fila, text=uso, background=BLANCO, foreground=SECUNDARIO, font=self.F["peq"],
                             justify="left", anchor="w")
            texto.grid(row=1, column=0, sticky="we", pady=(p(3), 0))
            _envolver(texto)
            if variable is None:
                tk.Label(fila, text="Siempre", background=BLANCO, foreground=SECUNDARIO,
                         font=self.F["mini"]).grid(row=0, column=1, rowspan=2, padx=(p(16), 0))
            else:
                Interruptor(fila, self.pintor, variable).grid(row=0, column=1, rowspan=2, padx=(p(16), 0))
        self.pie_columna = tk.Frame(col, background=FONDO, height=p(16))
        self.pie_columna.pack()

        # columna lateral: resumen oscuro y acciones
        r = Tarjeta(lateral, self.pintor, fondo_padre=FONDO, relleno=TINTA, borde=None, radio=14,
                    margen=(28, 26, 28, 28))
        r.pack(fill="x")
        tk.Label(r.cuerpo, text="RESUMEN", background=TINTA, foreground="#9AA0A6",
                 font=self.F["caps"]).pack(anchor="w", pady=(0, p(14)))
        self.resumen = {}
        for n, clave in enumerate(("Expedientes", "Lectura", "Tipo", "Salida", "Temas de búsqueda", "Licencia")):
            if n:
                tk.Frame(r.cuerpo, background=TINTA_2, height=1).pack(fill="x")
            f = tk.Frame(r.cuerpo, background=TINTA)
            f.pack(fill="x", pady=p(10))
            tk.Label(f, text=clave, background=TINTA, foreground="#D5D8DB", font=self.F["base"]).pack(side="left")
            valor = tk.Label(f, text="", background=TINTA, foreground=BLANCO, font=self.F["fuerte"])
            valor.pack(side="right")
            self.resumen[clave] = valor
        self.b_procesar = Boton(r.cuerpo, self.pintor, "Procesar", self._procesar, clase="principal",
                                icono="flecha", icono_derecha=True, fondo=TINTA, fuente=self.F["fuerte"],
                                ancho=244, alto=50)
        self.b_procesar.pack(pady=(p(18), 0))
        self.b_atras = Boton(r.cuerpo, self.pintor, "Atrás", lambda: self._ir_a_paso(self.paso - 1),
                             clase="oscuro", fondo=TINTA, ancho=244, alto=40)
        a = Tarjeta(lateral, self.pintor, fondo_padre=FONDO, radio=14, margen=(8, 8, 8, 8))
        a.pack(fill="x", pady=(p(16), 0))
        self.b_abrir = Boton(a.cuerpo, self.pintor, "Abrir carpeta de resultados", self._abrir, clase="fila",
                             icono="carpeta", ancho=282, alto=40, alinear="izquierda")
        self.b_abrir.pack()
        self.b_abrir.activar(False)
        Boton(a.cuerpo, self.pintor, "Ver registro", lambda: self._ir("registro"), clase="fila",
              icono="registro", ancho=282, alto=40, alinear="izquierda").pack(pady=(p(2), 0))

        self._montar_procesando(pagina)
        self._ir_a_paso(1)
        self._pintar_modo()
        self._resumir()

    def _pintar_modo(self):
        elegido = self.modo.get()
        for valor, (t, marca, _desc, _chip) in self.opciones_modo.items():
            activo = valor == elegido
            t.colores(ROJO if activo else FILETE, 2 if activo else 1)
            marca.configure(image=self.pintor.radio(activo, BLANCO))

    def _ir_a_paso(self, paso: int):
        """Muestra solo las tarjetas de ese paso. Sin documentos no se pasa del primero."""
        paso = max(1, min(len(PASOS), paso))
        if paso > 1 and self.lista.size() == 0:
            paso = 1
        self.paso = paso
        for marcos in self.pasos_tarjetas.values():
            for m in marcos:
                m.pack_forget()
        for m in self.pasos_tarjetas[paso]:
            m.pack(fill="x", pady=(0, self.px(16)), before=self.pie_columna)
        for n, (marca, rotulo) in enumerate(self.pasos_rotulos, start=1):
            actual, hecho = n == paso, n < paso
            marca.configure(image=self.pintor.paso(n, "actual" if actual else "hecho" if hecho else "pendiente"))
            rotulo.configure(foreground=TINTA if actual or hecho else SECUNDARIO,
                             font=self.F["fuerte" if actual else "base"])
        # las tarjetas recién mostradas se pintan ya, sin esperar: evita un parpadeo sin fondo
        self.update_idletasks()
        for m in self.pasos_tarjetas[paso]:
            for t in (m, *[w for w in m.cuerpo.winfo_children() if isinstance(w, Tarjeta)]):
                t._redibujar()
        if hasattr(self, "opciones_modo"):
            for t, *_ in self.opciones_modo.values():
                t._redibujar()
        lienzo = getattr(self.pie_columna.master, "lienzo", None)
        if lienzo is not None:
            lienzo.yview_moveto(0)
        if paso > 1:
            self.b_atras.pack(pady=(self.px(8), 0))
        else:
            self.b_atras.pack_forget()
        self._resumir()

    def _resumir(self):
        """Columna «Resumen»: lo que va a pasar al pulsar Procesar."""
        if not hasattr(self, "resumen"):
            return
        from .clasificar import leer_palabras_clave

        n = self.lista.size()
        self.resumen["Expedientes"].configure(text=str(n))
        self.resumen["Lectura"].configure(text="Con IA" if self.modo.get() == "gemini" else "Básico, sin IA")
        self.resumen["Tipo"].configure(text={"Detectar automáticamente": "Automático"}.get(
            self.ambito.get(), self.ambito.get()))
        self.resumen["Salida"].configure(text="Un único PDF" if self.unir.get() or n <= 1
                                         else "Un PDF por expediente")
        temas = leer_palabras_clave(self.palabras.get("1.0", "end")) if hasattr(self, "palabras") else {}
        self.resumen["Temas de búsqueda"].configure(text=str(len(temas)))
        self.resumen["Licencia"].configure(text=self.licencia.rotulo,
                                           foreground=BLANCO if self.licencia.puede_procesar else "#FF8A95")
        if getattr(self, "paso", 3) < len(PASOS) and not (self.hilo and self.hilo.is_alive()):
            # pasos 1 y 2: el botón rojo solo avanza
            self.b_procesar.orden = lambda: self._ir_a_paso(self.paso + 1)
            self.b_procesar.rotular("Siguiente" if n else "Añada algún documento")
            self.b_procesar.activar(n > 0)
            return
        if not (self.hilo and self.hilo.is_alive()):
            if not self.licencia.puede_procesar:
                self.b_procesar.orden = self._ir_a_licencia
                self.b_procesar.rotular("Activar licencia")
                self.b_procesar.activar(True)
                return
            self.b_procesar.orden = self._procesar
            self.b_procesar.rotular(f"Procesar  {n} expediente{'s' if n != 1 else ''}" if n else "Procesar")
            self.b_procesar.activar(n > 0)

    def _sugerir_palabra(self, linea: str) -> None:
        """Añade una línea de ejemplo («Cliente: ») y deja el cursor al final para completarla."""
        actual = self.palabras.get("1.0", "end-1c")
        if actual and not actual.endswith("\n"):
            self.palabras.insert("end", "\n")
        self.palabras.insert("end", linea)
        self.palabras.mark_set("insert", "end-1c")
        self.palabras.see("end")
        self.palabras.focus_set()

    # --- vista «Procesando» -----------------------------------------------------
    def _montar_procesando(self, pagina):
        p = self.px
        self.v_proceso = tk.Frame(pagina, background=FONDO)
        caja = tk.Frame(self.v_proceso, background=FONDO)
        caja.pack(fill="both", expand=True, padx=p(40), pady=(p(32), 0))
        panel = Tarjeta(caja, self.pintor, fondo_padre=FONDO, relleno=TINTA, borde=None, radio=14,
                        margen=(32, 28, 32, 28))
        panel.pack(fill="x")
        c = panel.cuerpo
        self.pr_etiqueta = tk.Label(c, text="PROCESANDO", background=TINTA, foreground="#D5D8DB",
                                    font=self.F["caps"])
        self.pr_etiqueta.grid(row=0, column=0, sticky="w")
        self.pr_titulo = tk.Label(c, text="", background=TINTA, foreground=BLANCO, font=self.F["panel"])
        self.pr_titulo.grid(row=1, column=0, sticky="w", pady=(p(4), 0))
        self.pr_pct = tk.Label(c, text="0 %", background=TINTA, foreground=BLANCO, font=self.F["pct"])
        self.pr_pct.grid(row=0, column=1, rowspan=2, sticky="e")
        c.columnconfigure(0, weight=1)
        self.pr_barra = tk.Canvas(c, height=p(5), background=TINTA_2, highlightthickness=0, borderwidth=0)
        self.pr_barra.grid(row=2, column=0, columnspan=2, sticky="we", pady=(p(18), p(22)))
        self.pr_barra.bind("<Configure>", lambda e: self._pintar_barra())
        self._fraccion = 0.0
        self.pr_detalle = tk.Label(c, text="", background=TINTA, foreground="#D5D8DB", font=self.F["peq"],
                                   anchor="w", justify="left", wraplength=p(760))
        self.pr_detalle.grid(row=3, column=0, sticky="w")
        botones = tk.Frame(c, background=TINTA)
        botones.grid(row=3, column=1, sticky="e")
        self.b_cancelar = Boton(botones, self.pintor, "Cancelar", self.cancelar.set, clase="oscuro", fondo=TINTA)
        self.b_volver = Boton(botones, self.pintor, "Volver", self._volver, clase="oscuro", fondo=TINTA)
        self.b_abrir_fin = Boton(botones, self.pintor, "Abrir carpeta de resultados", self._abrir,
                                 clase="principal", icono="carpeta", fondo=TINTA)
        self.b_cancelar.pack(side="right")

        tabla = Tarjeta(caja, self.pintor, fondo_padre=FONDO, radio=12, margen=(2, 8, 2, 8))
        tabla.pack(fill="x", pady=(p(16), 0))
        self.pr_tabla = tabla.cuerpo
        tk.Label(caja, text="Puede seguir trabajando; el detalle completo queda en «Ver registro».",
                 background=FONDO, foreground=SECUNDARIO, font=self.F["peq"]).pack(anchor="w", pady=(p(12), 0))

    def _pintar_barra(self):
        b = self.pr_barra
        ancho, alto = b.winfo_width(), b.winfo_height()
        b.delete("all")
        b.create_rectangle(0, 0, int(ancho * max(0.0, min(1.0, self._fraccion))), alto, fill=ROJO, width=0)

    def _filas_proceso(self, nombres: list[str]):
        p, t = self.px, self.pr_tabla
        for w in t.winfo_children():
            w.destroy()
        anchos = (p(44), None, p(110), p(118), p(96), p(110))
        enc = tk.Frame(t, background=BLANCO)
        enc.pack(fill="x", padx=p(24), pady=(p(6), p(12)))
        enc.columnconfigure(1, weight=1)
        for col, texto in enumerate(("N.º", "EXPEDIENTE") + tuple(f.upper() for f in FASES)):
            tk.Label(enc, text=texto, background=BLANCO, foreground=SECUNDARIO, font=self.F["caps"],
                     width=0, anchor="w" if col < 2 else "center").grid(row=0, column=col, sticky="we")
            if anchos[col]:
                enc.columnconfigure(col, minsize=anchos[col])
        self.pr_filas = []
        for n, nombre in enumerate(nombres):
            tk.Frame(t, background=FILETE, height=1).pack(fill="x")
            f = tk.Frame(t, background=BLANCO)
            f.pack(fill="x", padx=p(24), pady=p(12))
            f.columnconfigure(1, weight=1)
            for col, ancho in enumerate(anchos):
                if ancho:
                    f.columnconfigure(col, minsize=ancho)
            tk.Label(f, text=f"{n + 1:02d}", background=BLANCO, foreground=SECUNDARIO,
                     font=self.F["peq"], anchor="w").grid(row=0, column=0, rowspan=2, sticky="w")
            tk.Label(f, text=nombre, background=BLANCO, foreground=TINTA, font=self.F["base"],
                     anchor="w").grid(row=0, column=1, sticky="w")
            sub = tk.Label(f, text="En cola", background=BLANCO, foreground=SECUNDARIO, font=self.F["mini"],
                           anchor="w")
            sub.grid(row=1, column=1, sticky="w")
            celdas = []
            for k in range(len(FASES)):
                celda = tk.Label(f, text="Pendiente", background=BLANCO, foreground=SECUNDARIO,
                                 font=self.F["mini"], compound="left")
                celda.grid(row=0, column=2 + k, rowspan=2)
                celdas.append(celda)
            self.pr_filas.append({"sub": sub, "celdas": celdas, "error": False, "nombre": nombre})

    def _marcar_fila(self, n: int, fases_hechas: int, en_curso: bool, texto: str = ""):
        fila = self.pr_filas[n]
        if fila["error"]:
            return
        for k, celda in enumerate(fila["celdas"]):
            if k < fases_hechas:
                celda.configure(text=" Hecho", foreground=BIEN_TEXTO, font=self.F["mini"],
                                image=self.pintor.icono("visto", BIEN_TEXTO, BLANCO, 13))
            elif k == fases_hechas and en_curso:
                celda.configure(text=" En curso", foreground=ROJO, font=(self.F["mini"][0], 8, "bold"),
                                image=self.pintor.icono("curso", ROJO, BLANCO, 13))
            else:
                celda.configure(text="Pendiente", foreground=SECUNDARIO, font=self.F["mini"], image="")
        if texto:
            fila["sub"].configure(text=texto)

    def _avance(self, mensaje: str, fraccion: float):
        """Traduce el avance global (0-1) a «expediente n de N» y a la fase de cada fila."""
        self._fraccion = fraccion
        self._pintar_barra()
        self.pr_pct.configure(text=f"{round(fraccion * 100)} %")
        total = len(getattr(self, "pr_filas", []))
        if not total:
            return
        n = min(total - 1, int(fraccion * total))
        local = fraccion * total - n
        self.pr_titulo.configure(text=f"Expediente {n + 1} de {total}")
        if mensaje.startswith("ERROR en «"):
            for i, fila in enumerate(self.pr_filas):
                if fila["nombre"] in mensaje:
                    fila["error"] = True
                    fila["sub"].configure(text="Ha fallado; se sigue con los demás", foreground=ROJO)
        ia = self.cfg.modo == "gemini"
        separado = self.cfg.motor_clasificacion in ("claude", "mistral")
        if local < (0.3 if ia and separado else 0.9):
            fase = 0
        elif local < 0.9:
            fase = 1
        elif local < 0.92:
            fase = 2
        else:
            fase = 3
        for i in range(n):
            self._marcar_fila(i, len(FASES), False, "Terminado")
        corta = mensaje if len(mensaje) < 70 else mensaje[:67] + "…"
        self._marcar_fila(n, fase, True, corta)

    def _ver_proceso(self, visible: bool):
        if visible:
            self.v_formulario.pack_forget()
            self.v_proceso.pack(fill="both", expand=True)
        else:
            self.v_proceso.pack_forget()
            self.v_formulario.pack(fill="both", expand=True)

    def _volver(self):
        self._ver_proceso(False)
        self._ir_a_paso(1)

    def _fin_proceso(self, clase: str, mensaje: str):
        """Al terminar: todas las filas hechas (salvo las fallidas) y los botones de salida."""
        if clase == "fin" and not mensaje.startswith("Cancelado"):
            for i in range(len(getattr(self, "pr_filas", []))):
                self._marcar_fila(i, len(FASES), False, "Terminado")
            self._fraccion = 1.0
            self.pr_pct.configure(text="100 %")
            self.pr_etiqueta.configure(text="TERMINADO")
            self.pr_titulo.configure(text="Proceso completado")
        elif clase == "fin":
            self.pr_etiqueta.configure(text="CANCELADO")
            self.pr_titulo.configure(text="Proceso cancelado")
        else:
            self.pr_etiqueta.configure(text="CON ERRORES")
            self.pr_titulo.configure(text="Revise el registro")
        self._pintar_barra()
        self.pr_detalle.configure(text=mensaje.splitlines()[0][:200])
        self.b_cancelar.pack_forget()
        self.b_volver.pack(side="right")
        if self.ultima_salida and self.ultima_salida.exists():
            self.b_abrir_fin.pack(side="right", padx=(0, self.px(8)))

    # --- Registro --------------------------------------------------------------
    def _montar_registro(self, pagina):
        p = self.px
        caja = tk.Frame(pagina, background=FONDO)
        caja.pack(fill="both", expand=True, padx=p(40), pady=p(32))
        t = Tarjeta(caja, self.pintor, fondo_padre=FONDO, radio=14)
        t.pack(fill="both", expand=True)
        c = t.cuerpo
        cab = tk.Frame(c, background=BLANCO)
        cab.pack(fill="x", pady=(0, p(14)))
        tk.Label(cab, text="Registro de esta sesión", background=BLANCO, foreground=TINTA,
                 font=self.F["tarjeta"]).pack(side="left")
        Boton(cab, self.pintor, "Abrir el archivo de registro", self._ver_registro, icono="registro").pack(
            side="right")
        self.registro = tk.Text(c, state="disabled", wrap="word", relief="flat", borderwidth=0,
                                background=CAMPO, foreground=TINTA, padx=p(14), pady=p(12),
                                font=self.F["mono"], highlightthickness=0)
        self.registro.pack(fill="both", expand=True)

    # --- entradas ----------------------------------------------------------------
    def _anadir(self, rutas):
        """Añade al final, cada tanda en orden natural de nombre («Tomo 2» antes que «Tomo 10»).

        Windows entrega en desorden los archivos arrastrados o elegidos a la vez; el orden de la
        lista es el que se usa al unir, y se puede cambiar con las flechas de cada fila y A–Z.
        """
        actuales = set(self.lista.get(0, "end"))
        nuevas = [r for r in sorted((r for r in rutas if r), key=lambda r: _orden_natural(Path(r)))
                  if r not in actuales]
        if nuevas:
            self.lista.rutas.extend(dict.fromkeys(nuevas))
            self.lista._pintar()

    def _ordenar(self):
        """Ordena toda la lista por nombre de archivo, con los números en orden natural."""
        self.lista.rutas.sort(key=lambda r: _orden_natural(Path(r)))
        self.lista._pintar()

    def _anadir_archivos(self):
        tipos = " ".join("*" + e for e in sorted(EXT_VALIDAS))
        self._anadir(filedialog.askopenfilenames(
            title="Elegir PDF o imágenes", filetypes=[("PDF e imágenes", tipos), ("Todos", "*.*")]))

    def _anadir_carpeta(self):
        self._anadir([filedialog.askdirectory(title="Elegir carpeta")])

    def _soltar(self, evento):
        self._anadir(self.tk.splitlist(evento.data))

    def _quitar(self):
        self.lista.rutas.clear()
        self.lista._pintar()

    def _nombres(self):
        """Plantillas de nombre de la carpeta y los archivos que se generan, con vista previa."""
        d = tk.Toplevel(self)
        d.title("Nombres de los archivos resultantes")
        d.transient(self)
        d.grab_set()
        d.configure(background=BLANCO)
        f = ttk.Frame(d, padding=self.px(24))
        f.pack(fill="both", expand=True)
        campos = [("Carpeta de cada expediente", "carpeta", ""), ("PDF con marcadores", "pdf", ".pdf"),
                  ("Índice en Excel", "excel", ".xlsx"), ("Cada documento (si se separan)", "documento", ".pdf")]
        v = {c: tk.StringVar(value=getattr(self.cfg, f"nombre_{c}")) for _, c, _ in campos}
        previas = {c: tk.StringVar() for _, c, _ in campos}
        for n, (texto, clave, ext) in enumerate(campos):
            ttk.Label(f, text=texto).grid(row=2 * n, column=0, sticky="w", pady=(8, 0), padx=(0, 12))
            ttk.Entry(f, textvariable=v[clave], width=48).grid(row=2 * n, column=1, sticky="we", pady=(8, 0))
            if ext:
                ttk.Label(f, text=ext).grid(row=2 * n, column=2, sticky="w", padx=(6, 0), pady=(8, 0))
            ttk.Label(f, textvariable=previas[clave], style="Nota.TLabel").grid(
                row=2 * n + 1, column=1, columnspan=2, sticky="w")

        def actualizar(*_):
            p = Plantillas(**{c: x.get() for c, x in v.items()})
            previas["carpeta"].set("Ejemplo: " + p.de_carpeta("PO 123-2025"))
            previas["pdf"].set("Ejemplo: " + p.de_pdf("PO 123-2025"))
            previas["excel"].set("Ejemplo: " + p.de_excel("PO 123-2025"))
            previas["documento"].set("Ejemplo: " + p.de_documento(EJEMPLO, "PO 123-2025"))

        for x in v.values():
            x.trace_add("write", actualizar)
        actualizar()
        fila = 2 * len(campos)
        ttk.Label(f, style="Nota.TLabel", wraplength=self.px(600), justify="left", text=(
            f"Huecos que se rellenan solos: {HUECOS_GENERALES}.\nSolo para cada documento: "
            f"{HUECOS_DOCUMENTO}.\nLos caracteres que Windows no admite se sustituyen por un espacio; "
            "si una plantilla queda vacía se usa la de por defecto. El archivo original nunca se "
            "sobrescribe.")).grid(row=fila, column=0, columnspan=3, sticky="w", pady=(14, 12))

        def restaurar():
            for c, x in v.items():
                x.set(POR_DEFECTO[c])

        def guardar():
            for c, x in v.items():
                setattr(self.cfg, f"nombre_{c}", x.get().strip() or POR_DEFECTO[c])
            config.guardar(self.cfg)
            d.destroy()

        botones = tk.Frame(f, background=BLANCO)
        botones.grid(row=fila + 1, column=0, columnspan=3, sticky="we")
        Boton(botones, self.pintor, "Restaurar", restaurar).pack(side="left")
        Boton(botones, self.pintor, "Guardar", guardar, clase="principal").pack(side="right")

    def _elegir_salida(self):
        carpeta = filedialog.askdirectory(title="Carpeta de resultados")
        if carpeta:
            self.salida.set(carpeta)

    def _rotulo_gemini(self):
        """Texto de la opción «Con IA» según quién lee y quién clasifica."""
        c = self.cfg
        if c.motor_clasificacion == "claude":
            texto = "Mistral lee las páginas escaneadas y Claude clasifica los documentos."
            aviso = "Envía datos a la API de Anthropic"
        elif c.motor_clasificacion == "mistral":
            texto = "Mistral lee y clasifica los documentos."
            aviso = "Envía datos a Mistral AI"
        elif c.motor_ocr == "mistral":
            texto = "Mistral lee las páginas; Gemini (Vertex AI) clasifica."
            aviso = "Envía datos a Mistral y a Google"
        else:
            texto = "Gemini (Vertex AI) lee y clasifica los documentos."
            aviso = "Envía datos a Google Vertex AI"
        if c.mistral_economico and c.motor_ocr == "mistral":
            texto += " Lectura en modo económico."
        _t, _m, desc, chip = self.opciones_modo["gemini"]
        desc.configure(text=texto)
        chip.rotulo.configure(text=aviso)

    # --- Configuración ---------------------------------------------------------
    def _configurar(self, contenedor=None):
        """Sin contenedor, muestra la pestaña «Configuración»; con él, la monta (una sola vez)."""
        if contenedor is None:
            self._ir("config")
            return
        p = self.px
        d = self
        # barra fija inferior: guardar o descartar
        barra = tk.Frame(contenedor, background=BLANCO)
        barra.pack(side="bottom", fill="x")
        tk.Frame(contenedor, background=FILETE, height=1).pack(side="bottom", fill="x")
        self.cfg_estado = tk.Label(barra, text="La configuración se guarda en este equipo.", background=BLANCO,
                                   foreground=SECUNDARIO, font=self.F["peq"], anchor="w")
        self.cfg_estado.pack(side="left", padx=p(40), pady=p(14))
        b_guardar = Boton(barra, self.pintor, "Guardar cambios", lambda: aceptar(), clase="principal")
        b_guardar.pack(side="right", padx=(0, p(40)))
        b_descartar = Boton(barra, self.pintor, "Descartar", lambda: descartar())
        b_descartar.pack(side="right", padx=(0, p(8)))

        cuerpo = tk.Frame(contenedor, background=FONDO)
        cuerpo.pack(fill="both", expand=True)
        nav = tk.Frame(cuerpo, background=FONDO, width=p(220))
        nav.pack(side="left", fill="y", padx=(p(40), p(24)), pady=(p(32), 0))
        derecha = tk.Frame(cuerpo, background=FONDO)
        derecha.pack(side="left", fill="both", expand=True, padx=(0, p(40)), pady=(p(32), 0))
        col = desplazable(derecha)

        v = {k: tk.StringVar(value=str(getattr(self.cfg, k)))
             for k in ("proyecto", "ubicacion", "modelo", "credenciales", "paginas_por_lote",
                       "consultas_paralelas", "token_github", "mistral_clave", "mistral_servidor",
                       "mistral_modelo_clasificacion", "mistral_modelo", "claude_clave", "claude_modelo")}
        economico = tk.BooleanVar(value=self.cfg.mistral_economico)
        usar_texto = tk.BooleanVar(value=self.cfg.usar_texto_existente)
        buscar_al_abrir = tk.BooleanVar(value=self.cfg.buscar_actualizaciones)
        from .ocr_mistral import SERVIDOR_GLOBAL, SERVIDOR_UE

        MOTORES = {"Mistral OCR": "mistral", "Gemini (Vertex AI)": "gemini"}
        CLASIFICADORES = {"Claude (Anthropic)": "claude", "Mistral": "mistral", "Gemini (Vertex AI)": "gemini"}
        motor = tk.StringVar()
        clasificador = tk.StringVar()

        def campo(padre, fila, columna, rotulo, widget, nota="", ancho_col=1):
            self._rotulo(padre, rotulo).grid(row=fila, column=columna, columnspan=ancho_col, sticky="w",
                                             pady=(p(14) if fila else 0, p(6)),
                                             padx=(0 if columna == 0 else p(16), 0))
            widget.grid(row=fila + 1, column=columna, columnspan=ancho_col, sticky="we", ipady=p(3),
                        padx=(0 if columna == 0 else p(16), 0))
            if nota:
                tk.Label(padre, text=nota, background=BLANCO, foreground=SECUNDARIO, font=self.F["mini"],
                         anchor="w", justify="left", wraplength=p(330)).grid(
                    row=fila + 2, column=columna, columnspan=ancho_col, sticky="w", pady=(p(6), 0),
                    padx=(0 if columna == 0 else p(16), 0))

        def rejilla(padre):
            g = tk.Frame(padre, background=BLANCO)
            g.pack(fill="x")
            g.columnconfigure((0, 1), weight=1, uniform="c")
            return g

        def texto_nota(padre, texto):
            tk.Label(padre, text=texto, background=BLANCO, foreground=SECUNDARIO, font=self.F["mini"],
                     anchor="w", justify="left", wraplength=p(680)).pack(fill="x", pady=(p(14), 0))

        secciones = {}

        # Licencia
        c = self._tarjeta(col, "Licencia", subtitulo=(
            "Marcadores PDF funciona con una suscripción. La clave de la cuenta es la misma que la de "
            "Sumario; la primera activación abre una prueba gratuita de 30 días para 3 equipos."))
        secciones["Licencia"] = c.marco
        self.lic_estado = tk.Frame(c, background=BLANCO)
        self.lic_estado.pack(fill="x", pady=(0, p(16)))
        g = rejilla(c)
        clave_l = tk.Frame(g, background=BLANCO)
        self.lic_clave = tk.StringVar(value=self.cfg.licencia_clave)
        ttk.Entry(clave_l, textvariable=self.lic_clave, show="•").pack(side="left", fill="x", expand=True,
                                                                        ipady=p(3))
        b_activar = Boton(clave_l, self.pintor, "Activar", lambda: activar(), clase="principal")
        b_activar.pack(side="left", padx=(p(8), 0))
        campo(g, 0, 0, "Clave de la cuenta (SUM-…)", clave_l, ancho_col=2)
        f = tk.Frame(c, background=BLANCO)
        f.pack(fill="x", pady=(p(14), 0))
        tk.Label(f, text="¿Sin cuenta, o quiere suscribirse o liberar un equipo?", background=BLANCO,
                 foreground=SECUNDARIO, font=self.F["peq"]).pack(side="left")
        Boton(f, self.pintor, "Abrir el portal de la cuenta", lambda: __import__("webbrowser").open(licencia.PORTAL),
              clase="enlace", icono="flecha", icono_derecha=True, fuente=self.F["peq"], alto=24).pack(
            side="left", padx=(p(8), 0))

        def activar():
            clave = self.lic_clave.get().strip()
            if not licencia.clave_valida(clave):
                messagebox.showwarning("Licencia", "La clave de la cuenta empieza por «SUM-» y tiene 36 "
                                       "caracteres. Cópiela del portal de su cuenta.", parent=d)
                return
            if not self.cfg.licencia_huella:
                self.cfg.licencia_huella = licencia.nueva_huella()
            huella = self.cfg.licencia_huella

            def trabajo():
                return licencia.pedir_testigo(clave, huella)

            def fin(r):
                if "e" in r:
                    texto = str(r["e"]) if isinstance(r["e"], licencia.ErrorLicencia) else f"Error: {r['e']}"
                    self.cfg_estado.configure(text=texto.splitlines()[0], foreground=AVISO_TEXTO)
                    messagebox.showwarning("Licencia", texto, parent=d)
                    return
                self.cfg.licencia_clave, self.cfg.licencia_testigo = clave, r["r"]
                try:
                    config.guardar(self.cfg)
                except OSError:
                    pass
                self.licencia = licencia.estado(self.cfg.licencia_testigo)
                self._pintar_licencia()
                self._resumir()
                self.cfg_estado.configure(text=f"Licencia: {self.licencia.rotulo.lower()}.", foreground=SECUNDARIO)
                self._estado_pie(f"Licencia {self.licencia.rotulo.lower()}.",
                                 "bien" if self.licencia.puede_procesar else "aviso")

            tarea_en_hilo(b_activar, "Comprobando la licencia…", trabajo, fin)

        # Resultados
        c = self._tarjeta(col, "Opciones de los resultados", subtitulo="Qué se genera para cada expediente.")
        secciones["Resultados"] = c.marco
        g = rejilla(c)
        for n, (var, titulo, desc) in enumerate((
                (self.indice, "Índice al inicio del PDF", "Página de índice antes del primer documento."),
                (self.capa, "Texto buscable", "Capa de texto sobre las páginas escaneadas."),
                (self.separar, "Un PDF por documento", "Además del expediente completo."),
                (self.enderezar, "Enderezar páginas giradas", "Corrige la orientación automáticamente."),
                (self.blancas, "Quitar páginas en blanco", "Atención: cambia la numeración de páginas."),
                (self.rag, "Exportar para RAG", "Formato listo para AnythingLLM."),
                (self.nav, "Botones de navegación", "En cada página: volver al índice y saltar al documento "
                                                    "anterior o siguiente de su clase. No se imprimen."))):
            self._interruptor(g, var, titulo, desc).grid(row=n // 2, column=n % 2, sticky="w",
                                                         pady=(0, p(18)), padx=(p(10), 0))

        # Contenido del índice
        c = self._tarjeta(col, "Contenido del índice", subtitulo=(
            "La lista de todos los documentos va siempre. Lo que desmarque no aparece ni en el índice "
            "ni en los marcadores."))
        secciones["Contenido del índice"] = c.marco
        segmentos = c.cabecera.acciones
        Boton(segmentos, self.pintor, "Solo la lista", lambda: [x.set(False) for x in self.contenido.values()],
              clase="chip", fuente=self.F["peq"], alto=34).pack(side="left")
        Boton(segmentos, self.pintor, "Todo", lambda: [x.set(True) for x in self.contenido.values()],
              clase="chip", fuente=self.F["peq"], alto=34).pack(side="left", padx=(p(6), 0))
        g = rejilla(c)
        for n, (clave, titulo, desc) in enumerate((
                ("cronologia", "Cronología", "Procesal o de hechos."),
                ("grupos", "Apartados por clase", "Resoluciones, declaraciones, escritos…"),
                ("agenda", "Señalamientos y plazos", "Fechas de vistas y vencimientos."),
                ("remisiones", "Remisiones entre documentos", "Qué escritos citan cada resolución."),
                ("palabras", "Palabras clave", "Apartado por cada tema indicado."))):
            self._interruptor(g, self.contenido[clave], titulo, desc).grid(
                row=n // 2, column=n % 2, sticky="w", pady=(0, p(18)), padx=(p(10), 0))

        # Quién lee y quién clasifica
        c = self._tarjeta(col, "Quién lee y quién clasifica",
                          subtitulo="Abajo solo se muestran los datos de los servicios elegidos.")
        secciones["Quién lee y clasifica"] = c.marco
        g = rejilla(c)
        campo(g, 0, 0, "Lee las páginas escaneadas",
              ttk.Combobox(g, textvariable=motor, values=list(MOTORES), state="readonly"))
        campo(g, 0, 1, "Clasifica los documentos",
              ttk.Combobox(g, textvariable=clasificador, values=list(CLASIFICADORES), state="readonly"))
        aviso_claude = self._aviso(c, "Con Claude como clasificador, el texto de los documentos se envía a la "
                                      "API de Anthropic.", envolver=680)

        # Mistral
        c = self._tarjeta(col, "Mistral")
        secciones["Mistral"] = c.marco
        g = rejilla(c)
        clave_m = tk.Frame(g, background=BLANCO)
        ttk.Entry(clave_m, textvariable=v["mistral_clave"], show="•").pack(side="left", fill="x", expand=True,
                                                                           ipady=p(3))
        b_mistral = Boton(clave_m, self.pintor, "Probar", lambda: probar_mistral())
        b_mistral.pack(side="left", padx=(p(8), 0))
        campo(g, 0, 0, "Clave de API", clave_m, ancho_col=2)
        campo(g, 3, 0, "Servidor", ttk.Combobox(g, textvariable=v["mistral_servidor"],
                                                values=[SERVIDOR_UE, SERVIDOR_GLOBAL]))
        campo(g, 3, 1, "Modelo para clasificar", ttk.Entry(g, textvariable=v["mistral_modelo_clasificacion"]),
              "Solo se usa si Mistral clasifica.")
        from .ocr_mistral import MODELO as OCR_ACTUAL, MODELO_ANTERIOR

        g2 = rejilla(c)
        g2.pack_configure(pady=(p(14), 0))
        campo(g2, 0, 0, "Modelo de OCR", ttk.Combobox(g2, textvariable=v["mistral_modelo"],
                                                     values=[OCR_ACTUAL, MODELO_ANTERIOR]),
              "El anterior cuesta menos por página. Compárelos antes de cambiar.")
        f_comp = tk.Frame(g2, background=BLANCO)
        b_comparar = Boton(f_comp, self.pintor, "Comparar modelos de OCR…", lambda: comparar_ocr())
        b_comparar.pack(anchor="w")
        campo(g2, 0, 1, "Prueba con un PDF suyo (10 páginas)", f_comp)
        f = tk.Frame(c, background=BLANCO)
        f.pack(fill="x", pady=(p(18), 0))
        self._interruptor(f, economico, "Modo económico del OCR",
                          "La mitad de precio, pero puede tardar horas. Para cargas grandes sin prisa.").pack(
            anchor="w", padx=(p(10), 0))
        texto_nota(c, "Las páginas escaneadas se envían a Mistral AI. En modo económico Mistral "
                      "atiende el encargo cuando tiene hueco (como máximo 24 h); se puede cerrar el programa y, "
                      "al volver a procesar, se recoge lo hecho sin pagarlo dos veces. La clave se obtiene en "
                      "console.mistral.ai.")

        # Anthropic
        c = self._tarjeta(col, "Anthropic")
        secciones["Anthropic"] = c.marco
        g = rejilla(c)
        clave_c = tk.Frame(g, background=BLANCO)
        ttk.Entry(clave_c, textvariable=v["claude_clave"], show="•").pack(side="left", fill="x", expand=True,
                                                                          ipady=p(3))
        b_claude = Boton(clave_c, self.pintor, "Probar", lambda: probar_claude())
        b_claude.pack(side="left", padx=(p(8), 0))
        campo(g, 0, 0, "Clave de API", clave_c, ancho_col=2)
        campo(g, 3, 0, "Modelo para clasificar", ttk.Entry(g, textvariable=v["claude_modelo"]))
        texto_nota(c, "Se usa la API directa de Anthropic, donde se aplican los créditos mensuales de los "
                      "planes Max, Ultra y Team (clave creada en platform.claude.com, en la organización "
                      "vinculada al plan).")

        # Google
        c = self._tarjeta(col, "Google Vertex AI")
        secciones["Google Vertex AI"] = c.marco
        g = rejilla(c)
        campo(g, 0, 0, "ID del proyecto de Google Cloud", ttk.Entry(g, textvariable=v["proyecto"]))
        campo(g, 0, 1, "Ubicación", ttk.Combobox(g, textvariable=v["ubicacion"],
                                                                 values=config.UBICACIONES_UE))
        campo(g, 3, 0, "Modelo de Gemini", ttk.Entry(g, textvariable=v["modelo"]))
        cred = tk.Frame(g, background=BLANCO)
        ttk.Entry(cred, textvariable=v["credenciales"]).pack(side="left", fill="x", expand=True, ipady=p(3))
        Boton(cred, self.pintor, "Buscar…", lambda: v["credenciales"].set(
            filedialog.askopenfilename(parent=d, filetypes=[("JSON", "*.json")]) or v["credenciales"].get())).pack(
            side="left", padx=(p(8), 0))
        campo(g, 3, 1, "Clave de la cuenta de servicio (.json)", cred)
        f = tk.Frame(c, background=BLANCO)
        f.pack(fill="x", pady=(p(16), 0))
        b_probar = Boton(f, self.pintor, "Probar conexión con Google", lambda: probar())
        b_probar.pack(side="left")
        texto_nota(c, "«Probar conexión» comprueba la combinación de ubicación y modelo y, si falla, "
                      "busca otra que funcione.")

        # Avanzado
        c = self._tarjeta(col, "Avanzado")
        secciones["Avanzado"] = c.marco
        g = rejilla(c)
        campo(g, 0, 0, "Páginas por consulta (1-40)", ttk.Entry(g, textvariable=v["paginas_por_lote"]))
        campo(g, 0, 1, "Consultas simultáneas (1-8)", ttk.Entry(g, textvariable=v["consultas_paralelas"]))
        campo(g, 3, 0, "Token de GitHub para actualizaciones (opcional)",
              ttk.Entry(g, textvariable=v["token_github"], show="•"), ancho_col=2)
        g = rejilla(c)
        g.pack_configure(pady=(p(20), 0))
        self._interruptor(g, usar_texto, "Aprovechar el texto que ya trae el PDF", "Mucho más rápido.").grid(
            row=0, column=0, sticky="w", padx=(p(10), 0))
        self._interruptor(g, buscar_al_abrir, "Buscar actualizaciones al abrir",
                          "Comprueba si hay versión nueva cada vez.").grid(row=0, column=1, sticky="w",
                                                                           padx=(p(10), 0))

        # Acerca de
        c = self._tarjeta(col, "Acerca de")
        secciones["Acerca de"] = c.marco
        tk.Label(c, text=(f"Marcadores PDF {__version__} · Creada por {AUTOR}. Software libre bajo licencia "
                          f"GNU AGPL-3.0, sin ninguna garantía. Código fuente y condiciones: {CODIGO}"),
                 background=BLANCO, foreground=SECUNDARIO, font=self.F["peq"], justify="left", anchor="w",
                 wraplength=p(680)).pack(fill="x")
        Boton(c, self.pintor, "Ver la presentación", self._presentacion).pack(anchor="w", pady=(p(14), 0))
        tk.Frame(col, background=FONDO, height=p(8)).pack()

        # navegación lateral
        botones_nav = {}

        def ir_a(nombre):
            marco = secciones[nombre]
            lienzo = col.lienzo
            alto = max(1, col.winfo_height())
            lienzo.yview_moveto(max(0.0, (marco.winfo_y() - p(4)) / alto))
            marcar(nombre)

        def marcar(nombre):
            for n, b in botones_nav.items():
                b.clase = "nav_activo" if n == nombre else "nav"
                b._pintar("normal")

        for nombre in secciones:
            b = Boton(nav, self.pintor, nombre, lambda n=nombre: ir_a(n), clase="nav", fondo=FONDO,
                      ancho=220, alto=40, alinear="izquierda")
            b.pack(pady=(0, p(3)))
            botones_nav[nombre] = b
        marcar("Licencia")
        self._ir_a_seccion = ir_a
        self._pintar_licencia()

        tarjetas_servicio = {"mistral": secciones["Mistral"], "claude": secciones["Anthropic"],
                             "google": secciones["Google Vertex AI"]}
        nav_servicio = {"mistral": botones_nav["Mistral"], "claude": botones_nav["Anthropic"],
                        "google": botones_nav["Google Vertex AI"]}

        def mostrar_servicios(*_):
            usa_ocr, clasifica = MOTORES.get(motor.get()), CLASIFICADORES.get(clasificador.get())
            visibles = {"mistral": usa_ocr == "mistral" or clasifica in ("mistral", "claude"),
                        "claude": clasifica == "claude",
                        "google": "gemini" in (usa_ocr, clasifica)}
            for clave in tarjetas_servicio:
                tarjetas_servicio[clave].pack_forget()
                nav_servicio[clave].pack_forget()
            for clave in ("mistral", "claude", "google"):
                if visibles[clave]:
                    tarjetas_servicio[clave].pack(fill="x", pady=(0, p(16)), before=secciones["Avanzado"])
                    nav_servicio[clave].pack(pady=(0, p(3)), before=botones_nav["Avanzado"])
            if clasifica == "claude":
                aviso_claude.pack(fill="x", pady=(p(16), 0))
            else:
                aviso_claude.pack_forget()

        # cambios pendientes: «Guardar cambios» solo se activa si hay algo que guardar
        vigiladas = [*v.values(), economico, usar_texto, buscar_al_abrir, motor, clasificador, self.indice,
                     self.capa, self.separar, self.enderezar, self.blancas, self.rag, self.nav,
                     *self.contenido.values()]
        cambios = {"hay": False}

        def pendiente(*_):
            if not cambios.get("cargando"):
                cambios["hay"] = True
                b_guardar.activar(True)
                b_descartar.activar(True)
                self.cfg_estado.configure(text="Hay cambios sin guardar.", foreground=AVISO_TEXTO)

        def limpio(texto="La configuración se guarda en este equipo."):
            cambios["hay"] = False
            b_guardar.activar(False)
            b_descartar.activar(False)
            self.cfg_estado.configure(text=texto, foreground=SECUNDARIO)

        def cargar():
            """Pone en pantalla lo guardado (al abrir y al descartar)."""
            cambios["cargando"] = True
            c = self.cfg
            for k, x in v.items():
                x.set(str(getattr(c, k)))
            economico.set(c.mistral_economico)
            usar_texto.set(c.usar_texto_existente)
            buscar_al_abrir.set(c.buscar_actualizaciones)
            motor.set(next((k for k, x in MOTORES.items() if x == c.motor_ocr), "Mistral OCR"))
            clasificador.set(next((k for k, x in CLASIFICADORES.items() if x == c.motor_clasificacion),
                                  "Claude (Anthropic)"))
            for var, valor in ((self.indice, c.indice_en_pdf), (self.capa, c.capa_texto),
                               (self.separar, c.separar), (self.enderezar, c.enderezar),
                               (self.blancas, c.quitar_blancas), (self.rag, c.exportar_rag),
                               (self.nav, c.navegacion)):
                var.set(valor)
            for k, var in self.contenido.items():
                var.set(getattr(c, f"indice_{k}"))
            cambios["cargando"] = False
            mostrar_servicios()

        def descartar():
            cargar()
            limpio("Cambios descartados.")

        def aceptar():
            try:
                lote = max(1, min(config.PAGINAS_POR_LOTE_MAX, int(v["paginas_por_lote"].get())))
            except ValueError:
                lote = config.PAGINAS_POR_LOTE
            nuevo_motor = MOTORES.get(motor.get(), "gemini")
            nuevo_clasificador = CLASIFICADORES.get(clasificador.get(), "gemini")
            if nuevo_clasificador in ("mistral", "claude"):
                nuevo_motor = "mistral"  # sin Gemini, las páginas escaneadas las lee Mistral
            if "mistral" in (nuevo_motor, nuevo_clasificador) and not v["mistral_clave"].get().strip():
                messagebox.showwarning("Marcadores PDF", "Para usar Mistral falta su clave de API "
                                       "(lee las páginas escaneadas).", parent=d)
                return
            if nuevo_clasificador == "claude" and not v["claude_clave"].get().strip():
                messagebox.showwarning("Marcadores PDF", "Para clasificar con Claude falta la clave de "
                                       "API de Anthropic.", parent=d)
                return
            c = self.cfg
            c.proyecto = v["proyecto"].get().strip()
            c.ubicacion = v["ubicacion"].get().strip()
            c.modelo = v["modelo"].get().strip()
            c.credenciales = v["credenciales"].get().strip()
            c.paginas_por_lote = lote
            try:
                c.consultas_paralelas = max(1, min(config.CONSULTAS_PARALELAS_MAX,
                                                   int(v["consultas_paralelas"].get())))
            except ValueError:
                c.consultas_paralelas = config.CONSULTAS_PARALELAS
            c.token_github = v["token_github"].get().strip()
            c.usar_texto_existente = usar_texto.get()
            c.buscar_actualizaciones = buscar_al_abrir.get()
            c.motor_ocr, c.motor_clasificacion = nuevo_motor, nuevo_clasificador
            c.mistral_clave = v["mistral_clave"].get().strip()
            c.mistral_servidor = v["mistral_servidor"].get().strip() or SERVIDOR_UE
            c.mistral_economico = economico.get()
            c.mistral_modelo = v["mistral_modelo"].get().strip() or OCR_ACTUAL
            c.mistral_modelo_clasificacion = (v["mistral_modelo_clasificacion"].get().strip()
                                              or "mistral-medium-latest")
            c.claude_clave = v["claude_clave"].get().strip()
            c.claude_modelo = v["claude_modelo"].get().strip() or "claude-haiku-5-5"
            self._volcar_config()
            self._rotulo_gemini()
            limpio("Configuración guardada.")
            self._estado_pie("Configuración guardada.")

        def tarea_en_hilo(boton, aviso_inicial, trabajo, al_terminar):
            boton.activar(False)
            self.cfg_estado.configure(text=aviso_inicial, foreground=SECUNDARIO)
            resultado = {}

            def tarea():
                try:
                    resultado["r"] = trabajo()
                except Exception as e:
                    registro.log.warning("Prueba de conexión: %s: %s", type(e).__name__, e)
                    resultado["e"] = e

            def vigilar():
                if not resultado:
                    d.after(200, vigilar)
                    return
                boton.activar(True)
                al_terminar(resultado)

            threading.Thread(target=tarea, daemon=True).start()
            vigilar()

        def probar_mistral():
            from .ocr_mistral import OCRMistral

            clave = v["mistral_clave"].get().strip()
            if not clave:
                messagebox.showwarning("Marcadores PDF", "Indique antes la clave de API de Mistral.", parent=d)
                return
            servidor = v["mistral_servidor"].get().strip() or SERVIDOR_UE
            modelo = v["mistral_modelo_clasificacion"].get().strip() or "mistral-medium-latest"
            clasifica = CLASIFICADORES.get(clasificador.get()) == "mistral"
            lotes = economico.get()

            def trabajo():
                from .clasificador_mistral import AnalizadorMistral

                ocr = OCRMistral(clave, servidor)
                texto = ocr.probar()
                if lotes:
                    texto += "\n" + ocr.probar_lotes()
                if clasifica:
                    texto += "\n\n" + AnalizadorMistral(clave, servidor, modelo).probar()
                return texto

            def fin(r):
                texto = r.get("r") or f"No funciona: {r.get('e')}"
                self.cfg_estado.configure(text=texto.splitlines()[0])
                messagebox.showinfo("Prueba de Mistral", texto, parent=d)

            tarea_en_hilo(b_mistral, "Probando Mistral con una página de prueba…", trabajo, fin)

        def comparar_ocr():
            from .ocr_mistral import comparar_modelos

            clave = v["mistral_clave"].get().strip()
            if not clave:
                messagebox.showwarning("Marcadores PDF", "Indique antes la clave de API de Mistral.", parent=d)
                return
            ruta = filedialog.askopenfilename(parent=d, title="PDF escaneado para comparar",
                                              filetypes=[("PDF", "*.pdf")])
            if not ruta:
                return
            servidor = v["mistral_servidor"].get().strip() or SERVIDOR_UE
            carpeta = Path(self.salida.get().strip() or config.carpeta_por_defecto()) / "Comparación de OCR"

            def trabajo():
                return comparar_modelos(clave, servidor, Path(ruta), carpeta, (OCR_ACTUAL, MODELO_ANTERIOR))

            def fin(r):
                texto = r.get("r") or f"No se ha podido comparar: {r.get('e')}"
                self.cfg_estado.configure(text=texto.splitlines()[0][:150])
                self._escribir(texto)
                messagebox.showinfo("Comparación de OCR", texto, parent=d)

            tarea_en_hilo(b_comparar, "Leyendo 10 páginas con cada modelo…", trabajo, fin)

        def probar_claude():
            clave = v["claude_clave"].get().strip()
            if not clave:
                messagebox.showwarning("Marcadores PDF", "Indique antes la clave de API de Anthropic.", parent=d)
                return
            modelo = v["claude_modelo"].get().strip() or "claude-haiku-5-5"

            def trabajo():
                from .clasificador_claude import AnalizadorClaude

                return AnalizadorClaude(clave, modelo).probar()

            def fin(r):
                texto = r.get("r") or f"No funciona: {r.get('e')}"
                self.cfg_estado.configure(text=texto.splitlines()[0])
                messagebox.showinfo("Prueba de Claude", texto, parent=d)

            tarea_en_hilo(b_claude, "Probando Claude con una página de prueba…", trabajo, fin)

        def probar():
            from .ocr_gemini import buscar_combinacion

            datos = {k: x.get().strip() for k, x in v.items()}
            if not (datos["proyecto"] and datos["credenciales"]):
                messagebox.showwarning("Marcadores PDF", "Indique antes el proyecto y la clave JSON.", parent=d)
                return

            def trabajo():
                return buscar_combinacion(datos["proyecto"], datos["credenciales"], datos["ubicacion"],
                                          datos["modelo"], config.UBICACIONES_UE, config.MODELOS_CANDIDATOS)

            def fin(r):
                bueno, informe = r.get("r") or (None, [f"{type(r['e']).__name__}: {r['e']}"])
                if bueno:
                    v["ubicacion"].set(bueno[0])
                    v["modelo"].set(bueno[1])
                    self.cfg_estado.configure(text=f"Funciona: {bueno[1]} en {bueno[0]}. Pulse «Guardar cambios».")
                else:
                    self.cfg_estado.configure(text="No se ha encontrado ninguna combinación que funcione.")
                messagebox.showinfo("Resultado de la prueba", "\n".join(informe), parent=d)

            tarea_en_hilo(b_probar, "Probando… puede tardar un minuto.", trabajo, fin)

        cargar()
        for x in vigiladas:
            x.trace_add("write", pendiente)
        motor.trace_add("write", mostrar_servicios)
        clasificador.trace_add("write", mostrar_servicios)
        limpio()

    def _ir_a_licencia(self):
        self._ir("config")
        self.after(50, lambda: self._ir_a_seccion("Licencia"))

    def _pintar_licencia(self):
        """Estado de la licencia en su tarjeta de Configuración."""
        marco = getattr(self, "lic_estado", None)
        if marco is None:
            return
        for w in marco.winfo_children():
            w.destroy()
        e = self.licencia
        clase = "bien" if e.puede_procesar else "aviso"
        texto = f"{e.rotulo}. {e.detalle}"
        if e.correo:
            texto += f"  Cuenta: {e.correo}."
        self._aviso(marco, texto, clase, envolver=680).pack(fill="x")

    # --- presentación ------------------------------------------------------------
    def _presentacion(self):
        """Pantalla de bienvenida: qué hace, qué claves necesita y quién la ha creado."""
        p = self.px
        d = tk.Toplevel(self)
        d.title("Bienvenido a Marcadores PDF")
        d.transient(self)
        d.configure(background=BLANCO)
        d.resizable(False, False)
        poner_icono(d)
        tk.Frame(d, background=ROJO, height=p(3)).pack(fill="x")
        f = tk.Frame(d, background=BLANCO)
        f.pack(fill="both", expand=True, padx=p(32), pady=(p(24), p(22)))
        tk.Label(f, text="Marcadores PDF", background=BLANCO, foreground=TINTA, font=self.F["app"]).pack(anchor="w")
        tk.Label(f, text=f"Versión {__version__} · creada por {AUTOR}", background=BLANCO, foreground=SECUNDARIO,
                 font=self.F["peq"]).pack(anchor="w", pady=(0, p(14)))

        def bloque(titulo, texto):
            tk.Label(f, text=titulo, background=BLANCO, foreground=ROJO, font=self.F["caps"]).pack(
                anchor="w", pady=(p(10), p(4)))
            tk.Label(f, text=texto, background=BLANCO, foreground=TINTA, font=self.F["base"], justify="left",
                     wraplength=p(560), anchor="w").pack(anchor="w")

        bloque("QUÉ HACE", (
            "Ordena expedientes judiciales escaneados: lee cada página, separa y clasifica los "
            "documentos (resoluciones, escritos de parte, declaraciones…), extrae plazos y "
            "señalamientos y genera un PDF con marcadores e índice enlazado, un Excel y una "
            "cronología. El archivo original nunca se modifica."))
        bloque("QUÉ NECESITA: UNA LICENCIA Y DOS CLAVES DE API", (
            "1. Una cuenta de Táctica Legal (la misma que la de Sumario). La primera activación abre "
            "una prueba gratuita de 30 días para 3 equipos; después, suscripción desde el portal.\n"
            "2. Mistral (lee las páginas escaneadas). Se obtiene en "
            "console.mistral.ai → API Keys.\n"
            "3. Claude, de Anthropic (clasifica los documentos). Se obtiene en platform.claude.com "
            "→ API Keys. Los planes Max, Ultra y Team de Claude incluyen créditos mensuales de API: "
            "se activan en claude.ai → Ajustes → Facturación.\n"
            "Las claves se pegan en la pestaña «Configuración» y se guardan solo en este equipo. "
            "Como alternativa a Claude se puede clasificar con Mistral o con Gemini (Google)."))
        bloque("TENGA EN CUENTA", (
            "Las páginas se envían al proveedor de IA elegido para leerlas y clasificarlas. "
            "La clasificación puede contener errores: revise siempre plazos y fechas."))
        tk.Label(f, text=(f"© {AUTOR}. Software libre bajo licencia GNU AGPL-3.0, sin ninguna garantía. "
                          f"Código fuente: {CODIGO}"), background=BLANCO, foreground=SECUNDARIO,
                 font=self.F["mini"], wraplength=p(560), justify="left").pack(anchor="w", pady=(p(16), p(10)))
        no_mostrar = tk.BooleanVar(value=self.cfg.ocultar_presentacion)
        self._interruptor(f, no_mostrar, "No volver a mostrar al abrir").pack(anchor="w")
        botones = tk.Frame(f, background=BLANCO)
        botones.pack(fill="x", pady=(p(18), 0))

        def cerrar(ir_a_configuracion=False):
            if no_mostrar.get() != self.cfg.ocultar_presentacion:
                self.cfg.ocultar_presentacion = no_mostrar.get()
                try:
                    config.guardar(self.cfg)
                except OSError:
                    pass
            d.destroy()
            if ir_a_configuracion:
                self._configurar()

        Boton(botones, self.pintor, "Ir a Configuración", lambda: cerrar(True), clase="principal").pack(side="right")
        Boton(botones, self.pintor, "Empezar", cerrar).pack(side="right", padx=(0, p(8)))
        d.protocol("WM_DELETE_WINDOW", cerrar)
        d.update_idletasks()
        x = self.winfo_rootx() + max(0, (self.winfo_width() - d.winfo_width()) // 2)
        y = self.winfo_rooty() + max(0, (self.winfo_height() - d.winfo_height()) // 3)
        d.geometry(f"+{x}+{y}")
        d.grab_set()

    # --- proceso -------------------------------------------------------------------
    def _volcar_config(self):
        self.cfg.modo = self.modo.get()
        self.cfg.capa_texto = self.capa.get()
        if not self.salida.get().strip():
            self.salida.set(str(config.carpeta_por_defecto()))
        self.cfg.carpeta_salida = self.salida.get().strip()
        self.cfg.indice_en_pdf = self.indice.get()
        self.cfg.separar = self.separar.get()
        self.cfg.unir_todo = self.unir.get()
        from .clasificar import AMBITOS

        self.cfg.tipo_expediente = next((k for k, v in AMBITOS.items() if v == self.ambito.get()), "auto")
        self.cfg.enderezar = self.enderezar.get()
        self.cfg.quitar_blancas = self.blancas.get()
        self.cfg.exportar_rag = self.rag.get()
        self.cfg.navegacion = self.nav.get()
        for clave, var in self.contenido.items():
            setattr(self.cfg, f"indice_{clave}", var.get())
        self.cfg.palabras_clave = self.palabras.get("1.0", "end").strip() + "\n"
        try:
            config.guardar(self.cfg)
        except OSError:
            pass

    def _procesar(self):
        entradas = list(self.lista.get(0, "end"))
        if not entradas:
            messagebox.showinfo("Marcadores PDF", "Añada primero algún PDF, imagen o carpeta.")
            return
        self._volcar_config()
        self.licencia = licencia.estado(self.cfg.licencia_testigo)
        if not self.licencia.puede_procesar:
            messagebox.showwarning("Marcadores PDF", f"{self.licencia.rotulo}. {self.licencia.detalle}")
            self._resumir()
            self._ir_a_licencia()
            return
        if self.cfg.modo == "gemini":
            if self.cfg.motor_clasificacion == "claude" and not self.cfg.claude_clave:
                messagebox.showwarning("Marcadores PDF", "Falta la clave de API de Anthropic.")
                self._configurar()
                return
            if self.cfg.motor_clasificacion in ("mistral", "claude"):
                if not self.cfg.mistral_clave:
                    messagebox.showwarning("Marcadores PDF", "Falta la clave de API de Mistral "
                                           "(lee las páginas escaneadas).")
                    self._configurar()
                    return
            elif not (self.cfg.proyecto and self.cfg.credenciales):
                messagebox.showwarning("Marcadores PDF", "Falta configurar Vertex AI: proyecto y clave de la cuenta de servicio.")
                self._configurar()
                return
        self.cancelar.clear()
        self.b_procesar.activar(False)
        self.b_actualizar.activar(False)
        # vista «Procesando»
        self.pr_etiqueta.configure(text="PROCESANDO")
        self.pr_titulo.configure(text="Preparando…")
        self.pr_detalle.configure(text=("Lectura con IA · " + self.opciones_modo["gemini"][2].cget("text"))
                                  if self.cfg.modo == "gemini" else "Lectura básica, sin IA")
        self._fraccion = 0.0
        self.pr_pct.configure(text="0 %")
        self._filas_proceso([])
        for b in (self.b_volver, self.b_abrir_fin):
            b.pack_forget()
        self.b_cancelar.pack(side="right")
        self._ver_proceso(True)
        salida = self.cfg.carpeta_salida
        self.hilo = threading.Thread(target=self._trabajo, args=(entradas, salida), daemon=True)
        self.hilo.start()

    def _trabajo(self, entradas, salida):
        try:
            expedientes = recoger(entradas, self.cfg.unir_todo)
            if not expedientes:
                raise ValueError("No se han encontrado PDF ni imágenes en lo seleccionado.")
            self.cola.put(("lista", [e.nombre for e in expedientes], 0.0))
            analizador = crear_analizador(self.cfg)

            plantillas = Plantillas.desde(self.cfg)

            def destino_de(exp):
                self.ultima_salida = carpeta_de(exp, salida, plantillas)
                return self.ultima_salida

            fallidos = procesar_varios(
                expedientes, destino_de, analizador, cancelar=self.cancelar,
                aviso=lambda m, f: self.cola.put(("aviso", m, f)),
                palabras_clave=self.cfg.palabras_clave, capa_texto=self.cfg.capa_texto,
                separar=self.cfg.separar, indice_en_pdf=self.cfg.indice_en_pdf, plantillas=plantillas,
                tipo_expediente=self.cfg.tipo_expediente, enderezar=self.cfg.enderezar,
                quitar_blancas=self.cfg.quitar_blancas, exportar_rag=self.cfg.exportar_rag,
                navegacion=self.cfg.navegacion,
                contenido_indice=tuple(k for k in self.contenido if getattr(self.cfg, f"indice_{k}")))
            carpetas = [carpeta_de(e, salida, plantillas) for e in expedientes]
            if len(carpetas) > 1:
                # con varios expedientes, «Abrir carpeta» abre la que los contiene a todos
                self.ultima_salida = carpetas[0].parent
            if fallidos:
                self.cola.put(("error", resumen_fallos(fallidos, len(expedientes)), 1.0))
            elif len(carpetas) > 1:
                lista = "\n".join(f"  · {c.name}" for c in carpetas)
                self.cola.put(("fin", f"Proceso completado: {len(carpetas)} expedientes, cada uno en su "
                                      f"carpeta dentro de {carpetas[0].parent}:\n{lista}", 1.0))
            else:
                self.cola.put(("fin", "Proceso completado.", 1.0))
        except Cancelado:
            self.cola.put(("fin", "Cancelado. Lo ya leído queda guardado y no se volverá a consultar.", 0.0))
        except Exception as e:
            registro.log.exception("El proceso ha fallado")
            from .ocr_gemini import CuotaAgotada, ModeloNoDisponible

            texto = str(e) if isinstance(e, (CuotaAgotada, ModeloNoDisponible, ValueError)) else f"{type(e).__name__}: {e}"
            self.cola.put(("error", texto, 0.0))

    def _escribir(self, texto):
        self.registro["state"] = "normal"
        self.registro.insert("end", texto + "\n")
        self.registro.see("end")
        self.registro["state"] = "disabled"

    def _atender_cola(self):
        try:
            while True:
                clase, mensaje, fraccion = self.cola.get_nowait()
                if clase == "version":
                    self._ofrecer_actualizacion(mensaje, fraccion)
                    continue
                if clase == "avance":
                    self._estado_pie(f"Descargando la actualización… {round(fraccion * 100)} %", None)
                    continue
                if clase == "instalada":
                    self._reiniciar_tras_actualizar(mensaje)
                    continue
                if clase == "licencia":
                    self.licencia = mensaje
                    self._pintar_licencia()
                    self._resumir()
                    continue
                if clase == "lista":
                    self._filas_proceso(mensaje)
                    self._avance("", 0.0)
                    continue
                self._escribir(("ERROR: " if clase == "error" else "") + mensaje)
                if clase == "aviso":
                    self._avance(mensaje, fraccion)
                    self._estado_pie(mensaje, None)
                if clase in ("fin", "error"):
                    self.b_actualizar.activar(True)
                    if self.ultima_salida and self.ultima_salida.exists():
                        self.b_abrir.activar(True)
                    self._fin_proceso(clase, mensaje)
                    self._resumir()
                    self._estado_pie(mensaje, "error" if clase == "error" else "bien")
                    if clase == "error":
                        messagebox.showerror("Marcadores PDF", mensaje)
        except queue.Empty:
            pass
        self.after(150, self._atender_cola)

    # --- actualizaciones ---------------------------------------------------------
    def _buscar_actualizacion(self, avisar_siempre: bool):
        if self.hilo and self.hilo.is_alive():
            if avisar_siempre:
                messagebox.showinfo("Marcadores PDF", "Espere a que termine el proceso en curso.")
            return
        self.b_actualizar.activar(False)
        texto = f"Comprobando si hay una versión nueva (tiene la {__version__})…"
        self._escribir(texto)
        self._estado_pie(texto, None)

        def tarea():
            try:
                self.cola.put(("version", actualizar.buscar(self.cfg.token_github), avisar_siempre))
            except actualizar.ErrorActualizacion as e:
                self.cola.put(("version", e, avisar_siempre))

        threading.Thread(target=tarea, daemon=True).start()

    def _ofrecer_actualizacion(self, resultado, avisar_siempre: bool):
        self.b_actualizar.activar(True)
        if isinstance(resultado, Exception):
            registro.log.warning("No se pudo comprobar si hay actualizaciones: %s", resultado)
            texto = (f"No se ha podido comprobar si hay una versión nueva ({resultado}). "
                     "Puede intentarlo con «Buscar actualizaciones».")
            self._escribir(texto)
            self._estado_pie(texto, "aviso")
            if avisar_siempre:
                messagebox.showwarning("Actualizaciones", str(resultado))
            elif not self._reintento_actualizacion:  # al arrancar, a veces la red aún no está lista
                self._reintento_actualizacion = True
                self.after(60_000, lambda: self._buscar_actualizacion(avisar_siempre=False))
            return
        if resultado is None:
            registro.log.info("Actualizaciones: tiene la última versión (%s)", __version__)
            texto = f"Tiene la última versión de Marcadores PDF ({__version__})."
            self._escribir(texto)
            self._estado_pie(texto)
            if avisar_siempre:
                messagebox.showinfo("Actualizaciones", f"Ya tiene la última versión ({__version__}).")
            return
        registro.log.info("Actualizaciones: hay una versión nueva (%s; tiene la %s)", resultado.numero, __version__)
        self._estado_pie(f"Hay una versión nueva: {resultado.numero}.", "aviso")
        if not actualizar.es_ejecutable():
            messagebox.showinfo("Actualizaciones", f"Hay una versión nueva: {resultado.numero}.")
            return
        if not messagebox.askyesno(
                "Actualización disponible",
                f"Hay una versión nueva de Marcadores PDF: {resultado.numero} (tiene la {__version__}).\n\n"
                "¿Descargarla e instalarla ahora? El programa se reiniciará."):
            return
        self.b_procesar.activar(False)
        self.b_actualizar.activar(False)
        self._escribir(f"Descargando la versión {resultado.numero}…")

        def tarea():
            try:
                nuevo = actualizar.instalar(resultado, self.cfg.token_github,
                                            progreso=lambda f: self.cola.put(("avance", "", f)))
                self.cola.put(("instalada", (resultado.numero, nuevo), 1.0))
            except actualizar.ErrorActualizacion as e:
                registro.log.warning("Actualización fallida: %s", e)
                self.cola.put(("error", str(e), 0.0))

        threading.Thread(target=tarea, daemon=True).start()

    def _reiniciar_tras_actualizar(self, datos):
        numero, nuevo = datos
        self._volcar_config()
        try:
            actualizar.reiniciar(nuevo)
        except OSError:
            messagebox.showinfo("Actualizaciones",
                                f"Versión {numero} descargada. Cierre el programa y abra «{nuevo.name}».")
            return
        self.destroy()

    def _ver_registro(self):
        ruta = registro.ruta_registro()
        if not ruta.exists():
            messagebox.showinfo("Marcadores PDF", "Todavía no hay nada registrado.")
        elif hasattr(os, "startfile"):
            os.startfile(ruta)  # noqa: S606 (solo Windows)

    def report_callback_exception(self, tipo, valor, traza):
        """Errores de la propia ventana: se anotan y se muestran, en vez de perderse."""
        registro.log.error("Error en la ventana", exc_info=(tipo, valor, traza))
        try:
            messagebox.showerror("Marcadores PDF", f"Se ha producido un error inesperado:\n{valor}\n\n"
                                                   "El detalle está en la pestaña «Registro».")
        except Exception:
            pass

    def _abrir(self):
        if self.ultima_salida and hasattr(os, "startfile"):
            os.startfile(self.ultima_salida)  # noqa: S606 (solo Windows)

    def _cerrar(self):
        self._volcar_config()
        self.destroy()


def main(recien_actualizado: bool = False):
    registro.iniciar()
    if not instancia.primera("Marcadores PDF", esperar=20.0 if recien_actualizado else 0.0):
        registro.log.info("Ya había una ventana abierta: se muestra esa en vez de abrir otra")
        return
    Ventana().mainloop()
