// @vitest-environment happy-dom
// The side nav's one tree (decision 0031 rule 11): course-anchored for an instructor (course
// pages, then semesters: live first, three rows, the rest folded; one expanded at a time),
// semester-anchored for a student (the term's courses, another live term, Past semesters), and
// one person in both roles seeing each set of semesters in its own shell only.

import { render } from 'preact';
import { act } from 'preact/test-utils';
import { afterEach, describe, expect, it } from 'vitest';
import { App, createState } from '../src/app';
import { ConsoleAuth } from '../src/auth/console';
import { PatAuth } from '../src/auth/pat';
import { GitHubClient } from '../src/github/client';
import type { CohortRef, Course, Semester } from '../src/model/discovery';
import { termOf } from '../src/model/discovery';
import { Sidenav, StudentNav } from '../src/ui/shell';
import { FakeGitHub } from './fake';

const NOW = Date.parse('2026-10-01T12:00:00Z');
const COURSE_ORG = 'hertie-dsl-demo-course-e1234';
const ref = (key: string): CohortRef => {
  const org = `hertie-dsl-demo-${key}`;
  const t = termOf(org);
  return { org, term: t.term, termLabel: t.label };
};
const course = (keys: string[]): Course => ({ org: COURSE_ORG, name: 'Machine Learning', code: 'E1234', description: '', write: true, admins: [], cohorts: keys.map(ref), meta: null });

let root: HTMLElement | null = null;
afterEach(() => {
  if (root) render(null, root);
  root?.remove();
  root = null;
  history.replaceState(null, '', '/');
});
function show(v: preact.VNode) {
  root = document.createElement('div');
  document.body.append(root);
  act(() => render(v, root!));
  return root;
}
/** The semester nodes in order, each with whether it is expanded and whether it sits under the fold. */
const nodes = (h: HTMLElement) =>
  [...h.querySelectorAll<HTMLElement>('.sidenav-test ul.tree > li, ul.tree > li')]
    .filter((li) => li.querySelector(':scope > .row > a')?.getAttribute('href')?.startsWith('?cohort='))
    .map((li) => ({
      name: li.querySelector('.node-name')!.textContent!.replace(' (live)', ''),
      open: li.querySelector(':scope > .row > .chev')!.getAttribute('aria-expanded') === 'true',
      folded: !!li.parentElement!.closest('li'),
      live: !!li.querySelector('.nav-dot'),
    }));
const chevOf = (h: HTMLElement, name: string) => [...h.querySelectorAll<HTMLElement>('li')].find((li) => li.querySelector(':scope > .row')?.textContent?.startsWith(name))!.querySelector<HTMLButtonElement>(':scope > .row > .chev')!;

