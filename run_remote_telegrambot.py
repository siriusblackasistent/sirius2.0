#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import telebot
import base64
from io import BytesIO
from types import SimpleNamespace
from telebot import types
import json
import random
import time
import requests
import os
import logging
import re
import subprocess
from sirius_settings import BASE_DIR, load_json, get_secret, get_settings, media_python, server_config
import threading
from datetime import datetime

# ========== Налаштування ==========
logging.basicConfig(filename='telebot.log', filemode='w', level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

version = "1.9"

saved_options = server_config()

baseUrl = saved_options["baseUrl"].rstrip("/")

default_options = {'adminChats': [], 'adminUsers': [], 'guestChats': {}, 'registrationHomes': ['30', '70'], 'allowAddAdmins': True, 'botKey': 'TOKEN_HERE', 'menu': 'помічник,люстра,кухня,вогник,баня', 'menuAdmin': 'меню,статус,кам1', 'menuByHome': {'30': 'меню,статус,кам1', '70': '70 кухня,70 столовая,70 статус'}}
options_tele = {**default_options, **load_json(BASE_DIR / "options_telegrambot.json"), **get_settings("telegram")}
options_tele["botKey"] = get_secret("telegram.bot_token")


def save_options_tele():
    with (BASE_DIR / "options_telegrambot.json").open("w", encoding="utf-8") as f:
        json.dump({k: v for k, v in options_tele.items() if k != "botKey"}, f, ensure_ascii=False, indent=4)


if not options_tele["botKey"]:
    raise SystemExit("Задайте telegram.bot_token у secrets.local.json")
bot = telebot.TeleBot(options_tele["botKey"])
latestRec = ""
restxt = ""

REZKA_PYTHON = media_python()
REZKA_CATALOG = str(BASE_DIR / "rezka_catalog.py")
_media_settings = get_settings("media")
MEDIA_SERVER_URL = "http://{}:{}".format(
    _media_settings.get("host") or "127.0.0.1", _media_settings.get("port") or 8766)
media_sessions = {}
MEDIA_TEXT_COMMANDS = {
    "пауза": ("pause", None),
    "вперед": ("forward", None),
    "вперед хвилина": ("forward", None),
    "назад": ("back", None),
    "назад хвилина": ("back", None),
    "420": ("quality", 420),
    "720": ("quality", 720),
    "1080": ("quality", 1080),
    "зміна озвучки": ("voice", None),
    "озвучка": ("voice", None),
    "виключити": ("stop", None),
    "стоп фільм": ("stop", None),
    "наступна серія": ("next_episode", None),
    "попередня серія": ("previous_episode", None),
}

# Стан реєстрації гостей
guest_registration = {}
user_home = {}  # зберігаємо номер будинку для кожного користувача


def save_guest_to_file(data):
    with open("guest_log.txt", "a", encoding="utf-8") as f:
        f.write(" | ".join(f"{k}: {v}" for k, v in data.items()) + "\n")


def get_guest_home_by_chat(chat_id):
    guest_chats = options_tele.get("guestChats", {})
    return str(guest_chats.get(str(chat_id), guest_chats.get(chat_id, ""))).strip()


def get_registration_homes():
    homes = options_tele.get("registrationHomes", ["30", "70"])
    return [str(h).strip() for h in homes if str(h).strip()]


def is_admin_chat(chat_id):
    return chat_id in options_tele.get("adminChats", [])


def is_admin_user(user_id):
    return user_id in options_tele.get("adminUsers", [])


def is_guest_chat(chat_id):
    return bool(get_guest_home_by_chat(chat_id))


def get_menu_for_chat(chat_id):
    if is_admin_chat(chat_id):
        return options_tele.get("menuAdmin") or options_tele.get("menu", "")

    home = get_guest_home_by_chat(chat_id)
    if home:
        home_menu = options_tele.get("menuByHome", {}).get(home)
        if home_menu:
            return home_menu

    return options_tele.get("menu", "")


def build_menu_markup(chat_id):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=False)
    buttons = [
        types.KeyboardButton(item.strip())
        for item in get_menu_for_chat(chat_id).split(",")
        if item.strip()
    ]
    for index in range(0, len(buttons), 2):
        markup.row(*buttons[index:index + 2])
    return markup


