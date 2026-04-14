@echo off
setlocal
cd /d "%~dp0"
echo === DART QuickReport — local Windows build ===

rem 1) deps
python -m pip install --upgrade pip || goto :err
pip install -r requirements_qr.txt pyinstaller || goto :err

rem 2) 로컬 빌드는 _version.py 를 'local' 로 표시 (자동업데이트 항상 발동)
echo # Local build > dart_qr\_version.py
echo __version__ = "local" >> dart_qr\_version.py

rem 3) build
pyinstaller --onefile --windowed --clean ^
  --name DART_QuickReport ^
  --collect-all anthropic ^
  --hidden-import=openpyxl ^
  --hidden-import=dotenv ^
  dart_quickreport.py || goto :err

echo.
echo Built: dist\DART_QuickReport.exe
echo (Tip: 로컬 빌드는 GitHub Release 의 최신 .exe 로 자동 업데이트됩니다.)
pause
exit /b 0

:err
echo.
echo Build failed.
pause
exit /b 1
