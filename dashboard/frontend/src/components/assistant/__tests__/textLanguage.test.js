import { describe, expect, it } from 'vitest';
import textLanguage from '../textLanguage';

describe('textLanguage', () => {
  it.each([
    ['Привет! Чем могу помочь?', 'en', 'ru'],
    ['Привіт, як справи? Їжак', 'en', 'uk'],
    ['Hallo, wie kann ich Ihnen heute helfen?', 'en', 'de'],
    ['Hello, how can I help you with this?', 'de', 'en'],
    ['Grüße', 'en', 'de'],
    ['OK', 'de', 'de'],
    ['OK', 'ru', 'en'],
    ['12:30 · 42', 'ru', 'ru'],
  ])('%s is %s → %s like the hub tells it', (text, fallback, expected) => {
    expect(textLanguage(text, fallback)).toBe(expected);
  });
});
