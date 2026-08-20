"""
Notulen Rapat Zoom - Rekam Audio -> Transkripsi (faster-whisper) -> Notulen (Gemini AI)

Cara pakai:
  python zoom_notulen.py --durasi 3600                    # rekam 1 jam dari mikrofon
  python zoom_notulen.py --durasi 3600 --sumber loopback  # rekam audio sistem (semua suara dari speaker)
  python zoom_notulen.py --list-devices                   # daftar perangkat loopback WASAPI
  python zoom_notulen.py --audio rekaman.wav              # transkripsi file audio yang sudah ada
  python zoom_notulen.py --audio rekaman.wav --engine gemini  # transkripsi cloud via Gemini (super cepat)
  python zoom_notulen.py --durasi 300 --bahasa id         # tentukan durasi & bahasa transkripsi
  python zoom_notulen.py --format md                      # simpan notulen hanya sebagai Markdown
  python zoom_notulen.py --judul "Rapat Mingguan"           # tentukan judul notulen
  python zoom_notulen.py --daftar-peserta "Budi, Siti, Andi"  # sertakan daftar peserta
  python zoom_notulen.py --tujuan-email "budi@mail.com, siti@mail.com"  # kirim notulen via email
  python zoom_notulen.py --abaikan-senyap               # lewati peringatan rekaman sunyi

  Pengaturan default (judul, peserta, email, durasi, dll.) bisa disimpan di
  file 'konfigurasi.json' (lihat konfigurasi.example.json) agar tidak perlu
  mengetik ulang setiap kali. Argumen CLI tetap menang atas konfigurasi.

CATATAN TENTANG AUDIO ZOOM:
  - Default (--sumber mic): merekam dari mikrofon - hanya suara Anda sendiri.
  - --sumber loopback (Windows, butuh pyaudiowpatch): menangkap SEMUA audio yang
    keluar dari speaker, termasuk suara peserta Zoom lain. Tidak perlu fitur rekam
    bawaan Zoom. Pastikan audio rapat tidak di-mute dan output audio aktif.
  - Alternatif: rekam dengan fitur "Record to computer" bawaan Zoom, ekstrak
    audionya menjadi .wav (mis. dengan ffmpeg), lalu jalankan dengan --audio.
"""

import argparse
import json
import os

# Menyembunyikan peringatan symlink dari huggingface_hub di Windows
# yang hanya membuat panik pengguna biasa.
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
import re
import sys
import tempfile
import threading
import time
import wave
from datetime import datetime

import sounddevice as sd
from faster_whisper import WhisperModel
from dotenv import load_dotenv
from google import genai
from google.genai import types


