// Materials rendered from the private copy (markdown, notebooks, HTML pages and decks with
// their `_files/` bundle, PDFs, large files) and the Join forms against the seeded issue forms.

import { readFileSync } from 'node:fs';
import { render } from 'preact-render-to-string';
import { describe, expect, it } from 'vitest';
import { parse } from 'yaml';
import { GitHubClient, encodeBase64, type TreeEntry } from '../src/github/client';
import { ASSET_LIMIT, showFile } from '../src/model/materials';
import { CELL_BREAK, htmlRefs, inlineHtml, notebookCells, notebookHtml, resolve, splitRendered } from '../src/model/viewer';
import { AskedList, JoinCourseForm, TeamForm, issueState, joinCourseUrl, joinTeamUrl } from '../src/screens/StudentJoin';
import { MaterialsTree, ShownView, materialHref, splitEntry } from '../src/screens/StudentMaterials';
import { FakeGitHub, fileBody, json } from './fake';

const ORG = 'hertie-dsl-demo-f2026';
const REPO = 'materials';
const text = (v: preact.VNode) => render(v).replace(/<[^>]+>/g, ' ').replace(/&amp;/g, '&').replace(/&rsquo;/g, '’').replace(/\s+/g, ' ');

// A deck laid out as Quarto renders a revealjs presentation: the page, then `<stem>_files/libs/...`.
const DECK = 'lectures/05/deck.html';
const DECK_FILES: Record<string, string> = {
  [DECK]: `<!DOCTYPE html><html><head><meta name="generator" content="quarto-1.5.57">
<link rel="stylesheet" href="deck_files/libs/revealjs/dist/reveal.css">
<link rel="stylesheet" href="https://fonts.example/remote.css">
</head><body><div class="reveal"><div class="slides"><section><h1>Trees</h1><img src="deck_files/figure-revealjs/tree.svg"></section></div></div>
<script src="deck_files/libs/revealjs/dist/reveal.js"></script>
<script>Reveal.initialize({ hash: true });</script></body></html>`,
  'lectures/05/deck_files/libs/revealjs/dist/reveal.css': '.reveal{position:relative}@font-face{font-family:x;src:url(../fonts/x.woff)}',
  'lectures/05/deck_files/libs/revealjs/fonts/x.woff': 'wOFF',
  'lectures/05/deck_files/libs/revealjs/dist/reveal.js': 'window.Reveal={initialize(){document.title="ready"}};',
  'lectures/05/deck_files/figure-revealjs/tree.svg': '<svg xmlns="http://www.w3.org/2000/svg"/>',
  'lectures/05/deck_files/libs/unused.js': 'never read',
  'lectures/01/Session1_demo_deck.html': '<html><head><link rel="stylesheet" href="Session1_demo_deck_files/deck.css"></head><body><img src="Session1_demo_deck_files/figure.svg" alt="A loss curve"></body></html>',
  'lectures/01/Session1_demo_deck_files/deck.css': 'body{font-family:serif}',
  'lectures/01/Session1_demo_deck_files/figure.svg': '<svg xmlns="http://www.w3.org/2000/svg" width="4" height="3"/>',
  'readings/01/READINGS.md': '# Readings\n\n- Goodfellow et al.',
  'labs/01/Lab.ipynb': JSON.stringify({
    cells: [
      { cell_type: 'markdown', source: ['# Lab 1\n', 'Load the data.'] },
      { cell_type: 'code', source: 'import pandas as pd\nprint("<ok>")', outputs: [{ output_type: 'stream', name: 'stdout', text: ['<ok>\n'] }, { output_type: 'display_data', data: { 'image/png': 'iVBORw0KGgo=\n', 'text/plain': '<Figure>' } }] },
      { cell_type: 'markdown', source: 'Done.' },
      { cell_type: 'code', source: '1/0', outputs: [{ output_type: 'error', ename: 'ZeroDivisionError', evalue: 'division by zero', traceback: ['\u001b[31mTraceback\u001b[0m'] }] },
    ],
  }),
  'lectures/03/slides.pdf': '%PDF-1.4',
};

const tree: TreeEntry[] = Object.entries(DECK_FILES).map(([path, t]) => ({ path, mode: '100644', type: 'blob', sha: `sha-${path}`, size: t.length }));

