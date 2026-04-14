@echo off
python "%~dp0dart_fill_v10.py"
if %errorlevel% neq 0 (
    echo.
    echo [Error] Please run 1_install.bat first.
    echo.
    pause
)
