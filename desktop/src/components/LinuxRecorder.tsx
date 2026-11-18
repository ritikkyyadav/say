import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../lib/api';
import type { PlatformInfo } from '../lib/api';
import { copyText } from '../lib/clipboard';

export function LinuxRecorder({ platform }: { platform: PlatformInfo }) {
  const qc = useQueryClient();
  const status = useQuery({
    queryKey: ['dictation'], queryFn: api.dictation, refetchInterval: 300,
    staleTime: 0, retry: false,
  });
  const action = useMutation({
    mutationFn: api.controlDictation,
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ['dictation'] });
      qc.invalidateQueries({ queryKey: ['transcripts'] });
      qc.invalidateQueries({ queryKey: ['summary'] });
    },
  });
  const [copied, setCopied] = useState(false);
  const [copyError, setCopyError] = useState<string | null>(null);
  const data = status.data;
  const state = status.isError ? 'error' : data?.state ?? 'loading';
  const recording = state === 'recording';
  const processing = state === 'processing';
  const latest = data?.latest;
  const manual = !platform.automatic_insertion;
  const disabled = action.isPending || status.isError || processing ||
    (!recording && !data?.can_record);

  useEffect(() => { setCopied(false); setCopyError(null); }, [latest?.text, latest?.id]);
  useEffect(() => {
    if (!latest?.text) return;
    for (const key of ['vocabulary', 'summary', 'transcripts', 'desktop_usage', 'heatmap']) {
      qc.invalidateQueries({ queryKey: [key] });
    }
  }, [latest?.text, latest?.id, qc]);
  useEffect(() => {
    const cancel = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && recording && !action.isPending && !event.defaultPrevented) {
        event.preventDefault();
        action.mutate('cancel');
      }
    };
    window.addEventListener('keydown', cancel);
    return () => window.removeEventListener('keydown', cancel);
  }, [recording, action.isPending, action.mutate]);

  const copy = async () => {
    if (!latest) return;
    setCopyError(null);
    try {
      await copyText(latest.text);
      setCopied(true);
    } catch (error) {
      setCopyError(error instanceof Error ? error.message : 'Select the text and copy it manually.');
    }
  };
  const title = recording ? 'Listening. Take your time.' : processing ? 'Finishing your words…' :
    status.isError ? 'Waiting for the local engine' : 'Speak here. Copy anywhere.';
  const detail = platform.session === 'headless'
    ? 'Waiting for the local engine to identify this desktop session. Recording stays off until it is ready.'
    : manual
    ? 'Wayland keeps global keys and automatic insertion under desktop control. Record here, then copy and paste your words.'
    : data?.shortcut_started
      ? `Hold ${platform.hotkey_label ?? 'Ctrl+Space'} in another app to type there. A quick tap starts hands-free; tap again to finish. Recording here keeps the result for copy.`
      : 'The global shortcut is not available yet. You can still record here and copy the result.';
  const error = action.error instanceof Error ? action.error.message : copyError;

  return (
    <section className={`card linux-recorder${recording ? ' is-recording' : ''}`}
      data-testid="linux-recorder" data-state={state} aria-label="Linux dictation controls">
      <div className="linux-recorder-top">
        <div className="linux-recorder-copy">
          <span className="linux-eyebrow">{platform.session === 'headless' ? 'Linux · connecting' : manual ? 'Wayland · manual paste' : 'X11 · local dictation'}</span>
          <h2>{title}</h2>
          <p>{detail}</p>
        </div>
        <div className="linux-recorder-actions">
          <button className="btn acc" data-testid="linux-record-toggle" disabled={disabled}
            onClick={() => action.mutate(recording ? 'stop' : 'start')}>
            {processing ? 'Processing…' : action.isPending ? 'Please wait…' : recording ? 'Stop' : 'Record for copy'}
          </button>
          {recording && <button className="btn gho" data-testid="linux-cancel"
            disabled={action.isPending} onClick={() => action.mutate('cancel')}>Cancel</button>}
        </div>
      </div>
      <p className="linux-recorder-status" role="status" aria-live="polite">
        {status.isError ? 'The engine is reconnecting. Saved transcripts are not removed.' :
          data?.message || 'Starting the local engine…'}
      </p>
      {latest && !recording && !processing && (
        <div className="linux-recorder-result">
          <label htmlFor="linux-result">{latest.delivery === 'inserted' ? 'Last inserted text' : 'Ready to copy'}</label>
          <textarea id="linux-result" data-testid="linux-result-text" value={latest.text} readOnly rows={3}
            aria-label="Latest complete dictation" />
          <button className="btn pri" data-testid="linux-copy-result" onClick={copy}>
            {copied ? 'Copied' : 'Copy text'}
          </button>
        </div>
      )}
      {error && <p className="linux-recorder-error" role="alert">{error}</p>}
    </section>
  );
}
