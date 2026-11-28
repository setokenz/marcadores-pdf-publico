import json
import sys
from pathlib import Path

import pymupdf as fitz
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from marcadores.clasificador_local import AnalizadorBasico, buscar_fecha
from marcadores.clasificar import leer_palabras_clave
from marcadores.entrada import recoger
from marcadores.ocr_gemini import AnalizadorGemini
from marcadores.proceso import procesar, procesar_varios

PAGINAS = [
    "AL JUZGADO DE PRIMERA INSTANCIA\nDon Fulano, procurador, formula DEMANDA de juicio ordinario.",
    "SUPLICO AL JUZGADO que admita la demanda.\nEn Valencia, a 3 de enero de 2024.",
    "DECRETO\nLetrado de la Administración de Justicia.\nEn Valencia, a 15 de enero de 2024.\nAdmito a trámite la demanda.",
    "DILIGENCIA DE ORDENACIÓN\nEn Valencia, a 2 de febrero de 2024. Se tiene por contestada.\n"
    "Se concede a la parte actora el plazo de cinco días para alegaciones.",
    "ACTA DE DECLARACIÓN\nTestigo: Mengano. En Valencia, a 10/03/2024.",
    "Continúa la declaración del testigo.",
    "AUTO\nEn Valencia, a 20 de marzo de 2024.\nSe acuerda la medida cautelar solicitada.\n"
    "Se señala la audiencia previa para el día 12 de junio de 2024 a las 10:30 horas.\n"
    "Contra este auto cabe recurso de reposición en el plazo de cinco días.",
    "SENTENCIA\nEn Valencia, a 5 de mayo de 2024. FALLO: estimo la demanda con condena en costas.",
]


def crear_pdf(ruta: Path, con_texto: bool = True):
    pdf = fitz.open()
    for texto in PAGINAS:
        pagina = pdf.new_page()
        if con_texto:
            pagina.insert_textbox(pagina.rect + (50, 50, -50, -50), texto, fontsize=12)
    pdf.save(ruta)


def test_fecha():
    assert buscar_fecha("En Valencia, a 3 de enero de 2024") == "2024-01-03"
    assert buscar_fecha("fecha 10/03/2024") == "2024-03-10"


def test_palabras_clave():
    r = leer_palabras_clave("# c\nCostas: tasación de costas, condena en costas\nCautelares: medida cautelar")
    assert r == {"Costas": ["tasación de costas", "condena en costas"], "Cautelares": ["medida cautelar"]}


def test_modo_basico(tmp_path):
    origen = tmp_path / "expediente.pdf"
    crear_pdf(origen)
    exp = recoger([origen])[0]
    salida = tmp_path / "salida"
    docs = procesar(exp, salida, AnalizadorBasico(),
                    "Costas: condena en costas\nCautelares: medida cautelar", separar=True)
    assert [d.tipo for d in docs] == ["escrito_parte", "decreto", "diligencia_ordenacion",
                                      "declaracion", "auto", "sentencia"]
    assert [(d.pagina_inicio, d.pagina_fin) for d in docs] == [(1, 2), (3, 3), (4, 4), (5, 6), (7, 7), (8, 8)]
    assert docs[0].fecha == "2024-01-03"  # fecha tomada de la última hoja del escrito
    assert docs[4].etiquetas == ["Cautelares"] and docs[5].etiquetas == ["Costas"]
    with fitz.open(salida / "expediente - marcadores.pdf") as pdf:
        toc = pdf.get_toc(simple=False)
        n = pdf.page_count - 8  # hojas de índice delante de las 8 del expediente
        assert n >= 1
        assert pdf[0].get_label() == "Índice i" and pdf[n].get_label() == "1"
        enlaces = [e for i in range(n) for e in pdf[i].get_links()]
        # cada línea del índice enlaza con su página: la sentencia (pág. 8) está en la hoja 8 + n
        assert {e["page"] - n for e in enlaces} == {0, 2, 3, 4, 6, 7}
        texto = "".join(pdf[i].get_text() for i in range(n))
        assert "Sentencias (1)" in texto and "Autos (1)" in texto and "Decretos (1)" in texto
        assert "Señalamientos (1)" in texto and "12/06/2024 10:30" in texto
        assert "Plazos (1)" in texto and "[Costas]" in texto
        # las filas con palabras clave llevan fondo amarillo en el índice
        assert any(abs(d["fill"][2] - 0.6) < 0.01 for i in range(n)
                   for d in pdf[i].get_drawings() if d.get("fill"))
        # la palabra clave queda resaltada y el marcador apunta a su altura en la página
        assert [a.type[1] for a in pdf[6 + n].annots() if a.type[1] != "FreeText"] == ["Highlight"]
        costas = next(t for t in toc if t[1].startswith("pág. 8 · «condena en costas»"))
        assert costas[2] == 8 + n and costas[3]["to"].y > 30
    titulos = [t[1] for t in toc if t[0] == 1]
    assert titulos[1].startswith("Cronología procesal (")  # hay resoluciones: judicial
    assert titulos[2:] == ["Documentos (orden del escaneo)", "Resoluciones (4)", "Declaraciones (1)",
                       "Escritos de parte (1)", "Señalamientos (1)", "Plazos (1)", "Palabras clave"]
    nivel2 = [t[1] for t in toc if t[0] == 2]
    i = nivel2.index("Sentencias (1)")
    assert nivel2[i:i + 4] == ["Sentencias (1)", "Autos (1)", "Decretos (1)",
                               "Diligencias de ordenación (1)"]
    # el plazo del recurso de reposición (recurso intermedio) no se recoge
    assert [x["plazo"] for d in docs for x in d.plazos] == ["cinco dias"]
    assert docs[2].plazos[0]["pagina"] == 4 and docs[4].senalamientos[0]["fecha"] == "2024-06-12"
    from openpyxl import load_workbook
    libro = load_workbook(salida / "expediente - índice.xlsx")
    assert libro.sheetnames == ["Índice", "Cronología", "Señalamientos", "Plazos"] and libro["Plazos"].max_row == 2
    assert not (salida / "expediente - índice.docx").exists()  # el índice en Word se retiró
    assert (salida / "expediente - índice.xlsx").exists()
    assert len(list((salida / "documentos").glob("*.pdf"))) == 6


class ClienteFalso:
    """Imita a Gemini: responde según las páginas pedidas y falla una vez con un lote."""

    def __init__(self):
        self.llamadas = []
        self.models = self

    def generate_content(self, model, contents, config):
        nums = [int(p.text.split()[1]) for p in contents if p.text and p.text.startswith("PÁGINA")]
        self.llamadas.append(nums)
        if len(nums) == 4:  # respuesta rota: obliga a partir el lote
            return type("R", (), {"text": "{no es json"})()
        basico = AnalizadorBasico()
        with fitz.open() as pdf:
            for t in PAGINAS:
                pg = pdf.new_page()
                pg.insert_textbox(pg.rect + (50, 50, -50, -50), t, fontsize=12)
            infos = basico.analizar_lote(pdf, [n - 1 for n in nums])
        datos = [{"pagina": i.pagina,
                  "lineas": [{"t": t, "c": [100 + 40 * n, 80, 130 + 40 * n, 900]}
                             for n, t in enumerate(i.texto.splitlines()) if t.strip()],
                  "inicia_documento": i.inicia_documento,
                  "tipo": i.tipo, "titulo": i.titulo, "fecha": i.fecha, "autor": "",
                  "subtipo": "Demanda" if i.tipo == "escrito_parte" else i.subtipo,
                  "parte": "Fulano S.L." if i.tipo == "escrito_parte" else "",
                  "posicion": "demandante" if i.tipo == "escrito_parte" else "",
                  "plazos": i.plazos, "senalamientos": i.senalamientos} for i in infos]
        return type("R", (), {"text": json.dumps(datos)})()


def test_gemini_simulado_con_imagenes_y_cache(tmp_path):
    origen = tmp_path / "escaneado.pdf"
    crear_pdf(origen, con_texto=False)  # páginas sin texto, como un escaneo
    exp = recoger([origen])[0]
    cliente = ClienteFalso()
    analizador = AnalizadorGemini("p", "europe-southwest1", "gemini-x", cliente=cliente,
                                  paginas_por_lote=4, paralelo=1)
    docs = procesar(exp, tmp_path / "s", analizador)
    assert len(docs) == 6
    assert docs[0].emisor == "Fulano S.L. (demandante)" and docs[0].clase == "Demanda"
    # solo se parte el lote que falla; el siguiente vuelve a intentarse con el tamaño normal
    assert cliente.llamadas == [[1, 2, 3, 4], [1, 2], [3, 4], [5, 6, 7, 8], [5, 6], [7, 8]]
    with fitz.open(tmp_path / "s" / "escaneado - marcadores.pdf") as pdf:
        ultima = pdf[pdf.page_count - 1]
        assert "SENTENCIA" in ultima.get_text()  # capa de texto buscable añadida
        # cada línea queda colocada sobre su recuadro: 10 % y 14 % de la altura, 8 % del ancho
        zonas = ultima.search_for("SENTENCIA")
        assert zonas and abs(zonas[0].x0 - 0.08 * ultima.rect.width) < 3
        assert 0.08 * ultima.rect.height < zonas[0].y0 < 0.13 * ultima.rect.height
        assert ultima.search_for("FALLO")[0].y0 > zonas[0].y1
    # segunda pasada: todo sale de la caché, sin nuevas consultas
    antes = len(cliente.llamadas)
    procesar(exp, tmp_path / "s", analizador)
    assert len(cliente.llamadas) == antes


def test_imagenes_de_carpeta(tmp_path):
    carpeta = tmp_path / "escaneos"
    carpeta.mkdir()
    for n in (1, 2, 10):
        pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 200, 280))
        pix.clear_with(255)
        pix.save(carpeta / f"hoja{n}.png")
    exps = recoger([carpeta])
    assert len(exps) == 1 and [r.name for r in exps[0].rutas] == ["hoja1.png", "hoja2.png", "hoja10.png"]
    docs = procesar(exps[0], tmp_path / "s", AnalizadorBasico())
    assert len(docs) == 1 and docs[0].pagina_fin == 3


def test_buscar_combinacion():
    from marcadores.ocr_gemini import buscar_combinacion

    class Falso:
        def __init__(self, u, m):
            self.u, self.m = u, m

        def probar(self):
            if (self.u, self.m) != ("eu", "gemini-b"):
                e = Exception("no existe")
                e.code = 404
                raise e

    bueno, informe = buscar_combinacion("p", "c", "europe-southwest1", "gemini-a",
                                        ["eu", "europe-west1"], ["gemini-b"], fabrica=Falso)
    assert bueno == ("eu", "gemini-b")
    assert informe[0].startswith("✘ gemini-a en europe-southwest1") and informe[-1].startswith("✔")


def test_config_migra_modelo_retirado(tmp_path, monkeypatch):
    from marcadores import config

    monkeypatch.setenv("APPDATA", str(tmp_path))
    config.guardar(config.Config(modelo="gemini-2.5-flash", proyecto="x"))
    cfg = config.cargar()
    assert cfg.modelo == config.MODELO_POR_DEFECTO and cfg.proyecto == "x"
    # quien tenía gemini-3.8-flash (se quedaba parado) pasa al 3.5 una sola vez
    config.ruta_config().write_text('{"modelo": "gemini-3.8-flash"}', encoding="utf-8")
    assert config.cargar().modelo == "gemini-3.5-flash"
    config.ruta_config().write_text('{"modelo": "gemini-3.8-flash", "usar_texto_existente": true}',
                                    encoding="utf-8")
    assert config.cargar().modelo == "gemini-3.8-flash"  # si luego lo elige a mano, se respeta
    # configuración de una versión anterior: 6 páginas por consulta pasa al nuevo valor
    config.ruta_config().write_text('{"paginas_por_lote": 6, "proyecto": "x"}', encoding="utf-8")
    assert config.cargar().paginas_por_lote == config.PAGINAS_POR_LOTE
    assert config.carpeta_por_defecto().name == "Marcadores PDF"


