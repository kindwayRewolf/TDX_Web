(() => {
  const AUTO_REFRESH_MS = 10 * 60 * 1000;
  const HISTORY_LIMIT = 10;
  const HISTORY_KEY = 'kindway_rain_station_history_v1';
  const STATION_KEY = 'kindway_rain_station_id';
  const FILTERS_KEY = 'kindway_rain_filters_v1';
  const REGION_SELECTIONS_KEY = 'kindway_rain_region_selections_v1';
  const periods = ['10min', '1hr', '3hr', '6hr', '12hr', '24hr'];
  const regions = [
    { key: 'songshan', name: '松山', stationId: 'C0AH70' },
    { key: 'xizhi', name: '國三S010K', stationId: 'CAA030' },
    { key: 'xinfeng', name: '新豐', stationId: 'C0D590' },
    { key: 'shilin', name: '平等國小', stationId: 'A1AA20' },
  ];
  const stationSelect = document.getElementById('station-select');
  const historySelect = document.getElementById('history-select');
  const historyCount = document.getElementById('history-count');
  const countySelect = document.getElementById('county-select');
  const townSelect = document.getElementById('town-select');
  const searchInput = document.getElementById('station-search');
  const refreshButton = document.getElementById('refresh-btn');
  const statusText = document.getElementById('rain-status');
  const statusDot = document.getElementById('status-dot');
  const errorBox = document.getElementById('rain-error');
  const stationCount = document.getElementById('station-count');
  const apiCallCount = document.getElementById('api-call-count');
  let savedFilters = {};
  let savedHistory = [];
  let savedRegionIds = {};
  try {
    savedFilters = JSON.parse(localStorage.getItem(FILTERS_KEY) || '{}');
  } catch {}
  try {
    const parsedHistory = JSON.parse(localStorage.getItem(HISTORY_KEY) || '[]');
    if (Array.isArray(parsedHistory)) {
      savedHistory = parsedHistory
        .filter(item => item && typeof item.id === 'string')
        .slice(0, HISTORY_LIMIT);
    }
  } catch {}
  try {
    savedRegionIds = JSON.parse(localStorage.getItem(REGION_SELECTIONS_KEY) || '{}');
  } catch {}
  const state = {
    stations: [],
    history: savedHistory,
    regionIds: Object.fromEntries(regions.map(region => [region.key, savedRegionIds[region.key] || region.stationId])),
    selectedId: savedFilters.stationId || localStorage.getItem(STATION_KEY) || '',
    county: savedFilters.county || '',
    town: savedFilters.town || '',
    search: savedFilters.search || '',
    refreshTimer: null,
    loading: false,
  };
  searchInput.value = state.search;

  function saveFilters() {
    try {
      localStorage.setItem(FILTERS_KEY, JSON.stringify({
        stationId: state.selectedId,
        county: state.county,
        town: state.town,
        search: state.search,
      }));
      if (state.selectedId) localStorage.setItem(STATION_KEY, state.selectedId);
    } catch {}
  }

  function saveHistory() {
    try {
      localStorage.setItem(HISTORY_KEY, JSON.stringify(state.history));
    } catch {}
  }

  function addOption(select, value, label) {
    const option = document.createElement('option');
    option.value = value;
    option.textContent = label;
    select.appendChild(option);
  }

  function replaceOptions(select, firstLabel, values, selectedValue) {
    select.replaceChildren();
    addOption(select, '', firstLabel);
    for (const value of values) addOption(select, value, value);
    select.value = values.includes(selectedValue) ? selectedValue : '';
    select.disabled = values.length === 0;
  }

  function renderHistory() {
    const available = state.history.filter(item => state.stations.some(station => station.id === item.id));
    historySelect.replaceChildren();
    addOption(historySelect, '', available.length ? '選擇最近測站' : '尚無使用紀錄');
    for (const item of available) {
      addOption(historySelect, item.id, `${item.name} · ${item.town} (${item.id})`);
    }
    historySelect.disabled = available.length === 0;
    historyCount.textContent = available.length ? `${available.length}/${HISTORY_LIMIT}` : '';
  }

  function rememberStation(station) {
    if (!station) return;
    state.history = [
      { id: station.id, name: station.name, county: station.county, town: station.town },
      ...state.history.filter(item => item.id !== station.id),
    ].slice(0, HISTORY_LIMIT);
    saveHistory();
    renderHistory();
  }

  function updateFilters(restoreSaved = false) {
    const previousCounty = restoreSaved ? state.county : countySelect.value;
    const previousTown = restoreSaved ? state.town : townSelect.value;
    const counties = [...new Set(state.stations.map(station => station.county))].sort((a, b) => a.localeCompare(b, 'zh-Hant'));
    replaceOptions(countySelect, '全部縣市', counties, previousCounty);

    const county = countySelect.value;
    state.county = county;
    const towns = [...new Set(state.stations
      .filter(station => !county || station.county === county)
      .map(station => station.town))].sort((a, b) => a.localeCompare(b, 'zh-Hant'));
    replaceOptions(townSelect, '全部鄉鎮', towns, previousTown);

    const town = townSelect.value;
    state.town = town;
    const query = searchInput.value.trim().toLocaleLowerCase();
    state.search = searchInput.value;
    const filtered = state.stations.filter(station => {
      if (county && station.county !== county) return false;
      if (town && station.town !== town) return false;
      if (query && !`${station.name} ${station.id} ${station.county} ${station.town}`.toLocaleLowerCase().includes(query)) return false;
      return true;
    });

    stationSelect.replaceChildren();
    for (const station of filtered) {
      addOption(stationSelect, station.id, `${station.name} · ${station.town} (${station.id})`);
    }
    stationSelect.disabled = filtered.length === 0;
    stationCount.textContent = filtered.length ? `${filtered.length} 站` : '0 站';

    const retained = filtered.some(station => station.id === state.selectedId)
      ? state.selectedId
      : (filtered[0]?.id || '');
    stationSelect.value = retained;
    state.selectedId = retained;
    if (retained) {
      renderStation(state.stations.find(station => station.id === retained));
    } else {
      renderStation(null);
    }
    saveFilters();
  }

  function formatAmount(value) {
    return value === null || value === undefined || !Number.isFinite(Number(value))
      ? '—'
      : Number(value).toFixed(1);
  }

  function formatObservationTime(value) {
    if (!value) return '—';
    return value.replace('T', ' ').replace(/([+-]\d{2}:\d{2}|Z)$/, '');
  }

  function renderStation(station) {
    document.getElementById('selected-location').textContent = station
      ? `${station.county} · ${station.town}`
      : '尚未選擇測站';
    document.getElementById('selected-station-name').textContent = station?.name || '雨量觀測';
    document.getElementById('observation-time').textContent = formatObservationTime(station?.obs_time);
    for (const period of periods) {
      document.getElementById(`rain-${period}`).textContent = formatAmount(station?.rainfall?.[period]);
    }
  }

  function renderRegion(region) {
    const card = document.querySelector(`[data-region="${region.key}"]`);
    const stationId = state.regionIds[region.key];
    const station = state.stations.find(item => item.id === stationId);
    card.querySelector('[data-region-location]').textContent = station
      ? `${station.county} · ${station.town}`
      : `${region.name} · 未知區域`;
    card.querySelector('[data-region-id]').textContent = stationId;
    card.querySelector('[data-region-time]').textContent = formatObservationTime(station?.obs_time);
    card.querySelector('[data-region-rain="10min"]').textContent = formatAmount(station?.rainfall?.['10min']);
    card.querySelector('[data-region-rain="1hr"]').textContent = formatAmount(station?.rainfall?.['1hr']);
    card.querySelector('[data-region-rain="24hr"]').textContent = formatAmount(station?.rainfall?.['24hr']);
  }

  function updateRegionCards() {
    for (const region of regions) {
      const select = document.getElementById(`region-${region.key}`);
      const selectedId = state.regionIds[region.key];
      select.replaceChildren();
      addOption(select, '', '選擇測站');
      for (const station of state.stations) {
        addOption(select, station.id, `${station.name} · ${station.county} ${station.town} (${station.id})`);
      }
      if (selectedId && !state.stations.some(station => station.id === selectedId)) {
        addOption(select, selectedId, `${region.name} (${selectedId}) · 暫無資料`);
      }
      select.value = selectedId;
      select.disabled = state.stations.length === 0;
      renderRegion(region);
    }
  }

  function setStatus(message, kind = 'live') {
    statusText.textContent = message;
    statusDot.classList.toggle('is-error', kind === 'error');
    statusDot.classList.toggle('is-live', kind === 'live');
  }

  function scheduleRefresh(delay = AUTO_REFRESH_MS) {
    if (state.refreshTimer) clearTimeout(state.refreshTimer);
    state.refreshTimer = setTimeout(() => loadStations(false), delay);
  }

  async function loadStations(force) {
    if (state.loading) return;
    state.loading = true;
    refreshButton.disabled = true;
    setStatus(force ? '正在更新觀測資料…' : '正在載入測站資料…', 'loading');
    errorBox.hidden = true;
    try {
      const url = force ? '/api/rain/stations?refresh=1' : '/api/rain/stations';
      const response = await fetch(url, { cache: 'no-store' });
      const data = await response.json();
      const callsToday = Number(data.api_calls_today);
      if (Number.isFinite(callsToday)) apiCallCount.textContent = `今日 CWA API 呼叫：${callsToday} 次`;
      if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
      state.stations = Array.isArray(data.stations) ? data.stations : [];
      updateRegionCards();
      updateFilters(true);
      renderHistory();
      for (const control of [countySelect, townSelect, searchInput]) control.disabled = state.stations.length === 0;
      refreshButton.disabled = false;
      const refreshedAt = data.fetched_at ? new Date(data.fetched_at * 1000) : new Date();
      document.getElementById('server-updated').textContent = `資料更新 ${refreshedAt.toLocaleTimeString('zh-TW', { hour: '2-digit', minute: '2-digit' })}`;
      setStatus(`觀測資料已更新 · ${state.stations.length} 個測站`);
      scheduleRefresh();
    } catch (error) {
      setStatus('資料暫時無法取得', 'error');
      errorBox.textContent = error.message.includes('CWA_API_KEY')
        ? '尚未設定氣象署 API 金鑰。請在 Kindway 專案的 .env 設定 CWA_API_KEY。'
        : `雨量資料載入失敗：${error.message}`;
      errorBox.hidden = false;
      refreshButton.disabled = false;
      scheduleRefresh(60_000);
    } finally {
      state.loading = false;
    }
  }

  countySelect.addEventListener('change', () => updateFilters());
  townSelect.addEventListener('change', () => updateFilters());
  searchInput.addEventListener('input', () => updateFilters());
  stationSelect.addEventListener('change', () => {
    state.selectedId = stationSelect.value;
    const selectedStation = state.stations.find(station => station.id === state.selectedId);
    renderStation(selectedStation);
    rememberStation(selectedStation);
    saveFilters();
  });
  historySelect.addEventListener('change', () => {
    const station = state.stations.find(item => item.id === historySelect.value);
    if (!station) return;
    state.county = station.county;
    state.town = station.town;
    state.search = '';
    searchInput.value = '';
    state.selectedId = station.id;
    updateFilters(true);
    rememberStation(station);
    historySelect.value = '';
  });
  for (const region of regions) {
    document.getElementById(`region-${region.key}`).addEventListener('change', event => {
      state.regionIds[region.key] = event.target.value;
      try {
        localStorage.setItem(REGION_SELECTIONS_KEY, JSON.stringify(state.regionIds));
      } catch {}
      renderRegion(region);
    });
  }
  refreshButton.addEventListener('click', () => loadStations(true));
  window.addEventListener('beforeunload', () => {
    if (state.refreshTimer) clearTimeout(state.refreshTimer);
  });

  loadStations(false);
})();
