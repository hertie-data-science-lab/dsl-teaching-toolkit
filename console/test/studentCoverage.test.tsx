// WP-D5, full coverage of what a student is told today: the brief, the teams with room, the
// whole receipts thread, the auditor, shape notes, a drop-box or external group's team,
// readings, the pending invitation, the About block, the small fields, the instructors'
// email and pictures, Set up, the timezone per semester, and archived semesters as history.

import { render } from 'preact-render-to-string';
import { describe, expect, it } from 'vitest';
import { EnvCtx, type Env } from '../src/env';
import { GitHubClient, type GhTeam } from '../src/github/client';
import { discoverEstate, invitationUrl, pendingOrgs, type Semester } from '../src/model/discovery';
import { forgetMyTeams, knownAuditor, parseGradebook, patchLines, readMine, readReceipts, teamOf, threadKind, type Mine } from '../src/model/mine';
import { forgetStudentPrefs, lastVisit, localPaths, markVisit, resetVisits, saveLocalPaths, type PrefStore } from '../src/model/prefs';
import { SiteSource, homeText, pictureOf, sitePicture, type SemesterAssignment, type SemesterFacts } from '../src/model/student';
import { weekItems } from '../src/model/week';
import { ArchivedSemester, AboutView, AssignmentsView, AuditorNote, InstructorsView, MarksView, ScheduleView, WeekList } from '../src/screens/Student';
import { AskedList, JoinScreen, TeamList, joinTeamUrl } from '../src/screens/StudentJoin';
import { ReadingsView, materialHref } from '../src/screens/StudentMaterials';
import { SetupView, cloneCommand, forkOf, joinPath, vscodeFolder } from '../src/screens/StudentSetup';
import { InvitedGroup } from '../src/screens/Home';
import { GhMd } from '../src/ui/rendered';
import { FakeGitHub, fileBody, json } from './fake';
import { StudentNav } from '../src/ui/shell';
import { FILES, GRADES, ORG, SITE } from './fixtures/site';

const LOGIN = 'octo-student';
const NOW = Date.parse('2026-09-24T12:00:00+02:00');
const text = (v: preact.VNode) => render(v).replace(/<[^>]+>/g, ' ').replace(/&amp;/g, '&').replace(/&rsquo;/g, '’').replace(/&quot;/g, '"').replace(/\s+/g, ' ');
const client = (fake: FakeGitHub) => new GitHubClient({ token: () => 't', fetch: fake.fetch });

function siteFake(fake = new FakeGitHub(), files: Record<string, string> = FILES): FakeGitHub {
  for (const dir of ['_lectures', '_events', '_assignments', '_announcements']) {
    const names = Object.keys(files).filter((f) => f.startsWith(`${dir}/`));
    fake.on('GET', `/repos/${ORG}/${SITE}/contents/${dir}`, names.map((p) => ({ name: p.split('/')[1], path: p, sha: `sha-${p}`, type: 'file' })));
  }
  for (const [p, t] of Object.entries(files)) fake.on('GET', `/repos/${ORG}/${SITE}/contents/${p}`, fileBody(p, t));
  return fake;
}
const facts = async (): Promise<SemesterFacts> => (await new SiteSource(client(siteFake())).facts(ORG))!;
const byslug = async () => Object.fromEntries((await facts()).assignments.map((a) => [a.slug, a]));
const repo = (name: string, push = true) => ({ name, full_name: `${ORG}/${name}`, private: true, default_branch: 'main', html_url: '', permissions: { push, pull: true } });
const team = (slug: string, org = ORG): GhTeam => ({ slug, name: slug, organization: { login: org } });

function mine(over: Partial<Mine> = {}): Mine {
  return {
    units: {
      'assignment-2': { slug: 'assignment-2', repo: 'assignment-2-octo-student', team: null, members: null, shared: false },
      'assignment-3-project': { slug: 'assignment-3-project', repo: 'assignment-3-project-team-latency', team: 'team-latency', members: [LOGIN], shared: false },
      'assignment-6': { slug: 'assignment-6', repo: 'assignment-6-submissions', team: null, members: null, shared: true },
      'assignment-9': { slug: 'assignment-9', repo: 'assignment-9-octo-student', team: null, members: null, shared: false },
    },
    gradebook: parseGradebook(GRADES, '2026-09-23T08:00:00Z'),
    auditor: false,
    ...over,
  };
}

