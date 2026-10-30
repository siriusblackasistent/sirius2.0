import unittest
from unittest.mock import Mock, patch

import router_memory
from sirius_core import SiriusCore


class GuestRouterTests(unittest.TestCase):
    def setUp(self):
        router_memory._sessions.clear()
        self.core = SiriusCore()
        self.core.init_with_plugins()
        self.assertEqual(self.core.plugin_errors, {})
        globals_ = self.core.fuzzy_processors['gpt_router'][1].__globals__
        self.provider = Mock(return_value='Уточни, будь ласка.')
        override = patch.dict(globals_, call_gpt_with_retry=self.provider, OPENAI_API_KEY='test', RATE_LIMIT_UNTIL=0)
        override.start()
        self.addCleanup(override.stop)
        self.operate = Mock(return_value=True)
        self.target_globals = next(handler.__globals__ for handler in self.core.commands.values()
                                   if callable(handler) and handler.__name__ == 'light_bahroma')
        device = patch.dict(self.target_globals, _operate=self.operate, _write_status=Mock())
        device.start()
        self.addCleanup(device.stop)
        network = patch('socket.socket.connect', side_effect=AssertionError('Unexpected network'))
        network.start()
        self.addCleanup(network.stop)

    def call(self, text, chat=1, user=1):
        return self.core.handle_message(text, chat, user, home='30')

    def test_routed_and_direct_commands_return_only_plugin_reply(self):
        direct = self.call('бахрома')['text']
        self.provider.return_value = '/бахрома\nлогіка: треба світло'
        routed = self.call('прошу освітити подвірʼя')['text']
        self.assertEqual(routed, direct)
        self.assertNotIn('старшого', routed)
        self.assertNotIn('логіка', routed)
        self.assertNotIn('/бахрома', routed)

    def test_direct_command_bypasses_router_and_updates_target(self):
        self.call('попередня незрозуміла фраза')
        self.provider.reset_mock()
        self.call('баня')
        self.provider.assert_not_called()
        self.provider.return_value = '/бахрома'
        result = self.call('виключи')
        self.assertIn('Уточни пристрій', result['text'])
        self.assertEqual(self.operate.call_count, 1)

    def test_short_off_cannot_expand_to_all_lights(self):
        self.call('бахрома')
        self.provider.return_value = '/барнхаус все світло вимкнути'
        self.assertIn('Уточни пристрій', self.call('виключи')['text'])
        self.assertEqual(self.operate.call_count, 1)

    def test_same_target_followup_can_execute(self):
        self.call('бахрома')
        self.provider.return_value = '/вулиця'
        self.operate.return_value = False
        self.assertIn('вимкнув', self.call('виключи')['text'])
        self.assertEqual(self.operate.call_count, 2)

    def test_memory_isolated_and_expires_after_sixty_seconds(self):
        with patch('router_memory.time.monotonic', return_value=100) as clock:
            self.call('приватний-маркер')
            clock.return_value = 104
            self.call('продовження')
            self.assertIn('приватний-маркер', self.provider.call_args.args[0])
            self.call('інший запит', user=2)
            self.assertNotIn('приватний-маркер', self.provider.call_args.args[0])
            self.call('інший запит', chat=2)
            self.assertNotIn('приватний-маркер', self.provider.call_args.args[0])
            clock.return_value = 164
            self.call('нова тема')
            self.assertNotIn('приватний-маркер', self.provider.call_args.args[0])

    def test_disabled_and_internal_commands_are_not_offered(self):
        self.call('невідомий запит')
        catalog = self.provider.call_args.args[0].split('=== ДОЗВОЛЕНІ КОМАНДИ ===')[1].split('=== ЗАПИТ КОРИСТУВАЧА ===')[0]
        self.assertNotIn('/70ванна', catalog)
        self.assertNotIn('/команда', catalog)
        self.assertNotIn('__gpt_router_dummy__', catalog)
        self.assertIn('/знайди', catalog)

    def test_unknown_command_batch_does_not_partially_switch_devices(self):
        self.provider.return_value = '/баня\n/несуществующаякоманда'
        self.assertIn('дозволену команду', self.call('зроби дію')['text'])
        self.operate.assert_not_called()

    def test_router_cannot_recursively_call_itself(self):
        self.provider.return_value = '/команда повтори'
        self.assertIn('дозволену команду', self.call('прошу виконати')['text'])
        self.assertEqual(self.provider.call_count, 1)

    def test_movie_search_through_router_keeps_guest_plugin(self):
        self.provider.return_value = '/знайди лабіринт'
        with patch('subprocess.run') as catalog:
            catalog.return_value.stdout = '[{"name":"Лабіринт","year":1986}]'
            result = self.call('з найди лабірінт')
        self.assertIn('Лабіринт', result['text'])
        self.assertNotIn('старшого', result['text'])
        self.assertIn('лабіринт', catalog.call_args.args[0])
        self.assertEqual(result['media_search']['items'][0]['name'], 'Лабіринт')
        self.assertNotIn('media_search', self.call('ЧАС'))


if __name__ == '__main__':
    unittest.main()
