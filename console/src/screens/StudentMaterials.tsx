// Materials for a student: the released `materials` repo as a tree (one recursive tree read),
// a file opened in the console from the private copy with the student's own token (the viewer
// both consoles share, `ui/Viewer.tsx`), and the readings of each session. Nothing is published
// anywhere. Top folders that only support the rest (data, images, code a notebook
// loads) come last, folded, as "Supporting files" (decision 0029 rule 6). The tree stays on
// screen while a file is open (decision 0035 rule 11): in a left column on wide screens, above
// the file on narrow ones, the open file marked; each file row carries the menu
// (`StudentFiles.tsx`, rule 10). A kind tab opens its files in the same split (`FileBeside`),
// its own list in the left column.

import { DEFAULT_TIMEZONE } from '../model/policy';
import { useEnv } from '../env';
import type { TreeEntry } from '../github/client';
import { buildTree, type TreeNode } from '../edit/badges';
import { fmtDay } from '../model/format';
import { KINDS } from '../model/policy';
import { sortedRows, type SemesterFacts } from '../model/student';
import { CheckLine, Loading, Md, ghUrl } from '../ui/bits';
import { FileHead, FolderHead } from '../ui/FileTree';
import { useLoad } from '../ui/load';
import { OpenFile, RepoFile, Split } from '../ui/Viewer';
import { useYourSetup } from '../ui/FileRow';
import { FileLinkItem, FileList } from './StudentFiles';

/** `<repo>/<path>` back into its parts, for a repo among `repos`. */
export function splitEntry(entry: string, repos: string[]): { repo: string; path: string } | null {
  const i = entry.indexOf('/');
  if (i < 0) return null;
  const repo = entry.slice(0, i);
  return repos.includes(repo) ? { repo, path: entry.slice(i + 1) } : null;
}

export function MaterialsView({ org, repos, entry }: { org: string; repos: string[]; entry?: string }) {
  const env = useEnv();
  const trees = useLoad(
    env ? () => Promise.all(repos.map(async (r) => [r, (await env.client.listTree(org, r, 'HEAD', true))?.tree ?? null] as const)) : null,
    [org, repos.join(',')],
  );
  if (trees.kind === 'loading') return <Loading what="Reading the materials" />;
  if (trees.kind === 'failed') return <CheckLine cls="bad">The materials could not be read: {trees.error}</CheckLine>;
  const at = entry ? splitEntry(entry, repos) : null;
  if (at) {
    return (
      <Split label="All materials" side={<MaterialsTree org={org} trees={trees.value} current={at} />}>
        <OpenFile org={org} at={at} tree={trees.value.find(([r]) => r === at.repo)?.[1] ?? []} />
      </Split>
    );
  }
  return <MaterialsTree org={org} trees={trees.value} />;
}

/** `children` (a kind tab's list) with the file `entry` names open beside it, its repo's tree read for it; `children` alone for an entry in none of `repos`. */
export function FileBeside({ org, repos, entry, label, children }: { org: string; repos: string[]; entry: string; label: string; children: preact.ComponentChildren }) {
  const at = splitEntry(entry, repos);
  if (!at) return <>{children}</>;
  return <Split label={label} side={children}><RepoFile org={org} at={at} /></Split>;
}

/**
 * A top-level tree node is supporting files (decision 0026): a folder named for a kind of the
 * institution's that is released but never a row (`assets/`). Folder names imply no other kind
 * (decision 0038: a kind comes from the schedule), so no other folder is folded away.
 */
export const isSupport = (n: TreeNode) => !!n.children && KINDS.some((k) => !k.shown && !k.system && [k.key, k.plural].includes(n.name.toLowerCase()));

/** Every repo's tree; `current`, the file open beside it, is marked and its folders open. */
export function MaterialsTree({ org, trees, current }: { org: string; trees: (readonly [string, TreeEntry[] | null])[]; current?: { repo: string; path: string } | null }) {
  const setup = useYourSetup();
  const shown = trees.filter(([, t]) => t !== null);
  if (!shown.length) return <p class="footnote">Nothing has been released yet, or you cannot read the materials: that needs the semester’s student team, which joining gives you.</p>;
  return (
    <div class="stack">
      {shown.map(([repo, tree]) => {
        const files = tree!.filter((t) => t.type === 'blob').map((t) => t.path);
        const here = current?.repo === repo ? current.path : null;
        const node = (n: TreeNode, depth: number): preact.JSX.Element =>
          n.children ? (
            <li class="ft-dir"><details open={(depth === 0 && !n.name.endsWith('_files')) || !!here?.startsWith(`${n.path}/`)}><summary><FolderHead name={n.name} /></summary><ul>{n.children.map((c) => node(c, depth + 1))}</ul></details></li>
          ) : (
            <li class={`ft-file${here === n.path ? ' current' : ''}`}><FileHead name={n.name}><FileLinkItem org={org} repos={[repo]} link={{ name: n.name, repo, path: n.path, url: ghUrl(org, repo, n.path, 'HEAD') }} setup={setup} cls="ft-name" current={here === n.path} /></FileHead></li>
          );
        const top = buildTree(files);
        const support = top.filter(isSupport);
        const main = top.filter((n) => !isSupport(n));
        return (
          <section class="panel section" aria-label={repo}>
            <h2>{repo}</h2>
            {main.length ? <ul class="file-tree">{main.map((n) => node(n, 0))}</ul> : files.length ? null : <p class="footnote">Nothing released yet.</p>}
            {support.length ? (
              <details class="fold supporting" open={!!here && support.some((n) => here.startsWith(`${n.path}/`))}>
                <summary>Supporting files</summary>
                <ul class="file-tree">{support.map((n) => node(n, 1))}</ul>
              </details>
            ) : null}
          </section>
        );
      })}
      {current ? null : <p class="footnote">Every file opens here, read with your own account: nothing is published.</p>}
    </div>
  );
}

/** The readings of every session: its reading files (opened here, each with its menu, decision 0035 rule 10), its reading list, or "to come" while they are planned but not out. */
export function ReadingsView({ org, facts, now }: { org: string; facts: SemesterFacts; now: number }) {
  const tz = facts.timezone || DEFAULT_TIMEZONE;
  const rows = sortedRows(facts.rows, tz).filter((r) => r.readings.length || r.readingList || r.readingsPending);
  if (!rows.length) return null;
  const year = new Date(now).getFullYear();
  return (
    <section class="panel section" aria-labelledby="h-readings">
      <h2 id="h-readings">Readings</h2>
      <ul class="plain-list readings">
        {rows.map((r) => (
          <li>
            <b>{r.title}</b>{r.subtitle ? `: ${r.subtitle}` : ''} <span class="footnote">{fmtDay(r.when, tz, year)}</span>
            {r.readings.length ? <FileList org={org} repos={facts.materialsRepos} links={r.readings} /> : null}
            {r.readingList ? <Md class="reading-list" src={r.readingList.replace(/^#{1,6}\s+(.+)$/gm, '**$1**')} /> : null}
            {!r.readings.length && !r.readingList ? <span class="footnote"> Readings to come.</span> : null}
          </li>
        ))}
      </ul>
    </section>
  );
}
