import { describe, it, expect } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { SlotValue, SlotData } from '../SlotValue';
import { I18nProvider } from '../../i18n';

const show = (ui) => render(<I18nProvider>{ui}</I18nProvider>);

describe('SlotValue — scalars', () => {
  it('renders a short scalar with its key', () => {
    show(<SlotValue label="city" value="Berlin" />);
    expect(screen.getByText(/Berlin/)).toBeInTheDocument();
    expect(screen.getByText(/city/)).toBeInTheDocument();
  });

  it('renders a non-string scalar as JSON', () => {
    show(<SlotValue label="count" value={42} />);
    expect(screen.getByText(/42/)).toBeInTheDocument();
  });

  it('truncates a long scalar and expands it on click', async () => {
    const user = userEvent.setup();
    const long = 'x'.repeat(50);
    show(<SlotValue label={null} value={long} max={10} />);

    const chip = screen.getByText(`${'x'.repeat(10)}…`);
    expect(chip).toHaveAttribute('title', long);

    await user.click(chip);
    expect(screen.getByText(long)).toBeInTheDocument();
  });

  it('leaves a scalar within the limit unclickable', () => {
    show(<SlotValue label={null} value="short" max={10} />);
    const chip = screen.getByText('short');
    expect(chip).not.toHaveAttribute('title');
    expect(chip.className).not.toContain('cursor-pointer');
  });
});

describe('SlotValue — containers', () => {
  it('renders an object as an expanded tree at the top level', () => {
    show(<SlotValue label={null} value={{ a: 1, b: 'two' }} />);
    expect(screen.getByText('{2 fields}')).toBeInTheDocument();
    expect(screen.getByText(/a/)).toBeInTheDocument();
    expect(screen.getByText(/two/)).toBeInTheDocument();
  });

  it('renders a JSON string the agent stored as a tree, not as escaped text', () => {
    show(<SlotValue label={null} value='{"a":1}' />);
    expect(screen.getByText('{1 field}')).toBeInTheDocument();
  });

  it('indexes array items by position', () => {
    show(<SlotValue label={null} value={[10, 20]} />);
    expect(screen.getByText('[2 items]')).toBeInTheDocument();
    expect(screen.getByText(/^0:/)).toBeInTheDocument();
    expect(screen.getByText(/^1:/)).toBeInTheDocument();
  });

  it('collapses on click and hides the children', async () => {
    const user = userEvent.setup();
    show(<SlotValue label={null} value={{ secret: 'visible' }} />);
    expect(screen.getByText(/visible/)).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /1 field/ }));
    expect(screen.queryByText(/visible/)).not.toBeInTheDocument();
  });

  it('keeps a nested container collapsed so a deep blob stays list-sized', () => {
    show(<SlotValue label={null} value={{ outer: { inner: 'hidden' } }} />);
    // Both levels summarise as "{1 field}"; only the outer one is expanded.
    expect(screen.getAllByText('{1 field}')).toHaveLength(2);
    expect(screen.getByText(/outer/)).toBeInTheDocument();
    expect(screen.queryByText(/hidden/)).not.toBeInTheDocument();
  });

  it('shows the first 8 children and reveals the rest on demand', async () => {
    const user = userEvent.setup();
    const many = Object.fromEntries(Array.from({ length: 11 }, (_, i) => [`k${i}`, `v${i}`]));
    show(<SlotValue label={null} value={many} />);

    expect(screen.getByText(/v7/)).toBeInTheDocument();
    expect(screen.queryByText(/v8/)).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: '+3 more' }));
    expect(screen.getByText(/v10/)).toBeInTheDocument();
  });

  it('labels an empty container with a translated word, not a raw i18n key', () => {
    show(<SlotValue label={null} value={{}} />);
    expect(screen.getByText('{0 fields}')).toBeInTheDocument();
    expect(screen.getByText('empty')).toBeInTheDocument();
    expect(screen.queryByText('slotValue.empty')).not.toBeInTheDocument();
  });
});

describe('SlotData', () => {
  it('flows scalars on one row and gives containers their own tree', () => {
    const { container } = show(
      <SlotData data={{ city: 'Berlin', count: 3, nested: { a: 1 } }} />,
    );
    const row = container.querySelector('.flex.flex-wrap');
    expect(within(row).getByText(/Berlin/)).toBeInTheDocument();
    expect(within(row).getByText(/3/)).toBeInTheDocument();
    expect(within(row).queryByText('{1 field}')).not.toBeInTheDocument();
    expect(screen.getByText('{1 field}')).toBeInTheDocument();
  });

  it('renders nothing at all for empty data', () => {
    const { container } = show(<SlotData data={{}} />);
    expect(container).toBeEmptyDOMElement();
    const { container: c2 } = show(<SlotData data={null} />);
    expect(c2).toBeEmptyDOMElement();
  });
});
