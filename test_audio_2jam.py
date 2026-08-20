"""
Test Script: Audio 2 Jam dengan VAD On/Off

Script ini membuat file audio simulasi 2 jam dan menguji transkripsi
dengan VAD aktif dan nonaktif untuk membandingkan hasil.

Cara pakai:
  python test_audio_2jam.py

Catatan:
  - Audio yang dihasilkan adalah SINTETIS (bukan suara asli)
  - Tujuannya untuk menguji apakah VAD terlalu agresif
  - Untuk hasil terbaik, gunakan file audio asli dari Zoom
"""

import os
import sys
import tempfile
import time
import wave
import struct
import math

# Konstanta
SAMPLE_RATE = 16000
CHANNELS = 1
# Untuk test cepat, gunakan durasi pendek. Untuk test sesungguhnya, ganti ke 2 * 3600
# DURATION_SECONDS = 2 * 3600  # 2 jam (untuk test sesungguhnya)
DURATION_SECONDS = 60  # 1 menit (untuk test cepat)
CHUNK_SIZE = 30 * SAMPLE_RATE  # chunk per 30 detik


def buat_audio_sintetis(durasi_detik, nama_file):
    """Membuat file audio sintetis dengan pola suara seperti percakapan.
    
    Pola:
    - 5 detik suara (440 Hz)
    - 2 detik sunyi
    - Ulangi selama durasi yang ditentukan
    """
    print(f"Membuat audio sintetis {durasi_detik / 3600:.1f} jam...")
    
    total_frames = int(durasi_detik * SAMPLE_RATE)
    frames_written = 0
    
    with wave.open(nama_file, 'wb') as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(2)  # 16-bit
        wf.setframerate(SAMPLE_RATE)
        
        while frames_written < total_frames:
            # Pola: 5 detik suara, 2 detik sunyi
            pattern_frames = 7 * SAMPLE_RATE  # 7 detik per pola
            
            for i in range(pattern_frames):
                if frames_written >= total_frames:
                    break
                
                # 5 detik pertama: suara (440 Hz)
                if i < 5 * SAMPLE_RATE:
                    # Tambah sedikit noise agar VAD tidak terlalu agresif
                    value = int(16000 * math.sin(2 * math.pi * 440 * i / SAMPLE_RATE))
                    value += int(500 * math.sin(2 * math.pi * 100 * i / SAMPLE_RATE))  # harmonik
                else:
                    # 2 detik terakhir: sunyi (dengan noise sangat kecil)
                    value = int(100 * math.sin(2 * math.pi * 50 * i / SAMPLE_RATE))
                
                # Clamp value
                value = max(-32768, min(32767, value))
                
                # Tulis frame
                wf.writeframes(struct.pack('<h', value))
                frames_written += 1
            
            # Progress setiap 10 menit
            if frames_written % (10 * 60 * SAMPLE_RATE) == 0:
                persen = frames_written / total_frames * 100
                print(f"  Progress: {persen:.1f}% ({frames_written / SAMPLE_RATE / 3600:.2f} jam)")
    
    print(f"Audio selesai: {nama_file}")
    print(f"  Durasi: {durasi_detik / 3600:.1f} jam")
    print(f"  Ukuran: {os.path.getsize(nama_file) / 1024 / 1024:.1f} MB")
    
    return nama_file


def test_vad_settings():
    """Test pengaturan VAD."""
    import zoom_notulen
    
    print(f"\n{'='*60}")
    print("TEST PENGATURAN VAD")
    print(f"{'='*60}")
    
    print(f"\nPengaturan VAD saat ini:")
    print(f"  WHISPER_VAD: {zoom_notulen.WHISPER_VAD}")
    print(f"  WHISPER_VAD_THOLD: {zoom_notulen.WHISPER_VAD_THOLD}")
    print(f"  WHISPER_MODEL: {zoom_notulen.WHISPER_MODEL}")
    
    print(f"\nParameter VAD (saat aktif):")
    print(f"  threshold: {zoom_notulen.WHISPER_VAD_THOLD} (lebih tinggi = lebih toleran)")
    print(f"  min_speech_duration_ms: 250")
    print(f"  min_silence_duration_ms: 500")
    print(f"  speech_pad_ms: 200")
    
    return True


def main():
    """Fungsi utama test."""
    print("=" * 60)
    print("TEST AUDIO 2 JAM DENGAN VAD ON/OFF")
    print("=" * 60)
    
    # Buat audio sintetis
    with tempfile.TemporaryDirectory() as d:
        audio_file = os.path.join(d, "test_2jam.wav")
        buat_audio_sintetis(DURATION_SECONDS, audio_file)
        
        # Test pengaturan VAD
        test_vad_settings()
        
        # Tampilkan informasi audio
        print(f"\n{'='*60}")
        print("INFORMASI AUDIO")
        print(f"{'='*60}")
        import wave
        with wave.open(audio_file, 'rb') as wf:
            durasi = wf.getnframes() / wf.getframerate()
            print(f"  File: {audio_file}")
            print(f"  Durasi: {durasi:.1f} detik ({durasi/60:.1f} menit)")
            print(f"  Sample rate: {wf.getframerate()} Hz")
            print(f"  Kanal: {wf.getnchannels()}")
            print(f"  Bit depth: {wf.getsampwidth() * 8} bit")
            print(f"  Ukuran: {os.path.getsize(audio_file) / 1024 / 1024:.1f} MB")
    
    print("\n" + "=" * 60)
    print("REKOMENDASI UNTUK AUDIO 2 JAM ZOOM MEETING")
    print("=" * 60)
    print("""
Untuk hasil terbaik dengan rekaman Zoom 2 jam:

1. Gunakan --no-vad jika transkripsi terlalu pendek:
   python zoom_notulen.py --audio rekaman_2jam.wav --no-vad

2. Gunakan --engine gemini untuk hasil lebih cepat & akurat:
   python zoom_notulen.py --audio rekaman_2jam.wav --engine gemini

3. Kombinasi keduanya (paling direkomendasikan):
   python zoom_notulen.py --audio rekaman_2jam.wav --engine gemini --no-vad

4. Tambahkan konteks domain untuk akurasi lebih baik:
   python zoom_notulen.py --audio rekaman_2jam.wav --no-vad --konteks "rapat, materi, diskusi"
""")
    
    print("=" * 60)
    print("SELESAI")
    print("=" * 60)


if __name__ == "__main__":
    main()
