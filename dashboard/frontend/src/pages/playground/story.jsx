import React, { useCallback, useEffect, useState } from 'react';
import {
  AlertTriangle, Check, Copy, Download, Loader, ScrollText, Sparkles,
} from 'lucide-react';
import { getSimStory, narrateSimStory } from '../../api';
import MarkdownRenderer from '../../components/MarkdownRenderer';
import { useChannel } from '../../components/stream';
import { useI18n } from '../../i18n';
import { isLiveStatus } from './status';

/**
 * The run, read as one text — twice, side by side.
 *
 * The transcript and the event log are both lists, and a list is how you
 * audit a run rather than how you read one. This is the reading: the whole
 * scenario as continuous prose, so it can be followed end to end, quoted, or
 * handed to somebody who was not watching.
 *
 * Two columns rather than a toggle, because the two texts are read *against*
 * each other:
 *   * left — the **chronicle**, compiled from the tick log. Every line is a
 *     template or something an agent literally said. Free, exact, and there
 *     while the run is still going.
 *   * right — the **retelling**, one model call over that chronicle. It reads
 *     better and it can be wrong, which is precisely why the thing it was
 *     made from stays open next to it instead of being hidden behind a tab.
 *
 * A retelling takes as long as a model takes, so it is written on screen: the
 * draft tails in a five-line window while it streams, and the moment the
 * finished text lands the draft is dropped and only the result remains. The
 * window is `flex-col-reverse` + `overflow-hidden`, which pins the newest line
 * to the bottom and lets everything older leave through the top — the same
 * five lines however the text wraps.
 */
export default function StoryPane({ runId, status, ticksDone = 0, heightClass = '' }) {
  const { t, language } = useI18n();
  const [chronicle, setChronicle] = useState('');
  const [narration, setNarration] = useState(null);
  const [loading, setLoading] = useState(true);
  const [narrating, setNarrating] = useState(false);
  const [draft, setDraft] = useState('');
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    if (!runId) {
      setLoading(false);
      return;
    }
    try {
      const { data } = await getSimStory(runId, language);
      setChronicle(data.chronicle || '');
      setNarration(data.narration?.text ? data.narration : null);
    } catch (e) {
      setError(e.response?.data?.detail || t('playground.story.loadFailed'));
    } finally {
      setLoading(false);
    }
  }, [runId, language, t]);

  // Recomposed as the run grows: the chronicle is a pure function of the
  // ticks, so a live run's reading is simply the newest one.
  useEffect(() => { load(); }, [load, ticksDone]);

  // The retelling as it is written. Same channel the page already follows for
  // ticks — a draft is one more thing happening to this run.
  useChannel(runId ? `sim:${runId}` : null, (ev) => {
    const data = ev?.data;
    if (data?.type === 'story_delta') setDraft(data.text || '');
    else if (data?.type === 'story_start') setDraft('');
    else if (data?.type === 'story_done') setDraft('');
  });

  const narrate = async () => {
    setNarrating(true);
    setError('');
    setDraft('');
    try {
      const { data } = await narrateSimStory(runId, language);
      setNarration(data.narration);
    } catch (e) {
      setError(e.response?.data?.detail || t('playground.story.narrateFailed'));
    } finally {
      setNarrating(false);
      setDraft('');
    }
  };

  // Nothing has been run yet: the same sentence the other two panes give,
  // rather than an empty document that reads as a failed load.
  if (!runId) {
    return (
      <p className="text-base text-gray-400 italic">
        {t('playground.runTheScenarioToInspect')}
      </p>
    );
  }

  if (loading) {
    return (
      <div className="flex items-center gap-2 text-sm text-gray-500 py-6">
        <Loader className="w-4 h-4 animate-spin" /> {t('common.loading')}
      </div>
    );
  }

  return (
    <div className={`flex flex-col min-h-0 ${heightClass}`}>
      {error && (
        <div className="shrink-0 mb-3 flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
          <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" /> {error}
        </div>
      )}

      {/* One row on a wide screen, stacked below it — two long texts side by
          side need the width, and narrower than that the chronicle simply
          comes first. */}
      <div className="flex-1 min-h-0 grid grid-cols-1 lg:grid-cols-2 gap-5 pb-4">
        <Column
          icon={ScrollText}
          title={t('playground.story.chronicle')}
          note={t('playground.story.compiled')}
          text={chronicle}
          filename={`${runId}-chronicle.md`}
          footer={isLiveStatus(status) && (
            <p className="mt-4 text-xs text-gray-400 italic">
              {t('playground.story.growing')}
            </p>
          )}
        />

        <Column
          icon={Sparkles}
          title={t('playground.story.narration')}
          note={narration
            ? t('playground.story.narratedBy', { model: narration.model || '—' })
            : t('playground.story.narrateHint')}
          text={narration?.text || ''}
          filename={`${runId}-narration.md`}
          action={(
            <button
              type="button" onClick={narrate}
              disabled={narrating || !chronicle}
              className="inline-flex items-center gap-1.5 px-2.5 py-1 text-xs font-semibold text-indigo-700 bg-indigo-50 border border-indigo-200 rounded-md hover:bg-indigo-100 disabled:opacity-50"
            >
              {narrating
                ? <Loader className="w-3.5 h-3.5 animate-spin" />
                : <Sparkles className="w-3.5 h-3.5" />}
              {narration ? t('playground.story.retell') : t('playground.story.narrate')}
            </button>
          )}
          /* While the model writes, the column is the draft; when it lands,
             the draft is gone and only the finished text is here. */
          body={narrating ? <Draft text={draft} /> : null}
          empty={t('playground.story.notRetoldYet')}
        />
      </div>
    </div>
  );
}


