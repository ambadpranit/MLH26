/* ExamRadar 2.0 — frontend. Plain JavaScript, no build step, no network except this app's own server.
   Sections: utils · dates · kinds/subjects · state · UI helpers · theme · data flow · drawing · dialogs · events · init */
'use strict';

/* ================================================================== utils */
const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => {                 // always textContent: email text is untrusted, never innerHTML
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
};
const SVGNS = 'http://www.w3.org/2000/svg';
const svgEl = (tag, attrs) => {
  const n = document.createElementNS(SVGNS, tag);
  for (const k in attrs) n.setAttribute(k, attrs[k]);
  return n;
};
const icon = (id) => { const s = svgEl('svg', { class: 'ico' }); s.appendChild(svgEl('use', { href: '#' + id })); return s; };
const store = {                                  // localStorage can be blocked: never let it break the page
  get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* quota or blocked */ } },
  del(k) { try { localStorage.removeItem(k); } catch (e) { /* ignore */ } }
};
const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
const plural = (n, w) => n + ' ' + w + (n === 1 ? '' : 's');

/* ================================================================== dates */
const pad = (n) => String(n).padStart(2, '0');
const key = (d) => d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate());
const parseKey = (k) => new Date(k + 'T00:00:00');
const todayKey = () => key(new Date());
const addDays = (d, n) => { const x = new Date(d); x.setDate(x.getDate() + n); return x; };
const monthStart = (d) => new Date(d.getFullYear(), d.getMonth(), 1);
const fmtDate = (k, long) => parseKey(k).toLocaleDateString('en-GB', long
  ? { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' }
  : { weekday: 'short', day: 'numeric', month: 'short' });
const fmtRange = (ev) => ev.start ? (ev.end ? ev.start + '–' + ev.end : ev.start) : 'All day';
const daysUntil = (k) => Math.round((parseKey(k) - parseKey(todayKey())) / 86400000);
const relDays = (n) => n === 0 ? 'today' : n === 1 ? 'tomorrow' : n === -1 ? 'yesterday' : n > 0 ? 'in ' + n + ' days' : Math.abs(n) + ' days ago';
const WEEK_START = 0;                            // 0 = Sunday first, 1 = Monday first
const DOW = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
function startOf(ev) {                           // the moment an event starts (local time, like the .ics file)
  const [y, mo, d] = ev.date.split('-').map(Number);
  const [h, mi] = ev.start ? ev.start.split(':').map(Number) : [0, 0];
  return new Date(y, mo - 1, d, h, mi);
}

/* ================================================================== kinds + subjects */
const KINDS = {
  exam:     { label: 'Exam' },
  deadline: { label: 'Deadline' },
  event:    { label: 'Event' },
  meeting:  { label: 'Meeting' },
  other:    { label: 'Other' }
};
const kindOf = (ev) => KINDS[ev.kind] ? ev.kind : 'other';
const kindColor = (k) => 'var(--k-' + k + ')';

// "Data Structures exam" -> "Data Structures",  "DBMS assignment submission" -> "DBMS".
// Anything that is not an exam or coursework deadline goes under one "Events & meetings" entry.
const NOISE = /\b(final|end|mid|semester|sem|exams?|examinations?|tests?|quiz(?:zes)?|viva(?:\s*voce)?|assignments?|submissions?|deadlines?|due|lab|practicals?|internals?|schedules?|date\s*sheet|class(?:es)?|lecture|registration|last\s+date|unit|paper|re-?exam)\b/gi;
const COURSEWORK = /\b(exams?|examinations?|tests?|quiz(?:zes)?|viva|assignments?|submissions?|lab|practicals?|internals?|paper|project|report)\b/i;
const OTHER_SUBJECT = 'Events & meetings';
function subjectName(ev) {
  if (ev.kind !== 'exam' && ev.kind !== 'deadline') return OTHER_SUBJECT;
  if (ev.kind === 'deadline' && !COURSEWORK.test(ev.title || '')) return OTHER_SUBJECT;
  const cleaned = (ev.title || '').replace(NOISE, ' ').replace(/[\s\-–—:,()]+/g, ' ')
    .replace(/^(of|for|the|and)\s+|\s+(of|for|the|and)$/gi, '').trim();
  return cleaned || (ev.title || 'General').trim() || 'General';
}
const subjectKey = (ev) => subjectName(ev).toLowerCase();
const hueOf = (str) => { let h = 7; for (const c of str) h = (h * 31 + c.charCodeAt(0)) % 360; return h; };
const subjectColor = (k) => 'hsl(' + hueOf(k) + (theme === 'minimal' ? ' 42% 72%)' : ' 78% 60%)');   // pastel in the Minimal theme
const colorOf = (ev) => S.colorBy === 'subject' ? subjectColor(subjectKey(ev)) : kindColor(kindOf(ev));

/* ================================================================== state */
const S = {
  events: [], clashes: [], review: [], level: {}, ics: '', stats: null,
  view: 'month', colorBy: 'kind', subject: null, tab: 'cal',
  cursor: monthStart(new Date()), selected: todayKey(), hidden: new Set(),
  query: '', upcomingOnly: false
};

function subjectList() {
  const map = new Map(), t = todayKey();
  S.events.forEach((ev, i) => {
    const k = subjectKey(ev);
    if (!map.has(k)) map.set(k, { key: k, name: subjectName(ev), items: [] });
    map.get(k).items.push(i);
  });
  return [...map.values()].map((g) => {
    const next = g.items.map((i) => S.events[i]).filter((e) => e.date >= t).sort((a, b) => a.date.localeCompare(b.date))[0];
    return { ...g, count: g.items.length, next };
  }).sort((a, b) => (a.next ? a.next.date : '9999').localeCompare(b.next ? b.next.date : '9999') || a.name.localeCompare(b.name));
}
// events that pass the subject filter and the type chips (indices point into S.events so clash data stays valid)
const visible = () => S.events.map((ev, i) => ({ ev, i }))
  .filter(({ ev }) => !S.hidden.has(kindOf(ev)) && (!S.subject || subjectKey(ev) === S.subject));
function byDate() {
  const map = {};
  visible().forEach((x) => { (map[x.ev.date] ||= []).push(x); });
  return map;
}
const levelClass = (i) => S.level[i] === 'clash' ? ' is-clash' : S.level[i] ? ' is-maybe' : '';
const mark = (i) => S.level[i] === 'clash' ? '⚠ ' : S.level[i] ? '? ' : '';

/* ================================================================== UI helpers */
function toast(text, kind) {
  const t = el('div', 'alert toast t-' + (kind || 'ok'));
  t.setAttribute('role', 'status');
  t.appendChild(el('div', 'grow', text));
  $('toasts').appendChild(t);
  setTimeout(() => t.remove(), 4500);
}
function showAlert(box, msg, kind) {
  box.hidden = false; box.replaceChildren();
  const a = el('div', 'alert t-' + (kind || 'danger'));
  a.setAttribute('role', 'alert');
  a.appendChild(el('div', 'grow', msg));
  box.appendChild(a);
}
const hideAlert = (box) => { box.hidden = true; box.replaceChildren(); };
async function getJson(url) {
  const r = await fetch(url);
  if (!r.ok && r.status !== 404) throw new Error('Server error ' + r.status);
  return r.json();
}
async function postJson(url, body) {
  const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  return r.json();
}
function animateNumber(node, to) {
  const from = Number(node.dataset.v || 0);
  node.dataset.v = to;
  if (reduceMotion || from === to) { node.textContent = to; return; }
  const t0 = performance.now(), dur = 600;
  const step = (now) => {
    const p = Math.min(1, (now - t0) / dur), e = 1 - Math.pow(1 - p, 3);
    node.textContent = Math.round(from + (to - from) * e);
    if (p < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}
const openImport = () => $('impModal').showModal();
const dialogs = ['impModal', 'evModal', 'helpModal'];
dialogs.forEach((id) => {                        // click on the dimmed backdrop closes a dialog
  const d = $(id);
  d.addEventListener('click', (e) => { if (e.target === d) d.close(); });
});
$('impModal').addEventListener('close', () => { $('gPass').value = ''; });   // a password never lingers

/* ================================================================== theme */
const THEMES = [['minimal', 'Minimal', '#dfe1e6', '#f0b7c3'], ['system', 'System', '#8e98c4', '#e8ecff'], ['neon', 'Neon', '#22e5ff', '#ff3df0'], ['aurora', 'Aurora', '#5dffb8', '#b06cff'],
  ['sunset', 'Sunset', '#ff8a3d', '#ff3d6e'], ['paper', 'Paper', '#5b3df5', '#ff3d81']];
const THEME_BG = { minimal: '#f7f7f5', system: '#070912', neon: '#070912', aurora: '#0a0618', sunset: '#130806', paper: '#eef1fb' };
let theme = 'minimal';
function applyTheme(t) {
  theme = THEMES.some((x) => x[0] === t) ? t : 'minimal';   // unknown saved themes fall back to Minimal
  if (theme === 'system') document.documentElement.removeAttribute('data-theme'); else document.documentElement.setAttribute('data-theme', theme);
  store.set('examradar:theme', theme);
  document.querySelector('meta[name="theme-color"]').content = THEME_BG[theme];
  drawThemeMenu();
  if (S.events.length) drawAll();               // subject colours differ per theme (pastel in Minimal)
}
function drawThemeMenu() {
  const m = $('themeMenu'); m.replaceChildren();
  THEMES.forEach(([v, name, a, b]) => {
    const btn = el('button', v === theme ? 'on' : ''); btn.type = 'button'; btn.setAttribute('role', 'menuitemradio');
    btn.setAttribute('aria-checked', String(v === theme));
    const sw = el('span', 'sw'); sw.style.setProperty('--a', a); sw.style.setProperty('--b', b);
    btn.append(sw, el('span', '', name));
    btn.onclick = () => { applyTheme(v); toggleThemeMenu(false); };
    m.appendChild(btn);
  });
}
function toggleThemeMenu(on) {
  $('themeMenu').hidden = !on; $('themeBtn').setAttribute('aria-expanded', String(on));
}
$('themeBtn').onclick = (e) => { e.stopPropagation(); toggleThemeMenu($('themeMenu').hidden); };
document.addEventListener('click', (e) => { if (!$('themeMenu').contains(e.target)) toggleThemeMenu(false); });

/* ================================================================== mobile drawer */
function setDrawer(on) {
  $('side').classList.toggle('open', on); $('scrim').classList.toggle('open', on);
  $('menuBtn').setAttribute('aria-expanded', String(on));
}
$('menuBtn').onclick = () => setDrawer(!$('side').classList.contains('open'));
$('scrim').onclick = () => setDrawer(false);

/* ================================================================== Ollama models */
let preferredModel = '';
async function loadModels(preferred) {
  const sel = $('model'), note = $('ollama');
  sel.replaceChildren();
  let data;
  try { data = await getJson('/api/models'); }
  catch (e) { data = { ok: false, models: [], error: 'Could not reach the ExamRadar server.' }; }
  const names = (data.models || []).slice();
  const chosen = names.includes(preferred) ? preferred : (names[0] || preferred);
  if (chosen && !names.includes(chosen)) names.unshift(chosen);
  names.forEach((n) => { const o = el('option', '', n); o.value = n; sel.appendChild(o); });
  if (chosen) sel.value = chosen;
  const set = (text, kind) => {
    note.replaceChildren();
    const dot = el('span', 'status-dot'); dot.style.setProperty('--c', 'var(--' + kind + ')');
    note.append(dot, document.createTextNode(text));
  };
  if (!data.ok) set(data.error || 'Ollama is not reachable.', 'danger');
  else if (!names.length) set('Ollama is running but has no models yet. Run: ollama pull ' + preferred, 'warn');
  else if (preferred && chosen !== preferred) set('“' + preferred + '” is not installed, so ' + chosen + ' is selected.', 'warn');
  else set('Ollama is running here (' + plural(data.models.length, 'model') + ')', 'ok');
}
$('refresh').onclick = () => loadModels($('model').value || preferredModel);

/* ================================================================== extraction (streamed from the server) */
let abortCtl = null, busyTimer = null;
function setBusy(on) {
  ['go', 'gRun', 'gFetch'].forEach((id) => { $(id).disabled = on; });
  $('go').textContent = on ? 'Working…' : 'Find my exams';
  $('busy').hidden = !on;
  if (!on && busyTimer) { clearInterval(busyTimer); busyTimer = null; }
  if (!on) updateGmailCount();
}
async function run(payload) {
  hideAlert($('err'));
  $('impModal').close();
  const t0 = performance.now();
  let progress = 'Starting…', finished = false, failed = false;
  const tick = () => { $('status').textContent = progress + ' · ' + Math.round((performance.now() - t0) / 1000) + ' s'; };
  const bar = $('bar'), wrap = $('barWrap');
  wrap.classList.add('indet'); bar.style.width = '';
  abortCtl = new AbortController();
  setBusy(true); tick(); busyTimer = setInterval(tick, 500);
  $('busy').scrollIntoView({ behavior: reduceMotion ? 'auto' : 'smooth', block: 'nearest' });
  try {
    const res = await fetch('/api/extract', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, signal: abortCtl.signal,
      body: JSON.stringify({ ...payload, model: $('model').value, sent: $('sent').value })
    });
    if (!res.ok || !res.body) throw new Error('Server error ' + res.status);
    const reader = res.body.getReader(), dec = new TextDecoder();
    let buf = '';
    const handle = (line) => {
      let msg;
      try { msg = JSON.parse(line); } catch (e) { return; }          // ignore a garbled line, keep reading
      if (msg.type === 'progress') {
        if (msg.total && msg.done > 0) { wrap.classList.remove('indet'); bar.style.width = (100 * msg.done / msg.total) + '%'; }
        progress = msg.done === 0 ? 'Reading ' + plural(msg.total, 'email') + ' · the first is slowest while the model loads'
                                  : 'Read ' + msg.done + ' of ' + msg.total + ' emails';
        tick();
      } else if (msg.type === 'error') { failed = true; showAlert($('err'), msg.message); }
      else if (msg.type === 'done') { finished = true; showResults(msg); }
    };
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let nl;
      while ((nl = buf.indexOf('\n')) >= 0) {
        const line = buf.slice(0, nl).trim(); buf = buf.slice(nl + 1);
        if (line) handle(line);
      }
    }
    if (buf.trim()) handle(buf.trim());
    if (!finished && !failed) showAlert($('err'), 'The server stopped before it finished. Is Ollama still running?');
  } catch (e) {
    if (e.name === 'AbortError') toast('Cancelled. The email being read may still finish in the background.', 'warn');
    else showAlert($('err'), String(e.message || e));
  } finally { abortCtl = null; setBusy(false); }
}
$('cancelRun').onclick = () => { if (abortCtl) abortCtl.abort(); };

/* ================================================================== results <-> state */
function focusDate(events) {
  const t = todayKey(), first = events.find((e) => e.date >= t) || events[0];
  return first ? first.date : t;
}
function levelsFrom(clashes) {
  const level = {};
  clashes.forEach((c) => [c.a, c.b].forEach((k) => { if (level[k] !== 'clash') level[k] = c.level; }));
  return level;
}
function loadState(msg) {
  S.events = (msg.events || []).filter((e) => e && e.date);   // an event without a date can never be placed
  S.clashes = msg.clashes || []; S.review = msg.review || [];
  S.ics = msg.ics || ''; S.stats = msg.stats || null; S.level = levelsFrom(S.clashes); S.subject = null;
  S.selected = focusDate(S.events); S.cursor = monthStart(parseKey(S.selected));
  S.sDay = null; S.sCursor = monthStart(parseKey(S.selected));
}
function showResults(msg) {
  loadState(msg);
  store.set('examradar:last', JSON.stringify({ events: S.events, clashes: S.clashes, review: S.review, ics: S.ics, stats: S.stats }));
  drawAll(); setTab('schedule');
  const n = msg.stats ? msg.stats.emails : 0;
  if (!n) showAlert($('err'), 'No emails found to read. Paste some text or pick emails from Gmail first.', 'warn');
  else if (!S.events.length && !S.review.length) showAlert($('err'), 'No exam or event dates were found in these emails.', 'warn');
  else toast(plural(S.events.length, 'event') + ' added to the calendar' + (S.review.length ? ', ' + S.review.length + ' to check' : ''));
  window.scrollTo({ top: 0, behavior: reduceMotion ? 'auto' : 'smooth' });
  ExamReminders.check();                       // a quiz that is already close is announced right after the import
}

/* ================================================================== drawing */
function drawAll() {
  drawStats(); drawNext(); drawRadar(); drawClashes(); drawSubjects(); drawLegend(); drawSchedule(); drawCalendar(); drawAgenda(); drawReview();
  $('dl').disabled = !S.events.length;
}

function drawStats() {
  const sure = S.clashes.filter((c) => c.level === 'clash').length, maybe = S.clashes.length - sure, s = S.stats;
  animateNumber($('stEvents'), S.events.length);
  animateNumber($('stClash'), sure);
  $('stClashBox').style.setProperty('--c', sure ? 'var(--danger)' : 'var(--ok)');
  $('stClash').style.color = sure ? 'var(--danger)' : 'var(--ok)';
  $('stClashDesc').textContent = maybe ? maybe + ' more possible' : 'same-day overlaps';
  animateNumber($('stReview'), S.review.length);
  $('stReviewBox').style.setProperty('--c', S.review.length ? 'var(--warn)' : 'var(--ok)');
  $('stReview').style.color = S.review.length ? 'var(--warn)' : 'var(--ok)';
  $('stEmails').textContent = s ? s.emails : '–';
  $('stEmailsDesc').textContent = s ? s.skipped + ' skipped · ' + s.model + (s.seconds ? ' · ' + s.seconds.toFixed(1) + ' s' : '') : 'paste or connect Gmail';
  ['reviewCount', 'reviewCountM'].forEach((id) => { $(id).textContent = S.review.length; $(id).hidden = !S.review.length; });
}

/* ---- "next up" hero with a live countdown */
function nextEvent() {
  const now = Date.now(), list = visible().filter(({ ev }) => ev.date >= todayKey());
  const upcoming = list.filter(({ ev }) => startOf(ev).getTime() >= now || ev.date === todayKey());
  return upcoming.find(({ ev }) => ev.kind === 'exam') || upcoming[0] || null;
}
function drawNext() {
  const box = $('next'); box.replaceChildren();
  const x = nextEvent();
  if (!x) {
    box.style.removeProperty('--c');
    box.className = 'glass next-empty';
    box.appendChild(el('h2', '', S.events.length ? 'Nothing coming up' : 'All clear'));
    box.appendChild(el('p', '', S.events.length ? 'No upcoming events match the current filters.' : 'Import some emails and your next exam appears here with a live countdown.'));
    return;
  }
  const { ev } = x;
  box.className = 'glass next'; box.style.setProperty('--c', colorOf(ev));
  const left = el('div', '');
  left.appendChild(el('small', '', ev.kind === 'exam' ? 'Next exam' : 'Next up'));
  left.appendChild(el('h2', '', ev.title));
  left.appendChild(el('div', 'when', fmtDate(ev.date, true) + ' · ' + fmtRange(ev) + (ev.venue ? ' · ' + ev.venue : '')));
  const open = el('button', 'btn sm', 'Details'); open.type = 'button'; open.style.marginTop = '12px'; open.onclick = () => openEvent(x.i);
  left.appendChild(open);
  const ms = Math.max(0, startOf(ev) - Date.now());
  const d = Math.floor(ms / 864e5), h = Math.floor(ms % 864e5 / 36e5), m = Math.floor(ms % 36e5 / 6e4);
  const cd = el('div', 'cd');
  cd.setAttribute('role', 'timer'); cd.setAttribute('aria-label', d + ' days, ' + h + ' hours, ' + m + ' minutes left');
  [[d, 'days'], [h, 'hours'], [m, 'min']].forEach(([v, l]) => { const c = el('div'); c.append(el('b', '', pad(v)), el('span', '', l)); cd.appendChild(c); });
  box.append(left, cd);
}

/* ---- radar: every upcoming event as a blip. Distance = how soon, angle = stable per event. */
const RADAR_DAYS = 60;
function drawRadar() {
  const box = $('radar'), cap = $('radarCap'); box.replaceChildren();
  const C = 150, R = 138;
  const svg = svgEl('svg', { viewBox: '0 0 300 300', role: 'group', 'aria-label': 'Radar of upcoming events, closer to the centre means sooner' });
  [[7, '7d'], [30, '30d'], [RADAR_DAYS, '60d']].forEach(([d, label], n) => {
    const r = R * Math.sqrt(d / RADAR_DAYS);
    svg.appendChild(svgEl('circle', { cx: C, cy: C, r, class: 'ring' + (n === 0 ? ' dash' : '') }));
    const t = svgEl('text', { x: C + 4, y: C - r + 10, class: 'rl' }); t.textContent = label; svg.appendChild(t);
  });
  [0, 90].forEach((a) => svg.appendChild(svgEl('line', { x1: C - R, y1: C, x2: C + R, y2: C, class: 'axis', transform: 'rotate(' + a + ' ' + C + ' ' + C + ')' })));
  svg.appendChild(svgEl('circle', { cx: C, cy: C, r: 3, fill: 'var(--accent)' }));

  const all = visible().filter(({ ev }) => ev.date >= todayKey());
  const inRange = all.filter(({ ev }) => daysUntil(ev.date) <= RADAR_DAYS);
  const idle = () => {
    cap.replaceChildren();
    cap.appendChild(el('b', '', inRange.length ? plural(inRange.length, 'event') + ' in range' : 'No blips yet'));
    cap.appendChild(el('span', '', all.length > inRange.length ? (all.length - inRange.length) + ' further than 60 days' : 'Hover or tap a blip'));
  };
  inRange.forEach(({ ev, i }) => {
    const n = daysUntil(ev.date), ang = (hueOf(ev.title + ev.date) - 90) * Math.PI / 180;
    const r = R * Math.sqrt(Math.max(n, 0.8) / RADAR_DAYS);
    const g = svgEl('g', { class: 'blip' + (n <= 3 ? ' soon' : ''), tabindex: '0', role: 'button',
      'aria-label': ev.title + ', ' + relDays(n) + ', ' + fmtDate(ev.date),
      transform: 'translate(' + (C + r * Math.cos(ang)).toFixed(1) + ' ' + (C + r * Math.sin(ang)).toFixed(1) + ')' });
    g.style.setProperty('--c', colorOf(ev));
    if (ev.kind === 'exam' || n <= 3) g.appendChild(svgEl('circle', { class: 'pulse', r: 5 }));
    g.appendChild(svgEl('circle', { class: 'core', r: 4.5 }));
    const show = () => {
      cap.replaceChildren(el('b', '', ev.title), el('span', '', relDays(n)[0].toUpperCase() + relDays(n).slice(1) + ' · ' + fmtDate(ev.date) + (ev.start ? ' · ' + ev.start : '')));
    };
    g.addEventListener('mouseenter', show); g.addEventListener('focus', show);
    g.addEventListener('mouseleave', idle); g.addEventListener('blur', idle);
    g.addEventListener('click', () => openEvent(i));
    g.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openEvent(i); } });
    svg.appendChild(g);
  });
  box.appendChild(svg);
  if (!inRange.length) box.appendChild(el('div', 'radar-empty', S.events.length ? 'Nothing in the next 60 days.' : 'Import emails to light up the radar.'));
  idle();
}

