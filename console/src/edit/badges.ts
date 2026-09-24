// What happens to each file of a materials repo, from its `publish.yml` public patterns and
// its root `.releaseignore`: withheld files never reach students (and so are never public),
// files matching a public pattern are also published openly, the rest are released to
// students only. Both lists use gitignore syntax (`./glob`).
//
// Mirrors the engine: `repos.NEVER_MATERIAL` names are never copied out at all (withheld);
// `repos.PUBLICATION_DENYLIST` paths are released but never published, whatever a pattern
// says; a public `<stem>.html` deck also publishes its `<stem>_files/` bundle
// (`site._public_selection`, `site._bundle_prefix`).

import { compile, matchRules, type Rule } from './glob';

export type Badge = 'public' | 'withheld' | 'released' | 'never_public';

export const BADGE_WORD: Record<Badge, string> = {
  public: 'published openly', withheld: 'withheld', released: 'released to students', never_public: 'released to students, never published',
};

/** `repos.PUBLICATION_DENYLIST`, matched per path component, case-insensitively. */
const DENYLIST = [/^solutions?$/, /^grading_config\.yml$/, /^grading\.yml$/, /^tests$/, /^\.env$/, /^\.env\..*$/, /^\.git$/];
/** `repos.NEVER_MATERIAL`, matched per path component, case-insensitively. */
const NEVER_MATERIAL = new Set(['.ds_store', '.gitkeep', 'thumbs.db', 'desktop.ini', '__pycache__', '.ipynb_checkpoints']);

const parts = (path: string) => path.split('/').filter(Boolean).map((x) => x.toLowerCase());
export const neverMaterial = (path: string) => parts(path).some((x) => NEVER_MATERIAL.has(x));
export const denylisted = (path: string) => parts(path).some((x) => DENYLIST.some((re) => re.test(x)));
const publishable = (path: string) => !denylisted(path) && !neverMaterial(path);
const DECK = /\.html?$/i;

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
  const pubRules = pub.map((r) => r.rule), ignRules = ign.map((r) => r.rule);
  const open = new Set(files.filter((f) => publishable(f) && matchRules(pubRules, f)));
  for (const deck of [...open].filter((f) => DECK.test(f))) {
    const prefix = `${deck.slice(0, deck.lastIndexOf('.'))}_files/`;
    for (const f of files) if (f.startsWith(prefix) && publishable(f)) open.add(f);
  }
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
