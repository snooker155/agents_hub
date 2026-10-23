import { render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi } from 'vitest';
import { I18nProvider } from '../../i18n';
import WorkspacePicker from '../WorkspacePicker';

const WORKSPACES = [
  { name: 'zerno', tasks_count: 3, attached: true, target: '/srv/zerno' },
  { name: 'default', tasks_count: 1 },
  { name: 'Login system', tasks_count: 5 },
];
const label = (ws) => (ws.name === 'default' ? 'default (All)' : ws.name);

const show = (onChange = vi.fn()) => {
  render(
    <I18nProvider><MemoryRouter>
      <WorkspacePicker workspaces={WORKSPACES} value="zerno" onChange={onChange} label={label} />
    </MemoryRouter></I18nProvider>,
  );
  return onChange;
};

describe('WorkspacePicker', () => {
  it('lists default first, with task counts and the linked folder', () => {
    show();
    fireEvent.click(screen.getByRole('button', { name: /workspace/i }));
    const options = screen.getAllByRole('option');
    expect(options.map((o) => o.textContent)).toEqual([
      expect.stringContaining('default (All)'),
      expect.stringContaining('Login system'),
      expect.stringContaining('zerno'),
    ]);
    expect(options[2]).toHaveAttribute('aria-selected', 'true');
    expect(options[2].textContent).toContain('3 tasks');
    expect(options[2].textContent).toContain('linked folder');
    expect(options[0].textContent).toContain('1 task');
  });

  it('searches and picks with the keyboard', () => {
    const onChange = show();
    fireEvent.click(screen.getByRole('button', { name: /workspace/i }));
    const search = screen.getByLabelText('Search workspaces');
    fireEvent.change(search, { target: { value: 'log' } });
    expect(screen.getAllByRole('option')).toHaveLength(1);
    fireEvent.keyDown(search, { key: 'Enter' });
    expect(onChange).toHaveBeenCalledWith('Login system');
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
  });

  it('says so when nothing matches', () => {
    show();
    fireEvent.click(screen.getByRole('button', { name: /workspace/i }));
    fireEvent.change(screen.getByLabelText('Search workspaces'), { target: { value: 'zzz' } });
    expect(screen.getByText('No workspace matches "zzz"')).toBeInTheDocument();
  });
});