/* ---- sidebar: subjects */
function setSubject(k) {
  S.subject = S.subject === k ? null : k;
  const mine = S.events.filter((e) => !S.subject || subjectKey(e) === S.subject);
  if (S.subject && mine.length) gotoDate((mine.find((e) => e.date >= todayKey()) || mine[0]).date);
  refreshFiltered(); setDrawer(false);
}
function refreshFiltered() { drawSubjects(); drawNext(); drawRadar(); drawLegend(); drawSchedule(); drawCalendar(); drawAgenda(); }
function drawSubjects() {
  const box = $('subjects'); box.replaceChildren();
  const list = subjectList();
  const all = el('button', 'subj' + (S.subject ? '' : ' on')); all.type = 'button';
  const dot = el('span', 'dot'); dot.style.setProperty('--c', 'var(--accent)');
  const mid = el('span', 'mid'); mid.append(el('b', '', 'All subjects'), el('small', '', 'Everything on the calendar'));
  all.append(dot, mid, el('span', 'cnt', String(S.events.length)));
  all.onclick = () => { S.subject = null; refreshFiltered(); setDrawer(false); };
  box.appendChild(all);
  if (!list.length) box.appendChild(el('p', 'muted', 'Subjects appear here once exams are found, so you can look at one at a time.')).style.cssText = 'font-size:13px;padding:10px';
  list.forEach((g) => {
    const b = el('button', 'subj' + (S.subject === g.key ? ' on' : '')); b.type = 'button';
    b.setAttribute('aria-pressed', String(S.subject === g.key));
    const d = el('span', 'dot'); d.style.setProperty('--c', subjectColor(g.key));
    const m = el('span', 'mid'); m.append(el('b', 'truncate', g.name), el('small', '', g.next ? 'Next: ' + fmtDate(g.next.date) : 'No upcoming date'));
    b.append(d, m, el('span', 'cnt', String(g.count)));
    b.onclick = () => setSubject(g.key);
    box.appendChild(b);
  });
  const chip = $('filterChip'), cur = list.find((g) => g.key === S.subject);
  chip.hidden = !cur; chip.replaceChildren();
  if (cur) {
    chip.appendChild(el('span', '', cur.name));
    const x = el('button', '', '✕'); x.type = 'button'; x.setAttribute('aria-label', 'Show all subjects');
    x.onclick = () => { S.subject = null; refreshFiltered(); };
    chip.appendChild(x);
  }
}

