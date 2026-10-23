import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { I18nProvider } from '../../../i18n';
import FlowTaskList from '../FlowTaskList';
import * as api from '../../../api';

vi.mock('../../../api', () => ({
  getProjects: vi.fn(),
}));

const TASKS = [
  { id: 1, key: 'TASK-1', title: 'Build the feature', description: 'Implement user login', project_id: 'proj-1' },
  { id: 2, key: 'TASK-2', title: 'Write documentation', description: 'API docs', project_id: 'proj-2' },
  { id: 3, title: 'Fix bug in parser', description: 'Parser crashes on empty input', project: 'Infra' },
];

const PROJECTS = [
  { id: 'proj-1', name: 'Frontend' },
  { id: 'proj-2', name: 'Backend' },
];

const show = (props) => {
  const defaults = {
    tasks: TASKS,
    taskId: null,
    onPick: vi.fn(),
    workspace: 'test-workspace',
  };
  return {
    ...render(
      <I18nProvider>
        <FlowTaskList {...{ ...defaults, ...props }} />
      </I18nProvider>,
    ),
    onPick: defaults.onPick,
  };
};

describe('FlowTaskList', () => {
  it('shows the empty state when no tasks are provided', () => {
    show({ tasks: [], workspace: null });
    expect(screen.getByText(/No tasks yet/)).toBeInTheDocument();
  });

  it('renders tasks with key, title and project badge', async () => {
    api.getProjects.mockResolvedValue({ data: PROJECTS });
    show({});

    await waitFor(() => {
      expect(screen.getByText('TASK-1')).toBeInTheDocument();
      expect(screen.getByText('Build the feature')).toBeInTheDocument();
      expect(screen.getByText('Frontend')).toBeInTheDocument();
    });
  });

  it('shows key before title in the same row', async () => {
    api.getProjects.mockResolvedValue({ data: PROJECTS });
    show({});

    await waitFor(() => {
      const row = screen.getByRole('button', { name: /Build the feature/ });
      // Check that key and title are both in the row
      expect(row.textContent).toContain('TASK-1');
      expect(row.textContent).toContain('Build the feature');
    });
  });

  it('filters tasks by key', async () => {
    api.getProjects.mockResolvedValue({ data: PROJECTS });
    show({});

    await waitFor(() => {
      expect(screen.getByText('Build the feature')).toBeInTheDocument();
    });

    const searchInput = screen.getByPlaceholderText(/Search by title, key or project/);
    fireEvent.change(searchInput, { target: { value: 'TASK-2' } });

    await waitFor(() => {
      expect(screen.queryByText('Build the feature')).not.toBeInTheDocument();
      expect(screen.getByText('Write documentation')).toBeInTheDocument();
    });
  });

  it('filters tasks by title', async () => {
    api.getProjects.mockResolvedValue({ data: PROJECTS });
    show({});

    await waitFor(() => {
      expect(screen.getByText('Build the feature')).toBeInTheDocument();
    });

    const searchInput = screen.getByPlaceholderText(/Search by title, key or project/);
    fireEvent.change(searchInput, { target: { value: 'documentation' } });

    await waitFor(() => {
      expect(screen.queryByText('Build the feature')).not.toBeInTheDocument();
      expect(screen.getByText('Write documentation')).toBeInTheDocument();
    });
  });

  it('filters tasks by project name', async () => {
    api.getProjects.mockResolvedValue({ data: PROJECTS });
    show({});

    await waitFor(() => {
      expect(screen.getByText('Frontend')).toBeInTheDocument();
      expect(screen.getByText('Backend')).toBeInTheDocument();
    });

    const searchInput = screen.getByPlaceholderText(/Search by title, key or project/);
    fireEvent.change(searchInput, { target: { value: 'Frontend' } });

    await waitFor(() => {
      expect(screen.getByText('Build the feature')).toBeInTheDocument();
      expect(screen.queryByText('Write documentation')).not.toBeInTheDocument();
      expect(screen.getByText('Frontend')).toBeInTheDocument();
    });
  });

  it('filters case-insensitively', async () => {
    api.getProjects.mockResolvedValue({ data: PROJECTS });
    show({});

    await waitFor(() => {
      expect(screen.getByText('Build the feature')).toBeInTheDocument();
    });

    const searchInput = screen.getByPlaceholderText(/Search by title, key or project/);
    fireEvent.change(searchInput, { target: { value: 'task-2' } });

    await waitFor(() => {
      expect(screen.queryByText('Build the feature')).not.toBeInTheDocument();
      expect(screen.getByText('Write documentation')).toBeInTheDocument();
    });
  });

  it('shows "No tasks match" when filter matches nothing', async () => {
    api.getProjects.mockResolvedValue({ data: PROJECTS });
    show({});

    await waitFor(() => {
      expect(screen.getByText('Build the feature')).toBeInTheDocument();
    });

    const searchInput = screen.getByPlaceholderText(/Search by title, key or project/);
    fireEvent.change(searchInput, { target: { value: 'nonexistent' } });

    await waitFor(() => {
      expect(screen.getByText(/No tasks match/)).toBeInTheDocument();
    });
  });

  it('marks the attached task with a cyan bar and background', async () => {
    api.getProjects.mockResolvedValue({ data: PROJECTS });
    show({ taskId: 1 });

    await waitFor(() => {
      const button = screen.getByRole('button', { name: /Build the feature/ });
      expect(button).toHaveClass('border-cyan-500', 'bg-cyan-50');
      expect(button).toHaveAttribute('aria-pressed', 'true');
    });
  });

  it('calls onPick when a task row is clicked', async () => {
    api.getProjects.mockResolvedValue({ data: PROJECTS });
    const { onPick } = show({});

    await waitFor(() => {
      expect(screen.getByText('Build the feature')).toBeInTheDocument();
    });

    const button = screen.getByRole('button', { name: /Build the feature/ });
    fireEvent.click(button);

    expect(onPick).toHaveBeenCalledWith(TASKS[0]);
  });

  it('uses task.project as fallback when project_id is not in projects', async () => {
    api.getProjects.mockResolvedValue({ data: PROJECTS });
    show({});

    await waitFor(() => {
      // Task 3 has project='Infra' instead of project_id, should show Infra
      expect(screen.getByText('Fix bug in parser')).toBeInTheDocument();
      expect(screen.getByText('Infra')).toBeInTheDocument();
    });
  });

  it('gracefully handles projects loading error', async () => {
    api.getProjects.mockRejectedValue(new Error('Network error'));
    const { onPick } = show({});

    await waitFor(() => {
      // Should still render tasks even if projects loading fails
      expect(screen.getByText('Build the feature')).toBeInTheDocument();
    });

    const button = screen.getByRole('button', { name: /Build the feature/ });
    fireEvent.click(button);
    expect(onPick).toHaveBeenCalledWith(TASKS[0]);
  });
});
