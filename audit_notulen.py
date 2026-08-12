# -*- coding: utf-8 -*-
"""Audit anti-halusinasi untuk semua notulen.

Memeriksa setiap `Notulen_Rapat_<stempel>.md`: nama PIC/penanggung jawab pada
bagian Action Items harus BENAR-BENAR muncul di transkrip yang bersangkutan.
Jika ada nama yang tidak ditemukan di transkrip (kemungkinan Gemini mengarang
nama dari Daftar Peserta), notulen tersebut dilaporkan sebagai BERHALUSINASI.

Cara pakai:
    python audit_notulen.py                 # audit semua notulen, cetak laporan
    python audit_notulen.py --output laporan.md   # simpan laporan ke file Markdown
    python audit_notulen.py --berhenti-gagal      # exit code 1 jika ada halusinasi

Kode keluar:
    0 = semua notulen bersih (atau tidak ada yang berhalusinasi)
    1 = ditemukan notulen berhalusinasi (hanya jika --berhenti-gagal)
    2 = tidak ada notulen / transkrip yang cocok ditemukan

Catatan: skrip ini hanya membaca file - tidak memanggil Gemini (validasi murni
lokal, tanpa kuota API). Memerlukan GEMINI_API_KEY hanya agar modul zoom_notulen
dapat di-import; nilainya tidak dipakai untuk validasi.
"""

import argparse
import glob
import os
import re
import sys

# API key dummy hanya untuk memenuhi pemeriksaan saat import zoom_notulen
# (validasi di bawah tidak memanggil API sama sekali).
os.environ.setdefault("GEMINI_API_KEY", "kunci-import-tidak-dipakai")

import zoom_notulen  # noqa: E402

# Peta manual untuk notulen yang dibuat ulang (regenerasi): stempel notulen baru
# memakai transkrip dari stempel lama. Tambahkan di sini bila membuat notulen
# ulang dari transkrip yang sudah ada dengan timestamp berbeda.
PEMETAAN_TRANSKRIP = {
    "20260811_174600": "20260810_121046",
    "20260811_174602": "20260811_162516",
    "20260811_174604": "20260811_163328",
}

POLA_STEMPEL = re.compile(r"(\d{8}_\d{6})")


def transkrip_untuk(stempel_notulen):
    """Menentukan nama file transkrip untuk stempel notulen tertentu.

    Prioritas: peta manual (regenerasi) > transkrip dengan stempel yang sama.
    Mengembalikan nama file, atau None jika tidak ditemukan.
    """
    stempel = PEMETAAN_TRANSKRIP.get(stempel_notulen, stempel_notulen)
    nama = os.path.join("Hasil_Transkripsi", f"Transkrip_{stempel}.txt")
    return nama if os.path.isfile(nama) else None


def audit_semua():
    """Memeriksa semua notulen. Mengembalikan daftar dict hasil per file."""
    hasil = []
    for md in sorted(glob.glob(os.path.join("Hasil_Notulen", "Notulen_Rapat_*.md"))):
        cocok = POLA_STEMPEL.search(md)
        if not cocok:
            continue
        stempel = cocok.group(1)
        nama_transkrip = transkrip_untuk(stempel)
        if not nama_transkrip:
            hasil.append(
                {"file": md, "status": "TANPA-TRANSKRIP", "nama": [], "transkrip": None}
            )
            continue
        with open(md, encoding="utf-8") as f:
            notulen = f.read()
        with open(nama_transkrip, encoding="utf-8") as f:
            transkrip = f.read()
        mencurigakan = zoom_notulen.periksa_halusinasi_notulen(notulen, transkrip)
        status = "HALUSINASI" if mencurigakan else "BERSIH"
        nama_file_laporan = os.path.basename(md)
        hasil.append(
            {"file": nama_file_laporan, "status": status, "nama": mencurigakan, "transkrip": os.path.basename(nama_transkrip)}
        )
    return hasil


def cetak_laporan(hasil, output=None):
    """Menampilkan laporan audit (ke stdout dan/atau file Markdown)."""
    baris = ["# Audit Halusinasi Notulen", ""]
    baris.append(f"Total: {len(hasil)} notulen diperiksa")
    halusinasi = [h for h in hasil if h["status"] == "HALUSINASI"]
    bersih = [h for h in hasil if h["status"] == "BERSIH"]
    tanpa = [h for h in hasil if h["status"] == "TANPA-TRANSKRIP"]
    baris.append("")
    baris.append(f"- ✅ BERSIH: {len(bersih)}")
    baris.append(f"- 🔴 HALUSINASI: {len(halusinasi)}")
    baris.append(f"- ⚠️ Tanpa transkrip (tidak diperiksa): {len(tanpa)}")
    baris.append("")
    baris.append("## Detail")
    baris.append("")
    for h in hasil:
        if h["status"] == "BERSIH":
            baris.append(f"- ✅ **{h['file']}** — bersih")
        elif h["status"] == "HALUSINASI":
            nama = ", ".join(f"`{n}`" for n in h["nama"])
            baris.append(f"- 🔴 **{h['file']}** — nama tidak ada di transkrip: {nama}")
        else:
            baris.append(f"- ⚠️ **{h['file']}** — transkrip `{h['transkrip']}` tidak ditemukan")
    teks = "\n".join(baris)

    print(teks)
    if output:
        with open(output, "w", encoding="utf-8") as f:
            f.write(teks + "\n")
        print(f"\nLaporan disimpan ke '{output}'")


def main():
    zoom_notulen.aktifkan_utf8_stdout()  # agar emoji/Unicode aman di konsol Windows
    parser = argparse.ArgumentParser(
        description="Audit halusinasi: nama PIC Action Item harus ada di transkrip."
    )
    parser.add_argument(
        "--output", default=None,
        help="Simpan laporan audit ke file Markdown (mis. laporan_audit.md).",
    )
    parser.add_argument(
        "--berhenti-gagal", action="store_true",
        help="Kembalikan kode keluar 1 jika ada notulen berhalusinasi (untuk otomatisasi/CI).",
    )
    args = parser.parse_args()

    hasil = audit_semua()
    if not hasil:
        print("Tidak ada notulen ditemukan.")
        sys.exit(2)
    cetak_laporan(hasil, args.output)

    ada_halusinasi = any(h["status"] == "HALUSINASI" for h in hasil)
    if ada_halusinasi and args.berhenti_gagal:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
