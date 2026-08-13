/**
 * Microphone capture for the Gemini Live API.
 *
 * MediaRecorder is the obvious way to capture audio and the wrong one here: it
 * produces webm/opus containers, and the API wants raw PCM. An AudioWorklet
 * gives the unencoded samples instead.
 *
 * This runs on the audio rendering thread, which must never block: no
 * allocation in the hot path beyond what is needed, no logging, no awaiting.
 *
 * Input   Float32 in [-1, 1] at whatever rate the AudioContext runs (usually
 *         44100 or 48000, and not always what we asked for).
 * Output  Int16 little-endian at exactly 16000 Hz, in ~100 ms chunks.
 */

const TARGET_RATE = 16000;
const CHUNK_MS = 100;
const CHUNK_SAMPLES = (TARGET_RATE * CHUNK_MS) / 1000; // 1600

class PCMProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    // `sampleRate` is a global provided to worklets: the real context rate.
    this.ratio = sampleRate / TARGET_RATE;
    this.pending = new Float32Array(CHUNK_SAMPLES);
    this.pendingCount = 0;
    // Fractional read position into the incoming block, carried across calls so
    // resampling does not restart every 128 samples and drift.
    this.position = 0;
    this.muted = false;

    this.port.onmessage = (event) => {
      if (event.data && typeof event.data.muted === "boolean") {
        this.muted = event.data.muted;
      }
    };
  }

  process(inputs) {
    const input = inputs[0];
    if (!input || !input[0]) {
      return true; // keep the node alive; the mic may still be warming up
    }
    if (this.muted) {
      return true;
    }

    const samples = input[0];

    // Linear interpolation down to 16 kHz. Nearest-neighbour is cheaper but
    // audibly rougher, and speech recognition is sensitive to it.
    while (this.position < samples.length) {
      const index = Math.floor(this.position);
      const frac = this.position - index;
      const current = samples[index];
      const next = index + 1 < samples.length ? samples[index + 1] : current;

      this.pending[this.pendingCount++] = current + (next - current) * frac;

      if (this.pendingCount === CHUNK_SAMPLES) {
        this.port.postMessage(this.toInt16(this.pending), []);
        this.pendingCount = 0;
      }

      this.position += this.ratio;
    }

    // Carry the leftover fraction into the next block.
    this.position -= samples.length;

    return true;
  }

  toInt16(floats) {
    const out = new Int16Array(floats.length);
    for (let i = 0; i < floats.length; i++) {
      const clamped = Math.max(-1, Math.min(1, floats[i]));
      // Asymmetric on purpose: Int16 spans -32768..32767, so the negative side
      // uses a larger multiplier. Using 32767 for both clips quiet audio.
      out[i] = clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff;
    }
    return out.buffer;
  }
}

registerProcessor("pcm-processor", PCMProcessor);
