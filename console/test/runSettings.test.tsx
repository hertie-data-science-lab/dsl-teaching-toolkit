// WP-B4: how a semester runs its assignments (the Overview form, the semester's defaults, the
// schedule's new entry), the marks grid by team, student and question, and the wizards.

import { render } from 'preact-render-to-string';
import { describe, expect, it } from 'vitest';
import type { Course } from '../src/model/discovery';
import { StaticFiles } from '../src/model/files';
import type { Loaded } from '../src/model/status';
import type { Assignment, Status } from '../src/model/types';
import * as defs from '../src/ops/defs';
import { AssignmentScreen, AssignmentsScreen } from '../src/screens/Assignments';
import { cardsDone } from '../src/screens/NewCohort';
import { ScheduleScreen } from '../src/screens/Schedule';
import type { CohortProps } from '../src/screens/types';
import { assignmentArgs } from '../src/wizards/model';
import { initialValues } from '../src/screens/NewAssignment';
import example from './fixtures/status.example.json';

const COURSE_ORG = 'hertie-dsl-demo-course-e1234';
const COHORT_ORG = 'hertie-dsl-demo-f2026';
const NOW = Date.parse('2026-09-23T10:00:00+02:00');
const STATUS = example as unknown as Status;
const cohort = { org: COHORT_ORG, term: 'f2026', termLabel: 'Fall 2026' };
const course: Course = { org: COURSE_ORG, name: 'Machine Learning', code: 'E1234', description: '', write: true, admins: [], cohorts: [cohort], meta: {} };

const solo = STATUS.assignments![0];
const team: Assignment = { ...solo, slug: 'assignment-3', title: 'Group project', template: 'assignment-3-f2026', state: 'marking', units: 1, submissions: 1, teams: 1, marks: { filled: 0, total: 1 } };
const status: Status = { ...STATUS, assignments: [solo, team] };
const ready: Loaded = { kind: 'ready', status, sha: 's', stale: [] };

const SCHEDULE = [
  'timezone: Europe/Berlin',
  'assignments:',
  '  assignment-2:',
  '    course_source_repo: assignment-2-f2026',
  '    handout_datetime: 2026-09-15T10:00',
  '    due_datetime: 2026-09-27T23:59',
  '  assignment-3:',
  '    course_source_repo: assignment-3-f2026',
  '    handout_datetime: 2026-09-16T10:00',
  '    due_datetime: 2026-09-20T23:59',
  '',
].join('\n');
const ASSIGNMENTS = '# INSTRUCTOR-OWNED\ndefaults:\n  late_window_days: 5\n  late_penalty_per_day: 5%\n';
const COURSE_FILE = 'course_name: Machine Learning\nassignment_defaults:\n  max_team_size: 4\n';
const TEAM_SHEET = [
  'teams:',
  '  team-alpha:',
  '    info:',
  '      submitted: 2026-09-20T20:00',
  '      days_late: 0',
  '    feedback_group: Solid work.',
  '    members:',
  '      anna-a:',
  '        adjustment_individual: 2',
  '        feedback_individual:',
  '        notes_not_shared_with_students:',
  '      ben-b:',
  '        adjustment_individual:',
  '        feedback_individual:',
  '        notes_not_shared_with_students:',
  '    score_group:',
  '      Q1: 4',
  '      Q2:',
  '    feedback_per_question:',
  '      Q1: Nice proof.',
  '      Q2:',
  '',
].join('\n');
const SOLO_SHEET = 'submissions:\n  carla-c:\n    info:\n      submitted: 2026-09-27T20:00\n      days_late: 0\n    score_individual:\n      Q1:\n    adjustment_individual:\n    feedback_individual:\n    notes_not_shared_with_students:\n';

const base: Record<string, string> = {
  [`${COHORT_ORG}/semester-config/schedule.yml`]: SCHEDULE,
  [`${COHORT_ORG}/semester-config/assignments.yml`]: ASSIGNMENTS,
  [`${COURSE_ORG}/.github/dsl-course.yml`]: COURSE_FILE,
  [`${COURSE_ORG}/assignment-2-f2026/grading_config.yml`]: 'type: individual\nsubmit_via: assignment_repo\nquestions:\n  Q1: 10\n',
  [`${COURSE_ORG}/assignment-3-f2026/grading_config.yml`]: 'type: group\nquestions:\n  Q1: 5\n  Q2: {points: 5, file: report.tex}\n',
  [`${COHORT_ORG}/semester-config/grading_sheets/assignment-3.yml`]: TEAM_SHEET,
  [`${COHORT_ORG}/semester-config/grading_sheets/assignment-2.yml`]: SOLO_SHEET,
};
const filesWith = (over: Record<string, string> = {}) => new StaticFiles({ ...base, ...over });
const props = (over: Partial<CohortProps> = {}): CohortProps => ({ course, cohort, loaded: ready, files: filesWith(), now: NOW, ...over });
const html = (v: preact.VNode) => render(v);

