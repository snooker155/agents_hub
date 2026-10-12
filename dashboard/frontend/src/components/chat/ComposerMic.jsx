import { Loader, Mic } from 'lucide-react';

/**
 * The composer's microphone, in the Send button's place while the box is
 * empty: hold it (or Space) and speak, let go and it is sent. `voice` is
 * ./useChatVoice; the ring follows the microphone's level while it listens.
 */
export default function ComposerMic({ voice, disabled, t }) {
  const ring = voice.talking ? Math.round(2 + voice.level * 10) : 0;
  const title = voice.transcribing ? t('chat.voice.transcribing')
    : voice.talking ? t('chat.voice.recording') : t('chat.voice.hold');
  const press = (e) => {
    if (disabled || e.button > 0) return;
    e.preventDefault();
    e.currentTarget.setPointerCapture?.(e.pointerId);
    voice.start();
  };
  const release = (e) => {
    e.currentTarget.releasePointerCapture?.(e.pointerId);
    if (voice.active()) voice.end();
  };
  return (
    <button
      type="button"
      onPointerDown={press}
      onPointerUp={release}
      onPointerCancel={release}
      onContextMenu={(e) => e.preventDefault()}
      disabled={disabled || voice.transcribing}
      title={title}
      aria-label={title}
      aria-pressed={voice.talking}
      data-testid="composer-mic"
      data-state={voice.transcribing ? 'transcribing' : voice.talking ? 'recording' : 'idle'}
      style={ring ? { boxShadow: `0 0 0 ${ring}px rgb(239 68 68 / 0.25)` } : undefined}
      className={`flex-shrink-0 w-8 h-8 flex items-center justify-center rounded-full transition-colors touch-none select-none
        disabled:opacity-40 disabled:cursor-not-allowed
        ${voice.talking ? 'bg-red-500 text-white' : 'bg-indigo-600 text-white hover:bg-indigo-700'}`}
    >
      {voice.transcribing ? <Loader className="w-3.5 h-3.5 animate-spin" /> : <Mic className="w-3.5 h-3.5" />}
    </button>
  );
}
