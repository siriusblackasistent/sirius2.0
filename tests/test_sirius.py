import time
import unittest
from unittest.mock import patch

from sirius_core import SiriusCore


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.core = SiriusCore()
        self.core.plugin_manifests["core"] = {"options": {"fuzzy_threshold": .86, "fuzzy_margin": .08}}

    def test_longest_prefix_and_word_boundary(self):
        commands = {"світло": None, "світло кухня": None}
        match = self.core.find_best_cmd_with_fuzzy("СВІТЛО   кухня завтра", commands)
        self.assertEqual(match, ("світло кухня", 1.0, "завтра"))
        self.assertIsNone(self.core.find_best_cmd_with_fuzzy("світлофор", commands, threshold=1))

    def test_typo(self):
        self.assertEqual(self.core.find_best_cmd_with_fuzzy("пагода завтра", {"погода": None}, threshold=.75), ("погода", .8333333333333334, "завтра"))

    def test_ambiguous_match_does_not_execute(self):
        self.core.plugin_manifests["core"]["options"]["fuzzy_threshold"] = .5
        self.core.commands = {"abcde": lambda c,p: c.reply("WRONG"), "abcdf": lambda c,p: c.reply("WRONG")}
        result = self.core.handle_message("abcdg", 1, 1)
        self.assertIn("Уточни", result["text"])
        self.assertNotIn("WRONG", result["text"])

    def test_context_history_and_cancellation_are_isolated(self):
        def continuation(core, phrase):
            core.reply("answer " + phrase)
        def begin(core, phrase):
            core.history.append("private")
            core.context_set(continuation)
            core.reply("question")
        self.core.commands = {"діалог": begin, "привіт": lambda c,p: c.reply("hello")}
        self.assertTrue(self.core.handle_message("діалог", 1, 1)["context_active"])
        self.assertEqual(self.core.handle_message("привіт", 1, 2)["text"], "hello")
        self.assertEqual(self.core.handle_message("привіт", 2, 1)["text"], "hello")
        self.assertEqual(self.core.handle_message("так", 1, 1)["text"], "answer так")
        self.core.handle_message("діалог", 1, 1)
        self.assertFalse(self.core.handle_message("відміна", 1, 1)["context_active"])
        self.assertEqual(self.core.sessions[("1", "1")].history, ["private", "private"])

    def test_three_second_idle_timeout_preserves_history_and_other_users(self):
        def dialog(core, phrase):
            core.history.append(phrase)
            core.gpt_history.append(phrase)
            core.context_set(dialog)
            core.reply("dialog " + phrase)

        self.core.commands = {"діалог": dialog, "привіт": lambda c,p: c.reply("hello")}
        with patch("sirius_core.time.monotonic", return_value=100) as clock:
            self.core.handle_message("діалог перший", 1, 1)
            clock.return_value = 101
            self.core.handle_message("діалог другий", 1, 2)
            clock.return_value = 103
            result = self.core.handle_message("привіт", 1, 1)
            self.assertEqual(result["text"], "hello")
            self.assertFalse(result["context_active"])
            self.assertEqual(self.core.sessions[("1", "1")].gpt_history, [])
            self.assertEqual(self.core.sessions[("1", "1")].history, ["перший"])
            clock.return_value = 103.5
            result = self.core.handle_message("продовження", 1, 2)
            self.assertEqual(result["text"], "dialog продовження")
            self.assertEqual(self.core.sessions[("1", "2")].gpt_history, ["другий", "продовження"])

    def test_messages_refresh_idle_timeout_and_cancel_preserves_history(self):
        def dialog(core, phrase):
            core.history.append(phrase)
            core.gpt_history.append(phrase)
            core.context_set(dialog)
            core.reply("dialog")

        self.core.commands = {"діалог": dialog}
        with patch("sirius_core.time.monotonic", return_value=100) as clock:
            self.core.handle_message("діалог початок", 1, 1)
            clock.return_value = 102.9
            self.assertTrue(self.core.handle_message("продовження", 1, 1)["context_active"])
            clock.return_value = 105.8
            self.assertTrue(self.core.handle_message("ще", 1, 1)["context_active"])
            self.core.handle_message("відміна", 1, 1)
            session = self.core.sessions[("1", "1")]
            self.assertEqual(session.gpt_history, [])
            self.assertEqual(session.history, ["початок", "продовження", "ще"])

    def test_expired_context_and_multiple_replies(self):
        def begin(core, phrase):
            core.context_set(lambda c,p: c.reply("expired"), duration=-1)
            core.reply("one")
            core.reply("two")
        self.core.commands = {"почати": begin, "привіт": lambda c,p: c.reply("hello")}
        result = self.core.handle_message("почати", 1, 1)
        self.assertEqual(result["text"], "one\ntwo")
        self.assertFalse(result["context_active"])
        self.assertEqual(self.core.handle_message("привіт", 1, 1)["text"], "hello")

    def test_timer_owner_and_metadata(self):
        def timer(core, phrase):
            core.reply(core.session.metadata["user_name"])
            core.set_timer(-1, (lambda c,p: c.reply(p), "ready"))
        self.core.commands = {"таймер": timer}
        self.assertEqual(self.core.handle_message("таймер", 1, 1, user_name="Сіріус")["text"], "Сіріус")
        self.assertEqual(self.core.notifications(1, 2), [])
        self.assertEqual(self.core.notifications(1, 1), ["ready"])
        self.assertEqual(self.core.notifications(1, 1), [])

    def test_plugin_load_and_greeting_without_external_requests(self):
        self.core.init_with_plugins()
        self.assertEqual(self.core.plugin_errors, {})
        self.assertIn("Сіріус", self.core.handle_message("привіт", 1, 1)["text"])


class ApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_transport_authentication_and_validation(self):
        import httpx
        import run_sirius_server as server
        fake = SiriusCore()
        fake.commands = {"привіт": lambda c,p: c.reply("Сіріус")}
        with patch.object(server, "core", fake), patch.object(fake, "init_with_plugins"), patch.object(server, "get_secret", return_value="test-key"):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://test") as client:
                payload = {"text": "привіт", "chat_id": 1, "user_id": 2}
                self.assertEqual((await client.post("/message", json=payload)).status_code, 401)
                headers = {"X-Sirius-Key": "test-key"}
                response = await client.post("/message", json=payload, headers=headers)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["text"], "Сіріус")
                self.assertEqual((await client.post("/message", json={**payload, "text": ""}, headers=headers)).status_code, 422)
                self.assertEqual((await client.get("/notifications", params={"chat_id": 1, "user_id": 2}, headers=headers)).json(), {"messages": []})
                self.assertEqual((await client.get("/sendTxtCmd", params={"cmd": "привіт"})).status_code, 200)

if __name__ == "__main__":
    unittest.main()
