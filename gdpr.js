(function () {
  'use strict';

  var KEY = 'gdpr_v1';

  // ── Verificare rapidă — zero overhead dacă a acceptat deja ──────────────────
  try {
    if (localStorage.getItem(KEY)) return;
  } catch (e) {
    return; // localStorage blocat (modul incognito strict) → nu afișăm nimic
  }

  // ── CSS injectat în <head> ──────────────────────────────────────────────────
  var css = document.createElement('style');
  css.textContent = [
    // Container fix bottom — invizibil și în afara viewport-ului până la trigger
    '#gdpr{',
      'position:fixed;bottom:0;left:0;right:0;z-index:99998;',
      'padding:0 12px 12px;',
      'pointer-events:none;',
      'transform:translateY(110%);',
      'transition:transform .42s cubic-bezier(.4,0,.2,1);',
    '}',

    // Vizibil — slide-up
    '#gdpr.gdpr-on{',
      'transform:translateY(0);',
      'pointer-events:auto;',
    '}',

    // Card interior
    '#gdpr-card{',
      'background:#161616;',
      'border:1px solid rgba(255,255,255,0.07);',
      'border-top:1px solid rgba(196,98,45,0.25);',
      'border-radius:14px;',
      'padding:18px 20px 18px 22px;',
      'max-width:720px;',
      'margin:0 auto;',
      'box-shadow:0 -8px 48px rgba(0,0,0,0.55),0 0 0 1px rgba(196,98,45,0.08);',
      'display:flex;align-items:center;gap:20px;',
      'backdrop-filter:blur(16px);-webkit-backdrop-filter:blur(16px);',
    '}',

    // Indicator accent stânga
    '#gdpr-card::before{',
      'content:"";flex-shrink:0;',
      'width:3px;height:36px;border-radius:99px;',
      'background:linear-gradient(180deg,#c4622d,rgba(196,98,45,0.3));',
    '}',

    // Zona text
    '#gdpr-body{flex:1;min-width:0;}',

    '#gdpr-label{',
      'font:700 10px/1 Inter,system-ui,sans-serif;',
      'letter-spacing:2px;text-transform:uppercase;',
      'color:#c4622d;margin:0 0 7px;',
    '}',

    '#gdpr-text{',
      'font:400 13px/1.65 Inter,system-ui,sans-serif;',
      'color:#7a7672;margin:0;',
    '}',

    '#gdpr-text a{',
      'color:#c4622d;text-decoration:none;border-bottom:1px solid rgba(196,98,45,0.3);',
      'transition:border-color .15s;',
    '}',
    '#gdpr-text a:hover{border-bottom-color:#c4622d;}',

    // Butoane
    '#gdpr-actions{display:flex;flex-direction:column;gap:8px;flex-shrink:0;align-items:center;}',

    '#gdpr-btn{',
      'background:#c4622d;color:#fff;border:none;border-radius:8px;',
      'padding:10px 24px;white-space:nowrap;',
      'font:600 13px/1 Inter,system-ui,sans-serif;',
      'letter-spacing:.02em;cursor:pointer;',
      'transition:opacity .15s,transform .15s;',
    '}',
    '#gdpr-btn:hover{opacity:.88;transform:translateY(-1px);}',
    '#gdpr-btn:active{opacity:1;transform:translateY(0);}',

    '#gdpr-link{',
      'font:400 11.5px/1 Inter,system-ui,sans-serif;',
      'color:#3e3e3e;text-decoration:none;',
      'transition:color .15s;white-space:nowrap;',
    '}',
    '#gdpr-link:hover{color:#7a7672;}',

    // Mobile: stacked layout
    '@media(max-width:600px){',
      '#gdpr-card{flex-wrap:wrap;gap:14px;}',
      '#gdpr-card::before{display:none;}',
      '#gdpr-actions{flex-direction:row;width:100%;justify-content:flex-end;}',
    '}',
  ].join('');
  document.head.appendChild(css);

  // ── HTML Banner ─────────────────────────────────────────────────────────────
  var banner = document.createElement('div');
  banner.id = 'gdpr';
  banner.setAttribute('role', 'dialog');
  banner.setAttribute('aria-modal', 'false');
  banner.setAttribute('aria-label', 'Notificare privind confidențialitatea datelor');
  banner.innerHTML = [
    '<div id="gdpr-card">',
      '<div id="gdpr-body">',
        '<p id="gdpr-label">Confidențialitate &amp; Date</p>',
        '<p id="gdpr-text">',
          'Această aplicație salvează local tokenul de autentificare și preferințele UI ',
          'strict pentru funcționarea ei. ',
          '<strong style="color:#a09890;font-weight:600">',
            'Zero cookies de tracking, zero publicitate, zero vânzare de date.',
          '</strong>',
          ' <a href="/privacy" target="_blank" rel="noopener">',
            'Politică de confidențialitate →',
          '</a>',
        '</p>',
      '</div>',
      '<div id="gdpr-actions">',
        '<button id="gdpr-btn" type="button">Am înțeles</button>',
        '<a href="/privacy" target="_blank" rel="noopener" id="gdpr-link">Citește detalii</a>',
      '</div>',
    '</div>',
  ].join('');

  document.body.appendChild(banner);

  // ── Slide-up după 600ms — pagina se încarcă complet, nu blocăm nimic ────────
  var timer = setTimeout(function () {
    banner.classList.add('gdpr-on');
  }, 600);

  // ── Dismiss — salvează consimțământul cu timestamp ──────────────────────────
  function dismiss() {
    clearTimeout(timer);

    // Salvăm în localStorage cu metadate complete
    try {
      localStorage.setItem(KEY, JSON.stringify({
        accepted:   true,
        timestamp:  new Date().toISOString(),
        version:    '1.0',
        ua_hash:    (navigator.userAgent || '').slice(0, 40),
      }));
    } catch (e) { /* localStorage blocat — continuăm oricum */ }

    // Slide-down + remove din DOM
    banner.style.transition = 'transform .32s cubic-bezier(.4,0,.2,1), opacity .32s';
    banner.classList.remove('gdpr-on');
    banner.style.opacity = '0';
    setTimeout(function () {
      try { banner.parentNode && banner.parentNode.removeChild(banner); } catch (e) {}
    }, 360);
  }

  document.getElementById('gdpr-btn').addEventListener('click', dismiss);

  // Dismiss și la apăsare Escape (accesibilitate)
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && banner.classList.contains('gdpr-on')) dismiss();
  }, { once: true });

})();