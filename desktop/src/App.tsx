import { useEffect, useState } from 'react';
import { useNavigate, useRouter } from './lib/router';
import { useQuery } from '@tanstack/react-query';
import { api } from './lib/api';
import { Rail } from './components/Rail';
import { Home } from './routes/Home';
import { Models } from './routes/Models';
import { Overlay } from './routes/Overlay';
import { Onboarding } from './routes/Onboarding';

// The header used to carry "On-device · Audio never leaves this Mac" and a
// pair of status pills. It says nothing now on purpose: a badge that repeats
// the same reassurance on every screen stops being information and starts
// being decoration, and the hotkey state is visible in the mark itself.
function TopBar({ crumb }: { crumb: string }) {
  return (
    <div className="topbar">
      <div className="wordmark">
        <b>Say</b>
        <span>{crumb}</span>
      </div>
    </div>
  );
}

const PAGES: Record<string, { crumb: string; render: () => JSX.Element }> = {
  '/': { crumb: 'Home', render: () => <Home /> },
  '/overlay': { crumb: 'Overlay', render: () => <Overlay /> },
  '/models': { crumb: 'Models', render: () => <Models /> },
};

export default function App() {
  const navigate = useNavigate();
  const { path } = useRouter();
  const [dismissed, setDismissed] = useState(false);
  // If the engine cannot be reached we show the app, not the onboarding: a
  // returning user whose engine is briefly down must never be walked through
  // setup again.
  const profile = useQuery({ queryKey: ['profile'], queryFn: api.profile });
  const needsOnboarding = profile.data ? !profile.data.onboarded && !dismissed : false;

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!(e.metaKey || e.ctrlKey)) return;
      const target = { '1': '/', '2': '/overlay', '3': '/models' }[e.key];
      if (target) {
        e.preventDefault();
        navigate(target);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [navigate]);

  if (needsOnboarding) {
    return <Onboarding onDone={() => setDismissed(true)} />;
  }

  const page = PAGES[path] ?? PAGES['/'];

  return (
    <div className="shell">
      <Rail />
      <main className="main">
        <TopBar crumb={page.crumb} />
        {page.render()}
      </main>
    </div>
  );
}
