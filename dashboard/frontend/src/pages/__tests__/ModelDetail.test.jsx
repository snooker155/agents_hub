import { render, screen, waitFor, fireEvent, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import { I18nProvider } from '../../i18n';

// Feature 6: the model structure page. reactflow and the three canvas both
// need a real layout engine or WebGL, so each is replaced by a thin stand-in
// that renders what the page hands it: reactflow's nodes through the page's
// own node types, the Canvas's children into a plain div.

const ok = (data) => Promise.resolve({ data });
const notFound = () => Promise.reject({ response: { status: 404 } });

const tensor = (name, dtype, parameters, role = null) => ({
  name, shape: [parameters], dtype, parameters, bytes: parameters * 2, role,
});

const STRUCTURE = {
  kind: 'structure',
  source: 'gguf',
  model: {
    id: 'tiny.gguf', provider: 'local', name: 'Tiny Llama', file: '/m/tiny.gguf',
    file_size_bytes: 3 * 1024 ** 3, architecture: 'llama', parameters: 1.2e9, layers: 3,
    hidden_size: 2048, heads: 32, kv_heads: 4, vocab_size: 32000, context_length: 4096,
    feed_forward_length: 5632, quantization: 'Q4_K_M', dtype: null, modified_at: null, metadata: {},
  },
  blocks: [
    { id: 'emb', kind: 'embedding', label: 'Embedding', index: null, parameters: 6.5e7, bytes: 4e7, quantization: 'Q4_K',
      tensors: [tensor('token_embd.weight', 'Q4_K', 6.5e7, 'embedding')] },
    ...[0, 1, 2].map((i) => ({
      id: `blk.${i}`, kind: 'layer', label: `Layer ${i}`, index: i, parameters: 4e7, bytes: 2.4e7, quantization: 'Q4_K',
      tensors: [
        tensor(`blk.${i}.attn_q.weight`, 'Q4_K', 4e6, 'attn_q'),
        tensor(`blk.${i}.ffn_up.weight`, 'Q6_K', 1.1e7, 'ffn_up'),
        tensor(`blk.${i}.attn_norm.weight`, 'F32', 2048, 'norm'),
      ],
    })),
    { id: 'norm', kind: 'norm', label: 'Output norm', index: null, parameters: 2048, bytes: 8192, quantization: null,
      tensors: [tensor('output_norm.weight', 'F32', 2048)] },
    { id: 'head', kind: 'head', label: 'Output', index: null, parameters: 6.5e7, bytes: 5e7, quantization: 'Q6_K',
      tensors: [tensor('output.weight', 'Q6_K', 6.5e7)] },
  ],
  memory: {
    weights_bytes: 2.5 * 1024 ** 3, by_dtype: {}, by_kind: {},
    kv_cache_bytes_per_token: 22528, kv_cache_bytes_at_context: 88 * 1024 ** 2,
  },
};

const CARD = {
  kind: 'card',
  model: {
    id: 'gpt-x', provider: 'openai', context_window: 128000, input_price: 2.5, output_price: 10,
    cached_input_price: 1.25, released_at: '2025-05-01', enabled: true, default: false, price_source: 'auto',
  },
};

const getModelStructure = vi.fn();
const putMyPreferences = vi.fn(() => notFound());

vi.mock('../../api/modelStructure', () => ({
  getModelStructure: (...a) => getModelStructure(...a),
}));
vi.mock('../../api/palette', () => ({
  getMyPreferences: () => notFound(),
  putMyPreferences: (...a) => putMyPreferences(...a),
}));

vi.mock('reactflow', () => {
  const ReactFlow = ({ nodes, nodeTypes, onNodeClick }) => (
    <div data-testid="reactflow">
      {nodes.map((node) => {
        const Node = nodeTypes[node.type];
        return (
          <div key={node.id} onClick={(e) => onNodeClick?.(e, node)}>
            <Node id={node.id} data={node.data} selected={node.selected} />
          </div>
        );
      })}
    </div>
  );
  return {
    default: ReactFlow,
    Background: () => null,
    Controls: () => null,
    Handle: () => null,
    Position: { Top: 'top', Bottom: 'bottom', Left: 'left', Right: 'right' },
  };
});
vi.mock('reactflow/dist/style.css', () => ({}));

vi.mock('@react-three/fiber', () => ({
  Canvas: ({ children }) => <div data-testid="r3f-canvas">{children}</div>,
}));
vi.mock('@react-three/drei', () => ({
  OrbitControls: () => null,
  Html: ({ children }) => <div>{children}</div>,
}));

import ModelDetail from '../ModelDetail';

const show = (path = '/models/local/tiny.gguf') => render(
  <I18nProvider>
    <MemoryRouter initialEntries={[path]}>
      <Routes><Route path="/models/:provider/:model" element={<ModelDetail />} /></Routes>
    </MemoryRouter>
  </I18nProvider>,
);

describe('ModelDetail', () => {
  beforeEach(() => {
    getModelStructure.mockReset();
    putMyPreferences.mockClear();
    window.localStorage.clear();
    // React renders <mesh> and friends as unknown DOM tags under the mocked
    // Canvas; those warnings are expected here.
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('shows the header stats and the blocks, and selecting a block fills the table', async () => {
    getModelStructure.mockImplementation(() => ok(STRUCTURE));
    show('/models/local/tiny.gguf');

    expect(await screen.findByText('Tiny Llama')).toBeInTheDocument();
    expect(getModelStructure).toHaveBeenCalledWith('local', 'tiny.gguf');
    const tiles = screen.getByTestId('stat-tiles');
    expect(within(tiles).getByText('llama')).toBeInTheDocument();
    expect(within(tiles).getByText('1.2B')).toBeInTheDocument();
    expect(within(tiles).getByText('32 (4 KV)')).toBeInTheDocument();
    expect(within(tiles).getByText('Q4_K_M')).toBeInTheDocument();
    expect(within(tiles).getByText('3 GB')).toBeInTheDocument();
    expect(within(tiles).getByText('88 MB')).toBeInTheDocument();

    for (const id of ['emb', 'blk.0', 'blk.1', 'blk.2', 'norm', 'head']) {
      expect(screen.getByTestId(`block-node-${id}`)).toBeInTheDocument();
    }
    // The first block is selected on arrival.
    expect(screen.getByTestId('block-node-emb')).toHaveAttribute('data-selected', 'true');

    fireEvent.click(screen.getByTestId('block-node-blk.1'));
    expect(screen.getByTestId('block-node-blk.1')).toHaveAttribute('data-selected', 'true');
    const table = screen.getByTestId('tensor-table');
    expect(within(table).getByText('Tensors in Layer 1')).toBeInTheDocument();
    expect(within(table).getByText('blk.1.attn_q.weight')).toBeInTheDocument();
    expect(within(table).getByText('blk.1.ffn_up.weight')).toBeInTheDocument();

    // Sorted by bytes, largest first; the filter narrows the rows.
    const firstRow = within(table).getAllByRole('row')[1];
    expect(within(firstRow).getByText('blk.1.ffn_up.weight')).toBeInTheDocument();
    fireEvent.change(within(table).getByRole('searchbox'), { target: { value: 'attn_q' } });
    expect(within(table).queryByText('blk.1.ffn_up.weight')).not.toBeInTheDocument();
    expect(within(table).getByText('1 of 3 tensors')).toBeInTheDocument();

    expect(screen.getByTestId('dtype-legend')).toBeInTheDocument();
  });

  it('keeps the selection when switching to 3D and stores the choice', async () => {
    getModelStructure.mockImplementation(() => ok(STRUCTURE));
    show();
    await screen.findByTestId('block-node-head');
    fireEvent.click(screen.getByTestId('block-node-head'));

    fireEvent.click(screen.getByRole('button', { name: '3D' }));
    expect(await screen.findByTestId('structure-3d')).toBeInTheDocument();
    expect(screen.queryByTestId('structure-2d')).not.toBeInTheDocument();
    expect(screen.getByText('Tensors in Output')).toBeInTheDocument();
    expect(screen.getByText('output.weight')).toBeInTheDocument();

    expect(window.localStorage.getItem('agents_hub_model_view')).toBe('3d');
    expect(putMyPreferences).toHaveBeenCalledWith({ model_view: '3d' });

    fireEvent.click(screen.getByRole('button', { name: '2D' }));
    expect(await screen.findByTestId('block-node-head')).toHaveAttribute('data-selected', 'true');
    expect(window.localStorage.getItem('agents_hub_model_view')).toBe('2d');
  });

  it('opens in the stored view', async () => {
    window.localStorage.setItem('agents_hub_model_view', '3d');
    getModelStructure.mockImplementation(() => ok(STRUCTURE));
    show();
    expect(await screen.findByTestId('structure-3d')).toBeInTheDocument();
  });

  it('renders the card for an API model', async () => {
    getModelStructure.mockImplementation(() => ok(CARD));
    show('/models/openai/gpt-x');
    const card = await screen.findByTestId('model-card');
    expect(within(card).getByText('$2.5 per 1M tokens')).toBeInTheDocument();
    expect(within(card).getByText('$10 per 1M tokens')).toBeInTheDocument();
    expect(within(card).getByText('$1.25 per 1M tokens')).toBeInTheDocument();
    expect(within(card).getByText('128K tokens')).toBeInTheDocument();
    expect(screen.getByText(/structure of an API model cannot be read/)).toBeInTheDocument();
    expect(screen.queryByRole('group', { name: 'View' })).not.toBeInTheDocument();
  });

  it('shows the error detail and survives an empty block list', async () => {
    getModelStructure.mockImplementationOnce(() => Promise.reject({ response: { status: 404, data: { detail: 'No such model' } } }));
    show();
    expect(await screen.findByRole('alert')).toHaveTextContent('No such model');

    getModelStructure.mockImplementationOnce(() => ok({ ...STRUCTURE, blocks: [] }));
    fireEvent.click(screen.getByRole('button', { name: /Try again/ }));
    expect(await screen.findByText('The file was read but no blocks were found in it.')).toBeInTheDocument();
    expect(screen.getByTestId('stat-tiles')).toBeInTheDocument();
  });

  it('decodes an encoded model segment', async () => {
    getModelStructure.mockImplementation(() => ok(CARD));
    show('/models/ollama/library%2Fllama3%3A8b');
    await waitFor(() => expect(getModelStructure).toHaveBeenCalledWith('ollama', 'library/llama3:8b'));
  });
});
