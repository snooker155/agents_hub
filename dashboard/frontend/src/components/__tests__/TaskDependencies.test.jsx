import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { I18nProvider } from '../../i18n';

const updateTask = vi.fn();
// What the server answers; a refusal is thrown here, outside the spy.
let answer = async () => ({ data: {} });
vi.mock('../../api', async (importOriginal) => ({
  ...(await importOriginal()),
  updateTask: (...a) => { updateTask(...a); return answer(...a); },
}));

import DependencyChips from '../task/DependencyChips';
import DependsEditor from '../task/DependsEditor';
import { TaskLinksRow } from '../task/TaskMetaRow';

const A = { id: 'a', key: 'ZER-1', title: 'Design the menu', status: 'done' };
const B = { id: 'b', key: 'ZER-2', title: 'Build the page', status: 'in_progress', depends: ['a'] };
const C = { id: 'c', key: 'ZER-3', title: 'Deploy it', status: 'todo', depends: ['a', 'b'] };
const byId = new Map([A, B, C].map((x) => [x.id, x]));

const wrap = (ui) => render(<MemoryRouter><I18nProvider>{ui}</I18nProvider></MemoryRouter>);

beforeEach(() => { updateTask.mockReset(); answer = async () => ({ data: {} }); });

describe('DependencyChips', () => {
  it('names each task waited for, done ones apart', () => {
    wrap(<DependencyChips ids={C.depends} byId={byId} />);
    expect(screen.getByText(/waits for/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /ZER-1/ })).toHaveAttribute('data-done', 'true');
    expect(screen.getByRole('link', { name: /ZER-2/ })).toHaveAttribute('data-done', 'false');
  });

  it('says waited for once every dependency is done', () => {
    wrap(<DependencyChips ids={B.depends} byId={byId} />);
    expect(screen.getByText(/waited for/)).toBeInTheDocument();
  });

  it('renders nothing without dependencies', () => {
    wrap(<DependencyChips ids={[]} byId={byId} />);
    expect(screen.queryByTestId('dependency-chips')).toBeNull();
  });
});

describe('TaskLinksRow', () => {
  it('shows what the task blocks and offers to change its dependencies', () => {
    const onEdit = vi.fn();
    wrap(<TaskLinksRow depTasks={[]} dependents={[B, C]} parentTask={null} onEditDepends={onEdit} />);
    expect(screen.getByTestId('depends-on')).toHaveTextContent('nothing');
    expect(screen.getByTestId('blocks')).toHaveTextContent('Build the page');
    expect(screen.getByTestId('blocks')).toHaveTextContent('Deploy it');
    fireEvent.click(screen.getByRole('button', { name: /change/i }));
    expect(onEdit).toHaveBeenCalled();
  });
});

describe('DependsEditor', () => {
  const open = (task, onSaved = vi.fn(), onClose = vi.fn()) => {
    wrap(<DependsEditor task={task} candidates={[A, B, C]} onSaved={onSaved} onClose={onClose} />);
    return { onSaved, onClose };
  };

  it('adds a task through the search and saves the list', async () => {
    const { onSaved, onClose } = open(B);
    expect(screen.getByTestId('depends-list')).toHaveTextContent('Design the menu');
    fireEvent.click(screen.getByTestId('depends-picker'));
    // The task itself and what it already waits for are not offered.
    expect(screen.queryByRole('option', { name: /Build the page/ })).toBeNull();
    expect(screen.queryByRole('option', { name: /Design the menu/ })).toBeNull();
    fireEvent.click(screen.getByRole('option', { name: /Deploy it/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(updateTask).toHaveBeenCalledWith('b', { depends: ['a', 'c'] }));
    expect(onSaved).toHaveBeenCalled();
    expect(onClose).toHaveBeenCalled();
  });

  it('removes a dependency', async () => {
    open(C);
    fireEvent.click(screen.getAllByRole('button', { name: /remove the dependency/i })[0]);
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(updateTask).toHaveBeenCalledWith('c', { depends: ['b'] }));
  });

  it('shows the server refusing a cycle and stays open', async () => {
    answer = async () => {
      throw Object.assign(new Error('400'), { response: { data: { detail: 'Dependency cycle detected' } } });
    };
    const { onClose } = open(A);
    fireEvent.click(screen.getByTestId('depends-picker'));
    fireEvent.click(screen.getByRole('option', { name: /Deploy it/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    expect(await screen.findByText('Dependency cycle detected')).toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
  });
});