def test_actualizacion(tmp_path, monkeypatch):
    import io
    import urllib.request

    from marcadores import actualizar

    assert actualizar.es_mas_nueva("v0.4.12", "0.4.9") and not actualizar.es_mas_nueva("v0.4.9", "0.4.9")
    assert not actualizar.es_mas_nueva("ultima-version", "0.1.0")

    contenido = b"MZ" + b"x" * 500
    publicado = {"tag_name": "v99.0.1", "assets": [{
        "name": "MarcadoresPDF.exe", "size": len(contenido),
        "url": "https://api.github.com/repos/setokenz/marcadores-pdf/releases/assets/1"}]}

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            self.close()

    import urllib.error

    def solo_privado(pet, timeout):  # el repositorio público aún no existe
        if "marcadores-pdf-publico" in pet.full_url:
            raise urllib.error.HTTPError(pet.full_url, 404, "Not Found", {}, None)
        return Resp(json.dumps(publicado).encode())

    version = actualizar.buscar(abrir=solo_privado)
    assert version.numero == "99.0.1" and version.privado
    publicado["tag_name"] = "v0.0.1"
    assert actualizar.buscar(abrir=solo_privado) is None

    class Abridor:
        def open(self, pet, timeout):
            assert pet.get_header("Authorization") == "Bearer secreto"
            return Resp(contenido)

    monkeypatch.setattr(urllib.request, "build_opener", lambda *a: Abridor())
    exe = tmp_path / "MarcadoresPDF.exe"
    exe.write_bytes(b"MZ viejo")
    assert actualizar.instalar(version, "secreto", exe=exe) == exe
    assert exe.read_bytes() == contenido
    assert (tmp_path / "MarcadoresPDF.old.exe").read_bytes() == b"MZ viejo"

    # ejecutable publicado con la versión en el nombre: se guarda aparte y se borra el anterior
    publicado["tag_name"] = "v99.0.2"
    publicado["assets"].append({"name": "MarcadoresPDF-99.0.2.exe", "size": len(contenido),
                                "url": "https://api.github.com/repos/setokenz/marcadores-pdf/releases/assets/2"})
    v2 = actualizar.buscar(abrir=solo_privado)
    assert v2.nombre == "MarcadoresPDF-99.0.2.exe" and v2.url.endswith("/2")
    nuevo = actualizar.instalar(v2, "secreto", exe=exe)
    assert nuevo.name == "MarcadoresPDF-99.0.2.exe" and nuevo.read_bytes() == contenido and exe.exists()
    assert actualizar.borrar_anterior(str(exe), exe=nuevo, intentos=1) and not exe.exists()
    ajeno = tmp_path / "otro.exe"
    ajeno.write_bytes(b"x")
    assert not actualizar.borrar_anterior(str(ajeno), exe=nuevo, intentos=1) and ajeno.exists()
    exe.write_bytes(contenido)

    monkeypatch.setattr("time.sleep", lambda s: None)
    # descarga dañada: el programa actual no se toca
    version.tamano += 1
    try:
        actualizar.instalar(version, "secreto", exe=exe)
        assert False
    except actualizar.ErrorActualizacion:
        pass
    assert exe.read_bytes() == contenido and not (tmp_path / "MarcadoresPDF.new.exe").exists()


def test_fallo_de_conexion_no_repite(monkeypatch):
    from marcadores import ocr_gemini

    intentos = []

    class SinRed:
        def __init__(self, u, m):
            intentos.append((u, m))

        def probar(self):
            raise OSError("('Connection aborted.', PermissionError(13, 'Permission denied'))")

    monkeypatch.setattr(ocr_gemini, "diagnostico_conexion", lambda: ["diagnóstico"])
    bueno, informe = ocr_gemini.buscar_combinacion("p", "c", "eu", "gemini-a", ["europe-west1"],
                                                   ["gemini-b"], fabrica=SinRed)
    assert bueno is None and len(intentos) == 1  # no insiste con otras 20 combinaciones
    assert "diagnóstico" in informe and "antivirus" in informe[-1]


def test_icono_presente():
    raiz = Path(__file__).resolve().parent.parent
    datos = (raiz / "assets" / "icono.ico").read_bytes()
    assert datos[:4] == b"\x00\x00\x01\x00" and datos[4] == 9  # archivo .ico con 9 tamaños
    assert "--icon assets/icono.ico" in (raiz / ".github/workflows/compilar.yml").read_text(encoding="utf-8")


def test_bloqueos_reintentos_y_cancelacion(tmp_path, monkeypatch):
    import threading

    from marcadores import ocr_gemini
    from marcadores.proceso import Cancelado

    monkeypatch.setattr(ocr_gemini.time, "sleep", lambda s: None)
    origen = tmp_path / "e.pdf"
    crear_pdf(origen, con_texto=False)
    exp = recoger([origen])[0]

    class ReadTimeout(Exception):
        pass

    class Lento(ClienteFalso):
        """No responde a lotes de más de 2 páginas, rechaza el ajuste de razonamiento una vez
        y falla una vez por saturación."""

        def __init__(self):
            super().__init__()
            self.fallos = {"razonamiento": 1, "saturado": 1}

        def generate_content(self, model, contents, config):
            if config.thinking_config is not None and self.fallos["razonamiento"]:
                self.fallos["razonamiento"] -= 1
                e = Exception("thinking_level is not supported")
                e.code = 400
                raise e
            nums = [int(p.text.split()[1]) for p in contents if p.text and p.text.startswith("PÁGINA")]
            if len(nums) > 2:
                self.llamadas.append(nums)
                raise ReadTimeout("The read operation timed out")
            if self.fallos["saturado"]:
                self.fallos["saturado"] -= 1
                e = Exception("resource exhausted")
                e.code = 429
                raise e
            return super().generate_content(model, contents, config)

    cliente = Lento()
    analizador = AnalizadorGemini("p", "eu", "gemini-x", cliente=cliente, paginas_por_lote=8,
                                  paralelo=1)
    avisos = []
    docs = procesar(exp, tmp_path / "s", analizador, aviso=lambda m, f: avisos.append(m))
    assert len(docs) == 6
    assert cliente.llamadas == [[1, 2, 3, 4, 5, 6, 7, 8], [1, 2, 3, 4], [1, 2], [3, 4],
                                [5, 6, 7, 8], [5, 6], [7, 8]]
    assert any("Se repite en lotes de 4" in a for a in avisos)
    assert any("limita el uso por minuto (429). Pausa de 15 s y se sigue (reintento 1 de 7)" in a
               for a in avisos)

    # Cancelar surte efecto antes de la siguiente consulta y conserva lo ya leído
    cancelar = threading.Event()
    cliente2 = ClienteFalso()
    analizador2 = AnalizadorGemini("p", "eu", "gemini-y", cliente=cliente2, paginas_por_lote=2,
                                   paralelo=1)

    def aviso(m, f):
        if "páginas 3-4" in m:
            cancelar.set()

    try:
        procesar(exp, tmp_path / "s2", analizador2, aviso=aviso, cancelar=cancelar)
        assert False
    except Cancelado:
        pass
    assert cliente2.llamadas == [[1, 2]]
    datos = json.loads((tmp_path / "s2" / "e.ocr.json").read_text(encoding="utf-8"))
    assert [p["pagina"] for p in datos["paginas"]] == [1, 2]


def test_registro_de_errores(tmp_path, monkeypatch):
    import logging

    from marcadores import registro

    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr(registro.log, "handlers", [])
    registro.iniciar()
    try:
        raise ValueError("fallo de prueba")
    except ValueError:
        registro.log.exception("El proceso ha fallado")
    for g in registro.log.handlers:
        g.flush()
    texto = registro.ruta_registro().read_text(encoding="utf-8")
    assert "Marcadores PDF" in texto and "ValueError: fallo de prueba" in texto and "Traceback" in texto
    for g in list(registro.log.handlers):
        g.close()
        registro.log.removeHandler(g)
    logging.getLogger("marcadores").setLevel(logging.NOTSET)


def test_acceso_directo(tmp_path):
    from marcadores import acceso

    exe = tmp_path / "MarcadoresPDF-9.9.9.exe"
    exe.write_bytes(b"MZ")
    if sys.platform != "win32":
        assert acceso.crear(exe, forzar=True, carpeta=tmp_path) is False
        return
    enlace = tmp_path / acceso.NOMBRE
    assert acceso.crear(exe, forzar=False, carpeta=tmp_path) and not enlace.exists()  # no lo impone
    assert acceso.crear(exe, forzar=True, carpeta=tmp_path) and enlace.exists()
    assert exe.name.encode("utf-16-le") in enlace.read_bytes() or exe.name.encode() in enlace.read_bytes()


class ClientePar:
    """Imita a Gemini para pruebas con varios hilos: no toca PyMuPDF dentro de las consultas."""

    def __init__(self, retardo=0.05):
        basico = AnalizadorBasico()
        with fitz.open() as pdf:
            for t in PAGINAS:
                pg = pdf.new_page()
                pg.insert_textbox(pg.rect + (50, 50, -50, -50), t, fontsize=12)
            self.infos = {i.pagina: i for i in basico.analizar_lote(pdf, list(range(len(PAGINAS))))}
        self.models = self
        self.retardo = retardo
        self.configs = []
        self.contenidos = []
        self.llamadas = []
        self.activas = 0
        self.max_activas = 0
        self.cerrojo = __import__("threading").Lock()
        self.accion = None  # función opcional (nums, config) que puede lanzar o bloquear

    def generate_content(self, model, contents, config):
        import time

        nums = [int(p.text.split()[1]) for p in contents if p.text and p.text.startswith("PÁGINA")]
        with self.cerrojo:
            self.activas += 1
            self.max_activas = max(self.max_activas, self.activas)
            self.llamadas.append(nums)
            self.configs.append(config)
            self.contenidos.append(contents)
        try:
            time.sleep(self.retardo)
            if self.accion:
                respuesta = self.accion(nums, config)
                if respuesta is not None:
                    return respuesta
            datos = [{"pagina": n,
                      "lineas": [{"t": t, "c": [100, 80, 130, 900]}
                                 for t in self.infos[n].texto.splitlines() if t.strip()],
                      "inicia_documento": self.infos[n].inicia_documento, "tipo": self.infos[n].tipo,
                      "titulo": self.infos[n].titulo, "fecha": self.infos[n].fecha, "autor": "",
                      "subtipo": self.infos[n].subtipo, "parte": "", "posicion": "",
                      "plazos": [], "senalamientos": []} for n in nums]
            return type("R", (), {"text": json.dumps(datos)})()
        finally:
            with self.cerrojo:
                self.activas -= 1


def _expediente_escaneado(tmp_path, nombre="p.pdf"):
    origen = tmp_path / nombre
    crear_pdf(origen, con_texto=False)
    return recoger([origen])[0]


def test_lectura_en_paralelo(tmp_path):
    exp = _expediente_escaneado(tmp_path)
    cliente = ClientePar(retardo=0.6)  # más que lo que tarda en preparar las imágenes del lote siguiente
    avisos = []
    paralelo = AnalizadorGemini("p", "eu", "gemini-x", cliente=cliente, paginas_por_lote=2, paralelo=4)
    docs = procesar(exp, tmp_path / "par", paralelo, aviso=lambda m, f: avisos.append(m))
    assert cliente.max_activas >= 2  # hubo consultas simultáneas
    assert sorted(n for l in cliente.llamadas for n in l) == list(range(1, 9))
    # mismo resultado que leyendo en serie
    serie = AnalizadorGemini("p", "eu", "gemini-x", cliente=ClientePar(0), paginas_por_lote=2, paralelo=1)
    docs_serie = procesar(exp, tmp_path / "serie", serie)
    assert [(d.tipo, d.pagina_inicio, d.pagina_fin) for d in docs] == [
        (d.tipo, d.pagina_inicio, d.pagina_fin) for d in docs_serie]
    # el modelo recibe el ajuste de velocidad y la resolución media
    c = cliente.configs[0]
    assert c.thinking_config.thinking_level == "LOW"  # MINIMAL lo rechaza gemini-3.8-flash
    assert str(c.media_resolution).endswith("MEDIA_RESOLUTION_MEDIUM")
    datos = json.loads((tmp_path / "par" / "p.ocr.json").read_text(encoding="utf-8"))
    assert [p["pagina"] for p in datos["paginas"]] == list(range(1, 9))
    assert any("Leídas 8 de 8 páginas" in a for a in avisos)


