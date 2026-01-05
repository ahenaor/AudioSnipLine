import json
import os
import re
import ssl
import subprocess
import tempfile
from datetime import datetime
from typing import Callable, Dict, Optional, Tuple

from pytubefix import YouTube

ssl._create_default_https_context = ssl._create_unverified_context
# -----------------------------

# Idiomas soportados
SUPPORTED_LANGUAGES: Dict[str, str] = {
    "es": "Spanish",
    "en": "English",
    "pt": "Portuguese",
    "de": "German",
    "fr": "French",
    "it": "Italian",
    "ko": "Korean",
    "ca": "Catalan",
    "pl": "Polish",
    "ja": "Japanese",
    "ru": "Russian",
    "uk": "Ukrainian",
}


def _normalize_time(t: str) -> Optional[str]:
    """Accepts mm:ss or hh:mm:ss. Returns hh:mm:ss or None."""
    if not t:
        return None
    t = t.strip()
    if not t:
        return None
    if re.match(r"^\d{1,2}:\d{2}$", t):
        return "00:" + t
    if re.match(r"^\d{1,2}:\d{2}:\d{2}$", t):
        return t
    raise ValueError("Tiempo inválido. Usa mm:ss o hh:mm:ss (ej: 04:34 o 00:04:34).")


def _time_to_seconds(hhmmss: str) -> int:
    h, m, s = (int(x) for x in hhmmss.split(":"))
    return h * 3600 + m * 60 + s


def _sanitize_name(name: str) -> str:
    name = name.strip()
    name = re.sub(r"\s+", " ", name)
    name = re.sub(r"[^a-zA-Z0-9 _-]+", "", name)
    name = name.strip(" ._-")
    return name


def process_audio_job_in_memory(
    url: str,
    custom_filename: str = "",
    start: str = "",
    end: str = "",
    speakers_count: Optional[int] = None,
    language: Optional[str] = None,
    language_code: Optional[str] = None,
    preferredcodec: str = "mp3",
    on_progress: Optional[Callable[[Dict], None]] = None,
) -> Tuple[Dict, bytes, bytes]:
    """
    Procesa audio usando pytubefix (cliente nativo Python) y ffmpeg local.
    Incluye bypass de SSL para evitar errores en macOS.
    """

    if not url or not url.strip():
        raise ValueError("URL no puede estar vacía.")

    execution_ts = datetime.now().strftime("%Y%m%d%H%M%S")
    start_input, end_input = start, end
    start_norm = _normalize_time(start) if start else None
    end_norm = _normalize_time(end) if end else None

    if speakers_count is not None and (
        not isinstance(speakers_count, int) or speakers_count < 1
    ):
        raise ValueError("El número de hablantes debe ser un entero >= 1.")

    if (language is None) ^ (language_code is None):
        raise ValueError(
            "Si seleccionas idioma, debes enviar tanto 'language' como 'language_code'."
        )

    if start_norm and end_norm:
        if _time_to_seconds(end_norm) <= _time_to_seconds(start_norm):
            raise ValueError(
                f"END debe ser mayor que START. (START={start_input}, END={end_input})"
            )

    used_custom_filename = bool(custom_filename and custom_filename.strip())
    used_trim = bool(start_norm or end_norm)

    download_error = None
    success = False
    mp3_bytes = b""
    video_id = "unknown"
    original_title = "Unknown"

    # Nombre base
    base_name = (
        _sanitize_name(custom_filename)
        if used_custom_filename
        else f"audio_{execution_ts}"
    )
    mp3_filename = f"{base_name}.mp3"
    json_filename = f"{base_name}.json"

    with tempfile.TemporaryDirectory(prefix="audiosnipline_") as tmpdir:
        final_mp3_path = os.path.join(tmpdir, mp3_filename)

        try:
            if on_progress:
                on_progress({"status": "downloading", "_percent_str": "10%"})

            # --- FASE 1: PYTUBEFIX EXTRACTION ---
            # Instanciamos el objeto YouTube.
            # client='WEB' suele ser el más estable para evitar throttles
            yt = YouTube(url, client="WEB")

            video_id = yt.video_id
            original_title = yt.title

            # Si el usuario no puso nombre, usamos el título real ahora que lo tenemos
            if not used_custom_filename:
                base_name = _sanitize_name(original_title)
                mp3_filename = f"{base_name}.mp3"
                json_filename = f"{base_name}.json"
                final_mp3_path = os.path.join(tmpdir, mp3_filename)

            if on_progress:
                on_progress({"status": "downloading", "_percent_str": "30%"})

            # Seleccionar el stream de audio (m4a/webm con mejor bitrate)
            audio_stream = yt.streams.get_audio_only()
            if not audio_stream:
                raise Exception("No se encontró stream de audio disponible.")

            if on_progress:
                on_progress({"status": "downloading", "_percent_str": "50%"})

            # Descarga del crudo
            raw_file_path = audio_stream.download(
                output_path=tmpdir, filename_prefix="raw_"
            )

            if on_progress:
                on_progress({"status": "downloading", "_percent_str": "80%"})

            # --- FASE 2: CONVERSIÓN Y RECORTE (FFMPEG) ---

            cmd = ["ffmpeg", "-y", "-i", raw_file_path]
            cmd += ["-hide_banner", "-loglevel", "error"]

            if start_norm:
                cmd += ["-ss", start_norm]
            if end_norm:
                cmd += ["-to", end_norm]

            # Forzamos codec mp3 y bitrate decente
            cmd += ["-acodec", "libmp3lame", "-q:a", "2"]
            cmd.append(final_mp3_path)

            subprocess.run(
                cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )

            if on_progress:
                on_progress({"status": "finished", "_percent_str": "100%"})

            if os.path.exists(final_mp3_path):
                with open(final_mp3_path, "rb") as f:
                    mp3_bytes = f.read()
                success = True

        except Exception as e:
            download_error = str(e)
            success = False

        # Metadata final
        metadata = {
            "url": url,
            "execution_ts": execution_ts,
            "video_id": video_id,
            "original_video_title": original_title,
            "mp3_filename": mp3_filename,
            "json_filename": json_filename,
            "used_custom_filename": used_custom_filename,
            "used_trim": used_trim,
            "start_input": start_input,
            "end_input": end_input,
            "speakers_count": speakers_count,
            "language": language,
            "language_code": language_code,
            "success": success,
            "error": download_error,
            "mp3_size_bytes": len(mp3_bytes),
            "backend": "pytubefix-local-ssl-patched",
        }

        json_bytes = json.dumps(metadata, ensure_ascii=False, indent=2).encode("utf-8")

        return metadata, mp3_bytes, json_bytes
