// @vitest-environment happy-dom
// The file row and viewer on the instructor side (decision 0040 rule 6): the course materials
// tree's files open in the console's viewer, their repo's tree beside them, each row with the
// shared menu; the viewer's header has the file's GitHub link and a full-screen toggle that
// Escape leaves. A PDF draws inline in an unsandboxed blob: frame whose Blob type is forced to
// application/pdf (materials-release decision 10); an HTML page keeps its sandboxed frame.

import { render } from 'preact';
import { act } from 'preact/test-utils';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { EnvCtx, type Env } from '../src/env';
import { GitHubClient } from '../src/github/client';
import type { Course } from '../src/model/discovery';
import type { Loaded } from '../src/model/status';
import type { Status } from '../src/model/types';
import { parseSearch } from '../src/router';
import { MaterialsIndexScreen, fileIn } from '../src/screens/CourseIndex';
import type { CourseProps } from '../src/screens/types';
import { MaterialsTree, fileViewHref } from '../src/ui/MaterialsTree';
import { ShownView } from '../src/ui/Viewer';
import { FakeGitHub, fileBody } from './fake';
import example from './fixtures/status.example.json';
import { StaticFiles } from './staticFiles';

const ORG = 'hertie-dsl-demo-course-e1234';
const COHORT = 'hertie-dsl-demo-f2026';
const MAT = 'course-materials-f2026';
const PATHS = ['SYLLABUS.md', 'lectures/01/notes.md', 'lectures/01/slides.pdf', 'labs/lab.ipynb'];
const blob = (path: string) => ({ path, mode: '100644', type: 'blob', sha: `sha-${path}`, size: 10 });
const course: Course = { org: ORG, name: 'Machine Learning', code: 'E1234', description: '', write: true, admins: [], cohorts: [{ org: COHORT, term: 'f2026', termLabel: 'Fall 2026' }], meta: null };
const ready: Loaded = { kind: 'ready', status: example as unknown as Status, sha: 's', stale: [] };
const files = new StaticFiles({}, {}, { [`${ORG}/${MAT}`]: PATHS });
const cp = (file?: string): CourseProps => ({ course, loaded: { kind: 'absent' }, cohortStates: { [COHORT]: ready }, files, now: Date.parse('2026-09-23T10:00:00Z'), file });

let root: HTMLElement | null = null;
beforeEach(() => sessionStorage.clear());
afterEach(() => {
  if (root) render(null, root);
  root?.remove();
  root = null;
});

async function mount(view: preact.VNode) {
  const gh = new FakeGitHub()
    .on('GET', `/repos/${ORG}/${MAT}/git/trees/HEAD?recursive=1`, { sha: 't', truncated: false, tree: PATHS.map(blob) })
    .on('GET', `/repos/${ORG}/${MAT}/contents/lectures/01/notes.md`, fileBody('lectures/01/notes.md', '# Notes'))
    .on('POST', '/markdown', () => new Response('<h1>Notes</h1>', { status: 200 }));
  const env = { user: { login: 'prof', id: 1, name: 'P', email: null, avatar_url: '' }, client: new GitHubClient({ token: () => 't', fetch: gh.fetch }) } as unknown as Env;
  root = document.createElement('div');
  document.body.appendChild(root);
  await act(() => render(<EnvCtx.Provider value={env}>{view}</EnvCtx.Provider>, root!));
  for (let i = 0; i < 6; i++) await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  return root;
}

describe('the course materials tree', () => {
  it('opens a file in the console, with the shared menu after its name', async () => {
    const el = await mount(<MaterialsTree org={ORG} repo={MAT} paths={PATHS.map((path) => ({ path, dir: false }))} />);
    const li = [...el.querySelectorAll('.ft-file')].find((x) => x.querySelector('.ft-name')!.textContent === 'SYLLABUS.md')!;
    const name = li.querySelector<HTMLAnchorElement>('a.ft-name')!;
    expect(name.getAttribute('href')).toBe(fileViewHref(ORG, MAT, 'SYLLABUS.md'));
    expect(name.getAttribute('target')).toBeNull();
    const items = [...li.querySelectorAll('.open-menu [role="menuitem"]')];
    expect(items.map((a) => a.textContent)).toEqual(['In the console', 'On GitHub', 'On github.dev', 'Set up your editor in Profile']);
    expect(items[0].getAttribute('href')).toBe(fileViewHref(ORG, MAT, 'SYLLABUS.md'));
    expect(items[1].getAttribute('href')).toBe(`https://github.com/${ORG}/${MAT}/blob/HEAD/SYLLABUS.md`);
    // The editor row points at the course's Profile, not a semester's.
    expect(items[3].getAttribute('href')).toBe(`?course=${ORG}#profile`);
  });

  it('marks the open file and opens the folders above it', async () => {
    const el = await mount(<MaterialsTree org={ORG} repo={MAT} paths={PATHS.map((path) => ({ path, dir: false }))} current="lectures/01/notes.md" />);
    const marked = [...el.querySelectorAll('a[aria-current]')];
    expect(marked.map((a) => a.textContent)).toEqual(['notes.md']);
    expect(marked[0].closest('li')!.classList.contains('current')).toBe(true);
    const folder = (p: string) => el.querySelector(`li[data-folder="${p}"] > details`) as HTMLDetailsElement;
    expect(folder('lectures').open).toBe(true);
    expect(folder('lectures/01').open).toBe(true);
    expect(folder('labs').open).toBe(false);
  });

  it('round-trips the file link through the query string to the repo it names', () => {
    const q = parseSearch(fileViewHref(ORG, MAT, 'lectures/01/notes.md').split('#')[0]);
    expect(fileIn(q.file, [MAT])).toEqual({ repo: MAT, path: 'lectures/01/notes.md' });
    expect(fileIn(q.file, ['other-repo'])).toBeNull();
    expect(fileIn(`${MAT}/`, [MAT])).toBeNull();
  });
});

