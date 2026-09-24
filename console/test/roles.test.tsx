// The role and org model's shell: Home's two groups, the semester toggle, the student
// shell and the instructor's Student view, the mode, and the token kind.

import { render } from 'preact-render-to-string';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ConsoleAuth } from '../src/auth/console';
import { PatAuth } from '../src/auth/pat';
import type { Course, Estate, Role, Semester } from '../src/model/discovery';
import { hiddenSemesters, saveHiddenSemesters, type PrefStore } from '../src/model/prefs';
import { modeOf, parseSearch, studentContext } from '../src/router';
import { HomeScreen } from '../src/screens/Home';
import { StudentScreen, studentScreen } from '../src/screens/Student';
import { Sidenav, StudentNav } from '../src/ui/shell';
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
    expect(out).toContain('<h1>Your semesters</h1>');
    expect(out).not.toContain('New course');
    expect(out).not.toContain('Your courses');
    expect(out).toContain('Natural Language Processing, Fall 2026');
    expect(out).toMatch(/cohort-card past" href="\?semester=hertie-nlp-f2025#week"/);
    expect(out).toMatch(/cohort-card" href="\?semester=hertie-nlp-f2026#week"/);
    expect(text(<HomeScreen courses={[]} semesters={[NLP_OLD]} cohortStates={{}} now={0} user={user} />)).toContain('Archived');
  });

  it('a person with both roles sees Your courses, then Your semesters', () => {
    const out = render(<HomeScreen courses={[course]} semesters={[NLP]} cohortStates={{}} now={0} user={user} />);
    expect(out).toContain('<h1>Your courses</h1>');
    expect(out.indexOf('Machine Learning, Fall 2026')).toBeLessThan(out.indexOf('Your semesters'));
    expect(out.indexOf('Your semesters')).toBeLessThan(out.indexOf('Natural Language Processing, Fall 2026'));
  });

  it('a fine-grained token that sees no org says to add the orgs under Resource owner', () => {
    const t = text(<HomeScreen courses={[]} semesters={[]} kind="fine-grained" cohortStates={{}} now={0} user={user} />);
    expect(t).toContain('This token can see no course or semester orgs; when creating it, add the orgs under Resource owner and grant the organisation permission Members: read');
    expect(render(<HomeScreen courses={[]} semesters={[]} kind="fine-grained" cohortStates={{}} now={0} user={user} />)).toContain('https://github.com/settings/personal-access-tokens');
    expect(text(<HomeScreen courses={[]} semesters={[]} kind="classic" cohortStates={{}} now={0} user={user} />)).toContain('No courses found');
  });

  it('Show these semesters hides what the viewer chose, per viewer, and still lists it to bring back', () => {
    const map = new Map<string, string>();
    vi.stubGlobal('localStorage', { getItem: (k: string) => map.get(k) ?? null, setItem: (k: string, v: string) => void map.set(k, v) });
    saveHiddenSemesters('octo', new Set([NLP_OLD.org]));
    const out = render(<HomeScreen courses={[]} semesters={[NLP, NLP_OLD]} cohortStates={{}} now={0} user={user} />);
    expect(out).not.toContain('href="?semester=hertie-nlp-f2025#week"');
    expect(out).toContain('href="?semester=hertie-nlp-f2026#week"');
    expect(out).toContain('Show these semesters');
    expect(text(<HomeScreen courses={[]} semesters={[NLP, NLP_OLD]} cohortStates={{}} now={0} user={user} />)).toContain('Natural Language Processing, Fall 2025');
    const other = render(<HomeScreen courses={[]} semesters={[NLP, NLP_OLD]} cohortStates={{}} now={0} user={{ ...user, login: 'someone-else' }} />);
    expect(other).toContain('href="?semester=hertie-nlp-f2025#week"');
  });

  it('the toggle survives storage that is missing or refuses', () => {
    const refusing: PrefStore = { getItem: () => { throw new Error('denied'); }, setItem: () => { throw new Error('denied'); } };
    expect(hiddenSemesters('octo', refusing)).toEqual(new Set());
    expect(() => saveHiddenSemesters('octo', new Set(['x']), refusing)).not.toThrow();
    expect(hiddenSemesters('octo', { getItem: () => '{not json', setItem: () => {} })).toEqual(new Set());
    expect(hiddenSemesters('octo', null)).toEqual(new Set());
    vi.stubGlobal('localStorage', undefined);
    expect(render(<HomeScreen courses={[]} semesters={[NLP]} cohortStates={{}} now={0} user={user} />)).toContain('?semester=hertie-nlp-f2026#week');
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

  it('the student nav lists the six screens, each a placeholder for now', () => {
    const nav = render(<StudentNav courses={[]} cohortStates={{}} semesters={[NLP]} semester={NLP} current="marks" />);
    const labels = [...nav.matchAll(/<li><a href="\?semester=hertie-nlp-f2026#(\w+)"[^>]*>([^<]+)</g)].map((m) => [m[1], m[2]]);
    expect(labels).toEqual([['week', 'This week'], ['schedule', 'Schedule'], ['assignments', 'Assignments'], ['marks', 'Marks'], ['materials', 'Materials'], ['instructors', 'Instructors']]);
    expect(nav).toMatch(/#marks" aria-current="page"/);
    expect(nav).toContain('Your semesters');
    const t = text(<StudentScreen semester={NLP} screen="marks" studentView={false} />);
    expect(t).toContain('Marks');
    expect(t).toContain('Coming in D3.');
    expect(t).not.toContain('Student view');
    expect(studentScreen('nonsense')).toBe('week');
  });

  it('Student view shows a banner, the instructor’s own identity only, and a way back', () => {
    const s = studentContext(both, parseSearch(`?semester=${cohort.org}`))!.semester;
    const out = render(<StudentScreen semester={s} screen="week" studentView />);
    expect(text(<StudentScreen semester={s} screen="week" studentView />)).toContain('Student view. What a student of Machine Learning, Fall 2026 sees, shown with your own account: no student’s repos or marks.');
    expect(out).toContain(`href="?cohort=${cohort.org}#cohort"`);
  });

  it('the instructor nav offers Student view on a semester they teach', () => {
    const nav = render(<Sidenav courses={[course]} semesters={[NLP]} course={course} cohort={cohort} cohortStates={{}} current="week" problems={0} />);
    expect(nav).toContain(`href="?semester=${cohort.org}#week">Student view`);
    expect(nav).toContain(`href="?semester=${NLP.org}#week"`);
    const ro = render(<Sidenav courses={[{ ...course, write: false }]} course={{ ...course, write: false }} cohort={cohort} cohortStates={{}} current="week" problems={0} />);
    expect(ro).not.toContain('Student view');
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
