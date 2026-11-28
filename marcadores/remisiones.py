"""Remisiones entre documentos del mismo expediente («el auto de 3 de marzo de 2024»).

Dos fuentes, que se suman:
- patrones de texto, sin IA: los escritos forenses citan las resoluciones de forma muy regular
  (clase y fecha);
- el modelo que clasifica, que además de las anteriores entiende «la resolución recurrida» o
  «su escrito de 3 de abril» y cita otras clases de documento (escritos, oficios, informes).

En los dos casos el documento citado se busca en el expediente por su clase y su fecha.

Regla estricta, porque un enlace equivocado es peor que ninguno: solo se enlaza cuando hay
UN ÚNICO documento de esa clase y esa fecha. Si no hay ninguno (no está en el expediente, o
el OCR leyó mal la fecha) o hay varios (dos autos del mismo día), la remisión queda anotada
como «sin localizar» y no se enlaza.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .clasificador_local import MESES, sin_tildes
from .modelos import Documento, PaginaInfo

# Cómo se nombra cada clase en un escrito -> tipo interno. «autos» (el procedimiento) no cuenta.
CLASES = {
    "sentencia": "sentencia",
    "auto": "auto",
    "providencia": "providencia",
    "decreto": "decreto",
    "diligencia de ordenación": "diligencia_ordenacion",
    "diligencia de ordenacion": "diligencia_ordenacion",
}
_FECHA = (r"(?P<dia>\d{1,2})\s+de\s+(?P<mes>[a-záéíóú]+)\s+de(?:l)?\s+(?P<anio>\d{4})"
          r"|(?P<d2>\d{1,2})[/.-](?P<m2>\d{1,2})[/.-](?P<a2>\d{4})")
# clase + (número opcional) + conector + fecha, todo en la misma frase
PATRON = re.compile(
    r"\b(?P<clase>sentencia|auto|providencia|decreto|diligencia\s+de\s+ordenaci[oó]n)\b"
    r"(?:\s+(?:n[.ºo°úu]*m?\.?\s*)?\d+/\d{2,4})?,?"
    r"\s+(?:de\s+fecha\s+|del\s+d[ií]a\s+|dictad[oa]\s+(?:el\s+(?:d[ií]a\s+)?|con\s+fecha\s+|en\s+fecha\s+)"
    r"|fechad[oa]\s+(?:el\s+)?|de\s+|del\s+)"
    r"(?P<fecha>" + _FECHA + r")", re.IGNORECASE)


@dataclass
class Remision:
    origen: Documento  # documento que cita
    pagina: int  # página (del expediente) donde está la cita
    frase: str  # texto literal de la cita, con un solo espacio entre palabras
    tipo: str  # clase citada
    fecha: str  # AAAA-MM-DD
    destino: Documento | None = None  # documento citado; None = sin localizar
    motivo: str = ""  # por qué no se ha localizado
    fuente: str = "patrón"  # patrón | IA


def _fecha(m: re.Match) -> str:
    if m.group("dia"):
        mes = MESES.get(sin_tildes(m.group("mes").lower()))
        dia, anio = int(m.group("dia")), int(m.group("anio"))
    else:
        mes, dia, anio = int(m.group("m2")), int(m.group("d2")), int(m.group("a2"))
    if not mes or not 1 <= mes <= 12 or not 1 <= dia <= 31 or not 1900 <= anio <= 2100:
        return ""
    return f"{anio:04d}-{mes:02d}-{dia:02d}"


def buscar(texto: str) -> list[tuple[str, str, str]]:
    """Citas de una resolución con su fecha: [(tipo, fecha AAAA-MM-DD, frase literal)]."""
    halladas = []
    for m in PATRON.finditer(texto):
        fecha = _fecha(m)
        clase = " ".join(sin_tildes(m.group("clase").lower()).split())
        if fecha:
            halladas.append((CLASES[clase], fecha, " ".join(m.group(0).split())))
    return halladas


def resolver(docs: list[Documento], paginas: list[PaginaInfo]) -> list[Remision]:
    """Remisiones del expediente, enlazadas a su documento cuando es inequívoco."""
    por_clave: dict[tuple[str, str], list[Documento]] = {}
    for d in docs:
        if d.fecha:
            por_clave.setdefault((d.tipo, d.fecha), []).append(d)
    remisiones, vistas = [], set()
    for p in paginas:
        origen = next((d for d in docs if d.pagina_inicio <= p.pagina <= d.pagina_fin), None)
        if origen is None or not p.texto:
            continue
        for tipo, fecha, frase in buscar(p.texto):
            if (origen.tipo, origen.fecha) == (tipo, fecha):
                continue  # el documento se nombra a sí mismo («AUTO … de 3 de marzo de 2024»)
            if (p.pagina, frase.lower()) in vistas:
                continue
            vistas.add((p.pagina, frase.lower()))
            candidatos = [d for d in por_clave.get((tipo, fecha), []) if d is not origen]
            r = Remision(origen, p.pagina, frase, tipo, fecha)
            if len(candidatos) == 1:
                r.destino = candidatos[0]
            else:
                r.motivo = ("no está en el expediente (o su fecha se leyó de otra forma)" if not candidatos
                            else f"hay {len(candidatos)} documentos de esa clase y fecha")
            remisiones.append(r)
    return sorted(remisiones + _del_modelo(docs, paginas, remisiones), key=lambda r: r.pagina)


def _norma(s: str) -> str:
    return " ".join(sin_tildes(s).lower().split())


def _del_modelo(docs: list[Documento], paginas: list[PaginaInfo], ya: list[Remision]) -> list[Remision]:
    """Remisiones que ha señalado el modelo, con tres filtros antes de darlas por buenas:

    1. la cita tiene que estar, literal, en el texto de la página (si el modelo la ha
       parafraseado o inventado, se descarta);
    2. clase y fecha válidas, y distinto del propio documento;
    3. la misma regla estricta: un único documento de esa clase (y esa fecha, si la da).
       Sin fecha solo se acepta si en todo el expediente hay un único documento de esa clase.
    Lo que los patrones ya han encontrado en esa página no se repite.
    """
    from .modelos import TIPOS

    nuevas, vistas = [], {(r.pagina, r.tipo, r.fecha) for r in ya}
    frases = {(r.pagina, _norma(r.frase)) for r in ya}
    for p in paginas:
        origen = next((d for d in docs if d.pagina_inicio <= p.pagina <= d.pagina_fin), None)
        if origen is None or not p.remisiones or not p.texto:
            continue
        texto = _norma(p.texto)
        for x in p.remisiones:
            cita, tipo, fecha = " ".join(str(x.get("cita", "")).split()), x.get("clase", ""), x.get("fecha", "")
            if tipo not in TIPOS or tipo == "otros" or len(cita) < 6 or _norma(cita) not in texto:
                continue
            if fecha and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", fecha):
                fecha = ""
            if (p.pagina, tipo, fecha) in vistas or (p.pagina, _norma(cita)) in frases:
                continue
            if any(_norma(cita) in f or f in _norma(cita) for pag, f in frases if pag == p.pagina):
                continue  # es la misma cita que ya halló el patrón, con alguna palabra más o menos
            vistas.add((p.pagina, tipo, fecha))
            frases.add((p.pagina, _norma(cita)))
            candidatos = [d for d in docs if d.tipo == tipo and d is not origen and (not fecha or d.fecha == fecha)]
            r = Remision(origen, p.pagina, cita, tipo, fecha, fuente="IA")
            if len(candidatos) == 1:
                r.destino = candidatos[0]
            elif not candidatos:
                r.motivo = "no está en el expediente (o su fecha se leyó de otra forma)"
            else:
                r.motivo = (f"hay {len(candidatos)} documentos de esa clase" + (" y fecha" if fecha else
                            "; la cita no da la fecha"))
            nuevas.append(r)
    return nuevas


def citado_por(remisiones: list[Remision]) -> list[tuple[Documento, list[Remision]]]:
    """Sentido inverso: cada documento citado con quienes lo citan, en el orden del expediente."""
    grupos: dict[int, tuple[Documento, list[Remision]]] = {}
    for r in remisiones:
        if r.destino is not None:
            grupos.setdefault(r.destino.numero, (r.destino, []))[1].append(r)
    return [grupos[n] for n in sorted(grupos)]
