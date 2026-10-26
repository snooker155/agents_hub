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
// a chat record can be resumed there. Every bubble carries its turn's id
// (`turn_id`: the flow_start line's own, or its timestamp for an older
// record). A turn's summary (`flow_summary`, the Summary view) is a bubble
// marked `summary` at the end of the turn it names, wherever in the log it
// was written: one asked for after the fact comes after later turns.
//
// A turn that ran the flow again from a node (flow/rerun.py) has no message of
// its own: its user bubble carries `rerun` ({node_id, label, mode}) instead,
// worded by rerunBubbleText. A chat turn says so on its flow_start (`turn`), a
// task run on the flow_resume line written just before it.
export function reconstructRunMessages(run) {
  const events = run?.events || [];
  const bubbles = [];
  const summaries = new Map();
  let turnHasUser = false;
  let pendingRerun = null;
  let turnId = null;
  const push = (b) => bubbles.push(turnId ? { ...b, turn_id: turnId } : b);
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
      turnId = ev.turn_id || ev.timestamp || null;
      const rerun = ev.turn?.rerun || pendingRerun;
      pendingRerun = null;
      if (rerun) {
        push({ id: `${ev.timestamp}-u`, role: 'user', content: '', rerun });
        turnHasUser = true;
      }
    } else if (ev.type === 'agent_start') {
      if (!turnHasUser) {
        const userText = extractUserMessage(ev.input);
        if (userText) {
          push({ id: `${ev.timestamp}-u`, role: 'user', content: userText });
        }
        turnHasUser = true;
      }
    } else if (ev.type === 'flow_summary') {
      // A turn's summary (the flow chat's Summary view), placed below.
      const key = ev.turn_id || turnId;
      if (key) summaries.set(key, { id: `${ev.timestamp}-summary`, role: 'agent', summary: true, turn_id: key, content: ev.output || '' });
    } else if (ev.type === 'agent_finish' || ev.type === 'agent_error' || ev.type === 'agent_stopped') {
      // A switch's bubble is worded where it is shown (routeText), from the
      // branch it chose, not from its English log summary.
      const route = ev.type === 'agent_finish' && ev.route ? ev.route : null;
      push({
        id: `${ev.timestamp}-${ev.node_id || 'a'}`,
        role: 'agent',
        agent_label: ev.agent_name || ev.agent_id || 'Agent',
        // The node's run: its steps and files are fetched from it (chat/runTrail.js).
        ...(ev.run_id ? { run_id: ev.run_id } : {}),
        // The files its line names, linked at once (the trail adds the diffs).
        ...(Array.isArray(ev.files) && ev.files.length ? { files: ev.files } : {}),
        ...(route ? { route } : {}),
        content: route ? '' : ev.output || ev.content || '',
        error: ev.type !== 'agent_finish',
      });
    }
  }
  if (!summaries.size) return bubbles;
  // Each summary at the end of its turn.
  const out = [];
  bubbles.forEach((b, i) => {
    out.push(b);
    const next = bubbles[i + 1];
    if (b.turn_id && summaries.has(b.turn_id) && next?.turn_id !== b.turn_id) {
      out.push(summaries.get(b.turn_id));
      summaries.delete(b.turn_id);
    }
  });
  return out;
}

// What the bubble of a node that chose the next step says (a switch, a
// condition): the step or steps it sent the run to, and the value it went by
// (`key=value` from the node's summary, chat/flow_driver.py node_route).
export function routeText(t, route) {
  const detail = route?.detail;
  if (detail && Array.isArray(detail.routes)) return switchRoutesText(t, route, detail);
  if (detail && Array.isArray(detail.checked)) return switchText(t, route, detail);
  const names = (route?.targets || []).map((x) => `**${x.label || x.node_id}**`);
  const head = !names.length
    ? t('flowFlowChat.noBranch')
    : t(names.length === 1 ? 'flowFlowChat.branchChosen' : 'flowFlowChat.branchChosenMany', { targets: names.join(', ') });
  const on = String(route?.on || '').replace(/[`\r\n]+/g, ' ').trim();
  return on ? `${head}\n\n\`${on}\`` : head;
}

// A switch's decision in words (flow/entities/conditions detail): the key,
// the condition that held and where it led, or the ones that did not and
// the default step. The whole value is tested, so none of it is quoted.
function switchText(t, route, detail) {
  const cond = (c) => t(`flowFlowChat.switchOps.${c.op}`, {
    value: String(c.value ?? ''),
    defaultValue: `${c.op} ${String(c.value ?? '')}`.trim(),
  });
  const names = (route.targets || []).map((x) => `**${x.label || x.node_id}**`).join(', ');
  const key = detail.key || '';
  if ((detail.matched || []).length) {
    return t('flowFlowChat.switchMatched', { key, cond: detail.matched.map(cond).join(', '), targets: names });
  }
  const conds = (detail.checked || []).map(cond).join('; ');
  return detail.default && names
    ? t('flowFlowChat.switchDefault', { key, conds, targets: names })
    : t('flowFlowChat.switchNone', { key, conds });
}

// A switch's decision in words when its conditions name their own keys
// (detail.routes): the conditions of the route that held, joined by "and" or
// "or", and where it led; else that none held and the default step, if any.
function switchRoutesText(t, route, detail) {
  const names = (route.targets || []).map((x) => `**${x.label || x.node_id}**`).join(', ');
  const held = detail.routes.filter((r) => r.held);
  if (held.length) {
    const conds = held.map((r) => (r.conditions || [])
      .map((c) => t('flowFlowChat.switchCond', {
        key: c.key,
        cond: t(`flowFlowChat.switchOps.${c.op}`, {
          value: String(c.value ?? ''),
          defaultValue: `${c.op} ${String(c.value ?? '')}`.trim(),
        }),
      }))
      .join(` ${t(r.match === 'any' ? 'flowFlowChat.switchOr' : 'flowFlowChat.switchAnd')} `))
      .join('; ');
    return t('flowFlowChat.switchRouteMatched', { conds, targets: names });
  }
  return detail.default && names
    ? t('flowFlowChat.switchRouteDefault', { targets: names })
    : t('flowFlowChat.switchRouteNone');
}

// What the bubble of a node with no agent says once it is done: the branch a
// switch chose, or the node's own summary (a transform's written keys).
export function entityNodeText(t, event) {
  if (event?.route) return routeText(t, event.route);
  return event?.summary || '';
}

// What a user bubble of a run again from a node says.
export function rerunBubbleText(t, rerun) {
  const node = rerun?.label || rerun?.node_id || '';
  return rerun?.mode === 'single'
    ? t('flowFlowChat.rerunSingle', { node })
    : t('flowFlowChat.rerunRest', { node });
}
