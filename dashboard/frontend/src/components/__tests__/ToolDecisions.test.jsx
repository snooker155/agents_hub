import React from 'react';
import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';

vi.mock('../../i18n', () => ({
  useI18n: () => ({ t: (k) => k }),
}));

import ToolDecisions from '../run/ToolDecisions';
import { toolDecisionsOf } from '../run/toolDecisionsOf';
import RunLoopPanel from '../run/RunLoopPanel';

describe('ToolDecisions', () => {
  it('lists each decision with its mode, result, author and reason', () => {
    render(<ToolDecisions run={{ loop: { tool_decisions: [
      { tool: 'run_shell', mode: 'always_ask', decision: 'run', reason: 'approved', by: 'policy' },
      { tool: 'write_file', mode: 'auto', decision: 'deny', reason: 'outside workspace', by: 'auto', cached: true },
    ] } }} />);
    expect(screen.getByText('runLoop.toolDecisions (2)')).toBeInTheDocument();
    expect(screen.getByText('run_shell')).toBeInTheDocument();
    expect(screen.getByText('always_ask')).toBeInTheDocument();
    expect(screen.getByText('deny')).toBeInTheDocument();
    expect(screen.getByText('outside workspace')).toBeInTheDocument();
    expect(screen.getByText('runLoop.decisionCached')).toBeInTheDocument();
  });

  it('reads an empty list off a run without loop data', () => {
    expect(toolDecisionsOf(null)).toEqual([]);
    expect(toolDecisionsOf({ loop: {} })).toEqual([]);
    expect(toolDecisionsOf({ loop: { tool_decisions: [null, { tool: 'a' }] } })).toEqual([{ tool: 'a' }]);
  });

  it('leaves the loop panel empty when decisions are all the loop holds', () => {
    const { container } = render(<RunLoopPanel run={{ run_id: 'r1', loop: { tool_decisions: [{ tool: 'a', decision: 'run' }] } }} />);
    expect(container.firstChild).toBeNull();
  });
});
