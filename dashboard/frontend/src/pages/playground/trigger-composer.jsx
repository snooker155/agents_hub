import React, { useState } from 'react';
import { Loader, Send } from 'lucide-react';
import { useI18n } from '../../i18n';

/**
 * Send a message into a running world from outside it.
 *
 * In triggered mode this is the external event that wakes an agent; in a
 * synchronous world it is an ordinary message, delivered on the next tick. It
 * is deliberately the same door either way — an agent must not be able to tell
 * an operator's poke from a colleague's.
 */
export function TriggerComposer({ agents, onSend, className = '' }) {
  const { t } = useI18n();
  const [agent, setAgent] = useState(agents[0] || '');
  const [text, setText] = useState('');
  const [sending, setSending] = useState(false);

  const send = async () => {
    if (!agent || !text.trim()) return;
    setSending(true);
    const ok = await onSend(agent, text.trim());
    setSending(false);
    if (ok) setText('');
  };

  return (
    /* Built like the chat's composer, because it is one: one bordered field
       that lights up as a whole on focus, the text at reading size, and a
       round send button at the end. The role picker is the only extra — it is
       the "to:" of this message, so it is wide enough to read a name in
       rather than a dropdown to guess at. */
    <div className={className}>
      <div className="text-[11px] font-bold text-gray-500 uppercase mb-1.5">
        {t('playground.externalTrigger')}
      </div>
      {/* Filled rather than outlined: the field sits on a white card, and an
          outline alone left the recipient picker floating in the card's own
          background instead of reading as one input.
          The fill stays put on focus, and the picker drops its native
          appearance so the tint runs under it too — a select left to the
          platform paints its own background on macOS, which cut the fill in
          two and undid the one-field reading this is built for. */}
      <div
        className="flex items-center gap-3 bg-gray-100 border border-gray-300 rounded-2xl px-3 py-2
          focus-within:border-indigo-400 focus-within:ring-2 focus-within:ring-indigo-100
          shadow-sm transition-all"
      >
        <select
          value={agent} onChange={(e) => setAgent(e.target.value)}
          title={t('playground.triggerRecipient')}
          className="shrink-0 w-40 sm:w-48 appearance-none text-sm text-gray-700 bg-transparent
            border-0 border-r border-gray-300 rounded-none pr-2 py-1 focus:outline-none truncate"
        >
          {agents.map((a) => <option key={a} value={a}>{a}</option>)}
        </select>
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') send(); }}
          placeholder={t('playground.triggerPlaceholder')}
          className="flex-1 min-w-0 text-base text-gray-800 placeholder-gray-400 bg-transparent
            border-0 focus:outline-none leading-relaxed"
        />
        <button
          onClick={send} disabled={sending || !text.trim()}
          title={t('playground.externalTrigger')}
          className="flex-shrink-0 w-8 h-8 flex items-center justify-center rounded-full
            bg-indigo-600 text-white hover:bg-indigo-700
            disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
        >
          {sending ? <Loader className="w-4 h-4 animate-spin" /> : <Send className="w-3.5 h-3.5" />}
        </button>
      </div>
      <p className="text-[11px] text-gray-400 mt-1.5">{t('playground.triggerHint')}</p>
    </div>
  );
}

export default TriggerComposer;
