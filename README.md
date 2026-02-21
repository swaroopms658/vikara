
# Vikara AI - Voice Scheduling Agent

A real-time voice agent that initiates a conversation, collects meeting details, confirms them, and creates a real calendar event — using only free tiers, developed on Windows, with a Python FastAPI backend.
<img width="1839" height="763" alt="image" src="https://github.com/user-attachments/assets/b4d52b9c-cd8f-4174-a01b-95389d458c95" />


## Architecture

- **Backend**: FastAPI (Python)
- **STT**: Groq Whisper (Free Tier)
- **LLM**: Groq Llama 3.1 (Free Tier)
- **TTS**: Browser speechSynthesis API
- **Calendar**: Google Calendar API
- **Transport**: WebSocket (raw PCM audio)

## Prerequisites

You will need API keys for the following services:
1.  **Groq**: [https://console.groq.com/](https://console.groq.com/) — Used for both STT (Whisper) and LLM (Llama 3.1)
2.  **Google Calendar API**:
    -   Create a Project in Google Cloud Console.
    -   Enable "Google Calendar API".
    -   Create a Service Account and download the JSON key file.
    -   **Important**: Share your primary calendar with the Service Account email address (found in the JSON file) so it can create events.

## Local Setup

1.  **Clone/Open the Repository**:
    Ensure you are in the project directory.

2.  **Environment Variables**:
    Copy `.env.example` to `.env` and fill in your API keys.
    ```bash
    cp .env.example .env
    ```
    For Google Calendar, put the `service_account.json` in the project root and set `GOOGLE_SERVICE_ACCOUNT_JSON=service_account.json` in `.env`.

3.  **Install Dependencies**:
    ```bash
    pip install -r requirements.txt
    ```

4.  **Run the Server**:
    ```bash
    uvicorn app.main:app --reload
    ```
    Or on Windows:
    ```bash
    run.bat
    ```

5.  **Access the App**:
    Open [http://localhost:8000](http://localhost:8000) in your browser.

## Deployment to Render

1.  **Push to GitHub**:
    Create a new repository on GitHub and push this code.

2.  **Create Service on Render**:
    -   Go to [dashboard.render.com](https://dashboard.render.com/).
    -   Click "New +", select "Web Service".
    -   Connect your GitHub repository.

3.  **Configure Render**:
    -   **Runtime**: Python 3
    -   **Build Command**: `pip install -r requirements.txt`
    -   **Start Command**: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
    -   **Environment Variables**: Add `GROQ_API_KEY` and optionally `GOOGLE_SERVICE_ACCOUNT_JSON`.

4.  **Deploy**:
    Click "Create Web Service". Once deployed, you will get a public URL.

## Usage

1.  Open the web page.
2.  Click "Start Conversation".
3.  Speak to the agent (e.g., "Hi, I'd like to schedule a meeting with John.").
4.  The agent will ask for details one at a time (Name, Time, Title).
5.  Confirm the details.
6.  The agent will say "creating event" and the event will appear in your Google Calendar.
