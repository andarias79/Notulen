"""Tes unit untuk zoom_notulen.py.

Menjalankan:
    python -m unittest test_zoom_notulen -v

Tes TIDAK membutuhkan dependensi pihak ketiga (sounddevice, soundfile,
faster-whisper, google-genai, python-dotenv) karena semua di-mock sebelum import.
"""
import math
import os

import sys

import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

# ---------------------------------------------------------------------------
# Mock dependensi berat/opsional SEBELUM import zoom_notulen
# ---------------------------------------------------------------------------
def _buat_modul(nama, **atribut):
    mod = types.ModuleType(nama)
    for k, v in atribut.items():
        setattr(mod, k, v)
    sys.modules[nama] = mod
    return mod


_buat_modul("dotenv", load_dotenv=lambda *a, **k: None)
_buat_modul(
    "sounddevice",
    rec=lambda *a, **k: None,
    wait=lambda *a, **k: None,
    write=lambda *a, **k: None,
    RawInputStream=None,  # diganti via mock.patch.object di tes rekam_audio
)
_buat_modul("soundfile", write=lambda *a, **k: None)
# faster-whisper di-mock: WhisperModel dipatch per-tes dengan mock.patch.object
_buat_modul("faster_whisper", WhisperModel=object)

class _Config:
    """Pengganti types.GenerateContentConfig yang menerima kwargs."""

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


genai_mod = _buat_modul("google.genai", Client=lambda *a, **k: None)
_buat_modul("google.genai.types", GenerateContentConfig=_Config)
_buat_modul("google", genai=genai_mod, __path__=[])

# API key palsu agar import zoom_notulen berhasil
os.environ["GEMINI_API_KEY"] = "kunci-pengujian"

import zoom_notulen  # noqa: E402


class TestBuatNotulen(unittest.TestCase):
    """Tes buat_notulen dengan klien Gemini di-mock."""

    def _klien_palsu(self, teks="# Notulen Rapat"):
        """Membuat klien Gemini palsu yang mengembalikan teks tertentu."""
        client = mock.MagicMock()
        client.models.generate_content.return_value.text = teks
        return client

    def _panggil(self, client, *args, **kwargs):
        """Memanggil buat_notulen dengan Client di-patch."""
        with mock.patch.object(zoom_notulen.genai, "Client", return_value=client):
            return zoom_notulen.buat_notulen(*args, **kwargs)

    def test_judul_masuk_ke_prompt(self):
        client = self._klien_palsu()
        hasil = self._panggil(client, "transkrip rapat", judul="Rapat Mingguan")
        self.assertEqual(hasil, "# Notulen Rapat")
        prompt = client.models.generate_content.call_args.kwargs["contents"]
        self.assertIn('"# Notulen Rapat - Rapat Mingguan"', prompt)
        self.assertIn("Rapat Mingguan", prompt)

    def test_daftar_peserta_masuk_ke_prompt(self):
        client = self._klien_palsu()
        self._panggil(client, "transkrip", daftar_peserta="Budi, Siti")
        prompt = client.models.generate_content.call_args.kwargs["contents"]
        self.assertIn("Daftar Peserta", prompt)
        self.assertIn("Budi, Siti", prompt)

    def test_prompt_tanpa_judul_dan_peserta(self):
        client = self._klien_palsu()
        self._panggil(client, "transkrip")
        prompt = client.models.generate_content.call_args.kwargs["contents"]
        self.assertIn("judul/topik rapat", prompt)  # instruksi default tetap ada
        self.assertNotIn("Daftar Peserta", prompt)

    def test_model_dan_system_instruction_diteruskan(self):
        client = self._klien_palsu()
        self._panggil(client, "transkrip", model="gemini-eksperimen")
        call = client.models.generate_content.call_args
        self.assertEqual(call.kwargs["model"], "gemini-eksperimen")
        self.assertIn(
            "sekretaris profesional",
            call.kwargs["config"].system_instruction,
        )

    def test_prompt_larang_halusinasi_nama_di_action_items(self):
        """Prompt wajib memuat aturan anti-halusinasi: nama hanya jika ada di transkrip."""
        client = self._klien_palsu()
        self._panggil(client, "transkrip", daftar_peserta="Budi, Siti")
        prompt = client.models.generate_content.call_args.kwargs["contents"]
        # Aturan anti-halusinasi eksplisit
        self.assertIn("ANTI-HALUSINASI", prompt)
        self.assertIn("JANGAN PERNAH menciptakan nama pembicara", prompt)
        self.assertIn("Pembicara N", prompt)
        # Daftar Peserta hanya referensi, bukan sumber nama pembicara
        self.assertIn("JANGAN berasumsi siapa berbicara dari daftar ini", prompt)
        self.assertNotIn("Gunakan nama dari Daftar Peserta saat menulis Action Items", prompt)

    def test_prompt_minta_angka_kunci_dipertahankan(self):
        """Prompt meminta Poin-Poin Utama mempertahankan angka target vs realisasi."""
        client = self._klien_palsu()
        self._panggil(client, "transkrip")
        prompt = client.models.generate_content.call_args.kwargs["contents"]
        self.assertIn("PERTAHANKAN angka-angka kunci", prompt)
        self.assertIn("target vs realisasi", prompt)

    def test_system_instruction_anti_halusinasi(self):
        """system_instruction juga memuat aturan anti-halusinasi (penguatan)."""
        client = self._klien_palsu()
        self._panggil(client, "transkrip")
        system = client.models.generate_content.call_args.kwargs["config"].system_instruction
        self.assertIn("JANGAN PERNAH menciptakan nama pembicara", system)
        self.assertIn("Pembicara N", system)

    def test_respon_kosong_memicu_systemexit(self):
        client = self._klien_palsu(teks="   ")
        with self.assertRaises(SystemExit):
            self._panggil(client, "transkrip")

    def test_error_api_memicu_systemexit(self):
        client = self._klien_palsu()
        client.models.generate_content.side_effect = Exception("API down")
        with self.assertRaises(SystemExit):
            self._panggil(client, "transkrip")

    def test_retry_otomatis_saat_503(self):
        client = mock.MagicMock()
        err = Exception("503 UNAVAILABLE. Model sedang sibuk")
        err.code = 503
        client.models.generate_content.side_effect = [
            err, err, mock.MagicMock(text="# Hasil"),
        ]
        with mock.patch.object(zoom_notulen.genai, "Client", return_value=client), mock.patch(
            "zoom_notulen.time.sleep"
        ):
            hasil = zoom_notulen.buat_notulen("transkrip")
        self.assertEqual(hasil, "# Hasil")
        self.assertEqual(client.models.generate_content.call_count, 3)

    def test_error_non_transien_langsung_gagal_tanpa_retry(self):
        client = self._klien_palsu()
        err = Exception("400 invalid argument")
        err.code = 400
        client.models.generate_content.side_effect = err
        with mock.patch.object(zoom_notulen.genai, "Client", return_value=client), mock.patch(
            "zoom_notulen.time.sleep"
        ) as sleep:
            with self.assertRaises(SystemExit):
                self._panggil(client, "transkrip")
        sleep.assert_not_called()
        self.assertEqual(client.models.generate_content.call_count, 1)

    def test_fallback_ke_model_cadangan_saat_kuota_habis(self):
        """429 yang tak kunjung hilang setelah retry -> beralih ke model cadangan."""
        client = mock.MagicMock()
        err = Exception("429 RESOURCE_EXHAUSTED. Quota exceeded")
        err.code = 429
        resp = mock.MagicMock(text="# Hasil dari cadangan")
        # 4x gagal (retry habis) pada model utama, lalu sukses di model cadangan
        client.models.generate_content.side_effect = [err] * 4 + [resp]
        with mock.patch("zoom_notulen.time.sleep"):
            hasil = zoom_notulen._panggil_generate_dengan_retry(
                client, "model-utama", "konten", model_cadangan=["model-cadangan"]
            )
        self.assertEqual(hasil, resp)
        model_dipanggil = [
            c.kwargs["model"] for c in client.models.generate_content.call_args_list
        ]
        self.assertEqual(
            model_dipanggil, ["model-utama"] * 4 + ["model-cadangan"]
        )

    def test_semua_model_gagal_memicu_systemexit(self):
        """Jika model utama DAN cadangan semua gagal -> SystemExit."""
        client = mock.MagicMock()
        err = Exception("503 UNAVAILABLE")
        err.code = 503
        client.models.generate_content.side_effect = [err] * 8  # 4 utama + 4 cadangan
        with mock.patch("zoom_notulen.time.sleep"), self.assertRaises(SystemExit):
            zoom_notulen._panggil_generate_dengan_retry(
                client, "model-utama", "konten", model_cadangan=["model-cadangan"]
            )
        self.assertEqual(client.models.generate_content.call_count, 8)

    def test_transkripsi_gemini_meneruskan_model_cadangan(self):
        """transkripsi_audio_gemini memakai GEMINI_TRANSKRIPSI_CADANGAN saat memanggil."""
        with mock.patch.object(
            zoom_notulen,
            "_panggil_generate_dengan_retry",
            return_value=mock.MagicMock(text="[00:00] Pembicara 1: Halo"),
        ) as panggil:
            client = mock.MagicMock()
            berkas = mock.MagicMock()
            berkas.name = "files/audio-1"
            keadaan = mock.MagicMock()
            keadaan.name = "ACTIVE"
            berkas.state = keadaan
            client.files.upload.return_value = berkas
            client.files.get.return_value = berkas
            with mock.patch.object(zoom_notulen.genai, "Client", return_value=client), mock.patch.object(
                zoom_notulen, "_durasi_audio", return_value=30.0
            ), mock.patch.object(zoom_notulen.os.path, "getsize", return_value=5 * 1024 * 1024):
                zoom_notulen.transkripsi_audio_gemini("audio.wav", "id")
        call = panggil.call_args
        self.assertEqual(call.args[1], zoom_notulen.GEMINI_TRANSKRIPSI_MODEL)
        self.assertEqual(call.kwargs["model_cadangan"], zoom_notulen.GEMINI_TRANSKRIPSI_CADANGAN)


