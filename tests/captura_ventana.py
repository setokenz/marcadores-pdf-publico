"""Abre la ventana y guarda capturas (se usa en la compilación para revisar el diseño).

Capturas: presentación, Procesar con tres expedientes de ejemplo, la vista «Procesando»
simulada, Configuración y el diálogo de nombres. No procesa nada ni toca la configuración real.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import ImageGrab  # noqa: E402

from marcadores import config  # noqa: E402
from marcadores.gui import Ventana  # noqa: E402

destino = Path(sys.argv[1])
config.guardar = lambda cfg: None  # no tocar la configuración real
v = Ventana()
for nombre in (r"C:\Expedientes\Tomo I - Diligencias previas.pdf", r"C:\Expedientes\Tomo II - Instrucción.pdf",
               r"C:\Expedientes\Escritos de parte.pdf"):
    v.lista.insert("end", nombre)
v.attributes("-topmost", True)


def capturar(ruta, ventana):
    ventana.update()
    x, y = ventana.winfo_rootx(), ventana.winfo_rooty()
    caja = (x, y, x + ventana.winfo_width(), y + ventana.winfo_height())
    ImageGrab.grab(bbox=caja, all_screens=True).save(ruta)


def con_sufijo(sufijo):
    return destino.with_name(destino.stem + sufijo + ".png")


def paso0():
    dialogos = [w for w in v.winfo_children() if w.winfo_class() == "Toplevel"]
    if dialogos:  # pantalla de presentación (sin claves configuradas)
        capturar(con_sufijo("-presentacion"), dialogos[-1])
        dialogos[-1].destroy()
    v.after(500, paso1)


def paso1():
    capturar(destino, v)  # paso 1 del asistente: documentos
    v._ir_a_paso(2)
    capturar(con_sufijo("-paso2"), v)
    v._ir_a_paso(3)
    capturar(con_sufijo("-paso3"), v)
    v._ir_a_paso(1)
    # vista «Procesando» simulada: sin hilo ni consultas
    v._filas_proceso(["Tomo I - Diligencias previas", "Tomo II - Instrucción", "Escritos de parte"])
    v.pr_detalle.configure(text="Lectura con IA · Mistral lee, Claude clasifica")
    v._ver_proceso(True)
    v._avance("OCR con Mistral: páginas 41-60 (40 de 312 leídas)…", 0.36)
    v.after(800, paso2)


def paso2():
    capturar(con_sufijo("-procesando"), v)
    v._ver_proceso(False)
    v._configurar()  # muestra la pestaña «Configuración»
    v.after(1200, paso3)


def paso3():
    capturar(con_sufijo("-config"), v)
    v._ir("procesar")
    v._nombres()
    v.after(1200, paso4)


def paso4():
    dialogo = [w for w in v.winfo_children() if w.winfo_class() == "Toplevel"][-1]
    capturar(con_sufijo("-nombres"), dialogo)
    v.destroy()


v.after(1500, paso0)
v.mainloop()
