@echo off
rem CATATAN: jangan letakkan `chcp 65001` di bagian atas file. Bug cmd.exe yang dikenal:
rem setelah chcp 65001 (UTF-8), perintah `set /p` membaca stdin yang di-redirect sebagai
rem kosong, sehingga menu interaktif rusak. chcp 65001 dijalankan belakangan di :eksekusi,
rem tepat sebelum python dijalankan, agar output UTF-8 tetap tampil benar.
setlocal EnableDelayedExpansion
cd /d "%~dp0"
title Notulen Rapat Zoom

rem ============================================================
rem  run.bat - Jalankan Notulen Rapat Zoom
rem
rem  Penggunaan:
rem    run.bat                          -> tampilkan menu interaktif
rem    run.bat loopback                 -> audio sistem WASAPI, tanya durasi & engine
rem    run.bat --durasi 300 --judul X   -> teruskan argumen apa pun (tambahkan --engine gemini bila perlu)
rem    run.bat (menu 1, 2, 5)           -> menanyakan engine transkripsi (lokal/gemini)
rem    run.bat (menu 6)                 -> audit anti-halusinasi semua notulen
rem ============================================================
rem
rem  CATATAN: semua blok if DI BAWAH ditulis SATU BARIS. cmd.exe punya quirk:
rem  blok if parenthesized multi-baris yang muncul SETELAH perintah `set /p`
rem  bisa membuat `set /p` membaca input sebagai kosong. Gaya satu baris
rem  menghindari masalah tersebut dan terbukti stabil.

rem ----- Pilih mode -----
if /i "%~1"=="loopback" goto :loopback
if "%~1"=="" goto :menu
set "ARGS=%*"
goto :run

:menu
echo.
echo ==========================================
echo        MENU NOTULEN RAPAT ZOOM
echo ==========================================
echo   1. Rekam dari mikrofon
echo   2. Rekam audio sistem (loopback)
echo   3. Buat index semua notulen
echo   4. Jalankan tes unit
echo   5. Proses file audio yang sudah ada
echo   6. Audit anti-halusinasi notulen
echo.
echo   (Pilihan 1, 2, dan 5 menanyakan engine transkripsi: lokal / gemini)
echo ==========================================
set "PILIHAN="
set /p "PILIHAN=Masukkan pilihan (1-6): "

if "%PILIHAN%"=="1" goto :pilih_engine_mic
if "%PILIHAN%"=="2" goto :loopback
if "%PILIHAN%"=="3" goto :index
if "%PILIHAN%"=="4" goto :tes
if "%PILIHAN%"=="5" goto :audio
if "%PILIHAN%"=="6" goto :audit
if "%PILIHAN%"=="" goto :keluar
echo.
echo [ERROR] Pilihan "%PILIHAN%" tidak valid. Masukkan angka 1-6.
pause
goto :menu

:keluar
echo.
echo [INFO] Pilihan kosong - keluar.
pause
exit /b 0

:pilih_engine_mic
set "COBA_ENGINE=0"
call :pilih_engine
set "ARGS=--engine !ENGINE!"
goto :run

:pilih_engine
set /a "COBA_ENGINE+=1"
if %COBA_ENGINE% GEQ 6 goto :engine_default
rem ----- Tanya engine transkripsi; Enter = default dari konfigurasi.json (jika ada) -----
set "ENGINE_CFG="
for /f "usebackq tokens=2 delims=:,}" %%a in (`findstr /c:"engine" konfigurasi.json 2^>nul`) do set "ENGINE_CFG=%%a"
if defined ENGINE_CFG set "ENGINE_CFG=%ENGINE_CFG: =%"
if defined ENGINE_CFG set "ENGINE_CFG=%ENGINE_CFG:"=%"
if not defined ENGINE_CFG set "ENGINE_CFG=lokal"
echo.
echo   Engine transkripsi:
echo     1. Lokal (faster-whisper) - gratis, offline
echo     2. Gemini (cloud) - super cepat, butuh internet
set "ENGINE="
set /p "ENGINE=Pilih engine (Enter = %ENGINE_CFG%): "
if "%ENGINE%"=="" set "ENGINE=%ENGINE_CFG%"
if /i "%ENGINE%"=="1" set "ENGINE=lokal"
if /i "%ENGINE%"=="2" set "ENGINE=gemini"
if /i "%ENGINE%"=="lokal" goto :eof
if /i "%ENGINE%"=="gemini" goto :eof
echo [ERROR] Pilihan engine tidak valid. Pilih 1, 2, atau Enter.
goto :pilih_engine

