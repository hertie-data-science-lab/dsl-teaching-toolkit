// The wizards: derived names, which step opens, conditional fields, the two forbidden format
// pairs, the request args against the registry's schemas, the live checks and the central
// set-up run against a fake GitHub, and one render per wizard.

import { render } from 'preact-render-to-string';
import { describe, expect, it } from 'vitest';
import { layout } from '../src/forms/Form';
import { GitHubClient } from '../src/github/client';
import type { Course } from '../src/model/discovery';
import { StaticFiles } from '../src/model/files';
import type { Loaded } from '../src/model/status';
import type { Status } from '../src/model/types';
import { validateArgs } from '../src/ops/adapter';
import { opSpec } from '../src/ops/registry';
import { FormatPicker } from '../src/forms/FormatPicker';
import { NewAssignmentScreen, extrasOf, initialValues, naDone, S1, S2, withExtras } from '../src/screens/NewAssignment';
import { NewCohortScreen, cardsDone, nkDone } from '../src/screens/NewCohort';
import { NewCourseScreen, ncDone, ncOrg } from '../src/screens/NewCourse';
import { NewMaterialsScreen } from '../src/screens/NewMaterials';
import { ScheduleScreen } from '../src/screens/Schedule';
import type { CohortProps, CourseProps } from '../src/screens/types';
import { assignmentMarking, assignmentWork, newMaterials } from '../src/tiers/wizard';
import { CENTRAL, bootstrapInputs, runBootstrap } from '../src/wizards/central';
import {
  PUBLIC_DIRS, assignmentArgs, autogradeBlock, cohortOrgName, cohortTerms, contentTerms, courseOrgName, courseSlugOf, formatBlock, formatError, materialsArgs,
  nextFreeNumber, nextTerm, openAt, signature, templateRepo, toggleFormat,
} from '../src/wizards/model';
import { checkOrg, checkTemplate } from '../src/wizards/verify';
import { wizardOf } from '../src/router';
import example from './fixtures/status.example.json';
import { FakeGitHub, fileBody, json } from './fake';

const NOW = Date.parse('2026-09-23T10:00:00+02:00');
const COURSE_ORG = 'hertie-dsl-demo-course-e1234';
const COHORT_ORG = 'hertie-dsl-demo-f2026';
const course: Course = {
  org: COURSE_ORG, name: 'Machine Learning', code: 'E1234', description: '', write: true, admins: ['a-example'],
  cohorts: [{ org: COHORT_ORG, term: 'f2026', termLabel: 'Fall 2026' }], meta: { assignment_defaults: { max_team_size: 5 } },
};
const ready: Loaded = { kind: 'ready', status: example as unknown as Status, sha: 's', stale: [] };
const cp = (files = new StaticFiles()): CourseProps => ({ course, loaded: ready, cohortStates: { [COHORT_ORG]: ready }, files, now: NOW });

describe('derived names', () => {
  it('follows hertie-<course-slug>-<code> for a course and hertie-<course-slug>-<term> for a cohort', () => {
    expect(courseOrgName('DSL Demo Course', 'E1234')).toBe('hertie-dsl-demo-course-e1234');
    expect(courseOrgName('Machine Learning & Society!', 'GRAD-E1339')).toBe('hertie-machine-learning-society-grad-e1339');
    expect(courseSlugOf(COURSE_ORG, 'E1234')).toBe('dsl-demo-course');
    expect(cohortOrgName(COURSE_ORG, 'E1234', 's2027')).toBe('hertie-dsl-demo-course-s2027');
    expect(ncOrg({ admins: [], course_name: 'Deep Learning', course_code: 'E2345' })).toBe('hertie-deep-learning-e2345');
    expect(ncOrg({ admins: [], course_name: 'Deep Learning', course_code: 'E2345', org: 'hertie-dl-e2345' })).toBe('hertie-dl-e2345');
  });

  it('names templates and materials the way scaffold does, and finds the next free number per term', () => {
    expect(templateRepo(4, 'f2026')).toBe('assignment-4-f2026');
    expect(nextFreeNumber(['assignment-1-f2026', 'assignment-3-f2026', 'assignment-7-s2026'], 'f2026')).toBe(4);
    expect(nextFreeNumber(['assignment-7-s2026'], 'f2026')).toBe(1);
  });

  it('offers the terms after the newest cohort', () => {
    expect(nextTerm('f2026')).toBe('s2027');
    expect(nextTerm('s2027')).toBe('f2027');
    expect(cohortTerms(['f2026'], NOW)).toEqual(['s2027', 'f2027']);
    expect(cohortTerms([], Date.parse('2026-03-01'))).toEqual(['f2026', 's2027']);
    expect(contentTerms(['f2026'], NOW)).toEqual(['f2026', 's2027']);
  });

  it('routes each wizard and its step', () => {
    expect(wizardOf('new-assignment-3')).toEqual({ name: 'new-assignment', step: 3 });
    expect(wizardOf('new-course')).toEqual({ name: 'new-course', step: undefined });
    expect(wizardOf('new-materials')).toEqual({ name: 'new-materials' });
    expect(wizardOf('templates')).toBeNull();
  });
});

