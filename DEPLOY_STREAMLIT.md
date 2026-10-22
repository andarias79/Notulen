# 🚀 Deploy ke Streamlit Community Cloud

Aplikasi ini bisa dijalankan online (gratis) langsung dari repo GitHub Anda
menggunakan **Streamlit Community Cloud**.

## Prasyarat

- Kode sudah di-push ke `https://github.com/andarias79/Notulen` (branch `master`).
- Akun GitHub; akun Streamlit dibuat lewat sign-in GitHub.
- API key Gemini (dapat di [aistudio.google.com/apikey](https://aistudio.google.com/apikey)).

## ✅ Cek sebelum push: pastikan `.venv` tidak ikut ter-commit

Environment lokal (`.venv` hasil `setup.bat`) **tidak boleh di-push** ke GitHub:
ukurannya besar, berisi binary khusus Windows, dan Streamlit Cloud membangun
sendiri dari `requirements.txt` + `packages.txt` — bukan dari venv lokal Anda.
`.gitignore` sudah memblokirnya, tapi uji ulang sekali ini sebelum deploy:

```bash
# 1. Tidak boleh muncul .venv/ (atau venv/) di daftar perubahan
git status --short

# 2. Harus mengembalikan aturan ignore (artinya aman)
git check-ignore -v .venv

# 3. Tidak boleh ada file venv yang sudah terlacak git
git ls-files | grep -iE "\.?venv/"
```

Kalau di poin 3 muncul file (biasanya karena venv dibuat sebelum `.gitignore`
diperbarui), singkirkan dari pelacakan tanpa menghapus file lokal:

```bash
git rm -r --cached .venv

git commit -m "chore: singkirkan venv dari pelacakan git"
```

Lalu samakan isi `requirements.txt` dengan dependensi yang dipakai
(`setup.bat` menginstal persis file ini), sehingga versi di cloud = versi lokal.

## Langkah deploy

1. Buka <https://share.streamlit.io> lalu **sign in dengan akun GitHub**.
2. Klik **Create app** → **Paste GitHub URL**, isi:
   - Repository: `andarias79/Notulen`
   - Branch: `master`
   - Main file path: `streamlit_app.py`
3. Klik **Deploy**. Build pertama memakan waktu beberapa menit
   (Streamlit Cloud otomatis membaca:
   - `requirements.txt` → dependensi Python (`PyAudioWPatch` otomatis dilewati
     di Linux via marker `sys_platform == "win32"`),
   - `packages.txt` → paket sistem Apt (`libportaudio2` untuk sounddevice),
   - `.streamlit/config.toml` → tema/server).
4. Aplikasi mendapat URL publik seperti `https://andarias79-notulen.streamlit.app`.

## 🔐 Mengisi secrets (pengganti `.env`)

File `.env` **tidak ikut ter-push** ke GitHub — itu memang bagus. Di cloud,
API key diisi lewat fitur *Secrets* milik Streamlit Cloud:

1. Buka aplikasi Anda di **share.streamlit.io** → menu titik-titik
   (**⋮**) di samping nama app → **Settings** → **Secrets**.
2. Tempel format TOML berikut (persis formatnya di
   `.streamlit/secrets.toml.example`):

   ```toml
   # butuh
   GEMINI_API_KEY = "ganti_dengan_API_KEY_asli"

   # opsional: kirim email via Resend
   RESEND_API_KEY = "re_xxxxxxxxxxxx"
   RESEND_PENGIRIM = "onboarding@resend.dev"
   ```

3. Klik **Save**, lalu **Reboot app** dari menu **⋮** apabila app sudah berjalan.

Aplikasi sudah dikodekan agar otomatis menyalin `st.secrets` ke
environment variable sebelum `zoom_notulen` diimport
(lihat bagian atas `streamlit_app.py`) — tidak perlu mengubah apa pun.

> ⚠️ **Keamanan**: jangan pernah menaruh API key di `config.toml`, di kode,
> atau meng-commit `.streamlit/secrets.toml` (sudah diblokir `.gitignore`).

## Batas fitur di cloud (Linux)

| Fitur | Status di cloud | Alternatif |
|---|---|---|
| Upload audio/video + transkripsi (lokal & Gemini) | ✅ jalan | — |
| YouTube download + notulen | ✅ jalan | — |
| Subtitle / translate / PDF / email | ✅ jalan | — |
| 🔊 Rekam WASAPI loopback (tangkap semua peserta) | ❌ khusus Windows | Rekaman "Record to computer" Zoom → upload hasil audionya |
| 🎙️ Rekam mikrofon dari Web UI | ❌ (browser/server tanpa perangkat input) | Upload file audio |
| Tombol `q` di terminal untuk stop | ❌ (tidak ada terminal) | Tombol **⏹️ Hentikan Rekaman** untuk sesi lokal |

Rekaman panjang via mode lokal (faster-whisper `large-v3-turbo`, ~3,1 GB)
di cloud gratis bisa lambat/kena batas memori — gunakan **engine Gemini**
di sidebar untuk hasil tercepat di cloud.

## Menjalankan ulang / memperbarui

- Setiap `git push` ke `master` memicu redeploy otomatis.
- Aplikasi free tier tidur setelah tidak dipakai; refresh URL untuk membangunkan.

## Jalankan dengan secrets secara LOKAL (opsional)

```bash
cp .streamlit/secrets.toml.example .streamlit/secrets.toml   # lalu isi API key
streamlit run streamlit_app.py                               # → http://localhost:8501
```

Uraian lengkap aplikasi (fitur, CLI, FAQ) tetap di **README.md**.
