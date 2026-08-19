# 📝 Notulen Rapat Zoom

Buat notulen rapat otomatis dari rapat Zoom: **rekam audio → transkripsi (lokal faster-whisper atau cloud Gemini) → notulen rapi (Gemini AI)** — semuanya dalam satu perintah.

```text
Rekam audio  →  Transkripsi (faster-whisper lokal / Gemini cloud)  →  Notulen (Gemini AI)
   .wav              .txt raw (ber-timestamp)                        .md + .txt
```

> ⚡ **Dua mesin transkripsi, pilih sesuai kebutuhan** — `--engine lokal` (default) atau `--engine gemini`:
>
> **Lokal (faster-whisper)** — gratis & offline. Engine CTranslate2 + kuantisasi INT8 + VAD silero,
> akurasi setara OpenAI Whisper tanpa GPU. **Benchmark terukur di CPU (tanpa GPU), model `small`, klip 60 detik:**
> openai-whisper **0,5× realtime** → faster-whisper **17,6× realtime** (±35× lebih cepat).
> Untuk rekaman rapat 103 menit: dari **±3,4 jam** menjadi **±6–25 menit**.
>
> **Cloud (Gemini)** — ⚡ **super cepat**: audio diunggah ke server Google dan ditranskripsikan
> verbatim oleh model `gemini-3.6-flash`, lengkap dengan **timestamp per segmen + speaker diarization**
> (Pembicara 1, Pembicara 2, …). Tidak ada beban CPU lokal sama sekali — rapat 2 jam selesai dalam
> hitungan menit. Butuh internet & kuota Gemini (tier gratis tersedia).

---

## ✨ Fitur

- 🎥 **YouTube langsung**: download audio dari URL YouTube dengan `--youtube <url>` — otomatis diunduh, dikonversi, ditranskripsi, dan dibuat notulen.
- 🎙️ **Dua sumber perekaman**: mikrofon (`mic`) atau **audio sistem langsung dari Windows** via WASAPI loopback (`loopback`) — menangkap suara *semua* peserta rapat Zoom tanpa fitur rekam bawaan.
- ⏹️ **Hentikan rekaman kapan saja**: tekan **`q`** (atau **Ctrl+C**) saat merekam — rekaman berhenti, audio yang sudah terekam **tetap disimpan** lalu langsung diproses ke transkripsi & notulen. Jika dihentikan sangat cepat (< 2 detik), muncul peringatan "rekaman sangat pendek".
- 📄 **Transkripsi bahasa Indonesia** (atau bahasa lain) dengan dua mesin: **faster-whisper lokal** (CTranslate2 + INT8 + VAD, **±4× lebih cepat** dari OpenAI Whisper di CPU) atau **transkripsi cloud Gemini** (`--engine gemini` — super cepat, verbatim, ber-timestamp **+ speaker diarization**).
- 📂 **Transkrip mentah otomatis disimpan** ke `Transkrip_<timestamp>.txt` untuk dokumentasi & audit.
- 🔊 **Deteksi rekaman sunyi otomatis** — RMS audio diukur sebelum transkripsi; jika hampir diam, peringatan jelas (dengan angka & penyebab) ditampilkan lebih dulu.
- 🤖 **Notulen terstruktur otomatis** oleh Gemini AI: Ringkasan Eksekutif, Poin Diskusi, Keputusan, dan Action Items — dengan **aturan anti-halusinasi**: Action Items hanya boleh ditugaskan ke nama yang benar-benar muncul di transkrip (jika transkrip hanya memakai label `Pembicara N`, PIC ditulis sebagai `Pembicara N`, bukan nama tebakan), dan angka kunci (target vs realisasi, persentase) dipertahankan di Poin-Poin Utama Diskusi.
- 🕵️ **Validasi anti-halusinasi otomatis**: setelah notulen dibuat, skrip mencocokkan nama PIC di Action Items dengan isi transkrip — jika ada nama yang tidak muncul di transkrip (mis. Gemini mengarang nama dari Daftar Peserta), peringatan jelas ditampilkan sebelum notulen disebar. Validator cerdas: mengenali berbagai format tabel (`PIC`, `PIC / Siapa`, `Nama`, `Penanggung Jawab`), mengabaikan label `Pembicara N`, nama tim (`Tim Keuangan`), dan frasa tugas — jadi hanya nama orang yang benar-benar halusinasi yang dilaporkan.
- 📝 **Output Markdown & teks polos** (`.md` + `.txt`) dengan heading rapi, siap dibuka di VS Code/Obsidian/GitHub.
- 🗂️ **Nama file ber-timestamp** — tidak menimpa hasil sebelumnya.
- 🛡️ **API key aman** — disimpan di `.env`, bukan di source code.
- 📧 **Kirim notulen via email** ke peserta (opsional, via Resend API — gratis 100 email/hari).
- 🔁 **Proses file audio yang sudah ada** (mis. hasil rekaman Zoom yang diekstrak) tanpa perlu merekam ulang.
- 🎬 **Dukungan video langsung**: proses file video (MP4, MKV, AVI, MOV, dll.) — audio diekstrak otomatis lalu ditranskripsi.
- 📁 **Batch proses folder**: `--folder <path>` memproses semua file audio/video di folder secara otomatis.
- 📄 **Subtitle/Caption generator**: buat file subtitle `.srt` (SubRip) atau `.vtt` (WebVTT) dari transkrip untuk video player.
- 🌐 **Translate transkrip**: terjemahkan transkrip ke bahasa lain via Gemini (`--translate en`, `--translate ja`, dll.). Mempertahankan format timestamp dan label pembicara.
- 🔍 **Search di transkrip**: cari kata/frasa di transkrip dengan `--search "kata kunci"`. Mendukung regex dan case-sensitive.

