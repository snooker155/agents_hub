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
    fireEvent.change(screen.getByTestId('map-agent'), { target: { value: 'checker' } });
    expect(onPatch).toHaveBeenLastCalledWith({ config: { max_items: 50, agent_id: 'checker' } });
    fireEvent.click(screen.getByLabelText(/Keep going when some items fail/));
    expect(onPatch).toHaveBeenLastCalledWith({ config: { max_items: 50, allow_failures: true } });
  });
});

describe('SwitchConfig', () => {
  it('adds a case whose target is one of the next steps', () => {
    const onPatch = vi.fn();
    wrap(<SwitchConfig node={{ id: 's', data: { entity_id: 'state_switch', config: { key: 'score' } } }}
      successors={NEXT} onPatch={onPatch} />);
    fireEvent.click(screen.getByRole('button', { name: 'Add case' }));
    expect(onPatch).toHaveBeenLastCalledWith({
      config: { key: 'score', cases: [{ op: 'equals', value: '', target: '' }] },
    });
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
