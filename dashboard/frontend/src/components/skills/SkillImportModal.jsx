/**
 * Import a skill written in the SKILL.md format (the one Claude Code and the
 * Agent Skills spec use): YAML frontmatter with a name and a description,
 * then the instructions. Pasted or picked as a file; the server parses it.
 * A folder of skills kept in a repository comes in through Sync instead.
 */
import { useState } from 'react';
import { FileUp, X } from 'lucide-react';
import { importSkillMarkdown } from '../../api/skillVersions';
import { useI18n } from '../../i18n';
import { errorDetail, useToast } from '../toast';

const MAX_BYTES = 256 * 1024;

export default function SkillImportModal({ workspace, targets = [], onClose, onImported }) {
  const { t } = useI18n();
  const toast = useToast();
  const [content, setContent] = useState('');
  const [agentId, setAgentId] = useState('');
  const [busy, setBusy] = useState(false);

  const pickFile = async (event) => {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (!file) return;
    if (file.size > MAX_BYTES) {
      toast.error(t('skillsCatalog.import.tooLarge'));
      return;
    }
    setContent(await file.text());
  };

  const submit = async () => {
    setBusy(true);
    try {
      const { data } = await importSkillMarkdown(workspace, content, agentId);
      toast.success(t('skillsCatalog.import.done', { name: data.name }));
      onImported?.(data);
      onClose();
    } catch (e) {
      toast.error(t('skillsCatalog.import.failed'), errorDetail(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4" role="dialog"
      aria-modal="true" aria-label={t('skillsCatalog.import.title')}>
      <div className="bg-white rounded-xl shadow-xl w-full max-w-2xl max-h-[90vh] overflow-y-auto">
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-100">
          <h3 className="text-lg font-bold text-gray-800 flex items-center gap-2">
            <FileUp className="w-5 h-5 text-indigo-600" /> {t('skillsCatalog.import.title')}
          </h3>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600" aria-label={t('common.close')}>
            <X className="w-5 h-5" />
          </button>
        </div>
        <div className="p-6 space-y-4">
          <p className="text-xs text-gray-500">{t('skillsCatalog.import.hint')}</p>
          <label className="inline-flex items-center gap-2 text-xs font-semibold text-indigo-600 cursor-pointer">
            <FileUp className="w-3.5 h-3.5" /> {t('skillsCatalog.import.pickFile')}
            <input type="file" accept=".md,text/markdown,text/plain" className="hidden" onChange={pickFile} />
          </label>
          <textarea
            value={content}
            onChange={(e) => setContent(e.target.value)}
            rows={14}
            aria-label={t('skillsCatalog.import.content')}
            placeholder={'---\nname: release-notes\ndescription: Use when writing release notes.\n---\n\n# Release notes\n...'}
            className="w-full px-3 py-2 border border-gray-200 rounded-lg text-xs font-mono focus:outline-none"
          />
          <div>
            <label className="block text-xs font-semibold text-gray-600 mb-1">{t('skillsCatalog.attachToAgent')}</label>
            <select
              value={agentId}
              onChange={(e) => setAgentId(e.target.value)}
              className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm focus:outline-none"
            >
              <option value="">{t('skillsCatalog.catalogEntryNoAgent')}</option>
              {targets.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
            </select>
          </div>
        </div>
        <div className="flex justify-end gap-2 px-6 py-4 border-t border-gray-100">
          <button onClick={onClose}
            className="px-4 py-2 text-sm font-semibold border border-gray-200 text-gray-600 rounded-lg hover:bg-gray-50">
            {t('skillsCatalog.cancel')}
          </button>
          <button onClick={submit} disabled={busy || !content.trim()}
            className="px-4 py-2 text-sm font-semibold bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 disabled:opacity-50">
            {t('skillsCatalog.import.submit')}
          </button>
        </div>
      </div>
    </div>
  );
}