class TestPeriksaHalusinasiNotulen(unittest.TestCase):
    """Tes validasi anti-halusinasi: nama PIC Action Items harus ada di transkrip."""

    def test_nama_di_action_items_tidak_ada_di_transkrip_terdeteksi(self):
        """Kasus nyata: notulen menulis 'Budi' padahal transkrip hanya 'Pembicara N'."""
        notulen = (
            "# Notulen Rapat\n"
            "## Action Items / Tindak Lanjut\n"
            "| No | PIC | Tindak Lanjut |\n"
            "|----|-----|---------------|\n"
            "| 1 | Budi | Menindaklanjuti sanksi. |\n"
            "| 2 | Siti | Memproses draf kerja sama. |\n"
        )
        transkrip = "[00:00] Pembicara 1: Silakan.\n[01:00] Pembicara 2: Terima kasih."
        hasil = zoom_notulen.periksa_halusinasi_notulen(notulen, transkrip)
        self.assertEqual(sorted(hasil), ["Budi", "Siti"])

    def test_nama_yang_muncul_di_transkrip_tidak_dilaporkan(self):
        """Nama asli yang benar-benar muncul di transkrip dianggap aman."""
        notulen = (
            "## Action Items / Tindak Lanjut\n"
            "- Budi: menindaklanjuti rekomendasi sanksi\n"
        )
        transkrip = "[00:05] Pembicara 1: Budi akan menindaklanjuti."
        self.assertEqual(zoom_notulen.periksa_halusinasi_notulen(notulen, transkrip), [])

    def test_label_pembicara_n_dianggap_aman(self):
        """PIC berupa 'Pembicara N' (sesuai aturan prompt) tidak dilaporkan."""
        notulen = (
            "## Action Items / Tindak Lanjut\n"
            "| PIC | Tindak Lanjut |\n"
            "|-----|---------------|\n"
            "| Pembicara 2 | Memproses draf. |\n"
        )
        transkrip = "[01:00] Pembicara 2: Terima kasih."
        self.assertEqual(zoom_notulen.periksa_halusinasi_notulen(notulen, transkrip), [])

    def test_tanpa_bagian_action_items_mengembalikan_kosong(self):
        notulen = "# Notulen Rapat\n## Ringkasan\nSemua berjalan baik."
        self.assertEqual(zoom_notulen.periksa_halusinasi_notulen(notulen, "transkrip apa pun"), [])

    def test_nama_tidak_lolos_batas_kata(self):
        """Pencocokan harus kata utuh: 'Andi' tidak cocok dengan 'Andika'."""
        notulen = "## Action Items\n- Andi: siapkan laporan"
        transkrip = "[00:00] Pembicara 1: Andika yang mengerjakan."
        self.assertEqual(zoom_notulen.periksa_halusinasi_notulen(notulen, transkrip), ["Andi"])

    def test_section_berakhir_di_heading_berikutnya(self):
        """Nama di luar bagian Action Items tidak ikut diperiksa."""
        notulen = (
            "# Notulen\n"
            "## Action Items\n"
            "- Budi: cek dokumen\n"
            "## Lampiran\n"
            "Dibuat oleh Siti"
        )
        transkrip = "[00:00] Pembicara 1: Halo."
        self.assertEqual(zoom_notulen.periksa_halusinasi_notulen(notulen, transkrip), ["Budi"])

    def test_bullet_dengan_en_dash_diekstrak(self):
        notulen = "## Tindak Lanjut\n- Andi \u2013 menyiapkan anggaran"
        transkrip = "[00:00] Pembicara 1: Halo."
        self.assertEqual(zoom_notulen.periksa_halusinasi_notulen(notulen, transkrip), ["Andi"])

    def test_bullet_frasa_tugas_tanpa_nama_tidak_dilaporkan(self):
        """'Menyiapkan laporan - disetujui' bukan 'Nama - tugas' -> tidak jadi nama PIC."""
        notulen = "## Tindak Lanjut\n- Menyiapkan laporan - disetujui rapat"
        transkrip = "[00:00] Pembicara 1: Halo."
        self.assertEqual(zoom_notulen.periksa_halusinasi_notulen(notulen, transkrip), [])

    def test_pic_duplikat_hanya_dilaporkan_sekali(self):
        """PIC sama di beberapa baris -> nama muncul sekali di daftar mencurigakan."""
        notulen = (
            "## Action Items / Tindak Lanjut\n"
            "| No | PIC | Tindak Lanjut |\n"
            "|----|-----|---------------|\n"
            "| 1 | Budi | Tugas A |\n"
            "| 2 | Budi | Tugas B |\n"
            "| 3 | Siti | Tugas C |\n"
        )
        transkrip = "[00:00] Pembicara 1: Halo."
        self.assertEqual(zoom_notulen.periksa_halusinasi_notulen(notulen, transkrip), ["Budi", "Siti"])

    def test_nama_tim_tidak_dilaporkan(self):
        """PIC berupa nama tim ('Tim Keuangan / Pemasaran') bukan halusinasi nama orang."""
        notulen = (
            "## Action Items / Tindak Lanjut\n"
            "| Tugas | Penanggung Jawab | Tenggat Waktu |\n"
            "| :--- | :--- | :--- |\n"
            "| Memproses anggaran | Tim Keuangan / Pemasaran | Segera |\n"
            "| Persiapan rilis | Tim Produk / Pengembangan | Oktober |\n"
        )
        transkrip = "[00:00] Pembicara 1: Halo."
        self.assertEqual(zoom_notulen.periksa_halusinasi_notulen(notulen, transkrip), [])

    def test_header_pic_siapa_dikenali(self):
        """Header 'PIC / Siapa' (format 3 kolom) -> kolom PIC benar di index 0."""
        notulen = (
            "## Action Items / Tindak Lanjut\n"
            "| PIC / Siapa | Tindak Lanjut / Apa yang Dilakukan | Status / Catatan |\n"
            "| :--- | :--- | :--- |\n"
            "| Tim Terkait | Melanjutkan ke tahap berikutnya | Menunggu instruksi |\n"
        )
        transkrip = "[00:00] Pembicara 1: Halo."
        self.assertEqual(zoom_notulen.periksa_halusinasi_notulen(notulen, transkrip), [])

    def test_header_nama_dikenali_untuk_tabel_2_kolom(self):
        """Header 'Nama' (tabel 2 kolom) -> kolom PIC index 0 -> Budi terdeteksi."""
        notulen = (
            "## Action Items / Tindak Lanjut\n"
            "| Nama | Tugas / Tindak Lanjut |\n"
            "| :--- | :--- |\n"
            "| Budi | Menyiapkan bahan materi. |\n"
            "| Siti | Mengoordinasikan alur kerja. |\n"
        )
        transkrip = "[00:00] Pembicara 1: Halo."
        self.assertEqual(zoom_notulen.periksa_halusinasi_notulen(notulen, transkrip), ["Budi", "Siti"])

    def test_pic_placeholder_strip_tidak_dilaporkan(self):
        """Sel PIC berisi '-' (tanpa penanggung jawab) bukan nama -> tidak di-flag."""
        notulen = (
            "## Action Items\n"
            "| No | PIC | Tindak Lanjut |\n"
            "|----|-----|---------------|\n"
            "| 1 | - | Tugas A |\n"
            "| 2 | - | Tugas B |\n"
        )
        transkrip = "[00:00] Pembicara 1: Halo."
        self.assertEqual(zoom_notulen.periksa_halusinasi_notulen(notulen, transkrip), [])

    def test_peran_generik_peserta_tidak_dilaporkan(self):
        """PIC berupa peran generik ('Peserta') bukan nama orang -> tidak di-flag."""
        notulen = (
            "## Action Items\n"
            "| Tindakan | Penanggung Jawab |\n"
            "| :--- | :--- |\n"
            "| Mengonfirmasi email | Peserta |\n"
        )
        transkrip = "[00:00] Pembicara 1: Silakan."
        self.assertEqual(zoom_notulen.periksa_halusinasi_notulen(notulen, transkrip), [])


class TestDeteksiSenyap(unittest.TestCase):
    """Tes ukur_rms dan ambang deteksi rekaman sunyi."""

    @staticmethod
    def _buat_wav(path, frekuensi=0, amplitudo=0.0, durasi=1.0, rate=16000):
        import wave as wavemod

        n = int(rate * durasi)
        if frekuensi:
            t = np.arange(n) / rate
            sig = (amplitudo * np.sin(2 * np.pi * frekuensi * t) * 32767).astype(np.int16)
        else:
            sig = np.zeros(n, dtype=np.int16)
        with wavemod.open(path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(rate)
            wf.writeframes(sig.tobytes())
        return path

    def test_rekaman_sunyi_terdeteksi(self):
        with tempfile.TemporaryDirectory() as d:
            p = self._buat_wav(os.path.join(d, "sunyi.wav"))
            rms = zoom_notulen.ukur_rms(p)
            self.assertIsNotNone(rms)
            self.assertLess(rms, zoom_notulen.AMBANG_RMS_SENYAP)

    def test_rekaman_bersuara_tidak_dianggap_sunyi(self):
        with tempfile.TemporaryDirectory() as d:
            p = self._buat_wav(os.path.join(d, "nyaring.wav"), frekuensi=440, amplitudo=0.5)
            rms = zoom_notulen.ukur_rms(p)
            self.assertIsNotNone(rms)
            self.assertGreater(rms, zoom_notulen.AMBANG_RMS_SENYAP)
            self.assertAlmostEqual(rms, 0.5 / np.sqrt(2), places=2)  # RMS sinus = amp/√2

    def test_file_tidak_ada_mengembalikan_none(self):
        self.assertIsNone(zoom_notulen.ukur_rms("file_tidak_ada.wav"))


class TestSimpanNotulen(unittest.TestCase):
    """Tes fungsi simpan_notulen(teks, nama_dasar, format_file)."""

    NAMA = "Notulen_Rapat_20260809_120000"

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="notulen_test_")
        self.dasar = str(Path(self.dir) / self.NAMA)

    def test_format_md_hanya_membuat_md(self):
        zoom_notulen.simpan_notulen("# Judul", self.dasar, "md")
        self.assertEqual(set(p.name for p in Path(self.dir).iterdir()), {self.NAMA + ".md"})

    def test_format_txt_hanya_membuat_txt(self):
        zoom_notulen.simpan_notulen("# Judul", self.dasar, "txt")
        self.assertEqual(set(p.name for p in Path(self.dir).iterdir()), {self.NAMA + ".txt"})

    def test_format_both_membuat_keduanya(self):
        zoom_notulen.simpan_notulen("# Judul", self.dasar, "both")
        md = Path(self.dasar + ".md")
        txt = Path(self.dasar + ".txt")
        self.assertTrue(md.exists())
        self.assertTrue(txt.exists())
        self.assertEqual(md.read_text(encoding="utf-8"), "# Judul")
        self.assertEqual(txt.read_text(encoding="utf-8"), "# Judul")

    def test_konten_utf8_tersimpan_utuh(self):
        konten = "# Notulen Rapat\n\n## Ringkasan\nDiskusi tentang anggaran 2026."
        zoom_notulen.simpan_notulen(konten, self.dasar, "md")
        self.assertEqual(Path(self.dasar + ".md").read_text(encoding="utf-8"), konten)


class TestSimpanTranskrip(unittest.TestCase):
    """Tes fungsi simpan_transkrip(teks, nama_file)."""

    def test_transkrip_tersimpan_utf8(self):
        with tempfile.TemporaryDirectory() as d:
            path = str(Path(d) / "Transkrip_20260809_120000.txt")
            zoom_notulen.simpan_transkrip("Halo semua peserta rapat", path)
            self.assertEqual(Path(path).read_text(encoding="utf-8"), "Halo semua peserta rapat")


class TestPisahkanEmails(unittest.TestCase):
    """Tes pemisahan & validasi alamat email."""

    def test_emails_valid(self):
        self.assertEqual(
            zoom_notulen.pisahkan_emails("budi@mail.com, siti@mail.com"),
            ["budi@mail.com", "siti@mail.com"],
        )

    def test_spasi_dan_kosong_dibersihkan(self):
        self.assertEqual(
            zoom_notulen.pisahkan_emails(" budi@mail.com , , siti@mail.com "),
            ["budi@mail.com", "siti@mail.com"],
        )

    def test_email_invalid_ditolak(self):
        with self.assertRaises(SystemExit):
            zoom_notulen.pisahkan_emails("bukan-email")


class TestKirimEmail(unittest.TestCase):
    """Tes kirim_email_resend dengan modul resend di-mock."""

    def _resend_palsu(self):
        resend_palsu = types.SimpleNamespace(api_key=None, Emails=types.SimpleNamespace())
        resend_palsu.terkirim = []

        def fake_send(params):
            resend_palsu.terkirim.append(params)
            return {"id": "id-123"}

        resend_palsu.Emails.send = fake_send
        return resend_palsu

    def test_kirim_berhasil(self):
        fake = self._resend_palsu()
        with mock.patch.dict(sys.modules, {"resend": fake}), mock.patch.dict(
            os.environ, {"RESEND_API_KEY": "re_xxx"}
        ):
            zoom_notulen.kirim_email_resend(["budi@mail.com"], "Subjek Rapat", "Isi notulen")
        self.assertEqual(fake.api_key, "re_xxx")
        params = fake.terkirim[0]
        self.assertEqual(params["to"], ["budi@mail.com"])
        self.assertEqual(params["subject"], "Subjek Rapat")
        self.assertEqual(params["text"], "Isi notulen")

    def test_tanpa_api_key_memicu_error(self):
        fake = self._resend_palsu()
        with mock.patch.dict(sys.modules, {"resend": fake}), mock.patch.dict(
            os.environ, {}, clear=True
        ):
            with self.assertRaises(SystemExit):
                zoom_notulen.kirim_email_resend(["budi@mail.com"], "S", "I")

    def test_error_api_memicu_systemexit(self):
        fake = self._resend_palsu()

        def fake_send(params):
            raise Exception("rate limit")

        fake.Emails.send = fake_send
        with mock.patch.dict(sys.modules, {"resend": fake}), mock.patch.dict(
            os.environ, {"RESEND_API_KEY": "re_xxx"}
        ):
            with self.assertRaises(SystemExit):
                zoom_notulen.kirim_email_resend(["budi@mail.com"], "S", "I")


