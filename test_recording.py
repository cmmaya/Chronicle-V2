#!/usr/bin/env python3
"""
Practical audio recording test to verify working configurations.
"""

import sounddevice as sd
import numpy as np
import time
import wave

def test_basic_recording():
    """Test basic audio recording with the recommended configuration"""
    print("Testing basic audio recording...")
    
    # Recommended configuration from investigation
    duration = 3.0  # 3 seconds
    sample_rate = 44100
    channels = 1  # Mono
    device = None  # Default device
    
    print(f"Recording for {duration} seconds...")
    print("Speak into your microphone now!")
    
    try:
        # Record audio
        recording = sd.rec(
            int(duration * sample_rate),
            samplerate=sample_rate,
            channels=channels,
            device=device,
            dtype=np.float32
        )
        
        # Wait for recording to complete
        sd.wait()
        
        # Analyze the recording
        max_amplitude = np.max(np.abs(recording))
        mean_amplitude = np.mean(np.abs(recording))
        
        print(f"✓ Recording completed successfully!")
        print(f"  - Samples recorded: {len(recording)}")
        print(f"  - Max amplitude: {max_amplitude:.6f}")
        print(f"  - Mean amplitude: {mean_amplitude:.6f}")
        
        # Check if we actually captured sound
        if max_amplitude > 0.001:  # Threshold for detecting actual sound
            print(f"✓ Audio input detected! Recording contains sound.")
        else:
            print(f"⚠ Very quiet recording - check microphone levels")
        
        # Save to file as test
        filename = "/tmp/test_recording.wav"
        try:
            # Convert to int16 for WAV file
            recording_int16 = (recording * 32767).astype(np.int16)
            
            with wave.open(filename, 'w') as wf:
                wf.setnchannels(channels)
                wf.setsampwidth(2)  # 2 bytes for int16
                wf.setframerate(sample_rate)
                wf.writeframes(recording_int16.tobytes())
            
            print(f"✓ Recording saved to {filename}")
        except Exception as e:
            print(f"⚠ Could not save file: {e}")
        
        return True
        
    except Exception as e:
        print(f"✗ Recording failed: {e}")
        return False

def test_device_robustness():
    """Test multiple quick recordings to ensure stability"""
    print("\nTesting recording stability (5 short recordings)...")
    
    success_count = 0
    for i in range(5):
        try:
            recording = sd.rec(
                int(0.5 * 44100),  # 0.5 seconds
                samplerate=44100,
                channels=1,
                device=None,
                dtype=np.float32
            )
            sd.wait()
            
            if len(recording) > 0:
                success_count += 1
                print(f"  Test {i+1}: ✓ Success")
            else:
                print(f"  Test {i+1}: ✗ No data")
                
        except Exception as e:
            print(f"  Test {i+1}: ✗ Error: {e}")
    
    print(f"\nStability test: {success_count}/5 recordings successful")
    return success_count == 5

def main():
    """Main test function"""
    print("=== PRACTICAL AUDIO RECORDING TEST ===")
    print()
    
    # Show current audio setup
    print("Current audio configuration:")
    print(f"  - Default devices: input={sd.default.device[0]}, output={sd.default.device[1]}")
    print(f"  - Available devices: {len(sd.query_devices())} total")
    print()
    
    # Test basic recording
    basic_success = test_basic_recording()
    
    # Test stability
    stability_success = test_device_robustness()
    
    # Final recommendations
    print("\n=== RECOMMENDATIONS ===")
    
    if basic_success and stability_success:
        print("✓ Audio recording is working properly!")
        print("\nRecommended configuration for your application:")
        print("  - device=None (use default)")
        print("  - channels=1 (mono)")
        print("  - samplerate=44100")
        print("  - dtype=np.float32")
        print("\nExample code:")
        print("  recording = sd.rec(")
        print("      frames, samplerate=44100, channels=1,")
        print("      device=None, dtype=np.float32")
        print("  )")
        
    else:
        print("⚠ Some issues detected:")
        if not basic_success:
            print("  - Basic recording failed")
        if not stability_success:
            print("  - Stability issues detected")
        
        print("\nTroubleshooting suggestions:")
        print("  1. Check system audio settings")
        print("  2. Verify microphone permissions")
        print("  3. Try different sample rates (22050, 48000)")
        
    print(f"\nDevice 7 issue: The original error occurs because device 7 doesn't exist")
    print(f"on this system. Only devices 0 and 1 are available (both PulseAudio).")

if __name__ == "__main__":
    main()