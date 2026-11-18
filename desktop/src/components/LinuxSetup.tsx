import { useEffect, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../lib/api';
import type { PlatformInfo } from '../lib/api';
import { Mark } from './Mark';

export function LinuxSetup({ platform, onDone }: { platform: PlatformInfo; onDone: () => void }) {
  const qc = useQueryClient();
  const profile = useQuery({ queryKey: ['profile'], queryFn: api.profile });
  const [name, setName] = useState('');
  const seeded = useRef(false);
  useEffect(() => {
    if (!seeded.current && profile.data) {
      seeded.current = true;
      setName(profile.data.first_name);
    }
  }, [profile.data]);
  const finish = useMutation({
    mutationFn: async () => {
      const next = await api.setProfile({ first_name: name.trim(), onboarded: true });
      if (!next.onboarded) throw new Error('The settings could not be saved. Check your data directory.');
      return next;
    },
    onSuccess: (next) => { qc.setQueryData(['profile'], next); onDone(); },
  });
  const manual = !platform.automatic_insertion;

  return (
    <div className="ob linux-setup">
      <form className="ob-card" onSubmit={(event) => { event.preventDefault(); finish.mutate(); }}>
        <div className="ob-body">
          <div className="ob-mark"><Mark burst={1} /></div>
          <span className="linux-eyebrow">Linux · {platform.session === 'headless' ? 'local dictation' : manual ? 'manual paste' : 'X11'}</span>
          <h1>Your voice, on your desktop.</h1>
          <p>Speech recognition runs on this computer. The English model is built in and ready offline;
            other models download only when you ask. There is no account or API key.</p>
          <div className="linux-setup-notes">
            <section><h3>Use your default microphone</h3>
              <p>Choose the input in your desktop’s Sound settings. Say opens it when you record,
                not while it is idle. Cancel discards the recording.</p></section>
            <section><h3>{manual ? 'Record, copy, paste' : 'A shortcut, or a copy'}</h3>
              <p>{manual
                ? 'Use the Record and Stop buttons. Then click Copy text and paste into your app. This is the supported recording path on Wayland; global hotkeys and automatic insertion stay disabled there.'
                : `Hold ${platform.hotkey_label ?? 'Ctrl+Space'} while another app is focused; release to insert. Tap for hands-free. The in-app recorder keeps text for manual copy.`}</p></section>
            <section><h3>Your clipboard stays yours</h3>
              <p>{manual
                ? 'Nothing is copied until you click Copy. The transcript is also kept in local history.'
                : 'Automatic insertion briefly uses the clipboard and restores its content. Clipboard managers may retain the dictated text. History is there if insertion is refused.'}</p></section>
          </div>
          <label className="linux-setup-name" htmlFor="linux-name">What should we call you? <span>Optional, and kept locally.</span></label>
          <input id="linux-name" className="ob-input" value={name} maxLength={48} autoComplete="off"
            placeholder="Your name" onChange={(event) => setName(event.target.value)} />
          {finish.error && <p role="alert" className="linux-recorder-error">{finish.error.message}</p>}
        </div>
        <div className="ob-foot"><span className="card-s">You can change your name and preferences later.</span>
          <button className="btn acc" data-testid="linux-setup-finish" type="submit" disabled={finish.isPending}>
            {finish.isPending ? 'Saving…' : 'Open Say'}
          </button>
        </div>
      </form>
    </div>
  );
}
