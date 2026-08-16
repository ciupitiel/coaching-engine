# =============================================================================
#  pdf_report_generator.py — P7: Raport Săptămânal PDF
#  Noian Cristian · Coaching Engine
#  -----------------------------------------------------------------------------
#  Generează PDF profesional cu:
#    · Header/footer branded (Noian Lab)
#    · 4 metrici cheie (kcal medie, proteină, Δ greutate, streak)
#    · Grafic linie greutate (ultimele 14 zile)
#    · Bare de compliance macros vs target
#    · Tabel zilnic detaliat (7 zile)
#    · Narativă AI generată de Groq (opțional, 3 fraze)
#
#  Funcție publică:
#    generate_weekly_pdf_report(email, week_offset=0, groq_client=None) → bytes
#
#  Font: DejaVu Sans (din apt fonts-dejavu în Dockerfile)
#  Fallback: Helvetica (fără diacritice) dacă fontul nu e găsit local
# =============================================================================

import io
import os
import datetime

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, Flowable,
)
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from database_analytics import get_weekly_food_summary
from database import get_checkins, get_profile, get_user_sessions
from database_streak import compute_streak


# ─────────────────────────────────────────────────────────────────────────────
#  FONT — DejaVu cu fallback la Helvetica
# ─────────────────────────────────────────────────────────────────────────────

_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",       # Linux (Docker)
    "/usr/local/share/fonts/DejaVuSans.ttf",                  # macOS Homebrew
    "DejaVuSans.ttf",                                          # folder curent
]
_FONT_BOLD_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/local/share/fonts/DejaVuSans-Bold.ttf",
    "DejaVuSans-Bold.ttf",
]
_FONTS_OK = False


def _try_register():
    global _FONTS_OK
    if _FONTS_OK:
        return
    rp = next((p for p in _FONT_CANDIDATES      if os.path.exists(p)), None)
    bp = next((p for p in _FONT_BOLD_CANDIDATES if os.path.exists(p)), None)
    if rp and bp:
        try:
            pdfmetrics.registerFont(TTFont("DV",   rp))
            pdfmetrics.registerFont(TTFont("DV-B", bp))
            _FONTS_OK = True
        except Exception as e:
            print(f"⚠️  PDF font registration: {e}")


def _f(bold: bool = False) -> str:
    """Returnează fontul disponibil (DejaVu sau Helvetica fallback)."""
    _try_register()
    if _FONTS_OK:
        return "DV-B" if bold else "DV"
    return "Helvetica-Bold" if bold else "Helvetica"


# ─────────────────────────────────────────────────────────────────────────────
#  CULORI
# ─────────────────────────────────────────────────────────────────────────────

C_ACCENT  = colors.HexColor("#C4622D")
C_ACCENTL = colors.HexColor("#F5E8DF")
C_DARK    = colors.HexColor("#111111")
C_TEXT1   = colors.HexColor("#1A1A1A")
C_TEXT2   = colors.HexColor("#666666")
C_TEXT3   = colors.HexColor("#AAAAAA")
C_BORDER  = colors.HexColor("#E0E0E0")
C_ROW_ALT = colors.HexColor("#F8F8F8")
C_GREEN   = colors.HexColor("#22C55E")
C_YELLOW  = colors.HexColor("#EAB308")
C_RED     = colors.HexColor("#EF4444")


# ─────────────────────────────────────────────────────────────────────────────
#  CUSTOM FLOWABLES
# ─────────────────────────────────────────────────────────────────────────────

