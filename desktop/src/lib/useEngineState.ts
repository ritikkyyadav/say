import { useEffect, useState } from 'react';
import { DictationStateName, subscribeEvents } from './api';

export function useEngineState(): DictationStateName {
  const [state, setState] = useState<DictationStateName>('idle');
  useEffect(() => {
    const cleanup = subscribeEvents(
      (e) => setState(e.state ?? 'idle'),
      () => setState('idle'),
    );
    return cleanup;
  }, []);
  return state;
}
