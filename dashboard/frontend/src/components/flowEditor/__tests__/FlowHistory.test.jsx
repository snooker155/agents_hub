import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
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
    // The kind leads each row's second line.
    expect(screen.getByText(/^Chat · /)).toBeInTheDocument();
    expect(screen.getByText(/^Background run · /)).toBeInTheDocument();
  });

  it('offers Delete on a chat and on a task run, and passes the entry without selecting it', () => {
    const runs = [
      { run_group: 'g1', kind: 'chat', title: 'Hello there', started_at: '2026-01-01T00:00:00Z', status: 'completed', events: [] },
      { run_group: 'g2', kind: 'task', title: 'Nightly run', started_at: '2026-01-02T00:00:00Z', status: 'completed', events: [] },
      // Still running, and an entry of the old shared log: nothing to delete yet / no file of its own.
      { run_group: 'g3', kind: 'task', title: 'Going', started_at: '2026-01-03T00:00:00Z', status: 'running', events: [] },
      { run_group: 'legacy-1', kind: 'task', title: 'Old', started_at: '2025-01-01T00:00:00Z', status: 'completed', events: [] },
    ];
    const onDelete = vi.fn();
    const onSelect = vi.fn();
    show({ runs, instances: {}, onResume: vi.fn(), onSelect, onDelete });
    const buttons = screen.getAllByTestId('flow-history-delete');
    expect(buttons).toHaveLength(2);
    fireEvent.click(buttons[1]);
    expect(onDelete).toHaveBeenCalledWith(runs[1]);
    expect(onSelect).not.toHaveBeenCalled();
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
