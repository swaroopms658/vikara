"""
LLM Service handles communication with the Groq API for conversation and data extraction.
"""
import os # Access environment variables for API keys
import logging # Log Groq interaction issues for debugging
from groq import Groq # Direct client for high-performance inference

logger = logging.getLogger(__name__)

class LLMService:
    """
    Service to manage Large Language Model interactions via Groq.
    Supports both standard chat responses and structured JSON extraction.
    """
    def __init__(self):
        """Initializes the Groq client with API key validation."""
        self.api_key = os.getenv("GROQ_API_KEY")
        if not self.api_key:
            logger.error("GROQ_API_KEY is not set")
            raise ValueError("GROQ_API_KEY is not set")
        self.client = Groq(api_key=self.api_key)

    def get_response(self, messages, system_prompt):
        """
        Generates a conversational response from the LLM.
        :param messages: List of previous conversation turns.
        :param system_prompt: The persona/instructions for the model.
        """
        # Prepare messages
        all_messages = [{"role": "system", "content": system_prompt}] + messages
        
        try:
            logger.info(f"Requesting LLM response using model: llama-3.3-70b-versatile")
            chat_completion = self.client.chat.completions.create(
                messages=all_messages,
                model="llama-3.3-70b-versatile",
                timeout=10.0 # Add explicit timeout
            )
            return chat_completion.choices[0].message.content
        except Exception as e:
            logger.error(f"Groq API error (Detailed): {type(e).__name__}: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return "I'm sorry, I'm having trouble processing that right now."

    def extract_details(self, conversation_history):
        """
        Parses conversation history into structured meeting data.
        Returns a JSON string containing summary, start_time, and duration.
        """
        from datetime import datetime
        current_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        system_prompt = f"""
        Extract the following details from the conversation as a flat JSON object.
        Reference date (TODAY): {current_date}
        
        {{
            "summary": "meeting title (use 'Meeting' if not specified)",
            "start_time": "ISO 8601 format (e.g., 2026-10-27T10:00:00, assume future year based on reference date)",
            "duration_minutes": 30
        }}
        
        Return ONLY the JSON object. Do not nest it under any key.
        """
        messages = [{"role": "system", "content": system_prompt}] + conversation_history
        try:
            chat_completion = self.client.chat.completions.create(
                messages=messages,
                model="llama-3.1-8b-instant",
                response_format={"type": "json_object"}
            )
            return chat_completion.choices[0].message.content
        except Exception as e:
            logger.error(f"Groq Extraction error: {e}")
            return None
