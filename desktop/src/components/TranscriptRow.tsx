// One dictation in the feed: what you said, where it landed, and — when the
// clip was kept — a play button and a waveform that fills as it plays.
//
// The audio is fetched lazily on first play. Keeping audio is opt-in, so for
// most rows `has_audio` is false and the control is simply disabled rather
// than hidden: a row that silently loses its play button when you turn the
// setting off looks broken.

import { useEffect, useRef, useState } from 'react';
import { api, Transcript } from '../lib/api';
import { appLabel } from '../lib/appNames';
import { mmss } from '../lib/format';

// A stable pseudo-waveform per row. The real envelope would mean decoding the
// WAV; this is honest about being decoration — it never claims to be the
// signal, it just gives the progress fill something to travel through.
function bars(seed: number, n = 64): number[] {
  return Array.from({ length: n }, (_, k) => 18 + Math.abs(Math.sin((seed + 1) * 7.3 + k * 1.7)) * 82);
}

export function TranscriptRow({ row }: { row: Transcript }) {
  const [open, setOpen] = useState(false);
  const [playing, setPlaying] = useState(false);
  const [progress, setProgress] = useState(0);
  const [copied, setCopied] = useState(false);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const urlRef = useRef<string | null>(null);

  const shape = bars(row.id);

  useEffect(
    () => () => {
      audioRef.current?.pause();
      if (urlRef.current) URL.revokeObjectURL(urlRef.current);
    },
    [],
  );

  const toggle = async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (!row.has_audio) return;

    if (playing) {
      audioRef.current?.pause();
      setPlaying(false);
      return;
    }
    try {
      if (!audioRef.current) {
        const url = await api.recordingAudioUrl(row.id);
        urlRef.current = url;
        const el = new Audio(url);
        el.addEventListener('timeupdate', () =>
          setProgress(el.duration ? el.currentTime / el.duration : 0),
        );
        el.addEventListener('ended', () => {
          setPlaying(false);
          setProgress(0);
        });
        audioRef.current = el;
      }
      await audioRef.current.play();
      setPlaying(true);
    } catch {
      setPlaying(false);
    }
  };

  const copy = async (e: React.MouseEvent) => {
    e.stopPropagation();
    try {
      await navigator.clipboard.writeText(row.text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1400);
    } catch {
      /* clipboard unavailable — nothing useful to say about it */
    }
    api.copyTranscript(row.id).catch(() => {});
  };

  const cursor = Math.floor(progress * shape.length);
  const time = new Date(row.ts_utc).toLocaleTimeString('en', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  });

  return (
    <div
      className={`trow${open ? ' open' : ''}${playing ? ' playing' : ''}`}
      onClick={() => setOpen((v) => !v)}
    >
      <button
        className="play"
        onClick={toggle}
        disabled={!row.has_audio}
        title={row.has_audio ? (playing ? 'Pause' : 'Play') : 'No audio kept for this one'}
        aria-label={playing ? 'Pause' : 'Play'}
      >
        {playing ? (
          <svg viewBox="0 0 24 24" fill="currentColor">
            <rect x="8" y="6" width="3" height="12" rx="1" />
            <rect x="13" y="6" width="3" height="12" rx="1" />
          </svg>
        ) : (
          <svg viewBox="0 0 24 24" fill="currentColor">
            <path d="M8 5.5v13l11-6.5z" />
          </svg>
        )}
      </button>

      <div className="tbody">
        <div className="tmeta">
          <b>{time}</b>
          <i className="sep" />
          <b>{appLabel(row.target_app)}</b>
          <i className="sep" />
          {mmss(row.audio_seconds)}
          <i className="sep" />
          {row.word_count} words
        </div>
        <div className="ttext">{row.text}</div>
        <div className="wave">
          {shape.map((h, k) => (
            <i
              key={k}
              className={k < cursor ? 'done' : k === cursor ? 'cur' : ''}
              style={{ height: `${h}%` }}
            />
          ))}
        </div>
      </div>

      <div className="tacts">
        <button className="ghost" onClick={copy}>
          {copied ? (
            '✓ Copied'
          ) : (
            <>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
                <rect x="9" y="9" width="11" height="11" rx="2" />
                <path d="M5 15V5a2 2 0 012-2h8" />
              </svg>
              Copy
            </>
          )}
        </button>
      </div>
    </div>
  );
}
