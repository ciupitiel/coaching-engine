import os
import asyncio
import json
import datetime
from io import BytesIO
from typing import Optional
from functools import lru_cache
from fastapi import FastAPI, HTTPException, Depends, Query
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel, Field
from groq import AsyncGroq
import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.starlette import StarletteIntegration
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from main_stripe_additions import init_stripe_router
from database_push import init_db_push
from push_engine import init_push_scheduler, shutdown_push_scheduler
from main_push_additions import init_push_router
from nutritionist_stripe import init_nutritionist_stripe_router
from database_coach_v2 import init_db_coach_v2
from main_morning_additions import init_morning_router
from database_morning_plan import init_db_morning_plan
from database_templates import init_db_templates
from database_stripe import init_db_stripe
from main_streak_additions import init_streak_router
from adaptive_engine import run_adaptive_analysis
from main_coach_additions import init_coach_router
from database_programs  import init_db_programs
from fridge_additions import init_fridge_router
from programs_additions import init_programs_router
from photo_food_log_additions import init_photo_food_router
from main_templates_additions import init_templates_router
from main_barcode_additions import init_barcode_router
from database_exercise import init_db_exercise           # #16: Exerciții & Calorii Arse
from main_exercise_additions import init_exercise_router  # #16: Exerciții & Calorii Arse
from fastapi.responses import JSONResponse
from pwa_engine import MANIFEST, SW_CONTENT, generate_app_icon
from reportlab.lib.colors import HexColor, white
from database_nutritionist  import init_nutritionist_db
from nutritionist_additions import init_nutritionist_router
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table,
    TableStyle, HRFlowable,
)
from reportlab.platypus.flowables import Flowable
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from rate_limiter import RateLimiterMiddleware, init_rate_limit_router
from coaching_engine import select_bmr_formula
from chat_engine import build_coach_system_prompt
from contextlib import asynccontextmanager
from database import (
    init_pool, get_pool,
    init_db, save_session, create_user, get_user_by_email,
    get_user_sessions, save_profile, get_profile,
    save_checkin, get_checkins, get_checkin_summary,
)
from database_p4_additions import init_db_p4
from database_settings import get_settings
from database_settings import init_db_settings
from database_settings_e5 import init_db_settings_e5   # ← E5: migrare coloane noi
from rag_engine import init_rag_engine
from main_settings_additions import init_settings_router
from main_analytics_additions import init_analytics_router
from main_p4_additions import init_food_router
from main_p5_additions import init_meal_plan_router
from main_p6_additions import init_p6_router
from main_report_additions import init_report_router
from main_password_reset_additions import init_password_reset_router
from main_email_verification_additions import init_email_verification_router
from database_onboarding import init_db_onboarding
from database_share import init_db_share
from database_micronutrient import init_db_micronutrient
from database_referral import init_db_referral, create_referral_pending
from main_referral_additions import init_referral_router
from main_share_additions import init_share_router
from database_email_verification import (
    init_db_email_verification,
    create_verification_token,
    mark_user_unverified,
    is_user_verified,
)
from email_service import send_verification_email
from food_adaptive_bridge import get_food_intake_stats, build_food_context_for_ai
from database_password_reset import init_db_password_reset
from auth import (
    verify_password, get_password_hash, create_access_token,
    get_current_user_email, require_user_email,
)
from dotenv import load_dotenv
load_dotenv()  # Încarcă .env explicit în main.py

# ── Inițializare Sentry — activ doar dacă SENTRY_DSN e setat în .env ─────────
_SENTRY_DSN = os.getenv("SENTRY_DSN", "")
if _SENTRY_DSN:
    sentry_sdk.init(
        dsn=_SENTRY_DSN,
        integrations=[
            StarletteIntegration(transaction_style="endpoint"),
            FastApiIntegration(transaction_style="endpoint"),
        ],
        traces_sample_rate=0.1,  # 10% requesturi → performance tracking
        send_default_pii=False,  # GDPR: zero IP-uri sau emailuri trimise la Sentry
        environment="production" if os.getenv("RENDER") else "development",
    )
    print("✅  Sentry: monitoring activ")
else:
    print("ℹ️   Sentry: SENTRY_DSN lipsă — monitoring dezactivat")

# Model pentru datele de Login/Signup
class UserAuth(BaseModel):
    email: str
    password: str

# ══════════════════════════════════════════════════════════════════
# FONTURI
# ══════════════════════════════════════════════════════════════════
PDF_FONT_REGULAR = 'Helvetica'
PDF_FONT_BOLD    = 'Helvetica-Bold'

def init_pdf_fonts():
    global PDF_FONT_REGULAR, PDF_FONT_BOLD
    script_dir = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        (os.path.join(script_dir, 'DejaVuSans.ttf'),
         os.path.join(script_dir, 'DejaVuSans-Bold.ttf'), 'DejaVu'),
        ('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
         '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 'DejaVu'),
        ('/usr/share/fonts/TTF/DejaVuSans.ttf',
         '/usr/share/fonts/TTF/DejaVuSans-Bold.ttf', 'DejaVu'),
    ]
    for reg, bold, name in candidates:
        if not os.path.exists(reg):
            continue
        try:
            pdfmetrics.registerFont(TTFont(f'{name}R', reg))
            PDF_FONT_REGULAR = f'{name}R'
            if os.path.exists(bold):
                pdfmetrics.registerFont(TTFont(f'{name}B', bold))
                PDF_FONT_BOLD = f'{name}B'
            else:
                PDF_FONT_BOLD = f'{name}R'
            print(f"✅ PDF font: {name}")
            return
        except Exception:
            continue
    print("⚠️  PDF font: Helvetica (descarcă DejaVuSans.ttf pentru diacritice)")

