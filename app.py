#!/usr/bin/env python3
"""
台鐵時刻表 — Flask Web API
Reuses TDX auth/filter logic; serves JSON to the HTML frontend.
"""

import collections
import json
import logging
import bisect
import math
import os
import re
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, date, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, make_response, redirect, render_template, request, url_for

log = logging.getLogger(__name__)

load_dotenv(Path(__file__).parent / ".env")

# ─── TDX API Reference — all available TRA endpoints ─────────────────────
# Base: https://tdx.transportdata.tw/api/basic
#
# ── v3 Static / Reference ─────────────────────────────────────────────────
# GET /v3/Rail/TRA/Station                               取得車站基本資料
# GET /v3/Rail/TRA/StationExit                           取得車站出入口基本資料
# GET /v3/Rail/TRA/StationFacility                       取得車站設施資料
# GET /v3/Rail/TRA/Line                                  取得路線基本資料
# GET /v3/Rail/TRA/StationOfLine                         取得路線車站基本資料
# GET /v3/Rail/TRA/TrainType                             取得所有列車車種資料
# GET /v3/Rail/TRA/Shape                                 取得線型基本資料
# GET /v3/Rail/TRA/Operator                              取得台鐵營運業者基本資料
# GET /v3/Rail/TRA/LineNetwork                           取得路線網路拓撲基本資料
# GET /v3/Rail/TRA/LineTransfer                          取得內部路線轉乘資料
# GET /v3/Rail/TRA/StationTransfer                       取得車站跨運具轉乘資訊
#
# ── v3 Fare ───────────────────────────────────────────────────────────────
# GET /v3/Rail/TRA/ODFare                                取得票價資料(檔案)
# GET /v3/Rail/TRA/ODFare/{OriginID}/to/{DestID}         取得指定起迄站間票價資料
#
# ── v3 Timetable ──────────────────────────────────────────────────────────
# GET /v3/Rail/TRA/GeneralTrainTimetable                 取得所有車次的定期時刻表資料   ← USED
# GET /v3/Rail/TRA/GeneralTrainTimetable/TrainNo/{No}    取得指定[車次]的定期時刻表資料
# GET /v3/Rail/TRA/GeneralStationTimetable               取得各站的定期站別時刻表資料
# GET /v3/Rail/TRA/GeneralStationTimetable/Station/{ID}  取得指定[車站]的定期站別時刻表資料
# GET /v3/Rail/TRA/SpecificTrainTimetable                取得所有特殊車次時刻表資料
# GET /v3/Rail/TRA/SpecificTrainTimetable/TrainNo/{No}   取得指定[車次]的特殊車次時刻表資料
# GET /v3/Rail/TRA/DailyTrainTimetable/Today             取得當天車次時刻表資料
# GET /v3/Rail/TRA/DailyTrainTimetable/Today/TrainNo/{No} 取得當天指定[車次]的時刻表資料
# GET /v3/Rail/TRA/DailyTrainTimetable/TrainDates        取得臺鐵每日時刻表所有供應的日期資料
# GET /v3/Rail/TRA/DailyTrainTimetable/TrainDate/{Date}  取得指定[日期]所有車次的時刻表資料  ← USED
# GET /v3/Rail/TRA/DailyTrainTimetable/OD/{Orig}/to/{Dest}/{Date}          取得指定[日期],[起迄站間]之站間時刻表資料(僅列出查詢的停靠站)
# GET /v3/Rail/TRA/DailyTrainTimetable/OD/Inclusive/{Orig}/to/{Dest}/{Date} 取得指定[日期],[起迄站間]之站間時刻表資料
# GET /v3/Rail/TRA/DailyStationTimetable/Today           取得當天各站站別時刻表資料
# GET /v3/Rail/TRA/DailyStationTimetable/Today/Station/{ID} 取得當天指定[車站]的時刻表資料
# GET /v3/Rail/TRA/DailyStationTimetable/TrainDate/{Date} 取得各站每日站別時刻表資料
#
# ── v3 Live ───────────────────────────────────────────────────────────────
# GET /v3/Rail/TRA/StationLiveBoard                      取得列車即時到離站資料
# GET /v3/Rail/TRA/StationLiveBoard/Station/{ID}         取得指定[車站]的列車即時到離站資料
# GET /v3/Rail/TRA/TrainLiveBoard                        取得列車即時位置置動態資料
# GET /v3/Rail/TRA/TrainLiveBoard/TrainNo/{No}           取得指定[車次]的列車即時位置置動態資料  ← USED (trainlive)
#
# ── v3 Alerts / News ──────────────────────────────────────────────────────
# GET /v3/Rail/TRA/Alert                                 取得營運通阻資料  ← USED
# GET /v3/Rail/TRA/News                                  取得最新消息  ← USED
#
# ── v2 ────────────────────────────────────────────────────────────────────
# GET /v2/Rail/TRA/Network                               取得臺鐵路網資料
# GET /v2/Rail/TRA/Station                               取得車站基本資料
# GET /v2/Rail/TRA/Line                                  取得路線基本資料
# GET /v2/Rail/TRA/StationOfLine                         取得路線車站基本資料
# GET /v2/Rail/TRA/TrainType                             取得所有列車車種資料
# GET /v2/Rail/TRA/Shape                                 取得軌道路網實體路線圖資料
# GET /v2/Rail/TRA/ODFare                                取得票價資料
# GET /v2/Rail/TRA/ODFare/{OriginID}/to/{DestID}         取得指定[起迄站間]之票價資料
# GET /v2/Rail/TRA/GeneralTrainInfo                      取得所有車次的定期車次資料
# GET /v2/Rail/TRA/GeneralTrainInfo/TrainNo/{No}         取得指定[車次]的定期車次資料
# GET /v2/Rail/TRA/GeneralTimetable                      取得所有車次的定期時刻表資料
# GET /v2/Rail/TRA/GeneralTimetable/TrainNo/{No}         取得指定[車次]的定期時刻表資料
# GET /v2/Rail/TRA/DailyTrainInfo/Today                  取得當天所有車次的車次資料
# GET /v2/Rail/TRA/DailyTrainInfo/Today/TrainNo/{No}     取得當天指定[車次]的車次資料
# GET /v2/Rail/TRA/DailyTrainInfo/TrainDate/{Date}       取得指定[日期]所有車次的車次資料
# GET /v2/Rail/TRA/DailyTrainInfo/TrainNo/{No}/TrainDate/{Date} 取得指定[日期]與[車次]的車次資料
# GET /v2/Rail/TRA/DailyTimetable/Today                  取得當天所有車次的時刻表資料
# GET /v2/Rail/TRA/DailyTimetable/Today/TrainNo/{No}     取得當天指定[車次]的時刻表資料
# GET /v2/Rail/TRA/DailyTimetable/TrainDates             取得台鐵每日時刻表所有供應的日期資料
# GET /v2/Rail/TRA/DailyTimetable/TrainDate/{Date}       取得指定[日期]所有車次的時刻表資料
# GET /v2/Rail/TRA/DailyTimetable/TrainNo/{No}/TrainDate/{Date} 取得指定[日期],[車次]的時刻表資料
# GET /v2/Rail/TRA/DailyTimetable/Station/{ID}/{Date}    取得指定[日期],[車站]的站別時刻表資料
# GET /v2/Rail/TRA/DailyTimetable/OD/{Orig}/to/{Dest}/{Date}  取得指定[日期],[起迄站間]之站間時刻表資料
# GET /v2/Rail/TRA/LiveBoard                             取得車站別列車即時到離站電子看板(動態前後30分鐘的車次)
# GET /v2/Rail/TRA/LiveBoard/Station/{ID}                取得指定[車站]列車即時到離站電子看板  ← USED (liveboard)
# GET /v2/Rail/TRA/LiveTrainDelay                        取得列車即時準點/延誤時間資料
# ─────────────────────────────────────────────────────────────────────────

# ─── Static asset cache buster ─────────────────────────────────────────────
# Bump this string whenever JS/CSS changes to force browsers to refetch.
_CACHE_VER = "20260924a"

# ─── TDX 認證 — Key pool ──────────────────────────────────────────────────
# Supports multiple key pairs. Set in env:
#   TDX_CLIENT_ID / TDX_CLIENT_SECRET        ← key #0 (required, existing)
#   TDX_CLIENT_ID_1 / TDX_CLIENT_SECRET_1   ← key #1 (optional)
#   TDX_CLIENT_ID_2 / TDX_CLIENT_SECRET_2   ← key #2 (optional)
#   … and so on
TOKEN_URL = (
    "https://tdx.transportdata.tw/auth/realms/TDXConnect"
    "/protocol/openid-connect/token"
)


class _KeyRateLimiter:
    """Sliding-window rate limiter: max 5 requests per 60 seconds per API key.

    Thread-safe. `try_acquire()` atomically claims a slot or immediately
    reports that this key is rate-limited.
    """
    WINDOW  = 60.0   # seconds
    MAX_REQ = 5      # TDX free-plan limit per key

    def __init__(self) -> None:
        self._ts: collections.deque = collections.deque()   # epoch timestamps of issued requests
        self._lock = threading.Lock()

    def _prune(self, now: float) -> None:
        """Remove timestamps that have fallen outside the current window."""
        cutoff = now - self.WINDOW
        while self._ts and self._ts[0] <= cutoff:
            self._ts.popleft()

    def next_available_at(self) -> float:
        """Return epoch-seconds when the next slot opens (≤ now means ready)."""
        with self._lock:
            now = time.time()
            self._prune(now)
            return now if len(self._ts) < self.MAX_REQ else self._ts[0] + self.WINDOW

    def try_acquire(self) -> bool:
        """Claim a slot immediately, returning False when the key is limited."""
        with self._lock:
            now = time.time()
            self._prune(now)
            if len(self._ts) >= self.MAX_REQ:
                return False
            self._ts.append(now)
            return True

    def mark_exhausted(self) -> None:
        """Force-fill the window so this key is unavailable for ~60 s.
        Called as a safety net after an unexpected 429 response."""
        with self._lock:
            now = time.time()
            self._ts.clear()
            for _ in range(self.MAX_REQ):
                self._ts.append(now)


def _load_key_pool() -> list[dict]:
    """Build a list of {id, secret, token_cache} from environment variables."""
    pool = []
    # Key #0 — original bare names (required)
    _id0 = os.environ.get("TDX_CLIENT_ID", "")
    _sc0 = os.environ.get("TDX_CLIENT_SECRET", "")
    if _id0 and _sc0:
        pool.append({"id": _id0, "secret": _sc0, "token_cache": {}, "limiter": _KeyRateLimiter()})
    # Keys #1, #2, ... — numbered suffixes
    i = 1
    while True:
        _id = os.environ.get(f"TDX_CLIENT_ID_{i}", "")
        _sc = os.environ.get(f"TDX_CLIENT_SECRET_{i}", "")
        if not _id or not _sc:
            break
        pool.append({"id": _id, "secret": _sc, "token_cache": {}, "limiter": _KeyRateLimiter()})
        i += 1
    if not pool:
        raise RuntimeError("No TDX API credentials found in environment")
    return pool

_key_pool   = _load_key_pool()
_pool_index = 0          # next key to use (round-robin)
_pool_lock  = threading.Lock()


def _get_token_for(key: dict) -> str:
    """Return a valid access token for one key entry, refreshing if needed."""
    cache = key["token_cache"]
    with _cache_lock:
        if time.time() < cache.get("expires_at", 0) - 30:
            return cache["access_token"]
    resp = requests.post(
        TOKEN_URL,
        headers={"content-type": "application/x-www-form-urlencoded"},
        data={
            "grant_type":    "client_credentials",
            "client_id":     key["id"],
            "client_secret": key["secret"],
        },
        timeout=15,
    )
    resp.raise_for_status()
    j = resp.json()
    with _cache_lock:
        cache["access_token"] = j["access_token"]
        cache["expires_at"]   = time.time() + int(j.get("expires_in", 1800))
    return cache["access_token"]


# ─── In-memory caches (no filesystem dependency) ───────────────────────────
_timetable_cache: dict = {}      # {"trains": [...], "fetched_at": float, "expire_iso": str}
_daily_cache: dict = {}          # {date_str: {"trains": [...], "fetched_at": float}}
_od_cache: dict = {}             # {"{fc}_{tc}": {"ab": [...], "ba": [...], "fetched_at": float}}
_liveboard_cache: dict = {}      # {station_id: {"delays": {train_no: delay_min}, "boards": [...], "fetched_at": float}}
_alert_cache:     dict = {}      # {"items": [...], "fetched_at": float}
_news_cache:      dict = {}      # {"items": [...], "fetched_at": float}
_trainlive_cache:   dict = {}      # {train_no: {"live": {...}, "fetched_at": float}}
_fare_cache:        dict = {}      # {"{fc}_{tc}": {"fares": {...}, "fetched_at": float}}
_rain_cache:        dict = {}      # {"stations": [...], "fetched_at": float}
_mrt_live_cache:    dict = {}      # {operator: {"boards": [...], "fetched_at": float}}
_mrt_flt_cache:     dict = {}      # {operator: {"data": [...], "fetched_at": float}} — FirstLastTimetable
_bus_routes_cache:  dict = {}      # {city: {"routes": [...], "fetched_at": float}}
_bus_stops_cache:   dict = {}      # {"{city}_{route}": {"stops": [...], "fetched_at": float}}
_bus_eta_cache:     dict = {}      # {"{city}_{route}": {"etas": [...], "fetched_at": float}}
_cache_lock = threading.Lock()
_RAIN_TIMEZONE = ZoneInfo("Asia/Taipei")
_rain_api_call_date = ""
_rain_api_call_count = 0


def _rain_api_calls_today(increment: bool = False) -> int:
    global _rain_api_call_date, _rain_api_call_count
    today = datetime.now(_RAIN_TIMEZONE).date().isoformat()
    with _cache_lock:
        if _rain_api_call_date != today:
            _rain_api_call_date = today
            _rain_api_call_count = 0
        if increment:
            _rain_api_call_count += 1
        return _rain_api_call_count

OD_CACHE_TTL          = 30 * 60          # 30 minutes  (matches client TTL)
GENERAL_CACHE_TTL     = 12 * 3600        # 12 hours    (general timetable rarely changes)
DAILY_CACHE_TTL       = 7 * 24 * 3600   # 7 days      (matches client TTL)
LIVE_CACHE_TTL        = 120              # 120 seconds — shared by liveboard & trainlive
LIVEBOARD_CACHE_TTL   = LIVE_CACHE_TTL
TRAINLIVE_CACHE_TTL   = LIVE_CACHE_TTL
ALERT_CACHE_TTL       = 15 * 60         # 15 minutes
NEWS_CACHE_TTL        = 60 * 60         # 1 hour
FARE_CACHE_TTL        = 24 * 3600       # 24 hours  (fares rarely change)
RAIN_CACHE_TTL        = 10 * 60         # 10 minutes (CWA rainfall refresh interval)
MRT_LIVE_CACHE_TTL    = 60              # 60 seconds (MRT LiveBoard refresh)
MRT_FLT_CACHE_TTL     = 24 * 3600       # 24 hours (FirstLastTimetable rarely changes)
BUS_ROUTES_CACHE_TTL  = 24 * 3600       # 24 hours (route list rarely changes)
BUS_STOPS_CACHE_TTL   = 24 * 3600       # 24 hours (stop sequence rarely changes)
BUS_ETA_CACHE_TTL     = 60              # 60 seconds (ETA data refreshes frequently)