def resolve_home_for_message(message):
    chat_home = get_guest_home_by_chat(message.chat.id)
    if chat_home:
        return chat_home
    return user_home.get(message.from_user.id, "0")


def normalize_guest_command(raw_text, guest_home):
    text = (raw_text or "").strip().lower()
    if not text:
        return None, "Порожня команда"

    if text.startswith(("через ", "будильник ", "о ")):
        return text, None

    if text in {"відміна", "скасувати", "стоп", "/cancel"}:
        return text, None

    if text.startswith("#") or text.startswith("помічник"):
        return text, None

    menu_triggers = {"m", "/menu", "/"}
    if text in menu_triggers or text.startswith("/"):
        return text, None

    # Для дому 30 не додаємо префікс автоматично: відправляємо як є.
    if guest_home == "30":
        if re.match(r"^70\s*.*$", text):
            return None, "У цьому чаті доступний тільки будинок 30"
        text_without_30 = re.sub(r"^30\s*", "", text).strip()
        return (text_without_30 or text), None

    # Для дому 70 залишаємо префіксування.
    match = re.match(r"^(30|70)\s*(.*)$", text)
    if match:
        typed_home = match.group(1)
        rest = (match.group(2) or "").strip()
        if typed_home != guest_home:
            return None, f"У цьому чаті доступний тільки будинок {guest_home}"
        return f"{guest_home} {rest}".strip(), None

    return f"{guest_home} {text}".strip(), None


# ========== Вітання нового учасника ==========
@bot.message_handler(content_types=['new_chat_members'])
def greet_new_member(message):
    # Реєстрація тільки у гостьових чатах (30/70)
    guest_home = get_guest_home_by_chat(message.chat.id)
    if not guest_home:
        return

    for new_member in message.new_chat_members:
        if new_member.is_bot:
            continue

        chat_id = message.chat.id
        user_id = new_member.id
        name = new_member.first_name
        if new_member.last_name:
            name += " " + new_member.last_name

        welcome_cmd = f"помічник я новий гість. {name}. привітайся зі мною..."
        send_cmd_towebapi(welcome_cmd, message, user_name=name)

        guest_registration[user_id] = {
            "step": 0,
            "chat_id": chat_id,
            "expected_home": guest_home,
            "data": {
                "Дата": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "Ім'я": name,
                "Група": str(chat_id),
                "Будинок": guest_home
            }
        }
        user_home[user_id] = guest_home
        bot.send_message(chat_id, f"#Реєстрація: {name}, скільки днів ви плануєте залишитися?")


# ========== Відправка команди на веб-API ==========
def send_cmd_towebapi(cmd, message, user_name=None):
    if not cmd or cmd.startswith("#"):
        return
    chat_id, user_id = message.chat.id, message.from_user.id
    with notification_lock:
        notification_users.add((chat_id, user_id))
    try:
        response = requests.post(baseUrl + "/message", json={
            "text": cmd, "chat_id": chat_id, "user_id": user_id,
            "user_name": user_name or message.from_user.first_name or "",
            "home": resolve_home_for_message(message),
        }, headers={"X-Sirius-Key": get_secret("server.api_key")}, timeout=90)
        response.raise_for_status()
        result = response.json()
        text = result.get("text") or "Команду оброблено."
        markup = timer_keyboard(result.get("confirmation"), user_id)
        smartlife = result.get("smartlife")
        if smartlife and smartlife.get("check"):
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton('Перевірити підключення', callback_data=f'sl:check:{user_id}'))
        if result.get("media_search") is not None:
            search = result["media_search"]
            media_sessions.pop((chat_id, user_id), None)
            if search["items"]:
                media_sessions[(chat_id, user_id)] = {
                    **search, "expires": time.monotonic() + 15 * 60,
                }
                markup = rezka_results_keyboard(user_id, len(search["items"]))
        for start in range(0, len(text), 4000):
            kwargs = {"reply_markup": markup} if markup and start + 4000 >= len(text) and not (smartlife and smartlife.get('qr_png')) else {}
            bot.send_message(chat_id, text[start:start + 4000], **kwargs)
        if smartlife and smartlife.get('qr_png'):
            photo = BytesIO(base64.b64decode(smartlife['qr_png'], validate=True))
            photo.name = 'smartlife-qr.png'
            bot.send_photo(chat_id, photo, reply_markup=markup)
    except (requests.RequestException, ValueError):
        bot.send_message(chat_id, "Помилка зв'язку з сервером Сіріуса")

