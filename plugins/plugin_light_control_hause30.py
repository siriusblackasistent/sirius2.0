"""Будинок 30: окремі блоки пристроїв, керування й фоновий опитувач.
Змінюйте IP/ID/ключ/канал на початку потрібної функції.
Опрос не запускається під час імпорту: його запускає сервер через lifecycle hook.
"""
from datetime import datetime
from sirius_settings import get_secret, get_settings
from midea_beautiful import appliance_state, connect_to_cloud
from pathlib import Path
import logging
import math
import os
import tempfile
import threading
import time

POLL_INTERVAL = 60
REQUEST_TIMEOUT = 3
CONFIRM_ATTEMPTS = 4
CONFIRM_DELAY = 0.2
STATUS_FILE = Path(__file__).resolve().parents[1] / "status" / "house_30.txt"
HOUSE = "30"
_OBSERVATIONS = {}
_AC_OBSERVATION = {}
_AC_LOCK = threading.Lock()
_STATE_LOCK = threading.Lock()
_DEVICE_LOCKS = {}
_FILE_LOCK = threading.Lock()
_MONITOR = None
log = logging.getLogger(__name__)

# ==================== КОМАНДИ ПЛАГІНА ====================
def start(core):
    return {
        "name": "Будинок 30: керування та опитування",
        "version": "2.2",
        "commands": {
            'опрос|опитування|опрос 30|опитування 30': request_poll,
            'статус 30|статус світла 30': show_status,
            'вимкни все світло|вимкнути все світло|барнхаус все світло вимкнути|вимкни барнхаус|вимкнути барнхаус|вимкнути всі лампочки|вимкнути світло в барнхаусі': turn_all_lights_off,
            'люстра|люстру|люста|світильник': light_lustra,
            'бахрома|бахрому|вулиця|вогник|світло на дворі': light_bahroma,
            'бойлер|бойлери|окроп': kettle,
            'тв барн|проектор': tv_barn,
            'баня|баню|банька|світло в бані': banya,
            'кухня|кухню|туалет|світло на кухні': light_kithen,
            'полив': poliv,
            'включи охолодження|увімкни охолодження|жарко|холод|кондиціонер|охолодження': turn_on_cooling,
            'включи обігрів|увімкни обігрів|увімкнути обігрів|мені холодно|кондиціонернаобігрів|кондиціонер на обігрів|обігрів': turn_on_heating,
            'кондиціонер гаси |вимикай дуйчик|вимкнути кондиціонер': turn_off_air_conditioner,
        },
        "toggle_handlers": _DEVICES,
        "scheduled_commands": {
            "вимкни все світло|вимкнути все світло": turn_all_lights_off,
            'включи охолодження|увімкни охолодження|жарко|холод|кондиціонер|охолодження': turn_on_cooling,
            'включи обігрів|увімкни обігрів|увімкнути обігрів|мені холодно|кондиціонернаобігрів|кондиціонер на обігрів|обігрів': turn_on_heating,
            'кондиціонер гаси |вимикай дуйчик|вимкнути кондиціонер': turn_off_air_conditioner,
        },
        "schedule_homes": ["0", "30"],
        "start_background": start_background,
        "stop_background": stop_background,
    }


# ==================== ЛЮСТРУ ====================
# IP, ID та ключ змінюються на початку цього блока.
def light_lustra(core=None, phrase="", action="toggle"):
    name = 'Люстра'
    ip = ''
    device_id = ''
    local_key = ''
    version = 3.3
    channel = '1'
    config = dict(name=name, ip=ip, device_id=device_id, local_key=local_key,
                  version=version, channel=channel, protocol="tuya")
    if action == "config":
        return config
    result = _command(core, phrase, action, config)
    if result is True:
        core.reply('Люстру увімкнув, мяу!')
    elif result is False:
        core.reply('Люстру вимкнув, мяу!')
    elif result == "offline":
        core.reply('Немає відповіді від пристрою «Люстру». Можливо, він не в мережі, мур.')
    elif result == "busy":
        core.reply("Зараз перевіряю цей пристрій. Повтори за кілька секунд, мур.")
    elif result == "denied":
        core.reply("Цей пристрій належить будинку 30, мур.")
    elif result == "invalid":
        core.reply("Не зрозумів дію, мур. Напиши назву пристрою, наприклад «кухня».")
    else:
        core.reply('Команда для «Люстру» не пройшла або стан не підтверджено, мур.')


