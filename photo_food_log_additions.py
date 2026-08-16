# =============================================================================
#  photo_food_log_additions.py — FastAPI Router · Photo Food Log
#  Noian Lab · v3
#  -----------------------------------------------------------------------------
#  POST /food/photo/analyze  → analizează imaginea via Vision AI
#  POST /food/photo/log      → salvează în food_logs
#  GET  /food/photo/debug-vision → testează modelele vision disponibile
#
#  Adaugă în main.py:
#    from photo_food_log_additions import init_photo_food_router
#    app.include_router(init_photo_food_router(groq_client))
# =============================================================================

import os
from fastapi               import APIRouter, Depends, HTTPException
from pydantic              import BaseModel, Field
from premium_guard         import require_premium
from auth                  import require_user_email
from database_p4_additions import save_food_log
from photo_food_analyzer   import analyze_food_photo, debug_vision_models


class PhotoAnalyzeRequest(BaseModel):
    image_base64: str = Field(..., description="Data URL JPEG/PNG de la Canvas API")
    meal_type:    str = Field(default="general")


class PhotoLogRequest(BaseModel):
    meal_type:        str        = "general"
    meal_name:        str        = "Masă fotografiată"
    foods:            list[dict] = []
    total_calories:   int        = 0
    total_protein_g:  int        = 0
    total_carbs_g:    int        = 0
    total_fat_g:      int        = 0
    analysis_quality: str        = "medium"


MEAL_LABELS = {
    "mic_dejun": "Mic Dejun", "gustare": "Gustare",
    "pranz": "Prânz", "cina": "Cină", "general": "General",
}


def init_photo_food_router(groq_client) -> APIRouter:
    router = APIRouter(prefix="/food", tags=["Photo Food Log"])

    # ── POST /food/photo/analyze ──────────────────────────────────────────────
    @router.post("/photo/analyze")
    async def photo_analyze(
        req:   PhotoAnalyzeRequest,
        email: str = Depends(require_premium),
    ):
        if not req.image_base64 or len(req.image_base64) < 5_000:
            raise HTTPException(400, "Imaginea e prea mică. Încearcă o fotografie mai clară.")

        result = await analyze_food_photo(groq_client, req.image_base64)

        if not result.get("detected"):
            return {"detected": False, "notes": result.get("notes", "")}

        return {
            "detected":         True,
            "meal_name":        result.get("meal_name", "Masă detectată"),
            "analysis_quality": result.get("analysis_quality", "medium"),
            "foods":            result.get("foods", []),
            "total_calories":   result.get("total_calories", 0),
            "total_protein_g":  result.get("total_protein_g", 0),
            "total_carbs_g":    result.get("total_carbs_g", 0),
            "total_fat_g":      result.get("total_fat_g", 0),
            "notes":            result.get("notes", ""),
        }

    # ── POST /food/photo/log ──────────────────────────────────────────────────
    @router.post("/photo/log")
    async def photo_log(
        req:   PhotoLogRequest,
        email: str = Depends(require_premium),
    ):
        meal_label = MEAL_LABELS.get(req.meal_type, "General")
        items = [
            {
                "name":     f.get("name", "Aliment"),
                "quantity": f.get("portion_estimate", "—"),
                "calories": int(f.get("calories", 0)),
            }
            for f in (req.foods or [])
        ]
        confidence_map = {"high": "high", "medium": "medium", "low": "low"}
        confidence = confidence_map.get(req.analysis_quality, "medium")
        note = (
            f"[Photo Log] · {req.analysis_quality.capitalize()} quality · "
            f"{len(req.foods)} aliment{'e' if len(req.foods) != 1 else ''} · "
            f"{meal_label}"
        )
        saved = await save_food_log(
            email=email, meal_type=req.meal_type,
            description=req.meal_name or "Masă fotografiată",
            calories=req.total_calories, protein_g=req.total_protein_g,
            carbs_g=req.total_carbs_g, fat_g=req.total_fat_g,
            items=items, confidence=confidence, notes=note,
        )
        return {
            "id":             saved["id"],
            "meal_type":      req.meal_type,
            "total_calories": req.total_calories,
        }

    # ── GET /food/photo/debug-vision ─────────────────────────────────────────
    # Accesibil FĂRĂ premium — util pentru debugging în Render/dev
    # URL: https://domeniu.tău/food/photo/debug-vision
    # Returnează ce modele vision funcționează pe contul tău Groq
    @router.get("/photo/debug-vision")
    async def debug_vision(email: str = Depends(require_user_email)):
        """
        Testează toate modelele vision și returnează care funcționează.
        Apelează după deployment pentru a confirma care model e disponibil.

        Răspuns exemplu:
        {
          "working_models": ["meta-llama/llama-4-scout-17b-16e-instruct"],
          "recommended": "meta-llama/llama-4-scout-17b-16e-instruct",
          "all_results": {
            "meta-llama/llama-4-scout-...": {"status": "OK", "response": "..."},
            "llama-3.2-90b-vision-preview": {"status": "FAIL", "error": "..."}
          }
        }
        """
        return await debug_vision_models(groq_client)
        # ── GET /food/photo/list-models ──────────────────────────────────────────
    @router.get("/photo/list-models")
    async def list_groq_models(email: str = Depends(require_user_email)):
        try:
            models = await groq_client.models.list()
            all_ids = sorted([m.id for m in models.data])
            vision_candidates = [
                m for m in all_ids
                if any(k in m.lower() for k in
                       ['vision', 'scout', 'maverick', 'llama-4', 'llava', 'pixtral'])
            ]
            return {"vision_candidates": vision_candidates, "all_models": all_ids}
        except Exception as e:
            return {"error": str(e)}
    return router