# ─── Bus city codes ────────────────────────────────────────────────────────
_BUS_CITIES = {
    "Taipei": "\u81fa\u5317\u5e02",
    "NewTaipei": "\u65b0\u5317\u5e02",
    "Taoyuan": "\u6843\u5712\u5e02",
    "Taichung": "\u81fa\u4e2d\u5e02",
    "Tainan": "\u81fa\u5357\u5e02",
    "Kaohsiung": "\u9ad8\u96c4\u5e02",
    "Keelung": "\u57fa\u9686\u5e02",
    "Hsinchu": "\u65b0\u7af9\u5e02",
    "HsinchuCounty": "\u65b0\u7af9\u7e23",
    "MiaoliCounty": "\u82d7\u6817\u7e23",
    "ChanghuaCounty": "\u5f70\u5316\u7e23",
    "NantouCounty": "\u5357\u6295\u7e23",
    "YunlinCounty": "\u96f2\u6797\u7e23",
    "ChiayiCounty": "\u5609\u7fa9\u7e23",
    "Chiayi": "\u5609\u7fa9\u5e02",
    "PingtungCounty": "\u5c4f\u6771\u7e23",
    "YilanCounty": "\u5b9c\u862d\u7e23",
    "HualienCounty": "\u82b1\u84ee\u7e23",
    "TaitungCounty": "\u81fa\u6771\u7e23",
    "KinmenCounty": "\u91d1\u9580\u7e23",
    "PenghuCounty": "\u6f8e\u6e56\u7e23",
    "LienchiangCounty": "\u9023\u6c5f\u7e23",
}
_BUS_DEFAULT_CITIES = ["Taipei", "NewTaipei"]

# ─── 車站代碼表 ────────────────────────────────────────────────────────────
# Populated at startup by _load_seed_data() from seed_data.json,
# then overwritten by _load_stations_from_api() on each successful TDX fetch.
STATIONS: dict[str, str] = {}

_TRIP_LINE_MAP = {1: "山線", 2: "海線", 3: "成追線"}

# Branch lines cannot be derived from an address, so they are kept here.
_BRANCH_LINE_GROUPS: list[dict] = [
    {"city": "內灣線", "codes": ["1210","1190","1191","1192","1193","1201","1202","1203","1204","1205","1206","1207","1208"]},
    {"city": "六家線", "codes": ["1193","1194"]},
    {"city": "集集線", "codes": ["3430","3431","3432","3433","3434","3435","3436"]},
    {"city": "成追線", "codes": ["3350","2260"]},
    {"city": "沙崙線", "codes": ["4270","4271","4272"]},
    {"city": "平溪線", "codes": ["7330","7331","7332","7333","7334","7335","7336"]},
    {"city": "深澳線", "codes": ["7360","7361","7362"]},
]

# Populated by _load_stations_from_api(); city groups in geographic order.
_STATION_GROUPS: list[dict] = []

# code → StationClass int. 0=特等, 1=一等, 2=二等, 3=三等, 4=簡易
# Populated at startup by _load_seed_data(), overwritten by API loader.
_STATION_CLASSES: dict[str, int] = {}

# code → phone / address. Populated by _load_stations_from_api(), loaded from seed.
_STATION_PHONES:    dict[str, str] = {}
_STATION_ADDRESSES: dict[str, str] = {}

# Preferred north-to-south display order for cities.
_CITY_ORDER = [
    "基隆市","新北市","台北市","桃園市","新竹市","新竹縣",
    "苗栗縣","台中市","彰化縣","南投縣","雲林縣","嘉義縣","嘉義市",
    "台南市","高雄市","屏東縣","台東縣","花蓮縣","宜蘭縣",
]

# Virtual/special stations to hide from the UI picker.
_HIDDEN_STATION_IDS: set[str] = {
    "1001",   # 臺北-環島 — virtual station for round-island trains only
    "5170",   # 枋野      — signal station (號誌站), not open to passengers
    "5998",   # 南方小站  — maintenance/facility stop, not a regular passenger station
    "5999",   # 潮州基地  — rolling-stock depot stop, not a regular passenger station
}

# ─── TRA↔MRT 轉乘站對照（台北捷運 TRTC / 高雄捷運 KRTC）────────────────────
# TRA station code → list of connected MRT lines + station info
_TRA_MRT_TRANSFER: dict[str, list[dict]] = {
    "0980": [{"line": "BL", "line_name": "板南線", "station": "BL22", "mrt_name": "南港"}],
    "0990": [{"line": "G",  "line_name": "松山新店線", "station": "G19", "mrt_name": "松山"}],
    "1000": [
        {"line": "BL", "line_name": "板南線",     "station": "BL12", "mrt_name": "台北車站"},
        {"line": "R",  "line_name": "淡水信義線", "station": "R10",  "mrt_name": "台北車站"},
    ],
    "1010": [{"line": "BL", "line_name": "板南線", "station": "BL10", "mrt_name": "龍山寺"}],
    "1020": [{"line": "BL", "line_name": "板南線", "station": "BL07", "mrt_name": "板橋"}],
    "4340": [{"operator": "KRTC", "line": "R", "line_name": "紅線", "station": "R16", "mrt_name": "左營/高鐵"}],
}

# ─── CWA 降雨 API ─────────────────────────────────────────────────────────
_CWA_API_KEY = os.environ.get("CWA_API_KEY", "").strip()
_CWA_RAIN_URL = "https://opendata.cwa.gov.tw/api/v1/rest/datastore/O-A0002-001"

# 台鐵站名 → CWA 氣象站搜尋關鍵字（僅需填寫無法直接對應的站）
_TRAIN_TO_AREA: dict[str, str] = {
    "汐科":  "汐止",
    "新左營": "左營",
    "蘇澳新": "蘇澳",
    "三坑":  "基隆",
    "百福":  "汐止",
    "五堵":  "汐止",
    "浮洲":  "板橋",
    "山佳":  "樹林",
    "富岡":  "楊梅",
    "新城":  "新城",
}


def _cwa_fetch_rain_stations(force: bool = False) -> list:
    """Fetch all CWA automated rain station data, cached for RAIN_CACHE_TTL."""
    now = time.time()
    with _cache_lock:
        if (not force and _rain_cache.get("stations")
                and now - _rain_cache.get("fetched_at", 0) < RAIN_CACHE_TTL):
            return _rain_cache["stations"]
    if not _CWA_API_KEY:
        with _cache_lock:
            _rain_cache["last_error"] = "missing_key"
        return []
    try:
        _rain_api_calls_today(increment=True)
        r = requests.get(
            _CWA_RAIN_URL,
            params={"Authorization": _CWA_API_KEY, "format": "JSON"},
            timeout=20,
            verify=False,
        )
        r.raise_for_status()
        payload = r.json()
        stations = payload.get("records", {}).get("Station", [])
        if not isinstance(stations, list) or not stations:
            with _cache_lock:
                _rain_cache["last_error"] = "empty_response"
                return _rain_cache.get("stations", [])
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else 0
        error_code = f"http_{status}"
        log.warning("CWA rain API rejected request (HTTP %s)", status)
        with _cache_lock:
            _rain_cache["last_error"] = error_code
            return _rain_cache.get("stations", [])
    except requests.Timeout:
        log.warning("CWA rain API request timed out")
        with _cache_lock:
            _rain_cache["last_error"] = "timeout"
            return _rain_cache.get("stations", [])
    except requests.exceptions.SSLError as exc:
        details = " ".join(str(arg) for arg in exc.args).lower()
        verify_phrases = (
            "certificate verify failed",
            "cert_verify_failed",
            "unable to get local issuer certificate",
            "self-signed certificate",
            "hostname mismatch",
            "certificate has expired",
        )
        error_code = (
            "tls_certificate_verification"
            if any(phrase in details for phrase in verify_phrases)
            else "tls_handshake"
        )
        log.warning("CWA TLS request failed (%s)", error_code)
        with _cache_lock:
            _rain_cache["last_error"] = error_code
            return _rain_cache.get("stations", [])
    except requests.RequestException as exc:
        error_code = f"request_{type(exc).__name__}"
        log.warning("CWA rain API request failed (%s)", type(exc).__name__)
        with _cache_lock:
            _rain_cache["last_error"] = error_code
            return _rain_cache.get("stations", [])
    except (ValueError, AttributeError, TypeError) as exc:
        error_code = f"invalid_response_{type(exc).__name__}"
        log.warning("CWA rain API returned invalid data (%s)", type(exc).__name__)
        with _cache_lock:
            _rain_cache["last_error"] = error_code
            return _rain_cache.get("stations", [])
    with _cache_lock:
        _rain_cache["stations"] = stations
        _rain_cache["fetched_at"] = time.time()
        _rain_cache["last_error"] = ""
    return stations


def _cwa_rain_error_message(error_code: str) -> str:
    if error_code == "http_401":
        return "CWA rejected the API key (HTTP 401). Verify CWA_API_KEY in Render."
    if error_code == "http_403":
        return "CWA denied the request (HTTP 403). Check the API key permissions."
    if error_code.startswith("http_"):
        return f"CWA returned {error_code[5:]}. Check the key and CWA service status."
    if error_code == "timeout":
        return "The request to CWA timed out from the hosting service."
    if error_code == "tls_certificate_verification":
        return "Render cannot verify CWA's TLS certificate chain. Check the CA trust chain in the hosting image."
    if error_code == "tls_handshake":
        return "The TLS handshake with CWA failed from the hosting service. Check the CWA endpoint and outbound TLS support."
    if error_code.startswith("request_"):
        return f"The hosting service could not reach CWA ({error_code[8:]})."
    if error_code.startswith("invalid_response_"):
        return "CWA returned data in an unexpected format."
    if error_code == "empty_response":
        return "CWA returned no rainfall station records."
    return "Check the hosting service logs for the CWA request failure."


def _find_rain_for(name: str, stations: list) -> dict:
    """Find rainfall data for a station name from CWA station list."""
    kw = _TRAIN_TO_AREA.get(name, name)
    for s in stations:
        sname = s.get("StationName", "")
        stown = s.get("GeoInfo", {}).get("TownName", "")
        if kw == sname or kw in sname or kw in stown:
            rf = s.get("RainfallElement", {})
            r10 = rf.get("Past10Min", {}).get("Precipitation")
            r1h = rf.get("Past1hr", {}).get("Precipitation")
            obs = s.get("ObsTime", {}).get("DateTime", "")
            def _fmt(v):
                if v is None or (isinstance(v, (int, float)) and float(v) < -9990):
                    return "—"
                try:
                    return f"{float(v):.1f}"
                except (ValueError, TypeError):
                    return "—"
            return {"rain_10min": _fmt(r10), "rain_1hr": _fmt(r1h), "obs_time": obs}
    return {"rain_10min": "—", "rain_1hr": "—", "obs_time": ""}

# ─── Seed data loader ───────────────────────────────────────────────────────
_SEED_FILE            = Path(__file__).parent / "seed_data.json"
_TIMETABLE_CACHE_FILE = Path(__file__).parent / "timetable_cache.json"
_seed_generated_at: str = ""   # set by _load_seed_data(); used by startup thread

def _load_seed_data() -> None:
    """Load bootstrap station data from seed_data.json into module globals.
    Called once at startup; on miss the globals stay empty until API loads."""
    global _seed_generated_at
    try:
        seed = json.loads(_SEED_FILE.read_text(encoding="utf-8"))
        _seed_generated_at = seed.get("generated_at", "")
        if seed.get("stations"):
            STATIONS.clear()
            STATIONS.update(
                {n: c for n, c in seed["stations"].items()
                 if c not in _HIDDEN_STATION_IDS}
            )
        if seed.get("station_classes"):
            _STATION_CLASSES.clear()
            _STATION_CLASSES.update(
                {k: int(v) for k, v in seed["station_classes"].items()
                 if k not in _HIDDEN_STATION_IDS}
            )
        if seed.get("station_groups"):
            _STATION_GROUPS.clear()
            for g in seed["station_groups"]:
                codes = [c for c in g.get("codes", []) if c not in _HIDDEN_STATION_IDS]
                if codes:
                    _STATION_GROUPS.append({"city": g["city"], "codes": codes})
        if seed.get("station_phones"):
            _STATION_PHONES.clear()
            _STATION_PHONES.update(seed["station_phones"])
        if seed.get("station_addresses"):
            _STATION_ADDRESSES.clear()
            _STATION_ADDRESSES.update(seed["station_addresses"])
        print(
            f"[seed] Loaded {len(STATIONS)} stations, "
            f"{len(_STATION_GROUPS)} groups from {_SEED_FILE.name}"
        )
    except FileNotFoundError:
        print(f"[seed] {_SEED_FILE.name} not found — waiting for API load")
    except Exception as exc:
        print(f"[seed] Load failed ({exc}) — waiting for API load")


# ─── Timetable disk cache ──────────────────────────────────────────────────
_save_lock = threading.Lock()   # ensures only one disk-write runs at a time

