// The course overview as a status board (decision 0025): the problems roll-up, each
// semester's next automatic event, the website block's indicator and Recent activity.

import { signal } from '@preact/signals';
import { render } from 'preact-render-to-string';
import { describe, expect, it } from 'vitest';
import { bannerLine } from '../src/app';
import { EnvCtx, type Env } from '../src/env';
import type { Course } from '../src/model/discovery';
import { StaticFiles } from './staticFiles';
import { nextEvent, nextEventWords, recentActivity, rollUpProblems, whoWord, type Activity } from '../src/model/status';
import type { Loaded } from '../src/model/status';
import type { Operation, Problem, Status } from '../src/model/types';
import { CourseScreen, SiteLive, siteLiveState } from '../src/screens/Course';
import { ProblemCards } from '../src/ui/bits';
import example from './fixtures/status.example.json';

const STATUS = example as unknown as Status;
const NOW = Date.parse('2026-09-23T10:00:00+02:00');
const COURSE_ORG = 'hertie-dsl-demo-course-e1234';
const COHORT_ORG = 'hertie-dsl-demo-f2026';
const OLD_ORG = 'hertie-dsl-demo-f2025';
const cohort = { org: COHORT_ORG, term: 'f2026', termLabel: 'Fall 2026' };
const old = { org: OLD_ORG, term: 'f2025', termLabel: 'Fall 2025' };
const course: Course = { org: COURSE_ORG, name: 'Machine Learning', code: 'E1234', description: '', write: true, admins: [], cohorts: [cohort, old], meta: null };
const ready = (status: Status): Loaded => ({ kind: 'ready', status, sha: 's', stale: [] });
/** One overview panel's markup, from `from` to its section's end. */
const panelOf = (out: string, from: string) => out.slice(out.indexOf(from), out.indexOf('</section>', out.indexOf(from)));
const text = (v: preact.VNode) => render(v).replace(/<[^>]+>/g, ' ').replace(/&amp;/g, '&').replace(/\s+/g, ' ');

const prob = (id: string, scope: 'course' | 'semester', stage = 'K4'): Problem => ({ id, scope, stage, text: `${id} broke.`, stops: 'Something stops.', fix: { repo: `${COHORT_ORG}/semester-config`, path: 'schedule.yml', screen: 'schedule', entry: 's5' } });
const op = (run_id: number, finished: string, o: Partial<Operation> = {}): Operation => ({ run_id, op: 'release.entry', conclusion: 'done', summary: '', finished, ...o });

describe('Problems roll-up', () => {
  it('lists the course’s first, then each live semester’s tagged, a repeated course fault once', () => {
    const list = rollUpProblems([prob('tpl', 'course', 'C5')], [{ org: COHORT_ORG, label: 'Fall 2026', problems: [prob('tpl', 'course', 'C5'), prob('sched', 'semester')] }]);
    expect(list.map((p) => [p.id, p.semester?.label])).toEqual([['tpl', undefined], ['sched', 'Fall 2026']]);
  });

  it('tags a semester’s card with a link to its Dashboard, and its Fix opens that semester', () => {
    const out = render(<ProblemCards list={rollUpProblems([], [{ org: COHORT_ORG, label: 'Fall 2026', problems: [prob('sched', 'semester')] }])} />);
    expect(out).toContain(`<a class="chip p-tag" href="?cohort=${COHORT_ORG}#dashboard">Fall 2026</a>`);
    expect(out).toContain(`<a class="btn small" href="?cohort=${COHORT_ORG}#schedule-s5">Fix</a>`);
    // An untagged card keeps its own page's link.
    expect(render(<ProblemCards list={[prob('sched', 'semester')]} />)).toContain('<a class="btn small" href="#schedule-s5">Fix</a>');
  });

  it('counts every problem in the panel head and leaves archived semesters out', () => {
    const archived = ready({ ...STATUS, semester: { ...STATUS.semester!, live: false }, problems: [prob('gone', 'semester')] });
    const out = render(<CourseScreen course={course} loaded={{ kind: 'absent' }} cohortStates={{ [COHORT_ORG]: ready(STATUS), [OLD_ORG]: archived }} files={new StaticFiles()} now={NOW} />);
    const panel = panelOf(out, 'id="course-problems"');
    expect(panel).toContain('<span class="count-badge" aria-label="2 problems">2</span>');
    expect(panel).toContain('>Fall 2026</a>');
    expect(panel).not.toContain('gone broke.');
  });
});

