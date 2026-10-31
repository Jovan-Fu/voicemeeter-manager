"""Windows Shell Link COM access, without launching scripting hosts."""
from contextlib import contextmanager
import ctypes
from ctypes import wintypes
from pathlib import Path
import uuid


class GUID(ctypes.Structure):
    _fields_ = [('data', ctypes.c_byte * 16)]

    @classmethod
    def parse(cls, value):
        return cls.from_buffer_copy(uuid.UUID(value).bytes_le)


def _method(pointer, index, *args):
    table = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    return ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *args)(table[index])


def _check(result):
    if result < 0:
        raise OSError(f'Windows Shell Link COM error: 0x{result & 0xffffffff:08x}')


@contextmanager
def _link(path=None):
    ole = ctypes.WinDLL('ole32')
    ole.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    ole.CoInitializeEx.restype = ctypes.c_long
    ole.CoCreateInstance.argtypes = [ctypes.POINTER(GUID), ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)]
    ole.CoCreateInstance.restype = ctypes.c_long
    result = ole.CoInitializeEx(None, 2)
    # RPC_E_CHANGED_MODE means the calling thread already initialized COM.
    if result not in (0, 1, -2147417850):
        _check(result)
    shell = ctypes.c_void_p()
    persist = ctypes.c_void_p()
    try:
        clsid = GUID.parse('00021401-0000-0000-c000-000000000046')
        iid = GUID.parse('000214f9-0000-0000-c000-000000000046')
        _check(ole.CoCreateInstance(ctypes.byref(clsid), None, 1, ctypes.byref(iid), ctypes.byref(shell)))
        persist_iid = GUID.parse('0000010b-0000-0000-c000-000000000046')
        _check(_method(shell, 0, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p))(shell, ctypes.byref(persist_iid), ctypes.byref(persist)))
        if path is not None:
            _check(_method(persist, 5, wintypes.LPCWSTR, wintypes.DWORD)(persist, str(Path(path).resolve()), 0))
        yield shell, persist
    finally:
        for ptr in (persist, shell):
            if ptr:
                _method(ptr, 2)(ptr)
        if result in (0, 1):
            ole.CoUninitialize()


def read_shortcut(path):
    with _link(path) as (shell, _):
        target = ctypes.create_unicode_buffer(32768)
        args = ctypes.create_unicode_buffer(32768)
        _check(_method(shell, 3, wintypes.LPWSTR, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)(shell, target, len(target), None, 4))
        _check(_method(shell, 10, wintypes.LPWSTR, ctypes.c_int)(shell, args, len(args)))
        return {'target': target.value, 'arguments': args.value}


def write_shortcut(path, target, arguments, update=False):
    """Updating an existing link preserves its other Shell Link properties."""
    with _link(path if update else None) as (shell, persist):
        _check(_method(shell, 20, wintypes.LPCWSTR)(shell, str(target)))
        _check(_method(shell, 11, wintypes.LPCWSTR)(shell, str(arguments)))
        _check(_method(shell, 9, wintypes.LPCWSTR)(shell, str(Path(target).parent)))
        _check(_method(shell, 15, ctypes.c_int)(shell, 7))  # SW_SHOWMINNOACTIVE
        _check(_method(persist, 6, wintypes.LPCWSTR, wintypes.BOOL)(persist, str(Path(path).resolve()), True))
