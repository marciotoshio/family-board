"""Current weather + short forecast from Open-Meteo (no API key required).

Results are cached in memory and refreshed by the background scheduler; the
API endpoints only ever read the cache. Location defaults to Mississauga and
can be overridden with WEATHER_LAT / WEATHER_LON / WEATHER_PLACE.
"""
import os
import time
import threading

import httpx

_LOCK = threading.Lock()
_cache = {"data": None, "error": None, "updated": 0.0}

# WMO weather interpretation codes -> (icon, short label)
_WMO = {
    0: ("☀️", "Clear"),
    1: ("🌤️", "Mainly clear"),
    2: ("⛅", "Partly cloudy"),
    3: ("☁️", "Overcast"),
    45: ("🌫️", "Fog"),
    48: ("🌫️", "Fog"),
    51: ("🌦️", "Light drizzle"),
    53: ("🌦️", "Drizzle"),
    55: ("🌧️", "Drizzle"),
    56: ("🌧️", "Freezing drizzle"),
    57: ("🌧️", "Freezing drizzle"),
    61: ("🌦️", "Light rain"),
    63: ("🌧️", "Rain"),
    65: ("🌧️", "Heavy rain"),
    66: ("🌧️", "Freezing rain"),
    67: ("🌧️", "Freezing rain"),
    71: ("🌨️", "Light snow"),
    73: ("🌨️", "Snow"),
    75: ("❄️", "Heavy snow"),
    77: ("❄️", "Snow grains"),
    80: ("🌦️", "Showers"),
    81: ("🌧️", "Showers"),
    82: ("⛈️", "Heavy showers"),
    85: ("🌨️", "Snow showers"),
    86: ("🌨️", "Snow showers"),
    95: ("⛈️", "Thunderstorm"),
    96: ("⛈️", "Thunderstorm"),
    99: ("⛈️", "Thunderstorm"),
}


def _describe(code) -> tuple:
    return _WMO.get(code, ("🌡️", "—"))


def _fetch() -> dict:
    resp = httpx.get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": os.environ.get("WEATHER_LAT", "43.5890"),
            "longitude": os.environ.get("WEATHER_LON", "-79.6441"),
            "current": "temperature_2m,apparent_temperature,relative_humidity_2m,"
                       "weather_code,wind_speed_10m",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min",
            "timezone": "auto",
            "forecast_days": "4",
        },
        timeout=10.0,
    )
    resp.raise_for_status()
    j = resp.json()
    cur, daily = j["current"], j["daily"]
    icon, label = _describe(cur["weather_code"])

    days = []
    for i, day in enumerate(daily["time"]):
        di, dl = _describe(daily["weather_code"][i])
        days.append({
            "date": day,
            "icon": di,
            "label": dl,
            "max": round(daily["temperature_2m_max"][i]),
            "min": round(daily["temperature_2m_min"][i]),
        })

    return {
        "place": os.environ.get("WEATHER_PLACE", "Mississauga"),
        "temp": round(cur["temperature_2m"]),
        "feels": round(cur["apparent_temperature"]),
        "humidity": cur["relative_humidity_2m"],
        "wind": round(cur["wind_speed_10m"]),
        "icon": icon,
        "label": label,
        "days": days,
        "updated": time.time(),
    }


def refresh():
    """Fetch fresh weather; keep the previous cache on failure."""
    try:
        data = _fetch()
        with _LOCK:
            _cache.update(data=data, error=None, updated=time.time())
    except Exception as e:
        with _LOCK:
            _cache.update(error=f"{type(e).__name__}: {e}")


def get() -> dict:
    """Flattened weather payload for the API (data fields + an 'error' key)."""
    with _LOCK:
        data = dict(_cache["data"]) if _cache["data"] else {}
        data["error"] = _cache["error"]
        return data