class MetricRow(Flowable):
    """Rând cu N carduri de metrici (kcal / proteină / greutate / streak)."""

    def __init__(self, metrics: list[dict], width: float = 180*mm, height: float = 28*mm):
        super().__init__()
        self.metrics = metrics
        self.width   = width
        self.height  = height

    def wrap(self, aW, aH):
        return self.width, self.height

    def draw(self):
        c   = self.canv
        n   = len(self.metrics)
        gap = 4 * mm
        cw  = (self.width - (n - 1) * gap) / n
        ch  = self.height

        for i, m in enumerate(self.metrics):
            x = i * (cw + gap)

            # Card background
            c.setFillColor(C_ROW_ALT)
            c.setStrokeColor(C_BORDER)
            c.setLineWidth(0.4)
            c.roundRect(x, 0, cw, ch, 2 * mm, fill=1, stroke=1)

            # Accent bar (top edge)
            c.setFillColor(C_ACCENT)
            c.rect(x, ch - 1.2 * mm, cw, 1.2 * mm, fill=1, stroke=0)

            # Value (large)
            c.setFont(_f(bold=True), 14)
            c.setFillColor(C_ACCENT if m.get("accent") else C_TEXT1)
            c.drawCentredString(x + cw / 2, ch * 0.54, m.get("value", "—"))

            # Label
            c.setFont(_f(), 7)
            c.setFillColor(C_TEXT2)
            c.drawCentredString(x + cw / 2, ch * 0.28, m.get("label", ""))

            # Sub label
            if m.get("sub"):
                c.setFont(_f(), 6.5)
                c.setFillColor(C_TEXT3)
                c.drawCentredString(x + cw / 2, 2 * mm, m["sub"])


