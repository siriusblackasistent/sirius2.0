"""House-scoped lighting menus and explicit actions, shared by three house plugins."""
from concurrent.futures import ThreadPoolExecutor
import re
from threading import Lock
from sirius_settings import BASE_DIR, load_json

_LOCKS = {}
_LOCKS_GUARD = Lock()


def house_manifest(core, home, lights):
    if not hasattr(core, "light_houses"):
        core.light_houses = {}
    core.light_houses[home] = lights
    return {"name": "Світло — будинок " + home, "version": "1.0", "commands": {}}


def registry(home):
    if home not in {"0", "30", "70"}:
        raise ValueError("Unknown house")
    return load_json(BASE_DIR / "devices" / ("house_" + home + ".local.json")).get("lights", {})


def normalize(text):
    return " ".join(text.casefold().strip().split())


def permitted(core, home):
    source = str(core.session.metadata.get("home", "0"))
    return source == "0" or source == home


def panel(core, home, device="all"):
    lights = core.light_houses.get(home, {})
    configured = registry(home)
    items = [{"id": key, "name": item["name"], "configured": key in configured}
             for key, item in lights.items()]
    core.session.lighting = {"home": home, "device": device, "items": items,
                             "can_choose_house": str(core.session.metadata.get("home", "0")) == "0"}


def _read_tuya(device, dps):
    result = device.status()
    value = result.get("dps", {}).get(dps) if isinstance(result, dict) else None
    if not isinstance(value, bool):
        raise ValueError("No boolean channel state")
    return value


def _sonoff_request(config, method, data):
    import requests
    response = requests.post("http://" + config["ip"] + ":8081/zeroconf/" + method,
                             json={"deviceid": config["device_id"], "data": data}, timeout=3)
    response.raise_for_status()
    result = response.json()
    if result.get("error") != 0:
        raise ValueError("Sonoff rejected request")
    return result


def _read_sonoff(config):
    state = _sonoff_request(config, "info", {}).get("data", {}).get("switch")
    if state not in {"on", "off"}:
        raise ValueError("Missing switch state")
    return state == "on"


def switch(config, action):
    """Return (observed state, verified). Unknown states never imply 'off'."""
    if action not in {"on", "off", "status", "toggle"}:
        raise ValueError("Unknown action")
    device = gateway = None
    try:
        if config["protocol"] == "tuya":
            import tinytuya
            if config.get("cid"):
                gateway = tinytuya.Device(config["device_id"], address=config.get("ip"),
                    local_key=config["local_key"], persist=True, version=config.get("version", 3.3))
                gateway.set_socketTimeout(3)
                gateway.set_socketRetryLimit(1)
                device = tinytuya.OutletDevice(config["device_id"], cid=config["cid"], parent=gateway)
            else:
                device = tinytuya.OutletDevice(config["device_id"], config["ip"], config["local_key"])
                device.set_version(config.get("version", 3.3))
            device.set_socketTimeout(3)
            device.set_socketRetryLimit(1)
            dps = str(config.get("dps", "1"))
            read = lambda: _read_tuya(device, dps)
            def write(value):
                result = device.set_status(value, switch=dps)
                if isinstance(result, dict) and ("Error" in result or "Err" in result):
                    raise ValueError("Tuya rejected request")
        elif config["protocol"] == "sonoff":
            read = lambda: _read_sonoff(config)
            write = lambda value: _sonoff_request(config, "switch", {"switch": "on" if value else "off"})
        else:
            raise ValueError("Unsupported light protocol")
        before = read()
        if action == "status":
            return before, True
        desired = not before if action == "toggle" else action == "on"
        if desired == before:
            return before, True
        write(desired)
        try:
            actual = read()
            return actual, actual == desired
        except Exception:
            return None, False
    finally:
        for item in (device, gateway):
            if item is not None and hasattr(item, "close"):
                try:
                    item.close()
                except Exception:
                    pass


