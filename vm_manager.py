# -*- coding: utf-8 -*-
"""
VoiceMeeter Banana 管理工具 - 核心逻辑模块
包含：
1. VoiceMeeter 进程与 API 控制 (启动、关闭、重启音频引擎、载入配置、开机自启)
2. 虚拟声卡驱动检测与自动下载安装 (VB-CABLE, VAC Lite)
3. 系统声音默认设备检测与一键设置 (对齐截图中的播放与录制默认设备)
4. Chrome 浏览器 Flags 自动配置 (禁用 WebRTC 自动音量调整与全局回声消除)
"""

import os
import sys
import time
import json
import shutil
import ctypes
import subprocess
import winreg
import re
import tempfile
import hashlib
import threading
from functools import wraps
from pathlib import Path
from security_utils import download_https, safe_extract_zip, system_executable
from secure_install import PACKAGES, verify_package, launch_verified_archive, launch_verified_local, BrokerStillRunning
from chrome_navigation import navigate_chrome_omnibox
from windows_shortcuts import write_shortcut
from windows_audio import query_system_audio, set_default_preset, matches_preset
import voicemeeter_remote as remote_api
from voicemeeter_process import running_pids, force_stop
from xml_config import attributes as xml_attributes, network_risks
from startup_settings import inspect_startup, ensure_hidden_startup
from file_safety import atomic_copy, atomic_json, backup_file, replace_default_configs, read_json_snapshot, guarded_json

_operation_lock = threading.RLock()

def exclusive_operation(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        if not _operation_lock.acquire(blocking=False):
            return False, "另一项配置操作正在进行，请稍后重试。"
        try:
            return function(*args, **kwargs)
        except Exception as exc:
            return False, f"操作失败，未宣称配置成功：{exc}"
        finally:
            _operation_lock.release()
    return wrapped

def voicemeeter_startup_status():
    return inspect_startup([VOICEMEETER_BANANA_EXE, VOICEMEETER_BANANA_X64_EXE])

# 常量与资源定位
def get_base_dir():
    """获取程序根目录 (兼容源码运行与 PyInstaller 打包后的 exe 运行)"""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))

def get_resource_path(relative_path):
    """内置资源优先使用随构建提供的副本。"""
    base = get_base_dir()
    if hasattr(sys, '_MEIPASS'):
        bundled = os.path.join(sys._MEIPASS, relative_path)
        if os.path.exists(bundled):
            return bundled
    p1 = os.path.join(base, relative_path)
    if os.path.exists(p1):
        return p1
    if hasattr(sys, '_MEIPASS'):
        p2 = os.path.join(sys._MEIPASS, relative_path)
        if os.path.exists(p2):
            return p2
    return p1

BASE_DIR = get_base_dir()
CACHE_DIR = os.path.join(BASE_DIR, "cache")

VOICEMEETER_DIR_X86 = r"C:\Program Files (x86)\VB\Voicemeeter"
VOICEMEETER_BANANA_EXE = os.path.join(VOICEMEETER_DIR_X86, "voicemeeterpro.exe")
VOICEMEETER_BANANA_X64_EXE = os.path.join(VOICEMEETER_DIR_X86, "voicemeeterpro_x64.exe")
VOICEMEETER_DLL_X64 = os.path.join(VOICEMEETER_DIR_X86, "VoicemeeterRemote64.dll")
VOICEMEETER_DLL_X86 = os.path.join(VOICEMEETER_DIR_X86, "VoicemeeterRemote.dll")
SETUP_EXE = os.path.join(VOICEMEETER_DIR_X86, "voicemeeterprosetup.exe")
DOCS_VM_DIR = os.path.join(os.path.expanduser("~"), "Documents", "Voicemeeter")
RUN_REG_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_REG_NAME = "VoicemeeterBanana_AutoConfig"

# 驱动下载 URL
URL_VBCABLE = PACKAGES["vbcable"]["url"]
URL_VAC_LITE = PACKAGES["vac"]["url"]


# ==============================================================================
# 1. VoiceMeeter 进程与系统状态
# ==============================================================================

def get_installed_exe():
    """获取 VoiceMeeter Banana 可执行文件路径"""
    if os.path.exists(VOICEMEETER_BANANA_X64_EXE):
        return VOICEMEETER_BANANA_X64_EXE
    elif os.path.exists(VOICEMEETER_BANANA_EXE):
        return VOICEMEETER_BANANA_EXE
    return None


def get_remote_dll():
    """获取对应的 VoicemeeterRemote DLL"""
    is_64bit_os = sys.maxsize > 2**32
    if is_64bit_os and os.path.exists(VOICEMEETER_DLL_X64):
        return VOICEMEETER_DLL_X64
    if os.path.exists(VOICEMEETER_DLL_X86):
        return VOICEMEETER_DLL_X86
    return None


