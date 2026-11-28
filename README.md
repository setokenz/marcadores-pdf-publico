# Marcadores PDF

Programa para Windows que ordena expedientes judiciales escaneados: lee cada página (OCR),
separa y clasifica los documentos (resoluciones, escritos de parte con quién los presenta,
declaraciones, oficios…), extrae plazos y señalamientos y genera:

- un **PDF con marcadores**, índice enlazado al principio y texto buscable;
- un **Excel** con el índice, la cronología, los plazos y los señalamientos;
- opcionalmente, una carpeta **«RAG»** con un archivo por documento para cargarlo en
  AnythingLLM u otro sistema de preguntas sobre documentos.

El archivo original nunca se modifica.

Creada por **Eduardo Goig Alique · Táctica Legal**.

## Descarga

1. Entre en **[Releases](../../releases/latest)** y descargue `MarcadoresPDF-<versión>.exe`.
2. Guárdelo en una carpeta propia (por ejemplo `Documentos\Marcadores PDF`) y ábralo con doble clic.
   No necesita instalación. La primera vez crea un acceso directo en el escritorio y, a partir de
   entonces, avisa y se actualiza solo cuando hay una versión nueva.

### El aviso de Windows al abrirlo por primera vez («Windows protegió su PC»)

El programa no lleva firma digital de pago, así que Windows no conoce a su editor y lo advierte.
Es normal en programas pequeños distribuidos así. Los textos exactos pueden variar según la
versión de Windows y del navegador:

1. **Al descargarlo**, el navegador puede decir que el archivo «no se descarga normalmente».
   En Microsoft Edge: pulse los **tres puntos (…)** junto a la descarga → **Conservar** →
   **Mostrar más** → **Conservar de todas formas**.
2. **Al abrirlo**, aparece una ventana azul: *«Windows protegió su PC. Microsoft Defender
   SmartScreen impidió el inicio de una aplicación no reconocida»*. Pulse **«Más información»**
   y después **«Ejecutar de todas formas»**. Solo pasa la primera vez.
3. Si su antivirus lo bloquea, puede deberse a la forma en que se empaqueta el programa
   (PyInstaller). Compruebe que lo ha descargado de esta página y añádalo como excepción.

Si prefiere no hacerlo, puede revisar el código de este repositorio y compilarlo usted mismo.

## Qué necesita

El programa no incluye ninguna clave: cada usuario usa sus propias cuentas, que se configuran en
la pestaña **Configuración**. Las claves se guardan solo en su equipo
(`%APPDATA%\MarcadoresPDF`).

- **Una licencia de uso**: la versión oficial funciona con una suscripción de Táctica Legal. La
  clave de la cuenta se pega en **Configuración → Licencia**; la primera activación abre una
  prueba gratuita de 30 días para 3 equipos.
- **Mistral** (recomendado para leer las páginas escaneadas): clave de API de
  console.mistral.ai.
- **Para clasificar**, una de estas opciones:
  - **Gemini** en Google Cloud Vertex AI: proyecto y archivo JSON de una cuenta de servicio;
  - **Mistral**, con la misma clave;
  - **Claude** (Anthropic): clave de API de platform.claude.com.
- Modo **básico sin IA**: solo para PDF que ya tienen texto, sin enviar nada fuera.

## Privacidad y responsabilidad

- Las páginas de los documentos se envían al proveedor de IA que usted elija (Google, Mistral o
  Anthropic) para leerlas y clasificarlas. Valore en cada caso si es compatible con sus
  obligaciones de protección de datos y de secreto profesional.
- El programa no envía documentos ni estadísticas a sus autores. Se conecta solo a GitHub, para
  comprobar si hay versiones nuevas, y al servidor de licencias de Táctica Legal: le envía la clave
  de la cuenta y un identificador aleatorio de este equipo (para el límite de 3 equipos) y recibe
  un justificante firmado que vale 14 días sin conexión.
- La clasificación la hace un modelo de IA y puede contener errores: revise siempre el
  resultado, en especial plazos y fechas. Los plazos se transcriben; no se calcula su vencimiento.

## Licencia

© Eduardo Goig Alique · Táctica Legal. Software libre bajo la licencia **GNU Affero General
Public License v3.0** (archivo `LICENSE`).
Se ofrece **sin ninguna garantía**, en los términos de esa licencia. Puede usarlo, estudiarlo,
modificarlo y redistribuirlo; si distribuye una versión modificada, debe publicarla con la misma
licencia y su código fuente.

Usa, entre otras, estas bibliotecas: PyMuPDF (AGPL-3.0, Artifex), openpyxl (MIT), google-genai y
google-auth (Apache-2.0), anthropic (Apache-2.0), truststore (MIT), tkinterdnd2 (MIT) y cryptography (Apache-2.0 / BSD).

Este repositorio contiene el código de la versión vigente (se publica sin historial); el
desarrollo se hace aparte.
