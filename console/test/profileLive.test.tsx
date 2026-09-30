// @vitest-environment happy-dom
// Profile mounted through the App on `?#profile`: once the course's status is read, the
// "Clone every repo" block lists its materials repos and templates.

import { render } from 'preact';
import { act } from 'preact/test-utils';
import { afterEach, expect, it } from 'vitest';
import { App, createState } from '../src/app';
import { ConsoleAuth } from '../src/auth/console';
import { PatAuth } from '../src/auth/pat';
import { GitHubClient } from '../src/github/client';
import type { Course } from '../src/model/discovery';
import { saveYourSetup } from '../src/model/prefs';
import { FakeGitHub, fileBody, json } from './fake';
import example from './fixtures/status.example.json';

const user = { login: 'octo', id: 1, name: 'Octo Cat', email: null, avatar_url: '' };
const ORG = example.course.org;
const course: Course = { org: ORG, name: 'Machine Learning', code: 'E1234', description: '', write: true, admins: [], cohorts: [{ org: 'hertie-dsl-demo-f2026', term: 'f2026', termLabel: 'Fall 2026' }], meta: null };

let root: HTMLElement | null = null;
afterEach(() => {
  if (root) render(null, root);
  root?.remove();
  root = null;
  history.replaceState(null, '', '/');
  localStorage.clear();
});

const OTHER = 'hertie-dsl-other-course-e9999';
const other: Course = { ...course, org: OTHER, name: 'Other', code: 'E9999' };
const otherStatus = { ...example, course: { ...example.course, org: OTHER, materials: [{ ...example.course.materials[0], repo: 'other-materials' }], templates: [] } };

async function mount(url: string) {
  history.replaceState(null, '', url);
  const gh = new FakeGitHub()
    .on('GET', `/repos/${ORG}/.github/contents/.system/status.json`, () => json(fileBody('.system/status.json', JSON.stringify(example))))
    .on('GET', `/repos/${OTHER}/.github/contents/.system/status.json`, () => json(fileBody('.system/status.json', JSON.stringify(otherStatus))))
    .on('GET', /git\/trees\/HEAD/, { sha: 't', tree: [], truncated: false });
  const s = createState({ auth: new ConsoleAuth(new PatAuth({ store: null }), null), client: new GitHubClient({ token: () => 't', fetch: gh.fetch }) });
  s.user.value = user;
  s.estate.value = { courses: [course, other], semesters: [], roles: new Map(), kind: 'classic' };
  root = document.createElement('div');
  document.body.appendChild(root);
  await act(async () => render(<App state={s} />, root!));
  for (let i = 0; i < 5 && !root.querySelector('.clone-all'); i++) await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  return root;
}

it('shows the course repos to clone on ?#profile once the course status is ready', async () => {
  saveYourSetup(user.login, { folder: '/Users/o/repos', editor: 'vscode' });
  const root = await mount('/?#profile');
  expect(root.querySelector('#view h1')?.textContent).toBe('Profile');
  const repos = [...example.course.materials.map((m) => m.repo), ...example.course.templates.map((t) => t.repo)];
  expect(root.querySelector('.clone-all code')?.textContent).toBe(
    repos.map((r) => `git clone https://github.com/${ORG}/${r}.git "/Users/o/repos/${ORG}/${r}"`).join('\n'),
  );
});

it('shows the repos of the course the Open menu came from on ?course=<org>#profile', async () => {
  saveYourSetup(user.login, { folder: '/Users/o/repos', editor: 'vscode' });
  const root = await mount(`/?course=${OTHER}#profile`);
  expect(root.querySelector('.clone-all code')?.textContent).toBe(`git clone https://github.com/${OTHER}/other-materials.git "/Users/o/repos/${OTHER}/other-materials"`);
});
