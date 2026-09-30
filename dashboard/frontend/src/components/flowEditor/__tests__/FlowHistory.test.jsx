import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { I18nProvider } from '../../../i18n';
import { FlowHistory } from '../FlowHistory';

const show = (props) => render(
  <I18nProvider>
    <FlowHistory {...props} />
  </I18nProvider>,
);

describe('FlowHistory', () => {
  it('shows the empty state when there are no runs', () => {
    show({ runs: [], instances: {}, onResume: vi.fn(), onSelect: vi.fn() });
    expect(screen.getByText(/No previous runs yet/)).toBeInTheDocument();
  });

  it('renders a chat run and a task run with their statuses', () => {
    const runs = [
      { run_group: 'g1', kind: 'chat', title: 'Hello there', started_at: '2026-01-01T00:00:00Z', status: 'completed', events: [] },
      { run_group: 'g2', kind: 'task', title: 'Nightly run', started_at: '2026-01-02T00:00:00Z', status: 'running', events: [] },
    ];
    show({ runs, instances: {}, onResume: vi.fn(), onSelect: vi.fn() });

    expect(screen.getByText('Hello there')).toBeInTheDocument();
    expect(screen.getByText('Nightly run')).toBeInTheDocument();
    expect(screen.getByText('Completed')).toBeInTheDocument();
    expect(screen.getByText('Running')).toBeInTheDocument();
    // Kind badges.
    expect(screen.getByText('Chat')).toBeInTheDocument();
    expect(screen.getByText('Task run')).toBeInTheDocument();
  });

  it('offers Resume only for a run that is failed/awaiting input with a checkpoint', () => {
    const runs = [
      { run_group: 'g1', kind: 'task', title: 'Failed run', started_at: '2026-01-01T00:00:00Z', status: 'failed', events: [] },
      { run_group: 'g2', kind: 'task', title: 'Completed run', started_at: '2026-01-01T00:00:00Z', status: 'completed', events: [] },
    ];
    const instances = {
      g1: { status: 'failed', checkpoint: { some: 'state' } },
      g2: { status: 'completed', checkpoint: null },
    };
    show({ runs, instances, onResume: vi.fn(), onSelect: vi.fn() });

    expect(screen.getByRole('button', { name: 'Resume' })).toBeInTheDocument();
    // Only one Resume button, the completed run does not get one.
    expect(screen.getAllByRole('button', { name: 'Resume' })).toHaveLength(1);
  });
});
