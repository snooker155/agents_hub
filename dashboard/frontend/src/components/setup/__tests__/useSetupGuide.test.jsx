import { StrictMode } from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';

const setupGuideApi = vi.hoisted(() => ({
  getSetupGuide: vi.fn(),
  setupGuideAction: vi.fn(),
}));
vi.mock('../../../api/setupGuide', () => setupGuideApi);

import useSetupGuide from '../useSetupGuide';

function Probe() {
  const { guide } = useSetupGuide();
  return <span data-testid="state">{guide ? `${guide.done}/${guide.total}` : 'none'}</span>;
}

describe('useSetupGuide', () => {
  it('takes the guide in StrictMode, whose cleanup runs once between two mounts', async () => {
    setupGuideApi.getSetupGuide.mockResolvedValue({ data: { active: false, done: 2, total: 13, steps: [] } });
    render(<StrictMode><Probe /></StrictMode>);
    await waitFor(() => expect(screen.getByTestId('state').textContent).toBe('2/13'));
  });
});