# ==================== ВУЛИЧНЕ СВІТЛО ====================
# IP, ID та ключ змінюються на початку цього блока.
def light_bahroma(core=None, phrase="", action="toggle"):
    name = 'Вуличне світло'
    ip = ''
    device_id = ''
    password = ''  # Поточний Sonoff Zeroconf працює без пароля.
    port = 8081
    config = dict(name=name, ip=ip, device_id=device_id, password=password,
                  port=port, protocol="sonoff")
    if action == "config":
        return config
    result = _command(core, phrase, action, config)
    if result is True:
        core.reply('Вуличне світло увімкнув, мяу!')
    elif result is False:
        core.reply('Вуличне світло вимкнув, мяу!')
    elif result == "offline":
        core.reply('Немає відповіді від пристрою «Вуличне світло». Можливо, він не в мережі, мур.')
    elif result == "busy":
        core.reply("Зараз перевіряю цей пристрій. Повтори за кілька секунд, мур.")
    elif result == "denied":
        core.reply("Цей пристрій належить будинку 30, мур.")
    elif result == "invalid":
        core.reply("Не зрозумів дію, мур. Напиши назву пристрою, наприклад «кухня».")
    else:
        core.reply('Команда для «Вуличне світло» не пройшла або стан не підтверджено, мур.')


# ==================== БОЙЛЕРИ ====================
# IP, ID та ключ змінюються на початку цього блока.
def kettle(core=None, phrase="", action="toggle"):
    name = 'Бойлери'
    ip = ''
    device_id = ''
    password = ''  # Поточний Sonoff Zeroconf працює без пароля.
    port = 8081
    config = dict(name=name, ip=ip, device_id=device_id, password=password,
                  port=port, protocol="sonoff")
    if action == "config":
        return config
    result = _command(core, phrase, action, config)
    if result is True:
        core.reply('Бойлери увімкнув, мяу!')
    elif result is False:
        core.reply('Бойлери вимкнув, мяу!')
    elif result == "offline":
        core.reply('Немає відповіді від пристрою «Бойлери». Можливо, він не в мережі, мур.')
    elif result == "busy":
        core.reply("Зараз перевіряю цей пристрій. Повтори за кілька секунд, мур.")
    elif result == "denied":
        core.reply("Цей пристрій належить будинку 30, мур.")
    elif result == "invalid":
        core.reply("Не зрозумів дію, мур. Напиши назву пристрою, наприклад «кухня».")
    else:
        core.reply('Команда для «Бойлери» не пройшла або стан не підтверджено, мур.')


# ==================== ПРОЕКТОР ====================
# IP, ID та ключ змінюються на початку цього блока.
def tv_barn(core=None, phrase="", action="toggle"):
    name = 'Проектор'
    ip = ''
    device_id = ''
    password = ''  # Поточний Sonoff Zeroconf працює без пароля.
    port = 8081
    config = dict(name=name, ip=ip, device_id=device_id, password=password,
                  port=port, protocol="sonoff")
    if action == "config":
        return config
    result = _command(core, phrase, action, config)
    if result is True:
        core.reply('Проектор увімкнув, мяу!')
    elif result is False:
        core.reply('Проектор вимкнув, мяу!')
    elif result == "offline":
        core.reply('Немає відповіді від пристрою «Проектор». Можливо, він не в мережі, мур.')
    elif result == "busy":
        core.reply("Зараз перевіряю цей пристрій. Повтори за кілька секунд, мур.")
    elif result == "denied":
        core.reply("Цей пристрій належить будинку 30, мур.")
    elif result == "invalid":
        core.reply("Не зрозумів дію, мур. Напиши назву пристрою, наприклад «кухня».")
    else:
        core.reply('Команда для «Проектор» не пройшла або стан не підтверджено, мур.')


