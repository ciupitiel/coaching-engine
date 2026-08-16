import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel

from auth import require_user_email
from premium_guard import require_premium
from database import get_checkins, get_profile, get_user_sessions
from meal_plan_generator import generate_weekly_meal_plan
from rag_engine import (
    build_rag_context_string,
    get_rag_stats,
    query_all_meal_types,
)

_tasks: dict[str, dict] = {}


def _new_task_id() -> str:
    """Generează un ID scurt, uman-lizibil (8 caractere hex)."""
    return str(uuid.uuid4()).replace("-", "")[:8]


# ─────────────────────────────────────────────────────────────────────────────
#  MODELE PYDANTIC — P6
# ─────────────────────────────────────────────────────────────────────────────

class SmartMealPlanRequest(BaseModel):
    preferences: str = ""
    # n_per_meal: câte alimente RAG injectăm per tip de masă (4 = optim)
    # Mai mult = prompt mai lung = mai multă acuratețe, dar mai lent
    n_per_meal: int = 4

async def _bg_adaptive_analysis(
    task_id:     str,
    email:       str,
    groq_client,
) -> None:

    _tasks[task_id]["status"] = "running"
    _tasks[task_id]["started_at"] = datetime.now().isoformat()

    try:
        # ── 1. Date din DB ────────────────────────────────────────────────
        checkins     = await get_checkins(email, limit=30)
        # get_checkins returnează ORDER BY date ASC:
        #   → checkins[0]  = cel mai vechi check-in
        #   → checkins[-1] = cel mai recent check-in
        profile      = await get_profile(email)
        sessions     = await get_user_sessions(email, limit=1)
        last_session = sessions[0] if sessions else None

        # ── 2. Context pentru AI ─────────────────────────────────────────
        checkin_summary = ""
        if checkins:
            first = checkins[0]    # FIX: cel mai vechi (ASC → index 0)
            last  = checkins[-1]   # FIX: cel mai recent (ASC → index -1)
            total_change = round(
                float(last.get("weight_kg", 0)) - float(first.get("weight_kg", 0)), 1
            )
            recent_3 = [c["weight_kg"] for c in checkins[-3:]]   # FIX: ultimele 3, nu primele 3
            stagnation = (max(recent_3) - min(recent_3)) <= 0.5 if len(recent_3) >= 3 else False

            checkin_summary = (
                f"Check-in-uri: {len(checkins)} total. "
                f"Greutate inițială: {first.get('weight_kg')} kg. "
                f"Greutate actuală: {last.get('weight_kg')} kg. "
                f"Schimbare totală: {'+' if total_change >= 0 else ''}{total_change} kg. "
                + ("STAGNARE DETECTATĂ în ultimele 3 check-in-uri. " if stagnation else "")
            )
        else:
            checkin_summary = "Niciun check-in înregistrat încă."

        tdee_summary = ""
        if last_session:
            tdee_summary = (
                f"TDEE calculat: {last_session.get('tdee')} kcal. "
                f"Țintă zilnică: {last_session.get('target_kcal')} kcal. "
                f"Macros: P{last_session.get('protein_g')}g "
                f"C{last_session.get('carbs_g')}g G{last_session.get('fat_g')}g."
            )