def timer_keyboard(confirmation, user_id):
    if not confirmation:
        return None
    markup = types.InlineKeyboardMarkup()
    ident = confirmation["id"]
    markup.add(
        types.InlineKeyboardButton("Підтвердити", callback_data=f"tm:y:{user_id}:{ident}"),
        types.InlineKeyboardButton("Скасувати", callback_data=f"tm:n:{user_id}:{ident}"),
    )
    return markup


@bot.callback_query_handler(func=lambda call: call.data and call.data.startswith('sl:'))
def handle_smartlife_callback(call):
    if call.data != f'sl:check:{call.from_user.id}':
        bot.answer_callback_query(call.id, 'Це підключення іншого користувача')
        return
    message = SimpleNamespace(chat=call.message.chat, from_user=call.from_user)
    if resolve_home_for_message(message) != '0':
        bot.answer_callback_query(call.id, 'Smart Life доступний тільки з будинку 0')
        return
    bot.answer_callback_query(call.id, 'Перевіряю підключення')
    send_cmd_towebapi('smartlife перевірити', message)


@bot.callback_query_handler(func=lambda call: call.data and call.data.startswith("tm:"))
def handle_timer_callback(call):
    try:
        _, choice, owner, ident = call.data.split(":", 3)
        if int(owner) != call.from_user.id:
            bot.answer_callback_query(call.id, "Це таймер іншого користувача")
            return
        if choice not in {"y", "n"}:
            bot.answer_callback_query(call.id, "Невідома дія")
            return
        chat_id = call.message.chat.id
        if not is_admin_chat(chat_id) and not is_guest_chat(chat_id) and not is_admin_user(call.from_user.id):
            bot.answer_callback_query(call.id, "Доступ заблоковано")
            return
        bot.answer_callback_query(call.id)
        response = requests.post(baseUrl + "/timer/decision", json={
            "chat_id": chat_id, "user_id": call.from_user.id, "id": ident,
            "decision": "confirm" if choice == "y" else "cancel",
        }, headers={"X-Sirius-Key": get_secret("server.api_key")}, timeout=15)
        response.raise_for_status()
        result = response.json()
        if result.get("ok"):
            with notification_lock:
                notification_users.add((chat_id, call.from_user.id))
            bot.edit_message_reply_markup(chat_id, call.message.message_id, reply_markup=None)
        bot.send_message(chat_id, result["text"])
    except (requests.RequestException, ValueError):
        bot.send_message(call.message.chat.id, "Не отримав підтвердження від сервера. Можна натиснути кнопку ще раз.")


notification_users = set()
notification_lock = threading.Lock()

def poll_notifications():
    while True:
        with notification_lock:
            users = list(notification_users)
        for chat_id, user_id in users:
            try:
                response = requests.get(baseUrl + "/notifications", params={"chat_id": chat_id, "user_id": user_id},
                    headers={"X-Sirius-Key": get_secret("server.api_key")}, timeout=10)
                response.raise_for_status()
                for text in response.json().get("messages", []):
                    bot.send_message(chat_id, text)
            except Exception:
                logging.warning("Cannot deliver Sirius timer notification")
        time.sleep(2)



CONTENT_TYPE_LABELS = {
    "film": "Фільм",
    "series": "Серіал",
    "cartoon": "Мультфільм",
    "anime": "Аніме",
    "video": "Відео",
}


