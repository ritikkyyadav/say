import { ReactNode } from 'react';

export function KpiCard({
  title,
  value,
  subtitle,
  right,
  chart,
}: {
  title: string;
  value: ReactNode;
  subtitle?: ReactNode;
  right?: ReactNode;
  chart?: ReactNode;
}) {
  return (
    <div className="card p-5 flex flex-col gap-3 min-h-[160px]">
      <div className="flex items-start justify-between gap-2">
        <div className="text-[12px] uppercase tracking-wide text-ink-mute">{title}</div>
        {right ? <div>{right}</div> : null}
      </div>
      <div className="flex items-end justify-between gap-3">
        <div>
          <div className="text-3xl font-semibold tabular-nums leading-none">{value}</div>
          {subtitle ? <div className="text-xs text-ink-mute mt-2">{subtitle}</div> : null}
        </div>
        {chart ? <div className="shrink-0">{chart}</div> : null}
      </div>
    </div>
  );
}

export function HalfDonut({ percent }: { percent: number }) {
  const clamped = Math.max(0, Math.min(100, percent));
  // Half-donut via conic-gradient. Works without SVG.
  return (
    <div
      className="w-20 h-10 rounded-t-full"
      style={{
        background: `conic-gradient(from 270deg, #0B5D5A ${clamped * 1.8}deg, #E5E1DA ${clamped * 1.8}deg 180deg, transparent 180deg)`,
      }}
      aria-hidden
    />
  );
}

export function DeltaBadge({ deltaPct }: { deltaPct: number }) {
  const positive = deltaPct >= 0;
  return (
    <span
      className={`pill ${positive ? 'bg-emerald-50 text-emerald-700' : 'bg-rose-50 text-rose-700'}`}
    >
      {positive ? '↗' : '↘'} {Math.abs(deltaPct).toFixed(0)}% this month
    </span>
  );
}
