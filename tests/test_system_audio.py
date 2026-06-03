import soundcard as sc 
import soundfile as sf

DURATION_SECONDS = 10
SAMPLE_RATE = 48000
OUTPUT_FILE = "system_audio.wav"

def record_system_audio():
    speakers = sc.all_speakers()

    print("\nAvailable speakers:")
    for i, speaker in enumerate(speakers):
        print(f"[{i}] {speaker.name}")

    # Change index if needed
    speaker = sc.default_speaker()
    print(f"\nUsing speaker: {speaker.name}")
    print(f"Recording for {DURATION_SECONDS} seconds...")
    print("Play some audio now.")

    # Important: loopback=True
    mic = sc.get_microphone(
        id=str(speaker.id),
        include_loopback=True
    )

    with mic.recorder(samplerate=SAMPLE_RATE) as recorder:
        data = recorder.record(
            numframes=DURATION_SECONDS * SAMPLE_RATE
        )

    sf.write(OUTPUT_FILE, data, SAMPLE_RATE)
    print(f"\nSaved recording to: {OUTPUT_FILE}")


if __name__ == "__main__":
    record_system_audio()