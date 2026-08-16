# =============================================================================
#  main_photo_food_additions.py — Photo Food Log · Router
#  Noian Lab · Noian Cristian
#  -----------------------------------------------------------------------------
#  Adaugă în main.py exact 3 linii:
#
#  ① La importuri (după `from main_morning_additions import init_morning_router`):
#       from main_photo_food_additions import init_photo_food_router
#
#  ② La routers (după `app.include_router(init_morning_router())`):
#       app.include_router(init_photo_food_router(groq_client))
#
#  NOTĂ: Nu necesită init_db separat — folosește tabelul food_logs existent.
#  -----------------------------------------------------------------------------
#  Endpoint-uri expuse (ambele cer Premium + JWT):
#
#    POST /food/photo/analyze
#      Body:  {image_base64: string, meal_type: string}
#      → Trimite imaginea la Groq Vision
#      → Returnează JSON cu alimentele detectate și macros
#      → NU scrie în DB (user vede rezultatele și confirmă mai întâi)
#
#    POST /food/photo/log
#      Body:  {meal_type, meal_name, foods[], total_calories, total_protein_g,
#               total_carbs_g, total_fat_g, analysis_quality}
#      → Scrie un singur log în food_logs cu toate alimentele ca items_json
#      → confidence = "high" (foto AI este mai precis decât text)
#      → Returnează {ok, log_id, total_calories, meal_type}
#
#  Securitate:
#    · JWT obligatoriu pe ambele endpoint-uri (require_user_email)
#    · Premium guard prin database_stripe.is_user_premium()
#    · Validare format base64 (nu executăm AI pe date invalide)
#    · Limită imagine: max 5MB base64 (~3.75MB imagine brută)
#    · meal_type validat din lista MEAL_TYPES
# =============================================================================

import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from auth import require_user_email
from database_p4_additions import save_food_log
from database_stripe import is_user_premium
from food_logger import MEAL_TYPES
from photo_food_analyzer import analyze_food_photo

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
#  CONSTANTE
# ─────────────────────────────────────────────────────────────────────────────

_VALID_MEAL_TYPES = set(MEAL_TYPES)   # sincron cu food_logger.MEAL_TYPES
_MAX_B64_CHARS    = 7_000_000         # ~5.25MB imagine brută — safe pentru Groq Vision


# ─────────────────────────────────────────────────────────────────────────────
#  MODELE PYDANTIC
# ─────────────────────────────────────────────────────────────────────────────

class PhotoAnalyzeRequest(BaseModel):
    image_base64: str = Field(
        ...,
        min_length=100,
        description="Imagine JPEG/PNG comprimată client-side, base64 (cu sau fără data URL prefix)",
    )
    meal_type: str = Field(
        default="general",
        description="mic_dejun | gustare | pranz | cina | general",
    )

    @field_validator("image_base64")
    @classmethod
    def validate_image_size(cls, v: str) -> str:
        if len(v) > _MAX_B64_CHARS:
            raise ValueError(
                f"Imaginea este prea mare ({len(v):,} chars). "
                "Compresia client-side trebuie să reducă imaginea sub 5MB."
            )
        return v

    @field_validator("meal_type")
    @classmethod
    def validate_meal_type(cls, v: str) -> str:
        if v not in _VALID_MEAL_TYPES:
            return "general"
        return v


class PhotoFoodItem(BaseModel):
    """Un aliment detectat din imagine — structura identică cu răspunsul analyzer-ului."""
    name:             str = Field(..., min_length=1, max_length=200)
    portion_estimate: str = Field(default="—", max_length=100)
    calories:         int = Field(..., ge=0, le=5000)
    protein_g:        int = Field(..., ge=0, le=500)
    carbs_g:          int = Field(..., ge=0, le=500)
    fat_g:            int = Field(..., ge=0, le=300)
    confidence:       str = Field(default="medium")


class PhotoLogRequest(BaseModel):
    """
    Cerere de logare după ce userul a văzut și confirmat rezultatele analizei.
    Frontend-ul trimite datele deja analizate — zero re-procesare AI.
    """
    meal_type:       str              = Field(default="general")
    meal_name:       str              = Field(..., min_length=1, max_length=200)
    foods:           list[PhotoFoodItem] = Field(..., min_length=1, max_length=20)
    total_calories:  int              = Field(..., ge=0, le=10000)
    total_protein_g: int              = Field(..., ge=0, le=1000)
    total_carbs_g:   int              = Field(..., ge=0, le=1000)
    total_fat_g:     int              = Field(..., ge=0, le=500)
    analysis_quality: str             = Field(default="medium")

    @field_validator("meal_type")
    @classmethod
    def validate_meal_type(cls, v: str) -> str:
        if v not in _VALID_MEAL_TYPES:
            return "general"
        return v


# ─────────────────────────────────────────────────────────────────────────────
#  FACTORY ROUTER — pattern identic cu toate celelalte *_additions.py
# ─────────────────────────────────────────────────────────────────────────────

