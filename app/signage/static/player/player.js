/* Signage-player (browserweergave).
 *
 * Toestandsmachine
 *   IDLE ─▶ LOADING_NEXT ─▶ STARTING_NEXT ─▶ PLAYING ─(klaar)─▶ PREPARE_TRANSITION ─▶ PLAYING_TRANSITION
 *              ▲                                  │                                         │
 *              └────────────(geen overgang)────────┴──────────(overgang afgelopen/fout)───────┘
 *   PLAYING ─(laatste item, 'eenmaal en laten staan')─▶ HOLD
 *   (niets afspeelbaar) ─▶ STANDBY ─(30 s of nieuwe configuratie)─▶ LOADING_NEXT
 *   ERROR: een item of overgang faalt; wordt gelogd en overgeslagen.
 *
 * Regels
 * - Elk item wordt in een eigen laag voorbereid terwijl het vorige nog speelt (voorladen).
 *   Pas als de nieuwe laag klaar is (afbeelding gedecodeerd, eerste videoframe geladen,
 *   pagina geladen) wordt hij boven de oude gezet; daarna verdwijnt de oude. Zo is er
 *   geen zwart beeld tussen items.
 * - Een overgang start alleen vanuit PLAYING (dus nooit na een overgang) en speelt in een
 *   eigen, vooraf geladen laag volledig af. Het volgende item verschijnt pas daarna.
 * - Elke asynchrone stap onthoudt de 'generatie'. Wisselt de playlist intussen, dan worden
 *   verouderde stappen genegeerd: geen raceconditions.
 * - HTML draait in een iframe vanaf een andere origin (localhost i.p.v. 127.0.0.1), zodat
 *   een pagina de player niet kan beïnvloeden. Een HTML-item eindigt altijd na zijn duur.
 */
'use strict';

const TEST = new URLSearchParams(location.search).has('test');
const POLL_MS = 2000;
const STATUS_MS = 3000;
const PREPARE_TIMEOUT = 20000;
const TRANSITION_READY_TIMEOUT = 3000;
const STANDBY_RETRY = 30000;
const HTML_READY_MAX = 4000;

const S = {
  IDLE: 'IDLE', LOADING_NEXT: 'LOADING_NEXT', STARTING_NEXT: 'STARTING_NEXT', PLAYING: 'PLAYING',
  PREPARE_TRANSITION: 'PREPARE_TRANSITION', PLAYING_TRANSITION: 'PLAYING_TRANSITION',
  HOLD: 'HOLD', STANDBY: 'STANDBY', ERROR: 'ERROR',
};

const stage = document.getElementById('stage');
let state = S.IDLE;
let gen = 0;
let cfg = null;           // actieve configuratie
let pending = null;       // bijgewerkte versie van dezelfde playlist, actief na huidig item
let items = [];
let fallbackActive = false;
let index = -1;
let current = null;       // { item, slot }
let preload = null;       // { key, item, promise }
let tslots = {};          // overgang-id -> { src, slot, ready }
let activeTransition = null;
let failures = 0;
let timers = [];
let z = 1;
let lastRevision = null;
const errors = [];
const history = [];
const slotsByEl = new WeakMap();
const videoStats = { dropped: 0, total: 0 };

// ---------------------------------------------------------------------------
// Hulpfuncties
// ---------------------------------------------------------------------------
function log(msg) {
  errors.push(new Date().toTimeString().slice(0, 8) + ' ' + msg);
  while (errors.length > 10) errors.shift();
  console.warn('[signage]', msg);
}
function setState(s, name) {
  state = s;
  history.push({ t: performance.now(), state: s, name: name || null });
  if (history.length > 500) history.shift();
}
function clearTimers() { timers.forEach(clearTimeout); timers = []; }
function later(ms, fn) { const t = setTimeout(fn, ms); timers.push(t); return t; }
function frames(n) {
  return new Promise((res) => { const step = () => (n-- <= 0 ? res() : requestAnimationFrame(step)); requestAnimationFrame(step); });
}
function timeout(promise, ms, what) {
  let t;
  const guard = new Promise((_, rej) => { t = setTimeout(() => rej(new Error(what + ': time-out')), ms); });
  return Promise.race([promise, guard]).finally(() => clearTimeout(t));
}
function keyOf(item) { return item ? item.media_id + '|' + item.src + '|' + item.scale : null; }
function fitClass(mode) { return 'fit-' + (mode === 'contain' || mode === 'fill' ? mode : 'cover'); }

