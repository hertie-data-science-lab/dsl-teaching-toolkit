// @vitest-environment happy-dom
// Your repos in Profile (decision 0035 rule 9, from the Set up page of decision 0027 rule 4):
// one section per live semester the person studies, the first open, the rest folded and read
// only when opened; in each, the fork check per materials repo, then the same Open button the
// instructors have for the fork and each assignment repo, the folder and editor from Profile;
// no command blocks.

import { render } from 'preact';
import { act } from 'preact/test-utils';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { App, createState } from '../src/app';
import { ConsoleAuth } from '../src/auth/console';
import { PatAuth } from '../src/auth/pat';
import { EnvCtx, type Env } from '../src/env';
import { GitHubClient } from '../src/github/client';
import type { Semester } from '../src/model/discovery';
import type { Mine } from '../src/model/mine';
import { STUDENT_STATUS_PATH } from '../src/model/names';
import { saveYourSetup } from '../src/model/prefs';
import type { SemesterFacts } from '../src/model/student';
import { RepoChecks } from '../src/screens/StudentSetup';
import { FakeGitHub, fileBody } from './fake';
import FILE from './fixtures/student-status.json?raw';

const LOGIN = 'octo-student';
const ORG = 'hertie-nlp-f2026';
const facts = { materialsRepos: ['materials', 'labs'] } as unknown as SemesterFacts;
const mine = { units: { 'assignment-2': { slug: 'assignment-2', repo: 'assignment-2-octo-student', team: null, members: null, shared: false } } } as unknown as Mine;
const forked = (org: string) => ({ name: 'materials', fork: true, parent: { full_name: `${org}/materials` }, html_url: `https://github.com/${LOGIN}/materials` });
const settle = async () => {
  for (let i = 0; i < 6; i++) await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
};

let root: HTMLElement | null = null;
beforeEach(() => localStorage.clear());
afterEach(() => {
  if (root) render(null, root);
  root?.remove();
  root = null;
  history.replaceState(null, '', '/');
});

async function mount() {
  const gh = new FakeGitHub().on('GET', `/repos/${LOGIN}/materials`, forked(ORG));
  const env = { user: { login: LOGIN, id: 1, name: 'O', email: null, avatar_url: '' }, client: new GitHubClient({ token: () => 't', fetch: gh.fetch }) } as unknown as Env;
  root = document.createElement('div');
  document.body.appendChild(root);
  await act(() => render(<EnvCtx.Provider value={env}><RepoChecks org={ORG} facts={facts} mine={mine} /></EnvCtx.Provider>, root!));
  await settle();
  return root;
}

it('checks each fork, gives the forked one an Open button, and shows no command blocks', async () => {
  const el = await mount();
  const [materials, labs] = [...el.querySelectorAll<HTMLElement>('section[aria-label]')];
  expect(materials.textContent).toContain(`You have forked it: ${LOGIN}/materials`);
  expect(materials.querySelector('.split')).not.toBeNull();
  expect(labs.querySelector('a.btn')!.textContent).toContain('Fork labs');
  expect(labs.querySelector('.split')).toBeNull();
  // The clone command lives only in the Open menu's Clone ?.
  expect(el.querySelector('pre')).toBeNull();
  expect([...el.querySelectorAll('code')].every((c) => c.closest('.open-menu'))).toBe(true);
  // The assignment repo gets the small Open button.
  expect(el.querySelector(`#h-own-${ORG}`)!.closest('section')!.querySelector('.split.small')).not.toBeNull();
  // Profile is the page this sits on: no line sending the student to it.
  expect(el.textContent).not.toContain('Set your folder and editor in Profile');
});