function drawClashes() {
  const box = $('clashes'); box.replaceChildren();
  box.hidden = !S.clashes.length;
  S.clashes.forEach((c) => {
    const a = S.events[c.a], b = S.events[c.b];
    if (!a || !b) return;
    const sure = c.level === 'clash';
    const al = el('div', 'alert ' + (sure ? 't-danger' : 't-warn')); al.setAttribute('role', 'alert');
    al.appendChild(icon('i-alert'));
    const t = el('div', 'grow');
    t.appendChild(el('strong', '', (sure ? 'Clash' : 'Possible clash') + ' on ' + fmtDate(a.date)));
    t.appendChild(el('div', '', a.title + ' (' + fmtRange(a) + ') and ' + b.title + ' (' + fmtRange(b) + ')'));
    t.appendChild(el('small', 'muted', c.reason));
    const go = el('button', 'btn sm', 'Show'); go.type = 'button';
    go.onclick = () => { S.subject = null; refreshFiltered(); gotoDate(a.date); window.scrollTo({ top: $('calBody').offsetTop - 120, behavior: 'smooth' }); };
    al.append(t, go); box.appendChild(al);
  });
}
function drawLegend() {
  const box = $('legend'); box.replaceChildren();
  const present = new Set(S.events.map(kindOf));
  Object.entries(KINDS).forEach(([k, v]) => {
    if (S.events.length && !present.has(k)) return;
    const off = S.hidden.has(k);
    const b = el('button', 'badge k-' + k + (off ? ' off' : ''), v.label);
    b.type = 'button'; b.setAttribute('aria-pressed', String(!off));
    b.title = (off ? 'Show ' : 'Hide ') + v.label.toLowerCase() + 's';
    b.onclick = () => { off ? S.hidden.delete(k) : S.hidden.add(k); refreshFiltered(); };
    box.appendChild(b);
  });
  if (S.clashes.length) box.appendChild(el('span', 'badge', '⚠ = clash'));
}

