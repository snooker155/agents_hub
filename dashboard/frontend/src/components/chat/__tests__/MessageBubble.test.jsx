import { describe, expect, it } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { I18nProvider } from '../../../i18n';
import { MessageBubble } from '../MessageBubble';

const show = (msg) => render(
  <I18nProvider>
    <MessageBubble msg={msg} />
  </I18nProvider>,
);

describe('MessageBubble: system notice', () => {
  it('renders a compaction notice as a centered line, not a chat bubble', () => {
    const { container } = show({ id: 'n1', role: 'system', kind: 'compaction', content: 'Earlier messages were folded into a summary (5 turns).' });
    expect(screen.getByText(/folded into a summary/)).toBeInTheDocument();
    // No avatar rendered for a system notice, unlike a real bubble.
    expect(container.querySelector('svg')).toBeNull();
  });

  it('still renders an ordinary agent bubble normally', () => {
    show({ id: 'm1', role: 'agent', content: 'Hello there' });
    expect(screen.getByText('Hello there')).toBeInTheDocument();
  });
});

describe('MessageBubble: a line said to the Assistant', () => {
  it('is marked as spoken, and a typed one is not', () => {
    show({ id: 'u1', role: 'user', content: 'what failed today', voice: true });
    expect(screen.getByTestId('spoken-caption')).toHaveTextContent('spoken');
  });

  it('a typed line has no mark', () => {
    show({ id: 'u2', role: 'user', content: 'and yesterday?' });
    expect(screen.queryByTestId('spoken-caption')).toBeNull();
  });
});

describe('MessageBubble: a turn that delegated', () => {
  const live = (running) => ({
    id: 'a1', role: 'agent', content: '', timeline: [
      { type: 'tool', tool: 'delegate_task', running },
      { type: 'delegation', run_id: 'r2', agent_id: 'researcher', agent_name: 'Researcher', running, ok: running ? null : true, timeline: [] },
    ],
  });
  const mark = (container) => container.querySelector('svg.ah-live-mark');

  it('shows the delegate scene until the delegated agent finishes', async () => {
    const { container } = render(<I18nProvider><MessageBubble msg={live(true)} isStreaming /></I18nProvider>);
    await waitFor(() => expect(mark(container)).toHaveAttribute('data-state', 'delegate'));
  });

  it('goes back to working once it has finished', async () => {
    const { container } = render(<I18nProvider><MessageBubble msg={live(false)} isStreaming /></I18nProvider>);
    await waitFor(() => expect(mark(container)?.getAttribute('data-state')).not.toBe('delegate'));
  });
});