const PATCH = '<!-- dsl-patch:abc123def456 -->\nThe instructors updated `data/train.csv` in this repository on 2026-09-23; pull before you continue. Your own commits are untouched.';
const THREAD_BODY = '<!-- dsl-course: receipts -->\n**Due:** Tue 29 Sep, 23:59\n**Team:** team-latency - fill in CONTRIBUTIONS.md before the deadline.';

function receiptsFake(): FakeGitHub {
  return new FakeGitHub()
    .on('GET', /\/issues\?labels=dsl-receipts/, [{ number: 7, title: 'Submission receipts', state: 'open', html_url: 'https://github.com/r/7', body: THREAD_BODY, created_at: '', comments: 3, labels: [] }])
    .on('GET', /\/issues\/7\/comments/, [
      { id: 1, body: '<!-- dsl-receipt:none:due -->\n**No submission recorded** at the deadline.', created_at: '2026-09-20T00:00:00Z', html_url: 'https://github.com/r/7#1' },
      { id: 2, body: PATCH, created_at: '2026-09-23T10:00:00Z', html_url: 'https://github.com/r/7#2' },
      { id: 3, body: '<!-- dsl-marks-returned:assignment-2 -->\nMarks returned: see your marks repo.', created_at: '2026-09-23T11:00:00Z', html_url: 'https://github.com/r/7#3' },
    ]);
}

function memStore(): PrefStore & { data: Map<string, string> } {
  const data = new Map<string, string>();
  return { data, getItem: (k) => data.get(k) ?? null, setItem: (k, v) => void data.set(k, v) };
}
const refusing: PrefStore = { getItem: () => { throw new Error('blocked'); }, setItem: () => { throw new Error('blocked'); } };

describe('1. the assignment brief', () => {
  it('is the page body out of its raw guard, only once handed out', async () => {
    const a = await byslug();
    expect(a['assignment-2'].brief).toBe('Train two classifiers and **compare** them.\n\n## What to submit\n\nThe notebook.');
    expect(a['assignment-8'].brief).toBe('');
    expect(a['assignment-7'].brief).toBe('');
  });

  it('expands from the card, rendered here by the console until GitHub’s rendering arrives', async () => {
    const t = text(<AssignmentsView org={ORG} facts={await facts()} mine={mine()} now={NOW} studentView={false} receipts={{}} />);
    expect(t).toContain('The brief');
    expect(render(<GhMd src="Train **two**." />)).toContain('<b>two</b>');
  });
});

describe('2. teams with room, pick to join', () => {
  it('reads names, headcounts and caps, and never the member digests', async () => {
    const f = await facts();
    const a3 = f.assignments.find((a) => a.slug === 'assignment-3-project')!;
    expect(a3.teams).toEqual([{ name: 'team-latency', members: 1, cap: 3 }, { name: 'team-full', members: 3, cap: 3 }]);
    expect(JSON.stringify(f)).not.toMatch(/9c485c|members_sha256|team_salt/);
  });

  it('lists the teams with room first, a full one without a pick, and a pick fills in the form', async () => {
    const a3 = (await byslug())['assignment-3-project'];
    const picked: string[] = [];
    const t = text(<TeamList a={a3} current={null} onPick={(n) => picked.push(n)} picked="" />);
    expect(t).toMatch(/team-latency 1 of 3 members Pick team-full 3 of 3 members full/);
    const btn = findButton(TeamList({ a: a3, current: null, onPick: (n) => picked.push(n), picked: '' }));
    btn.props.onClick();
    expect(picked).toEqual(['team-latency']);
    expect(text(<TeamList a={a3} current={null} onPick={() => {}} picked="team-latency" />)).toContain('Picked');
    expect(new URL(joinTeamUrl(ORG, a3.slug, 'team-latency')).searchParams.get('team')).toBe('team-latency');
    expect(text(<TeamList a={{ ...a3, teams: [] }} current={null} onPick={() => {}} picked="" />)).toContain('No team has formed for Assignment 3 Project yet');
    expect(text(<TeamList a={a3} current="team-latency" onPick={() => {}} picked="" />)).toContain('your team');
  });
});

function findButton(v: unknown): { props: { onClick: () => void } } {
  const stack = [v];
  while (stack.length) {
    const n = stack.pop() as { type?: unknown; props?: { children?: unknown; onClick?: () => void } } | null;
    if (!n || typeof n !== 'object') continue;
    if (Array.isArray(n)) { stack.push(...n); continue; }
    if (n.type === 'button' && n.props?.onClick) return n as { props: { onClick: () => void } };
    if (typeof n.type === 'function' && n.props) {
      stack.push((n.type as (p: unknown) => unknown)(n.props));
      continue;
    }
    stack.push(n.props?.children);
  }
  throw new Error('no button');
}

