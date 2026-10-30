from datetime import datetime
import unittest
from unittest.mock import Mock, patch
from plugins import plugin_datetime as plugin
from sirius_core import SiriusCore

class DateTimeTests(unittest.TestCase):
    def test_cat_time_examples(self):
        samples={(6,15):'шшоста п’ятнадцямть',(22,21):'двадцять друрга двадцять один',
                 (3,14):'трретя чотирнадцямть',(15,0):'п’ятнадцямта нулль нулль'}
        for (hour,minute),expected in samples.items():
            self.assertEqual(plugin.format_time(datetime(2026,1,1,hour,minute)),expected)
    def test_all_hours_and_minutes_are_nonempty_words(self):
        for hour in range(24):
            for minute in range(60):
                text=plugin.format_time(datetime(2026,1,1,hour,minute))
                self.assertTrue(text.strip())
                self.assertFalse(any(c.isdigit() for c in text))
        for minute in (-1,60):
            with self.assertRaises(ValueError):plugin.minute_words(minute)
    def test_time_uses_kyiv_timezone(self):
        core=Mock()
        with patch.object(plugin,'datetime') as clock:
            clock.now.return_value=datetime(2026,1,1,6,15)
            plugin.play_time(core,'')
            clock.now.assert_called_once_with(plugin.KYIV)
        self.assertEqual(core.reply.call_args.args[0],'шшоста п’ятнадцямть')
    def test_time_commands_are_owned_by_datetime_plugin(self):
        core=SiriusCore();core.init_with_plugins()
        self.assertEqual(core.plugin_errors,{})
        for command in ('час','яка година','котра година'):
            match=core.find_best_cmd_with_fuzzy(command,core.commands)
            self.assertEqual(core.commands[match[0]].__module__,'sirius_plugins.plugin_datetime')
