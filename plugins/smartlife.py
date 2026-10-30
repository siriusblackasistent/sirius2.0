"""Smart Life QR onboarding and account-scoped inventory; no device writes."""
import base64
import hashlib
from io import BytesIO
import json
import logging
import os
from pathlib import Path
import re
import tempfile
import time

from sirius_settings import get_settings

log = logging.getLogger(__name__)
FLOW_TTL = 300
# Public authorization client used by the SDK's Home Assistant integration.
# This is an application identifier, not a user credential or a developer secret.
CLIENT_ID = 'HA_3y9q4ak7g4ephrvke'
SCHEMA = 'haauthorize'
CANCEL = {'відміна', 'скасувати', 'стоп', '/cancel'}


def start(core):
    return {'name': 'Smart Life', 'version': '0.1',
            'commands': {'smartlife|-smartlife': command}, 'input_handler': consume_input}


def account_path(core):
    meta = core.session.metadata
    identity = json.dumps([meta['chat_id'], meta['user_id']], ensure_ascii=False)
    digest = hashlib.sha256(identity.encode()).hexdigest()
    return core.root / 'data' / 'smartlife' / digest / 'account.json'


def read_json(path, default=None):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default


def write_private(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix='.smartlife-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def login_control():
    from tuya_sharing import LoginControl
    logging.getLogger('tuya_sharing').setLevel(logging.CRITICAL)
    login = LoginControl()
    bound_timeout(login.session)
    return login


def bound_timeout(session):
    request = session.request

    def bounded(method, url, **kwargs):
        kwargs['timeout'] = (3, 10)
        return request(method, url, **kwargs)

    session.request = bounded


def client_settings():
    settings = get_settings('smartlife')
    return settings.get('client_id') or CLIENT_ID, settings.get('schema') or SCHEMA


def begin_qr(core, code):
    code = code.strip()
    if not re.fullmatch(r'[A-Za-z0-9]{6,64}', code):
        core.reply('Введи код користувача зі Smart Life: Профіль → Налаштування → Обліковий запис і безпека → Код користувача.')
        return
    import qrcode
    client_id, schema = client_settings()
    response = login_control().qr_code(client_id, schema, code)
    token = response.get('result', {}).get('qrcode') if response.get('success') else None
    if not isinstance(token, str) or not token:
        core.reply('Smart Life не видав QR. Перевір код користувача та спробуй ще раз.')
        return
    image = BytesIO()
    qrcode.make('tuyaSmart--qrLogin?token=' + token).save(image, format='PNG')
    core.session.smartlife = {'phase': 'scan', 'code': code, 'token': token,
                             'client_id': client_id, 'expires': time.monotonic() + FLOW_TTL}
    core.session.smartlife_result = {'qr_png': base64.b64encode(image.getvalue()).decode(),
                                   'check': True}
    core.reply('Відскануй QR у Smart Life: «+» → сканування, підтвердь вхід, потім натисни «Перевірити підключення». Якщо QR на цьому ж телефоні — збережи зображення та вибери його зі сканера застосунку, якщо він це підтримує.')


def finish_login(core):
    flow = core.session.smartlife
    if flow.get('phase') != 'scan' or flow.get('expires', 0) <= time.monotonic():
        core.session.smartlife = {}
        core.reply('QR уже неактивний. Напиши «smartlife підключити» для нового входу.')
        return
    success, info = login_control().login_result(flow['token'], flow['client_id'], flow['code'])
    if not success:
        core.session.smartlife_result = {'check': True}
        core.reply('Вхід ще не підтверджено або QR застарів. Підтвердь його у Smart Life та натисни перевірку ще раз; для нового QR — «smartlife підключити».')
        return
    fields = ('t', 'uid', 'expire_time', 'access_token', 'refresh_token')
    account = {'version': 1, 'client_id': flow['client_id'], 'user_code': flow['code'],
               'terminal_id': info['terminal_id'], 'endpoint': info['endpoint'],
               'token_info': {key: info[key] for key in fields}}
    write_private(account_path(core), account)
    core.session.smartlife = {}
    show_devices(core)


def fetch_devices(account, path):
    from tuya_sharing import Manager
    logging.getLogger('tuya_sharing').setLevel(logging.CRITICAL)

    class TokenSaver:
        def update_token(self, token_info):
            account['token_info'] = token_info
            write_private(path, account)

    manager = Manager(account['client_id'], account['user_code'], account['terminal_id'],
                      account['endpoint'], account['token_info'], TokenSaver())
    bound_timeout(manager.customer_api.session)
    devices = {}
    # Inventory only: do not open MQTT, send commands or change device state.
    for home in manager.home_repository.query_homes():
        response = manager.customer_api.get('/v1.0/m/life/ha/home/devices', {'homeId': home.id})
        if not response or not response.get('success'):
            raise RuntimeError('Device inventory unavailable')
        for item in response['result']:
            devices[str(item['id'])] = {**item, 'smartlife_home': home.name}
    return list(devices.values())


def show_devices(core):
    path = account_path(core)
    account = read_json(path)
    if not account:
        core.reply('Спочатку підключи Smart Life: «smartlife підключити».')
        return
    devices = fetch_devices(account, path)
    inventory_path = path.with_name('devices.json')
    old = read_json(inventory_path, {'devices': {}})['devices']
    inventory = {}
    lines = ['Пристрої Smart Life:']
    for number, item in enumerate(devices, 1):
        device_id = str(item['id'])
        previous = old.get(device_id, {})
        entry = {**item, 'display_name': previous.get('display_name') or item.get('name') or 'Без назви',
                 'description': previous.get('description', ''), 'location': previous.get('location', ''),
                 'controls': previous.get('controls', [])}
        inventory[device_id] = entry
        online = item.get('online')
        state = 'у мережі' if online is True else 'не в мережі' if online is False else 'стан невідомий'
        lines.append(f"{number}. {entry['display_name']} — {state}")
    write_private(inventory_path, {'version': 1, 'devices': inventory})
    core.reply('\n'.join(lines) if devices else 'Smart Life підключено, але доступних пристроїв не знайдено.')


def command(core, phrase):
    core.session.sensitive_input = True
    from router_memory import clear
    clear()
    if str(core.session.metadata.get('home', '')) != '0':
        core.reply('Підключення Smart Life доступне тільки з будинку 0.')
        return
    words = phrase.strip().split(maxsplit=1)
    action = words[0].casefold() if words else ''
    try:
        if action == 'код':
            begin_qr(core, words[1] if len(words) > 1 else '')
        elif action in {'перевірити', 'перевірка'}:
            finish_login(core)
        elif action in {'список', 'оновити', 'пристрої'}:
            show_devices(core)
        elif action in {'', 'підключити'}:
            if not action and account_path(core).exists():
                show_devices(core)
            else:
                core.session.smartlife = {'phase': 'code', 'expires': time.monotonic() + FLOW_TTL}
                core.reply('Введи код користувача зі Smart Life: Профіль → Налаштування → Обліковий запис і безпека → Код користувача. Можна окремим повідомленням або «smartlife код …». Для скасування — «відміна».')
        else:
            core.reply('Команди: smartlife підключити, smartlife перевірити, smartlife список.')
    except ImportError:
        core.reply('Бібліотеки Smart Life ще не встановлені. Встанови залежності з requirements.txt.')
    except Exception as exc:
        log.warning('Smart Life operation failed (%s)', type(exc).__name__)
        core.reply('Не вдалося отримати відповідь Smart Life. Спробуй ще раз; якщо вхід прострочений — «smartlife підключити».')


def consume_input(core, raw_text):
    text = raw_text.strip()
    match = re.fullmatch(r'-?smartlife(?:\s+(.*))?', text, re.IGNORECASE)
    if match:
        core.context_clear()
        command(core, match[1] or '')
        return True
    flow = core.session.smartlife
    if not flow:
        return False
    normalized = core.normalize(text)
    if any(normalized == alias.strip() or normalized.startswith(alias.strip() + ' ')
           for group in core.commands for alias in group.split('|')):
        return False
    if str(core.session.metadata.get('home', '')) != '0' or flow.get('expires', 0) <= time.monotonic():
        core.session.smartlife = {}
        if re.fullmatch(r'[A-Za-z0-9]{6,64}', text):
            core.session.sensitive_input = True
            core.reply('Час введення коду минув. Напиши «smartlife підключити».')
            return True
        return False
    if text.casefold() in CANCEL:
        core.session.smartlife = {}
        return False
    if flow.get('phase') == 'code' and re.fullmatch(r'[A-Za-z0-9]{6,64}', text):
        core.session.sensitive_input = True
        try:
            begin_qr(core, text)
        except Exception as exc:
            log.warning('Smart Life QR failed (%s)', type(exc).__name__)
            core.reply('Не вдалося створити QR Smart Life. Перевір код і спробуй ще раз.')
        return True
    return False
