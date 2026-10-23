import { describe, it, expect } from 'vitest';
import { withHandoffLine } from '../handoff';

const LINE = 'Передаю чат агенту Bean Scout.';
const next = { role: 'agent', agent_id: 'bean_scout',
  handoff: { from_agent_id: 'main-agent', to_agent_id: 'bean_scout', reason: 'Найду 3 обжарщика в Берлине.' } };

describe('withHandoffLine', () => {
  it('replaces a bare reason with the line naming the agent', () => {
    const msg = { role: 'agent', agent_id: 'main-agent', content: 'Найду 3 обжарщика в Берлине.' };
    expect(withHandoffLine(msg, next, LINE).content).toBe(LINE);
  });

  it('keeps text of its own and adds the line after it', () => {
    const msg = { role: 'agent', agent_id: 'main-agent', content: 'Это вопрос к поставщикам.' };
    expect(withHandoffLine(msg, next, LINE).content).toBe(`Это вопрос к поставщикам.\n\n${LINE}`);
  });

  it('adds the line to the build timeline, dropping a bare reason', () => {
    const msg = { role: 'agent', agent_id: 'main-agent', content: '',
      timeline: [{ type: 'tool', tool: 'handoff_to_agent' }, { type: 'text', text: 'Найду 3 обжарщика в Берлине.' }] };
    expect(withHandoffLine(msg, next, LINE).timeline).toEqual([
      { type: 'tool', tool: 'handoff_to_agent' }, { type: 'text', text: LINE }]);
  });

  it('leaves a bubble that did not hand over alone', () => {
    const msg = { role: 'agent', agent_id: 'main-agent', content: 'Ответ.' };
    expect(withHandoffLine(msg, { role: 'user', content: 'ещё' }, LINE)).toBe(msg);
    expect(withHandoffLine(msg, { ...next, handoff: { ...next.handoff, from_agent_id: 'other' } }, LINE)).toBe(msg);
  });
});

import { conversationTarget } from '../handoff';

describe('conversationTarget', () => {
  const ids = ['main-agent', 'bean_scout'];

  it('follows the agent the conversation was handed to', () => {
    const conv = { id: 'c1', target_mode: 'agent', agent_id: 'bean_scout' };
    expect(conversationTarget(conv, ids)).toMatchObject({ targetMode: 'agent', agentId: 'bean_scout' });
    const moved = conversationTarget({ ...conv, agent_id: 'main-agent' }, ids);
    expect(moved.key).not.toBe(conversationTarget(conv, ids).key);
  });

  it('waits for the agent list, flows and teams do not', () => {
    expect(conversationTarget({ id: 'c1', agent_id: 'bean_scout' }, [])).toBeNull();
    expect(conversationTarget({ id: 'c2', target_mode: 'flow', flow_id: 'f' }, [])).toMatchObject({ targetMode: 'flow', flowId: 'f' });
    expect(conversationTarget({ id: 'c3', target_mode: 'team', team_id: 't' }, [])).toMatchObject({ targetMode: 'team', teamId: 't' });
  });

  it('gives no agent the workspace does not offer', () => {
    expect(conversationTarget({ id: 'c1', agent_id: 'gone' }, ids).targetMode).toBeNull();
    expect(conversationTarget(null, ids)).toBeNull();
  });
});