def _save_timetable_disk_cache() -> None:
    """Serialize general + daily timetable caches to disk.
    Called in a background thread after every successful TDX fetch so the
    next server restart can skip those TDX calls entirely.
    Uses a lock so concurrent calls coalesce (last write wins, no races).
    Writes to a temp file first then renames for atomicity."""
    if not _save_lock.acquire(blocking=False):
        return   # another save is already in progress — skip this one
    try:
        with _cache_lock:
            general = {
                "trains":     _timetable_cache.get("trains", []),
                "fetched_at": _timetable_cache.get("fetched_at", 0),
                "expire_iso": _timetable_cache.get("expire_iso", ""),
            }
            # Keep only recent/future dates to avoid unbounded growth.
            cutoff = (date.today() - timedelta(days=1)).isoformat()
            daily = {
                k: {"trains": v["trains"], "fetched_at": v["fetched_at"]}
                for k, v in _daily_cache.items()
                if k >= cutoff
            }
        # Snapshot station classes outside the lock (dict is small, thread-safe read)
        classes_snapshot = dict(_STATION_CLASSES)
        payload = json.dumps(
            {"general": general, "daily": daily, "station_classes": classes_snapshot},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        # Atomic write: write to .tmp then rename so a mid-write restart
        # never leaves a truncated/corrupt cache file.
        tmp = _TIMETABLE_CACHE_FILE.with_suffix(".tmp")
        tmp.write_text(payload, encoding="utf-8")
        tmp.replace(_TIMETABLE_CACHE_FILE)
        size_kb = len(payload.encode()) // 1024
        print(
            f"[cache] Wrote {_TIMETABLE_CACHE_FILE.name} "
            f"({size_kb} KB, {len(general['trains'])} general trains, {len(daily)} dates)"
        )
    except Exception as exc:
        print(f"[cache] Disk write failed ({exc})")
    finally:
        _save_lock.release()


def _load_disk_caches() -> None:
    """Restore general + daily timetable caches from disk at startup.
    With both seed_data.json and timetable_cache.json present, the server
    can serve the first user request with zero TDX API calls."""
    try:
        data = json.loads(_TIMETABLE_CACHE_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"[cache] {_TIMETABLE_CACHE_FILE.name} not found — will build on first query")
        return
    except Exception as exc:
        print(f"[cache] Disk load failed ({exc})")
        return

    general = data.get("general", {})
    if general.get("trains"):
        with _cache_lock:
            _timetable_cache["trains"]     = general["trains"]
            _timetable_cache["fetched_at"] = float(general.get("fetched_at", 0))
            _timetable_cache["expire_iso"] = general.get("expire_iso", "")
        print(f"[cache] Restored {len(general['trains'])} general trains from disk")

    cutoff = (date.today() - timedelta(days=1)).isoformat()
    loaded = 0
    for date_str, entry in data.get("daily", {}).items():
        if date_str < cutoff or not entry.get("trains"):
            continue
        with _cache_lock:
            _daily_cache[date_str] = {
                "trains":     entry["trains"],
                "fetched_at": float(entry.get("fetched_at", 0)),
            }
        loaded += 1
    if loaded:
        print(f"[cache] Restored {loaded} daily timetable(s) from disk")

    # Restore station classes — secondary source after seed_data.json.
    # Only applied if _STATION_CLASSES is still empty (seed didn't have them).
    cached_classes = {
        k: int(v) for k, v in data.get("station_classes", {}).items()
        if isinstance(v, (int, float)) and k not in _HIDDEN_STATION_IDS
    }
    if cached_classes and not _STATION_CLASSES:
        _STATION_CLASSES.update(cached_classes)
        print(f"[cache] Restored {len(cached_classes)} station classes from disk")


# ─── Dynamic station loader ─────────────────────────────────────────────────
# v2 returns StationClass as string "0"–"5" for all stations (v3 only has 0/1).
# v2 also provides LocationCity directly, avoiding address parsing.
_TDX_STATION_URL = (
    "https://tdx.transportdata.tw/api/basic/v2/Rail/TRA/Station?$format=JSON"
)
_TDX_LINE_URL = (
    "https://tdx.transportdata.tw/api/basic/v3/Rail/TRA/StationOfLine?$format=JSON"
)

# Display order for branch-line groups (names only — codes come from TDX API).
_BRANCH_LINE_NAMES: tuple[str, ...] = (
    "內灣線", "六家線", "集集線", "成追線", "沙崙線", "平溪線", "深澳線",
)


def _load_branch_lines() -> list[dict]:
    """Fetch branch-line station groups from TDX StationOfLine API.
    Returns [{city, codes}, ...] on success, or falls back to _BRANCH_LINE_GROUPS."""
    try:
        data = api_get(_TDX_LINE_URL)
        if isinstance(data, dict):
            # v3 may wrap in {"StationOfLines": [...]}
            for key in ("StationOfLines", "TrainStationOfLines"):
                if key in data:
                    data = data[key]
                    break
        if not isinstance(data, list):
            return _BRANCH_LINE_GROUPS
        branch_map: dict[str, list[str]] = {}
        for line in data:
            name = (line.get("LineName") or {}).get("Zh_tw", "")
            if name not in _BRANCH_LINE_NAMES:
                continue
            if line.get("Direction", 0) != 0:   # keep outbound direction only
                continue
            codes = [
                s.get("StationID", "").strip()
                for s in line.get("Stations", [])
                if s.get("StationID", "").strip()
            ]
            if codes:
                branch_map[name] = codes
        result = [{"city": n, "codes": branch_map[n]}
                  for n in _BRANCH_LINE_NAMES if n in branch_map]
        if result:
            print(f"[stations] Loaded {len(result)} branch line groups from TDX API")
            return result
        return _BRANCH_LINE_GROUPS
    except Exception as exc:
        print(f"[stations] StationOfLine API failed ({exc}), using built-in branch groups")
        return _BRANCH_LINE_GROUPS


def _load_stations_from_api() -> bool:
    """Fetch all TRA stations from TDX v2 and update STATIONS, _VALID_CODES,
    _STATION_CLASSES and _STATION_GROUPS in-place.
    v2 returns StationClass as a string for all station tiers (0-5) and
    provides LocationCity directly — no address parsing needed."""
    # Snapshot branch-line groups from seed before any mutation so we can
    # reuse them without firing the StationOfLine API call.
    _branch_names_set = set(_BRANCH_LINE_NAMES)
    seeded_branches = [g for g in _STATION_GROUPS if g.get("city") in _branch_names_set]
    try:
        data = api_get(_TDX_STATION_URL)
        # v2 Station API returns a flat list directly
        if not isinstance(data, list) or len(data) < 50:
            return False

        _TRAD_NORM = {"臺北": "台北", "臺中": "台中", "臺南": "台南", "臺東": "台東"}

        new: dict[str, str] = {}
        new_classes: dict[str, int] = {}
        new_phones: dict[str, str] = {}
        new_addresses: dict[str, str] = {}
        city_map: dict[str, list[str]] = {}
        for item in data:
            code = item.get("StationID", "").strip()
            name = (item.get("StationName") or {}).get("Zh_tw", "").strip()
            if not (code and name):
                continue
            if code in _HIDDEN_STATION_IDS:
                continue
            new[name] = code
            # v2 StationClass is a string "0"–"5"; cast to int.
            cls_raw = item.get("StationClass")
            if cls_raw is not None:
                try:
                    new_classes[code] = int(cls_raw)
                except (ValueError, TypeError):
                    pass
            phone = (item.get("StationPhone") or "").strip()
            if phone:
                new_phones[code] = phone
            addr = (item.get("StationAddress") or "").strip()
            for trad, simp in _TRAD_NORM.items():
                addr = addr.replace(trad, simp)
            if addr:
                new_addresses[code] = addr
            # v2 provides LocationCity directly — no address parsing required.
            city = (item.get("LocationCity") or "").strip()
            for trad, simp in _TRAD_NORM.items():
                city = city.replace(trad, simp)
            if city:
                city_map.setdefault(city, []).append(code)

        if not new:
            return False

        with _cache_lock:
            STATIONS.clear()
            STATIONS.update(new)
            _VALID_CODES.clear()
            _VALID_CODES.update(new.values())
            # Merge new API classes into the existing dict.
            # v2 returns StationClass for all tiers (0-5), so new_classes covers
            # the full station set.  UPDATE (rather than REPLACE) is used so that
            # any code present in seed but absent from the API response is preserved.
            if new_classes:
                _STATION_CLASSES.update(new_classes)
            if new_phones:
                _STATION_PHONES.clear()
                _STATION_PHONES.update(new_phones)
            if new_addresses:
                _STATION_ADDRESSES.clear()
                _STATION_ADDRESSES.update(new_addresses)

        # Build ordered city groups
        groups: list[dict] = []
        seen: set[str] = set()
        for city in _CITY_ORDER:
            if city in city_map:
                groups.append({"city": city, "codes": city_map[city]})
                seen.add(city)
        # Append any unexpected cities not in the order list
        for city, codes in city_map.items():
            if city not in seen:
                groups.append({"city": city, "codes": codes})
        # Reuse branch lines from seed if available — avoids StationOfLine API call
        if seeded_branches:
            groups.extend(seeded_branches)
            print(f"[stations] Reused {len(seeded_branches)} branch line groups from seed")
        else:
            groups.extend(_load_branch_lines())
        with _cache_lock:
            _STATION_GROUPS.clear()
            _STATION_GROUPS.extend(groups)

        print(f"[stations] Loaded {len(new)} stations, {len(groups)} groups from TDX API")

        # Persist updated data to seed file so next startup is always fresh.
        # Always save the full merged _STATION_CLASSES (243 entries) — not just
        # new_classes from the API (~32 entries for cls 0/1 only), which would
        # overwrite the seed and lose 二等~簡易 class data on the next cold start.
        try:
            seed_out = {
                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "stations":          new,
                "station_classes":   dict(_STATION_CLASSES),
                "station_groups":    _STATION_GROUPS,
                "station_phones":    dict(_STATION_PHONES),
                "station_addresses": dict(_STATION_ADDRESSES),
            }
            tmp = _SEED_FILE.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(seed_out, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp.replace(_SEED_FILE)
            print(f"[seed] Wrote {_SEED_FILE.name} ({len(new)} stations, {len(seed_out['station_classes'])} classes)")
        except Exception as write_exc:
            print(f"[seed] Write failed ({write_exc})")

        return True
    except Exception as exc:
        print(f"[stations] API load failed ({exc}), using seed fallback")
        return False


# ─── Token ─────────────────────────────────────────────────────────────────
def api_get(url: str) -> dict:
    """Fetch a TDX API URL with per-key rate limiting (max 5 req / 60 s per key).

    Key selection strategy:
      - Always pick the key whose next available slot opens soonest.
      - Break ties by round-robin (_pool_index) so load spreads evenly.
            - `try_acquire()` atomically claims an available slot without blocking a
                request thread; if all keys are limited, fail fast so the route returns 503.
      - 429 is still handled as a safety net: `mark_exhausted()` forces a ~60 s
        cooldown on the offending key so the next retry uses a different one.
    """
    global _pool_index
    _MAX_RETRIES = len(_key_pool) + 1

    for _ in range(_MAX_RETRIES):
        # Pick the key with the earliest available slot.
        # Tiebreak by distance from _pool_index (round-robin when all keys are free).
        with _pool_lock:
            start = _pool_index % len(_key_pool)
        ordered_indices = sorted(
            range(len(_key_pool)),
            key=lambda i: (
                _key_pool[i]["limiter"].next_available_at(),
                (i - start) % len(_key_pool),   # round-robin tiebreaker
            ),
        )
        best_idx = next(
            (i for i in ordered_indices if _key_pool[i]["limiter"].try_acquire()),
            None,
        )
        if best_idx is None:
            raise RuntimeError("All TDX API keys are currently rate limited")
        key = _key_pool[best_idx]

        try:
            token = _get_token_for(key)
        except requests.HTTPError:
            # Token endpoint failed — skip to next key in round-robin order.
            with _pool_lock:
                _pool_index = (best_idx + 1) % len(_key_pool)
            continue

        headers = {"authorization": f"Bearer {token}", "Accept-Encoding": "gzip"}
        resp = requests.get(url, headers=headers, timeout=60)

        if resp.status_code == 429:
            # Safety net: limiter should prevent this, but if TDX rejects anyway,
            # force a 60 s cooldown so the next retry picks a different key.
            key["limiter"].mark_exhausted()
            continue

        if resp.status_code == 401:
            # Token expired mid-flight — refresh and retry once on the same key.
            with _cache_lock:
                key["token_cache"].clear()
            token = _get_token_for(key)
            headers["authorization"] = f"Bearer {token}"
            resp = requests.get(url, headers=headers, timeout=60)

        resp.raise_for_status()

        # Advance index so the next call starts from the key after this one.
        with _pool_lock:
            _pool_index = (best_idx + 1) % len(_key_pool)
        return resp.json()

    raise RuntimeError(f"All {len(_key_pool)} API key(s) exhausted for URL: {url}")


# ─── Train data ────────────────────────────────────────────────────────────
def get_all_trains() -> list:
    with _cache_lock:
        cached = _timetable_cache.get("trains")
        fetched_at = _timetable_cache.get("fetched_at", 0)
        expire_iso = _timetable_cache.get("expire_iso", "")

    if cached is not None:
        # Check API-provided expiry first
        if expire_iso:
            try:
                if datetime.now().astimezone() < datetime.fromisoformat(expire_iso):
                    return cached
            except ValueError:
                pass
        # Fall back to local TTL
        elif time.time() - fetched_at < GENERAL_CACHE_TTL:
            return cached

    url = (
        "https://tdx.transportdata.tw/api/basic/v3/Rail/TRA"
        "/GeneralTrainTimetable?$format=JSON"
    )
    data = api_get(url)
    trains = data.get("TrainTimetables", [])
    with _cache_lock:
        _timetable_cache["trains"]     = trains
        _timetable_cache["fetched_at"] = time.time()
        _timetable_cache["expire_iso"] = data.get("ExpireDate", "")
    threading.Thread(target=_save_timetable_disk_cache, daemon=True).start()
    return trains


def fetch_daily_trains(date_str: str) -> list:
    with _cache_lock:
        entry = _daily_cache.get(date_str)
    if entry and time.time() - entry["fetched_at"] < DAILY_CACHE_TTL:
        return entry["trains"]

    url = (
        f"https://tdx.transportdata.tw/api/basic/v3/Rail/TRA"
        f"/DailyTrainTimetable/TrainDate/{date_str}?$format=JSON"
    )
    data = api_get(url)
    trains = data.get("TrainTimetables", [])
    # Build BikeFlag lookup outside the lock (read-only on fresh `trains` list)
    daily_bike = {
        t.get("TrainInfo", {}).get("TrainNo"): t.get("TrainInfo", {}).get("BikeFlag")
        for t in trains
        if t.get("TrainInfo", {}).get("TrainNo")
    }
    with _cache_lock:
        _daily_cache[date_str] = {"trains": trains, "fetched_at": time.time()}
        # Prune old dates
        cutoff = (date.today() - timedelta(days=7)).isoformat()
        for k in [k for k in _daily_cache if k < cutoff]:
            del _daily_cache[k]
        # Sync BikeFlag from daily → general timetable cache
        general_trains = _timetable_cache.get("trains")
        if general_trains and daily_bike:
            for t in general_trains:
                info = t.get("TrainInfo", {})
                no = info.get("TrainNo")
                if no in daily_bike and daily_bike[no] != info.get("BikeFlag"):
                    info["BikeFlag"] = daily_bike[no]
            # Invalidate OD cache so updated BikeFlag is reflected
            _od_cache.clear()
    threading.Thread(target=_save_timetable_disk_cache, daemon=True).start()
    return trains


# ─── Formatting helpers ────────────────────────────────────────────────────
def _tdx_str(v) -> str:
    """Extract Zh_tw string from a TDX multilingual object or plain string."""
    return v.get("Zh_tw", "") if isinstance(v, dict) else str(v or "")


def _format_train_type(raw: str) -> str:
    if raw.startswith("自強"):
        lower = raw.lower()
        if "推拉" in raw or "pp" in lower:
            return "自強PP"
        m = re.search(r"(\d{3,4})", raw)
        if m:
            return f"自強{m.group(1)}"
        return "自強"
    return re.sub(r"[(（][^)）]*[)）]", "", raw).strip()


def _parse_note(raw: str) -> str:
    if not raw:
        return ""
    note = raw.strip()
    _DAILY = "每日行駛。"
    if note.startswith(_DAILY):
        note = note[len(_DAILY):].strip()
    return note


# ─── OD filter ────────────────────────────────────────────────────────────
def filter_od(all_trains: list, from_code: str, to_code: str) -> list:
    result = []
    for item in all_trains:
        stops = item.get("StopTimes", [])
        orig_seq = dest_seq = None
        orig_dep = dest_arr = None
        for s in stops:
            sid = s.get("StationID", "")
            if sid == from_code:
                orig_seq = s.get("StopSequence", 0)
                orig_dep = s.get("DepartureTime", "")
            elif sid == to_code:
                dest_seq = s.get("StopSequence", 0)
                dest_arr = s.get("ArrivalTime", s.get("DepartureTime", ""))
        if orig_seq is None or dest_seq is None or orig_seq >= dest_seq:
            continue

        info       = item.get("TrainInfo", {})
        train_type = info.get("TrainTypeName", {}).get("Zh_tw", "")
        train_no   = info.get("TrainNo", "")
        start_st   = info.get("StartingStationName", {}).get("Zh_tw", "")
        end_st     = info.get("EndingStationName",   {}).get("Zh_tw", "")
        
        bike_flag = bool(info.get("BikeFlag", 0))
        trip_line  = info.get("TripLine", 0)
        note       = info.get("Note", "")

        duration = ""
        if orig_dep and dest_arr:
            try:
                dh, dm = map(int, orig_dep.split(":"))
                ah, am = map(int, dest_arr.split(":"))
                mins = (ah * 60 + am) - (dh * 60 + dm)
                if mins < 0:
                    mins += 24 * 60
                duration = f"{mins // 60}:{mins % 60:02d}"
            except ValueError:
                pass

        trip_line_name = _TRIP_LINE_MAP.get(trip_line, "")
        parsed_note    = _parse_note(note)
        remark_parts   = [p for p in [trip_line_name, parsed_note] if p]

        result.append({
            "train_no":   train_no,
            "train_type": _format_train_type(train_type),
            "dep":        orig_dep,
            "arr":        dest_arr,
            "duration":   duration,
            "route":      f"{start_st}→{end_st}",
            "bike":       bike_flag,
            "remark":     "　".join(remark_parts),
        })

    result.sort(key=lambda x: x["dep"])
    return result


# ─── Flask app ─────────────────────────────────────────────────────────────
app = Flask(__name__)


@app.after_request
def set_security_headers(response):
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src https://fonts.gstatic.com; "
        "img-src 'self' https://tdx.transportdata.tw; "
        "connect-src 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "object-src 'none'"
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    return response


@app.route("/")
def index():
    stations_list = [{"name": n, "code": c, "cls": _STATION_CLASSES.get(c, -1),
                      "mrt": _TRA_MRT_TRANSFER.get(c)}
                     for n, c in STATIONS.items()]
    return render_template("index.html", stations=stations_list,
                           station_groups=_STATION_GROUPS,
                           cache_ver=_CACHE_VER)


@app.route("/Rain")
@app.route("/rain")
def rain_page():
    return render_template("rain.html", cache_ver=_CACHE_VER)


@app.route("/Main")
@app.route("/main")
def main_page():
    """Hub page linking every feature; ?train=NNN jumps to the train detail page."""
    train_no = request.args.get("train", "").strip()
    if re.fullmatch(r"\d{1,5}", train_no):
        return redirect(url_for("train_detail", train_no=train_no))
    return render_template("main.html", train_error=bool(train_no))


@app.route("/index2.html")
def index2():
    stations_list = [{"name": n, "code": c, "cls": _STATION_CLASSES.get(c, -1),
                      "mrt": _TRA_MRT_TRANSFER.get(c)}
                     for n, c in STATIONS.items()]
    return render_template("index2.html", stations=stations_list,
                           station_groups=_STATION_GROUPS,
                           cache_ver=_CACHE_VER)


@app.route("/api/station-groups")
def api_station_groups():
    """Return city → station-code groups for the city picker.
    Falls back to an empty list if the dynamic loader hasn't run yet."""
    return jsonify(_STATION_GROUPS)


# Load seed data + timetable disk cache first so every global is populated
# before the first request arrives — zero TDX calls needed on warm restart.
_load_seed_data()
_load_disk_caches()
_VALID_CODES: set[str] = set(STATIONS.values())

# Guard: ensure _load_stations_from_api() runs exactly once, whether triggered
# from the background thread or __main__ (prevents double calls that hit the
# TDX 5-req/min rate limit).
_station_load_done = False
_station_load_lock = threading.Lock()


def _run_station_load_once() -> None:
    """Call _load_stations_from_api() at most once per process lifetime."""
    global _station_load_done
    with _station_load_lock:
        if _station_load_done:
            return
        _station_load_done = True
    with app.app_context():
        _load_stations_from_api()


# Try to load full station list from TDX API at startup.
# Run in a background thread so the first page load (DailyTrainTimetable)
# doesn't race with these 2 calls and blow the 5-req/min limit.
def _startup_station_load():
    time.sleep(5)   # brief pause — lets the server finish binding before calling TDX
    # Skip API refresh if seed_data.json was written in the last 24 hours.
    # Station data (names, codes, classes) changes extremely rarely, so an
    # up-to-date seed avoids unnecessary TDX API calls on every restart.
    if _seed_generated_at:
        try:
            age_s = (datetime.now() - datetime.fromisoformat(_seed_generated_at)).total_seconds()
            if age_s < 86400:   # 24 hours
                print(f"[stations] Seed is {age_s/3600:.1f}h old — skipping API refresh")
                return
        except Exception:
            pass
    _run_station_load_once()


threading.Thread(target=_startup_station_load, daemon=True).start()


@app.route("/api/trains")
def api_trains():
    """General timetable for an OD pair."""
    from_code = request.args.get("from", "").strip()
    to_code   = request.args.get("to",   "").strip()

    if not from_code or not to_code:
        return jsonify({"error": "Missing 'from' or 'to' parameter"}), 400
    if from_code == to_code:
        return jsonify({"error": "Origin and destination must differ"}), 400
    if from_code not in _VALID_CODES or to_code not in _VALID_CODES:
        return jsonify({"error": "Invalid station code"}), 400

    cache_key = f"{from_code}_{to_code}"
    with _cache_lock:
        entry = _od_cache.get(cache_key)
    if entry and time.time() - entry["fetched_at"] < OD_CACHE_TTL:
        return jsonify({"ab": entry["ab"], "ba": entry["ba"], "cached": True})

    try:
        all_trains = get_all_trains()
        ab = filter_od(all_trains, from_code, to_code)
        ba = filter_od(all_trains, to_code, from_code)
        now = time.time()
        reverse_key = f"{to_code}_{from_code}"
        with _cache_lock:
            _od_cache[cache_key]   = {"ab": ab, "ba": ba, "fetched_at": now}
            _od_cache[reverse_key] = {"ab": ba, "ba": ab, "fetched_at": now}
            # Evict expired entries
            expired = [k for k, v in _od_cache.items() if now - v["fetched_at"] > OD_CACHE_TTL]
            for k in expired:
                del _od_cache[k]
        return jsonify({"ab": ab, "ba": ba, "cached": False})
    except requests.HTTPError as e:
        return jsonify({"error": f"TDX API error: {e}"}), 502
    except RuntimeError:
        return jsonify({"error": "API rate limit exceeded, please try again later"}), 503
    except Exception:
        log.exception("Unexpected error in /api/trains")
        return jsonify({"error": "Internal server error"}), 500


@app.route("/api/trains/daily")
def api_trains_daily():
    """Specific date timetable for an OD pair."""
    from_code = request.args.get("from", "").strip()
    to_code   = request.args.get("to",   "").strip()
    date_str  = request.args.get("date", "").strip()

    if not from_code or not to_code or not date_str:
        return jsonify({"error": "Missing 'from', 'to', or 'date' parameter"}), 400
    if from_code == to_code:
        return jsonify({"error": "Origin and destination must differ"}), 400
    if from_code not in _VALID_CODES or to_code not in _VALID_CODES:
        return jsonify({"error": "Invalid station code"}), 400
    try:
        date_val = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return jsonify({"error": "date must be YYYY-MM-DD"}), 400
    today = date.today()
    if date_val < today - timedelta(days=2) or date_val > today + timedelta(days=90):
        return jsonify({"error": "date must be within 2 days ago to 90 days ahead"}), 400

    try:
        raw = fetch_daily_trains(date_str)
        ab  = filter_od(raw, from_code, to_code)
        ba  = filter_od(raw, to_code, from_code)
        return jsonify({"ab": ab, "ba": ba, "date": date_str})
    except requests.HTTPError as e:
        return jsonify({"error": f"TDX API error: {e}"}), 502
    except RuntimeError:
        return jsonify({"error": "API rate limit exceeded, please try again later"}), 503
    except Exception:
        log.exception("Unexpected error in /api/trains/daily")
        return jsonify({"error": "Internal server error"}), 500


@app.route("/api/liveboard")
def api_liveboard():
    """Return delay times and full station board for trains at a given station.
    Uses v2 LiveBoard which reliably includes DepartureTime and ArrivalTime.
    Response: {delays: {trainNo: min}, boards: [{...}], cached, fetched_at}
    Cached for 120 seconds (LIVEBOARD_CACHE_TTL)."""
    station = request.args.get("station", "").strip()
    if not station:
        return jsonify({"error": "Missing 'station' parameter"}), 400
    if station not in _VALID_CODES:
        return jsonify({"error": "Invalid station code"}), 400

    with _cache_lock:
        entry      = _liveboard_cache.get(station)
        fetched_at = entry["fetched_at"] if entry else 0
    if entry and time.time() - fetched_at < LIVEBOARD_CACHE_TTL:
        return jsonify({"delays": entry["delays"], "boards": entry["boards"],
                        "cached": True, "fetched_at": fetched_at})

    try:
        url = (
            f"https://tdx.transportdata.tw/api/basic/v2/Rail/TRA"
            f"/LiveBoard/Station/{station}?$format=JSON"
        )
        data = api_get(url)
        # v2 LiveBoard returns a flat list directly; TrainTypeName is a plain string
        trains_raw = data if isinstance(data, list) else data.get("TrainLiveBoards", [])
        delays = {}
        boards = []
        for t in trains_raw:
            no = t.get("TrainNo", "")
            if not no:
                continue
            delay = int(t.get("DelayTime") or 0)
            delays[no] = delay
            _raw_dir = t.get("Direction")
            boards.append({
                "train_no":   no,
                "train_type": _format_train_type(_tdx_str(t.get("TrainTypeName", ""))),
                "dest":       _tdx_str(t.get("EndingStationName", "")),
                "arrival":    t.get("ScheduledArrivalTime", ""),
                "departure":  t.get("ScheduledDepartureTime", ""),
                "delay":      delay,
                "direction":  int(_raw_dir) if _raw_dir is not None else -1,
            })
        now = time.time()
        with _cache_lock:
            _liveboard_cache[station] = {"delays": delays, "boards": boards, "fetched_at": now}
            # Evict stale entries
            expired = [k for k, v in _liveboard_cache.items() if now - v["fetched_at"] > LIVEBOARD_CACHE_TTL]
            for k in expired:
                del _liveboard_cache[k]
        return jsonify({"delays": delays, "boards": boards, "cached": False, "fetched_at": now})
    except requests.HTTPError as e:
        return jsonify({"error": f"TDX API error: {e}"}), 502
    except RuntimeError:
        return jsonify({"error": "API rate limit exceeded, please try again later"}), 503
    except Exception:
        log.exception("Unexpected error in /api/liveboard")
        return jsonify({"error": "Internal server error"}), 500


# ─── Train detail page ────────────────────────────────────────────────────
@app.route("/train/<train_no>")
def train_detail(train_no: str):
    if not re.match(r"^\d{1,5}$", train_no):
        return "Invalid train number", 400
    return render_template("train_detail.html", train_no=train_no)


@app.route("/api/train/<train_no>")
def api_train_detail(train_no: str):
    """Return full stop list and metadata for a single train."""
    if not re.match(r"^\d{1,5}$", train_no):
        return jsonify({"error": "Invalid train number"}), 400

    # Search general timetable cache first
    train_item = None
    try:
        for item in get_all_trains():
            if item.get("TrainInfo", {}).get("TrainNo") == train_no:
                train_item = item
                break
    except Exception:
        pass

    # Fall back to today's daily timetable
    if train_item is None:
        try:
            for item in fetch_daily_trains(date.today().isoformat()):
                if item.get("TrainInfo", {}).get("TrainNo") == train_no:
                    train_item = item
                    break
        except Exception:
            pass

    if train_item is None:
        return jsonify({"error": "Train not found"}), 404

    info       = train_item.get("TrainInfo", {})
    train_type = info.get("TrainTypeName", {}).get("Zh_tw", "")
    stops = [
        {
            "seq":          s.get("StopSequence", 0),
            "station_id":   s.get("StationID", ""),
            "station_name": s.get("StationName", {}).get("Zh_tw", ""),
            "arrival":      s.get("ArrivalTime", ""),
            "departure":    s.get("DepartureTime", ""),
            "phone":        _STATION_PHONES.get(s.get("StationID", ""), ""),
            "address":      _STATION_ADDRESSES.get(s.get("StationID", ""), ""),
        }
        for s in train_item.get("StopTimes", [])
    ]
    return jsonify({
        "train_no":         train_no,
        "train_type":       train_type,
        "train_type_short": _format_train_type(train_type),
        "route":            (
            f"{info.get('StartingStationName', {}).get('Zh_tw', '')}"
            f"→{info.get('EndingStationName', {}).get('Zh_tw', '')}"
        ),
        "note":             _parse_note(info.get("Note", "")),
        "bike":             bool(info.get("BikeFlag", 0)),
        "trip_line":        _TRIP_LINE_MAP.get(info.get("TripLine", 0), ""),
        "stops":            stops,
    })


@app.route("/api/trainlive/<train_no>")
def api_trainlive(train_no: str):
    """Return real-time train position from TrainLiveBoard API. Cached 120 s (TRAINLIVE_CACHE_TTL)."""
    if not re.match(r"^\d{1,5}$", train_no):
        return jsonify({"error": "Invalid train number"}), 400
    with _cache_lock:
        entry      = _trainlive_cache.get(train_no)
        fetched_at = entry["fetched_at"] if entry else 0
    if entry and time.time() - fetched_at < TRAINLIVE_CACHE_TTL:
        return jsonify({"live": entry["live"], "cached": True})
    try:
        url  = (
            f"https://tdx.transportdata.tw/api/basic/v3/Rail/TRA"
            f"/TrainLiveBoard/TrainNo/{train_no}?$format=JSON"
        )
        data = api_get(url)
        # v3 may return {"TrainLiveBoards": [...]} or a direct list
        items = (
            data if isinstance(data, list)
            else data.get("TrainLiveBoards",
                          [data] if (isinstance(data, dict) and "StationID" in data)
                          else [])
        )
        live = None
        if items:
            raw = items[0]
            sname = raw.get("StationName", "")
            live = {
                "station_id":   raw.get("StationID", ""),
                "station_name": sname.get("Zh_tw", "") if isinstance(sname, dict) else sname,
                "delay_time":   int(raw.get("DelayTime") or 0),
                "update_time":  raw.get("UpdateTime", ""),
            }
        now = time.time()
        with _cache_lock:
            _trainlive_cache[train_no] = {"live": live, "fetched_at": now}
            expired = [k for k, v in _trainlive_cache.items() if now - v["fetched_at"] > TRAINLIVE_CACHE_TTL]
            for k in expired:
                del _trainlive_cache[k]
        return jsonify({"live": live, "cached": False})
    except requests.HTTPError as e:
        return jsonify({"live": None, "error": f"TDX API error: {e}"}), 502
    except RuntimeError:
        return jsonify({"live": None, "error": "API rate limit exceeded, please try again later"}), 503
    except Exception:
        log.exception("Unexpected error in /api/trainlive/%s", train_no)
        return jsonify({"live": None, "error": "Internal server error"}), 500


@app.route("/api/alert")
def api_alert():
    """Return TRA service alerts. Cached for 5 minutes."""
    with _cache_lock:
        entry      = _alert_cache.get("items")
        fetched_at = _alert_cache.get("fetched_at", 0)
    if entry is not None and time.time() - fetched_at < ALERT_CACHE_TTL:
        return jsonify({"alerts": entry, "cached": True})
    try:
        url  = "https://tdx.transportdata.tw/api/basic/v3/Rail/TRA/Alert?$format=JSON"
        data = api_get(url)
        alerts = data if isinstance(data, list) else data.get("Alerts", [])
        with _cache_lock:
            _alert_cache["items"]      = alerts
            _alert_cache["fetched_at"] = time.time()
        return jsonify({"alerts": alerts, "cached": False})
    except requests.HTTPError as e:
        return jsonify({"alerts": [], "error": f"TDX API error: {e}"}), 502
    except RuntimeError:
        return jsonify({"alerts": [], "error": "API rate limit exceeded, please try again later"}), 503
    except Exception:
        log.exception("Unexpected error in /api/alert")
        return jsonify({"alerts": [], "error": "Internal server error"}), 500


@app.route("/api/news")
def api_news():
    """Return TRA latest news. Cached for 1 hour."""
    with _cache_lock:
        entry      = _news_cache.get("items")
        fetched_at = _news_cache.get("fetched_at", 0)
    if entry is not None and time.time() - fetched_at < NEWS_CACHE_TTL:
        return jsonify({"news": entry, "cached": True})
    try:
        url  = "https://tdx.transportdata.tw/api/basic/v3/Rail/TRA/News?$format=JSON"
        data = api_get(url)
        news = data if isinstance(data, list) else data.get("News", [])
        with _cache_lock:
            _news_cache["items"]      = news
            _news_cache["fetched_at"] = time.time()
        return jsonify({"news": news, "cached": False})
    except requests.HTTPError as e:
        return jsonify({"news": [], "error": f"TDX API error: {e}"}), 502
    except RuntimeError:
        return jsonify({"news": [], "error": "API rate limit exceeded, please try again later"}), 503
    except Exception:
        log.exception("Unexpected error in /api/news")
        return jsonify({"news": [], "error": "Internal server error"}), 500


@app.route("/api/fare")
def api_fare():
    """Return adult fares for an OD pair, keyed by train category.

    Response: {"fares": {"自強": 100, "莒光": 80, "復興": 70, "區間": 60}, "cached": bool}
    """
    from_code = request.args.get("from", "").strip()
    to_code   = request.args.get("to",   "").strip()

    if not from_code or not to_code:
        return jsonify({"error": "Missing 'from' or 'to' parameter"}), 400
    if from_code not in _VALID_CODES or to_code not in _VALID_CODES:
        return jsonify({"error": "Invalid station code"}), 400
    if from_code == to_code:
        return jsonify({"fares": {}, "cached": True})

    cache_key = f"{from_code}_{to_code}"
    with _cache_lock:
        entry = _fare_cache.get(cache_key)
    if entry and time.time() - entry["fetched_at"] < FARE_CACHE_TTL:
        return jsonify({"fares": entry["fares"], "cached": True})

    try:
        url = (
            f"https://tdx.transportdata.tw/api/basic/v3/Rail/TRA"
            f"/ODFare/{from_code}/to/{to_code}?$format=JSON"
        )
        data = api_get(url)

        # Parse fares — the v3 response wraps records in {"ODFares": [...]}.
        # Each ODFare has: TrainType (1=自強,2=莒光,3=復興,4=區間快,5=區間),
        #   Direction (0=short route, 1=long way around the island loop),
        #   TravelDistance, and a Fares[] array.
        # Inside Fares[]: TicketType 1=one-way, FareClass 1=adult full-price.
        # We must pick the SHORT direction and map TrainType → category name.
        od_list = data if isinstance(data, list) else data.get("ODFares", [data] if "Fares" in data else [])

        # Determine the short-route direction (minimum TravelDistance).
        min_dist = float("inf")
        short_dir = 0
        for od in od_list:
            d = od.get("TravelDistance", float("inf"))
            if d < min_dist:
                min_dist = d
                short_dir = od.get("Direction", 0)

        _TTYPE = {1: "自強", 2: "莒光", 3: "復興", 4: "區間", 5: "區間"}
        fares: dict[str, int] = {}
        for od in od_list:
            if od.get("Direction") != short_dir:
                continue
            cat = _TTYPE.get(od.get("TrainType"))
            if not cat:
                continue
            for f in od.get("Fares", []):
                if f.get("TicketType") != 1 or f.get("FareClass") != 1:
                    continue
                price = f.get("Price", 0)
                if price and (cat not in fares or price < fares[cat]):
                    fares[cat] = price

        now = time.time()
        reverse_key = f"{to_code}_{from_code}"
        with _cache_lock:
            _fare_cache[cache_key]   = {"fares": fares, "fetched_at": now}
            # Fare is same in both directions for TRA
            _fare_cache[reverse_key] = {"fares": fares, "fetched_at": now}
            # Evict expired
            expired = [k for k, v in _fare_cache.items() if now - v["fetched_at"] > FARE_CACHE_TTL]
            for k in expired:
                del _fare_cache[k]

        return jsonify({"fares": fares, "cached": False})
    except requests.HTTPError as e:
        return jsonify({"fares": {}, "error": f"TDX API error: {e}"}), 502
    except RuntimeError:
        return jsonify({"fares": {}, "error": "API rate limit exceeded"}), 503
    except Exception:
        log.exception("Unexpected error in /api/fare")
        return jsonify({"fares": {}, "error": "Internal server error"}), 500


@app.route("/api/rain")
def api_rain():
    """Return all stations, one station by ID, or rainfall for station areas."""
    from_name = request.args.get("from", "").strip()
    to_name   = request.args.get("to", "").strip()
    station_id = request.args.get("station", "").strip()
    if station_id and (from_name or to_name):
        return jsonify({"error": "Use either 'station' or 'from'/'to' parameters"}), 400
    if station_id or (not from_name and not to_name):
        return api_rain_stations()
    stations = _cwa_fetch_rain_stations()
    result = {}
    if from_name:
        rain_from = _find_rain_for(from_name, stations)
        rain_from["name"] = from_name
        result["from"] = rain_from
    if to_name:
        rain_to = _find_rain_for(to_name, stations)
        rain_to["name"] = to_name
        result["to"] = rain_to
    return jsonify(result)


_RAIN_PERIODS = ("10min", "1hr", "3hr", "6hr", "12hr", "24hr")


def _rain_station_items(force: bool = False) -> tuple[list, str, int]:
    """Return (normalized stations, error message, HTTP status) for CWA rainfall."""
    if not _CWA_API_KEY:
        return [], "CWA_API_KEY is not configured", 503

    stations = _cwa_fetch_rain_stations(force=force)
    if not stations:
        with _cache_lock:
            error_code = _rain_cache.get("last_error", "")
        return [], _cwa_rain_error_message(error_code), 502

    periods = {
        "10min": "Past10Min",
        "1hr": "Past1hr",
        "3hr": "Past3hr",
        "6hr": "Past6Hr",
        "12hr": "Past12hr",
        "24hr": "Past24hr",
    }

    def _amount(station: dict, key: str) -> float | None:
        value = (station.get("RainfallElement") or {}).get(key, {}).get("Precipitation")
        try:
            amount = float(value)
        except (ValueError, TypeError):
            return None
        if not math.isfinite(amount) or amount < -9990:
            return None
        return round(amount, 1)

    items = []
    for station in stations:
        station_id = str(station.get("StationId") or station.get("StationID") or "").strip()
        if not station_id:
            continue
        geo = station.get("GeoInfo") or {}
        station_name = station.get("StationName") or "未命名測站"
        if isinstance(station_name, dict):
            station_name = station_name.get("Zh_tw") or station_name.get("En") or "未命名測站"
        rainfall = station.get("RainfallElement") or {}
        items.append({
            "id": station_id,
            "name": station_name,
            "county": geo.get("CountyName") or "未知",
            "town": geo.get("TownName") or "未知",
            "obs_time": (station.get("ObsTime") or {}).get("DateTime", ""),
            "rainfall": {label: _amount(station, field) for label, field in periods.items()},
        })

    items.sort(key=lambda item: (item["county"], item["town"], item["name"]))
    return items, "", 200


@app.route("/api/rain/stations")
def api_rain_stations():
    """Return CWA rainfall observations for the station picker and dashboard."""
    items, error, status = _rain_station_items(force=request.args.get("refresh") == "1")
    if error:
        return jsonify({"error": error, "api_calls_today": _rain_api_calls_today()}), status
    with _cache_lock:
        fetched_at = _rain_cache.get("fetched_at", 0)
    metadata = {"fetched_at": fetched_at, "api_calls_today": _rain_api_calls_today()}
    station_id = request.args.get("station", "").strip()
    if station_id:
        station = next((item for item in items if item["id"].casefold() == station_id.casefold()), None)
        if station is None:
            return jsonify({"error": f"Rain station '{station_id}' was not found", **metadata}), 404
        return jsonify({"station": station, **metadata})
    county = request.args.get("county", "").strip().replace("台", "臺")
    if county:
        items = [item for item in items if item["county"] == county]
    return jsonify({"stations": items, **metadata})


@app.route("/rain/readable")
def rain_readable_page():
    """Render cached rainfall data as a JavaScript-free HTML table."""
    result = api_rain_stations()
    if isinstance(result, tuple):
        response, status = result[0], result[1]
    else:
        response, status = result, 200
    payload = response.get_json(silent=True) or {}
    stations = payload.get("stations", [])
    if payload.get("station"):
        stations = [payload["station"]]
    default_order = {station_id: index for index, station_id in enumerate(("C0AH70", "CAA030", "C0D590", "A1AA20"))}
    stations.sort(key=lambda station: (
        (0, default_order[station["id"]])
        if station["id"] in default_order
        else (1, station["county"], station["town"], station["name"])
    ))
    fetched_at = payload.get("fetched_at")
    fetched_at_text = (
        datetime.fromtimestamp(fetched_at, _RAIN_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S %Z")
        if fetched_at else ""
    )
    return render_template(
        "rain_readable.html",
        stations=stations,
        error=payload.get("error", ""),
        fetched_at_text=fetched_at_text,
    ), status


_RAIN_DEFAULT_IDS = ("C0AH70", "CAA030", "C0D590", "A1AA20")
_RAIN_SUMMARY_TOP_N = 20


@app.route("/rain/summary")
def rain_summary_page():
    """Compact JavaScript-free rainfall summary sized for LLM/crawler fetchers."""
    result = api_rain_stations()
    if isinstance(result, tuple):
        response, status = result[0], result[1]
    else:
        response, status = result, 200
    payload = response.get_json(silent=True) or {}
    stations = payload.get("stations", [])

    county = request.args.get("county", "").strip().replace("台", "臺")
    ids_arg = request.args.get("ids", "").strip()
    ids = [i.strip().upper() for i in ids_arg.split(",") if i.strip()][:50] if ids_arg else list(_RAIN_DEFAULT_IDS)

    by_id = {s["id"].upper(): s for s in stations}
    key_stations = [by_id[i] for i in ids if i in by_id]
    scoped = [s for s in stations if s["county"] == county] if county else stations

    def _top(period: str) -> list:
        wet = [s for s in scoped if (s["rainfall"].get(period) or 0) > 0]
        wet.sort(key=lambda s: s["rainfall"][period], reverse=True)
        return wet[:_RAIN_SUMMARY_TOP_N]

    county_max = []
    if not county:
        groups: dict[str, list] = {}
        for s in stations:
            groups.setdefault(s["county"], []).append(s)
        for name, items in sorted(groups.items()):
            top_1hr = max(items, key=lambda s: s["rainfall"].get("1hr") or 0)
            top_24hr = max(items, key=lambda s: s["rainfall"].get("24hr") or 0)
            county_max.append({
                "county": name,
                "count": len(items),
                "max_1hr": top_1hr["rainfall"].get("1hr"),
                "max_1hr_station": top_1hr,
                "max_24hr": top_24hr["rainfall"].get("24hr"),
                "max_24hr_station": top_24hr,
            })

    fetched_at = payload.get("fetched_at")
    fetched_at_text = (
        datetime.fromtimestamp(fetched_at, _RAIN_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S %Z")
        if fetched_at else ""
    )
    obs_time = next((s["obs_time"] for s in stations if s.get("obs_time")), "")
    return render_template(
        "rain_summary.html",
        error=payload.get("error", ""),
        fetched_at_text=fetched_at_text,
        obs_time=obs_time,
        total=len(stations),
        county=county,
        county_stations=sorted(scoped, key=lambda s: (s["town"], s["name"])) if county else [],
        key_stations=key_stations,
        top_1hr=_top("1hr"),
        top_24hr=_top("24hr"),
        county_max=county_max,
        top_n=_RAIN_SUMMARY_TOP_N,
    ), status


# -- Turnaround planner: ride out one train, transfer, ride back one train within a time budget --
_TURNAROUND_DIRECTIONS = {"1": "逆行（西部南下／東部北上）", "0": "順行（西部北上／東部南下）", "both": "兩方向"}
_TURNAROUND_TIGHT_MIN = 5
_TURNAROUND_COOKIE = "turnaround_opts"


def _hhmm_to_min(text: str) -> int | None:
    try:
        h, m = text.split(":")[:2]
        return int(h) * 60 + int(m)
    except (AttributeError, ValueError):
        return None


def _min_to_hhmm(minutes: int) -> str:
    return f"{(minutes // 60) % 24:02d}:{minutes % 60:02d}"


def _train_stop_times(train: dict) -> list[tuple[str, int, int]]:
    """Return [(station_id, arrival_min, departure_min)] with times monotonic across midnight."""
    stops, offset, last = [], 0, -1
    for s in train.get("StopTimes", []):
        if s.get("SuspendedFlag"):
            continue
        arr = _hhmm_to_min(s.get("ArrivalTime") or s.get("DepartureTime", ""))
        dep = _hhmm_to_min(s.get("DepartureTime") or s.get("ArrivalTime", ""))
        if arr is None or dep is None:
            continue
        arr += offset
        if arr < last:
            offset += 1440
            arr += 1440
        dep += offset
        if dep < arr:
            offset += 1440
            dep += 1440
        last = dep
        stops.append((s.get("StationID", ""), arr, dep))
    return stops


def _turnaround_plans(trains: list, start: str, end: str, t0: int, direction: str = "both",
                      budget: int = 180, transfer: int = 10, exit_walk: int = 10,
                      local_only: bool = True) -> list[dict]:
    """Best plan per turnaround station, farthest (longest outbound ride) first."""
    usable = []
    for t in trains:
        info = t.get("TrainInfo", {})
        type_name = _tdx_str(info.get("TrainTypeName", ""))
        if local_only and not type_name.startswith("區間"):
            continue
        usable.append((info, _format_train_type(type_name), _train_stop_times(t)))

    # station -> [(departure_min, arrival_at_end_min, info, type)] for trains that later stop at `end`
    returns: dict[str, list] = {}
    for info, type_short, stops in usable:
        end_idx = next((i for i, s in enumerate(stops) if s[0] == end), None)
        if end_idx is None:
            continue
        for sid, _, dep in stops[:end_idx]:
            returns.setdefault(sid, []).append((dep, stops[end_idx][1], info, type_short))

    deadline = t0 + budget - exit_walk
    best: dict[str, dict] = {}
    for info, type_short, stops in usable:
        if direction in ("0", "1") and str(info.get("Direction")) != direction:
            continue
        start_idx = next((i for i, s in enumerate(stops) if s[0] == start), None)
        if start_idx is None:
            continue
        dep_start = stops[start_idx][2]
        if dep_start < t0 or dep_start >= deadline:
            continue
        for sid, arr, _ in stops[start_idx + 1:]:
            if arr >= deadline:
                break
            if sid in (start, end):
                continue
            ready = arr + transfer
            options = [r for r in returns.get(sid, []) if r[0] >= ready and r[1] <= deadline]
            if not options:
                continue
            back_dep, back_arr, back_info, back_type = min(options, key=lambda r: (r[1], -r[0]))
            ride = arr - dep_start
            current = best.get(sid)
            if current and (current["back_arr_min"], -current["ride_min"]) <= (back_arr, -ride):
                continue
            total = back_arr + exit_walk - t0
            best[sid] = {
                "code": sid,
                "direction": str(info.get("Direction")),
                "out_train": info.get("TrainNo", ""),
                "out_type": type_short,
                "out_dep": _min_to_hhmm(dep_start),
                "out_arr": _min_to_hhmm(arr),
                "wait_min": back_dep - arr,
                "back_train": back_info.get("TrainNo", ""),
                "back_type": back_type,
                "back_dep": _min_to_hhmm(back_dep),
                "back_arr": _min_to_hhmm(back_arr),
                "back_arr_min": back_arr,
                "exit_by": _min_to_hhmm(back_arr + exit_walk),
                "ride_min": ride,
                "total_min": total,
                "slack_min": budget - total,
                "tight": budget - total < _TURNAROUND_TIGHT_MIN,
            }
    names = {code: name for name, code in STATIONS.items()}
    plans = sorted(best.values(), key=lambda p: (-p["ride_min"], p["total_min"]))
    for p in plans:
        p["station"] = names.get(p["code"], p["code"])
        p.pop("back_arr_min")
    return plans


def _turnaround_query(args) -> dict:
    """Validate request args and compute plans; raises ValueError on bad input."""
    start, end = str(args.get("start", "")).strip(), str(args.get("end", "")).strip()
    if start not in _VALID_CODES or end not in _VALID_CODES:
        raise ValueError("Invalid start or end station")
    if start == end:
        raise ValueError("Start and end must differ")
    now = datetime.now(_RAIN_TIMEZONE)
    date_str = str(args.get("date") or now.date().isoformat()).strip()
    try:
        date_val = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        raise ValueError("date must be YYYY-MM-DD")
    if not now.date() - timedelta(days=1) <= date_val <= now.date() + timedelta(days=60):
        raise ValueError("date must be within 1 day ago to 60 days ahead")
    time_str = str(args.get("time") or now.strftime("%H:%M")).strip()
    t0 = _hhmm_to_min(time_str)
    if t0 is None or not 0 <= t0 < 1440:
        raise ValueError("time must be HH:MM")
    direction = str(args.get("dir") or "both")
    if direction not in _TURNAROUND_DIRECTIONS:
        raise ValueError("dir must be 0, 1 or both")

    def _int(key: str, default: int, low: int, high: int) -> int:
        try:
            return max(low, min(int(args.get(key, default)), high))
        except (TypeError, ValueError):
            return default

    budget = _int("budget", 180, 30, 360)
    transfer = _int("transfer", 10, 0, 60)
    exit_walk = _int("exit", 10, 0, 60)
    local_only = str(args.get("types", "local")) != "all"
    plans = _turnaround_plans(fetch_daily_trains(date_str), start, end, t0, direction,
                              budget, transfer, exit_walk, local_only)
    names = {code: name for name, code in STATIONS.items()}
    return {
        "start": start, "start_name": names.get(start, start),
        "end": end, "end_name": names.get(end, end),
        "date": date_str, "time": _min_to_hhmm(t0), "dir": direction,
        "budget": budget, "transfer": transfer, "exit": exit_walk,
        "types": "local" if local_only else "all",
        "plans": plans,
    }


@app.route("/api/turnaround")
def api_turnaround():
    """Farthest turnaround stations reachable from start and back to end within a time budget."""
    try:
        return jsonify(_turnaround_query(request.args))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except requests.HTTPError as e:
        return jsonify({"error": f"TDX API error: {e}"}), 502
    except RuntimeError:
        return jsonify({"error": "API rate limit exceeded, please try again later"}), 503
    except Exception:
        log.exception("Unexpected error in /api/turnaround")
        return jsonify({"error": "Internal server error"}), 500


@app.route("/turnaround")
def turnaround_page():
    """Server-rendered turnaround planner form and results (no JavaScript)."""
    names = {code: name for name, code in STATIONS.items()}
    groups = [{"city": g["city"], "stations": [(c, names[c]) for c in g["codes"] if c in names]}
              for g in _STATION_GROUPS]
    now = datetime.now(_RAIN_TIMEZONE)
    defaults = {
        "start": "", "end": "", "dir": "both",
        "budget": "180", "transfer": "10", "exit": "10", "types": "local",
    }
    # Date and time are not remembered so the page always opens on today and the current time.
    try:
        saved = json.loads(request.cookies.get(_TURNAROUND_COOKIE, "") or "{}")
    except ValueError:
        saved = {}
    if isinstance(saved, dict):
        defaults.update({k: str(v)[:16] for k, v in saved.items() if k in defaults})
    form = {k: request.args.get(k, v) for k, v in defaults.items()}
    form["date"] = request.args.get("date", now.date().isoformat())
    form["time"] = request.args.get("time", now.strftime("%H:%M"))
    result, error, status = None, "", 200
    if form["start"] and form["end"] and request.args:
        try:
            result = _turnaround_query(request.args)
        except ValueError as e:
            error, status = str(e), 400
        except (requests.HTTPError, RuntimeError):
            error, status = "TDX timetable is temporarily unavailable, please try again later.", 503
    response = make_response(render_template(
        "turnaround.html", groups=groups, form=form, result=result, error=error,
        directions=_TURNAROUND_DIRECTIONS, tight_min=_TURNAROUND_TIGHT_MIN), status)
    if result:
        remembered = {k: str(result[k]) for k in defaults}
        response.set_cookie(_TURNAROUND_COOKIE, json.dumps(remembered), max_age=365 * 86400,
                            samesite="Lax", httponly=True, secure=request.is_secure)
    return response


# -- Fastest planner: direct trains, or express then local with one transfer --
_FASTEST_COOKIE = "fastest_opts"
_FASTEST_EXPRESS = {
    "all": "全部對號車（自強／普悠瑪／太魯閣／莒光）",
    "ct": "自強＋莒光",
    "cg": "莒光",
}


def _is_local_type(type_name: str) -> bool:
    return type_name.startswith("區間")


def _is_express_type(type_name: str, group: str) -> bool:
    if _is_local_type(type_name):
        return False
    if group == "cg":
        return type_name.startswith("莒光")
    if group == "ct":
        return type_name.startswith(("自強", "莒光"))
    return type_name.startswith(("自強", "莒光", "普悠瑪", "太魯閣"))


def _pareto(journeys: list[dict]) -> list[dict]:
    """Drop journeys that leave earlier but arrive no sooner than another one."""
    kept, best_arr = [], None
    for j in sorted(journeys, key=lambda j: (-j["dep_min"], j["arr_min"], len(j["legs"]))):
        if best_arr is None or j["arr_min"] < best_arr:
            kept.append(j)
            best_arr = j["arr_min"]
    return sorted(kept, key=lambda j: j["dep_min"])


def _fastest_journeys(trains: list, start: str, end: str, t0: int, express: str = "all",
                      transfer: int = 10) -> dict[str, list]:
    """Return {"fastest": [...], "local": [...]} journeys departing start at/after t0."""
    names = {code: name for name, code in STATIONS.items()}
    parsed = []
    for t in trains:
        info = t.get("TrainInfo", {})
        type_name = _tdx_str(info.get("TrainTypeName", ""))
        parsed.append((info, type_name, _format_train_type(type_name), _train_stop_times(t)))

    def leg(info, type_short, from_code, dep, to_code, arr) -> dict:
        return {"train_no": info.get("TrainNo", ""), "type": type_short,
                "from": names.get(from_code, from_code), "dep": _min_to_hhmm(dep),
                "to": names.get(to_code, to_code), "arr": _min_to_hhmm(arr)}

    def journey(legs: list, dep: int, arr: int, via: str = "", wait: int = 0) -> dict:
        return {"dep": _min_to_hhmm(dep), "arr": _min_to_hhmm(arr), "dep_min": dep, "arr_min": arr,
                "total_min": arr - dep, "via": names.get(via, via), "wait_min": wait, "legs": legs}

    direct_express, direct_local = [], []
    # station -> [(dep, arr_at_end, info, type_short)] for local trains that later stop at `end`
    local_returns: dict[str, list] = {}
    for info, type_name, type_short, stops in parsed:
        idx = {sid: i for i, (sid, _, _) in enumerate(stops)}
        if end not in idx:
            continue
        end_i = idx[end]
        if start in idx and idx[start] < end_i and stops[idx[start]][2] >= t0:
            dep, arr = stops[idx[start]][2], stops[end_i][1]
            j = journey([leg(info, type_short, start, dep, end, arr)], dep, arr)
            if _is_local_type(type_name):
                direct_local.append(j)
            elif _is_express_type(type_name, express):
                direct_express.append(j)
        if _is_local_type(type_name):
            for sid, _, dep in stops[:end_i]:
                local_returns.setdefault(sid, []).append((dep, stops[end_i][1], info, type_short))

    # Per station: departures sorted, plus suffix index of the earliest arrival at `end` (ties -> later dep).
    suffix_best: dict[str, tuple[list, list]] = {}
    for sid, rows in local_returns.items():
        rows.sort(key=lambda r: r[0])
        best_idx, best_i = [0] * len(rows), len(rows) - 1
        for i in range(len(rows) - 1, -1, -1):
            if (rows[i][1], -rows[i][0]) < (rows[best_i][1], -rows[best_i][0]):
                best_i = i
            best_idx[i] = best_i
        suffix_best[sid] = ([r[0] for r in rows], best_idx)

    def best_local_from(sid: str, ready: int, exclude_no: str):
        if sid not in suffix_best:
            return None
        deps, best_idx = suffix_best[sid]
        i = bisect.bisect_left(deps, ready)
        if i >= len(deps):
            return None
        row = local_returns[sid][best_idx[i]]
        if row[2].get("TrainNo") != exclude_no:
            return row
        others = [r for r in local_returns[sid][i:] if r[2].get("TrainNo") != exclude_no]
        return min(others, key=lambda r: (r[1], -r[0])) if others else None

    def transfers(first_ok) -> list[dict]:
        found = []
        for info, type_name, type_short, stops in parsed:
            if not first_ok(type_name):
                continue
            start_i = next((i for i, s in enumerate(stops) if s[0] == start), None)
            if start_i is None or stops[start_i][2] < t0:
                continue
            dep = stops[start_i][2]
            best = None
            for sid, arr, _ in stops[start_i + 1:]:
                if sid == end:
                    break
                row = best_local_from(sid, arr + transfer, info.get("TrainNo"))
                if row and (best is None or (row[1], -row[0]) < (best[0], -best[1])):
                    best = (row[1], row[0], sid, arr, row[2], row[3])
            if best:
                back_arr, back_dep, sid, arr, b_info, b_type = best
                found.append(journey(
                    [leg(info, type_short, start, dep, sid, arr), leg(b_info, b_type, sid, back_dep, end, back_arr)],
                    dep, back_arr, via=sid, wait=back_dep - arr))
        return found

    fastest = _pareto(direct_express + direct_local + transfers(lambda n: _is_express_type(n, express)))
    local_transfers = transfers(_is_local_type)
    local_best = {id(j) for j in _pareto(direct_local + local_transfers)}
    local = sorted(direct_local + [j for j in local_transfers if id(j) in local_best],
                   key=lambda j: (j["dep_min"], j["arr_min"]))
    return {"fastest": fastest, "local": local}


def _fastest_query(args) -> dict:
    """Validate request args and compute journeys; raises ValueError on bad input."""
    start, end = str(args.get("start", "")).strip(), str(args.get("end", "")).strip()
    if start not in _VALID_CODES or end not in _VALID_CODES:
        raise ValueError("Invalid start or end station")
    if start == end:
        raise ValueError("Start and end must differ")
    now = datetime.now(_RAIN_TIMEZONE)
    date_str = str(args.get("date") or now.date().isoformat()).strip()
    try:
        date_val = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        raise ValueError("date must be YYYY-MM-DD")
    if not now.date() - timedelta(days=1) <= date_val <= now.date() + timedelta(days=60):
        raise ValueError("date must be within 1 day ago to 60 days ahead")
    t0 = _hhmm_to_min(str(args.get("time") or now.strftime("%H:%M")).strip())
    if t0 is None or not 0 <= t0 < 1440:
        raise ValueError("time must be HH:MM")
    express = str(args.get("express") or "all")
    if express not in _FASTEST_EXPRESS:
        raise ValueError("express must be all, ct or cg")
    try:
        transfer = max(0, min(int(args.get("transfer", 10)), 60))
    except (TypeError, ValueError):
        transfer = 10
    try:
        limit = max(1, min(int(args.get("limit", 10)), 50))
    except (TypeError, ValueError):
        limit = 10
    found = _fastest_journeys(fetch_daily_trains(date_str), start, end, t0, express, transfer)
    # Direct local journeys are shared between both lists, so pop defensively.
    for journeys in found.values():
        for j in journeys:
            j.pop("dep_min", None)
            j.pop("arr_min", None)
    names = {code: name for name, code in STATIONS.items()}
    return {
        "start": start, "start_name": names.get(start, start),
        "end": end, "end_name": names.get(end, end),
        "date": date_str, "time": _min_to_hhmm(t0), "express": express, "transfer": transfer,
        "fastest": found["fastest"][:limit],
        "local": found["local"][:limit * 2],
    }


@app.route("/api/fastest")
def api_fastest():
    """Fastest journeys (direct, or express then local) plus all local options between two stations."""
    try:
        return jsonify(_fastest_query(request.args))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except requests.HTTPError as e:
        return jsonify({"error": f"TDX API error: {e}"}), 502
    except RuntimeError:
        return jsonify({"error": "API rate limit exceeded, please try again later"}), 503
    except Exception:
        log.exception("Unexpected error in /api/fastest")
        return jsonify({"error": "Internal server error"}), 500


@app.route("/fastest")
def fastest_page():
    """Server-rendered fastest-route planner (no JavaScript)."""
    names = {code: name for name, code in STATIONS.items()}
    groups = [{"city": g["city"], "stations": [(c, names[c]) for c in g["codes"] if c in names]}
              for g in _STATION_GROUPS]
    now = datetime.now(_RAIN_TIMEZONE)
    defaults = {"start": STATIONS.get("松山", ""), "end": STATIONS.get("新竹", ""),
                "express": "all", "transfer": "10"}
    try:
        saved = json.loads(request.cookies.get(_FASTEST_COOKIE, "") or "{}")
    except ValueError:
        saved = {}
    if isinstance(saved, dict):
        defaults.update({k: str(v)[:16] for k, v in saved.items() if k in defaults})
    form = {k: request.args.get(k, v) for k, v in defaults.items()}
    # Date and time are never remembered: always today and now unless given.
    form["date"] = request.args.get("date", now.date().isoformat())
    form["time"] = request.args.get("time", now.strftime("%H:%M"))
    if request.args.get("swap"):
        form["start"], form["end"] = form["end"], form["start"]
        return redirect(url_for("fastest_page", **form))

    result, error, status = None, "", 200
    if form["start"] and form["end"]:
        try:
            result = _fastest_query(form)
        except ValueError as e:
            error, status = str(e), 400
        except (requests.HTTPError, RuntimeError):
            error, status = "TDX timetable is temporarily unavailable, please try again later.", 503
    response = make_response(render_template(
        "fastest.html", groups=groups, form=form, result=result, error=error,
        express_groups=_FASTEST_EXPRESS), status)
    if result:
        remembered = {k: str(result[k]) for k in ("start", "end", "express", "transfer")}
        response.set_cookie(_FASTEST_COOKIE, json.dumps(remembered), max_age=365 * 86400,
                            samesite="Lax", httponly=True, secure=request.is_secure)
    return response


# -- MCP (Model Context Protocol) server: stateless Streamable HTTP, JSON responses only --
_MCP_PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
_MCP_RAIN_NOTE = "Source: Taiwan CWA O-A0002-001 automatic rain gauges, refreshed every 10 minutes. Units: mm; null = no data."
_MCP_TOOLS = [
    {
        "name": "get_rain",
        "title": "Taiwan rainfall by station or county",
        "description": "Live rainfall for one CWA rain gauge (station ID, e.g. C0AH70 = Songshan, 466920 = Taipei) "
                       "or for all gauges in one county/city (Traditional Chinese, e.g. 臺北市, 花蓮縣). "
                       "County results are sorted by 24-hour rainfall, wettest first. " + _MCP_RAIN_NOTE,
        "inputSchema": {
            "type": "object",
            "properties": {
                "station": {"type": "string", "description": "CWA station ID"},
                "county": {"type": "string", "description": "County/city name, e.g. 臺北市 (台 also accepted)"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
            },
        },
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "get_top_rain",
        "title": "Wettest rain gauges in Taiwan",
        "description": "Rain gauges with the highest rainfall for a period, optionally within one county. "
                       "Use this for questions like 'where is it raining most'. " + _MCP_RAIN_NOTE,
        "inputSchema": {
            "type": "object",
            "properties": {
                "period": {"type": "string", "enum": list(_RAIN_PERIODS), "default": "1hr"},
                "county": {"type": "string", "description": "Optional county/city filter, e.g. 花蓮縣"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
            },
        },
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "get_rain_for_train_stations",
        "title": "Rainfall near Taiwan Railway stations",
        "description": "10-minute and 1-hour rainfall near Taiwan Railway (TRA) stations, by Chinese station name "
                       "(e.g. 臺北, 花蓮). " + _MCP_RAIN_NOTE,
        "inputSchema": {
            "type": "object",
            "properties": {
                "from": {"type": "string", "description": "TRA station name"},
                "to": {"type": "string", "description": "TRA station name"},
            },
        },
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
]


_MCP_TRA_NOTE = "Source: TDX (Taiwan Transport Data eXchange), Taiwan Railway (TRA). Station accepts a Chinese name (臺北/台北) or TRA station code."
_MCP_STATION_PROP = {"type": "string", "description": "TRA station name in Chinese (e.g. 臺北, 花蓮, 松山) or station code (e.g. 1000)"}
_MCP_TOOLS += [
    {
        "name": "tra_find_station",
        "title": "Find Taiwan Railway station",
        "description": "Look up TRA station names and codes by partial Chinese name. " + _MCP_TRA_NOTE,
        "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "tra_timetable",
        "title": "Taiwan Railway timetable between two stations",
        "description": "Trains from one TRA station to another on a date (default today, Taiwan time), "
                       "with departure/arrival time, duration, train type and route. " + _MCP_TRA_NOTE,
        "inputSchema": {
            "type": "object",
            "properties": {
                "from": _MCP_STATION_PROP,
                "to": _MCP_STATION_PROP,
                "date": {"type": "string", "description": "YYYY-MM-DD, default today"},
                "after": {"type": "string", "description": "Only trains departing at/after HH:MM"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 15},
            },
            "required": ["from", "to"],
        },
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "tra_liveboard",
        "title": "Taiwan Railway live departures at a station",
        "description": "Upcoming trains at a TRA station with real-time delay in minutes. "
                       "direction: 0 = northbound/clockwise, 1 = southbound/counter-clockwise. " + _MCP_TRA_NOTE,
        "inputSchema": {
            "type": "object",
            "properties": {
                "station": _MCP_STATION_PROP,
                "direction": {"type": "integer", "enum": [0, 1]},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 20},
            },
            "required": ["station"],
        },
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "tra_train",
        "title": "Taiwan Railway train details and live position",
        "description": "Stop list, train type and route for a TRA train number, plus its current station and delay. " + _MCP_TRA_NOTE,
        "inputSchema": {"type": "object", "properties": {"train_no": {"type": "string", "description": "e.g. 408"}}, "required": ["train_no"]},
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "tra_fare",
        "title": "Taiwan Railway adult one-way fares",
        "description": "Adult one-way fares (NTD) between two TRA stations by train class. " + _MCP_TRA_NOTE,
        "inputSchema": {"type": "object", "properties": {"from": _MCP_STATION_PROP, "to": _MCP_STATION_PROP}, "required": ["from", "to"]},
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "tra_alerts",
        "title": "Taiwan Railway service alerts",
        "description": "Current TRA service disruption alerts. " + _MCP_TRA_NOTE,
        "inputSchema": {"type": "object", "properties": {}},
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "tra_turnaround",
        "title": "Farthest TRA turnaround within a time limit",
        "description": "Enter `start` at `time`, ride one train out, transfer (walk `transfer` min), ride one train "
                       "back and exit at `end` (walk `exit` min) within `budget` minutes. Returns every feasible "
                       "turnaround station, farthest first, with both trains and spare minutes (tight = under 5 min). "
                       "dir: 1 = west-coast southbound, 0 = west-coast northbound, both = either. "
                       "types: local = 區間/區間快 only (default), all = every train. " + _MCP_TRA_NOTE,
        "inputSchema": {
            "type": "object",
            "properties": {
                "start": _MCP_STATION_PROP,
                "end": _MCP_STATION_PROP,
                "date": {"type": "string", "description": "YYYY-MM-DD, default today (Taiwan)"},
                "time": {"type": "string", "description": "Entry time HH:MM, default now"},
                "dir": {"type": "string", "enum": ["0", "1", "both"], "default": "both"},
                "budget": {"type": "integer", "minimum": 30, "maximum": 360, "default": 180},
                "transfer": {"type": "integer", "minimum": 0, "maximum": 60, "default": 10},
                "exit": {"type": "integer", "minimum": 0, "maximum": 60, "default": 10},
                "types": {"type": "string", "enum": ["local", "all"], "default": "local"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
            },
            "required": ["start", "end"],
        },
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "tra_fastest",
        "title": "Fastest TRA journey (express then local)",
        "description": "Fastest ways from `start` to `end` departing at/after `time`: direct trains, or an express train "
                       "then one transfer (at least `transfer` min) to a local 區間/區間快 train. Also returns all "
                       "local-only options (direct, or 區間↔區間快 with one transfer). "
                       "express: all = 自強/普悠瑪/太魯閣/莒光, ct = 自強+莒光, cg = 莒光 only. " + _MCP_TRA_NOTE,
        "inputSchema": {
            "type": "object",
            "properties": {
                "start": _MCP_STATION_PROP,
                "end": _MCP_STATION_PROP,
                "date": {"type": "string", "description": "YYYY-MM-DD, default today (Taiwan)"},
                "time": {"type": "string", "description": "Departure time HH:MM, default now"},
                "express": {"type": "string", "enum": ["all", "ct", "cg"], "default": "all"},
                "transfer": {"type": "integer", "minimum": 0, "maximum": 60, "default": 10},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
            },
            "required": ["start", "end"],
        },
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
]


def _mcp_clamp(value, default: int, upper: int) -> int:
    try:
        return max(1, min(int(value), upper))
    except (TypeError, ValueError):
        return default


def _mcp_view(view, path: str, query: dict | None = None, **kwargs) -> dict:
    """Call an existing Flask JSON view internally; raise RuntimeError on error status."""
    with app.test_request_context(path, query_string=query or {}):
        response = app.make_response(view(**kwargs))
    data = response.get_json(silent=True) or {}
    if response.status_code >= 400:
        raise RuntimeError(data.get("error") or f"HTTP {response.status_code}")
    return data


def _mcp_tra_station(value) -> tuple[str, str]:
    """Resolve a TRA station name or code to (name, code)."""
    text = str(value or "").strip().replace("台", "臺")
    if not text:
        raise ValueError("Station is required")
    if text in STATIONS:
        return text, STATIONS[text]
    names_by_code = {code: name for name, code in STATIONS.items()}
    if text in names_by_code:
        return names_by_code[text], text
    stripped = text.removesuffix("車站").removesuffix("站")
    if stripped in STATIONS:
        return stripped, STATIONS[stripped]
    matches = [name for name in STATIONS if stripped in name]
    if len(matches) == 1:
        return matches[0], STATIONS[matches[0]]
    hint = f" Did you mean: {', '.join(matches[:10])}?" if matches else ""
    raise ValueError(f"Unknown TRA station '{value}'.{hint}")


def _mcp_call_rail_tool(name: str, args: dict) -> dict:
    if name == "tra_find_station":
        query = str(args.get("query") or "").strip().replace("台", "臺").removesuffix("站")
        if not query:
            raise ValueError("query is required")
        found = [{"name": n, "code": c} for n, c in STATIONS.items() if query in n or query == c]
        return {"query": query, "stations": found[:30]}

    if name == "tra_timetable":
        from_name, from_code = _mcp_tra_station(args.get("from"))
        to_name, to_code = _mcp_tra_station(args.get("to"))
        date_str = str(args.get("date") or "").strip() or datetime.now(_RAIN_TIMEZONE).date().isoformat()
        data = _mcp_view(api_trains_daily, "/api/trains/daily", {"from": from_code, "to": to_code, "date": date_str})
        trains = data.get("ab", [])
        after = str(args.get("after") or "").strip()
        if after:
            trains = [t for t in trains if t.get("dep", "") >= after]
        limit = _mcp_clamp(args.get("limit"), 15, 100)
        return {"from": from_name, "to": to_name, "date": date_str, "total": len(trains), "trains": trains[:limit]}

    if name == "tra_liveboard":
        station_name, code = _mcp_tra_station(args.get("station"))
        data = _mcp_view(api_liveboard, "/api/liveboard", {"station": code})
        boards = data.get("boards", [])
        if args.get("direction") in (0, 1):
            boards = [b for b in boards if b.get("direction") == args["direction"]]
        limit = _mcp_clamp(args.get("limit"), 20, 50)
        fetched_at = data.get("fetched_at") or 0
        return {"station": station_name, "code": code, "boards": boards[:limit],
                "fetched_at": datetime.fromtimestamp(fetched_at, _RAIN_TIMEZONE).isoformat() if fetched_at else ""}

    if name == "tra_train":
        train_no = str(args.get("train_no") or "").strip()
        if not re.fullmatch(r"\d{1,5}", train_no):
            raise ValueError("train_no must be 1-5 digits")
        detail = _mcp_view(api_train_detail, f"/api/train/{train_no}", train_no=train_no)
        try:
            detail["live"] = _mcp_view(api_trainlive, f"/api/trainlive/{train_no}", train_no=train_no).get("live")
        except RuntimeError:
            detail["live"] = None
        for stop in detail.get("stops", []):
            stop.pop("phone", None)
            stop.pop("address", None)
        return detail

    if name == "tra_fare":
        from_name, from_code = _mcp_tra_station(args.get("from"))
        to_name, to_code = _mcp_tra_station(args.get("to"))
        data = _mcp_view(api_fare, "/api/fare", {"from": from_code, "to": to_code})
        return {"from": from_name, "to": to_name, "currency": "TWD", "fares": data.get("fares", {})}

    if name == "tra_alerts":
        alerts = _mcp_view(api_alert, "/api/alert").get("alerts", [])
        return {"count": len(alerts), "alerts": alerts[:20]}

    if name == "tra_turnaround":
        query = {k: args[k] for k in ("date", "time", "dir", "budget", "transfer", "exit", "types") if args.get(k) is not None}
        query["start"] = _mcp_tra_station(args.get("start"))[1]
        query["end"] = _mcp_tra_station(args.get("end"))[1]
        try:
            result = _turnaround_query(query)
        except (requests.HTTPError, RuntimeError):
            raise RuntimeError("TDX timetable is temporarily unavailable, please try again later")
        limit = _mcp_clamp(args.get("limit"), 10, 50)
        result["feasible_count"] = len(result["plans"])
        result["plans"] = result["plans"][:limit]
        return result

    if name == "tra_fastest":
        query = {k: args[k] for k in ("date", "time", "express", "transfer", "limit") if args.get(k) is not None}
        query["start"] = _mcp_tra_station(args.get("start"))[1]
        query["end"] = _mcp_tra_station(args.get("end"))[1]
        try:
            return _fastest_query(query)
        except (requests.HTTPError, RuntimeError):
            raise RuntimeError("TDX timetable is temporarily unavailable, please try again later")

    raise LookupError(name)


def _mcp_call_tool(name: str, args: dict) -> dict:
    """Run a tool; returns structured content or raises ValueError/RuntimeError for bad input or upstream errors."""
    if name.startswith("tra_"):
        return _mcp_call_rail_tool(name, args)
    if name == "get_rain_for_train_stations":
        from_name = str(args.get("from") or "").strip()
        to_name = str(args.get("to") or "").strip()
        if not from_name and not to_name:
            raise ValueError("Provide 'from' and/or 'to' station names")
        stations = _cwa_fetch_rain_stations()
        result = {}
        for key, station_name in (("from", from_name), ("to", to_name)):
            if station_name:
                result[key] = {"name": station_name, **_find_rain_for(station_name, stations)}
        return result

    items, error, _ = _rain_station_items()
    if error:
        raise RuntimeError(error)
    with _cache_lock:
        fetched_at = _rain_cache.get("fetched_at", 0)
    meta = {"fetched_at": datetime.fromtimestamp(fetched_at, _RAIN_TIMEZONE).isoformat() if fetched_at else ""}
    county = str(args.get("county") or "").strip().replace("台", "臺")
    if county:
        items = [item for item in items if item["county"] == county]
        if not items:
            raise ValueError(f"No rain gauges found for county '{county}'")

    if name == "get_rain":
        station_id = str(args.get("station") or "").strip()
        if station_id:
            station = next((i for i in items if i["id"].casefold() == station_id.casefold()), None)
            if station is None:
                raise ValueError(f"Rain station '{station_id}' was not found")
            return {"station": station, **meta}
        if not county:
            raise ValueError("Provide 'station' or 'county' (use get_top_rain for all of Taiwan)")
        limit = _mcp_clamp(args.get("limit"), 50, 200)
        ranked = sorted(items, key=lambda i: i["rainfall"].get("24hr") or 0, reverse=True)
        return {"county": county, "total": len(items), "stations": ranked[:limit], **meta}

    if name == "get_top_rain":
        period = args.get("period") or "1hr"
        if period not in _RAIN_PERIODS:
            raise ValueError(f"period must be one of {', '.join(_RAIN_PERIODS)}")
        limit = _mcp_clamp(args.get("limit"), 20, 100)
        wet = [i for i in items if (i["rainfall"].get(period) or 0) > 0]
        wet.sort(key=lambda i: i["rainfall"][period], reverse=True)
        return {"period": period, "county": county or "all", "wet_station_count": len(wet),
                "stations": wet[:limit], **meta}

    raise LookupError(name)


def _mcp_handle(message) -> dict | None:
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or "method" not in message:
        return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid Request"}}
    msg_id = message.get("id")
    if msg_id is None:
        return None  # notification
    method = message["method"]
    params = message.get("params") or {}

    def ok(result: dict) -> dict:
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    def err(code: int, text: str) -> dict:
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": text}}

    if method == "initialize":
        requested = params.get("protocolVersion")
        return ok({
            "protocolVersion": requested if requested in _MCP_PROTOCOL_VERSIONS else _MCP_PROTOCOL_VERSIONS[0],
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "tdx-web", "title": "Taiwan Railway & Rainfall", "version": "1.1.0"},
            "instructions": "Answer Taiwan Railway (TRA) timetable, live delay, fare, alert and rainfall questions with these tools. "
                            "Times are Taiwan time (UTC+8). Mention the observation/fetch time of live data.",
        })
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": _MCP_TOOLS})
    if method == "tools/call":
        name = params.get("name", "")
        args = params.get("arguments") or {}
        if not isinstance(args, dict):
            return err(-32602, "arguments must be an object")
        try:
            data = _mcp_call_tool(name, args)
        except LookupError:
            return err(-32602, f"Unknown tool: {name}")
        except (ValueError, RuntimeError) as exc:
            return ok({"content": [{"type": "text", "text": str(exc)}], "isError": True})
        except Exception:
            log.exception("MCP tool %s failed", name)
            return ok({"content": [{"type": "text", "text": "Internal server error"}], "isError": True})
        return ok({
            "content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False)}],
            "structuredContent": data,
            "isError": False,
        })
    return err(-32601, f"Method not found: {method}")


@app.route("/mcp", methods=["GET", "POST", "DELETE"])
def mcp_endpoint():
    """Read-only MCP server exposing TRA railway and CWA rainfall tools."""
    if request.method != "POST":
        return jsonify({"error": "Method not allowed; POST JSON-RPC messages"}), 405, {"Allow": "POST"}
    payload = request.get_json(silent=True)
    if payload is None:
        return jsonify({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}), 400
    if isinstance(payload, list):
        responses = [r for r in (_mcp_handle(m) for m in payload[:20]) if r is not None]
        return (jsonify(responses), 200) if responses else ("", 202)
    response = _mcp_handle(payload)
    return (jsonify(response), 200) if response is not None else ("", 202)


@app.route("/api/mrt/liveboard")
def api_mrt_liveboard():
    """Return MRT live arrivals for an operator and station ID."""
    station_id = request.args.get("station", "").strip().upper()
    if not station_id:
        return jsonify({"error": "Missing 'station' parameter"}), 400
    op = request.args.get("operator", "TRTC").strip().upper()
    if op not in {"TRTC", "KRTC"}:
        return jsonify({"error": "Invalid MRT operator"}), 400

    # Check cache
    now = time.time()
    with _cache_lock:
        entry = _mrt_live_cache.get(op)
        if entry and (now - entry["fetched_at"] < MRT_LIVE_CACHE_TTL):
            boards = entry["boards"]
        else:
            boards = None

    if boards is None:
        try:
            url = f"https://tdx.transportdata.tw/api/basic/v2/Rail/Metro/LiveBoard/{op}?$format=JSON"
            data = api_get(url)
            boards = data if isinstance(data, list) else data.get("LiveBoards", [])
            with _cache_lock:
                _mrt_live_cache[op] = {"boards": boards, "fetched_at": time.time()}
        except requests.HTTPError as e:
            return jsonify({"trains": [], "error": f"TDX API error: {e}"}), 502
        except RuntimeError:
            return jsonify({"trains": [], "error": "API rate limit exceeded"}), 503
        except Exception:
            log.exception("Unexpected error in /api/mrt/liveboard")
            return jsonify({"trains": [], "error": "Internal server error"}), 500

    # Filter boards for the requested station
    def _strip_op(sid):
        """Normalize 'TRTC-G19' → 'G19'."""
        return sid.split("-", 1)[-1] if "-" in sid else sid

    norm_sta = _strip_op(station_id)
    sta_boards = [b for b in boards if _strip_op(b.get("StationID", "")) == norm_sta]

    # Format response
    trains = []
    for b in sta_boards:
        dest_name = (b.get("DestinationStationName") or
                     b.get("DestinationStaionName") or {})
        svc = b.get("ServiceStatus", 0)
        raw_eta = b.get("EstimateTime", "")

        if svc in (1, 2):
            eta_str = "進站中"
            eta_min = 0
        elif raw_eta == "" or raw_eta is None:
            eta_str = "—"
            eta_min = -1
        else:
            try:
                eta_min = int(raw_eta)
                eta_str = "進站中" if eta_min == 0 else f"{eta_min} 分"
            except (ValueError, TypeError):
                eta_str = str(raw_eta)
                eta_min = -1

        trains.append({
            "destination": dest_name.get("Zh_tw", ""),
            "line_id": _strip_op(b.get("LineID", "")),
            "eta": eta_str,
            "eta_min": eta_min,
            "trip_sign": b.get("TripHeadSign", ""),
        })

    # Sort by ETA (arriving first, then by minutes)
    trains.sort(key=lambda t: (t["eta_min"] < 0, t["eta_min"]))

    # If no live trains, provide FirstLastTimetable as fallback
    first_last = []
    if not trains:
        first_last = _get_mrt_first_last(op, norm_sta)

    return jsonify({"station": station_id, "operator": op,
                    "trains": trains, "first_last": first_last})


def _get_mrt_first_last(op: str, station_id: str) -> list:
    """Return first/last train times for a MRT station (cached 24h)."""
    now = time.time()
    with _cache_lock:
        entry = _mrt_flt_cache.get(op)
        if entry and (now - entry["fetched_at"] < MRT_FLT_CACHE_TTL):
            flt_data = entry["data"]
        else:
            flt_data = None

    if flt_data is None:
        try:
            url = f"https://tdx.transportdata.tw/api/basic/v2/Rail/Metro/FirstLastTimetable/{op}?$format=JSON"
            data = api_get(url)
            flt_data = data if isinstance(data, list) else data.get("FirstLastTimetables", [])
            with _cache_lock:
                _mrt_flt_cache[op] = {"data": flt_data, "fetched_at": time.time()}
        except Exception:
            log.warning("MRT FirstLastTimetable fetch failed", exc_info=True)
            return []

    def _strip_op(sid):
        return sid.split("-", 1)[-1] if "-" in sid else sid

    results = []
    for item in flt_data:
        if _strip_op(item.get("StationID", "")) != station_id:
            continue
        dest = (item.get("DestinationStationName") or
                item.get("DestinationStaionName") or {}).get("Zh_tw", "")
        first = item.get("FirstTrainTime", "")
        last = item.get("LastTrainTime", "")
        line_id = _strip_op(item.get("LineID", ""))
        if first or last:
            results.append({
                "destination": dest,
                "line_id": line_id,
                "first": first,
                "last": last,
            })
    return results


# ═══════════════════════════════════════════════════════════════════════════
# BUS API ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════

def _bus_fetch_routes_for_city(city: str) -> list:
    """Fetch all routes for a bus city (with 24h memory cache)."""
    now = time.time()
    with _cache_lock:
        entry = _bus_routes_cache.get(city)
    if entry and now - entry["fetched_at"] < BUS_ROUTES_CACHE_TTL:
        return entry["routes"]

    url = (
        f"https://tdx.transportdata.tw/api/basic"
        f"/v2/Bus/Route/City/{city}"
        f"?$select=RouteUID,RouteID,RouteName,DepartureStopNameZh,DestinationStopNameZh"
        f"&$format=JSON"
    )
    try:
        data = api_get(url)
    except Exception as e:
        log.warning("Bus routes fetch failed for %s: %s", city, e)
        return []

    routes = []
    for r in data if isinstance(data, list) else []:
        routes.append({
            "RouteUID": r.get("RouteUID", ""),
            "RouteName": r.get("RouteName", {}).get("Zh_tw", ""),
            "RouteNameEn": r.get("RouteName", {}).get("En", ""),
            "Departure": r.get("DepartureStopNameZh", ""),
            "Destination": r.get("DestinationStopNameZh", ""),
        })

    with _cache_lock:
        _bus_routes_cache[city] = {"routes": routes, "fetched_at": time.time()}
    log.info("Cached %d bus routes for %s", len(routes), city)
    return routes


def _bus_fetch_eta(city: str, route_name: str) -> list:
    """Fetch ETA for a bus route (with 60s memory cache)."""
    cache_key = f"{city}_{route_name}"
    now = time.time()
    with _cache_lock:
        entry = _bus_eta_cache.get(cache_key)
    if entry and now - entry["fetched_at"] < BUS_ETA_CACHE_TTL:
        return entry["etas"]

    url = (
        f"https://tdx.transportdata.tw/api/basic"
        f"/v2/Bus/EstimatedTimeOfArrival/City/{city}/{route_name}"
        f"?$format=JSON"
    )
    data = api_get(url)
    etas = []
    for item in (data if isinstance(data, list) else []):
        etas.append({
            "StopUID": item.get("StopUID", ""),
            "StopName": item.get("StopName", {}).get("Zh_tw", ""),
            "Direction": item.get("Direction", -1),
            "EstimateTime": item.get("EstimateTime"),
            "StopStatus": item.get("StopStatus", -1),
            "NextBusTime": item.get("NextBusTime"),
            "IsLastBus": item.get("IsLastBus"),
        })

    with _cache_lock:
        _bus_eta_cache[cache_key] = {"etas": etas, "fetched_at": time.time()}
        # Evict stale entries
        expired = [k for k, v in _bus_eta_cache.items() if now - v["fetched_at"] > BUS_ETA_CACHE_TTL * 3]
        for k in expired:
            del _bus_eta_cache[k]
    return etas


@app.route("/bus")
def bus_page():
    """Serve the bus dashboard page."""
    return render_template("bus.html")


@app.route("/api/bus/cities")
def api_bus_cities():
    """Return list of available bus cities."""
    result = [{"City": k, "CityNameZH": v} for k, v in _BUS_CITIES.items()]
    return jsonify(result)


@app.route("/api/bus/search")
def api_bus_search():
    """Search bus routes across multiple cities.
    Query params: q (required), cities (comma-separated, default Taipei,NewTaipei)"""
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify([])

    cities_param = request.args.get("cities", "").strip()
    if cities_param:
        cities = [c.strip() for c in cities_param.split(",") if c.strip() in _BUS_CITIES]
    else:
        cities = list(_BUS_DEFAULT_CITIES)
    if not cities:
        cities = list(_BUS_DEFAULT_CITIES)

    q_lower = q.lower()
    results = []

    def _search_city(city):
        all_routes = _bus_fetch_routes_for_city(city)
        matched = [
            r for r in all_routes
            if q_lower in r["RouteName"].lower()
            or q_lower in r.get("RouteNameEn", "").lower()
        ]
        matched.sort(key=lambda x: x["RouteName"])
        return city, matched[:20]

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {pool.submit(_search_city, c): c for c in cities}
        for fut in as_completed(futures):
            try:
                city, matched = fut.result()
                if matched:
                    results.append({
                        "City": city,
                        "CityNameZH": _BUS_CITIES.get(city, city),
                        "Routes": matched,
                    })
            except Exception as e:
                log.warning("Bus search error for %s: %s", futures[fut], e)

    # Keep default cities first
    city_order = {c: i for i, c in enumerate(cities)}
    results.sort(key=lambda x: city_order.get(x["City"], 999))
    return jsonify(results)


@app.route("/api/bus/stops")
def api_bus_stops():
    """Get stop sequence for a bus route (cached 24h).
    Query params: city (required), route (required)"""
    city = request.args.get("city", "").strip()
    route_name = request.args.get("route", "").strip()
    if not city or not route_name:
        return jsonify({"error": "Missing 'city' or 'route' parameter"}), 400
    if city not in _BUS_CITIES:
        return jsonify({"error": "Invalid city"}), 400

    cache_key = f"{city}_{route_name}"
    now = time.time()
    with _cache_lock:
        entry = _bus_stops_cache.get(cache_key)
    if entry and now - entry["fetched_at"] < BUS_STOPS_CACHE_TTL:
        return jsonify({"stops": entry["stops"], "cached": True})

    try:
        url = (
            f"https://tdx.transportdata.tw/api/basic"
            f"/v2/Bus/DisplayStopOfRoute/City/{city}/{route_name}"
            f"?$format=JSON"
        )
        data = api_get(url)
        result = []
        for direction_data in (data if isinstance(data, list) else []):
            stops = []
            for s in direction_data.get("Stops", []):
                stops.append({
                    "StopUID": s.get("StopUID", ""),
                    "StopName": s.get("StopName", {}).get("Zh_tw", ""),
                    "StopNameEn": s.get("StopName", {}).get("En", ""),
                    "StopSequence": s.get("StopSequence", 0),
                })
            result.append({
                "Direction": direction_data.get("Direction", 0),
                "RouteName": direction_data.get("RouteName", {}).get("Zh_tw", ""),
                "Stops": stops,
            })

        with _cache_lock:
            _bus_stops_cache[cache_key] = {"stops": result, "fetched_at": time.time()}
        return jsonify({"stops": result, "cached": False})
    except requests.HTTPError as e:
        return jsonify({"error": f"TDX API error: {e}"}), 502
    except RuntimeError:
        return jsonify({"error": "API rate limit exceeded, please try again later"}), 503
    except Exception:
        log.exception("Unexpected error in /api/bus/stops")
        return jsonify({"error": "Internal server error"}), 500


def _bus_estimate_2nd(eta_list: list, stops_data: list, direction: int,
                      target_stop_uid: str, first_eta: int) -> int | None:
    """Estimate 2nd bus arrival from earlier stops on the route.

    When the 1st bus is close (< 15 min), it has likely passed stops 3-5
    stations back. Those earlier stops' ETAs then represent the NEXT bus.
    We add estimated inter-stop travel time to get 2nd bus arrival at target.
    """
    # Find stop sequence for this direction
    stop_seq = None
    for d in stops_data:
        if d.get("Direction") == direction:
            stop_seq = d.get("Stops", [])
            break
    if not stop_seq:
        return None

    # Sort by StopSequence
    stop_seq = sorted(stop_seq, key=lambda s: s.get("StopSequence", 0))

    # Find target stop index
    target_idx = None
    for i, s in enumerate(stop_seq):
        if s["StopUID"] == target_stop_uid:
            target_idx = i
            break
    if target_idx is None:
        return None

    # Build ETA lookup by StopUID for this direction
    eta_by_uid = {}
    for e in eta_list:
        if e["Direction"] == direction:
            uid = e["StopUID"]
            if uid not in eta_by_uid:
                eta_by_uid[uid] = e.get("EstimateTime")

    # Look at stops 3-5 positions earlier
    best_eta2 = None
    for offset in range(3, 6):
        earlier_idx = target_idx - offset
        if earlier_idx < 0:
            break
        earlier_uid = stop_seq[earlier_idx]["StopUID"]
        earlier_eta = eta_by_uid.get(earlier_uid)
        if earlier_eta is None:
            continue

        # If earlier stop's ETA > first bus ETA at target, it's likely a 2nd bus
        if earlier_eta > first_eta:
            # Estimate travel time: ~90 sec per stop
            travel_time = offset * 90
            estimated_arrival = earlier_eta + travel_time
            if best_eta2 is None or estimated_arrival < best_eta2:
                best_eta2 = estimated_arrival

    return best_eta2


@app.route("/api/bus/batch_eta", methods=["POST"])
def api_bus_batch_eta():
    """Batch ETA fetch for multiple bus stops across routes/cities.
    Request body: [{city, routeName, direction, stopUID, stopName}]
    Returns: [{city, routeName, direction, stopUID, stopName, EstimateTime, StopStatus, IsLastBus, EstimateTime2}]"""
    try:
        items = request.get_json(force=True)
        if not isinstance(items, list):
            return jsonify({"error": "Expected JSON array"}), 400

        # Group by (city, routeName) to deduplicate API calls
        route_groups = {}
        for item in items:
            key = (item.get("city", ""), item.get("routeName", ""))
            if key[0] not in _BUS_CITIES:
                continue
            if key not in route_groups:
                route_groups[key] = []
            route_groups[key].append(item)

        # Fetch ETA for each unique (city, route) in parallel
        eta_cache_local = {}

        def _fetch_group(key):
            city, route_name = key
            return key, _bus_fetch_eta(city, route_name)

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = {pool.submit(_fetch_group, k): k for k in route_groups}
            for fut in as_completed(futures):
                try:
                    key, eta_list = fut.result()
                    eta_cache_local[key] = eta_list
                except Exception as e:
                    log.warning("Batch ETA fetch error: %s", e)

        # Pre-fetch stop sequences for estimating 2nd bus
        stops_cache_local = {}
        for key in route_groups:
            city_k, route_k = key
            cache_key = f"{city_k}_{route_k}"
            with _cache_lock:
                entry = _bus_stops_cache.get(cache_key)
            if entry:
                stops_cache_local[key] = entry["stops"]
            else:
                stops_cache_local[key] = []

        # Match requested stops to ETA data
        results = []
        for item in items:
            city = item.get("city", "")
            route_name = item.get("routeName", "")
            direction = item.get("direction")
            stop_uid = item.get("stopUID", "")
            key = (city, route_name)

            eta_list = eta_cache_local.get(key, [])
            # Collect all matching records (multiple buses may approach)
            matches = [e for e in eta_list
                       if e["StopUID"] == stop_uid and e["Direction"] == direction]
            matches.sort(key=lambda x: x.get("EstimateTime") if x.get("EstimateTime") is not None else 999999)

            eta2 = None
            if matches:
                match = matches[0]
                # Direct 2nd bus from API (multiple plates)
                if len(matches) > 1:
                    eta2 = matches[1].get("EstimateTime")

                # Estimate 2nd bus from earlier stops when 1st bus < 15 min
                first_eta = match.get("EstimateTime")
                if eta2 is None and first_eta is not None and first_eta <= 900:
                    eta2 = _bus_estimate_2nd(
                        eta_list, stops_cache_local.get(key, []),
                        direction, stop_uid, first_eta
                    )

                results.append({
                    "city": city,
                    "routeName": route_name,
                    "direction": direction,
                    "stopUID": stop_uid,
                    "stopName": match.get("StopName", ""),
                    "EstimateTime": match.get("EstimateTime"),
                    "StopStatus": match.get("StopStatus", -1),
                    "IsLastBus": match.get("IsLastBus"),
                    "NextBusTime": match.get("NextBusTime"),
                    "EstimateTime2": eta2,
                })
            else:
                results.append({
                    "city": city,
                    "routeName": route_name,
                    "direction": direction,
                    "stopUID": stop_uid,
                    "stopName": item.get("stopName", ""),
                    "EstimateTime": None,
                    "StopStatus": -1,
                    "IsLastBus": None,
                    "NextBusTime": None,
                    "EstimateTime2": None,
                })

        return jsonify(results)
    except requests.HTTPError as e:
        return jsonify({"error": f"TDX API error: {e}"}), 502
    except RuntimeError:
        return jsonify({"error": "API rate limit exceeded, please try again later"}), 503
    except Exception:
        log.exception("Unexpected error in /api/bus/batch_eta")
        return jsonify({"error": "Internal server error"}), 500


@app.route("/health")
def health():
    return "OK", 200

@app.route("/debug/cache")
def debug_cache():
    """Debug endpoint — export cache data for analysis."""
    if not app.debug:
        return "Not found", 404
    with _cache_lock:
        snapshot = {
            "timetable": _timetable_cache.copy(),
            "daily_keys": list(_daily_cache.keys()),
            "daily_data": {k: v.copy() for k, v in _daily_cache.items()},
        }
    timetable = snapshot["timetable"]
    daily = snapshot["daily_data"]
    return jsonify({
            "timetable_cache": {
                "has_trains": bool(timetable.get("trains")),
                "train_count": len(timetable.get("trains", [])),
                "fetched_at": timetable.get("fetched_at", 0),
                "expire_iso": timetable.get("expire_iso", ""),
                "sample_trains": [
                    {
                        "train_no": t.get("TrainInfo", {}).get("TrainNo", ""),
                        "bike_flag_raw": t.get("TrainInfo", {}).get("BikeFlag", None),
                        "bike_flag_type": type(t.get("TrainInfo", {}).get("BikeFlag", None)).__name__,
                    }
                    for t in timetable.get("trains", [])[:10]
                ]
            },
            "daily_cache": {
                "entries": len(daily),
                "dates": list(daily.keys()),
                "sample": {
                    d: {
                        "train_count": len(daily[d].get("trains", [])),
                        "sample_trains": [
                            {
                                "train_no": t.get("TrainInfo", {}).get("TrainNo", ""),
                                "bike_flag_raw": t.get("TrainInfo", {}).get("BikeFlag", None),
                                "bike_flag_type": type(t.get("TrainInfo", {}).get("BikeFlag", None)).__name__,
                            }
                            for t in daily[d].get("trains", [])[:5]
                        ]
                    }
                    for d in list(daily.keys())[:1]
                }
            },
            "station_groups": {
                "group_count": len(_STATION_GROUPS),
                "groups": [
                    {"city": g["city"], "count": len(g["codes"]),
                     "sample": g["codes"][:3]}
                    for g in _STATION_GROUPS
                ]
            }
        })

if __name__ == "__main__":
    #app.run()
    app.run(debug=os.environ.get("FLASK_DEBUG", "0") == "1", port=5000)
