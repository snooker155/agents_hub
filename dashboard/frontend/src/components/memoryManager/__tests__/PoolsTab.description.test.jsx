import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { PoolsTab } from '../PoolsTab';
import { I18nProvider } from '../../../i18n';

const POOL = {
  id: 'pool-1', name: 'Team pool', description: 'Old text', workspace: 'default',
  blocks: [], notes: [], structured_data: {},
};
const updateSharedMemory = vi.fn(() => Promise.resolve({ data: POOL }));

vi.mock('../../../api', () => ({
  getSharedMemory: () => Promise.resolve({ data: POOL }),
  createSharedMemory: () => Promise.resolve({ data: POOL }),
  updateSharedMemory: (...a) => updateSharedMemory(...a),
  getMemoryEpisodesStats: () => Promise.resolve({ data: { total: 0 } }),
  getMemoryGraphStats: () => Promise.resolve({ data: { node_count: 0 } }),
}));

describe('PoolsTab description edit', () => {
  it('saves the edited description and refreshes the list', async () => {
    const onRefresh = vi.fn();
    render(
      <I18nProvider>
        <PoolsTab memories={[POOL]} onRefresh={onRefresh} workspaceFilter="default" onPoolSelected={() => {}} />
      </I18nProvider>,
    );
    fireEvent.click(screen.getByText('Team pool'));
    fireEvent.click(await screen.findByLabelText('Edit description'));
    const box = screen.getByLabelText('Description');
    expect(box).toHaveValue('Old text');
    fireEvent.change(box, { target: { value: '  Save meeting decisions  ' } });
    fireEvent.click(screen.getByText('Save'));
    await waitFor(() => expect(updateSharedMemory).toHaveBeenCalledWith('pool-1', { description: 'Save meeting decisions' }));
    await waitFor(() => expect(onRefresh).toHaveBeenCalled());
    expect(await screen.findByText('Save meeting decisions')).toBeInTheDocument();
  });
});
