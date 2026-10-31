import ctypes
import itertools
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import vm_manager as vm
import voicemeeter_process as processes
import voicemeeter_remote as remote

CONFIG = '''<VBAudioVoicemeeterSettings>
<InputDev index="1" name="Test Mic" />
<Strip index="1" mute="0" solo="0" busa="0" busb="1" dblevel="-3.00" />
<Bus index="1" mute="0" mono="0" dblevel="0.00" />
</VBAudioVoicemeeterSettings>'''


class Function:
    def __init__(self, function):
        self.function = function
    def __call__(self, *args):
        return self.function(*args)


class Engine:
    def __init__(self, selected):
        self.running = True
        self.selected = selected
        self.events = []
        self.resist_shutdown = True
        self.ignore_load = False
        self.drop_loads = 0
        self.save_on_exit = False
        self.values = {name: 99.0 for name in remote.expected_parameters(CONFIG)}
        self.logins = 0
        self.VBVMR_Login = Function(self.login)
        self.VBVMR_Logout = Function(lambda: 0)
        self.VBVMR_IsParametersDirty = Function(lambda: 0 if self.running else -1)
        self.VBVMR_SetParameterFloat = Function(self.float_command)
        self.VBVMR_SetParameterStringW = Function(self.string_command)
        self.VBVMR_GetParameterFloat = Function(self.get_float)

    def login(self):
        self.logins += 1
        return 0

    def float_command(self, name, value):
        if name == b'Command.Shutdown':
            self.events.append('shutdown')
            if not self.resist_shutdown:
                self.running = False
                if self.save_on_exit:
                    self.selected.write_text(CONFIG.replace('mute="0"', 'mute="1"'), encoding='utf-8')
        return 0

    def string_command(self, name, path):
        assert name == b'Command.Load'
        self.events.append('load')
        if self.drop_loads:
            self.drop_loads -= 1
            return 0
        if not self.ignore_load:
            self.values.update(remote.expected_parameters(Path(path).read_text(encoding='utf-8')))
        return 0

    def get_float(self, name, output):
        ctypes.cast(output, ctypes.POINTER(ctypes.c_float)).contents.value = self.values[name.decode()]
        return 0

    def force(self, paths):
        self.events.append('force')
        self.running = False
        return [123]

    def launch(self, path, hide=False):
        assert hide
        self.events.append('launch')
        self.running = True
        return True, 'process created but not loaded'

    def menus(self, *args, **kwargs):
        self.events.append('menus')
        return True, 'defaults restored'


class ApplyChainTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.selected = self.root / '选中的配置 with spaces.xml'
        self.selected.write_text(CONFIG, encoding='utf-8')
        self.engine = Engine(self.selected)

    def apply(self):
        remote.close()
        with patch.object(remote.ctypes, 'WinDLL', return_value=self.engine), patch.object(remote.time, 'sleep'), patch.object(vm, 'is_process_running', side_effect=lambda *args: self.engine.running), patch.object(vm, 'wait_voicemeeter_exit', side_effect=lambda *args: not self.engine.running), patch.object(vm, 'force_stop', side_effect=self.engine.force), patch.object(vm, 'get_remote_dll', return_value='vendor.dll'), patch.object(vm, 'configure_voicemeeter_menu_defaults', side_effect=self.engine.menus), patch.object(vm, 'launch_voicemeeter', side_effect=self.engine.launch), patch.object(vm, 'DOCS_VM_DIR', str(self.root / 'Documents')), patch.object(remote.time, 'monotonic', side_effect=itertools.count().__next__):
            result = vm.apply_xml_config(str(self.selected))
        remote.close()
        return result

    def test_timeout_force_stop_then_save_launch_explicit_load_and_readback(self):
        ok, message = self.apply()
        self.assertTrue(ok, message)
        self.assertEqual(self.engine.events, ['shutdown', 'force', 'menus', 'launch', 'load'])
        self.assertEqual(self.engine.values, remote.expected_parameters(CONFIG))
        self.assertEqual(self.engine.logins, 1)

    def test_graceful_exit_does_not_force_kill(self):
        self.engine.resist_shutdown = False
        self.assertTrue(self.apply()[0])
        self.assertNotIn('force', self.engine.events)

    def test_builtin_preset_restores_a1_monitoring_for_both_virtual_channels(self):
        sample = Path(__file__).resolve().parents[1] / 'sample_config.xml'
        self.selected.write_bytes(sample.read_bytes())
        ok, message = self.apply()
        self.assertTrue(ok, message)
        # Screenshot contract: both virtual inputs feed headphones via A1,
        # while channel 1 retains B1 and channel 2 retains B2 independently.
        expected = {'Strip[3].A1': 1, 'Strip[3].B1': 1, 'Strip[3].B2': 0,
                    'Strip[4].A1': 1, 'Strip[4].B1': 0, 'Strip[4].B2': 1}
        for name, value in expected.items():
            self.assertEqual(self.engine.values[name], value, name)

    def test_process_creation_without_effective_load_must_fail(self):
        self.engine.ignore_load = True
        ok, message = self.apply()
        self.assertFalse(ok)
        self.assertIn('参数仍不一致', message)
        self.assertTrue(self.engine.running)

    def test_load_lost_during_engine_reconnect_is_reissued(self):
        self.engine.drop_loads = 1
        ok, message = self.apply()
        self.assertTrue(ok, message)
        self.assertEqual(self.engine.events.count('load'), 2)
        self.assertEqual(self.engine.values, remote.expected_parameters(CONFIG))

    def test_freezes_selected_lastsettings_before_voice_app_overwrites_on_exit(self):
        self.engine.resist_shutdown = False
        self.engine.save_on_exit = True
        self.assertTrue(self.apply()[0])
        saved = self.root / 'Documents/VoicemeeterBanana_LastSettings.xml'
        self.assertEqual(saved.read_text(encoding='utf-8'), CONFIG)
        self.assertEqual(self.engine.values['Strip[0].Mute'], 0)

    def test_failed_force_stop_never_overwrites_or_reopens(self):
        with patch.object(vm, 'is_process_running', return_value=True), patch.object(remote, 'request_shutdown', side_effect=OSError('API unavailable')), patch.object(vm, 'force_stop', side_effect=PermissionError('Access denied')), patch.object(vm, 'launch_voicemeeter') as launch, patch.object(vm, 'replace_default_configs') as write:
            ok, message = vm.apply_xml_config(str(self.selected))
        self.assertFalse(ok)
        self.assertIn('Access denied', message)
        launch.assert_not_called()
        write.assert_not_called()

    def test_running_process_with_configuration_is_loaded_not_treated_as_noop(self):
        exe = self.root / 'voicemeeterpro_x64.exe'
        exe.touch()
        with patch.object(vm, 'get_installed_exe', return_value=str(exe)), patch.object(vm, 'is_process_running', return_value=True), patch.object(remote, 'load_and_verify', return_value=(True, 'verified')) as load:
            self.assertTrue(vm.launch_voicemeeter(str(self.selected))[0])
            load.assert_called_once()

    def test_windows_launch_keeps_literal_vendor_quoted_L_switch(self):
        exe = self.root / 'Program Files/voicemeeterpro_x64.exe'
        exe.parent.mkdir()
        exe.touch()
        with patch.object(vm, 'get_installed_exe', return_value=str(exe)), patch.object(vm, 'is_process_running', return_value=False), patch.object(vm.subprocess, 'Popen') as launch:
            self.assertTrue(vm.launch_voicemeeter(str(self.selected), hide=True)[0])
        command = launch.call_args.args[0]
        self.assertIsInstance(command, str)
        self.assertIn(' -L"' + str(self.selected) + '" -h', command)
        self.assertEqual(launch.call_args.kwargs['executable'], str(exe))

    def test_eq_cell_values_do_not_override_bus_master_gain_in_verification(self):
        content = CONFIG + '<VoiceMeeterBUSEQ extra="1"><Bus index="1" channel="1" dblevel="-20" />'
        self.assertEqual(remote.expected_parameters(content)['Bus[0].Gain'], 0)

    def test_nonfinite_xml_parameters_are_not_accepted_as_matching(self):
        with self.assertRaises(ValueError):
            remote.expected_parameters(CONFIG.replace('-3.00', 'NaN'))

    def test_force_stop_skips_same_named_executable_outside_installation(self):
        api = MagicMock()
        api.OpenProcess.side_effect = [1001, 1002]
        api.WaitForSingleObject.side_effect = [258, 0]
        expected = str(self.root / 'voicemeeterpro_x64.exe')
        other = str(self.root / 'unrelated/voicemeeterpro_x64.exe')
        with patch.object(processes, 'kernel', return_value=api), patch.object(processes, 'candidate_pids', return_value=[10, 20]), patch.object(processes, 'image_path', side_effect=[processes.normalized(expected), processes.normalized(other)]):
            self.assertEqual(processes.force_stop([expected]), [10])
        api.TerminateProcess.assert_called_once_with(1001, 1)
        self.assertEqual(api.CloseHandle.call_count, 2)

    def test_force_stop_waits_for_actual_exit_before_reporting_success(self):
        api = MagicMock()
        api.OpenProcess.return_value = 1001
        api.WaitForSingleObject.side_effect = [258, 258]
        expected = str(self.root / 'voicemeeterpro_x64.exe')
        with patch.object(processes, 'kernel', return_value=api), patch.object(processes, 'candidate_pids', return_value=[10]), patch.object(processes, 'image_path', return_value=processes.normalized(expected)):
            with self.assertRaises(OSError):
                processes.force_stop([expected])


if __name__ == '__main__':
    unittest.main()
