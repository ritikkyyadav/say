import { useEffect, useState } from 'react';
import { DictationStateEvent, DictationStateName, subscribeEvents } from './api';

const IDLE: DictationStateEvent = { state: 'idle' };

/** The whole latest engine event: state plus, while processing, progress. */
export function useEngineEvent(): DictationStateEvent {
  const [event, setEvent] = useState<DictationStateEvent>(IDLE);
  useEffect(() => {
    const cleanup = subscribeEvents(
      (e) => setEvent(e.state ? e : IDLE),
      () => setEvent(IDLE),
    );
    return cleanup;
  }, []);
  return event;
}

export function useEngineState(): DictationStateName {
  return useEngineEvent().state ?? 'idle';
}
