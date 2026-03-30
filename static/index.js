// ── State ─────────────────────────────────────────────────────────────────
// STATIONS and CITY_STATIONS are declared in the inline <script> block in
// index.html (Jinja2-injected variables), available as globals here.
let trainsAB = [], trainsBA = [];
let dailyBikeMap  = {};   // trainNo → bool, populated from last daily query
let liveDelayMap  = {};   // trainNo → delay minutes, populated from liveboard
let _nextTrainTimer = null; // setTimeout handle for next-train auto-refresh


// ── Client-side Cache Manager ─────────────────────────────────────────────
const _CACHE_VER = '3';  // bump to invalidate all OD/daily localStorage entries
(function() {
  const stored = localStorage.getItem('tdx_cache_ver');
  if (stored !== _CACHE_VER) {
    Object.keys(localStorage)
      .filter(k => k.startsWith('tdx_od_') || k.startsWith('tdx_daily_'))
      .forEach(k => localStorage.removeItem(k));
    localStorage.setItem('tdx_cache_ver', _CACHE_VER);
  }
})();

const CacheManager = {
  OD_TTL:    30 * 60 * 1000,        // 30 minutes in ms
  DAILY_TTL: 7  * 24 * 3600 * 1000, // 7 days in ms
  MAX_KEYS:  40,                     // evict oldest beyond this limit

  _key(type, ...parts) {
    return `tdx_${type}_${parts.join('_')}`;
  },

  get(key) {
    try {
      const raw = localStorage.getItem(key);
      if (!raw) return null;
      const entry = JSON.parse(raw);
      if (Date.now() > entry.expires_at) {
        localStorage.removeItem(key);
        return null;
      }
      return entry.data;
    } catch { return null; }
  },

  set(key, data, ttl) {
    try {
      this._evictIfNeeded();
      localStorage.setItem(key, JSON.stringify({
        data,
        expires_at: Date.now() + ttl,
        saved_at:   Date.now(),
      }));
    } catch (e) {
      // localStorage full — evict aggressively then retry once
      this._evictOldest(10);
      try { localStorage.setItem(key, JSON.stringify({ data, expires_at: Date.now() + ttl, saved_at: Date.now() })); }
      catch { /* give up silently */ }
    }
  },

  getOD(fromCode, toCode) {
    return this.get(this._key('od', fromCode, toCode));
  },
  setOD(fromCode, toCode, data) {
    this.set(this._key('od', fromCode, toCode), data, this.OD_TTL);
  },

  getDaily(fromCode, toCode, dateStr) {
    return this.get(this._key('daily', fromCode, toCode, dateStr));
  },
  setDaily(fromCode, toCode, dateStr, data) {
    this.set(this._key('daily', fromCode, toCode, dateStr), data, this.DAILY_TTL);
  },

  _evictIfNeeded() {
    const keys = this._tdxKeys();
    if (keys.length >= this.MAX_KEYS) this._evictOldest(keys.length - this.MAX_KEYS + 1);
  },

  _evictOldest(n) {
    this._tdxKeys()
      .map(k => { try { return { k, t: JSON.parse(localStorage.getItem(k)).saved_at }; } catch { return { k, t: 0 }; } })
      .sort((a, b) => a.t - b.t)
      .slice(0, n)
      .forEach(({ k }) => localStorage.removeItem(k));
  },

  _tdxKeys() {
    return Object.keys(localStorage).filter(k => k.startsWith('tdx_od_') || k.startsWith('tdx_daily_'));
  },

  stats() {
    const keys = this._tdxKeys();
    const bytes = keys.reduce((s, k) => s + (localStorage.getItem(k) || '').length * 2, 0);
    return { entries: keys.length, kb: (bytes / 1024).toFixed(1) };
  },
};

// ── Saved preferences ─────────────────────────────────────────────────────
function loadPrefs() {
  try { return JSON.parse(localStorage.getItem('tdx_prefs') || '{}'); } catch { return {}; }
}
function savePrefs(p) {
  try {
    const cur = loadPrefs();
    localStorage.setItem('tdx_prefs', JSON.stringify({ ...cur, ...p }));
  } catch {}
}
function trackUsage(code) {
  const p = loadPrefs();
  const freq = p.freq || {};
  freq[code] = (freq[code] || 0) + 1;
  savePrefs({ freq });
}
function getFreqCodes(n = 6) {
  const freq = loadPrefs().freq || {};
  return Object.entries(freq)
    .sort((a, b) => b[1] - a[1])
    .slice(0, n)
    .map(([code]) => code);
}

// ── City → stations grouping — server-injected from TDX API via Flask template ──
// Refreshed from /api/station-groups below; localStorage cache applied synchronously.
// CITY_STATIONS is declared in index.html (inline script, Jinja2-injected).

function getCityForCode(code) {
  return CITY_STATIONS.find(g => g.codes.includes(code))?.city || CITY_STATIONS[0]?.city || '';
}

