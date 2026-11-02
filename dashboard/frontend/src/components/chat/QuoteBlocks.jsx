/**
 * Quotes as the chat shows them (quote.js): waiting in the composer, each with
 * its source and a remove button, and in the bubble of the message they went
 * with, a few lines each, the rest on a click.
 */
import { useState } from 'react';
import { MessageSquareQuote, X } from 'lucide-react';
import { useI18n } from '../../i18n';

/** The quotes attached to the next message. */
export function PendingQuotes({ quotes, onRemove, disabled = false }) {
  const { t } = useI18n();
  if (!quotes.length) return null;
  return (
    <div className="mb-2 space-y-1.5" data-testid="pending-quotes">
      {quotes.map((q) => (
        <div
          key={q.id}
          className="flex items-start gap-2 rounded-lg border border-indigo-200 border-l-4 border-l-indigo-400 bg-indigo-50 px-3 py-1.5"
        >
          <MessageSquareQuote className="mt-0.5 h-3.5 w-3.5 flex-shrink-0 text-indigo-400" />
          <div className="min-w-0 flex-1">
            <div className="truncate text-[11px] font-medium text-indigo-700">{q.quote_of}</div>
            <div className="line-clamp-2 whitespace-pre-wrap break-words font-mono text-[11px] leading-snug text-gray-600">
              {q.content}
            </div>
          </div>
          <button
            type="button"
            onClick={() => onRemove(q.id)}
            className="flex-shrink-0 text-indigo-300 hover:text-red-600"
            title={t('chat.removeAttachment')}
            disabled={disabled}
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      ))}
    </div>
  );
}

function SentQuote({ quote }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const long = quote.text.split('\n').length > 4 || quote.text.length > 320;
  return (
    <div className="rounded-md border-l-2 border-indigo-200 bg-white/10 px-2 py-1" data-testid="message-quote">
      <div className="flex items-center gap-1 text-[11px] text-indigo-100">
        <MessageSquareQuote className="h-3 w-3 flex-shrink-0" />
        <span className="truncate">{quote.source}</span>
      </div>
      <div className={`whitespace-pre-wrap break-words font-mono text-[11px] leading-snug text-indigo-50 ${open ? '' : 'line-clamp-4'}`}>
        {quote.text}
      </div>
      {long && (
        <button type="button" onClick={() => setOpen(!open)} className="text-[11px] text-indigo-100 underline">
          {open ? t('chat.quote.collapse') : t('chat.quote.showAll')}
        </button>
      )}
    </div>
  );
}

/** The quotes a sent message carried, above its text. */
export function MessageQuotes({ quotes, hasText = true }) {
  if (!quotes?.length) return null;
  return (
    <div className={`space-y-1 ${hasText ? 'mb-1.5' : ''}`}>
      {quotes.map((q, i) => <SentQuote key={i} quote={q} />)}
    </div>
  );
}