describe('the course materials index with a file open', () => {
  it('shows the file in the viewer beside its repo’s tree', async () => {
    const el = await mount(<MaterialsIndexScreen {...cp(`${MAT}/lectures/01/notes.md`)} />);
    const split = el.querySelector('.materials-split')!;
    expect(split).not.toBeNull();
    expect(split.querySelector('.materials-tree a[aria-current]')!.textContent).toBe('notes.md');
    const file = split.querySelector('.materials-file')!;
    expect(file.querySelector('h2')!.textContent).toBe('lectures/01/notes.md');
    expect(file.querySelector('.md-body')!.innerHTML).toContain('Notes');
    expect(file.querySelector('a.textlink')!.getAttribute('href')).toBe(`https://github.com/${ORG}/${MAT}/blob/HEAD/lectures/01/notes.md`);
    expect(el.querySelector(`a[href="?course=${ORG}#materials"]`)).not.toBeNull();
  });

  it('shows the index for a file in no materials repo', async () => {
    const el = await mount(<MaterialsIndexScreen {...cp('elsewhere/x.md')} />);
    expect(el.querySelector('.materials-split')).toBeNull();
    expect(el.textContent).toContain('Course materials repos');
  });
});

describe('the viewer’s full screen', () => {
  it('toggles from the header, and Escape leaves it', async () => {
    const el = await mount(<MaterialsIndexScreen {...cp(`${MAT}/lectures/01/notes.md`)} />);
    const panel = () => el.querySelector('.materials-file > section')!;
    const button = () => [...panel().querySelectorAll('button')].find((b) => /full screen/i.test(b.textContent!))!;
    expect(button().textContent).toBe('Full screen');
    expect(panel().classList.contains('viewer-full')).toBe(false);
    await act(() => button().click());
    expect(panel().classList.contains('viewer-full')).toBe(true);
    expect(button().getAttribute('aria-pressed')).toBe('true');
    expect(button().textContent).toBe('Leave full screen');
    await act(() => { document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' })); });
    expect(panel().classList.contains('viewer-full')).toBe(false);
    await act(() => button().click());
    await act(() => button().click());
    expect(panel().classList.contains('viewer-full')).toBe(false);
  });
});

describe('the viewer’s PDF frame', () => {
  // Detached from the document, so happy-dom does not try to load the frames.
  const view = async (v: preact.VNode) => {
    root = document.createElement('div');
    await act(() => render(v, root!));
    for (let i = 0; i < 2; i++) await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
    return root;
  };
  it('draws a PDF in a frame over a blob: URL, unsandboxed, its Blob type forced to application/pdf', async () => {
    const made: Blob[] = [];
    const create = vi.spyOn(URL, 'createObjectURL').mockImplementation((b) => (made.push(b as Blob), 'blob:console/pdf-1'));
    const revoke = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => undefined);
    try {
      // The file's own mime is ignored: only the browser's PDF viewer may load unsandboxed.
      const el = await view(<ShownView shown={{ kind: 'file', bytes: new Uint8Array([37, 80, 68, 70]), mime: 'text/html', pdf: true }} name="slides.pdf" />);
      const frame = el.querySelector('iframe')!;
      expect(frame.className).toBe('page-frame pdf-frame');
      expect(frame.getAttribute('src')).toBe('blob:console/pdf-1');
      expect(frame.getAttribute('title')).toBe('slides.pdf');
      expect(frame.hasAttribute('sandbox')).toBe(false);
      expect(made.map((b) => b.type)).toEqual(['application/pdf']);
      const links = [...el.querySelectorAll('.footnote a')].map((a) => a.textContent);
      expect(links).toEqual(['Open in a new tab', 'Download']);
    } finally {
      create.mockRestore();
      revoke.mockRestore();
    }
  });

  it('keeps an HTML page in its sandboxed srcdoc frame, and gives any other file no frame', async () => {
    const page = await view(<ShownView shown={{ kind: 'page', srcdoc: '<p>Hi</p>', file: '<p>Hi</p>', missing: 0 }} name="page.html" />);
    expect(page.querySelector('iframe')!.getAttribute('sandbox')).toBe('');
    expect(page.querySelector('iframe')!.hasAttribute('src')).toBe(false);
    render(null, root!);
    root!.remove();
    const other = await view(<ShownView shown={{ kind: 'file', bytes: new Uint8Array([1]), mime: 'application/zip', pdf: false }} name="data.zip" />);
    expect(other.querySelector('iframe')).toBeNull();
  });
});