function buildCitySelect(sel, selectedCity) {
  sel.innerHTML = '';
  // Prepend 常用車站 group if there is history
  const freqCodes = getFreqCodes();
  if (freqCodes.length) {
    const opt = document.createElement('option');
    opt.value = '常用車站';
    opt.textContent = '★ 常用車站';
    if ('常用車站' === selectedCity) opt.selected = true;
    sel.appendChild(opt);
  }
  CITY_STATIONS.forEach(g => {
    const opt = document.createElement('option');
    opt.value = g.city;
    opt.textContent = g.city;
    if (g.city === selectedCity) opt.selected = true;
    sel.appendChild(opt);
  });
}

// 特等站 & 一等站 — derived from server-injected STATIONS (StationClass from TDX API)
// 0 = 特等站, 1 = 一等站
const SPECIAL_CLASS = new Set(STATIONS.filter(s => s.cls === 0).map(s => s.code));
const FIRST_CLASS   = new Set(STATIONS.filter(s => s.cls === 1).map(s => s.code));
const SECOND_CLASS  = new Set(STATIONS.filter(s => s.cls === 2).map(s => s.code));
const THIRD_CLASS   = new Set(STATIONS.filter(s => s.cls === 3).map(s => s.code));
const SIMPLE_CLASS  = new Set(STATIONS.filter(s => s.cls === 4).map(s => s.code));
const STATION_MAP   = new Map(STATIONS.map(s => [s.code, s]));

// Return the highest-class marker visible for this city's station list.
// minCls: lowest class value present in the city (0=特等 … 3=三等).
// We show a prefix only for stations at that minimum level.
function stationPrefix(code, freqSet, minCls) {
  if (SPECIAL_CLASS.has(code))      return '◆ ';
  if (FIRST_CLASS.has(code))        return '● ';
  if (freqSet && freqSet.has(code)) return '★ ';
  if (minCls >= 2 && SECOND_CLASS.has(code)) return '▸ ';
  if (minCls >= 3 && THIRD_CLASS.has(code))  return '▹ ';
  if (minCls >= 4 && SIMPLE_CLASS.has(code)) return '▫ ';
  return '';
}

function buildStationSelect(sel, cityName, selectedCode) {
  sel.innerHTML = '';
  const freqCodes = getFreqCodes();
  const freqSet   = new Set(freqCodes);
  // Resolve codes list: special 常用車站 group or normal group
  let codes;
  if (cityName === '常用車站') {
    codes = freqCodes;
  } else {
    const group = CITY_STATIONS.find(g => g.city === cityName);
    if (!group) return;
    codes = group.codes;
  }
  // Find the highest station class present in this city's list.
  // minCls drives which tier gets a highlight prefix:
  //   0 or 1 → only ◆/● shown (no extra tier)
  //   2      → ▸ for 二等
  //   3+     → ▹ for 三等 (city has neither 特/一/二等)
  const minCls = codes.reduce((best, c) => {
    const s = STATION_MAP.get(c);
    return (s && s.cls >= 0 && s.cls < best) ? s.cls : best;
  }, 99);
  codes.forEach(code => {
    const station = STATION_MAP.get(code);
    if (!station) return;
    const opt = document.createElement('option');
    opt.value = station.code;
    opt.textContent = stationPrefix(code, freqSet, minCls) + station.name;
    if (station.code === selectedCode) opt.selected = true;
    sel.appendChild(opt);
  });
  if (!sel.value && sel.options.length) sel.selectedIndex = 0;
}

const fromCitySel = document.getElementById('from-city-sel');
const toCitySel   = document.getElementById('to-city-sel');
const fromSel     = document.getElementById('from-sel');
const toSel       = document.getElementById('to-sel');
const prefs       = loadPrefs();

const _initFrom = prefs.from || '0970';
const _initTo   = prefs.to   || '0990';

// ── Max rows selector ──────────────────────────────────────────────────────────────────
const _rowsSel = document.getElementById('rows-sel');
function _applyMaxRows(n) {
  const h = n === 0 ? 'none' : `calc(40px * ${n} + 37px)`;
  document.documentElement.style.setProperty('--tbl-max-h', h);
}
// Restore saved preference
(function() {
  const saved = parseInt(loadPrefs().maxRows ?? '8', 10);
  _rowsSel.value = String(saved);
  _applyMaxRows(saved);
})();
_rowsSel.addEventListener('change', () => {
  const n = parseInt(_rowsSel.value, 10);
  _applyMaxRows(n);
  savePrefs({ maxRows: n });
});

// ── Station-groups cache ──────────────────────────────────────────────────
const _SG_CACHE_KEY = 'tra_station_groups_v2';
const _SG_CACHE_TTL = 30 * 24 * 3600 * 1000; // 30 days
// Apply cached groups synchronously so the first build already uses live data.
try {
  const _c = JSON.parse(localStorage.getItem(_SG_CACHE_KEY) || 'null');
  if (_c && Date.now() - _c.ts < _SG_CACHE_TTL && Array.isArray(_c.groups) && _c.groups.length)
    CITY_STATIONS = _c.groups;
} catch {}

