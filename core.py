import json
import os
import re
import ssl
import subprocess
import tempfile
from datetime import datetime
from typing import Callable, Dict, Optional, Tuple

# Reemplazamos requests por pytubefix
from pytubefix import YouTube

# --- PARCHE SSL ---
# Necesario para macOS local
ssl._create_default_https_context = ssl._create_unverified_context
# ------------------

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
    Procesa audio usando pytubefix con ESTRATEGIA DE FALLBACK.
    Intenta WEB -> ANDROID -> TV para maximizar compatibilidad Local vs Cloud.
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
    used_backend_client = "none"

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
                on_progress({"status": "downloading", "_percent_str": "5%"})

            # --- ESTRATEGIA DE FALLBACK DE CLIENTES ---
            # 1. WEB: Funciona bien en local (IP residencial). Falla en Cloud (PoToken).
            # 2. ANDROID: Suele saltar el bloqueo en Cloud. A veces falla en local (403).
            # 3. TV: Opción de respaldo ("Nuclear option").

            clients_to_try = ["WEB", "ANDROID", "TV"]
            yt = None
            audio_stream = None

            last_exception = None

            for client_type in clients_to_try:
                try:
                    # Intentamos instanciar con el cliente actual
                    print(f"🔄 Intentando con cliente: {client_type}...")
                    yt_candidate = YouTube(url, client=client_type)

                    # Intentamos leer metadata para ver si el cliente tiene acceso real
                    _ = yt_candidate.title

                    # Intentamos obtener streams
                    streams = yt_candidate.streams

                    # Si llegamos aquí sin error, este cliente funciona
                    yt = yt_candidate
                    used_backend_client = client_type
                    print(f"✅ Cliente {client_type} conectado exitosamente.")
                    break
                except Exception as ex:
                    print(f"⚠️ Cliente {client_type} falló: {ex}")
                    last_exception = ex
                    continue

            if not yt:
                raise Exception(
                    f"Todos los clientes fallaron. Último error: {last_exception}"
                )

            video_id = yt.video_id
            original_title = yt.title

            if not used_custom_filename:
                base_name = _sanitize_name(original_title)
                mp3_filename = f"{base_name}.mp3"
                json_filename = f"{base_name}.json"
                final_mp3_path = os.path.join(tmpdir, mp3_filename)

            if on_progress:
                on_progress({"status": "downloading", "_percent_str": "30%"})

            # Selección de stream robusta
            audio_stream = yt.streams.get_audio_only()
            if not audio_stream:
                # Fallback manual de filtro
                audio_streams = (
                    yt.streams.filter(only_audio=True).order_by("abr").desc()
                )
                if audio_streams:
                    audio_stream = audio_streams.first()

            if not audio_stream:
                raise Exception(
                    f"No se encontró audio con cliente {used_backend_client}"
                )

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

            # Forzamos MP3 estéreo
            cmd += ["-acodec", "libmp3lame", "-q:a", "2", "-ac", "2"]
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
            print(f"DEBUG FINAL ERROR: {e}")
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
            "backend": f"pytubefix-fallback-strategy ({used_backend_client})",
        }

        json_bytes = json.dumps(metadata, ensure_ascii=False, indent=2).encode("utf-8")

        return metadata, mp3_bytes, json_bytes
