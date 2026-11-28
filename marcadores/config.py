"""Configuración guardada del usuario."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict
from pathlib import Path

from .nombres import POR_DEFECTO as NOMBRES

UBICACIONES_UE = [
    "eu",  # multirregión Unión Europea (los datos no salen de la UE)
    "europe-southwest1",  # Madrid
    "europe-west1",  # Bélgica
    "europe-west4",  # Países Bajos
    "europe-west3",  # Fráncfort
    "europe-west9",  # París
    "europe-west8",  # Milán
]

MODELO_POR_DEFECTO = "gemini-3.5-flash"  # estable, en la multirregión eu y admite razonamiento LOW
# Modelos que se prueban, por este orden, al pulsar «Probar conexión».
MODELOS_CANDIDATOS = ["gemini-3.5-flash", "gemini-3.8-flash", "gemini-3.5-flash-lite"]

PALABRAS_EJEMPLO = (
    "# Una etiqueta por línea:  Etiqueta: palabra o frase, otra palabra\n"
    "Medidas cautelares: medida cautelar, medidas cautelares\n"
    "Costas: tasación de costas, condena en costas\n"
    "Recursos: recurso de apelación, recurso de reposición, recurso de reforma\n"
)


# El modelo admite 1.048.576 tokens de entrada pero solo 65.536 de salida, y la transcripción
# completa de una página densa, con la posición de cada línea, ocupa unos 2.000. 15 páginas deja margen; si una
# respuesta se trunca, el lote se parte solo por la mitad.
PAGINAS_POR_LOTE = 8
PAGINAS_POR_LOTE_MAX = 40
CONSULTAS_PARALELAS = 4  # consultas simultáneas a Gemini
CONSULTAS_PARALELAS_MAX = 8


def carpeta_por_defecto() -> Path:
    return Path.home() / "Documents" / "Marcadores PDF"


@dataclass
class Config:
    proyecto: str = ""
    ubicacion: str = "eu"
    modelo: str = MODELO_POR_DEFECTO
    credenciales: str = ""  # ruta del JSON de la cuenta de servicio
    paginas_por_lote: int = PAGINAS_POR_LOTE
    consultas_paralelas: int = CONSULTAS_PARALELAS
    carpeta_salida: str = ""  # vacío = carpeta por defecto
    ppp: int = 150
    palabras_clave: str = PALABRAS_EJEMPLO
    capa_texto: bool = True
    usar_texto_existente: bool = True  # aprovechar el texto que ya trae el PDF
    motor_ocr: str = "mistral"  # gemini | mistral: quién lee las páginas escaneadas
    mistral_clave: str = ""
    mistral_servidor: str = "https://api.eu.mistral.ai"
    mistral_modelo: str = "mistral-ocr-latest"
    mistral_economico: bool = False  # OCR por lotes: la mitad de precio, pero puede tardar horas
    motor_clasificacion: str = "claude"  # gemini | mistral | claude: quién clasifica los documentos
    mistral_modelo_clasificacion: str = "mistral-medium-latest"
    claude_clave: str = ""  # API de Anthropic: aquí se aplican los créditos de Max / Ultra / Team
    claude_modelo: str = "claude-haiku-5-5"
    nombre_carpeta: str = NOMBRES["carpeta"]
    nombre_pdf: str = NOMBRES["pdf"]
    nombre_excel: str = NOMBRES["excel"]
    nombre_documento: str = NOMBRES["documento"]
    acceso_directo: str = ""  # ejecutable al que apunta el acceso del escritorio
    buscar_actualizaciones: bool = True
    token_github: str = ""  # solo si el repositorio es privado
    indice_en_pdf: bool = True
    tipo_expediente: str = "auto"  # auto | judicial | extrajudicial
    enderezar: bool = True  # enderezar páginas escaneadas de lado o boca abajo
    quitar_blancas: bool = False  # retirar páginas en blanco (cambia la numeración)
    ocultar_presentacion: bool = False  # «No volver a mostrar» en la pantalla de presentación
    # contenido del índice y de los marcadores (la lista de todos los documentos va siempre)
    indice_cronologia: bool = True
    indice_grupos: bool = True
    indice_agenda: bool = True
    indice_remisiones: bool = True
    indice_palabras: bool = True
    navegacion: bool = True  # botones en las páginas: volver al índice y saltar entre documentos
    exportar_rag: bool = True  # carpeta «RAG» con un Markdown por documento, para AnythingLLM
    separar: bool = False
    unir_todo: bool = False
    modo: str = "gemini"  # gemini | basico
    # licencia de uso (ver licencia.py): clave de la cuenta, identificador de este equipo y
    # último testigo firmado que ha dado el servidor (vale 14 días sin conexión)
    licencia_clave: str = ""
    licencia_huella: str = ""
    licencia_testigo: str = ""


def ruta_config() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / ".config")
    return Path(base) / "MarcadoresPDF" / "config.json"


def cargar() -> Config:
    try:
        datos = json.loads(ruta_config().read_text(encoding="utf-8"))
        campos = Config.__dataclass_fields__
        cfg = Config(**{k: v for k, v in datos.items() if k in campos})
        if cfg.modelo.startswith(("gemini-1.", "gemini-2.")):  # retirados de Vertex AI
            cfg.modelo = MODELO_POR_DEFECTO
        if "usar_texto_existente" not in datos and cfg.modelo == "gemini-3.8-flash":
            cfg.modelo = MODELO_POR_DEFECTO  # cambio de modelo por defecto (3.8 se quedaba parado)
        if "consultas_paralelas" not in datos and datos.get("paginas_por_lote") in (6, 15, 20):
            cfg.paginas_por_lote = PAGINAS_POR_LOTE  # valores por defecto de versiones anteriores
        return cfg
    except Exception:
        return Config()


def guardar(cfg: Config) -> None:
    ruta = ruta_config()
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(json.dumps(asdict(cfg), ensure_ascii=False, indent=2), encoding="utf-8")
