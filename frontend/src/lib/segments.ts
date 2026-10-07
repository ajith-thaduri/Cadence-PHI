// Split a text into plain and highlighted segments from non-overlapping [start, end) spans.
// Pure function (unit tested): the browser highlights text it already holds - the server never echoes it back.

export interface Span {
  start: number;
  end: number;
  key: string;
}

export interface Segment<T extends Span> {
  text: string;
  span: T | null;
}

export function segments<T extends Span>(text: string, spans: T[]): Segment<T>[] {
  const sorted = spans
    .filter((s) => s.start >= 0 && s.end <= text.length && s.end > s.start)
    .sort((a, b) => a.start - b.start || b.end - a.end);
  const out: Segment<T>[] = [];
  let pos = 0;
  for (const s of sorted) {
    if (s.start < pos) continue; // overlapping span: the earlier (outer) one wins
    if (s.start > pos) out.push({ text: text.slice(pos, s.start), span: null });
    out.push({ text: text.slice(s.start, s.end), span: s });
    pos = s.end;
  }
  if (pos < text.length) out.push({ text: text.slice(pos), span: null });
  return out;
}

export function mask(value: string): string {
  return value.replace(/[A-Za-z0-9]/g, '•');
}
