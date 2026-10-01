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

describe('the course banner (decision 0031 rule 11)', () => {
  // The course's names check reads its .github tree; an empty one is a migrated course.
  const migrated = () => new FakeGitHub().on('GET', new RegExp(`^/repos/${course.org}/\\.github/git/trees/HEAD`), { sha: 'r', truncated: false, tree: [] });
  const settle = async () => { for (let i = 0; i < 5; i++) await act(async () => {}); };
  const crumbs = (el: HTMLElement) => [...el.querySelectorAll('#view .crumbs > a, #view .crumbs > span:not([aria-hidden])')].map((c) => [c.textContent, c.getAttribute('href')]);
  it('opens every instructor semester page with the course as the one h1, the semester line, the Student view pill and the semester on GitHub', async () => {
    const el = await mount(`/?cohort=${cohort.org}#schedule`, migrated());
    await settle();
    const banner = el.querySelector('#view .course-banner')!;
    expect(banner.querySelector('h1')?.textContent).toBe('Machine Learning');
    expect(banner.querySelector('.cb-sem .sem-title')?.textContent).toBe('Fall 2026');
    expect(banner.querySelector(`.cb-side a[href="?semester=${cohort.org}#week"]`)?.textContent).toBe('Student view');
    expect(banner.querySelector('.cb-side')!.textContent).toContain('Semester on GitHub');
    expect(el.querySelectorAll('h1')).toHaveLength(1);
    expect(el.querySelector('.topbar .app-view')).toBeNull();
    // Crumbs follow the tree; on Schedule the semester is a link to its Dashboard.
    expect(crumbs(el)).toEqual([['All courses', '?#home'], ['Machine Learning', `?course=${course.org}#course`], ['Fall 2026', `?cohort=${cohort.org}#dashboard`]]);
    expect(el.querySelector('#view .crumbs')!.textContent).toContain('›');
  });

  it('on the Dashboard the semester crumb is plain', async () => {
    const el = await mount(`/?cohort=${cohort.org}#dashboard`, migrated());
    await settle();
    expect(crumbs(el).at(-1)).toEqual(['Fall 2026', null]);
    expect(el.querySelector('#view .course-banner .btn.small.quiet')?.textContent).toBe('Student view');
  });

  it('heads the course overview with New semester and no semester line; Overview is the current page', async () => {
    const el = await mount(`/?course=${course.org}#course`, migrated());
    await settle();
    const banner = el.querySelector('#view .course-banner')!;
    expect(banner.querySelector('h1')!.textContent).toContain('Machine Learning');
    expect(banner.querySelector('h1 .hint-btn')).not.toBeNull();
    expect(banner.querySelector('.cb-side a.btn')?.textContent).toBe('New semester');
    expect(banner.querySelector('.cb-sem')).toBeNull();
    expect(el.querySelectorAll('h1')).toHaveLength(1);
    expect(el.querySelector('#view .page-head')).toBeNull();
    expect(crumbs(el)).toEqual([['All courses', '?#home'], ['Machine Learning', null]]);
    expect(el.querySelector('.sidenav .nav-anchor')?.getAttribute('aria-current')).toBeNull();
    const cur = el.querySelectorAll('.sidenav [aria-current]');
    expect(cur).toHaveLength(1);
    expect(cur[0].textContent).toBe('Overview');
  });

  it('heads Course details with the course banner, no right side, and the page title as an h2', async () => {
    const el = await mount(`/?course=${course.org}#details`, migrated());
    await settle();
    const banner = el.querySelector('#view .course-banner')!;
    expect(banner.classList.contains('plain')).toBe(true);
    expect(banner.querySelector('.cb-side')).toBeNull();
    expect(banner.querySelector('h1')!.textContent).toBe('Machine Learning');
    expect(el.querySelector('#view .page-head h2.h1')!.textContent).toContain('Course details');
    expect(el.querySelectorAll('h1')).toHaveLength(1);
    expect(crumbs(el)).toEqual([['All courses', '?#home'], ['Machine Learning', `?course=${course.org}#course`]]);
    expect(el.querySelector('.sidenav .nav-anchor')?.getAttribute('aria-current')).toBeNull();
  });

  it('in an instructor’s preview, the top bar’s view and the banner lead back to the Dashboard', async () => {
    const el = await mount(`/?semester=${cohort.org}#week`);
    await settle();
    const back = `?cohort=${cohort.org}#dashboard`;
    expect(el.querySelector('.topbar a.app-view')?.getAttribute('href')).toBe(back);
    expect(el.querySelector('.topbar a.app-view .long')?.textContent).toBe('Student view (preview)');
    expect(el.querySelector(`.course-banner a[href="${back}"]`)?.textContent).toBe('Back to instructor view');
    expect(el.querySelector('.course-banner')?.textContent).not.toContain('Student view');
    expect(el.querySelectorAll('h1')).toHaveLength(1);
  });

  it('for a real student the course is the h1, the crumbs go semester first, and there is no way to the instructor view', async () => {
    const el = await mount(`/?semester=${NLP.org}#week`);
    expect(el.querySelector('.topbar .app-view')).toBeNull();
    expect(el.querySelector('.topbar .app-name small')?.textContent).toBe('Student view');
    const banner = el.querySelector('.course-banner')!;
    expect(banner.querySelector('h1')?.textContent).toBe('Natural Language Processing');
    expect(banner.querySelector('.sem-title')?.textContent).toBe('Fall 2026');
    expect(banner.textContent).not.toContain('instructor view');
    expect(banner.textContent).toContain('Semester on GitHub');
    // This person teaches a course, so the landing page is All courses; the semester has no page.
    expect(crumbs(el)).toEqual([['All courses', '?#home'], ['Fall 2026', null], ['Natural Language Processing', null]]);
  });

  it('links the course crumb on a student’s Schedule', async () => {
    const el = await mount(`/?semester=${NLP.org}#schedule`);
    expect(crumbs(el).at(-1)).toEqual(['Natural Language Processing', `?semester=${NLP.org}#week`]);
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
    act(() => render(<Sidenav courses={[course]} course={course} cohortStates={{}} current={current} sub={(w) => (asked.push(w), sub)} entry={entry} />, root!));
    return root;
  };
  const group = (h: HTMLElement, label: string) => [...h.querySelectorAll('.sidenav li, li')].find((g) => g.querySelector(':scope > .row > a')?.textContent === label)!;
  it('is collapsed by default, and the chevron opens and closes it', () => {
    const h = show('course');
    const mat = group(h, 'Handout materials');
    const chev = mat.querySelector<HTMLButtonElement>('button.chev')!;
    expect(chev.getAttribute('aria-expanded')).toBe('false');
    expect(mat.querySelector<HTMLElement>('ul.lvl2')!.hidden).toBe(true);
    // Collapsed groups ask for nothing beyond the status.
    expect(asked.at(-1)).toEqual({ materials: false, templates: false });
    act(() => chev.click());
    expect(asked.at(-1)).toEqual({ materials: true, templates: false });
    expect(chev.getAttribute('aria-expanded')).toBe('true');
    expect(mat.querySelector<HTMLElement>('ul.lvl2')!.hidden).toBe(false);
    const links = [...mat.querySelectorAll('ul.lvl2 a')];
    expect(links.map((a) => a.getAttribute('href'))).toEqual(['#materials-course-materials-f2026', 'https://github.com/x/lecture-code']);
    expect(links[1].getAttribute('target')).toBe('_blank');
    act(() => chev.click());
    expect(mat.querySelector<HTMLElement>('ul.lvl2')!.hidden).toBe(true);
  });
  it('opens on a page inside it and marks that page, not the index', () => {
    const h = show('templates', 'assignment-3-f2026');
    const tpl = group(h, 'Assignment templates');
    expect(tpl.querySelector('button.chev')!.getAttribute('aria-expanded')).toBe('true');
    expect(tpl.querySelector(':scope > .row > a')!.getAttribute('aria-current')).toBeNull();
    const cur = tpl.querySelector('ul.lvl2 a[aria-current="page"]')!;
    expect(cur.textContent).toBe('Group project');
    expect(group(h, 'Handout materials').querySelector('button.chev')!.getAttribute('aria-expanded')).toBe('false');
    // On the index itself, the index is the current page.
    render(null, root!);
    root!.remove();
    const idx = show('templates');
    expect(group(idx, 'Assignment templates').querySelector(':scope > .row > a')!.getAttribute('aria-current')).toBe('page');
  });
});
