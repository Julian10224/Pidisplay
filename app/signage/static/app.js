// Signage beheer — lokale beheeromgeving (Preact + htm, zonder buildstap of CDN).
import { html, render, useState, useEffect, useRef, useCallback, useMemo } from './vendor/htm-preact.js';
import Sortable from './vendor/sortable.esm.js';

// ---------------------------------------------------------------------------
// API en kleine hulpmiddelen
// ---------------------------------------------------------------------------
let CSRF = null;
const listeners = { toast: new Set(), auth: new Set() };
const emit = (ch, v) => listeners[ch].forEach((fn) => fn(v));

async function api(method, path, body) {
  const headers = {};
  let payload;
  if (body !== undefined) { payload = JSON.stringify(body); headers['Content-Type'] = 'application/json'; }
  if (method !== 'GET' && CSRF) headers['X-CSRF-Token'] = CSRF;
  let r;
  try {
    r = await fetch(path, { method, headers, body: payload, credentials: 'same-origin' });
  } catch (e) {
    const err = new Error('Geen verbinding met de signage-server.');
    err.status = 0; err.data = {};
    throw err;
  }
  let data = null;
  try { data = await r.json(); } catch (e) { /* geen JSON */ }
  if (r.status === 401 && data && data.code === 'auth') emit('auth', 'lost');
  if (!r.ok) {
    const err = new Error((data && data.error) || `Serverfout (${r.status}).`);
    err.status = r.status; err.data = data || {};
    throw err;
  }
  return data;
}

// Upload met voortgang (fetch kan geen uploadvoortgang melden).
function uploadRaw(url, file, onProgress, extraHeaders = {}) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', url);
    xhr.setRequestHeader('Content-Type', 'application/octet-stream');
    if (CSRF) xhr.setRequestHeader('X-CSRF-Token', CSRF);
    Object.entries(extraHeaders).forEach(([k, v]) => xhr.setRequestHeader(k, v));
    xhr.upload.onprogress = (e) => { if (e.lengthComputable) onProgress(e.loaded / e.total); };
    xhr.onload = () => {
      let data = {};
      try { data = JSON.parse(xhr.responseText); } catch (e) { /* leeg */ }
      if (xhr.status >= 200 && xhr.status < 300) resolve(data);
      else {
        const err = new Error(data.error || `Upload mislukt (${xhr.status}).`);
        err.status = xhr.status; err.data = data;
        reject(err);
      }
    };
    xhr.onerror = () => reject(new Error('Verbinding verbroken tijdens het uploaden.'));
    xhr.send(file);
  });
}

const toast = (text, kind = 'info') => emit('toast', { text, kind, id: Math.random() });
const fail = (e) => toast(e.message || String(e), 'error');

