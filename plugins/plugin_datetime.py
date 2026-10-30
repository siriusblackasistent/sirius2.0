
"""Котячий годинник Sirius — лише години та хвилини."""

from datetime import datetime
from zoneinfo import ZoneInfo

KYIV = ZoneInfo("Europe/Kyiv")

# Години від 00 до 23
HOURS = (
    "нулльова",                 # 00
    "перрша",                  # 01
    "друрга",                  # 02
    "трретя",                  # 03
    "чотирлапа",               # 04
    "п’ямта",                 # 05
    "шшоста",                  # 06
    "сссьома",                # 07
    "вуссьма",                # 08
    "дев’мята",               # 09
    "десямта",                # 10
    "одинадцямта",            # 11
    "мурнадцята",             # 12
    "тринадцямта",            # 13
    "чотирнадцямта",          # 14
    "п’ятнадцямта",           # 15
    "шерстнадцята",           # 16
    "сімнадцямта",            # 17
    "вісімнадцямта",          # 18
    "дев’ятнадцямта",         # 19
    "двадцямта",              # 20
    "двадцять перрша",        # 21
    "двадцять друрга",        # 22
    "двадцять трретя",        # 23
)

# Числа хвилин від 0 до 19
ONES = (
    "нулль",
    "один",
    "два",
    "три",
    "чотири",
    "п’ять",
    "шшшість",
    "сссім",
    "вусім",
    "дев’мять",
    "десямть",
    "одинадцямть",
    "мурнадцять",
    "тринадцямть",
    "чотирнадцямть",
    "п’ятнадцямть",
    "шерстнадцять",
    "сімнадцямть",
    "вісімнадцямть",
    "дев’ятнадцямть",
)

TENS = {
    20: "двадцять",
    30: "тридцять",
    40: "сорок",
    50: "п’ятдесят",
}


def start(core):
    return {
        "name": "Котячий годинник Sirius",
        "version": "3.0",
        "require_online": False,
        "commands": {
            "час|яка година|котра година|скільки часу|время":
                play_time,
        },
    }


def minute_words(value):
    """Котяча вимова хвилин від 00 до 59."""
    if not 0 <= value <= 59:
        raise ValueError("Хвилини повинні бути від 0 до 59")

    # 00 -> нулль нулль
    if value == 0:
        return "нулль нулль"

    # 01 -> нулль один, 09 -> нулль дев’мять
    if value < 10:
        return "нулль " + ONES[value]

    if value < 20:
        return ONES[value]

    tens, units = divmod(value, 10)
    result = TENS[tens * 10]

    if units:
        result += " " + ONES[units]

    return result


def format_time(current):
    """Коротка котяча вимова часу."""
    hour = HOURS[current.hour]
    minute = minute_words(current.minute)

    return f"{hour} {minute}"


def play_time(core, phrase):
    current = datetime.now(KYIV)
    core.reply(format_time(current))
