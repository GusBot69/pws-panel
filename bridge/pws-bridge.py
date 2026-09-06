#!/usr/bin/env python3
"""PWS bridge — serves weather JSON on 127.0.0.1:8766 for the GNOME panel extension.

Pure stdlib. Reuses the same fetchers as /pws (pws_fetcher.py + aqi_fetcher.py).
Caches responses for 10 min so multiple clients can't hammer Weather.com.
Endpoints:
  GET /health               -> {"ok": true}
  GET /pws                  -> home station, backward-compatible
  GET /pws?station=X        -> explicit station ID
  GET /pws?lat=Y&lon=Z      -> auto: nearest PWS to coordinates (via weather.com v3 point)
  GET /stations             -> list of known/curated station IDs (for settings UI)
"""
import json
import math
import os
import sys
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# Fetchers are shipped next to this script (installed by install.sh)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from pws_fetcher import fetch_pws_current, fetch_pws_current_precip_total, _PWS_KEY
from aqi_fetcher import fetch_local_aqi

HOST = "127.0.0.1"
PORT = 8766
CACHE_TTL = 600  # seconds (10 min) — respects the 15-min Weather.com polling rule
# Station IDs are env-overridable so installs aren't pinned to any one town.
# Defaults are placeholders; a real install sets these (or passes ?station=).
HOME_STATION = os.environ.get("PWS_HOME_STATION", "PWS-LOCAL")
FALLBACK_STATIONS = os.environ.get("PWS_FALLBACK_STATIONS", "PWS-LOCAL-2").split(",")
POINT_URL = ("https://api.weather.com/v3/location/point"
             "?language=en-US&format=json&apiKey={key}&geocode={lat},{lon}")
CURRENT_URL = ("https://api.weather.com/v2/pws/observations/current"
               "?stationId={station}&format=json&units=e&apiKey={key}")
RIVER_URL = os.environ.get(
    "PWS_RIVER_URL", ""  # e.g. your county dam/stream gauge page — none by default
)
RIVER_LABEL = os.environ.get("PWS_RIVER_LABEL", "River gauge")

_cache = {"data": None, "ts": 0.0, "key": ""}
_gauge_cache = {"data": None, "ts": 0.0}


