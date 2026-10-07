import { mask, segments } from '../lib/segments';

describe('segments', () => {
  const text = 'Call Maria at 415-555-0100 today.';
  it('splits text around spans and preserves every character', () => {
    const s = segments(text, [
      { start: 5, end: 10, key: 'a' },
      { start: 14, end: 26, key: 'b' },
    ]);
    expect(s.map((x) => x.text).join('')).toBe(text);
    expect(s.filter((x) => x.span).map((x) => x.text)).toEqual(['Maria', '415-555-0100']);
  });
  it('ignores out-of-range and empty spans, and overlapping inner spans', () => {
    const s = segments(text, [
      { start: 5, end: 10, key: 'a' },
      { start: 7, end: 9, key: 'inner' },
      { start: 40, end: 50, key: 'out' },
      { start: 3, end: 3, key: 'empty' },
    ]);
    expect(s.filter((x) => x.span).map((x) => x.span!.key)).toEqual(['a']);
    expect(s.map((x) => x.text).join('')).toBe(text);
  });
  it('handles unsorted spans and spans at the edges', () => {
    const s = segments('abcdef', [
      { start: 4, end: 6, key: 'end' },
      { start: 0, end: 2, key: 'start' },
    ]);
    expect(s.map((x) => [x.text, x.span?.key ?? null])).toEqual([
      ['ab', 'start'],
      ['cd', null],
      ['ef', 'end'],
    ]);
  });
});

describe('mask', () => {
  it('hides letters and digits but keeps the shape', () => {
    expect(mask('Maria 415-555')).toBe('••••• •••-•••');
  });
});