function fake(): FakeGitHub {
  const f = new FakeGitHub();
  for (const [p, t] of Object.entries(DECK_FILES)) f.on('GET', `/repos/${ORG}/${REPO}/contents/${p.split('/').map(encodeURIComponent).join('/')}`, fileBody(p, t));
  // GitHub's markdown endpoint, as far as the tests need: each paragraph wrapped, a # line a heading.
  f.on('POST', '/markdown', (req) => new Response(String((req.body as { text: string }).text).split(/\n{2,}/).map((b) => (b.startsWith('# ') ? `<h1>${b.slice(2).split('\n')[0]}</h1>` : `<p>${b}</p>`)).join('\n'), { status: 200 }));
  return f;
}
const client = (f: FakeGitHub) => new GitHubClient({ token: () => 't', fetch: f.fetch });
const entry = (p: string) => tree.find((t) => t.path === p)!;

describe('HTML bundles', () => {
  it('resolves bundle paths relative to the page and leaves URLs alone', () => {
    expect(resolve('lectures/05/deck.html', 'deck_files/a.css')).toBe('lectures/05/deck_files/a.css');
    expect(resolve('lectures/05/deck_files/libs/revealjs/dist/reveal.css', '../fonts/x.woff')).toBe('lectures/05/deck_files/libs/revealjs/fonts/x.woff');
    expect(resolve('a/b.html', 'https://x.org/y.css')).toBeNull();
    expect(resolve('a/b.html', 'data:image/png;base64,AA')).toBeNull();
    expect(resolve('a/b.html', '#top')).toBeNull();
  });

  it('finds the files a Quarto deck refers to, not the rest of its bundle', () => {
    const avail = new Set(tree.map((t) => t.path));
    expect(htmlRefs(DECK_FILES[DECK], DECK, avail).sort()).toEqual([
      'lectures/05/deck_files/figure-revealjs/tree.svg', 'lectures/05/deck_files/libs/revealjs/dist/reveal.css', 'lectures/05/deck_files/libs/revealjs/dist/reveal.js',
    ]);
  });

  it('inlines the bundle into one file: scripts kept to download, dropped for the in-app view', () => {
    const b = (s: string) => new TextEncoder().encode(s);
    const assets = new Map(Object.entries(DECK_FILES).filter(([p]) => p.includes('deck_files')).map(([p, t]) => [p, b(t)] as const));
    const keep = inlineHtml(DECK_FILES[DECK], DECK, assets, 'keep');
    expect(keep).not.toContain('src="deck_files');
    expect(keep).not.toContain('href="deck_files');
    expect(keep).toContain('<style>.reveal{position:relative}@font-face{font-family:x;src:url("data:font/woff;base64,');
    expect(keep).toContain('<script>window.Reveal={initialize');
    expect(keep).toContain('<script>Reveal.initialize({ hash: true });</script>');
    expect(keep).toContain('src="data:image/svg+xml;base64,');
    expect(keep).toContain('href="https://fonts.example/remote.css"');
    const drop = inlineHtml(DECK_FILES[DECK], DECK, assets, 'drop');
    expect(drop).not.toMatch(/<script/i);
    expect(drop).toContain('<style>.reveal');
  });
});