describe('the instructor tree', () => {
  it('anchors the course, lists its pages before its semesters, and has no switcher', () => {
    const h = show(<Sidenav courses={[course(['f2026'])]} course={course(['f2026'])} cohortStates={{}} current="course" now={NOW} />);
    const anchor = h.querySelector('a.nav-anchor')!;
    expect(anchor.getAttribute('href')).toBe(`?course=${COURSE_ORG}#course`);
    // On the overview the Overview leaf is the one current entry, as Dashboard is in a semester; the anchor links there too.
    expect(anchor.getAttribute('aria-current')).toBeNull();
    const cur = h.querySelectorAll('[aria-current]');
    expect(cur).toHaveLength(1);
    expect(cur[0].textContent).toBe('Overview');
    expect(cur[0].getAttribute('href')).toBe(anchor.getAttribute('href'));
    const leaves = [...h.querySelectorAll('ul.tree > li > a.leaf, ul.tree > li > .row > a')].map((a) => a.textContent);
    expect(leaves.slice(0, 5)).toEqual(['Overview', 'Course details', 'Handout materials', 'Assignment templates', 'Public website']);
    const t = h.textContent!;
    expect(t.indexOf('Public website')).toBeLessThan(t.indexOf('Semesters'));
    expect(h.querySelector('.switcher, .popmenu')).toBeNull();
    // One semester: live, expanded on the course page, nothing folded.
    expect(nodes(h)).toEqual([{ name: 'Fall 2026', open: true, folded: false, live: true }]);
  });

  it('shows a read-only course its Overview, current on the overview, and hides the edit pages', () => {
    const ro = { ...course(['f2026']), write: false };
    const h = show(<Sidenav courses={[ro]} course={ro} cohortStates={{}} current="course" now={NOW} />);
    const cur = h.querySelectorAll('[aria-current]');
    expect(cur).toHaveLength(1);
    expect(cur[0].textContent).toBe('Overview');
    expect(cur[0].getAttribute('href')).toBe(`?course=${COURSE_ORG}#course`);
    const t = h.textContent!;
    for (const page of ['Course details', 'Handout materials', 'Assignment templates', 'Public website']) expect(t).not.toContain(page);
    expect(t).toContain('Read only: other pages need write access.');
  });

  it('with three semesters, shows them all newest first, marks the past ones ended, and expands the newest live one', () => {
    const c = course(['f2025', 'f2026', 's2026']);
    const h = show(<Sidenav courses={[c]} course={c} cohortStates={{}} current="details" now={NOW} />);
    expect(nodes(h)).toEqual([
      { name: 'Fall 2026', open: true, folded: false, live: true },
      { name: 'Spring 2026 ended', open: false, folded: false, live: false },
      { name: 'Fall 2025 ended', open: false, folded: false, live: false },
    ]);
    expect(h.textContent).not.toContain('Older semesters');
  });

  it('with six, puts every live one first, fills to three rows, folds the rest, and opens the fold on a folded semester', () => {
    const c = course(['s2024', 'f2024', 's2025', 'f2025', 's2026', 'f2026', 'w2026']);
    const h = show(<Sidenav courses={[c]} course={c} cohortStates={{}} current="course" now={NOW} />);
    const list = nodes(h);
    expect(list.map((n) => [n.name, n.live, n.folded])).toEqual([
      ['Winter 2026', true, false], ['Fall 2026', true, false], ['Spring 2026 ended', false, false],
      ['Fall 2025 ended', false, true], ['Spring 2025 ended', false, true], ['Fall 2024 ended', false, true], ['Spring 2024 ended', false, true],
    ]);
    expect(list.filter((n) => n.open).map((n) => n.name)).toEqual(['Winter 2026']);
    const fold = [...h.querySelectorAll('.nav-fold')].find((f) => f.textContent === 'Older semesters (4)')!;
    const foldChev = fold.previousElementSibling as HTMLButtonElement;
    expect(foldChev.getAttribute('aria-expanded')).toBe('false');
    expect(h.querySelector<HTMLElement>(`#${foldChev.getAttribute('aria-controls')}`)!.hidden).toBe(true);
    act(() => foldChev.click());
    expect(h.querySelector<HTMLElement>(`#${foldChev.getAttribute('aria-controls')}`)!.hidden).toBe(false);
    render(null, root!);
    root!.remove();
    // On a folded semester's page, that one is expanded and the fold starts open.
    const old = c.cohorts.find((k) => k.term === 's2024')!;
    const h2 = show(<Sidenav courses={[c]} course={c} cohort={old} cohortStates={{}} current="schedule" now={NOW} />);
    expect(nodes(h2).filter((n) => n.open).map((n) => n.name)).toEqual(['Spring 2024 ended']);
    expect(h2.querySelector('.nav-fold')!.previousElementSibling!.getAttribute('aria-expanded')).toBe('true');
    expect(h2.querySelector('a[aria-current="page"]')!.textContent).toBe('Schedule');
    // The open semester's pages keep the query; another's name its org.
    expect(h2.querySelector('a[aria-current="page"]')!.getAttribute('href')).toBe('#schedule');
  });

  it('on a semester page, expands that semester; the chevrons are an accordion and do not navigate', () => {
    const c = course(['f2025', 's2026', 'f2026']);
    const open = c.cohorts.find((k) => k.term === 's2026')!;
    const h = show(<Sidenav courses={[c]} course={c} cohort={open} cohortStates={{}} current="dashboard" now={NOW} />);
    expect(nodes(h).filter((n) => n.open).map((n) => n.name)).toEqual(['Spring 2026 ended']);
    const f26 = chevOf(h, 'Fall 2026');
    expect(f26.getAttribute('aria-label')).toBe('Show the Fall 2026 pages');
    act(() => f26.click());
    expect(nodes(h).filter((n) => n.open).map((n) => n.name)).toEqual(['Fall 2026']);
    expect(f26.getAttribute('aria-label')).toBe('Hide the Fall 2026 pages');
    // The other semester's pages name its org.
    const sched = h.querySelector(`#${f26.getAttribute('aria-controls')} a`)!;
    expect(sched.getAttribute('href')).toBe(`?cohort=${c.cohorts.find((k) => k.term === 'f2026')!.org}#dashboard`);
    act(() => f26.click());
    expect(nodes(h).filter((n) => n.open)).toEqual([]);
  });

  it('puts the chevron before the label, and keeps a leaf’s label in line', () => {
    const h = show(<Sidenav courses={[course(['f2026'])]} course={course(['f2026'])} cohortStates={{}} current="course" now={NOW} />);
    const chev = chevOf(h, 'Fall 2026');
    expect(chev.parentElement!.firstElementChild).toBe(chev);
    // A group with nothing to show keeps the gutter empty.
    expect(h.querySelector('.row')!.firstElementChild!.tagName).toBe('SPAN');
    expect(h.querySelector('a.leaf')!.textContent).toBe('Overview');
  });
});

