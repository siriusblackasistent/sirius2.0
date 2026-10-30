from pathlib import Path
from tempfile import TemporaryDirectory
import signal
import subprocess
import unittest
from unittest.mock import Mock, patch

import run_sirius
import sirius_status
from plugins import plugin_greetings, plugin_light_control_hause0, plugin_light_control_hause30
from sirius_core import SiriusCore


class ServicesTests(unittest.TestCase):
    def settings(self, section):
        return self.config.get(section, {})

    def setUp(self):
        self.config = {"services": {"assistant": True, "telegram": True, "media": True}}
        for target, kwargs in [
            ("run_sirius.get_settings", {"side_effect": self.settings}),
            ("run_sirius.get_secret", {"return_value": "test-key"}),
            ("run_sirius.media_python", {"return_value": "/guest/.venv/bin/python"}),
            ("run_sirius.os.killpg", {}),
        ]:
            patcher = patch(target, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.service = run_sirius.SiriusServices()

    def test_single_start_launches_enabled_components_and_stops_owned_groups(self):
        children = [Mock(pid=i) for i in (101, 102, 103)]
        for child in children:
            child.poll.return_value = None
        with patch.object(run_sirius.subprocess, "Popen", side_effect=children) as popen, \
                patch.object(self.service, "wait_for_api", return_value=True) as ready, \
                patch.object(self.service.stopping, "wait", return_value=True):
            self.assertEqual(self.service.run(), 0)
        scripts = [Path(call.args[0][1]).name for call in popen.call_args_list]
        self.assertEqual(scripts, ["run_sirius_server.py", "rezka_media_server.py", "run_remote_telegrambot.py"])
        self.assertEqual(popen.call_args_list[1].args[0][0], "/guest/.venv/bin/python")
        ready.assert_called_once()
        for child in children:
            run_sirius.os.killpg.assert_any_call(child.pid, signal.SIGTERM)
            child.wait.assert_called_once()

    def test_disabled_components_never_start(self):
        self.config["services"] = dict.fromkeys(("assistant", "telegram", "media", "status"), False)
        with patch.object(run_sirius.subprocess, "Popen") as popen:
            self.assertEqual(self.service.run(), 0)
        popen.assert_not_called()

    def test_failed_component_stops_remaining_children(self):
        self.config["services"] = {"assistant": True, "telegram": False, "media": False}
        child = Mock(pid=201)
        child.poll.return_value = 1
        with patch.object(run_sirius.subprocess, "Popen", return_value=child), \
                patch.object(self.service.stopping, "wait", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "assistant exited"):
                self.service.run()
        run_sirius.os.killpg.assert_called_once_with(201, signal.SIGTERM)

    def test_missing_token_fails_before_starting_anything(self):
        with patch("run_sirius.get_secret", side_effect=lambda key: "key" if key == "server.api_key" else ""), \
                patch.object(run_sirius.subprocess, "Popen") as popen:
            with self.assertRaisesRegex(ValueError, "telegram.bot_token"):
                self.service.run()
        popen.assert_not_called()

    def test_api_readiness_timeout(self):
        with patch("run_sirius.server_config", return_value={"baseUrl": "http://127.0.0.1:5003"}), \
                patch("run_sirius.urllib.request.urlopen", side_effect=OSError("offline")), \
                patch("run_sirius.time.monotonic", side_effect=[100, 131]):
            with self.assertRaisesRegex(RuntimeError, "did not become ready"):
                self.service.wait_for_api()

    def test_non_boolean_switch_is_rejected(self):
        self.config["services"]["media"] = "off"
        with self.assertRaisesRegex(ValueError, "services.media"):
            self.service.run()

    def test_spawn_failure_cleans_up_an_already_started_component(self):
        child = Mock(pid=301)
        with patch.object(run_sirius.subprocess, "Popen", side_effect=[child, OSError("missing python")]):
            with self.assertRaisesRegex(OSError, "missing python"):
                self.service.run()
        run_sirius.os.killpg.assert_called_once_with(301, signal.SIGTERM)
        child.wait.assert_called_once()

    def test_shutdown_kills_an_unresponsive_process_group(self):
        child = Mock(pid=401)
        child.wait.side_effect = [subprocess.TimeoutExpired("test", 10), 0]
        self.service.children = {"media": child}
        self.service.stop()
        run_sirius.os.killpg.assert_any_call(401, signal.SIGTERM)
        run_sirius.os.killpg.assert_any_call(401, signal.SIGKILL)
        self.assertEqual(self.service.children, {})


class LocalStatusTests(unittest.TestCase):
    def test_status_reads_only_the_requesting_house_from_guest_folder(self):
        with TemporaryDirectory() as folder, patch.object(sirius_status, "BASE_DIR", Path(folder)):
            status = Path(folder) / "status"
            status.mkdir()
            (status / "house_30.txt").write_text("guest house 30", encoding="utf-8")
            (status / "house_0.txt").write_text("guest house 0", encoding="utf-8")
            core = SiriusCore()
            core.commands = {"статус": plugin_greetings.status}
            self.assertEqual(core.handle_message("статус", 1, 1, home="30")["text"], "guest house 30")
            self.assertEqual(core.handle_message("статус", 1, 2, home="0")["text"], "guest house 0")
            self.assertIn("Ще немає", core.handle_message("статус", 1, 3, home="70")["text"])
            self.assertEqual(sirius_status.status_text("../core.py"), "Невідомий будинок.")

    def test_status_off_never_starts_a_poller(self):
        for plugin in (plugin_light_control_hause0, plugin_light_control_hause30):
            with patch.object(plugin, "get_settings", return_value={"status": False}), \
                    patch.object(plugin, "Poller") as poller:
                core = Mock()
                plugin.start_background(core)
                plugin.request_poll(core, "")
                poller.assert_not_called()
                self.assertIn("вимкнено", core.reply.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
