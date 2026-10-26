import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { I18nProvider } from '../../../i18n';

const updateFlow = vi.fn(async () => ({ data: {} }));
vi.mock('../../../api/flows', () => ({ updateFlow: (...args) => updateFlow(...args) }));

const { default: FlowTitle } = await import('../FlowTitle');

const show = (onRenamed) => render(
  <I18nProvider><FlowTitle flow={{ id: 'f1', name: 'Coffee' }} onRenamed={onRenamed} /></I18nProvider>,
);

// A flow is renamed in its page header the way a task's title is.
describe('FlowTitle', () => {
  it('saves only the new name and hands it to the page', async () => {
    const onRenamed = vi.fn();
    show(onRenamed);
    fireEvent.click(screen.getByText('Coffee'));
    fireEvent.change(screen.getByRole('textbox'), { target: { value: '  Coffee trends  ' } });
    fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Enter' });
    await waitFor(() => expect(onRenamed).toHaveBeenCalledWith('Coffee trends'));
    expect(updateFlow).toHaveBeenCalledWith('f1', { name: 'Coffee trends' });
  });

  it('keeps the name when the field is left empty', async () => {
    updateFlow.mockClear();
    const onRenamed = vi.fn();
    show(onRenamed);
    fireEvent.click(screen.getByText('Coffee'));
    fireEvent.change(screen.getByRole('textbox'), { target: { value: '   ' } });
    fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Enter' });
    await waitFor(() => expect(screen.getByText('Coffee')).toBeInTheDocument());
    expect(updateFlow).not.toHaveBeenCalled();
    expect(onRenamed).not.toHaveBeenCalled();
  });
});
