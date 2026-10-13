// The assignment hub (decision 0008): tabs, the default tab by state, and the old hashes.

import { render } from 'preact-render-to-string';
import { describe, expect, it, vi } from 'vitest';
import type { GitHubClient } from '../src/github/client';
import type { Course } from '../src/model/discovery';
import { LiveFiles, RETRY_MS, type Files } from '../src/model/files';
import { StaticFiles } from './staticFiles';
import type { Loaded } from '../src/model/status';
import type { Assignment, AssignmentState, Status } from '../src/model/types';
import { hashOf, movedHash, parseHash, replaceHash } from '../src/router';
import { AssignmentScreen, AssignmentsScreen, defaultTab, misrouted } from '../src/screens/Assignments';
import type { CohortProps } from '../src/screens/types';
import { releaseAdhoc, releaseAgain, releaseEarly, releaseNow } from '../src/ops/defs';
import { Sidenav } from '../src/ui/shell';
import example from './fixtures/status.example.json';

const COURSE_ORG = 'hertie-dsl-demo-course-e1234';
const COHORT_ORG = 'hertie-dsl-demo-f2026';
const NOW = Date.parse('2026-09-23T10:00:00+02:00');
const STATUS = example as unknown as Status;
const cohort = { org: COHORT_ORG, term: 'f2026', termLabel: 'Fall 2026' };
const course: Course = { org: COURSE_ORG, name: 'Machine Learning', code: 'E1234', description: '', write: true, admins: [], cohorts: [cohort], meta: {} };

const solo = STATUS.assignments![0];
const team: Assignment = { ...solo, slug: 'assignment-3', title: 'Group project', template: 'assignment-3-f2026', state: 'teams_forming', units: 0, submissions: 0, teams: 1 };
const status: Status = { ...STATUS, assignments: [solo, team] };
const ready: Loaded = { kind: 'ready', status, sha: 's', stale: [] };
const files = new StaticFiles({ [`${COHORT_ORG}/semester-config/teams.csv`]: 'assignment,team,github_handle\nassignment-3,team-alpha,anna-a\n' });
const props = (over: Partial<CohortProps> = {}): CohortProps => ({ course, cohort, loaded: ready, files, now: NOW, ...over });
const current = (out: string) => /<a href="[^"]*" aria-current="page">([^<]+)<\/a>/.exec(out)?.[1];

