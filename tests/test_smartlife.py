import base64
from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from telebot import types
from plugins import smartlife as plugin
from sirius_core import SiriusCore
from test_guest_media_web import bot_functions, message


class SmartLifeTests(unittest.TestCase):
    def setUp(self):
        self.folder = TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.core = SiriusCore(root=self.folder.name)
        manifest = plugin.start(self.core)
        self.core.commands = {**manifest['commands'], 'час': lambda c,p: c.reply('12:00'),
                              'status': lambda c,p: c.reply('status ok')}
        self.core.input_processors['smartlife'] = manifest['input_handler']
        self.login = Mock()
        self.login.qr_code.return_value = {'success':True, 'result':{'qrcode':'qr-token'}}
        self.login.login_result.return_value = (True, {
            'terminal_id':'terminal', 'endpoint':'https://example.com', 't':123,
            'uid':'account', 'expire_time':3600, 'access_token':'private-access', 'refresh_token':'private-refresh'})
        for name, kwargs in [
            ('plugins.smartlife.login_control', {'return_value':self.login}),
            ('plugins.smartlife.fetch_devices', {'return_value':[
                {'id':'one','name':'Лампа','online':True,'local_key':'private-key'},
                {'id':'two','name':'Розетка','online':False}]}),
            ('socket.socket.connect', {'side_effect':AssertionError('Unexpected network')})]:
            patcher = patch(name, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)

    def call(self, text, chat=1, user=2, home='0'):
        return self.core.handle_message(text, chat, user, home=home)

    def authorize(self):
        self.call('smartlife')
        self.call('AbC123Xy')
        return self.call('smartlife перевірити')

    def test_home_restriction_and_no_cloud_call(self):
        with patch('sirius_core.record_turn') as remember:
            for home in ('30','70',''):
                result = self.call('smartlife код AbC123Xy', home=home)
                self.assertIn('тільки з будинку 0', result['text'])
        remember.assert_not_called()
        self.login.qr_code.assert_not_called()

    def test_qr_preserves_case_and_never_enters_router_history(self):
        with patch('sirius_core.record_turn') as remember:
            self.call('-smartlife')
            result = self.call('AbC123Xy')
        remember.assert_not_called()
        self.assertEqual(self.login.qr_code.call_args.args[-1], 'AbC123Xy')
        png = base64.b64decode(result['smartlife']['qr_png'])
        self.assertTrue(png.startswith(b'\x89PNG'))
        self.assertTrue(result['smartlife']['check'])
        self.assertNotIn('AbC123Xy', result['text'])
        self.assertEqual(self.core.sessions[('1','2')].gpt_history, [])

    def test_other_commands_work_and_flow_survives_three_second_context(self):
        with patch('time.monotonic', return_value=100) as clock:
            self.call('smartlife')
            clock.return_value = 110
            self.assertEqual(self.call('status')['text'], 'status ok')
            self.assertIn('smartlife', self.call('AbC123Xy'))

    def test_chat_and_user_flows_are_isolated(self):
        self.call('smartlife')
        self.call('AbC123Xy', user=3)
        self.call('AbC123Xy', chat=9)
        self.login.qr_code.assert_not_called()
        self.call('AbC123Xy')
        self.login.qr_code.assert_called_once()

    def test_cancel_and_expiry_remove_pending_login(self):
        self.call('smartlife')
        self.call('відміна')
        self.assertEqual(self.core.sessions[('1','2')].smartlife, {})
        with patch('time.monotonic', return_value=100) as clock:
            self.call('smartlife')
            clock.return_value = 401
            result = self.call('AbC123Xy')
        self.assertIn('Час введення', result['text'])
        self.login.qr_code.assert_not_called()

    def test_success_persists_private_account_and_device_inventory_without_secrets_in_reply(self):
        result = self.authorize()
        self.assertIn('Лампа — у мережі', result['text'])
        self.assertIn('Розетка — не в мережі', result['text'])
        self.assertNotIn('private', result['text'])
        files = list(Path(self.folder.name).glob('data/smartlife/*/*.json'))
        self.assertEqual(len(files), 2)
        for file in files:
            self.assertEqual(file.stat().st_mode & 0o777, 0o600)
        registry = next(p for p in files if p.name == 'devices.json')
        entry = json.loads(registry.read_text())['devices']['one']
        self.assertEqual(entry['location'], '')
        self.assertEqual(entry['controls'], [])
        entry['display_name'] = 'Світло в кухні'
        entry['location'] = 'Кухня'
        data = json.loads(registry.read_text())
        data['devices']['one'] = entry
        registry.write_text(json.dumps(data))
        self.assertIn('Світло в кухні', self.call('smartlife список')['text'])

    def test_waiting_for_confirmation_does_not_save_account(self):
        self.call('smartlife код AbC123Xy')
        self.login.login_result.return_value = (False, {'code':'pending'})
        result = self.call('smartlife перевірити')
        self.assertTrue(result['smartlife']['check'])
        self.assertEqual(list(Path(self.folder.name).rglob('account.json')), [])

    def test_saved_connection_is_not_shared_with_another_user(self):
        self.authorize()
        result = self.call('smartlife', user=3)
        self.assertIn('Введи код користувача', result['text'])
        self.assertNotIn('Лампа', result['text'])

    def test_network_errors_do_not_echo_provider_credentials(self):
        self.login.qr_code.side_effect = RuntimeError('private-access')
        result = self.call('smartlife код AbC123Xy')
        self.assertNotIn('private-access', result['text'])
        self.assertIn('Не вдалося', result['text'])

    def test_sdk_transport_timeout_is_bounded(self):
        session = SimpleNamespace(request=Mock(return_value='ok'))
        original = session.request
        plugin.bound_timeout(session)
        self.assertEqual(session.request('GET','https://example.com',timeout=60), 'ok')
        self.assertEqual(original.call_args.kwargs['timeout'], (3,10))


