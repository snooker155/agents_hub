import { useState } from 'react';
import { Check, Copy } from 'lucide-react';

import { SNIPPETS } from './connectionSnippet';
import { useI18n } from '../i18n';

// The two pieces both connection surfaces need: copying a value that is shown
// once, and the snippet someone pastes into their own project. They live here
// rather than on the list page because the detail page needs them too, and a
// page importing from another page is not how anything else here is arranged.

/** Copy-to-clipboard that says it worked, because a silent copy is a copy
 *  people do twice. */
export function CopyButton({ value, label }) {
  const { t } = useI18n();
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard access can be refused (insecure origin, denied permission).
      // The text is on screen and selectable, so this is not worth an error.
    }
  };
  return (
    <button
      type="button"
      onClick={copy}
      className="inline-flex items-center gap-1.5 px-2.5 py-1 text-xs font-semibold text-gray-600 bg-white border border-gray-200 rounded-lg hover:bg-gray-50"
    >
      {copied ? <Check className="w-3.5 h-3.5 text-emerald-600" /> : <Copy className="w-3.5 h-3.5" />}
      {copied ? t('connections.copied') : (label || t('connections.copy'))}
    </button>
  );
}

/** The snippet box, with one tab per way of reporting. */
export function SetupSnippet({ token, connectionId }) {
  const { t } = useI18n();
  const [tab, setTab] = useState('python');
  const snippet = SNIPPETS[tab].build({ token, connectionId });
  return (
    <div>
      <div className="flex items-center gap-1 mb-2">
        {Object.entries(SNIPPETS).map(([key, { label }]) => (
          <button
            key={key}
            type="button"
            onClick={() => setTab(key)}
            className={`px-2.5 py-1 text-xs font-semibold rounded-lg ${
              tab === key ? 'bg-indigo-50 text-indigo-700' : 'text-gray-500 hover:bg-gray-50'
            }`}
          >
            {label}
          </button>
        ))}
        <span className="ml-auto">
          <CopyButton value={snippet} label={t('connections.copySnippet')} />
        </span>
      </div>
      <pre className="bg-gray-900 text-gray-100 text-[11px] leading-relaxed rounded-lg p-3 overflow-x-auto whitespace-pre">
        {snippet}
      </pre>
    </div>
  );
}