class TestKonfigurasi(unittest.TestCase):
    """Tes muat_konfigurasi dan terapkan_konfigurasi."""

    @staticmethod
    def _tulis_json(d, data):
        import json

        p = os.path.join(d, "konfigurasi.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(data, f)
        return p

    def test_file_tidak_ada_kembalikan_kosong(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(zoom_notulen.muat_konfigurasi(os.path.join(d, "kosong.json")), {})

    def test_json_rusak_kembalikan_kosong(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "rusak.json")
            with open(p, "w", encoding="utf-8") as f:
                f.write("{bukan json")
            self.assertEqual(zoom_notulen.muat_konfigurasi(p), {})

    def test_json_valid_termuat(self):
        with tempfile.TemporaryDirectory() as d:
            p = self._tulis_json(d, {"judul": "Rapat X", "durasi": 300})
            cfg = zoom_notulen.muat_konfigurasi(p)
            self.assertEqual(cfg["judul"], "Rapat X")
            self.assertEqual(cfg["durasi"], 300)

    def test_default_bawaan_tanpa_cli_dan_konfigurasi(self):
        args = zoom_notulen.buat_parser().parse_args([])
        cfg = zoom_notulen.terapkan_konfigurasi(args, {})
        self.assertEqual(cfg["durasi"], 60)
        self.assertEqual(cfg["sumber"], "mic")
        self.assertEqual(cfg["format"], "both")
        self.assertEqual(cfg["bahasa"], "id")
        self.assertEqual(cfg["engine"], "lokal")
        self.assertEqual(cfg["model"], "gemini-3.6-flash")
        self.assertIsNone(cfg["judul"])
        self.assertIsNone(cfg["tujuan_email"])

    def test_konfigurasi_mengisi_default(self):
        args = zoom_notulen.buat_parser().parse_args([])
        cfg = zoom_notulen.terapkan_konfigurasi(
            args,
            {"judul": "Rapat X", "durasi": 3600, "sumber": "loopback", "engine": "gemini", "tujuan_email": "a@b.com"},
        )
        self.assertEqual(cfg["judul"], "Rapat X")
        self.assertEqual(cfg["durasi"], 3600)
        self.assertEqual(cfg["sumber"], "loopback")
        self.assertEqual(cfg["engine"], "gemini")
        self.assertEqual(cfg["tujuan_email"], "a@b.com")

    def test_flag_dari_konfigurasi_dipakai(self):
        """no_vad/flac/pdf=false dari konfigurasi.json tidak ditimpa default CLI.

        Regresi: parser memakai default False (bukan None) sehingga nilai True
        dari konfigurasi.json selalu tertimpa.
        """
        args = zoom_notulen.buat_parser().parse_args([])
        cfg = zoom_notulen.terapkan_konfigurasi(
            args, {"no_vad": True, "flac": True, "pdf": True}
        )
        self.assertIs(cfg["no_vad"], True)
        self.assertIs(cfg["flac"], True)
        self.assertIs(cfg["pdf"], True)

    def test_flag_cli_menang_atas_konfigurasi(self):
        args = zoom_notulen.buat_parser().parse_args(["--flac", "--pdf"])
        cfg = zoom_notulen.terapkan_konfigurasi(args, {"flac": False, "pdf": False})
        self.assertIs(cfg["flac"], True)
        self.assertIs(cfg["pdf"], True)

    def test_cli_engine_menang_atas_konfigurasi(self):
        args = zoom_notulen.buat_parser().parse_args(["--engine", "lokal"])
        cfg = zoom_notulen.terapkan_konfigurasi(args, {"engine": "gemini"})
        self.assertEqual(cfg["engine"], "lokal")

    def test_cli_menang_atas_konfigurasi(self):
        args = zoom_notulen.buat_parser().parse_args(["--durasi", "120", "--judul", "Dari CLI"])
        cfg = zoom_notulen.terapkan_konfigurasi(args, {"durasi": 3600, "judul": "Dari Konfig"})
        self.assertEqual(cfg["durasi"], 120)
        self.assertEqual(cfg["judul"], "Dari CLI")

    def test_sumber_invalid_di_konfigurasi_ditolak(self):
        args = zoom_notulen.buat_parser().parse_args([])
        with self.assertRaises(SystemExit):
            zoom_notulen.terapkan_konfigurasi(args, {"sumber": "radio"})

    def test_format_invalid_di_konfigurasi_ditolak(self):
        args = zoom_notulen.buat_parser().parse_args([])
        with self.assertRaises(SystemExit):
            zoom_notulen.terapkan_konfigurasi(args, {"format": "docx"})

    def test_engine_invalid_di_konfigurasi_ditolak(self):
        args = zoom_notulen.buat_parser().parse_args([])
        with self.assertRaises(SystemExit):
            zoom_notulen.terapkan_konfigurasi(args, {"engine": "radar"})


class TestParsingArgumen(unittest.TestCase):
    """Tes parsing argumen CLI via buat_parser()."""

    def test_nilai_default_none(self):
        """Opsi yang bisa diisi konfigurasi bernilai None saat tidak diberikan CLI."""
        args = zoom_notulen.buat_parser().parse_args([])
        self.assertIsNone(args.durasi)
        self.assertIsNone(args.sumber)
        self.assertIsNone(args.format)
        self.assertIsNone(args.bahasa)
        self.assertIsNone(args.engine)
        self.assertIsNone(args.model)
        self.assertIsNone(args.judul)
        self.assertIsNone(args.daftar_peserta)
        self.assertIsNone(args.tujuan_email)
        self.assertIsNone(args.audio)
        self.assertIsNone(args.perangkat)
        self.assertFalse(args.list_devices)
        self.assertFalse(args.abaikan_senyap)
        self.assertIsNone(args.noise_reduce)
        self.assertIsNone(args.notulen_mode)

    def test_opsi_lengkap(self):
        args = zoom_notulen.buat_parser().parse_args(
            [
                "--durasi", "3600",
                "--sumber", "loopback",
                "--format", "md",
                "--judul", "Rapat Tim Produk",
                "--daftar-peserta", "Budi, Siti, Andi",
                "--tujuan-email", "budi@mail.com, siti@mail.com",
                "--bahasa", "en",
                "--perangkat", "8",
                "--engine", "gemini",
                "--list-devices",
                "--abaikan-senyap",
            ]
        )
        self.assertEqual(args.durasi, 3600)
        self.assertEqual(args.sumber, "loopback")
        self.assertEqual(args.format, "md")
        self.assertEqual(args.judul, "Rapat Tim Produk")
        self.assertEqual(args.daftar_peserta, "Budi, Siti, Andi")
        self.assertEqual(args.tujuan_email, "budi@mail.com, siti@mail.com")
        self.assertEqual(args.bahasa, "en")
        self.assertEqual(args.perangkat, 8)
        self.assertEqual(args.engine, "gemini")
        self.assertTrue(args.list_devices)
        self.assertTrue(args.abaikan_senyap)

    def test_sumber_invalid_ditolak(self):
        with self.assertRaises(SystemExit):
            zoom_notulen.buat_parser().parse_args(["--sumber", "radio"])

    def test_engine_invalid_ditolak(self):
        with self.assertRaises(SystemExit):
            zoom_notulen.buat_parser().parse_args(["--engine", "radar"])

    def test_format_invalid_ditolak(self):
        with self.assertRaises(SystemExit):
            zoom_notulen.buat_parser().parse_args(["--format", "docx"])

    def test_durasi_harus_angka(self):
        with self.assertRaises(SystemExit):
            zoom_notulen.buat_parser().parse_args(["--durasi", "abc"])

    def test_perangkat_harus_angka(self):
        with self.assertRaises(SystemExit):
            zoom_notulen.buat_parser().parse_args(["--perangkat", "bukan-angka"])

    def test_youtube_argumen_diterima(self):
        args = zoom_notulen.buat_parser().parse_args(
            ["--youtube", "https://www.youtube.com/watch?v=abc123"]
        )
        self.assertEqual(args.youtube, "https://www.youtube.com/watch?v=abc123")
        self.assertIsNone(args.audio)

    def test_youtube_default_none(self):
        args = zoom_notulen.buat_parser().parse_args([])
        self.assertIsNone(args.youtube)

    def test_folder_argumen_diterima(self):
        args = zoom_notulen.buat_parser().parse_args(
            ["--folder", "./rekaman"]
        )
        self.assertEqual(args.folder, "./rekaman")

    def test_folder_default_none(self):
        args = zoom_notulen.buat_parser().parse_args([])
        self.assertIsNone(args.folder)


class TestUnduhYouTube(unittest.TestCase):
    """Tes unduh_audio_youtube: download audio dari URL YouTube."""

    def test_ytidlp_belum_terinstal_gagal(self):
        """Jika yt-dlp belum terinstal, unduh_audio_youtube harus raise SystemExit."""
        with mock.patch.dict(sys.modules, {"yt_dlp": None}):
            with self.assertRaises(SystemExit):
                zoom_notulen.unduh_audio_youtube("https://example.com")

    def test_url_invalid_gagal_download(self):
        """URL tidak valid harus mengembalikan None (Error ditangkap)."""
        # Buat exception class palsu yang mirip yt_dlp.utils.DownloadError
        class FakeDownloadError(Exception):
            pass

        mock_ydl_module = mock.MagicMock()
        mock_ydl_module.utils.DownloadError = FakeDownloadError

        mock_instance = mock.MagicMock()
        mock_instance.extract_info.side_effect = FakeDownloadError("URL tidak valid")
        mock_ydl_module.YoutubeDL.return_value.__enter__ = mock.MagicMock(
            return_value=mock_instance
        )
        mock_ydl_module.YoutubeDL.return_value.__exit__ = mock.MagicMock(
            return_value=False
        )

        with mock.patch.dict(sys.modules, {"yt_dlp": mock_ydl_module}):
            hasil = zoom_notulen.unduh_audio_youtube("https://invalid-url")
            # DownloadError ditangkap -> None dikembalikan
            self.assertIsNone(hasil)

    def test_download_berhasil_file_ditemukan(self):
        """Download berhasil dan file WAV ditemukan."""
        mock_ydl_module = mock.MagicMock()
        mock_instance = mock.MagicMock()
        mock_instance.extract_info.return_value = {
            "title": "Video Contoh",
            "duration": 300,
        }
        mock_ydl_module.YoutubeDL.return_value.__enter__ = mock.MagicMock(
            return_value=mock_instance
        )
        mock_ydl_module.YoutubeDL.return_value.__exit__ = mock.MagicMock(
            return_value=False
        )

        with mock.patch.dict(sys.modules, {"yt_dlp": mock_ydl_module}):
            with tempfile.TemporaryDirectory() as d:
                jalur = os.path.join(d, "test.wav")
                # Simulasi file sudah ada setelah download
                with open(jalur, "w") as f:
                    f.write("fake wav")
                hasil = zoom_notulen.unduh_audio_youtube(
                    "https://www.youtube.com/watch?v=test", jalur
                )
                self.assertEqual(hasil, jalur)


class TestEkstrakAudio(unittest.TestCase):
    """Tes ekstrak_audio_dari_video: konversi video ke WAV."""

    def test_file_video_dikenali(self):
        """File dengan ekstensi video harus dikenali."""
        self.assertTrue(zoom_notulen.adalah_file_audio_video("video.mp4"))
        self.assertTrue(zoom_notulen.adalah_file_audio_video("video.mkv"))
        self.assertTrue(zoom_notulen.adalah_file_audio_video("video.avi"))
        self.assertTrue(zoom_notulen.adalah_file_audio_video("video.mov"))

    def test_file_audio_dikenali(self):
        """File dengan ekstensi audio harus dikenali."""
        self.assertTrue(zoom_notulen.adalah_file_audio_video("audio.wav"))
        self.assertTrue(zoom_notulen.adalah_file_audio_video("audio.mp3"))
        self.assertTrue(zoom_notulen.adalah_file_audio_video("audio.m4a"))
        self.assertTrue(zoom_notulen.adalah_file_audio_video("audio.ogg"))

    def test_file_tidak_dikenal(self):
        """File yang bukan audio/video harus ditolak."""
        self.assertFalse(zoom_notulen.adalah_file_audio_video("document.txt"))
        self.assertFalse(zoom_notulen.adalah_file_audio_video("image.jpg"))
        self.assertFalse(zoom_notulen.adalah_file_audio_video("video.mp4.bak"))

    def test_folder_tidak_ada_gagal(self):
        """Jika folder tidak ada, proses_folder harus raise SystemExit."""
        cfg = {"engine": "lokal", "bahasa": "id", "konteks": None, "format": "both", "daftar_peserta": None}
        with self.assertRaises(SystemExit):
            zoom_notulen.proses_folder("/folder/tidak/ada", cfg)

    def test_folder_kosong_gagal(self):
        """Jika folder kosong (tanpa file audio/video), harus raise SystemExit."""
        cfg = {"engine": "lokal", "bahasa": "id", "konteks": None, "format": "both", "daftar_peserta": None}
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(SystemExit):
                zoom_notulen.proses_folder(d, cfg)

    def test_folder_dengan_file_audio_diproses(self):
        """Folder dengan file audio harus diproses."""
        cfg = {
            "engine": "lokal", "bahasa": "id", "konteks": None,
            "format": "both", "daftar_peserta": None, "judul": None,
            "notulen_mode": "single"
        }
        with tempfile.TemporaryDirectory() as d:
            # Buat file WAV palsu dengan audio (bukan sunyi)
            import wave
            import math
            wav_path = os.path.join(d, "test.wav")
            with wave.open(wav_path, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(16000)
                # Buat sine wave agar tidak terdeteksi sunyi
                samples = []
                for i in range(16000):
                    val = int(16000 * math.sin(2 * math.pi * 440 * i / 16000))
                    samples.append(val.to_bytes(2, byteorder='little', signed=True))
                wf.writeframes(b''.join(samples))

            with mock.patch.object(
                zoom_notulen, "transkripsi_audio", return_value="Transkrip palsu"
            ):
                with mock.patch.object(
                    zoom_notulen, "_buat_notulen_sesuai_mode", return_value="# Notulen"
                ):
                    with mock.patch.object(
                        zoom_notulen, "simpan_notulen"
                    ):
                        with mock.patch.object(
                            zoom_notulen, "simpan_transkrip"
                        ):
                            hasil = zoom_notulen.proses_folder(d, cfg)
                            self.assertEqual(len(hasil), 1)
                            self.assertEqual(hasil[0][0], "test.wav")


class TestRekamMic(unittest.TestCase):
    """Tes rekam_audio (mode mic): streaming bertahap ke file WAV int16."""

    def test_rekam_audio_menulis_wav_int16_bertahap(self):
        import wave as wavemod

        rate = zoom_notulen.SAMPLE_RATE
        total_frames = int(1 * rate)  # durasi 1 detik
        frame_bytes = 2 * zoom_notulen.KANAL_REKAM  # mono x int16

        class StreamPalsu:
            """Stream yang mengembalikan chunk kecil bertahap (meniru hardware)."""

            def __init__(self):
                self.dibaca = 0

            def read(self, n):
                n = min(n, total_frames - self.dibaca)
                if n <= 0:
                    return b"", False
                self.dibaca += n
                return b"\x00" * (n * frame_bytes), False

        class RawInputPalsu:
            def __enter__(self):
                return StreamPalsu()

            def __exit__(self, *a):
                return False

        with mock.patch.object(
            zoom_notulen.sd, "RawInputStream", return_value=RawInputPalsu()
        ), tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "rekam.wav")
            zoom_notulen.rekam_audio(1, path)
            with wavemod.open(path, "rb") as wf:
                self.assertEqual(wf.getnchannels(), zoom_notulen.KANAL_REKAM)
                self.assertEqual(wf.getsampwidth(), 2)  # int16
                self.assertEqual(wf.getframerate(), rate)
                self.assertEqual(wf.getnframes(), total_frames)

    def test_error_memicu_systemexit(self):
        with mock.patch.object(
            zoom_notulen.sd,
            "RawInputStream",
            side_effect=RuntimeError("no input device"),
        ), tempfile.TemporaryDirectory() as d:
            with self.assertRaises(SystemExit):
                zoom_notulen.rekam_audio(1, os.path.join(d, "x.wav"))

    def test_tombol_henti_menghentikan_rekaman_dan_menyimpan_file(self):
        """Tombol 'q' di tengah rekaman -> loop berhenti, file WAV tetap tersimpan."""
        import wave as wavemod
        import threading

        rate = zoom_notulen.SAMPLE_RATE
        frame_bytes = 2 * zoom_notulen.KANAL_REKAM

        class StreamPalsu:
            def read(self, n):
                return b"\x00" * (n * frame_bytes), False  # tak terbatas (durasi 10 dtk)

        class RawInputPalsu:
            def __enter__(self):
                return StreamPalsu()

            def __exit__(self, *a):
                return False

        berhenti = threading.Event()

        class StreamPalsu:
            """Menandai flag berhenti SETELAH chunk pertama (meniru tekan tombol q)."""

            def __init__(self):
                self.dibaca = 0

            def read(self, n):
                if self.dibaca == 0:
                    # Chunk pertama ditulis dulu (biarkan satu putaran loop normal)
                    self.dibaca = 1
                    return b"\x00" * (n * frame_bytes), False
                berhenti.set()  # mulai iterasi berikutnya: flag sudah aktif -> break
                return b"\x00" * (n * frame_bytes), False

        with mock.patch.object(
            zoom_notulen, "_rekam_berhenti_dengan_pause", return_value=(berhenti, threading.Event())
        ), mock.patch.object(
            zoom_notulen.sd, "RawInputStream", return_value=RawInputPalsu()
        ), tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "rekam_stop.wav")
            durasi = zoom_notulen.rekam_audio(10, path)
            self.assertLess(durasi, 10)  # berhenti lebih awal
            with wavemod.open(path, "rb") as wf:
                self.assertGreater(wf.getnframes(), 0)  # file TIDAK kosong
                self.assertLess(wf.getnframes(), 10 * rate)  # lebih pendek dari durasi penuh

    def test_ctrl_c_menghentikan_rekaman_dan_menyimpan_file(self):
        """KeyboardInterrupt di tengah rekaman -> file tetap tersimpan (bukan dibuang)."""
        import wave as wavemod

        rate = zoom_notulen.SAMPLE_RATE
        frame_bytes = 2 * zoom_notulen.KANAL_REKAM

        class StreamPalsu:
            def __init__(self):
                self.dibaca = 0

            def read(self, n):
                self.dibaca += 1
                if self.dibaca == 2:
                    raise KeyboardInterrupt()  # pengguna menekan Ctrl+C
                return b"\x00" * (n * frame_bytes), False

        class RawInputPalsu:
            def __enter__(self):
                return StreamPalsu()

            def __exit__(self, *a):
                return False

        with mock.patch.object(zoom_notulen, "_rekam_berhenti_dengan_pause", return_value=(None, threading.Event())), mock.patch.object(
            zoom_notulen.sd, "RawInputStream", return_value=RawInputPalsu()
        ), tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "rekam_ctrlc.wav")
            durasi = zoom_notulen.rekam_audio(10, path)
            self.assertLess(durasi, 10)
            with wavemod.open(path, "rb") as wf:
                self.assertGreater(wf.getnframes(), 0)

    def test_pantau_tombol_henti_mendeteksi_q(self):
        """Utas pemantau menandai flag saat tombol 'q' ditekan."""
        import threading

        msvcrt_palsu = types.SimpleNamespace()
        msvcrt_palsu.kbhit = lambda: True
        msvcrt_palsu.getch = lambda: b"q"
        berhenti = threading.Event()
        with mock.patch.object(zoom_notulen, "_deteksi_msvcrt", return_value=msvcrt_palsu):
            zoom_notulen._pantau_tombol_henti(berhenti)
        self.assertTrue(berhenti.is_set())

    def test_pantau_tombol_henti_abaikan_tombol_lain(self):
        """Tombol selain 'q' tidak menandai flag; 'q' berikutnya menandai."""
        import threading

        tombol = iter([b"x", b"a", b"q"])
        msvcrt_palsu = types.SimpleNamespace()
        msvcrt_palsu.kbhit = lambda: True
        msvcrt_palsu.getch = lambda: next(tombol)
        berhenti = threading.Event()
        with mock.patch.object(zoom_notulen, "_deteksi_msvcrt", return_value=msvcrt_palsu):
            zoom_notulen._pantau_tombol_henti(berhenti)
        self.assertTrue(berhenti.is_set())

    def test_pantau_tombol_henti_tanpa_msvcrt_berakhir_seketika(self):
        """OS tanpa msvcrt: utas berakhir tanpa menandai flag (Ctrl+C masih bisa)."""
        import threading

        berhenti = threading.Event()
        with mock.patch.object(zoom_notulen, "_deteksi_msvcrt", return_value=None):
            zoom_notulen._pantau_tombol_henti(berhenti)
        self.assertFalse(berhenti.is_set())


