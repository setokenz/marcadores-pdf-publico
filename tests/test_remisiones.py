"""Remisiones entre documentos: detección por patrones, regla estricta y enlaces en el PDF."""
import pymupdf as fitz

from marcadores import salidas
from marcadores.modelos import Documento, PaginaInfo
from marcadores.remisiones import buscar, citado_por, resolver


def test_buscar_patrones():
    texto = ("Que, notificado el Auto de fecha 20 de marzo de 2024, y vista la providencia de\n"
             "2/4/2024, así como el decreto nº 15/2024, de 5 de mayo de 2024 y la Diligencia de "
             "Ordenación dictada el día 7 de junio de 2024, y la sentencia del 1 de julio del 2024.")
    assert [(t, f) for t, f, _ in buscar(texto)] == [
        ("auto", "2024-03-20"), ("providencia", "2024-04-02"), ("decreto", "2024-05-05"),
        ("diligencia_ordenacion", "2024-06-07"), ("sentencia", "2024-07-01")]
    assert buscar(texto)[1][2] == "providencia de 2/4/2024"  # frase literal, sin saltos de línea


def test_no_confunde():
    assert buscar("en los autos de 3 de marzo de 2024") == []  # «autos» es el procedimiento
    assert buscar("el auto de 40 de marzo de 2024") == [] and buscar("el auto de 3 de marzol de 2024") == []
    assert buscar("el auto. De 3 de marzo de 2024 es la carta") == []  # no cruza el punto


def docs_y_paginas():
    docs = [Documento(1, "auto", "Auto de admisión", "2024-03-20", "", 1, 1),
            Documento(2, "escrito_parte", "Recurso de reposición", "2024-04-01", "", 2, 3),
            Documento(3, "auto", "Auto resolviendo", "2024-05-05", "", 4, 4),
            Documento(4, "auto", "Otro auto del mismo día", "2024-05-05", "", 5, 5),
            Documento(5, "oficio", "Oficio al banco", "2024-06-01", "", 6, 6)]
    textos = {1: "AUTO. En Valencia, a 20 de marzo de 2024. Se admite la demanda.",
              2: "AL TRIBUNAL. Que frente al auto de 20 de marzo de 2024 interpongo recurso.",
              3: "Reitero lo dicho sobre el Auto de 20 de marzo de 2024 y sobre la providencia de 9/9/2023.",
              4: "AUTO. A 5 de mayo de 2024. Visto el recurso.", 5: "AUTO. A 5 de mayo de 2024.",
              6: "En cumplimiento del auto de fecha 5 de mayo de 2024 y del auto de 20 de marzo de 2024."}
    return docs, [PaginaInfo(pagina=n, texto=t) for n, t in textos.items()]


def test_resolver_regla_estricta():
    docs, paginas = docs_y_paginas()
    rs = resolver(docs, paginas)
    resumen = [(r.origen.numero, r.pagina, r.tipo, r.fecha, r.destino.numero if r.destino else None) for r in rs]
    assert resumen == [
        (2, 2, "auto", "2024-03-20", 1), (2, 3, "auto", "2024-03-20", 1),
        (2, 3, "providencia", "2023-09-09", None),  # no está en el expediente
        (5, 6, "auto", "2024-05-05", None),  # dos autos de ese día: no se elige
        (5, 6, "auto", "2024-03-20", 1)]
    assert "no está" in rs[2].motivo and "hay 2" in rs[3].motivo
    # el auto no se cita a sí mismo por llevar su propia fecha en el encabezado
    assert not [r for r in rs if r.origen.numero in (1, 3, 4)]
    (citado, citas), = citado_por(rs)
    assert citado.numero == 1 and [c.pagina for c in citas] == [2, 3, 6]


def test_pdf_con_enlaces_indice_y_excel(tmp_path):
    docs, paginas = docs_y_paginas()
    pdf = fitz.open()
    for p in paginas:
        pdf.new_page().insert_textbox(fitz.Rect(60, 80, 540, 300), p.texto, fontsize=12)
    rs = resolver(docs, paginas)
    salidas.escribir_pdf(pdf, docs, paginas, tmp_path / "e.pdf", capa_texto=False, nombre="Ejemplo",
                         remisiones=rs)
    salidas.escribir_excel(docs, tmp_path / "e.xlsx", "judicial", rs)
    with fitz.open(tmp_path / "e.pdf") as r:
        n = r.page_count - 6
        hoja = r[n + 1]  # página 2: el recurso cita el auto de la página 1
        subrayados = [a for a in hoja.annots(types=[fitz.PDF_ANNOT_UNDERLINE])]
        assert len(subrayados) == 1 and "Auto" in subrayados[0].info["content"]
        enlace = next(e for e in hoja.get_links() if fitz.Rect(e["from"]).intersects(subrayados[0].rect))
        assert enlace["page"] == n  # va a la página 1 del expediente
        # la cita sin localizar (dos autos del mismo día) no se enlaza
        assert len([a for a in r[n + 5].annots(types=[fitz.PDF_ANNOT_UNDERLINE])]) == 1
        texto = "".join(r[i].get_text() for i in range(n))
        assert "REMISIONES ENTRE DOCUMENTOS (3)" in texto and "citado 3 veces" in texto
        assert [t[1] for t in r.get_toc() if t[0] == 1 and t[1].startswith("Remisiones")] == ["Remisiones (3)"]
    from openpyxl import load_workbook

    h = load_workbook(tmp_path / "e.xlsx")["Remisiones"]
    assert h.max_row == 6 and "Sin localizar" in h.cell(row=4, column=8).value
    # desmarcado en «Contenido del índice»: sin bloque ni marcadores (los enlaces de las páginas siguen)
    pdf2 = fitz.open()
    for p in paginas:
        pdf2.new_page().insert_textbox(fitz.Rect(60, 80, 540, 300), p.texto, fontsize=12)
    salidas.escribir_pdf(pdf2, docs, paginas, tmp_path / "s.pdf", capa_texto=False, remisiones=rs,
                         contenido=("cronologia",))
    with fitz.open(tmp_path / "s.pdf") as r:
        assert "REMISIONES ENTRE DOCUMENTOS" not in "".join(r[i].get_text() for i in range(r.page_count - 6))
        assert [a for a in r[r.page_count - 5].annots(types=[fitz.PDF_ANNOT_UNDERLINE])]
        assert not [t for t in r.get_toc() if t[1].startswith("Remisiones")]


