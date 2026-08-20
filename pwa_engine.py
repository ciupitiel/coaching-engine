import io
from functools import lru_cache

from PIL import Image, ImageDraw


# ─────────────────────────────────────────────────────────────────────────────
#  MANIFEST
# ─────────────────────────────────────────────────────────────────────────────

MANIFEST: dict = {
    "name":             "Noian Lab · Coaching Engine",
    "short_name":       "Coaching",
    "description":      "Motor nutrițional adaptiv · Bazat pe inteligență artificială",
    "start_url":        "/",
    "scope":            "/",
    "display":          "standalone",
    "background_color": "#080808",
    "theme_color":      "#080808",
    "orientation":      "portrait-primary",
    "lang":             "ro",
    "categories":       ["health", "fitness"],
    "icons": [
        {
            "src":     "/icon-192.png",
            "sizes":   "192x192",
            "type":    "image/png",
            "purpose": "any maskable",
        },
        {
            "src":     "/icon-512.png",
            "sizes":   "512x512",
            "type":    "image/png",
            "purpose": "any maskable",
        },
    ],
    "shortcuts": [
        {
            "name":        "Food Logger",
            "short_name":  "Loghează",
            "url":         "/?tab=nutritie",
            "description": "Loghează rapid ce ai mâncat",
        },
        {
            "name":        "Check-in Greutate",
            "short_name":  "Check-in",
            "url":         "/?tab=progres",
            "description": "Adaugă greutatea de azi",
        },
    ],
}


