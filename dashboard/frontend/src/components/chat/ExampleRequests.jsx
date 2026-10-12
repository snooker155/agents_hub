import { MessageSquareText } from 'lucide-react';
import { useEffect, useState } from 'react';
import { useI18n } from '../../i18n';

/**
 * Clickable example requests for an empty chat: an agent's (AgentExamples)
 * or a flow's (the Chat page in flow mode, the flow run page's chat).
 * `load()` returns a promise of the response whose `data.examples` is the
 * list; it runs again whenever `loadKey` changes. Nothing shows until that
 * answer is in, so `fallback` never flashes before the real examples; with
 * no examples and no fallback nothing shows at all. A click hands the text to
 * `onPick`, which puts it in the message box rather than sending it.
 */
export default function ExampleRequests({ load, loadKey, fallback = null, onPick, compact = false }) {
  const { t } = useI18n();
  const [loaded, setLoaded] = useState({ key: null, examples: [] });

  useEffect(() => {
    if (!loadKey) return undefined;
    let alive = true;
    load()
      .then((res) => { if (alive) setLoaded({ key: loadKey, examples: res.data?.examples || [] }); })
      .catch(() => { if (alive) setLoaded({ key: loadKey, examples: [] }); });
    return () => { alive = false; };
    // `load` is a fresh closure on every render; `loadKey` names what it loads.
  }, [loadKey]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!loadKey || loaded.key !== loadKey) return null;
  const examples = loaded.examples.length ? loaded.examples : (fallback || []);
  if (!examples.length) return null;

  return (
    <div className={`${compact ? 'mt-4' : 'mt-6'} w-full max-w-2xl`}>
      <div className="text-xs font-medium uppercase tracking-wide text-gray-400 mb-2">
        {t('chat.examples.title')}
      </div>
      <div className={`grid gap-2 ${compact ? '' : 'sm:grid-cols-2'}`}>
        {examples.map((text) => (
          <button
            key={text}
            type="button"
            onClick={() => onPick(text)}
            title={t('chat.examples.hint')}
            className="flex items-start gap-2 text-left text-sm text-gray-700 bg-white border border-gray-200 rounded-xl px-3 py-2.5 hover:border-indigo-300 hover:bg-indigo-50 transition-colors"
          >
            <MessageSquareText className="w-4 h-4 mt-0.5 shrink-0 text-indigo-400" />
            <span className="leading-snug">{text}</span>
          </button>
        ))}
      </div>
    </div>
  );
}
