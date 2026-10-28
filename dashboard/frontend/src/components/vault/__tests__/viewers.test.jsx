import { render, screen } from '@testing-library/react';
import { afterEach, describe, it, expect, vi } from 'vitest';

import { I18nProvider } from '../../../i18n';
import RecordViewer from '../viewers';

const show = (doc) => render(<I18nProvider><RecordViewer doc={doc} /></I18nProvider>);

describe('RecordViewer', () => {
  it('shows chat roles and text', () => {
    show({ kind: 'chat', data: { messages: [{ role: 'user', content: 'hello' }, { role: 'assistant', content: 'hi there' }] } });
    expect(screen.getByText('hello')).toBeInTheDocument();
    expect(screen.getByText('You')).toBeInTheDocument();
    expect(screen.getByText('hi there')).toBeInTheDocument();
  });

  it('says a skipped file is too large', () => {
    show({ kind: 'file', data: { name: 'big.bin', size: 5000000, skipped: 'too_large' } });
    expect(screen.getByText('Too large, not synced.')).toBeInTheDocument();
  });

  it('falls back to a JSON tree for other kinds', () => {
    show({ kind: 'plan', data: { goal: 'ship it' } });
    expect(screen.getByText('ship it')).toBeInTheDocument();
  });

  describe('file blob URLs', () => {
    const created = [];
    const revoked = [];
    afterEach(() => { vi.restoreAllMocks(); created.length = 0; revoked.length = 0; });
    const stubUrls = () => {
      let n = 0;
      URL.createObjectURL = URL.createObjectURL || (() => '');
      URL.revokeObjectURL = URL.revokeObjectURL || (() => {});
      vi.spyOn(URL, 'createObjectURL').mockImplementation(() => { n += 1; const u = `blob:test/${n}`; created.push(u); return u; });
      vi.spyOn(URL, 'revokeObjectURL').mockImplementation((u) => { revoked.push(u); });
    };
    // btoa of the PNG magic bytes is enough for an <img> preview.
    const png = (name) => ({ kind: 'file', data: { name, mime_type: 'image/png', size: 4, content_b64: btoa('\x89PNG') } });

    it('revokes the decrypted file URL when the viewer goes away', () => {
      stubUrls();
      const view = show(png('a.png'));
      const live = created.at(-1);
      expect(screen.getByRole('img')).toHaveAttribute('src', live);
      expect(revoked).not.toContain(live);
      view.unmount();
      expect(revoked).toContain(live);
      expect(created.every((u) => revoked.includes(u))).toBe(true);
    });

    it('revokes the old URL when another file replaces it', () => {
      stubUrls();
      const view = show(png('a.png'));
      const first = created.at(-1);
      view.rerender(<I18nProvider><RecordViewer doc={{ ...png('b.png'), data: { ...png('b.png').data, content_b64: btoa('other') } }} /></I18nProvider>);
      expect(revoked).toContain(first);
      expect(screen.getByRole('img')).toHaveAttribute('src', created.at(-1));
    });
  });
});