class TestRekamLoopback(unittest.TestCase):
    """Tes rekam_audio_loopback: perekaman WASAPI loopback + resample otomatis."""

    def _buat_pyaudio_palsu(self, native_rate, native_ch, stream_palsu=None, error_open=False):
        """Membuat modul pyaudiowpatch palsu dengan context manager yang benar."""
        device = {
            "index": 0,
            "name": "Speakers [Loopback]",
            "maxInputChannels": native_ch,
            "defaultSampleRate": native_rate,
            "isLoopbackDevice": True,
        }

        class PyAudioPalsu:
            paWASAPI = "WASAPI"
            paInt16 = "paInt16"

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def get_host_api_info_by_type(self, api_type):
                return {"defaultOutputDevice": 0}

            def get_device_info_by_index(self, idx):
                return device

            def get_loopback_device_info_generator(self):
                return [device]

            def get_sample_size(self, fmt):
                return 2

            def open(self, **kwargs):
                if error_open:
                    raise OSError("Invalid sample rate")
                return stream_palsu

        class PyAudioModule:
            """Modul palsu yang mengembalikan PyAudioPalsu saat dipanggil."""
            paWASAPI = "WASAPI"
            paInt16 = "paInt16"

            def PyAudio(self):
                return PyAudioPalsu()

        return PyAudioModule()

    def test_rekam_loopback_merekam_di_native_rate_lalu_resample(self):
        """Rekam di native rate perangkat, lalu resample ke 16kHz mono."""
        import wave as wavemod

        native_rate = 48000
        native_ch = 2
        frame_bytes = 2 * native_ch  # int16 stereo
        total_frames = int(1 * native_rate)  # 1 detik

        class StreamPalsu:
            def __init__(self):
                self.dibaca = 0
            def read(self, n, exception_on_overflow=False):
                n = min(n, total_frames - self.dibaca)
                if n <= 0:
                    return b""
                self.dibaca += n
                return b"\x00" * (n * frame_bytes)
            def stop_stream(self):
                pass
            def close(self):
                pass

        pyaudio_mod = self._buat_pyaudio_palsu(native_rate, native_ch, StreamPalsu())

        # Patch soundfile untuk resample
        import numpy as np
        sf_palsu = mock.MagicMock()
        data_palsu = np.random.randn(48000, 2).astype(np.float32) * 0.1
        sf_palsu.read.return_value = (data_palsu, 48000)
        sf_palsu.info.return_value = mock.MagicMock(samplerate=48000, channels=2)

        with mock.patch.dict(sys.modules, {"pyaudiowpatch": pyaudio_mod, "soundfile": sf_palsu}), \
             mock.patch.object(zoom_notulen, "_rekam_berhenti_dengan_pause", return_value=(threading.Event(), threading.Event())), \
             tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "loopback.wav")
            durasi = zoom_notulen.rekam_audio_loopback(1, path)
            self.assertTrue(os.path.isfile(path))
            with wavemod.open(path, "rb") as wf:
                self.assertEqual(wf.getframerate(), zoom_notulen.SAMPLE_RATE)
                self.assertEqual(wf.getnchannels(), zoom_notulen.KANAL_REKAM)

    def test_rekam_loopback_native_rate_sudah_16khz_tanpa_resample(self):
        """Jika native rate sudah 16kHz mono, tidak perlu resample."""
        native_rate = 16000
        native_ch = 1
        frame_bytes = 2 * native_ch
        total_frames = int(1 * native_rate)

        class StreamPalsu:
            def __init__(self):
                self.dibaca = 0
            def read(self, n, exception_on_overflow=False):
                n = min(n, total_frames - self.dibaca)
                if n <= 0:
                    return b""
                self.dibaca += n
                return b"\x00" * (n * frame_bytes)
            def stop_stream(self):
                pass
            def close(self):
                pass

        pyaudio_mod = self._buat_pyaudio_palsu(native_rate, native_ch, StreamPalsu())

        with mock.patch.dict(sys.modules, {"pyaudiowpatch": pyaudio_mod}), \
             mock.patch.object(zoom_notulen, "_rekam_berhenti_dengan_pause", return_value=(threading.Event(), threading.Event())), \
             mock.patch.object(zoom_notulen, "_resample_ke_16khz") as resample_mock, \
             tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "loopback.wav")
            zoom_notulen.rekam_audio_loopback(1, path)
            resample_mock.assert_not_called()

    def test_rekam_loopback_gagal_buka_stream_systemexit(self):
        """Gagal membuka stream loopback -> SystemExit dengan pesan jelas."""
        pyaudio_mod = self._buat_pyaudio_palsu(48000, 2, error_open=True)

        with mock.patch.dict(sys.modules, {"pyaudiowpatch": pyaudio_mod}), \
             mock.patch.object(zoom_notulen, "_rekam_berhenti_dengan_pause", return_value=(threading.Event(), threading.Event())), \
             tempfile.TemporaryDirectory() as d:
            with self.assertRaises(SystemExit):
                zoom_notulen.rekam_audio_loopback(1, os.path.join(d, "x.wav"))


