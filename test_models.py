import os, dotenv
import google.genai as genai

dotenv.load_dotenv()
client = genai.Client(api_key=os.getenv('GEMINI_API_KEY'))

# List all available models
print("=== All available models ===")
for m in client.models.list():
    print(m.name)

# Test which flash models work for audio
print("\n=== Testing audio support ===")
# Create a tiny test wav
import wave, struct
with wave.open('test_tiny.wav', 'w') as f:
    f.setnchannels(1)
    f.setsampwidth(2)
    f.setframerate(16000)
    f.writeframes(struct.pack('<h', 0) * 16000)

candidates = [
    "gemini-3.6-flash",
    "gemini-3-flash-preview",
    "gemini-flash-latest",
    "gemini-flash-lite-latest",
    "gemini-3.1-flash-lite",
]

for model_name in candidates:
    print(f"\nTesting {model_name}...")
    try:
        berkas = client.files.upload(file='test_tiny.wav')
        resp = client.models.generate_content(model=model_name, contents=[berkas, 'say hello'])
        print(f"  OK: {resp.text[:50] if resp.text else '(empty)'}")
        client.files.delete(name=berkas.name)
    except Exception as e:
        print(f"  FAIL: {e}")
        try:
            client.files.delete(name=berkas.name)
        except:
            pass

os.remove('test_tiny.wav')