def test_paralelo_divide_lotes_sin_respuesta(tmp_path):
    exp = _expediente_escaneado(tmp_path)
    cliente = ClientePar(0)
    cliente.accion = lambda nums, c: (type("R", (), {"text": "{roto"})() if len(nums) > 2 else None)
    analizador = AnalizadorGemini("p", "eu", "gemini-x", cliente=cliente, paginas_por_lote=4, paralelo=3)
    avisos = []
    docs = procesar(exp, tmp_path / "s", analizador, aviso=lambda m, f: avisos.append(m))
    assert len(docs) == 6
    assert sorted(n for l in cliente.llamadas if len(l) <= 2 for n in l) == list(range(1, 9))
    assert any("Se repite en lotes de 2" in a for a in avisos)


def test_paralelo_baja_el_razonamiento_una_sola_vez(tmp_path):
    exp = _expediente_escaneado(tmp_path)
    cliente = ClientePar(0.05)

    def rechaza_el_ajuste(nums, config):
        if config.thinking_config and config.thinking_config.thinking_level == "LOW":
            e = Exception("Thinking level is unsupported: THINKING_LEVEL_LOW")
            e.code = 400
            raise e

    cliente.accion = rechaza_el_ajuste
    analizador = AnalizadorGemini("p", "eu", "gemini-x", cliente=cliente, paginas_por_lote=2, paralelo=4)
    docs = procesar(exp, tmp_path / "s", analizador)
    assert len(docs) == 6
    assert analizador._nivel == 1  # varios hilos fallaron a la vez y solo se avanzó un paso
    # tras el rechazo, las consultas van sin ajuste y las páginas se leen todas
    assert any(c.thinking_config is None for c in cliente.configs)


def test_429_espera_baja_las_consultas_y_acaba_con_mensaje_claro(tmp_path, monkeypatch):
    from marcadores import ocr_gemini
    from marcadores.ocr_gemini import CuotaAgotada

    esperas = []
    monkeypatch.setattr(ocr_gemini.time, "sleep", lambda s: esperas.append(s))
    exp = _expediente_escaneado(tmp_path)
    cliente = ClientePar(0)

    def satura(nums, config):
        e = Exception("429 RESOURCE_EXHAUSTED. Resource exhausted. Please try again later.")
        e.code = 429
        raise e

    cliente.accion = satura
    analizador = AnalizadorGemini("p", "eu", "gemini-x", cliente=cliente, paginas_por_lote=2, paralelo=4)
    avisos = []
    try:
        procesar(exp, tmp_path / "s", analizador, aviso=lambda m, f: avisos.append(m))
        assert False
    except CuotaAgotada as e:
        assert "429" in str(e) and "Consultas simultáneas" in str(e)
    assert analizador.paralelo < 4  # se bajó la presión sobre la cuota
    assert any("(reintento 7 de 7)" in a for a in avisos)
    assert any("Se reduce a" in a for a in avisos)


def test_429_cancelar_actua_durante_la_espera(tmp_path):
    import threading
    import time

    from marcadores.proceso import Cancelado

    exp = _expediente_escaneado(tmp_path)
    cliente = ClientePar(0)

    def satura(nums, config):
        e = Exception("429 RESOURCE_EXHAUSTED")
        e.code = 429
        raise e

    cliente.accion = satura
    cancelar = threading.Event()
    analizador = AnalizadorGemini("p", "eu", "gemini-x", cliente=cliente, paginas_por_lote=8, paralelo=1)
    inicio = time.monotonic()

    def aviso(m, f):
        if "reintento 1 de 7" in m:
            cancelar.set()  # el usuario pulsa Cancelar mientras se espera 20 s

    try:
        procesar(exp, tmp_path / "s", analizador, aviso=aviso, cancelar=cancelar)
        assert False
    except Cancelado:
        pass
    assert time.monotonic() - inicio < 5  # no esperó los 20 s


def test_paralelo_cancelar_no_espera_a_las_consultas(tmp_path):
    import threading
    import time

    from marcadores.proceso import Cancelado

    exp = _expediente_escaneado(tmp_path)
    cliente = ClientePar(0)
    liberar = threading.Event()
    cliente.accion = lambda nums, c: liberar.wait(10) and None
    cancelar = threading.Event()
    analizador = AnalizadorGemini("p", "eu", "gemini-x", cliente=cliente, paginas_por_lote=2, paralelo=2)
    inicio = time.monotonic()

    def aviso(m, f):
        if "Leyendo" in m:
            cancelar.set()

    try:
        procesar(exp, tmp_path / "s", analizador, aviso=aviso, cancelar=cancelar)
        assert False
    except Cancelado:
        pass
    liberar.set()
    assert time.monotonic() - inicio < 5
    assert len(cliente.llamadas) <= 2  # no se lanzaron más consultas que las permitidas


def test_504_deadline_exceeded_parte_el_lote_sin_repetirlo(tmp_path, monkeypatch):
    """El error real visto en Vertex: un lote grande agota los 5 minutos y devuelve 504."""
    from marcadores import ocr_gemini

    esperas = []
    monkeypatch.setattr(ocr_gemini.time, "sleep", lambda s: esperas.append(s))
    exp = _expediente_escaneado(tmp_path)
    for paralelo in (1, 3):
        cliente = ClientePar(0)

        def agota(nums, config):
            if len(nums) > 2:
                e = Exception("504 DEADLINE_EXCEEDED. Deadline expired before operation could complete.")
                e.code = 504
                raise e

        cliente.accion = agota
        analizador = AnalizadorGemini("p", "eu", "gemini-x", cliente=cliente, paginas_por_lote=8,
                                      paralelo=paralelo)
        avisos = []
        # carpetas sin relación entre sí, para que la segunda pasada no reaproveche la primera
        docs = procesar(exp, tmp_path / f"s{paralelo}" / "r", analizador, aviso=lambda m, f: avisos.append(m))
        assert len(docs) == 6
        grandes = [l for l in cliente.llamadas if len(l) > 2]
        assert len(grandes) == len(set(map(tuple, grandes)))  # ningún lote grande se repitió igual
        assert not [e for e in esperas if e >= 3]  # sin esperas de reintento: se partió directamente
        assert any("Se repite en lotes de" in a for a in avisos)


def _pdf_mixto(ruta: Path) -> None:
    """Páginas 1-4 con texto propio (como un PDF ya pasado por OCR) y 5-8 solo imagen."""
    pdf = fitz.open()
    for n, texto in enumerate(PAGINAS):
        pagina = pdf.new_page()
        if n < 4:
            pagina.insert_textbox(pagina.rect + (50, 50, -50, -50), texto * 3, fontsize=11)
        else:
            with fitz.open() as aux:
                hoja = aux.new_page()
                hoja.insert_textbox(hoja.rect + (50, 50, -50, -50), texto, fontsize=11)
                pagina.insert_image(pagina.rect, pixmap=hoja.get_pixmap(dpi=60))
    pdf.save(ruta)


def test_aprovecha_el_texto_que_ya_trae_el_pdf(tmp_path):
    origen = tmp_path / "ya_ocr.pdf"
    _pdf_mixto(origen)
    exp = recoger([origen])[0]
    cliente = ClientePar(0)
    analizador = AnalizadorGemini("p", "eu", "gemini-x", cliente=cliente, paginas_por_lote=8, paralelo=1)
    docs = procesar(exp, tmp_path / "s", analizador, "Cautelares: medida cautelar")
    partes = cliente.contenidos[0]
    imagenes = [p for p in partes if getattr(p, "inline_data", None)]
    marcas = [p.text for p in partes if p.text and p.text.startswith("PÁGINA")]
    assert len(imagenes) == 4  # solo las páginas escaneadas van como imagen
    assert sum("TEXTO YA RECONOCIDO" in m for m in marcas) == 4
    assert all("TEXTO YA RECONOCIDO" in m for m in marcas[:4])
    with fitz.open(origen) as pdf:
        original = pdf[0].get_text()
    datos = json.loads((tmp_path / "s" / "ya_ocr.ocr.json").read_text(encoding="utf-8"))
    p1, p5 = datos["paginas"][0], datos["paginas"][4]
    assert p1["texto"] == original and p1["lineas"] == []  # texto propio, sin pedir transcripción
    assert p5["lineas"]  # la página escaneada sí trae líneas del modelo
    assert len(docs) >= 1


def test_sin_aprovechar_texto_todo_va_como_imagen(tmp_path):
    origen = tmp_path / "ya_ocr.pdf"
    _pdf_mixto(origen)
    exp = recoger([origen])[0]
    cliente = ClientePar(0)
    analizador = AnalizadorGemini("p", "eu", "gemini-x", cliente=cliente, paginas_por_lote=8,
                                  paralelo=1, usar_texto=False)
    procesar(exp, tmp_path / "s", analizador)
    imagenes = [p for p in cliente.contenidos[0] if getattr(p, "inline_data", None)]
    assert len(imagenes) == 8


def test_avisa_mientras_espera_a_google(tmp_path, monkeypatch):
    import marcadores.proceso as proceso

    monkeypatch.setattr(proceso, "LATIDO", 0.3)
    exp = _expediente_escaneado(tmp_path)
    cliente = ClientePar(1.2)  # Google tarda en contestar
    analizador = AnalizadorGemini("p", "eu", "gemini-x", cliente=cliente, paginas_por_lote=8, paralelo=1)
    avisos = []
    procesar(exp, tmp_path / "s", analizador, aviso=lambda m, f: avisos.append(m))
    assert any(a.startswith("Esperando respuesta de Google: 1 consulta(s) en curso") for a in avisos)


def _respuesta_cortada():
    """Lo que devolvió Vertex con las páginas 158 y 169: 65.519 tokens y fin MAX_TOKENS."""
    candidato = type("C", (), {"finish_reason": "MAX_TOKENS"})()
    return type("R", (), {"text": "[{", "candidates": [candidato], "usage_metadata": None})()


def test_limite_de_respuesta_y_temperatura_por_defecto(tmp_path):
    origen = tmp_path / "ya_ocr.pdf"
    _pdf_mixto(origen)  # 4 páginas con texto y 4 escaneadas
    exp = recoger([origen])[0]
    cliente = ClientePar(0)
    analizador = AnalizadorGemini("p", "eu", "gemini-x", cliente=cliente, paginas_por_lote=8, paralelo=1)
    procesar(exp, tmp_path / "s", analizador)
    c = cliente.configs[0]
    assert c.temperature is None  # Google pide no bajarla en Gemini 3: provoca bucles
    assert c.max_output_tokens == 1500 + 4 * 4500 + 4 * 600  # tope proporcional, no 65.535


def test_pagina_en_bucle_se_repite_y_se_lee(tmp_path):
    exp = _expediente_escaneado(tmp_path)
    cliente = ClientePar(0)
    bucles = {"p3": 1}

    def bucle_en_la_3(nums, config):
        if 3 in nums and (len(nums) > 1 or bucles["p3"]):
            if len(nums) == 1:
                bucles["p3"] -= 1
            return _respuesta_cortada()

    cliente.accion = bucle_en_la_3
    analizador = AnalizadorGemini("p", "eu", "gemini-x", cliente=cliente, paginas_por_lote=4, paralelo=1)
    avisos = []
    procesar(exp, tmp_path / "s", analizador, aviso=lambda m, f: avisos.append(m))
    datos = json.loads((tmp_path / "s" / "p.ocr.json").read_text(encoding="utf-8"))
    assert not any(p["error"] for p in datos["paginas"])  # la 3 acabó leyéndose
    assert any("Página 3: respuesta cortada por longitud (posible bucle). Se repite (1 de 2)" in a
               for a in avisos)
    assert [5, 6, 7, 8] in cliente.llamadas  # el resto siguió con lotes de 4


def test_pagina_que_siempre_falla_no_bloquea_el_expediente(tmp_path):
    exp = _expediente_escaneado(tmp_path)
    cliente = ClientePar(0)
    cliente.accion = lambda nums, c: _respuesta_cortada() if 3 in nums else None
    analizador = AnalizadorGemini("p", "eu", "gemini-x", cliente=cliente, paginas_por_lote=4, paralelo=2)
    avisos = []
    docs = procesar(exp, tmp_path / "s", analizador, aviso=lambda m, f: avisos.append(m))
    assert docs
    assert cliente.llamadas.count([3]) == 3  # un intento y dos repeticiones
    assert any("No se ha podido leer la página 3" in a for a in avisos)


