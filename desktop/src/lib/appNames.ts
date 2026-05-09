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
};

export function appLabel(bundleOrName: string | null | undefined): string {
  if (!bundleOrName) return 'Unknown';
  if (MAP[bundleOrName]) return MAP[bundleOrName];
  // If it's a reverse-DNS bundle id, take the last component capitalised.
  if (bundleOrName.includes('.')) {
    const tail = bundleOrName.split('.').pop() || bundleOrName;
    return tail.charAt(0).toUpperCase() + tail.slice(1);
  }
  return bundleOrName;
}
