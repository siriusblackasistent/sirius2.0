from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import threading
import time
import unittest
from unittest.mock import Mock, patch

from plugins import plugin_light_control_hause30 as plugin
from sirius_core import SiriusCore


class House30Tests(unittest.TestCase):
    def setUp(self):
        plugin._OBSERVATIONS.clear()
        plugin._DEVICE_LOCKS.clear()
        plugin._AC_OBSERVATION.clear()
        ac = patch.object(plugin, '_poll_ac')
        ac.start()
        self.addCleanup(ac.stop)
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'house_30.txt'
        self.core = SimpleNamespace(_lock=threading.RLock())

    def test_device_blocks_have_complete_config_and_no_network_on_load(self):
        with patch('requests.post') as post, patch('tinytuya.OutletDevice') as device:
            configs = [block(action='config') for block in plugin._DEVICES]
            manifest = plugin.start(self.core)
        self.assertEqual(len(configs), 7)
        self.assertIn('start_background', manifest)
        post.assert_not_called()
        device.assert_not_called()
        self.assertTrue(all('ip' in config and 'device_id' in config for config in configs))
        self.assertEqual(plugin.banya(action='config')['device_id'], '')

    def test_one_offline_device_does_not_stop_poll_or_erase_last_good(self):
        config = plugin.light_lustra(action='config')
        plugin._record(config, True)
        def read(config, action):
            self.assertEqual(action, 'status')
            if config['name'] == 'Люстра':
                raise TimeoutError('offline')
            plugin._record(config, False)
            return False
        with patch.object(plugin, '_operate', side_effect=read) as operate:
            result = plugin.Poller(self.core, self.path).poll_once()
        self.assertEqual(operate.call_count, 7)
        self.assertIn('недоступний; останній стан: увімкнено', result)
        self.assertIn('Полив: вимкнено', result)
        self.assertEqual(self.path.read_text(), result)
        self.assertEqual(list(self.path.parent.glob('.house30-*')), [])

    def test_no_overlapping_polling_cycles(self):
        poller = plugin.Poller(self.core, self.path)
        poller.cycle_lock.acquire()
        try:
            with patch.object(plugin, '_operate') as operate:
                self.assertIsNone(poller.poll_once())
                operate.assert_not_called()
        finally:
            poller.cycle_lock.release()

    def test_each_block_owns_its_success_and_error_replies(self):
        core = Mock()
        core.session.metadata = {'home': '30'}
        cases = [(True, 'увімкнув, мяу'), (False, 'вимкнув, мяу'),
                 (plugin.DeviceUnavailableError(), 'не в мережі'),
                 (ValueError(), 'не пройшла'), (BlockingIOError(), 'Повтори')]
        for block in plugin._DEVICES:
            for result, text in cases:
                core.reply.reset_mock()
                kwargs = {'side_effect': result} if isinstance(result, Exception) else {'return_value': result}
                with patch.object(plugin, '_operate', **kwargs), patch.object(plugin, '_write_status'):
                    block(core, '', 'on')
                actual = core.reply.call_args.args[0]
                if block is plugin.banya and result is True:
                    self.assertIn('баньки', actual)
                elif isinstance(result, bool):
                    self.assertIn('увімкнув' if result else 'вимкнув', actual)
                else:
                    self.assertIn(text, actual)

    def test_device_busy_returns_immediately_without_network(self):
        config = plugin.light_kithen(action='config')
        lock = plugin._lock_for(config)
        self.assertIs(lock, plugin._lock_for(plugin.light_lustra(action='config')))
        lock.acquire()
        try:
            with patch('tinytuya.OutletDevice') as device, self.assertRaises(BlockingIOError):
                plugin._operate(config, 'on')
            device.assert_not_called()
        finally:
            lock.release()

    def test_tuya_poll_read_only_and_explicit_on_idempotent(self):
        config = plugin.light_kithen(action='config')
        device = Mock()
        device.status.return_value = {'dps': {'2': True}}
        with patch('tinytuya.OutletDevice', return_value=device):
            self.assertTrue(plugin._operate(config, 'status'))
            self.assertTrue(plugin._operate(config, 'on'))
        device.set_status.assert_not_called()
        device.set_socketTimeout.assert_called_with(plugin.REQUEST_TIMEOUT)
        device.set_socketRetryLimit.assert_called_with(1)

    def test_sonoff_correct_target_timeout_and_no_write_for_poll(self):
        config = plugin.banya(action='config')
        with patch('requests.post') as post:
            post.return_value.json.return_value = {'error': 0, 'data': {'switch': 'off'}}
            self.assertFalse(plugin._operate(config, 'status'))
            post.assert_called_once()
            self.assertTrue(post.call_args.args[0].endswith('/info'))
            self.assertEqual(post.call_args.kwargs['json']['deviceid'], config['device_id'])
            self.assertEqual(post.call_args.kwargs['timeout'], plugin.REQUEST_TIMEOUT)

    def test_poll_command_is_nonblocking_and_status_uses_file(self):
        core = Mock()
        core.session.metadata = {'home': '30'}
        monitor = Mock()
        with patch.object(plugin, 'start_background') as start, patch.object(plugin, '_MONITOR', monitor):
            plugin.request_poll(core, '')
            start.assert_called_once_with(core)
            monitor.request.assert_called_once_with(core.session)
        self.path.write_text('cached status')
        with patch.object(plugin, 'STATUS_FILE', self.path), patch.object(plugin, '_operate') as operate:
            plugin.show_status(core, '')
            operate.assert_not_called()
            core.reply.assert_called_with('cached status')

    def test_worker_coalesces_requests_sends_one_reply_and_stops(self):
        poller = plugin.Poller(self.core, self.path)
        session = SimpleNamespace(outbox=[])
        entered, release, done = threading.Event(), threading.Event(), threading.Event()
        def poll():
            entered.set()
            release.wait(2)
            done.set()
            return 'finished'
        with patch.object(poller, 'poll_once', side_effect=poll) as cycle:
            poller.start()
            try:
                self.assertTrue(entered.wait(1))
                poller.request(session)
                poller.request(session)
                release.set()
                self.assertTrue(done.wait(1))
                for _ in range(100):
                    if session.outbox:
                        break
                    time.sleep(.005)
                self.assertEqual(session.outbox, ['finished'])
                self.assertEqual(cycle.call_count, 1)
            finally:
                release.set()
                poller.stop()
        self.assertFalse(poller.thread.is_alive())

    def test_poll_interval_repeats_without_new_thread(self):
        poller = plugin.Poller(self.core, self.path)
        second = threading.Event()
        count = []
        def poll():
            count.append(threading.get_ident())
            if len(count) >= 2:
                second.set()
            return 'ok'
        with patch.object(plugin, 'POLL_INTERVAL', .02), patch.object(poller, 'poll_once', side_effect=poll):
            poller.start()
            try:
                self.assertTrue(second.wait(1))
            finally:
                poller.stop()
        self.assertEqual(len(set(count)), 1)

    def test_other_house_cannot_control_or_request_poll(self):
        core = Mock()
        core.session.metadata = {'home': '70'}
        with patch.object(plugin, '_operate') as operate, patch.object(plugin, 'start_background') as start:
            plugin.light_lustra(core)
            plugin.request_poll(core, '')
            operate.assert_not_called()
            start.assert_not_called()

    def test_plugin_loading_does_not_start_poller_and_hooks_are_called(self):
        core = SiriusCore()
        start, stop = Mock(), Mock()
        core.plugin_manifests = {'fake': {'start_background': start, 'stop_background': stop}}
        core.start_background_services()
        core.stop_background_services()
        start.assert_called_once_with(core)
        stop.assert_called_once_with()

if __name__ == '__main__':
    unittest.main()
