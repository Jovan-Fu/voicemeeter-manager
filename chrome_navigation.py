"""Navigate a newly requested Chrome tab through its omnibox, with focus guards.

Chrome can reject chrome:// URLs passed by external applications. Never send
keys to an arbitrary foreground app or retain a delayed global keyboard worker.
"""
import ctypes
from ctypes import wintypes
import os
import time


def navigate_chrome_omnibox(chrome_exe, url, timeout=2.0):
    if url not in {
        'chrome://flags/', 'chrome://flags/#temporary-unexpire-flags-m154',
        'chrome://flags/#enable-webrtc-allow-input-volume-adjustment',
        'chrome://flags/#chrome-wide-echo-cancellation',
    }:
        raise ValueError('Only fixed Chrome audio settings URLs are allowed')
    user = ctypes.WinDLL('user32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    user.GetForegroundWindow.restype = wintypes.HWND
    user.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user.GetAsyncKeyState.argtypes = [ctypes.c_int]
    user.GetAsyncKeyState.restype = ctypes.c_short
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]

    def is_target(hwnd):
        window_class = ctypes.create_unicode_buffer(256)
        if not user.GetClassNameW(hwnd, window_class, len(window_class)) or window_class.value != 'Chrome_WidgetWin_1':
            return False
        pid = wintypes.DWORD()
        user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        handle = kernel.OpenProcess(0x1000, False, pid.value)
        if not handle:
            return False
        try:
            size = wintypes.DWORD(32768)
            image = ctypes.create_unicode_buffer(size.value)
            return bool(kernel.QueryFullProcessImageNameW(handle, 0, image, ctypes.byref(size))) and os.path.normcase(image.value) == os.path.normcase(os.path.abspath(chrome_exe))
        finally:
            kernel.CloseHandle(handle)

    target = None
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        window = user.GetForegroundWindow()
        if window and is_target(window):
            target = window
            break
        time.sleep(0.05)
    if not target:
        return False

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [('wVk', wintypes.WORD), ('wScan', wintypes.WORD),
                    ('dwFlags', wintypes.DWORD), ('time', wintypes.DWORD),
                    ('dwExtraInfo', ctypes.c_size_t)]

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [('dx', wintypes.LONG), ('dy', wintypes.LONG),
                    ('mouseData', wintypes.DWORD), ('dwFlags', wintypes.DWORD),
                    ('time', wintypes.DWORD), ('dwExtraInfo', ctypes.c_size_t)]

    class INPUTUNION(ctypes.Union):
        _fields_ = [('ki', KEYBDINPUT), ('mi', MOUSEINPUT)]

    class INPUT(ctypes.Structure):
        _anonymous_ = ('u',)
        _fields_ = [('type', wintypes.DWORD), ('u', INPUTUNION)]

    user.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
    user.SendInput.restype = wintypes.UINT

    def key(vk=0, scan=0, flags=0):
        event = INPUT()
        event.type = 1
        event.ki = KEYBDINPUT(vk, scan, flags, 0, 0)
        return event

    def send(events):
        # Check the same window and its image before every input batch. If focus
        # changes, abort; do not refocus or send input to another application.
        if (user.GetForegroundWindow() != target or not is_target(target)
                or any(user.GetAsyncKeyState(vk) & 0x8000 for vk in (0x10, 0x11, 0x12, 0x5B, 0x5C))):
            return False
        batch = (INPUT * len(events))(*events)
        return user.SendInput(len(events), batch, ctypes.sizeof(INPUT)) == len(events)

    # Give the tab created by Chrome's process handoff time to finish activation.
    time.sleep(0.35)
    if not send([key(0x11), key(0x4C), key(0x4C, flags=2), key(0x11, flags=2)]):
        return False
    time.sleep(0.1)
    text = []
    for ch in url:
        text.extend([key(scan=ord(ch), flags=4), key(scan=ord(ch), flags=6)])
    if not send(text):
        return False
    return send([key(0x0D), key(0x0D, flags=2)])
