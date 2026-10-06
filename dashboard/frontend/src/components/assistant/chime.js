/**
 * Two short rising notes: the assistant heard its name and is listening.
 * Made with WebAudio, so there is no file to fetch; silent where the browser
 * has no AudioContext or keeps it suspended until a gesture.
 */
let shared = null;

export function chime({ volume = 0.08 } = {}) {
  try {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) return;
    shared = shared || new Ctx();
    const ctx = shared;
    if (ctx.state === 'suspended') ctx.resume().catch(() => {});
    const now = ctx.currentTime;
    [[660, 0], [990, 0.11]].forEach(([freq, at]) => {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = 'sine';
      osc.frequency.value = freq;
      gain.gain.setValueAtTime(0, now + at);
      gain.gain.linearRampToValueAtTime(volume, now + at + 0.015);
      gain.gain.exponentialRampToValueAtTime(0.0001, now + at + 0.16);
      osc.connect(gain).connect(ctx.destination);
      osc.start(now + at);
      osc.stop(now + at + 0.18);
    });
  } catch { /* no sound: the mark still shows it */ }
}

export default chime;