buildCitySelect(fromCitySel, getCityForCode(_initFrom));
buildStationSelect(fromSel, fromCitySel.value, _initFrom);
buildCitySelect(toCitySel, getCityForCode(_initTo));
buildStationSelect(toSel, toCitySel.value, _initTo);

// Refresh from API in the background; update cache and rebuild if data changed.
fetch('/api/station-groups')
  .then(r => r.json())
  .then(groups => {
    if (!Array.isArray(groups) || groups.length === 0) return;
    try { localStorage.setItem(_SG_CACHE_KEY, JSON.stringify({ts: Date.now(), groups})); } catch {}
    CITY_STATIONS = groups;
    const savedFrom = fromSel.value;
    const savedTo   = toSel.value;
    buildCitySelect(fromCitySel, getCityForCode(savedFrom));
    buildStationSelect(fromSel, fromCitySel.value, savedFrom);
    buildCitySelect(toCitySel, getCityForCode(savedTo));
    buildStationSelect(toSel, toCitySel.value, savedTo);
  })
  .catch(() => { /* keep current groups */ });

// City change → rebuild station list
fromCitySel.addEventListener('change', () => buildStationSelect(fromSel, fromCitySel.value, null));
toCitySel.addEventListener('change',   () => buildStationSelect(toSel,   toCitySel.value,   null));


const now = new Date();

// Default date to today (local date, not UTC — avoids showing yesterday between 00:00–07:59 in UTC+8)
const _pad = n => String(n).padStart(2, '0');
document.getElementById('date-input').value =
  `${now.getFullYear()}-${_pad(now.getMonth() + 1)}-${_pad(now.getDate())}`;

// ── Swap ──────────────────────────────────────────────────────────────────
document.getElementById('swap-btn').addEventListener('click', () => {
  const tmpCity = fromCitySel.value;
  const tmpCode = fromSel.value;
  fromCitySel.value = toCitySel.value;
  buildStationSelect(fromSel, fromCitySel.value, toSel.value);
  toCitySel.value = tmpCity;
  buildStationSelect(toSel, toCitySel.value, tmpCode);
  queryGeneral();
});

// ── Filters ───────────────────────────────────────────────────────────────
const chipReserved = document.getElementById('chip-reserved');
const chipLocal    = document.getElementById('chip-local');
const chipExpress  = document.getElementById('chip-express');
[chipReserved, chipLocal, chipExpress].forEach(chip => {
  chip.addEventListener('click', () => {
    const cb = chip.querySelector('input');
    cb.checked = !cb.checked;
    chip.classList.toggle('off', !cb.checked);
    renderTables();
  });
});

function filterOn(id) {
  return document.querySelector(`#${id} input`).checked;
}

// ── Status helpers ────────────────────────────────────────────────────────
function setStatus(msg, loading = false) {
  document.getElementById('status-text').textContent = msg;
  document.getElementById('spinner').style.display = loading ? 'block' : 'none';
}

function showError(msg) {
  const el = document.getElementById('error-banner');
  el.textContent = msg;
  el.style.display = msg ? 'block' : 'none';
}

