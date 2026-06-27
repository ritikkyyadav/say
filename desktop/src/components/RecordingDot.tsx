import { useEngineState } from '../lib/useEngineState';

export function RecordingDot() {
  const state = useEngineState();
  const isRecording = state === 'recording';
  if (!isRecording) return null;
  return (
    <div className="flex items-center gap-2 px-2.5 py-1 rounded-full bg-crail/10 text-crail-deep border border-crail/20">
      <span className="w-2 h-2 rounded-full bg-crail recording-dot" />
      <span className="text-[12px] font-medium">Recording</span>
    </div>
  );
}
