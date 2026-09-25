// The student screens and their sources: the site source behind StudentData, a student's own
// repos, team, receipts and gradebook, This week, and each screen's render.

import { render } from 'preact-render-to-string';
import { describe, expect, it } from 'vitest';
import { GitHubClient } from '../src/github/client';
import type { Semester } from '../src/model/discovery';
import { isMarked, markEntry, parseGradebook, readMine, readReceipts, unitOf, type Mine } from '../src/model/mine';
import { FRESH_MS, SiteSource, cutoffFrom, instant, myState, startOfDay, type SemesterFacts } from '../src/model/student';
import { weekItems } from '../src/model/week';
import { ArchiveNotice, AssignmentsView, InstructorsView, MarksView, ScheduleView, StudentScreen, WeekList } from '../src/screens/Student';
import { FakeGitHub, fileBody } from './fake';
import { FILES, GRADES, ORG, SITE } from './fixtures/site';

const LOGIN = 'octo-student';
const text = (v: preact.VNode) => render(v).replace(/<[^>]+>/g, ' ').replace(/&amp;/g, '&').replace(/&rsquo;/g, '’').replace(/\s+/g, ' ');

function siteFake(fake = new FakeGitHub()): FakeGitHub {
  for (const dir of ['_lectures', '_events', '_assignments', '_announcements']) {
    const names = Object.keys(FILES).filter((f) => f.startsWith(`${dir}/`));
    fake.on('GET', `/repos/${ORG}/${SITE}/contents/${dir}`, names.map((p) => ({ name: p.split('/')[1], path: p, sha: `sha-${p}`, type: 'file' })));
  }
  for (const [p, t] of Object.entries(FILES)) fake.on('GET', `/repos/${ORG}/${SITE}/contents/${p}`, fileBody(p, t));
  return fake;
}

const client = (fake: FakeGitHub) => new GitHubClient({ token: () => 't', fetch: fake.fetch });

async function demoFacts(): Promise<SemesterFacts> {
  return (await new SiteSource(client(siteFake())).facts(ORG))!;
}

