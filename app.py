#!/usr/bin/env python3
"""
台鐵時刻表 — Flask Web API
Reuses TDX auth/filter logic; serves JSON to the HTML frontend.
"""

import collections
import json
import logging
import os
import re
import time
import threading
from datetime import datetime, date, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request

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
_CACHE_VER = "20260403a"

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

    Thread-safe.  `acquire()` blocks until a slot is free, then claims it
    atomically — so a request is never issued before a slot is guaranteed.
    Works correctly with any number of concurrent threads.
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

    def acquire(self) -> None:
        """Block until a slot is available, then claim it atomically."""
        while True:
            with self._lock:
                now = time.time()
                self._prune(now)
                if len(self._ts) < self.MAX_REQ:
                    self._ts.append(now)
                    return
                wait = self._ts[0] + self.WINDOW - now
            # Sleep outside the lock so other threads can acquire concurrently.
            time.sleep(min(wait, 1.0))

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
_cache_lock = threading.Lock()

OD_CACHE_TTL          = 30 * 60          # 30 minutes  (matches client TTL)
GENERAL_CACHE_TTL     = 12 * 3600        # 12 hours    (general timetable rarely changes)
DAILY_CACHE_TTL       = 7 * 24 * 3600   # 7 days      (matches client TTL)
LIVE_CACHE_TTL        = 120              # 120 seconds — shared by liveboard & trainlive
LIVEBOARD_CACHE_TTL   = LIVE_CACHE_TTL
TRAINLIVE_CACHE_TTL   = LIVE_CACHE_TTL
ALERT_CACHE_TTL       = 15 * 60         # 15 minutes
NEWS_CACHE_TTL        = 60 * 60         # 1 hour
FARE_CACHE_TTL        = 24 * 3600       # 24 hours  (fares rarely change)

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
      - `acquire()` then blocks the calling thread until that slot is confirmed
        (atomic claim).  This prevents 429s proactively instead of reacting to them.
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
        best_idx = min(
            range(len(_key_pool)),
            key=lambda i: (
                _key_pool[i]["limiter"].next_available_at(),
                (i - start) % len(_key_pool),   # round-robin tiebreaker
            ),
        )
        key = _key_pool[best_idx]

        # Block here until the chosen key has a free rate-limit slot.
        key["limiter"].acquire()

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
    stations_list = [{"name": n, "code": c, "cls": _STATION_CLASSES.get(c, -1)}
                     for n, c in STATIONS.items()]
    return render_template("index.html", stations=stations_list,
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
