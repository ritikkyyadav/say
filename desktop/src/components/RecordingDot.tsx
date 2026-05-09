import { useEngineState } from '../lib/useEngineState';

export function RecordingDot() {
  const state = useEngineState();
  const isRecording = state === 'recording';
  if (!isRecording) return null;
  return (
    <div className="flex items-center gap-2 px-2 py-1 rounded-full bg-red-50 text-red-700">
      <span className="w-2 h-2 rounded-full bg-red-500 recording-dot" />
      <span className="text-xs font-medium">Recording</span>
    </div>
  );
}