// ── XSS helpers ──────────────────────────────────────────────────────────
function escHtml(s) {
  return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;')
                        .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// ── Train category ────────────────────────────────────────────────────────
function getCategory(t) {
  if (t.train_type.startsWith('區間快')) return 'express';
  if (t.train_type.startsWith('區間'))   return 'local';
  return 'reserved';
}

// ── Remark pill renderer ──────────────────────────────────────────────────
const _LINE_NAMES = new Set(['山線', '海線', '成追線']);
function remarkTags(remark) {
  if (!remark) return '';
  return remark.split('\u3000').map(p => {
    p = p.trim();
    if (!p) return '';
    let cls = 'remark-schedule';
    if (_LINE_NAMES.has(p)) cls = 'remark-line';
    else if (p.includes('座票') || p.includes('無座')) cls = 'remark-seat';
    return `<span class="remark-tag ${cls}">${escHtml(p)}</span>`;
  }).join('');
}

// ── Render one table ──────────────────────────────────────────────────────
function renderTable(scrollEl, trains, titleEl, countEl, fromName, toName, isAB, fromCode, toCode) {
  const showReserved = filterOn('chip-reserved');
  const showLocal    = filterOn('chip-local');
  const showExpress  = filterOn('chip-express');
  const _now  = new Date();
  const nowHM = `${String(_now.getHours()).padStart(2,'0')}:${String(_now.getMinutes()).padStart(2,'0')}`;

  const filtered = trains.filter(t => {
    const cat = getCategory(t);
    if (cat === 'reserved' && !showReserved) return false;
    if (cat === 'local'    && !showLocal)    return false;
    if (cat === 'express'  && !showExpress)  return false;
    return true;
  });

  titleEl.textContent = `${isAB ? '🔵' : '🟠'} ${fromName} → ${toName}`;
  countEl.textContent = `${filtered.length} 班`;

  if (!filtered.length) {
    scrollEl.innerHTML = `<div class="empty-state"><div class="icon">🔍</div><span>無符合班次</span></div>`;
    return;
  }

  let prevHour = null;
  const rows = filtered.map((t, i) => {
    const cat = getCategory(t);
    const badgeClass = cat === 'reserved' ? 'badge-reserved' : cat === 'express' ? 'badge-express' : 'badge-local';
    let trClass = `type-${cat}`;
    if (t.dep < nowHM) trClass += ' row-past';
    const curHour = parseInt(t.dep.split(':')[0]);
    const hourBreak = prevHour !== null && curHour > prevHour;
    if (hourBreak) trClass += ' hour-break';
    prevHour = curHour;

    // Normalize bike flag — apply daily override if available, else use server value
    const hasBike = t.train_no in dailyBikeMap ? dailyBikeMap[t.train_no] : Boolean(t.bike);
    const bikeTag = hasBike ? '<span class="remark-tag remark-bike">\ud83d\udeb2</span>' : '';
    const remarkHtml = bikeTag + remarkTags(t.remark);
    return `<tr class="${trClass}" data-dep="${t.dep}" data-train-no="${escHtml(t.train_no)}">
      <td><span class="badge ${badgeClass}">${escHtml(t.train_type)}</span></td>
      <td class="train-no">${escHtml(t.train_no)}</td>
      <td class="time-dep">${t.dep}</td>
      <td class="time-arr">${t.arr}</td>
      <td class="duration">${t.duration}</td>
      <td class="left remark">${remarkHtml}</td>
    </tr>`;
  }).join('');

  scrollEl.innerHTML = `
    <table>
      <thead><tr>
        <th>車種</th><th>車次</th><th>出發</th><th>抵達</th><th>時長</th><th style="text-align:left">備註</th>
      </tr></thead>
      <tbody>${rows}</tbody>
    </table>`;

  // Row click → open train detail in new tab
  scrollEl.querySelector('tbody')?.addEventListener('click', e => {
    const row = e.target.closest('tr[data-train-no]');
    if (!row) return;
    const params = fromCode && toCode ? `?from=${encodeURIComponent(fromCode)}&to=${encodeURIComponent(toCode)}` : '';
    window.open(`/train/${encodeURIComponent(row.dataset.trainNo)}${params}`, '_blank');
  });

  // Scroll so next upcoming train is the first visible row
  let nextRow = null;
  for (const row of scrollEl.querySelectorAll('tbody tr[data-dep]')) {
    if (row.dataset.dep >= nowHM) { nextRow = row; break; }
  }
  if (nextRow) {
    requestAnimationFrame(() => {
      const containerRect = scrollEl.getBoundingClientRect();
      const rowRect = nextRow.getBoundingClientRect();
      const theadH = scrollEl.querySelector('thead')?.offsetHeight || 0;
      scrollEl.scrollTop += rowRect.top - containerRect.top - theadH;
    });

    // Schedule auto-refresh at the START of the minute AFTER departure.
    // e.g. train departs 18:38 → timer fires at 18:39:00.
    // At that point nowHM="18:39" so "18:38" < "18:39" → correctly dimmed as past.
    if (isAB) {
      if (_nextTrainTimer) clearTimeout(_nextTrainTimer);
      const depParts = nextRow.dataset.dep.split(':');
      const depMs = new Date().setHours(Number(depParts[0]), Number(depParts[1]) + 1, 0, 0);
      const msUntil = depMs - Date.now();
      if (msUntil > 0) {
        _nextTrainTimer = setTimeout(() => {
          renderTables();
          fetchLive();
        }, msUntil);
      }
    }
  }
  // Overlay live delay data if available
  overlayDelays(scrollEl);
}

// ── Render both tables ────────────────────────────────────────────────────
function renderTables() {
  const fromCode = fromSel.value;
  const toCode   = toSel.value;
  const fromName = STATION_MAP.get(fromCode)?.name || fromCode;
  const toName   = STATION_MAP.get(toCode)?.name   || toCode;

  // Keep server chronological order; renderTable will scroll to next train
  const sortedAB = [...trainsAB];
  const sortedBA = [...trainsBA];

  renderTable(
    document.getElementById('scroll-ab'),
    sortedAB,
    document.getElementById('title-ab'),
    document.getElementById('count-ab'),
    fromName, toName, true, fromCode, toCode
  );
  renderTable(
    document.getElementById('scroll-ba'),
    sortedBA,
    document.getElementById('title-ba'),
    document.getElementById('count-ba'),
    toName, fromName, false, toCode, fromCode
  );
}

// ── Fetch helpers ─────────────────────────────────────────────────────────
async function fetchTrains(url, statusMsg) {
  showError('');
  setStatus(statusMsg, true);
  document.getElementById('query-btn').disabled = true;
  document.getElementById('daily-btn').disabled = true;

  try {
    const resp = await fetch(url);
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.error || resp.statusText);
    return data;
  } catch (e) {
    showError(`查詢失敗：${e.message}`);
    setStatus('查詢失敗');
    return null;
  } finally {
    document.getElementById('query-btn').disabled = false;
    document.getElementById('daily-btn').disabled = false;
  }
}