describe('which step a wizard opens at', () => {
  it('never goes past the first step that is not done, and can return to any done one', () => {
    expect(openAt([false, false, false, false], 3)).toBe(1);
    expect(openAt([true, false, false, false], 4)).toBe(2);
    expect(openAt([true, true, true, false], 2)).toBe(2);
    expect(openAt([true, true, true, false])).toBe(4);
  });

  it('keeps a New assignment step done only while its answers are the ones verified', () => {
    const v = { ...initialValues(null, 'f2026'), name: 'Trees', number: 4 };
    const d = { v, verified: { 1: signature(v, S1), 2: signature(v, S2) } };
    expect(naDone(d, v, false)).toEqual([true, true, false, false]);
    expect(naDone(d, { ...v, type: 'group' }, false)).toEqual([true, false, false, false]);
    expect(naDone(d, { ...v, name: 'Forests' }, false)).toEqual([false, false, false, false]);
  });

  it('skips questions 2 and 3 when copying a template, and every question once the template exists', () => {
    const v = { ...initialValues(null, 'f2026'), name: 'Trees', number: 4, copy_from: 'assignment-2-f2026' };
    expect(naDone({ v, verified: { 1: signature(v, S1) } }, v, false)).toEqual([true, true, true, false]);
    expect(openAt(naDone({ v, verified: {} }, v, true))).toBe(4);
  });

  it('trusts live checks over what the draft remembers', () => {
    const org = 'hertie-deep-learning-e2345';
    const ok = [{ text: 'x', ok: true }];
    const no = [{ text: 'x', ok: false }];
    const d = { admins: [], orgVerified: org, setUp: org, detailsSaved: org };
    expect(ncDone(d, org, null, null)).toEqual([true, true, false, false]);
    expect(ncDone(d, org, no, null)).toEqual([false, false, false, false]);
    expect(ncDone(d, org, ok, no)).toEqual([true, false, false, false]);
    expect(nkDone({}, COHORT_ORG, ok, ok)).toEqual([true, true, false]);
    expect(nkDone({ orgVerified: 'other' }, COHORT_ORG, null, null)).toEqual([false, false, false]);
  });
});

describe('conditional fields', () => {
  it('asks only what the template is: no run setting, which is each semester\'s', () => {
    const t = assignmentWork();
    const team = layout(t, { ...initialValues(null, 'f2026'), type: 'group', submit_via: 'external' });
    expect(team.main.map((i) => i.key)).toEqual(['type', 'submit_via']);
    expect(team.main.flatMap((i) => i.under)).toEqual([]);
    expect(team.advanced).toEqual([]);
    for (const k of ['team_formation', 'max_team_size', 'submit_url', 'visibility']) expect(t).not.toHaveProperty(k);
    expect(assignmentMarking()).not.toHaveProperty('late_window_days');
  });

  it('refuses tests for a drop box and a written report, and shows the tests folder only when tests run', () => {
    const t = assignmentMarking();
    const base = initialValues(null, 'f2026');
    expect(autogradeBlock(base)).toBeNull();
    expect(t.autograde.forced?.({ ...base, submit_via: 'shared_dropbox_repo' })?.reason).toMatch(/shared drop box/);
    expect(autogradeBlock({ ...base, formats: ['latex'] })).toMatch(/written report/);
    expect(layout(t, { ...base, autograde: 'true' }).main.find((i) => i.key === 'autograde')!.under.map((i) => i.key)).toEqual(['tests']);
    expect(layout(t, { ...base, autograde: 'true', formats: ['latex'] }).main.find((i) => i.key === 'autograde')!.under).toEqual([]);
  });

  it('asks which folders to publish only when publishing openly, and not at all when copying', () => {
    const t = newMaterials(['f2026'], ['course-materials-f2026']);
    expect(layout(t, { term: 'f2026', open: false }).main.map((i) => i.key)).toEqual(['term', 'open']);
    expect(layout(t, { term: 'f2026', open: true }).main[1].under.map((i) => i.key)).toEqual(['public_dirs', 'public_types']);
    const copy = layout(t, { term: 'f2026', open: true, copy_from: 'course-materials-f2026' });
    expect(copy.main[1].under).toEqual([]);
    expect(t.open.forced?.({ copy_from: 'x' })?.value).toBe(false);
  });
});

