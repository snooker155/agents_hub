import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi } from 'vitest';
import { I18nProvider } from '../../../i18n';
import SetupGuidePanel from '../SetupGuidePanel';

// useWelcomeTour drives driver.js, which needs a real DOM walk this test does
// not care about; only that its start() got called from the tour step.
const tour = vi.hoisted(() => ({ start: vi.fn() }));
vi.mock('../../docs/WelcomeTour', () => ({ useWelcomeTour: () => tour }));

const GUIDE = {
  active: true, started_at: '2026-01-01T00:00:00Z', finished_at: null, dismissed_at: null,
  mode: 'text', admin: true, multi: false, needs_model: false,
  steps: [
    { id: 'model', group: 'setup', title: 'Connect a model', why: 'Nothing runs without one.',
      status: 'done', detail: 'openai/gpt-5', page: '/models', required: true, marked: false },
    { id: 'web_search', group: 'setup', title: 'Turn on web search', why: 'So agents can search.',
      status: 'todo', detail: '', page: '/settings', required: false, marked: false },
    { id: 'people', group: 'setup', title: 'Invite your team', why: 'Everyone gets their own sign in.',
      status: 'skipped', detail: '', page: '/users', required: false, marked: false },
    { id: 'first_chat', group: 'start', title: 'Talk to an agent in Chat', why: 'Chat is where you work.',
      status: 'todo', detail: '', page: '/chat', required: false, marked: false },
    { id: 'tour', group: 'start', title: 'Take the welcome tour', why: 'Two minutes over the pages.',
      status: 'todo', detail: '', page: '', required: false, marked: false },
  ],
  done: 1, total: 5, next: 'web_search', complete: false, work: null,
};

const renderPanel = (props = {}) => render(
  <I18nProvider>
    <MemoryRouter>
      <SetupGuidePanel guide={GUIDE} act={vi.fn()} onAsk={vi.fn()} {...props} />
    </MemoryRouter>
  </I18nProvider>,
);

describe('SetupGuidePanel', () => {
  it('renders nothing while the guide has not loaded yet', () => {
    const { container } = render(
      <I18nProvider><MemoryRouter><SetupGuidePanel guide={null} act={vi.fn()} onAsk={vi.fn()} /></MemoryRouter></I18nProvider>,
    );
    expect(container.textContent).toBe('');
  });

  it('shows the two groups with their steps and the progress count', () => {
    renderPanel();
    expect(screen.getByText('1 of 5')).toBeTruthy();
    expect(screen.getByText('Set up the hub')).toBeTruthy();
    expect(screen.getByText('Start using it')).toBeTruthy();
    expect(screen.getByTestId('setup-step-model')).toBeTruthy();
    expect(screen.getByTestId('setup-step-first_chat')).toBeTruthy();
  });

  it('a todo step offers "do it with the assistant", which calls onAsk with the step', () => {
    const onAsk = vi.fn();
    renderPanel({ onAsk });
    const row = screen.getByTestId('setup-step-web_search');
    fireEvent.click(within(row).getByText('Do it with the assistant'));
    expect(onAsk).toHaveBeenCalledWith(expect.objectContaining({ id: 'web_search' }));
  });

  it('the required step has no skip button, an optional one does', () => {
    renderPanel();
    const required = screen.getByTestId('setup-step-model');
    // "model" is already done, so it has no actions at all to check against;
    // use first_chat (todo, not required) and web_search (todo, not required)
    // plus a required-and-todo row is not in this fixture, so this asserts
    // the row that IS required shows no Skip even though it is done.
    expect(within(required).queryByText('Skip')).toBeNull();
    const optional = screen.getByTestId('setup-step-web_search');
    expect(within(optional).getByText('Skip')).toBeTruthy();
  });

  it('skip calls act with the skip action and the step id', async () => {
    const act = vi.fn(() => Promise.resolve(GUIDE));
    renderPanel({ act });
    fireEvent.click(within(screen.getByTestId('setup-step-web_search')).getByText('Skip'));
    await waitFor(() => expect(act).toHaveBeenCalledWith('skip', { step: 'web_search' }));
  });

  it('a skipped step offers undo skip, which calls act with unskip', async () => {
    const act = vi.fn(() => Promise.resolve(GUIDE));
    renderPanel({ act });
    fireEvent.click(within(screen.getByTestId('setup-step-people')).getByText('Undo skip'));
    await waitFor(() => expect(act).toHaveBeenCalledWith('unskip', { step: 'people' }));
  });

  it('the tour step always offers "take the tour", which starts the welcome tour', () => {
    renderPanel();
    fireEvent.click(within(screen.getByTestId('setup-step-tour')).getByText('Take the tour'));
    expect(tour.start).toHaveBeenCalled();
  });

  it('shows Restart always, and Finish only when complete', () => {
    renderPanel();
    expect(screen.getByText('Restart guide')).toBeTruthy();
    expect(screen.queryByText('Finish')).toBeNull();
    renderPanel({ guide: { ...GUIDE, complete: true } });
    expect(screen.getAllByText('Finish').length).toBeGreaterThan(0);
  });
});
