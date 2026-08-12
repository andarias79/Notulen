"""Tes unit untuk index_notulen.py.

Menjalankan:
    python -m unittest test_index_notulen -v
"""

import os
import tempfile
import unittest

import index_notulen


class TestBacaJudul(unittest.TestCase):
    def test_judul_dari_heading_pertama(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "n.md")
            with open(p, "w", encoding="utf-8") as f:
                f.write("# Notulen Rapat - Rapat Mingguan\n\n## Ringkasan\nisi\n")
            self.assertEqual(index_notulen.baca_judul(p), "Notulen Rapat - Rapat Mingguan")

    def test_tanpa_heading_mengembalikan_fallback(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "n.md")
            with open(p, "w", encoding="utf-8") as f:
                f.write("## Ringkasan\nisi tanpa judul\n")
            self.assertEqual(index_notulen.baca_judul(p), "(tanpa judul)")

    def test_file_tidak_ada_mengembalikan_fallback(self):
        self.assertEqual(index_notulen.baca_judul("tidak_ada.md"), "(tanpa judul)")


class TestKumpulkanNotulen(unittest.TestCase):
    def _tulis(self, folder, nama, judul):
        os.makedirs(os.path.join(folder, "Hasil_Notulen"), exist_ok=True)
        p = os.path.join(folder, "Hasil_Notulen", nama)
        with open(p, "w", encoding="utf-8") as f:
            f.write(f"# {judul}\n\nisi\n")
        return p

    def test_mengumpulkan_dan_mengurut_terbaru_dulu(self):
        with tempfile.TemporaryDirectory() as d:
            self._tulis(d, "Notulen_Rapat_20260809_100000.md", "Rapat A")
            self._tulis(d, "Notulen_Rapat_20260809_090000.md", "Rapat B")
            self._tulis(d, "Notulen_Rapat_20260808_080000.md", "Rapat C")
            entri = index_notulen.kumpulkan_notulen(d)
            self.assertEqual([e["judul"] for e in entri], ["Rapat A", "Rapat B", "Rapat C"])
            self.assertEqual(entri[0]["nama"], "Notulen_Rapat_20260809_100000.md")

    def test_file_non_notulen_diabaikan(self):
        with tempfile.TemporaryDirectory() as d:
            self._tulis(d, "Notulen_Rapat_20260809_100000.md", "Rapat A")
            os.makedirs(os.path.join(d, "Hasil_Notulen"), exist_ok=True)
            with open(os.path.join(d, "Hasil_Notulen", "catatan_biasa.md"), "w", encoding="utf-8") as f:
                f.write("# Bukan notulen\n")
            entri = index_notulen.kumpulkan_notulen(d)
            self.assertEqual(len(entri), 1)

    def test_md_dan_txt_tidak_duplikat(self):
        with tempfile.TemporaryDirectory() as d:
            self._tulis(d, "Notulen_Rapat_20260809_100000.md", "Rapat A")
            self._tulis(d, "Notulen_Rapat_20260809_100000.txt", "Rapat A")
            entri = index_notulen.kumpulkan_notulen(d)
            self.assertEqual(len(entri), 1)
            self.assertTrue(entri[0]["nama"].endswith(".md"))

    def test_txt_saja_tetap_dihitung(self):
        with tempfile.TemporaryDirectory() as d:
            self._tulis(d, "Notulen_Rapat_20260809_100000.txt", "Rapat A")
            entri = index_notulen.kumpulkan_notulen(d)
            self.assertEqual(len(entri), 1)
            self.assertTrue(entri[0]["nama"].endswith(".txt"))

    def test_nama_tidak_valid_diabaikan(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "Hasil_Notulen"), exist_ok=True)
            with open(os.path.join(d, "Hasil_Notulen", "Notulen_Rapat_salah.md"), "w", encoding="utf-8") as f:
                f.write("# X\n")
            self.assertEqual(index_notulen.kumpulkan_notulen(d), [])


if __name__ == "__main__":
    unittest.main()
