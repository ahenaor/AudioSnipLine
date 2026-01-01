# AudioSnipLine

**AudioSnipLine** es un *side project* **engineering-first** construido para convertir videos de YouTube a **MP3** (con recorte opcional por ventana temporal) y generar un **JSON de metadata** por corrida, orientado a **trazabilidad y linaje** (data lineage) en flujos de trabajo de datos.

El proyecto está diseñado como un mini-pipeline: una **UI en Streamlit** (`app.py`) y un **motor desacoplado** (`core.py`) que orquesta `yt-dlp` + `ffmpeg` en un **workspace efímero**. El servidor **no persiste artefactos** en un directorio “final”: el MP3/JSON se generan temporalmente, se cargan a memoria y se entregan al usuario vía descargas.

---

## Principios de diseño

- **Engineering-first:** separación UI / lógica de negocio, validaciones explícitas, naming determinista.
- **Outputs portables:** MP3 + JSON; el JSON es un “compañero” natural del audio para auditoría.
- **Sin persistencia final en servidor:** los artefactos se generan en un directorio temporal por ejecución y se devuelven como bytes para descarga.
- **Calidad “best effort”:** el pipeline intenta la mejor fuente de audio disponible y cae a alternativas cuando es necesario.

---

## Features

- ✅ Descarga de audio desde YouTube usando `yt-dlp` con estrategia de fallback:
  - `bestaudio[ext=m4a] / bestaudio / best[ext=mp4] / best`
- ✅ Recorte opcional de audio **durante el proceso** (sin pasos manuales posteriores):
  - `START` y `END` en formato `mm:ss` o `hh:mm:ss`
  - Validación: **END > START** (cuando ambos están presentes)
- ✅ Extracción a **MP3** vía FFmpeg (`FFmpegExtractAudio`) con calidad alta por defecto (V0).
- ✅ Saneamiento de nombres de archivo (para evitar caracteres problemáticos y lograr determinismo).
- ✅ Descargas desde la UI:
  - ZIP (MP3 + JSON)
  - MP3 individual
  - JSON individual
- ✅ JSON de metadata por audio (limpio, sin duplicados innecesarios).

---

## Arquitectura

**1) UI (Streamlit) — `app.py`**
- Captura inputs: URL, nombre opcional, ventana temporal (START/END).
- Dispara el procesamiento.
- Renderiza resultados en el orden:
  1) Resultado  
  2) Tamaños (MP3/JSON/ZIP)  
  3) Descargas  
  4) Metadata  

**2) Motor — `core.py`**
- Normaliza y valida tiempos (`mm:ss` → `hh:mm:ss`; END > START).
- Resuelve naming seguro (y cachea `download=False` para rapidez en reruns).
- Ejecuta `yt-dlp` + `ffmpeg` en un `TemporaryDirectory`.
- Retorna:
  - `metadata` (dict)
  - `mp3_bytes`
  - `json_bytes`

**3) Entrega**
- El servidor no guarda archivos “finales”.
- La UI construye un ZIP en memoria y habilita descargas.

---

## Estructura del repositorio

```
.
├── app.py          # UI Streamlit
├── core.py         # Motor: yt-dlp + ffmpeg + metadata
├── requirements.txt
├── LICENSE
└── README.md
```

---

## Requisitos

- **Python**: recomendado **3.11+** (debería funcionar en 3.10+, pero 3.11+ es la base sugerida).
- **FFmpeg**: instalado y disponible en `PATH`.
- Dependencias Python:
  - `streamlit`
  - `yt-dlp`

> **Verificación rápida**
```bash
python --version
ffmpeg -version
```

---

## Instalación (clonar + entorno virtual)

### 1) Clonar
```bash
git clone https://github.com/<TU_USUARIO>/audiosnipline.git
cd audiosnipline
```

### 2) Crear y activar un virtualenv

#### macOS / Linux (bash/zsh)
```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

#### Windows (PowerShell)
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

---

## Instalar FFmpeg (por sistema operativo)

### macOS (Homebrew)
```bash
brew install ffmpeg
ffmpeg -version
```

### Ubuntu / Debian
```bash
sudo apt-get update
sudo apt-get install -y ffmpeg
ffmpeg -version
```

### Windows (opciones comunes)

**Winget**
```powershell
winget install Gyan.FFmpeg
ffmpeg -version
```

**Chocolatey**
```powershell
choco install ffmpeg
ffmpeg -version
```

> Si `ffmpeg -version` no funciona, el problema casi siempre es **PATH**.

---

## Ejecutar la app

```bash
streamlit run app.py
```

Abre el navegador en la URL que Streamlit muestre en consola (típicamente `http://localhost:8501`).

---

## Uso

- **URL de YouTube**: obligatorio
- **Nombre del archivo de salida (opcional)**:
  - Si lo dejas vacío, se usa el título original del video (sanitizado).
  - Si lo defines, se genera `CUSTOM_NAME.mp3` y `CUSTOM_NAME.json`.
- **START / END (opcional)**:
  - Formatos soportados: `mm:ss` o `hh:mm:ss`
  - Ejemplos: `04:34`, `00:04:34`, `10:27`
  - Si defines ambos, se valida **END > START**.

---

## Outputs (descargas)

Cuando el proceso finaliza, la UI ofrece:

- **ZIP**: `{basename}.zip` contiene:
  - `{basename}.mp3`
  - `{basename}.json`
- Descargas individuales:
  - MP3
  - JSON

> Importante: el servidor **no persiste** un directorio de salida “final”.  
> Los artefactos existen temporalmente durante la ejecución y luego se entregan como bytes.

---

## Metadata JSON (schema)

Ejemplo típico:

```json
{
  "url": "https://www.youtube.com/watch?v=...",
  "execution_ts": "20251230235528",
  "video_id": "obyArPUIffg",
  "original_video_title": "Ultimo video",
  "mp3_filename": "Mi_Audio.mp3",
  "json_filename": "Mi_Audio.json",
  "used_custom_filename": true,
  "used_trim": true,
  "start_input": "04:34",
  "end_input": "10:27",
  "speakers_count": null,
  "success": true,
  "error": null,
  "mp3_size_bytes": 7609846
}
```

Notas:
- `speakers_count` es opcional: si no se especifica, queda en `null`.
- `original_video_title` mantiene el título original para trazabilidad (aunque uses nombre custom).
- `start_input`/`end_input` guardan exactamente lo que el usuario escribió.
- `success/error` permiten auditoría y debugging rápido.

---

## Troubleshooting

### 1) “ffmpeg not found”
- Asegura que FFmpeg está instalado y en el `PATH`:
  ```bash
  ffmpeg -version
  ```

### 2) Errores 403 / cambios de YouTube
YouTube cambia con frecuencia. Si tienes fallos intermitentes:
- Actualiza `yt-dlp`:
  ```bash
  pip install -U yt-dlp
  ```
- Verifica si el video requiere autenticación/edad/región.
- Prueba con otra URL para descartar restricciones del contenido.

### 3) El recorte no parece exacto
- Revisa formato de `START/END` (mm:ss o hh:mm:ss).
- Si `END <= START`, la app debe fallar con validación.

---

## Consideraciones legales y de uso

AudioSnipLine es una herramienta técnica. Asegúrate de respetar derechos de autor, licencias y los términos aplicables del contenido que proceses.

---

## Autor

**Alejandro Henao Ruiz**  
- GitHub: https://github.com/ahenaor  
- LinkedIn: https://www.linkedin.com/in/ahenaor/
