import { NavLink } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { api } from '../lib/api';

const NAV: { to: string; label: string; disabled?: boolean }[] = [
  { to: '/', label: 'Home' },
  { to: '/insights', label: 'Insights' },
  { to: '#', label: 'Dictionary', disabled: true },
  { to: '#', label: 'Snippets', disabled: true },
  { to: '#', label: 'Style', disabled: true },
  { to: '#', label: 'Transforms', disabled: true },
  { to: '#', label: 'Scratchpad', disabled: true },
];

export function Sidebar() {
  const { data: summary } = useQuery({
    queryKey: ['summary'],
    queryFn: api.summary,
  });

  return (
    <aside className="w-[240px] shrink-0 h-full flex flex-col px-3 py-4 border-r border-hairline bg-surface/70">
      <div className="px-3 mb-6 flex items-center gap-2.5">
        <img
          src="/logo.png"
          alt="Say"
          className="w-9 h-9 rounded-[10px] shadow-ring"
          draggable={false}
        />
        <div className="leading-tight">
          <div className="font-semibold text-ink text-[15px] tracking-tight">Say</div>
          <div className="text-[11px] text-ink-mute">Local · on-device</div>
        </div>
      </div>

      <nav className="flex flex-col gap-1">
        {NAV.map((item) =>
          item.disabled ? (
            <div key={item.label} className="sidebar-item-disabled">
              <span className="text-sm">{item.label}</span>
              <span className="ml-auto text-[10px] uppercase tracking-wide text-ink-soft">
                soon
              </span>
            </div>
          ) : (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === '/'}
              className={({ isActive }) =>
                isActive ? 'sidebar-item-active' : 'sidebar-item'
              }
            >
              {item.label}
            </NavLink>
          ),
        )}
      </nav>

      <div className="mt-auto px-3 py-4 border-t border-hairline">
        <div className="text-[11px] uppercase tracking-wide text-ink-soft mb-1">
          Local-only
        </div>
        <div className="text-sm text-ink">
          {summary?.transcript_count ?? '—'} transcripts on this Mac
        </div>
        <div className="text-[11px] text-ink-mute mt-1">
          Audio is never written to disk.
        </div>
      </div>
    </aside>
  );
}
