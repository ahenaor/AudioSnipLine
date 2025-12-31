import io
import os
import zipfile

import streamlit as st

from core import process_audio_job_in_memory

STATE_RESULT = "result_payload"


def fmt_mb(n_bytes: int) -> str:
    return f"{n_bytes / (1024 * 1024):.2f} MB"


def clear_results():
    st.session_state.pop(STATE_RESULT, None)


st.set_page_config(page_title="AudioSnipLine", layout="centered")

# --- Header (centrado) ---
st.markdown(
    "<h1 style='text-align:center; margin-bottom: 0.25rem;'>AudioSnipLine</h1>",
    unsafe_allow_html=True,
)

st.markdown(
    """
    <p style='text-align:center; color:#555; margin-top:0;'>
      Side project <b>engineering-first</b> para extracción y recorte selectivo de audio con Python:
      <b>FFmpeg</b> para trimming, saneamiento de nombres y <b>metadata JSON</b> para trazabilidad y linaje por corrida.
      <br/>
      <span style="font-size: 0.95em;">
        by <b>Alejandro Henao Ruiz</b> —
        <a href="https://github.com/ahenaor" target="_blank" rel="noopener noreferrer">GitHub</a> •
        <a href="https://www.linkedin.com/in/ahenaor/" target="_blank" rel="noopener noreferrer">LinkedIn</a>
      </span>
    </p>
    """,
    unsafe_allow_html=True,
)

st.divider()

# =========================
# Inputs
# =========================
st.subheader("Parámetros")

url = st.text_input(
    "URL de YouTube",
    value="https://www.youtube.com/watch?v=obyArPUIffg",
    placeholder="Pega aquí la URL del video",
)

custom_filename = st.text_input(
    "Nombre del archivo de salida (opcional)",
    value="CodinEric_Reflexion_Laboral_Interesante",
    help="Si lo dejas vacío, se usa el título del video (sanitizado).",
    placeholder="Ej: CodinEric_Reflexion_Laboral_Interesante",
)

c1, c2 = st.columns(2)
with c1:
    start = st.text_input(
        "Inicio del recorte (opcional)",
        value="04:34",
        help="Formato: mm:ss o hh:mm:ss. Ej: 04:34 o 00:04:34",
        placeholder="mm:ss (ej: 04:34)",
    )
with c2:
    end = st.text_input(
        "Fin del recorte (opcional)",
        value="10:27",
        help="Formato: mm:ss o hh:mm:ss. Ej: 10:27 o 00:10:27",
        placeholder="mm:ss (ej: 10:27)",
    )

st.divider()

# --- Botón centrado ---
btn_col1, btn_col2, btn_col3 = st.columns([1, 2, 1])
with btn_col2:
    run = st.button("Procesar", type="primary", use_container_width=True)

# Placeholders de progreso (NO se renderiza nada hasta que haya run)
progress_placeholder = st.empty()
status_placeholder = st.empty()

# =========================
# Procesamiento
# =========================
if run:
    clear_results()

    # Renderizar progreso solo aquí
    progress_bar = progress_placeholder.progress(0)

    def on_progress(d: dict):
        status = d.get("status")

        if status == "downloading":
            p = d.get("_percent_str", "").strip().replace("%", "")
            try:
                pct = float(p)
                progress_bar.progress(min(max(int(pct), 0), 100))
            except Exception:
                pass
            status_placeholder.info("Procesando… (descargando y preparando audio)")

        elif status == "finished":
            progress_bar.progress(100)
            status_placeholder.info("Post-procesando…")

        elif status == "error":
            status_placeholder.error("Ocurrió un error durante el procesamiento.")

    try:
        with st.spinner("Procesando…"):
            metadata, mp3_bytes, json_bytes = process_audio_job_in_memory(
                url=url,
                custom_filename=custom_filename,
                start=start,
                end=end,
                preferredcodec="mp3",
                on_progress=on_progress,
            )

        # Limpieza inmediata de UI de progreso
        status_placeholder.empty()
        progress_placeholder.empty()

        mp3_filename = metadata.get("mp3_filename") or "audio.mp3"
        json_filename = metadata.get("json_filename") or "audio.json"
        zip_name = os.path.splitext(mp3_filename)[0] + ".zip"

        # ZIP en memoria
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(mp3_filename, mp3_bytes)
            zf.writestr(json_filename, json_bytes)
        zip_bytes = zip_buffer.getvalue()

        st.session_state[STATE_RESULT] = {
            "metadata": metadata,
            "mp3_bytes": mp3_bytes,
            "json_bytes": json_bytes,
            "zip_bytes": zip_bytes,
            "mp3_filename": mp3_filename,
            "json_filename": json_filename,
            "zip_name": zip_name,
        }

        st.rerun()

    except Exception as e:
        status_placeholder.empty()
        progress_placeholder.empty()
        st.exception(e)

# =========================
# Render de resultados (si existen)
# =========================
payload = st.session_state.get(STATE_RESULT)

if payload:
    metadata = payload["metadata"]
    mp3_bytes = payload["mp3_bytes"]
    json_bytes = payload["json_bytes"]
    zip_bytes = payload["zip_bytes"]
    mp3_filename = payload["mp3_filename"]
    json_filename = payload["json_filename"]
    zip_name = payload["zip_name"]

    # Botón de limpieza manual (determinista)
    clean_col1, clean_col2, clean_col3 = st.columns([1, 2, 1])
    with clean_col2:
        if st.button("Limpiar resultados", use_container_width=True):
            clear_results()
            st.rerun()

    # --- Resultado ---
    st.subheader("Resultado")
    if metadata.get("success"):
        st.success("✅ Proceso completado.")
    else:
        st.error("❌ El proceso falló. Revisa el detalle en metadata (error).")

    # --- Tamaños ---
    st.subheader("Tamaños")
    m1, m2, m3 = st.columns(3)
    m1.metric("MP3", fmt_mb(len(mp3_bytes)))
    m2.metric("JSON", fmt_mb(len(json_bytes)))
    m3.metric("ZIP", fmt_mb(len(zip_bytes)))

    # --- Descargas (apiladas) ---
    st.subheader("Descargas")

    st.download_button(
        label="⬇️ Descargar ZIP (MP3 + JSON)",
        data=zip_bytes,
        file_name=zip_name,
        mime="application/zip",
        use_container_width=True,
    )

    st.download_button(
        label="⬇️ Descargar MP3",
        data=mp3_bytes,
        file_name=mp3_filename,
        mime="audio/mpeg",
        use_container_width=True,
    )

    st.download_button(
        label="⬇️ Descargar JSON",
        data=json_bytes,
        file_name=json_filename,
        mime="application/json",
        use_container_width=True,
    )

    # --- Metadata (al final) ---
    st.subheader("Metadata")
    tabs = st.tabs(["Vista", "JSON (raw)"])
    with tabs[0]:
        st.json(metadata)
    with tabs[1]:
        st.code(json_bytes.decode("utf-8"), language="json")
