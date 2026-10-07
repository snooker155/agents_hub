import { useCallback, useEffect, useRef, useState } from 'react';
import { steerRun } from '../../api/steering';
import {
  availableModes, buildSteerBubble, effectiveMode, inFlightRunId, insertSteerBubble,
  loadSteerMode, nextTurnText, readLocalSteerMode, saveSteerMode, takeQueuedSteers,
} from './steering';

// The ways to talk to a turn this tab is not sending (`liveRunId`): nothing
// here learns when it ends, so nothing can wait for it.
const ELSEWHERE_MODES = ['inject', 'interrupt', 'system'];
import { genId } from './turnState';

/**
 * The composer's side of steering (see ./steering.js): which mode a message
 * sent mid-turn uses, sending it, the queue of messages waiting for the turn
 * to end, and sending that queue as the next turn once it has.
 *
 * Reads the Chat page's own state: `loading` says the open conversation's
 * turn is running, the last bubble's `run_id` says which run to talk to, and
 * `turns` (./useChatTurns.js) lists every conversation with a turn running in
 * this tab. A conversation's turn can end while another one is open; what
 * waited for it then goes to that conversation as its next turn
 * (`sendMessage(text, {convId})`). Without `turns`, the open conversation's
 * `loading` is the only turn there is.
 *
 * `liveRunId` is a turn running in the open conversation that this tab is
 * not sending: another tab's or device's, or this tab's own from before the
 * Chat page was left and opened again. It can be steered too; an interrupt
 * asks the server to send the message as the next turn, since this tab will
 * not see the turn end.
 *
 * Called by the page rather than by the composer, which is not rendered for
 * every conversation: the queue has to outlive a switch to one without it.
 */
