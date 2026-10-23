import { afterEach, describe, expect, it, vi } from 'vitest';
import { confirmStatusChange } from '../task/taskUtils';

const t = (key, vars) => `${key}:${vars?.title ?? ''}`;

describe('confirmStatusChange', () => {
  afterEach(() => vi.restoreAllMocks());

  it('asks before a task goes back to todo, and follows the answer', () => {
    const ask = vi.spyOn(window, 'confirm').mockReturnValue(false);
    expect(confirmStatusChange(t, { title: 'Menu', status: 'done' }, 'todo')).toBe(false);
    expect(ask).toHaveBeenCalledWith('taskDetails.confirmResetToTodo:Menu');
    ask.mockReturnValue(true);
    expect(confirmStatusChange(t, { title: 'Menu', status: 'done' }, 'todo')).toBe(true);
  });

  it('does not ask for any other move', () => {
    const ask = vi.spyOn(window, 'confirm');
    expect(confirmStatusChange(t, { title: 'Menu', status: 'todo' }, 'todo')).toBe(true);
    expect(confirmStatusChange(t, { title: 'Menu', status: 'todo' }, 'in_progress')).toBe(true);
    expect(ask).not.toHaveBeenCalled();
  });
});
