import { describe, expect, it } from 'vitest';
import { GitHubClient } from '../src/github/client';
import { discoverEstate, parseRegistry, termOf, type TokenKind } from '../src/model/discovery';
import { FakeGitHub, fileBody, json } from './fake';

const discover = (gh: FakeGitHub, kind: TokenKind = 'classic') => discoverEstate(new GitHubClient({ token: () => 't', fetch: gh.fetch }), { kind, login: 'octo' });
const discoverCourses = async (gh: FakeGitHub) => (await discover(gh)).courses;

function estate() {
  return new FakeGitHub()
    .on('GET', '/user/orgs?per_page=100&page=1', [{ login: 'hertie-ml-e1234' }, { login: 'hertie-ids-c11' }, { login: 'some-other-org' }, { login: 'no-dotgithub' }])
    .on('GET', '/repos/hertie-ml-e1234/.github', { name: '.github', topics: ['dsl-course-hub'], permissions: { push: true } })
    .on('GET', '/repos/hertie-ids-c11/.github', { name: '.github', topics: ['dsl-course-hub'], permissions: { push: false, pull: true } })
    .on('GET', '/repos/some-other-org/.github', { name: '.github', topics: ['profile'], permissions: { push: true } })
    .on('GET', '/repos/hertie-ml-e1234/.github/contents/cohort-courses-pages.yml', fileBody('cohort-courses-pages.yml', 'cohorts:\n  - hertie-ml-s2026\n  - hertie-ml-f2026\n'))
    .on('GET', '/repos/hertie-ml-e1234/.github/contents/dsl-course.yml', fileBody('dsl-course.yml', 'course_name: Machine Learning\ncourse_code: E1234\npeople:\n  course_admins:\n    - github_handle: a-example\n'))
    .on('GET', '/repos/hertie-ids-c11/.github/contents/cohort-courses-pages.yml', fileBody('x', '- hertie-ids-f2026\n'))
    .on('GET', '/repos/hertie-ids-c11/.github/contents/dsl-course.yml', fileBody('x', 'course_name: Intro to Data Science\ncourse_code: C11\n'));
}

describe('discovery', () => {
  it('keeps only orgs whose .github carries dsl-course-hub, writable first', async () => {
    const courses = await discoverCourses(estate());
    expect(courses.map((c) => c.org)).toEqual(['hertie-ml-e1234', 'hertie-ids-c11']);
    expect(courses.map((c) => c.write)).toEqual([true, false]);
  });

  it('reads the cohorts, newest first, with their term labels and the course identity', async () => {
    const [ml] = await discoverCourses(estate());
    expect(ml.name).toBe('Machine Learning');
    expect(ml.code).toBe('E1234');
    expect(ml.admins).toEqual(['a-example']);
    expect(ml.cohorts.map((c) => [c.org, c.termLabel])).toEqual([['hertie-ml-f2026', 'Fall 2026'], ['hertie-ml-s2026', 'Spring 2026']]);
  });

  it('falls back to the topics endpoint when the repo object carries none', async () => {
    const gh = new FakeGitHub()
      .on('GET', '/user/orgs?per_page=100&page=1', [{ login: 'o' }])
      .on('GET', '/repos/o/.github', { name: '.github', permissions: { push: true } })
      .on('GET', '/repos/o/.github/topics', { names: ['dsl-course-hub'] });
    const courses = await discoverCourses(gh);
    expect(courses).toHaveLength(1);
    expect(courses[0].cohorts).toEqual([]);
  });

  it('parses both registry shapes and nothing else', () => {
    expect(parseRegistry('cohorts: [a, b]')).toEqual(['a', 'b']);
    expect(parseRegistry('- a\n- b\n')).toEqual(['a', 'b']);
    expect(parseRegistry('just a string')).toEqual([]);
    expect(parseRegistry(': : :')).toEqual([]);
    expect(parseRegistry(null)).toEqual([]);
  });

  it('labels terms', () => {
    expect(termOf('hertie-dsl-demo-f2026')).toEqual({ term: 'f2026', label: 'Fall 2026' });
    expect(termOf('hertie-x-s2027').label).toBe('Spring 2027');
  });
});

// ----------------------------------------------------------------------------- estate

const MEMBERSHIPS = '/user/memberships/orgs?state=active&per_page=100&page=1';
const COURSE = 'hertie-dsl-demo-course-e1234';
const SEM = 'hertie-dsl-demo-f2026';
const OLD = 'hertie-dsl-demo-f2025';
const OTHER_SEM = 'hertie-nlp-f2026';
const meta = (course: string) => fileBody('dsl-course.yml', `# SYSTEM-OWNED\ncourse: ${course}\norg: x\n`);

