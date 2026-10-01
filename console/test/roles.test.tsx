// The role and org model's shell: Home's two groups, the semester toggle, the student
// shell and the instructor's Student view, the mode, and the token kind.

import { render } from 'preact-render-to-string';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ConsoleAuth } from '../src/auth/console';
import { PatAuth } from '../src/auth/pat';
import type { Course, Estate, Role, Semester } from '../src/model/discovery';
import { currentOnly, saveCurrentOnly, type PrefStore } from '../src/model/prefs';
import { modeOf, parseSearch, studentContext, studentLanding } from '../src/router';
import { HomeScreen, studentPast } from '../src/screens/Home';
import type { SemesterFacts } from '../src/model/student';
import { StudentScreen, studentScreen } from '../src/screens/Student';
import { CourseBanner, Sidenav, StudentNav, Topbar } from '../src/ui/shell';
import { FakeGitHub, json } from './fake';

const user = { login: 'octo', id: 1, name: 'Octo Cat', email: null, avatar_url: '' };
const COURSE_ORG = 'hertie-dsl-demo-course-e1234';
const cohort = { org: 'hertie-dsl-demo-f2026', term: 'f2026', termLabel: 'Fall 2026' };
const course: Course = { org: COURSE_ORG, name: 'Machine Learning', code: 'E1234', description: '', write: true, admins: [], cohorts: [cohort], meta: null };
const sem = (org: string, over: Partial<Semester> = {}): Semester => ({
  org, term: 'f2026', termLabel: 'Fall 2026', courseOrg: 'hertie-nlp-e1282', courseName: 'Natural Language Processing', archived: false, role: 'student', ...over,
});
const NLP = sem('hertie-nlp-f2026');
const NLP_OLD = sem('hertie-nlp-f2025', { term: 'f2025', termLabel: 'Fall 2025', archived: true });
const estate = (courses: Course[], semesters: Semester[], roles: [string, Role][]): Estate => ({ courses, semesters, roles: new Map(roles), kind: 'classic' });
const text = (v: preact.VNode) => render(v).replace(/<[^>]+>/g, ' ').replace(/&amp;/g, '&').replace(/&rsquo;/g, '’').replace(/\s+/g, ' ');

afterEach(() => vi.unstubAllGlobals());

