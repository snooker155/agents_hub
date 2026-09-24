import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { I18nProvider } from '../../i18n';
import CodeView from '../renderers/CodeView';

const view = {
  view_id: 'v1',
  kind: 'code',
  title: 'Snippet',
  spec: {
    language: 'python',
    filename: 'hello.py',
    body: 'print("hello")',
    dependencies: ['requests'],
    version: 3,
    description: 'A tiny script.',
  },
};

const show = (v) => render(
  <I18nProvider>
    <CodeView view={v} />
  </I18nProvider>,
);

describe('CodeView', () => {
  it('renders the filename, version and body', () => {
    show(view);
    expect(screen.getByText('hello.py')).toBeInTheDocument();
    expect(screen.getAllByText('python').length).toBeGreaterThan(0);
    expect(screen.getByText('v3')).toBeInTheDocument();
    expect(screen.getByText(/print\("hello"\)/)).toBeInTheDocument();
    expect(screen.getByText('A tiny script.')).toBeInTheDocument();
    expect(screen.getByText('requests')).toBeInTheDocument();
  });

  it('falls back to a generic label when the filename is missing', () => {
    show({ ...view, spec: { ...view.spec, filename: '' } });
    expect(screen.getByText('Untitled snippet')).toBeInTheDocument();
  });
});
