import { describe, it, expect } from 'vitest';
import { applyOp, foldOps, opLabel } from '../opsClient';

describe('applyOp', () => {
  it('adds a value at a dotted path, creating the objects along the way', () => {
    expect(applyOp({}, { op: 'add', path: 'spec.nodes.n1', value: { label: 'A' } }))
      .toEqual({ spec: { nodes: { n1: { label: 'A' } } } });
  });

  it('updates an existing leaf without disturbing its siblings', () => {
    const doc = { spec: { nodes: { n1: { label: 'A' }, n2: { label: 'B' } } } };
    const next = applyOp(doc, { op: 'update', path: 'spec.nodes.n1', value: { label: 'A2' } });
    expect(next.spec.nodes).toEqual({ n1: { label: 'A2' }, n2: { label: 'B' } });
  });

  it('removes a leaf', () => {
    const doc = { spec: { nodes: { n1: 1, n2: 2 } } };
    expect(applyOp(doc, { op: 'remove', path: 'spec.nodes.n1' }).spec.nodes).toEqual({ n2: 2 });
  });

  it('empties a keyed map with clear', () => {
    const doc = { spec: { nodes: { n1: 1 }, title: 'keep me' } };
    const next = applyOp(doc, { op: 'clear', path: 'spec.nodes' });
    expect(next.spec.nodes).toEqual({});
    expect(next.spec.title).toBe('keep me');
  });

  it('resets the whole spec when clear carries no path', () => {
    const doc = { spec: { nodes: { n1: 1 } }, meta: { id: 'v1' } };
    const next = applyOp(doc, { op: 'clear', path: '' });
    expect(next.spec).toEqual({});
    expect(next.meta).toEqual({ id: 'v1' });
  });

  it('treats a whitespace-only path as no path', () => {
    expect(applyOp({ spec: { a: 1 } }, { op: 'clear', path: '   ' }).spec).toEqual({});
  });

  it('never leaves the source document mutated', () => {
    const doc = { spec: { nodes: { n1: { label: 'A' } } } };
    const snapshot = structuredClone(doc);
    applyOp(doc, { op: 'add', path: 'spec.nodes.n2', value: { label: 'B' } });
    applyOp(doc, { op: 'remove', path: 'spec.nodes.n1' });
    expect(doc).toEqual(snapshot);
  });

  it('does not build a path just to remove or clear something that is not there', () => {
    const next = applyOp({ spec: {} }, { op: 'remove', path: 'spec.nodes.n1' });
    expect(next).toEqual({ spec: {} });
    expect(applyOp({}, { op: 'clear', path: 'spec.nodes' })).toEqual({});
  });

  it('ignores an op with no path at all', () => {
    const doc = { spec: { a: 1 } };
    expect(applyOp(doc, { op: 'add', value: 1 })).toEqual(doc);
    expect(applyOp(doc, {})).toEqual(doc);
  });

  it('overwrites a scalar standing where a container must go', () => {
    const next = applyOp({ spec: 'oops' }, { op: 'add', path: 'spec.nodes.n1', value: 1 });
    expect(next.spec).toEqual({ nodes: { n1: 1 } });
  });
});

describe('foldOps', () => {
  it('replays a stream of ops in order', () => {
    const doc = foldOps({}, [
      { op: 'add', path: 'spec.nodes.n1', value: { label: 'A' } },
      { op: 'add', path: 'spec.nodes.n2', value: { label: 'B' } },
      { op: 'update', path: 'spec.nodes.n1', value: { label: 'A2' } },
      { op: 'remove', path: 'spec.nodes.n2' },
    ]);
    expect(doc).toEqual({ spec: { nodes: { n1: { label: 'A2' } } } });
  });

  it('returns the base unchanged for an empty or missing op list', () => {
    expect(foldOps({ spec: { a: 1 } }, [])).toEqual({ spec: { a: 1 } });
    expect(foldOps({ spec: { a: 1 } }, null)).toEqual({ spec: { a: 1 } });
    expect(foldOps(null, [])).toEqual({});
  });

  it('does not mutate the base document', () => {
    const base = { spec: { nodes: {} } };
    foldOps(base, [{ op: 'add', path: 'spec.nodes.n1', value: 1 }]);
    expect(base).toEqual({ spec: { nodes: {} } });
  });
});

describe('opLabel', () => {
  it('labels each op kind with its sign and the tail of the path', () => {
    expect(opLabel({ op: 'add', path: 'spec.nodes.n1' })).toBe('+ nodes.n1');
    expect(opLabel({ op: 'update', path: 'spec.nodes.n1' })).toBe('~ nodes.n1');
    expect(opLabel({ op: 'remove', path: 'spec.nodes.n1' })).toBe('− nodes.n1');
    expect(opLabel({ op: 'clear', path: 'spec.nodes' })).toBe('⟲ spec.nodes');
  });

  it('marks an unknown op kind rather than dropping it', () => {
    expect(opLabel({ op: 'wat', path: 'spec.x' })).toBe('? spec.x');
    expect(opLabel(undefined)).toBe('? ');
  });
});
