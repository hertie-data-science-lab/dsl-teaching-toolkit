// @vitest-environment happy-dom
// Handout materials (decision 0026): the topics write behind "Treat as handout materials", the
// Supporting files kind, the syllabus select, the weekly plan's Copy, and the withheld tree's
// look and its line flash in the pattern box.

import { render } from 'preact';
import { act } from 'preact/test-utils';
import { afterEach, describe, expect, it } from 'vitest';
import { EnvCtx, type Env } from '../src/env';
import { GitHubClient, type GhRepo } from '../src/github/client';
import type { Course } from '../src/model/discovery';
import { StaticFiles } from './staticFiles';
import { KIND_LABEL } from '../src/model/format';
import { CONTENT_KINDS, inferKind } from '../src/model/materialsRules';
import { StatusStore } from '../src/model/status';
import type { Adapter, Handle } from '../src/ops/adapter';
import { generateSyllabus } from '../src/ops/defs';
import { targetOf } from '../src/ops/Panel';
import { OpsSession } from '../src/ops/session';
import { MaterialsScreen, changedLine, syllabusChoices } from '../src/screens/CourseEdit';
import { OtherRepoRow } from '../src/screens/CourseIndex';
import type { CourseProps } from '../src/screens/types';
import { PatternTree } from '../src/ui/PatternTree';
import { FakeGitHub, json } from './fake';

const ORG = 'hertie-dsl-demo-course-e1234';
const MAT = 'course-materials-f2026';
const USER = { login: 'a-example', id: 1, name: 'A. Example', email: null, avatar_url: '' };
const course: Course = { org: ORG, name: 'Machine Learning', code: 'E1234', description: '', write: true, admins: [], cohorts: [], meta: null };

function envOf(gh: FakeGitHub, ops?: OpsSession): Env {
  const client = new GitHubClient({ token: () => 't', fetch: gh.fetch });
  return { client, user: USER, ops: ops ?? new OpsSession(new DispatchStub()), statuses: new StatusStore(client), files: new StaticFiles({}), pollMs: 0 };
}

/** An adapter whose every run completes at once with `block` as its outcome's generated text. */
class DispatchStub implements Adapter {
  constructor(private readonly block = '') {}
  async submit(i: { op: string; courseOrg: string; preview: boolean }): Promise<Handle> {
    return { op: i.op, runId: 1, htmlUrl: '', preview: i.preview, courseOrg: i.courseOrg };
  }
  async watch() {
    return { state: 'completed' as const, conclusion: 'success', steps: [], htmlUrl: '' };
  }
  async outcome(h: Handle) {
    return { outcome: { schema: 'dsl.outcome/1' as const, op: h.op, run_id: 1, actor: 'a', preview: h.preview, conclusion: 'previewed' as const, summary: 'Built.', block: this.block } };
  }
  async cancel() {}
}

let root: HTMLElement | null = null;
afterEach(() => {
  if (root) render(null, root);
  root?.remove();
  root = null;
});

async function mount(env: Env | null, ui: preact.VNode) {
  root = document.createElement('div');
  document.body.appendChild(root);
  await act(() => render(<EnvCtx.Provider value={env}>{ui}</EnvCtx.Provider>, root!));
  return root;
}
const button = (text: string) => [...root!.querySelectorAll('button')].find((b) => b.textContent?.trim() === text);
async function settle(until: () => boolean) {
  for (let i = 0; i < 200 && !until(); i++) await act(() => new Promise((r) => setTimeout(r, 0)));
}