describe('the two forbidden format pairs', () => {
  it('cannot pick Quarto beside R Markdown, either way round', () => {
    expect(formatBlock(['rmd'], 'qmd', false)).toBe('R Markdown and Quarto cannot be combined.');
    expect(formatBlock(['qmd'], 'rmd', false)).toBe('R Markdown and Quarto cannot be combined.');
    expect(formatBlock(['rmd'], 'py', false)).toBeNull();
  });

  it('cannot pick a notebook beside Python files while tests run, and can when they do not', () => {
    expect(formatBlock(['ipynb'], 'py', true)).toMatch(/cannot run automatic tests/);
    expect(formatBlock(['py'], 'ipynb', true)).toMatch(/cannot run automatic tests/);
    expect(formatBlock(['ipynb'], 'py', false)).toBeNull();
    expect(autogradeBlock({ formats: ['ipynb', 'py'] })).toMatch(/Pick one format/);
  });

  it('lets "No starter file" replace the rest, and refuses the pairs however they were reached', () => {
    expect(toggleFormat(['ipynb', 'py'], 'none')).toEqual(['none']);
    expect(toggleFormat(['none'], 'py')).toEqual(['py']);
    expect(formatBlock(['none'], 'py', false)).toMatch(/stands alone/);
    expect(formatError({ formats: ['rmd', 'qmd'] })).toMatch(/cannot be combined/);
    expect(formatError({ formats: ['ipynb', 'py'], autograde: 'true' })).toMatch(/automatic tests/);
    expect(formatError({ formats: [] })).toBe('Choose at least one.');
  });

  it('renders the blocked choice disabled with its reason', () => {
    const out = render(<FormatPicker v={{ formats: ['rmd'], autograde: 'false' }} set={() => {}} />);
    expect(out).toMatch(/<label class="check off" title="R Markdown and Quarto cannot be combined\."><input type="checkbox" id="na-fmt-qmd" disabled/);
    expect(out).toContain('R Markdown and Quarto cannot be combined.');
  });
});

