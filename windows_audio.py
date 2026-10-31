"""Windows Core Audio COM access with no helper executable or script host.

Enumeration uses documented MMDevice/IPropertyStore interfaces. The optional
default-device setter uses the existing tool's Windows IPolicyConfig interface;
it is instantiated only for an explicit setting operation, never for detection.
"""
from contextlib import contextmanager
import ctypes
from ctypes import wintypes

from windows_shortcuts import GUID

E_NOTFOUND = 0x80070490
CHANGED_MODE = 0x80010106


class AudioError(OSError):
    pass


def checked(result, operation):
    if result < 0:
        code = result & 0xffffffff
        explanation = {
            0x80070422: '相关 Windows 服务被禁用',
            0x800706BA: 'Windows 音频/RPC 服务不可用',
            0x80040154: 'Windows 音频 COM 组件未注册或当前环境不支持',
            0x80070005: '当前进程无权访问音频组件',
        }.get(code, 'Windows 音频接口返回错误')
        raise AudioError(f'{operation}：{explanation}（HRESULT 0x{code:08X}）')


def method(pointer, index, *arguments):
    table = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    return ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *arguments)(table[index])


def release(pointer):
    if pointer:
        method(pointer, 2)(pointer)


class BLOB(ctypes.Structure):
    _fields_ = [('size', wintypes.ULONG), ('data', ctypes.c_void_p)]


class VALUE(ctypes.Union):
    _fields_ = [('pointer', ctypes.c_void_p), ('blob', BLOB), ('integer', ctypes.c_longlong)]


class PROPVARIANT(ctypes.Structure):
    _fields_ = [('vt', wintypes.WORD), ('reserved1', wintypes.WORD),
                ('reserved2', wintypes.WORD), ('reserved3', wintypes.WORD), ('value', VALUE)]


class PROPERTYKEY(ctypes.Structure):
    _fields_ = [('fmtid', GUID), ('pid', wintypes.DWORD)]


@contextmanager
def com_session():
    ole = ctypes.WinDLL('ole32')
    ole.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    ole.CoInitializeEx.restype = ctypes.c_long
    ole.CoCreateInstance.argtypes = [ctypes.POINTER(GUID), ctypes.c_void_p, wintypes.DWORD,
                                    ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)]
    ole.CoCreateInstance.restype = ctypes.c_long
    ole.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    ole.CoTaskMemFree.restype = None
    ole.PropVariantClear.argtypes = [ctypes.POINTER(PROPVARIANT)]
    ole.PropVariantClear.restype = ctypes.c_long
    initialized = ole.CoInitializeEx(None, 2)
    if initialized & 0xffffffff != CHANGED_MODE:
        checked(initialized, '初始化 COM')
    try:
        yield ole
    finally:
        if initialized in (0, 1):
            ole.CoUninitialize()


class CoreAudio:
    def __enter__(self):
        self.session = com_session()
        self.ole = self.session.__enter__()
        self.enumerator, self.policy = ctypes.c_void_p(), ctypes.c_void_p()
        self.warnings = []
        try:
            self.enumerator = self.create('bcde0395-e52f-467c-8e3d-c4579291692e',
                                          'a95664d2-9614-4f35-a746-de8db63617e6', '创建音频设备枚举器')
            return self
        except BaseException:
            self.session.__exit__(None, None, None)
            raise

    def __exit__(self, kind, value, trace):
        release(self.policy)
        release(self.enumerator)
        self.session.__exit__(kind, value, trace)

    def create(self, class_id, interface_id, operation):
        pointer = ctypes.c_void_p()
        clsid, iid = GUID.parse(class_id), GUID.parse(interface_id)
        checked(self.ole.CoCreateInstance(ctypes.byref(clsid), None, 1, ctypes.byref(iid), ctypes.byref(pointer)), operation)
        return pointer

    def device_id(self, device):
        allocated = ctypes.c_void_p()
        checked(method(device, 5, ctypes.POINTER(ctypes.c_void_p))(device, ctypes.byref(allocated)), '读取设备 ID')
        try:
            return ctypes.wstring_at(allocated) if allocated else ''
        finally:
            self.ole.CoTaskMemFree(allocated)

    def device_info(self, device):
        identifier = self.device_id(device)
        store = ctypes.c_void_p()
        name = ''
        try:
            checked(method(device, 4, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p))(device, 0, ctypes.byref(store)), '读取设备属性')
            key = PROPERTYKEY(GUID.parse('a45c254e-df1c-4efd-8020-67d146a850e0'), 14)
            value = PROPVARIANT()
            try:
                checked(method(store, 5, ctypes.POINTER(PROPERTYKEY), ctypes.POINTER(PROPVARIANT))(store, ctypes.byref(key), ctypes.byref(value)), '读取设备名称')
                if value.vt == 31 and value.value.pointer:
                    name = ctypes.wstring_at(value.value.pointer)
            finally:
                self.ole.PropVariantClear(ctypes.byref(value))
        except AudioError as exc:
            self.warnings.append(str(exc))
        finally:
            release(store)
        return {'id': identifier, 'name': name or '设备名称不可读', 'name_available': bool(name)}

    def devices(self, flow):
        collection = ctypes.c_void_p()
        checked(method(self.enumerator, 3, ctypes.c_int, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p))(
            self.enumerator, flow, 1, ctypes.byref(collection)), '枚举活动音频设备')
        devices = []
        try:
            count = wintypes.UINT()
            checked(method(collection, 3, ctypes.POINTER(wintypes.UINT))(collection, ctypes.byref(count)), '读取设备数量')
            for index in range(count.value):
                device = ctypes.c_void_p()
                try:
                    checked(method(collection, 4, wintypes.UINT, ctypes.POINTER(ctypes.c_void_p))(collection, index, ctypes.byref(device)), '读取音频设备')
                    devices.append(self.device_info(device))
                except AudioError as exc:
                    self.warnings.append(str(exc))
                finally:
                    release(device)
        finally:
            release(collection)
        return devices

    def default(self, flow, role=0):
        device = ctypes.c_void_p()
        result = method(self.enumerator, 4, ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p))(
            self.enumerator, flow, role, ctypes.byref(device))
        try:
            if result & 0xffffffff == E_NOTFOUND:
                return None
            checked(result, '读取默认播放设备' if flow == 0 else '读取默认录制设备')
            return self.device_info(device)
        finally:
            release(device)

    def set_default(self, identifier, role):
        if not self.policy:
            self.policy = self.create('870af99c-171d-4f9e-af0d-e63df40c2bc9',
                                      'f8679f50-850a-41cf-9c72-430f290290c8', '创建默认设备设置接口')
        checked(method(self.policy, 13, wintypes.LPCWSTR, ctypes.c_int)(self.policy, identifier, role),
                f'设置默认设备（角色 {role}）')


