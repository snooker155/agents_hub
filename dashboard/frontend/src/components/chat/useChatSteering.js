import { useCallback, useEffect, useRef, useState } from 'react';
import { steerRun } from '../../api/steering';
import {
  availableModes, buildSteerBubble, effectiveMode, inFlightRunId, insertSteerBubble,
  loadSteerMode, nextTurnText, readLocalSteerMode, saveSteerMode, takeQueuedSteers,
} from './steering';
import { genId } from './turnState';

/**
 * The composer's side of steering (see ./steering.js): which mode a message
 * sent mid-turn uses, sending it, the queue of messages waiting for the turn
 * to end, and sending that queue as the next turn once it has.
 *
 * Reads the Chat page's own state (`page` is the page context), so the page
 * itself needs no wiring beyond what it already publishes: `loading` says a
 * turn is running, the last bubble's `run_id` says which run to talk to.
 */
export function useChatSteering(page) {
  const {
    conversations, currentConvId, input, loading, sendMessage, setConversations,
    setInput, stopGeneration, targetMode,
  } = page;
  const [mode, setModeState] = useState(readLocalSteerMode);
  // Messages that wait for the turn to end: `{id, convId, text, interrupt}`.
  const [queue, setQueue] = useState([]);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const stoppedByUser = useRef(false);
  const wasLoading = useRef(loading);

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
  const runId = inFlightRunId(conv?.messages, { loading, targetMode });
  const modes = availableModes({ loading, runId, targetMode });
  const activeMode = effectiveMode(mode, modes);
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
      if (err?.response?.status === 409 && sent === 'inject') enqueue(body);
      else {
        setInput(body);
        const detail = err?.response?.data?.detail;
        setError((detail && (detail.message || detail)) || err?.message || '');
      }
    }
    return true;
  }, [activeMode, currentConvId, enqueue, input, runId, setConversations, setInput]);

  // Stop from the composer: whatever was queued goes back into the box
  // rather than out as a new turn nobody asked for any more.
  const stop = useCallback(() => {
    stoppedByUser.current = true;
    stopGeneration();
  }, [stopGeneration]);

  // Once a turn has ended: what waited for it goes out as the next turn, or
  // back into the box when the person stopped the turn themselves.
  const sendWhatWaited = useCallback(() => {
    const current = (conversations || []).find((c) => c.id === currentConvId);
    const { texts: steerTexts } = takeQueuedSteers(current?.messages);
    const mine = queue.filter((q) => q.convId === currentConvId);
    const text = nextTurnText({ queue: mine, steerTexts });
    const byUser = stoppedByUser.current;
    stoppedByUser.current = false;
    if (!text) return;
    if (steerTexts.length) {
      setConversations((prev) => prev.map((c) => (
        c.id !== currentConvId ? c : { ...c, messages: takeQueuedSteers(c.messages).messages }
      )));
    }
    setQueue((prev) => prev.filter((q) => q.convId !== currentConvId));
    if (byUser) {
      setInput([text, input].filter((s) => String(s || '').trim()).join('\n\n'));
      setNotice('restored');
      return;
    }
    // sendMessage clears the box; what the person has typed since stays.
    const typed = input;
    sendMessage(text);
    if (typed) setInput(typed);
  }, [conversations, currentConvId, input, queue, sendMessage, setConversations, setInput]);

  // The page's `loading` going false is the only sign a turn has ended, and
  // starting the next turn then is this effect's whole job: it runs once per
  // turn, on that edge, so the state it sets cannot cascade.
  useEffect(() => {
    const ended = wasLoading.current && !loading;
    wasLoading.current = loading;
    if (ended && currentConvId) sendWhatWaited(); // eslint-disable-line react-hooks/set-state-in-effect
  }, [loading, currentConvId, sendWhatWaited]);

  return {
    mode, setMode, modes, activeMode, runId, queued, removeQueued, steer, stop,
    error, notice, busy: Boolean(loading),
  };
}

export default useChatSteering;
