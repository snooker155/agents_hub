import React from 'react';
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { I18nProvider } from '../../../i18n';
import StageCanvas from '../stage';
import { stageLayout, columnsFor } from '../stage-model';

// Two rooms, two characters, one of whom walked from the other room and spoke
// to the one already standing there — the smallest scene with all three of the
// stage's layers in it.
const FRAME_0 = {
  renderer: 'custom',
  hour: 9,
  time_of_day: 'morning',
  globals: { unrest: 2 },
  locations: [
    { name: 'tavern', occupants: ['Old Tam'], items: ['mug'], entities: ['hearth'], exits: ['road'] },
    { name: 'road', occupants: ['Mira'], items: [], entities: [], exits: ['tavern'] },
  ],
  agents: [
    { name: 'Old Tam', role: 'innkeeper', location: 'tavern', inventory: ['key'], stats: { mood: 3 } },
    { name: 'Mira', role: 'traveller', location: 'road', inventory: [], stats: {} },
  ],
};

const FRAME_1 = {
  ...FRAME_0,
  locations: [
    { name: 'tavern', occupants: ['Old Tam', 'Mira'], items: ['mug'], entities: ['hearth'], exits: ['road'] },
    { name: 'road', occupants: [], items: [], entities: [], exits: ['tavern'] },
  ],
  agents: [
    { name: 'Old Tam', role: 'innkeeper', location: 'tavern', inventory: ['key'], stats: { mood: 3 } },
    { name: 'Mira', role: 'traveller', location: 'tavern', inventory: [], stats: {} },
  ],
};

const TICKS = [
  { tick: 0, decisions: [], resolutions: [], events: [], idle: [], frame: FRAME_0 },
  {
    tick: 1,
    decisions: [
      { agent: 'Mira', action: { action: 'move_to', args: { location: 'tavern' } }, reasoning: '' },
      {
        agent: 'Old Tam',
        action: { action: 'speak_to', args: { agent: 'Mira', text: 'What is in that bag?' } },
        reasoning: '',
      },
    ],
    resolutions: [
      { agent: 'Mira', action: 'move_to', args: {}, ok: true, message: 'moved', effects: { from: 'road', to: 'tavern' } },
      { agent: 'Old Tam', action: 'speak_to', args: {}, ok: true, message: 'queued', effects: {} },
    ],
    events: ['Mira arrives'], idle: [], frame: FRAME_1,
  },
];

const wrap = (ui) => render(<I18nProvider>{ui}</I18nProvider>);

describe('stageLayout', () => {
  it('places every room and every character standing in one', () => {
    const layout = stageLayout({ frame: FRAME_0, tick: TICKS[0], roles: [] });
    expect(layout.rooms.map((r) => r.name)).toEqual(['tavern', 'road']);
    expect(layout.rooms[0].cards.map((c) => c.name)).toEqual(['Old Tam']);
    expect(layout.rooms[1].cards.map((c) => c.name)).toEqual(['Mira']);
    expect(layout.width).toBeGreaterThan(0);
    expect(layout.height).toBeGreaterThan(0);
  });

  it('draws each declared exit once, not once per end', () => {
    const layout = stageLayout({ frame: FRAME_0, tick: TICKS[0] });
    expect(layout.edges).toHaveLength(1);
  });

  it('reads a move from the resolution the world wrote', () => {
    const layout = stageLayout({
      frame: FRAME_1, prevFrame: FRAME_0, tick: TICKS[1],
    });
    expect(layout.moves).toHaveLength(1);
    expect(layout.moves[0]).toMatchObject({ agent: 'Mira', fromName: 'road', toName: 'tavern' });
  });

  it('falls back to the previous frame when no resolution says so', () => {
    const silent = { ...TICKS[1], resolutions: [] };
    const layout = stageLayout({ frame: FRAME_1, prevFrame: FRAME_0, tick: silent });
    expect(layout.moves.map((m) => m.agent)).toEqual(['Mira']);
  });

  it('connects an addressed action to the agent it was addressed to', () => {
    const layout = stageLayout({ frame: FRAME_1, prevFrame: FRAME_0, tick: TICKS[1] });
    expect(layout.interactions).toHaveLength(1);
    expect(layout.interactions[0]).toMatchObject({
      agent: 'Old Tam', target: 'Mira', action: 'speak_to', refused: false,
    });
    expect(layout.interactions[0].speech).toContain('bag');
  });

  it('marks an action the world refused', () => {
    const refused = {
      ...TICKS[1],
      resolutions: [{ agent: 'Old Tam', action: 'speak_to', args: {}, ok: false, message: 'not here' }],
    };
    const layout = stageLayout({ frame: FRAME_1, prevFrame: FRAME_0, tick: refused });
    expect(layout.interactions[0].refused).toBe(true);
  });

  it('draws no line to somebody who is not on the stage', () => {
    const offstage = {
      ...TICKS[1],
      decisions: [{ agent: 'Old Tam', action: { action: 'speak_to', args: { agent: 'Nobody', text: 'hi' } } }],
    };
    const layout = stageLayout({ frame: FRAME_1, prevFrame: FRAME_0, tick: offstage });
    expect(layout.interactions).toHaveLength(0);
  });

  it('keeps a character the frame reports with no room to stand in', () => {
    const loose = {
      ...FRAME_0,
      agents: [...FRAME_0.agents, { name: 'Ghost', location: 'nowhere', inventory: [] }],
    };
    const layout = stageLayout({ frame: loose, tick: TICKS[0] });
    expect(layout.homeless.map((a) => a.name)).toEqual(['Ghost']);
  });

  it('shows an agent still writing as thinking, but only where asked', () => {
    const layout = stageLayout({
      frame: FRAME_0, tick: TICKS[0], activity: [{ tick: 0, agent: 'Mira', action: '' }],
    });
    expect(layout.turns.get('Mira').thinking).toBe(true);
  });

  it('keeps every card inside its room and off its neighbours', () => {
    // The layout is arithmetic, not flow, so nothing stops a mis-summed height
    // from putting two characters on top of each other. This is the guard.
    const crowded = {
      ...FRAME_0,
      locations: [
        { name: 'tavern', occupants: ['Old Tam', 'Mira', 'Bo'], items: ['mug', 'rope'], entities: ['hearth'], exits: ['road'] },
        { name: 'road', occupants: [], items: [], entities: [], exits: ['tavern'] },
      ],
      agents: [
        { name: 'Old Tam', location: 'tavern', inventory: ['key'], stats: { mood: 3 } },
        { name: 'Mira', location: 'tavern', inventory: [], stats: {} },
        { name: 'Bo', location: 'tavern', inventory: ['rope', 'lamp'], stats: { coin: 2 } },
      ],
    };
    const layout = stageLayout({ frame: crowded, tick: TICKS[1], prevFrame: FRAME_0 });
    const room = layout.rooms[0];
    room.cards.forEach((card, i) => {
      expect(card.y).toBeGreaterThanOrEqual(room.y);
      expect(card.y + card.h).toBeLessThanOrEqual(room.y + room.h);
      const next = room.cards[i + 1];
      if (next) expect(next.y).toBeGreaterThanOrEqual(card.y + card.h);
    });
    // And rooms themselves never overlap.
    layout.rooms.forEach((a) => layout.rooms.forEach((b) => {
      if (a === b) return;
      const apart = a.x + a.w <= b.x || b.x + b.w <= a.x
        || a.y + a.h <= b.y || b.y + b.h <= a.y;
      expect(apart).toBe(true);
    }));
  });

  it('keeps the map squarish rather than one long ribbon', () => {
    expect(columnsFor(1)).toBe(1);
    expect(columnsFor(4)).toBe(2);
    expect(columnsFor(16)).toBe(4);
  });
});

