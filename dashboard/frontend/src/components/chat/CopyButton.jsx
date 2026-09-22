/**
 * A copy button that says it copied. Used by every code block the chat renders.
 */
import { useI18n } from '../../i18n';
import { Check, Copy } from 'lucide-react';
import { useState } from 'react';

// Markdown-ish renderer (no external deps)
// ---------------------------------------------------------------------------
function CopyButton({ text }) {
  const { t } = useI18n();
  const [copied, setCopied] = useState(false);
  const copy = () => {
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  };
  return (
    <button
      onClick={copy}
      className="absolute top-2 right-2 p-1 rounded text-gray-400 hover:text-gray-200 transition-colors"
      title={t('chat.copy')}
    >
      {copied ? <Check className="w-3.5 h-3.5" /> : <Copy className="w-3.5 h-3.5" />}
    </button>
  );
}

export { CopyButton };