function stageSize() {
  const rot = cfg ? Number(cfg.rotation) || 0 : 0;
  const w = window.innerWidth, h = window.innerHeight;
  return rot === 90 || rot === 270 ? { w: h, h: w } : { w, h };
}

// ---------------------------------------------------------------------------
// Lagen (slots)
// ---------------------------------------------------------------------------
function placeIframe(slot) {
  const { iframe, design, scale } = slot;
  if (!design) return;
  const st = stageSize();
  const sx = st.w / design.w, sy = st.h / design.h;
  let tf;
  if (scale === 'fill') {
    tf = `scale(${sx}, ${sy})`;
  } else {
    const s = scale === 'contain' ? Math.min(sx, sy) : Math.max(sx, sy);
    const tx = (st.w - design.w * s) / 2, ty = (st.h - design.h * s) / 2;
    tf = `translate(${tx}px, ${ty}px) scale(${s})`;
  }
  iframe.style.width = design.w + 'px';
  iframe.style.height = design.h + 'px';
  iframe.style.transformOrigin = '0 0';
  iframe.style.transform = tf;
}

function makeSlot(entry, opts) {
  const el = document.createElement('div');
  el.className = 'slot';
  el.style.zIndex = String(++z);
  const slot = { el, kind: entry.kind, entry, ready: false, destroyed: false };
  slotsByEl.set(el, slot);
  let ready;
  if (entry.kind === 'image') {
    const img = new Image();
    img.className = fitClass(entry.scale);
    img.decoding = 'async';
    img.alt = '';
    slot.media = img;
    el.appendChild(img);
    img.src = entry.src;
    ready = img.decode();
  } else if (entry.kind === 'video') {
    const v = document.createElement('video');
    v.className = fitClass(entry.scale);
    v.muted = !!opts.muted;
    v.volume = Math.max(0, Math.min(1, (opts.volume == null ? 80 : opts.volume) / 100));
    v.playsInline = true;
    v.preload = 'auto';
    v.controls = false;
    v.disablePictureInPicture = true;
    v.setAttribute('disableremoteplayback', '');
    slot.media = v;
    el.appendChild(v);
    ready = new Promise((res, rej) => {
      v.addEventListener('loadeddata', res, { once: true });
      v.addEventListener('error', () => rej(new Error('video kan niet worden geladen')), { once: true });
    });
    v.src = entry.src;
    v.load();
  } else if (entry.kind === 'html') {
    const f = document.createElement('iframe');
    const h = entry.html || {};
    slot.iframe = f;
    slot.scale = entry.scale;
    slot.design = h.width && h.height ? { w: Number(h.width), h: Number(h.height) } : null;
    if (!slot.design) el.classList.add('free');
    f.setAttribute('sandbox', 'allow-scripts allow-same-origin allow-forms');
    f.setAttribute('scrolling', 'no');
    f.setAttribute('allow', 'autoplay');
    f.setAttribute('referrerpolicy', 'no-referrer');
    el.appendChild(f);
    placeIframe(slot);
    // Klaar bij 'load', maar uiterlijk na 4 s tonen: een pagina die (zonder internet) op
    // externe bronnen blijft wachten mag de playlist niet ophouden.
    ready = new Promise((res) => {
      f.addEventListener('load', res, { once: true });
      setTimeout(res, HTML_READY_MAX);
    });
    f.src = h.src || entry.src;
  } else {
    ready = Promise.reject(new Error('onbekend mediatype'));
  }
  stage.appendChild(el);
  slot.promise = timeout(ready, PREPARE_TIMEOUT, entry.name || 'item').then(() => { slot.ready = true; return slot; },
    (err) => { destroySlot(slot); throw err; });
  return slot;
}

function showSlot(slot) { slot.el.style.zIndex = String(++z); slot.el.classList.add('on'); }
function hideSlot(slot) { slot.el.classList.remove('on'); }

