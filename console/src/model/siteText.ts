// The site's home page as both consoles read it: the instructor's Site tab previews what the
// student console shows.

import { str } from './format';

const IF_SITE = /\{%-?\s*if\s+site\.(\w+)\s*-?%\}([\s\S]*?)\{%-?\s*endif\s*-?%\}/g;
const VAR_SITE = /\{\{-?\s*site\.(\w+)\s*-?\}\}/g;

/**
 * The site's home page (`index.md`, the instructors' own) as plain markdown: `{{ site.x }}`
 * filled from `_config.yml`, an `{% if site.x %}` block kept only when x is set, and any other
 * Liquid tag dropped. Enough for what a home page carries; the site itself is the reference.
 * Both consoles show this one reading: the instructor's preview is what students see.
 */
export function homeText(body: string, cfg: Record<string, unknown>): string {
  const val = (k: string) => str(cfg[k]);
  return body
    .replace(IF_SITE, (_m: string, k: string, inner: string) => (val(k) ? inner : ''))
    .replace(VAR_SITE, (_m: string, k: string) => val(k))
    .replace(/\{%[\s\S]*?%\}|\{\{[\s\S]*?\}\}/g, '')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}

/** The `site.x` keys `body` reads (in `{{ site.x }}` or `{% if site.x %}`) that `cfg` leaves empty, so `homeText` shows nothing for them. */
export function unsetSiteKeys(body: string, cfg: Record<string, unknown>): string[] {
  const keys = new Set<string>();
  for (const re of [IF_SITE, VAR_SITE]) for (const m of body.matchAll(re)) if (!str(cfg[m[1]])) keys.add(m[1]);
  return [...keys];
}
