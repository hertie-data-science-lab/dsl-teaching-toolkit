// The shell (decision 0021): the app-level top bar, two levels of navigation, the switcher
// without wizards, `#setup` landing on Profile, and the sign-in page's copy.

import { render } from 'preact-render-to-string';
import { describe, expect, it } from 'vitest';
import { App, createState } from '../src/app';
import { AppAuth } from '../src/auth/app';
import { ConsoleAuth } from '../src/auth/console';
import { PatAuth } from '../src/auth/pat';
import { GitHubClient } from '../src/github/client';
import type { Course } from '../src/model/discovery';
import { landing, movedHash, parseHash, parseSearch, resolveContext } from '../src/router';
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
  it('still sends a person with one writable course to Dashboard on sign-in', () => {
    expect(landing([course])).toBe('dashboard');
    expect(landing([course, { ...course, org: 'x' }])).toBe('home');
  });
});

describe('two levels', () => {
  const app = (hash: string) => {
    const gh = new FakeGitHub();
    const s = createState({ auth: new ConsoleAuth(new PatAuth({ store: null }), null), client: new GitHubClient({ token: () => 't', fetch: gh.fetch }) });
    s.user.value = user;
    s.estate.value = { courses: [course], semesters: [], roles: new Map(), kind: 'classic' };
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
    const out = render(<Topbar user={user} title="Instructor view" onSignOut={() => {}} />);
    expect(out).toMatch(/<a class="who" href="#profile" aria-label="Your profile">/);
    expect(out).toContain('Octo Cat');
    expect(out).not.toContain('Your setup');
    expect(out).not.toContain('hdr-links');
    expect(out).toContain('>Guide</a>');
    expect(out).toContain('>Sign out</button>');
  });
});

describe('switcher and side nav', () => {
  it('lists no wizards, and All courses clears the query', () => {
    const out = render(<Sidenav courses={[course]} course={course} cohort={cohort} cohortStates={{}} current="dashboard" problems={0} />);
    expect(out).not.toContain('New course');
    expect(out).not.toContain('New semester');
    expect(out).toContain('<a href="?#home" role="menuitem">All courses</a>');
  });
  it('reads All courses on Home', () => {
    expect(render(<Sidenav courses={[course]} cohortStates={{}} current="home" problems={0} />)).toContain('<span>All courses</span>');
  });
  it('orders the external links Student view, Public site, Semester, Course', () => {
    const t = text(<Sidenav courses={[course]} course={course} cohort={cohort} cohortStates={{}} current="dashboard" problems={0} />);
    const at = ['Student view', 'Public site', 'Semester on GitHub', 'Course on GitHub'].map((l) => t.indexOf(l));
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
    expect(t).toContain('Where instructors run their courses and semesters, and students find their materials, assignments and marks.');
    expect(t).toContain(LINE);
    expect(t).not.toContain('approve the lab');
    expect(out.indexOf('>Sign in with GitHub</button>')).toBeGreaterThan(-1);
    expect(out.indexOf('>Sign in with GitHub</button>')).toBeLessThan(out.indexOf(LINE));
    expect(out).toMatch(/<p class="footnote">The console can see/);
    expect(out).toContain('<summary>Use a token instead</summary><div class="fold-body"><form');
  });
});
