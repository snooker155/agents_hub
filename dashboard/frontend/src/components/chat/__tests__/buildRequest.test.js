import { describe, it, expect } from 'vitest';
import {
  truncateTitle, buildAttachmentLine, buildHistoryPayload,
  resolveConversationTitle, buildStreamRequestBody,
} from '../send/buildRequest';

// A minimal `t` stand-in: enough of the two keys buildAttachmentLine actually
// calls, plus the newConversation placeholder resolveConversationTitle needs.
const t = (key, vars) => {
  if (key === 'chat.attachedEntities') return `Attached: ${vars.entities}`;
  if (key === 'chat.attachedFiles') return `Files: ${vars.files}`;
  if (key === 'chat.newConversation') return 'New conversation';
  return key;
};

describe('truncateTitle', () => {
  it('leaves a short string untouched', () => {
    expect(truncateTitle('hello')).toBe('hello');
  });

  it('cuts a long string at 50 chars and adds an ellipsis', () => {
    const long = 'x'.repeat(60);
    const out = truncateTitle(long);
    expect(out).toBe(`${'x'.repeat(50)}…`);
  });
});

describe('buildAttachmentLine', () => {
  it('is empty with nothing attached', () => {
    expect(buildAttachmentLine(t, { pendingAttachments: [], pendingReferences: [] })).toBe('');
  });

  it('lists attached files', () => {
    const line = buildAttachmentLine(t, {
      pendingAttachments: [{ filename: 'a.txt' }, { filename: 'b.txt' }],
      pendingReferences: [],
    });
    expect(line).toBe('Files: a.txt, b.txt');
  });

  it('lists attached entities, preferring their label over id', () => {
    const line = buildAttachmentLine(t, {
      pendingAttachments: [],
      pendingReferences: [{ id: 't1', label: 'Task One' }, { id: 't2' }],
    });
    expect(line).toBe('Attached: Task One, t2');
  });

  it('joins both lines when both are present', () => {
    const line = buildAttachmentLine(t, {
      pendingAttachments: [{ filename: 'a.txt' }],
      pendingReferences: [{ id: 't1', label: 'Task One' }],
    });
    expect(line).toBe('Attached: Task One\nFiles: a.txt');
  });
});

describe('buildHistoryPayload', () => {
  it('keeps only user/agent messages with non-blank content', () => {
    const messages = [
      { role: 'user', content: 'hi' },
      { role: 'agent', content: '' },
      { role: 'agent', content: '  ' },
      { role: 'agent', content: 'hello there' },
    ];
    expect(buildHistoryPayload(messages)).toEqual([
      { role: 'user', content: 'hi' },
      { role: 'agent', content: 'hello there' },
    ]);
  });

  it('keeps only the last 40 turns', () => {
    const messages = Array.from({ length: 50 }, (_, i) => ({ role: 'user', content: `m${i}` }));
    const out = buildHistoryPayload(messages);
    expect(out).toHaveLength(40);
    expect(out[0].content).toBe('m10');
    expect(out[39].content).toBe('m49');
  });

  it('survives an empty/missing list', () => {
    expect(buildHistoryPayload(null)).toEqual([]);
    expect(buildHistoryPayload([])).toEqual([]);
  });
});

describe('resolveConversationTitle', () => {
  it('auto-titles a brand new conversation from the typed text', () => {
    const title = resolveConversationTitle({ text: 'Plan the launch', attachmentLine: '', convRecord: undefined, t });
    expect(title).toBe('Plan the launch');
  });

  it('falls back to the placeholder when there is no text and no attachment line', () => {
    const title = resolveConversationTitle({ text: '', attachmentLine: '', convRecord: undefined, t });
    expect(title).toBe('New conversation');
  });

  it('keeps a real, already-set title', () => {
    const convRecord = { title: 'My custom title', messages: [{ role: 'user', content: 'hi' }] };
    const title = resolveConversationTitle({ text: 'second message', attachmentLine: '', convRecord, t });
    expect(title).toBe('My custom title');
  });

  it('replaces a placeholder title (in any locale) with the new auto-title', () => {
    // The conversation still carries the Russian wording for "New
    // conversation" — a title written before a language switch must still be
    // recognised as unset.
    const convRecord = { title: 'Новый диалог', messages: [{ role: 'user', content: 'first' }] };
    const title = resolveConversationTitle({ text: 'Plan the launch', attachmentLine: '', convRecord, t });
    expect(title).toBe('Plan the launch');
  });

  it('re-titles an empty conversation even if it already carries a title', () => {
    const convRecord = { title: 'stale title', messages: [] };
    const title = resolveConversationTitle({ text: 'Plan the launch', attachmentLine: '', convRecord, t });
    expect(title).toBe('Plan the launch');
  });
});

describe('buildStreamRequestBody', () => {
  const base = {
    text: 'hello', effectiveWorkspace: 'ws1', projectId: 'p1', convId: 'c1', convTitle: 'Chat',
    clientId: 'client-1', historyPayload: [], pendingAttachments: [], pendingReferences: [],
    selectedWorkspace: 'ws1',
  };

  it('targets the agent in agent mode', () => {
    const body = buildStreamRequestBody({
      ...base, targetMode: 'agent', isFlowMode: false, isTeamMode: false,
      selectedAgent: 'agent-1', selectedFlow: 'flow-1', selectedTeam: 'team-1',
    });
    expect(body.agent_id).toBe('agent-1');
    expect(body.flow_id).toBeNull();
    expect(body.team_id).toBeNull();
  });

  it('targets the flow in flow mode', () => {
    const body = buildStreamRequestBody({
      ...base, targetMode: 'flow', isFlowMode: true, isTeamMode: false,
      selectedAgent: 'agent-1', selectedFlow: 'flow-1', selectedTeam: 'team-1',
    });
    expect(body.agent_id).toBeNull();
    expect(body.flow_id).toBe('flow-1');
    expect(body.team_id).toBeNull();
  });

  it('targets the team in team mode', () => {
    const body = buildStreamRequestBody({
      ...base, targetMode: 'team', isFlowMode: false, isTeamMode: true,
      selectedAgent: 'agent-1', selectedFlow: 'flow-1', selectedTeam: 'team-1',
    });
    expect(body.agent_id).toBeNull();
    expect(body.flow_id).toBeNull();
    expect(body.team_id).toBe('team-1');
  });

  it('maps attachments and marks store_to_workspace only when a workspace is selected', () => {
    const body = buildStreamRequestBody({
      ...base, targetMode: 'agent', isFlowMode: false, isTeamMode: false,
      selectedAgent: 'agent-1', selectedFlow: null, selectedTeam: null,
      pendingAttachments: [{ filename: 'a.txt', content: 'data', store_to_workspace: true }],
      selectedWorkspace: '',
    });
    expect(body.attachments).toEqual([{ filename: 'a.txt', content: 'data', store_to_workspace: false }]);
  });

  it('maps references to pointer-only entries', () => {
    const body = buildStreamRequestBody({
      ...base, targetMode: 'agent', isFlowMode: false, isTeamMode: false,
      selectedAgent: 'agent-1', selectedFlow: null, selectedTeam: null,
      pendingReferences: [{ kind: 'task', id: 't1', label: 'Task One' }],
    });
    expect(body.references).toEqual([{ kind: 'task', id: 't1', label: 'Task One' }]);
  });
});
