"""Explicit Windows build entry point. Audio uses native COM; no helper executable is compiled."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def validate_sources():
    required = ('sample_config.xml', 'VoiceMeeterManager_Single.spec',
                'vm_gui.py', 'vm_manager.py', 'startup_settings.py',
                'windows_shortcuts.py', 'secure_install.py', 'chrome_navigation.py', 'windows_audio.py', 'voicemeeter_remote.py', 'voicemeeter_process.py', 'xml_config.py', 'file_safety.py', 'backup_maintenance.py')
    for name in required:
        if not (ROOT / name).is_file():
            raise FileNotFoundError('Required source missing: ' + name)
    for path in ROOT.glob('*.py'):
        compile(path.read_bytes(), str(path), 'exec')


def build():
    if sys.platform != 'win32':
        raise RuntimeError('Build on Windows or use the GitHub Actions Windows workflow.')
    validate_sources()
    if sys.version_info < (3, 14, 10):
        raise RuntimeError('Build requires Python 3.14.10 or newer; use the pinned GitHub workflow.')
    subprocess.run([sys.executable, '-m', 'PyInstaller', 'VoiceMeeterManager_Single.spec',
                    '--noconfirm', '--distpath', 'dist', '--workpath', 'build'],
                   check=True, cwd=ROOT, timeout=600)
    executable = ROOT / 'dist/StereoAudioManager.exe'
    if not executable.is_file():
        raise RuntimeError('Expected executable missing after build')
    import PyInstaller
    sources = sorted([p for p in ROOT.glob('*.py')] + [ROOT / 'sample_config.xml', ROOT / 'VoiceMeeterManager_Single.spec', ROOT / 'requirements-build.txt'])
    info = {'version': '2.1.6', 'python': platform.python_version(), 'pyinstaller': PyInstaller.__version__,
            'commit': os.environ.get('GITHUB_SHA'),
            'exe_sha256': hashlib.sha256(executable.read_bytes()).hexdigest(),
            'source_sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}}
    (ROOT / 'dist/build-info.json').write_text(json.dumps(info, indent=2), encoding='utf-8')
    (ROOT / 'dist/SHA256SUMS.txt').write_text(info['exe_sha256'] + '  StereoAudioManager.exe\n', encoding='ascii')
    print('Built: ' + str(executable))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--check-only', action='store_true', help='Validate sources without compiling any binaries')
    options = parser.parse_args()
    if options.check_only:
        validate_sources()
        print('Source/build inputs OK; no binaries generated.')
    else:
        build()
