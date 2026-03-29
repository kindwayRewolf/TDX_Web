#!/usr/bin/env python3
"""
台鐵時刻表 — Flask Web API
Reuses TDX auth/filter logic; serves JSON to the HTML frontend.
"""

import os
import re
import time
import threading
from datetime import datetime, date, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request

load_dotenv(Path(__file__).parent / ".env")

# ─── TDX 認證 ──────────────────────────────────────────────────────────────
CLIENT_ID     = os.environ["TDX_CLIENT_ID"]
CLIENT_SECRET = os.environ["TDX_CLIENT_SECRET"]
TOKEN_URL     = (
    "https://tdx.transportdata.tw/auth/realms/TDXConnect"
    "/protocol/openid-connect/token"
)

# ─── In-memory caches (no filesystem dependency) ───────────────────────────
_token_cache: dict = {}          # {"access_token": ..., "expires_at": ...}
_timetable_cache: dict = {}      # {"trains": [...], "fetched_at": float, "expire_iso": str}
_daily_cache: dict = {}          # {date_str: {"trains": [...], "fetched_at": float}}
_od_cache: dict = {}             # {"{fc}_{tc}": {"ab": [...], "ba": [...], "fetched_at": float}}
_liveboard_cache: dict = {}      # {"trains": {train_no: delay_min}, "fetched_at": float}
_cache_lock = threading.Lock()

OD_CACHE_TTL        = 10 * 60          # 10 minutes  (matches client TTL)
DAILY_CACHE_TTL     = 7 * 24 * 3600   # 7 days      (matches client TTL)
LIVEBOARD_CACHE_TTL = 60               # 60 seconds  (live data, short TTL)

# ─── 車站代碼表 ────────────────────────────────────────────────────────────
STATIONS: dict[str, str] = {
    "基隆": "0900", "三坑": "0910", "八堵": "0920", "七堵": "0930",
    "百福": "0940", "五堵": "0950", "汐止": "0960", "汐科": "0970",
    "南港": "0980", "松山": "0990", "臺北": "1000", "萬華": "1010",
    "板橋": "1020", "浮洲": "1030", "樹林": "1040", "山佳": "1060",
    "鶯歌": "1070", "桃園": "1080", "內壢": "1090", "中壢": "1100",
    "埔心": "1110", "楊梅": "1120", "富岡": "1130", "新豐": "1170",
    "湖口": "1160", "竹北": "1180", "新竹": "1210",
    "竹南": "1250", "苗栗": "3160", "三義": "3190", "豐原": "3230",
    "潭子": "3250", "臺中": "3300", "彰化": "3360", "員林": "3390",
    "田中": "3420", "二水": "3430", "斗六": "3470", "斗南": "3480",
    "嘉義": "4080", "新營": "4120", "臺南": "4220", "新左營": "4340",
    "左營": "4350", "高雄": "4400", "鳳山": "4440", "屏東": "5000",
    "花蓮": "7000", "新城": "7030", "宜蘭": "7190", "羅東": "7160",
    "蘇澳新": "7130",
}

_TRIP_LINE_MAP = {1: "山線", 2: "海線", 3: "成追線"}


# ─── Token ─────────────────────────────────────────────────────────────────
def get_token() -> str:
    with _cache_lock:
        if time.time() < _token_cache.get("expires_at", 0) - 30:
            return _token_cache["access_token"]
    resp = requests.post(
        TOKEN_URL,
        headers={"content-type": "application/x-www-form-urlencoded"},
        data={
            "grant_type":    "client_credentials",
            "client_id":     CLIENT_ID,
            "client_secret": CLIENT_SECRET,
        },
        timeout=15,
    )
    resp.raise_for_status()
    j = resp.json()
    with _cache_lock:
        _token_cache["access_token"] = j["access_token"]
        _token_cache["expires_at"]   = time.time() + int(j.get("expires_in", 1800))
    return _token_cache["access_token"]


