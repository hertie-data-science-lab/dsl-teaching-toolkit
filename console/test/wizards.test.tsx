// The wizards: derived names, which step opens, conditional fields, the two forbidden format
// pairs, the request args against the registry's schemas, the live checks and the central
// set-up run against a fake GitHub, and one render per wizard.

import { render } from 'preact-render-to-string';
import { describe, expect, it, vi } from 'vitest';
import { layout } from '../src/forms/Form';
import { GitHubClient } from '../src/github/client';
import type { Course } from '../src/model/discovery';
import { StaticFiles } from '../src/model/files';
import type { Loaded } from '../src/model/status';
import type { Status } from '../src/model/types';
import { validateArgs } from '../src/ops/adapter';
import { FormatPicker } from '../src/forms/FormatPicker';
import { NewAssignmentScreen, copySentences, extrasOf, initialValues, linesFor, naDone, ordinalUnconfirmed, sourceOf, S1, S2, S3, S4, withExtras } from '../src/screens/NewAssignment';
import { NewCohortScreen, cardsDone, nkDone } from '../src/screens/NewCohort';
import { NewCourseScreen, ncDone, ncOrg } from '../src/screens/NewCourse';
import { NewMaterialsScreen } from '../src/screens/NewMaterials';
import { ScheduleScreen } from '../src/screens/Schedule';
import type { CohortProps, CourseProps } from '../src/screens/types';
import { assignmentMarking, assignmentWork, newMaterials } from '../src/tiers/wizard';
import { CENTRAL, bootstrapInputs, runBootstrap } from '../src/wizards/central';
import {
  assignmentArgs, autogradeBlock, cohortOrgName, cohortTerms, contentTerms, courseOrgName, courseSlugOf, formatBlock, formatError, materialsArgs,
  IMPORT_UNTICKED_MAIN, IMPORT_UNTICKED_SOLUTION, importFixed, liveSemesters, nextTerm, openAt, ordinalInName, parseSource, signature, sourceFixed, templateRepo, tickedEntries, toggleFormat,
} from '../src/wizards/model';
import { allOk, checkOrg, checkTemplate, readSource } from '../src/wizards/verify';
import { saveDraft } from '../src/wizards/drafts';
import { GitHubError, copyError, copyFiles, type TreeEntry } from '../src/github/client';
import { OrgSteps } from '../src/wizards/Wizard';
import { installReturn, wizardOf } from '../src/router';
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

  it('names a template assignment-<name> the way scaffold.template_repo does: no number, no semester', () => {
    expect(templateRepo('Regression')).toBe('assignment-regression');
    expect(templateRepo('  Neural networks from scratch! ')).toBe('assignment-neural-networks-from-scratch');
    expect(templateRepo('Assignment: Trees & forests')).toBe('assignment-trees-forests');
    expect(templateRepo('Assignment')).toBe('');
    expect(templateRepo('***')).toBe('');
    expect(templateRepo('x'.repeat(80))).toBe(`assignment-${'x'.repeat(60)}`);
    expect(templateRepo(`${'a'.repeat(59)} b`)).toBe(`assignment-${'a'.repeat(59)}`);
  });

  it('warns on a name that carries an ordinal, and never blocks on it once the tick is given', () => {
    for (const n of ['Assignment 3', 'assignment3: trees', 'A2 Regression', 'Trees (a 4)', 'Assignment-5', '3. Trees', ' 12 Angry men']) expect(ordinalInName(n), n).toBe(true);
    for (const n of ['Regression', 'Data 2', 'Trees and forests', 'Lab work', 'Area 51 is a place', '']) expect(ordinalInName(n), n).toBe(false);
    expect(ordinalUnconfirmed({ name: 'Assignment 3' })).toBe(true);
    expect(ordinalUnconfirmed({ name: 'Assignment 3', keep_number: true })).toBe(false);
    expect(ordinalUnconfirmed({ name: 'Regression' })).toBe(false);
  });

  it('offers the live semesters to schedule a new template in, newest first', () => {
    const cs = [{ term: 's2026', org: 'b' }, { term: 'f2026', org: 'a' }, { term: 'f2025', org: 'c' }, { term: 's2027', org: 'd' }];
    expect(liveSemesters(cs, (c) => c.org !== 'c').map((c) => c.term)).toEqual(['s2027', 'f2026', 's2026']);
    expect(liveSemesters(cs, () => false)).toEqual([]);
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
    const v = { ...initialValues(null), name: 'Trees' };
    const d = { v, verified: { 1: signature(v, S1), 2: signature(v, S2) } };
    expect(naDone(d, v, false)).toEqual([true, true, false, false, false]);
    expect(naDone(d, { ...v, type: 'group' }, false)).toEqual([true, false, false, false, false]);
    expect(naDone(d, { ...v, name: 'Forests' }, false)).toEqual([false, false, false, false, false]);
    expect(naDone(d, { ...v, start: 'repo', source_repo: 'a/b' }, false)).toEqual([false, false, false, false, false]);
  });

  it('asks every question when importing too (settings are not read from the source), and none once the template exists', () => {
    const v = { ...initialValues(null), name: 'Trees', start: 'template', source_template: 'assignment-2-f2026' };
    expect(naDone({ v, verified: { 1: signature(v, S1) } }, v, false)).toEqual([true, false, false, false, false]);
    const all = { 1: signature(v, S1), 2: signature(v, S2), 3: signature(v, S3), 4: signature(v, S4) };
    expect(naDone({ v, verified: all }, v, false)).toEqual([true, true, true, true, false]);
    expect(openAt(naDone({ v, verified: {} }, v, true))).toBe(5);
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
    const team = layout(t, { ...initialValues(null), type: 'group', submit_via: 'external' });
    expect(team.main.map((i) => i.key)).toEqual(['type', 'submit_via']);
    expect(team.main.flatMap((i) => i.under)).toEqual([]);
    expect(team.advanced).toEqual([]);
    for (const k of ['team_formation', 'max_team_size', 'submit_url', 'visibility']) expect(t).not.toHaveProperty(k);
    expect(assignmentMarking()).not.toHaveProperty('late_window_days');
  });

  it('refuses tests for a drop box and a written report, and shows the tests folder only when tests run', () => {
    const t = assignmentMarking();
    const base = initialValues(null);
    expect(autogradeBlock(base)).toBeNull();
    expect(t.autograde.forced?.({ ...base, submit_via: 'shared_dropbox_repo' })?.reason).toMatch(/shared drop box/);
    expect(autogradeBlock({ ...base, formats: ['latex'] })).toMatch(/written report/);
    expect(layout(t, { ...base, autograde: 'true' }).main.find((i) => i.key === 'autograde')!.under.map((i) => i.key)).toEqual(['tests']);
    expect(layout(t, { ...base, autograde: 'true', formats: ['latex'] }).main.find((i) => i.key === 'autograde')!.under).toEqual([]);
  });

  it('asks nothing about the public website: it has its own settings', () => {
    const t = newMaterials(['f2026'], ['course-materials-f2026']);
    expect(layout(t, { term: 'f2026' }).main.map((i) => i.key)).toEqual(['term']);
    expect(Object.keys(t)).toEqual(['term', 'copy_from']);
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
  it('builds assignment.create args the registry accepts: the name and the template keys, never a number, a semester or copy_from', () => {
    const v = { ...initialValues(null), name: ' Trees ', start: 'template', source_template: 'assignment-2-f2026' };
    const solo = assignmentArgs(v);
    expect(solo).toEqual({ name: 'Trees', type: 'individual', submit_via: 'assignment_repo', formats: 'ipynb', autograde: false });
    expect(validateArgs('assignment.create', JSON.parse(JSON.stringify(solo)))).toEqual([]);
    const team = assignmentArgs({ ...v, type: 'group', team_formation: 'assigned', submit_via: 'shared_dropbox_repo', visibility: 'public', autograde: 'true', formats: ['py', 'rmd'] });
    expect(team).toMatchObject({ type: 'group', autograde: false, formats: 'py,rmd' });
    // How a semester runs it is its assignments.yml: never sent (decision 0009).
    expect(team).not.toHaveProperty('team_formation');
    expect(team).not.toHaveProperty('visibility');
    expect(validateArgs('assignment.create', JSON.parse(JSON.stringify(team)))).toEqual([]);
    expect(validateArgs('assignment.create', { type: 'individual' })).not.toEqual([]);
  });

  it('builds materials.create args', () => {
    expect(materialsArgs({ term: 'f2026' })).toEqual({ semester: 'f2026' });
    expect(validateArgs('materials.create', materialsArgs({ term: 'f2026' }))).toEqual([]);
    expect(materialsArgs({ term: 'f2026', copy_from: 'course-materials-s2026' })).toEqual({ semester: 'f2026', copy_from: 'course-materials-s2026' });
  });

  it('writes no run setting into grading_config.yml: those are each semester\'s', () => {
    const v = { ...initialValues(null), type: 'group', max_team_size: 3, submit_via: 'external', submit_url: 'https://moodle.example.org/a4', late_window_days: 3, late_penalty_per_day: '5%' };
    expect(extrasOf(v)).toEqual({});
    const text = '# INSTRUCTOR-OWNED\ntitle: Trees\nsubmit_via: external\n';
    expect(withExtras(text, {})).toBeNull();
    expect(withExtras(text, { title: 'Forests' })).toContain('title: Forests');
  });

  it('writes points per question only when some were given: with none, no total', () => {
    const v = initialValues(null);
    expect(extrasOf({ ...v, questions: [] })).toEqual({});
    expect(extrasOf({ ...v, questions: [{ name: ' ', points: '', file: '' }] })).toEqual({});
    expect(extrasOf({ ...v, questions: [{ name: 'Q1', points: '10', file: '' }, { name: 'Q2', points: '5', file: 'report.tex' }] })).toEqual({ questions: { Q1: 10, Q2: { file: 'report.tex', points: 5 } } });
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
    const oks = async (gh: FakeGitHub, opts = {}) => (await checkOrg(client(gh), org, opts)).checks.map((c) => c.ok);
    expect(await oks(new FakeGitHub())).toEqual([false, false]);
    const invited = new FakeGitHub().on('GET', `/orgs/${org}`, { login: org, id: 42 }).on('GET', `/orgs/${org}/memberships/hertie-dsl-bot`, { state: 'pending', role: 'admin' });
    const got = await checkOrg(client(invited), org);
    expect(got.id).toBe(42);
    expect(got.checks[1].ok).toBe(false);
    expect(got.checks[1].hint).toBe('Invited. The DSL team registers new courses; the bot then joins by itself.');
    const fine = new FakeGitHub().on('GET', `/orgs/${org}`, { login: org }).on('GET', `/orgs/${org}/memberships/hertie-dsl-bot`, { state: 'active', role: 'admin' });
    expect(await oks(fine)).toEqual([true, true]);
    const hidden = new FakeGitHub().on('GET', `/orgs/${org}`, { login: org }).on('GET', `/orgs/${org}/memberships/hertie-dsl-bot`, () => json({ message: 'Must be an owner' }, 403));
    expect((await oks(hidden))[1]).toBeNull();
  });

  it('finds the console app on the org across every page of installations, from an App token only', async () => {
    const org = 'hertie-deep-learning-e2345';
    const others = Array.from({ length: 100 }, (_, i) => ({ account: { login: `other-${i}`, type: 'Organization' } }));
    const base = () => new FakeGitHub()
      .on('GET', `/orgs/${org}`, { login: org, id: 42 })
      .on('GET', `/orgs/${org}/memberships/hertie-dsl-bot`, { state: 'active', role: 'admin' })
      .on('GET', '/user/installations?per_page=100&page=1', { installations: others });
    const on = base().on('GET', '/user/installations?per_page=100&page=2', { installations: [{ account: { login: 'Hertie-Deep-Learning-E2345', type: 'Organization' } }] });
    const r = await checkOrg(client(on), org, { kind: 'app', slug: 'dsl-teaching-toolkit' });
    expect(r.checks.map((c) => c.ok)).toEqual([true, true, true]);
    expect(allOk(r.checks)).toBe(true);
    const off = base().on('GET', '/user/installations?per_page=100&page=2', { installations: [] });
    const missing = await checkOrg(client(off), org, { kind: 'app', slug: 'dsl-teaching-toolkit' });
    expect(missing.checks[1]).toMatchObject({ ok: false, hint: 'Install it; GitHub brings you back here.' });
    expect(allOk(missing.checks)).toBe(false);
    // A classic or fine-grained token cannot see installations: a line that says so, and does not block.
    const classic = base();
    const told = await checkOrg(client(classic), org, { kind: 'classic', slug: 'dsl-teaching-toolkit' });
    expect(told.checks[1]).toMatchObject({ ok: null, soft: true, text: `Cannot tell from this token; the console app must be installed on ${org}.` });
    expect(allOk(told.checks)).toBe(true);
    expect(classic.seen.some((x) => x.url.includes('/user/installations'))).toBe(false);
    // A build that names no app has no install step at all.
    expect((await checkOrg(client(base()), org, { kind: 'app', slug: '' })).checks).toHaveLength(2);
  });

  it('brings the person back from installing the app to the wizard step they left', () => {
    const none = () => null;
    expect(installReturn('?course=x', none)).toBeNull();
    let asked = 0;
    expect(installReturn('', () => (asked++, null))).toBeNull();
    expect(asked).toBe(0);
    expect(installReturn('?installation_id=7&setup_action=install', none)).toBe('#new-course-1');
    expect(installReturn('?installation_id=7&setup_action=install', () => `?course=${COURSE_ORG}#new-semester-1`)).toBe(`?course=${COURSE_ORG}#new-semester-1`);
    expect(installReturn('?setup_action=update&installation_id=7', () => '#new-course-1')).toBe('#new-course-1');
    // Anything that is not a first wizard step is ignored; other parameters stay.
    expect(installReturn('?installation_id=7&setup_action=install&x=1', () => '#course')).toBe('?x=1#new-course-1');
  });

  it('renders the three links, the name and the bot handle to copy, and no Check button', () => {
    const org = 'hertie-deep-learning-e2345';
    const check = { id: 42, checks: [{ text: 'a', ok: true }, { text: 'b', ok: false }, { text: 'c', ok: false, hint: 'Invited. The DSL team registers new courses; the bot then joins by itself.' }] };
    const html = render(<OrgSteps org={org} check={check} busy={false} run={() => {}} back="#new-course-1" slug="dsl-teaching-toolkit" />);
    expect(html).toContain('href="https://github.com/account/organizations/new?plan=free"');
    expect(html).toContain('href="https://github.com/apps/dsl-teaching-toolkit/installations/new/permissions?target_id=42"');
    expect(html).toContain(`href="https://github.com/orgs/${org}/people"`);
    expect(html).toContain(`<code>${org}</code>`);
    expect(html).toContain('<code>hertie-dsl-bot</code>');
    expect(html).toContain('Invited. The DSL team registers new courses; the bot then joins by itself.');
    expect(html).toContain('Check again');
    expect(html).not.toContain('proposed');
    // Before the org exists there is nothing to install on or invite to.
    const before = render(<OrgSteps org={org} check={{ id: null, checks: [{ text: 'a', ok: false }, { text: 'b', ok: false }, { text: 'c', ok: false }] }} busy={false} run={() => {}} back="#new-course-1" slug="dsl-teaching-toolkit" />);
    expect(before).not.toContain('/installations/new');
    expect(before).not.toContain('/people"');
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
    expect(out).toContain('Three things on GitHub');
    expect(out).toContain('GitHub lets only a person do these three things. Everything after them is automatic.');
    expect(out).toContain('https://github.com/account/organizations/new?plan=free');
    expect(out).toContain('Invite hertie-dsl-bot as an Owner');
    expect(out).not.toContain('Today: invite');
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

  it('New assignment asks the name and what it starts from first: no number, no semester', () => {
    const out = render(<NewAssignmentScreen {...cp()} step={5} />);
    expect(out).toContain('Question 1 of 4');
    expect(out).toContain('Four questions, then a check');
    expect(out).toContain('The assignment’s title.');
    expect(out).toContain('Start from');
    expect(out).toContain('A template of this course');
    expect(out).toContain('A repo the console can read');
    expect(out).toContain('The console reads public repos, and private repos in organisations where the DSL console app is installed. For another organisation, an owner installs the app there first.');
    expect(out).not.toContain('Semester');
    expect(out).not.toContain('next free');
  });

  it('New assignment shows the repo under the name and the ordinal warning with its tick', () => {
    const kept = new Map<string, string>();
    vi.stubGlobal('localStorage', { getItem: (k: string) => kept.get(k) ?? null, setItem: (k: string, v: string) => kept.set(k, v), removeItem: (k: string) => kept.delete(k) });
    try {
      saveDraft(`new-assignment:${COURSE_ORG}`, { v: { ...initialValues(null), name: 'Assignment 3: Regression' }, verified: {} });
      const out = render(<NewAssignmentScreen {...cp()} step={1} />);
      expect(out).toContain('Repo: <code>assignment-3-regression</code>');
      expect(out).toContain('Numbers are added automatically when an assignment joins a semester’s schedule: the third assignment becomes assignment-3, and each student’s copy assignment-3-&lt;handle>. Keep the number in the name anyway?');
      expect(out).toContain('Keep the number');
      expect(out).toMatch(/<button class="btn" type="button" disabled[^>]*>Continue/);
      saveDraft(`new-assignment:${COURSE_ORG}`, { v: { ...initialValues(null), name: 'Regression' }, verified: {} });
      const plain = render(<NewAssignmentScreen {...cp()} step={1} />);
      expect(plain).toContain('Repo: <code>assignment-regression</code>');
      expect(plain).not.toContain('Keep the number');
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it('New assignment’s points step is optional and says it can be filled in later', () => {
    const kept = new Map<string, string>();
    vi.stubGlobal('localStorage', { getItem: (k: string) => kept.get(k) ?? null, setItem: (k: string, v: string) => kept.set(k, v), removeItem: (k: string) => kept.delete(k) });
    try {
      const v = { ...initialValues(null), name: 'Regression' };
      saveDraft(`new-assignment:${COURSE_ORG}`, { v, verified: { 1: signature(v, S1), 2: signature(v, S2), 3: signature(v, S3) } });
      const out = render(<NewAssignmentScreen {...cp()} step={4} />);
      expect(out).toContain('Question 4 of 4');
      expect(out).toContain('you can fill it in later');
      expect(out).toContain('no total is shown');
      expect(out).toContain('>Skip</button>');
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it('New materials offers the term and asks nothing about publishing', () => {
    const out = render(<NewMaterialsScreen {...cp()} />);
    expect(out).toContain('Will create <code>course-materials-f2026</code> in the course.');
    expect(out).not.toContain('public website');
  });

  it('the schedule editor opens a new assignment entry prefilled with the template', () => {
    const files = new StaticFiles({ [`${COHORT_ORG}/semester-config/schedule.yml`]: 'timezone: Europe/Berlin\nassignments: {}\n' });
    const p: CohortProps = { course, cohort: course.cohorts[0], loaded: ready, files, now: NOW, entry: 'new', prefill: 'assignment-4-f2026' };
    const out = render(<ScheduleScreen {...p} />);
    expect(out).toContain('Assignment entry');
    expect(out).toContain('<option value="assignment-4-f2026" selected');
  });

  it('the schedule editor proposes the next number as the key and names templates by their title', () => {
    const tpl = example.course!.templates[0].repo;
    const files = new StaticFiles({
      [`${COHORT_ORG}/semester-config/schedule.yml`]: 'timezone: Europe/Berlin\nassignments:\n  assignment-1:\n    course_source_repo: x\n    due_datetime: 2026-10-01\n',
      [`${COURSE_ORG}/${tpl}/grading_config.yml`]: 'title: Group project\n',
    });
    const p: CohortProps = { course, cohort: course.cohorts[0], loaded: ready, files, now: NOW, entry: 'new', prefill: tpl };
    const out = render(<ScheduleScreen {...p} />);
    expect(out).toContain(`<option value="${tpl}" selected>Group project</option>`);
    expect(out).toMatch(/<input id="e-num" type="number" min="1" max="999" value="2"/);
    expect(out).toContain('The number students see. Change it if this is not the next assignment.');
    expect(out).toContain('Each student’s repo is assignment-2-&lt;handle>.');
    expect(out).toContain('<b>Assignment 2</b>');
  });
});

describe('New assignment: import from a repo', () => {
  const client = (gh: FakeGitHub) => new GitHubClient({ token: () => 't', fetch: gh.fetch });
  const blob = (path: string, sha = `s-${path.replace(/\//g, '_')}`): TreeEntry => ({ path, mode: '100644', type: 'blob', sha });
  const SOURCE = ['README.md', 'starter.ipynb', 'data/train.csv', 'solution/answer.ipynb', 'tests/test_a.py', 'grading_config.yml', '.env', 'docs/tests/notes.md', '.github/workflows/release.yml', '.DS_Store'].map((x) => blob(x));

  it('reads owner/repo or any GitHub link of it', () => {
    expect(parseSource('hertie-x/regression')).toEqual({ owner: 'hertie-x', repo: 'regression' });
    expect(parseSource('https://github.com/hertie-x/regression.git')).toEqual({ owner: 'hertie-x', repo: 'regression' });
    expect(parseSource('github.com/a/b/tree/main/src')).toEqual({ owner: 'a', repo: 'b' });
    expect(parseSource('git@github.com:a/b.git')).toEqual({ owner: 'a', repo: 'b' });
    for (const bad of ['', 'regression', 'a/', '/b', '-a/b', 'a/..', 'https://gitlab.com/a/b']) expect(parseSource(bad), bad).toBeNull();
    expect(sourceOf({ start: 'template', source_template: 'assignment-trees' }, COURSE_ORG)).toEqual({ owner: COURSE_ORG, repo: 'assignment-trees' });
    expect(sourceOf({ start: 'fresh', source_repo: 'a/b' }, COURSE_ORG)).toBeNull();
  });

  it('ticks everything but solutions, tests, grading files, secrets, the toolkit workflows and never-material names', () => {
    const main = tickedEntries(SOURCE, IMPORT_UNTICKED_MAIN, importFixed('main')).map((e) => e.path);
    expect(main).toEqual(['README.md', 'starter.ipynb', 'data/train.csv']);
    // Re-ticking a folder the defaults leave out is one line off the list.
    const lines = IMPORT_UNTICKED_MAIN.filter((l) => l !== 'tests');
    expect(tickedEntries(SOURCE, lines, importFixed('main')).map((e) => e.path)).toEqual(['README.md', 'starter.ipynb', 'data/train.csv', 'tests/test_a.py', 'docs/tests/notes.md']);
    // The solution branch keeps its model answer and tests; its grading_config.yml is the wizard's.
    const sol = tickedEntries(SOURCE, IMPORT_UNTICKED_SOLUTION, importFixed('solution')).map((e) => e.path);
    expect(sol).toEqual(['README.md', 'starter.ipynb', 'data/train.csv', 'solution/answer.ipynb', 'tests/test_a.py', 'docs/tests/notes.md']);
    expect(importFixed('solution')('grading_config.yml')).toBe('set by this wizard');
    expect(importFixed('main')('.github/workflows')).toBe('set by the toolkit');
    // Lines kept for another source are not used for this one.
    expect(linesFor({ source: 'a/b', main: [], solution: [] }, 'c/d').main).toEqual(IMPORT_UNTICKED_MAIN);
    expect(linesFor({ source: 'a/b', main: ['x'], solution: [] }, 'a/b').main).toEqual(['x']);
  });

  it('reads the source with the user’s token: main by its default branch, and solution when there is one', async () => {
    const gh = new FakeGitHub()
      .on('GET', '/repos/a/b', { name: 'b', default_branch: 'trunk' })
      .on('GET', '/repos/a/b/git/trees/trunk?recursive=1', { sha: 't1', truncated: false, tree: [blob('README.md'), { path: 'data', mode: '040000', type: 'tree', sha: 'd' }, { path: 'lib', mode: '160000', type: 'commit', sha: 'c' }] })
      .on('GET', '/repos/a/b/branches/solution', { name: 'solution' })
      .on('GET', '/repos/a/b/git/trees/solution?recursive=1', { sha: 't2', truncated: true, tree: [blob('solution/x.py')] });
    const r = await readSource(client(gh), 'a', 'b');
    expect(r.check.ok).toBe(true);
    const lib = { path: 'lib', mode: '160000', type: 'commit', sha: 'c' };
    expect(r.main).toEqual({ branch: 'trunk', entries: [blob('README.md'), lib], truncated: false });
    // A submodule is listed as a fixed, unticked row, and never copied.
    expect(sourceFixed('main', r.main!.entries)('lib')).toBe('not copied');
    expect(sourceFixed('main', r.main!.entries)('README.md')).toBeNull();
    expect(tickedEntries(r.main!.entries, [], sourceFixed('main', r.main!.entries)).map((e) => e.path)).toEqual(['README.md']);
    expect(r.solution).toEqual({ branch: 'solution', entries: [blob('solution/x.py')], truncated: true });
    const none = await readSource(client(new FakeGitHub()), 'a', 'gone');
    expect(none.check).toMatchObject({ ok: false, hint: 'Not found, or the console cannot read it. It reads public repos and repos in organisations where the DSL console app is installed.' });
    expect(none.main).toBeNull();
    const refused = await readSource(client(new FakeGitHub().on('GET', '/repos/sso/b', () => json({ message: 'Resource protected by organization SAML enforcement.' }, 403))), 'sso', 'b');
    expect(refused.check).toMatchObject({ ok: false, hint: 'GitHub refused the read. The organisation may require the app to be installed or SSO to be authorised.' });
    const down = await readSource(client(new FakeGitHub().on('GET', '/repos/a/b', () => json({ message: 'Server Error' }, 500))), 'a', 'b');
    expect(down.check).toMatchObject({ ok: null, hint: 'GitHub did not answer (Server Error).' });
  });

  it('copies the ticked files as one commit over the branch head: blobs, one tree, one commit, a fast-forward', async () => {
    const created: unknown[] = [];
    const gh = new FakeGitHub()
      .on('GET', `/repos/${COURSE_ORG}/assignment-regression/branches/main`, { name: 'main', commit: { sha: 'head1', commit: { tree: { sha: 'tree1' } } } })
      .on('GET', /^\/repos\/a\/b\/git\/blobs\//, (req) => ({ body: { content: `${req.url.split('/').pop()}\n`, encoding: 'base64' } }))
      .on('POST', `/repos/${COURSE_ORG}/assignment-regression/git/blobs`, (req) => {
        created.push(req.body);
        return { status: 201, body: { sha: `new-${created.length}` } };
      })
      .on('POST', `/repos/${COURSE_ORG}/assignment-regression/git/trees`, { sha: 'tree2' })
      .on('POST', `/repos/${COURSE_ORG}/assignment-regression/git/commits`, { sha: 'commit2' })
      .on('PATCH', `/repos/${COURSE_ORG}/assignment-regression/git/refs/heads/main`, { object: { sha: 'commit2' } });
    const ticked = tickedEntries(SOURCE, IMPORT_UNTICKED_MAIN, importFixed('main'));
    const seen: string[] = [];
    const author = { name: 'A', email: 'a@example.org' };
    const r = await copyFiles(client(gh), { owner: 'a', repo: 'b' }, { owner: COURSE_ORG, repo: 'assignment-regression', branch: 'main' }, ticked, { message: 'm', author, progress: (n, of) => seen.push(`${n}/${of}`) });
    expect(r).toEqual({ branch: 'main', copied: 3 });
    // Each blob as the source gave it (base64, its line breaks dropped).
    expect(created).toEqual(['s-README.md', 's-starter.ipynb', 's-data_train.csv'].map((content) => ({ content, encoding: 'base64' })));
    const body = (m: string, end: string) => gh.seen.find((x) => x.method === m && x.url.endsWith(end))!.body;
    expect(body('POST', '/git/trees')).toEqual({
      base_tree: 'tree1',
      tree: [
        { path: 'README.md', mode: '100644', type: 'blob', sha: 'new-1' },
        { path: 'starter.ipynb', mode: '100644', type: 'blob', sha: 'new-2' },
        { path: 'data/train.csv', mode: '100644', type: 'blob', sha: 'new-3' },
      ],
    });
    expect(body('POST', '/git/commits')).toEqual({ message: 'm', tree: 'tree2', parents: ['head1'], author, committer: author });
    expect(body('PATCH', '/refs/heads/main')).toEqual({ sha: 'commit2', force: false });
    expect(seen).toEqual(['0/3', '1/3', '2/3', '3/3']);
  });

  it('commits nothing when a file cannot be copied, and says so per branch', async () => {
    const gh = new FakeGitHub()
      .on('GET', `/repos/${COURSE_ORG}/t/branches/solution`, { name: 'solution', commit: { sha: 'h', commit: { tree: { sha: 't' } } } })
      .on('GET', /^\/repos\/a\/b\/git\/blobs\//, () => ({ status: 403, body: { message: 'Resource not accessible by integration' } }));
    const r = await copyFiles(client(gh), { owner: 'a', repo: 'b' }, { owner: COURSE_ORG, repo: 't', branch: 'solution' }, [blob('x.py')], { message: 'm', author: { name: 'A', email: 'a@x' } });
    expect(r.copied).toBe(0);
    expect(r.error).toBe('GitHub refused the copy. Your account may lack access to the source or the template.');
    expect(gh.seen.some((x) => x.method === 'POST' || x.method === 'PATCH')).toBe(false);
    const said = copySentences([{ branch: 'main', copied: 3 }, r]);
    expect(said.ok).toEqual(['Copied 3 files to main.']);
    expect(said.bad).toEqual(['Not copied to solution. GitHub refused the copy. Your account may lack access to the source or the template.']);
    expect((await copyFiles(client(gh), { owner: 'a', repo: 'b' }, { owner: COURSE_ORG, repo: 'gone', branch: 'main' }, [blob('x')], { message: 'm', author: { name: 'A', email: 'a@x' } })).error).toBe('gone has no main branch.');
  });

  it('says why a copy failed in plain sentences, each with a full stop', () => {
    const e = (status: number, m: string) => new GitHubError(status, m, 'u');
    expect(copyError(e(404, 'Not Found'))).toBe('GitHub found no such file or repo. The console may not be able to read the source or write to the template.');
    expect(copyError(e(422, 'Update is not a fast forward'))).toBe('The branch moved during the copy. Nothing was added to it.');
    expect(copyError(e(500, 'Server Error'))).toBe('Server Error.');
    expect(copyError(new Error('Failed to fetch.'))).toBe('Failed to fetch.');
  });
});