def fetch_river_gauge():
    """Scrape a live river/dam gauge page (level + gate positions).

    Default source is the author's local county dam page; override with
    PWS_RIVER_URL / PWS_RIVER_LABEL for any other gauge. Page updates
    continuously; we cache 15 min and never let a scrape failure break
    the main /pws payload.
    """
    now = time.time()
    if _gauge_cache["data"] is not None and (now - _gauge_cache["ts"]) < 900:
        return _gauge_cache["data"]
    if not RIVER_URL:
        return None
    try:
        req = urllib.request.Request(RIVER_URL, headers={"User-Agent": "Mozilla/5.0"})
        html = urllib.request.urlopen(req, timeout=10).read().decode("utf-8", "replace")
        import re
        def grab(span_id):
            m = re.search(r'id="%s">\s*([^<]+?)\s*<' % re.escape(span_id), html)
            return m.group(1).strip() if m else None
        data = {
            "label": RIVER_LABEL,
            "stream_level_ft": grab("MainContent_fw_stlvl1"),
            "pedestrian_bridge_ft": grab("MainContent_fw_napPedbridge"),
            "gate1_pct": grab("MainContent_fw_gte1"),
            "gate2_pct": grab("MainContent_fw_gte2"),
            "gate3_pct": grab("MainContent_fw_gte3"),
            "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }
        if data["stream_level_ft"] is None:
            return None
        _gauge_cache["data"] = data
        _gauge_cache["ts"] = now
        return data
    except Exception:
        return None


def fetch_station_current(station):
    """Fetch current conditions for an arbitrary station ID. Returns dict or None."""
    url = CURRENT_URL.format(station=station, key=_PWS_KEY)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "PwsBridge/1.0 (hermes)"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        observations = data.get("observations", [])
        if not observations:
            return None
        obs = observations[0]
        imperial = obs.get("imperial", {})
        return {
            "temp_f": imperial.get("temp"),
            "humidity": obs.get("humidity"),
            "precip_rate": imperial.get("precipRate"),
            "precip_total": imperial.get("precipTotal"),
            "pressure": imperial.get("pressure"),
            "dewpt_f": imperial.get("dewpt"),
            "wind_mph": imperial.get("windSpeed"),
            "wind_gust_mph": obs.get("windgustHigh"),
            "wind_dir": obs.get("winddir"),
            "solar_radiation": obs.get("solarRadiation"),
            "uv_index": obs.get("uv"),
            "obs_time": obs.get("obsTimeUtc"),
        }
    except Exception:
        return None


def haversine_km(lat1, lon1, lat2, lon2):
    """Great-circle distance between two coordinates in kilometers."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = (math.sin(dp / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2)
    return 2 * r * math.asin(math.sqrt(a))


def resolve_nearest_station(lat, lon):
    """Ask weather.com v3 point endpoint for the nearest PWS to coords.
    Returns station ID string or None on failure."""
    url = POINT_URL.format(key=_PWS_KEY, lat=lat, lon=lon)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "PwsBridge/1.0 (hermes)"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        return data.get("location", {}).get("pwsId")
    except Exception:
        return None


def build_payload(station=HOME_STATION):
    if station and station.upper() != HOME_STATION:
        data = fetch_station_current(station)
    else:
        data = fetch_pws_current()
    if not data:
        return None
    aqi = fetch_local_aqi() or {}
    gauge = fetch_river_gauge() or {}
    return {
        "temp_f": data.get("temp_f"),
        "humidity": data.get("humidity"),
        "dewpt_f": data.get("dewpt_f"),
        "pressure": data.get("pressure"),
        "wind_mph": data.get("wind_mph"),
        "wind_gust_mph": data.get("wind_gust_mph"),
        "wind_dir": data.get("wind_dir"),
        "solar_radiation": data.get("solar_radiation"),
        "uv_index": data.get("uv_index"),
        "precip_rate": data.get("precip_rate"),
        "precip_total": data.get("precip_total"),
        "today_precip_total": fetch_pws_current_precip_total(),
        "aqi": aqi.get("aqi"),
        "aqi_level": aqi.get("level"),
        "aqi_emoji": aqi.get("emoji"),
        "pm25_aqi": aqi.get("pm25_aqi"),
        "dominant": aqi.get("dominant"),
        "station": station.upper() if station else HOME_STATION,
        "location_label": "",
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "river": gauge or None,
    }


def get_payload(station=HOME_STATION):
    key = station.upper() if station else HOME_STATION
    now = time.time()
    if _cache["data"] is not None and _cache["key"] == key and (now - _cache["ts"]) < CACHE_TTL:
        return _cache["data"]
    payload = build_payload(key)
    if payload is not None:
        _cache["data"] = payload
        _cache["ts"] = now
        _cache["key"] = key
    elif _cache["data"] is not None:
        # Fresh fetch failed — serve stale cache rather than nothing
        pass
    return _cache["data"]


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)

        if path == "/health":
            self._send(200, {"ok": True})
        elif path == "/stations":
            self._send(200, {"ok": True, "stations": [HOME_STATION, *FALLBACK_STATIONS]})
        elif path == "/pws":
            station = qs.get("station", [None])[0]
            lat = qs.get("lat", [None])[0]
            lon = qs.get("lon", [None])[0]
            home_lat = qs.get("home-lat", [None])[0]
            home_lon = qs.get("home-lon", [None])[0]
            home_radius = qs.get("home-radius", [None])[0]

            if lat and lon:
                try:
                    clat, clon = float(lat), float(lon)

                    # Geofence: inside home radius -> home station
                    if home_lat and home_lon:
                        hlat, hlon = float(home_lat), float(home_lon)
                        radius = float(home_radius) if home_radius else 5.0
                        if haversine_km(clat, clon, hlat, hlon) <= radius:
                            payload = get_payload(HOME_STATION)
                            if payload is not None:
                                payload["auto_mode"] = "home-geofence"
                                self._send(200, {"ok": True, **payload})
                                return
                            # Home station unreachable — fall through to nearest

                    # Outside geofence: nearest PWS to coordinates
                    auto = resolve_nearest_station(clat, clon)
                    if auto:
                        payload = get_payload(auto)
                        if payload is not None and payload.get("temp_f") is not None:
                            payload["auto_mode"] = "nearest"
                            self._send(200, {"ok": True, **payload})
                            return
                        # Silent/empty station — fall back to home
                        station = station or HOME_STATION
                    else:
                        station = station or HOME_STATION
                except (TypeError, ValueError):
                    station = station or HOME_STATION

            payload = get_payload(station or HOME_STATION)
            if payload is None:
                self._send(503, {"error": "PWS unreachable", "ok": False})
            else:
                self._send(200, {"ok": True, **payload})
        else:
            self._send(404, {"error": "not found"})

    def log_message(self, fmt, *args):
        sys.stderr.write("[pws-bridge] %s\n" % (fmt % args))


if __name__ == "__main__":
    if not _PWS_KEY:
        print("[pws-bridge] FATAL: PWS_API_KEY env var not set — refusing to start "
              "with no Weather Underground key.", flush=True, file=sys.stderr)
        sys.exit(2)
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"[pws-bridge] listening on http://{HOST}:{PORT}", flush=True)
    srv.serve_forever()
