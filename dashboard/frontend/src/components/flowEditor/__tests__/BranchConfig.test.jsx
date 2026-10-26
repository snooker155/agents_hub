import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { I18nProvider } from '../../../i18n';
import { MapConfig, RouteConfig, SwitchConfig } from '../BranchConfig';
import { serializeNode } from '../graphHelpers';

// Branching decided while a flow runs: a map node (one agent per item), a
// switch on a state value, and an agent node that picks its next step.

const AGENTS = [{ id: 'checker', name: 'Checker' }, { id: 'writer', name: 'Writer' }];
const NEXT = [{ id: 'fix', label: 'Fix' }, { id: 'ship', label: 'Ship' }];
const wrap = (ui) => render(<I18nProvider>{ui}</I18nProvider>);

describe('MapConfig', () => {
  it('picks the agent for each item and writes the task into config', () => {
    const onPatch = vi.fn();
    wrap(<MapConfig node={{ id: 'm', data: { entity_id: 'map', config: { max_items: 50 } } }} agents={AGENTS} onPatch={onPatch} />);
    fireEvent.click(screen.getByTestId('map-agent'));
    fireEvent.click(screen.getByRole('option', { name: /Checker/ }));
    expect(onPatch).toHaveBeenLastCalledWith({ config: { max_items: 50, agent_id: 'checker' } });
    fireEvent.click(screen.getByLabelText(/Keep going when some items fail/));
    expect(onPatch).toHaveBeenLastCalledWith({ config: { max_items: 50, allow_failures: true } });
  });
});

