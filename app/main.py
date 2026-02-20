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
        {"role": "system", "content": "You are Vikara, an intelligent voice assistant that helps schedule meetings. "
         "- Ask for the attendee's name if not provided.\n"
         "- Ask for the desired meeting time if not provided.\n"
         "- Ask for the meeting summary/title if not provided.\n"
         "- Once you have the name, time, and title, ask for confirmation. If confirmed, say 'creating event'.\n"
         "- Output strictly text that should be spoken. Keep responses concise."}
    ]

    # Queue for transcripts to process
    transcript_queue = asyncio.Queue()

    try:
        # Task 1: Receive audio and feed to STT
        async def receive_audio():
            """Continuously receives raw PCM chunks and feeds them to Groq Whisper STT."""
            try:
                while True:
                    data = await websocket.receive_bytes()

                    # Feed audio to STT (it buffers and detects speech)
                    transcript = await stt_service.add_audio(data)

                    if transcript:
                        logger.info(f"Transcript received: '{transcript}'")
                        transcript_queue.put_nowait(transcript)
                        # Send transcript to UI
                        try:
                            await websocket.send_json({"type": "transcript", "text": transcript})
                        except:
                            pass
            except WebSocketDisconnect:
                logger.info("Client disconnected from receive_audio")
                # Try to transcribe any remaining audio
                remaining = await stt_service.force_transcribe()
                if remaining:
                    transcript_queue.put_nowait(remaining)
            except Exception as e:
                logger.error(f"Error receiving audio: {e}")

        # Task 2: Process transcripts and generate responses
        async def process_conversation():
            """
            Main brain: Waits for transcripts -> LLM response -> Sends text for browser TTS.
            """
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
                    llm_service.get_response, messages, system_prompt=messages[0]['content']
                )
                messages.append({"role": "assistant", "content": response_text})

                logger.info(f"LLM Response: {response_text}")

                # Send text response to UI (browser handles TTS via speechSynthesis)
                try:
                    await websocket.send_json({"type": "response", "text": response_text})
                    # Also send a speak command so the browser knows to use TTS
                    await websocket.send_json({"type": "speak", "text": response_text})
                except Exception as e:
                    logger.error(f"Error sending response: {e}")

                # Calendar event creation detection
                if "creating event" in response_text.lower():
                    logger.info("Agent indicates event creation. Extracting details...")

                    extraction_json = await asyncio.to_thread(llm_service.extract_details, messages)
                    if extraction_json:
                        try:
                            details = json.loads(extraction_json)
                            logger.info(f"Extracted details: {details}")

                            # Handle potential nested 'meeting' key
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
                                    success_msg = "I have successfully scheduled the meeting."
                                    await websocket.send_json({"type": "response", "text": success_msg})
                                    await websocket.send_json({"type": "speak", "text": success_msg})
                                else:
                                    error_msg = "Sorry, I couldn't schedule the meeting. Please try again."
                                    await websocket.send_json({"type": "response", "text": error_msg})
                                    await websocket.send_json({"type": "speak", "text": error_msg})
                        except Exception as e:
                            logger.error(f"Error parsing extraction: {e}")

        receive_task = asyncio.create_task(receive_audio())
        process_task = asyncio.create_task(process_conversation())

        await asyncio.gather(receive_task, process_task)

    except WebSocketDisconnect:
        logger.info("WebSocket disconnected")
    except Exception as e:
        logger.error(f"An error occurred: {e}")
    finally:
        await stt_service.finish()
