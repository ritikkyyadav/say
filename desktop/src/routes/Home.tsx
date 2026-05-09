import { useQuery } from '@tanstack/react-query';
import { api } from '../lib/api';
import { groupByDay } from '../lib/grouping';
import { TranscriptCard } from '../components/TranscriptCard';

function StatTile({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="card p-4">
      <div className="text-[11px] uppercase tracking-wide text-ink-mute">{label}</div>
      <div className="text-2xl font-semibold tabular-nums mt-1">{value}</div>
    </div>
  );
}

export function Home() {
  const profile = useQuery({ queryKey: ['profile'], queryFn: api.profile });
  const summary = useQuery({ queryKey: ['summary'], queryFn: api.summary });
  const transcripts = useQuery({
    queryKey: ['transcripts'],
    queryFn: () => api.transcripts(200),
  });

  const groups = transcripts.data ? groupByDay(transcripts.data) : [];
  const firstName = profile.data?.first_name ?? 'there';

  return (
    <div className="grid grid-cols-1 lg:grid-cols-[1fr_280px] gap-6">
      <div className="min-w-0">
        <h1 className="text-2xl font-semibold mb-6">
          Welcome back, {firstName}
        </h1>

        {transcripts.isLoading ? (
          <div className="text-ink-mute text-sm">Loading transcripts…</div>
        ) : transcripts.isError ? (
          <div className="card p-4 text-rose-700 text-sm">
            Couldn't reach the dictation engine.
          </div>
        ) : groups.length === 0 ? (
          <div className="card p-8 text-center text-ink-mute">
            <div className="text-base text-ink mb-2">No dictations yet.</div>
            <div className="text-sm">
              Press Fn (or your fallback hotkey) to start.
            </div>
          </div>
        ) : (
          <div className="flex flex-col gap-8">
            {groups.map((g) => (
              <section key={g.isoDate}>
                <h2 className="text-[11px] tracking-widest uppercase text-ink-soft mb-3">
                  {g.label}
                </h2>
                <div className="flex flex-col gap-3">
                  {g.rows.map((row) => (
                    <TranscriptCard key={row.id} row={row} />
                  ))}
                </div>
              </section>
            ))}
          </div>
        )}
      </div>

      <aside className="flex flex-col gap-3">
        <StatTile
          label="Total words"
          value={summary.data ? summary.data.total_words.toLocaleString() : '—'}
        />
        <StatTile
          label="Words per minute"
          value={summary.data ? summary.data.wpm_30d.toFixed(0) : '—'}
        />
        <StatTile
          label="Streak (days)"
          value={summary.data ? summary.data.streak_days : '—'}
        />
      </aside>
    </div>
  );
}
