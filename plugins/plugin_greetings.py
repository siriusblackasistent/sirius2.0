


# Приветствие (и демо-плагин)
# author: Vladislav Janvarev (inspired by EnjiRouz)
from __future__ import print_function
from sirius_settings import BASE_DIR, get_secret
from sirius_status import status_text
import datetime
import googleapiclient
from google.oauth2 import service_account
from googleapiclient.discovery import build
import time
import subprocess
import socket

import random
from sirius_core import SiriusCore


# функция на старте
def start(core:SiriusCore):
    manifest = { # возвращаем настройки плагина - словарь
        "name": "Привет", # имя
        "version": "1.0", # версия
        "require_online": True, # требует ли онлайн?

        "description": "Демонстрационный плагин\n"
                       "Голосовая команда: привет|доброе утро",

        "commands": { # набор скиллов. Фразы скилла разделены | . Если найдены - вызывается функция
            "стрибай|добрий день|ты тупий|привіт|пострибай|потанцюй|покажи лапку": play_greetings,
            "злість|покажи гнів|гнів": play_angry,

            "інформація|что ты умеешь|ты кто?": play_info,
            "вайфай|интернет": play_wifi,
            "занотуй|відправ|повідомлення": send,
          
            "курс|долар|гривня": kurs,
            "постав на сигналізацію|двері|сигналізація": door,

            
            "статус|мій дім": status,


        }
    }
    return manifest
def play_info(core: SiriusCore, phrase: str):
    core.reply(core.plugin_options("core").get("about", "Я Сіріус. Допомагаю з командами, нагадуваннями та розумним будинком."))


def play_greetings(core: SiriusCore, phrase: str):
    greetings = core.plugin_options("core").get("greetings", ["Привіт! Я Сіріус."])
    core.reply(random.choice(greetings) if greetings else "Привіт! Я Сіріус.")
   
def play_angry(core, phrase: str): # в phrase находится остаток фразы после названия скилла,
                                      # если юзер сказав більше, 
                                      # в цьому плагіні не використовується

    # Випадкове привітання
    greetings = [
        "ох накладу купу на подушку 😡",
        "тікай кожаний 😡",
        "тобі котець шшш 😡",
    ]
    greet_str = greetings[random.randint(0, len(greetings) - 1)]
    
    # Відтворення обраної фрази
    core.reply(greet_str)





def play_wifi(core: SiriusCore, phrase: str):
    password = get_secret("wifi.password")
    core.reply("Пароль Wi-Fi: " + password if password else "Пароль Wi-Fi ще не налаштований.")





def send(core:SiriusCore, phrase: str):
    try:
    
        from notifiers import get_notifier

        bot_token = get_secret('plugin_greetings.send_bot_token')
        chatID = get_secret("telegram.send_chat_id")

        telegram = get_notifier("telegram")
        telegram.notify(token=bot_token, chat_id=chatID, message=phrase)
        core.reply(phrase)
    except:
        print("nof")
   
def kurs(core:SiriusCore, phrase: str):
    import requests
    # URL публічного API ПриватБанку для отримання інформації про курси валют
    url = 'https://api.privatbank.ua/p24api/pubinfo?exchange&coursid=5'

    # Надсилання HTTP запиту до API
    response = requests.get(url)

    # Перевірка на успішність запиту
    if response.status_code == 200:
        exchange_rates = response.json()

        # Шукаємо курс долара до гривні
        usd_to_uah_rate = next((rate for rate in exchange_rates if rate['ccy'] == 'USD'), None)
        if usd_to_uah_rate:
            core.reply(f"Курс купівлі USD до UAH: {usd_to_uah_rate['buy']}")
            core.reply(f"Курс продажу USD до UAH: {usd_to_uah_rate['sale']},Курс купівлі USD до UAH: {usd_to_uah_rate['buy']}")
        else:
            core.reply("Не знайдено курс для USD.")
    else:
        core.reply(f"Не вдалось отримати дані. Статус код: {response.status_code}")


def door(core: SiriusCore, phrase: str):
    """Ставимо будинок на сигналізацію та повідомляємо про це."""
    status_path = BASE_DIR / "status" / "alarm.txt"
    status_path.parent.mkdir(exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    try:
        with open(status_path, "a", encoding="utf-8") as file:
            file.write(f"{timestamp}: охорона увімкнена\n")
    except Exception as e:
        print("Door status write error:", e)
        core.reply("Не можу записати статус сигналізації.")
        return

    try:
        from notifiers import get_notifier

        bot_token = get_secret('plugin_greetings.door_bot_token')
        chat_id = get_secret("telegram.door_chat_id")
        telegram = get_notifier("telegram")
        telegram.notify(
            token=bot_token,
            chat_id=chat_id,
            message=f"Сигналізацію ввімкнено ({timestamp}).",
        )
    except Exception as e:
        print("Door telegram notify error:", e)

    core.reply("Сигналізація активована, мур.")








def status(core:SiriusCore, phrase: str):
    core.reply(status_text(core.session.metadata.get("home", "0")))