describe('opening a file', () => {
  it('offers a deck that runs scripts as one self-contained file, reading only the files it uses', async () => {
    const f = fake();
    const s = await showFile(client(f), ORG, REPO, entry(DECK), tree);
    expect(s.kind).toBe('deck');
    if (s.kind !== 'deck') return;
    expect(s.missing).toBe(0);
    expect(s.file).toContain('document.title="ready"');
    expect(f.seen.some((x) => x.url.includes('unused.js'))).toBe(false);
    expect(f.seen.some((x) => x.url.includes('fonts/x.woff'))).toBe(true);
    const out = render(<ShownView shown={s} name="deck.html" />);
    expect(out).toContain('Open the deck');
    expect(out).not.toContain('<iframe');
  });

  it('shows a page with no scripts in a frame sandboxed with no permissions, its images inlined', async () => {
    const s = await showFile(client(fake()), ORG, REPO, entry('lectures/01/Session1_demo_deck.html'), tree);
    expect(s.kind).toBe('page');
    if (s.kind !== 'page') return;
    expect(s.srcdoc).toContain('<style>body{font-family:serif}</style>');
    expect(s.srcdoc).toContain('src="data:image/svg+xml;base64,');
    expect(render(<ShownView shown={s} name="Session1_demo_deck.html" />)).toMatch(/<iframe class="page-frame" title="Session1_demo_deck.html" sandbox(="")? srcdoc="/);
  });

  it('stops reading a bundle at the asset limit and says how many files are missing', async () => {
    const many = Array.from({ length: ASSET_LIMIT + 5 }, (_, i) => `big_files/img${i}.png`);
    const html = `<html><body>${many.map((p) => `<img src="${p}">`).join('')}</body></html>`;
    const t: TreeEntry[] = [{ path: 'big.html', mode: '', type: 'blob', sha: 'h', size: html.length }, ...many.map((p) => ({ path: p, mode: '', type: 'blob' as const, sha: p, size: 3 }))];
    const f = new FakeGitHub().on('GET', /\/contents\/big\.html$/, fileBody('big.html', html)).on('GET', /\/contents\/big_files\//, (r) => json(fileBody(r.url, 'png')));
    const s = await showFile(client(f), ORG, REPO, t[0], t);
    expect(s.kind === 'page' && s.missing).toBe(5);
  });

  it('renders markdown with GitHub’s renderer, and a notebook’s markdown cells in one call', async () => {
    const f = fake();
    const md = await showFile(client(f), ORG, REPO, entry('readings/01/READINGS.md'), tree);
    expect(md).toEqual({ kind: 'rendered', html: '<h1>Readings</h1>\n<p>- Goodfellow et al.</p>' });
    const before = f.seen.filter((x) => x.url.endsWith('/markdown')).length;
    const nb = await showFile(client(f), ORG, REPO, entry('labs/01/Lab.ipynb'), tree);
    expect(f.seen.filter((x) => x.url.endsWith('/markdown')).length - before).toBe(1);
    expect(nb.kind).toBe('rendered');
    const html = nb.kind === 'rendered' ? nb.html : '';
    expect(html).toContain('<div class="nb-md"><h1>Lab 1</h1>');
    expect(html).toContain('<div class="nb-md"><p>Done.</p></div>');
    expect(html).toContain('print(&quot;&lt;ok&gt;&quot;)');
    expect(html).toContain('<pre class="nb-out">&lt;ok&gt;\n</pre>');
    expect(html).toContain('src="data:image/png;base64,iVBORw0KGgo="');
    expect(html).toContain('ZeroDivisionError: division by zero\nTraceback');
    expect(html).not.toContain('\u001b');
  });

  it('falls back to its own markdown when GitHub’s renderer is not reachable', () => {
    const cells = notebookCells(DECK_FILES['labs/01/Lab.ipynb'])!;
    expect(notebookHtml(cells, ['<p>a</p>', '<p>b</p>'])).toContain('<div class="nb-md"><p>b</p></div>');
    expect(splitRendered(`<p>a</p>\n<p>${CELL_BREAK}</p>\n<p>b</p>`, 2)).toEqual(['<p>a</p>\n', '<p>b</p>']);
    expect(splitRendered('<p>a</p>', 2)).toBeNull();
  });

  it('reads a file over 1 MB from the blob API, refuses one over 100 MB, and offers a PDF to open or download', async () => {
    const f = new FakeGitHub().on('GET', '/repos/o/r/git/blobs/bigsha', { content: encodeBase64('%PDF big'), encoding: 'base64' });
    const big: TreeEntry = { path: 'l/big.pdf', mode: '', type: 'blob', sha: 'bigsha', size: 5 * 1024 * 1024 };
    const s = await showFile(client(f), 'o', 'r', big, [big]);
    expect(s).toMatchObject({ kind: 'file', pdf: true, mime: 'application/pdf' });
    expect(new TextDecoder().decode((s as { bytes: Uint8Array }).bytes)).toBe('%PDF big');
    expect(await showFile(client(f), 'o', 'r', { ...big, size: 101 * 1024 * 1024 }, [])).toEqual({ kind: 'too_large' });
    expect(text(<ShownView shown={{ kind: 'too_large' }} name="x" />)).toContain('100 MB');
  });

  it('lists the materials repo as a tree whose files open in the console', () => {
    const out = render(<MaterialsTree org={ORG} trees={[[REPO, tree]]} />);
    expect(out).toContain(`href="${materialHref(ORG, REPO, 'readings/01/READINGS.md')}"`);
    expect(materialHref(ORG, REPO, 'labs/a b&c.ipynb')).toBe(`?semester=${ORG}#materials-materials%2Flabs%2Fa%20b%26c.ipynb`);
    expect(splitEntry('materials/labs/a.ipynb', [REPO])).toEqual({ repo: REPO, path: 'labs/a.ipynb' });
    expect(splitEntry('other/x', [REPO])).toBeNull();
    expect(text(<MaterialsTree org={ORG} trees={[[REPO, null]]} />)).toContain('Nothing has been released yet');
  });
});

describe('Join', () => {
  const form = (name: string) => parse(readFileSync(new URL(`../../templates/welcome/ISSUE_TEMPLATE/${name}`, import.meta.url), 'utf8')) as { labels: string[]; body: { type: string; id?: string }[] };

  it('opens the seeded issue forms, filling in exactly their text inputs by field id', () => {
    const course = new URL(joinCourseUrl(ORG, ' dsl-ab3k9m '));
    expect(course.pathname).toBe(`/${ORG}/welcome/issues/new`);
    expect(course.searchParams.get('template')).toBe('01-join-course.yml');
    expect(form('01-join-course.yml').body.filter((b) => b.type === 'input').map((b) => b.id)).toEqual(['enrol_code']);
    expect(course.searchParams.get('enrol_code')).toBe('dsl-ab3k9m');
    const team = new URL(joinTeamUrl(ORG, 'assignment-3-project', 'team-alpha'));
    expect(team.searchParams.get('template')).toBe('02-join-team.yml');
    const inputs = form('02-join-team.yml').body.filter((b) => b.type === 'input').map((b) => b.id);
    expect(inputs).toEqual(['assignment', 'team']);
    for (const id of inputs) expect(team.searchParams.has(id!)).toBe(true);
    expect(form('02-join-team.yml').labels).toEqual(['team-formation']);
    expect(form('01-join-course.yml').labels).toEqual(['onboarding']);
  });

  it('reads the automation’s answer off the issue’s labels', () => {
    const at = (names: string[], state = 'closed') => issueState({ labels: names.map((name) => ({ name })), state });
    expect(at(['onboarding', 'onboarded'])).toMatchObject({ tone: 'ok', settled: true });
    expect(at(['team-formation', 'team-recorded'])).toMatchObject({ word: 'Your team is recorded', tone: 'ok' });
    expect(at(['team-formation', 'team-refused'])).toMatchObject({ tone: 'bad', settled: true });
    expect(at(['onboarding', 'needs-review'], 'open')).toMatchObject({ tone: 'warn', settled: true });
    expect(at(['onboarding'], 'open')).toMatchObject({ tone: 'busy', settled: false });
  });

  it('lists your requests with the bot’s last reply, its hidden marks removed', () => {
    const issue = { number: 4, title: 'Join team: octo-student', state: 'closed', html_url: 'https://github.com/i/4', created_at: '2026-09-24T10:00:00Z', comments: 1, labels: [{ name: 'team-formation' }, { name: 'team-recorded' }] };
    const t = text(<AskedList asked={[{ issue, reply: { id: 1, body: '<!-- dsl -->Recorded **team-alpha**.', created_at: '', html_url: '' } }]} />);
    expect(t).toContain('Join team: octo-student');
    expect(t).toContain('Your team is recorded');
    expect(t).toMatch(/Recorded team-alpha ?\./);
    expect(text(<AskedList asked={[]} />)).toContain('You have opened no Join course or Join team request here yet.');
  });

  it('offers the team form only for an assignment forming teams, with its close date and size', () => {
    const base = { title: 'Assignment 3 Project', subtitle: 'Group project', handout: null, due: null, lateCutoff: null, lateRule: '', cutoffSentence: '', submitVia: 'assignment_repo' as const, privateRepo: true, submitUrl: '', group: true, solutionShown: null, maxPoints: '', handedOut: true };
    expect(text(<TeamForm org={ORG} assignments={[{ ...base, slug: 'a', teamFormation: null }]} mine={null} />)).toContain('No assignment is forming teams now.');
    const t = text(<TeamForm org={ORG} assignments={[{ ...base, slug: 'assignment-3-project', teamFormation: { closes: '26th Oct', cap: 3 } }]} mine={null} />);
    expect(t).toContain('Teams can form until 26th Oct. At most 3 in a team.');
    expect(render(<JoinCourseForm org={ORG} />)).toContain('disabled');
  });
});
