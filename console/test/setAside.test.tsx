// @vitest-environment happy-dom
// Setting suggestions aside (decisions 0032 and 0034): only a suggestion has a circle; it asks,
// writes the id into dsl-course.yml's set_aside: and moves the line to the Set aside fold, Bring
// back removes the id at once, and a read-only viewer gets neither.

import { render } from 'preact';
import { act } from 'preact/test-utils';
import { afterEach, describe, expect, it } from 'vitest';
import { EnvCtx, type Env } from '../src/env';
import { GitHubClient, decodeBase64 } from '../src/github/client';
import type { Course } from '../src/model/discovery';
import { StaticFiles } from './staticFiles';
import { StatusStore, type Loaded } from '../src/model/status';
import type { CourseStatus, Status } from '../src/model/types';
import { CONFLICT } from '../src/edit/save';
import { DispatchAdapter } from '../src/ops/adapter';
import { OpsSession } from '../src/ops/session';
import { CourseTabs } from '../src/screens/Course';
import { setAsideText } from '../src/screens/SetAside';
import example from './fixtures/status.example.json';
import { FakeGitHub, json, type Seen } from './fake';

const ORG = 'hertie-dsl-demo-course-e1234';
const REPO = 'course-materials-f2026';
const USER = { login: 'a-example', id: 1, name: 'A. Example', email: null, avatar_url: '' };
const course: Course = { org: ORG, name: 'Machine Learning', code: 'E1234', description: '', write: true, admins: [], cohorts: [], meta: {} };
const BASE = (example as unknown as Status).course!;
const META = '# The course card.\ncourse_name: Machine Learning # shown everywhere\ncourse_code: E1234\n';
const SESSIONS = `materials:${REPO}:sessions`;
const SYLLABUS = `materials:${REPO}:syllabus`;

