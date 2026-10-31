"""A single logged-in vendor Remote API client with load-and-readback checks."""
import atexit
import ctypes
import math
from contextlib import contextmanager
from pathlib import Path
import re
import threading
import time
from xml_config import attributes

_lock = threading.RLock()
_dll = None
_dll_path = None


def close():
    global _dll, _dll_path
    with _lock:
        if _dll is not None:
            try:
                _dll.VBVMR_Logout()
            finally:
                _dll = _dll_path = None


atexit.register(close)


@contextmanager
def session(dll_path):
    global _dll, _dll_path
    if not dll_path:
        raise OSError('未找到安装目录中的 VoiceMeeter Remote DLL。')
    with _lock:
        if _dll is None or _dll_path != str(dll_path):
            close()
            candidate = ctypes.WinDLL(str(dll_path))
            candidate.VBVMR_Login.argtypes = []
            candidate.VBVMR_Login.restype = ctypes.c_long
            candidate.VBVMR_Logout.argtypes = []
            candidate.VBVMR_Logout.restype = ctypes.c_long
            candidate.VBVMR_IsParametersDirty.argtypes = []
            candidate.VBVMR_IsParametersDirty.restype = ctypes.c_long
            candidate.VBVMR_SetParameterFloat.argtypes = [ctypes.c_char_p, ctypes.c_float]
            candidate.VBVMR_SetParameterFloat.restype = ctypes.c_long
            candidate.VBVMR_SetParameterStringW.argtypes = [ctypes.c_char_p, ctypes.c_wchar_p]
            candidate.VBVMR_SetParameterStringW.restype = ctypes.c_long
            candidate.VBVMR_GetParameterFloat.argtypes = [ctypes.c_char_p, ctypes.POINTER(ctypes.c_float)]
            candidate.VBVMR_GetParameterFloat.restype = ctypes.c_long
            result = candidate.VBVMR_Login()
            if result < 0:
                raise OSError(f'连接 VoiceMeeter Remote API 失败（{result}）')
            _dll, _dll_path = candidate, str(dll_path)
        yield _dll


def request_shutdown(dll_path):
    with session(dll_path) as dll:
        result = dll.VBVMR_SetParameterFloat(b'Command.Shutdown', 1.0)
        if result != 0:
            raise OSError(f'VoiceMeeter 退出命令失败（{result}）')


def request_engine_restart(dll_path):
    with session(dll_path) as dll:
        result = dll.VBVMR_SetParameterFloat(b'Command.Restart', 1.0)
        if result != 0:
            raise OSError(f'VoiceMeeter 引擎重启命令失败（{result}）')


def expected_parameters(content):
    mappings = {
        'Strip': {'mute': 'Mute', 'solo': 'Solo', 'mono': 'Mono', 'dblevel': 'Gain',
                  'busa': 'A1', 'busa2': 'A2', 'busa3': 'A3', 'busb': 'B1', 'busb2': 'B2'},
        'Bus': {'mute': 'Mute', 'mono': 'Mono', 'dblevel': 'Gain'},
    }
    expected = {}
    # Main Bus elements precede the separate EQ section, whose cells also use
    # the Bus tag but do not describe bus master gain.
    main = re.split(r'<VoiceMeeterBUSEQ\b', content, maxsplit=1)[0]
    for match in re.finditer(r'<(Strip|Bus)\b([^<>]*)/?>', main):
        values = attributes(match.group(2))
        try:
            index = int(values.get('index', '0')) - 1
        except ValueError:
            continue
        if not 0 <= index < 5:
            continue
        for xml_key, parameter in mappings[match.group(1)].items():
            if xml_key in values:
                value = float(values[xml_key])
                if not math.isfinite(value):
                    raise ValueError('配置含无效的非有限数值：' + xml_key)
                expected[f'{match.group(1)}[{index}].{parameter}'] = value
    return expected


def read_parameters(dll, expected):
    result = dll.VBVMR_IsParametersDirty()
    if result < 0:
        raise OSError(f'VoiceMeeter 音频引擎尚未连接（{result}）')
    differences = []
    for name, target in expected.items():
        current = ctypes.c_float()
        code = dll.VBVMR_GetParameterFloat(name.encode('ascii'), ctypes.byref(current))
        if code != 0:
            raise OSError(f'无法读取 {name}（{code}）')
        if not math.isfinite(current.value):
            raise OSError(f'读取 {name} 得到无效数值')
        if abs(current.value - target) > 0.02:
            differences.append(f'{name}: 当前 {current.value:.2f}，配置 {target:.2f}')
    return differences


def load_and_verify(dll_path, xml_path, timeout=12.0):
    path = Path(xml_path).resolve(strict=True)
    expected = expected_parameters(path.read_text(encoding='utf-8-sig'))
    if not expected:
        return False, '选中 XML 没有可核对的通道/总线参数，不能宣称载入成功。'
    deadline = time.monotonic() + timeout
    last_error = '等待 VoiceMeeter 音频引擎连接'
    with session(dll_path) as dll:
        while time.monotonic() < deadline:
            if dll.VBVMR_IsParametersDirty() >= 0:
                break
            time.sleep(0.15)
        else:
            return False, last_error
        last_send = float('-inf')
        while time.monotonic() < deadline:
            now = time.monotonic()
            if now - last_send >= 1.25:
                # A newly restarted engine can reconnect after the first Load
                # was queued. Reissue until readback confirms the requested mix.
                code = dll.VBVMR_SetParameterStringW(b'Command.Load', str(path))
                if code != 0:
                    return False, f'VoiceMeeter 未接受配置载入命令（{code}）'
                last_send = now
                time.sleep(0.5)
            try:
                differences = read_parameters(dll, expected)
                if not differences:
                    return True, f'已通过 VoiceMeeter 官方接口载入，并核对 {len(expected)} 项静音、独奏、增益及路由等核心参数。'
                last_error = '载入后参数仍不一致：' + '; '.join(differences[:6])
            except OSError as exc:
                last_error = str(exc)
            time.sleep(0.15)
    return False, last_error
