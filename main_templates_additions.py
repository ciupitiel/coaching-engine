# =============================================================================
#  main_templates_additions.py — Feature #18: Quick Meal Templates · Router
#  Noian Cristian · Noian Lab
#  -----------------------------------------------------------------------------
#  Modul NOU. Nu modifică niciun fișier existent.
#
#  Pattern identic cu celelalte routers: factory function cu closure.
#  Înregistrare în main.py:
#
#      from main_templates_additions import init_templates_router
#      app.include_router(init_templates_router())
#
#  Endpoints:
#    GET    /food/templates          → listează template-uri (require_user_email)
#    POST   /food/templates          → salvează template nou (require_premium)
#    POST   /food/templates/{id}/use → loghează instant, zero AI (require_premium)
#    DELETE /food/templates/{id}     → șterge template (require_user_email)
#
#  Autorizare:
#    · GET + DELETE = require_user_email (vizualizare/ștergere = gratuită)
#    · POST (creare + utilizare) = require_premium (creează food_log = premium)
#      Consistent cu POST /food/log care este deja require_premium.
# =============================================================================

from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field
from typing import Optional
from auth import require_user_email
from premium_guard import require_premium
from database_templates import (
    save_template,
    get_templates,
    increment_template_use,
    delete_template,
)
from database_p4_additions import save_food_log
from food_logger import MEAL_TYPES
import datetime as _dt


# ─────────────────────────────────────────────────────────────────────────────
#  MODELE PYDANTIC
# ─────────────────────────────────────────────────────────────────────────────

class TemplateSaveRequest(BaseModel):
    """
    Payload pentru salvarea unui template nou.
    Trimis din două surse:
      1. Banner de sugestie automată (date pre-completate din logul tocmai salvat)
      2. Viitor: buton manual "Salvează ca template" pe un log existent
    """
    name:        str   = Field(..., min_length=1, max_length=80,
                               description="Numele template-ului, max 80 caractere.")
    meal_type:   str   = Field(default="general",
                               description="mic_dejun | gustare | pranz | cina | general")
    description: str   = Field(..., min_length=2, max_length=500,
                               description="Descrierea originală a mesei — salvată identic.")
    calories:    int   = Field(..., ge=0)
    protein_g:   int   = Field(..., ge=0)
    carbs_g:     int   = Field(..., ge=0)
    fat_g:       int   = Field(..., ge=0)
    items:       list  = Field(default=[],
                               description="Items breakdown de la AI — re-folosit la logare.")
    notes:       str   = Field(default="")


# ─────────────────────────────────────────────────────────────────────────────
#  FACTORY FUNCTION — pattern identic cu init_food_router etc.
# ─────────────────────────────────────────────────────────────────────────────

