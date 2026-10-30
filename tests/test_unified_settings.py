from pathlib import Path
from tempfile import TemporaryDirectory
import ast
import unittest
import sys
from unittest.mock import patch
import sirius_settings as settings

class SettingsTests(unittest.TestCase):
    def test_blank_server_template_uses_guest_port_for_api_and_client(self):
        with patch.object(settings, 'load_json', return_value={}), \
                patch.object(settings, 'get_settings', return_value={'host': '', 'port': '', 'baseUrl': ''}):
            config = settings.server_config()
        self.assertEqual(config['port'], 5003)
        self.assertEqual(config['baseUrl'], 'http://127.0.0.1:5003')

    def test_custom_server_port_is_shared_with_client(self):
        with patch.object(settings, 'load_json', return_value={}), \
                patch.object(settings, 'get_settings', return_value={'host': '0.0.0.0', 'port': 6006}):
            config = settings.server_config()
        self.assertEqual(config['baseUrl'], 'http://127.0.0.1:6006')

    def test_media_interpreter_defaults_to_current_local_environment(self):
        with patch.object(settings, 'get_settings', return_value={'python': ''}):
            self.assertEqual(settings.media_python(), sys.executable)
        with patch.object(settings, 'get_settings', return_value={'python': '.venv/bin/python'}):
            self.assertEqual(settings.media_python(), str((settings.BASE_DIR / '.venv/bin/python').absolute()))

    def test_template_has_no_populated_values(self):
        tree = ast.parse((settings.BASE_DIR / 'core').read_text())
        data = ast.literal_eval(tree.body[1].value)
        def check(value):
            if isinstance(value, dict):
                for child in value.values(): check(child)
            elif isinstance(value, list): self.assertEqual(value, [])
            else: self.assertIn(value, ('', False))
        check(data)
        self.assertEqual(set(data['midea']), {'0', '30'})

    def test_core_priority_blank_value_and_legacy_fallback(self):
        with TemporaryDirectory() as tmp, patch.object(settings, 'BASE_DIR', Path(tmp)), patch.dict('os.environ', {}, clear=True):
            root = Path(tmp)
            (root/'secrets.local.json').write_text('{"telegram":{"bot_token":"old"}}')
            (root/'core').write_text('raise RuntimeError("Template must not execute")')
            self.assertEqual(settings.get_secret('telegram.bot_token'), 'old')
            (root/'core.py').write_text('SETTINGS = {"telegram":{"bot_token":""},"midea":{"30":{"account":"test"}}}')
            self.assertEqual(settings.get_secret('telegram.bot_token'), '')
            self.assertEqual(settings.get_secret('plugin_greetings.midea_account'), 'test')
            with patch.dict('os.environ', {'SIRIUS_TELEGRAM_BOT_TOKEN': 'override'}):
                self.assertEqual(settings.get_secret('telegram.bot_token'), 'override')
