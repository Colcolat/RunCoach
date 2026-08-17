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

// --- voice state -------------------------------------------------------------

/**
 * One attribute drives every visual difference between idle, listening,
 * thinking, speaking and unavailable. Keeping it in the DOM rather than in
 * scattered class toggles means the CSS owns the appearance and this file only
 * ever says which state we are in.
 *
 * The status line is set alongside it, never instead of it: colour alone would
 * leave the state invisible to anyone who cannot see the difference.
 */
function setState(name, message) {
  el("voice").dataset.state = name;
  if (message !== undefined) el("status").textContent = message;
}

function setLabel(text) {
  el("mic-label").textContent = text;
}

// --- conversation view -------------------------------------------------------

function addBubble(role, text, { partial = false, notice = false } = {}) {
  const log = el("log");
  const node = document.createElement("div");
  node.className = `bubble ${role}${partial ? " partial" : ""}${notice ? " notice" : ""}`;
  node.textContent = text;
  log.appendChild(node);
  log.scrollTop = log.scrollHeight;
  return node;
}

function addDivider(text) {
  const node = document.createElement("p");
  node.className = "divider";
  node.textContent = text;
  el("log").appendChild(node);
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

// --- profile panel -----------------------------------------------------------

const LEVEL_LABELS = {
  principiante: "Principiante",
  intermedio: "Intermedio",
  avanzado: "Avanzado",
};

function setField(id, value, { countdown } = {}) {
  const field = el(id);
  const known = value !== null && value !== undefined && value !== "";

  field.dataset.empty = known ? "false" : "true";
  field.querySelector("[data-value]").textContent = known ? value : "sin registrar";

  const slot = field.querySelector("[data-countdown]");
  if (slot) {
    slot.hidden = !countdown;
    if (countdown) slot.textContent = countdown;
  }
  return known;
}

/** Turn "2026-10-01" into "1 de octubre de 2026", in the reader's locale rules. */
function spellDate(iso) {
  const parsed = new Date(`${iso}T00:00:00`);
  if (Number.isNaN(parsed.getTime())) return iso;
  return parsed.toLocaleDateString("es-ES", {
    day: "numeric",
    month: "long",
    year: "numeric",
  });
}

/**
 * The weeks come from the server, which already computes them for the coach's
 * prompt. Recomputing here would be a second implementation of the same
 * arithmetic, free to disagree with the one the coach reasons from.
 */
function raceCountdown(weeks) {
  if (weeks === null || weeks === undefined) return null;
  if (weeks < 0) return "ya pasó";
  if (weeks === 0) return "es esta semana";
  if (weeks === 1) return "falta 1 semana";
  return `faltan ${weeks} semanas`;
}

// --- the week ----------------------------------------------------------------

// 1 = Monday, matching what the server stores. Days are integers rather than
// names because the coach answers in whatever language it is spoken to, and a
// week given in English has to fill the same panel as one given in Spanish.
const DAY_NAMES = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"];
const DAY_INITIALS = ["L", "M", "X", "J", "V", "S", "D"];

function renderPlan(sessions, totalKm) {
  const panel = el("plan");
  if (!sessions || sessions.length === 0) {
    panel.hidden = true;
    return;
  }
  panel.hidden = false;

  el("plan-total").textContent = `${totalKm} km repartidos en ${sessions.length} ${
    sessions.length === 1 ? "día" : "días"
  }`;

  const list = el("plan-list");
  list.replaceChildren(
    ...sessions.map((session) => {
      const item = document.createElement("li");

      const day = document.createElement("span");
      day.className = "plan-day";
      day.textContent = DAY_NAMES[session.day - 1] || "";

      const distance = document.createElement("span");
      distance.className = "plan-km";
      distance.textContent = `${session.km} km`;

      item.append(day, distance);

      if (session.note) {
        const note = document.createElement("span");
        note.className = "plan-note";
        note.textContent = session.note;
        item.append(note);
      }
      return item;
    })
  );

  // The strip covers all seven days, so a rest day is visibly a rest day rather
  // than an absence the eye has to notice.
  const byDay = new Map(sessions.map((session) => [session.day, session]));
  el("week").replaceChildren(
    ...DAY_INITIALS.map((initial, index) => {
      const day = index + 1;
      const session = byDay.get(day);

      const cell = document.createElement("div");
      cell.className = "day";
      cell.setAttribute("role", "listitem");
      cell.dataset.rest = session ? "false" : "true";
      cell.setAttribute(
        "aria-label",
        session
          ? `${DAY_NAMES[index]}, ${session.km} kilómetros`
          : `${DAY_NAMES[index]}, descanso`
      );

      const label = document.createElement("span");
      label.className = "day-label";
      label.textContent = initial;

      const value = document.createElement("span");
      value.className = "day-km";
      // A dot, not the word: the strip is read at a glance and "descanso"
      // wrapped across two lines in a cell this size.
      value.textContent = session ? session.km : "·";

      cell.append(label, value);
      return cell;
    })
  );
}

async function refreshProfile() {
  let data;
  try {
    const response = await fetch(`/api/profile/${sessionId()}`);
    data = await response.json();
  } catch (error) {
    // The panel is informational. Failing to read it must not disturb the
    // conversation, which is what the runner actually came for.
    return;
  }

  const known = [
    setField("field-goal", data.goal),
    setField("field-level", LEVEL_LABELS[data.experience_level] || data.experience_level),
    setField("field-volume", data.weekly_km ? `${data.weekly_km} km por semana` : null),
    setField("field-race", data.race_date ? spellDate(data.race_date) : null, {
      countdown: raceCountdown(data.weeks_to_race),
    }),
  ].some(Boolean);

  el("profile").dataset.known = known ? "true" : "false";
  renderPlan(data.plan, data.plan_total_km);
  renderTelegram(data);
}

/**
 * Reminders are the one thing a closed tab cannot do, which is the entire
 * reason for linking a chat at all. The link is a deep link: Telegram hands
 * whatever follows `start=` to the bot, so the session id travels with the tap
 * and the runner never types a code.
 */
function renderTelegram(data) {
  const box = el("telegram");

  // No bot configured on this deployment: offer nothing rather than a dead link.
  if (!data.telegram_url) {
    box.hidden = true;
    return;
  }
  box.hidden = false;

  const link = el("telegram-link");
  const note = el("telegram-note");

  if (data.telegram_linked) {
    link.hidden = true;
    note.textContent = data.reminder_at
      ? `Telegram conectado. Te aviso a las ${data.reminder_at}.`
      : "Telegram conectado. Dime a qué hora quieres que te avise.";
    return;
  }

  link.hidden = false;
  link.href = data.telegram_url;
  note.textContent =
    "Para que te avise cuando toque entrenar, aunque tengas esto cerrado.";
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

  setState("speaking", "El entrenador está hablando");
  source.onended = () => {
    if (ctx.currentTime >= state.nextPlayTime - 0.05 && state.listening) {
      setState("listening", "Escuchando");
    }
  };
}

// --- voice session -----------------------------------------------------------

async function startVoice() {
  try {
    setState("idle", "Pidiendo permiso del micrófono");
    state.micStream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    });
  } catch (error) {
    setState("unavailable", "Sin acceso al micrófono. Puedes escribir abajo.");
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
        setState("listening", "Escuchando");
        setLabel("Terminar");
        break;
      case "transcript":
        // The runner's own words coming back means the model is transcribing,
        // not composing yet; the coach's mean it has started answering.
        if (message.role === "coach") setState("thinking", "Pensando");
        appendTranscript(message.role, message.text);
        break;
      case "turn_complete":
        finishTurn();
        break;
      case "budget":
        // The voice budget is nearly spent. Said out loud rather than left to
        // surprise the runner when the microphone stops answering.
        addBubble(
          "coach",
          `Nos queda alrededor de ${Math.max(1, Math.round(message.remaining / 60))} ` +
            "minuto(s) de voz en esta sesión. Después seguimos por escrito, sin perder el hilo.",
          { notice: true }
        );
        break;
      case "profile_updated":
        // The server read something new out of what was just said. Speaking a
        // goal should fill the panel exactly as typing it does.
        refreshProfile();
        break;
      case "fallback":
        finishTurn();
        stopVoice(fallbackText(message.reason), "unavailable");
        break;
    }
  };

  socket.onerror = () =>
    setState("unavailable", "Error de conexión. Puedes escribir abajo.");
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

