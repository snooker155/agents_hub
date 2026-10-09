import { useCallback, useEffect, useRef, useState } from 'react';
import { ChevronDown, ChevronRight, Loader, Mic, Play, Square, Trash2, Upload } from 'lucide-react';
import {
  getRuntimeVoices, addRuntimeVoice, updateRuntimeVoice, deleteRuntimeVoice, getRuntimeVoiceAudio, tryRuntimeVoice,
  cleanRuntimeVoice, getRuntimeJob,
} from '../../api/localModels';
import { CLONING_ENGINES } from './speechPresets';
import { useI18n } from '../../i18n';
import { useToast, errorDetail } from '../toast';

// The languages a recording can be marked with: Chatterbox's, in the order
// people are likeliest to pick them.
const LANGUAGES = ['ru', 'en', 'de', 'fr', 'es', 'it', 'pt', 'pl', 'nl', 'tr', 'sv', 'da', 'no', 'fi', 'el',
  'ja', 'zh', 'ko', 'ar', 'he', 'hi', 'ms', 'sw'];
// A recording stops by itself here: the runtime keeps 30 s at most.
const MAX_SECONDS = 30;
// What a recording's room can be cleaned of: nothing, noise, noise and echo.
const CLEANUP_MODES = ['none', 'denoise', 'restore'];
// How often a running cleanup is asked how far it got.
const CLEANUP_POLL_MS = 1500;
const NAME_RE = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,39}$/;
// What to read aloud while recording: varied sounds, about 30 seconds (the
// runtime picks the best 10 for Chatterbox, OpenVoice hears all of it).
const READ_ALOUD = {
  ru: 'Сегодня утром я вышел из дома чуть раньше обычного. На улице было прохладно, пахло свежим хлебом из пекарни на углу. Я купил кофе, посмотрел на часы и понял, что успеваю на встречу. Как же приятно никуда не спешить! По дороге я встретил старого знакомого, и мы поговорили о планах на выходные. Он собирается за город, к озеру, а я, наверное, останусь дома и наконец дочитаю книгу. Вечером обещали дождь, так что зонт лучше взять с собой.',
  en: 'This morning I left home a little earlier than usual. The air was cool, and the bakery on the corner smelled of fresh bread. I bought a coffee, checked the time, and realised I would make it to the meeting. What a pleasure it is not to rush! On the way I ran into an old friend, and we talked about our plans for the weekend. He is heading out of town to the lake, while I will probably stay home and finally finish my book. They say it will rain tonight, so I had better take an umbrella.',
  de: 'Heute Morgen bin ich etwas früher als sonst aus dem Haus gegangen. Die Luft war kühl, und die Bäckerei an der Ecke duftete nach frischem Brot. Ich kaufte einen Kaffee, sah auf die Uhr und merkte, dass ich pünktlich sein würde. Wie schön es ist, sich nicht zu beeilen! Unterwegs traf ich einen alten Bekannten, und wir sprachen über unsere Pläne fürs Wochenende. Er fährt aufs Land an den See, ich bleibe wohl zu Hause und lese endlich mein Buch zu Ende. Für den Abend ist Regen angesagt, also nehme ich lieber einen Schirm mit.',
};
// A sample longer than this has a part picked for Chatterbox worth showing.
const REFERENCE_SECONDS = 10;

// The reason in an error whose body came back as a Blob (the audio routes).
async function blobDetail(e) {
  const data = e?.response?.data;
  if (data instanceof Blob) {
    try {
      const parsed = JSON.parse(await data.text());
      return parsed?.detail || parsed?.error?.message || '';
    } catch {
      return '';
    }
  }
  return errorDetail(e);
}

/**
 * One sound at a time for the whole card: a recording or a line read in it.
 * `key` names what plays, so its button can show a stop sign.
 */
