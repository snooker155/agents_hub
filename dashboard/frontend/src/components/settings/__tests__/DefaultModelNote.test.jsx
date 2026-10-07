import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect } from 'vitest';

import { I18nProvider } from '../../../i18n';
import DefaultModelNote from '../DefaultModelNote';

const show = (ui) => render(<I18nProvider><MemoryRouter>{ui}</MemoryRouter></I18nProvider>);

describe('DefaultModelNote', () => {
  it('names the model, its price and links the Models page', () => {
    show(<DefaultModelNote models={[{ provider: 'openai', model: 'gpt-5.4-mini', input_price: 0.75, output_price: 4.5 }]} />);
    expect(screen.getByText(/Switched on gpt-5\.4-mini at \$0\.75 \/ \$4\.50 per 1M tokens/)).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Models page.' }).getAttribute('href')).toBe('/models');
  });

  it('renders nothing when nothing was switched on', () => {
    const { container } = show(<DefaultModelNote models={[]} />);
    expect(container.textContent).toBe('');
  });
});