describe('Treat as handout materials', () => {
  // The listing carries no topics: the button reads them from GitHub just before it writes.
  const repo: GhRepo = { name: 'lecture-code' } as GhRepo;
  const row = (write = true) => <ul class="rows"><OtherRepoRow org={ORG} repo={repo} write={write} /></ul>;

  it('adds the dsl-materials topic to the repo’s own, then says when it shows', async () => {
    const gh = new FakeGitHub()
      .on('GET', `/repos/${ORG}/lecture-code/topics`, { names: ['python'] })
      .on('PUT', `/repos/${ORG}/lecture-code/topics`, { names: ['python', 'dsl-materials'] });
    await mount(envOf(gh), row());
    await act(() => button('Treat as handout materials')!.click());
    await settle(() => root!.textContent!.includes('Added.'));
    expect(gh.seen.map((s) => s.method)).toEqual(['GET', 'PUT']);
    const put = gh.seen.find((s) => s.method === 'PUT')!;
    expect(put.url).toBe(`https://api.github.com/repos/${ORG}/lecture-code/topics`);
    expect(put.body).toEqual({ names: ['python', 'dsl-materials'] });
    expect(root!.textContent).toContain('Added. It shows under Handout materials after the next Refresh.');
    expect(button('Treat as handout materials')).toBeUndefined();
  });

  it('keeps the button and says why when GitHub refuses', async () => {
    const gh = new FakeGitHub().on('GET', /\/topics$/, { names: [] }).on('PUT', /\/topics$/, () => json({ message: 'Must have admin rights to Repository.' }, 403));
    await mount(envOf(gh), row());
    await act(() => button('Treat as handout materials')!.click());
    await settle(() => !!root!.querySelector('.check-line.bad'));
    expect(root!.querySelector('.check-line.bad')!.textContent).toContain('Could not add the topic');
    expect(button('Treat as handout materials')).toBeDefined();
  });

  it('offers nothing on a course the person cannot change', async () => {
    await mount(envOf(new FakeGitHub()), row(false));
    expect(button('Treat as handout materials')).toBeUndefined();
  });
});

describe('the Supporting files kind', () => {
  it('comes from the policy export, with its names', () => {
    expect(KIND_LABEL.assets).toBe('Supporting files');
    expect(CONTENT_KINDS).toContain('assets');
    for (const f of ['data', 'img', 'images', 'src', 'assets', 'figures', 'fig', 'static']) expect(inferKind(f)).toEqual({ kind: 'assets', named: true });
    // Decision 0031 rule 10: any other name is supporting files by default, not a lecture.
    expect(inferKind('code')).toEqual({ kind: 'assets', named: false });
    expect(inferKind('Lectures')).toEqual({ kind: 'lecture', named: true });
  });
});

const TREE = ['SYLLABUS.md', 'Notes.MD', 'README.md', 'syllabus.pdf', 'lectures/01/a.pdf', 'lectures/01/b.pdf', 'data/x.csv'];
const props = (files: StaticFiles): CourseProps => ({ course, loaded: { kind: 'absent' }, cohortStates: {}, files, now: 0, entry: MAT });

describe('the syllabus panel', () => {
  it('offers the default and the top-level Markdown files, and keeps a current value that is neither', () => {
    expect(syllabusChoices(TREE, 'SYLLABUS.md')).toEqual(['SYLLABUS.md', 'Notes.MD', 'README.md']);
    expect(syllabusChoices(['README.md'], 'syllabus.pdf')).toEqual(['SYLLABUS.md', 'README.md', 'syllabus.pdf']);
  });

  it('is a select, defaulting to SYLLABUS.md, with the weekly plan beneath', async () => {
    const files = new StaticFiles({ [`${ORG}/${MAT}/.releaseignore`]: 'solutions/\n' }, {}, { [`${ORG}/${MAT}`]: TREE });
    await mount(null, <MaterialsScreen {...props(files)} />);
    const sel = root!.querySelector<HTMLSelectElement>('select#m-syl')!;
    expect([...sel.options].map((o) => o.value)).toEqual(['SYLLABUS.md', 'Notes.MD', 'README.md']);
    expect(sel.value).toBe('SYLLABUS.md');
    expect(root!.querySelector('h3')!.textContent).toContain('Weekly plan for the syllabus');
  });
});

