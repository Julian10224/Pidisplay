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
  return html`<span style="display:grid;place-items:center;width:100%;height:100%;color:#8592A4">
    <${Icon} name=${media.kind === 'video' ? 'video' : 'image'} /></span>`;
}

function MediaPreview({ media, onClose }) {
  return html`<${Modal} title=${media.name} wide onClose=${onClose}>
    <div class="preview-stage">
      ${media.kind === 'video'
        ? html`<video src=${`/api/media/${media.id}/playback`} controls autoplay muted playsinline></video>`
        : html`<img src=${`/api/media/${media.id}/playback`} alt=${media.name} />`}
    </div>
    <p class="muted small">${media.width}×${media.height} · ${media.codec} · ${fmtBytes(media.size)}
      ${media.kind === 'video' ? ` · ${fmtDur(media.duration)}` : ''}</p>
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
function Dashboard() {
  const [st, setSt] = useState(null);
  const [playlists, setPlaylists] = useState([]);
  const [strip, setStrip] = useState(null);
  const [busy, setBusy] = useState(false);
  const loadStatus = async () => setSt(await api('GET', '/api/status'));
  const loadPlaylists = async () => setPlaylists((await api('GET', '/api/playlists')).playlists);
  usePoll(loadStatus, 4000);
  useEffect(() => { loadPlaylists().catch(fail); }, []);

  const local = st && st.players.find((p) => p.kind === 'local');
  const eff = local && local.effective_playlist;
  const effKey = eff ? `${eff.id}` : '';
  const revision = local && local.status && local.status.revision;
  useEffect(() => {
    if (!eff) { setStrip(null); return; }
    api('GET', `/api/playlists/${eff.id}`).then((r) => setStrip(r.playlist)).catch(() => setStrip(null));
  }, [effKey, revision]);

  if (!st) return html`<p class="muted">Status laden…</p>`;

  const status = (local && local.status) || {};
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
          ${cur && cur.media_id
            ? html`<img src=${`/api/media/${cur.media_id}/thumb`} alt=${'Nu op het scherm: ' + cur.name} onError=${(e) => { e.target.style.visibility = 'hidden'; }} />`
            : html`<div class="empty">${!local || !local.online ? 'De player meldt zich niet. Controleer of het scherm is aangesloten en herstart de player.'
                : standby ? 'Het standby-scherm wordt getoond: er is niets om af te spelen.' : 'Player start op…'}</div>`}
        </div>
        <div class="monitor-bar">
          <span class=${'tally' + (cur ? '' : ' off')}><span class="dot"></span>${cur ? 'Op het scherm' : standby ? 'Standby' : 'Geen beeld'}</span>
          <span class="spacer"></span>
          ${cur && html`<span>${cur.index + 1} / ${cur.total}</span>`}
        </div>
        ${strip && strip.items.length > 0 && html`<div class="filmstrip" aria-label="Volgorde in de playlist">
          ${strip.items.map((it, i) => html`<div class=${'frame' + (cur && cur.index === i && cur.media_id === it.media.id ? ' now' : '')} key=${it.item_id} title=${it.media.name}>
            <${Thumb} media=${it.media} /></div>`)}</div>`}
      </div>

      <div class="panel now-panel">
        <div>
          <div class="muted small">Nu afgespeeld</div>
          <div class="now-title">${cur ? cur.name : '—'}</div>
        </div>
        <dl class="facts">
          <dt>Playlist</dt><dd>${eff ? eff.name : 'geen'}</dd>
          <dt>Player</dt><dd>${local && local.online
            ? html`<span class="badge ok"><span class="dot"></span>actief</span>`
            : html`<span class="badge alert"><span class="dot"></span>offline</span>`}</dd>
          <dt>Laatst gezien</dt><dd>${local ? fmtAgo(local.last_contact) : '–'}</dd>
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
function MediaPage() {
  const [data, setData] = useState(null);
  const [queue, setQueue] = useState([]);
  const [over, setOver] = useState(false);
  const [q, setQ] = useState('');
  const [filter, setFilter] = useState('all');
  const [sort, setSort] = useState('new');
  const [preview, setPreview] = useState(null);
  const [rename, setRename] = useState(null);
  const fileRef = useRef();
  const running = useRef(false);
  const queueRef = useRef([]);

  const load = async () => setData(await api('GET', '/api/media'));
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

  const addFiles = (files) => {
    if (!data) return;
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
      const names = e.data.playlists.map((p) => p.name).join(', ');
      const force = await confirmDialog({ title: 'Bestand is in gebruik', danger: true, confirm: 'Toch verwijderen',
        body: html`<p>Dit bestand staat in: <strong>${names}</strong>. Het wordt ook uit die playlists gehaald.</p>
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
    if (filter === 'problem') xs = xs.filter((m) => m.status === 'warning' || m.missing);
    const by = { new: (a, b) => b.created_at - a.created_at, name: (a, b) => a.name.localeCompare(b.name, 'nl'),
      size: (a, b) => b.size - a.size, duration: (a, b) => (b.duration || 0) - (a.duration || 0) }[sort];
    return [...xs].sort(by);
  }, [data, q, filter, sort]);

  return html`<div>
    <div class="page-head"><div><h1>Media</h1>
      <p>Afbeeldingen (PNG, JPEG, WebP) en video's (bij voorkeur MP4 met H.264 en AAC, maximaal 1080p30).</p></div></div>

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
        <option value="video">Video's</option><option value="problem">Met waarschuwing</option></select>
      <select aria-label="Sorteren" value=${sort} onChange=${(e) => setSort(e.target.value)}>
        <option value="new">Nieuwste eerst</option><option value="name">Naam</option>
        <option value="size">Grootte</option><option value="duration">Duur</option></select>
      <span class="spacer"></span>
      ${data && html`<span class="muted small">${list.length} van ${data.media.length}</span>`}
    </div>

    ${!data ? html`<p class="muted">Media laden…</p>` : data.media.length === 0
      ? html`<div class="panel empty-state"><${Icon} name="media" /><h2>Nog geen media</h2>
          <p>Upload afbeeldingen of video's om ze in een playlist te zetten.</p></div>`
      : html`<div class="media-grid">${list.map((m) => html`<article class=${'tile' + (m.missing ? ' missing' : m.status === 'warning' ? ' warn' : '')} key=${m.id}>
          <button class="tile-thumb" onClick=${() => setPreview(m)} aria-label=${'Voorbeeld van ' + m.name}>
            <${Thumb} media=${m} />
            <span class="kind"><${Icon} name=${m.kind} />${m.kind === 'video' ? fmtDur(m.duration) : 'Afbeelding'}</span>
          </button>
          <div class="tile-body">
            <div class="tile-name">${m.name}</div>
            <div class="tile-meta">${m.width}×${m.height} · ${m.format.toUpperCase()} · ${fmtBytes(m.size)}</div>
            <div class="row" style="gap:6px">
              ${m.missing && html`<span class="badge alert">Bestand ontbreekt</span>`}
              ${m.status === 'warning' && html`<span class="badge live" title=${m.warning}>Waarschuwing</span>`}
              ${m.playlists.length > 0 && html`<span class="badge signal" title=${m.playlists.map((p) => p.name).join(', ')}>In ${m.playlists.length} playlist${m.playlists.length > 1 ? 's' : ''}</span>`}
            </div>
            ${m.warning && html`<p class="small muted">${m.warning}</p>`}
            <div class="tile-actions">
              <button class="btn ghost icon" title="Voorbeeld" aria-label="Voorbeeld" onClick=${() => setPreview(m)}><${Icon} name="eye" /></button>
              <button class="btn ghost icon" title="Naam wijzigen" aria-label="Naam wijzigen" onClick=${() => setRename(m)}><${Icon} name="edit" /></button>
              <a class="btn ghost icon" title="Origineel downloaden" aria-label="Origineel downloaden" href=${`/api/media/${m.id}/download`}><${Icon} name="download" /></a>
              <span class="spacer"></span>
              <button class="btn ghost icon danger" title="Verwijderen" aria-label="Verwijderen" onClick=${() => remove(m)}><${Icon} name="trash" /></button>
            </div>
          </div></article>`)}</div>`}
    ${preview && html`<${MediaPreview} media=${preview} onClose=${() => setPreview(null)} />`}
    ${rename && html`<${Prompt} title="Naam wijzigen" label="Naam" initial=${rename.name} onClose=${() => setRename(null)}
      onSubmit=${async (v) => { await api('PATCH', `/api/media/${rename.id}`, { name: v }); toast('Naam gewijzigd.'); await load(); }} />`}
  </div>`;
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
              <span class="meta">${p.item_count} items · ${fmtDur(p.total_duration)}${p.is_default ? ' · standaard' : ''}${p.valid ? '' : ' · niet afspeelbaar'}</span>
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
  const [previewing, setPreviewing] = useState(false);
  const listRef = useRef();
  const draftRef = useRef();
  draftRef.current = draft;

  const toDraft = (p) => ({ name: p.name, repeat_mode: p.repeat_mode, mute: p.mute,
    items: p.items.map((it) => ({ key: 'i' + it.item_id, media: it.media, duration: it.duration })) });
  const load = async () => {
    const r = await api('GET', `/api/playlists/${id}`);
    setPl(r.playlist);
    setDraft(toDraft(r.playlist));
  };
  useEffect(() => {
    load().catch((e) => { fail(e); location.hash = '#/playlists'; });
    api('GET', '/api/settings').then((r) => setSettings(r.settings)).catch(() => {});
  }, [id]);

  const serial = (d) => d && JSON.stringify({ n: d.name, r: d.repeat_mode, m: d.mute, i: d.items.map((x) => [x.media.id, x.duration ?? null]) });
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
  const move = (i, delta) => {
    const j = i + delta;
    if (j < 0 || j >= draft.items.length) return;
    const items = [...draft.items];
    [items[i], items[j]] = [items[j], items[i]];
    setItems(items);
    requestAnimationFrame(() => { const h = listRef.current && listRef.current.children[j]; if (h) h.querySelector('.handle').focus(); });
  };
  const defDur = settings ? settings.default_image_duration : 10;
  const total = draft.items.reduce((s, it) => s + (it.media.kind === 'image' ? Number(it.duration || defDur) : (it.media.duration || 0)), 0);

  const save = async (activate) => {
    setBusy(true);
    try {
      await api('PUT', `/api/playlists/${id}`, { name: draft.name, repeat_mode: draft.repeat_mode, mute: draft.mute,
        items: draft.items.map((it) => ({ media_id: it.media.id, duration: it.media.kind === 'image' && it.duration !== '' ? it.duration : null })) });
      if (activate) {
        await api('POST', `/api/playlists/${id}/activate`, { target: 'default' });
        toast('Opgeslagen en geactiveerd. Het scherm schakelt binnen enkele seconden over.');
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
        <label class="check" style="align-self:end;min-height:38px"><input type="checkbox" checked=${draft.mute}
          onChange=${(e) => setDraft({ ...draft, mute: e.target.checked })} />Geluid van video's uit</label>
      </div>
      ${pl.problems.length > 0 && !dirty && html`<div class="notice warning"><${Icon} name="alert" /><span>${pl.problems.join(' ')}</span></div>`}
    </div>

    <div>
      <div class="panel-head"><h2>Volgorde</h2>
        <button class="btn" onClick=${() => setPicker(true)}><${Icon} name="plus" />Media toevoegen</button></div>
      ${draft.items.length === 0 && html`<div class="panel empty-state"><p>Deze playlist is leeg. Voeg media toe om te beginnen.</p></div>`}
      <div class="items" ref=${listRef}>
        ${draft.items.map((it, i) => html`<div class=${'item' + (it.media.missing ? ' bad' : '')} key=${it.key}>
          <span class="pos">${i + 1}</span>
          <button class="handle" title="Slepen om te verplaatsen (of pijltjestoetsen)" aria-label=${`Verplaats ${it.media.name}, positie ${i + 1}`}
            onKeyDown=${(e) => { if (e.key === 'ArrowUp') { e.preventDefault(); move(i, -1); } if (e.key === 'ArrowDown') { e.preventDefault(); move(i, 1); } }}>
            <${Icon} name="grip" /></button>
          <div class="thumb"><${Thumb} media=${it.media} /></div>
          <div class="info"><div class="name" title=${it.media.name}>${it.media.name}</div>
            <div class="muted small">${it.media.kind === 'video' ? 'Video' : 'Afbeelding'}${it.media.missing ? ' · bestand ontbreekt' : ''}${it.media.status === 'warning' ? ' · waarschuwing' : ''}</div></div>
          <div class="dur">${it.media.kind === 'image'
            ? html`<input type="number" min="1" max="86400" step="1" placeholder=${String(defDur)} aria-label="Weergaveduur in seconden"
                value=${it.duration ?? ''} onInput=${(e) => { const items = [...draft.items]; items[i] = { ...it, duration: e.target.value === '' ? null : Number(e.target.value) }; setItems(items); }} /> s`
            : html`<span>${fmtDur(it.media.duration)} (volledig)</span>`}</div>
          <button class="btn ghost icon" aria-label=${'Verwijder ' + it.media.name + ' uit playlist'} title="Uit playlist halen"
            onClick=${() => setItems(draft.items.filter((_, j) => j !== i))}><${Icon} name="x" /></button>
        </div>`)}
      </div>
      ${draft.items.length > 0 && html`<p class="muted small" style="margin-top:8px">Lege duur = standaardduur (${String(defDur).replace('.', ',')} s). Video's spelen altijd volledig af.</p>`}
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
      onPick=${(ms) => setItems([...draft.items, ...ms.map((m) => ({ key: 'n' + Math.random().toString(36).slice(2), media: m, duration: null }))])} />`}
    ${previewing && html`<${PlaylistPreview} items=${draft.items} defDur=${defDur} onClose=${() => setPreviewing(false)} />`}
  </section>`;
}

