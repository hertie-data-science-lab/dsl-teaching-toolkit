// The shell (decision 0021): the app-level top bar, two levels of navigation, the side nav
// without wizards (decision 0031 rule 11), `#setup` landing on Profile, and the sign-in page's copy.

import { render } from 'preact-render-to-string';
import { describe, expect, it } from 'vitest';
import { App, createState, subPages } from '../src/app';
import { StaticFiles } from '../src/model/files';
import type { Loaded } from '../src/model/status';
import { AppAuth } from '../src/auth/app';
import { ConsoleAuth } from '../src/auth/console';
import { PatAuth } from '../src/auth/pat';
import { GitHubClient } from '../src/github/client';
import type { Course, Semester } from '../src/model/discovery';
import { movedHash, parseHash, parseSearch, resolveContext } from '../src/router';
import { SignInScreen } from '../src/screens/Home';
import { Sidenav, Topbar } from '../src/ui/shell';
import { FakeGitHub } from './fake';

const user = { login: 'octo', id: 1, name: 'Octo Cat', email: null, avatar_url: '' };
const cohort = { org: 'hertie-dsl-demo-f2026', term: 'f2026', termLabel: 'Fall 2026' };
const course: Course = { org: 'hertie-dsl-demo-course-e1234', name: 'Machine Learning', code: 'E1234', description: '', write: true, admins: [], cohorts: [cohort], meta: null };
const text = (v: preact.VNode) => render(v).replace(/<[^>]+>/g, ' ').replace(/&amp;/g, '&').replace(/&rsquo;/g, '’').replace(/\s+/g, ' ');

describe('router', () => {
  it('lands an old #setup on Profile in the instructor screens, never in a student’s', () => {
    expect(parseHash('#setup')).toEqual({ screen: 'profile' });
    expect(movedHash('#setup')).toBe('#profile');
    expect(parseHash('#setup', true)).toEqual({ screen: 'setup' });
    expect(movedHash('#setup', true)).toBeNull();
    expect(movedHash('#profile')).toBeNull();
  });
  it('makes Home about no course or semester, whatever the query names', () => {
    const home = { screen: 'home' };
    expect(resolveContext([course], parseSearch('?cohort=hertie-dsl-demo-f2026'), home)).toEqual({});
    expect(resolveContext([course], parseSearch('?course=hertie-dsl-demo-course-e1234'), home)).toEqual({});
    expect(resolveContext([course], parseSearch('?cohort=hertie-dsl-demo-f2026'), { screen: 'dashboard' })).toEqual({ course, cohort });
  });
});

describe('two levels', () => {
  const app = (hash: string, semesters: Semester[] = []) => {
    const gh = new FakeGitHub();
    const s = createState({ auth: new ConsoleAuth(new PatAuth({ store: null }), null), client: new GitHubClient({ token: () => 't', fetch: gh.fetch }) });
    s.user.value = user;
    s.estate.value = { courses: [course], semesters, roles: new Map([[course.org, 'instructor'], ...semesters.map((x) => [x.org, 'student'] as [string, 'student'])]), kind: 'classic' };
    s.hash.value = hash;
    return render(<App state={s} />);
  };
  it('renders Profile and Guide full width, without the side nav', () => {
    for (const hash of ['#profile', '#help']) {
      const out = app(hash);
      expect(out).not.toContain('class="sidenav"');
      expect(out).not.toContain('>Menu</button>');
      expect(out).toContain('id="view"');
    }
  });
  it('lands an instructor on All courses, even with one writable course (decision 0030 rule 1)', () => {
    const out = app('');
    expect(out).toContain('<h1>All courses');
    expect(out).not.toContain('course-banner');
  });
  it('lands a person with a course and one live student semester on All courses, with Your semesters below', () => {
    const nlp: Semester = { org: 'hertie-nlp-f2026', term: 'f2026', termLabel: 'Fall 2026', courseOrg: 'hertie-nlp-e1282', courseName: 'Natural Language Processing', archived: false, role: 'student' };
    const out = app('', [nlp]);
    expect(out).toContain('<h1>All courses');
    expect(out).toContain('Your semesters');
    expect(out).toContain(`?semester=${nlp.org}#week`);
    expect(out).not.toContain('course-banner');
  });
  it('keeps the side nav on Home', () => {
    const out = app('#home');
    expect(out).toContain('class="sidenav"');
    expect(out).toContain('>Menu</button>');
  });
});

describe('top bar', () => {
  it('names the product always, the view only once signed in', () => {
    expect(text(<Topbar user={null} />)).toContain('DSL Teaching Console');
    expect(render(<Topbar user={null} />)).not.toContain('<small>');
    const out = render(<Topbar user={user} title="Instructor view" onSignOut={() => {}} />);
    expect(out).toContain('DSL Teaching Console<small>Instructor view</small>');
  });
  it('links the person to Profile, with no Your setup pill and no course links', () => {
    const out = render(<Topbar user={user} title="Instructor view" onSignOut={() => {}} guide />);
    const who = /<a[^>]*class="who"[^>]*>/.exec(out)?.[0] ?? '';
    expect(who).toContain('href="?#profile"');
    expect(who).toContain('aria-label="Your profile"');
    expect(out).toMatch(/<a[^>]*href="\?#help"[^>]*>Guide<\/a>/);
    expect(out).toMatch(/<a[^>]*class="app-name"[^>]*href="\?#home"|<a[^>]*href="\?#home"[^>]*class="app-name"/);
    expect(out).toContain('Octo Cat');
    expect(out).not.toContain('Your setup');
    expect(out).not.toContain('hdr-links');
    expect(out).toContain('>Guide</a>');
    expect(out).toContain('>Sign out</button>');
  });
  it('shows Guide only to a person with an instructor role (decision 0029 rule 5)', () => {
    const out = render(<Topbar user={user} title="Student view" onSignOut={() => {}} />);
    expect(out).not.toContain('Guide');
    expect(out).toContain('>Sign out</button>');
  });
});