def test_enlace_en_pagina_girada():
    """El subrayado y el enlace caen sobre la misma zona también en una página girada."""
    pdf = fitz.open()
    pdf.new_page()
    hoja = pdf.new_page()
    hoja.insert_text((80, 200), "Visto el auto de 20 de marzo de 2024.", fontsize=12)
    hoja.set_rotation(90)
    docs = [Documento(1, "auto", "", "2024-03-20", "", 1, 1), Documento(2, "oficio", "", "2024-04-01", "", 2, 2)]
    rs = resolver(docs, [PaginaInfo(pagina=2, texto="Visto el auto de 20 de marzo de 2024.")])
    assert salidas._enlazar_remisiones(pdf, rs, 0) == 1
    guardado = fitz.open("pdf", pdf.tobytes())  # como lo verá el lector
    hoja = guardado[1]  # la página debe seguir viva mientras se usan sus anotaciones
    nota = next(hoja.annots(types=[fitz.PDF_ANNOT_UNDERLINE]))
    enlace = hoja.get_links()[0]
    # get_links da la zona en el espacio girado; las anotaciones, en el de la página sin girar
    zona = fitz.Rect(enlace["from"]) * hoja.derotation_matrix
    zona.normalize()
    assert enlace["page"] == 0 and (zona & nota.rect).get_area() > 0.8 * zona.get_area()


def test_remisiones_del_modelo():
    """Lo que señala el modelo pasa los mismos filtros: cita literal y un único documento."""
    docs, paginas = docs_y_paginas()
    docs.append(Documento(6, "informe_pericial", "Informe del perito", "2024-02-10", "", 7, 7))
    paginas.append(PaginaInfo(pagina=7, texto="INFORME PERICIAL. A 10 de febrero de 2024."))
    paginas[1].texto += " Se acompaña lo dicho por el perito en su dictamen de febrero."
    paginas[1].remisiones = [
        {"cita": "el perito en su dictamen de febrero", "clase": "informe_pericial", "fecha": ""},  # único
        {"cita": "auto de 20 de marzo de 2024", "clase": "auto", "fecha": "2024-03-20"},  # ya está (patrón)
        {"cita": "la resolución que nunca se escribió", "clase": "auto", "fecha": "2024-03-20"},  # inventada
        {"cita": "frente al auto", "clase": "auto", "fecha": ""}]  # sin fecha y hay tres autos
    rs = resolver(docs, paginas)
    del_modelo = [(r.frase, r.destino.numero if r.destino else None) for r in rs if r.fuente == "IA"]
    assert del_modelo == [("el perito en su dictamen de febrero", 6), ("frente al auto", None)]
    assert "hay 3 documentos de esa clase" in next(r.motivo for r in rs if r.frase == "frente al auto")
    assert len([r for r in rs if r.pagina == 2 and r.tipo == "auto" and r.destino]) == 1  # sin duplicar


def test_el_clasificador_recoge_las_remisiones():
    """El campo «remisiones» de la respuesta del modelo llega a la página; si falta, lista vacía."""
    from marcadores.ocr_gemini import ESQUEMA, INSTRUCCIONES, AnalizadorGemini, Lote

    assert "remisiones" in ESQUEMA["items"]["required"] and "remisiones:" in INSTRUCCIONES
    a = AnalizadorGemini.__new__(AnalizadorGemini)
    a._consultar = lambda partes, indices: [
        {"pagina": 1, "tipo": "escrito_parte", "remisiones": [
            {"cita": "el auto de 20 de marzo de 2024", "clase": "auto", "fecha": "2024-03-20"}, {"clase": "auto"}]},
        {"pagina": 2, "tipo": "auto"}]
    partes = Lote()
    partes.textos.update({1: "Contra el auto de 20 de marzo de 2024.", 2: "AUTO"})
    partes.lineas.update({1: [], 2: []})
    uno, dos = a.procesar_partes(partes, [0, 1])
    assert uno.remisiones == [{"cita": "el auto de 20 de marzo de 2024", "clase": "auto", "fecha": "2024-03-20"}]
    assert dos.remisiones == [] and PaginaInfo.de_dict({"pagina": 3}).remisiones == []
