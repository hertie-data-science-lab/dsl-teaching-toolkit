// @vitest-environment happy-dom
// The shell mounted on a real URL (decision 0021): Profile opens whatever semester the query
// names, a student's `#setup` stays their Set up, and an instructor's `#setup` becomes `#profile`.

import { render } from 'preact';
import { act } from 'preact/test-utils';
import { afterEach, describe, expect, it } from 'vitest';
import { App, createState } from '../src/app';
import { ConsoleAuth } from '../src/auth/console';
import { PatAuth } from '../src/auth/pat';
import { GitHubClient } from '../src/github/client';
import type { Course, Semester } from '../src/model/discovery';
import { FakeGitHub } from './fake';

const user = { login: 'octo', id: 1, name: 'Octo Cat', email: null, avatar_url: '' };
const cohort = { org: 'hertie-dsl-demo-f2026', term: 'f2026', termLabel: 'Fall 2026' };
const course: Course = { org: 'hertie-dsl-demo-course-e1234', name: 'Machine Learning', code: 'E1234', description: '', write: true, admins: [], cohorts: [cohort], meta: null };
const NLP: Semester = { org: 'hertie-nlp-f2026', term: 'f2026', termLabel: 'Fall 2026', courseOrg: 'hertie-nlp-e1282', courseName: 'Natural Language Processing', archived: false, role: 'student' };

let root: HTMLElement | null = null;
afterEach(() => {
  if (root) render(null, root);
  root?.remove();
  root = null;
  history.replaceState(null, '', '/');
});

async function mount(url: string) {
  history.replaceState(null, '', url);
  const gh = new FakeGitHub();
  const s = createState({ auth: new ConsoleAuth(new PatAuth({ store: null }), null), client: new GitHubClient({ token: () => 't', fetch: gh.fetch }) });
  s.user.value = user;
  s.estate.value = { courses: [course], semesters: [NLP], roles: new Map([[NLP.org, 'student' as const], [cohort.org, 'instructor' as const]]), kind: 'classic' };
  root = document.createElement('div');
  document.body.appendChild(root);
  await act(async () => render(<App state={s} />, root!));
  await act(async () => {});
  return root;
}

describe('the shell on a real URL', () => {
  it('shows Profile, not the student screens, on ?semester=<org>#profile', async () => {
    const el = await mount(`/?semester=${NLP.org}#profile`);
    expect(el.querySelector('.sidenav')).toBeNull();
    expect(el.querySelector('#view h1')?.textContent).not.toBe('This week');
    expect(el.querySelector('#view .field')).not.toBeNull();
    expect(location.hash).toBe('#profile');
  });

  it('keeps a student’s #setup as their Set up, with no rewrite', async () => {
    const el = await mount(`/?semester=${NLP.org}#setup`);
    expect(el.querySelector('.sidenav [aria-current="page"]')?.textContent).toBe('Set up');
    expect(location.hash).toBe('#setup');
  });

  it('rewrites an instructor’s #setup to #profile and shows Profile', async () => {
    const el = await mount('/#setup');
    expect(location.hash).toBe('#profile');
    expect(el.querySelector('.sidenav')).toBeNull();
    expect(el.querySelector('#view .field')).not.toBeNull();
  });

  it('treats ?semester= of a semester the person has no role in as instructor screens', async () => {
    await mount('/?semester=hertie-other-f2026#setup');
    expect(location.hash).toBe('#profile');
  });
});
