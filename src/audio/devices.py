import sounddevice as sd


def list_audio_devices() -> list:
    devices = sd.query_devices()
    return [
        {
            'index': i,
            'name': device['name'],
            'max_input_channels': device['max_input_channels'],
            'max_output_channels': device['max_output_channels'],
            'default_samplerate': device['default_samplerate']
        }
        for i, device in enumerate(devices)
    ]