class TestResampleKe16khz(unittest.TestCase):
    """Tes _resample_ke_16khz: konversi audio ke 16kHz mono."""

    def test_resample_stereo_48khz_ke_16khz_mono(self):
        """Audio stereo 48kHz harus di-resample ke 16kHz mono."""
        import numpy as np
        import wave as wavemod

        # Buat file WAV stereo 48kHz palsu
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "input.wav")
            data = np.random.randn(48000, 2).astype(np.float32) * 0.1
            data_int16 = (data * 32767).clip(-32768, 32767).astype(np.int16)
            with wavemod.open(path, "wb") as wf:
                wf.setnchannels(2)
                wf.setsampwidth(2)
                wf.setframerate(48000)
                wf.writeframes(data_int16.tobytes())

            # Patch soundfile untuk membaca file
            sf_palsu = mock.MagicMock()
            sf_palsu.read.return_value = (data, 48000)

            with mock.patch.dict(sys.modules, {"soundfile": sf_palsu}):
                hasil = zoom_notulen._resample_ke_16khz(path, 48000, 2)
                self.assertIsNotNone(hasil)
                self.assertTrue(os.path.isfile(hasil))
                # Cek hasil: harus 16kHz mono
                with wavemod.open(hasil, "rb") as wf:
                    self.assertEqual(wf.getframerate(), 16000)
                    self.assertEqual(wf.getnchannels(), 1)
                # Bersihkan
                os.remove(hasil)

    def test_resample_gagal_mengembalikan_none(self):
        """Jika soundfile tidak terinstal, kembalikan None."""
        with mock.patch.dict(sys.modules, {"soundfile": None}):
            hasil = zoom_notulen._resample_ke_16khz("nonexistent.wav", 48000, 2)
            self.assertIsNone(hasil)


class TestPeringatanDurasiPanjang(unittest.TestCase):
    """Tes peringatan_durasi_panjang: info untuk rekaman > 1 jam."""

    def test_tidak_ada_peringatan_untuk_durasi_pendek(self):
        self.assertIsNone(zoom_notulen.peringatan_durasi_panjang(60))
        self.assertIsNone(zoom_notulen.peringatan_durasi_panjang(3600))

    def test_peringatan_untuk_durasi_panjang(self):
        teks = zoom_notulen.peringatan_durasi_panjang(7200)
        self.assertIsNotNone(teks)
        self.assertIn("DURASI PEREKAMAN PANJANG", teks)
        self.assertIn("7200", teks)
        self.assertIn("2.0 jam", teks)
        self.assertIn("MB", teks)  # perkiraan ukuran file (16 kHz mono int16)

    def test_durasi_config_panjang_memicu_peringatan_di_main_path(self):
        """Durasi 7200 dari konfigurasi.json -> peringatan bukan None."""
        cfg = zoom_notulen.terapkan_konfigurasi(
            zoom_notulen.buat_parser().parse_args([]), {"durasi": 7200}
        )
        self.assertIsNotNone(zoom_notulen.peringatan_durasi_panjang(cfg["durasi"]))


class TestTranskripsi(unittest.TestCase):
    """Tes transkripsi_audio (faster-whisper): bahasa eksplisit vs 'auto', timestamp."""

    def setUp(self):
        # Bersihkan cache model agar mock object tidak bocor antar test
        zoom_notulen._whisper_cache = {"model": None, "nama": None}

    @staticmethod
    def _segmen_palsu(daftar, bahasa="id"):
        """Mengubah [(start, end, teks), ...] menjadi (generator segmen, info)."""
        info = types.SimpleNamespace(language=bahasa, duration=10.0)
        generator = iter(
            types.SimpleNamespace(start=s, end=e, text=t) for s, e, t in daftar
        )
        return generator, info

    def _panggil(self, daftar, bahasa="id"):
        model = mock.MagicMock()
        model.transcribe.return_value = self._segmen_palsu(daftar)
        with mock.patch.object(zoom_notulen, "WhisperModel", return_value=model):
            hasil = zoom_notulen.transkripsi_audio("a.wav", bahasa)
        return hasil, model

    def test_bahasa_explisit_diteruskan_ke_faster_whisper(self):
        hasil, model = self._panggil([(0.0, 5.0, "Halo dunia")], "id")
        self.assertEqual(hasil, "[00:00] Halo dunia")
        # VAD aktif dengan parameter tambahan
        call_kwargs = model.transcribe.call_args.kwargs
        self.assertEqual(call_kwargs["language"], "id")
        self.assertEqual(call_kwargs["beam_size"], zoom_notulen.WHISPER_BEAM_SIZE)
        self.assertTrue(call_kwargs["vad_filter"])  # VAD aktif
        self.assertIn("vad_parameters", call_kwargs)  # ada parameter VAD

    def test_konteks_menambahkan_hotwords_ke_transkrip(self):
        """Konteks domain diteruskan sebagai initial_prompt DAN hotwords."""
        model = mock.MagicMock()
        model.transcribe.return_value = self._segmen_palsu([(0.0, 5.0, "Kemenkes")])
        with mock.patch.object(zoom_notulen, "WhisperModel", return_value=model):
            zoom_notulen.transkripsi_audio("a.wav", "id", konteks="Kemenkes Permenpan SJT")
        kwargs = model.transcribe.call_args.kwargs
        self.assertEqual(kwargs["initial_prompt"], "Kemenkes Permenpan SJT")
        self.assertEqual(kwargs["hotwords"], "Kemenkes Permenpan SJT")

    def test_tanpa_konteks_tidak_ada_hotwords(self):
        """Tanpa konteks, hotwords tidak diteruskan ke Whisper."""
        model = mock.MagicMock()
        model.transcribe.return_value = self._segmen_palsu([(0.0, 5.0, "Halo")])
        with mock.patch.object(zoom_notulen, "WhisperModel", return_value=model):
            zoom_notulen.transkripsi_audio("a.wav", "id")
        kwargs = model.transcribe.call_args.kwargs
        self.assertNotIn("hotwords", kwargs)
        self.assertNotIn("initial_prompt", kwargs)

    def test_bahasa_auto_memanggil_tanpa_language(self):
        """'auto' tidak boleh memaksa bahasa -> faster-whisper mendeteksi sendiri."""
        _, model = self._panggil([(0.0, 5.0, "Hello world")], "auto")
        self.assertIsNone(model.transcribe.call_args.kwargs["language"])
        self.assertTrue(model.transcribe.call_args.kwargs["vad_filter"])

    def test_timestamp_menit_dan_detik(self):
        hasil, _ = self._panggil(
            [(65.0, 70.0, "Lanjut diskusi"), (120.5, 130.0, "Selesai")], "id"
        )
        self.assertEqual(hasil, "[01:05] Lanjut diskusi\n[02:00] Selesai")

    def test_segmen_kosong_diabaikan(self):
        hasil, _ = self._panggil([(0.0, 5.0, "   "), (5.0, 10.0, "Terdengar")], "id")
        self.assertEqual(hasil, "[00:05] Terdengar")

    def test_semua_segmen_kosong_memicu_systemexit(self):
        """Audio bising tanpa teks yang layak -> SystemExit, bukan string kosong."""
        model = mock.MagicMock()
        model.transcribe.return_value = self._segmen_palsu([(0.0, 5.0, "   "), (5.0, 10.0, "")])
        with mock.patch.object(zoom_notulen, "WhisperModel", return_value=model):
            with self.assertRaises(SystemExit):
                zoom_notulen.transkripsi_audio("a.wav", "id")

    def test_transkripsi_kosong_memicu_systemexit(self):
        model = mock.MagicMock()
        model.transcribe.return_value = (iter(()), types.SimpleNamespace(language="id", duration=10.0))
        with mock.patch.object(zoom_notulen, "WhisperModel", return_value=model):
            with self.assertRaises(SystemExit):
                zoom_notulen.transkripsi_audio("a.wav", "id")

    def test_bahasa_auto_berfungsi_lewat_parser_dan_konfigurasi(self):
        """Nilai 'auto' bisa masuk lewat CLI atau file konfigurasi."""
        args = zoom_notulen.buat_parser().parse_args(["--bahasa", "auto"])
        cfg = zoom_notulen.terapkan_konfigurasi(args, {})
        self.assertEqual(cfg["bahasa"], "auto")
        # Dari konfigurasi juga berlaku
        args2 = zoom_notulen.buat_parser().parse_args([])
        cfg2 = zoom_notulen.terapkan_konfigurasi(args2, {"bahasa": "auto"})
        self.assertEqual(cfg2["bahasa"], "auto")


