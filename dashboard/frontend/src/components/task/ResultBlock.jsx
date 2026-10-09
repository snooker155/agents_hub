/**
 * One agent result of a task with a rendered and raw view toggle.
 */
import { Code2, Eye } from 'lucide-react';
import { useMemo, useState } from 'react';
import MarkdownRenderer from '../MarkdownRenderer';
import SystemPatchCard from '../SystemPatchCard';
import { parseSystemPatch } from '../systemPatch';
import { useI18n } from '../../i18n';

// One agent result with its own Rendered / Raw view toggle.
function ResultBlock({ entry }) {
  const { t } = useI18n();
  const [view, setView] = useState('rendered');
  const text = String(entry.result ?? '');
  // A result the system loop wrote leads with a machine readable marker line;
  // the card built from it goes above the diff, and the marker itself is
  // stripped so it never renders as literal markdown text.
  const patch = useMemo(() => parseSystemPatch(text), [text]);
  const renderedText = patch ? patch.body : text;
  return (
    <div className="border border-indigo-100 rounded-lg p-3 bg-indigo-50">
      <div className="flex items-center gap-3 mb-2">
        <span className="text-xs font-medium text-indigo-600">{entry.agent_id || 'Agent'}</span>
        {entry.timestamp && (
          <span className="text-xs text-gray-400">{new Date(entry.timestamp).toLocaleString()}</span>
        )}
        <div className="ml-auto flex items-center gap-2">
          <div className="flex rounded-lg border border-indigo-200 overflow-hidden text-xs font-semibold">
            <button
              type="button"
              onClick={() => setView('rendered')}
              className={`inline-flex items-center gap-1 px-2.5 py-1 transition-colors ${view === 'rendered' ? 'bg-indigo-600 text-white' : 'bg-white text-gray-600 hover:bg-gray-50'}`}
            >
              <Eye className="w-3.5 h-3.5" /> {t('taskDetails.rendered')}
            </button>
            <button
              type="button"
              onClick={() => setView('raw')}
              className={`inline-flex items-center gap-1 px-2.5 py-1 transition-colors border-l border-indigo-200 ${view === 'raw' ? 'bg-indigo-600 text-white' : 'bg-white text-gray-600 hover:bg-gray-50'}`}
            >
              <Code2 className="w-3.5 h-3.5" /> {t('taskDetails.raw')}
            </button>
          </div>
          {entry.run_id && (
            <span className="text-xs text-gray-300 font-mono">{entry.run_id.slice(0, 8)}</span>
          )}
        </div>
      </div>
      {view === 'rendered' ? (
        <div className="bg-white rounded-md p-3 border border-indigo-100">
          {patch && <SystemPatchCard meta={patch.meta} />}
          <MarkdownRenderer content={renderedText} />
        </div>
      ) : (
        <pre className="text-xs text-gray-700 whitespace-pre-wrap">{text}</pre>
      )}
    </div>
  );
}

export default ResultBlock;
