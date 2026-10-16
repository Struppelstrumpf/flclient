/* FlyConomy In-Game Panel – GSX-style command surface + local bridge */

/* Pure rules of panel 1.4.31 (D11/D12) – no DOM, no network; tested with node. */
var FlyConomyPanelLogic = (function () {
  // Never sent twice via the second bridge URL: a timeout does not prove that the
  // first request did not arrive (start, cancel, money, log).
  var NO_FALLBACK = ['/start', '/cancel', '/refuel', '/end'];
  var CANCEL_ARM_MS = 4000;

  function pathOnly(path) {
    var p = String(path || '');
    var q = p.indexOf('?');
    return q >= 0 ? p.slice(0, q) : p;
  }

  // Second URL only when the first one was not reachable at all (status 0,
  // no timeout) and never for start/cancel/refuel/end.
  function shouldTryFallback(path, err) {
    if (!err || err.status !== 0 || err.timeout) return false;
    return NO_FALLBACK.indexOf(pathOnly(path)) < 0;
  }

  // Two taps within 4 s cancel the flight; the first tap only arms.
  function cancelTap(armedUntil, now) {
    if (armedUntil && now <= armedUntil) return { fire: true, armedUntil: 0 };
    return { fire: false, armedUntil: now + CANCEL_ARM_MS };
  }

  // Number keys never cancel, push back or load the EFB.
  function keyAllowed(info) {
    info = info || {};
    if (info.id === 'btnCancel' || info.id === 'btnLoadEfb') return false;
    if (info.gsx === 'pushback') return false;
    return true;
  }

  function inflight(snap) {
    return !!(snap && snap.active_flight_id);
  }

  // The server has a flight the client does not record (lost /start answer, no
  // session on this PC): logging cannot work, only "Abbrechen" helps (review S8).
  function unrecorded(snap) {
    return !!(snap && snap.active_flight_id && snap.flight_unrecorded);
  }

  function efbLocked(snap) {
    if (!inflight(snap)) return false;
    var s = (snap && snap.last_sample) || {};
    var gs = Number(s.ground_speed) || 0;
    return s.on_ground === false || gs > 6;
  }

  function flightButton(snap) {
    if (snap && snap.pending_start && !inflight(snap)) {
      return { text: 'Start wartet – NICHT losfliegen', cls: 'cta amber', disabled: true };
    }
    if (unrecorded(snap)) return { text: 'Ohne Aufzeichnung – bitte abbrechen', cls: 'cta amber', disabled: true };
    if (inflight(snap)) return { text: 'Flug loggen', cls: 'cta green', disabled: false };
    return { text: 'Flug starten', cls: 'cta red', disabled: false };
  }

  function usd(n, signed) {
    var v = Math.round(Number(n) || 0);
    var a = String(Math.abs(v)).replace(/\B(?=(\d{3})+(?!\d))/g, '.');
    if (signed) return (v >= 0 ? '+' : '−') + '$' + a;
    return (v < 0 ? '−' : '') + '$' + a;
  }

  function lastResultText(res, nowSec) {
    if (!res || !res.at) return '';
    if (nowSec && nowSec - Number(res.at) > 900) return '';
    var parts = ['Letzter Flug ' + (res.from || '?') + ' → ' + (res.to || '?')];
    if (res.net != null) parts.push('netto ' + usd(res.net, true));
    else if (res.paid != null) parts.push('ausgezahlt ' + usd(res.paid));
    var und = res.undelivered || [];
    if (und.length) {
      var first = und[0] || {};
      parts.push(
        und.length +
          ' nicht geliefert' +
          (first.reason ? ' (' + String(first.reason).slice(0, 60) + ')' : '')
      );
    }
    return parts.join(' · ');
  }

  function stickyText(snap, nowSec) {
    if (!snap) return '';
    if (snap.pending_start && !inflight(snap)) {
      return snap.pending_start_text || 'Start wartet – NICHT losfliegen';
    }
    if (unrecorded(snap)) {
      return (
        snap.flight_unrecorded_text ||
        'Flug läuft auf dem Server ohne Aufzeichnung – nur „Abbrechen“ hilft, dann neu starten.'
      );
    }
    if (inflight(snap) && snap.title_mismatch) {
      return 'Anderes Flugzeug geladen – Aufzeichnung pausiert, Flug NICHT abgebrochen.';
    }
    if (inflight(snap) && snap.position_mismatch) {
      return snap.mismatch_kind === 'gap'
        ? 'Flugzeug steht nicht am letzten Punkt – Flug NICHT abgebrochen. Unten wählen.'
        : 'Positionssprung gemeldet – Aufzeichnung kurz pausiert, der Server prüft.';
    }
    if (!inflight(snap)) return lastResultText(snap.last_result, nowSec);
    return '';
  }

  return {
    NO_FALLBACK: NO_FALLBACK,
    CANCEL_ARM_MS: CANCEL_ARM_MS,
    shouldTryFallback: shouldTryFallback,
    cancelTap: cancelTap,
    keyAllowed: keyAllowed,
    efbLocked: efbLocked,
    unrecorded: unrecorded,
    flightButton: flightButton,
    lastResultText: lastResultText,
    stickyText: stickyText,
    usd: usd,
  };
})();

