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
    expect(groups[0]).toMatchObject({ key: 'before', label: 'Before the term' });
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
  it('reads semester, dates, week, exams and archive', () => {
    const s = { ...STATUS, semester: { ...STATUS.semester!, archive_date: '2027-01-12' } };
    expect(headerLine(s, sched, rows, TZ)).toBe('Fall 2026, 7 Sep to 18 Dec. Week 3 of 15. Exams 22 Oct and 15 Dec. Archive 12 Jan 2027.');
  });

  it('says Starts before week 1 and leaves out what is not known, with no stray punctuation', () => {
    const s = { ...STATUS, semester: { ...STATUS.semester!, week: 0, archive_date: null } };
    expect(headerLine(s, sched, [], TZ)).toBe('Fall 2026, 7 Sep to 18 Dec. Starts 7 Sep.');
    const bare = { ...STATUS, semester: { ...STATUS.semester!, start: null, end: null, archive_date: null } };
    expect(headerLine(bare, null, [], TZ)).toBe('Fall 2026. Week 3 of 15.');
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

function mount() {
  const files = new StaticFiles({ [`${COHORT_ORG}/semester-config/schedule.yml`]: SCHEDULE });
  host = document.createElement('div');
  document.body.appendChild(host);
  act(() => render(<CohortScreen course={course} cohort={cohort} loaded={{ kind: 'ready', status: STATUS, sha: 's', stale: [] }} files={files} now={NOW} heartbeat={null} />, host!));
  return host;
}

const cell = (h: HTMLElement, w: number) => h.querySelectorAll<HTMLButtonElement>('.term-strip button.wk')[w - 1];
const button = (h: HTMLElement, label: string) => [...h.querySelectorAll('button')].find((b) => b.textContent === label)!;
const click = (b: HTMLElement) => act(() => b.click());
const problemsText = (h: HTMLElement) => [...h.querySelectorAll('.problem .p-say')].map((p) => p.textContent);

describe('the Dashboard', () => {
  it('opens on This week: week 3 pressed, its rows, its problems and every undated one', () => {
    const h = mount();
    expect(h.querySelector('h1')!.textContent).toMatch(/^Dashboard \?/);
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
    expect(listed).not.toContain('Before the term');
    click(button(h, 'All weeks'));
    const all = h.querySelector('.grid-2 .panel')!.textContent!;
    expect(all).toContain('Midterm');
    expect(all).toContain('Before the term');
    expect(all).toContain('After the term');
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
  });
});