def parse_rezka_query(query):
    patterns = (
        r"\bs\s*(\d+)\s*e\s*(\d+)\b",
        r"\b(\d+)\s*[xх]\s*(\d+)\b",
        r"\b(\d+)\s*сезон\w*\s*(\d+)\s*сер\w*\b",
        r"\bсезон\w*\s*(\d+)\s*сер\w*\s*(\d+)\b",
    )
    for pattern in patterns:
        match = re.search(pattern, query, flags=re.IGNORECASE)
        if match:
            title = (query[:match.start()] + " " + query[match.end():]).strip()
            return re.sub(r"\s+", " ", title), int(match.group(1)), int(match.group(2))
    return query.strip(), None, None


def rezka_search(query, limit=5):
    process = subprocess.run(
        [REZKA_PYTHON, REZKA_CATALOG, "search", query, "--limit", str(limit)],
        capture_output=True, text=True, timeout=45, check=True
    )
    return json.loads(process.stdout)


def rezka_results_keyboard(user_id, count):
    markup = types.InlineKeyboardMarkup(row_width=2)
    buttons = [
        types.InlineKeyboardButton(f"Увімкнути {number}", callback_data=f"rz:play:{user_id}:{number - 1}")
        for number in range(1, count + 1)
    ]
    markup.add(*buttons)
    markup.add(types.InlineKeyboardButton("Не знайдено / скасувати", callback_data=f"rz:cancel:{user_id}:0"))
    return markup


def rezka_controls_keyboard(user_id, state=None):
    markup = types.InlineKeyboardMarkup(row_width=3)
    markup.add(
        types.InlineKeyboardButton("Пауза", callback_data=f"rz:ctl:{user_id}:pause"),
        types.InlineKeyboardButton("-1 хв", callback_data=f"rz:ctl:{user_id}:back"),
        types.InlineKeyboardButton("+1 хв", callback_data=f"rz:ctl:{user_id}:forward"),
    )
    markup.add(
        types.InlineKeyboardButton("420", callback_data=f"rz:ctl:{user_id}:q420"),
        types.InlineKeyboardButton("720", callback_data=f"rz:ctl:{user_id}:q720"),
        types.InlineKeyboardButton("1080", callback_data=f"rz:ctl:{user_id}:q1080"),
    )
    markup.add(
        types.InlineKeyboardButton("Попередня серія", callback_data=f"rz:ctl:{user_id}:prev_episode"),
        types.InlineKeyboardButton("Наступна серія", callback_data=f"rz:ctl:{user_id}:next_episode"),
    )
    markup.add(
        types.InlineKeyboardButton("Змінити озвучку", callback_data=f"rz:ctl:{user_id}:voice"),
        types.InlineKeyboardButton("Виключити", callback_data=f"rz:ctl:{user_id}:stop"),
    )
    for voice in (state or {}).get("voices", []):
        label = ("✅ " if voice.get("selected") else "") + voice["name"]
        markup.add(types.InlineKeyboardButton(label, callback_data=f"rz:ctl:{user_id}:v{voice['index']}"))
    return markup


def media_request(path, payload):
    response = requests.post(MEDIA_SERVER_URL + path, json=payload, timeout=60)
    data = response.json()
    if response.status_code != 200:
        raise RuntimeError(data.get("error", "Помилка медіасервера"))
    return data


def send_media_started(chat_id, user_id, result):
    # Telegram needs a text label to attach the controls; the title is enough.
    message = result.get("title") or result["message"]
    bot.send_message(chat_id, message, reply_markup=rezka_controls_keyboard(user_id, result))


