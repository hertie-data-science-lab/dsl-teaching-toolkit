// What a withhold list does to each file of a repo, and how a click on the tree edits it.
// One widget serves `.releaseignore` (withheld from students), course.yml's `open_course_site.withhold`
// (kept off the public website) and the import picker (decision 0016).
//
// The rule is the engine's (`releaseignore`): gitignore syntax, the last matching line wins,
// and a withheld folder is never walked, so nothing inside it can be re-included. The cases in
// `schemas/materials.json` were answered by the engine and the tests hold this file to them.
// Never-material names (`.DS_Store`, `__pycache__`) and the list file itself never leave the repo;
// a `.gitkeep` is a file like any other (it is how an empty folder ships).
//
// Decision B (2026-11-04): a list compiled with the institution's defaults (`compileAll(lines,
// { defaults: true })`, `.releaseignore` only) reads the content names (`solutions`, `tests`) as
// implied first lines, so a click on one writes `!path` and a second click takes it out again;
// the never-released names (`.env`, `.git`, grading files) and the never-copied ones stand apart,
// which no line changes.

import { clutter, denylisted, hardDenied, parts } from '../model/selection';
import { carriesDefaults, compile, compileAll, deciding, matchRules, withheldBy, type CompileOpts, type Rule } from './glob';

export type Badge = 'withheld' | 'released';

/** Clutter (`selection.CLUTTER`) and the list file itself: never copied anywhere. */
export const neverMaterial = (path: string) => clutter(path) || parts(path).includes('.releaseignore');
/** The institution denylist (`selection.DENYLIST`): solutions, tests, grading files, `.env*`, `.git`, `.system`. */
export { denylisted, hardDenied };

export interface Badged {
  badges: Record<string, Badge>;
  /** Pattern lines that match no file or folder, as written. */
  unmatched: string[];
  /** How many rules the list has, comments and blank lines aside. */
  rules: number;
}

