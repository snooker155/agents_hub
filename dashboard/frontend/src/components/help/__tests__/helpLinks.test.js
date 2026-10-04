import { describe, expect, it } from 'vitest';
import { isAppRoute, parseHelpText } from '../helpLinks';

describe('the Help reply parser', () => {
  it('turns a link to a route into in-app navigation', () => {
    expect(parseHelpText('Open [Models](/models) first.')).toEqual([
      { type: 'text', text: 'Open ' },
      { type: 'nav', text: 'Models', to: '/models' },
      { type: 'text', text: ' first.' },
    ]);
  });

  it('starts the tour from #tour', () => {
    expect(parseHelpText('[Take the tour](#tour)')).toEqual([{ type: 'tour', text: 'Take the tour' }]);
  });

  it('keeps web links external and bold as bold', () => {
    expect(parseHelpText('**Models** and [site](https://example.com)')).toEqual([
      { type: 'strong', text: 'Models' },
      { type: 'text', text: ' and ' },
      { type: 'external', text: 'site', href: 'https://example.com' },
    ]);
  });

  it('reads any other target as plain text, never as a link', () => {
    expect(parseHelpText('see [x](javascript:void0) and [doc](help.md)')).toEqual([
      { type: 'text', text: 'see x and doc' },
    ]);
    expect(isAppRoute('//evil.example')).toBe(false);
    expect(isAppRoute('/settings/providers')).toBe(true);
  });

  it('handles empty input', () => {
    expect(parseHelpText('')).toEqual([]);
    expect(parseHelpText(null)).toEqual([]);
  });
});