function usePlayer() {
  const current = useRef(null);
  const [state, setState] = useState({ key: '', loading: false });

  const stop = useCallback(() => {
    const now = current.current;
    current.current = null;
    if (now?.audio) {
      now.audio.onended = null;
      now.audio.ontimeupdate = null;
      now.audio.pause();
    }
    if (now?.url) URL.revokeObjectURL(now.url);
    setState({ key: '', loading: false });
  }, []);

  useEffect(() => stop, [stop]);

  // `range` ([start, end] seconds) plays only that part.
  const play = useCallback(async (key, fetchBlob, range) => {
    stop();
    const mine = { key };
    current.current = mine;
    setState({ key, loading: true });
    try {
      const blob = await fetchBlob();
      if (current.current !== mine) return null;
      mine.url = URL.createObjectURL(blob);
      mine.audio = new Audio(mine.url);
      mine.audio.onended = () => { if (current.current === mine) stop(); };
      if (range) {
        mine.audio.currentTime = range[0];
        mine.audio.ontimeupdate = () => {
          if (current.current === mine && mine.audio.currentTime >= range[1]) stop();
        };
      }
      setState({ key, loading: false });
      await mine.audio.play();
      return true;
    } catch (e) {
      if (current.current === mine) stop();
      throw e;
    }
  }, [stop]);

  return { playing: state.key, loading: state.loading, play, stop };
}

// Whether a voice's sample is long enough that the part Chatterbox listens
// to is a part of it, not the whole.
function hasReference(voice) {
  const ref = voice.reference;
  return Array.isArray(ref) && ref.length === 2 && (voice.duration || 0) > REFERENCE_SECONDS + 0.5;
}

function languageName(code, locale) {
  try {
    return new Intl.DisplayNames([locale], { type: 'language' }).of(code) || code;
  } catch {
    return code;
  }
}

/**
 * The Local tab's recorded voices: samples of people's own speech that the
 * runtime's cloning models speak in. Chatterbox reads text in the voice
 * (closer, slow without a GPU); OpenVoice gives speech another model read the
 * voice's timbre (fast, the reading model's intonation). A recording shows up
 * as a voice of both, in every voice picker, for the person who made it (and
 * for everyone once shared). `models` is the runtime's model list.
 */