/** One of the two texts: a heading that says what it is and where it came
    from, its own copy/download, and the document itself on its own scroll. */
function Column({ icon: Icon, title, note, text, filename, action, body, footer, empty }) {
  const { t } = useI18n();
  const [copied, setCopied] = useState(false);

  const copy = () => {
    navigator.clipboard?.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    }, () => {});
  };

  const download = () => {
    const blob = new Blob([text], { type: 'text/markdown;charset=utf-8' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = filename;
    a.click();
    URL.revokeObjectURL(a.href);
  };

  return (
    <section className="flex flex-col min-h-0 min-w-0">
      <div className="shrink-0 flex items-center gap-2 pb-2 border-b border-gray-100">
        <Icon className="w-4 h-4 text-indigo-500 shrink-0" />
        <span className="text-xs font-bold text-gray-700 uppercase tracking-wide">{title}</span>
        <div className="ml-auto flex items-center gap-1.5">
          {action}
          {text && (
            <>
              <button
                type="button" onClick={copy} title={t('playground.story.copy')}
                className="p-1 text-gray-400 hover:text-indigo-600"
              >
                {copied
                  ? <Check className="w-3.5 h-3.5 text-emerald-600" />
                  : <Copy className="w-3.5 h-3.5" />}
              </button>
              <button
                type="button" onClick={download} title={t('playground.story.download')}
                className="p-1 text-gray-400 hover:text-indigo-600"
              >
                <Download className="w-3.5 h-3.5" />
              </button>
            </>
          )}
        </div>
      </div>
      <p className="shrink-0 text-[11px] text-gray-400 py-1.5">{note}</p>
      <div className="flex-1 min-h-0 overflow-y-auto pr-1">
        {body || (text
          ? (
            <div className="text-gray-800 leading-relaxed">
              <MarkdownRenderer content={text} />
              {footer}
            </div>
          )
          : <p className="text-sm text-gray-400 italic">{empty}</p>
        )}
      </div>
    </section>
  );
}


/**
 * The retelling being written — the last five lines of it, moving up.
 *
 * Reversed flex is what makes the window a tail: the newest line sits at the
 * bottom and everything older overflows past the top, where it is clipped. No
 * scrolling, no jumping, and the height stays five lines however the text
 * happens to wrap.
 */
function Draft({ text }) {
  const { t } = useI18n();
  return (
    <div className="rounded-lg border border-violet-100 bg-violet-50 p-3">
      <div className="flex items-center gap-2 text-xs font-semibold text-violet-700 mb-1.5">
        <Loader className="w-3.5 h-3.5 animate-spin" />
        {t('playground.story.writing')}
      </div>
      {/* Exactly five lines of text-sm at leading-relaxed. No scrollbar and
          no scrolling: the reversed column keeps the newest line on the floor
          of the box and lets the rest leave through the ceiling. */}
      <div className="flex flex-col-reverse overflow-hidden h-[7.1rem]">
        <div className="text-sm text-gray-700 whitespace-pre-wrap leading-relaxed break-words">
          {text || t('playground.story.waitingForModel')}
        </div>
      </div>
    </div>
  );
}
