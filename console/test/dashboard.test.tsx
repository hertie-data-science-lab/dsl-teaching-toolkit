// @vitest-environment happy-dom
// The Dashboard (decision 0015): term weeks counted from semester.start and clamped, with a
// before and an after bucket; the header line; the week cells as filters for the rows and the
// problems, undated problems always shown; the expanded timeline.

import { render } from 'preact';
import { act } from 'preact/test-utils';
import { afterEach, describe, expect, it } from 'vitest';
import type { Course } from '../src/model/discovery';
import { StaticFiles } from '../src/model/files';
import { fmtDays } from '../src/model/format';
import { inWeeks, parseSchedule, scheduleRows, termOf, weekOf } from '../src/model/schedule';
import type { Status } from '../src/model/types';
import { CohortScreen, headerLine, weekGroups } from '../src/screens/Cohort';
import example from './fixtures/status.example.json';

const STATUS = example as unknown as Status;
const TZ = 'Europe/Berlin';
const NOW = Date.parse('2026-09-23T10:00:00+02:00'); // week 3
const COURSE_ORG = 'hertie-dsl-demo-course-e1234';
const COHORT_ORG = 'hertie-dsl-demo-f2026';
const cohort = { org: COHORT_ORG, term: 'f2026', termLabel: 'Fall 2026' };
const course: Course = { org: COURSE_ORG, name: 'Machine Learning', code: 'E1234', description: '', write: true, admins: [], cohorts: [cohort], meta: {} };

const SCHEDULE = `timezone: Europe/Berlin
semester_start: 2026-09-07
semester_end: 2026-12-18
releases:
  s5:
    event_datetime: 2026-10-08T10:00
    title: Trees and ensembles
    deploy:
      - course_source_repo: course-materials-f2026
        course_source_path: lectures/05_trees
assignments:
  assignment-2:
    course_source_repo: assignment-2-f2026
    handout_datetime: 2026-09-15T10:00
    due_datetime: 2026-09-27T23:59
events:
  orientation:
    title: Orientation
    event_datetime: 2026-09-01T09:00
  midterm:
    kind: exam
    title: Midterm
    event_datetime: 2026-10-22T10:00
  final:
    kind: exam
    title: Final
    event_datetime: 2026-12-15T10:00
`;
const sched = parseSchedule(SCHEDULE)!;
const rows = scheduleRows(STATUS, sched, NOW, TZ);
const term = termOf(STATUS, sched, '2026-09-23');

describe('term weeks', () => {
  it('count from semester.start, with a bucket before and after the term', () => {
    expect(term).toEqual({ start: '2026-09-07', end: '2026-12-18', weeks: 15 });
    expect(weekOf('2026-09-07', term, TZ)).toBe(1);
    expect(weekOf('2026-09-13T23:59:00+02:00', term, TZ)).toBe(1);
    expect(weekOf('2026-09-14T00:00', term, TZ)).toBe(2);
    expect(weekOf('2026-12-18', term, TZ)).toBe(15);
    expect(weekOf('2026-09-01T09:00', term, TZ)).toBe('before');
    expect(weekOf('2027-01-31', term, TZ)).toBe('after');
    // The zone decides the day: 23:30 UTC on Sunday is Monday in Berlin.
    expect(weekOf('2026-09-13T23:30:00Z', term, TZ)).toBe(2);
  });

  it('group every week under All weeks, empty ones too, and out-of-term rows in their buckets', () => {
    const groups = weekGroups(rows, term, TZ, 'all');
    expect(groups[0]).toMatchObject({ key: 'before', label: 'Before the semester' });
    expect(groups[0].rows.map((r) => r.entry)).toEqual(['orientation']);
    expect(groups.filter((g) => typeof g.key === 'number')).toHaveLength(15);
    expect(groups.find((g) => g.key === 4)!.rows).toEqual([]);
    const after = groups.find((g) => g.key === 'after')!;
    expect(after.rows.map((r) => r.entry)).toEqual(['archive']);
    // A chosen week list shows only those weeks that hold something, in term order.
    expect(weekGroups(rows, term, TZ, [7, 3, 4]).map((g) => g.key)).toEqual([3, 7]);
  });

  it('keep undated items apart from the dated ones in the selected weeks', () => {
    const items = [{ w: '2026-10-08T10:00:00+02:00' }, { w: '2026-09-22T10:00' }, { w: undefined }];
    expect(inWeeks(items, (i) => i.w, [3], term, TZ)).toEqual({ dated: [items[1]], undated: [items[2]] });
    expect(inWeeks(items, (i) => i.w, [], term, TZ).dated).toHaveLength(2);
  });
});