# ==================== СВІТЛО В БАНІ ====================
# IP, ID та ключ змінюються на початку цього блока.
def banya(core=None, phrase="", action="toggle"):
    name = 'Світло в бані'
    ip = ''
    device_id = ''
    password = ''  # Поточний Sonoff Zeroconf працює без пароля.
    port = 8081
    config = dict(name=name, ip=ip, device_id=device_id, password=password,
                  port=port, protocol="sonoff")
    if action == "config":
        return config
    result = _command(core, phrase, action, config)
    if result is True:
        core.reply('гарноі баньки господарю, мяу!')
    elif result is False:
        core.reply('Світло в бані вимкнув, мяу!')
    elif result == "offline":
        core.reply('Немає відповіді від пристрою «Світло в бані». Можливо, він не в мережі, мур.')
    elif result == "busy":
        core.reply("Зараз перевіряю цей пристрій. Повтори за кілька секунд, мур.")
    elif result == "denied":
        core.reply("Цей пристрій належить будинку 30, мур.")
    elif result == "invalid":
        core.reply("Не зрозумів дію, мур. Напиши назву пристрою, наприклад «кухня».")
    else:
        core.reply('Команда для «Світло в бані» не пройшла або стан не підтверджено, мур.')


# ==================== СВІТЛО НА КУХНІ ====================
# IP, ID та ключ змінюються на початку цього блока.
def light_kithen(core=None, phrase="", action="toggle"):
    name = 'Світло на кухні'
    ip = ''
    device_id = ''
    local_key = ''
    version = 3.3
    channel = '2'
    config = dict(name=name, ip=ip, device_id=device_id, local_key=local_key,
                  version=version, channel=channel, protocol="tuya")
    if action == "config":
        return config
    result = _command(core, phrase, action, config)
    if result is True:
        core.reply('Світло на кухні увімкнув, мяу!')
    elif result is False:
        core.reply('Світло на кухні вимкнув, мяу!')
    elif result == "offline":
        core.reply('Немає відповіді від пристрою «Світло на кухні». Можливо, він не в мережі, мур.')
    elif result == "busy":
        core.reply("Зараз перевіряю цей пристрій. Повтори за кілька секунд, мур.")
    elif result == "denied":
        core.reply("Цей пристрій належить будинку 30, мур.")
    elif result == "invalid":
        core.reply("Не зрозумів дію, мур. Напиши назву пристрою, наприклад «кухня».")
    else:
        core.reply('Команда для «Світло на кухні» не пройшла або стан не підтверджено, мур.')


# ==================== ПОЛИВ ====================
# IP, ID та ключ змінюються на початку цього блока.
def poliv(core=None, phrase="", action="toggle"):
    name = 'Полив'
    ip = ''
    device_id = ''
    local_key = ''
    version = 3.3
    channel = '101'
    config = dict(name=name, ip=ip, device_id=device_id, local_key=local_key,
                  version=version, channel=channel, protocol="tuya")
    if action == "config":
        return config
    result = _command(core, phrase, action, config)
    if result is True:
        core.reply('Полив увімкнув,буде мокренька травичка мяу!')
    elif result is False:
        core.reply('Полив вимкнув, мяу!')
    elif result == "offline":
        core.reply('Немає відповіді від пристрою «Полив». Можливо, він не в мережі, мур.')
    elif result == "busy":
        core.reply("Зараз перевіряю цей пристрій. Повтори за кілька секунд, мур.")
    elif result == "denied":
        core.reply("Цей пристрій належить будинку 30, мур.")
    elif result == "invalid":
        core.reply("Не зрозумів дію, мур. Напиши назву пристрою, наприклад «кухня».")
    else:
        core.reply('Команда для «Полив» не пройшла або стан не підтверджено, мур.')

