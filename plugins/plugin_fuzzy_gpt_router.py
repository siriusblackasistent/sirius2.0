"""Sirius AI router, ported from Irene with guest credentials and command dispatch."""
from sirius_settings import get_secret
import os
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
import openai
from sirius_core import SiriusCore
from notifiers import get_notifier
from router_memory import prompt_context, remember_action, safe_followup
modname = os.path.basename(__file__)[:-3]
PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
PROMPT_FILE = os.path.join(PLUGIN_DIR, 'промт.txt')
COMMANDS_LIST_FILE = os.path.join(PLUGIN_DIR, 'commands_list.txt')
GPT_MODEL = 'gpt-4o-mini'
GPT_TIMEOUT = 10
GPT_MAX_RETRIES = 3
RATE_LIMIT_BLOCK_SECONDS = 90
LOG_ONLY_MODE = False
ROUTER_LOCK = False
RATE_LIMIT_UNTIL = 0.0
LAST_SUCCESSFUL_RESPONSE = ''
DUMMY_COMMAND_KEY = '__gpt_router_dummy__'
OPENAI_API_KEY = get_secret('plugin_fuzzy_gpt_router.openai_api_key')
TG_BOT_TOKEN = get_secret('plugin_fuzzy_gpt_router.tg_bot_token')
TG_CHAT_ID = get_secret('telegram.router_chat_id')
telegram = get_notifier('telegram')
MAX_TG_LEN = 3500

def tg_log(text: str):
    """Надсилає лог у Telegram, не валить плагін якщо помилка."""
    return
_ENCODING_CANDIDATES = ('utf-8', 'utf-8-sig', 'cp1251', 'latin-1')

def read_text(path: str, fallback: str):
    """Читає файл з fallback кодувань."""
    if not os.path.exists(path):
        return (fallback, None)
    last_err = None
    for enc in _ENCODING_CANDIDATES:
        try:
            with open(path, 'r', encoding=enc) as f:
                return (f.read(), enc)
        except UnicodeDecodeError as e:
            last_err = e
            continue
        except Exception as e:
            last_err = e
            break
    tg_log(f'⚠️ Не зміг прочитати файл: {path}\n{last_err}')
    return (fallback, None)

def strip_meta_from_phrase(phrase: str) -> str:
    """
    Приклад у тебе: "треба включити ...#nameдмитроHome0fortelegramgrup"
    Можемо відокремити мета-частину після #, щоб GPT краще розумів.
    """
    if not phrase:
        return ''
    return phrase.split('#', 1)[0].strip()

def parse_gpt_commands_and_text(txt: str):
    """
    GPT може відповісти:
    /баня
    /телевізор
    логіка: ...
    або просто текстом.

    Повертає:
    - commands: список рядків без "/" (clean)
    - speak_text: те, що треба озвучити користувачу (може бути txt, або узагальнення)
    """
    if not txt:
        return ([], '')
    lines = [l.strip() for l in txt.splitlines() if l.strip()]
    cmd_lines = [l for l in lines if l.startswith('/')]
    commands = [c.lstrip('/').strip() for c in cmd_lines if c.lstrip('/').strip()]
    speak_text = txt.strip()
    return (commands, speak_text)

def extract_logic_comment(txt: str) -> str:
    """Повертає текст GPT без рядків-команд (що починаються з '/')."""
    if not txt:
        return ''
    cleaned_lines = []
    for line in txt.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith('/'):
            continue
        cleaned_lines.append(stripped)
    return ' '.join(cleaned_lines).strip()

def _noop_command(core: SiriusCore, phrase: str):
    """Порожній обробник для заглушки."""
    return

def execute_clean_command(core: SiriusCore, clean_cmd: str):
    """
    Виконує дозволену команду через SiriusCore.
    Повертає готову відповідь плагіна.
    """
    global ROUTER_LOCK
    ROUTER_LOCK = True
    try:
        match = core.find_best_cmd_with_fuzzy(clean_cmd, core.commands, threshold=1.0)
        if match is None or match[0] == '__clarify__' or match[0] in core.plugin_commands.get(modname, []):
            core.reply('Не знайшов дозволену команду. Уточни, будь ласка.')
            return core.lastSay
        key, _, rest = match
        core.execute_next(rest, core.commands[key])
        remember_action(clean_cmd)
        response_text = (core.lastSay or '').strip()
        return response_text.strip()
    finally:
        ROUTER_LOCK = False