describe('what the wizards send', () => {
  it('builds assignment.create args the registry accepts, for alone, teams and a copy', () => {
    const v = { ...initialValues(null, 'f2026'), name: 'Trees', number: 4 };
    const solo = assignmentArgs(v);
    expect(solo).toEqual({ name: 'Trees', number: '4', semester: 'f2026', type: 'individual', submit_via: 'assignment_repo', formats: 'ipynb', autograde: false });
    expect(validateArgs('assignment.create', JSON.parse(JSON.stringify(solo)))).toEqual([]);
    const team = assignmentArgs({ ...v, type: 'group', team_formation: 'assigned', submit_via: 'shared_dropbox_repo', visibility: 'public', autograde: 'true', formats: ['py', 'rmd'] });
    expect(team).toMatchObject({ type: 'group', autograde: false, formats: 'py,rmd' });
    // How a semester runs it is its assignments.yml: never sent (decision 0009).
    expect(team).not.toHaveProperty('team_formation');
    expect(team).not.toHaveProperty('visibility');
    expect(validateArgs('assignment.create', JSON.parse(JSON.stringify(team)))).toEqual([]);
    const copy = assignmentArgs({ ...v, copy_from: 'assignment-2-f2026' });
    expect(copy).toEqual({ name: 'Trees', number: '4', semester: 'f2026', copy_from: 'assignment-2-f2026' });
    expect(validateArgs('assignment.create', copy)).toEqual([]);
  });

  it('builds materials.create args, nothing public unless asked', () => {
    expect(materialsArgs({ term: 'f2026', open: false })).toMatchObject({ semester: 'f2026', public_dirs: '(nothing public)' });
    const open = materialsArgs({ term: 'f2026', open: true, public_dirs: 'everything', public_types: 'html' });
    expect(open).toEqual({ semester: 'f2026', public_dirs: 'everything', public_types: 'html' });
    expect(validateArgs('materials.create', open)).toEqual([]);
    expect(materialsArgs({ term: 'f2026', open: true, copy_from: 'course-materials-s2026' })).toEqual({ semester: 'f2026', copy_from: 'course-materials-s2026' });
  });

  it('offers the engine’s publish answers, with its defaults', () => {
    const args = opSpec('materials.create').args_schema.properties!;
    expect(['(nothing public)', ...PUBLIC_DIRS]).toEqual(args.public_dirs.enum);
    expect(materialsArgs({ term: 'f2026', open: true })).toEqual({ semester: 'f2026', public_dirs: PUBLIC_DIRS[0], public_types: args.public_types.default });
  });

  it('writes no run setting into grading_config.yml: those are each semester\'s', () => {
    const v = { ...initialValues(null, 'f2026'), type: 'group', max_team_size: 3, submit_via: 'external', submit_url: 'https://moodle.example.org/a4', late_window_days: 3, late_penalty_per_day: '5%' };
    expect(extrasOf(v)).toEqual({});
    const text = '# INSTRUCTOR-OWNED\ntitle: Trees\nsubmit_via: external\n';
    expect(withExtras(text, {})).toBeNull();
    expect(withExtras(text, { title: 'Forests' })).toContain('title: Forests');
  });

  it('sends the central set-up its hidden inputs and refuses a bad handle before dispatching', () => {
    expect(bootstrapInputs({ org: 'hertie-deep-learning-e2345', courseName: 'Deep Learning', code: 'E2345', admins: ['a-example', ' b-sample '] })).toEqual({
      org: 'hertie-deep-learning-e2345', course_name: 'Deep Learning', course_code: 'E2345', set_secret: 'true', admin: 'a-example,b-sample', central_ref: 'release',
    });
    expect(() => bootstrapInputs({ org: 'hertie-x', courseName: 'X', code: 'E1', admins: ['--bad'] })).toThrow(/admin handle/);
  });
});

describe('live checks against GitHub', () => {
  const client = (gh: FakeGitHub) => new GitHubClient({ token: () => 't', fetch: gh.fetch });

  it('dispatches Bootstrap Course Org on the central repo and follows the run', async () => {
    let polls = 0;
    const gh = new FakeGitHub()
      .on('POST', `/repos/${CENTRAL.owner}/${CENTRAL.repo}/actions/workflows/bootstrap-org.yml/dispatches`, { workflow_run_id: 77, run_url: '', html_url: 'https://github.com/run/77' })
      .on('GET', `/repos/${CENTRAL.owner}/${CENTRAL.repo}/actions/runs/77`, () => {
        polls++;
        return json({ id: 77, status: polls > 1 ? 'completed' : 'in_progress', conclusion: polls > 1 ? 'success' : null, html_url: 'https://github.com/run/77' });
      });
    const seen: string[] = [];
    const r = await runBootstrap(client(gh), { org: 'hertie-deep-learning-e2345', courseName: 'Deep Learning', code: 'E2345', admins: ['a-example'] }, (x) => seen.push(x.state), { pollMs: 0, sleep: async () => {} });
    expect(r).toMatchObject({ runId: 77, state: 'completed', conclusion: 'success' });
    expect(seen).toEqual(['queued', 'running', 'completed']);
    const body = gh.seen.find((x) => x.method === 'POST')!.body as { ref: string; inputs: Record<string, string>; return_run_details: boolean };
    expect(body.ref).toBe('main');
    expect(body.return_run_details).toBe(true);
    expect(body.inputs.central_ref).toBe('release');
  });

  it('says what to do when the org is missing, the bot is only invited, or all is well', async () => {
    const org = 'hertie-deep-learning-e2345';
    expect((await checkOrg(client(new FakeGitHub()), org)).map((c) => c.ok)).toEqual([false, false]);
    const invited = new FakeGitHub().on('GET', `/orgs/${org}`, { login: org }).on('GET', `/orgs/${org}/memberships/hertie-dsl-bot`, { state: 'pending', role: 'admin' });
    const [, bot] = await checkOrg(client(invited), org);
    expect(bot.ok).toBe(false);
    expect(bot.hint).toMatch(/has not accepted/);
    const fine = new FakeGitHub().on('GET', `/orgs/${org}`, { login: org }).on('GET', `/orgs/${org}/memberships/hertie-dsl-bot`, { state: 'active', role: 'admin' });
    expect((await checkOrg(client(fine), org)).map((c) => c.ok)).toEqual([true, true]);
    const hidden = new FakeGitHub().on('GET', `/orgs/${org}`, { login: org }).on('GET', `/orgs/${org}/memberships/hertie-dsl-bot`, () => json({ message: 'Must be an owner' }, 403));
    expect((await checkOrg(client(hidden), org))[1].ok).toBeNull();
  });

  it('verifies a new template: both branches and a settings file that parses', async () => {
    const repo = 'assignment-4-f2026';
    const gh = new FakeGitHub()
      .on('GET', `/repos/${COURSE_ORG}/${repo}`, { name: repo })
      .on('GET', `/repos/${COURSE_ORG}/${repo}/branches/main`, { name: 'main' })
      .on('GET', `/repos/${COURSE_ORG}/${repo}/branches/solution`, { name: 'solution' })
      .on('GET', `/repos/${COURSE_ORG}/${repo}/contents/grading_config.yml?ref=solution`, fileBody('grading_config.yml', 'title: Trees\n', 'g1'));
    const t = await checkTemplate(client(gh), COURSE_ORG, repo);
    expect(t.checks.map((c) => c.ok)).toEqual([true, true, true]);
    expect(t.config).toEqual({ text: 'title: Trees\n', sha: 'g1' });
    expect((await checkTemplate(client(new FakeGitHub()), COURSE_ORG, repo)).checks.map((c) => c.ok)).toEqual([false]);
  });
});

