@echo off
chcp 65001 >nul
cd /d "%~dp0"
python tools\check_public_release.py
if errorlevel 1 exit /b 1
python -m pip install -r requirements-build.txt -q
if errorlevel 1 exit /b 1
python -m PyInstaller --noconfirm sheets_post_filter.spec
if errorlevel 1 (
  echo 打包失败
  pause
  exit /b 1
)
copy /Y config.example.json "dist\数据汇总工具\config.example.json" >nul
copy /Y 使用说明.txt "dist\数据汇总工具\使用说明.txt" >nul
copy /Y logo.ico "dist\数据汇总工具\logo.ico" >nul
copy /Y "web\static\logo.png" "dist\数据汇总工具\logo.png" >nul
if not exist "dist\数据汇总工具\web\static" mkdir "dist\数据汇总工具\web\static"
copy /Y "web\static\logo.png" "dist\数据汇总工具\web\static\logo.png" >nul
copy /Y uninstall-template.bat "dist\数据汇总工具\uninstall.bat" >nul
echo.
echo 已生成：%~dp0dist\数据汇总工具\数据汇总工具.exe
echo 双击 exe，或使用桌面快捷方式「数据汇总工具」
pause
