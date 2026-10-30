"""Погода через Open-Meteo (https://open-meteo.com/)."""

import os
from sirius_settings import get_settings
from typing import Optional

import requests

from sirius_core import SiriusCore

modname = os.path.basename(__file__)[:-3]

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
HEADERS = {
    "User-Agent": "SiriusCatWeather/1.0 (contact: sirius@example.com)",
}

WEATHER_CODES = {
    0: "ясно",
    1: "переважно ясно",
    2: "мінлива хмарність",
    3: "похмуро",
    45: "туман",
    48: "туман з памороззю",
    51: "слабка мжичка",
    53: "мжичка",
    55: "сильна мжичка",
    56: "паморозь",
    57: "інтенсивна паморозь",
    61: "слабкий дощ",
    63: "дощ",
    65: "сильний дощ",
    66: "крижаний дощ",
    67: "сильний крижаний дощ",
    71: "слабкий сніг",
    73: "сніг",
    75: "сильний сніг",
    77: "зледенілий сніг",
    80: "короткочасні зливи",
    81: "зливи",
    82: "сильні зливи",
    85: "снігопад",
    86: "сильний снігопад",
    95: "гроза",
    96: "гроза з градом",
    99: "сильна гроза з градом",
}


def start(core: SiriusCore):
    manifest = {
        "name": "Погода (Open-Meteo)",
        "version": "3.0",
        "require_online": True,
        "description": (
            "Поточна погода та прогноз через Open-Meteo.\n"
            "Команди: погода, погода завтра, погода післязавтра, прогноз погоди.\n"
            "Працює за координатами lat/lon у налаштуваннях."
        ),
        "default_options": {
            "lat": 50.4501,
            "lon": 30.5234,
        },
        "commands": {
            "погода": (run_weather, ""),
            "прогноз погоды|прогноз погода": (run_weather, "prognoz"),
        },
    }
    return manifest


def start_with_options(core: SiriusCore, manifest: dict):
    options = manifest["options"]
    options.update(get_settings("weather"))
    if "lat" not in options or "lon" not in options:
        options["lat"] = 50.4501
        options["lon"] = 30.5234
        core.save_plugin_options(modname, options)
    return manifest


def fetch_forecast(lat: float, lon: float) -> dict:
    params = {
        "latitude": lat,
        "longitude": lon,
        "current_weather": True,
        "hourly": "temperature_2m,relative_humidity_2m,wind_speed_10m",
        "daily": "weathercode,temperature_2m_max,temperature_2m_min",
        "timezone": "auto",
    }
    resp = requests.get(OPEN_METEO_URL, params=params, headers=HEADERS, timeout=8)
    resp.raise_for_status()
    return resp.json()


def weather_code_text(code: Optional[int]) -> str:
    if code is None:
        return "невідомо"
    return WEATHER_CODES.get(code, "незрозуміла погода")


def describe_current(data: dict) -> str:
    current = data.get("current_weather") or {}
    if not current:
        return "Не можу отримати поточні дані."

    time_str = current.get("time", "")
    temp = current.get("temperature")
    wind = current.get("windspeed")
    code = current.get("weathercode")

    humidity = None
    hourly = data.get("hourly") or {}
    hourly_times = hourly.get("time") or []
    if time_str in hourly_times:
        idx = hourly_times.index(time_str)
        rh = hourly.get("relative_humidity_2m") or []
        if idx < len(rh):
            humidity = rh[idx]

    parts = [
        f"Зараз {weather_code_text(code)}.",
        f"Температура {round(temp)}°C." if temp is not None else "",
        f"Вітер {round(wind)} км/год." if wind is not None else "",
        f"Вологість {int(humidity)}%." if humidity is not None else "",
    ]
    return " ".join(p for p in parts if p)


def describe_day(data: dict, index: int, label: str):
    daily = data.get("daily") or {}
    temps_max = daily.get("temperature_2m_max") or []
    temps_min = daily.get("temperature_2m_min") or []
    codes = daily.get("weathercode") or []

    if index >= len(temps_max) or index >= len(temps_min) or index >= len(codes):
        return None

    desc = weather_code_text(codes[index])
    tmax = round(temps_max[index])
    tmin = round(temps_min[index])
    return f"{label}: {desc}. Вдень до {tmax}°C, вночі близько {tmin}°C."


def run_weather(core: SiriusCore, phrase: str, addparam: str):
    options = core.plugin_options(modname)
    lat = options.get("lat")
    lon = options.get("lon")

    if lat is None or lon is None:
        core.reply("Не вказані координати для прогнозу.")
        return

    try:
        data = fetch_forecast(lat, lon)
    except Exception as e:
        print("Open-Meteo error:", e)
        core.reply("Не можу отримати дані погоди, перевір інтернет.")
        return

    phrase_norm = (phrase or "").lower().strip()

    if addparam == "prognoz" or phrase_norm == "прогноз":
        parts = []
        for label, idx in (("Сьогодні", 0), ("Завтра", 1), ("Післязавтра", 2)):
            text = describe_day(data, idx, label)
            if text:
                parts.append(text)
        if not parts:
            parts.append(describe_current(data))
        core.reply(" ".join(parts))
        return

    if phrase_norm in ("", "зараз"):
        core.reply(describe_current(data))
        return

    if "зав" in phrase_norm:
        text = describe_day(data, 1, "Прогноз на завтра")
        if text:
            core.reply(text)
        else:
            core.reply(describe_current(data))
        return

    if "після" in phrase_norm or "после" in phrase_norm:
        text = describe_day(data, 2, "Прогноз на післязавтра")
        if text:
            core.reply(text)
        else:
            core.reply(describe_current(data))
        return

    core.reply(describe_current(data))
