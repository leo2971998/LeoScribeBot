"""
Optimized Whisper-based speech recognition for LeoScribeBot
Performance-focused implementation for real-time transcription
"""

import asyncio
import io
import logging
import time
import wave
from pathlib import Path
from typing import Optional, Union

logger = logging.getLogger(__name__)

# Global whisper model instance for efficiency
_whisper_model = None

# Fallback flag for environments where whisper isn't available
WHISPER_AVAILABLE = False

# Try importing whisper with graceful fallback
try:
    import whisper
    WHISPER_AVAILABLE = True
    logger.info("Whisper available for optimized speech recognition")
except ImportError:
    logger.warning("Whisper not available - falling back to Google Speech Recognition")
    whisper = None

# Also try SpeechRecognition as backup
try:
    import speech_recognition as sr
    SPEECH_RECOGNITION_AVAILABLE = True
except ImportError:
    logger.error("SpeechRecognition not available - no fallback possible")
    sr = None
    SPEECH_RECOGNITION_AVAILABLE = False


class WhisperTranscriber:
    """
    Optimized Whisper transcriber for real-time performance
    """
    
    def __init__(self, model_size: str = "base"):
        """
        Initialize Whisper transcriber with specified model size
        
        Args:
            model_size: Model size for whisper ("tiny", "base", "small", "medium", "large")
                       "tiny" and "base" are recommended for real-time use
        """
        self.model_size = model_size
        self.model = None
        self.is_loaded = False
        self.fallback_recognizer = None
        
        # Performance tracking
        self.transcription_count = 0
        self.total_time = 0.0
        
        # Initialize fallback recognizer
        if SPEECH_RECOGNITION_AVAILABLE:
            self.fallback_recognizer = sr.Recognizer()
            logger.info("Initialized fallback Google Speech Recognition")
    
    async def load_model(self) -> bool:
        """
        Load Whisper model asynchronously to avoid blocking startup
        """
        if not WHISPER_AVAILABLE:
            logger.warning("Whisper not available, using fallback recognition")
            return False
            
        try:
            start_time = time.time()
            logger.info(f"Loading Whisper {self.model_size} model...")
            
            # Load model in thread pool to avoid blocking
            loop = asyncio.get_event_loop()
            self.model = await loop.run_in_executor(None, whisper.load_model, self.model_size)
            
            load_time = time.time() - start_time
            logger.info(f"Whisper {self.model_size} model loaded in {load_time:.2f}s")
            
            self.is_loaded = True
            return True
            
        except Exception as e:
            logger.error(f"Failed to load Whisper model: {e}")
            return False
    
    def _prepare_audio_data(self, pcm_bytes: bytes, sample_rate: int = 48000) -> bytes:
        """
        Convert raw PCM data to WAV format that whisper can process
        """
        audio_io = io.BytesIO()
        with wave.open(audio_io, "wb") as wav_file:
            wav_file.setnchannels(2)          # Discord stereo
            wav_file.setsampwidth(2)          # 16-bit
            wav_file.setframerate(sample_rate) # Discord sample rate
            wav_file.writeframes(pcm_bytes)
        audio_io.seek(0)
        return audio_io.getvalue()
    
    async def _transcribe_with_whisper(self, audio_data: bytes) -> Optional[str]:
        """
        Transcribe audio using Whisper model
        """
        if not self.model or not WHISPER_AVAILABLE:
            return None
            
        try:
            start_time = time.time()
            
            # Save audio data to temporary file for whisper
            import tempfile
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_file:
                temp_file.write(audio_data)
                temp_path = temp_file.name
            
            try:
                # Transcribe with optimized settings for real-time
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(
                    None, 
                    lambda: self.model.transcribe(
                        temp_path,
                        # Optimizations for real-time performance
                        language="en",              # Skip language detection
                        task="transcribe",          # Don't translate
                        temperature=0.0,            # Deterministic output
                        best_of=1,                  # Single pass
                        beam_size=1,                # Fastest beam search
                        patience=1.0,               # Less patience for speed
                        length_penalty=1.0,         # Standard penalty
                        suppress_tokens="",         # Don't suppress anything
                        initial_prompt="",          # No initial prompt
                        condition_on_previous_text=False,  # Don't use context for speed
                        fp16=True,                  # Use half precision for speed
                        compression_ratio_threshold=2.4,
                        logprob_threshold=-1.0,
                        no_speech_threshold=0.6,
                    )
                )
                
                transcription_time = time.time() - start_time
                
                # Update performance metrics
                self.transcription_count += 1
                self.total_time += transcription_time
                avg_time = self.total_time / self.transcription_count
                
                if transcription_time > 0.1:  # Log slow transcriptions
                    logger.debug(f"Whisper transcription took {transcription_time:.3f}s (avg: {avg_time:.3f}s)")
                
                text = result.get("text", "").strip()
                return text if text else None
                
            finally:
                # Clean up temporary file
                try:
                    Path(temp_path).unlink()
                except Exception:
                    pass
                    
        except Exception as e:
            logger.error(f"Whisper transcription error: {e}")
            return None
    
    async def _transcribe_with_fallback(self, audio_data: bytes) -> Optional[str]:
        """
        Fallback transcription using Google Speech Recognition
        """
        if not self.fallback_recognizer or not SPEECH_RECOGNITION_AVAILABLE:
            return None
            
        try:
            # Use SpeechRecognition with the WAV data
            audio_io = io.BytesIO(audio_data)
            with sr.AudioFile(audio_io) as source:
                audio = self.fallback_recognizer.record(source)
            
            # Run recognition in thread pool
            loop = asyncio.get_event_loop()
            text = await loop.run_in_executor(
                None,
                self.fallback_recognizer.recognize_google,
                audio
            )
            
            return text.strip() if text else None
            
        except sr.UnknownValueError:
            logger.debug("Fallback recognition: Could not understand audio")
            return None
        except sr.RequestError as e:
            logger.error(f"Fallback recognition error: {e}")
            return None
        except Exception as e:
            logger.error(f"Fallback transcription error: {e}")
            return None
    
    async def transcribe_audio(self, pcm_bytes: bytes) -> Optional[str]:
        """
        Transcribe audio data with automatic fallback
        
        Args:
            pcm_bytes: Raw PCM audio data from Discord
            
        Returns:
            Transcribed text or None if transcription failed
        """
        if not pcm_bytes:
            return None
            
        # Prepare audio data
        audio_data = self._prepare_audio_data(pcm_bytes)
        
        # Try Whisper first (if available and loaded)
        if self.is_loaded and WHISPER_AVAILABLE:
            text = await self._transcribe_with_whisper(audio_data)
            if text:
                return text
            logger.debug("Whisper transcription failed, trying fallback")
        
        # Fallback to Google Speech Recognition
        return await self._transcribe_with_fallback(audio_data)
    
    def get_performance_stats(self) -> dict:
        """Get performance statistics"""
        if self.transcription_count > 0:
            avg_time = self.total_time / self.transcription_count
        else:
            avg_time = 0.0
            
        return {
            "model_size": self.model_size,
            "whisper_available": WHISPER_AVAILABLE,
            "model_loaded": self.is_loaded,
            "transcription_count": self.transcription_count,
            "total_time": self.total_time,
            "average_time": avg_time,
            "fallback_available": SPEECH_RECOGNITION_AVAILABLE
        }


