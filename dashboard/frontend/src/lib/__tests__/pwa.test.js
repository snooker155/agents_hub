import { afterEach, describe, expect, it, vi } from 'vitest';
import { install, installMode, isIos, isStandalone, registerServiceWorker } from '../pwa';

describe('isIos', () => {
  it('knows an iPhone and an iPad that reports itself as a Mac', () => {
    expect(isIos({ userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X)' })).toBe(true);
    expect(isIos({ userAgent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)', maxTouchPoints: 5 })).toBe(true);
  });

  it('is false for a desktop Mac and for Android', () => {
    expect(isIos({ userAgent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)', maxTouchPoints: 0 })).toBe(false);
    expect(isIos({ userAgent: 'Mozilla/5.0 (Linux; Android 15; Pixel 9)' })).toBe(false);
  });
});

describe('isStandalone', () => {
  it('reads the display mode, and Safari’s own flag', () => {
    const win = (matches, standalone) => ({ matchMedia: () => ({ matches }), navigator: { standalone } });
    expect(isStandalone(win(true, undefined))).toBe(true);
    expect(isStandalone(win(false, true))).toBe(true);
    expect(isStandalone(win(false, undefined))).toBe(false);
  });
});

describe('the install offer', () => {
  afterEach(() => vi.restoreAllMocks());

  it('is a prompt once the browser offers one, and is spent by install()', async () => {
    const offer = new Event('beforeinstallprompt');
    offer.prompt = vi.fn();
    offer.userChoice = Promise.resolve({ outcome: 'accepted' });
    window.dispatchEvent(offer);
    expect(installMode()).toBe('prompt');

    await expect(install()).resolves.toBe(true);
    expect(offer.prompt).toHaveBeenCalledOnce();
    expect(installMode()).not.toBe('prompt');
  });

  it('is nothing once the app is installed', () => {
    window.dispatchEvent(new Event('appinstalled'));
    expect(installMode()).toBe(null);
  });
});

describe('registerServiceWorker', () => {
  it('does nothing outside a production build', () => {
    const register = vi.fn();
    vi.stubGlobal('navigator', { ...navigator, serviceWorker: { register } });
    registerServiceWorker({ enabled: false });
    window.dispatchEvent(new Event('load'));
    expect(register).not.toHaveBeenCalled();
    vi.unstubAllGlobals();
  });
});