function fmtBytes(n) {
  if (n == null) return '–';
  const u = ['B', 'KB', 'MB', 'GB', 'TB'];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(n >= 10 || i === 0 ? 0 : 1).replace('.', ',')} ${u[i]}`;
}
function fmtDur(s) {
  if (s == null || isNaN(s)) return '–';
  s = Math.round(s);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  return h ? `${h}:${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}` : `${m}:${String(sec).padStart(2, '0')}`;
}
function fmtUptime(s) {
  if (s == null) return '–';
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  if (d) return `${d} d ${h} u`;
  if (h) return `${h} u ${m} min`;
  return `${m} min`;
}
function fmtAgo(ts) {
  if (!ts) return 'nooit';
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 10) return 'zojuist';
  if (s < 60) return `${Math.round(s)} s geleden`;
  if (s < 3600) return `${Math.round(s / 60)} min geleden`;
  if (s < 86400) return `${Math.round(s / 3600)} u geleden`;
  return new Date(ts * 1000).toLocaleString('nl-NL');
}
const fmtDate = (ts) => new Date(ts * 1000).toLocaleString('nl-NL', { dateStyle: 'medium', timeStyle: 'short' });
const REPEAT_LABELS = {
  loop: 'Steeds herhalen',
  once_hold_last: 'Eén keer, laatste item blijft staan',
  once_then_default: 'Eén keer, daarna standaardplaylist',
};

function usePoll(fn, ms, deps = []) {
  useEffect(() => {
    let alive = true, timer;
    const run = async () => {
      if (!alive) return;
      if (document.visibilityState === 'visible') { try { await fn(); } catch (e) { /* stil */ } }
      timer = setTimeout(run, ms);
    };
    run();
    return () => { alive = false; clearTimeout(timer); };
  }, deps);
}

function useHash() {
  const [hash, setHash] = useState(location.hash || '#/');
  useEffect(() => {
    const on = () => setHash(location.hash || '#/');
    addEventListener('hashchange', on);
    return () => removeEventListener('hashchange', on);
  }, []);
  return hash;
}

// ---------------------------------------------------------------------------
// Iconen (inline, geen externe bestanden)
// ---------------------------------------------------------------------------
const P = {
  screen: 'M3 5h18v11H3z M8 20h8 M12 16v4',
  media: 'M4 5h16v14H4z M4 15l4-4 4 4 3-3 5 5 M15 9.5a1.5 1.5 0 1 0 0-.01',
  list: 'M9 6h11 M9 12h11 M9 18h11 M4 6h.01 M4 12h.01 M4 18h.01',
  players: 'M2 6h9v7H2z M13 9h9v7h-9z M5 17h3 M16 19h3',
  gear: 'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z',
  upload: 'M12 16V4 M7 9l5-5 5 5 M4 20h16',
  trash: 'M4 7h16 M10 11v6 M14 11v6 M6 7l1 13h10l1-13 M9 7V4h6v3',
  edit: 'M4 20h4L19 9l-4-4L4 16z M14 6l4 4',
  eye: 'M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12z M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z',
  download: 'M12 4v12 M7 11l5 5 5-5 M4 20h16',
  play: 'M7 4l13 8-13 8z',
  skipNext: 'M5 5l10 7-10 7z M18 5v14',
  skipPrev: 'M19 5L9 12l10 7z M6 5v14',
  image: 'M4 5h16v14H4z M4 15l4-4 4 4 3-3 5 5',
  video: 'M3 6h13v12H3z M16 10l5-3v10l-5-3',
  grip: 'M9 6h.01 M15 6h.01 M9 12h.01 M15 12h.01 M9 18h.01 M15 18h.01',
  x: 'M6 6l12 12 M18 6L6 18',
  plus: 'M12 5v14 M5 12h14',
  alert: 'M12 3l10 18H2z M12 10v4 M12 17h.01',
  info: 'M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20z M12 11v6 M12 7h.01',
  refresh: 'M20 12a8 8 0 1 1-2.3-5.7 M20 4v5h-5',
  power: 'M12 3v9 M6.3 6.3a8 8 0 1 0 11.4 0',
  logout: 'M15 4h4v16h-4 M10 8l-4 4 4 4 M6 12h10',
  check: 'M5 12l5 5 9-10',
  html: 'M4 4h16v16H4z M9 9l-3 3 3 3 M15 9l3 3-3 3',
  transition: 'M4 6h7v12H4z M13 6h7v12h-7z M9 12h6 M13 10l2 2-2 2',
  globe: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z M3 12h18 M12 3c3 3 3 15 0 18 M12 3c-3 3-3 15 0 18',
};
const KIND_LABEL = { image: 'Foto', video: 'Video', html: 'HTML' };
const SCALE_LABELS = { cover: 'Vullen', contain: 'Passend', fill: 'Uitrekken' };
const SCALE_HELP = {
  cover: 'Vult het hele scherm; de beeldverhouding blijft behouden, randen worden zo nodig bijgesneden.',
  contain: 'Het hele beeld blijft zichtbaar met de juiste verhouding; er kunnen zwarte randen ontstaan.',
  fill: 'Vult het hele scherm zonder bijsnijden; het beeld wordt uitgerekt en kan vervormen.',
};
const Icon = ({ name, label }) => html`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"
  stroke-linecap="round" stroke-linejoin="round" aria-hidden=${label ? undefined : 'true'} role=${label ? 'img' : undefined}
  aria-label=${label}>${P[name].split(' M').map((d, i) => html`<path d=${(i ? 'M' : '') + d} />`)}</svg>`;

const BrandMark = () => html`<svg class="brand-mark" viewBox="0 0 32 32" aria-hidden="true">
  <rect x="2" y="5" width="28" height="18" rx="3" fill="var(--signal)" />
  <rect x="12" y="25" width="8" height="3" rx="1" fill="var(--signal)" />
  <circle cx="24" cy="10" r="3" fill="var(--live)" /></svg>`;

// ---------------------------------------------------------------------------
// Algemene componenten
// ---------------------------------------------------------------------------
function Toasts() {
  const [items, setItems] = useState([]);
  useEffect(() => {
    const on = (t) => {
      setItems((xs) => [...xs.slice(-3), t]);
      setTimeout(() => setItems((xs) => xs.filter((x) => x.id !== t.id)), t.kind === 'error' ? 8000 : 4000);
    };
    listeners.toast.add(on);
    return () => listeners.toast.delete(on);
  }, []);
  return html`<div class="toasts" role="status" aria-live="polite">
    ${items.map((t) => html`<div class=${'toast ' + t.kind} key=${t.id}>
      <span>${t.text}</span>
      <button aria-label="Sluiten" onClick=${() => setItems((xs) => xs.filter((x) => x.id !== t.id))}>×</button>
    </div>`)}</div>`;
}

function Modal({ title, onClose, children, wide, footer }) {
  const ref = useRef();
  useEffect(() => {
    const prev = document.activeElement;
    const el = ref.current && ref.current.querySelector('input, select, textarea, button.primary, button');
    if (el) el.focus();
    const key = (e) => { if (e.key === 'Escape') onClose(); };
    addEventListener('keydown', key);
    return () => { removeEventListener('keydown', key); if (prev && prev.focus) prev.focus(); };
  }, []);
  return html`<div class="modal-backdrop" onMouseDown=${(e) => { if (e.target === e.currentTarget) onClose(); }}>
    <div class=${'modal' + (wide ? ' wide' : '')} role="dialog" aria-modal="true" aria-label=${title} ref=${ref}>
      <div class="modal-head"><h2>${title}</h2>
        <button class="btn ghost icon" aria-label="Sluiten" onClick=${onClose}><${Icon} name="x" /></button></div>
      ${children}
      ${footer && html`<div class="modal-foot">${footer}</div>`}
    </div></div>`;
}

// Bevestigingsdialoog als promise: await confirmDialog({...})
let setDialog = () => {};
function DialogHost() {
  const [d, setD] = useState(null);
  setDialog = setD;
  if (!d) return null;
  const close = (v) => { setD(null); d.resolve(v); };
  return html`<${Modal} title=${d.title} onClose=${() => close(false)} footer=${html`
      <button class="btn" onClick=${() => close(false)}>Annuleren</button>
      <button class=${'btn ' + (d.danger ? 'danger solid' : 'primary')} onClick=${() => close(true)}>${d.confirm || 'Doorgaan'}</button>`}>
    <div class="stack" style="gap:10px">${d.body}</div>
  </${Modal}>`;
}
const confirmDialog = (opts) => new Promise((resolve) => setDialog({ ...opts, resolve }));

function Prompt({ title, label, initial = '', confirm = 'Opslaan', onSubmit, onClose }) {
  const [v, setV] = useState(initial);
  const [busy, setBusy] = useState(false);
  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    try { await onSubmit(v.trim()); onClose(); } catch (err) { fail(err); } finally { setBusy(false); }
  };
  return html`<${Modal} title=${title} onClose=${onClose}>
    <form class="stack" style="gap:14px" onSubmit=${submit}>
      <label class="field">${label}<input type="text" value=${v} onInput=${(e) => setV(e.target.value)} maxlength="80" required /></label>
      <div class="modal-foot"><button type="button" class="btn" onClick=${onClose}>Annuleren</button>
        <button class="btn primary" disabled=${busy || !v.trim()}>${confirm}</button></div>
    </form></${Modal}>`;
}

function Thumb({ media, alt = '' }) {
  if (!media) return null;
  if (media.has_thumb) return html`<img src=${`/api/media/${media.id}/thumb`} alt=${alt} loading="lazy" />`;
  const icon = media.kind === 'video' ? 'video' : media.kind === 'html' ? (media.html_source === 'builtin' ? 'globe' : 'html') : 'image';
  return html`<span class="thumb-icon"><${Icon} name=${icon} />${media.kind === 'html' && html`<small>${media.html_source === 'builtin' ? 'Lokale pagina' : 'HTML'}</small>`}</span>`;
}

function mediaMeta(m) {
  if (m.kind === 'html') {
    if (m.html_source === 'builtin') return 'Ingebouwde lokale pagina';
    return `${m.format === 'zip' ? 'HTML-project (ZIP)' : 'HTML-bestand'} · ${fmtBytes(m.size)}${m.html_width ? ` · ontwerp ${m.html_width}×${m.html_height}` : ''}`;
  }
  return `${m.width}×${m.height} · ${m.format.toUpperCase()} · ${fmtBytes(m.size)}`;
}

function MediaView({ media, scale = 'contain', controls = true, onEnded }) {
  if (media.kind === 'html') {
    return html`<iframe class="preview-frame" src=${media.preview_url} sandbox="allow-scripts" title=${media.name}></iframe>`;
  }
  if (media.kind === 'video') {
    return html`<video src=${`/api/media/${media.id}/playback`} style=${`object-fit:${scale}`} controls=${controls}
      autoplay muted playsinline onEnded=${onEnded} onError=${onEnded}></video>`;
  }
  return html`<img src=${`/api/media/${media.id}/playback`} style=${`object-fit:${scale}`} alt=${media.name} />`;
}

function MediaPreview({ media, onClose }) {
  return html`<${Modal} title=${media.name} wide onClose=${onClose}>
    <div class="preview-stage"><${MediaView} media=${media} /></div>
    <p class="muted small">${mediaMeta(media)}${media.kind === 'video' ? ` · ${media.codec} · ${fmtDur(media.duration)}` : ''}</p>
    ${media.kind === 'html' && html`<p class="muted small">Voorbeeld in een afgeschermd venster. Op het scherm vult de pagina het volledige beeld.</p>`}
    ${media.warning && html`<div class="notice warning"><${Icon} name="alert" /><span>${media.warning}</span></div>`}
  </${Modal}>`;
}

// ---------------------------------------------------------------------------
// Inloggen en eerste installatie
// ---------------------------------------------------------------------------
function AuthScreen({ mode, systemName, onDone }) {
  const [form, setForm] = useState({ setup_code: '', username: '', password: '', password2: '' });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value });
  const submit = async (e) => {
    e.preventDefault();
    setError(null);
    if (mode === 'setup' && form.password !== form.password2) { setError('De wachtwoorden zijn niet gelijk.'); return; }
    setBusy(true);
    try {
      await api('POST', mode === 'setup' ? '/api/auth/setup' : '/api/auth/login', form);
      onDone();
    } catch (err) { setError(err.message); } finally { setBusy(false); }
  };
  return html`<main class="auth"><div class="auth-card">
    <div class="row"><${BrandMark} /><div><div class="brand-name">${systemName}</div><div class="brand-sub">Signage beheer</div></div></div>
    ${mode === 'setup' && html`<div class="auth-screen"><div class="monitor-screen">
      <div><p>Op het scherm dat aan de Raspberry Pi hangt staat een setup-code.</p>
      <p class="code" aria-hidden="true">●●●●-●●●●</p><p>Vul die hieronder in om de beheerder aan te maken.</p></div></div></div>`}
    <form class="panel" onSubmit=${submit}>
      <h1>${mode === 'setup' ? 'Beheerder aanmaken' : 'Inloggen'}</h1>
      ${mode === 'setup' && html`<label class="field">Setup-code
        <input type="text" inputmode="numeric" autocomplete="one-time-code" placeholder="1234-5678" value=${form.setup_code} onInput=${set('setup_code')} required /></label>`}
      <label class="field">Gebruikersnaam
        <input type="text" autocomplete="username" value=${form.username} onInput=${set('username')} required minlength="3" maxlength="32" /></label>
      <label class="field">Wachtwoord ${mode === 'setup' && html`<span class="hint">Minimaal 8 tekens.</span>`}
        <input type="password" autocomplete=${mode === 'setup' ? 'new-password' : 'current-password'} value=${form.password} onInput=${set('password')} required minlength=${mode === 'setup' ? 8 : 1} /></label>
      ${mode === 'setup' && html`<label class="field">Wachtwoord herhalen
        <input type="password" autocomplete="new-password" value=${form.password2} onInput=${set('password2')} required minlength="8" /></label>`}
      ${error && html`<div class="notice error" role="alert"><${Icon} name="alert" /><span>${error}</span></div>`}
      <button class="btn primary" disabled=${busy}>${mode === 'setup' ? 'Beheerder aanmaken' : 'Inloggen'}</button>
      ${mode === 'login' && html`<p class="muted small">Wachtwoord kwijt? Zie ‘Toegang herstellen’ in de README op de SD-kaart.</p>`}
    </form></div></main>`;
}

// ---------------------------------------------------------------------------
// Dashboard
// ---------------------------------------------------------------------------
function rendererText(st) {
  const parts = [];
  if (st.renderer === 'browser') {
    parts.push('Chromium (foto, video, HTML)');
    const vs = st.video_stats;
    if (vs && vs.total > 50) parts.push(`${(100 * vs.dropped / vs.total).toFixed(1).replace('.', ',')}% videoframes overgeslagen`);
    if (st.viewport && st.viewport.screen_w) parts.push(`${st.viewport.screen_w}×${st.viewport.screen_h}`);
  } else if (st.renderer === 'mpv') {
    parts.push('mpv (foto, video; geen HTML)');
    if (st.hwdec) parts.push(st.hwdec === 'no' ? 'softwaredecodering' : `hardwaredecodering (${st.hwdec})`);
  }
  if (st.renderer_note) parts.push(st.renderer_note);
  return parts.join(' · ');
}

// ---------------------------------------------------------------------------
// Live bediening: het echte scherm vanuit de website door de playlist laten klikken.
// ---------------------------------------------------------------------------
const LIVE_T_KEY = 'signage.liveTransition';
function useLiveControl(reload) {
  const alive = useRef(true);
  const burstId = useRef(0);
  const [sent, setSent] = useState(null);     // { player, seq } van de laatste opdracht
  const [withT, setWithTState] = useState(() => { try { return localStorage.getItem(LIVE_T_KEY) === '1'; } catch (e) { return false; } });
  useEffect(() => () => { alive.current = false; }, []);
  const setWithT = (v) => { setWithTState(v); try { localStorage.setItem(LIVE_T_KEY, v ? '1' : '0'); } catch (e) { /* genegeerd */ } };
  // Na een opdracht de status even elke 0,5 s verversen, zodat je direct ziet wat er speelt.
  const burst = () => {
    const id = ++burstId.current;
    let n = 0;
    const step = async () => {
      if (!alive.current || id !== burstId.current || n++ >= 24) return;
      try { await reload(); } catch (e) { /* stil */ }
      setTimeout(step, 500);
    };
    setTimeout(step, 300);
  };
  const send = async (player, action, extra = {}) => {
    if (!player) return;
    try {
      const r = await api('POST', `/api/players/${player.id}/control`, { action, transition: withT, ...extra });
      if (alive.current) setSent({ player: player.id, seq: String(r.seq) });
      burst();
    } catch (e) { fail(e); }
  };
  return { send, sent, withT, setWithT };
}

function canControl(p) {
  return !!(p && p.online && p.status && p.status.control);
}

function LiveControls({ player, ctl, hint }) {
  if (!player || !player.online) return null;
  const s = player.status || {};
  if (!s.control) return html`<p class="muted small">Live bediening werkt zodra deze player versie 1.3 of nieuwer draait.</p>`;
  const last = s.control.last;
  const failed = ctl.sent && ctl.sent.player === player.id && last && String(last.seq) === ctl.sent.seq && last.result !== 'ok'
    ? last.result : null;
  return html`<div class="live-ctl" role="group" aria-label=${'Live bediening van ' + player.name}>
    <div class="row">
      <button class="btn" onClick=${() => ctl.send(player, 'prev')} title="Vorige item nu op het scherm tonen"><${Icon} name="skipPrev" />Vorige</button>
      <button class="btn primary" onClick=${() => ctl.send(player, 'next')} title="Volgende item nu op het scherm tonen">Volgende<${Icon} name="skipNext" /></button>
      <label class="check small"><input type="checkbox" checked=${ctl.withT} onChange=${(e) => ctl.setWithT(e.target.checked)} />Met overgang</label>
    </div>
    ${failed ? html`<p class="small live-fail" role="status">Niet uitgevoerd: ${failed}.</p>`
      : hint && html`<p class="muted small">${hint}</p>`}
  </div>`;
}

/* Positie van het huidige item. Bij willekeurige volgorde de plaats in de playlist (zoals in
   de filmstrip), niet de plaats in de geschudde ronde: die telt altijd 1, 2, 3… op en wekt
   zo de indruk dat de playlist op volgorde speelt. */
function livePosition(cur, strip) {
  const shuffle = cur.shuffle != null ? cur.shuffle : !!(strip && strip.shuffle);
  if (!shuffle) return `${cur.index + 1} / ${cur.total}`;
  const pos = strip && cur.item_id != null ? strip.items.findIndex((it) => it.item_id === cur.item_id) : -1;
  return pos >= 0 ? `willekeurig · item ${pos + 1} van ${strip.items.length}` : 'willekeurige volgorde';
}

function Dashboard() {
  const [st, setSt] = useState(null);
  const [playlists, setPlaylists] = useState([]);
  const [strip, setStrip] = useState(null);
  const [busy, setBusy] = useState(false);
  const loadStatus = async () => setSt(await api('GET', '/api/status'));
  const loadPlaylists = async () => setPlaylists((await api('GET', '/api/playlists')).playlists);
  usePoll(loadStatus, 4000);
  useEffect(() => { loadPlaylists().catch(fail); }, []);
  const ctl = useLiveControl(loadStatus);

  const local = st && st.players.find((p) => p.kind === 'local');
  const eff = local && local.effective_playlist;
  const effKey = eff ? `${eff.id}` : '';
  const revision = local && local.status && local.status.revision;
  useEffect(() => {
    if (!eff) { setStrip(null); return; }
    api('GET', `/api/playlists/${eff.id}`).then((r) => setStrip(r.playlist)).catch(() => setStrip(null));
  }, [effKey, revision]);

  // Pijltjestoetsen: vorige/volgende op het scherm (niet in invoervelden of keuzelijsten).
  const liveRef = useRef(null);
  liveRef.current = canControl(local) ? local : null;
  useEffect(() => {
    const key = (e) => {
      if (e.altKey || e.ctrlKey || e.metaKey || e.shiftKey || !liveRef.current) return;
      if (document.querySelector('.modal-backdrop')) return;
      if (e.target.closest && e.target.closest('input, select, textarea, [contenteditable]')) return;
      if (e.key === 'ArrowRight') { e.preventDefault(); ctl.send(liveRef.current, 'next'); }
      if (e.key === 'ArrowLeft') { e.preventDefault(); ctl.send(liveRef.current, 'prev'); }
    };
    addEventListener('keydown', key);
    return () => removeEventListener('keydown', key);
  });

  if (!st) return html`<p class="muted">Status laden…</p>`;

  const status = (local && local.status) || {};
  const live = canControl(local);
  const cur = local && local.online && status.state === 'playing' ? status.current : null;
  const standby = local && local.online && status.state === 'standby';
  const disk = st.disk;
  const diskPct = disk ? Math.round((disk.used / disk.total) * 100) : 0;
  const diskClass = disk && disk.free < (st.disk.total * 0.05) ? 'bad' : diskPct > 85 ? 'warn' : '';

  const switchPlaylist = async (id) => {
    if (!id) return;
    setBusy(true);
    try {
      await api('POST', `/api/playlists/${id}/activate`, { target: local && !local.follows_default ? local.id : 'default' });
      toast('Playlist geactiveerd. Het scherm schakelt binnen enkele seconden over.');
      await Promise.all([loadStatus(), loadPlaylists()]);
    } catch (e) {
      fail(e.data && e.data.problems ? new Error(e.data.problems.join(' ')) : e);
    } finally { setBusy(false); }
  };
  const restartPlayer = async () => {
    setBusy(true);
    try { await api('POST', '/api/system/player', { action: 'restart' }); toast('Player wordt herstart.'); }
    catch (e) { fail(e); } finally { setBusy(false); }
  };

  return html`<div class="stack">
    <div class="page-head"><div><h1>${st.settings.system_name}</h1>
      <p>Wat er nu op het scherm staat, en hoe het systeem ervoor staat.</p></div></div>

    <section class="onair" aria-label="Nu op het scherm">
      <div class="monitor">
        <div class="monitor-screen">
          ${cur && cur.transition ? html`<div class="empty"><${Icon} name="transition" /><br />${cur.name}</div>`
            : cur && cur.kind === 'html' ? html`<div class="empty"><${Icon} name="html" /><br />HTML-pagina: ${cur.name}</div>`
            : cur && cur.media_id
            ? html`<img src=${`/api/media/${cur.media_id}/thumb`} alt=${'Nu op het scherm: ' + cur.name} onError=${(e) => { e.target.style.visibility = 'hidden'; }} />`
            : html`<div class="empty">${!local || !local.online ? 'De player meldt zich niet. Controleer of het scherm is aangesloten en herstart de player.'
                : standby ? 'Het standby-scherm wordt getoond: er is niets om af te spelen.' : 'Player start op…'}</div>`}
        </div>
        <div class="monitor-bar">
          <span class=${'tally' + (cur ? '' : ' off')}><span class="dot"></span>${cur ? 'Op het scherm' : standby ? 'Standby' : 'Geen beeld'}</span>
          <span class="spacer"></span>
          ${cur && html`<span>${livePosition(cur, strip)}</span>`}
        </div>
        ${strip && strip.items.length > 0 && html`<div class="filmstrip" aria-label="Volgorde in de playlist">
          ${strip.items.map((it, i) => {
            const now = cur && !cur.transition && (cur.item_id != null ? cur.item_id === it.item_id
              : cur.media_id === it.media.id && (strip.shuffle || cur.index === i));
            return live
              ? html`<button type="button" class=${'frame' + (now ? ' now' : '')} key=${it.item_id} aria-current=${now ? 'true' : undefined}
                  title=${`${i + 1}. ${it.media.name} — nu op het scherm tonen`} aria-label=${`${i + 1}. ${it.media.name} nu op het scherm tonen`}
                  onClick=${() => ctl.send(local, 'goto', { item_id: it.item_id })}><${Thumb} media=${it.media} /></button>`
              : html`<div class=${'frame' + (now ? ' now' : '')} key=${it.item_id} title=${it.media.name}><${Thumb} media=${it.media} /></div>`;
          })}</div>`}
      </div>

      <div class="panel now-panel">
        <div>
          <div class="muted small">Nu afgespeeld</div>
          <div class="now-title">${cur ? cur.name : '—'}</div>
        </div>
        <${LiveControls} player=${local} ctl=${ctl}
          hint=${strip && strip.items.length ? 'Klik op een beeldje om dat item meteen te tonen. ← en → werken ook.' : null} />
        <dl class="facts">
          <dt>Playlist</dt><dd>${eff ? eff.name : 'geen'}</dd>
          <dt>Player</dt><dd>${local && local.online
            ? html`<span class="badge ok"><span class="dot"></span>actief</span>`
            : html`<span class="badge alert"><span class="dot"></span>offline</span>`}</dd>
          <dt>Laatst gezien</dt><dd>${local ? fmtAgo(local.last_contact) : '–'}</dd>
          ${status.renderer && html`<dt>Weergave</dt><dd>${rendererText(status)}</dd>`}
        </dl>
        <label class="field">Playlist op dit scherm
          <select disabled=${busy} value=${eff ? eff.id : ''} onChange=${(e) => switchPlaylist(e.target.value)}>
            ${!eff && html`<option value="">Kies een playlist…</option>`}
            ${playlists.map((p) => html`<option value=${p.id} disabled=${!p.valid}>${p.name}${p.valid ? '' : ' (leeg of ongeldig)'}</option>`)}
          </select></label>
        <div class="row">
          <a class="btn" href="#/playlists">Playlists bewerken</a>
          <button class="btn ghost" disabled=${busy} onClick=${restartPlayer}><${Icon} name="refresh" />Player herstarten</button>
        </div>
      </div>
    </section>

    ${st.warnings.length > 0 && html`<section class="notices" aria-label="Meldingen">
      ${st.warnings.map((w) => html`<div class=${'notice ' + w.level}><${Icon} name=${w.level === 'info' ? 'info' : 'alert'} /><span>${w.text}</span></div>`)}
    </section>`}

    <section class="vitals" aria-label="Systeemstatus">
      <div class="vital"><div class="k">Signage-server</div>
        <div class="v"><span class="badge ok"><span class="dot"></span>actief</span></div>
        <div class="muted small">versie ${st.server.version} · ${fmtUptime(st.server.uptime)}</div></div>
      <div class="vital"><div class="k">Opslag vrij</div><div class="v">${disk ? fmtBytes(disk.free) : '–'}</div>
        <div class=${'meter ' + diskClass}><span style=${`width:${diskPct}%`}></span></div>
        <div class="muted small">${diskPct}% van ${disk ? fmtBytes(disk.total) : '–'} in gebruik</div></div>
      <div class="vital"><div class="k">Systeem actief</div><div class="v">${fmtUptime(st.system.uptime)}</div>
        <div class="muted small">${st.system.model}</div></div>
      <div class="vital"><div class="k">Processor</div>
        <div class="v">${st.system.temperature != null ? `${String(st.system.temperature).replace('.', ',')} °C` : '–'}</div>
        <div class="muted small">belasting ${st.system.load ? st.system.load[0].toString().replace('.', ',') : '–'} ·
          geheugen vrij ${st.system.memory && st.system.memory.available ? fmtBytes(st.system.memory.available) : '–'}</div></div>
    </section>

    <section class="panel">
      <div class="panel-head"><h2>Belangrijkste instellingen</h2><a class="btn small" href="#/instellingen">Wijzigen</a></div>
      <dl class="facts">
        <dt>Standaardplaylist</dt><dd>${st.default_playlist ? st.default_playlist.name : 'geen'}</dd>
        <dt>Standaardduur afbeelding</dt><dd>${String(st.settings.default_image_duration).replace('.', ',')} s</dd>
        <dt>Volume</dt><dd>${st.settings.volume}%</dd>
        <dt>Media / playlists</dt><dd>${st.counts.media} bestanden, ${st.counts.playlists} playlists</dd>
        <dt>Beheeradres</dt><dd>${st.network.urls.map((u) => html`<div><a href=${u}>${u}</a></div>`)}</dd>
      </dl>
    </section>
  </div>`;
}

// ---------------------------------------------------------------------------
// Media
// ---------------------------------------------------------------------------
function rolesText(roles) {
  const r = roles || [];
  const parts = [];
  if (r.includes('transition')) parts.push('als overgang');
  if (r.includes('logo')) parts.push('als logo');
  return parts.length ? ` (${parts.join(', ')})` : '';
}

function usageBadges(m) {
  const pl = m.playlists || [];
  const has = (role) => pl.filter((p) => (p.roles || []).includes(role));
  const out = [];
  const asItem = has('item'), asTrans = has('transition'), asLogo = has('logo');
  const names = (xs) => xs.map((p) => p.name).join(', ');
  if (asItem.length) out.push(html`<span class="badge signal" title=${names(asItem)}>In ${asItem.length} playlist${asItem.length > 1 ? 's' : ''}</span>`);
  if (asTrans.length) out.push(html`<span class="badge live" title=${names(asTrans)}>Overgang</span>`);
  if (asLogo.length) out.push(html`<span class="badge logo-badge" title=${names(asLogo)}>Logo</span>`);
  return out;
}

function MediaPage() {
  const [data, setData] = useState(null);
  const [queue, setQueue] = useState([]);
  const [over, setOver] = useState(false);
  const [q, setQ] = useState('');
  const [filter, setFilter] = useState('all');
  const [sort, setSort] = useState('new');
  const [preview, setPreview] = useState(null);
  const [rename, setRename] = useState(null);
  const [addPage, setAddPage] = useState(false);
  const [sel, setSel] = useState(() => new Set());
  const fileRef = useRef();
  const running = useRef(false);
  const queueRef = useRef([]);

  const load = async () => {
    const d = await api('GET', '/api/media');
    setData(d);
    setSel((cur) => new Set([...cur].filter((id) => d.media.some((m) => m.id === id))));
  };
  useEffect(() => { load().catch(fail); }, []);

  const update = (id, patch) => {
    queueRef.current = queueRef.current.map((u) => (u.id === id ? { ...u, ...patch } : u));
    setQueue(queueRef.current);
  };

  const processQueue = async () => {
    if (running.current) return;
    running.current = true;
    while (true) {
      const next = queueRef.current.find((u) => u.state === 'waiting');
      if (!next) break;
      update(next.id, { state: 'uploading', progress: 0 });
      try {
        const res = await uploadRaw('/api/media/upload', next.file, (p) => update(next.id, { progress: p, state: p >= 1 ? 'processing' : 'uploading' }),
          { 'X-Filename': encodeURIComponent(next.file.name) });
        update(next.id, { state: 'done', progress: 1, note: res.duplicate ? 'Bestond al; bestaande versie gebruikt.' : (res.media.warning || null) });
        await load();
      } catch (e) {
        update(next.id, { state: 'failed', error: e.message });
      }
    }
    running.current = false;
  };

  const pendingFiles = useRef([]);
  useEffect(() => {
    // Bestanden die gekozen werden voordat de lijst geladen was, alsnog verwerken.
    if (data && pendingFiles.current.length) { const f = pendingFiles.current; pendingFiles.current = []; addFiles(f); }
  }, [data]);

  const addFiles = (files) => {
    if (!data) { pendingFiles.current = [...pendingFiles.current, ...files]; return; }
    const allowed = data.limits.allowed;
    let free = data.limits.free_bytes - data.limits.min_free_bytes;
    const items = [...files].map((file) => {
      const ext = '.' + (file.name.split('.').pop() || '').toLowerCase();
      let error = null;
      if (!allowed.includes(ext)) error = `Bestandstype ${ext} wordt niet ondersteund.`;
      else if (file.size > data.limits.max_upload_bytes) error = `Groter dan de maximale uploadgrootte (${fmtBytes(data.limits.max_upload_bytes)}).`;
      else if (file.size > free) error = 'Onvoldoende vrije opslagruimte.';
      else free -= file.size;
      return { id: Math.random().toString(36).slice(2), file, state: error ? 'failed' : 'waiting', progress: 0, error };
    });
    queueRef.current = [...queueRef.current.filter((u) => u.state !== 'done'), ...items];
    setQueue(queueRef.current);
    processQueue();
  };

  const remove = async (m) => {
    const ok = await confirmDialog({ title: 'Bestand verwijderen?', danger: true, confirm: 'Verwijderen',
      body: html`<p>‘${m.name}’ wordt definitief van de Raspberry Pi verwijderd.</p>` });
    if (!ok) return;
    try {
      await api('DELETE', `/api/media/${m.id}`);
      toast('Bestand verwijderd.');
    } catch (e) {
      if (e.status !== 409) { fail(e); return; }
      const names = e.data.playlists.map((p) => p.name + rolesText(p.roles)).join(', ');
      const force = await confirmDialog({ title: 'Bestand is in gebruik', danger: true, confirm: 'Toch verwijderen',
        body: html`<p>Dit bestand staat in: <strong>${names}</strong>. Het wordt uit die playlists gehaald; als overgang of logo wordt het uitgezet.</p>
          ${e.data.in_active && html`<div class="notice error"><${Icon} name="alert" /><span>Een van deze playlists speelt nu op een scherm.
            Bevat die playlist daarna niets meer, dan verschijnt het standby-scherm.</span></div>`}` });
      if (!force) return;
      try { await api('DELETE', `/api/media/${m.id}?force=1`); toast('Bestand verwijderd en uit playlists gehaald.'); }
      catch (e2) { fail(e2); return; }
    }
    load().catch(fail);
  };

  const list = useMemo(() => {
    if (!data) return [];
    let xs = data.media.filter((m) => m.name.toLowerCase().includes(q.toLowerCase()));
    if (filter === 'image') xs = xs.filter((m) => m.kind === 'image');
    if (filter === 'video') xs = xs.filter((m) => m.kind === 'video');
    if (filter === 'html') xs = xs.filter((m) => m.kind === 'html');
    if (filter === 'logo') xs = xs.filter((m) => m.kind === 'image' && m.format === 'PNG');
    if (filter === 'unused') xs = xs.filter((m) => !m.playlists.length);
    if (filter === 'problem') xs = xs.filter((m) => m.status === 'warning' || m.missing);
    const by = { new: (a, b) => b.created_at - a.created_at, name: (a, b) => a.name.localeCompare(b.name, 'nl'),
      size: (a, b) => b.storage_bytes - a.storage_bytes, duration: (a, b) => (b.duration || 0) - (a.duration || 0) }[sort];
    return [...xs].sort(by);
  }, [data, q, filter, sort]);

  const toggle = (id) => setSel((cur) => { const n = new Set(cur); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  const removeSelected = async () => {
    const chosen = data.media.filter((m) => sel.has(m.id));
    if (!chosen.length) return;
    const bytes = chosen.reduce((a, m) => a + m.storage_bytes, 0);
    const ok = await confirmDialog({ title: `${chosen.length} bestand${chosen.length > 1 ? 'en' : ''} verwijderen?`, danger: true, confirm: 'Verwijderen',
      body: html`<p>Deze bestanden worden definitief van de Raspberry Pi verwijderd (${fmtBytes(bytes)}):</p>
        <ul class="small">${chosen.slice(0, 12).map((m) => html`<li>${m.name}</li>`)}${chosen.length > 12 && html`<li>en nog ${chosen.length - 12}</li>`}</ul>` });
    if (!ok) return;
    const ids = chosen.map((m) => m.id);
    try {
      const r = await api('POST', '/api/media/delete', { ids });
      toast(`${r.deleted} bestand${r.deleted > 1 ? 'en' : ''} verwijderd.`);
    } catch (e) {
      if (e.status !== 409) { fail(e); return; }
      const force = await confirmDialog({ title: 'Sommige bestanden zijn in gebruik', danger: true, confirm: 'Toch alles verwijderen',
        body: html`<p>Deze bestanden worden gebruikt en worden ook uit de playlists gehaald (als overgang of logo: uitgezet):</p>
          <ul class="small">${e.data.in_use.map((u) => html`<li><strong>${u.name}</strong>: ${u.playlists.map((p) => p.name + rolesText(p.roles)).join(', ')}</li>`)}</ul>
          ${e.data.in_active && html`<div class="notice error"><${Icon} name="alert" /><span>Een van deze playlists speelt nu op een scherm.
            Bevat die playlist daarna niets meer, dan verschijnt het standby-scherm.</span></div>`}` });
      if (!force) return;
      try { const r = await api('POST', '/api/media/delete', { ids, force: true }); toast(`${r.deleted} bestanden verwijderd en uit playlists gehaald.`); }
      catch (e2) { fail(e2); return; }
    }
    setSel(new Set());
    load().catch(fail);
  };
  const sum = data && data.summary;
  const usedPct = sum ? Math.round(100 * (sum.disk_total - sum.disk_free) / sum.disk_total) : 0;

  return html`<div>
    <div class="page-head"><div><h1>Media</h1>
      <p>Alles wat op deze Raspberry Pi staat: foto's (PNG, JPEG, WebP), video's (bij voorkeur MP4 met H.264 en AAC, maximaal 1080p30),
        HTML-pagina's (.html of .zip met index.html) en logo's (PNG). Hier kun je alles bekijken en verwijderen.</p></div>
      <button class="btn" onClick=${() => setAddPage(true)}><${Icon} name="globe" />Lokale pagina toevoegen</button></div>

    ${sum && html`<section class="vitals storage" aria-label="Opslag">
      <div class="vital"><div class="k">Media op de Pi</div><div class="v">${fmtBytes(sum.media_bytes)}</div>
        <div class="muted small">${data.media.length} bestanden: ${sum.counts.image} foto's, ${sum.counts.video} video's, ${sum.counts.html} HTML</div></div>
      <div class="vital"><div class="k">Vrije ruimte</div><div class="v">${fmtBytes(sum.disk_free)}</div>
        <div class=${'meter ' + (usedPct > 90 ? 'bad' : usedPct > 80 ? 'warn' : '')}><span style=${`width:${usedPct}%`}></span></div>
        <div class="muted small">${usedPct}% van ${fmtBytes(sum.disk_total)} in gebruik</div></div>
      <div class="vital"><div class="k">Niet gebruikt</div><div class="v">${sum.unused} bestand${sum.unused === 1 ? '' : 'en'}</div>
        <div class="small">${sum.unused > 0 ? html`<button class="btn small ghost" style="padding-left:0" onClick=${() => setFilter('unused')}>Tonen</button>`
          : html`<span class="muted">Alles staat in een playlist</span>`}</div></div>
      <div class="vital"><div class="k">Back-ups</div><div class="v">${fmtBytes(sum.backups_bytes)}</div>
        <div class="small"><a href="#/instellingen">Beheren onder Instellingen</a></div></div>
    </section>`}

    <div class=${'dropzone' + (over ? ' over' : '')}
      onDragOver=${(e) => { e.preventDefault(); setOver(true); }} onDragLeave=${() => setOver(false)}
      onDrop=${(e) => { e.preventDefault(); setOver(false); addFiles(e.dataTransfer.files); }}>
      <p><strong>Sleep bestanden hierheen</strong> of</p>
      <p style="margin-top:8px"><button class="btn primary" onClick=${() => fileRef.current.click()}><${Icon} name="upload" />Bestanden kiezen</button></p>
      <input ref=${fileRef} type="file" multiple hidden accept=${data ? data.limits.allowed.join(',') : ''}
        onChange=${(e) => { addFiles(e.target.files); e.target.value = ''; }} />
      ${data && html`<p class="muted small" style="margin-top:8px">Maximaal ${fmtBytes(data.limits.max_upload_bytes)} per bestand ·
        ${fmtBytes(data.limits.free_bytes)} vrij</p>`}
    </div>
    ${queue.length > 0 && html`<div class="uploads" aria-live="polite">
      ${queue.map((u) => html`<div class=${'upload-row' + (u.state === 'failed' ? ' failed' : '')} key=${u.id}>
        <span>${u.file.name} <span class="muted">(${fmtBytes(u.file.size)})</span></span>
        <span class="small">${{ waiting: 'Wacht…', uploading: `${Math.round(u.progress * 100)}%`, processing: 'Controleren…',
          done: html`<span class="badge ok">Klaar</span>`, failed: html`<span class="badge alert">Mislukt</span>` }[u.state]}</span>
        ${(u.state === 'uploading' || u.state === 'processing') && html`<div class="meter"><span style=${`width:${Math.round(u.progress * 100)}%`}></span></div>`}
        ${u.error && html`<span class="err small">${u.error}</span>`}
        ${u.note && html`<span class="muted small" style="grid-column:1/-1">${u.note}</span>`}
      </div>`)}
      ${queue.some((u) => u.state === 'done' || u.state === 'failed') && !queue.some((u) => u.state === 'waiting' || u.state === 'uploading' || u.state === 'processing') &&
        html`<div><button class="btn small ghost" onClick=${() => { queueRef.current = []; setQueue([]); }}>Lijst wissen</button></div>`}
    </div>`}

    <div class="toolbar">
      <input type="search" placeholder="Zoeken op naam" aria-label="Zoeken op naam" value=${q} onInput=${(e) => setQ(e.target.value)} />
      <select aria-label="Filter" value=${filter} onChange=${(e) => setFilter(e.target.value)}>
        <option value="all">Alle media</option><option value="image">Afbeeldingen</option>
        <option value="video">Video's</option><option value="html">HTML</option><option value="logo">PNG (geschikt als logo)</option>
        <option value="unused">Niet gebruikt</option><option value="problem">Met waarschuwing</option></select>
      <select aria-label="Sorteren" value=${sort} onChange=${(e) => setSort(e.target.value)}>
        <option value="new">Nieuwste eerst</option><option value="name">Naam</option>
        <option value="size">Ruimte op de Pi</option><option value="duration">Duur</option></select>
      <span class="spacer"></span>
      ${data && html`<span class="muted small">${list.length} van ${data.media.length}</span>`}
    </div>
    ${data && data.media.length > 0 && html`<div class=${'selection-bar' + (sel.size ? ' active' : '')}>
      <label class="check"><input type="checkbox" aria-label="Alles in deze weergave selecteren"
        checked=${list.length > 0 && list.every((m) => sel.has(m.id))}
        onChange=${(e) => setSel(e.target.checked ? new Set([...sel, ...list.map((m) => m.id)]) : new Set([...sel].filter((id) => !list.some((m) => m.id === id))))} />
        ${sel.size ? `${sel.size} geselecteerd` : 'Alles selecteren'}</label>
      ${sel.size > 0 && html`<span class="muted small">${fmtBytes(data.media.filter((m) => sel.has(m.id)).reduce((a, m) => a + m.storage_bytes, 0))}</span>
        <span class="spacer"></span>
        <button class="btn small ghost" onClick=${() => setSel(new Set())}>Selectie wissen</button>
        <button class="btn small danger solid" onClick=${removeSelected}><${Icon} name="trash" />Selectie verwijderen</button>`}
    </div>`}

    ${!data ? html`<p class="muted">Media laden…</p>` : data.media.length === 0
      ? html`<div class="panel empty-state"><${Icon} name="media" /><h2>Nog geen media</h2>
          <p>Upload foto's, video's of HTML-pagina's om ze in een playlist te zetten.</p></div>`
      : list.length === 0 ? html`<div class="panel empty-state"><p>Geen bestanden in deze weergave.</p>
          <button class="btn" onClick=${() => { setFilter('all'); setQ(''); }}>Alle media tonen</button></div>`
      : html`<div class="media-grid">${list.map((m) => html`<article class=${'tile' + (m.missing ? ' missing' : m.status === 'warning' ? ' warn' : '') + (sel.has(m.id) ? ' selected' : '')} key=${m.id}>
          <label class="tile-check" title="Selecteren"><input type="checkbox" checked=${sel.has(m.id)} onChange=${() => toggle(m.id)} aria-label=${'Selecteer ' + m.name} /></label>
          <button class="tile-thumb" onClick=${() => setPreview(m)} aria-label=${'Voorbeeld van ' + m.name}>
            <${Thumb} media=${m} />
            <span class="kind"><${Icon} name=${m.kind} />${m.kind === 'video' ? fmtDur(m.duration) : KIND_LABEL[m.kind]}</span>
          </button>
          <div class="tile-body">
            <div class="tile-name">${m.name}</div>
            <div class="tile-meta">${mediaMeta(m)}${m.storage_bytes ? ` · ${fmtBytes(m.storage_bytes)} op de Pi` : ''}</div>
            <div class="row" style="gap:6px">
              ${m.missing && html`<span class="badge alert">Bestand ontbreekt</span>`}
              ${m.status === 'warning' && html`<span class="badge live" title=${m.warning}>Waarschuwing</span>`}
              ${usageBadges(m)}
              ${!m.playlists.length && html`<span class="badge">Niet gebruikt</span>`}
            </div>
            ${m.warning && html`<p class="small muted">${m.warning}</p>`}
            <div class="tile-actions">
              <button class="btn ghost icon" title="Voorbeeld" aria-label="Voorbeeld" onClick=${() => setPreview(m)}><${Icon} name="eye" /></button>
              <button class="btn ghost icon" title="Wijzigen" aria-label="Wijzigen" onClick=${() => setRename(m)}><${Icon} name="edit" /></button>
              ${m.html_source !== 'builtin' && html`<a class="btn ghost icon" title="Origineel downloaden" aria-label="Origineel downloaden" href=${`/api/media/${m.id}/download`}><${Icon} name="download" /></a>`}
              <span class="spacer"></span>
              <button class="btn ghost icon danger" title="Verwijderen" aria-label="Verwijderen" onClick=${() => remove(m)}><${Icon} name="trash" /></button>
            </div>
          </div></article>`)}</div>`}
    ${preview && html`<${MediaPreview} media=${preview} onClose=${() => setPreview(null)} />`}
    ${rename && html`<${MediaEdit} media=${rename} onClose=${() => setRename(null)} onSaved=${() => load().catch(fail)} />`}
    ${addPage && data && html`<${Modal} title="Lokale pagina toevoegen" onClose=${() => setAddPage(false)}>
      <p class="muted small">Ingebouwde schermpagina's van dit systeem. Ze werken zonder internet en vullen het hele scherm.</p>
      <div class="stack" style="gap:8px">${data.builtin_pages.map((pg) => html`<button class="pl-item" key=${pg.id}
        onClick=${async () => { try { await api('POST', '/api/media/builtin', { page: pg.id }); toast(`'${pg.name}' toegevoegd aan media.`); setAddPage(false); await load(); } catch (e) { fail(e); } }}>
        <span class="name"><${Icon} name="globe" /> ${pg.name}</span><span class="meta">${pg.description}</span></button>`)}</div>
    </${Modal}>`}
  </div>`;
}

function MediaEdit({ media, onClose, onSaved }) {
  const [name, setName] = useState(media.name);
  const [w, setW] = useState(media.html_width || '');
  const [h, setH] = useState(media.html_height || '');
  const [busy, setBusy] = useState(false);
  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    try {
      const body = { name };
      if (media.kind === 'html') { body.html_width = w || null; body.html_height = h || null; }
      await api('PATCH', `/api/media/${media.id}`, body);
      toast('Opgeslagen.');
      onSaved();
      onClose();
    } catch (err) { fail(err); } finally { setBusy(false); }
  };
  return html`<${Modal} title="Media wijzigen" onClose=${onClose}>
    <form class="stack" style="gap:14px" onSubmit=${submit}>
      <label class="field">Naam<input type="text" value=${name} maxlength="120" onInput=${(e) => setName(e.target.value)} required /></label>
      ${media.kind === 'html' && html`<fieldset class="fieldset"><legend>Ontwerpformaat (optioneel)</legend>
        <p class="muted small">Leeg: de pagina vult het scherm zelf (responsief). Ingevuld, bijvoorbeeld 1920 × 1080: de pagina wordt op dat
          formaat getekend en daarna volgens de schaalmodus (vullen, passend of uitrekken) over het scherm geschaald.</p>
        <div class="row"><input type="number" min="100" max="7680" placeholder="breedte" aria-label="Breedte" value=${w} onInput=${(e) => setW(e.target.value)} style="width:120px" />
          <span>×</span><input type="number" min="100" max="7680" placeholder="hoogte" aria-label="Hoogte" value=${h} onInput=${(e) => setH(e.target.value)} style="width:120px" />
          <span class="muted small">pixels</span></div></fieldset>`}
      <div class="modal-foot"><button type="button" class="btn" onClick=${onClose}>Annuleren</button>
        <button class="btn primary" disabled=${busy || !name.trim()}>Opslaan</button></div>
    </form></${Modal}>`;
}

// ---------------------------------------------------------------------------
// Playlists
// ---------------------------------------------------------------------------
function PlaylistsPage({ selected }) {
  const [list, setList] = useState(null);
  const [creating, setCreating] = useState(false);
  const loadList = async () => setList((await api('GET', '/api/playlists')).playlists);
  useEffect(() => { loadList().catch(fail); }, []);
  useEffect(() => {
    if (!selected && list && list.length) location.replace(`#/playlists/${list[0].id}`);
  }, [list, selected]);

  return html`<div>
    <div class="page-head"><div><h1>Playlists</h1><p>Zet media in de juiste volgorde en kies wat er op het scherm speelt.</p></div>
      <button class="btn primary" onClick=${() => setCreating(true)}><${Icon} name="plus" />Nieuwe playlist</button></div>
    ${!list ? html`<p class="muted">Laden…</p>` : list.length === 0
      ? html`<div class="panel empty-state"><${Icon} name="list" /><h2>Nog geen playlists</h2>
          <p>Maak een playlist en voeg er media aan toe.</p>
          <button class="btn primary" onClick=${() => setCreating(true)}>Nieuwe playlist</button></div>`
      : html`<div class="pl-layout">
          <nav class="pl-list" aria-label="Playlists">${list.map((p) => html`<a class="pl-item" href=${`#/playlists/${p.id}`}
              aria-current=${String(String(p.id) === String(selected))} key=${p.id}
              onClick=${(e) => { if (editorDirty && String(p.id) !== String(selected) && !confirm('Je hebt niet-opgeslagen wijzigingen. Toch naar een andere playlist?')) e.preventDefault(); }}>
              <span class="name">${p.name}${p.players.length > 0 && html`<span class="badge live"><span class="dot"></span>actief</span>`}</span>
              <span class="meta">${p.item_count} items · ${fmtDur(p.total_duration)}${p.shuffle ? ' · willekeurig' : ''}${p.is_default ? ' · standaard' : ''}${p.valid ? '' : ' · niet afspeelbaar'}</span>
            </a>`)}</nav>
          ${selected ? html`<${PlaylistEditor} key=${selected} id=${selected} onChanged=${loadList} />` : null}
        </div>`}
    ${creating && html`<${Prompt} title="Nieuwe playlist" label="Naam" confirm="Aanmaken" onClose=${() => setCreating(false)}
      onSubmit=${async (v) => { const r = await api('POST', '/api/playlists', { name: v }); await loadList(); location.hash = `#/playlists/${r.id}`; }} />`}
  </div>`;
}

let editorDirty = false;
addEventListener('beforeunload', (e) => { if (editorDirty) { e.preventDefault(); e.returnValue = ''; } });

function PlaylistEditor({ id, onChanged }) {
  const [pl, setPl] = useState(null);
  const [draft, setDraft] = useState(null);
  const [settings, setSettings] = useState(null);
  const [busy, setBusy] = useState(false);
  const [picker, setPicker] = useState(false);
  const [tpicker, setTpicker] = useState(null);   // null | 'default' | index van item
  const [lpicker, setLpicker] = useState(null);   // null | 'all' | index van item (logo)
  const [previewing, setPreviewing] = useState(false);
  const [tpreview, setTpreview] = useState(null);
  const listRef = useRef();
  const draftRef = useRef();
  draftRef.current = draft;

  const toDraft = (p) => ({
    name: p.name, repeat_mode: p.repeat_mode, mute: p.mute, shuffle: !!p.shuffle,
    transition_enabled: p.transition_enabled, transition_media: p.transition_media, new_item_transition: p.new_item_transition,
    items: p.items.map((it) => ({ key: 'i' + it.item_id, media: it.media, duration: it.duration, scale_mode: it.scale_mode,
      transition_mode: it.transition_mode, transition_media: it.transition_media, logo_media: it.logo_media || null })),
  });
  const load = async () => {
    const r = await api('GET', `/api/playlists/${id}`);
    setPl(r.playlist);
    setDraft(toDraft(r.playlist));
  };
  useEffect(() => {
    load().catch((e) => { fail(e); location.hash = '#/playlists'; });
    api('GET', '/api/settings').then((r) => setSettings(r.settings)).catch(() => {});
  }, [id]);

  const serial = (d) => d && JSON.stringify({
    n: d.name, r: d.repeat_mode, m: d.mute, te: d.transition_enabled, tm: d.transition_media ? d.transition_media.id : null,
    nt: d.new_item_transition, sh: !!d.shuffle,
    i: d.items.map((x) => [x.media.id, x.duration ?? null, x.scale_mode || null, x.transition_mode,
      x.transition_mode === 'custom' && x.transition_media ? x.transition_media.id : null, x.logo_media ? x.logo_media.id : null]),
  });
  const dirty = pl && draft && serial(draft) !== serial(toDraft(pl));
  editorDirty = !!dirty;
  useEffect(() => () => { editorDirty = false; }, []);

  useEffect(() => {
    if (!listRef.current) return undefined;
    const s = Sortable.create(listRef.current, {
      handle: '.handle', animation: 150, ghostClass: 'sortable-ghost', chosenClass: 'sortable-chosen',
      onEnd: (evt) => {
        const { oldIndex, newIndex, item, from } = evt;
        if (oldIndex === newIndex) return;
        // DOM terugzetten; Preact verplaatst de elementen op basis van de nieuwe state.
        from.removeChild(item);
        from.insertBefore(item, from.children[oldIndex] || null);
        const items = [...draftRef.current.items];
        const [moved] = items.splice(oldIndex, 1);
        items.splice(newIndex, 0, moved);
        setDraft({ ...draftRef.current, items });
      },
    });
    return () => s.destroy();
  }, [!!draft]);

  if (!pl || !draft) return html`<div class="panel"><p class="muted">Laden…</p></div>`;

  const setItems = (items) => setDraft({ ...draft, items });
  const patchItem = (i, patch) => { const items = [...draft.items]; items[i] = { ...items[i], ...patch }; setItems(items); };
  const move = (i, delta) => {
    const j = i + delta;
    if (j < 0 || j >= draft.items.length) return;
    const items = [...draft.items];
    [items[i], items[j]] = [items[j], items[i]];
    setItems(items);
    requestAnimationFrame(() => { const h = listRef.current && listRef.current.children[j]; if (h) h.querySelector('.handle').focus(); });
  };
  const defDur = settings ? settings.default_image_duration : 10;
  const globalScale = settings ? settings.scale_mode : 'cover';
  const transitionOf = (it) => {
    if (!draft.transition_enabled || it.transition_mode === 'none') return null;
    if (it.transition_mode === 'custom') return it.transition_media;
    return draft.transition_media;
  };
  const total = draft.items.reduce((s, it) => {
    const t = transitionOf(it);
    return s + (it.media.kind === 'video' ? (it.media.duration || 0) : Number(it.duration || defDur)) + (t ? (t.duration || 0) : 0);
  }, 0);

  const save = async (activate) => {
    setBusy(true);
    try {
      await api('PUT', `/api/playlists/${id}`, {
        name: draft.name, repeat_mode: draft.repeat_mode, mute: draft.mute, shuffle: draft.shuffle,
        transition_enabled: draft.transition_enabled,
        transition_media_id: draft.transition_media ? draft.transition_media.id : null,
        new_item_transition: draft.new_item_transition,
        items: draft.items.map((it) => ({
          media_id: it.media.id,
          duration: it.media.kind !== 'video' && it.duration !== '' ? it.duration : null,
          scale_mode: it.scale_mode || null,
          transition_mode: it.transition_mode === 'custom' && !it.transition_media ? 'default' : it.transition_mode,
          transition_media_id: it.transition_mode === 'custom' && it.transition_media ? it.transition_media.id : null,
          logo_media_id: it.logo_media ? it.logo_media.id : null,
        })),
      });
      if (activate) {
        const r = await api('POST', `/api/playlists/${id}/activate`, { target: 'default' });
        toast('Opgeslagen en geactiveerd. Het scherm schakelt binnen enkele seconden over.');
        (r.warnings || []).forEach((w) => toast(w, 'error'));
      } else toast('Playlist opgeslagen.');
      await load();
      onChanged();
    } catch (e) {
      fail(e.data && e.data.problems ? new Error(`${e.message} ${e.data.problems.join(' ')}`) : e);
    } finally { setBusy(false); }
  };
  const removePlaylist = async () => {
    const ok = await confirmDialog({ title: 'Playlist verwijderen?', danger: true, confirm: 'Verwijderen',
      body: html`<p>‘${pl.name}’ wordt verwijderd. De media zelf blijven bewaard.</p>` });
    if (!ok) return;
    try { await api('DELETE', `/api/playlists/${id}`); }
    catch (e) {
      if (e.status !== 409) { fail(e); return; }
      const force = await confirmDialog({ title: 'Playlist is actief', danger: true, confirm: 'Toch verwijderen',
        body: html`<p>Deze playlist speelt op: <strong>${e.data.players.map((p) => p.name).join(', ')}</strong>.
          Na verwijderen toont dat scherm het standby-scherm tot je een andere playlist activeert.</p>` });
      if (!force) return;
      try { await api('DELETE', `/api/playlists/${id}?force=1`); } catch (e2) { fail(e2); return; }
    }
    editorDirty = false;
    toast('Playlist verwijderd.');
    await onChanged();
    location.hash = '#/playlists';
  };

  const pickLogo = (m) => {
    if (lpicker === 'all') { setItems(draft.items.map((it) => ({ ...it, logo_media: m }))); toast(`Logo ingesteld voor alle ${draft.items.length} items. Vergeet niet op te slaan.`); }
    else patchItem(lpicker, { logo_media: m });
  };
  const anyLogo = draft.items.some((it) => it.logo_media);
  const pickTransition = (m) => {
    if (tpicker === 'default') setDraft({ ...draft, transition_media: m });
    else patchItem(tpicker, { transition_mode: 'custom', transition_media: m });
  };
  const loopsBack = draft.repeat_mode === 'loop';

  return html`<section class="stack" aria-label=${'Playlist ' + pl.name}>
    <div class="panel stack" style="gap:14px">
      <div class="row">
        ${pl.players.length > 0 ? html`<span class="badge live"><span class="dot"></span>Speelt op ${pl.players.map((p) => p.name).join(', ')}</span>`
          : html`<span class="badge">Niet actief</span>`}
        ${pl.is_default && html`<span class="badge signal">Standaardplaylist</span>`}
        <span class="spacer"></span>
        <span class="muted small">${draft.items.length} items · ${fmtDur(total)} per ronde</span>
      </div>
      <div class="form-grid">
        <label class="field">Naam<input type="text" value=${draft.name} maxlength="80" onInput=${(e) => setDraft({ ...draft, name: e.target.value })} /></label>
        <label class="field">Herhalen
          <select value=${draft.repeat_mode} onChange=${(e) => setDraft({ ...draft, repeat_mode: e.target.value })}>
            ${Object.entries(REPEAT_LABELS).map(([k, v]) => html`<option value=${k}>${v}</option>`)}</select></label>
        <div class="stack" style="gap:6px;align-self:end">
          <label class="check"><input type="checkbox" checked=${draft.shuffle}
            onChange=${(e) => setDraft({ ...draft, shuffle: e.target.checked })} />Willekeurige volgorde</label>
          <label class="check"><input type="checkbox" checked=${draft.mute}
            onChange=${(e) => setDraft({ ...draft, mute: e.target.checked })} />Geluid van video's uit</label>
        </div>
      </div>
      ${draft.shuffle && html`<p class="section-note" style="margin:0">Elke ronde wordt de volgorde opnieuw geschud: elk item komt één keer langs
        en nooit twee keer achter elkaar. Overgangen en logo's blijven bij hun item horen.</p>`}
      ${pl.problems.length > 0 && !dirty && html`<div class="notice warning"><${Icon} name="alert" /><span>${pl.problems.join(' ')}</span></div>`}
      ${(pl.warnings || []).length > 0 && !dirty && html`<div class="notice"><${Icon} name="info" /><span>${pl.warnings.join(' ')}</span></div>`}
    </div>

    <div class="panel stack transitions-panel" style="gap:12px">
      <div class="panel-head" style="margin:0"><h2><${Icon} name="transition" /> Overgangen</h2>
        <label class="check"><input type="checkbox" checked=${draft.transition_enabled}
          onChange=${(e) => setDraft({ ...draft, transition_enabled: e.target.checked })} />Overgangsvideo's gebruiken</label></div>
      <p class="section-note" style="margin:0">Een overgang is een video die na een item volledig wordt afgespeeld, vóór het volgende item verschijnt.
        Overgangen veranderen niets aan de media of de volgorde.</p>
      ${draft.transition_enabled && html`<div class="form-grid">
        <div class="field">Standaardovergang
          <div class="row" style="gap:8px">
            ${draft.transition_media ? html`<span class="tchip"><span class="thumb"><${Thumb} media=${draft.transition_media} /></span>${draft.transition_media.name}
                <span class="muted small">${fmtDur(draft.transition_media.duration)}</span></span>`
              : html`<span class="muted">Nog geen video gekozen</span>`}
            <button class="btn small" onClick=${() => setTpicker('default')}>Video kiezen</button>
            ${draft.transition_media && html`<button class="btn small ghost" onClick=${() => setTpreview(draft.transition_media)}><${Icon} name="play" />Bekijken</button>`}
          </div></div>
        <label class="field">Nieuwe items krijgen
          <select value=${draft.new_item_transition} onChange=${(e) => setDraft({ ...draft, new_item_transition: e.target.value })}>
            <option value="default">de standaardovergang</option><option value="none">geen overgang</option></select></label>
      </div>`}
    </div>

    <div>
      <div class="panel-head"><h2>${draft.shuffle ? 'Items (willekeurige volgorde)' : 'Volgorde'}</h2>
        <div class="row">
          ${draft.items.length > 0 && html`<button class="btn" onClick=${() => setLpicker('all')}><${Icon} name="image" />Logo voor alle items</button>`}
          ${anyLogo && html`<button class="btn ghost" onClick=${() => setItems(draft.items.map((it) => ({ ...it, logo_media: null })))}>Alle logo's weghalen</button>`}
          <button class="btn" onClick=${() => setPicker(true)}><${Icon} name="plus" />Media toevoegen</button>
        </div></div>
      ${draft.items.length === 0 && html`<div class="panel empty-state"><p>Deze playlist is leeg. Voeg media toe om te beginnen.</p></div>`}
      <div class="items" ref=${listRef}>
        ${draft.items.map((it, i) => {
          const t = transitionOf(it);
          const last = i === draft.items.length - 1;
          return html`<div class=${'item-wrap' + (it.media.missing ? ' bad' : '')} key=${it.key}>
          <div class="item">
            <span class="pos">${i + 1}</span>
            <button class="handle" title="Slepen om te verplaatsen (of pijltjestoetsen)" aria-label=${`Verplaats ${it.media.name}, positie ${i + 1}`}
              onKeyDown=${(e) => { if (e.key === 'ArrowUp') { e.preventDefault(); move(i, -1); } if (e.key === 'ArrowDown') { e.preventDefault(); move(i, 1); } }}>
              <${Icon} name="grip" /></button>
            <div class="thumb"><${Thumb} media=${it.media} /></div>
            <div class="info"><div class="name" title=${it.media.name}>${it.media.name}</div>
              <div class="muted small"><span class=${'kind-tag k-' + it.media.kind}><${Icon} name=${it.media.kind} />${KIND_LABEL[it.media.kind]}</span>
                ${it.media.missing ? ' · bestand ontbreekt' : ''}${it.media.status === 'warning' ? ' · waarschuwing' : ''}</div>
              <div class="logo-ctl">
                ${it.logo_media && html`<span class="logo-chip" title="Logo linksboven"><img src=${`/api/media/${it.logo_media.id}/playback`} alt="" /><span>${it.logo_media.name}</span></span>`}
                <button class="btn tiny ghost" aria-label=${(it.logo_media ? 'Logo wijzigen bij ' : 'Logo toevoegen aan ') + it.media.name}
                  onClick=${() => setLpicker(i)}>${it.logo_media ? 'Wijzigen' : '+ Logo'}</button>
                ${it.logo_media && html`<button class="btn tiny ghost" aria-label=${'Logo weghalen bij ' + it.media.name}
                  onClick=${() => patchItem(i, { logo_media: null })}>Weghalen</button>`}
              </div></div>
            <div class="dur">${it.media.kind !== 'video'
              ? html`<input type="number" min="1" max="86400" step="1" placeholder=${String(defDur)} aria-label="Weergaveduur in seconden"
                  value=${it.duration ?? ''} onInput=${(e) => patchItem(i, { duration: e.target.value === '' ? null : Number(e.target.value) })} /> s`
              : html`<span>${fmtDur(it.media.duration)} (volledig)</span>`}</div>
            <select class="scale-select" aria-label="Schaalmodus" title="Schaalmodus" value=${it.scale_mode || ''}
              disabled=${it.media.kind === 'html' && !it.media.html_width}
              onChange=${(e) => patchItem(i, { scale_mode: e.target.value || null })}>
              <option value="">${it.media.kind === 'html' && !it.media.html_width ? 'Pagina vult zelf' : `Standaard (${SCALE_LABELS[globalScale]})`}</option>
              ${Object.entries(SCALE_LABELS).map(([k, v]) => html`<option value=${k}>${v}</option>`)}</select>
            <button class="btn ghost icon" aria-label=${'Verwijder ' + it.media.name + ' uit playlist'} title="Uit playlist halen"
              onClick=${() => setItems(draft.items.filter((_, j) => j !== i))}><${Icon} name="x" /></button>
          </div>
          ${draft.transition_enabled && (!last || loopsBack || draft.shuffle) && html`<div class="connector">
            <span class="conn-line" aria-hidden="true"></span>
            <${Icon} name="transition" />
            <span class="small">${draft.shuffle ? 'Overgang na dit item' : `Overgang ${last ? 'terug naar item 1' : `naar item ${i + 2}`}`}:</span>
            <select aria-label=${`Overgang na ${it.media.name}`} value=${it.transition_mode}
              onChange=${(e) => { const v = e.target.value; if (v === 'custom') setTpicker(i); else patchItem(i, { transition_mode: v }); }}>
              <option value="default">Standaard${draft.transition_media ? ` (${draft.transition_media.name})` : ' (geen gekozen)'}</option>
              <option value="none">Geen overgang</option>
              <option value="custom">${it.transition_mode === 'custom' && it.transition_media ? `Eigen: ${it.transition_media.name}` : 'Andere video…'}</option>
            </select>
            ${it.transition_mode === 'custom' && html`<button class="btn small ghost" onClick=${() => setTpicker(i)}>Wijzigen</button>`}
            ${t && html`<button class="btn small ghost" onClick=${() => setTpreview(t)}><${Icon} name="play" />Bekijken</button>`}
          </div>`}
        </div>`;
        })}
      </div>
      ${draft.items.length > 0 && html`<p class="muted small" style="margin-top:8px">Lege duur = standaardduur (${String(defDur).replace('.', ',')} s) voor foto's en HTML.
        Video's en overgangen spelen altijd volledig af. Schaalmodus <em>Standaard</em> volgt de instelling onder Instellingen → Weergave.</p>`}
    </div>

    <div class=${dirty ? 'dirty-bar' : 'row'}>
      ${dirty && html`<span>Niet-opgeslagen wijzigingen</span>`}
      <span class="spacer"></span>
      <button class="btn ghost" style=${dirty ? 'color:inherit' : ''} onClick=${() => setPreviewing(true)} disabled=${!draft.items.length}><${Icon} name="play" />Voorbeeld</button>
      ${dirty && html`<button class="btn ghost" style="color:inherit" onClick=${() => setDraft(toDraft(pl))}>Ongedaan maken</button>`}
      <button class="btn" disabled=${busy || !dirty} onClick=${() => save(false)}>Opslaan</button>
      <button class="btn primary" disabled=${busy || !draft.items.length} onClick=${() => save(true)}>${dirty ? 'Opslaan en activeren' : 'Activeren'}</button>
      ${!dirty && html`<button class="btn danger" disabled=${busy} onClick=${removePlaylist}><${Icon} name="trash" />Verwijderen</button>`}
    </div>

    ${picker && html`<${MediaPicker} onClose=${() => setPicker(false)}
      onPick=${(ms) => setItems([...draft.items, ...ms.map((m) => ({ key: 'n' + Math.random().toString(36).slice(2), media: m,
        duration: null, scale_mode: null, transition_mode: draft.new_item_transition, transition_media: null, logo_media: null }))])} />`}
    ${lpicker !== null && html`<${MediaPicker} single kind="image" filter=${(m) => m.format === 'PNG'}
      title=${lpicker === 'all' ? 'Logo voor alle items kiezen' : 'Logo kiezen (PNG)'} confirmLabel="Kiezen"
      emptyText="Er zijn nog geen PNG-afbeeldingen. Upload eerst een logo als PNG (bij voorkeur met transparante achtergrond)."
      note="Het logo komt linksboven, maximaal 18% van de breedte en hoogte van het scherm, en wordt niet vergroot."
      onClose=${() => setLpicker(null)} onPick=${(ms) => ms[0] && pickLogo(ms[0])} />`}
    ${tpicker !== null && html`<${MediaPicker} single kind="video" title="Overgangsvideo kiezen" confirmLabel="Kiezen"
      onClose=${() => setTpicker(null)} onPick=${(ms) => ms[0] && pickTransition(ms[0])} />`}
    ${tpreview && html`<${Modal} title=${'Overgang: ' + tpreview.name} wide onClose=${() => setTpreview(null)}>
      <div class="preview-stage"><${MediaView} media=${tpreview} scale=${globalScale} controls=${false} /></div>
      <p class="muted small">Zo ziet de overgang eruit (${fmtDur(tpreview.duration)}), geschaald volgens de standaardmodus ${SCALE_LABELS[globalScale].toLowerCase()}.</p>
    </${Modal}>`}
    ${previewing && html`<${PlaylistPreview} items=${draft.items} defDur=${defDur} globalScale=${globalScale} shuffle=${draft.shuffle}
      transitionOf=${transitionOf} loopsBack=${loopsBack} onClose=${() => setPreviewing(false)} />`}
  </section>`;
}

function MediaPicker({ onPick, onClose, single = false, kind = null, filter = null, title = 'Media toevoegen', confirmLabel = 'Toevoegen', emptyText = null, note = null }) {
  const [media, setMedia] = useState(null);
  const [sel, setSel] = useState([]);
  const [q, setQ] = useState('');
  useEffect(() => {
    api('GET', '/api/media').then((r) => setMedia(r.media.filter((m) => !m.missing && (!kind || m.kind === kind) && (!filter || filter(m))))).catch(fail);
  }, []);
  const toggle = (m) => {
    if (single) { setSel(sel[0] && sel[0].id === m.id ? [] : [m]); return; }
    setSel(sel.find((x) => x.id === m.id) ? sel.filter((x) => x.id !== m.id) : [...sel, m]);
  };
  const shown = (media || []).filter((m) => m.name.toLowerCase().includes(q.toLowerCase()));
  return html`<${Modal} title=${title} wide onClose=${onClose} footer=${html`
      <span class="muted small" style="margin-right:auto">${single ? (sel[0] ? sel[0].name : (kind === 'video' ? 'Kies één video' : 'Kies één bestand')) : `${sel.length} gekozen, in deze volgorde toegevoegd`}</span>
      <button class="btn" onClick=${onClose}>Annuleren</button>
      <button class="btn primary" disabled=${!sel.length} onClick=${() => { onPick(sel); onClose(); }}>${confirmLabel}</button>`}>
    ${note && html`<p class="muted small">${note}</p>`}
    <input type="search" placeholder="Zoeken op naam" aria-label="Zoeken op naam" value=${q} onInput=${(e) => setQ(e.target.value)} />
    ${!media ? html`<p class="muted">Laden…</p>` : media.length === 0
      ? html`<p>${emptyText || (kind === 'video' ? 'Er zijn nog geen video\'s.' : 'Er zijn nog geen media.')} <a href="#/media" onClick=${onClose}>Naar Media</a>.</p>`
      : html`<div class="picker-grid">${shown.map((m) => {
          const n = sel.findIndex((x) => x.id === m.id);
          return html`<button class="pick" aria-pressed=${String(n >= 0)} onClick=${() => toggle(m)} key=${m.id}>
            <div class="thumb"><${Thumb} media=${m} /></div>
            <div class="n">${n >= 0 && !single ? `${n + 1}. ` : ''}${m.name}</div>
            <div class="n muted small">${KIND_LABEL[m.kind]}${m.kind === 'video' ? ' · ' + fmtDur(m.duration) : ''}</div></button>`;
        })}</div>`}
  </${Modal}>`;
}

function shuffledOrder(n, avoidFirst) {
  const a = [...Array(n).keys()];
  for (let i = n - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); [a[i], a[j]] = [a[j], a[i]]; }
  if (n > 1 && a[0] === avoidFirst) { const k = 1 + Math.floor(Math.random() * (n - 1)); [a[0], a[k]] = [a[k], a[0]]; }
  return a;
}

function PlaylistPreview({ items, defDur, globalScale, shuffle, transitionOf, loopsBack, onClose }) {
  // Speelt de playlist in de browser na, inclusief overgangen, logo's en willekeurige volgorde.
  const n = items.length;
  const initial = () => (shuffle ? shuffledOrder(n, null) : [...Array(n).keys()]);
  const [step, setStep] = useState(() => ({ order: initial(), pos: 0, t: null }));
  const idx = step.order[step.pos % n];
  const it = items[idx];
  const next = useCallback(() => setStep((s) => {
    const cur = s.order[s.pos];
    if (!s.t) {
      const tr = transitionOf(items[cur]);
      const last = s.pos === n - 1;
      if (tr && (!last || loopsBack || shuffle)) return { ...s, t: tr };
    }
    if (s.pos + 1 < n) return { order: s.order, pos: s.pos + 1, t: null };
    return { order: shuffle ? shuffledOrder(n, cur) : s.order, pos: 0, t: null };
  }), [n, shuffle]);
  const prev = () => setStep((s) => ({ ...s, pos: (s.pos - 1 + n) % n, t: null }));
  useEffect(() => {
    if (step.t || !it || it.media.kind === 'video') return undefined;
    const t = setTimeout(next, 1000 * Number(it.duration || defDur));
    return () => clearTimeout(t);
  }, [step]);
  const scale = it.scale_mode || globalScale;
  return html`<${Modal} title="Voorbeeld van de playlist" wide onClose=${onClose}>
    <div class="preview-stage">
      ${step.t
        ? html`<${MediaView} key=${'t' + step.pos} media=${step.t} scale=${globalScale} controls=${false} onEnded=${next} />`
        : html`<${MediaView} key=${it.key + step.pos} media=${it.media} scale=${scale} controls=${false} onEnded=${next} />`}
      ${!step.t && it.logo_media && html`<img class="preview-logo" src=${`/api/media/${it.logo_media.id}/playback`} alt="" />`}
    </div>
    <div class="row"><span>${step.t ? `Overgang: ${step.t.name}` : `${step.pos + 1} / ${n} · ${it.media.name}`}${shuffle ? ' · willekeurig' : ''}</span><span class="spacer"></span>
      <button class="btn small" onClick=${prev}>Vorige</button>
      <button class="btn small" onClick=${next}>Volgende</button></div>
    <p class="muted small">Het voorbeeld speelt in je browser zonder geluid, met dezelfde volgorde, overgangen, logo's en schaalmodi als het scherm.</p>
  </${Modal}>`;
}

// ---------------------------------------------------------------------------
// Schermen en players
// ---------------------------------------------------------------------------
function PlayersPage() {
  const [players, setPlayers] = useState(null);
  const [playlists, setPlaylists] = useState([]);
  const [info, setInfo] = useState(null);
  const [adding, setAdding] = useState(false);
  const [token, setToken] = useState(null);
  const [renaming, setRenaming] = useState(null);
  const load = async () => setPlayers((await api('GET', '/api/players')).players);
  usePoll(load, 5000);
  const ctl = useLiveControl(load);
  useEffect(() => {
    api('GET', '/api/playlists').then((r) => setPlaylists(r.playlists)).catch(fail);
    api('GET', '/api/system/info').then(setInfo).catch(() => {});
  }, []);

  const patch = async (p, body) => {
    try { await api('PATCH', `/api/players/${p.id}`, body); toast('Opgeslagen.'); await load(); }
    catch (e) { fail(e.data && e.data.problems ? new Error(e.data.problems.join(' ')) : e); }
  };
  const del = async (p) => {
    if (!await confirmDialog({ title: 'Player verwijderen?', danger: true, confirm: 'Verwijderen',
      body: html`<p>‘${p.name}’ kan zich daarna niet meer aanmelden.</p>` })) return;
    try { await api('DELETE', `/api/players/${p.id}`); await load(); } catch (e) { fail(e); }
  };
  const newToken = async (p) => {
    if (!await confirmDialog({ title: 'Nieuw token maken?', confirm: 'Nieuw token',
      body: html`<p>Het oude token werkt daarna niet meer. Zet het nieuwe token in /etc/signage/player.ini op die player.</p>` })) return;
    try { const r = await api('POST', `/api/players/${p.id}/token`); setToken({ name: p.name, token: r.token }); } catch (e) { fail(e); }
  };
  const serverUrl = info ? (info.urls[1] || info.urls[0]) : 'http://<ip-van-deze-pi>';
  const defaultPl = playlists.find((p) => p.is_default);

  return html`<div class="stack">
    <div class="page-head"><div><h1>Schermen</h1><p>De signage-server, de players die content afspelen en de schermen die eraan hangen.</p></div>
      <button class="btn" onClick=${() => setAdding(true)}><${Icon} name="plus" />Externe player toevoegen</button></div>

    ${info && html`<section class="panel"><div class="panel-head"><h2>Signage-server</h2><span class="badge ok"><span class="dot"></span>actief</span></div>
      <dl class="facts"><dt>Apparaat</dt><dd>${info.model} (${info.hostname})</dd>
        <dt>Adressen</dt><dd>${info.urls.join(', ')}</dd><dt>Versie</dt><dd>${info.version}</dd></dl></section>`}

    ${!players ? html`<p class="muted">Laden…</p>` : html`<div class="players">${players.map((p) => {
      const s = p.status || {};
      const cur = p.online && s.state === 'playing' ? s.current : null;
      const displays = s.display || [];
      return html`<article class="panel player-card" key=${p.id}>
        <div class="mini-monitor">
          <div class="monitor-screen">${cur && cur.media_id && cur.kind !== 'html' && !cur.transition ? html`<img src=${`/api/media/${cur.media_id}/thumb`} alt="" onError=${(e) => { e.target.style.visibility = 'hidden'; }} />`
            : cur ? html`<span class="small">${cur.transition ? 'Overgang' : 'HTML-pagina'}</span>`
            : html`<span class="small">${p.online ? (s.state === 'standby' ? 'Standby' : 'Geen beeld') : 'Offline'}</span>`}</div>
          <div class="rot">${p.display_name}${p.rotation ? ` · ${p.rotation}° gedraaid` : ''}</div>
        </div>
        <div class="stack" style="gap:12px">
          <div class="row"><h2>${p.name}</h2>
            ${p.online ? html`<span class="badge ok"><span class="dot"></span>online</span>` : html`<span class="badge alert"><span class="dot"></span>offline</span>`}
            <span class="badge">${p.kind === 'local' ? 'Deze Raspberry Pi' : 'Extern'}</span>
            <span class="spacer"></span>
            <button class="btn ghost small" onClick=${() => setRenaming(p)}><${Icon} name="edit" />Naam</button></div>
          <dl class="facts">
            <dt>Laatste contact</dt><dd>${fmtAgo(p.last_contact)}</dd>
            <dt>Nu</dt><dd>${cur ? `${cur.name} (${cur.shuffle ? 'willekeurige volgorde' : `${cur.index + 1}/${cur.total}`})` : s.state === 'standby' ? 'Standby-scherm' : '–'}</dd>
            <dt>Scherm</dt><dd>${displays.length ? displays.map((d) => html`<div>${d.connector}: ${d.connected ? `aangesloten${d.mode ? `, ${d.mode}` : ''}` : 'niet aangesloten'}</div>`) : 'onbekend'}</dd>
            ${s.version && html`<dt>Player</dt><dd>versie ${s.version}${s.mpv_restarts ? ` · ${s.mpv_restarts}× herstart` : ''}</dd>`}
            ${s.renderer && html`<dt>Weergave</dt><dd>${rendererText(s)}</dd>`}
            ${s.message && html`<dt>Melding</dt><dd>${s.message}</dd>`}
          </dl>
          <${LiveControls} player=${p} ctl=${ctl} />
          <div class="form-grid">
            <label class="field">Playlist
              <select value=${p.follows_default ? 'default' : p.playlist_id} onChange=${(e) => patch(p, { playlist_id: e.target.value })}>
                <option value="default">Standaardplaylist volgen${defaultPl ? ` (${defaultPl.name})` : ''}</option>
                ${playlists.map((pl) => html`<option value=${pl.id} disabled=${!pl.valid}>${pl.name}</option>`)}</select></label>
            <label class="field">Schermstand
              <select value=${p.rotation} onChange=${(e) => patch(p, { rotation: Number(e.target.value) })}>
                <option value="0">Liggend (0°)</option><option value="90">Staand (90°)</option>
                <option value="180">Ondersteboven (180°)</option><option value="270">Staand (270°)</option></select></label>
            <label class="field">Naam van het scherm
              <input type="text" value=${p.display_name} maxlength="60" onChange=${(e) => patch(p, { display_name: e.target.value })} /></label>
          </div>
          ${(s.errors || []).length > 0 && html`<details><summary class="small">Recente meldingen (${s.errors.length})</summary>
            <div class="log-view" style="margin-top:8px">${s.errors.join('\n')}</div></details>`}
          ${p.kind === 'remote' && html`<div class="row"><button class="btn small" onClick=${() => newToken(p)}>Nieuw token</button>
            <button class="btn small danger" onClick=${() => del(p)}><${Icon} name="trash" />Verwijderen</button></div>`}
        </div></article>`;
    })}</div>`}

    ${adding && html`<${Prompt} title="Externe player toevoegen" label="Naam (bijvoorbeeld ‘Kantine’)" confirm="Toevoegen" onClose=${() => setAdding(false)}
      onSubmit=${async (v) => { const r = await api('POST', '/api/players', { name: v }); setToken({ name: v, token: r.token }); await load(); }} />`}
    ${token && html`<${Modal} title=${'Token voor ' + token.name} onClose=${() => setToken(null)}
        footer=${html`<button class="btn primary" onClick=${() => setToken(null)}>Klaar</button>`}>
      <p>Dit token wordt maar één keer getoond. Installeer op de andere Raspberry Pi (via SSH, met internet) alleen de player:</p>
      <div class="token-box">curl -fsSL https://raw.githubusercontent.com/Julian10224/Pidisplay/main/get.sh | sudo bash -s -- --role player --server ${serverUrl} --token ${token.token}</div>
      <p class="muted small">Of, met de map <code>signage</code> op de SD-kaart:</p>
      <div class="token-box">sudo bash /boot/firmware/signage/install.sh --role player --server ${serverUrl} --token ${token.token}</div>
      <p class="muted small">De externe player downloadt de media naar zijn eigen SD-kaart en blijft afspelen als de verbinding wegvalt.</p>
    </${Modal}>`}
    ${renaming && html`<${Prompt} title="Naam van de player" label="Naam" initial=${renaming.name} onClose=${() => setRenaming(null)}
      onSubmit=${async (v) => { await api('PATCH', `/api/players/${renaming.id}`, { name: v }); await load(); }} />`}
  </div>`;
}

// ---------------------------------------------------------------------------
// Instellingen
// ---------------------------------------------------------------------------
function Section({ id, title, note, children }) {
  return html`<section class="panel" id=${id}><h2>${title}</h2>${note && html`<p class="section-note" style="margin-top:4px">${note}</p>`}${children}</section>`;
}

/* Software bijwerken vanaf GitHub. Tijdens de update herstart de server; deze sectie blijft
   de status opvragen en laadt de pagina opnieuw zodra de nieuwe versie draait. */
function UpdateSection() {
  const [u, setU] = useState(null);
  const [checking, setChecking] = useState(false);
  const [watch, setWatch] = useState(null);       // { since } zolang we een update volgen
  const [away, setAway] = useState(false);        // server even niet bereikbaar (herstart)
  const load = async () => { const r = await api('GET', '/api/system/update'); setU(r); setAway(false); return r; };
  useEffect(() => { load().catch(() => {}); }, []);
  const status = u && u.status;
  const running = !!(status && status.state === 'running');
  useEffect(() => {
    if (!watch && !running) return undefined;
    let alive = true, timer;
    const tick = async () => {
      if (!alive) return;
      try {
        const r = await load();
        const st = r.status || {};
        const ours = watch && st.started >= watch.since - 5;
        if (ours && st.state === 'done' && r.current === st.to) {
          toast(`Bijgewerkt naar ${r.current}. De pagina wordt opnieuw geladen.`);
          setTimeout(() => location.reload(), 2500);
          return;
        }
        if (ours && (st.state === 'failed' || st.state === 'interrupted')) { setWatch(null); return; }
        if (!watch && st.state !== 'running') return;
      } catch (e) { if (alive) setAway(true); }
      if (alive) timer = setTimeout(tick, 2000);
    };
    timer = setTimeout(tick, 1500);
    return () => { alive = false; clearTimeout(timer); };
  }, [!!watch, running]);

  const check = async () => {
    setChecking(true);
    try { const r = await api('POST', '/api/system/update/check'); setU({ ...u, check: r.check }); }
    catch (e) { fail(e); } finally { setChecking(false); }
  };
  const start = async () => {
    const c = u.check;
    if (!await confirmDialog({ title: `Bijwerken naar ${c.latest}?`, confirm: 'Nu bijwerken',
      body: html`<p>De nieuwe versie wordt van GitHub gedownload en geïnstalleerd. De website en het scherm zijn
        daarbij een paar minuten niet bereikbaar (langer als er systeempakketten bij moeten). Playlists, media en
        instellingen blijven behouden.</p>` })) return;
    try {
      await api('POST', '/api/system/update');
      setWatch({ since: Date.now() / 1000 });
      setU({ ...u, status: { state: 'running', message: 'Update gestart…', started: Date.now() / 1000 }, log: '' });
    } catch (e) { fail(e); }
  };

  if (!u) return html`<${Section} id="update" title="Software bijwerken"><p class="muted">Laden…</p></${Section}>`;
  const c = u.check;
  const busyNow = running || !!watch;
  const failed = status && (status.state === 'failed' || status.state === 'interrupted');
  return html`<${Section} id="update" title="Software bijwerken"
      note=${`Nieuwe versies komen van GitHub (${u.repo}, ${u.branch}). Playlists, media en instellingen blijven behouden.`}>
    <dl class="facts" style="margin-bottom:14px">
      <dt>Geïnstalleerde versie</dt><dd>${u.current}</dd>
      ${c && html`<dt>Nieuwste versie</dt><dd>${c.latest} ${c.newer ? html`<span class="badge ok">nieuw</span>` : html`<span class="muted small">(geïnstalleerd)</span>`}</dd>`}
      ${status && status.state === 'done' && !busyNow && html`<dt>Laatste update</dt><dd>${status.from} → ${status.to}, ${fmtDate(status.updated)}</dd>`}
    </dl>
    ${!u.enabled && html`<div class="notice warning"><${Icon} name="alert" /><span>Bijwerken via de website staat uit (<code>[update]</code> in signage.ini).</span></div>`}
    ${busyNow && html`<div class="notice" role="status"><${Icon} name="refresh" /><span>${away
      ? 'De server herstart met de nieuwe versie…'
      : (status && status.state === 'running' && (!watch || status.started >= watch.since - 5) && status.message) || 'Update gestart…'}
      Laat deze pagina open; hij wordt vanzelf opnieuw geladen.</span></div>`}
    ${failed && !busyNow && html`<div class="notice error" role="alert"><${Icon} name="alert" /><span>${status.message}</span></div>`}
    ${c && c.newer && c.notes && html`<details open style="margin:10px 0"><summary class="small">Wat is er nieuw</summary>
      <div class="log-view" style="margin-top:8px;white-space:pre-wrap">${c.notes}</div></details>`}
    <div class="row" style="margin-top:12px">
      <button class="btn" disabled=${checking || busyNow || !u.enabled} onClick=${check}><${Icon} name="refresh" />${checking ? 'Controleren…' : 'Controleren op updates'}</button>
      ${c && c.newer && html`<button class="btn primary" disabled=${busyNow || !u.enabled || !u.priv_available} onClick=${start}><${Icon} name="download" />Bijwerken naar ${c.latest}</button>`}
      ${c && !c.newer && !busyNow && html`<span class="muted small">Je hebt de nieuwste versie.</span>`}
    </div>
    <p class="muted small" style="margin-top:10px">Mislukt een update, dan blijft de werkende versie actief of zet je de vorige terug met <code>sudo signage-admin rollback</code>.
      Externe players werk je apart bij (via SSH: zie de README).</p>
    ${u.log && html`<details open=${failed} style="margin-top:10px"><summary class="small">Logboek van de laatste update</summary>
      <div class="log-view" style="margin-top:8px">${u.log}</div></details>`}
  </${Section}>`;
}

function SettingsPage({ user, onLogout }) {
  const [s, setS] = useState(null);
  const [info, setInfo] = useState(null);
  const [playlists, setPlaylists] = useState([]);
  const [zones, setZones] = useState([]);
  const [busy, setBusy] = useState('');
  const [host, setHost] = useState('');
  const [tz, setTz] = useState('');
  const [manualTime, setManualTime] = useState('');
  const [backups, setBackups] = useState(null);
  const [logUnit, setLogUnit] = useState('all');
  const [log, setLog] = useState('');
  const [pw, setPw] = useState({ current: '', new: '', new2: '' });
  const restoreRef = useRef();
  const [restoreProgress, setRestoreProgress] = useState(null);

  const loadInfo = async () => { const i = await api('GET', '/api/system/info'); setInfo(i); setHost(i.hostname); setTz(i.time.timezone || ''); };
  const loadBackups = async () => setBackups(await api('GET', '/api/backups'));
  const loadLog = async (unit = logUnit) => setLog((await api('GET', `/api/system/logs?unit=${unit}&lines=300`)).log);
  useEffect(() => {
    api('GET', '/api/settings').then((r) => setS(r.settings)).catch(fail);
    api('GET', '/api/playlists').then((r) => setPlaylists(r.playlists)).catch(fail);
    api('GET', '/api/system/timezones').then((r) => setZones(r.timezones)).catch(() => {});
    loadInfo().catch(fail);
    loadBackups().catch(fail);
    loadLog().catch(() => {});
  }, []);

  const run = async (key, fn, okText) => {
    setBusy(key);
    try { await fn(); if (okText) toast(okText); } catch (e) { fail(e.data && e.data.problems ? new Error(e.data.problems.join(' ')) : e); }
    finally { setBusy(''); }
  };

  const saveGeneral = (e) => {
    e.preventDefault();
    run('general', async () => {
      const r = await api('PUT', '/api/settings', { system_name: s.system_name, default_image_duration: s.default_image_duration,
        volume: s.volume, default_playlist_id: s.default_playlist_id || null });
      setS(r.settings);
    }, 'Instellingen opgeslagen.');
  };

  const restore = async (name) => {
    if (!await confirmDialog({ title: 'Back-up terugzetten?', danger: true, confirm: 'Terugzetten',
      body: html`<p>Alle playlists, players en instellingen worden vervangen door de stand van ${name ? `‘${name}’` : 'het gekozen bestand'}.
        Vlak ervoor wordt automatisch een veiligheidsback-up gemaakt. De server herstart daarna.</p>` })) return false;
    return true;
  };
  const afterRestore = (msg) => {
    toast(msg);
    let tries = 0;
    const wait = async () => {
      tries++;
      try { await fetch('/api/auth/state', { cache: 'no-store' }).then((r) => { if (!r.ok) throw new Error(); }); if (tries > 2) { location.reload(); return; } } catch (e) { /* nog bezig */ }
      if (tries < 60) setTimeout(wait, 2000);
    };
    setTimeout(wait, 3000);
  };

  if (!s || !info) return html`<p class="muted">Instellingen laden…</p>`;
  const priv = info.priv_available;
  const privNote = !priv && html`<div class="notice warning"><${Icon} name="alert" /><span>Systeemacties zijn niet beschikbaar: de hulpfunctie met beheerrechten ontbreekt. Voer het installatiescript opnieuw uit.</span></div>`;

  return html`<div>
    <div class="page-head"><div><h1>Instellingen</h1><p>Wijzigingen worden direct op de Raspberry Pi toegepast.</p></div></div>
    <div class="settings">
      <nav class="settings-nav" aria-label="Onderdelen">
        ${[['algemeen', 'Algemeen'], ['weergave', 'Weergave'], ['netwerk', 'Netwerk'], ['tijd', 'Tijd'], ['backup', 'Back-up'],
          ['logboek', 'Logboeken'], ['update', 'Bijwerken'], ['systeem', 'Systeem'], ['account', 'Account']].map(([k, v]) =>
          html`<a href=${'#/instellingen'} onClick=${(e) => { e.preventDefault(); document.getElementById(k).scrollIntoView({ behavior: 'smooth', block: 'start' }); }}>${v}</a>`)}
      </nav>
      <div class="stack">
        ${privNote}
        <${Section} id="algemeen" title="Algemeen">
          <form class="stack" style="gap:14px" onSubmit=${saveGeneral}>
            <div class="form-grid">
              <label class="field">Naam van het systeem<span class="hint">Staat op het standby-scherm en in de beheeromgeving.</span>
                <input type="text" value=${s.system_name} maxlength="60" onInput=${(e) => setS({ ...s, system_name: e.target.value })} required /></label>
              <label class="field">Standaardplaylist<span class="hint">Speelt op elke player die de standaard volgt.</span>
                <select value=${s.default_playlist_id || ''} onChange=${(e) => setS({ ...s, default_playlist_id: e.target.value ? Number(e.target.value) : null })}>
                  <option value="">Geen (standby-scherm)</option>
                  ${playlists.map((p) => html`<option value=${p.id} disabled=${!p.valid}>${p.name}</option>`)}</select></label>
              <label class="field">Standaardduur afbeelding (s)<span class="hint">Voor items zonder eigen duur.</span>
                <input type="number" min="1" max="86400" value=${s.default_image_duration} onInput=${(e) => setS({ ...s, default_image_duration: e.target.value })} required /></label>
              <label class="field">Volume (${s.volume}%)<span class="hint">Geluid van video's via HDMI.</span>
                <input type="range" min="0" max="100" value=${s.volume} onInput=${(e) => setS({ ...s, volume: Number(e.target.value) })} /></label>
            </div>
            <div><button class="btn primary" disabled=${busy === 'general'}>Opslaan</button></div>
          </form>
        </${Section}>

        <${Section} id="weergave" title="Weergave" note="Hoe foto's, video's en overgangen het scherm vullen, en het beheer van de player.">
          <fieldset class="fieldset" style="margin-bottom:16px"><legend>Schaalmodus (standaard voor alle items)</legend>
            <div class="scale-options" role="radiogroup">
              ${['cover', 'contain', 'fill'].map((mode) => html`<label class="scale-option" key=${mode}>
                <div class="demo" aria-hidden="true"><span style=${{ cover: 'left:0;right:0;top:-90%;bottom:-90%', contain: 'top:0;bottom:0;left:34%;right:34%', fill: 'inset:0' }[mode]}></span></div>
                <strong><input type="radio" name="scale" value=${mode} checked=${(s.scale_mode || 'cover') === mode}
                  onChange=${() => { const prev = s.scale_mode; setS({ ...s, scale_mode: mode });
                    run('scale', async () => { try { const r = await api('PUT', '/api/settings', { scale_mode: mode }); setS(r.settings); }
                      catch (e) { setS((cur) => ({ ...cur, scale_mode: prev })); throw e; } }, `Schaalmodus: ${SCALE_LABELS[mode]}.`); }} />
                  ${SCALE_LABELS[mode]}${mode === 'cover' ? ' (aanbevolen)' : ''}</strong>
                <span class="small muted">${SCALE_HELP[mode]}</span></label>`)}
            </div>
            <p class="muted small">Per playlistitem kun je hiervan afwijken. HTML-pagina's vullen het scherm altijd zelf, behalve als je bij de pagina een vast ontwerpformaat instelt; dan geldt de schaalmodus ook voor de pagina.</p>
          </fieldset>
          <fieldset class="fieldset" style="margin-bottom:16px"><legend>Weergave van deze player (kioskmodus)</legend>
            ${info.renderer && html`<div class="stack" style="gap:10px">
              <div class="row" style="align-items:end">
                <label class="field" style="flex:1;min-width:220px">Weergave
                  <select value=${info.renderer.setting} disabled=${!priv || busy === 'renderer'}
                    onChange=${(e) => run('renderer', async () => { const r = await api('POST', '/api/system/renderer', { renderer: e.target.value }); setInfo({ ...info, renderer: r.renderer }); }, 'Weergave opgeslagen; de player herstart.')}>
                    <option value="auto">Automatisch (browser als die beschikbaar is)</option>
                    <option value="browser">Browser: foto's, video's, HTML en overgangen</option>
                    <option value="mpv">mpv: foto's, video's en overgangen, geen HTML</option>
                  </select></label>
                ${info.renderer.fallback_active && html`<button class="btn" disabled=${!priv || busy === 'renderer'}
                  onClick=${() => run('renderer', async () => { const r = await api('POST', '/api/system/renderer', { renderer: 'reset' }); setInfo({ ...info, renderer: r.renderer }); }, 'Browserweergave wordt opnieuw geprobeerd.')}>Browserweergave opnieuw proberen</button>`}
              </div>
              ${!info.renderer.browser_installed && html`<p class="muted small">De browserweergave (Chromium en cage) is niet geïnstalleerd; de player gebruikt mpv. Installeer opnieuw zonder <code>--no-browser</code> om HTML te kunnen tonen.</p>`}
              ${info.renderer.fallback_active && html`<div class="notice warning"><${Icon} name="alert" /><span>De browserweergave startte drie keer niet; de player is automatisch overgeschakeld op mpv. HTML-pagina's worden overgeslagen. Hieronder staat de foutmelding van de laatste poging. Los de oorzaak op (of werk de software bij) en kies daarna <b>Browserweergave opnieuw proberen</b>.</span></div>`}
              ${info.renderer.last_failure && html`<details open=${info.renderer.fallback_active}>
                <summary class="small">Foutmelding van de laatste mislukte start van de browserweergave (${fmtDate(info.renderer.last_failure_at)})</summary>
                <div class="log-view" style="margin-top:8px">${info.renderer.last_failure}</div>
                <p class="muted small">Vraag je hulp bij dit probleem, kopieer dan deze tekst.</p></details>`}
            </div>`}
          </fieldset>
          <dl class="facts" style="margin-bottom:14px"><dt>Player</dt><dd>${info.player_service.active ? html`<span class="badge ok">actief</span>` : html`<span class="badge alert">gestopt</span>`}</dd>
            <dt>Automatisch starten</dt><dd>${info.player_service.enabled ? 'aan' : 'uit'}</dd></dl>
          <div class="row">
            <button class="btn" disabled=${!priv || busy === 'player'} onClick=${() => run('player', () => api('POST', '/api/system/player', { action: 'restart' }), 'Player wordt herstart.')}><${Icon} name="refresh" />Player herstarten</button>
            ${info.player_service.enabled
              ? html`<button class="btn" disabled=${!priv || busy === 'player'} onClick=${() => run('player', async () => { await api('POST', '/api/system/player', { action: 'disable' }); await loadInfo(); }, 'Player gestopt en automatisch starten uitgezet.')}>Player stoppen en niet automatisch starten</button>`
              : html`<button class="btn primary" disabled=${!priv || busy === 'player'} onClick=${() => run('player', async () => { await api('POST', '/api/system/player', { action: 'enable' }); await loadInfo(); }, 'Player gestart en automatisch starten aangezet.')}>Player starten en automatisch starten</button>`}
          </div>
          <p class="section-note" style="margin-top:12px">Schermstand (liggend/staand) stel je per scherm in onder <a href="#/schermen">Schermen</a>.
            Decodering en audio-uitgang staan in /etc/signage/player.ini.</p>
        </${Section}>

        <${Section} id="netwerk" title="Netwerk" note="De beheeromgeving is alleen bereikbaar vanaf het lokale netwerk. Netwerkverbinding, Wi-Fi en SSH worden hier niet gewijzigd.">
          <dl class="facts" style="margin-bottom:14px">
            <dt>Adressen</dt><dd>${info.urls.map((u) => html`<div>${u}</div>`)}</dd>
            <dt>Poort</dt><dd>${info.port} <span class="muted small">(wijzigen in ${info.config_path})</span></dd>
            <dt>Toegang</dt><dd>${info.allow_public_clients ? 'ook vanaf publieke adressen' : 'alleen lokaal netwerk'}</dd>
          </dl>
          <form class="row" style="align-items:end" onSubmit=${(e) => { e.preventDefault(); run('host', async () => { await api('POST', '/api/system/hostname', { hostname: host }); await loadInfo(); }, 'Hostnaam gewijzigd.'); }}>
            <label class="field" style="flex:1;min-width:200px">Hostnaam<span class="hint">Bereikbaar als ${host || '…'}.local. Het IP-adres blijft altijd werken.</span>
              <input type="text" value=${host} pattern="[a-z0-9-]{1,63}" onInput=${(e) => setHost(e.target.value.toLowerCase())} required /></label>
            <button class="btn" disabled=${!priv || busy === 'host' || host === info.hostname}>Hostnaam opslaan</button>
          </form>
        </${Section}>

        <${Section} id="tijd" title="Tijd" note="Afspelen hangt niet af van de klok. Zonder internet loopt de klok door vanaf de laatst bekende tijd; stel hem zo nodig handmatig in.">
          <dl class="facts" style="margin-bottom:14px"><dt>Tijd op de Pi</dt><dd>${info.local_time}</dd>
            <dt>Tijdsynchronisatie</dt><dd>${info.time.ntp ? (info.time.synchronized ? 'aan, gesynchroniseerd' : 'aan, nog niet gesynchroniseerd') : 'uit'}</dd></dl>
          <div class="stack" style="gap:14px">
            <form class="row" style="align-items:end" onSubmit=${(e) => { e.preventDefault(); run('tz', async () => { await api('POST', '/api/system/timezone', { timezone: tz }); await loadInfo(); }, 'Tijdzone ingesteld.'); }}>
              <label class="field" style="flex:1;min-width:200px">Tijdzone
                <select value=${tz} onChange=${(e) => setTz(e.target.value)}>${zones.map((z) => html`<option value=${z}>${z}</option>`)}</select></label>
              <button class="btn" disabled=${!priv || busy === 'tz'}>Tijdzone opslaan</button></form>
            <label class="check"><input type="checkbox" checked=${!!info.time.ntp} disabled=${!priv || busy === 'ntp'}
              onChange=${(e) => run('ntp', async () => { await api('POST', '/api/system/ntp', { enabled: e.target.checked }); await loadInfo(); }, 'Opgeslagen.')} />
              Tijd automatisch synchroniseren als er internet is</label>
            <form class="row" style="align-items:end" onSubmit=${(e) => { e.preventDefault(); run('time', async () => { await api('POST', '/api/system/time', { datetime: manualTime }); await loadInfo(); }, 'Tijd ingesteld.'); }}>
              <label class="field" style="flex:1;min-width:200px">Tijd handmatig instellen<span class="hint">Zet automatische synchronisatie uit.</span>
                <input type="datetime-local" value=${manualTime} onInput=${(e) => setManualTime(e.target.value)} required /></label>
              <button class="btn" disabled=${!priv || busy === 'time'}>Tijd instellen</button></form>
          </div>
        </${Section}>

        <${Section} id="backup" title="Back-up en herstel" note="Een back-up bevat playlists, players, instellingen en optioneel alle media. Download back-ups naar je computer; een kapotte SD-kaart neemt anders de back-ups mee. Elke nacht wordt automatisch een back-up zonder media gemaakt.">
          <div class="row" style="margin-bottom:14px">
            <button class="btn primary" disabled=${!!busy} onClick=${() => run('bk', async () => { await api('POST', '/api/backups', { include_media: false }); await loadBackups(); }, 'Back-up gemaakt.')}>Back-up maken</button>
            <button class="btn" disabled=${!!busy} onClick=${() => run('bk', async () => { await api('POST', '/api/backups', { include_media: true }); await loadBackups(); }, 'Back-up met media gemaakt.')}>Back-up met media maken</button>
            <button class="btn ghost" disabled=${!!busy} onClick=${() => restoreRef.current.click()}><${Icon} name="upload" />Back-upbestand terugzetten</button>
            <input type="file" hidden ref=${restoreRef} accept=".gz,.tar.gz,application/gzip" onChange=${async (e) => {
              const f = e.target.files[0]; e.target.value = '';
              if (!f || !await restore(f.name)) return;
              setRestoreProgress(0);
              try { const r = await uploadRaw('/api/backups/restore', f, setRestoreProgress); afterRestore(r.message); }
              catch (err) { fail(err); } finally { setRestoreProgress(null); }
            }} />
          </div>
          ${busy === 'bk' && html`<p class="muted small">Back-up wordt gemaakt…</p>`}
          ${restoreProgress != null && html`<div class="meter"><span style=${`width:${Math.round(restoreProgress * 100)}%`}></span></div>`}
          ${backups && (backups.backups.length === 0 ? html`<p class="muted">Nog geen back-ups.</p>` : html`<div class="table-wrap"><table class="list">
            <thead><tr><th>Datum</th><th>Soort</th><th>Grootte</th><th></th></tr></thead>
            <tbody>${backups.backups.map((b) => html`<tr key=${b.name}>
              <td class="nowrap">${fmtDate(b.created)}</td>
              <td>${b.pre_restore ? 'Vóór herstel' : b.automatic ? 'Automatisch' : 'Handmatig'}${b.include_media ? ', met media' : ''}</td>
              <td class="nowrap">${fmtBytes(b.size)}</td>
              <td><div class="row" style="justify-content:flex-end;gap:4px">
                <a class="btn ghost icon" title="Downloaden" aria-label="Downloaden" href=${`/api/backups/${encodeURIComponent(b.name)}`}><${Icon} name="download" /></a>
                <button class="btn ghost small" disabled=${!!busy} onClick=${async () => { if (!await restore(b.name)) return; run('rs', async () => { const r = await api('POST', `/api/backups/restore?name=${encodeURIComponent(b.name)}`); afterRestore(r.message); }); }}>Terugzetten</button>
                <button class="btn ghost icon danger" title="Verwijderen" aria-label="Verwijderen" disabled=${!!busy} onClick=${async () => {
                  if (!await confirmDialog({ title: 'Back-up verwijderen?', danger: true, confirm: 'Verwijderen', body: html`<p>${b.name}</p>` })) return;
                  run('del', async () => { await api('DELETE', `/api/backups/${encodeURIComponent(b.name)}`); await loadBackups(); }, 'Back-up verwijderd.'); }}><${Icon} name="trash" /></button>
              </div></td></tr>`)}</tbody></table></div>`)}
        </${Section}>

        <${Section} id="logboek" title="Logboeken">
          <div class="row" style="margin-bottom:10px">
            <select style="width:auto" value=${logUnit} onChange=${(e) => { setLogUnit(e.target.value); loadLog(e.target.value).catch(fail); }}>
              <option value="all">Server en player</option><option value="server">Alleen server</option><option value="player">Alleen player</option></select>
            <button class="btn" onClick=${() => loadLog().catch(fail)}><${Icon} name="refresh" />Vernieuwen</button></div>
          <div class="log-view" tabindex="0" aria-label="Logboek">${log || 'Geen regels.'}</div>
        </${Section}>

        <${UpdateSection} />

        <${Section} id="systeem" title="Systeem">
          <dl class="facts" style="margin-bottom:14px">
            <dt>Softwareversie</dt><dd>${info.version}</dd><dt>Apparaat</dt><dd>${info.model}</dd>
            <dt>Besturingssysteem</dt><dd>${info.os} (${info.arch})</dd><dt>Kernel</dt><dd>${info.kernel}</dd>
            <dt>Python</dt><dd>${info.python}</dd><dt>Gegevensmap</dt><dd>${info.data_dir}</dd>
            <dt>Configuratie</dt><dd>${info.config_path}</dd><dt>Max. upload</dt><dd>${info.max_upload_mb} MB</dd>
          </dl>
          <button class="btn danger" disabled=${!priv} onClick=${async () => {
            if (!await confirmDialog({ title: 'Raspberry Pi herstarten?', danger: true, confirm: 'Herstarten',
              body: html`<p>Het scherm is ongeveer een minuut zwart. Daarna start alles automatisch weer.</p>` })) return;
            run('reboot', () => api('POST', '/api/system/reboot'), 'De Raspberry Pi herstart.'); }}><${Icon} name="power" />Raspberry Pi herstarten</button>
        </${Section}>

        <${Section} id="account" title="Account" note=${`Ingelogd als ${user}.`}>
          <form class="stack" style="gap:14px" onSubmit=${(e) => { e.preventDefault();
            if (pw.new !== pw.new2) { fail(new Error('De nieuwe wachtwoorden zijn niet gelijk.')); return; }
            run('pw', async () => { await api('POST', '/api/auth/password', { current: pw.current, new: pw.new }); setPw({ current: '', new: '', new2: '' }); }, 'Wachtwoord gewijzigd.'); }}>
            <div class="form-grid">
              <label class="field">Huidig wachtwoord<input type="password" autocomplete="current-password" value=${pw.current} onInput=${(e) => setPw({ ...pw, current: e.target.value })} required /></label>
              <label class="field">Nieuw wachtwoord<input type="password" autocomplete="new-password" minlength="8" value=${pw.new} onInput=${(e) => setPw({ ...pw, new: e.target.value })} required /></label>
              <label class="field">Nieuw wachtwoord herhalen<input type="password" autocomplete="new-password" minlength="8" value=${pw.new2} onInput=${(e) => setPw({ ...pw, new2: e.target.value })} required /></label>
            </div>
            <div class="row"><button class="btn primary" disabled=${busy === 'pw'}>Wachtwoord wijzigen</button>
              <button type="button" class="btn ghost" onClick=${onLogout}><${Icon} name="logout" />Uitloggen</button></div>
          </form>
        </${Section}>
      </div>
    </div>
  </div>`;
}

// ---------------------------------------------------------------------------
// Applicatie-shell
// ---------------------------------------------------------------------------
const NAV = [
  ['#/', 'Dashboard', 'screen'], ['#/media', 'Media', 'media'], ['#/playlists', 'Playlists', 'list'],
  ['#/schermen', 'Schermen', 'players'], ['#/instellingen', 'Instellingen', 'gear'],
];

function App() {
  const [auth, setAuth] = useState(null);
  const hash = useHash();
  const check = async () => {
    try {
      const a = await api('GET', '/api/auth/state');
      CSRF = a.csrf || null;
      setAuth(a);
    } catch (e) { setAuth({ error: e.message }); }
  };
  useEffect(() => {
    check();
    const on = () => check();
    listeners.auth.add(on);
    return () => listeners.auth.delete(on);
  }, []);

  if (!auth) return html`<p class="boot">Verbinden met de signage-server…</p>`;
  if (auth.error) return html`<main class="auth"><div class="panel auth-card"><h1>Geen verbinding</h1><p>${auth.error}</p>
    <button class="btn primary" onClick=${check}>Opnieuw proberen</button></div></main>`;
  if (auth.setup_required) return html`<${AuthScreen} mode="setup" systemName=${auth.system_name} onDone=${check} /><${Toasts} />`;
  if (!auth.logged_in) return html`<${AuthScreen} mode="login" systemName=${auth.system_name} onDone=${check} /><${Toasts} />`;

  const logout = async () => { await api('POST', '/api/auth/logout').catch(() => {}); CSRF = null; check(); };
  const parts = hash.replace(/^#\/?/, '').split('/');
  const route = parts[0] || '';
  const active = '#/' + route;
  let page;
  if (route === 'media') page = html`<${MediaPage} />`;
  else if (route === 'playlists') page = html`<${PlaylistsPage} selected=${parts[1] || null} />`;
  else if (route === 'schermen') page = html`<${PlayersPage} />`;
  else if (route === 'instellingen') page = html`<${SettingsPage} user=${auth.user} onLogout=${logout} />`;
  else page = html`<${Dashboard} />`;

  return html`<div class="shell">
    <aside class="rail">
      <div class="brand"><${BrandMark} /><div><div class="brand-name">${auth.system_name}</div><div class="brand-sub">Signage beheer</div></div></div>
      <nav class="nav" aria-label="Hoofdmenu">${NAV.map(([href, label, icon]) => html`<a href=${href}
        aria-current=${active === href ? 'page' : undefined}
        onClick=${(e) => { if (editorDirty && !confirm('Je hebt niet-opgeslagen wijzigingen in een playlist. Toch weggaan?')) e.preventDefault(); else editorDirty = false; }}>
        <${Icon} name=${icon} /><span>${label}</span></a>`)}</nav>
      <div class="rail-foot">Ingelogd als ${auth.user}<br /><button class="btn small ghost" onClick=${logout}><${Icon} name="logout" />Uitloggen</button></div>
    </aside>
    <main class="main">
      <div class="mobile-top"><div class="row"><${BrandMark} /><strong>${auth.system_name}</strong></div>
        <button class="btn small ghost" onClick=${logout} aria-label="Uitloggen"><${Icon} name="logout" /></button></div>
      ${page}
    </main>
    <${Toasts} /><${DialogHost} />
  </div>`;
}

render(html`<${App} />`, document.getElementById('app'));
