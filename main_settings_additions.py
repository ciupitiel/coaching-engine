import csv
import io
import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from auth import require_user_email
from database_settings import (
    delete_user_data,
    get_all_user_data,
    get_settings,
    save_settings,
)


# ─────────────────────────────────────────────────────────────────────────────
#  MODELE PYDANTIC — Etapa 5 (extins E5)
# ─────────────────────────────────────────────────────────────────────────────

class SettingsPayload(BaseModel):
    theme:                   str  = Field(default="dark",        description="'dark' | 'amoled'")
    accent_color:            str  = Field(default="amber",       description="'amber'|'cyan'|'emerald'|'violet'|'white'")  # [E5 NOU]
    density:                 str  = Field(default="comfortable", description="'compact'|'comfortable'|'spacious'")          # [E5 NOU]
    theme_sync:              str  = Field(default="manual",      description="'manual'|'auto'")                            # [E5 NOU]
    ai_persona:              str  = Field(default="empatic",     description="'empatic'|'stiintific'|'militar'")
    diet_template:           str  = Field(default="standard",    description="'standard'|'keto'|'mediteranean'|'if_16_8'")
    allergies:               str  = Field(default="",            max_length=500)
    adaptive_aggressiveness: int  = Field(default=2,             ge=1, le=3)
    reduce_animations:       bool = Field(default=False)
    units:                   str  = Field(default="metric",      description="'metric'|'hybrid'")


class DeleteAccountPayload(BaseModel):
    confirm: bool = Field(
        ...,
        description="Trebuie să fie explicit true. Protecție împotriva ștergerii accidentale."
    )


# ─────────────────────────────────────────────────────────────────────────────
#  ROUTER
# ─────────────────────────────────────────────────────────────────────────────

