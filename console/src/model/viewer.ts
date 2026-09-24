// Showing a released file from a private materials repo inside the console, with no public
// copy: pure functions over bytes and text, so they run (and are tested) without a browser.
// Imports nothing, so a plain `node` can load this file for the CSP check in headless Chrome.
//
// HTML (decks included): the page's policy (script-src 'self', frame-src 'none', no
// 'unsafe-eval') is inherited by every document the console makes, whether an about:srcdoc
// frame, a blob: frame (with or without a `csp` attribute) or a blob: tab: checked in
// headless Chrome, no deck script runs in any of them. So a deck is shown in-app as a static
// page (its `_files/` bundle inlined: stylesheets as <style>, images as data: URLs, which
// the policy allows) in a frame sandboxed with no permissions, and a deck that needs its
// scripts (reveal.js, Quarto) is offered as ONE self-contained file to download: every
// bundle file inlined, scripts included, which opens from disk and runs.

export type ViewKind = 'markdown' | 'notebook' | 'html' | 'pdf' | 'image' | 'text' | 'other';

const EXT: Record<string, ViewKind> = {
  md: 'markdown', markdown: 'markdown', ipynb: 'notebook', html: 'html', htm: 'html', pdf: 'pdf',
  png: 'image', jpg: 'image', jpeg: 'image', gif: 'image', svg: 'image', webp: 'image',
  txt: 'text', py: 'text', r: 'text', csv: 'text', json: 'text', yml: 'text', yaml: 'text', bib: 'text', tex: 'text', qmd: 'text', rmd: 'text', sql: 'text', sh: 'text',
};

export const extOf = (path: string) => (/\.([^./]+)$/.exec(path)?.[1] ?? '').toLowerCase();
export const viewKind = (path: string): ViewKind => EXT[extOf(path)] ?? 'other';

const MIME: Record<string, string> = {
  png: 'image/png', jpg: 'image/jpeg', jpeg: 'image/jpeg', gif: 'image/gif', svg: 'image/svg+xml', webp: 'image/webp',
  css: 'text/css', js: 'text/javascript', mjs: 'text/javascript', json: 'application/json', pdf: 'application/pdf', html: 'text/html',
  woff: 'font/woff', woff2: 'font/woff2', ttf: 'font/ttf', otf: 'font/otf', eot: 'application/vnd.ms-fontobject',
  mp4: 'video/mp4', webm: 'video/webm', ipynb: 'application/x-ipynb+json',
};
export const mimeOf = (path: string) => MIME[extOf(path)] ?? 'application/octet-stream';

