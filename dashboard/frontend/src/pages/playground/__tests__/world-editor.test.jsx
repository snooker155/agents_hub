import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { I18nProvider } from '../../../i18n';
import { EffectList, PlaceholderHelp } from '../world-editor';
import { effectFieldRole, effectForType } from '../world-spec';

const POOLS = {
  who: ['actor', 'arg:agent'],
  whoAll: ['actor', 'arg:agent', '*'],
  locations: ['hall', 'cellar'],
  entities: ['the hatch'],
  items: ['the bell'],
  globals: ['alarm'],
  stats: ['coin'],
  stateKeys: ['open'],
};

const wrap = (ui) => render(<I18nProvider>{ui}</I18nProvider>);

const row = (i = 0) => screen.getAllByRole('textbox')[i];

describe('the effect table', () => {
  it('says which field each effect actually reads', () => {
    expect(effectFieldRole('message', 'target')).toBe('whoAll');
    expect(effectFieldRole('log', 'target')).toBe(null);
    expect(effectFieldRole('add_stat', 'value')).toBe('amount');
    // An effect type nothing declares reads nothing, rather than throwing.
    expect(effectFieldRole('invented', 'value')).toBe(null);
  });

  it('clears the fields a new type will never read', () => {
    const before = { type: 'message', target: 'arg:agent', name: '', value: 'hi' };
    expect(effectForType(before, 'log')).toEqual({
      type: 'log', target: '', name: '', value: 'hi',
    });
  });
});

describe('an effect row', () => {
  it('offers the world’s own names for the field this effect type reads', async () => {
    const user = userEvent.setup();
    wrap(<EffectList effects={[{ type: 'add_stat', target: '', name: '', value: '' }]}
                     onChange={() => {}} pools={POOLS} />);
    // `add_stat` writes a character value: whose, and which one.
    expect(screen.getByText('Whose / to whom')).toBeTruthy();
    expect(screen.getByText('Which character value')).toBeTruthy();
    await user.click(row(0));
    expect(screen.getByRole('button', { name: 'arg:agent' })).toBeTruthy();
    await user.click(row(1));
    expect(screen.getByRole('button', { name: 'coin' })).toBeTruthy();
  });

  it('greys out the fields its type ignores', () => {
    wrap(<EffectList effects={[{ type: 'log', target: '', name: '', value: '' }]}
                     onChange={() => {}} pools={POOLS} />);
    // A log line is one box of text; the other two are not read at all.
    expect(screen.getAllByText('not used')).toHaveLength(2);
    expect(row(0).disabled).toBe(true);
    expect(row(1).disabled).toBe(true);
    expect(row(2).disabled).toBe(false);
  });

  it('drops what the old type held when the type changes', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    wrap(<EffectList effects={[{ type: 'message', target: 'arg:agent', name: '', value: 'hi' }]}
                     onChange={onChange} pools={POOLS} />);
    await user.selectOptions(screen.getByRole('combobox'), 'log');
    expect(onChange).toHaveBeenCalledWith([
      { type: 'log', target: '', name: '', value: 'hi' },
    ]);
  });
});

describe('the placeholder legend', () => {
  const legend = (props = {}) => wrap(
    <PlaceholderHelp action="search" args={[{ name: 'location', type: 'location' }]}
                     globals={['alarm']} stats={['coin']} {...props} />,
  ).container;

  it('is written with this action’s own arguments', () => {
    const container = legend();
    expect(within(container).getByText('{arg.location}')).toBeTruthy();
    expect(within(container).getByText('arg:location')).toBeTruthy();
    // The pools behind each placeholder are named, so "{global.x}" is not a
    // riddle about which x the world has.
    expect(within(container).getByText('(alarm)', { exact: false })).toBeTruthy();
  });

  it('keeps the two substitutions apart: braces print, arg: points', () => {
    const container = legend();
    // The bare `arg:` form is filed under the pickers, not under text, and the
    // worked example shows the same argument doing both jobs.
    expect(within(container).getByText(/In the pickers/)).toBeTruthy();
    expect(within(container).getByText(/the effect lands on them/)).toBeTruthy();
    expect(within(container).getByText(/Say the agent takes “search” with location = cellar/))
      .toBeTruthy();
    expect(within(container).getByText(/“{actor} searched {arg.location}” comes out as “Wren searched cellar”/))
      .toBeTruthy();
    expect(within(container).getByText(/arg:location means the cellar itself/)).toBeTruthy();
  });

  it('says so when there is nothing to put behind a placeholder yet', () => {
    const container = legend({ args: [], globals: [], stats: [] });
    expect(within(container).getAllByText(/none declared yet/).length).toBeGreaterThan(0);
    // With no arguments declared the example still reads as one.
    expect(within(container).getByText('{arg.location}')).toBeTruthy();
  });
});
