// A materials repo's files as a collapsible tree, each file badged with what happens to it.

import { BADGE_WORD, buildTree, type Badge, type TreeNode } from '../edit/badges';
import { editUrl } from './bits';

const BADGE_CLASS: Record<Badge, string> = { public: 'ok', withheld: 'amber', released: '', never_public: '' };

function counts(n: TreeNode, badges: Record<string, Badge>, out: Record<Badge, number> = { public: 0, withheld: 0, released: 0, never_public: 0 }) {
  if (!n.children) out[badges[n.path] ?? 'released']++;
  else for (const c of n.children) counts(c, badges, out);
  return out;
}

function Node({ n, badges, org, repo, branch, depth, kinds }: { n: TreeNode; badges: Record<string, Badge>; org: string; repo: string; branch: string | null; depth: number; kinds: Record<string, string> }) {
  if (!n.children) {
    const b = badges[n.path] ?? 'released';
    return (
      <li class="ft-file">
        <span class="ft-name">{n.name}</span>
        <span class={`chip ${BADGE_CLASS[b]}`}>{BADGE_WORD[b]}</span>
        {branch ? <a class="textlink" href={editUrl(org, repo, n.path, branch)} target="_blank" rel="noopener" aria-label={`Edit ${n.path} on GitHub`}>Edit on GitHub</a> : null}
      </li>
    );
  }
  const c = counts(n, badges);
  return (
    <li class="ft-dir">
      <details open={depth === 0}>
        <summary>
          <span class="ft-name">{n.name}/</span>
          {depth === 0 && kinds[n.name] ? <span class="chip">{kinds[n.name]}</span> : null}
          {c.public ? <span class="chip ok">{c.public} hosted</span> : null}
          {c.withheld ? <span class="chip amber">{c.withheld} withheld</span> : null}
        </summary>
        <ul>{n.children.map((x) => <Node n={x} badges={badges} org={org} repo={repo} branch={branch} depth={depth + 1} kinds={kinds} />)}</ul>
      </details>
    </li>
  );
}

/** `branch` is the repo's default branch; while it is unknown (null) the Edit links are left out. `kinds` labels a top-level folder with its kind. */
export function FileTree({ files, badges, org, repo, branch, kinds = {} }: { files: string[]; badges: Record<string, Badge>; org: string; repo: string; branch: string | null; kinds?: Record<string, string> }) {
  if (!files.length) return <p class="footnote">The repo has no files yet.</p>;
  return <ul class="file-tree">{buildTree(files).map((n) => <Node n={n} badges={badges} org={org} repo={repo} branch={branch} depth={0} kinds={kinds} />)}</ul>;
}
