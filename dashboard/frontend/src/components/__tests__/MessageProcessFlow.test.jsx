import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

vi.mock('../../api', () => ({
  getMessageInsights: vi.fn(() => new Promise(() => {})),
  getEvalSetsForRun: vi.fn(() => new Promise(() => {})),
  createEvalSet: vi.fn(),
  addEvalCase: vi.fn(),
}));

import { I18nProvider } from '../../i18n';
import MessageProcessFlow from '../MessageProcessFlow';

const show = (tools) => render(
  <I18nProvider><MessageProcessFlow runs={[{ run_id: 'r1', input: 'go', output: 'done', tools }]} /></I18nProvider>,
);

describe('MessageProcessFlow', () => {
  it('draws a delegation as the delegated run, with a link to its page', () => {
    show([{ step: 1, tool: 'run_agent_tool', input: '{"agent_id": "@coder"}',
      output: '{"ok": true, "run_id": "child", "agent_id": "swe_agent", "output": "ok"}' }]);
    expect(screen.getByText('Delegated to swe_agent')).toBeInTheDocument();
    expect(screen.getByTestId('delegated-run-link')).toHaveAttribute('href', '/messages/child');
    expect(screen.queryByText('Tool: run_agent_tool')).toBeNull();
  });

  it('says why a tool call failed', () => {
    show([{ step: 1, tool: 'read_file', input: '{"path": "a.bin"}', status: 'error',
      output: '{"ok": false, "error": "read_file failed: Binary file"}' }]);
    expect(screen.getByTestId('tool-error')).toHaveTextContent('Error: read_file failed: Binary file');
  });
});
