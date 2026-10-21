"""Pengujian REST api_notulen.py — endpoint diuji dengan mock (tanpa panggilan Gemini nyata).

Pola sama dengan test_zoom_notulen.py: set GEMINI_API_KEY sebelum import agar
modul tidak SystemExit. Fungsi zoom_notulen di-patch per-test.
"""

import os

os.environ["GEMINI_API_KEY"] = "kunci-pengujian"

import io
from unittest import mock
from unittest.mock import patch

import zoom_notulen as zn
from fastapi.testclient import TestClient

from api_notulen import app

client = TestClient(app)


# ── /health ──────────────────────────────────────────────────────────
class TestHealth:
    def test_health_melaporkan_konfigurasi(self):
        res = client.get("/health")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "ok"
        assert data["modul_notulen"] is True
        assert data["model_transkripsi"] == zn.GEMINI_TRANSKRIPSI_MODEL
        assert data["model_notulen"] == zn.GEMINI_MODEL
        assert data["api_key_ada"] is True

    def test_health_modul_tak_tersedia(self):
        with patch("api_notulen.zn", None), patch("api_notulen._PESAN_IMPORT", "kunci hilang"):
            res = client.get("/health")
        assert res.status_code == 200
        assert res.json()["status"] == "degraded"


# ── /notulen ─────────────────────────────────────────────────────────
class TestNotulen:
    def test_mode_single_memanggil_buat_notulen(self):
        with patch.object(zn, "buat_notulen", return_value="# Notulen Rapat - Uji A") as dgn:
            res = client.post(
                "/notulen", data={"transkrip": "[00:05] hallo", "judul": "Uji A", "mode": "single"}
            )
        assert res.status_code == 200
        assert res.json()["notulen_md"] == "# Notulen Rapat - Uji A"
        dgn.assert_called_once_with("[00:05] hallo", judul="Uji A", daftar_peserta=None)

    def test_mode_multi_memanggil_buat_notulen_multipass(self):
        with patch.object(zn, "buat_notulen_multipass", return_value="## Fakta") as dgn:
            res = client.post(
                "/notulen",
                data={"transkrip": "[00:05] hallo", "peserta": "Budi", "mode": "multi"},
            )
        assert res.status_code == 200
        dgn.assert_called_once_with("[00:05] hallo", judul=None, daftar_peserta="Budi")

    def test_transkrip_kosong_ditolak(self):
        res = client.post("/notulen", data={"transkrip": "   "})
        assert res.status_code == 422

    def test_mode_tidak_dikenal_ditolak(self):
        res = client.post("/notulen", data={"transkrip": "isi", "mode": "double"})
        assert res.status_code == 422
        assert "multi" in res.json()["detail"]


# ── /transkripsi ─────────────────────────────────────────────────────
class TestTranskripsi:
    def test_engine_gemini_diteruskan_ke_dispatcher(self):
        with patch.object(zn, "transkripsi_audio", return_value="[00:01] tes") as dgn:
            res = client.post(
                "/transkripsi",
                files={"file": ("audio.wav", io.BytesIO(b"RIFFDUMMY"), "audio/wav")},
                data={"engine": "gemini", "bahasa": "id", "konteks": "rapat apel pagi"},
            )
        assert res.status_code == 200, res.text
        data = res.json()
        assert data["transkrip"] == "[00:01] tes"
        assert data["engine"] == "gemini"
        args, kwargs = dgn.call_args
        # File sebenarnya tersimpan dengan ekstensi .wav
        assert args[0].endswith(".wav") and os.path.isfile(args[0]) is False  # sudah dihapus
        assert args[1] == "id"
        assert kwargs == {"engine": "gemini", "konteks": "rapat apel pagi", "no_vad": False}

    def test_file_temp_dihapus_setelah_selesai(self):
        tersimpan = {}
        asli_isfile = os.path.isfile

        def rekam_jalur(nama_file, *a, **k):
            tersimpan["jalur"] = nama_file
            return "teks"

        with patch.object(zn, "transkripsi_audio", side_effect=rekam_jalur):
            client.post(
                "/transkripsi",
                files={"file": ("x.flac", io.BytesIO(b"fLaC"), "audio/flac")},
                data={"engine": "lokal"},
            )
        assert tersimpan["jalur"].endswith(".flac")
        assert asli_isfile(tersimpan["jalur"]) is False  # temp terbersihkan

    def test_file_temp_dihapus_jika_gagal(self):
        def gagal(nama_file, *a, **k):
            raise SystemExit("\nERROR: durasi audio 9.0 jam melebihi batas Gemini (8 jam per request).")

        with patch.object(zn, "transkripsi_audio", side_effect=gagal):
            res = client.post(
                "/transkripsi",
                files={"file": ("x.wav", io.BytesIO(b"RIFF"), "audio/wav")},
                data={"engine": "gemini"},
            )
        assert res.status_code == 400  # pesan durasi → 400
        assert "melebihi batas" in res.json()["detail"]

    def test_error_gemini_umum_jadi_502(self):
        with patch.object(
            zn, "transkripsi_audio", side_effect=SystemExit("\nERROR saat memanggil Gemini API: boom")
        ):
            res = client.post(
                "/transkripsi",
                files={"file": ("x.wav", io.BytesIO(b"RIFF"), "audio/wav")},
                data={"engine": "gemini"},
            )
        assert res.status_code == 502

    def test_engine_tidak_valid(self):
        res = client.post(
            "/transkripsi",
            files={"file": ("x.wav", io.BytesIO(b"RIFF"), "audio/wav")},
            data={"engine": "radar"},
        )
        assert res.status_code == 422


# ── /proses ──────────────────────────────────────────────────────────
class TestProses:
    def test_proses_menggabungkan_transkripsi_dan_notulen(self):
        with (
            patch.object(zn, "transkripsi_audio", return_value="[00:01] a") as dgn_tr,
            patch.object(zn, "buat_notulen_multipass", return_value="# Notulen") as dgn_nt,
        ):
            res = client.post(
                "/proses",
                files={"file": ("x.wav", io.BytesIO(b"RIFF"), "audio/wav")},
                data={"engine": "gemini", "judul": "Rapat Harian"},
            )
        assert res.status_code == 200, res.text
        data = res.json()
        assert data["transkrip"] == "[00:01] a"
        assert data["notulen_md"] == "# Notulen"
        dgn_nt.assert_called_once_with("[00:01] a", judul="Rapat Harian", daftar_peserta=None)

    def test_proses_mode_single(self):
        with (
            patch.object(zn, "transkripsi_audio", return_value="teks"),
            patch.object(zn, "buat_notulen", return_value="## Notulen single") as dgn,
        ):
            res = client.post(
                "/proses",
                files={"file": ("x.wav", io.BytesIO(b"RIFF"), "audio/wav")},
                data={"mode": "single"},
            )
        assert res.status_code == 200
        assert res.json()["notulen_md"] == "## Notulen single"
