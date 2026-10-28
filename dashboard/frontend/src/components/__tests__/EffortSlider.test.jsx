import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { I18nProvider } from '../../i18n';
import { EffortSlider } from '../EffortSelect';

// The header's effort slider: the leftmost stop is the model's own default,
// the chosen value is named next to the title, and a choice is sent once,
// when the slider is let go.
const efforts = ['none', 'low', 'medium', 'high', 'xhigh'];

function renderSlider(props) {
  const onChange = vi.fn();
  render(
    <I18nProvider>
      <EffortSlider efforts={efforts} value={null} onChange={onChange}
        title="Reasoning effort" defaultLabel="Model default (Medium)" {...props} />
    </I18nProvider>,
  );
  return { onChange, slider: screen.getByRole('slider', { name: 'Reasoning effort' }) };
}

describe('EffortSlider', () => {
  it('starts on the model default and names it by the title', () => {
    const { slider } = renderSlider();
    expect(slider).toHaveValue('0');
    expect(screen.getByText('Model default (Medium)')).toBeInTheDocument();
  });

  it('shows a stored effort at its stop', () => {
    const { slider } = renderSlider({ value: 'high' });
    expect(slider).toHaveValue('4');
    expect(screen.getByText('High')).toBeInTheDocument();
  });

  it('sends the choice once, on release', () => {
    const { slider, onChange } = renderSlider();
    fireEvent.change(slider, { target: { value: '2' } });
    fireEvent.change(slider, { target: { value: '3' } });
    expect(onChange).not.toHaveBeenCalled();
    expect(screen.getByText('Medium')).toBeInTheDocument();
    fireEvent.pointerUp(slider);
    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange).toHaveBeenCalledWith('medium');
  });

  it('goes back to the model default at the leftmost stop', () => {
    const { slider, onChange } = renderSlider({ value: 'low' });
    fireEvent.change(slider, { target: { value: '0' } });
    fireEvent.keyUp(slider);
    expect(onChange).toHaveBeenCalledWith(null);
  });
});