init_pdf_fonts()

# ══════════════════════════════════════════════════════════════════
# FASTAPI — lifespan actualizat pentru PostgreSQL (Etapa 3)
# ══════════════════════════════════════════════════════════════════
@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_pool()    # NOU Etapa 3 — pool PostgreSQL, PRIMUL
    await init_db()      # Creează tabelele core
    await init_db_p4()   # Creează food_logs
    await init_rag_engine()
    await init_db_settings()
    await init_db_settings_e5()   # ← E5: ADD COLUMN IF NOT EXISTS (idempotent, zero downtime)
    await init_db_password_reset()
    await init_db_email_verification()
    await init_nutritionist_db()
    await init_db_push()
    await init_db_exercise()    # #16: tabel exercise_logs
    await init_db_stripe()      # #17: coloane Stripe în users
    await init_db_onboarding()
    await init_db_share()
    await init_db_micronutrient()
    await init_db_referral()
    await init_db_templates() # #18: meal_templates
    await init_db_morning_plan()  # Morning Plans
    await init_db_coach_v2()      # Coach Recommendations
    await init_db_programs()
    init_push_scheduler()
    yield
    shutdown_push_scheduler()
    await get_pool().close()          # NOU Etapa 3 — cleanup la shutdown
    print("🔌  PostgreSQL pool: închis.")

app = FastAPI(title="Coaching Engine API", version="1.5", lifespan=lifespan)
app.add_middleware(RateLimiterMiddleware)
# ── PWA ──────────────────────────────────────────────────────
@app.get("/manifest.json", include_in_schema=False)
async def pwa_manifest():
    return JSONResponse(
        content=MANIFEST,
        headers={"Cache-Control": "public, max-age=86400"},
    )

@app.get("/sw.js", include_in_schema=False)
async def pwa_sw():
    return Response(
        content=SW_CONTENT,
        media_type="application/javascript",
        headers={
            "Cache-Control":        "no-cache, no-store, must-revalidate",
            "Service-Worker-Allowed": "/",
        },
    )

@app.get("/icon-{size}.png", include_in_schema=False)
async def pwa_icon(size: int):
    if size not in (192, 512, 180, 167, 152):
        size = 192
    return Response(
        content=generate_app_icon(size),
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=604800"},
    )
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
if not GROQ_API_KEY:
    print("⚠️  GROQ_API_KEY lipsește!")
groq_client = AsyncGroq(api_key=GROQ_API_KEY)
app.include_router(init_food_router(groq_client))
app.include_router(init_meal_plan_router(groq_client))
app.include_router(init_photo_food_router(groq_client))
app.include_router(init_p6_router(groq_client))
app.include_router(init_report_router(groq_client))
app.include_router(init_settings_router())
app.include_router(init_analytics_router())
app.include_router(init_share_router())
app.include_router(init_referral_router())
app.include_router(init_stripe_router())
app.include_router(init_streak_router())
app.include_router(init_password_reset_router())
app.include_router(init_email_verification_router())
app.include_router(init_rate_limit_router())
app.include_router(init_push_router())
app.include_router(init_coach_router())
app.include_router(init_nutritionist_router())
app.include_router(init_barcode_router())
app.include_router(init_nutritionist_stripe_router())
app.include_router(init_exercise_router(groq_client))   # #16: Exerciții & Calorii Arse
app.include_router(init_templates_router()) # #18: Meal Templates
app.include_router(init_morning_router())   # Morning Plan Confirm
app.include_router(init_programs_router())
app.include_router(init_fridge_router(groq_client))

# ══════════════════════════════════════════════════════════════════
# MODELE PYDANTIC — Core
# ══════════════════════════════════════════════════════════════════
class ClientProfile(BaseModel):
    name:           str   = Field(..., min_length=2, max_length=50)
    weight_kg:      float = Field(..., gt=30, lt=300)
    height_cm:      float = Field(..., gt=100, lt=250)
    age:            int   = Field(..., gt=13, lt=100)
    sex:            str   = Field(..., pattern="^(m|f)$")
    activity_level: str
    goal:           str
    body_type:      Optional[str]   = None
    body_fat_pct:   Optional[float] = Field(None, ge=3.0, le=65.0)

class MacroBreakdown(BaseModel):
    protein_g: int;  protein_kcal: float
    carbs_g:   int;  carbs_kcal:   float
    fat_g:     int;  fat_kcal:     float
    total_kcal: int

class WeeklyChangeInfo(BaseModel):
    type: str;  rate: float;  display: str

class CalculationResponse(BaseModel):
    client: str;  bmr: int;  tdee: int;  target_calories: int
    goal_selected: str;  weekly_change: WeeklyChangeInfo
    macros: MacroBreakdown;  coaching_insight: str
    formula_used:      str             = "Mifflin-St Jeor"
    estimated_bf_pct:  Optional[float] = None
    lean_body_mass_kg: Optional[float] = None

class ReportRequest(BaseModel):
    client_name: str;  age: int;  sex: str
    weight_kg: float;  height_cm: float
    activity_level: str;  goal: str
    goal_display: str;  weekly_display: str
    bmr: int;  tdee: int;  target_calories: int;  formula_used: str
    estimated_bf_pct:  Optional[float] = None
    lean_body_mass_kg: Optional[float] = None
    protein_g: int;  protein_kcal: int
    carbs_g:   int;  carbs_kcal:   int
    fat_g:     int;  fat_kcal:     int;  total_kcal: int
    coaching_insight: str