function MediaPicker({ onPick, onClose }) {
  const [media, setMedia] = useState(null);
  const [sel, setSel] = useState([]);
  const [q, setQ] = useState('');
  useEffect(() => { api('GET', '/api/media').then((r) => setMedia(r.media.filter((m) => !m.missing))).catch(fail); }, []);
  const toggle = (m) => setSel(sel.find((x) => x.id === m.id) ? sel.filter((x) => x.id !== m.id) : [...sel, m]);
  const shown = (media || []).filter((m) => m.name.toLowerCase().includes(q.toLowerCase()));
  return html`<${Modal} title="Media toevoegen" wide onClose=${onClose} footer=${html`
      <span class="muted small" style="margin-right:auto">${sel.length} gekozen, in deze volgorde toegevoegd</span>
      <button class="btn" onClick=${onClose}>Annuleren</button>
      <button class="btn primary" disabled=${!sel.length} onClick=${() => { onPick(sel); onClose(); }}>Toevoegen</button>`}>
    <input type="search" placeholder="Zoeken op naam" aria-label="Zoeken op naam" value=${q} onInput=${(e) => setQ(e.target.value)} />
    ${!media ? html`<p class="muted">Laden…</p>` : media.length === 0
      ? html`<p>Er zijn nog geen media. <a href="#/media" onClick=${onClose}>Upload eerst bestanden</a>.</p>`
      : html`<div class="picker-grid">${shown.map((m) => {
          const n = sel.findIndex((x) => x.id === m.id);
          return html`<button class="pick" aria-pressed=${String(n >= 0)} onClick=${() => toggle(m)} key=${m.id}>
            <div class="thumb"><${Thumb} media=${m} /></div>
            <div class="n">${n >= 0 ? `${n + 1}. ` : ''}${m.name}</div></button>`;
        })}</div>`}
  </${Modal}>`;
}

