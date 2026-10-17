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
 * - Willekeurige volgorde: elke ronde is een nieuwe, geschudde volgorde waarin elk item
 *   precies één keer voorkomt; het eerste item van een ronde is nooit gelijk aan het laatste
 *   van de vorige. De volgende ronde wordt één keer bepaald, zodat voorladen en afspelen
 *   hetzelfde item kiezen.
 * - Een logo hoort bij het item: het zit in dezelfde laag en verschijnt en verdwijnt dus
 *   tegelijk met het item. Overgangen en het standby-scherm hebben geen logo.
 * - Live bediening (website): 'volgende', 'vorige' en 'naar item' starten een nieuwe
 *   generatie en laten het gekozen item via de normale weg verschijnen (voorladen, pas
 *   wisselen als het klaar is, eventueel eerst de overgang). Daarna loopt de playlist
 *   gewoon verder vanaf dat item. Opdrachten komen via /cmd van de player-daemon.
 */
'use strict';

const TEST = new URLSearchParams(location.search).has('test');
const POLL_MS = 2000;
const STATUS_MS = 3000;
const PREPARE_TIMEOUT = 20000;
const TRANSITION_READY_TIMEOUT = 5000;
// De videochip van de Pi 3B+ decodeert hooguit 2 video's tegelijk (Chromium: "Too many
// decoder instances, max=2"); een derde video valt terug op de processor en hapert.
const MAX_VIDEOS = 2;
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
let roundCache = null;    // volgende geschudde ronde: { base, src, list }
let played = [];          // afgespeelde posities { list, idx, fb } (voor 'vorige' bij willekeurig)
let lastControl = null;   // laatst uitgevoerde opdracht van de live bediening
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
  if (s === S.PLAYING || s === S.PLAYING_TRANSITION || s === S.HOLD || s === S.STANDBY) reportSoon();
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
  if (entry.logo) {
    // Logo linksboven, in dezelfde laag als het item. Een kapot logo houdt het item niet tegen.
    const lg = new Image();
    lg.className = 'logo';
    lg.alt = '';
    lg.decoding = 'async';
    lg.src = entry.logo;
    el.appendChild(lg);
    const itemReady = ready;
    ready = Promise.all([itemReady, lg.decode().catch(() => { lg.remove(); log(`Logo bij '${entry.name}' kan niet worden getoond.`); })]);
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

/* Aantal geladen video's (elke geladen video houdt een decoder van de videochip bezet). */
function liveVideos() { return stage.querySelectorAll('video[src]').length; }

// Overgangen. Een overgangsvideo wordt pas geladen als het item dat ervoor speelt begint
// (armTransition) en direct na gebruik weer vrijgegeven (releaseTransition), zodat er nooit
// meer dan 2 video's tegelijk een decoder bezetten.
function syncTransitions(conf) {
  const wanted = (conf && conf.transitions) || {};
  const keep = new Set();
  [cfg, pending, conf].forEach((c) => { if (c && c.transitions) Object.keys(c.transitions).forEach((k) => keep.add(k)); });
  for (const id of Object.keys(tslots)) {
    if (!keep.has(id) && (!activeTransition || activeTransition.id !== id)) { releaseTransition(tslots[id]); delete tslots[id]; }
  }
  const muted = !!((conf.playlist || {}).mute);
  const vol = Math.max(0, Math.min(1, (conf.volume == null ? 80 : conf.volume) / 100));
  for (const [id, t] of Object.entries(wanted)) {
    const e = tslots[id];
    if (e && e.src === t.src) {
      e.muted = muted; e.volume = vol;
      const m = e.slot && e.slot.media;   // volume en geluid volgen gewijzigde instellingen
      if (m) { m.muted = muted; m.volume = vol; }
      continue;
    }
    if (e && e !== activeTransition) releaseTransition(e);
    tslots[id] = { id, src: t.src, name: t.name, duration: t.video_duration, scale: conf.scale_default,
      muted, volume: vol, slot: null, ready: null, failed: false };
  }
}

function armTransition(t) {
  if (!t || (t.slot && !t.slot.destroyed)) return;
  const slot = makeSlot({ kind: 'video', src: t.src, name: 'overgang ' + (t.name || ''), scale: t.scale },
    { muted: t.muted, volume: Math.round(t.volume * 100) });
  t.slot = slot;
  t.failed = false;
  t.ready = slot.promise.catch((e) => { t.failed = true; log('Overgang ' + (t.name || t.id) + ' niet beschikbaar: ' + e.message); throw e; });
  t.ready.catch(() => {});
}

function releaseTransition(t) {
  if (!t || !t.slot) return;
  destroySlot(t.slot);
  t.slot = null;
  t.ready = null;
}

// ---------------------------------------------------------------------------
// Volgorde
// ---------------------------------------------------------------------------
function isShuffle(conf, fb) {
  const pl = (conf && conf.playlist) || {};
  return !!(fb ? pl.fallback_shuffle : pl.shuffle);
}

function shuffled(list, avoidFirstId) {
  const a = list.slice();
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]];
  }
  if (a.length > 1 && avoidFirstId != null && a[0].media_id === avoidFirstId) {
    const k = 1 + Math.floor(Math.random() * (a.length - 1));
    [a[0], a[k]] = [a[k], a[0]];
  }
  return a;
}

