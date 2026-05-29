// TDX Bus - Group-based ETA Dashboard
// Persistence: browser localStorage
// API: /api/bus/search, /api/bus/stops, /api/bus/batch_eta, /api/bus/cities

(function() {
'use strict';

// ===== Storage =====
const STORAGE_KEY = 'tdx_bus_data';

function loadData() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) return JSON.parse(raw);
  } catch(e) { /* ignore */ }
  return {
    groups: [{ id: 'g1', name: 'My Stops', stops: [] }],
    activeGroupId: 'g1',
    refreshInterval: 15000,
  };
}

function saveData() {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(appData));
}

let appData = loadData();

// ===== State =====
let selectedCities = ['Taipei', 'NewTaipei'];
let allCities = [];
let searchTimer = null;
let refreshTimer = null;

// Route detail state
let routeDetailCity = '';
let routeDetailName = '';
let routeDetailDeparture = '';
let routeDetailDestination = '';
let routeDetailStops = [];
let routeDetailDir = 0;

// Pending add-to-group
let pendingStop = null;

// ===== Init =====
document.addEventListener('DOMContentLoaded', function() {
  loadCities();
  renderGroupBar();
  renderDashboard();
  startRefresh();
  document.getElementById('searchInput').addEventListener('input', onSearchInput);
});

// ===== API =====
async function apiFetch(url, opts) {
  const resp = await fetch(url, opts);
  if (!resp.ok) {
    const err = await resp.json().catch(function() { return {}; });
    throw new Error(err.error || 'HTTP ' + resp.status);
  }
  return resp.json();
}

// ===== Cities =====
async function loadCities() {
  try {
    allCities = await apiFetch('/api/bus/cities');
  } catch(e) {
    allCities = [
      {City: 'Taipei', CityNameZH: '\u81fa\u5317\u5e02'},
      {City: 'NewTaipei', CityNameZH: '\u65b0\u5317\u5e02'},
    ];
  }
}

// ===== Group bar =====
function renderGroupBar() {
  const bar = document.getElementById('groupBar');
  let html = appData.groups.map(function(g) {
    const cls = g.id === appData.activeGroupId ? 'active' : '';
    return '<div class="group-tab ' + cls + '" onclick="switchGroup(\'' + g.id + '\')">' + esc(g.name) + '</div>';
  }).join('');
  html += '<button class="group-add-btn" onclick="addGroup()">+</button>';
  bar.innerHTML = html;
}

window.switchGroup = function(id) {
  appData.activeGroupId = id;
  saveData();
  renderGroupBar();
  renderDashboard();
  restartRefresh();
};

window.addGroup = function() {
  const name = prompt('Group name:');
  if (!name || !name.trim()) return;
  const id = 'g' + Date.now();
  appData.groups.push({ id: id, name: name.trim(), stops: [] });
  appData.activeGroupId = id;
  saveData();
  renderGroupBar();
  renderDashboard();
};

// ===== Dashboard =====
function getActiveGroup() {
  return appData.groups.find(function(g) { return g.id === appData.activeGroupId; }) || appData.groups[0];
}

function renderDashboard() {
  const container = document.getElementById('dashboard');
  const group = getActiveGroup();
  if (!group || group.stops.length === 0) {
    container.innerHTML = '<div class="empty-state"><h3>No saved stops</h3><p>Tap "Search route..." to find and add bus stops</p></div>';
    return;
  }

  const html = group.stops.map(function(s, i) {
    const etaId = 'eta-' + i;
    return '<div class="dash-item">' +
      '<div class="dash-route-badge">' + esc(s.routeName) + '</div>' +
      '<div class="dash-info">' +
        '<div class="dash-stop-name">' + esc(s.stopName) + '</div>' +
        '<div class="dash-direction">' + esc(s.dirLabel || '') + '</div>' +
      '</div>' +
      '<div class="dash-eta" id="' + etaId + '">' +
        '<div class="dash-eta-text eta-c-dim">--</div>' +
      '</div>' +
      '<button class="dash-remove" onclick="removeStop(' + i + ')">&#10005;</button>' +
    '</div>';
  }).join('');
  container.innerHTML = html;
  fetchGroupEta();
}

window.removeStop = function(idx) {
  const group = getActiveGroup();
  group.stops.splice(idx, 1);
  saveData();
  renderDashboard();
};

