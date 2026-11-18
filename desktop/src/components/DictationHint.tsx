import { usePlatform } from '../lib/platform';

export function DictationHint() {
  const platform = usePlatform();
  if (platform.platform === 'linux' && !platform.automatic_insertion) {
    return <>Record above, click Copy text, and paste into the app you are working in.</>;
  }
  return <>Hold <span className="kbd">{platform.hotkey_label ?? 'Fn'}</span> and speak.
    Release, and the text appears in whatever app you are working in.</>;
}