describe('the assignment hub', () => {
  it('parses tabs, and the retired Teams and Marks hashes, to the hub', () => {
    expect(parseHash('#assignment-assignment-2')).toEqual({ screen: 'assignment', entry: 'assignment-2' });
    expect(parseHash('#assignment-assignment-2/marks')).toEqual({ screen: 'assignment', entry: 'assignment-2', tab: 'marks' });
    expect(parseHash('#teams-assignment-3')).toEqual({ screen: 'assignment', entry: 'assignment-3', tab: 'teams' });
    expect(parseHash('#marks-assignment-2')).toEqual({ screen: 'assignment', entry: 'assignment-2', tab: 'marks' });
    expect(parseHash('#teams')).toEqual({ screen: 'assignments' });
    expect(parseHash('#marks')).toEqual({ screen: 'assignments' });
    expect(hashOf(parseHash('#teams-assignment-3'))).toBe('#assignment-assignment-3/teams');
    expect(hashOf(parseHash('#schedule-s5'))).toBe('#schedule-s5');
  });

  it('rewrites an old Teams or Marks hash in the address bar, keeping the query string', () => {
    const replaceState = vi.fn();
    vi.stubGlobal('history', { replaceState });
    vi.stubGlobal('location', { search: `?cohort=${COHORT_ORG}` });
    try {
      for (const [old, now] of [['#teams-assignment-3', '#assignment-assignment-3/teams'], ['#marks-assignment-2', '#assignment-assignment-2/marks'], ['#teams', '#assignments'], ['#marks', '#assignments']]) {
        const to = movedHash(old);
        expect(to).toBe(now);
        replaceHash(to!);
        expect(replaceState).toHaveBeenLastCalledWith(null, '', `?cohort=${COHORT_ORG}${now}`);
      }
      for (const same of ['', '#', '#assignment-assignment-3/teams', '#schedule-s5']) expect(movedHash(same)).toBeNull();
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it('opens on Teams while teams form, on Marks once marking starts, else Overview', () => {
    const at = (state: AssignmentState, a: Assignment = team) => defaultTab({ ...a, state });
    expect(at('teams_forming')).toBe('teams');
    expect(at('blocked')).toBe('teams');
    expect(at('marking')).toBe('marks');
    expect(at('returned', solo)).toBe('marks');
    expect(at('open')).toBe('overview');
    expect(at('declared', solo)).toBe('overview');
  });

  it('shows Teams only for a team assignment, and the default tab as current', () => {
    const t = render(<AssignmentScreen {...props({ entry: 'assignment-3' })} />);
    expect(t).toContain('href="#assignment-assignment-3/teams" aria-current="page">Teams');
    expect(t).toContain('Without a team');
    const s = render(<AssignmentScreen {...props({ entry: 'assignment-2' })} />);
    expect(s).not.toContain('>Teams</a>');
    expect(current(s)).toBe('Overview');
    expect(s).toContain('href="#assignment-assignment-2/marks"');
  });

  it('a teams.csv it could not read is an error, never an empty team list to save over', () => {
    const failing: Files = Object.assign(Object.create(files), {
      file: (o: string, r: string, path: string) => (path === 'teams.csv' ? { kind: 'error', message: 'HTTP 502' } : files.file(o, r, path)),
    });
    const out = render(<AssignmentScreen {...props({ entry: 'assignment-3', tab: 'teams', files: failing })} />);
    expect(out).toContain('Could not read teams.csv: HTTP 502. It is tried again shortly.');
    expect(out).not.toContain('Without a team');
    expect(out).not.toContain('Create team');
  });

  it('the Teams tab links the students’ assignment page, not the retired site page', () => {
    const out = render(<AssignmentScreen {...props({ entry: 'assignment-3', tab: 'teams' })} />);
    expect(out).toContain(`href="?semester=${COHORT_ORG}#assignment-assignment-3"`);
    expect(out).not.toContain('github.io/assignments');
    expect(out).not.toContain('student site');
  });

  it('says what the engine does while teams form: handed out, a repo per team, none for the team-less', () => {
    const out = render(<AssignmentScreen {...props({ entry: 'assignment-3', tab: 'overview' })} />);
    expect(out).toContain('Handed out');
    expect(out).toContain('students without a team get none');
    expect(out).not.toContain('own repo at hand out');
    expect(out).not.toContain('until hand out');
    expect(out).not.toContain('>Hand out now<');
  });

  it('reads 0, not null, when the gradebook does not say how many submitted', () => {
    const unknown: Status = { ...status, assignments: [{ ...solo, submissions: null }, team] };
    const out = render(<AssignmentScreen {...props({ entry: 'assignment-2', tab: 'overview', loaded: { ...ready, status: unknown } })} />);
    expect(out).toContain(`0 of ${solo.units} submitted so far.`);
    expect(out).not.toContain('null');
  });

  it('an assigned-teams assignment has no window on its Teams tab', () => {
    const assigned = new StaticFiles({
      [`${COHORT_ORG}/semester-config/teams.csv`]: 'assignment,team,github_handle\n',
      [`${COHORT_ORG}/semester-config/assignments.yml`]: 'assignments:\n  assignment-3:\n    team_formation: assigned\n',
    });
    const out = render(<AssignmentScreen {...props({ entry: 'assignment-3', tab: 'teams', files: assigned })} />);
    expect(out).toContain('You assign every team here; students cannot form their own.');
    expect(out).not.toContain('Students can form and join teams');
    expect(out).not.toContain('Email ');
  });

  it('a Teams link to an assignment done alone lands on its Overview, and says so in the address', () => {
    const out = render(<AssignmentScreen {...props({ entry: 'assignment-2', tab: 'teams' })} />);
    expect(current(out)).toBe('Overview');
    expect(misrouted(solo, 'teams')).toBe('#assignment-assignment-2/overview');
    expect(misrouted(team, 'teams')).toBeNull();
    expect(misrouted(solo, 'marks')).toBeNull();
  });

  it('renders the named tab over the default one', () => {
    const out = render(<AssignmentScreen {...props({ entry: 'assignment-3', tab: 'overview' })} />);
    expect(current(out)).toBe('Overview');
    expect(out).toContain('What you can do, by state');
  });
});

describe('the cohort nav', () => {
  it('lists the cohort pages in order, without Marks or Teams (decision 0037)', () => {
    const nav = render(<Sidenav courses={[course]} course={course} cohort={cohort} cohortStates={{ [COHORT_ORG]: ready }} current="assignments" />);
    const first = nav.slice(nav.indexOf('href="#dashboard"') - 9, nav.indexOf('</ul>', nav.indexOf('href="#dashboard"')));
    const names = [...first.matchAll(/<a href="#[a-z]+"[^>]*>([A-Za-z ]+)/g)].map((m) => m[1]);
    expect(names).toEqual(['Dashboard', 'Schedule', 'Assignments', 'Students', 'Instructors', 'Site', 'Operations']);
    expect(nav).toContain('href="#assignments" aria-current="page"');
    expect(nav).not.toContain('href="#marks"');
    expect(nav).not.toContain('href="#teams"');
  });
});

describe('the Assignments index', () => {
  it('shows teams formed and students without a team, marked of total and returned', () => {
    const roster = 'hertie_email,name,role,github_handle\na@x.org,A,enrolled,anna-a\nb@x.org,B,enrolled,ben-b\nc@x.org,C,enrolled,\n';
    const f = new StaticFiles({
      [`${COHORT_ORG}/semester-config/students.csv`]: roster,
      [`${COHORT_ORG}/semester-config/teams.csv`]: 'assignment,team,github_handle\nassignment-3,team-alpha,Anna-A\nassignment-2,x,ben-b\n',
    });
    const back = { ...solo, marks: { filled: 40, total: 48 }, returned: true };
    const out = render(<AssignmentsScreen {...props({ files: f, loaded: { kind: 'ready', status: { ...status, assignments: [back, team] }, sha: 's', stale: [] } })} />);
    expect(out).toContain('<th>Teams</th><th>Marked</th><th>Returned</th>');
    expect(out).toContain('1 formed<br/><span class="footnote">1 without a team</span>');
    expect(out).toContain('<td class="num">40 / 48</td>');
    expect(out).toContain('<span class="chip ok">Yes</span>');
  });
});

describe('entry releases', () => {
  it('offer no destination: the engine takes it from the schedule', () => {
    const scope = { courseOrg: COURSE_ORG, cohortOrg: COHORT_ORG, where: 'Fall 2026' };
    const r = { id: 's5', ident: 'Session 5', title: 'Trees', when: 'Thu 8 Oct 10:00', source: { repo: 'course-materials-f2026', path: 'lectures/05' } };
    for (const def of [releaseNow(scope, r), releaseEarly(scope, r), releaseAgain(scope, r)]) {
      expect(def.args).toEqual({ entry: 's5' });
      expect(Object.keys(def.options ?? {})).toEqual([]);
    }
    expect(Object.keys(releaseAdhoc(scope, ['course-materials-f2026']).options ?? {})).toContain('semester_dest_repo');
  });
});

describe('a failed read', () => {
  const flush = async () => { for (let i = 0; i < 4; i++) await Promise.resolve(); };
  it('is an error, not absent, and is read again on an ask once RETRY_MS has passed', async () => {
    const tree = { sha: 'r', truncated: false, tree: [{ path: 'lectures', type: 'tree', sha: 'x', mode: '040000' }] };
    const listTree = vi.fn().mockRejectedValueOnce(new Error('HTTP 502')).mockResolvedValueOnce(tree);
    const listOrgRepos = vi.fn().mockRejectedValueOnce(new Error('HTTP 403 secondary rate limit')).mockResolvedValueOnce([]);
    let now = 0;
    const live = new LiveFiles({ listTree, listOrgRepos } as unknown as GitHubClient, () => now);
    live.tree('o', 'r');
    live.repos('o');
    await flush();
    expect(live.tree('o', 'r')).toEqual({ kind: 'error', message: 'HTTP 502' });
    expect(live.repos('o')).toEqual({ kind: 'error', message: 'HTTP 403 secondary rate limit' });
    // Shown as an error meanwhile; not asked again on every render.
    now = RETRY_MS - 1;
    expect(live.tree('o', 'r').kind).toBe('error');
    expect(listTree).toHaveBeenCalledTimes(1);
    now = RETRY_MS;
    expect(live.tree('o', 'r').kind).toBe('loading');
    expect(live.repos('o').kind).toBe('loading');
    await flush();
    expect(live.tree('o', 'r')).toMatchObject({ kind: 'ready', paths: [{ path: 'lectures', dir: true }] });
    expect(live.repos('o')).toEqual({ kind: 'ready', repos: [] });
    expect(listTree).toHaveBeenCalledTimes(2);
  });
});

describe('last change', () => {
  it('is read again after the console writes the file', async () => {
    const dates = ['2026-09-01T10:00:00Z', '2026-09-24T12:00:00Z'];
    const client = { lastCommitDate: vi.fn(async () => dates.shift() ?? null) } as unknown as GitHubClient;
    const live = new LiveFiles(client);
    const path = 'grading_sheets/assignment-2.yml';
    expect(live.lastChange(COHORT_ORG, 'semester-config', path)).toBeUndefined();
    await Promise.resolve();
    await Promise.resolve();
    expect(live.lastChange(COHORT_ORG, 'semester-config', path)).toBe('2026-09-01T10:00:00Z');
    live.put(COHORT_ORG, 'semester-config', path, undefined, 'submissions: {}\n', 'new');
    await Promise.resolve();
    await Promise.resolve();
    expect(live.lastChange(COHORT_ORG, 'semester-config', path)).toBe('2026-09-24T12:00:00Z');
    expect(client.lastCommitDate).toHaveBeenCalledTimes(2);
  });
});