:engine_default
echo [INFO] Terlalu banyak percobaan - memakai engine default (%ENGINE_CFG%).
set "ENGINE=%ENGINE_CFG%"
goto :eof

:loopback
rem Default durasi: ambil dari konfigurasi.json jika ada, selain itu 3600
set "DURASI_CFG="
for /f "usebackq tokens=2 delims=:,}" %%a in (`findstr /c:"durasi" konfigurasi.json 2^>nul`) do set "DURASI_CFG=%%a"
if defined DURASI_CFG set "DURASI_CFG=%DURASI_CFG: =%"
if defined DURASI_CFG set "DURASI_CFG=%DURASI_CFG:"=%"
if defined DURASI_CFG set /a "DURASI_CFG=%DURASI_CFG%" 2>nul
if not defined DURASI_CFG set "DURASI_CFG=3600"
set "DURASI="
set /p "DURASI=Durasi perekaman dalam detik (Enter = %DURASI_CFG%; mis. 7200 = 2 jam): "
if "%DURASI%"=="" set "DURASI=%DURASI_CFG%"
set "COBA_ENGINE=0"
call :pilih_engine
set "ARGS=--sumber loopback --durasi %DURASI% --engine %ENGINE%"
goto :run

:audio
set "FILE_AUDIO="
set /p "FILE_AUDIO=Masukkan path file audio (bisa drag file ke jendela ini): "
if "%FILE_AUDIO%"=="" goto :keluar
rem Buang tanda kutip jika path diketik dengan kutip
set "FILE_AUDIO=%FILE_AUDIO:"=%"
if not exist "%FILE_AUDIO%" goto :file_tidak_ada
set "COBA_ENGINE=0"
call :pilih_engine
set "PERINTAH=python zoom_notulen.py --audio "%FILE_AUDIO%" --engine %ENGINE%"
goto :eksekusi

:file_tidak_ada
echo.
echo [ERROR] File tidak ditemukan: %FILE_AUDIO%
pause
goto :menu

:run
set "PERINTAH=python zoom_notulen.py %ARGS%"
goto :eksekusi

:index
set "PERINTAH=python index_notulen.py"
goto :eksekusi

:tes
set "PERINTAH=python -m unittest test_zoom_notulen test_index_notulen test_audit_notulen -v"
goto :eksekusi

:audit
set "PERINTAH=python audit_notulen.py"
goto :eksekusi

:eksekusi
echo.
echo ==========================================
echo    NOTULEN RAPAT ZOOM
echo ==========================================
echo.

rem ----- Aktifkan UTF-8 HANYA di sini (setelah semua set /p selesai) -----
chcp 65001 >nul

rem ----- Cek Python tersedia -----
where python >nul 2>nul
if errorlevel 1 goto :no_python

rem ----- Aktivasi virtual environment jika ada -----
if exist ".venv\Scripts\activate.bat" call ".venv\Scripts\activate.bat"
if exist "venv\Scripts\activate.bat" call "venv\Scripts\activate.bat"

echo [INFO] Perintah: %PERINTAH%
echo.
%PERINTAH%
set "EXITCODE=%ERRORLEVEL%"
goto :selesai

:no_python
echo [ERROR] Python tidak ditemukan di PATH.
echo Instal Python 3.9-3.11 dari https://python.org lalu coba lagi.
pause
exit /b 1

:selesai
echo.
if not "%EXITCODE%"=="0" (
    echo [ERROR] Perintah berhenti dengan kode %EXITCODE%.
    echo Solusi umum:
    echo   - pip install -r requirements.txt
    echo   - Salin .env.example menjadi .env dan isi GEMINI_API_KEY
    echo   - Pastikan ffmpeg terinstal dan masuk PATH
)
pause
exit /b %EXITCODE%