def test_recupera_consultas_simultaneas_tras_un_429(tmp_path, monkeypatch):
    from marcadores import ocr_gemini

    monkeypatch.setattr(ocr_gemini.time, "sleep", lambda s: None)
    monkeypatch.setattr(ocr_gemini, "RECUPERAR_TRAS", 0)
    monkeypatch.setattr(ocr_gemini, "EXITOS_PARA_SUBIR", 2)
    exp = _expediente_escaneado(tmp_path)
    cliente = ClientePar(0)
    primera = {"pendiente": True}

    def un_429(nums, config):
        if primera["pendiente"]:
            primera["pendiente"] = False
            e = Exception("429 RESOURCE_EXHAUSTED")
            e.code = 429
            raise e

    cliente.accion = un_429
    analizador = AnalizadorGemini("p", "eu", "gemini-x", cliente=cliente, paginas_por_lote=1, paralelo=4)
    procesar(exp, tmp_path / "s", analizador)
    assert analizador.paralelo == 4  # bajó a 2 tras el 429 y volvió al máximo


TITULO_ROTO = "Nota simple informativa del Registro de la Propiedad de Ejemplo n\x01 3, finca 1234"


def test_excel_admite_titulos_con_caracteres_invisibles(tmp_path):
    """El error real: IllegalCharacterError al guardar el índice en Excel."""
    from openpyxl import load_workbook

    from marcadores import salidas
    from marcadores.modelos import Documento

    doc = Documento(numero=1, tipo="documento", titulo=TITULO_ROTO, fecha="", autor="Registro\x0b",
                    pagina_inicio=1, pagina_fin=2)
    salidas.escribir_excel([doc], tmp_path / "i.xlsx")
    fila = [c.value for c in load_workbook(tmp_path / "i.xlsx")["Índice"][2]]
    assert "Ejemplo n 3, finca 1234" in fila[4] and fila[5] == "Registro"


def test_respuesta_del_modelo_se_limpia(tmp_path):
    exp = _expediente_escaneado(tmp_path)
    cliente = ClientePar(0)

    def con_caracter_invisible(nums, config):
        datos = [{"pagina": n, "lineas": [{"t": "Finca n\x01 1234", "c": [100, 80, 130, 900]}],
                  "inicia_documento": n == 1, "tipo": "documento", "titulo": TITULO_ROTO,
                  "fecha": "", "autor": "", "subtipo": "Nota\x02 simple", "parte": "", "posicion": "",
                  "plazos": [], "senalamientos": []} for n in nums]
        return type("R", (), {"text": json.dumps(datos)})()

    cliente.accion = con_caracter_invisible
    analizador = AnalizadorGemini("p", "eu", "gemini-x", cliente=cliente, paginas_por_lote=8, paralelo=1)
    docs = procesar(exp, tmp_path / "s", analizador)
    assert docs[0].titulo == TITULO_ROTO.replace("\x01", "") and docs[0].subtipo == "Nota simple"
    assert (tmp_path / "s" / "p - índice.xlsx").exists()


def test_un_expediente_que_falla_no_detiene_los_demas(tmp_path, monkeypatch):
    from marcadores import salidas
    from marcadores.proceso import procesar_varios, resumen_fallos

    rutas = []
    for nombre in ("a", "b", "c"):
        rutas.append(tmp_path / f"{nombre}.pdf")
        crear_pdf(rutas[-1])
    expedientes = recoger(rutas)
    original = salidas.escribir_excel

    def falla_con_b(docs, destino, *resto):
        if destino.name.startswith("b "):
            raise ValueError("fallo simulado")
        return original(docs, destino, *resto)

    monkeypatch.setattr(salidas, "escribir_excel", falla_con_b)
    avisos = []
    fallidos = procesar_varios(expedientes, lambda e: tmp_path / "s" / e.nombre, AnalizadorBasico(),
                               aviso=lambda m, f: avisos.append(m))
    assert [e.nombre for e, _ in fallidos] == ["b"]
    assert (tmp_path / "s" / "c" / "c - marcadores.pdf").exists()  # siguió con el siguiente
    assert any("ERROR en «b»" in a for a in avisos)
    assert "1 de 3" in resumen_fallos(fallidos, 3)


def test_la_cuota_agotada_si_detiene_el_lote(tmp_path, monkeypatch):
    from marcadores import ocr_gemini
    from marcadores.ocr_gemini import CuotaAgotada
    from marcadores.proceso import procesar_varios

    monkeypatch.setattr(ocr_gemini.time, "sleep", lambda s: None)
    rutas = [tmp_path / "a.pdf", tmp_path / "b.pdf"]
    for r in rutas:
        crear_pdf(r, con_texto=False)
    cliente = ClientePar(0)

    def satura(nums, config):
        e = Exception("429 RESOURCE_EXHAUSTED")
        e.code = 429
        raise e

    cliente.accion = satura
    analizador = AnalizadorGemini("p", "eu", "gemini-x", cliente=cliente, paginas_por_lote=8, paralelo=1)
    try:
        procesar_varios(recoger(rutas), lambda e: tmp_path / "s" / e.nombre, analizador)
        assert False
    except CuotaAgotada:
        pass
    assert len(cliente.llamadas) == 8  # 1 intento + 7 reintentos del primero; el segundo ni se intentó


def test_429_pausa_todas_las_consultas_y_separa_los_envios(tmp_path, monkeypatch):
    """Tras un 429 no sale ninguna consulta hasta que pasa la pausa, y los envíos van espaciados."""
    import time as reloj

    from marcadores import ocr_gemini

    monkeypatch.setattr(ocr_gemini, "INTERVALO_MINIMO", 0.2)
    monkeypatch.setattr(ocr_gemini, "ESPERAS_CUOTA", (1,))
    exp = _expediente_escaneado(tmp_path)
    cliente = ClientePar(0)
    envios = []
    primera = {"pendiente": True}

    def un_429(nums, config):
        envios.append(reloj.monotonic())
        if primera["pendiente"]:
            primera["pendiente"] = False
            e = Exception("429 RESOURCE_EXHAUSTED. TPM quota exhausted.")
            e.code = 429
            raise e

    cliente.accion = un_429
    analizador = AnalizadorGemini("p", "eu", "gemini-x", cliente=cliente, paginas_por_lote=2, paralelo=4)
    avisos = []
    procesar(exp, tmp_path / "s", analizador, aviso=lambda m, f: avisos.append(m))
    huecos = [b - a for a, b in zip(envios, envios[1:])]
    assert huecos[0] >= 0.9  # tras el 429, la siguiente consulta esperó la pausa común de 1 s
    tras_pausa = envios[1:]
    # los envíos van espaciados (0,2 s programados; se tolera el retraso al despertar un hilo)
    assert tras_pausa[-1] - tras_pausa[0] >= 0.2 * (len(tras_pausa) - 1) * 0.9
    assert all(h >= 0.08 for h in huecos[1:])  # y nunca dos a la vez
    assert any("limita el uso por minuto" in a for a in avisos)


def test_plantillas_de_nombres():
    from marcadores.nombres import EJEMPLO, Plantillas

    p = Plantillas()
    assert p.de_carpeta("PO 123-2025") == "PO 123-2025 - clasificado"
    assert p.de_pdf("PO 123-2025") == "PO 123-2025 - marcadores.pdf"
    assert p.de_documento(EJEMPLO) == "007 - Auto - 20-03-2025 - Auto de admisión de la demanda.pdf"
    q = Plantillas(carpeta="Exp {nombre} ({hoy})", pdf="{nombre}", excel="",
                   documento="{fecha} {clase} [{num}] {desconocido}")
    assert q.de_carpeta("A/B: C").startswith("Exp A B C (20")
    assert q.de_excel("x") == "x - índice.xlsx"  # plantilla vacía: la de por defecto
    assert q.de_documento(EJEMPLO) == "20-03-2025 Auto de admisión [007] {desconocido}.pdf"
    # huecos vacíos no dejan separadores colgando ni repetidos
    sin_fecha = Plantillas().de_documento(type(EJEMPLO)(numero=1, tipo="auto", titulo="Auto", fecha="",
                                                         autor="", pagina_inicio=1, pagina_fin=1))
    assert sin_fecha == "001 - Auto.pdf"
    assert Plantillas(pdf="CON").de_pdf("x") == "x - marcadores.pdf"  # nombre reservado de Windows


def test_procesar_con_nombres_personalizados(tmp_path):
    from marcadores.nombres import Plantillas
    from marcadores.proceso import carpeta_de

    origen = tmp_path / "PO 7-2025.pdf"
    crear_pdf(origen)
    exp = recoger([origen])[0]
    plantillas = Plantillas(carpeta="{nombre} resultados", pdf="Expediente {nombre}",
                            excel="Índice {nombre}", documento="documento")
    destino = carpeta_de(exp, tmp_path / "salida", plantillas)
    procesar(exp, destino, AnalizadorBasico(), separar=True, plantillas=plantillas)
    assert destino.name == "PO 7-2025 resultados"
    assert (destino / "Expediente PO 7-2025.pdf").exists() and (destino / "Índice PO 7-2025.xlsx").exists()
    nombres = sorted(p.name for p in (destino / "documentos").glob("*.pdf"))
    assert nombres == ["documento (2).pdf", "documento (3).pdf", "documento (4).pdf",
                       "documento (5).pdf", "documento (6).pdf", "documento.pdf"]


def test_nunca_sobrescribe_el_original(tmp_path):
    from marcadores.nombres import Plantillas

    carpeta = tmp_path / "x"
    carpeta.mkdir()
    origen = carpeta / "exp.pdf"
    crear_pdf(origen)
    antes = origen.read_bytes()
    try:
        procesar(recoger([origen])[0], carpeta, AnalizadorBasico(), plantillas=Plantillas(pdf="{nombre}"))
        assert False
    except ValueError as e:
        assert "encima del archivo original" in str(e)
    assert origen.read_bytes() == antes


def test_cambiar_el_nombre_de_carpeta_no_vuelve_a_pagar(tmp_path):
    from marcadores.nombres import Plantillas
    from marcadores.proceso import carpeta_de

    exp = _expediente_escaneado(tmp_path)
    cliente = ClientePar(0)
    analizador = AnalizadorGemini("p", "eu", "gemini-x", cliente=cliente, paginas_por_lote=8, paralelo=1)
    procesar(exp, carpeta_de(exp, tmp_path / "res"), analizador)
    llamadas = len(cliente.llamadas)
    nuevas = Plantillas(carpeta="{nombre} v2")
    procesar(exp, carpeta_de(exp, tmp_path / "res", nuevas), analizador, plantillas=nuevas)
    assert len(cliente.llamadas) == llamadas  # encontró lo leído en la carpeta con el nombre anterior
    assert (tmp_path / "res" / "p v2" / "p - marcadores.pdf").exists()


# --- OCR con Mistral -------------------------------------------------------
class MistralFalso:
    """Imita la API de OCR de Mistral: devuelve el texto de cada página pedida, en orden."""

    def __init__(self, con_bloques=True, fallo=None):
        self.pedidas = []  # cuerpos recibidos
        self.siguiente = 0
        self.con_bloques = con_bloques
        self.fallo = fallo  # función(cuerpo) que puede lanzar un error

    def __call__(self, url, clave, cuerpo, tiempo=300):
        import base64

        self.pedidas.append(cuerpo)
        if self.fallo:
            self.fallo(cuerpo)
        datos = base64.b64decode(cuerpo["document"]["document_url"].split(",", 1)[1])
        with fitz.open(stream=datos, filetype="pdf") as pdf:
            n = pdf.page_count
        paginas = []
        for k in range(n):
            texto = PAGINAS[(self.siguiente + k) % len(PAGINAS)]
            pagina = {"index": k, "markdown": "## " + texto, "images": [],
                      "dimensions": {"dpi": 200, "width": 1700, "height": 2200}}
            if self.con_bloques and cuerpo.get("include_blocks"):
                pagina["blocks"] = [{"type": "text", "text": texto,
                                     "bbox": {"top_left_x": 170, "top_left_y": 220,
                                              "bottom_right_x": 1530, "bottom_right_y": 660}}]
            paginas.append(pagina)
        self.siguiente += n
        return {"pages": paginas, "model": "mistral-ocr-latest", "usage_info": {"pages_processed": n}}


