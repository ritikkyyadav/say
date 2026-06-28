import { useEffect } from 'react';
import { Route, Routes, useNavigate } from 'react-router-dom';
import { Sidebar } from './components/Sidebar';
import { RecordingDot } from './components/RecordingDot';
import { HotkeyPill } from './components/HotkeyPill';
import { Home } from './routes/Home';
import { Insights } from './routes/Insights';
import { Models } from './routes/Models';
import { Recordings } from './routes/Recordings';

export default function App() {
  const navigate = useNavigate();

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === '1') {
        e.preventDefault();
        navigate('/');
      } else if ((e.metaKey || e.ctrlKey) && e.key === '2') {
        e.preventDefault();
        navigate('/insights');
      } else if ((e.metaKey || e.ctrlKey) && e.key === '3') {
        e.preventDefault();
        navigate('/models');
      } else if ((e.metaKey || e.ctrlKey) && e.key === '4') {
        e.preventDefault();
        navigate('/recordings');
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [navigate]);

  return (
    <div className="flex h-full">
      <Sidebar />
      <main className="flex-1 min-w-0 overflow-y-auto">
        <header className="sticky top-0 bg-book-cloth z-10 border-b border-cloud-light px-10 py-4 flex items-center justify-between">
          <div className="flex items-center gap-2.5 text-[12px] text-cloud-dark">
            <span className="eyebrow">On-device</span>
            <span className="text-cloud-mid">·</span>
            <span>Audio never leaves this Mac</span>
          </div>
          <div className="flex items-center gap-3">
            <HotkeyPill />
            <RecordingDot />
          </div>
        </header>
        <div className="px-10 py-12 max-w-container">
          <Routes>
            <Route path="/" element={<Home />} />
            <Route path="/insights" element={<Insights />} />
            <Route path="/models" element={<Models />} />
            <Route path="/recordings" element={<Recordings />} />
            <Route path="*" element={<Home />} />
          </Routes>
        </div>
      </main>
    </div>
  );
}