describe('the header line', () => {
  it('reads exams and archive; the semester, dates and week are the banner’s', () => {
    const s = { ...STATUS, semester: { ...STATUS.semester!, archive_date: '2027-01-12' } };
    expect(headerLine(s, sched, rows, TZ, 2026)).toBe('Exams 22 Oct and 15 Dec. Archive 12 Jan 2027.');
  });

  it('says Starts before week 1 only without an end date, and leaves out what is not known', () => {
    const s = { ...STATUS, semester: { ...STATUS.semester!, week: 0, archive_date: null } };
    expect(headerLine(s, sched, [], TZ, 2026)).toBe('');
    const open = { ...STATUS, semester: { ...STATUS.semester!, week: 0, end: null, archive_date: null } };
    expect(headerLine(open, null, [], TZ, 2026)).toBe('Starts 7 Sep.');
    // No start date: the year is said against this year.
    const bare = { ...STATUS, semester: { ...STATUS.semester!, start: null, end: null, archive_date: '2027-01-12' } };
    expect(headerLine(bare, null, [], TZ, 2026)).toBe('Archive 12 Jan 2027.');
  });

  it('names a month once', () => {
    expect(fmtDays(['2026-12-08', '2026-12-15'], TZ, 2026)).toBe('8 and 15 Dec');
    expect(fmtDays(['2026-12-01', '2026-12-08', '2026-12-15'], TZ, 2026)).toBe('1, 8 and 15 Dec');
    expect(fmtDays(['2027-01-05'], TZ, 2026)).toBe('5 Jan 2027');
  });
});

// ------------------------------------------------------------------ the screen

let host: HTMLElement | null = null;
afterEach(() => {
  if (host) render(null, host);
  host?.remove();
  host = null;
});

function mount(status: Status = STATUS, now = NOW) {
  const files = new StaticFiles({ [`${COHORT_ORG}/semester-config/schedule.yml`]: SCHEDULE });
  host = document.createElement('div');
  document.body.appendChild(host);
  act(() => render(<CohortScreen course={course} cohort={cohort} loaded={{ kind: 'ready', status, sha: 's', stale: [] }} files={files} now={now} heartbeat={null} />, host!));
  return host;
}

const cell = (h: HTMLElement, w: number) => h.querySelectorAll<HTMLButtonElement>('.term-strip button.wk')[w - 1];
const button = (h: HTMLElement, label: string) => [...h.querySelectorAll('button')].find((b) => b.textContent === label)!;
const click = (b: HTMLElement) => act(() => b.click());
const problemsText = (h: HTMLElement) => [...h.querySelectorAll('.problem .p-say')].map((p) => p.textContent);