def aktifkan_utf8_stdout():
    """Memaksa output konsol/file ke UTF-8 agar karakter Unicode (mis. Sirilik,
    Korea, emoji dalam transkrip Whisper) tidak memicu UnicodeEncodeError di
    Windows, yang default konsolnya cp1252."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass  # stdin/stdout tidak mendukung reconfigure (mis. di beberapa IDE)

# ==========================================
# 1. KONFIGURASI
# ==========================================
# Memuat variabel dari file .env (API key TIDAK disimpan di source code)
load_dotenv()

GEMINI_API_KEY = (os.environ.get("GEMINI_API_KEY") or "").strip()
if not GEMINI_API_KEY:
    raise SystemExit(
        "\nERROR: GEMINI_API_KEY tidak ditemukan.\n"
        "  1. Salin .env.example menjadi .env\n"
        "  2. Isi GEMINI_API_KEY dengan API key Anda (aistudio.google.com/apikey)\n"
    )

# Model Flash untuk pembuatan notulen: kualitas reasoning bagus untuk ringkasan
# dan action items. Flash-Lite jadi cadangan jika kuota Flash habis.
GEMINI_MODEL = "gemini-3.6-flash"
GEMINI_NOTULEN_CADANGAN = ["gemini-flash-lite-latest", "gemini-flash-latest"]
GEMINI_PERCOBAAN_ULANG = 4  # percobaan ulang otomatis untuk error sementara (503/429)
GEMINI_JEDA_AWAL = 5        # jeda awal (detik) sebelum coba ulang, meningkat eksponensial
# Mode transkripsi cloud (--engine gemini): audio diunggah ke File API Google lalu
# ditranskripsikan verbatim + speaker diarization oleh Gemini. Jauh lebih cepat
# daripada CPU lokal, tapi butuh internet & kuota Gemini.
GEMINI_TRANSKRIPSI_MODEL = "gemini-3.6-flash"  # model multimodal terbaru dengan dukungan audio (gemini-2.5-flash tidak lagi tersedia untuk pengguna baru)
# Cadangan transkripsi cloud: jika model utama kena 429 (kuota harian habis) atau 503
# (lonjakan permintaan) setelah semua percobaan ulang, otomatis beralih ke model
# berikutnya dalam daftar ini agar transkripsi tidak gagal. Kuota tier gratis dihitung
# per model, jadi cadangan memakai kuota yang terpisah.
GEMINI_TRANSKRIPSI_CADANGAN = ["gemini-3.5-flash", "gemini-flash-latest"]
GEMINI_MAKS_DURASI_AUDIO = 8 * 3600  # batas aman durasi audio per request (~8 jam; dokumen resmi: max 9,5 jam)
GEMINI_POTONG_DURASI = 2 * 3600      # audio > 2 jam dipotong otomatis (hindari batas token output)
GEMINI_KOMPRES_FLAC_MIN_MB = 20      # WAV > 20 MB dikompres ke FLAC sementara sebelum upload (lebih cepat)
GEMINI_TUNGGU_FILE_DETIK = 600       # waktu tunggu maksimal file diproses di server Gemini (detik)
# faster-whisper: engine CTranslate2 + kuantisasi INT8 -> hingga ±4x lebih cepat
# daripada openai-whisper di CPU dengan akurasi setara, plus VAD silero untuk
# melewati bagian sunyi. 'large-v3-turbo' (alias 'turbo') memberikan akurasi
# setara large-v3 tapi 3-4x lebih cepat (809M vs 1.55B parameter).
# Download pertama ~3.1 GB, setelahnya di-cache.
WHISPER_MODEL = "large-v3-turbo"
WHISPER_BEAM_SIZE = 3   # beam search; 3 = sweet-spot kecepatan di CPU i3 (5 = default CTranslate2)
WHISPER_VAD = True      # lewati bagian sunyi (silero VAD) -> lebih cepat & hasil lebih rapi
WHISPER_VAD_THOLD = 0.5  # threshold VAD: 0.5 = default (lebih toleran dari 0.35)
SAMPLE_RATE = 16000     # 16 kHz mono = standar Whisper -> file 4x lebih kecil dari 44.1 kHz stereo
KANAL_REKAM = 1         # mono cukup untuk transkripsi ucapan
AMBANG_RMS_SENYAP = 0.005  # di bawah nilai ini, rekaman dianggap hampir sunyi
AMBANG_RMS_SAMPEL_DETIK = 30  # jumlah detik di awal file yang dibaca untuk ukur RMS (hemat RAM)
AMBANG_DURASI_PANJANG = 3600  # di atas nilai ini (detik), tampilkan info durasi panjang
AMBANG_REKAM_MINIMAL = 2   # rekaman lebih pendek dari ini (detik) -> peringatan "terlalu pendek"
PROGRESS_INTERVAL = 5   # interval (detik) untuk menampilkan progress bar perekaman
GEMINI_TEMPERATURE = 0.2  # temperature rendah -> output notulen lebih konsisten & deterministik
# Tombol untuk menghentikan perekaman lebih awal: tekan 'q' (atau Ctrl+C).
# Perekaman berhenti, audio yang sudah terekam TETAP disimpan & langsung diproses.
TOMBOL_HENTI = "q"
TOMBOL_PAUSE = "p"   # tekan 'p' untuk pause/resume perekaman
TOMBOL_RESUME = "r"  # alias: 'r' juga bisa untuk resume (selain 'p')

KONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "konfigurasi.json")
PILIHAN_SUMBER = ("mic", "loopback")
PILIHAN_FORMAT = ("md", "txt", "both")
PILIHAN_ENGINE = ("lokal", "gemini")  # 'lokal' = faster-whisper (offline), 'gemini' = transkripsi cloud
PILIHAN_NOTULEN_MODE = ("single", "multi")  # 'single' = 1-pass langsung, 'multi' = 2-pass (fakta lalu notulen)


def muat_konfigurasi(path=KONFIG_PATH):
    """Memuat pengaturan default rapat dari file JSON. Mengembalikan dict kosong jika tidak ada/rusak."""
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def terapkan_konfigurasi(args, konfig):
    """Nilai akhir opsi: argumen CLI > file konfigurasi > default bawaan."""
    hasil = {
        "durasi": 60,
        "sumber": "mic",
        "format": "both",
        "bahasa": "id",
        "engine": "lokal",
        "model": GEMINI_MODEL,
        "judul": None,
        "daftar_peserta": None,
        "tujuan_email": None,
        "konteks": None,
        "noise_reduce": False,
        "notulen_mode": "multi",
        "subtitle": "none",
        "translate": None,
        "no_vad": False,
        "flac": False,
    }

    # 1. Dari file konfigurasi
    for k in hasil:
        if k in konfig and konfig[k] not in (None, ""):
            hasil[k] = konfig[k]

    # Validasi nilai yang berasal dari konfigurasi
    if not isinstance(hasil["durasi"], int) or hasil["durasi"] <= 0:
        raise SystemExit("\nERROR: 'durasi' di konfigurasi.json harus angka positif.")
    if hasil["sumber"] not in PILIHAN_SUMBER:
        raise SystemExit(
            f"\nERROR: 'sumber' di konfigurasi.json harus salah satu dari {PILIHAN_SUMBER}."
        )
    if hasil["format"] not in PILIHAN_FORMAT:
        raise SystemExit(
            f"\nERROR: 'format' di konfigurasi.json harus salah satu dari {PILIHAN_FORMAT}."
        )
    if hasil["engine"] not in PILIHAN_ENGINE:
        raise SystemExit(
            f"\nERROR: 'engine' di konfigurasi.json harus salah satu dari {PILIHAN_ENGINE}."
        )
    if hasil["notulen_mode"] not in PILIHAN_NOTULEN_MODE:
        raise SystemExit(
            f"\nERROR: 'notulen_mode' di konfigurasi.json harus salah satu dari {PILIHAN_NOTULEN_MODE}."
        )

    # 2. Argumen CLI menang jika diisi eksplisit
    for k in hasil:
        nilai = getattr(args, k, None)
        if nilai is not None:
            hasil[k] = nilai
    return hasil


# ==========================================
# 2. FUNGSI PEREKAM AUDIO
# ==========================================
def peringatan_durasi_panjang(durasi):
    """Info untuk durasi rekam panjang (> 1 jam). Mengembalikan teks atau None."""
    if durasi <= AMBANG_DURASI_PANJANG:
        return None
    ukuran = durasi * SAMPLE_RATE * 2 * KANAL_REKAM / 1e9  # int16 (2 byte), mono
    satuan = "GB"
    if ukuran < 1:
        ukuran *= 1000
        satuan = "MB"
    return (
        "\n!!! INFO: DURASI PEREKAMAN PANJANG !!!\n"
        f"  Durasi: {durasi} detik (~{durasi / 3600:.1f} jam).\n"
        "  - Perekaman aman untuk RAM: audio ditulis bertahap ke disk (streaming).\n"
        f"  - Perkiraan ukuran file WAV: ~{ukuran:.0f} {satuan} (16 kHz mono, mode mic;\n"
        "    loopback mengikuti rate/kanal perangkat dan bisa lebih besar).\n"
        "  - Transkripsi: mode lokal (faster-whisper INT8) sangat cepat; mode cloud\n"
        "    (--engine gemini) memproses di server Google (butuh internet, audio maks ±8 jam).\n"
        "  - Pastikan ruang disk cukup sebelum merekam.\n"
    )


def _deteksi_msvcrt():
    """Mengimpor msvcrt (Windows) dengan aman. Mengembalikan modul atau None."""
    try:
        import msvcrt
        return msvcrt
    except ImportError:
        return None


def _pantau_tombol_henti(berhenti, pause_event=None):
    """Utas latar: menunggu tombol henti ('q') atau pause ('p'/'r') di konsol.

    Hanya aktif di Windows (msvcrt). Di OS lain (tanpa msvcrt) utas berakhir
    seketika - Ctrl+C tetap bisa dipakai sebagai penghenti darurat.

    berhenti: threading.Event - ditandai saat 'q' ditekan
    pause_event: threading.Event - ditandai saat 'p' ditekan (pause), di-clear saat 'r' (resume)
    """
    msvcrt = _deteksi_msvcrt()
    if msvcrt is None:
        return
    while not berhenti.is_set():
        if msvcrt.kbhit():
            tombol = msvcrt.getch().lower()
            if tombol in (TOMBOL_HENTI.encode(), b"\x03"):  # 'q' atau Ctrl+C
                berhenti.set()
                print(f"\n-> Tombol '{TOMBOL_HENTI}' ditekan: menghentikan perekaman...")
                return
            elif pause_event is not None and tombol in (TOMBOL_PAUSE.encode(), TOMBOL_RESUME.encode()):
                if pause_event.is_set():
                    # Sedang pause -> resume
                    pause_event.clear()
                    print(f"\n-> Tombol '{tombol.decode()}': Resume perekaman...", end="", flush=True)
                else:
                    # Sedang rekam -> pause
                    pause_event.set()
                    print(f"\n-> Tombol '{tombol.decode()}': PAUSE perekaman...", end="", flush=True)
        time.sleep(0.05)


def _rekam_berhenti_dengan_pause():
    """Memulai utas pemantau tombol henti DAN pause/resume.

    Mengembalikan tuple (berhenti_event, pause_event).
    """
    berhenti = threading.Event()
    pause = threading.Event()
    utas = threading.Thread(target=_pantau_tombol_henti, args=(berhenti, pause), daemon=True)
    utas.start()
    return berhenti, pause


def _berhenti_ditekan(berhenti):
    """Apakah pengguna meminta berhenti merekam (tombol q di konsol)?"""
    return berhenti is not None and berhenti.is_set()


def _format_durasi(detik):
    """Memformat detik menjadi MM:SS atau HH:MM:SS."""
    jam, sisa = divmod(int(detik), 3600)
    menit, dtk = divmod(sisa, 60)
    if jam:
        return f"{jam:02d}:{menit:02d}:{dtk:02d}"
    return f"{menit:02d}:{dtk:02d}"


def _cetak_progress(terekam, total, sample_rate, bar_lebar=30):
    """Mencetak progress bar perekaman ke konsol (satu baris, overwrite)."""
    persen = min(terekam / total, 1.0) if total > 0 else 0
    terisi = int(bar_lebar * persen)
    bar = "#" * terisi + "-" * (bar_lebar - terisi)
    waktu_rekam = _format_durasi(terekam / sample_rate)
    waktu_total = _format_durasi(total / sample_rate)
    print(f"\r      [{bar}] {waktu_rekam} / {waktu_total} ({persen:.0%})", end="", flush=True)


def rekam_audio(durasi_detik, nama_file):
    """Merekam audio dari perangkat input default selama durasi_detik detik.

    Audio ditulis bertahap ke disk (streaming per chunk) — aman untuk rapat
    panjang karena tidak memuat seluruh rekaman ke RAM. Tekan 'q' (atau Ctrl+C)
    untuk menghentikan lebih awal: file TETAP disimpan dan lanjut diproses.
    Mengembalikan durasi yang benar-benar terekam (detik).
    """
    print(f"\n[1/3] Merekam audio rapat selama {durasi_detik} detik (16 kHz mono)...")
    print(f"      Tekan '{TOMBOL_HENTI}' = stop | '{TOMBOL_PAUSE}' = pause/resume")
    try:
        channels = KANAL_REKAM
        sampwidth = 2  # paInt16
        chunk = 1024
        total_frames = int(durasi_detik * SAMPLE_RATE)
        frame_bytes = sampwidth * channels
        progress_interval = int(PROGRESS_INTERVAL * SAMPLE_RATE)  # cetak progress tiap N detik
        terakhir_progress = 0
        waktu_pause_total = 0  # total waktu dalam mode pause
        waktu_pause_mulai = 0  # kapan pause dimulai

        berhenti, pause_event = _rekam_berhenti_dengan_pause()
        try:
            with sd.RawInputStream(
                samplerate=SAMPLE_RATE,
                blocksize=chunk,
                channels=channels,
                dtype="int16",
            ) as stream:
                with wave.open(nama_file, "wb") as wf:
                    wf.setnchannels(channels)
                    wf.setsampwidth(sampwidth)
                    wf.setframerate(SAMPLE_RATE)
                    total_read = 0
                    while total_read < total_frames:
                        if _berhenti_ditekan(berhenti):
                            break

                        # Handle pause/resume
                        if pause_event.is_set():
                            if waktu_pause_mulai == 0:
                                waktu_pause_mulai = time.time()
                            time.sleep(0.1)  # hemat CPU saat pause
                            continue
                        elif waktu_pause_mulai > 0:
                            # Resume dari pause
                            waktu_pause_total += time.time() - waktu_pause_mulai
                            waktu_pause_mulai = 0

                        try:
                            data, overflowed = stream.read(chunk)
                        except KeyboardInterrupt:
                            print("\n-> Perekaman dihentikan (Ctrl+C). Audio disimpan & diproses.")
                            break
                        frames_diperoleh = len(data) // frame_bytes
                        if frames_diperoleh < chunk:
                            print(
                                f"\n-> Peringatan: {chunk - frames_diperoleh} frame audio hilang "
                                "(buffer overflow). Kualitas rekaman mungkin menurun."
                            )
                        sisa = min(frames_diperoleh, total_frames - total_read)
                        wf.writeframes(data[: sisa * frame_bytes])
                        total_read += frames_diperoleh
                        # Progress bar: cetak setiap PROGRESS_INTERVAL detik
                        if total_read - terakhir_progress >= progress_interval:
                            _cetak_progress(total_read, total_frames, SAMPLE_RATE)
                            terakhir_progress = total_read
                    # Progress akhir (100%)
                    _cetak_progress(total_read, total_frames, SAMPLE_RATE)
                    print()  # newline setelah progress bar
        except KeyboardInterrupt:
            print("\n-> Perekaman dihentikan (Ctrl+C). Audio disimpan & diproses.")
        finally:
            # Matikan utas pemantau tombol - jika tidak, ia terus menelan tombol q
            # selama transkripsi/prompt berikutnya (utas daemon bocor per sesi).
            if berhenti is not None:
                berhenti.set()
    except Exception as e:
        raise SystemExit(f"\nERROR saat merekam audio: {e}") from e
    durasi_aktual = total_read / SAMPLE_RATE
    if durasi_aktual < durasi_detik - 0.5:
        print(
            f"-> Perekaman dihentikan lebih awal setelah {durasi_aktual:.1f} detik. "
            "Audio tetap disimpan & akan diproses."
        )
    print(f"-> Perekaman selesai. File disimpan sebagai: {nama_file}")
    return durasi_aktual


# ==========================================
# 3. FUNGSI PEREKAM LOOPBACK WASAPI (WINDOWS)
# ==========================================
def _impor_pyaudiowpatch():
    """Mengimpor pyaudiowpatch dengan pesan error yang jelas jika belum terinstal."""
    try:
        import pyaudiowpatch as pyaudio
        return pyaudio
    except ImportError:
        raise SystemExit(
            "\npyaudiowpatch belum terinstal. Instal dengan:\n"
            "  pip install pyaudiowpatch\n"
            "(Perpustakaan Windows untuk perekaman WASAPI loopback.)"
        ) from None


def daftar_perangkat_loopback():
    """Mencetak daftar perangkat output WASAPI yang mendukung loopback."""
    pyaudio = _impor_pyaudiowpatch()
    with pyaudio.PyAudio() as p:
        print("\nPerangkat WASAPI loopback yang tersedia:")
        for device in p.get_loopback_device_info_generator():
            print(
                f"  [{device['index']}] {device['name']} "
                f"(ch {device['maxInputChannels']}, {int(device['defaultSampleRate'])} Hz)"
            )
        print("\nGunakan index di atas dengan opsi --perangkat <index>.")


def rekam_audio_loopback(durasi_detik, nama_file, index_perangkat=None):
    """Merekam audio sistem (WASAPI loopback) - menangkap semua suara yang keluar dari speaker."""
    pyaudio = _impor_pyaudiowpatch()

    print(f"\n[1/3] Merekam audio sistem (WASAPI loopback) selama {durasi_detik} detik...")
    print(f"      Tekan '{TOMBOL_HENTI}' (atau Ctrl+C) untuk menghentikan lebih awal.")
    with pyaudio.PyAudio() as p:
        wasapi_info = p.get_host_api_info_by_type(pyaudio.paWASAPI)

        if index_perangkat is not None:
            # Pakai perangkat loopback tertentu (lihat --list-devices)
            device = p.get_device_info_by_index(index_perangkat)
            if not device.get("isLoopbackDevice"):
                raise SystemExit(
                    f"\nERROR: index {index_perangkat} bukan perangkat loopback. Gunakan --list-devices."
                )
        else:
            # Cari pasangan loopback dari perangkat output default
            device = p.get_device_info_by_index(wasapi_info["defaultOutputDevice"])
            if not device.get("isLoopbackDevice"):
                for loopback in p.get_loopback_device_info_generator():
                    if device["name"] in loopback["name"]:
                        device = loopback
                        break
                else:
                    raise SystemExit(
                        "\nERROR: perangkat loopback WASAPI tidak ditemukan. Coba --list-devices."
                    )

        # Rekam di format asli perangkat (native sample rate & kanal),
        # lalu resample ke 16 kHz mono setelah selesai.
        # WASAPI loopback TIDAK selalu mendukung resampling otomatis -
        # banyak driver hanya menerima sample rate asli perangkat.
        channels_rekam = device["maxInputChannels"] or KANAL_REKAM
        sample_rate_rekam = int(device["defaultSampleRate"])
        chunk = 1024
        sudah_resample = False  # menandakan apakah perlu resample di akhir
        print(f"-> Merekam dari: {device['name']}")
        if sample_rate_rekam != SAMPLE_RATE or channels_rekam != KANAL_REKAM:
            print(f"      Format asli: {sample_rate_rekam} Hz, {channels_rekam} ch")
            print(f"      (akan di-resample ke {SAMPLE_RATE} Hz mono setelah perekaman)")
            sudah_resample = True
        else:
            print(f"      Format: {sample_rate_rekam} Hz mono")
        print(f"      Tekan '{TOMBOL_HENTI}' = stop | '{TOMBOL_PAUSE}' = pause/resume")

        try:
            # Rekam di format native perangkat - WASAPI hanya menerima
            # sample rate yang didukung oleh perangkat output.
            stream = p.open(
                format=pyaudio.paInt16,
                channels=channels_rekam,
                rate=sample_rate_rekam,
                frames_per_buffer=chunk,
                input=True,
                input_device_index=device["index"],
            )
        except Exception as e:
            raise SystemExit(
                f"\nERROR: gagal membuka stream loopback dari '{device['name']}'.\n"
                f"Detail: {e}\nPastikan perangkat output aktif dan tidak dipakai aplikasi lain."
            ) from e

        total_frames = int(durasi_detik * sample_rate_rekam)
        frame_bytes = 2 * channels_rekam  # paInt16 = 2 byte per frame per kanal
        total_read = 0
        progress_interval = int(PROGRESS_INTERVAL * sample_rate_rekam)
        terakhir_progress = 0
        waktu_pause_total = 0
        waktu_pause_mulai = 0
        berhenti, pause_event = _rekam_berhenti_dengan_pause()
        try:
            try:
                with wave.open(nama_file, "wb") as wf:
                    wf.setnchannels(channels_rekam)
                    wf.setsampwidth(p.get_sample_size(pyaudio.paInt16))
                    wf.setframerate(sample_rate_rekam)
                    while total_read < total_frames:
                        if _berhenti_ditekan(berhenti):
                            break

                        # Handle pause/resume
                        if pause_event.is_set():
                            if waktu_pause_mulai == 0:
                                waktu_pause_mulai = time.time()
                            time.sleep(0.1)  # hemat CPU saat pause
                            continue
                        elif waktu_pause_mulai > 0:
                            # Resume dari pause
                            waktu_pause_total += time.time() - waktu_pause_mulai
                            waktu_pause_mulai = 0

                        try:
                            data = stream.read(chunk, exception_on_overflow=False)
                        except KeyboardInterrupt:
                            print("\n-> Perekaman dihentikan (Ctrl+C). Audio disimpan & diproses.")
                            break
                        frames_diperoleh = len(data) // frame_bytes
                        if frames_diperoleh < chunk:
                            print(
                                f"\n-> Peringatan: {chunk - frames_diperoleh} frame audio hilang "
                                "(buffer overflow). Kualitas rekaman mungkin menurun."
                            )
                        sisa = min(frames_diperoleh, total_frames - total_read)
                        wf.writeframes(data[: sisa * frame_bytes])
                        total_read += frames_diperoleh
                        # Progress bar: cetak setiap PROGRESS_INTERVAL detik
                        if total_read - terakhir_progress >= progress_interval:
                            _cetak_progress(total_read, total_frames, sample_rate_rekam)
                            terakhir_progress = total_read
                    _cetak_progress(total_read, total_frames, sample_rate_rekam)
                    print()
            except KeyboardInterrupt:
                print("\n-> Perekaman dihentikan (Ctrl+C). Audio disimpan & diproses.")
            except Exception as e:
                raise SystemExit(f"\nERROR saat merekam audio loopback: {e}") from e
        finally:
            # Matikan utas pemantau tombol - jika tidak, ia terus menelan tombol q
            # selama transkripsi/prompt berikutnya (utas daemon bocor per sesi).
            if berhenti is not None:
                berhenti.set()
            stream.stop_stream()
            stream.close()
    durasi_aktual = total_read / sample_rate_rekam
    if durasi_aktual < durasi_detik - 0.5:
        print(
            f"-> Perekaman dihentikan lebih awal setelah {durasi_aktual:.1f} detik. "
            "Audio tetap disimpan & akan diproses."
        )

    # Resample ke 16 kHz mono (standar Whisper) jika format asli berbeda
    if sudah_resample and os.path.isfile(nama_file):
        print(f"-> Resample dari {sample_rate_rekam} Hz {channels_rekam}ch ke {SAMPLE_RATE} Hz mono...")
        jalur_16khz = _resample_ke_16khz(nama_file, sample_rate_rekam, channels_rekam)
        if jalur_16khz and os.path.isfile(jalur_16khz):
            # Ganti file asli dengan hasil resample
            try:
                import shutil
                shutil.move(jalur_16khz, nama_file)
            except Exception as e:
                print(f"-> Peringatan: gagal mengganti file ({e}). File asli tetap dipakai.")
                # Hapus file resample yang gagal dipindah
                try:
                    os.remove(jalur_16khz)
                except OSError:
                    pass
            else:
                print(f"-> Resample selesai: {nama_file}")
        else:
            print(f"-> Resample gagal, menggunakan file asli ({sample_rate_rekam} Hz {channels_rekam}ch)")

    print(f"-> Perekaman selesai. File disimpan sebagai: {nama_file}")
    return durasi_aktual


def _resample_ke_16khz(nama_file, rate_asli, kanal_asli):
    """Mengonversi audio ke 16 kHz mono (standar Whisper) -> file lebih kecil, proses lebih cepat.

    Mengembalikan path file WAV baru, atau None jika gagal.
    """
    try:
        import soundfile as sf
        import numpy as np

        data, _ = sf.read(nama_file, dtype="float32")
        # Stereo/multi-kanal -> mono (rata-rata semua kanal)
        if len(data.shape) > 1 and data.shape[1] > 1:
            data = data.mean(axis=1)
        # Resample ke 16 kHz dengan anti-aliasing filter
        if rate_asli != SAMPLE_RATE:
            try:
                from scipy.signal import resample_poly
                from math import gcd
                # resample_poly menerapkan low-pass filter sebelum downsampling
                # -> mencegah aliasing yang bisa merusak akurasi transkripsi
                faktor = gcd(rate_asli, SAMPLE_RATE)
                data = resample_poly(data, SAMPLE_RATE // faktor, rate_asli // faktor)
            except ImportError:
                # Fallback ke interpolasi linear jika scipy tidak terinstal
                jumlah_baru = int(len(data) * SAMPLE_RATE / rate_asli)
                indeks_asli = np.arange(len(data))
                indeks_baru = np.linspace(0, len(data) - 1, jumlah_baru)
                data = np.interp(indeks_baru, indeks_asli, data)
        # Simpan sebagai WAV 16-bit
        data_int16 = (data * 32767).clip(-32768, 32767).astype(np.int16)
        fd, jalur = tempfile.mkstemp(suffix="_16khz.wav")
        os.close(fd)
        with wave.open(jalur, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(data_int16.tobytes())
        return jalur
    except Exception as e:
        print(f"-> Peringatan: gagal resample audio ({e}). Menggunakan file asli.")
        return None


def kompres_wav_ke_flac(nama_file, jalur_output=None):
    """Mengompres file WAV ke FLAC (lossless compression) secara chunked.

    Membaca audio per chunk (1M frames) agar tidak memuat seluruh file ke RAM
    untuk file besar (mis. 1+ GB dari loopback). FLAC mengurangi ukuran ~50-60%.
    jalur_output: path file FLAC output (default: same directory as input, ganti ekstensi).
    Mengembalikan path file FLAC, atau None jika gagal.
    """
    try:
        import soundfile as sf
    except ImportError:
        print("-> Peringatan: library 'soundfile' belum terinstal. Lewati kompresi FLAC.")
        return None

    if not nama_file.lower().endswith(".wav"):
        print("-> Peringatan: file bukan WAV. Lewati kompresi FLAC.")
        return None

    if jalur_output is None:
        jalur_output = nama_file[:-4] + ".flac"
    ukuran_wav = os.path.getsize(nama_file)

    print(f"-> Mengompres WAV ke FLAC...")
    try:
        CHUNK_FRAMES = 1024 * 1024  # ~1M frames per chunk
        with sf.SoundFile(nama_file, "r") as src:
            with sf.SoundFile(
                jalur_output, "w", samplerate=src.samplerate,
                channels=src.channels, format="FLAC", subtype="PCM_16"
            ) as dst:
                while True:
                    data = src.read(CHUNK_FRAMES, dtype="int16")
                    if len(data) == 0:
                        break
                    dst.write(data)
        ukuran_flac = os.path.getsize(jalur_output)
        pengurangan = (1 - ukuran_flac / ukuran_wav) * 100
        print(f"   WAV: {ukuran_wav / 1024 / 1024:.1f} MB -> FLAC: {ukuran_flac / 1024 / 1024:.1f} MB ({pengurangan:.0%} lebih kecil)")
        return jalur_output
    except Exception as e:
        print(f"-> Peringatan: gagal kompresi FLAC ({e}). Menggunakan file WAV.")
        return None


# ==========================================
# 3b. FUNGSI PENGURANGAN NOISE (OPSIONAL)
# ==========================================
def kurangi_noise(nama_file):
    """Mengurangi noise latar (AC, keyboard, latar belakang) dari file audio.

    Menggunakan library noisereduce untuk spectral gating. Memproses audio
    per chunk (30 detik) agar hemat RAM untuk file besar.
    Mengembalikan path file WAV bersih (temp), atau None jika gagal/library tidak ada.
    """
    try:
        import noisereduce as nr
        import soundfile as sf
    except ImportError:
        print(
            "-> Peringatan: library 'noisereduce' belum terinstal. Lewati noise reduction.\n"
            "   Instal dengan: pip install noisereduce"
        )
        return None

    print("-> Mengurangi noise latar belakang audio...")
    try:
        info = sf.info(nama_file)
        rate = info.samplerate
        channels = info.channels
        CHUNK_DETIK = 30  # proses per 30 detik agar hemat RAM
        chunk_frames = int(CHUNK_DETIK * rate)

        fd, jalur = tempfile.mkstemp(suffix="_clean.wav")
        os.close(fd)

        with sf.SoundFile(nama_file, "r") as src:
            with sf.SoundFile(
                jalur, "w", samplerate=rate, channels=channels,
                format="WAV", subtype="PCM_16"
            ) as dst:
                while True:
                    data = src.read(chunk_frames, dtype="float32")
                    if len(data) == 0:
                        break
                    # Mono atau multi-channel: noisereduce menangani keduanya
                    if len(data.shape) > 1 and data.shape[1] > 1:
                        # Proses per kanal lalu gabung kembali
                        import numpy as np
                        cleaned = np.column_stack([
                            nr.reduce_noise(y=data[:, ch], sr=rate, prop_decrease=0.7)
                            for ch in range(data.shape[1])
                        ])
                    else:
                        cleaned = nr.reduce_noise(y=data, sr=rate, prop_decrease=0.7)
                    dst.write(cleaned)
        print(f"-> Noise reduction selesai: {jalur}")
        return jalur
    except Exception as e:
        print(f"-> Peringatan: gagal mengurangi noise ({e}). Menggunakan audio asli.")
        return None


# ==========================================
# 3c. FUNGSI DOWNLOAD AUDIO DARI YOUTUBE
# ==========================================
def unduh_audio_youtube(url, nama_file=None):
    """Mengunduh audio dari URL YouTube dan mengonversi ke WAV.

    Menggunakan yt-dlp untuk download + konversi otomatis ke WAV 16kHz mono.
    Mengembalikan path file WAV yang diunduh, atau None jika gagal.
    """
    try:
        import yt_dlp
    except ImportError:
        raise SystemExit(
            "\nyt-dlp belum terinstal. Instal dengan:\n"
            "  pip install yt-dlp\n"
            "(Library untuk download audio/video dari YouTube dan situs lain.)"
        ) from None

    print(f"\n-> Mengunduh audio dari YouTube...")
    print(f"   URL: {url}")

    if nama_file is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        DIR_REKAMAN = "Hasil_Rekaman"
        os.makedirs(DIR_REKAMAN, exist_ok=True)
        nama_file = os.path.join(DIR_REKAMAN, f"youtube_{timestamp}.wav")

    # Opsi yt-dlp: download audio, konversi ke WAV, sample rate 16kHz, mono
    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": nama_file.replace(".wav", ".%(ext)s"),
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "wav",
            "preferredquality": "192",
        }],
        "postprocessor_args": [
            "-ar", "16000",  # sample rate 16 kHz (standar Whisper)
            "-ac", "1",     # mono
        ],
        "quiet": False,
        "no_warnings": False,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            judul = info.get("title", "YouTube Audio")
            durasi = info.get("duration", 0)
            print(f"   Judul: {judul}")
            if durasi:
                print(f"   Durasi: {_format_durasi(durasi)}")
    except yt_dlp.utils.DownloadError as e:
        print(f"\n-> Gagal mengunduh dari YouTube: {e}")
        print("   Pastikan URL valid dan yt-dlp ter-update (pip install -U yt-dlp).")
        return None
    except Exception as e:
        print(f"\n-> Gagal mengunduh dari YouTube: {e}")
        return None

    # yt-dlp mungkin menghasilkan file dengan nama sedikit berbeda
    # Cari file WAV yang dihasilkan
    if not os.path.isfile(nama_file):
        # Coba cari file dengan ekstensi lain yang mungkin dihasilkan
        base = nama_file.replace(".wav", "")
        for ext in [".wav", ".webm", ".m4a", ".mp3"]:
            kandidat = base + ext
            if os.path.isfile(kandidat):
                if ext != ".wav":
                    # Konversi ke WAV jika perlu
                    print(f"-> Mengonversi {ext} ke WAV...")
                    try:
                        import subprocess
                        subprocess.run(
                            ["ffmpeg", "-y", "-i", kandidat, "-ar", "16000", "-ac", "1", nama_file],
                            check=True,
                            capture_output=True,
                        )
                        os.remove(kandidat)
                    except (subprocess.CalledProcessError, FileNotFoundError) as e:
                        print(f"-> Gagal konversi ke WAV: {e}")
                        # Jika ffmpeg tidak ada, coba pakai file asli
                        if ext in (".m4a", ".mp3"):
                            return kandidat
                        return None
                break
        else:
            print("-> File audio tidak ditemukan setelah download.")
            return None

    print(f"-> Audio tersimpan: {nama_file}")
    return nama_file


# ==========================================
# 3d. FUNGSI EKSTRAKSI AUDIO DARI VIDEO
# ==========================================
# Format video yang didukung
FORMAT_VIDEO = (".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv", ".wmv", ".m4v")
# Format audio yang didukung
FORMAT_AUDIO = (".wav", ".mp3", ".m4a", ".ogg", ".flac", ".aac", ".wma")


def ekstrak_audio_dari_video(jalur_video, jalur_output=None):
    """Mengekstrak audio dari file video dan mengonversi ke WAV 16kHz mono.

    Menggunakan ffmpeg (via subprocess atau yt-dlp untuk konversi).
    Mengembalikan path file WAV, atau None jika gagal.
    """
    import subprocess

    if jalur_output is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        DIR_REKAMAN = "Hasil_Rekaman"
        os.makedirs(DIR_REKAMAN, exist_ok=True)
        nama_base = os.path.splitext(os.path.basename(jalur_video))[0]
        jalur_output = os.path.join(DIR_REKAMAN, f"{nama_base}_{timestamp}.wav")

    print(f"-> Mengekstrak audio dari video: {os.path.basename(jalur_video)}")

    # Coba ffmpeg langsung
    try:
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", jalur_video,
                "-vn",                  # skip video
                "-ar", "16000",          # sample rate 16 kHz
                "-ac", "1",              # mono
                "-acodec", "pcm_s16le",  # WAV PCM 16-bit
                jalur_output
            ],
            check=True,
            capture_output=True,
        )
        print(f"-> Audio terekstrak: {jalur_output}")
        return jalur_output
    except FileNotFoundError:
        print("-> ffmpeg tidak ditemukan. Mencoba via yt-dlp...")
    except subprocess.CalledProcessError as e:
        print(f"-> ffmpeg gagal: {e.stderr.decode('utf-8', errors='ignore')[:200]}")
        return None

    # Fallback: gunakan yt-dlp untuk ekstrak audio dari video
    try:
        import yt_dlp
        ydl_opts = {
            "format": "bestaudio/best",
            "outtmpl": jalur_output.replace(".wav", ".%(ext)s"),
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "wav",
                "preferredquality": "192",
            }],
            "postprocessor_args": ["-ar", "16000", "-ac", "1"],
            "quiet": True,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([jalur_video])
        if os.path.isfile(jalur_output):
            print(f"-> Audio terekstrak (via yt-dlp): {jalur_output}")
            return jalur_output
    except ImportError:
        pass
    except Exception as e:
        print(f"-> yt-dlp gagal: {e}")

    print("-> Gagal mengekstrak audio. Pastikan ffmpeg terinstal atau file video valid.")
    return None


def adalah_file_audio_video(jalur):
    """Mengecek apakah file adalah audio/video yang didukung."""
    ext = os.path.splitext(jalur)[1].lower()
    return ext in (FORMAT_VIDEO + FORMAT_AUDIO)


def _cetak_progress_batch(saat_ini, total, nama_file, bar_lebar=30):
    """Mencetak progress bar untuk batch processing."""
    persen = min(saat_ini / total, 1.0) if total > 0 else 0
    terisi = int(bar_lebar * persen)
    bar = "#" * terisi + "-" * (bar_lebar - terisi)
    # Potong nama file jika terlalu panjang
    nama_pendek = nama_file if len(nama_file) <= 30 else nama_file[:27] + "..."
    print(f"\r      [{bar}] {saat_ini}/{total} ({persen:.0%}) {nama_pendek:<33}", end="", flush=True)


def proses_folder(jalur_folder, cfg, engine="lokal", bahasa="id"):
    """Memproses semua file audio/video di folder secara batch.

    Mengembalikan list tuple (nama_file, transkrip_teks, notulen_teks) untuk setiap file.
    """
    if not os.path.isdir(jalur_folder):
        raise SystemExit(f"\nERROR: folder tidak ditemukan: {jalur_folder}")

    # Cari semua file audio/video di folder
    files = []
    for f in sorted(os.listdir(jalur_folder)):
        jalur = os.path.join(jalur_folder, f)
        if os.path.isfile(jalur) and adalah_file_audio_video(jalur):
            files.append(jalur)

    if not files:
        raise SystemExit(
            f"\nERROR: tidak ditemukan file audio/video di folder: {jalur_folder}\n"
            f"   Format yang didukung: {', '.join(FORMAT_VIDEO + FORMAT_AUDIO)}"
        )

    total_file = len(files)
    print(f"\n{'='*60}")
    print(f"  BATCH PROCESSING: {total_file} file ditemukan")
    print(f"  Folder: {jalur_folder}")
    print(f"  Engine: {engine} | Bahasa: {bahasa}")
    print(f"{'='*60}")
    print("\nDaftar file:")
    for i, f in enumerate(files, 1):
        ukuran = os.path.getsize(f) / 1024  # KB
        print(f"  {i:2d}. {os.path.basename(f):<40} ({ukuran:.0f} KB)")
    print()

    hasil = []
    DIR_NOTULEN = "Hasil_Notulen"
    DIR_TRANSKRIP = "Hasil_Transkripsi"
    os.makedirs(DIR_NOTULEN, exist_ok=True)
    os.makedirs(DIR_TRANSKRIP, exist_ok=True)

    # Statistik
    sukses = 0
    dilewati = 0
    gagal = 0

    for i, jalur_file in enumerate(files, 1):
        nama_file = os.path.basename(jalur_file)
        print(f"\n{'='*60}")
        _cetak_progress_batch(i, total_file, nama_file)
        print()  # newline setelah progress bar
        print(f"  File {i}/{total_file}: {nama_file}")
        print(f"{'='*60}")

        # Ekstrak audio dari video jika perlu
        ext = os.path.splitext(jalur_file)[1].lower()
        if ext in FORMAT_VIDEO:
            print("  -> Mengekstrak audio dari video...")
            audio_file = ekstrak_audio_dari_video(jalur_file)
            if not audio_file:
                print(f"  -> SKIP: gagal mengekstrak audio dari {nama_file}")
                dilewati += 1
                continue
        else:
            audio_file = jalur_file

        # Deteksi sunyi
        rms = ukur_rms(audio_file)
        if rms is not None and rms < AMBANG_RMS_SENYAP:
            print(f"  -> SKIP: file hampir sunyi (RMS: {rms:.4f})")
            if ext in FORMAT_VIDEO and audio_file != jalur_file:
                _hapus_file(audio_file)
            dilewati += 1
            continue

        # Transkripsi
        print("  -> Transkripsi...")
        try:
            transkrip_teks = transkripsi_audio(audio_file, bahasa, engine,
                                               konteks=cfg.get("konteks"))
        except Exception as e:
            print(f"  -> SKIP: transkripsi gagal ({e})")
            if ext in FORMAT_VIDEO and audio_file != jalur_file:
                _hapus_file(audio_file)
            gagal += 1
            continue

        if not transkrip_teks or not transkrip_teks.strip():
            print("  -> SKIP: transkripsi kosong")
            if ext in FORMAT_VIDEO and audio_file != jalur_file:
                _hapus_file(audio_file)
            dilewati += 1
            continue

        # Simpan transkrip
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        nama_tanpa_ext = os.path.splitext(nama_file)[0]
        nama_transkrip = os.path.join(DIR_TRANSKRIP, f"Transkrip_{nama_tanpa_ext}_{timestamp}.txt")
        simpan_transkrip(transkrip_teks, nama_transkrip)
        print(f"  -> Transkrip tersimpan: {os.path.basename(nama_transkrip)}")

        # Generate subtitle jika diminta
        if cfg.get("subtitle", "none") != "none":
            DIR_SUBTITLE = "Hasil_Subtitle"
            os.makedirs(DIR_SUBTITLE, exist_ok=True)
            nama_subtitle = os.path.join(DIR_SUBTITLE, f"subtitle_{nama_tanpa_ext}_{timestamp}")
            simpan_subtitle(transkrip_teks, nama_subtitle, cfg["subtitle"])

        # Translate transkrip jika diminta
        if cfg.get("translate"):
            DIR_TRANSLATE = "Hasil_Translate"
            os.makedirs(DIR_TRANSLATE, exist_ok=True)
            teks_translate = translate_transkrip(
                transkrip_teks, cfg["translate"], cfg.get("model", GEMINI_MODEL)
            )
            nama_translate = os.path.join(DIR_TRANSLATE, f"Transkrip_{nama_tanpa_ext}_{timestamp}")
            simpan_translate(teks_translate, nama_translate, cfg["translate"])

        # Buat notulen
        print("  -> Membuat notulen...")
        try:
            info_rapat = {
                "tanggal": datetime.now().strftime("%d %B %Y"),
                "waktu": datetime.now().strftime("%H:%M WIB"),
            }
            notulen = _buat_notulen_sesuai_mode(
                cfg, transkrip_teks, cfg.get("daftar_peserta"), info_rapat=info_rapat
            )
            nama_notulen = os.path.join(DIR_NOTULEN, f"Notulen_{nama_tanpa_ext}_{timestamp}")
            simpan_notulen(notulen, nama_notulen, cfg.get("format", "both"))

            # Export ke PDF jika diminta
            if cfg.get("pdf", False):
                DIR_PDF = "Hasil_PDF"
                os.makedirs(DIR_PDF, exist_ok=True)
                nama_pdf = os.path.join(DIR_PDF, f"Notulen_{nama_tanpa_ext}_{timestamp}.pdf")
                export_pdf(notulen, nama_pdf, nama_tanpa_ext)

            hasil.append((nama_file, transkrip_teks, notulen))
            sukses += 1
            print(f"  -> Berhasil: {nama_file}")
        except Exception as e:
            print(f"  -> Gagal buat notulen: {e}")
            hasil.append((nama_file, transkrip_teks, None))
            gagal += 1

        # Hapus file audio sementara jika dari video
        if ext in FORMAT_VIDEO and audio_file != jalur_file:
            _hapus_file(audio_file)

    # Ringkasan akhir
    print(f"\n{'='*60}")
    print(f"  RINGKASAN BATCH PROCESSING")
    print(f"{'='*60}")
    print(f"  Total file   : {total_file}")
    print(f"  Berhasil     : {sukses} [OK]")
    print(f"  Dilewati     : {dilewati} [SKIP] (sunyi/tidak ada audio)")
    print(f"  Gagal        : {gagal} [FAIL]")
    print(f"  Output       : {DIR_NOTULEN}/, {DIR_TRANSKRIP}/")
    print(f"{'='*60}")

    return hasil


# ==========================================
# 4. FUNGSI TRANSKRIPSI (LOKAL & CLOUD)
# ==========================================
def ukur_rms(nama_file):
    """Menghitung RMS (kekuatan audio) dari sampel awal file WAV.

    Hanya membaca AMBANG_RMS_SAMPEL_DETIK detik pertama agar hemat RAM
    untuk file besar (mis. 1+ GB dari loopback). Mengembalikan None jika gagal.
    """
    try:
        import numpy as np

        with wave.open(nama_file, "rb") as wf:
            sampwidth = wf.getsampwidth()
            framerate = wf.getframerate()
            nchannels = wf.getnchannels()
            total_frames = wf.getnframes()
            # Baca hanya N detik pertama (hemat RAM untuk file >1 GB)
            frames_baca = min(total_frames, int(AMBANG_RMS_SAMPEL_DETIK * framerate))
            frame_bytes = wf.readframes(frames_baca)

        if sampwidth == 2:
            dtype, pembagi = "<i2", 32768.0
        elif sampwidth == 4:
            dtype, pembagi = "<i4", 2147483648.0
        elif sampwidth == 1:
            dtype, pembagi = "u1", 128.0
        else:
            return None

        data = np.frombuffer(frame_bytes, dtype=dtype).astype(np.float64) / pembagi
        if data.size == 0:
            return 0.0
        return float(np.sqrt((data**2).mean()))
    except Exception:
        return None


def transkripsi_audio(nama_file, bahasa, engine="lokal", konteks=None, no_vad=False):
    """Dispatcher transkripsi: memilih engine 'lokal' (faster-whisper, offline)
    atau 'gemini' (transkripsi cloud via Gemini AI - super cepat).

    Hasil keduanya berupa teks ber-timestamp [MM:SS] per segmen (Gemini juga
    menambahkan speaker diarization).

    konteks: teks konteks/istilah domain untuk meningkatkan akurasi (opsional).
    no_vad: jika True, nonaktifkan VAD filter (untuk audio yang tidak terpotong).
    """
    if engine == "gemini":
        return transkripsi_audio_gemini(nama_file, bahasa, konteks=konteks)
    return transkripsi_audio_lokal(nama_file, bahasa, konteks=konteks, no_vad=no_vad)


# ── Singleton cache untuk WhisperModel ──
# Model large-v3-turbo (~3.1 GB) memakan 30-60 detik untuk loading ke RAM.
# Dengan cache, model hanya di-load sekali dan dipakai ulang untuk semua
# transkripsi berikutnya (terutama penting untuk batch processing).
_whisper_cache = {"model": None, "nama": None}


def _dapatkan_whisper_model(nama_model=WHISPER_MODEL):
    """Mengembalikan WhisperModel yang di-cache (singleton). Load hanya sekali."""
    if _whisper_cache["model"] is None or _whisper_cache["nama"] != nama_model:
        print(f"      (memuat model '{nama_model}' ke RAM — proses pertama kali mungkin lambat)")
        _whisper_cache["model"] = WhisperModel(nama_model, device="cpu", compute_type="int8")
        _whisper_cache["nama"] = nama_model
    else:
        print(f"      (model '{nama_model}' sudah di-cache — skip loading)")
    return _whisper_cache["model"]


def transkripsi_audio_lokal(nama_file, bahasa, konteks=None, no_vad=False):
    """Mengubah file audio menjadi teks menggunakan faster-whisper (CTranslate2 + INT8).

    Jauh lebih cepat daripada openai-whisper (hingga ±4x di CPU) berkat engine
    CTranslate2 dengan kuantisasi INT8, plus VAD silero untuk melewati bagian
    sunyi. Hasil berupa teks ber-timestamp [MM:SS] per segmen.

    bahasa='auto' -> deteksi bahasa otomatis (cocok untuk rapat campuran,
    mis. Indonesia-Inggris). Kode bahasa lain (mis. 'id', 'en') memaksa bahasa.

    konteks: teks berisi istilah domain (mis. 'Kemenkes, Permenpan, SJT') agar
    Whisper lebih akurat mengenali istilah teknis yang jarang.
    """
    label = bahasa if bahasa != "auto" else "auto (deteksi otomatis)"
    print(
        f"\n[2/3] Mengubah audio menjadi teks (faster-whisper '{WHISPER_MODEL}' INT8, "
        f"bahasa '{label}')..."
    )
    print("      (model CTranslate2 diunduh otomatis saat pertama kali dipakai)")
    if konteks:
        print(f"      (konteks domain: {konteks[:80]}{'...' if len(konteks) > 80 else ''})")
    # Tentukan apakah VAD aktif atau tidak
    vad_aktif = WHISPER_VAD and not no_vad
    if no_vad:
        print("      ⚠️ VAD dinonaktifkan (--no-vad). Semua bagian audio akan diproses.")

    try:
        model = _dapatkan_whisper_model(WHISPER_MODEL)
        opsi_transkrip = {
            "language": bahasa if bahasa != "auto" else None,
            "beam_size": WHISPER_BEAM_SIZE,
            "vad_filter": vad_aktif,
        }
        # VAD filter parameters: threshold lebih tinggi = lebih toleran
        # (tidak terlalu agresif menghapus suara)
        if vad_aktif:
            opsi_transkrip["vad_parameters"] = {
                "threshold": WHISPER_VAD_THOLD,  # 0.5 = lebih toleran
                "min_speech_duration_ms": 250,    # min 250ms dianggap suara
                "min_silence_duration_ms": 500,   # min 500ms dianggap sunyi
                "speech_pad_ms": 200,             # padding 200ms sebelum/sesudah suara
            }
        # initial_prompt memberikan konteks domain ke Whisper agar istilah teknis
        # (mis. singkatan, nama organisasi, jargon) lebih akurat dikenali.
        if konteks:
            opsi_transkrip["initial_prompt"] = konteks
            # hot-words: meningkatkan probabilitas kata-kata tertentu di Whisper
            # (space-separated). Sangat membantu untuk istilah teknis yang jarang.
            opsi_transkrip["hotwords"] = konteks
        print(f"      VAD: {'aktif (threshold=' + str(WHISPER_VAD_THOLD) + ')' if vad_aktif else 'nonaktif'}")
        segmen, info = model.transcribe(nama_file, **opsi_transkrip)
        segmen = list(segmen)  # generator lazy -> jalankan seluruh inferensi
    except Exception as e:
        raise SystemExit(f"\nERROR saat transkripsi: {e}") from e

    if not segmen:
        raise SystemExit(
            "\nTranskripsi kosong - audio tidak terdengar atau file rusak.\n"
            "Cek file audio Anda atau pastikan perangkat input benar."
        )
    if bahasa == "auto":
        print(f"-> Bahasa terdeteksi: {info.language or '?'}")
    print(f"-> Durasi audio: {info.duration:.1f} detik, {len(segmen)} segmen terdeteksi")

    baris = []
    for s in segmen:
        teks_seg = (s.text or "").strip()
        if not teks_seg:
            continue
        detik = int(s.start)
        jam, sisa = divmod(detik, 3600)
        menit, dtk = divmod(sisa, 60)
        stempel = f"{jam:02d}:{menit:02d}:{dtk:02d}" if jam else f"{menit:02d}:{dtk:02d}"
        baris.append(f"[{stempel}] {teks_seg}")
    if not baris:
        raise SystemExit(
            "\nTranskripsi kosong - audio tidak terdengar atau file rusak.\n"
            "Cek file audio Anda atau pastikan perangkat input benar."
        )
    print("-> Transkripsi selesai.")
    return "\n".join(baris)


def _durasi_audio(nama_file):
    """Durasi audio (detik) menggunakan soundfile. Mengembalikan None jika gagal."""
    try:
        import soundfile as sf
        return float(sf.info(nama_file).duration)
    except Exception:
        return None


def _kompres_ke_flac(nama_file):
    """Mengompres WAV menjadi FLAC sementara (jauh lebih kecil -> upload lebih cepat).

    Mengembalikan path file FLAC temp, atau None jika gagal.
    """
    fd, jalur = tempfile.mkstemp(suffix=".flac")
    os.close(fd)
    hasil = kompres_wav_ke_flac(nama_file, jalur_output=jalur)
    if hasil:
        return hasil
    # Bersihkan file kosong jika gagal
    try:
        os.remove(jalur)
    except OSError:
        pass
    return None


def _potong_audio(nama_file, mulai_detik, durasi_detik):
    """Memotong audio menjadi WAV sementara [mulai, mulai+durasi) detik.

    Mengembalikan path file WAV temp, atau None jika gagal.
    """
    try:
        import soundfile as sf
        data, rate = sf.read(
            nama_file, start=int(mulai_detik), frames=int(durasi_detik), dtype="int16"
        )
        if data.size == 0:
            return None
        fd, jalur = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        sf.write(jalur, data, rate, format="WAV", subtype="PCM_16")
        return jalur
    except Exception:
        return None


def _hapus_file(jalur):
    """Menghapus file temp dengan aman (abaikan error)."""
    if jalur and os.path.isfile(jalur):
        try:
            os.remove(jalur)
        except OSError:
            pass


def _prompt_transkripsi(bahasa, konteks=None):
    """Prompt transkripsi verbatim + timestamp + speaker diarization untuk Gemini."""
    if bahasa == "auto":
        instruksi_bahasa = "- Deteksi bahasa yang digunakan dalam audio secara otomatis.\n"
    else:
        instruksi_bahasa = f"- Bahasa audio: '{bahasa}'.\n"
    instruksi_konteks = ""
    if konteks:
        instruksi_konteks = (
            f"- KONTEKS DOMAIN (istilah/singkatan penting yang mungkin muncul di audio): {konteks}\n"
            "  Gunakan ejaan istilah di atas jika terdengar di audio.\n"
        )
    return (
        "Anda adalah transkriber profesional. Transkripsikan audio rapat ini SECARA VERBATIM "
        "(kata per kata, tanpa menghilangkan apa pun, tanpa meringkas).\n"
        f"{instruksi_bahasa}"
        f"{instruksi_konteks}"
        "- Satu baris per segmen pembicaraan.\n"
        "- Awali SETIAP baris dengan timestamp [MM:SS] (atau [HH:MM:SS] jika lebih dari 1 jam).\n"
        "- Lakukan speaker diarization: beri label 'Pembicara 1', 'Pembicara 2', dst. saat "
        "pembicara berganti.\n"
        "- Format tiap baris: [MM:SS] Pembicara N: teks yang diucapkan\n"
        "- Keluarkan HANYA baris transkrip. Tanpa pendahuluan, tanpa penjelasan, tanpa blok kode."
    )


POLA_TIMESTAMP_GEMINI = re.compile(r"^\[(\d{1,2}):(\d{2})(?::(\d{2}))?\]")


def _parsing_transkrip_gemini(teks, offset=0):
    """Mengubah respons Gemini menjadi daftar baris [MM:SS]/[HH:MM:SS] + teks.

    Membuang baris non-transkrip (pembukaan, blok kode) dan menggeser timestamp
    sebesar offset (untuk audio yang dipotong-potong). Jika Gemini tidak memakai
    format timestamp sama sekali, teks mentah dikembalikan sebagai cadangan agar
    hasil transkripsi tidak hilang.
    """
    baris = []
    for b in teks.splitlines():
        b = b.strip()
        if not b or b.startswith("```"):
            continue
        baris.append(b)
    ber_stempel = []
    for b in baris:
        cocok = POLA_TIMESTAMP_GEMINI.match(b)
        if not cocok:
            continue
        if cocok.group(3) is not None:
            # Format [HH:MM:SS]
            detik = int(cocok.group(1)) * 3600 + int(cocok.group(2)) * 60 + int(cocok.group(3))
        else:
            # Format [MM:SS]
            detik = int(cocok.group(1)) * 60 + int(cocok.group(2))
        detik += offset
        jam, sisa = divmod(detik, 3600)
        menit, dtk = divmod(sisa, 60)
        stempel = f"{jam:02d}:{menit:02d}:{dtk:02d}" if jam else f"{menit:02d}:{dtk:02d}"
        ber_stempel.append(f"[{stempel}]{b[cocok.end():]}")
    return ber_stempel if ber_stempel else baris


def _unggah_dan_tunggu(client, jalur):
    """Mengunggah file ke File API Gemini lalu menunggu hingga siap (state ACTIVE)."""
    print(f"-> Mengunggah audio ke Gemini ({os.path.getsize(jalur) / 1e6:.1f} MB)...")
    berkas = client.files.upload(file=jalur)
    batas_waktu = time.time() + GEMINI_TUNGGU_FILE_DETIK
    while True:
        status = client.files.get(name=berkas.name)
        keadaan = getattr(status.state, "name", str(status.state))
        if keadaan == "ACTIVE":
            return status
        if keadaan == "FAILED":
            raise SystemExit("\nERROR: pemrosesan file audio gagal di server Gemini.")
        if time.time() > batas_waktu:
            raise SystemExit(
                "\nERROR: waktu tunggu pemrosesan file audio di server Gemini habis (10 menit)."
            )
        print("-> Menunggu audio siap diproses di server...")
        time.sleep(3)


def _transkripsi_gemini_satu(client, nama_file, bahasa, offset=0, konteks=None):
    """Transkripsi satu file audio via Gemini: kompres -> unggah -> transkripsi -> parsing."""
    jalur_upload = nama_file
    jalur_flac = None
    nama_berkas = None
    try:
        if (
            nama_file.lower().endswith(".wav")
            and os.path.getsize(nama_file) > GEMINI_KOMPRES_FLAC_MIN_MB * 1024 * 1024
        ):
            print("-> Mengompres WAV ke FLAC sementara (upload lebih cepat)...")
            jalur_flac = _kompres_ke_flac(nama_file)
            if jalur_flac:
                jalur_upload = jalur_flac
        try:
            berkas = _unggah_dan_tunggu(client, jalur_upload)
        except SystemExit:
            raise
        except Exception as e:
            raise SystemExit(
                f"\nERROR saat mengunggah audio ke Gemini: {e}\n"
                "Periksa koneksi internet dan GEMINI_API_KEY Anda."
            ) from e
        nama_berkas = berkas.name
        prompt = _prompt_transkripsi(bahasa, konteks=konteks)
        response = _panggil_generate_dengan_retry(
            client,
            GEMINI_TRANSKRIPSI_MODEL,
            contents=[berkas, prompt],
            model_cadangan=GEMINI_TRANSKRIPSI_CADANGAN,
        )
        teks = (getattr(response, "text", "") or "").strip()
        if not teks:
            raise SystemExit("\nGemini mengembalikan transkripsi kosong. Silakan coba lagi.")
        baris = _parsing_transkrip_gemini(teks, offset)
        if not baris:
            raise SystemExit(
                "\nTranskripsi kosong - audio tidak terdengar atau file rusak.\n"
                "Cek file audio Anda atau pastikan perangkat input benar."
            )
        return "\n".join(baris)
    finally:
        # Bersihkan file remote & temp di SEMUA jalur (termasuk gagal upload/polling)
        if nama_berkas:
            try:
                client.files.delete(name=nama_berkas)  # otomatis terhapus juga setelah 48 jam
            except Exception:
                pass
        _hapus_file(jalur_flac)


def transkripsi_audio_gemini(nama_file, bahasa, konteks=None):
    """Transkripsi cloud via Gemini AI: unggah audio -> transkrip verbatim + timestamp + pembicara.

    Jauh lebih cepat daripada transkripsi lokal karena diproses di server Google.
    Membutuhkan internet & kuota Gemini. Audio > 2 jam dipotong otomatis agar
    hasil tidak terpotong oleh batas token output.

    konteks: teks berisi istilah domain untuk meningkatkan akurasi (opsional).
    """
    label = bahasa if bahasa != "auto" else "auto (deteksi otomatis)"
    print(
        f"\n[2/3] Mengubah audio menjadi teks (Gemini cloud '{GEMINI_TRANSKRIPSI_MODEL}', "
        f"bahasa '{label}')..."
    )
    print("      (audio diunggah ke server Google - butuh internet)")
    if konteks:
        print(f"      (konteks domain: {konteks[:80]}{'...' if len(konteks) > 80 else ''})")
    if GEMINI_TRANSKRIPSI_CADANGAN:
        print(
            f"      (cadangan otomatis jika kuota/lonjakan: {', '.join(GEMINI_TRANSKRIPSI_CADANGAN)})"
        )

    durasi = _durasi_audio(nama_file)
    if durasi is None:
        durasi = 0
    if durasi > GEMINI_MAKS_DURASI_AUDIO:
        raise SystemExit(
            f"\nERROR: durasi audio {durasi / 3600:.1f} jam melebihi batas Gemini "
            f"({GEMINI_MAKS_DURASI_AUDIO / 3600:.0f} jam per request).\n"
            "Potong audio menjadi beberapa bagian lalu proses satu per satu."
        )
    if durasi > GEMINI_POTONG_DURASI:
        print(
            f"-> Audio {durasi / 3600:.1f} jam: dipotong otomatis per "
            f"{GEMINI_POTONG_DURASI // 3600} jam agar hasil tidak terpotong."
        )

    client = genai.Client(api_key=GEMINI_API_KEY)

    if durasi > GEMINI_POTONG_DURASI:
        # Audio panjang: transkripsi bertahap per potongan untuk menghindari batas token output
        semua_baris = []
        mulai = 0
        while mulai < durasi:
            panjang = min(GEMINI_POTONG_DURASI, durasi - mulai)
            jalur_potongan = _potong_audio(nama_file, mulai, panjang)
            if jalur_potongan is None:
                raise SystemExit(
                    "\nERROR: gagal memotong audio. Pastikan format didukung (WAV/FLAC/OGG) "
                    "atau konversi dulu dengan ffmpeg."
                )
            try:
                teks = _transkripsi_gemini_satu(client, jalur_potongan, bahasa, offset=mulai, konteks=konteks)
                if teks:
                    semua_baris.append(teks)
            finally:
                _hapus_file(jalur_potongan)
            mulai += GEMINI_POTONG_DURASI
        if not semua_baris:
            raise SystemExit(
                "\nTranskripsi kosong - audio tidak terdengar atau file rusak.\n"
                "Cek file audio Anda atau pastikan perangkat input benar."
            )
        hasil = "\n".join(semua_baris)
    else:
        hasil = _transkripsi_gemini_satu(client, nama_file, bahasa, konteks=konteks)
    print("-> Transkripsi selesai.")
    return hasil


# ==========================================
# 5. FUNGSI PEMBUATAN NOTULEN (GEMINI API)
# ==========================================
def _error_transien(e):
    """Apakah error Gemini layak dicoba ulang (503 sibuk / 429 rate limit)?"""
    kode = getattr(e, "code", None)
    pesan = str(e)
    return (
        kode in (429, 503)
        or "UNAVAILABLE" in pesan
        or "RESOURCE_EXHAUSTED" in pesan
        or "rate limit" in pesan.lower()
    )


def _panggil_generate_dengan_retry(client, model, contents, config=None, model_cadangan=None):
    """Memanggil generate_content dengan retry otomatis untuk error sementara (503/429).

    Jika `model_cadangan` diberikan (daftar model), dan error sementara tidak kunjung
    hilang setelah semua percobaan ulang, otomatis beralih ke model cadangan berikutnya
    (kuota tier gratis dihitung per model, jadi cadangan memakai kuota terpisah).
    Dipakai bersama oleh pembuatan notulen dan transkripsi cloud Gemini.
    """
    daftar_model = [model] + list(model_cadangan or [])
    error_terakhir = None
    for i, mdl in enumerate(daftar_model):
        for percobaan in range(1, GEMINI_PERCOBAAN_ULANG + 1):
            try:
                if config is not None:
                    return client.models.generate_content(
                        model=mdl, contents=contents, config=config
                    )
                return client.models.generate_content(model=mdl, contents=contents)
            except Exception as e:
                if not _error_transien(e):
                    raise SystemExit(f"\nERROR saat memanggil Gemini API: {e}") from e
                error_terakhir = e
                if percobaan < GEMINI_PERCOBAAN_ULANG:
                    tunggu = GEMINI_JEDA_AWAL * (2 ** (percobaan - 1))
                    print(
                        f"-> Gemini sedang sibuk (error sementara). Mencoba lagi dalam {tunggu} "
                        f"detik (percobaan {percobaan}/{GEMINI_PERCOBAAN_ULANG})..."
                    )
                    time.sleep(tunggu)
                    continue
                # Percobaan terakhir untuk model ini gagal: coba model cadangan (jika ada)
                if i < len(daftar_model) - 1:
                    print(
                        f"-> Kuota/lonjakan model '{mdl}' tidak kunjung membaik. "
                        f"Beralih ke model cadangan '{daftar_model[i + 1]}'..."
                    )
                break  # keluar dari loop percobaan model ini
    raise SystemExit(
        f"\nERROR: semua model transkripsi gagal (kuota/lonjakan). "
        f"Error terakhir: {error_terakhir}"
    ) from error_terakhir


def buat_notulen(teks_transkrip, judul=None, daftar_peserta=None, model=GEMINI_MODEL,
                 info_rapat=None):
    """Mengubah transkrip menjadi notulen rapat terstruktur via Gemini AI.

    info_rapat: dict opsional berisi 'tanggal', 'waktu', 'durasi' untuk metadata notulen.
    """
    print(f"\n[3/3] Memproses notulen rapat dengan Gemini AI ({model})...")
    client = genai.Client(api_key=GEMINI_API_KEY)

    if judul:
        instruksi_judul = f'- Judul yang WAJIB dipakai: "# Notulen Rapat - {judul}"'
    else:
        instruksi_judul = '- Judul: "# Notulen Rapat - <judul/topik rapat>" (tentukan topik dari transkrip)'

    if daftar_peserta:
        # Daftar Peserta hanya INFO tambahan dari penyelenggara (bukan dari transkrip).
        # Nama di sini TIDAK otomatis berarti orang itu berbicara atau mendapat tugas.
        instruksi_peserta = (
            f'- "## Daftar Peserta" (bullet list, hanya nama-nama ini): {daftar_peserta}\n'
            '- Daftar Peserta hanya untuk referensi - JANGAN berasumsi siapa berbicara dari daftar ini\n'
        )
    else:
        instruksi_peserta = ""

    # Metadata rapat (tanggal, waktu, durasi) — dicantumkan jika tersedia
    instruksi_metadata = ""
    if info_rapat:
        meta_parts = []
        if info_rapat.get("tanggal"):
            meta_parts.append(f"Tanggal: {info_rapat['tanggal']}")
        if info_rapat.get("waktu"):
            meta_parts.append(f"Waktu: {info_rapat['waktu']}")
        if info_rapat.get("durasi"):
            meta_parts.append(f"Durasi: {info_rapat['durasi']}")
        if meta_parts:
            instruksi_metadata = (
                '- "## Informasi Rapat" (sebelum Ringkasan Eksekutif, berisi: '
                + ", ".join(meta_parts) + ")\n"
            )

    prompt = f"""Buatkan notulen rapat yang rapi dan terstruktur berdasarkan transkrip berikut.