export default function VoicesCard({ models }) {
  const { t, language: locale } = useI18n();
  const toast = useToast();
  const [voices, setVoices] = useState(null);
  const [open, setOpen] = useState(true);
  const [adding, setAdding] = useState(null);
  const player = usePlayer();

  const cloning = (models || []).filter((m) => CLONING_ENGINES.includes(m.engine));
  const readers = (models || []).filter((m) => m.kind === 'speech' && !CLONING_ENGINES.includes(m.engine));
  const hasOpenVoice = cloning.some((m) => m.engine === 'openvoice');

  // Asked again after a change here and when a cloning model comes or goes,
  // not on every status poll.
  const [reloadKey, setReloadKey] = useState(0);
  const load = useCallback(() => setReloadKey((k) => k + 1), []);
  const cloningKey = cloning.map((m) => m.name).join(',');
  useEffect(() => {
    let live = true;
    getRuntimeVoices()
      .then(({ data }) => { if (live) setVoices(data?.voices || []); })
      // The runtime is not running, or too old for voices: no card.
      .catch(() => { if (live) setVoices(null); });
    return () => { live = false; };
  }, [reloadKey, cloningKey]);

  // Running cleanups (a runtime job each), asked until they end; the end
  // reloads the list, which then carries the cleaned sample.
  const [progress, setProgress] = useState({});
  const reported = useRef(new Set());
  const cleaningIds = (voices || []).map((v) => v.cleaning?.job_id).filter(Boolean).join(',');
  useEffect(() => {
    if (!cleaningIds) return undefined;
    let live = true;
    const ids = cleaningIds.split(',');
    const tick = async () => {
      const found = await Promise.all(ids.map((id) => getRuntimeJob(id).then((r) => r.data).catch(() => null)));
      if (!live) return;
      setProgress((old) => Object.fromEntries([...Object.entries(old), ...found.filter(Boolean).map((j) => [j.id, j])]));
      const ended = found.filter((j) => j && (j.status === 'done' || j.status === 'error') && !reported.current.has(j.id));
      ended.forEach((j) => {
        reported.current.add(j.id);
        if (j.status === 'done') toast.success(t('localModels.voices.cleaned', { name: j.meta?.voice || '' }));
        else toast.error(t('localModels.voices.cleanupFailed'), j.error || '');
      });
      if (ended.length) load();
    };
    tick();
    const timer = setInterval(tick, CLEANUP_POLL_MS);
    return () => { live = false; clearInterval(timer); };
  }, [cleaningIds]); // eslint-disable-line react-hooks/exhaustive-deps

  if (voices === null) return null;

  const change = async (voice, fields) => {
    try {
      await updateRuntimeVoice(voice.name, fields);
      load();
    } catch (e) {
      toast.error(t('localModels.voices.updateFailed'), errorDetail(e));
    }
  };

  const remove = async (voice) => {
    if (!window.confirm(t('localModels.voices.confirmDelete', { name: voice.name }))) return;
    try {
      await deleteRuntimeVoice(voice.name);
      toast.success(t('localModels.voices.deleted', { name: voice.name }));
      load();
    } catch (e) {
      toast.error(t('localModels.voices.deleteFailed'), errorDetail(e));
    }
  };

  const clean = async (voice, mode) => {
    try {
      await cleanRuntimeVoice(voice.name, mode);
      load();
    } catch (e) {
      toast.error(t('localModels.voices.cleanupFailed'), errorDetail(e));
    }
  };

  // The sample, the recording before its cleanup, or (`range`) the part of
  // the sample Chatterbox listens to.
  const listen = async (voice, original = false, range = null) => {
    const key = `${range ? 'reference' : original ? 'original' : 'sample'}:${voice.name}`;
    if (player.playing === key) return player.stop();
    try {
      await player.play(key, async () => (await getRuntimeVoiceAudio(voice.name, original)).data, range);
    } catch (e) {
      toast.error(t('localModels.voices.playFailed'), await blobDetail(e));
    }
  };

  const tryIt = async (voice, model) => {
    const key = `try:${voice.name}:${model}`;
    if (player.playing === key) return player.stop();
    let took = '';
    try {
      await player.play(key, async () => {
        const r = await tryRuntimeVoice(voice.name, model, locale);
        took = r.headers?.['x-synthesis-seconds'] || '';
        return r.data;
      });
      if (took) toast.success(t('localModels.voices.took', { model, seconds: Number(took).toFixed(1) }));
    } catch (e) {
      toast.error(t('localModels.voices.tryFailed'), await blobDetail(e));
    }
  };

  return (
    <div className="bg-white border border-gray-200 rounded-xl overflow-hidden" data-testid="voices-card">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className={`w-full flex items-center gap-2 px-4 py-3 text-left bg-gray-50 hover:bg-gray-100 transition-colors focus:outline-none border-gray-100 ${open ? 'border-b' : ''}`}
      >
        {open ? <ChevronDown className="w-4 h-4 text-gray-500 shrink-0" /> : <ChevronRight className="w-4 h-4 text-gray-500 shrink-0" />}
        <Mic className="w-4 h-4 text-gray-500 shrink-0" />
        <span className="font-semibold text-gray-800 text-sm">{t('localModels.voices.title')}</span>
        <span className="text-xs text-gray-400">{t('localModels.voices.count', { count: voices.length })}</span>
      </button>
      {open && (
        <div className="p-4 space-y-3">
          <p className="text-xs text-gray-500">{t('localModels.voices.hint')}</p>
          {cloning.length === 0 && (
            <p className="text-xs text-amber-700" data-testid="voices-no-models">{t('localModels.voices.noModels')}</p>
          )}
          {voices.length === 0 ? (
            <p className="text-sm text-gray-400">{t('localModels.voices.empty')}</p>
          ) : (
            <ul className="divide-y divide-gray-100" data-testid="voices-list">
              {voices.map((v) => (
                <li key={v.name} className="py-2 flex flex-wrap items-center gap-x-3 gap-y-1.5 text-sm">
                  <span className="font-medium text-gray-800">{v.name}</span>
                  <span className="text-xs text-gray-400">
                    {[v.language && languageName(v.language, locale),
                      v.gender && t(`localModels.voices.genders.${v.gender}`),
                      v.duration && t('localModels.voices.seconds', { seconds: Math.round(v.duration) }),
                      v.cleanup && v.cleanup !== 'none' && t(`localModels.voices.cleanupModes.${v.cleanup}`)]
                      .filter(Boolean).join(' · ')}
                  </span>
                  {v.mine && <span className="text-[11px] px-1.5 py-0.5 rounded bg-indigo-50 text-indigo-700">{t('localModels.voices.mine')}</span>}
                  {v.shared && <span className="text-[11px] px-1.5 py-0.5 rounded bg-gray-100 text-gray-600">{t('localModels.voices.sharedBadge')}</span>}
                  <span className="flex-1" />
                  <button
                    type="button"
                    onClick={() => listen(v)}
                    className="flex items-center gap-1 text-xs px-2 py-1 rounded border border-gray-200 text-gray-600 hover:bg-gray-100"
                  >
                    {player.playing === `sample:${v.name}` ? <Square className="w-3 h-3" /> : <Play className="w-3 h-3" />}
                    {t('localModels.voices.playSample')}
                  </button>
                  {hasReference(v) && (
                    <button
                      type="button"
                      onClick={() => listen(v, false, v.reference)}
                      title={t('localModels.voices.referenceTitle', {
                        start: v.reference[0].toFixed(1), end: v.reference[1].toFixed(1),
                      })}
                      data-testid="voice-play-reference"
                      className="flex items-center gap-1 text-xs px-2 py-1 rounded border border-gray-200 text-gray-600 hover:bg-gray-100"
                    >
                      {player.playing === `reference:${v.name}` ? <Square className="w-3 h-3" /> : <Play className="w-3 h-3" />}
                      {t('localModels.voices.reference', {
                        start: Math.round(v.reference[0]), end: Math.round(v.reference[1]),
                      })}
                    </button>
                  )}
                  {v.cleanup && v.cleanup !== 'none' && (
                    <button
                      type="button"
                      onClick={() => listen(v, true)}
                      title={t('localModels.voices.playOriginalTitle')}
                      data-testid="voice-play-original"
                      className="flex items-center gap-1 text-xs px-2 py-1 rounded border border-gray-200 text-gray-600 hover:bg-gray-100"
                    >
                      {player.playing === `original:${v.name}` ? <Square className="w-3 h-3" /> : <Play className="w-3 h-3" />}
                      {t('localModels.voices.playOriginal')}
                    </button>
                  )}
                  {cloning.map((m) => {
                    const key = `try:${v.name}:${m.name}`;
                    const busy = player.playing === key;
                    // The engine's name, or the model's when the engine has
                    // several (chatterbox-4bit-mlx and chatterbox-8bit-mlx).
                    const label = cloning.filter((o) => o.engine === m.engine).length > 1
                      ? m.name : t(`localModels.speech.engines.${m.engine}`);
                    return (
                      <button
                        key={m.name}
                        type="button"
                        onClick={() => tryIt(v, m.name)}
                        title={t('localModels.voices.tryTitle', { model: m.name })}
                        data-testid={`voice-try-${m.engine}`}
                        className="flex items-center gap-1 text-xs px-2 py-1 rounded border border-indigo-200 text-indigo-700 hover:bg-indigo-50"
                      >
                        {busy && player.loading ? <Loader className="w-3 h-3 animate-spin" />
                          : busy ? <Square className="w-3 h-3" /> : <Play className="w-3 h-3" />}
                        {label}
                      </button>
                    );
                  })}
                  {v.editable && (
                    <>
                      <label className="flex items-center gap-1 text-xs text-gray-600" title={t('localModels.voices.cleanupHint')}>
                        {t('localModels.voices.cleanup')}
                        <select
                          value={v.cleaning?.mode || v.cleanup || 'none'}
                          disabled={!!v.cleaning}
                          onChange={(e) => clean(v, e.target.value)}
                          data-testid="voice-cleanup"
                          className="text-xs border border-gray-200 rounded px-1 py-0.5 bg-white disabled:opacity-60"
                        >
                          {CLEANUP_MODES.map((m) => <option key={m} value={m}>{t(`localModels.voices.cleanupModes.${m}`)}</option>)}
                        </select>
                      </label>
                      <label className="flex items-center gap-1 text-xs text-gray-600">
                        <input type="checkbox" checked={!!v.shared} onChange={(e) => change(v, { shared: e.target.checked })} />
                        {t('localModels.voices.shared')}
                      </label>
                      {hasOpenVoice && (
                        <label className="flex items-center gap-1 text-xs text-gray-600" title={t('localModels.voices.baseHint')}>
                          {t('localModels.voices.base')}
                          <select
                            value={v.base_model || ''}
                            onChange={(e) => change(v, { base_model: e.target.value, base_voice: '' })}
                            className="text-xs border border-gray-200 rounded px-1 py-0.5 bg-white"
                          >
                            <option value="">{t('localModels.voices.baseAuto')}</option>
                            {readers.map((m) => <option key={m.name} value={m.name}>{m.name}</option>)}
                          </select>
                        </label>
                      )}
                      <button
                        type="button"
                        onClick={() => setAdding({ name: v.name, language: v.language || '', gender: v.gender || '', shared: !!v.shared, replace: true, cleanup: v.cleanup || 'restore' })}
                        className="text-xs text-gray-500 hover:text-gray-800"
                      >
                        {t('localModels.voices.replace')}
                      </button>
                      <button type="button" onClick={() => remove(v)} aria-label={t('localModels.voices.delete')} className="text-gray-400 hover:text-red-600">
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    </>
                  )}
                  {v.cleaning && (
                    <CleanupProgress job={progress[v.cleaning.job_id]} />
                  )}
                </li>
              ))}
            </ul>
          )}
          {adding ? (
            <VoiceRecorder
              initial={adding}
              taken={voices.map((v) => v.name)}
              onCancel={() => setAdding(null)}
              onSaved={(name) => {
                setAdding(null);
                toast.success(t('localModels.voices.saved', { name }));
                load();
              }}
            />
          ) : (
            <button
              type="button"
              onClick={() => setAdding({ name: '', language: locale && LANGUAGES.includes(locale) ? locale : 'en', gender: '', shared: false, replace: false, cleanup: 'restore' })}
              data-testid="voice-add"
              className="flex items-center gap-1.5 text-sm px-3 py-1.5 rounded-lg bg-indigo-600 text-white hover:bg-indigo-700"
            >
              <Mic className="w-4 h-4" />
              {t('localModels.voices.add')}
            </button>
          )}
          {cloning.some((m) => m.engine === 'chatterbox') && (
            <p className="text-[11px] text-gray-400">{t('localModels.voices.watermark')}</p>
          )}
        </div>
      )}
    </div>
  );
}

