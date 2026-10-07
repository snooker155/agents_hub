import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { I18nProvider } from '../../../i18n';
import FirstModelForm from '../FirstModelForm';

const api = vi.hoisted(() => ({ connectFirstModel: vi.fn() }));
vi.mock('../../../api/setupGuide', () => api);

const renderForm = (props = {}) => render(
  <I18nProvider>
    <MemoryRouter><FirstModelForm {...props} /></MemoryRouter>
  </I18nProvider>,
);

describe('FirstModelForm', () => {
  beforeEach(() => {
    api.connectFirstModel.mockReset();
  });

  it('a non-administrator is told to ask one instead of seeing the form', () => {
    renderForm({ admin: false });
    expect(screen.getByTestId('first-model-admin-only')).toHaveTextContent('Ask your administrator to connect a model.');
    expect(screen.queryByTestId('first-model-form')).toBeNull();
  });

  it('the key field is hidden for a local provider, and Connect needs no key there', () => {
    renderForm();
    expect(screen.getByLabelText('API key')).toBeTruthy();
    fireEvent.change(screen.getByLabelText('Provider'), { target: { value: 'ollama' } });
    expect(screen.queryByLabelText('API key')).toBeNull();
    expect(screen.getByLabelText('Base URL (optional)')).toHaveAttribute('placeholder', 'http://localhost:11434');
    expect(screen.getByRole('button', { name: 'Connect' })).not.toBeDisabled();
  });

  it('Connect is disabled for a cloud provider until a key is typed', () => {
    renderForm();
    expect(screen.getByRole('button', { name: 'Connect' })).toBeDisabled();
    fireEvent.change(screen.getByLabelText('API key'), { target: { value: 'sk-test' } });
    expect(screen.getByRole('button', { name: 'Connect' })).not.toBeDisabled();
  });

  it('submits the provider, key and base URL, and reports success through onDone', async () => {
    api.connectFirstModel.mockResolvedValue({
      data: { ok: true, provider: 'openai', model: 'gpt-5.2', models: ['gpt-5.2'], summary: 'Connected.' },
    });
    const onDone = vi.fn();
    renderForm({ onDone });
    fireEvent.change(screen.getByLabelText('API key'), { target: { value: 'sk-test' } });
    fireEvent.click(screen.getByRole('button', { name: 'Connect' }));
    await waitFor(() => expect(api.connectFirstModel).toHaveBeenCalledWith({
      provider: 'openai', api_key: 'sk-test', base_url: '',
    }));
    await waitFor(() => expect(onDone).toHaveBeenCalledWith(
      expect.objectContaining({ ok: true, provider: 'openai' }),
    ));
  });

  it('shows the server\'s own error message under the form', async () => {
    api.connectFirstModel.mockRejectedValue({ response: { status: 400, data: { detail: { code: 'rejected', message: 'Bad key' } } } });
    renderForm();
    fireEvent.change(screen.getByLabelText('API key'), { target: { value: 'sk-bad' } });
    fireEvent.click(screen.getByRole('button', { name: 'Connect' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('That key was not accepted. Check it and try again.');
  });

  it('a 403 tells a non-administrator to ask one, even on a stray submit', async () => {
    api.connectFirstModel.mockRejectedValue({ response: { status: 403 } });
    renderForm();
    fireEvent.change(screen.getByLabelText('API key'), { target: { value: 'sk-test' } });
    fireEvent.click(screen.getByRole('button', { name: 'Connect' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Ask your administrator to connect a model.');
  });

  it('links to the Models page\'s Local tab for the hub\'s own runtime', () => {
    renderForm();
    const link = screen.getByRole('link', { name: 'Open the Local tab' });
    expect(link).toHaveAttribute('href', '/models?tab=local');
  });
});
