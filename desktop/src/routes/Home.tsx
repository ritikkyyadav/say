import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api, invalidateEngineEndpoint } from '../lib/api';
import { groupByDay } from '../lib/grouping';
import { TranscriptCard } from '../components/TranscriptCard';
import { PermissionsBanner } from '../components/PermissionsBanner';

function StatTile({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="card p-5">
      <div className="eyebrow">{label}</div>
      <div className="stat-number text-[2.25rem] mt-3">{value}</div>
    </div>
  );
}

function EngineDisconnected({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  const msg = error instanceof Error ? error.message : String(error ?? 'Unknown error');
  return (
    <div className="card p-6">
      <h3 className="font-serif text-h3 font-normal text-ink mb-2">
        Can't reach the dictation engine
      </h3>
      <p className="text-cloud-dark text-[14px] leading-relaxed mb-4 max-w-prose">
        The desktop window talks to the engine over a local HTTP socket. Make sure the
        engine is running, then retry.
      </p>
      <div className="text-[13px] font-mono text-ink bg-book-cloth border border-cloud-light rounded-md px-3 py-2 mb-4 overflow-x-auto">
        ~/.venvs/say/bin/python say/app.py --server
      </div>
      <div className="text-[12px] text-cloud-dark mb-4">
        Last error: <span className="font-mono">{msg}</span>
      </div>
      <button onClick={onRetry} className="btn-primary">
        Retry
      </button>
    </div>
  );
}

export function Home() {
  const queryClient = useQueryClient();
  const profile = useQuery({ queryKey: ['profile'], queryFn: api.profile });
  const summary = useQuery({ queryKey: ['summary'], queryFn: api.summary });
  const transcripts = useQuery({
    queryKey: ['transcripts'],
    queryFn: () => api.transcripts(200),
  });

  const groups = transcripts.data ? groupByDay(transcripts.data) : [];
  const firstName = profile.data?.first_name ?? 'there';

  const retry = () => {
    invalidateEngineEndpoint();
    queryClient.invalidateQueries();
  };

  return (
    <div className="grid grid-cols-1 lg:grid-cols-[1fr_300px] gap-12">
      <div className="min-w-0">
        <header className="mb-10">
          <p className="eyebrow mb-3">Your dictation</p>
          <h1 className="display text-[2.75rem]">Welcome back, {firstName}</h1>
        </header>

        <PermissionsBanner />

        {transcripts.isLoading ? (
          <div className="text-cloud-dark text-[14px]">Loading transcripts…</div>
        ) : transcripts.isError ? (
          <EngineDisconnected error={transcripts.error} onRetry={retry} />
        ) : groups.length === 0 ? (
          <div className="card p-10 max-w-prose">
            <h3 className="font-serif text-h3 font-normal text-ink mb-2">No dictations yet</h3>
            <p className="text-cloud-dark text-[14px] leading-relaxed">
              Hold <span className="kbd">Fn</span> and speak. Release, and the text appears in
              whatever app you're working in. Everything you say stays on this Mac.
            </p>
          </div>
        ) : (
          <div className="flex flex-col gap-10">
            {groups.map((g) => (
              <section key={g.isoDate}>
                <h2 className="eyebrow mb-4">{g.label}</h2>
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

      <aside className="flex flex-col gap-4">
        <StatTile
          label="Total words"
          value={summary.data ? summary.data.total_words.toLocaleString() : '—'}
        />
        <StatTile
          label="Words per minute"
          value={summary.data ? summary.data.wpm_30d.toFixed(0) : '—'}
        />
        <StatTile
          label="Streak · days"
          value={summary.data ? summary.data.streak_days : '—'}
        />
      </aside>
    </div>
  );
}