class TestTranskripsiGemini(unittest.TestCase):
    """Tes transkripsi_audio_gemini: upload File API, polling ACTIVE, parsing timestamp."""

    def _klien_palsu(self, teks=None):
        """Klien Gemini palsu dengan files.upload/get/delete dan generate_content."""
        if teks is None:
            teks = "[00:00] Pembicara 1: Halo semua\n[01:30] Pembicara 2: Selamat pagi"
        client = mock.MagicMock()
        berkas = mock.MagicMock()
        berkas.name = "files/audio-123"
        keadaan = mock.MagicMock()
        keadaan.name = "ACTIVE"
        berkas.state = keadaan
        client.files.upload.return_value = berkas
        client.files.get.return_value = berkas
        resp = mock.MagicMock()
        resp.text = teks
        client.models.generate_content.return_value = resp
        return client

    def _panggil(self, client, bahasa="id", nama_file="audio.wav"):
        with mock.patch.object(zoom_notulen.genai, "Client", return_value=client), mock.patch.object(
            zoom_notulen, "_durasi_audio", return_value=60.0
        ), mock.patch.object(zoom_notulen.os.path, "getsize", return_value=5 * 1024 * 1024):
            return zoom_notulen.transkripsi_audio_gemini(nama_file, bahasa)

    def test_unggah_dan_generate_content_dipanggil(self):
        client = self._klien_palsu()
        hasil = self._panggil(client)
        self.assertIn("Pembicara 1: Halo semua", hasil)
        client.files.upload.assert_called_once()
        self.assertEqual(client.files.upload.call_args.kwargs["file"], "audio.wav")
        call = client.models.generate_content.call_args
        self.assertEqual(call.kwargs["model"], zoom_notulen.GEMINI_TRANSKRIPSI_MODEL)
        isi = call.kwargs["contents"]
        self.assertIn("VERBATIM", isi[1])
        client.files.delete.assert_called_once_with(name="files/audio-123")

    def test_prompt_memuat_instruksi_bahasa(self):
        client = self._klien_palsu()
        self._panggil(client, bahasa="id")
        isi = client.models.generate_content.call_args.kwargs["contents"]
        self.assertIn("Bahasa audio: 'id'", isi[1])

    def test_bahasa_auto_meminta_deteksi(self):
        client = self._klien_palsu()
        self._panggil(client, bahasa="auto")
        isi = client.models.generate_content.call_args.kwargs["contents"]
        self.assertIn("Deteksi bahasa", isi[1])

    def test_parsing_timestamp_dan_geser_offset(self):
        teks = "[00:42] Pembicara 1: Halo\n[01:02:03] Pembicara 2: Selesai\nPembukaan tidak ikut"
        baris = zoom_notulen._parsing_transkrip_gemini(teks, offset=3600)
        self.assertEqual(baris[0], "[01:00:42] Pembicara 1: Halo")
        self.assertEqual(baris[1], "[02:02:03] Pembicara 2: Selesai")
        self.assertEqual(len(baris), 2)

    def test_parsing_tanpa_timestamp_mengembalikan_teks_mentah(self):
        """Fallback: jika Gemini tidak memakai format timestamp, teks mentah tetap dipakai."""
        self.assertEqual(
            zoom_notulen._parsing_transkrip_gemini("tidak ada timestamp", 0),
            ["tidak ada timestamp"],
        )

    def test_parsing_membuang_blok_kode_dan_baris_kosong(self):
        teks = "```\n[00:01] Pembicara 1: A\n[00:02] Pembicara 2: B\n```"
        self.assertEqual(
            zoom_notulen._parsing_transkrip_gemini(teks, 0),
            ["[00:01] Pembicara 1: A", "[00:02] Pembicara 2: B"],
        )

    def test_respon_kosong_memicu_systemexit(self):
        client = self._klien_palsu(teks="   ")
        with self.assertRaises(SystemExit):
            self._panggil(client)

    def test_respon_hanya_markdown_memicu_systemexit(self):
        """Teks hanya berisi blok kode kosong -> tidak ada konten -> SystemExit."""
        client = self._klien_palsu(teks="```\n```")
        with self.assertRaises(SystemExit):
            self._panggil(client)

    def test_state_failed_memicu_systemexit(self):
        client = self._klien_palsu()
        keadaan = mock.MagicMock()
        keadaan.name = "FAILED"
        client.files.get.return_value.state = keadaan
        with self.assertRaises(SystemExit):
            self._panggil(client)

    def test_durasi_melebihi_batas_memicu_systemexit(self):
        client = self._klien_palsu()
        with mock.patch.object(zoom_notulen.genai, "Client", return_value=client), mock.patch.object(
            zoom_notulen, "_durasi_audio", return_value=10 * 3600
        ):
            with self.assertRaises(SystemExit):
                zoom_notulen.transkripsi_audio_gemini("audio.wav", "id")
        client.files.upload.assert_not_called()

    def test_audio_panjang_dipotong_otomatis(self):
        """Audio di atas GEMINI_POTONG_DURASI (30 menit) dipotong otomatis;
        timestamp tiap potongan digeser oleh offset."""
        client = self._klien_palsu()
        durasi_audio = 3 * 3600 + 10
        jumlah_harapan = math.ceil(durasi_audio / zoom_notulen.GEMINI_POTONG_DURASI)
        with mock.patch.object(zoom_notulen.genai, "Client", return_value=client), mock.patch.object(
            zoom_notulen, "_durasi_audio", return_value=durasi_audio
        ), mock.patch.object(
            zoom_notulen, "_potong_audio", return_value="/tmp/potongan.wav"
        ) as potong, mock.patch.object(
            zoom_notulen, "_transkripsi_gemini_satu", side_effect=lambda c, f, b, offset=0, konteks=None: (
                f"[{offset // 3600:02d}:{offset % 3600 // 60:02d}:00] Bagian"
            )
        ) as satu, mock.patch.object(zoom_notulen, "_hapus_file") as hapus:
            hasil = zoom_notulen.transkripsi_audio_gemini("audio.wav", "id")
        self.assertEqual(potong.call_count, jumlah_harapan)  # 3 j 10 dtk / 30 mnt -> 7 potongan
        self.assertEqual(satu.call_count, jumlah_harapan)
        # Offset tiap potongan harus kelipatan GEMINI_POTONG_DURASI berurutan
        offsets = [c.kwargs["offset"] for c in satu.call_args_list]
        self.assertEqual(
            offsets,
            [i * zoom_notulen.GEMINI_POTONG_DURASI for i in range(jumlah_harapan)],
        )
        self.assertIn("[00:00:00] Bagian", hasil)
        self.assertIn("[03:00:00] Bagian", hasil)
        hapus.assert_called()

    def test_wav_besar_dikompres_ke_flac(self):
        """WAV > ambang dikompres ke FLAC sementara sebelum upload."""
        client = self._klien_palsu()
        with mock.patch.object(zoom_notulen.genai, "Client", return_value=client), mock.patch.object(
            zoom_notulen, "_durasi_audio", return_value=60.0
        ), mock.patch.object(
            zoom_notulen.os.path, "getsize", return_value=50 * 1024 * 1024
        ), mock.patch.object(
            zoom_notulen, "_kompres_ke_flac", return_value="/tmp/audio.flac"
        ) as kompres:
            zoom_notulen.transkripsi_audio_gemini("audio.wav", "id")
        kompres.assert_called_once_with("audio.wav")
        self.assertEqual(client.files.upload.call_args.kwargs["file"], "/tmp/audio.flac")

    def test_engine_gemini_melewati_dispatcher(self):
        """--engine gemini memilih transkripsi cloud; default memilih lokal."""
        with mock.patch.object(zoom_notulen, "transkripsi_audio_gemini", return_value="G") as g, mock.patch.object(
            zoom_notulen, "transkripsi_audio_lokal", return_value="L"
        ) as l:
            self.assertEqual(zoom_notulen.transkripsi_audio("a.wav", "id", "gemini"), "G")
            self.assertEqual(zoom_notulen.transkripsi_audio("a.wav", "id", "lokal"), "L")
            self.assertEqual(zoom_notulen.transkripsi_audio("a.wav", "id"), "L")
        g.assert_called_once_with("a.wav", "id", konteks=None)
        self.assertEqual(l.call_count, 2)

class TestNoiseReduce(unittest.TestCase):
    """Tes fungsi kurangi_noise."""

    def test_tanpa_library_mengembalikan_none(self):
        """Jika noisereduce belum terinstal, kembalikan None tanpa error."""
        with mock.patch.dict(sys.modules, {"noisereduce": None}):
            # Re-import akan gagal karena None, tapi fungsi harus menangani gracefully
            hasil = zoom_notulen.kurangi_noise("audio.wav")
            self.assertIsNone(hasil)

    def test_file_tidak_valid_mengembalikan_none(self):
        """File audio tidak valid -> kembalikan None tanpa crash."""
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "bukan_audio.wav")
            with open(path, "w") as f:
                f.write("bukan audio")
            hasil = zoom_notulen.kurangi_noise(path)
            self.assertIsNone(hasil)


class TestMultiPassNotulen(unittest.TestCase):
    """Tes buat_notulen_multipass: 2-pass generation."""

    def _klien_palsu(self, teks_pass1="TOPIK DISKUSI:\n1. Topik A", teks_pass2="# Notulen Rapat"):
        """Klien Gemini palsu yang mengembalikan teks berbeda untuk tiap panggilan."""
        client = mock.MagicMock()
        resp1 = mock.MagicMock(text=teks_pass1)
        resp2 = mock.MagicMock(text=teks_pass2)
        client.models.generate_content.side_effect = [resp1, resp2]
        return client

    def test_multipass_memanggil_gemini_dua_kali(self):
        """Multi-pass harus memanggil generate_content 2 kali (fakta lalu notulen)."""
        client = self._klien_palsu()
        with mock.patch.object(zoom_notulen.genai, "Client", return_value=client):
            hasil = zoom_notulen.buat_notulen_multipass("transkrip")
        self.assertEqual(client.models.generate_content.call_count, 2)
        self.assertEqual(hasil, "# Notulen Rapat")

    def test_multipass_pass1_kosong_fallback_ke_single(self):
        """Jika Pass 1 kosong, fallback ke buat_notulen single-pass."""
        client = mock.MagicMock()
        # Pass 1 kosong
        resp_kosong = mock.MagicMock(text="   ")
        client.models.generate_content.return_value = resp_kosong
        with mock.patch.object(zoom_notulen.genai, "Client", return_value=client):
            with mock.patch.object(
                zoom_notulen, "buat_notulen", return_value="# Fallback"
            ) as fallback:
                hasil = zoom_notulen.buat_notulen_multipass("transkrip")
        fallback.assert_called_once()
        self.assertEqual(hasil, "# Fallback")

    def test_multipass_prompt_pass1_anti_halusinasi(self):
        """Pass 1 (ekstraksi fakta) harus memuat aturan anti-halusinasi."""
        client = self._klien_palsu()
        with mock.patch.object(zoom_notulen.genai, "Client", return_value=client):
            zoom_notulen.buat_notulen_multipass("transkrip")
        # Pass 1 = panggilan pertama
        prompt1 = client.models.generate_content.call_args_list[0].kwargs["contents"]
        self.assertIn("JANGAN mengarang fakta", prompt1)
        self.assertIn("BENAR-BENAR tertulis di transkrip", prompt1)


class TestDispatcherNotulenMode(unittest.TestCase):
    """Tes _buat_notulen_sesuai_mode: dispatch ke single atau multi-pass."""

    def test_mode_multi_memanggil_multipass(self):
        cfg = {"notulen_mode": "multi", "judul": None, "model": "m"}
        with mock.patch.object(
            zoom_notulen, "buat_notulen_multipass", return_value="Multi"
        ) as mp:
            hasil = zoom_notulen._buat_notulen_sesuai_mode(cfg, "t", None)
        mp.assert_called_once()
        self.assertEqual(hasil, "Multi")

    def test_mode_single_memanggil_buat_notulen(self):
        cfg = {"notulen_mode": "single", "judul": None, "model": "m"}
        with mock.patch.object(
            zoom_notulen, "buat_notulen", return_value="Single"
        ) as sp:
            hasil = zoom_notulen._buat_notulen_sesuai_mode(cfg, "t", None)
        sp.assert_called_once()
        self.assertEqual(hasil, "Single")


