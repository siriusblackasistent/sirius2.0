"""Будинок 0: окремі блоки пристроїв, керування й фоновий опитувач.
Змінюйте IP/ID/ключ/канал на початку потрібної функції.
Опрос не запускається під час імпорту: його запускає сервер через lifecycle hook.
"""
from datetime import datetime
from sirius_settings import get_settings
from pathlib import Path
import logging
import os
import tempfile
import threading
import time

POLL_INTERVAL = 60
REQUEST_TIMEOUT = 3
STATUS_FILE = Path(__file__).resolve().parents[1] / "status" / "house_0.txt"
HOUSE = "0"
_OBSERVATIONS = {}
_STATE_LOCK = threading.Lock()
_DEVICE_LOCKS = {}
_FILE_LOCK = threading.Lock()
_MONITOR = None
log = logging.getLogger(__name__)

# ==================== КОМАНДИ ПЛАГІНА ====================
def start(core):
    return {
        "name": "Будинок 0: керування та опитування",
        "version": "2.2",
        "commands": {
            "будиночок|домик|світло в будиночку": light_domic,
            "бра|1": zigbee_bra,
            "опрос 0|опитування 0": request_poll,
            "статус 0|статус світла 0": show_status,
            "вимкни все світло 0|вимкнути все світло 0": turn_all_lights_off,
        },
        "toggle_handlers": _DEVICES,
        "scheduled_commands": {"вимкни все світло 0|вимкнути все світло 0": turn_all_lights_off},
        "schedule_homes": ["0"],
        "start_background": start_background,
        "stop_background": stop_background,
    }






# ==================== СВІТЛО В БУДИНОЧКУ ====================
def light_domic(core=None, phrase="", action="toggle"):
    name = 'Світло в будиночку'
    ip = ''
    device_id = ''
    local_key = ''
    version = 3.3
    channel = "1"
    config = dict(name=name, ip=ip, device_id=device_id, local_key=local_key,
                  version=version, channel=channel, protocol="tuya")
    if action == "config":
        return config
    result = _command(core, phrase, action, config)
    if result is True:
        core.reply('Світло в будиночку увімкнув, мяу!')
    elif result is False:
        core.reply('Світло в будиночку вимкнув, мяу!')
    elif result == "offline":
        core.reply('Немає відповіді від пристрою «Світло в будиночку». Можливо, він не в мережі, мур.')
    elif result == "busy":
        core.reply("Зараз перевіряю цей пристрій. Повтори за кілька секунд, мур.")
    elif result == "denied":
        core.reply("Цей пристрій належить будинку 0, мур.")
    elif result == "invalid":
        core.reply("Не зрозумів дію, мур. Напиши назву пристрою, наприклад «кухня».")
    else:
        core.reply('Команда для «Світло в будиночку» не пройшла або стан не підтверджено, мур.')


# ==================== БРА ====================
def zigbee_bra(core=None, phrase="", action="toggle"):
    name = 'Бра'
    ip = None  # IP шлюзу; None зберігає попередній автоматичний пошук.
    device_id = ''
    local_key = ''
    child_id = 'a4c13862d8c3d5f5'
    version = 3.3
    channel = "1"
    config = dict(name=name, ip=ip, device_id=device_id, local_key=local_key,
                  cid=child_id, version=version, channel=channel, protocol="tuya")
    if action == "config":
        return config
    result = _command(core, phrase, action, config)
    if result is True:
        core.reply('Бра увімкнув, мяу!')
    elif result is False:
        core.reply('Бра вимкнув, мяу!')
    elif result == "offline":
        core.reply('Немає відповіді від пристрою «Бра». Можливо, він не в мережі, мур.')
    elif result == "busy":
        core.reply("Зараз перевіряю цей пристрій. Повтори за кілька секунд, мур.")
    elif result == "denied":
        core.reply("Цей пристрій належить будинку 0, мур.")
    elif result == "invalid":
        core.reply("Не зрозумів дію, мур. Напиши назву пристрою, наприклад «кухня».")
    else:
        core.reply('Команда для «Бра» не пройшла або стан не підтверджено, мур.')

