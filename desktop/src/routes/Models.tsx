import { useQuery } from '@tanstack/react-query';
import { api } from '../lib/api';
import { ModelCard, formatBytes } from '../components/ModelCard';

export function Models() {
  const { data, isLoading, error } = useQuery({
    queryKey: ['models'],
    queryFn: api.models,
    // Poll fast only while a download is in flight so the progress bar moves;
    // otherwise rely on the default staleTime (no needless requests).
    refetchInterval: (query) =>
      query.state.data?.models.some((m) => m.download.state === 'downloading') ? 800 : false,
  });

  return (
    <div>
      <header className="mb-9">
        <p className="eyebrow mb-3">Choose your engine</p>
        <h1 className="display text-[2.75rem]">Models</h1>
      </header>

      {/* Privacy promise + total on-disk footprint. */}
      <div className="card p-5 mb-6 flex items-center justify-between gap-6">
        <div className="max-w-prose">
          <div className="text-[14px] text-ink">Every model runs fully on this Mac.</div>
          <div className="text-[12px] text-cloud-dark mt-1 leading-relaxed">
            Pick speed or accuracy to match the moment — switching applies instantly, no relaunch.
            Models download straight from Hugging Face to your local cache; audio and transcripts
            never leave the device.
          </div>
        </div>
        <div className="text-right shrink-0">
          <div className="stat-number text-[1.75rem]">{formatBytes(data?.cache_bytes ?? 0)}</div>
          <div className="eyebrow mt-1">On disk</div>
        </div>
      </div>

      {isLoading ? (
        <div className="text-[14px] text-cloud-dark">Loading models…</div>
      ) : error ? (
        <div className="card p-5 text-[14px] text-crail-deep">
          Couldn't reach the dictation engine. Make sure Say is running.
        </div>
      ) : (
        <div className="flex flex-col gap-3">
          {data!.models.map((m) => (
            <ModelCard key={m.id} model={m} />
          ))}
        </div>
      )}
    </div>
  );
}