describe('the site source behind StudentData', () => {
  it('leaves a row the site keeps off its schedule off the student schedule too', async () => {
    const fake = new FakeGitHub();
    const files = {
      ...FILES,
      '_lectures/readings-extra.md': '---\nkind: readings\ntype: readings\ndate: 2026-09-02T09:00:00\ntitle: "Readings"\noff_schedule: true\nlinks: []\n---\n',
      '_lectures/lab-week-2.md': '---\nkind: lab\ntype: lab\ntitle: "Lab"\nundated: true\nlinks: []\n---\n',
    };
    for (const dir of ['_lectures', '_events', '_assignments', '_announcements']) {
      const names = Object.keys(files).filter((f) => f.startsWith(`${dir}/`));
      fake.on('GET', `/repos/${ORG}/${SITE}/contents/${dir}`, names.map((p) => ({ name: p.split('/')[1], path: p, sha: `sha-${p}`, type: 'file' })));
    }
    for (const [p, t] of Object.entries(files)) fake.on('GET', `/repos/${ORG}/${SITE}/contents/${p}`, fileBody(p, t));
    const f = (await new SiteSource(client(fake)).facts(ORG))!;
    expect(f.rows.map((r) => r.id)).not.toContain('readings-extra');
    expect(f.rows.map((r) => r.id)).not.toContain('lab-week-2');
    expect(f.rows.map((r) => r.id)).toContain('session-01');
  });

  it('reads the rows by kind, the assignments, the cards and the archive date from the public site repo', async () => {
    const f = await demoFacts();
    const kinds = f.rows.map((r) => `${r.id}:${r.kind}`).sort();
    expect(kinds).toContain('session-01:lecture');
    expect(kinds).toContain('lab-03:lab');
    expect(kinds).toContain('01-midterm-exam:exam');
    expect(kinds).toContain('term-start:term_date');
    expect(kinds).toContain('02-assignment-2:handout:assignment');
    expect(kinds).toContain('02-assignment-2:due:due');
    const s1 = f.rows.find((r) => r.id === 'session-01')!;
    expect(s1.links[0]).toMatchObject({ repo: 'materials', path: 'lectures/01_deep-learning-in-public-policy/Session1_E1394_DL_preLecture.pdf' });
    expect(f.rows.find((r) => r.id === 'session-12')!.released).toBe(false);
    expect(f.rows.find((r) => r.id === 'term-start')!.allDay).toBe(true);
    expect(f.archive).toBe('2027-01-12T09:00:00');
    expect(f.rows.find((r) => r.id === 'cohort-archived')).toMatchObject({ title: 'Semester archived', details: '', allDay: true });
    expect(f.materialsRepos).toEqual(['materials']);
    expect(f.latePolicy).toHaveLength(2);
  });

  it('reads each assignment’s shape, dates and team formation', async () => {
    const f = await demoFacts();
    const a = Object.fromEntries(f.assignments.map((x) => [x.slug, x]));
    expect(a['assignment-2']).toMatchObject({ submitVia: 'assignment_repo', privateRepo: true, group: false, due: '2026-09-29T23:59:00', lateCutoff: '2026-10-09T23:59:00', lateRule: '10% per day, up to 10 days', maxPoints: '25', handedOut: true });
    expect(a['assignment-3-project']).toMatchObject({ group: true, teamFormation: { closes: '26th Oct', cap: 3 } });
    expect(a['assignment-6']).toMatchObject({ submitVia: 'shared_dropbox_repo', privateRepo: false, group: false });
    expect(a['assignment-7']).toMatchObject({ submitVia: 'external', submitUrl: 'https://moodle.hertie-school.org/mod/assign/view.php?id=424242', lateCutoff: null });
    expect(a['assignment-8'].handedOut).toBe(false);
  });

  it('keeps only card pictures the page may load, and reads the site once per semester', async () => {
    const fake = siteFake();
    const src = new SiteSource(client(fake));
    const f = (await src.facts(ORG))!;
    expect(f.instructors.map((c) => [c.name, c.role, c.picture])).toEqual([
      ['Prof. Lynn Kaack, PhD', 'instructor', 'https://github.com/LynnKaack.png'],
      ['Henry Baker', 'teaching_assistant', `https://${ORG}.github.io/_images/pp/henrycgbaker.jpg`],
    ]);
    const n = fake.seen.length;
    await src.facts(ORG.toUpperCase());
    expect(fake.seen.length).toBe(n);
    let t = 0;
    const timed = new SiteSource(client(fake), () => t);
    await timed.facts(ORG);
    const m = fake.seen.length;
    t = FRESH_MS + 1;
    await timed.facts(ORG);
    expect(fake.seen.length).toBeGreaterThan(m);
    expect(fake.seen.every((s) => s.url.includes(`/repos/${ORG}/${SITE}/`))).toBe(true);
  });

  it('is null for a semester with no site', async () => {
    expect(await new SiteSource(client(new FakeGitHub())).facts('nobody-f2026')).toBeNull();
  });
});

describe('time and a student’s state', () => {
  it('reads wall-clock times in the semester’s zone, across the clock change', () => {
    expect(new Date(instant('2026-09-29T23:59:00')).toISOString()).toBe('2026-09-29T21:59:00.000Z');
    expect(new Date(instant('2026-11-10T23:59:00')).toISOString()).toBe('2026-11-10T22:59:00.000Z');
    expect(instant('2026-09-29T23:59:00+02:00')).toBe(Date.parse('2026-09-29T21:59:00Z'));
    expect(new Date(startOfDay(Date.parse('2026-09-24T23:30:00Z'))).toISOString()).toBe('2026-09-24T22:00:00.000Z');
  });

  it('takes the late cutoff from the late rule', () => {
    expect(cutoffFrom('2026-09-29T23:59:00', '10% per day, up to 10 days')).toBe('2026-10-09T23:59:00');
    expect(cutoffFrom('2026-09-29T23:59:00', 'accepted up to 7 days late')).toBe('2026-10-06T23:59:00');
    expect(cutoffFrom('2026-09-29T23:59:00', 'not accepted after the deadline')).toBe('2026-09-29T23:59:00');
    expect(cutoffFrom('2026-09-29T23:59:00', '')).toBeNull();
  });

  it('moves not handed out -> open -> late window -> marking -> marks returned', async () => {
    const a = (await demoFacts()).assignments.find((x) => x.slug === 'assignment-2')!;
    const at = (s: string) => Date.parse(s);
    expect(myState({ ...a, handedOut: false }, false, at('2026-08-30T10:00:00+02:00'))).toBe('not_handed_out');
    expect(myState(a, false, at('2026-09-24T10:00:00+02:00'))).toBe('open');
    expect(myState(a, false, at('2026-10-01T10:00:00+02:00'))).toBe('late_window');
    expect(myState(a, false, at('2026-10-10T10:00:00+02:00'))).toBe('marking');
    expect(myState(a, true, at('2026-10-10T10:00:00+02:00'))).toBe('returned');
  });
});

