@echo off
chcp 65001 >nul
setlocal
echo Đang chạy script build SoátVăn Portable...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\build-portable.ps1"
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [ERROR] Build thất bại với mã lỗi %ERRORLEVEL%.
    pause
    exit /b %ERRORLEVEL%
)
echo.
echo [SUCCESS] Đã hoàn tất đóng gói Portable!
pause