// ── General timetable query ───────────────────────────────────────────────
async function queryGeneral() {
  const fromCode = fromSel.value;
  const toCode   = toSel.value;
  if (!fromCode || !toCode) { showError('請選擇車站'); return; }
  if (fromCode === toCode) { showError('出發站與抵達站不能相同'); return; }

  const fromName = STATION_MAP.get(fromCode)?.name || fromCode;
  const toName   = STATION_MAP.get(toCode)?.name   || toCode;
  savePrefs({ from: fromCode, to: toCode });
  trackUsage(fromCode);
  trackUsage(toCode);

  // ── Client cache hit ──
  const cached = CacheManager.getOD(fromCode, toCode);
  if (cached) {
    trainsAB = cached.ab || [];
    trainsBA = cached.ba || [];
    renderTables();
    const s = CacheManager.stats();
    setStatus(`💾 本機快取　${fromName}→${toName}: ${trainsAB.length} 班　${toName}→${fromName}: ${trainsBA.length} 班　(${s.entries} 筆 / ${s.kb} KB)`);
    document.getElementById('source-footer').textContent = '資料來源：本機快取（localStorage）　常態班表';
    fetchLive();
    fetchStationBoards();
    return;
  }

  // ── Cache miss — fetch from server ──
  const data = await fetchTrains(`/api/trains?from=${fromCode}&to=${toCode}`, '查詢常態班表中…');
  if (!data) return;

  trainsAB = data.ab || [];
  trainsBA = data.ba || [];
  CacheManager.setOD(fromCode, toCode, { ab: trainsAB, ba: trainsBA });
  renderTables();

  const src = data.cached ? '📦 伺服器快取' : '🔄 即時';
  const s = CacheManager.stats();
  setStatus(`${src}　${fromName}→${toName}: ${trainsAB.length} 班　${toName}→${fromName}: ${trainsBA.length} 班　(${s.entries} 筆 / ${s.kb} KB)`);
  document.getElementById('source-footer').innerHTML = '資料介接「<a href="https://tdx.transportdata.tw/" target="_blank" rel="noopener">交通部TDX平臺</a>」&amp; 平臺標章　常態班表';
  fetchLive();
  fetchStationBoards();
}

// ── Daily query ───────────────────────────────────────────────────────────
async function queryDaily() {
  const fromCode = fromSel.value;
  const toCode   = toSel.value;
  const dateStr  = document.getElementById('date-input').value;
  if (!fromCode || !toCode) { showError('請選擇車站'); return; }
  if (fromCode === toCode) { showError('出發站與抵達站不能相同'); return; }
  if (!dateStr) { showError('請選擇日期'); return; }

  const fromName = STATION_MAP.get(fromCode)?.name || fromCode;
  const toName   = STATION_MAP.get(toCode)?.name   || toCode;

  // ── Client cache hit ──
  const cached = CacheManager.getDaily(fromCode, toCode, dateStr);
  if (cached) {
    trainsAB = cached.ab || [];
    trainsBA = cached.ba || [];
    // Rebuild dailyBikeMap from cached trains
    dailyBikeMap = {};
    [...trainsAB, ...trainsBA].forEach(t => { dailyBikeMap[t.train_no] = Boolean(t.bike); });
    renderTables();
    const s = CacheManager.stats();
    setStatus(`💾 本機快取　${dateStr}　${fromName}→${toName}: ${trainsAB.length} 班　${toName}→${fromName}: ${trainsBA.length} 班　(${s.entries} 筆 / ${s.kb} KB)`);
    document.getElementById('source-footer').textContent = `資料來源：本機快取（localStorage）　${dateStr} 實際行駛班次`;
    fetchStationBoards();
    return;
  }

  // ── Cache miss — fetch from server ──
  const data = await fetchTrains(
    `/api/trains/daily?from=${fromCode}&to=${toCode}&date=${dateStr}`,
    `查詢 ${dateStr} 班次…`
  );
  if (!data) return;

  trainsAB = data.ab || [];
  trainsBA = data.ba || [];
  // Populate dailyBikeMap for real-time override in regular table
  dailyBikeMap = {};
  [...trainsAB, ...trainsBA].forEach(t => { dailyBikeMap[t.train_no] = Boolean(t.bike); });
  CacheManager.setDaily(fromCode, toCode, dateStr, { ab: trainsAB, ba: trainsBA });
  renderTables();

  const s = CacheManager.stats();
  setStatus(`📅 ${dateStr}　${fromName}→${toName}: ${trainsAB.length} 班　${toName}→${fromName}: ${trainsBA.length} 班　(${s.entries} 筆 / ${s.kb} KB)`);
  document.getElementById('source-footer').innerHTML =
    `資料介接「<a href="https://tdx.transportdata.tw/" target="_blank" rel="noopener">交通部TDX平臺</a>」&amp; 平臺標章　${dateStr} 實際行駛班次`;
  // fetchLive() is user-triggered only (即時 button) — not called automatically
  // to avoid adding 2 extra TDX LiveBoard calls on every page load.
  fetchStationBoards();
}

