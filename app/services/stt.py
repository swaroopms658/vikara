"""
STT Service using Groq's Whisper API for Speech-to-Text.
Buffers PCM audio chunks and transcribes them using Groq's whisper-large-v3 model.
"""
import os
import logging
import io
import wave
import struct
import asyncio
from groq import Groq

logger = logging.getLogger(__name__)


class STTService:
    """
    Manages speech-to-text using Groq's Whisper API.
    Buffers raw PCM audio chunks and sends them for batch transcription
    when enough audio has been accumulated or silence is detected.
    """

    def __init__(self, sample_rate=48000):
        """Initialize the Groq client for Whisper transcription."""
        self.api_key = os.getenv("GROQ_API_KEY")
        if not self.api_key:
            logger.error("GROQ_API_KEY is not set")
            raise ValueError("GROQ_API_KEY is not set")
        self.client = Groq(api_key=self.api_key.strip())
        self.sample_rate = sample_rate
        self.audio_buffer = bytearray()
        self.silence_chunks = 0
        self.speech_detected = False
        # Minimum audio length to transcribe (in bytes): ~1 second of audio
        self.min_audio_bytes = sample_rate * 2  # 16-bit = 2 bytes per sample
        # Silence threshold: RMS below this is considered silence
        self.silence_threshold = 150
        # Number of consecutive silent chunks to trigger end-of-speech
        self.silence_trigger = 12  # ~12 chunks * 85ms = ~1 second of silence
        self._lock = asyncio.Lock()

    def _compute_rms(self, pcm_bytes):
        """Compute RMS amplitude of int16 PCM audio."""
        if len(pcm_bytes) < 2:
            return 0
        n_samples = len(pcm_bytes) // 2
        samples = struct.unpack(f'<{n_samples}h', pcm_bytes[:n_samples * 2])
        if not samples:
            return 0
        rms = (sum(s * s for s in samples) / len(samples)) ** 0.5
        return rms

    def _pcm_to_wav(self, pcm_bytes):
        """Convert raw PCM bytes to a WAV file in memory."""
        buf = io.BytesIO()
        with wave.open(buf, 'wb') as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)  # 16-bit
            wf.setframerate(self.sample_rate)
            wf.writeframes(pcm_bytes)
        buf.seek(0)
        buf.name = "audio.wav"  # Groq SDK needs a filename
        return buf

    async def add_audio(self, chunk):
        """
        Add an audio chunk to the buffer.
        Returns transcription text if end-of-speech is detected, else None.
        """
        rms = self._compute_rms(chunk)

        async with self._lock:
            if rms > self.silence_threshold:
                # Speech detected
                self.speech_detected = True
                self.silence_chunks = 0
                self.audio_buffer.extend(chunk)
            else:
                if self.speech_detected:
                    # Still accumulate a bit of trailing silence
                    self.audio_buffer.extend(chunk)
                    self.silence_chunks += 1

                    if self.silence_chunks >= self.silence_trigger:
                        # End of speech detected - transcribe
                        if len(self.audio_buffer) >= self.min_audio_bytes:
                            pcm_data = bytes(self.audio_buffer)
                            self._reset_buffer()
                            return await self._transcribe(pcm_data)
                        else:
                            self._reset_buffer()
                            return None
                # If no speech detected yet, don't buffer silence

        return None

    def _reset_buffer(self):
        """Reset the audio buffer and speech detection state."""
        self.audio_buffer = bytearray()
        self.silence_chunks = 0
        self.speech_detected = False

    async def _transcribe(self, pcm_bytes):
        """Send buffered audio to Groq Whisper for transcription."""
        try:
            wav_file = self._pcm_to_wav(pcm_bytes)
            duration_sec = len(pcm_bytes) / (self.sample_rate * 2)
            logger.info(f"Sending {duration_sec:.1f}s audio to Groq Whisper for transcription...")

            # Run the blocking Groq API call in a thread
            transcription = await asyncio.to_thread(
                self.client.audio.transcriptions.create,
                file=wav_file,
                model="whisper-large-v3",
                language="en",
                response_format="text"
            )

            text = transcription.strip() if isinstance(transcription, str) else str(transcription).strip()
            logger.info(f"Groq Whisper transcript: '{text}'")
            return text if text else None

        except Exception as e:
            logger.error(f"Groq Whisper transcription error: {e}")
            return None

    async def force_transcribe(self):
        """Force transcription of any remaining buffered audio."""
        async with self._lock:
            if len(self.audio_buffer) >= self.min_audio_bytes:
                pcm_data = bytes(self.audio_buffer)
                self._reset_buffer()
                return await self._transcribe(pcm_data)
            self._reset_buffer()
            return None

    async def finish(self):
        """Clean up resources."""
        self._reset_buffer()
