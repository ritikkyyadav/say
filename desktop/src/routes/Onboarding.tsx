// First run.
//
// The whole flow is local. There is no account, no email field, no "create
// your profile" that quietly means "create a row in our database" — the one
// email this product asks for is collected on the website at download time
// and never reaches the binary. Everything typed here goes into a settings
// file on this machine and is read by nothing else.
//
// It exists for one reason: someone who lands on Home with no name, no
// permissions and no transcripts sees an empty dashboard and has no idea
// whether the app works. Four steps fix all three.

import { useEffect, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, openPrivacySettings, PrivacyPane } from '../lib/api';
import { useEngineState } from '../lib/useEngineState';
import { GlyphPreview } from '../components/GlyphPreview';
import { Pattern } from '../lib/glyphPattern';
import { Mark } from '../components/Mark';

const STEPS = ['Welcome', 'Name', 'Permissions', 'Your mark', 'Try it'] as const;

const PANES: { pane: PrivacyPane; label: string; why: string }[] = [
  { pane: 'microphone', label: 'Microphone', why: 'so Say can hear you' },
  { pane: 'accessibility', label: 'Accessibility', why: 'so it can type for you, and watch the Fn key' },
  { pane: 'input-monitoring', label: 'Input Monitoring', why: 'so the key press reaches Say at all' },
];

