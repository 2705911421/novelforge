@echo off
setlocal
cd /d "%~dp0.."
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0launch_novelforge.ps1" -ProjectRoot "%CD%"
endlocal
