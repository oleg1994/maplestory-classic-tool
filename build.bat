@echo off
cd /d "%~dp0"
.venv\Scripts\python -m PyInstaller --noconfirm packaging\MapleTool.spec
