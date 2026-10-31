import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import winreg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import file_safety as files
import startup_settings as startup
import vm_manager as vm
from windows_shortcuts import read_shortcut, write_shortcut


class LogicTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.exe = self.root / 'voicemeeterpro_x64.exe'
        self.exe.touch()

    def state(self, enabled=False, hidden=False, entries=None, errors=None):
        return dict(enabled=enabled, hidden=hidden, entries=entries or [], errors=errors or [])

    def test_native_shortcut_roundtrip_preserves_spaced_config_path(self):
        link = self.root / startup.NATIVE_LINK
        args = '-L"C:\\My Documents\\settings.xml" -h'
        write_shortcut(link, str(self.exe), args)
        read = read_shortcut(link)
        self.assertEqual(read['target'], str(self.exe))
        self.assertEqual(read['arguments'], args)
        self.assertTrue(startup.is_hidden(read['arguments']))

    def test_native_named_startup_detected_by_target_not_manager_filename(self):
        link = self.root / startup.NATIVE_LINK
        write_shortcut(link, str(self.exe), '-h')
        with patch.object(startup, 'startup_folders', return_value=[(winreg.HKEY_CURRENT_USER, self.root)]), patch.object(startup, '_approved', return_value=True), patch.object(startup.winreg, 'OpenKey', side_effect=FileNotFoundError):
            state = startup.inspect_startup([str(self.exe)])
        self.assertTrue(state['enabled'])
        self.assertTrue(state['hidden'])
        self.assertEqual(state['entries'][0]['name'], startup.NATIVE_LINK)

    def test_unrelated_link_with_familiar_filename_not_treated_as_enabled(self):
        write_shortcut(self.root / startup.NATIVE_LINK, 'C:\\Windows\\notepad.exe', '-h')
        with patch.object(startup, 'startup_folders', return_value=[(winreg.HKEY_CURRENT_USER, self.root)]), patch.object(startup.winreg, 'OpenKey', side_effect=FileNotFoundError):
            self.assertFalse(startup.inspect_startup([str(self.exe)])['enabled'])

    def test_already_hidden_is_idempotent_no_write(self):
        with patch.object(startup, 'inspect_startup', return_value=self.state(True, True)), patch.object(startup, 'write_shortcut') as write:
            self.assertTrue(startup.ensure_hidden_startup([str(self.exe)], str(self.exe))[0])
            write.assert_not_called()

    def test_only_adds_hidden_argument_to_existing_native_link(self):
        entry = dict(root=winreg.HKEY_CURRENT_USER, enabled=True, hidden=False, kind='shortcut', path=str(self.root / startup.NATIVE_LINK), target=str(self.exe), arguments='-L"C:\\custom.xml"')
        with patch.object(startup, 'inspect_startup', side_effect=[self.state(True, False, [entry]), self.state(True, True)]), patch.object(startup, 'write_shortcut') as write:
            self.assertTrue(startup.ensure_hidden_startup([str(self.exe)], str(self.exe))[0])
            self.assertEqual(write.call_args.args[0], entry['path'])
            self.assertEqual(write.call_args.args[2], '-L"C:\\custom.xml" -h')
            self.assertTrue(write.call_args.kwargs['update'])

    def test_windows_disabled_entry_does_not_create_duplicate(self):
        with patch.object(startup, 'inspect_startup', return_value=self.state(entries=[dict(enabled=False)])), patch.object(startup, 'write_shortcut') as write:
            self.assertFalse(startup.ensure_hidden_startup([str(self.exe)], str(self.exe))[0])
            write.assert_not_called()

    def test_inspection_error_does_not_mean_not_configured(self):
        with patch.object(startup, 'inspect_startup', return_value=self.state(None, errors=['Access denied'])), patch.object(startup, 'write_shortcut') as write:
            self.assertFalse(startup.ensure_hidden_startup([str(self.exe)], str(self.exe))[0])
            write.assert_not_called()

    def test_missing_registration_creates_only_native_named_hidden_entry(self):
        with patch.object(startup, 'inspect_startup', side_effect=[self.state(), self.state(True, True)]), patch.object(startup, 'startup_folders', return_value=[(winreg.HKEY_CURRENT_USER, self.root)]), patch.object(startup, 'write_shortcut') as write:
            self.assertTrue(startup.ensure_hidden_startup([str(self.exe)], str(self.exe))[0])
            self.assertEqual(Path(write.call_args.args[0]).name, startup.NATIVE_LINK)
            self.assertEqual(write.call_args.args[2], '-h')

    def test_hiding_machine_startup_does_not_request_admin_or_duplicate_it(self):
        entry = dict(root=winreg.HKEY_LOCAL_MACHINE, enabled=True, hidden=False)
        with patch.object(startup, 'inspect_startup', return_value=self.state(True, False, [entry])), patch.object(startup, 'write_shortcut') as write:
            self.assertFalse(startup.ensure_hidden_startup([str(self.exe)], str(self.exe))[0])
            write.assert_not_called()

    def test_failed_json_serialization_keeps_original(self):
        path = self.root / 'Local State'
        path.write_bytes(b'{"original":true}')
        with patch.object(files.json, 'dumps', side_effect=OSError('simulated disk failure')):
            with self.assertRaises(OSError):
                files.atomic_json(path, {'new': True})
        self.assertEqual(path.read_bytes(), b'{"original":true}')

    def test_failed_replace_keeps_original_and_cleans_temporary(self):
        path = self.root / 'Local State'
        path.write_bytes(b'original')
        with patch.object(files.os, 'replace', side_effect=OSError('locked file')):
            with self.assertRaises(OSError):
                files.atomic_json(path, {'new': True})
        self.assertEqual(path.read_bytes(), b'original')
        self.assertEqual(list(self.root.glob('*.tmp')), [])

    def test_backups_do_not_overwrite_previous_snapshot(self):
        path = self.root / 'Local State'
        path.write_bytes(b'a')
        first = files.backup_file(path)
        path.write_bytes(b'b')
        second = files.backup_file(path)
        self.assertNotEqual(first, second)
        self.assertEqual(first.read_bytes(), b'a')

    def test_restore_rejects_live_browser_before_writing(self):
        with patch.object(vm, 'is_process_running', return_value=True), patch.object(vm, 'atomic_json') as write:
            self.assertFalse(vm.restore_chrome_backup()[0])
            write.assert_not_called()

    def test_force_flag_cannot_bypass_live_browser_guard(self):
        (self.root / 'Local State').write_text('{}')
        with patch.object(vm, 'get_chrome_user_data_path', return_value=str(self.root)), patch.object(vm, 'is_process_running', return_value=True), patch.object(vm, 'atomic_json') as write:
            self.assertFalse(vm.apply_chrome_audio_flags(kill_chrome_first=True)[0])
            write.assert_not_called()

    def test_bad_restore_json_keeps_current_profile(self):
        current = self.root / 'Local State'
        current.write_text('{"current":1}')
        backup = self.root / 'Local State.bak_bad'
        backup.write_text('invalid json')
        with patch.object(vm, 'get_chrome_user_data_path', return_value=str(self.root)), patch.object(vm, 'is_process_running', return_value=False):
            self.assertFalse(vm.restore_chrome_backup(str(backup))[0])
        self.assertEqual(current.read_text(), '{"current":1}')

    def test_save_without_restart_does_not_launch_or_change_startup(self):
        config = self.root / 'config.xml'
        config.write_text('<OutputDev index="1" name="speaker" />')
        docs = self.root / 'docs'
        with patch.object(vm, 'DOCS_VM_DIR', str(docs)), patch.object(vm, 'is_process_running', return_value=False), patch.object(vm, 'launch_voicemeeter') as launch, patch.object(vm, 'configure_autostart') as startup_change:
            self.assertTrue(vm.apply_xml_config(str(config), restart_app=False)[0])
            launch.assert_not_called()
            startup_change.assert_not_called()
        self.assertEqual((docs / 'VoicemeeterBanana_LastSettings.xml').read_text(), config.read_text())

    def test_no_restart_while_running_leaves_config_unchanged(self):
        config = self.root / 'config.xml'
        config.write_text('<OutputDev index="1" name="speaker" />')
        with patch.object(vm, 'is_process_running', return_value=True), patch.object(vm, 'atomic_copy') as write:
            self.assertFalse(vm.apply_xml_config(str(config), restart_app=False)[0])
            write.assert_not_called()

    def test_builtin_sample_uses_current_documents_recording_directory(self):
        sample = self.root / 'sample_config.xml'
        sample.write_text('<OutputDev index="1" name="speaker" /><RecordDirectory></RecordDirectory>')
        docs = self.root / 'User & Documents'
        with patch.object(vm, 'get_resource_path', return_value=str(sample)), patch.object(vm, 'DOCS_VM_DIR', str(docs)), patch.object(vm, 'is_process_running', return_value=False):
            self.assertTrue(vm.apply_xml_config(str(sample), restart_app=False)[0])
        self.assertIn('User &amp; Documents</RecordDirectory>', (docs / 'VoicemeeterBanana_LastSettings.xml').read_text())

    def test_shutdown_failure_does_not_replace_settings(self):
        config = self.root / 'config.xml'
        config.write_text('<OutputDev index="1" name="speaker" />')
        with patch.object(vm, 'is_process_running', return_value=True), patch.object(vm, 'kill_voicemeeter', return_value=(False, 'cannot stop')), patch.object(vm, 'atomic_copy') as write:
            self.assertFalse(vm.apply_xml_config(str(config))[0])
            write.assert_not_called()

    def test_running_menu_defaults_use_automatic_apply_chain(self):
        with patch.object(vm, 'is_process_running', return_value=True), patch.object(vm, 'apply_xml_config', return_value=(True, 'loaded')) as apply, patch.object(vm.winreg, 'CreateKey') as write:
            self.assertTrue(vm.configure_voicemeeter_menu_defaults('selected.xml')[0])
            apply.assert_called_once_with('selected.xml', restart_app=True, set_as_default=True)
            write.assert_not_called()

    def test_audio_refresh_never_runs_helper_executable(self):
        result = dict(success=True, playback_default="Voicemeeter Input", record_default="Voicemeeter Out B1")
        with patch.object(vm, 'query_system_audio', return_value=result), patch.object(vm.subprocess, 'run') as run, patch.object(vm.subprocess, 'check_output') as output:
            self.assertTrue(vm.get_system_audio_status()["is_playback_match"])
            run.assert_not_called()
            output.assert_not_called()

    def test_another_mutation_cannot_run_concurrently(self):
        entered, release = threading.Event(), threading.Event()
        def hold_lock():
            with vm._operation_lock:
                entered.set()
                release.wait(5)
        worker = threading.Thread(target=hold_lock)
        worker.start()
        self.assertTrue(entered.wait(2))
        try:
            ok, message = vm.configure_autostart()
            self.assertFalse(ok)
            self.assertIn('另一项', message)
        finally:
            release.set()
            worker.join()

    def test_menu_defaults_never_changes_vendor_trial_counter(self):
        with patch.object(vm, 'is_process_running', return_value=False), patch.object(vm, 'get_installed_exe', return_value=str(self.exe)), patch.object(vm.winreg, 'CreateKey'), patch.object(vm.winreg, 'SetValueEx') as set_value, patch.object(vm, 'configure_autostart', return_value=(True, 'already enabled')):
            self.assertTrue(vm.configure_voicemeeter_menu_defaults()[0])
            self.assertNotIn('nbLaunch', [call.args[1] for call in set_value.call_args_list])

    def test_second_default_config_failure_rolls_back_first_file(self):
        source, first, second = [self.root / name for name in ('new.xml', 'LastSettings.xml', 'TodaySettings.xml')]
        source.write_bytes(b'new')
        first.write_bytes(b'old-first')
        second.write_bytes(b'old-second')
        copy = files.atomic_copy
        calls = 0
        def fail_second(src, dest):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError('second destination unavailable')
            copy(src, dest)
        with patch.object(files, 'atomic_copy', side_effect=fail_second), self.assertRaises(OSError):
            files.replace_default_configs(source, [first, second])
        self.assertEqual(first.read_bytes(), b'old-first')
        self.assertEqual(second.read_bytes(), b'old-second')



if __name__ == '__main__':
    unittest.main()
