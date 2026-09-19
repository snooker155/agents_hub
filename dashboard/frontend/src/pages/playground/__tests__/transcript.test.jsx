import React from 'react';
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { I18nProvider } from '../../../i18n';
import { ScenarioTranscript, EventFeed } from '../transcript';
import { eventLines } from '../events';

const TICKS = [
  {
    tick: 0,
    decisions: [
      { agent: 'Mira', reasoning: 'The ask is thin, I should lift it.',
        action: { action: 'submit_order', args: { side: 'buy', price: 101, qty: 5 } },
        triggers: ['tick'], duration_ms: 1200, cost: 0.0012, observation: { cash: 100 },
        inbound_tokens: 10, outbound_tokens: 4, run_id: 'run_1' },
      { agent: 'Old Tam', reasoning: 'Ask the newcomer what they carry.',
        action: { action: 'speak_to', args: { agent: 'Mira', text: 'What is in that bag?' } },
        triggers: ['message'], duration_ms: 900, cost: 0.0009, observation: {} },
    ],
    resolutions: [
      { agent: 'Mira', action: 'submit_order', args: {}, ok: false, message: 'not enough cash' },
      { agent: 'Old Tam', action: 'speak_to', args: {}, ok: true, message: 'message queued' },
    ],
    events: ['trade: 5 @ 101'], idle: ['Silent Bo'], frame: {}, cost: 0.002,
  },
];

const wrap = (ui) => render(
  <MemoryRouter><I18nProvider>{ui}</I18nProvider></MemoryRouter>,
);

