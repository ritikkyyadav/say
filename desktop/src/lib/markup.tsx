// Insight copy carries the lightest possible markup so the phrasing can live
// in one place (say/vocabulary.py) instead of being reassembled here from a
// pile of numbers. `*word*` is the emphasis the design sets in italic accent;
// `**text**` is bold. Nothing else is supported, and nothing is interpreted as
// HTML — this renders React elements, never `dangerouslySetInnerHTML`.

import { Fragment, ReactNode } from 'react';

const TOKEN = /(\*\*[^*]+\*\*|\*[^*]+\*)/g;

export function markup(text: string): ReactNode {
  return (text || '')
    .split(TOKEN)
    .filter((part) => part !== '')
    .map((part, i) => {
      if (part.length > 4 && part.startsWith('**') && part.endsWith('**')) {
        return <b key={i}>{part.slice(2, -2)}</b>;
      }
      if (part.length > 2 && part.startsWith('*') && part.endsWith('*')) {
        return <em key={i}>{part.slice(1, -1)}</em>;
      }
      return <Fragment key={i}>{part}</Fragment>;
    });
}
