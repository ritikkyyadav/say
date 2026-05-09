import { useState } from 'react';
import { api, Transcript } from '../lib/api';

function formatTimeOfDay(iso: string): string {
  try {
    return new Date(iso).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
  } catch {
    return iso;
  }
}

export function TranscriptCard({ row }: { row: Transcript }) {
  const [copied, setCopied] = useState(false);

  const onCopy = async () => {
    try {
      await navigator.clipboard.writeText(row.text);
      setCopied(true);
      api.copyTranscript(row.id).catch(() => {});
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard denied — silently no-op */
    }
  };

  return (
    <article className="card group p-4 relative">
      <div className="flex items-center justify-between text-[12px] text-ink-mute mb-2">
        <span>{formatTimeOfDay(row.ts_utc)}</span>
        <span className="opacity-0 group-hover:opacity-100 transition-opacity">
          <button
            onClick={onCopy}
            className="pill-mute hover:bg-accent/10 hover:text-accent"
            aria-label="Copy transcript"
          >
            {copied ? 'Copied' : 'Copy'}
          </button>
        </span>
      </div>
      <p className="text-ink leading-snug whitespace-pre-wrap">{row.text}</p>
    </article>
  );
}
