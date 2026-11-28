"""Exportación para RAG (AnythingLLM u otro sistema de preguntas sobre documentos).

Por cada expediente se crea, dentro de su carpeta de resultados:

- una carpeta «RAG» con un archivo Markdown por documento, con nombre descriptivo (es lo que
  AnythingLLM muestra como fuente de cada respuesta). Cada archivo empieza con los datos del
  documento y luego su texto página a página. Cada página lleva delante una línea con el
  documento, su fecha y el número de página: así cualquier fragmento que el RAG recupere sabe
  de qué documento y de qué página procede, aunque el troceo lo separe de la cabecera.
- un archivo «… - rag.jsonl» con un registro por documento (datos y texto por página), para
  otros sistemas (Qdrant, n8n…) que quieran los datos estructurados.

Los números de página son los del expediente en el PDF con marcadores (los del índice).
"""
from __future__ import annotations

import json
from pathlib import Path

from .modelos import Documento, PaginaInfo, fecha_es, limpiar
from .nombres import rellenar

CARPETA = "RAG"


def _nombre_archivo(d: Documento) -> str:
    titulo = d.titulo if d.titulo and d.titulo.lower() != d.clase.lower() else ""
    valores = {"num": f"{d.numero:03d}", "clase": d.clase, "fecha": d.fecha or "sin fecha",
               "titulo": titulo}
    return rellenar("{num} - {clase} - {fecha} - {titulo}", valores, "{num} - {clase}")[:110] + ".md"


def _datos(d: Documento, expediente: str, ambito: str) -> list[tuple[str, str]]:
    filas = [("Expediente", expediente), ("Tipo de expediente", ambito),
             ("Documento", f"nº {d.numero:03d} de {expediente}"), ("Tipo", d.tipo_nombre),
             ("Clase", d.clase), ("Título", d.titulo), ("Fecha", d.fecha_es),
             ("Órgano o parte", d.emisor), ("Páginas del expediente", d.paginas)]
    if d.etiquetas:
        filas.append(("Palabras clave", ", ".join(d.etiquetas)))
    for x in d.plazos:
        filas.append(("Plazo", f"{x.get('plazo', '')}: {x.get('concepto', '')} (pág. {x.get('pagina', '')})"))
    for x in d.senalamientos:
        cuando = " ".join(t for t in (fecha_es(x.get("fecha", "")) or x.get("fecha", ""), x.get("hora", "")) if t)
        lugar = f", {x['lugar']}" if x.get("lugar") else ""
        filas.append(("Señalamiento", f"{cuando}: {x.get('acto', '')}{lugar} (pág. {x.get('pagina', '')})"))
    return [(k, limpiar(str(v))) for k, v in filas if str(v).strip()]


def escribir_rag(docs: list[Documento], paginas: list[PaginaInfo], carpeta: Path, expediente: str,
                 ambito: str = "judicial", jsonl: Path | None = None) -> Path:
    """Escribe la exportación y devuelve la carpeta «RAG». Rehace su contenido en cada pasada."""
    destino = carpeta / CARPETA
    destino.mkdir(parents=True, exist_ok=True)
    for viejo in destino.glob("*.md"):  # de una pasada anterior: la numeración puede cambiar
        viejo.unlink()
    por_numero = {p.pagina: p for p in paginas}
    registros = []
    usados: set[str] = set()
    for d in docs:
        datos = _datos(d, expediente, ambito)
        etiqueta = " · ".join(t for t in (f"Doc. {d.numero:03d}", d.clase, d.fecha_es, expediente) if t)
        lineas = [f"# {d.rotulo}", ""]
        lineas += [f"- **{k}:** {v}" for k, v in datos]
        lineas.append("")
        textos = []
        for n in range(d.pagina_inicio, d.pagina_fin + 1):
            texto = limpiar((por_numero.get(n).texto if por_numero.get(n) else "") or "").strip()
            textos.append({"pagina": n, "texto": texto})
            lineas += [f"## Página {n}", f"[{etiqueta} · pág. {n}]", "",
                       texto or "[página sin texto legible]", ""]
        nombre = _nombre_archivo(d)
        while nombre.lower() in usados:
            nombre = nombre[:-3] + "_.md"
        usados.add(nombre.lower())
        (destino / nombre).write_text("\n".join(lineas), encoding="utf-8")
        registros.append({
            "expediente": expediente, "tipo_expediente": ambito, "numero": d.numero,
            "archivo": nombre, "tipo": d.tipo, "tipo_nombre": d.tipo_nombre, "clase": d.clase,
            "titulo": d.titulo, "fecha": d.fecha, "organo_o_parte": d.emisor, "autor": d.autor,
            "parte": d.parte, "posicion": d.posicion, "pagina_inicio": d.pagina_inicio,
            "pagina_fin": d.pagina_fin, "etiquetas": d.etiquetas, "plazos": d.plazos,
            "senalamientos": d.senalamientos, "paginas": textos})
    if jsonl is not None:
        with open(jsonl, "w", encoding="utf-8") as f:
            for r in registros:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return destino
