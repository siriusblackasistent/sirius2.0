import unittest
from unittest.mock import Mock, patch

from sirius_core import SiriusCore
from plugins import plugin_light_control_hause0 as house0
from plugins import plugin_light_control_hause30 as house30


class ToggleCommandTests(unittest.TestCase):
    def setUp(self):
        self.core = SiriusCore()
        self.core.init_with_plugins()
        self.assertEqual(self.core.plugin_errors, {})
        for plugin in (house0, house30):
            self.core.commands.update(plugin.start(self.core)['commands'])
        guard = patch('requests.post', side_effect=AssertionError('Unexpected network request'))
        guard.start()
        self.addCleanup(guard.stop)

    def test_bare_and_prefixed_commands_toggle_both_directions(self):
        for plugin, alias, home, channel in [(house30, 'кухня', '30', '2'),
                                              (house0, 'будиночок', '0', '1')]:
            for prefix in ['', 'увімкни ', 'увімкнути ', 'вимкни ', 'вимкнути ']:
                for initial in [False, True]:
                    with self.subTest(alias=alias, prefix=prefix, initial=initial):
                        device = Mock()
                        device.status.side_effect = [{'dps': {channel: initial}},
                                                     {'dps': {channel: not initial}}]
                        with patch('tinytuya.OutletDevice', return_value=device), patch.object(plugin, '_write_status'):
                            response = self.core.handle_message(prefix + alias, 1, 1, home=home)
                        device.set_status.assert_called_once_with(not initial, switch=channel)
                        self.assertIn('вимкнув' if initial else 'увімкнув', response['text'])

    def test_collective_off_keeps_off_action(self):
        for plugin, command, home in [(house30, 'вимкни все світло', '30'),
                                       (house0, 'вимкни все світло 0', '0')]:
            with patch.object(plugin, '_operate', return_value=False) as operate, patch.object(plugin, '_write_status'):
                self.core.handle_message(command, 1, 1, home=home)
            self.assertEqual(operate.call_count, len(plugin._LIGHTS))
            self.assertTrue(all(call.args[1] == 'off' for call in operate.call_args_list))

    def test_prefix_does_not_rewrite_other_commands_or_context(self):
        handler = lambda core, phrase: None
        match = self.core.find_best_cmd_with_fuzzy('увімкни обігрів', self.core.commands)
        self.assertIn('увімкни обігрів', match[0].split('|'))
        self.assertEqual(match[1:], (1.0, ''))
        self.assertIsNone(self.core.find_best_cmd_with_fuzzy('вимкни кухня', {'кухня': handler}, threshold=1))