describe('Home groups', () => {
  it('a student-only account lands on Your semesters, archived ones greyed, with no instructor actions', () => {
    const out = render(<HomeScreen courses={[]} semesters={[NLP, NLP_OLD]} cohortStates={{}} now={0} user={user} />);
    expect(out).toContain('<h1>Your semesters <span class="hint-wrap">');
    // This semester first, then Past semesters, as the instructor's page has them (decision 0029 rule 3).
    expect(out.indexOf('This semester')).toBeLessThan(out.indexOf('hertie-nlp-f2026#week'));
    expect(out.indexOf('hertie-nlp-f2026#week')).toBeLessThan(out.indexOf('Past semesters'));
    expect(out.indexOf('Past semesters')).toBeLessThan(out.indexOf('hertie-nlp-f2025#week'));
    expect(out).not.toContain('Open the semester');
    expect(out).not.toContain('Show these semesters');
    expect(out).not.toContain('New course');
    expect(out).not.toContain('Your courses');
    expect(out).toContain('Natural Language Processing, Fall 2026');
    expect(out).toMatch(/cohort-card past" href="\?semester=hertie-nlp-f2025#week"/);
    expect(out).toMatch(/cohort-card" href="\?semester=hertie-nlp-f2026#week"/);
    expect(text(<HomeScreen courses={[]} semesters={[NLP_OLD]} cohortStates={{}} now={0} user={user} />)).toContain('Archived');
  });

  it('a student in two semesters sees each once: the running one under This semester, the ended one under Past semesters (decision 0031)', () => {
    // The demo's test student: Fall 2025 ended but is not archived; no catalogue sections.
    const now = Date.parse('2026-10-01T12:00:00Z');
    const f2025 = sem('hertie-dsl-demo-f2025', { term: 'f2025', termLabel: 'Fall 2025', courseOrg: COURSE_ORG, courseName: 'Deep Learning', courseCode: 'E1234' });
    const f2026 = sem('hertie-dsl-demo-f2026', { courseOrg: COURSE_ORG, courseName: 'Deep Learning', courseCode: 'E1234' });
    const out = render(<HomeScreen courses={[]} semesters={[f2026, f2025]} cohortStates={{}} now={now} user={user} />);
    for (const org of [f2025.org, f2026.org]) expect(out.split(`href="?semester=${org}#week"`)).toHaveLength(2);
    expect(out.indexOf('This semester')).toBeLessThan(out.indexOf(`${f2026.org}#week`));
    expect(out.indexOf(`${f2026.org}#week`)).toBeLessThan(out.indexOf('Past semesters'));
    expect(out.indexOf('Past semesters')).toBeLessThan(out.indexOf(`${f2025.org}#week`));
    expect(out).toContain(`cohort-card past" href="?semester=${f2025.org}#week"`);
    expect(out).not.toContain('DSL courses');
    expect(out).not.toContain('read only');
    expect(out).toContain('<span class="cc-code">E1234</span>');
    expect(text(<HomeScreen courses={[]} semesters={[f2026, f2025]} cohortStates={{}} now={now} user={user} />)).toContain('Ended');
    // Its past semester offers Current only, though nothing is archived.
    expect(out).toContain('Current only');
  });

  it('judges a student’s semester past by archive, then its last day, then its key', () => {
    const end = { end: '2026-12-19' } as SemesterFacts;
    expect(studentPast(NLP, end, Date.parse('2026-12-19T22:00:00Z'))).toBe(false);
    expect(studentPast(NLP, end, Date.parse('2026-12-20T01:00:00Z'))).toBe(true);
    expect(studentPast(NLP_OLD, null, 0)).toBe(true);
    // No facts: Fall 2026's key ends it on 1 February 2027.
    expect(studentPast(NLP, undefined, Date.parse('2027-01-31T00:00:00Z'))).toBe(false);
    expect(studentPast(NLP, undefined, Date.parse('2027-02-02T00:00:00Z'))).toBe(true);
  });

  it('a person with both roles sees All courses, then Your semesters', () => {
    const out = render(<HomeScreen courses={[course]} semesters={[NLP]} cohortStates={{}} now={0} user={user} />);
    expect(out).toContain('<h1>All courses <span class="hint-wrap">');
    expect(out.indexOf('Machine Learning, Fall 2026')).toBeLessThan(out.indexOf('Your semesters'));
    expect(out.indexOf('Your semesters')).toBeLessThan(out.indexOf('Natural Language Processing, Fall 2026'));
  });

  it('a fine-grained token that sees no org says to add the orgs under Resource owner', () => {
    const t = text(<HomeScreen courses={[]} semesters={[]} kind="fine-grained" cohortStates={{}} now={0} user={user} />);
    expect(t).toContain('This token can see no course or semester orgs; when creating it, add the orgs under Resource owner and grant the organisation permission Members: read');
    expect(render(<HomeScreen courses={[]} semesters={[]} kind="fine-grained" cohortStates={{}} now={0} user={user} />)).toContain('https://github.com/settings/personal-access-tokens');
    expect(text(<HomeScreen courses={[]} semesters={[]} kind="classic" cohortStates={{}} now={0} user={user} />)).toContain('No courses found');
  });

  it('Current only hides the past semesters, per viewer, and is offered only when there are some', () => {
    const map = new Map<string, string>();
    vi.stubGlobal('localStorage', { getItem: (k: string) => map.get(k) ?? null, setItem: (k: string, v: string) => void map.set(k, v) });
    expect(render(<HomeScreen courses={[]} semesters={[NLP]} cohortStates={{}} now={0} user={user} />)).not.toContain('Current only');
    saveCurrentOnly('octo', true);
    const out = render(<HomeScreen courses={[]} semesters={[NLP, NLP_OLD]} cohortStates={{}} now={0} user={user} />);
    expect(out).toMatch(/<label class="check my-only"><input type="checkbox" checked[^>]*\/?><span>Current only<\/span>/);
    expect(out).not.toContain('href="?semester=hertie-nlp-f2025#week"');
    expect(out).not.toContain('Past semesters');
    expect(out).toContain('href="?semester=hertie-nlp-f2026#week"');
    const other = render(<HomeScreen courses={[]} semesters={[NLP, NLP_OLD]} cohortStates={{}} now={0} user={{ ...user, login: 'someone-else' }} />);
    expect(other).toContain('href="?semester=hertie-nlp-f2025#week"');
  });

  it('the toggle survives storage that is missing or refuses', () => {
    const refusing: PrefStore = { getItem: () => { throw new Error('denied'); }, setItem: () => { throw new Error('denied'); } };
    expect(currentOnly('octo', refusing)).toBe(false);
    expect(() => saveCurrentOnly('octo', true, refusing)).not.toThrow();
    expect(currentOnly('octo', null)).toBe(false);
    vi.stubGlobal('localStorage', undefined);
    expect(render(<HomeScreen courses={[]} semesters={[NLP]} cohortStates={{}} now={0} user={user} />)).toContain('?semester=hertie-nlp-f2026#week');
  });

  it('a student with exactly one live semester and no instructor role lands on it', () => {
    expect(studentLanding(estate([], [NLP, NLP_OLD], [[NLP.org, 'student'], [NLP_OLD.org, 'student']]))).toBe(NLP.org);
    expect(studentLanding(estate([], [NLP, sem('hertie-maths-f2026')], [[NLP.org, 'student']]))).toBeNull();
    expect(studentLanding(estate([], [NLP_OLD], [[NLP_OLD.org, 'student']]))).toBeNull();
    expect(studentLanding(estate([course], [NLP], [[COURSE_ORG, 'instructor'], [NLP.org, 'student']]))).toBeNull();
  });
});

describe('mode and the student shell', () => {
  const both = estate([course], [NLP], [[COURSE_ORG, 'instructor'], [cohort.org, 'instructor'], [NLP.org, 'student']]);

  it('a semester the person studies in opens their student screens; one they teach opens the Student view', () => {
    expect(studentContext(both, parseSearch(`?semester=${NLP.org}`))).toMatchObject({ semester: { org: NLP.org }, studentView: false });
    const view = studentContext(both, parseSearch(`?semester=${cohort.org}`));
    expect(view).toMatchObject({ studentView: true, semester: { org: cohort.org, courseName: 'Machine Learning', termLabel: 'Fall 2026' } });
    expect(studentContext(both, parseSearch('?semester=not-mine'))).toBeNull();
  });

  it('compares org names whatever their case', () => {
    expect(studentContext(both, parseSearch(`?semester=${NLP.org.toUpperCase()}`))?.semester.org).toBe(NLP.org);
    const mixed = estate([], [sem('Hertie-Maths-f2026')], [['hertie-maths-f2026', 'student']]);
    expect(studentContext(mixed, parseSearch('?semester=hertie-maths-f2026'))?.semester.org).toBe('Hertie-Maths-f2026');
  });

  it('a semester known only from a course registry waits for its .github before saying whether it is archived', () => {
    const q = parseSearch(`?semester=${cohort.org}`);
    expect(studentContext(both, q)).toMatchObject({ pending: true, semester: { archived: false } });
    expect(studentContext(both, q, () => true)).toMatchObject({ pending: false, semester: { archived: true } });
    expect(studentContext(both, q, () => false)).toMatchObject({ pending: false, semester: { archived: false } });
    expect(studentContext(both, parseSearch(`?semester=${NLP.org}`))).toMatchObject({ pending: false });
  });

  it('mode is student in a semester’s student screens, else instructor for anyone who teaches anywhere', () => {
    expect(modeOf(both, parseSearch(`?semester=${NLP.org}`))).toBe('student');
    expect(modeOf(both, parseSearch(`?cohort=${cohort.org}`))).toBe('instructor');
    expect(modeOf(both, parseSearch(''))).toBe('instructor');
    expect(modeOf(estate([], [NLP], [[NLP.org, 'student']]), parseSearch(''))).toBe('student');
  });

  it('a student whose older semester ended unarchived lands on the running one’s This week (decision 0031)', () => {
    const now = Date.parse('2026-10-01T12:00:00Z');
    const f2025 = sem('hertie-dsl-demo-f2025', { term: 'f2025', termLabel: 'Fall 2025' });
    const f2026 = sem('hertie-dsl-demo-f2026');
    expect(studentLanding(estate([], [f2026, f2025], [[f2026.org, 'student'], [f2025.org, 'student']]), now)).toBe(f2026.org);
  });

  it('the student nav marks a semester that ended unarchived as ended', () => {
    const old = sem('hertie-nlp-f2024', { term: 'f2024', termLabel: 'Fall 2024' });
    const nav = render(<StudentNav root="Your semesters" semesters={[old]} semester={old} current="week" />);
    expect(nav).toContain('<span class="nav-anchor">Fall 2024<span class="n-soon"> ended</span></span>');
    expect(nav).not.toContain('nav-dot');
  });

  it('the student nav lists the eight screens', () => {
    const nav = render(<StudentNav root="Your semesters" semesters={[NLP]} semester={NLP} current="marks" />);
    const labels = [...nav.matchAll(/<li><a href="\?semester=hertie-nlp-f2026#(\w+)"[^>]*>([^<]+)</g)].map((m) => [m[1], m[2]]);
    expect(labels).toEqual([['week', 'This week'], ['schedule', 'Schedule'], ['assignments', 'Assignments'], ['marks', 'Marks'], ['materials', 'Materials'], ['setup', 'Set up'], ['join', 'Join'], ['instructors', 'Instructors']]);
    expect(nav).toMatch(/#marks" aria-current="page"/);
    expect(nav).toContain('Your semesters');
    const t = text(<StudentScreen semester={NLP} screen="marks" studentView={false} />);
    expect(t).toContain('Marks');
    expect(t).not.toContain('Coming in D3.');
    expect(t).not.toContain('Student view');
    expect(studentScreen('nonsense')).toBe('week');
  });

  it('Student view shows a banner, the instructor’s own identity only, and a way back', () => {
    const s = studentContext(both, parseSearch(`?semester=${cohort.org}`))!.semester;
    expect(text(<StudentScreen semester={s} screen="week" studentView />)).toContain('Student view. What a student of this semester sees, shown with your own account: no student’s repos or marks.');
    const banner = render(<CourseBanner crumbs={[]} name={s.courseName} semester={{ org: s.org, termLabel: s.termLabel, view: 'back' }} />);
    expect(banner).toContain(`<a class="textlink" href="?cohort=${cohort.org}#dashboard">Back to instructor view</a>`);
    expect(banner).not.toContain('Student view');
  });

  it('the Student view is entered from the semester banner’s pill, not the side nav', () => {
    const nav = render(<Sidenav courses={[course]} course={course} cohort={cohort} cohortStates={{}} current="dashboard" />);
    expect(nav).not.toContain('Student view');
    expect(nav).not.toContain('Semester on GitHub');
    // The person's student semesters are on All courses, not in the course's tree.
    expect(nav).not.toContain(`?semester=${NLP.org}`);
    const banner = render(<CourseBanner crumbs={[]} name="Machine Learning" semester={{ org: cohort.org, termLabel: 'Fall 2026', view: 'student' }} />);
    expect(banner).toContain(`<a class="btn small quiet" href="?semester=${cohort.org}#week">Student view</a>`);
    expect(banner).toContain('<h1>Machine Learning</h1>');
    expect(banner).toContain('<span class="sem-title">Fall 2026</span>');
    expect(banner).toContain(`href="https://github.com/${cohort.org}"`);
  });

  it('the top bar’s view is a link back only in an instructor’s preview', () => {
    const user = { login: 'a', id: 1, name: 'A', email: null, avatar_url: '' };
    const preview = render(<Topbar user={user} title="Student view (preview)" titleHref={`?cohort=${cohort.org}#dashboard`} />);
    expect(preview).toContain(`<a class="app-view" href="?cohort=${cohort.org}#dashboard"><span class="long">Student view (preview)</span><span class="short">Preview</span></a>`);
    const real = render(<Topbar user={user} title="Student view" />);
    expect(real).toContain('DSL Teaching Console<small>Student view</small></a>');
    expect(real).not.toContain('app-view');
  });
});

describe('token kind', () => {
  it('tells classic from fine-grained from signed out', async () => {
    const user2 = { login: 'octo', id: 1, name: null, email: null, avatar_url: '' };
    const classic = new FakeGitHub().on('GET', '/user', () => json(user2, 200, { 'x-oauth-scopes': 'repo, workflow' }));
    const a = new ConsoleAuth(new PatAuth({ fetch: classic.fetch, store: null }), null);
    expect(a.kind()).toBeNull();
    await a.signIn('ghp_x');
    expect(a.kind()).toBe('classic');
    const fine = new FakeGitHub().on('GET', '/user', () => json(user2)).on('GET', /\/orgs\?per_page=100$/, () => json([]));
    const b = new ConsoleAuth(new PatAuth({ fetch: fine.fetch, store: null }), null);
    await b.signIn('github_pat_x');
    expect(b.kind()).toBe('fine-grained');
  });
});
