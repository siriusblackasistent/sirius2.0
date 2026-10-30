from sirius_settings import BASE_DIR, media_python
"""HDRezka catalog commands for Sirius."""

import json
import subprocess

from sirius_core import SiriusCore


REZKA_PYTHON = media_python()
REZKA_CATALOG = str(BASE_DIR / "rezka_catalog.py")


def start(core: SiriusCore):
    return {
        "name": "HDRezka",
        "version": "1.1",
        "require_online": True,
        "description": "Пошук фільмів і серіалів на HDRezka",
        "commands": {
            "популярний|популярне|популярні|популярні фільми|що популярне|що подивитись": popular,
            "пошук|знайди": search,
        },
    }


def popular(core: SiriusCore, phrase: str):
    core.session.media_search = {"items": [], "season": None, "episode": None}
    try:
        process = subprocess.run(
            [REZKA_PYTHON, REZKA_CATALOG, "popular", "--limit", "5"],
            capture_output=True,
            text=True,
            timeout=40,
            check=True,
        )
        items = json.loads(process.stdout)
        if not items:
            core.reply("Популярних фільмів зараз не знайдено, мяу")
            return

        core.session.media_search = {"items": items, "season": None, "episode": None}

        lines = ["Зараз популярні:"]
        for number, item in enumerate(items, start=1):
            lines.append(f"{number}. {item['name']} ({item['year']})")
        core.reply("\n".join(lines))
    except (subprocess.SubprocessError, json.JSONDecodeError, KeyError, OSError):
        core.reply("Не вдалося отримати популярні фільми, мяу")


def search(core: SiriusCore, phrase: str):
    core.session.media_search = {"items": [], "season": None, "episode": None}
    query = phrase.strip()
    if not query:
        core.reply("Після слова пошук напишіть назву фільму або серіалу")
        return

    try:
        process = subprocess.run(
            [REZKA_PYTHON, REZKA_CATALOG, "search", query, "--limit", "5"],
            capture_output=True,
            text=True,
            timeout=40,
            check=True,
        )
        items = json.loads(process.stdout)
        if not items:
            core.reply(f"За запитом {query} нічого не знайдено, мяу")
            return

        core.session.media_search = {"items": items, "season": None, "episode": None}

        lines = [f"Знайдено за запитом {query}:"]
        for number, item in enumerate(items, start=1):
            lines.append(f"{number}. {item['name']} ({item['year']})")
        core.reply("\n".join(lines))
    except (subprocess.SubprocessError, json.JSONDecodeError, KeyError, OSError):
        core.reply("Не вдалося виконати пошук, мяу")