@bot.callback_query_handler(func=lambda call: call.data and call.data.startswith("rz:"))
def handle_rezka_callback(call):
    try:
        _, operation, owner, value = call.data.split(":", 3)
        if int(owner) != call.from_user.id:
            bot.answer_callback_query(call.id, "Це меню іншого користувача")
            return
        chat_id = call.message.chat.id
        key = (chat_id, call.from_user.id)
        if operation == "cancel":
            media_sessions.pop(key, None)
            bot.edit_message_reply_markup(chat_id, call.message.message_id, reply_markup=None)
            bot.answer_callback_query(call.id, "Пошук закрито")
            return
        if operation == "play":
            session = media_sessions.get(key)
            if not session or session["expires"] < time.monotonic():
                media_sessions.pop(key, None)
                raise RuntimeError("Результати пошуку вже застаріли. Виконайте пошук ще раз")
            items = session["items"]
            index = int(value)
            if not 0 <= index < len(items):
                raise RuntimeError("Немає такого результату")
            item = items[index]
            home = get_guest_home_by_chat(chat_id) or "0"
            result = media_request("/play", {
                "url": item["url"], "title": item["name"], "home": home, "quality": 1080,
                "season": session.get("season"), "episode": session.get("episode"),
            })
            send_media_started(chat_id, call.from_user.id, result)
            bot.answer_callback_query(call.id, "Запускаю")
            return
        controls = {
            "pause": ("pause", None), "back": ("back", None), "forward": ("forward", None),
            "q420": ("quality", 420), "q720": ("quality", 720), "q1080": ("quality", 1080),
            "voice": ("voice", None), "stop": ("stop", None),
            "next_episode": ("next_episode", None), "prev_episode": ("previous_episode", None),
        }
        if re.fullmatch(r"v\d+", value):
            action, argument = "voice_index", int(value[1:])
        else:
            action, argument = controls[value]
        result = media_request("/control", {"action": action, "value": argument})
        if "voices" in result:
            try:
                bot.edit_message_reply_markup(chat_id, call.message.message_id,
                    reply_markup=rezka_controls_keyboard(call.from_user.id, result))
            except telebot.apihelper.ApiTelegramException as exc:
                if "message is not modified" not in str(exc).lower():
                    raise
        bot.answer_callback_query(call.id, result["message"], show_alert=True)
    except Exception as exc:
        bot.answer_callback_query(call.id, str(exc)[:180], show_alert=True)


