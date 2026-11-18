import type { PlatformInfo } from './api';

export function fallbackPlatform(agent: string): PlatformInfo {
  const linux = /Linux/i.test(agent);
  const mac = /Mac/i.test(agent);
  return {
    platform: linux ? 'linux' : mac ? 'macos' : /Windows/i.test(agent) ? 'windows' : 'other',
    session: linux ? 'headless' : 'native',
    automatic_insertion: false,
    global_hotkey: false,
    hotkey_label: mac ? 'Fn' : null,
    overlay: mac,
    manual_paste: linux,
    supported_backends: [],
  };
}
