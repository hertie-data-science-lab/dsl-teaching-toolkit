// The in-app file viewer both consoles use (decision 0035 rules 10-11, decision 0040 rule 6): a
// file from a repo, read with the signed-in person's own token and drawn here (see
// model/materials.ts for how each kind is shown), never gated by any selection or hosted copy.
// A PDF draws inline (materials-release decision 10) in an unsandboxed frame over a blob: URL
// whose type is forced to application/pdf, so nothing but the browser's PDF viewer loads there
// (Chrome refuses that viewer in any sandboxed frame); an HTML page keeps its sandboxed srcdoc.
// The header carries the file's GitHub link and a full-screen toggle (the projector case: the
// file fills the screen; Escape or the button leaves). `Split` keeps a list beside the open
// file: in a left column on wide screens, above the file on narrow ones.

import { useEffect, useRef, useState } from 'preact/hooks';
import { useEnv } from '../env';
import type { TreeEntry } from '../github/client';
import { openDeck } from '../model/deckTab';
import { showFile, type Shown } from '../model/materials';
import { dataUrl } from '../model/viewer';
import { CheckLine, Loading, ghUrl } from './bits';
import { Ext } from './icons';
import { useLoad } from './load';

/**
 * `side` (labelled `label`) in a left column on wide screens, above the file on narrow ones, and
 * the file beside it (decision 0035 rule 11). With `bar` (the Files panel's splitter) the bar sits
 * between them and the columns take the ratio their wrapper sets (`files-split`); `box` is the
 * columns' element, for the bar to measure.
 */
export function Split({ label, side, children, bar, box }: { label: string; side: preact.ComponentChildren; children: preact.ComponentChildren; bar?: preact.ComponentChildren; box?: preact.RefObject<HTMLDivElement> }) {
  return (
    <div class={`materials-split${bar ? ' files-split' : ''}`} ref={box}>
      <nav class="materials-tree" aria-label={label}>{side}</nav>
      {bar}
      <div class="materials-file">{children}</div>
    </div>
  );
}

/** The file `at` from its repo's tree, or a note when the tree has no such file. */
export function OpenFile({ org, at, tree }: { org: string; at: { repo: string; path: string }; tree: TreeEntry[] }) {
  const e = tree.find((t) => t.path === at.path && t.type === 'blob');
  return e ? <FileView org={org} repo={at.repo} entry={e} tree={tree} /> : <p class="footnote">{at.path} is not in {at.repo}.</p>;
}

/** The file `at`, its repo's tree read for it (one recursive read, ETag-cached). */
export function RepoFile({ org, at }: { org: string; at: { repo: string; path: string } }) {
  const env = useEnv();
  const tree = useLoad(env ? () => env.client.listTree(org, at.repo, 'HEAD', true) : null, [org, at.repo, at.path]);
  return tree.kind === 'loading' ? <Loading what="Reading the materials" />
    : tree.kind === 'failed' ? <CheckLine cls="bad">The materials could not be read: {tree.error}</CheckLine>
    : <OpenFile org={org} at={at} tree={tree.value?.tree ?? []} />;
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

/**
 * Full screen for `el`: the panel covers the window (`.viewer-full`) and, where the browser
 * allows and `screen` is on, the screen itself. Escape leaves (the browser's own Escape ends its
 * full screen, which this follows), as does the button (`FullScreenButton`).
 */
export function useFullScreen(el: { current: HTMLElement | null }, screen = true): [boolean, () => void] {
  const [full, setFull] = useState(false);
  useEffect(() => {
    if (!full) return;
    const node = el.current;
    const key = (e: KeyboardEvent) => e.key === 'Escape' && setFull(false);
    const change = () => !document.fullscreenElement && setFull(false);
    document.addEventListener('keydown', key);
    document.addEventListener('fullscreenchange', change);
    if (screen && node?.requestFullscreen && !document.fullscreenElement) node.requestFullscreen().catch(() => undefined);
    return () => {
      document.removeEventListener('keydown', key);
      document.removeEventListener('fullscreenchange', change);
      if (document.fullscreenElement && document.exitFullscreen) document.exitFullscreen().catch(() => undefined);
    };
  }, [full]);
  return [full, () => setFull((f) => !f)];
}

/** The Full screen toggle of the file viewer's header, and of the schedule entry sheet's. */
export function FullScreenButton({ full, toggle }: { full: boolean; toggle: () => void }) {
  return <button class="btn small quiet" type="button" aria-pressed={full} onClick={toggle}>{full ? 'Leave full screen' : 'Full screen'}</button>;
}

export function FileView({ org, repo, entry, tree }: { org: string; repo: string; entry: TreeEntry; tree: TreeEntry[] }) {
  const env = useEnv();
  const shown = useLoad(env ? () => showFile(env.client, org, repo, entry, tree) : null, [org, repo, entry.sha]);
  const panel = useRef<HTMLElement>(null);
  const [full, toggleFull] = useFullScreen(panel);
  const name = entry.path.split('/').pop() ?? entry.path;
  return (
    <section class={`panel section${full ? ' viewer-full' : ''}`} aria-label={name} ref={panel}>
      <div class="a-head">
        <h2 class="mono">{entry.path}</h2>
        <span class="actions">
          <a class="textlink" href={ghUrl(org, repo, entry.path, 'HEAD')} target="_blank" rel="noopener">On GitHub <Ext /></a>
          <FullScreenButton full={full} toggle={toggleFull} />
        </span>
      </div>
      {shown.kind === 'loading' ? <Loading what={`Reading ${name}`} /> : shown.kind === 'failed' ? <CheckLine cls="bad">{name} could not be read: {shown.error}</CheckLine> : <ShownView shown={shown.value} name={name} />}
    </section>
  );
}

export function ShownView({ shown, name }: { shown: Shown; name: string }) {
  const bytes = shown.kind === 'file' || shown.kind === 'image' ? shown.bytes : shown.kind === 'page' || shown.kind === 'deck' ? shown.file : null;
  // A PDF's Blob type is forced, never the file's own: its frame is unsandboxed.
  const mime = shown.kind === 'file' && shown.pdf ? 'application/pdf' : shown.kind === 'file' || shown.kind === 'image' ? shown.mime : 'text/html';
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
      // A data: URL, as instructor pictures are: the page's CSP allows no blob: image.
      return <><img class="file-img" src={dataUrl(name, shown.bytes)} alt={name} /><p>{save}</p></>;
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
      if (shown.pdf && url) {
        return (
          <>
            <iframe class="page-frame pdf-frame" src={url} title={name} />
            <p class="footnote actions"><a class="btn small" href={url} target="_blank" rel="noopener">Open in a new tab</a> {save}</p>
          </>
        );
      }
      return <p class="actions">{save}</p>;
  }
}
