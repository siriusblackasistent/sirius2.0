"""One entry point for the components enabled in core.py."""
import logging
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

from sirius_settings import BASE_DIR, get_secret, get_settings, server_config, media_python

log = logging.getLogger(__name__)


class SiriusServices:
    def __init__(self):
        self.children = {}
        self.stopping = threading.Event()

    def start(self, name, script, python=sys.executable):
        self.children[name] = subprocess.Popen(
            [python, str(BASE_DIR / script)], cwd=BASE_DIR, start_new_session=True)
        log.info("Started %s", name)

    def check_children(self):
        for name, child in self.children.items():
            code = child.poll()
            if code is not None:
                raise RuntimeError(f"{name} exited with code {code}")

    def wait_for_api(self, timeout=30):
        url = server_config()["baseUrl"]
        deadline = time.monotonic() + timeout
        while not self.stopping.is_set():
            self.check_children()
            try:
                with urllib.request.urlopen(url.rstrip("/") + "/health", timeout=1) as response:
                    if response.status == 200:
                        return True
            except (OSError, ValueError, urllib.error.URLError):
                pass
            if time.monotonic() >= deadline:
                raise RuntimeError("Sirius API did not become ready")
            self.stopping.wait(.25)
        return False

    def stop(self):
        # Each component owns a process group, including any VLC subprocesses.
        children = list(reversed(self.children.items()))
        for name, child in children:
            try:
                os.killpg(child.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        deadline = time.monotonic() + 10
        for name, child in children:
            try:
                child.wait(timeout=max(0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                child.wait()
            log.info("Stopped %s", name)
        self.children.clear()

    def run(self):
        flags = {"assistant": True, "telegram": False, "media": False, "status": True,
                 **get_settings("services")}
        for name in ("assistant", "telegram", "media", "status"):
            if type(flags[name]) is not bool:
                raise ValueError(f"services.{name} must be True or False")
        if flags["assistant"] and not get_secret("server.api_key"):
            raise ValueError("Configure server.api_key in core.py")
        if flags["telegram"] and not get_secret("telegram.bot_token"):
            raise ValueError("Configure telegram.bot_token in core.py")
        try:
            if flags["assistant"]:
                self.start("assistant", "run_sirius_server.py")
            if flags["media"]:
                self.start("media", "rezka_media_server.py", media_python())
            if flags["telegram"]:
                if not self.wait_for_api():
                    return 0
                self.start("telegram", "run_remote_telegrambot.py")
            if not self.children:
                log.info("All components are disabled in core.py")
                return 0
            while not self.stopping.wait(.25):
                self.check_children()
            return 0
        finally:
            self.stop()


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    services = SiriusServices()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: services.stopping.set())
    try:
        return services.run()
    except Exception as exc:
        log.error("Sirius stopped: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
