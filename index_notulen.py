"""
index_notulen.py - Membuat daftar isi (index) semua notulen rapat yang pernah dibuat.

Cara pakai:
  python index_notulen.py                # index semua notulen (terbaru dulu), simpan Index_Notulen.md
  python index_notulen.py --terbalik     # urut dari yang terlama
  python index_notulen.py --output daftar.md   # nama file output kustom
"""

import argparse
import glob
import os
import re
from datetime import datetime

FOLDER = os.path.dirname(os.path.abspath(__file__))
POLA_NAMA = re.compile(r"^Notulen_Rapat_(\d{8})_(\d{6})\.(md|txt)$")


def baca_judul(nama_file):
    """Mengambil judul dari baris pertama yang diawali '# ' di file Markdown."""
    try:
        with open(nama_file, "r", encoding="utf-8") as f:
            for baris in f:
                baris = baris.strip()
                if baris.startswith("# "):
                    return baris[2:].strip()
    except OSError:
        pass
    return "(tanpa judul)"


def kumpulkan_notulen(folder=FOLDER):
    """Memindai folder untuk file Notulen_Rapat_*.md (fallback .txt) dan
    mengembalikan daftar entri {waktu, judul, nama} terurut terbaru dulu."""
    entri = []
    for path in sorted(glob.glob(os.path.join(folder, "Hasil_Notulen", "Notulen_Rapat_*.*"))):
        nama = os.path.basename(path)
        cocok = POLA_NAMA.match(nama)
        if not cocok:
            continue
        try:
            waktu = datetime.strptime(f"{cocok.group(1)}_{cocok.group(2)}", "%Y%m%d_%H%M%S")
        except ValueError:
            continue
        # Prioritas file .md; jika keduanya ada, jangan duplikasi
        if nama.endswith(".txt") and os.path.exists(os.path.join(folder, "Hasil_Notulen", nama[:-4] + ".md")):
            continue
        entri.append({
            "waktu": waktu,
            "judul": baca_judul(path),
            "nama": nama,
        })
    entri.sort(key=lambda e: e["waktu"], reverse=True)
    return entri


def main():
    parser = argparse.ArgumentParser(
        description="Buat daftar isi semua notulen rapat yang pernah dibuat."
    )
    parser.add_argument(
        "--terbalik", action="store_true",
        help="Urutkan dari notulen terlama (default: terbaru dulu)."
    )
    parser.add_argument(
        "--output", default="Index_Notulen.md",
        help="Nama file output (default: Index_Notulen.md)."
    )
    args = parser.parse_args()

    entri = kumpulkan_notulen()
    if not entri:
        print("Tidak ada file notulen (Notulen_Rapat_*.md/*.txt) yang ditemukan di folder ini.")
        return

    if args.terbalik:
        entri.reverse()

    baris = [
        "# Daftar Isi Notulen Rapat",
        "",
        f"Total: {len(entri)} notulen",
        "",
        "| No | Tanggal & Waktu | Judul | File |",
        "|----|----------------|-------|------|",
    ]
    for i, e in enumerate(entri, 1):
        waktu = e["waktu"].strftime("%d %b %Y, %H:%M")
        judul_aman = e["judul"].replace("|", "\\|")
        baris.append(f"| {i} | {waktu} | {judul_aman} | `{e['nama']}` |")

    teks = "\n".join(baris) + "\n"
    print(teks)

    with open(args.output, "w", encoding="utf-8") as f:
        f.write(teks)
    print(f"Index disimpan ke '{args.output}'")


if __name__ == "__main__":
    main()
