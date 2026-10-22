"""Wrapper REST untuk zoom_notulen — akses transkripsi & notulen via HTTP API.

Menjalankan server:
    python api_notulen.py                 # default http://127.0.0.1:8000
    python api_notulen.py --port 9000
    uvicorn api_notulen:app --host 0.0.0.0 --port 8000

Deteksi .venv otomatis: jika dijalankan dengan Python yang BUKAN .venv
(mis. Python global hasil instalasi baru) dan .venv tersedia, proses otomatis
dipindahkan ke Python .venv — dependensi tidak perlu diinstal dua kali.
Lewati dengan variabel lingkungan NOTULEN_TANPA_VENV=1.

Dokumentasi interaktif otomatis (Swagger): http://127.0.0.1:8000/docs
Dokumentasi alternatif (ReDoc):          http://127.0.0.1:8000/redoc

Endpoint:
    GET  /health       — status konfigurasi (API key, engine, model)
    POST /transkripsi  — upload file audio/video → JSON {transkrip}
    POST /notulen      — kirim teks transkrip → JSON {notulen_md}
    POST /proses       — upload audio → JSON {transkrip, notulen_md} sekaligus

Kebutuhan dependensi (sudah ada di requirements.txt): fastapi, uvicorn,
python-multipart (untuk UploadFile).

Catatan perilaku:
- Endpoint didefinisikan sebagai fungsi biasa (bukan `async def`) sehingga
  FastAPI otomatis menjalankannya di thread pool — transkripsi lokal
  yang lama tidak memblokir event loop.
- `zoom_notulen` melaporkan kegagalan Gemini (kuota/model tidak ada/dsb.)
  lewat `SystemExit`; di sini diterjemahkan menjadi HTTP 502
  (atau 400 untuk error validasi input seperti audio terlalu panjang).
- File upload ditulis ke file temporer dengan ekstensi asli (dikenal
  oleh checker format audio), lalu dihapus di blok finally.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path


# ------------------------------------------------------------------
# Deteksi venv: pindahkan proses ke Python .venv bila dijalankan di
# luar venv. Harus berjalan SEBELUM import uvicorn/fastapi, supaya
# Python global tanpa dependensi tetap bisa menyalakan server dari
# .venv hasil setup.bat.
# ------------------------------------------------------------------
_VENV_REEXEC = "NOTULEN_VENV_REEXEC"  # penanda agar re-exec tidak berulang


def _cmd_venv(dasar=None):
    """Kembalikan perintah re-exec ke Python .venv, atau None jika tidak perlu.

    Memberlakukan diri hanya pada dua cara menyalakan server:
      1. Script langsung : python api_notulen.py --port 9000
      2. Via CLI uvicorn : uvicorn api_notulen:app --port 8000
    Saat diimpor sebagai modul (tests, aplikasi lain) None dikembalikan —
    pemanggil yang menentukan environment-nya.
    """
    if os.environ.get(_VENV_REEXEC):  # sudah re-exec — jangan berulang
        return None
    if os.environ.get("NOTULEN_TANPA_VENV"):  # pintu darurat untuk skip venv
        return None
    dasar = Path(dasar or Path(__file__).resolve().parent).resolve()
    folder_venv = dasar / ".venv"
    if os.name == "nt":
        venv_python = folder_venv / "Scripts" / "python.exe"
    else:
        venv_python = folder_venv / "bin" / "python"
    if not venv_python.is_file():  # .venv belum dibuat (setup.bat belum dijalankan)
        return None
    try:
        if Path(sys.executable).resolve() == venv_python.resolve():
            return None  # sudah berjalan di dalam .venv
    except OSError:
        pass

    argv = sys.argv
    script = Path(__file__).resolve()
    if argv and Path(argv[0]).resolve() == script:
        # python api_notulen.py ... → jalankan ulang script di venv
        return [str(venv_python), str(script)] + list(argv[1:])
    if len(argv) > 1 and argv[1] == "api_notulen:app":
        # uvicorn/pip run api_notulen:app ... → modul uvicorn di venv
        return [str(venv_python), "-m", "uvicorn"] + list(argv[1:])
    return None


def _rerun_di_venv():
    """Eksekusi perintah dari _cmd_venv() bila ada, lalu hentikan proses lama."""
    cmd = _cmd_venv()
    if not cmd:
        return
    env = os.environ.copy()
    env[_VENV_REEXEC] = "1"
    print(f"[INFO] Menyalakan API server dengan Python dari .venv ({cmd[0]}) ...")
    try:
        hasil = subprocess.run(cmd, env=env)
    except OSError as e:
        print(f"[INFO] Gagal memindahkan proses ke .venv ({e}) — lanjut dengan Python ini.")
        return
    raise SystemExit(hasil.returncode)


_rerun_di_venv()

import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

_PESAN_IMPORT = ""  # terisi hanya jika import zoom_notulen gagal (GEMINI_API_KEY kosong)
try:
    import zoom_notulen as zn
except SystemExit as e:  # GEMINI_API_KEY tidak ada → app tetap hidup, /health yang menjelaskan
    zn = None
    _PESAN_IMPORT = str(e)

app = FastAPI(
    title="Notulen API",
    description=(
        "Wrapper REST untuk transkripsi rapat (faster-whisper lokal / Gemini cloud) "
        "dan pembuatan notulen via Gemini. Modul inti: `zoom_notulen.py`."
    ),
    version="1.0.0",
)

# Izinkan dipanggil dari browser/aplikasi lain lintas origin (dev/local; ketatkan
# `allow_origins` jika API ini nanti diekspos ke internet publik).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Batas ukuran upload (byte) — longgar: audio RAW 8 jam file WAV ~1 GB.
UKURAN_MAKS_UPLOAD = 2 * 1024**3

ENGINE_VALID = ("lokal", "gemini")
MODE_NOTULEN_VALID = ("single", "multi")


# ------------------------------------------------------------------
# Util
# ------------------------------------------------------------------
def _pastikan_modul():
    """Gemini API tanpa kunci → balas 503 dengan instruksi perbaikan yang jelas."""
    if zn is None:
        raise HTTPException(status_code=503, detail=_PESAN_IMPORT)


def _validasi_engine(mode: str) -> str:
    if mode not in ENGINE_VALID:
        raise HTTPException(
            status_code=422,
            detail=f"engine harus salah satu dari {ENGINE_VALID}, dapat: {mode!r}",
        )
    return mode


def _validasi_mode(mode: str) -> str:
    if mode not in MODE_NOTULEN_VALID:
        raise HTTPException(
            status_code=422,
            detail=f"mode harus salah satu dari {MODE_NOTULEN_VALID}, dapat: {mode!r}",
        )
    return mode


def _simpan_upload(file: UploadFile) -> Path:
    """Tulis UploadFile ke file temporer unik (ekstensi asli dipertahankan).

    NamedTemporaryFile(delete=False) sudah membuat file 0600 — hanya proses ini
    yang bisa membacanya. Ekstensi asli dipertahankan agar checker format audio
    (WAV/FLAC/OGG/MP4...) mengenalinya.
    """
    sumber = Path(file.filename or "upload")
    fd, nama = tempfile.mkstemp(prefix="api_notulen_", suffix=sumber.suffix)
    try:
        ukuran = 0
        with os.fdopen(fd, "wb") as tujuan:
            while True:
                slab = file.file.read(1024 * 1024)
                if not slab:
                    break
                ukuran += len(slab)
                if ukuran > UKURAN_MAKS_UPLOAD:
                    raise HTTPException(
                        status_code=413,
                        detail=f"File terlalu besar (maks {UKURAN_MAKS_UPLOAD / 1024**3:.0f} GB).",
                    )
                tujuan.write(slab)
        return Path(nama)
    except HTTPException:
        _hapus_file(Path(nama))
        raise
    except OSError as e:
        _hapus_file(Path(nama))
        raise HTTPException(status_code=500, detail=f"Gagal menulis file sementara: {e}") from e


def _hapus_file(jalur: Path) -> None:
    jalur.unlink(missing_ok=True)


def _systemexit_ke_http(e: SystemExit) -> HTTPException:
    """zoom_notulen pakai SystemExit untuk error Gemini/audio → petakan ke HTTP."""
    pesan = str(e).strip()
    if "durasi audio" in pesan or "GEMINI_API_KEY" in pesan:
        # Error validasi input (audio terlalu panjang) / konfigurasi hilang
        return HTTPException(status_code=400, detail=pesan)
    return HTTPException(status_code=502, detail=pesan or "Terjadi kesalahan di modul notulen.")


# ------------------------------------------------------------------
# Endpoint
# ------------------------------------------------------------------
@app.get("/health", summary="Status konfigurasi & engine")
def health():
    info = {
        "status": "ok" if zn is not None else "degraded",
        "modul_notulen": zn is not None,
    }
    if not info["modul_notulen"]:
        return {"status": "degraded", "detail": _PESAN_IMPORT}

    info.update(
        {
            "model_transkripsi": zn.GEMINI_TRANSKRIPSI_MODEL,
            "model_notulen": zn.GEMINI_MODEL,
            "cadangan_transkripsi": list(zn.GEMINI_TRANSKRIPSI_CADANGAN),
            "cadangan_notulen": list(zn.GEMINI_NOTULEN_CADANGAN),
            "kuota_potongan_gemini_menit": zn.GEMINI_POTONG_DURASI // 60,
            "api_key_ada": bool(zn.GEMINI_API_KEY),
        }
    )
    return info


@app.post("/transkripsi", summary="Upload audio/video → teks ber-timestamp")
def transkripsi(
    file: UploadFile = File(...),
    engine: str = Form("gemini"),
    bahasa: str = Form("id"),
    konteks: str = Form(None),
    no_vad: bool = Form(False),
):
    """Transkripsikan satu file audio ke teks `[MM:SS] isi`.

    - engine `gemini`: di server Google (cepat, kuota per-model, butuh internet).
    - engine `lokal`: faster-whisper di mesin ini (gratis, offline).
    """
    _pastikan_modul()
    _validasi_engine(engine)
    jalur = None
    try:
        jalur = _simpan_upload(file)
        try:
            teks = zn.transkripsi_audio(
                str(jalur), bahasa, engine=engine, konteks=konteks or None, no_vad=no_vad
            )
        except SystemExit as e:
            raise _systemexit_ke_http(e) from e
        if not teks or not teks.strip():
            raise HTTPException(status_code=502, detail="Transkripsi menghasilkan teks kosong.")
        return {"file": file.filename, "engine": engine, "bahasa": bahasa, "transkrip": teks}
    finally:
        if jalur:
            _hapus_file(jalur)


@app.post("/notulen", summary="Teks transkrip → notulen Markdown")
def notulen(
    transkrip: str = Form(...),
    judul: str = Form(None),
    peserta: str = Form(None),
    mode: str = Form("multi"),
):
    """Susun notulen rapat Markdown dari teks transkrip."""
    _pastikan_modul()
    _validasi_mode(mode)
    if not transkrip.strip():
        raise HTTPException(status_code=422, detail="transkrip tidak boleh kosong.")

    pemanggil = zn.buat_notulen_multipass if mode == "multi" else zn.buat_notulen
    daftar_peserta = peserta.strip() if peserta else None
    try:
        notulen_md = pemanggil(transkrip, judul=judul or None, daftar_peserta=daftar_peserta)
    except SystemExit as e:
        raise _systemexit_ke_http(e) from e
    if not notulen_md or not notulen_md.strip():
        raise HTTPException(status_code=502, detail="Notulen menghasilkan output kosong.")
    return {"judul": judul, "mode": mode, "notulen_md": notulen_md}


@app.post("/proses", summary="Upload audio → transkrip + notulen sekaligus")
def proses(
    file: UploadFile = File(...),
    engine: str = Form("gemini"),
    bahasa: str = Form("id"),
    judul: str = Form(None),
    peserta: str = Form(None),
    mode: str = Form("multi"),
    konteks: str = Form(None),
    no_vad: bool = Form(False),
):
    """Pipeline lengkap: audio → transkripsi → notulen Markdown."""
    _pastikan_modul()
    _validasi_engine(engine)
    _validasi_mode(mode)
    jalur = None
    try:
        jalur = _simpan_upload(file)
        try:
            teks = zn.transkripsi_audio(
                str(jalur), bahasa, engine=engine, konteks=konteks or None, no_vad=no_vad
            )
        except SystemExit as e:
            raise _systemexit_ke_http(e) from e
        if not teks or not teks.strip():
            raise HTTPException(status_code=502, detail="Transkripsi menghasilkan teks kosong.")

        pemanggil = zn.buat_notulen_multipass if mode == "multi" else zn.buat_notulen
        daftar_peserta = peserta.strip() if peserta else None
        try:
            notulen_md = pemanggil(teks, judul=judul or None, daftar_peserta=daftar_peserta)
        except SystemExit as e:
            raise _systemexit_ke_http(e) from e
        return {
            "file": file.filename,
            "engine": engine,
            "bahasa": bahasa,
            "mode": mode,
            "transkrip": teks,
            "notulen_md": notulen_md,
        }
    finally:
        if jalur:
            _hapus_file(jalur)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Server REST Notulen (docs di /docs)")
    parser.add_argument("--host", default="127.0.0.1", help="Alamat bind (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="Port bind (default: 8000)")
    args = parser.parse_args()
    uvicorn.run(app, host=args.host, port=args.port)
