import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';

import { I18nProvider } from '../../i18n';
import ToolStatusMark from '../ToolStatusMark';
import { toolOutcome, toolStatus } from '../toolStatus';
import { TimelineToolCard } from '../chat/timeline';

describe('toolStatus', () => {
  it('reads a running call as running whatever it carries', () => {
    expect(toolStatus({ running: true, status: 'ok' })).toBe('running');
  });

  it('takes the status the backend sent', () => {
    expect(toolStatus({ output: 'fine', status: 'error' })).toBe('error');
    expect(toolStatus({ output: 'ERROR: x', status: 'ok' })).toBe('ok');
  });

  it('reads a stored call without a status from its output', () => {
    expect(toolStatus({ output: '{"ok": false, "error": "no such file"}' })).toBe('error');
    expect(toolStatus({ output: 'ERROR: boom' })).toBe('error');
    expect(toolStatus({ output: 'Error: boom' })).toBe('error');
    expect(toolStatus({ output: '{"ok": true}' })).toBe('ok');
    expect(toolStatus({ output: 'the error was fixed' })).toBe('ok');
    expect(toolStatus({ error: 'raised' })).toBe('error');
  });

  it('has no verdict for a call that never returned', () => {
    expect(toolStatus({ output: null })).toBe('unknown');
    expect(toolStatus(null)).toBe('unknown');
  });

  it('carries status and the policy verdict from a live event', () => {
    expect(toolOutcome({ type: 'tool_end', status: 'error', evaluated_permission: 'allow', reason_code: 'r' }))
      .toEqual({ status: 'error', evaluated_permission: 'allow', reason_code: 'r' });
    expect(toolOutcome({ type: 'tool_end' })).toEqual({});
  });
});

describe('ToolStatusMark', () => {
  const draw = (node) => render(<I18nProvider>{node}</I18nProvider>);

  it('marks each state with its own dot and label', () => {
    draw(<>
      <ToolStatusMark entry={{ running: true }} />
      <ToolStatusMark entry={{ output: 'ok' }} />
      <ToolStatusMark entry={{ output: '{"ok": false}' }} />
    </>);
    const marks = screen.getAllByTestId('tool-status');
    expect(marks.map((m) => m.dataset.status)).toEqual(['running', 'ok', 'error']);
    expect(marks[0].className).toContain('animate-pulse');
    expect(marks[2].className).toContain('bg-red-500');
    expect(marks[2].getAttribute('aria-label')).toBeTruthy();
  });

  it('is drawn on a Build-view tool card', () => {
    draw(<TimelineToolCard entry={{ type: 'tool', tool: 'read_file', output: '{"ok": false}' }} />);
    expect(screen.getByTestId('tool-status').dataset.status).toBe('error');
  });
});