/** Each file's badge from one list, and the rules that match nothing; `rs` the list compiled, when the caller has it. */
export function badgeFiles(files: string[], lines: string[], rs: Rule[] = compileAll(lines)): Badged {
  const own = rs.filter((r) => !r.default);
  const badges: Record<string, Badge> = {};
  for (const f of files) badges[f] = neverMaterial(f) || withheldBy(own, f) ? 'withheld' : 'released';
  const unmatched = own.filter((r) => !files.some((f) => matchRules([{ ...r, neg: false }], f))).map((r) => r.line);
  return { badges, unmatched, rules: own.length };
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
 * broader rule, or neither. Under the institution's defaults (rules compiled with them), also
 * withheld by a content name (`default`, `at` the path or the folder it sits in), a content name
 * a `!` line re-includes (`overridden`, `rule` that line), and the names no line changes:
 * never released (`hardDenied`) and never copied (clutter, the list file).
 */
export type Standing =
  | { kind: 'own' }
  | { kind: 'included' }
  | { kind: 'broader'; rule: string; at: string; legal: boolean }
  | { kind: 'default'; at: string }
  | { kind: 'overridden'; rule: string }
  | { kind: 'never_released' }
  | { kind: 'never_copied' }
  | { kind: 'released' };

export type Bucket = 'never' | 'withheld' | 'released';

/** What a standing comes to: never leaves the repo, withheld (the row unticked: a folder copy leaves it), or released. */
export function bucket(s: Standing): Bucket {
  if (s.kind === 'never_released' || s.kind === 'never_copied') return 'never';
  return s.kind === 'own' || s.kind === 'broader' || s.kind === 'default' ? 'withheld' : 'released';
}

export function standing(rs: Rule[], lines: string[], path: string, isDir: boolean): Standing {
  if (carriesDefaults(rs)) {
    if (neverMaterial(path)) return { kind: 'never_copied' };
    if (hardDenied(path)) return { kind: 'never_released' };
  }
  const live = lines.filter((l) => compile(l));
  if (live.some((l) => !l.trim().startsWith('!') && names(l, path, isDir))) return { kind: 'own' };
  const w = withheldBy(rs, path, isDir);
  const withheld = (x: NonNullable<typeof w>): Standing => (x.rule.default ? { kind: 'default', at: x.at } : { kind: 'broader', rule: x.rule.line, at: x.at, legal: x.at === path });
  // A withheld folder is never walked: no `!` line of the path's own re-includes it.
  if (w && w.at !== path) return withheld(w);
  // A content name itself (not only something inside one).
  const content = rs.some((r) => r.default && (!r.dirOnly || isDir) && r.re.test(path));
  const bang = live.find((l) => l.trim().startsWith('!') && names(l.trim().slice(1), path, isDir));
  if (bang) return content ? { kind: 'overridden', rule: bang.trim() } : { kind: 'included' };
  // A content name the copy carries: the `!` line deciding it re-included it.
  if (!w) return content ? { kind: 'overridden', rule: deciding(rs, path, isDir)?.line ?? '' } : { kind: 'released' };
  return withheld(w);
}

/** How many of `files` stand in each bucket, by their standings. */
export function tally(files: string[], standingOf: (path: string) => Standing): Record<Bucket, number> {
  const n = { never: 0, withheld: 0, released: 0 };
  for (const f of files) n[bucket(standingOf(f))]++;
  return n;
}

/**
 * A click: the list after it, or why it cannot change: the path sits inside a folder
 * a `.releaseignore` line withholds (`at` is that folder), or it is a folder a `/**` rule
 * withholds, which also covers everything inside it so no `!` line can release its files (`at`
 * is the folder itself).
 */
export type Toggled = { lines: string[] } | { blocked: { rule: string; at: string } };

/**
 * Toggle one path, so the row always flips: its own line goes (so does its own `!` line), and
 * when that leaves it as it was, the line that flips it is written instead; a path nothing
 * withholds gets its exact line; one a broader rule withholds gets `!line` when git allows
 * re-inclusion, and otherwise nothing changes and the caller says which folder rule to remove.
 * A content name the default withholds gets `!line`; a path inside a default-withheld folder
 * releases that folder with it (its `!` line, then the next one in's). `o` is
 * how the list is compiled (`defaults` for `.releaseignore`).
 */
export function toggle(lines: string[], path: string, isDir: boolean, o: CompileOpts = {}): Toggled {
  const s = standing(compileAll(lines, o), lines, path, isDir);
  const line = exactLine(path, isDir);
  const own = (l: string) => !!compile(l) && !l.trim().startsWith('!') && names(l, path, isDir);
  const ownBang = (l: string) => !!compile(l) && l.trim().startsWith('!') && names(l.trim().slice(1), path, isDir);
  const after = (next: string[], out: boolean) => {
    const t = standing(compileAll(next, o), next, path, isDir);
    if ((bucket(t) !== 'released') === out) return { lines: next };
    return { lines: out ? append(next, line) : t.kind === 'default' && t.at === path ? append(next, `!${line}`) : next };
  };
  if (s.kind === 'never_released' || s.kind === 'never_copied') return { lines };
  if (s.kind === 'own') return after(lines.filter((l) => !own(l)), false);
  if (s.kind === 'included' || s.kind === 'overridden') return lines.some(ownBang) ? after(lines.filter((l) => !ownBang(l)), true) : { lines: append(lines, line) };
  if (s.kind === 'released') return { lines: append(lines, line) };
  if (s.kind === 'default') {
    if (s.at === path) return { lines: append(lines, `!${line}`) };
    // Inside a default-withheld folder: its `!` line releases the folder with the path.
    const next = append(lines, `!${exactLine(s.at, true)}`);
    return bucket(standing(compileAll(next, o), next, path, isDir)) === 'released' ? { lines: next } : toggle(next, path, isDir, o);
  }
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

/** A row of the tree as worked out once: its standing, and the files at or under it withheld (fixed ones counted), with their states where `state` names one. */
export interface RowModel<S extends string = string> {
  s: Standing;
  withheld: number;
  /** The files that ship, by state (`treeModel`'s `state`). */
  states: Partial<Record<S, number>>;
}

/**
 * Every row of `tree` in one post-order pass: each path's standing in the list (`rs` compiled from
 * `lines`) and its folder's counts. `fixed`: a row no list changes (counted withheld); `state`: a
 * shipping file's state, rolled up per folder.
 */
export function treeModel<S extends string = string>(
  tree: TreeNode[], rs: Rule[], lines: string[],
  o: { fixed?: (path: string) => unknown; state?: (path: string) => S | undefined } = {},
): Map<string, RowModel<S>> {
  const out = new Map<string, RowModel<S>>();
  const visit = (n: TreeNode): RowModel<S> => {
    const s = standing(rs, lines, n.path, !!n.children);
    let row: RowModel<S>;
    if (n.children) {
      row = { s, withheld: 0, states: {} };
      for (const c of n.children) {
        const x = visit(c);
        row.withheld += x.withheld;
        for (const k of Object.keys(x.states) as S[]) row.states[k] = (row.states[k] ?? 0) + (x.states[k] ?? 0);
      }
    } else {
      const out = !!o.fixed?.(n.path) || bucket(s) !== 'released';
      const st = out ? undefined : o.state?.(n.path);
      row = { s, withheld: out ? 1 : 0, states: st ? ({ [st]: 1 } as Partial<Record<S, number>>) : {} };
    }
    out.set(n.path, row);
    return row;
  };
  tree.forEach(visit);
  return out;
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