// ── Station live board ───────────────────────────────────────────────────────
// stnMode: 0=車次|車種  1=車種|車次  2=合併(228普悠瑪)  3=合併(普悠瑪228)
const STN_MODE_LABELS = ['車次｜車種', '車種｜車次', '車次車種', '車種車次'];
let stnMode = (() => {
  const s = localStorage.getItem('tdx_stn_mode');
  if (s !== null) return Math.min(3, Math.max(0, parseInt(s, 10) || 0));
  return localStorage.getItem('tdx_stn_type_first') === '1' ? 1 : 0;
})();

(function() {
  const btn = document.getElementById('stn-toggle-btn');
  btn.textContent = STN_MODE_LABELS[stnMode];
})();

const _lastBoards = { from: null, to: null };

const _STN_BOARD_VIEW_LABELS = ['🚏 出發', '🏁 到達', '⇄ 兩者', '✖️ 關閉'];
let stnBoardView = 0;  // 0=出發only, 1=到達only, 2=both, 3=hidden

function _applyStnBoardView() {
  const isMobile = window.innerWidth <= 600;
  const winFrom  = document.getElementById('stn-win-from');
  const winTo    = document.getElementById('stn-win-to');
  const outer    = document.querySelector('.station-duo-outer');
  const btn      = document.getElementById('stn-board-view-btn');
  if (!winFrom || !winTo) return;
  if (!isMobile) {
    winFrom.classList.remove('stn-win-hidden');
    winTo.classList.remove('stn-win-hidden');
    if (outer) outer.style.display = '';
    return;
  }
  const hidden = stnBoardView === 3;
  if (outer) outer.style.display = hidden ? 'none' : '';
  winFrom.classList.toggle('stn-win-hidden', stnBoardView === 1);
  winTo.classList.toggle('stn-win-hidden',   stnBoardView === 0);
  if (btn) btn.textContent = _STN_BOARD_VIEW_LABELS[stnBoardView];
}

document.getElementById('stn-toggle-btn').addEventListener('click', () => {
  stnMode = (stnMode + 1) % 4;
  localStorage.setItem('tdx_stn_mode', stnMode);
  document.getElementById('stn-toggle-btn').textContent = STN_MODE_LABELS[stnMode];
  if (_lastBoards.from) renderStationBoard(_lastBoards.from.boards, _lastBoards.from.name, 'stn-from-wrap', 'stn-label-from', '出發');
  if (_lastBoards.to)   renderStationBoard(_lastBoards.to.boards,   _lastBoards.to.name,   'stn-to-wrap',   'stn-label-to',   '到達');
});

document.getElementById('stn-board-view-btn').addEventListener('click', () => {
  stnBoardView = (stnBoardView + 1) % 4;
  _applyStnBoardView();
});
window.addEventListener('resize', _applyStnBoardView);

function _sliceBoards(list, timeOf, hhmm) {
  const sorted = [...list].sort((a, b) => timeOf(a).localeCompare(timeOf(b)));
  let idx = sorted.findIndex(t => timeOf(t) >= hhmm);
  if (idx < 0) idx = Math.max(0, sorted.length - 3);
  return sorted.slice(idx, idx + 6);
}

function _makeRows(slice, timeOf) {
  return slice.map(t => {
    const sched   = (timeOf(t) || '—').slice(0, 5);
    const delayEl = t.delay > 0
      ? `<span class="stn-delay-pos">+${t.delay}分</span>`
      : `<span class="stn-delay-ok">準點</span>`;
    const noSpan   = `<span style="font-family:var(--font-mono)">${escHtml(t.train_no)}</span>`;
    const typeSpan = `<span style="color:var(--fg-dim)">${escHtml(t.train_type)}</span>`;
    let col1;
    if (stnMode >= 2) {
      const inner = stnMode === 2 ? `${noSpan} ${typeSpan}` : `${typeSpan} ${noSpan}`;
      col1 = `<td>${inner}</td>`;
    } else {
      col1 = stnMode === 1
        ? `<td>${typeSpan}</td><td>${noSpan}</td>`
        : `<td>${noSpan}</td><td>${typeSpan}</td>`;
    }
    return `<tr style="cursor:pointer" data-train-no="${escHtml(t.train_no)}">${col1}
      <td style="color:var(--fg-dim)">${escHtml(t.dest)}</td>
      <td style="font-family:var(--font-mono);text-align:right">${escHtml(sched)}</td>
      <td style="text-align:right">${delayEl}</td>
    </tr>`;
  }).join('');
}

