
import time
import logging
from src.audio_capture.core import DualSourceChunkedRecorder

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

def main():
    recorder = DualSourceChunkedRecorder(session_path="sessions/test_session")
    recorder.start()
    print("Recording for 15 seconds...")
    time.sleep(15)
    mic_chunks, sys_chunks = recorder.stop()
    print(f"Mic chunks: {len(mic_chunks)}")
    print(f"System chunks: {len(sys_chunks)}")

if __name__ == "__main__":
    main()
