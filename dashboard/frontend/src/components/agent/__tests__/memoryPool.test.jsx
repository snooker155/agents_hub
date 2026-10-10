import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { MemoryPoolDetails } from '../memoryPool';

// The Memory tab of an agent. Six of its icons were used without an import,
// so the tab threw a ReferenceError as soon as an agent had a pool attached:
// ESLint 10 started reading JSX names as references and caught it.
const pool = {
  id: 'pool-1', name: 'HR knowledge', description: 'Policies',
  files: [
    { name: 'handbook.md', size: 2048, rag_status: 'indexed', vectorized: true, chunk_count: 4,
      embedding_dims: 1536, preview: '# Leave\n30 days a year' },
  ],
};

describe('MemoryPoolDetails', () => {
  it('shows an attached pool with its files', () => {
    render(<MemoryPoolDetails pool={pool} />);
    expect(screen.getByText('pool-1')).toBeInTheDocument();
    expect(screen.getByText('handbook.md')).toBeInTheDocument();
  });

  it('opens a file preview and its tool call', () => {
    render(<MemoryPoolDetails pool={pool} />);
    const previews = screen.getAllByRole('button').filter((b) => /preview/i.test(b.textContent));
    expect(previews.length).toBeGreaterThan(0);
    fireEvent.click(previews[0]);
    expect(screen.getByText(/"memory_id": "pool-1"/)).toBeInTheDocument();
  });
});
