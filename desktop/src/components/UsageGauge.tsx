export function UsageGauge({
  label,
  share,
  count,
}: {
  label: string;
  share: number;
  count: number;
}) {
  const pct = Math.max(0, Math.min(1, share)) * 100;
  return (
    <li className="flex items-center gap-3 py-2">
      <div className="w-32 truncate text-sm text-ink" title={label}>
        {label}
      </div>
      <div className="flex-1 h-2 rounded-full bg-canvas overflow-hidden">
        <div
          className="h-full rounded-full bg-accent"
          style={{ width: `${pct}%` }}
        />
      </div>
      <div className="w-16 text-right text-xs text-ink-mute tabular-nums">
        {count} · {pct.toFixed(0)}%
      </div>
    </li>
  );
}
