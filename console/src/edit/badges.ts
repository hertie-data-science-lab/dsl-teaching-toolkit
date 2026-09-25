// What happens to each file of a materials repo, from its `publish.yml` public patterns and
// its root `.releaseignore`: withheld files never reach students (and so are never hosted),
// files matching a public pattern are also hosted openly on the student site, the rest are
// released to students only. Both lists use gitignore syntax (`./glob`).
//
// The engine's rule, `materials.hosted_paths`, with its lists read from
// `schemas/materials.json`: never-material names are never copied out at all (withheld);
// denylisted paths are released but never hosted, whatever a pattern says; a hosted deck
// also hosts its `<stem>_files/` and the asset folders beside it. The same file carries
// cases the engine answered, and the tests hold this function to them.

import rules from '../../schemas/materials.json';
import { compile, matchRules, type Rule } from './glob';

export type Badge = 'public' | 'withheld' | 'released' | 'never_public';

export const BADGE_WORD: Record<Badge, string> = {
  public: 'hosted on the student site', withheld: 'withheld', released: 'released to students', never_public: 'released to students, never hosted',
};

/** An fnmatch pattern (`.env.*`) as a whole-name regex. */
const fnmatch = (p: string) => new RegExp(`^${p.replace(/[.+^${}()|[\]\\]/g, '\\$&').replace(/\*/g, '.*').replace(/\?/g, '.')}$`);
/** `repos.PUBLICATION_DENYLIST`, matched per path component, case-insensitively. */
const DENYLIST = rules.denylist.map(fnmatch);
/** `repos.NEVER_MATERIAL`, matched per path component, case-insensitively. */
const NEVER_MATERIAL = new Set(rules.never_material.map((x) => x.toLowerCase()));

const parts = (path: string) => path.split('/').filter(Boolean).map((x) => x.toLowerCase());
export const neverMaterial = (path: string) => parts(path).some((x) => NEVER_MATERIAL.has(x));
export const denylisted = (path: string) => parts(path).some((x) => DENYLIST.some((re) => re.test(x)));
const publishable = (path: string) => !denylisted(path) && !neverMaterial(path);
const isDeck = (path: string) => {
  const name = path.slice(path.lastIndexOf('/') + 1);
  return name.includes('.') && rules.deck_extensions.includes(name.slice(name.lastIndexOf('.') + 1).toLowerCase());
};

/** `materials.bundle_prefixes`: the folders a deck's assets sit in, beside it. */
export function bundlePrefixes(deck: string): string[] {
  const folder = deck.includes('/') ? `${deck.slice(0, deck.lastIndexOf('/'))}/` : '';
  return [`${deck.slice(0, deck.lastIndexOf('.'))}_files/`, ...rules.bundle_dirs.map((d) => `${folder}${d}/`)];
}

/** `materials.hosted_paths`: the paths the public patterns host, bundles included. */
export function hostedPaths(files: string[], publicLines: string[]): Set<string> {
  const pub = publicLines.map(compile).filter((r): r is Rule => r !== null);
  const open = new Set(files.filter((f) => publishable(f) && matchRules(pub, f)));
  for (const deck of [...open].filter(isDeck)) {
    const prefixes = bundlePrefixes(deck);
    for (const f of files) if (prefixes.some((x) => f.startsWith(x)) && publishable(f)) open.add(f);
  }
  return open;
}

export interface Badged {
  badges: Record<string, Badge>;
  /** Pattern lines that match no file, per list, as written. */
  unmatched: { public: string[]; withheld: string[] };
  /** How many rules each list has, comments and blank lines aside. */
  rules: { public: number; withheld: number };
}

function rulesOf(lines: string[]): { line: string; rule: Rule }[] {
  return lines.flatMap((line) => {
    const rule = compile(line);
    return rule ? [{ line: line.trim(), rule }] : [];
  });
}

function unmatched(rules: { line: string; rule: Rule }[], files: string[]): string[] {
  return rules.filter(({ rule }) => !files.some((f) => matchRules([{ ...rule, neg: false }], f))).map((r) => r.line);
}

/** Each file's badge, and the rules that match nothing. */
export function badgeFiles(files: string[], publicLines: string[], withheldLines: string[]): Badged {
  const pub = rulesOf(publicLines), ign = rulesOf(withheldLines);
  const ignRules = ign.map((r) => r.rule);
  const open = hostedPaths(files, publicLines);
  const badges: Record<string, Badge> = {};
  for (const f of files)
    badges[f] = neverMaterial(f) || matchRules(ignRules, f) ? 'withheld' : open.has(f) ? 'public' : denylisted(f) ? 'never_public' : 'released';
  return { badges, unmatched: { public: unmatched(pub, files), withheld: unmatched(ign, files) }, rules: { public: pub.length, withheld: ign.length } };
}

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
