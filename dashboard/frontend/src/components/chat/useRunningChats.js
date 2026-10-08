import { useCallback, useEffect, useState } from 'react';
import { getRunningChats } from '../../api';
import { useLiveResource, useStream } from '../stream';

/** How often the list is asked again with no event at all: a turn whose end
 *  was lost stops counting as running on the server after a while
 *  (common/live_runs.RUNNING_SILENCE_SECONDS), and nothing announces that. */
const FALLBACK_MS = 60000;

const sameSet = (a, b) => a.size === b.size && [...a].every((x) => b.has(x));

/**
 * The conversations being answered right now, whoever started the turn: this
 * tab, another tab or device, Telegram, an agent's inbox. The server keeps
 * them (GET /api/chats/running) and says when the set changes
 * (`chat_turns.changed` on the app channel); this follows both.
 *
 * @returns {Set<string>} conversation ids.
 */
export function useRunningChats() {
  const { onRefetch } = useStream();
  const [running, setRunning] = useState(() => new Set());

  const load = useCallback(() => {
    getRunningChats()
      .then(({ data }) => {
        const next = new Set(data?.conversations || []);
        setRunning((prev) => (sameSet(prev, next) ? prev : next));
      })
      .catch(() => { /* keep the last answer */ });
  }, []);

  useLiveResource(load, { type: 'chat_turns.changed' });
  // Events missed while the stream was down or behind.
  useEffect(() => onRefetch(load), [onRefetch, load]);
  useEffect(() => {
    const id = setInterval(load, FALLBACK_MS);
    return () => clearInterval(id);
  }, [load]);

  return running;
}

export default useRunningChats;
