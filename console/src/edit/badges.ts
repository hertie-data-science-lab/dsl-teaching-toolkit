// What a withhold list does to each file of a repo, and how a click on the tree edits it.
// One widget serves `.releaseignore` (withheld from students), `opencourse.yml`'s `withhold`
// (kept off the public website) and the import picker (decision 0016).
//
// The rule is the engine's (`releaseignore`): gitignore syntax, the last matching line wins,
// and a withheld folder is never walked, so nothing inside it can be re-included. The cases in
// `schemas/materials.json` were answered by the engine and the tests hold this file to them.
// Never-material names (`.DS_Store`, `.gitkeep`) and the list file itself never leave the repo.

import rules from '../../schemas/materials.json';
import { compile, compileAll, matchRules, withheldBy, type Rule } from './glob';

export type Badge = 'withheld' | 'released';

/** `repos.PUBLICATION_DENYLIST` as whole-name regexes, matched per path component. */
const fnmatch = (p: string) => new RegExp(`^${p.replace(/[.+^${}()|[\]\\]/g, '\\$&').replace(/\*/g, '.*').replace(/\?/g, '.')}$`);
const DENYLIST = rules.denylist.map(fnmatch);
/** `repos.NEVER_MATERIAL`, matched per path component, case-insensitively. */
const NEVER_MATERIAL = new Set(rules.never_material.map((x) => x.toLowerCase()));

const parts = (path: string) => path.split('/').filter(Boolean).map((x) => x.toLowerCase());
export const neverMaterial = (path: string) => parts(path).some((x) => NEVER_MATERIAL.has(x) || x === '.releaseignore');
/** Never published whatever a list says: solutions, tests, grading files, `.env`. */
export const denylisted = (path: string) => parts(path).some((x) => DENYLIST.some((re) => re.test(x)));

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

/** Where a path stands in a list: its own line, re-included by its own `!` line, withheld by a broader rule, or neither. */
export type Standing =
  | { kind: 'own' }
  | { kind: 'included' }
  | { kind: 'broader'; rule: string; at: string; legal: boolean }
  | { kind: 'released' };

export function standing(rs: Rule[], lines: string[], path: string, isDir: boolean): Standing {
  const live = lines.filter((l) => compile(l));
  if (live.some((l) => !l.trim().startsWith('!') && names(l, path, isDir))) return { kind: 'own' };
  if (live.some((l) => l.trim().startsWith('!') && names(l.trim().slice(1), path, isDir))) return { kind: 'included' };
  const w = withheldBy(rs, path, isDir);
  if (!w) return { kind: 'released' };
  return { kind: 'broader', rule: w.rule.line, at: w.at, legal: w.at === path };
}

/**
 * A click: the list after it, or why it cannot change: the path sits inside a withheld folder
 * (`at` is that folder), or it is a folder a `/**` rule withholds, which also covers everything
 * inside it so no `!` line can release its files (`at` is the folder itself).
 */
export type Toggled = { lines: string[] } | { blocked: { rule: string; at: string } };

/**
 * Toggle one path: its own line goes (so does its own `!` line); a path nothing withholds gets
 * its exact line; one a broader rule withholds gets `!line` when git allows re-inclusion, and
 * otherwise nothing changes and the caller says which folder rule to remove.
 */
export function toggle(lines: string[], path: string, isDir: boolean): Toggled {
  const s = standing(compileAll(lines), lines, path, isDir);
  const line = exactLine(path, isDir);
  if (s.kind === 'own') return { lines: lines.filter((l) => !(compile(l) && !l.trim().startsWith('!') && names(l, path, isDir))) };
  if (s.kind === 'included') return { lines: lines.filter((l) => !(compile(l) && l.trim().startsWith('!') && names(l.trim().slice(1), path, isDir))) };
  if (s.kind === 'released') return { lines: append(lines, line) };
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
