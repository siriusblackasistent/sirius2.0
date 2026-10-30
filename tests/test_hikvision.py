import ast
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from plugins import plugin_hikvision as camera
from sirius_core import SiriusCore
from sirius_settings import BASE_DIR, get_settings, load_json
from test_guest_media_web import bot_functions, message


class GuestCameraTests(unittest.TestCase):
    def setUp(self):
        self.core = SiriusCore()
        self.core.commands.update(camera.start(self.core)['commands'])
        self.config = {'cameras':{
            '1':{'name':'Камера 1','url':'http://192.0.2.10','allowed_homes':['0','30']},
            '2':{'name':'Камера 2','allowed_homes':['0']}}}
        self.telegram = {'botKey':'guest-token','adminChats':[-100],'adminUsers':[5],
                         'guestChats':{'-30':'30','-70':'70'}}
        self.send = Mock()
        self.fetch = Mock(return_value=b'jpeg')
        for name,kwargs in [('settings',{'return_value':self.config}),
                            ('telegram_settings',{'return_value':self.telegram}),
                            ('fetch_snapshot',{'new':self.fetch}),('send_photo',{'new':self.send})]:
            p=patch.object(camera,name,**kwargs);p.start();self.addCleanup(p.stop)
        p=patch('socket.socket.connect',side_effect=AssertionError('Unexpected network'))
        p.start();self.addCleanup(p.stop)

    def call(self,text,chat=-100,user=5,home='0'):
        return self.core.handle_message(text,chat,user,home=home)

    def test_all_camera_one_commands_bypass_gpt_and_send_to_origin(self):
        for text in ('кам1','Кам1','камера1','Камера1','-кам1','кам 1','камера 1','камера один'):
            with self.subTest(text=text):
                self.send.reset_mock()
                result=self.call(text)
                self.assertEqual(result['text'],'Знімок камери 1 надіслано.')
                self.send.assert_called_once_with('guest-token','-100',b'jpeg','Камера 1')

    def test_house_30_can_request_only_camera_one(self):
        self.assertIn('надіслано',self.call('кам1',chat=-30,user=9,home='30')['text'])
        self.send.assert_called_once_with('guest-token','-30',b'jpeg','Камера 1')
        self.send.reset_mock();self.fetch.reset_mock()
        self.assertIn('дозволеному',self.call('камера 2',chat=-30,user=9,home='30')['text'])
        self.fetch.assert_not_called();self.send.assert_not_called()

    def test_house_70_and_unregistered_chat_cannot_claim_home_30(self):
        for chat,user,home in ((-70,5,'70'),(-999,9,'30')):
            self.assertIn('дозволеному',self.call('кам1',chat=chat,user=user,home=home)['text'])
        self.fetch.assert_not_called();self.send.assert_not_called()

    def test_compact_alias_does_not_accept_another_camera_argument(self):
        self.assertIn('Напиши',self.call('кам1 2')['text'])
        self.fetch.assert_not_called()


class CameraMenusTests(unittest.TestCase):
    def test_camera_button_is_in_current_admin_and_house_30_menus_only(self):
        for settings in (load_json(BASE_DIR/'telegram.example.json'),):
            self.assertIn('кам1',settings['menuAdmin'].split(','))
            self.assertIn('кам1',settings['menuByHome']['30'].split(','))
            self.assertNotIn('кам1',settings['menuByHome']['70'].split(','))

    def test_house_30_telegram_forwards_alias_without_house_prefix(self):
        tree=ast.parse((BASE_DIR/'run_remote_telegrambot.py').read_text())
        tree.body=[node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name=='normalize_guest_command']
        import re
        scope={'re':re};exec(compile(tree,'<normalizer>','exec'),scope)
        self.assertEqual(scope['normalize_guest_command']('кам1','30'),('кам1',None))

    def test_guest_token_is_loaded_from_guest_settings(self):
        core=SiriusCore()
        with patch('sirius_settings.get_settings',return_value={'adminChats':[-100]}), \
                patch('sirius_settings.load_json',return_value={}), \
                patch('sirius_settings.get_secret',return_value='guest-only-token') as secret:
            options=camera.telegram_settings({},core)
        self.assertEqual(options['botKey'],'guest-only-token')
        secret.assert_called_once_with('telegram.bot_token')

    def test_all_guest_plugins_load_without_alias_collisions(self):
        core=SiriusCore();core.init_with_plugins()
        self.assertEqual(core.plugin_errors,{})
        self.assertIn('plugin_hikvision',core.plugin_manifests)
