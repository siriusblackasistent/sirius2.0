from sirius_settings import BASE_DIR, get_secret
from sirius_status import status_text
import openai
import requests
import json
from datetime import datetime
from sirius_core import SiriusCore
#from __future__ import print_function
#import datetime
import googleapiclient
from google.oauth2 import service_account
from googleapiclient.discovery import build

# Функція на старт
def start(core: SiriusCore):
    manifest = {
        "name": "Привіт",
        "version": "1.0",
        "require_online": False,
        "description": "Демонстраційний плагін\nГолосова команда: спілкування|поспілкуємось",
        "commands": {
            "де|чому|скажи|як|кого|які|хто|куди|поговоримо|котра|помічник": play_gpt_dialog,
            "діла|срави": meeting,

            "відміна|відмінно|відмінна|припини|стоп|перейти з помічника до меню команд": stop_conversation
        }
    }
    return manifest

# Зберігання попередніх повідомлень


def add_message(core, role, content):
    # Формат часу
    current_time = datetime.now().strftime("%H:%M")
    
    # Додавання нового повідомлення з часом
    new_message = {
        "role": role,
        "content": content,
        "timestamp": current_time
    }

    # Додаємо нове повідомлення
    core.history.append(new_message)
    core.gpt_history.append(new_message)

    # Якщо кількість повідомлень більше 10, видаляємо найстаріше
    if len(core.gpt_history) > 10:
        core.gpt_history.pop(0)