---

## 📋 Persyaratan

| Kebutuhan | Detail |
|---|---|
| OS | **Windows 10/11** (untuk WASAPI loopback). Bagian transkripsi & notulen jalan di OS lain, tapi perekaman loopback khusus Windows. |
| Python | **3.9 – 3.13** (faster-whisper/ctranslate2 mendukung hingga Python 3.13). |
| ffmpeg | **Tidak wajib lagi** — faster-whisper memakai PyAV yang membundel library FFmpeg sendiri. Hanya perlu jika ingin **konversi format audio manual** (lihat [instalasi ffmpeg](#-instalasi-ffmpeg)). |
| API Key | Gemini AI (gratis di [Google AI Studio](https://aistudio.google.com/apikey)). |

---

## 🚀 Instalasi

### 1. Siapkan file proyek

```bash
# (Jika pakai git)
git clone <url-repo-anda>
cd Notulen
```

File yang dibutuhkan:
```
zoom_notulen.py      # skrip utama
requirements.txt     # daftar dependensi Python
.env.example         # template API key
.gitignore           # melindungi .env & file hasil
```

### 2. Instal ffmpeg (opsional)

> **Tidak wajib lagi untuk transkripsi** — faster-whisper memakai PyAV yang membundel
> library FFmpeg sendiri. ffmpeg hanya diperlukan jika ingin **konversi format audio**
> secara manual (mis. `.m4a` → `.wav`). Di Windows:

```bash
# Opsi A — via winget (direkomendasikan)
winget install Gyan.FFmpeg

# Opsi B — via Chocolatey
choco install ffmpeg
```

Verifikasi instalasi:

```bash
ffmpeg -version
```

> Jika tidak bisa memakai winget/choco: unduh dari [ffmpeg.org](https://ffmpeg.org/download.html), ekstrak, lalu tambahkan folder `bin` ke **Environment Variables → Path**.

### 3. Install dependensi Python

```bash
pip install -r requirements.txt
```

> `pyaudiowpatch` (untuk loopback) otomatis hanya terinstal di Windows (ditandai `sys_platform == "win32"` di `requirements.txt`).
> 
> **Untuk fitur YouTube** (`--youtube`): `yt-dlp` sudah termasuk di `requirements.txt`. Jika ingin update manual:
> ```bash
> pip install -U yt-dlp
> ```

### 4. Konfigurasi API key

```bash
# 1. Salin template
copy .env.example .env        # Windows
# atau: cp .env.example .env  # bash

# 2. Buka .env dan isi API key Anda
GEMINI_API_KEY=API_KEY_ANDA_DI_SINI
```

⚠️ **Jangan pernah meng-commit file `.env`** — sudah dilindungi oleh `.gitignore`. Jika sebelumnya ada API key yang pernah terekspos di source code, **revoke** di Google AI Studio dan buat yang baru.

---

## 🎯 Penggunaan

### ⚙️ Pengaturan default rapat (konfigurasi.json)

Simpan pengaturan rapat agar tidak perlu mengetik ulang setiap kali:

```bash
copy konfigurasi.example.json konfigurasi.json   # Windows
# atau: cp konfigurasi.example.json konfigurasi.json
```

Lalu edit isinya (contoh):

```json
{
  "judul": "Rapat Mingguan Tim",
  "daftar_peserta": "Budi, Siti, Andi",
  "tujuan_email": "budi@mail.com, siti@mail.com",
  "durasi": 3600,
  "sumber": "loopback",
  "format": "both",
  "bahasa": "id",
  "engine": "lokal"
}
```

Setelah itu cukup jalankan `python zoom_notulen.py` — semua default terpakai otomatis.
**Prioritas nilai: argumen CLI > konfigurasi.json > default bawaan.**

> `durasi` di config ini juga dipakai `run.bat`: menu **1** memakainya langsung, menu **2**/`run.bat loopback` menjadikannya **default** saat menekan Enter (tetap bisa diketik nilai lain, tanpa batas maksimum). Nilai `engine` di config juga menjadi **default** pada prompt pilih engine (menu 1, 2, 5) — cukup tekan Enter; jika key `engine` tidak ada, fallback ke `lokal`.

> `konfigurasi.json` berisi data pribadi (email peserta) sehingga otomatis diabaikan git;
> `konfigurasi.example.json` tetap dilacak sebagai template.

### 📇 Daftar isi semua notulen

Setelah beberapa rapat, buat index semua notulen (tanggal, waktu, judul, file):

```bash
python index_notulen.py              # terbaru dulu → Index_Notulen.md
python index_notulen.py --terbalik   # urut dari terlama
python index_notulen.py --output daftar.md
```

### 🖱️ Cara cepat (double-click) — Windows

Double-click `run.bat` untuk menjalankan skrip dengan konfigurasi default:

| Perintah | Hasil |
|---|---|
| `run.bat` (double-click) | Tampilkan **menu interaktif**: 1=Rekam mikrofon, 2=Loopback, 3=Index notulen, 4=Tes unit, 5=Proses file audio yang sudah ada, 6=Audit anti-halusinasi notulen |
| `run.bat loopback` | Rekam audio sistem WASAPI (semua peserta) — **bertanya durasi** (Enter = durasi dari `konfigurasi.json`, fallback 1 jam; tanpa batas maksimum) **lalu pilih engine** |
| `run.bat --durasi 300 --judul "Rapat X"` | Teruskan argumen apa pun ke skrip (tambahkan `--engine gemini` bila perlu — tanpa prompt) |

`run.bat` otomatis: pindah ke folder skrip, mengaktifkan virtual environment (`.venv`/`venv`) jika ada, memeriksa Python, dan menampilkan pesan solusi jika gagal (tanpa menutup jendela).

#### 🎚️ Pilih engine transkripsi (lokal / gemini)

Menu **1** (Rekam mikrofon), **2** (Loopback), dan **5** (Proses file audio) kini
**menanyakan engine transkripsi** sebelum menjalankan skrip. Menu **6** (Audit
alusinasi) langsung menjalankan `audit_notulen.py` (murni lokal, tanpa API):

```text
   Engine transkripsi:
     1. Lokal (faster-whisper) - gratis, offline
     2. Gemini (cloud) - super cepat, butuh internet
Pilih engine (Enter = lokal): 
```

| Input | Hasil |
|---|---|
| `1` | Engine **lokal** (faster-whisper CTranslate2 INT8 + VAD — gratis & offline) |
| `2` | Engine **gemini** (transkripsi cloud `gemini-3.6-flash` — super cepat, verbatim, + speaker diarization; butuh internet & `GEMINI_API_KEY`) |
| Enter (kosong) | Pakai **default dari `konfigurasi.json`** (key `engine`); jika tidak ada, fallback `lokal` |

Contoh alur menu 5 (proses file): pilih `5` → ketik/drag path audio → pilih engine
(`2` untuk cloud Gemini, Enter untuk default) → skrip berjalan dengan `--engine` terpilih.
Jika pilihan engine salah 6× berturut-turut, `run.bat` otomatis memakai default agar tidak
terjebak di loop.

### Contoh cepat

```bash
# Rekam 1 jam dari mikrofon, hasilkan .md + .txt
python zoom_notulen.py --durasi 3600

# Rekam 1 jam audio sistem (semua peserta Zoom) — Windows
python zoom_notulen.py --durasi 3600 --sumber loopback

# Cek daftar perangkat loopback yang tersedia
python zoom_notulen.py --list-devices

# Proses file audio yang sudah ada (mis. hasil rekaman Zoom)
python zoom_notulen.py --audio rekaman_zoom.wav

# Download & proses audio dari YouTube langsung
python zoom_notulen.py --youtube "https://www.youtube.com/watch?v=VIDEO_ID"

# YouTube + transkripsi cloud Gemini (super cepat)
python zoom_notulen.py --youtube "https://www.youtube.com/watch?v=VIDEO_ID" --engine gemini

# Proses file video langsung (audio diekstrak otomatis)
python zoom_notulen.py --audio rapat_zoom.mp4

# Batch proses semua file audio/video di folder
python zoom_notulen.py --folder ./rekaman_rapat/

# Batch proses dengan transkripsi cloud Gemini
python zoom_notulen.py --folder ./rekaman_rapat/ --engine gemini

# Buat subtitle SRT dari transkrip
python zoom_notulen.py --audio rekaman_zoom.wav --subtitle srt

# Buat subtitle SRT dan VTT sekaligus
python zoom_notulen.py --audio rekaman_zoom.wav --subtitle both

# YouTube + subtitle untuk video
python zoom_notulen.py --youtube "https://youtu.be/VIDEO_ID" --subtitle srt

# Translate transkrip ke bahasa Inggris
python zoom_notulen.py --audio rapat.wav --translate en

# Translate transkrip ke bahasa Jepang
python zoom_notulen.py --audio rapat.wav --translate ja

# Translate + subtitle sekaligus
python zoom_notulen.py --audio rapat.wav --translate en --subtitle srt

# Cari "action items" di transkrip
python zoom_notulen.py --search "action items"

# Cari dengan regex
python zoom_notulen.py --search "rapat.{0,10}senin" --search-regex

# Cari case-sensitive (huruf besar/kecil berbeda)
python zoom_notulen.py --search "Budi" --search-case

# Cari di file transkrip tertentu
python zoom_notulen.py --search "Budi" --transkrip Hasil_Transkripsi/Transkrip_xxx.txt

# SUPER CEPAT: transkripsi cloud via Gemini (verbatim + timestamp + pembicara)
python zoom_notulen.py --audio rekaman_zoom.wav --engine gemini

# Rekam 5 menit, transkripsi bahasa Inggris, simpan hanya Markdown
python zoom_notulen.py --durasi 300 --bahasa en --format md

# Transkripsi dengan deteksi bahasa otomatis (rapat campuran Indonesia-Inggris)
python zoom_notulen.py --durasi 300 --bahasa auto

# Rekam dengan judul & daftar peserta notulen yang ditentukan sendiri
python zoom_notulen.py --durasi 3600 --judul "Rapat Mingguan Tim Produk" \
    --daftar-peserta "Budi, Siti, Andi"

# Kirim notulen via email ke peserta (butuh setup Resend, lihat di bawah)
python zoom_notulen.py --durasi 3600 --tujuan-email "budi@mail.com, siti@mail.com"
```

### Daftar opsi (CLI)

| Opsi | Default | Fungsi |
|---|---|---|
| `--durasi <detik>` | `60` | Durasi perekaman dalam detik — **tanpa batas maksimum** (mis. `7200` = 2 jam, `10800` = 3 jam). Jika > 1 jam, skrip menampilkan info otomatis (ukuran file & RAM transkripsi) sebelum merekam. **Selama merekam, tekan `q` (atau Ctrl+C) untuk berhenti lebih awal** — audio tersimpan & tetap diproses |
| `--audio <path>` | — | Transkripsi file audio yang sudah ada (lewati perekaman) |
| `--youtube <url>` | — | URL YouTube untuk diunduh dan diproses langsung (butuh `yt-dlp`, lihat instalasi) |
| `--folder <path>` | — | Folder berisi file audio/video untuk diproses secara batch (semua file didukung) |
| `--sumber {mic, loopback}` | `mic` | Sumber audio: mikrofon atau audio sistem WASAPI |
| `--list-devices` | off | Tampilkan perangkat WASAPI loopback lalu keluar |
| `--perangkat <index>` | otomatis | Paksa pakai perangkat loopback tertentu |
| `--engine {lokal, gemini}` | `lokal` | Mesin transkripsi: `lokal` = faster-whisper di komputer Anda (gratis, offline); `gemini` = transkripsi cloud via Gemini AI (super cepat, speaker diarization, butuh internet & kuota Gemini) |
| `--bahasa <kode>` | `id` | Bahasa transkripsi. Kode bahasa: `en`, `id`, `ms`, dst. **`auto`** = deteksi bahasa otomatis (cocok untuk rapat campuran Indonesia–Inggris) |
| `--format {md, txt, both}` | `both` | Format output notulen |
| `--subtitle {srt, vtt, both, none}` | `none` | Buat file subtitle: `srt` = SubRip (.srt), `vtt` = WebVTT (.vtt), `both` = keduanya |
| `--translate <bahasa>` | — | Terjemahkan transkrip via Gemini: `en` (Inggris), `ja` (Jepang), `ko` (Korea), `zh` (China), `ms` (Melayu), dll. |
| `--search <kata>` | — | Cari kata/frasa di transkrip. Mendukung regex dengan `--search-regex` |
| `--search-regex` | off | Gunakan mode regex untuk `--search` |
| `--search-case` | off | Huruf besar/kecil berbeda saat search (default: case-insensitive) |
| `--judul <teks>` | otomatis | Judul notulen (dipakai Gemini di heading `# Notulen Rapat - ...`). Jika kosong, Gemini menentukan topik dari transkrip |
| `--daftar-peserta <teks>` | — | Daftar peserta rapat dipisah koma (mis. `"Budi, Siti, Andi"`). Disertakan sebagai bagian `## Daftar Peserta` di notulen |
| `--tujuan-email <teks>` | — | Email penerima notulen dipisah koma (mis. `"budi@mail.com, siti@mail.com"`). Kirim notulen via Resend API setelah dibuat |
| `--model <model>` | `gemini-flash-lite-latest` | Model Gemini untuk membuat notulen. Default adalah model **Flash-Lite tier gratis**; ganti ke `gemini-flash-latest` untuk kualitas lebih tinggi |
| `--abaikan-senyap` | off | Lewati peringatan rekaman sunyi dan tetap proses transkripsi (untuk audio sangat pelan tapi masih ada isinya) |

### Output

Folder Anda akan secara otomatis terstruktur seperti ini:
```text
Hasil_Notulen/ Notulen_Rapat_2026...md    ← notulen Markdown (heading & tabel rapi)
Hasil_Transkripsi/ Transkrip_2026...txt   ← transkrip mentah (untuk dokumentasi)
Hasil_Subtitle/ subtitle_2026...srt       ← subtitle SRT/VTT (jika --subtitle diaktifkan)
Hasil_Translate/ Transkrip_2026_en.txt   ← terjemahan (jika --translate diaktifkan)
Hasil_Rekaman/ rekaman_zoom_2026...wav    ← audio (jika merekam via aplikasi ini)
```

> Model transkripsi cloud default `gemini-3.6-flash`; jika kuota/lonjakan (429/503) tidak hilang setelah retry otomatis, skrip **beralih ke model cadangan** (`gemini-3.5-flash`, `gemini-flash-latest`) karena kuota tier gratis dihitung per model. Ubah `WHISPER_MODEL` di bagian atas `zoom_notulen.py` untuk menyesuaikan model transkripsi lokal (`base` → `small` / `medium` / `large-v3` untuk akurasi lebih tinggi; `base`/`tiny` untuk kecepatan maksimal). Engine faster-whisper memakai kuantisasi INT8 + VAD silero sehingga ±4× lebih cepat dari OpenAI Whisper di CPU.

---

## 📧 Kirim Notulen via Email (Resend)

Skrip bisa mengirim notulen langsung ke peserta rapat menggunakan **Resend API**
(gratis 100 email/hari, 3000/bulan):

```bash
pip install resend
```

Lalu isi `.env`:

```env
RESEND_API_KEY=re_xxxxxxxxxxxx        # buat di https://resend.com/api-keys
RESEND_PENGIRIM=notulen@domain-anda.com
```

Pakai:

```bash
python zoom_notulen.py --durasi 3600 --tujuan-email "budi@mail.com, siti@mail.com"
```

### Catatan penting

- ⚠️ **Sandbox**: tanpa verifikasi domain, pengirim default `onboarding@resend.dev` **hanya bisa mengirim ke email akun Anda sendiri**. Untuk mengirim ke siapa pun, verifikasi domain di dashboard Resend lalu atur `RESEND_PENGIRIM`.
- Isi email = notulen (Markdown) sebagai teks polos; subjek otomatis memakai `--judul` jika ada.
- Jika `RESEND_API_KEY` kosong atau `resend` belum terinstal, skrip berhenti dengan pesan jelas tanpa mengirim.

---

## 🔊 WASAPI Loopback (Rekam Audio Sistem)

### Apa itu?

WASAPI loopback adalah fitur Windows yang "menyadap" semua audio yang **keluar dari speaker**. Dengan mode ini, skrip merekam suara seluruh peserta Zoom (yang masuk lewat speaker) — bukan hanya suara mikrofon Anda.

### Kenapa dipakai?

- Tanpa perlu menyalakan fitur **Record** bawaan Zoom.
- Menangkap suara semua peserta, bukan hanya Anda.
- Hasilnya file `.wav` biasa yang bisa langsung ditranskripsi.

### Cara pakai

```bash
# 1. (Opsional) Cek perangkat loopback yang tersedia
python zoom_notulen.py --list-devices

# 2. Rekam audio sistem
python zoom_notulen.py --durasi 3600 --sumber loopback
```

Jika ada beberapa perangkat output dan pilihan otomatis tidak sesuai:

```bash
python zoom_notulen.py --durasi 3600 --sumber loopback --perangkat 8
```

### Tips penting

- ✅ Pastikan **volume audio Zoom tidak di-mute** dan volume output (speaker) **tidak nol** — loopback menangkap persis apa yang keluar dari speaker.
- ✅ Tutup aplikasi yang memakai output audio secara eksklusif jika muncul error "device busy".
- ⚠️ **Hanya Windows.** Di Linux/macOS, gunakan mode `mic` atau file audio (`--audio`).
- 🕵️ **Etika & privasi**: merekam rapat tanpa sepengetahuan peserta bisa melanggar kebijakan privasi/etik dan hukum di beberapa yurisdiksi. **Pastikan semua peserta tahu dan menyetujui perekaman.**

---

## ⚙️ Alur Kerja Skrip

```
[1/3] Perekaman      →  16 kHz mono (mic) / WASAPI (loopback) → .wav
[2/3] Transkripsi    →  --engine lokal: faster-whisper CTranslate2 INT8 + VAD (model "small")
                     →  --engine gemini: upload ke server Google → Gemini 3.6 Flash verbatim + timestamp + pembicara
[3/3] Notulen        →  Gemini AI → Markdown ber-heading → simpan .md / .txt
```

---

## ❓ FAQ

**1. Muncul `ERROR: GEMINI_API_KEY tidak ditemukan.`**
File `.env` belum dibuat atau belum terisi. Salin `.env.example` menjadi `.env` dan isi `GEMINI_API_KEY` (buat di [aistudio.google.com/apikey](https://aistudio.google.com/apikey)).

**2. `pyaudiowpatch belum terinstal.`**
Jalankan `pip install pyaudiowpatch` (khusus Windows). Pastikan Python 3.9–3.11 — di versi lebih baru paket ini bisa gagal ter-install.

**3. Hasil transkripsi kosong / `Transkripsi kosong`.**
Skrip kini **mendeteksi rekaman sunyi otomatis** (ukur RMS setelah merekam) dan menampilkan peringatan jelas *sebelum* transkripsi, lengkap dengan kemungkinan penyebab:
- Mode `mic`: mikrofon tidak menangkap suara — cek perangkat input default Windows.
- Mode `loopback`: audio rapat sedang mute atau volume output nol.
- File `--audio` rusak/format tidak didukung — konversi dulu ke `.wav` (mis. dengan ffmpeg: `ffmpeg -i input.m4a -ar 16000 output.wav`).

**4. `ffmpeg tidak ditemukan` saat konversi audio manual.**
Sejak memakai faster-whisper (PyAV membundel library FFmpeg), ffmpeg **tidak wajib** untuk transkripsi. Jika Anda memakainya untuk **konversi format manual** (mis. `.m4a` → `.wav`) dan muncul error ini: ffmpeg belum terinstal atau tidak masuk PATH. Lihat [instalasi ffmpeg](#-instalasi-ffmpeg), lalu **restart terminal** setelah menambah PATH.

**5. Transkripsinya lama / tidak akurat.**
- **Solusi tercepat: `--engine gemini`** — transkripsi cloud via Gemini (server Google), tanpa beban CPU lokal, plus speaker diarization otomatis. Cukup butuh internet & kuota Gemini.
- Mode lokal memakai **faster-whisper** (CTranslate2 + INT8 + VAD) yang **±4× lebih cepat** dari OpenAI Whisper di CPU dengan akurasi setara, dan merekam langsung di **16 kHz mono** (file 4× lebih kecil → lebih cepat diproses).
- Model CTranslate2 diunduh sekali saat pertama dipakai (beberapa ratus MB).
- Masih kurang cepat? Ganti `WHISPER_MODEL` ke `base`/`tiny` di bagian atas `zoom_notulen.py`, atau gunakan `--engine gemini` (cloud, super cepat).
- Untuk akurasi lebih tinggi pada istilah rapat: `WHISPER_MODEL = "medium"` atau `"large-v3"`.
- File audio panjang (berjam-jam) tetap butuh waktu di CPU — untuk kasus ekstrem, potong audio per segmen lalu gabungkan transkripnya.

**6. Rapat saya campur bahasa (Indonesia–Inggris).**
Gunakan `--bahasa auto` (atau `"bahasa": "auto"` di `konfigurasi.json`) agar Whisper mendeteksi bahasa audio sendiri — pilihan terbaik untuk rapat campuran. Alternatif: `--bahasa id` jika dominan bahasa Indonesia.

**7. Rapat lebih dari beberapa jam — apakah aman?**
Tidak ada batas durasi — `--durasi` menerima angka berapa pun (mis. `10800` = 3 jam), dan `run.bat` kini bertanya durasi. Mode `mic` maupun `loopback` **menulis audio ke disk bertahap** (streaming per chunk), jadi aman untuk rapat panjang tanpa boros RAM. Jika durasi > 1 jam, skrip otomatis menampilkan **info durasi panjang** (perkiraan ukuran file WAV, RAM transkripsi, saran cek disk) sebelum merekam. Satu-satunya batasan: faster-whisper memuat audio ke memori saat transkripsi — untuk kasus ekstrem, potong audio per segmen lalu gabungkan transkripnya.

**7b. Bagaimana cara menghentikan perekaman lebih awal?**
Saat perekaman berjalan, cukup **tekan `q`** pada jendela cmd (atau **Ctrl+C**).
Rekaman berhenti seketika — audio yang sudah terekam **tetap disimpan** dan langsung
melanjutkan ke tahap transkripsi + notulen (tidak perlu memulai dari awal). Jika
rekaman dihentikan terlalu cepat (< 2 detik), muncul peringatan "rekaman sangat
pendek" — transkripsi tetap dicoba, tapi hasilnya kemungkinan kosong. Catatan:
tombol `q` aktif di Windows (konsol); Ctrl+C selalu berfungsi di semua platform.

**8. Berapa biaya memakai Gemini API?**
Model notulen default `gemini-flash-lite-latest` termasuk **tier gratis** Google dengan kuota harian terbesar (cek harga & kuota terkini di [AI Studio pricing](https://ai.google.dev/pricing)). Transkripsi **lokal** (`--engine lokal`) berjalan offline (gratis). Mode **cloud** (`--engine gemini`) memakai model `gemini-3.6-flash` — ada tier gratis harian, dan file audio **diunggah + dihapus otomatis dari server setelah 48 jam** (Files API gratis).

**12. Apakah `--engine gemini` butuh internet? Bagaimana cara kerjanya?**
Ya — audio diunggah ke server Google lalu ditranskripsikan verbatim oleh `gemini-3.6-flash` dengan timestamp + speaker diarization. Alurnya: WAV besar dikompres ke FLAC sementara (upload lebih cepat) → unggah ke Files API → tunggu file siap (`ACTIVE`) → Gemini menulis transkrip → file dihapus dari server. Audio lebih dari 2 jam **dipotong otomatis** per bagian agar hasil tidak terpotong; batas aman ±8 jam per request.
Jika model transkripsi kena **429 (kuota harian tier gratis habis)** atau **503 (lonjakan permintaan)** yang tidak hilang setelah percobaan ulang otomatis, skrip **otomatis beralih ke model cadangan** (`gemini-3.5-flash`, `gemini-flash-latest`) karena kuota tier gratis dihitung **per model** — jadi transkripsi tetap jalan meski kuota satu model habis. Daftar cadangan bisa disesuaikan di konstanta `GEMINI_TRANSKRIPSI_CADANGAN` di `zoom_notulen.py`.

**13. Transkripsi cloud vs lokal — mana yang dipakai untuk hasil terbaik?**
- **Cloud (`--engine gemini`)**: tercepat & paling akurat untuk istilah teknis, plus label pembicara. Pilih ini jika internet stabil & kuota tersedia.
- **Lokal (`--engine lokal`)**: gratis, offline, privasi penuh (audio tidak pernah keluar dari komputer). Pilih ini untuk rapat sensitif.
- Hasil keduanya ber-timestamp dan diteruskan ke tahap notulen yang sama.

**9. Muncul error `503 UNAVAILABLE` / model sedang sibuk?**
Itu **bukan** berarti berbayar — ini error sementara karena lonjakan permintaan. Skrip kini **otomatis mencoba ulang** (hingga 4× dengan jeda bertambah) sebelum menyerah, jadi biasanya error ini hilang sendiri. Jika tetap gagal, jalankan ulang beberapa menit kemudian.

**10. Bisakah tanpa merekam sama sekali?**
Bisa. Gunakan rekaman lokal Zoom ("Record to computer"), ekstrak audionya dengan ffmpeg:
```bash
ffmpeg -i "Rapat_zoom.mp4" -ar 16000 -ac 1 rekaman.wav
python zoom_notulen.py --audio rekaman.wav
```

**11. File output ada di mana?**
Di folder yang sama dengan skrip: `rekaman_zoom_<timestamp>.wav`, `Notulen_Rapat_<timestamp>.md`, dan `Notulen_Rapat_<timestamp>.txt`.

---

## 🧪 Pengujian

Tes unit (berbasis `unittest` bawaan Python) tersedia untuk fungsi penyimpanan
(`simpan_notulen`, `simpan_transkrip`), parsing argumen CLI, transkripsi lokal
maupun cloud (Gemini di-mock). Tes berjalan **tanpa perlu menginstal dependensi
pihak ketiga** (semua di-mock):

```bash
python -m unittest test_zoom_notulen -v
```

Contoh keluaran: `Ran 105 tests ... OK`.

Tes audit juga punya file tersendiri:

```bash
python -m unittest test_audit_notulen -v
```

### 🕵️ Audit anti-halusinasi notulen

Periksa semua notulen apakah Action Items memakai nama yang tidak muncul di transkrip
(kemungkinan halusinasi Gemini — nama dikarang dari Daftar Peserta):

```bash
python audit_notulen.py                      # audit semua, cetak laporan
python audit_notulen.py --output laporan.md  # simpan laporan ke file Markdown
python audit_notulen.py --berhenti-gagal     # exit code 1 jika ada halusinasi (untuk CI/otomatisasi)
```

Atau jalankan sekali-klik lewat menu **6** di `run.bat`. Skrip ini **murni lokal
tanpa API** (tidak memakai kuota Gemini). Notulen yang dibuat
ulang dari transkrip lama (timestamp berbeda) dipetakan otomatis lewat `PEMETAAN_TRANSKRIP`
di bagian atas `audit_notulen.py`.

---

## 🗂️ Struktur Proyek

```
Notulen/
├── zoom_notulen.py       # skrip utama
├── index_notulen.py           # buat daftar isi semua notulen
├── konfigurasi.example.json    # template pengaturan default rapat (salin → konfigurasi.json)
├── test_zoom_notulen.py       # tes unit skrip utama (unittest)
├── test_index_notulen.py      # tes unit skrip index (unittest)
├── test_audit_notulen.py      # tes unit audit_notulen (unittest)
├── audit_notulen.py           # audit anti-halusinasi: cek Action Items vs transkrip (jalankan kapan pun)
├── run.bat               # peluncur sekali-klik: menu rekam/index/tes + pilih engine lokal/gemini (Windows)
├── run-index.bat         # buat index notulen sekali-klik (Windows)
├── run-test.bat          # jalankan tes unit sekali-klik (Windows)
├── requirements.txt     # dependensi
├── .env.example         # template API key (salin → .env)
├── .env                 # API key Anda (TIDAK di-commit)
├── .gitignore           # file yang diabaikan git
└── README.md            # dokumentasi ini
```

---

## 🧯 Troubleshooting Cepat

| Masalah | Solusi |
|---|---|
| `ModuleNotFoundError: No module named 'dotenv'` | `pip install -r requirements.txt` |
| `ERROR: gagal membuka stream loopback` | Perangkat sedang dipakai aplikasi lain / output tidak aktif. Tutup pemutar audio, lalu coba `--list-devices` + `--perangkat` |
| Index `--perangkat` ditolak | Pastikan index dari hasil `--list-devices` dan gunakan bersama `--sumber loopback` |
| Gemini error (rate limit / quota) | Tunggu sebentar atau ganti model via `--model` |
| Hasil transkrip acak-acakan | Naikkan model Whisper (`WHISPER_MODEL` di kode) atau perbaiki kualitas rekaman |

---

## ⚖️ Catatan Legal & Privasi

Perekaman percakapan orang lain membutuhkan **persetujuan semua pihak** di banyak yurisdiksi. Gunakan alat ini secara bertanggung jawab, hanya untuk rapat yang Anda ikuti dan dengan sepengetahuan peserta.

---

Dibuat dengan ❤️ — selamat mencatat rapat tanpa ribet!
