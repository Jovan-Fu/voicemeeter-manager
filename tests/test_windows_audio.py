import ctypes
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from windows_audio import AudioError, PROPVARIANT, checked, matches_preset, query_system_audio, set_default_preset


class FakeAudio:
    def __init__(self):
        self.warnings = []
        self.available = {
            0: [dict(id='play-target', name='Voicemeeter Input (VB-Audio Voicemeeter VAIO)')],
            1: [dict(id='record-target', name='Voicemeeter Out B1 (VB-Audio Voicemeeter VAIO)')],
        }
        self.original = {0: dict(id='play-old', name='耳机 | USB'), 1: dict(id='record-old', name='麦克风（USB）')}
        self.defaults = {(flow, role): self.original[flow] for flow in range(2) for role in range(3)}
        self.set_calls = []
        self.record_error = False
        self.enumeration_error = False
        self.fail_once = False
        self.ignore_changes = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def default(self, flow, role=0):
        if flow == 1 and self.record_error:
            raise AudioError('读取录制设备失败（HRESULT 0x80070005）')
        return self.defaults[flow, role]

    def devices(self, flow):
        if self.enumeration_error:
            raise AudioError('secondary device enumeration failed')
        return self.available[flow]

    def set_default(self, identifier, role):
        self.set_calls.append((identifier, role))
        if self.fail_once and identifier == 'play-target' and role == 1:
            self.fail_once = False
            raise AudioError('set role 1 failed')
        if self.ignore_changes:
            return
        for flow in range(2):
            for device in self.available[flow] + [self.original[flow]]:
                if device['id'] == identifier:
                    self.defaults[flow, role] = device
                    return
        raise AssertionError('unknown test device')


class AudioTests(unittest.TestCase):
    def test_propvariant_uses_native_pointer_size(self):
        self.assertEqual(ctypes.sizeof(PROPVARIANT), 24 if ctypes.sizeof(ctypes.c_void_p) == 8 else 16)

    def test_read_preserves_unicode_and_pipe_without_helper_protocol(self):
        audio = FakeAudio()
        status = query_system_audio(lambda: audio)
        self.assertTrue(status['success'])
        self.assertEqual(status['playback_default'], '耳机 | USB')
        self.assertEqual(status['record_default'], '麦克风（USB）')
        self.assertEqual(audio.set_calls, [])

    def test_one_direction_failure_does_not_hide_other_default(self):
        audio = FakeAudio()
        audio.record_error = True
        status = query_system_audio(lambda: audio)
        self.assertFalse(status['success'])
        self.assertEqual(status['playback_default'], '耳机 | USB')
        self.assertIsNone(status['playback_error'])
        self.assertIn('0x80070005', status['record_error'])

    def test_secondary_device_error_keeps_valid_defaults(self):
        audio = FakeAudio()
        audio.enumeration_error = True
        status = query_system_audio(lambda: audio)
        self.assertTrue(status['success'])
        self.assertEqual(status['playback_default'], '耳机 | USB')
        self.assertTrue(status['warnings'])

    def test_no_default_endpoint_is_distinct_from_api_failure(self):
        audio = FakeAudio()
        audio.defaults = {key: None for key in audio.defaults}
        status = query_system_audio(lambda: audio)
        self.assertTrue(status['success'])
        self.assertEqual(status['playback_default'], '')
        self.assertEqual(status['error'], '')

    def test_com_failure_retains_specific_hresult(self):
        def unavailable():
            raise AudioError('音频服务不可用（HRESULT 0x800706BA）')
        status = query_system_audio(unavailable)
        self.assertFalse(status['success'])
        self.assertIn('0x800706BA', status['playback_error'])
        self.assertIn('0x800706BA', status['record_error'])

    def test_missing_requested_device_does_not_partially_change_playback(self):
        audio = FakeAudio()
        audio.available[1] = []
        ok, message = set_default_preset(factory=lambda: audio)
        self.assertFalse(ok)
        self.assertIn('未找到活动设备', message)
        self.assertEqual(audio.set_calls, [])

    def test_duplicate_endpoint_names_require_explicit_selection(self):
        audio = FakeAudio()
        audio.available[0].append(dict(id='duplicate', name=audio.available[0][0]['name']))
        ok, message = set_default_preset(factory=lambda: audio)
        self.assertFalse(ok)
        self.assertIn('多个同名设备', message)
        self.assertEqual(audio.set_calls, [])

    def test_all_roles_are_verified_for_both_requested_directions(self):
        audio = FakeAudio()
        self.assertTrue(set_default_preset(factory=lambda: audio)[0])
        self.assertEqual(len(audio.set_calls), 6)
        for flow in range(2):
            self.assertTrue(all(audio.defaults[flow, role] == audio.available[flow][0] for role in range(3)))

    def test_partial_set_failure_requests_rollback(self):
        audio = FakeAudio()
        audio.fail_once = True
        ok, message = set_default_preset(factory=lambda: audio)
        self.assertFalse(ok)
        self.assertIn('set role 1 failed', message)
        self.assertTrue(all(audio.defaults[flow, role] == audio.original[flow] for flow in range(2) for role in range(3)))

    def test_success_return_without_actual_system_change_is_not_accepted(self):
        audio = FakeAudio()
        audio.ignore_changes = True
        ok, message = set_default_preset(factory=lambda: audio)
        self.assertFalse(ok)
        self.assertIn('复核失败', message)

    def test_already_configured_roles_are_not_rewritten(self):
        audio = FakeAudio()
        audio.defaults = {(flow, role): audio.available[flow][0] for flow in range(2) for role in range(3)}
        self.assertTrue(set_default_preset(factory=lambda: audio)[0])
        self.assertEqual(audio.set_calls, [])

    def test_aux_and_similarly_named_devices_do_not_match_primary_bus(self):
        self.assertTrue(matches_preset('Voicemeeter Input (VB-Audio Voicemeeter VAIO)', 0))
        self.assertFalse(matches_preset('Voicemeeter AUX Input (VB-Audio)', 0))
        self.assertFalse(matches_preset('Voicemeeter Input Extra', 0))
        self.assertTrue(matches_preset('Voicemeeter Output (VB-Audio)', 1))


if __name__ == '__main__':
    unittest.main()
