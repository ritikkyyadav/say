import { Transcript } from './api';

export type DayGroup = {
  label: string;
  isoDate: string; // YYYY-MM-DD in user's local timezone
  rows: Transcript[];
};

const MONTHS = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
];

function ymd(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

function labelFor(iso: string, today: Date): string {
  const tIso = ymd(today);
  if (iso === tIso) return 'TODAY';
  const yest = new Date(today);
  yest.setDate(yest.getDate() - 1);
  if (iso === ymd(yest)) return 'YESTERDAY';
  const d = new Date(iso + 'T00:00:00');
  return `${MONTHS[d.getMonth()].toUpperCase()} ${d.getDate()}, ${d.getFullYear()}`;
}

export function groupByDay(rows: Transcript[], now: Date = new Date()): DayGroup[] {
  const buckets = new Map<string, Transcript[]>();
  for (const r of rows) {
    const local = new Date(r.ts_utc);
    const key = ymd(local);
    if (!buckets.has(key)) buckets.set(key, []);
    buckets.get(key)!.push(r);
  }
  const sortedKeys = [...buckets.keys()].sort((a, b) => (a < b ? 1 : -1));
  return sortedKeys.map((iso) => ({
    label: labelFor(iso, now),
    isoDate: iso,
    rows: buckets.get(iso)!.sort((a, b) => (a.ts_utc < b.ts_utc ? 1 : -1)),
  }));
}
