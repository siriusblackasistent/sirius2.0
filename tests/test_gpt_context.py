import unittest
from types import SimpleNamespace
from unittest.mock import Mock, mock_open, patch

from plugins import plugin_GPT_asistan as dialog
from sirius_core import SiriusCore


class GptContextTests(unittest.TestCase):
    def test_expired_messages_are_not_sent_to_gpt_but_history_survives(self):
        core = SiriusCore()
        core.commands = {"діалог": dialog.play_gpt_dialog}
        exchange = Mock()
        exchange.json.return_value = [{"ccy": "USD", "buy": "40", "sale": "41"}]
        response = SimpleNamespace(choices=[{"message": {"content": "відповідь"}}])
        with patch("sirius_core.time.monotonic", return_value=100) as clock, \
                patch.object(dialog, "get_secret", return_value="test-key"), \
                patch.object(dialog.requests, "get", return_value=exchange), \
                patch.object(dialog, "open", mock_open(read_data=""), create=True), \
                patch.object(dialog.openai.ChatCompletion, "create", return_value=response) as gpt:
            core.handle_message("діалог перший-маркер", 1, 1)
            clock.return_value = 102
            core.handle_message("другий-маркер", 1, 1)
            second = gpt.call_args.kwargs["messages"]
            self.assertTrue(any("перший-маркер" in item["content"] for item in second))
            clock.return_value = 105
            result = core.handle_message("діалог третій-маркер", 1, 1)
            self.assertIn("відповідь", result["text"])
            self.assertEqual(gpt.call_count, 3)
            third = gpt.call_args.kwargs["messages"]
            self.assertFalse(any("перший-маркер" in item["content"] or
                                 "другий-маркер" in item["content"] for item in third))
            session = core.sessions[("1", "1")]
            self.assertEqual(len(session.history), 6)
            self.assertEqual(len(session.gpt_history), 2)


if __name__ == "__main__":
    unittest.main()
