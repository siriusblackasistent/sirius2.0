"""Hikvision JPEG snapshots to the requesting, authorized Telegram chat."""
from io import BytesIO
import json
import logging
from pathlib import Path
import re
from urllib.parse import urlsplit

import requests
from requests.auth import HTTPDigestAuth
from hikvisionapi import Client
from router_memory import _identity

ROOT = Path(__file__).resolve().parents[1]
MAX_IMAGE_BYTES = 9 * 1024 * 1024
log = logging.getLogger(__name__)


class SnapshotClient(Client):
    def _check_session(self):
        # Upstream's constructor probes /System/status without a timeout.
        # Snapshots need only one bounded GET, using the device's Digest auth.
        session = requests.Session()
        session.trust_env = False
        session.auth = HTTPDigestAuth(self.login, self.password)
        return session


def start(core):
    return {'name': 'Hikvision snapshots', 'version': '1.3', 'require_online': True,
            # The old dispatcher picks the first prefix, without word boundaries.
            # Compact aliases must precede the generic "камера" and "кам".
            'commands': {'камера1|кам1|-кам1|кам 1': snapshot_one,
                         'камера|camera|хіквіжн|hikvision|кам': snapshot}}


def settings():
    path = ROOT / 'options' / 'hikvision.local.json'
    return json.loads(path.read_text(encoding='utf-8'))


def guest_core(core):
    return callable(getattr(type(core), 'handle_message', None))


def reply(core, text):
    (core.reply if guest_core(core) else core.say)(text)


def telegram_settings(config, core=None):
    if guest_core(core):
        from sirius_settings import BASE_DIR, get_settings, get_secret, load_json
        options = {**load_json(BASE_DIR / 'options_telegrambot.json'), **get_settings('telegram')}
        options['botKey'] = get_secret('telegram.bot_token')
        return options
    path = Path(config.get('telegram_config') or
                ROOT.parent / 'remo' / 'Remote-Irene' / 'options_telegrambot.json')
    return json.loads(path.read_text(encoding='utf-8'))


def authorized_chat(config, telegram, number='1', core=None):
    identity = _identity.get()
    if not identity or (identity[0] != 'telegram' and not (guest_core(core) and identity[0] == f'guest:{id(core)}')):
        return None
    _, chat_id, user_id = identity
    chats = {str(value) for value in config.get('allowed_chats', telegram.get('adminChats', []))}
    users = {str(value) for value in config.get('allowed_users', telegram.get('adminUsers', []))}
    # A camera may be sent to an approved group or an administrator's own DM.
    # An admin's command in an unapproved guest group does not disclose a photo.
    if chat_id in chats or (chat_id == user_id and user_id in users):
        return chat_id
    homes = telegram.get('guestChats', {})
    home = str(homes.get(chat_id, homes.get(int(chat_id), ''))) if str(chat_id).lstrip('-').isdigit() else ''
    camera = config.get('cameras', {}).get(number, {})
    if home and home in {str(value) for value in camera.get('allowed_homes', ['0'])}:
        return chat_id
    return None


def fetch_snapshot(camera):
    url = camera['url'].rstrip('/')
    parsed = urlsplit(url)
    if (parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.fragment or parsed.path):
        raise ValueError('Invalid camera URL')
    if not camera.get('username') or not camera.get('password'):
        raise ValueError('Missing camera credentials')
    channel = int(camera.get('channel', 101))
    if not 1 <= channel <= 99999:
        raise ValueError('Invalid channel')
    client = SnapshotClient(url, camera['username'], camera['password'], timeout=8)
    client.req.verify = camera.get('verify_tls', True)
    try:
        response = client.Streaming.channels[channel].picture(
            method='get', type='opaque_data', allow_redirects=False)
        try:
            response.raise_for_status()
            if response.status_code != 200:
                raise ValueError('Unexpected camera response')
            data = bytearray()
            for chunk in response.iter_content(chunk_size=65536):
                data.extend(chunk)
                if len(data) > MAX_IMAGE_BYTES:
                    raise ValueError('Snapshot exceeds Telegram photo limit')
            if not data.startswith(b'\xff\xd8\xff'):
                raise ValueError('Camera did not return JPEG')
            return bytes(data)
        finally:
            response.close()
    finally:
        client.req.close()


def send_photo(token, chat_id, image, caption):
    photo = BytesIO(image)
    photo.name = 'camera.jpg'
    response = requests.post(f'https://api.telegram.org/bot{token}/sendPhoto',
        data={'chat_id': chat_id, 'caption': caption},
        files={'photo': ('camera.jpg', photo, 'image/jpeg')}, timeout=(3, 25))
    try:
        response.raise_for_status()
        if not response.json().get('ok'):
            raise RuntimeError('Telegram did not accept photo')
    finally:
        response.close()


def snapshot(core, phrase):
    try:
        config = settings()
        telegram = telegram_settings(config, core)
        # The old Telegram client appends #name...Home... metadata.
        phrase = phrase.split('#', 1)[0]
        match = re.fullmatch(r'\s*(\d{1,2}|один|одна|перш[а-яіїє]*)\s*', phrase.casefold())
        if not match:
            reply(core, 'Напиши «камера 1».')
            return
        number = match[1] if match[1].isdigit() else '1'
        chat_id = authorized_chat(config, telegram, number, core)
        if chat_id is None:
            reply(core, 'Знімки камер доступні лише в дозволеному Telegram-чаті.')
            return
        camera = config.get('cameras', {}).get(number)
        if not camera:
            reply(core, 'Камеру з таким номером ще не налаштовано.')
            return
        token = telegram.get('botKey')
        if not token or token == 'TOKEN_HERE':
            reply(core, 'Токен Telegram-бота ще не налаштований.')
            return
        image = fetch_snapshot(camera)
        send_photo(token, chat_id, image, camera.get('name') or f'Камера {number}')
        reply(core, f'Знімок камери {number} надіслано.')
    except FileNotFoundError:
        reply(core, 'Налаштування камери ще не заповнені.')
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else None
        log.warning('Hikvision snapshot HTTP error (%s)', status)
        reply(core, 'Перевір логін, пароль і право перегляду камери.' if status in {401,403}
                 else 'Не вдалося отримати або надіслати знімок камери.')
    except Exception as exc:
        # Exception messages can include camera credentials or Telegram URLs.
        log.warning('Hikvision snapshot failed (%s)', type(exc).__name__)
        reply(core, 'Не вдалося отримати або надіслати знімок камери. Перевір доступність і налаштування.')


def snapshot_one(core, phrase):
    tail = phrase.split('#', 1)[0].strip()
    # The old dispatcher consumes one extra character after an alias. With
    # "камера1#name..." it removes '#', leaving just the legacy metadata.
    legacy_metadata = re.fullmatch(r'name[^\r\n]*Home\d+fortelegramgrup', tail)
    if tail and not legacy_metadata:
        reply(core, 'Напиши «кам1» або «камера 1».')
        return
    snapshot(core, '1')