def is_process_running(proc_name="voicemeeterpro"):
    if proc_name == "voicemeeterpro":
        return bool(running_pids([VOICEMEETER_BANANA_EXE, VOICEMEETER_BANANA_X64_EXE]))
    output = subprocess.check_output(
        [system_executable("tasklist"), "/FI", f"IMAGENAME eq {proc_name}*", "/FO", "CSV", "/NH"],
        text=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    import csv
    names = {row[0].casefold() for row in csv.reader(output.splitlines()) if row}
    if proc_name == "voicemeeterpro":
        return bool(names & {"voicemeeterpro.exe", "voicemeeterpro_x64.exe"})
    return proc_name.casefold() + ".exe" in names


def get_startup_dir():
    """获取 Windows 用户开机启动文件夹路径 (shell:startup)"""
    return os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs\Startup")


def get_startup_shortcut_path():
    """获取自启快捷方式完整路径"""
    return os.path.join(get_startup_dir(), "VoiceMeeter Banana AutoStart.lnk")


def open_startup_dir():
    """打开开机启动文件夹供用户直观查看"""
    sdir = get_startup_dir()
    if os.path.exists(sdir):
        os.startfile(sdir)
        return True, "已打开开机启动文件夹"
    return False, "未找到启动文件夹"


def get_system_status():
    """获取系统状态概要"""
    exe_path = get_installed_exe()
    is_installed = exe_path is not None and os.path.exists(exe_path)
    process_error = None
    try:
        is_running = is_process_running("voicemeeterpro")
    except Exception as exc:
        is_running, process_error = None, str(exc)

    startup = voicemeeter_startup_status()
    autostart_enabled = startup["enabled"]
    autostart_cmd = "; ".join(e.get("path") or e.get("command", "") for e in startup["entries"] if e["enabled"])

    return {
        "installed": is_installed,
        "exe_path": exe_path,
        "is_running": is_running,
        "process_error": process_error,
        "dll_path": get_remote_dll(),
        "docs_dir": DOCS_VM_DIR,
        "autostart_enabled": autostart_enabled,
        "autostart_cmd": autostart_cmd,
        "autostart_hidden": startup["hidden"],
        "autostart_details": startup
    }


@exclusive_operation
def launch_voicemeeter(config_xml=None, hide=False):
    exe_path = get_installed_exe()
    if not exe_path or not os.path.isfile(exe_path):
        return False, "未找到 VoiceMeeter Banana 主程序，请先安装。"
    if config_xml and not os.path.isfile(config_xml):
        return False, "选中的 XML 配置文件不存在，未改为忽略配置启动。"
    if is_process_running("voicemeeterpro"):
        if config_xml:
            return remote_api.load_and_verify(get_remote_dll(), config_xml)
        return True, "VoiceMeeter 已运行。"
    command = subprocess.list2cmdline([exe_path])
    if config_xml:
        # Preserve the vendor's literal -L"path with spaces" command format.
        command += ' -L"' + os.path.abspath(config_xml) + '"'
    if hide:
        command += " -h"
    flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(command, executable=exe_path, close_fds=True, creationflags=flags)
    return True, "已启动 VoiceMeeter 进程，配置是否生效由后续载入与读回核验确认。"


def wait_voicemeeter_exit(timeout):
    deadline = time.monotonic() + timeout
    while True:
        if not is_process_running("voicemeeterpro"):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.15)


@exclusive_operation
def kill_voicemeeter(allow_force=False):
    if not is_process_running("voicemeeterpro"):
        return True, "VoiceMeeter 当前未运行。"
    graceful_error = "正常退出等待超时"
    try:
        remote_api.request_shutdown(get_remote_dll())
        if wait_voicemeeter_exit(3.0):
            return True, "VoiceMeeter 已正常退出。"
    except Exception as exc:
        graceful_error = str(exc)
    if not allow_force:
        return False, "VoiceMeeter 未正常退出：" + graceful_error
    try:
        force_stop([VOICEMEETER_BANANA_EXE, VOICEMEETER_BANANA_X64_EXE])
        if not wait_voicemeeter_exit(3.0):
            return False, "强制结束后仍检测到 VoiceMeeter，未覆盖配置或重复启动。"
        return True, "VoiceMeeter 已自动强制结束并确认退出。"
    except Exception as exc:
        return False, "自动结束 VoiceMeeter 失败：" + str(exc)


@exclusive_operation
def restart_audio_engine():
    if not is_process_running("voicemeeterpro"):
        return False, "VoiceMeeter 尚未运行。"
    try:
        remote_api.request_engine_restart(get_remote_dll())
        return True, "已发送 VoiceMeeter 音频引擎重启命令。"
    except Exception as exc:
        return False, "发送音频引擎重启命令失败：" + str(exc)


# ==============================================================================
# 2. XML 配置文件解析与生效
# ==============================================================================

