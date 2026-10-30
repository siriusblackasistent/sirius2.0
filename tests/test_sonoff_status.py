from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from plugins import plugin_light_control_hause30 as house30
from plugins import plugin_light_control_hause0 as house0


class SonoffAndTemperatureTests(unittest.TestCase):
    def setUp(self):
        house30._OBSERVATIONS.clear()
        house30._AC_OBSERVATION.clear()
        house30._DEVICE_LOCKS.clear()

    def response(self, switch=None, error=0):
        result = Mock()
        result.json.return_value = {'error': error, 'data': {} if switch is None else {'switch': switch}}
        return result

    def test_sonoff_delayed_confirmation_does_not_repeat_switch(self):
        for block in (house30.light_bahroma, house30.banya):
            config = block(action='config')
            with patch('requests.post', side_effect=[self.response('off'), self.response(),
                       self.response('off'), self.response('on')]) as post, patch.object(house30.time, 'sleep'):
                self.assertTrue(house30._operate(config, 'toggle'))
            methods = [call.args[0].rsplit('/', 1)[1] for call in post.call_args_list]
            self.assertEqual(methods, ['info', 'switch', 'info', 'info'])
            self.assertTrue(all(call.kwargs['json']['deviceid'] == config['device_id'] for call in post.call_args_list))
            self.assertEqual(post.call_args_list[1].kwargs['json']['data'], {'switch': 'on'})

    def test_unconfirmed_sonoff_never_reports_success_or_switches_again(self):
        with patch('requests.post', side_effect=[self.response('off'), self.response()] +
                   [self.response('off') for _ in range(house30.CONFIRM_ATTEMPTS)]) as post, \
                patch.object(house30.time, 'sleep'):
            with self.assertRaisesRegex(ValueError, 'not confirmed'):
                house30._operate(house30.banya(action='config'), 'toggle')
        self.assertEqual(sum(call.args[0].endswith('/switch') for call in post.call_args_list), 1)

    def test_rejected_switch_does_not_report_success(self):
        with patch('requests.post', side_effect=[self.response('off'), self.response(error=400)]):
            with self.assertRaisesRegex(ValueError, 'Sonoff rejected'):
                house30._operate(house30.light_bahroma(action='config'), 'on')

    def test_ac_poll_is_read_only_and_status_has_temperatures_without_dates(self):
        appliance = SimpleNamespace(state=SimpleNamespace(indoor_temperature=24.5, outdoor_temperature=12,
                                    running=True), set_state=Mock())
        with patch.object(house30, 'MIDEA_ACCOUNT', 'test'), patch.object(house30, 'MIDEA_PASSWORD', 'test'), \
                patch.object(house30, 'get_midea_cloud_appliances', return_value=(None, [appliance])):
            house30._poll_ac()
        appliance.set_state.assert_not_called()
        with TemporaryDirectory() as folder:
            house30._record(house30.banya(action='config'), False)
            text = house30._write_status(Path(folder) / 'status.txt')
        self.assertIn('Кондиціонер: увімкнено', text)
        self.assertIn('Температура в будинку: 24.5 °C', text)
        self.assertIn('Температура надворі (кондиціонер): 12 °C', text)
        self.assertNotRegex(text, r'\d{2}\.\d{2}\.\d{4}|\d{2}:\d{2}')

    def test_ac_failure_preserves_but_marks_old_temperature(self):
        house30._AC_OBSERVATION.update(indoor=22, outdoor=10, running=False)
        with patch.object(house30, 'MIDEA_ACCOUNT', 'test'), patch.object(house30, 'MIDEA_PASSWORD', 'test'), \
                patch.object(house30, 'get_midea_cloud_appliances', side_effect=RuntimeError):
            house30._poll_ac()
        with TemporaryDirectory() as folder:
            text = house30._write_status(Path(folder) / 'status.txt')
        self.assertIn('22 °C (останнє отримане значення)', text)
        self.assertIn('Кондиціонер: недоступний', text)

    def test_house0_status_also_has_no_dates(self):
        with TemporaryDirectory() as folder:
            text = house0._write_status(Path(folder) / 'status.txt')
        self.assertEqual(text.splitlines()[0], 'Будинок 0')
        self.assertNotRegex(text, r'\d{2}\.\d{2}\.\d{4}|\d{2}:\d{2}')


if __name__ == '__main__':
    unittest.main()
