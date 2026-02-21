/**
 * Frontend logic for Vikara Voice Intelligence.
 * Manages Microphone streaming via Web Audio API (raw PCM),
 * WebSocket communication, Browser TTS, and Premium UI updates.
 * 
 * KEY: Mic is muted while TTS is speaking to prevent feedback loops.
 * Both client-side (stops sending audio) AND server-side (discards audio).
 */

let socket;
let audioContext;
let audioStream;
let speechQueue = [];
let isSpeaking = false;
let micMuted = false;

// DOM Elements
const startBtn = document.getElementById('start-btn');
const stopBtn = document.getElementById('stop-btn');
const statusText = document.getElementById('status-text');
const statusDot = document.getElementById('status-dot');
const transcriptContainer = document.getElementById('transcript-container');
const emptyState = document.getElementById('empty-state');
const bars = document.querySelectorAll('.bar');

startBtn.onclick = startConversation;
stopBtn.onclick = stopConversation;

async function startConversation() {
    startBtn.classList.add('hidden');
    stopBtn.classList.remove('hidden');
    updateStatus("Connecting", "bg-yellow-500");

    try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        audioStream = stream;
        document.getElementById('latency-text').textContent = "GROQ WHISPER + LLM ACTIVE";

        audioContext = new (window.AudioContext || window.webkitAudioContext)();
        const sampleRate = audioContext.sampleRate;
        console.log('Native sample rate:', sampleRate);

        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        socket = new WebSocket(`${protocol}//${window.location.host}/ws/audio?sample_rate=${sampleRate}`);

        socket.onopen = () => {
            updateStatus("Listening", "bg-green-500", true);
            if (emptyState) emptyState.style.display = 'none';
            startRecording(stream, sampleRate);
        };

        socket.onmessage = async (event) => {
            if (typeof event.data === 'string') {
                const data = JSON.parse(event.data);
                if (data.type === 'transcript') {
                    addBubble(data.text, 'user');
                } else if (data.type === 'response') {
                    addBubble(data.text, 'agent');
                } else if (data.type === 'speak') {
                    speakText(data.text);
                }
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

function updateStatus(text, colorClass, pulse = false) {
    statusText.textContent = text;
    statusDot.className = `w-3 h-3 rounded-full ${colorClass} ${pulse ? 'pulse' : ''}`;
}

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

/**
 * Browser TTS using speechSynthesis API.
 * Mutes mic during speech and sends "unmute" signal to server when done.
 */
function speakText(text) {
    speechQueue.push(text);
    processSpeechQueue();
}

function processSpeechQueue() {
    if (isSpeaking || speechQueue.length === 0) return;

    isSpeaking = true;
    micMuted = true;
    updateStatus("Speaking", "bg-purple-500", true);
    console.log('[TTS] Speaking — mic MUTED');

    const text = speechQueue.shift();
    const utterance = new SpeechSynthesisUtterance(text);

    utterance.rate = 1.0;
    utterance.pitch = 1.0;
    utterance.volume = 1.0;

    const voices = speechSynthesis.getVoices();
    const preferredVoice = voices.find(v =>
        v.name.includes('Google') && v.lang.startsWith('en')
    ) || voices.find(v =>
        v.lang.startsWith('en') && v.name.includes('Female')
    ) || voices.find(v =>
        v.lang.startsWith('en')
    );

    if (preferredVoice) {
        utterance.voice = preferredVoice;
    }

    utterance.onend = () => {
        isSpeaking = false;

        // If there are more items in the queue, speak them first (stay muted)
        if (speechQueue.length > 0) {
            processSpeechQueue();
            return;
        }

        // Queue is empty — wait 500ms then unmute
        setTimeout(() => {
            micMuted = false;
            console.log('[TTS] All speech done — mic UNMUTED');
            updateStatus("Listening", "bg-green-500", true);
            if (socket && socket.readyState === WebSocket.OPEN) {
                socket.send(JSON.stringify({ type: "unmute" }));
            }
        }, 500);
    };

    utterance.onerror = (e) => {
        console.error("Speech synthesis error:", e);
        isSpeaking = false;
        micMuted = false;
        if (socket && socket.readyState === WebSocket.OPEN) {
            socket.send(JSON.stringify({ type: "unmute" }));
        }
        processSpeechQueue();
    };

    speechSynthesis.speak(utterance);
}

/**
 * Raw PCM audio capture via Web Audio API.
 * Skips sending when micMuted is true (during TTS).
 */
function startRecording(stream, sampleRate) {
    const source = audioContext.createMediaStreamSource(stream);
    const processor = audioContext.createScriptProcessor(4096, 1, 1);

    processor.onaudioprocess = (e) => {
        if (!socket || socket.readyState !== WebSocket.OPEN) return;
        if (micMuted) return;

        const float32 = e.inputBuffer.getChannelData(0);
        const int16 = new Int16Array(float32.length);
        for (let i = 0; i < float32.length; i++) {
            const s = Math.max(-1, Math.min(1, float32[i]));
            int16[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
        }

        socket.send(int16.buffer);
    };

    source.connect(processor);
    processor.connect(audioContext.destination);

    audioContext._processor = processor;
    audioContext._source = source;

    animateBars(true);
}

function stopConversation() {
    speechSynthesis.cancel();
    speechQueue = [];
    isSpeaking = false;
    micMuted = false;

    if (audioContext) {
        if (audioContext._processor) audioContext._processor.disconnect();
        if (audioContext._source) audioContext._source.disconnect();
        audioContext.close().catch(() => { });
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

let barInterval;
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
        bars.forEach(bar => { bar.style.height = '8px'; });
    }
}

// Preload voices
if (typeof speechSynthesis !== 'undefined') {
    speechSynthesis.getVoices();
    speechSynthesis.onvoiceschanged = () => speechSynthesis.getVoices();
}
