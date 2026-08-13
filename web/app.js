/**
 * RunCoach browser client.
 *
 * Voice runs over a WebSocket to the server, which proxies to Gemini Live. The
 * browser never sees the API key.
 *
 * Text chat over POST /api/chat is always available, and is what the session
 * falls back to when the voice budget runs out or the model is unreachable.
 */

const SESSION_KEY = "runcoach.session";

/**
 * The session id is the only thing that identifies a returning runner: there is
 * no login. Keeping it in localStorage means closing the tab does not erase the
 * conversation, and losing it is indistinguishable from being a new visitor.
 */
function sessionId() {
  let id = localStorage.getItem(SESSION_KEY);
  if (!id) {
    id = crypto.randomUUID().replace(/-/g, "");
    localStorage.setItem(SESSION_KEY, id);
  }
  return id;
}

const state = {
  socket: null,
  audioContext: null,
  playbackContext: null,
  workletNode: null,
  micStream: null,
  outputRate: 24000,
  nextPlayTime: 0,
  listening: false,
  partial: { user: null, coach: null },
};

const el = (id) => document.getElementById(id);

// --- conversation view -------------------------------------------------------

function addBubble(role, text, { partial = false } = {}) {
  const log = el("log");
  const node = document.createElement("div");
  node.className = `bubble ${role}${partial ? " partial" : ""}`;
  node.textContent = text;
  log.appendChild(node);
  log.scrollTop = log.scrollHeight;
  return node;
}

/**
 * Transcripts arrive in fragments as the speaker talks. Appending each one as
 * its own bubble would shred a sentence across a dozen lines, so fragments are
 * accumulated into the same bubble until the turn completes.
 */
function appendTranscript(role, text) {
  if (!state.partial[role]) {
    state.partial[role] = addBubble(role, text, { partial: true });
  } else {
    state.partial[role].textContent += text;
  }
  el("log").scrollTop = el("log").scrollHeight;
}

function finishTurn() {
  for (const role of ["user", "coach"]) {
    if (state.partial[role]) {
      state.partial[role].classList.remove("partial");
      state.partial[role] = null;
    }
  }
}

function setStatus(text, kind = "") {
  const node = el("status");
  node.textContent = text;
  node.className = `status ${kind}`;
}

// --- playback ----------------------------------------------------------------

/**
 * Audio arrives as raw PCM in many small chunks. Playing each one with its own
 * `start()` at "now" would overlap them; scheduling each to begin where the
 * previous ended keeps the speech continuous.
 */
function playChunk(arrayBuffer) {
  if (!state.playbackContext) {
    state.playbackContext = new AudioContext();
  }
  const ctx = state.playbackContext;

  const pcm = new Int16Array(arrayBuffer);
  const floats = new Float32Array(pcm.length);
  for (let i = 0; i < pcm.length; i++) {
    floats[i] = pcm[i] / 0x8000;
  }

  // The buffer declares the model's rate; the browser resamples to the device.
  const buffer = ctx.createBuffer(1, floats.length, state.outputRate);
  buffer.copyToChannel(floats, 0);

  const source = ctx.createBufferSource();
  source.buffer = buffer;
  source.connect(ctx.destination);

  const startAt = Math.max(ctx.currentTime, state.nextPlayTime);
  source.start(startAt);
  state.nextPlayTime = startAt + buffer.duration;

  setStatus("El entrenador está hablando", "speaking");
  source.onended = () => {
    if (ctx.currentTime >= state.nextPlayTime - 0.05 && state.listening) {
      setStatus("Escuchando", "listening");
    }
  };
}

// --- voice session -----------------------------------------------------------