class SmartLifeTelegramTests(unittest.TestCase):
    def test_admin_user_code_is_not_lowercased_by_telegram(self):
        scope = bot_functions(get_guest_home_by_chat=lambda chat: '', is_admin_user=lambda user: True)
        scope['send_cmd_towebapi'] = Mock()
        scope['handle_text'](message('AbC123Xy'))
        self.assertEqual(scope['send_cmd_towebapi'].call_args.args[0], 'AbC123Xy')

    def test_qr_is_sent_as_photo_with_owner_scoped_button(self):
        scope = bot_functions(base64=base64, BytesIO=BytesIO, types=types)
        scope['requests'].post.return_value.json.return_value = {
            'text':'Відскануй QR', 'smartlife':{'qr_png':base64.b64encode(b'png').decode(),'check':True}}
        scope['send_cmd_towebapi']('smartlife код AbC123Xy', message('code'))
        photo = scope['bot'].send_photo.call_args
        self.assertEqual(photo.args[1].getvalue(), b'png')
        self.assertEqual(photo.kwargs['reply_markup'].keyboard[0][0].callback_data, 'sl:check:2')

    def test_other_users_cannot_confirm_login(self):
        scope = bot_functions({'handle_smartlife_callback'}, SimpleNamespace=SimpleNamespace)
        scope['send_cmd_towebapi'] = Mock()
        scope['resolve_home_for_message'] = lambda m: '0'
        call = SimpleNamespace(data='sl:check:2', id='callback', from_user=SimpleNamespace(id=3),
                               message=SimpleNamespace(chat=SimpleNamespace(id=1)))
        scope['handle_smartlife_callback'](call)
        scope['send_cmd_towebapi'].assert_not_called()
        call.from_user.id = 2
        scope['handle_smartlife_callback'](call)
        self.assertEqual(scope['send_cmd_towebapi'].call_args.args[0], 'smartlife перевірити')


class SmartLifeSdkTests(unittest.TestCase):
    def test_inventory_reads_names_without_commands_or_mqtt_and_refresh_saves_token(self):
        manager = Mock()
        manager.home_repository.query_homes.return_value = [SimpleNamespace(id='home', name='Мій дім')]
        manager.customer_api.get.return_value = {'success':True, 'result':[
            {'id':'device','name':'Світильник','online':True}]}
        account = {'client_id':'client','user_code':'code','terminal_id':'terminal',
                   'endpoint':'https://example.com','token_info':{'access_token':'old'}}
        with TemporaryDirectory() as folder, patch('tuya_sharing.Manager', return_value=manager) as factory:
            path = Path(folder) / 'account.json'
            result = plugin.fetch_devices(account, path)
            self.assertEqual(result[0]['name'], 'Світильник')
            self.assertEqual(result[0]['smartlife_home'], 'Мій дім')
            manager.send_commands.assert_not_called()
            manager.refresh_mq.assert_not_called()
            manager.update_device_cache.assert_not_called()
            factory.call_args.args[-1].update_token({'access_token':'renewed'})
            self.assertEqual(json.loads(path.read_text())['token_info']['access_token'], 'renewed')
