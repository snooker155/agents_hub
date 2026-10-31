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
            <Node id={node.id} data={node.data} selected={Boolean(node.selected)} />
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
  containers: [
    { name: 'agents-hub-node-inst_1', agent_id: 'writer', state: 'running', worker: 'wk-1',
      owner_kind: 'instance', owner_id: 'inst_1', workspace: 'acme', host: 'daemon' },
    { name: 'agents-hub-node-inst_old', agent_id: 'writer', state: 'running', worker: 'wk-old' },
    { name: 'lucid_poitras', agent_id: 'basic_agent', state: 'running', worker: '' },
  ],
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

describe('buildGraph on a big cluster', () => {
  const many = (n, role, extra = {}) => Array.from({ length: n }, (_, i) => ({
    member_id: `${role}-${i}`, role, host: `h${i % 2}`, status: 'live', load: {}, ...extra }));
  const g = buildGraph({
    members: [...many(9, 'api'), ...many(10, 'worker'), ...many(5, 'all', { status: 'stale' }).map((m, i) => ({ ...m, member_id: `old-${i}` }))],
    queue: {}, stack: [],
  }, t);
  const ys = g.nodes.filter((n) => n.type === 'svc').map((n) => n.position.y);
  const xs = g.nodes.filter((n) => n.type === 'svc').map((n) => n.position.x);

  it('wraps long columns so the map grows across, not down', () => {
    expect(Math.max(...ys) - Math.min(...ys)).toBeLessThan(4 * 100);
    expect(Math.max(...xs) - Math.min(...xs)).toBeGreaterThan(Math.max(...ys) - Math.min(...ys));
  });

  it('folds stale members into one card', () => {
    const ids = g.nodes.map((n) => n.id);
    expect(ids).toContain('stale:api');
    expect(ids.filter((id) => id.startsWith('m:old-'))).toEqual([]);
    expect(g.nodes.find((n) => n.id === 'stale:api').data.detail).toHaveProperty('old-0');
  });

  it('keeps the queue right of every api and left of every worker', () => {
    const x = (id) => g.nodes.find((n) => n.id === id).position.x;
    const apiXs = g.nodes.filter((n) => n.id.startsWith('m:api-')).map((n) => n.position.x);
    const workerXs = g.nodes.filter((n) => n.id.startsWith('m:worker-')).map((n) => n.position.x);
    expect(Math.max(...apiXs)).toBeLessThan(x('queue'));
    expect(Math.min(...workerXs)).toBeGreaterThan(x('queue'));
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

  it('draws each agent container beside the worker that started it', () => {
    const g = buildGraph(MAP, t);
    const box = g.nodes.find((n) => n.id === 'c:agents-hub-node-inst_1');
    expect(box.data.kind).toBe('container');
    expect(box.data.title).toBe('writer');
    expect(g.edges.some((e) => e.source === 'm:wk-1' && e.target === 'c:agents-hub-node-inst_1')).toBe(true);
    // Not drawn: a container of a stopped worker, and one nobody launched.
    expect(g.nodes.find((n) => n.id === 'c:agents-hub-node-inst_old')).toBeUndefined();
    expect(g.nodes.find((n) => n.id === 'c:lucid_poitras')).toBeUndefined();
    const worker = g.nodes.find((n) => n.id === 'm:wk-1');
    expect(box.position.x).toBeGreaterThan(worker.position.x);
  });

  it('marks the picked node and its lines until another is picked', () => {
    show();
    const node = () => screen.getByTestId('node-m:wk-1').firstChild;
    expect(node()).not.toHaveAttribute('data-selected');
    fireEvent.click(screen.getByTestId('node-m:wk-1'));
    expect(node()).toHaveAttribute('data-selected', 'true');
    fireEvent.click(screen.getByTestId('node-s:database'));
    expect(node()).not.toHaveAttribute('data-selected');
    expect(screen.getByTestId('node-s:database').firstChild).toHaveAttribute('data-selected', 'true');
    fireEvent.click(screen.getByTestId('pane'));
    expect(screen.getByTestId('node-s:database').firstChild).not.toHaveAttribute('data-selected');
  });

  it('offers the log of a picked member or container, not of a service', () => {
    const onViewLogs = vi.fn();
    render(<I18nProvider><ServiceMap map={MAP} onViewLogs={onViewLogs} /></I18nProvider>);
    fireEvent.click(screen.getByTestId('node-m:wk-1'));
    fireEvent.click(screen.getByRole('button', { name: /log/i }));
    expect(onViewLogs).toHaveBeenLastCalledWith({ member: expect.objectContaining({ member_id: 'wk-1' }) });
    fireEvent.click(screen.getByTestId('node-c:agents-hub-node-inst_1'));
    fireEvent.click(screen.getByRole('button', { name: /log/i }));
    expect(onViewLogs).toHaveBeenLastCalledWith({ container: expect.objectContaining({ name: 'agents-hub-node-inst_1' }) });
    fireEvent.click(screen.getByTestId('node-s:database'));
    expect(screen.queryByRole('button', { name: /log/i })).toBeNull();
  });
});