def _gemini_con_mistral(monkeypatch, mistral, **opciones):
    from marcadores import ocr_mistral

    monkeypatch.setattr(ocr_mistral, "_enviar", mistral)
    cliente = ClientePar(0)
    analizador = AnalizadorGemini("p", "eu", "gemini-x", cliente=cliente, paginas_por_lote=8, paralelo=1)
    analizador.ocr_externo = ocr_mistral.OCRMistral("clave-de-prueba", **opciones)
    return cliente, analizador


def test_mistral_lee_y_gemini_solo_clasifica(tmp_path, monkeypatch):
    exp = _expediente_escaneado(tmp_path)  # 8 páginas que son solo imagen
    mistral = MistralFalso()
    cliente, analizador = _gemini_con_mistral(monkeypatch, mistral)
    docs = procesar(exp, tmp_path / "s", analizador, "Cautelares: medida cautelar")
    assert len(mistral.pedidas) == 1 and mistral.pedidas[0]["include_blocks"] is True
    assert mistral.pedidas[0]["model"] == "mistral-ocr-latest"
    imagenes = [p for c in cliente.contenidos for p in c if getattr(p, "inline_data", None)]
    assert imagenes == []  # Gemini no recibe ni una imagen: solo texto para clasificar
    assert len(docs) == 6
    with fitz.open(tmp_path / "s" / "p - marcadores.pdf") as pdf:
        n = pdf.page_count - 8
        hoja = pdf[n + 7]
        zona = hoja.search_for("SENTENCIA")
        # el texto está dentro del recuadro del bloque (10 %-30 % de la altura de la página)
        assert zona and 0.09 * hoja.rect.height < zona[0].y0 < 0.31 * hoja.rect.height
        assert any(hoja.search_for("costas"))
    assert (tmp_path / "s" / "p.mistral.json").exists()
    # segunda pasada en otra carpeta: Mistral no se vuelve a pagar si se reaprovecha su caché
    import shutil
    (tmp_path / "s2").mkdir()
    shutil.copy(tmp_path / "s" / "p.mistral.json", tmp_path / "s2" / "p.mistral.json")
    cliente2, analizador2 = _gemini_con_mistral(monkeypatch, mistral)
    procesar(exp, tmp_path / "s2", analizador2)
    assert len(mistral.pedidas) == 1


def test_mistral_sin_posiciones_sigue_siendo_buscable(tmp_path, monkeypatch):
    exp = _expediente_escaneado(tmp_path)
    cliente, analizador = _gemini_con_mistral(monkeypatch, MistralFalso(con_bloques=False))
    procesar(exp, tmp_path / "s", analizador)
    with fitz.open(tmp_path / "s" / "p - marcadores.pdf") as pdf:
        assert "SENTENCIA" in pdf[pdf.page_count - 1].get_text()


def test_mistral_que_no_admite_bloques_se_repite_sin_ellos(tmp_path, monkeypatch):
    from marcadores.ocr_mistral import ErrorMistral

    def rechaza(cuerpo):
        if cuerpo.get("include_blocks"):
            raise ErrorMistral(422, 'HTTP 422: {"detail": "include_blocks not permitted"}')

    exp = _expediente_escaneado(tmp_path)
    mistral = MistralFalso(fallo=rechaza)
    cliente, analizador = _gemini_con_mistral(monkeypatch, mistral)
    procesar(exp, tmp_path / "s", analizador)
    assert [("include_blocks" in c) for c in mistral.pedidas] == [True, False]


def test_mistral_clave_incorrecta_detiene_el_lote(tmp_path, monkeypatch):
    from marcadores.ocr_mistral import ConfiguracionMistral, ErrorMistral
    from marcadores.proceso import procesar_varios

    def deniega(cuerpo):
        raise ErrorMistral(401, "HTTP 401: Unauthorized")

    rutas = [tmp_path / "a.pdf", tmp_path / "b.pdf"]
    for r in rutas:
        crear_pdf(r, con_texto=False)
    mistral = MistralFalso(fallo=deniega)
    cliente, analizador = _gemini_con_mistral(monkeypatch, mistral)
    try:
        procesar_varios(recoger(rutas), lambda e: tmp_path / "s" / e.nombre, analizador)
        assert False
    except ConfiguracionMistral as e:
        assert "clave de API" in str(e)
    assert len(mistral.pedidas) == 1  # no siguió con el segundo fichero


def test_mistral_caido_las_paginas_van_a_gemini(tmp_path, monkeypatch):
    from marcadores import ocr_mistral
    from marcadores.ocr_mistral import ErrorMistral

    monkeypatch.setattr(ocr_mistral.time, "sleep", lambda s: None)

    def caido(cuerpo):
        raise ErrorMistral(503, "HTTP 503: Service Unavailable")

    exp = _expediente_escaneado(tmp_path)
    mistral = MistralFalso(fallo=caido)
    cliente, analizador = _gemini_con_mistral(monkeypatch, mistral)
    avisos = []
    docs = procesar(exp, tmp_path / "s", analizador, aviso=lambda m, f: avisos.append(m))
    assert len(mistral.pedidas) == 1 + len(ocr_mistral.ESPERAS)
    assert any("irán a Gemini como imagen" in a for a in avisos)
    imagenes = [p for c in cliente.contenidos for p in c if getattr(p, "inline_data", None)]
    assert len(imagenes) == 8 and len(docs) == 6


def test_mistral_formatos_de_respuesta():
    from marcadores.ocr_mistral import lineas_de_bloques, markdown_a_texto

    md = "# AUTO\n\n![img-0.jpeg](img-0.jpeg)\n**En Valencia**, a 1 de enero.\n| A | B |\n|---|---|\n| 1 | 2 |"
    assert markdown_a_texto(md) == "AUTO\n\nEn Valencia, a 1 de enero.\nA | B\n1 | 2"
    dim = {"width": 1000, "height": 2000}
    pixeles = {"dimensions": dim, "blocks": [{"content": "Hola", "bbox": [100, 200, 900, 400]}]}
    assert lineas_de_bloques(pixeles) == [["Hola", 100, 100, 200, 900]]
    normal = {"blocks": [{"text": "Hola", "x": 0.1, "y": 0.1, "width": 0.8, "height": 0.1}]}
    assert lineas_de_bloques(normal) == [["Hola", 100, 100, 200, 900]]
    raro = {"dimensions": dim, "blocks": [{"text": "Hola", "polygon": [[1, 2], [3, 4]]}]}
    assert lineas_de_bloques(raro) == []  # formato desconocido: sin posiciones, sin fallar


def test_crear_analizador_con_mistral(tmp_path, monkeypatch):
    from marcadores import config
    from marcadores.ocr_mistral import OCRMistral
    from marcadores.proceso import crear_analizador

    cfg = config.Config(proyecto="p", motor_ocr="mistral", mistral_clave="x", motor_clasificacion="gemini")
    monkeypatch.setattr("marcadores.ocr_gemini.AnalizadorGemini.__init__",
                        lambda self, *a, **k: setattr(self, "ocr_externo", None))
    analizador = crear_analizador(cfg)
    assert isinstance(analizador.ocr_externo, OCRMistral)
    assert analizador.ocr_externo.url == "https://api.eu.mistral.ai/v1/ocr"
    try:
        OCRMistral("")
        assert False
    except ValueError:
        pass


# --- clasificación con Mistral (sin Google) -------------------------------
class MistralCompleto(MistralFalso):
    """OCR y clasificación: imita /v1/ocr y /v1/chat/completions."""

    def __init__(self, fallo_chat=None, **k):
        super().__init__(**k)
        self.chats = []
        self.fallo_chat = fallo_chat

    def __call__(self, url, clave, cuerpo, tiempo=300):
        if not url.endswith("/v1/chat/completions"):
            return super().__call__(url, clave, cuerpo, tiempo)
        import re

        from marcadores.clasificador_local import REGLAS, buscar_fecha, sin_tildes

        self.chats.append(cuerpo)
        if self.fallo_chat:
            respuesta = self.fallo_chat(cuerpo)
            if respuesta is not None:
                return respuesta
        texto = cuerpo["messages"][1]["content"]
        paginas = []
        for n, contenido in re.findall(r"PÁGINA (\d+) · TEXTO YA RECONOCIDO \(no transcribir\):\n(.*?)(?=\n\nPÁGINA |\Z)",
                                       texto, re.S):
            primera = sin_tildes(contenido.strip().splitlines()[0]).upper() if contenido.strip() else ""
            regla = next((r for r in REGLAS if r[0].search(primera)), None)
            paginas.append({"pagina": int(n), "lineas": [], "inicia_documento": bool(regla),
                            "tipo": regla[1] if regla else "otros", "titulo": regla[2] if regla else "",
                            "fecha": buscar_fecha(contenido) if regla else "", "autor": "",
                            "subtipo": "", "parte": "", "posicion": "", "plazos": [],
                            "senalamientos": []})
        return {"choices": [{"finish_reason": "stop",
                             "message": {"content": json.dumps({"paginas": paginas})}}],
                "usage": {"prompt_tokens": 1000, "completion_tokens": 200}}


def _todo_mistral(monkeypatch, mistral, **k):
    from marcadores import ocr_mistral
    from marcadores.clasificador_mistral import AnalizadorMistral

    monkeypatch.setattr(ocr_mistral, "_enviar", mistral)
    analizador = AnalizadorMistral("clave-de-prueba", paginas_por_lote=4, paralelo=1, **k)
    analizador.ocr_externo = ocr_mistral.OCRMistral("clave-de-prueba")
    return analizador


TIPOS_ESPERADOS = ["escrito_parte", "decreto", "diligencia_ordenacion", "declaracion", "auto", "sentencia"]


def test_mistral_lee_y_clasifica_sin_google(tmp_path, monkeypatch):
    exp = _expediente_escaneado(tmp_path)
    mistral = MistralCompleto()
    analizador = _todo_mistral(monkeypatch, mistral)
    docs = procesar(exp, tmp_path / "s", analizador, "Costas: condena en costas")
    assert [d.tipo for d in docs] == TIPOS_ESPERADOS
    assert docs[1].fecha == "2024-01-15" and docs[5].etiquetas == ["Costas"]
    assert len(mistral.pedidas) == 1 and len(mistral.chats) == 2  # 1 OCR y 2 lotes de 4 páginas
    chat = mistral.chats[0]
    assert chat["model"] == "mistral-medium-latest"
    assert chat["response_format"]["type"] == "json_schema"
    assert chat["response_format"]["json_schema"]["strict"] is True
    assert "Eres un oficial" in chat["messages"][0]["content"]
    assert "TEXTO YA RECONOCIDO" in chat["messages"][1]["content"]
    with fitz.open(tmp_path / "s" / "p - marcadores.pdf") as pdf:
        assert pdf[pdf.page_count - 1].search_for("SENTENCIA")  # texto buscable de Mistral OCR


def test_mistral_sin_esquema_estricto_pide_json_simple(tmp_path, monkeypatch):
    from marcadores.ocr_mistral import ErrorMistral

    def rechaza_esquema(cuerpo):
        if cuerpo["response_format"]["type"] == "json_schema":
            raise ErrorMistral(400, 'HTTP 400: {"message": "response_format json_schema not supported"}')

    exp = _expediente_escaneado(tmp_path)
    mistral = MistralCompleto(fallo_chat=rechaza_esquema)
    docs = procesar(exp, tmp_path / "s", _todo_mistral(monkeypatch, mistral))
    assert [c["response_format"]["type"] for c in mistral.chats] == ["json_schema", "json_object",
                                                                      "json_object"]
    assert [d.tipo for d in docs] == TIPOS_ESPERADOS


def test_mistral_429_al_clasificar_pausa_y_sigue(tmp_path, monkeypatch):
    from marcadores import ocr_gemini
    from marcadores.ocr_mistral import ErrorMistral

    monkeypatch.setattr(ocr_gemini.time, "sleep", lambda s: None)
    veces = {"n": 0}

    def un_429(cuerpo):
        veces["n"] += 1
        if veces["n"] == 1:
            raise ErrorMistral(429, "HTTP 429: rate limit exceeded")

    exp = _expediente_escaneado(tmp_path)
    mistral = MistralCompleto(fallo_chat=un_429)
    avisos = []
    docs = procesar(exp, tmp_path / "s", _todo_mistral(monkeypatch, mistral),
                    aviso=lambda m, f: avisos.append(m))
    assert [d.tipo for d in docs] == TIPOS_ESPERADOS
    assert any("Mistral limita el uso (429)" in a for a in avisos)


