import React from 'react';
import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';

// A run a person moved to another model mid-run (steering mode switch_model):
// the panel lists the switch, and the calls the new model answered carry a
// "switched" badge rather than the fallback one.

vi.mock('../../api/agentVersions', () => ({ getRunAgentVersion: vi.fn(), rollbackRunAgent: vi.fn() }));
vi.mock('../../i18n', () => ({
  useI18n: () => ({ t: (k, vars) => (vars ? `${k} ${JSON.stringify(vars)}` : k) }),
}));

import RunLoopPanel from '../run/RunLoopPanel';

describe('RunLoopPanel model switches', () => {
  it('lists each switch with its step, models, author and failure', () => {
    render(<RunLoopPanel run={{
      run_id: 'r1',
      loop: {
        model_switches: [
          { msg_id: 's1', after_step: 2, from: 'anthropic/claude-a', to: 'openai/gpt-b', by: 'anton' },
          { msg_id: 's2', after_step: 4, to: 'openai/gone', error: 'not enabled' },
        ],
        answered_by: [
          { provider: 'openai', model: 'gpt-b', fallback: true, switched: true, reason: 'switch_model' },
          { provider: 'openai', model: 'spare', fallback: true, reason: 'RateLimitError' },
        ],
      },
    }} />);
    expect(screen.getByText('runLoop.modelSwitches')).toBeInTheDocument();
    expect(screen.getByText('runLoop.switchFromTo {"from":"anthropic/claude-a","to":"openai/gpt-b"}')).toBeInTheDocument();
    expect(screen.getByText('runLoop.switchBy {"name":"anton"}')).toBeInTheDocument();
    expect(screen.getByText('runLoop.switchFailed {"error":"not enabled"}')).toBeInTheDocument();
    expect(screen.getByText('runLoop.switched')).toBeInTheDocument();
    expect(screen.getByText('runLoop.fallback')).toBeInTheDocument();
    expect(screen.queryByText('switch_model')).toBeNull();
  });
});