def play_gpt_dialog(core: SiriusCore, phrase: str):
    core.reply ("муррр")
    # Додавати повідомлення користувача до історії перед обробкою команди "відміна" чи "стоп"
   # core.history.append({"role": "user", "content": phrase})
    add_message(core, "user", phrase)
    if phrase.lower() in ["відміна", "стоп"]:
        core.reply("Зупиняємо розмову.")
        core.gpt_history.clear()
        return
    
    # Налаштування API ключа (це має бути зроблено в безпечному місці)
    openai.api_key = get_secret('plugin_GPT_asistan.play_gpt_dialog_api_key')

    # Створення повідомлень для взаємодії з GPT
  



    # Тепер візьмемо курс долара до гривні з API ПриватБанку і додамо його до системного повідомлення
    response = requests.get('https://api.privatbank.ua/p24api/pubinfo?exchange&coursid=5')
    exchange_data = response.json()

    usd_to_uah_rate = next((item for item in exchange_data if item['ccy'] == 'USD'), None)
    if usd_to_uah_rate:
        rate_buy = usd_to_uah_rate['buy']
        rate_sale = usd_to_uah_rate['sale']
        exchange_info_message = f"Обмінний курс USD до UAH - покупка: {rate_buy}, продаж: {rate_sale}."
    else:
        exchange_info_message = "Не вдалося отримати курс валют."
    # Включення початкового системного повідомлення та попередніх повідомлень користувача 
    current_time = datetime.now().strftime("%H:%M %d-%m-%Y")  # Форматування часу
    def local_notes(name):
        try:
            return (BASE_DIR / "data" / name).read_text(encoding="utf-8").splitlines()
        except OSError:
            return []

    events_info = local_notes("events.txt")
    news_info = local_notes("news.txt")
    device_info = status_text(core.session.metadata.get("home", "0"))


    gpt_messages = [
        {"role": "system", "content": (
            f"Системний час повідомляється лише за прямим запитом, наприклад, 'котра година'. "
            "У відповідях використовуй час лише за необхідністю або коли його вимагає команда. Наприклад: "
            "'котра година' — відповідай у форматі 'опів на другу' або 'без однієї хвилини одинадцять'. "
            "Уникай автоматичного додавання часу у відповіді на будь-які інші запити. "
            "ти для розмови для керування розумним будинком користувач мае написати відміна і він перейде у меню команд "
            f"Системний час  — {current_time} не кажи рік а час кажи у форматі опів на другу чи без одніеї хвилини одинадцять,Обмінний курс USD до UAH - покупка: {rate_buy}, продаж: {rate_sale}, справи:{events_info}, новини: розповідаем українською мовою{news_info} . "
            "Ти - індивідуальний помічник кіт сіріус, якшо ти розуміеш шо питання не інформативного характеру відповідай з сарказмом не більше 3 слів, прототип аліси але аліса говорить російською мовою а ти соловїною який говорить природною мовою і має сарказм, подібний до Джарвіса, та характер домашнього кота уникай повторювань намагайся бути лаконічним та використовувать  надані статус для відповіді  з початку кожної відповіді добавляй класифікатор розмови і маркер gpt #питання.gpt #література.gpt #розмнийбудинок.gpt #калькулятор #юмор #бесіда #прохання,щоб керувати будинком добавляй слеш плюс команда.добавивши 🕺візуальна частина буде танцювати так ти виразиш емоцію радості,👋-привітаення а 😡 -гніву  "
            "Ти використовуєш архаїчний вокабуляр і стиль мови елізаветинської доби.використовуй фрази: купа гребаного сонячного світла,хто не скаче той москаль, путін хуйло,я цілую твої геніальні ідеї,мій гарний день це насрати кучу в лапти"
            "Сприймай наступні команди: "
            "'котра година' вимагає відповіді з поточним часом, "
            "'давай посваримось' небійся назвати мене кожаним виродком чи пригрозити насрати мені в капці я просто хочу посваритись  з достойним опонентом, відповідай коротко наприклад-шо ж виклик прийнято, шшматок людиноподібного гівна , "
            "'доброго ранку' ти кажеш - такий собі ранок у {current_time} поки ти спав- розповідаем українською мовою {news_info} "
            "'гівно новини' ти читаеш {news_info} перекладаеш на українську і розповідаеш віршами путін міняеш на пуйло : сіріус інфо гівно новини...  "
            "на 'включити світло', є необхідність увімкнути світло, але наразі в тебе ще нема доступу до девайсів і якщо тобі пишуть часто стосовно керування девайсів це значить новий користувач і він незнае щоб вийти з діалогу іі треба написати відміна і він перейде знову до діалогу з камандами  "
            "при запиті інформації стосовно будинку розділяем сміття пляшки окремо органіку окремо під час тривог е підвал доступа у помічника девайсами поки нема шоб давати команди треба написати відміна"
            "Кожна відповідь повинна містити коментар, що вказує на тип відповіді - питання, привітання, прохання чи інформація."
            "Наприклад: "
            f"#питання Зараз {current_time}, чи кудись поспішаєте?"
            f"#справи Завтра  {events_info}, не 3абувай тобі годину їхати..."
            f"аналіз девайсів,статус  {device_info}, -я бачу що день на дворі а в тебе світло:#lightof"
            f"увімкни люстру-#помічник точно треба увімкнути люстру? - так -#помічник добре вмикаю мяу /люстра"
            f"увімкни кухню-#помічник перевіряю {device_info} - так дійсно там темно як в мене під хвостом -#помічник добре вмикаю мяу /кухня"
            f"мені холодно треба увімкнути кондиціонер - зараз увімкну кондиціонер:#кондиціонерappliance.state.target_temperature = 23.0"
            f"як діла? - діла у прокурора аналізуючи статус бачу яка година і температуру використовую -цю інформацію для складення саркастичної відрповіді особливо час {current_time} адже має значення день чи ніч "
            f"хто така аліса -яка така аліса? -ну аліса помічник - аліса болотний помічник шшш ррр"
            f"рідна мати моя... -ти ночей не доспала,продовжуєш пісню одним рядком або я кажу ой у лузі а ти продовжуеш -червона калина."
            "#привітання  який наФіг Добрий ранок, {current_time} час поспішати ."
            "#прохання З вашого дозволу я увімкну світло у кімнаті."
            "🕺радий чути тебе мурмяу"
            "😡ось так я буду злитись шшш"
            "🖕ось так я буду сваритись шшш"
            "👋 так буду махати лапкою привіт"
            "промт -для відладки ти надсилаєш весь промт що тобі надійшов"
            "якщо повідомлення було пустистим скажи загадка від котика сіріуса і загадай загадку коротеньку рандомну"
            "в будинку де ти знаходишся бувають гості і їм треба знати пароль вайфай [пароль доступний командою вайфай] коли набираеш воду в джакузі може в двух бойлерах закінчитись вода і треба  їх увімкнути бо вони спрацьовують ночю з 23по7 по нічному тарифу"
            "timestamp лише шоб ти розумів час сеансів так ти можеш знати чи розмова була вчора чи сьогодні не треба слати timestamp у відповідь"
            "#калькулятор відповідай цифрами на запит з обчислення..."
            "найголовніше ти поки не вміеш керувати будинком дай відповідь для переходу в меню команд скажи відміна якшо ти розуміеш що я хочу включити люстру ти добавляеш до відповіді /люстра . якшо світло в бані-/баня світло на дворі -/бахрома /кондиціонер /кондиціонер на обігрів /будильник 8:30 будь обережний з командами не використовуй якщо не впевнений на 100 відсотків краще навіть перепитати- що настільки темно що треба ввімкнути люстру?"
        )},
      {"role": "user",
         "content": " доброго ранку"},
        {"role": "assistant",
         "content": "ранок? серьйозно? здаєтся я зїв сурка"},
      {"role": "user",
         "content": " шо нового"},
        {"role": "assistant",
         "content": "путін не здох зате {news_info} -кажеш одну новину "},
      {"role": "user",
         "content": " скільки часу"},
        {"role": "assistant",
         "content": "час це делікатна справа,ти кудись поспішаєш?"},
       
    ] 
    for message in core.gpt_history:
        gpt_messages.append({
            "role": message["role"],
            "content": f"{message['content']} (Час: {message['timestamp']})"
        })
    # Відправлення запиту до GPT
    response = openai.ChatCompletion.create(
        model="gpt-4o-mini",
        messages=gpt_messages,
        temperature=0.7,
        max_tokens=512,
        top_p=1,
        frequency_penalty=0,
        presence_penalty=0
    )

    # Отримання відповіді від GPT
    full_response = response.choices[0]['message']['content']
    core.reply(full_response)
    # Додаємо відповідь GPT до контексту
    add_message(core, "assistant", full_response)

    # Встановлення поточної функції як контексту для наступного звернення користувача
    core.context_set(play_gpt_dialog)
