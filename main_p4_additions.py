from fastapi import APIRouter, HTTPException, Depends, UploadFile, File, Form
from pydantic import BaseModel, Field
from food_logger import parse_food_description, build_food_log_summary, MEAL_TYPES
from database_p4_additions import save_food_log, get_food_logs_by_date, delete_food_log
from database import get_user_sessions
from premium_guard import require_premium
from auth import require_user_email
from voice_transcriber import transcribe_audio
import datetime as _dt


# ─────────────────────────────────────────────────────────────────────────────
#  MODELE PYDANTIC — P4
# ─────────────────────────────────────────────────────────────────────────────

class FoodLogRequest(BaseModel):
    description: str = Field(..., min_length=2, max_length=500,
                              description="Descriere liberă a mâncării, în română.")
    meal_type:   str = Field(default="general",
                              description="mic_dejun | gustare | pranz | cina | general")


# ─────────────────────────────────────────────────────────────────────────────
#  FACTORY FUNCȚIE — injectează groq_client prin closure, fără import circular
# ─────────────────────────────────────────────────────────────────────────────

def init_food_router(groq_client) -> APIRouter:
    """
    Creează și returnează router-ul P4 cu groq_client capturat în closure.
    
    Pattern ales: factory function cu closure — elimină riscul de import circular
    (main.py → main_p4_additions.py → main.py) și nu necesită variabile globale.
    
    Apelat O SINGURĂ DATĂ la pornirea serverului, în main.py:
        app.include_router(init_food_router(groq_client))
    """
    router = APIRouter(prefix="/food", tags=["P4 · Food Logger"])

    # ── POST /food/log ────────────────────────────────────────────────────────
    @router.post("/log")
    async def food_log_post(
        req:   FoodLogRequest,
        email: str = Depends(require_premium),
    ):
        """
        Flux complet:
          1. Validează meal_type (fallback la 'general' dacă necunoscut)
          2. Trimite descrierea la Groq/Llama cu temperature=0.15 (JSON consistent)
          3. Recalculează totalurile local (nu avem încredere în sumele AI-ului)
          4. Salvează în food_logs
          5. Returnează parsed + id pentru update imediat în UI
        """
        if req.meal_type not in MEAL_TYPES:
            req.meal_type = "general"

        # Apelul AI — groq_client capturat din closure
        parsed = await parse_food_description(groq_client, req.description)

        if "error" in parsed:
            raise HTTPException(
                status_code=422,
                detail=parsed["error"]
            )

        totals = parsed.get("totals", {})
        saved  = await save_food_log(
            email=email,
            meal_type=req.meal_type,
            description=req.description,
            calories=  int(totals.get("calories",  0)),
            protein_g= int(totals.get("protein_g", 0)),
            carbs_g=   int(totals.get("carbs_g",   0)),
            fat_g=     int(totals.get("fat_g",     0)),
            items=     parsed.get("items",     []),
            confidence=parsed.get("confidence","medium"),
            notes=     parsed.get("notes",     ""),
        )

        # ── Detecție mese repetitive → sugestie template ─────────────────────
        suggest_template = None
        try:
            from database_templates import (
                get_description_frequency,
                template_description_exists,
            )
            freq = await get_description_frequency(email, req.description)
            if freq >= 3 and not await template_description_exists(email, req.description):
                auto_name = req.description[:45] + ("…" if len(req.description) > 45 else "")
                suggest_template = {
                    "name": auto_name,
                    "meal_type": req.meal_type,
                    "description": req.description,
                    "calories": int(totals.get("calories", 0)),
                    "protein_g": int(totals.get("protein_g", 0)),
                    "carbs_g": int(totals.get("carbs_g", 0)),
                    "fat_g": int(totals.get("fat_g", 0)),
                    "items": parsed.get("items", []),
                    "notes": parsed.get("notes", ""),
                }
        except Exception:
            pass  # Sugestia e non-critică

        response: dict = {
            "ok":     True,
            "log_id": saved["id"],
            "date":   saved["date"],
            "parsed": parsed,
        }
        if suggest_template:
            response["suggest_template"] = suggest_template
        return response

    # ── GET /food/today ───────────────────────────────────────────────────────
    @router.get("/today")
    async def food_today_get(email: str = Depends(require_user_email)):
        """
        Returnează logurile de azi + summary calculat față de target-ul macro.
        Target-ul macro vine din ultimul calcul TDEE al userului (sessions table).
        Dacă nu există calcul TDEE → summary fără progress bars (targets=None).
        """
        today = _dt.datetime.now().strftime("%Y-%m-%d")
        logs  = await get_food_logs_by_date(email, today)

        # Extragem macro target din ultimul calcul TDEE
        sessions     = await get_user_sessions(email, limit=1)
        last_session = sessions[0] if sessions else None
        target_macros = None
        if last_session:
            target_macros = {
                "target_kcal": last_session.get("target_kcal"),
                "protein_g":   last_session.get("protein_g"),
                "carbs_g":     last_session.get("carbs_g"),
                "fat_g":       last_session.get("fat_g"),
            }

        summary = build_food_log_summary(logs, target_macros)

        return {
            "logs":    logs,
            "summary": summary,
            "date":    today,
        }

    # ── DELETE /food/log/{log_id} ─────────────────────────────────────────────
    @router.delete("/log/{log_id}")
    async def food_log_delete(
        log_id: int,
        email:  str = Depends(require_user_email),
    ):
        """
        Șterge un log specific.
        Verificarea email-ului din DB previne ștergerea logurilor altor utilizatori.
        """
        deleted = await delete_food_log(email, log_id)
        if not deleted:
            raise HTTPException(
                status_code=404,
                detail="Log negăsit sau nu îți aparține.",
            )
        return {"ok": True, "deleted_id": log_id}

    # ── GET /food/meal-types ──────────────────────────────────────────────────
    @router.get("/meal-types")
    async def food_meal_types_get():
        """Returnează tipurile de masă disponibile. Util pentru dropdown dinamic."""
        return {"meal_types": MEAL_TYPES}

    # ── POST /food/voice ──────────────────────────────────────────────────────
    @router.post("/voice")
    async def food_voice_post(
        audio:     UploadFile = File(..., description="Fișier audio (webm/mp4/ogg/wav)"),
        meal_type: str        = Form(default="general"),
        email:     str        = Depends(require_premium),
    ):
        """
        Pipeline complet Voice → Text → Macros → Database în sub 5 secunde.

        Flux:
          1. Citim audio (WebM/MP4/OGG de la MediaRecorder din browser)
          2. Groq Whisper Large v3 → text transcris (zero cost, ~2-3s)
          3. Groq Llama 3.3 70B → parse macros din text (reutilizăm parse_food_description)
          4. Salvăm în food_logs cu nota [Voice]
          5. Returnăm transcrierea + parsed + log_id pentru update imediat în UI

        De ce e rapid:
          - Groq Whisper: 10× mai rapid decât OpenAI Whisper API
          - Refolosim groq_client (nu creăm instanțe noi)
          - Pipeline secvențial ≈ 2s Whisper + 1.5s LLM = <4s total

        Notă: python-multipart este necesar în requirements.txt pentru UploadFile.
        """
        # ── 1. Citim audio bytes ──────────────────────────────────────────
        audio_bytes = await audio.read()
        filename    = audio.filename or "voice.webm"

        # ── 2. Transcriere Whisper (Groq) ─────────────────────────────────
        transcription = await transcribe_audio(groq_client, audio_bytes, filename)

        if "error" in transcription:
            raise HTTPException(status_code=422, detail=transcription["error"])

        text = transcription["text"].strip()

        if len(text) < 3:
            raise HTTPException(
                status_code=422,
                detail="Transcriere prea scurtă. Vorbește mai clar sau mai aproape de microfon.",
            )

        # ── 3. Parse macros (același engine ca text logging) ──────────────
        if meal_type not in MEAL_TYPES:
            meal_type = "general"

        parsed = await parse_food_description(groq_client, text)

        if "error" in parsed:
            raise HTTPException(
                status_code=422,
                detail=f"Whisper a transcris: '{text}' — dar AI-ul nu a putut estima macros. Încearcă o descriere mai specifică.",
            )

        # ── 4. Salvăm în food_logs ────────────────────────────────────────
        totals = parsed.get("totals", {})
        saved  = await save_food_log(
            email=email,
            meal_type=meal_type,
            description=text,                              # textul transcris devine descrierea
            calories=  int(totals.get("calories",  0)),
            protein_g= int(totals.get("protein_g", 0)),
            carbs_g=   int(totals.get("carbs_g",   0)),
            fat_g=     int(totals.get("fat_g",     0)),
            items=     parsed.get("items",     []),
            confidence=parsed.get("confidence","medium"),
            notes=     f"[Voice] {parsed.get('notes', '')}".strip(),
        )

        # ── 5. Răspuns complet pentru frontend ────────────────────────────
        return {
            "ok":           True,
            "transcription": text,                          # UI-ul îl afișează în textarea
            "log_id":       saved["id"],
            "date":         saved["date"],
            "parsed":       parsed,                         # items breakdown disponibil imediat
        }

    return router