const sem = (key: string, courseName: string, over: Partial<Semester> = {}): Semester => {
  const org = `hertie-${courseName.toLowerCase().replace(/\W+/g, '-')}-${key}`;
  const t = termOf(org);
  return { org, term: t.term, termLabel: t.label, courseOrg: `${org}-course`, courseName, archived: false, role: 'student', ...over };
};

describe('the student tree', () => {
  const dl = sem('f2026', 'Deep Learning'), maths = sem('f2026', 'Maths for Policy');
  const stats = sem('s2026', 'Statistics I', { archived: true }), intro = sem('f2025', 'Introduction to Data Science', { archived: true });
  const all = [dl, maths, stats, intro];

  it('anchors the open term as plain text with its dot, lists its courses with the open one expanded, and past terms collapsed', () => {
    const h = show(<StudentNav root="Your semesters" semesters={all} semester={dl} current="week" now={NOW} />);
    expect(h.querySelector('a.nav-root')!.textContent).toBe('‹ Your semesters');
    const anchor = h.querySelector('.nav-anchor')!;
    expect(anchor.tagName).toBe('SPAN');
    expect(anchor.querySelector('.nav-dot')).not.toBeNull();
    expect(anchor.textContent).toBe('Fall 2026 (live)');
    const courses = [...h.querySelectorAll<HTMLElement>('ul.tree')][0];
    const rows = [...courses.querySelectorAll(':scope > li > .row > a')].map((a) => a.textContent);
    expect(rows).toEqual(['Deep Learning', 'Maths for Policy']);
    expect(chevOf(h, 'Deep Learning').getAttribute('aria-expanded')).toBe('true');
    expect(chevOf(h, 'Maths for Policy').getAttribute('aria-expanded')).toBe('false');
    expect(h.querySelector('a[aria-current="page"]')!.getAttribute('href')).toBe(`?semester=${dl.org}#week`);
    // Past semesters: both terms collapsed, newest first, each unfolding to its courses as links.
    const label = [...h.querySelectorAll('.nav-h')].map((x) => x.textContent);
    expect(label).toEqual(['Past semesters']);
    const spring = chevOf(h, 'Spring 2026');
    expect(spring.getAttribute('aria-expanded')).toBe('false');
    expect(chevOf(h, 'Fall 2025').getAttribute('aria-expanded')).toBe('false');
    expect(h.textContent!.indexOf('Spring 2026')).toBeLessThan(h.textContent!.indexOf('Fall 2025'));
    expect(h.textContent).toContain('Spring 2026 archived');
    act(() => spring.click());
    const links = [...h.querySelectorAll(`#${spring.getAttribute('aria-controls')} a`)];
    expect(links.map((a) => [a.textContent, a.getAttribute('href')])).toEqual([['Statistics I', `?semester=${stats.org}#week`]]);
    // No third level: a past term's course is a plain link.
    expect(h.querySelector(`#${spring.getAttribute('aria-controls')} .chev`)).toBeNull();
    expect(h.querySelector('.switcher, .popmenu')).toBeNull();
  });

  it('opening a past semester re-anchors the tree on it and lists the live term above Past semesters with its dot', () => {
    const h = show(<StudentNav root="Your semesters" semesters={all} semester={stats} current="week" now={NOW} />);
    const anchor = h.querySelector('.nav-anchor')!;
    expect(anchor.textContent).toBe('Spring 2026 archived');
    expect(anchor.querySelector('.nav-dot')).toBeNull();
    expect(chevOf(h, 'Statistics I').getAttribute('aria-expanded')).toBe('true');
    const live = [...h.querySelectorAll<HTMLElement>('li')].find((li) => li.querySelector(':scope > .row .nav-dot'))!;
    const liveLink = live.querySelector(':scope > .row > a')!;
    expect(liveLink.textContent).toBe('Fall 2026 (live)');
    expect(liveLink.getAttribute('href')).toBe(`?semester=${dl.org}#week`);
    const t = h.textContent!;
    expect(t.indexOf('Fall 2026')).toBeLessThan(t.indexOf('Past semesters'));
    // The group lists only the past terms.
    const after = t.slice(t.indexOf('Past semesters'));
    expect(after).toContain('Fall 2025');
    expect(after).not.toContain('Fall 2026');
  });

  it('a Student view is one semester: its course, no other semesters', () => {
    const h = show(<StudentNav root="All courses" semesters={all} semester={sem('f2026', 'Machine Learning', { role: 'instructor' })} current="week" studentView now={NOW} />);
    expect([...h.querySelectorAll('ul.tree > li > .row > a')].map((a) => a.textContent)).toEqual(['Machine Learning']);
    expect(h.textContent).not.toContain('Past semesters');
    expect(h.textContent).not.toContain('Deep Learning');
  });
});

