"""Public Sirius settings. Credentials belong in secrets.local.json."""

def start(core):
    return {
        "name": "Сіріус", "version": "1.0", "require_online": False,
        "default_options": {
            "assistant_name": "Сіріус",
            "about": "Я Сіріус. Допомагаю з командами, нагадуваннями та розумним будинком.",
            "context_seconds": 3,
            "fuzzy_threshold": 0.86,
            "fuzzy_margin": 0.08,
            "greetings": ["Привіт! Я Сіріус, твій помічник. Мяу!"],
            "unknown_reply": "Не знайшов команду. Уточни, будь ласка.",
            "context_reply": "Уточни відповідь або напиши «відміна».",
        },
    }

def start_with_options(core, manifest):
    return manifest