describe('a semester’s next automatic event', () => {
  const s: Status = {
    ...STATUS,
    releases: [{ ...STATUS.releases![0], id: 's7', number: 7, when: '2026-10-08T10:00:00+02:00', state: 'planned' }, { ...STATUS.releases![0], when: '2026-09-30T10:00:00+02:00', state: 'will_be_skipped' }],
    assignments: [{ ...STATUS.assignments![0], slug: 'assignment-2', number: 2, state: 'declared', handout: '2026-10-06T10:00:00+02:00', solution_shown: '2026-12-01T10:00:00+01:00' }],
  };
  it('is the earliest future release, hand out, solution shown or archive', () => {
    expect(nextEvent(s, NOW)).toEqual({ title: 'Assignment 2', word: 'hand out', when: '2026-10-06T10:00:00+02:00' });
    expect(nextEventWords(nextEvent(s, NOW), 'Europe/Berlin', 2026)).toBe('Next: Assignment 2 hand out, Tue 6 Oct');
    // A release automation will skip is not one; past the hand out, the planned release is next.
    expect(nextEvent(s, Date.parse('2026-10-07T00:00:00+02:00'))?.title).toBe('Lecture 7');
    expect(nextEventWords(nextEvent(s, Date.parse('2026-12-02T00:00:00+01:00')), 'Europe/Berlin', 2026)).toBe('Next: archive, Sun 31 Jan 2027');
  });
  it('skips the hand out of an assignment already handed out, and takes the held solution date', () => {
    const out: Status = { ...s, releases: [], assignments: [{ ...s.assignments![0], state: 'open', solution_shown: '2026-10-01T10:00:00+02:00', solution_held_until: '2026-10-20T23:59:00+02:00' }] };
    expect(nextEvent(out, NOW)).toEqual({ title: 'Assignment 2', word: 'solution shown', when: '2026-10-20T23:59:00+02:00' });
  });
  it('says Nothing scheduled when none is ahead', () => {
    expect(nextEventWords(nextEvent({ ...s, semester: { ...s.semester!, archive_date: null } }, Date.parse('2027-03-01T00:00:00Z')))).toBe('Nothing scheduled');
  });
  it('shows on each live semester’s row', () => {
    const t = text(<CourseScreen course={course} loaded={{ kind: 'absent' }} cohortStates={{ [COHORT_ORG]: ready(s) }} files={new StaticFiles()} now={NOW} />);
    expect(t).toContain('Fall 2026 Live Week 3 of 15 Next: Assignment 2 hand out, Tue 6 Oct');
  });
  it('says when the semester starts before week 1, and no week while its dates are unset', () => {
    const row = (semester: Partial<Status['semester']>) =>
      text(<CourseScreen course={course} loaded={{ kind: 'absent' }} cohortStates={{ [COHORT_ORG]: ready({ ...s, semester: { ...s.semester!, ...semester } }) }} files={new StaticFiles()} now={NOW} />);
    expect(row({ week: 0 })).toContain('Fall 2026 Live Starts Mon 7 Sep Next:');
    const unset = row({ week: null, weeks: null, start: null, end: null });
    expect(unset).toContain('Fall 2026 Live Next:');
    expect(unset).not.toMatch(/Week (null|undefined|0)/);
  });
});

describe('the public website indicator', () => {
  const at = (h: number) => new Date(NOW - h * 3600000).toISOString();
  const say = (published: boolean, last: Operation | undefined, running: boolean) => render(<SiteLive org={COURSE_ORG} published={published} last={last} running={running} now={NOW} />);
  it('reads live, publishing, failed or not published', () => {
    const done = op(7, at(3), { op: 'course.publish_website' });
    const failed = op(8, at(1), { op: 'course.publish_website', conclusion: 'failed' });
    expect(siteLiveState(true, done, false)).toBe('live');
    expect(siteLiveState(true, done, true)).toBe('publishing');
    expect(siteLiveState(true, failed, false)).toBe('failed');
    expect(siteLiveState(false, undefined, false)).toBe('off');
    const live = say(true, done, false);
    expect(live).toContain('<span class="dot ok" aria-hidden="true"></span><span>Live · updated 3 h ago</span>');
    expect(live).toContain(`href="https://${COURSE_ORG}.github.io"`);
    expect(say(true, undefined, false)).toContain('<span>Live</span>');
    expect(say(false, undefined, true)).toContain('<span class="dot amber" aria-hidden="true"></span><span>Publishing…</span>');
    const bad = say(true, failed, false);
    expect(bad).toContain('<span class="dot bad"');
    expect(bad).toContain(`href="https://github.com/${COURSE_ORG}/.github/actions/runs/8"`);
    expect(bad).toContain('Last publish failed');
    const off = say(false, undefined, false);
    expect(off).toContain('<span class="dot idle" aria-hidden="true"></span><span>Not published</span>');
    expect(off).not.toContain('github.io');
  });
  it('sits inside Course details, with Publish and Edit website details', () => {
    const out = render(<CourseScreen course={course} loaded={{ kind: 'absent' }} cohortStates={{ [COHORT_ORG]: ready(STATUS) }} files={new StaticFiles()} now={NOW} />);
    const details = panelOf(out, '<h2>Course details');
    expect(details).toContain('<h3>Public website');
    expect(details).toContain('Not published');
    expect(details).toContain('>Publish website</button>');
    expect(details).toContain('href="#website">Edit website details</a>');
  });
});

