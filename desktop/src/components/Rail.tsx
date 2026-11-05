// The whole navigation: a mark that goes home, and two destinations.
//
// There is deliberately no status text anywhere on it. "Fn ready" and a green
// dot told you the app was working, which you could already tell from the fact
// that it was working; when a recording is actually live the mark itself
// animates, which is the same information without a label.

import { useState } from 'react';
import { NavItem, useNavigate } from '../lib/router';
import { useEngineEvent } from '../lib/useEngineState';
import { Mark } from './Mark';

function OverlayIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round">
      <path d="M3 13h3l2.5-7 3 14 2.5-9 2 4h5" />
    </svg>
  );
}

function ModelsIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 3l8 4.5v9L12 21l-8-4.5v-9L12 3z" />
      <path d="M12 12l8-4.5M12 12v9M12 12L4 7.5" />
    </svg>
  );
}

export function Rail() {
  const navigate = useNavigate();
  const event = useEngineEvent();
  const recording = event.state === 'recording';
  const [poke, setPoke] = useState(0);
  // The mark carries no label by design; a long wait is the one thing worth
  // a tooltip, and the engine now says how far along it is.
  const progress = event.state === 'processing' && event.progress ? event.progress : null;
  const label = progress ? `Transcribing ${progress.done} of ${progress.total}` : 'Home';

  return (
    <aside className="rail">
      <button
        className="rail-logo"
        onClick={() => {
          setPoke((n) => n + 1);
          navigate('/');
        }}
        onMouseEnter={() => setPoke((n) => n + 1)}
        title={label}
        aria-label={label}
      >
        <Mark live={recording} burst={poke} />
      </button>

      <nav className="rail-nav">
        <NavItem
          to="/overlay"
          className={(active) => (active ? 'nav-btn on' : 'nav-btn')}
          aria-label="Overlay"
        >
          <OverlayIcon />
          <span className="nav-tip">Overlay</span>
        </NavItem>
        <NavItem
          to="/models"
          className={(active) => (active ? 'nav-btn on' : 'nav-btn')}
          aria-label="Models"
        >
          <ModelsIcon />
          <span className="nav-tip">Models</span>
        </NavItem>
      </nav>
    </aside>
  );
}