/** How far a voice's cleanup got: the runtime job's last line and percent. */
function CleanupProgress({ job }) {
  const { t } = useI18n();
  const percent = job?.percent > 0 && job.percent < 100 ? ` ${Math.round(job.percent)}%` : '';
  return (
    <span className="basis-full flex items-center gap-1.5 text-xs text-indigo-700" data-testid="voice-cleaning">
      <Loader className="w-3 h-3 animate-spin shrink-0" />
      <span className="truncate">{t('localModels.voices.cleaning')}{job?.message ? ` ${job.message}${percent}` : ''}</span>
    </span>
  );
}

/**
 * A new recording, or a new sample for one of the person's own voices
 * (`initial.replace`): from the microphone, with a passage to read in the
 * voice's language, or from a file. Nothing is kept without the consent box.
 */
function VoiceRecorder({ initial, taken, onCancel, onSaved }) {
  const { t, language: locale } = useI18n();
  const toast = useToast();
  const [form, setForm] = useState(initial);
  const [audio, setAudio] = useState(null);
  const [preview, setPreview] = useState('');
  const [consent, setConsent] = useState(false);
  const [recording, setRecording] = useState(false);
  const [seconds, setSeconds] = useState(0);
  const [saving, setSaving] = useState(false);
  const rec = useRef(null);
  const fileInput = useRef(null);

  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.type === 'checkbox' ? e.target.checked : e.target.value }));

  useEffect(() => () => {
    if (preview) URL.revokeObjectURL(preview);
  }, [preview]);

  const stopRecording = useCallback(() => {
    const r = rec.current;
    if (r && r.recorder.state !== 'inactive') r.recorder.stop();
  }, []);

  useEffect(() => () => {
    const r = rec.current;
    rec.current = null;
    if (r) {
      clearInterval(r.timer);
      if (r.recorder.state !== 'inactive') r.recorder.stop();
      r.stream.getTracks().forEach((track) => track.stop());
    }
  }, []);

  const take = (blob) => {
    setAudio(blob);
    setPreview((old) => {
      if (old) URL.revokeObjectURL(old);
      return URL.createObjectURL(blob);
    });
  };

  const startRecording = async () => {
    let stream;
    try {
      // No echo cancelling or gain riding: both change the voice the models learn.
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: false, autoGainControl: false, noiseSuppression: true },
      });
    } catch (e) {
      toast.error(t('localModels.voices.micFailed'), e?.message || '');
      return;
    }
    const recorder = new MediaRecorder(stream);
    const chunks = [];
    const started = Date.now();
    const timer = setInterval(() => {
      const s = Math.floor((Date.now() - started) / 1000);
      setSeconds(s);
      if (s >= MAX_SECONDS) stopRecording();
    }, 250);
    recorder.ondataavailable = (e) => { if (e.data?.size) chunks.push(e.data); };
    recorder.onstop = () => {
      clearInterval(timer);
      stream.getTracks().forEach((track) => track.stop());
      rec.current = null;
      setRecording(false);
      if (chunks.length) take(new Blob(chunks, { type: recorder.mimeType || 'audio/webm' }));
    };
    rec.current = { recorder, stream, timer };
    setSeconds(0);
    setRecording(true);
    recorder.start(250);
  };

  const pickFile = (e) => {
    const file = e.target.files?.[0];
    if (file) take(file);
    e.target.value = '';
  };

  const nameOk = NAME_RE.test(form.name) && form.name.toLowerCase() !== 'default'
    && (form.replace || !taken.includes(form.name));
  const ready = nameOk && audio && consent && !recording && !saving;

  const save = async () => {
    setSaving(true);
    try {
      const ext = (audio.type || '').includes('ogg') ? 'ogg' : (audio.type || '').includes('mp4') ? 'm4a' : 'webm';
      await addRuntimeVoice({
        name: form.name, audio, filename: audio.name || `${form.name}.${ext}`, language: form.language,
        gender: form.gender, shared: form.shared, consent, replace: form.replace, cleanup: form.cleanup,
      });
      onSaved(form.name);
    } catch (e) {
      toast.error(t('localModels.voices.saveFailed'), errorDetail(e));
    } finally {
      setSaving(false);
    }
  };

  const passage = READ_ALOUD[form.language] || READ_ALOUD.en;

  return (
    <div className="border border-indigo-100 rounded-lg p-3 space-y-3 bg-indigo-50/30" data-testid="voice-recorder">
      <div className="flex flex-wrap gap-3 items-end">
        <label className="text-xs text-gray-600 flex flex-col gap-1">
          {t('localModels.voices.name')}
          <input
            value={form.name}
            onChange={set('name')}
            disabled={form.replace}
            placeholder={t('localModels.voices.namePlaceholder')}
            data-testid="voice-name"
            className="text-sm border border-gray-200 rounded px-2 py-1 bg-white w-40"
          />
        </label>
        <label className="text-xs text-gray-600 flex flex-col gap-1">
          {t('localModels.voices.language')}
          <select value={form.language} onChange={set('language')} className="text-sm border border-gray-200 rounded px-2 py-1 bg-white focus:outline-none">
            {LANGUAGES.map((code) => <option key={code} value={code}>{languageName(code, locale)}</option>)}
          </select>
        </label>
        <label className="text-xs text-gray-600 flex flex-col gap-1">
          {t('localModels.voices.gender')}
          <select value={form.gender} onChange={set('gender')} className="text-sm border border-gray-200 rounded px-2 py-1 bg-white focus:outline-none">
            {['', 'female', 'male'].map((g) => <option key={g} value={g}>{t(`localModels.voices.genders.${g || 'none'}`)}</option>)}
          </select>
        </label>
        <label className="text-xs text-gray-600 flex flex-col gap-1">
          {t('localModels.voices.cleanup')}
          <select value={form.cleanup || 'none'} onChange={set('cleanup')} data-testid="voice-recorder-cleanup" className="text-sm border border-gray-200 rounded px-2 py-1 bg-white focus:outline-none">
            {CLEANUP_MODES.map((m) => <option key={m} value={m}>{t(`localModels.voices.cleanupModes.${m}`)}</option>)}
          </select>
        </label>
        <label className="flex items-center gap-1.5 text-xs text-gray-600 pb-1.5">
          <input type="checkbox" checked={!!form.shared} onChange={set('shared')} />
          {t('localModels.voices.shared')}
        </label>
      </div>
      {form.cleanup && form.cleanup !== 'none' && (
        <p className="text-[11px] text-gray-500">{t('localModels.voices.cleanupHint')}</p>
      )}
      {form.name && !nameOk && <p className="text-xs text-red-600">{t('localModels.voices.nameInvalid')}</p>}

      <div className="text-xs text-gray-600 space-y-1">
        <div className="font-medium">{t('localModels.voices.readThis')}</div>
        <p className="text-sm text-gray-800 bg-white border border-gray-100 rounded p-2 leading-relaxed">{passage}</p>
        <div className="text-gray-400">{t('localModels.voices.recordHint')}</div>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        {recording ? (
          <button type="button" onClick={stopRecording} className="flex items-center gap-1.5 text-sm px-3 py-1.5 rounded-lg bg-red-600 text-white hover:bg-red-700">
            <Square className="w-4 h-4" />
            {t('localModels.voices.stop', { seconds })}
          </button>
        ) : (
          <button type="button" onClick={startRecording} data-testid="voice-record" className="flex items-center gap-1.5 text-sm px-3 py-1.5 rounded-lg border border-gray-200 text-gray-700 hover:bg-gray-100">
            <Mic className="w-4 h-4" />
            {audio ? t('localModels.voices.recordAgain') : t('localModels.voices.record')}
          </button>
        )}
        <span className="text-xs text-gray-400">{t('localModels.voices.or')}</span>
        <button type="button" onClick={() => fileInput.current?.click()} disabled={recording} className="flex items-center gap-1.5 text-sm px-3 py-1.5 rounded-lg border border-gray-200 text-gray-700 hover:bg-gray-100 disabled:opacity-50">
          <Upload className="w-4 h-4" />
          {t('localModels.voices.chooseFile')}
        </button>
        <input ref={fileInput} type="file" accept="audio/*,video/*" onChange={pickFile} className="hidden" data-testid="voice-file" />
        {preview && !recording && <audio controls src={preview} className="h-8" />}
      </div>

      <label className="flex items-start gap-2 text-xs text-gray-700">
        <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} className="mt-0.5" data-testid="voice-consent" />
        {t('localModels.voices.consent')}
      </label>

      <div className="flex gap-2">
        <button type="button" onClick={save} disabled={!ready} data-testid="voice-save" className="flex items-center gap-1.5 text-sm px-3 py-1.5 rounded-lg bg-indigo-600 text-white hover:bg-indigo-700 disabled:opacity-50">
          {saving && <Loader className="w-4 h-4 animate-spin" />}
          {t('localModels.voices.save')}
        </button>
        <button type="button" onClick={onCancel} className="text-sm px-3 py-1.5 rounded-lg text-gray-600 hover:bg-gray-100">
          {t('localModels.voices.cancel')}
        </button>
      </div>
    </div>
  );
}
