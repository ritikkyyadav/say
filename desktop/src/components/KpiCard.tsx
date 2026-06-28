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
    <div className="card p-5 flex flex-col gap-4 min-h-[168px]">
      <div className="flex items-start justify-between gap-2">
        <div className="eyebrow">{title}</div>
        {right ? <div>{right}</div> : null}
      </div>
      <div className="flex items-end justify-between gap-3 mt-auto">
        <div>
          <div className="stat-number text-[2.5rem]">{value}</div>
          {subtitle ? <div className="text-[12px] text-cloud-dark mt-2">{subtitle}</div> : null}
        </div>
        {chart ? <div className="shrink-0">{chart}</div> : null}
      </div>
    </div>
  );
}

export function HalfDonut({ percent }: { percent: number }) {
  const clamped = Math.max(0, Math.min(100, percent));
  // Half-donut via conic-gradient — crail fill on a cloud-light track. No SVG needed.
  return (
    <div
      className="w-20 h-10 rounded-t-full"
      style={{
        background: `conic-gradient(from 270deg, #d97757 ${clamped * 1.8}deg, #e8e6dc ${clamped * 1.8}deg 180deg, transparent 180deg)`,
      }}
      aria-hidden
    />
  );
}

export function DeltaBadge({ deltaPct }: { deltaPct: number }) {
  const positive = deltaPct >= 0;
  // Direction carried by the arrow + text; colour stays restrained so the one
  // accent (crail) isn't diluted. Up reads warm, down reads muted.
  return (
    <span
      className={
        positive
          ? 'pill bg-crail/10 text-crail-deep border border-crail/20'
          : 'pill bg-book-cloth text-cloud-dark border border-cloud-light'
      }
    >
      {positive ? '↗' : '↘'} {Math.abs(deltaPct).toFixed(0)}% this month
    </span>
  );
}