describe('one person, two roles', () => {
  const taught: Course = { ...course(['f2026']), org: COURSE_ORG };
  const studied = sem('f2026', 'Natural Language Processing');
  async function mount(url: string, courses: Course[] = [taught]) {
    history.replaceState(null, '', url);
    const s = createState({ auth: new ConsoleAuth(new PatAuth({ store: null }), null), client: new GitHubClient({ token: () => 't', fetch: new FakeGitHub().on('GET', /\/git\/trees\/HEAD/, { sha: 'r', truncated: false, tree: [] }).fetch }) });
    s.user.value = { login: 'octo', id: 1, name: 'Octo Cat', email: null, avatar_url: '' };
    s.estate.value = { courses, semesters: [studied], roles: new Map([[studied.org, 'student' as const], [taught.cohorts[0].org, 'instructor' as const], [COURSE_ORG, 'instructor' as const]]), kind: 'classic' };
    root = document.createElement('div');
    document.body.append(root);
    await act(async () => render(<App state={s} />, root!));
    for (let i = 0; i < 5; i++) await act(async () => {});
    return root;
  }
  it('keeps the taught semesters out of the student tree', async () => {
    const el = await mount(`/?semester=${studied.org}#week`);
    const nav = el.querySelector('.sidenav')!;
    expect(nav.textContent).toContain('Natural Language Processing');
    expect(nav.innerHTML).not.toContain(taught.cohorts[0].org);
    expect(nav.textContent).not.toContain('Machine Learning');
  });
  it('keeps the student semesters out of the instructor tree', async () => {
    const el = await mount(`/?course=${COURSE_ORG}#details`);
    const nav = el.querySelector('.sidenav')!;
    expect(nav.querySelector('.nav-anchor')!.textContent).toBe('Machine Learning');
    expect(nav.innerHTML).not.toContain(studied.org);
    expect(el.querySelector('.switcher')).toBeNull();
  });
  it('lights the course’s Overview in the New semester wizard', async () => {
    const el = await mount(`/?course=${COURSE_ORG}#new-semester-1`);
    expect(el.querySelector('.sidenav .nav-anchor')!.getAttribute('aria-current')).toBeNull();
    expect(el.querySelector('.sidenav a.leaf[aria-current]')!.textContent).toBe('Overview');
  });
  it('heads a read-only course’s pages with the course banner, keeping the Public site link', async () => {
    const ro: Course = { ...taught, write: false };
    const k = ro.cohorts[0];
    const el = await mount(`/?cohort=${k.org}#dashboard`, [ro]);
    const banner = el.querySelector('#view .course-banner')!;
    expect(banner.querySelector('h1')!.textContent).toBe('Machine Learning');
    expect(banner.querySelector('.sem-title')!.textContent).toBe('Fall 2026');
    expect(banner.textContent).not.toContain('Student view');
    expect(el.querySelectorAll('h1')).toHaveLength(1);
    const crumbs = [...el.querySelectorAll('#view .crumbs > a, #view .crumbs > span:not([aria-hidden])')].map((c) => [c.textContent, c.getAttribute('href')]);
    expect(crumbs).toEqual([['All courses', '?#home'], ['Machine Learning', `?course=${COURSE_ORG}#course`], ['Fall 2026', null]]);
    expect(el.querySelector('#view .ro-banner')).not.toBeNull();
    expect(el.querySelector(`.sidenav a[href="https://${k.org}.github.io"]`)).not.toBeNull();
  });
});

