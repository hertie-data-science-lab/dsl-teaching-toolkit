// Materials for a student: the released `materials` repo as a tree (one recursive tree read),
// a file opened in the console from the private copy with the student's own token, and the
// readings of each session. Nothing is published anywhere; see model/materials.ts for how
// each kind is shown. Top folders that only support the rest (data, images, code a notebook
// loads) come last, folded, as "Supporting files" (decision 0029 rule 6). The tree stays on
// screen while a file is open (decision 0035 rule 11): in a left column on wide screens, above
// the file on narrow ones, the open file marked; each file row carries the button row
// (`StudentFiles.tsx`, rule 10).

import { DEFAULT_TIMEZONE } from '../model/policy';
import { useEffect, useState } from 'preact/hooks';
import { useEnv } from '../env';
import type { TreeEntry } from '../github/client';
import { buildTree, type TreeNode } from '../edit/badges';
import { openDeck } from '../model/deckTab';
import { fmtDay } from '../model/format';
import { ASSETS_KIND, aliasKind } from '../model/materialsRules';
import { showFile, type Shown } from '../model/materials';
import { sortedRows, type SemesterFacts } from '../model/student';
import { CheckLine, Loading, Md, ghUrl } from '../ui/bits';
import { FileHead, FolderHead } from '../ui/FileTree';
import { Ext } from '../ui/icons';
import { useLoad } from '../ui/load';
import { FileLinkItem, FileList, useYourSetup } from './StudentFiles';

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
    const tree = trees.value.find(([r]) => r === at.repo)?.[1] ?? [];
    const e = tree.find((t) => t.path === at.path && t.type === 'blob');
    return (
      <div class="materials-split">
        <nav class="materials-tree" aria-label="All materials"><MaterialsTree org={org} trees={trees.value} current={at} /></nav>
        <div class="materials-file">{e ? <FileView org={org} repo={at.repo} entry={e} tree={tree} /> : <p class="footnote">{at.path} is not in {at.repo}.</p>}</div>
      </div>
    );
  }
  return <MaterialsTree org={org} trees={trees.value} />;
}

/**
 * A top-level tree node is supporting files (decision 0026): a folder the engine's built-in
 * names make so (data/, img/, ...). Only those: a folder no name covers is supporting files
 * by default (decision 0031), but the status file carries no repo's own kinds, so one the
 * instructor set to a lecture or lab would be folded away by mistake.
 */
export const isSupport = (n: TreeNode) => !!n.children && aliasKind(n.name) === ASSETS_KIND;

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

/** An object URL for bytes, revoked when the view goes. */
function useObjectUrl(bytes: Uint8Array | string | null, mime: string): string | null {
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    if (bytes === null || typeof URL === 'undefined' || !URL.createObjectURL) return;
    const u = URL.createObjectURL(new Blob([bytes as BlobPart], { type: mime }));
    setUrl(u);
    return () => URL.revokeObjectURL(u);
  }, [bytes, mime]);
  return url;
}

function FileView({ org, repo, entry, tree }: { org: string; repo: string; entry: TreeEntry; tree: TreeEntry[] }) {
  const env = useEnv();
  const shown = useLoad(env ? () => showFile(env.client, org, repo, entry, tree) : null, [org, repo, entry.sha]);
  const name = entry.path.split('/').pop() ?? entry.path;
  return (
    <section class="panel section" aria-label={name}>
      <div class="a-head"><h2 class="mono">{entry.path}</h2><a class="textlink" href={ghUrl(org, repo, entry.path, 'HEAD')} target="_blank" rel="noopener">On GitHub <Ext /></a></div>
      {shown.kind === 'loading' ? <Loading what={`Reading ${name}`} /> : shown.kind === 'failed' ? <CheckLine cls="bad">{name} could not be read: {shown.error}</CheckLine> : <ShownView shown={shown.value} name={name} />}
    </section>
  );
}

export function ShownView({ shown, name }: { shown: Shown; name: string }) {
  const bytes = shown.kind === 'file' || shown.kind === 'image' ? shown.bytes : shown.kind === 'page' || shown.kind === 'deck' ? shown.file : null;
  const mime = shown.kind === 'file' || shown.kind === 'image' ? shown.mime : 'text/html';
  const url = useObjectUrl(bytes, mime);
  const save = url ? <a class="btn outline small" href={url} download={name}>Download</a> : null;
  switch (shown.kind) {
    case 'too_large':
      return <p class="footnote">This file is over GitHub’s 100 MB limit for reading it this way; open it on GitHub.</p>;
    case 'rendered':
      return <div class="md-body" dangerouslySetInnerHTML={{ __html: shown.html }} />;
    case 'text':
      return <pre class="file-text">{shown.text}</pre>;
    case 'image':
      return <><img class="file-img" src={url ?? ''} alt={name} /><p>{save}</p></>;
    case 'page':
      return (
        <>
          <iframe class="page-frame" title={name} sandbox="" srcdoc={shown.srcdoc} />
          <p class="footnote">{save} {shown.missing ? `${shown.missing} of its files could not be read. ` : ''}Links inside the page do not open here.</p>
        </>
      );
    case 'deck':
      return (
        <div class="stack">
          <p class="actions">
            <button class="btn small" type="button" onClick={() => openDeck(shown.file, name)}>Open the deck</button>
            {save}
          </p>
          <p class="footnote">It opens in a new tab, in the console’s deck viewer, where its scripts run shut off from your account.{shown.missing ? ` ${shown.missing} of its files could not be read.` : ''}</p>
        </div>
      );
    case 'file':
      return (
        <p class="actions">
          {shown.pdf && url ? <a class="btn small" href={url} target="_blank" rel="noopener">Open in a new tab</a> : null}
          {save}
        </p>
      );
  }
}

/** The readings of every session: its reading files (opened here, each with its button row, decision 0035 rule 10), its reading list, or "to come" while they are planned but not out. */
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