# ==================== ОПИТУВАННЯ ТА СЛУЖБОВІ ФУНКЦІЇ ====================
# Додали новий блок? Додайте його функцію до _DEVICES, а світильник — також до _LIGHTS.
_DEVICES = [light_domic, zigbee_bra]
_LIGHTS = [light_domic, zigbee_bra]

class DeviceUnavailableError(Exception):
    pass

def _authorized(core):
    return str(core.session.metadata.get("home", "0")) in {"0", HOUSE}

def _lock_for(config):
    with _STATE_LOCK:
        return _DEVICE_LOCKS.setdefault(config["device_id"], threading.Lock())

def _record(config, value=None, error=None):
    with _STATE_LOCK:
        row = _OBSERVATIONS.setdefault(config["name"], {})
        row["checked"] = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
        row["error"] = error
        if value is not None:
            row["value"] = value
            row["last_ok"] = row["checked"]

def _operate(config, action):
    """All network calls have finite timeouts; a shared relay is never polled concurrently."""
    if action not in {"status", "on", "off", "toggle"}:
        raise ValueError("Unknown action")
    lock = _lock_for(config)
    if not lock.acquire(blocking=False):
        raise BlockingIOError("Device is busy")
    client = gateway = None
    try:
        if config["protocol"] == "tuya":
            import tinytuya
            if config.get("cid"):
                gateway = tinytuya.Device(config["device_id"], address=config["ip"], local_key=config["local_key"], persist=True, version=config["version"])
                gateway.set_socketTimeout(REQUEST_TIMEOUT)
                gateway.set_socketRetryLimit(1)
                client = tinytuya.OutletDevice(config["device_id"], cid=config["cid"], parent=gateway)
            else:
                client = tinytuya.OutletDevice(config["device_id"], config["ip"], config["local_key"])
                client.set_version(config["version"])
            client.set_socketTimeout(REQUEST_TIMEOUT)
            client.set_socketRetryLimit(1)
            def read():
                payload = client.status()
                state = payload.get("dps", {}).get(config["channel"]) if isinstance(payload, dict) else None
                if not isinstance(state, bool):
                    raise DeviceUnavailableError("Missing channel state")
                return state
            def write(value):
                payload = client.set_status(value, switch=config["channel"])
                if isinstance(payload, dict) and ("Error" in payload or "Err" in payload):
                    raise ValueError("Device rejected command")
        else:
            import requests
            if config["password"]:
                raise ValueError("Password authentication is not supported by this Zeroconf transport")
            def request(method, data):
                response = requests.post(f"http://{config['ip']}:{config['port']}/zeroconf/{method}",
                    json={"deviceid": config["device_id"], "data": data}, timeout=REQUEST_TIMEOUT)
                response.raise_for_status()
                payload = response.json()
                if payload.get("error") != 0:
                    raise ValueError("Sonoff rejected request")
                return payload
            def read():
                state = request("info", {}).get("data", {}).get("switch")
                if state not in {"on", "off"}:
                    raise DeviceUnavailableError("Missing switch state")
                return state == "on"
            def write(value):
                request("switch", {"switch": "on" if value else "off"})
        current = read()
        _record(config, current)
        if action == "status":
            return current
        desired = not current if action == "toggle" else action == "on"
        if current != desired:
            write(desired)
            current = read()
            _record(config, current)
            if current != desired:
                raise ValueError("Requested state was not confirmed")
        return current
    except Exception as exc:
        _record(config, error=type(exc).__name__)
        raise
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass
        if gateway is not None:
            try:
                gateway.close()
            except Exception:
                pass
        lock.release()