describe('the weekly plan', () => {
  const scope = { courseOrg: ORG, cohortOrg: 'hertie-dsl-demo-f2026', where: 'Fall 2026' };

  it('writes into the chosen syllabus and ends with a link to it on GitHub', () => {
    const def = generateSyllabus(scope, MAT, 'docs/E1282 syllabus.md');
    expect(def.args).toEqual({ course_source_repo: MAT, syllabus: 'docs/E1282 syllabus.md' });
    expect(targetOf(def, def.args)).toBe(`https://github.com/${ORG}/${MAT}/blob/HEAD/docs/E1282%20syllabus.md`);
    expect(def.intro).toContain('docs/E1282 syllabus.md');
    expect(def.intro).not.toContain('.system');
  });

  it('holds the text of the last good preview, per op and scope', async () => {
    const ops = new OpsSession(new DispatchStub('### Session 1'), { pollMs: 0, sleep: async () => {} });
    const def = generateSyllabus(scope, MAT, 'SYLLABUS.md');
    expect(ops.lastBlock(def)).toBeNull();
    ops.open(def);
    await ops.start('preview');
    expect(ops.lastBlock(def)).toBe('### Session 1');
    expect(ops.lastBlock(generateSyllabus(scope, 'other-repo', 'SYLLABUS.md'))).toBeNull();
  });
});

describe('the withheld tree', () => {
  it('says which line a click added or changed, and nothing for a removal', () => {
    expect(changedLine(['a/', 'b/'], ['a/', 'b/', 'c/'])).toBe(2);
    expect(changedLine(['a/', 'b/'], ['a/', '!b/x'])).toBe(1);
    expect(changedLine(['a/', 'b/', 'c/'], ['a/', 'c/'])).toBeNull();
    expect(changedLine(['a/'], [])).toBeNull();
    expect(changedLine(['a/'], ['a/'])).toBeNull();
  });

  it('looks like a file tree: icons, indent, withheld rows struck and greyed', async () => {
    await mount(null, <PatternTree files={TREE} patterns={['lectures/01/a.pdf', 'data/']} onChange={() => {}} kinds={{ data: 'Supporting files' }} />);
    const file = (name: string) => [...root!.querySelectorAll('.ft-file')].find((li) => li.querySelector('.ft-name')!.textContent === name)!;
    expect(file('a.pdf').classList.contains('ft-out')).toBe(true);
    expect(file('b.pdf').classList.contains('ft-out')).toBe(false);
    expect(file('b.pdf').querySelector('svg.ft-icon')).not.toBeNull();
    const data = [...root!.querySelectorAll('.ft-dir')].find((li) => li.querySelector('.ft-name')!.textContent === 'data/')!;
    expect(data.classList.contains('ft-out')).toBe(true);
    expect(data.querySelector('summary .ft-closed svg')).not.toBeNull();
    expect(data.querySelector('summary .ft-opened svg')).not.toBeNull();
    expect(data.querySelector('summary .chip')!.textContent).toBe('Supporting files');
  });

  it('sits beside the patterns, and a click flashes the line it wrote', async () => {
    const files = new StaticFiles({ [`${ORG}/${MAT}/.releaseignore`]: 'solutions/\n' }, {}, { [`${ORG}/${MAT}`]: TREE });
    await mount(null, <MaterialsScreen {...props(files)} />);
    const grid = root!.querySelector('.grid-2.withhold')!;
    expect(grid.children[0].querySelector('.file-tree')).not.toBeNull();
    expect(grid.children[1].querySelector('textarea#ign-pat')).not.toBeNull();
    expect(grid.querySelector('.pm-line.flash')).toBeNull();
    await act(() => root!.querySelector<HTMLButtonElement>('button[aria-label="Withhold SYLLABUS.md"]')!.click());
    const box = root!.querySelector<HTMLTextAreaElement>('textarea#ign-pat')!;
    const lines = box.value.split('\n');
    const flashed = [...grid.querySelectorAll('.pm-line')].findIndex((l) => l.classList.contains('flash'));
    expect(lines[flashed]).toBe('/SYLLABUS.md');
    // Typing in the box clears the flash; the tree follows the text.
    await act(() => {
      box.value = 'solutions/\n/SYLLABUS.md\n/README.md';
      box.dispatchEvent(new Event('input', { bubbles: true }));
    });
    expect(grid.querySelector('.pm-line.flash')).toBeNull();
    expect([...root!.querySelectorAll('.ft-file.ft-out .ft-name')].map((n) => n.textContent)).toEqual(expect.arrayContaining(['SYLLABUS.md', 'README.md']));
  });
});
