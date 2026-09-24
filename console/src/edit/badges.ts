// What happens to each file of a materials repo, from its `publish.yml` public patterns and
// its root `.releaseignore`: withheld files never reach students (and so are never public),
// files matching a public pattern are also published openly, the rest are released to
// students only. Both lists use gitignore syntax (`./glob`).

import { compile, matchRules, type Rule } from './glob';

export type Badge = 'public' | 'withheld' | 'released';

export const BADGE_WORD: Record<Badge, string> = { public: 'published openly', withheld: 'withheld', released: 'released to students' };

export interface Badged {
  badges: Record<string, Badge>;
  /** Pattern lines that match no file, per list, as written. */
  unmatched: { public: string[]; withheld: string[] };
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
  const pubRules = pub.map((r) => r.rule), ignRules = ign.map((r) => r.rule);
  const badges: Record<string, Badge> = {};
  for (const f of files) badges[f] = matchRules(ignRules, f) ? 'withheld' : matchRules(pubRules, f) ? 'public' : 'released';
  return { badges, unmatched: { public: unmatched(pub, files), withheld: unmatched(ign, files) } };
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
