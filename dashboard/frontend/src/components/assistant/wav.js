/**
 * A recording as a WAV file, for browsers whose MediaRecorder cannot make a
 * compressed one the transcription route takes. Mono, 16-bit, resampled to
 * 16 kHz: speech needs no more, and a minute stays under 2 MB.
 */

export const WAV_RATE = 16000;

/** Float samples of every captured chunk, joined and resampled to `rate`. */
export function resample(chunks, fromRate, rate = WAV_RATE) {
  const total = chunks.reduce((n, c) => n + c.length, 0);
  const joined = new Float32Array(total);
  let at = 0;
  for (const c of chunks) {
    joined.set(c, at);
    at += c.length;
  }
  if (!fromRate || fromRate === rate) return joined;
  const ratio = fromRate / rate;
  const out = new Float32Array(Math.floor(total / ratio));
  for (let i = 0; i < out.length; i += 1) {
    // Average the source samples this output sample covers: a cheap low pass.
    const start = Math.floor(i * ratio);
    const end = Math.min(total, Math.floor((i + 1) * ratio));
    let sum = 0;
    for (let j = start; j < end; j += 1) sum += joined[j];
    out[i] = end > start ? sum / (end - start) : joined[start] || 0;
  }
  return out;
}

/** 16-bit PCM WAV bytes of mono float samples. */
export function encodeWav(samples, rate = WAV_RATE) {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);
  const text = (offset, s) => { for (let i = 0; i < s.length; i += 1) view.setUint8(offset + i, s.charCodeAt(i)); };
  text(0, 'RIFF');
  view.setUint32(4, 36 + samples.length * 2, true);
  text(8, 'WAVE');
  text(12, 'fmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);          // PCM
  view.setUint16(22, 1, true);          // mono
  view.setUint32(24, rate, true);
  view.setUint32(28, rate * 2, true);   // bytes per second
  view.setUint16(32, 2, true);          // block align
  view.setUint16(34, 16, true);         // bits per sample
  text(36, 'data');
  view.setUint32(40, samples.length * 2, true);
  for (let i = 0; i < samples.length; i += 1) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return new Uint8Array(buffer);
}

/** Root mean square of a byte time-domain frame (AnalyserNode), 0 to 1. */
export function levelOf(bytes) {
  if (!bytes || !bytes.length) return 0;
  let sum = 0;
  for (let i = 0; i < bytes.length; i += 1) {
    const v = (bytes[i] - 128) / 128;
    sum += v * v;
  }
  // Speech sits around 0.05 to 0.3 RMS; stretched so a normal voice fills the meter.
  return Math.min(1, Math.sqrt(sum / bytes.length) * 3.5);
}
