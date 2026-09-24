// Materials for a student: the released `materials` repo as a tree (one recursive tree read),
// and a file opened in the console from the private copy with the student's own token.
// Nothing is published anywhere; see model/materials.ts for how each kind is shown.

import { useEffect, useState } from 'preact/hooks';
import { useEnv } from '../env';
import type { TreeEntry } from '../github/client';
import { buildTree, type TreeNode } from '../edit/badges';
import { openDeck } from '../model/deckTab';
import { showFile, type Shown } from '../model/materials';
import { studentHref } from '../router';
import { CheckLine, Loading } from '../ui/bits';
import { Ext } from '../ui/icons';
import { useLoad } from '../ui/load';

/** The route entry for a file: `<repo>/<path>`. */
export const materialHref = (org: string, repo: string, path: string) => `${studentHref(org, 'materials')}-${encodeURIComponent(`${repo}/${path}`)}`;

/** `<repo>/<path>` back into its parts, for a repo among `repos`. */
export function splitEntry(entry: string, repos: string[]): { repo: string; path: string } | null {
  const i = entry.indexOf('/');
  if (i < 0) return null;
  const repo = entry.slice(0, i);
  return repos.includes(repo) ? { repo, path: entry.slice(i + 1) } : null;
}

const ghBlob = (org: string, repo: string, path: string) => `https://github.com/${org}/${repo}/blob/HEAD/${path.split('/').map(encodeURIComponent).join('/')}`;

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
      <div class="stack">
        <p><a class="textlink" href={studentHref(org, 'materials')}>All materials</a></p>
        {e ? <FileView org={org} repo={at.repo} entry={e} tree={tree} /> : <p class="footnote">{at.path} is not in {at.repo}.</p>}
      </div>
    );
  }
  return <MaterialsTree org={org} trees={trees.value} />;
}

export function MaterialsTree({ org, trees }: { org: string; trees: (readonly [string, TreeEntry[] | null])[] }) {
  const shown = trees.filter(([, t]) => t !== null);
  if (!shown.length) return <p class="footnote">Nothing has been released yet, or you cannot read the materials: that needs the semester’s student team, which joining gives you.</p>;
  return (
    <div class="stack">
      {shown.map(([repo, tree]) => {
        const files = tree!.filter((t) => t.type === 'blob').map((t) => t.path);
        const node = (n: TreeNode, depth: number): preact.JSX.Element =>
          n.children ? (
            <li class="ft-dir"><details open={depth === 0 && !n.name.endsWith('_files')}><summary><span class="ft-name">{n.name}/</span></summary><ul>{n.children.map((c) => node(c, depth + 1))}</ul></details></li>
          ) : (
            <li class="ft-file"><a class="ft-name" href={materialHref(org, repo, n.path)}>{n.name}</a></li>
          );
        return (
          <section class="panel section" aria-label={repo}>
            <h2>{repo}</h2>
            {files.length ? <ul class="file-tree">{buildTree(files).map((n) => node(n, 0))}</ul> : <p class="footnote">Nothing released yet.</p>}
          </section>
        );
      })}
      <p class="footnote">Every file opens here, read with your own account: nothing is published.</p>
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
      <div class="a-head"><h2 class="mono">{entry.path}</h2><a class="textlink" href={ghBlob(org, repo, entry.path)} target="_blank" rel="noopener">On GitHub <Ext /></a></div>
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
