import unittest
from unittest.mock import Mock, patch
from datetime import datetime

from plugins.plugin_timer import parse_duration, timer
from sirius_core import SiriusCore


class TimerTests(unittest.TestCase):
    def test_duration_words_and_digits(self):
        examples = {
            "п’ять хвилин": 300, "на двадцять п'ять секунд": 25,
            "ДВІ ГОДИНИ": 7200, "одну хвилину": 60,
            "сто двадцять три секунди": 123, "девʼяносто девʼять секунд": 99,
            "шістнадцять": 960, "пять минут": 300, "двадцать одну минуту": 1260,
            "два часа": 7200, "5хв": 300, "30 секунд": 30,
            "на 5": 300, "10 годин": 36000, "10000 секунд": 10000,
        }
        for phrase, expected in examples.items():
            with self.subTest(phrase=phrase):
                self.assertEqual(parse_duration(phrase), expected)

    def test_invalid_phrases_do_not_schedule(self):
        for phrase in ["нуль секунд", "0", "мінус п’ять хвилин", "-5 хв", "10001", "два три хвилини", "десять п’ять хвилин", "п’ять котів", "5.5 хв", "25:99", "", "п’ять хвилин дві секунди"]:
            with self.subTest(phrase=phrase):
                core = Mock()
                timer(core, phrase)
                core.set_timer.assert_not_called()
                core.context_set.assert_called_once_with(timer)

    def test_followup_words_create_timer_in_correct_session(self):
        core = SiriusCore()
        core.commands = {"таймер": timer}
        self.assertTrue(core.handle_message("таймер", 11, 22)["context_active"])
        with patch("sirius_core.time.monotonic", return_value=100):
            result = core.handle_message("п’ять хвилин", 11, 22)
        self.assertFalse(result["context_active"])
        self.assertEqual(core.sessions[("11", "22")].timers[0][0], 400)
        self.assertIn("Таймер встановлено", result["text"])

    def test_numeric_clock_still_rolls_to_tomorrow(self):
        core = Mock()
        with patch("plugins.plugin_timer.datetime") as clock:
            clock.now.return_value = datetime(2026, 9, 26, 19, 0)
            timer(core, "о 18:30")
        self.assertEqual(core.set_timer.call_args.args[0], 23.5 * 3600)

if __name__ == "__main__":
    unittest.main()