# ==================== ОПИТУВАННЯ ТА СЛУЖБОВІ ФУНКЦІЇ ====================
# Додали новий блок? Додайте його функцію до _DEVICES, а світильник — також до _LIGHTS.

# Кондиціонер будинку 30 — Midea Cloud.
# Облікові дані читаються з розділу midea → 30 у core.py.
MIDEA_ACCOUNT = get_secret("plugin_greetings.midea_account")
MIDEA_PASSWORD = get_secret("plugin_greetings.midea_password")

def get_midea_cloud_appliances():
    """Повертає актуальний стан кондиціонерів через Midea Cloud."""
    cloud = connect_to_cloud(account=MIDEA_ACCOUNT, password=MIDEA_PASSWORD)
    appliances = []
    for details in cloud.list_appliances():
        appliances.append(
            appliance_state(
                cloud=cloud,
                use_cloud=True,
                appliance_id=str(details["id"]),
                appliance_type=details["type"],
                retries=1,
                timeout=5,
                cloud_timeout=5,
            )
        )
    return cloud, appliances


def _poll_ac():
    """Read temperatures and running state, without issuing any AC controls."""
    if not _AC_LOCK.acquire(blocking=False):
        return
    try:
        if not MIDEA_ACCOUNT or not MIDEA_PASSWORD:
            raise DeviceUnavailableError('Midea account is not configured')
        _, appliances = get_midea_cloud_appliances()
        state = next((item.state for item in appliances if hasattr(item.state, 'indoor_temperature')), None)
        if state is None:
            raise DeviceUnavailableError('No air conditioner found')
        def temperature(value):
            return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None
        with _STATE_LOCK:
            _AC_OBSERVATION.update(
                running=bool(state.running),
                indoor=temperature(state.indoor_temperature),
                outdoor=temperature(state.outdoor_temperature), error=None)
    except Exception as exc:
        with _STATE_LOCK:
            _AC_OBSERVATION['error'] = type(exc).__name__
        log.warning('House 30 AC status unavailable (%s)', type(exc).__name__)
    finally:
        _AC_LOCK.release()

def turn_on_cooling(core, phrase: str):
    if not _authorized(core):
        core.reply("Це кондиціонер будинку 30, мур.")
        return
    try:
        cloud, appliances = get_midea_cloud_appliances()
        if not appliances:
            core.reply("Не знайшов кондиціонер, мур.")
            return

        for appliance in appliances:
            appliance.set_state(
                mode=1,
                target_temperature=23.0,
                running=True,
                cloud=cloud,
            )
        core.reply("Вмикаю охолодження на 23 градуси, мур.")
    except Exception as e:
        print("AC COOLING ERROR:", e)
        core.reply("Не вдалося ввімкнути охолодження, мур.")

def turn_on_heating(core, phrase: str):
    if not _authorized(core):
        core.reply("Це кондиціонер будинку 30, мур.")
        return
    try:
        cloud, appliances = get_midea_cloud_appliances()
        if not appliances:
            core.reply("Не знайшов кондиціонер, мур.")
            return

        for appliance in appliances:
            appliance.set_state(
                mode=4,
                target_temperature=27.0,
                running=True,
                cloud=cloud,
            )
        core.reply("Вмикаю обігрів на 27 градусів, мур.")
    except Exception as e:
        print("AC HEATING ERROR:", e)
        core.reply("Не вдалося ввімкнути обігрів, мур.")

