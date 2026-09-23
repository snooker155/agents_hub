import { describe, expect, it } from 'vitest';
import { fmtKeys, normalizeNode, parseKeys, serializeNode } from '../graphHelpers';

describe('normalizeNode / serializeNode round trip', () => {
  it('keeps an agent node the same shape after normalize then serialize', () => {
    const stored = {
      id: 'n1',
      position: { x: 10, y: 20 },
      type: 'flowNode',
      style: { width: 90 },
      data: {
        label: 'Research', agent_id: 'researcher', domain: 'analysis',
        nodeTask: 'Look things up', input: ['topic'], output: ['findings'],
      },
    };
    const normalized = normalizeNode(stored, () => {});
    expect(normalized.data.label).toBe('Research');
    expect(normalized.data.agent_id).toBe('researcher');
    expect(normalized.data.input).toEqual(['topic']);
    expect(normalized.data.output).toEqual(['findings']);
    // onRunNode is carried on the node so the canvas can dispatch a per-node run.
    expect(typeof normalized.data.onRunNode).toBe('function');

    const serialized = serializeNode(normalized);
    expect(serialized.id).toBe('n1');
    expect(serialized.data.label).toBe('Research');
    expect(serialized.data.agent_id).toBe('researcher');
    expect(serialized.data.domain).toBe('analysis');
    expect(serialized.data.nodeTask).toBe('Look things up');
    expect(serialized.data.input).toEqual(['topic']);
    expect(serialized.data.output).toEqual(['findings']);
    // Policy fields were never set, so they stay out of the payload entirely.
    expect(serialized.data.retry).toBeUndefined();
    expect(serialized.data.timeout_seconds).toBeUndefined();
  });

  it('writes retry/timeout only when one of them is actually set', () => {
    const node = normalizeNode({ id: 'n2', data: { label: 'Node' } }, () => {});
    node.data.retry = { max: 3, backoff_seconds: 1.5 };
    node.data.timeout_seconds = 30;
    const serialized = serializeNode(node);
    expect(serialized.data.retry).toEqual({ max: 3, backoff_seconds: 1.5 });
    expect(serialized.data.timeout_seconds).toBe(30);
  });

  it('keeps entity fields (entity_id, category, config) only when present', () => {
    const withEntity = normalizeNode(
      { id: 'n3', data: { label: 'Condition', entity_id: 'branch', category: 'condition', config: { expr: 'x > 1' } } },
      () => {},
    );
    const serialized = serializeNode(withEntity);
    expect(serialized.data.entity_id).toBe('branch');
    expect(serialized.data.category).toBe('condition');
    expect(serialized.data.config).toEqual({ expr: 'x > 1' });

    const plain = normalizeNode({ id: 'n4', data: { label: 'Agent node', agent_id: 'a1' } }, () => {});
    const serializedPlain = serializeNode(plain);
    expect(serializedPlain.data.entity_id).toBeUndefined();
    // normalizeNode defaults an agent node's category to 'agent', so that
    // default round-trips through serialize too.
    expect(serializedPlain.data.category).toBe('agent');
    expect(serializedPlain.data.config).toBeUndefined();
  });
});

describe('parseKeys / fmtKeys', () => {
  it('parses a comma-separated list, trimming and dropping empties', () => {
    expect(parseKeys('a, b ,, c')).toEqual(['a', 'b', 'c']);
    expect(parseKeys('')).toEqual([]);
  });

  it('formats an array back into a comma-separated string', () => {
    expect(fmtKeys(['a', 'b', 'c'])).toBe('a, b, c');
    expect(fmtKeys([])).toBe('');
    expect(fmtKeys(null)).toBe('');
  });

  it('round-trips through parse then format', () => {
    const text = 'topic, findings, summary';
    expect(fmtKeys(parseKeys(text))).toBe(text);
  });
});