describe('3. the whole receipts thread', () => {
  it('keeps every comment with its kind, and the issue body with the CONTRIBUTIONS ask', async () => {
    const r = (await readReceipts(client(receiptsFake()), ORG, 'assignment-3-project-team-latency'))!;
    expect(r.thread.map((c) => c.kind)).toEqual(['receipt', 'patch', 'marks']);
    expect(r.body).toContain('fill in CONTRIBUTIONS.md before the deadline');
    expect(r.body).not.toContain('<!--');
    expect(threadKind('hello')).toBe('comment');
  });

  it('shows the patch note as "pull before you continue", the thread in full, and asks for CONTRIBUTIONS.md', async () => {
    const r = (await readReceipts(client(receiptsFake()), ORG, 'x'))!;
    const v = <AssignmentsView org={ORG} facts={await facts()} mine={mine()} now={NOW} studentView={false} receipts={{ 'assignment-2-octo-student': r }} />;
    const t = text(v);
    expect(t).toContain('Pull before you continue.');
    expect(t).toContain('updated data/train.csv');
    expect(t).toContain('Every comment on Submission receipts 3');
    expect(t).toContain('Files updated');
    expect(t).toContain('Fill in CONTRIBUTIONS.md in your team repo before the deadline.');
    expect(render(v)).toContain(`href="https://github.com/${ORG}/assignment-3-project-team-latency/blob/HEAD/CONTRIBUTIONS.md"`);
  });

  it('puts a This week line for a patch newer than the last visit, and none for an older one', async () => {
    const f = await facts();
    const r = (await readReceipts(client(receiptsFake()), ORG, 'x'))!;
    const lines = patchLines(f.assignments, mine(), { 'assignment-2-octo-student': r });
    expect(lines).toEqual([{ slug: 'assignment-2', when: '2026-09-23T10:00:00Z' }]);
    const patch = (seen: number | null) => weekItems(f, mine(), NOW, lines, seen).filter((i) => i.kind === 'patch').map((i) => i.text);
    expect(patch(Date.parse('2026-09-22T00:00:00Z'))).toEqual(['Your instructors updated files in your Assignment 2 repo: pull before you continue']);
    expect(patch(Date.parse('2026-09-23T12:00:00Z'))).toEqual([]);
    expect(patch(null)).toHaveLength(1);
  });

  it('stores the visit only when marked, keeps this load’s answer, and survives blocked storage', () => {
    resetVisits();
    const store = memStore();
    expect(lastVisit(LOGIN, ORG, store)).toBeNull();
    resetVisits();
    expect(lastVisit(LOGIN, ORG, store)).toBeNull(); // never marked: a failed read stores nothing
    markVisit(LOGIN, ORG, 1000, store);
    expect(lastVisit(LOGIN, ORG, store)).toBeNull(); // same load, same answer
    resetVisits();
    expect(lastVisit(LOGIN, ORG, store)).toBe(1000);
    resetVisits();
    expect(lastVisit(LOGIN, 'other-f2026', refusing)).toBeNull();
    expect(() => markVisit(LOGIN, 'other-f2026', 1, refusing)).not.toThrow();
    resetVisits();
  });

  it('forgets every visit time and folder of the signed-out person, and no one else’s', () => {
    const data = new Map<string, string>([
      [`dsl-console-visit:${LOGIN}:${ORG}`, '1'], [`dsl-console-visit:${LOGIN}:other-f2026`, '2'], [`dsl-console-paths:${LOGIN}`, '{}'],
      ['dsl-console-visit:someone:x', '3'], ['dsl-console-paths:someone', '{}'], ['console-theme', 'dark'],
    ]);
    const store: PrefStore = { getItem: (k) => data.get(k) ?? null, setItem: (k, v) => void data.set(k, v), removeItem: (k) => void data.delete(k), get length() { return data.size; }, key: (i) => [...data.keys()][i] ?? null };
    resetVisits();
    lastVisit(LOGIN, ORG, store);
    forgetStudentPrefs(LOGIN, store);
    expect([...data.keys()].sort()).toEqual(['console-theme', 'dsl-console-paths:someone', 'dsl-console-visit:someone:x']);
    data.set(`dsl-console-visit:${LOGIN}:${ORG}`, '5');
    expect(lastVisit(LOGIN, ORG, store)).toBe(5); // the in-memory answers went too
    expect(() => forgetStudentPrefs(LOGIN, refusing)).not.toThrow();
    resetVisits();
  });
});