class WeightChart(Flowable):
    """Grafic linie greutate din check-in-uri reale."""

    def __init__(self, checkins: list[dict], width: float = 180*mm, height: float = 55*mm):
        super().__init__()
        self.checkins = checkins
        self.width    = width
        self.height   = height

    def wrap(self, aW, aH):
        return self.width, self.height

    def draw(self):
        c  = self.canv
        W  = self.width
        H  = self.height

        # Margini interioare
        ML, MR, MT, MB = 18 * mm, 5 * mm, 4 * mm, 10 * mm
        px = ML
        py = MB
        pw = W - ML - MR
        ph = H - MT - MB

        if not self.checkins or len(self.checkins) < 2:
            c.setFont(_f(), 8)
            c.setFillColor(C_TEXT3)
            c.drawCentredString(W / 2, H / 2, "Date insuficiente — adauga check-in-uri de greutate")
            return

        weights = [float(ci.get("weight_kg", 0)) for ci in self.checkins]
        dates   = [ci.get("date", "")[:10]        for ci in self.checkins]
        n       = len(weights)

        wmin, wmax = min(weights), max(weights)
        wr = max(wmax - wmin, 0.5)

        def gx(i): return px + (i / (n - 1)) * pw if n > 1 else px + pw / 2
        def gy(w): return py + ((w - wmin) / wr) * ph

        # Background plot area
        c.setFillColor(colors.HexColor("#FAFAFA"))
        c.setStrokeColor(C_BORDER)
        c.setLineWidth(0.3)
        c.rect(px, py, pw, ph, fill=1, stroke=1)

        # Grid horizontală (2 linii)
        c.setStrokeColor(C_BORDER)
        c.setLineWidth(0.25)
        for k in (1, 2):
            yg = py + (k / 3) * ph
            c.line(px, yg, px + pw, yg)

        # Labels axa Y (4 valori)
        c.setFont(_f(), 6.5)
        c.setFillColor(C_TEXT3)
        for k in range(4):
            yg  = py + (k / 3) * ph
            val = wmin + (k / 3) * wr
            c.drawRightString(px - 2, yg - 2.5, f"{val:.1f}")

        # Area fill sub linie
        c.setFillColor(C_ACCENTL)
        path = c.beginPath()
        path.moveTo(gx(0), py)
        path.lineTo(gx(0), gy(weights[0]))
        for i in range(1, n):
            path.lineTo(gx(i), gy(weights[i]))
        path.lineTo(gx(n - 1), py)
        path.close()
        c.drawPath(path, fill=1, stroke=0)

        # Linia principală
        c.setStrokeColor(C_ACCENT)
        c.setLineWidth(1.5)
        path = c.beginPath()
        path.moveTo(gx(0), gy(weights[0]))
        for i in range(1, n):
            path.lineTo(gx(i), gy(weights[i]))
        c.drawPath(path, fill=0, stroke=1)

        # Puncte (cerc alb + cerc accent)
        for i in range(n):
            xi, yi = gx(i), gy(weights[i])
            c.setFillColor(colors.white)
            c.circle(xi, yi, 1.6 * mm, fill=1, stroke=0)
            c.setFillColor(C_ACCENT)
            c.circle(xi, yi, 1.0 * mm, fill=1, stroke=0)

        # Labels axa X (max 5 pentru a evita aglomerarea)
        idxs = sorted({0, n // 4, n // 2, 3 * n // 4, n - 1} if n >= 5 else set(range(n)))
        c.setFont(_f(), 6)
        c.setFillColor(C_TEXT3)
        for i in idxs:
            d = dates[i][5:].replace("-", "/")  # MM/DD
            c.drawCentredString(gx(i), py - 7, d)


class MacroBar(Flowable):
    """Bară orizontală de aderență macro față de target."""

    def __init__(self, label: str, actual: int, target: int, unit: str = "kcal",
                 width: float = 180 * mm):
        super().__init__()
        self.label  = label
        self.actual = actual
        self.target = target
        self.unit   = unit
        self.width  = width

    def wrap(self, aW, aH):
        return self.width, 8 * mm

    def draw(self):
        c  = self.canv
        W  = self.width
        h  = 8 * mm
        ym = h / 2

        pct = min(150, round(self.actual / self.target * 100)) if self.target else 0
        bar_color = C_GREEN if pct >= 85 else (C_YELLOW if pct >= 60 else C_RED)

        LW = 28 * mm
        PW = 13 * mm
        VW = 24 * mm
        BW = W - LW - PW - VW - 4 * mm
        BH = 4.5 * mm
        BY = ym - BH / 2

        # Label
        c.setFont(_f(bold=True), 8)
        c.setFillColor(C_TEXT1)
        c.drawString(0, ym - 2.5, self.label)

        # Track (background)
        c.setFillColor(C_BORDER)
        c.roundRect(LW, BY, BW, BH, 2 * mm, fill=1, stroke=0)

        # Fill
        fw = min((pct / 100) * BW, BW) if pct > 0 else 0
        if fw > 0:
            c.setFillColor(bar_color)
            c.roundRect(LW, BY, fw, BH, 2 * mm, fill=1, stroke=0)

        # Procentaj
        c.setFont(_f(bold=True), 8)
        c.setFillColor(bar_color)
        c.drawCentredString(LW + BW + PW / 2, ym - 2.5, f"{pct}%")

        # Actual/Target
        c.setFont(_f(), 7)
        c.setFillColor(C_TEXT3)
        c.drawRightString(W, ym - 2.5, f"{self.actual}/{self.target} {self.unit}")


# ─────────────────────────────────────────────────────────────────────────────
#  HEADER + FOOTER (onFirstPage / onLaterPages callback)
# ─────────────────────────────────────────────────────────────────────────────

def _page_callback(canvas, doc, week_label: str, email: str) -> None:
    """Desenează header dark și footer pe fiecare pagină."""
    canvas.saveState()
    W, H = A4

    # ── Header ─────────────────────────────────────────────────────────────
    canvas.setFillColor(C_DARK)
    canvas.rect(0, H - 18 * mm, W, 18 * mm, fill=1, stroke=0)

    # Dot logo
    canvas.setFillColor(C_ACCENT)
    canvas.circle(16 * mm, H - 9 * mm, 2.5 * mm, fill=1, stroke=0)

    # Brand text
    canvas.setFont(_f(bold=True), 10)
    canvas.setFillColor(colors.white)
    canvas.drawString(22 * mm, H - 7.5 * mm, "NOIAN LAB")
    canvas.setFont(_f(), 7.5)
    canvas.setFillColor(colors.HexColor("#888888"))
    canvas.drawString(22 * mm, H - 13.5 * mm, "Bazat pe inteligenta artificiala")

    # Dreapta
    canvas.setFont(_f(bold=True), 8.5)
    canvas.setFillColor(colors.HexColor("#BBBBBB"))
    canvas.drawRightString(W - 12 * mm, H - 7.5 * mm, "RAPORT SAPTAMANAL")
    canvas.setFont(_f(), 7.5)
    canvas.setFillColor(colors.HexColor("#777777"))
    canvas.drawRightString(W - 12 * mm, H - 13.5 * mm, week_label)

    # Linie accent
    canvas.setStrokeColor(C_ACCENT)
    canvas.setLineWidth(1.2)
    canvas.line(0, H - 18 * mm, W, H - 18 * mm)

    # ── Footer ─────────────────────────────────────────────────────────────
    canvas.setFillColor(C_ROW_ALT)
    canvas.rect(0, 0, W, 10 * mm, fill=1, stroke=0)
    canvas.setStrokeColor(C_BORDER)
    canvas.setLineWidth(0.3)
    canvas.line(0, 10 * mm, W, 10 * mm)

    canvas.setFont(_f(), 7)
    canvas.setFillColor(C_TEXT3)
    now_str = datetime.datetime.now().strftime("%d.%m.%Y %H:%M")
    canvas.drawString(12 * mm, 3.5 * mm, f"Generat: {now_str}")
    canvas.drawCentredString(W / 2, 3.5 * mm, email)
    canvas.drawRightString(W - 12 * mm, 3.5 * mm, "coaching.noianlab.ro")

    canvas.restoreState()


# ─────────────────────────────────────────────────────────────────────────────
#  STILURI PARAGRAPH
# ─────────────────────────────────────────────────────────────────────────────

def _build_styles() -> dict:
    f  = _f()
    fb = _f(bold=True)
    return {
        "eyebrow": ParagraphStyle("eyebrow", fontName=fb, fontSize=8,
                                   textColor=C_ACCENT, spaceAfter=2, leading=12),
        "h1":      ParagraphStyle("h1", fontName=fb, fontSize=18,
                                   textColor=C_TEXT1, spaceAfter=2, leading=22),
        "h2":      ParagraphStyle("h2", fontName=fb, fontSize=10.5,
                                   textColor=C_TEXT1, spaceAfter=6, spaceBefore=10, leading=15),
        "body":    ParagraphStyle("body", fontName=f, fontSize=9,
                                   textColor=C_TEXT2, spaceAfter=6, leading=14),
        "body_b":  ParagraphStyle("body_b", fontName=fb, fontSize=9,
                                   textColor=C_TEXT1, spaceAfter=6, leading=14),
        "th":      ParagraphStyle("th", fontName=fb, fontSize=8,
                                   textColor=colors.white, leading=11),
        "td":      ParagraphStyle("td", fontName=f, fontSize=8.5,
                                   textColor=C_TEXT1, leading=12),
    }


# ─────────────────────────────────────────────────────────────────────────────
#  UTILITARE
# ─────────────────────────────────────────────────────────────────────────────

_MONTHS_RO = [
    "Ian", "Feb", "Mar", "Apr", "Mai", "Iun",
    "Iul", "Aug", "Sep", "Oct", "Nov", "Dec"
]

GOAL_LABELS: dict[str, str] = {
    "cut_bland":  "Taiere Blanda",
    "cut":        "Taiere Moderata",
    "mentinere":  "Mentinere",
    "bulk_lean":  "Lean Bulk",
    "bulk":       "Bulk Moderat",
}


def _week_range(offset: int = 0) -> tuple[datetime.date, datetime.date]:
    today  = datetime.date.today()
    monday = today - datetime.timedelta(days=today.weekday() + (-offset * 7))
    sunday = monday + datetime.timedelta(days=6)
    return monday, sunday


def _fmt_date(d: datetime.date) -> str:
    return f"{d.day} {_MONTHS_RO[d.month - 1]} {d.year}"


# ─────────────────────────────────────────────────────────────────────────────
#  AI NARRATIVE (opțional)
# ─────────────────────────────────────────────────────────────────────────────

async def _generate_narrative(
    groq_client,
    food_summary: dict,
    weight_change,
    targets: dict | None,
    goal: str,
) -> str:
    """Generează 3 fraze de analiză a săptămânii via Groq. Silent fail."""
    if not groq_client:
        return ""
    try:
        avg    = food_summary.get("avg_daily", {})
        days   = food_summary.get("days_logged", 0)
        t_kcal = (targets or {}).get("calories", 0)

        ctx = (
            f"Obiectiv: {GOAL_LABELS.get(goal, goal)}. "
            f"Zile loggate: {days}/7. "
            f"Calorii medii: {avg.get('calories', 0)} kcal"
            + (f" vs target {t_kcal} kcal" if t_kcal else "") + ". "
            + (f"Schimbare greutate saptamana: {weight_change:+.1f} kg. "
               if weight_change is not None else "")
            + f"Proteina medie: {avg.get('protein_g', 0)}g/zi."
        )

        r = await groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Esti un coach de nutritie. Scrie EXACT 3 fraze scurte in romana, "
                        "fara diacritice cu bytes speciali: "
                        "1) ce a mers bine (cu cifre concrete), "
                        "2) ce trebuie imbunatatit, "
                        "3) actiunea concreta pentru saptamana viitoare. "
                        "Fara titluri, bullets, sau cuvinte goale (Bravo, Felicitari). "
                        "Direct si specific."
                    ),
                },
                {"role": "user", "content": f"Analizeaza saptamana: {ctx}"},
            ],
            temperature=0.4,
            max_tokens=220,
        )
        return r.choices[0].message.content.strip()
    except Exception as e:
        print(f"⚠️  PDF narrative error: {e}")
        return ""