def control(home, ident, action, name):
    config = registry(home).get(ident)
    if not config:
        return name + ": ще не налаштовано."
    key = config.get("device_id", ident)
    with _LOCKS_GUARD:
        lock = _LOCKS.setdefault(key, Lock())
    try:
        with lock:
            state, verified = switch(config, action)
        if not verified:
            return name + ": команду надіслано, але потрібний стан ще не підтверджено."
        return name + (": увімкнено." if state else ": вимкнено.")
    except Exception:
        return name + ": не вдалося отримати відповідь пристрою. Перевір живлення та зв’язок."


def handle(core, home, ident, action):
    if not permitted(core, home):
        core.reply("У цьому чаті доступний тільки будинок " + str(core.session.metadata.get("home")) + ".")
        return
    lights = core.light_houses.get(home, {})
    if ident != "all" and ident not in lights:
        core.reply("Не знайшов цей вимикач.")
        return
    if action == "menu":
        if ident == "all":
            core.reply("Світло · Будинок " + home + ("\nОберіть кімнату або вимкніть усе світло." if lights else "\nВимикачі ще не налаштовані."))
        else:
            core.reply(lights[ident]["name"] + " · Будинок " + home + "\nОберіть дію.")
    elif ident == "all":
        if action != "off":
            core.reply("Для всього будинку доступна команда «вимкни все світло».")
            return
        configured = registry(home)
        items = [(key, data) for key, data in lights.items() if key in configured]
        if not items:
            core.reply("У будинку " + home + " ще немає налаштованих вимикачів.")
        else:
            with ThreadPoolExecutor(max_workers=3) as executor:
                results = list(executor.map(lambda item: control(home, item[0], "off", item[1]["name"]), items))
            core.reply("Світло · Будинок " + home + "\n" + "\n".join(results))
    else:
        core.reply(control(home, ident, action, lights[ident]["name"]))
    panel(core, home, ident)


VERBS = {
    "увімкни": "on", "увімкнути": "on", "включи": "on", "включити": "on",
    "вимкни": "off", "вимкнути": "off", "виключи": "off", "виключити": "off",
    "статус": "status", "перевір": "status", "перемкни": "toggle", "перемкнути": "toggle",
}


def try_handle(core, phrase):
    if not getattr(core, "light_houses", None):
        return False
    text = normalize(phrase)
    home = str(core.session.metadata.get("home", "0"))
    prefix = re.match(r"^(0|30|70)\s+(.+)$", text)
    suffix = re.fullmatch(r"(.+?)\s+(?:в|у) будинку (0|30|70)", text)
    if prefix:
        home, text = prefix.groups()
    elif suffix:
        text, home = suffix.groups()
    if text in {"світло", "освітлення", "💡 світло", "/lights"}:
        handle(core, home, "all", "menu")
        return True
    if text in {"вимкни все світло", "вимкнути все світло", "барнхаус все світло вимкнути", "вимкни барнхаус", "вимкнути барнхаус", "вимкнути всі лампочки", "вимкнути світло в барнхаусі"}:
        handle(core, home, "all", "off")
        return True
    action = "menu"
    first, _, rest = text.partition(" ")
    if first in VERBS:
        action, text = VERBS[first], rest
    lights = core.light_houses.get(home, {})
    for ident, item in lights.items():
        if text in [normalize(alias) for alias in item["aliases"]]:
            handle(core, home, ident, action)
            return True
    # Recognizable lighting requests must not fall through to GPT/device toggles.
    known = {normalize(alias) for group in core.light_houses.values() for item in group.values() for alias in item["aliases"]}
    if text in known:
        core.reply("У будинку " + home + " цей вимикач не налаштований. Напиши «світло», щоб побачити доступні.")
        return True
    if first in VERBS and ("світло" in text or any(text.startswith(alias + " ") for alias in known)):
        core.reply("Уточни кімнату й одну дію. Наприклад: «увімкни кухню». Або відкрий «світло».")
        return True
    return False
