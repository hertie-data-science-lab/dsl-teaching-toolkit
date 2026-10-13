// A materials repo's files as a folder tree, to look at, not to edit (the course materials
// index): every folder starts collapsed, with "N files" beside it while it is, and a GitHub link
// to its tree page; a file's name opens its blob page on GitHub. The folders a viewer opened are
// kept per repo in sessionStorage, a convenience only. `focus` (`?folder=<repo>/<path>`) opens
// that folder and its parents, and scrolls it into view.

import { useEffect, useRef, useState } from 'preact/hooks';
import { readJson, safeStorage, writeJson } from '../auth/types';
import { buildTree, type TreeNode } from '../edit/badges';
import { plural } from '../model/format';
import { ghUrl } from './bits';
import { FileHead, FolderHead } from './FileTree';
import { Ext } from './icons';

const storeKey = (org: string, repo: string) => `dsl.materialsTree.${org}/${repo}`;

/** `path` and every folder above it. */
const withParents = (path: string) => path.split('/').map((_, i, a) => a.slice(0, i + 1).join('/'));

/** The files under a folder, at any depth. */
export const fileCount = (n: TreeNode): number => (n.children ? n.children.reduce((s, c) => s + fileCount(c), 0) : 1);

/** The link that opens `?folder=<repo>/<path>` on the course materials index. */
export const folderHref = (org: string, repo: string, path: string) => `?course=${org}&folder=${encodeURIComponent(`${repo}/${path}`)}#materials`;

export function MaterialsTree({ org, repo, files, focus }: { org: string; repo: string; files: string[]; focus?: string }) {
  const [open, setOpen] = useState<Set<string>>(() => {
    const kept = readJson<string[]>(safeStorage('session'), storeKey(org, repo));
    return new Set([...(Array.isArray(kept) ? kept : []), ...(focus ? withParents(focus) : [])]);
  });
  const list = useRef<HTMLUListElement>(null);
  const tree = buildTree(files.filter((f) => !f.startsWith('.')));
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
        <li class="ft-file">
          <FileHead name={n.name}><a class="ft-name" href={ghUrl(org, repo, n.path, 'HEAD')} target="_blank" rel="noopener">{n.name}</a></FileHead>
        </li>
      );
    }
    const isOpen = open.has(n.path);
    return (
      <li class="ft-dir" data-folder={n.path}>
        <details open={isOpen} onToggle={(e) => toggle(n.path, (e.currentTarget as HTMLDetailsElement).open)}>
          <summary>
            <FolderHead name={n.name} />
            {isOpen ? null : <span class="ft-count">{plural(fileCount(n), 'file')}</span>}
            <a class="ft-gh" href={ghUrl(org, repo, n.path, 'HEAD', 'tree')} target="_blank" rel="noopener" aria-label={`${n.path}/ on GitHub`}>GitHub <Ext /></a>
          </summary>
          <ul>{n.children.map(node)}</ul>
        </details>
      </li>
    );
  };
  if (!tree.length) return <p class="footnote">The repo has no files yet.</p>;
  return <ul class="file-tree materials-tree" ref={list}>{tree.map(node)}</ul>;
}
