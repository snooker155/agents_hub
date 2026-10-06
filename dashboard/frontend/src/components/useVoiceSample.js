import { useCallback, useEffect, useRef, useState } from 'react';

/**
 * Plays a voice sample: `toggle(fetchSample)` asks for one
 * (`fetchSample(signal)` resolves to `{blob, language, text}`) and plays
 * it; a second call while it loads or plays stops it. `state` is
 * `{loading, playing, text, language, error}`. A change of `sig` (the values
 * the sample was made with) stops it and forgets what it said.
 */
export default function useVoiceSample(sig) {
  const [state, setState] = useState({});
  const current = useRef(null);

  const release = useCallback(() => {
    const now = current.current;
    current.current = null;
    if (!now) return;
    now.abort?.abort();
    if (now.audio) {
      now.audio.onended = null;
      now.audio.pause();
    }
    if (now.url) URL.revokeObjectURL(now.url);
  }, []);

  useEffect(() => release, [release]);
  // Other values stop the sample; what it said is shown only for its own.
  useEffect(() => { release(); }, [sig, release]);
  const shown = state.sig === sig ? state : {};

  const toggle = useCallback(async (fetchSample, failed) => {
    if (current.current) {
      release();
      setState((s) => ({ sig, text: s.text, language: s.language }));
      return;
    }
    const abort = new AbortController();
    const mine = { abort };
    current.current = mine;
    setState({ sig, loading: true });
    try {
      const got = await fetchSample(abort.signal);
      if (current.current !== mine) return;
      const url = URL.createObjectURL(got.blob);
      const audio = new Audio(url);
      Object.assign(mine, { url, audio, abort: null });
      audio.onended = () => {
        if (current.current !== mine) return;
        release();
        setState((s) => ({ sig, text: s.text, language: s.language }));
      };
      setState({ sig, playing: true, text: got.text, language: got.language });
      await audio.play();
    } catch (e) {
      if (abort.signal.aborted || current.current !== mine) return;
      release();
      setState({ sig, error: e?.message || failed || 'Could not play the sample' });
    }
  }, [release, sig]);

  return { state: shown, toggle, busy: Boolean(shown.loading || shown.playing) };
}