# ── Food→Adaptive Bridge: context real de consum ──────────────────────
        from food_adaptive_bridge import get_food_intake_stats, build_food_context_for_ai
        food_stats   = await get_food_intake_stats(email, days=14)
        target_k     = int(last_session.get("target_kcal", 0)) if last_session else 0
        food_context = build_food_context_for_ai(food_stats, target_k)
        goal = (profile or {}).get("goal", "mentinere")

        # ── 3. Narativă AI via Groq ──────────────────────────────────────
        system_prompt = (
            "Ești un coach personal de nutriție și fitness. "
            "Analizezi progresul unui client și oferi un raport de 3 paragrafe SCURTE. "
            "REGULI: Persoana a II-a mereu. Cifre exacte din context. "
            "Zero fraze goale (Bravo!, Felicitări!). "
            "Ton direct, cald, practic. Exclusiv română. "
            "Format: §1 Progres curent §2 Ce funcționează/ce nu §3 Acțiunea concretă de mâine."
        )

        user_msg = (
            f"Analizează progresul:\n"
            f"Obiectiv: {goal}\n"
            f"Progres greutate: {checkin_summary}\n"
            f"Date TDEE: {tdee_summary if tdee_summary else 'Niciun calcul TDEE.'}\n"
            f"Date nutriționale reale:\n{food_context}\n"
            f"Generează raportul în 3 paragrafe scurte."
        )

        ai_response = await groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_msg},
            ],
            temperature=0.5,
            max_tokens=500,
        )

        narrative = ai_response.choices[0].message.content.strip()

        # ── 4. Stochează rezultatul ───────────────────────────────────────
        _tasks[task_id].update({
            "status":       "done",
            "completed_at": datetime.now().isoformat(),
            "result": {
                "narrative":      narrative,
                "checkins_count": len(checkins),
                "goal":           goal,
                "has_tdee":       last_session is not None,
                "last_weight_kg": checkins[-1].get("weight_kg") if checkins else None,  # FIX: cel mai recent
            },
        })

    except Exception as exc:
        _tasks[task_id].update({
            "status":       "error",
            "completed_at": datetime.now().isoformat(),
            "error":        str(exc),
        })


# ─────────────────────────────────────────────────────────────────────────────
#  FACTORY FUNCȚIE — same pattern as P4 și P5
# ─────────────────────────────────────────────────────────────────────────────