function envOf(gh: FakeGitHub, f: StaticFiles): Env {
  const client = new GitHubClient({ token: () => 't', fetch: gh.fetch });
  return { client, user: USER, ops: new OpsSession(new DispatchAdapter(client, () => USER.login)), statuses: new StatusStore(client), files: f, pollMs: 0 };
}
const github = () => new FakeGitHub().on('PUT', /\/contents\//, () => json({ content: { sha: 'b1' }, commit: { sha: 'c1' } })).on('GET', /\/check-runs/, { check_runs: [] });
const puts = (gh: FakeGitHub) => gh.seen.filter((s) => s.method === 'PUT' && s.url.endsWith('/contents/dsl-course.yml'));
const body = (s: Seen) => decodeBase64((s.body as { content: string }).content);

let root: HTMLElement | null = null;
afterEach(() => {
  if (root) render(null, root);
  root?.remove();
  root = null;
});
async function mount(c: CourseStatus, f: StaticFiles, gh = github(), write = true, problems?: Status['problems']) {
  root ??= document.body.appendChild(document.createElement('div'));
  const base = example as unknown as Status;
  const loaded: Loaded = { kind: 'ready', status: { ...base, course: c, problems: problems ?? base.problems }, sha: 's', stale: [] };
  await act(() => render(<EnvCtx.Provider value={envOf(gh, f)}><CourseTabs p={{ course: { ...course, write }, loaded, cohortStates: {}, files: f, now: 0 }} c={c} /></EnvCtx.Provider>, root!));
  return gh;
}
async function settle(until: () => boolean) {
  for (let i = 0; i < 200 && !until(); i++) await act(() => new Promise((r) => setTimeout(r, 0)));
}
const files = (text = META) => new StaticFiles({ [`${ORG}/.github/dsl-course.yml`]: text });
const circle = (label: string) => root!.querySelector<HTMLButtonElement>(`button.s-circle[aria-label="Set aside ${label}…"]`)!;
const dialog = () => root!.querySelector<HTMLElement>('[role="dialog"]');
const button = (t: string) => [...root!.querySelectorAll<HTMLElement>('[role="dialog"] .actions > *')].find((b) => b.textContent === t)!;
const titles = () => [...root!.querySelectorAll('[role="tab"]')].map((b) => b.childNodes[0].textContent);
// Each tab's badge; '✓' for the green tick.
const counts = () => [...root!.querySelectorAll('[role="tab"] .n')].map((n) => (n.classList.contains('ok') ? '✓' : n.textContent));
const aside = () => [...root!.querySelectorAll('.aside-list > li')].map((li) => li.textContent);

describe('the course panel', () => {
  it('has four tabs: Problems first and open, Suggestions, Set aside, then Setup at the right', async () => {
    await mount(BASE, files());
    expect(titles()).toEqual(['Problems', 'Suggestions', 'Set aside', 'Setup']);
    // The fixture's template problem has no date: now. C6 and the weekly plan are suggestions.
    expect(counts()).toEqual(['1', '2', '0', '4 of 5']);
    expect(root!.querySelector('[role="tab"][aria-selected="true"]')!.textContent).toBe('Problems1');
    expect(root!.querySelector('[role="tab"] .n.bad')!.textContent).toBe('1');
    expect(root!.querySelector('.ptabs .spacer')!.nextElementSibling!.textContent).toBe('Setup4 of 5');
  });

  it('says No problems on the course, with a green tick on its tab', async () => {
    await mount({ ...BASE, stages: { ...BASE.stages, C5: 'done' } }, files(), github(), true, []);
    expect(counts()[0]).toBe('✓');
    expect(root!.querySelector('#dash-problems .no-problems')!.textContent).toBe('No problems on the course.');
    expect(root!.textContent).toContain('A problem here is something on the course itself that automation cannot act on');
  });

  it('gives only a suggestion a circle', async () => {
    const c = { ...BASE, stages: { ...BASE.stages, C3: 'todo' as const } };
    await mount(c, files());
    expect(circle('Course details filled in')).toBeNull();
    expect(circle('Public website')).not.toBeNull();
    expect(circle('Syllabus')).not.toBeNull();
    expect([...root!.querySelectorAll('button.s-circle')]).toHaveLength(2);
  });

  it('leaves a needed to-do off the panel: it shows on its repo’s row', async () => {
    const tpl = { id: 'template:assignment-1:brief', kind: 'template' as const, repo: 'assignment-1', text: 'The brief (README.md) is not written yet.', optional: false, set_aside: false };
    await mount({ ...BASE, todo: [...BASE.todo!, tpl] }, files());
    expect(root!.textContent).not.toContain('Brief written');
  });
});

describe('the circle before a suggestion', () => {
  it('asks, writes the id into set_aside keeping the file, and moves the step to the fold', async () => {
    const f = files();
    const gh = await mount(BASE, f);
    expect(circle('Public website').getAttribute('title')).toBe('Set aside…');
    await act(() => circle('Public website').click());
    expect(dialog()!.querySelector('h2')!.textContent).toBe('Set aside “Public website”?');
    expect(dialog()!.textContent).toContain('It’s optional: automation runs without it. It moves to Set aside at the end of this list, for everyone on the course. You can bring it back at any time.');
    await act(() => button('Set aside').click());
    expect(button('Setting aside…')).toBeDefined();
    await settle(() => !dialog());
    expect(body(puts(gh)[0])).toBe(`${META}set_aside:\n  - C6\n`);
    expect(gh.seen.find((s) => s.method === 'PUT')!.body).toMatchObject({ message: 'course: set aside C6, from the DSL Teaching Console' });
    expect(aside()).toEqual(['Public website · Bring back']);
    expect(counts()).toEqual(['1', '1', '1', '4 of 5']);
    expect(circle('Public website')).toBeNull();
  });

  it('sets the syllabus row aside as both its checks', async () => {
    const f = files();
    const syl = { id: SYLLABUS, kind: 'materials' as const, repo: REPO, text: 'SYLLABUS.md is still the placeholder.', optional: true, set_aside: false };
    const gh = await mount({ ...BASE, todo: [syl, ...BASE.todo!] }, f);
    const row = [...root!.querySelectorAll('.setup li')].find((li) => li.textContent!.startsWith('Syllabus'))!;
    expect(row.querySelector('.s-why')!.textContent).toContain('SYLLABUS.md is still the placeholder, and the weekly plan (written from the schedule) is not in it yet.');
    await act(() => circle('Syllabus').click());
    await act(() => button('Set aside').click());
    await settle(() => !dialog());
    expect(body(puts(gh)[0])).toBe(`${META}set_aside:\n  - ${SYLLABUS}\n  - ${SESSIONS}\n`);
    expect(aside()).toEqual([`Syllabus (${REPO}) · Bring back`]);
  });

  it('keeps the dialog open and says why when the save fails; Cancel writes nothing', async () => {
    const f = files();
    const bad = new FakeGitHub().on('PUT', /\/contents\//, () => json({ message: 'Server error' }, 500));
    await mount(BASE, f, bad);
    await act(() => circle('Public website').click());
    await act(() => button('Set aside').click());
    await settle(() => !!dialog()?.querySelector('.bad'));
    expect(dialog()!.textContent).toContain('Not saved');
    await act(() => button('Cancel').click());
    expect(dialog()).toBeNull();
    expect(aside()).toEqual([]);
  });

  it('keeps the dialog open on a stale file and writes nothing', async () => {
    const f = files();
    const stale = new FakeGitHub().on('PUT', /\/contents\//, () => json({ message: 'dsl-course.yml does not match static' }, 409));
    await mount(BASE, f, stale);
    await act(() => circle('Public website').click());
    await act(() => button('Set aside').click());
    await settle(() => !!dialog()?.querySelector('.bad'));
    expect(dialog()!.textContent).toContain(CONFLICT);
    expect(f.file(ORG, '.github', 'dsl-course.yml')).toMatchObject({ kind: 'ready', text: META });
    expect(aside()).toEqual([]);
    expect(button('Set aside')).toBeDefined();
  });

  it('is described by its text, names one Close, and gives focus back on close', async () => {
    await mount(BASE, files());
    circle('Public website').focus();
    await act(() => circle('Public website').click());
    const d = dialog()!;
    expect(d.getAttribute('aria-describedby')).toBe('modal-text');
    expect(d.querySelector('#modal-text')!.textContent).toMatch(/^It’s optional/);
    expect(d.querySelector('.x')!.getAttribute('aria-hidden')).toBe('true');
    expect([...d.querySelectorAll('button:not([aria-hidden])')].filter((b) => (b.getAttribute('aria-label') ?? b.textContent) === 'Close')).toHaveLength(0);
    await act(() => button('Cancel').click());
    await settle(() => document.activeElement === circle('Public website'));
    expect(document.activeElement).toBe(circle('Public website'));
  });

  it('sends focus to the Set aside fold when the line it came from moved', async () => {
    await mount(BASE, files());
    circle('Public website').focus();
    await act(() => circle('Public website').click());
    await act(() => button('Set aside').click());
    await settle(() => !dialog());
    await settle(() => document.activeElement?.tagName === 'SUMMARY');
    expect(document.activeElement!.textContent).toBe('Set aside1');
    expect(document.activeElement!.getAttribute('role')).toBe('tab');
  });
});

describe('the Set aside fold', () => {
  const listed = `${META}set_aside:\n  - C6\n  - ${SESSIONS}\n`;

  it('lists what is set aside and brings one back at once, with no dialog', async () => {
    const f = files(listed);
    const gh = await mount(BASE, f);
    expect(aside()).toEqual(['Public website · Bring back', `Syllabus (${REPO}) · Bring back`]);
    expect(counts()).toEqual(['1', '0', '2', '4 of 5']);
    await act(() => root!.querySelector<HTMLButtonElement>('button[aria-label="Bring back Public website"]')!.click());
    expect(dialog()).toBeNull();
    await settle(() => aside().length === 1);
    expect(body(puts(gh)[0])).toBe(`${META}set_aside:\n  - ${SESSIONS}\n`);
    expect(circle('Public website')).not.toBeNull();
  });

  it('drops the key when the last id is brought back', () => {
    expect(setAsideText(`${META}set_aside:\n  - C6\n`, 'C6', false)).toEqual({ text: META });
    expect(setAsideText(`${META}set_aside: [C6]\n`, SESSIONS, true)).toEqual({ text: `${META}set_aside:\n  - C6\n  - ${SESSIONS}\n` });
  });

  it('shows a step done anyway as done, and lists nothing for its id', async () => {
    await mount({ ...BASE, stages: { ...BASE.stages, C6: 'done' } }, files(`${META}set_aside: [C6]\n`));
    expect(aside()).toEqual([]);
    const c6 = [...root!.querySelectorAll('.setup li')].find((li) => li.textContent!.startsWith('Public website'))!;
    expect(c6.className).toBe('done');
  });

  it('puts a green tick on Setup once every needed step is done', async () => {
    await mount({ ...BASE, stages: { ...BASE.stages, C5: 'done' } }, files(listed));
    expect(counts()[3]).toBe('✓');
  });
});

describe('a read-only viewer', () => {
  it('gets no circle, no Bring back and no word about the circle', async () => {
    await mount(BASE, files(`${META}set_aside: [C6]\n`), github(), false);
    expect(root!.querySelector('button.s-circle')).toBeNull();
    expect(aside()).toEqual(['Public website']);
    expect(root!.querySelector('.aside-fold button')).toBeNull();
    expect(root!.textContent).not.toContain('click the circle');
  });

  it('a writer’s Suggestions tab says how to set aside', async () => {
    await mount(BASE, files());
    expect(root!.textContent).toContain('click the circle before it to set it aside');
  });
});