Tulis hasilnya dalam format MARKDOWN dengan heading rapi, dengan struktur berikut:
{instruksi_judul}
{instruksi_peserta}{instruksi_metadata}- "## Ringkasan Eksekutif" (2-3 kalimat, sertakan angka kunci jika ada)
- "## Poin-Poin Utama Diskusi" (pakai bullet list, PERTAHANKAN angka-angka kunci: target vs realisasi, persentase, jumlah yang disebut di transkrip)
- "## Keputusan yang Diambil" (pakai nomor atau bullet)
- "## Action Items / Tindak Lanjut" (pakai daftar atau tabel Markdown)

ATURAN WAJIB (ANTI-HALUSINASI):
- JANGAN PERNAH menciptakan nama pembicara. Transkrip memakai label 'Pembicara N' dan/atau nama asli - gunakan PERSIS apa yang tertulis di transkrip.
- Nama asli boleh ditulis hanya jika nama itu BENAR-BENAR muncul di transkrip.
- Jika transkrip hanya memakai 'Pembicara N', tulis PIC/penanggung jawab Action Item sebagai 'Pembicara N' (mis. 'Pembicara 2') - JANGAN menggantinya dengan nama orang lain yang tidak disebut di transkrip.
- JANGAN membuat/menebak fakta, angka, keputusan, atau penugasan yang tidak ada di transkrip.
- JANGAN menambahkan nama orang pada Action Items kecuali nama itu muncul di transkrip DAN jelas bertanggung jawab atas tindakan itu (bukan sekadar disebut-sebut).

