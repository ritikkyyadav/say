// One dictation in the feed: what you said, where it landed, and — when the
// clip was kept — a play button and a waveform that fills as it plays.
//
// The audio is fetched lazily on first play. Keeping audio is opt-in, so for
// most rows `has_audio` is false and the control is simply disabled rather
// than hidden: a row that silently loses its play button when you turn the
// setting off looks broken.
//
// Fix lets you write your own version in place. That pair — what Say typed,
// what you meant — is the one label that works in every app, including the
// terminals and chat windows the read-back cannot see into. `row.text` is never
// rewritten; the row shows your version and keeps Say's underneath.

import { useEffect, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
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
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState('');
  const [saving, setSaving] = useState(false);
  const [failed, setFailed] = useState(false);
  const qc = useQueryClient();
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const urlRef = useRef<string | null>(null);

  const shape = bars(row.id);
  // What the row shows and copies: your version when there is one.
  const shown = row.corrected ?? row.text;
  const fixed = row.corrected !== null && row.corrected !== row.text;
  const checked = row.corrected !== null && row.corrected === row.text;

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
      await navigator.clipboard.writeText(shown);
      setCopied(true);
      setTimeout(() => setCopied(false), 1400);
    } catch {
      /* clipboard unavailable — nothing useful to say about it */
    }
    api.copyTranscript(row.id).catch(() => {});
  };

  const startFix = (e: React.MouseEvent) => {
    e.stopPropagation();
    setDraft(shown);
    setFailed(false);
    setEditing(true);
    setOpen(true);
  };

  const settle = async (work: () => Promise<unknown>) => {
    setSaving(true);
    setFailed(false);
    try {
      await work();
      await qc.invalidateQueries({ queryKey: ['transcripts'] });
      setEditing(false);
    } catch {
      setFailed(true);
    } finally {
      setSaving(false);
    }
  };
  const save = () => settle(() => api.correctTranscript(row.id, draft));
  const undo = () => settle(() => api.clearTranscriptCorrection(row.id));

  const onEditKey = (e: React.KeyboardEvent) => {
    e.stopPropagation();
    if (e.key === 'Escape') setEditing(false);
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey) && draft.trim()) save();
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
      onClick={() => !editing && setOpen((v) => !v)}
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
          {row.review && !fixed && !checked && (
            <>
              <i className="sep" />
              <span className="tfix" title="A second model wrote this differently">
                {row.review.differing_words} {row.review.differing_words === 1 ? 'word' : 'words'} in doubt
              </span>
            </>
          )}
          {(fixed || checked) && (
            <>
              <i className="sep" />
              <span className="tfix">{fixed ? 'fixed by you' : 'checked'}</span>
            </>
          )}
        </div>
        {editing ? (
          <div onClick={(e) => e.stopPropagation()}>
            <textarea
              className="tedit"
              value={draft}
              autoFocus
              spellCheck
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={onEditKey}
              aria-label="Your version of this transcript"
            />
            <div className="tedit-bar">
              <button className="ghost solid" onClick={save} disabled={saving || !draft.trim()}>
                {draft.trim() === row.text.trim() ? 'It was right' : 'Save'}
              </button>
              <button className="ghost" onClick={() => setEditing(false)} disabled={saving}>
                Cancel
              </button>
              {row.corrected !== null && (
                <button className="ghost" onClick={undo} disabled={saving}>
                  Remove my version
                </button>
              )}
              {failed ? (
                <span className="err">Could not save. The engine did not answer.</span>
              ) : (
                <span className="hint">⌘↩ to save · Esc to cancel</span>
              )}
            </div>
          </div>
        ) : (
          <div className="ttext">{shown}</div>
        )}
        {fixed && !editing && (
          <div className="torig">
            <b>Say typed:</b> {row.text}
          </div>
        )}
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
        {!editing && (
          <button className="ghost" onClick={startFix} title="Write what you actually said">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
              <path d="M4 20h4L19 9l-4-4L4 16v4z" />
              <path d="M13.5 6.5l4 4" />
            </svg>
            Fix
          </button>
        )}
      </div>
    </div>
  );
}
