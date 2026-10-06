// @vitest-environment happy-dom
// Setting optional Setup & To do items aside (decision 0032): the circle asks, an optional item
// is written into dsl-course.yml's set_aside: and moves to the section's fold, a required one
// says why it cannot be, Bring back removes the id at once, and a read-only viewer gets neither.

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
import { SetupPanel, overviewHeights } from '../src/screens/Course';
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
async function mount(c: CourseStatus, f: StaticFiles, gh = github(), write = true) {
  root ??= document.body.appendChild(document.createElement('div'));
  const loaded: Loaded = { kind: 'ready', status: { ...(example as unknown as Status), course: c }, sha: 's', stale: [] };
  await act(() => render(<EnvCtx.Provider value={envOf(gh, f)}><SetupPanel p={{ course: { ...course, write }, loaded, cohortStates: {}, files: f, now: 0 }} c={c} /></EnvCtx.Provider>, root!));
  return gh;
}
async function settle(until: () => boolean) {
  for (let i = 0; i < 200 && !until(); i++) await act(() => new Promise((r) => setTimeout(r, 0)));
}
const files = (text = META) => new StaticFiles({ [`${ORG}/.github/dsl-course.yml`]: text });
const circle = (label: string) => root!.querySelector<HTMLButtonElement>(`button.s-circle[aria-label="Set aside ${label}…"]`)!;
const dialog = () => root!.querySelector<HTMLElement>('[role="dialog"]');
const button = (t: string) => [...root!.querySelectorAll<HTMLElement>('[role="dialog"] .actions > *')].find((b) => b.textContent === t)!;
const folds = () => [...root!.querySelectorAll('.aside-fold > summary')].map((s) => s.textContent);
const counts = () => [...root!.querySelectorAll('.setup-fold > summary .cnt')].map((s) => s.textContent);

describe('the circle before an optional item', () => {
  it('asks, writes the id into set_aside keeping the file, and moves the step to the fold', async () => {
    const f = files();
    const gh = await mount(BASE, f);
    expect(counts()).toEqual(['4 of 6 done', '1 open']);
    expect(circle('Public website').getAttribute('title')).toBe('Set aside…');
    await act(() => circle('Public website').click());
    expect(dialog()!.querySelector('h2')!.textContent).toBe('Set aside “Public website”?');
    expect(dialog()!.textContent).toContain('It’s optional: the course is ready without it. It moves to Set aside at the end of this list, for everyone on the course. You can bring it back at any time.');
    await act(() => button('Set aside').click());
    expect(button('Setting aside…')).toBeDefined();
    await settle(() => !dialog());
    expect(body(puts(gh)[0])).toBe(`${META}set_aside:\n  - C6\n`);
    expect(gh.seen.find((s) => s.method === 'PUT')!.body).toMatchObject({ message: 'course: set aside C6, from the DSL Teaching Console' });
    expect(folds()).toEqual(['Set aside (1)']);
    expect(root!.querySelector('.aside-fold')!.textContent).toBe('Set aside (1)Public website · Bring back');
    expect(counts()).toEqual(['4 of 5 done', '1 open']);
    expect(circle('Public website')).toBeNull();
  });

  it('sets an optional to-do aside, which leaves To do for its fold', async () => {
    const f = files();
    const gh = await mount(BASE, f);
    await act(() => circle('Weekly plan in the syllabus').click());
    expect(dialog()!.querySelector('h2')!.textContent).toBe('Set aside “Weekly plan in the syllabus”?');
    await act(() => button('Set aside').click());
    await settle(() => !dialog());
    expect(body(puts(gh)[0])).toBe(`${META}set_aside:\n  - ${SESSIONS}\n`);
    expect(counts()[1]).toBe('Nothing to do');
    expect(root!.textContent).toContain('Nothing to do.');
    expect(root!.querySelectorAll('.setup-fold')[1].querySelector('.aside-fold')!.textContent).toBe(`Set aside (1)Weekly plan in the syllabus (${REPO}) · Bring back`);
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
    expect(folds()).toEqual([]);
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
    expect(folds()).toEqual([]);
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
    expect(document.activeElement!.textContent).toBe('Set aside (1)');
  });
});

