#!/usr/bin/env python3
"""Local HDRezka/VLC media controller."""

import asyncio
import json
import logging
import os
from pathlib import Path
import re
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from bs4 import BeautifulSoup
from hdrezka import HDRezkaClient
from sirius_settings import get_secret, get_settings

MEDIA_SETTINGS = get_settings("media")
HOST = MEDIA_SETTINGS.get("host") or "127.0.0.1"
PORT = int(MEDIA_SETTINGS.get("port") or 8766)
RC_HOST = MEDIA_SETTINGS.get("rc_host") or "127.0.0.1"
RC_PORT = int(MEDIA_SETTINGS.get("rc_port") or 4213)
PAUSE_TIMEOUT = 30 * 60
log = logging.getLogger(__name__)


def playback_home(payload):
    """Both the main chat and house 30 use this machine's HDMI output."""
    home = str(payload.get("home"))
    if home not in {"0", "30"}:
        raise RuntimeError("HDMI-відтворення доступне тільки для будинків 0 і 30")
    return home


def vlc_environment():
    env = os.environ.copy()
    runtime = env.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    env["XDG_RUNTIME_DIR"] = runtime
    env["DISPLAY"] = MEDIA_SETTINGS.get("display") or env.get("DISPLAY") or ":0"
    env["DBUS_SESSION_BUS_ADDRESS"] = env.get("DBUS_SESSION_BUS_ADDRESS") or f"unix:path={runtime}/bus"
    # Do not advertise a Wayland socket that does not exist in an X11 session.
    wayland = env.get("WAYLAND_DISPLAY", "wayland-0")
    if (Path(runtime) / wayland).exists():
        env["WAYLAND_DISPLAY"] = wayland
    else:
        env.pop("WAYLAND_DISPLAY", None)
    auth = MEDIA_SETTINGS.get("xauthority") or env.get("XAUTHORITY")
    if not auth:
        auth = next((str(path) for path in (
            Path(runtime) / "gdm/Xauthority", Path.home() / ".Xauthority") if path.is_file()), None)
    if auth:
        env["XAUTHORITY"] = str(auth)
    return env


def credentials():
    login = os.environ.get("REZKA_LOGIN")
    password = os.environ.get("REZKA_PASSWORD")
    if login and password:
        return login, password
    login = get_secret("rezka_play.login")
    password = get_secret("rezka_play.password")
    if not login or not password:
        raise RuntimeError("Configure rezka.login and rezka.password in core.py")
    return login, password