def test_mistral_respuesta_cortada_parte_el_lote(tmp_path, monkeypatch):
    def corta_lotes_grandes(cuerpo):
        if cuerpo["messages"][1]["content"].count("PÁGINA ") > 2:
            return {"choices": [{"finish_reason": "length", "message": {"content": "{\"pag"}}]}

    exp = _expediente_escaneado(tmp_path)
    mistral = MistralCompleto(fallo_chat=corta_lotes_grandes)
    avisos = []
    docs = procesar(exp, tmp_path / "s", _todo_mistral(monkeypatch, mistral),
                    aviso=lambda m, f: avisos.append(m))
    assert [d.tipo for d in docs] == TIPOS_ESPERADOS
    assert any("Se repite en lotes de 2" in a for a in avisos)


def test_crear_analizador_todo_con_mistral():
    from marcadores import config
    from marcadores.clasificador_mistral import ESQUEMA, AnalizadorMistral
    from marcadores.ocr_mistral import OCRMistral
    from marcadores.proceso import crear_analizador

    cfg = config.Config(motor_clasificacion="mistral", mistral_clave="x")  # sin nada de Google
    analizador = crear_analizador(cfg)
    assert isinstance(analizador, AnalizadorMistral)
    assert isinstance(analizador.ocr_externo, OCRMistral)
    assert analizador.url == "https://api.eu.mistral.ai/v1/chat/completions"
    items = ESQUEMA["properties"]["paginas"]["items"]
    assert ESQUEMA["properties"]["paginas"]["type"] == "array" and items["type"] == "object"
    assert items["additionalProperties"] is False and items["properties"]["pagina"]["type"] == "integer"
    try:
        crear_analizador(config.Config(motor_clasificacion="mistral", mistral_clave=""))
        assert False
    except ValueError:
        pass


def _escaneado_largo(ruta: Path, paginas: int) -> None:
    pdf = fitz.open()
    imagen = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 60, 80))
    imagen.clear_with(255)
    for _ in range(paginas):
        pdf.new_page().insert_image(fitz.Rect(0, 0, 595, 842), pixmap=imagen)
    pdf.save(ruta)


def test_mistral_lotes_de_50_y_parte_los_demasiado_grandes(tmp_path, monkeypatch):
    from marcadores import ocr_mistral
    from marcadores.ocr_mistral import ErrorMistral, OCRMistral

    ruta = tmp_path / "largo.pdf"
    _escaneado_largo(ruta, 60)
    mistral = MistralFalso()
    monkeypatch.setattr(ocr_mistral, "_enviar", mistral)
    with fitz.open(ruta) as pdf:
        leido = OCRMistral("k").leer(pdf, list(range(60)), tmp_path / "c.json", "h")
    tamanos = []
    for c in mistral.pedidas:
        import base64
        with fitz.open(stream=base64.b64decode(c["document"]["document_url"].split(",", 1)[1]),
                       filetype="pdf") as d:
            tamanos.append(d.page_count)
    assert tamanos == [50, 10] and len(leido) == 60

    # si Mistral dice que es demasiado grande, se parte en dos y no se pierde ninguna página
    def limita_a_25(cuerpo):
        import base64
        with fitz.open(stream=base64.b64decode(cuerpo["document"]["document_url"].split(",", 1)[1]),
                       filetype="pdf") as d:
            if d.page_count > 25:
                raise ErrorMistral(413, "HTTP 413: Request Entity Too Large")

    mistral2 = MistralFalso(fallo=limita_a_25)
    monkeypatch.setattr(ocr_mistral, "_enviar", mistral2)
    with fitz.open(ruta) as pdf:
        leido = OCRMistral("k").leer(pdf, list(range(60)), tmp_path / "c2.json", "h")
    assert len(leido) == 60 and len(mistral2.pedidas) == 4  # 50 (rechazada) → 25 + 25, y 10


def test_instancia_unica_fuera_de_windows_no_bloquea():
    from marcadores import instancia

    assert instancia.primera("Marcadores PDF") is True


class LotesFalso:
    """Imita la API de lotes de Mistral: cada trabajo pasa por la cola antes de terminar."""

    def __init__(self, consultas_en_cola=1, estado_final="SUCCESS", con_archivo=False, sin_lotes=False):
        self.ocr = MistralFalso()
        self.trabajos = {}
        self.orden = []  # registro de lo que se ha pedido: ("crear", id) / ("consultar", id)
        self.borrados = []
        self.en_cola = consultas_en_cola
        self.estado_final = estado_final
        self.con_archivo = con_archivo
        self.sin_lotes = sin_lotes

    def __call__(self, metodo, url, clave, cuerpo=None, tiempo=120, crudo=False):
        from marcadores.ocr_mistral import ErrorMistral

        if self.sin_lotes and "/v1/batch/" in url:
            raise ErrorMistral(404, "HTTP 404: Not Found")
        if metodo == "POST" and url.endswith("/v1/batch/jobs"):
            assert cuerpo["endpoint"] == "/v1/ocr" and cuerpo["model"] == "mistral-ocr-latest"
            ident = f"t{len(self.trabajos)}"
            peticion = cuerpo["requests"][0]
            respuesta = self.ocr("", clave, peticion["body"])
            self.trabajos[ident] = {"consultas": 0, "respuesta": respuesta, "custom_id": peticion["custom_id"]}
            self.orden.append(("crear", ident))
            return {"id": ident, "status": "QUEUED"}
        if metodo == "GET" and "/v1/batch/jobs/" in url:
            ident = url.rsplit("/", 1)[1].split("?")[0]
            t = self.trabajos[ident]
            t["consultas"] += 1
            self.orden.append(("consultar", ident))
            if t["consultas"] <= self.en_cola:
                return {"id": ident, "status": "QUEUED"}
            fila = {"custom_id": t["custom_id"], "response": {"status_code": 200, "body": t["respuesta"]}}
            datos = {"id": ident, "status": self.estado_final, "total_requests": 1, "succeeded_requests": 1}
            if self.estado_final == "SUCCESS":
                if self.con_archivo:
                    datos["output_file"] = f"f-{ident}"
                    t["archivo"] = json.dumps(fila)
                else:
                    datos["outputs"] = [fila]
            return datos
        if metodo == "GET" and "/v1/files/" in url and url.endswith("/content"):
            ident = url.split("/v1/files/f-")[1].split("/")[0]
            return self.trabajos[ident]["archivo"]
        if metodo == "DELETE":
            self.borrados.append(url.rsplit("/", 1)[1])
            return {}
        raise AssertionError(f"petición inesperada {metodo} {url}")


@pytest.fixture
def lotes_rapidos(monkeypatch):
    from marcadores import ocr_mistral

    monkeypatch.setattr(ocr_mistral, "INTERVALO_CONSULTA", 0)


def _sin_sincrono(*a, **k):
    raise AssertionError("no debería usarse el OCR normal")


def test_modo_economico_encarga_espera_y_recoge(tmp_path, monkeypatch, lotes_rapidos):
    from marcadores import ocr_mistral
    from marcadores.ocr_mistral import OCRMistral

    ruta = tmp_path / "largo.pdf"
    _escaneado_largo(ruta, 60)
    lotes = LotesFalso(consultas_en_cola=2)
    monkeypatch.setattr(ocr_mistral, "_http", lotes)
    monkeypatch.setattr(ocr_mistral, "_enviar", _sin_sincrono)
    avisos = []
    with fitz.open(ruta) as pdf:
        leido = OCRMistral("k", economico=True).leer(pdf, list(range(60)), tmp_path / "c.json", "h",
                                                      aviso=avisos.append)
    assert len(leido) == 60 and len(lotes.trabajos) == 2  # 50 + 10 páginas
    assert any("esperando a Mistral" in a for a in avisos)
    assert json.loads((tmp_path / "c.json").read_text(encoding="utf-8"))["trabajos"] == []
    assert leido[1][0] and leido[60][0]


def test_modo_economico_resultado_en_archivo_se_descarga_y_se_borra(tmp_path, monkeypatch, lotes_rapidos):
    from marcadores import ocr_mistral
    from marcadores.ocr_mistral import OCRMistral

    ruta = tmp_path / "corto.pdf"
    _escaneado_largo(ruta, 5)
    lotes = LotesFalso(con_archivo=True)
    monkeypatch.setattr(ocr_mistral, "_http", lotes)
    monkeypatch.setattr(ocr_mistral, "_enviar", _sin_sincrono)
    with fitz.open(ruta) as pdf:
        leido = OCRMistral("k", economico=True).leer(pdf, list(range(5)), tmp_path / "c.json", "h")
    assert len(leido) == 5 and lotes.borrados == ["f-t0"]


def test_modo_economico_se_puede_cerrar_y_recoger_despues(tmp_path, monkeypatch, lotes_rapidos):
    import threading

    from marcadores import ocr_mistral
    from marcadores.ocr_gemini import ConsultaCancelada
    from marcadores.ocr_mistral import OCRMistral

    ruta = tmp_path / "corto.pdf"
    _escaneado_largo(ruta, 5)
    lotes = LotesFalso(consultas_en_cola=1)
    monkeypatch.setattr(ocr_mistral, "_http", lotes)
    monkeypatch.setattr(ocr_mistral, "_enviar", _sin_sincrono)
    cancelar = threading.Event()

    def aviso(m):  # el usuario cierra mientras espera
        if "esperando" in m:
            cancelar.set()

    with fitz.open(ruta) as pdf:
        with pytest.raises(ConsultaCancelada):
            OCRMistral("k", economico=True).leer(pdf, list(range(5)), tmp_path / "c.json", "h",
                                                 aviso=aviso, cancelar=cancelar)
        assert len(json.loads((tmp_path / "c.json").read_text(encoding="utf-8"))["trabajos"]) == 1
        # al volver (aunque ya no esté el modo económico) se recoge sin encargarlo otra vez
        leido = OCRMistral("k").leer(pdf, list(range(5)), tmp_path / "c.json", "h")
    assert len(leido) == 5 and len(lotes.trabajos) == 1


def test_modo_economico_sin_lotes_o_fallido_pasa_a_modo_normal(tmp_path, monkeypatch, lotes_rapidos):
    from marcadores import ocr_mistral
    from marcadores.ocr_mistral import OCRMistral

    ruta = tmp_path / "corto.pdf"
    _escaneado_largo(ruta, 5)
    for lotes in (LotesFalso(sin_lotes=True), LotesFalso(estado_final="FAILED")):
        normal = MistralFalso()
        monkeypatch.setattr(ocr_mistral, "_http", lotes)
        monkeypatch.setattr(ocr_mistral, "_enviar", normal)
        cache = tmp_path / f"c{id(lotes)}.json"
        with fitz.open(ruta) as pdf:
            leido = OCRMistral("k", economico=True).leer(pdf, list(range(5)), cache, "h")
        assert len(leido) == 5 and len(normal.pedidas) == 1


def test_modo_economico_con_varios_expedientes_encarga_todos_antes_de_esperar(
        tmp_path, monkeypatch, lotes_rapidos):
    from marcadores import ocr_mistral
    from marcadores.ocr_mistral import OCRMistral

    for d in ("a", "b"):
        (tmp_path / d).mkdir()
    exps = [_expediente_escaneado(tmp_path / d) for d in ("a", "b")]
    lotes = LotesFalso(consultas_en_cola=1)
    monkeypatch.setattr(ocr_mistral, "_http", lotes)
    monkeypatch.setattr(ocr_mistral, "_enviar", _sin_sincrono)
    cliente = ClientePar(0)
    analizador = AnalizadorGemini("p", "eu", "gemini-x", cliente=cliente, paginas_por_lote=8, paralelo=1)
    analizador.ocr_externo = OCRMistral("k", economico=True)
    fallidos = procesar_varios(exps, lambda e: e.carpeta / "salida", analizador)
    assert fallidos == []
    assert [o for o, _ in lotes.orden[:2]] == ["crear", "crear"]  # los dos encargos van primero
    assert len(lotes.trabajos) == 2


