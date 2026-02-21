"""
Calendar Service manages integration with Google Calendar API.
Handles authentication via Service Account and event creation.
"""
import os # Access environment variables for credentials path
import logging # Log event creation details and errors
import datetime # Handle date Parsing and duration calculations
from google.oauth2 import service_account # Google-specific auth for server-to-server communication
from googleapiclient.discovery import build # Client for building the Calendar API service

logger = logging.getLogger(__name__)

class CalendarService:
    """
    Service to interact with Google Calendar.
    Enables the agent to schedule meetings autonomously.
    """
    def __init__(self):
        """Initializes settings and triggers authentication."""
        self.creds = None
        self.service = None
        self.scopes = ['https://www.googleapis.com/auth/calendar']
        self._authenticate()

    def _authenticate(self):
        """Internal authentication logic: Checks for Service Account credentials."""
        # Determine authentication method based on env vars
        # Prioritize Service Account
        sa_info = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
        if sa_info:
            # If it's a path
            if os.path.exists(sa_info):
                self.creds = service_account.Credentials.from_service_account_file(
                    sa_info, scopes=self.scopes
                )
            else:
                 # Assume it's JSON content (would need parsing, skipping for now to keep simple)
                 logger.warning("GOOGLE_SERVICE_ACCOUNT_JSON provided but file not found. Assuming content parsing not implemented yet.")
        
        if self.creds:
            self.service = build('calendar', 'v3', credentials=self.creds)
        else:
            logger.warning("No Google Calendar credentials found. Calendar features will be disabled.")

    def create_event(self, summary, start_time, duration_minutes=30, attendee_email=None):
        """
        Creates an event on the Service Account calendar.
        :param summary: Title of the meeting.
        :param start_time: ISO format start string.
        :param duration_minutes: Meeting length in minutes.
        :param attendee_email: Optional email for the invite (fallback used on restriction).
        """
        if not self.service:
            logger.error("Calendar service not initialized.")
            return None

        try:
            # Parse start_time (assuming ISO format from LLM)
            # LLM should return ISO string
            start_dt = datetime.datetime.fromisoformat(start_time.replace('Z', '+00:00'))
            end_dt = start_dt + datetime.timedelta(minutes=duration_minutes)

            event = {
                'summary': summary,
                'start': {
                    'dateTime': start_dt.isoformat(),
                    'timeZone': 'Asia/Kolkata',
                },
                'end': {
                    'dateTime': end_dt.isoformat(),
                    'timeZone': 'Asia/Kolkata',
                },
            }
            
            if attendee_email:
                event['attendees'] = [{'email': attendee_email}]
            else:
                # Fallback to hardcoded for testing if needed, or just warn
                logger.warning("No attendee email provided for event.")

            try:
                event_result = self.service.events().insert(calendarId='primary', body=event, sendUpdates='all').execute()
            except Exception as e:
                if "forbiddenForServiceAccounts" in str(e) or "403" in str(e):
                    logger.warning("Service Account cannot send invites. Retrying without attendees.")
                    if 'attendees' in event:
                        del event['attendees']
                    event_result = self.service.events().insert(calendarId='primary', body=event).execute()
                else:
                    raise e

            logger.info(f"Event created: {event_result.get('htmlLink')}")
            return event_result
        except Exception as e:
            logger.error(f"Error creating event: {e}")
            return None