describe('side nav', () => {
  it('lists no wizards, and All courses clears the query', () => {
    const out = render(<Sidenav courses={[course]} course={course} cohort={cohort} cohortStates={{}} current="dashboard" />);
    expect(out).not.toContain('New course');
    expect(out).not.toContain('New semester');
    expect(out).toMatch(/<a class="nav-root" href="\?#home"><span aria-hidden="true">‹ <\/span>All courses<\/a>/);
  });
  it('marks All courses current on Home, with no course in the nav', () => {
    const out = render(<Sidenav courses={[course]} cohortStates={{}} current="home" />);
    expect(out).toContain('<a class="nav-root" href="?#home" aria-current="page">');
    expect(out).not.toContain('nav-anchor');
  });
  it('orders the external links Public site, Course (Student view and the semester on GitHub are in the banner)', () => {
    const t = text(<Sidenav courses={[course]} course={course} cohort={cohort} site={cohort} cohortStates={{}} current="dashboard" />);
    const at = ['Public site', 'Course on GitHub'].map((l) => t.indexOf(l));
    expect(at.every((i) => i >= 0)).toBe(true);
    expect([...at].sort((a, b) => a - b)).toEqual(at);
    expect(t).not.toContain('Student site');
  });
});

describe('sign-in page', () => {
  const LINE = 'The console can see and change only what your GitHub account can.';
  it('says what the console is, and what it can reach under the button', () => {
    const app = new AppAuth({ clientId: 'Iv1.x', relayUrl: 'https://relay.example', redirectUri: 'https://c.example/', store: null });
    const out = render(<SignInScreen auth={new ConsoleAuth(new PatAuth({ store: null }), app)} onSignedIn={() => {}} />);
    const t = text(<SignInScreen auth={new ConsoleAuth(new PatAuth({ store: null }), app)} onSignedIn={() => {}} />);
    expect(t).toContain('A single central console for both instructors and students to manage their GitHub-based DSL courses.');
    expect(t).toContain(LINE);
    expect(t).not.toContain('approve the lab');
    expect(out.indexOf('>Sign in with GitHub</button>')).toBeGreaterThan(-1);
    expect(out.indexOf('>Sign in with GitHub</button>')).toBeLessThan(out.indexOf(LINE));
    expect(out).toMatch(/<p class="footnote">The console can see/);
    expect(out).toMatch(/<details class="fold">.*<div class="fold-body"><form[\s\S]*id="pat"[\s\S]*<\/form><\/div><\/details>/s);
  });
});

describe('the course nav’s sub-pages', () => {
  const status = { kind: 'ready', sha: 's', stale: [], status: { schema: 'dsl.status/1', inputs: {}, course: {
    org: course.org, ready: true, stages: {}, todo: [],
    materials: [{ repo: 'course-materials-f2026', state: 'ready' }],
    templates: [{ repo: 'assignment-3-f2026', slug: 'assignment-3-f2026', state: 'ready' }, { repo: 'assignment-4', slug: 'assignment-4', state: 'todo' }],
  } } } as unknown as Loaded;
  const files = new StaticFiles(
    { [`${course.org}/assignment-3-f2026/grading_config.yml`]: 'title: Group project\n' },
    {},
    {},
    { [course.org]: [{ name: '.github' }, { name: 'course-materials-f2026' }, { name: 'lecture-code', html_url: 'https://github.com/x/lecture-code' }, { name: 'assignment-4', topics: ['dsl-assignment'] }] },
  );
  const all = { materials: true, templates: true, titles: true };
  it('lists materials first, then the other repos as links to GitHub', () => {
    const s = subPages(course, status, {}, files, all)!;
    expect(s.materials).toEqual([
      { repo: 'course-materials-f2026', label: 'course-materials-f2026', href: '#materials-course-materials-f2026' },
      { repo: 'lecture-code', label: 'lecture-code', href: 'https://github.com/x/lecture-code', ext: true },
    ]);
    // Collapsed, Materials reads no repo list.
    expect(subPages(course, status, {}, files, { ...all, materials: false })!.materials.map((x) => x.repo)).toEqual(['course-materials-f2026']);
  });
  it('labels a template by its title, else its repo name, and by the repo name alone when titles are off', () => {
    expect(subPages(course, status, {}, files, all)!.templates.map((t) => t.label)).toEqual(['Group project', 'assignment-4']);
    expect(subPages(course, status, {}, files, { ...all, titles: false })!.templates.map((t) => t.label)).toEqual(['assignment-3-f2026', 'assignment-4']);
    expect(subPages(course, status, {}, files, { ...all, templates: false })!.templates[0].href).toBe('#template-assignment-3-f2026');
  });
  it('gives nothing for a read-only course or before any status is read', () => {
    expect(subPages({ ...course, write: false }, status, {}, files, all)).toBeUndefined();
    expect(subPages(course, { kind: 'loading' }, {}, files, all)).toBeUndefined();
  });
});
