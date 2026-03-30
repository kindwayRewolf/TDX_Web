// TRAIN_NO is declared in the inline <script> block in train_detail.html
// (Jinja2-injected variable), available as a global here.
const params   = new URLSearchParams(location.search);
const FROM     = params.get('from') || '';
const TO       = params.get('to')   || '';

// ── Helpers ────────────────────────────────────────────────────────────────
function fmtTime(iso) {
  if (!iso) return '—';
  // If already HH:MM format
  if (/^\d{2}:\d{2}/.test(iso)) return iso.slice(0, 5);
  try {
    const d = new Date(iso);
    return `${String(d.getMonth()+1).padStart(2,'0')}/${String(d.getDate()).padStart(2,'0')} ` +
           `${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}`;
  } catch { return iso; }
}

function badgeClass(typeShort) {
  if (!typeShort) return 'badge-local';
  if (typeShort.startsWith('區間快')) return 'badge-express';
  if (typeShort.startsWith('區間'))   return 'badge-local';
  return 'badge-reserved';
}

function statusLabel(status) {
  if (status === 0) return ['status-0', '全部停駛'];
  if (status === 1) return ['status-1', '正常營運'];
  return ['status-2', '異常狀況'];
}

// ── Render alerts ──────────────────────────────────────────────────────────
function renderAlerts(alerts) {
  const el = document.getElementById('alert-content');
  if (!alerts || alerts.length === 0) {
    el.innerHTML = '<div class="empty-notice">目前無通阻通知</div>';
    return;
  }
  el.innerHTML = '<div class="alert-list">' +
    alerts.map(a => {
      const [cls, label] = statusLabel(a.Status ?? 1);
      const title = escHtml(a.Title || '（無標題）');
      const desc  = escHtml(a.Description || '');
      const start = a.StartTime ? fmtTime(a.StartTime) : '';
      const end   = a.EndTime   ? fmtTime(a.EndTime)   : '';
      const time  = start ? `${start}${end ? ' ～ ' + end : ''}` : '';
      return `<div class="alert-card">
        <div class="alert-header">
          <span class="alert-status ${cls}">${label}</span>
          <span class="alert-title">${title}</span>
        </div>
        ${desc ? `<div class="alert-desc">${desc}</div>` : ''}
        ${time ? `<div class="alert-time">⏱ ${time}</div>` : ''}
      </div>`;
    }).join('') +
  '</div>';
}

// ── Render news ────────────────────────────────────────────────────────────
function renderNews(news) {
  const el = document.getElementById('news-content');
  // Filter to zh-tw only (or all if none)
  const filtered = (news || []).filter(n => !n.Language || n.Language.toLowerCase().startsWith('zh'));
  const items    = filtered.length ? filtered : (news || []);
  if (!items.length) {
    el.innerHTML = '<div class="empty-notice">目前無最新消息</div>';
    return;
  }
  el.innerHTML = '<div class="news-list">' +
    items.slice(0, 10).map(n => {
      const title = escHtml(n.Title || '（無標題）');
      const desc  = escHtml(n.Description || '');
      const pub   = n.PublishTime ? fmtTime(n.PublishTime) : '';
      return `<div class="news-card">
        <div class="news-title">${title}</div>
        ${desc ? `<div class="news-desc">${desc}</div>` : ''}
        ${pub ? `<div class="news-time">🗓 ${pub}</div>` : ''}
      </div>`;
    }).join('') +
  '</div>';
}

// ── HTML escape helper (prevents XSS from URL params used in innerHTML) ──────
function escHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// ── Determine which stop index is "current" ───────────────────────────────
function computeStartIdx(stops, liveStationId) {
  // 1. Live data: train just departed liveStationId
  if (liveStationId) {
    const idx = stops.findIndex(s => s.station_id === liveStationId);
    if (idx !== -1) return idx + 1;  // next stop after the one just departed
    // Passing station — fall through to time-based estimate
  }
  // 2. Time-based: find the last stop whose departure has already passed
  const now = new Date();
  const hhmm = `${String(now.getHours()).padStart(2,'0')}:${String(now.getMinutes()).padStart(2,'0')}`;
  let lastPastIdx = -1;
  for (let i = 0; i < stops.length; i++) {
    const dep = stops[i].departure || stops[i].arrival || '';
    if (dep && dep.slice(0, 5) <= hhmm) lastPastIdx = i;
  }
  if (lastPastIdx !== -1) return lastPastIdx + 1;
  // 3. FROM station anchor (train hasn't departed yet)
  if (FROM) {
    const idx = stops.findIndex(s => s.station_id === FROM);
    if (idx !== -1) return idx;
  }
  return 0;
}

