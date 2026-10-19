#!/usr/bin/env python3
"""Transcribe a single audio file from session 12 and print the transcript.

Usage:
    python test_process_session12.py
"""

import sys
from pathlib import Path

# Make sure src/ is importable
sys.path.insert(0, str(Path(__file__).parent / 'src'))

from transcription.processor import TranscriptionProcessor
from transcription.parakeet import ModelLoadError


def main():
    session_id = 12
    session_path = Path('sessions') / f'session_{session_id:03d}'

    # Instantiate processor preferring Whisper's 'small' model
    processor = TranscriptionProcessor(str(session_path), db=None, model_path='small')

    # Ensure model is loaded
    try:
        processor.load_model()
    except ModelLoadError as e:
        print(f"ERROR: Failed to load any STT backend: {e}")
        print("Please install a supported backend (openai-whisper + torch, parakeet-ctc, or coqui-stt) and try again.")
        return
    except Exception as e:
        print(f"ERROR: Unexpected error while loading STT model: {e}")
        return

    # Target file relative to session path
    target_rel = Path('audio') / 'mic' / '20260526_140044_20260526_140053.wav'
    target_path = session_path / target_rel

    if not target_path.exists():
        print(f"Target file not found: {target_path}")
        return

    print(f"Transcribing file: {target_path}")
    try:
        text = processor.transcribe_audio(str(target_path))
        print('\n--- Transcript ---')
        print(text)
        print('--- End Transcript ---\n')
    except Exception as e:
        import traceback
        print(f"ERROR: Transcription failed: {repr(e)}")
        print(traceback.format_exc())


if __name__ == '__main__':
    main()