describe('4. the auditor', () => {
  it('is read from the secret auditors team, the person’s own membership only', async () => {
    const fake = new FakeGitHub()
      .on('GET', new RegExp(`^/orgs/${ORG}/repos`), [])
      .on('GET', `/orgs/${ORG}/teams/auditors/memberships/${LOGIN}`, { state: 'active', role: 'member' });
    const m = await readMine(client(fake), ORG, LOGIN, (await facts()).assignments);
    expect(m.auditor).toBe(true);
    expect(fake.seen.filter((s) => s.url.includes('/teams/auditors/')).map((s) => s.url)).toEqual([`https://api.github.com/orgs/${ORG}/teams/auditors/memberships/${LOGIN}`]);
    const student = await readMine(client(new FakeGitHub().on('GET', new RegExp(`^/orgs/${ORG}/repos`), [])), ORG, LOGIN, []);
    expect(student.auditor).toBe(false);
  });

  it('fails as a whole when the role cannot be read, and then promises nothing', async () => {
    const fake = new FakeGitHub()
      .on('GET', new RegExp(`^/orgs/${ORG}/repos`), [])
      .on('GET', `/orgs/${ORG}/teams/auditors/memberships/${LOGIN}`, () => json({ message: 'Forbidden' }, 403));
    await expect(readMine(client(fake), ORG, LOGIN, [])).rejects.toThrow();
    const t = text(<AssignmentsView org={ORG} facts={await facts()} mine={null} now={NOW} studentView={false} receipts={{}} unknownRole />);
    expect(t).toContain('Could not read your role');
    expect(t).not.toContain('Your repo');
    expect(t).not.toContain('join or create one');
    expect(t).not.toContain('How to hand in');
  });

  it('an auditor’s nav has no Marks or Join, and no "Reading your repos and marks" line', async () => {
    const fake = new FakeGitHub()
      .on('GET', new RegExp(`^/orgs/${ORG}/repos`), [])
      .on('GET', `/orgs/${ORG}/teams/auditors/memberships/${LOGIN}`, { state: 'active' });
    await readMine(client(fake), ORG, LOGIN, []);
    expect(knownAuditor(ORG.toUpperCase())).toBe(true);
    const sem: Semester = { org: ORG, term: 'f2026', termLabel: 'Fall 2026', courseOrg: 'c', courseName: 'Deep Learning', archived: false, role: 'student' };
    const nav = render(<StudentNav courses={[]} cohortStates={{}} semesters={[sem]} semester={sem} current="week" />);
    expect(nav).not.toContain('#marks');
    expect(nav).not.toContain('#join');
    expect(nav).toContain('#materials');
    await readMine(client(new FakeGitHub().on('GET', new RegExp(`^/orgs/${ORG}/repos`), [])), ORG, LOGIN, []);
    expect(knownAuditor(ORG)).toBe(false);
    expect(render(<StudentNav courses={[]} cohortStates={{}} semesters={[sem]} semester={sem} current="week" />)).toContain('#marks');
  });

  it('promises no repo, team or marks: "As an auditor you ..."', async () => {
    const f = await facts();
    const m = mine({ auditor: true, gradebook: null, units: {} });
    expect(text(<AuditorNote />)).toContain('As an auditor you read the materials and follow the schedule');
    const a = text(<AssignmentsView org={ORG} facts={f} mine={m} now={NOW} studentView={false} receipts={{}} />);
    expect(a).toContain('As an auditor you hand in no work for this assignment.');
    expect(a).not.toContain('Your repo');
    expect(a).not.toContain('join or create one');
    expect(text(<MarksView org={ORG} login={LOGIN} facts={f} gradebook={null} studentView={false} auditor />)).toContain('As an auditor you get no marks');
    expect(text(<JoinScreen org={ORG} facts={f} mine={m} studentView={false} />)).toContain('As an auditor you do not join a team.');
    const week = weekItems(f, m, NOW).map((i) => i.kind);
    expect(week).not.toContain('teams');
    expect(week).not.toContain('marks');
  });
});

