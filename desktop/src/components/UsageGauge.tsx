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
      <div className="w-32 truncate text-[14px] text-ink" title={label}>
        {label}
      </div>
      <div className="flex-1 h-1.5 rounded-full bg-cloud-light overflow-hidden">
        <div className="h-full rounded-full bg-crail" style={{ width: `${pct}%` }} />
      </div>
      <div className="w-16 text-right text-[12px] text-cloud-dark tnum">
        {count} · {pct.toFixed(0)}%
      </div>
    </li>
  );
}
