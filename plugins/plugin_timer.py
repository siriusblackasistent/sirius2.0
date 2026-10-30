import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

def start(core):
    return {"name": "Таймер", "version": "1.2", "commands": {"таймер|нагадай": timer, "через": delayed, "будильник|о": alarm}}

# Cardinal numbers used in durations; the rest of the command is not rewritten.
ONES = {
    "нуль": 0, "ноль": 0, "один": 1, "одна": 1, "одну": 1, "одне": 1,
    "два": 2, "дві": 2, "две": 2, "три": 3, "чотири": 4, "четыре": 4,
    "п'ять": 5, "пять": 5, "шість": 6, "шесть": 6, "сім": 7, "семь": 7,
    "вісім": 8, "восемь": 8, "дев'ять": 9, "девять": 9,
}
TEENS = dict(zip(
    "десять одинадцять дванадцять тринадцять чотирнадцять п'ятнадцять шістнадцять сімнадцять вісімнадцять дев'ятнадцять".split(), range(10, 20)))
TEENS.update(zip(
    "десять одиннадцать двенадцать тринадцать четырнадцать пятнадцать шестнадцать семнадцать восемнадцать девятнадцать".split(), range(10, 20)))
TENS = dict(zip("двадцять тридцять сорок п'ятдесят шістдесят сімдесят вісімдесят дев'яносто".split(), range(20, 100, 10)))
TENS.update(zip("двадцать тридцать сорок пятьдесят шестьдесят семьдесят восемьдесят девяносто".split(), range(20, 100, 10)))
HUNDREDS = dict(zip("сто двісті триста чотириста п'ятсот шістсот сімсот вісімсот дев'ятсот".split(), range(100, 1000, 100)))
HUNDREDS.update(zip("сто двести триста четыреста пятьсот шестьсот семьсот восемьсот девятьсот".split(), range(100, 1000, 100)))


def normalize_duration(text):
    return " ".join(text.casefold().replace("’", "'").replace("ʼ", "'").replace("`", "'").split())


def parse_number(text):
    """Digits or cardinal words from zero to 999; malformed sequences return None."""
    text = normalize_duration(text)
    if re.fullmatch(r"[0-9]{1,5}", text):
        return int(text)
    words = text.split()
    if not words:
        return None
    if len(words) == 1 and words[0] in ONES:
        return ONES[words[0]]
    value = 0
    if words[0] in HUNDREDS:
        value += HUNDREDS[words.pop(0)]
    if words and words[0] in TEENS:
        value += TEENS[words.pop(0)]
        return value if not words else None
    if words and words[0] in TENS:
        value += TENS[words.pop(0)]
    if words and words[0] in ONES and ONES[words[0]] > 0:
        value += ONES[words.pop(0)]
    return value if not words else None


def parse_duration(phrase):
    phrase = normalize_duration(phrase)
    phrase = re.sub(r"^на\s+", "", phrase)
    match = re.fullmatch(
        r"(.+?)\s*(секунд[аиуы]?|сек|с|хвилин[аиу]?|хв|минут[ауы]?|мин|годин[аиу]?|год|час(?:а|ов)?)?", phrase)
    if not match:
        return None
    number = parse_number(match[1])
    if number is None or not 0 < number <= 10000:
        return None
    unit = match[2] or "хв"
    multiplier = 1 if unit.startswith("с") else 3600 if unit.startswith(("год", "час")) else 60
    return number * multiplier


def timer(core, phrase):
    phrase = normalize_duration(phrase)
    clock = re.fullmatch(r"(?:о\s*)?(\d{1,2}):(\d{2})", phrase)
    if clock and int(clock[1]) < 24 and int(clock[2]) < 60:
        now = datetime.now()
        target = now.replace(hour=int(clock[1]), minute=int(clock[2]), second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        seconds = (target - now).total_seconds()
    else:
        seconds = parse_duration(phrase)
        if seconds is None:
            core.reply("На скільки? Напиши, наприклад, «п’ять хвилин», «30 секунд» або «о 18:30».")
            core.context_set(timer)
            return
    core.set_timer(seconds, (finished, phrase))
    core.reply("Таймер встановлено: " + phrase)

def finished(core, description):
    core.reply("Час минув: " + description + ". Мяу!")


def delayed(core, phrase):
    phrase = normalize_duration(phrase)
    words = phrase.split()
    for count in range(len(words) - 1, 0, -1):
        seconds = parse_duration(" ".join(words[:count]))
        if seconds is not None:
            when = datetime.now(ZoneInfo("Europe/Kyiv")) + timedelta(seconds=seconds)
            core.propose_action(seconds, " ".join(words[count:]), when.strftime("%d.%m.%Y %H:%M:%S") + " (Київ)")
            return
    core.reply("Напиши, наприклад: «через 5 хв вимкни все світло».")


def alarm(core, phrase):
    phrase = normalize_duration(phrase)
    match = re.fullmatch(r"(?:о\s+)?(\d{1,2})(?::|\s+)(\d{1,2})\s+(.+)", phrase)
    if not match or int(match[1]) > 23 or int(match[2]) > 59:
        core.reply("Напиши, наприклад: «будильник о 8 45 включи обігрів» або «о 08:45 включи обігрів».")
        return
    now = datetime.now(ZoneInfo("Europe/Kyiv"))
    target = now.replace(hour=int(match[1]), minute=int(match[2]), second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    seconds = target.timestamp() - now.timestamp()
    core.propose_action(seconds, match[3], target.strftime("%d.%m.%Y %H:%M:%S") + " (Київ)")
