// @vitest-environment happy-dom
// The Dashboard (decisions 0015 and 0034): term weeks counted from semester.start and clamped,
// with a before and an after bucket; the header line; the week cells as filters for the agenda;
// the Problems list (now and soon, whatever the strip picks); the expanded timeline.

import { render } from 'preact';
import { act } from 'preact/test-utils';
import { afterEach, describe, expect, it } from 'vitest';
import type { Course } from '../src/model/discovery';
import { StaticFiles } from './staticFiles';
import { fmtDays } from '../src/model/format';
import { inWeeks, parseSchedule, scheduleRows, termOf, weekGroups, weekOf } from '../src/model/schedule';
import type { Status } from '../src/model/types';
import { CohortScreen, headerLine } from '../src/screens/Cohort';
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
    const groups = weekGroups(rows, (r) => r.when, term, TZ, 'all');
    expect(groups[0]).toMatchObject({ key: 'before', label: 'Before the semester' });
    expect(groups[0].rows.map((r) => r.entry)).toEqual(['orientation']);
    expect(groups.filter((g) => typeof g.key === 'number')).toHaveLength(15);
    expect(groups.find((g) => g.key === 4)!.rows).toEqual([]);
    const after = groups.find((g) => g.key === 'after')!;
    expect(after.rows.map((r) => r.entry)).toEqual(['archive']);
    // A chosen week list shows only those weeks that hold something, in term order.
    expect(weekGroups(rows, (r) => r.when, term, TZ, [7, 3, 4]).map((g) => g.key)).toEqual([3, 7]);
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
const problemsText = (h: HTMLElement) => [...h.querySelectorAll('#dash-panel-problems .problem .p-say')].map((p) => p.textContent);
const tabs = (h: HTMLElement) => [...h.querySelectorAll('[role="tab"]')].map((b) => b.childNodes[0].textContent);
const selectedTab = (h: HTMLElement) => h.querySelector('[role="tab"][aria-selected="true"]')!.childNodes[0].textContent;
const tabCount = (h: HTMLElement, label: string) => {
  const n = [...h.querySelectorAll('[role="tab"]')].find((b) => b.childNodes[0].textContent === label)!.querySelector('.n')!;
  return n.classList.contains('ok') ? '✓' : n.textContent;
};