/* Lijst voor de volgende ronde. Bij willekeurige volgorde één keer geschud per ronde
   (gecachet), zodat voorladen en afspelen dezelfde keuze zien. */
function nextRound(base, doShuffle, src) {
  if (!doShuffle) return base;
  if (roundCache && roundCache.src === src && roundCache.base === base) return roundCache.list;
  const lastId = current && current.item ? current.item.media_id : null;
  roundCache = { base, src, list: shuffled(base, lastId) };
  return roundCache.list;
}

/* Bepaal het volgende item zonder de afspeeltoestand te wijzigen. */
function peekNext() {
  let conf = cfg, list = items, idx = index, fb = fallbackActive;
  if (pending) {
    conf = pending;
    fb = false;
    if (isShuffle(pending, false)) {
      list = nextRound(pending.items || [], true, pending);   // bewerkt + willekeurig: nieuwe ronde
      idx = -1;
    } else {
      const curId = current && current.item.media_id;
      const nlist = pending.items || [];
      list = nlist;
      idx = (index >= 0 && index < nlist.length && nlist[index].media_id === curId) ? index : -1;
    }
  }
  if (!list.length) return { type: 'standby' };
  let nxt = idx + 1;
  if (nxt >= list.length) {
    const mode = (conf.playlist || {}).repeat_mode || 'loop';
    if (mode === 'once_hold_last' && !fb) return { type: 'hold' };
    if (mode === 'once_then_default' && !fb && (conf.fallback_items || []).length) {
      return { type: 'item', conf, list: nextRound(conf.fallback_items, isShuffle(conf, true), 'fallback'), idx: 0, fb: true };
    }
    const base = fb ? (conf.fallback_items || list) : (conf.items || list);
    list = nextRound(base, isShuffle(conf, fb), list);
    nxt = 0;
  }
  return { type: 'item', conf, list, idx: nxt, fb };
}

function commit(p) {
  if (p.conf !== cfg) { cfg = p.conf; applyRotation(); played = []; }
  if (pending && p.conf === pending) pending = null;
  if (roundCache && p.list === roundCache.list) roundCache = null;   // ronde is begonnen
  items = p.list;
  index = p.idx;
  fallbackActive = p.fb;
}

