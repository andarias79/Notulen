@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Index Notulen Rapat

echo.
echo ==========================================
echo      INDEX NOTULEN RAPAT
echo ==========================================
echo.

rem ----- Cek Python tersedia -----
where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python tidak ditemukan di PATH.
    echo Instal Python 3.9-3.13 dari https://python.org lalu coba lagi.
    pause
    exit /b 1
)

rem ----- Aktivasi virtual environment jika ada -----
if exist ".venv\Scripts\activate.bat" (
    call ".venv\Scripts\activate.bat"
) else if exist "venv\Scripts\activate.bat" (
    call "venv\Scripts\activate.bat"
)

echo [INFO] Menjalankan: python index_notulen.py
echo.
python index_notulen.py
set "EXITCODE=%ERRORLEVEL%"

echo.
if not "%EXITCODE%"=="0" (
    echo [ERROR] Index gagal dengan kode %EXITCODE%.
)
pause
exit /b %EXITCODE%
