@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "SHEETS_POST_FILTER_DATA_DIR=%LOCALAPPDATA%\sheets-post-filter"
python -c "import tkinter,gspread" 2>nul
if errorlevel 1 (
  echo 正在安装依赖...
  python -m pip install -r requirements.txt
)
echo 正在打开最新版数据汇总工具...
python desktop_app.py
pause