describe('a student’s own data', () => {
  const repo = (name: string, push: boolean) => ({ name, full_name: `${ORG}/${name}`, private: true, default_branch: 'main', html_url: '', permissions: { push, pull: true } });

  it('finds their own repo, their team’s (push only), the drop box, and nothing for external', async () => {
    const as = Object.fromEntries((await demoFacts()).assignments.map((x) => [x.slug, x]));
    const repos = [repo('assignment-2-octo-student', true), repo('assignment-2-someone', false), repo('assignment-3-project-team-beta', false), repo('assignment-3-project-team-alpha', true), repo('assignment-6-submissions', true)];
    expect(unitOf(as['assignment-2'], repos, 'Octo-Student')).toMatchObject({ repo: 'assignment-2-octo-student', team: null });
    expect(unitOf(as['assignment-3-project'], repos, LOGIN)).toMatchObject({ repo: 'assignment-3-project-team-alpha', team: 'team-alpha' });
    expect(unitOf(as['assignment-3-project'], repos.filter((r) => r.name !== 'assignment-3-project-team-alpha'), LOGIN)).toMatchObject({ repo: null, team: null });
    expect(unitOf(as['assignment-6'], repos, LOGIN)).toMatchObject({ repo: 'assignment-6-submissions', shared: true });
    expect(unitOf(as['assignment-7'], repos, LOGIN).repo).toBeNull();
  });

  it('reads the repo list, their own gradebook and their team, and no one else’s', async () => {
    const facts = await demoFacts();
    const fake = new FakeGitHub()
      .on('GET', new RegExp(`^/orgs/${ORG}/repos`), [repo('assignment-2-octo-student', true), repo('assignment-3-project-team-alpha', true)])
      .on('GET', `/repos/${ORG}/grades-${LOGIN}/contents/grades.yml`, fileBody('grades.yml', GRADES))
      .on('GET', new RegExp(`^/repos/${ORG}/grades-${LOGIN}/commits`), [{ commit: { committer: { date: '2026-09-23T08:00:00Z' } } }])
      .on('GET', new RegExp(`^/orgs/${ORG}/teams/assignment-3-project-team-alpha/members`), [{ login: LOGIN }, { login: 'mate-one' }]);
    const mine = await readMine(client(fake), ORG, LOGIN, facts.assignments);
    expect(mine.units['assignment-3-project']).toMatchObject({ team: 'team-alpha', members: [LOGIN, 'mate-one'] });
    expect(mine.gradebook?.updated).toBe('2026-09-23T08:00:00Z');
    expect(isMarked(mine.gradebook, 'assignment-2')).toBe(true);
    expect(isMarked(mine.gradebook, 'assignment-6')).toBe(false);
    const grades = fake.seen.filter((s) => s.url.includes('/grades-'));
    expect(grades.length).toBeGreaterThan(0);
    expect(grades.every((s) => s.url.includes(`/grades-${LOGIN}/`))).toBe(true);
  });

  it('reads a gradebook in today’s shape and in the per-question shape (WP-B3), missing fields blank', () => {
    const g = parseGradebook(GRADES)!;
    expect(g.entries['assignment-2']).toMatchObject({ finalGrade: '21', maxPoints: '25', score: { Q1: '9', Q2: '12' }, daysLate: '0' });
    expect(g.entries['assignment-2'].feedback).toContain('threshold');
    expect(g.entries['assignment-3-project']).toMatchObject({ team: 'team-alpha', teamFeedback: 'A convincing model card.' });
    expect(g.entries['assignment-6']).toMatchObject({ finalGrade: '', penalty: '-30%' });
    const b3 = markEntry({ final_grade: 30, feedback: { overall: 'Good.', Q1: 'Tidy.', Q2: null }, question_feedback: { Q3: 'Check units.' } });
    expect(b3).toMatchObject({ finalGrade: '30', feedback: 'Good.', questionFeedback: { Q1: 'Tidy.', Q3: 'Check units.' } });
    expect(parseGradebook('total: 88\nassignments: {}\n')?.total).toBe('88');
    expect(parseGradebook(': : :')).toBeNull();
  });

  it('finds the Submission receipts thread by its label and shows the newest receipt without its marks', async () => {
    const fake = new FakeGitHub()
      .on('GET', /\/issues\?labels=dsl-feedback/, [{ number: 1, title: 'Submission receipts', state: 'open', html_url: 'https://github.com/x/1', created_at: '', comments: 3, labels: [] }])
      .on('GET', /\/issues\/1\/comments/, [
        { id: 1, body: '<!-- dsl-receipt:abc:due -->\nRecorded `abc1234` at the deadline.', created_at: '2026-09-30T00:00:00Z', html_url: '' },
        { id: 2, body: '<!-- dsl-receipt:def:updated -->\nUpdated after a late push: `def5678`.', created_at: '2026-10-01T09:00:00Z', html_url: '' },
        { id: 3, body: '<!-- dsl-marks-returned:assignment-2 -->\nMarks returned: see your marks repo.', created_at: '2026-10-12T09:00:00Z', html_url: '' },
      ]);
    const r = await readReceipts(client(fake), ORG, 'assignment-2-octo-student');
    expect(r).toMatchObject({ url: 'https://github.com/x/1', last: { text: 'Updated after a late push: `def5678`.', when: '2026-10-01T09:00:00Z' } });
    expect(r!.thread.map((c) => c.kind)).toEqual(['receipt', 'receipt', 'marks']);
    expect(await readReceipts(client(new FakeGitHub().on('GET', /\/issues/, [])), ORG, 'r')).toBeNull();
  });
});

