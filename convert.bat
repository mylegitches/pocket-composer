@echo off
cd /d "%~dp0"
if "%~1"=="" (".venv\Scripts\python.exe" -m flp2score data) else (".venv\Scripts\python.exe" -m flp2score %*)
