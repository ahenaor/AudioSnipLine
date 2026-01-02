import json
import os
import re
import tempfile
from datetime import datetime
from functools import lru_cache
from typing import Callable, Dict, Optional, Tuple

# Idiomas soportados para selección manual desde la UI (nombre en inglés + código común)
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


import yt_dlp


def _normalize_time(t: str) -> Optional[str]:
    """Accepts mm:ss or hh:mm:ss. Returns hh:mm:ss or None."""
    if not t:
        return None
    t = t.strip()
    if not t:
        return None

    # mm:ss -> 00:mm:ss
    if re.match(r"^\d{1,2}:\d{2}$", t):
        return "00:" + t

    # hh:mm:ss
    if re.match(r"^\d{1,2}:\d{2}:\d{2}$", t):
        return t

    raise ValueError("Tiempo inválido. Usa mm:ss o hh:mm:ss (ej: 04:34 o 00:04:34).")


def _time_to_seconds(hhmmss: str) -> int:
    """hh:mm:ss -> seconds"""
    h, m, s = (int(x) for x in hhmmss.split(":"))
    return h * 3600 + m * 60 + s


def _sanitize_name(name: str) -> str:
    """Sanitiza nombre para filename (sin paths)."""
    name = name.strip()
    name = re.sub(r"\s+", " ", name)
    name = re.sub(r"[^a-zA-Z0-9 _-]+", "", name)
    name = name.strip(" ._-")
    return name


@lru_cache(maxsize=256)
def _resolve_download_false(url: str) -> Tuple[str, str, Optional[str]]:
    """
    Cachea información mínima desde extract_info(download=False) y un basename seguro
    para construir el output cuando no hay CUSTOM_FILENAME.

    Returns: (safe_base_title, original_video_title, video_id)
    """
    ydl_opts = {"quiet": True, "noplaylist": True}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)

    original_title = info.get("title") or "audio"
    video_id = info.get("id")

    # Sanitización consistente con yt-dlp (prepare_filename) usando el info real
    info_for_name = dict(info)
    info_for_name["ext"] = "mp3"
    with yt_dlp.YoutubeDL(
        {"outtmpl": "%(title)s.%(ext)s", "quiet": True, "noplaylist": True}
    ) as name_ydl:
        safe_path = name_ydl.prepare_filename(info_for_name)

    safe_base_title = os.path.splitext(os.path.basename(safe_path))[0] or "audio"
    return safe_base_title, original_title, video_id


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
    Procesa audio desde YouTube y devuelve:
      - metadata dict
      - mp3_bytes
      - json_bytes

    Nota: yt-dlp/ffmpeg requieren escritura a disco durante el proceso, por eso
    usamos un TemporaryDirectory por ejecución (ephemeral) y luego retornamos bytes.
    """

    if not url or not url.strip():
        raise ValueError("URL no puede estar vacía.")

    execution_ts = datetime.now().strftime("%Y%m%d%H%M%S")

    start_input = start
    end_input = end

    start_norm = _normalize_time(start) if start else None
    end_norm = _normalize_time(end) if end else None

    # Validación opcional: número de hablantes
    if speakers_count is not None:
        # bool es subclase de int; lo excluimos explícitamente
        if isinstance(speakers_count, bool) or not isinstance(speakers_count, int):
            raise ValueError(
                "El número de hablantes debe ser un entero (1, 2, 3, ...)."
            )
        if speakers_count < 1:
            raise ValueError("El número de hablantes debe ser un entero >= 1.")

    # Validación opcional: idioma seleccionado por el usuario
    # Reglas:
    # - Si el usuario no selecciona idioma: language y language_code deben ser None
    # - Si selecciona: ambos deben venir informados y ser consistentes con SUPPORTED_LANGUAGES
    if (language is None) ^ (language_code is None):
        raise ValueError(
            "Si seleccionas idioma, debes enviar tanto 'language' como 'language_code' (o ambos null)."
        )

    if language is not None and language_code is not None:
        if not isinstance(language, str) or not isinstance(language_code, str):
            raise ValueError("El idioma debe ser texto y el código debe ser texto.")
        if language_code not in SUPPORTED_LANGUAGES:
            raise ValueError(f"Código de idioma no soportado: {language_code!r}.")
        expected = SUPPORTED_LANGUAGES[language_code]
        if language != expected:
            raise ValueError(
                f"Inconsistencia de idioma: para {language_code!r} se esperaba {expected!r}, pero llegó {language!r}."
            )

    # Validación END > START (cuando ambos están presentes)
    if start_norm and end_norm:
        if _time_to_seconds(end_norm) <= _time_to_seconds(start_norm):
            raise ValueError(
                f"END debe ser mayor que START. (START={start_input!r}, END={end_input!r})"
            )

    used_custom_filename = bool(custom_filename and custom_filename.strip())
    used_trim = bool(start_norm or end_norm)

    def progress_hook(d: Dict):
        if on_progress:
            on_progress(d)

    download_error = None

    safe_base_title, original_title, video_id = _resolve_download_false(url)

    if used_custom_filename:
        base_name = _sanitize_name(custom_filename)
        if not base_name:
            raise ValueError("El nombre de salida quedó vacío tras sanitizarlo.")
    else:
        base_name = safe_base_title

    mp3_filename = f"{base_name}.{preferredcodec}"
    json_filename = f"{base_name}.json"

    with tempfile.TemporaryDirectory(prefix="audiosnipline_") as tmpdir:
        outtmpl = (
            os.path.join(tmpdir, f"{base_name}.%(ext)s")
            if used_custom_filename
            else os.path.join(tmpdir, "%(title)s.%(ext)s")
        )
        final_mp3_path = os.path.join(tmpdir, mp3_filename)

        # “Mejor calidad posible” + fallback; extracción a mp3 V0
        ydl_opts = {
            "format": "bestaudio[ext=m4a]/bestaudio/best[ext=mp4]/best",
            "outtmpl": outtmpl,
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": preferredcodec,
                    "preferredquality": "0",  # V0 (alta calidad)
                }
            ],
            "noplaylist": True,
            "progress_hooks": [progress_hook],
        }

        # Recorte “durante descarga” con ffmpeg si aplica
        if used_trim:
            trim_args = []
            if start_norm:
                trim_args += ["-ss", start_norm]
            if end_norm:
                trim_args += ["-to", end_norm]

            # Pasamos los argumentos directamente al postprocesador
            # en lugar de usar ffmpeg como descargador externo
            ydl_opts["postprocessor_args"] = trim_args
        # if used_trim:
        #    ffmpeg_i_args = []
        #    if start_norm:
        #        ffmpeg_i_args += ["-ss", start_norm]
        #    if end_norm:
        #        ffmpeg_i_args += ["-to", end_norm]
        #    ydl_opts.update(
        #        {
        #            "external_downloader": "ffmpeg",
        #            "external_downloader_args": {"ffmpeg_i": ffmpeg_i_args},
        #        }
        #    )

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                _ = ydl.extract_info(url, download=True)
        except Exception as e:
            download_error = repr(e)

        success = os.path.exists(final_mp3_path) and download_error is None

        mp3_bytes = b""
        if os.path.exists(final_mp3_path):
            with open(final_mp3_path, "rb") as f:
                mp3_bytes = f.read()

        # Metadata limpia (sin duplicados innecesarios)
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
        }

        json_bytes = json.dumps(metadata, ensure_ascii=False, indent=2).encode("utf-8")
        return metadata, mp3_bytes, json_bytes
