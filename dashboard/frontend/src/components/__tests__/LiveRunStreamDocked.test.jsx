import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import { I18nProvider } from '../../i18n';

vi.mock('../../api/steering', () => ({ steerRun: vi.fn(), listRunSteering: vi.fn(() => new Promise(() => {})) }));
vi.mock('../stream', () => ({ useChannel: () => {} }));

import LiveRunStream from '../LiveRunStream';
import { ActivityText } from '../task/ActivityEntry';

const run = (status) => ({ run_id: 'run-1', agent_id: 'swe_agent', text: 'Writing index.html', thinking: [], tools: [], status });

const docked = (props) => render(
  <I18nProvider>
    <LiveRunStream sessionId="s" docked emptyText="Nobody is working" waitingText="Waiting for output" {...props} />
  </I18nProvider>,
);

describe('LiveRunStream as a tab', () => {
  it('is there with nothing running and says so', () => {
    docked({});
    expect(screen.getByTestId('live-run-docked')).toBeInTheDocument();
    expect(screen.getByText('Nobody is working')).toBeInTheDocument();
  });

  it('says it is waiting while a run has started but nothing streamed', () => {
    docked({ working: true });
    expect(screen.getByText('Waiting for output')).toBeInTheDocument();
  });

  it('shows a finished run no more', () => {
    docked({ runId: 'run-1', seed: run('done') });
    expect(screen.queryByText('Writing index.html')).toBeNull();
    expect(screen.getByText('Nobody is working')).toBeInTheDocument();
  });

  it('scrolls the output and keeps the box for the running run below it', () => {
    docked({ runId: 'run-1', seed: run('running') });
    const box = screen.getByTestId('live-run-box');
    const steer = screen.getByTestId('live-run-steer');
    expect(box.className).toMatch(/overflow-y-auto/);
    expect(box.className).toMatch(/flex-1/);
    expect(box).toHaveTextContent('Writing index.html');
    // A plain stream, not a framed and tinted card that grows.
    const stream = screen.getByTestId('live-run-stream');
    expect(stream.className).not.toMatch(/border|bg-/);
    // The agent and run id stay above the scrolling part.
    const heading = screen.getByTestId('live-run-heading');
    expect(heading).toHaveTextContent('swe_agent');
    expect(box.contains(heading)).toBe(false);
    // The steering box is outside the scrolling part, after it.
    expect(box.contains(steer)).toBe(false);
    expect(box.compareDocumentPosition(steer) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });
});

describe('activity entries of the self-check', () => {
  const show = (entry, agent) => render(
    <MemoryRouter><I18nProvider><ActivityText entry={entry} agent={agent} /></I18nProvider></MemoryRouter>);

  it('lists the checks left for a person', () => {
    show({ type: 'person_checks', checks: ['Open the page on a phone', 'Read the prices'] });
    expect(screen.getByText(/For a person to check/)).toBeInTheDocument();
    expect(screen.getByText('Read the prices')).toBeInTheDocument();
  });

  it('says why the agent could not finish', () => {
    show({ type: 'self_reported_blocked', reason: 'no browser' }, 'swe_agent');
    expect(screen.getByText(/could not finish: no browser/)).toBeInTheDocument();
  });
});
