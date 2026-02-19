"""
Main entry point for the Vikara Voice Agent.
Handles WebSocket connections, integrates STT, LLM, TTS, and Calendar services.
"""
import os # Interface with the operating system (env vars)
import json # Handle structured data exchange with frontend
import logging # Log server activity and errors for debugging
import asyncio # Non-blocking task management for real-time performance
from fastapi import FastAPI, WebSocket, WebSocketDisconnect # Async web framework for APIs and WebSockets
from fastapi.middleware.cors import CORSMiddleware # Support Cross-Origin requests from different domains
from fastapi.staticfiles import StaticFiles # Serve HTML/JS/CSS assets
from fastapi.responses import RedirectResponse, FileResponse # Helper for URL redirection and file serving
from pathlib import Path # Robust path handling
from dotenv import load_dotenv # Secret management from .env files

# Internal service integrations
from app.services.stt import STTService 
from app.services.llm import LLMService 
from app.services.tts import TTSService 
from app.services.calendar_service import CalendarService 

# Initialize environment configuration
load_dotenv()

# --- CRITICAL: Sanitize API keys ---
# Environment variables (especially from Render dashboard or .env files)
# can contain trailing newlines/whitespace that corrupt HTTP headers.
# We must clean them BEFORE any SDK reads them.
for key_name in ["GROQ_API_KEY", "DEEPGRAM_API_KEY", "ELEVENLABS_API_KEY"]:
    val = os.environ.get(key_name)
    if val:
        os.environ[key_name] = val.strip()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Robust Static File Mounting
# We find the absolute path to the 'static' directory relative to this file
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC_DIR = os.path.join(BASE_DIR, "static")

# Mount static files for assets (css, js, etc.)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

@app.get("/")
async def root():
    """Serve the main frontend page directly at the root."""
    index_path = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {
        "status": "online",
        "message": "Vikara AI Backend is Running!",
        "note": "index.html not found in static folder"
    }

@app.get("/debug")
def debug_info():
    """Diagnostic endpoint to verify file structure on Render."""
    return {
        "cwd": os.getcwd(),
        "file": __file__,
        "base_dir": BASE_DIR,
        "static_dir": STATIC_DIR,
        "static_exists": os.path.exists(STATIC_DIR),
        "index_exists": os.path.exists(os.path.join(STATIC_DIR, "index.html")),
        "ls_static": os.listdir(STATIC_DIR) if os.path.exists(STATIC_DIR) else []
    }

# Initialize services
# Note: Instantiate inside the websocket endpoint or globally depending on thread safety
# STT needs a new instance per connection usually if it holds socket state
llm_service = LLMService()
tts_service = TTSService()
calendar_service = CalendarService()

