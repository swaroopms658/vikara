/**
 * Frontend logic for Vikara Voice Intelligence.
 * Manages Microphone streaming, WebSocket communication, and Premium UI updates.
 */

let socket; // WebSocket connection to the backend
let audioContext; // Web Audio API context for raw PCM capture
let audioProcessor; // ScriptProcessorNode for capturing raw audio
let audioStream; // MediaStream from getUserMedia
let audioQueue = []; // Queues incoming audio blobs from the server to play them sequentially
let isPlaying = false; // Flag to prevent overlapping audio playback

// DOM Elements: Used to update the UI state and markers
const startBtn = document.getElementById('start-btn');
const stopBtn = document.getElementById('stop-btn');
const statusText = document.getElementById('status-text');
const statusDot = document.getElementById('status-dot');
const transcriptContainer = document.getElementById('transcript-container');
const emptyState = document.getElementById('empty-state');
const bars = document.querySelectorAll('.bar');

/**
 * Initializes the conversation: Gets mic access and opens the WebSocket.
 */
startBtn.onclick = startConversation;

/**
 * Ends the session: Stops recording and closes the socket.
 */
stopBtn.onclick = stopConversation;

async function startConversation() {
    startBtn.classList.add('hidden');
    stopBtn.classList.remove('hidden');
    updateStatus("Connecting", "bg-yellow-500");

    try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        document.getElementById('latency-text').textContent = "GROQ + DEEPGRAM ACTIVE";

        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        socket = new WebSocket(`${protocol}//${window.location.host}/ws/audio`);

        socket.onopen = () => {
            updateStatus("Listening", "bg-green-500", true);
            if (emptyState) emptyState.style.display = 'none';
            startRecording(stream);
        };

        socket.onmessage = async (event) => {
            if (typeof event.data === 'string') {
                const data = JSON.parse(event.data);
                if (data.type === 'transcript') {
                    addBubble(data.text, 'user');
                } else if (data.type === 'response') {
                    addBubble(data.text, 'agent');
                }
            } else {
                // Audio binary
                const blob = event.data;
                audioQueue.push(blob);
                processAudioQueue();
            }
        };

        socket.onclose = () => {
            stopConversation();
            updateStatus("Disconnected", "bg-red-500");
        };

        socket.onerror = (error) => {
            console.error("WebSocket Error:", error);
            updateStatus("Error", "bg-red-500");
        };

    } catch (err) {
        console.error("Error accessing microphone:", err);
        updateStatus("Mic Denied", "bg-red-500");
        resetUI();
    }
}

/** Updates status text and UI color markers. */
function updateStatus(text, colorClass, pulse = false) {
    statusText.textContent = text;
    statusDot.className = `w-3 h-3 rounded-full ${colorClass} ${pulse ? 'pulse' : ''}`;
}

/** Adds a chat bubble to the UI. Sender is 'user' or 'agent'. */
function addBubble(text, sender) {
    const bubble = document.createElement('div');
    bubble.className = `flex ${sender === 'user' ? 'justify-end' : 'justify-start'} bubble-in`;

    const inner = document.createElement('div');
    inner.className = `max-w-[80%] rounded-2xl px-4 py-2 ${sender === 'user'
        ? 'bg-blue-600/20 text-blue-100 border border-blue-500/30'
        : 'bg-white/10 text-gray-100 border border-white/10'
        }`;
    inner.textContent = text;

    bubble.appendChild(inner);
    transcriptContainer.appendChild(bubble);
    transcriptContainer.scrollTop = transcriptContainer.scrollHeight;
}

function startRecording(stream) {
    audioStream = stream;
    audioContext = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 16000 });
    const source = audioContext.createMediaStreamSource(stream);

    // ScriptProcessorNode captures raw PCM audio
    audioProcessor = audioContext.createScriptProcessor(4096, 1, 1);

    audioProcessor.onaudioprocess = (e) => {
        if (socket && socket.readyState === WebSocket.OPEN) {
            const float32Data = e.inputBuffer.getChannelData(0);
            // Convert float32 [-1, 1] to int16 [-32768, 32767]
            const int16Data = new Int16Array(float32Data.length);
            for (let i = 0; i < float32Data.length; i++) {
                const s = Math.max(-1, Math.min(1, float32Data[i]));
                int16Data[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
            }
            socket.send(int16Data.buffer);
        }
    };

    source.connect(audioProcessor);
    audioProcessor.connect(audioContext.destination);
    animateBars(true);
}

function stopConversation() {
    if (audioProcessor) {
        audioProcessor.disconnect();
        audioProcessor = null;
    }
    if (audioContext) {
        audioContext.close();
        audioContext = null;
    }
    if (audioStream) {
        audioStream.getTracks().forEach(track => track.stop());
        audioStream = null;
    }
    if (socket && socket.readyState === WebSocket.OPEN) {
        socket.close();
    }
    animateBars(false);
    resetUI();
}

function resetUI() {
    startBtn.classList.remove('hidden');
    stopBtn.classList.add('hidden');
}

/** Plays audio synthesize blobs in the correct order. */
async function processAudioQueue() {
    if (isPlaying || audioQueue.length === 0) return;

    isPlaying = true;
    updateStatus("Speaking", "bg-purple-500", true);

    const blob = audioQueue.shift();
    const audioUrl = URL.createObjectURL(blob);
    const audio = new Audio(audioUrl);

    audio.onended = () => {
        isPlaying = false;
        updateStatus("Listening", "bg-green-500", true);
        URL.revokeObjectURL(audioUrl);
        processAudioQueue();
    };

    try {
        await audio.play();
    } catch (e) {
        console.error("Error playing audio:", e);
        isPlaying = false;
        processAudioQueue();
    }
}

let barInterval;

/** Controls the visual bar animation while thinking/speaking. */
function animateBars(active) {
    if (barInterval) clearInterval(barInterval);
    if (active) {
        barInterval = setInterval(() => {
            bars.forEach(bar => {
                const h = Math.floor(Math.random() * 30) + 10;
                bar.style.height = `${h}px`;
                bar.style.transition = "height 0.2s ease-in-out";
            });
        }, 200);
    } else {
        bars.forEach(bar => {
            bar.style.height = '8px';
        });
    }
}
