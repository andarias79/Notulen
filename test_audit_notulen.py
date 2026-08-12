"""Tes unit untuk audit_notulen.py.

Menjalankan:
    python -m unittest test_audit_notulen

Tes TIDAK membutuhkan dependensi pihak ketiga (semua di-mock) dan tidak memanggil
API Gemini - hanya memvalidasi logika pemetaan transkrip & pemindaian file.
"""

import os
import sys
import tempfile
import types
import unittest
from unittest import mock

# ---------------------------------------------------------------------------
# Mock dependensi berat SEBELUM import zoom_notulen (sama seperti test_zoom_notulen)
# ---------------------------------------------------------------------------
def _buat_modul(nama, **atribut):
    mod = types.ModuleType(nama)
    for k, v in atribut.items():
        setattr(mod, k, v)
    sys.modules[nama] = mod
    return mod


_buat_modul("dotenv", load_dotenv=lambda *a, **k: None)
_buat_modul("sounddevice", rec=lambda *a, **k: None, wait=lambda *a, **k: None)
_buat_modul("soundfile", write=lambda *a, **k: None)
_buat_modul("faster_whisper", WhisperModel=object)


class _Config:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


genai_mod = _buat_modul("google.genai", Client=lambda *a, **k: None)
_buat_modul("google.genai.types", GenerateContentConfig=_Config)
_buat_modul("google", genai=genai_mod, __path__=[])

os.environ["GEMINI_API_KEY"] = "kunci-pengujian"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import audit_notulen  # noqa: E402


class TestPolaStempel(unittest.TestCase):
    def test_ekstrak_stempel_dari_nama_file(self):
        cocok = audit_notulen.POLA_STEMPEL.search("Notulen_Rapat_20260811_174600.md")
        self.assertIsNotNone(cocok)
        self.assertEqual(cocok.group(1), "20260811_174600")

    def test_nama_file_tanpa_stempel_tidak_cocok(self):
        self.assertIsNone(audit_notulen.POLA_STEMPEL.search("README.md"))


class TestTranskripUntuk(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="audit_test_")
        self.cwd_sebelum = os.getcwd()
        os.chdir(self.dir)

    def tearDown(self):
        os.chdir(self.cwd_sebelum)

    def _tulis(self, nama, isi=""):
        subfolder = "Hasil_Transkripsi" if nama.startswith("Transkrip") else "Hasil_Notulen"
        p = os.path.join(self.dir, subfolder)
        os.makedirs(p, exist_ok=True)
        with open(os.path.join(p, nama), "w", encoding="utf-8") as f:
            f.write(isi)
        return nama

    def test_stempel_sama_dipakai(self):
        self._tulis("Transkrip_20260811_123456.txt")
        self.assertEqual(
            audit_notulen.transkrip_untuk("20260811_123456"), os.path.join("Hasil_Transkripsi", "Transkrip_20260811_123456.txt")
        )

    def test_peta_manual_untuk_notulen_regenerasi(self):
        """Notulen baru (174600) memakai transkrip lama (121046) via PEMETAAN_TRANSKRIP."""
        self._tulis("Transkrip_20260810_121046.txt")
        self.assertEqual(
            audit_notulen.transkrip_untuk("20260811_174600"), os.path.join("Hasil_Transkripsi", "Transkrip_20260810_121046.txt")
        )

    def test_transkrip_tidak_ada_mengembalikan_none(self):
        self.assertIsNone(audit_notulen.transkrip_untuk("20260811_999999"))


class TestAuditSemua(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="audit_test_")
        self.cwd_sebelum = os.getcwd()
        os.chdir(self.dir)

    def tearDown(self):
        os.chdir(self.cwd_sebelum)

    def _tulis(self, nama, isi=""):
        subfolder = "Hasil_Transkripsi" if nama.startswith("Transkrip") else "Hasil_Notulen"
        p = os.path.join(self.dir, subfolder)
        os.makedirs(p, exist_ok=True)
        with open(os.path.join(p, nama), "w", encoding="utf-8") as f:
            f.write(isi)
        return nama

    def test_notulen_dengan_nama_halusinasi_terdeteksi(self):
        """Action Items memakai 'Budi' padahal transkrip hanya 'Pembicara 1'."""
        self._tulis(
            "Transkrip_20260811_123456.txt",
            "[00:00] Pembicara 1: Halo semua.",
        )
        self._tulis(
            "Notulen_Rapat_20260811_123456.md",
            "# Notulen\n## Action Items / Tindak Lanjut\n"
            "| No | PIC | Tindak Lanjut |\n|---|---|---|\n| 1 | Budi | Cek dokumen. |\n",
        )
        hasil = audit_notulen.audit_semua()
        self.assertEqual(len(hasil), 1)
        self.assertEqual(hasil[0]["status"], "HALUSINASI")
        self.assertIn("Budi", hasil[0]["nama"])

    def test_notulen_bersih_tidak_dilaporkan(self):
        self._tulis(
            "Transkrip_20260811_123457.txt",
            "[00:00] Pembicara 1: Siti akan memproses draf.",
        )
        self._tulis(
            "Notulen_Rapat_20260811_123457.md",
            "# Notulen\n## Action Items / Tindak Lanjut\n"
            "- Siti: memproses draf kerja sama\n",
        )
        hasil = audit_notulen.audit_semua()
        self.assertEqual(len(hasil), 1)
        self.assertEqual(hasil[0]["status"], "BERSIH")

    def test_notulen_tanpa_transkrip_ditandai(self):
        self._tulis("Notulen_Rapat_20260811_123458.md", "# Notulen\n## Action Items\n- X: tugas")
        hasil = audit_notulen.audit_semua()
        self.assertEqual(len(hasil), 1)
        self.assertEqual(hasil[0]["status"], "TANPA-TRANSKRIP")


if __name__ == "__main__":
    unittest.main()
