"""
TTS Service handles Text-to-Speech using ElevenLabs' high-quality voices.
"""
import os # Access environment variables for API keys
import logging # Log synthesis success and API errors
from elevenlabs.client import ElevenLabs # Official Python client for ElevenLabs
from elevenlabs import stream # Helper for streaming audio playback (if needed)

logger = logging.getLogger(__name__)

class TTSService:
    """
    Manages audio synthesis using ElevenLabs.
    Converts agent text responses into natural-sounding MP3 audio.
    """
    def __init__(self):
        """Initializes the ElevenLabs client with API key validation."""
        self.api_key = os.getenv("ELEVENLABS_API_KEY")
        if not self.api_key:
            logger.error("ELEVENLABS_API_KEY is not set")
            raise ValueError("ELEVENLABS_API_KEY is not set")
        self.client = ElevenLabs(api_key=self.api_key)

    
    def generate_audio(self, text):
        try:
            logger.info(f"Generating audio for text: {text[:20]}...")
            # Use text_to_speech.convert
            audio_generator = self.client.text_to_speech.convert(
                text=text,
                voice_id="21m00Tcm4TlvDq8ikWAM", # Rachel
                model_id="eleven_multilingual_v2",
                output_format="mp3_44100_128",
            )
            # Consume generator to get full bytes
            audio = b"".join(audio_generator)
            logger.info("Audio generation successful.")
            return audio
        except Exception as e:
            logger.error(f"ElevenLabs API error: {e}")
            return None        

    def generate_stream(self, text):
        try:
            # Using a default voice, e.g., "Rachel"
            audio_stream = self.client.text_to_speech.convert(
                text=text,
                voice_id="21m00Tcm4TlvDq8ikWAM", # Rachel
                model_id="eleven_multilingual_v2",
                output_format="mp3_44100_128",
                # stream=True is implied/default for convert? No, convert usually streams by default or returns generator.
            )
            return audio_stream
        except Exception as e:
            logger.error(f"ElevenLabs API error: {e}")
            return None
