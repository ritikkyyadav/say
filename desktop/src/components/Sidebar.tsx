import { NavLink } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { api } from '../lib/api';

const NAV: { to: string; label: string; disabled?: boolean }[] = [
  { to: '/', label: 'Home' },
  { to: '/insights', label: 'Insights' },
  { to: '/models', label: 'Models' },
  { to: '/recordings', label: 'Recordings' },
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
    <aside className="w-[244px] shrink-0 h-full flex flex-col px-3 py-5 border-r border-cloud-light bg-paper">
      <div className="px-3 mb-9 flex items-center gap-3">
        <img
          src="/logo.png"
          alt="Oceanus"
          className="w-9 h-9 rounded-md border border-cloud-light"
          draggable={false}
        />
        <div className="leading-none">
          <div className="font-serif text-[22px] tracking-tight text-ink">Oceanus</div>
          <div className="text-[11px] text-cloud-dark mt-1">Local · on-device</div>
        </div>
      </div>

      <nav className="flex flex-col gap-0.5">
        {NAV.map((item) =>
          item.disabled ? (
            <div key={item.label} className="sidebar-item-disabled">
              <span>{item.label}</span>
              <span className="ml-auto eyebrow text-[9px]">soon</span>
            </div>
          ) : (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === '/'}
              className={({ isActive }) =>
                isActive
                  ? 'sidebar-item-active relative pl-4 before:absolute before:left-0 before:top-1.5 before:bottom-1.5 before:w-[3px] before:rounded-full before:bg-crail before:content-[""]'
                  : 'sidebar-item'
              }
            >
              {item.label}
            </NavLink>
          ),
        )}
      </nav>

      <div className="mt-auto px-3 pt-5 border-t border-cloud-light">
        <div className="eyebrow mb-2">Local-only</div>
        <div className="text-[14px] text-ink leading-snug">
          {summary?.transcript_count ?? '—'} transcripts on this Mac
        </div>
        <div className="text-[12px] text-cloud-dark mt-1.5 leading-relaxed">
          Nothing ever leaves this Mac.
        </div>
      </div>
    </aside>
  );
}
