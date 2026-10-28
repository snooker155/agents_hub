import { describe, expect, it } from 'vitest';
import { fmtKeys, normalizeNode, parseKeys, serializeNode, upstreamOutputKeys, wireAgentToSwitch } from '../graphHelpers';

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

describe('normalizeNode keeps an agent node\'s routing', () => {
  it('survives a load and a save', () => {
    const stored = {
      id: 'n1', position: { x: 0, y: 0 },
      data: { label: 'Router', agent_id: 'a', route: 'one', route_default: 'n2', route_hint: 'pick one' },
    };
    const saved = serializeNode(normalizeNode(stored));
    expect(saved.data).toMatchObject({ route: 'one', route_default: 'n2', route_hint: 'pick one' });
  });
});

describe('keys a switch can test', () => {
  const node = (id, data) => ({ id, data: { label: id.toUpperCase(), ...data } });
  const nodes = [
    node('a', { output: ['notes'] }), node('b', { output: ['verdict'] }),
    node('c', { output: ['later'] }), node('s', { entity_id: 'state_switch' }),
  ];
  const edges = [{ source: 'a', target: 'b' }, { source: 'b', target: 's' }, { source: 's', target: 'c' }];

  it('lists what every node upstream writes, not what comes after', () => {
    expect(upstreamOutputKeys('s', nodes, edges)).toEqual([
      { key: 'notes', writers: ['A'] }, { key: 'verdict', writers: ['B'] },
    ]);
  });
});

describe('an agent joined to a switch', () => {
  const agent = (output = []) => ({ id: 'a', data: { category: 'agent', agent_id: 'checker', label: 'Checker', output } });
  const sw = (config = {}) => ({ id: 's', data: { entity_id: 'state_switch', category: 'condition', config } });

  it('gets an output key the conditions with no key then test', () => {
    const blank = { routes: [{ target: 'x', match: 'all', conditions: [{ key: '', op: 'not_empty', value: '' }] }] };
    const out = wireAgentToSwitch([agent(), sw(blank)], 'a', 's');
    expect(out[0].data.output).toEqual(['checker_result']);
    expect(out[1].data.config.routes[0].conditions[0].key).toBe('checker_result');
  });

  it('writes the key the switch already tests, from either shape', () => {
    const routes = { routes: [{ target: 'x', match: 'all', conditions: [{ key: 'verdict', op: 'equals', value: 'ok' }] }] };
    const out = wireAgentToSwitch([agent(), sw(routes)], 'a', 's');
    expect(out[0].data.output).toEqual(['verdict']);
    expect(out[1].data.config).toEqual(routes);
    const legacy = wireAgentToSwitch([agent(), sw({ key: 'score', cases: [{ op: 'gt', value: 3, target: 'x' }] })], 'a', 's');
    expect(legacy[0].data.output).toEqual(['score']);
  });

  it('names the key after the agent when the switch has no conditions yet', () => {
    const out = wireAgentToSwitch([agent(), sw()], 'a', 's');
    expect(out[0].data.output).toEqual(['checker_result']);
    expect(out[1].data.config).toEqual({});
  });

  it('leaves an agent that already writes a key, and other joins, as they are', () => {
    const written = [agent(['notes']), sw()];
    expect(wireAgentToSwitch(written, 'a', 's')).toBe(written);
    const reversed = [agent(), sw()];
    expect(wireAgentToSwitch(reversed, 's', 'a')).toBe(reversed);
  });
});
