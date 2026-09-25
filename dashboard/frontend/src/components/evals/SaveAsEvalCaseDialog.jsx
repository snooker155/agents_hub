/**
 * "To eval case" on any run: an agent run or an entity run (flow, team, loop,
 * scenario). Loads which eval sets fit the run's target (GET
 * /evals/for-run/{run_id}) plus a preview of the case case_from_run would
 * build, lets the user pick or create a set and edit input/expected/rubric,
 * then saves through the usual POST /evals/{id}/cases with from_run_id.
 *
 * For a failed run `expected` starts empty (there is nothing right to repeat)
 * and the rubric field is framed as "what should have happened" instead of
 * an optional extra, since a failed case is graded on that, not on a match.
 */
import { useEffect, useState } from 'react';
import { FlaskConical, Loader, X } from 'lucide-react';
import { getEvalSetsForRun, createEvalSet, addEvalCase } from '../../api';
import { useI18n } from '../../i18n';

const KIND_LABEL_KEYS = {
  agent: 'evals.kind_agent', flow: 'evals.kind_flow', team: 'evals.kind_team',
  loop: 'evals.kind_loop', scenario: 'evals.kind_scenario',
};

export default function SaveAsEvalCaseDialog({ runId, workspace, onClose, onSaved }) {
  const { t } = useI18n();
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [runInfo, setRunInfo] = useState(null);
  const [evalSets, setEvalSets] = useState([]);
  const [previewError, setPreviewError] = useState('');
  const [setId, setSetId] = useState('');
  const [newSetName, setNewSetName] = useState('');
  const [input, setInput] = useState('');
  const [expected, setExpected] = useState('');
  const [rubric, setRubric] = useState('');
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState('');
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      setLoadError('');
      try {
        const { data } = await getEvalSetsForRun(runId);
        if (cancelled) return;
        setRunInfo(data.run);
        setEvalSets(data.eval_sets || []);
        setSetId(data.eval_sets?.[0]?.eval_set_id || '');
        if (data.preview) {
          setInput(data.preview.input || '');
          setExpected(data.preview.expected || '');
        } else {
          setPreviewError(data.preview_error || '');
        }
      } catch (err) {
        if (!cancelled) setLoadError(err.response?.data?.detail || t('evals.caseDialog.loadFailed'));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [runId, t]);

  const failed = Boolean(runInfo?.failed);
  const canSave = !loading && !saving && !previewError && input.trim()
    && (setId || newSetName.trim());

  const handleSave = async () => {
    if (!canSave || !runInfo) return;
    setSaving(true);
    setSaveError('');
    try {
      let targetSetId = setId;
      if (!targetSetId) {
        const { data: created } = await createEvalSet({
          name: newSetName.trim(),
          workspace: workspace || null,
          target: { kind: runInfo.target_kind, id: runInfo.target_id },
          graders: [{ kind: failed ? 'rubric' : 'substring', params: {}, weight: 1 }],
        });
        targetSetId = created.eval_set_id;
      }
      const { data } = await addEvalCase(targetSetId, {
        from_run_id: runId,
        input,
        expected: expected.trim() ? expected : null,
        rubric: rubric.trim() ? rubric : null,
      });
      setSaved(true);
      onSaved?.(data.eval_set, data.case);
    } catch (err) {
      setSaveError(err.response?.data?.detail || t('evals.caseDialog.saveFailed'));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4" data-testid="save-as-eval-case-dialog">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-lg max-h-[85vh] overflow-auto">
        <div className="flex items-center justify-between px-5 py-3 border-b border-gray-200 sticky top-0 bg-white">
          <h3 className="text-sm font-bold text-gray-900 flex items-center gap-1.5">
            <FlaskConical className="w-4 h-4 text-indigo-600" />
            {t('evals.caseDialog.title')}
          </h3>
          <button onClick={onClose} className="p-1 text-gray-400 hover:text-gray-700">
            <X className="w-4 h-4" />
          </button>
        </div>
        <div className="p-5 space-y-4">
          {loading ? (
            <div className="flex items-center gap-2 text-sm text-gray-500 py-6 justify-center">
              <Loader className="w-4 h-4 animate-spin" /> {t('evals.loading')}
            </div>
          ) : loadError ? (
            <p className="text-sm text-red-600">{loadError}</p>
          ) : (
            <>
              {runInfo && (
                <p className="text-xs text-gray-500">
                  {t(KIND_LABEL_KEYS[runInfo.target_kind] || 'evals.kind_agent')}
                  {runInfo.target_id ? ` · ${runInfo.target_id}` : ''}
                </p>
              )}
              {failed && (
                <div className="rounded-lg bg-amber-50 border border-amber-200 px-3 py-2 text-xs text-amber-800">
                  {t('evals.caseDialog.failedRunHint')}
                </div>
              )}
              {previewError && (
                <p className="text-xs text-red-600">{previewError}</p>
              )}

              <div>
                <label className="block text-xs font-bold text-gray-500 uppercase mb-1">
                  {t('evals.evalSets')}
                </label>
                <select
                  value={setId}
                  onChange={(e) => setSetId(e.target.value)}
                  className="w-full border border-gray-300 rounded-lg px-2 py-1.5 text-sm"
                >
                  <option value="">{t('evals.newEvalSet')}</option>
                  {evalSets.map((s) => (
                    <option key={s.eval_set_id} value={s.eval_set_id}>{s.name}</option>
                  ))}
                </select>
                {!setId && (
                  <input
                    value={newSetName}
                    onChange={(e) => setNewSetName(e.target.value)}
                    placeholder={t('messageDetails.newEvalSetName')}
                    className="mt-2 w-full border border-gray-300 rounded-lg px-2 py-1.5 text-sm"
                  />
                )}
              </div>

              <div>
                <label className="block text-xs font-bold text-gray-500 uppercase mb-1">{t('evals.input')}</label>
                <textarea
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  rows={3}
                  className="w-full border border-gray-300 rounded-lg px-2 py-1.5 text-sm font-mono"
                />
              </div>

              <div>
                <label className="block text-xs font-bold text-gray-500 uppercase mb-1">{t('evals.expected')}</label>
                <textarea
                  value={expected}
                  onChange={(e) => setExpected(e.target.value)}
                  rows={3}
                  placeholder={failed ? t('evals.caseDialog.expectedBlankFailed') : ''}
                  className="w-full border border-gray-300 rounded-lg px-2 py-1.5 text-sm font-mono"
                />
              </div>

              <div>
                <label className="block text-xs font-bold text-gray-500 uppercase mb-1">
                  {failed ? t('evals.caseDialog.rubricFailedLabel') : t('evals.rubric')}
                </label>
                <textarea
                  value={rubric}
                  onChange={(e) => setRubric(e.target.value)}
                  rows={2}
                  className="w-full border border-gray-300 rounded-lg px-2 py-1.5 text-sm"
                />
              </div>

              {saveError && <p className="text-xs text-red-600">{saveError}</p>}
              {saved && <p className="text-xs text-green-600">{t('messageDetails.savedAsEvalCase')}</p>}

              <div className="flex justify-end gap-2 pt-2">
                <button onClick={onClose} className="px-3 py-2 text-sm font-semibold text-gray-600 hover:text-gray-900">
                  {t('evals.cancel')}
                </button>
                <button
                  onClick={handleSave}
                  disabled={!canSave}
                  className="inline-flex items-center px-4 py-2 text-sm font-semibold text-white bg-indigo-600 rounded-lg hover:bg-indigo-700 disabled:opacity-50"
                >
                  {saving ? <Loader className="w-4 h-4 mr-1.5 animate-spin" /> : <FlaskConical className="w-4 h-4 mr-1.5" />}
                  {t('evals.caseDialog.save')}
                </button>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
