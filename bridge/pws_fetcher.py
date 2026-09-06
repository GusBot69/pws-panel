#!/usr/bin/env python3
"""PWS rain source — pulls daily precipitation from the configured primary station.

Env-overridable so a public install isn't pinned to any one town:
  PWS_HOME_STATION  (primary, full sensor suite)
  PWS_WIND_STATION  (fallback with anemometer)
Wunderground PWS API — public key, no registration needed."""

import json
import os
import time
import urllib.request
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Chicago")
_PWS_STATION = os.environ.get("PWS_HOME_STATION", "PWS-LOCAL")  # PRIMARY — full sensor suite.
_WIND_STATION = os.environ.get("PWS_WIND_STATION", "PWS-LOCAL-2")  # FALLBACK — used only if primary is unreachable.
_PWS_KEY = os.environ.get("PWS_API_KEY", "")  # REQUIRED — no embedded default (public repo)
_DAILY_URL_TEMPLATE = "https://api.weather.com/v2/pws/history/daily?stationId={station}&format=json&units=e&date={date}&apiKey={key}"
_CURRENT_URL_TEMPLATE = "https://api.weather.com/v2/pws/observations/current?stationId={station}&format=json&units=e&apiKey={key}"


def fetch_pws_daily_precip(date=None):
    """Fetch daily precip total from the primary station. Returns float inches or None on failure."""
    if date is None:
        yesterday = datetime.now(TZ) - timedelta(days=1)
        date = yesterday.strftime("%Y%m%d")
    
    url = _DAILY_URL_TEMPLATE.format(station=_PWS_STATION, date=date, key=_PWS_KEY)
    for attempt in range(2):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "YewBot/2.0 (yew@hermes.local)"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())
            observations = data.get("observations", [])
            if observations:
                imperial = observations[0].get("imperial", {})
                val = imperial.get("precipTotal")
                if val is not None:
                    return float(val)
            # FIX 2026-08-01: empty observations ≠ "no rain". Return None so
            # silent_update's reconciliation knows the API gave no data and
            # can retry later instead of permanently zeroing the day.
            return None
        except Exception:
            if attempt < 1:
                time.sleep(2)
    return None


def _fetch_wind():
    """Fetch wind, solar, and UV data from the fallback station.

    NOTE: fallback station precip may be unreliable depending on hardware.
    Only use this for wind/solar/UV fields."""
    url = _CURRENT_URL_TEMPLATE.format(station=_WIND_STATION, key=_PWS_KEY)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "YewBot/2.0 (yew@hermes.local)"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        observations = data.get("observations", [])
        if not observations:
            return {}
        obs = observations[0]
        imperial = obs.get("imperial", {})
        return {
            "wind_mph": imperial.get("windspeedAvg", 0),
            "wind_gust_mph": imperial.get("windgustHigh", 0),
            "wind_dir": obs.get("winddir"),
            "solar_radiation": obs.get("solarRadiation"),
            "uv_index": obs.get("uv"),
            # Precip — fallback station's rain gauge may be unreliable, so the
            # caller pulls precip from the primary station instead.
            "precip_rate": imperial.get("precipRate"),
            "precip_total": imperial.get("precipTotal"),
        }
    except Exception:
        return {}


def fetch_pws_current():
    """Fetch current conditions from the fallback station + wind from the primary. Returns dict or None."""
    url = _CURRENT_URL_TEMPLATE.format(station=_PWS_STATION, key=_PWS_KEY)
    result = None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "YewBot/2.0 (yew@hermes.local)"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        observations = data.get("observations", [])
        if not observations:
            return None
        obs = observations[0]
        imperial = obs.get("imperial", {})
        result = {
            "temp_f": imperial.get("temp"),
            "humidity": obs.get("humidity"),
            "precip_rate": imperial.get("precipRate"),
            "precip_total": imperial.get("precipTotal"),
            "pressure": imperial.get("pressure"),
            "dewpt_f": imperial.get("dewpt"),
            "wind_mph": imperial.get("windSpeed"),
        }
    except Exception:
        return None

    # Primary station may lack an anemometer; the fallback station fills in
    # wind/solar/UV fields the primary didn't return.
    # ⚠️ Never fill precip from the fallback — verify its rain gauge works first.
    try:
        fallback = _fetch_wind()
        if fallback:
            for key in ("wind_mph", "wind_gust_mph", "wind_dir", "solar_radiation", "uv_index"):
                if result.get(key) is None and fallback.get(key) is not None:
                    result[key] = fallback[key]
    except Exception:
        pass

    return result


def fetch_pws_current_precip_total():
    """
    Fetch today's precipitation accumulation from current conditions endpoint.
    Returns float inches or None on failure.
    
    This resets at midnight local time, so it represents 'rain since midnight today.'
    """
    current = fetch_pws_current()
    if current is None:
        return None
    pt = current.get("precip_total")
    if pt is None:
        return None
    return float(pt)


if __name__ == "__main__":
    # Test both sources
    print(f"Daily history yesterday: {fetch_pws_daily_precip()}")
    print(f"Current conditions today: {fetch_pws_current_precip_total()}")
    print()
    today_str = datetime.now(TZ).strftime("%Y%m%d")
    print(f"Daily history for today: {fetch_pws_daily_precip(today_str)}")