function schedulePreload(opts, target) {
  const p = target || peekNext();
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
  // Geen derde video tegelijk: dan wordt de volgende video pas geladen zodra de huidige
  // (of de overgang) is vrijgegeven.
  if (item.kind === 'video' && liveVideos() >= MAX_VIDEOS) return;
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
async function advance(g, forced) {
  setState(S.LOADING_NEXT);
  for (let attempt = 0; attempt < 1000; attempt++) {
    if (g !== gen) return;
    const p = forced || peekNext();   // 'forced': gekozen via de live bediening
    forced = null;
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
  if (activeTransition) { releaseTransition(activeTransition); activeTransition = null; }
  hideFallback();
  clearTimers();
  // De overgang na dit item alvast laden; andere geladen overgangen vrijgeven.
  const nextT = item.transition ? tslots[item.transition] : null;
  Object.values(tslots).forEach((t) => { if (t !== nextT) releaseTransition(t); });
  armTransition(nextT);
  const top = played[played.length - 1];
  if (!top || top.list !== items || top.idx !== index) { played.push({ list: items, idx: index, fb: fallbackActive }); }
  if (played.length > 50) played.shift();
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

async function itemDone(g, forced) {
  if (g !== gen || state !== S.PLAYING) return;     // alleen vanuit PLAYING: geen dubbele afhandeling
  failures = 0;
  clearTimers();
  const p = forced || peekNext();
  if (p.type === 'hold') { setState(S.HOLD, current.item.name); return; }
  const tid = current.item.transition;
  const t = tid ? tslots[tid] : null;
  if (!t) { advance(g, forced); return; }
  setState(S.PREPARE_TRANSITION, t.name);
  armTransition(t);           // normaal al geladen bij de start van het item
  try {
    await timeout(t.ready, TRANSITION_READY_TIMEOUT, 'overgang');
  } catch (e) {
    if (g !== gen) return;
    log(`Overgang '${t.name || tid}' overgeslagen: ${e.message}`);
    advance(g, forced);
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
    releaseTransition(t);
    log(`Overgang '${t.name || tid}' start niet: ${e.message}`);
    advance(g, forced);
    return;
  }
  if (g !== gen) { releaseTransition(t); return; }
  activeTransition = t;
  setState(S.PLAYING_TRANSITION, t.name);
  // De overgang bedekt nu het scherm: de video van het vorige item vrijgeven en het
  // volgende item voorbereiden terwijl de overgang speelt.
  await frames(2);
  if (g !== gen || state !== S.PLAYING_TRANSITION) return;
  if (current && current.slot && current.slot.kind === 'video') destroySlot(current.slot);
  schedulePreload(null, forced);
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
  advance(g, forced);
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
    if (activeTransition) { releaseTransition(activeTransition); activeTransition = null; }
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
  roundCache = null;
  played = [];
  items = isShuffle(conf, false) ? shuffled(conf.items || [], current && current.item ? current.item.media_id : null)
    : (conf.items || []).slice();
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
// Live bediening
// ---------------------------------------------------------------------------
/* Een bijgewerkte versie van dezelfde playlist meteen actief maken: de website toont al de
   nieuwe versie, dus 'naar item' moet daarin zoeken. */
function adoptPending() {
  if (!pending) return;
  const conf = pending;
  pending = null;
  const cur = current && !current.standby ? current.item : null;
  cfg = conf;
  roundCache = null;
  applyRotation();
  const base = conf.items || [];
  items = isShuffle(conf, false) ? shuffled(base, cur ? cur.media_id : null) : base.slice();
  index = cur ? items.findIndex((it) => it.item_id != null && it.item_id === cur.item_id) : -1;
  if (index > 0 && isShuffle(conf, false)) {
    // Nieuwe willekeurige ronde die met het huidige item begint: de andere items volgen nog.
    [items[0], items[index]] = [items[index], items[0]];
    index = 0;
  }
  fallbackActive = false;
  played = [];
}

/* Doel van een opdracht als { list, idx, fb }, of een tekst als het niet kan. */
function manualTarget(cmd) {
  const base = (cfg && cfg.items) || [];
  if (!base.length) return 'er staat niets in de playlist';
  const fb = fallbackActive;
  const curId = current && current.item ? current.item.media_id : null;
  if (cmd.action === 'next') {
    if (index + 1 >= 0 && index + 1 < items.length) return { list: items, idx: index + 1, fb };
    const mode = (cfg.playlist || {}).repeat_mode || 'loop';
    if (mode === 'once_then_default' && !fb && (cfg.fallback_items || []).length) {
      const f = cfg.fallback_items;
      return { list: isShuffle(cfg, true) ? shuffled(f, curId) : f.slice(), idx: 0, fb: true };
    }
    const src = (fb ? cfg.fallback_items : base) || base;    // ook bij 'laatste blijft staan'
    return { list: isShuffle(cfg, fb) ? shuffled(src, curId) : src.slice(), idx: 0, fb };
  }
  if (cmd.action === 'prev') {
    if (!items.length) return 'er is geen vorig item';
    if (state === S.PLAYING_TRANSITION && index >= 0 && index < items.length) return { list: items, idx: index, fb };
    if (isShuffle(cfg, fb)) {
      if (played.length >= 2) { played.pop(); return played[played.length - 1]; }
      if (index > 0) return { list: items, idx: index - 1, fb };
      return 'er is nog geen vorig item in deze willekeurige ronde';
    }
    return { list: items, idx: index >= 0 ? (index - 1 + items.length) % items.length : items.length - 1, fb };
  }
  if (cmd.action === 'goto') {
    const pos = base.findIndex((it) => it.item_id === cmd.item_id);
    if (pos < 0) return 'dit item staat niet in de playlist op het scherm (bestand ontbreekt of verouderd)';
    if (!fb) {
      const i = items.findIndex((it) => it.item_id === cmd.item_id);
      if (i >= 0) return { list: items, idx: i, fb: false };
    }
    if (isShuffle(cfg, false)) {
      const lst = shuffled(base, null);
      const j = lst.findIndex((it) => it.item_id === cmd.item_id);
      [lst[0], lst[j]] = [lst[j], lst[0]];
      return { list: lst, idx: 0, fb: false };
    }
    return { list: base.slice(), idx: pos, fb: false };
  }
  return 'onbekende opdracht';
}

function control(cmd) {
  if (!cfg || state === S.IDLE) return 'de player is nog niet klaar';
  adoptPending();
  const t = manualTarget(cmd);
  if (typeof t === 'string') return t;
  const target = { type: 'item', conf: cfg, list: t.list, idx: t.idx, fb: t.fb };
  const tid = current && !current.standby ? current.item.transition : null;
  const withTransition = !!cmd.transition && state === S.PLAYING && !!tid && !!tslots[tid];
  const g = ++gen;
  clearTimers();
  failures = 0;
  if (withTransition) {
    itemDone(g, target);
  } else {
    // Geen overgang: geladen overgangen vrijgeven, zodat het gekozen item een decoder heeft.
    Object.values(tslots).forEach((t) => { if (t !== activeTransition) releaseTransition(t); });
    advance(g, target);
  }
  return 'ok';
}

function handleCommand(c) {
  let result;
  try { result = control(c); } catch (e) { result = 'fout: ' + e.message; }
  if (result !== 'ok') log(`Opdracht '${c.action}' niet uitgevoerd: ${result}`);
  lastControl = { seq: c.id, action: c.action, result };
  reportSoon();
}

let cmdBoot = '', cmdSeq = null;
async function commandLoop() {
  for (;;) {
    try {
      const q = cmdSeq == null ? '' : `?boot=${encodeURIComponent(cmdBoot)}&after=${cmdSeq}`;
      const r = await fetch('/cmd' + q, { cache: 'no-store' });
      if (r.ok) {
        const d = await r.json();
        if (cmdSeq != null && d.boot === cmdBoot) (d.commands || []).forEach(handleCommand);
        cmdBoot = d.boot;
        cmdSeq = d.seq;
        continue;
      }
    } catch (e) { /* daemon even niet bereikbaar */ }
    await new Promise((res) => setTimeout(res, 2000));
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

/* Eenmalig: tekent Chromium met de GPU (en welke), en decodeert het H.264 1080p30 in hardware?
   Zichtbaar op het dashboard bij 'Weergave'. */
const gpuInfo = { renderer: null, h264_hw: null };
try {
  const c = document.createElement('canvas');
  const gl = c.getContext('webgl');
  if (gl) {
    const ext = gl.getExtension('WEBGL_debug_renderer_info');
    gpuInfo.renderer = String(ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER)).slice(0, 120);
    const lose = gl.getExtension('WEBGL_lose_context');
    if (lose) lose.loseContext();
  } else {
    gpuInfo.renderer = 'geen WebGL';
  }
} catch (e) { /* genegeerd */ }
if (navigator.mediaCapabilities && navigator.mediaCapabilities.decodingInfo) {
  navigator.mediaCapabilities.decodingInfo({ type: 'file', video: { contentType: 'video/mp4; codecs="avc1.640028"',
    width: 1920, height: 1080, bitrate: 8000000, framerate: 30 } })
    .then((r) => { gpuInfo.h264_hw = !!(r && r.supported && r.powerEfficient); }, () => {});
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
      item_id: cur.item_id == null ? null : cur.item_id,
      shuffle: isShuffle(cfg, fallbackActive),
    } : null,
    control: { v: 1, last: lastControl },
    errors: errors.slice(),
    gpu: gpuInfo,
    viewport: { w: window.innerWidth, h: window.innerHeight, dpr: window.devicePixelRatio,
      screen_w: Math.round(screen.width * window.devicePixelRatio), screen_h: Math.round(screen.height * window.devicePixelRatio) },
    video_stats: vs,
    revision: cfg && cfg.revision,
  };
}

async function postStatus() {
  try {
    await fetch('/status', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(snapshot()) });
  } catch (e) { /* genegeerd */ }
}
async function reportStatus() {
  await postStatus();
  setTimeout(reportStatus, STATUS_MS);
}
/* Na een wissel van item direct melden (de daemon geeft het meteen door aan de server). */
let soonTimer = null;
function reportSoon() {
  if (soonTimer) return;
  soonTimer = setTimeout(() => { soonTimer = null; postStatus(); }, 50);
}

// ---------------------------------------------------------------------------
// Testinstrumentatie (?test): telt frames zonder zichtbare, geladen laag.
// ---------------------------------------------------------------------------
if (TEST) {
  const probe = { frames: 0, blank: 0, blankAt: [], started: false };
  window.__signage = { probe, history, errors, state: () => state, snapshot, gen: () => gen, control };
  const check = () => {
    const visible = Array.from(stage.querySelectorAll('.slot.on')).some((el) => {
      const s = slotsByEl.get(el);
      if (!s || !s.ready) return false;
      if (s.kind === 'video') return s.media.readyState >= 2;
      return true;
    });
    if (visible) probe.started = true;
    probe.maxVideos = Math.max(probe.maxVideos || 0, liveVideos());
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
commandLoop();