# ══════════════════════════════════════════════════════════════════
# MODELE PYDANTIC — P1: Profil Persistent & Check-in-uri (NOU v1.4)
# ══════════════════════════════════════════════════════════════════
class ProfileSaveRequest(BaseModel):
    height_cm:         float = Field(..., gt=100, lt=250)
    age:               int   = Field(..., gt=13,  lt=100)
    sex:               str   = Field(..., pattern="^(m|f)$")
    activity_level:    str
    goal:              str
    initial_weight_kg: float = Field(..., gt=20,  lt=300)

class CheckinRequest(BaseModel):
    weight_kg: float = Field(..., gt=20, lt=300)
    notes:     str   = ""

class ChatMessage(BaseModel):
    role:    str
    content: str

class ChatRequest(BaseModel):
    message: str            = Field(..., min_length=1, max_length=2000)
    history: list[ChatMessage] = []

# ══════════════════════════════════════════════════════════════════
# PDF ENGINE — REDESIGN PREMIUM v1.3 (nemodificat)
# ══════════════════════════════════════════════════════════════════
class DistBar(Flowable):
    """Bară de distribuție calorică cu 3 segmente colorate."""
    def __init__(self, p, c, f, width, height=10):
        super().__init__()
        self.segs   = [(p, HexColor('#e07b45')), (c, HexColor('#5b9cf6')), (f, HexColor('#f0c060'))]
        self.width  = width
        self.height = height

    def draw(self):
        self.canv.setFillColor(HexColor('#ede9e3'))
        self.canv.roundRect(0, 0, self.width, self.height, 4, fill=1, stroke=0)
        x = 0
        for pct, color in self.segs:
            w = (pct / 100.0) * self.width
            if w > 1:
                self.canv.setFillColor(color)
                self.canv.rect(x, 0, w - 2, self.height, fill=1, stroke=0)
                x += w

