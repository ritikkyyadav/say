import { useEffect } from 'react';
import { Route, Routes, useNavigate } from 'react-router-dom';
import { Sidebar } from './components/Sidebar';
import { RecordingDot } from './components/RecordingDot';
import { Home } from './routes/Home';
import { Insights } from './routes/Insights';

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
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [navigate]);

  return (
    <div className="flex h-full">
      <Sidebar />
      <main className="flex-1 min-w-0 overflow-y-auto">
        <header className="sticky top-0 bg-canvas/85 backdrop-blur z-10 border-b border-hairline px-10 py-4 flex items-center justify-between">
          <div className="flex items-center gap-2 text-[13px] text-ink-mute">
            <span>Say</span>
            <span className="text-ink-soft">·</span>
            <span>Local dictation</span>
          </div>
          <div className="flex items-center gap-3 text-[12px] text-ink-mute">
            <span className="hidden sm:inline">Hold</span>
            <span className="kbd">Fn</span>
            <span className="hidden sm:inline">to dictate</span>
            <RecordingDot />
          </div>
        </header>
        <div className="px-10 py-8 max-w-5xl">
          <Routes>
            <Route path="/" element={<Home />} />
            <Route path="/insights" element={<Insights />} />
            <Route path="*" element={<Home />} />
          </Routes>
        </div>
      </main>
    </div>
  );
}