function PlaylistPreview({ items, defDur, onClose }) {
  const [i, setI] = useState(0);
  const it = items[i % items.length];
  const next = useCallback(() => setI((x) => (x + 1) % items.length), [items.length]);
  useEffect(() => {
    if (!it || it.media.kind !== 'image') return undefined;
    const t = setTimeout(next, 1000 * Number(it.duration || defDur));
    return () => clearTimeout(t);
  }, [i]);
  return html`<${Modal} title="Voorbeeld van de playlist" wide onClose=${onClose}>
    <div class="preview-stage">
      ${it.media.kind === 'video'
        ? html`<video key=${it.key + i} src=${`/api/media/${it.media.id}/playback`} autoplay muted playsinline onEnded=${next} onError=${next}></video>`
        : html`<img key=${it.key + i} src=${`/api/media/${it.media.id}/playback`} alt=${it.media.name} />`}
    </div>
    <div class="row"><span>${(i % items.length) + 1} / ${items.length} · ${it.media.name}</span><span class="spacer"></span>
      <button class="btn small" onClick=${() => setI((x) => (x - 1 + items.length) % items.length)}>Vorige</button>
      <button class="btn small" onClick=${next}>Volgende</button></div>
    <p class="muted small">Het voorbeeld speelt in je browser zonder geluid. Het echte scherm kan iets andere overgangen tonen.</p>
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
          <div class="monitor-screen">${cur && cur.media_id ? html`<img src=${`/api/media/${cur.media_id}/thumb`} alt="" />`
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
            <dt>Nu</dt><dd>${cur ? `${cur.name} (${cur.index + 1}/${cur.total})` : s.state === 'standby' ? 'Standby-scherm' : '–'}</dd>
            <dt>Scherm</dt><dd>${displays.length ? displays.map((d) => html`<div>${d.connector}: ${d.connected ? `aangesloten${d.mode ? `, ${d.mode}` : ''}` : 'niet aangesloten'}</div>`) : 'onbekend'}</dd>
            ${s.version && html`<dt>Player</dt><dd>versie ${s.version}${s.mpv_restarts ? ` · ${s.mpv_restarts}× herstart` : ''}</dd>`}
            ${s.message && html`<dt>Melding</dt><dd>${s.message}</dd>`}
          </dl>
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
      <p>Dit token wordt maar één keer getoond. Installeer op de andere Raspberry Pi alleen de player:</p>
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
          ['logboek', 'Logboeken'], ['systeem', 'Systeem'], ['account', 'Account']].map(([k, v]) =>
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

        <${Section} id="weergave" title="Weergave" note="De player draait zonder desktop direct op het HDMI-scherm en start automatisch na het opstarten.">
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