describe('5. shape notes', () => {
  it('reads the shape, its note and the cutoff sentence', async () => {
    const a = await byslug();
    expect(a['assignment-2']).toMatchObject({ shape: 'assignment-repo-private', shapeNote: 'NB: this repo is private - only you and the instructors can read it.' });
    expect(a['assignment-6'].shapeNote).toContain('everyone in the semester can read the whole repo');
    expect(a['assignment-9'].shape).toBe('assignment-repo-student-choice');
  });

  it('shows the note on the card, and after the cutoff a student-choice repo may be made public', async () => {
    const v = <AssignmentsView org={ORG} facts={await facts()} mine={mine()} now={NOW} studentView={false} receipts={{}} />;
    const t = text(v);
    expect(t).toContain('NB: everyone in the semester can read the whole repo');
    expect(t).toContain('The late cutoff has passed: you may make assignment-9-octo-student public');
    expect(render(v)).toContain(`href="https://github.com/${ORG}/assignment-9-octo-student/settings"`);
    const before = text(<AssignmentsView org={ORG} facts={await facts()} mine={mine()} now={Date.parse('2026-09-01T12:00:00+02:00')} studentView={false} receipts={{}} />);
    expect(before).not.toContain('The late cutoff has passed');
  });
});

describe('6. the team of a drop-box or external group', () => {
  const box: SemesterAssignment = { slug: 'assignment-6', title: 'Assignment 6', subtitle: '', handout: null, due: null, lateCutoff: null, lateRule: '', cutoffSentence: '', submitVia: 'shared_dropbox_repo', privateRepo: false, submitUrl: '', group: true, teamFormation: null, solutionShown: null, maxPoints: '', handedOut: true, brief: '', shape: 'shared-dropbox-repo', shapeNote: '', tbc: false, teams: [] };

  it('comes from the person’s GitHub teams, by the assignment’s own slug and this org only', () => {
    const slugs = ['assignment-6', 'assignment-6-extra'];
    expect(teamOf(box, [team('assignment-6-extra-team-q'), team('assignment-6-team-x', 'other-org'), team('assignment-6-team-x')], ORG, slugs)).toEqual({ team: 'team-x', slug: 'assignment-6-team-x' });
    expect(teamOf(box, [team('assignment-6-extra-team-q')], ORG, slugs)).toBeNull();
    expect(teamOf(box, [team('students'), team('assignment-6')], ORG, slugs)).toBeNull();
  });

  it('reaches the unit with its members', async () => {
    const fake = new FakeGitHub()
      .on('GET', new RegExp(`^/orgs/${ORG}/repos`), [repo('assignment-6-submissions')])
      .on('GET', /^\/user\/teams/, [team('assignment-6-team-x')])
      .on('GET', new RegExp(`^/orgs/${ORG}/teams/assignment-6-team-x/members`), [{ login: LOGIN }, { login: 'mate' }]);
    const m = await readMine(client(fake), ORG, LOGIN, [box, { ...box, slug: 'assignment-7', submitVia: 'external' }]);
    expect(m.units['assignment-6']).toMatchObject({ repo: 'assignment-6-submissions', shared: true, team: 'team-x', members: [LOGIN, 'mate'] });
    expect(m.units['assignment-7'].team).toBeNull();
  });

  it('reads /user/teams once per session, not once per semester', async () => {
    const fake = new FakeGitHub()
      .on('GET', /^\/orgs\/[^/]+\/repos/, [])
      .on('GET', /^\/user\/teams/, [team('assignment-6-team-x')]);
    const c = client(fake);
    await readMine(c, ORG, LOGIN, [box]);
    await readMine(c, 'hertie-other-f2026', LOGIN, [box]);
    expect(fake.seen.filter((s) => s.url.includes('/user/teams'))).toHaveLength(1);
    forgetMyTeams(c);
    await readMine(c, ORG, LOGIN, [box]);
    expect(fake.seen.filter((s) => s.url.includes('/user/teams'))).toHaveLength(2);
  });
});

describe('7. readings per session', () => {
  it('reads the reading files, the reading list and the pending flag', async () => {
    const f = await facts();
    const s1 = f.rows.find((r) => r.id === 'session-01')!;
    expect(s1.readings.map((l) => l.path)).toEqual(['readings/01_deep-learning-in-public-policy/chapter1.pdf']);
    expect(s1.readingList).toBe('### Session 1 readings\n\nRead chapter 1 before class.');
    expect(f.rows.find((r) => r.id === 'session-12')!.readingsPending).toBe(true);
  });

  it('shows them on Schedule and on Materials, opening in the console', async () => {
    const f = await facts();
    const href = materialHref(ORG, 'materials', 'readings/01_deep-learning-in-public-policy/chapter1.pdf');
    const sched = render(<ScheduleView facts={f} mine={null} now={NOW} org={ORG} />);
    expect(sched).toContain('Readings:');
    expect(sched).toContain(`href="${href}"`);
    expect(sched).toContain('Readings to come.');
    const mat = text(<ReadingsView org={ORG} facts={f} now={NOW} />);
    expect(mat).toMatch(/Session 1 ?: Deep learning in public policy/);
    expect(mat).toContain('Session 1 readings');
    expect(mat).toContain('Read chapter 1 before class.');
    expect(mat).toMatch(/Session 12 ?: Tutorial presentations/);
    expect(mat).toContain('Readings to come.');
  });
});

