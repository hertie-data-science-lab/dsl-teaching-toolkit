// The student screens' source once the engine writes `student-status.json` (WP-D4): the file
// read into the model, the site as the fallback for a semester without one, and Join requests
// opened through the API under the marker line the join workflows route on.

import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { parse } from 'yaml';
import { GitHubClient } from '../src/github/client';
import { JOIN_MARKERS, STUDENT_STATUS_PATH } from '../src/model/names';
import { StatusFileSource, factsFromStatus } from '../src/model/student';
import { isJoinRequest, joinCourseBody, joinTeamBody } from '../src/screens/StudentJoin';
import { FakeGitHub, fileBody } from './fake';

const ORG = 'hertie-dsl-demo-f2026';
// A render of the engine's own code (`tests/test_student_status.py` keeps it fresh).
const FILE = readFileSync(new URL('./fixtures/student-status.json', import.meta.url), 'utf8');
const DOC = JSON.parse(FILE);
const SCHEMA = JSON.parse(readFileSync(new URL('../schemas/student-status.schema.json', import.meta.url), 'utf8'));
const client = (fake: FakeGitHub) => new GitHubClient({ token: () => 't', fetch: fake.fetch });
const template = (rel: string) => readFileSync(new URL(`../../templates/join/${rel}`, import.meta.url), 'utf8');

describe('student-status.json', () => {
  it('is read from the semester’s .github, where the engine writes it', () => {
    expect(STUDENT_STATUS_PATH).toBe('.system/student-status.json');
    expect(Object.keys(SCHEMA.properties).sort()).toEqual(Object.keys(DOC).sort());
  });

  it('gives the screens the engine’s cutoff, timezone, kinds and archive date', () => {
    const f = factsFromStatus(DOC);
    const a1 = f.assignments.find((a) => a.slug === 'assignment-1')!;
    expect(a1.lateCutoff).toMatch(/^2026-10-07T23:59/);
    expect(a1.brief).toBe('Fit a line.');
    expect(a1.handedOut).toBe(true);
    expect(f.timezone).toBe('Europe/Berlin');
    expect(f.kinds?.lecture.label).toBe('Lecture');
    expect(f.archive).toBe('2027-01-31');
    expect(f.courseName).toBe('Deep Learning');
    expect(f.syllabus).toMatchObject({ repo: 'materials', path: 'SYLLABUS.md' });
    expect(f.materialsRepos).toEqual(['materials']);
  });

  it('keeps a team to its name, headcount and cap', () => {
    const p = factsFromStatus(DOC).assignments.find((a) => a.slug === 'assignment-2-project')!;
    expect(p.teams).toEqual([{ name: 'red-team', members: 2, cap: 3 }]);
    expect(p.teamFormation?.cap).toBe(3);
  });

  it('gives a lecture its files, with its readings the same links so the screen can tell them apart', () => {
    const lec = factsFromStatus(DOC).rows.find((r) => r.id === 'lecture_01')!;
    expect(lec.readings).toHaveLength(1);
    expect(lec.links).toContain(lec.readings[0]);
    expect(lec.readingList).toContain('Chapter 1.');
    expect(lec.links.filter((l) => !lec.readings.includes(l)).map((l) => l.name)).toEqual(['slides.pdf', 'code/ (2 files)']);
  });

  it('shows an email only where the file carries one', () => {
    const cards = factsFromStatus(DOC).instructors;
    expect(cards.map((c) => [c.role, c.email])).toEqual([['instructor', 'shown@hertie-school.org'], ['teaching_assistant', '']]);
  });

  it('reads the file once, and falls back to the site for a semester without one', async () => {
    const fake = new FakeGitHub().on('GET', `/repos/${ORG}/.github/contents/.system/student-status.json`, fileBody(STUDENT_STATUS_PATH, FILE));
    const src = new StatusFileSource(client(fake));
    const f = await src.facts(ORG);
    expect(f?.assignments.length).toBe(DOC.assignments.length);
    await src.facts(ORG);
    expect(fake.seen.filter((s) => s.url.includes('student-status.json'))).toHaveLength(1);
    expect(fake.seen.some((s) => s.url.includes('.github.io'))).toBe(false);

    const none = new FakeGitHub();
    await new StatusFileSource(client(none)).facts(ORG);
    expect(none.seen.some((s) => s.url.includes(`/repos/${ORG}/${ORG}.github.io/`))).toBe(true);
  });
});

describe('Join through the API', () => {
  it('writes the body the seeded workflows parse, under their marker line', () => {
    const onboard = template('onboard.yml');
    const team = template('team-formation.yml');
    expect(onboard).toContain(`startsWith(github.event.issue.body, '${JOIN_MARKERS.join_course}')`);
    expect(team).toContain(`startsWith(github.event.issue.body, '${JOIN_MARKERS.join_team}')`);
    const course = joinCourseBody(' dsl-ab3k9m ');
    expect(course.startsWith(JOIN_MARKERS.join_course)).toBe(true);
    expect(/Enrolment code\s*[\r\n]+\s*(dsl-[a-z0-9]{6})\b/i.exec(course)?.[1]).toBe('dsl-ab3k9m');
    // The workflow's own three expressions, read out of the seeded file.
    const expr = (name: string) => new RegExp(new RegExp(`const ${name} = \\(issue\\.body \\|\\| ''\\)\\.match\\(/(.+?)/i\\);`).exec(team)![1], 'i');
    const body = joinTeamBody('assignment-3-project', 'create', 'team-x');
    expect(body.startsWith(JOIN_MARKERS.join_team)).toBe(true);
    expect(expr('aMatch').exec(body)?.[1]).toBe('assignment-3-project');
    expect(expr('cMatch').exec(body)?.[1]).toBe('Create');
    expect(expr('tMatch').exec(body)?.[1]).toBe('team-x');
  });

  it('names the marker in each form, whose own path keeps its label', () => {
    expect(template('ISSUE_TEMPLATE/01-join-course.yml')).toContain(JOIN_MARKERS.join_course);
    expect((parse(template('ISSUE_TEMPLATE/02-join-team.yml')) as { labels: string[] }).labels).toEqual(['team-formation']);
  });

  it('counts an unlabelled issue with the marker as a request, and nothing else', () => {
    expect(isJoinRequest({ labels: [], body: joinCourseBody('dsl-ab3k9m') })).toBe(true);
    expect(isJoinRequest({ labels: [{ name: 'onboarding' }], body: '' })).toBe(true);
    expect(isJoinRequest({ labels: [], body: 'hello' })).toBe(false);
  });

  it('opens the issue as the student, with no labels', async () => {
    const fake = new FakeGitHub().on('POST', `/repos/${ORG}/join/issues`, { number: 7, title: 'Join course', state: 'open', html_url: '', created_at: '', comments: 0, labels: [] });
    const issue = await client(fake).createIssue(ORG, 'join', 'Join course', joinCourseBody('dsl-ab3k9m'));
    expect(issue.number).toBe(7);
    const post = fake.seen.find((s) => s.method === 'POST')!;
    expect(post.body).toEqual({ title: 'Join course', body: joinCourseBody('dsl-ab3k9m') });
  });
});