describe('StageCanvas', () => {
  it('draws the rooms, the cast, what they carry and what they did', () => {
    wrap(<StageCanvas ticks={TICKS} cursor={1} roles={[]} />);
    expect(screen.getByText('tavern')).toBeInTheDocument();
    expect(screen.getByText('road')).toBeInTheDocument();
    expect(screen.getByText('Old Tam')).toBeInTheDocument();
    expect(screen.getByText('key')).toBeInTheDocument();        // carried
    expect(screen.getByText('mug')).toBeInTheDocument();        // on the floor
    expect(screen.getByText('hearth')).toBeInTheDocument();     // a fixture
    // The action names the card and the arc between the two of them; the words
    // themselves are the speaker's card only, once.
    expect(screen.getAllByText('speak_to')).toHaveLength(2);
    expect(screen.getByText(/What is in that bag/)).toBeInTheDocument();
  });

  it('lists what the world itself wrote on the tick on screen', () => {
    const refused = {
      ...TICKS[1],
      resolutions: [{ agent: 'Mira', action: 'move_to', args: {}, ok: false, message: 'the door is barred' }],
      idle: ['Bo'],
    };
    wrap(<StageCanvas ticks={[TICKS[0], refused]} cursor={1} roles={[]} />);
    expect(screen.getByText('Mira arrives')).toBeInTheDocument();
    expect(screen.getByText(/the door is barred/)).toBeInTheDocument();
    expect(screen.getByText(/Silent: Bo/)).toBeInTheDocument();
  });

  it('shows the world its own numbers and its clock', () => {
    wrap(<StageCanvas ticks={TICKS} cursor={1} roles={[]} />);
    expect(screen.getByText(/Tick 1/)).toBeInTheDocument();
    expect(screen.getByText('unrest', { exact: false })).toBeInTheDocument();
  });

  it('says so rather than drawing an empty canvas before the first tick', () => {
    wrap(<StageCanvas ticks={[]} cursor={0} roles={[]} />);
    expect(screen.getByText(/Nothing to show yet/)).toBeInTheDocument();
  });

  it('falls back to the world view for a world with nowhere to stand', () => {
    const market = [{ tick: 0, decisions: [], resolutions: [], frame: { renderer: 'market', ticker: 'ACME', agents: [] } }];
    wrap(<StageCanvas ticks={market} cursor={0} roles={[]} />);
    expect(screen.getByText(/no map to draw/)).toBeInTheDocument();
    expect(screen.getByText('ACME')).toBeInTheDocument();
  });
});