function gotoDate(k) { S.selected = k; S.cursor = monthStart(parseKey(k)); setTab('cal'); drawCalendar(); }

/* ---- calendar: month + week */
function drawCalendar() {
  document.querySelectorAll('#viewSwitch button').forEach((b) => b.classList.toggle('on', b.dataset.view === S.view));
  document.querySelectorAll('#colorSwitch button').forEach((b) => b.classList.toggle('on', b.dataset.color === S.colorBy));
  const map = byDate(), body = $('calBody'), today = todayKey();
  body.replaceChildren();
  if (S.view === 'month') drawMonth(body, map, today); else drawWeek(body, map, today);
  if (!S.events.length) {
    const empty = el('div', 'empty');
    empty.append(el('h3', '', 'Your calendar is empty'),
      el('p', '', 'Import some emails (paste them, try the samples, or connect Gmail) and your exams and deadlines will show up here.'));
    const b = el('button', 'btn primary', 'Import emails'); b.type = 'button'; b.onclick = openImport;
    empty.appendChild(b); body.appendChild(empty);
  }
  drawDayPanel(map);
}
function openDay(k, d, outside) {
  S.selected = k;
  if (outside) S.cursor = monthStart(d);
  drawCalendar();
}
function drawMonth(body, map, today) {
  $('calTitle').textContent = S.cursor.toLocaleDateString('en-GB', { month: 'long', year: 'numeric' });
  const grid = el('div', 'mgrid');
  for (let n = 0; n < 7; n++) grid.appendChild(el('div', 'dow', DOW[(n + WEEK_START) % 7]));
  const first = monthStart(S.cursor);
  const lead = (first.getDay() - WEEK_START + 7) % 7, start = addDays(first, -lead);
  const rows = Math.ceil((lead + new Date(first.getFullYear(), first.getMonth() + 1, 0).getDate()) / 7);
  for (let n = 0; n < rows * 7; n++) {
    const d = addDays(start, n), k = key(d), list = map[k] || [];
    const outside = d.getMonth() !== S.cursor.getMonth();
    const cell = el('div', 'day' + (outside ? ' out' : '') + (k === S.selected ? ' sel' : '') + (k === today ? ' today' : '') + (k < today ? ' past' : ''));
    const num = el('button', 'num', String(d.getDate()));
    num.type = 'button';
    num.setAttribute('aria-label', fmtDate(k, true) + ', ' + plural(list.length, 'event'));
    num.setAttribute('aria-pressed', String(k === S.selected));
    cell.appendChild(num);
    list.slice(0, 3).forEach(({ ev, i }) => {
      const pill = el('button', 'pill' + levelClass(i), mark(i) + (ev.start ? ev.start + ' ' : '') + ev.title);
      pill.type = 'button'; pill.title = ev.title; pill.style.setProperty('--c', colorOf(ev));
      pill.onclick = (e) => { e.stopPropagation(); S.selected = k; openEvent(i); };
      cell.appendChild(pill);
    });
    if (list.length > 3) cell.appendChild(el('span', 'more', '+' + (list.length - 3) + ' more'));
    if (list.length) {                                       // phones: coloured dots instead of text
      const dots = el('div', 'dots');
      list.slice(0, 6).forEach(({ ev }) => { const dot = el('i'); dot.style.setProperty('--c', colorOf(ev)); dots.appendChild(dot); });
      cell.appendChild(dots);
    }
    cell.onclick = () => openDay(k, d, outside);
    grid.appendChild(cell);
  }
  body.appendChild(grid);
}
function drawWeek(body, map, today) {
  const sel = parseKey(S.selected), start = addDays(sel, -((sel.getDay() - WEEK_START + 7) % 7)), end = addDays(start, 6);
  const f = (d, o) => d.toLocaleDateString('en-GB', o);
  $('calTitle').textContent = f(start, { day: 'numeric', month: 'short' }) + ' – ' + f(end, { day: 'numeric', month: 'short', year: 'numeric' });
  const grid = el('div', 'wgrid');
  for (let n = 0; n < 7; n++) {
    const d = addDays(start, n), k = key(d), list = map[k] || [];
    const col = el('div', 'wcol' + (k === S.selected ? ' sel' : '') + (k === today ? ' today' : ''));
    const head = el('div', 'wh'); head.append(el('span', '', DOW[d.getDay()]), el('b', '', String(d.getDate())));
    const wl = el('div', 'wlist');
    if (!list.length) wl.appendChild(el('span', 'wnone', 'Nothing'));
    list.forEach(({ ev, i }) => {
      const b = el('button', 'wcard' + levelClass(i)); b.type = 'button'; b.style.setProperty('--c', colorOf(ev));
      b.append(el('b', '', mark(i) + fmtRange(ev)), el('span', '', ev.title));
      if (ev.venue) b.appendChild(el('i', 'truncate', ev.venue));
      b.onclick = (e) => { e.stopPropagation(); S.selected = k; openEvent(i); };
      wl.appendChild(b);
    });
    col.append(head, wl);
    col.onclick = () => { S.selected = k; drawCalendar(); };
    grid.appendChild(col);
  }
  body.appendChild(grid);
}

