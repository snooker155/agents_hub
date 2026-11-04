import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { I18nProvider } from '../../../i18n';
import AgentPicker from '../AgentPicker';
import { AgentDropdown, ProjectDropdown } from '../targetPickers';

// The chat's top bar shows the agent and the project by name alone on a
// screen under 1800 CSS px while a column is open beside the chat
// (ChatTopBar passes iconOnWide); otherwise they keep their icons.

const AGENTS = [{ id: 'main-agent', name: 'Main Agent', tools: [] }];
const draw = (ui) => render(<I18nProvider><MemoryRouter>{ui}</MemoryRouter></I18nProvider>);

describe('top bar pickers on a smaller screen', () => {
  it('drop the agent avatar and the project icon under 1800 CSS px', () => {
    draw(
      <>
        <AgentDropdown agents={AGENTS} value="main-agent" onChange={vi.fn()} conversations={[]} iconOnWide />
        <ProjectDropdown projects={[{ id: 'p1', name: 'Site' }]} value="p1" onChange={vi.fn()} iconOnWide />
      </>,
    );
    expect(screen.getByTestId('agent-picker').firstElementChild).toHaveClass('max-[1799px]:hidden');
    expect(screen.getByText('Site').closest('button').querySelector('svg')).toHaveClass('max-[1799px]:hidden');
  });

  it('keep their icons with the columns closed, and outside the top bar', () => {
    const { unmount } = draw(
      <>
        <AgentDropdown agents={AGENTS} value="main-agent" onChange={vi.fn()} conversations={[]} />
        <ProjectDropdown projects={[{ id: 'p1', name: 'Site' }]} value="p1" onChange={vi.fn()} />
      </>,
    );
    expect(screen.getByTestId('agent-picker').firstElementChild).not.toHaveClass('max-[1799px]:hidden');
    expect(screen.getByText('Site').closest('button').querySelector('svg')).not.toHaveClass('max-[1799px]:hidden');
    unmount();
    draw(<AgentPicker agents={AGENTS} value="main-agent" onChange={vi.fn()} />);
    expect(screen.getByTestId('agent-picker').firstElementChild).not.toHaveClass('max-[1799px]:hidden');
  });
});
