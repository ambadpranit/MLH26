/* ExamRadar — reminders.
   This is the piece that was missing: nothing used to compare the calendar with the clock, so a quiz that was
   found in an email was only ever *displayed*, never *announced*.

   How it works
   - check() looks at every event, works out which "stage" it is in (3 days / tomorrow / today / 1 hour) and shows a
     reminder if that stage has not been announced yet. What was announced is remembered in localStorage, so a
     reload never repeats a reminder and a missed one is still shown when the app is next opened.
   - check() runs: on start, after every import, every 30 s, when the tab becomes visible again, and when the
     window regains focus (a laptop that slept would otherwise skip ahead).
   - Reminders show as in-page toast cards (always) and as desktop notifications (if the user switched them on
     with the bell button and the window is not in front).
   Limit: it runs while ExamRadar is open (browser tab or `python app.py --app` window). There is no server push.
   The .ics download still carries its own alarm for when the app is closed.
   Email text is untrusted: everything is written with textContent, never innerHTML. */
(function () {
  'use strict';

  const STORE_KEY = 'examradar:reminders';
  const DESKTOP_KEY = 'examradar:desktop-alerts';
  const HOUR = 3600e3, DAY = 86400e3;
  const maxVisible = () => (window.innerWidth < 560 ? 2 : 3);   // more than this at once becomes one summary card
  const SNOOZE_MS = HOUR;
  const REMIND_KINDS = new Set(['exam', 'deadline']);
  const QUIZ_WORDS = /\b(quiz(?:zes)?|test|viva|exam(?:ination)?s?|assessment)\b/i;   // a quiz the model filed as "event"/"other"
  const RANK = { d3: 1, d1: 2, today: 3, h1: 4 };

  let cfg = null;                              // set by init()
  const shown = new Map();                     // key -> toast node currently on screen

  /* ------------------------------------------------------------ small helpers */
  const pad = (n) => String(n).padStart(2, '0');
  const dayKey = (d) => d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate());
  const midnight = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate());
  const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text !== undefined) n.textContent = text; return n; };
  const store = {
    get() { try { return JSON.parse(localStorage.getItem(STORE_KEY) || 'null') || { seen: {}, snooze: {} }; } catch (e) { return { seen: {}, snooze: {} }; } },
    set(v) { try { localStorage.setItem(STORE_KEY, JSON.stringify(v)); } catch (e) { /* blocked or full: reminders still show, they may repeat after a reload */ } }
  };

  const wants = (ev) => !!ev && !!ev.date && (REMIND_KINDS.has(ev.kind) || QUIZ_WORDS.test(ev.title || ''));
  const evKey = (ev) => ev.date + '|' + (ev.start || '') + '|' + String(ev.title || '').toLowerCase().trim();
  function startOf(ev) {
    const [y, mo, d] = ev.date.split('-').map(Number);
    const [h, mi] = ev.start ? ev.start.split(':').map(Number) : [0, 0];
    return new Date(y, mo - 1, d, h, mi);
  }

  /* ------------------------------------------------------------ pure logic (unit-testable) */
  // Which stage is this event in right now? null = nothing to announce (too far away, or already started).
  function stageFor(ev, now) {
    const days = Math.round((midnight(startOf({ date: ev.date })) - midnight(now)) / DAY);   // calendar days, DST-safe
    if (days < 0) return null;
    if (ev.start) {
      const ms = startOf(ev) - now;
      if (ms <= 0) return null;                                  // already started
      if (ms <= HOUR) return 'h1';
    } else if (days === 0) {
      return 'today';                                            // all-day event happening today
    }
    if (days === 0) return 'today';
    if (days === 1) return 'd1';
    if (days <= 3) return 'd3';
    return null;
  }

  function whenText(ev, stage, now) {
    const at = ev.start ? ' at ' + ev.start : '';
    if (stage === 'h1') { const m = Math.max(1, Math.ceil((startOf(ev) - now) / 60000)); return 'Starts in ' + m + ' min'; }
    if (stage === 'today') return 'Today' + at;
    if (stage === 'd1') return 'Tomorrow' + at;
    const days = Math.round((midnight(startOf({ date: ev.date })) - midnight(now)) / DAY);
    return 'In ' + days + ' days' + at;
  }

  // Everything that should be announced now. Pure: gets the state in, returns the list, changes nothing.
  function due(events, now, state) {
    const out = [];
    (events || []).forEach((ev, index) => {
      if (!wants(ev)) return;
      const stage = stageFor(ev, now); if (!stage) return;
      const key = evKey(ev);
      if ((state.seen[key] || 0) >= RANK[stage]) return;          // this stage (or a later one) was already announced
      if ((state.snooze[key] || 0) > now.getTime()) return;
      out.push({ key, index, ev, stage, rank: RANK[stage], when: whenText(ev, stage, now) });
    });
    return out.sort((a, b) => startOf(a.ev) - startOf(b.ev));
  }

  function prune(state, now) {                                    // forget events from more than two weeks ago
    const cutoff = dayKey(new Date(now.getTime() - 14 * DAY));
    ['seen', 'snooze'].forEach((k) => Object.keys(state[k]).forEach((key) => { if (key.slice(0, 10) < cutoff) delete state[k][key]; }));
  }

  /* ------------------------------------------------------------ desktop notifications */
  const supported = () => typeof Notification !== 'undefined';
  const desktopOn = () => supported() && Notification.permission === 'granted' && localStorage.getItem(DESKTOP_KEY) === '1';

  async function desktopNotify(title, body, tag) {
    if (!desktopOn()) return;
    if (document.hasFocus() && !document.hidden) return;          // the toast is enough while the window is in front
    const opts = { body, tag, icon: '/icons/icon-192.png', badge: '/icons/icon-192.png', renotify: false };
    try {
      const reg = navigator.serviceWorker && await navigator.serviceWorker.getRegistration();
      if (reg && reg.showNotification) { await reg.showNotification(title, opts); return; }
    } catch (e) { /* fall through to the plain constructor */ }
    try { new Notification(title, opts); } catch (e) { /* some browsers only allow the service-worker route */ }
  }

  /* ------------------------------------------------------------ toast card */
  const kindLabel = (ev) => (cfg.kindLabel ? cfg.kindLabel(ev) : ev.kind);

  function drop(node) {
    node.classList.add('leaving');
    setTimeout(() => node.remove(), 220);
    for (const [k, v] of shown) if (v === node) shown.delete(k);
  }

  function card(r) {
    const ev = r.ev;
    const n = el('div', 'rtoast k-' + (ev.kind || 'other') + (r.stage === 'h1' || r.stage === 'today' ? ' urgent' : ''));
    n.setAttribute('role', r.stage === 'h1' ? 'alert' : 'status');
    n.append(el('i', 'rt-bar'));
    const body = el('div', 'rt-body');
    const top = el('div', 'rt-top');
    top.append(el('span', 'rt-kind', kindLabel(ev) + (cfg.subjectOf ? ' · ' + cfg.subjectOf(ev) : '')));
    const x = el('button', 'rt-x', '×'); x.type = 'button'; x.setAttribute('aria-label', 'Dismiss reminder');
    x.onclick = () => drop(n);
    top.append(x);
    body.append(top, el('div', 'rt-title', ev.title), el('div', 'rt-when', r.when));
    const meta = [ev.start ? (ev.end ? ev.start + '–' + ev.end : ev.start) : 'All day', ev.venue].filter(Boolean).join(' · ');
    if (meta) body.append(el('div', 'rt-meta', meta));
    const acts = el('div', 'rt-actions');
    const view = el('button', 'rt-btn primary', 'View'); view.type = 'button';
    view.onclick = () => { drop(n); cfg.open(r.index); };
    const snz = el('button', 'rt-btn', 'Snooze 1 h'); snz.type = 'button';
    snz.onclick = () => { snooze(r.key); drop(n); };
    acts.append(view, snz);
    body.append(acts);
    n.append(body);
    return n;
  }

  function summaryCard(count, onView) {
    const n = el('div', 'rtoast summary'); n.setAttribute('role', 'status'); n.append(el('i', 'rt-bar'));
    const body = el('div', 'rt-body');
    const top = el('div', 'rt-top'); top.append(el('span', 'rt-kind', 'Reminders'));
    const x = el('button', 'rt-x', '×'); x.type = 'button'; x.setAttribute('aria-label', 'Dismiss'); x.onclick = () => drop(n); top.append(x);
    body.append(top, el('div', 'rt-title', '+' + count + ' more coming up'));
    const acts = el('div', 'rt-actions'); const v = el('button', 'rt-btn primary', 'Open schedule'); v.type = 'button';
    v.onclick = () => { drop(n); onView(); }; acts.append(v); body.append(acts); n.append(body);
    return n;
  }

  function snooze(key) {
    const st = store.get(); st.snooze[key] = Date.now() + SNOOZE_MS; store.set(st);
  }

  /* ------------------------------------------------------------ public: check */
  function check(opts) {
    if (!cfg) return [];
    const now = (opts && opts.now) || new Date();
    const state = store.get();
    prune(state, now);
    const list = due(cfg.getEvents(), now, state);
    if (!list.length) { store.set(state); return []; }

    const host = document.getElementById('toasts');
    list.forEach((r) => { state.seen[r.key] = Math.max(state.seen[r.key] || 0, r.rank); });   // mark all, even those folded into the summary
    store.set(state);

    const visible = list.slice(0, maxVisible()), rest = list.length - visible.length;
    visible.forEach((r) => {
      const old = shown.get(r.key); if (old) old.remove();                // a newer stage replaces the old card
      const node = card(r); shown.set(r.key, node); host.appendChild(node);
      desktopNotify(r.ev.title, r.when + (r.ev.venue ? ' · ' + r.ev.venue : ''), r.key);
    });
    if (rest > 0) {
      host.appendChild(summaryCard(rest, cfg.openSchedule));
      desktopNotify('ExamRadar', list.length + ' reminders coming up', 'examradar-summary');
    }
    return list;
  }

  /* ------------------------------------------------------------ bell button */
  function paintBell() {
    const b = cfg && cfg.button; if (!b) return;
    const on = desktopOn();
    b.setAttribute('aria-pressed', String(on));
    b.classList.toggle('on', on);
    b.title = !supported() ? 'Desktop alerts are not supported in this browser (in-app reminders still work)'
      : Notification.permission === 'denied' ? 'Desktop alerts are blocked in your browser settings'
      : on ? 'Desktop alerts are on. Click to turn off' : 'Turn on desktop alerts';
    b.setAttribute('aria-label', b.title);
  }

  async function onBell() {
    if (!supported()) { cfg.toast('Desktop alerts are not supported here. Reminders still pop up inside ExamRadar.', 'warn'); return; }
    if (Notification.permission === 'denied') { cfg.toast('Notifications are blocked for this site. Allow them in the browser’s site settings, then click the bell again.', 'warn'); return; }
    if (Notification.permission === 'default') {
      let res = 'denied'; try { res = await Notification.requestPermission(); } catch (e) { /* old Safari callback API */ }
      if (res !== 'granted') { paintBell(); cfg.toast('Desktop alerts stay off. In-app reminders still work.', 'warn'); return; }
      localStorage.setItem(DESKTOP_KEY, '1'); paintBell();
      cfg.toast('Desktop alerts on. You will be told when an exam or quiz is close.', 'accent'); return;
    }
    const next = localStorage.getItem(DESKTOP_KEY) === '1' ? '0' : '1';
    try { localStorage.setItem(DESKTOP_KEY, next); } catch (e) { /* ignore */ }
    paintBell(); cfg.toast(next === '1' ? 'Desktop alerts on.' : 'Desktop alerts off.', next === '1' ? 'accent' : 'ok');
  }

  /* ------------------------------------------------------------ public: init + debug helpers */
  function init(config) {
    cfg = config;
    if (cfg.button) { cfg.button.onclick = onBell; paintBell(); }
    document.addEventListener('visibilitychange', () => { if (!document.hidden) check(); });
    window.addEventListener('focus', () => check());
    if (navigator.serviceWorker) navigator.serviceWorker.addEventListener('message', (e) => {
      if (e.data && e.data.type === 'open-schedule') cfg.openSchedule();
    });
  }

  window.ExamReminders = {
    init, check, paintBell,
    // internals, exposed for tests and for debugging from the browser console
    _stageFor: stageFor, _due: due, _wants: wants,
    // Console helper: what does the engine think of every event right now?
    debug() {
      const now = new Date(), st = store.get();
      const rows = (cfg ? cfg.getEvents() : []).map((ev) => {
        const key = evKey(ev), stage = wants(ev) ? stageFor(ev, now) : null;
        return { title: ev.title, date: ev.date, start: ev.start || '', kind: ev.kind, tracked: wants(ev), stage: stage || '-',
          announced: Object.keys(RANK).find((s) => RANK[s] === st.seen[key]) || '-', snoozed: (st.snooze[key] || 0) > now.getTime() };
      });
      if (console.table) console.table(rows);
      return rows;
    },
    reset() { try { localStorage.removeItem(STORE_KEY); } catch (e) { /* ignore */ } return check(); }   // forget what was announced and announce again
  };
})();
