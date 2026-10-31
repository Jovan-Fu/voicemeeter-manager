"""Reviewed vendor payloads; only the protected elevated copy may be executed."""
import base64
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess

from security_utils import system_executable

VB_CERT = "A77952D93229D0EC36E2543081EEA7D125732B9C"
VAC_CERT = "618593B1A98D662C1FCF099FB7A4D51AE0F25D2C"
PACKAGES = {
    "vbcable": {
        "url": "https://download.vb-audio.com/Download_CABLE/VBCABLE_Driver_Pack45.zip",
        "sha256": "b950e39f01af1d04ea623c8f6d8eb9b6ea5c477c637295fabf20631c85116bfb",
        "certificate": VB_CERT,
        "x64": "VBCABLE_Setup_x64.exe", "x86": "VBCABLE_Setup.exe",
    },
    "vac": {
        "url": "https://software.muzychenko.net/freeware/vac471lite.zip",
        "sha256": "1ed42a7e763bf6237438fb62191ae88b2922f014b37658b29d7801d72c9d5357",
        "certificate": VAC_CERT,
        "x64": "setup64.exe", "x86": "setup.exe", "arm64": "setup64a.exe",
    },
}


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_package(path, package_id):
    if sha256_file(path) != PACKAGES[package_id]["sha256"]:
        raise ValueError("安装包 SHA-256 与已审核官网版本不符，已停止安装。请勿关闭杀毒软件。")


def installer_name(package_id):
    arch = os.environ.get("PROCESSOR_ARCHITEW6432", platform.machine()).lower()
    key = "arm64" if arch in ("arm64", "aarch64") else "x64" if "64" in arch else "x86"
    name = PACKAGES[package_id].get(key)
    if not name:
        raise ValueError("此驱动包没有经过验证的当前架构安装入口，请从官网确认兼容版本。")
    return name


