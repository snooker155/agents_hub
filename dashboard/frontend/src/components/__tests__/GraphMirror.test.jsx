import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import GraphMirror from '../GraphMirror';
import { I18nProvider } from '../../i18n';

// A picture of a graph this hub does not run. What matters is that it draws
// what the agent reported, survives the shapes real graphs have (branches,
// loops, terminals), and says why it is empty when it is.

const show = (props) => render(<I18nProvider><GraphMirror {...props} /></I18nProvider>);

const graph = {
  framework: 'langgraph',
  nodes: [
    { id: '__start__', label: '__start__', kind: 'terminal' },
    { id: 'triage', label: 'triage', kind: 'node' },
    { id: 'pricing', label: 'pricing', kind: 'node' },
    { id: 'answer', label: 'answer', kind: 'node' },
  ],
  edges: [
    { source: '__start__', target: 'triage' },
    { source: 'triage', target: 'pricing', conditional: true },
    { source: 'triage', target: 'answer', conditional: true },
    { source: 'pricing', target: 'answer' },
  ],
};

describe('GraphMirror', () => {
  it('draws every node the agent reported', () => {
    const { container } = show({ topology: graph });
    ['triage', 'pricing', 'answer'].forEach((name) => {
      expect(screen.getByText(name)).toBeInTheDocument();
    });
    expect(container.querySelectorAll('path[marker-end]')).toHaveLength(4);
  });

  it('marks a conditional edge as one that may not be taken', () => {
    const { container } = show({ topology: graph });
    const dashed = [...container.querySelectorAll('path[stroke-dasharray]')];
    expect(dashed).toHaveLength(2);
  });

  it('lays a branch out below what it branches from', () => {
    const { container } = show({ topology: graph });
    const y = (label) => {
      const text = [...container.querySelectorAll('text')].find((t) => t.textContent === label);
      return Number(text.getAttribute('y'));
    };
    expect(y('triage')).toBeLessThan(y('pricing'));
    expect(y('pricing')).toBeLessThan(y('answer'));
  });

  it('draws a graph that loops instead of refusing it', () => {
    // A cycle has no topological order; an agent loop is a cycle, so the one
    // shape most worth looking at must not be the one that fails to render.
    const looping = {
      nodes: [{ id: 'work' }, { id: 'critic' }].map((n) => ({ ...n, label: n.id, kind: 'node' })),
      edges: [
        { source: 'work', target: 'critic' },
        { source: 'critic', target: 'work', conditional: true },
      ],
    };
    show({ topology: looping });
    expect(screen.getByText('work')).toBeInTheDocument();
    expect(screen.getByText('critic')).toBeInTheDocument();
  });

  it('highlights the node that is running now', () => {
    const { container } = show({ topology: graph, activeNode: 'pricing' });
    const active = [...container.querySelectorAll('text')].find((t) => t.textContent === 'pricing');
    expect(active.getAttribute('class')).toContain('fill-white');
  });

  it('explains an empty graph rather than showing an empty box', () => {
    show({ topology: { ok: false, error: 'the service is not running', nodes: [], edges: [] } });
    expect(screen.getByText('the service is not running')).toBeInTheDocument();
  });

  it('renders nothing breakable when there is no topology at all', () => {
    show({ topology: null });
    expect(screen.getByText(/no graph has been fetched/i)).toBeInTheDocument();
  });
});
