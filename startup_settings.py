"""Inspect VoiceMeeter's real Windows startup registrations; no manager autostart."""
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import subprocess
import winreg

from windows_shortcuts import read_shortcut, write_shortcut

NATIVE_LINK = 'Voicemeeter (VB-Audio).LNK'
LEGACY_LINK = 'VoiceMeeter Banana AutoStart.lnk'
RUN_PATH = r'Software\Microsoft\Windows\CurrentVersion\Run'
APPROVED = r'Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved'


def command_args(command):
    shell = ctypes.WinDLL('shell32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    shell.CommandLineToArgvW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
    shell.CommandLineToArgvW.restype = ctypes.POINTER(wintypes.LPWSTR)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    count = ctypes.c_int()
    pointer = shell.CommandLineToArgvW(command, ctypes.byref(count))
    if not pointer:
        raise OSError('Cannot parse Windows command line')
    try:
        return [pointer[i] for i in range(count.value)]
    finally:
        kernel.LocalFree(pointer)


def is_hidden(arguments):
    return '-h' in [a.casefold() for a in command_args('placeholder.exe ' + arguments)]


def _known_folder(csidl):
    buf = ctypes.create_unicode_buffer(32768)
    if ctypes.windll.shell32.SHGetFolderPathW(None, csidl, None, 0, buf) != 0:
        raise OSError('Cannot resolve Windows Startup folder')
    return Path(buf.value)


def startup_folders():
    return [(winreg.HKEY_CURRENT_USER, _known_folder(7)),
            (winreg.HKEY_LOCAL_MACHINE, _known_folder(24))]


def _normalized(path):
    # Shell Link returns long paths even when Windows TEMP/Run uses an 8.3
    # alias (e.g. RUNNER~1). Resolve aliases before comparing executable paths.
    resolved = os.path.realpath(os.path.expandvars(path))
    if resolved[:8].casefold() == '\\\\?\\unc\\':
        resolved = '\\\\' + resolved[8:]
    elif resolved.startswith('\\\\?\\') and resolved[5:7] == ':\\':
        resolved = resolved[4:]
    return os.path.normcase(resolved)


def _approved(root, category, name):
    try:
        with winreg.OpenKey(root, APPROVED + '\\' + category) as key:
            value, _ = winreg.QueryValueEx(key, name)
        # Windows uses 3/7 for disabled startup entries. Preserve unknown formats.
        if isinstance(value, bytes) and value and value[0] in (3, 7):
            return False
        if isinstance(value, bytes) and value and value[0] in (2, 6):
            return True
        raise ValueError('Unrecognized Windows startup approval state: ' + name)
    except FileNotFoundError:
        return True


def inspect_startup(executable_paths):
    expected = {_normalized(path) for path in executable_paths if path}
    entries, errors = [], []
    try:
        folders = startup_folders()
    except OSError as exc:
        return {'enabled': None, 'hidden': None, 'entries': [], 'errors': [str(exc)]}
    for root, folder in folders:
        try:
            links = list(folder.glob('*.lnk')) if folder.exists() else []
            for path in links:
                try:
                    link = read_shortcut(path)
                    if _normalized(link['target']) not in expected:
                        continue
                    enabled = _approved(root, 'StartupFolder', path.name)
                    entries.append(dict(link, kind='shortcut', path=str(path), name=path.name,
                                        root=root, enabled=enabled, hidden=is_hidden(link['arguments'])))
                except (OSError, ValueError) as exc:
                    if 'voicemeeter' in path.name.casefold():
                        errors.append(f'{path.name}: {exc}')
        except OSError as exc:
            errors.append(str(exc))
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(root, RUN_PATH, 0, winreg.KEY_READ | view) as key:
                    for index in range(winreg.QueryInfoKey(key)[1]):
                        name, command, kind = winreg.EnumValue(key, index)
                        if kind not in (winreg.REG_SZ, winreg.REG_EXPAND_SZ):
                            continue
                        args = command_args(os.path.expandvars(command))
                        if not args or _normalized(args[0]) not in expected:
                            continue
                        category = 'Run32' if view == winreg.KEY_WOW64_32KEY else 'Run'
                        entry = dict(kind='registry', name=name, root=root, view=view,
                                     command=command, target=args[0], arguments=subprocess.list2cmdline(args[1:]),
                                     enabled=_approved(root, category, name), hidden='-h' in [a.casefold() for a in args[1:]])
                        if not any(e.get('command') == command and e['root'] == root and e['name'] == name for e in entries):
                            entries.append(entry)
            except FileNotFoundError:
                pass
            except (OSError, ValueError) as exc:
                errors.append(str(exc))
    active = [e for e in entries if e['enabled']]
    return {'enabled': True if active else None if errors else False,
            'hidden': all(e['hidden'] for e in active) if active else False,
            'entries': entries, 'errors': errors}


def ensure_hidden_startup(executable_paths, executable, config_xml=None):
    """Idempotent: do nothing if the existing startup registration is hidden."""
    state = inspect_startup(executable_paths)
    if state['enabled'] and state['hidden']:
        return True, 'VoiceMeeter 自身已设置隐藏自启动，未重复添加或改写。'
    if state['errors']:
        return False, '启动项检测不完整，未新增或覆盖：' + '; '.join(state['errors'])
    active = [e for e in state['entries'] if e['enabled']]
    if not active and state['entries']:
        return False, 'VoiceMeeter 启动项被 Windows 禁用。请先在 Windows 设置 → 应用 → 启动中启用；不会新增重复项绕过禁用。'
    try:
        for entry in active:
            if entry['hidden']:
                continue
            if entry['root'] != winreg.HKEY_CURRENT_USER:
                return False, '检测到所有用户的启动项未隐藏。请在 VoiceMeeter 菜单中调整；工具不会擅自请求管理员权限。'
        if active:
            for entry in active:
                if entry['hidden']:
                    continue
                arguments = (entry['arguments'] + ' -h').strip()
                if entry['kind'] == 'shortcut':
                    write_shortcut(entry['path'], entry['target'], arguments, update=True)
                else:
                    command = entry['command'] + ' -h'
                    with winreg.OpenKey(entry['root'], RUN_PATH, 0, winreg.KEY_SET_VALUE | entry['view']) as key:
                        winreg.SetValueEx(key, entry['name'], 0, winreg.REG_SZ, command)
        else:
            folder = next(folder for root, folder in startup_folders() if root == winreg.HKEY_CURRENT_USER)
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / NATIVE_LINK
            if path.exists():
                return False, '同名启动快捷方式已存在但目标不匹配，未覆盖。'
            arguments = '-h'
            if config_xml:
                arguments = f'-L"{Path(config_xml).resolve()}" -h'
            write_shortcut(path, executable, arguments)
        updated = inspect_startup(executable_paths)
        if updated['enabled'] and updated['hidden']:
            return True, 'VoiceMeeter 隐藏自启动已设置并复核；没有为本管理工具添加自启动。'
        return False, '已尝试设置，但复核未确认启用且隐藏；请检查 Windows 启动设置。'
    except (OSError, ValueError) as exc:
        return False, f'设置隐藏自启动失败：{exc}'
