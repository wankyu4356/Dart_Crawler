@echo off
setlocal
cd /d "%~dp0"
echo === DART QuickReport onefile build ===
pip install -r requirements_qr.txt pyinstaller || goto :err
pyinstaller --onefile --windowed --clean --name DART_QuickReport ^
  --collect-all anthropic ^
  --hidden-import=openpyxl ^
  --hidden-import=dotenv ^
  dart_quickreport.py || goto :err
echo.
echo Built: dist\DART_QuickReport.exe
pause
exit /b 0
:err
echo Build failed.
pause
exit /b 1
