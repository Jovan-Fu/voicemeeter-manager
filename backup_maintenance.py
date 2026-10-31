"""Explicit, reviewable cleanup of old regular Chrome backups only."""
import hashlib
from pathlib import Path
import time


def regular_backup(path):
    return (path.name.startswith(('Local State.bak_', 'Local State.pre_restore_'))
            and not path.name.startswith('Local State.bak_conflict_')
            and path.is_file() and not path.is_symlink() and not path.is_junction())


def cleanup_plan(directory, now=None):
    root = Path(directory).resolve()
    if not root.is_dir():
        return []
    backups = sorted((p for p in root.iterdir() if regular_backup(p)),
                     key=lambda p: p.stat().st_mtime_ns, reverse=True)
    cutoff = (time.time() if now is None else now) - 30 * 86400
    plan = []
    # Always retain the newest ten; conflict recovery files are never candidates.
    for path in backups[10:]:
        stat = path.stat()
        if stat.st_mtime < cutoff:
            plan.append({'path': str(path), 'size': stat.st_size,
                         'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    return plan


def apply_cleanup(directory, reviewed):
    root = Path(directory).resolve()
    deleted = 0
    for entry in reviewed:
        path = Path(entry['path'])
        if path.parent != root or path.resolve().parent != root or not regular_backup(path):
            raise ValueError('备份位置或类型已变化，已停止清理。')
        if hashlib.sha256(path.read_bytes()).hexdigest() != entry['sha256']:
            raise ValueError('备份内容已变化，已停止清理。')
        path.unlink()
        deleted += 1
    return deleted
