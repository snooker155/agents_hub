import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';

import { I18nProvider } from '../../../i18n';

// reactflow needs a layout engine; a stand-in renders the nodes through the
// component's own node types, which is what these tests are about.
vi.mock('reactflow', () => {
  const ReactFlow = ({ nodes, nodeTypes, onNodeClick, onPaneClick }) => (
    <div data-testid="reactflow">
      <div data-testid="pane" onClick={() => onPaneClick?.()} />
      {nodes.map((node) => {
        const Node = nodeTypes[node.type];
        return (
          <div key={node.id} data-testid={`node-${node.id}`} onClick={(e) => onNodeClick?.(e, node)}>
            <Node id={node.id} data={node.data} />
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
    MarkerType: { ArrowClosed: 'arrowclosed' },
    Position: { Top: 'top', Bottom: 'bottom', Left: 'left', Right: 'right' },
  };
});
vi.mock('reactflow/dist/style.css', () => ({}));

import ServiceMap, { buildGraph } from '../ServiceMap';

const t = (k) => k;

const MAP = {
  members: [
    { member_id: 'api-1', role: 'api', host: 'h1', status: 'live', load: { sse_clients: 3, running_runs_on_host: 2 } },
    { member_id: 'wk-1', role: 'worker', host: 'h2', status: 'live', load: { tracked: 2 }, capabilities: { concurrency: 4 } },
    { member_id: 'wk-old', role: 'worker', host: 'h2', status: 'stopped', load: {} },
  ],
  queue: { queued: 2, leased: 0, running: 1, oldest_queued_seconds: 9, items: [] },
  stack: [
    { id: 'database', kind: 'database', label: 'PostgreSQL', status: 'ok', summary: 'PostgreSQL 16', detail: { dialect: 'postgres' },
      load: 5, capacity: 100, unit: 'connections', latency_ms: null },
    { id: 'broker', kind: 'broker', label: 'Redis', status: 'off', summary: 'No broker', detail: {}, load: null, capacity: null, unit: '', latency_ms: null },
    { id: 'models_runtime', kind: 'models_runtime', label: 'Model runtime', status: 'fail', summary: 'down', detail: {}, load: null, capacity: null, unit: '', latency_ms: null },
    { id: 'compose', kind: 'compose', label: 'Compose stack', status: 'ok', summary: '3 of 3', detail: {
      services: [{ service: 'frontend', running: 1, total: 1 }] }, load: 3, capacity: 3, unit: 'containers', latency_ms: null },
  ],
  hub_nodes: [{ id: 'n1', name: 'Mac mini', online: true, capacity: 4, in_flight: 1 }],
  hub_link: { linked: true, url: 'https://hub.example', name: 'Main' },
};

describe('buildGraph', () => {
  const g = buildGraph(MAP, t);
  const ids = g.nodes.map((n) => n.id);

  it('draws live members, the queue, in-use services and leaves stopped and off ones out', () => {
    expect(ids).toEqual(expect.arrayContaining(['people', 'dashboard', 'm:api-1', 'queue', 'm:wk-1', 's:database', 's:models_runtime', 'n:n1', 'remote']));
    expect(ids).not.toContain('m:wk-old');
    expect(ids).not.toContain('s:broker');
    expect(ids).not.toContain('s:compose');
    expect(g.idle.map((c) => c.id)).toEqual(['broker']);
  });

  it('animates the edges that carry traffic', () => {
    const edge = (id) => g.edges.find((e) => e.id === id);
    expect(edge('queue>m:wk-1').animated).toBe(true);
    expect(edge('m:api-1>queue').animated).toBe(true);
    expect(edge('queue>n:n1').animated).toBe(true);
  });

  it('keeps a one-process install small', () => {
    const small = buildGraph({
      members: [{ member_id: 'solo', role: 'all', status: 'live', load: {} }], queue: {},
      stack: [{ id: 'database', kind: 'database', label: 'SQLite', status: 'ok', summary: '', detail: {}, load: null, capacity: null, unit: '' }],
    }, t);
    expect(small.nodes.map((n) => n.id).sort()).toEqual(['m:solo', 'people', 'queue', 's:database']);
  });
});

describe('ServiceMap', () => {
  const show = () => render(<I18nProvider><ServiceMap map={MAP} /></I18nProvider>);

  it('renders nodes for members and stack components with their load', () => {
    show();
    expect(screen.getByText('api-1')).toBeInTheDocument();
    expect(screen.getByText('wk-1')).toBeInTheDocument();
    expect(screen.getByText('Database')).toBeInTheDocument();
    expect(screen.getByText('connections 5/100')).toBeInTheDocument();
    expect(screen.getByText('runs 2/4')).toBeInTheDocument();
    expect(screen.getByText('Mac mini')).toBeInTheDocument();
  });

  it('puts a switched-off service under Not in use, not in the diagram', () => {
    show();
    const chips = screen.getByTestId('not-in-use');
    expect(chips).toHaveTextContent('Redis');
    expect(screen.queryByTestId('node-s:broker')).toBeNull();
  });

  it('shows a failed service with a red dot and opens its details on click', () => {
    show();
    expect(screen.getByTestId('node-s:models_runtime').querySelector('[data-testid="dot-fail"]')).not.toBeNull();
    fireEvent.click(screen.getByTestId('node-s:database'));
    expect(screen.getByTestId('map-detail')).toHaveTextContent('dialect');
    fireEvent.click(screen.getByTestId('pane'));
    expect(screen.queryByTestId('map-detail')).toBeNull();
  });
});
