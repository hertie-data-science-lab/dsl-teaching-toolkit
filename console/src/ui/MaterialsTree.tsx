// A materials repo's files as a folder tree, to look at, not to edit (the course materials
// index): every folder starts collapsed, with "N files" beside it while it is, and a GitHub link
// to its tree page; each file is the file row both consoles share (`FileRow.tsx`), its name
// opening the file in the console's viewer (`?file=<repo>/<path>`, decision 0040 rule 6). The
// folders a viewer opened are kept per repo in sessionStorage, a convenience only. `focus`
// (`?folder=<repo>/<path>`) opens that folder and its parents, and scrolls it into view;
// `current`, the file open beside the tree, is marked and its folders opened.

import { useEffect, useMemo, useRef, useState } from 'preact/hooks';
import { readJson, safeStorage, writeJson } from '../auth/types';
import { buildTree, type TreeNode } from '../edit/badges';
import { plural } from '../model/format';
import { profileHref } from '../model/open';
import { ghUrl } from './bits';
import { FileRow, useYourSetup } from './FileRow';
import { FileHead, FolderHead } from './FileTree';
import { Ext } from './icons';

const storeKey = (org: string, repo: string) => `dsl.materialsTree.${org}/${repo}`;

/** `path` and every folder above it. */
const withParents = (path: string) => path.split('/').map((_, i, a) => a.slice(0, i + 1).join('/'));

/** The tree of a repo's files (dot folders and files left out), and the files under each folder at any depth. */
function treeOf(paths: { path: string; dir: boolean }[]): { tree: TreeNode[]; counts: Map<string, number> } {
  const tree = buildTree(paths.filter((x) => !x.dir && !x.path.startsWith('.')).map((x) => x.path));
  const counts = new Map<string, number>();
  const count = (n: TreeNode): number => {
    if (!n.children) return 1;
    const c = n.children.reduce((s, x) => s + count(x), 0);
    counts.set(n.path, c);
    return c;
  };
  tree.forEach(count);
  return { tree, counts };
}

/** The link that opens `?folder=<repo>/<path>` on the course materials index. */
export const folderHref = (org: string, repo: string, path: string) => `?course=${org}&folder=${encodeURIComponent(`${repo}/${path}`)}#materials`;

/** The link that opens a file in the viewer on the course materials index, `?file=<repo>/<path>`. */
export const fileViewHref = (org: string, repo: string, path: string) => `?course=${org}&file=${encodeURIComponent(`${repo}/${path}`)}#materials`;

/** The folders above `path`. */
const parentsOf = (path: string) => withParents(path).slice(0, -1);

export function MaterialsTree({ org, repo, paths, focus, current }: { org: string; repo: string; paths: { path: string; dir: boolean }[]; focus?: string; current?: string }) {
  const setup = useYourSetup();
  const [open, setOpen] = useState<Set<string>>(() => {
    const kept = readJson<string[]>(safeStorage('session'), storeKey(org, repo));
    return new Set([...(Array.isArray(kept) ? kept : []), ...(focus ? withParents(focus) : []), ...(current ? parentsOf(current) : [])]);
  });
  const list = useRef<HTMLUListElement>(null);
  const { tree, counts } = useMemo(() => treeOf(paths), [paths]);
  useEffect(() => {
    if (!focus) return;
    setOpen((o) => (withParents(focus).every((x) => o.has(x)) ? o : new Set([...o, ...withParents(focus)])));
    // After the page's own scroll to the top on a move.
    const t = setTimeout(() => {
      const li = [...(list.current?.querySelectorAll<HTMLElement>('li[data-folder]') ?? [])].find((x) => x.dataset.folder === focus);
      li?.scrollIntoView?.({ block: 'start' });
    }, 0);
    return () => clearTimeout(t);
  }, [focus, tree.length > 0]);
  const toggle = (path: string, now: boolean) => {
    if (open.has(path) === now) return;
    const next = new Set(open);
    if (now) next.add(path);
    else next.delete(path);
    setOpen(next);
    writeJson(safeStorage('session'), storeKey(org, repo), [...next]);
  };
  const node = (n: TreeNode) => {
    if (!n.children) {
      return (
        <li class={`ft-file${current === n.path ? ' current' : ''}`}>
          <FileHead name={n.name}>
            <FileRow org={org} link={{ name: n.name, repo, path: n.path, url: ghUrl(org, repo, n.path, 'HEAD') }} here={fileViewHref(org, repo, n.path)} setup={setup} profile={profileHref(org)} cls="ft-name" current={current === n.path} />
          </FileHead>
        </li>
      );
    }
    const isOpen = open.has(n.path);
    return (
      <li class="ft-dir" data-folder={n.path}>
        <details open={isOpen} onToggle={(e) => toggle(n.path, (e.currentTarget as HTMLDetailsElement).open)}>
          <summary>
            <FolderHead name={n.name} />
            {isOpen ? null : <span class="ft-count">{plural(counts.get(n.path) ?? 0, 'file')}</span>}
            <a class="ft-gh" href={ghUrl(org, repo, n.path, 'HEAD', 'tree')} target="_blank" rel="noopener" aria-label={`${n.path}/ on GitHub`}>GitHub <Ext /></a>
          </summary>
          {isOpen ? <ul>{n.children.map(node)}</ul> : null}
        </details>
      </li>
    );
  };
  if (!tree.length) return <p class="footnote">The repo has no files yet.</p>;
  return <ul class="file-tree materials-tree" ref={list}>{tree.map(node)}</ul>;
}
