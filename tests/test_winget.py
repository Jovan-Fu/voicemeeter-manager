"""Exercise installation decisions with mocked WinGet; never install drivers."""
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import vm_manager as vm


class WingetTests(unittest.TestCase):
    def run_winget(self, installed, code=0, upgrade=False, stdout='', stderr=''):
        with patch.object(vm, 'get_installed_exe', side_effect=installed), patch.object(vm.subprocess, 'run', return_value=subprocess.CompletedProcess([], code, stdout, stderr)) as command:
            result = vm.install_voicemeeter(method='winget', upgrade_existing=upgrade)
        return result, command

    def test_existing_banana_is_preserved_without_upgrade_consent(self):
        result, command = self.run_winget(['installed-banana.exe'])
        self.assertTrue(result[0], result[1])
        self.assertIn('未重复安装或升级', result[1])
        command.assert_not_called()

    def test_fresh_install_targets_banana_without_implicit_upgrade(self):
        result, command = self.run_winget([None, 'installed-banana.exe'])
        self.assertTrue(result[0], result[1])
        args = command.call_args.args[0]
        self.assertEqual(args[1], 'install')
        self.assertEqual(args[args.index('--id') + 1], 'VB-Audio.Voicemeeter.Banana')
        self.assertIn('--no-upgrade', args)
        self.assertNotIn('--include-unknown', args)
        self.assertNotIn('--ignore-security-hash', args)

    def test_explicit_upgrade_allows_unknown_version_for_only_banana(self):
        result, command = self.run_winget(['installed-banana.exe', 'installed-banana.exe'], upgrade=True)
        self.assertTrue(result[0], result[1])
        args = command.call_args.args[0]
        self.assertEqual(args[1], 'upgrade')
        self.assertEqual(args[args.index('--id') + 1], 'VB-Audio.Voicemeeter.Banana')
        self.assertIn('--include-unknown', args)
        self.assertNotIn('--all', args)
        self.assertNotIn('--force', args)
        self.assertNotIn('--ignore-security-hash', args)

    def test_not_applicable_with_existing_banana_is_not_claimed_upgraded(self):
        for code in (0x8A15002B, 0x8A15002B - 2**32):
            with self.subTest(code=code):
                result, _ = self.run_winget(['installed-banana.exe', 'installed-banana.exe'], code, upgrade=True, stdout='No applicable upgrade')
                self.assertTrue(result[0], result[1])
                self.assertIn('不表示升级或修复成功', result[1])

    def test_stale_registration_without_banana_is_not_install_success(self):
        result, _ = self.run_winget([None, None], 0x8A15002B, stdout='version unknown')
        self.assertFalse(result[0])
        self.assertIn('未找到可用的 Banana 主程序', result[1])

    def test_success_exit_without_installed_banana_is_not_accepted(self):
        result, _ = self.run_winget([None, None])
        self.assertFalse(result[0])
        self.assertIn('尚未检测到', result[1])

    def test_other_failure_retains_stdout_stderr_and_normalized_code(self):
        result, _ = self.run_winget([None], -1, stdout='output detail', stderr='error detail')
        self.assertFalse(result[0])
        self.assertIn('0xFFFFFFFF', result[1])
        self.assertIn('output detail', result[1])
        self.assertIn('error detail', result[1])


if __name__ == '__main__':
    unittest.main()
