@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Setup Notulen Rapat Zoom

echo.
echo ==========================================
echo      SETUP NOTULEN RAPAT ZOOM
echo ==========================================
echo.

rem ============================================================
rem  setup.bat - Siapkan environment & dependensi dalam satu kali
rem  jalan. Membuat virtual environment (.venv) lalu menginstal
rem  seluruh isi requirements.txt ke dalamnya.
rem
rem  Penggunaan:
rem    setup.bat                -> buat .venv (jika belum ada) + instal dependensi
rem    setup.bat /penuh         -> buat ulang .venv dari nol lalu instal ulang semua
rem
rem  Catatan: run.bat, run-test.bat, dan run-index.bat otomatis
rem  memakai .venv ini jika ada, jadi cukup dijalankan sekali.
rem ============================================================

set "PENUH=0"
if /i "%~1"=="/penuh" set "PENUH=1"

rem ----- Cek Python tersedia -----
where python >nul 2>nul
if errorlevel 1 goto :no_python
echo [INFO] Python ditemukan.

rem ----- Buat virtual environment jika belum ada -----
if exist ".venv\Scripts\python.exe" (
    echo [INFO] Virtual environment .venv sudah ada - dilewati.
    goto :aktif
)
if "%PENUH%"=="1" (
    echo [INFO] Mode /penuh: menghapus .venv lama lalu buat ulang...
    rmdir /s /q ".venv" >nul 2>nul
)
echo [INFO] Membuat virtual environment .venv ...
python -m venv .venv
if errorlevel 1 goto :venv_gagal

:aktif
call ".venv\Scripts\activate.bat"
if errorlevel 1 goto :no_venv

rem ----- Upgrade pip -----
echo [INFO] Upgrade pip ...
python -m pip install --upgrade pip
if errorlevel 1 echo [INFO] Upgrade pip gagal - lanjut dengan pip yang ada.

rem ----- Instal dependensi -----
echo.
echo [INFO] Menginstal dependensi dari requirements.txt ...
echo        (butuh beberapa menit tergantung koneksi)
echo.
python -m pip install -r requirements.txt
if errorlevel 1 goto :instal_gagal

rem ----- Verifikasi paket kunci -----
echo.
echo [INFO] Verifikasi paket kunci ...
python -c "import faster_whisper, streamlit, yt_dlp, dotenv, scipy, soundfile; print('[OK]  faster-whisper', faster_whisper.__version__); print('[OK]  streamlit     ', streamlit.__version__); print('[OK]  yt-dlp        ', yt_dlp.version.__version__); print('[OK]  dotenv, scipy, soundfile')"
if errorlevel 1 goto :verifikasi_gagal

rem ----- Cek opsional (tidak wajib): ffmpeg & deno -----
where ffmpeg >nul 2>nul
if errorlevel 1 (
    echo [INFO] ffmpeg tidak ditemukan. Tidak wajib ^(faster-whisper membundel PyAV^), tapi disarankan: winget install Gyan.FFmpeg
) else (
    echo [OK]  ffmpeg ditemukan.
)
where deno >nul 2>nul
if errorlevel 1 (
    echo [INFO] deno tidak ditemukan. Opsional, agar unduhan YouTube lebih andal: winget install DenoLand.Deno
) else (
    echo [OK]  deno ditemukan.
)

echo.
echo ==========================================
echo   SETUP SELESAI
echo ==========================================
echo.
echo   Terinstal ke .venv (dipakai otomatis oleh run.bat).
echo   Langkah tersisa sekali saja:
echo     1. Salin .env.example menjadi .env, isi GEMINI_API_KEY
echo     2. Jalankan aplikasi: run.bat  atau  streamlit run streamlit_app.py
echo.

pause
exit /b 0

:no_python
echo [ERROR] Python tidak ditemukan di PATH.
echo Instal Python 3.9-3.13 dari https://python.org lalu coba lagi.
pause
exit /b 1

:venv_gagal
echo [ERROR] Gagal membuat virtual environment.
echo   Coba jalankan manual: python -m venv .venv
pause
exit /b 1

:no_venv
echo [ERROR] Gagal mengaktifkan .venv.
echo Pastikan folder .venv\Scripts\activate.bat ada, lalu jalankan ulang setup.bat.
pause
exit /b 1

:instal_gagal
echo [ERROR] Instalasi dependensi gagal.
echo   - Cek koneksi internet
echo   - Ulangi dengan: setup.bat
echo   - Verifikasi manual: .venv\Scripts\activate.bat lalu pip install -r requirements.txt
pause
exit /b 1

:verifikasi_gagal
echo [ERROR] Ada paket kunci yang gagal diimpor.
echo Lihat pesan di atas; instal ulang paket yang bermasalah manual:
echo   .venv\Scripts\activate.bat lalu pip install -U nama_paket
pause
exit /b 1
