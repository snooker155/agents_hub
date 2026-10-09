/**
 * The assistant at the foot of every screen after the voice one: ask about
 * this screen by voice or by typing, hear and read the answer. The turn names
 * the screen (useWizardAssistant), so "what is this for?" needs no more.
 */
import { useState } from 'react';
import { Mic, SendHorizontal, Square, X } from 'lucide-react';
import { useI18n } from '../../i18n';
import LiveMark from '../liveMark/LiveMark';

export default function AssistDock({ assistant, screen, speak }) {
  const { t } = useI18n();
  const [text, setText] = useState('');
  const [open, setOpen] = useState(false);

  const send = async (message, spoken) => {
    setOpen(true);
    await assistant.ask(message, { spoken, screen, speak: spoken || speak });
  };

  const submit = (e) => {
    e.preventDefault();
    const message = text.trim();
    if (!message || assistant.busy) return;
    setText('');
    assistant.unlock();
    send(message, false);
  };

  const mic = async () => {
    if (assistant.listening) {
      const heard = await assistant.stopListening();
      if (heard) await send(heard, true);
      return;
    }
    assistant.unlock();
    setOpen(true);
    await assistant.startListening();
  };

  const markState = assistant.listening ? 'listen' : assistant.speaking ? 'speak' : assistant.busy ? 'think' : 'idle';
  const shown = open && (assistant.reply || assistant.heard || assistant.busy || assistant.listening);

  return (
    <div className="shrink-0 w-full max-w-xl mx-auto px-4 sm:px-0 pb-3" data-testid="first-run-assist">
      {shown && (
        <div className="mb-2 rounded-2xl border border-gray-200 bg-white px-4 py-3 shadow-sm max-h-48 overflow-y-auto relative" data-testid="first-run-assist-answer">
          <button type="button" onClick={() => { setOpen(false); assistant.cancelSpeech(); }}
            aria-label={t('firstRun.assist.close')} className="absolute right-2 top-2 p-1 text-gray-400 hover:text-gray-600">
            <X className="w-4 h-4" />
          </button>
          {assistant.heard && <p className="pr-6 text-sm text-gray-500">«{assistant.heard}»</p>}
          {assistant.reply
            ? <p className="pr-6 mt-1 text-sm text-gray-800 whitespace-pre-wrap">{assistant.reply}</p>
            : <p className="pr-6 mt-1 text-sm text-gray-400">{t(assistant.listening ? 'firstRun.voice.listening' : 'firstRun.voice.thinking')}</p>}
          {assistant.held && <p className="mt-1 text-xs text-gray-500">{t('firstRun.assist.held')}</p>}
          {assistant.error && <p className="mt-1 text-xs text-red-600">{assistant.error}</p>}
        </div>
      )}
      <form onSubmit={submit} className="flex items-center gap-2 rounded-full border border-gray-200 bg-white pl-2 pr-1.5 py-1.5 shadow-sm">
        <LiveMark state={markState} size={28} frame="logo" label="Agents Hub" />
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder={t('firstRun.assist.placeholder')}
          aria-label={t('firstRun.assist.placeholder')}
          className="flex-1 min-w-0 bg-transparent text-sm text-gray-900 placeholder:text-gray-400 focus:outline-none"
          data-testid="first-run-assist-input"
        />
        {assistant.canListen && (
          <button type="button" onClick={mic} disabled={assistant.busy}
            aria-label={assistant.listening ? t('firstRun.voice.stop') : t('firstRun.voice.talk')}
            className={`w-9 h-9 rounded-full flex items-center justify-center ${assistant.listening ? 'bg-red-500 text-white' : 'text-indigo-600 hover:bg-indigo-50'} disabled:opacity-40`}
            data-testid="first-run-assist-mic">
            {assistant.listening ? <Square className="w-4 h-4" /> : <Mic className="w-4 h-4" />}
          </button>
        )}
        <button type="submit" disabled={!text.trim() || assistant.busy} aria-label={t('firstRun.assist.send')}
          className="w-9 h-9 rounded-full bg-indigo-600 text-white flex items-center justify-center disabled:opacity-30"
          data-testid="first-run-assist-send">
          <SendHorizontal className="w-4 h-4" />
        </button>
      </form>
    </div>
  );
}
