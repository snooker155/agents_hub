import { beforeAll, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';

vi.mock('../../api', () => ({
  getMessageInsights: vi.fn(),
  getEvalSetsForRun: vi.fn(() => new Promise(() => {})),
  createEvalSet: vi.fn(),
  addEvalCase: vi.fn(),
}));

import { I18nProvider } from '../../i18n';
import ProcessGraph from '../ProcessGraph';
import { policyVerdict } from '../policyVerdict';

const RUN = {
  run_id: 'r1', agent_id: 'main-agent', input: 'tidy up', output: 'Done',
  status: 'completed',
  tools: [
    { step: 1, tool: 'read_file', input: "{'path': 'a'}", output: 'x',
      evaluated_permission: 'allow', reason_code: 'default_allow' },
    { step: 2, tool: 'run_shell', input: "{'command': 'rm -rf /'}", output: 'refused',
      evaluated_permission: 'deny', reason_code: 'auto_deny' },
    { step: 3, tool: 'list_tasks', input: '{}', output: '[]' },
  ],
};

describe('policy badge on tool nodes', () => {
  beforeAll(() => { Element.prototype.scrollIntoView = vi.fn(); });

  it('shows the permission on each tool node that carries one', () => {
    render(<I18nProvider><ProcessGraph messageRuns={[RUN]} /></I18nProvider>);
    const badges = screen.getAllByTestId('policy-badge');
    expect(badges).toHaveLength(2);
    expect(badges[0]).toHaveTextContent('allowed');
    expect(badges[1]).toHaveTextContent('denied');
    expect(badges[1].getAttribute('data-reason-code')).toBe('auto_deny');
    expect(badges[1].getAttribute('title')).toMatch(/classifier said deny/);
  });

  it('names the reason in the call detail', () => {
    render(<I18nProvider><ProcessGraph messageRuns={[RUN]} /></I18nProvider>);
    const node = screen.getAllByTestId('process-node').find((n) => n.textContent.includes('run_shell'));
    fireEvent.click(node);
    const modal = screen.getByTestId('process-step-modal');
    expect(within(modal).getByTestId('tool-call-policy')).toHaveTextContent(/denied.*classifier said deny/);
  });

  it('reads the two fields off a live event', () => {
    expect(policyVerdict({ type: 'tool_end', evaluated_permission: 'ask', reason_code: 'hook_ask' }))
      .toEqual({ evaluated_permission: 'ask', reason_code: 'hook_ask' });
    expect(policyVerdict({ type: 'tool_end' })).toEqual({});
  });
});
