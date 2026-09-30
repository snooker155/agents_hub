import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { I18nProvider } from '../../../i18n';
import { CharacterCard } from '../characters';

const wrap = (ui) => render(<I18nProvider>{ui}</I18nProvider>);

const WORLD = {
  role_specs: [
    {
      name: 'innkeeper', description: 'Runs the inn.',
      start_location: 'tavern', start_items: ['ledger'], stats: { mood: 2 },
      actions: ['serve'],
    },
  ],
  roles: ['innkeeper'],
  actions: [{ name: 'serve', roles: ['innkeeper'] }],
  generic_role: { start_location: 'road', stats: {}, actions: [] },
};

const AGENT = { id: 'agt_1', name: 'Agent One' };

const ROLE = {
  agent_id: 'agt_1', name: 'Mira', role: 'innkeeper',
  goal: 'Keep the inn running through the winter.',
  private_knowledge: 'Knows the guild is owed three winters of rent.',
  objective: 'profit', model: 'gpt-5', memory_horizon: 8,
  wake_every: 3, starts: true, npc: false,
};

describe('CharacterCard', () => {
  it('shows who the character is and what it wants', () => {
    wrap(
      <CharacterCard
        role={ROLE} agent={AGENT} world={WORLD} triggered
        onOpen={() => {}} onRemove={() => {}}
      />,
    );
    expect(screen.getByText('Mira')).toBeInTheDocument();
    expect(screen.getByText('Agent One · innkeeper')).toBeInTheDocument();
    expect(screen.getByText(ROLE.goal)).toBeInTheDocument();
    // Bound to a declared role: what it grants is on the card, and it is not
    // flagged as the amber "generic role" fallback.
    expect(screen.getByText(/starts in tavern/)).toBeInTheDocument();
    expect(screen.queryByText(/generic role/)).not.toBeInTheDocument();
  });

  it('chips the wiring: model, objective, memory, and the triggered-only settings', () => {
    wrap(
      <CharacterCard
        role={ROLE} agent={AGENT} world={WORLD} triggered
        onOpen={() => {}} onRemove={() => {}}
      />,
    );
    expect(screen.getByText('gpt-5')).toBeInTheDocument();
    expect(screen.getByText('profit')).toBeInTheDocument();
    expect(screen.getByText('memory 8')).toBeInTheDocument();
    expect(screen.getByText('wakes every 3')).toBeInTheDocument();
    expect(screen.getByText('opens the scene')).toBeInTheDocument();
    expect(screen.getByText('secret')).toBeInTheDocument();
  });

  it('flags a character cast in nothing the world declares', () => {
    wrap(
      <CharacterCard
        role={{ ...ROLE, role: 'stranger' }} agent={AGENT} world={WORLD} triggered={false}
        onOpen={() => {}} onRemove={() => {}}
      />,
    );
    expect(screen.getByText(/generic role/)).toBeInTheDocument();
  });

  it('opens the dialog and removes the character on their own clicks', () => {
    const onOpen = vi.fn();
    const onRemove = vi.fn();
    wrap(
      <CharacterCard
        role={ROLE} agent={AGENT} world={WORLD} triggered
        onOpen={onOpen} onRemove={onRemove}
      />,
    );
    fireEvent.click(screen.getByText('Mira'));
    expect(onOpen).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByTitle('Remove character'));
    expect(onRemove).toHaveBeenCalledTimes(1);
  });
});
