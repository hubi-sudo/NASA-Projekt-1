'use strict';

const $ = (sel) => document.querySelector(sel);
const el = (tag, attrs = {}, html = '') => Object.assign(document.createElement(tag), attrs, html ? { innerHTML: html } : {});
const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
const num = (v, d = 3) => v.toFixed(d).replace('.', ',').replace('-', '−');
const signed = (v, d = 3) => (v > 0 ? '+' : '') + num(v, d);
const pct = (v) => num(v, 1) + '%';
const DEFAULT_RIGHT = 'NDBI';
const MONTHS = ['stycznia', 'lutego', 'marca', 'kwietnia', 'maja', 'czerwca', 'lipca', 'sierpnia', 'września', 'października', 'listopada', 'grudnia'];

let CFG, map, ORDER, AVAILABLE, BOUNDS_LAYER;
const state = { region: null, pos: 0, left: 'RGB', swipe: 0.5, opacity: 0.9 };

fetch('config.json', { cache: 'no-cache' }).then((r) => {
  if (!r.ok) throw new Error(`config.json: HTTP ${r.status}`);
  return r.json();
}).then((cfg) => {
  CFG = cfg;
  ORDER = cfg.order;
  AVAILABLE = new Set(cfg.available);
  state.pos = ORDER.indexOf(DEFAULT_RIGHT);
  state.region = cfg.regions[0].id;
  const [y, m, d] = cfg.date.split('-').map(Number);
  $('#date').textContent = `${d} ${MONTHS[m - 1]} ${y}`;
  $('#product').textContent = cfg.product;
  readHash(location.hash.slice(1));
  initMap();
  initPanel();
  initProductBar();
  initSwipe();
  loadBoundaries();
  setPos(state.pos);
  updateClip();
  update(true);
  window.mapReady = true;
}).catch((e) => window.showMapError && window.showMapError(e.message || String(e)));

function regionById(id) { return CFG.regions.find((r) => r.id === id); }
function current() { return ORDER[Math.round(state.pos)]; }

const DataLayer = L.TileLayer.extend({
  createTile(coords, done) {
    if (!AVAILABLE.has(`${coords.z}/${coords.x}/${coords.y}`)) {
      const empty = document.createElement('div');
      setTimeout(() => done(null, empty), 0);
      return empty;
    }
    return L.TileLayer.prototype.createTile.call(this, coords, done);
  },
});

const layerCache = {};
function dataLayer(key, pane) {
  const id = `${pane}:${key}`;
  if (!layerCache[id]) {
    layerCache[id] = new DataLayer(`tiles/${key}/{z}/{x}/{y}.png`, {
      pane, className: 'data-tiles', minNativeZoom: CFG.minzoom, maxNativeZoom: CFG.maxzoom,
      maxZoom: 16, opacity: state.opacity, updateWhenZooming: false, keepBuffer: 3,
      attribution: 'Landsat 8: USGS/NASA',
    });
  }
  return layerCache[id];
}

function initMap() {
  map = L.map('map', { zoomControl: false, minZoom: 7, maxZoom: 16, zoomSnap: 0.25 });
  L.control.zoom({ position: 'topright' }).addTo(map);
  L.control.scale({ imperial: false, position: 'bottomleft' }).addTo(map);
  map.createPane('left').style.zIndex = 300;
  map.createPane('main').style.zIndex = 310;
  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19, opacity: 0.8, updateWhenZooming: false,
    attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
  }).addTo(map);
  const all = L.latLngBounds([].concat(...CFG.regions.map((r) => r.extent)));
  map.setMaxBounds(all.pad(0.6));
  map.fitBounds(regionById(state.region).focus, { padding: [20, 20] });
  map.on('move zoom zoomend viewreset resize', updateClip);
  map.on('moveend', detectRegion);
}

