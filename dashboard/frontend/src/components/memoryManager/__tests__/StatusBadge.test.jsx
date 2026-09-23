import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { StatusBadge } from '../StatusBadge';

describe('StatusBadge', () => {
  it('shows the indexed label and colour for an indexed file', () => {
    render(<StatusBadge status="indexed" />);
    expect(screen.getByText('Indexed')).toBeInTheDocument();
    expect(screen.getByText('Indexed').closest('span').className).toMatch(/text-green-700/);
  });

  it('falls back to the raw label for an unknown status', () => {
    render(<StatusBadge status="not-a-real-status" />);
    expect(screen.getByText('Raw')).toBeInTheDocument();
  });
});
