export function pct(x: unknown, digits = 1): string {
  return typeof x === 'number' && Number.isFinite(x) ? `${(x * 100).toFixed(digits)}%` : '-';
}

export function num(x: unknown, digits = 0): string {
  return typeof x === 'number' && Number.isFinite(x)
    ? x.toLocaleString(undefined, { maximumFractionDigits: digits })
    : '-';
}

export function when(iso: string | null | undefined): string {
  if (!iso) return '-';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

export function duration(start: string | null, end: string | null): string {
  if (!start) return '-';
  const ms = (end ? new Date(end) : new Date()).getTime() - new Date(start).getTime();
  if (!Number.isFinite(ms) || ms < 0) return '-';
  const s = Math.round(ms / 1000);
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`;
}

export function shortSha(s: string | null | undefined): string {
  return s ? s.slice(0, 12) : '-';
}
