import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import file_safety as files
import xml_config as xml
import voicemeeter_remote as remote
import vm_manager as vm
import secure_install as secure
import backup_maintenance as maintenance
import test_apply_chain as chain
from test_apply_chain import CONFIG, Function


class SecurityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / 'Local State'
        self.state.write_text('{"profile":{"last_used":"Default"}}', encoding='utf-8')

    def test_linear_parser_large_invalid_attribute_with_valid_preview(self):
        content = '<InputDev index="1" name="mic"/><Strip ' + 'a' * 1024 * 1024 + ' index="4" busa="1"/>'
        started = time.perf_counter()
        self.assertEqual(remote.expected_parameters(content), {'Strip[3].A1': 1})
        self.assertLess(time.perf_counter() - started, 3)

    def test_vendor_tolerance_and_opposite_quote_preserved(self):
        self.assertEqual(xml.attributes('name="user\'s mic" busa=\'1\''), {'name': "user's mic", 'busa': '1'})
        sample = (Path(vm.__file__).parent / 'sample_config.xml').read_text(encoding='utf-8-sig')
        self.assertEqual(len(remote.expected_parameters(sample)), 60)
        self.assertEqual(xml.network_risks(sample), [])

    def test_commit_preserves_actual_original(self):
        data, expected = files.read_json_snapshot(self.state)
        original = self.state.read_bytes()
        data['new'] = True
        backup = files.guarded_json(self.state, data, expected, lambda: True)
        self.assertEqual(backup.read_bytes(), original)
        self.assertTrue(json.loads(self.state.read_bytes())['new'])

    def test_missing_state_restored_without_overwriting_new_file(self):
        self.state.unlink()
        files.guarded_json(self.state, {'restored': 1}, None, lambda: True)
        with self.assertRaises(files.FileConflict):
            files.guarded_json(self.state, {'restored': 2}, None, lambda: True)
        self.assertEqual(json.loads(self.state.read_bytes()), {'restored': 1})

    def test_change_before_commit_is_not_overwritten(self):
        data, expected = files.read_json_snapshot(self.state)
        self.state.write_text('{"Profile 9":"new account"}')
        with self.assertRaises(files.FileConflict):
            files.guarded_json(self.state, data, expected, lambda: True)
        self.assertEqual(json.loads(self.state.read_bytes()), {'Profile 9': 'new account'})

    def test_last_instant_race_restores_and_retains_profile(self):
        data, expected = files.read_json_snapshot(self.state)
        replace = files.replace_with_preimage
        count = 0
        competing = b'{"Profile 9":"new account at commit"}'
        def race(destination, replacement, backup):
            nonlocal count
            count += 1
            if count == 1:
                self.state.write_bytes(competing)
            replace(destination, replacement, backup)
        with patch.object(files, 'replace_with_preimage', side_effect=race):
            with self.assertRaises(files.FileConflict):
                files.guarded_json(self.state, data, expected, lambda: True)
        self.assertEqual(self.state.read_bytes(), competing)
        self.assertTrue(any(p.read_bytes() == competing for p in self.root.glob('*.bak_conflict_*')))
        self.assertEqual(list(self.root.glob('*.tmp')), [])

    def test_live_browser_cancels_commit(self):
        data, expected = files.read_json_snapshot(self.state)
        original = self.state.read_bytes()
        with self.assertRaises(files.FileConflict):
            files.guarded_json(self.state, data, expected, lambda: False)
        self.assertEqual(self.state.read_bytes(), original)

    def test_apply_flags_and_restore_work_with_real_temp_files(self):
        with patch.object(vm, 'get_chrome_user_data_path', return_value=str(self.root)), patch.object(vm, 'is_process_running', return_value=False), patch.object(vm, 'get_chrome_version', return_value=(156, None)):
            ok, message = vm.apply_chrome_audio_flags()
            self.assertTrue(ok, message)
            self.assertIn('enable-webrtc-allow-input-volume-adjustment@2', json.loads(self.state.read_bytes())['browser']['enabled_labs_experiments'])
            backup = vm.get_chrome_backups()[0][1]
            ok, message = vm.restore_chrome_backup(backup)
            self.assertTrue(ok, message)
            self.assertNotIn('browser', json.loads(self.state.read_bytes()))

    def test_active_network_rejected_before_shutting_down(self):
        config = self.root / 'network.xml'
        config.write_text(CONFIG + '<VBAN status="1"/><VBANStreamOut status="1" ip="192.168.1.8" port="6980"/>')
        with patch.object(vm, 'kill_voicemeeter') as stop:
            ok, message = vm.apply_xml_config(config)
            self.assertFalse(ok)
            self.assertIn('192.168.1.8', message)
            stop.assert_not_called()

    def test_reviewed_file_change_rejected_before_shutting_down(self):
        config = self.root / 'changed.xml'
        config.write_text(CONFIG)
        digest = xml.inspect_file(config)['sha256']
        config.write_text(CONFIG + '<VBAN status="1"/>')
        with patch.object(vm, 'kill_voicemeeter') as stop:
            self.assertFalse(vm.apply_xml_config(config, expected_config_hash=digest, allow_network_config=True)[0])
            stop.assert_not_called()

    def test_network_control_and_audio_directions_are_in_preview(self):
        content = '<VBAN status="1"/>' + ''.join(f'<{tag} status="1" index="0" ip="10.0.0.8"/>' for tag in ('VBANStreamOut', 'VBANStreamIn', 'VBANCommandIn', 'VBANDataIn', 'VBANDataOut'))
        risks = '\n'.join(xml.network_risks(content))
        for label in ('发送音频', '接收音频', '控制命令', '数据/MIDI', '10.0.0.8'):
            self.assertIn(label, risks)
        self.assertEqual(xml.network_risks(content.replace('VBAN status="1"', 'VBAN status="0"')), [])

    def test_cleanup_retains_newest_ten_and_conflict_recovery(self):
        for i in range(12):
            path = self.root / f'Local State.bak_{i:02}'
            path.write_bytes(bytes([i]))
            os.utime(path, (1000 + i, 1000 + i))
        conflict = self.root / 'Local State.bak_conflict_saved'
        conflict.write_bytes(b'important')
        os.utime(conflict, (1, 1))
        plan = maintenance.cleanup_plan(self.root, now=50 * 86400)
        self.assertEqual(len(plan), 2)
        self.assertEqual(maintenance.apply_cleanup(self.root, plan), 2)
        self.assertTrue(conflict.exists())
        self.assertEqual(len(list(self.root.glob('Local State.bak_*'))), 11)

    def test_cleanup_refuses_changed_or_outside_file(self):
        path = self.root / 'Local State.bak_test'
        path.write_bytes(b'changed')
        with self.assertRaises(ValueError):
            maintenance.apply_cleanup(self.root, [{'path': str(path), 'sha256': '0' * 64}])
        with self.assertRaises(ValueError):
            maintenance.apply_cleanup(self.root, [{'path': str(self.root.parent / 'Local State.bak_test'), 'sha256': '0' * 64}])
        self.assertTrue(path.exists())

    def test_cache_removed_after_broker_completion_but_retained_if_still_running(self):
        for running in (False, True):
            cache = self.root / str(running)
            with patch.object(vm, 'CACHE_DIR', str(cache)), patch.object(vm, 'download_file_with_ua'), patch.object(vm, 'verify_package'), patch.object(vm, 'safe_extract_zip'), patch.object(vm, 'launch_verified_archive', side_effect=secure.BrokerStillRunning('still running') if running else None):
                ok, message = vm._install_reviewed_driver('vac')
                self.assertEqual(ok, not running, message)
                self.assertEqual(bool(list(cache.iterdir())), running)

    def test_broker_waits_and_reports_failure_or_timeout(self):
        for wait, exitcode in ((0, 0), (0, 1), (258, 0)):
            kernel, shell = MagicMock(), MagicMock()
            def execute(pointer):
                ctypes.cast(pointer, ctypes.POINTER(secure.SHELLEXECUTEINFO)).contents.hProcess = 123
                return 1
            def get_exit(handle, output):
                ctypes.cast(output, ctypes.POINTER(wintypes.DWORD)).contents.value = exitcode
                return 1
            shell.ShellExecuteExW = Function(execute)
            kernel.WaitForSingleObject = Function(lambda *args: wait)
            kernel.GetExitCodeProcess = Function(get_exit)
            with patch.object(secure.ctypes, 'WinDLL', side_effect=lambda name, **kw: shell if name == 'shell32' else kernel):
                if wait:
                    with self.assertRaises(secure.BrokerStillRunning):
                        secure._launch_broker('fixed command')
                elif exitcode:
                    with self.assertRaises(OSError):
                        secure._launch_broker('fixed command')
                else:
                    secure._launch_broker('fixed command')
                kernel.CloseHandle.assert_called_once_with(123)

    def navigation(self, window_class, modifier):
        import chrome_navigation as navigation
        user, kernel = MagicMock(), MagicMock()
        chrome = str(self.root / 'chrome.exe')
        def name(hwnd, output, count):
            output.value = window_class
            return len(window_class)
        def image(handle, flags, output, count):
            output.value = chrome
            return 1
        user.GetForegroundWindow = Function(lambda: 123)
        user.GetClassNameW = Function(name)
        user.GetAsyncKeyState = Function(lambda vk: modifier)
        kernel.OpenProcess = Function(lambda *args: 321)
        kernel.QueryFullProcessImageNameW = Function(image)
        user.SendInput = Function(lambda count, *args: count)
        with patch.object(navigation.ctypes, 'WinDLL', side_effect=lambda name, **kw: user if name == 'user32' else kernel), patch.object(navigation.time, 'sleep'):
            return navigation.navigate_chrome_omnibox(chrome, 'chrome://flags/', timeout=0.1)

    def test_chrome_navigation_rejects_nonbrowser_window_class(self):
        self.assertFalse(self.navigation('NotAChromeWindow', 0))

    def test_chrome_navigation_rejects_held_modifier(self):
        self.assertFalse(self.navigation('Chrome_WidgetWin_1', 0x8000))

    def test_chrome_navigation_still_sends_fixed_url_when_guards_pass(self):
        self.assertTrue(self.navigation('Chrome_WidgetWin_1', 0))


class ApprovedNetworkTests(unittest.TestCase):
    # Reuse the real load-chain simulation with additional approval options.
    setUp = chain.ApplyChainTests.setUp
    apply = chain.ApplyChainTests.apply
    def test_explicit_approval_keeps_network_configuration_and_load_chain(self):
        self.selected.write_text(CONFIG + '<VBAN status="1"/><VBANStreamOut status="1" ip="10.0.0.8"/>')
        original_apply = vm.apply_xml_config
        digest = xml.inspect_file(self.selected)['sha256']
        with patch.object(vm, 'apply_xml_config', side_effect=lambda *args, **kw: original_apply(*args, **kw, allow_network_config=True, expected_config_hash=digest)):
            ok, message = self.apply()
        self.assertTrue(ok, message)
        self.assertIn('load', self.engine.events)
        saved = (self.root / 'Documents/VoicemeeterBanana_LastSettings.xml').read_text(encoding='utf-8')
        self.assertIn('10.0.0.8', saved)


if __name__ == '__main__':
    unittest.main()
