// The assignment hub (decision 0008): tabs, the default tab by state, and the old hashes.

import { render } from 'preact-render-to-string';
import { describe, expect, it, vi } from 'vitest';
import type { GitHubClient } from '../src/github/client';
import type { Course } from '../src/model/discovery';
import { LiveFiles, StaticFiles } from '../src/model/files';
import type { Loaded } from '../src/model/status';
import type { Assignment, AssignmentState, Status } from '../src/model/types';
import { hashOf, movedHash, parseHash, replaceHash } from '../src/router';
import { AssignmentScreen, AssignmentsScreen, defaultTab } from '../src/screens/Assignments';
import { MarksOverviewScreen } from '../src/screens/Marking';
import { gradebookWrites, readSheet, returnedOn } from '../src/model/marks';
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
const files = new StaticFiles({ [`${COHORT_ORG}/classroom-config/teams.csv`]: 'assignment,team,github_handle\nassignment-3,team-alpha,anna-a\n' });
const props = (over: Partial<CohortProps> = {}): CohortProps => ({ course, cohort, loaded: ready, files, now: NOW, ...over });
const current = (out: string) => /<a href="[^"]*" aria-current="page">([^<]+)<\/a>/.exec(out)?.[1];

describe('the assignment hub', () => {
  it('parses tabs, and the retired Teams and Marks hashes, to the hub', () => {
    expect(parseHash('#assignment-assignment-2')).toEqual({ screen: 'assignment', entry: 'assignment-2' });
    expect(parseHash('#assignment-assignment-2/marks')).toEqual({ screen: 'assignment', entry: 'assignment-2', tab: 'marks' });
    expect(parseHash('#teams-assignment-3')).toEqual({ screen: 'assignment', entry: 'assignment-3', tab: 'teams' });
    expect(parseHash('#marks-assignment-2')).toEqual({ screen: 'assignment', entry: 'assignment-2', tab: 'marks' });
    expect(parseHash('#teams')).toEqual({ screen: 'assignments' });
    expect(hashOf(parseHash('#teams-assignment-3'))).toBe('#assignment-assignment-3/teams');
    expect(hashOf(parseHash('#schedule-s5'))).toBe('#schedule-s5');
  });

  it('rewrites an old Teams or Marks hash in the address bar, keeping the query string', () => {
    const replaceState = vi.fn();
    vi.stubGlobal('history', { replaceState });
    vi.stubGlobal('location', { search: `?cohort=${COHORT_ORG}` });
    try {
      for (const [old, now] of [['#teams-assignment-3', '#assignment-assignment-3/teams'], ['#marks-assignment-2', '#assignment-assignment-2/marks'], ['#teams', '#assignments']]) {
        const to = movedHash(old);
        expect(to).toBe(now);
        replaceHash(to!);
        expect(replaceState).toHaveBeenLastCalledWith(null, '', `?cohort=${COHORT_ORG}${now}`);
      }
      for (const same of ['', '#', '#marks', '#assignment-assignment-3/teams', '#schedule-s5']) expect(movedHash(same)).toBeNull();
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

  it('a Teams link to an assignment done alone lands on its Overview', () => {
    const out = render(<AssignmentScreen {...props({ entry: 'assignment-2', tab: 'teams' })} />);
    expect(current(out)).toBe('Overview');
  });

  it('renders the named tab over the default one', () => {
    const out = render(<AssignmentScreen {...props({ entry: 'assignment-3', tab: 'overview' })} />);
    expect(current(out)).toBe('Overview');
    expect(out).toContain('What you can do, by state');
  });
});

describe('the Marks overview', () => {
  const back: Assignment = { ...solo, slug: 'assignment-1', title: 'Intro', state: 'returned', units: 2, marks: { filled: 2, total: 2 }, returned: true };
  const SHEET = 'submissions:\n  anna-a:\n    score_individual: 9\n  ben-b:\n    score_individual: 7\n';
  const LEDGER = 'target,assignment,channel,content_hash,distributed_at,issue\nanna-a,,gradebook,abc,2026-09-10T08:00:00+00:00,\nben-b,,gradebook,def,2026-09-11T09:00:00+00:00,\nben-b,,email,def,2026-09-12T09:00:00+00:00,\n';
  const withSheets = new StaticFiles(
    { [`${COHORT_ORG}/classroom-config/grading_sheets/assignment-1.yml`]: SHEET, [`${COHORT_ORG}/classroom-config/gradebook/distributed.csv`]: LEDGER },
    { [`${COHORT_ORG}/classroom-config/grading_sheets`]: ['assignment-1.yml'] },
    {}, {},
    { [`${COHORT_ORG}/classroom-config/grading_sheets/assignment-1.yml`]: '2026-09-09T14:30:00Z' },
  );
  const st: Loaded = { kind: 'ready', status: { ...status, assignments: [back, solo] }, sha: 's', stale: [] };

  it('dates a return by the last gradebook write of the sheet’s students', () => {
    const w = gradebookWrites(LEDGER);
    expect(w.get('ben-b')).toBe('2026-09-11T09:00:00+00:00');
    expect(returnedOn(readSheet(SHEET, { submissions: { 'anna-a': {}, 'ben-b': {} } }), w)).toBe('2026-09-11T09:00:00+00:00');
  });

  it('lists one read-only row per assignment, linking to its Marks tab', () => {
    const out = render(<MarksOverviewScreen {...props({ loaded: st, files: withSheets })} />);
    expect(out).toContain('1 of 2 assignments returned; 0 marks still to enter.');
    expect(out).toContain('href="#assignment-assignment-1/marks">Assignment 1: Intro');
    expect(out).toContain('<td class="num">2 / 2</td>');
    expect(out).toContain('<th>Gradebooks last updated</th>');
    expect(out).toContain('<td><span class="chip ok">Yes</span></td><td class="num">Fri 11 Sep</td>');
    expect(out).toContain('Every return rewrites every student’s gradebook');
    expect(out).toContain('Wed 9 Sep');
    expect(out).toContain('No mark sheet yet');
    expect(out).not.toContain('<input');
  });

  it('says so when no mark sheet exists yet', () => {
    const out = render(<MarksOverviewScreen {...props()} />);
    expect(out).toContain('No mark sheets yet');
    expect(out).not.toContain('<table');
  });

  it('says the listing failed, rather than that there are no sheets', () => {
    const broken = new StaticFiles({}, { [`${COHORT_ORG}/classroom-config/grading_sheets`]: new Error('Bad credentials') });
    const out = render(<MarksOverviewScreen {...props({ files: broken })} />);
    expect(out).toContain('Could not list the mark sheets in classroom-config/grading_sheets: Bad credentials');
    expect(out).not.toContain('No mark sheets yet');
  });
});

describe('the cohort nav', () => {
  it('lists the cohort pages in order, with Marks and without Teams', () => {
    const nav = render(<Sidenav courses={[course]} course={course} cohort={cohort} cohortStates={{ [COHORT_ORG]: ready }} current="marks" problems={0} />);
    const first = nav.slice(nav.indexOf('href="#cohort"') - 9, nav.indexOf('</ul>', nav.indexOf('href="#cohort"')));
    const names = [...first.matchAll(/<a href="#[a-z]+"[^>]*>([A-Za-z ]+)/g)].map((m) => m[1]);
    expect(names).toEqual(['This week', 'Schedule', 'Assignments', 'Marks', 'Students', 'Staff', 'Site', 'Archive', 'Operations']);
    expect(nav).toContain('href="#marks" aria-current="page"');
    expect(nav).not.toContain('href="#teams"');
  });
});

describe('the Assignments index', () => {
  it('shows teams formed and students without a team, marked of total and returned', () => {
    const roster = 'hertie_email,name,role,github_handle\na@x.org,A,enrolled,anna-a\nb@x.org,B,enrolled,ben-b\nc@x.org,C,enrolled,\n';
    const f = new StaticFiles({
      [`${COHORT_ORG}/classroom-config/students.csv`]: roster,
      [`${COHORT_ORG}/classroom-config/teams.csv`]: 'assignment,team,github_handle\nassignment-3,team-alpha,Anna-A\nassignment-2,x,ben-b\n',
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
    expect(Object.keys(releaseAdhoc(scope, ['course-materials-f2026']).options ?? {})).toContain('cohort_dest_repo');
  });
});

describe('last change', () => {
  it('is read again after the console writes the file', async () => {
    const dates = ['2026-09-01T10:00:00Z', '2026-09-24T12:00:00Z'];
    const client = { lastCommitDate: vi.fn(async () => dates.shift() ?? null) } as unknown as GitHubClient;
    const live = new LiveFiles(client);
    const path = 'grading_sheets/assignment-2.yml';
    expect(live.lastChange(COHORT_ORG, 'classroom-config', path)).toBeUndefined();
    await Promise.resolve();
    await Promise.resolve();
    expect(live.lastChange(COHORT_ORG, 'classroom-config', path)).toBe('2026-09-01T10:00:00Z');
    live.put(COHORT_ORG, 'classroom-config', path, undefined, 'submissions: {}\n', 'new');
    await Promise.resolve();
    await Promise.resolve();
    expect(live.lastChange(COHORT_ORG, 'classroom-config', path)).toBe('2026-09-24T12:00:00Z');
    expect(client.lastCommitDate).toHaveBeenCalledTimes(2);
  });
});
