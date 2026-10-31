"""Security boundaries for downloaded archives and Windows command lookup."""
import ctypes
import os
import re
import stat
import tempfile
import time
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

MAX_DOWNLOAD_BYTES = 128 * 1024 * 1024
MAX_EXTRACT_BYTES = 256 * 1024 * 1024
MAX_ARCHIVE_FILES = 4096
MAX_DOWNLOAD_SECONDS = 120


def system_executable(name):
    """Resolve OS tools without searching the application directory or PATH."""
    buf = ctypes.create_unicode_buffer(32768)
    size = ctypes.windll.kernel32.GetSystemDirectoryW(buf, len(buf))
    if not size or size >= len(buf):
        raise OSError("Cannot resolve Windows system directory")
    if name == "powershell":
        name = r"WindowsPowerShell\v1.0\powershell.exe"
    else:
        name += ".exe"
    return os.path.join(buf.value, name)


class _HTTPSRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, host):
        self.host = host

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urllib.parse.urlsplit(newurl)
        if (target.scheme != "https" or target.hostname != self.host
                or target.port not in (None, 443) or target.username is not None):
            raise ValueError("Download redirect outside the trusted HTTPS host")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download_https(url, dest_path, progress_callback=None):
    target = urllib.parse.urlsplit(url)
    if (target.scheme != "https" or not target.hostname
            or target.port not in (None, 443) or target.username is not None):
        raise ValueError("Downloads require HTTPS")
    destination = Path(dest_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
    }
    req = urllib.request.Request(url, headers=headers)
    opener = urllib.request.build_opener(_HTTPSRedirect(target.hostname))
    # Unique file, atomic publication: failures never leave a usable partial ZIP.
    temp_path = None
    started = time.monotonic()
    try:
        with opener.open(req, timeout=30) as resp:
            total = int(resp.headers.get("Content-Length", 0))
            if total < 0 or total > MAX_DOWNLOAD_BYTES:
                raise ValueError("Download exceeds size limit")
            with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as f:
                temp_path = f.name
                downloaded = 0
                while chunk := resp.read(64 * 1024):
                    if time.monotonic() - started > MAX_DOWNLOAD_SECONDS:
                        raise TimeoutError("Download exceeds time limit")
                    downloaded += len(chunk)
                    if downloaded > MAX_DOWNLOAD_BYTES:
                        raise ValueError("Download exceeds size limit")
                    f.write(chunk)
                    if progress_callback and total:
                        progress_callback(downloaded, total)
                if total and total != downloaded:
                    raise ValueError("Incomplete download")
        os.replace(temp_path, destination)
        temp_path = None
    finally:
        if temp_path is not None:
            os.unlink(temp_path)


def safe_extract_zip(archive_path, destination):
    """Validate the entire ZIP before creating anything; never overwrite files."""
    root = Path(destination).resolve()
    if root.exists():
        raise ValueError("Extraction requires a fresh directory")
    with zipfile.ZipFile(archive_path) as archive:
        members = archive.infolist()
        if len(members) > MAX_ARCHIVE_FILES:
            raise ValueError("Too many archive entries")
        total = 0
        seen = set()
        checked = []
        for member in members:
            name = member.filename.replace("\\", "/")
            parts = name.rstrip("/").split("/")
            if (not name or name.startswith("/") or "\x00" in member.orig_filename
                    or any(p in ("", ".", "..") or p.endswith((".", " "))
                           or re.search(r'[<>:"|?*\x00-\x1f]', p)
                           or re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", p)
                           for p in parts)):
                raise ValueError("Unsafe archive path")
            mode = member.external_attr >> 16
            if stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR):
                raise ValueError("Archive links and special files are forbidden")
            # Older Python decompressors can allocate beyond declared ZIP sizes.
            if member.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                raise ValueError("Unsupported archive compression")
            key = "/".join(parts).casefold()
            if key in seen:
                raise ValueError("Duplicate archive path")
            seen.add(key)
            total += member.file_size
            if total > MAX_EXTRACT_BYTES:
                raise ValueError("Archive exceeds extraction limit")
            target = root.joinpath(*parts)
            if not target.resolve().is_relative_to(root):
                raise ValueError("Archive escapes extraction directory")
            checked.append((member, target))
        root.mkdir(parents=True)
        written = 0
        for member, target in checked:
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, target.open("xb") as output:
                while chunk := source.read(64 * 1024):
                    written += len(chunk)
                    if written > MAX_EXTRACT_BYTES:
                        raise ValueError("Archive exceeds extraction limit")
                    output.write(chunk)