describe('8. the pending invitation', () => {
  const gh = () => new FakeGitHub()
    .on('GET', '/user/orgs?per_page=100&page=1', [])
    .on('GET', '/user/memberships/orgs?state=active&per_page=100&page=1', [])
    .on('GET', '/user/memberships/orgs?state=pending&per_page=100&page=1', [{ state: 'pending', role: 'member', organization: { login: ORG } }])
    .on('GET', `/repos/${ORG}/.github`, { name: '.github', topics: ['dsl-semester'], permissions: { push: false, pull: true } })
    .on('GET', `/repos/${ORG}/semester-config/contents/.system/dsl-course.yml`, fileBody('dsl-course.yml', 'course: hertie-dsl-demo-course\n'))
    .on('GET', '/repos/hertie-dsl-demo-course/.github/contents/dsl-course.yml', fileBody('dsl-course.yml', 'course_name: Deep Learning\n'));

  it('shows the semester as Invited, not as a member’s', async () => {
    const e = await discoverEstate(client(gh()), { kind: 'classic', login: LOGIN });
    expect(e.semesters).toEqual([]);
    expect(e.invited?.map((s) => [s.org, s.courseName, s.termLabel])).toEqual([[ORG, 'Deep Learning', 'Fall 2026']]);
    const quiet = new FakeGitHub();
    expect(await pendingOrgs(client(quiet), 'fine-grained')).toEqual([]);
    expect(quiet.seen).toEqual([]);
  });

  it('links to GitHub’s accept page on Home and under Your requests', async () => {
    const e = await discoverEstate(client(gh()), { kind: 'classic', login: LOGIN });
    const home = render(<InvitedGroup invited={e.invited!} />);
    expect(home).toContain(`href="${invitationUrl(ORG)}"`);
    expect(home).toContain('Invited');
    const issue = { number: 3, title: 'Join course', state: 'closed', html_url: 'https://github.com/i/3', created_at: '2026-09-24T10:00:00Z', comments: 0, labels: [{ name: 'onboarding' }, { name: 'onboarded' }] };
    expect(render(<AskedList asked={[{ issue, reply: null }]} org={ORG} invitePending />)).toContain(`href="https://github.com/orgs/${ORG}/invitation"`);
    expect(render(<AskedList asked={[{ issue, reply: null }]} org={ORG} />)).not.toContain('/invitation');
  });
});

describe('9. the About block', () => {
  it('reads the course name, the home text with its Liquid resolved, announcements and the syllabus', async () => {
    const f = await facts();
    expect(f.courseName).toBe('Deep Learning (Demo)');
    expect(f.homeMarkdown).toBe('Welcome to **Deep Learning (Demo)** at the [Hertie School Data Science Lab](https://github.com/hertie-data-science-lab).\n\n**Questions?** Ask in the first session.');
    expect(f.announcements).toEqual([{ when: '2026-09-22T09:00:00', title: 'Room change', details: 'From this week the lab meets in **room 2.30**.' }]);
    expect(f.syllabus).toMatchObject({ repo: 'materials', path: 'SYLLABUS.md', name: 'SYLLABUS.md' });
    expect(homeText('{% if site.x %}A{% endif %}{{ site.y }}{% include z.html %}', { x: 'on', y: 'B' })).toBe('AB');
  });

  it('shows them on the semester’s This week, and a new announcement as a week line', async () => {
    const f = await facts();
    const v = <AboutView facts={f} org={ORG} tz="Europe/Berlin" now={NOW} />;
    const t = text(v);
    expect(t).toContain('About Deep Learning (Demo)');
    expect(t).toContain('Syllabus');
    expect(t).toContain('Welcome to Deep Learning (Demo)');
    expect(t).toContain('Room change');
    expect(render(v)).toContain(`href="${materialHref(ORG, 'materials', 'SYLLABUS.md')}"`);
    expect(weekItems(f, null, NOW).map((i) => `${i.kind}|${i.text}`)).toContain('news|Room change');
  });
});

