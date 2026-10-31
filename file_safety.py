"""Atomic replacements and non-overwriting backups for application settings."""
import json
import os
from pathlib import Path
import shutil
import tempfile
import uuid
import ctypes
from ctypes import wintypes
import hashlib


class FileConflict(OSError):
    pass


def fingerprint(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_json_snapshot(path):
    with open(path, 'rb') as stream:
        content = stream.read(32 * 1024 * 1024 + 1)
    if len(content) > 32 * 1024 * 1024:
        raise ValueError('Chrome 配置超过 32 MiB，未写入。')
    data = json.loads(content.decode('utf-8-sig'))
    if not isinstance(data, dict):
        raise ValueError('Chrome 配置不是 JSON 对象。')
    return data, hashlib.sha256(content).hexdigest()


def replace_with_preimage(destination, replacement, backup):
    """Windows replacement captures the actual prior file, not an earlier read."""
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.ReplaceFileW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p]
    api.ReplaceFileW.restype = wintypes.BOOL
    if not api.ReplaceFileW(str(Path(destination).resolve()), str(Path(replacement).resolve()),
                            str(Path(backup).resolve()), 0, None, None):
        error = ctypes.get_last_error()
        raise OSError(f'Windows 配置替换失败（{error}）；原文件/保留副本：{backup}')


def guarded_json(path, data, expected, can_write):
    """Detect conflicts before commit; retain the actual preimage at commit.

    This is not a transaction with Chrome. A racing change is preserved and a
    compensating restore is attempted only while our own result is still current.
    All versions captured during compensation remain available for recovery.
    """
    destination = Path(path)
    content = json.dumps(data, indent=2, ensure_ascii=False).encode('utf-8')
    own_hash = hashlib.sha256(content).hexdigest()
    fd, temporary = tempfile.mkstemp(prefix=destination.name + '.', suffix='.tmp', dir=destination.parent)
    previous = destination.with_name(destination.name + '.bak_commit_' + uuid.uuid4().hex)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if not can_write():
            raise FileConflict('Chrome 已启动，已取消写入。')
        if expected is None:
            if destination.exists():
                raise FileConflict('配置文件已由其他进程创建，已取消恢复。')
            # On Windows rename fails if the destination appeared concurrently.
            try:
                os.rename(temporary, destination)
            except FileExistsError as exc:
                raise FileConflict('配置文件已由其他进程创建，未覆盖。') from exc
            return None
        if fingerprint(destination) != expected:
            raise FileConflict('Chrome 配置已被其他进程修改，已取消写入；请关闭浏览器后重试。')
        replace_with_preimage(destination, temporary, previous)
        if fingerprint(previous) != expected:
            recovery = destination.with_name(destination.name + '.bak_conflict_' + uuid.uuid4().hex)
            # Preserve the preimage before moving it back into place.
            atomic_copy(previous, recovery)
            if fingerprint(destination) == own_hash:
                displaced = destination.with_name(destination.name + '.rollback_capture_' + uuid.uuid4().hex)
                replace_with_preimage(destination, previous, displaced)
                if fingerprint(displaced) != own_hash:
                    raise FileConflict(f'检测到连续并发修改，已保留所有捕获版本；请检查 {recovery} 和 {displaced}')
                raise FileConflict(f'检测到提交瞬间的并发修改，已恢复当刻原文件；保留副本：{recovery}')
            raise FileConflict(f'检测到并发修改，当前文件又有变化，未覆盖；当刻原文件保留于：{recovery}')
        return previous
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def backup_file(path, label='bak'):
    source = Path(path)
    if not source.exists():
        return None
    backup = source.with_name(source.name + '.' + label + '_' + uuid.uuid4().hex)
    shutil.copy2(source, backup)
    return backup


def atomic_write(path, writer):
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=destination.name + '.', suffix='.tmp', dir=destination.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            writer(stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, destination)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def atomic_copy(source, destination):
    if Path(source).resolve() == Path(destination).resolve():
        return
    def copy(stream):
        with open(source, 'rb') as original:
            shutil.copyfileobj(original, stream)
    atomic_write(destination, copy)


def atomic_json(path, data):
    # Serialize before opening any destination file, then publish a complete file.
    content = json.dumps(data, indent=2, ensure_ascii=False).encode('utf-8')
    atomic_write(path, lambda stream: stream.write(content))


def replace_default_configs(source, destinations):
    snapshots = [(Path(path), backup_file(path)) for path in destinations]
    written = []
    try:
        for path, _ in snapshots:
            atomic_copy(source, path)
            written.append(path)
    except Exception as exc:
        rollback_errors = []
        for path, backup in reversed(snapshots):
            if path not in written:
                continue
            try:
                if backup:
                    atomic_copy(backup, path)
                elif path.exists():
                    path.unlink()
            except OSError as rollback:
                rollback_errors.append(str(rollback))
        if rollback_errors:
            raise OSError(f'{exc}; rollback failed: {rollback_errors}; original backups retained') from exc
        raise
