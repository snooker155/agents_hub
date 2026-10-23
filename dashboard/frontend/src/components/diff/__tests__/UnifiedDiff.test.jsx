import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import UnifiedDiff from '../UnifiedDiff';
import { diffStats, parseUnifiedDiff } from '../diffRows';

const DIFF = [
  '--- instructions@v1',
  '+++ instructions@current',
  '@@ -1,3 +1,3 @@ ## Role',
  ' You are a reviewer.',
  '-Check the code carefully.',
  '+Check the code and the tests carefully.',
  ' Be brief.',
  '\\ No newline at end of file',
  '',
].join('\n');

describe('UnifiedDiff', () => {
  it('parses rows with line numbers and drops file headers', () => {
    const rows = parseUnifiedDiff(DIFF);
    expect(rows.map((r) => r.type)).toEqual(['hunk', 'ctx', 'del', 'add', 'ctx']);
    expect(rows[1]).toMatchObject({ old: 1, new: 1, text: 'You are a reviewer.' });
    expect(rows[2]).toMatchObject({ old: 2, text: 'Check the code carefully.' });
    expect(rows[3]).toMatchObject({ new: 2, text: 'Check the code and the tests carefully.' });
    expect(rows[4]).toMatchObject({ old: 3, new: 3 });
    expect(diffStats(rows)).toEqual({ added: 1, removed: 1 });
  });

  it('marks only the words that changed inside a paired line', () => {
    const { container } = render(<UnifiedDiff diff={DIFF} />);
    expect(screen.queryByText(/instructions@v1/)).not.toBeInTheDocument();
    const marks = [...container.querySelectorAll('span.bg-emerald-200')].map((n) => n.textContent);
    expect(marks.join('')).toBe('and the tests ');
    expect(container.querySelectorAll('span.bg-red-200')).toHaveLength(0);
    expect(screen.getByText('## Role')).toBeInTheDocument();
  });
});
