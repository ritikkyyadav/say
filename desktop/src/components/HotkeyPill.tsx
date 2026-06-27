import { useQuery } from '@tanstack/react-query';
import { api } from '../lib/api';

/**
 * Compact live status pill. Sits in the header so the user always sees
 * which hotkey is active or whether dictation is gated by permissions.
 */
export function HotkeyPill() {
  const { data: listener } = useQuery({
    queryKey: ['listener'],
    queryFn: api.listener,
    refetchInterval: 4_000,
    staleTime: 0,
  });

  if (!listener) {
    return (
      <span className="pill-mute">
        <span className="w-1.5 h-1.5 rounded-full bg-cloud-mid" />
        Connecting…
      </span>
    );
  }

  if (!listener.started) {
    return (
      <span className="pill bg-crail/10 text-crail-deep border border-crail/25">
        <span className="w-1.5 h-1.5 rounded-full bg-crail" />
        Hotkey unavailable
      </span>
    );
  }

  if (listener.kind === 'fn' && !listener.fell_back) {
    return (
      <span className="pill bg-paper text-ink border border-cloud-light">
        <span className="w-1.5 h-1.5 rounded-full bg-olive" />
        Fn ready
      </span>
    );
  }

  // legacy / fallback
  return (
    <span className="pill bg-crail/10 text-crail-deep border border-crail/25">
      <span className="w-1.5 h-1.5 rounded-full bg-crail" />
      {listener.label ?? 'Ctrl+Space'} · Fn disabled
    </span>
  );
}
