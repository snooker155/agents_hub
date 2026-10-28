import React from 'react';
import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';

vi.mock('../../i18n', () => ({
  useI18n: () => ({ t: (k) => k }),
}));

import LoadedTools from '../run/LoadedTools';
import { loadedToolsOf } from '../run/loadedToolsOf';
import RunLoopPanel from '../run/RunLoopPanel';

describe('LoadedTools', () => {
  it('lists the tools the run loaded, with what they are', () => {
    render(<LoadedTools run={{ loop: { loaded_tools: ['read_file', 'write_file'] } }} />);
    expect(screen.getByText('runLoop.loadedTools (2)')).toBeInTheDocument();
    expect(screen.getByText('runLoop.loadedToolsHint')).toBeInTheDocument();
    expect(screen.getByText('read_file')).toBeInTheDocument();
    expect(screen.getByText('write_file')).toBeInTheDocument();
  });

  it('reads an empty list off a run without loop data', () => {
    expect(loadedToolsOf(null)).toEqual([]);
    expect(loadedToolsOf({ loop: {} })).toEqual([]);
    expect(loadedToolsOf({ loop: { loaded_tools: ['a', ''] } })).toEqual(['a']);
  });

  it('leaves the loop panel empty when loaded tools are all the loop holds', () => {
    const { container } = render(<RunLoopPanel run={{ run_id: 'r1', loop: { loaded_tools: ['read_file'] } }} />);
    expect(container.firstChild).toBeNull();
  });
});