def build_pdf(req: ReportRequest) -> bytes:
    buf       = BytesIO()
    PAGE_W, _ = A4
    MARGIN    = 1.6 * cm
    CW        = PAGE_W - 2 * MARGIN
    today     = datetime.date.today().strftime("%d %B %Y")
    fn = PDF_FONT_REGULAR
    fb = PDF_FONT_BOLD

    C = {
        'dark':     HexColor('#111111'),
        'accent':   HexColor('#c4622d'),
        'acc_lt':   HexColor('#fdf0ea'),
        'surf':     HexColor('#fafaf8'),
        'surf2':    HexColor('#f2ede8'),
        'g1':       HexColor('#6b6560'),
        'g2':       HexColor('#d4cdc7'),
        'protein':  HexColor('#e07b45'),
        'carbs':    HexColor('#5b9cf6'),
        'fat':      HexColor('#f0c060'),
    }

    S = {
        'h_name':  ParagraphStyle('hn', fontName=fb, fontSize=14, textColor=white, leading=18),
        'h_sub':   ParagraphStyle('hs', fontName=fn, fontSize=8.5, textColor=HexColor('#a09890'), leading=13),
        'sec':     ParagraphStyle('sc', fontName=fb, fontSize=7, textColor=C['accent'], letterSpacing=2.2, leading=11),
        'lbl':     ParagraphStyle('lb', fontName=fb, fontSize=6.5, textColor=C['g1'], letterSpacing=1.5, leading=10, spaceAfter=2),
        'val':     ParagraphStyle('vl', fontName=fb, fontSize=12, textColor=C['dark'], leading=16),
        'val_acc': ParagraphStyle('va', fontName=fb, fontSize=12, textColor=C['accent'], leading=16),
        'hero_lbl':     ParagraphStyle('hl', fontName=fb, fontSize=6.5, textColor=C['g1'], letterSpacing=1.8, leading=10),
        'hero_num':     ParagraphStyle('hn2', fontName=fb, fontSize=32, textColor=C['dark'], leading=38),
        'hero_num_acc': ParagraphStyle('hna', fontName=fb, fontSize=32, textColor=C['accent'], leading=38),
        'hero_unit':    ParagraphStyle('hu', fontName=fn, fontSize=11, textColor=C['g1'], leading=15),
        'mc_lbl':  ParagraphStyle('ml', fontName=fb, fontSize=7, textColor=C['g1'], letterSpacing=2.2, leading=11),
        'mc_g_p':  ParagraphStyle('mgp', fontName=fb, fontSize=28, textColor=C['protein'], leading=34),
        'mc_g_c':  ParagraphStyle('mgc', fontName=fb, fontSize=28, textColor=C['carbs'],   leading=34),
        'mc_g_f':  ParagraphStyle('mgf', fontName=fb, fontSize=28, textColor=C['fat'],     leading=34),
        'mc_sub':  ParagraphStyle('ms', fontName=fn, fontSize=8.5, textColor=C['g1'],     leading=12),
        'ins':      ParagraphStyle('ins', fontName=fn, fontSize=10.5, textColor=C['dark'], leading=17.5, spaceAfter=10),
        'ins_lbl':  ParagraphStyle('il', fontName=fb, fontSize=7, textColor=C['accent'], letterSpacing=2.2, leading=11),
        'legend': ParagraphStyle('lg', fontName=fn, fontSize=8.5, textColor=C['g1'], leading=13),
        'footer': ParagraphStyle('ft', fontName=fn, fontSize=7.5, textColor=C['g1'], alignment=1, leading=11),
    }

    story = []

    # ── 1. HEADER ───────────────────────────────────────────────
    formula_note = "Katch-McArdle" if "Katch" in req.formula_used else "Mifflin-St Jeor"
    hdr = Table([[
        [
            Paragraph("NOIAN CRISTIAN", S['h_name']),
            Spacer(1, 3),
            Paragraph("Raport de nutritie personalizat", S['h_sub']),
        ],
        [
            Paragraph(today, S['h_sub']),
            Spacer(1, 3),
            Paragraph(f"Formula: {formula_note}", S['h_sub']),
        ],
    ]], colWidths=[CW * 0.58, CW * 0.42])
    hdr.setStyle(TableStyle([
        ('BACKGROUND',    (0,0), (-1,-1), HexColor('#0d0d0d')),
        ('TOPPADDING',    (0,0), (-1,-1), 18),
        ('BOTTOMPADDING', (0,0), (-1,-1), 18),
        ('LEFTPADDING',   (0,0), (0,-1),  22),
        ('RIGHTPADDING',  (-1,0),(-1,-1), 22),
        ('VALIGN',        (0,0), (-1,-1), 'MIDDLE'),
        ('ALIGN',         (1,0), (1,-1),  'RIGHT'),
    ]))
    story.append(hdr)
    story.append(Spacer(1, 18))

    # ── 2. HERO METRICS ─────────────────────────────────────────
    def hero_cell(label, number, accent=False):
        return [
            Paragraph(label.upper(), S['hero_lbl']),
            Spacer(1, 10),
            Paragraph(f"{number}", S['hero_num_acc'] if accent else S['hero_num']),
            Paragraph("kcal / zi", S['hero_unit']),
        ]

    hero_data = [[
        hero_cell("Metabolism bazal (BMR)", req.bmr),
        hero_cell("Necesar zilnic real (TDEE)", req.tdee),
        hero_cell("Tinta ta zilnica", req.target_calories, accent=True),
    ]]
    hero_tbl = Table(hero_data, colWidths=[CW / 3] * 3)
    hero_tbl.setStyle(TableStyle([
        ('BACKGROUND',    (0,0), (1,-1), C['surf']),
        ('BACKGROUND',    (2,0), (2,-1), C['acc_lt']),
        ('BOX',           (0,0), (0,-1), 0.5,  C['g2']),
        ('BOX',           (1,0), (1,-1), 0.5,  C['g2']),
        ('BOX',           (2,0), (2,-1), 1.5,  C['accent']),
        ('TOPPADDING',    (0,0), (-1,-1), 20),
        ('BOTTOMPADDING', (0,0), (-1,-1), 20),
        ('LEFTPADDING',   (0,0), (-1,-1), 18),
        ('RIGHTPADDING',  (0,0), (-1,-1), 18),
        ('VALIGN',        (0,0), (-1,-1), 'TOP'),
    ]))
    story.append(hero_tbl)
    story.append(Spacer(1, 18))

    # ── 3. CLIENT INFO ──────────────────────────────────────────
    sex_label = "Masculin" if req.sex == "m" else "Feminin"
    act_map   = {
        "sedentar": "Sedentar", "usor_activ": "Usor activ",
        "moderat_activ": "Moderat activ", "foarte_activ": "Foarte activ",
        "extrem_activ": "Extrem de activ",
    }
    act_label = act_map.get(req.activity_level, req.activity_level)

    def field(label, value, accent=False):
        return [
            Paragraph(label.upper(), S['lbl']),
            Paragraph(str(value), S['val_acc'] if accent else S['val']),
            Spacer(1, 8),
        ]

    col_l = [
        Paragraph("CLIENT", S['sec']), Spacer(1, 12),
        *field("Prenume", req.client_name),
        *field("Varsta / Sex", f"{req.age} ani  ·  {sex_label}"),
        *field("Corp", f"{req.weight_kg} kg  ·  {req.height_cm} cm"),
        *field("Activitate", act_label),
    ]
    col_r = [
        Paragraph("OBIECTIV", S['sec']), Spacer(1, 12),
        *field("Planul ales", req.goal_display, accent=True),
        *field("Estimare saptamanala", req.weekly_display),
    ]

    if req.lean_body_mass_kg and req.estimated_bf_pct:
        fat_mass = round(req.weight_kg - req.lean_body_mass_kg, 1)
        col_r += [
            Spacer(1, 4),
            Paragraph("COMPOZITIE CORPORALA", S['sec']),
            Spacer(1, 12),
            *field("Masa slaba (muschi + oase)", f"{req.lean_body_mass_kg} kg"),
            *field("Grasime corporala", f"{req.estimated_bf_pct}%"),
            *field("Masa grasa", f"{fat_mass} kg"),
        ]

    info_tbl = Table([[col_l, col_r]], colWidths=[CW * 0.47, CW * 0.53])
    info_tbl.setStyle(TableStyle([
        ('VALIGN',       (0,0), (-1,-1), 'TOP'),
        ('LEFTPADDING',  (0,0), (0,-1),   0),
        ('RIGHTPADDING', (0,0), (0,-1),  22),
        ('LEFTPADDING',  (1,0), (1,-1),  22),
        ('RIGHTPADDING', (1,0), (-1,-1),  0),
        ('TOPPADDING',   (0,0), (-1,-1),  0),
        ('BOTTOMPADDING',(0,0), (-1,-1),  0),
    ]))
    story.append(info_tbl)
    story.append(Spacer(1, 18))
    story.append(HRFlowable(width=CW, color=C['g2'], thickness=0.5))
    story.append(Spacer(1, 18))

    # ── 4. MACRO CARDS ──────────────────────────────────────────
    story.append(Paragraph("MACRONUTRIENTI ZILNICI", S['sec']))
    story.append(Spacer(1, 14))

    total = req.total_kcal or 1
    p_pct = round(req.protein_kcal / total * 100)
    c_pct = round(req.carbs_kcal   / total * 100)
    f_pct = 100 - p_pct - c_pct
    PAD   = 16
    BAR_W = CW / 3 - PAD * 2

    def mini_bar(pct, color):
        filled = max(int(BAR_W * pct / 100), 4)
        empty  = max(int(BAR_W - filled), 4)
        t = Table([['', '']], colWidths=[filled, empty], rowHeights=[7])
        t.setStyle(TableStyle([
            ('BACKGROUND',    (0,0), (0,0), color),
            ('BACKGROUND',    (1,0), (1,0), C['surf2']),
            ('TOPPADDING',    (0,0), (-1,-1), 0),
            ('BOTTOMPADDING', (0,0), (-1,-1), 0),
            ('LEFTPADDING',   (0,0), (-1,-1), 0),
            ('RIGHTPADDING',  (0,0), (-1,-1), 0),
        ]))
        return t

    def macro_cell(label, grams, kcal, pct, color, gram_style_key):
        return [
            Paragraph(label.upper(), S['mc_lbl']),
            Spacer(1, 12),
            Paragraph(
                f"<b>{grams}</b><font size='13' color='#6b6560'> g</font>",
                S[gram_style_key],
            ),
            Spacer(1, 12),
            mini_bar(pct, color),
            Spacer(1, 8),
            Paragraph(f"{kcal} kcal  ·  {pct}%", S['mc_sub']),
        ]

    macro_data = [[
        macro_cell("Proteina",     req.protein_g, req.protein_kcal, p_pct, C['protein'], 'mc_g_p'),
        macro_cell("Carbohidrati", req.carbs_g,   req.carbs_kcal,   c_pct, C['carbs'],   'mc_g_c'),
        macro_cell("Grasimi",      req.fat_g,     req.fat_kcal,     f_pct, C['fat'],     'mc_g_f'),
    ]]

    macro_tbl = Table(macro_data, colWidths=[CW / 3] * 3)
    macro_tbl.setStyle(TableStyle([
        ('BACKGROUND',    (0,0), (-1,-1), C['surf']),
        ('BOX',           (0,0), (0,-1),  0.5, C['g2']),
        ('BOX',           (1,0), (1,-1),  0.5, C['g2']),
        ('BOX',           (2,0), (2,-1),  0.5, C['g2']),
        ('TOPPADDING',    (0,0), (-1,-1), PAD),
        ('BOTTOMPADDING', (0,0), (-1,-1), PAD),
        ('LEFTPADDING',   (0,0), (-1,-1), PAD),
        ('RIGHTPADDING',  (0,0), (-1,-1), PAD),
        ('VALIGN',        (0,0), (-1,-1), 'TOP'),
    ]))
    story.append(macro_tbl)
    story.append(Spacer(1, 12))

    story.append(DistBar(p_pct, c_pct, f_pct, width=CW, height=10))
    story.append(Spacer(1, 8))
    story.append(Paragraph(
        f"Proteina {p_pct}%    Carbohidrati {c_pct}%    Grasimi {f_pct}%",
        S['legend'],
    ))
    story.append(Spacer(1, 22))
    story.append(HRFlowable(width=CW, color=C['g2'], thickness=0.5))
    story.append(Spacer(1, 18))

    # ── 5. AI COACHING INSIGHT ──────────────────────────────────
    story.append(Paragraph("ANALIZA PERSONALIZATA  ·  AI COACHING", S['ins_lbl']))
    story.append(Spacer(1, 14))

    raw_paras = [p.strip() for p in req.coaching_insight.split('\n\n') if p.strip()]
    if not raw_paras:
        raw_paras = [req.coaching_insight.strip()]

    insight_items = []
    for i, txt in enumerate(raw_paras):
        if i == 0:
            txt = '\u201c' + txt
        if i == len(raw_paras) - 1:
            txt = txt + '\u201d'
        insight_items.append(Paragraph(txt, S['ins']))

    insight_box = Table([[insight_items]], colWidths=[CW])
    insight_box.setStyle(TableStyle([
        ('BACKGROUND',    (0,0), (-1,-1), C['surf2']),
        ('TOPPADDING',    (0,0), (-1,-1), 20),
        ('BOTTOMPADDING', (0,0), (-1,-1), 20),
        ('LEFTPADDING',   (0,0), (-1,-1), 22),
        ('RIGHTPADDING',  (0,0), (-1,-1), 22),
        ('LINEBEFORE',    (0,0), (0,-1),  3, C['accent']),
        ('LINECOLOR',     (0,0), (0,-1),  C['accent']),
    ]))
    story.append(insight_box)
    story.append(Spacer(1, 30))

    # ── 6. FOOTER ───────────────────────────────────────────────
    story.append(HRFlowable(width=CW, color=C['g2'], thickness=0.5))
    story.append(Spacer(1, 8))
    story.append(Paragraph(
        f"Noian Cristian  ·  Bazat pe inteligenta artificiala  ·  {today}",
        S['footer']
    ))

    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=MARGIN, bottomMargin=MARGIN
    )
    doc.build(story)
    buf.seek(0)
    return buf.read()