# ========== Обробка текстових повідомлень ==========
@bot.message_handler(content_types=['text'])
def handle_text(message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    guest_home = get_guest_home_by_chat(chat_id)

    if user_id in guest_registration:
        reg = guest_registration[user_id]
        reg_chat_id = reg.get("chat_id")
        if reg_chat_id and reg_chat_id != chat_id:
            bot.send_message(chat_id, "#Реєстрація доступна тільки в групі, де ви приєдналися")
            return

        step = reg["step"]

        if step == 0:
            reg["data"]["Днів"] = message.text
            reg["step"] += 1
            bot.send_message(chat_id, "#Вкажіть ціну за перебування:")
        elif step == 1:
            reg["data"]["Ціна"] = message.text
            reg["step"] += 1
            bot.send_message(chat_id, "#Підтвердіть згоду у випадку пошкодження майна компенсувати його (напишіть: ок)")
        elif step == 2:
            if message.text.lower() == "ок":
                reg["data"]["Підтвердження"] = "ок"
                save_guest_to_file(reg["data"])
                del guest_registration[user_id]
                name = reg["data"]["Ім'я"]
                bot.send_message(chat_id, "#✅ Реєстрація завершена! чат з помічником підключено.")
                final_welcome_cmd = (
                    f"помічник привітай ще раз {name}, подякуй за реєстрацію, "
                    f"представся як кіт Сіріус, скажи пароль Wi-Fi, щоб перейти у меню команд треба просто написати меню "
                    f"і поясни, що щоб включити світло в бані треба написати просто 'баня', "
                    f"а щоб задати питання — 'помічник ...', "
                    f"щоб завершити діалог — написати 'відміна'."
                )
                send_cmd_towebapi(final_welcome_cmd, message, user_name=name)
            else:
                bot.send_message(chat_id, "#Напишіть 'ок' для підтвердження")
        return

    # ===== Не реєстрація =====
    if not is_admin_chat(chat_id) and not guest_home and not is_admin_user(user_id):
        bot.send_message(chat_id, "Доступ заблоковано")
        return

    msg_text = (message.text or "").strip().lower()
    # Telegram group commands include a bot mention: /menu@Sirius_bot.
    msg_text = re.sub(r"^(/\w+)@\w+(?=\s|$)", r"\1", msg_text)

    if msg_text in {"відміна", "скасувати", "стоп", "/cancel"}:
        media_sessions.pop((chat_id, user_id), None)

    session = media_sessions.get((chat_id, user_id))
    if session and msg_text.isdecimal() and msg_text not in MEDIA_TEXT_COMMANDS:
        if session["expires"] < time.monotonic():
            media_sessions.pop((chat_id, user_id), None)
            bot.send_message(chat_id, "Результати пошуку вже застаріли. Виконайте пошук ще раз")
            return
        index = int(msg_text) - 1
        if not 0 <= index < len(session["items"]):
            bot.send_message(chat_id, "Оберіть номер зі списку знайдених фільмів")
            return
        try:
            item = session["items"][index]
            result = media_request("/play", {
                "url": item["url"], "title": item["name"],
                "home": resolve_home_for_message(message), "quality": 1080,
                "season": session.get("season"), "episode": session.get("episode"),
            })
            send_media_started(chat_id, user_id, result)
        except Exception as exc:
            bot.send_message(chat_id, str(exc))
        return

    if msg_text == "пошук" or msg_text.startswith("пошук ") or msg_text.startswith("знайди "):
        media_sessions.pop((chat_id, user_id), None)
        raw_query = re.sub(r"^(пошук|знайди)\s*", "", msg_text).strip()
        query, season, episode = parse_rezka_query(raw_query)
        if not query:
            bot.send_message(chat_id, "Після слова пошук напишіть назву фільму або серіалу")
            return
        try:
            items = rezka_search(query, 20 if season is not None else 5)
            if season is not None:
                items = [item for item in items if item.get("type") == "series"][:5]
            if not items:
                bot.send_message(chat_id, f"За запитом {query} нічого не знайдено")
                return
            media_sessions[(chat_id, user_id)] = {
                "items": items,
                "season": season,
                "episode": episode,
                "expires": time.monotonic() + 15 * 60,
            }
            suffix = f", сезон {season}, серія {episode}" if season is not None else ""
            lines = [f"Знайдено за запитом {query}{suffix}:"]
            for number, item in enumerate(items, start=1):
                label = CONTENT_TYPE_LABELS.get(item.get("type"), "Відео")
                lines.append(f"{number}. [{label}] {item['name']} ({item['year']})")
            bot.send_message(chat_id, "\n".join(lines), reply_markup=rezka_results_keyboard(user_id, len(items)))
        except Exception:
            bot.send_message(chat_id, "Не вдалося виконати пошук")
        return

    if msg_text in MEDIA_TEXT_COMMANDS:
        action, value = MEDIA_TEXT_COMMANDS[msg_text]
        try:
            result = media_request("/control", {"action": action, "value": value})
            bot.send_message(chat_id, result["message"])
        except Exception as exc:
            bot.send_message(chat_id, str(exc))
        return

    if msg_text in ["m", "меню", "/menu", "/start", "/help", "/"]:
        bot.send_message(
            chat_id,
            "Оберіть потрібну команду. Меню можна згорнути кнопкою клавіатури Telegram.",
            reply_markup=build_menu_markup(chat_id),
        )
        return

    if guest_home:
        normalized_cmd, err = normalize_guest_command(message.text, guest_home)
        if err:
            bot.send_message(chat_id, err)
            return
        send_cmd_towebapi(normalized_cmd, message)
        return

    # Preserve Smart Life codes; the core normalizes ordinary commands itself.
    send_cmd_towebapi(message.text, message)


# ========== Запуск ==========
print(f"Запущено Telegram-бот Сіріуса, URL: {baseUrl}")
threading.Thread(target=poll_notifications, daemon=True).start()
bot.infinity_polling()
