"""Parakeet V3 transcription engine integration."""
import logging
import wave
import numpy as np
from pathlib import Path
from typing import Optional, List, Dict, Any
import struct

logger = logging.getLogger(__name__)


class ParakeetError(Exception):
    """Base exception for Parakeet transcription errors."""
    pass


class ModelLoadError(ParakeetError):
    """Failed to load Parakeet model."""
    pass


class TranscriptionError(ParakeetError):
    """Failed to transcribe audio."""
    pass


class ParakeetV3:
    """Wrapper for Parakeet V3 speech-to-text model.
    
    Supports offline transcription of audio files using the Parakeet V3 model.
    Handles audio preprocessing and model inference.
    """
    
    def __init__(self, model_path: Optional[str] = None, scorer_path: Optional[str] = None):
        """Initialize Parakeet V3 model.
        
        Args:
            model_path: Path to Parakeet model file (.tflite). 
                        If None, attempts to use default model.
            scorer_path: Path to language model scorer (optional).
        """
        self.model_path = model_path
        self.scorer_path = scorer_path
        self._model = None
        self._scorer = None
        self._loaded = False
        self._model_type: Optional[str] = None
        self._model_name: Optional[str] = None
    
    def load(self) -> None:
        """Load the Parakeet model and scorer.
        
        Raises:
            ModelLoadError: If model loading fails
        """
        if self._loaded:
            return
            
        # Primary: try Whisper first as preferred backend
        try:
            import whisper
            model_name = self.model_path if self.model_path else "small"
            self._model = whisper.load_model(model_name)
            self._model_type = 'whisper'
            self._model_name = model_name
            logger.info(f"Loaded Whisper model: {model_name}")
            self._loaded = True
            return
        except ImportError:
            pass
        except Exception as e:
            # Whisper present but failed to load
            raise ModelLoadError(f"Failed to load Whisper model: {str(e)}")

        # Secondary: try parakeet-ctc
        try:
            from parakeet import load_model
            self._model = load_model("parakeet_v3")
            self._model_type = 'parakeet'
            self._model_name = 'parakeet_v3'
            logger.info("Loaded Parakeet V3 model via parakeet-ctc")
            self._loaded = True
            return
        except ImportError:
            pass
        except Exception as e:
            raise ModelLoadError(f"Failed to load Parakeet model: {str(e)}")

        # Tertiary: try coqui-stt
        try:
            import coqui_stt
            if self.model_path:
                self._model = coqui_stt.Model(self.model_path)
                if self.scorer_path:
                    self._model.enableExternalScorer(self.scorer_path)
                self._model_type = 'coqui'
                self._model_name = self.model_path
                logger.info(f"Loaded Coqui STT model from {self.model_path}")
                self._loaded = True
                return
        except ImportError:
            pass
        except Exception as e:
            raise ModelLoadError(f"Failed to load Coqui-STT model: {str(e)}")

        # If we reach here, no supported backend loaded
        raise ModelLoadError(
            "Failed to load any speech-to-text model. "
            "Please install a supported STT package (openai-whisper, parakeet-ctc, or coqui-stt) "
            "and any required model files or dependencies (e.g. PyTorch)."
        )
    
    def is_loaded(self) -> bool:
        """Check if model is loaded."""
        return self._loaded
    
    def _load_audio_file(self, audio_path: str) -> np.ndarray:
        """Load and preprocess audio file to numpy array.
        
        Args:
            audio_path: Path to WAV audio file
            
        Returns:
            Audio data as float32 numpy array normalized to [-1, 1]
            
        Raises:
            TranscriptionError: If audio file cannot be loaded
        """
        try:
            with wave.open(str(audio_path), 'rb') as wf:
                # Verify audio format
                if wf.getnchannels() != 1 and wf.getnchannels() != 2:
                    raise TranscriptionError(f"Unsupported channel count: {wf.getnchannels()}")
                
                frames = wf.readframes(wf.getnframes())
                sample_width = wf.getsampwidth()
                sample_rate = wf.getframerate()
                
                # Convert to float32 array
                if sample_width == 2:
                    audio = np.frombuffer(frames, dtype=np.int16)
                    audio = audio.astype(np.float32) / 32768.0
                elif sample_width == 4:
                    audio = np.frombuffer(frames, dtype=np.int32)
                    audio = audio.astype(np.float32) / 2147483648.0
                else:
                    raise TranscriptionError(f"Unsupported sample width: {sample_width}")
                
                # Convert stereo to mono if needed
                if wf.getnchannels() == 2:
                    audio = audio.reshape(-1, 2).mean(axis=1)
                
                # Resample to 16000 Hz if needed (Parakeet expects 16kHz)
                if sample_rate != 16000:
                    audio = self._resample(audio, sample_rate, 16000)
                
                # Ensure float32 dtype for downstream models (Whisper expects float32)
                return audio.astype(np.float32)
                
        except wave.Error as e:
            raise TranscriptionError(f"Failed to read audio file: {str(e)}")
        except Exception as e:
            raise TranscriptionError(f"Audio preprocessing failed: {str(e)}")
    
    def _resample(self, audio: np.ndarray, orig_rate: int, target_rate: int) -> np.ndarray:
        """Simple audio resampling using linear interpolation.
        
        Args:
            audio: Input audio array
            orig_rate: Original sample rate
            target_rate: Target sample rate
            
        Returns:
            Resampled audio array
        """
        if orig_rate == target_rate:
            return audio.astype(np.float32)
            
        # Calculate new length
        new_length = int(len(audio) * target_rate / orig_rate)
        
        # Linear interpolation
        x_orig = np.linspace(0, 1, len(audio))
        x_new = np.linspace(0, 1, new_length)
        
        # np.interp returns float64 by default; cast to float32 for model compatibility
        return np.interp(x_new, x_orig, audio).astype(np.float32)
    
    def transcribe(self, audio_path: str) -> str:
        """Transcribe audio file to text.
        
        Args:
            audio_path: Path to WAV audio file
            
        Returns:
            Transcribed text string
            
        Raises:
            TranscriptionError: If transcription fails
            ModelLoadError: If model is not loaded
        """
        if not self._loaded:
            self.load()
        
        # If we reach here, model must be loaded (load() would have raised otherwise)
        if self._model is None:
            raise ModelLoadError("Model failed to load")
        
        try:
            # Load and preprocess audio
            # If using Whisper, let it handle audio file directly
            if getattr(self, '_model_type', None) == 'whisper':
                # Load audio into numpy array at 16kHz and pass directly to Whisper to avoid ffmpeg dependency
                audio = self._load_audio_file(audio_path)
                result = self._model.transcribe(audio)
                return result.get('text', '')

            # Otherwise, load and preprocess audio for parakeet/coqui
            audio = self._load_audio_file(audio_path)

            # Run inference
            try:
                # Try parakeet-ctc API
                text = self._model(audio)
                return text
            except (TypeError, AttributeError):
                # Fallback to coqui-stt API
                text = self._model.SpeechToText(audio.tobytes())
                return text
                
        except TranscriptionError:
            raise
        except Exception as e:
            raise TranscriptionError(f"Transcription failed: {str(e)}")
    
    def transcribe_stream(self, audio_chunk: np.ndarray) -> str:
        """Transcribe an audio chunk (for future real-time support).
        
        Note: Real-time transcription is out of scope for BU005,
        but this method is provided for API consistency.
        
        Args:
            audio_chunk: Audio data as float32 numpy array
            
        Returns:
            Transcribed text string
        """
        if not self._loaded:
            self.load()
        
        # Model must be loaded at this point
        if self._model is None:
            raise ModelLoadError("Model failed to load")
        
        # Whisper: accept numpy audio directly to avoid ffmpeg dependency
        if getattr(self, '_model_type', None) == 'whisper':
            try:
                # Ensure mono
                if len(audio_chunk.shape) > 1:
                    audio_chunk = audio_chunk.mean(axis=1)

                # Ensure float32
                audio_chunk = audio_chunk.astype(np.float32)

                result = self._model.transcribe(audio_chunk)
                return result.get('text', '')
            except Exception as e:
                raise TranscriptionError(f"Stream transcription (Whisper) failed: {str(e)}")

        # Ensure correct format
        if len(audio_chunk.shape) > 1:
            audio_chunk = audio_chunk.mean(axis=1)
        
        try:
            text = self._model(audio_chunk)
            return text
        except Exception as e:
            logger.warning(f"Stream transcription failed: {str(e)}")
            return ""
    
    def get_model_info(self) -> Dict[str, Any]:
        """Get information about the loaded model.
        
        Returns:
            Dictionary with model metadata
        """
        info = {
            "loaded": self._loaded,
            "model_path": self.model_path,
            "scorer_path": self.scorer_path,
            "model_type": self._model_type if self._model_type else "none"
        }
        
        if self._model is not None:
            try:
                info["sample_rate"] = getattr(self._model, 'sample_rate', 16000)
            except Exception:
                info["sample_rate"] = 16000
        else:
            info["sample_rate"] = 16000
            
        return info
    
    def unload(self) -> None:
        """Unload the model to free memory."""
        self._model = None
        self._scorer = None
        self._loaded = False
        logger.info("Parakeet model unloaded")