def init_photo_food_router(groq_client) -> APIRouter:
    """
    Creează router-ul Photo Food Log.
    Apelat O SINGURĂ DATĂ în main.py:
        app.include_router(init_photo_food_router(groq_client))

    groq_client: instanța AsyncGroq existentă din main.py — zero overhead.
    """
    router = APIRouter(prefix="/food/photo", tags=["Food · Photo Log"])

    # ── POST /food/photo/analyze ──────────────────────────────────────────────
    @router.post("/analyze")
    async def photo_analyze(
        req:   PhotoAnalyzeRequest,
        email: str = Depends(require_user_email),
    ):
        """
        Analizează o fotografie cu mâncare și returnează estimarea nutritivă.

        Flow:
          1. Verifică Premium (funcție exclusivă)
          2. Validare format imagine (Pydantic + size check)
          3. Trimite la Groq Vision → analyze_food_photo()
          4. Returnează structura completă (NU scrie în DB)

        Frontend-ul afișează rezultatele, userul confirmă, apoi apelează /log.

        Response 200:
          {
            "ok": true,
            "detected": true,
            "meal_name": "Piept de pui cu orez",
            "foods": [{name, portion_estimate, calories, protein_g, carbs_g, fat_g, confidence}],
            "total_calories": 482,
            "total_protein_g": 50,
            "total_carbs_g": 52,
            "total_fat_g": 7,
            "analysis_quality": "high",
            "notes": "..."
          }

        Response 200 (fără mâncare detectată):
          {"ok": true, "detected": false, "meal_name": null, "foods": [], ...}

        Response 403: Cont fără Premium activ.
        Response 422: Imagine invalidă / prea mare.
        Response 503: Groq Vision indisponibil (retry safe).
        """
        # Premium guard — Photo Food Log este exclusiv Premium
        premium = await is_user_premium(email)
        if not premium:
            raise HTTPException(
                status_code=403,
                detail={
                    "code":    "premium_required",
                    "message": "Photo Food Log este exclusiv Premium. Activează abonamentul pentru acces.",
                    "feature": "photo_food_log",
                },
            )

        # Analiză AI — toate erorile sunt gestionate intern, nu ridică excepție
        analysis = await analyze_food_photo(
            groq_client=groq_client,
            image_data=req.image_base64,
        )

        logger.info(
            "Photo analyze: user=%s detected=%s foods_count=%d kcal=%d quality=%s",
            email,
            analysis.get("detected"),
            len(analysis.get("foods", [])),
            analysis.get("total_calories", 0),
            analysis.get("analysis_quality"),
        )

        return {"ok": True, **analysis}

    # ── POST /food/photo/log ──────────────────────────────────────────────────
    @router.post("/log")
    async def photo_log(
        req:   PhotoLogRequest,
        email: str = Depends(require_user_email),
    ):
        """
        Loghează alimentele confirmate de user după analiza foto.

        NU re-analizează imaginea — primește datele deja confirmate de user.
        Aceasta elimină double-charging pe API și permite userului să editeze
        cantitățile înainte de confirmare.

        Structura în food_logs:
          · description = meal_name (ex: "Piept de pui cu orez")
          · items_json  = [{name, calories, protein_g, carbs_g, fat_g,
                            quantity=1, unit="portie", portion_estimate}]
          · confidence  = "high" (foto AI > text AI > manual)
          · notes       = "Photo Food Log · [quality] quality · [N] alimente"

        Un singur log per fotografie (nu N loguri pentru N alimente) →
        consistentă cu cum funcționează voice logging și barcode.

        Response 200:
          {"ok": true, "log_id": 1234, "total_calories": 482, "meal_type": "pranz"}

        Response 403: Premium necesar.
        Response 422: Date invalide.
        """
        # Premium guard — redundant safety check
        premium = await is_user_premium(email)
        if not premium:
            raise HTTPException(
                status_code=403,
                detail={
                    "code":    "premium_required",
                    "message": "Photo Food Log este exclusiv Premium.",
                    "feature": "photo_food_log",
                },
            )

        # Construiește items_json compatibil cu UI-ul existent (food_log_item breakdown)
        # Pattern identic cu food_logger.py și morning_confirm
        items = [
            {
                "name":             food.name,
                "calories":         food.calories,
                "protein_g":        food.protein_g,
                "carbs_g":          food.carbs_g,
                "fat_g":            food.fat_g,
                "quantity":         1,
                "unit":             "portie",
                "portion_estimate": food.portion_estimate,
                "confidence":       food.confidence,
            }
            for food in req.foods
        ]

        # Notă descriptivă pentru UI — calitatea analizei + numărul de alimente
        quality_labels = {"high": "precizie mare", "medium": "precizie medie", "low": "estimare"}
        quality_label  = quality_labels.get(req.analysis_quality, "estimare")
        notes = (
            f"Photo Food Log · {quality_label} · "
            f"{len(req.foods)} aliment{'e' if len(req.foods) != 1 else ''} detectat{'e' if len(req.foods) != 1 else ''}"
        )

        result = await save_food_log(
            email=email,
            meal_type=req.meal_type,
            description=req.meal_name,
            calories=req.total_calories,
            protein_g=req.total_protein_g,
            carbs_g=req.total_carbs_g,
            fat_g=req.total_fat_g,
            items=items,
            confidence="high",   # foto AI = cea mai precisă metodă de logare
            notes=notes,
        )

        logger.info(
            "Photo log saved: user=%s log_id=%s kcal=%d meal=%s",
            email, result.get("id"), req.total_calories, req.meal_type,
        )

        return {
            "ok":             True,
            "log_id":         result.get("id"),
            "total_calories": req.total_calories,
            "meal_type":      req.meal_type,
        }

    return router