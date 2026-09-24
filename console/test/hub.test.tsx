// The assignment hub (decision 0008): tabs, the default tab by state, and the old hashes.

import { render } from 'preact-render-to-string';
import { describe, expect, it } from 'vitest';
import type { Course } from '../src/model/discovery';
import { StaticFiles } from '../src/model/files';
import type { Loaded } from '../src/model/status';
import type { Assignment, AssignmentState, Status } from '../src/model/types';
import { hashOf, parseHash } from '../src/router';
import { AssignmentScreen, defaultTab } from '../src/screens/Assignments';
import type { CohortProps } from '../src/screens/types';
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