const NOW = Date.parse('2026-09-24T12:00:00+02:00');

async function mineFixture(): Promise<Mine> {
  return {
    units: {
      'assignment-2': { slug: 'assignment-2', repo: 'assignment-2-octo-student', team: null, members: null, shared: false },
      'assignment-3-project': { slug: 'assignment-3-project', repo: null, team: null, members: null, shared: false },
      'assignment-6': { slug: 'assignment-6', repo: 'assignment-6-submissions', team: null, members: null, shared: true },
    },
    gradebook: parseGradebook(GRADES, '2026-09-23T08:00:00Z'),
    auditor: false,
  };
}

describe('This week', () => {
  it('lists what is due, released and returned this week, and team formation, with no team noted', async () => {
    const items = weekItems(await demoFacts(), await mineFixture(), NOW);
    const t = items.map((i) => `${i.kind}|${i.text}${i.note ? `|${i.note}` : ''}`);
    expect(t).toContain('exam|Midterm Exam');
    expect(t).toContain('release|Lab 3: materials released');
    expect(t).toContain('hand_out|Assignment 6: Referee reports was handed out');
    expect(t).toContain('due|Assignment 2: Classification and evaluation is due');
    expect(t.some((x) => x.startsWith('marks|Marks returned (Assignment 2, Assignment 3 Project)'))).toBe(true);
    expect(t).toContain('teams|Team formation is open for Assignment 3 Project until 26th Oct|you have no team yet');
    expect(t.some((x) => x.includes('Session 12'))).toBe(false);
    const out = render(<WeekList items={items} tz="Europe/Berlin" org={ORG} />);
    expect(out).toContain(`href="?semester=${ORG}#assignments"`);
    expect(out).toContain('trow exam');
  });

  it('has no marks line or team note for an instructor’s Student view (no own data)', async () => {
    const t = weekItems(await demoFacts(), null, NOW);
    expect(t.some((i) => i.kind === 'marks')).toBe(false);
    expect(t.find((i) => i.kind === 'teams')?.note).toBeUndefined();
  });
});

