import json
import os
import re
import tempfile
from datetime import datetime
from functools import lru_cache
from typing import Callable, Dict, Optional, Tuple

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
            ffmpeg_i_args = []
            if start_norm:
                ffmpeg_i_args += ["-ss", start_norm]
            if end_norm:
                ffmpeg_i_args += ["-to", end_norm]
            ydl_opts.update(
                {
                    "external_downloader": "ffmpeg",
                    "external_downloader_args": {"ffmpeg_i": ffmpeg_i_args},
                }
            )

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
            "success": success,
            "error": download_error,
            "mp3_size_bytes": len(mp3_bytes),
        }

        json_bytes = json.dumps(metadata, ensure_ascii=False, indent=2).encode("utf-8")
        return metadata, mp3_bytes, json_bytes
