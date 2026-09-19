import { describe, expect, it } from 'vitest';
import { describeRoute } from '../registry';

/**
 * The floating chat has to know what a page is about before that page has said
 * anything, so the URL alone must answer it. These pin the inverse of the
 * `path_fn` each kind declares in `chat/references.py`: the page an entity
 * links to is the page that hands that entity to the chat.
 */
describe('describeRoute', () => {
  it('hands the record on an entity page to the chat', () => {
    expect(describeRoute('/tasks/t-1')).toEqual({
      scope: 'task:t-1', refs: [{ kind: 'task', id: 't-1' }],
    });
    expect(describeRoute('/views/v-1').refs[0].kind).toBe('view');
    expect(describeRoute('/projects/p-1').refs[0].kind).toBe('project');
    expect(describeRoute('/flows/f-1').refs[0].kind).toBe('flow');
  });

  it('reads the two entities that live in the query string', () => {
    expect(describeRoute('/loops', '?loop=l-1')).toEqual({
      scope: 'loop:l-1', refs: [{ kind: 'loop', id: 'l-1' }],
    });
    expect(describeRoute('/plan', '?tab=jobs&job=j-1')).toEqual({
      scope: 'job:j-1', refs: [{ kind: 'job', id: 'j-1' }],
    });
  });

  it('falls back to the list page itself when nothing is selected', () => {
    expect(describeRoute('/loops')).toEqual({ scope: 'loops', refs: [] });
    expect(describeRoute('/plan')).toEqual({ scope: 'plan', refs: [] });
  });

  it('does not read a playground sub-page as a scenario id', () => {
    expect(describeRoute('/playground/runs').refs).toEqual([]);
    expect(describeRoute('/playground/worlds').refs).toEqual([]);
    expect(describeRoute('/playground/s-1').refs[0]).toEqual({ kind: 'scenario', id: 's-1' });
  });

  it('decodes an id that had to be escaped to fit in the path', () => {
    expect(describeRoute('/agents/my%20agent').refs[0].id).toBe('my agent');
  });

  it('gives every other page a scope of its own', () => {
    // One thread per page: a question about Costs must not come back on Models.
    expect(describeRoute('/costs').scope).toBe('costs');
    expect(describeRoute('/models').scope).toBe('models');
    expect(describeRoute('/').scope).toBe('chat');
  });
});
