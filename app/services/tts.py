"""
TTS Service - Browser-based Text-to-Speech.
Since we're using the browser's built-in speechSynthesis API,
TTS is handled client-side. This module provides a placeholder
for any server-side TTS needs (e.g., future upgrade to a cloud TTS).
"""
import logging

logger = logging.getLogger(__name__)


class TTSService:
    """
    TTS is handled client-side via the browser's speechSynthesis API.
    This class is kept as a placeholder for the service interface.
    """

    def __init__(self):
        """No API key needed - TTS runs in the browser."""
        logger.info("TTS Service initialized (browser-based speechSynthesis)")

    def generate_audio(self, text):
        """
        Browser-based TTS doesn't generate audio server-side.
        Returns None - the text is sent to the browser for synthesis.
        """
        return None