def api_get(url: str) -> dict:
    token = get_token()
    headers = {"authorization": f"Bearer {token}", "Accept-Encoding": "gzip"}
    resp = requests.get(url, headers=headers, timeout=60)
    if resp.status_code == 401:
        with _cache_lock:
            _token_cache.clear()
        token = get_token()
        headers["authorization"] = f"Bearer {token}"
        resp = requests.get(url, headers=headers, timeout=60)
    resp.raise_for_status()
    return resp.json()


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
        elif time.time() - fetched_at < OD_CACHE_TTL:
            return cached

    url = (
        "https://tdx.transportdata.tw/api/basic/v3/Rail/TRA"
        "/GeneralTrainTimetable?$top=1000&$format=JSON"
    )
    data = api_get(url)
    trains = data.get("TrainTimetables", [])
    with _cache_lock:
        _timetable_cache["trains"]     = trains
        _timetable_cache["fetched_at"] = time.time()
        _timetable_cache["expire_iso"] = data.get("ExpireDate", "")
    return trains


def fetch_daily_trains(date_str: str) -> list:
    with _cache_lock:
        entry = _daily_cache.get(date_str)
    if entry and time.time() - entry["fetched_at"] < DAILY_CACHE_TTL:
        return entry["trains"]

    url = (
        f"https://tdx.transportdata.tw/api/basic/v3/Rail/TRA"
        f"/DailyTrainTimetable/TrainDate/{date_str}?$top=2000&$format=JSON"
    )
    data = api_get(url)
    trains = data.get("TrainTimetables", [])
    with _cache_lock:
        _daily_cache[date_str] = {"trains": trains, "fetched_at": time.time()}
        # Prune old dates
        cutoff = (date.today() - timedelta(days=7)).isoformat()
        for k in [k for k in _daily_cache if k < cutoff]:
            del _daily_cache[k]
        # Sync BikeFlag from daily → general timetable cache
        general_trains = _timetable_cache.get("trains")
        if general_trains:
            daily_bike = {
                t.get("TrainInfo", {}).get("TrainNo"): t.get("TrainInfo", {}).get("BikeFlag")
                for t in trains
                if t.get("TrainInfo", {}).get("TrainNo")
            }
            for t in general_trains:
                info = t.get("TrainInfo", {})
                no = info.get("TrainNo")
                if no in daily_bike and daily_bike[no] != info.get("BikeFlag"):
                    info["BikeFlag"] = daily_bike[no]
            # Invalidate OD cache so updated BikeFlag is reflected
            _od_cache.clear()
    return trains


# ─── Formatting helpers ────────────────────────────────────────────────────
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


_RE_NO_STANDING = re.compile(
    r"(?:(?:本(?:班次|列次))?不發售無座[位]?票[^。]*。?\s*"
    r"(?:非持本班次車票旅客[^。\n]*。?\s*)?)+"
)
_RE_LOUNGE     = re.compile(r"第\d+節(?:車廂)?為騰雲座艙[^。]*。?\s*(?:為本[公司局]+公告[^。]*。?\s*)?")
_RE_FREE_SEAT  = re.compile(r"第\d+[~\-]?\d*節(?:車廂)?為自由座車廂[^。]*。?\s*(?:非持本班次車票旅客[^。]*。?\s*)?")
_RE_NON_HOLDER = re.compile(r"非持本班次車票旅客[^。]*。?\s*")
_RE_SCHEDULE   = re.compile(r"^([逢][^。]+(?:行駛|停駛))[。]?")


def _parse_note(raw: str) -> str:
    if not raw:
        return ""
    note = raw.strip()
    _DAILY = "每日行駛。"
    if note.startswith(_DAILY):
        note = note[len(_DAILY):].strip()
    parts = []
    m = _RE_SCHEDULE.match(note)
    if m:
        parts.append(m.group(1))
        note = note[m.end():].strip()
    elif note.startswith("民國") or re.match(r"^\d{3}年", note):
        end = note.find("。")
        parts.append(note[:end] if end != -1 else note[:20])
        note = note[end + 1:].strip() if end != -1 else ""
    m2 = re.search(r"(在\S{1,4}跨日)", note)
    if m2:
        parts.append(m2.group(1))
        note = (note[:m2.start()] + note[m2.end():]).strip()
    if _RE_NO_STANDING.search(note):
        note = _RE_NO_STANDING.sub("", note).strip()
        parts.append("僅限有座票")
    note = _RE_LOUNGE.sub("", note)
    note = _RE_FREE_SEAT.sub("", note)
    note = _RE_NON_HOLDER.sub("", note)
    note = re.sub(r"※\s*", "", note).strip().strip("。").strip()
    if note:
        parts.append(note[:20] + ("…" if len(note) > 20 else ""))
    return "　".join(p for p in parts if p.strip())


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
            "train_name": f"{train_type} {train_no}",
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


