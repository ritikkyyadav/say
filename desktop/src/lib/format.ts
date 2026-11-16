// Formatting shared across the insights surfaces.
//
// Durations are shown as separate value/unit pairs rather than a single
// string because the design sets the number in a serif at 38px and the unit
// in the UI sans at 14px — "3h 37m" as one string cannot be typeset that way.

export const fmt = (n: number): string => Math.round(n).toLocaleString('en-US');

/** Seconds → `{ h, m }`, both integers. 13,020 → { h: 3, m: 37 }. */
export function hm(seconds: number): { h: number; m: number } {
  const total = Math.max(0, Math.round(seconds / 60));
  return { h: Math.floor(total / 60), m: total % 60 };
}

/** Seconds → "6:11". For durations shown inline next to a transcript. */
export function mmss(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

/** Seconds → "3 hours 37 minutes", for prose. */
export function longDuration(seconds: number): string {
  const { h, m } = hm(seconds);
  const parts: string[] = [];
  if (h) parts.push(`${h} hour${h === 1 ? '' : 's'}`);
  if (m || !h) parts.push(`${m} minute${m === 1 ? '' : 's'}`);
  return parts.join(' ');
}

export function bytes(n: number): string {
  if (!n) return '0 MB';
  if (n < 1024 ** 2) return `${Math.round(n / 1024)} KB`;
  if (n < 1024 ** 3) return `${Math.round(n / 1024 ** 2)} MB`;
  return `${(n / 1024 ** 3).toFixed(1)} GB`;
}

/** A rough download time, so a size means something to someone who does not
 *  think in gigabytes. 15 MB/s is an ordinary home connection. */
export function downloadTime(bytesTotal: number): string {
  const seconds = bytesTotal / (15 * 1024 ** 2);
  if (seconds < 45) return 'under a minute';
  return `about ${Math.round(seconds / 60)} min`;
}

export function greeting(now = new Date()): string {
  const h = now.getHours();
  if (h < 5) return 'Still up';
  if (h < 12) return 'Good morning';
  if (h < 17) return 'Good afternoon';
  if (h < 22) return 'Good evening';
  return 'Late again';
}