// ── Render train stops ─────────────────────────────────────────────────────
function renderTrain(data, liveData) {
  // Header
  const typeShort = data.train_type_short || '';
  document.getElementById('header-type').innerHTML =
    `<span class="train-type-badge ${badgeClass(typeShort)}">${escHtml(typeShort)}</span>`;
  document.getElementById('header-route').textContent = data.route || '';

  // Meta chips
  const metaEl = document.getElementById('train-meta');
  const chips = [];
  if (data.route)     chips.push(`<span class="meta-chip"><strong>路線</strong> ${escHtml(data.route)}</span>`);
  if (data.trip_line) chips.push(`<span class="meta-chip"><strong>via</strong> ${escHtml(data.trip_line)}</span>`);
  if (data.bike)      chips.push(`<span class="meta-chip"><strong>🚲</strong> 可攜帶自行車</span>`);
  if (data.note)      chips.push(`<span class="meta-chip">${escHtml(data.note)}</span>`);
  metaEl.innerHTML = chips.join('');

  // Live position chip
  const liveChip = document.getElementById('live-status-chip');
  const liveId   = liveData?.station_id || null;
  if (liveData && liveData.station_name) {
    const delay = liveData.delay_time > 0 ? ` +${escHtml(String(liveData.delay_time))}分` : '';
    liveChip.innerHTML =
      `<span style="font-size:12px;font-weight:400;color:var(--fg-dim);letter-spacing:0">
        ⦿ 剛離開 <strong style="color:var(--blue)">${escHtml(liveData.station_name)}</strong>${escHtml(delay)}
      </span>`;
  } else {
    liveChip.innerHTML =
      `<span style="font-size:12px;font-weight:400;color:var(--fg-dim);letter-spacing:0">（即時位置不可用）</span>`;
  }

  // Stop table — ALL stops, past dimmed, current highlighted, FROM/TO marked
  const allStops = data.stops || [];
  if (!allStops.length) {
    document.getElementById('train-content').innerHTML =
      '<div class="empty-notice">查無停靠站資訊</div>';
    return;
  }

  const startIdx   = computeStartIdx(allStops, liveId);
  // currentIdx: the stop the train just departed (-1 = hasn't started / unknown)
  const currentIdx = (startIdx > 0) ? startIdx - 1 : -1;

  const rows = allStops.map((s, i) => {
    const isPast  = i < startIdx && i !== currentIdx;
    const isCurr  = i === currentIdx;
    const isFrom  = FROM && s.station_id === FROM;
    const isTo    = TO   && s.station_id === TO;

    // Priority: FROM > TO > current > past
    const trClass   = isFrom ? 'highlight-from'
                    : isTo   ? 'highlight-to'
                    : isCurr ? 'highlight-live'
                    : isPast ? 'row-past'
                    : '';
    const nameClass = isFrom ? 'stop-name hl-from'
                    : isTo   ? 'stop-name hl-to'
                    : isCurr ? 'stop-name hl-live'
                    : 'stop-name';
    const label = isFrom ? '<span class="hl-label hl-label-from">出發</span>'
                : isTo   ? '<span class="hl-label hl-label-to">抵達</span>'
                : isCurr ? '<span class="hl-label hl-label-live">已到站</span>'
                : '';
    const timeClass = (isFrom || isTo || isCurr) ? 'stop-time main' : 'stop-time';
    const curr      = isCurr ? ' data-current="true"' : '';
    return `<tr class="${trClass}"${curr}>
      <td class="stop-seq col-seq">${escHtml(String(s.seq))}</td>
      <td><span class="${nameClass}">${escHtml(s.station_name || s.station_id)}</span>${label}</td>
      <td class="${timeClass} col-arr">${escHtml(s.arrival  || '\u2014')}</td>
      <td class="${timeClass}">${escHtml(s.departure || '\u2014')}</td>
      <td class="col-phone" style="font-size:12px;color:var(--fg-dim)">${s.phone ? `<a href="tel:${encodeURIComponent(s.phone)}" style="color:inherit;text-decoration:none">${escHtml(s.phone)}</a>` : ''}</td>
      <td class="col-addr" style="font-size:12px;color:var(--fg-dim);max-width:200px;overflow:hidden;text-overflow:ellipsis">${escHtml(s.address || '')}</td>
    </tr>`;
  }).join('');

  document.getElementById('train-content').innerHTML = `
    <div class="stop-table-wrap">
      <table class="stop-table">
        <thead><tr>
          <th class="col-seq">#</th><th>車站</th><th class="col-arr">到站</th><th>離站</th>
          <th class="col-phone">電話</th><th class="col-addr">地址</th>
        </tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;

  // Scroll the wrap to show current/from row near top
  const scrollTarget =
    document.querySelector('.stop-table tr[data-current="true"]') ||
    document.querySelector('.stop-table tr.highlight-from');
  if (scrollTarget) {
    setTimeout(() => {
      const wrap = document.querySelector('.stop-table-wrap');
      if (wrap) {
        // Offset relative to the scroll container
        const rowTop = scrollTarget.offsetTop - (scrollTarget.offsetParent?.offsetTop || 0);
        wrap.scrollTop = Math.max(0, rowTop - 37); // one row above
      }
    }, 200);
  }
}

// ── Load all data in parallel ──────────────────────────────────────────────
function fetchWithTimeout(url, ms = 15000) {
  const ctrl = new AbortController();
  const tid  = setTimeout(() => ctrl.abort(), ms);
  return fetch(url, { signal: ctrl.signal })
    .then(r => r.json())
    .finally(() => clearTimeout(tid));
}

// ── Client-side cache for alert / news (15 min TTL) ──────────────────────
const _DETAIL_CACHE_TTL = 15 * 60 * 1000;
function _cacheGet(key) {
  try {
    const v = JSON.parse(localStorage.getItem(key) || 'null');
    if (v && Date.now() - v.ts < _DETAIL_CACHE_TTL) return v.data;
  } catch {}
  return null;
}
function _cacheSet(key, data) {
  try { localStorage.setItem(key, JSON.stringify({ ts: Date.now(), data })); } catch {}
}

async function loadAll() {
  // Alert / news: serve from localStorage if fresh, else fetch
  const cachedAlert = _cacheGet('tdx_detail_alert');
  const cachedNews  = _cacheGet('tdx_detail_news');

  const [trainRes, liveRes, alertRes, newsRes] =
    await Promise.allSettled([
      fetchWithTimeout(`/api/train/${TRAIN_NO}`),
      fetchWithTimeout(`/api/trainlive/${TRAIN_NO}`),
      cachedAlert ? Promise.resolve(cachedAlert) : fetchWithTimeout('/api/alert'),
      cachedNews  ? Promise.resolve(cachedNews)  : fetchWithTimeout('/api/news'),
    ]);

  // ─ Alerts
  const alertData = alertRes.status === 'fulfilled' ? alertRes.value : null;
  if (alertData && !alertData.error) {
    if (!cachedAlert) _cacheSet('tdx_detail_alert', alertData);
    renderAlerts(alertData.alerts || []);
  } else {
    document.getElementById('alert-content').innerHTML =
      '<div class="empty-notice">通阻資訊暫時無法取得</div>';
  }

  // ─ News
  const newsData = newsRes.status === 'fulfilled' ? newsRes.value : null;
  if (newsData && !newsData.error) {
    if (!cachedNews) _cacheSet('tdx_detail_news', newsData);
    renderNews(newsData.news || []);
  } else {
    document.getElementById('news-content').innerHTML =
      '<div class="empty-notice">最新消息暫時無法取得</div>';
  }

  // ─ Train + live position
  const liveData = (liveRes.status === 'fulfilled' && liveRes.value?.live)
    ? liveRes.value.live : null;

  // ─ Train stop list
  if (trainRes.status === 'fulfilled' && !trainRes.value?.error) {
    renderTrain(trainRes.value, liveData);
  } else {
    const msg = trainRes.status === 'fulfilled'
      ? (trainRes.value?.error || '查詢失敗')
      : '網路錯誤';
    document.getElementById('train-content').innerHTML = '';
    const errEl = document.getElementById('error-box');
    errEl.textContent = `列車 ${TRAIN_NO} 查詢失敗：${msg}`;
    errEl.style.display = 'block';
    document.getElementById('header-route').textContent = '查無資料';
  }

  document.getElementById('page-footer').innerHTML =
    `資料介接「<a href="https://tdx.transportdata.tw/" target="_blank" rel="noopener">交通部TDX平臺</a>」&amp; 平臺標章　列車 ${TRAIN_NO}`;
}

loadAll();

// ── Collapsible sections (mobile: collapsed by default; desktop: always open) ──
const IS_MOBILE = document.documentElement.classList.contains('is-mobile');

function toggleSection(id) {
  if (!IS_MOBILE) return;   // desktop: click does nothing (always open)
  const body  = document.getElementById(id + '-body');
  const title = document.getElementById(id + '-title');
  const isOpen = body.classList.toggle('open');
  title.classList.toggle('open', isOpen);
}

// On desktop: immediately open both collapsible bodies
if (!IS_MOBILE) {
  ['alert', 'news'].forEach(id => {
    document.getElementById(id + '-body').classList.add('open');
  });
}
