// Reading one released file for the student's Materials screen, with the student's token:
// the bytes (contents API up to 1 MB, blob API above, nothing past GitHub's 100 MB), then
// the shape the screen shows. Markdown and notebook text is rendered by GitHub's own
// markdown endpoint (one call per file; its output is sanitised by GitHub); an HTML file's
// `_files/` bundle is read file by file, one call each, up to ASSET_LIMIT.

import { ONE_MB, type GitHubClient, type TreeEntry } from '../github/client';
import { md } from './format';
import {
  CELL_BREAK, cssRefs, extOf, hasScripts, htmlRefs, inlineHtml, markdownSources, mimeOf, notebookCells, notebookHtml, splitRendered, utf8, viewKind,
} from './viewer';

/** GitHub's API serves no file above this. */
export const API_CEILING = 100 * ONE_MB;
/** Most bundle files read for one HTML view: a runaway guard on the rate limit. */
export const ASSET_LIMIT = 80;

export type Shown =
  /** Safe HTML to place in the page (markdown, notebooks). */
  | { kind: 'rendered'; html: string }
  /** A static HTML page (no scripts), bundle inlined, for a frame with no permissions; `file` is the same, to download. */
  | { kind: 'page'; srcdoc: string; file: string; missing: number }
  /** An HTML deck that needs its scripts: the self-contained file, for the deck viewer or to download. */
  | { kind: 'deck'; file: string; missing: number }
  | { kind: 'image'; bytes: Uint8Array; mime: string }
  | { kind: 'text'; text: string }
  | { kind: 'file'; bytes: Uint8Array; mime: string; pdf: boolean }
  | { kind: 'too_large' };

const bytesOf = (client: GitHubClient, org: string, repo: string, e: TreeEntry) => client.getBytes(org, repo, e.path, e.sha, e.size ?? 0);

async function rendered(client: GitHubClient, text: string, context: string): Promise<string> {
  try {
    return await client.renderMarkdown(text, context);
  } catch {
    return md(text);
  }
}

/** What to show for `entry`, a blob of `tree` (the repo's recursive tree). */
export async function showFile(client: GitHubClient, org: string, repo: string, entry: TreeEntry, tree: TreeEntry[]): Promise<Shown> {
  if ((entry.size ?? 0) > API_CEILING) return { kind: 'too_large' };
  const kind = viewKind(entry.path);
  const bytes = await bytesOf(client, org, repo, entry);
  const context = `${org}/${repo}`;
  if (kind === 'markdown') return { kind: 'rendered', html: await rendered(client, utf8(bytes), context) };
  if (kind === 'notebook') {
    const cells = notebookCells(utf8(bytes));
    if (!cells) return { kind: 'text', text: utf8(bytes) };
    const sources = markdownSources(cells);
    let parts: string[] | null = null;
    if (sources.length) {
      const all = await client.renderMarkdown(sources.join(`\n\n${CELL_BREAK}\n\n`), context).catch(() => null);
      parts = all === null ? null : splitRendered(all, sources.length);
    }
    return { kind: 'rendered', html: notebookHtml(cells, parts ?? sources.map((s) => md(s))) };
  }
  if (kind === 'html') {
    const html = utf8(bytes);
    const blobs = new Map(tree.filter((t) => t.type === 'blob').map((t) => [t.path, t]));
    const available = new Set(blobs.keys());
    const assets = new Map<string, Uint8Array>();
    const want = htmlRefs(html, entry.path, available);
    const read = async (paths: string[]) => {
      const todo = paths.filter((p) => !assets.has(p)).slice(0, Math.max(0, ASSET_LIMIT - assets.size));
      await Promise.all(todo.map(async (p) => assets.set(p, await bytesOf(client, org, repo, blobs.get(p)!))));
      return todo;
    };
    await read(want);
    // A stylesheet's own images and fonts, one level down.
    const nested = [...assets.entries()].filter(([p]) => extOf(p) === 'css').flatMap(([p, b]) => cssRefs(utf8(b), p, available));
    await read(nested);
    const missing = new Set([...want, ...nested].filter((p) => !assets.has(p))).size;
    const file = inlineHtml(html, entry.path, assets, 'keep');
    if (hasScripts(html)) return { kind: 'deck', file, missing };
    return { kind: 'page', srcdoc: inlineHtml(html, entry.path, assets, 'drop'), file, missing };
  }
  if (kind === 'image') return { kind: 'image', bytes, mime: mimeOf(entry.path) };
  if (kind === 'text') return { kind: 'text', text: utf8(bytes) };
  return { kind: 'file', bytes, mime: mimeOf(entry.path), pdf: kind === 'pdf' };
}
