"""Terminate only installed Banana processes in the current Windows session."""
import ctypes
from ctypes import wintypes
import os


class PROCESSENTRY32(ctypes.Structure):
    _fields_ = [('dwSize', wintypes.DWORD), ('cntUsage', wintypes.DWORD),
                ('th32ProcessID', wintypes.DWORD), ('th32DefaultHeapID', ctypes.c_size_t),
                ('th32ModuleID', wintypes.DWORD), ('cntThreads', wintypes.DWORD),
                ('th32ParentProcessID', wintypes.DWORD), ('pcPriClassBase', wintypes.LONG),
                ('dwFlags', wintypes.DWORD), ('szExeFile', wintypes.WCHAR * 260)]


def kernel():
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    api.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    for name in ('Process32FirstW', 'Process32NextW'):
        fn = getattr(api, name)
        fn.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32)]
        fn.restype = wintypes.BOOL
    api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    api.OpenProcess.restype = wintypes.HANDLE
    api.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    api.QueryFullProcessImageNameW.restype = wintypes.BOOL
    api.ProcessIdToSessionId.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    api.ProcessIdToSessionId.restype = wintypes.BOOL
    api.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    api.TerminateProcess.restype = wintypes.BOOL
    api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    api.WaitForSingleObject.restype = wintypes.DWORD
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    return api


def normalized(path):
    return os.path.normcase(os.path.abspath(path))


def candidate_pids(api):
    current_session = wintypes.DWORD()
    if not api.ProcessIdToSessionId(os.getpid(), ctypes.byref(current_session)):
        raise ctypes.WinError(ctypes.get_last_error())
    snapshot = api.CreateToolhelp32Snapshot(2, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        entry = PROCESSENTRY32()
        entry.dwSize = ctypes.sizeof(entry)
        found = api.Process32FirstW(snapshot, ctypes.byref(entry))
        while found:
            if entry.szExeFile.casefold() in ('voicemeeterpro.exe', 'voicemeeterpro_x64.exe'):
                session = wintypes.DWORD()
                if not api.ProcessIdToSessionId(entry.th32ProcessID, ctypes.byref(session)):
                    error = ctypes.get_last_error()
                    if error != 87:  # process exited during enumeration
                        raise ctypes.WinError(error)
                elif session.value == current_session.value:
                    yield entry.th32ProcessID
            found = api.Process32NextW(snapshot, ctypes.byref(entry))
        if ctypes.get_last_error() not in (0, 18):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        api.CloseHandle(snapshot)


def image_path(api, handle):
    buf = ctypes.create_unicode_buffer(32768)
    size = wintypes.DWORD(len(buf))
    if not api.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
        raise ctypes.WinError(ctypes.get_last_error())
    return normalized(buf.value)


def running_pids(paths):
    api = kernel()
    expected = {normalized(path) for path in paths}
    result = []
    for pid in candidate_pids(api):
        handle = api.OpenProcess(0x1000, False, pid)
        if not handle:
            error = ctypes.get_last_error()
            if error == 87:
                continue
            raise ctypes.WinError(error)
        try:
            if image_path(api, handle) in expected:
                result.append(pid)
        finally:
            api.CloseHandle(handle)
    return result


def force_stop(paths):
    api = kernel()
    expected = {normalized(path) for path in paths}
    stopped = []
    for pid in candidate_pids(api):
        # Verify and terminate through the same handle, preventing PID reuse
        # between identity checking and termination. Never kill child processes.
        handle = api.OpenProcess(0x1000 | 0x100000 | 1, False, pid)
        if not handle:
            error = ctypes.get_last_error()
            if error == 87:
                continue
            raise OSError(f'无法结束 VoiceMeeter（PID {pid}，Windows 错误 {error}）。请确认管理工具和 VoiceMeeter 的权限级别一致。')
        try:
            if image_path(api, handle) not in expected:
                continue
            if api.WaitForSingleObject(handle, 0) == 0:
                continue
            if not api.TerminateProcess(handle, 1):
                raise ctypes.WinError(ctypes.get_last_error())
            if api.WaitForSingleObject(handle, 5000) != 0:
                raise OSError(f'强制结束 VoiceMeeter 后仍未退出（PID {pid}）')
            stopped.append(pid)
        finally:
            api.CloseHandle(handle)
    return stopped
