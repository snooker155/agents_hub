/**
 * The background runs of a chat as data: loading them, where each is shown,
 * and its progress and work in words (BackgroundRuns.jsx draws them).
 */
import { useCallback, useEffect, useState } from 'react';
import { getChatBackground } from '../../api';
import { useLiveRefetch } from '../stream';
import { currentActivity } from './trail';
import { toolActivity } from './toolActivity';

/** The runs of the conversation, kept current as they change. */
export function useBackgroundRuns(conversationId) {
  // Kept with the conversation they belong to, so another one's never show.
  // A run that ended is kept after the server stops listing it (its agent
  // was woken): its card then stands above the notice of that turn, so the
  // replies of its nodes do not vanish when the agent's turn begins.
  const [state, setState] = useState({ id: null, runs: [] });
  const load = useCallback(async () => {
    if (!conversationId) return;
    try {
      const { data } = await getChatBackground(conversationId);
      const fresh = data?.runs || [];
      setState((prev) => {
        const ids = new Set(fresh.map((r) => r.run_id));
        // Gone from the list means ended and reported, however briefly it
        // was listed as ended (the wake usually claims it at once).
        const kept = (prev.id === conversationId ? prev.runs : [])
          .filter((r) => !ids.has(r.run_id))
          .map((r) => ({ ...r, finished: true, woken: true }));
        return { id: conversationId, runs: [...kept, ...fresh] };
      });
    } catch { /* keep what was shown */ }
  }, [conversationId]);
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- the loader is shared with the live refetch; it sets state after its await
    load();
  }, [load]);
  useLiveRefetch(load, {
    sources: [
      { type: 'flow_runs.changed' }, { type: 'loop_runs.changed' },
      { type: 'team_runs.changed' }, { type: 'chat_turns.changed' },
    ],
    debounceMs: 600,
    enabled: Boolean(conversationId),
  });
  return state.id === conversationId ? state.runs : EMPTY;
}

const EMPTY = [];

/** Where a run is shown in full. */
export function runPagePath(run) {
  const id = encodeURIComponent(run.entity_id || '');
  const rid = encodeURIComponent(run.run_id || '');
  if (run.kind === 'flow') return `/flows/${id}/runs/${rid}`;
  if (run.kind === 'loop') return `/loops/${id}/runs/${rid}`;
  return `/teams/${id}?run=${rid}`;
}

/** How far a run has got, in one line. */
export function progressText(t, run) {
  const p = run.progress || {};
  if (run.kind === 'flow') {
    return p.total ? t('chat.background.flowProgress', { done: p.done, total: p.total }) : '';
  }
  if (run.kind === 'loop') {
    const parts = [];
    if (p.iteration) {
      parts.push(p.max_iterations
        ? t('chat.background.loopIteration', { n: p.iteration, max: p.max_iterations })
        : t('chat.background.loopIterationOnly', { n: p.iteration }));
    }
    if (p.score != null) parts.push(t('chat.background.loopScore', { score: Math.round(p.score) }));
    if (p.pass?.total) parts.push(t('chat.background.flowProgress', { done: p.pass.done, total: p.pass.total }));
    return parts.join(' · ');
  }
  const parts = [];
  if (p.round) {
    parts.push(p.max_rounds
      ? t('chat.background.teamRound', { n: p.round, max: p.max_rounds })
      : t('chat.background.teamRoundOnly', { n: p.round }));
  }
  return parts.join(' · ');
}

/** The composer footer's line for one run: what was started and how far it got,
 * short ("Flow “Name” started, 0 of 4"). */
export function footerText(t, run) {
  const p = run.progress || {};
  const name = run.name || '';
  let step = null;
  if (run.kind === 'flow' && p.total) step = { done: p.done || 0, total: p.total };
  if (run.kind === 'loop' && p.iteration) step = { done: p.iteration, total: p.max_iterations || null };
  if (run.kind === 'team' && p.round) step = { done: p.round, total: p.max_rounds || null };
  const kind = ['flow', 'loop', 'team'].includes(run.kind) ? run.kind : 'flow';
  if (!step) return t(`chat.background.footer.${kind}`, { name });
  if (!step.total) return t(`chat.background.footer.${kind}Step`, { name, done: step.done });
  return t(`chat.background.footer.${kind}Progress`, { name, ...step });
}

/** The channels whose events are a run's agents at work (a loop's pass, not the loop's own). */
export function workChannels(run) {
  const channels = run.channels || [];
  return run.kind === 'loop' ? channels.filter((c) => c.startsWith('run:')) : channels;
}

export const turnKind = (run) => (run.kind === 'team' ? 'team' : 'flow');

/** What a turn is doing now, in words: its tool, a thought, its answer. */
export function activityText(t, turn) {
  const a = currentActivity({ ...turn, thinking_live: turn.thinking_live || '' });
  if (!a) return t('chat.background.working');
  if (a.kind === 'tool') return toolActivity(t, a.tool, a.input);
  if (a.kind === 'thinking') return t('chat.background.thinking');
  return t('chat.background.writing');
}
