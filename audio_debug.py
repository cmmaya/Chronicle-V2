#!/usr/bin/env python3
"""
Audio device debugging script to investigate sounddevice issues.
Tests various configurations to find working audio recording setup.
"""

import sounddevice as sd
import numpy as np
import sys

def print_section(title):
    """Print a section header"""
    print(f"\n{'='*50}")
    print(f" {title}")
    print(f"{'='*50}")

def test_device_query():
    """Test querying specific device 7 and general device info"""
    print_section("DEVICE INFORMATION")
    
    # First, list all devices
    print("All available audio devices:")
    try:
        devices = sd.query_devices()
        for i, device in enumerate(devices):
            print(f"Device {i}: {device['name']}")
            print(f"  - Max inputs: {device['max_input_channels']}")
            print(f"  - Max outputs: {device['max_output_channels']}")
            print(f"  - Default sample rate: {device['default_samplerate']}")
            print()
    except Exception as e:
        print(f"Error listing devices: {e}")
        return False
    
    # Test querying device 7 specifically
    print("\nQuerying device 7 specifically:")
    try:
        device_7 = sd.query_devices(7)
        print(f"Device 7 details:")
        for key, value in device_7.items():
            print(f"  {key}: {value}")
    except Exception as e:
        print(f"Error querying device 7: {e}")
    
    # Test querying device 0 (HDA Intel PCH)
    print("\nQuerying device 0 (HDA Intel PCH):")
    try:
        device_0 = sd.query_devices(0)
        print(f"Device 0 details:")
        for key, value in device_0.items():
            print(f"  {key}: {value}")
    except Exception as e:
        print(f"Error querying device 0: {e}")
    
    return True

def test_recording_configurations():
    """Test different recording configurations"""
    print_section("RECORDING TESTS")
    
    # Test configurations
    configs = [
        {"device": None, "channels": 1, "name": "Default device, mono"},
        {"device": None, "channels": 2, "name": "Default device, stereo"},
        {"device": 0, "channels": 1, "name": "Device 0, mono"},
        {"device": 0, "channels": 2, "name": "Device 0, stereo"},
        {"device": 7, "channels": 1, "name": "Device 7, mono"},
        {"device": 7, "channels": 2, "name": "Device 7, stereo"},
    ]
    
    duration = 1.0  # 1 second test recording
    sample_rate = 44100
    
    working_configs = []
    
    for config in configs:
        print(f"\nTesting: {config['name']}")
        print(f"  Device: {config['device']}, Channels: {config['channels']}")
        
        try:
            # Test if we can record with this configuration
            recording = sd.rec(
                int(duration * sample_rate),
                samplerate=sample_rate,
                channels=config['channels'],
                device=config['device'],
                dtype=np.float32
            )
            
            # Wait for recording to complete
            sd.wait()
            
            # Check if we got valid data
            if recording is not None and len(recording) > 0:
                max_amplitude = np.max(np.abs(recording))
                print(f"  ✓ SUCCESS - Recorded {len(recording)} samples")
                print(f"  ✓ Max amplitude: {max_amplitude:.6f}")
                working_configs.append(config)
            else:
                print(f"  ✗ FAILED - No data recorded")
                
        except Exception as e:
            print(f"  ✗ FAILED - Error: {e}")
    
    return working_configs

def test_default_devices():
    """Test with default input/output devices"""
    print_section("DEFAULT DEVICE TESTS")
    
    try:
        # Get default devices
        default_input = sd.default.device[0] if sd.default.device[0] is not None else "None"
        default_output = sd.default.device[1] if sd.default.device[1] is not None else "None"
        
        print(f"Default input device: {default_input}")
        print(f"Default output device: {default_output}")
        
        # Test with explicitly setting default input
        if default_input != "None":
            print(f"\nTesting with default input device {default_input}:")
            try:
                recording = sd.rec(
                    int(1.0 * 44100),
                    samplerate=44100,
                    channels=1,
                    device=(default_input, None),  # input only
                    dtype=np.float32
                )
                sd.wait()
                print(f"  ✓ SUCCESS - Recorded with default input device")
            except Exception as e:
                print(f"  ✗ FAILED - Error: {e}")
        
    except Exception as e:
        print(f"Error getting default devices: {e}")

def main():
    """Main testing function"""
    print("sounddevice Audio Device Investigation")
    print("=====================================")
    
    # Test device queries
    if not test_device_query():
        print("Critical error: Cannot query audio devices")
        return
    
    # Test default devices
    test_default_devices()
    
    # Test various recording configurations
    working_configs = test_recording_configurations()
    
    # Summary
    print_section("SUMMARY")
    if working_configs:
        print("Working configurations found:")
        for config in working_configs:
            print(f"  ✓ {config['name']}")
        
        print(f"\nRecommendation: Use the first working configuration:")
        best_config = working_configs[0]
        print(f"  Device: {best_config['device']}")
        print(f"  Channels: {best_config['channels']}")
        
    else:
        print("❌ No working configurations found!")
        print("This suggests a deeper audio system issue.")
    
    print("\nInvestigation complete.")

if __name__ == "__main__":
    main()