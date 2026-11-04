// What a withhold list does to each file of a repo, and how a click on the tree edits it.
// One widget serves `.releaseignore` (withheld from students), course.yml's `open_course_site.withhold`
// (kept off the public website) and the import picker (decision 0016).
//
// The rule is the engine's (`releaseignore`): gitignore syntax, the last matching line wins,
// and a withheld folder is never walked, so nothing inside it can be re-included. The cases in
// `schemas/materials.json` were answered by the engine and the tests hold this file to them.
// Never-material names (`.DS_Store`, `.gitkeep`) and the list file itself never leave the repo.
//
// Decision B (2026-11-04): with `defaults`, the institution's content names (`solutions`,
// `tests`) are withheld as if the list began with them, so a click on one writes `!path` and a
// second click takes it out again; the never-released names (`.env`, `.git`, grading files) are
// the caller's `fixed` rows, which no line changes.

import { clutter, contentDenied, contentName, denylisted, hardDenied, parts } from '../model/selection';
import { compile, compileAll, matchRules, withheldBy, withheldOnCopy, type Rule } from './glob';

export type Badge = 'withheld' | 'released';

/** Clutter (`selection.CLUTTER`) and the list file itself: never copied anywhere. */
export const neverMaterial = (path: string) => clutter(path) || parts(path).includes('.releaseignore');
/** The institution denylist (`selection.DENYLIST`): solutions, tests, grading files, `.env*`, `.git`, `.system`. */
export { contentDenied, denylisted, hardDenied };

/** Whether a folder copy leaves `path` behind for the content default alone (no `!` line re-includes it). */
export const defaultWithheld = (rules: Rule[], path: string, isDir = false) => {
  const w = withheldOnCopy(rules, path, isDir, contentName);
  return !!w && w.rule === null;
};

export interface Badged {
  badges: Record<string, Badge>;
  /** Pattern lines that match no file or folder, as written. */
  unmatched: string[];
  /** How many rules the list has, comments and blank lines aside. */
  rules: number;
}

/** Each file's badge from one list, and the rules that match nothing. */
export function badgeFiles(files: string[], lines: string[]): Badged {
  const rs = compileAll(lines);
  const badges: Record<string, Badge> = {};
  for (const f of files) badges[f] = neverMaterial(f) || withheldBy(rs, f) ? 'withheld' : 'released';
  const unmatched = rs.filter((r) => !files.some((f) => matchRules([{ ...r, neg: false }], f))).map((r) => r.line);
  return { badges, unmatched, rules: rs.length };
}

/**
 * A repo's files under one withhold list, counted three ways: never copied whatever the list says
 * (`selection.publishable`: denylisted or never-material, unless a release names it), withheld by a line, and released.
 */
export function withholdCounts(files: string[], lines: string[]): { never: number; withheld: number; released: number } {
  const rs = compileAll(lines);
  const n = { never: 0, withheld: 0, released: 0 };
  for (const f of files) n[neverMaterial(f) || denylisted(f) ? 'never' : withheldBy(rs, f) ? 'withheld' : 'released']++;
  return n;
}

/**
 * The Withhold view's summary (decision B): never released (the never-released names, clutter and
 * the list file), withheld by the content default alone, withheld by a line, and released.
 */
export function withholdTally(files: string[], lines: string[]): { never: number; defaulted: number; withheld: number; released: number } {
  const rs = compileAll(lines);
  const n = { never: 0, defaulted: 0, withheld: 0, released: 0 };
  for (const f of files) {
    if (neverMaterial(f) || hardDenied(f)) n.never++;
    else {
      const w = withheldOnCopy(rs, f, false, contentName);
      n[!w ? 'released' : w.rule ? 'withheld' : 'defaulted']++;
    }
  }
  return n;
}

/**
 * The line that names exactly this file or folder: anchored at the root, a folder with its `/`,
 * and every character a pattern reads specially escaped (`[ ] * ? \\`, and a leading `!` or `#`),
 * so `a[b]/notes.pdf` withholds that file and not `ab/notes.pdf`.
 */
