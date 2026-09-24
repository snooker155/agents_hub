import React from 'react';
import { describe, it, expect } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { I18nProvider } from '../../../i18n';
import WorldView, { LabView } from '../renderers';

const wrap = (ui) => render(<I18nProvider>{ui}</I18nProvider>);

// A lab frame as playground/environments/lab.py emits it: a root hypothesis
// with a child, and a grandchild under that, experiments on the root.
const FRAME = {
  renderer: 'lab',
  tick: 3,
  question: 'Is the sample mean within 0.01 of 0.5?',
  budget: { experiments_used: 3, experiments_max: 12 },
  stop_reason: '',
  hypotheses: [
    {
      id: 'H1', text: 'The claim holds for every n', status: 'testing', depth: 0,
      parent: null, author: 'Theo', decided_by: '', decision_note: '',
      experiments: ['E1'],
      children: [
        {
          id: 'H2', text: 'It fails for small n', status: 'confirmed', depth: 1,
          parent: 'H1', author: 'Crit', decided_by: 'Lead', decision_note: 'n=10 misses',
          experiments: [],
          children: [
            {
              id: 'H3', text: 'Below n=9604 coverage drops', status: 'needs_repeat', depth: 2,
              parent: 'H2', author: 'Theo', decided_by: 'Lead', decision_note: '',
              experiments: [], children: [],
            },
          ],
        },
      ],
    },
    {
      id: 'H4', text: 'A rival claim', status: 'refuted', depth: 0, parent: null,
      author: 'Theo', decided_by: 'Lead', decision_note: '', experiments: [], children: [],
    },
  ],
  experiments: [
    {
      id: 'E1', hypothesis_id: 'H1', author: 'Exp', status: 'done', seed: 42,
      design: 'draw n uniforms 2000 times', metrics: { coverage: 0.9731, n: 10000 },
      result: { coverage: 0.9731 }, analysis: 'holds at n=10000',
      critiques: [{ author: 'Crit', text: 'one seed only' }],
    },
  ],
  report: [{ section: 'Abstract', text: 'We tested the claim.' }],
  formulas: [],
  datasets: {},
};

describe('LabView', () => {
  it('is what WorldView draws for a lab frame', () => {
    wrap(<WorldView frame={FRAME} />);
    expect(screen.getByText(FRAME.question)).toBeInTheDocument();
    expect(screen.getByTestId('lab-budget')).toHaveTextContent('3 of 12 runs');
  });

  it('nests the hypothesis tree and shows a status chip per node', () => {
    wrap(<LabView frame={FRAME} />);
    const nodes = screen.getAllByTestId('lab-hypothesis');
    expect(nodes.map((n) => n.getAttribute('data-depth'))).toEqual(['0', '1', '2', '0']);
    // The grandchild is rendered inside its parent's list item, not beside it.
    const child = nodes[1];
    expect(within(child).getByText('Below n=9604 coverage drops')).toBeInTheDocument();
    const chips = screen.getAllByTestId('lab-status').map((c) => c.textContent);
    expect(chips).toEqual(['testing', 'confirmed', 'needs repeat', 'refuted']);
    // Colour follows status.
    const refuted = screen.getAllByTestId('lab-status')[3];
    expect(refuted.className).toContain('text-red-700');
    expect(screen.getAllByTestId('lab-status')[2].className).toContain('text-amber-700');
  });

  it('expands a node to its experiments with seed, metrics and the latest critique', () => {
    wrap(<LabView frame={FRAME} />);
    expect(screen.queryByText('E1')).not.toBeInTheDocument();
    fireEvent.click(screen.getByText('The claim holds for every n'));
    expect(screen.getByText('E1')).toBeInTheDocument();
    expect(screen.getByText('seed 42')).toBeInTheDocument();
    expect(screen.getByText('coverage=0.9731 n=10000')).toBeInTheDocument();
    expect(screen.getByText(/Latest critique by Crit: one seed only/)).toBeInTheDocument();
  });

  it('keeps report sections collapsed until opened', () => {
    wrap(<LabView frame={FRAME} />);
    expect(screen.queryByText('We tested the claim.')).not.toBeInTheDocument();
    fireEvent.click(screen.getByText('Abstract'));
    expect(screen.getByText('We tested the claim.')).toBeInTheDocument();
  });

  it('says so when there is no lab state yet', () => {
    wrap(<LabView frame={{ renderer: 'lab' }} />);
    expect(screen.getByText('No world state yet.')).toBeInTheDocument();
  });
});
