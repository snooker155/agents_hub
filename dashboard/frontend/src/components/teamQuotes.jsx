import React from 'react';
import { useI18n } from '../i18n';
import { splitQuotes } from '../lib/teamQuotes';

/**
 * A team member's message with the outside text it quoted shown as quotes.
 *
 * Members put text they read outside the team between a `<<<QUOTE source>>>`
 * line and a `<<<END_QUOTE>>>` line (teams/prompts.py); the backend fences
 * those spans as data for the next member (common/untrusted.py). Here they
 * only need to read as quotes instead of raw markers.
 */

export default function QuotedText({ text, className = '' }) {
  const { t } = useI18n();
  const parts = splitQuotes(text);
  return (
    <div className={`text-sm text-gray-800 whitespace-pre-wrap ${className}`}>
      {parts.map((p, i) => (p.type === 'quote' ? (
        <blockquote key={i} className="my-1 border-l-2 border-gray-300 pl-3 text-gray-600">
          <span className="block text-[11px] font-semibold uppercase tracking-wide text-gray-400">
            {p.source ? t('teamDetails.quoteFrom', { source: p.source }) : t('teamDetails.quote')}
          </span>
          {p.text}
        </blockquote>
      ) : (
        <React.Fragment key={i}>{p.text.replace(/^\n+|\n+$/g, '')}</React.Fragment>
      )))}
    </div>
  );
}