var FlyConomyApp = (function () {
  const L = FlyConomyPanelLogic;
  const BRIDGE_URLS = ['http://127.0.0.1:17474', 'http://localhost:17474'];
  const PANEL_VERSION = '1.4.31';
  const DESIGN_W = 400;
  const PHONE_H = 820;
  const CHROME_H = 48;
  const DESIGN_H = PHONE_H + CHROME_H;
  // Minimum padding; fitStage adds proportional extras on large panels.
  const PAD_L = 8;
  const PAD_R = 20;
  const PAD_B = 48;
  // MSFS fits our whole box into the panel window, so the phone's visible size
  // is its share of that box: 1 = box hugs the phone (phone fills the window).
  const MIN_SCALE = 0.45;
  const MAX_SCALE = 1.0;
  const GRIP = 30;
  const SCALE_KEY = 'flyconomy_panel_scale3';
  const AUTO_LAYOUT_KEY = 'flyconomy_panel_autolayout_v1';
  const PHONE_MODE = /\/phone(?:\/|$)/.test(String((window.location || {}).pathname || ''));
  const UNITS = ['gal', 'L', 'kg', 'lb', 't'];
  const LPG = 3.785411784;
  const LBKG = 2.2046226218;
  const $ = (id) => document.getElementById(id);

  let bridgeBase = BRIDGE_URLS[0];
  let started = false;
  let lastAtc = '';
  let atcTriedAt = 0;
  let webLoginBusy = false;
  let payloadAppliedAt = 0;
  let simTriedAt = 0;
  let view = 'home';
  let snapCache = null;
  let bridgeFailStreak = 0;
  let lastBridgeOkAt = 0;
  let fuelDesk = null;
  let fuelUnit = 'gal';
  let fuelTarget = 0;
  let fuelMode = 'auto';
  let fuelPace = 'slow';
  let fuelBusy = false;
  let lastPumpActive = false;
  let flightBusy = false;
  let amountFocus = false;
  let uiScale = 1;
  let keysListenUntil = 0;
  let keysSawListen = false;
  let keysPollTimer = 0;
  let keysLabelAtBind = '';
  let namePanelRuns = 0;
  let fitRaf = 0;
  let lastStableScale = 1;
  let autoFitAttempts = 0;
  let autoFitDone = false;
  let cancelArmedUntil = 0;

  function log(msg) {
    const el = $('log');
    if (!el) return;
    el.textContent = '[' + new Date().toLocaleTimeString() + '] ' + msg + '\n' + el.textContent;
  }

  function jetA(desk) {
    return String((desk && desk.aircraft && desk.aircraft.fuel_type) || 'jeta') === 'jeta';
  }
  function kgPer(desk) {
    // Same density as the website and the server (fuelUnits.ts, fboOperationsService:
    // Jet A 3.08 kg/gal, 100LL 2.72 kg/gal). Changed only together with them (review S8).
    return jetA(desk) ? 3.08 : 2.72;
  }
  function toUnit(gal, unit, desk) {
    const g = Number(gal) || 0;
    const kg = g * kgPer(desk);
    if (unit === 'L') return g * LPG;
    if (unit === 'kg') return kg;
    if (unit === 'lb') return kg * LBKG;
    if (unit === 't') return kg / 1000;
    return g;
  }
  function fromUnit(val, unit, desk) {
    const n = Number(val) || 0;
    const kg = kgPer(desk);
    if (unit === 'L') return n / LPG;
    if (unit === 'kg') return n / kg;
    if (unit === 'lb') return n / LBKG / kg;
    if (unit === 't') return (n * 1000) / kg;
    return n;
  }
  function fmtN(v, unit) {
    var n = Number(v) || 0;
    var d = unit === 't' ? 2 : unit === 'gal' ? 1 : 0;
    return n.toFixed(d);
  }
  function pad2(n) {
    n = String(Math.floor(Number(n) || 0));
    return n.length < 2 ? '0' + n : n;
  }
  function fmtHms(sec) {
    sec = Math.max(0, Math.floor(Number(sec) || 0));
    return pad2(sec / 3600) + ':' + pad2((sec % 3600) / 60) + ':' + pad2(sec % 60);
  }

  function each(list, fn) {
    if (!list) return;
    var i;
    for (i = 0; i < list.length; i++) fn(list[i], i);
  }

  function setHidden(el, hide) {
    if (!el) return;
    if (hide) {
      el.classList.add('hidden');
      el.style.display = 'none';
    } else {
      el.classList.remove('hidden');
      el.style.display = '';
    }
  }

  function errText(e) {
    var m = (e && (e.message || e.description)) || String(e || '');
    if (!m || m === 'Type error' || m === 'TypeError' || String(m).toLowerCase() === 'type error') {
      return 'Keine Verbindung zur Bridge. MiniClient offen lassen.';
    }
    return m;
  }

  function hideSimHeader() {
    var ui = document.querySelector('ingame-ui');
    var roots = [document];
    if (ui) {
      ui.classList.remove('no-header');
      ui.classList.remove('no-resize');
      ui.setAttribute('title', 'FlyConomy');
      roots.push(ui);
      if (ui.shadowRoot) roots.push(ui.shadowRoot);
    }
    var r, j, list, el;
    for (r = 0; r < roots.length; r++) {
      if (!roots[r] || !roots[r].querySelectorAll) continue;
      list = roots[r].querySelectorAll('.ingameUiFooter, .footer, .border');
      for (j = 0; j < list.length; j++) {
        el = list[j];
        if (el.closest && el.closest('#stageOuter')) continue;
        el.style.cssText = 'display:none!important;pointer-events:none!important;height:0!important;opacity:0!important;';
      }
    }
    hideSimChrome(ui);
    pinNativeHeaderToMove();
  }

  // MSFS injects its own frame (title bar, borders) as extra children of
  // <ingame-ui>. Anything besides our stage and the drag header shows up as a
  // grey bar above the phone.
  function hideSimChrome(ui) {
    if (!ui || !ui.children) return;
    var stage = $('stageOuter');
    var header = findDragHeader(ui);
    var i, el, h;
    for (i = 0; i < ui.children.length; i++) {
      el = ui.children[i];
      if (!el || !el.style || el === stage) continue;
      if (stage && el.contains && el.contains(stage)) continue;
      if (header && (el === header || (el.contains && el.contains(header)))) continue;
      // Never hide a bar-shaped element while no header was identified – it is
      // the sim's title bar and dragging the panel depends on it.
      h = el.offsetHeight || 0;
      if (!header && h > 0 && h < 80) {
        el.style.cssText =
          'opacity:0!important;background:transparent!important;border:none!important;' +
          'pointer-events:auto!important;cursor:grab!important;';
        continue;
      }
      el.style.cssText =
        'display:none!important;opacity:0!important;height:0!important;pointer-events:none!important;';
    }
  }

  // The sim starts its window drag from its own title bar, so a press on our
  // move button is replayed on that bar at the same screen position.
  function forwardToNativeHeader(e) {
    var header = findDragHeader(document.querySelector('ingame-ui'));
    if (!header || !header.dispatchEvent) return;
    var x = e.clientX || 0;
    var y = e.clientY || 0;
    var kinds = ['pointerdown', 'mousedown'];
    var i;
    for (i = 0; i < kinds.length; i++) {
      try {
        var init = {
          bubbles: true,
          cancelable: true,
          clientX: x,
          clientY: y,
          screenX: e.screenX || x,
          screenY: e.screenY || y,
          button: 0,
          buttons: 1,
        };
        var ev;
        if (kinds[i] === 'pointerdown' && window.PointerEvent) {
          init.pointerId = e.pointerId != null ? e.pointerId : 1;
          init.isPrimary = true;
          ev = new PointerEvent('pointerdown', init);
        } else {
          ev = new MouseEvent(kinds[i], init);
        }
        header.dispatchEvent(ev);
      } catch (e0) {}
    }
  }

  function pinNativeHeaderToMove() {
    var handle = $('btnMove');
    var ui = document.querySelector('ingame-ui');
    var header = findDragHeader(ui);
    if (!handle || !header) return;
    if (ui && ui.style) ui.style.position = 'relative';
    var origin = ui || document.body;
    var or = origin.getBoundingClientRect();
    var hr = handle.getBoundingClientRect();
    var left = hr.left - or.left;
    var top = hr.top - or.top;
    header.style.cssText =
      'display:block!important;position:absolute!important;left:' +
      left +
      'px!important;top:' +
      top +
      'px!important;width:' +
      hr.width +
      'px!important;height:' +
      hr.height +
      'px!important;right:auto!important;bottom:auto!important;margin:0!important;padding:0!important;' +
      'overflow:hidden!important;opacity:0!important;background:transparent!important;border:none!important;' +
      'z-index:50!important;pointer-events:auto!important;cursor:grab!important;';
    var kids = header.querySelectorAll('*');
    var k;
    for (k = 0; k < kids.length; k++) {
      kids[k].style.pointerEvents = 'none';
      kids[k].style.opacity = '0';
    }
  }

  function clampScale(v) {
    var n = Number(v);
    if (!isFinite(n) || n <= 0) n = 1;
    return Math.max(MIN_SCALE, Math.min(MAX_SCALE, n));
  }

  function panelViewport() {
    var w = window.innerWidth || 0;
    var h = window.innerHeight || 0;
    if (!PHONE_MODE) {
      try {
        var ui = document.querySelector('ingame-ui');
        if (ui && ui.getBoundingClientRect) {
          var rect = ui.getBoundingClientRect();
          if (rect.width > 48) w = rect.width;
          if (rect.height > 48) h = rect.height;
        }
      } catch (eV) {}
    }
    if (w < 48) w = DESIGN_W + PAD_L + PAD_R;
    if (h < 48) h = DESIGN_H + PAD_B;
    return { w: w, h: h };
  }

  function contentMetrics(chromeH) {
    var ch = chromeH || CHROME_H;
    var padL = Math.max(PAD_L, Math.round(DESIGN_W * 0.02));
    var padR = Math.max(PAD_R, Math.round(DESIGN_W * 0.045));
    var padB = Math.max(PAD_B, Math.round(PHONE_H * 0.055));
    return {
      chromeH: ch,
      padL: padL,
      padR: padR,
      padB: padB,
      contentW: DESIGN_W + padL + padR,
      contentH: ch + PHONE_H + padB,
    };
  }

  /** Fit to MSFS panel window; stable rounding avoids edge flicker. */
  function effectiveScale(metrics) {
    var scale = clampScale(uiScale);
    var vp = panelViewport();
    var margin = 14;
    var widthFit = (vp.w - margin) / metrics.contentW;
    var heightFit = (vp.h - margin) / metrics.contentH;
    var next = clampScale(Math.min(scale, widthFit, heightFit, MAX_SCALE));
    next = Math.round(next * 400) / 400;
    if (Math.abs(next - lastStableScale) < 0.004) return lastStableScale;
    lastStableScale = next;
    return next;
  }

  function clearBox(el) {
    if (!el || !el.style) return;
    el.style.width = '';
    el.style.height = '';
    el.style.minWidth = '';
    el.style.minHeight = '';
    el.style.maxWidth = '';
    el.style.maxHeight = '';
  }

  function scheduleFitStage() {
    if (fitRaf) return;
    fitRaf = requestAnimationFrame(function () {
      fitRaf = 0;
      fitStage(true);
    });
  }

  function loadScale() {
    try {
      var raw = window.localStorage.getItem(SCALE_KEY);
      if (raw) return clampScale(parseFloat(raw));
    } catch (e) {}
    return clampScale(1);
  }

  function saveScale() {
    try {
      window.localStorage.setItem(SCALE_KEY, String(uiScale));
    } catch (e) {}
  }

  function loadAutoLayout() {
    try {
      var raw = window.localStorage.getItem(AUTO_LAYOUT_KEY);
      if (raw) return JSON.parse(raw);
    } catch (e) {}
    return null;
  }

  function saveAutoLayout(data) {
    try {
      window.localStorage.setItem(AUTO_LAYOUT_KEY, JSON.stringify(data || {}));
    } catch (e) {}
  }

  function needsAutoLayout() {
    if (autoFitDone) return false;
    var saved = loadAutoLayout();
    if (saved && saved.done) return false;
    try {
      if (window.localStorage.getItem(SCALE_KEY)) return false;
    } catch (e) {}
    return true;
  }

  function measureStageClip() {
    var vp = panelViewport();
    var outer = $('stageOuter');
    var ui = document.querySelector('ingame-ui');
    if (!outer || !ui || !ui.getBoundingClientRect) return { clipped: false, vp: vp };
    var r = outer.getBoundingClientRect();
    var ur = ui.getBoundingClientRect();
    var m = 6;
    var overflowRight = r.right - (ur.right - m);
    var overflowBottom = r.bottom - (ur.bottom - m);
    var overflowLeft = ur.left + m - r.left;
    var clipped = overflowRight > 1 || overflowBottom > 1 || overflowLeft > 1;
    return {
      clipped: clipped,
      vp: vp,
      overflowRight: overflowRight,
      overflowBottom: overflowBottom,
      overflowLeft: overflowLeft,
    };
  }

  function computeFitScale(metrics) {
    var vp = panelViewport();
    if (vp.w < 80 || vp.h < 80) return null;
    var margin = 16;
    var widthFit = (vp.w - margin) / metrics.contentW;
    var heightFit = (vp.h - margin) / metrics.contentH;
    return clampScale(Math.min(widthFit, heightFit, MAX_SCALE));
  }

  function autoFitPanelLayout() {
    var chromeH = CHROME_H;
    var chrome = $('phoneChrome');
    try {
      if (chrome && chrome.offsetHeight) chromeH = chrome.offsetHeight;
    } catch (eC) {}
    var metrics = contentMetrics(chromeH);
    var next = computeFitScale(metrics);
    if (next == null) return false;
    next = Math.round(next * 400) / 400;
    if (Math.abs(next - uiScale) > 0.003) {
      uiScale = next;
      saveScale();
      return true;
    }
    return false;
  }

  function centerStageInPanel(metrics, scale) {
    var outer = $('stageOuter');
    if (!outer || !outer.style) return;
    var vp = panelViewport();
    var scaledW = metrics.contentW * (scale < 0.998 ? scale : 1);
    var left = Math.max(0, Math.floor((vp.w - scaledW) / 2));
    outer.style.marginLeft = left + 'px';
  }

  function finishAutoLayout() {
    autoFitDone = true;
    var clip = measureStageClip();
    saveAutoLayout({
      done: true,
      scale: uiScale,
      vpW: Math.round(clip.vp.w),
      vpH: Math.round(clip.vp.h),
      centered: true,
      version: PANEL_VERSION,
      at: Date.now(),
    });
    log('Handy-Layout automatisch angepasst (' + Math.round(uiScale * 100) + '%)');
  }

  function scheduleAutoFitFirstOpen() {
    if (!needsAutoLayout()) return;
    function tick() {
      if (!needsAutoLayout()) return;
      autoFitAttempts++;
      autoFitPanelLayout();
      fitStage(true);
      var clip = measureStageClip();
      if (clip.clipped && uiScale > MIN_SCALE + 0.02) {
        uiScale = clampScale(uiScale * 0.94);
        saveScale();
        fitStage(true);
        clip = measureStageClip();
      }
      if (!clip.clipped || autoFitAttempts >= 14 || (clip.vp.w >= 120 && clip.vp.h >= 120 && autoFitAttempts >= 4)) {
        finishAutoLayout();
        return;
      }
      setTimeout(tick, autoFitAttempts < 5 ? 60 : 180);
    }
    requestAnimationFrame(function () {
      requestAnimationFrame(tick);
    });
  }

  function onPanelActive() {
    if (!needsAutoLayout()) return;
    scheduleAutoFitFirstOpen();
  }

  function applyBox(el, width, height) {
    if (!el || !el.style) return;
    el.style.width = width + 'px';
    el.style.height = height + 'px';
    el.style.minWidth = width + 'px';
    el.style.minHeight = height + 'px';
    el.style.maxWidth = width + 'px';
    el.style.maxHeight = height + 'px';
  }

  var lastBox = '';

  function fitStage(force) {
    var outer = $('stageOuter');
    var scaleEl = $('stageScale');
    var wrap = $('stageWrap');
    var stage = $('stage');
    var grip = $('btnResize');
    var chrome = $('phoneChrome');
    var ui = document.querySelector('ingame-ui');
    uiScale = clampScale(uiScale);
    var chromeH = CHROME_H;
    try {
      if (chrome && chrome.offsetHeight) chromeH = chrome.offsetHeight;
    } catch (eC) {}
    var metrics = contentMetrics(chromeH);
    var scale = effectiveScale(metrics);
    var key = metrics.contentW + 'x' + metrics.contentH + '@' + scale.toFixed(3);
    if (!force && key === lastBox) return;
    lastBox = key;
    if (wrap) {
      applyBox(wrap, DESIGN_W, PHONE_H);
      wrap.style.transform = 'none';
    }
    if (stage) applyBox(stage, DESIGN_W, PHONE_H);
    if (scaleEl) {
      applyBox(scaleEl, DESIGN_W, PHONE_H);
      scaleEl.style.transform = 'none';
      scaleEl.style.marginLeft = metrics.padL + 'px';
    }
    if (chrome) {
      chrome.style.transform = 'none';
      chrome.style.marginLeft = metrics.padL + 'px';
      chrome.style.marginRight = metrics.padR + 'px';
      chrome.style.marginBottom = '0px';
    }
    if (outer) {
      applyBox(outer, metrics.contentW, metrics.contentH);
      outer.style.transformOrigin = 'top left';
      outer.style.transform = scale < 0.998 ? 'scale(' + scale + ')' : 'none';
      outer.style.overflow = 'visible';
      if (loadAutoLayout() && loadAutoLayout().centered) centerStageInPanel(metrics, scale);
      else if (needsAutoLayout()) centerStageInPanel(metrics, scale);
      else outer.style.marginLeft = '0px';
    }
    if (grip) {
      grip.style.transform = 'none';
      grip.style.left = metrics.padL + 'px';
      grip.style.top = metrics.chromeH + PHONE_H - GRIP + 'px';
    }
    if (!PHONE_MODE) {
      clearBox(document.documentElement);
      clearBox(document.body);
      if (ui) {
        clearBox(ui);
        ui.style.overflow = 'visible';
      }
    }
    pinNativeHeaderToMove();
  }

  function bindViewportObserver() {
    if (PHONE_MODE) return;
    var ui = document.querySelector('ingame-ui');
    if (!ui || ui._fcViewportObs) return;
    ui._fcViewportObs = true;
    if (typeof ResizeObserver === 'function') {
      var ro = new ResizeObserver(function () {
        scheduleFitStage();
      });
      ro.observe(ui);
    }
  }

  function bindResize() {
    var grip = $('btnResize');
    if (!grip || grip._gxResizeBound) return;
    grip._gxResizeBound = true;
    var drag = null;
    function onMove(e) {
      if (!drag) return;
      var dx = (e.clientX || 0) - drag.x;
      var dy = (e.clientY || 0) - drag.y;
      // Bottom-left grip: down and left grow the phone, height leads so the
      // aspect ratio never changes.
      var f = ((drag.h + dy) / drag.h * 2 + (drag.w - dx) / drag.w) / 3;
      uiScale = clampScale(drag.scale * f);
      fitStage();
      if (e.preventDefault) e.preventDefault();
    }
    function onUp() {
      if (!drag) return;
      drag = null;
      saveScale();
      fitStage();
      log('Handy-Groesse ' + Math.round(uiScale * 100) + '%');
    }
    function onDown(e) {
      if (e.button != null && e.button !== 0) return;
      drag = {
        x: e.clientX || 0,
        y: e.clientY || 0,
        scale: uiScale,
        w: Math.max(1, DESIGN_W * uiScale),
        h: Math.max(1, PHONE_H * uiScale),
      };
      try {
        if (grip.setPointerCapture && e.pointerId != null) grip.setPointerCapture(e.pointerId);
      } catch (e0) {}
      if (e.preventDefault) e.preventDefault();
      if (e.stopPropagation) e.stopPropagation();
    }
    grip.addEventListener('pointerdown', onDown, true);
    grip.addEventListener('mousedown', onDown, true);
    window.addEventListener('pointermove', onMove, true);
    window.addEventListener('mousemove', onMove, true);
    window.addEventListener('pointerup', onUp, true);
    window.addEventListener('mouseup', onUp, true);
    window.addEventListener('pointercancel', onUp, true);
  }

  function namePanel() {
    var ui = document.querySelector('ingame-ui');
    if (ui) {
      ui.setAttribute('title', 'FlyConomy');
      ui.setAttribute('data-title', 'FlyConomy');
      ui.classList.remove('panelInvisible');
      ui.classList.remove('no-header');
      ui.classList.remove('no-resize');
      ui.setAttribute('content-fit', 'false');
      ui.setAttribute('resize-directions', 'all');
      ui.style.overflow = 'visible';
      hideSimHeader();
      if (namePanelRuns === 0) scheduleFitStage();
    }
    // Scanning the sim documents is only useful while the toolbar builds up.
    if (namePanelRuns++ > 8) return;
    try {
      var roots = [document];
      if (window.parent && window.parent.document) roots.push(window.parent.document);
      if (window.top && window.top.document && window.top.document !== document) {
        roots.push(window.top.document);
      }
      for (var r = 0; r < roots.length; r++) {
        var doc = roots[r];
        var nodes = doc.querySelectorAll(
          '[title="CustomPanel"], [aria-label="CustomPanel"], [data-title="CustomPanel"]'
        );
        for (var i = 0; i < nodes.length; i++) {
          nodes[i].setAttribute('title', 'FlyConomy');
          if (nodes[i].getAttribute('aria-label') === 'CustomPanel') {
            nodes[i].setAttribute('aria-label', 'FlyConomy');
          }
          if (nodes[i].textContent && nodes[i].textContent.trim() === 'CustomPanel') {
            nodes[i].textContent = 'FlyConomy';
          }
        }
      }
    } catch (e) {}
  }

  function applyAtcId(reg, force) {
    if (!reg) return;
    const value = String(reg).trim().toUpperCase();
    if (!value) return;
    const now = Date.now();
    if (!force && value === lastAtc && now - atcTriedAt < 4000) return;
    lastAtc = value;
    atcTriedAt = now;
    try {
      if (window.SimVar && typeof SimVar.SetSimVarValue === 'function') {
        SimVar.SetSimVarValue('ATC ID', 'string', value);
        SimVar.SetSimVarValue('ATC ID', 'String', value);
        SimVar.SetSimVarValue('ATC FLIGHT NUMBER', 'string', '');
      }
    } catch (e) {}
  }

  function isPaywarePayload(title) {
    var t = String(title || '').toLowerCase();
    if (!t) {
      try {
        t = String(SimVar.GetSimVarValue('TITLE', 'string') || '').toLowerCase();
      } catch (e0) {}
    }
    return /fenix|fnx|pmdg|toliss|inibuilds|ini builds|tristar|l-?1011|l1011|ifly|fslabs|headwind|qualitywings|felis|maddog|leonardo/.test(
      t
    );
  }

  function applySimPayload(load) {
    if (!load || !window.SimVar || typeof SimVar.SetSimVarValue !== 'function') return;
    var title = '';
    try {
      title = String(SimVar.GetSimVarValue('TITLE', 'string') || '');
    } catch (eT) {}
    if (isPaywarePayload(title)) return;
    try {
      var passengers = Number(load.passengers != null ? load.passengers : 0);
      if (passengers <= 0) {
        var seatsUsed = Number(load.seats_used != null ? load.seats_used : load.pax) || 0;
        if (seatsUsed > 1) passengers = seatsUsed - 1;
      }
      var payloadLb =
        Number(load.payload_lb) ||
        (load.payload_kg != null ? Number(load.payload_kg) * 2.20462262 : 0);
      if (payloadLb <= 0 && passengers <= 0) return;
      var personLb = 170;
      var n = 16;
      try {
        var cnt = SimVar.GetSimVarValue('PAYLOAD STATION COUNT', 'number');
        if (cnt && cnt > 0) n = Math.min(20, Math.max(1, Math.floor(cnt)));
      } catch (e) {}
      var i;
      for (i = 1; i <= n; i++) SimVar.SetSimVarValue('PAYLOAD STATION WEIGHT:' + i, 'Pounds', 0);
      var paxLb = passengers * personLb;
      var cargoLb = Math.max(0, payloadLb - paxLb);
      var paxSlots = cargoLb > 0 ? Math.max(1, n - 1) : n;
      var weights = [];
      for (i = 0; i < n; i++) weights[i] = 0;
      if (paxLb > 0) {
        var eachPax = paxLb / paxSlots;
        for (i = 0; i < paxSlots; i++) weights[i] += eachPax;
      }
      if (cargoLb > 0) {
        var cargoStart = paxLb > 0 ? Math.max(0, n - Math.max(1, Math.floor(n / 3))) : 0;
        var slots = [];
        for (i = cargoStart; i < n; i++) slots.push(i);
        if (!slots.length) slots = [n - 1];
        var eachCargo = cargoLb / slots.length;
        for (i = 0; i < slots.length; i++) weights[slots[i]] += eachCargo;
      }
      for (i = 0; i < n; i++) {
        if (weights[i] > 0) SimVar.SetSimVarValue('PAYLOAD STATION WEIGHT:' + (i + 1), 'Pounds', weights[i]);
      }
    } catch (e) {}
  }

  function settleAirbusFuelMode() {
    if (!window.SimVar || typeof SimVar.SetSimVarValue !== 'function') return;
    try {
      var title = '';
      try {
        title = String(SimVar.GetSimVarValue('TITLE', 'string') || '').toLowerCase();
      } catch (e0) {}
      if (
        title.indexOf('fenix') < 0 &&
        title.indexOf('a32nx') < 0 &&
        title.indexOf('toliss') < 0 &&
        title.indexOf('a320') < 0 &&
        title.indexOf('a321') < 0 &&
        title.indexOf('a319') < 0
      ) {
        return;
      }
      SimVar.SetSimVarValue('L:S_OH_FUEL_MODESEL', 'Number', 0);
      setTimeout(function () {
        try {
          SimVar.SetSimVarValue('L:S_OH_FUEL_MODESEL', 'Number', 1);
        } catch (e2) {}
      }, 1200);
    } catch (e) {}
  }

  function xhrOnce(base, path, opts) {
    return new Promise(function (resolve, reject) {
      var method = (opts && opts.method) || 'GET';
      var sep = path.indexOf('?') >= 0 ? '&' : '?';
      var url = base + path + sep + 'panel=' + encodeURIComponent(PANEL_VERSION);
      var x = new XMLHttpRequest();
      try {
        x.open(method, url, true);
      } catch (e0) {
        reject(e0);
        return;
      }
      var timeoutMs = method === 'POST' ? 20000 : 8000;
      var t0 = Date.now();
      try {
        x.timeout = timeoutMs;
      } catch (e1) {}
      if (opts && opts.body) {
        try {
          x.setRequestHeader('Content-Type', 'text/plain;charset=UTF-8');
        } catch (e2) {}
      }
      x.onreadystatechange = function () {
        if (x.readyState !== 4) return;
        var data = {};
        try {
          data = JSON.parse(x.responseText || '{}');
        } catch (e3) {
          data = {};
        }
        if (x.status >= 200 && x.status < 300 && data.ok !== false) resolve(data);
        else if (x.status === 0) {
          // readyState 4 comes before onerror/ontimeout – let those decide first;
          // a request that ran into the time limit counts as timeout either way.
          setTimeout(function () {
            if (x._fcFailed) return;
            x._fcFailed = true;
            var e0 = new Error('Bridge nicht erreichbar (HTTP 0)');
            e0.status = 0;
            e0.timeout = !!x._fcTimeout || Date.now() - t0 >= timeoutMs - 250;
            reject(e0);
          }, 40);
        } else {
          var err = new Error((data && (data.error || data.message)) || ('HTTP ' + x.status));
          err.status = x.status;
          err.data = data;
          reject(err);
        }
      };
      x.onerror = function () {
        if (x._fcFailed) return;
        x._fcFailed = true;
        var e1 = new Error('Bridge nicht erreichbar');
        e1.status = 0;
        e1.timeout = false;
        reject(e1);
      };
      x.ontimeout = function () {
        x._fcTimeout = true;
        if (x._fcFailed) return;
        x._fcFailed = true;
        var e2 = new Error('Bridge Timeout');
        e2.status = 0;
        e2.timeout = true;
        reject(e2);
      };
      try {
        x.send(opts && opts.body ? JSON.stringify(opts.body) : null);
      } catch (e4) {
        reject(e4);
      }
    });
  }

  async function bridge(path, opts) {
    var last = null;
    var i;
    var order = [bridgeBase];
    for (i = 0; i < BRIDGE_URLS.length; i++) {
      if (BRIDGE_URLS[i] !== bridgeBase) order.push(BRIDGE_URLS[i]);
    }
    for (i = 0; i < order.length; i++) {
      try {
        var data = await xhrOnce(order[i], path, opts);
        bridgeBase = order[i];
        return data;
      } catch (e) {
        last = e;
        // D11: the second URL only when the first was unreachable – never after an
        // answer or a timeout, and never for start/cancel/refuel/end.
        if (!L.shouldTryFallback(path, e)) break;
      }
    }
    throw last || new Error('Bridge nicht erreichbar');
  }

  function showAlert(msg, ok) {
    const el = $('alertBox');
    if (!el) return;
    if (!msg) {
      el.textContent = '';
      el.className = 'gx-alert hidden';
      return;
    }
    el.textContent = String(msg);
    el.className = 'gx-alert' + (ok ? ' ok' : '');
  }

  function setView(name) {
    if (name !== 'keys' && Date.now() < keysListenUntil) void bindHotkey('start');
    view = name;
    const stage = $('stage');
    if (stage) stage.setAttribute('data-view', name);
    setHidden($('viewHome'), name !== 'home');
    setHidden($('viewGsx'), name !== 'gsx');
    setHidden($('viewFuel'), name !== 'fuel');
    setHidden($('viewMore'), name !== 'more');
    setHidden($('viewRamp'), name !== 'ramp');
    setHidden($('viewRamp2'), name !== 'ramp2');
    setHidden($('viewKeys'), name !== 'keys');
    const back = $('btnBack');
    if (back) {
      if (name === 'home') back.classList.add('is-off');
      else back.classList.remove('is-off');
      back.setAttribute('data-go', name === 'ramp2' ? 'ramp' : name === 'keys' ? 'more' : 'home');
    }
    setHidden($('btnFlight'), name !== 'home');
    setHidden($('btnFuelHold'), name !== 'fuel');
    each(document.querySelectorAll('.mode'), function (m) {
      if (m.getAttribute('data-mode') === fuelMode) m.classList.add('on');
      else m.classList.remove('on');
    });
    syncFuelPace();
    if (name === 'fuel') void loadFuelDesk();
  }

  function syncFuelPace() {
    setHidden($('fuelPace'), view !== 'fuel' || fuelMode !== 'direct');
    var slow = $('btnPaceSlow');
    var fast = $('btnPaceFast');
    if (slow) {
      if (fuelPace === 'slow') slow.classList.add('on');
      else slow.classList.remove('on');
    }
    if (fast) {
      if (fuelPace === 'fast') fast.classList.add('on');
      else fast.classList.remove('on');
    }
  }

  function isInteractive(el) {
    if (!el || !el.closest) return false;
    return !!el.closest(
      'button, input, select, textarea, a, .tile, .hold, .cta, .gx-ico, .dock-back, .fuel-pace, .modal-card, .keys-bind, .chrome-btn, .chrome-grip, #phoneChrome'
    );
  }

  function findDragHeader(ui) {
    var sel =
      '.ingameUiHeader, .ingameUiHeaderWrapper, .panelHeader, .titleBar, .title-bar, .header';
    var roots = [];
    if (ui) {
      roots.push(ui);
      if (ui.shadowRoot) roots.push(ui.shadowRoot);
    }
    roots.push(document);
    var r, i, list;
    for (r = 0; r < roots.length; r++) {
      if (!roots[r] || !roots[r].querySelectorAll) continue;
      list = roots[r].querySelectorAll(sel);
      for (i = 0; i < list.length; i++) {
        if (list[i].closest && list[i].closest('#stageOuter')) continue;
        return list[i];
      }
    }
    return null;
  }

  function panelIds(ui) {
    var ids = [];
    if (ui) {
      if (ui.getAttribute) ids.push(ui.getAttribute('panel-id'));
      if (ui.id) ids.push(ui.id);
    }
    ids.push('PANEL_FLYCONOMY', 'PANEL_CUSTOM_PANEL', 'CustomPanel', 'FlyConomyPanel');
    var out = [];
    var seen = {};
    var i;
    for (i = 0; i < ids.length; i++) {
      if (!ids[i] || seen[ids[i]]) continue;
      seen[ids[i]] = true;
      out.push(ids[i]);
    }
    return out;
  }

  function startNativeDrag(e, ui) {
    ui = ui || document.querySelector('ingame-ui');
    var ids = panelIds(ui);
    var i;
    try {
      if (window.Coherent && typeof Coherent.trigger === 'function') {
        for (i = 0; i < ids.length; i++) {
          Coherent.trigger('ON_INGAME_UI_START_DRAG', ids[i]);
          Coherent.trigger('INGAME_UI_START_DRAGGING', ids[i]);
          Coherent.trigger('ON_INGAMEPANEL_START_DRAG', ids[i]);
        }
      }
    } catch (eC) {}
    if (ui) {
      try {
        ui.setAttribute('draggable', 'true');
      } catch (eD) {}
      if (typeof ui.startDragging === 'function') {
        try {
          ui.startDragging(e);
          return true;
        } catch (e0) {}
        try {
          ui.startDragging();
          return true;
        } catch (e1) {}
      }
      if (typeof ui.setDragging === 'function') {
        try {
          ui.setDragging(true);
          return true;
        } catch (e2) {}
      }
    }
    var header = findDragHeader(ui);
    if (!header || !e) return false;
    try {
      var ev = document.createEvent('MouseEvents');
      ev.initMouseEvent(
        'mousedown',
        true,
        true,
        window,
        1,
        e.screenX,
        e.screenY,
        e.clientX,
        e.clientY,
        false,
        false,
        false,
        false,
        0,
        null
      );
      header.dispatchEvent(ev);
      return true;
    } catch (e3) {}
    return false;
  }

  function closeInGamePanel() {
    if (/\/phone(?:\/|$)/.test(String(location.pathname || ''))) {
      try {
        window.close();
      } catch (e0) {}
      return;
    }
    var ui = document.querySelector('ingame-ui');
    var ids = panelIds(ui);
    var i;
    if (ui && typeof ui.closePanel === 'function') {
      try {
        ui.closePanel();
        return;
      } catch (e1) {}
    }
    try {
      if (window.Coherent && typeof Coherent.trigger === 'function') {
        for (i = 0; i < ids.length; i++) {
          Coherent.trigger('ON_CLOSE_PANEL', ids[i]);
          Coherent.trigger('CLOSE_INGAME_PANEL', ids[i]);
          Coherent.trigger('INGAMEPANEL_CLOSED', ids[i]);
          Coherent.trigger('ON_TOOLBAR_BUTTON_CLICK', ids[i]);
        }
      }
    } catch (e2) {}
    var closeBtn =
      document.querySelector('.ingameUiHeader .close, .ingameUiClose, .button.close, button.close') ||
      (ui && ui.shadowRoot && ui.shadowRoot.querySelector('.close, .ingameUiClose, button.close'));
    if (closeBtn && typeof closeBtn.click === 'function') {
      try {
        closeBtn.click();
        return;
      } catch (e3) {}
    }
    if (ui) {
      ui.classList.add('panelInvisible');
      try {
        ui.dispatchEvent(new Event('panelInactive'));
      } catch (e4) {}
    }
  }

  function bindPanelDrag() {
    var ui = document.querySelector('ingame-ui');
    var move = $('btnMove');
    var closeBtn = $('btnClosePanel');
    if (closeBtn && !closeBtn._gxCloseBound) {
      closeBtn._gxCloseBound = true;
      closeBtn.addEventListener('click', function (e) {
        e.preventDefault();
        e.stopPropagation();
        closeInGamePanel();
      });
    }
    bindResize();
    if (!move || move._gxDragBound) return;
    move._gxDragBound = true;
    function onDown(e) {
      if (e.button != null && e.button !== 0) return;
      pinNativeHeaderToMove();
      forwardToNativeHeader(e);
      startNativeDrag(e, ui);
    }
    move.addEventListener('pointerdown', onDown, true);
    move.addEventListener('mousedown', onDown, true);
  }

  function paintChips(snap, bridgeOk) {
    snap = snap || {};
    const inflight = !!(snap && snap.active_flight_id);
    const mf = (snap && snap.myflight) || {};
    const ac = mf.aircraft || {};
    const addons = (snap && snap.addons) || {};
    // In-flight: prefer the bridge registration. myflight can briefly arrive
    // without aircraft and made the chip flash "kein AC" every poll.
    const lockedReg = String(ac.registration || '').trim();
    const liveReg = String(snap.registration || '').trim();
    const label = String(snap.aircraft_label || '').trim();
    const reg = inflight
      ? liveReg || lockedReg || label
      : lockedReg || liveReg || label;
    const setChip = function (id, text, cls) {
      const el = $(id);
      if (!el) return;
      el.textContent = text;
      el.className = 'chip' + (cls ? ' ' + cls : '');
    };
    setChip('chipBridge', bridgeOk ? (snap.logged_in ? 'Bridge OK' : 'Client OK') : 'Bridge offline', bridgeOk ? 'ok' : 'bad');
    setChip(
      'chipAc',
      reg || 'kein AC',
      reg ? 'ok' : 'warn'
    );
    setChip(
      'chipJobs',
      String(mf.jobs_loaded || 0) + ' Jobs',
      mf.jobs_loaded ? 'ok' : ''
    );
    const pax = mf.pax != null ? mf.pax : (snap.load && snap.load.passengers);
    const seats = mf.seats || (snap.load && snap.load.seats) || 0;
    setChip('chipPax', (pax != null ? pax : 0) + (seats ? '/' + seats : '') + ' Pax', pax ? 'ok' : '');
    if (addons.fenix) {
      setHidden($('chipFenix'), false);
      setChip('chipFenix', 'Fenix', 'ok');
    } else {
      setHidden($('chipFenix'), true);
    }
    if (addons.gsx) {
      setHidden($('chipGsx'), false);
      setChip('chipGsx', 'GSX', 'ok');
    } else {
      setHidden($('chipGsx'), true);
    }
  }

  function paint(snap, bridgeOk) {
    snap = snap || {};
    snapCache = snap;
    const inflight = !!(snap && snap.active_flight_id);
    const logged = !!(snap && snap.logged_in);
    const mf = (snap && snap.myflight) || {};
    const ac = mf.aircraft || {};
    const lockedReg = ac.registration || '';
    const reg = lockedReg || (inflight ? snap.registration || '' : '') || snap.aircraft_label || '';
    const icao = ac.location || snap.current_icao || '';
    const name = snap.current_airport_name || '';
    const paused = inflight && !!snap.tracking_paused;
    if ($('headVer')) $('headVer').textContent = 'v' + PANEL_VERSION;
    if ($('headSub')) {
      $('headSub').textContent = inflight
        ? (paused ? 'Flug pausiert  ' : 'Flug aktiv  ') + fmtHms(snap.flight_elapsed_sec)
        : name || (logged ? 'Bereit' : bridgeOk ? 'Nicht angemeldet' : 'Client offline');
    }
    paintChips(snap, bridgeOk);
    if (!fuelBusy) {
      // D12: queued start, position question or the last result stay visible
      const sticky = L.stickyText(snap, Date.now() / 1000);
      showAlert(sticky, !!(sticky && !snap.pending_start && !snap.position_mismatch && !L.unrecorded(snap)));
    }

    const btn = $('btnFlight');
    if (btn && !flightBusy) {
      const fb = L.flightButton(snap);
      btn.disabled = !logged || !bridgeOk || fb.disabled;
      btn.className = fb.cls;
      btn.textContent = fb.text;
    }
    if ($('btnCancel')) $('btnCancel').disabled = !logged || !inflight;
    paintCancel();
    if ($('btnLoadEfb')) $('btnLoadEfb').disabled = L.efbLocked(snap);
    setHidden($('mismatchBar'), !(inflight && snap.mismatch_choice_open));
    if ($('btnLivery')) $('btnLivery').disabled = !logged;
    if ($('btnReport')) $('btnReport').disabled = !bridgeOk;
    if ($('btnWebLogin')) $('btnWebLogin').disabled = !bridgeOk || webLoginBusy;
    if ($('btnWebLoginAlt')) $('btnWebLoginAlt').disabled = !bridgeOk || webLoginBusy;

    const foot = $('footBar');
    if (foot) {
      const bits = [];
      if (bridgeOk) bits.push('Bridge OK');
      else bits.push('Bridge offline');
      if (snap && snap.addons && snap.addons.gsx) bits.push('GSX OK');
      if (snap && snap.addons && snap.addons.fenix) bits.push('Fenix OK');
      foot.textContent = bits.join('  ·  ');
      foot.className = 'gx-foot' + (bridgeOk ? '' : ' bad');
    }
    if ($('statusLine')) {
      $('statusLine').textContent = paused
        ? snap.sim_paused
          ? 'Simulator pausiert – Zeit und Track stehen. Der Flug laeuft weiter.'
          : snap.position_mismatch
          ? 'Flugzeug steht woanders als vor der Pause - Flug bleibt offen, bitte loggen oder abbrechen.'
          : 'Keine Sim-Daten - Aufzeichnung pausiert. Der Flug laeuft weiter, sobald Daten kommen.'
        : inflight
        ? 'Am Ziel stehen + Handbremse - oder Flug loggen.'
        : logged
          ? snap.last_message || 'Bereit.'
          : 'MiniClient starten und mit Discord anmelden.';
    }
    paintKeys(snap);
    if (view === 'fuel' && (fuelDesk || (snap && snap.fuel_pump && snap.fuel_pump.active))) paintFuel();
    applyAtcId(reg || '');
    var fid = snap && snap.active_flight_id;
    var load = (snap && snap.load) || {};
    if (inflight && fid && load && (load.payload_kg || load.pax || load.seats_used)) {
      if (payloadAppliedAt !== fid) {
        payloadAppliedAt = fid;
        applySimPayload(load);
      }
    } else if (!inflight) payloadAppliedAt = 0;
  }

  function paintCancel() {
    const b = $('btnCancel');
    if (!b) return;
    const armed = cancelArmedUntil && Date.now() <= cancelArmedUntil;
    if (!armed) cancelArmedUntil = 0;
    const lab = b.querySelector('.lab');
    if (lab) lab.textContent = armed ? 'Nochmal tippen!' : 'Flug abbrechen';
    if (armed) b.classList.add('armed');
    else b.classList.remove('armed');
  }

  async function positionChoice(choice) {
    try {
      const data = await bridge('/position-choice', { method: 'POST', body: { choice: choice } });
      log(choice === 'reposition' ? 'Flugzeug wird zurueckgesetzt...' : 'Weiterflug ab hier gemeldet');
      showAlert((data && data.message) || '', true);
      await refresh();
    } catch (e) {
      showAlert(errText(e));
    }
  }

  function sellerSelected() {
    const sel = $('fuelSeller');
    if (!sel || !fuelDesk) return null;
    const id = Number(sel.value);
    const list = fuelDesk.sellers || fuelDesk.fuel_sellers || fuelDesk.fbos || [];
    for (var i = 0; i < list.length; i++) if (Number(list[i].id) === id) return list[i];
    return list[0] || null;
  }

  function paintFuel() {
    if (!fuelDesk || !fuelDesk.aircraft) return;
    const ac = fuelDesk.aircraft;
    const cap = Number(ac.fuel_capacity) || 0;
    const cur = Number(ac.fuel_gallons) || 0;
    const pct = cap > 0 ? (cur / cap) * 100 : 0;
    if ($('fuelIcao')) $('fuelIcao').textContent = fuelDesk.icao || ac.location || '-';
    if ($('fuelType')) $('fuelType').textContent = String(ac.fuel_type || 'JET A').toUpperCase();
    if ($('fuelRead')) {
      $('fuelRead').textContent =
        fmtN(toUnit(cur, fuelUnit, fuelDesk), fuelUnit) +
        ' / ' +
        fmtN(toUnit(cap, fuelUnit, fuelDesk), fuelUnit) +
        ' ' +
        fuelUnit;
    }
    if ($('fuelBar')) $('fuelBar').style.width = Math.max(0, Math.min(100, pct)) + '%';
    if ($('btnFuelUnit')) {
      const lab = $('btnFuelUnit').querySelector('.lab');
      if (lab) lab.textContent = fuelUnit;
    }
    if ($('fuelUnitLbl')) $('fuelUnitLbl').textContent = fuelUnit;
    const range = $('fuelRange');
    if (range) {
      range.max = String(cap);
      range.value = String(fuelTarget);
    }
    if (!amountFocus && $('fuelAmount')) {
      $('fuelAmount').value = fmtN(toUnit(fuelTarget, fuelUnit, fuelDesk), fuelUnit);
    }
    const s = sellerSelected();
    const d = Math.round((fuelTarget - cur) * 10) / 10;
    const gal = Math.abs(d);
    const buyback = Number(fuelDesk.bank_buyback_price || 0);
    const price = d < 0 ? (buyback || 5.5) : s ? Number(s.price_per_gal) : 0;
    const est = d < 0 ? Math.round(gal * price * 0.7) : Math.round(gal * price);
    const stock = s ? Number(s.gallons_available) : 0;
    const bankish = !!(s && (s.is_bank_fallback || s.is_system));
    if ($('fuelPrice')) {
      $('fuelPrice').textContent = price ? '$' + price.toFixed(2) + ' / gal' : '-';
    }
    if ($('fuelEst')) {
      $('fuelEst').textContent =
        (s && (s.name || s.owner_name) ? (s.name || s.owner_name) + ' · ' : '') +
        (gal < 0.05 ? 'Keine Menge' : (d < 0 ? 'Rueckkauf $' : 'Kosten $') + est);
    }
    if ($('fuelStock')) {
      $('fuelStock').textContent = !s
        ? '-'
        : bankish && stock > 1000000
          ? 'Bestand: unbegrenzt'
          : 'Bestand: ' + Math.round(stock) + ' gal';
    }
    const pump = (snapCache && snapCache.fuel_pump) || {};
    const pumping = !!pump.active;
    if (lastPumpActive && !pumping) {
      void loadFuelDesk();
      if ($('holdFill')) $('holdFill').style.width = '0';
    }
    lastPumpActive = pumping;
    const hold = $('btnFuelHold');
    if (hold) {
      hold.disabled = fuelBusy || (!pumping && gal < 0.05);
      if (pumping) hold.classList.add('is-pumping');
      else hold.classList.remove('is-pumping');
      if ($('holdLbl')) {
        if (pumping && fuelMode === 'direct' && fuelPace === 'slow') {
          $('holdLbl').textContent = 'Tanke ' + Math.round(Number(pump.pct) || 0) + '%';
        } else if (pumping) {
          $('holdLbl').textContent = 'Tanke ' + Math.round(Number(pump.pct) || 0) + '%';
        } else if (d < -0.05) {
          $('holdLbl').textContent = 'Defuelen';
        } else {
          $('holdLbl').textContent = 'Tanken';
        }
      }
      if ($('holdFill')) {
        $('holdFill').style.width = pumping ? Math.max(0, Math.min(100, Number(pump.pct) || 0)) + '%' : '0';
      }
    }
  }

  async function loadFuelDesk() {
    try {
      fuelDesk = await bridge('/fuel-desk');
      const ac = fuelDesk.aircraft || {};
      fuelTarget = Number(ac.fuel_gallons) || 0;
      const sel = $('fuelSeller');
      if (sel) {
        sel.innerHTML = '';
        each(fuelDesk.sellers || fuelDesk.fuel_sellers || fuelDesk.fbos || [], function (s) {
          const o = document.createElement('option');
          o.value = s.id;
          o.textContent =
            (s.name || 'FBO') +
            ' · $' +
            Number(s.price_per_gal || 0).toFixed(2) +
            '/gal · ' +
            (s.is_bank_fallback && Number(s.gallons_available) > 1000000
              ? 'unbegrenzt'
              : Math.round(Number(s.gallons_available) || 0) + ' gal');
          sel.appendChild(o);
        });
      }
      paintFuel();
    } catch (e) {
      showAlert(errText(e));
    }
  }

  async function commitFuel() {
    if (!fuelDesk || !fuelDesk.aircraft || fuelBusy) return;
    const cur = Number(fuelDesk.aircraft.fuel_gallons) || 0;
    const d = Math.round((fuelTarget - cur) * 10) / 10;
    const gal = Math.abs(d);
    if (gal < 0.05) return;
    fuelBusy = true;
    try {
      const s = sellerSelected();
      const data = await bridge('/refuel', {
        method: 'POST',
        body: {
          aircraft_id: fuelDesk.aircraft.id,
          fuel_type: fuelDesk.aircraft.fuel_type,
          gallons: gal,
          operation: d < 0 ? 'defuel' : 'refuel',
          fbo_id: s && s.id,
          sim_mode: fuelMode,
          pump_pace: fuelMode === 'direct' ? fuelPace : '',
        },
      });
      log(data.message || 'Tanken OK');
      if (data.operation === 'defuel' && data.refund != null) {
        const who =
          data.airline_name ||
          (data.credited_account && data.credited_account !== 'pilot'
            ? data.credited_account
            : 'dein Konto');
        log('Rueckkauf $' + Math.round(Number(data.refund) || 0) + ' → ' + who);
      }
      settleAirbusFuelMode();
      await loadFuelDesk();
      await refresh();
    } catch (e) {
      showAlert(errText(e));
      log('Tanken: ' + errText(e));
    } finally {
      fuelBusy = false;
      if ($('holdFill')) $('holdFill').style.width = '0';
      paintFuel();
    }
  }

  async function onGsx(action) {
    try {
      const data = await bridge('/gsx', { method: 'POST', body: { action: action } });
      log(data.message || action);
      showAlert(data.message || ('GSX: ' + action), true);
    } catch (e) {
      showAlert(errText(e));
    }
  }

  async function onLoadEfb() {
    if (L.efbLocked(snapCache)) {
      showAlert('„Ins EFB laden“ ist im Flug gesperrt.');
      return;
    }
    try {
      var data;
      try {
        data = await bridge('/apply-load', { method: 'POST', body: {} });
      } catch (e1) {
        // GET only when the POST never reached the client (not after a timeout:
        // the load may already be on its way) and never during a flight.
        if (e1 && e1.status === 0 && !e1.timeout && !(snapCache && snapCache.active_flight_id)) {
          data = await bridge('/apply-load');
        } else {
          throw e1;
        }
      }
      applySimPayload((snapCache && snapCache.load) || {});
      settleAirbusFuelMode();
      log(data.message || 'EFB');
      showAlert(data.message || 'Beladung ins Flugzeug geschrieben', true);
    } catch (e) {
      showAlert(errText(e));
    }
  }

  async function refresh() {
    // Don't nuke the UI while a flight start/end is in progress — that call
    // can block SimConnect for several seconds and made the chips flicker.
    if (flightBusy) return;
    try {
      let snap = await bridge('/status');
      if (
        snap.logged_in &&
        !snap.sim_connected &&
        !snap.active_flight_id &&
        Date.now() - simTriedAt > 8000
      ) {
        simTriedAt = Date.now();
        try {
          await bridge('/connect-sim', { method: 'POST', body: {} });
          snap = await bridge('/status');
        } catch (e) {}
      }
      bridgeFailStreak = 0;
      lastBridgeOkAt = Date.now();
      snapCache = snap;
      paint(snap, true);
      if (view === 'fuel' && (fuelDesk || (snap && snap.fuel_pump && snap.fuel_pump.active))) paintFuel();
    } catch (e) {
      bridgeFailStreak += 1;
      // Hold the last good paint for a few failures / ~5s. One missed poll under
      // SimConnect load must not flash "Bridge offline / kein AC".
      if (bridgeFailStreak < 3 && snapCache && Date.now() - lastBridgeOkAt < 6000) {
        paint(snapCache, true);
        return;
      }
      paint(null, false);
    }
  }

  async function onFlight() {
    const inflight = !!(snapCache && snapCache.active_flight_id);
    const btn = $('btnFlight');
    if (flightBusy) return;
    flightBusy = true;
    if (btn) {
      btn.classList.add('is-press', 'busy');
      btn.textContent = inflight ? 'Flug wird geloggt...' : 'Flug wird gestartet...';
    }
    try {
      if (inflight) {
        const data = await bridge('/end', { method: 'POST', body: {} });
        if (data && (data.keep_flying || data.ok === false)) {
          log(data.error || data.message || 'Flug laeuft weiter.');
          return data;
        }
        log(data && data.logging ? 'Flug wird geloggt...' : 'Flug geloggt');
        showAlert('');
        return data;
      }
      log('Starte Flug...');
      if (!snapCache || !snapCache.sim_connected) {
        log('Verbinde MSFS...');
        try {
          await bridge('/connect-sim', { method: 'POST', body: {} });
          snapCache = await bridge('/status');
          paint(snapCache, true);
        } catch (e0) {
          log(errText(e0));
        }
      }
      const data = await bridge('/start', { method: 'POST', body: {} });
      const r = data.result || {};
      applyAtcId((r.aircraft && r.aircraft.registration) || '', true);
      if (r.load) {
        payloadAppliedAt = Date.now();
        applySimPayload(r.load);
        settleAirbusFuelMode();
      }
      log('Gestartet von ' + (r.from_icao || '?'));
    } catch (e) {
      log(errText(e));
    } finally {
      flightBusy = false;
      if (btn) btn.classList.remove('is-press', 'busy');
      await refresh();
    }
  }

  async function onWebLogin() {
    if (webLoginBusy) return;
    webLoginBusy = true;
    try {
      const data = await bridge('/open-web-login', { method: 'POST', body: {} });
      log(data.message || 'Website');
    } catch (e) {
      showAlert(errText(e));
    } finally {
      webLoginBusy = false;
      await refresh();
    }
  }

  function onKey(ev) {
    if (ev.target && (ev.target.tagName === 'INPUT' || ev.target.tagName === 'SELECT')) return;
    const k = ev.key;
    if (Date.now() < keysListenUntil) {
      // While a key is being bound the number shortcuts must stay quiet.
      if (k === 'Escape') void bindHotkey('start');
      return;
    }
    if (k === 'Escape') {
      setView('home');
      return;
    }
    const map = { 1: '1', 2: '2', 3: '3', 4: '4', 5: '5', 6: '6', 7: '7', 8: '8', 9: '9', 0: '0' };
    if (!map[k]) return;
    const roots = {
      home: $('viewHome'),
      gsx: $('viewGsx'),
      fuel: $('viewFuel'),
      more: $('viewMore'),
      ramp: $('viewRamp'),
      ramp2: $('viewRamp2'),
      keys: $('viewKeys'),
    };
    const root = roots[view] || $('viewHome');
    if (!root) return;
    const btn = root.querySelector('[data-key="' + k + '"]');
    if (!btn) return;
    if (!L.keyAllowed({ id: btn.id, gsx: btn.getAttribute('data-gsx') })) return;
    btn.click();
  }

  async function onSpawn(kind) {
    try {
      const data = await bridge('/spawn', { method: 'POST', body: { kind: kind } });
      log((data && data.message) || kind);
      showAlert((data && data.message) || '', true);
    } catch (e) {
      showAlert(e.message || String(e));
    }
  }

  function paintKeys(snap) {
    const hk = (snap && snap.hotkeys) || {};
    const startBtn = $('btnBindStart');
    const label = hk.start_label || hk.end_label || '';
    if (hk.listening) keysSawListen = true;
    else if (keysSawListen || (keysListenUntil && label && label !== keysLabelAtBind)) {
      keysSawListen = false;
      keysListenUntil = 0;
      keysFastPoll(false);
    }
    const listening = hk.listening || (Date.now() < keysListenUntil ? 'start' : '');
    if (startBtn) {
      startBtn.textContent = listening
        ? 'Taste druecken...'
        : label || 'Beliebige Taste druecken';
      if (listening) startBtn.classList.add('listening');
      else startBtn.classList.remove('listening');
    }
    if ($('keysVol') && hk.volume != null && document.activeElement !== $('keysVol')) {
      $('keysVol').value = String(hk.volume);
    }
    if ($('keysVolLbl') && hk.volume != null) $('keysVolLbl').textContent = String(hk.volume) + '%';
    if ($('keysHint')) {
      $('keysHint').textContent = listening
        ? 'Jetzt die Taste am Yoke, Throttle oder der Tastatur druecken. Fuer Kombi: erste halten, dann zweite. Nochmal tippen bricht ab.'
        : 'Eine Taste fuer Start und Loggen – Tastatur, Yoke oder Honeycomb-Throttle. Wirkt bei geschlossenem Panel.';
    }
  }

  function keysFastPoll(on) {
    if (keysPollTimer) {
      clearInterval(keysPollTimer);
      keysPollTimer = 0;
    }
    if (on) keysPollTimer = setInterval(function () { void refresh(); }, 300);
  }

  async function bindHotkey(slot) {
    if (Date.now() < keysListenUntil) {
      keysListenUntil = 0;
      keysSawListen = false;
      keysFastPoll(false);
      paintKeys(snapCache);
      try {
        paintKeys(await bridge('/hotkey', { method: 'POST', body: { action: 'cancel', slot: slot } }));
      } catch (e) {}
      return;
    }
    const hkNow = (snapCache && snapCache.hotkeys) || {};
    keysLabelAtBind = hkNow.start_label || hkNow.end_label || '';
    keysListenUntil = Date.now() + 20000;
    keysSawListen = false;
    paintKeys(snapCache);
    keysFastPoll(true);
    setTimeout(function () {
      if (Date.now() >= keysListenUntil) keysFastPoll(false);
    }, 20500);
    try {
      const data = await bridge('/hotkey', { method: 'POST', body: { action: 'listen', slot: slot } });
      paintKeys(data);
      log('Warte auf Taste...');
    } catch (e) {
      keysListenUntil = 0;
      keysFastPoll(false);
      paintKeys(snapCache);
      showAlert(e.message || String(e));
    }
  }

  async function clearHotkey(slot) {
    try {
      const data = await bridge('/hotkey', { method: 'POST', body: { action: 'clear', slot: slot } });
      paintKeys(data);
    } catch (e) {
      showAlert(e.message || String(e));
    }
  }

  async function setHotkeyVolume(pct) {
    try {
      const data = await bridge('/hotkey', { method: 'POST', body: { action: 'volume', volume: pct } });
      paintKeys(data);
    } catch (e) {}
  }

  function start() {
    if (started) return;
    if (!$('btnFlight')) return;
    started = true;
    if (!document.getElementById('fcInlineCss')) {
      var hrefs = [
        'CustomPanel.css',
        'FlyConomyPanel.css',
        '/InGamePanels/CustomPanel/CustomPanel.css',
      ];
      for (var hi = 0; hi < hrefs.length; hi++) {
        var ln = document.createElement('link');
        ln.rel = 'stylesheet';
        ln.type = 'text/css';
        ln.href = hrefs[hi];
        document.head.appendChild(ln);
      }
    }
    if (PHONE_MODE) {
      if ($('btnMove')) $('btnMove').classList.add('hidden');
    }
    try {
      const u = window.localStorage.getItem('flyconomy_fuel_unit');
      if (UNITS.indexOf(u) >= 0) fuelUnit = u;
    } catch (e) {}

    uiScale = loadScale();
    if (needsAutoLayout()) scheduleAutoFitFirstOpen();
    namePanel();
    setInterval(namePanel, 3000);
    bindPanelDrag();
    bindViewportObserver();
    window.addEventListener('resize', scheduleFitStage);
    scheduleFitStage();

    window.addEventListener('pointerdown', function (e) {
      const t =
        e.target &&
        e.target.closest &&
        e.target.closest('.tile, .cta, .dock-back, .hold, .gx-ico, .fuel-pace, .keys-bind, .mode, .chrome-btn');
      if (t && !t.disabled) t.classList.add('is-press');
    });
    window.addEventListener('pointerup', function () {
      each(document.querySelectorAll('.is-press'), function (el) {
        if (!el.classList.contains('busy')) el.classList.remove('is-press');
      });
    });
    window.addEventListener('pointercancel', function () {
      each(document.querySelectorAll('.is-press'), function (el) {
        if (!el.classList.contains('busy')) el.classList.remove('is-press');
      });
    });

    each(document.querySelectorAll('[data-go]'), function (btn) {
      btn.addEventListener('click', function () {
        setView(btn.getAttribute('data-go'));
      });
    });
    each(document.querySelectorAll('.gsx-act'), function (btn) {
      btn.addEventListener('click', function () {
        void onGsx(btn.getAttribute('data-gsx'));
      });
    });
    each(document.querySelectorAll('[data-fuel-sim]'), function (btn) {
      btn.addEventListener('click', function () {
        fuelMode = btn.getAttribute('data-fuel-sim') || 'auto';
        setView('fuel');
        each(document.querySelectorAll('.mode'), function (m) {
          if (m.getAttribute('data-mode') === fuelMode) m.classList.add('on');
          else m.classList.remove('on');
        });
      });
    });
    each(document.querySelectorAll('.spawn-act'), function (btn) {
      btn.addEventListener('click', function () {
        void onSpawn(btn.getAttribute('data-spawn'));
      });
    });
    if ($('btnBindStart'))
      $('btnBindStart').addEventListener('click', function () {
        void bindHotkey('start');
      });
    if ($('btnBindEnd'))
      $('btnBindEnd').addEventListener('click', function () {
        void bindHotkey('end');
      });
    if ($('btnClearStart'))
      $('btnClearStart').addEventListener('click', function () {
        void clearHotkey('start');
      });
    if ($('btnClearEnd'))
      $('btnClearEnd').addEventListener('click', function () {
        void clearHotkey('end');
      });
    if ($('btnKeysTest'))
      $('btnKeysTest').addEventListener('click', function () {
        var el = $('keysVol');
        void setHotkeyVolume(el ? Number(el.value) : 35);
      });
    if ($('keysVol'))
      $('keysVol').addEventListener('change', function (e) {
        void setHotkeyVolume(Number(e.target.value) || 0);
      });
    if ($('btnLoadEfb')) $('btnLoadEfb').addEventListener('click', onLoadEfb);
    if ($('btnFlight')) $('btnFlight').addEventListener('click', onFlight);
    if ($('btnCancel'))
      $('btnCancel').addEventListener('click', async function () {
        // D11: two taps within 4 s – a single (accidental) tap only arms.
        const tap = L.cancelTap(cancelArmedUntil, Date.now());
        cancelArmedUntil = tap.armedUntil;
        if (!tap.fire) {
          paintCancel();
          showAlert('Nochmal tippen, um den Flug wirklich abzubrechen.');
          setTimeout(paintCancel, L.CANCEL_ARM_MS + 50);
          return;
        }
        paintCancel();
        try {
          const data = await bridge('/cancel', { method: 'POST', body: { confirm: 'two-tap' } });
          log((data && data.pending_confirm && data.message) || 'Abgebrochen');
          await refresh();
        } catch (e) {
          log(errText(e));
        }
      });
    if ($('btnPosBack'))
      $('btnPosBack').addEventListener('click', function () {
        void positionChoice('reposition');
      });
    if ($('btnPosHere'))
      $('btnPosHere').addEventListener('click', function () {
        void positionChoice('continue');
      });
    if ($('btnLivery'))
      $('btnLivery').addEventListener('click', async function () {
        try {
          const data = await bridge('/livery-request', { method: 'POST', body: {} });
          log(data.message || 'Livery');
        } catch (e) {
          showAlert(errText(e));
        }
      });
    if ($('btnReport'))
      $('btnReport').addEventListener('click', async function () {
        try {
          await bridge('/report-problem', {
            method: 'POST',
            body: {
              code: (snapCache && (snapCache.last_error_code || snapCache.error_code)) || '',
              title: 'Panel – Problem melden',
              message: (snapCache && (snapCache.last_error || snapCache.last_message)) || '',
            },
          });
          log('Problem-Fenster geoeffnet');
        } catch (e) {
          log(errText(e));
        }
      });
    if ($('btnWebLogin')) $('btnWebLogin').addEventListener('click', onWebLogin);
    if ($('btnWebLoginAlt')) $('btnWebLoginAlt').addEventListener('click', onWebLogin);
    if ($('btnLog')) $('btnLog').addEventListener('click', function () { $('logModal').classList.remove('hidden'); });
    if ($('btnLog2')) $('btnLog2').addEventListener('click', function () { $('logModal').classList.remove('hidden'); });
    if ($('btnLogClose')) $('btnLogClose').addEventListener('click', function () { $('logModal').classList.add('hidden'); });
    if ($('logModal'))
      $('logModal').addEventListener('click', function (ev) {
        if (ev.target === $('logModal')) $('logModal').classList.add('hidden');
      });

    if ($('btnFuelUnit'))
      $('btnFuelUnit').addEventListener('click', function () {
        fuelUnit = UNITS[(UNITS.indexOf(fuelUnit) + 1) % UNITS.length];
        try {
          window.localStorage.setItem('flyconomy_fuel_unit', fuelUnit);
        } catch (e) {}
        paintFuel();
      });
    if ($('fuelRange'))
      $('fuelRange').addEventListener('input', function (e) {
        fuelTarget = Number(e.target.value) || 0;
        paintFuel();
      });
    if ($('fuelAmount')) {
      $('fuelAmount').addEventListener('focus', function () {
        amountFocus = true;
      });
      $('fuelAmount').addEventListener('blur', function () {
        amountFocus = false;
        paintFuel();
      });
      $('fuelAmount').addEventListener('input', function (e) {
        const n = parseFloat(String(e.target.value).replace(',', '.'));
        if (!isFinite(n) || !fuelDesk || !fuelDesk.aircraft) return;
        const cap = Number(fuelDesk.aircraft.fuel_capacity) || 0;
        fuelTarget = Math.max(0, Math.min(cap, fromUnit(n, fuelUnit, fuelDesk)));
        paintFuel();
      });
    }
    if ($('fuelSeller')) $('fuelSeller').addEventListener('change', paintFuel);
    each(document.querySelectorAll('.fuel-pct'), function (b) {
      b.addEventListener('click', function () {
        if (!fuelDesk || !fuelDesk.aircraft) return;
        const cap = Number(fuelDesk.aircraft.fuel_capacity) || 0;
        fuelTarget = (Number(b.getAttribute('data-pct')) / 100) * cap;
        paintFuel();
      });
    });
    each(document.querySelectorAll('.mode'), function (b) {
      b.addEventListener('click', function () {
        fuelMode = b.getAttribute('data-mode') || 'auto';
        each(document.querySelectorAll('.mode'), function (m) {
          if (m === b) m.classList.add('on');
          else m.classList.remove('on');
        });
        syncFuelPace();
      });
    });
    if ($('btnPaceSlow'))
      $('btnPaceSlow').addEventListener('click', function () {
        fuelPace = 'slow';
        syncFuelPace();
        paintFuel();
      });
    if ($('btnPaceFast'))
      $('btnPaceFast').addEventListener('click', function () {
        fuelPace = 'fast';
        syncFuelPace();
        paintFuel();
      });
    if ($('fuelSellerLab'))
      $('fuelSellerLab').addEventListener('click', function () {
        var sel = $('fuelSeller');
        if (sel && sel.focus) sel.focus();
        if (sel && sel.click) sel.click();
      });
    const hold = $('btnFuelHold');
    if (hold) {
      hold.addEventListener('click', function () {
        if (hold.disabled) return;
        var pump = (snapCache && snapCache.fuel_pump) || {};
        if (pump.active) return;
        void commitFuel();
      });
    }

    window.addEventListener('keydown', onKey);
    refresh();
    setInterval(function () {
      // A closed panel keeps running in MSFS; polling then only costs frames.
      if (document.hidden) return;
      void refresh();
    }, 1200);
  }

  return {
    start: start,
    applyAtcId: applyAtcId,
    onPanelActive: onPanelActive,
    // node tests only (backend/test/unit/panelLogic.test.ts)
    _bridge: bridge,
  };
})();

if (typeof TemplateElement !== 'undefined') {
  class IngamePanelFlyConomy extends TemplateElement {
    constructor() {
      super(...arguments);
      this.panelActive = false;
      this.ingameUi = null;
    }
    connectedCallback() {
      super.connectedCallback();
      var self = this;
      this.ingameUi = this.querySelector('ingame-ui');
      if (this.ingameUi) {
        this.ingameUi.addEventListener('panelActive', function () {
          self.panelActive = true;
          FlyConomyApp.start();
          if (FlyConomyApp.onPanelActive) FlyConomyApp.onPanelActive();
        });
        this.ingameUi.addEventListener('panelInactive', function () {
          self.panelActive = false;
        });
      }
      FlyConomyApp.start();
    }
    disconnectedCallback() {
      super.disconnectedCallback();
    }
  }
  window.customElements.define('ingamepanel-flyconomy', IngamePanelFlyConomy);
  if (typeof checkAutoload === 'function') checkAutoload();
} else if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', function () {
    FlyConomyApp.start();
  });
} else {
  FlyConomyApp.start();
}