function destroySlot(slot) {
  if (!slot || slot.destroyed) return;
  slot.destroyed = true;
  if (slot.kind === 'video' && slot.media) {
    const q = slot.media.getVideoPlaybackQuality ? slot.media.getVideoPlaybackQuality() : null;
    if (q && !slot.counted) { videoStats.dropped += q.droppedVideoFrames; videoStats.total += q.totalVideoFrames; }
    slot.media.pause();
    slot.media.removeAttribute('src');
    slot.media.load();
  }
  if (slot.iframe) slot.iframe.src = 'about:blank';
  slot.el.remove();
}

function prepare(item, conf) {
  const c = conf || cfg || {};
  return makeSlot(item, { muted: !!((c.playlist || {}).mute), volume: c.volume == null ? 80 : c.volume }).promise;
}

// Overgangen: elke overgangsvideo heeft een vaste, vooraf geladen laag.
function syncTransitions(conf) {
  const wanted = (conf && conf.transitions) || {};
  const keep = new Set();
  [cfg, pending, conf].forEach((c) => { if (c && c.transitions) Object.keys(c.transitions).forEach((k) => keep.add(k)); });
  for (const id of Object.keys(tslots)) {
    if (!keep.has(id) && (!activeTransition || activeTransition.id !== id)) { destroySlot(tslots[id].slot); delete tslots[id]; }
  }
  const muted = !!((conf.playlist || {}).mute);
  const vol = Math.max(0, Math.min(1, (conf.volume == null ? 80 : conf.volume) / 100));
  for (const [id, t] of Object.entries(wanted)) {
    if (tslots[id] && tslots[id].src === t.src) {
      const m = tslots[id].slot.media;   // volume en geluid volgen gewijzigde instellingen
      if (m) { m.muted = muted; m.volume = vol; }
      continue;
    }
    if (tslots[id]) destroySlot(tslots[id].slot);
    const slot = makeSlot({ kind: 'video', src: t.src, name: 'overgang ' + (t.name || ''), scale: conf.scale_default },
      { muted: !!((conf.playlist || {}).mute), volume: conf.volume });
    const entry = { id, src: t.src, slot, name: t.name, duration: t.video_duration, failed: false };
    entry.ready = slot.promise.catch((e) => { entry.failed = true; log('Overgang ' + (t.name || id) + ' niet beschikbaar: ' + e.message); throw e; });
    entry.ready.catch(() => {});
    tslots[id] = entry;
  }
}

function rearmTransition(t) {
  if (!t || t.slot.destroyed) return;
  hideSlot(t.slot);
  try { t.slot.media.pause(); t.slot.media.currentTime = 0; } catch (e) { /* genegeerd */ }
}

// ---------------------------------------------------------------------------
// Volgorde
// ---------------------------------------------------------------------------
/* Bepaal het volgende item zonder iets te wijzigen. */
function peekNext() {
  let conf = cfg, list = items, idx = index, fb = fallbackActive;
  if (pending) {
    const curId = current && current.item.media_id;
    const nlist = pending.items || [];
    conf = pending;
    list = nlist;
    fb = false;
    idx = (index >= 0 && index < nlist.length && nlist[index].media_id === curId) ? index : -1;
  }
  if (!list.length) return { type: 'standby' };
  let nxt = idx + 1;
  if (nxt >= list.length) {
    const mode = (conf.playlist || {}).repeat_mode || 'loop';
    if (mode === 'once_hold_last' && !fb) return { type: 'hold' };
    if (mode === 'once_then_default' && !fb && (conf.fallback_items || []).length) {
      return { type: 'item', conf, list: conf.fallback_items, idx: 0, fb: true };
    }
    nxt = 0;
  }
  return { type: 'item', conf, list, idx: nxt, fb };
}

function commit(p) {
  if (p.conf !== cfg) { cfg = p.conf; applyRotation(); }
  if (pending && p.conf === pending) pending = null;
  items = p.list;
  index = p.idx;
  fallbackActive = p.fb;
}

function schedulePreload(opts) {
  const p = peekNext();
  if (p.type !== 'item') return;
  const item = p.list[p.idx];
  const lead = HTML_READY_MAX + 1000;
  if (item.kind === 'html' && opts && opts.htmlLater > lead) {
    const g = gen;
    later(opts.htmlLater - lead, () => { if (g === gen && state === S.PLAYING) schedulePreload(); });
    return;
  }
  const key = keyOf(item);
  if (preload && preload.key === key) return;
  cancelPreload();
  // Voorbereiden met de instellingen (volume, mute) van de playlist waar het item bij hoort.
  const promise = prepare(item, p.conf);
  promise.catch(() => {});
  preload = { key, item, promise };
}

