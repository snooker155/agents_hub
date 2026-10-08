/**
 * Shared building blocks of the Docs sections: code block, callout, headings, rich text, cards, walkthrough.
 */
import { ArrowRight, Check, Copy } from 'lucide-react';
import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useI18n } from '../../i18n';

export function CodeBlock({ children, label }) {
  const { t } = useI18n();
  const [copied, setCopied] = useState(false);
  const copy = () => {
    navigator.clipboard?.writeText(children).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  };
  return (
    <div className="relative group my-3">
      {label && <p className="text-[11px] font-semibold uppercase tracking-wider text-gray-400 mb-1">{label}</p>}
      <pre className="bg-gray-900 text-gray-100 rounded-xl p-4 text-xs overflow-x-auto leading-relaxed">
        <code>{children}</code>
      </pre>
      <button
        onClick={copy}
        className="absolute top-2 right-2 p-1.5 rounded-lg bg-white/10 text-gray-300 hover:bg-white/20 opacity-0 group-hover:opacity-100 transition-opacity"
        title={t('docs.copy')}
      >
        {copied ? <Check className="w-3.5 h-3.5 text-green-400" /> : <Copy className="w-3.5 h-3.5" />}
      </button>
    </div>
  );
}

export function Callout({ children, tone = 'info' }) {
  const tones = {
    info: 'bg-indigo-50 border-indigo-200 text-indigo-900',
    warn: 'bg-amber-50 border-amber-200 text-amber-900',
    tip: 'bg-emerald-50 border-emerald-200 text-emerald-900',
  };
  return <div className={`border rounded-xl px-4 py-3 text-sm my-3 ${tones[tone]}`}>{children}</div>;
}

export function H2({ children, id }) {
  return <h2 id={id} className="text-xl font-bold text-gray-900 mt-8 mb-3 first:mt-0">{children}</h2>;
}
export function H3({ children }) {
  return <h3 className="text-base font-semibold text-gray-900 mt-5 mb-2">{children}</h3>;
}
export function P({ children }) {
  return <p className="text-sm text-gray-600 leading-relaxed my-2">{children}</p>;
}

// Prose in this page comes from the locale files, so a paragraph has to be one
// translation key rather than a dozen fragments glued together by JSX (which is
// how half of it stopped following the language switcher). `Rich` renders the
// small inline vocabulary those strings are allowed to use:
//   `code`   **bold**   *emphasis*   [label](/route)   [label](https://…)
const RICH_TOKEN = /(\[[^\]]+\]\([^)\s]+\)|`[^`]+`|\*\*[^*]+\*\*|\*[^*]+\*)/g;

export function Rich({ children }) {
  const text = typeof children === 'string' ? children : String(children ?? '');
  return (
    <>
      {text.split(RICH_TOKEN).filter(Boolean).map((part, i) => {
        const link = /^\[([^\]]+)\]\(([^)\s]+)\)$/.exec(part);
        if (link) {
          const [, label, href] = link;
          return href.startsWith('http')
            ? <a key={i} className="text-indigo-600 underline" href={href} target="_blank" rel="noreferrer">{label}</a>
            : <Link key={i} className="text-indigo-600 underline" to={href}>{label}</Link>;
        }
        if (part.startsWith('`') && part.endsWith('`')) {
          return <code key={i} className="bg-gray-100 px-1 rounded text-[0.92em]">{part.slice(1, -1)}</code>;
        }
        if (part.startsWith('**') && part.endsWith('**')) return <strong key={i}>{part.slice(2, -2)}</strong>;
        if (part.startsWith('*') && part.endsWith('*')) return <em key={i}>{part.slice(1, -1)}</em>;
        return <span key={i}>{part}</span>;
      })}
    </>
  );
}

export function FeatureCard({ icon, title, to, children }) {
  const Icon = icon;
  const navigate = useNavigate();
  return (
    <button
      onClick={() => navigate(to)}
      className="text-left p-4 rounded-xl border border-gray-200 bg-white hover:border-indigo-300 hover:shadow-sm transition-all group"
    >
      <div className="flex items-center gap-2 mb-1.5">
        <Icon className="w-4 h-4 text-indigo-500" />
        <span className="font-semibold text-sm text-gray-900">{title}</span>
        <ArrowRight className="w-3.5 h-3.5 text-gray-300 group-hover:text-indigo-500 ml-auto transition-colors" />
      </div>
      <p className="text-xs text-gray-500 leading-relaxed">{children}</p>
    </button>
  );
}

export function Walkthrough({ title, steps }) {
  return (
    <div className="my-4 rounded-xl border border-gray-200 overflow-hidden">
      <div className="px-4 py-2.5 bg-gray-50 border-b border-gray-200 text-sm font-semibold text-gray-800">{title}</div>
      <ol className="divide-y divide-gray-100">
        {steps.map((s, i) => (
          <li key={i} className="flex gap-3 px-4 py-3">
            <span className="w-6 h-6 rounded-full bg-indigo-100 text-indigo-600 text-xs font-bold flex items-center justify-center shrink-0">
              {i + 1}
            </span>
            <div className="text-sm text-gray-600">{s}</div>
          </li>
        ))}
      </ol>
    </div>
  );
}