describe('the Overview form: schedule entry and assignments.yml block as one', () => {
  it('shows the timings, the computed late cutoff and each run setting with its value and source', () => {
    const out = html(<AssignmentScreen {...props({ entry: 'assignment-2', tab: 'overview' })} />);
    expect(out).toContain('How this semester runs it');
    expect(out).toContain('value="2026-09-27"');
    expect(out).toContain('Fri 2 Oct 23:59 (due + 5 days)');
    expect(out).toContain('5 days at 5% a day, this semester’s default');
    expect(out).toContain('Private, institution default');
    expect(out).toContain('Applies to copies handed out after this change; existing copies keep theirs.');
    expect(out).toContain('Change for this assignment');
    expect(out).toContain('<a class="textlink" href="#assignments">Change the default</a>');
    expect(out).toContain('Repo name in this semester');
    // Alone: no team settings; their own repo: no external link.
    expect(out).not.toContain('How teams form');
    expect(out).not.toContain('Link to where they submit');
  });

  it('opens the field of a setting this assignment sets, with Use the default', () => {
    const files = filesWith({ [`${COHORT_ORG}/semester-config/assignments.yml`]: `${ASSIGNMENTS}assignments:\n  assignment-2:\n    visibility: public\n` });
    const out = html(<AssignmentScreen {...props({ entry: 'assignment-2', tab: 'overview', files })} />);
    expect(out).toContain('Public, set for this assignment');
    expect(out).toContain('<option value="public" selected');
    expect(out).toContain('Use the default');
    expect(out).toContain('Not available: student repos are not private');
  });

  it('shows the team settings, with the Teams link, for a team assignment', () => {
    const out = html(<AssignmentScreen {...props({ entry: 'assignment-3', tab: 'overview' })} />);
    expect(out).toContain('How teams form');
    expect(out).toContain('up to 4, this course’s default');
    expect(out).toContain('href="#assignment-assignment-3/teams">Open Teams</a>');
  });

  it('names the entry to Update every copy and Collect now, so two entries may share one template', () => {
    const shared = SCHEDULE.replace('course_source_repo: assignment-3-f2026', 'course_source_repo: assignment-2-f2026');
    const out = html(<AssignmentScreen {...props({ entry: 'assignment-2', tab: 'overview', files: filesWith({ [`${COHORT_ORG}/semester-config/schedule.yml`]: shared }) })} />);
    expect(out).toContain('>Collect now<');
    const ref = { slug: 'assignment-2', title: 'x', template: 'assignment-2-f2026', units: 1, group: false, when: '' };
    const scope = { courseOrg: COURSE_ORG, cohortOrg: COHORT_ORG, where: 'Fall 2026' };
    expect(defs.collect(scope, ref).args).toEqual({ course_source_repo: 'assignment-2-f2026', assignment: 'assignment-2' });
    expect(defs.updateCopies(scope, ref, []).args).toEqual({ course_source_repo: 'assignment-2-f2026', assignment: 'assignment-2' });
  });
});

describe('the semester’s defaults', () => {
  it('sit at the top of the Assignments index, each field over the course’s or the institution’s value', () => {
    const out = html(<AssignmentsScreen {...props()} />);
    expect(out).toContain('Defaults for this semester’s assignments');
    expect(out).toContain('value="5"');
    expect(out).toContain('this course’s default: up to 4');
    expect(out).toContain('institution default: Students form their own');
    expect(out).toContain('Change the course’s defaults');
  });

  it('are asked once in New semester, as an optional card', () => {
    expect(cardsDone(filesWith(), COHORT_ORG).defaults).toBe(true);
    expect(cardsDone(new StaticFiles({}), COHORT_ORG).defaults).toBe(false);
  });
});

