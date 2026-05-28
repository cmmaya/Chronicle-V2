# Setup Guide: Installing Whisper and PyTorch

This project prefers OpenAI Whisper as the primary speech-to-text backend. Whisper depends on PyTorch which must be installed separately with a wheel compatible with your OS and CUDA configuration.

Follow these steps to set up a development environment that can run the transcription pipeline using Whisper.

1) Choose Python version
   - Recommended: Python 3.10 or 3.11 for widest wheel availability.

2) Install PyTorch
   - Visit https://pytorch.org/get-started/locally and select the options matching your OS and CUDA support.
   - Example CPU-only install (works on Linux, Windows, macOS):
       pip install torch --index-url https://download.pytorch.org/whl/cpu
   - Example CUDA install (replace cu117 with your CUDA version):
       pip install torch --index-url https://download.pytorch.org/whl/cu117

3) Install Whisper
   - After PyTorch is installed, install Whisper:
       pip install openai-whisper

4) Install project dependencies
   - Finally, install the rest of the requirements:
       pip install -r requirements.txt

5) Verify Whisper loads
   - Run a small test in Python:
       import whisper
       model = whisper.load_model('small')
       print(model)

Notes
 - If you cannot install PyTorch (e.g., incompatible OS or Python version), you can configure the project to use alternative backends if available:
   - parakeet-ctc (if wheels are available for your environment)
   - coqui-stt (may require model files and platform-specific builds)
 - The requirements.txt file includes openai-whisper. Installing requirements without first installing a compatible torch wheel may fail.