def init_settings_router() -> APIRouter:
    router = APIRouter(prefix="/settings", tags=["Etapa 5 · Settings Hub"])

    # ── GET /settings ─────────────────────────────────────────────────────────
    @router.get("")
    async def settings_get(email: str = Depends(require_user_email)):
        """
        Returnează settings-urile curente ale userului autentificat.
        Dacă userul nu a salvat niciodată settings → returnează DEFAULT_SETTINGS
        cu is_default=True. Frontend-ul îl folosește pentru a afișa
        un hint 'Personalizează-ți experiența' la prima deschidere.
        """
        return await get_settings(email)

    # ── POST /settings ────────────────────────────────────────────────────────
    @router.post("")
    async def settings_post(
        payload: SettingsPayload,
        email:   str = Depends(require_user_email),
    ):
        """
        Salvează (sau actualizează) settings-urile.
        UPSERT complet — un singur apel indiferent că e prima salvare sau update.
        Răspunsul include settings-urile salvate efectiv (post-validare).
        """
        saved = await save_settings(email, payload.model_dump())
        return {"ok": True, "settings": saved}

    # ── GET /settings/export/json ─────────────────────────────────────────────
    @router.get("/export/json")
    async def settings_export_json(email: str = Depends(require_user_email)):
        """Exportă TOATE datele userului ca fișier JSON descărcabil."""
        data      = await get_all_user_data(email)
        filename  = f"coaching_data_{datetime.now().strftime('%Y%m%d')}.json"
        content   = json.dumps(data, ensure_ascii=False, indent=2, default=str)

        return Response(
            content=content,
            media_type="application/json",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Content-Length":      str(len(content.encode("utf-8"))),
            },
        )

    # ── GET /settings/export/csv ──────────────────────────────────────────────
    @router.get("/export/csv")
    async def settings_export_csv(email: str = Depends(require_user_email)):
        """Exportă logurile alimentare + check-in-urile ca CSV descărcabil."""
        data = await get_all_user_data(email)

        output = io.StringIO()
        writer = csv.writer(output, quoting=csv.QUOTE_MINIMAL)

        writer.writerow(["# Export Coaching Engine"])
        writer.writerow([f"# Email: {email}"])
        writer.writerow([f"# Data export: {datetime.now().strftime('%d.%m.%Y %H:%M')}"])
        writer.writerow([])

        writer.writerow(["=== CHECK-IN-URI GREUTATE ==="])
        writer.writerow(["Data", "Greutate (kg)", "Note"])
        checkins = data.get("checkins", [])
        if checkins:
            for c in checkins:
                writer.writerow([c.get("date",""), c.get("weight_kg",""), c.get("notes","")])
        else:
            writer.writerow(["—", "Niciun check-in înregistrat", ""])
        writer.writerow([])

        writer.writerow(["=== CALCULE TDEE ==="])
        writer.writerow(["Data","BMR (kcal)","TDEE (kcal)","Țintă (kcal)","Proteină (g)","Carbohidrați (g)","Grăsimi (g)","Formulă"])
        sessions = data.get("sessions", [])
        if sessions:
            for s in sessions:
                writer.writerow([s.get("timestamp","")[:10], s.get("bmr",""), s.get("tdee",""),
                                  s.get("target_kcal",""), s.get("protein_g",""), s.get("carbs_g",""),
                                  s.get("fat_g",""), s.get("formula_used","Mifflin-St Jeor")])
        else:
            writer.writerow(["—","Niciun calcul TDEE","","","","","",""])
        writer.writerow([])

        writer.writerow(["=== LOGURI ALIMENTARE ==="])
        writer.writerow(["Data","Tip masă","Descriere","Calorii","Proteină (g)","Carbohidrați (g)","Grăsimi (g)","Încredere AI"])
        food_logs = data.get("food_logs", [])
        if food_logs:
            for fl in food_logs:
                writer.writerow([fl.get("date",""), fl.get("meal_type",""), fl.get("description",""),
                                  fl.get("calories",0), fl.get("protein_g",0), fl.get("carbs_g",0),
                                  fl.get("fat_g",0), fl.get("confidence","")])
        else:
            writer.writerow(["—","Niciun log alimentar","","","","","",""])
        writer.writerow([])
        writer.writerow(["# Noian Cristian · Bazat pe inteligență artificială"])

        content  = output.getvalue()
        filename = f"coaching_export_{datetime.now().strftime('%Y%m%d')}.csv"

        return Response(
            content=content.encode("utf-8-sig"),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    # ── DELETE /settings/account ──────────────────────────────────────────────
    @router.delete("/account")
    async def settings_delete_account(
        payload: DeleteAccountPayload,
        email:   str = Depends(require_user_email),
    ):
        """Șterge PERMANENT contul și toate datele asociate."""
        if not payload.confirm:
            raise HTTPException(
                status_code=400,
                detail="Ștergerea contului necesită confirm=true. Această operație este ireversibilă.",
            )

        report        = await delete_user_data(email)
        total_deleted = sum(v for v in report.values() if isinstance(v, int))

        return {
            "ok":           True,
            "message":      "Contul și toate datele au fost șterse permanent.",
            "deleted_rows": total_deleted,
            "report":       report,
        }

    # ── GET /settings/options ─────────────────────────────────────────────────
    @router.get("/options")
    async def settings_options_get():
        """
        Returnează toate valorile valide pentru fiecare setare.
        Folosit de frontend pentru a construi dropdown-uri dinamic.
        """
        return {
            "themes": [
                {"value": "dark",   "label": "Dark (implicit)"},
                {"value": "amoled", "label": "AMOLED Black (economie baterie)"},
            ],
            "accent_colors": [  # [E5 NOU]
                {"value": "amber",   "label": "Neon Amber",   "hex": "#c4622d"},
                {"value": "cyan",    "label": "Electric Cyan", "hex": "#00b4d8"},
                {"value": "emerald", "label": "Emerald Green", "hex": "#22c55e"},
                {"value": "violet",  "label": "Cyber Violet",  "hex": "#8b5cf6"},
                {"value": "white",   "label": "Mono White",    "hex": "#d4d0cb"},
            ],
            "densities": [  # [E5 NOU]
                {"value": "compact",     "label": "Compact — maxim de date pe ecran"},
                {"value": "comfortable", "label": "Comfortable — echilibru (implicit)"},
                {"value": "spacious",    "label": "Spacious — lizibilitate maximă"},
            ],
            "theme_sync": [  # [E5 NOU]
                {"value": "manual", "label": "Manual — tu controlezi"},
                {"value": "auto",   "label": "Auto — urmează preferința sistemului"},
            ],
            "ai_personas": [
                {"value": "empatic",    "label": "Empatic — cald, înțelegător, motivational"},
                {"value": "stiintific", "label": "Științific — date precise, explicații tehnice"},
                {"value": "militar",    "label": "Militar — direct, fără scuze, disciplină"},
            ],
            "diet_templates": [
                {"value": "standard",     "label": "Standard — fără restricții"},
                {"value": "keto",         "label": "Keto — carbohidrați < 30g/zi"},
                {"value": "mediteranean", "label": "Mediteranean — ulei măsline, pește, legume"},
                {"value": "if_16_8",      "label": "IF 16:8 — post intermitent, fereastră 8h"},
            ],
            "adaptive_aggressiveness": [
                {"value": 1, "label": "Blând — ajustări mici, schimbări lente"},
                {"value": 2, "label": "Moderat — echilibru între viteză și confort (recomandat)"},
                {"value": 3, "label": "Agresiv — tăieri/creșteri mari, rezultate rapide"},
            ],
            "units": [
                {"value": "metric", "label": "Metric — kg, cm"},
                {"value": "hybrid", "label": "Hibrid — kg + ft/in pentru înălțime"},
            ],
        }

    return router