import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import SearchSelect from '../SearchSelect';

const ITEMS = [
  { id: 'writer', name: 'Writer', subtitle: 'Writes things' },
  { id: 'coder', name: 'Coder', subtitle: 'Writes code' },
];

const show = (props = {}) => {
  const onChange = vi.fn();
  render(
    <SearchSelect
      testId="picker"
      items={ITEMS}
      value=""
      onChange={onChange}
      placeholder="Choose"
      searchPlaceholder="Search"
      noMatchText="Nothing matches"
      emptyText="Nothing to pick"
      {...props}
    />,
  );
  return onChange;
};

describe('SearchSelect', () => {
  it('filters the rows by the search and picks one', () => {
    const onChange = show();
    fireEvent.click(screen.getByTestId('picker'));
    fireEvent.change(screen.getByPlaceholderText('Search'), { target: { value: 'code' } });
    expect(screen.queryByRole('option', { name: /Writer/ })).toBeNull();
    fireEvent.click(screen.getByRole('option', { name: /Coder/ }));
    expect(onChange).toHaveBeenCalledWith('coder');
  });

  it('picks the highlighted row with Enter', () => {
    const onChange = show();
    fireEvent.click(screen.getByTestId('picker'));
    const input = screen.getByPlaceholderText('Search');
    fireEvent.keyDown(input, { key: 'ArrowDown' });
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(onChange).toHaveBeenCalledWith('coder');
  });

  it('says when nothing matches', () => {
    show();
    fireEvent.click(screen.getByTestId('picker'));
    fireEvent.change(screen.getByPlaceholderText('Search'), { target: { value: 'zzz' } });
    expect(screen.getByText('Nothing matches')).toBeInTheDocument();
  });

  it('is inactive with nothing to pick', () => {
    show({ items: [] });
    const button = screen.getByTestId('picker');
    expect(button).toBeDisabled();
    expect(button).toHaveTextContent('Nothing to pick');
    fireEvent.click(button);
    expect(screen.queryByPlaceholderText('Search')).toBeNull();
  });
});