/* ---- event rows (day panel + agenda) */
function eventRow(ev, i) {
  const row = el('button', 'row' + levelClass(i)); row.type = 'button';
  row.style.setProperty('--c', colorOf(ev));
  const main = el('div', 'min');
  const t = el('div', 'tt'); t.appendChild(el('span', '', ev.title));
  t.appendChild(el('span', 'badge k-' + kindOf(ev), KINDS[kindOf(ev)].label));
  if (S.level[i]) t.appendChild(el('span', 'badge ' + (S.level[i] === 'clash' ? 't-danger' : 't-warn'), S.level[i] === 'clash' ? 'clash' : 'check clash'));
  main.appendChild(t);
  if (ev.venue) main.appendChild(el('small', '', 'Venue: ' + ev.venue));
  row.append(el('div', 'tm tnum', fmtRange(ev)), main, el('div', 'in', relDays(daysUntil(ev.date))));
  row.onclick = () => openEvent(i);
  return row;
}
function drawDayPanel(map) {
  const box = $('dayPanel'); box.replaceChildren();
  const list = (map || byDate())[S.selected] || [];
  const h = el('h3', '', fmtDate(S.selected, true));
  if (S.selected === todayKey()) h.appendChild(el('span', 'badge t-accent', 'today'));
  box.appendChild(h);
  if (!list.length) { box.appendChild(el('p', 'muted', 'No events on this day. Pick another day to see its events.')); return; }
  const wrap = el('div', 'rows');
  list.forEach(({ ev, i }) => wrap.appendChild(eventRow(ev, i)));
  box.appendChild(wrap);
}

/* ---- minimal schedule: mini month + upcoming list */
S.sCursor = monthStart(new Date()); S.sDay = null;
const timeSort = (a, b) => a.ev.date.localeCompare(b.ev.date) || (a.ev.start || '').localeCompare(b.ev.start || '');
function drawSchedule() {
  const box = $('tabSchedule'); box.replaceChildren();
  const today = todayKey(), map = byDate();
  const upcoming = visible().filter(({ ev }) => ev.date >= today).sort(timeSort);
  const shownList = S.sDay ? (map[S.sDay] || []).slice().sort(timeSort) : upcoming;

  const head = el('header', 'sch-head');
  const hl = el('div', ''); hl.appendChild(el('h2', '', S.sDay ? fmtDate(S.sDay, true) : 'Upcoming'));
  const nx = upcoming[0];
  hl.appendChild(el('p', 'sch-sub', !S.events.length ? 'Nothing here yet. Import your emails to see what is coming up.'
    : S.sDay ? plural(shownList.length, 'item') + ' on this day'
    : nx ? 'Next: ' + nx.ev.title + ' · ' + relDays(daysUntil(nx.ev.date)) : 'Nothing coming up. Enjoy the quiet.'));
  head.appendChild(hl);
  if (S.sDay) { const c = el('button', 'sch-clear', 'Show all upcoming'); c.type = 'button'; c.onclick = () => { S.sDay = null; drawSchedule(); }; head.appendChild(c); }
  else { const b = el('button', 'sch-clear', '+ Import emails'); b.type = 'button'; b.onclick = openImport; head.appendChild(b); }
  box.appendChild(head);

  const grid = el('div', 'sch-grid');
  grid.append(scheduleMonth(map, today), scheduleList(shownList, today));
  box.appendChild(grid);
}
function scheduleMonth(map, today) {
  const cal = el('section', 'sch-cal'); cal.setAttribute('aria-label', 'Month');
  const nav = el('div', 'sch-nav');
  const mk = (label, dir, ico) => { const b = el('button', 'sch-step'); b.type = 'button'; b.setAttribute('aria-label', label); b.appendChild(icon(ico));
    b.onclick = () => { S.sCursor = new Date(S.sCursor.getFullYear(), S.sCursor.getMonth() + dir, 1); drawSchedule(); }; return b; };
  nav.append(el('b', '', S.sCursor.toLocaleDateString('en-GB', { month: 'long', year: 'numeric' })), mk('Previous month', -1, 'i-l'), mk('Next month', 1, 'i-r'));
  cal.appendChild(nav);
  const dow = el('div', 'sch-dow');
  for (let i = 0; i < 7; i++) dow.appendChild(el('span', '', DOW[(i + WEEK_START) % 7][0]));
  cal.appendChild(dow);
  const g = el('div', 'sch-days');
  const start = addDays(S.sCursor, -((S.sCursor.getDay() - WEEK_START + 7) % 7));
  for (let i = 0; i < 42; i++) {
    const d = addDays(start, i), k = key(d), items = map[k] || [];
    const b = el('button', 'sch-day' + (d.getMonth() !== S.sCursor.getMonth() ? ' out' : '') + (k === today ? ' today' : '') + (k === S.sDay ? ' sel' : ''));
    b.type = 'button'; b.appendChild(el('span', '', String(d.getDate())));
    if (items.length) {
      const dots = el('i', 'dots');
      items.slice(0, 3).forEach(({ ev }) => { const dt = el('i', ''); dt.style.background = colorOf(ev); dots.appendChild(dt); });
      b.appendChild(dots);
    }
    b.setAttribute('aria-label', fmtDate(k, true) + (items.length ? ', ' + plural(items.length, 'item') : ''));
    b.onclick = () => { S.sDay = S.sDay === k ? null : k; drawSchedule(); };
    g.appendChild(b);
  }
  cal.appendChild(g);
  return cal;
}
function scheduleList(items, today) {
  const wrap = el('section', 'sch-list'); wrap.setAttribute('aria-label', 'Upcoming quizzes and exams');
  if (!items.length) {
    const e = el('div', 'sch-empty');
    e.append(el('b', '', S.sDay ? 'Nothing on this day' : S.events.length ? 'All clear' : 'No schedule yet'),
      el('span', '', S.sDay ? 'Pick another day, or show everything.' : S.events.length ? 'No upcoming items match your filters.' : 'Press “Import emails” to find your quizzes and exams.'));
    wrap.appendChild(e); return wrap;
  }
  const bucket = (ev) => { const n = daysUntil(ev.date); return S.sDay ? fmtDate(ev.date, true) : n < 0 ? 'Earlier' : n === 0 ? 'Today' : n === 1 ? 'Tomorrow' : n < 7 ? 'This week' : n < 31 ? 'Later this month' : 'Later'; };
  let last = null, group = null;
  items.forEach(({ ev, i }) => {
    const b = bucket(ev);
    if (b !== last) { last = b; wrap.appendChild(el('h3', 'sch-group', b)); group = el('div', 'sch-rows'); wrap.appendChild(group); }
    const row = el('button', 'sch-row k-' + kindOf(ev) + (S.level[i] ? ' warn' : '')); row.type = 'button';
    row.style.setProperty('--c', colorOf(ev));
    const date = el('div', 'sch-date'); date.append(el('b', '', String(parseKey(ev.date).getDate())), el('span', '', DOW[parseKey(ev.date).getDay()]));
    const main = el('div', 'sch-main'); main.appendChild(el('div', 'sch-title', mark(i) + ev.title));
    main.appendChild(el('div', 'sch-meta', [fmtRange(ev), ev.venue].filter(Boolean).join(' · ')));
    row.append(date, main, el('span', 'sch-pill', KINDS[kindOf(ev)].label), el('span', 'sch-in', relDays(daysUntil(ev.date))));
    row.onclick = () => openEvent(i);
    group.appendChild(row);
  });
  return wrap;
}