export function Onboarding({ onDone }: { onDone: () => void }) {
  const qc = useQueryClient();
  const [step, setStep] = useState(0);
  const [name, setName] = useState('');

  const profile = useQuery({ queryKey: ['profile'], queryFn: api.profile });
  const listener = useQuery({
    queryKey: ['listener'],
    queryFn: api.listener,
    refetchInterval: step === 2 ? 2_000 : false,
    staleTime: 0,
  });
  const overlay = useQuery({ queryKey: ['overlay'], queryFn: api.overlay });

  // Step 5 watches for the first real dictation, so the flow ends with proof
  // the thing works rather than a claim that it does.
  const engineState = useEngineState();
  const transcripts = useQuery({
    queryKey: ['transcripts'],
    queryFn: () => api.transcripts(5),
    refetchInterval: step === 4 ? 1_500 : false,
  });
  const [baseline, setBaseline] = useState<number | null>(null);
  useEffect(() => {
    if (step === 4 && baseline === null && transcripts.data) {
      setBaseline(transcripts.data.length ? transcripts.data[0].id : 0);
    }
  }, [step, baseline, transcripts.data]);
  const heard =
    baseline !== null && !!transcripts.data?.length && transcripts.data[0].id > baseline;

  // Seed the field from the profile exactly once, and never again.
  //
  // The first version listed `name` in its own dependencies and re-seeded
  // whenever the box was empty — so deleting the last character immediately
  // put the name back and the field could not be cleared at all. An effect
  // that both reads and writes the state it depends on will fight the user
  // for it, and the user always loses.
  const seeded = useRef(false);
  useEffect(() => {
    if (!seeded.current && profile.data) {
      seeded.current = true;
      setName(profile.data.first_name ?? '');
    }
  }, [profile.data]);

  const saveName = useMutation({
    mutationFn: (value: string) => api.setProfile({ first_name: value }),
    onSuccess: (p) => qc.setQueryData(['profile'], p),
  });
  const setTheme = useMutation({
    mutationFn: (patch: { theme?: string; pattern?: string }) => api.setOverlay(patch),
    onSuccess: (cfg) => qc.setQueryData(['overlay'], cfg),
  });
  const finish = useMutation({
    mutationFn: () => api.setProfile({ onboarded: true }),
    onSuccess: (p) => {
      qc.setQueryData(['profile'], p);
      qc.invalidateQueries();
      onDone();
    },
  });

  const fnReady = listener.data?.kind === 'fn' && !listener.data.fell_back;
  const next = () => setStep((s) => Math.min(STEPS.length - 1, s + 1));
  const back = () => setStep((s) => Math.max(0, s - 1));

  const ob = overlay.data;
  const preset = ob?.presets.find((p) => p.id === ob.theme);
  const shownColors = ob?.theme === 'custom' ? ob.colors : (preset?.colors ?? []);

  return (
    <div className="ob">
      <div className="ob-card">
        <div className="ob-rail">
          {STEPS.map((label, i) => (
            <div key={label} className={`ob-dot${i === step ? ' on' : i < step ? ' done' : ''}`}>
              <i />
              <span>{label}</span>
            </div>
          ))}
        </div>

        <div className="ob-body">
          {step === 0 && (
            <>
              <div className="ob-mark">
                <Mark burst={1} />
              </div>
              <h1>Hold a key. Talk. It types.</h1>
              <p>
                Say listens on this Mac and writes into whatever app you are already in.
                There is no account and nothing to sign in to — the app never makes a network
                call, and everything it learns about how you speak stays in a file on this
                machine.
              </p>
              <p className="ob-sub">Four short steps and you are done.</p>
            </>
          )}

          {step === 1 && (
            <>
              <h1>What should we call you?</h1>
              <p>
                Only used to say hello on the home screen. It is written to a settings file
                here and read by nothing else.
              </p>
              <input
                className="ob-input"
                value={name}
                maxLength={48}
                autoFocus
                placeholder="Your name"
                onChange={(e) => setName(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    saveName.mutate(name.trim());
                    next();
                  }
                }}
              />
            </>
          )}

          {step === 2 && (
            <>
              <h1>Three permissions.</h1>
              <p>
                macOS will not let any app hear you or type for you without these. Grant them
                to <b>Say</b> — the engine, not this window.
              </p>
              <div className="ob-perms">
                {PANES.map(({ pane, label, why }) => (
                  <div className="ob-perm" key={pane}>
                    <div>
                      <h4>{label}</h4>
                      <p>{why}</p>
                    </div>
                    <button className="btn pri" onClick={() => openPrivacySettings(pane)}>
                      Open Settings
                    </button>
                  </div>
                ))}
              </div>
              <div className={`ob-status${fnReady ? ' ok' : ''}`}>
                <i />
                {fnReady
                  ? 'The Fn key is live. Nothing else to do here.'
                  : 'Waiting for the Fn key — this updates on its own, no relaunch needed.'}
              </div>
            </>
          )}

          {step === 3 && (
            <>
              <h1>Pick your mark.</h1>
              <p>
                This is what appears while you speak. It never takes focus and you can change
                it later.
              </p>
              <div className="ob-glyph">
                <GlyphPreview
                  colors={shownColors}
                  adaptive={!shownColors.length}
                  backdrop="light"
                  pattern={(ob?.pattern ?? 'pulse') as Pattern}
                  height={230}
                />
              </div>
              <div className="ob-themes">
                {(ob?.presets ?? []).slice(0, 6).map((p) => (
                  <button
                    key={p.id}
                    className={ob?.theme === p.id ? 'ob-swatch on' : 'ob-swatch'}
                    title={p.name}
                    onClick={() => setTheme.mutate({ theme: p.id })}
                  >
                    {(p.colors.length ? p.colors : ['#17150F']).map((c, i) => (
                      <i key={i} style={{ background: c }} />
                    ))}
                  </button>
                ))}
              </div>
            </>
          )}

          {step === 4 && (
            <>
              <h1>{heard ? 'That is it.' : 'Try it now.'}</h1>
              {heard ? (
                <p>
                  Say heard you. That transcript is already on your home screen, and the more
                  you dictate the more it can tell you about how you actually speak.
                </p>
              ) : (
                <p>
                  Hold <span className="kbd">Fn</span>, say anything, and let go. The text
                  lands wherever your cursor is — including right here.
                </p>
              )}
              <div className={`ob-listen${heard ? ' heard' : ''}`}>
                <Mark live={engineState === 'recording'} burst={heard ? 2 : 0} />
                <span>
                  {heard
                    ? `"${transcripts.data![0].text.slice(0, 70)}${transcripts.data![0].text.length > 70 ? '…' : ''}"`
                    : engineState === 'recording'
                      ? 'Listening…'
                      : fnReady
                        ? 'Ready when you are.'
                        : 'The Fn key is not live — you can still finish and fix it later.'}
                </span>
              </div>
            </>
          )}
        </div>

        <div className="ob-foot">
          <button className="btn gho" style={{ width: 'auto' }} onClick={() => finish.mutate()}>
            Skip setup
          </button>
          <div style={{ display: 'flex', gap: 8 }}>
            {step > 0 && (
              <button className="btn gho" style={{ width: 'auto' }} onClick={back}>
                Back
              </button>
            )}
            {step < STEPS.length - 1 ? (
              <button
                className="btn acc"
                style={{ width: 'auto' }}
                onClick={() => {
                  // Saved even when empty: a cleared box means "just use my
                  // account name", which the engine already falls back to.
                  // Skipping the save would silently keep the old value and
                  // read as the field refusing to clear.
                  if (step === 1) saveName.mutate(name.trim());
                  next();
                }}
              >
                Continue
              </button>
            ) : (
              <button
                className="btn acc"
                style={{ width: 'auto' }}
                disabled={finish.isPending}
                onClick={() => finish.mutate()}
              >
                {heard ? 'Take me in' : 'Finish'}
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
