#!/usr/bin/env python3
"""
SOLUTION: Working audio recording configuration for Chronicle project.

This script demonstrates the correct way to handle audio recording
after investigating the "Error querying device 7" issue.
"""

import sounddevice as sd
import numpy as np

def get_working_audio_config():
    """
    Returns the working audio configuration based on investigation results.
    
    Returns:
        dict: Configuration parameters for sounddevice recording
    """
    return {
        'device': None,        # Use default device (not device 7!)
        'channels': 1,         # Mono recording works reliably
        'samplerate': 44100,   # Standard sample rate
        'dtype': np.float32    # Float32 for good precision
    }

def record_audio_safe(duration_seconds=5.0):
    """
    Safely record audio using the working configuration.
    
    Args:
        duration_seconds (float): How long to record
        
    Returns:
        np.ndarray: Recorded audio data, or None if failed
    """
    config = get_working_audio_config()
    
    try:
        print(f"Recording {duration_seconds} seconds of audio...")
        
        # Calculate number of frames
        frames = int(duration_seconds * config['samplerate'])
        
        # Record audio with working configuration
        recording = sd.rec(
            frames,
            samplerate=config['samplerate'],
            channels=config['channels'],
            device=config['device'],
            dtype=config['dtype']
        )
        
        # Wait for recording to complete
        sd.wait()
        
        # Verify we got data
        if recording is not None and len(recording) > 0:
            max_amp = np.max(np.abs(recording))
            print(f"✓ Successfully recorded {len(recording)} samples")
            print(f"  Max amplitude: {max_amp:.6f}")
            return recording
        else:
            print("✗ No audio data received")
            return None
            
    except Exception as e:
        print(f"✗ Recording error: {e}")
        return None

def main():
    """Demonstrate the working audio recording solution"""
    
    print("=== AUDIO RECORDING SOLUTION ===")
    print()
    
    # Show the issue and solution
    print("PROBLEM IDENTIFIED:")
    print("  - 'Error querying device 7' means device 7 doesn't exist")
    print("  - Your system only has devices 0 and 1 (both PulseAudio)")
    print("  - Device 7 was likely from a different system or configuration")
    print()
    
    print("SOLUTION:")
    config = get_working_audio_config()
    print("  - Use device=None (default device) instead of device=7")
    print("  - Use channels=1 (mono) for reliability")
    print("  - Use samplerate=44100 (standard)")
    print("  - Use dtype=np.float32 for precision")
    print()
    
    print("Current system devices:")
    devices = sd.query_devices()
    for i, device in enumerate(devices):
        inputs = device['max_input_channels']
        outputs = device['max_output_channels']
        print(f"  Device {i}: {device['name']} ({inputs} in, {outputs} out)")
    print()
    
    # Test the solution
    print("TESTING THE SOLUTION:")
    recording = record_audio_safe(2.0)
    
    if recording is not None:
        print("\n✓ SUCCESS! Audio recording is now working.")
        print("\nFor your Chronicle project, use this configuration:")
        print("```python")
        print("import sounddevice as sd")
        print("import numpy as np")
        print()
        print("# Working configuration")
        print("recording = sd.rec(")
        print("    frames=int(duration * 44100),")
        print("    samplerate=44100,")
        print("    channels=1,")
        print("    device=None,  # Use default, not device=7!")
        print("    dtype=np.float32")
        print(")")
        print("sd.wait()")
        print("```")
    else:
        print("\n✗ Still having issues. May need system-level troubleshooting.")

if __name__ == "__main__":
    main()