describe('the marks grid: teams > students > questions', () => {
  it('gives a team its score and feedback, each question a feedback cell, and each member feedback, Adjust ± and notes', () => {
    const out = html(<AssignmentScreen {...props({ entry: 'assignment-3', tab: 'marks' })} />);
    expect(out).toContain('aria-label="Q2 (out of 5) for team-alpha"');
    expect(out).toContain('/ 5 · <code>report.tex</code>');
    expect(out).toContain('aria-label="Team feedback for team-alpha">Solid work.</textarea>');
    expect(out).toContain('Team feedback per question');
    expect(out).toContain('aria-label="Feedback on Q1 for team-alpha">Nice proof.</textarea>');
    expect(out).toContain('aria-label="Feedback on Q2 for team-alpha"></textarea>');
    for (const h of ['anna-a', 'ben-b']) {
      expect(out).toContain(`aria-label="Feedback for ${h}"`);
      expect(out).toContain(`aria-label="Adjustment for ${h}"`);
      expect(out).toContain(`aria-label="Private notes for ${h}"`);
    }
    expect(out).toContain('aria-label="Hide the members of team-alpha"');
    expect(out).toContain('individual adjustment relative to team (optional)');
  });

  it('renders every input even when the sheet is blank, and folds feedback per question until there is some', () => {
    const out = html(<AssignmentScreen {...props({ entry: 'assignment-2', tab: 'marks' })} />);
    expect(out).toContain('aria-label="Q1 (out of 10) for carla-c"');
    expect(out).toContain('aria-label="Adjustment for carla-c"');
    expect(out).toContain('aria-label="Private notes for carla-c"');
    expect(out).toContain('aria-expanded="false" aria-label="Show feedback per question for carla-c"');
    expect(out).not.toContain('Feedback on Q1 for carla-c');
    // The penalty is the semester's, not a literal.
    expect(out).toContain('The penalty is 5% of the total per late day.');
  });

  it('reads a renamed entry’s mark sheet by its semester-side name', () => {
    const files = filesWith({
      [`${COHORT_ORG}/semester-config/assignments.yml`]: `${ASSIGNMENTS}assignments:\n  assignment-2:\n    semester_dest_repo: assignment-2-resit\n`,
      [`${COHORT_ORG}/semester-config/grading_sheets/assignment-2.yml`]: 'submissions: {}\n',
      [`${COHORT_ORG}/semester-config/grading_sheets/assignment-2-resit.yml`]: SOLO_SHEET,
    });
    const out = html(<AssignmentScreen {...props({ entry: 'assignment-2', tab: 'marks', files })} />);
    expect(out).toContain('aria-label="Q1 (out of 10) for carla-c"');
    expect(out).toContain('grading_sheets/assignment-2-resit.yml');
  });

  it('reads a renamed entry’s teams by its semester-side name', () => {
    const files = filesWith({
      [`${COHORT_ORG}/semester-config/assignments.yml`]: `${ASSIGNMENTS}assignments:\n  assignment-3:\n    semester_dest_repo: assignment-3-resit\n`,
      [`${COHORT_ORG}/semester-config/teams.csv`]: 'assignment,team,github_handle\nassignment-3,team-old,anna-a\nassignment-3-resit,team-resit,ben-b\n',
      [`${COHORT_ORG}/semester-config/students.csv`]: 'hertie_email,name,role,github_handle\nanna@x.org,Anna Adams,enrolled,anna-a\nben@x.org,Ben Baker,enrolled,ben-b\n',
    });
    const out = html(<AssignmentScreen {...props({ entry: 'assignment-3', tab: 'teams', files })} />);
    expect(out).toContain('team-resit');
    expect(out).not.toContain('team-old');
  });

  it('returns marks for this assignment only', () => {
    const d = defs.returnMarks({ courseOrg: COURSE_ORG, cohortOrg: COHORT_ORG, where: 'Fall 2026' }, { slug: 'assignment-3', title: 'x', template: 'assignment-3-f2026', units: 1, group: true, when: 'Marking' }, 1, 'assignment-3');
    expect(d.args).toEqual({ assignment: 'assignment-3' });
  });
});

describe('the wizards', () => {
  it('New assignment sends template keys only', () => {
    const args = assignmentArgs({ ...initialValues(null, 'f2026'), name: 'Trees', number: 4, type: 'group' });
    for (const k of ['team_formation', 'max_team_size', 'visibility', 'submit_url', 'late_window_days', 'late_penalty_per_day']) expect(args).not.toHaveProperty(k);
  });

  it('adding it to a semester’s schedule asks the run settings, with defaults from the cascade', () => {
    const files = filesWith({ [`${COURSE_ORG}/assignment-4-f2026/grading_config.yml`]: 'type: group\n' });
    const out = html(<ScheduleScreen {...props({ entry: 'new', prefill: 'assignment-4-f2026', files })} />);
    expect(out).toContain('How this semester runs it');
    expect(out).toContain('Written to assignments.yml with the entry.');
    expect(out).toContain('Students form their own, institution default');
    expect(out).toContain('up to 4, this course’s default');
    expect(out).toContain('Due + 5 days at 5% a day, this semester’s default');
  });
});