function loadBoundaries() {
  BOUNDS_LAYER = L.layerGroup().addTo(map);
  for (const r of CFG.regions) {
    fetch(`boundaries/${r.id}.geojson`).then((x) => x.json()).then((fc) => {
      L.geoJSON(fc, {
        interactive: false,
        style: { color: '#111', weight: 1.6, opacity: 0.85, fill: false, dashArray: '5 4' },
      }).addTo(BOUNDS_LAYER);
    });
  }
}

let active = new Set();
function renderLayers() {
  const i0 = Math.floor(state.pos + 1e-6);
  const i1 = Math.min(i0 + 1, ORDER.length - 1);
  const f = state.pos - i0;
  const want = new Map();
  const lower = dataLayer(ORDER[i0], 'main');
  want.set(lower, state.opacity);
  lower.setZIndex(1);
  if (f > 0.01 && i1 !== i0) {
    const upper = dataLayer(ORDER[i1], 'main');
    want.set(upper, state.opacity * f);
    upper.setZIndex(2);
  }
  want.set(dataLayer(state.left, 'left'), state.opacity);
  if (!(navigator.connection && navigator.connection.saveData)) {
    const near = Math.round(state.pos);
    for (const j of [near - 1, near + 1]) {
      const l = ORDER[j] && dataLayer(ORDER[j], 'main');
      if (l && !want.has(l)) { want.set(l, 0); l.setZIndex(0); }
    }
  }
  for (const l of active) if (!want.has(l)) map.removeLayer(l);
  for (const [l, o] of want) {
    if (!map.hasLayer(l)) l.addTo(map);
    l.setOpacity(o);
  }
  active = new Set(want.keys());
}

let swipeEl;
function initSwipe() {
  swipeEl = el('div', { className: 'swipe' }, `
    <div class="swipe-line"></div><div class="swipe-hit"></div>
    <div class="swipe-handle" role="slider" tabindex="0" aria-label="Położenie suwaka porównania"
         aria-valuemin="0" aria-valuemax="100">⟨⟩</div>
    <span class="swipe-label left"></span><span class="swipe-label right"></span>`);
  map.getContainer().appendChild(swipeEl);
  L.DomEvent.disableClickPropagation(swipeEl);
  L.DomEvent.disableScrollPropagation(swipeEl);
  let drag = false;
  const move = (e) => {
    const r = map.getContainer().getBoundingClientRect();
    state.swipe = clamp((e.clientX - r.left) / r.width, 0.02, 0.98);
    updateClip();
  };
  const stop = () => { drag = false; map.dragging.enable(); };
  for (const target of [swipeEl.querySelector('.swipe-hit'), swipeEl.querySelector('.swipe-handle')]) {
    target.addEventListener('pointerdown', (e) => {
      drag = true;
      target.setPointerCapture(e.pointerId);
      map.dragging.disable();
      e.preventDefault();
    });
    target.addEventListener('pointermove', (e) => drag && move(e));
    target.addEventListener('pointerup', stop);
    target.addEventListener('pointercancel', stop);
  }
  swipeEl.querySelector('.swipe-handle').addEventListener('keydown', (e) => {
    const step = { ArrowLeft: -0.02, ArrowRight: 0.02 }[e.key];
    if (step) {
      state.swipe = clamp(state.swipe + step, 0.02, 0.98);
      updateClip();
      e.preventDefault();
      e.stopPropagation();
    }
  });
}

function updateClip() {
  if (!map || !swipeEl) return;
  const size = map.getSize();
  const nw = map.containerPointToLayerPoint([0, 0]);
  const se = map.containerPointToLayerPoint(size);
  const x = nw.x + Math.round(size.x * state.swipe);
  map.getPane('left').style.clip = `rect(${nw.y}px, ${x}px, ${se.y}px, ${nw.x}px)`;
  map.getPane('main').style.clip = `rect(${nw.y}px, ${se.x}px, ${se.y}px, ${x}px)`;
  swipeEl.style.left = `${size.x * state.swipe}px`;
  swipeEl.querySelector('.swipe-handle').setAttribute('aria-valuenow', Math.round(state.swipe * 100));
}

