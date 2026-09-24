import { useRef } from 'react';
import { GitBranch, Copy } from 'lucide-react';
import { useI18n } from '../i18n';
import { useToast } from './toast';

/**
 * The card a system loop result gets instead of a plain markdown render: it
 * is a diff a human still has to push and open a pull request for, so the
 * branch, the commit and the exact command to pull it locally come first,
 * above the rendered diff itself (see TaskDetails' ResultBlock).
 */
export default function SystemPatchCard({ meta }) {
  const { t } = useI18n();
  const toast = useToast();
  const commandRef = useRef(null);

  const branch = meta?.branch || '';
  const commit = meta?.commit || '';
  const shortCommit = commit ? String(commit).slice(0, 7) : '';
  const fetchCommand = meta?.fetch_command || '';

  const handleCopy = async () => {
    if (typeof navigator !== 'undefined' && navigator.clipboard && navigator.clipboard.writeText) {
      try {
        await navigator.clipboard.writeText(fetchCommand);
        toast.success(t('taskDetails.systemPatch.copied'));
        return;
      } catch {
        // Clipboard permission denied or unavailable: fall through to manual selection.
      }
    }
    if (commandRef.current) {
      commandRef.current.focus();
      commandRef.current.select();
    }
    toast.error(t('taskDetails.systemPatch.copyFailed'));
  };

  return (
    <div className="mb-3 rounded-lg border border-amber-200 bg-amber-50 p-3">
      <div className="flex items-center gap-2 mb-2">
        <GitBranch className="w-4 h-4 text-amber-700" />
        <span className="text-xs font-semibold text-amber-800 uppercase tracking-wide">
          {t('taskDetails.systemPatch.title')}
        </span>
      </div>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm mb-2">
        <span className="text-gray-600">
          {t('taskDetails.systemPatch.branch')}: <span className="font-mono text-gray-900">{branch || '—'}</span>
        </span>
        <span className="text-gray-600">
          {t('taskDetails.systemPatch.commit')}: <span className="font-mono text-gray-900">{shortCommit || '—'}</span>
        </span>
      </div>
      <label className="block text-xs text-gray-500 mb-1">{t('taskDetails.systemPatch.fetchCommand')}</label>
      <div className="flex items-stretch gap-2 mb-2">
        <textarea
          ref={commandRef}
          readOnly
          rows={1}
          value={fetchCommand}
          className="flex-1 resize-none overflow-x-auto rounded-md border border-amber-200 bg-white px-2 py-1.5 font-mono text-xs text-gray-800"
        />
        <button
          type="button"
          onClick={handleCopy}
          className="inline-flex items-center gap-1 px-2.5 py-1.5 text-xs font-semibold text-amber-800 bg-white border border-amber-200 rounded-md hover:bg-amber-100 shrink-0"
        >
          <Copy className="w-3.5 h-3.5" /> {t('taskDetails.systemPatch.copyCommand')}
        </button>
      </div>
      <p className="text-xs text-amber-700">{t('taskDetails.systemPatch.humanNote')}</p>
    </div>
  );
}
