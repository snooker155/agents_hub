import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Eye, Loader, Save, Sparkles, Wand2 } from 'lucide-react';
import { updateScenario } from '../../api';
import MarkdownRenderer from '../../components/MarkdownRenderer';
import { useToast } from '../../components/toast';
import { useI18n } from '../../i18n';

/**
 * The scenario's prose: the world as it should be *read*.
 *
 * A world spec says what can be acted on — rooms, items, the actions that move
 * them — and that is all it can say. It cannot say what the town is afraid of,
 * why the guild tolerates the thieves, or that nobody here says a name out loud
 * after dark. Deriving any of that from a location list is guesswork, and until
 * now the only place to put it was the one-line description, which is a caption.
 *
 * So this is a page rather than a field. Nothing in the loop reads it — the
 * characters are told what the environment tells them, and that stays true —
 * but every *reading* of a run starts here: it opens the chronicle, and it is
 * handed to the narrator as the setting it must not contradict. Write the
 * world's history and its rules here and the retelling stops having to invent
 * them.
 *
 * Editor and preview side by side, because it is Markdown and because the two
 * are read against each other while writing. The save banner is the setup
 * form's, down to the way it protects edits made while the build chat was also
 * writing: both surfaces store the same scenario.
 */
export default function NarrativePanel({
  scenario, onSaved, onAskAgent, className = '',
}) {
  const { t } = useI18n();
  const toast = useToast();
  const [text, setText] = useState(scenario.narrative || '');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [preview, setPreview] = useState(true);

  // The chat can rewrite the narrative while it is open in this editor. Adopt
  // the stored text when there is nothing unsaved to lose, and say so rather
  // than overwriting somebody mid-sentence when there is.
  const syncedRef = useRef(scenario.narrative || '');
  const [outOfSync, setOutOfSync] = useState(false);
  useEffect(() => {
    const stored = scenario.narrative || '';
    if (stored === syncedRef.current) return;
    const previous = syncedRef.current;
    syncedRef.current = stored;
    setText((current) => {
      if (current === previous) {
        setOutOfSync(false);
        return stored;
      }
      setOutOfSync(true);
      return current;
    });
  }, [scenario.narrative]);

  const dirty = text !== (scenario.narrative || '');
  const words = useMemo(
    () => (text.trim() ? text.trim().split(/\s+/).length : 0),
    [text],
  );

  const save = async () => {
    setSaving(true);
    setError('');
    try {
      const { data } = await updateScenario(scenario.scenario_id, {
        ...scenario, narrative: text,
      });
      syncedRef.current = data.narrative || '';
      setOutOfSync(false);
      onSaved(data);
      toast.success(t('playgroundNarrative.saved'));
    } catch (e) {
      setError(e.response?.data?.detail || t('playground.saveFailed'));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className={`flex flex-col gap-3 ${className}`}>
      {error && <div className="text-xs text-red-600 shrink-0">{error}</div>}

      {outOfSync && (
        <div className="shrink-0 flex items-center justify-between gap-3 rounded-lg border border-sky-200 bg-sky-50 px-3 py-2">
          <span className="text-xs text-sky-800">{t('playground.changedElsewhere')}</span>
          <button
            onClick={() => { setText(scenario.narrative || ''); setOutOfSync(false); }}
            className="text-xs font-semibold text-sky-700 hover:text-sky-900 underline"
          >
            {t('playground.discardAndReload')}
          </button>
        </div>
      )}

      {/* One line, and no heading of its own: the tab that opened this pane is
          its title, and what is left — what the text is for, how long it has
          got, and the two buttons — reads as a single strip above the page
          rather than as three stacked rows of chrome. */}
      <div className="shrink-0 flex items-center gap-3 flex-wrap">
        <p className="flex-1 min-w-[18rem] text-xs text-gray-500 flex items-start gap-1.5">
          <Sparkles className="w-3.5 h-3.5 mt-0.5 shrink-0 text-indigo-400" />
          <span>{t('playgroundNarrative.hint')}</span>
        </p>
        <span className="text-[11px] text-gray-400">
          {t('playgroundNarrative.wordCount', { count: words })}
        </span>
        {/* The scenario's own builder already has the tool that stores this
            field, so writing the world is one click rather than a chat to find
            and a request to phrase. What it writes lands in the stored
            scenario, which this editor adopts — or reports as a conflict, if
            there is something unsaved here to protect. */}
        {onAskAgent && (
          <button
            onClick={() => onAskAgent(
              text.trim()
                ? t('playgroundNarrative.askPromptExtend')
                : t('playgroundNarrative.askPrompt'),
            )}
            className="inline-flex items-center gap-1 px-2 py-1 rounded-md text-[11px] font-semibold border border-indigo-200 bg-indigo-50 text-indigo-700 hover:bg-indigo-100"
          >
            <Wand2 className="w-3.5 h-3.5" /> {t('playgroundNarrative.askAgent')}
          </button>
        )}
        <button
          onClick={() => setPreview((p) => !p)}
          className={`inline-flex items-center gap-1 px-2 py-1 rounded-md text-[11px] font-semibold border ${
            preview
              ? 'bg-indigo-50 border-indigo-200 text-indigo-700'
              : 'bg-white border-gray-200 text-gray-500 hover:text-gray-700'
          }`}
        >
          <Eye className="w-3.5 h-3.5" /> {t('playgroundNarrative.preview')}
        </button>
        {dirty && (
          <>
            <button onClick={() => setText(scenario.narrative || '')} disabled={saving}
                    className="text-xs font-semibold text-amber-800 underline disabled:opacity-50">
              {t('playground.discard')}
            </button>
            <button
              onClick={save} disabled={saving}
              className="inline-flex items-center px-3 py-1.5 text-xs font-semibold text-white bg-indigo-600 rounded-md hover:bg-indigo-700 disabled:opacity-50"
            >
              {saving ? <Loader className="w-3.5 h-3.5 mr-1 animate-spin" /> : <Save className="w-3.5 h-3.5 mr-1" />}
              {t('playgroundNarrative.save')}
            </button>
          </>
        )}
      </div>

      <div className={`flex-1 min-h-0 grid gap-4 ${preview ? 'grid-cols-1 2xl:grid-cols-2' : 'grid-cols-1'}`}>
        <div className="bg-gray-50 rounded-lg border border-gray-200 p-2 flex flex-col min-h-[18rem] 2xl:min-h-0">
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder={t('playgroundNarrative.placeholder')}
            spellCheck
            className="flex-1 w-full resize-none border-0 focus:ring-0 focus:outline-none text-sm leading-relaxed font-mono text-gray-800 bg-transparent p-2"
          />
        </div>

        {preview && (
          <div className="rounded-lg border border-gray-200 p-4 overflow-y-auto min-h-[18rem] 2xl:min-h-0">
            {text.trim() ? (
              <MarkdownRenderer content={text} />
            ) : (
              <div className="text-sm text-gray-400 italic">
                {t('playgroundNarrative.emptyPreview')}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