def test_texto_de_pagina_escaneada_de_lado_no_se_parte(tmp_path):
    from marcadores.modelos import PaginaInfo
    from marcadores.salidas import _capa_texto

    with fitz.open() as pdf:
        pdf.new_page(width=595, height=842)
        # recuadros altos y estrechos, como los de un correo escaneado girado (página 16 real)
        lineas = [["Fulano de Tal Ejemplo <fulano@ejemplo.es>", 670, 892, 922, 911],
                  ["Le recuerdo que el plazo para presentar el escrito vence mañana,", 47, 647, 413, 667]]
        _capa_texto(pdf, [PaginaInfo(pagina=1, texto="x", lineas=lineas)])
        texto = pdf[0].get_text()
    assert "Fulano de Tal Ejemplo" in texto
    assert "plazo para presentar" in texto


def test_plazos_segun_tipo_de_expediente():
    from marcadores.clasificar import ajustar_al_ambito, decidir_ambito, segmentar
    from marcadores.modelos import PaginaInfo

    def paginas():
        return [PaginaInfo(pagina=1, inicia_documento=True, tipo="notificacion"),
                PaginaInfo(pagina=2, inicia_documento=True, tipo="documento",
                           plazos=[{"concepto": "Impugnar la factura", "plazo": "quince días hábiles"}]),
                PaginaInfo(pagina=3, inicia_documento=True, tipo="decreto",
                           plazos=[{"concepto": "Contestar a la demanda", "plazo": "veinte días"}])]

    # judicial: solo los plazos del tribunal
    docs = segmentar(paginas())
    assert decidir_ambito(docs) == "judicial"  # hay un decreto
    ajustar_al_ambito(docs, "judicial")
    assert docs[1].plazos == [] and docs[2].plazos[0]["plazo"] == "veinte días"
    # extrajudicial: también los de los documentos
    docs = segmentar(paginas())
    ajustar_al_ambito(docs, "extrajudicial")
    assert docs[1].plazos[0]["plazo"] == "quince días hábiles"
    # una notificación de LexNET delante de prueba documental no hace judicial el expediente
    assert decidir_ambito(segmentar(paginas()[:2])) == "extrajudicial"


def test_cronologia_ordenada_por_fecha_segun_tipo():
    from marcadores.modelos import Documento
    from marcadores.salidas import cronologia_de

    docs = [Documento(1, "escrito_parte", "Demanda", "2026-01-12", "", 1, 3, subtipo="Demanda",
                      parte="Juan Pérez", posicion="demandante"),
            Documento(2, "documento", "Factura", "2020-08-31", "", 4, 4),
            Documento(3, "decreto", "Decreto de admisión", "2026-02-03", "Tribunal", 5, 5,
                      plazos=[{"concepto": "Contestar", "plazo": "veinte días", "pagina": 5}],
                      senalamientos=[{"acto": "Audiencia previa", "fecha": "2099-06-15",
                                      "hora": "10:00", "lugar": "Sala 3", "pagina": 5}]),
            Documento(4, "providencia", "Providencia sin fecha", "", "", 6, 6)]
    judicial = cronologia_de(docs, "judicial")
    assert [h["doc"].numero for h in judicial] == [1, 3, 3, 4]  # sin la factura; sin fecha al final
    assert "Juan Pérez (demandante)" == judicial[0]["quien"]
    assert "veinte días" in judicial[1]["hito"]
    assert judicial[2]["pendiente"] and "Audiencia previa" in judicial[2]["hito"]
    hechos = cronologia_de(docs, "extrajudicial")
    assert hechos[0]["doc"].numero == 2  # la factura de 2020 es el primer hecho

def test_giro_por_recuadros_del_ocr():
    from marcadores.paginas import giro_por_recuadros

    # normal: renglones horizontales de arriba abajo
    normal = [[f"renglón número {i}", 100 + 40 * i, 100, 120 + 40 * i, 900] for i in range(6)]
    assert giro_por_recuadros(normal) == 0
    # de lado, leído de derecha a izquierda (lo de arriba del folio quedó a la derecha)
    derecha = [[f"renglón número {i}", 100, 900 - 60 * i, 900, 920 - 60 * i] for i in range(6)]
    assert giro_por_recuadros(derecha) == 270
    izquierda = [[f"renglón número {i}", 100, 100 + 60 * i, 900, 120 + 60 * i] for i in range(6)]
    assert giro_por_recuadros(izquierda) == 90
    # boca abajo: se lee de abajo arriba
    abajo = [[f"renglón número {i}", 880 - 40 * i, 100, 900 - 40 * i, 900] for i in range(6)]
    assert giro_por_recuadros(abajo) == 180
    # con muy pocos recuadros no se arriesga
    assert giro_por_recuadros(derecha[:2]) == 0


def test_enderezar_y_quitar_blancas(tmp_path):
    from openpyxl import load_workbook

    origen = tmp_path / "e.pdf"
    with fitz.open() as pdf:
        p = pdf.new_page()
        p.insert_text((72, 100), "SENTENCIA 12/2024 de 5 de mayo de 2024", fontsize=12)
        p.insert_text((72, 130), "En Valencia, a cinco de mayo de dos mil veinticuatro.", fontsize=11)
        pdf.new_page()  # en blanco
        p = pdf.new_page()
        p.insert_text((550, 820), "37", fontsize=9)  # solo el número de folio: también en blanco
        p = pdf.new_page()  # escrito de lado: el texto va de abajo arriba
        for i in range(6):
            p.insert_text((100 + 30 * i, 700), f"ESCRITO de la parte actora, renglón {i} del texto",
                          fontsize=11, rotate=90)
        p = pdf.new_page()  # firma suelta, sin texto: no se quita
        p.draw_bezier((300, 600), (340, 520), (380, 680), (440, 590), width=3)
        p.draw_line((280, 620), (460, 610), width=3)
        pdf.save(origen)
    exp = recoger([origen])[0]
    salida = tmp_path / "s"
    procesar(exp, salida, AnalizadorBasico(), quitar_blancas=True)
    with fitz.open(salida / "e - marcadores.pdf") as pdf:
        n = pdf.page_count - 3  # páginas del índice
        texto = "".join(pdf[i].get_text() for i in range(n))
        assert "Retiradas 2 páginas en blanco (numeración del original): 2, 3." in texto
        assert "Se han enderezado 1 páginas" in texto
        assert pdf[n + 1].rotation == 90  # el escrito girado, ahora segunda página
        assert pdf[n].rotation == 0 and pdf[n + 2].rotation == 0
    # el original no se toca
    with fitz.open(origen) as pdf:
        assert pdf.page_count == 5 and pdf[3].rotation == 0
    assert load_workbook(salida / "e - índice.xlsx")["Índice"].max_row >= 2


def test_tipo_de_expediente_decidido_para_el_lote(tmp_path):
    def pdf_con(ruta, lineas):
        with fitz.open() as pdf:
            p = pdf.new_page()
            for i, t in enumerate(lineas):
                p.insert_text((72, 100 + 20 * i), t, fontsize=11)
            pdf.save(ruta)

    (tmp_path / "e").mkdir()
    pdf_con(tmp_path / "e" / "a.pdf", ["DECRETO", "Letrado de la Administración de Justicia",
                                       "En Valencia, a 3 de febrero de 2026.",
                                       "Se admite a trámite la demanda presentada."])
    pdf_con(tmp_path / "e" / "b.pdf", ["FACTURA número 2020/118", "Fecha: 31 de agosto de 2020",
                                       "Honorarios profesionales por la escritura de compraventa."])
    expedientes = recoger([tmp_path / "e"])
    assert len(expedientes) == 2
    procesar_varios(expedientes, lambda e: tmp_path / "s" / e.nombre, AnalizadorBasico())
    with fitz.open(tmp_path / "s" / "b" / "b - marcadores.pdf") as pdf:
        texto = pdf[0].get_text()
    assert "Expediente judicial (decidido para todos los archivos" in texto
    # con el tipo elegido a mano no se cambia nada
    procesar_varios(expedientes, lambda e: tmp_path / "t" / e.nombre, AnalizadorBasico(),
                    tipo_expediente="extrajudicial")
    with fitz.open(tmp_path / "t" / "b" / "b - marcadores.pdf") as pdf:
        assert "Expediente extrajudicial" in pdf[0].get_text()


class ClaudeFalso:
    """Imita client.messages.create de la librería de Anthropic, clasificando como MistralCompleto."""

    def __init__(self, fallos=None):
        from types import SimpleNamespace

        self.llamadas = []
        self.fallos = list(fallos or [])  # excepciones a lanzar en las primeras llamadas
        self.messages = SimpleNamespace(create=self._crear)
        self._clasificador = MistralCompleto()

    def _crear(self, **kw):
        from types import SimpleNamespace

        self.llamadas.append(kw)
        if self.fallos:
            raise self.fallos.pop(0)
        cuerpo = {"messages": [{}, {"content": kw["messages"][0]["content"]}]}
        r = self._clasificador("x/v1/chat/completions", "k", cuerpo)
        texto = r["choices"][0]["message"]["content"]
        return SimpleNamespace(stop_reason="end_turn",
                               content=[SimpleNamespace(type="text", text=texto)],
                               usage=SimpleNamespace(input_tokens=900, output_tokens=150,
                                                     cache_read_input_tokens=0))


def _error_anthropic(clase, codigo, mensaje="error"):
    import anthropic
    import httpx2

    peticion = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    return clase(mensaje, response=httpx2.Response(codigo, request=peticion), body=None)


def _con_claude(monkeypatch, cliente, **k):
    from marcadores import ocr_mistral
    from marcadores.clasificador_claude import AnalizadorClaude

    monkeypatch.setattr(ocr_mistral, "_enviar", MistralFalso())
    analizador = AnalizadorClaude("", paginas_por_lote=4, paralelo=1, cliente=cliente, **k)
    analizador.ocr_externo = ocr_mistral.OCRMistral("clave-de-prueba")
    return analizador


