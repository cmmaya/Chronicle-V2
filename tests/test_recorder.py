#!/usr/bin/env python3
"""
Test script for the updated AudioRecorder class.
Tests recording with system default device and validates the configuration.
"""

import sys
import time
import logging
from pathlib import Path

# Add src to path so we can import our modules
sys.path.insert(0, str(Path(__file__).parent / "src"))

from audio.recorder import AudioRecorder

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

def test_audio_recorder():
    """Test the AudioRecorder with the new configuration."""
    
    # Create test output directory
    test_dir = Path(__file__).parent / "test_output"
    test_dir.mkdir(exist_ok=True)
    
    # Create recorder instance
    recorder = AudioRecorder()
    
    # Verify default configuration
    print("AudioRecorder Configuration:")
    print(f"  Device: {recorder.device_index} (system default)")
    print(f"  Channels: {recorder.channels}")
    print(f"  Sample Rate: {recorder.sample_rate}")
    print(f"  Data Type: float32")
    
    # Test file path
    test_file = test_dir / f"test_recording_{int(time.time())}.wav"
    
    try:
        print(f"\nStarting 3-second test recording...")
        print("Speak into your microphone now!")
        
        # Start recording with system default device
        recorder.start_recording()
        
        # Record for 3 seconds
        time.sleep(3)
        
        # Stop and save recording
        recorder.stop_recording(str(test_file))
        
        print(f"✓ Recording saved successfully to: {test_file}")
        
        # Check file exists and has reasonable size
        if test_file.exists():
            file_size = test_file.stat().st_size
            print(f"✓ File size: {file_size} bytes")
            
            if file_size > 1000:  # Should be at least 1KB for 3 seconds
                print("✓ Test PASSED: Recording appears to contain audio data")
                return True
            else:
                print("✗ Test FAILED: File too small, may not contain audio")
                return False
        else:
            print("✗ Test FAILED: Recording file not created")
            return False
            
    except Exception as e:
        print(f"✗ Test FAILED with error: {e}")
        return False

def list_audio_devices():
    """List available audio devices for reference."""
    try:
        import sounddevice as sd
        print("\nAvailable Audio Devices:")
        print(sd.query_devices())
    except Exception as e:
        print(f"Could not list audio devices: {e}")

if __name__ == "__main__":
    print("Testing AudioRecorder with updated configuration...")
    print("=" * 50)
    
    # List available devices for reference
    list_audio_devices()
    
    print("\n" + "=" * 50)
    
    # Run the test
    success = test_audio_recorder()
    
    if success:
        print("\n🎉 AudioRecorder test completed successfully!")
    else:
        print("\n❌ AudioRecorder test failed!")
        sys.exit(1)