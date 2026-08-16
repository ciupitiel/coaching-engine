# =============================================================================
#  voice_transcriber.py — P1: Voice → Text (Whisper via Groq)
#  Noian Cristian · Coaching Engine
#  -----------------------------------------------------------------------------
#  Zero-cost: whisper-large-v3 pe Groq free tier (~10 req/min, 28.800 sec/zi)
#  Suportă: webm, mp4/m4a, ogg, mp3, wav — orice returnează MediaRecorder
#
#  Flux: audio_bytes → Groq Whisper API → text transcris
#  Apelat din main_p4_additions.py /food/voice, nu din altă parte.
#
#  Funcție publică:
#    transcribe_audio(groq_client, audio_bytes, filename) → dict
# =============================================================================

# MIME types acceptate de Groq Whisper API
_MIME_BY_EXT: dict[str, str] = {
    "webm": "audio/webm",
    "mp4":  "audio/mp4",
    "m4a":  "audio/mp4",
    "mp3":  "audio/mpeg",
    "mpeg": "audio/mpeg",
    "wav":  "audio/wav",
    "ogg":  "audio/ogg",
    "flac": "audio/flac",
}

# Dimensiune minimă: sub 1KB = liniște / clic accidental
MIN_AUDIO_BYTES = 1_000


async def transcribe_audio(
    groq_client,
    audio_bytes: bytes,
    filename:    str = "voice.webm",
) -> dict:
    """
    Transcrie audio folosind Groq Whisper Large v3.

    De ce Groq în loc de OpenAI Whisper direct?
    → Groq free tier: ~10 req/min, 28.800 sec audio/zi — zero cost.
    → Latență: 2-4s pentru un clip de 10s vs 5-8s pe OpenAI pay-per-use.
    → Același model (whisper-large-v3), aceeași calitate.

    Args:
        groq_client  : instanța AsyncGroq din main.py (reutilizată)
        audio_bytes  : conținut binar audio (WebM/MP4/OGG din MediaRecorder)
        filename     : numele fișierului cu extensie corectă (setează MIME implicit)

    Returns:
        Success: {"text": "Am mâncat o șaormă...", "language": "ro"}
        Error:   {"error": "Eroare Whisper: ..."}
    """
    if len(audio_bytes) < MIN_AUDIO_BYTES:
        return {
            "error": (
                "Înregistrarea e prea scurtă sau goală. "
                "Apasă microfon, vorbește, apasă din nou."
            )
        }

    # Extrage extensia și obține MIME corect
    ext      = filename.rsplit(".", 1)[-1].lower() if "." in filename else "webm"
    mime     = _MIME_BY_EXT.get(ext, "audio/webm")

    try:
        # Groq async Whisper API
        transcription = await groq_client.audio.transcriptions.create(
            file=(filename, audio_bytes, mime),
            model="whisper-large-v3",
            response_format="json",
            # Nu forțăm language= → Whisper detectează automat (română e suportată bine)
            # temperature=0 pentru transcriere mai stabilă
        )

        text = (transcription.text or "").strip()

        if not text:
            return {
                "error": (
                    "Whisper nu a detectat nicio vorbire. "
                    "Verifică microfonul și încearcă din nou."
                )
            }

        return {"text": text, "language": "auto"}

    except Exception as exc:
        err_str = str(exc)

        # Rate limit — mesaj uman
        if "rate_limit" in err_str.lower() or "429" in err_str:
            return {
                "error": (
                    "Limita Groq Whisper atinsă temporar. "
                    "Încearcă din nou în 10 secunde."
                )
            }

        return {"error": f"Eroare Whisper: {err_str}"}