TRANSKRIP:
{teks_transkrip}
"""

    response = _panggil_generate_dengan_retry(
        client,
        model,
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=GEMINI_TEMPERATURE,
            system_instruction=(
                "Anda adalah seorang sekretaris profesional yang bertugas membuat "
                "notulen rapat yang rapi, lengkap, dan terstruktur.\n"
                "ATURAN WAJIB: JANGAN PERNAH menciptakan nama pembicara atau fakta yang tidak "
                "ada di transkrip. Gunakan persis label/nama yang tertulis di transkrip "
                "(mis. 'Pembicara N'). Action Item hanya boleh ditugaskan ke nama yang "
                "benar-benar muncul di transkrip dan jelas bertanggung jawab."
            )
        ),
        model_cadangan=GEMINI_NOTULEN_CADANGAN,
    )
    if not response.text or not response.text.strip():
        raise SystemExit("\nGemini mengembalikan notulen kosong. Silakan coba lagi.")
    return response.text


def buat_notulen_multipass(teks_transkrip, judul=None, daftar_peserta=None,
                           model=GEMINI_MODEL, info_rapat=None):
    """Membuat notulen rapat 2-tahap (multi-pass) untuk mengurangi halusinasi.

    Pass 1 - Ekstraksi Fakta: Gemini mengekstrak fakta terstruktur (topik, keputusan,
             action items, angka kunci) dari transkrip sebagai teks terstruktur.
    Pass 2 - Penulisan Notulen: Dari fakta terstruktur, susun notulen Markdown rapi.

    Keuntungan: setiap fakta bisa di-trace ke transkrip, mengurangi halusinasi nama/angka.
    info_rapat: dict opsional berisi 'tanggal', 'waktu', 'durasi' untuk metadata notulen.
    """
    print(f"\n[3/3] Memproses notulen rapat dengan Gemini AI ({model}) — mode multi-pass...")
    client = genai.Client(api_key=GEMINI_API_KEY)

    # ── Pass 1: Ekstraksi Fakta Terstruktur ──
    print("      Pass 1/2: Mengekstrak fakta dari transkrip...")
    prompt_fakta = f"""Anda adalah analis rapat yang teliti. Dari transkrip berikut, ekstrak SEMUA fakta penting secara terstruktur. JANGAN mengarang fakta yang tidak ada di transkrip.