function stopVoice(message, endState = "idle") {
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

  setLabel("Hablar");
  setState(endState, message || "Listo para hablar o escribir");
}

// --- text chat ---------------------------------------------------------------

async function sendText(event) {
  event.preventDefault();
  const input = el("text");
  const message = input.value.trim();
  if (!message) return;

  input.value = "";
  addBubble("user", message);
  setState("thinking", "Pensando");

  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, session_id: sessionId() }),
    });
    const data = await response.json();
    // A degraded reply is the application speaking, not the coach, and it reads
    // as one: a rate limit clears in under a minute and should not look like a
    // failure of the conversation.
    addBubble("coach", data.reply, { notice: data.degraded });
    setState("idle", data.degraded ? "Respuesta de respaldo" : "Listo para hablar o escribir");
    if (!data.degraded) refreshProfile();

    // The week is read from the coach's own reply, which means the extraction
    // starts only once the reply exists and finishes after the refresh above.
    // One more look, rather than a poll: if it is still not there the next turn
    // picks it up, and a panel is not worth a timer that never stops.
    if (data.plan_pending) setTimeout(refreshProfile, 2500);
  } catch (error) {
    addBubble("coach", "No pude conectar con el entrenador. Intenta de nuevo.", {
      notice: true,
    });
    setState("unavailable", "Error de conexión");
  }
}

// --- boot --------------------------------------------------------------------

async function boot() {
  el("talk").addEventListener("click", () => {
    if (state.listening) stopVoice();
    else startVoice();
  });
  el("composer").addEventListener("submit", sendText);

  refreshProfile();

  // Replay first. A returning runner should find the conversation where they
  // left it, not a greeting that pretends they have never been here.
  try {
    const stored = await fetch(`/api/history/${sessionId()}`);
    const { messages } = await stored.json();
    if (messages.length) {
      addDivider("Retomamos donde lo dejaste");
      for (const message of messages) {
        addBubble(message.role === "user" ? "user" : "coach", message.content);
      }
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