def query_system_audio(factory=None):
    result = {'success': False, 'playback_default': '', 'record_default': '',
              'playback_devices': [], 'record_devices': [],
              'playback_error': None, 'record_error': None, 'warnings': []}
    try:
        with (factory or CoreAudio)() as audio:
            for flow, key in ((0, 'playback'), (1, 'record')):
                try:
                    # Read the default independently; a damaged secondary device
                    # must not hide the actual system default.
                    default = audio.default(flow)
                    result[key + '_default'] = default['name'] if default else ''
                    try:
                        result[key + '_devices'] = [d['name'] for d in audio.devices(flow)]
                    except AudioError as exc:
                        result['warnings'].append(str(exc))
                except (OSError, ValueError) as exc:
                    result[key + '_error'] = str(exc)
            result['warnings'].extend(audio.warnings)
    except (OSError, ValueError) as exc:
        result['playback_error'] = result['record_error'] = str(exc)
    result['success'] = not (result['playback_error'] or result['record_error'])
    result['error'] = '; '.join(dict.fromkeys(e for e in (result['playback_error'], result['record_error']) if e))
    return result


def matches_preset(name, flow):
    candidates = ('voicemeeter input',) if flow == 0 else ('voicemeeter out b1', 'voicemeeter output')
    value = name.strip().casefold()
    return any(value == prefix or value.startswith(prefix + ' (') for prefix in candidates)


def select_device(devices, flow):
    # Prefer modern B1 naming; fall back to the older Output name only if absent.
    choices = [d for d in devices if matches_preset(d['name'], flow)]
    if flow == 1:
        modern = [d for d in choices if d['name'].strip().casefold().startswith('voicemeeter out b1')]
        choices = modern or choices
    if len(choices) != 1:
        label = 'Voicemeeter Input' if flow == 0 else 'Voicemeeter Out B1 / Output'
        raise AudioError(f'{label}：' + ('未找到活动设备，请检查驱动是否安装、设备是否被禁用' if not choices else '找到多个同名设备，请在系统声音面板中手动选择'))
    return choices[0]


def set_default_preset(playback=True, record=True, factory=None):
    if not playback and not record:
        return False, '未选择要设置的播放或录制设备。'
    touched = []
    try:
        with (factory or CoreAudio)() as audio:
            plans = []
            # Validate every requested endpoint before making any system change.
            for flow, enabled in ((0, playback), (1, record)):
                if enabled:
                    target = select_device(audio.devices(flow), flow)
                    originals = {role: audio.default(flow, role) for role in range(3)}
                    plans.append((flow, target, originals))
            try:
                for flow, target, originals in plans:
                    for role in range(3):
                        if originals[role] and originals[role]['id'] == target['id']:
                            continue
                        touched.append((flow, role, originals[role]))
                        audio.set_default(target['id'], role)
                    for role in range(3):
                        actual = audio.default(flow, role)
                        if not actual or actual['id'] != target['id']:
                            raise AudioError(f'设置后复核失败：{target["name"]}（角色 {role}）')
            except (OSError, ValueError) as exc:
                rollback_errors = []
                for _, role, previous in reversed(touched):
                    if not previous:
                        rollback_errors.append('该角色原先没有默认设备，无法自动还原')
                        continue
                    try:
                        audio.set_default(previous['id'], role)
                    except (OSError, ValueError) as rollback:
                        rollback_errors.append(str(rollback))
                return False, str(exc) + ('；部分设置可能仍有变化，请在系统声音面板检查：' + '; '.join(rollback_errors) if rollback_errors else '；已请求还原原默认设备。')
            return True, '已设置并复核全部选中设备的普通、多媒体和通信默认角色：\n' + '\n'.join(p[1]['name'] for p in plans)
    except (OSError, ValueError) as exc:
        return False, str(exc)
