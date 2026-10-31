/**
 * The team board read as a chat, and the member turns read as a run log.
 *
 * The board (teams/runner.py `_Board`) is a list of messages with a kind:
 * the request, the lead's instructions, members' replies, errors, the
 * closing answer. The team page draws it the way every other chat in the hub
 * is drawn (chat/AgentTranscript.jsx), so each entry becomes a message of the
 * Chat page's shape here; the member turns (GET /teams/runs/{id}/turns) are
 * what the participants column and the run log are built from.
 */
import { splitQuotes } from '../../lib/teamQuotes';
import { buildAttachmentLine } from '../chat/send/buildRequest';

/** The sender of the closing answer when the team has no lead. */
export const TEAM_SENDER = '(team)';

// What a run had attached, named under its request as the Chat page's
// bubble names a message's attachments.
export function attachedLine(t, attached) {
  const list = attached || [];
  return buildAttachmentLine(t, {
    pendingAttachments: list.filter((a) => a.kind === 'file').map((a) => ({ filename: a.label })),
    pendingReferences: list.filter((a) => a.kind !== 'file').map((a) => ({ label: a.label })),
  });
}

/**
 * A member's message with the outside text it quoted (`<<<QUOTE source>>>`
 * … `<<<END_QUOTE>>>`, teams/prompts.py) as Markdown quotes, so the chat's
 * Markdown draws them as quotes instead of raw markers.
 */
export function quotesToMarkdown(text, t) {
  return splitQuotes(text).map((p) => {
    if (p.type !== 'quote') return p.text;
    const head = p.source ? t('teamDetails.quoteFrom', { source: p.source }) : t('teamDetails.quote');
    const body = (p.text || '').split('\n').map((line) => `> ${line}`).join('\n');
    return `\n\n> *${head}*\n>\n${body}\n\n`;
  }).join('').trim();
}

const toWhom = (recipients) => (recipients || []).filter((r) => r && r !== '*');

/**
 * The board as chat messages: the request is the person's message, every
 * member's reply is an agent message under the member's name, the lead's
 * instructions of one round are one message naming who got what, and a round
 * opens with a quiet line. A message keeps the run that wrote it (`run_id`),
 * so its steps, files and views fill in from the run's trail.
 *
 * `working` are the turns still running: each stands in as a reply being
 * written under its member's name, without a run id (its trail is not
 * final yet, chat/runTrail.js keeps a trail once fetched). Its steps so far
 * come from `trails` (liveTrails.js), the ones the run log shows, so the
 * reply says what the member is doing and the Build view lists every step.
 */
export function boardChatMessages({ run, messages, working = [], trails = {}, t }) {
  const out = [];
  let round = 0;
  for (const m of messages || []) {
    if (m.kind !== 'goal' && m.round > round) {
      round = m.round;
      out.push({ id: `round:${round}`, role: 'system', content: t('teamDetails.roundN', { n: round }) });
    }
    const id = `m:${m.seq}`;
    const recipients = toWhom(m.recipients);
    if (m.kind === 'goal') {
      out.push({ id, role: 'user', content: [m.content, attachedLine(t, run?.attached)].filter(Boolean).join('\n') });
    } else if (m.kind === 'steer') {
      out.push({ id, role: 'user', content: m.content });
    } else if (m.kind === 'system') {
      // The first round of a request the person addressed with @ (teams/runner.py _run_addressed).
      const addressed = run?.addressed_to && m.round === 1;
      out.push({
        id, role: 'system',
        content: recipients.length
          ? t(addressed ? 'teamDetails.requestAddressedTo' : 'teamDetails.requestHandedTo', { name: recipients.join(', ') })
          : m.content,
      });
    } else if (m.kind === 'instruction') {
      const line = `**→ ${recipients.join(', ')}:** ${quotesToMarkdown(m.content, t)}`;
      const last = out[out.length - 1];
      if (last?.kind === 'instruction' && last.sender === m.sender && last.run_id === m.run_id) {
        last.content = `${last.content}\n\n${line}`;
      } else {
        out.push({ id, role: 'agent', kind: 'instruction', sender: m.sender, run_id: m.run_id || null, content: line });
      }
    } else if (m.kind === 'error') {
      out.push({
        id, role: 'agent', kind: 'error', sender: m.sender, run_id: m.run_id || null, error: true,
        content: m.error ? t('teamDetails.noContribution', { error: m.error }) : m.content,
      });
    } else {
      const head = recipients.length ? `**→ ${recipients.join(', ')}**\n\n` : '';
      out.push({
        id, role: 'agent', kind: m.kind, sender: m.sender, run_id: m.run_id || null,
        content: `${head}${quotesToMarkdown(m.content, t)}`,
      });
    }
  }
  for (const turn of working) {
    const trail = trails[String(turn.run_id)];
    // A streamed turn (components/runs/liveTurns.js) also has the text being
    // written and the thought under way; a polled trail only the steps.
    const streamed = trail?.turn;
    out.push({
      id: `live:${turn.run_id}`, role: 'agent', kind: 'working', sender: turn.speaker,
      content: streamed?.content || '', streaming: true, working_label: t('teamDetails.isWorking'),
      timeline: trail?.timeline || [], files: trail?.files || [],
      ...(streamed?.thinking_live ? { thinking_live: streamed.thinking_live } : {}),
    });
  }
  return out;
}

/** The round each turn spoke in, read off the board by the run it wrote. */
export function turnRounds(messages) {
  const rounds = {};
  for (const m of messages || []) {
    if (m.run_id && rounds[m.run_id] === undefined) rounds[m.run_id] = { round: m.round, kind: m.kind };
  }
  return rounds;
}

export const isTurnLive = (turn) => turn?.status === 'running' || turn?.status === 'pending';

/**
 * Each member's part in the run on screen: whether it is working now, how
 * its last turn ended, how many turns it took and what they cost.
 */
export function memberActivity(members, turns, messages) {
  const bySpeaker = {};
  for (const m of members || []) {
    bySpeaker[m.display_name] = { status: 'idle', turns: 0, cost: 0, error: '' };
  }
  for (const turn of turns || []) {
    const row = bySpeaker[turn.speaker];
    if (!row) continue;
    row.turns += 1;
    row.status = isTurnLive(turn) ? 'running' : (turn.status || 'completed');
    row.error = turn.status === 'failed' ? (turn.error || '') : '';
  }
  for (const m of messages || []) {
    const row = bySpeaker[m.sender];
    if (row && m.cost) row.cost += m.cost;
  }
  return bySpeaker;
}
