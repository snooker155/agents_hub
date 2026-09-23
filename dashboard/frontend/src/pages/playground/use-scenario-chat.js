import { useCallback, useMemo } from 'react';
import {
  getScenarioChat, clearScenarioChat, stopScenarioChat, scenarioChatUrl,
} from '../../api';
import { useI18n } from '../../i18n';

/**
 * The scenario's build chat, as one descriptor.
 *
 * A descriptor rather than wiring inside the sidebar, because the same
 * conversation has two homes — the sidebar's Chat tab, and the floating page
 * chat — and two copies of it would be two threads about one scenario.
 *
 * The callbacks are memoised on the scenario id on purpose: EntityChat loads
 * its transcript in an effect keyed on them, so fresh closures every render
 * would refetch the conversation on every keystroke.
 */
export function useScenarioChatDescriptor(scenarioId, envSpec, onScenarioChanged, registerSend) {
  const { t } = useI18n();

  const loadChat = useCallback(() => getScenarioChat(scenarioId), [scenarioId]);
  const clearChat = useCallback(() => clearScenarioChat(scenarioId), [scenarioId]);
  const stopChat = useCallback(() => stopScenarioChat(scenarioId), [scenarioId]);

  // The turn ends with the scenario as it now stands, so the form beside the
  // chat updates without a refetch.
  const onEvent = useCallback((ev) => {
    if (ev.type === 'scenario' && ev.scenario) onScenarioChanged(ev.scenario);
  }, [onScenarioChanged]);

  // What the world is made of is a per-environment question: a market has no
  // locations to walk between and no items to hand over, so those two openers
  // are offered only where the environment actually has a knob for them.
  const paramNames = useMemo(
    () => new Set((envSpec?.params || []).map((p) => String(p.name || ''))),
    [envSpec],
  );

  return useMemo(() => (scenarioId ? {
    scope: `scenario:${scenarioId}`,
    path: scenarioChatUrl(scenarioId),
    loadChat, clearChat, stopChat, onEvent, registerSend,
    title: t('playground.scenarioChat'),
    emptyHint: t('playground.scenarioChatHint'),
    suggestions: [
      t('playground.chatSuggestAddCharacter'),
      ...(paramNames.has('locations') ? [t('playground.chatSuggestAddLocation')] : []),
      ...(paramNames.has('starting_items') ? [t('playground.chatSuggestAddItem')] : []),
      t('playground.chatSuggestRetune'),
      t('playground.chatSuggestExplain'),
      // The chat can start the simulation too — behind an approval step, so the
      // opener asks for the estimate rather than for a run.
      t('playground.chatSuggestRun'),
    ],
  } : null), [scenarioId, loadChat, clearChat, stopChat, onEvent, registerSend, paramNames, t]);
}

export default useScenarioChatDescriptor;