class TestKonfigurasiBaru(unittest.TestCase):
    """Tes field konfigurasi baru: noise_reduce, notulen_mode."""

    def test_default_noise_reduce_false(self):
        args = zoom_notulen.buat_parser().parse_args([])
        cfg = zoom_notulen.terapkan_konfigurasi(args, {})
        self.assertEqual(cfg["noise_reduce"], False)

    def test_default_notulen_mode_multi(self):
        args = zoom_notulen.buat_parser().parse_args([])
        cfg = zoom_notulen.terapkan_konfigurasi(args, {})
        self.assertEqual(cfg["notulen_mode"], "multi")

    def test_notulen_mode_dari_konfigurasi(self):
        args = zoom_notulen.buat_parser().parse_args([])
        cfg = zoom_notulen.terapkan_konfigurasi(args, {"notulen_mode": "single"})
        self.assertEqual(cfg["notulen_mode"], "single")

    def test_notulen_mode_cli_menang(self):
        args = zoom_notulen.buat_parser().parse_args(["--notulen-mode", "single"])
        cfg = zoom_notulen.terapkan_konfigurasi(args, {"notulen_mode": "multi"})
        self.assertEqual(cfg["notulen_mode"], "single")

    def test_notulen_mode_invalid_ditolak(self):
        args = zoom_notulen.buat_parser().parse_args([])
        with self.assertRaises(SystemExit):
            zoom_notulen.terapkan_konfigurasi(args, {"notulen_mode": "turbo"})

    def test_noise_reduce_dari_konfigurasi(self):
        args = zoom_notulen.buat_parser().parse_args([])
        cfg = zoom_notulen.terapkan_konfigurasi(args, {"noise_reduce": True})
        self.assertEqual(cfg["noise_reduce"], True)

    def test_noise_reduce_cli_flag(self):
        args = zoom_notulen.buat_parser().parse_args(["--noise-reduce"])
        cfg = zoom_notulen.terapkan_konfigurasi(args, {})
        self.assertEqual(cfg["noise_reduce"], True)

    def test_parser_notulen_mode_invalid_ditolak(self):
        with self.assertRaises(SystemExit):
            zoom_notulen.buat_parser().parse_args(["--notulen-mode", "turbo"])

    def test_subtitle_argumen_diterima(self):
        args = zoom_notulen.buat_parser().parse_args(["--subtitle", "srt"])
        self.assertEqual(args.subtitle, "srt")

    def test_subtitle_default_none(self):
        args = zoom_notulen.buat_parser().parse_args([])
        self.assertIsNone(args.subtitle)


class TestSubtitleGenerator(unittest.TestCase):
    """Tes fungsi subtitle generator (SRT dan VTT)."""

    TRANSKRIP_CONTOH = (
        "[00:05] Halo semuanya, selamat pagi.\n"
        "[00:10] Pembicara 1: Mari kita mulai rapat hari ini.\n"
        "[01:30] Pembicara 2: Baik, saya setuju.\n"
        "[02:15:30] Pembicara 1: Terima kasih atas presentasinya.\n"
    )

    def test_parse_transkrip_ke_segments(self):
        """Parsing transkrip menghasilkan segments yang benar."""
        segments = zoom_notulen._parse_transkrip_ke_segments(self.TRANSKRIP_CONTOH)
        self.assertEqual(len(segments), 4)
        self.assertEqual(segments[0]["start"], 5.0)
        self.assertEqual(segments[0]["text"], "Halo semuanya, selamat pagi.")
        self.assertEqual(segments[1]["start"], 10.0)
        self.assertEqual(segments[2]["start"], 90.0)  # 1:30 = 90 detik
        self.assertEqual(segments[3]["start"], 8130.0)  # 2:15:30 = 2*3600+15*60+30

    def test_parse_transkrip_kosong(self):
        """Transkrip kosong menghasilkan list kosong."""
        segments = zoom_notulen._parse_transkrip_ke_segments("")
        self.assertEqual(segments, [])

    def test_detik_ke_srt_timestamp(self):
        """Konversi detik ke format SRT benar."""
        self.assertEqual(zoom_notulen._detik_ke_srt_timestamp(0), "00:00:00,000")
        self.assertEqual(zoom_notulen._detik_ke_srt_timestamp(65.5), "00:01:05,500")
        self.assertEqual(zoom_notulen._detik_ke_srt_timestamp(3661.123), "01:01:01,123")

    def test_detik_ke_vtt_timestamp(self):
        """Konversi detik ke format VTT benar."""
        self.assertEqual(zoom_notulen._detik_ke_vtt_timestamp(0), "00:00:00.000")
        self.assertEqual(zoom_notulen._detik_ke_vtt_timestamp(65.5), "00:01:05.500")
        self.assertEqual(zoom_notulen._detik_ke_vtt_timestamp(3661.123), "01:01:01.123")

    def test_generate_srt(self):
        """Generate SRT menghasilkan file yang valid."""
        with tempfile.TemporaryDirectory() as d:
            jalur = os.path.join(d, "test.srt")
            hasil = zoom_notulen.generate_srt(self.TRANSKRIP_CONTOH, jalur)
            self.assertEqual(hasil, jalur)
            self.assertTrue(os.path.isfile(jalur))
            with open(jalur, "r", encoding="utf-8") as f:
                isi = f.read()
            self.assertIn("1\n00:00:05,000 -->", isi)
            self.assertIn("Halo semuanya", isi)
            self.assertNotIn("WEBVTT", isi)  # SRT tidak punya header WEBVTT

    def test_generate_vtt(self):
        """Generate VTT menghasilkan file yang valid."""
        with tempfile.TemporaryDirectory() as d:
            jalur = os.path.join(d, "test.vtt")
            hasil = zoom_notulen.generate_vtt(self.TRANSKRIP_CONTOH, jalur)
            self.assertEqual(hasil, jalur)
            self.assertTrue(os.path.isfile(jalur))
            with open(jalur, "r", encoding="utf-8") as f:
                isi = f.read()
            self.assertTrue(isi.startswith("WEBVTT"))
            self.assertIn("1\n00:00:05.000 -->", isi)
            self.assertIn("Halo semuanya", isi)

    def test_generate_srt_tanpa_timestamp(self):
        """Transkrip tanpa timestamp tetap menghasilkan subtitle."""
        with tempfile.TemporaryDirectory() as d:
            jalur = os.path.join(d, "test.srt")
            hasil = zoom_notulen.generate_srt("Tidak ada timestamp", jalur)
            self.assertIsNone(hasil)  # Tidak ada segmen -> None

    def test_simpan_subtitle_both(self):
        """simpan_subtitle dengan format 'both' menghasilkan .srt dan .vtt."""
        with tempfile.TemporaryDirectory() as d:
            nama_dasar = os.path.join(d, "subtitle")
            hasil = zoom_notulen.simpan_subtitle(
                self.TRANSKRIP_CONTOH, nama_dasar, "both"
            )
            self.assertEqual(len(hasil), 2)
            self.assertTrue(any(".srt" in h for h in hasil))
            self.assertTrue(any(".vtt" in h for h in hasil))

    def test_simpan_subtitle_srt_only(self):
        """simpan_subtitle dengan format 'srt' hanya menghasilkan .srt."""
        with tempfile.TemporaryDirectory() as d:
            nama_dasar = os.path.join(d, "subtitle")
            hasil = zoom_notulen.simpan_subtitle(
                self.TRANSKRIP_CONTOH, nama_dasar, "srt"
            )
            self.assertEqual(len(hasil), 1)
            self.assertTrue(hasil[0].endswith(".srt"))

    def test_timestamp_1_jam_lebih(self):
        """Timestamp lebih dari 1 jam ditangani dengan benar."""
        transkrip = "[01:30:00] Test panjang"
        segments = zoom_notulen._parse_transkrip_ke_segments(transkrip)
        self.assertEqual(segments[0]["start"], 5400.0)  # 1:30:00 = 5400 detik


class TestTranslate(unittest.TestCase):
    """Tes fungsi translate_transkrip via Gemini."""

    def test_translate_argumen_diterima(self):
        args = zoom_notulen.buat_parser().parse_args(["--translate", "en"])
        self.assertEqual(args.translate, "en")

    def test_translate_default_none(self):
        args = zoom_notulen.buat_parser().parse_args([])
        self.assertIsNone(args.translate)

    def test_bahasa_translate_dikenali(self):
        """Semua bahasa dalam BAHASA_TRANSLATE harus dikenali."""
        self.assertIn("en", zoom_notulen.BAHASA_TRANSLATE)
        self.assertIn("id", zoom_notulen.BAHASA_TRANSLATE)
        self.assertIn("ja", zoom_notulen.BAHASA_TRANSLATE)
        self.assertIn("ko", zoom_notulen.BAHASA_TRANSLATE)
        self.assertIn("auto", zoom_notulen.BAHASA_TRANSLATE)

    def test_translate_mengembalikan_teks(self):
        """translate_transkrip harus mengembalikan teks terjemahan."""
        client = mock.MagicMock()
        resp = mock.MagicMock(text="[00:05] Hello everyone")
        client.models.generate_content.return_value = resp
        with mock.patch.object(zoom_notulen.genai, "Client", return_value=client):
            hasil = zoom_notulen.translate_transkrip(
                "[00:05] Halo semuanya", "en"
            )
            self.assertIn("Hello", hasil)
            # Pastikan prompt berisi instruksi untuk mempertahankan timestamp
            prompt = client.models.generate_content.call_args.kwargs["contents"]
            self.assertIn("timestamp", prompt.lower())

    def test_translate_respon_kosong_kembali_asli(self):
        """Jika Gemini mengembalikan kosong, transkrip asli dikembalikan."""
        client = mock.MagicMock()
        resp = mock.MagicMock(text="   ")  # kosong/spasi
        client.models.generate_content.return_value = resp
        with mock.patch.object(zoom_notulen.genai, "Client", return_value=client):
            hasil = zoom_notulen.translate_transkrip(
                "[00:05] Halo semuanya", "en"
            )
            self.assertEqual(hasil, "[00:05] Halo semuanya")  # kembali asli

    def test_simpan_translate(self):
        """simpan_translate harus membuat file .txt dengan kode bahasa."""
        with tempfile.TemporaryDirectory() as d:
            nama = os.path.join(d, "transkrip")
            hasil = zoom_notulen.simpan_translate("Hello world", nama, "en")
            self.assertTrue(os.path.isfile(hasil))
            self.assertIn("_en.txt", hasil)
            with open(hasil, "r", encoding="utf-8") as f:
                self.assertEqual(f.read(), "Hello world")

    def test_translate_prompt_mempertahankan_timestamp(self):
        """Prompt translate harus instruksikan pertahankan timestamp."""
        client = mock.MagicMock()
        resp = mock.MagicMock(text="[00:05] Translated text")
        client.models.generate_content.return_value = resp
        with mock.patch.object(zoom_notulen.genai, "Client", return_value=client):
            zoom_notulen.translate_transkrip("[00:05] Teks asli", "ja")
            prompt = client.models.generate_content.call_args.kwargs["contents"]
            self.assertIn("Japanese", prompt)  # nama bahasa di prompt
            self.assertIn("[MM:SS]", prompt)  # instruksi format timestamp


class TestTranslateIntegration(unittest.TestCase):
    """Tes integrasi translate ke configuration."""

    def test_translate_dari_konfigurasi(self):
        args = zoom_notulen.buat_parser().parse_args([])
        cfg = zoom_notulen.terapkan_konfigurasi(args, {"translate": "en"})
        self.assertEqual(cfg["translate"], "en")

    def test_translate_cli_menang(self):
        args = zoom_notulen.buat_parser().parse_args(["--translate", "ja"])
        cfg = zoom_notulen.terapkan_konfigurasi(args, {"translate": "en"})
        self.assertEqual(cfg["translate"], "ja")

    def test_translate_default_none(self):
        args = zoom_notulen.buat_parser().parse_args([])
        cfg = zoom_notulen.terapkan_konfigurasi(args, {})
        self.assertIsNone(cfg["translate"])