describe('the Dashboard', () => {
  it('opens on This week: week 3 pressed, its rows, and the Problems tab: every problem now or soon, whatever the week', () => {
    const h = mount();
    expect(h.querySelector('h2.h1')!.textContent).toMatch(/^Dashboard \?/);
    expect(h.querySelector('.stack > .panel .section-head h2')!.textContent).toContain('A red number counts that week');
    expect(cell(h, 3).getAttribute('aria-pressed')).toBe('true');
    expect(cell(h, 5).getAttribute('aria-pressed')).toBe('false');
    // The strip is the only picker: no This week / All weeks buttons, no ? beside Refresh, no problem badge.
    expect(button(h, 'This week')).toBeUndefined();
    expect(button(h, 'All weeks')).toBeUndefined();
    expect(h.querySelector('.page-head .actions .hint-btn')).toBeNull();
    expect(h.querySelector('.page-head .actions')!.textContent).toContain('Refresh');
    expect(h.querySelector('.page-head .probs')).toBeNull();
    // One tabbed panel under the strip, Problems first and open (decision 0034).
    expect(tabs(h)).toEqual(['Problems', 'Coming up', 'Suggestions', 'Set aside', 'Setup']);
    expect(selectedTab(h)).toBe('Problems');
    // An older status has no horizon: the next 7 days from now. s5 (8 Oct) is beyond them: not counted.
    expect(cell(h, 5).querySelector('.wk-count')).toBeNull();
    expect(problemsText(h).some((t) => t!.includes('Session 5 cites'))).toBe(false);
    expect(h.querySelector('.p-any')!.textContent).toContain('Any time');
    expect(h.querySelector('.p-any')!.textContent).toContain('autograde: sometimes');
    expect(h.textContent).toContain('37 of 48 submitted so far.');
  });

  it('filters the agenda to two selected weeks, and leaves the Problems tab alone', () => {
    const soon = { ...STATUS.problems![0], id: 'schedule:s4:SOURCE_MISSING', text: 'Session 4 cites a folder that is not there.', when: '2026-09-28T10:00:00+02:00' };
    const h = mount({ ...STATUS, problems: [soon, ...STATUS.problems!] });
    const before = problemsText(h);
    expect(before.some((t) => t!.includes('Session 4 cites'))).toBe(true);
    click(cell(h, 5));
    expect(cell(h, 3).getAttribute('aria-pressed')).toBe('true');
    expect(cell(h, 5).getAttribute('aria-pressed')).toBe('true');
    expect(h.textContent).toContain('Planned in weeks 3 and 5');
    expect(problemsText(h)).toEqual(before);
    const listed = h.querySelector('.grid-2 .panel')!.textContent!;
    expect(listed).toContain('Trees and ensembles');
    expect(listed).toContain('37 of 48 submitted so far.');
    expect(listed).not.toContain('Midterm');
    expect(listed).not.toContain('Before the semester');
    click(cell(h, 3));
    click(cell(h, 5)); // nothing selected is all weeks
    const all = h.querySelector('.grid-2 .panel')!.textContent!;
    expect(all).toContain('Midterm');
    expect(all).toContain('Before the semester');
    expect(all).toContain('After the semester');
    // The strip counts it in its week, red.
    expect(cell(h, 4).querySelector('.wk-count')!.textContent).toBe('1');
  });

  it('says a late release is late, with its Details', () => {
    const late: Status = { ...STATUS, releases: STATUS.releases!.map((r) => (r.id === 's5' ? { ...r, state: 'late' as const } : r)) };
    const h = mount(late);
    click(cell(h, 5));
    const listed = h.querySelector('.grid-2 .panel')!;
    expect(listed.textContent).toContain('Late: due Thu 8 Oct, not released yet.');
    expect(listed.textContent).not.toContain('Goes to students at its time');
    expect(listed.querySelector('a[href="#release-s5"]')!.textContent).toBe('Details');
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
    // The chevron sits before the heading (decision 0031 rule 11).
    const expand = h.querySelector<HTMLButtonElement>('.section-head .lead > button.chev')!;
    expect(expand.nextElementSibling!.tagName).toBe('H2');
    expect(expand.getAttribute('aria-label')).toBe('Show every week as a list');
    expect(expand.getAttribute('aria-expanded')).toBe('false');
    expect(h.querySelector('.wk-expand')).toBeNull();
    click(expand);
    expect(h.querySelector('.term-strip')).toBeNull();
    const tl = h.querySelector('.wk-timeline')!;
    expect(tl.textContent).toContain('Week 4 from Mon 28 Sep: nothing scheduled');
    expect(tl.querySelector('a[href="#schedule-s5"]')).not.toBeNull();
    expect(tl.querySelectorAll('.wk-empty').length).toBeGreaterThan(5);
    expect(tl.querySelector('input, select, textarea')).toBeNull();
    expect(expand.getAttribute('aria-label')).toBe('Show the week strip');
    click(expand);
    expect(h.querySelector('.term-strip')).not.toBeNull();
    expect(expand.getAttribute('aria-expanded')).toBe('false');
  });

  it('groups the problems: overdue, the next 7 days, then any time', () => {
    const late = { ...STATUS.problems![0], id: 'schedule:s2:SOURCE_MISSING', text: 'Session 2 was skipped.', when: '2026-09-17T10:00:00+02:00' };
    const soon = { ...STATUS.problems![0], id: 'schedule:s4:SOURCE_MISSING', text: 'Session 4 cites a folder that is not there.', when: '2026-09-28T10:00:00+02:00' };
    const h = mount({ ...STATUS, problems: [soon, late, ...STATUS.problems!] });
    expect([...h.querySelectorAll('#dash-panel-problems .week-h')].map((x) => x.textContent)).toEqual(['Overdue', 'Next 7 days', 'Any time']);
    expect(h.querySelector('.p-overdue')!.textContent).toContain('Session 2 was skipped.');
    expect(h.querySelector('.p-next')!.textContent).toContain('Session 4 cites');
    // Picking week 4 changes the agenda only.
    click(cell(h, 3));
    click(cell(h, 4));
    expect(h.querySelector('.p-overdue')).not.toBeNull();
    expect(h.textContent).toContain('Nothing scheduled in week 4.');
  });

  it('names the agenda panel for what it plans, with a ?', () => {
    const h = mount();
    const head = () => h.querySelector('.grid-2 .panel .section-head h2')!;
    expect(head().textContent).toMatch(/^Planned this week \?/);
    expect(head().querySelector('.hint-wrap')).not.toBeNull();
    click(cell(h, 5));
    expect(head().textContent).toMatch(/^Planned in weeks 3 and 5 /);
    click(cell(h, 3));
    click(cell(h, 5));
    expect(head().textContent).toMatch(/^Planned, all weeks /);
  });

  it('makes the verdict the one problem count: a button that opens the Problems tab and moves there', () => {
    const h = mount();
    click(h.querySelector<HTMLButtonElement>('[role="tab"][data-key="setup"]')!);
    expect(selectedTab(h)).toBe('Setup');
    const verdict = h.querySelector<HTMLButtonElement>('.verdict button.verdict-btn')!;
    expect(verdict.textContent).toBe('Needs fixing: 1 problem in the next 7 days');
    click(verdict);
    expect(selectedTab(h)).toBe('Problems');
    expect(document.activeElement).toBe(h.querySelector('#dash-problems'));
  });

  it('never says no problems while the only ones are undated', () => {
    const h = mount({ ...STATUS, problems: STATUS.problems!.filter((p) => !p.when) });
    expect(h.textContent).not.toContain('No problems.');
    expect(h.querySelector('.p-any')).not.toBeNull();
  });

  it('selects week 1 before the semester and all weeks after it', () => {
    const before = mount({ ...STATUS, semester: { ...STATUS.semester!, week: 0 } }, Date.parse('2026-09-01T10:00:00+02:00'));
    expect(cell(before, 1).getAttribute('aria-pressed')).toBe('true');
    expect(before.textContent).toContain('Planned this week');
    render(null, before);
    before.remove();
    const after = mount(STATUS, Date.parse('2027-01-20T10:00:00+01:00'));
    expect(after.textContent).toContain('Planned, all weeks');
    expect(after.textContent).toContain('After the semester');
  });
});

