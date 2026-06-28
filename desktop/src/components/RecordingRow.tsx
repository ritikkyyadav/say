import { useEffect, useRef, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { api, Recording } from '../lib/api';
import { appLabel } from '../lib/appNames';
import { formatBytes } from './ModelCard';

function timeOfDay(iso: string): string {
  try {
    return new Date(iso).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
  } catch {
    return iso;
  }
}

function fmtDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds <= 0) return '0:00';
  const s = Math.round(seconds);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

function PlayIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 14 14" fill="currentColor" aria-hidden>
      <path d="M3 1.8v10.4c0 .6.66.96 1.16.63l8-5.2a.75.75 0 0 0 0-1.26l-8-5.2A.75.75 0 0 0 3 1.8Z" />
    </svg>
  );
}

function PauseIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 14 14" fill="currentColor" aria-hidden>
      <rect x="2.5" y="2" width="3" height="10" rx="1" />
      <rect x="8.5" y="2" width="3" height="10" rx="1" />
    </svg>
  );
}

function Spinner() {
  return (
    <span className="inline-block w-3.5 h-3.5 rounded-full border-2 border-cloud-light border-t-crail animate-spin" />
  );
}

export function RecordingRow({ rec }: { rec: Recording }) {
  const qc = useQueryClient();
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const urlRef = useRef<string | null>(null);
  const [playing, setPlaying] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Release the blob URL + audio element when the row unmounts.
  useEffect(() => {
    return () => {
      audioRef.current?.pause();
      if (urlRef.current) URL.revokeObjectURL(urlRef.current);
      urlRef.current = null;
    };
  }, []);

  const ensureAudio = async (): Promise<HTMLAudioElement> => {
    let el = audioRef.current;
    if (!el) {
      el = new Audio();
      el.addEventListener('ended', () => setPlaying(false));
      el.addEventListener('pause', () => setPlaying(false));
      el.addEventListener('play', () => setPlaying(true));
      audioRef.current = el;
    }
    if (!urlRef.current) {
      setLoading(true);
      try {
        const u = await api.recordingAudioUrl(rec.id);
        urlRef.current = u;
        el.src = u;
      } finally {
        setLoading(false);
      }
    }
    return el;
  };

  const toggle = async () => {
    setError(null);
    try {
      const el = await ensureAudio();
      if (el.paused) await el.play();
      else el.pause();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Playback failed');
      setPlaying(false);
    }
  };

  const del = useMutation({
    mutationFn: () => api.deleteRecording(rec.id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['recordings'] });
      qc.invalidateQueries({ queryKey: ['recordings-config'] });
    },
  });

  return (
    <article className="card group p-4 flex items-start gap-4">
      <button
        onClick={toggle}
        disabled={!rec.has_audio || loading}
        className="shrink-0 mt-0.5 w-10 h-10 rounded-full grid place-items-center border border-cloud-light text-ink hover:border-crail/40 hover:text-crail-deep disabled:opacity-40 transition-colors"
        aria-label={playing ? 'Pause recording' : 'Play recording'}
      >
        {loading ? <Spinner /> : playing ? <PauseIcon /> : <PlayIcon />}
      </button>

      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2.5 text-[12px] text-cloud-dark mb-1.5">
          <span className="tnum">{timeOfDay(rec.ts_utc)}</span>
          <span className="text-cloud-mid">·</span>
          <span>{appLabel(rec.target_app)}</span>
          <span className="text-cloud-mid">·</span>
          <span className="tnum">{fmtDuration(rec.audio_seconds)}</span>
          <span className="text-cloud-mid">·</span>
          <span className="tnum">{formatBytes(rec.size_bytes)}</span>
          <span className="ml-auto opacity-0 group-hover:opacity-100 transition-opacity">
            <button
              onClick={() => del.mutate()}
              disabled={del.isPending}
              className="pill-mute hover:text-crail-deep hover:border-crail/30"
              aria-label="Delete recording"
            >
              {del.isPending ? 'Deleting…' : 'Delete'}
            </button>
          </span>
        </div>
        <p className="font-serif text-[1.0625rem] text-ink leading-relaxed whitespace-pre-wrap">
          {rec.text}
        </p>
        {error && <p className="text-[12px] text-crail-deep mt-1.5">{error}</p>}
      </div>
    </article>
  );
}