/** A course, two of its semesters, and another course's semester, with `push` on the ones given. */
function orgs(gh: FakeGitHub, push: string[] = [], archived: string[] = []) {
  const dot = (org: string, topics: string[]) => ({ name: '.github', topics, archived: archived.includes(org), permissions: { push: push.includes(org), pull: true } });
  return gh
    .on('GET', `/repos/${COURSE}/.github`, dot(COURSE, ['course-e1234', 'dsl-course-hub']))
    .on('GET', `/repos/${COURSE}/.github/contents/cohort-courses-pages.yml`, fileBody('x', `cohorts:\n  - ${OLD}\n  - ${SEM}\n`))
    .on('GET', `/repos/${COURSE}/.github/contents/dsl-course.yml`, fileBody('x', 'course_name: Machine Learning\ncourse_code: E1234\n'))
    .on('GET', `/repos/${SEM}/.github`, dot(SEM, ['dsl-cohort']))
    .on('GET', `/repos/${SEM}/.github/contents/dsl-course.yml`, meta(COURSE))
    .on('GET', `/repos/${OLD}/.github`, dot(OLD, ['dsl-semester']))
    .on('GET', `/repos/${OLD}/.github/contents/dsl-course.yml`, meta(COURSE))
    .on('GET', `/repos/${OTHER_SEM}/.github`, dot(OTHER_SEM, ['dsl-cohort']))
    .on('GET', `/repos/${OTHER_SEM}/.github/contents/dsl-course.yml`, meta('hertie-nlp-e1282'))
    .on('GET', '/repos/hertie-nlp-e1282/.github/contents/dsl-course.yml', fileBody('x', 'course_name: Natural Language Processing\n'));
}

const paths = (gh: FakeGitHub) => gh.seen.map((r) => r.url.replace('https://api.github.com', ''));

describe('discovery per token kind', () => {
  it('classic: merges /user/orgs with the active memberships, case-insensitively, and asks nothing else', async () => {
    const gh = orgs(new FakeGitHub(), [COURSE])
      .on('GET', '/user/orgs?per_page=100&page=1', [{ login: COURSE }])
      .on('GET', MEMBERSHIPS, [
        { state: 'active', role: 'admin', organization: { login: COURSE.toUpperCase() } },
        { state: 'active', role: 'member', organization: { login: OTHER_SEM } },
      ]);
    const e = await discover(gh);
    expect(e.kind).toBe('classic');
    expect(e.courses.map((c) => c.org)).toEqual([COURSE]);
    expect(e.semesters.map((s) => s.org)).toEqual([OTHER_SEM]);
    const asked = paths(gh);
    expect(asked.filter((p) => p.startsWith(`/repos/${COURSE}/.github`) && !p.includes('contents')).length).toBe(1);
    expect(asked.some((p) => p.startsWith('/user/repos') || p.startsWith('/user/installations') || p.startsWith('/user/memberships/orgs/'))).toBe(false);
  });

  it('GitHub App: adds the organisations of the visible installations, keeping only those the person is a member of', async () => {
    const gh = orgs(new FakeGitHub())
      .on('GET', '/user/orgs?per_page=100&page=1', [])
      .on('GET', MEMBERSHIPS, [])
      .on('GET', '/user/installations?per_page=100&page=1', {
        total_count: 3,
        installations: [
          { account: { login: SEM, type: 'Organization' } },
          { account: { login: OTHER_SEM, type: 'Organization' } },
          { account: { login: 'octo', type: 'User' } },
        ],
      })
      .on('GET', `/user/memberships/orgs/${SEM}`, { state: 'active', role: 'member', organization: { login: SEM } })
      .on('GET', `/user/memberships/orgs/${OTHER_SEM}`, { state: 'pending', role: 'member', organization: { login: OTHER_SEM } });
    const e = await discover(gh, 'app');
    expect(e.semesters.map((s) => [s.org, s.role])).toEqual([[SEM, 'student']]);
    expect(e.roles.has(OTHER_SEM)).toBe(false);
    expect(paths(gh)).not.toContain('/user/memberships/orgs/octo');
  });

  it('fine-grained: never trusts /user/orgs; finds the orgs through its repos and public memberships and checks each', async () => {
    const gh = orgs(new FakeGitHub(), [COURSE])
      .on('GET', '/user/repos?per_page=100&page=1', [{ owner: { login: COURSE, type: 'Organization' } }, { owner: { login: 'octo', type: 'User' } }])
      .on('GET', '/users/octo/orgs?per_page=100&page=1', [{ login: SEM }, { login: OTHER_SEM }])
      .on('GET', `/user/memberships/orgs/${COURSE}`, { state: 'active', role: 'member', organization: { login: COURSE } })
      .on('GET', `/user/memberships/orgs/${SEM}`, { state: 'active', role: 'member', organization: { login: SEM } })
      .on('GET', `/user/memberships/orgs/${OTHER_SEM}`, () => json({ message: 'Resource not accessible by personal access token' }, 403));
    const e = await discover(gh, 'fine-grained');
    expect(e.courses.map((c) => c.org)).toEqual([COURSE]);
    expect(e.semesters.map((s) => s.org)).toEqual([SEM]);
    expect(e.roles.has(OTHER_SEM)).toBe(false);
    expect(paths(gh).some((p) => p.startsWith('/user/orgs') || p.startsWith('/user/memberships/orgs?'))).toBe(false);
  });

  it('fine-grained that reaches no org: an empty estate, and the kind says why', async () => {
    const e = await discover(new FakeGitHub().on('GET', '/user/repos?per_page=100&page=1', []), 'fine-grained');
    expect(e).toMatchObject({ courses: [], semesters: [], kind: 'fine-grained' });
    expect(e.roles.size).toBe(0);
  });

  it('a listing that fails counts as empty', async () => {
    const gh = orgs(new FakeGitHub())
      .on('GET', '/user/orgs?per_page=100&page=1', () => json({ message: 'boom' }, 500))
      .on('GET', MEMBERSHIPS, [{ state: 'active', role: 'member', organization: { login: SEM } }]);
    expect((await discover(gh)).semesters.map((s) => s.org)).toEqual([SEM]);
  });
});