# Global transcriber instance
_transcriber_instance: Optional[WhisperTranscriber] = None

async def get_transcriber(model_size: str = "base") -> WhisperTranscriber:
    """
    Get the global transcriber instance
    
    Args:
        model_size: Whisper model size ("tiny", "base", "small", "medium", "large")
                   "tiny" = Fastest, least accurate
                   "base" = Good balance of speed and accuracy (recommended)
                   "small" = Better accuracy, slower
    """
    global _transcriber_instance
    
    if _transcriber_instance is None or _transcriber_instance.model_size != model_size:
        _transcriber_instance = WhisperTranscriber(model_size)
        await _transcriber_instance.load_model()
    
    return _transcriber_instance

async def transcribe_audio(pcm_bytes: bytes, model_size: str = "base") -> Optional[str]:
    """
    Convenience function for transcribing audio
    
    Args:
        pcm_bytes: Raw PCM audio data from Discord
        model_size: Whisper model size for optimal performance
        
    Returns:
        Transcribed text or None if failed
    """
    transcriber = await get_transcriber(model_size)
    return await transcriber.transcribe_audio(pcm_bytes)

# Performance testing function
async def benchmark_transcription(model_size: str = "base") -> dict:
    """
    Benchmark the transcription performance
    """
    transcriber = await get_transcriber(model_size)
    stats = transcriber.get_performance_stats()
    
    print("Whisper Transcription Performance:")
    print("=" * 50)
    print(f"Model size: {stats['model_size']}")
    print(f"Whisper available: {stats['whisper_available']}")
    print(f"Model loaded: {stats['model_loaded']}")
    print(f"Fallback available: {stats['fallback_available']}")
    print(f"Transcriptions: {stats['transcription_count']}")
    
    if stats['transcription_count'] > 0:
        print(f"Average time: {stats['average_time']:.3f}s")
        print(f"Total time: {stats['total_time']:.3f}s")
    
    return stats

if __name__ == "__main__":
    # Quick test
    async def test():
        print("Testing Whisper integration...")
        print(f"Whisper available: {WHISPER_AVAILABLE}")
        print(f"SpeechRecognition fallback: {SPEECH_RECOGNITION_AVAILABLE}")
        
        # Test initialization
        transcriber = await get_transcriber("base")
        stats = transcriber.get_performance_stats()
        
        print("\nInitialization complete:")
        for key, value in stats.items():
            print(f"  {key}: {value}")
    
    asyncio.run(test())