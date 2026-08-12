@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Tes Unit Notulen Rapat

echo.
echo ==========================================
echo       TES UNIT NOTULEN RAPAT
echo ==========================================
echo.

rem ----- Cek Python tersedia -----
where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python tidak ditemukan di PATH.
    echo Instal Python 3.9-3.11 dari https://python.org lalu coba lagi.
    pause
    exit /b 1
)

rem ----- Aktivasi virtual environment jika ada -----
if exist ".venv\Scripts\activate.bat" (
    call ".venv\Scripts\activate.bat"
) else if exist "venv\Scripts\activate.bat" (
    call "venv\Scripts\activate.bat"
)

echo [INFO] Menjalankan semua tes unit...
echo.
python -m unittest test_zoom_notulen test_index_notulen -v
set "EXITCODE=%ERRORLEVEL%"

echo.
if not "%EXITCODE%"=="0" (
    echo [ERROR] Ada tes yang gagal (kode %EXITCODE%).
    echo Solusi umum:
    echo   - pip install -r requirements.txt
    echo   - Pastikan ffmpeg terinstal dan masuk PATH
)
pause
exit /b %EXITCODE%
