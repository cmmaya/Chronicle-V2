import time
import traceback
from pathlib import Path

from src.audio_capture import create_chunked_recorder


def run_test():
    session_path = Path('sessions/session_test')
    # clean session path
    if session_path.exists():
        # remove existing files but avoid importing shutil to keep simple
        for p in session_path.rglob('*'):
            try:
                if p.is_file():
                    p.unlink()
            except Exception:
                pass
    session_path.mkdir(parents=True, exist_ok=True)

    # Create a chunked recorder for system audio with short chunks (5s)
    recorder = create_chunked_recorder(str(session_path), source='system', chunk_duration=5)

    print(f"Starting system chunked recorder with session_path={session_path}")
    try:
        recorder.start()
        # Wait slightly longer than one chunk duration to allow a chunk to be produced
        time.sleep(6)
        chunks = recorder.stop()
        print(f"Recorder stopped. Chunks captured: {len(chunks)}")
        for c in chunks:
            print(f"- {c.chunk_id}: {c.file_path} ({c.duration}s)")

        system_dir = session_path / 'audio' / 'system'
        print('\nFiles in system audio directory:')
        if system_dir.exists():
            for f in sorted(system_dir.iterdir()):
                print(f"- {f.name}")
        else:
            print('System audio directory does not exist')

    except Exception as e:
        print('Test failed with exception:')
        traceback.print_exc()


if __name__ == '__main__':
    run_test()