function cancelPreload() {
  if (!preload) return;
  preload.promise.then(destroySlot, () => {});
  preload = null;
}

// ---------------------------------------------------------------------------
// Toestandsovergangen
// ---------------------------------------------------------------------------
async function advance(g) {
  setState(S.LOADING_NEXT);
  for (let attempt = 0; attempt < 1000; attempt++) {
    if (g !== gen) return;
    const p = peekNext();
    if (p.type === 'standby') { showStandby(g, 'lege of geen playlist'); return; }
    if (p.type === 'hold') { setState(S.HOLD, current && current.item.name); return; }
    commit(p);
    const item = items[index];
    let promise;
    if (preload && preload.key === keyOf(item)) { promise = preload.promise; preload = null; } else { cancelPreload(); promise = prepare(item); }
    let slot;
    try {
      slot = await promise;
    } catch (e) {
      if (g !== gen) return;
      setState(S.ERROR, item.name);
      log(`Kan '${item.name}' niet tonen: ${e.message}`);
      failures++;
      if (failures >= items.length) { showStandby(g, 'geen afspeelbare items'); return; }
      setState(S.LOADING_NEXT);
      continue;
    }
    if (g !== gen) { destroySlot(slot); return; }
    if (await startNext(g, item, slot)) return;
    failures++;
    if (failures >= items.length) { showStandby(g, 'geen afspeelbare items'); return; }
  }
}

async function startNext(g, item, slot) {
  setState(S.STARTING_NEXT, item.name);
  // Luisteraars vóór het starten koppelen: een heel korte video kan anders al afgelopen
  // zijn voordat we luisteren. itemDone() negeert alles buiten PLAYING.
  let endedEarly = false;
  if (slot.kind === 'video') {
    slot.media.addEventListener('ended', () => { endedEarly = true; itemDone(g); }, { once: true });
    slot.media.addEventListener('error', () => {
      if (g === gen && state === S.PLAYING) { log(`Video '${item.name}' gaf een fout tijdens het afspelen.`); itemDone(g); }
    }, { once: true });
  }
  showSlot(slot);
  if (slot.kind === 'video') {
    try { await slot.media.play(); } catch (e) {
      if (g !== gen) { destroySlot(slot); return true; }
      log(`Video '${item.name}' start niet: ${e.message}`);
      destroySlot(slot);
      return false;
    }
  }
  await frames(2);            // nieuwe laag is getekend voordat de oude verdwijnt
  if (g !== gen) { destroySlot(slot); return true; }
  const old = current;
  current = { item, slot, started: performance.now() };
  if (old && old.slot !== slot) destroySlot(old.slot);
  if (activeTransition) { rearmTransition(activeTransition); activeTransition = null; }
  hideFallback();
  clearTimers();
  setState(S.PLAYING, item.name);
  if (slot.kind === 'video') {
    if (endedEarly || slot.media.ended) { later(0, () => itemDone(g)); }
    const vd = Number(item.video_duration) || 0;
    later(vd ? (vd * 1.5 + 30) * 1000 : 30 * 60 * 1000, () => {
      if (g === gen && state === S.PLAYING) { log(`Video '${item.name}' loopt vast; volgende item.`); itemDone(g); }
    });
  } else {
    later(Math.max(1, Number(item.duration) || 10) * 1000, () => itemDone(g));
  }
  // Volgend item alvast voorbereiden. Een HTML-pagina pas kort voor het einde, zodat haar
  // eigen animaties en timers niet al lopen terwijl ze nog onzichtbaar is.
  later(300, () => { if (g === gen) schedulePreload({ htmlLater: currentRemaining(item) }); });
  return true;
}

function currentRemaining(item) {
  const secs = item.kind === 'video' ? Number(item.video_duration) || 0 : Math.max(1, Number(item.duration) || 10);
  return secs * 1000 - 300;
}