# ─────────────────────────────────────────────────────────────────────────────
#  FUNCȚIA PRINCIPALĂ
# ─────────────────────────────────────────────────────────────────────────────

async def generate_weekly_pdf_report(
    email:       str,
    week_offset: int = 0,
    groq_client       = None,
) -> bytes:
    """
    Generează PDF-ul raportului săptămânal al utilizatorului.

    Args:
        email       : emailul utilizatorului autentificat
        week_offset : 0=curentă, -1=trecută, -2=acum 2 săpt.
        groq_client : instanța Groq (opțional — narativă AI)

    Returns:
        bytes — conținut PDF gata de trimis ca Response HTTP
    """
    monday, sunday = _week_range(week_offset)
    week_label = f"{_fmt_date(monday)} — {_fmt_date(sunday)}"

    # ── 1. Date din DB ─────────────────────────────────────────────────────
    food_summary = await get_weekly_food_summary(email, week_offset=week_offset)
    checkins     = await get_checkins(email, limit=30)
    profile      = await get_profile(email)
    sessions     = await get_user_sessions(email, limit=1)
    streak_data  = await compute_streak(email)
    last_session = sessions[0] if sessions else None

    targets: dict | None = None
    if last_session:
        targets = {
            "calories":  int(last_session.get("target_kcal") or 0),
            "protein_g": int(last_session.get("protein_g")   or 0),
            "carbs_g":   int(last_session.get("carbs_g")     or 0),
            "fat_g":     int(last_session.get("fat_g")       or 0),
        }

    goal = (profile or {}).get("goal", "mentinere")

    # Check-in-uri din săptămâna selectată (pentru Δ greutate)
    week_ci = [
        ci for ci in checkins
        if str(monday) <= ci.get("date", "") <= str(sunday)
    ]
    # Ultimele 14 zile pentru grafic (mai mult context vizual)
    chart_ci = checkins[-14:] if len(checkins) > 2 else checkins

    # Schimbare greutate
    weight_change: float | None = None
    if len(week_ci) >= 2:
        weight_change = float(week_ci[-1]["weight_kg"]) - float(week_ci[0]["weight_kg"])
    elif len(checkins) >= 2:
        weight_change = float(checkins[-1]["weight_kg"]) - float(checkins[-2]["weight_kg"])

    current_weight = checkins[-1].get("weight_kg") if checkins else None

    # ── 2. Narativă AI ─────────────────────────────────────────────────────
    narrative = await _generate_narrative(groq_client, food_summary, weight_change, targets, goal)

    # ── 3. Build PDF ────────────────────────────────────────────────────────
    buf = io.BytesIO()
    st  = _build_styles()

    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=15 * mm,
        rightMargin=15 * mm,
        topMargin=22 * mm,   # 18mm header + 4mm gap
        bottomMargin=14 * mm, # 10mm footer + 4mm gap
    )

    # Callback header/footer — capturat prin closure
    cb = lambda canvas, d: _page_callback(canvas, d, week_label, email)

    story  = []
    avg    = food_summary.get("avg_daily", {})
    total  = food_summary.get("total", {})
    days_l = food_summary.get("days_logged", 0)

    # ── TITLU ────────────────────────────────────────────────────────────────
    story.append(Spacer(1, 3 * mm))
    story.append(Paragraph("RAPORT SAPTAMANAL", st["eyebrow"]))
    story.append(Paragraph(week_label, st["h1"]))
    story.append(Paragraph(
        f"Obiectiv: <b>{GOAL_LABELS.get(goal, goal)}</b>  ·  "
        f"{days_l}/7 zile cu loguri alimentare",
        st["body"],
    ))
    story.append(HRFlowable(width="100%", thickness=0.5, color=C_BORDER, spaceAfter=4))

    # ── METRICI CHEIE ─────────────────────────────────────────────────────────
    story.append(Paragraph("SUMAR SAPTAMANA", st["h2"]))

    streak = streak_data.get("current_streak", 0)
    wc_str = f"{weight_change:+.1f} kg" if weight_change is not None else "—"

    metrics = [
        {
            "value": str(avg.get("calories", 0)),
            "label": "kcal / zi",
            "sub":   f"target: {targets['calories'] if targets else '—'} kcal",
        },
        {
            "value": f"{avg.get('protein_g', 0)}g",
            "label": "proteina / zi",
            "sub":   f"target: {targets['protein_g'] if targets else '—'}g",
        },
        {
            "value":  wc_str,
            "label":  "delta greutate",
            "sub":    f"curent: {current_weight} kg" if current_weight else "",
            "accent": True,
        },
        {
            "value": str(streak),
            "label": "zile streak",
            "sub":   f"record: {streak_data.get('longest_streak', 0)}",
        },
    ]
    story.append(MetricRow(metrics, width=180 * mm, height=28 * mm))
    story.append(Spacer(1, 5 * mm))

    # ── GRAFIC GREUTATE ───────────────────────────────────────────────────────
    if len(chart_ci) >= 2:
        story.append(Paragraph("PROGRES GREUTATE (ultimele 14 zile)", st["h2"]))
        story.append(WeightChart(chart_ci, width=180 * mm, height=55 * mm))
        story.append(Spacer(1, 3 * mm))

    # ── COMPLIANCE MACROS ─────────────────────────────────────────────────────
    if targets and days_l > 0:
        story.append(Paragraph("ADERENTA MACROS (medie zilnica vs target)", st["h2"]))
        macro_defs = [
            ("Calorii",      avg.get("calories",  0), targets["calories"],  "kcal"),
            ("Proteina",     avg.get("protein_g", 0), targets["protein_g"], "g"),
            ("Carbohidrati", avg.get("carbs_g",   0), targets["carbs_g"],   "g"),
            ("Grasimi",      avg.get("fat_g",     0), targets["fat_g"],     "g"),
        ]
        for lbl, act, tgt, unit in macro_defs:
            if tgt > 0:
                story.append(MacroBar(lbl, act, tgt, unit, width=180 * mm))
                story.append(Spacer(1, 2 * mm))
        story.append(Spacer(1, 3 * mm))

    # ── TABEL ZILNIC ──────────────────────────────────────────────────────────
    story.append(Paragraph("DETALIU ZILNIC", st["h2"]))

    daily = food_summary.get("daily_breakdown", [])
    if daily:
        th, td = st["th"], st["td"]

        rows = [[
            Paragraph("Zi",      th),
            Paragraph("Data",    th),
            Paragraph("Calorii", th),
            Paragraph("Prot",    th),
            Paragraph("Carb",    th),
            Paragraph("Gras",    th),
            Paragraph("Logat",   th),
        ]]

        for d in daily:
            has  = d.get("has_logs", False)
            fut  = d.get("is_future", False)
            date_obj = datetime.date.fromisoformat(d["date"]) if d.get("date") else None
            dfmt = f"{date_obj.day:02d}.{date_obj.month:02d}" if date_obj else "—"

            if fut:
                vals = [d.get("weekday",""), dfmt, "—", "—", "—", "—", "—"]
            elif has:
                vals = [
                    d.get("weekday",""), dfmt,
                    str(d.get("calories",  0)),
                    f"{d.get('protein_g', 0)}g",
                    f"{d.get('carbs_g',   0)}g",
                    f"{d.get('fat_g',     0)}g",
                    "Da",
                ]
            else:
                vals = [d.get("weekday",""), dfmt, "0", "0g", "0g", "0g", "Nu"]

            rows.append([Paragraph(v, td) for v in vals])

        # Rând total
        rows.append([
            Paragraph("TOTAL", st["body_b"]),
            Paragraph("—", td),
            Paragraph(str(total.get("calories",  0)), st["body_b"]),
            Paragraph(f"{total.get('protein_g', 0)}g", st["body_b"]),
            Paragraph(f"{total.get('carbs_g',   0)}g", st["body_b"]),
            Paragraph(f"{total.get('fat_g',     0)}g", st["body_b"]),
            Paragraph(f"{days_l}/7", st["body_b"]),
        ])

        # Coloane: 16+18+36+30+30+30+20 = 180mm (exact content width)
        col_w = [16*mm, 18*mm, 36*mm, 30*mm, 30*mm, 30*mm, 20*mm]

        tbl = Table(rows, colWidths=col_w, repeatRows=1)
        tbl.setStyle(TableStyle([
            # Header
            ("BACKGROUND",    (0, 0), (-1, 0), C_DARK),
            ("FONTNAME",      (0, 0), (-1, 0), _f(bold=True)),
            ("FONTSIZE",      (0, 0), (-1, 0), 8),
            ("TEXTCOLOR",     (0, 0), (-1, 0), colors.white),
            ("ALIGN",         (0, 0), (-1, 0), "CENTER"),
            ("TOPPADDING",    (0, 0), (-1, 0), 5),
            ("BOTTOMPADDING", (0, 0), (-1, 0), 5),
            # Body
            ("FONTNAME",      (0, 1), (-1, -2), _f()),
            ("FONTSIZE",      (0, 1), (-1, -2), 8.5),
            ("ALIGN",         (0, 1), (-1, -2), "CENTER"),
            ("ROWBACKGROUNDS",(0, 1), (-1, -2), [colors.white, C_ROW_ALT]),
            ("TOPPADDING",    (0, 1), (-1, -2), 4),
            ("BOTTOMPADDING", (0, 1), (-1, -2), 4),
            # Rând total
            ("BACKGROUND",    (0, -1), (-1, -1), colors.HexColor("#F0E8E2")),
            ("FONTNAME",      (0, -1), (-1, -1), _f(bold=True)),
            ("FONTSIZE",      (0, -1), (-1, -1), 8.5),
            ("ALIGN",         (0, -1), (-1, -1), "CENTER"),
            # Borders
            ("GRID",          (0, 0), (-1, -1), 0.3, C_BORDER),
            ("LINEBELOW",     (0, 0), (-1, 0),  1.0, C_ACCENT),
        ]))
        story.append(tbl)

    # ── NARATIVĂ AI ───────────────────────────────────────────────────────────
    if narrative:
        story.append(Spacer(1, 5 * mm))
        story.append(Paragraph("ANALIZA COACH · AI", st["h2"]))

        narr_tbl = Table(
            [[Paragraph(narrative, st["body"])]],
            colWidths=[180 * mm],
        )
        narr_tbl.setStyle(TableStyle([
            ("BACKGROUND",    (0, 0), (-1, -1), colors.HexColor("#FDF6F2")),
            ("LEFTPADDING",   (0, 0), (-1, -1), 14),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 14),
            ("TOPPADDING",    (0, 0), (-1, -1), 10),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
            ("LINEBEFORE",    (0, 0), (0, -1),  3, C_ACCENT),
            ("BOX",           (0, 0), (-1, -1), 0.5, C_BORDER),
        ]))
        story.append(narr_tbl)

    # ── GENERARE ──────────────────────────────────────────────────────────────
    doc.build(story, onFirstPage=cb, onLaterPages=cb)
    return buf.getvalue()