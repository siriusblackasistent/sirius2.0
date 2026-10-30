from io import BytesIO
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

with patch('threading.Thread.start'):
    import rezka_media_server as media


class HdmiMediaTests(unittest.TestCase):
    def test_main_and_guest_chats_both_reach_hdmi_playback(self):
        for home in ('0', '30'):
            payload = {'home': home, 'url': 'https://example.com/movie', 'title': 'Movie'}
            handler = media.Handler.__new__(media.Handler)
            handler.path = '/play'
            data = json.dumps(payload).encode()
            handler.headers = {'Content-Length': str(len(data))}
            handler.rfile = BytesIO(data)
            handler._reply = Mock()
            with patch.object(media, 'controller') as controller:
                controller.play.return_value = {'message': 'started'}
                handler.do_POST()
            controller.play.assert_called_once_with(payload['url'], 'Movie', 1080, None, None)
            handler._reply.assert_called_once_with(200, {'message': 'started'})

    def test_other_houses_are_rejected(self):
        for home in ('70', '', None):
            with self.assertRaises(RuntimeError):
                media.playback_home({'home': home})

    def test_x11_uses_current_gdm_authority_without_fake_wayland(self):
        with TemporaryDirectory() as folder:
            authority = Path(folder) / 'gdm/Xauthority'
            authority.parent.mkdir()
            authority.touch()
            with patch.dict(os.environ, {'XDG_RUNTIME_DIR': folder}, clear=True), \
                    patch.dict(media.MEDIA_SETTINGS, {}, clear=True):
                env = media.vlc_environment()
            self.assertEqual(env['XAUTHORITY'], str(authority))
            self.assertEqual(env['DISPLAY'], ':0')
            self.assertNotIn('WAYLAND_DISPLAY', env)

    def test_explicit_display_and_authority_settings_win(self):
        with patch.dict(os.environ, {'DISPLAY': ':9', 'XAUTHORITY': '/old'}, clear=True), \
                patch.dict(media.MEDIA_SETTINGS, {'display': ':1', 'xauthority': '/chosen'}, clear=True):
            env = media.vlc_environment()
        self.assertEqual(env['DISPLAY'], ':1')
        self.assertEqual(env['XAUTHORITY'], '/chosen')

    def make_controller(self):
        with patch('threading.Thread.start'):
            return media.MediaController()

    def test_vlc_uses_hdmi_audio_and_keeps_stderr_in_service_journal(self):
        controller = self.make_controller()
        process = Mock()
        with patch.object(media.subprocess, 'Popen', return_value=process) as launch, \
                patch.object(media.socket, 'create_connection'), patch.object(media.time, 'sleep'), \
                patch.dict(media.MEDIA_SETTINGS, {'alsa_device': 'plughw:0,7'}, clear=True):
            controller._start_vlc('https://example.com/stream', 42)
        command = launch.call_args.args[0]
        self.assertIn('--alsa-audio-device=plughw:0,7', command)
        self.assertIn('--fullscreen', command)
        self.assertIn('--start-time', command)
        self.assertNotIn('stderr', launch.call_args.kwargs)
        self.assertIs(controller.process, process)

    def test_failed_vlc_start_is_reported_and_cleared(self):
        controller = self.make_controller()
        process = Mock()
        process.poll.return_value = 1
        with patch.object(media.subprocess, 'Popen', return_value=process), \
                patch.object(media.socket, 'create_connection', side_effect=OSError), \
                patch.object(media.time, 'sleep'):
            with self.assertRaisesRegex(RuntimeError, 'VLC завершився'):
                controller._start_vlc('https://example.com/stream')
        self.assertIsNone(controller.process)

    def test_rc_timeout_stops_owned_vlc(self):
        controller = self.make_controller()
        process = Mock()
        process.poll.return_value = None
        with patch.object(media.subprocess, 'Popen', return_value=process), \
                patch.object(media.socket, 'create_connection', side_effect=OSError), \
                patch.object(media.time, 'sleep'), patch.object(controller, '_rc'):
            with self.assertRaisesRegex(RuntimeError, 'VLC RC не відповідає'):
                controller._start_vlc('https://example.com/stream')
        process.wait.assert_called_once_with(timeout=4)
        self.assertIsNone(controller.process)
