// Shown only when the hotkey is not fully working. It hides itself the moment
// the Fn listener is live, which is the one piece of permission state that is
// worth interrupting someone for — everything else the app can just get on
// with.

import { useQuery } from '@tanstack/react-query';
import { api, openPrivacySettings, PrivacyPane } from '../lib/api';

const PANES: { pane: PrivacyPane; label: string; why: string }[] = [
  { pane: 'microphone', label: 'Microphone', why: 'so Say can hear you' },
  { pane: 'accessibility', label: 'Accessibility', why: 'so it can type for you, and watch the Fn key' },
  { pane: 'input-monitoring', label: 'Input Monitoring', why: 'so the key press reaches Say at all' },
];

export function PermissionsBanner() {
  const { data: listener } = useQuery({
    queryKey: ['listener'],
    queryFn: api.listener,
    refetchInterval: 4_000,
    staleTime: 0,
  });

  if (!listener) return null;
  // Fn is live and nothing fell back — there is nothing to tell anyone.
  if (listener.kind === 'fn' && !listener.fell_back) return null;

  const headline = !listener.started
    ? 'The hotkey could not start'
    : 'Dictation works, but the Fn key is off';

  const detail = !listener.started
    ? 'Neither Fn nor the Ctrl+Space fallback registered. Grant these three, then reopen Say.'
    : 'Say is listening on Ctrl+Space because the engine has not been granted Accessibility. Grant it and Input Monitoring to switch back to Fn — no relaunch needed.';

  return (
    <div style={{ marginBottom: 20 }}>
      <div
        className="card"
        style={{ padding: '18px 22px', borderLeft: '3px solid var(--accent)', marginBottom: 11 }}
      >
        <div className="card-t">{headline}</div>
        <div className="card-s" style={{ maxWidth: '64ch' }}>
          {detail}
        </div>
      </div>
      <div className="banner">
        {PANES.map(({ pane, label, why }) => (
          <div className="pane" key={pane}>
            <h4>{label}</h4>
            <p>{why}</p>
            <button
              className="btn pri"
              style={{ width: 'auto', padding: '6px 12px', fontSize: 12 }}
              onClick={() => openPrivacySettings(pane)}
            >
              Open Settings
            </button>
          </div>
        ))}
      </div>
      <div className="card-s" style={{ marginTop: 10, maxWidth: '72ch' }}>
        Grant these to <b style={{ color: 'var(--ink)' }}>Say</b> — the engine, not the window.
        If it is not listed, click <span className="mono">+</span>, press{' '}
        <span className="mono">⌘⇧G</span> and add{' '}
        <span className="mono">/Applications/Say.app/Contents/Resources/SayEngine.app</span>.
      </div>
    </div>
  );
}
