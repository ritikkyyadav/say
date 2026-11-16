// Tiny lookup of common macOS bundle ids → human-friendly names. Used by
// the desktop-usage card. Anything not in the map falls back to the bundle
// id, then "Unknown".

const MAP: Record<string, string> = {
  'com.apple.Safari': 'Safari',
  'com.apple.TextEdit': 'TextEdit',
  'com.apple.mail': 'Mail',
  'com.apple.MobileSMS': 'Messages',
  'com.apple.iWork.Pages': 'Pages',
  'com.apple.iWork.Keynote': 'Keynote',
  'com.apple.iWork.Numbers': 'Numbers',
  'com.apple.Notes': 'Notes',
  'com.apple.Terminal': 'Terminal',
  'com.apple.dt.Xcode': 'Xcode',
  'com.microsoft.VSCode': 'VS Code',
  'com.microsoft.Word': 'Word',
  'com.microsoft.Outlook': 'Outlook',
  'com.microsoft.teams2': 'Teams',
  'com.tinyspeck.slackmacgap': 'Slack',
  'com.figma.Desktop': 'Figma',
  'com.linear.linear': 'Linear',
  'com.notion.id': 'Notion',
  'com.spotify.client': 'Spotify',
  'com.google.Chrome': 'Chrome',
  'com.brave.Browser': 'Brave',
  'com.discord.discord': 'Discord',
  'org.mozilla.firefox': 'Firefox',
  'us.zoom.xos': 'Zoom',
  'company.thebrowser.Browser': 'Arc',
  // The apps this is actually used in. Without these the usage card reads
  // "Warp-Stable", "Claudefordesktop" and "Desktop", which is the bundle id
  // showing through rather than a name anybody chose.
  'dev.warp.Warp-Stable': 'Warp',
  'dev.warp.Warp': 'Warp',
  'com.anthropic.claudefordesktop': 'Claude',
  'com.anthropic.claude': 'Claude',
  'com.openai.codex': 'Codex',
  'com.openai.chat': 'ChatGPT',
  'com.google.antigravity-ide': 'Antigravity',
  'com.apple.finder': 'Finder',
  'com.apple.Preview': 'Preview',
  'com.apple.systempreferences': 'System Settings',
  'com.ritikyadav.say.desktop': 'Say',
  'com.ritikyadav.say.dictation': 'Say',
  'md.obsidian': 'Obsidian',
  'com.todesktop.230313mzl4w4u92': 'Cursor',
  'com.jetbrains.intellij': 'IntelliJ',
  'com.googlecode.iterm2': 'iTerm',
  'com.apple.iChat': 'Messages',
  'com.readdle.SparkDesktop': 'Spark',
  'net.whatsapp.WhatsApp': 'WhatsApp',
  'ru.keepcoder.Telegram': 'Telegram',
};

export function appLabel(bundleOrName: string | null | undefined): string {
  if (!bundleOrName) return 'Unknown';
  if (MAP[bundleOrName]) return MAP[bundleOrName];
  // Unknown bundle id: take the last component and make it look like a name
  // rather than an identifier — drop a trailing channel suffix ("-Stable",
  // "-beta"), split camelCase, and capitalise. "dev.foo.Bar-Stable" reads as
  // "Bar", not "Bar-Stable".
  if (bundleOrName.includes('.')) {
    let tail = bundleOrName.split('.').pop() || bundleOrName;
    tail = tail.replace(/[-_](stable|beta|dev|canary|nightly|app|desktop)$/i, '');
    tail = tail.replace(/[-_]+/g, ' ').replace(/([a-z])([A-Z])/g, '$1 $2');
    return tail
      .split(' ')
      .filter(Boolean)
      .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
      .join(' ');
  }
  return bundleOrName;
}
