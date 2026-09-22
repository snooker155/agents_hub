import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { FeaturesProvider } from '../FeaturesContext';
import { useFeatures } from '../features';
import * as api from '../../api';

function Probe() {
  const { playground } = useFeatures();
  return <span data-testid="flag">{String(playground)}</span>;
}

const show = () => render(<FeaturesProvider><Probe /></FeaturesProvider>);
const flag = () => screen.getByTestId('flag').textContent;

describe('FeaturesProvider', () => {
  beforeEach(() => vi.restoreAllMocks());

  it('shows a feature while the answer is still on the wire', () => {
    vi.spyOn(api, 'getHealth').mockReturnValue(new Promise(() => {}));
    show();
    expect(flag()).toBe('true');
  });

  it('hides the feature the backend reports as off', async () => {
    vi.spyOn(api, 'getHealth').mockResolvedValue({ data: { features: { playground: false } } });
    show();
    await waitFor(() => expect(flag()).toBe('false'));
  });

  it('keeps showing everything when the backend reports no features at all', async () => {
    vi.spyOn(api, 'getHealth').mockResolvedValue({ data: { ok: true } });
    show();
    await waitFor(() => expect(api.getHealth).toHaveBeenCalled());
    expect(flag()).toBe('true');
  });

  it('hides nothing when the health call fails', async () => {
    vi.spyOn(api, 'getHealth').mockRejectedValue(new Error('offline'));
    show();
    await waitFor(() => expect(api.getHealth).toHaveBeenCalled());
    expect(flag()).toBe('true');
  });
});