def test_claude_clasifica_con_el_texto_de_mistral(tmp_path, monkeypatch):
    exp = _expediente_escaneado(tmp_path)
    cliente = ClaudeFalso()
    docs = procesar(exp, tmp_path / "s", _con_claude(monkeypatch, cliente), "Costas: condena en costas")
    assert [d.tipo for d in docs] == TIPOS_ESPERADOS
    assert len(cliente.llamadas) == 2  # 2 lotes de 4 páginas
    pedido = cliente.llamadas[0]
    assert pedido["model"] == "claude-haiku-5-5"
    assert pedido["output_config"]["format"]["type"] == "json_schema"
    assert pedido["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "Eres un oficial" in pedido["system"][0]["text"]
    assert "temperature" not in pedido  # la API la rechaza en los modelos actuales


def test_claude_pausa_ante_429_y_se_detiene_sin_clave_o_sin_creditos(tmp_path, monkeypatch):
    import anthropic

    from marcadores.clasificador_claude import ConfiguracionClaude
    from marcadores.proceso import procesar_varios

    exp = _expediente_escaneado(tmp_path)
    cliente = ClaudeFalso(fallos=[_error_anthropic(anthropic.RateLimitError, 429)])
    analizador = _con_claude(monkeypatch, cliente)
    pausas = []
    monkeypatch.setattr(analizador, "_pausar_todas", lambda s: pausas.append(s))
    docs = procesar(exp, tmp_path / "s", analizador)
    assert len(docs) == 6 and pausas == [15]

    for error, codigo in ((_error_anthropic(anthropic.AuthenticationError, 401), 401),
                          (_error_anthropic(anthropic.BadRequestError, 400,
                                            "Your credit balance is too low"), 402)):
        cliente = ClaudeFalso(fallos=[error] * 5)
        with pytest.raises(ConfiguracionClaude) as fallo:
            procesar_varios([exp], lambda e: tmp_path / "otra" / f"aparte{codigo}", _con_claude(monkeypatch, cliente))
        assert fallo.value.code == codigo


def test_crear_analizador_con_claude():
    from marcadores.clasificador_claude import AnalizadorClaude
    from marcadores.config import Config
    from marcadores.proceso import crear_analizador

    cfg = Config(motor_clasificacion="claude", claude_clave="k", mistral_clave="m")
    analizador = crear_analizador(cfg)
    assert isinstance(analizador, AnalizadorClaude) and analizador.ocr_externo is not None
    assert analizador.firma == "claude:claude-haiku-5-5:3"


def test_exportacion_para_rag(tmp_path):
    from marcadores.clasificador_local import AnalizadorBasico

    origen = tmp_path / "expediente.pdf"
    crear_pdf(origen)
    exp = recoger([origen])[0]
    salida = tmp_path / "s"
    docs = procesar(exp, salida, AnalizadorBasico(), "Costas: condena en costas")
    archivos = sorted((salida / "RAG").glob("*.md"))
    assert len(archivos) == len(docs)
    assert archivos[0].name.startswith("001 - ")
    texto = next(a for a in archivos if "Sentencia" in a.name).read_text(encoding="utf-8")
    assert "- **Palabras clave:** Costas" in texto
    assert "## Página 8" in texto and "[Doc. 006 · Sentencia" in texto  # cada página, identificada
    registros = [json.loads(l) for l in (salida / "expediente - rag.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [r["numero"] for r in registros] == [d.numero for d in docs]
    assert registros[2]["plazos"] and registros[2]["paginas"][0]["pagina"] == 4
    # al repetir, se rehace sin dejar archivos viejos
    procesar(exp, salida, AnalizadorBasico())
    assert len(list((salida / "RAG").glob("*.md"))) == len(docs)
    # y se puede desactivar
    procesar(exp, tmp_path / "t", AnalizadorBasico(), exportar_rag=False)
    assert not (tmp_path / "t" / "RAG").exists()

def test_actualizacion_desde_el_repositorio_publico_sin_token(tmp_path, monkeypatch):
    import io
    import urllib.request

    from marcadores import actualizar

    contenido = b"MZ" + b"y" * 300
    publicado = {"tag_name": "v99.1.0", "assets": [{
        "name": "MarcadoresPDF-99.1.0.exe", "size": len(contenido),
        "url": "https://api.github.com/repos/setokenz/marcadores-pdf-publico/releases/assets/7"}]}

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            self.close()

    def abrir(pet, timeout):
        assert "marcadores-pdf-publico" in pet.full_url and pet.get_header("Authorization") is None
        return Resp(json.dumps(publicado).encode())

    version = actualizar.buscar("secreto", abrir=abrir)
    assert version.numero == "99.1.0" and not version.privado

    class Abridor:
        def open(self, pet, timeout):
            assert pet.get_header("Authorization") is None  # el token no sale hacia el público
            return Resp(contenido)

    monkeypatch.setattr(urllib.request, "build_opener", lambda *a: Abridor())
    destino = tmp_path / "nuevo.exe"
    actualizar.descargar(version, destino, "secreto")
    assert destino.read_bytes() == contenido


def test_orden_natural_de_tomos():
    from marcadores.entrada import _orden_natural

    nombres = ["Tomo 10.pdf", "Tomo 2.pdf", "Tomo 1.pdf", "TOMO-IX.pdf", "TOMO-II.pdf", "TOMO-V.pdf",
               "CASO_PIEZA-I_TOMO-II_doc.pdf", "CASO_PIEZA-I_TOMO-I_doc.pdf"]
    orden = sorted(nombres, key=lambda n: _orden_natural(Path(n)))
    assert orden.index("Tomo 1.pdf") < orden.index("Tomo 2.pdf") < orden.index("Tomo 10.pdf")
    assert orden.index("TOMO-II.pdf") < orden.index("TOMO-V.pdf") < orden.index("TOMO-IX.pdf")
    assert orden.index("CASO_PIEZA-I_TOMO-I_doc.pdf") < orden.index("CASO_PIEZA-I_TOMO-II_doc.pdf")


def test_unir_todo_respeta_el_orden_recibido(tmp_path):
    for nombre, texto in (("Tomo 1.pdf", "PRIMERO"), ("Tomo 2.pdf", "SEGUNDO")):
        with fitz.open() as pdf:
            pdf.new_page().insert_text((72, 100), texto)
            pdf.save(tmp_path / nombre)
    from marcadores.entrada import unir

    exp = recoger([tmp_path / "Tomo 1.pdf", tmp_path / "Tomo 2.pdf"], unir_todo=True)[0]
    with unir(exp.rutas) as pdf:
        assert "PRIMERO" in pdf[0].get_text() and "SEGUNDO" in pdf[1].get_text()
    # una carpeta con los tomos se une en orden natural
    exp = recoger([tmp_path], unir_todo=True)[0]
    assert [r.name for r in exp.rutas] == ["Tomo 1.pdf", "Tomo 2.pdf"]

def test_valores_por_defecto_para_un_usuario_nuevo():
    from marcadores.config import Config

    cfg = Config()  # lo que anuncia la pantalla de presentación: Mistral lee y Claude clasifica
    assert cfg.motor_ocr == "mistral" and cfg.motor_clasificacion == "claude"
    assert not cfg.mistral_clave and not cfg.claude_clave and not cfg.ocultar_presentacion


def test_actualizacion_continua_si_se_corta_la_descarga(tmp_path, monkeypatch):
    import io
    import urllib.request

    from marcadores import actualizar

    contenido = b"MZ" + bytes(range(256)) * 40
    version = actualizar.Version("99.2.0", "https://api.github.com/repos/x/y/releases/assets/9",
                                 len(contenido), privado=False)
    peticiones = []

    class Resp(io.BytesIO):
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            self.close()

    class Abridor:
        def open(self, pet, timeout):
            rango = pet.get_header("Range")
            peticiones.append(rango)
            if rango is None:
                return Resp(contenido[:3000])  # la conexión se corta a mitad
            r = Resp(contenido[int(rango.split("=")[1].rstrip("-")):])
            r.status = 206
            return r

    monkeypatch.setattr(urllib.request, "build_opener", lambda *a: Abridor())
    destino = tmp_path / "nuevo.exe"
    actualizar.descargar(version, destino, esperar=lambda s: None)
    assert destino.read_bytes() == contenido
    assert peticiones == [None, "bytes=3000-"]


def test_indice_solo_lista_de_documentos(tmp_path):
    """Sin cronología, apartados, agenda ni palabras clave: solo la lista, en índice y marcadores."""
    origen = tmp_path / "expediente.pdf"
    crear_pdf(origen)
    salida = tmp_path / "salida"
    procesar(recoger([origen])[0], salida, AnalizadorBasico(), "Costas: condena en costas",
             contenido_indice=())
    with fitz.open(salida / "expediente - marcadores.pdf") as pdf:
        n = pdf.page_count - 8
        texto = "".join(pdf[i].get_text() for i in range(n)).upper()
        niveles1 = [t[1] for t in pdf.get_toc() if t[0] == 1]
    assert n == 1 and niveles1 == ["Índice", "Documentos (orden del escaneo)"]
    assert "TODOS LOS DOCUMENTOS" in texto
    assert "CRONOLOG" not in texto and "RESOLUCIONES JUDICIALES" not in texto and "PLAZOS" not in texto


def test_botones_de_navegacion(tmp_path):
    """Cada página vuelve a su fila del índice; la primera de cada documento salta al anterior y
    al siguiente de su clase; los botones son anotaciones que no se imprimen."""
    origen = tmp_path / "expediente.pdf"
    crear_pdf(origen)
    salida = tmp_path / "salida"
    docs = procesar(recoger([origen])[0], salida, AnalizadorBasico(),
                    "Tema: medida cautelar, condena en costas")
    with fitz.open(salida / "expediente - marcadores.pdf") as pdf:
        n = pdf.page_count - 8

        def botones(pagina):  # {texto: página de destino (numeración del expediente; 0 = índice)}
            hoja = pdf[pagina - 1 + n]
            destinos = {}
            for nota in hoja.annots(types=[fitz.PDF_ANNOT_FREE_TEXT]):
                assert not nota.flags & fitz.PDF_ANNOT_IS_PRINT  # no sale al imprimir
                enlace = next(e for e in hoja.get_links() if fitz.Rect(e["from"]).intersects(nota.rect))
                destinos[nota.info["content"]] = max(0, enlace["page"] - n + 1)
            return destinos

        assert all("Índice" in botones(p) and botones(p)["Índice"] == 0 for p in range(1, 9))
        # resoluciones del ejemplo: decreto (pág. 3), diligencia (4), auto (7), sentencia (8):
        # cada una es única en su clase, así que no tienen vecinos; la declaración tampoco
        assert set(botones(3)) == {"Índice"}
        # la palabra clave aparece en las páginas 7 y 8: se enlazan entre sí
        assert botones(7)["Tema »"] == 8 and botones(8)["« Tema"] == 7
        # el botón «Índice» apunta a la hoja del índice donde está la fila del documento
        fila = next(e for i in range(n) for e in pdf[i].get_links() if e["page"] - n == 7)
        assert fila is not None
    assert len(docs) == 6
    # sin navegación no se añade ningún botón
    procesar(recoger([origen])[0], tmp_path / "sin", AnalizadorBasico(), "", navegacion=False)
    with fitz.open(tmp_path / "sin" / "expediente - marcadores.pdf") as pdf:
        assert not [a for hoja in pdf for a in hoja.annots(types=[fitz.PDF_ANNOT_FREE_TEXT])]


def test_botones_entre_documentos_de_la_misma_clase():
    """Con dos autos, el primero lleva «Auto »» y el segundo «« Auto»; en páginas giradas también."""
    from marcadores import salidas
    from marcadores.modelos import Documento

    pdf = fitz.open()
    for _ in range(4):
        pdf.new_page()
    pdf[2].set_rotation(90)
    docs = [Documento(1, "auto", "", "", "", 1, 1), Documento(2, "providencia", "", "", "", 2, 2),
            Documento(3, "auto", "", "", "", 3, 4)]
    salidas._navegacion(pdf, docs, {}, 0, {})
    textos = [[a.info["content"] for a in hoja.annots()] for hoja in pdf]
    assert textos == [["Auto »"], [], ["« Auto"], []]
    assert pdf[0].get_links()[0]["page"] == 2 and pdf[2].get_links()[0]["page"] == 0
    # en la página girada el botón queda dentro de la hoja
    girada = pdf[2]  # la página debe seguir viva mientras se usan sus anotaciones
    assert next(girada.annots()).rect in girada.mediabox


def test_comparar_modelos_de_ocr(tmp_path, monkeypatch):
    """Lee las mismas páginas con dos modelos, mide la coincidencia y deja un .txt por modelo."""
    from marcadores import ocr_mistral

    origen = tmp_path / "escaneado.pdf"
    crear_pdf(origen, con_texto=False)
    falso = MistralFalso()
    monkeypatch.setattr(ocr_mistral, "_enviar", falso)
    modelos = ("mistral-ocr-latest", ocr_mistral.MODELO_ANTERIOR)
    informe = ocr_mistral.comparar_modelos("clave-de-prueba", ocr_mistral.SERVIDOR_UE, origen,
                                           tmp_path / "comp", modelos)
    assert [c["model"] for c in falso.pedidas] == list(modelos)  # una petición por modelo, mismas páginas
    assert "8 páginas escaneadas" in informe and "Coincidencia media del texto, palabra a palabra: 100.0 %" in informe
    assert sorted(f.name for f in (tmp_path / "comp").iterdir()) == [
        "escaneado - OCR mistral-ocr-2512.txt", "escaneado - OCR mistral-ocr-latest.txt"]
    assert "===== PÁGINA 8 =====" in (tmp_path / "comp" / "escaneado - OCR mistral-ocr-latest.txt").read_text("utf-8")
    # con lecturas distintas la coincidencia baja y se señalan las páginas que más difieren
    informe = ocr_mistral.comparar_modelos("clave-de-prueba", ocr_mistral.SERVIDOR_UE, origen,
                                           tmp_path / "comp", modelos, maximo=4)
    assert "100.0 %" not in informe and "Páginas que más difieren" in informe
    # un PDF que ya trae texto no sirve para comparar, y no se gasta nada
    con_texto = tmp_path / "texto.pdf"
    crear_pdf(con_texto)
    antes = len(falso.pedidas)
    assert "no tiene páginas escaneadas" in ocr_mistral.comparar_modelos(
        "clave-de-prueba", ocr_mistral.SERVIDOR_UE, con_texto, tmp_path / "comp", modelos)
    assert len(falso.pedidas) == antes
