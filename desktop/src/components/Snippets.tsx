// Snippets and the dictation keys, on the customise page.
//
// A snippet fires only when its trigger is the whole dictation, so the editor
// says that up front: people will otherwise expect "send my calendar link to
// Sam" to expand, and it deliberately does not. Saving sends the whole set;
// the engine refuses a bad one with a reason, which is shown as written.

import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../lib/api';
import { Reveal } from '../lib/motion';

type Row = { trigger: string; text: string };

const KEYS: [string, string][] = [
  ['Hold Fn', 'Dictate while held; release to type it'],
  ['Fn + Space', 'Hands-free: talk without holding; press Fn to finish'],
  ['Esc', 'Throw the recording away'],
  ['Fn + V', 'Type your last dictation again'],
  ['Fn + Z', 'Take back what Say just typed (only if you have not typed or clicked since)'],
];

export function DictationKeys() {
  return (
    <Reveal className="card" style={{ padding: '18px 22px' }}>
      <div className="card-t">Keys</div>
      <div style={{ display: 'grid', gridTemplateColumns: 'max-content 1fr', gap: '8px 18px', marginTop: 12 }}>
        {KEYS.map(([key, what]) => (
          <div key={key} style={{ display: 'contents' }}>
            <span className="kbd">{key}</span>
            <span className="card-s" style={{ marginTop: 0 }}>{what}</span>
          </div>
        ))}
      </div>
    </Reveal>
  );
}

export function SnippetsCard() {
  const qc = useQueryClient();
  const cfg = useQuery({ queryKey: ['snippets'], queryFn: api.snippets });
  const [rows, setRows] = useState<Row[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (cfg.data) {
      setRows(Object.entries(cfg.data.snippets).map(([trigger, text]) => ({ trigger, text })));
    }
  }, [cfg.data]);

  const save = useMutation({
    mutationFn: (next: Row[]) =>
      api.setSnippets(
        Object.fromEntries(next.filter((r) => r.trigger.trim() || r.text.trim()).map((r) => [r.trigger, r.text])),
      ),
    onSuccess: (data) => {
      qc.setQueryData(['snippets'], data);
      setError(null);
      setSaved(true);
      setTimeout(() => setSaved(false), 1600);
    },
    onError: (e: Error) => setError(e.message),
  });

  const update = (i: number, patch: Partial<Row>) =>
    setRows((rs) => rs.map((r, k) => (k === i ? { ...r, ...patch } : r)));

  if (cfg.isError) return null;
  const min = cfg.data?.min_trigger_words ?? 2;

  return (
    <Reveal className="card" style={{ padding: 22 }}>
      <div className="card-t">Snippets</div>
      <div className="card-s" style={{ marginBottom: 14, maxWidth: '60ch' }}>
        Say the trigger as the whole dictation and Say types the text instead, exactly as written.
        The same words inside a longer sentence never expand. Triggers need at least {min} words.
      </div>
      {rows.map((row, i) => (
        <div key={i} className="snip">
          <input
            value={row.trigger}
            placeholder="insert my calendar link"
            aria-label="Trigger"
            onChange={(e) => update(i, { trigger: e.target.value })}
          />
          <textarea
            value={row.text}
            placeholder="https://…"
            aria-label="Text to type"
            onChange={(e) => update(i, { text: e.target.value })}
          />
          <button className="ghost" onClick={() => setRows((rs) => rs.filter((_, k) => k !== i))}>
            Remove
          </button>
        </div>
      ))}
      <div className="tedit-bar">
        <button className="ghost" onClick={() => setRows((rs) => [...rs, { trigger: '', text: '' }])}>
          Add a snippet
        </button>
        <button className="ghost solid" onClick={() => save.mutate(rows)} disabled={save.isPending}>
          {saved ? '✓ Saved' : 'Save snippets'}
        </button>
        {error ? <span className="err">{error}</span> : null}
      </div>
    </Reveal>
  );
}