# ══════════════════════════════════════════════════════════════════
# CALCUL METABOLIC
# NOU Etapa 3: @lru_cache — substituie Redis pentru valori statice
# ══════════════════════════════════════════════════════════════════
@lru_cache(maxsize=None)   # 5 valori posibile → cache permanent în memorie
def get_activity_multiplier(level: str) -> float:
    return {
        "sedentar": 1.2, "usor_activ": 1.375, "moderat_activ": 1.55,
        "foarte_activ": 1.725, "extrem_activ": 1.9,
    }.get(level, 1.2)

def compute_metabolic_protocol(profile: ClientProfile) -> dict:
    bmr, formula_used, lbm_kg, effective_bf = select_bmr_formula(
        weight_kg=profile.weight_kg, height_cm=profile.height_cm,
        age=profile.age, sex=profile.sex,
        bf_pct=profile.body_fat_pct, body_type=profile.body_type,
    )
    tdee = bmr * get_activity_multiplier(profile.activity_level)

    goal_mapping = {
        "cut_bland": {"offset":-200,"weekly":"Slabire lenta (~0.2 kg/saptamana)","type":"deficit_bland","rate":-0.2,"p_g_per_kg":2.2,"fat_pct":0.25},
        "cut":       {"offset":-400,"weekly":"Slabire moderata (~0.4 kg/saptamana)","type":"deficit_moderat","rate":-0.4,"p_g_per_kg":2.4,"fat_pct":0.22},
        "mentinere": {"offset":0,   "weekly":"Mentinere & Recompozitie","type":"echilibrat","rate":0.0,"p_g_per_kg":2.0,"fat_pct":0.25},
        "bulk_lean": {"offset":200, "weekly":"Masa curata (~0.2 kg/saptamana)","type":"surplus_lean","rate":0.2,"p_g_per_kg":2.0,"fat_pct":0.25},
        "bulk":      {"offset":400, "weekly":"Masa moderata (~0.4 kg/saptamana)","type":"surplus_moderat","rate":0.4,"p_g_per_kg":1.8,"fat_pct":0.28},
    }

    s           = goal_mapping.get(profile.goal, goal_mapping["mentinere"])
    target_kcal = max(1200, round(tdee + s["offset"]))
    protein_g   = round(profile.weight_kg * s["p_g_per_kg"])
    protein_kcal= protein_g * 4
    fat_kcal    = target_kcal * s["fat_pct"]
    fat_g       = round(fat_kcal / 9)
    carbs_kcal  = max(0, target_kcal - (protein_kcal + fat_kcal))
    carbs_g     = round(carbs_kcal / 4)

    return {
        "bmr": round(bmr), "tdee": round(tdee), "target_calories": target_kcal,
        "weekly_change": {"type": s["type"], "rate": s["rate"], "display": s["weekly"]},
        "macros": {"protein_g":protein_g,"protein_kcal":protein_kcal,"carbs_g":carbs_g,
                   "carbs_kcal":carbs_kcal,"fat_g":fat_g,"fat_kcal":fat_kcal,"total_kcal":target_kcal},
        "formula_used": formula_used, "lbm_kg": lbm_kg, "effective_bf_pct": effective_bf,
    }