async function startVoice() {
  try {
    setStatus("Pidiendo permiso del micrófono", "");
    state.micStream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    });
  } catch (error) {
    setStatus("Sin acceso al micrófono. Puedes escribir abajo.", "error");
    return;
  }

  const protocol = location.protocol === "https:" ? "wss:" : "ws:";
  // The session id travels in the URL so the server can persist transcripts
  // against the same conversation the text chat writes to.
  const socket = new WebSocket(
    `${protocol}//${location.host}/ws/voice?session_id=${encodeURIComponent(sessionId())}`
  );
  socket.binaryType = "arraybuffer";
  state.socket = socket;

  socket.onmessage = async (event) => {
    if (event.data instanceof ArrayBuffer) {
      playChunk(event.data);
      return;
    }
    const message = JSON.parse(event.data);
    switch (message.type) {
      case "ready":
        state.outputRate = message.output_sample_rate;
        await openMicrophone(message.input_sample_rate);
        state.listening = true;
        setStatus("Escuchando", "listening");
        el("talk").textContent = "Terminar";
        el("talk").classList.add("active");
        break;
      case "transcript":
        appendTranscript(message.role, message.text);
        break;
      case "turn_complete":
        finishTurn();
        break;
      case "fallback":
        finishTurn();
        stopVoice(fallbackText(message.reason));
        break;
    }
  };

  socket.onerror = () => setStatus("Error de conexión. Puedes escribir abajo.", "error");
  socket.onclose = () => {
    if (state.listening) stopVoice("Sesión de voz terminada.");
  };
}

function fallbackText(reason) {
  switch (reason) {
    case "budget_exhausted":
      return "Se agotó el tiempo de voz de esta sesión. Seguimos por escrito.";
    case "not_configured":
      return "La voz no está configurada en este servidor. Seguimos por escrito.";
    default:
      return "La voz no está disponible ahora. Seguimos por escrito.";
  }
}

async function openMicrophone(inputRate) {
  // Ask for the rate the API wants. Browsers may ignore this, which is why the
  // worklet resamples rather than trusting it.
  state.audioContext = new AudioContext({ sampleRate: inputRate });
  await state.audioContext.audioWorklet.addModule("/static/pcm-processor.js");

  const source = state.audioContext.createMediaStreamSource(state.micStream);
  const node = new AudioWorkletNode(state.audioContext, "pcm-processor");

  node.port.onmessage = (event) => {
    if (state.socket && state.socket.readyState === WebSocket.OPEN) {
      state.socket.send(event.data);
    }
  };

  source.connect(node);
  // The worklet produces no output, but some browsers suspend a graph that is
  // not connected to a destination. A muted gain node keeps it running without
  // echoing the microphone into the speakers.
  const silence = state.audioContext.createGain();
  silence.gain.value = 0;
  node.connect(silence).connect(state.audioContext.destination);

  state.workletNode = node;
}

function stopVoice(message) {
  state.listening = false;

  if (state.socket && state.socket.readyState === WebSocket.OPEN) {
    state.socket.send(JSON.stringify({ type: "close" }));
    state.socket.close();
  }
  state.socket = null;

  if (state.micStream) {
    state.micStream.getTracks().forEach((track) => track.stop());
    state.micStream = null;
  }
  if (state.audioContext) {
    state.audioContext.close();
    state.audioContext = null;
  }
  state.workletNode = null;

  el("talk").textContent = "Hablar";
  el("talk").classList.remove("active");
  setStatus(message || "Listo para hablar o escribir", "");
}

// --- text chat ---------------------------------------------------------------

async function sendText(event) {
  event.preventDefault();
  const input = el("text");
  const message = input.value.trim();
  if (!message) return;

  input.value = "";
  addBubble("user", message);
  setStatus("Pensando", "thinking");

  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, session_id: sessionId() }),
    });
    const data = await response.json();
    addBubble("coach", data.reply);
    setStatus(data.degraded ? "Respuesta de respaldo" : "Listo", data.degraded ? "error" : "");
  } catch (error) {
    addBubble("coach", "No pude conectar con el entrenador. Intenta de nuevo.");
    setStatus("Error de conexión", "error");
  }
}

// --- boot --------------------------------------------------------------------

async function boot() {
  el("talk").addEventListener("click", () => {
    if (state.listening) stopVoice();
    else startVoice();
  });
  el("composer").addEventListener("submit", sendText);

  // Replay first. A returning runner should find the conversation where they
  // left it, not a greeting that pretends they have never been here.
  try {
    const stored = await fetch(`/api/history/${sessionId()}`);
    const { messages } = await stored.json();
    for (const message of messages) {
      addBubble(message.role === "user" ? "user" : "coach", message.content);
    }
    if (messages.length) {
      setStatus("Retomamos donde lo dejaste");
      return;
    }
  } catch (error) {
    // A history that cannot be read is not worth blocking the greeting on.
  }

  try {
    const response = await fetch("/api/welcome");
    addBubble("coach", (await response.json()).greeting);
  } catch (error) {
    addBubble("coach", "Hola, soy tu entrenador. ¿Qué quieres preparar?");
  }
}

boot();
