import { describe, it, expect } from 'vitest';
import { inTranscriptOrder } from '../useChatProcess';

describe('inTranscriptOrder', () => {
  const ids = new Set(['r1', 'r2', 'r3']);

  it('puts runs in the order of their turns, whatever order they were fetched in', () => {
    const fetched = [{ run_id: 'r3' }, { run_id: 'r1' }, { run_id: 'r2' }];
    expect(inTranscriptOrder(fetched, ids).map((r) => r.run_id)).toEqual(['r1', 'r2', 'r3']);
  });

  it('keeps a run the transcript does not hold yet at the end, and several rows of one run together', () => {
    const list = [
      { run_id: 'live', n: 1 }, { run_id: 'r2', n: 2 }, { run_id: 'r1', n: 3 }, { run_id: 'r2', n: 4 }, { message_id: 'm', n: 5 },
    ];
    expect(inTranscriptOrder(list, ids).map((r) => r.n)).toEqual([3, 2, 4, 1, 5]);
  });
});