describe('the screens', () => {
  it('Schedule groups the calendar by week, in the site’s kind colours, with my state on assignment rows', async () => {
    const out = render(<ScheduleView facts={await demoFacts()} mine={await mineFixture()} now={NOW} org={ORG} />);
    expect(out).toContain('Week 1 <span>from Mon 3 Aug</span>');
    expect(out).toMatch(/trow lec/);
    expect(out).toMatch(/trow lab/);
    expect(out).toMatch(/trow term/);
    expect(out).toMatch(/trow asg mine/);
    expect(out).toContain('not released yet');
    expect(out).toContain('today-line');
    expect(text(<ScheduleView facts={await demoFacts()} mine={await mineFixture()} now={NOW} org={ORG} />)).toContain('marks returned');
  });

  it('Assignments shows my repo, team, receipts, dates and late rule, and the hand-in place for external work', async () => {
    const receipts = { 'assignment-2-octo-student': { url: 'https://github.com/r/1', last: { text: 'Recorded `abc1234`.', when: '2026-09-30T00:00:00Z' }, body: '', thread: [] } };
    const v = <AssignmentsView org={ORG} facts={await demoFacts()} mine={await mineFixture()} now={NOW} studentView={false} receipts={receipts} />;
    const out = render(v);
    const t = text(v);
    expect(out).toContain(`href="https://github.com/${ORG}/assignment-2-octo-student"`);
    expect(t).toContain('Late cutoff Fri 9 Oct 23:59');
    expect(t).toContain('Late work 10% per day, up to 10 days');
    expect(t).toContain('Recorded abc1234');
    expect(t).toContain('You have no team yet: join or create one');
    expect(out).toContain('href="https://moodle.hertie-school.org/mod/assign/view.php?id=424242"');
    expect(t).toContain('Shared repo assignment-6-submissions');
    expect(t).toContain('marks returned');
  });

  it('Assignments in the Student view shows no one’s repo', async () => {
    const t = text(<AssignmentsView org={ORG} facts={await demoFacts()} mine={null} now={NOW} studentView />);
    expect(t).toContain('A student’s repo, team and receipts show here.');
    expect(t).not.toContain('octo-student');
  });

  it('Marks shows each final grade, penalty, overall and per-question feedback, and team feedback', async () => {
    const v = <MarksView org={ORG} login={LOGIN} facts={await demoFacts()} gradebook={parseGradebook(GRADES)} studentView={false} />;
    const t = text(v);
    expect(t).toContain('Assignment 2: Classification and evaluation 21 / 25');
    expect(t).toContain('Q1 9');
    expect(t).toContain('Team feedback A convincing model card.');
    expect(t).toContain('Late penalty -30%');
    expect(render(v)).toContain(`href="https://github.com/${ORG}/grades-${LOGIN}"`);
    expect(text(<MarksView org={ORG} login={LOGIN} facts={await demoFacts()} gradebook={null} studentView={false} />)).toContain('No marks yet');
    expect(text(<MarksView org={ORG} login={LOGIN} facts={await demoFacts()} gradebook={parseGradebook(GRADES)} studentView />)).not.toContain('21');
  });

  it('Instructors shows the cards, with initials where the picture cannot load', async () => {
    const out = render(<InstructorsView facts={await demoFacts()} />);
    expect(out).toContain('src="https://github.com/LynnKaack.png"');
    expect(text(<InstructorsView facts={await demoFacts()} />)).toContain('HB Henry Baker Teaching assistant; Research Engineer & Data Scientist');
  });

  it('the archive notice says when the semester becomes read-only', () => {
    expect(text(<ArchiveNotice when="2027-01-12T09:00:00" tz="Europe/Berlin" now={NOW} />)).toContain('This semester is archived on Tue 12 Jan 2027: every repository in it becomes read-only.');
    expect(text(<ArchiveNotice when="2026-01-12T09:00:00" tz="Europe/Berlin" now={NOW} />)).toContain('was archived');
  });

  it('an archived semester is history: a link to the org and nothing read', () => {
    const sem: Semester = { org: ORG, term: 'f2026', termLabel: 'Fall 2026', courseOrg: 'c', courseName: 'Deep Learning', archived: true, role: 'student' };
    const out = render(<StudentScreen semester={sem} screen="marks" studentView={false} />);
    expect(out).toContain(`href="https://github.com/${ORG}"`);
    expect(out).not.toContain('Reading');
  });
});