describe('the expansion follows the open page', () => {
  it('an instructor’s override lasts until another page opens, which shows the open page again', () => {
    const c = course(['s2026', 'f2026']);
    const open = c.cohorts.find((k) => k.term === 'f2026')!;
    const h = show(<Sidenav courses={[c]} course={c} cohort={open} cohortStates={{}} current="dashboard" now={NOW} />);
    act(() => chevOf(h, 'Spring 2026').click());
    expect(nodes(h).filter((n) => n.open).map((n) => n.name)).toEqual(['Spring 2026 ended']);
    act(() => render(<Sidenav courses={[c]} course={c} cohort={open} cohortStates={{}} current="schedule" now={NOW} />, root!));
    expect(nodes(h).filter((n) => n.open).map((n) => n.name)).toEqual(['Fall 2026']);
    expect(h.querySelector('a[aria-current="page"]')!.textContent).toBe('Schedule');
  });
  it('a student’s Past semesters fold back when the anchor changes', () => {
    const dl = sem('f2026', 'Deep Learning'), stats = sem('s2026', 'Statistics I', { archived: true }), intro = sem('f2025', 'Intro', { archived: true });
    const all = [dl, stats, intro];
    const h = show(<StudentNav root="Your semesters" semesters={all} semester={dl} current="week" now={NOW} />);
    act(() => chevOf(h, 'Fall 2025').click());
    expect(chevOf(h, 'Fall 2025').getAttribute('aria-expanded')).toBe('true');
    act(() => render(<StudentNav root="Your semesters" semesters={all} semester={stats} current="week" now={NOW} />, root!));
    expect(chevOf(h, 'Fall 2025').getAttribute('aria-expanded')).toBe('false');
  });
});
