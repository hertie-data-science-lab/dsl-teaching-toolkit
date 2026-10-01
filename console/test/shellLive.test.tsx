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
import { Sidenav, type SubWanted } from '../src/ui/shell';
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

async function mount(url: string, gh = new FakeGitHub()) {
  history.replaceState(null, '', url);
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

describe('the semester banner (decision 0025)', () => {
  // The course's names check reads its .github tree; an empty one is a migrated course.
  const migrated = () => new FakeGitHub().on('GET', new RegExp(`^/repos/${course.org}/\\.github/git/trees/HEAD`), { sha: 'r', truncated: false, tree: [] });
  const settle = async () => { for (let i = 0; i < 5; i++) await act(async () => {}); };
  it('opens every instructor semester page with the banner: one h1, the Student view pill and the semester on GitHub', async () => {
    const el = await mount(`/?cohort=${cohort.org}#schedule`, migrated());
    await settle();
    const banner = el.querySelector('#view .sem-banner');
    expect(banner).not.toBeNull();
    expect(banner!.querySelector('h1')?.textContent).toBe('Fall 2026');
    expect(banner!.querySelector('.banner-course')?.textContent).toBe('Machine Learning');
    expect(banner!.querySelector(`a[href="?semester=${cohort.org}#week"]`)?.textContent).toBe('Student view');
    expect(banner!.textContent).toContain('Semester on GitHub');
    expect(el.querySelectorAll('h1')).toHaveLength(1);
    expect(el.querySelector('.topbar .app-view')).toBeNull();
  });

  it('keeps the course pages’ own h1 and no banner', async () => {
    const el = await mount(`/?course=${course.org}#course`);
    expect(el.querySelector('.sem-banner')).toBeNull();
  });

  it('in an instructor’s preview, the top bar’s view and the banner lead back to the Dashboard', async () => {
    const el = await mount(`/?semester=${cohort.org}#week`);
    await settle();
    const back = `?cohort=${cohort.org}#dashboard`;
    expect(el.querySelector('.topbar a.app-view')?.getAttribute('href')).toBe(back);
    expect(el.querySelector('.topbar a.app-view')?.textContent).toBe('Student view (preview)');
    expect(el.querySelector(`.sem-banner a[href="${back}"]`)?.textContent).toBe('Back to instructor view');
    expect(el.querySelector('.sem-banner')?.textContent).not.toContain('Student view');
    expect(el.querySelectorAll('h1')).toHaveLength(1);
  });

  it('for a real student the view is plain text and the banner has no way to the instructor view', async () => {
    const el = await mount(`/?semester=${NLP.org}#week`);
    expect(el.querySelector('.topbar .app-view')).toBeNull();
    expect(el.querySelector('.topbar .app-name small')?.textContent).toBe('Student view');
    const banner = el.querySelector('.sem-banner')!;
    expect(banner.querySelector('h1')?.textContent).toBe('Fall 2026');
    expect(banner.querySelector('.banner-course')?.textContent).toBe('Natural Language Processing');
    expect(banner.textContent).not.toContain('instructor view');
    expect(banner.textContent).toContain('Semester on GitHub');
  });
});

describe('the course nav’s sub-pages', () => {
  const sub = {
    materials: [
      { repo: 'course-materials-f2026', label: 'course-materials-f2026', href: '#materials-course-materials-f2026' },
      { repo: 'lecture-code', label: 'lecture-code', href: 'https://github.com/x/lecture-code', ext: true },
    ],
    templates: [{ repo: 'assignment-3-f2026', label: 'Group project', href: '#template-assignment-3-f2026' }],
  };
  let asked: SubWanted[] = [];
  const show = (current: string, entry?: string) => {
    root = document.createElement('div');
    document.body.append(root);
    asked = [];
    act(() => render(<Sidenav courses={[course]} course={course} cohortStates={{}} current={current} problems={0} sub={(w) => (asked.push(w), sub)} entry={entry} />, root!));
    return root;
  };
  const group = (h: HTMLElement, label: string) => [...h.querySelectorAll('.nav-group')].find((g) => g.querySelector('.nav-row a')?.textContent === label)!;
  it('is collapsed by default, and the chevron opens and closes it', () => {
    const h = show('course');
    const mat = group(h, 'Materials');
    const chev = mat.querySelector<HTMLButtonElement>('button.nav-chev')!;
    expect(chev.getAttribute('aria-expanded')).toBe('false');
    expect(mat.querySelector<HTMLElement>('ul.nav-sub')!.hidden).toBe(true);
    // Collapsed groups ask for nothing beyond the status.
    expect(asked.at(-1)).toEqual({ materials: false, templates: false });
    act(() => chev.click());
    expect(asked.at(-1)).toEqual({ materials: true, templates: false });
    expect(chev.getAttribute('aria-expanded')).toBe('true');
    expect(mat.querySelector<HTMLElement>('ul.nav-sub')!.hidden).toBe(false);
    const links = [...mat.querySelectorAll('ul.nav-sub a')];
    expect(links.map((a) => a.getAttribute('href'))).toEqual(['#materials-course-materials-f2026', 'https://github.com/x/lecture-code']);
    expect(links[1].getAttribute('target')).toBe('_blank');
    act(() => chev.click());
    expect(mat.querySelector<HTMLElement>('ul.nav-sub')!.hidden).toBe(true);
  });
  it('opens on a page inside it and marks that page, not the index', () => {
    const h = show('templates', 'assignment-3-f2026');
    const tpl = group(h, 'Assignment templates');
    expect(tpl.querySelector('button.nav-chev')!.getAttribute('aria-expanded')).toBe('true');
    expect(tpl.querySelector('.nav-row a')!.getAttribute('aria-current')).toBeNull();
    const cur = tpl.querySelector('ul.nav-sub a[aria-current="page"]')!;
    expect(cur.textContent).toBe('Group project');
    expect(group(h, 'Materials').querySelector('button.nav-chev')!.getAttribute('aria-expanded')).toBe('false');
    // On the index itself, the index is the current page.
    render(null, root!);
    root!.remove();
    const idx = show('templates');
    expect(group(idx, 'Assignment templates').querySelector('.nav-row a')!.getAttribute('aria-current')).toBe('page');
  });
});
