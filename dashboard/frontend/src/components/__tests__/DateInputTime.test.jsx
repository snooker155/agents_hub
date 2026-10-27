import { render, screen, fireEvent, within } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { useState } from 'react';
import { I18nProvider } from '../../i18n';
import DateInput from '../DateInput';

function Field({ onChange }) {
  const [value, setValue] = useState('2026-10-27T09:00');
  return <DateInput mode="datetime" value={value} onChange={(v) => { setValue(v); onChange(v); }} />;
}

const open = () => fireEvent.click(screen.getByRole('button', { name: /calendar|календарь|kalender/i }));

describe('DateInput time', () => {
  it('sets the hour, then the minute, on the clock dial', () => {
    const onChange = vi.fn();
    render(<I18nProvider><Field onChange={onChange} /></I18nProvider>);
    open();
    fireEvent.click(screen.getByRole('button', { name: /^(hours|часы|stunden)$/i }));
    // Hours on two rings: 13–23 inside, so the afternoon is one click.
    fireEvent.click(within(screen.getByTestId('clock-hours')).getByRole('option', { name: '14' }));
    expect(onChange).toHaveBeenLastCalledWith('2026-10-27T14:00');
    // The hour leads to the minutes; picking one ends the pick.
    fireEvent.click(within(screen.getByTestId('clock-minutes')).getByRole('option', { name: '35' }));
    expect(onChange).toHaveBeenLastCalledWith('2026-10-27T14:35');
    expect(screen.queryByTestId('date-popup')).toBeNull();
  });

  it('takes any minute where the dial is let go', () => {
    const onChange = vi.fn();
    render(<I18nProvider><Field onChange={onChange} /></I18nProvider>);
    open();
    fireEvent.click(screen.getByRole('button', { name: /^(minutes|минуты|minuten)$/i }));
    const dial = screen.getByTestId('clock-minutes');
    // jsdom gives the dial a box at 0,0: its centre is at 104,104. 17 minutes
    // is 102° clockwise from the top.
    const a = ((102 - 90) * Math.PI) / 180;
    const point = { clientX: 104 + 82 * Math.cos(a), clientY: 104 + 82 * Math.sin(a), button: 0 };
    fireEvent.pointerDown(dial, point);
    fireEvent.pointerUp(dial, point);
    expect(onChange).toHaveBeenLastCalledWith('2026-10-27T09:17');
  });

  it('goes from the day to the hour dial, and is drawn outside the field', () => {
    const onChange = vi.fn();
    const { container } = render(<I18nProvider><Field onChange={onChange} /></I18nProvider>);
    open();
    const popup = screen.getByTestId('date-popup');
    // In <body>, not in the field: a modal that scrolls cannot cut it off.
    expect(container.contains(popup)).toBe(false);
    fireEvent.click(within(popup).getByRole('button', { name: '15' }));
    expect(onChange).toHaveBeenLastCalledWith('2026-10-15T09:00');
    expect(screen.getByTestId('clock-hours')).toBeInTheDocument();
  });

  it('keeps a parent that closes on an outside mousedown open', () => {
    const outside = vi.fn();
    document.addEventListener('mousedown', outside);
    render(<I18nProvider><Field onChange={() => {}} /></I18nProvider>);
    open();
    fireEvent.mouseDown(within(screen.getByTestId('date-popup')).getByRole('button', { name: '15' }));
    expect(outside).not.toHaveBeenCalled();
    document.removeEventListener('mousedown', outside);
  });
});