ATURAN KETAT:
- Hanya gunakan nama pembicara yang BENAR-BENAR tertulis di transkrip (mis. 'Pembicara 1', 'Pembicara 2', atau nama asli jika disebut).
- JANGAN menebak atau menciptakan nama orang. Jika tidak disebutkan, tulis 'Pembicara N'.
- Pertahankan SEMUA angka, persentase, target, realisasi persis seperti di transkrip.

Format output (teks terstruktur):

TOPIK DISKUSI:
1. [topik] — [ringkasan singkat, sertakan angka kunci]
2. ...

KEPUTUSAN:
1. [keputusan yang diambil]
2. ...

ACTION ITEMS:
1. PIC: [nama PERSIS dari transkrip] | Tugas: [deskripsi] | Tenggat: [jika disebutkan, atau '-']
2. ...

ANGKA KUNCI:
- [deskripsi]: [angka/persentase dari transkrip]

NAMA PEMBICARA YANG TERIDENTIFIKASI:
- [daftar nama/label pembicara yang muncul di transkrip]

TRANSKRIP:
{teks_transkrip}
"""

    resp_fakta = _panggil_generate_dengan_retry(
        client,
        model,
        contents=prompt_fakta,
        config=types.GenerateContentConfig(
            temperature=0.1,  # sangat rendah: ekstraksi fakta harus presisi
            system_instruction=(
                "Anda adalah analis rapat yang sangat teliti. Tugas Anda HANYA mengekstrak "
                "fakta yang ada di transkrip. JANGAN PERNAH menambah, menebak, atau "
                "menciptakan informasi yang tidak ada di transkrip."
            )
        ),
        model_cadangan=GEMINI_NOTULEN_CADANGAN,
    )
    if not resp_fakta.text or not resp_fakta.text.strip():
        print("-> Pass 1 gagal (kosong). Fallback ke mode single-pass...")
        return buat_notulen(teks_transkrip, judul, daftar_peserta, model, info_rapat)

    fakta = resp_fakta.text
    print(f"      Pass 1 selesai: {len(fakta)} karakter fakta diekstrak.")

    # ── Pass 2: Susun Notulen Dari Fakta ──
    print("      Pass 2/2: Menyusun notulen dari fakta terstruktur...")
    if judul:
        instruksi_judul = f'- Judul yang WAJIB dipakai: "# Notulen Rapat - {judul}"'
    else:
        instruksi_judul = '- Judul: "# Notulen Rapat - <judul/topik rapat>" (tentukan topik dari fakta)'

    if daftar_peserta:
        instruksi_peserta = (
            f'- "## Daftar Peserta" (bullet list, hanya nama-nama ini): {daftar_peserta}\n'
            '- Daftar Peserta hanya untuk referensi - JANGAN berasumsi siapa berbicara dari daftar ini\n'
        )
    else:
        instruksi_peserta = ""

    instruksi_metadata = ""
    if info_rapat:
        meta_parts = []
        if info_rapat.get("tanggal"):
            meta_parts.append(f"Tanggal: {info_rapat['tanggal']}")
        if info_rapat.get("waktu"):
            meta_parts.append(f"Waktu: {info_rapat['waktu']}")
        if info_rapat.get("durasi"):
            meta_parts.append(f"Durasi: {info_rapat['durasi']}")
        if meta_parts:
            instruksi_metadata = (
                '- "## Informasi Rapat" (sebelum Ringkasan Eksekutif, berisi: '
                + ", ".join(meta_parts) + ")\n"
            )

    prompt_notulen = f"""Susun notulen rapat yang rapi dan terstruktur berdasarkan FAKTA TERSTRUKTUR berikut.

PENTING: Fakta di bawah sudah diekstrak dari transkrip asli. Gunakan HANYA fakta ini — JANGAN menambah informasi baru.

Tulis hasilnya dalam format MARKDOWN dengan heading rapi, dengan struktur berikut:
{instruksi_judul}
{instruksi_peserta}{instruksi_metadata}- "## Ringkasan Eksekutif" (2-3 kalimat, sertakan angka kunci jika ada)
- "## Poin-Poin Utama Diskusi" (pakai bullet list, PERTAHANKAN angka-angka kunci)
- "## Keputusan yang Diambil" (pakai nomor atau bullet)
- "## Action Items / Tindak Lanjut" (pakai tabel Markdown: No | PIC | Tindak Lanjut | Tenggat Waktu)

ATURAN WAJIB (ANTI-HALUSINASI):
- Gunakan HANYA nama pembicara/PIC yang ada di FAKTA TERSTRUKTUR.
- JANGAN menambahkan nama orang yang tidak ada di fakta.
- JANGAN membuat angka, keputusan, atau penugasan baru.

