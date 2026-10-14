// @vitest-environment happy-dom
// Folder kinds at any depth: the materials settings' kind picker rides the withheld tree's own
// component, detects kind folders one level down, writes `kinds:` paths, and lists a content
// kind no folder has as a suggested row, never an error.

import { render } from 'preact';
import { render as html } from 'preact-render-to-string';
import { act } from 'preact/test-utils';
import { afterEach, describe, expect, it } from 'vitest';
import { EnvCtx, type Env } from '../src/env';
import { GitHubClient, decodeBase64 } from '../src/github/client';
import type { Course } from '../src/model/discovery';
import { StatusStore, type Loaded } from '../src/model/status';
import type { MaterialsCheck, Status } from '../src/model/types';
import { DispatchAdapter } from '../src/ops/adapter';
import { OpsSession } from '../src/ops/session';
import { MaterialsScreen, kindFolderPaths, writeHolds } from '../src/screens/CourseEdit';
import example from './fixtures/status.example.json';
import { FakeGitHub, json } from './fake';
import { StaticFiles } from './staticFiles';

const ORG = 'hertie-dsl-demo-course-e1234';
const MAT = 'course-materials-f2026';
const USER = { login: 'a-example', id: 1, name: 'A. Example', email: null, avatar_url: '' };
const course: Course = { org: ORG, name: 'Machine Learning', code: 'E1234', description: '', write: true, admins: [], cohorts: [], meta: {} };
// Shaped like the Maths course: kinds one level down, solutions denied, a declared deep folder.
const TREE = [
  'SYLLABUS.md',
  'students/lectures/01/slides.pdf',
  'students/labs/01/lab.ipynb',
  'students/notes.md',
  'solutions/labs/01/lab.ipynb',
  'readings/week1/paper.pdf',
  'm4ds/chapter1/notes.md',
  'course/2026/labs/extra.md',
];
const DIRS = [...new Set(TREE.flatMap((f) => f.split('/').slice(0, -1).map((_, i, a) => a.slice(0, i + 1).join('/'))))];
const STATUS = example as unknown as Status;

/** The example status with this repo's checks replaced. */
function loadedWith(checks: MaterialsCheck[]): Loaded {
  const materials = [{ repo: MAT, state: 'ready', checks }];
  return { kind: 'ready', status: { ...STATUS, course: { ...STATUS.course!, materials } }, sha: 's', stale: [] };
}
const DONE: MaterialsCheck = { id: 'kind_folder', label: 'At least one folder of a content kind', done: true, why: null, need: 'needed' };
const NO_READINGS: MaterialsCheck = { id: 'kind_folder:readings', label: 'No readings folder found', done: false, why: 'Pick the folder that holds readings under Folder kinds, or leave it if this course has none.', need: 'suggested' };

const files = (yml?: string) => new StaticFiles(yml ? { [`${ORG}/${MAT}/materials.yml`]: yml } : {}, {}, { [`${ORG}/${MAT}`]: TREE });
const screen = (f: StaticFiles, l: Loaded = loadedWith([DONE])) => <MaterialsScreen course={course} entry={MAT} loaded={l} cohortStates={{}} files={f} now={0} />;
const select = (out: string, path: string) => out.split(`aria-label="Kind of ${path}">`)[1]?.split('</select>')[0];

