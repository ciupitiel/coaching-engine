import os
import httpx

RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
RESEND_FROM    = os.getenv("RESEND_FROM", "onboarding@resend.dev")
APP_URL        = os.getenv("APP_URL", "http://localhost:8000").rstrip("/")


# ─────────────────────────────────────────────────────────────────────────────
#  TEMPLATE HTML — Resetare parolă
# ─────────────────────────────────────────────────────────────────────────────

def _build_reset_html(reset_link: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="ro">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Resetare parolă</title>
</head>
<body style="margin:0;padding:0;background:#080808;font-family:Inter,-apple-system,Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#080808;padding:48px 20px;">
    <tr><td align="center">
      <table width="520" cellpadding="0" cellspacing="0"
             style="background:#111111;border-radius:12px;border:1px solid #1e1e1e;
                    overflow:hidden;max-width:520px;width:100%;">
        <tr>
          <td style="background:#0d0d0d;padding:24px 32px;border-bottom:1px solid #1a1a1a;">
            <table cellpadding="0" cellspacing="0" width="100%">
              <tr>
                <td>
                  <p style="margin:0;font-size:11px;color:#c4622d;font-weight:700;
                             letter-spacing:2.5px;text-transform:uppercase;">NOIAN CRISTIAN</p>
                  <p style="margin:3px 0 0;font-size:10px;color:#444;letter-spacing:1px;">
                    Bazat pe inteligență artificială</p>
                </td>
                <td align="right">
                  <div style="width:32px;height:32px;border-radius:50%;border:2px solid #c4622d;
                               display:inline-flex;align-items:center;justify-content:center;">
                    <div style="width:10px;height:10px;border-radius:50%;background:#c4622d;"></div>
                  </div>
                </td>
              </tr>
            </table>
          </td>
        </tr>
        <tr>
          <td style="padding:36px 32px 28px;">
            <h1 style="margin:0 0 12px;font-size:22px;font-weight:600;color:#d4d0cb;
                        letter-spacing:-0.3px;line-height:1.3;">Resetare parolă</h1>
            <p style="margin:0 0 20px;font-size:15px;color:#777;line-height:1.75;">
              Ai solicitat resetarea parolei pentru contul tău.
              Apasă butonul de mai jos pentru a seta o parolă nouă.</p>
            <p style="margin:0 0 32px;font-size:13.5px;color:#4a4a4a;line-height:1.7;">
              Link-ul este valabil <strong style="color:#666;">15 minute</strong>.
              Dacă nu ai solicitat tu această resetare, ignoră acest email —
              contul tău este în siguranță.</p>
            <table cellpadding="0" cellspacing="0">
              <tr>
                <td style="border-radius:8px;background:#c4622d;">
                  <a href="{reset_link}"
                     style="display:inline-block;padding:14px 36px;font-size:15px;
                            font-weight:600;color:#ffffff;text-decoration:none;
                            border-radius:8px;letter-spacing:0.2px;">
                    Resetează parola
                  </a>
                </td>
              </tr>
            </table>
          </td>
        </tr>
        <tr>
          <td style="padding:0 32px 28px;">
            <p style="margin:0 0 6px;font-size:11.5px;color:#3a3a3a;">
              Sau copiază acest link în browser:</p>
            <p style="margin:0;font-size:11px;color:#4a4a4a;word-break:break-all;line-height:1.6;">
              {reset_link}</p>
          </td>
        </tr>
        <tr>
          <td style="padding:18px 32px;border-top:1px solid #1a1a1a;">
            <p style="margin:0;font-size:11px;color:#2e2e2e;line-height:1.5;">
              Noian Cristian · Bazat pe inteligență artificială</p>
          </td>
        </tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""


# ─────────────────────────────────────────────────────────────────────────────
#  TEMPLATE HTML — Verificare email (NOU)
# ─────────────────────────────────────────────────────────────────────────────

def _build_verification_html(verify_link: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="ro">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Confirmă emailul</title>
</head>
<body style="margin:0;padding:0;background:#080808;font-family:Inter,-apple-system,Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#080808;padding:48px 20px;">
    <tr><td align="center">
      <table width="520" cellpadding="0" cellspacing="0"
             style="background:#111111;border-radius:12px;border:1px solid #1e1e1e;
                    overflow:hidden;max-width:520px;width:100%;">
        <!-- HEADER -->
        <tr>
          <td style="background:#0d0d0d;padding:24px 32px;border-bottom:1px solid #1a1a1a;">
            <table cellpadding="0" cellspacing="0" width="100%">
              <tr>
                <td>
                  <p style="margin:0;font-size:11px;color:#c4622d;font-weight:700;
                             letter-spacing:2.5px;text-transform:uppercase;">NOIAN CRISTIAN</p>
                  <p style="margin:3px 0 0;font-size:10px;color:#444;letter-spacing:1px;">
                    Bazat pe inteligență artificială</p>
                </td>
                <td align="right">
                  <div style="width:32px;height:32px;border-radius:50%;border:2px solid #c4622d;
                               display:inline-flex;align-items:center;justify-content:center;">
                    <div style="width:10px;height:10px;border-radius:50%;background:#c4622d;"></div>
                  </div>
                </td>
              </tr>
            </table>
          </td>
        </tr>
        <!-- BODY -->
        <tr>
          <td style="padding:36px 32px 28px;">
            <h1 style="margin:0 0 12px;font-size:22px;font-weight:600;color:#d4d0cb;
                        letter-spacing:-0.3px;line-height:1.3;">Confirmă adresa de email</h1>
            <p style="margin:0 0 16px;font-size:15px;color:#777;line-height:1.75;">
              Bun venit! Un singur pas mai e necesar pentru a-ți activa contul.</p>
            <p style="margin:0 0 32px;font-size:13.5px;color:#4a4a4a;line-height:1.7;">
              Apasă butonul de mai jos pentru a confirma că această adresă de email
              îți aparține și a-ți activa contul. Link-ul nu expiră, dar îl poți folosi
              o singură dată.</p>
            <!-- CTA -->
            <table cellpadding="0" cellspacing="0">
              <tr>
                <td style="border-radius:8px;background:#c4622d;">
                  <a href="{verify_link}"
                     style="display:inline-block;padding:14px 36px;font-size:15px;
                            font-weight:600;color:#ffffff;text-decoration:none;
                            border-radius:8px;letter-spacing:0.2px;">
                    Confirmă emailul
                  </a>
                </td>
              </tr>
            </table>
          </td>
        </tr>
        <!-- LINK FALLBACK -->
        <tr>
          <td style="padding:0 32px 28px;">
            <p style="margin:0 0 6px;font-size:11.5px;color:#3a3a3a;">
              Sau copiază acest link în browser:</p>
            <p style="margin:0;font-size:11px;color:#4a4a4a;word-break:break-all;line-height:1.6;">
              {verify_link}</p>
          </td>
        </tr>
        <!-- FOOTER -->
        <tr>
          <td style="padding:18px 32px;border-top:1px solid #1a1a1a;">
            <p style="margin:0;font-size:11px;color:#2e2e2e;line-height:1.5;">
              Noian Cristian · Bazat pe inteligență artificială</p>
          </td>
        </tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""


# ─────────────────────────────────────────────────────────────────────────────
#  UTILITARE INTERNE
# ─────────────────────────────────────────────────────────────────────────────

async def _send_email(to_email: str, subject: str, html: str) -> bool:
    """
    Trimite un email via Resend API.
    Factorizat din funcțiile publice — zero duplicare de cod.

    Returns:
        True dacă Resend a confirmat (HTTP 200/201), False altfel.
        Nu aruncă excepții — erorile sunt logged, nu propagate.
    """
    if not RESEND_API_KEY:
        return False

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(
                "https://api.resend.com/emails",
                headers={
                    "Authorization": f"Bearer {RESEND_API_KEY}",
                    "Content-Type":  "application/json",
                },
                json={
                    "from":    RESEND_FROM,
                    "to":      [to_email],
                    "subject": subject,
                    "html":    html,
                },
            )

        if r.status_code in (200, 201):
            return True

        print(f"⚠️  Resend error {r.status_code}: {r.text[:300]}")
        return False

    except httpx.TimeoutException:
        print(f"⚠️  Resend timeout pentru {to_email}")
        return False
    except Exception as exc:
        print(f"⚠️  Email send failed: {type(exc).__name__}: {exc}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
#  FUNCȚII PUBLICE
# ─────────────────────────────────────────────────────────────────────────────

async def send_password_reset_email(to_email: str, reset_token: str) -> bool:
    """
    Trimite emailul de resetare parolă prin Resend API.

    Link din email: {APP_URL}?reset_token={token}
    → JS din index.html detectează ?reset_token= și afișează formularul de reset.

    Args:
        to_email    : adresa destinatarului
        reset_token : UUID generat de create_reset_token() din database_password_reset.py

    Returns:
        True dacă Resend a confirmat (HTTP 200/201), False altfel.
    """
    if not RESEND_API_KEY:
        reset_link = f"{APP_URL}?reset_token={reset_token}"
        print(f"⚠️  RESEND_API_KEY lipsă — email nesimulat.")
        print(f"🔗  Link resetare (test local): {reset_link}")
        return False

    reset_link = f"{APP_URL}?reset_token={reset_token}"
    html       = _build_reset_html(reset_link)

    ok = await _send_email(
        to_email=to_email,
        subject="Resetare parolă · Noian Lab",
        html=html,
    )
    if ok:
        print(f"✉️  Reset email trimis către {to_email}")
    else:
        print(f"⚠️  Reset email FAILED pentru {to_email}")
    return ok


async def send_verification_email(to_email: str, verify_token: str) -> bool:
    """
    Trimite emailul de confirmare cont prin Resend API.

    Link din email: {APP_URL}?verify_token={token}
    → JS din index.html detectează ?verify_token= și apelează GET /auth/verify-email.

    Args:
        to_email     : adresa destinatarului (emailul la signup)
        verify_token : UUID generat de create_verification_token() din database_email_verification.py

    Returns:
        True dacă Resend a confirmat (HTTP 200/201), False altfel.
        Nu aruncă excepții — chiar dacă emailul eșuează, contul a fost creat.
        Token-ul rămâne valid → userul poate solicita resend din UI.
    """
    if not RESEND_API_KEY:
        verify_link = f"{APP_URL}?verify_token={verify_token}"
        print(f"⚠️  RESEND_API_KEY lipsă — email verificare nesimulat.")
        print(f"🔗  Link verificare (test local): {verify_link}")
        return False

    verify_link = f"{APP_URL}?verify_token={verify_token}"
    html        = _build_verification_html(verify_link)

    ok = await _send_email(
        to_email=to_email,
        subject="Confirmă emailul · Noian Lab",
        html=html,
    )
    if ok:
        print(f"✉️  Verification email trimis către {to_email}")
    else:
        print(f"⚠️  Verification email FAILED pentru {to_email} (token salvat în DB, resend disponibil)")
    return ok
# ─────────────────────────────────────────────────────────────────────────────
#  TEMPLATES HTML — Onboarding (ziua 1, 3, 7)
# ─────────────────────────────────────────────────────────────────────────────

def _header_html() -> str:
    return """
      <table width="520" cellpadding="0" cellspacing="0"
             style="background:#111111;border-radius:12px;border:1px solid #1e1e1e;
                    overflow:hidden;max-width:520px;width:100%;">
        <tr>
          <td style="background:#0d0d0d;padding:24px 32px;border-bottom:1px solid #1a1a1a;">
            <table cellpadding="0" cellspacing="0" width="100%"><tr>
              <td>
                <p style="margin:0;font-size:11px;color:#c4622d;font-weight:700;
                           letter-spacing:2.5px;text-transform:uppercase;">NOIAN CRISTIAN</p>
                <p style="margin:3px 0 0;font-size:10px;color:#444;letter-spacing:1px;">
                  Bazat pe inteligență artificială</p>
              </td>
              <td align="right">
                <div style="width:32px;height:32px;border-radius:50%;border:2px solid #c4622d;
                             display:inline-flex;align-items:center;justify-content:center;">
                  <div style="width:10px;height:10px;border-radius:50%;background:#c4622d;"></div>
                </div>
              </td>
            </tr></table>
          </td>
        </tr>"""


def _footer_html() -> str:
    return """
        <tr>
          <td style="padding:18px 32px;border-top:1px solid #1a1a1a;">
            <p style="margin:0;font-size:11px;color:#2e2e2e;line-height:1.5;">
              Noian Cristian · Bazat pe inteligență artificială</p>
          </td>
        </tr>
      </table>"""


def _cta_button(link: str, label: str) -> str:
    return f"""
            <table cellpadding="0" cellspacing="0">
              <tr>
                <td style="border-radius:8px;background:#c4622d;">
                  <a href="{link}"
                     style="display:inline-block;padding:14px 36px;font-size:15px;
                            font-weight:600;color:#ffffff;text-decoration:none;
                            border-radius:8px;letter-spacing:0.2px;">{label}</a>
                </td>
              </tr>
            </table>"""


def _build_onboarding_day1_html(app_url: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="ro">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Primul pas</title></head>
<body style="margin:0;padding:0;background:#080808;font-family:Inter,-apple-system,Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#080808;padding:48px 20px;">
    <tr><td align="center">
      {_header_html()}
        <tr>
          <td style="padding:36px 32px 28px;">
            <h1 style="margin:0 0 16px;font-size:22px;font-weight:600;color:#d4d0cb;
                        letter-spacing:-0.3px;line-height:1.3;">Contul e activ. Acum loghează prima masă.</h1>
            <p style="margin:0 0 14px;font-size:15px;color:#777;line-height:1.75;">
              Food logger-ul funcționează în română naturală. Scrii ce ai mâncat exact cum ai spune verbal.</p>
            <p style="margin:0 0 8px;font-size:13.5px;color:#555;line-height:1.7;">
              <strong style="color:#888;">Exemple:</strong></p>
            <ul style="margin:0 0 20px;padding-left:20px;font-size:13px;color:#4a4a4a;line-height:2;">
              <li>„2 ouă scrambled cu pâine integrală și unt"</li>
              <li>„shakira proteică cu lapte 1.5%, 30g pudră vanilie"</li>
              <li>„piept de pui la grătar 200g cu orez fiert 150g"</li>
            </ul>
            <p style="margin:0 0 28px;font-size:13.5px;color:#4a4a4a;line-height:1.7;">
              AI-ul parsează cantitățile, calculează macros și loghează totul automat.
              Fără tabele nutriționale, fără cântărit obligatoriu.</p>
            {_cta_button(app_url, "Deschide food logger")}
          </td>
        </tr>
      {_footer_html()}
    </td></tr>
  </table>
</body></html>"""


def _build_onboarding_day3_html(app_url: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="ro">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Check-in greutate</title></head>
<body style="margin:0;padding:0;background:#080808;font-family:Inter,-apple-system,Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#080808;padding:48px 20px;">
    <tr><td align="center">
      {_header_html()}
        <tr>
          <td style="padding:36px 32px 28px;">
            <h1 style="margin:0 0 16px;font-size:22px;font-weight:600;color:#d4d0cb;
                        letter-spacing:-0.3px;line-height:1.3;">3 zile. Cântarul te așteaptă.</h1>
            <p style="margin:0 0 16px;font-size:15px;color:#777;line-height:1.75;">
              Progresul real nu se vede zilnic — se vede în trend. Un check-in pe zi, 30 de secunde,
              și aplicația îți arată dacă ești pe drumul bun sau trebuie ajustat ceva.</p>
            <p style="margin:0 0 8px;font-size:13px;color:#555;line-height:1.7;">
              Graficul de greutate calculează automat:</p>
            <ul style="margin:0 0 20px;padding-left:20px;font-size:13px;color:#4a4a4a;line-height:2;">
              <li>Medie mobilă 7 zile — elimină zgomotul zilnic</li>
              <li>Rată de schimbare reală — nu ce crezi tu că se întâmplă</li>
              <li>Predicție — când ajungi la greutatea țintă la ritmul actual</li>
            </ul>
            <p style="margin:0 0 28px;font-size:13.5px;color:#4a4a4a;line-height:1.7;">
              Mergi în tabul <strong style="color:#888;">Progres</strong> și loghează greutatea de azi.</p>
            {_cta_button(app_url, "Fă primul check-in")}
          </td>
        </tr>
      {_footer_html()}
    </td></tr>
  </table>
</body></html>"""


def _build_onboarding_day7_html(app_url: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="ro">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>O săptămână</title></head>
<body style="margin:0;padding:0;background:#080808;font-family:Inter,-apple-system,Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#080808;padding:48px 20px;">
    <tr><td align="center">
      {_header_html()}
        <tr>
          <td style="padding:36px 32px 28px;">
            <h1 style="margin:0 0 16px;font-size:22px;font-weight:600;color:#d4d0cb;
                        letter-spacing:-0.3px;line-height:1.3;">O săptămână înăuntru.</h1>
            <p style="margin:0 0 16px;font-size:15px;color:#777;line-height:1.75;">
              Contul gratuit include food logging, grafic de greutate și calculator TDEE.
              Destul pentru a începe. Nu destul pentru a optimiza.</p>
            <p style="margin:0 0 8px;font-size:13px;color:#c4622d;font-weight:600;letter-spacing:0.5px;">
              CE DEBLOCHEZI CU PREMIUM — 7€/LUNĂ:</p>
            <ul style="margin:0 0 24px;padding-left:20px;font-size:13px;color:#4a4a4a;line-height:2.2;">
              <li><strong style="color:#666;">Logging vocal</strong> — dictezi în loc să scrii</li>
              <li><strong style="color:#666;">Barcode scanner</strong> — scanezi orice produs</li>
              <li><strong style="color:#666;">Plan de masă AI</strong> — generat pentru tine specific</li>
              <li><strong style="color:#666;">Motor adaptiv</strong> — ajustează targetul caloric real</li>
              <li><strong style="color:#666;">Raport PDF săptămânal</strong> — descarcabil, partajabil</li>
              <li><strong style="color:#666;">Analytics trend 4 săptămâni</strong> — pattern-uri clare</li>
            </ul>
            <p style="margin:0 0 28px;font-size:13px;color:#3a3a3a;line-height:1.6;">
              Poți anula oricând. Fără taxe ascunse.</p>
            {_cta_button(app_url, "✦  Activează Premium")}
          </td>
        </tr>
      {_footer_html()}
    </td></tr>
  </table>
</body></html>"""


# ─────────────────────────────────────────────────────────────────────────────
#  SEND FUNCTIONS — Onboarding
# ─────────────────────────────────────────────────────────────────────────────

_ONBOARDING_SUBJECTS = {
    1: "Primul pas: loghează ce ai mâncat azi · Noian Lab",
    3: "Ziua 3: cântarul te așteaptă · Noian Lab",
    7: "O săptămână. Iată ce urmează. · Noian Lab",
}

_ONBOARDING_BUILDERS = {
    1: _build_onboarding_day1_html,
    3: _build_onboarding_day3_html,
    7: _build_onboarding_day7_html,
}


async def send_onboarding_email(to_email: str, day: int) -> bool:
    """
    Trimite emailul de onboarding pentru ziua `day` (1, 3 sau 7).

    Args:
        to_email : adresa destinatarului
        day      : 1 | 3 | 7

    Returns:
        True dacă Resend a confirmat, False altfel.
    """
    if day not in _ONBOARDING_BUILDERS:
        print(f"⚠️  Onboarding: ziua {day} nu există.")
        return False

    if not RESEND_API_KEY:
        print(f"⚠️  RESEND_API_KEY lipsă — onboarding day {day} nesimulat pentru {to_email}.")
        return False

    html = _ONBOARDING_BUILDERS[day](APP_URL)
    ok   = await _send_email(
        to_email=to_email,
        subject=_ONBOARDING_SUBJECTS[day],
        html=html,
    )
    if ok:
        print(f"✉️  Onboarding day {day} trimis → {to_email}")
    else:
        print(f"⚠️  Onboarding day {day} FAILED → {to_email}")
    return ok
# ─────────────────────────────────────────────────────────────────────────────
#  REFERRAL REWARD EMAIL
# ─────────────────────────────────────────────────────────────────────────────

async def send_referral_reward_email(to_email: str, referred_email: str) -> bool:
    """
    Notifică referrer-ul că prietenul s-a alăturat și că a primit 30 zile Premium.
    """
    if not RESEND_API_KEY:
        return False

    masked  = referred_email[:2] + "***@" + referred_email.split("@")[-1]
    html    = f"""<!DOCTYPE html>
<html lang="ro">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Ai câștigat o lună Premium!</title></head>
<body style="margin:0;padding:0;background:#080808;font-family:Inter,-apple-system,Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#080808;padding:48px 20px;">
    <tr><td align="center">
      {_header_html()}
        <tr>
          <td style="padding:36px 32px 28px;">
            <h1 style="margin:0 0 16px;font-size:22px;font-weight:600;color:#d4d0cb;letter-spacing:-.3px;line-height:1.3;">
              Prietenul tău s-a alăturat. 🎉</h1>
            <p style="margin:0 0 16px;font-size:15px;color:#777;line-height:1.75;">
              <strong style="color:#c4622d;">{masked}</strong> și-a confirmat contul Noian Lab
              folosind invitația ta.</p>
            <div style="background:rgba(196,98,45,0.08);border:1px solid rgba(196,98,45,0.2);
                         border-radius:12px;padding:20px 24px;margin:0 0 24px;">
              <p style="margin:0;font-size:16px;font-weight:600;color:#d4d0cb;text-align:center;">
                ✦ +30 zile Premium adăugate contului tău</p>
            </div>
            <p style="margin:0 0 28px;font-size:13px;color:#4a4a4a;line-height:1.7;">
              Fiecare prieten care se alătură prin link-ul tău îți aduce o lună Premium gratis.
              Găsești link-ul tău de invitație în tabul <strong style="color:#666;">Setări</strong>.</p>
            {_cta_button(APP_URL, "Deschide Noian Lab")}
          </td>
        </tr>
      {_footer_html()}
    </td></tr>
  </table>
</body></html>"""

    return await _send_email(
        to_email=to_email,
        subject="Ai câștigat o lună Premium gratis! · Noian Lab",
        html=html,
    )