# ══════════════════════════════════════════════════════════════════
# PROMPT AI — conversațional, persoana a doua
# NOU Etapa 3: @lru_cache(maxsize=1) — string constant, calculat o singură dată
# ══════════════════════════════════════════════════════════════════
@lru_cache(maxsize=1)      # String constant — niciodată nu se schimbă
def build_elite_system_prompt() -> str:
    return (
        "Ești un coach de nutriție și fitness care vorbești direct cu clientul, față în față. "
        "Ești ca un prieten bine pregătit — direct, cald și practic. Nu ești un robot, nu ești un doctor.\n\n"
        "REGULA #1: Vorbești MEREU la persoana a doua (TU, nu EL/EA, nu 'clientul').\n"
        "GREȘIT: 'Clientul are un TDEE de 3196 kcal.'\n"
        "CORECT: 'TDEE-ul tău de 3196 kcal înseamnă că arzi cam atât într-o zi normală.'\n\n"
        "REGULA #2: Dacă folosești un termen tehnic, explică-l imediat în română simplă.\n"
        "REGULA #3: 3 paragrafe scurte, fiecare cu UN singur mesaj clar și acționabil.\n"
        "REGULA #4: Zero fraze goale — Felicitări!, Ești pe drumul cel bun!, Succes!\n"
        "REGULA #5: Menționează 2-3 cifre concrete din date. Fac mesajul personal.\n"
        "REGULA #6: Scrie pentru oricine — de la 16 la 65 de ani trebuie să înțeleagă la fel.\n\n"
        "STRUCTURA:\n"
        "P1 — Ce înseamnă cifrele tale în practică.\n"
        "P2 — Cel mai important lucru concret cu mâncarea: când, cât, ce.\n"
        "P3 — Un sfat de mișcare + o propoziție despre cine ESTE această persoană ('Tu ești...').\n\n"
        "Maxim 200 de cuvinte. Exclusiv română."
    )

def build_user_context(profile: ClientProfile, calc: dict) -> str:
    m = calc["macros"]
    ctx = (
        f"Vorbești DIRECT cu {profile.name} (folosește 'tu', 'ai', 'vei'):\n\n"
        f"- {'Bărbat' if profile.sex == 'm' else 'Femeie'}, {profile.age} ani\n"
        f"- {profile.weight_kg} kg, {profile.height_cm} cm\n"
        f"- Activitate: {profile.activity_level}\n"
        f"- Obiectiv: {profile.goal} ({calc['weekly_change']['display']})\n\n"
    )
    if calc.get("lbm_kg"):
        ctx += (
            f"COMPOZIȚIE ({calc['formula_used']}):\n"
            f"- BF%: {calc.get('effective_bf_pct')}%\n"
            f"- Masă slabă: {calc.get('lbm_kg')} kg\n\n"
        )
    ctx += (
        f"CALCULE:\n"
        f"- BMR: {calc['bmr']} kcal | TDEE: {calc['tdee']} kcal | Țintă: {calc['target_calories']} kcal/zi\n"
        f"- P: {m['protein_g']}g | C: {m['carbs_g']}g | G: {m['fat_g']}g\n"
    )
    return ctx