describe('the Dashboard', () => {
  it('opens on This week: week 3 pressed, its rows, its problems and every undated one', () => {
    const h = mount();
    expect(h.querySelector('h2.h1')!.textContent).toMatch(/^Dashboard \?/);
    expect(h.querySelector('.section-head h2')!.textContent).toContain('A red number counts that week');
    expect(cell(h, 3).getAttribute('aria-pressed')).toBe('true');
    expect(cell(h, 5).getAttribute('aria-pressed')).toBe('false');
    expect(button(h, 'This week').getAttribute('aria-pressed')).toBe('true');
    expect(button(h, 'All weeks').getAttribute('aria-pressed')).toBe('false');
    // s5's problem is dated week 5: the strip counts it there, the list leaves it out.
    expect(cell(h, 5).querySelector('.wk-count')!.textContent).toBe('1');
    expect(cell(h, 3).querySelector('.wk-count')).toBeNull();
    expect(h.textContent).not.toContain('A release will be skipped');
    const say = problemsText(h);
    expect(say.some((t) => t!.includes('Session 5 cites'))).toBe(false);
    expect(h.querySelector('.p-anytime')!.textContent).toContain('Any time');
    expect(h.querySelector('.p-anytime')!.textContent).toContain('autograde: sometimes');
    expect(h.textContent).toContain('37 of 48 submitted so far.');
  });

  it('filters rows and problems to two selected weeks, and All weeks shows the buckets', () => {
    const h = mount();
    click(cell(h, 5));
    expect(cell(h, 3).getAttribute('aria-pressed')).toBe('true');
    expect(cell(h, 5).getAttribute('aria-pressed')).toBe('true');
    expect(button(h, 'This week').getAttribute('aria-pressed')).toBe('false');
    expect(h.textContent).toContain('Weeks 3 and 5');
    expect(problemsText(h).some((t) => t!.includes('Session 5 cites'))).toBe(true);
    const listed = h.querySelector('.grid-2 .panel')!.textContent!;
    expect(listed).toContain('Trees and ensembles');
    expect(listed).toContain('37 of 48 submitted so far.');
    expect(listed).not.toContain('Midterm');
    expect(listed).not.toContain('Before the semester');
    click(button(h, 'All weeks'));
    const all = h.querySelector('.grid-2 .panel')!.textContent!;
    expect(all).toContain('Midterm');
    expect(all).toContain('Before the semester');
    expect(all).toContain('After the semester');
    click(cell(h, 3));
    click(cell(h, 3)); // off again: nothing selected is All weeks
    expect(button(h, 'All weeks').getAttribute('aria-pressed')).toBe('true');
  });

  it('gives a hand out its chip, detail and Open assignment', () => {
    const h = mount();
    click(cell(h, 3));
    click(cell(h, 2));
    const listed = h.querySelector('.grid-2 .panel')!;
    expect(listed.textContent).toContain('Hand out');
    expect(listed.textContent).toContain('Hands out at its time.');
    expect(listed.querySelector('a[href="#assignment-assignment-2"]')!.textContent).toBe('Open assignment');
  });

  it('expands into the full timeline by week, read-only, and collapses back', () => {
    const h = mount();
    const expand = h.querySelector<HTMLButtonElement>('.wk-expand')!;
    expect(expand.getAttribute('aria-expanded')).toBe('false');
    click(expand);
    expect(h.querySelector('.term-strip')).toBeNull();
    const tl = h.querySelector('.wk-timeline')!;
    expect(tl.textContent).toContain('Week 4 from Mon 28 Sep: nothing scheduled');
    expect(tl.querySelector('a[href="#schedule-s5"]')).not.toBeNull();
    expect(tl.querySelectorAll('.wk-empty').length).toBeGreaterThan(5);
    expect(tl.querySelector('input, select, textarea')).toBeNull();
    click(h.querySelector<HTMLButtonElement>('.wk-expand')!);
    expect(h.querySelector('.term-strip')).not.toBeNull();
    expect(h.querySelector('.wk-expand')!.getAttribute('aria-label')).toBe('Show every week');
  });

  it('shows what is overdue under This week, and filters other weeks strictly', () => {
    const late = { ...STATUS.problems![0], id: 'schedule:s2:SOURCE_MISSING', text: 'Session 2 was skipped.', when: '2026-09-17T10:00:00+02:00' };
    const h = mount({ ...STATUS, problems: [late, ...STATUS.problems!] });
    expect(h.querySelector('.p-overdue')!.textContent).toContain('Overdue');
    expect(h.querySelector('.p-overdue')!.textContent).toContain('Session 2 was skipped.');
    click(cell(h, 3));
    click(cell(h, 4)); // week 4 alone: strict, so nothing overdue and nothing dated
    expect(h.querySelector('.p-overdue')).toBeNull();
    expect(h.textContent).toContain('No problems in week 4. 2 in other weeks.');
    expect(h.textContent).toContain('Nothing scheduled in week 4.');
    click(button(h, 'Show all weeks'));
    expect(button(h, 'All weeks').getAttribute('aria-pressed')).toBe('true');
  });

  it('never says no problems this week while the only ones are undated', () => {
    const h = mount({ ...STATUS, problems: STATUS.problems!.filter((p) => !p.when) });
    expect(h.textContent).not.toContain('No problems this week');
    expect(h.querySelector('.p-anytime')).not.toBeNull();
  });

  it('selects week 1 before the semester and All weeks after it', () => {
    const before = mount({ ...STATUS, semester: { ...STATUS.semester!, week: 0 } }, Date.parse('2026-09-01T10:00:00+02:00'));
    expect(cell(before, 1).getAttribute('aria-pressed')).toBe('true');
    expect(button(before, 'This week').getAttribute('aria-pressed')).toBe('true');
    expect(before.textContent).not.toContain('Before the semester');
    render(null, before);
    before.remove();
    const after = mount(STATUS, Date.parse('2027-01-20T10:00:00+01:00'));
    expect(button(after, 'All weeks').getAttribute('aria-pressed')).toBe('true');
    expect(after.textContent).toContain('After the semester');
  });
});

describe('the Dashboard and explicit numbers (decision 0020)', () => {
  it('counts an entry with no number in its week and says it will be skipped', () => {
    const status: Status = {
      ...STATUS,
      releases: [...(STATUS.releases ?? []), { id: 'guest', when: '2026-09-24T10:00:00+02:00', kind: 'lecture', number: null, title: 'Guest', state: 'will_be_skipped', source: { repo: 'course-materials-f2026', path: 'lectures/guest' }, dest: { repo: 'materials', path: 'lectures/guest' }, show_on_site: true, tbc: false }],
      problems: [...(STATUS.problems ?? []), { id: 'number:lecture:guest', scope: 'semester', stage: 'K4', text: 'Give guest a number.', stops: 'The release on Thu 24 Sep will be skipped.', fix: { repo: `${COHORT_ORG}/semester-config`, path: 'schedule.yml', line: 9, screen: 'schedule', entry: 'guest' }, when: '2026-09-24T10:00:00+02:00' }],
    };
    const h = mount(status);
    expect(cell(h, 3).querySelector('.wk-count')!.textContent).toBe('1');
    expect(problemsText(h).some((t) => t!.includes('Give guest a number.'))).toBe(true);
    expect(h.textContent).toContain('Will be skipped: it has no number.');
    expect(h.querySelector('a[href="#schedule-guest"]')).not.toBeNull();
  });
});
