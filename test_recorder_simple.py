#!/usr/bin/env python3
"""
Simple automated test for AudioRecorder functionality.
Tests the configuration and basic recording without requiring user input.
"""

import sys
import time
import logging
from pathlib import Path

# Add src to path so we can import our modules
sys.path.insert(0, str(Path(__file__).parent / "src"))

from audio.recorder import AudioRecorder

def test_recorder_config():
    """Test that the AudioRecorder has the correct configuration."""
    
    recorder = AudioRecorder()
    
    # Test default configuration
    assert recorder.device_index is None, f"Expected device_index to be None, got {recorder.device_index}"
    assert recorder.channels == 2, f"Expected 2 channels, got {recorder.channels}"
    assert recorder.sample_rate == 44100, f"Expected 44100 sample rate, got {recorder.sample_rate}"
    assert not recorder.is_recording, "Expected is_recording to be False initially"
    
    print("✓ AudioRecorder configuration test passed")

def test_recorder_start_stop():
    """Test starting and stopping the recorder (brief test)."""
    
    recorder = AudioRecorder()
    test_dir = Path(__file__).parent / "test_output"
    test_dir.mkdir(exist_ok=True)
    test_file = test_dir / f"quick_test_{int(time.time())}.wav"
    
    try:
        # Start recording
        recorder.start_recording()
        assert recorder.is_recording, "Expected is_recording to be True after start"
        
        # Record for just 0.5 seconds (minimal test)
        time.sleep(0.5)
        
        # Stop recording
        recorder.stop_recording(str(test_file))
        assert not recorder.is_recording, "Expected is_recording to be False after stop"
        
        # Check file was created
        assert test_file.exists(), "Expected recording file to be created"
        
        # Check file has some content
        file_size = test_file.stat().st_size
        assert file_size > 0, "Expected recording file to have content"
        
        print(f"✓ Recording test passed - created {file_size} byte file")
        
    except Exception as e:
        print(f"✗ Recording test failed: {e}")
        raise

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
    
    print("Running AudioRecorder automated tests...")
    print("=" * 40)
    
    test_recorder_config()
    test_recorder_start_stop()
    
    print("=" * 40)
    print("🎉 All tests passed! AudioRecorder is working correctly.")