# ══════════════════════════════════════════════════════════════════
# ENDPOINTS — Core (nemodificate față de v1.4)
# ══════════════════════════════════════════════════════════════════
@app.post("/calculate", response_model=CalculationResponse)
async def handle_calculation(
    profile: ClientProfile,
    user_email: Optional[str] = Depends(get_current_user_email)
):
    try:
        data = compute_metabolic_protocol(profile)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    insight = ""
    if GROQ_API_KEY:
        try:
            r = await groq_client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[
                    {"role": "system", "content": build_elite_system_prompt()},
                    {"role": "user",   "content": build_user_context(profile, data)},
                ],
                temperature=0.7, max_tokens=600, top_p=0.9,
            )
            insight = r.choices[0].message.content.strip()
        except Exception:
            insight = f"Tinta ta de {data['target_calories']} kcal/zi a fost calculata."
    else:
        insight = "Cheia API Groq nu este configurata."

    await save_session(profile, data, insight, user_email=user_email)

    return CalculationResponse(
        client=profile.name, bmr=data["bmr"], tdee=data["tdee"],
        target_calories=data["target_calories"], goal_selected=profile.goal,
        weekly_change=data["weekly_change"], macros=data["macros"],
        coaching_insight=insight,
        formula_used=data.get("formula_used", "Mifflin-St Jeor"),
        estimated_bf_pct=data.get("effective_bf_pct"),
        lean_body_mass_kg=data.get("lbm_kg"),
    )

@app.post("/report")
async def generate_report_pdf(req: ReportRequest):
    try:
        pdf = build_pdf(req)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    safe = req.client_name.lower().replace(" ", "_")
    fn   = f"raport_{safe}_{datetime.date.today().isoformat()}.pdf"
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{fn}"'})

@app.get("/auth/dev-verify", include_in_schema=False)
async def dev_verify_email(email: str):
    """Activare manuală cont — DOAR pe localhost."""
    if "localhost" not in os.getenv("APP_URL", ""):
        raise HTTPException(status_code=404)
    async with get_pool().acquire() as conn:
        result = await conn.execute(
            "UPDATE users SET is_verified = TRUE WHERE LOWER(email) = LOWER($1)",
            email.strip().lower(),
        )
    if "UPDATE 0" in result:
        raise HTTPException(status_code=404, detail="Email negăsit în DB.")
    return {"ok": True, "message": f"{email} activat. Te poți conecta acum."}

# ── NOU (corect) ──
@app.get("/")
async def read_index():
    if os.path.exists("index.html"):
        return FileResponse("index.html")
    raise HTTPException(status_code=404, detail="index.html nu a fost găsit.")

@app.get("/landing", include_in_schema=False)
async def landing_page():
    if os.path.exists("landing.html"):
        return FileResponse("landing.html")
    # Dacă landing.html lipsește, redirectăm la aplicație (nu buclă infinită)
    from fastapi.responses import RedirectResponse
    return RedirectResponse("/")

@app.get("/nutritionist", include_in_schema=False)
async def serve_nutritionist():
    return FileResponse("nutritionist.html")

@app.get("/programs", include_in_schema=False)
async def serve_programs():
    return FileResponse("programs.html")

@app.get("/coach", include_in_schema=False)
async def serve_coach():
    return FileResponse("coach.html")
@app.get("/admin", include_in_schema=False)
async def serve_admin():
    """Admin Dashboard — acces restricționat la ADMIN_EMAIL."""
    if os.path.exists("admin.html"):
        return FileResponse("admin.html")
    raise HTTPException(status_code=404, detail="admin.html lipsă.")

@app.get("/terms", include_in_schema=False)
async def terms_page():
    if os.path.exists("terms.html"):
        return FileResponse("terms.html")
    raise HTTPException(status_code=404, detail="terms.html nu a fost găsit.")

@app.get("/privacy", include_in_schema=False)
async def privacy_page():
    if os.path.exists("privacy.html"):
        return FileResponse("privacy.html")
    raise HTTPException(status_code=404, detail="privacy.html nu a fost găsit.")

@app.get("/gdpr.js", include_in_schema=False)
async def gdpr_js_file():
    if os.path.exists("gdpr.js"):
        return FileResponse("gdpr.js", media_type="application/javascript",
                            headers={"Cache-Control": "public, max-age=86400"})
    raise HTTPException(status_code=404, detail="gdpr.js nu a fost găsit.")

# ── Autentificare ──────────────────────────────────────────────────────────────

@app.post("/auth/signup")
async def signup(user: UserAuth, ref: str | None = Query(default=None)):
    hashed_pw = get_password_hash(user.password)
    success   = await create_user(user.email, hashed_pw)
    if not success:
        raise HTTPException(status_code=400, detail="Emailul este deja înregistrat.")

    # Activează contul direct — fără verificare email
    async with get_pool().acquire() as conn:
        await conn.execute(
            "UPDATE users SET is_verified = TRUE WHERE LOWER(email) = LOWER($1)",
            user.email.strip().lower(),
        )
    # Procesează codul de referral dacă a fost furnizat
    if ref:
        try:
            await create_referral_pending(ref_code=ref, referred_email=user.email)
        except Exception as _e:
            print(f"⚠️  Referral create_pending error: {_e}")

    print(f"✅  Cont creat și activat direct: {user.email}")
    return {
        "ok":      True,
        "message": "Cont creat cu succes! Te poți conecta acum.",
        "email":   user.email,
    }

