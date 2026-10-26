@echo off
cd /d "%~dp0"
python digital8_gui.py
if errorlevel 1 pause