// ===== Batch ETA =====
async function fetchGroupEta() {
  const group = getActiveGroup();
  if (!group || group.stops.length === 0) return;

  const body = group.stops.map(function(s) {
    return {
      city: s.city,
      routeName: s.routeName,
      direction: s.direction,
      stopUID: s.stopUID,
      stopName: s.stopName,
    };
  });

  try {
    const results = await apiFetch('/api/bus/batch_eta', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body),
    });

    results.forEach(function(r, i) {
      const el = document.getElementById('eta-' + i);
      if (!el) return;
      const f = formatEta(r);
      const f2 = formatEta2(r);
      el.innerHTML = '<div class="dash-eta-text ' + f.cls + '">' + f.text + '</div>' +
        (f2 ? '<div class="dash-eta-sub eta-second">' + f2 + '</div>' :
         f.sub ? '<div class="dash-eta-sub">' + f.sub + '</div>' : '');
    });

    document.getElementById('statusText').textContent = 'Updated: ' + new Date().toLocaleTimeString();
  } catch(e) {
    document.getElementById('statusText').textContent = 'Error: ' + e.message;
  }
}

function formatEta(r) {
  if (!r || r.StopStatus === -1) return { text: '--', cls: 'eta-c-dim', sub: '' };
  var st = r.StopStatus;
  if (st === 2) return { text: '\u904e\u7ad9\u4e0d\u505c', cls: 'eta-c-gray', sub: 'Bypass' };
  if (st === 3) return { text: '\u672b\u73ed\u5df2\u904e', cls: 'eta-c-gray', sub: 'Last Passed' };
  if (st === 4) return { text: '\u4eca\u65e5\u672a\u71df\u904b', cls: 'eta-c-gray', sub: 'No Service' };
  if (st === 1) {
    if (r.EstimateTime != null && r.EstimateTime > 0) {
      var min = Math.ceil(r.EstimateTime / 60);
      return { text: '~' + min + ' min', cls: 'eta-c-dim', sub: 'Not departed' };
    }
    return { text: '\u5c1a\u672a\u767c\u8eca', cls: 'eta-c-dim', sub: 'Not Departed' };
  }
  // Normal (status 0)
  if (r.EstimateTime == null) return { text: '--', cls: 'eta-c-dim', sub: '' };
  var sec = r.EstimateTime;
  if (sec <= 60) return { text: '\u5373\u5c07\u9032\u7ad9', cls: 'eta-c-arriving pulse', sub: 'Arriving' };
  if (sec <= 180) return { text: Math.ceil(sec / 60) + ' min', cls: 'eta-c-soon', sub: '' };
  return { text: Math.ceil(sec / 60) + ' min', cls: 'eta-c-normal', sub: '' };
}

function formatEta2(r) {
  // Return formatted string for 2nd bus, or null if unavailable
  if (!r || r.EstimateTime2 == null) return null;
  var sec2 = r.EstimateTime2;
  if (sec2 <= 60) return '2nd: arriving';
  return '2nd: ~' + Math.ceil(sec2 / 60) + ' min';
}

// ===== Auto-refresh =====
function startRefresh() {
  stopRefresh();
  var interval = appData.refreshInterval || 15000;
  if (interval > 0) {
    refreshTimer = setInterval(fetchGroupEta, interval);
    document.getElementById('statusRight').textContent = 'Auto: ' + (interval / 1000) + 's';
  } else {
    document.getElementById('statusRight').textContent = 'Manual';
  }
}

function stopRefresh() {
  if (refreshTimer) { clearInterval(refreshTimer); refreshTimer = null; }
}

function restartRefresh() {
  stopRefresh();
  startRefresh();
  fetchGroupEta();
}

// ===== Search =====
window.openSearch = function() {
  document.getElementById('searchOverlay').classList.add('show');
  renderCityChips();
  setTimeout(function() { document.getElementById('searchInput').focus(); }, 100);
};

window.closeSearch = function() {
  document.getElementById('searchOverlay').classList.remove('show');
  document.getElementById('searchInput').value = '';
  document.getElementById('searchContent').innerHTML = '<div class="empty-state"><p>Type a route number to search</p></div>';
};

function renderCityChips() {
  var container = document.getElementById('cityChips');
  var cities = allCities.length > 0 ? allCities : [
    {City: 'Taipei', CityNameZH: '\u81fa\u5317\u5e02'},
    {City: 'NewTaipei', CityNameZH: '\u65b0\u5317\u5e02'}
  ];
  var html = cities.slice(0, 8).map(function(c) {
    var active = selectedCities.indexOf(c.City) >= 0 ? 'active' : '';
    return '<div class="city-chip ' + active + '" onclick="toggleCity(\'' + c.City + '\')">' + c.CityNameZH + '</div>';
  }).join('');
  html += '<div class="city-chip" onclick="showAllCities()" style="color:var(--blue)">More...</div>';
  container.innerHTML = html;
}