def parse_xml_config(xml_path):
    """
    解析 VoiceMeeter 配置文件 XML，提取通道名称、硬件输入输出等关键配置信息。
    支持容错：兼容 VB-Audio 导出的重复标签等非严格 XML 格式。
    """
    if not os.path.exists(xml_path):
        raise FileNotFoundError(f"配置文件未找到: {xml_path}")

    with open(xml_path, "rb") as f:
        raw = f.read(8 * 1024 * 1024 + 1)
    if len(raw) > 8 * 1024 * 1024:
        raise ValueError("XML 配置超过 8 MiB 安全上限")
    content = raw.decode("utf-8", errors="ignore")

    summary = {
        "hardware_inputs": [],
        "hardware_outputs": [],
        "strip_labels": {},
        "bus_labels": {},
        "raw_path": os.path.abspath(xml_path),
        "file_size": os.path.getsize(xml_path),
    }

    # Never scan attributes across another '<': repeated malformed opening
    # tags otherwise cause quadratic backtracking across the whole file.
    for match in re.finditer(r'<(InputDev|OutputDev)\b([^<>]*)>', content):
        attributes = xml_attributes(match.group(2))
        idx, name = attributes.get("index", ""), attributes.get("name", "")
        if not idx.isdecimal() or not name or name == "-":
            continue
        if match.group(1) == "InputDev":
            summary["hardware_inputs"].append(f"输入 {idx} (物理): {name}")
        else:
            summary["hardware_outputs"].append(f"输出 A{idx} (物理): {name}")

    label_matches = re.findall(r'<Label(Strip\d+|VirtualStrip\d+|Bus\d+|VirtualBus\d+)>([^<]+)</Label\1>', content)
    for label_type, label_val in label_matches:
        label_val = label_val.strip()
        if not label_val:
            continue
        if "Strip" in label_type:
            summary["strip_labels"][label_type] = label_val
        elif "Bus" in label_type:
            summary["bus_labels"][label_type] = label_val

    named_strips = []
    for i in range(1, 4):
        val = summary["strip_labels"].get(f"Strip{i}", f"硬件输入 {i}")
        named_strips.append(f"物理通道 {i}: {val}")
    for i in range(1, 3):
        val = summary["strip_labels"].get(f"VirtualStrip{i}", f"虚拟输入 {i}")
        named_strips.append(f"虚拟通道 {i}: {val}")
    summary["named_strips"] = named_strips

    named_buses = []
    for i in range(1, 4):
        val = summary["bus_labels"].get(f"Bus{i}", f"物理输出 A{i}")
        named_buses.append(f"物理输出 A{i}: {val}")
    for i in range(1, 3):
        val = summary["bus_labels"].get(f"VirtualBus{i}", f"虚拟输出 B{i}")
        named_buses.append(f"虚拟输出 B{i}: {val}")
    summary["named_buses"] = named_buses
    summary["network_risks"] = network_risks(content)

    return summary


@exclusive_operation
def apply_xml_config(xml_path, restart_app=True, set_as_default=True, progress_callback=None, allow_network_config=False, expected_config_hash=None):
    if not os.path.isfile(xml_path):
        return False, f"配置文件不存在：{xml_path}"
    # Freeze the chosen file BEFORE any shutdown. VoiceMeeter may overwrite its
    # LastSettings file with the current mixer state when it exits.
    with open(xml_path, "rb") as source:
        selected = source.read(8 * 1024 * 1024 + 1)
    if len(selected) > 8 * 1024 * 1024:
        return False, "XML 配置超过 8 MiB，未结束 VoiceMeeter。"
    digest = hashlib.sha256(selected).hexdigest()
    if expected_config_hash is not None and digest != expected_config_hash:
        return False, "选中的配置在确认后发生变化，未结束 VoiceMeeter；请重新检查并确认。"
    risks = network_risks(selected.decode("utf-8-sig"))
    if risks and not allow_network_config:
        return False, "配置包含 VBAN 网络音频/控制设置，请在界面预览并确认后再载入：\n" + "\n".join(risks)
    with tempfile.TemporaryDirectory(prefix="stereo-load-") as stage:
        frozen = Path(stage) / "selected.xml"
        frozen.write_bytes(selected)
        summary = parse_xml_config(frozen)
        if not (summary["hardware_inputs"] or summary["hardware_outputs"]):
            return False, "文件不是包含输入/输出设备的 VoiceMeeter 配置。"
        if restart_app and not remote_api.expected_parameters(selected.decode("utf-8-sig")):
            return False, "配置没有可核对的通道/总线参数，未结束 VoiceMeeter。"
        running = is_process_running("voicemeeterpro")
        if running and not restart_app:
            return False, "VoiceMeeter 正在运行；请使用一键应用以自动退出、载入并重新打开。"
        if running:
            if progress_callback:
                progress_callback("已固定选中配置；正在自动退出 VoiceMeeter，超时后将自动结束进程...")
            ok, message = kill_voicemeeter(allow_force=True)
            if not ok:
                return False, message
        load_path = str(frozen)
        if progress_callback:
            progress_callback("已确认 VoiceMeeter 退出；正在保存选中配置和默认设置...")
        if set_as_default:
            os.makedirs(DOCS_VM_DIR, exist_ok=True)
            last_xml = os.path.join(DOCS_VM_DIR, "VoicemeeterBanana_LastSettings.xml")
            today_xml = os.path.join(DOCS_VM_DIR, "VoicemeeterBanana_TodaySettings.xml")
            if Path(xml_path).resolve() == Path(get_resource_path("sample_config.xml")).resolve():
                from xml.sax.saxutils import escape
                content = selected.decode("utf-8-sig")
                content = re.sub(r"<RecordDirectory>[^<]*</RecordDirectory>",
                                 lambda _: "<RecordDirectory>" + escape(DOCS_VM_DIR) + "</RecordDirectory>", content)
                frozen.write_text(content, encoding="utf-8")
            replace_default_configs(frozen, [last_xml, today_xml])
            load_path = last_xml
        if not restart_app:
            return True, "选中配置已保存，未启动 VoiceMeeter。"
        menu_warning = ""
        if set_as_default:
            menus_ok, menus_message = configure_voicemeeter_menu_defaults()
            if not menus_ok:
                menu_warning = "\n默认菜单设置未全部完成：" + menus_message
        if progress_callback:
            progress_callback("正在隐藏重新打开 VoiceMeeter，并通过官方接口载入配置...")
        ok, message = launch_voicemeeter(load_path, hide=True)
        if not ok:
            return False, "配置已保存，但自动重新打开失败：" + message
        # Popen success and an already-running process do not prove Load worked.
        if progress_callback:
            progress_callback("正在读回核对静音、独奏、增益及 A/B 路由，请稍候...")
        ok, message = remote_api.load_and_verify(get_remote_dll(), load_path)
        if not ok:
            return False, "VoiceMeeter 已重新打开，但配置尚未通过读回核验：" + message + menu_warning
        return not menu_warning, "选中配置已自动载入，VoiceMeeter 已隐藏重新打开。\n" + message + menu_warning