export function exactLine(path: string, isDir: boolean): string {
  const escaped = path.replace(/[[\]*?\\]/g, '\\$&').replace(/^[!#]/, '\\$&');
  return `${path.includes('/') ? '' : '/'}${escaped}${isDir ? '/' : ''}`;
}

/** A line's pattern with the anchor dropped, for comparing spellings of one path's line. */
const bare = (line: string) => line.trim().replace(/^\//, '');

/** Whether a list line names exactly this path (`/x`, `x`, and for a folder `x/`). */
const names = (line: string, path: string, isDir: boolean) => bare(line) === bare(exactLine(path, isDir));

/**
 * Where a path stands in a list: its own line, re-included by its own `!` line, withheld by a
 * broader rule, or neither. With `defaults` (decision B), also withheld by the content default
 * (`at` the path or the folder it sits in) or, for a content name a `!` line re-includes, overridden.
 */
export type Standing =
  | { kind: 'own' }
  | { kind: 'included' }
  | { kind: 'broader'; rule: string; at: string; legal: boolean }
  | { kind: 'default'; at: string }
  | { kind: 'overridden' }
  | { kind: 'released' };

/** A standing whose row is unticked: a folder copy leaves it behind. */
export const isOut = (s: Standing) => s.kind === 'own' || s.kind === 'broader' || s.kind === 'default';

export function standing(rs: Rule[], lines: string[], path: string, isDir: boolean, defaults = false): Standing {
  const live = lines.filter((l) => compile(l));
  if (live.some((l) => !l.trim().startsWith('!') && names(l, path, isDir))) return { kind: 'own' };
  const content = defaults && contentName(path.split('/').pop() ?? '');
  if (live.some((l) => l.trim().startsWith('!') && names(l.trim().slice(1), path, isDir))) return { kind: content ? 'overridden' : 'included' };
  const w = defaults ? withheldOnCopy(rs, path, isDir, contentName) : withheldBy(rs, path, isDir);
  if (!w) return { kind: content ? 'overridden' : 'released' };
  if (!w.rule) return { kind: 'default', at: w.at };
  return { kind: 'broader', rule: w.rule.line, at: w.at, legal: w.at === path };
}

/**
 * A click: the list after it, or why it cannot change: the path sits inside a withheld folder
 * (`at` is that folder), or it is a folder a `/**` rule withholds, which also covers everything
 * inside it so no `!` line can release its files (`at` is the folder itself).
 */
export type Toggled = { lines: string[] } | { blocked: { rule: string; at: string; byDefault?: boolean } };

/**
 * Toggle one path: its own line goes (so does its own `!` line); a path nothing withholds gets
 * its exact line; one a broader rule withholds gets `!line` when git allows re-inclusion, and
 * otherwise nothing changes and the caller says which folder rule to remove. With `defaults`
 * (decision B) a click always flips the row: a content name the default withholds gets `!line`
 * (one inside a default-withheld folder is blocked, `byDefault`), a re-included one loses its own
 * `!line` or, re-included by a broader one, gets its exact line.
 */
export function toggle(lines: string[], path: string, isDir: boolean, defaults = false): Toggled {
  const s = standing(compileAll(lines), lines, path, isDir, defaults);
  const line = exactLine(path, isDir);
  const own = (l: string) => !!compile(l) && !l.trim().startsWith('!') && names(l, path, isDir);
  const ownBang = (l: string) => !!compile(l) && l.trim().startsWith('!') && names(l.trim().slice(1), path, isDir);
  const after = (next: string[], out: boolean) => {
    if (!defaults) return { lines: next };
    const t = standing(compileAll(next), next, path, isDir, true);
    if (isOut(t) === out) return { lines: next };
    return { lines: out ? append(next, line) : t.kind === 'default' && t.at === path ? append(next, `!${line}`) : next };
  };
  if (s.kind === 'own') return after(lines.filter((l) => !own(l)), false);
  if (s.kind === 'included' || s.kind === 'overridden') return lines.some(ownBang) ? after(lines.filter((l) => !ownBang(l)), true) : { lines: append(lines, line) };
  if (s.kind === 'released') return { lines: append(lines, line) };
  if (s.kind === 'default') return s.at === path ? { lines: append(lines, `!${line}`) } : { blocked: { rule: '', at: s.at, byDefault: true } };
  if (!s.legal || (isDir && /(^|\/)\*\*$/.test(s.rule.trim()))) return { blocked: { rule: s.rule, at: s.at } };
  return { lines: append(lines, `!${line}`) };
}

/** `lines` with `line` last (the last match wins), in place of a trailing blank line. */
const append = (lines: string[], line: string) => [...(lines.length && !lines[lines.length - 1].trim() ? lines.slice(0, -1) : lines), line];

export interface TreeNode {
  name: string;
  path: string;
  /** Folders only, sorted folders first then by name. */
  children?: TreeNode[];
}

/** File paths as a nested tree of folders and files. */
export function buildTree(files: string[]): TreeNode[] {
  const root: TreeNode = { name: '', path: '', children: [] };
  for (const f of files) {
    const parts = f.split('/');
    let at = root;
    parts.forEach((name, i) => {
      const path = parts.slice(0, i + 1).join('/');
      const leaf = i === parts.length - 1;
      let next = at.children!.find((c) => c.name === name && !c.children === leaf);
      if (!next) {
        next = leaf ? { name, path } : { name, path, children: [] };
        at.children!.push(next);
      }
      at = next;
    });
  }
  const sort = (n: TreeNode[]): TreeNode[] => {
    n.sort((a, b) => Number(!a.children) - Number(!b.children) || a.name.localeCompare(b.name));
    for (const c of n) if (c.children) sort(c.children);
    return n;
  };
  return sort(root.children!);
}