describe('roles', () => {
  const member = (logins: string[]) => logins.map((login) => ({ state: 'active', role: 'member', organization: { login } }));

  it('push on .github makes an instructor; membership alone a student; a person may be both across orgs', async () => {
    const gh = orgs(new FakeGitHub(), [SEM]).on('GET', MEMBERSHIPS, member([SEM, OTHER_SEM]));
    const e = await discover(gh);
    expect(Object.fromEntries(e.roles)).toEqual({ [SEM]: 'instructor', [OTHER_SEM]: 'student' });
    expect(e.semesters.map((s) => [s.org, s.role])).toEqual([[SEM, 'instructor'], [OTHER_SEM, 'student']]);
  });

  it('a writable course makes its registered semesters the instructor’s, even where .github gives no push', async () => {
    const gh = orgs(new FakeGitHub(), [COURSE]).on('GET', MEMBERSHIPS, member([COURSE, SEM]));
    const e = await discover(gh);
    expect(Object.fromEntries(e.roles)).toEqual({ [COURSE]: 'instructor', [SEM]: 'instructor', [OLD]: 'instructor' });
    expect(e.semesters.map((s) => s.role)).toEqual(['instructor']);
  });

  it('a read-only course carries no role; its semester, joined as a student, is a student’s', async () => {
    const gh = orgs(new FakeGitHub()).on('GET', MEMBERSHIPS, member([COURSE, SEM]));
    const e = await discover(gh);
    expect(e.courses.map((c) => c.write)).toEqual([false]);
    expect(Object.fromEntries(e.roles)).toEqual({ [SEM]: 'student' });
  });

  it('names each semester after its course, reading the course’s public .github when the person is not in it, and marks archived ones', async () => {
    const gh = orgs(new FakeGitHub(), [], [OLD]).on('GET', MEMBERSHIPS, member([COURSE, OLD, SEM, OTHER_SEM]));
    const e = await discover(gh);
    expect(e.semesters.map((s) => [s.org, s.courseName, s.termLabel, s.archived])).toEqual([
      [SEM, 'Machine Learning', 'Fall 2026', false],
      [OTHER_SEM, 'Natural Language Processing', 'Fall 2026', false],
      [OLD, 'Machine Learning', 'Fall 2025', true],
    ]);
  });

  it('an org whose .github carries neither topic is not shown', async () => {
    const gh = new FakeGitHub()
      .on('GET', MEMBERSHIPS, member(['plain-org']))
      .on('GET', '/repos/plain-org/.github', { name: '.github', topics: ['profile'], permissions: { push: true } });
    const e = await discover(gh);
    expect([e.courses.length, e.semesters.length, e.roles.size]).toEqual([0, 0, 0]);
  });
});