export function useChatSteering(page) {
  const {
    conversations, currentConvId, input, liveRunId = null, loading, sendMessage,
    setConversations, setInput, stopGeneration, targetMode, turns,
  } = page;
  const [mode, setModeState] = useState(readLocalSteerMode);
  // Messages that wait for the turn to end: `{id, convId, text, interrupt}`.
  const [queue, setQueue] = useState([]);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  // Conversations whose turn the person stopped from the composer.
  const stoppedByUser = useRef(new Set());
  // The conversations with a turn running, as one string so the effect below
  // runs when the set changes and not on every new object.
  const runningKey = turns
    ? Object.keys(turns).sort().join('\n')
    : (loading && currentConvId ? currentConvId : '');
  const wasRunning = useRef(runningKey);

  // A choice made before the account's answer arrives wins over it.
  const chosenHere = useRef(false);
  useEffect(() => {
    let live = true;
    loadSteerMode().then((m) => { if (live && !chosenHere.current) setModeState(m); }).catch(() => {});
    return () => { live = false; };
  }, []);

  const setMode = useCallback((m) => {
    chosenHere.current = true;
    setModeState(m);
    saveSteerMode(m);
  }, []);

  const conv = (conversations || []).find((c) => c.id === currentConvId);
  const elsewhere = !loading && Boolean(liveRunId);
  const runId = elsewhere ? liveRunId : inFlightRunId(conv?.messages, { loading, targetMode });
  const modes = elsewhere ? ELSEWHERE_MODES : availableModes({ loading, runId, targetMode });
  const activeMode = elsewhere
    ? (modes.includes(mode) ? mode : 'inject')
    : effectiveMode(mode, modes);
  const queued = queue.filter((q) => q.convId === currentConvId);

  const enqueue = useCallback((text, interrupt = false) => {
    const item = { id: genId(), convId: currentConvId, text, interrupt };
    setQueue((prev) => (interrupt ? [item, ...prev] : [...prev, item]));
  }, [currentConvId]);

  const removeQueued = useCallback((id) => {
    setQueue((prev) => prev.filter((q) => q.id !== id));
  }, []);

  /** Send `text` (default: the box) into the running turn with `chosen`. */
  const steer = useCallback(async (text = input, chosen = activeMode) => {
    const body = String(text || '').trim();
    if (!body || !chosen) return false;
    setError('');
    setNotice('');
    setInput('');
    if (chosen === 'queue' || !runId) {
      enqueue(body);
      return true;
    }
    if (chosen === 'interrupt' && elsewhere) {
      try {
        await steerRun(runId, body, 'interrupt', { send: true });
      } catch (err) {
        setInput(body);
        const detail = err?.response?.data?.detail;
        setError((detail && (detail.message || detail)) || err?.message || '');
      }
      return true;
    }
    if (chosen === 'interrupt') {
      // Queued first so the message goes out even if the turn ends on its
      // own before the stop lands.
      enqueue(body, true);
      try {
        await steerRun(runId, body, 'interrupt');
      } catch (err) {
        if (err?.response?.status !== 409) {
          setError(err?.response?.data?.detail?.message || err?.response?.data?.detail || err?.message || '');
        }
      }
      return true;
    }
    const sent = chosen === 'system' ? 'system' : 'inject';
    try {
      const { data } = await steerRun(runId, body, sent);
      const msgId = data?.message?.msg_id;
      setConversations((prev) => prev.map((c) => (
        c.id !== currentConvId ? c
          : { ...c, messages: insertSteerBubble(c.messages, buildSteerBubble({ text: body, msgId, mode: sent })) }
      )));
    } catch (err) {
      // The turn ended between the key press and the post: the message is
      // not lost, it waits for the next turn like a queued one. An
      // instruction has no next turn to wait for: it goes back in the box.
      // A turn sent elsewhere has no queue here to wait in: the message
      // starts the next turn now.
      if (err?.response?.status === 409 && sent === 'inject') {
        if (elsewhere) sendMessage(body);
        else enqueue(body);
      } else {
        setInput(body);
        const detail = err?.response?.data?.detail;
        setError((detail && (detail.message || detail)) || err?.message || '');
      }
    }
    return true;
  }, [activeMode, currentConvId, elsewhere, enqueue, input, runId, sendMessage, setConversations, setInput]);

  // Stop from the composer: whatever was queued goes back into the box
  // rather than out as a new turn nobody asked for any more.
  const stop = useCallback(() => {
    if (currentConvId) stoppedByUser.current.add(currentConvId);
    stopGeneration();
  }, [currentConvId, stopGeneration]);

  // Once a conversation's turn has ended: what waited for it goes out as that
  // conversation's next turn, or back into the box when the person stopped
  // the turn themselves.
  const sendWhatWaited = useCallback((convId) => {
    const here = convId === currentConvId;
    const conv = (conversations || []).find((c) => c.id === convId);
    const { texts: steerTexts } = takeQueuedSteers(conv?.messages);
    const mine = queue.filter((q) => q.convId === convId);
    const text = nextTurnText({ queue: mine, steerTexts });
    const byUser = stoppedByUser.current.has(convId);
    stoppedByUser.current.delete(convId);
    if (!text) return;
    // Stopped, and the person has left the conversation since: it stays
    // queued there rather than going out as a turn nobody asked for.
    if (byUser && !here) return;
    if (steerTexts.length) {
      setConversations((prev) => prev.map((c) => (
        c.id !== convId ? c : { ...c, messages: takeQueuedSteers(c.messages).messages }
      )));
    }
    setQueue((prev) => prev.filter((q) => q.convId !== convId));
    if (byUser) {
      setInput([text, input].filter((s) => String(s || '').trim()).join('\n\n'));
      setNotice('restored');
      return;
    }
    if (!here) {
      sendMessage(text, { convId });
      return;
    }
    // sendMessage clears the box; what the person has typed since stays.
    const typed = input;
    sendMessage(text);
    if (typed) setInput(typed);
  }, [conversations, currentConvId, input, queue, sendMessage, setConversations, setInput]);

  // A conversation leaving the running set is the only sign its turn has
  // ended, and starting its next turn then is this effect's whole job: it
  // runs once per ended turn, on that edge, so the state it sets cannot
  // cascade.
  useEffect(() => {
    const before = wasRunning.current ? wasRunning.current.split('\n') : [];
    wasRunning.current = runningKey;
    const now = new Set(runningKey ? runningKey.split('\n') : []);
    for (const convId of before) {
      if (!now.has(convId)) sendWhatWaited(convId); // eslint-disable-line react-hooks/set-state-in-effect
    }
  }, [runningKey, sendWhatWaited]);

  return {
    mode, setMode, modes, activeMode, runId, queued, removeQueued, steer, stop,
    error, notice, busy: Boolean(loading || elsewhere),
  };
}

export default useChatSteering;