describe('SwitchConfig', () => {
  const UPSTREAM = [{ key: 'verdict', writers: ['Checker'] }];
  const ROUTE = (conditions, extra = {}) => ({ target: 'fix', match: 'all', conditions, ...extra });
  const show = (config, onPatch = vi.fn(), declared = []) => wrap(
    <SwitchConfig node={{ id: 's', data: { entity_id: 'state_switch', config } }}
      successors={NEXT} onPatch={onPatch} stateKeys={{ upstream: UPSTREAM, declared }} />,
  );

  it('adds a next step with one condition, the step picked first', () => {
    const onPatch = vi.fn();
    show({}, onPatch);
    fireEvent.click(screen.getByRole('button', { name: 'Add next step' }));
    expect(onPatch).toHaveBeenLastCalledWith({
      config: { routes: [{ target: '', match: 'all', conditions: [{ key: '', op: 'equals', value: '' }] }] },
    });
  });

  it('gives each condition its own key, offering the request and what the nodes before it write', () => {
    const onPatch = vi.fn();
    const routes = [ROUTE([{ key: '', op: 'equals', value: 'ok' }])];
    show({ routes }, onPatch);
    expect(screen.getByTestId('switch-key-missing')).toBeInTheDocument();
    fireEvent.focus(screen.getByTestId('switch-key-0-0'));
    expect(screen.getByRole('option', { name: /user_request/ })).toBeInTheDocument();
    fireEvent.mouseDown(screen.getByRole('option', { name: /verdict/ }));
    expect(onPatch).toHaveBeenLastCalledWith({
      config: { routes: [ROUTE([{ key: 'verdict', op: 'equals', value: 'ok' }])] },
    });
  });

  it('joins several conditions of one step with and or or', () => {
    const onPatch = vi.fn();
    const conditions = [{ key: 'verdict', op: 'equals', value: 'ok' }, { key: 'score', op: 'gt', value: '3' }];
    show({ routes: [ROUTE(conditions)] }, onPatch, ['score']);
    expect(screen.getByText('and')).toBeInTheDocument();
    fireEvent.change(screen.getByTestId('switch-route-match-0'), { target: { value: 'any' } });
    expect(onPatch).toHaveBeenLastCalledWith({ config: { routes: [ROUTE(conditions, { match: 'any' })] } });
  });

  it('switches a condition off and back on', () => {
    const onPatch = vi.fn();
    const conditions = [{ key: 'verdict', op: 'equals', value: 'ok' }];
    const { unmount } = show({ routes: [ROUTE(conditions)] }, onPatch);
    fireEvent.click(screen.getByTestId('switch-condition-on-0-0'));
    expect(onPatch).toHaveBeenLastCalledWith({
      config: { routes: [ROUTE([{ key: 'verdict', op: 'equals', value: 'ok', enabled: false }])] },
    });
    unmount();
    show({ routes: [ROUTE([{ key: '', op: 'equals', value: '', enabled: false }])] });
    expect(screen.getByText('Switched off, not checked.')).toBeInTheDocument();
    // Only the switch itself stays usable, to turn the condition back on.
    expect(screen.getByTestId('switch-condition-on-0-0').disabled).toBe(false);
    expect(screen.getByTestId('switch-key-0-0').disabled).toBe(true);
    expect(screen.getByRole('button', { name: 'Remove condition' }).disabled).toBe(true);
    for (const control of screen.getByTestId('switch-route-0').querySelectorAll('.bg-slate-50 select, .bg-slate-50 input:not([type=checkbox])')) {
      expect(control.disabled).toBe(true);
    }
    expect(screen.queryByTestId('switch-key-missing')).toBeNull();
    expect(screen.queryByText('Enter the value to compare with.')).toBeNull();
  });

  it('reads the first shape and saves the new one on the first edit', () => {
    const onPatch = vi.fn();
    show({ key: 'verdict', cases: [{ op: 'equals', value: 'ok', target: 'fix' }], default: 'ship' }, onPatch);
    expect(screen.getByTestId('switch-key-0-0').value).toBe('verdict');
    fireEvent.click(screen.getByRole('button', { name: 'Add condition' }));
    expect(onPatch).toHaveBeenLastCalledWith({
      config: {
        default: 'ship',
        routes: [ROUTE([{ key: 'verdict', op: 'equals', value: 'ok' }, { key: '', op: 'equals', value: '' }])],
      },
    });
  });

  it('warns about a key no node before it writes, but not the request, a written or a declared key', () => {
    const { unmount } = show({ routes: [ROUTE([{ key: 'score', op: 'gt', value: '3' }])] });
    expect(screen.getByTestId('switch-key-unwritten')).toBeInTheDocument();
    unmount();
    for (const [key, declared] of [['user_request', []], ['verdict', []], ['topic', ['topic']]]) {
      const view = show({ routes: [ROUTE([{ key, op: 'not_empty' }])] }, vi.fn(), declared);
      expect(screen.queryByTestId('switch-key-unwritten')).toBeNull();
      view.unmount();
    }
  });

  it('warns while it has fewer than two next steps', () => {
    wrap(<SwitchConfig node={{ id: 's', data: { config: {} } }} successors={NEXT.slice(0, 1)} onPatch={vi.fn()} />);
    expect(screen.getByText(/Connect this node to the steps it chooses between/)).toBeInTheDocument();
  });
});

describe('RouteConfig', () => {
  it('lets the agent pick its next step and offers a fallback', () => {
    const onPatch = vi.fn();
    const { rerender } = wrap(<RouteConfig node={{ id: 'a', data: {} }} successors={NEXT} onPatch={onPatch} />);
    fireEvent.change(screen.getByTestId('route-mode'), { target: { value: 'one' } });
    expect(onPatch).toHaveBeenLastCalledWith({ route: 'one' });
    rerender(<I18nProvider><RouteConfig node={{ id: 'a', data: { route: 'one' } }} successors={NEXT} onPatch={onPatch} /></I18nProvider>);
    expect(screen.getByRole('option', { name: 'Ship' })).toBeInTheDocument();
  });

  it('is disabled with fewer than two next steps', () => {
    wrap(<RouteConfig node={{ id: 'a', data: {} }} successors={[]} onPatch={vi.fn()} />);
    expect(screen.getByTestId('route-mode').disabled).toBe(true);
  });

  it('is saved with the node only when set', () => {
    const plain = serializeNode({ id: 'a', position: {}, type: 'flowNode', data: { agent_id: 'x' } });
    expect(plain.data.route).toBeUndefined();
    const routed = serializeNode({
      id: 'a', position: {}, type: 'flowNode',
      data: { agent_id: 'x', route: 'many', route_default: 'fix', route_hint: 'by severity' },
    });
    expect(routed.data).toMatchObject({ route: 'many', route_default: 'fix', route_hint: 'by severity' });
  });
});