window.toggleCity = function(city) {
  var idx = selectedCities.indexOf(city);
  if (idx >= 0) selectedCities.splice(idx, 1);
  else selectedCities.push(city);
  if (selectedCities.length === 0) selectedCities.push('Taipei');
  renderCityChips();
  var q = document.getElementById('searchInput').value.trim();
  if (q) doSearch(q);
};

window.showAllCities = function() {
  var container = document.getElementById('cityChips');
  var cities = allCities.length > 0 ? allCities : [];
  container.innerHTML = cities.map(function(c) {
    var active = selectedCities.indexOf(c.City) >= 0 ? 'active' : '';
    return '<div class="city-chip ' + active + '" onclick="toggleCity(\'' + c.City + '\')">' + c.CityNameZH + '</div>';
  }).join('');
};

function onSearchInput() {
  var q = document.getElementById('searchInput').value.trim();
  if (searchTimer) clearTimeout(searchTimer);
  if (!q) {
    document.getElementById('searchContent').innerHTML = '<div class="empty-state"><p>Type a route number to search</p></div>';
    return;
  }
  searchTimer = setTimeout(function() { doSearch(q); }, 300);
}

async function doSearch(q) {
  var content = document.getElementById('searchContent');
  content.innerHTML = '<div class="search-loading"><span class="spinner">Searching</span></div>';

  try {
    var cities = selectedCities.join(',');
    var results = await apiFetch('/api/bus/search?q=' + encodeURIComponent(q) + '&cities=' + cities);

    if (results.length === 0) {
      content.innerHTML = '<div class="empty-state"><p>No routes found</p></div>';
      return;
    }

    var html = '';
    for (var gi = 0; gi < results.length; gi++) {
      var group = results[gi];
      html += '<div class="search-city-header">' + esc(group.CityNameZH) + '</div>';
      for (var ri = 0; ri < group.Routes.length; ri++) {
        var r = group.Routes[ri];
        html += '<div class="search-route-item" onclick="openRoute(\'' + esc(group.City) + '\',\'' + esc(r.RouteName) + '\',\'' + esc(r.Departure) + '\',\'' + esc(r.Destination) + '\')">' +
          '<span class="search-route-name">' + esc(r.RouteName) + '</span>' +
          '<span class="search-route-dest">' + esc(r.Departure) + ' - ' + esc(r.Destination) + '</span>' +
        '</div>';
      }
    }
    content.innerHTML = html;
  } catch(e) {
    content.innerHTML = '<div class="empty-state"><p style="color:var(--red)">Error: ' + e.message + '</p></div>';
  }
}

// ===== Route detail =====
window.openRoute = async function(city, name, departure, destination) {
  routeDetailCity = city;
  routeDetailName = name;
  routeDetailDeparture = departure;
  routeDetailDestination = destination;
  routeDetailDir = 0;

  document.getElementById('routeDetailName').textContent = name;
  document.getElementById('routeDetailEndpoints').textContent = departure + ' - ' + destination;
  document.getElementById('routeOverlay').classList.add('show');
  document.getElementById('routeStops').innerHTML = '<div class="search-loading"><span class="spinner">Loading</span></div>';

  try {
    var resp = await apiFetch('/api/bus/stops?city=' + city + '&route=' + encodeURIComponent(name));
    routeDetailStops = resp.stops || resp;
    renderRouteDirTabs();
    renderRouteStops();
  } catch(e) {
    document.getElementById('routeStops').innerHTML = '<div class="empty-state"><p style="color:var(--red)">' + e.message + '</p></div>';
  }
};

window.closeRoute = function() {
  document.getElementById('routeOverlay').classList.remove('show');
};

function renderRouteDirTabs() {
  var tabs = document.getElementById('routeDirTabs');
  if (routeDetailStops.length <= 1) { tabs.innerHTML = ''; return; }

  tabs.innerHTML = routeDetailStops.map(function(d, i) {
    var lastStop = d.Stops.length > 0 ? d.Stops[d.Stops.length - 1].StopName : '';
    var label = '\u5f80' + lastStop;
    var cls = i === routeDetailDir ? 'active' : '';
    return '<div class="route-dir-tab ' + cls + '" onclick="switchRouteDir(' + i + ')">' + label + '</div>';
  }).join('');
}

window.switchRouteDir = function(idx) {
  routeDetailDir = idx;
  renderRouteDirTabs();
  renderRouteStops();
};