def classify_openai_error(err: Exception) -> str:
    """
    Повертає тип помилки ('auth', 'rate', 'network', 'timeout', 'other')
    """
    text = str(err).lower()
    if '401' in text or 'invalid api key' in text or 'authentication' in text:
        return 'auth'
    if '429' in text or 'rate limit' in text or 'quota' in text:
        return 'rate'
    if isinstance(err, FuturesTimeout) or 'timeout' in text:
        return 'timeout'
    if 'connection' in text or 'network' in text:
        return 'network'
    return 'other'

def _gpt_request(full_prompt: str):
    completion = openai.ChatCompletion.create(model=GPT_MODEL, messages=[{'role': 'user', 'content': full_prompt}], temperature=0.2, request_timeout=GPT_TIMEOUT)
    return completion.choices[0].message['content'].strip()

def call_gpt_with_retry(full_prompt: str):
    """
    Викликає GPT з жорстким таймаутом та експоненційним ретраєм.
    """
    backoff = 1
    last_error = None
    for attempt in range(1, GPT_MAX_RETRIES + 1):
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(_gpt_request, full_prompt)
            try:
                return future.result(timeout=GPT_TIMEOUT)
            except FuturesTimeout as e:
                last_error = e
            except Exception as e:
                last_error = e
        if attempt < GPT_MAX_RETRIES:
            time.sleep(backoff)
            backoff *= 2
    raise last_error if last_error else RuntimeError('Unknown GPT error')

def start(core: SiriusCore):
    manifest = {'name': 'Fuzzy GPT Router', 'version': '3.0', 'require_online': True, 'commands': {'команда': gpt_router_entry_point, DUMMY_COMMAND_KEY: _noop_command}, 'fuzzy_processor': {'gpt_router': (init, predict)}}
    return manifest

def init(core: SiriusCore):
    tg_log('✅ GPT Router init: готово')

def gpt_router_entry_point(core: SiriusCore, phrase: str):
    """
    Ручний вхід: користувач сказав "команда ..." і ми примусово шлемо це в GPT Router.
    """
    tg_log(f"▶ Ручний вхід 'команда'. Фраза: {phrase}")
    return run_router(core, phrase, source='manual')

def predict(core: SiriusCore, phrase: str, context: dict, allow_rest_phrase=True):
    """
    VACore викликає fuzzy_processor, коли НЕ знайшла команду.
    Тут важливо: якщо ми повернемо "ніби знайдену команду", VACore може піти в execute_next
    і викликати повторні штуки.

    Тому ми:
    - робимо всю роботу тут
    - повертаємо None (щоб VACore потім сказала "не зрозуміла команда" або вже не сказала)
      Але ми вже озвучимо відповідь, тому краще повернути None і нічого більше.
    """
    if not phrase or not phrase.strip():
        tg_log('🟡 predict отримав порожню фразу — ігнорую.')
        return None
    if ROUTER_LOCK:
        tg_log('🟡 predict пропущено (виконуються внутрішні команди).')
        return None
    handled = run_router(core, phrase, source='fuzzy')
    if handled:
        if DUMMY_COMMAND_KEY not in context:
            context[DUMMY_COMMAND_KEY] = _noop_command
        return (DUMMY_COMMAND_KEY, 1.0, '')
    return None

def allowed_catalog(core, descriptions):
    """Describe only commands that are actually registered in this guest core."""
    groups = {group: {core.normalize(alias) for alias in group.split('|')
                      if alias and not alias.startswith('__')}
              for group in core.commands
              if group not in core.plugin_commands.get(modname, [])}
    aliases = set().union(*groups.values()) if groups else set()
    lines, described = [], set()
    for line in descriptions.splitlines():
        if line.startswith('/'):
            alias = core.normalize(line.split()[0].lstrip('/'))
            if alias in aliases:
                lines.append(line)
                described.add(alias)
    for group, names in groups.items():
        if names and not names.intersection(described):
            lines.append('/' + group.split('|')[0])
    return '\n'.join(lines)

