import { describe, expect, it } from 'vitest';
import { GitHubClient } from '../src/github/client';
import { discoverCourses, parseRegistry, termOf } from '../src/model/discovery';
import { FakeGitHub, fileBody } from './fake';

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
    const courses = await discoverCourses(new GitHubClient({ token: () => 't', fetch: estate().fetch }));
    expect(courses.map((c) => c.org)).toEqual(['hertie-ml-e1234', 'hertie-ids-c11']);
    expect(courses.map((c) => c.write)).toEqual([true, false]);
  });

  it('reads the cohorts, newest first, with their term labels and the course identity', async () => {
    const [ml] = await discoverCourses(new GitHubClient({ token: () => 't', fetch: estate().fetch }));
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
    const courses = await discoverCourses(new GitHubClient({ token: () => 't', fetch: gh.fetch }));
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
