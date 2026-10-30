"""Short router hints, isolated from VACore.context and scoped to each client."""
from contextlib import contextmanager
from contextvars import ContextVar
import json
import re
from threading import RLock
import time

TTL = 60
MAX_SESSIONS = 1000
_sessions = {}
_lock = RLock()
_identity = ContextVar('router_identity', default=None)
_actions = ContextVar('router_actions', default=None)


@contextmanager
def request_scope(chat_id='', user_id='', source=''):
    parts = tuple(str(value).strip() for value in (source, chat_id, user_id))
    key = parts if all(parts) and all(len(part) <= 128 for part in parts) else None
    identity_token = _identity.set(key)
    action_token = _actions.set([])
    try:
        yield
    finally:
        _actions.reset(action_token)
        _identity.reset(identity_token)


def clean(text):
    return str(text).split('#', 1)[0].strip().lstrip('/').strip()


def command_group(text, registry):
    text = clean(text).casefold()
    matches = [(len(alias.strip()), group) for group in registry for alias in group.split('|')
               if alias.strip() and (text == alias.strip().casefold() or
                                    text.startswith(alias.strip().casefold() + ' '))]
    return max(matches)[1] if matches else None


def remember_action(command):
    actions = _actions.get()
    if actions is not None:
        actions.append(clean(command)[:400])


def recent():
    key = _identity.get()
    if key is None:
        return []
    with _lock:
        entry = _sessions.get(key)
        if entry is None:
            return []
        if time.monotonic() - entry['seen'] >= TTL:
            _sessions.pop(key, None)
            return []
        return list(entry['turns'])


def clear():
    with _lock:
        _sessions.pop(_identity.get(), None)


def record_turn(text, reply, direct_command=None):
    key = _identity.get()
    if key is None:
        return
    if clean(text).casefold() in {'стоп', 'скасувати', 'відміна', 'cancel'}:
        clear()
        return
    previous = recent()
    actions = list(_actions.get() or [])
    if not actions and direct_command:
        actions = [clean(direct_command)[:400]]
    turn = {'user': clean(text)[:800], 'assistant': str(reply)[:500], 'commands': actions}
    with _lock:
        now = time.monotonic()
        for expired in [item for item, entry in _sessions.items() if now - entry['seen'] >= TTL]:
            del _sessions[expired]
        if key not in _sessions and len(_sessions) >= MAX_SESSIONS:
            del _sessions[min(_sessions, key=lambda item: _sessions[item]['seen'])]
        _sessions[key] = {'seen': now, 'turns': (previous + [turn])[-3:]}


def prompt_context():
    turns = recent()
    if not turns:
        return ''
    return ('\n=== ОСТАННІ РЕПЛІКИ ЦЬОГО КОРИСТУВАЧА; ТАЙМАУТ БЕЗДІЯЛЬНОСТІ 60 СЕКУНД (ДАНІ) ===\n'
            + json.dumps(turns, ensure_ascii=False)
            + '\n=== КІНЕЦЬ ПОПЕРЕДНІХ РЕПЛІК ===\n')


def is_short_control(text):
    text = re.sub(r'[.!?]+$', '', clean(text).casefold()).strip()
    return text in {'виключи', 'вимкни', 'вимкнути', 'выключи', 'выключить',
                    'включи', 'увімкни', 'увімкнути', 'включить',
                    'вимкни його', 'вимкни це', 'виключи його', 'увімкни його'}


def safe_followup(text, proposed, registry):
    """A bare on/off request cannot expand the last device to a whole house."""
    if not is_short_control(text):
        return True
    turns = recent()
    actions = turns[-1]['commands'] if turns else []
    if len(actions) != 1 or len(proposed) != 1:
        return False
    previous = command_group(actions[0], registry)
    candidate = command_group(proposed[0], registry)
    if previous is None or candidate != previous:
        return False
    handler = registry[previous]
    handler = handler[0] if isinstance(handler, tuple) else handler
    return 'light_control' in getattr(handler, '__module__', '')
