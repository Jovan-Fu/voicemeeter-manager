@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist "StereoAudioManager.exe" (
    start "" "StereoAudioManager.exe"
    exit /b 0
)
python -B vm_gui.py
if errorlevel 1 pause
