@echo off
rem CATATAN: jangan letakkan `chcp 65001` di bagian atas file setelah menu
rem interaktif (lihat catatan di run.bat). Di sini tidak ada set /p yang
rem dibaca via stdin, tapi chcp tetap ditaruh di :eksekusi agar konsisten.
setlocal EnableDelayedExpansion
cd /d "%~dp0"
title Notulen API Server

rem ============================================================
rem  run-api.bat - Jalankan server REST Notulen (api_notulen.py)
rem
rem  Penggunaan:
rem    run-api.bat                  -> server di http://127.0.0.1:8000
rem    run-api.bat --port 9000      -> server di http://127.0.0.1:9000
rem    run-api.bat --host 0.0.0.0   -> bisa diakses dari jaringan lokal
rem
rem  Dokumentasi interaktif setelah menyala: http://127.0.0.1:8000/docs
rem  (port 8000 diganti bila Anda memakai --port)
rem ============================================================

if not exist "api_notulen.py" goto :no_api

set "ARGS=%*"
goto :eksekusi

:eksekusi
echo.
echo ==========================================
echo    NOTULEN API SERVER (api_notulen.py)
echo ==========================================
echo.

rem ----- Aktifkan UTF-8 sebelum python dijalankan -----
chcp 65001 >nul

rem ----- Cek Python tersedia -----
where python >nul 2>nul
if errorlevel 1 goto :no_python

rem ----- Aktivasi virtual environment jika ada -----
if exist ".venv\Scripts\activate.bat" call ".venv\Scripts\activate.bat"
if exist "venv\Scripts\activate.bat" call "venv\Scripts\activate.bat"

echo [INFO] Perintah: python api_notulen.py %ARGS%
echo [INFO] Tekan Ctrl+C di jendela ini untuk menghentikan server.
echo.
python api_notulen.py %ARGS%
set "EXITCODE=%ERRORLEVEL%"
goto :selesai

:no_api
echo [ERROR] api_notulen.py tidak ditemukan di: %CD%
pause
exit /b 1

:no_python
echo [ERROR] Python tidak ditemukan di PATH.
echo Instal Python 3.9-3.13 dari https://python.org lalu coba lagi.
pause
exit /b 1

:selesai
echo.
if not "%EXITCODE%"=="0" (
    echo [ERROR] Server berhenti dengan kode %EXITCODE%.
    echo Solusi umum:
    echo   - Jalankan setup.bat agar dependensi lengkap di .venv
    echo   - Salin .env.example menjadi .env dan isi GEMINI_API_KEY
)
pause
exit /b %EXITCODE%
