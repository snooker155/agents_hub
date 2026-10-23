// Pure helpers for the History list and the log stream: timestamp formatting,
// the run-status palette, and rebuilding chat bubbles from a run's flow-log
// events. Kept separate from the components that use them (FlowHistory.jsx,
// FlowLog.jsx) so those files export components only.

export function fmtTime(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
}

export function fmtDateTime(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  return d.toLocaleString([], {
    month: 'short', day: 'numeric',
    hour: '2-digit', minute: '2-digit', hour12: false,
  });
}

export const RUN_STATUS_STYLES = {
  completed: { dot: 'bg-emerald-500', text: 'text-emerald-600', label: 'Completed' },
  stopped: { dot: 'bg-rose-500', text: 'text-rose-600', label: 'Stopped' },
  running: { dot: 'bg-amber-500 animate-pulse', text: 'text-amber-600', label: 'Running' },
  failed: { dot: 'bg-rose-500', text: 'text-rose-600', label: 'Failed' },
  awaiting_input: { dot: 'bg-violet-500', text: 'text-violet-600', label: 'Waiting for a person' },
};

// Reconstruct conversation bubbles from a History record's flow-log events.
// Each flow_start begins a turn; the first node's input holds the user message,
// and every node's terminal event (finish/error/stopped) becomes an agent bubble
// carrying that node's output. Bubble shape matches FlowChat's message objects so
// a chat record can be resumed there.
//
// A turn that ran the flow again from a node (flow/rerun.py) has no message of
// its own: its user bubble carries `rerun` ({node_id, label, mode}) instead,
// worded by rerunBubbleText. A chat turn says so on its flow_start (`turn`), a
// task run on the flow_resume line written just before it.
export function reconstructRunMessages(run) {
  const events = run?.events || [];
  const bubbles = [];
  let turnHasUser = false;
  let pendingRerun = null;
  const extractUserMessage = (input) => {
    const text = String(input || '');
    const marker = 'Latest user message:';
    const idx = text.lastIndexOf(marker);
    const tail = idx >= 0 ? text.slice(idx + marker.length) : text;
    // What the person wrote, without what rides after it: attached files,
    // and the step instruction a root node gets under its input.
    return tail.split('\n=== Attached files ===')[0].split('\n## Your specific task for this step:')[0].trim();
  };
  for (const ev of events) {
    if (ev.type === 'flow_resume' && ev.rerun) {
      pendingRerun = ev.rerun;
    } else if (ev.type === 'flow_start') {
      turnHasUser = false;
      const rerun = ev.turn?.rerun || pendingRerun;
      pendingRerun = null;
      if (rerun) {
        bubbles.push({ id: `${ev.timestamp}-u`, role: 'user', content: '', rerun });
        turnHasUser = true;
      }
    } else if (ev.type === 'agent_start') {
      if (!turnHasUser) {
        const userText = extractUserMessage(ev.input);
        if (userText) {
          bubbles.push({ id: `${ev.timestamp}-u`, role: 'user', content: userText });
        }
        turnHasUser = true;
      }
    } else if (ev.type === 'agent_finish' || ev.type === 'agent_error' || ev.type === 'agent_stopped') {
      bubbles.push({
        id: `${ev.timestamp}-${ev.node_id || 'a'}`,
        role: 'agent',
        agent_label: ev.agent_name || ev.agent_id || 'Agent',
        // The node's run: its steps and files are fetched from it (chat/runTrail.js).
        ...(ev.run_id ? { run_id: ev.run_id } : {}),
        content: ev.output || ev.content || '',
        error: ev.type !== 'agent_finish',
      });
    }
  }
  return bubbles;
}

// What a user bubble of a run again from a node says.
export function rerunBubbleText(t, rerun) {
  const node = rerun?.label || rerun?.node_id || '';
  return rerun?.mode === 'single'
    ? t('flowFlowChat.rerunSingle', { node })
    : t('flowFlowChat.rerunRest', { node });
}