function _dirRow(label, cls) {
  return `<tr class="stn-dir-row ${cls}"><td colspan="6">${label}</td></tr>`;
}

function renderStationBoard(boards, stationName, wrapId, labelClass, labelText) {
  const wrap = document.getElementById(wrapId);
  if (!wrap) return;

  const isArr = labelText === '到達';
  const timeOf = t => isArr ? (t.arrival || t.departure || '') : (t.departure || t.arrival || '');
  const timeLabel = isArr ? '到站' : '離站';
  const now  = new Date();
  const hhmm = `${String(now.getHours()).padStart(2,'0')}:${String(now.getMinutes()).padStart(2,'0')}`;

  const th1 = stnMode >= 2
    ? (stnMode === 2 ? '<th>車次車種</th>' : '<th>車種車次</th>')
    : (stnMode === 1 ? '<th>車種</th><th style="font-family:var(--font-mono)">#</th>'
                     : '<th style="font-family:var(--font-mono)">#</th><th>車種</th>');
  const thead = `<thead><tr>${th1}<th>終點</th><th style="text-align:right">${timeLabel}</th><th style="text-align:right">誤點</th></tr></thead>`;

  // Split by TDX Direction field: 0 = 往北 (northbound), 1 = 往南 (southbound)
  const all = boards || [];
  const hasDir = all.some(t => t.direction === 0 || t.direction === 1);

  let tbodyRows;
  if (hasDir) {
    const southSlice = _sliceBoards(all.filter(t => t.direction === 1), timeOf, hhmm);
    const northSlice = _sliceBoards(all.filter(t => t.direction === 0), timeOf, hhmm);
    const otherSlice = _sliceBoards(all.filter(t => t.direction !== 0 && t.direction !== 1), timeOf, hhmm);
    if (!southSlice.length && !northSlice.length && !otherSlice.length) {
      tbodyRows = null;
    } else {
      tbodyRows = (northSlice.length ? _dirRow('↑ 往北列車', 'dir-up')   + _makeRows(northSlice, timeOf) : '')
               + (southSlice.length ? _dirRow('↓ 往南列車', 'dir-down') + _makeRows(southSlice, timeOf) : '')
               + (otherSlice.length ? _dirRow('　其他列車', '')          + _makeRows(otherSlice, timeOf) : '');
    }
  } else {
    const slice = _sliceBoards(all, timeOf, hhmm);
    tbodyRows = slice.length ? _makeRows(slice, timeOf) : null;
  }

  const tableHtml = tbodyRows !== null
    ? `<table class="stn-table">${thead}<tbody>${tbodyRows}</tbody></table>`
    : `<div style="padding:8px 10px;font-size:11px;color:var(--fg-dim)">目前無車次資訊</div>`;

  wrap.innerHTML =
    `<div class="station-card">
      <div class="station-card-header"><span class="stn-label ${labelClass}">${labelText}</span>${escHtml(stationName)}</div>
      ${tableHtml}
    </div>`;

  // Event delegation for station board row clicks
  wrap.querySelector('.stn-table tbody')?.addEventListener('click', e => {
    const row = e.target.closest('tr[data-train-no]');
    if (!row) return;
    const no = encodeURIComponent(row.dataset.trainNo);
    const f  = encodeURIComponent(fromSel.value);
    const t  = encodeURIComponent(toSel.value);
    location.href = `/train/${no}?from=${f}&to=${t}`;
  });
}

async function fetchStationBoards() {
  const fromCode = fromSel.value;
  const toCode   = toSel.value;
  if (!fromCode || !toCode || fromCode === toCode) return;

  const fromName = STATION_MAP.get(fromCode)?.name || fromCode;
  const toName   = STATION_MAP.get(toCode)?.name   || toCode;

  document.getElementById('station-live-wrap').style.display = 'block';
  // Show placeholders while loading
  document.getElementById('stn-from-wrap').innerHTML =
    `<div class="station-card"><div class="station-card-header"><span class="stn-label stn-label-from">出發</span>${escHtml(fromName)}</div><div style="padding:8px 10px;font-size:11px;color:var(--fg-dim)">載入中…</div></div>`;
  document.getElementById('stn-to-wrap').innerHTML =
    `<div class="station-card"><div class="station-card-header"><span class="stn-label stn-label-to">到達</span>${escHtml(toName)}</div><div style="padding:8px 10px;font-size:11px;color:var(--fg-dim)">載入中…</div></div>`;

  const [r1, r2] = await Promise.allSettled([
    fetch(`/api/liveboard?station=${fromCode}`).then(r => r.json()),
    fetch(`/api/liveboard?station=${toCode}`).then(r => r.json()),
  ]);

  const d1 = r1.status === 'fulfilled' ? r1.value : {};
  const d2 = r2.status === 'fulfilled' ? r2.value : {};

  _lastBoards.from = { boards: d1.boards || [], name: fromName };
  _lastBoards.to   = { boards: d2.boards || [], name: toName };

  renderStationBoard(d1.boards || [], fromName, 'stn-from-wrap', 'stn-label-from', '出發');
  renderStationBoard(d2.boards || [], toName,   'stn-to-wrap',   'stn-label-to',   '到達');
  _applyStnBoardView();

  // Also update delay overlay if live data was already shown
  if (d1.delays || d2.delays) {
    liveDelayMap = {};
    for (const [no, delay] of Object.entries(d1.delays || {})) liveDelayMap[no] = delay;
    for (const [no, delay] of Object.entries(d2.delays || {})) {
      if (!(no in liveDelayMap) || liveDelayMap[no] === 0) liveDelayMap[no] = delay;
    }
    overlayDelays(document.getElementById('scroll-ab'));
    overlayDelays(document.getElementById('scroll-ba'));
  }
}