describe('10. the small fields', () => {
  it('reads TBC flags, due-row details and max points', async () => {
    const f = await facts();
    expect(f.rows.find((r) => r.id === 'lab-03')!.tbc).toBe(true);
    expect(f.rows.find((r) => r.id === '02-assignment-2:due')!.details).toBe('Hand in the notebook and the one-page memo.');
    expect((await byslug())['assignment-8'].tbc).toBe(true);
  });

  it('shows TBC, row details, the points, the course’s late sentences and deep-linked file chips', async () => {
    const f = await facts();
    const sched = render(<ScheduleView facts={f} mine={null} now={NOW} org={ORG} />);
    expect(sched).toContain('class="tbc">TBC');
    expect(sched).toContain('Hand in the notebook and the one-page memo.');
    expect(sched).toContain(`href="${materialHref(ORG, 'materials', 'lectures/01_deep-learning-in-public-policy/Session1_E1394_DL_preLecture.pdf')}"`);
    expect(sched).toContain('>Lab_Session_3.ipynb<');
    const t = text(<AssignmentsView org={ORG} facts={f} mine={mine()} now={NOW} studentView={false} receipts={{}} />);
    expect(t).toContain('Out of 25 points');
    expect(t).toMatch(/Due [^)]*\(TBC\)/);
    expect(t).toContain('Late work in this course You have free 8 late days.');
  });
});

describe('11. the instructors', () => {
  it('shows an email only where the card carries one, and resolves a site picture', async () => {
    const f = await facts();
    const out = render(<InstructorsView facts={f} org={ORG} />);
    expect(out).toContain('href="mailto:instructor@example.org"');
    expect(out.match(/mailto:/g)).toHaveLength(1);
    expect(pictureOf('/_images/pp/x.jpg', ORG)).toBe(`https://${ORG}.github.io/_images/pp/x.jpg`);
    expect(pictureOf('https://github.com/a.png', ORG)).toBe('https://github.com/a.png');
    expect(pictureOf('pp/x.jpg', ORG)).toBe('');
    expect(sitePicture(`https://${ORG}.github.io/_images/pp/henrycgbaker.jpg`, ORG)).toEqual([SITE, '_images/pp/henrycgbaker.jpg']);
    expect(sitePicture('https://example.org/x.jpg', ORG)).toBeNull();
  });

  it('reads a site picture through StudentData, from the site repo only, as data:', async () => {
    const fake = new FakeGitHub().on('GET', `/repos/${ORG}/${SITE}/contents/_images/pp/henrycgbaker.jpg`, { type: 'file', content: btoa('JPEG') });
    const src = new SiteSource(client(fake));
    expect(await src.picture(ORG, `https://${ORG}.github.io/_images/pp/henrycgbaker.jpg`)).toBe(`data:image/jpeg;base64,${btoa('JPEG')}`);
    expect(await src.picture(ORG, 'https://github.com/a.png')).toBe('https://github.com/a.png');
    expect(await src.picture(ORG, 'https://example.org/x.jpg')).toBe('');
    expect(await src.picture(ORG, `https://${ORG}.github.io/x.svg`)).toBe('');
    expect(fake.seen.map((s) => s.url)).toEqual([`https://api.github.com/repos/${ORG}/${SITE}/contents/_images/pp/henrycgbaker.jpg`]);
    expect(sitePicture(`https://${ORG}.github.io/%E0%A4%A.jpg`, ORG)).toBeNull();
  });
});

