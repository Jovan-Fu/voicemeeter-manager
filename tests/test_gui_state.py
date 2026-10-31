"""Hidden Tk widget tests: no clicks, app activation, real settings or audio changes."""
import sys
from pathlib import Path
import tkinter as tk
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import vm_gui


class GuiStateTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        with patch.object(vm_gui.VoiceMeeterManagerApp, 'refresh_all_status'):
            self.app = vm_gui.VoiceMeeterManagerApp(self.root)

    def render_startup(self, enabled, hidden, entries=None, errors=None):
        details = dict(enabled=enabled, hidden=hidden, entries=entries or [], errors=errors or [])
        data = dict(installed=False, is_running=False, autostart_details=details)
        with patch.object(vm_gui.vm_manager, 'get_system_status', return_value=data), patch.object(vm_gui.vm_manager, 'check_vbcable_installed', return_value=None), patch.object(vm_gui.vm_manager, 'check_vac_installed', return_value=None):
            self.app.refresh_vm_status()

    def test_existing_hidden_startup_disables_redundant_action(self):
        self.render_startup(True, True, [dict(enabled=True)])
        self.assertEqual(str(self.app.btn_enable_autostart['state']), 'disabled')
        self.assertIn('已设置隐藏自启动', self.app.lbl_autostart_indicator['text'])

    def test_missing_startup_enables_setup_action(self):
        self.render_startup(False, False)
        self.assertEqual(str(self.app.btn_enable_autostart['state']), 'normal')

    def test_unknown_status_does_not_prompt_to_add_duplicate(self):
        self.render_startup(None, None, errors=['access denied'])
        self.assertEqual(str(self.app.btn_enable_autostart['state']), 'disabled')
        self.assertIn('无法完整检测', self.app.lbl_autostart_indicator['text'])

    def test_windows_disabled_startup_is_not_called_unconfigured(self):
        self.render_startup(False, False, [dict(enabled=False)])
        self.assertIn('Windows 禁用', self.app.lbl_autostart_indicator['text'])

    def test_partial_audio_failure_keeps_working_playback_visible(self):
        result = dict(success=False, playback_default='Voicemeeter Input', record_default='',
                      playback_devices=['Voicemeeter Input'], record_devices=[],
                      is_playback_match=True, is_record_match=False,
                      playback_error=None, record_error='capture unavailable HRESULT 0x80070005', warnings=[])
        with patch.object(vm_gui.vm_manager, 'get_system_audio_status', return_value=result):
            self.app.refresh_audio_status()
        self.assertIn('✔ Voicemeeter Input', self.app.lbl_cur_playback['text'])
        self.assertIn('0x80070005', self.app.lbl_cur_record['text'])
        self.assertIn('0x80070005', self.app.audio_diagnostics)

    def test_missing_default_device_is_not_reported_as_helper_failure(self):
        result = dict(success=True, playback_default='', record_default='',
                      playback_devices=[], record_devices=[],
                      is_playback_match=False, is_record_match=False,
                      playback_error=None, record_error=None, warnings=[])
        with patch.object(vm_gui.vm_manager, 'get_system_audio_status', return_value=result):
            self.app.refresh_audio_status()
        self.assertIn('Windows 未设置默认活动设备', self.app.lbl_cur_playback['text'])
