import ast
import json
from pathlib import Path
from types import SimpleNamespace
import sys
import time
import unittest
from unittest.mock import Mock, patch

import movie_cards
import run_guest_web
from sirius_settings import BASE_DIR


def bot_functions(function_names=None, **overrides):
    """Load handlers without starting Telegram or opening/truncating its log."""
    tree = ast.parse((BASE_DIR / 'run_remote_telegrambot.py').read_text())
    names = set(function_names or {'handle_text', 'send_cmd_towebapi'})
    tree.body = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    for node in tree.body:
        node.decorator_list = []
    import re
    import threading
    scope = dict(re=re, time=time, requests=Mock(), bot=Mock(), media_sessions={},
        guest_registration={}, get_guest_home_by_chat=lambda chat: '30',
        is_admin_chat=lambda chat: False, is_admin_user=lambda user: False,
        MEDIA_TEXT_COMMANDS={}, normalize_guest_command=Mock(return_value=('баня', None)),
        resolve_home_for_message=lambda message: '30', media_request=Mock(return_value={'message': 'started'}),
        send_media_started=Mock(), build_menu_markup=Mock(),
        notification_lock=threading.Lock(), notification_users=set(),
        baseUrl='http://guest', get_secret=lambda name: 'test', timer_keyboard=Mock(return_value=None),
        rezka_results_keyboard=Mock(return_value='buttons'))
    scope.update(overrides)
    exec(compile(tree, '<telegram handlers>', 'exec'), scope)
    return scope


def message(text, chat=1, user=2):
    return SimpleNamespace(text=text, chat=SimpleNamespace(id=chat),
                           from_user=SimpleNamespace(id=user, first_name='Test'))


class TelegramMediaTests(unittest.TestCase):
    def test_routed_results_have_buttons_and_number_selects_exact_movie(self):
        scope = bot_functions()
        item = {'name': 'Голая правда', 'url': 'https://example.com/movie', 'year': 2009}
        scope['requests'].post.return_value.json.return_value = {
            'text': 'found', 'media_search': {'items': [item], 'season': None, 'episode': None}}
        scope['send_cmd_towebapi']('хочу фільм', message('хочу фільм'))
        scope['bot'].send_message.assert_called_once_with(1, 'found', reply_markup='buttons')
        scope['handle_text'](message('1'))
        scope['normalize_guest_command'].assert_not_called()
        scope['media_request'].assert_called_once_with('/play', {
            'url': item['url'], 'title': item['name'], 'home': '30', 'quality': 1080,
            'season': None, 'episode': None})

    def test_expired_or_invalid_number_never_controls_devices(self):
        for expires, number in [(time.monotonic() - 1, '1'), (time.monotonic() + 60, '2')]:
            scope = bot_functions(media_sessions={(1, 2): {'items': [{}], 'expires': expires}})
            scope['handle_text'](message(number))
            scope['normalize_guest_command'].assert_not_called()
            scope['media_request'].assert_not_called()

    def test_menu_mention_and_cancel(self):
        scope = bot_functions(media_sessions={(1, 2): {'items': [{}], 'expires': time.monotonic()+60}})
        scope['handle_text'](message('/menu@Siriusblacktekst_bot'))
        scope['build_menu_markup'].assert_called_once_with(1)
        scope['send_cmd_towebapi'] = Mock()
        scope['handle_text'](message('Відміна'))
        self.assertNotIn((1, 2), scope['media_sessions'])


class WebMediaTests(unittest.TestCase):
    def test_routed_result_becomes_clickable_card_without_starting_playback(self):
        voice = SimpleNamespace(remember_local_reply=Mock())
        response = Mock()
        response.json.return_value = {'text': 'found', 'media_search': {'items': [
            {'name': 'Лабіринт', 'url': 'https://example.com/movie', 'year': 1986}]}}
        with patch.dict(sys.modules, voice_daemon=voice), patch('requests.get', return_value=response) as request, \
                patch('requests.post') as control:
            result = run_guest_web.forward_command('хочу подивитись лабіринт', client_id='one')
        self.assertEqual(result['movies'][0]['name'], 'Лабіринт')
        control.assert_not_called()
        self.assertTrue(request.call_args.args[0].endswith(':5003/sendTxtCmd'))
        token = result['movies'][0]['id']
        self.assertEqual(movie_cards.items[token]['url'], 'https://example.com/movie')

    def test_direct_catalog_uses_guest_interpreter_and_path(self):
        with patch('movie_cards.subprocess.run') as catalog:
            catalog.return_value.stdout = '[]'
            movie_cards.search_movies('Знайди лабіринт')
        self.assertEqual(catalog.call_args.args[0][1], str(BASE_DIR / 'rezka_catalog.py'))
        self.assertTrue(movie_cards.MEDIA.endswith(':8766'))


class WebApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_legacy_web_contract_local_only_and_session_isolation(self):
        import httpx
        import run_sirius_server as server
        from sirius_core import SiriusCore
        fake = SiriusCore()
        fake.commands = {'час': lambda c,p: c.reply('09:24')}
        with patch.object(server, 'core', fake):
            for host, status in [('127.0.0.1', 200), ('192.0.2.1', 403)]:
                transport = httpx.ASGITransport(app=server.app, client=(host, 123))
                async with httpx.AsyncClient(transport=transport, base_url='http://test') as client:
                    result = await client.get('/sendTxtCmd', params={'cmd':'ЧАС','chat_id':'a','user_id':'a'})
                    self.assertEqual(result.status_code, status)
                    if status == 200:
                        self.assertEqual(result.json()['restxt'], '09:24')
                        denied = await client.get('/sendTxtCmd', params={'cmd':'час'}, headers={'X-Forwarded-For':'192.0.2.1'})
                        self.assertEqual(denied.status_code, 403)
        self.assertNotEqual(server.web_identity('a', 'a'), server.web_identity('b', 'b'))
        self.assertEqual(server.web_identity('a', 'a'), server.web_identity('a', 'a'))