def run_router(core: SiriusCore, phrase: str, source: str='fuzzy'):
    """
    Основна логіка роутера:
    - читаємо промт/статуси/команди
    - формуємо prompt
    - питаємо GPT
    - якщо є /команди — виконуємо їх безпосередньо у VACore (без "/")
    - якщо нема — озвучуємо відповідь GPT
    - повертаємо None (щоб не було повторних заходів)
    """
    global RATE_LIMIT_UNTIL, LAST_SUCCESSFUL_RESPONSE
    now = time.time()
    if RATE_LIMIT_UNTIL and now < RATE_LIMIT_UNTIL:
        core.reply('GPT відпочиває, зачекай трохи і спробуй знову.')
        return True
    user_phrase_clean = strip_meta_from_phrase(phrase)
    meta_tail = ''
    if '#' in phrase:
        meta_tail = '#' + phrase.split('#', 1)[1]
    tg_log(f'🧩 Джерело: {source}\n🗣 Запит: {user_phrase_clean}\n📎 Мета: {meta_tail}')
    (static_prompt, enc1) = read_text(PROMPT_FILE, 'Промт не знайдено.')
    if enc1:
        pass
    else:
        tg_log('⚠️ Промт не знайдено або не прочитано')
    device_status = 'Статуси пропущено для швидкої відповіді.'
    (commands_list, enc3) = read_text(COMMANDS_LIST_FILE, 'Перелік команд недоступний.')
    if enc3:
        pass
    else:
        tg_log('⚠️ Список команд не знайдено/не прочитано')
    commands_list = allowed_catalog(core, commands_list)
    full_prompt = (
        static_prompt
        + '\n\n=== СТАТУС ДЕВАЙСІВ ===\n' + device_status
        + '\n=== ДОЗВОЛЕНІ КОМАНДИ ===\n' + commands_list
        + prompt_context()
        + '\n=== ЗАПИТ КОРИСТУВАЧА ===\n' + user_phrase_clean
        + '\n\nПРАВИЛА:\n'
        '- Для керування пристроями потрібна однозначна ціль. Для пошуку фільму достатньо зрозумілого наміру та пошукової назви.\n'
        '- Якщо намір або ціль неясні — попроси коротке ввічливе уточнення.\n'
        '- Якщо даєш /команди — кожна команда з нового рядка.\n'
        '- Повертай лише команди, без пояснень і підтверджень: відповідь дасть плагін.\n'
        '- Команди мають бути ТІЛЬКИ зі списку дозволених.\n'
    )
    if not OPENAI_API_KEY:
        core.reply('Відсутній OpenAI ключ, мяу.')
        tg_log('❌ OPENAI_API_KEY не заданий.')
        return True
    openai.api_key = OPENAI_API_KEY
    try:
        txt = call_gpt_with_retry(full_prompt)
    except Exception as e:
        kind = classify_openai_error(e)
        if kind == 'auth':
            RATE_LIMIT_UNTIL = time.time() + RATE_LIMIT_BLOCK_SECONDS
            msg = 'GPT ключ відсутній або невірний.'
        elif kind == 'rate':
            RATE_LIMIT_UNTIL = time.time() + RATE_LIMIT_BLOCK_SECONDS
            msg = 'GPT каже, що квота закінчилась. Спробуй трохи пізніше.'
        elif kind in ('timeout', 'network'):
            msg = "Немає зв'язку з GPT, повторю пізніше."
        else:
            msg = 'GPT поламався, спробуй знову.'
        core.reply(msg)
        return True
    tg_log(f'🤖 GPT відповів:\n{txt}')
    (commands, speak_text) = parse_gpt_commands_and_text(txt)
    if not safe_followup(user_phrase_clean, commands, core.commands):
        core.reply('Що саме увімкнути або вимкнути? Уточни пристрій, будь ласка.')
        return True
    tg_log(f'📨 Команд знайдено: {len(commands)}\n{commands}')
    if not commands:
        core.reply(speak_text)
        LAST_SUCCESSFUL_RESPONSE = speak_text
        return True
    if LOG_ONLY_MODE:
        core.reply(speak_text)
        return True
    for command in commands:
        match = core.find_best_cmd_with_fuzzy(command, core.commands, threshold=1.0)
        if match is None or match[0] == '__clarify__' or match[0] in core.plugin_commands.get(modname, []):
            core.reply('Не знайшов дозволену команду. Уточни, будь ласка.')
            return True
    for clean_cmd in commands:
        try:
            execute_clean_command(core, clean_cmd)
        except Exception:
            core.reply(f'Не зміг виконати {clean_cmd}.')
    return True
