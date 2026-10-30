import asyncio
from datetime import datetime
from pathlib import Path
import runpy
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

from sirius_core import SiriusCore
from plugins.plugin_timer import start, alarm


class ScheduledTests(unittest.TestCase):
    def setUp(self):
        self.clock = patch('sirius_core.time.monotonic', return_value=100)
        self.now = self.clock.start()
        self.addCleanup(self.clock.stop)
        self.core = SiriusCore()
        self.action = Mock(side_effect=lambda core, phrase: core.reply('Зроблено'))
        self.core.commands = start(self.core)['commands']
        self.core.scheduled_commands = {'вимкни все світло': self.action}
        self.core.schedule_homes = {'вимкни все світло': ['0', '30']}

    def proposal(self, phrase='через 5хв вимкни все світло', **metadata):
        result = self.core.handle_message(phrase, 1, 2, **metadata)
        self.assertIn('confirmation', result)
        return result['confirmation']['id']

    def test_confirm_required_and_executes_without_telegram_poll(self):
        ident = self.proposal()
        self.assertEqual(self.core.sessions[('1', '2')].timers, [])
        self.now.return_value = 120
        result = self.core.decide_action(1, 2, ident, 'confirm')
        self.assertIn('встановлено', result['text'])
        self.assertEqual(self.core.sessions[('1', '2')].timers[0][0], 400)
        self.now.return_value = 399
        self.core.run_due_timers()
        self.action.assert_not_called()
        self.now.return_value = 400
        self.core.run_due_timers()
        self.action.assert_called_once()
        self.core.run_due_timers()
        self.action.assert_called_once()
        self.assertTrue(self.core.notifications(1, 2))
        self.assertEqual(self.core.notifications(1, 2), [])

    def test_wrong_owner_and_duplicate_confirmation(self):
        ident = self.proposal()
        self.assertFalse(self.core.decide_action(1, 3, ident, 'confirm')['ok'])
        self.assertFalse(self.core.decide_action(3, 2, ident, 'confirm')['ok'])
        self.core.decide_action(1, 2, ident, 'confirm')
        self.core.decide_action(1, 2, ident, 'confirm')
        self.assertEqual(len(self.core.sessions[('1', '2')].timers), 1)

    def test_cancel_and_expiration(self):
        ident = self.proposal()
        self.core.decide_action(1, 2, ident, 'cancel')
        self.core.decide_action(1, 2, ident, 'confirm')
        self.assertEqual(self.core.sessions[('1', '2')].timers, [])
        ident = self.proposal('через 5 секунд вимкни все світло')
        self.now.return_value = 106
        self.assertIn('минув', self.core.decide_action(1, 2, ident, 'confirm')['text'])
        self.assertEqual(self.core.sessions[('1', '2')].timers, [])
        ident = self.proposal('через 1 годину вимкни все світло')
        self.now.return_value = 407
        self.assertIn('минув', self.core.decide_action(1, 2, ident, 'confirm')['text'])

    def test_words_and_multiple_proposals(self):
        first = self.proposal('через двадцять п’ять секунд вимкни все світло')
        second = self.proposal('через дві хвилини вимкни все світло')
        self.core.decide_action(1, 2, first, 'confirm')
        self.core.decide_action(1, 2, second, 'cancel')
        self.assertEqual(self.core.sessions[('1', '2')].timers[0][0], 125)

    def test_unknown_or_wrong_home_never_uses_gpt(self):
        gpt = Mock()
        self.core.fuzzy_processors = {'gpt': (None, gpt)}
        for phrase, home in [('через 5 хв невідома дія', '0'), ('через 5хв вимкни все світло', '70'), ('через 5хв вимкни все світло крім кухні', '0')]:
            result = self.core.handle_message(phrase, 1, 2, home=home)
            self.assertNotIn('confirmation', result)
        gpt.assert_not_called()
        self.action.assert_not_called()

    def test_alarm_clock_and_next_day(self):
        fake = Mock()
        with patch('plugins.plugin_timer.datetime') as clock:
            clock.now.return_value = datetime(2026, 9, 26, 9, 0, tzinfo=ZoneInfo('Europe/Kyiv'))
            alarm(fake, 'о 8 45 включи обігрів')
        self.assertEqual(fake.propose_action.call_args.args[0], 23.75 * 3600)
        self.assertIn('27.09.2026 08:45', fake.propose_action.call_args.args[2])
        for phrase in ['о 25:30 дія', '8 99 дія', '8:45', '-1 30 дія']:
            fake.reset_mock()
            alarm(fake, phrase)
            fake.propose_action.assert_not_called()

    def test_direct_clock_alias(self):
        self.proposal('о 8 45 вимкни все світло')
        self.proposal('будильник о 08:45 вимкни все світло')

    def test_text_cancellation_and_saved_metadata_context(self):
        ident = self.proposal(home='30', user_name='Автор')
        self.core.handle_message('відміна', 1, 2)
        self.core.decide_action(1, 2, ident, 'confirm')
        self.assertFalse(self.core.sessions[('1', '2')].timers)
        ident = self.proposal(home='30', user_name='Автор')
        self.core.decide_action(1, 2, ident, 'confirm')
        session = self.core.sessions[('1', '2')]
        context = lambda core, phrase: None
        session.context, session.expires = context, 999
        session.metadata = {'home': '0'}
        seen = []
        self.action.side_effect = lambda core, phrase: seen.append(core.session.metadata['home'])
        self.now.return_value = 401
        self.core.run_due_timers()
        self.assertEqual(seen, ['30'])
        self.assertIs(session.context, context)
        self.assertEqual(session.metadata, {'home': '0'})

    def test_one_failed_action_does_not_drop_next(self):
        self.action.side_effect = RuntimeError('offline')
        first = self.proposal()
        second = self.proposal()
        self.core.decide_action(1, 2, first, 'confirm')
        self.core.decide_action(1, 2, second, 'confirm')
        self.now.return_value = 401
        self.core.run_due_timers()
        self.assertEqual(self.action.call_count, 2)
        self.assertIn('Не вдалося', '\n'.join(self.core.notifications(1, 2)))


class ScheduledApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_confirmation_api_and_background_worker(self):
        import httpx
        import run_sirius_server as server
        fake = Mock()
        fake.decide_action.return_value = {'ok': True, 'text': 'Таймер встановлено'}
        with patch.object(server, 'core', fake), patch.object(server, 'get_secret', return_value='test'):
            async with server.lifespan(server.app):
                for _ in range(50):
                    if fake.run_due_timers.called:
                        break
                    await asyncio.sleep(.01)
                fake.run_due_timers.assert_called()
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url='http://test') as client:
                    payload = {'chat_id': 1, 'user_id': 2, 'id': '1234567890abcdef', 'decision': 'confirm'}
                    self.assertEqual((await client.post('/timer/decision', json=payload)).status_code, 401)
                    result = await client.post('/timer/decision', json=payload, headers={'X-Sirius-Key': 'test'})
                    self.assertTrue(result.json()['ok'])
                    fake.decide_action.assert_called_once_with(1, 2, payload['id'], 'confirm')


class TelegramConfirmationTests(unittest.TestCase):
    def test_buttons_and_owner_check_with_no_network(self):
        import requests, telebot, threading
        bot = Mock()
        bot.message_handler.side_effect = lambda *a, **k: lambda fn: fn
        bot.callback_query_handler.side_effect = lambda *a, **k: lambda fn: fn
        with patch('sirius_settings.get_secret', return_value='test'), patch.object(telebot, 'TeleBot', return_value=bot), patch.object(threading.Thread, 'start'), patch.object(requests, 'post') as post:
            ns = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'run_remote_telegrambot.py'))
            user = 1  # Synthetic user; public defaults intentionally have no administrators.
            ns['options_tele']['adminUsers'] = [user]
            ident = '1234567890abcdef'
            keyboard = ns['timer_keyboard']({'id': ident}, user).to_dict()['inline_keyboard'][0]
            self.assertEqual([button['text'] for button in keyboard], ['Підтвердити', 'Скасувати'])
            self.assertLessEqual(len(keyboard[0]['callback_data'].encode()), 64)
            call = SimpleNamespace(id='call', data=keyboard[0]['callback_data'], from_user=SimpleNamespace(id=user+1), message=SimpleNamespace(chat=SimpleNamespace(id=111), message_id=9))
            ns['handle_timer_callback'](call)
            post.assert_not_called()
            call.from_user.id = user
            post.return_value.json.return_value = {'ok': True, 'text': 'Таймер встановлено'}
            ns['handle_timer_callback'](call)
            self.assertEqual(post.call_args.kwargs['json']['user_id'], user)
            self.assertEqual(post.call_args.kwargs['json']['decision'], 'confirm')
            bot.edit_message_reply_markup.assert_called_once_with(111, 9, reply_markup=None)

if __name__ == '__main__':
    unittest.main()