FAKTA TERSTRUKTUR:
{fakta}
"""

    resp_notulen = _panggil_generate_dengan_retry(
        client,
        model,
        contents=prompt_notulen,
        config=types.GenerateContentConfig(
            temperature=GEMINI_TEMPERATURE,
            system_instruction=(
                "Anda adalah seorang sekretaris profesional yang bertugas menyusun "
                "notulen rapat dari fakta yang sudah diekstrak.\n"
                "ATURAN WAJIB: JANGAN PERNAH menciptakan nama pembicara atau fakta baru. "
                "Gunakan HANYA informasi dari FAKTA TERSTRUKTUR yang diberikan."
            )
        ),
        model_cadangan=GEMINI_NOTULEN_CADANGAN,
    )
    if not resp_notulen.text or not resp_notulen.text.strip():
        print("-> Pass 2 gagal (kosong). Fallback ke mode single-pass...")
        return buat_notulen(teks_transkrip, judul, daftar_peserta, model, info_rapat)

    print("      Multi-pass selesai.")
    return resp_notulen.text


POLA_HEADING_MD = re.compile(r"^#{1,6}\s+.*$", re.MULTILINE)
POLA_LABEL_PEMBICARA = re.compile(r"^pembicara\s+\d+$", re.IGNORECASE)
# Nama tim (mis. 'Tim Keuangan / Pemasaran', 'Tim Terkait / Teknis') BUKAN nama orang -
# menugaskan tugas ke tim adalah wajar dan tidak perlu dianggap halusinasi.
# (Tradeoff: orang bernama persis 'Tim' juga terlewati - jarang dalam konteks rapat.)
POLA_NAMA_TIM = re.compile(r"^tim\b", re.IGNORECASE)
# Kata peran generik yang bukan nama orang (PIC berupa peran, bukan orang tertentu)
POLA_PERAN_GENERIK = re.compile(
    r"^(peserta|divisi|bagian|sekretariat|pihak terkait|tim terkait)$", re.IGNORECASE
)
# Nama kolom header tabel yang berisi PIC/penanggung jawab (dicocokkan utuh, case-insensitive).
# Daftar eksplisit karena frasa multi-kata (mis. 'penanggung jawab') tidak boleh dipecah.
NAMA_KOLOM_PIC = frozenset({
    "pic", "pic (action by)", "action by", "penanggung jawab",
    "pic (penanggung jawab)", "pic / siapa", "pic/siapa", "pic (siapa)",
    "siapa", "nama", "pic / penanggung jawab", "pic (penanggung jawab) / siapa",
    "siapa (nama)", "nama (pic)", "pic / siapa (nama)", "pic (siapa) / nama",
})
# Kata header tabel Action Items yang BUKAN entri data (untuk melewati baris header)
KATA_HEADER_TABEL = NAMA_KOLOM_PIC | frozenset({
    "no", "tugas", "tugas / tindak lanjut", "tindakan", "tindakan (task)",
    "tindak lanjut", "tindak lanjut / apa yang dilakukan", "tenggat waktu",
    "tenggat waktu (deadline)", "deadline", "batas waktu", "status",
    "status / catatan", "catatan", "keterangan", "tenggat waktu / keterangan",
})
# Kata kerja umum yang memulai frasa tugas (bukan nama) pada bullet Action Item,
# mis. '- Menyiapkan laporan - disetujui' -> segmen pertama BUKAN nama PIC.
KATA_KERJA_AWAL_TUGAS = frozenset(
    "menyiapkan menyusun memproses menindaklanjuti membuat melakukan melaksanakan "
    "mempersiapkan koordinasi berkoordinasi membahas menyampaikan mengirim "
    "mengumpulkan menyetujui melaporkan memantau mengevaluasi menyediakan "
    "memastikan menghubungi mengoordinasikan menindaklanjuti".split()
)


def _ekstrak_bagian_action_items(teks_notulen):
    """Mengambil bagian 'Action Items / Tindak Lanjut' dari notulen Markdown.

    Dimulai dari heading yang memuat 'Action Items' atau 'Tindak Lanjut' hingga
    heading berikutnya (atau akhir teks). Mengembalikan teks bagian, atau None
    jika heading tidak ditemukan.
    """
    cocok = re.search(
        r"^#{1,6}\s+.*?(Action Items|Tindak Lanjut).*$",
        teks_notulen,
        re.IGNORECASE | re.MULTILINE,
    )
    if not cocok:
        return None
    mulai = cocok.start()
    akhir = len(teks_notulen)
    for h in POLA_HEADING_MD.finditer(teks_notulen, mulai + 1):
        akhir = h.start()
        break
    return teks_notulen[mulai:akhir]


def _indeks_kolom_pic(baris):
    """Untuk baris header tabel Action Items, kembalikan index kolom PIC (atau None)."""
    if not baris.startswith("|"):
        return None
    sel = [s.strip() for s in baris.strip("|").split("|")]
    for i, s in enumerate(sel):
        # Abaikan markdown * / _ pada header (mis. '*PIC*') sebelum mencocokkan
        bersih = s.strip("*_ ").lower()
        if bersih in NAMA_KOLOM_PIC:
            return i
    return None


def _nama_pic_dari_baris(baris, kolom_pic=1):
    """Mengekstrak calon nama PIC/penanggung jawab dari satu baris Action Item.

    Mendukung format tabel (kolom PIC dideteksi dari header; default kolom ke-2
    untuk format '| No | PIC | ... |') dan bullet ('- Nama: tugas' / '- Nama - tugas').
    Mengembalikan nama (str) atau None jika baris bukan entri Action Item.
    """
    baris = baris.strip()
    if not baris:
        return None
    if baris.startswith("|"):
        sel = [s.strip() for s in baris.strip("|").split("|")]
        # Baris pemisah tabel (hanya '-', ':', spasi)
        if sel and all(re.fullmatch(r"[\-: ]+", s) for s in sel if s):
            return None
        if len(sel) > kolom_pic:
            pic = sel[kolom_pic].strip("*_ ")
            if pic.lower() in KATA_HEADER_TABEL:
                return None  # baris header tabel
            if re.fullmatch(r"[\-\u2013\u2014:.]+", pic):
                return None  # sel kosong/placeholder (mis. '-') bukan nama
            return pic or None
        return None
    # Bullet / baris teks: hilangkan awalan bullet
    for awalan in ("-", "*"):
        if baris.startswith(awalan):
            baris = baris[len(awalan):].strip()
            break
    for pemisah in (":", "-", "\u2013"):  # titik dua, strip, en-dash
        if pemisah in baris:
            calon = baris.split(pemisah, 1)[0].strip()
            if calon and len(calon.split()) <= 4:
                # Hindari false positive: frasa tugas tanpa nama PIC pada bullet,
                # mis. '- Menyiapkan laporan - disetujui' (bukan 'Nama - tugas')
                if pemisah != ":" and calon.split()[0].lower() in KATA_KERJA_AWAL_TUGAS:
                    return None
                return calon
    return None


def periksa_halusinasi_notulen(teks_notulen, teks_transkrip):
    """Memvalidasi nama PIC di Action Items notulen terhadap isi transkrip.

    Mengembalikan daftar nama yang muncul sebagai PIC/penanggung jawab Action Item
    di notulen tetapi TIDAK ditemukan di transkrip - indikasi halusinasi Gemini
    (nama dikarang dari Daftar Peserta, padahal transkrip hanya memakai label
    'Pembicara N'). Label 'Pembicara N' dianggap aman dan tidak dilaporkan.

    Contoh: notulen menulis 'Budi' padahal transkrip hanya berisi 'Pembicara 1'
    -> mengembalikan ['Budi'].
    """
    bagian = _ekstrak_bagian_action_items(teks_notulen)
    if not bagian:
        return []
    mencurigakan = []
    transkrip_rendah = (teks_transkrip or "").lower()
    kolom_pic = 1  # format default '| No | PIC | ... |'
    # Deteksi header tabel untuk format 2 kolom '| PIC | Tindak Lanjut |' atau lainnya
    for baris in bagian.splitlines():
        idx = _indeks_kolom_pic(baris)
        if idx is not None:
            kolom_pic = idx
            break
    for baris in bagian.splitlines():
        nama = _nama_pic_dari_baris(baris, kolom_pic)
        if not nama:
            continue
        # Aman: label 'Pembicara N', nama tim ('Tim X'), atau peran generik ('Peserta')
        if (
            POLA_LABEL_PEMBICARA.match(nama)
            or POLA_NAMA_TIM.match(nama)
            or POLA_PERAN_GENERIK.match(nama)
        ):
            continue
        # Nama harus muncul sebagai kata utuh di transkrip (batas kata)
        if not re.search(
            r"\b" + re.escape(nama.lower()) + r"\b", transkrip_rendah
        ):
            mencurigakan.append(nama)
    # Buang duplikat (PIC sama di beberapa baris Action Item) dengan menjaga urutan
    terlihat, unik = set(), []
    for n in mencurigakan:
        if n not in terlihat:
            terlihat.add(n)
            unik.append(n)
    return unik


# ==========================================
# 6. FUNGSI PENYIMPANAN HASIL (NOTULEN & TRANSKRIP)
# ==========================================
def simpan_transkrip(teks, nama_file):
    """Menyimpan transkrip mentah ke file teks (UTF-8)."""
    with open(nama_file, "w", encoding="utf-8") as f:
        f.write(teks)
    print(f"\n-> Transkrip mentah disimpan ke '{nama_file}'")


def simpan_notulen(teks, nama_dasar, format_file):
    """Menyimpan notulen ke file sesuai format yang diminta: 'md', 'txt', atau 'both'."""
    disimpan = []
    if format_file in ("md", "both"):
        nama = f"{nama_dasar}.md"
        with open(nama, "w", encoding="utf-8") as f:
            f.write(teks)
        disimpan.append(nama)
    if format_file in ("txt", "both"):
        nama = f"{nama_dasar}.txt"
        with open(nama, "w", encoding="utf-8") as f:
            f.write(teks)
        disimpan.append(nama)
    print(f"\nNotulen berhasil disimpan ke: {', '.join(disimpan)}")


# ==========================================
# 6b. FUNGSI SUBTITLE GENERATOR (.srt / .vtt)
# ==========================================
POLA_TIMESTAMP_SUBTITLE = re.compile(r"^\[(\d{1,2}):(\d{2})(?::(\d{2}))?\]\s*(.*)")


def _parse_transkrip_ke_segments(teks_transkrip):
    """Mengubah teks transkrip [MM:SS] menjadi list dict {start, end, text}.

    Format transkrip:
      [00:05] Halo semuanya
      [01:30:15] Pembicara 1: Selamat pagi

    Mengembalikan list of dict: [{"start": 5.0, "end": 10.0, "text": "Halo semuanya"}, ...]
    """
    segments = []
    waktu_sebelumnya = 0.0

    for baris in teks_transkrip.splitlines():
        baris = baris.strip()
        if not baris:
            continue

        match = POLA_TIMESTAMP_SUBTITLE.match(baris)
        if not match:
            # Baris tanpa timestamp, gabung dengan segmen sebelumnya
            if segments:
                segments[-1]["text"] += " " + baris
            continue

        teks = match.group(4).strip()

        # Format: [MM:SS] atau [HH:MM:SS]
        if match.group(3) is not None:
            # [HH:MM:SS]
            jam = int(match.group(1))
            menit = int(match.group(2))
            detik = int(match.group(3))
        else:
            # [MM:SS]
            jam = 0
            menit = int(match.group(1))
            detik = int(match.group(2))

        start = jam * 3600 + menit * 60 + detik

        # Set end segmen sebelumnya ke waktu mulai segmen ini
        if segments:
            segments[-1]["end"] = start

        # Estimasi durasi segmen: min 2 detik, max 10 detik
        # (berdasarkan panjang teks)
        estimasi_durasi = max(2.0, min(10.0, len(teks) * 0.08))
        end = start + estimasi_durasi

        segments.append({
            "start": start,
            "end": end,
            "text": teks,
        })
        waktu_sebelumnya = start

    # Update end segmen terakhir
    if segments:
        segments[-1]["end"] = segments[-1]["start"] + 3.0  # default 3 detik

    return segments


def _detik_ke_srt_timestamp(detik):
    """Mengkonversi detik ke format SRT: HH:MM:SS,mmm"""
    jam = int(detik // 3600)
    menit = int((detik % 3600) // 60)
    det = int(detik % 60)
    mili = int((detik % 1) * 1000)
    return f"{jam:02d}:{menit:02d}:{det:02d},{mili:03d}"


def _detik_ke_vtt_timestamp(detik):
    """Mengkonversi detik ke format VTT: HH:MM:SS.mmm"""
    jam = int(detik // 3600)
    menit = int((detik % 3600) // 60)
    det = int(detik % 60)
    mili = int((detik % 1) * 1000)
    return f"{jam:02d}:{menit:02d}:{det:02d}.{mili:03d}"


def generate_srt(teks_transkrip, nama_file=None):
    """Mengubah transkrip [MM:SS] menjadi file subtitle .srt (SubRip).

    Format SRT yang dihasilkan:
      1
      00:00:05,000 --> 00:00:10,000
      Halo semuanya

      2
      00:01:30,000 --> 00:01:35,000
      Pembicara 1: Selamat pagi

    Mengembalikan path file .srt yang dihasilkan.
    """
    if nama_file is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        DIR_SUBTITLE = "Hasil_Subtitle"
        os.makedirs(DIR_SUBTITLE, exist_ok=True)
        nama_file = os.path.join(DIR_SUBTITLE, f"subtitle_{timestamp}.srt")

    segments = _parse_transkrip_ke_segments(teks_transkrip)
    if not segments:
        print("-> Peringatan: tidak ada segmen untuk dibuat subtitle.")
        return None

    baris = []
    for i, seg in enumerate(segments, 1):
        start_str = _detik_ke_srt_timestamp(seg["start"])
        end_str = _detik_ke_srt_timestamp(seg["end"])
        baris.append(str(i))
        baris.append(f"{start_str} --> {end_str}")
        baris.append(seg["text"])
        baris.append("")  # blank line separator

    with open(nama_file, "w", encoding="utf-8") as f:
        f.write("\n".join(baris))

    print(f"-> Subtitle SRT disimpan: {nama_file} ({len(segments)} segmen)")
    return nama_file


def generate_vtt(teks_transkrip, nama_file=None):
    """Mengubah transkrip [MM:SS] menjadi file subtitle .vtt (WebVTT).

    Format VTT yang dihasilkan:
      WEBVTT

      1
      00:00:05.000 --> 00:00:10.000
      Halo semuanya

      2
      00:01:30.000 --> 00:01:35.000
      Pembicara 1: Selamat pagi

    Mengembalikan path file .vtt yang dihasilkan.
    """
    if nama_file is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        DIR_SUBTITLE = "Hasil_Subtitle"
        os.makedirs(DIR_SUBTITLE, exist_ok=True)
        nama_file = os.path.join(DIR_SUBTITLE, f"subtitle_{timestamp}.vtt")

    segments = _parse_transkrip_ke_segments(teks_transkrip)
    if not segments:
        print("-> Peringatan: tidak ada segmen untuk dibuat subtitle.")
        return None

    baris = ["WEBVTT", ""]  # header VTT
    for i, seg in enumerate(segments, 1):
        start_str = _detik_ke_vtt_timestamp(seg["start"])
        end_str = _detik_ke_vtt_timestamp(seg["end"])
        baris.append(str(i))
        baris.append(f"{start_str} --> {end_str}")
        baris.append(seg["text"])
        baris.append("")  # blank line separator

    with open(nama_file, "w", encoding="utf-8") as f:
        f.write("\n".join(baris))

    print(f"-> Subtitle VTT disimpan: {nama_file} ({len(segments)} segmen)")
    return nama_file


def simpan_subtitle(teks_transkrip, nama_dasar, format_subtitle):
    """Menyimpan subtitle dalam format yang diminta: 'srt', 'vtt', atau 'both'."""
    disimpan = []
    if format_subtitle in ("srt", "both"):
        nama = f"{nama_dasar}.srt"
        hasil = generate_srt(teks_transkrip, nama)
        if hasil:
            disimpan.append(hasil)
    if format_subtitle in ("vtt", "both"):
        nama = f"{nama_dasar}.vtt"
        hasil = generate_vtt(teks_transkrip, nama)
        if hasil:
            disimpan.append(hasil)
    if disimpan:
        print(f"Subtitle berhasil disimpan ke: {', '.join(disimpan)}")
    return disimpan


# ==========================================
# 6c. FUNGSI TRANSLATE TRANKRIP VIA GEMINI
# ==========================================
# Bahasa yang didukung untuk translate
BAHASA_TRANSLATE = {
    "id": "Bahasa Indonesia",
    "en": "English",
    "ms": "Bahasa Melayu",
    "ja": "Japanese",
    "ko": "Korean",
    "zh": "Chinese",
    "ar": "Arabic",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "pt": "Portuguese",
    "ru": "Russian",
    "th": "Thai",
    "vi": "Vietnamese",
    "tr": "Turkish",
    "auto": "auto-detect",
}


def translate_transkrip(teks_transkrip, bahasa_tujuan, model=GEMINI_MODEL):
    """Menerjemahkan transkrip rapat ke bahasa tujuan via Gemini AI.

    Menggunakan Gemini dengan temperature rendah untuk hasil terjemahan yang konsisten.
    Mempertahankan format timestamp [MM:SS] dan label pembicara.

    bahasa_tujuan: kode bahasa (mis. 'en', 'ja', 'id', dll.) atau nama bahasa.
    Mengembalikan teks transkrip yang sudah diterjemahkan.
    """
    # Tentukan nama bahasa tujuan
    nama_bahasa = BAHASA_TRANSLATE.get(bahasa_tujuan, bahasa_tujuan)

    print(f"\n-> Menerjemahkan transkrip ke {nama_bahasa} via Gemini ({model})...")

    client = genai.Client(api_key=GEMINI_API_KEY)

    prompt = f"""Anda adalah penerjemah profesional. Terjemahkan transkrip rapat berikut ke {nama_bahasa}.

ATURAN WAJIB:
1. PERTAHANKAN format timestamp [MM:SS] atau [HH:MM:SS] - JANGAN terjemahkan timestamp.
2. PERTAHANKAN label pembicara (mis. 'Pembicara 1', 'Speaker 1') - JANGAN terjemahkan.
3. Terjemahkan ISI PERCAKAPAN saja, bukan format/timestamp.
4. Pertahankan istilah teknis/nama organisasi yang sudah dikenal luas.
5. Gunakan terjemahan yang natural dan mudah dipahami di bahasa target.
6. JANGAN menambah atau mengurangi informasi dari transkrip asli.
7. Keluarkan HANYA baris transkrip yang sudah diterjemahkan. Tanpa penjelasan.

CONTOH:
[00:05] Pembicara 1: Selamat pagi, mari kita mulai rapat hari ini.
->
[00:05] Speaker 1: Good morning, let's start today's meeting.

