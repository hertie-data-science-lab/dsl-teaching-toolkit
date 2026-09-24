// The console's Content-Security-Policy (decision 0002): scripts only from the console's own
// origin, calls only to GitHub and the sign-in relay, images from GitHub's avatars, fonts
// from Google Fonts, no frames. `vite.config.ts` fills the relay's origin into index.html at
// build time; the dev server drops the meta, since its live reload needs a websocket.
// No 'unsafe-eval': the schema validators are compiled at build time (`virtual:validators`).

export const CSP_PLACEHOLDER = '%CONSOLE_CSP%';

/** The relay's https origin for connect-src; '' when the build has none, it does not parse, or it is not https. */
export function relayOrigin(url: string | undefined): string {
  if (!url) return '';
  try {
    const u = new URL(url);
    return u.protocol === 'https:' ? u.origin : '';
  } catch {
    return '';
  }
}

export function cspPolicy(relayUrl?: string): string {
  const relay = relayOrigin(relayUrl);
  return [
    "default-src 'self'",
    `connect-src 'self' https://api.github.com https://github.com${relay ? ` ${relay}` : ''}`,
    "img-src 'self' https://avatars.githubusercontent.com https://github.com data:",
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
    'font-src https://fonts.gstatic.com',
    "frame-src 'none'",
    "base-uri 'self'",
  ].join('; ');
}

/** index.html with its CSP meta filled in (a build) or taken out (the dev server). */
export function withCsp(html: string, build: boolean, relayUrl?: string): string {
  if (!build) return html.replace(/[ \t]*<meta http-equiv="Content-Security-Policy"[^>]*>\n?/, '');
  return html.replace(CSP_PLACEHOLDER, cspPolicy(relayUrl));
}