export function esc(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

export function toBase64(bytes: Uint8Array): string {
  let bin = '';
  for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(bin);
}

export const utf8 = (bytes: Uint8Array) => new TextDecoder().decode(bytes);

export const dataUrl = (path: string, bytes: Uint8Array) => `data:${mimeOf(path)};base64,${toBase64(bytes)}`;

// --------------------------------------------------------------------------- HTML bundles

/** `ref` (as written in `from`) as a repo path, or null for anything not in the repo (a URL, a data: URI, an anchor). */
export function resolve(from: string, ref: string): string | null {
  if (!ref || /^(?:[a-z][a-z0-9+.-]*:|\/\/|#)/i.test(ref) || ref.startsWith('/')) return null;
  const clean = decodeURIComponent(ref.split(/[?#]/)[0]);
  const parts = from.split('/').slice(0, -1);
  for (const seg of clean.split('/')) {
    if (seg === '..') parts.pop();
    else if (seg && seg !== '.') parts.push(seg);
  }
  return parts.join('/');
}

const ATTR = /(<(?:img|script|link|source|video|audio|image|use|input)\b[^>]*?\s(?:src|href|xlink:href|poster))\s*=\s*(["'])([^"']*)\2/gi;
const CSS_URL = /url\(\s*(["']?)([^"')]+)\1\s*\)/gi;

/** The repo paths an HTML file refers to (stylesheets, scripts, images), limited to those in `available`. */
export function htmlRefs(html: string, htmlPath: string, available: Set<string>): string[] {
  const out = new Set<string>();
  for (const m of html.matchAll(ATTR)) {
    const p = resolve(htmlPath, m[3]);
    if (p && available.has(p)) out.add(p);
  }
  for (const m of html.matchAll(CSS_URL)) {
    const p = resolve(htmlPath, m[2]);
    if (p && available.has(p)) out.add(p);
  }
  return [...out];
}

/** The repo paths a stylesheet refers to with url(...) (images, fonts). */
export function cssRefs(css: string, cssPath: string, available: Set<string>): string[] {
  const out = new Set<string>();
  for (const m of css.matchAll(CSS_URL)) {
    const p = resolve(cssPath, m[2]);
    if (p && available.has(p)) out.add(p);
  }
  return [...out];
}

function inlineCss(css: string, cssPath: string, assets: Map<string, Uint8Array>): string {
  return css.replace(CSS_URL, (all, _q: string, ref: string) => {
    const p = resolve(cssPath, ref);
    const b = p ? assets.get(p) : undefined;
    return b && p ? `url("${dataUrl(p, b)}")` : all;
  });
}

export const hasScripts = (html: string) => /<script\b/i.test(html);

/**
 * One self-contained document from an HTML file and the bundle files it refers to. `scripts`:
 * `keep` inlines them too (the file to download), `drop` removes every script (the static
 * in-app view, where the console's policy would block them anyway).
 */
export function inlineHtml(html: string, htmlPath: string, assets: Map<string, Uint8Array>, scripts: 'keep' | 'drop'): string {
  let out = html;
  if (scripts === 'drop') out = out.replace(/<script\b[\s\S]*?<\/script\s*>/gi, '').replace(/<script\b[^>]*\/>/gi, '');
  // Stylesheets become <style> blocks (with their own url(...)s inlined).
  out = out.replace(/<link\b[^>]*>/gi, (tag) => {
    if (!/rel\s*=\s*["']?stylesheet/i.test(tag)) return tag;
    const href = /href\s*=\s*(["'])([^"']*)\1/i.exec(tag)?.[2] ?? '';
    const p = resolve(htmlPath, href);
    const b = p ? assets.get(p) : undefined;
    return b && p ? `<style>${inlineCss(utf8(b), p, assets).replace(/<\/style/gi, '<\\/style')}</style>` : tag;
  });
  if (scripts === 'keep') {
    out = out.replace(/<script\b([^>]*?)\ssrc\s*=\s*(["'])([^"']*)\2([^>]*)>\s*<\/script\s*>/gi, (tag, pre: string, _q: string, src: string, post: string) => {
      const p = resolve(htmlPath, src);
      const b = p ? assets.get(p) : undefined;
      return b ? `<script${pre}${post}>${utf8(b).replace(/<\/script/gi, '<\\/script')}</script>` : tag;
    });
  }
  out = out.replace(ATTR, (all, head: string, q: string, ref: string) => {
    const p = resolve(htmlPath, ref);
    const b = p ? assets.get(p) : undefined;
    return b && p ? `${head}=${q}${dataUrl(p, b)}${q}` : all;
  });
  return inlineCss(out, htmlPath, assets);
}

// --------------------------------------------------------------------------- notebooks

interface Cell {
  cell_type: string;
  source?: string | string[];
  outputs?: Output[];
}
interface Output {
  output_type: string;
  text?: string | string[];
  name?: string;
  data?: Record<string, string | string[]>;
  ename?: string;
  evalue?: string;
  traceback?: string[];
}

const joined = (v: string | string[] | undefined) => (Array.isArray(v) ? v.join('') : (v ?? ''));
const noAnsi = (s: string) => s.replace(/\u001b\[[0-9;]*[A-Za-z]/g, '');

/** A notebook's cells, or null when the text is not a notebook. */
export function notebookCells(text: string): Cell[] | null {
  try {
    const nb = JSON.parse(text) as { cells?: Cell[] };
    return Array.isArray(nb.cells) ? nb.cells : null;
  } catch {
    return null;
  }
}

/** The markdown cells' sources, in order (rendered in one call and passed back to notebookHtml). */
export const markdownSources = (cells: Cell[]) => cells.filter((c) => c.cell_type === 'markdown').map((c) => joined(c.source));

function outputHtml(o: Output): string {
  if (o.output_type === 'stream') return `<pre class="nb-out${o.name === 'stderr' ? ' err' : ''}">${esc(noAnsi(joined(o.text)))}</pre>`;
  if (o.output_type === 'error') return `<pre class="nb-out err">${esc(noAnsi([`${o.ename}: ${o.evalue}`, ...(o.traceback ?? [])].join('\n')))}</pre>`;
  const d = o.data ?? {};
  for (const t of ['image/png', 'image/jpeg', 'image/gif']) if (d[t]) return `<img class="nb-img" alt="" src="data:${t};base64,${joined(d[t]).replace(/[^A-Za-z0-9+/=]/g, '')}">`;
  if (d['image/svg+xml']) return `<img class="nb-img" alt="" src="data:image/svg+xml;base64,${btoa(unescape(encodeURIComponent(joined(d['image/svg+xml']))))}">`;
  if (d['text/plain']) return `<pre class="nb-out">${esc(noAnsi(joined(d['text/plain'])))}</pre>`;
  if (d['text/html']) return '<p class="footnote">(An HTML output, not shown here.)</p>';
  return '';
}

/**
 * The notebook as HTML: markdown cells from `rendered` (one entry per markdown cell, already
 * safe HTML), code cells as escaped source and their outputs (text, errors, images).
 */
export function notebookHtml(cells: Cell[], rendered: string[]): string {
  let m = 0;
  return cells.map((c) => {
    if (c.cell_type === 'markdown') return `<div class="nb-md">${rendered[m++] ?? ''}</div>`;
    const src = `<pre class="nb-src"><code>${esc(joined(c.source))}</code></pre>`;
    if (c.cell_type !== 'code') return src;
    return `<div class="nb-code">${src}${(c.outputs ?? []).map(outputHtml).join('')}</div>`;
  }).join('\n');
}

/** A sentinel paragraph between markdown cells, so every cell renders in one call and splits back apart. */
export const CELL_BREAK = 'DSLNBCELLBREAK';
export const splitRendered = (html: string, n: number): string[] | null => {
  const parts = html.split(new RegExp(`<p[^>]*>${CELL_BREAK}</p>\\s*`));
  return parts.length === n ? parts : null;
};
