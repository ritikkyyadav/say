import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  api,
  invalidateEngineEndpoint,
  revealPath,
  RecordingsConfig,
  RecordingsConfigPatch,
  RetentionMode,
} from '../lib/api';
import { groupByDay } from '../lib/grouping';
import { formatBytes } from '../components/ModelCard';
import { RecordingRow } from '../components/RecordingRow';

const GIB = 1024 * 1024 * 1024;

function Switch({
  on,
  onChange,
  disabled,
}: {
  on: boolean;
  onChange: (v: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <button
      role="switch"
      aria-checked={on}
      disabled={disabled}
      onClick={() => onChange(!on)}
      className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors disabled:opacity-50 ${
        on ? 'bg-crail' : 'bg-cloud-light'
      }`}
    >
      <span
        className={`inline-block h-5 w-5 transform rounded-full bg-paper shadow transition-transform ${
          on ? 'translate-x-[22px]' : 'translate-x-0.5'
        }`}
      />
    </button>
  );
}

function RetentionControl({
  config,
  onChange,
  busy,
}: {
  config: RecordingsConfig;
  onChange: (patch: RecordingsConfigPatch) => void;
  busy: boolean;
}) {
  const [days, setDays] = useState(String(config.retention_days));
  const [gb, setGb] = useState(String(Math.max(1, Math.round(config.max_bytes / GIB))));

  useEffect(() => setDays(String(config.retention_days)), [config.retention_days]);
  useEffect(
    () => setGb(String(Math.max(1, Math.round(config.max_bytes / GIB)))),
    [config.max_bytes],
  );

  const seg = (mode: RetentionMode, label: string) => (
    <button
      onClick={() => onChange({ retention_mode: mode })}
      disabled={busy}
      className={
        config.retention_mode === mode
          ? 'px-3 py-1.5 rounded-md text-[13px] bg-ink text-paper'
          : 'px-3 py-1.5 rounded-md text-[13px] text-cloud-dark hover:text-ink'
      }
    >
      {label}
    </button>
  );

  const commitDays = () => {
    const n = Math.max(1, Math.round(Number(days) || config.retention_days));
    onChange({ retention_mode: 'days', retention_days: n });
  };
  const commitGb = () => {
    const n = Math.max(1, Math.round(Number(gb) || 1));
    onChange({ retention_mode: 'size', max_bytes: n * GIB });
  };

  return (
    <div className="flex flex-col gap-2">
      <div className="eyebrow">Keep recordings</div>
      <div className="flex items-center gap-1 p-1 rounded-lg bg-book-cloth border border-cloud-light w-fit">
        {seg('keep_all', 'Everything')}
        {seg('days', 'For a while')}
        {seg('size', 'Up to a size')}
      </div>
      {config.retention_mode === 'days' && (
        <div className="flex items-center gap-2 text-[13px] text-cloud-dark">
          <span>Delete clips older than</span>
          <input
            type="number"
            min={1}
            value={days}
            onChange={(e) => setDays(e.target.value)}
            onBlur={commitDays}
            onKeyDown={(e) => e.key === 'Enter' && commitDays()}
            className="w-16 px-2 py-1 rounded-md border border-cloud-light bg-paper tnum text-ink"
          />
          <span>days</span>
        </div>
      )}
      {config.retention_mode === 'size' && (
        <div className="flex items-center gap-2 text-[13px] text-cloud-dark">
          <span>Keep newest clips up to</span>
          <input
            type="number"
            min={1}
            value={gb}
            onChange={(e) => setGb(e.target.value)}
            onBlur={commitGb}
            onKeyDown={(e) => e.key === 'Enter' && commitGb()}
            className="w-16 px-2 py-1 rounded-md border border-cloud-light bg-paper tnum text-ink"
          />
          <span>GB — oldest are removed first</span>
        </div>
      )}
    </div>
  );
}

function ControlBar({
  config,
  notice,
  onChange,
  onExport,
  onClear,
  busy,
  exporting,
  clearing,
}: {
  config: RecordingsConfig;
  notice: string | null;
  onChange: (patch: RecordingsConfigPatch) => void;
  onExport: () => void;
  onClear: () => void;
  busy: boolean;
  exporting: boolean;
  clearing: boolean;
}) {
  return (
    <div className="card p-5 mb-8 flex flex-col gap-5">
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-3">
            <Switch
              on={config.save_recordings}
              disabled={busy}
              onChange={(v) => onChange({ save_recordings: v })}
            />
            <span className="text-[15px] text-ink font-medium">Save audio receipts</span>
          </div>
          <p className="text-[13px] text-cloud-dark mt-2 max-w-prose leading-relaxed">
            Keep a local WAV of every dictation so you can replay, verify and export
            exactly what you said. Stored on this Mac only — never uploaded.
          </p>
        </div>
        <div className="text-right shrink-0">
          <div className="stat-number text-[1.75rem]">{config.count}</div>
          <div className="eyebrow mt-1">{formatBytes(config.total_bytes)} on disk</div>
        </div>
      </div>

      <div className="border-t border-cloud-light pt-5">
        <RetentionControl config={config} onChange={onChange} busy={busy} />
      </div>

      <div className="border-t border-cloud-light pt-5 flex items-center gap-3">
        <button onClick={onExport} disabled={exporting || config.count === 0} className="btn-primary">
          {exporting ? 'Exporting…' : 'Export all (.zip)'}
        </button>
        <button
          onClick={onClear}
          disabled={clearing || config.count === 0}
          className="pill-mute hover:text-crail-deep hover:border-crail/30"
        >
          {clearing ? 'Clearing…' : 'Clear all'}
        </button>
        {notice && <span className="text-[13px] text-cloud-dark ml-1">{notice}</span>}
      </div>
    </div>
  );
}

export function Recordings() {
  const qc = useQueryClient();
  const cfg = useQuery({ queryKey: ['recordings-config'], queryFn: api.recordingsConfig });
  const list = useQuery({ queryKey: ['recordings'], queryFn: () => api.recordings(500) });
  const [notice, setNotice] = useState<string | null>(null);

  const setConfig = useMutation({
    mutationFn: (patch: RecordingsConfigPatch) => api.setRecordingsConfig(patch),
    onSuccess: (data) => {
      qc.setQueryData(['recordings-config'], data);
      qc.invalidateQueries({ queryKey: ['recordings'] });
      setNotice(null);
    },
    onError: (e) => setNotice(e instanceof Error ? e.message : 'Could not save setting'),
  });

  const exportAll = useMutation({
    mutationFn: () => api.exportRecordings(),
    onSuccess: (res) => {
      if (res.count > 0 && res.path) {
        setNotice(`Exported ${res.count} clip${res.count === 1 ? '' : 's'} — revealing in Finder`);
        revealPath(res.path);
      } else {
        setNotice('Nothing to export yet.');
      }
    },
    onError: (e) => setNotice(e instanceof Error ? e.message : 'Export failed'),
  });

  const clearAll = useMutation({
    mutationFn: () => api.clearRecordings(),
    onSuccess: (res) => {
      setNotice(`Deleted ${res.deleted} recording${res.deleted === 1 ? '' : 's'}.`);
      qc.invalidateQueries({ queryKey: ['recordings'] });
      qc.invalidateQueries({ queryKey: ['recordings-config'] });
    },
    onError: (e) => setNotice(e instanceof Error ? e.message : 'Could not clear recordings'),
  });

  const onClear = () => {
    if (window.confirm('Delete every saved recording? This cannot be undone.')) {
      clearAll.mutate();
    }
  };

  const config = cfg.data;
  const groups = list.data ? groupByDay(list.data) : [];

  if (cfg.isError || list.isError) {
    const err = cfg.error ?? list.error;
    const msg = err instanceof Error ? err.message : 'Unknown error';
    return (
      <div>
        <header className="mb-10">
          <p className="eyebrow mb-3">Audio receipts</p>
          <h1 className="display text-[2.75rem]">Recordings</h1>
        </header>
        <div className="card p-6">
          <h3 className="font-serif text-h3 font-normal text-ink mb-2">
            Can't reach the dictation engine
          </h3>
          <p className="text-cloud-dark text-[14px] mb-4">Last error: <span className="font-mono">{msg}</span></p>
          <button
            onClick={() => {
              invalidateEngineEndpoint();
              qc.invalidateQueries();
            }}
            className="btn-primary"
          >
            Retry
          </button>
        </div>
      </div>
    );
  }

  return (
    <div>
      <header className="mb-10">
        <p className="eyebrow mb-3">Audio receipts</p>
        <h1 className="display text-[2.75rem]">Recordings</h1>
      </header>

      {config && (
        <ControlBar
          config={config}
          notice={notice}
          onChange={(patch) => setConfig.mutate(patch)}
          onExport={() => exportAll.mutate()}
          onClear={onClear}
          busy={setConfig.isPending}
          exporting={exportAll.isPending}
          clearing={clearAll.isPending}
        />
      )}

      {list.isLoading ? (
        <div className="text-cloud-dark text-[14px]">Loading recordings…</div>
      ) : groups.length === 0 ? (
        <div className="card p-10 max-w-prose">
          <h3 className="font-serif text-h3 font-normal text-ink mb-2">
            {config?.save_recordings ? 'No recordings yet' : 'Saving is off'}
          </h3>
          <p className="text-cloud-dark text-[14px] leading-relaxed">
            {config?.save_recordings ? (
              <>
                Hold <span className="kbd">Fn</span> and speak. Each dictation will be saved
                here as an audio receipt you can replay and export.
              </>
            ) : (
              <>
                Turn on <span className="text-ink font-medium">Save audio receipts</span> above to
                start keeping a local copy of each dictation. Until then, audio stays
                in-memory only and is never written to disk.
              </>
            )}
          </p>
        </div>
      ) : (
        <div className="flex flex-col gap-10">
          {groups.map((g) => (
            <section key={g.isoDate}>
              <h2 className="eyebrow mb-4">{g.label}</h2>
              <div className="flex flex-col gap-3">
                {g.rows.map((row) => (
                  <RecordingRow key={row.id} rec={row} />
                ))}
              </div>
            </section>
          ))}
        </div>
      )}
    </div>
  );
}