async function itemDone(g) {
  if (g !== gen || state !== S.PLAYING) return;     // alleen vanuit PLAYING: geen dubbele afhandeling
  failures = 0;
  clearTimers();
  const p = peekNext();
  if (p.type === 'hold') { setState(S.HOLD, current.item.name); return; }
  const tid = current.item.transition;
  const t = tid ? tslots[tid] : null;
  if (!t) { advance(g); return; }
  setState(S.PREPARE_TRANSITION, t.name);
  schedulePreload();
  try {
    await timeout(t.ready, TRANSITION_READY_TIMEOUT, 'overgang');
  } catch (e) {
    if (g !== gen) return;
    log(`Overgang '${t.name || tid}' overgeslagen: ${e.message}`);
    advance(g);
    return;
  }
  if (g !== gen) return;
  const v = t.slot.media;
  try { v.currentTime = 0; } catch (e) { /* genegeerd */ }
  showSlot(t.slot);
  try {
    await v.play();
  } catch (e) {
    if (g !== gen) return;
    rearmTransition(t);
    log(`Overgang '${t.name || tid}' start niet: ${e.message}`);
    advance(g);
    return;
  }
  if (g !== gen) { rearmTransition(t); return; }
  activeTransition = t;
  setState(S.PLAYING_TRANSITION, t.name);
  // De overgangsvideo blijft bestaan; alle luisteraars en timers van deze ronde worden
  // daarna verwijderd, zodat er bij langdurig afspelen niets ophoopt.
  const ac = new AbortController();
  let tmo;
  const finished = new Promise((res) => {
    v.addEventListener('ended', () => res('ok'), { signal: ac.signal });
    v.addEventListener('error', () => res('fout'), { signal: ac.signal });
    const td = Number(t.duration) || 30;
    tmo = setTimeout(() => res('time-out'), (td + 5) * 1000);
  });
  const how = await finished;
  ac.abort();
  clearTimeout(tmo);
  if (g !== gen || state !== S.PLAYING_TRANSITION) return;
  if (how !== 'ok') log(`Overgang '${t.name || tid}' afgebroken (${how}).`);
  // De overgang blijft op het laatste frame staan tot het volgende item zichtbaar is.
  advance(g);
}

function showStandby(g, reason) {
  clearTimers();
  cancelPreload();
  setState(S.STANDBY, reason);
  const old = current;
  current = null;
  const src = cfg && cfg.standby_src;
  const done = () => {
    if (old) destroySlot(old.slot);
    if (activeTransition) { rearmTransition(activeTransition); activeTransition = null; }
  };
  if (src) {
    makeSlot({ kind: 'image', src, scale: 'contain', name: 'standby' }, {}).promise.then((slot) => {
      if (g !== gen) { destroySlot(slot); return; }
      showSlot(slot);
      current = { item: { media_id: null, name: 'standby', kind: 'image' }, slot, standby: true };
      frames(2).then(done);
      hideFallback();
    }, () => { done(); showFallback((cfg && cfg.system_name) || 'Signage'); });
  } else {
    done();
    showFallback((cfg && cfg.system_name) || 'Signage');
  }
  later(STANDBY_RETRY, () => { if (g === gen && items.length) restart(cfg); });
}

function showFallback(text) {
  document.getElementById('fallback-text').textContent = text;
  document.getElementById('fallback').hidden = false;
}
function hideFallback() { document.getElementById('fallback').hidden = true; }

function applyRotation() {
  const r = cfg ? Number(cfg.rotation) || 0 : 0;
  stage.className = r ? 'r' + r : '';
  Array.from(stage.querySelectorAll('.slot')).forEach((el) => {
    const s = slotsByEl.get(el);
    if (s && s.design) placeIframe(s);
  });
}
window.addEventListener('resize', () => applyRotation());

/* Volledig opnieuw beginnen met een (nieuwe) playlist. Het huidige beeld blijft staan
   tot het eerste item van de nieuwe playlist klaar is. */
function restart(conf) {
  const g = ++gen;
  clearTimers();
  cancelPreload();
  cfg = conf;
  pending = null;
  items = (conf.items || []).slice();
  fallbackActive = false;
  index = -1;
  failures = 0;
  applyRotation();
  syncTransitions(conf);
  advance(g);
}

