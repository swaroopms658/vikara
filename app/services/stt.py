"""
STT Service handles real-time Speech-to-Text using Deepgram's Nova-2 model.
It manages an asynchronous WebSocket connection for live audio streaming.
"""
import os # Access environment variables for API keys
import logging # Log connection status and transcription errors
import asyncio # Non-blocking I/O for handling high-frequency audio chunks
import json # Parsing structured responses
from deepgram import AsyncDeepgramClient # Official SDK for Deepgram interaction

logger = logging.getLogger(__name__)

class STTService:
    """
    Manages the lifecycle of a Speech-to-Text session.
    Features: Connection management, real-time audio chunking, and callback execution.
    """
    def __init__(self):
        """Validates credentials and initializes the Deepgram client."""
        self.api_key = os.getenv("DEEPGRAM_API_KEY")
        if not self.api_key:
            logger.error("DEEPGRAM_API_KEY is not set")
            raise ValueError("DEEPGRAM_API_KEY is not set")
        self.dg_client = AsyncDeepgramClient(api_key=self.api_key.strip())
        self.connection = None
        self._socket_ctx = None
        self.listen_task = None

    async def connect(self, on_message_callback, on_error_callback, encoding="linear16", sample_rate="48000"):
        """
        Establishes a WebSocket connection with Deepgram.
        :param on_message_callback: Function to call when a transcript is received.
        :param on_error_callback: Function to call on STT failure.
        :param encoding: Audio encoding format (default: linear16 for raw PCM).
        :param sample_rate: Audio sample rate in Hz (default: 48000).
        """
        try:
            logger.info(f"Connecting to Deepgram with encoding={encoding}, sample_rate={sample_rate}")
            # Create a websocket connection to Deepgram using v1.connect context manager manually
            # We configure options via kwargs
            self._socket_ctx = self.dg_client.listen.v1.connect(
                model="nova-2",
                language="en-US",
                smart_format="true",
                interim_results="true",
                endpointing="300",
                encoding=encoding,
                sample_rate=sample_rate
            )
            
            # Enter the context manager manually to keep connection open
            self.connection = await self._socket_ctx.__aenter__()

            async def on_message(result):
                # result is likely a Pydantic model or dict
                try:
                    # Check if it's a Results event (it might be Metadata etc)
                    # The SDK parses it into models.
                    # We look for 'channel' and 'alternatives'
                    # Based on standard response: result.channel.alternatives[0].transcript
                    
                    # Note: SDK types might vary, safest is to check attributes
                    if hasattr(result, "channel") and result.channel:
                        alternatives = result.channel.alternatives
                        if alternatives and len(alternatives) > 0:
                            transcript = alternatives[0].transcript
                            is_final = getattr(result, "is_final", False)
                            speech_final = getattr(result, "speech_final", False)
                            # Always log the transcript state for debugging
                            logger.info(f"DG Result: text='{transcript}' is_final={is_final} speech_final={speech_final}")
                            if not transcript:
                                return
                            
                            # Treat either is_final or speech_final as actionable
                            # Treat either is_final or speech_final as actionable
                            effective_final = is_final or speech_final
                            if asyncio.iscoroutinefunction(on_message_callback):
                                await on_message_callback(transcript, effective_final)
                            else:
                                on_message_callback(transcript, effective_final)
                        else:
                             logger.warning("Deepgram result has no alternatives")
                    else:
                        # logger.debug("Deepgram message with no channel (metadata?)")
                        pass

                except Exception as e:
                    logger.error(f"Error processing message: {e}")

            async def on_error(error):
                logger.error(f"Deepgram error: {error}")
                if asyncio.iscoroutinefunction(on_error_callback):
                    await on_error_callback(error)
                else:
                    on_error_callback(error)

            # Register event handlers
            # "message" event contains the parsed response
            self.connection.on("message", on_message)
            self.connection.on("error", on_error)
            
            
            # Start the listening loop in background
            self.listen_task = asyncio.create_task(self.connection.start_listening())
            
            # Start KeepAlive task
            # self.keep_alive_task = asyncio.create_task(self._keep_alive())

            logger.info("Connected to Deepgram")
            return True

        except Exception as e:
            logger.error(f"Error connecting to Deepgram: {e}")
            return False

    async def _keep_alive(self):
        try:
            while True:
                await asyncio.sleep(5) # Send every 5 seconds
                if self.connection:
                    # Using send_media to bypass protected _send, and sending dict directly
                    # The SDK class _send method handles dict -> json.dumps
                    keep_alive_msg = {"type": "KeepAlive"}
                    # logger.debug("Sending KeepAlive")
                    await self.connection.send_media(keep_alive_msg)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"KeepAlive error: {e}")

    async def send_audio(self, chunk):
        """Streams a raw audio chunk to the active Deepgram connection."""
        if self.connection:
            # logger.debug(f"Sending {len(chunk)} bytes to Deepgram")
            await self.connection.send_media(chunk)
            # logger.debug("Sent audio to Deepgram")
        else:
            logger.error("Cannot send audio: Deepgram connection is None")

    async def finish(self):
        """Closes the Deepgram connection and cleans up background tasks."""
        # Cancel listening task
        if self.listen_task:
            self.listen_task.cancel()
            try:
                await self.listen_task
            except asyncio.CancelledError:
                pass
        
        # Cancel KeepAlive task
        if hasattr(self, 'keep_alive_task') and self.keep_alive_task:
            self.keep_alive_task.cancel()
            try:
                await self.keep_alive_task
            except asyncio.CancelledError:
                pass

        # Exit context manager to close socket
        if self._socket_ctx:
            await self._socket_ctx.__aexit__(None, None, None)
            self._socket_ctx = None
        self.connection = None