def _write_status(path=STATUS_FILE):
    # One complete replacement: readers never see a half-written status file.
    with _FILE_LOCK:
        with _STATE_LOCK:
            rows = {name: dict(row) for name, row in _OBSERVATIONS.items()}
        lines = ["Будинок 0"]
        for block in _DEVICES:
            name = block(action="config")["name"]
            row = rows.get(name, {})
            state = "немає даних" if "value" not in row else "увімкнено" if row["value"] else "вимкнено"
            if row.get("error"):
                state = "недоступний; останній стан: " + state
            lines.append(name + ": " + state)
        content = "\n".join(lines) + "\n"
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".house0-", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(content)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return content

def _command(core, phrase, action, config):
    import requests
    if not _authorized(core):
        return "denied"
    if phrase.strip():
        return "invalid"
    try:
        return _operate(config, action)
    except BlockingIOError:
        return "busy"
    except (DeviceUnavailableError, requests.Timeout, requests.ConnectionError, TimeoutError, ConnectionError):
        return "offline"
    except Exception:
        return "failed"
    finally:
        try:
            _write_status()
        except OSError:
            log.warning("Could not write house 0 status")


class Poller:
    def __init__(self, core, path=STATUS_FILE):
        self.core = core
        self.path = path
        self.stop_event = threading.Event()
        self.wake = threading.Event()
        self.cycle_lock = threading.Lock()
        self.request_lock = threading.Lock()
        self.requesters = []
        self.busy = False
        self.thread = threading.Thread(target=self.run, name="house0-poller", daemon=True)

    def start(self):
        self.wake.set()
        self.thread.start()

    def request(self, session):
        with self.request_lock:
            if not any(item is session for item in self.requesters):
                self.requesters.append(session)
            if not self.busy:
                self.wake.set()

    def poll_once(self):
        if not self.cycle_lock.acquire(blocking=False):
            return None
        try:
            for block in _DEVICES:
                if self.stop_event.is_set():
                    break
                config = block(action="config")
                try:
                    _operate(config, "status")
                except BlockingIOError:
                    pass
                except Exception as exc:
                    # One failed device cannot interrupt the remaining checks.
                    _record(config, error=type(exc).__name__)
            return _write_status(self.path)
        finally:
            self.cycle_lock.release()

    def run(self):
        while not self.stop_event.is_set():
            self.wake.wait(POLL_INTERVAL)
            if self.stop_event.is_set():
                break
            with self.request_lock:
                self.wake.clear()
                self.busy = True
            try:
                result = self.poll_once()
            except Exception as exc:
                log.warning("House 0 polling cycle failed (%s)", type(exc).__name__)
                result = "Не вдалося записати результати опитування будинку 0."
            finally:
                with self.request_lock:
                    requesters, self.requesters = self.requesters, []
                    self.busy = False
            if requesters and result:
                with self.core._lock:
                    for session in requesters:
                        session.outbox.append(result)

    def stop(self):
        self.stop_event.set()
        self.wake.set()
        self.thread.join(timeout=REQUEST_TIMEOUT * 2 + 1)

def start_background(core):
    global _MONITOR
    if not get_settings("services").get("status", True):
        return
    if _MONITOR is None or not _MONITOR.thread.is_alive():
        _MONITOR = Poller(core)
        _MONITOR.start()

def stop_background():
    if _MONITOR is not None:
        _MONITOR.stop()

def request_poll(core, phrase):
    if not get_settings("services").get("status", True):
        core.reply("Фоновий статус вимкнено в core.py: services.status = False.")
        return
    if not _authorized(core):
        core.reply("Опитування доступне для будинків 0 і 0.")
        return
    start_background(core)
    _MONITOR.request(core.session)
    core.reply("Опитування будинку 0 запущено у фоні. Результат надійде після завершення.")

def show_status(core, phrase):
    if not _authorized(core):
        core.reply("У цьому чаті недоступний статус будинку 0.")
        return
    try:
        core.reply(STATUS_FILE.read_text(encoding="utf-8"))
    except OSError:
        core.reply("Ще немає статусів будинку 0. Напиши «опрос».")

def turn_all_lights_off(core, phrase):
    for block in _LIGHTS:
        block(core, phrase, "off")