describe('12. Set up', () => {
  it('checks the fork: forked, none, or a same-named repo that is not the fork', async () => {
    const fake = new FakeGitHub()
      .on('GET', `/repos/${LOGIN}/materials`, { name: 'materials', fork: true, parent: { full_name: `${ORG}/materials` }, html_url: `https://github.com/${LOGIN}/materials` })
      .on('GET', `/repos/${LOGIN}/notes`, { name: 'notes', fork: false, html_url: `https://github.com/${LOGIN}/notes` });
    expect(await forkOf(client(fake), LOGIN, ORG, 'materials')).toEqual({ kind: 'forked', url: `https://github.com/${LOGIN}/materials` });
    expect(await forkOf(client(fake), LOGIN, ORG, 'notes')).toMatchObject({ kind: 'other' });
    expect(await forkOf(client(fake), LOGIN, ORG, 'absent')).toEqual({ kind: 'none' });
  });

  it('writes the clone command and the editor link for the saved folder, Windows included', () => {
    expect(joinPath('/Users/jane/hertie/', 'materials')).toBe('/Users/jane/hertie/materials');
    expect(joinPath('C:\\Users\\jane\\hertie', 'materials')).toBe('C:\\Users\\jane\\hertie\\materials');
    expect(vscodeFolder('/Users/jane/hertie/materials')).toBe('vscode://file/Users/jane/hertie/materials');
    expect(vscodeFolder('C:\\Users\\jane\\materials')).toBe('vscode://file/C:/Users/jane/materials');
    expect(cloneCommand('https://github.com/jane/materials', '/Users/jane/hertie', 'materials')).toBe('git clone https://github.com/jane/materials.git "/Users/jane/hertie/materials"');
    expect(cloneCommand('https://github.com/jane/materials', '', 'materials')).toBe('git clone https://github.com/jane/materials.git');
  });

  it('remembers the folders in this browser only, and survives blocked storage', () => {
    const store = memStore();
    saveLocalPaths(LOGIN, { materials: '/m', assignments: '/a' }, store);
    expect(localPaths(LOGIN, store)).toEqual({ materials: '/m', assignments: '/a' });
    expect(localPaths('someone-else', store)).toEqual({ materials: '', assignments: '' });
    expect(() => saveLocalPaths(LOGIN, { materials: '/m', assignments: '' }, refusing)).not.toThrow();
    expect(localPaths(LOGIN, refusing)).toEqual({ materials: '', assignments: '' });
  });

  it('lists the student’s own assignment repos to clone, and nothing personal in the Student view', async () => {
    const f = await facts();
    const t = text(<SetupView org={ORG} facts={f} mine={mine()} studentView={false} />);
    expect(t).toContain(`git clone https://github.com/${ORG}/assignment-2-octo-student.git`);
    expect(t).toContain('Your local folders');
    const sv = text(<SetupView org={ORG} facts={f} mine={null} studentView />);
    expect(sv).toContain('A student sets up their fork');
    expect(sv).not.toContain('git clone');
  });
});

describe('13. the timezone per semester', () => {
  it('each This week line keeps its semester’s zone, and Home formats it there', async () => {
    const f = await facts();
    const ny: SemesterFacts = { ...f, timezone: 'America/New_York', rows: [{ ...f.rows.find((r) => r.id === '01-midterm-exam')! }] , announcements: [], assignments: [] };
    const items = weekItems(ny, null, NOW);
    expect(items.map((i) => [i.kind, i.tz])).toEqual([['exam', 'America/New_York']]);
    expect(new Date(items[0].at).toISOString()).toBe('2026-09-25T14:00:00.000Z');
    const t = text(<WeekList items={items} tz="Europe/Berlin" org={ORG} />);
    expect(t).toContain('10:00');
  });

  it('reads the zone from the site’s _config.yml when it names one', async () => {
    const fake = siteFake(new FakeGitHub(), { ...FILES, '_config.yml': 'timezone: America/New_York\n' });
    const f = (await new SiteSource(client(fake)).facts(ORG))!;
    expect(f.timezone).toBe('America/New_York');
  });
});

describe('14. archived semesters as history', () => {
  const sem: Semester = { org: ORG, term: 'f2026', termLabel: 'Fall 2026', courseOrg: 'c', courseName: 'Deep Learning', archived: true, role: 'student' };
  const env = (fake: FakeGitHub) => ({ client: client(fake), user: { login: LOGIN, id: 1, name: null, email: null, avatar_url: '' } } as unknown as Env);

  it('reads the student’s own repos and marks (reads only), and nothing in the Student view', () => {
    const fake = new FakeGitHub();
    const own = render(<EnvCtx.Provider value={env(fake)}><ArchivedSemester semester={sem} /></EnvCtx.Provider>);
    expect(own).toContain('Reading your repos and marks');
    const sv = render(<EnvCtx.Provider value={env(fake)}><ArchivedSemester semester={sem} studentView /></EnvCtx.Provider>);
    expect(sv).not.toContain('Reading');
    expect(sv).toContain(`href="https://github.com/${ORG}"`);
  });

  it('the history shows marks read-only from the gradebook', async () => {
    const t = text(<MarksView org={ORG} login={LOGIN} facts={await facts()} gradebook={parseGradebook(GRADES)} studentView={false} />);
    expect(t).toContain('21 / 25');
  });
});