@app.post("/auth/login")
async def login(user: UserAuth):
    db_user = await get_user_by_email(user.email)
    if not db_user or not verify_password(user.password, db_user["password_hash"]):
        raise HTTPException(status_code=401, detail="Email sau parola incorecte.")
    access_token = create_access_token(data={"sub": user.email})
    return {"access_token": access_token, "token_type": "bearer"}

# ══════════════════════════════════════════════════════════════════
# ENDPOINTS — P1: Profil Persistent & Check-in-uri (NOU v1.4)
# ══════════════════════════════════════════════════════════════════

@app.post("/profile/save")
async def endpoint_save_profile(
    req: ProfileSaveRequest,
    email: str = Depends(require_user_email),
):
    await save_profile(
        email=email,
        height_cm=req.height_cm,
        age=req.age,
        sex=req.sex,
        activity_level=req.activity_level,
        goal=req.goal,
        initial_weight_kg=req.initial_weight_kg,
    )
    return {"ok": True, "message": "Profil salvat."}


@app.get("/profile/get")
async def endpoint_get_profile(email: str = Depends(require_user_email)):
    profile = await get_profile(email)
    return {"profile": profile}


@app.post("/checkin/save")
async def endpoint_save_checkin(
    req: CheckinRequest,
    email: str = Depends(require_user_email),
):
    result = await save_checkin(email, req.weight_kg, req.notes)
    return {"ok": True, **result}


@app.get("/checkin/history")
async def endpoint_checkin_history(email: str = Depends(require_user_email)):
    checkins = await get_checkins(email)
    return {"checkins": checkins, "count": len(checkins)}


@app.get("/checkin/summary")
async def endpoint_checkin_summary(email: str = Depends(require_user_email)):
    summary = await get_checkin_summary(email)
    return summary


@app.post("/chat")
async def ai_chat_coach(
    req: ChatRequest,
    email: str = Depends(require_user_email),
):
    profile, checkins, summary, sessions, user_settings = await asyncio.gather(
        get_profile(email),
        get_checkins(email, limit=30),
        get_checkin_summary(email),
        get_user_sessions(email, limit=1),
        get_settings(email),
    )
    last_session = sessions[0] if sessions else None

    system_prompt = build_coach_system_prompt(
        profile=profile,
        checkins=checkins,
        summary=summary,
        last_session=last_session,
        persona=user_settings.get("ai_persona", "empatic"),
    )

    messages = [{"role": "system", "content": system_prompt}]
    for msg in req.history:
        messages.append({"role": msg.role, "content": msg.content})
    messages.append({"role": "user", "content": req.message})

    if not GROQ_API_KEY:
        raise HTTPException(status_code=503, detail="GROQ_API_KEY lipsește din configurare.")

    try:
        r = await groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=messages,
            temperature=0.75,
            max_tokens=600,
            top_p=0.9,
        )
        reply = r.choices[0].message.content.strip()
        return {"reply": reply, "ok": True}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Eroare AI: {str(e)}")

@app.get("/adaptive/analysis")
async def get_adaptive_analysis(email: str = Depends(require_user_email)):
    checkins, profile, sessions, food_stats = await asyncio.gather(
        get_checkins(email, limit=90),
        get_profile(email),
        get_user_sessions(email, limit=1),
        get_food_intake_stats(email, days=14),
    )
    last_session = sessions[0] if sessions else None
    actual_kcal  = food_stats["avg_daily_kcal"] if food_stats["has_data"] else None

    result = run_adaptive_analysis(
        checkins=checkins,
        profile=profile,
        last_session=last_session,
        actual_kcal=actual_kcal,
    )

    result["food_intake"] = food_stats
    return result

@app.get("/auth/is-admin", include_in_schema=False)
async def check_is_admin(email: str = Depends(require_user_email)):
    """Returnează dacă emailul curent este adminul aplicației."""
    admin_email = os.getenv("ADMIN_EMAIL", "").strip().lower()
    return {"is_admin": bool(admin_email) and email.lower() == admin_email}

@app.get("/auth/my-sessions")
async def get_my_sessions(email: str = Depends(require_user_email)):
    sessions = await get_user_sessions(email, limit=10)
    return {"sessions": sessions, "count": len(sessions)}


@app.post("/chat/stream")
async def ai_chat_coach_stream(
    req: ChatRequest,
    email: str = Depends(require_user_email),
):
   
    profile      = await get_profile(email)
    checkins     = await get_checkins(email, limit=30)
    summary      = await get_checkin_summary(email)
    sessions     = await get_user_sessions(email, limit=1)
    last_session = sessions[0] if sessions else None
    user_settings = await get_settings(email)

    system_prompt = build_coach_system_prompt(
        profile=profile,
        checkins=checkins,
        summary=summary,
        last_session=last_session,
        persona=user_settings.get("ai_persona", "empatic"),
    )

    messages = [{"role": "system", "content": system_prompt}]
    for msg in req.history:
        messages.append({"role": msg.role, "content": msg.content})
    messages.append({"role": "user", "content": req.message})

    if not GROQ_API_KEY:
        raise HTTPException(status_code=503, detail="GROQ_API_KEY lipsește.")

    async def event_stream():
        try:
            stream = await groq_client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=messages,
                temperature=0.75,
                max_tokens=600,
                top_p=0.9,
                stream=True,
            )
            async for chunk in stream:
                delta = chunk.choices[0].delta.content
                if delta:
                    yield f"data: {json.dumps({'delta': delta}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'done': True})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":    "no-cache",
            "X-Accel-Buffering": "no",   # Esențial pentru Nginx pe Render.com
        },
    )


# ══════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)