describe('the Dashboard and the horizon (decision 0034)', () => {
  it('neither counts nor lists a later problem: it waits in Coming up, by week, with when it becomes a problem', () => {
    const h = mount();
    expect(tabCount(h, 'Coming up')).toBe('1');
    const coming = h.querySelector('#dash-panel-coming')!;
    expect(coming.querySelector('.week-h')!.textContent).toBe('Week 5 from 5 Oct · problems from 1 Oct');
    expect(coming.querySelector('li')!.textContent).toContain('Session 5 cites folder lectures/05_trees');
    expect(coming.querySelector('li a')!.getAttribute('href')).toBe('#schedule-s5');
    expect(coming.textContent).toContain('Each item becomes a problem 7 days before the date it is needed by');
  });

  it('says the term’s health over the horizon, with what is coming up, and prints the engine’s days', () => {
    const h = mount();
    expect(h.querySelector('.verdict')!.textContent).toBe('!Needs fixing: 1 problem in the next 7 days1 coming up');
    const fine = mount({ ...STATUS, horizon: { days: 14 }, problems: [{ ...STATUS.problems![0], when: '2026-11-08T10:00:00+01:00' }], semester: { ...STATUS.semester!, stages: { ...STATUS.semester!.stages, K4: 'done', K5: 'done' } } });
    expect(fine.querySelector('.verdict')!.className).toBe('verdict ok');
    expect(fine.querySelector('.verdict')!.textContent).toBe('On track: nothing to fix in the next 14 days1 coming up');
    // One-time setup is not in the verdict or the lede: the tick on the Setup tab says it.
    expect(fine.querySelector('.lede')!.textContent).not.toContain('Setup complete');
    expect(tabCount(fine, 'Setup')).toBe('✓');
  });

  it('takes the engine’s bites over its own reading of when', () => {
    const h = mount({ ...STATUS, problems: [{ ...STATUS.problems![0], bites: 'soon' }] });
    expect(problemsText(h).some((t) => t!.includes('Session 5 cites'))).toBe(true);
    expect(cell(h, 5).querySelector('.wk-count')!.textContent).toBe('1');
  });

  it('names a semester that is not set up by its first missing step', () => {
    const s: Status = { ...STATUS, problems: [], semester: { ...STATUS.semester!, stages: { ...STATUS.semester!.stages, K3: 'todo', K4: 'done', K5: 'done' }, stage_why: { K3: 'No instructor is declared in instructors.yml yet.' } } };
    const h = mount(s);
    expect(h.querySelector('.verdict')!.textContent).toBe('Not set up: no instructor is declared in instructors.yml yet');
    expect(tabCount(h, 'Setup')).toBe('5 of 6');
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
