import { describe, it, expect } from 'vitest';
import { withNoticeFiles } from '../noticeFiles';

describe('files of a background run', () => {
  it('are listed under the reply to its notice, after none of the reply’s own', () => {
    const notice = { id: 'n', role: 'system', notice: { type: 'background_run_finished', files: [
      { op: 'add', path: 'a.md', url: '/artifacts?file=1', run_id: 'r1' },
      { op: 'modify', path: 'b.md', run_id: 'r2' },
    ] } };
    const reply = { id: 'a', role: 'agent', content: 'done', files: [{ op: 'modify', path: 'b.md', url: '/x' }] };
    const out = withNoticeFiles([{ id: 'u', role: 'user', content: 'go' }, notice, reply]);
    expect(out[2].files.map((f) => f.path)).toEqual(['a.md', 'b.md']);
    expect(out[2].files[1].url).toBe('/x');
  });

  it('leaves a transcript without such notices as it is', () => {
    const messages = [{ id: 'u', role: 'user', content: 'hi' }];
    expect(withNoticeFiles(messages)).toBe(messages);
  });
});