def init_p6_router(groq_client) -> APIRouter:
    """
    Creează și returnează router-ul P6 cu groq_client capturat în closure.
    Apelat O SINGURĂ DATĂ la pornirea serverului din main.py.
    """
    router = APIRouter(tags=["P6 · BackgroundTasks + RAG"])

    # ══ BACKGROUND TASKS — Analiză adaptivă asincronă ════════════════════════

    # ── POST /adaptive/queue ──────────────────────────────────────────────────
    @router.post("/adaptive/queue")
    async def adaptive_queue_post(
        background_tasks: BackgroundTasks,
        email: str = Depends(require_premium),
    ):
        task_id = _new_task_id()
        _tasks[task_id] = {
            "status":     "queued",
            "created_at": datetime.now().isoformat(),
            "result":     None,
            "error":      None,
        }

        background_tasks.add_task(
            _bg_adaptive_analysis,
            task_id=task_id,
            email=email,
            groq_client=groq_client,
        )

        return {
            "task_id":    task_id,
            "status":     "queued",
            "message":    "Analiza a pornit. Verifică rezultatul cu GET /adaptive/result/{task_id}",
            "poll_every": "2s",
        }

    # ── GET /adaptive/result/{task_id} ───────────────────────────────────────
    @router.get("/adaptive/result/{task_id}")
    async def adaptive_result_get(
        task_id: str,
        email: str = Depends(require_premium),
    ):
        task = _tasks.get(task_id)
        if not task:
            raise HTTPException(
                status_code=404,
                detail=f"Task '{task_id}' negăsit. "
                       "Task-urile se șterg la restartul serverului — generează un task nou.",
            )
        return {"task_id": task_id, **task}

    # ── GET /adaptive/tasks ───────────────────────────────────────────────────
    @router.get("/adaptive/tasks")
    async def adaptive_tasks_list(
        email: str = Depends(require_premium),
    ):
        recent = list(_tasks.items())[-10:]
        return {
            "total_tasks": len(_tasks),
            "recent": [
                {"task_id": tid, **data}
                for tid, data in recent
            ],
        }

    # ══ RAG SMART MEAL PLAN ═══════════════════════════════════════════════════

    # ── POST /meal-plan/smart ─────────────────────────────────────────────────
    @router.post("/meal-plan/smart")
    async def smart_meal_plan_post(
        req:   SmartMealPlanRequest,
        email: str = Depends(require_premium),
    ):
        sessions     = await get_user_sessions(email, limit=1)
        last_session = sessions[0] if sessions else None

        if not last_session:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Efectuează mai întâi un calcul TDEE din pagina principală. "
                    "AI-ul are nevoie de macros-urile tale pentru a genera planul."
                ),
            )

        profile = await get_profile(email)
        goal    = (profile or {}).get("goal", "mentinere")

        target_kcal = int(last_session.get("target_kcal") or 2000)
        protein_g   = int(last_session.get("protein_g")   or 150)
        carbs_g     = int(last_session.get("carbs_g")     or 200)
        fat_g       = int(last_session.get("fat_g")       or 70)

        # ── RAG: recuperare alimente relevante din ChromaDB ──────────────
        # query_all_meal_types: 4 query-uri HNSW → ~4ms total
        n_per = max(2, min(req.n_per_meal, 6))   # limitare 2-6
        foods_by_meal = query_all_meal_types(goal=goal, n_per_meal=n_per)

        # Construim string-ul de context RAG
        rag_context = build_rag_context_string(foods_by_meal, goal)

        # ── Injecție RAG în preferences (ZERO modificări meal_plan_generator.py)
        # Câmpul `preferences` e destinat restricțiilor userului, dar acceptă
        # orice string — inclusiv context RAG. LLM înțelege și respectă contextul.
        enhanced_prefs = req.preferences.strip()
        if rag_context:
            enhanced_prefs = (
                f"{enhanced_prefs}\n\n{rag_context}" if enhanced_prefs
                else rag_context
            )

        # ── Generare plan alimentar (P5 engine, RAG-enhanced) ────────────
        plan = await generate_weekly_meal_plan(
            groq_client=groq_client,
            target_kcal=target_kcal,
            protein_g=protein_g,
            carbs_g=carbs_g,
            fat_g=fat_g,
            goal=goal,
            preferences=enhanced_prefs,
        )

        if "error" in plan:
            raise HTTPException(status_code=500, detail=plan["error"])

        # ── Atașăm metadate extinse (P5 meta + RAG stats) ────────────────
        plan["meta"] = {
            "target_kcal":     target_kcal,
            "protein_g":       protein_g,
            "carbs_g":         carbs_g,
            "fat_g":           fat_g,
            "goal":            goal,
            "days_count":      len(plan.get("plan", [])),
            "rag_foods_count": sum(len(v) for v in foods_by_meal.values()),
            "rag_used":        bool(rag_context),
            "engine":          "RAG-enhanced (ChromaDB + Llama-3.3-70b)",
        }

        return plan

    # ══ MONITORING ════════════════════════════════════════════════════════════

    # ── GET /rag/stats ────────────────────────────────────────────────────────
    @router.get("/rag/stats")
    async def rag_stats_get():
        """
        Statistici despre ChromaDB: câte alimente sunt indexate, vocab size, status.
        Nu necesită autentificare (date non-sensibile, debugging only).
        """
        return get_rag_stats()

    # ── GET /p6/health ────────────────────────────────────────────────────────
    @router.get("/p6/health")
    async def p6_health_get():
        """
        Health check pentru P6: BackgroundTasks + RAG.
        Verifică că task store-ul e activ și RAG-ul e inițializat.
        """
        rag = get_rag_stats()
        return {
            "background_tasks": {
                "active_tasks": len(_tasks),
                "store":        "in-memory dict (Redis substituție)",
            },
            "rag_engine":     rag,
            "substitutions": {
                "celery":    "FastAPI BackgroundTasks (nativ)",
                "redis":     "dict în memorie (in-process)",
                "pinecone":  "ChromaDB EphemeralClient",
                "openai":    "Groq/Llama-3.3-70b",
            },
        }

    return router