describe('Recent activity', () => {
  it('merges the lists, newest first, each run once, five at most', () => {
    const a = (run: number, h: number, where = 'Fall 2026'): Activity => ({ ...op(run, new Date(NOW - h * 3600000).toISOString()), where });
    const rows = recentActivity([[a(1, 1, 'course')], [a(1, 1), a(2, 5), a(3, 2), a(4, 3)], [a(5, 9), a(6, 0.5), a(7, 4)]]);
    expect(rows.map((r) => r.run_id)).toEqual([6, 1, 3, 4, 7]);
    expect(rows[1].where).toBe('course');
  });
  it('says you, automation, or the login', () => {
    expect(whoWord('Octo', 'octo')).toBe('you');
    expect(whoWord('', 'octo')).toBe('automation');
    expect(whoWord('a-example', 'octo')).toBe('a-example');
    expect(whoWord(undefined, 'octo')).toBeNull();
  });
  it('lists each operation with its semester, age and who, linked to its run', () => {
    const outcome = JSON.stringify({ schema: 'dsl.outcome/1', op: 'release.entry', run_id: 4821, actor: 'octo', preview: false, conclusion: 'done', summary: 'x' });
    const files = new StaticFiles({ [`${COHORT_ORG}/semester-config/.system/outcomes/release.entry.json`]: outcome });
    const runs = signal([{ run_id: 99, op: 'site.update', conclusion: 'failed', summary: '', finished: new Date(NOW).toISOString(), course: COURSE_ORG }]);
    const env = { ops: { runs, current: signal(null) }, user: { login: 'octo' } } as unknown as Env;
    const at = { ...STATUS, operations: [{ ...STATUS.operations![0], finished: '2026-09-23T05:00:00Z' }] };
    const out = render(<EnvCtx.Provider value={env}><CourseScreen course={course} loaded={{ kind: 'absent' }} cohortStates={{ [COHORT_ORG]: ready(at) }} files={files} now={NOW} /></EnvCtx.Provider>);
    const panel = panelOf(out, '<h2>Recent activity');
    expect(panel).toContain(`<a class="textlink" href="?cohort=${COHORT_ORG}#operations">All operations</a>`);
    const t = panel.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ');
    expect(t).toContain('Update site just now course · you');
    expect(t).toContain('Release 3 h ago Fall 2026 · you');
    expect(panel).toContain(`href="https://github.com/${COURSE_ORG}/.github/actions/runs/4821"`);
    expect(panel).toContain('class="mark fail"');
  });
  it('reads who ran a course operation from the course’s own outcome file', () => {
    const outcome = JSON.stringify({ schema: 'dsl.outcome/1', op: 'course.publish_website', run_id: 50, actor: 'a-example', preview: false, conclusion: 'done', summary: 'x' });
    const files = new StaticFiles({ [`${COURSE_ORG}/.github/.system/outcomes/course.publish_website.json`]: outcome });
    const own = ready({ ...STATUS, operations: [op(50, '2026-09-23T06:00:00Z', { op: 'course.publish_website' })] });
    const t = text(<CourseScreen course={{ ...course, cohorts: [] }} loaded={own} cohortStates={{}} files={files} now={NOW} />);
    expect(t).toContain('Publish website 2 h ago course · a-example');
  });
  it('dates the website from the last real publish, not a preview', () => {
    const pub = { ...STATUS, course: { ...STATUS.course!, stages: { ...STATUS.course!.stages, C6: 'done' as const } } };
    const sem = ready({ ...pub, operations: [op(60, '2026-09-23T05:00:00Z', { op: 'course.publish_website' }), op(61, '2026-09-23T07:00:00Z', { op: 'course.publish_website', conclusion: 'previewed' })] });
    const t = text(<CourseScreen course={course} loaded={{ kind: 'absent' }} cohortStates={{ [COHORT_ORG]: sem }} files={new StaticFiles()} now={NOW} />);
    expect(t).toContain('Live · updated 3 h ago');
  });
  it('says Nothing has run yet, with no All operations link without a live semester', () => {
    const out = render(<CourseScreen course={{ ...course, cohorts: [] }} loaded={{ kind: 'absent' }} cohortStates={{}} files={new StaticFiles()} now={NOW} />);
    const panel = panelOf(out, '<h2>Recent activity');
    expect(panel).toContain('Nothing has run yet.');
    expect(panel).not.toContain('All operations');
  });
});

describe('the semester banner’s line', () => {
  it('has the state, the week and the dates, each only when known', () => {
    const line = bannerLine(STATUS.semester);
    expect(line.week).toBe('Week 3 of 15');
    expect(line.dates).toBe('Mon 7 Sep to Fri 18 Dec');
    expect(text(<>{line.chip}</>).trim()).toBe('Live');
    expect(bannerLine({ ...STATUS.semester!, week: 0, start: null })).toMatchObject({ week: undefined, dates: undefined });
    expect(bannerLine(undefined)).toEqual({});
  });
});
