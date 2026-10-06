// @vitest-environment happy-dom
// The student Set up page (decision 0027 rule 4): the fork check per materials repo, then the
// same Open button the instructors have for the fork and each assignment repo, the folder and
// editor from Profile; no command blocks.

import { render } from 'preact';
import { act } from 'preact/test-utils';
import { afterEach, beforeEach, expect, it } from 'vitest';
import { App, createState } from '../src/app';
import { ConsoleAuth } from '../src/auth/console';
import { PatAuth } from '../src/auth/pat';
import { EnvCtx, type Env } from '../src/env';
import { GitHubClient } from '../src/github/client';
import type { Semester } from '../src/model/discovery';
import type { Mine } from '../src/model/mine';
import { saveYourSetup } from '../src/model/prefs';
import type { SemesterFacts } from '../src/model/student';
import { SetupView } from '../src/screens/StudentSetup';
import { FakeGitHub } from './fake';

const LOGIN = 'octo-student';
const ORG = 'hertie-nlp-f2026';
const facts = { materialsRepos: ['materials', 'labs'] } as unknown as SemesterFacts;
const mine = { units: { 'assignment-2': { slug: 'assignment-2', repo: 'assignment-2-octo-student', team: null, members: null, shared: false } } } as unknown as Mine;

let root: HTMLElement | null = null;
beforeEach(() => localStorage.clear());
afterEach(() => {
  if (root) render(null, root);
  root?.remove();
  root = null;
  history.replaceState(null, '', '/');
});

async function mount() {
  const gh = new FakeGitHub().on('GET', `/repos/${LOGIN}/materials`, { name: 'materials', fork: true, parent: { full_name: `${ORG}/materials` }, html_url: `https://github.com/${LOGIN}/materials` });
  const env = { user: { login: LOGIN, id: 1, name: 'O', email: null, avatar_url: '' }, client: new GitHubClient({ token: () => 't', fetch: gh.fetch }) } as unknown as Env;
  root = document.createElement('div');
  document.body.appendChild(root);
  await act(() => render(<EnvCtx.Provider value={env}><SetupView org={ORG} facts={facts} mine={mine} studentView={false} /></EnvCtx.Provider>, root!));
  for (let i = 0; i < 3; i++) await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
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
  expect(el.querySelector('#h-own')!.closest('section')!.querySelector('.split.small')).not.toBeNull();
  // No setup saved: one line to Profile.
  const link = [...el.querySelectorAll('a')].find((a) => a.textContent === 'Set your folder and editor in Profile')!;
  expect(link.getAttribute('href')).toBe('?#profile');
});

it('opens the fork from the semester’s folder in Profile, and drops the Profile line once a folder is saved', async () => {
  saveYourSetup(LOGIN, { folder: '/Users/o/repos', editor: 'vscode' });
  const el = await mount();
  const main = el.querySelector<HTMLAnchorElement>('section[aria-label="materials"] .split-main')!;
  expect(main.textContent).toBe('Open or clone');
  expect(main.getAttribute('href')).toBe(`vscode://file/Users/o/repos/${ORG}/materials`);
  const own = el.querySelector<HTMLAnchorElement>('.split.small .split-main')!;
  expect(own.getAttribute('href')).toBe(`vscode://file/Users/o/repos/${ORG}/assignment-2-octo-student`);
  expect(own.getAttribute('aria-label')).toBe('Open or clone');
  expect(el.textContent).not.toContain('Set your folder and editor in Profile');
});

it('opens Profile for a student with no course of their own', async () => {
  const semester: Semester = { org: ORG, term: 'f2026', termLabel: 'Fall 2026', courseOrg: 'hertie-nlp-e1282', courseName: 'Natural Language Processing', archived: false, role: 'student' };
  history.replaceState(null, '', '/#profile');
  const s = createState({ auth: new ConsoleAuth(new PatAuth({ store: null }), null), client: new GitHubClient({ token: () => 't', fetch: new FakeGitHub().fetch }) });
  s.user.value = { login: LOGIN, id: 1, name: 'O', email: null, avatar_url: '' };
  s.estate.value = { courses: [], semesters: [semester], roles: new Map([[ORG, 'student' as const]]), kind: 'classic' };
  root = document.createElement('div');
  document.body.appendChild(root);
  await act(async () => render(<App state={s} />, root!));
  await act(async () => {});
  expect(root.querySelector('#view h1')?.textContent).toBe('Profile');
  expect(root.querySelector('#ys-folder')).not.toBeNull();
});
