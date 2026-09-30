import { describe, it, expect } from 'vitest';
import { mapSendError, noStreamPatch } from '../send/errors';

const t = (key) => (key === 'chat.failedToGetResponse' ? 'Failed to get a response.' : key);

describe('mapSendError', () => {
  it('returns null for an aborted request (nothing to show)', () => {
    const err = { name: 'AbortError' };
    expect(mapSendError(err, t)).toBeNull();
  });

  it('prefers the server-provided detail message', () => {
    const err = { response: { data: { detail: 'Agent not found' } }, message: 'Request failed with status code 404' };
    const msg = mapSendError(err, t);
    expect(msg.content).toBe('Agent not found');
    expect(msg.error).toBe(true);
    expect(msg.role).toBe('agent');
    expect(msg.run_id).toBeNull();
    expect(typeof msg.id).toBe('string');
  });

  it('falls back to the error message when there is no server detail', () => {
    const err = { message: 'Network Error' };
    expect(mapSendError(err, t).content).toBe('Network Error');
  });

  it('falls back to the translated generic message when the error carries nothing usable', () => {
    const err = {};
    expect(mapSendError(err, t).content).toBe('Failed to get a response.');
  });

  it('gives every mapped error a distinct id', () => {
    const a = mapSendError({ message: 'x' }, t);
    const b = mapSendError({ message: 'x' }, t);
    expect(a.id).not.toBe(b.id);
  });
});

describe('noStreamPatch', () => {
  it('marks the bubble as an error with the no-output message', () => {
    const tt = (key) => (key === 'chat.noStreamedOutput' ? 'No streamed output received.' : key);
    expect(noStreamPatch(tt)).toEqual({ content: 'No streamed output received.', error: true });
  });
});
