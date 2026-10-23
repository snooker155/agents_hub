import { render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { I18nProvider } from '../../../i18n';
import { EntityPalette } from '../FlowCanvas';

// Each registry entry shows what the node does under its name; a built-in
// entity's text comes from the locale, a user entity keeps its own.

const ENTITIES = {
  processor: [
    { id: 'summarize', name: 'Summarize', category: 'processor', group: 'Text', description: 'Condense the input text.' },
    { id: 'my_proc', name: 'My Proc', category: 'processor', group: 'Custom', description: 'Does my thing.' },
  ],
};

const show = () => render(<I18nProvider><EntityPalette entitiesByCategory={ENTITIES} /></I18nProvider>);

describe('EntityPalette', () => {
  afterEach(() => localStorage.clear());

  it('shows a description under each entity', () => {
    show();
    expect(screen.getByText(/roughly the first N words/)).toBeInTheDocument();
    expect(screen.getByText('Does my thing.')).toBeInTheDocument();
  });

  it('translates built-in entities and categories', () => {
    localStorage.setItem('agents_hub_language', 'ru');
    show();
    expect(screen.getByText('Обработчики')).toBeInTheDocument();
    expect(screen.getByText('Сокращение')).toBeInTheDocument();
    expect(screen.getByText('My Proc')).toBeInTheDocument();
  });
});