def export_current_config(save_path):
    """导出当前运行配置"""
    last_xml = os.path.join(DOCS_VM_DIR, "VoicemeeterBanana_LastSettings.xml")
    if not os.path.exists(last_xml):
        return False, "未找到当前 VoiceMeeter 配置（请先在软件中保存一次设置）。"
    try:
        shutil.copy2(last_xml, save_path)
        return True, f"配置已成功导出至: {save_path}"
    except Exception as e:
        return False, f"导出失败: {str(e)}"


@exclusive_operation
def configure_autostart(xml_path=None, enable=True, hide=True):
    """Manage VoiceMeeter's own registration, never register this manager."""
    if not enable:
        return False, "请在 VoiceMeeter 的 Run on Windows Startup 菜单或 Windows 启动设置中取消；工具不会删除现有启动项。"
    exe = get_installed_exe()
    if not exe:
        return False, "未找到 VoiceMeeter Banana，请先安装。"
    # Native VoiceMeeter reloads its own saved settings. A Startup click must not
    # overwrite the selected XML or point to a transient bundled resource.
    return ensure_hidden_startup([VOICEMEETER_BANANA_EXE, VOICEMEETER_BANANA_X64_EXE], exe)


@exclusive_operation
def configure_voicemeeter_menu_defaults(xml_path=None, allow_network_config=False, expected_config_hash=None):
    """Preserve one-click defaults: automatically stop/reload if VM is running."""
    options = {}
    if allow_network_config:
        options["allow_network_config"] = True
    if expected_config_hash is not None:
        options["expected_config_hash"] = expected_config_hash
    if is_process_running("voicemeeterpro"):
        if xml_path:
            return apply_xml_config(xml_path, restart_app=True, set_as_default=True, **options)
        # The GUI normally provides the selected XML. Avoid guessing or replacing
        # a running mix with the bundled sample when no file was supplied.
        last = os.path.join(DOCS_VM_DIR, "VoicemeeterBanana_LastSettings.xml")
        if os.path.isfile(last):
            return apply_xml_config(last, restart_app=True, set_as_default=True, **options)
        return False, "请先选择要应用的配置文件，再一键设置；不会擅自改用另一份配置。"
    if not get_installed_exe():
        return False, "未找到 VoiceMeeter Banana。"
    if xml_path:
        ok, message = apply_xml_config(xml_path, restart_app=False, set_as_default=True, **options)
        if not ok:
            return False, message
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"VB-Audio\VoiceMeeter") as key:
        for name, value in {"AutoRestart": 1, "AutoRestartAll": 1, "SysTray": 1,
                            "DelayedStartS": 5, "SysTrayShowOnRun": 0, "HideMode": 1}.items():
            winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, value)
    ok, message = configure_autostart()
    if not ok:
        return False, "默认托盘/引擎设置已保存，但隐藏自启未完成：" + message
    return True, "默认托盘、隐藏窗口、引擎重启设置已保存；下次启动 VoiceMeeter 后检查菜单。\n" + message


# ==============================================================================
# 3. 驱动检测与自动下载安装 (VB-CABLE, VAC Lite)
# ==============================================================================

def check_driver_installed(driver_keyword):
    """检查指定关键词的声卡驱动是否已安装"""
    try:
        output = subprocess.check_output(
            [system_executable("powershell"), "-NoProfile", "-Command", "$ErrorActionPreference='Stop'; $env:PSModulePath=Join-Path $PSHOME 'Modules'; Get-CimInstance Win32_SoundDevice | Select-Object -ExpandProperty Name"],
            text=True, timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        )
        return driver_keyword.lower() in output.lower()
    except Exception:
        return None


def check_vbcable_installed():
    """检测 VB-Audio Virtual Cable 是否已安装"""
    return check_driver_installed("VB-Audio Virtual Cable")


def check_vac_installed():
    """检测 Virtual Audio Cable (VAC) 是否已安装"""
    return check_driver_installed("Virtual Audio Cable")


def download_file_with_ua(url, dest_path, progress_callback=None):
    """验证 HTTPS 重定向并限制下载时间、体积，原子发布完整文件。"""
    download_https(url, dest_path, progress_callback)