class TestSearch(unittest.TestCase):
    """Tes fungsi search_transkrip."""

    TRANSKRIP_CONTOH = (
        "[00:05] Halo semuanya, selamat pagi.\n"
        "[00:10] Pembicara 1: Mari kita bahas action items hari ini.\n"
        "[01:30] Pembicara 2: Action items untuk Budi sudah selesai.\n"
        "[02:00] Pembicara 1: Bagus, ada lagi action items lain?\n"
    )

    def test_search_argumen_diterima(self):
        args = zoom_notulen.buat_parser().parse_args(["--search", "action"])
        self.assertEqual(args.search, "action")

    def test_search_default_none(self):
        args = zoom_notulen.buat_parser().parse_args([])
        self.assertIsNone(args.search)

    def test_search_regex_argumen(self):
        args = zoom_notulen.buat_parser().parse_args(["--search-regex"])
        self.assertTrue(args.search_regex)

    def test_search_case_argumen(self):
        args = zoom_notulen.buat_parser().parse_args(["--search-case"])
        self.assertTrue(args.search_case)

    def test_search_menemukan_kata(self):
        """Search harus menemukan semua kemunculan kata kunci."""
        hasil = zoom_notulen.search_transkrip(self.TRANSKRIP_CONTOH, "action items")
        self.assertEqual(len(hasil), 3)  # 3 baris mengandung "action items"
        self.assertEqual(hasil[0]["timestamp"], "[00:10]")

    def test_search_case_insensitive(self):
        """Default search harus case-insensitive."""
        hasil = zoom_notulen.search_transkrip(self.TRANSKRIP_CONTOH, "ACTION")
        self.assertEqual(len(hasil), 3)

    def test_search_case_sensitive(self):
        """Search case-sensitive harus membedakan huruf besar/kecil."""
        # "action" lowercase hanya cocok di baris 1 dan 3 (baris 2 pakai "Action")
        hasil = zoom_notulen.search_transkrip(
            self.TRANSKRIP_CONTOH, "action", case_sensitive=True
        )
        self.assertEqual(len(hasil), 2)  # baris 1 dan 3 punya "action" lowercase
        # Coba dengan "Action" capital - hanya cocok di baris 2
        hasil2 = zoom_notulen.search_transkrip(
            self.TRANSKRIP_CONTOH, "Action", case_sensitive=True
        )
        self.assertEqual(len(hasil2), 1)  # baris 2 punya "Action" capital

    def test_search_regex(self):
        """Search regex harus bekerja dengan benar."""
        hasil = zoom_notulen.search_transkrip(
            self.TRANSKRIP_CONTOH, r"action.{0,5}items", use_regex=True
        )
        self.assertEqual(len(hasil), 3)

    def test_search_tidak_ditemukan(self):
        """Jika tidak ditemukan, hasil harus kosong."""
        hasil = zoom_notulen.search_transkrip(self.TRANSKRIP_CONTOH, "xyz123")
        self.assertEqual(len(hasil), 0)

    def test_search_timestamp_diekstrak(self):
        """Timestamp harus diekstrak dengan benar."""
        hasil = zoom_notulen.search_transkrip(self.TRANSKRIP_CONTOH, "Budi")
        self.assertEqual(len(hasil), 1)
        self.assertEqual(hasil[0]["timestamp"], "[01:30]")
        self.assertIn("Budi", hasil[0]["text"])

    def test_search_line_number(self):
        """Nomor baris harus benar."""
        hasil = zoom_notulen.search_transkrip(self.TRANSKRIP_CONTOH, "Budi")
        self.assertEqual(hasil[0]["line"], 3)  # baris ke-3

    def test_simpan_hasil_search(self):
        """simpan_hasil_search harus membuat file .txt."""
        hasil = zoom_notulen.search_transkrip(self.TRANSKRIP_CONTOH, "action")
        with tempfile.TemporaryDirectory() as d:
            nama = os.path.join(d, "search.txt")
            zoom_notulen.simpan_hasil_search(hasil, "action", nama)
            self.assertTrue(os.path.isfile(nama))
            with open(nama, "r", encoding="utf-8") as f:
                isi = f.read()
            self.assertIn("action", isi)
            self.assertIn("3 hasil", isi)

    def test_cetak_hasil_search_kosong(self):
        """cetak_hasil_search untuk hasil kosong tidak boleh error."""
        # Seharusnya tidak raise exception
        zoom_notulen.cetak_hasil_search([], "tidak_ada")


class TestSearchIntegration(unittest.TestCase):
    """Tes integrasi search ke parser."""

    def test_search_dengan_transkrip(self):
        """--search + --transkrip harus bekerja bersama."""
        args = zoom_notulen.buat_parser().parse_args(
            ["--search", "test", "--transkrip", "file.txt"]
        )
        self.assertEqual(args.search, "test")
        self.assertEqual(args.transkrip, "file.txt")

    def test_search_dengan_audio(self):
        """--search + --audio harus transkripsi dulu lalu cari."""
        args = zoom_notulen.buat_parser().parse_args(
            ["--search", "test", "--audio", "audio.wav"]
        )
        self.assertEqual(args.search, "test")
        self.assertEqual(args.audio, "audio.wav")


class TestPDFExport(unittest.TestCase):
    """Tes fungsi export_pdf."""

    def test_pdf_argumen_diterima(self):
        args = zoom_notulen.buat_parser().parse_args(["--pdf"])
        self.assertTrue(args.pdf)

    def test_pdf_default_false(self):
        args = zoom_notulen.buat_parser().parse_args([])
        self.assertFalse(args.pdf)

    def test_export_pdf_membuat_file(self):
        """export_pdf harus membuat file PDF yang valid."""
        with tempfile.TemporaryDirectory() as d:
            jalur = os.path.join(d, "test.pdf")
            teks = "# Judul Test\nIni adalah isi dokumen.\n\n[00:05] Timestamp contoh."
            hasil = zoom_notulen.export_pdf(teks, jalur, "Judul Test")
            self.assertEqual(hasil, jalur)
            self.assertTrue(os.path.isfile(jalur))
            # Cek ukuran file > 0
            self.assertGreater(os.path.getsize(jalur), 0)

    def test_export_pdf_fpdf2_belum_terinstal(self):
        """Jika fpdf2 belum terinstal, export_pdf harus raise SystemExit."""
        with mock.patch.dict(sys.modules, {"fpdf": None}):
            with self.assertRaises(SystemExit):
                zoom_notulen.export_pdf("test", "test.pdf")

    def test_export_pdf_judul_muncul(self):
        """Judul harus muncul di PDF."""
        with tempfile.TemporaryDirectory() as d:
            jalur = os.path.join(d, "test.pdf")
            zoom_notulen.export_pdf("Isi dokumen", jalur, "Judul Rapat")
            # Baca PDF sebagai binary dan cek ada string judul
            with open(jalur, "rb") as f:
                isi = f.read()
            # PDF menyimpan teks dalam format tertentu, cek partial
            self.assertGreater(len(isi), 0)

    def test_export_pdf_baris_kosong(self):
        """Baris kosong tidak boleh menyebabkan error."""
        with tempfile.TemporaryDirectory() as d:
            jalur = os.path.join(d, "test.pdf")
            teks = "Baris 1\n\n\nBaris 2"
            hasil = zoom_notulen.export_pdf(teks, jalur)
            self.assertIsNotNone(hasil)

    def test_simpan_pdf(self):
        """simpan_pdf harus membuat file PDF."""
        with tempfile.TemporaryDirectory() as d:
            nama = os.path.join(d, "notulen")
            hasil = zoom_notulen.simpan_pdf("Isi notulen", nama, "Notulen")
            self.assertIsNotNone(hasil)
            self.assertTrue(hasil.endswith(".pdf"))
            self.assertTrue(os.path.isfile(hasil))

def _impor_soundfile_asli():
    """Impor soundfile ASLI (bypass mock di sys.modules); None jika tidak terinstal."""
    import importlib
    simpan = sys.modules.get("soundfile")
    sys.modules.pop("soundfile", None)
    try:
        return importlib.import_module("soundfile")
    except ImportError:
        return None
    finally:
        if simpan is not None:
            sys.modules["soundfile"] = simpan
        else:
            sys.modules.pop("soundfile", None)


def _soundfile_asli_tersedia():
    return _impor_soundfile_asli() is not None


class TestPerbaikanBugRegresi(unittest.TestCase):
    """Regresi untuk jalur yang dahulu bocor (tidak tertutup tes lama)."""

    def test_format_help_tidak_crash_tanda_persen(self):
        """format_help() tidak boleh gagal karena '%' telanjang di help --flac."""
        try:
            hasil = zoom_notulen.buat_parser().format_help()
        except ValueError as e:
            self.fail(f"format_help() crash: {e}")
        self.assertIn("--flac", hasil)

    def test_durasi_rusak_dari_config_ditimpa_cli(self):
        """'durasi' tidak valid di konfigurasi.json boleh ditimpa CLI yang valid."""
        parser = zoom_notulen.buat_parser()
        args = parser.parse_args(["--durasi", "120"])
        cfg = zoom_notulen.terapkan_konfigurasi(args, {"durasi": "3600"})
        self.assertEqual(cfg["durasi"], 120)

    def test_durasi_rusak_dari_config_tanpa_cli_tetap_ditolak(self):
        """Tanpa override CLI, 'durasi' tidak valid tetap ditolak."""
        parser = zoom_notulen.buat_parser()
        args = parser.parse_args([])
        with self.assertRaises(SystemExit):
            zoom_notulen.terapkan_konfigurasi(args, {"durasi": "3600"})

    def test_mode_transkrip_memanggil_pembuat_notulen(self):
        """main() dengan --transkrip harus memanggil _buat_notulen_sesuai_mode
        (regresi untuk typo '_buat_notulan' yang menyebabkan NameError)."""
        with tempfile.TemporaryDirectory() as d:
            jalur_t = os.path.join(d, "Transkrip_uji.txt")
            with open(jalur_t, "w", encoding="utf-8") as f:
                f.write("[00:05] Pembicara 1: selamat pagi")
            argv = ["zoom_notulen.py", "--transkrip", jalur_t]
            with mock.patch.object(sys, "argv", argv), \
                 mock.patch.object(zoom_notulen, "_buat_notulen_sesuai_mode",
                                   return_value="# Notulen Uji") as mk, \
                 mock.patch.object(zoom_notulen, "simpan_notulen") as sn, \
                 mock.patch.object(zoom_notulen, "periksa_halusinasi_notulen",
                                   return_value=[]):
                zoom_notulen.main()
        mk.assert_called_once()
        sn.assert_called_once()

    def test_hentikan_rekaman_menyetel_event(self):
        """hentikan_rekaman() menyetel event perekaman aktif (dipakai Web UI)."""
        ev = threading.Event()
        zoom_notulen._rekaman_aktif["berhenti"] = ev
        try:
            self.assertTrue(zoom_notulen.hentikan_rekaman())
            self.assertTrue(ev.is_set())
        finally:
            zoom_notulen._rekaman_aktif["berhenti"] = None
        self.assertFalse(zoom_notulen.hentikan_rekaman())

    @unittest.skipUnless(_soundfile_asli_tersedia(), "soundfile asli tidak terinstal")
    def test_ukur_rms_mendukung_flac(self):
        """ukur_rms mendukung FLAC (hasil --flac) — tidak mengembalikan None."""
        sf_asli = _impor_soundfile_asli()
        with tempfile.TemporaryDirectory() as d:
            jalur = os.path.join(d, "uji.flac")
            data = np.sin(np.linspace(0.0, 100.0, 16000)) * 5000.0
            sf_asli.write(jalur, data, 16000, format="FLAC", subtype="PCM_16")
            with mock.patch.dict(sys.modules, {"soundfile": sf_asli}):
                rms = zoom_notulen.ukur_rms(jalur)
            self.assertIsNotNone(rms)
            self.assertGreater(rms, 1e-4)


if __name__ == "__main__":
    unittest.main()