@app.route("/")
def index():
    stations_list = [{"name": n, "code": c} for n, c in STATIONS.items()]
    return render_template("index.html", stations=stations_list)


@app.route("/api/stations")
def api_stations():
    return jsonify([{"name": n, "code": c} for n, c in STATIONS.items()])


_VALID_CODES = set(STATIONS.values())


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
        with _cache_lock:
            _od_cache[cache_key] = {"ab": ab, "ba": ba, "fetched_at": now}
            # Evict expired entries
            expired = [k for k, v in _od_cache.items() if now - v["fetched_at"] > OD_CACHE_TTL]
            for k in expired:
                del _od_cache[k]
        return jsonify({"ab": ab, "ba": ba, "cached": False})
    except requests.HTTPError as e:
        return jsonify({"error": f"TDX API error: {e}"}), 502
    except Exception:
        return jsonify({"error": "Internal server error"}), 500


@app.route("/api/trains/daily")
def api_trains_daily():
    """Specific date timetable for an OD pair."""
    from_code = request.args.get("from", "").strip()
    to_code   = request.args.get("to",   "").strip()
    date_str  = request.args.get("date", "").strip()

    if not from_code or not to_code or not date_str:
        return jsonify({"error": "Missing 'from', 'to', or 'date' parameter"}), 400
    if from_code not in _VALID_CODES or to_code not in _VALID_CODES:
        return jsonify({"error": "Invalid station code"}), 400
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return jsonify({"error": "date must be YYYY-MM-DD"}), 400

    try:
        raw = fetch_daily_trains(date_str)
        ab  = filter_od(raw, from_code, to_code)
        ba  = filter_od(raw, to_code, from_code)
        return jsonify({"ab": ab, "ba": ba, "date": date_str})
    except requests.HTTPError as e:
        return jsonify({"error": f"TDX API error: {e}"}), 502
    except Exception:
        return jsonify({"error": "Internal server error"}), 500

@app.route("/api/liveboard")
def api_liveboard():
    """Return current delay times for trains at a given station. Cached for 60 seconds."""
    station = request.args.get("station", "").strip()
    if not station:
        return jsonify({"error": "Missing 'station' parameter"}), 400
    if station not in _VALID_CODES:
        return jsonify({"error": "Invalid station code"}), 400

    with _cache_lock:
        entry      = _liveboard_cache.get(station)
        fetched_at = entry["fetched_at"] if entry else 0
    if entry and time.time() - fetched_at < LIVEBOARD_CACHE_TTL:
        return jsonify({"delays": entry["delays"], "cached": True, "fetched_at": fetched_at})

    try:
        url = (
            f"https://tdx.transportdata.tw/api/basic/v2/Rail/TRA"
            f"/LiveBoard/Station/{station}?$format=JSON"
        )
        data = api_get(url)
        # v2 LiveBoard returns a flat list directly
        trains_raw = data if isinstance(data, list) else data.get("TrainLiveBoards", [])
        delays = {
            t["TrainNo"]: int(t.get("DelayTime", 0))
            for t in trains_raw
            if t.get("TrainNo")
        }
        now = time.time()
        with _cache_lock:
            _liveboard_cache[station] = {"delays": delays, "fetched_at": now}
        return jsonify({"delays": delays, "cached": False, "fetched_at": now})
    except requests.HTTPError as e:
        return jsonify({"error": f"TDX API error: {e}"}), 502
    except Exception:
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
            }
        })

if __name__ == "__main__":
    #app.run()
    app.run(debug=True, port=5000)
