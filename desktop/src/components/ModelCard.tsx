import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { api, ModelInfo } from '../lib/api';

export function formatBytes(n: number): string {
  if (!n || n < 0) return '0 MB';
  const gb = n / 1e9;
  if (gb >= 1) return `${gb.toFixed(gb >= 10 ? 0 : 1)} GB`;
  return `${Math.max(1, Math.round(n / 1e6))} MB`;
}

// Five-dot meter — the editorial way to show a 1..5 tier without a bar chart.
function Meter({ label, value }: { label: string; value: number }) {
  return (
    <div className="flex items-center gap-1.5">
      <span className="eyebrow text-[10px]">{label}</span>
      <span className="flex gap-0.5" aria-label={`${label} ${value} of 5`}>
        {[1, 2, 3, 4, 5].map((i) => (
          <span
            key={i}
            className={`w-1.5 h-1.5 rounded-full ${i <= value ? 'bg-crail' : 'bg-cloud-light'}`}
          />
        ))}
      </span>
    </div>
  );
}

export function ModelCard({ model }: { model: ModelInfo }) {
  const qc = useQueryClient();
  const invalidate = () => qc.invalidateQueries({ queryKey: ['models'] });
  const [confirmRemove, setConfirmRemove] = useState(false);

  const setActive = useMutation({ mutationFn: () => api.setActiveModel(model.id), onSuccess: invalidate });
  const download = useMutation({ mutationFn: () => api.downloadModel(model.id), onSuccess: invalidate });
  const cancel = useMutation({ mutationFn: () => api.cancelDownload(model.id), onSuccess: invalidate });
  const remove = useMutation({
    mutationFn: () => api.deleteModel(model.id),
    onSuccess: () => {
      setConfirmRemove(false);
      invalidate();
    },
  });

  const dl = model.download;
  const isDownloading = dl.state === 'downloading' || download.isPending;
  const sizeText = model.downloaded ? formatBytes(model.size_bytes) : formatBytes(model.download_bytes);
  const langText = model.languages === 'multilingual' ? '99 languages' : 'English';
  const muterror = setActive.error || download.error || remove.error || cancel.error;

  return (
    <article
      className={`card p-5 ${model.active ? 'border-crail/50 bg-crail/[0.03]' : ''}`}
    >
      <div className="flex items-start justify-between gap-6">
        {/* ── Identity + meta ─────────────────────────────────────────── */}
        <div className="min-w-0">
          <div className="flex items-center gap-2.5 flex-wrap">
            <h3 className="font-serif text-[1.125rem] text-ink leading-none">{model.name}</h3>
            <span className="pill-mute capitalize">{model.family}</span>
            {model.recommended ? <span className="pill-accent">Recommended</span> : null}
          </div>
          <p className="text-[13px] text-cloud-dark mt-2 leading-relaxed max-w-prose">
            {model.description}
          </p>
          <div className="flex items-center gap-x-5 gap-y-2 mt-3.5 flex-wrap">
            <Meter label="Speed" value={model.speed} />
            <Meter label="Accuracy" value={model.accuracy} />
            <span className="text-[12px] text-cloud-dark tnum">{model.params} params</span>
            <span className="text-[12px] text-cloud-dark tnum">{sizeText}</span>
            <span className="text-[12px] text-cloud-dark">{langText}</span>
          </div>
        </div>

        {/* ── State-driven action area ────────────────────────────────── */}
        <div className="shrink-0 flex flex-col items-end gap-2 min-w-[150px]">
          {model.active ? (
            <span className="pill-accent">
              <span className="w-1.5 h-1.5 rounded-full bg-crail" />
              Active
            </span>
          ) : isDownloading ? (
            <div className="w-[150px]">
              <div className="h-1.5 rounded-full bg-cloud-light overflow-hidden">
                <div
                  className="h-full bg-crail transition-all duration-500"
                  style={{ width: `${Math.max(4, dl.percent)}%` }}
                />
              </div>
              <div className="flex items-center justify-between mt-1.5">
                <span className="text-[11px] text-cloud-dark tnum">Downloading… {dl.percent}%</span>
                <button
                  className="text-[11px] text-cloud-dark hover:text-crail-deep transition-colors"
                  onClick={() => cancel.mutate()}
                  disabled={cancel.isPending}
                >
                  {cancel.isPending ? 'Cancelling…' : 'Cancel'}
                </button>
              </div>
            </div>
          ) : model.downloaded ? (
            <>
              <button
                className="btn-primary w-full"
                onClick={() => setActive.mutate()}
                disabled={setActive.isPending}
              >
                {setActive.isPending ? 'Switching…' : 'Use this model'}
              </button>
              {model.bundled ? (
                <span className="text-[11px] text-cloud-dark">Bundled · always available</span>
              ) : confirmRemove ? (
                <div className="flex items-center gap-2">
                  <button
                    className="text-[12px] text-crail-deep font-medium hover:underline"
                    onClick={() => remove.mutate()}
                    disabled={remove.isPending}
                  >
                    {remove.isPending ? 'Removing…' : 'Confirm remove'}
                  </button>
                  <span className="text-cloud-mid">·</span>
                  <button
                    className="text-[12px] text-cloud-dark hover:text-ink"
                    onClick={() => setConfirmRemove(false)}
                    disabled={remove.isPending}
                  >
                    Keep
                  </button>
                </div>
              ) : (
                <button
                  className="text-[12px] text-cloud-dark hover:text-crail-deep transition-colors"
                  onClick={() => setConfirmRemove(true)}
                >
                  Remove from disk
                </button>
              )}
            </>
          ) : (
            <button
              className="btn-secondary w-full"
              onClick={() => download.mutate()}
              disabled={download.isPending}
            >
              {dl.state === 'error' ? 'Retry download' : `Download · ${formatBytes(model.download_bytes)}`}
            </button>
          )}

          {dl.state === 'error' && !isDownloading ? (
            <span className="text-[11px] text-crail-deep text-right max-w-[180px]">
              {dl.error ?? 'Download failed'}
            </span>
          ) : null}
          {muterror ? (
            <span className="text-[11px] text-crail-deep text-right max-w-[180px]">
              {(muterror as Error).message}
            </span>
          ) : null}
        </div>
      </div>
    </article>
  );
}
