/**
 * Team turns: every board entry is its own bubble, labelled with who said it
 * and who it was for. There are no token events to stream — a member's turn
 * arrives as a whole reply.
 */
import { genId } from '../turnState';
import { markSteerDelivered } from '../steering';

// Returns true when it handled `event`, so the caller knows not to try the
// flow / generic handlers on the same event.
function handleTeamEvent(event, ctx) {
  const { convId, setConversations, setActiveRunId, setSessionId, state } = ctx;

  if (event.type === 'team_message' && event.kind === 'steer') {
    // A message this chat sent into the running team, now on its board
    // (teams/runner.py): the steer bubble already shows it, so it only turns
    // delivered. Its `run_id` carries the steering message id.
    setConversations((prev) =>
      prev.map((c) =>
        c.id !== convId ? c : { ...c, messages: markSteerDelivered(c.messages, event.run_id, null) },
      ),
    );
    return true;
  }

  if (event.type === 'team_message') {
    if (event.run_id) { state.runId = event.run_id; setActiveRunId(event.run_id); }
    const recipients = (event.recipients || []).filter((r) => r !== '*');
    setConversations((prev) =>
      prev.map((c) =>
        c.id !== convId ? c : {
          ...c,
          messages: [
            ...c.messages,
            {
              id: genId(),
              role: 'agent',
              agent_id: event.sender,
              agent_label: recipients.length
                ? `${event.sender} → ${recipients.join(', ')}`
                : event.sender,
              content: event.content || '',
              error: event.kind === 'error',
              run_id: event.run_id || null,
              // The team run this turn belongs to: what the composer steers.
              team_run_id: event.team_run_id || null,
              inbound_tokens: null,
              outbound_tokens: null,
              total_tokens: event.tokens || null,
              tool_calls: null,
              duration_ms: null,
            },
          ],
        },
      ),
    );
    return true;
  }

  if (event.type === 'team_meta') {
    if (event.session_id) setSessionId(event.session_id);
    return true;
  }

  return false;
}

export { handleTeamEvent };
