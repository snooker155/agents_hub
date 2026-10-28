/**
 * One agent result of a task with a rendered and raw view toggle.
 */
import { Bot, Code2, Eye, FileText } from 'lucide-react';
import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import MarkdownRenderer from '../MarkdownRenderer';
import SystemPatchCard from '../SystemPatchCard';
import { parseSystemPatch } from '../systemPatch';
import { useI18n } from '../../i18n';

// One agent result in a neutral frame of its own: a header line with who
// wrote it (and on which subtask), when, the run and a small view switch,
// then the text, and last the files the run created or changed, each
// opening in the Files tab (`onOpenFile`). No tint, so agents are told apart
// by the frame alone.
function ResultBlock({ entry, onOpenFile }) {
  const { t } = useI18n();
  const [view, setView] = useState('rendered');
  const text = String(entry.result ?? '');
  // A result the system loop wrote leads with a machine readable marker line;
  // the card built from it goes above the diff, and the marker itself is
  // stripped so it never renders as literal markdown text.
  const patch = useMemo(() => parseSystemPatch(text), [text]);
  const renderedText = patch ? patch.body : text;
  const when = entry.updated_at || entry.timestamp;
  const viewButton = (id, Icon, label) => (
    <button
      type="button"
      onClick={() => setView(id)}
      aria-pressed={view === id}
      className={`inline-flex items-center gap-1 px-2 py-0.5 rounded transition-colors ${
        view === id ? 'bg-white text-gray-800 shadow-sm' : 'text-gray-500 hover:text-gray-700'
      }`}
    >
      <Icon className="w-3.5 h-3.5" /> {label}
    </button>
  );
  return (
    <section className="border border-gray-200 rounded-lg overflow-hidden">
      <div className="flex items-center gap-2 px-4 py-2 min-w-0 flex-wrap bg-gray-50 border-b border-gray-200">
        <Bot className="w-4 h-4 text-gray-400 shrink-0" />
        <span className="text-sm font-semibold text-gray-800">{entry.agent_id || 'Agent'}</span>
        {entry.subtask && (
          <span className="text-sm text-gray-500 truncate min-w-0" title={entry.subtask.title}>
            {entry.subtask.key ? `${entry.subtask.key} · ` : ''}{entry.subtask.title}
          </span>
        )}
        {when && (
          <span className="text-xs text-gray-400">{new Date(when).toLocaleString()}</span>
        )}
        <div className="ml-auto flex items-center gap-3">
          {entry.run_id && (
            // The run's own page: its input, steps, tools and tokens.
            <Link
              to={`/messages/${entry.run_id}`}
              title={entry.run_id}
              className="text-xs font-mono text-indigo-600 hover:text-indigo-700 hover:underline"
            >
              {entry.run_id.slice(0, 8)}
            </Link>
          )}
          <div className="flex items-center gap-0.5 rounded-md bg-gray-100 p-0.5 text-xs font-medium">
            {viewButton('rendered', Eye, t('taskDetails.rendered'))}
            {viewButton('raw', Code2, t('taskDetails.raw'))}
          </div>
        </div>
      </div>
      <div className="px-4 py-3">
        {view === 'rendered' ? (
          <div className="text-sm text-gray-800">
            {patch && <SystemPatchCard meta={patch.meta} />}
            <MarkdownRenderer content={renderedText} />
          </div>
        ) : (
          <pre className="text-xs text-gray-700 whitespace-pre-wrap break-words">{text}</pre>
        )}
      </div>
      {(entry.files || []).length > 0 && (
        <div className="px-4 py-2 border-t border-gray-100 flex items-center gap-1.5 flex-wrap" data-testid="result-files">
          <span className="text-xs text-gray-500">{t('taskDetails.changedFiles')}</span>
          {entry.files.map((path) => (
            <button
              key={path}
              type="button"
              onClick={() => onOpenFile?.(path)}
              disabled={!onOpenFile}
              title={path}
              className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-xs font-mono text-indigo-700 bg-indigo-50 border border-indigo-100 hover:bg-indigo-100 disabled:cursor-default max-w-[16rem]"
            >
              <FileText className="w-3 h-3 shrink-0" />
              <span className="truncate">{path}</span>
            </button>
          ))}
        </div>
      )}
    </section>
  );
}

export default ResultBlock;