def init_templates_router() -> APIRouter:
    """
    Creează și returnează router-ul pentru template-uri.
    Apelat O SINGURĂ DATĂ la pornire, în main.py:

        app.include_router(init_templates_router())
    """
    router = APIRouter(prefix="/food/templates", tags=["#18 · Meal Templates"])


    # ═════════════════════════════════════════════════════════════════════════
    #  GET /food/templates
    # ═════════════════════════════════════════════════════════════════════════
    @router.get("")
    async def templates_list(email: str = Depends(require_user_email)):
        """
        Returnează toate template-urile salvate ale userului.
        Ordine: use_count DESC (cele mai frecvent folosite primele).

        Autorizare: require_user_email — vizualizarea e gratuită.
        Un user care și-a pierdut premium-ul poate vedea în continuare
        template-urile salvate (și le poate șterge), dar nu le poate folosi.
        """
        templates = await get_templates(email)
        return {"templates": templates, "count": len(templates)}


    # ═════════════════════════════════════════════════════════════════════════
    #  POST /food/templates
    # ═════════════════════════════════════════════════════════════════════════
    @router.post("")
    async def templates_save(
        req:   TemplateSaveRequest,
        email: str = Depends(require_premium),
    ):
        """
        Salvează un template nou.

        Apelat din frontend când userul confirmă banner-ul de sugestie automată
        (afișat după ce a logat aceeași masă de 3+ ori).

        Autorizare: require_premium — consistent cu POST /food/log.
        Validări:
          · Max _MAX_TEMPLATES_PER_USER template-uri per user
          · Unicitate pe descriere (case-insensitive, trimmed)
        Ambele aruncă ValueError → HTTP 409.
        """
        if req.meal_type not in MEAL_TYPES:
            req.meal_type = "general"

        try:
            saved = await save_template(
                email=email,
                name=req.name,
                meal_type=req.meal_type,
                description=req.description,
                calories=req.calories,
                protein_g=req.protein_g,
                carbs_g=req.carbs_g,
                fat_g=req.fat_g,
                items=req.items,
                notes=req.notes,
            )
        except ValueError as e:
            raise HTTPException(status_code=409, detail=str(e))

        return {"ok": True, "template": saved}


    # ═════════════════════════════════════════════════════════════════════════
    #  POST /food/templates/{template_id}/use
    # ═════════════════════════════════════════════════════════════════════════
    @router.post("/{template_id}/use")
    async def templates_use(
        template_id: int,
        email:       str = Depends(require_premium),
        meal_type:   Optional[str] = Query(
            default=None,
            description="Override meal_type din UI. Dacă lipsește, se folosește meal_type-ul din template."
        ),
    ):
        """
        Loghează instant masa salvată în template — ZERO apel AI.

        Avantaj față de POST /food/log:
          · POST /food/log  ≈ 2-4 secunde (Groq LLM parse)
          · POST /food/templates/{id}/use  ≈ 50-100ms (2 operații DB)

        Flux:
          1. UPDATE meal_templates (use_count++, last_used_at=now) + RETURNING macros
             — atomic, fără race condition, fără al doilea SELECT
          2. INSERT food_logs cu macros stocate în template (confidence="high")
          3. Returnează răspuns identic structural cu POST /food/log
             → același handler JS în frontend

        Autorizare: require_premium.
        Consistență: POST /food/log (care creează food_log-uri) este deja require_premium.
        """
        # ── 1. Increment use_count + fetch macros (atomic) ─────────────────
        tmpl = await increment_template_use(email, template_id)
        if not tmpl:
            raise HTTPException(
                status_code=404,
                detail="Template negăsit sau nu îți aparține.",
            )

        # ── 2. Salvează food_log cu macros stocate (zero AI) ──────────────
        # meal_type: UI-ul trimite selectedMealType ca query param.
        # Dacă lipsește sau e invalid → fallback la meal_type din template.
        effective_meal_type = meal_type if (meal_type and meal_type in MEAL_TYPES) else tmpl["meal_type"]

        saved = await save_food_log(
            email=email,
            meal_type=effective_meal_type,
            description=tmpl["description"],
            calories=tmpl["calories"],
            protein_g=tmpl["protein_g"],
            carbs_g=tmpl["carbs_g"],
            fat_g=tmpl["fat_g"],
            items=tmpl.get("items", []),
            confidence="high",          # date verificate anterior de AI → high confidence
            notes=f"[Template: {tmpl['name']}] {tmpl.get('notes', '')}".strip(),
        )

        # ── 3. Răspuns — structură identică cu POST /food/log ─────────────
        # Frontend-ul poate refolosi același handler (renderFoodSection etc.)
        return {
            "ok":            True,
            "log_id":        saved["id"],
            "date":          saved["date"],
            "template_id":   template_id,
            "template_name": tmpl["name"],
            "parsed": {
                "totals": {
                    "calories":  tmpl["calories"],
                    "protein_g": tmpl["protein_g"],
                    "carbs_g":   tmpl["carbs_g"],
                    "fat_g":     tmpl["fat_g"],
                },
                "items":      tmpl.get("items", []),
                "confidence": "high",
                "notes":      tmpl.get("notes", ""),
            },
        }


    # ═════════════════════════════════════════════════════════════════════════
    #  DELETE /food/templates/{template_id}
    # ═════════════════════════════════════════════════════════════════════════
    @router.delete("/{template_id}")
    async def templates_delete(
        template_id: int,
        email:       str = Depends(require_user_email),
    ):
        """
        Șterge un template specific al userului.

        Autorizare: require_user_email — ștergerea propriilor date este gratuită.
        Un user care și-a pierdut premium-ul poate în continuare să-și cureațe template-urile.
        """
        deleted = await delete_template(email, template_id)
        if not deleted:
            raise HTTPException(
                status_code=404,
                detail="Template negăsit sau nu îți aparține.",
            )
        return {"ok": True, "deleted_id": template_id}


    return router