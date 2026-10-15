@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist "%~dp0FileForge.exe" (
  start "" "%~dp0FileForge.exe"
  exit /b 0
)
python "%~dp0fileforge_app.py"
if errorlevel 1 pause
