import React from 'react';
import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { I18nProvider } from '../../i18n';
import RunOverrides from '../run/RunOverrides';

const show = (run) => render(<I18nProvider><RunOverrides run={run} /></I18nProvider>);

describe('RunOverrides', () => {
  it('renders nothing for a run without overrides', () => {
    const { container } = show({ run_id: 'r1' });
    expect(container).toBeEmptyDOMElement();
  });

  it('lists what the run changed', () => {
    show({
      run_id: 'r1',
      overrides: {
        model: 'gpt-4o-mini',
        system_append: 'Be brief.',
        tools: { add: ['read_file'], remove: ['run_shell'] },
        skills: false,
        mcp: ['github'],
        output_schema: { type: 'object' },
      },
    });
    expect(screen.getByTestId('run-overrides')).toBeInTheDocument();
    expect(screen.getByText('gpt-4o-mini')).toBeInTheDocument();
    expect(screen.getByText('Be brief.')).toBeInTheDocument();
    expect(screen.getByText('added: read_file; removed: run_shell')).toBeInTheDocument();
    expect(screen.getByText('github')).toBeInTheDocument();
    expect(screen.getByText('off')).toBeInTheDocument();
    expect(screen.getByText(/"type": "object"/)).toBeInTheDocument();
  });
});