/* ---- agenda with search + upcoming filter */
function matchesQuery(ev) {
  const q = S.query.trim().toLowerCase();
  return !q || [ev.title, ev.venue, ev.subject, ev.quote, subjectName(ev), KINDS[kindOf(ev)].label].some((s) => (s || '').toLowerCase().includes(q));
}
function drawAgenda() {
  const box = $('tabAgenda'); box.replaceChildren();
  const today = todayKey();
  const tools = el('div', 'toolbar'); tools.style.padding = '0 0 14px';
  const sb = el('label', 'searchbox'); sb.appendChild(icon('i-search'));
  const inp = el('input'); inp.id = 'search'; inp.type = 'search'; inp.placeholder = 'Search title, venue, subject…  ( / )'; inp.value = S.query;
  inp.setAttribute('aria-label', 'Search the agenda');
  inp.oninput = () => { S.query = inp.value; const pos = inp.selectionStart; drawAgendaList(list); inp.focus(); inp.setSelectionRange(pos, pos); };
  sb.appendChild(inp);
  const seg = el('div', 'seg');
  [['All', false], ['Upcoming', true]].forEach(([label, v]) => {
    const b = el('button', S.upcomingOnly === v ? 'on' : '', label); b.type = 'button';
    b.onclick = () => { S.upcomingOnly = v; drawAgenda(); };
    seg.appendChild(b);
  });
  const dl = el('button', 'btn sm primary'); dl.type = 'button'; dl.append(icon('i-down'), document.createTextNode('Download .ics'));
  dl.disabled = !S.events.length; dl.onclick = () => downloadIcs(S.ics, 'exam-calendar.ics');
  const r = el('div', 'r'); r.append(seg, dl);
  tools.append(sb, r); box.appendChild(tools);
  const list = el('div', ''); box.appendChild(list);
  drawAgendaList(list);
}
function drawAgendaList(list) {
  list.replaceChildren();
  const today = todayKey();
  const items = visible().filter(({ ev }) => matchesQuery(ev) && (!S.upcomingOnly || ev.date >= today));
  if (!items.length) {
    list.appendChild(el('p', 'muted', !S.events.length ? 'No events yet.' : S.query ? 'Nothing matches “' + S.query + '”.'
      : 'Everything is filtered out. Choose “All subjects”, turn a type back on, or switch to “All”.'));
    return;
  }
  let last = null, wrap = null;
  items.forEach(({ ev, i }) => {
    if (ev.date !== last) {
      last = ev.date;
      const h = el('h3', 'agenda-day' + (ev.date < today ? ' past' : ''), fmtDate(ev.date, true) + (ev.date === today ? ' · today' : ''));
      list.appendChild(h);
      wrap = el('div', 'rows' + (ev.date < today ? ' past' : '')); if (ev.date < today) wrap.style.opacity = '.62';
      list.appendChild(wrap);
    }
    wrap.appendChild(eventRow(ev, i));
  });
}

function drawReview() {
  const box = $('tabReview'); box.replaceChildren();
  if (!S.review.length) {
    const e = el('div', 'empty'); e.append(el('h3', '', 'Nothing needs checking'),
      el('p', '', 'Every event on the calendar had its quote and date verified against the email.'));
    box.appendChild(e); return;
  }
  box.appendChild(el('p', 'muted', 'None of these were added to the calendar. ExamRadar never guesses.')).style.marginBottom = '12px';
  const wrap = el('div', 'rows'); box.appendChild(wrap);
  S.review.forEach((r) => {
    const reject = r.level === 'rejected';
    const card = el('div', 'rcard ' + (reject ? 't-danger' : 't-warn'));
    const t = el('div', 'tt'); t.style.cssText = 'font-weight:700;display:flex;flex-wrap:wrap;gap:8px;align-items:center';
    t.append(el('span', '', r.title || '(no title)'), el('span', 'badge', reject ? 'not trusted' : 'please confirm'));
    card.appendChild(t);
    const ul = el('ul'); (r.reasons || []).forEach((x) => ul.appendChild(el('li', '', x))); card.appendChild(ul);
    if (r.date_text || r.time_text) card.appendChild(el('small', 'muted', 'As written: ' + ((r.date_text || '') + ' ' + (r.time_text || '')).trim()));
    if (r.quote) card.appendChild(el('blockquote', '', r.quote));
    wrap.appendChild(card);
  });
}