function renderRouteStops() {
  var container = document.getElementById('routeStops');
  var dirData = routeDetailStops[routeDetailDir];
  if (!dirData || dirData.Stops.length === 0) {
    container.innerHTML = '<div class="empty-state"><p>No stops</p></div>';
    return;
  }

  var group = getActiveGroup();
  var html = dirData.Stops.map(function(s) {
    var already = group && group.stops.some(function(gs) {
      return gs.stopUID === s.StopUID && gs.routeName === routeDetailName && gs.city === routeDetailCity && gs.direction === dirData.Direction;
    });
    var btn = already
      ? '<span class="added-mark">&#10003;</span>'
      : '<button class="add-btn" onclick="addStopToGroup(\'' + esc(s.StopUID) + '\',\'' + esc(s.StopName) + '\',' + dirData.Direction + ')">+</button>';
    return '<div class="route-stop-row"><span class="stop-name">' + esc(s.StopName) + '</span>' + btn + '</div>';
  }).join('');
  container.innerHTML = html;
}

window.addStopToGroup = function(stopUID, stopName, direction) {
  var dirData = routeDetailStops[routeDetailDir];
  var lastStop = dirData && dirData.Stops.length > 0 ? dirData.Stops[dirData.Stops.length - 1].StopName : '';
  var dirLabel = '\u5f80' + lastStop;

  var stopObj = {
    city: routeDetailCity,
    routeName: routeDetailName,
    direction: direction,
    stopUID: stopUID,
    stopName: stopName,
    dirLabel: dirLabel,
  };

  if (appData.groups.length > 1) {
    pendingStop = stopObj;
    showGroupPicker();
  } else {
    var group = getActiveGroup();
    if (!group.stops.some(function(s) { return s.stopUID === stopUID && s.routeName === routeDetailName && s.direction === direction; })) {
      group.stops.push(stopObj);
      saveData();
    }
    renderRouteStops();
  }
};

// ===== Group picker =====
function showGroupPicker() {
  var list = document.getElementById('groupPickerList');
  list.innerHTML = appData.groups.map(function(g) {
    return '<div class="popup-item" onclick="pickGroup(\'' + g.id + '\')">' + esc(g.name) + '</div>';
  }).join('');
  document.getElementById('groupPickerBg').classList.add('show');
}

window.closeGroupPicker = function() {
  document.getElementById('groupPickerBg').classList.remove('show');
  pendingStop = null;
};

window.pickGroup = function(gid) {
  if (!pendingStop) { window.closeGroupPicker(); return; }
  var group = appData.groups.find(function(g) { return g.id === gid; });
  if (group) {
    var ps = pendingStop;
    if (!group.stops.some(function(s) { return s.stopUID === ps.stopUID && s.routeName === ps.routeName && s.direction === ps.direction; })) {
      group.stops.push(ps);
      saveData();
    }
  }
  window.closeGroupPicker();
  renderRouteStops();
};

// ===== Settings =====
window.openSettings = function() {
  document.getElementById('refreshSelect').value = String(appData.refreshInterval || 15000);
  renderSettingsGroups();
  document.getElementById('settingsBg').classList.add('show');
};

window.closeSettings = function() {
  document.getElementById('settingsBg').classList.remove('show');
  renderGroupBar();
  renderDashboard();
  restartRefresh();
};

window.saveRefreshSetting = function() {
  appData.refreshInterval = parseInt(document.getElementById('refreshSelect').value) || 0;
  saveData();
};

function renderSettingsGroups() {
  var container = document.getElementById('settingsGroupList');
  container.innerHTML = appData.groups.map(function(g, i) {
    var delBtn = appData.groups.length > 1
      ? '<button onclick="deleteGroup(' + i + ')">&#10005;</button>'
      : '';
    return '<div class="group-manage-item">' +
      '<input value="' + esc(g.name) + '" onchange="renameGroup(' + i + ', this.value)">' +
      delBtn +
    '</div>';
  }).join('');
}

window.renameGroup = function(idx, name) {
  if (!name.trim()) return;
  appData.groups[idx].name = name.trim();
  saveData();
};

window.deleteGroup = function(idx) {
  if (appData.groups.length <= 1) return;
  var removed = appData.groups.splice(idx, 1)[0];
  if (appData.activeGroupId === removed.id) {
    appData.activeGroupId = appData.groups[0].id;
  }
  saveData();
  renderSettingsGroups();
};

window.addGroupFromSettings = function() {
  var name = prompt('Group name:');
  if (!name || !name.trim()) return;
  var id = 'g' + Date.now();
  appData.groups.push({ id: id, name: name.trim(), stops: [] });
  saveData();
  renderSettingsGroups();
};

// ===== Helpers =====
function esc(s) {
  if (!s) return '';
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

})();
