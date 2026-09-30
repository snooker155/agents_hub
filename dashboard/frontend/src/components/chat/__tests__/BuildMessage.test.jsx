import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { I18nProvider } from '../../../i18n';
import { BuildMessage } from '../BuildMessage';

const show = (msg) => render(
  <I18nProvider>
    <BuildMessage msg={msg} agentName="Agent" onJumpArtifact={() => {}} />
  </I18nProvider>,
);

describe('BuildMessage: system notice', () => {
  it('renders a compaction notice as a centered line in the Build view too', () => {
    const { container } = show({ id: 'n1', role: 'system', kind: 'compaction', content: 'Earlier messages were folded into a summary (5 turns).' });
    expect(screen.getByText(/folded into a summary/)).toBeInTheDocument();
    // No avatar: a notice is not a message from anyone.
    expect(container.querySelector('svg')).toBeNull();
  });

  it('still renders a user message as a bubble', () => {
    const { container } = show({ id: 'u1', role: 'user', content: 'Hello there' });
    expect(screen.getByText('Hello there')).toBeInTheDocument();
    expect(container.querySelector('svg')).not.toBeNull();
  });
});
