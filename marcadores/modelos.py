"""Tipos de documento y estructuras de datos."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# Caracteres de control invisibles (salvo tabulador y saltos de línea). Algunos PDF los traen en su
# capa de texto, por ejemplo en lugar de «º», y Excel los rechaza al guardar.
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def limpiar(texto: str) -> str:
    return _CONTROL.sub("", texto or "")

# clave -> nombre visible
TIPOS = {
    "sentencia": "Sentencia",
    "auto": "Auto",
    "providencia": "Providencia",
    "decreto": "Decreto",
    "diligencia_ordenacion": "Diligencia de ordenación",
    "diligencia": "Diligencia",
    "acta": "Acta",
    "declaracion": "Declaración",
    "oficio": "Oficio",
    "exhorto": "Exhorto / auxilio judicial",
    "notificacion": "Notificación / cédula",
    "escrito_parte": "Escrito de parte",
    "informe_pericial": "Informe pericial",
    "atestado": "Atestado / informe policial",
    "documento": "Documento / prueba documental",
    "otros": "Otros",
}

# Índices temáticos: nombre -> tipos que incluye
GRUPOS = {
    "Resoluciones": ["sentencia", "auto", "providencia", "decreto", "diligencia_ordenacion"],
    "Declaraciones": ["declaracion"],
    "Escritos de parte": ["escrito_parte"],
    "Actas": ["acta"],
    "Oficios y auxilio judicial": ["oficio", "exhorto"],
    "Periciales y atestados": ["informe_pericial", "atestado"],
}


PLURALES = {
    "sentencia": "Sentencias",
    "auto": "Autos",
    "providencia": "Providencias",
    "decreto": "Decretos",
    "diligencia_ordenacion": "Diligencias de ordenación",
}


def fecha_es(fecha: str) -> str:
    p = (fecha or "").split("-")
    if len(p) == 3 and all(x.isdigit() for x in p):
        return f"{p[2]}/{p[1]}/{p[0]}"
    return ""


@dataclass
class PaginaInfo:
    pagina: int  # empieza en 1
    texto: str = ""
    lineas: list = field(default_factory=list)  # [[texto, ymin, xmin, ymax, xmax]] en milésimas de página
    inicia_documento: bool = False
    tipo: str = "otros"
    titulo: str = ""
    fecha: str = ""  # AAAA-MM-DD o vacío
    autor: str = ""
    subtipo: str = ""  # clase concreta: «Auto de admisión», «Contestación a la demanda»…
    parte: str = ""  # en escritos de parte: quién lo presenta
    posicion: str = ""  # demandante, demandado, Ministerio Fiscal…
    plazos: list = field(default_factory=list)  # [{concepto, plazo}]
    senalamientos: list = field(default_factory=list)  # [{acto, fecha, hora, lugar}]
    remisiones: list = field(default_factory=list)  # [{cita, clase, fecha}]: lo que dice el modelo
    error: bool = False

    def a_dict(self) -> dict:
        return dict(self.__dict__)

    @classmethod
    def de_dict(cls, d: dict) -> "PaginaInfo":
        campos = cls.__dataclass_fields__
        return cls(**{k: v for k, v in d.items() if k in campos})


@dataclass
class Documento:
    numero: int
    tipo: str
    titulo: str
    fecha: str
    autor: str
    pagina_inicio: int
    pagina_fin: int
    etiquetas: list[str] = field(default_factory=list)
    texto: str = ""
    subtipo: str = ""
    parte: str = ""
    posicion: str = ""
    plazos: list = field(default_factory=list)  # [{concepto, plazo, pagina}]
    senalamientos: list = field(default_factory=list)  # [{acto, fecha, hora, lugar, pagina}]

    @property
    def clase(self) -> str:
        return self.subtipo or self.tipo_nombre

    @property
    def emisor(self) -> str:
        """Órgano o persona que emite el documento; en escritos, la parte y su posición."""
        if self.parte:
            return f"{self.parte} ({self.posicion})" if self.posicion else self.parte
        return self.autor

    @property
    def tipo_nombre(self) -> str:
        return TIPOS.get(self.tipo, "Otros")

    @property
    def fecha_es(self) -> str:
        return fecha_es(self.fecha)

    @property
    def paginas(self) -> str:
        if self.pagina_inicio == self.pagina_fin:
            return str(self.pagina_inicio)
        return f"{self.pagina_inicio}-{self.pagina_fin}"

    @property
    def rotulo(self) -> str:
        partes = [f"{self.numero:03d}", self.tipo_nombre]
        if self.fecha_es:
            partes.append(self.fecha_es)
        if self.titulo and self.titulo.lower() != self.tipo_nombre.lower():
            partes.append(self.titulo)
        return " · ".join(partes)
