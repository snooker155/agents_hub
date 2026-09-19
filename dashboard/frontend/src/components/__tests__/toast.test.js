import { describe, it, expect } from 'vitest';
import { errorDetail } from '../toast';

describe('errorDetail', () => {
  it('prefers the server-side detail', () => {
    const err = { response: { data: { detail: 'Agent not found' } }, message: 'Request failed' };
    expect(errorDetail(err)).toBe('Agent not found');
  });

  it('joins a FastAPI validation error list', () => {
    const err = { response: { data: { detail: [
      { loc: ['body', 'name'], msg: 'field required' },
      { loc: ['body', 'id'], msg: 'not a valid id' },
    ] } } };
    expect(errorDetail(err)).toBe('field required; not a valid id');
  });

  it('falls back to the transport error when the server never answered', () => {
    expect(errorDetail({ message: 'Network Error' })).toBe('Network Error');
  });

  it('trims surrounding whitespace', () => {
    expect(errorDetail({ response: { data: { detail: '  boom  ' } } })).toBe('boom');
  });

  it('ignores a blank detail and uses the message instead', () => {
    expect(errorDetail({ response: { data: { detail: '   ' } }, message: 'Timeout' }))
      .toBe('Timeout');
  });

  it('returns undefined when there is nothing worth showing', () => {
    expect(errorDetail(undefined)).toBeUndefined();
    expect(errorDetail({})).toBeUndefined();
    expect(errorDetail({ response: { data: {} } })).toBeUndefined();
    expect(errorDetail({ response: { data: { detail: [] } } })).toBeUndefined();
    expect(errorDetail({ message: '   ' })).toBeUndefined();
  });

  it('ignores a non-string detail shape it cannot render', () => {
    expect(errorDetail({ response: { data: { detail: { code: 500 } } }, message: 'x' })).toBe('x');
  });
});