SW_CONTENT: str = r"""
// ============================================================
//  Service Worker — Noian Lab Coaching Engine v3.9
//  Strategie: Cache First pentru assets, Network Only pentru API
//  Push: Web Push API + VAPID · Morning Plan actions
// ============================================================

const CACHE_VER  = 'coaching-v4.7';
const CACHE_URLS = [
    '/',
    '/manifest.json',
    '/icon-192.png',
    'https://fonts.googleapis.com/css2?family=Inter:ital,wght@0,300;0,400;0,500;0,600;0,700;1,300;1,400&display=swap',
    'https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js',
];

// ── INSTALL: pre-cache assets statice ────────────────────────
self.addEventListener('install', event => {
    self.skipWaiting();
    event.waitUntil(
        caches.open(CACHE_VER).then(cache => {
            return cache.addAll(CACHE_URLS).catch(() => {});
        })
    );
});

// ── ACTIVATE: curăță cache-urile vechi ───────────────────────
self.addEventListener('activate', event => {
    event.waitUntil(
        caches.keys().then(keys =>
            Promise.all(
                keys.filter(k => k !== CACHE_VER).map(k => caches.delete(k))
            )
        ).then(() => self.clients.claim())
    );
});

// ── FETCH: strategia de caching ──────────────────────────────
self.addEventListener('fetch', event => {
    const url  = new URL(event.request.url);
    const path = url.pathname;

    // Network Only — API calls (date în timp real, niciodată cache)
    const API_PREFIXES = [
        '/auth', '/calculate', '/food', '/settings', '/chat',
        '/adaptive', '/meal-plan', '/checkin', '/profile',
        '/report', '/rag', '/p6', '/voice', '/push',
        '/streak', '/analytics', '/rate-limits', '/morning',
    ];
    if (API_PREFIXES.some(p => path.startsWith(p))) {
        return;
    }

    // Cache First — assets statice (HTML, fonturi, Chart.js)
    event.respondWith(
        caches.match(event.request).then(cached => {
            if (cached) return cached;

            return fetch(event.request).then(response => {
                if (
                    response &&
                    response.status === 200 &&
                    response.type !== 'opaque'
                ) {
                    const clone = response.clone();
                    caches.open(CACHE_VER).then(cache => {
                        cache.put(event.request, clone);
                    });
                }
                return response;
            }).catch(() => {
                if (event.request.destination === 'document') {
                    return caches.match('/');
                }
            });
        })
    );
});

// ── PUSH: primire notificare de la server ─────────────────────
//  payload JSON: { title, body, tag, url, token?, actions?, requireInteraction? }
self.addEventListener('push', event => {
    let data = {};
    try {
        data = event.data ? event.data.json() : {};
    } catch (e) {
        data = { title: 'Coaching Engine', body: event.data ? event.data.text() : '' };
    }

    const title   = data.title || 'Coaching Engine';
    const options = {
        body:               data.body || 'Notificare nouă',
        icon:               '/icon-192.png',
        badge:              '/icon-192.png',
        tag:                data.tag || 'coaching-notif',
        renotify:           false,
        requireInteraction: data.requireInteraction || false,
        silent:             false,
        data: {
            url:   data.url   || '/',
            token: data.token || null,   // UUID din morning_plans — auth pentru /morning/confirm
        },
    };

    // Butoane acțiune (Morning Plan: "✓ Loghez tot" / "✗ Modific")
    if (Array.isArray(data.actions) && data.actions.length > 0) {
        options.actions = data.actions;
    }

    event.waitUntil(
        self.registration.showNotification(title, options)
    );
});

// ── HELPER: focusează tab existent sau deschide unul nou ──────
function _openOrFocus(url) {
    return clients.matchAll({ type: 'window', includeUncontrolled: true }).then(list => {
        for (const client of list) {
            if ('focus' in client && client.url.includes(self.location.origin)) {
                return client.navigate(url).then(c => c.focus());
            }
        }
        if (clients.openWindow) return clients.openWindow(url);
    });
}

// ── NOTIFICATIONCLICK ─────────────────────────────────────────
self.addEventListener('notificationclick', event => {
    event.notification.close();

    const notifData = event.notification.data || {};
    const token     = notifData.token || null;
    const baseUrl   = notifData.url   || '/';

    // ── Acțiune: "✓ Loghez tot" — bulk insert food_logs via token ────────────
    if (event.action === 'confirm-log' && token) {
        event.waitUntil(
            fetch('/morning/confirm', {
                method:  'POST',
                headers: { 'Content-Type': 'application/json' },
                body:    JSON.stringify({ token }),
            })
            .then(res => res.json())
            .then(result => {
                if (result.ok && !result.idempotent) {
                    // Notifică tab-urile deschise să reîncarce logurile
                    return clients.matchAll({ type: 'window', includeUncontrolled: true })
                        .then(list => {
                            list.forEach(c => c.postMessage({
                                type:         'MORNING_CONFIRMED',
                                logs_created: result.logs_created,
                                total_kcal:   result.total_kcal,
                            }));
                        });
                }
            })
            .catch(() => {
                // Rețea down sau server error — userul vede notificarea închisă,
                // poate deschide app-ul și confirma manual din GET /morning/today
            })
        );
        return;
    }

    // ── Acțiune: "✗ Modific" — deschide tab Nutriție cu planul preîncărcat ──
    if (event.action === 'modify') {
        const url = token
            ? `/?tab=nutritie&morning_token=${token}`
            : '/?tab=nutritie';
        event.waitUntil(_openOrFocus(url));
        return;
    }

    // ── Default: click pe corpul notificării ──────────────────────────────────
    event.waitUntil(_openOrFocus(baseUrl));
});
""".strip()


@lru_cache(maxsize=8)
def generate_app_icon(size: int) -> bytes:
    """
    Generează iconița app-ului ca PNG, cache-uită cu lru_cache.

    Design concentric:
      ① Background pătrat #080808 (--bg)
      ② Cerc exterior #c4622d (--accent) cu margine 12%
      ③ Cerc interior #080808 cu margine 28% (creează un inel accent)
      ④ Punct central #c4622d cu margine 44%

    Antialiasing: desenăm la 4× și facem downscale pentru muchii netede.
    """
    render_size = size * 4
    img  = Image.new("RGB", (render_size, render_size), "#080808")
    draw = ImageDraw.Draw(img)

    m1 = int(render_size * 0.10)
    draw.ellipse(
        [m1, m1, render_size - m1, render_size - m1],
        fill="#c4622d",
    )

    m2 = int(render_size * 0.26)
    draw.ellipse(
        [m2, m2, render_size - m2, render_size - m2],
        fill="#080808",
    )

    m3 = int(render_size * 0.42)
    draw.ellipse(
        [m3, m3, render_size - m3, render_size - m3],
        fill="#c4622d",
    )

    img = img.resize((size, size), Image.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()