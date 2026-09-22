import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
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
