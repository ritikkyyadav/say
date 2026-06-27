import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../lib/api';
import { DeltaBadge, HalfDonut, KpiCard } from '../components/KpiCard';
import { UsageGauge } from '../components/UsageGauge';
import { StreakHeatmap } from '../components/StreakHeatmap';
import { PermissionsBanner } from '../components/PermissionsBanner';
import { appLabel } from '../lib/appNames';

// Reference cohort for the WPM percentile pill. Percentiles based on a
// public typing-corpus baseline; treat as illustrative until we collect
// enough first-party data to build a real distribution. See the build
// prompt for the assumption.
const WPM_REFERENCE = [40, 60, 80, 100, 120, 140, 160, 180, 200];

function topPercentForWpm(wpm: number): number {
  // Crude piecewise rank — returns "Top X%" so the pill matches the
  // Wispr Flow gauge card.
  let i = 0;
  for (; i < WPM_REFERENCE.length; i++) {
    if (wpm < WPM_REFERENCE[i]) break;
  }
  const rank = i / WPM_REFERENCE.length; // 0..1
  const top = Math.max(1, Math.round((1 - rank) * 100));
  return top;
}

function deltaPct(curr: number, prev: number): number {
  if (prev <= 0) return curr > 0 ? 100 : 0;
  return ((curr - prev) / prev) * 100;
}

export function Insights() {
  const [tab, setTab] = useState<'usage' | 'voice'>('usage');

  return (
    <div>
      <header className="mb-9">
        <p className="eyebrow mb-3">How you dictate</p>
        <h1 className="display text-[2.75rem]">Insights</h1>
      </header>
      <PermissionsBanner />
      <div className="flex items-center gap-6 mb-8 border-b border-cloud-light">
        <TabButton active={tab === 'usage'} onClick={() => setTab('usage')}>
          Your Usage
        </TabButton>
        <TabButton active={tab === 'voice'} onClick={() => setTab('voice')}>
          Your Voice
        </TabButton>
      </div>
      {tab === 'usage' ? <YourUsage /> : <YourVoice />}
    </div>
  );
}

function TabButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      className={`pb-3 -mb-px text-[14px] border-b-2 transition-colors ${
        active
          ? 'border-crail text-ink font-medium'
          : 'border-transparent text-cloud-dark hover:text-ink'
      }`}
    >
      {children}
    </button>
  );
}

function YourUsage() {
  const summary = useQuery({ queryKey: ['summary'], queryFn: api.summary });
  const usage = useQuery({
    queryKey: ['desktop_usage'],
    queryFn: () => api.desktopUsage(30),
  });
  const heatmap = useQuery({
    queryKey: ['heatmap'],
    queryFn: () => api.heatmap(180),
  });

  const s = summary.data;
  const wpm = s?.wpm_30d ?? 0;
  const top = topPercentForWpm(wpm);
  const delta = s ? deltaPct(s.words_this_month, s.words_prev_month) : 0;

  return (
    <div className="flex flex-col gap-6">
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <KpiCard
          title="Words per minute"
          value={wpm.toFixed(0)}
          subtitle="last 30 days"
          right={<span className="pill-accent">Top {top}%</span>}
          chart={<HalfDonut percent={Math.min(100, wpm)} />}
        />
        <KpiCard
          title="Fixes made by Whisper"
          value={s?.fixes_count ?? 0}
          subtitle="words corrected · dictionary fixes"
          right={<span className="pill-mute">Coming soon</span>}
        />
        <KpiCard
          title="Total words dictated"
          value={s ? s.total_words.toLocaleString() : '—'}
          subtitle={s ? `${s.words_this_month.toLocaleString()} in last 30 days` : ''}
          right={s ? <DeltaBadge deltaPct={delta} /> : null}
        />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="card p-5">
          <div className="eyebrow mb-4">Desktop usage</div>
          {usage.isLoading ? (
            <div className="text-[14px] text-cloud-dark">Loading…</div>
          ) : (usage.data?.length ?? 0) === 0 ? (
            <div className="text-[14px] text-cloud-dark">
              No dictations yet — start using Say to populate this.
            </div>
          ) : (
            <ul>
              {usage.data!.slice(0, 6).map((row) => (
                <UsageGauge
                  key={row.target_app}
                  label={appLabel(row.target_app)}
                  share={row.share}
                  count={row.count}
                />
              ))}
            </ul>
          )}
        </div>

        {heatmap.data ? (
          <StreakHeatmap data={heatmap.data} />
        ) : (
          <div className="card p-5 text-[14px] text-cloud-dark">Loading heatmap…</div>
        )}
      </div>
    </div>
  );
}

function YourVoice() {
  return (
    <div className="card p-10 max-w-prose">
      <h3 className="font-serif text-h3 font-normal text-ink mb-2">Your Voice</h3>
      <p className="text-[14px] text-cloud-dark leading-relaxed">
        Coming soon. We'll surface tone, pacing, and filler patterns once enough local data is
        captured — computed on this Mac, never uploaded.
      </p>
    </div>
  );
}
