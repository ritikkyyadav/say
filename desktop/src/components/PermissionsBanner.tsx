import { useQuery } from '@tanstack/react-query';
import { api, openPrivacySettings, PrivacyPane } from '../lib/api';

const PANES: { pane: PrivacyPane; label: string; why: string }[] = [
  {
    pane: 'microphone',
    label: 'Microphone',
    why: 'so Oceanus can hear you while you dictate',
  },
  {
    pane: 'accessibility',
    label: 'Accessibility',
    why: 'so the Fn-key listener can capture the global hotkey',
  },
  {
    pane: 'input-monitoring',
    label: 'Input Monitoring',
    why: 'so the Ctrl+Space fallback can receive keypresses',
  },
];

export function PermissionsBanner() {
  const { data: listener } = useQuery({
    queryKey: ['listener'],
    queryFn: api.listener,
    refetchInterval: 4_000,   // keep status fresh while user grants permissions
    staleTime: 0,
  });

  // If the Fn listener is active and not falling back, everything is fine —
  // hide the banner entirely.
  if (listener && listener.kind === 'fn' && !listener.fell_back) {
    return null;
  }

  // While the listener state is loading, render nothing rather than a flash.
  if (!listener) return null;

  const state = describe(listener);

  return (
    <div className="card p-6 mb-8 border-l-[3px] border-l-crail">
      <div className="flex items-start gap-4">
        <div
          className={`w-8 h-8 rounded-full flex items-center justify-center font-serif text-[15px] shrink-0 mt-0.5 ${
            state.severity === 'error' ? 'bg-ink text-book-cloth' : 'bg-crail text-book-cloth'
          }`}
        >
          {state.severity === 'error' ? '!' : 'i'}
        </div>
        <div className="flex-1 min-w-0">
          <h3 className="font-serif text-h4 font-normal text-ink mb-1.5">{state.title}</h3>
          <p className="text-cloud-dark text-[14px] leading-relaxed mb-5 max-w-prose">
            {state.body}
          </p>

          <div className="flex flex-col gap-2">
            {PANES.map(({ pane, label, why }) => (
              <div
                key={pane}
                className="flex items-center justify-between gap-3 px-3.5 py-2.5 rounded-md bg-book-cloth border border-cloud-light"
              >
                <div className="min-w-0">
                  <div className="text-[14px] font-medium text-ink">{label}</div>
                  <div className="text-[12px] text-cloud-dark truncate">{why}</div>
                </div>
                <button
                  onClick={() => openPrivacySettings(pane)}
                  className="shrink-0 px-3 py-1.5 rounded-md text-[12px] font-medium bg-ink text-book-cloth hover:bg-[#2a2a28] transition-colors"
                >
                  Open Settings
                </button>
              </div>
            ))}
          </div>

          <p className="mt-5 text-[12px] text-cloud-dark leading-relaxed max-w-prose">
            Grant these to <span className="font-medium text-ink">Oceanus</span> (the engine). If it
            isn't in the list yet, click <span className="font-mono">+</span>, press{' '}
            <span className="font-mono">⌘⇧G</span>, and add{' '}
            <span className="font-mono">
              /Applications/Oceanus.app/Contents/Resources/OceanusEngine.app
            </span>
            . Quit and reopen Oceanus once all three are on — this banner hides itself when Fn
            becomes active.
          </p>
        </div>
      </div>
    </div>
  );
}

type Severity = 'warn' | 'error';

function describe(listener: {
  kind: 'fn' | 'legacy' | null;
  fell_back: boolean;
  fn_error: string | null;
  started: boolean;
  accessibility_trusted: boolean;
}): { title: string; body: string; severity: Severity } {
  if (!listener.started) {
    return {
      title: 'Hotkey listener could not start',
      body:
        'Neither the Fn key nor the Ctrl+Space fallback registered. Grant the three permissions below and relaunch Oceanus.',
      severity: 'error',
    };
  }
  if (listener.kind === 'legacy' && listener.fell_back) {
    return {
      title: 'Dictation works, but Fn key is disabled',
      body:
        'Oceanus is currently listening on Ctrl+Space because Accessibility isn\'t granted to the engine. Grant Accessibility AND Input Monitoring below to switch to Fn — and to make Ctrl+Space actually receive events (pynput needs Input Monitoring too).',
      severity: 'warn',
    };
  }
  return {
    title: 'Permissions check',
    body: 'Make sure all three permissions are granted to Oceanus.',
    severity: 'warn',
  };
}
