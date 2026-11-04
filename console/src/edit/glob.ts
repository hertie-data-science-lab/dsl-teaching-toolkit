// `.gitignore` pattern matching, for the previews of `.releaseignore` and course.yml's
// `withhold` (both use gitignore's syntax; the last matching pattern wins, `!` re-includes).
// The preview reads the repo's root file only; nested `.releaseignore` files still apply
// when the engine copies.
//
// Decision B (2026-11-04): `compileAll(lines, { defaults: true })` reads a `.releaseignore` as the
// engine does (`selection.deny`), the institution's content names (`solutions`, `tests`) as
// implied first lines: matched case aside, at any depth, so a `!` line that matches one later
// re-includes it, the last match winning.

import { DENYLIST_CONTENT } from '../model/selection';

export interface Rule {
  re: RegExp;
  neg: boolean;
  dirOnly: boolean;
  /** The pattern line as written, trimmed (the content name, for a default rule). */
  line: string;
  /** One of the institution's content names, implied before the list's own lines. */
  default?: true;
}

export interface CompileOpts {
  /** The institution's content names first, as `.releaseignore` reads them on a copy. */
  defaults?: boolean;
}

function toRegex(glob: string): string {
  let out = '';
  for (let i = 0; i < glob.length; i++) {
    const c = glob[i];
    if (c === '*' && glob[i + 1] === '*') {
      const slashAfter = glob[i + 2] === '/';
      out += slashAfter ? '(?:.*/)?' : '.*';
      i += slashAfter ? 2 : 1;
    } else if (c === '\\' && i + 1 < glob.length) {
      // An escaped character is itself (`\[` is a literal bracket).
      out += glob[++i].replace(/[.*+?^${}()|[\]\\/]/g, '\\$&');
    } else if (c === '*') out += '[^/]*';
    else if (c === '?') out += '[^/]';
    else if (c === '[' && glob.indexOf(']', i + 2) > i) {
      // A character class, `!` or `^` negating it, as gitignore reads one.
      const end = glob.indexOf(']', i + 2);
      let body = glob.slice(i + 1, end);
      const neg = body[0] === '!' || body[0] === '^';
      if (neg) body = body.slice(1);
      out += `[${neg ? '^' : ''}${body.replace(/\\/g, '\\\\')}]`;
      i = end;
    } else out += c.replace(/[.+^${}()|[\]\\]/g, '\\$&');
  }
  return out;
}

export function compile(line: string): Rule | null {
  let p = line.replace(/\s+$/, '');
  if (!p || p.startsWith('#')) return null;
  const neg = p.startsWith('!');
  if (neg) p = p.slice(1);
  const dirOnly = p.endsWith('/');
  p = p.replace(/\/+$/, '');
  const anchored = p.includes('/');
  p = p.replace(/^\//, '');
  const body = toRegex(p);
  return { re: new RegExp(anchored ? `^${body}$` : `^(?:.*/)?${body}$`), neg, dirOnly, line: line.trim() };
}

function hits(r: Rule, path: string, isDir: boolean): boolean {
  const parts = path.split('/');
  for (let i = 1; i <= parts.length; i++) {
    const sub = parts.slice(0, i).join('/');
    const subIsDir = i < parts.length || isDir;
    if (r.dirOnly && !subIsDir) continue;
    if (r.re.test(sub)) return true;
  }
  return false;
}

/** The content names as rules: any depth, case aside (`selection.is_content_denied`). */
const contentRules = (): Rule[] => DENYLIST_CONTENT.map((name) => {
  const r = compile(name)!;
  return { ...r, re: new RegExp(r.re.source, 'i'), default: true as const };
});

/** The pattern list's rules, compiled once for matching many paths; with `defaults`, the content names first. */
export function compileAll(lines: string[], o: CompileOpts = {}): Rule[] {
  const own = lines.map(compile).filter((r): r is Rule => r !== null);
  return o.defaults ? [...contentRules(), ...own] : own;
}

/** Whether `rules` were compiled with the institution's content names. */
export const carriesDefaults = (rules: Rule[]) => rules.some((r) => r.default);

/** Whether `path` matches the rules, gitignore style: the last matching rule decides. */
export function matchRules(rules: Rule[], path: string, isDir = false): boolean {
  let hit = false;
  for (const r of rules) if (hits(r, path, isDir)) hit = !r.neg;
  return hit;
}

/** Whether `path` matches the pattern list, gitignore style: the last matching line decides. */
export function matches(lines: string[], path: string, isDir = false): boolean {
  return matchRules(compileAll(lines), path, isDir);
}

/** The rule that decides `path` itself (not its folders), gitignore style: the last one matching it, or null; `own` leaves the default rules out. */
export function deciding(rules: Rule[], path: string, isDir: boolean, own = false): Rule | null {
  let hit: Rule | null = null;
  for (const r of rules) if ((!r.dirOnly || isDir) && !(own && r.default) && r.re.test(path)) hit = r;
  return hit;
}

/**
 * Git's own rule, as the engine applies it (`releaseignore.Ignore.excludes`): each folder on the
 * way down is judged first, and a withheld folder is never walked, so nothing inside it can be
 * re-included. The rule that withholds `path` and where it matched (`path` itself or a folder
 * above it), or null. `below`: a folder copy of that folder, which judges the content names only
 * below it (`selection.deny`); the list's own lines still apply at every level.
 */
export function withheldBy(rules: Rule[], path: string, isDir = false, below?: string): { rule: Rule; at: string } | null {
  const parts = path.split('/').filter(Boolean);
  for (let i = 1; i <= parts.length; i++) {
    const at = parts.slice(0, i).join('/');
    const r = deciding(rules, at, i < parts.length || isDir, below !== undefined && at.length <= below.length);
    if (r && !r.neg) return { rule: r, at };
  }
  return null;
}