/* ================================================================== event dialog + calendar links + single-event .ics */
function eventTimes(ev) {                        // floating local times, like the .ics file
  const [y, mo, d] = ev.date.split('-').map(Number);
  if (!ev.start) return { allDay: true, a: new Date(y, mo - 1, d), b: new Date(y, mo - 1, d + 1) };
  const a = startOf(ev);
  let b = new Date(a.getTime() + 3600000);
  if (ev.end) { const [eh, em] = ev.end.split(':').map(Number); const e = new Date(y, mo - 1, d, eh, em); if (e > a) b = e; }
  return { allDay: false, a, b };
}
const gfmt = (d, t) => d.getFullYear() + pad(d.getMonth() + 1) + pad(d.getDate()) + (t ? 'T' + pad(d.getHours()) + pad(d.getMinutes()) + '00' : '');
function googleUrl(ev) {
  const t = eventTimes(ev);
  const p = new URLSearchParams({ action: 'TEMPLATE', text: ev.title, dates: gfmt(t.a, !t.allDay) + '/' + gfmt(t.b, !t.allDay),
    location: ev.venue || '', details: 'From email: ' + (ev.subject || '') + '\n' + (ev.quote || '') });
  return 'https://calendar.google.com/calendar/render?' + p;
}
function outlookUrl(ev) {
  const t = eventTimes(ev), iso = (d) => d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()) + 'T' + pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':00';
  const p = new URLSearchParams({ path: '/calendar/action/compose', rru: 'addevent', subject: ev.title,
    startdt: t.allDay ? ev.date : iso(t.a), enddt: t.allDay ? key(t.b) : iso(t.b), allday: String(t.allDay),
    location: ev.venue || '', body: ev.quote || '' });
  return 'https://outlook.live.com/calendar/0/deeplink/compose?' + p;
}
const icsEsc = (s) => String(s).replace(/\\/g, '\\\\').replace(/;/g, '\\;').replace(/,/g, '\\,').replace(/\r?\n/g, '\\n');
function icsFold(line) {                         // RFC 5545: 75 octets per line, never split a UTF-8 character
  const enc = new TextEncoder(); let out = '', cur = '', bytes = 0, limit = 75;
  for (const ch of line) {
    const n = enc.encode(ch).length;
    if (bytes + n > limit) { out += cur + '\r\n '; cur = ''; bytes = 0; limit = 74; }
    cur += ch; bytes += n;
  }
  return out + cur;
}
function icsFor(ev) {
  const t = eventTimes(ev), stamp = new Date().toISOString().replace(/[-:]/g, '').replace(/\.\d+/, '');
  let h = 0; for (const c of ev.title + ev.date + (ev.start || '')) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  const L = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//examradar//local//EN', 'CALSCALE:GREGORIAN', 'BEGIN:VEVENT',
    'UID:' + h.toString(16) + '@examradar', 'DTSTAMP:' + stamp, 'SUMMARY:' + icsEsc(ev.title)];
  if (t.allDay) L.push('DTSTART;VALUE=DATE:' + gfmt(t.a), 'DTEND;VALUE=DATE:' + gfmt(t.b));
  else L.push('DTSTART:' + gfmt(t.a, true), 'DTEND:' + gfmt(t.b, true));
  if (ev.venue) L.push('LOCATION:' + icsEsc(ev.venue));
  L.push('DESCRIPTION:' + icsEsc('From email: ' + (ev.subject || '') + '\nQuote: ' + (ev.quote || '')));
  if (ev.kind === 'exam' || ev.kind === 'deadline') L.push('BEGIN:VALARM', 'ACTION:DISPLAY', 'DESCRIPTION:Reminder', 'TRIGGER:-P1D', 'END:VALARM');
  L.push('END:VEVENT', 'END:VCALENDAR');
  return L.map(icsFold).join('\r\n') + '\r\n';
}
function openEvent(i) {
  const ev = S.events[i]; if (!ev) return;
  const box = $('evBody'); box.replaceChildren();
  box.appendChild(el('h3', '', ev.title)).style.marginBottom = '0';
  const badges = el('div', ''); badges.style.cssText = 'display:flex;flex-wrap:wrap;gap:8px';
  badges.appendChild(el('span', 'badge solid k-' + kindOf(ev), KINDS[kindOf(ev)].label));
  badges.appendChild(el('span', 'badge', subjectName(ev)));
  if (S.level[i]) badges.appendChild(el('span', 'badge ' + (S.level[i] === 'clash' ? 't-danger' : 't-warn'), S.level[i] === 'clash' ? 'Clashes with another event' : 'May clash'));
  box.appendChild(badges);
  const dl = el('dl', 'dl');
  const add = (k, v) => { dl.append(el('dt', '', k), el('dd', '', v)); };
  add('Date', fmtDate(ev.date, true)); add('Time', fmtRange(ev));
  const n = daysUntil(ev.date); add('When', relDays(n)[0].toUpperCase() + relDays(n).slice(1));
  if (ev.venue) add('Venue', ev.venue);
  box.appendChild(dl);
  S.clashes.filter((c) => c.a === i || c.b === i).forEach((c) => {
    const o = S.events[c.a === i ? c.b : c.a]; if (!o) return;
    const a = el('div', 'alert ' + (c.level === 'clash' ? 't-danger' : 't-warn'));
    a.appendChild(el('div', 'grow', 'Overlaps with “' + o.title + '” (' + fmtRange(o) + '). ' + c.reason + '.')); box.appendChild(a);
  });
  const src = el('div', 'quote'); src.appendChild(el('small', '', 'WHERE THIS CAME FROM'));
  if (ev.quote) src.appendChild(el('blockquote', '', ev.quote));
  if (ev.subject) src.appendChild(el('small', '', 'Email: ' + ev.subject)).style.cssText = 'margin:6px 0 0;letter-spacing:0;font-weight:500;font-size:12px';
  box.appendChild(src);
  $('evGoogle').href = googleUrl(ev); $('evOutlook').href = outlookUrl(ev);
  $('evIcs').onclick = () => downloadIcs(icsFor(ev), (ev.title.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'event') + '.ics');
  $('evModal').showModal();
}

/* ================================================================== tabs, navigation, downloads */
const TAB_TITLES = { schedule: 'Schedule', cal: 'Calendar', agenda: 'Agenda', review: 'Needs checking' };
function setTab(t) {
  S.tab = t;
  document.querySelectorAll('#mainTabs .tab').forEach((b) => { const on = b.dataset.tab === t; b.classList.toggle('on', on); b.setAttribute('aria-selected', on); });
  document.querySelectorAll('#bnav [data-tab]').forEach((b) => b.classList.toggle('on', b.dataset.tab === t));
  $('tabSchedule').hidden = t !== 'schedule'; $('tabCal').hidden = t !== 'cal'; $('tabAgenda').hidden = t !== 'agenda'; $('tabReview').hidden = t !== 'review';
  $('pageTitle').textContent = TAB_TITLES[t];
}
document.querySelectorAll('#mainTabs .tab, #bnav [data-tab]').forEach((b) => { b.onclick = () => setTab(b.dataset.tab); });
document.querySelectorAll('#viewSwitch button').forEach((b) => { b.onclick = () => { S.view = b.dataset.view; drawCalendar(); }; });
document.querySelectorAll('#colorSwitch button').forEach((b) => {
  b.onclick = () => { S.colorBy = b.dataset.color; store.set('examradar:colorby', S.colorBy); drawNext(); drawRadar(); drawSchedule(); drawCalendar(); drawAgenda(); };
});
function step(dir) {
  if (S.view === 'month') {
    S.cursor = new Date(S.cursor.getFullYear(), S.cursor.getMonth() + dir, 1);
    const sel = parseKey(S.selected);
    if (sel.getMonth() !== S.cursor.getMonth() || sel.getFullYear() !== S.cursor.getFullYear()) S.selected = key(S.cursor);
  } else {
    const d = addDays(parseKey(S.selected), 7 * dir); S.selected = key(d); S.cursor = monthStart(d);
  }
  drawCalendar();
}
function goToday() { const d = new Date(); S.selected = key(d); S.cursor = monthStart(d); drawCalendar(); }
$('prev').onclick = () => step(-1); $('next-btn').onclick = () => step(1); $('today').onclick = goToday;

function downloadIcs(text, name) {
  const a = el('a');
  a.href = URL.createObjectURL(new Blob([text], { type: 'text/calendar' }));
  a.download = name || 'exam-calendar.ics';
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}
$('dl').onclick = () => downloadIcs(S.ics, 'exam-calendar.ics');
$('forget').onclick = () => {
  store.del('examradar:last');
  loadState({}); S.stats = null; S.selected = todayKey(); S.cursor = monthStart(new Date());
  hideAlert($('err')); drawAll(); toast('Saved calendar cleared', 'accent');
};
['openImport', 'openImportTop', 'bnavImport'].forEach((id) => { $(id).onclick = () => { setDrawer(false); openImport(); }; });
$('helpBtn').onclick = () => $('helpModal').showModal();

/* ================================================================== keyboard shortcuts */
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') { setDrawer(false); toggleThemeMenu(false); return; }
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  const t = e.target;
  if (t && (/^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName) || t.isContentEditable)) return;
  if (document.querySelector('dialog[open]')) return;
  const k = e.key.toLowerCase();
  if (k === 'arrowleft' && S.tab === 'cal') step(-1);
  else if (k === 'arrowright' && S.tab === 'cal') step(1);
  else if (k === 't') { setTab('cal'); goToday(); }
  else if (k === 'm') { S.view = 'month'; setTab('cal'); drawCalendar(); }
  else if (k === 'w') { S.view = 'week'; setTab('cal'); drawCalendar(); }
  else if (k === '1') setTab('schedule');
  else if (k === '2') setTab('cal');
  else if (k === '3') setTab('agenda');
  else if (k === '4') setTab('review');
  else if (k === 'i') openImport();
  else if (k === '?') $('helpModal').showModal();
  else if (k === '/') { e.preventDefault(); setTab('agenda'); const s = $('search'); if (s) s.focus(); }
});

