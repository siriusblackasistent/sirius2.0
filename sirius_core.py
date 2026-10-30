"""Text command dispatcher, plugin registry and isolated Sirius conversations."""
from contextvars import ContextVar
from dataclasses import dataclass, field
from difflib import SequenceMatcher
import importlib.util
import logging
from pathlib import Path
from threading import RLock
import time
import secrets
import unicodedata

from sirius_settings import BASE_DIR, load_json
from router_memory import request_scope, remember_action, record_turn

log = logging.getLogger(__name__)

@dataclass
class Session:
    context: object = None
    expires: float = 0
    last_seen: float = field(default_factory=time.monotonic)
    messages: list = field(default_factory=list)
    history: list = field(default_factory=list)
    gpt_history: list = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    timers: list = field(default_factory=list)
    last_reply: str = ""
    pending_actions: dict = field(default_factory=dict)
    confirmation: object = None
    outbox: list = field(default_factory=list)
    media_search: object = None
    smartlife: dict = field(default_factory=dict)
    smartlife_result: object = None
    sensitive_input: bool = False

class SiriusCore:
    def __init__(self, root=BASE_DIR):
        self.root = Path(root)
        self.commands = {}
        self.toggle_aliases = set()
        self.scheduled_commands = {}
        self.schedule_homes = {}
        self.plugin_commands = {}
        self.plugin_manifests = {}
        self.fuzzy_processors = {}
        self.input_processors = {}
        self.plugin_errors = {}
        self.sessions = {}
        self._session = ContextVar("sirius_session")
        self._lock = RLock()
        self._routing = ContextVar("sirius_routing", default=False)

    @staticmethod
    def normalize(text):
        text = unicodedata.normalize("NFKC", text).casefold()
        return " ".join(text.replace("’", "'").replace("ʼ", "'").split())

    def init_with_plugins(self):
        self._load_plugin("core")
        for path in sorted((self.root / "plugins").glob("plugin_*.py")):
            if path.stat().st_size:
                self._load_plugin(path.stem)
        if (self.root / "plugins" / "smartlife.py").is_file():
            self._load_plugin("smartlife")

    def _load_plugin(self, name):
        try:
            spec = importlib.util.spec_from_file_location(
                "sirius_plugins." + name, self.root / "plugins" / (name + ".py"))
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            manifest = module.start(self)
            defaults = manifest.get("default_options", {})
            manifest["options"] = {**defaults, **load_json(self.root / "options" / (name + ".json"))}
            if hasattr(module, "start_with_options"):
                manifest = module.start_with_options(self, manifest) or manifest
            commands = manifest.get("commands", {})
            existing = {self.normalize(a) for k in self.commands for a in k.split("|")}
            incoming = [self.normalize(a) for k in commands for a in k.split("|")]
            if len(incoming) != len(set(incoming)) or existing.intersection(incoming):
                raise ValueError("Duplicate command alias in " + name)
            for init, _ in manifest.get("fuzzy_processor", {}).values():
                if init:
                    init(self)
            self.plugin_manifests[name] = manifest
            self.plugin_commands[name] = list(commands)
            self.commands.update(commands)
            for key, handler in commands.items():
                if handler in manifest.get("toggle_handlers", []):
                    self.toggle_aliases.update(self.normalize(alias) for alias in key.split("|"))
            for key, handler in manifest.get("scheduled_commands", {}).items():
                self.scheduled_commands[key] = handler
                self.schedule_homes[key] = manifest.get("schedule_homes", ["0"])
            self.fuzzy_processors.update(manifest.get("fuzzy_processor", {}))
            if manifest.get("input_handler"):
                self.input_processors[name] = manifest["input_handler"]
        except Exception as exc:
            self.plugin_errors[name] = type(exc).__name__
            log.warning("Plugin %s could not load (%s)", name, type(exc).__name__)

    def start_background_services(self):
        for name, manifest in self.plugin_manifests.items():
            if manifest.get("start_background"):
                try:
                    manifest["start_background"](self)
                except Exception as exc:
                    self.plugin_errors[name] = type(exc).__name__
                    log.warning("Plugin background start failed: %s", name)

    def stop_background_services(self):
        for name, manifest in self.plugin_manifests.items():
            if manifest.get("stop_background"):
                try:
                    manifest["stop_background"]()
                except Exception:
                    log.warning("Plugin background stop failed: %s", name)

    def plugin_options(self, name):
        return self.plugin_manifests.get(name, {}).get("options", {})

    def plugin_manifest(self, name):
        return self.plugin_manifests.get(name, {})

    def save_plugin_options(self, name, options):
        import json
        path = self.root / "options" / (name + ".json")
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(options, ensure_ascii=False, indent=2), encoding="utf-8")

    @property
    def session(self):
        return self._session.get()

    @property
    def context(self):
        session = self.session
        if session.context is not None and session.expires <= time.monotonic():
            self.context_clear()
            session.gpt_history.clear()
        return session.context

    @property
    def history(self):
        return self.session.history

    @property
    def gpt_history(self):
        return self.session.gpt_history

    @property
    def lastSay(self):
        return self.session.last_reply

    def reply(self, text):
        text = str(text).strip()
        if text:
            self.session.last_reply = text
            self.session.messages.append(text)

    def context_set(self, context, duration=None):
        self.session.context = context
        self.session.expires = time.monotonic() + (
            duration if duration is not None else self.plugin_options("core").get("context_seconds", 3))

    def context_clear(self):
        self.session.context = None
        self.session.expires = 0

    def find_best_cmd_with_fuzzy(self, command, context, allow_rest_phrase=True, threshold=None):
        command = self.normalize(command)
        if context is self.commands:
            prefix, _, tail = command.partition(" ")
            if prefix in {"увімкни", "увімкнути", "вимкни", "вимкнути", "включи", "включити",
                          "виключи", "виключити", "включить", "выключи", "выключить"} and tail in self.toggle_aliases:
                command = tail
        candidates = []
        for keyall in context:
            for alias in keyall.split("|"):
                alias = self.normalize(alias)
                if not alias or alias.startswith("__"):
                    continue
                if command == alias:
                    return keyall, 1.0, ""
                if allow_rest_phrase and command.startswith(alias + " "):
                    candidates.append((len(alias), keyall, command[len(alias):].strip()))
        if candidates:
            _, key, rest = max(candidates)
            return key, 1.0, rest
        threshold = threshold if threshold is not None else self.plugin_options("core").get("fuzzy_threshold", .86)
        scores = {}
        words = command.split()
        for keyall in context:
            for alias in keyall.split("|"):
                alias = self.normalize(alias)
                if len(alias) < 4 or alias.startswith("__"):
                    continue
                count = len(alias.split())
                prefix = " ".join(words[:count]) if allow_rest_phrase else command
                score = SequenceMatcher(None, prefix, alias).ratio()
                rest = " ".join(words[count:]) if allow_rest_phrase else ""
                if score > scores.get(keyall, (0, ""))[0]:
                    scores[keyall] = score, rest
        ranked = sorted(((score, key, rest) for key, (score, rest) in scores.items()), reverse=True)
        if ranked and ranked[0][0] >= threshold:
            if len(ranked) > 1 and ranked[0][0] - ranked[1][0] < self.plugin_options("core").get("fuzzy_margin", .08):
                self.reply("Уточни команду: " + " або ".join(item[1].split("|")[0] for item in ranked[:2]) + "?")
                return "__clarify__", 1.0, ""
            score, key, rest = ranked[0]
            return key, score, rest
        return None

    def execute_next(self, command, context=None):
        context = self.commands if context is None else context
        if not isinstance(context, dict):
            self.context_clear()
            result = context[0](self, command, context[1]) if isinstance(context, tuple) else context(self, command)
            if isinstance(result, str):
                self.reply(result)
            return
        result = self.find_best_cmd_with_fuzzy(command, context)
        if result:
            key, _, rest = result
            if key != "__clarify__":
                self.execute_next(rest, context[key])
                if context is self.commands and key not in self.plugin_commands.get("plugin_fuzzy_gpt_router", []):
                    remember_action(key.split("|")[0] + (" " + rest if rest else ""))
            return
        # Legacy GPT fallback is kept separate from the local similarity search.
        if context is self.commands and not self._routing.get():
            token = self._routing.set(True)
            try:
                for _, predict in self.fuzzy_processors.values():
                    if predict(self, command, dict(context), True) is not None:
                        return
            finally:
                self._routing.reset(token)
        option = "context_reply" if self.context is not None else "unknown_reply"
        self.reply(self.plugin_options("core").get(option, "Не знайшов команду."))

    def handle_message(self, text, chat_id, user_id, **metadata):
        # Serializing handlers also protects existing synchronous device plugins.
        with self._lock, request_scope(chat_id, user_id, f"guest:{id(self)}"):
            now = time.monotonic()
            for key, old in list(self.sessions.items()):
                if not old.timers and not old.outbox and now - old.last_seen > 86400:
                    del self.sessions[key]
            session = self.sessions.setdefault((str(chat_id), str(user_id)), Session())
            token = self._session.set(session)
            try:
                session.messages = []
                session.confirmation = None
                session.media_search = None
                session.smartlife_result = None
                session.sensitive_input = False
                session.last_reply = ""
                if now - session.last_seen >= self.plugin_options("core").get("context_seconds", 3):
                    self.context_clear()
                    session.gpt_history.clear()
                session.last_seen = now
                session.metadata = {**metadata, "chat_id": str(chat_id), "user_id": str(user_id)}
                raw_text = text
                text = self.normalize(text)
                handled = any(handler(self, raw_text) for handler in self.input_processors.values())
                if handled:
                    pass
                elif text in {"відміна", "скасувати", "стоп", "/cancel"}:
                    self.context_clear()
                    session.gpt_history.clear()
                    for proposal in session.pending_actions.values():
                        if proposal["status"] == "pending":
                            proposal["status"] = "cancelled"
                    self.reply("Діалог завершено. Можна писати нову команду.")
                else:
                    self.execute_next(text, self.context)
                result = {"text": "\n".join(session.messages), "context_active": self.context is not None}
                if session.confirmation:
                    result["confirmation"] = session.confirmation
                if session.media_search is not None:
                    result["media_search"] = session.media_search
                if session.smartlife_result is not None:
                    result["smartlife"] = session.smartlife_result
                return result
            except Exception as exc:
                log.warning("Command failed (%s)", type(exc).__name__)
                return {"text": "Не вдалося виконати команду. Спробуй ще раз.", "context_active": self.context is not None, "error": "command_failed"}
            finally:
                if not session.sensitive_input:
                    record_turn(text, "\n".join(session.messages))
                self._session.reset(token)

    def set_timer(self, duration, callback, timerFuncUpd=None):
        self.session.timers.append((time.monotonic() + duration, callback))
        return len(self.session.timers) - 1

    def propose_action(self, seconds, command, when):
        """Resolve an opted-in action now; confirmation never reinterprets its text."""
        home = str(self.session.metadata.get("home", "0"))
        allowed = {key: handler for key, handler in self.scheduled_commands.items()
                   if home in self.schedule_homes.get(key, ["0"])}
        match = self.find_best_cmd_with_fuzzy(command, allowed, threshold=1.0)
        if match is None or match[0] == "__clarify__" or match[2]:
            self.reply("Не знайшов доступної відкладеної дії. Напиши точну команду, наприклад «вимкни все світло» або «включи обігрів».")
            return
        now = time.monotonic()
        self.session.pending_actions = {key: item for key, item in self.session.pending_actions.items()
                                        if item["retain_until"] > now}
        if len(self.session.pending_actions) >= 100:
            self.reply("Забагато запланованих дій. Спробуй пізніше.")
            return
        key, _, rest = match
        ident = secrets.token_hex(8)
        proposal = {
            "status": "pending", "deadline": now + seconds,
            "expires": min(now + 300, now + seconds),
            "retain_until": now + seconds + 86400,
            "handler": allowed[key], "phrase": rest,
            "command": command, "when": when,
            "metadata": dict(self.session.metadata),
        }
        self.session.pending_actions[ident] = proposal
        self.session.confirmation = {"id": ident, "command": command, "when": when}
        self.reply(f"Відкладена дія: {command}\nКоли: {when}\nПідтвердь протягом 5 хвилин і до зазначеного часу. Таймер ще не встановлено.")

    def decide_action(self, chat_id, user_id, ident, decision):
        with self._lock:
            session = self.sessions.get((str(chat_id), str(user_id)))
            proposal = session.pending_actions.get(ident) if session else None
            if proposal is None:
                return {"ok": False, "text": "Це завдання іншого користувача або воно вже недоступне."}
            if decision not in {"confirm", "cancel"}:
                return {"ok": False, "text": "Невідома дія."}
            status = proposal["status"]
            if status != "pending":
                labels = {"scheduled": "Таймер уже встановлено.", "cancelled": "Завдання скасовано.",
                          "expired": "Час підтвердження минув. Створи завдання знову.",
                          "executed": "Дію вже запущено."}
                return {"ok": True, "text": labels[status]}
            if time.monotonic() >= proposal["expires"]:
                proposal["status"] = "expired"
                return {"ok": True, "text": "Час підтвердження минув. Створи завдання знову."}
            if decision == "cancel":
                proposal["status"] = "cancelled"
                return {"ok": True, "text": "Відкладену дію скасовано."}
            proposal["status"] = "scheduled"
            session.timers.append((proposal["deadline"], (self._execute_action, proposal)))
            return {"ok": True, "text": f"Таймер встановлено: {proposal['when']} — {proposal['command']}"}

    @staticmethod
    def _execute_action(core, proposal):
        proposal["status"] = "executed"
        core.session.metadata = proposal["metadata"]
        core.reply("Запускаю відкладену команду: " + proposal["command"])
        core.execute_next(proposal["phrase"], proposal["handler"])

    def run_due_timers(self):
        """The server invokes this independently of Telegram polling."""
        with self._lock:
            now = time.monotonic()
            for session in list(self.sessions.values()):
                due = [item for item in session.timers if item[0] <= now]
                session.timers = [item for item in session.timers if item[0] > now]
                if not due:
                    continue
                token = self._session.set(session)
                saved = (session.messages, session.context, session.expires, session.metadata,
                         session.last_reply, session.confirmation)
                try:
                    for _, callback in due:
                        session.messages = []
                        try:
                            if isinstance(callback, tuple):
                                callback[0](self, callback[1])
                            else:
                                callback(self)
                        except Exception as exc:
                            log.warning("Scheduled action failed (%s)", type(exc).__name__)
                            self.reply("Не вдалося виконати відкладену дію. Перевір пристрій.")
                        session.outbox.extend(session.messages)
                finally:
                    (session.messages, session.context, session.expires, session.metadata,
                     session.last_reply, session.confirmation) = saved
                    self._session.reset(token)

    def notifications(self, chat_id, user_id):
        self.run_due_timers()
        with self._lock:
            session = self.sessions.get((str(chat_id), str(user_id)))
            if session is None:
                return []
            result = list(session.outbox)
            session.outbox.clear()
            return result