@app.websocket("/ws/audio")
async def websocket_endpoint(websocket: WebSocket):
    """
    Core WebSocket handler. Orchestrates data flow between:
    Client Audio -> STT -> LLM -> TTS -> Client Audio/Json.
    """
    await websocket.accept()
    logger.info("WebSocket connected")

    # Read audio encoding params from query string
    sample_rate = websocket.query_params.get("sample_rate", "48000")
    logger.info(f"Client sample rate: {sample_rate}")

    stt_service = STTService()
    
    # Conversation state
    messages = [
        {"role": "system", "content":    "- Ask for the attendee's name if not provided.\n    - Ask for the desired meeting time if not provided.\n    - Ask for the meeting summary/title if not provided.\n    - Once you have the name, time, and title, ask for confirmation. If confirmed, say 'creating event'. Output strictly text that should be spoken."}
    ]
    
    # Queues for processing
    transcript_queue = asyncio.Queue()

    async def on_transcript(transcript, is_final):
        """Callback for STT: Sends final transcripts to the conversation queue and the UI."""
        # We only care about final transcripts for the LLM to avoid interrupting too often
        if is_final:
            logger.info(f"Final Transcript (Queueing): {transcript}")
            transcript_queue.put_nowait(transcript)
            # Send to UI
            try:
                await websocket.send_json({"type": "transcript", "text": transcript})
            except:
                pass

    def on_error(error):
        """Callback for STT errors."""
        logger.error(f"STT Error: {error}")

    try:
        # Connect to Deepgram with linear16 encoding and client's sample rate
        if not await stt_service.connect(on_transcript, on_error, encoding="linear16", sample_rate=sample_rate):
            await websocket.close(code=1011)
            return

        # Start a task to receive audio from client and send to STT
        async def receive_audio():
            """Continuously receives raw audio chunks from the client and feeds them to STT."""
            try:
                while True:
                    data = await websocket.receive_bytes()
                    logger.info(f"Received audio chunk: {len(data)} bytes, Header: {data[:10].hex()}")
                    await stt_service.send_audio(data)
            except WebSocketDisconnect:
                logger.info("Client disconnected from receive_audio")
            except Exception as e:
                logger.error(f"Error receiving audio: {e}")

        receive_task = asyncio.create_task(receive_audio())

        # Task to process transcripts and generate responses
        async def process_conversation():
            """
            Main brain of the agent: 
            Waits for transcripts -> Gets LLM response -> Generates/Sends TTS -> Handles Tool use (Calendar).
            """
            logger.info("Starting conversation processing loop")
            while True:
                user_text = await transcript_queue.get()
                logger.info(f"Processing transcript: {user_text}")
                if not user_text.strip():
                    continue

                messages.append({"role": "user", "content": user_text})
                
                # Check for specific intent (e.g., "creating event") in previous turn or decide logic here
                # For simplicity, we just pass to LLM
                logger.info("Calling LLM...")
                # Run blocking LLM call in a thread
                response_text = await asyncio.to_thread(llm_service.get_response, messages, system_prompt=messages[0]['content'])
                messages.append({"role": "assistant", "content": response_text})
                
                logger.info(f"LLM Response: {response_text}")
                
                # Send text response to UI
                try:
                    await websocket.send_json({"type": "response", "text": response_text})
                except Exception as e:
                    logger.error(f"Error sending response JSON: {e}")
                
                # Generate audio (non-streaming for now to ensure stability)
                logger.info(f"Generating audio for text: {response_text[:20]}...")
                audio_bytes = await asyncio.to_thread(tts_service.generate_audio, response_text)
                
                # Send audio back to client
                if audio_bytes:
                     # Helper to chunk the bytes if needed, but sending whole blob is fine for small responses
                     # Or chunk it manually to simulate stream? No need.
                     # WebSocket frame size limits might apply, but usually fine for <1MB
                     # But for better UX, maybe send in 32k chunks?
                     # Let's just send it.
                     logger.info(f"Sending audio response: {len(audio_bytes)} bytes")
                     await websocket.send_bytes(audio_bytes)
                else:
                    logger.error("No audio bytes generated to send.")
                
                # Simple keyword detection for calendar creation
                if "creating event" in response_text.lower():
                    logger.info("Agent indicates event creation. Extracting details...")
                    
                    # Extract details using LLM
                    extraction_json = await asyncio.to_thread(llm_service.extract_details, messages)
                    if extraction_json:
                        try:
                            import json
                            details = json.loads(extraction_json)
                            logger.info(f"Extracted details: {details}")
                            
                            # Handle potential nested 'meeting' key from some models
                            if "meeting" in details and isinstance(details["meeting"], dict):
                                details = details["meeting"]
                            
                            summary = details.get("summary")
                            if not summary:
                                summary = details.get("title", "Meeting")
                                
                            start_time = details.get("start_time")
                            duration = details.get("duration_minutes", 30)
                            # Hardcoded email for confirmation as per user request
                            attendee_email = "swaroopms658@gmail.com"
                            
                            if start_time:
                                event = await asyncio.to_thread(calendar_service.create_event, summary, start_time, duration, attendee_email)
                                if event:
                                    success_msg = "I have successfully scheduled the meeting."
                                    # Send success audio
                                    success_audio = await asyncio.to_thread(tts_service.generate_audio, success_msg)
                                    if success_audio:
                                        await websocket.send_bytes(success_audio)
                                else:
                                    # Fallback if calendar fails
                                    logger.error("Failed to create calendar event.")
                        except Exception as e:
                            logger.error(f"Error parsing extraction: {e}")
                    
        process_task = asyncio.create_task(process_conversation())

        await asyncio.gather(receive_task, process_task)

    except WebSocketDisconnect:
        logger.info("WebSocket disconnected")
    except Exception as e:
        logger.error(f"An error occurred: {e}")
    finally:
        await stt_service.finish()