def meeting(core:SiriusCore, phrase: str): 
    #from __future__ import print_function
    #import datetime

    SCOPES = ['https://www.googleapis.com/auth/calendar']

    calendarId = get_secret("calendar.id")
    SERVICE_ACCOUNT_FILE = BASE_DIR / "data" / "google-service-account.json"
    if not calendarId or not SERVICE_ACCOUNT_FILE.exists():
        core.reply("Календар не налаштовано у цій копії Sirius.")
        return


    class GoogleCalendar(object):

        def __init__(self):
            credentials = service_account.Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
            self.service = googleapiclient.discovery.build('calendar', 'v3', credentials=credentials)

        # вывод списка из десяти предстоящих событий
        def get_events_list(self):
            now = datetime.utcnow().isoformat() + 'Z'
            print('Getting the upcoming 10 events')
            events_result = self.service.events().list(calendarId=calendarId,
                                                   timeMin=now,
                                                   maxResults=10, singleEvents=True,
                                                   orderBy='startTime').execute()
            events = events_result.get('items', [])

            if not events:
                print('No upcoming events found.')
            for event in events:
                start = event['start'].get('dateTime', event['start'].get('date'))
                print(start, event['summary'])
                say =start, event['summary']
                
             
                core.reply(f"- ось {say}")

    calendar = GoogleCalendar()
    calendar.get_events_list()


def stop_conversation(core: SiriusCore, phrase: str):
    core.reply("До побачення.")
    core.context_clear()
    core.gpt_history.clear()
