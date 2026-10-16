/* FlyConomy desktop shell – website content + My Flight SimConnect controls */
(function () {
  const SITE = 'https://flyconomy.de';
  const $ = (id) => document.getElementById(id);
  const api = () => window.pywebview && window.pywebview.api;

  let logs = [];
  let currentRoute = 'myflight';
  let accessToken = '';
  /** iframe under file:// uses a different storage partition than top-level OAuth */
  let siteSessionSeeded = false;
  let lastSitePath = '';
  let busy = false;
  let booted = false;
  let wired = false;

  function pathForRoute(route) {
    if (route === 'dashboard') return '/dashboard';
    return '/' + route;
  }

  function seedUrl(path, token) {
    return (
      SITE +
      '/desktop-auth#token=' +
      encodeURIComponent(token) +
      '&next=' +
      encodeURIComponent(path)
    );
  }

  function showGate(show) {
    $('loginGate').classList.toggle('hidden', !show);
    $('appShell').classList.toggle('locked', show);
    if (show) {
      const frame = $('siteFrame');
      if (frame) frame.src = 'about:blank';
      accessToken = '';
      siteSessionSeeded = false;
      lastSitePath = '';
    }
  }

  async function ensureToken() {
    if (accessToken && accessToken.length > 20) return accessToken;
    const snap = await call('get_status');
    accessToken = (snap && snap.access_token) || '';
    return accessToken;
  }

  async function loadSite(path, force) {
    const frame = $('siteFrame');
    if (!frame) return false;
    const token = await ensureToken();
    if (!token) {
      pushLog('Nicht angemeldet – bitte mit Discord anmelden.');
      showGate(true);
      return false;
    }
    if (!force && lastSitePath === path && siteSessionSeeded && frame.src && frame.src !== 'about:blank') {
      return true;
    }
    frame.src = seedUrl(path, token);
    siteSessionSeeded = true;
    lastSitePath = path;
    return true;
  }

  async function setRoute(route) {
    currentRoute = route;
    document.querySelectorAll('.nav-btn[data-route]').forEach((el) => {
      el.classList.toggle('active', el.dataset.route === route);
    });
    $('btnAccount').classList.toggle('active', route === 'account');

    const isFlight = route === 'myflight';
    const topbar = $('appTopbar');
    const ops = $('myflightOps');
    if (topbar) topbar.classList.toggle('hidden', !isFlight);
    // Ops deck lives on the website My Flight page (same look in client iframe).
    if (ops) ops.classList.add('hidden');

    document.querySelectorAll('.view').forEach((el) => el.classList.remove('active'));

    if (route === 'account') {
      $('view-account').classList.add('active');
      return;
    }

    $('view-site').classList.add('active');
    try {
      await loadSite(pathForRoute(route), false);
    } catch (e) {
      pushLog('Seite: ' + (e.message || e));
    }
  }

  function openLog() {
    // Console is a website popup now; keep buffer for bridge callers.
  }

  function pushLog(line) {
    logs.unshift(line);
    logs = logs.slice(0, 200);
  }

  function fmtHms(sec) {
    sec = Math.max(0, Math.floor(Number(sec) || 0));
    const h = String(Math.floor(sec / 3600)).padStart(2, '0');
    const m = String(Math.floor((sec % 3600) / 60)).padStart(2, '0');
    const s = String(sec % 60).padStart(2, '0');
    return h + ':' + m + ':' + s;
  }

  function render(snap) {
    if (!snap) return;
    const logged = !!snap.logged_in;
    showGate(!logged);
    if (!logged) return;

    if (snap.access_token) {
      accessToken = snap.access_token;
    }

    const user = snap.user || null;
    const name = (user && user.name) || 'Pilot';
    const initials = String(name).slice(0, 2).toUpperCase();

    $('railUser').textContent = String(name).slice(0, 8);
    $('accountName').textContent = name;
    $('accountSub').textContent = 'Angemeldet – gilt für App und Website';
    $('avatar').textContent = initials;

    const money = Math.round(Number((user && user.money) || 0));
    const bank = Math.round(Number((user && user.bank) || 0));
    const topName = $('topName');
    const topCash = $('topCash');
    const topBank = $('topBank');
    if (topName) topName.textContent = name;
    if (topCash) topCash.textContent = '$' + money.toLocaleString('en-US');
    if (topBank) topBank.textContent = '$' + bank.toLocaleString('en-US');

    const simOk = !!snap.sim_connected;
    const chipS = $('chipSim');
    if (chipS) {
      chipS.textContent = simOk ? 'MSFS verbunden' : 'MSFS getrennt';
      chipS.className = 'fc-pill ' + (simOk ? 'sim-on' : 'sim-off');
    }

    const inflight = !!snap.active_flight_id;
    const flightEl = $('statFlight');
    if (flightEl) {
      if (inflight) {
        let label = 'Flug aktiv · ' + fmtHms(snap.flight_elapsed_sec);
        if (snap.hours_remaining != null && snap.hours_remaining !== '') {
          label += ' · ' + Number(snap.hours_remaining).toFixed(1) + 'h';
        }
        flightEl.textContent = label;
      } else {
        flightEl.textContent = 'Kein Flug';
      }
      flightEl.className = 'fc-pill' + (inflight ? ' flying' : '');
    }
    const acEl = $('statAircraft');
    if (acEl) {
      acEl.textContent = inflight ? snap.aircraft_label || snap.registration || '' : '';
      acEl.classList.toggle('hidden', !inflight || !acEl.textContent);
    }

    const simTitle = $('simTitle');
    if (simTitle) {
      const t = snap.sim_title || (snap.last_sample && snap.last_sample.aircraft_type) || '';
      simTitle.textContent = t || (simOk ? 'Titel wird gelesen…' : 'MSFS nicht verbunden');
    }
    const simIcao = $('simIcao');
    if (simIcao) {
      const icao = snap.current_icao || '';
      const name = snap.current_airport_name || '';
      simIcao.textContent = icao ? icao + (name ? ' · ' + name : '') : simOk ? 'kein Airport in der Nähe' : '—';
    }
    const btnLivery = $('btnLivery');
    if (btnLivery) {
      btnLivery.disabled = busy || !logged || !simOk || !(snap.sim_title || (snap.last_sample && snap.last_sample.aircraft_type));
    }

    const alert = $('alertBox');
    if (alert) {
      const err = snap.last_error;
      if (err) {
        alert.textContent = err;
        alert.classList.remove('hidden');
      } else if (!busy) {
        alert.textContent = '';
        alert.classList.add('hidden');
      }
    }
    const btnConnect = $('btnConnect');
    const btnStart = $('btnStart');
    const btnCancel = $('btnCancel');
    const btnLogout = $('btnLogout');
    if (btnConnect) btnConnect.disabled = busy;
    if (btnStart) btnStart.disabled = busy || !logged || inflight || !!snap.crew_guest;
    if (btnCancel) btnCancel.disabled = busy || !logged || !inflight;
    if (btnLogout) btnLogout.disabled = !logged || busy;

    if (btnConnect) btnConnect.classList.toggle('needs', !simOk);
    if (btnStart) btnStart.classList.toggle('needs', logged && !inflight);

    const ver = $('loginVer');
    if (ver && snap.client_version) ver.textContent = 'v' + snap.client_version;

    const hint = $('flightHint');
    if (hint && !busy) {
      if (snap.last_error) {
        hint.textContent = 'Siehe Meldung unten.';
      } else if (inflight && !simOk) {
        hint.textContent = 'Offener Flug – am Ziel Handbremse oder „Abbrechen“.';
      } else if (!simOk) {
        hint.textContent = 'Zuerst MSFS starten, dann „MSFS verbinden“ oder direkt „Flug starten“.';
      } else if (inflight) {
        hint.textContent = 'Flug läuft – am Ziel Handbremse setzen (stehen). Airliner: Fenix oft ≤8 kt.';
      } else {
        hint.textContent = 'Bereit. Aufträge laden, dann „Flug starten“.';
      }
    }
  }

  async function call(method, args) {
    const a = api();
    if (!a || !a[method]) throw new Error('Bridge nicht bereit');
    return a[method].apply(a, args || []);
  }

  async function refresh() {
    try {
      const snap = await call('get_status');
      render(snap);
      const lines = await call('get_logs');
      if (Array.isArray(lines) && lines.length) {
        logs = lines.slice().reverse();
        const box = $('logBox');
        if (box) box.textContent = logs.join('\n');
      }
    } catch (e) {}
  }

  async function safe(label, fn) {
    if (busy) return;
    busy = true;
    openLog();
    pushLog(label + '…');
    const hint = $('flightHint');
    if (hint) hint.textContent = label + ' läuft… bitte warten.';
    const btnStart = $('btnStart');
    const btnCancel = $('btnCancel');
    if (btnStart) btnStart.disabled = true;
    if (btnCancel) btnCancel.disabled = true;
    try {
      const res = await fn();
      if (res && res.message) pushLog(res.message);
      return res;
    } catch (e) {
      const msg = String(e.message || e);
      pushLog(label + ': ' + msg);
      openLog();
      if (hint) hint.textContent = 'Fehler – siehe rote Meldung.';
      const alert = $('alertBox');
      if (alert) {
        alert.textContent = msg;
        alert.classList.remove('hidden');
      }
    } finally {
      busy = false;
      await refresh();
    }
  }

  function wire() {
    if (wired) return;
    wired = true;
    document.querySelectorAll('.nav-btn[data-route]').forEach((btn) => {
      btn.addEventListener('click', () => {
        setRoute(btn.dataset.route);
      });
    });
    $('btnAccount').onclick = () => setRoute('account');
    const btnWeb = $('btnWebsite');
    if (btnWeb) {
      btnWeb.onclick = () => {
        call('open_website').catch(() => {
          window.open('https://flyconomy.de', '_blank');
        });
      };
    }
    $('btnConnect').onclick = () => safe('MSFS', () => call('connect_sim'));
    $('btnStart').onclick = () => safe('Start', () => call('start_flight'));
    $('btnCancel').onclick = () => safe('Abbruch', () => call('cancel_flight'));
    const btnLivery = $('btnLivery');
    if (btnLivery) {
      btnLivery.onclick = () => safe('Livery', () => call('request_livery'));
    }
    $('btnGateLogin').onclick = () => safe('Login', () => call('start_web_login'));
    const btnLog = $('btnToggleLog');
    if (btnLog) {
      btnLog.onclick = () => {
        const box = $('logBox');
        box.classList.toggle('collapsed');
        box.classList.toggle('open');
      };
    }
    const btnSettings = $('btnOpenSettingsHint');
    if (btnSettings) {
      btnSettings.onclick = () => setRoute('myflight');
    }
    $('btnLogout').onclick = () =>
      safe('Logout', async () => {
        const res = await call('logout');
        siteSessionSeeded = false;
        accessToken = '';
        lastSitePath = '';
        setRoute('myflight');
        return res;
      });
  }

  function boot() {
    if (booted) return;
    booted = true;
    wire();
    setRoute('myflight');
    refresh();
    setInterval(refresh, 1500);
    call('bootstrap').then(refresh).catch(() => {});
  }

  window.addEventListener('pywebviewready', boot);
  if (window.pywebview && window.pywebview.api) boot();
  else wire();
})();