describe('the circle before a required item', () => {
  it('says what a required setup step still needs, and opens its page', async () => {
    const c = { ...BASE, stages: { ...BASE.stages, C3: 'todo' as const }, stage_why: { C3: 'Course details have no description yet.' } };
    const gh = await mount(c, files());
    await act(() => circle('Course details filled in').click());
    expect(dialog()!.querySelector('h2')!.textContent).toBe('“Course details filled in” can’t be set aside');
    expect(dialog()!.querySelector('p')!.textContent).toBe('A new semester needs this step first. Still missing: course details have no description yet.');
    expect(button('Open course details').getAttribute('href')).toBe('#details');
    await act(() => button('Close').click());
    expect(dialog()).toBeNull();
    expect(puts(gh)).toHaveLength(0);
  });

  it('names the step a blocked one waits for and opens that step’s page', async () => {
    const c = { ...BASE, stages: { ...BASE.stages, C2: 'todo' as const, C3: 'blocked' as const }, stage_why: { C3: 'Waiting for the course to be set up.' } };
    await mount(c, files());
    await act(() => circle('Course details filled in').click());
    expect(dialog()!.querySelector('#modal-text')!.textContent).toBe('A new semester needs this step first. It waits for Course set up on GitHub.');
    expect(button('Open .github').getAttribute('href')).toBe(`https://github.com/${ORG}/.github`);
  });

  it('sends a step with a problem to the problem', async () => {
    const c = { ...BASE, stages: { ...BASE.stages, C3: 'problem' as const }, stage_why: { C3: '1 problem needs fixing.' } };
    await mount(c, files());
    await act(() => circle('Course details filled in').click());
    expect(dialog()!.querySelector('#modal-text')!.textContent).toBe('A new semester needs this step first. A problem stops it: fix that first.');
    expect(button('See the problem').getAttribute('href')).toBe('#course-problems');
  });

  it('says a required to-do stops the repo, materials released and templates handed out', async () => {
    const tpl = { id: 'template:assignment-1:brief', kind: 'template' as const, repo: 'assignment-1', text: 'The brief (README.md) is not written yet.', optional: false, set_aside: false };
    const syl = { id: SYLLABUS, kind: 'materials' as const, repo: REPO, text: 'SYLLABUS.md is still the placeholder.', optional: false, set_aside: false };
    await mount({ ...BASE, todo: [syl, tpl] }, files());
    await act(() => circle('Syllabus written').click());
    expect(dialog()!.querySelector('h2')!.textContent).toBe('“Syllabus written” can’t be set aside');
    expect(dialog()!.querySelector('p')!.textContent).toBe(`${REPO} can’t be released without it. If you no longer need this materials repo, archive its repo on GitHub and it leaves this list.`);
    expect(button('Open settings').getAttribute('href')).toBe(`#materials-${REPO}`);
    await act(() => button('Close').click());
    await act(() => circle('Brief written').click());
    expect(dialog()!.querySelector('p')!.textContent).toBe('assignment-1 can’t be handed out without it. If you no longer need this assignment template, archive its repo on GitHub and it leaves this list.');
    expect(button('Open settings').getAttribute('href')).toBe('#template-assignment-1');
  });
});

describe('the Set aside fold', () => {
  const listed = `${META}set_aside:\n  - C6\n  - ${SESSIONS}\n`;

  it('counts each section’s items and brings one back at once, with no dialog', async () => {
    const f = files(listed);
    const gh = await mount(BASE, f);
    expect(folds()).toEqual(['Set aside (1)', 'Set aside (1)']);
    // Every step not set aside is done but C5: setup is not complete; To do has nothing open.
    expect(counts()).toEqual(['4 of 5 done', 'Nothing to do']);
    await act(() => root!.querySelector<HTMLButtonElement>('button[aria-label="Bring back Public website"]')!.click());
    expect(dialog()).toBeNull();
    await settle(() => folds().length === 1);
    expect(body(puts(gh)[0])).toBe(`${META}set_aside:\n  - ${SESSIONS}\n`);
    expect(circle('Public website')).not.toBeNull();
  });

  it('weighs the overview from the same list the panel shows', () => {
    const o = { course: BASE, problems: 0, semesters: 0, description: '', activity: 0 };
    const plain = overviewHeights({ ...o, list: [] })[0].h;
    expect(overviewHeights({ ...o, list: ['C6', SESSIONS] })[0].h).toBe(plain - 2 - 2);
  });

  it('drops the key when the last id is brought back', () => {
    expect(setAsideText(`${META}set_aside:\n  - C6\n`, 'C6', false)).toEqual({ text: META });
    expect(setAsideText(`${META}set_aside: [C6]\n`, SESSIONS, true)).toEqual({ text: `${META}set_aside:\n  - C6\n  - ${SESSIONS}\n` });
  });

  it('shows a step done anyway as done, and folds nothing for its id', async () => {
    await mount({ ...BASE, stages: { ...BASE.stages, C6: 'done' } }, files(`${META}set_aside: [C6]\n`));
    expect(folds()).toEqual([]);
    const c6 = [...root!.querySelectorAll('.setup li')].find((li) => li.textContent!.startsWith('Public website'))!;
    expect(c6.className).toBe('done');
  });

  it('collapses Initial setup once every step not set aside is done', async () => {
    await mount({ ...BASE, stages: { ...BASE.stages, C5: 'done' } }, files(listed));
    const setup = root!.querySelector<HTMLDetailsElement>('.setup-fold')!;
    expect(setup.open).toBe(false);
    expect(counts()[0]).toBe('Complete');
  });
});

describe('a read-only viewer', () => {
  it('gets no circle, no Bring back and no word about the circle', async () => {
    await mount(BASE, files(`${META}set_aside: [C6]\n`), github(), false);
    expect(root!.querySelector('button.s-circle')).toBeNull();
    expect(root!.querySelector('.aside-fold')!.textContent).toBe('Set aside (1)Public website');
    expect(root!.querySelector('.aside-fold button')).toBeNull();
    expect(root!.textContent).not.toContain('click the circle');
  });

  it('a writer’s panel ? says how to set aside', async () => {
    await mount(BASE, files());
    expect(root!.querySelector('h2')!.textContent).toContain('Optional items can be set aside: click the circle before them.');
  });
});