describe('scenario transcript', () => {
  it('renders a turn as speech + action + resolution', () => {
    wrap(<ScenarioTranscript ticks={TICKS} cursor={0} activation="synchronous" />);
    // Her own heading, and again as the agent Old Tam addressed — a name is
    // painted wherever it appears, which is the point of painting it.
    expect(screen.getAllByText('Mira').length).toBeGreaterThan(1);
    expect(screen.getByText('What is in that bag?')).toBeTruthy();
    expect(screen.getByText('not enough cash')).toBeTruthy();
    expect(screen.getByText('submit_order')).toBeTruthy();
    // synchronous: the tick is a real boundary and gets a divider
    expect(screen.getAllByText('tick 0').length).toBeGreaterThan(0);
    expect(screen.getByText(/Idle this tick/)).toBeTruthy();
  });

  it('stamps each turn with its tick when triggered', () => {
    wrap(<ScenarioTranscript ticks={TICKS} cursor={0} activation="triggered" />);
    expect(screen.getAllByText('tick 0').length).toBe(2);
  });

  it('shows in-flight turns only while following', () => {
    const live = [{ tick: 1, agent: 'Mira', reasoning: '', action: { action: 'hold', args: {} }, observation: {} }];
    const { unmount } = wrap(
      <ScenarioTranscript ticks={TICKS} cursor={0} inFlight={live} following />,
    );
    expect(screen.getByText('hold')).toBeTruthy();
    unmount();
    wrap(<ScenarioTranscript ticks={TICKS} cursor={0} inFlight={live} following={false} />);
    expect(screen.queryByText('hold')).toBeNull();
  });

  it('puts world events and refusals in the event log', () => {
    wrap(<EventFeed ticks={TICKS} cursor={0} />);
    expect(screen.getByText('trade: 5 @ 101')).toBeTruthy();
    expect(screen.getByText(/Mira: submit_order/)).toBeTruthy();
  });

  it('does not paint a refusal as an alarm', () => {
    wrap(<EventFeed ticks={TICKS} cursor={0} />);
    // A refused action is an ordinary outcome of a simulation, so the log reads
    // in the same muted tone as any other line.
    const line = screen.getByText(/Mira: submit_order/).closest('div');
    expect(line.className).not.toMatch(/red/);
  });

  it('reads newest first, in both panes', () => {
    // Two ticks, so "newest first" is something the DOM can be asked about.
    const two = [
      TICKS[0],
      { tick: 1, decisions: [{ agent: 'Mira', reasoning: '', action: { action: 'cancel_order', args: {} }, observation: {} }],
        resolutions: [], events: ['trade: 1 @ 99'], idle: [], frame: {} },
    ];
    const { unmount } = wrap(<ScenarioTranscript ticks={two} cursor={1} activation="synchronous" />);
    expect(screen.getAllByText(/^tick \d+$/).map((el) => el.textContent)[0]).toBe('tick 1');
    unmount();

    wrap(<EventFeed ticks={two} cursor={1} />);
    const newest = screen.getByText('trade: 1 @ 99');
    const oldest = screen.getByText('trade: 5 @ 101');
    expect(newest.compareDocumentPosition(oldest) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('says a started run is under way rather than that nothing has run', () => {
    // Between pressing Run and the first tick there is nothing to render but
    // the fact that it is running — and the empty state must not read as "you
    // have not started anything".
    wrap(<ScenarioTranscript ticks={[]} cursor={0} status="running" />);
    expect(screen.getByText(/waiting for the first tick/i)).toBeTruthy();
    expect(screen.queryByText(/Run the scenario/i)).toBeNull();
  });

  it('says the scenario is being launched before the model server answers', () => {
    // The gap between pressing Run and the first model reply is the longest
    // silence on the page, and it is not the same silence as a live run whose
    // agents are thinking — a model server that never answers stops here.
    wrap(<ScenarioTranscript ticks={[]} cursor={0} status="starting" />);
    expect(screen.getByText(/nothing has come back from the model server/i)).toBeTruthy();
    expect(screen.queryByText(/Run the scenario/i)).toBeNull();
  });

  it('says it is being stopped while the calls in flight are cut', () => {
    wrap(<ScenarioTranscript ticks={[]} cursor={0} status="stopping" />);
    expect(screen.getByText(/Stopping/i)).toBeTruthy();
  });

  it('says what it is waiting for when a triggered world is idle from the start', () => {
    wrap(<ScenarioTranscript ticks={[]} cursor={0} status="running"
                             activation="triggered" waitingForTrigger />);
    // Waiting to be poked is not the same as waiting for a slow model.
    expect(screen.queryByText(/waiting for the first tick/i)).toBeNull();
  });

  it('still says nothing has run when nothing has', () => {
    wrap(<ScenarioTranscript ticks={[]} cursor={0} />);
    expect(screen.getByText(/Run the scenario/i)).toBeTruthy();
  });

  it('gives a woken agent a bubble before it has answered', () => {
    // A tick is as slow as its slowest agent; the page has to show work in
    // progress rather than nothing at all until the whole tick is written.
    wrap(<ScenarioTranscript
      ticks={[]} cursor={0} status="running" following
      activity={[{ tick: 0, agent: 'Mira', triggers: ['message'],
                   messages: [{ from: 'Old Tam', text: 'What is in that bag?' }],
                   reasoning: 'Half a thou', action: '' }]}
    />);
    expect(screen.getByText('Mira')).toBeTruthy();
    expect(screen.getByText('Half a thou')).toBeTruthy();
    // What it was woken to read is shown with it.
    expect(screen.getByText('What is in that bag?')).toBeTruthy();
    // …instead of the "nothing yet" placeholder.
    expect(screen.queryByText(/waiting for the first tick/i)).toBeNull();
  });

  it('names the action as soon as the agent has written it', () => {
    wrap(<ScenarioTranscript
      ticks={[]} cursor={0} status="running" following
      activity={[{ tick: 3, agent: 'Mira', triggers: [], messages: [],
                   reasoning: 'r', action: 'submit_order' }]}
    />);
    expect(screen.getByText(/submitting submit_order/)).toBeTruthy();
  });

  it('drops the working bubbles when scrubbed off the newest tick', () => {
    wrap(<ScenarioTranscript
      ticks={TICKS} cursor={0} following={false}
      activity={[{ tick: 1, agent: 'Mira', triggers: [], messages: [],
                   reasoning: 'later', action: '' }]}
    />);
    expect(screen.queryByText('later')).toBeNull();
  });

  it('shows the mail a turn answers, and says the spoken line last', () => {
    const heard = [{
      tick: 0,
      decisions: [{
        agent: 'Mira', reasoning: 'Answer him.',
        action: { action: 'speak_to', args: { agent: 'Old Tam', text: 'Only rope.' } },
        observation: { messages: [{ from: 'Old Tam', text: 'What is in that bag?' }] },
        triggers: ['message'],
      }],
      resolutions: [], events: [], idle: [], frame: {},
    }];
    wrap(<ScenarioTranscript ticks={heard} cursor={0} activation="triggered" />);
    const mail = screen.getByText('What is in that bag?');
    const thought = screen.getByText('Thought');
    const call = screen.getByText('speak_to');
    const said = screen.getByText('Only rope.');
    // What it heard, what it thought, what it called — and the reply last.
    for (const [before, after] of [[mail, thought], [thought, call], [call, said]]) {
      expect(before.compareDocumentPosition(after) & Node.DOCUMENT_POSITION_FOLLOWING)
        .toBeTruthy();
    }
  });

  it('paints every name in the colour that name always has', () => {
    // Following who is talking to whom is the whole reading; matching a name
    // to a bubble letter by letter is what made a busy tick unreadable.
    const heard = [{
      tick: 0,
      decisions: [{
        agent: 'Mira',
        action: { action: 'speak_to', args: { agent: 'Old Tam', text: 'Only rope.' } },
        observation: { messages: [{ from: 'Old Tam', text: 'What is in that bag?' }] },
        triggers: ['message'],
      }],
      resolutions: [], events: [], idle: [], frame: {},
    }];
    wrap(<ScenarioTranscript ticks={heard} cursor={0} activation="triggered" />);
    // The sender of the mail he sent, and the addressee of the reply — one
    // tone, wherever the name is addressed to somebody. (Not inside the
    // call's arguments: that line is machinery and stays grey.)
    const painted = screen.getAllByText('Old Tam');
    expect(painted.length).toBe(2);
    const tones = new Set(painted.map((el) => el.className.match(/text-\w+-900/)?.[0]));
    expect(tones.size).toBe(1);
    expect([...tones][0]).toBeTruthy();
    // And it is not the tone of the agent whose turn it is.
    const speaker = screen.getByText('Mira').className.match(/text-\w+-900/)?.[0];
    expect(speaker).not.toBe([...tones][0]);
  });

  it('gives the reply the only filled surface in a turn', () => {
    // The hierarchy was upside down: mail, thought and tool call were each a
    // tinted card while the reply was white on a white page. Colour now means
    // "the agent said this", and everything else is grey.
    const heard = [{
      tick: 0,
      decisions: [{
        agent: 'Mira', reasoning: 'Answer him.',
        action: { action: 'speak_to', args: { agent: 'Old Tam', text: 'Only rope.' } },
        observation: { messages: [{ from: 'Old Tam', text: 'What is in that bag?' }] },
        triggers: ['message'],
      }],
      resolutions: [], events: [], idle: [], frame: {},
    }];
    wrap(<ScenarioTranscript ticks={heard} cursor={0} activation="triggered" />);

    const bubble = screen.getByText('Only rope.').parentElement;
    expect(bubble.className).toMatch(/bg-\w+-50/);

    // What it was handed is a rule in the margin, not a card.
    const mail = screen.getByText('What is in that bag?').closest('.border-l-2');
    expect(mail).toBeTruthy();
    expect(mail.className).not.toMatch(/bg-/);

    // The machinery around the reply carries no fill of its own.
    const thought = screen.getByText('Thought').closest('div');
    expect(thought.className).not.toMatch(/bg-/);
    const call = screen.getByText('speak_to').closest('div');
    expect(call.className).not.toMatch(/bg-/);
  });

  it('still paints a refusal, because the world saying no is news', () => {
    wrap(<ScenarioTranscript ticks={TICKS} cursor={0} activation="synchronous" />);
    const refused = screen.getByText('not enough cash').closest('div');
    expect(refused.className).toMatch(/red/);
  });

  it('counts every line the world wrote', () => {
    expect(eventLines(TICKS, 0).map((l) => l.kind)).toEqual(['event', 'refused']);
    expect(eventLines(TICKS, -1)).toEqual([]);
  });
});
