import { describe, expect, it } from 'vitest';
import {
  DEFAULT_PREFS, PREFS_KEY, applyTurnEvent, delegateOf, feedFromThread, firstParagraph, isScreenLink,
  lastAnswer, markState, readPrefs, showModeSwitch, writePrefs,
} from '../assistantState';
import { encodeWav, levelOf, resample } from '../wav';
import { embedUrl, isEmbedded, EMBED_FRAME_NAME } from '../../embed';

describe('the personal / service switch', () => {
  it('is offered only where the server has a service thread for this person', () => {
    expect(showModeSwitch({ service_available: true })).toBe(true);
    // single mode: one thread, already the service one
    expect(showModeSwitch({ service_available: false, home: 'default' })).toBe(false);
    expect(showModeSwitch(null)).toBe(false);
  });
});

describe('the transcript', () => {
  it('builds a reply from tokens and replaces it with the final message', () => {
    let feed = [{ k: 'user', text: 'hi' }];
    feed = applyTurnEvent(feed, { type: 'token', token: 'Hel' });
    feed = applyTurnEvent(feed, { type: 'token', token: 'lo' });
    expect(feed[1]).toEqual({ k: 'assistant', text: 'Hello', live: true });
    feed = applyTurnEvent(feed, { type: 'tool_start', tool: 'list_tasks' });
    feed = applyTurnEvent(feed, { type: 'tool_end' });
    feed = applyTurnEvent(feed, { type: 'message', content: 'Two tasks.' });
    expect(feed.map((e) => e.k)).toEqual(['user', 'assistant', 'tool', 'assistant']);
    expect(feed[2].status).toBe('done');
    expect(lastAnswer(feed)).toBe('Two tasks.');
  });

  it('reads a stored thread from its trace, or its messages', () => {
    expect(feedFromThread({ trace: [{ k: 'user', text: 'a' }] })).toEqual([{ k: 'user', text: 'a' }]);
    expect(feedFromThread({ messages: [{ role: 'assistant', content: 'b' }] })).toEqual([{ k: 'assistant', text: 'b' }]);
  });

  it('shows the first paragraph large and keeps the details for the transcript', () => {
    expect(firstParagraph('\nDone, two tasks.\n\n- one\n- two')).toBe('Done, two tasks.');
    expect(lastAnswer([{ k: 'assistant', text: 'old' }, { k: 'user', text: 'new question' }])).toBe('');
  });

  it('opens app routes beside the conversation and names a delegate', () => {
    expect(isScreenLink('/tasks/1')).toBe(true);
    expect(isScreenLink('//evil.example')).toBe(false);
    expect(isScreenLink('https://example.com')).toBe(false);
    expect(delegateOf('{"agent_id": "researcher"}')).toBe('researcher');
    expect(delegateOf({ agent_id: 'writer' })).toBe('writer');
    expect(delegateOf('not json')).toBe('');
  });
});

describe('the live mark', () => {
  it('listens, thinks, waits, speaks and follows the turn', () => {
    expect(markState({ recording: true, busy: true })).toBe('listen');
    expect(markState({ transcribing: true })).toBe('think');
    expect(markState({ busy: true, waiting: true, speaking: true })).toBe('wait');
    // a running tool keeps its scene while the voice says what it is doing
    expect(markState({ busy: true, speaking: true, tool: 'list_tasks' })).toBe('read');
    expect(markState({ busy: true, speaking: true, tool: 'hub_lookup', input: { kind: 'run' } })).toBe('search-history');
    expect(markState({ busy: true, speaking: true, thinking: true })).toBe('speak');
    expect(markState({ busy: true, tool: 'web_search' })).toBe('search-web');
    expect(markState({ busy: true })).toBe('working');
    expect(markState({})).toBe('idle');
  });
});

describe('what the page remembers', () => {
  const store = () => {
    const data = {};
    return { getItem: (k) => data[k] ?? null, setItem: (k, v) => { data[k] = v; }, data };
  };

  it('keeps the mode toggles and the voice, and survives broken storage', () => {
    const s = store();
    expect(readPrefs(s)).toEqual(DEFAULT_PREFS);
    writePrefs({ ...DEFAULT_PREFS, muted: true, voice: 'nova' }, s);
    expect(JSON.parse(s.data[PREFS_KEY]).voice).toBe('nova');
    expect(readPrefs(s).muted).toBe(true);
    expect(readPrefs({ getItem: () => '{oops' })).toEqual(DEFAULT_PREFS);
    expect(readPrefs({ getItem: () => { throw new Error('blocked'); } })).toEqual(DEFAULT_PREFS);
  });
});

describe('a recording as WAV', () => {
  it('resamples to 16 kHz and writes a playable header', () => {
    const samples = resample([new Float32Array(48000).fill(0.5)], 48000);
    expect(samples.length).toBe(16000);
    const bytes = encodeWav(samples);
    expect(String.fromCharCode(...bytes.slice(0, 4))).toBe('RIFF');
    expect(new DataView(bytes.buffer).getUint32(24, true)).toBe(16000);
    expect(bytes.length).toBe(44 + 16000 * 2);
    expect(levelOf(new Uint8Array(64).fill(128))).toBe(0);
  });
});

describe('a page shown beside the assistant', () => {
  it('is drawn alone only inside the named frame', () => {
    const top = {};
    expect(isEmbedded({ self: top, top, name: EMBED_FRAME_NAME })).toBe(false);
    expect(isEmbedded({ self: {}, top, name: EMBED_FRAME_NAME })).toBe(true);
    expect(isEmbedded({ self: {}, top, name: 'other' })).toBe(false);
    expect(embedUrl('/tasks/1', '/agents_hub/demo/')).toBe('/agents_hub/demo/tasks/1');
    expect(embedUrl('/tasks/1', '/')).toBe('/tasks/1');
  });
});