def _install_reviewed_driver(package_id, progress_callback=None):
    work_dir = None
    retain_cache = False
    cache_root = Path(CACHE_DIR).resolve()
    try:
        package = PACKAGES[package_id]
        os.makedirs(CACHE_DIR, exist_ok=True)
        work_dir = tempfile.mkdtemp(prefix=package_id + "-", dir=CACHE_DIR)
        zip_path = os.path.join(work_dir, "vendor.zip")
        extract_dir = os.path.join(work_dir, "extracted")
        if progress_callback:
            progress_callback("正在从厂商官网下载：" + package["url"])
        download_file_with_ua(package["url"], zip_path)
        verify_package(zip_path, package_id)
        safe_extract_zip(zip_path, extract_dir)
        if progress_callback:
            progress_callback("正在验证安装器签名和发布者；通过后请求管理员授权...")
        launch_verified_archive(zip_path, package_id, extract_dir)
        return True, "安全验证和官方安装向导已正常结束；临时下载缓存已进入清理。驱动生效可能需要重启电脑。"
    except BrokerStillRunning as exc:
        retain_cache = True
        return False, str(exc)
    except Exception as exc:
        return False, f"安装已停止：{exc}\n如被杀毒软件隔离，请保留检测名称并从官网核查，勿关闭防护。"
    finally:
        if work_dir and not retain_cache:
            candidate = Path(work_dir)
            # Remove only the per-request directory we created, never the cache
            # root, local installer, or an unresolved junction outside that root.
            if (candidate.resolve().parent == cache_root
                    and candidate.name.startswith(package_id + '-')
                    and not candidate.is_symlink()
                    and not candidate.is_junction()):
                try:
                    shutil.rmtree(candidate)
                except OSError:
                    if progress_callback:
                        progress_callback('临时缓存被占用，已保留：' + str(candidate))


@exclusive_operation
def install_vbcable(progress_callback=None):
    return _install_reviewed_driver("vbcable", progress_callback)


@exclusive_operation
def install_vac(progress_callback=None):
    return _install_reviewed_driver("vac", progress_callback)


@exclusive_operation
def install_voicemeeter(installer_path=None, method="auto"):
    """安装 VoiceMeeter"""
    possible_installers = []
    if installer_path and os.path.exists(installer_path):
        possible_installers.append(installer_path)

    local_setup = os.path.join(BASE_DIR, "VoicemeeterProSetup.exe")
    if os.path.exists(local_setup):
        possible_installers.append(local_setup)

    if os.path.exists(SETUP_EXE):
        possible_installers.append(SETUP_EXE)

    if method in ("local", "auto") and possible_installers:
        target = possible_installers[0]
        try:
            launch_verified_local(target)
            return True, "安全验证和官方安装向导已正常结束；驱动生效可能需要重启电脑。"
        except Exception as e:
            return False, f"本地安装异常: {str(e)}"

    if method in ("winget", "auto"):
        try:
            winget_cmd = [
                os.path.join(os.environ["LOCALAPPDATA"], "Microsoft", "WindowsApps", "winget.exe"),
                "install", "-e", "--id", "VB-Audio.Voicemeeter", "--source", "winget",
                "--accept-source-agreements", "--accept-package-agreements",
                "--silent"
            ]
            proc = subprocess.run(
                winget_cmd,
                capture_output=True,
                text=True,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            )
            if proc.returncode == 0:
                return True, "已通过 Windows Winget 成功安装 VoiceMeeter！\n提示: 虚拟音频驱动需要重启生效。"
            else:
                return False, f"Winget 安装失败 (代码 {proc.returncode}):\n{proc.stdout or proc.stderr}"
        except Exception as e:
            return False, f"Winget 执行失败: {str(e)}"

    return False, "未找到有效的安装源（本地安装包或 Winget）。"


# ==============================================================================
# 4. 系统默认声音设备检测与一键设置 (对齐用户截图)
# ==============================================================================

def get_system_audio_status():
    result = query_system_audio()
    result["is_playback_match"] = matches_preset(result["playback_default"], 0)
    result["is_record_match"] = matches_preset(result["record_default"], 1)
    return result


@exclusive_operation
def set_system_default_audio(set_playback=True, set_record=True):
    return set_default_preset(playback=set_playback, record=set_record)


def open_sound_control_panel():
    """打开 Windows 原生声音控制面板 (mmsys.cpl，即截图中的界面)"""
    try:
        subprocess.Popen([system_executable("control"), os.path.join(os.path.dirname(system_executable("control")), "mmsys.cpl")])
        return True, "已打开系统声音控制面板！"
    except Exception as e:
        return False, f"打开失败: {str(e)}"


# ==============================================================================
# 5. Chrome 浏览器 Flags 自动配置
# ==============================================================================

def get_chrome_user_data_path():
    """获取 Chrome User Data 路径"""
    return os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\User Data")


def get_chrome_version():
    """获取安装的 Chrome 主版本号 (例如 154)"""
    paths = [
        r"C:\Program Files\Google\Chrome\Application",
        r"C:\Program Files (x86)\Google\Chrome\Application",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application")
    ]
    for p in paths:
        if os.path.isfile(os.path.join(p, "chrome.exe")):
            versions = [tuple(map(int, item.split('.'))) for item in os.listdir(p)
                        if re.fullmatch(r"[0-9]+(?:\.[0-9]+)+", item)
                        and os.path.isdir(os.path.join(p, item))]
            return max(versions)[0] if versions else None, os.path.join(p, "chrome.exe")
    return None, None