let pbar;
function initProductBar() {
  pbar = el('div', { className: 'pbar' }, `
    <div class="pbar-head"><strong id="pbar-title"></strong><span class="hint">przeciągnij lub użyj ← →</span></div>
    <div class="pbar-track" role="slider" tabindex="0" aria-label="Warstwa po prawej stronie"
         aria-valuemin="0" aria-valuemax="${ORDER.length - 1}">
      <div class="pbar-rail"></div><div class="pbar-thumb"></div>
    </div>`);
  map.getContainer().appendChild(pbar);
  L.DomEvent.disableClickPropagation(pbar);
  L.DomEvent.disableScrollPropagation(pbar);
  const track = pbar.querySelector('.pbar-track');
  ORDER.forEach((key, i) => {
    const b = el('button', { type: 'button', className: 'pbar-stop', tabIndex: -1 }, CFG.layers[key].label);
    b.style.left = `${(i / (ORDER.length - 1)) * 100}%`;
    b.dataset.i = i;
    track.appendChild(b);
  });
  let drag = false;
  const posFrom = (e) => {
    const r = track.getBoundingClientRect();
    return clamp((e.clientX - r.left) / r.width, 0, 1) * (ORDER.length - 1);
  };
  track.addEventListener('pointerdown', (e) => {
    drag = true;
    track.setPointerCapture(e.pointerId);
    cancelAnimation();
    setPos(posFrom(e));
  });
  track.addEventListener('pointermove', (e) => drag && setPos(posFrom(e)));
  const release = () => { if (drag) { drag = false; animateTo(Math.round(state.pos)); } };
  track.addEventListener('pointerup', release);
  track.addEventListener('pointercancel', release);
  track.addEventListener('keydown', (e) => {
    const step = { ArrowLeft: -1, ArrowRight: 1, Home: -99, End: 99 }[e.key];
    if (!step) return;
    e.preventDefault();
    animateTo(clamp(Math.round(state.pos) + step, 0, ORDER.length - 1));
  });
  document.addEventListener('keydown', (e) => {
    if (e.target.closest('input, select, textarea, [role="slider"]')) return;
    if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
      animateTo(clamp(Math.round(state.pos) + (e.key === 'ArrowLeft' ? -1 : 1), 0, ORDER.length - 1));
    }
  });
}

let anim = null;
function cancelAnimation() { if (anim) cancelAnimationFrame(anim); anim = null; }
function animateTo(target) {
  cancelAnimation();
  const from = state.pos;
  const t0 = performance.now();
  const dur = Math.min(450, 160 + Math.abs(target - from) * 90);
  const step = (t) => {
    const k = clamp((t - t0) / dur, 0, 1);
    setPos(from + (target - from) * (1 - Math.pow(1 - k, 3)));
    anim = k < 1 ? requestAnimationFrame(step) : null;
  };
  anim = requestAnimationFrame(step);
}

let lastKey = null;
function setPos(p) {
  state.pos = clamp(p, 0, ORDER.length - 1);
  renderLayers();
  pbar.querySelector('.pbar-thumb').style.left = `${(state.pos / (ORDER.length - 1)) * 100}%`;
  pbar.querySelector('.pbar-track').setAttribute('aria-valuenow', Math.round(state.pos));
  if (current() !== lastKey) update();
}

function initPanel() {
  const chips = $('#regions');
  for (const r of CFG.regions) {
    const b = el('button', { type: 'button', className: 'chip', textContent: r.name });
    b.dataset.id = r.id;
    b.addEventListener('click', () => {
      state.region = r.id;
      map.flyToBounds(r.focus, { padding: [20, 20], duration: 0.9 });
      update();
    });
    chips.appendChild(b);
  }
  const left = $('#left-select');
  for (const key of ORDER) left.appendChild(el('option', { value: key, textContent: `${CFG.layers[key].label} · ${CFG.layers[key].name}` }));
  left.value = state.left;
  left.addEventListener('change', () => { state.left = left.value; renderLayers(); update(); });
  $('#swap').addEventListener('click', () => {
    const oldLeft = state.left;
    state.left = current();
    left.value = state.left;
    cancelAnimation();
    setPos(ORDER.indexOf(oldLeft));
    update();
  });
  $('#opacity').value = state.opacity;
  $('#opacity').addEventListener('input', (e) => { state.opacity = +e.target.value; renderLayers(); });
  $('#show-bounds').addEventListener('change', (e) => {
    if (e.target.checked) BOUNDS_LAYER.addTo(map); else map.removeLayer(BOUNDS_LAYER);
  });
}