function onConfig(conf) {
  if (!conf || conf.revision == null || conf.revision === lastRevision) return;
  lastRevision = conf.revision;
  const curPl = cfg && cfg.playlist ? cfg.playlist.id : null;
  const newPl = conf.playlist ? conf.playlist.id : null;
  const immediate = !cfg || curPl !== newPl || !items.length ||
    [S.IDLE, S.STANDBY, S.HOLD, S.ERROR].includes(state);
  if (immediate) { restart(conf); return; }
  // Zelfde playlist, bewerkt: wordt actief na het huidige item (of de lopende overgang).
  pending = conf;
  syncTransitions(conf);
  if (Number(conf.rotation) !== Number(cfg.rotation)) { cfg = Object.assign({}, cfg, { rotation: conf.rotation }); applyRotation(); }
  if (preload) {
    const p = peekNext();
    if (p.type !== 'item' || keyOf(p.list[p.idx]) !== preload.key) { cancelPreload(); if (state === S.PLAYING || state === S.PLAYING_TRANSITION) schedulePreload(); }
  }
}

// ---------------------------------------------------------------------------
// Communicatie met de player-daemon
// ---------------------------------------------------------------------------
async function poll() {
  try {
    const r = await fetch('/state', { cache: 'no-store' });
    if (r.ok) onConfig(await r.json());
  } catch (e) { /* daemon even niet bereikbaar: doorspelen */ }
  setTimeout(poll, POLL_MS);
}

function snapshot() {
  const cur = current && !current.standby ? current.item : null;
  let vs = { dropped: videoStats.dropped, total: videoStats.total };
  if (current && current.slot.kind === 'video' && current.slot.media.getVideoPlaybackQuality) {
    const q = current.slot.media.getVideoPlaybackQuality();
    vs = { dropped: vs.dropped + q.droppedVideoFrames, total: vs.total + q.totalVideoFrames };
  }
  return {
    phase: state,
    current: cur ? {
      media_id: state === S.PLAYING_TRANSITION ? null : cur.media_id,
      name: state === S.PLAYING_TRANSITION ? 'Overgang: ' + (activeTransition && activeTransition.name || '') : cur.name,
      kind: cur.kind, index, total: items.length, transition: state === S.PLAYING_TRANSITION,
    } : null,
    errors: errors.slice(),
    viewport: { w: window.innerWidth, h: window.innerHeight, dpr: window.devicePixelRatio,
      screen_w: Math.round(screen.width * window.devicePixelRatio), screen_h: Math.round(screen.height * window.devicePixelRatio) },
    video_stats: vs,
    revision: cfg && cfg.revision,
  };
}

async function reportStatus() {
  try {
    await fetch('/status', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(snapshot()) });
  } catch (e) { /* genegeerd */ }
  setTimeout(reportStatus, STATUS_MS);
}

// ---------------------------------------------------------------------------
// Testinstrumentatie (?test): telt frames zonder zichtbare, geladen laag.
// ---------------------------------------------------------------------------
if (TEST) {
  const probe = { frames: 0, blank: 0, blankAt: [], started: false };
  window.__signage = { probe, history, errors, state: () => state, snapshot, gen: () => gen };
  const check = () => {
    const visible = Array.from(stage.querySelectorAll('.slot.on')).some((el) => {
      const s = slotsByEl.get(el);
      if (!s || !s.ready) return false;
      if (s.kind === 'video') return s.media.readyState >= 2;
      return true;
    });
    if (visible) probe.started = true;
    if (probe.started) {
      probe.frames++;
      if (!visible && document.getElementById('fallback').hidden) { probe.blank++; probe.blankAt.push(state); }
    }
    // Tijdens een overgang moet de overgang de bovenste zichtbare laag zijn.
    if (state === S.PLAYING_TRANSITION && activeTransition) {
      const on = Array.from(stage.querySelectorAll('.slot.on'));
      const top = on.sort((a, b) => Number(b.style.zIndex) - Number(a.style.zIndex))[0];
      probe.transitionFrames = (probe.transitionFrames || 0) + 1;
      if (top !== activeTransition.slot.el) probe.transitionHidden = (probe.transitionHidden || 0) + 1;
    }
    requestAnimationFrame(check);
  };
  requestAnimationFrame(check);
}

poll();
reportStatus();