def get_last_active_chrome_profile():
    """
    获取最后打开/修改的 Chrome Profile (多开账号) 信息
    返回: (profile_dir, profile_name, last_time_str)
    """
    user_data = get_chrome_user_data_path()
    try:
        with open(os.path.join(user_data, "Local State"), "r", encoding="utf-8") as stream:
            state = json.load(stream)
        recent = state.get("profile", {}).get("last_used")
        if isinstance(recent, str) and re.fullmatch(r"Default|Profile [0-9]+", recent):
            info = state.get("profile", {}).get("info_cache", {}).get(recent, {})
            return recent, info.get("shortcut_name") or info.get("name") or recent, "Chrome 最近使用记录"
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    if not os.path.exists(user_data):
        return None, None, None

    candidates = []
    try:
        for d in os.listdir(user_data):
            p_dir = os.path.join(user_data, d)
            if os.path.isdir(p_dir) and re.fullmatch(r"Default|Profile [0-9]+", d):
                pref = os.path.join(p_dir, "Preferences")
                target = pref if os.path.exists(pref) else p_dir
                try:
                    candidates.append((d, os.path.getmtime(target)))
                except Exception:
                    pass
    except Exception:
        pass

    if not candidates:
        return "Default", "Default", "未知"

    candidates.sort(key=lambda x: x[1], reverse=True)
    last_profile, mtime = candidates[0]
    last_time_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(mtime))

    # 获取友好名称
    friendly_name = last_profile
    local_state_file = os.path.join(user_data, "Local State")
    if os.path.exists(local_state_file):
        try:
            with open(local_state_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            info = data.get("profile", {}).get("info_cache", {}).get(last_profile, {})
            friendly_name = info.get("shortcut_name") or info.get("name") or last_profile
        except Exception:
            pass

    return last_profile, friendly_name, last_time_str


def chrome_flags_url(flag_type="all"):
    urls = {
        "all": "chrome://flags/",
        "unexpire": "chrome://flags/#temporary-unexpire-flags-m154",
        "webrtc": "chrome://flags/#enable-webrtc-allow-input-volume-adjustment",
        "echo": "chrome://flags/#chrome-wide-echo-cancellation",
    }
    if flag_type not in urls:
        raise ValueError("未知的音频设置类型")
    return urls[flag_type]


def open_chrome_audio_flags_in_last_browser(flag_type="all"):
    """Open a fixed internal URL in the most recently used profile, without global key injection."""
    try:
        target_url = chrome_flags_url(flag_type)
        _, chrome_exe = get_chrome_version()
        if not chrome_exe or not os.path.isfile(chrome_exe):
            return False, "未检测到已安装的 Google Chrome 浏览器。"
        last_profile, friendly_name, _ = get_last_active_chrome_profile()
        if not last_profile:
            last_profile = "Default"
        if not re.fullmatch(r"Default|Profile [0-9]+", last_profile):
            return False, "最近使用的多开账号目录名称异常，已停止打开。"
        cmd = [chrome_exe, f"--profile-directory={last_profile}", target_url]
        subprocess.Popen(cmd, close_fds=True)
        if not navigate_chrome_omnibox(chrome_exe, target_url):
            return False, ("已请求打开账号，但未能确认 Chrome 前台窗口，已停止自动输入。\n"
                           f"请在目标账号地址栏粘贴已复制的网址并回车：{target_url}")
        return True, f"已在 Chrome 账号【{friendly_name or last_profile}】的地址栏打开：{target_url}"
    except Exception as exc:
        return False, f"打开 Chrome 页面失败：{exc}"


def get_chrome_flags_status():
    """获取 Chrome Flags 配置状态"""
    user_data = get_chrome_user_data_path()
    local_state_file = os.path.join(user_data, "Local State")
    if not os.path.exists(local_state_file):
        return {
            "has_chrome": get_chrome_version()[1] is not None,
            "local_state_exists": False,
            "experiments": [],
            "vol_adjustment_disabled": False,
            "echo_cancellation_disabled": False,
            "unexpire_details": "M154 / M155：未读取到设置",
            "unexpire_enabled": False
        }

    try:
        with open(local_state_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        exp = data.get("browser", {}).get("enabled_labs_experiments", [])

        vol_disabled = "enable-webrtc-allow-input-volume-adjustment@2" in exp
        echo_disabled = "chrome-wide-echo-cancellation@2" in exp
        milestones = [154, 155]
        unexpire_enabled = all(f"temporary-unexpire-flags-m{m}@1" in exp for m in milestones)
        unexpire_details = " / ".join(f"M{m}：" + ("已启用" if f"temporary-unexpire-flags-m{m}@1" in exp else "未设置") for m in milestones)

        return {
            "has_chrome": True,
            "local_state_exists": True,
            "experiments": exp,
            "vol_adjustment_disabled": vol_disabled,
            "echo_cancellation_disabled": echo_disabled,
            "unexpire_details": unexpire_details,
            "unexpire_enabled": unexpire_enabled
        }
    except Exception as exc:
        return {
            "has_chrome": True,
            "read_error": str(exc),
            "local_state_exists": True,
            "experiments": [],
            "vol_adjustment_disabled": False,
            "echo_cancellation_disabled": False,
            "unexpire_details": "M154 / M155：未读取到设置",
            "unexpire_enabled": False
        }


@exclusive_operation
def apply_chrome_audio_flags(kill_chrome_first=False):
    """
    配置 Chrome Flags 写入 Local State：
    1. 启用最近两个/当前版本的 Temporarily unexpire flags (@1)
    2. 将 Allow WebRTC to adjust the input volume 设为 Disabled (@2)
    3. 将 Chrome-wide echo cancellation 设为 Disabled (@2)

    安全机制：
    - 绝不在后台强杀 Chrome (避免 200+ 多开会话丢失)
    - 写入前自动生成带时间戳的安全备份 Local State.bak_YYYYMMDD_HHMMSS
    - 若 Chrome 正在运行则提示用户手动关闭，或推荐使用免改配置的命令行快捷方式
    """
    user_data = get_chrome_user_data_path()
    local_state_file = os.path.join(user_data, "Local State")
    if not os.path.exists(local_state_file):
        return False, f"未找到 Chrome 配置文件: {local_state_file}\n请确保已安装并至少运行过一次 Chrome！"

    # 安全检查：绝不静默杀死运行中的 Chrome 多开进程
    if is_process_running("chrome"):
        return False, (
                "⚠ 检测到 Chrome 浏览器正在运行中 (包含多个多开实例)！\n\n"
                "为了彻底保障您的多开窗口和账号数据安全，本工具已禁止强制终止 Chrome。\n"
                "建议做法：\n"
                "1. 推荐：直接点击【生成免修改安全快捷方式】，免改配置直接启动；\n"
                "2. 若要写入配置文件，请先手动关闭所有 Chrome 窗口后再点击应用。"
            )

    try:
        # 1. 自动生成带时间戳的安全备份
        original_backup = backup_file(local_state_file)

        data, expected = read_json_snapshot(local_state_file)

        browser_dict = data.setdefault("browser", {})
        experiments = browser_dict.setdefault("enabled_labs_experiments", [])

        # 获取 Chrome 版本，动态计算需要开启的 unexpire milestones
        major_ver, _ = get_chrome_version()
        unexpire_list = []
        if major_ver:
            # 包含当前及前两个版本，例如 154, 153, 152, 151, 150, 149
            for m in range(max(145, major_ver - 4), major_ver + 1):
                unexpire_list.append(f"temporary-unexpire-flags-m{m}@1")
        else:
            # 兜底默认 M149, M150, M151, M152, M153, M154
            for m in [149, 150, 151, 152, 153, 154]:
                unexpire_list.append(f"temporary-unexpire-flags-m{m}@1")

        # 移除已有冲突项
        clean_exp = []
        target_keys = [
            "enable-webrtc-allow-input-volume-adjustment",
            "chrome-wide-echo-cancellation"
        ]
        for item in experiments:
            base_name = item.split("@")[0]
            if base_name in target_keys or base_name.startswith("temporary-unexpire-flags-m"):
                continue
            clean_exp.append(item)

        # 写入目标项
        # 1. 临时延期 flags 设为 Enabled (@1)
        for u in unexpire_list:
            clean_exp.append(u)

        # 2. Allow WebRTC to adjust the input volume 设为 Disabled (@2)
        clean_exp.append("enable-webrtc-allow-input-volume-adjustment@2")

        # 3. Chrome-wide echo cancellation 设为 Disabled (@2)
        clean_exp.append("chrome-wide-echo-cancellation@2")

        browser_dict["enabled_labs_experiments"] = clean_exp

        if is_process_running("chrome"):
            return False, "Chrome 已重新启动，取消写入以保护浏览器配置。"
        commit_backup = guarded_json(local_state_file, data, expected, lambda: not is_process_running("chrome"))

        return True, (
            "✔ Chrome 音频 Flags 已成功写入 Local State！\n"
            f"• 延期 Flags (Enabled): {', '.join([u.split('@')[0] for u in unexpire_list])}\n"
            "• 麦克风自动音量调整 (Disabled): enable-webrtc-allow-input-volume-adjustment\n"
            "• 全局回声消除 (Disabled): chrome-wide-echo-cancellation\n"
            f"• 原始备份已保存: {os.path.basename(commit_backup or original_backup)}\n"
            "重新打开 Chrome 即可生效！"
        )
    except Exception as e:
        return False, f"写入 Chrome 配置失败: {str(e)}"


def get_chrome_backups():
    """获取所有 Chrome Local State 备份文件列表"""
    user_data = get_chrome_user_data_path()
    if not os.path.exists(user_data):
        return []
    backups = []
    for f in os.listdir(user_data):
        if f.startswith("Local State.bak"):
            full_p = os.path.join(user_data, f)
            try:
                mtime = os.path.getmtime(full_p)
                size = os.path.getsize(full_p)
                backups.append((f, full_p, mtime, size))
            except Exception:
                pass
    backups.sort(key=lambda x: x[2], reverse=True)
    return backups


@exclusive_operation
def restore_chrome_backup(backup_path=None):
    """
    一键恢复 Chrome 原始 Local State 配置：
    从 Local State.bak* 备份中还原，彻底恢复所有多开配置及原始设置。
    """
    if is_process_running("chrome"):
        return False, "请先手动退出所有 Chrome 窗口，再恢复备份。"
    user_data = get_chrome_user_data_path()
    local_state_file = os.path.join(user_data, "Local State")

    backups = get_chrome_backups()
    if not backups:
        return False, "未在 Chrome 目录找到任何 Local State 备份文件！"

    if backup_path and not os.path.isfile(backup_path):
        return False, "指定的备份文件已不存在，未擅自改用其他备份。"
    target_backup = backup_path if backup_path else backups[0][1]

    try:
        _, expected = read_json_snapshot(local_state_file) if os.path.exists(local_state_file) else ({}, None)
        restored, _ = read_json_snapshot(target_backup)
        if not isinstance(restored, dict) or not restored:
            return False, "备份不是有效的 Chrome 配置对象，未恢复。"
        backup_file(local_state_file, "pre_restore")
        if is_process_running("chrome"):
            return False, "Chrome 已重新启动，取消恢复。"
        guarded_json(local_state_file, restored, expected, lambda: not is_process_running("chrome"))
        return True, (
            f"✔ 成功恢复 Chrome 原始配置！\n"
            f"还原备份源: {os.path.basename(target_backup)}\n"
            "所有浏览器多开配置已完全重置恢复至备份时的状态。"
        )
    except Exception as e:
        return False, f"还原备份失败: {str(e)}"


@exclusive_operation
def restore_chrome_profile_shortcuts(target_dir=None):
    """
    一键恢复/重建所有 Chrome 多开快捷方式：
    从 Local State (或其备份) 中读取所有多开 Profile 账号信息，
    并在桌面 'Chrome多开快捷方式' 文件夹中批量生成所有对应的快捷方式！
    """
    user_data = get_chrome_user_data_path()
    local_state_file = os.path.join(user_data, "Local State")

    # 如果当前 Local State 不存在或无法读取，尝试从备份读取
    data = None
    candidates = [local_state_file] + [b[1] for b in get_chrome_backups()]
    for p in candidates:
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if data and "profile" in data and "info_cache" in data["profile"]:
                    break
            except Exception:
                continue

    if not data or "profile" not in data or "info_cache" not in data["profile"]:
        return False, "未能从 Local State 中读取到多开 Profile 信息！"

    info_cache = data["profile"]["info_cache"]
    if not info_cache:
        return False, "未找到任何 Chrome 多开配置文件 (Profile)。"

    _, chrome_exe = get_chrome_version()
    if not chrome_exe or not os.path.exists(chrome_exe):
        chrome_exe = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

    if not os.path.exists(chrome_exe):
        return False, "未找到 Chrome 浏览器可执行程序路径。"

    desktop_dir = os.path.expanduser("~/Desktop")
    if target_dir is None:
        target_dir = os.path.join(desktop_dir, "Chrome多开快捷方式")

    os.makedirs(target_dir, exist_ok=True)

    success_count = 0
    failures = []
    for profile_dir, p_info in info_cache.items():
        if not re.fullmatch(r"Default|Profile [0-9]+", profile_dir) or not isinstance(p_info, dict):
            failures.append(str(profile_dir))
            continue
        raw_name = p_info.get("shortcut_name") or p_info.get("name") or profile_dir
        safe_name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", str(raw_name)).strip('. ')[:100] or "Chrome"
        # Include directory id to avoid two equal account names overwriting links.
        lnk_path = os.path.join(target_dir, f"{safe_name} ({profile_dir}).lnk")
        if os.path.exists(lnk_path):
            failures.append(profile_dir + "（同名快捷方式已存在，未覆盖）")
            continue
        try:
            write_shortcut(lnk_path, chrome_exe, subprocess.list2cmdline([f"--profile-directory={profile_dir}"]))
            success_count += 1
        except OSError as exc:
            failures.append(profile_dir + ": " + str(exc))
    return not failures and success_count > 0, (f"已创建 {success_count} 个快捷方式；跳过或失败 {len(failures)} 个。\n目录：{target_dir}\n" + "; ".join(failures[:5]))


@exclusive_operation
def create_chrome_safe_shortcut(desktop=True):
    _, chrome_exe = get_chrome_version()
    if not chrome_exe or not os.path.isfile(chrome_exe):
        return False, "未找到 Chrome。"
    folder = os.path.expanduser("~/Desktop") if desktop else BASE_DIR
    lnk_path = os.path.join(folder, "Google Chrome (立体音设置).lnk")
    if os.path.exists(lnk_path):
        return False, "同名快捷方式已存在，未覆盖。"
    write_shortcut(lnk_path, chrome_exe, "--disable-features=WebRtcAllowInputVolumeAdjustment,ChromeWideEchoCancellation")
    return True, f"快捷方式已创建：{lnk_path}\n请完全退出 Chrome 后使用；开关是否有效取决于浏览器版本。"


def launch_chrome_with_audio_flags():
    """
    启动 Chrome 并附加命令行免音频处理参数：
    开关是否生效取决于 Chrome 版本，运行中的浏览器可能忽略新参数。
    --disable-features=WebRtcAllowInputVolumeAdjustment,ChromeWideEchoCancellation
    """
    _, chrome_exe = get_chrome_version()
    if not chrome_exe or not os.path.exists(chrome_exe):
        chrome_exe = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

    if not os.path.exists(chrome_exe):
        return False, "未找到 Google Chrome 可执行文件！"

    if is_process_running("chrome"):
        return False, "请先完全退出 Chrome；运行中的浏览器可能忽略新启动参数。"
    cmd = [chrome_exe, "--disable-features=WebRtcAllowInputVolumeAdjustment,ChromeWideEchoCancellation"]
    try:
        subprocess.Popen(cmd)
        return True, "已请求带音频参数启动 Chrome，请在浏览器中验证效果。"
    except Exception as e:
        return False, f"启动 Chrome 失败: {str(e)}"