TRANSKRIP ASLI:
{teks_transkrip}
"""

    response = _panggil_generate_dengan_retry(
        client,
        model,
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0.1,  # rendah: terjemahan harus konsisten & akurat
            system_instruction=(
                "Anda adalah penerjemah profesional yang sangat teliti. "
                "Tugas Anda HANYA menerjemahkan teks percakapan dari transkrip rapat. "
                "JANGAN PERNAH mengubah, menambah, atau menghapus timestamp atau label pembicara."
            )
        ),
        model_cadangan=GEMINI_NOTULEN_CADANGAN,
    )

    if not response.text or not response.text.strip():
        print("-> Peringatan: hasil terjemahan kosong. Menggunakan transkrip asli.")
        return teks_transkrip

    hasil = response.text.strip()
    print(f"-> Terjemahan selesai: {len(hasil)} karakter")
    return hasil


def simpan_translate(teks_translate, nama_dasar, bahasa_tujuan):
    """Menyimpan hasil translate ke file .txt."""
    nama = f"{nama_dasar}_{bahasa_tujuan}.txt"
    with open(nama, "w", encoding="utf-8") as f:
        f.write(teks_translate)
    print(f"-> Terjemahan disimpan: {nama}")
    return nama


# ==========================================
# 6d. FUNGSI SEARCH DI TRANKRIP
# ==========================================
def search_transkrip(teks_transkrip, kata_kunci, case_sensitive=False, use_regex=False):
    """Mencari kata/frasa di transkrip dan mengembalikan hasil yang cocok.

    kata_kunci: string yang dicari (bisa berupa regex jika use_regex=True)
    case_sensitive: True = huruf besar/kecil berbeda, False = case-insensitive
    use_regex: True = gunakan regex, False = pencarian literal

    Mengembalikan list dict: [{"line": nomor_baris, "timestamp": "[MM:SS]", "text": teks}, ...]
    """
    hasil = []
    flags = 0 if case_sensitive else re.IGNORECASE

    try:
        if use_regex:
            pola = re.compile(kata_kunci, flags)
        else:
            # Escape karakter regex untuk pencarian literal
            pola = re.compile(re.escape(kata_kunci), flags)
    except re.error as e:
        print(f"-> Error regex: {e}")
        return hasil

    for i, baris in enumerate(teks_transkrip.splitlines(), 1):
        baris_strip = baris.strip()
        if not baris_strip:
            continue

        if pola.search(baris_strip):
            # Ekstrak timestamp jika ada
            match_ts = re.match(r"^(\[\d{1,2}:\d{2}(?::\d{2})?\])\s*(.*)", baris_strip)
            if match_ts:
                timestamp = match_ts.group(1)
                teks = match_ts.group(2)
            else:
                timestamp = ""
                teks = baris_strip

            hasil.append({
                "line": i,
                "timestamp": timestamp,
                "text": teks,
                "raw": baris_strip,
            })

    return hasil


def cetak_hasil_search(hasil, kata_kunci, max_hasil=50):
    """Mencetak hasil search dengan format yang rapi."""
    if not hasil:
        print(f"\n-> Tidak ditemukan '{kata_kunci}' di transkrip.")
        return

    print(f"\n{'='*60}")
    print(f"  HASIL PENCARIAN: '{kata_kunci}'")
    print(f"  Ditemukan: {len(hasil)} hasil")
    print(f"{'='*60}")

    for i, h in enumerate(hasil[:max_hasil], 1):
        ts = h["timestamp"] if h["timestamp"] else f"[baris {h['line']}]"
        # Highlight kata kunci dalam teks
        teks = h["text"]
        pola = re.compile(re.escape(kata_kunci), re.IGNORECASE)
        teks_highlight = pola.sub(f">>>{kata_kunci.upper()}<<<", teks)
        print(f"\n  {i}. {ts} {teks_highlight}")

    if len(hasil) > max_hasil:
        print(f"\n  ... dan {len(hasil) - max_hasil} hasil lainnya.")

    print(f"\n{'='*60}")


def simpan_hasil_search(hasil, kata_kunci, nama_file=None):
    """Menyimpan hasil search ke file .txt."""
    if not hasil:
        return None

    if nama_file is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        DIR_SEARCH = "Hasil_Search"
        os.makedirs(DIR_SEARCH, exist_ok=True)
        # Bersihkan nama file dari karakter tidak aman
        kata_bersih = re.sub(r'[^a-zA-Z0-9_]', '_', kata_kunci)[:30]
        nama_file = os.path.join(DIR_SEARCH, f"search_{kata_bersih}_{timestamp}.txt")

    baris = [f"HASIL PENCARIAN: '{kata_kunci}'"]
    baris.append(f"Ditemukan: {len(hasil)} hasil")
    baris.append("=" * 50)
    baris.append("")

    for i, h in enumerate(hasil, 1):
        ts = h["timestamp"] if h["timestamp"] else f"[baris {h['line']}]"
        baris.append(f"{i}. {ts} {h['text']}")

    with open(nama_file, "w", encoding="utf-8") as f:
        f.write("\n".join(baris))

    print(f"-> Hasil search disimpan: {nama_file}")
    return nama_file


# ==========================================
# 6e. FUNGSI EXPORT KE PDF
# ==========================================
# 6e. FUNGSI EXPORT KE PDF
# ==========================================
def _cari_font_unicode():
    """Mencari file font TTF yang mendukung Unicode di sistem.

    Mengembalikan path file font, atau None jika tidak ditemukan.
    Prioritas: DejaVuSans > NotoSans > LiberationSans > Arial.
    """
    # Daftar font Unicode umum di Windows dan Linux
    kandidat = [
        # Windows
        os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts", "DejaVuSans.ttf"),
        os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts", "DejaVuSans-Bold.ttf"),
        os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts", "arial.ttf"),
        os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts", "arialuni.ttf"),
        # Linux
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
    ]
    for jalur in kandidat:
        if os.path.isfile(jalur):
            return jalur
    return None


def export_pdf(teks, nama_file=None, judul="Dokumen"):
    """Mengubah teks (transkrip/notulen) menjadi file PDF.

    Menggunakan fpdf2 untuk generasi PDF dengan font Unicode.
    Mengembalikan path file PDF yang dihasilkan.
    """
    try:
        from fpdf import FPDF
    except ImportError:
        raise SystemExit(
            "\nfpdf2 belum terinstal. Instal dengan:\n"
            "  pip install fpdf2\n"
            "(Library untuk generasi PDF di Python.)"
        ) from None

    if nama_file is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        DIR_PDF = "Hasil_PDF"
        os.makedirs(DIR_PDF, exist_ok=True)
        nama_file = os.path.join(DIR_PDF, f"{judul.lower().replace(' ', '_')}_{timestamp}.pdf")

    print(f"-> Membuat file PDF: {nama_file}")

    try:
        pdf = FPDF()
        pdf.set_auto_page_break(auto=True, margin=15)

        # Coba gunakan font Unicode (DejaVuSans) agar karakter non-Latin
        # (huruf Indonesia berdiakritik, CJK, dll.) tampil dengan benar.
        font_unicode = _cari_font_unicode()
        if font_unicode:
            pdf.add_font("Unicode", "", font_unicode, uni=True)
            # Cari font bold jika tersedia
            font_bold = font_unicode.replace("Regular", "Bold").replace(".ttf", "-Bold.ttf")
            if not os.path.isfile(font_bold):
                font_bold = font_unicode  # fallback ke regular
            pdf.add_font("Unicode", "B", font_bold, uni=True)
            nama_font = "Unicode"
        else:
            # Fallback ke Helvetica (tidak support Unicode penuh)
            nama_font = "Helvetica"
            print("-> Peringatan: font Unicode tidak ditemukan. Karakter non-Latin mungkin tidak tampil.")

        pdf.add_page()

        # Judul
        pdf.set_font(nama_font, "B", 16)
        pdf.cell(0, 10, judul, ln=True, align="C")
        pdf.ln(5)

        # Tanggal cetak
        pdf.set_font(nama_font, "", 9)
        pdf.cell(0, 8, f"Dicetak: {datetime.now().strftime('%d %B %Y %H:%M')}", ln=True, align="R")
        pdf.ln(5)

        # Isi konten
        pdf.set_font(nama_font, "", 11)
        for baris in teks.splitlines():
            baris_strip = baris.strip()
            if not baris_strip:
                pdf.ln(3)  # spasi untuk baris kosong
                continue

            # Cek apakah ini heading (mulai dengan #)
            if baris_strip.startswith("# "):
                pdf.set_font(nama_font, "B", 14)
                pdf.multi_cell(0, 8, baris_strip.lstrip("# "))
                pdf.set_font(nama_font, "", 11)
            elif baris_strip.startswith("## "):
                pdf.set_font(nama_font, "B", 12)
                pdf.multi_cell(0, 7, baris_strip.lstrip("# ").strip())
                pdf.set_font(nama_font, "", 11)
            elif baris_strip.startswith("### "):
                pdf.set_font(nama_font, "B", 11)
                pdf.multi_cell(0, 7, baris_strip.lstrip("# ").strip())
                pdf.set_font(nama_font, "", 11)
            # Cek apakah ini timestamp [MM:SS]
            elif re.match(r"^\[\d{1,2}:\d{2}", baris_strip):
                pdf.set_font(nama_font, "", 10)
                pdf.set_text_color(100, 100, 100)  # abu-abu untuk timestamp
                pdf.multi_cell(0, 6, baris_strip)
                pdf.set_text_color(0, 0, 0)  # kembali ke hitam
                pdf.set_font(nama_font, "", 11)
            else:
                pdf.multi_cell(0, 6, baris_strip)

            pdf.ln(2)  # spasi antar baris

        pdf.output(nama_file)
        print(f"-> PDF berhasil dibuat: {nama_file}")
        return nama_file

    except Exception as e:
        print(f"-> Gagal membuat PDF: {e}")
        return None


def simpan_pdf(teks, nama_dasar, judul="Dokumen"):
    """Menyimpan teks ke file PDF."""
    nama = f"{nama_dasar}.pdf"
    return export_pdf(teks, nama, judul)


# ==========================================
# 7. FUNGSI PENGIRIMAN EMAIL (RESEND)
# ==========================================
POLA_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def pisahkan_emails(teks):
    """Mengubah string email (dipisah koma) menjadi daftar email yang divalidasi."""
    hasil = []
    for bagian in teks.split(","):
        email = bagian.strip()
        if not email:
            continue
        if not POLA_EMAIL.match(email):
            raise SystemExit(f"\nERROR: alamat email tidak valid: {email}")
        hasil.append(email)
    return hasil


def kirim_email_resend(penerima, subjek, isi):
    """Mengirim email via Resend API. Membutuhkan RESEND_API_KEY di .env."""
    try:
        import resend
    except ImportError:
        raise SystemExit(
            "\nPaket 'resend' belum terinstal. Instal dengan:\n"
            "  pip install resend"
        ) from None

    api_key = os.environ.get("RESEND_API_KEY")
    if not api_key:
        raise SystemExit(
            "\nERROR: RESEND_API_KEY tidak ditemukan.\n"
            "  Buat API key di https://resend.com/api-keys lalu tambahkan\n"
            "  RESEND_API_KEY=<key> ke file .env\n"
        )

    resend.api_key = api_key

    params = {
        "from": os.environ.get("RESEND_PENGIRIM", "onboarding@resend.dev"),
        "to": penerima,
        "subject": subjek,
        "text": isi,
    }
    try:
        hasil = resend.Emails.send(params)
    except Exception as e:
        raise SystemExit(f"\nERROR saat mengirim email via Resend: {e}") from e

    print(f"-> Email terkirim ke {', '.join(penerima)} (id: {hasil.get('id', '?')})")


# ==========================================
# 8. MAIN / EXECUTION WORKFLOW
# ==========================================
def buat_parser():
    """Membuat parser argumen CLI (dipisahkan dari main() agar mudah diuji)."""
    parser = argparse.ArgumentParser(
        description="Buat notulen rapat dari Zoom: rekam audio, transkripsi dengan Whisper, notulen dengan Gemini."
    )
    parser.add_argument(
        "--durasi", type=int, default=None,
        help="Durasi perekaman dalam detik (default: 60, atau dari konfigurasi.json)."
    )
    parser.add_argument(
        "--audio", default=None,
        help="Path ke file audio yang sudah ada (mis. hasil ekstraksi rekaman Zoom). "
             "Jika diisi, tahap perekaman dilewati."
    )
    parser.add_argument(
        "--youtube", default=None,
        help="URL video YouTube untuk diunduh dan diproses langsung. "
             "Audio akan diunduh (via yt-dlp), dikonversi ke WAV, lalu ditranskripsi. "
             "Butuh internet & yt-dlp terinstal (pip install yt-dlp)."
    )
    parser.add_argument(
        "--folder", default=None,
        help="Path ke folder berisi file audio/video untuk diproses secara batch. "
             "Semua file audio/video di folder akan ditranskripsi & dibuatkan notulen. "
             "Format didukung: wav, mp3, m4a, mp4, mkv, avi, dll."
    )
    parser.add_argument(
        "--bahasa", default=None,
        help="Bahasa untuk transkripsi (default: id, atau dari konfigurasi.json). "
             "Gunakan 'auto' agar deteksi bahasa otomatis (cocok untuk rapat campuran)."
    )
    parser.add_argument(
        "--engine", choices=PILIHAN_ENGINE, default=None,
        help="Mesin transkripsi: 'lokal' = faster-whisper di komputer Anda (default, gratis, "
             "offline); 'gemini' = transkripsi cloud via Gemini AI (super cepat, speaker "
             "diarization, butuh internet & kuota Gemini)."
    )
    parser.add_argument(
        "--sumber", choices=["mic", "loopback"], default=None,
        help="Sumber audio: 'mic' = mikrofon (default), 'loopback' = audio sistem "
             "WASAPI (Windows, butuh pyaudiowpatch)."
    )
    parser.add_argument(
        "--list-devices", action="store_true",
        help="Tampilkan daftar perangkat WASAPI loopback yang tersedia lalu keluar."
    )
    parser.add_argument(
        "--perangkat", type=int, default=None,
        help="Index perangkat loopback yang dipakai (lihat --list-devices). "
             "Default: pasangan loopback dari perangkat output default."
    )
    parser.add_argument(
        "--no-vad", action="store_true", default=False,
        help="Nonaktifkan VAD (Voice Activity Detection) pada transkripsi lokal. "
             "Gunakan jika transkripsi terlalu pendek atau banyak bagian yang hilang. "
             "VAD bisa terlalu agresif pada audio dengan volume rendah."
    )
    parser.add_argument(
        "--flac", action="store_true", default=False,
        help="Kompresi file audio WAV ke FLAC (lossless) setelah rekaman. "
             "Mengurangi ukuran file ~50-60% tanpa kehilangan kualitas."
    )
    parser.add_argument(
        "--format", choices=["md", "txt", "both"], default=None,
        help="Format file notulen yang disimpan: 'md' = Markdown, 'txt' = teks polos, "
             "'both' = keduanya (default)."
    )
    parser.add_argument(
        "--subtitle", choices=["srt", "vtt", "both", "none"], default=None,
        help="Buat file subtitle dari transkrip: 'srt' = SubRip (.srt), 'vtt' = WebVTT (.vtt), "
             "'both' = keduanya, 'none' = tidak buat subtitle (default)."
    )
    parser.add_argument(
        "--translate", default=None,
        metavar="BAHASA",
        help="Terjemahkan transkrip ke bahasa lain via Gemini. "
             "Kode bahasa: 'en' (Inggris), 'ja' (Jepang), 'ko' (Korea), 'zh' (China), "
             "'ms' (Melayu), 'ar' (Arab), 'es' (Spanyol), 'fr' (Prancis), 'de' (Jerman), dll. "
             "Contoh: --translate en, --translate ja"
    )
    parser.add_argument(
        "--search", default=None,
        metavar="KATA_KUNCI",
        help="Cari kata/frasa di transkrip. Mendukung regex (gunakan --search-regex). "
             "Contoh: --search \"action items\", --search \"Budi\""
    )
    parser.add_argument(
        "--search-regex", action="store_true", default=False,
        help="Gunakan mode regex untuk --search (contoh: --search \"rapat.{0,10}senin\" --search-regex)"
    )
    parser.add_argument(
        "--search-case", action="store_true", default=False,
        help="Huruf besar/kecil berbeda saat search (default: case-insensitive)"
    )
    parser.add_argument(
        "--pdf", action="store_true", default=False,
        help="Export transkrip dan/atau notulen ke file PDF (butuh fpdf2). "
             "File PDF disimpan di folder Hasil_PDF/."
    )
    parser.add_argument(
        "--judul", default=None,
        help="Judul notulen (mis. 'Rapat Mingguan Tim Produk'). Jika tidak diisi, "
             "Gemini menentukan topik dari transkrip."
    )
    parser.add_argument(
        "--daftar-peserta", default=None,
        help="Daftar peserta rapat, dipisah koma (mis. 'Budi, Siti, Andi'). "
             "Disertakan sebagai bagian 'Daftar Peserta' di notulen."
    )
    parser.add_argument(
        "--tujuan-email", default=None,
        help="Alamat email penerima notulen, dipisah koma (mis. 'budi@mail.com, siti@mail.com'). "
             "Kirim notulen via Resend API (butuh RESEND_API_KEY di .env)."
    )
    parser.add_argument(
        "--model", default=None,
        help=f"Model Gemini yang digunakan (default: {GEMINI_MODEL})."
    )
    parser.add_argument(
        "--konteks", default=None,
        help="Istilah/singkatan domain untuk meningkatkan akurasi transkripsi "
             "(mis. 'Kemenkes, Permenpan, SJT, P2KA'). Whisper & Gemini menggunakan "
             "konteks ini untuk mengenali kata-kata teknis dengan lebih baik."
    )
    parser.add_argument(
        "--transkrip", default=None,
        help="Path ke file transkrip (.txt) yang sudah ada. Jika diisi, tahap perekaman "
             "dan transkripsi DILEWATI — langsung buat notulen dari transkrip ini. "
             "Berguna untuk regenerasi notulen jika hasil pertama kurang memuaskan."
    )
    parser.add_argument(
        "--abaikan-senyap", action="store_true",
        help="Lewati peringatan rekaman sunyi dan tetap proses transkripsi "
             "(untuk rekaman yang sangat pelan tapi masih ada isinya)."
    )
    parser.add_argument(
        "--noise-reduce", action="store_true", default=None,
        help="Kurangi noise latar belakang (AC, keyboard, dll.) sebelum transkripsi. "
             "Membutuhkan library noisereduce (pip install noisereduce)."
    )
    parser.add_argument(
        "--notulen-mode", choices=PILIHAN_NOTULEN_MODE, default=None,
        help="Mode pembuatan notulen: 'multi' = 2-pass (ekstrak fakta lalu susun notulen, "
             "lebih akurat, default); 'single' = 1-pass langsung (lebih cepat)."
    )
    parser.add_argument(
        "--beam-size", type=int, default=None,
        help=f"Beam size untuk transkripsi lokal (default: {WHISPER_BEAM_SIZE}). "
             "Nilai kecil (2-3) = lebih cepat; nilai besar (5-8) = lebih akurat."
    )
    return parser


def _buat_notulen_sesuai_mode(cfg, teks_transkrip, daftar_peserta, info_rapat=None):
    """Dispatcher: memilih mode pembuatan notulen sesuai konfigurasi."""
    if cfg["notulen_mode"] == "multi":
        return buat_notulen_multipass(
            teks_transkrip, judul=cfg["judul"], daftar_peserta=daftar_peserta,
            model=cfg["model"], info_rapat=info_rapat
        )
    return buat_notulen(
        teks_transkrip, judul=cfg["judul"], daftar_peserta=daftar_peserta,
        model=cfg["model"], info_rapat=info_rapat
    )


def main():
    aktifkan_utf8_stdout()
    parser = buat_parser()
    args = parser.parse_args()

    # Muat pengaturan default rapat dari konfigurasi.json (jika ada)
    konfig = muat_konfigurasi()
    if konfig:
        print("[INFO] Pengaturan default dimuat dari 'konfigurasi.json'")
    cfg = terapkan_konfigurasi(args, konfig)

    # Override beam_size ke global jika user meng-override via CLI
    global WHISPER_BEAM_SIZE
    if args.beam_size is not None:
        WHISPER_BEAM_SIZE = args.beam_size
        print(f"[INFO] Beam size diubah ke {WHISPER_BEAM_SIZE} (dari --beam-size)")

    if args.perangkat is not None and cfg["sumber"] != "loopback":
        raise SystemExit("\nERROR: opsi --perangkat hanya berlaku dengan --sumber loopback.")

    if args.list_devices:
        daftar_perangkat_loopback()
        return

    # ======= Mode search: cari di transkrip yang sudah ada =======
    if args.search:
        # Cari di file transkrip yang sudah ada
        if args.transkrip:
            jalur_transkrip = args.transkrip
            if not os.path.isfile(jalur_transkrip):
                raise SystemExit(f"\nERROR: file transkrip tidak ditemukan: {jalur_transkrip}")
            with open(jalur_transkrip, "r", encoding="utf-8") as f:
                transkrip_teks = f.read()
        elif args.audio:
            # Jika ada file audio, transkripsi dulu lalu cari
            if not os.path.isfile(args.audio):
                raise SystemExit(f"\nERROR: file audio tidak ditemukan: {args.audio}")
            print(f"[INFO] Transkripsi audio dulu, lalu cari: {args.audio}")
            transkrip_teks = transkripsi_audio(args.audio, cfg["bahasa"], cfg["engine"],
                                               konteks=cfg["konteks"])
        else:
            # Cari di semua file transkrip di folder Hasil_Transkripsi
            DIR_TRANSKRIP = "Hasil_Transkripsi"
            if not os.path.isdir(DIR_TRANSKRIP):
                raise SystemExit(f"\nERROR: folder {DIR_TRANSKRIP} tidak ditemukan.")
            transkrip_teks = ""
            for f in sorted(os.listdir(DIR_TRANSKRIP)):
                if f.endswith(".txt"):
                    with open(os.path.join(DIR_TRANSKRIP, f), "r", encoding="utf-8") as fh:
                        transkrip_teks += f"\n--- {f} ---\n" + fh.read()
            if not transkrip_teks.strip():
                raise SystemExit(f"\nERROR: tidak ada file transkrip di {DIR_TRANSKRIP}.")

        # Jalankan search
        hasil = search_transkrip(
            transkrip_teks,
            args.search,
            case_sensitive=args.search_case,
            use_regex=args.search_regex
        )
        cetak_hasil_search(hasil, args.search)

        # Simpan hasil search
        if hasil:
            simpan_hasil_search(hasil, args.search)
        return

    # Nama file memakai timestamp agar tidak menimpa hasil sebelumnya
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Buat direktori output secara otomatis jika belum ada
    DIR_REKAMAN = "Hasil_Rekaman"
    DIR_TRANSKRIP = "Hasil_Transkripsi"
    DIR_NOTULEN = "Hasil_Notulen"
    os.makedirs(DIR_REKAMAN, exist_ok=True)
    os.makedirs(DIR_TRANSKRIP, exist_ok=True)
    os.makedirs(DIR_NOTULEN, exist_ok=True)

    # ======= Mode regenerasi notulen dari transkrip yang sudah ada =======
    if args.transkrip:
        jalur_transkrip = args.transkrip
        if not os.path.isfile(jalur_transkrip):
            raise SystemExit(f"\nERROR: file transkrip tidak ditemukan: {jalur_transkrip}")
        print(f"[INFO] Mode regenerasi notulen dari transkrip: {jalur_transkrip}")
        with open(jalur_transkrip, "r", encoding="utf-8") as f:
            transkrip_teks = f.read()
        if not transkrip_teks.strip():
            raise SystemExit("\nERROR: file transkrip kosong.")
        print(f"-> Transkrip dimuat: {len(transkrip_teks)} karakter")

        # Normalisasi daftar peserta
        daftar_peserta = None
        if cfg["daftar_peserta"]:
            daftar_peserta = ", ".join(
                p.strip() for p in cfg["daftar_peserta"].split(",") if p.strip()
            )

        notulen = _buat_notulan_sesuai_mode(
            cfg, transkrip_teks, daftar_peserta
        )

        # Generate subtitle jika diminta
        if cfg["subtitle"] != "none":
            DIR_SUBTITLE = "Hasil_Subtitle"
            os.makedirs(DIR_SUBTITLE, exist_ok=True)
            nama_subtitle = os.path.join(DIR_SUBTITLE, f"subtitle_{timestamp}")
            simpan_subtitle(transkrip_teks, nama_subtitle, cfg["subtitle"])

        # Translate transkrip jika diminta
        if cfg["translate"]:
            DIR_TRANSLATE = "Hasil_Translate"
            os.makedirs(DIR_TRANSLATE, exist_ok=True)
            teks_translate = translate_transkrip(transkrip_teks, cfg["translate"], cfg["model"])
            nama_translate = os.path.join(DIR_TRANSLATE, f"Transkrip_{timestamp}")
            simpan_translate(teks_translate, nama_translate, cfg["translate"])

        # Validasi anti-halusinasi
        nama_mencurigakan = periksa_halusinasi_notulen(notulen, transkrip_teks)
        if nama_mencurigakan:
            print(
                "\n!!! PERINGATAN: nama di Action Items tidak ditemukan di transkrip !!!\n"
                f"  Nama: {', '.join(nama_mencurigakan)}\n"
                "  Cek ulang Action Items pada notulen sebelum disebarluaskan.\n"
            )

        print("\n==========================================")
        print("            HASIL NOTULEN RAPAT           ")
        print("==========================================")
        print(notulen)

        simpan_notulen(notulen, os.path.join(DIR_NOTULEN, f"Notulen_Rapat_{timestamp}"), cfg["format"])

        # Export ke PDF jika diminta
        if args.pdf:
            DIR_PDF = "Hasil_PDF"
            os.makedirs(DIR_PDF, exist_ok=True)
            nama_pdf_transkrip = os.path.join(DIR_PDF, f"Transkrip_{timestamp}.pdf")
            export_pdf(transkrip_teks, nama_pdf_transkrip, "Transkrip Rapat")
            nama_pdf_notulen = os.path.join(DIR_PDF, f"Notulen_{timestamp}.pdf")
            export_pdf(notulen, nama_pdf_notulen, cfg.get("judul") or "Notulen Rapat")

        if cfg["tujuan_email"]:
            penerima = pisahkan_emails(cfg["tujuan_email"])
            subjek = f"Notulen Rapat - {cfg['judul']}" if cfg["judul"] else f"Notulen Rapat {timestamp}"
            kirim_email_resend(penerima, subjek, notulen)
        return

    # ======= Mode batch: proses semua file di folder =======
    if args.folder:
        proses_folder(args.folder, cfg, engine=cfg["engine"], bahasa=cfg["bahasa"])
        return

    # ======= Pipeline standar: rekam -> transkrip -> notulen =======
    # Mode YouTube: download audio dari URL lalu proses seperti file audio biasa
    if args.youtube:
        audio_file = unduh_audio_youtube(args.youtube)
        if not audio_file or not os.path.isfile(audio_file):
            raise SystemExit("\nERROR: gagal mengunduh audio dari YouTube.")
        print(f"[0/3] Audio YouTube siap diproses: {audio_file}")
    elif args.audio:
        audio_file = args.audio
        if not os.path.isfile(audio_file):
            raise SystemExit(f"\nERROR: file audio tidak ditemukan: {audio_file}")
        print(f"[0/3] Menggunakan file audio yang sudah ada: {audio_file}")
    else:
        audio_file = os.path.join(DIR_REKAMAN, f"rekaman_zoom_{timestamp}.wav")
        peringatan = peringatan_durasi_panjang(cfg["durasi"])
        if peringatan:
            print(peringatan)
        if cfg["sumber"] == "loopback":
            durasi_terekam = rekam_audio_loopback(
                cfg["durasi"], audio_file, index_perangkat=args.perangkat
            )
        else:
            durasi_terekam = rekam_audio(cfg["durasi"], audio_file)
        if durasi_terekam is not None and durasi_terekam < AMBANG_REKAM_MINIMAL:
            print(
                f"\n!!! PERINGATAN: rekaman sangat pendek ({durasi_terekam:.1f} detik) !!!\n"
                "  Transkripsi tetap dicoba - jika hasilnya kosong/tidak berguna,\n"
                "  jalankan ulang perekaman dengan durasi yang lebih lama.\n"
            )

    # Kompresi FLAC otomatis setelah rekaman (hemat ruang disk)
    if cfg.get("flac", False) and audio_file.lower().endswith(".wav"):
        jalur_flac = kompres_wav_ke_flac(audio_file)
        if jalur_flac:
            # Ganti audio_file ke FLAC untuk transkripsi
            audio_file = jalur_flac
            print(f"-> File FLAC siap untuk transkripsi: {jalur_flac}")

    # Deteksi rekaman hampir sunyi SEBELUM transkripsi (hemat waktu & kuota)
    rms = ukur_rms(audio_file)
    if not args.abaikan_senyap and rms is not None and rms < AMBANG_RMS_SENYAP:
        print(
            "\n!!! PERINGATAN: REKAMAN HAMPIR SUNYI !!!\n"
            f"  Kekuatan audio (RMS): {rms:.4f} (ambang sunyi: {AMBANG_RMS_SENYAP:.4f})\n"
            "  Kemungkinan penyebab:\n"
            "  - Mikrofon di-mute atau volume input 0 (mode mic)\n"
            "  - Tidak ada audio yang keluar dari speaker (mode loopback)\n"
            "  - Izin privasi mikrofon diblokir Windows\n"
            "  Transkripsi tetap dilanjutkan - jika hasilnya kosong, perbaiki\n"
            "  audio lalu jalankan ulang.\n"
        )

    # Noise reduction opsional: kurangi noise latar belakang sebelum transkripsi
    audio_transkripsi = audio_file
    jalur_bersih = None
    if cfg["noise_reduce"]:
        jalur_bersih = kurangi_noise(audio_file)
        if jalur_bersih:
            audio_transkripsi = jalur_bersih

    transkrip_teks = transkripsi_audio(audio_transkripsi, cfg["bahasa"], cfg["engine"],
                                       konteks=cfg["konteks"], no_vad=cfg.get("no_vad", False))

    # Hapus file noise-reduced sementara setelah transkripsi selesai
    if jalur_bersih:
        _hapus_file(jalur_bersih)

    # Simpan transkrip mentah ke file terpisah untuk dokumentasi
    nama_transkrip = os.path.join(DIR_TRANSKRIP, f"Transkrip_{timestamp}.txt")
    simpan_transkrip(transkrip_teks, nama_transkrip)

    # Generate subtitle jika diminta
    if cfg["subtitle"] != "none":
        DIR_SUBTITLE = "Hasil_Subtitle"
        os.makedirs(DIR_SUBTITLE, exist_ok=True)
        nama_subtitle = os.path.join(DIR_SUBTITLE, f"subtitle_{timestamp}")
        simpan_subtitle(transkrip_teks, nama_subtitle, cfg["subtitle"])

    # Translate transkrip jika diminta
    if cfg["translate"]:
        DIR_TRANSLATE = "Hasil_Translate"
        os.makedirs(DIR_TRANSLATE, exist_ok=True)
        teks_translate = translate_transkrip(transkrip_teks, cfg["translate"], cfg["model"])
        nama_translate = os.path.join(DIR_TRANSLATE, f"Transkrip_{timestamp}")
        simpan_translate(teks_translate, nama_translate, cfg["translate"])

    print("\n--- HASIL TRANSKRIP RAW ---")
    print(transkrip_teks)

    # Normalisasi daftar peserta (pisah koma, buang spasi berlebih & kosong)
    daftar_peserta = None
    if cfg["daftar_peserta"]:
        daftar_peserta = ", ".join(
            p.strip() for p in cfg["daftar_peserta"].split(",") if p.strip()
        )

    # Susun metadata rapat (dari timestamp file audio)
    info_rapat = {
        "tanggal": datetime.now().strftime("%d %B %Y"),
        "waktu": datetime.now().strftime("%H:%M WIB"),
    }

    notulen = _buat_notulen_sesuai_mode(
        cfg, transkrip_teks, daftar_peserta, info_rapat=info_rapat
    )

    # Validasi anti-halusinasi: nama PIC di Action Items harus ada di transkrip
    nama_mencurigakan = periksa_halusinasi_notulen(notulen, transkrip_teks)
    if nama_mencurigakan:
        print(
            "\n!!! PERINGATAN: nama di Action Items tidak ditemukan di transkrip !!!\n"
            "  (kemungkinan halusinasi Gemini - nama dikarang dari Daftar Peserta,\n"
            "   padahal transkrip hanya memakai label 'Pembicara N')\n"
            f"  Nama: {', '.join(nama_mencurigakan)}\n"
            "  Cek ulang Action Items pada notulen sebelum disebarluaskan.\n"
        )

    print("\n==========================================")
    print("            HASIL NOTULEN RAPAT           ")
    print("==========================================")
    print(notulen)

    # Simpan notulen ke file sesuai format yang diminta
    simpan_notulen(notulen, os.path.join(DIR_NOTULEN, f"Notulen_Rapat_{timestamp}"), cfg["format"])

    # Export ke PDF jika diminta
    if args.pdf:
        DIR_PDF = "Hasil_PDF"
        os.makedirs(DIR_PDF, exist_ok=True)
        # Export transkrip ke PDF
        nama_pdf_transkrip = os.path.join(DIR_PDF, f"Transkrip_{timestamp}.pdf")
        export_pdf(transkrip_teks, nama_pdf_transkrip, "Transkrip Rapat")
        # Export notulen ke PDF
        nama_pdf_notulen = os.path.join(DIR_PDF, f"Notulen_{timestamp}.pdf")
        export_pdf(notulen, nama_pdf_notulen, cfg.get("judul") or "Notulen Rapat")

    # Kirim notulen via email jika diminta
    if cfg["tujuan_email"]:
        penerima = pisahkan_emails(cfg["tujuan_email"])
        subjek = f"Notulen Rapat - {cfg['judul']}" if cfg["judul"] else f"Notulen Rapat {timestamp}"
        kirim_email_resend(penerima, subjek, notulen)


if __name__ == "__main__":
    main()