def turn_off_air_conditioner(core, phrase: str):
    if not _authorized(core):
        core.reply("Це кондиціонер будинку 30, мур.")
        return
    try:
        cloud, appliances = get_midea_cloud_appliances()

        if not appliances:
            core.reply("не знайшов кондиціонер мур")
            return

        for appliance in appliances:
            appliance.set_state(running=False, cloud=cloud)
        core.reply("Вимикаю кондиціонер, мур.")

    except Exception as e:
        print("AC ERROR:", e)
        core.reply("щось пішло не так мур")


_DEVICES = [light_lustra, light_bahroma, kettle, tv_barn, banya, light_kithen, poliv]
_LIGHTS = [light_lustra, light_bahroma, banya, light_kithen]

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
    client = None
    try:
        if config["protocol"] == "tuya":
            import tinytuya
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
            # Some Sonoff relays acknowledge before /info shows the new state.
            # Retry only the observation: never repeat a toggle/switch write.
            for attempt in range(CONFIRM_ATTEMPTS):
                current = read()
                _record(config, current)
                if current == desired:
                    break
                if attempt + 1 < CONFIRM_ATTEMPTS:
                    time.sleep(CONFIRM_DELAY)
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
        lock.release()

def _write_status(path=STATUS_FILE):
    # One complete replacement: readers never see a half-written status file.
    with _FILE_LOCK:
        with _STATE_LOCK:
            rows = {name: dict(row) for name, row in _OBSERVATIONS.items()}
            ac = dict(_AC_OBSERVATION)
        lines = ["Будинок 30"]
        for block in _DEVICES:
            name = block(action="config")["name"]
            row = rows.get(name, {})
            state = "немає даних" if "value" not in row else "увімкнено" if row["value"] else "вимкнено"
            if row.get("error"):
                state = "недоступний; останній стан: " + state
            lines.append(name + ": " + state)
        ac_status = 'немає даних' if 'running' not in ac else 'увімкнено' if ac['running'] else 'вимкнено'
        if ac.get('error'):
            ac_status = 'недоступний; останній стан: ' + ac_status
        lines.append('Кондиціонер: ' + ac_status)
        for key, label in [('indoor', 'Температура в будинку'), ('outdoor', 'Температура надворі (кондиціонер)')]:
            value = ac.get(key)
            value_text = 'немає даних' if value is None else f'{value:g} °C'
            if ac.get('error') and value is not None:
                value_text += ' (останнє отримане значення)'
            lines.append(label + ': ' + value_text)
        content = "\n".join(lines) + "\n"
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".house30-", dir=path.parent)
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
    except (DeviceUnavailableError, requests.Timeout, requests.ConnectionError, TimeoutError, ConnectionError) as exc:
        log.warning('Device unavailable: %s, action=%s (%s)', config['name'], action, type(exc).__name__)
        return "offline"
    except Exception as exc:
        log.warning('Device command failed: %s, action=%s (%s)', config['name'], action, type(exc).__name__)
        return "failed"
    finally:
        try:
            _write_status()
        except OSError:
            log.warning("Could not write house 30 status")


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
        self.thread = threading.Thread(target=self.run, name="house30-poller", daemon=True)

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
            if not self.stop_event.is_set():
                _poll_ac()
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
                log.warning("House 30 polling cycle failed (%s)", type(exc).__name__)
                result = "Не вдалося записати результати опитування будинку 30."
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
        core.reply("Опитування доступне для будинків 0 і 30.")
        return
    start_background(core)
    _MONITOR.request(core.session)
    core.reply("Опитування будинку 30 запущено у фоні. Результат надійде після завершення.")

def show_status(core, phrase):
    if not _authorized(core):
        core.reply("У цьому чаті недоступний статус будинку 30.")
        return
    try:
        core.reply(STATUS_FILE.read_text(encoding="utf-8"))
    except OSError:
        core.reply("Ще немає статусів будинку 30. Напиши «опрос».")

def turn_all_lights_off(core, phrase):
    for block in _LIGHTS:
        block(core, phrase, "off")
