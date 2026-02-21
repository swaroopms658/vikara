"""
Main entry point for the Vikara Voice Agent.
Handles WebSocket connections, integrates STT (Groq Whisper), LLM (Groq), 
Browser TTS (speechSynthesis), and Google Calendar services.
"""
import os
import json
import logging
import asyncio
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse, FileResponse
from pathlib import Path
from dotenv import load_dotenv

# Internal service integrations
from app.services.stt import STTService
from app.services.llm import LLMService
from app.services.tts import TTSService
from app.services.calendar_service import CalendarService

# Initialize environment configuration
load_dotenv()

# --- Sanitize API keys ---
for key_name in ["GROQ_API_KEY"]:
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
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC_DIR = os.path.join(BASE_DIR, "static")

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

# Initialize global services (stateless ones)
llm_service = LLMService()
tts_service = TTSService()
calendar_service = CalendarService()

SYSTEM_PROMPT = """You are Vikara, a concise voice assistant that helps schedule meetings.

RULES:
- NEVER assume or make up information the user hasn't explicitly said.
- Ask for ONE piece of information at a time, in this order:
  1. Attendee name
  2. Meeting time
  3. Meeting title/summary
- Only say 'creating event' AFTER the user explicitly confirms all three details.
- Keep responses to 1-2 short sentences maximum. You are a voice agent - be very concise.
- Do NOT repeat back what the user said. Just acknowledge and ask for the next piece of info.
- If you can't understand the user, ask them to repeat."""

@app.websocket("/ws/audio")
async def websocket_endpoint(websocket: WebSocket):
    """
    Core WebSocket handler. Orchestrates:
    Client Audio (PCM) -> Groq Whisper (STT) -> Groq LLM -> Text (Browser TTS)
    """
    await websocket.accept()
    logger.info("WebSocket connected")

    # Read audio sample rate from query string
    sample_rate = int(websocket.query_params.get("sample_rate", "48000"))
    logger.info(f"Client sample rate: {sample_rate}")

    # Create a new STT service per connection (holds buffer state)
    stt_service = STTService(sample_rate=sample_rate)

    # Conversation state
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT}
    ]

    # Queue for transcripts to process
    transcript_queue = asyncio.Queue()
    
    # Flag to pause audio processing while agent is speaking
    agent_speaking = False
    
    # Event that gets set when client signals unmute
    unmute_event = asyncio.Event()

    async def send_and_wait_for_speak(ws, text):
        """Send text + speak command, then WAIT for the client's unmute signal."""
        nonlocal agent_speaking
        
        # Mute and flush before speaking
        agent_speaking = True
        await stt_service.flush_buffer()
        
        # Send response text and speak command
        await ws.send_json({"type": "response", "text": text})
        await ws.send_json({"type": "speak", "text": text})
        logger.info(f"Sent speak: '{text[:50]}...' — waiting for unmute")
        
        # Wait for client to signal TTS is done (with timeout)
        unmute_event.clear()
        try:
            await asyncio.wait_for(unmute_event.wait(), timeout=30.0)
        except asyncio.TimeoutError:
            logger.warning("Unmute timeout — forcing unmute")
        
        # Flush any stale audio accumulated during TTS, then unmute
        await stt_service.flush_buffer()
        agent_speaking = False
        logger.info("Unmuted after TTS complete")

    try:
        # Task 1: Receive ALL messages (binary audio + text control signals)
        async def receive_messages():
            """Receives both audio bytes and control messages from the client."""
            nonlocal agent_speaking
            try:
                while True:
                    msg = await websocket.receive()
                    
                    if msg["type"] == "websocket.receive":
                        if "bytes" in msg and msg["bytes"]:
                            data = msg["bytes"]
                            
                            # Skip audio while agent is speaking
                            if agent_speaking:
                                continue

                            transcript = await stt_service.add_audio(data)
                            if transcript:
                                logger.info(f"Transcript received: '{transcript}'")
                                transcript_queue.put_nowait(transcript)
                                try:
                                    await websocket.send_json({"type": "transcript", "text": transcript})
                                except:
                                    pass
                        
                        elif "text" in msg and msg["text"]:
                            try:
                                data = json.loads(msg["text"])
                                if data.get("type") == "unmute":
                                    logger.info("Client: TTS done signal received")
                                    unmute_event.set()
                            except json.JSONDecodeError:
                                pass
                    
                    elif msg["type"] == "websocket.disconnect":
                        break
                        
            except WebSocketDisconnect:
                logger.info("Client disconnected")
                if not agent_speaking:
                    remaining = await stt_service.force_transcribe()
                    if remaining:
                        transcript_queue.put_nowait(remaining)
            except Exception as e:
                logger.error(f"Error in receive_messages: {e}")

        # Task 2: Process transcripts and generate responses
        async def process_conversation():
            """Main brain: Waits for transcripts -> LLM response -> Browser TTS."""
            logger.info("Starting conversation processing loop")
            while True:
                user_text = await transcript_queue.get()
                logger.info(f"Processing transcript: {user_text}")
                if not user_text.strip():
                    continue

                messages.append({"role": "user", "content": user_text})

                # Get LLM response
                logger.info("Calling Groq LLM...")
                response_text = await asyncio.to_thread(
                    llm_service.get_response, messages, system_prompt=SYSTEM_PROMPT
                )
                messages.append({"role": "assistant", "content": response_text})
                logger.info(f"LLM Response: {response_text}")

                # Speak the response and WAIT for TTS to finish
                try:
                    await send_and_wait_for_speak(websocket, response_text)
                except Exception as e:
                    logger.error(f"Error in send_and_wait_for_speak: {e}")

                # Calendar event creation — only after TTS has finished
                if "creating event" in response_text.lower():
                    logger.info("Agent indicates event creation. Extracting details...")
                    extraction_json = await asyncio.to_thread(llm_service.extract_details, messages)
                    if extraction_json:
                        try:
                            details = json.loads(extraction_json)
                            logger.info(f"Extracted details: {details}")

                            if "meeting" in details and isinstance(details["meeting"], dict):
                                details = details["meeting"]

                            summary = details.get("summary") or details.get("title", "Meeting")
                            start_time = details.get("start_time")
                            duration = details.get("duration_minutes", 30)
                            attendee_email = "swaroopms658@gmail.com"

                            if start_time:
                                event = await asyncio.to_thread(
                                    calendar_service.create_event, summary, start_time, duration, attendee_email
                                )
                                if event:
                                    await send_and_wait_for_speak(websocket, "Meeting scheduled successfully.")
                                else:
                                    await send_and_wait_for_speak(websocket, "Sorry, I couldn't schedule the meeting.")
                        except Exception as e:
                            logger.error(f"Error parsing extraction: {e}")

        receive_task = asyncio.create_task(receive_messages())
        process_task = asyncio.create_task(process_conversation())

        await asyncio.gather(receive_task, process_task)

    except WebSocketDisconnect:
        logger.info("WebSocket disconnected")
    except Exception as e:
        logger.error(f"An error occurred: {e}")
    finally:
        await stt_service.finish()