it('opens the fork and the assignment repo from the semester’s folder in Profile', async () => {
  saveYourSetup(LOGIN, { folder: '/Users/o/repos', editor: 'vscode' });
  const el = await mount();
  const main = el.querySelector<HTMLAnchorElement>('section[aria-label="materials"] .split-main')!;
  expect(main.textContent).toBe('Open or clone');
  expect(main.getAttribute('href')).toBe(`vscode://file/Users/o/repos/${ORG}/materials`);
  const own = el.querySelector<HTMLAnchorElement>('.split.small .split-main')!;
  expect(own.getAttribute('href')).toBe(`vscode://file/Users/o/repos/${ORG}/assignment-2-octo-student`);
  expect(own.getAttribute('aria-label')).toBe('Open or clone');
});

describe('Profile’s Your repos sections', () => {
  const ML = 'hertie-ml-f2026';
  const OLD = 'hertie-nlp-f2024';
  const sem = (org: string, courseName: string, over: Partial<Semester> = {}): Semester => ({ org, term: 'f2026', termLabel: 'Fall 2026', courseOrg: `${org}-course`, courseName, archived: false, role: 'student', ...over });
  const statusOf = (org: string) => `/repos/${org}/.github/contents/.system/student-status.json`;

  async function app(semesters: Semester[], courses: unknown[] = []) {
    history.replaceState(null, '', '/#profile');
    const gh = new FakeGitHub();
    for (const k of semesters) gh.on('GET', statusOf(k.org), fileBody(STUDENT_STATUS_PATH, FILE)).on('GET', `/repos/${LOGIN}/materials`, forked(semesters[0].org));
    const s = createState({ auth: new ConsoleAuth(new PatAuth({ store: null }), null), client: new GitHubClient({ token: () => 't', fetch: gh.fetch }) });
    s.user.value = { login: LOGIN, id: 1, name: 'O', email: null, avatar_url: '' };
    s.now.value = Date.parse('2026-09-23T12:00:00Z');
    s.estate.value = { courses: courses as never, semesters, roles: new Map(semesters.map((k) => [k.org, k.role] as const)), kind: 'classic' };
    root = document.createElement('div');
    document.body.appendChild(root);
    await act(async () => render(<App state={s} />, root!));
    await settle();
    return { el: root, gh };
  }
  const reads = (gh: FakeGitHub, org: string) => gh.seen.filter((x) => x.url.includes(statusOf(org))).length;

  it('opens Profile for a student with no course of their own, folder and editor first', async () => {
    const { el } = await app([sem(ORG, 'Natural Language Processing')]);
    expect(el.querySelector('#view h1')?.textContent).toBe('Profile');
    expect(el.querySelector('#ys-folder')).not.toBeNull();
  });

  it('gives each live semester a section, the first open and read, the others folded and read only once opened', async () => {
    const { el, gh } = await app([sem(ORG, 'Natural Language Processing'), sem(ML, 'Machine Learning'), sem(OLD, 'Old NLP', { archived: true, termLabel: 'Fall 2024' })]);
    const sections = [...el.querySelectorAll<HTMLDetailsElement>('details.your-repos')];
    expect(sections.map((d) => d.querySelector('summary')!.textContent)).toEqual(['Your repos in Natural Language Processing, Fall 2026', 'Your repos in Machine Learning, Fall 2026']);
    expect(sections.map((d) => d.open)).toEqual([true, false]);
    expect(sections[0].textContent).toContain(`You have forked it: ${LOGIN}/materials`);
    expect(reads(gh, ML)).toBe(0);
    await act(async () => {
      sections[1].open = true;
      sections[1].dispatchEvent(new Event('toggle'));
    });
    await settle();
    expect(reads(gh, ML)).toBe(1);
    expect(sections[1].querySelector('section[aria-label="materials"]')).not.toBeNull();
  });

  it('shows an instructor with no student semester no such section', async () => {
    const { el } = await app([sem(ORG, 'Natural Language Processing', { role: 'instructor' })]);
    expect(el.querySelector('#view h1')?.textContent).toBe('Profile');
    expect(el.querySelector('details.your-repos')).toBeNull();
  });
});