// ── Live delay overlay ─────────────────────────────────────────────────────
function overlayDelays(containerEl) {
  if (!containerEl) return;
  for (const row of containerEl.querySelectorAll('tr[data-train-no]')) {
    const no    = row.dataset.trainNo;
    const delay = liveDelayMap[no];
    const cell  = row.querySelector('td.remark');
    if (!cell) continue;
    // Remove any previous delay tag
    const prev = cell.querySelector('.remark-delay');
    if (prev) prev.remove();
    if (delay > 0) {
      const tag = document.createElement('span');
      tag.className = 'remark-tag remark-delay';
      tag.textContent = `誤${delay}分`;
      cell.prepend(tag);
    }
  }
}

async function fetchLive() {
  const btn      = document.getElementById('live-btn');
  const fromCode = fromSel.value;
  const toCode   = toSel.value;
  btn.classList.add('live-loading');
  btn.disabled = true;
  try {
    const [res1, res2] = await Promise.allSettled([
      fetch(`/api/liveboard?station=${fromCode}`).then(r => r.json()),
      fetch(`/api/liveboard?station=${toCode}`).then(r => r.json()),
    ]);
    const d1 = res1.status === 'fulfilled' ? res1.value : {};
    const d2 = res2.status === 'fulfilled' ? res2.value : {};
    if (d1.error && d2.error) throw new Error(d1.error || d2.error);
    // Merge delays: prefer non-zero values (a train delayed at origin
    // should not be overwritten by an on-time read at destination).
    liveDelayMap = {};
    for (const [no, delay] of Object.entries(d1.delays || {})) {
      liveDelayMap[no] = delay;
    }
    for (const [no, delay] of Object.entries(d2.delays || {})) {
      if (!(no in liveDelayMap) || liveDelayMap[no] === 0)
        liveDelayMap[no] = delay;
    }
    btn.classList.remove('live-loading');
    btn.classList.add('live-on');
    const fetched = Math.max(d1.fetched_at || 0, d2.fetched_at || 0);
    const t  = new Date(fetched * 1000);
    const hm = `${String(t.getHours()).padStart(2,'0')}:${String(t.getMinutes()).padStart(2,'0')}:${String(t.getSeconds()).padStart(2,'0')}`;
    btn.textContent = `🟢 即時 ${hm}`;
    overlayDelays(document.getElementById('scroll-ab'));
    overlayDelays(document.getElementById('scroll-ba'));
  } catch (e) {
    btn.classList.remove('live-loading');
    btn.classList.remove('live-on');
    btn.textContent = '🟡 即時狀態';
    showError(`即時狀態無法取得：${e.message}`);
  } finally {
    btn.disabled = false;
  }
}

// ── Event bindings ────────────────────────────────────────────────────────
document.getElementById('query-btn').addEventListener('click', queryGeneral);
document.getElementById('daily-btn').addEventListener('click', queryDaily);
document.getElementById('live-btn').addEventListener('click', fetchLive);
let _queryDebounce = null;
function _debouncedQuery() {
  clearTimeout(_queryDebounce);
  _queryDebounce = setTimeout(queryGeneral, 300);
}
fromSel.addEventListener('change', _debouncedQuery);
toSel.addEventListener('change', _debouncedQuery);

// ── Timer update ──────────────────────────────────────────────────────────
function updateTimer() {
  const now = new Date();
  const hours = String(now.getHours()).padStart(2, '0');
  const minutes = String(now.getMinutes()).padStart(2, '0');
  const seconds = String(now.getSeconds()).padStart(2, '0');
  document.getElementById('timer').textContent = `${hours}:${minutes}:${seconds}`;
}

// Update timer immediately and every second
updateTimer();
setInterval(updateTimer, 1000);

// Auto-query on load with saved prefs — fetch today's timetable
// (queryDaily always calls fetchStationBoards() internally)
queryDaily();

// Warm-up ping on page load to wake Render free-tier from cold start.
fetch("/health").catch(() => {});
