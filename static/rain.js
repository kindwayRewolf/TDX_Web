(() => {
  const AUTO_REFRESH_MS = 10 * 60 * 1000;
  const HISTORY_LIMIT = 10;
  const HISTORY_KEY = 'kindway_rain_station_history_v1';
  const STATION_KEY = 'kindway_rain_station_id';
  const FILTERS_KEY = 'kindway_rain_filters_v1';
  const periods = ['10min', '1hr', '3hr', '6hr', '12hr', '24hr'];
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
  let savedFilters = {};
  let savedHistory = [];
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
  const state = {
    stations: [],
    history: savedHistory,
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
      if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
      state.stations = Array.isArray(data.stations) ? data.stations : [];
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
  refreshButton.addEventListener('click', () => loadStations(true));
  window.addEventListener('beforeunload', () => {
    if (state.refreshTimer) clearTimeout(state.refreshTimer);
  });

  loadStations(false);
})();
