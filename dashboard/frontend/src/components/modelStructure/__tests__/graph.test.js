import { describe, it, expect } from 'vitest';
import {
  buildGraph, layerScale, dtypeColorKey, formatBytes, formatCount, DTYPE_KEYS,
} from '../graph';

const STRUCTURE = {
  blocks: [
    { id: 'head', kind: 'head', label: 'Output', parameters: 1000 },
    { id: 'l1', kind: 'layer', index: 1, label: 'Layer 1', parameters: 5e7 },
    { id: 'rope', kind: 'other', label: 'rope freqs', parameters: 64 },
    { id: 'norm', kind: 'norm', label: 'Final norm', parameters: 10 },
    { id: 'l0', kind: 'layer', index: 0, label: 'Layer 0', parameters: 5e7 },
    { id: 'emb', kind: 'embedding', label: 'Embedding', parameters: 1e8 },
  ],
};

describe('buildGraph', () => {
  it('orders embedding, layers, norm, head, then side blocks', () => {
    const { nodes, edges } = buildGraph(STRUCTURE);
    expect(nodes.map((n) => n.id)).toEqual(['emb', 'l0', 'l1', 'norm', 'head', 'rope']);
    expect(nodes.find((n) => n.id === 'rope').side).toBe(true);
    expect(edges).toEqual([
      { source: 'emb', target: 'l0' },
      { source: 'l0', target: 'l1' },
      { source: 'l1', target: 'norm' },
      { source: 'norm', target: 'head' },
    ]);
  });

  it('tolerates missing blocks', () => {
    expect(buildGraph({})).toEqual({ nodes: [], edges: [] });
    expect(buildGraph(null)).toEqual({ nodes: [], edges: [] });
  });
});

describe('layerScale', () => {
  it('stays within 0..1 and keeps small blocks visible', () => {
    const { nodes } = buildGraph(STRUCTURE);
    const scale = layerScale(nodes);
    for (const v of Object.values(scale)) {
      expect(v).toBeGreaterThanOrEqual(0);
      expect(v).toBeLessThanOrEqual(1);
    }
    expect(scale.emb).toBe(1);
    expect(scale.norm).toBeGreaterThan(0.1);
    expect(scale.l0).toBeGreaterThan(scale.head);
  });

  it('gives zero to empty blocks and one to equal blocks', () => {
    expect(layerScale([{ id: 'a', parameters: 0 }])).toEqual({ a: 0 });
    expect(layerScale([{ id: 'a', parameters: 5 }, { id: 'b', parameters: 5 }])).toEqual({ a: 1, b: 1 });
  });
});

describe('dtypeColorKey', () => {
  it.each([
    ['F32', 'f32'], ['float32', 'f32'], ['F16', 'f16'], ['torch.float16', 'f16'],
    ['BF16', 'bf16'], ['torch.bfloat16', 'bf16'], ['Q8_0', 'q8'], ['Q6_K', 'q6'],
    ['Q5_K_M', 'q5'], ['Q4_K_M', 'q4'], ['Q4_0', 'q4'], ['Q3_K_S', 'q3'], ['Q2_K', 'q2'],
    ['IQ3_XXS', 'iq'], ['IQ4_NL', 'iq'], ['I8', 'q8'], ['mystery', 'other'], [null, 'other'],
  ])('%s -> %s', (dtype, key) => {
    expect(dtypeColorKey(dtype)).toBe(key);
    expect(DTYPE_KEYS).toContain(key);
  });
});

describe('formatting', () => {
  it('formats counts and bytes', () => {
    expect(formatCount(1.2e9)).toBe('1.2B');
    expect(formatCount(340e6)).toBe('340M');
    expect(formatCount(512)).toBe('512');
    expect(formatBytes(512)).toBe('512 B');
    expect(formatBytes(1536)).toBe('1.5 KB');
    expect(formatBytes(4 * 1024 ** 3)).toBe('4 GB');
  });
});
