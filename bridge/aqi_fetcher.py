#!/usr/bin/env python3
"""Fetch AQI for the configured city from aqicn.org (no auth required)."""
import os
import urllib.request
import re
import json

AQI_CITY_URL = os.environ.get(
    "PWS_AQI_URL", "https://aqicn.org/city/"  # set full city path per install
)


def fetch_local_aqi():
    """Scrape AQI from aqicn.org for the configured city URL.

    Returns dict with:
        aqi: int - AQI value
        level: str - category (Good, Moderate, etc.)
        pm25: float - PM2.5 in µg/m³
        dominant: str - dominant pollutant
    """
    url = os.environ.get("PWS_AQI_URL", "") or AQI_CITY_URL
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        return None
    
    result = {}
    
    # Extract overall AQI value
    m = re.search(r'overall air quality index is (\d+)', html)
    if m:
        result["aqi"] = int(m.group(1))
    
    # Extract PM2.5
    m = re.search(r'PM.*?2\.5.*?AQI is (\d+)', html)
    if m:
        result["pm25_aqi"] = int(m.group(1))
    
    # Extract PM2.5 concentration
    m = re.search(r'PM25.*?(\d+\.?\d*)\s*µg/m³', html)
    if m:
        result["pm25_ugm3"] = float(m.group(1))
    
    # Determine category
    aqi = result.get("aqi", 0)
    if aqi <= 50:
        result["level"] = "Good"
        result["emoji"] = "🟢"
    elif aqi <= 100:
        result["level"] = "Moderate"
        result["emoji"] = "🟡"
    elif aqi <= 150:
        result["level"] = "Unhealthy for Sensitive"
        result["emoji"] = "🟠"
    elif aqi <= 200:
        result["level"] = "Unhealthy"
        result["emoji"] = "🔴"
    elif aqi <= 300:
        result["level"] = "Very Unhealthy"
        result["emoji"] = "🟣"
    else:
        result["level"] = "Hazardous"
        result["emoji"] = "⚫"
    
    result["dominant"] = "PM2.5"
    
    return result


if __name__ == "__main__":
    aqi_data = fetch_local_aqi()
    if aqi_data:
        print(json.dumps(aqi_data, indent=2))
    else:
        print("Could not fetch AQI data")
