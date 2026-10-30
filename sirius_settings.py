"""Local configuration for Sirius; importing this module performs no writes."""
import json
import os
import runpy
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

def media_python():
    """Use the configured interpreter, or the same environment as Sirius."""
    configured = get_settings("media").get("python")
    return str((BASE_DIR / configured).absolute()) if configured else sys.executable

def load_json(path, default=None):
    path = Path(path)
    if not path.exists():
        return {} if default is None else default
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)

def get_settings(section=None):
    """Only core.py is loaded; the extensionless core is a blank template."""
    path = BASE_DIR / "core.py"
    settings = runpy.run_path(str(path))["SETTINGS"] if path.exists() else {}
    return settings if section is None else settings.get(section, {})


def server_config():
    """Use the same guest address for API and clients, including blank templates."""
    config = {**load_json(BASE_DIR / "server.local.json"), **get_settings("server")}
    config["host"] = config.get("host") or "127.0.0.1"
    config["port"] = int(config.get("port") or 5003)
    host = "127.0.0.1" if config["host"] in {"0.0.0.0", "::"} else config["host"]
    if ":" in host:
        host = f"[{host}]"
    config["baseUrl"] = config.get("baseUrl") or f"http://{host}:{config['port']}"
    return config


SECRET_PATHS = {
    "plugin_greetings.midea_account": "midea.30.account",
    "plugin_greetings.midea_password": "midea.30.password",
    "PARSER_DEVICEC.midea_account": "midea.30.account",
    "PARSER_DEVICEC.midea_password": "midea.30.password",
    "plugin_GPT_asistan.play_gpt_dialog_api_key": "gpt.api_key",
    "plugin_fuzzy_gpt_router.openai_api_key": "gpt.router_api_key",
    "plugin_fuzzy_gpt_router.tg_bot_token": "telegram.router_bot_token",
    "plugin_greetings.send_bot_token": "telegram.send_bot_token",
    "plugin_greetings.door_bot_token": "telegram.door_bot_token",
    "rezka_play.login": "rezka.login",
    "rezka_play.password": "rezka.password",
    "rezka_login_test.login": "rezka.login",
    "rezka_login_test.password": "rezka.password",
}


def get_secret(name, default=""):
    """Environment overrides core.py; old secrets remain a migration fallback."""
    env_name = "SIRIUS_" + name.replace(".", "_").upper()
    if env_name in os.environ:
        return os.environ[env_name]
    data = get_settings()
    for part in SECRET_PATHS.get(name, name).split("."):
        if not isinstance(data, dict) or part not in data:
            break
        data = data[part]
    else:
        return data
    data = load_json(BASE_DIR / "secrets.local.json")
    for part in name.split("."):
        if not isinstance(data, dict) or part not in data:
            return default
        data = data[part]
    return data
