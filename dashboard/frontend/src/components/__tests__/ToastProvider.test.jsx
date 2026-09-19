import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, fireEvent, render, screen } from '@testing-library/react';
import ToastProvider from '../ToastProvider';
import { useToast } from '../toast';
import { I18nProvider } from '../../i18n';

// A consumer that exposes the toast API through buttons the test can click.
function Harness({ detail }) {
  const toast = useToast();
  return (
    <div>
      <button type="button" onClick={() => toast.error('Could not save', detail)}>fail</button>
      <button type="button" onClick={() => toast.success('Saved')}>ok</button>
      <button type="button" onClick={() => toast.error('')}>empty</button>
    </div>
  );
}

const show = (props = {}) => render(
  <I18nProvider><ToastProvider><Harness {...props} /></ToastProvider></I18nProvider>,
);

describe('ToastProvider', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  // fireEvent rather than userEvent: the latter's internal delay does not
  // advance under fake timers, which the auto-dismiss assertions need.
  const click = (name) => fireEvent.click(screen.getByText(name));
  const clickButton = (name) => fireEvent.click(screen.getByRole('button', { name }));

  it('shows nothing until something is reported', () => {
    show();
    expect(screen.queryByText('Could not save')).not.toBeInTheDocument();
  });

  it('surfaces a failure the caller would otherwise have swallowed', () => {
    show();
    click('fail');
    expect(screen.getByText('Could not save')).toBeInTheDocument();
  });

  it('shows the server detail under the message', () => {
    show({ detail: 'Agent not found' });
    click('fail');
    expect(screen.getByText('Could not save')).toBeInTheDocument();
    expect(screen.getByText('Agent not found')).toBeInTheDocument();
  });

  it('reports successes too', () => {
    show();
    click('ok');
    expect(screen.getByText('Saved')).toBeInTheDocument();
  });

  it('ignores an empty message rather than flashing a blank box', () => {
    show();
    click('empty');
    expect(screen.queryByRole('button', { name: 'Dismiss' })).not.toBeInTheDocument();
  });

  it('disappears on its own', () => {
    show();
    click('fail');
    expect(screen.getByText('Could not save')).toBeInTheDocument();

    act(() => { vi.advanceTimersByTime(6000); });
    expect(screen.queryByText('Could not save')).not.toBeInTheDocument();
  });

  it('stays put until its time is up', () => {
    show();
    click('fail');
    act(() => { vi.advanceTimersByTime(5000); });
    expect(screen.getByText('Could not save')).toBeInTheDocument();
  });

  it('can be dismissed by hand', () => {
    show();
    click('fail');
    clickButton('Dismiss');
    expect(screen.queryByText('Could not save')).not.toBeInTheDocument();
  });

  it('keeps only the most recent few so a failing loop cannot fill the screen', () => {
    show();
    for (let i = 0; i < 5; i += 1) click('fail');
    expect(screen.getAllByText('Could not save')).toHaveLength(3);
  });

  it('announces politely instead of stealing focus', () => {
    const { container } = show();
    click('fail');
    const live = container.querySelector('[aria-live="polite"]');
    expect(live).toBeInTheDocument();
    expect(live).toHaveTextContent('Could not save');
    // A toast reports; it must never pull focus out from under the user.
    expect(live.contains(document.activeElement)).toBe(false);
  });
});