class MediaController:
    def __init__(self):
        self.lock = threading.RLock()
        self.process = None
        self.url = ""
        self.title = ""
        self.quality = 1080
        self.translator_index = 0
        self.translators = []
        self.is_series = False
        self.season = None
        self.episode = None
        self.episodes = []
        self.paused_at = None
        threading.Thread(target=self._watch_pause, daemon=True).start()

    async def _resolve(self, url, quality, translator_index, season=None, episode=None):
        login, password = credentials()
        async with HDRezkaClient() as client:
            await client.login(login, password)
            player = await client.player(url)
            translators = list(player.post.translators.name_id.items())
            translator_index %= max(1, len(translators))
            is_series = hasattr(player, "get_episodes")
            episodes = []
            if is_series:
                all_episodes = set()
                selected_translation = None
                indexes = [(translator_index + offset) % len(translators) for offset in range(len(translators))]
                for index in indexes:
                    translator_id = translators[index][1]
                    payload = await client.ajax.get_episodes(player.post.id, translator_id)
                    soup = BeautifulSoup(payload["episodes"], "html.parser")
                    choices = sorted({
                        (int(item["data-season_id"]), int(item["data-episode_id"]))
                        for item in soup.select("[data-season_id][data-episode_id]")
                    })
                    all_episodes.update(choices)
                    if not choices:
                        continue
                    if season is None and episode is None:
                        season, episode = choices[0]
                    if (season, episode) in choices:
                        selected_translation = (index, translator_id, choices)
                        break
                if selected_translation is None:
                    available = sorted(ep for current_season, ep in all_episodes if current_season == season)
                    if not available:
                        seasons = sorted({current_season for current_season, _ in all_episodes})
                        raise RuntimeError(f"Сезону {season} немає. Доступні сезони: {', '.join(map(str, seasons))}")
                    raise RuntimeError(
                        f"У сезоні {season} немає серії {episode}. Доступні серії: {min(available)}-{max(available)}"
                    )
                translator_index, translator_id, episodes = selected_translation
                stream = await player.get_stream(season, episode, translator_id)
            else:
                if season is not None or episode is not None:
                    raise RuntimeError("Обраний результат не є серіалом")
                translator_id = translators[translator_index][1] if translators else None
                stream = await player.get_stream(translator_id)
            available = [int(q) for q in stream.video.qualities]
            requested = 480 if quality == 420 else quality
            selected = min(available, key=lambda item: abs(item - requested))
            urls = stream.video[selected].last_url
            return str(urls[0]), selected, translators, translator_index, is_series, season, episode, episodes

    def _rc(self, command, wait=0.15):
        with socket.create_connection((RC_HOST, RC_PORT), timeout=2) as conn:
            conn.settimeout(1)
            try:
                conn.recv(4096)
            except (socket.timeout, OSError):
                pass
            conn.sendall((command + "\n").encode())
            time.sleep(wait)
            chunks = []
            while True:
                try:
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    chunks.append(chunk)
                except socket.timeout:
                    break
            return b"".join(chunks).decode(errors="replace")

    def _current_time(self):
        try:
            response = self._rc("get_time")
            numbers = re.findall(r"(?m)^\s*(\d+)\s*$", response)
            return int(numbers[-1]) if numbers else 0
        except OSError:
            return 0

    def _stop_vlc(self):
        if self.process and self.process.poll() is None:
            try:
                self._rc("shutdown")
                self.process.wait(timeout=4)
            except Exception:
                self.process.terminate()
                try:
                    self.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.process.kill()
        self.process = None

    def _start_vlc(self, stream_url, start_time=0):
        self._stop_vlc()
        env = vlc_environment()
        command = [
            "/usr/bin/cvlc", "--fullscreen", "--no-video-title-show",
            "--extraintf", "rc", "--rc-host", f"{RC_HOST}:{RC_PORT}",
            "--aout=alsa", "--alsa-audio-device=" + (MEDIA_SETTINGS.get("alsa_device") or "plughw:0,3"),
        ]
        if start_time > 0:
            command += ["--start-time", str(start_time)]
        command.append(stream_url)
        # stderr inherits the service journal so display/audio errors are visible.
        # VLC can include signed stream URLs in its diagnostics; keep logs private.
        self.process = subprocess.Popen(command, env=env, stdout=subprocess.DEVNULL)
        for _ in range(30):
            time.sleep(0.2)
            try:
                with socket.create_connection((RC_HOST, RC_PORT), timeout=0.2):
                    return
            except OSError:
                if self.process.poll() is not None:
                    self._stop_vlc()
                    raise RuntimeError("VLC завершився під час запуску. Перевір графічну сесію та журнал sirius2.0.service")
        self._stop_vlc()
        raise RuntimeError("VLC RC не відповідає. Перевір журнал sirius2.0.service")

    def play(self, url, title, quality=1080, season=None, episode=None):
        with self.lock:
            resolved = asyncio.run(self._resolve(url, quality, 0, season, episode))
            stream_url, selected, translators, translator_index, is_series, season, episode, episodes = resolved
            self._start_vlc(stream_url)
            self.url = url
            self.title = title
            self.quality = selected
            self.translator_index = translator_index
            self.translators = translators
            self.is_series = is_series
            self.season = season
            self.episode = episode
            self.episodes = episodes
            self.paused_at = None
            voice = translators[translator_index][0] if translators else "за замовчуванням"
            if is_series:
                message = f"Серіал «{title}», сезон {season}, серія {episode} запущено"
            else:
                message = f"Фільм «{title}» запущено"
            return self._playback_result(message)

    def _playback_result(self, message):
        return {
            "message": message, "title": self.title, "quality": self.quality,
            "voice": self.translators[self.translator_index][0] if self.translators else "за замовчуванням",
            "voices": [{"index": index, "name": name, "selected": index == self.translator_index}
                       for index, (name, _) in enumerate(self.translators)],
        }

    def control(self, action, value=None):
        with self.lock:
            if action == "stop":
                self._stop_vlc()
                self.paused_at = None
                return {"message": "Відтворення вимкнено"}
            if not self.process or self.process.poll() is not None:
                raise RuntimeError("Фільм зараз не запущено")
            if action == "pause":
                self._rc("pause")
                self.paused_at = None if self.paused_at else time.monotonic()
                return {"message": "Відтворення продовжено" if self.paused_at is None else "Пауза"}
            if action == "forward":
                self._rc("seek +60")
                return {"message": "Перемотано вперед на хвилину"}
            if action == "back":
                self._rc("seek -60")
                return {"message": "Перемотано назад на хвилину"}
            if action in {"quality", "voice", "voice_index", "next_episode", "previous_episode"}:
                if action == "voice_index" and (type(value) is not int or not 0 <= value < len(self.translators)):
                    raise ValueError("Немає такої озвучки")
                if action == "voice_index" and value == self.translator_index:
                    return self._playback_result("Цю озвучку вже вибрано")
                position = self._current_time() if action in {"quality", "voice", "voice_index"} else 0
                quality = int(value) if action == "quality" else self.quality
                index = self.translator_index + 1 if action == "voice" else self.translator_index
                if action == "voice_index":
                    index = value
                season, episode = self.season, self.episode
                if action in {"next_episode", "previous_episode"}:
                    if not self.is_series:
                        raise RuntimeError("Зараз запущено не серіал")
                    current = self.episodes.index((season, episode))
                    offset = 1 if action == "next_episode" else -1
                    target = current + offset
                    if target < 0 or target >= len(self.episodes):
                        raise RuntimeError("Інших серій немає")
                    season, episode = self.episodes[target]
                resolved = asyncio.run(self._resolve(self.url, quality, index, season, episode))
                stream_url, selected, translators, translator_index, is_series, season, episode, episodes = resolved
                self._start_vlc(stream_url, position)
                self.quality = selected
                self.translators = translators
                self.translator_index = translator_index
                self.is_series = is_series
                self.season, self.episode, self.episodes = season, episode, episodes
                self.paused_at = None
                voice = translators[self.translator_index][0] if translators else "за замовчуванням"
                if action == "quality":
                    message = f"Якість змінено на {selected}p"
                elif action in {"voice", "voice_index"}:
                    message = f"Озвучка: {voice}"
                else:
                    message = f"Запущено сезон {season}, серія {episode}"
                return self._playback_result(message)
            raise ValueError("Невідома команда")

    def _watch_pause(self):
        while True:
            time.sleep(30)
            with self.lock:
                if self.paused_at and time.monotonic() - self.paused_at >= PAUSE_TIMEOUT:
                    self._stop_vlc()
                    self.paused_at = None


controller = MediaController()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return

    def _reply(self, status, payload):
        data = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}")
            if self.path == "/play":
                playback_home(payload)
                season = payload.get("season")
                episode = payload.get("episode")
                result = controller.play(
                    payload["url"], payload.get("title", "Фільм"), int(payload.get("quality", 1080)),
                    int(season) if season is not None else None,
                    int(episode) if episode is not None else None,
                )
            elif self.path == "/control":
                result = controller.control(payload["action"], payload.get("value"))
            else:
                self._reply(404, {"error": "not found"})
                return
            self._reply(200, result)
        except Exception as exc:
            log.warning("Media request failed: path=%s (%s)", self.path, type(exc).__name__)
            self._reply(400, {"error": str(exc)})


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