def _ps_literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def verify_installer(path, certificate):
    """Fail closed: offline/revocation/AV errors are not accepted as a valid signer."""
    script = (
        "$ErrorActionPreference='Stop'; $env:PSModulePath=Join-Path $PSHOME 'Modules'; "
        f"$s=Get-AuthenticodeSignature -LiteralPath {_ps_literal(Path(path).resolve())}; "
        f"if ($s.Status -ne 'Valid' -or $s.SignerCertificate.Thumbprint -ne '{certificate}') "
        "{throw '安装器签名无效或发布者证书不匹配，已停止安装。'}"
    )
    result = subprocess.run(
        [system_executable("powershell"), "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW, timeout=90,
    )
    if result.returncode:
        raise ValueError("安装器签名无效、证书验证失败或发布者不匹配，已停止安装；请从官网核查。")


# This script is embedded in the trusted build, never loaded from the writable cache.
# The elevated process copies first, hashes the protected bytes, then checks the
# signature in that protected directory. It never executes from user-writable paths.
BROKER_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$env:PSModulePath = Join-Path $PSHOME 'Modules'
$stage = $null
$brokerExit = 0
try {
    $cfg = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('__CONFIG__')) | ConvertFrom-Json
    $admin = [Security.Principal.SecurityIdentifier]::new('S-1-5-32-544')
    $system = [Security.Principal.SecurityIdentifier]::new('S-1-5-18')
    $acl = [Security.AccessControl.DirectorySecurity]::new()
    $acl.SetAccessRuleProtection($true, $false)
    $acl.SetOwner($admin)
    foreach ($sid in @($admin, $system)) {
        $rule = [Security.AccessControl.FileSystemAccessRule]::new($sid, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow')
        $acl.AddAccessRule($rule)
    }
    $parent = [Environment]::GetFolderPath('ProgramFiles')
    if (-not [IO.Path]::IsPathRooted($parent)) { throw 'Cannot resolve protected installation directory' }
    $stage = [IO.Path]::Combine($parent, ('StereoAudioInstaller-' + [Guid]::NewGuid().ToString('N')))
    if ([IO.Directory]::Exists($stage) -or [IO.File]::Exists($stage)) { throw 'Staging collision' }
    [IO.Directory]::CreateDirectory($stage, $acl) | Out-Null
    if (((Get-Acl -LiteralPath $stage).Owner -ne $admin.Translate([Security.Principal.NTAccount]).Value)) {
        throw 'Unexpected staging owner'
    }
    $payload = [IO.Path]::Combine($stage, 'payload.bin')
    # Copy does not execute, and the trusted digest is verified AFTER copying.
    $inputFile = [IO.File]::Open($cfg.source, 'Open', 'Read', 'Read')
    try {
        if ($inputFile.Length -gt 134217728) { throw 'Payload exceeds size limit' }
        $outputFile = [IO.File]::Open($payload, 'CreateNew', 'Write', 'None')
        try { $inputFile.CopyTo($outputFile) } finally { $outputFile.Dispose() }
    } finally { $inputFile.Dispose() }
    if ((Get-FileHash -LiteralPath $payload -Algorithm SHA256).Hash -ne $cfg.sha256) {
        throw 'Payload changed or failed SHA-256 verification. Installation blocked.'
    }
    if ($cfg.archive) {
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        $extracted = [IO.Path]::Combine($stage, 'extracted')
        [IO.Compression.ZipFile]::ExtractToDirectory($payload, $extracted)
        $exe = [IO.Path]::Combine($extracted, $cfg.installer)
    } else {
        $exe = [IO.Path]::Combine($stage, 'VoicemeeterSetup.exe')
        [IO.File]::Move($payload, $exe)
    }
    $signature = Get-AuthenticodeSignature -LiteralPath $exe
    if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Thumbprint -ne $cfg.certificate) {
        throw 'Installer signature or expected publisher certificate verification failed.'
    }
    # Run the visible vendor wizard; never use silent install switches.
    $proc = Start-Process -FilePath $exe -WorkingDirectory ([IO.Path]::GetDirectoryName($exe)) -PassThru -Wait
    if ($proc.ExitCode -notin @(0, 3010, 1641)) { throw ('Installer returned code ' + $proc.ExitCode) }
} catch {
    $brokerExit = 1
    Add-Type -AssemblyName System.Windows.Forms
    [Windows.Forms.MessageBox]::Show($_.Exception.Message, 'Stereo Audio - Installation blocked') | Out-Null
} finally {
    if ($stage) {
        $resolved = [IO.Path]::GetFullPath($stage)
        $trustedParent = [IO.Path]::GetFullPath([Environment]::GetFolderPath('ProgramFiles')).TrimEnd('\') + '\'
        if ($resolved.StartsWith($trustedParent, [StringComparison]::OrdinalIgnoreCase) -and
            [IO.Path]::GetFileName($resolved) -match '^StereoAudioInstaller-[a-f0-9]{32}$') {
            Remove-Item -LiteralPath $resolved -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}
exit $brokerExit
"""


def broker_command(source, sha256, certificate, installer="", archive=True):
    config = {"source": str(Path(source).resolve()), "sha256": sha256,
              "certificate": certificate, "installer": installer, "archive": archive}
    encoded_config = base64.b64encode(json.dumps(config).encode("utf-8")).decode("ascii")
    script = BROKER_SCRIPT.replace("__CONFIG__", encoded_config)
    encoded_script = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    return "-NoProfile -NonInteractive -WindowStyle Hidden -EncodedCommand " + encoded_script


def launch_verified_archive(archive, package_id, extracted):
    package = PACKAGES[package_id]
    verify_package(archive, package_id)
    name = installer_name(package_id)
    verify_installer(Path(extracted) / name, package["certificate"])
    _launch_broker(broker_command(archive, package["sha256"], package["certificate"], name))


def launch_verified_local(installer):
    path = Path(installer).resolve(strict=True)
    if path.suffix.lower() != ".exe" or path.stat().st_size > 128 * 1024 * 1024:
        raise ValueError("请选择官网 VoiceMeeter EXE 安装包（不超过 128 MiB）。")
    digest = sha256_file(path)
    verify_installer(path, VB_CERT)
    _launch_broker(broker_command(path, digest, VB_CERT, archive=False))


class BrokerStillRunning(OSError):
    """Keep source cache when completion cannot be established."""


class SHELLEXECUTEINFO(ctypes.Structure):
    _fields_ = [('cbSize', wintypes.DWORD), ('fMask', wintypes.ULONG),
                ('hwnd', wintypes.HWND), ('lpVerb', wintypes.LPCWSTR),
                ('lpFile', wintypes.LPCWSTR), ('lpParameters', wintypes.LPCWSTR),
                ('lpDirectory', wintypes.LPCWSTR), ('nShow', ctypes.c_int),
                ('hInstApp', wintypes.HINSTANCE), ('lpIDList', ctypes.c_void_p),
                ('lpClass', wintypes.LPCWSTR), ('hkeyClass', wintypes.HKEY),
                ('dwHotKey', wintypes.DWORD), ('hIcon', wintypes.HANDLE),
                ('hProcess', wintypes.HANDLE)]


def _launch_broker(parameters):
    shell = ctypes.WinDLL('shell32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    shell.ShellExecuteExW.argtypes = [ctypes.POINTER(SHELLEXECUTEINFO)]
    shell.ShellExecuteExW.restype = wintypes.BOOL
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.GetExitCodeProcess.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    request = SHELLEXECUTEINFO()
    request.cbSize = ctypes.sizeof(request)
    request.fMask = 0x40  # SEE_MASK_NOCLOSEPROCESS: wait for the verifier/wizard.
    request.lpVerb = 'runas'
    request.lpFile = system_executable('powershell')
    request.lpParameters = parameters
    request.nShow = 0
    if not shell.ShellExecuteExW(ctypes.byref(request)):
        raise OSError("管理员授权已取消或安全安装验证进程无法启动。")
    if not request.hProcess:
        raise BrokerStillRunning('无法取得安装进程句柄；缓存已保留，请以官方安装向导为准。')
    try:
        if kernel.WaitForSingleObject(request.hProcess, 15 * 60 * 1000) != 0:
            raise BrokerStillRunning('安装向导尚未完成或无法确认退出；缓存已保留，请完成向导后重试。')
        code = wintypes.DWORD()
        if not kernel.GetExitCodeProcess(request.hProcess, ctypes.byref(code)):
            raise BrokerStillRunning('无法读取安装结果；缓存已保留。')
        if code.value:
            raise OSError('安全验证或官方安装向导失败，请查看安装提示。')
    finally:
        kernel.CloseHandle(request.hProcess)