describe('the wizard screens', () => {
  it('New course derives the org and will not skip ahead of the org check', () => {
    const out = render(<NewCourseScreen files={new StaticFiles()} step={3} />);
    expect(out).toContain('Step 1 of 3');
    expect(out).toContain('Create the org on GitHub, install the app, then I check');
    expect(out).toContain('https://github.com/account/organizations/new?plan=free');
    expect(out).toContain('Today: invite hertie-dsl-bot as an Owner');
  });

  it('New cohort derives hertie-<course-slug>-<term> for the next term', () => {
    const out = render(<NewCohortScreen {...cp()} step={1} />);
    expect(out).toContain('New semester: Spring 2027');
    expect(out).toContain('value="hertie-dsl-demo-course-s2027"');
    expect(out).toContain('Two steps, then three editors');
  });

  it('New cohort counts the three cards from the cohort’s own files', () => {
    const files = new StaticFiles({ [`${COHORT_ORG}/semester-config/instructors.yml`]: 'instructors:\n  - github_handle: a-example\n    role: instructor\n', [`${COHORT_ORG}/semester-config/students.csv`]: 'hertie_email,name,role\n' });
    expect(cardsDone(files, COHORT_ORG)).toEqual({ staff: true, schedule: false, students: false, defaults: false });
  });

  it('New assignment asks what it is first, with the next free number and the derived repo', () => {
    const out = render(<NewAssignmentScreen {...cp()} step={4} />);
    expect(out).toContain('Question 1 of 3');
    expect(out).toContain('Three questions, then a check');
    expect(out).toContain('next free: 4');
    expect(out).toContain('Will create <code>assignment-4-f2026</code> in the course.');
    expect(out).toContain('Advanced <span class="cnt">(none changed)</span>');
  });

  it('New materials offers the term and a closed publish toggle', () => {
    const out = render(<NewMaterialsScreen {...cp()} />);
    expect(out).toContain('Will create <code>course-materials-f2026</code> in the course.');
    expect(out).toContain('Host some of it on the student site');
    expect(out).not.toContain('Which folders');
  });

  it('the schedule editor opens a new assignment entry prefilled with the template', () => {
    const files = new StaticFiles({ [`${COHORT_ORG}/semester-config/schedule.yml`]: 'timezone: Europe/Berlin\nassignments: {}\n' });
    const p: CohortProps = { course, cohort: course.cohorts[0], loaded: ready, files, now: NOW, entry: 'new', prefill: 'assignment-4-f2026' };
    const out = render(<ScheduleScreen {...p} />);
    expect(out).toContain('Assignment entry');
    expect(out).toContain('<option value="assignment-4-f2026" selected');
  });
});
