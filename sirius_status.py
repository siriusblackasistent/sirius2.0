"""Read only this installation's cached device observations."""
from sirius_settings import BASE_DIR


def status_text(home):
    home = str(home)
    if home not in {"0", "30", "70"}:
        return "Невідомий будинок."
    path = BASE_DIR / "status" / f"house_{home}.txt"
    try:
        return path.read_text(encoding="utf-8").strip() or "Статус ще не отримано."
    except OSError:
        return f"Ще немає статусів будинку {home}. Дочекайся опитування або напиши «опитування {home}»."
