import base64
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import secure_install as secure
import vm_manager as vm


class FixedTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_unsigned_or_changed_archive_never_reaches_elevation(self):
        bad = self.root / 'fake.zip'
        bad.write_bytes(b'not the vendor package')
        for package in ('vbcable', 'vac'):
            with self.subTest(package=package), patch.object(secure, '_launch_broker') as elevate:
                with self.assertRaises(ValueError):
                    secure.launch_verified_archive(bad, package, self.root)
                elevate.assert_not_called()

    def test_signature_failure_prevents_local_elevation(self):
        installer = self.root / 'fake.exe'
        installer.write_bytes(b'not a signed PE')
        with patch.object(secure.subprocess, 'run') as powershell, patch.object(secure, '_launch_broker') as elevate:
            powershell.return_value.returncode = 1
            with self.assertRaises(ValueError):
                secure.launch_verified_local(installer)
            elevate.assert_not_called()

    def test_signature_failure_prevents_archive_elevation(self):
        for package in ('vbcable', 'vac'):
            with self.subTest(package=package), patch.object(secure, 'verify_package'), patch.object(secure, 'verify_installer', side_effect=ValueError('bad publisher')), patch.object(secure, '_launch_broker') as elevate:
                with self.assertRaises(ValueError):
                    secure.launch_verified_archive(self.root / 'vendor.zip', package, self.root)
                elevate.assert_not_called()

    def test_broker_receives_pinned_hash_not_computed_untrusted_archive_hash(self):
        with patch.object(secure, 'verify_package'), patch.object(secure, 'verify_installer'), patch.object(secure, '_launch_broker') as elevate:
            secure.launch_verified_archive(self.root / "a'$()b.zip", 'vac', self.root)
        encoded = elevate.call_args.args[0].split()[-1]
        script = base64.b64decode(encoded).decode('utf-16le')
        self.assertIn(secure.PACKAGES['vac']['sha256'], base64.b64decode(script.split("FromBase64String('")[1].split("')")[0]).decode())
        self.assertLess(script.index('$inputFile.CopyTo'), script.index('Get-FileHash'))
        self.assertLess(script.index('Get-FileHash'), script.index('ExtractToDirectory'))
        self.assertLess(script.index('Get-AuthenticodeSignature'), script.index('$proc = Start-Process'))

    def test_broker_powershell_syntax(self):
        command = secure.broker_command(self.root / 'archive.zip', '0' * 64, secure.VB_CERT)
        script = base64.b64decode(command.split()[-1]).decode('utf-16le')
        path = self.root / 'parse-only.ps1'
        path.write_text(script, encoding='utf-8-sig')
        parse = "$tokens=$null;$errs=$null;[Management.Automation.Language.Parser]::ParseFile(" + secure._ps_literal(path) + ",[ref]$tokens,[ref]$errs)|Out-Null;if($errs.Count){$errs|Out-String|Write-Output;exit 1}"
        result = subprocess.run([secure.system_executable('powershell'), '-NoProfile', '-NonInteractive', '-Command', parse], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_each_flags_button_opens_its_own_url(self):
        chrome = self.root / 'chrome.exe'
        chrome.touch()
        for kind in ('all', 'unexpire', 'webrtc', 'echo'):
            with self.subTest(kind=kind), patch.object(vm, 'navigate_chrome_omnibox', return_value=True), patch.object(vm, 'get_chrome_version', return_value=(156, str(chrome))), patch.object(vm, 'get_last_active_chrome_profile', return_value=('Profile 2', 'Test', 'now')), patch.object(vm.subprocess, 'Popen') as launch:
                self.assertTrue(vm.open_chrome_audio_flags_in_last_browser(kind)[0])
                args = launch.call_args.args[0]
                self.assertEqual(args[-1], vm.chrome_flags_url(kind))
                self.assertIn('--profile-directory=Profile 2', args)

    def test_malformed_profile_is_not_forwarded(self):
        chrome = self.root / 'chrome.exe'
        chrome.touch()
        with patch.object(vm, 'get_chrome_version', return_value=(156, str(chrome))), patch.object(vm, 'get_last_active_chrome_profile', return_value=('Default" --no-sandbox', '', '')), patch.object(vm.subprocess, 'Popen') as launch:
            self.assertFalse(vm.open_chrome_audio_flags_in_last_browser()[0])
            launch.assert_not_called()

    def test_navigation_stops_when_chrome_foreground_is_not_confirmed(self):
        chrome = self.root / 'chrome.exe'
        chrome.touch()
        with patch.object(vm, 'get_chrome_version', return_value=(156, str(chrome))), patch.object(vm, 'get_last_active_chrome_profile', return_value=('Default', 'Test', 'now')), patch.object(vm.subprocess, 'Popen'), patch.object(vm, 'navigate_chrome_omnibox', return_value=False):
            ok, message = vm.open_chrome_audio_flags_in_last_browser()
            self.assertFalse(ok)
            self.assertIn('停止自动输入', message)

    def test_arbitrary_urls_are_rejected_before_keyboard_access(self):
        from chrome_navigation import navigate_chrome_omnibox
        with self.assertRaises(ValueError):
            navigate_chrome_omnibox('chrome.exe', 'https://unexpected.example/')

    def test_last_used_record_precedes_background_mtime(self):
        (self.root / 'Local State').write_text(json.dumps({'profile': {'last_used': 'Profile 3', 'info_cache': {'Profile 3': {'name': 'Recent'}}}}))
        with patch.object(vm, 'get_chrome_user_data_path', return_value=str(self.root)):
            self.assertEqual(vm.get_last_active_chrome_profile()[:2], ('Profile 3', 'Recent'))

    def test_both_screenshot_milestones_are_reported_separately(self):
        (self.root / 'Local State').write_text(json.dumps({'browser': {'enabled_labs_experiments': ['temporary-unexpire-flags-m154@1']}}))
        with patch.object(vm, 'get_chrome_user_data_path', return_value=str(self.root)):
            status = vm.get_chrome_flags_status()
        self.assertFalse(status['unexpire_enabled'])
        self.assertIn('M154：已启用', status['unexpire_details'])
        self.assertIn('M155：未设置', status['unexpire_details'])


if __name__ == '__main__':
    unittest.main()
