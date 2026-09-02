// Progressive enhancement only: every page works with this file blocked.
(function () {
  'use strict';

  var root = document.documentElement;

  // ---- theme -------------------------------------------------------------
  // Three states, cycled in this order. "system" is the default and is
  // represented by the absence of a data-theme attribute, so the CSS
  // prefers-color-scheme rules take over.
  var ORDER = ['system', 'light', 'dark'];
  var LABELS = {
    system: 'Colour theme: follow system',
    light: 'Colour theme: light',
    dark: 'Colour theme: dark'
  };

  function read() {
    try {
      var v = localStorage.getItem('theme');
      return (v === 'light' || v === 'dark') ? v : 'system';
    } catch (e) { return 'system'; }
  }

  function resolvedDark(mode) {
    if (mode === 'dark') return true;
    if (mode === 'light') return false;
    return !!(window.matchMedia &&
              window.matchMedia('(prefers-color-scheme: dark)').matches);
  }

  function apply(mode) {
    if (mode === 'system') root.removeAttribute('data-theme');
    else root.setAttribute('data-theme', mode);

    var meta = document.getElementById('theme-color');
    if (meta) meta.setAttribute('content', resolvedDark(mode) ? '#14170f' : '#fbfaf7');

    var btn = document.getElementById('theme-toggle');
    if (btn) {
      btn.setAttribute('aria-label', LABELS[mode]);
      btn.setAttribute('title', LABELS[mode]);
    }
  }

  var mode = read();
  apply(mode);

  var toggle = document.getElementById('theme-toggle');
  if (toggle) {
    toggle.addEventListener('click', function () {
      mode = ORDER[(ORDER.indexOf(mode) + 1) % ORDER.length];
      try {
        if (mode === 'system') localStorage.removeItem('theme');
        else localStorage.setItem('theme', mode);
      } catch (e) { /* private mode: the choice just will not persist */ }
      apply(mode);
    });
  }

  // Track OS changes while the reader is on "system".
  if (window.matchMedia) {
    var mq = window.matchMedia('(prefers-color-scheme: dark)');
    var onChange = function () { if (mode === 'system') apply('system'); };
    if (mq.addEventListener) mq.addEventListener('change', onChange);
    else if (mq.addListener) mq.addListener(onChange);
  }

  // ---- mobile navigation -------------------------------------------------
  var navToggle = document.querySelector('.nav-toggle');
  var nav = document.getElementById('nav');
  if (navToggle && nav) {
    navToggle.addEventListener('click', function () {
      var open = nav.classList.toggle('open');
      navToggle.setAttribute('aria-expanded', open ? 'true' : 'false');
    });
    // Close on Escape, and when focus leaves the header entirely.
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && nav.classList.contains('open')) {
        nav.classList.remove('open');
        navToggle.setAttribute('aria-expanded', 'false');
        navToggle.focus();
      }
    });
  }

  // ---- cookie consent ----------------------------------------------------
  // Ads are gated server-side on this cookie, so reload once after accepting
  // to let the server decide what to emit.
  var banner = document.getElementById('consent');
  if (banner) {
    banner.addEventListener('click', function (e) {
      var choice = e.target && e.target.dataset && e.target.dataset.consent;
      if (!choice) return;
      var year = 365 * 24 * 60 * 60;
      var secure = location.protocol === 'https:' ? '; Secure' : '';
      document.cookie = 'cookie_consent=' + choice + '; Max-Age=' + year +
                        '; Path=/; SameSite=Lax' + secure;
      banner.remove();
      if (choice === 'accepted') location.reload();
    });
  }
})();
