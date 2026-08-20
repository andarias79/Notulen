"""
Notulen Rapat Zoom - Web UI (Streamlit)

Jalankan dengan:
  streamlit run streamlit_app.py

Atau dengan opsi:
  streamlit run streamlit_app.py --server.port 8501
  streamlit run streamlit_app.py --server.headless true
"""

import os
import re
import sys
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

import streamlit as st

# Import modul utama
try:
    import zoom_notulen
except ImportError as e:
    st.error(f"Gagal mengimport zoom_notulen: {e}")
    st.stop()

# ==========================================
# KONFIGURASI HALAMAN
# ==========================================
st.set_page_config(
    page_title="Notulen Rapat Zoom",
    page_icon="📝",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ==========================================
# STYLE CUSTOM
# ==========================================
st.markdown("""
<style>
    .main-header {
        font-size: 2.5rem;
        font-weight: bold;
        color: #1f77b4;
        text-align: center;
        padding: 1rem 0;
    }
    .feature-card {
        background-color: #f0f2f6;
        border-radius: 10px;
        padding: 1rem;
        margin: 0.5rem 0;
    }
    .status-success {
        color: #28a745;
        font-weight: bold;
    }
    .status-error {
        color: #dc3545;
        font-weight: bold;
    }
</style>
""", unsafe_allow_html=True)


# ==========================================
# SESSION STATE
# ==========================================
if "transkrip" not in st.session_state:
    st.session_state.transkrip = None
if "notulen" not in st.session_state:
    st.session_state.notulen = None
if "notulen_original" not in st.session_state:
    st.session_state.notulen_original = None
if "edit_history" not in st.session_state:
    st.session_state.edit_history = []  # List of previous notulen states
if "history_index" not in st.session_state:
    st.session_state.history_index = -1  # Current position in history
if "audio_file" not in st.session_state:
    st.session_state.audio_file = None
if "is_recording" not in st.session_state:
    st.session_state.is_recording = False
if "recording_thread" not in st.session_state:
    st.session_state.recording_thread = None


def simpan_ke_history(teks):
    """Simpan teks ke history untuk undo/redo."""
    # Hapus history setelah index saat ini (jika user edit setelah undo)
    st.session_state.edit_history = st.session_state.edit_history[:st.session_state.history_index + 1]
    # Tambah state baru
    st.session_state.edit_history.append(teks)
    st.session_state.history_index = len(st.session_state.edit_history) - 1
    # Batasi history maksimal 50 item
    if len(st.session_state.edit_history) > 50:
        st.session_state.edit_history = st.session_state.edit_history[-50:]
        st.session_state.history_index = len(st.session_state.edit_history) - 1


def undo():
    """Undo: kembali ke state sebelumnya."""
    if st.session_state.history_index > 0:
        st.session_state.history_index -= 1
        st.session_state.notulen = st.session_state.edit_history[st.session_state.history_index]
        return True
    return False


def redo():
    """Redo: maju ke state berikutnya."""
    if st.session_state.history_index < len(st.session_state.edit_history) - 1:
        st.session_state.history_index += 1
        st.session_state.notulen = st.session_state.edit_history[st.session_state.history_index]
        return True
    return False


def bandingkan_teks(teks_lama, teks_baru):
    """Membandingkan dua teks dan menghasilkan diff.
    
    Mengembalikan HTML dengan highlight perubahan:
    - Hijau: baris yang ditambahkan
    - Merah: baris yang dihapus
    - Kuning: baris yang diubah
    """
    import difflib
    
    baris_lama = teks_lama.splitlines(keepends=True)
    baris_baru = teks_baru.splitlines(keepends=True)
    
    # Generate unified diff
    diff = difflib.unified_diff(
        baris_lama,
        baris_baru,
        fromfile='Original',
        tofile='Edited',
        lineterm=''
    )
    
    # Konversi ke HTML dengan warna
    html_lines = ['<div style="font-family: monospace; font-size: 12px; padding: 10px; background: #f8f9fa; border-radius: 5px; max-height: 400px; overflow-y: auto;">']
    
    jumlah_tambah = 0
    jumlah_hapus = 0
    jumlah_ubah = 0
    
    for line in diff:
        line = line.rstrip('\n')
        if line.startswith('+++') or line.startswith('---') or line.startswith('@@'):
            # Header diff - tampilkan abu-abu
            html_lines.append(f'<div style="color: #6c757d; padding: 2px 5px;">{line}</div>')
        elif line.startswith('+'):
            # Baris ditambahkan - hijau
            html_lines.append(f'<div style="background: #d4edda; color: #155724; padding: 2px 5px;">+ {line[1:]}</div>')
            jumlah_tambah += 1
        elif line.startswith('-'):
            # Baris dihapus - merah
            html_lines.append(f'<div style="background: #f8d7da; color: #721c24; padding: 2px 5px; text-decoration: line-through;">- {line[1:]}</div>')
            jumlah_hapus += 1
        else:
            # Baris tidak berubah
            html_lines.append(f'<div style="padding: 2px 5px;">{line}</div>')
    
    html_lines.append('</div>')
    
    # Tambah statistik
    stats_html = f'''
    <div style="margin-top: 10px; padding: 10px; background: #e9ecef; border-radius: 5px;">
        <strong>Statistik Perubahan:</strong>
        <span style="color: #28a745; margin-left: 10px;">+ {jumlah_tambah} ditambahkan</span>
        <span style="color: #dc3545; margin-left: 10px;">- {jumlah_hapus} dihapus</span>
        <span style="color: #ffc107; margin-left: 10px;">~ {len(baris_baru)} total baris</span>
    </div>
    '''
    
    return stats_html + ''.join(html_lines)


# ==========================================
# FUNGSI HELPER
# ==========================================
def muat_konfigurasi():
    """Muat konfigurasi dari file JSON."""
    try:
        return zoom_notulen.muat_konfigurasi()
    except Exception:
        return {}


def get_daftar_bahasa():
    """Daftar bahasa yang didukung."""
    return {
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
        "auto": "Auto-detect",
    }


def get_daftar_engine():
    """Daftar engine transkripsi."""
    return {
        "lokal": "Lokal (faster-whisper) - Gratis, Offline",
        "gemini": "Gemini (Cloud) - Super Cepat, Butuh Internet",
    }


def proses_audio(audio_path, bahasa, engine, judul=None, daftar_peserta=None,
                 notulen_mode="multi", subtitle="none", translate=None, pdf=False):
    """Fungsi utama untuk memproses audio."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Setup direktori
    DIR_TRANSKRIP = "Hasil_Transkripsi"
    DIR_NOTULEN = "Hasil_Notulen"
    os.makedirs(DIR_TRANSKRIP, exist_ok=True)
    os.makedirs(DIR_NOTULEN, exist_ok=True)

    # 1. Transkripsi
    with st.status("📝 Memproses transkripsi...", expanded=True) as status:
        st.write(f"Engine: {engine} | Bahasa: {bahasa}")
        
        # Inisialisasi progress bar
        progress_bar = st.progress(0, text="Menyiapkan model...")
        
        def update_progress(p):
            progress_bar.progress(p, text=f"Transkripsi berjalan... {int(p*100)}%")

        try:
            transkrip_teks = zoom_notulen.transkripsi_audio(
                audio_path, bahasa, engine,
                progress_callback=update_progress if engine == "lokal" else None
            )
            progress_bar.progress(1.0, text="Selesai!")
            status.update(label="✅ Transkripsi selesai!", state="complete")
        except Exception as e:
            progress_bar.empty()
            status.update(label="❌ Transkripsi gagal", state="error")
            return None, None, str(e)

    # Simpan transkrip
    nama_transkrip = os.path.join(DIR_TRANSKRIP, f"Transkrip_{timestamp}.txt")
    zoom_notulen.simpan_transkrip(transkrip_teks, nama_transkrip)

    # 2. Buat Notulen
    with st.status("🤖 Membuat notulen dengan Gemini AI...", expanded=True) as status:
        st.write(f"Mode: {notulen_mode}")
        try:
            cfg = {
                "judul": judul,
                "model": zoom_notulen.GEMINI_MODEL,
                "notulen_mode": notulen_mode,
            }
            notulen = zoom_notulen._buat_notulen_sesuai_mode(
                cfg, transkrip_teks, daftar_peserta
            )
            status.update(label="✅ Notulen selesai!", state="complete")
        except Exception as e:
            status.update(label="❌ Notulen gagal", state="error")
            return transkrip_teks, None, str(e)

    # Simpan notulen
    nama_notulen = os.path.join(DIR_NOTULEN, f"Notulen_Rapat_{timestamp}")
    zoom_notulen.simpan_notulen(notulen, nama_notulen, "both")
    
    # Simpan notulen asli untuk fitur edit/reset
    st.session_state.notulen_original = notulen
    
    # Inisialisasi history untuk undo/redo
    st.session_state.edit_history = [notulen]
    st.session_state.history_index = 0

    # 3. Subtitle (opsional)
    if subtitle != "none":
        with st.status("📄 Membuat subtitle...", expanded=True) as status:
            try:
                DIR_SUBTITLE = "Hasil_Subtitle"
                os.makedirs(DIR_SUBTITLE, exist_ok=True)
                nama_subtitle = os.path.join(DIR_SUBTITLE, f"subtitle_{timestamp}")
                zoom_notulen.simpan_subtitle(transkrip_teks, nama_subtitle, subtitle)
                status.update(label="✅ Subtitle selesai!", state="complete")
            except Exception as e:
                status.update(label="⚠️ Subtitle gagal", state="warning")

    # 4. Translate (opsional)
    if translate:
        with st.status(f"🌐 Menerjemahkan ke {translate}...", expanded=True) as status:
            try:
                DIR_TRANSLATE = "Hasil_Translate"
                os.makedirs(DIR_TRANSLATE, exist_ok=True)
                teks_translate = zoom_notulen.translate_transkrip(
                    transkrip_teks, translate, cfg["model"]
                )
                nama_translate = os.path.join(DIR_TRANSLATE, f"Transkrip_{timestamp}_{translate}.txt")
                with open(nama_translate, "w", encoding="utf-8") as f:
                    f.write(teks_translate)
                status.update(label="✅ Terjemahan selesai!", state="complete")
            except Exception as e:
                status.update(label="⚠️ Translate gagal", state="warning")

    # 5. PDF (opsional)
    if pdf:
        with st.status("📕 Membuat PDF...", expanded=True) as status:
            try:
                DIR_PDF = "Hasil_PDF"
                os.makedirs(DIR_PDF, exist_ok=True)
                nama_pdf = os.path.join(DIR_PDF, f"Notulen_{timestamp}.pdf")
                zoom_notulen.export_pdf(notulen, nama_pdf, judul or "Notulen Rapat")
                status.update(label="✅ PDF selesai!", state="complete")
            except Exception as e:
                status.update(label="⚠️ PDF gagal", state="warning")

    return transkrip_teks, notulen, None


# ==========================================
# SIDEBAR
# ==========================================
with st.sidebar:
    st.image("https://img.icons8.com/color/96/000000/meeting.png", width=80)
    st.title("⚙️ Pengaturan")

    # Muat konfigurasi
    konfig = muat_konfigurasi()

    # Engine transkripsi
    st.subheader("🔧 Engine Transkripsi")
    engine_options = get_daftar_engine()
    engine = st.selectbox(
        "Pilih Engine",
        options=list(engine_options.keys()),
        format_func=lambda x: engine_options[x],
        index=0,
    )

    # Bahasa
    st.subheader("🌍 Bahasa")
    bahasa_options = get_daftar_bahasa()
    bahasa = st.selectbox(
        "Bahasa Transkripsi",
        options=list(bahasa_options.keys()),
        format_func=lambda x: bahasa_options[x],
        index=0,
    )

    # Notulen Mode
    st.subheader("📋 Mode Notulen")
    notulen_mode = st.radio(
        "Mode",
        options=["multi", "single"],
        format_func=lambda x: "Multi-pass (Akurat)" if x == "multi" else "Single-pass (Cepat)",
        index=0,
    )

    # Opsi Tambahan
    st.subheader("➕ Opsi Tambahan")
    subtitle = st.selectbox(
        "Subtitle",
        options=["none", "srt", "vtt", "both"],
        format_func=lambda x: {
            "none": "Tidak",
            "srt": "SRT (.srt)",
            "vtt": "VTT (.vtt)",
            "both": "SRT + VTT",
        }[x],
        index=0,
    )

    translate = st.selectbox(
        "Translate",
        options=[None, "en", "ja", "ko", "zh", "ms"],
        format_func=lambda x: {
            None: "Tidak",
            "en": "English",
            "ja": "Japanese",
            "ko": "Korean",
            "zh": "Chinese",
            "ms": "Melayu",
        }[x],
        index=0,
    )

    export_pdf = st.checkbox("Export ke PDF", value=False)


# ==========================================
# MAIN CONTENT
# ==========================================
st.markdown('<h1 class="main-header">📝 Notulen Rapat Zoom</h1>', unsafe_allow_html=True)
st.markdown("---")

# Tab utama
tab1, tab2, tab3, tab4 = st.tabs([
    "📁 Upload Audio/Video",
    "🎙️ Rekam Audio",
    "📊 Hasil",
    "ℹ️ Bantuan",
])


# ==========================================
# TAB 1: UPLOAD AUDIO/VIDEO
# ==========================================
with tab1:
    st.header("📁 Upload File Audio/Video")

    col1, col2 = st.columns([2, 1])

    with col1:
        uploaded_file = st.file_uploader(
            "Pilih file audio atau video",
            type=["wav", "mp3", "m4a", "ogg", "flac", "mp4", "mkv", "avi", "mov", "webm"],
            help="Format yang didukung: WAV, MP3, M4A, OGG, FLAC, MP4, MKV, AVI, MOV, WEBM",
        )

    with col2:
        judul = st.text_input("Judul Rapat (opsional)", placeholder="Contoh: Rapat Mingguan Tim")
        daftar_peserta = st.text_input("Daftar Peserta (opsional)", placeholder="Budi, Siti, Andi")

    if uploaded_file is not None:
        # Simpan file yang diupload
        with tempfile.NamedTemporaryFile(delete=False, suffix=f"_{uploaded_file.name}") as tmp:
            tmp.write(uploaded_file.getbuffer())
            st.session_state.audio_file = tmp.name

        st.success(f"✅ File terupload: **{uploaded_file.name}**")
        st.info(f"📏 Ukuran: {uploaded_file.size / 1024 / 1024:.2f} MB")

        # Tombol proses
        if st.button("🚀 Proses Sekarang", type="primary", use_container_width=True):
            transkrip, notulen, error = proses_audio(
                st.session_state.audio_file,
                bahasa,
                engine,
                judul=judul or None,
                daftar_peserta=daftar_peserta or None,
                notulen_mode=notulen_mode,
                subtitle=subtitle,
                translate=translate,
                pdf=export_pdf,
            )

            if error:
                st.error(f"❌ Error: {error}")
            else:
                st.session_state.transkrip = transkrip
                st.session_state.notulen = notulen
                st.success("✅ Proses selesai! Lihat tab 'Hasil' untuk melihat hasilnya.")


# ==========================================
# TAB 2: REKAM AUDIO
# ==========================================
with tab2:
    st.header("🎙️ Rekam Audio")

    col1, col2 = st.columns(2)

    with col1:
        sumber = st.selectbox(
            "Sumber Audio",
            options=["mic", "loopback"],
            format_func=lambda x: "🎤 Mikrofon" if x == "mic" else "🔊 Audio Sistem (Loopback)",
        )

    with col2:
        durasi = st.number_input(
            "Durasi (detik)",
            min_value=10,
            max_value=14400,
            value=3600,
            step=60,
            help="Durasi perekaman dalam detik (mis. 3600 = 1 jam)",
        )

    # Info loopback
    if sumber == "loopback":
        st.info(
            "💡 **WASAPI Loopback**: Merekam semua audio yang keluar dari speaker. "
            "Cocok untuk merekam rapat Zoom/Meet. Pastikan volume audio tidak mute."
        )

    # Fungsi perekaman yang dijalankan di thread terpisah
    def _jalankan_rekaman(path, sumber, durasi):
        """Menjalankan perekaman audio di thread terpisah."""
        try:
            if sumber == "loopback":
                zoom_notulen.rekam_audio_loopback(durasi, path)
            else:
                zoom_notulen.rekam_audio(durasi, path)
        except Exception as e:
            # Simpan error ke session state agar UI bisa menampilkannya
            st.session_state.recording_error = str(e)
        finally:
            st.session_state.is_recording = False

    # Status rekaman
    if st.session_state.is_recording:
        st.warning("🔴 Sedang merekam... Tekan 'q' di terminal untuk berhenti.")
        # Cek apakah thread rekaman sudah selesai
        thread = st.session_state.get("recording_thread")
        if thread is not None and not thread.is_alive():
            # Thread selesai - proses hasilnya
            st.session_state.recording_thread = None
            err = st.session_state.pop("recording_error", None)
            if err:
                st.error(f"❌ Error saat merekam: {err}")
                st.session_state.is_recording = False
            elif st.session_state.audio_file and os.path.exists(st.session_state.audio_file) and os.path.getsize(st.session_state.audio_file) > 0:
                st.session_state.is_recording = False
                st.success("✅ Rekaman selesai! Memproses...")
                transkrip, notulen, error = proses_audio(
                    st.session_state.audio_file,
                    bahasa,
                    engine,
                    judul=judul if 'judul' in locals() else None,
                    daftar_peserta=daftar_peserta if 'daftar_peserta' in locals() else None,
                    notulen_mode=notulen_mode,
                    subtitle=subtitle,
                    translate=translate,
                    pdf=export_pdf,
                )
                if error:
                    st.error(f"❌ Error: {error}")
                else:
                    st.session_state.transkrip = transkrip
                    st.session_state.notulen = notulen
                    st.success("✅ Proses selesai!")
            else:
                st.session_state.is_recording = False
                st.warning("⚠️ Rekaman kosong atau tidak ada file.")
        else:
            # Masih merekam - tampilkan progress
            if st.button("⏹️ Hentikan Rekaman", type="secondary"):
                st.info("Tekan 'q' atau Ctrl+C di terminal untuk menghentikan rekaman.")
    else:
        if st.button("🎙️ Mulai Rekam", type="primary", use_container_width=True):
            with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp:
                st.session_state.audio_file = tmp.name

            st.session_state.is_recording = True
            st.session_state.recording_error = None

            # Jalankan perekaman di thread terpisah agar UI tidak freeze
            thread = threading.Thread(
                target=_jalankan_rekaman,
                args=(st.session_state.audio_file, sumber, durasi),
                daemon=True,
            )
            st.session_state.recording_thread = thread
            thread.start()
            st.rerun()


# ==========================================
# TAB 3: HASIL
# ==========================================
with tab3:
    st.header("📊 Hasil Proses")

    if st.session_state.transkrip and st.session_state.notulen:
        col1, col2 = st.columns(2)

        with col1:
            st.subheader("📝 Transkrip")
            st.text_area(
                "Transkrip",
                value=st.session_state.transkrip,
                height=400,
                disabled=True,
                key="transkrip_display",
            )

        with col2:
            st.subheader("📋 Notulen")
            
            # Mode toggle: Preview, Edit, atau Compare
            mode_notulen = st.radio(
                "Mode Notulen",
                options=["preview", "edit", "compare"],
                format_func=lambda x: {"preview": "👁️ Preview", "edit": "✏️ Edit", "compare": "🔍 Compare"}[x],
                horizontal=True,
                key="mode_notulen",
            )

            if mode_notulen == "preview":
                # Mode preview: tampilkan markdown
                st.markdown(st.session_state.notulen)
            
            elif mode_notulen == "compare":
                # Mode compare: tampilkan perbedaan
                if hasattr(st.session_state, 'notulen_original') and st.session_state.notulen_original:
                    if st.session_state.notulen != st.session_state.notulen_original:
                        st.markdown("**Perbedaan antara Original dan Edit:**")
                        diff_html = bandingkan_teks(st.session_state.notulen_original, st.session_state.notulen)
                        st.markdown(diff_html, unsafe_allow_html=True)
                    else:
                        st.info("✅ Tidak ada perubahan. Notulen sama dengan versi original.")
                else:
                    st.info("ℹ️ Belum ada versi original untuk dibandingkan.")
            
            else:
                # Mode edit: text area untuk edit
                
                # ── Find & Replace ──
                with st.expander("🔍 Find & Replace", expanded=False):
                    fr_col1, fr_col2 = st.columns(2)
                    with fr_col1:
                        find_text = st.text_input("Cari", key="find_text", placeholder="Teks yang dicari...")
                    with fr_col2:
                        replace_text = st.text_input("Ganti dengan", key="replace_text", placeholder="Teks pengganti...")
                    
                    fr_opts1, fr_opts2, fr_opts3 = st.columns([1, 1, 2])
                    with fr_opts1:
                        case_sensitive = st.checkbox("Case sensitive", key="find_case")
                    with fr_opts2:
                        use_regex = st.checkbox("Regex", key="find_regex")
                    with fr_opts3:
                        # Hitung jumlah match
                        match_count = 0
                        if find_text:
                            try:
                                teks_current = st.session_state.notulen or ""
                                if use_regex:
                                    flags = 0 if case_sensitive else re.IGNORECASE
                                    matches = re.findall(find_text, teks_current, flags)
                                    match_count = len(matches)
                                else:
                                    if case_sensitive:
                                        match_count = teks_current.count(find_text)
                                    else:
                                        match_count = teks_current.lower().count(find_text.lower())
                            except re.error:
                                match_count = -1  # invalid regex
                        
                        if match_count >= 0:
                            st.caption(f"🔍 Ditemukan: **{match_count}** match")
                        else:
                            st.caption("⚠️ Regex tidak valid")
                    
                    # Tombol aksi Find & Replace
                    fr_btn1, fr_btn2, fr_btn3 = st.columns(3)
                    with fr_btn1:
                        if st.button("🔄 Replace All", use_container_width=True, help="Ganti semua match"):
                            if find_text and st.session_state.notulen is not None:
                                try:
                                    teks = st.session_state.notulen
                                    if use_regex:
                                        flags = 0 if case_sensitive else re.IGNORECASE
                                        teks_baru = re.sub(find_text, replace_text, teks, flags=flags)
                                    else:
                                        if case_sensitive:
                                            teks_baru = teks.replace(find_text, replace_text)
                                        else:
                                            # Case-insensitive replace manual
                                            pattern = re.compile(re.escape(find_text), re.IGNORECASE)
                                            teks_baru = pattern.sub(replace_text, teks)
                                    
                                    if teks_baru != st.session_state.notulen:
                                        st.session_state.notulen = teks_baru
                                        simpan_ke_history(teks_baru)
                                        st.success(f"✅ Semua match diganti!")
                                        st.rerun()
                                    else:
                                        st.info("Tidak ada perubahan.")
                                except re.error as e:
                                    st.error(f"Regex error: {e}")
                    
                    with fr_btn2:
                        if st.button("◀️ Find Prev", use_container_width=True, help="Cari match sebelumnya"):
                            st.info("💡 Edit teks di bawah, lalu klik '💾 Simpan'.")
                    
                    with fr_btn3:
                        if st.button("▶️ Find Next", use_container_width=True, help="Cari match berikutnya"):
                            st.info("💡 Edit teks di bawah, lalu klik '💾 Simpan'.")
                
                notulen_edited = st.text_area(
                    "Edit Notulen",
                    value=st.session_state.notulen,
                    height=400,
                    key="notulen_edit",
                )
                
                # Toolbar Undo/Redo + Simpan
                col_undo, col_redo, col_save, col_reset, col_regenerate = st.columns([1, 1, 2, 1, 1])
                
                with col_undo:
                    if st.button("↩️ Undo", use_container_width=True, help="Kembali ke perubahan sebelumnya"):
                        if undo():
                            st.rerun()
                        else:
                            st.warning("Tidak ada yang bisa di-undo")
                
                with col_redo:
                    if st.button("↪️ Redo", use_container_width=True, help="Maju ke perubahan berikutnya"):
                        if redo():
                            st.rerun()
                        else:
                            st.warning("Tidak ada yang bisa di-redo")
                
                with col_save:
                    if st.button("💾 Simpan Perubahan", type="primary", use_container_width=True):
                        st.session_state.notulen = notulen_edited
                        simpan_ke_history(notulen_edited)
                        # Simpan ke file
                        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                        DIR_NOTULEN = "Hasil_Notulen"
                        os.makedirs(DIR_NOTULEN, exist_ok=True)
                        nama_file = os.path.join(DIR_NOTULEN, f"Notulen_Edited_{timestamp}.md")
                        with open(nama_file, "w", encoding="utf-8") as f:
                            f.write(notulen_edited)
                        st.success(f"✅ Perubahan tersimpan ke {nama_file}")
                        st.rerun()
                
                with col_reset:
                    if st.button("🔄 Reset", use_container_width=True, help="Kembalikan ke notulen asli"):
                        st.session_state.notulen = st.session_state.notulen_original if hasattr(st.session_state, 'notulen_original') else st.session_state.notulen
                        simpan_ke_history(st.session_state.notulen)
                        st.info("🔄 Notulen dikembalikan ke asli.")
                        st.rerun()
                
                with col_regenerate:
                    if st.button("🤖 Regenerate", use_container_width=True, help="Buat notulen baru dari transkrip"):
                        st.info("⏳ Membuat notulen baru...")
                        if not hasattr(st.session_state, 'notulen_original'):
                            st.session_state.notulen_original = st.session_state.notulen
                        st.rerun()
                
                # Tampilkan info history
                if st.session_state.edit_history:
                    posisi = st.session_state.history_index + 1
                    total = len(st.session_state.edit_history)
                    st.caption(f"📚 History: {posisi}/{total} perubahan")

        # Tombol download
        st.subheader("💾 Download Hasil")
        col1, col2, col3 = st.columns(3)

        with col1:
            st.download_button(
                label="📥 Download Transkrip (.txt)",
                data=st.session_state.transkrip,
                file_name=f"transkrip_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt",
                mime="text/plain",
            )

        with col2:
            st.download_button(
                label="📥 Download Notulen (.md)",
                data=st.session_state.notulen,
                file_name=f"notulen_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md",
                mime="text/markdown",
            )

        with col3:
            st.download_button(
                label="📥 Download Notulen (.txt)",
                data=st.session_state.notulen,
                file_name=f"notulen_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt",
                mime="text/plain",
            )
        
        # Tampilkan info perubahan
        if hasattr(st.session_state, 'notulen_original'):
            if st.session_state.notulen != st.session_state.notulen_original:
                st.warning("⚠️ Notulen telah dimodifikasi. Klik 'Simpan Perubahan' untuk menyimpan.")
    else:
        st.info("🔄 Belum ada hasil. Upload audio atau rekam terlebih dahulu di tab 'Upload Audio/Video' atau 'Rekam Audio'.")


# ==========================================
# TAB 4: BANTUAN
# ==========================================
with tab4:
    st.header("ℹ️ Bantuan")

    st.subheader("🔧 Fitur Utama")
    st.markdown("""
    - **Upload Audio/Video**: Upload file audio atau video untuk diproses
    - **Rekam Audio**: Rekam langsung dari mikrofon atau audio sistem
    - **Transkripsi**: Konversi audio ke teks dengan faster-whisper atau Gemini
    - **Notulen Otomatis**: Buat notulen terstruktur dengan Gemini AI
    - **Edit Notulen**: Edit notulen langsung di browser dengan undo/redo
    - **Subtitle**: Generate file subtitle .srt atau .vtt
    - **Translate**: Terjemahkan transkrip ke bahasa lain
    - **Export PDF**: Konversi hasil ke file PDF
    """)

    st.subheader("🎯 Engine Transkripsi")
    st.markdown("""
    - **Lokal (faster-whisper)**: Gratis, offline, akurasi tinggi
    - **Gemini (Cloud)**: Super cepat, butuh internet, speaker diarization
    """)

    st.subheader("🌍 Bahasa yang Didukung")
    st.markdown("""
    Indonesia, English, Melayu, Japanese, Korean, Chinese,
    Arabic, Spanish, French, German, dan auto-detect.
    """)

    st.subheader("⌨️ Tombol Keyboard (Mode Rekam)")
    st.markdown("""
    - **q**: Stop rekaman
    - **p**: Pause/Resume rekaman
    - **r**: Resume rekaman (alias p)
    - **Ctrl+C**: Stop rekaman (darurat)
    """)

    st.subheader("📁 Format File")
    st.markdown("""
    **Audio**: WAV, MP3, M4A, OGG, FLAC, AAC, WMA
    **Video**: MP4, MKV, AVI, MOV, WEBM, FLV, WMV, M4V
    """)


# ==========================================
# FOOTER
# ==========================================
st.markdown("---")
st.markdown(
    '<p style="text-align: center; color: #888;">'
    '📝 Notulen Rapat Zoom | Dibuat dengan ❤️ dan Streamlit'
    '</p>',
    unsafe_allow_html=True,
)