/* ================================================================== import dialog: tabs, paste, sample */
function setSource(src) {
  document.querySelectorAll('#srcTabs button').forEach((t) => { const on = t.dataset.src === src; t.classList.toggle('on', on); t.setAttribute('aria-selected', on); });
  $('srcPaste').hidden = src !== 'paste'; $('srcGmail').hidden = src !== 'gmail';
}
document.querySelectorAll('#srcTabs button').forEach((t) => { t.onclick = () => setSource(t.dataset.src); });
async function fill(url, what) {
  try {
    const d = await getJson(url);
    if (d.error) throw new Error(d.error);
    $('emails').value = d.text;
    toast(what + (d.count !== undefined ? ' (' + plural(d.count, 'email') + ')' : '') + ' loaded. Press “Find my exams”.', 'accent');
  } catch (e) { showAlert($('err'), String(e.message || e)); $('impModal').close(); }
}
$('go').onclick = () => {
  const text = $('emails').value.trim();
  if (!text) { toast('Paste at least one email first, or press “Load sample emails”.', 'warn'); return; }
  run({ text });
};
$('clear').onclick = () => { $('emails').value = ''; hideAlert($('err')); $('emails').focus(); };
$('sample').onclick = () => fill('/api/sample', 'Sample emails');
$('inbox').onclick = () => fill('/api/inbox', 'Fetched inbox');

/* ================================================================== Gmail */
const G = { emails: [], picked: new Set() };
function setGmailStep(n) { document.querySelectorAll('#srcGmail .steps li').forEach((s, i) => s.classList.toggle('on', i < n)); }
function drawGmailList() {
  const ul = $('gItems'); ul.replaceChildren();
  G.emails.forEach((e) => {
    const li = el('li');
    const cb = el('input'); cb.type = 'checkbox'; cb.checked = G.picked.has(e.id); cb.setAttribute('aria-label', 'Include: ' + e.subject);
    const mid = el('div', 'm');
    mid.appendChild(el('b', 'truncate', e.subject));
    mid.appendChild(el('small', 'truncate', ((e.from || '').replace(/<.*>/, '').trim() || e.from || 'unknown sender') + ' · ' + fmtDate(e.sent)));
    li.append(cb, mid);
    if (e.relevant) li.appendChild(el('span', 'badge t-ok', 'likely'));
    const toggle = () => { cb.checked ? G.picked.add(e.id) : G.picked.delete(e.id); updateGmailCount(); };
    cb.onchange = toggle;
    li.onclick = (ev) => { if (ev.target !== cb) { cb.checked = !cb.checked; toggle(); } };
    ul.appendChild(li);
  });
  updateGmailCount();
}
function updateGmailCount() {
  const rel = G.emails.filter((e) => e.relevant).length;
  $('gCount').textContent = G.emails.length + ' emails · ' + rel + ' look relevant';
  $('gRun').textContent = G.picked.size ? 'Find my exams in ' + plural(G.picked.size, 'email') : 'Pick at least one email';
  if (!abortCtl) $('gRun').disabled = !G.picked.size;
}
document.querySelectorAll('[data-pick]').forEach((b) => {
  b.onclick = () => {
    const w = b.dataset.pick;
    G.picked = new Set(G.emails.filter((e) => w === 'all' || (w === 'relevant' && e.relevant)).map((e) => e.id));
    drawGmailList();
  };
});
$('gPass').addEventListener('input', () => setGmailStep($('gPass').value ? 3 : 2));
$('gForm').onsubmit = async (ev) => {
  ev.preventDefault();
  hideAlert($('gErr'));
  const user = $('gUser').value.trim();
  $('gFetch').disabled = true; $('gSpin').hidden = false; $('gFetchText').textContent = 'Reading your inbox…';
  try {
    const d = await postJson('/api/gmail/fetch', { user, password: $('gPass').value, host: $('gHost').value.trim(),
      days: Number($('gDays').value), limit: Number($('gLimit').value) });
    if (!d.ok) { showAlert($('gErr'), d.error || 'Could not read your mailbox.'); return; }
    $('gPass').value = '';                                   // the password never lingers on the page
    store.set('examradar:gmail-user', user);
    G.emails = d.emails; G.picked = new Set(d.emails.filter((e) => e.relevant).map((e) => e.id));
    $('gList').hidden = false; drawGmailList(); setGmailStep(4);
    $('gList').scrollIntoView({ behavior: reduceMotion ? 'auto' : 'smooth', block: 'nearest' });
    if (!d.emails.length) showAlert($('gErr'), 'No emails found in the last ' + d.days + ' days.', 'warn');
    else if (!G.picked.size) showAlert($('gErr'), 'None of these ' + d.emails.length + ' emails mention exams or events. You can still tick some and try.', 'warn');
    else toast('Fetched ' + d.emails.length + ' emails. ' + G.picked.size + ' look relevant and are ticked.', 'accent');
  } catch (e) { showAlert($('gErr'), 'Could not reach the ExamRadar server: ' + (e.message || e)); }
  finally { $('gFetch').disabled = false; $('gSpin').hidden = true; $('gFetchText').textContent = 'Connect & fetch emails'; }
};
$('gRun').onclick = () => { if (G.picked.size) run({ gmail_ids: [...G.picked] }); };

/* ================================================================== install as an app (Chromium) */
let installEvent = null;
window.addEventListener('beforeinstallprompt', (e) => { e.preventDefault(); installEvent = e; $('installBtn').hidden = false; });
window.addEventListener('appinstalled', () => { $('installBtn').hidden = true; toast('ExamRadar installed. Open it from your apps.'); });
$('installBtn').onclick = async () => {
  if (!installEvent) return;
  installEvent.prompt(); await installEvent.userChoice; installEvent = null; $('installBtn').hidden = true;
};
if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(() => {});

/* ================================================================== start */
(async function init() {
  applyTheme(store.get('examradar:theme') || 'minimal');
  $('sent').value = todayKey();
  $('gUser').value = store.get('examradar:gmail-user') || '';
  S.colorBy = store.get('examradar:colorby') === 'subject' ? 'subject' : 'kind';
  try {
    const saved = JSON.parse(store.get('examradar:last') || 'null');
    if (saved && Array.isArray(saved.events)) loadState(saved);
  } catch (e) { /* ignore a corrupt save */ }
  ExamReminders.init({
    getEvents: () => S.events,
    open: (i) => { setTab('schedule'); openEvent(i); },
    openSchedule: () => { setTab('schedule'); window.focus(); },
    toast, button: $('alertBtn'),
    kindLabel: (ev) => KINDS[kindOf(ev)].label,
    subjectOf: subjectName
  });
  drawAll(); setTab('schedule');
  if (S.events.length) toast('Restored your last calendar', 'accent');
  ExamReminders.check();                       // reminders that came due while the app was closed
  // keep "today", the countdown, the radar and the reminders fresh without a reload
  let day = todayKey();
  setInterval(() => { if (todayKey() !== day) { day = todayKey(); drawAll(); } else { drawNext(); drawSchedule(); } ExamReminders.check(); }, 30000);
  try {
    const cfg = await getJson('/api/config');
    $('sample').hidden = !cfg.has_sample; $('inbox').hidden = !cfg.has_inbox;
    preferredModel = cfg.default_model;
    await loadModels(preferredModel);
  } catch (e) { showAlert($('err'), 'Could not reach the ExamRadar server. Is “python app.py” still running?'); }
  if (!S.events.length) setTimeout(openImport, 400);     // first visit: go straight to importing
})();