describe('the Folder kinds tree', () => {
  it('detects kind folders at the top and one level down, never deeper unless declared', () => {
    expect(kindFolderPaths(DIRS, {}).sort()).toEqual(['course', 'm4ds', 'readings', 'students', 'students/labs', 'students/lectures']);
    // A declared path counts at any depth; a folder inside a kind folder is part of it.
    expect(kindFolderPaths(DIRS, { 'course/2026/labs': 'lab' })).toContain('course/2026/labs');
    expect(kindFolderPaths([...DIRS, 'readings/labs'], {})).not.toContain('readings/labs');
  });

  it('never looks inside a top folder the .releaseignore withholds, as the engine does not', () => {
    // `kind_folders` descends only RELEASED top folders, so a withheld folder's child gets no chip.
    const out = kindFolderPaths(DIRS, { 'course/2026/labs': 'lab' }, ['# withheld', 'students/', 'course']);
    expect(out).not.toContain('students/labs');
    expect(out).not.toContain('students/lectures');
    expect(out).not.toContain('course/2026/labs');
    // The top folder itself is still listed (the engine's `folders` keeps it); a released one is looked inside.
    expect(out).toContain('students');
    expect(kindFolderPaths(DIRS, {}, ['readings/'])).toContain('students/labs');
  });

  it('renders nested folders through the withheld tree’s component, each with a picker, detected ones pre-selected', () => {
    const out = html(screen(files()));
    // Both settings are the one file-tree component.
    expect(out.match(/<ul class="file-tree">/g)?.length).toBe(2);
    expect(select(out, 'students/lectures').startsWith('<option value selected>Lecture (from its name)</option>')).toBe(true);
    expect(select(out, 'students/labs').startsWith('<option value selected>Lab (from its name)</option>')).toBe(true);
    expect(select(out, 'readings').startsWith('<option value selected>Readings (from its name)</option>')).toBe(true);
    // Deeper than one level, a folder is part of the one above until a kind is picked for it.
    expect(select(out, 'course/2026/labs').startsWith('<option value selected>Part of course/2026/</option>')).toBe(true);
    expect(select(out, 'course/2026/labs')).toContain('<option value="lab">Lab</option>');
    // Every picker offers supporting files, the "none" a declaration can say.
    expect(select(out, 'readings')).toContain('<option value="assets">Supporting files</option>');
    // Never released, never a kind.
    expect(out).not.toContain('aria-label="Kind of solutions');
    // The withheld tree chips nested kind folders too.
    expect(out).toContain('<span class="ft-name">lectures/</span><span class="chip">Lecture</span>');
  });

  it('shows a declaration as set here, told apart from a detected default', () => {
    const out = html(screen(files('kinds:\n  students/labs: readings\n  readings: assets\n')));
    expect(select(out, 'students/labs')).toContain('<option value="readings" selected>Readings</option>');
    expect(select(out, 'students/labs')).not.toContain('<option value selected>');
    expect(select(out, 'readings')).toContain('<option value="assets" selected>Supporting files</option>');
    expect(out).toMatch(/<span class="ft-name">labs\/<\/span><span class="footnote">set here<\/span><select class="ft-kind"/);
    expect(out).not.toMatch(/<span class="ft-name">lectures\/<\/span><span class="footnote">set here/);
  });

  it('lists a content kind no folder has as a suggested row under the tree, and nothing when all are found', () => {
    const out = html(screen(files(), loadedWith([DONE, NO_READINGS])));
    const kinds = out.split('<h2>Folder kinds')[1].split('<h2>')[0];
    expect(kinds).toMatch(/<ul class="setup"><li class="open suggested">.*No readings folder found.*Pick the folder that holds readings under Folder kinds/);
    expect(out).not.toContain('class="s-mark problem"');
    expect(html(screen(files())).split('<h2>Folder kinds')[1].split('<h2>')[0]).not.toContain('No readings folder found');
  });

  it('writes kinds: with folder paths', () => {
    expect(writeHolds(null, { syllabus: '', kinds: { 'students/lectures': 'lecture', 'course/2026/labs': 'lab' } })).toContain('kinds:\n  students/lectures: lecture\n  course/2026/labs: lab\n');
  });
});

let root: HTMLElement | null = null;
afterEach(() => {
  if (root) render(null, root);
  root?.remove();
  root = null;
});

describe('saving Folder kinds', () => {
  it('writes the picked folder’s path, and only what was picked', async () => {
    const gh = new FakeGitHub().on('PUT', /\/contents\//, () => json({ content: { sha: 'b1' }, commit: { sha: 'c1' } })).on('GET', /\/check-runs/, { check_runs: [] });
    const f = files();
    const client = new GitHubClient({ token: () => 't', fetch: gh.fetch });
    const env: Env = { client, user: USER, ops: new OpsSession(new DispatchAdapter(client, () => USER.login)), statuses: new StatusStore(client), files: f, pollMs: 0 };
    root = document.body.appendChild(document.createElement('div'));
    await act(() => render(<EnvCtx.Provider value={env}>{screen(f)}</EnvCtx.Provider>, root!));
    const sel = root.querySelector<HTMLSelectElement>('select[aria-label="Kind of course/2026/labs"]')!;
    await act(() => {
      sel.value = 'lab';
      sel.dispatchEvent(new Event('change', { bubbles: true }));
    });
    const section = [...root.querySelectorAll('section')].find((s) => s.querySelector('h2')?.textContent?.startsWith('Folder kinds'))!;
    const save = [...section.querySelectorAll('button')].find((b) => b.textContent === 'Save')!;
    expect(save.disabled).toBe(false);
    await act(() => save.click());
    for (let i = 0; i < 100 && !gh.seen.some((s) => s.method === 'PUT'); i++) await act(() => new Promise((r) => setTimeout(r, 0)));
    const put = gh.seen.find((s) => s.method === 'PUT' && s.url.endsWith('/contents/materials.yml'))!;
    const text = decodeBase64((put.body as { content: string }).content);
    expect(text).toContain('kinds:\n  course/2026/labs: lab\n');
    // The detected folders stay detected: nothing written for them.
    expect(text).not.toContain('students/lectures');
  });
});

describe('a missing kind on the course’s to-do list', () => {
  it('reads as its own suggested row, linking to the settings', async () => {
    const { courseItems } = await import('../src/screens/Course');
    const c = { ...STATUS.course!, materials: [{ repo: MAT, state: 'ready', checks: [DONE, NO_READINGS] }], todo: [
      { id: `materials:${MAT}:kind_folder:readings`, kind: 'materials', check: 'kind_folder', repo: MAT, text: NO_READINGS.why!, screen: 'materials', entry: MAT, need: 'suggested', set_aside: false },
    ] } as unknown as Parameters<typeof courseItems>[0];
    const row = courseItems(c, [], []).find((i) => i.id === `materials:${MAT}:kind_folder:readings`)!;
    expect(row.label).toBe('No readings folder found');
    expect(row.why).toBe(NO_READINGS.why);
    expect(row.need).toBe('suggested');
    expect(row.hint).toBeUndefined();
    expect(row.link?.href).toBe(`#materials-${MAT}`);
  });
});
