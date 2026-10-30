from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from telebot import types
from test_guest_media_web import bot_functions

with patch('threading.Thread.start'):
    import rezka_media_server as media


class VoiceSelectionTests(unittest.TestCase):
    def controller(self):
        with patch('threading.Thread.start'):
            controller = media.MediaController()
        controller.process = Mock()
        controller.process.poll.return_value = None
        controller.url = 'https://example.com/movie'
        controller.title = 'Експеримент'
        controller.translators = [('Українська', 1), ('Оригінал', 2)]
        return controller

    def test_play_returns_all_named_voices_and_selected_one(self):
        controller = self.controller()
        resolved = ('stream', 1080, controller.translators, 0, False, None, None, [])
        with patch.object(controller, '_resolve', new=AsyncMock(return_value=resolved)), \
                patch.object(controller, '_start_vlc'):
            result = controller.play(controller.url, controller.title)
        self.assertEqual(result['title'], 'Експеримент')
        self.assertEqual(result['voices'], [
            {'index': 0, 'name': 'Українська', 'selected': True},
            {'index': 1, 'name': 'Оригінал', 'selected': False}])

    def test_select_voice_preserves_position_and_marks_selection(self):
        controller = self.controller()
        resolved = ('stream', 1080, controller.translators, 1, False, None, None, [])
        with patch.object(controller, '_resolve', new=AsyncMock(return_value=resolved)) as resolve, \
                patch.object(controller, '_current_time', return_value=123), \
                patch.object(controller, '_start_vlc') as start:
            result = controller.control('voice_index', 1)
        self.assertEqual(resolve.call_args.args[2], 1)
        start.assert_called_once_with('stream', 123)
        self.assertTrue(result['voices'][1]['selected'])
        self.assertFalse(result['voices'][0]['selected'])

    def test_invalid_voice_does_not_restart_movie(self):
        controller = self.controller()
        with patch.object(controller, '_start_vlc') as start:
            for index in (-1, 2, True, '1', None):
                with self.assertRaisesRegex(ValueError, 'озвучки'):
                    controller.control('voice_index', index)
        start.assert_not_called()

    def test_current_voice_does_not_restart_movie(self):
        controller = self.controller()
        with patch.object(controller, '_start_vlc') as start:
            result = controller.control('voice_index', 0)
        start.assert_not_called()
        self.assertIn('вже вибрано', result['message'])

    def test_telegram_shows_only_title_and_named_voice_buttons(self):
        scope = bot_functions({'rezka_controls_keyboard', 'send_media_started'}, types=types)
        result = self.controller()._playback_result('Фільм запущено')
        scope['send_media_started'](1, 2, result)
        sent = scope['bot'].send_message.call_args
        self.assertEqual(sent.args, (1, 'Експеримент'))
        buttons = [button for row in sent.kwargs['reply_markup'].keyboard for button in row]
        self.assertTrue(any(b.text == '✅ Українська' and b.callback_data == 'rz:ctl:2:v0' for b in buttons))
        self.assertTrue(any(b.text == 'Оригінал' and b.callback_data == 'rz:ctl:2:v1' for b in buttons))

    def test_voice_button_dispatches_exact_index_and_refreshes_keyboard(self):
        scope = bot_functions({'handle_rezka_callback'})
        result = self.controller()._playback_result('Озвучка змінена')
        scope['media_request'].return_value = result
        scope['rezka_controls_keyboard'] = Mock(return_value='updated')
        call = SimpleNamespace(data='rz:ctl:2:v1', id='callback', from_user=SimpleNamespace(id=2),
                               message=SimpleNamespace(chat=SimpleNamespace(id=1), message_id=99))
        scope['handle_rezka_callback'](call)
        scope['media_request'].assert_called_once_with('/control', {'action':'voice_index', 'value':1})
        scope['bot'].edit_message_reply_markup.assert_called_once_with(1, 99, reply_markup='updated')
        scope['media_request'].reset_mock()
        call.from_user.id = 3
        scope['handle_rezka_callback'](call)
        scope['media_request'].assert_not_called()