function detectRegion() {
  const c = map.getCenter();
  const hit = CFG.regions.find((r) => L.latLngBounds(r.extent).contains(c));
  if (hit && hit.id !== state.region) { state.region = hit.id; update(); }
}

function update(force) {
  const key = current();
  const changedLayer = key !== lastKey;
  lastKey = key;
  const layer = CFG.layers[key];
  for (const b of document.querySelectorAll('#regions .chip')) b.setAttribute('aria-pressed', b.dataset.id === state.region);
  pbar.querySelectorAll('.pbar-stop').forEach((b) => b.classList.toggle('on', ORDER[b.dataset.i] === key));
  $('#pbar-title').innerHTML = `Prawa strona: ${layer.label}<span class="long"> · ${layer.name}</span>`;
  if (swipeEl) {
    swipeEl.querySelector('.swipe-label.left').textContent = `◀ ${CFG.layers[state.left].label}`;
    swipeEl.querySelector('.swipe-label.right').textContent = `${layer.label} ▶`;
  }
  if (changedLayer || force) renderLayerCard(key);
  renderStats(regionById(state.region), key);
  writeHash();
}

function renderLayerCard(key) {
  const l = CFG.layers[key];
  let legend = '';
  if (l.kind === 'index') {
    const stops = l.lut.match(/.{6}/g).filter((_, i) => i % 16 === 0 || i === 254).map((h) => `#${h}`);
    const [lo, hi] = l.range;
    legend = `<div class="ramp" style="background:linear-gradient(90deg,${stops.join(',')})"></div>
      <div class="ramp-ticks"><span>≤ ${num(lo, 2)}</span><span>0</span><span>≥ ${num(hi, 2)}</span></div>
      <div class="ramp-words"><span>${l.low}</span><span>${l.high}</span></div>`;
  }
  $('#layer-card').innerHTML = `
    <h2>Warstwa po prawej</h2>
    <h3>${l.label}</h3>
    <p class="muted small">${l.name}</p>
    <div class="formula">${l.formula}</div>
    <p>${l.about}</p>${legend}`;
}

function renderStats(region, key) {
  const layer = CFG.layers[key];
  $('#stats-title').textContent = `Statystyki · ${region.name}`;
  if (layer.kind !== 'index') {
    $('#stats').innerHTML = '<p class="small muted">Statystyki są liczone dla wskaźników NDBI, IBI i MNDWI.</p>';
    return;
  }
  const s = region.stats.indices[key];
  $('#stats').innerHTML = `
    <dl class="stats">
      <div><dt>Średnia ${key}</dt><dd>${signed(s.mean)}</dd></div>
      <div><dt>Mediana</dt><dd>${signed(s.median)}</dd></div>
      <div><dt>Piksele &gt; 0</dt><dd>${pct(s.positive_pct)}</dd></div>
    </dl>
    <p class="small muted">W granicach miast, ${num(region.stats.area_km2, 0)} km².</p>`;
}

function readHash(hash) {
  const [region, key, left] = decodeURIComponent(hash).split('/');
  if (CFG.regions.some((r) => r.id === region)) state.region = region;
  if (ORDER.includes(key)) state.pos = ORDER.indexOf(key);
  if (ORDER.includes(left)) state.left = left;
}

function writeHash() {
  history.replaceState(null, '', `#${state.region}/${current()}/${state.left}`);
}
