// The schedule editor's forms against the YAML the mock-up shows beside each one
// (design/mockups/schedule-forms.html v3, schedule.yml): golden files, key generation and
// renumbering, and the validation the Save button names.

import { describe, expect, it } from 'vitest';
import { parse } from 'yaml';
import G_LECTURE from './fixtures/schedule-forms/lecture.yml?raw';
import G_LAB from './fixtures/schedule-forms/lab.yml?raw';
import G_READINGS from './fixtures/schedule-forms/readings.yml?raw';
import G_EXAM from './fixtures/schedule-forms/exam.yml?raw';
import G_EVENT from './fixtures/schedule-forms/event.yml?raw';
import G_ASSETS from './fixtures/schedule-forms/assets.yml?raw';
import G_ASSIGNMENT from './fixtures/schedule-forms/assignment.yml?raw';
import G_ASSIGNMENT_LW from './fixtures/schedule-forms/assignment-late-window.yml?raw';
import G_SEMESTER from './fixtures/schedule-forms/semester.yml?raw';
import { YamlText } from '../src/edit/yamlText';
import { cutoffOf } from '../src/screens/RunSettings';
import {
  adoptDefaultList, applyChanges, changesOf, defaultListPath, defaultReleaseAt, draftErrors, emptyTwin, fillLists, listWrites, newDraft, newlyManual, nextNumber, nextSession, readDraft, releaseAt, writeDraft,
  type AssignmentDraft, type EntryDraft, type ListDraft, type SemesterDraft, type SemesterEntryDraft,
} from '../src/model/scheduleEdit';

const doc = (t: string) => (parse(t) ?? {}) as Record<string, unknown>;
/** A reading list written in the schedule (the legacy inline form, decision 0044). */
const inlineList = (text: string): ListDraft => ({ repo: '', path: '', text, sha: null, inline: true, loaded: true });
const save = (t: string, d: EntryDraft | AssignmentDraft | SemesterEntryDraft, base: EntryDraft | AssignmentDraft | SemesterEntryDraft | null = null, removed: string[] = [], handedOut = false) =>
  applyChanges(t, doc(t), changesOf([d], [base]), removed, { handedOut: () => handedOut });

const BASE = `timezone: Europe/Berlin
lectures:
  lecture-4:
    session: 4
    title: Regularisation basics
    event_datetime: 2026-09-29T10:00
`;

describe('the forms write the mock-up’s YAML', () => {
  it('Lecture: session, title, date and time, location, details, picked files; key generated, number left to the session', () => {
    const d = newDraft('lecture', doc(BASE)) as EntryDraft;
    expect(d.session).toBe(5);
    const filled: EntryDraft = {
      ...d, title: 'Regularisation', slots: [{ date: '2026-10-06', time: '10:00', location: 'Room 1.10', details: '' }], details: 'Ridge and lasso.',
      files: [
        { repo: 'course-materials-f2026', path: 'lectures/05_regularisation', toRepo: '', toPath: '', atDate: '', atTime: '', public: false },
        { repo: 'lecture-code-f2026', path: 'mlpkg/regularise', toRepo: '', toPath: '', atDate: '', atTime: '', public: false },
      ],
      twin: { ...emptyTwin({ ...d, slots: [{ date: '2026-10-05', time: '09:00', location: '', details: '' }] }), list: inlineList('- Goodfellow et al. (2016), ch. 7.\n'), files: [{ repo: 'course-materials-f2026', path: 'readings/05', toRepo: '', toPath: '', atDate: '', atTime: '', public: false }] },
    };
    const out = save(BASE, filled);
    expect(out.keys).toEqual(['lecture-5', 'readings-5']);
    expect(out.text).toBe(`${BASE}  lecture-5:
    session: 5
    title: Regularisation
    event_datetime: 2026-10-06T10:00
    location: Room 1.10
    details: Ridge and lasso.
    files:
      - from_course_repo: course-materials-f2026
        from_path: lectures/05_regularisation
      - from_course_repo: lecture-code-f2026
        from_path: mlpkg/regularise
readings:
  readings-5:
    session: 5
    event_datetime: 2026-10-05T09:00
    reading_list: |
      - Goodfellow et al. (2016), ch. 7.
    files:
      - from_course_repo: course-materials-f2026
        from_path: readings/05
`);
  });

  it('Lab: a number of its own and two slots, each with its own location and details', () => {
    const d = { ...(newDraft('lab', doc(BASE)) as EntryDraft), session: 3 as const, number: 2 as const, title: 'Backprop by hand',
      slots: [{ date: '2026-09-16', time: '08:00', location: 'Lab 2.30', details: 'Group 1' }, { date: '2026-09-17', time: '18:00', location: 'Lab 2.32', details: 'Group 2' }] };
    const out = save(BASE, d);
    expect(out.keys).toEqual(['lab-2']);
    expect(parse(out.text).labs).toEqual({
      'lab-2': { session: 3, number: 2, title: 'Backprop by hand', event_datetime: [{ at: '2026-09-16T08:00', location: 'Lab 2.30', details: 'Group 1' }, { at: '2026-09-17T18:00', location: 'Lab 2.32', details: 'Group 2' }] },
    });
  });

  it('Lab: prefilled with the session after its own last one, and the lecture of that unit’s date and place', () => {
    const t = `${BASE}labs:\n  lab-3:\n    session: 3\n    event_datetime: 2026-09-23T14:00\n`;
    const d = newDraft('lab', doc(t)) as EntryDraft;
    expect(d.session).toBe(4);
    expect(d.slots[0]).toEqual({ date: '2026-09-29', time: '10:00', location: '', details: '' });
  });

  it('Readings on their own: title, date and time, reading guide; no session', () => {
    const d = { ...(newDraft('readings', doc(BASE)) as EntryDraft), title: 'Background reading', slots: [{ date: '2026-08-25', time: '09:00', location: '', details: '' }], list: inlineList('- Any linear algebra refresher.') };
    const out = save(BASE, d);
    expect(out.keys).toEqual(['readings-background']);
    expect(out.text.slice(BASE.length)).toBe(`readings:
  readings-background:
    title: Background reading
    event_datetime: 2026-08-25T09:00
    reading_list: "- Any linear algebra refresher."
`);
  });

  it('Readings: a file made public writes public: true on that item only', () => {
    const d = { ...(newDraft('readings', doc(BASE)) as EntryDraft), session: 5 as const, slots: [{ date: '2026-10-05', time: '', location: '', details: '' }],
      files: [{ repo: 'course-materials-f2026', path: 'readings/05/goodfellow-ch6.pdf', toRepo: '', toPath: '', atDate: '', atTime: '', public: false }, { repo: 'course-materials-f2026', path: 'readings/05/lecture-notes.pdf', toRepo: '', toPath: '', atDate: '', atTime: '', public: true }] };
    const out = parse(save(BASE, d).text).readings['readings-5'];
    expect(out.files).toEqual([{ from_course_repo: 'course-materials-f2026', from_path: 'readings/05/goodfellow-ch6.pdf' }, { from_course_repo: 'course-materials-f2026', from_path: 'readings/05/lecture-notes.pdf', public: true }]);
  });

  it('Readings: a reading list naming a course file is kept as the file has it', () => {
    const t = `${BASE}readings:\n  readings-4:\n    session: 4\n    reading_list:\n      from_course_repo: course-materials-f2026\n      from_path: readings/04/READINGS.md\n`;
    const d = readDraft(doc(t), 'readings-4') as EntryDraft;
    expect(d.list).toEqual({ repo: 'course-materials-f2026', path: 'readings/04/READINGS.md', text: '', sha: null, inline: false, loaded: false });
    const out = parse(save(t, { ...d, title: 'Week 4' }, d).text).readings['readings-4'];
    expect(out.reading_list).toEqual({ from_course_repo: 'course-materials-f2026', from_path: 'readings/04/READINGS.md' });
  });

  it('Exam: title, date and time, location, details; the key from the title', () => {
    const d = { ...(newDraft('exam', doc(BASE)) as EntryDraft), title: 'Midterm exam', slots: [{ date: '2026-10-20', time: '10:00', location: 'Room 2.32', details: '' }], details: 'Bring a calculator.' };
    const out = save(BASE, d);
    expect(out.keys).toEqual(['exam-midterm']);
    expect(out.text.slice(BASE.length)).toBe(`exams:
  exam-midterm:
    title: Midterm exam
    event_datetime: 2026-10-20T10:00
    location: Room 2.32
    details: Bring a calculator.
`);
  });

  it('Event: Date TBC writes event_datetime: tbc', () => {
    const d = { ...(newDraft('other', doc(BASE)) as EntryDraft), title: 'Clinic', tbc: true };
    expect(parse(save(BASE, d).text).events).toEqual({ 'event-clinic': { title: 'Clinic', event_datetime: 'tbc' } });
  });

  it('Supporting files: show_on_site stays unwritten (false by default for this kind), and on when switched on', () => {
    const d = { ...(newDraft('assets', doc(BASE)) as EntryDraft), title: 'Datasets', slots: [{ date: '2026-09-01', time: '08:00', location: '', details: '' }], files: [{ repo: 'course-materials-f2026', path: 'data', toRepo: '', toPath: '', atDate: '', atTime: '', public: false }] };
    expect(d.show).toBe(false);
    const out = save(BASE, d);
    expect(out.text.slice(BASE.length)).toBe(`assets:
  assets-datasets:
    title: Datasets
    event_datetime: 2026-09-01T08:00
    files:
      - from_course_repo: course-materials-f2026
        from_path: data
`);
    expect(parse(save(BASE, { ...d, show: true }).text).assets['assets-datasets'].show_on_site).toBe(true);
  });

  it('Assignment: number, template, hand-out and due; nothing else when every run setting is inherited', () => {
    const d = { ...(newDraft('assignment', doc(BASE)) as AssignmentDraft), template: 'assignment-gradient-descent', handoutDate: '2026-10-13', handoutTime: '14:00', dueDate: '2026-10-27', dueTime: '23:59' };
    expect(d.number).toBe(1);
    const out = save(BASE, { ...d, number: 3 });
    expect(out.keys).toEqual(['assignment-3']);
    expect(out.text.slice(BASE.length)).toBe(`assignments:
  assignment-3:
    number: 3
    template: assignment-gradient-descent
    handout_datetime: 2026-10-13T14:00
    due_datetime: 2026-10-27T23:59
`);
  });

  it('Assignment: a run setting changed is written inline; overrides of the template’s shape too', () => {
    const d = { ...(newDraft('assignment', doc(BASE)) as AssignmentDraft), number: 4, template: 'assignment-group-project', handoutDate: '2026-10-13', dueDate: '2026-11-15', dueTime: '23:59',
      run: { late_window_days: 5, late_penalty_per_day: '5%', visibility: 'public', team_formation: 'self_select', max_team_size: 4, submit_url: 'https://moodle.example/x' }, group: 'group' as const, submitVia: 'external', repoName: 'project' };
    expect(parse(save(BASE, d).text).assignments['assignment-4']).toEqual({
      number: 4, template: 'assignment-group-project', type: 'group', submit_via: 'external', handout_datetime: '2026-10-13T14:00', due_datetime: '2026-11-15T23:59', repo_name: 'project',
      late_window_days: 5, late_penalty_per_day: '5%', team_formation: 'self_select', max_team_size: 4, assignment_repo_visibility: 'public', submit_url: 'https://moodle.example/x',
    });
  });

  it('Assignment: solutions and marks dates, Dates TBC', () => {
    const d = { ...(newDraft('assignment', doc(BASE)) as AssignmentDraft), number: 2, template: 't', handoutDate: '2026-09-15', dueDate: '2026-10-13', dueTime: '', solutionOn: true, solutionDate: '2026-10-16', solutionTime: '09:00', marksOn: true, marksDate: '2026-10-30' };
    const a = parse(save(BASE, d).text).assignments['assignment-2'];
    expect([a.due_datetime, a.solution_datetime, a.marks_return_datetime]).toEqual(['2026-10-13', '2026-10-16T09:00', '2026-10-30']);
    const tbc = parse(save(BASE, { ...d, datesTbc: true }).text).assignments['assignment-2'];
    expect(tbc.due_datetime).toBe('tbc');
    expect(tbc.handout_datetime).toBeUndefined();
  });

  it('Semester: a break with from and to; start, end and archive by date', () => {
    const br: SemesterEntryDraft = { ...(newDraft('semester', doc(BASE)) as SemesterEntryDraft), type: 'break', title: 'Reading week', from: '2026-10-19', to: '2026-10-23' };
    const out = save(BASE, br);
    expect(out.keys).toEqual(['reading-week']);
    expect(out.text.slice(BASE.length)).toBe(`semester:
  reading-week:
    type: break
    title: Reading week
    from: 2026-10-19
    to: 2026-10-23
`);
    const ar: SemesterEntryDraft = { ...br, type: 'archive', title: '', from: '2027-02-16', to: '' };
    expect(parse(save(BASE, ar).text).semester).toEqual({ archive: { type: 'archive', event_datetime: '2027-02-16' } });
  });

  it('the dates sheet writes the start and end entries and no timezone (semester.yml’s, decision 0039)', () => {
    const t = `semester:\n  start:   {type: start, event_datetime: 2026-09-07}\n  end:     {type: end,   event_datetime: 2026-12-18}\n`;
    const d = readDraft(doc(t), 'semester') as SemesterDraft;
    expect(d).toEqual({ kind: 'semester', start: '2026-09-07', end: '2026-12-18' });
    const y = new YamlText(t);
    writeDraft(y, { ...d, end: '2026-12-19' }, doc(t));
    expect(parse(y.text).semester.end).toEqual({ type: 'end', event_datetime: '2026-12-19' });
    expect(y.text).toContain('start:   {type: start, event_datetime: 2026-09-07}');
    expect(y.text).not.toContain('timezone');
  });
});


// Each form filled as the mock-up fills it, saved into a file holding only the timezone: the
// result is the golden file, the YAML the mock-up shows beside that form.
const TZ = 'timezone: Europe/Berlin\n';
const file = (repo: string, path: string, x: Partial<EntryDraft['files'][number]> = {}) => ({ repo, path, toRepo: '', toPath: '', atDate: '', atTime: '', public: false, ...x });
const at = (date: string, time = '', location = '', details = '') => ({ date, time, location, details });

describe('golden files: each form writes the mock-up’s YAML', () => {
  it('Lecture with its Readings sub-form', () => {
    const d = newDraft('lecture', doc(TZ)) as EntryDraft;
    const filled: EntryDraft = {
      ...d, session: 5, title: 'Regularisation', slots: [at('2026-10-06', '10:00', 'Room 1.10')], details: 'Ridge, lasso and early stopping.',
      files: [file('course-materials-f2026', 'lectures/05_regularisation'), file('lecture-code-f2026', 'mlpkg/regularise')],
      twin: { ...emptyTwin(d), slots: [at('2026-10-05', '09:00')], list: inlineList('- Goodfellow, Bengio & Courville (2016), ch. 7.\n'), files: [file('course-materials-f2026', 'readings/05')] },
    };
    expect(save(TZ, filled).text).toBe(G_LECTURE);
  });

  it('Lab: its own number, two slots each with location and details', () => {
    const d = { ...(newDraft('lab', doc(TZ)) as EntryDraft), session: 3 as const, number: 2 as const, title: 'Backprop by hand',
      slots: [at('2026-09-16', '08:00', 'Lab 2.30', 'Group 1'), at('2026-09-17', '18:00', 'Lab 2.32', 'Group 2')], files: [file('course-materials-f2026', 'labs/02_backprop')] };
    expect(save(TZ, d).text).toBe(G_LAB);
  });

  it('Readings on their own', () => {
    const d = { ...(newDraft('readings', doc(TZ)) as EntryDraft), title: 'Background reading', slots: [at('2026-08-25', '09:00')], list: inlineList('- Any linear algebra refresher.') };
    expect(save(TZ, d).text).toBe(G_READINGS);
  });

  it('Exam', () => {
    const d = { ...(newDraft('exam', doc(TZ)) as EntryDraft), title: 'Midterm exam', slots: [at('2026-10-20', '10:00', 'Room 2.32')], details: 'Bring a calculator.' };
    expect(save(TZ, d).text).toBe(G_EXAM);
  });

  it('Event', () => {
    const d = { ...(newDraft('other', doc(TZ)) as EntryDraft), title: 'Clinic drop-in', slots: [at('2026-10-01', '16:00', 'Room 1.10')], details: 'Optional' };
    expect(save(TZ, d).text).toBe(G_EVENT);
  });

  it('Supporting files', () => {
    const d = { ...(newDraft('assets', doc(TZ)) as EntryDraft), title: 'Datasets', slots: [at('2026-09-01', '08:00')], files: [file('course-materials-f2026', 'data')] };
    expect(save(TZ, d).text).toBe(G_ASSETS);
  });

  it('Assignment: nothing but its number, template and dates when every run setting is inherited; a typed late window adds one line', () => {
    const d = { ...(newDraft('assignment', doc(TZ)) as AssignmentDraft), number: 3, template: 'assignment-gradient-descent', handoutDate: '2026-10-13', handoutTime: '14:00', dueDate: '2026-10-27', dueTime: '23:59' };
    expect(save(TZ, d).text).toBe(G_ASSIGNMENT);
    expect(save(TZ, { ...d, run: { late_window_days: 5 } }).text).toBe(G_ASSIGNMENT_LW);
  });

  it('Semester: a break, then the archive, beside the start and end', () => {
    const start = `${TZ}semester:\n  start:   {type: start, event_datetime: 2026-09-07}\n  end:     {type: end,   event_datetime: 2026-12-18}\n`;
    const br = { ...(newDraft('semester', doc(start)) as SemesterEntryDraft), type: 'break' as const, title: 'Reading week', from: '2026-10-19', to: '2026-10-23' };
    const withBreak = save(start, br).text;
    const ar = { ...(newDraft('semester', doc(withBreak)) as SemesterEntryDraft), type: 'archive' as const, from: '2027-02-16' };
    // show_on_site: true is the default, written only where the file already spells it (the mock-up's archive line spells it).
    expect(save(withBreak, ar).text).toBe(G_SEMESTER);
  });
});

const NUMBERED = `lectures:
  lecture-3:
    session: 3
    title: Backpropagation
    event_datetime: 2026-09-15T10:00
  lecture-4:
    session: 4
    title: Regularisation
    event_datetime: 2026-09-22T10:00
readings:
  readings-3:
    session: 3
    event_datetime: 2026-09-14
`;

describe('keys follow numbers', () => {
  it('renames lecture-3 to lecture-6 and re-sorts the block, its readings twin with it', () => {
    const base = readDraft(doc(NUMBERED), 'lecture-3') as EntryDraft;
    expect(base.twin?.id).toBe('readings-3');
    const out = save(NUMBERED, { ...base, session: 6 }, base);
    const got = parse(out.text);
    expect(Object.keys(got.lectures)).toEqual(['lecture-4', 'lecture-6']);
    expect(got.lectures['lecture-6'].session).toBe(6);
    expect(got.readings).toEqual({ 'readings-6': { session: 6, event_datetime: '2026-09-14' } });
  });

  it('swaps two lectures in one save without ever writing a key twice', () => {
    const b3 = readDraft(doc(NUMBERED), 'lecture-3') as EntryDraft, b4 = readDraft(doc(NUMBERED), 'lecture-4') as EntryDraft;
    const out = applyChanges(NUMBERED, doc(NUMBERED), changesOf([{ ...b3, session: 4, twin: null }, { ...b4, session: 3 }], [{ ...b3, twin: null }, b4]), []);
    const got = parse(out.text).lectures;
    expect(Object.keys(got)).toEqual(['lecture-3', 'lecture-4']);
    expect([got['lecture-3'].title, got['lecture-4'].title]).toEqual(['Regularisation', 'Backpropagation']);
  });

  it('keeps a key written by hand when the number changes', () => {
    const t = 'lectures:\n  s5:\n    session: 5\n    event_datetime: 2026-10-06T10:00\n';
    const base = readDraft(doc(t), 's5') as EntryDraft;
    expect(save(t, { ...base, session: 6 }, base).keys).toEqual(['s5']);
  });

  it('renames an assignment key only before the hand-out', () => {
    const t = 'assignments:\n  assignment-3:\n    number: 3\n    template: t\n    due_datetime: 2026-10-27\n';
    const base = readDraft(doc(t), 'assignment-3') as AssignmentDraft;
    expect(save(t, { ...base, number: 5 }, base).keys).toEqual(['assignment-5']);
    expect(save(t, { ...base, number: 5 }, base, [], true).keys).toEqual(['assignment-3']);
  });

  it('proposes the next session and number from the file', () => {
    expect(nextSession(doc(NUMBERED), 'lecture')).toBe(5);
    expect(nextSession(doc(NUMBERED), 'lab')).toBe(1);
    expect(nextNumber(doc('assignments:\n  a: {number: 2, template: t, due_datetime: x}\n'), 'assignment')).toBe(3);
  });

  it('edits an entry in place, keeping keys the form does not show and the files it did not touch byte for byte', () => {
    const t = `lectures:\n  lecture-3:   # the first one\n    session: 3\n    owner: henry\n    event_datetime: 2026-09-15T10:00\n    files:\n      - {from_course_repo: m, from_path: a}\n      - from_course_repo: m\n        from_path: b\n        release_datetime: 2026-09-15 09:00\n`;
    const base = readDraft(doc(t), 'lecture-3') as EntryDraft;
    const out = save(t, { ...base, title: 'Backprop', files: [base.files[1]] }, base);
    expect(out.text).toContain('owner: henry');
    expect(out.text).toContain('lecture-3:   # the first one');
    expect(out.text).toContain('release_datetime: 2026-09-15 09:00');
    expect(parse(out.text).lectures['lecture-3'].files).toHaveLength(1);
  });
});

describe('validation as you type', () => {
  it('asks about a hand out by hand only when the entry newly has no hand out date', () => {
    const d = newDraft('assignment', {}) as AssignmentDraft;
    const manual = { ...d, manual: true };
    expect(newlyManual(manual, null)).toBe(true);
    expect(newlyManual(manual, d)).toBe(true);
    expect(newlyManual(manual, manual)).toBe(false);
    expect(newlyManual(d, null)).toBe(false);
    expect(newlyManual(newDraft('lecture', {}), null)).toBe(false);
    // The engine reports it out: its copies exist, nothing to ask.
    expect(newlyManual(manual, d, 'declared')).toBe(true);
    expect(newlyManual(manual, d, 'open')).toBe(false);
  });

  it('flags a duplicate session or number live, and lets a removed one go', () => {
    const d = { ...(newDraft('lecture', doc(NUMBERED)) as EntryDraft), session: 4 as const, slots: [{ date: '2026-10-06', time: '', location: '', details: '' }] };
    expect(draftErrors(d, { doc: doc(NUMBERED) }).number).toBe('Lecture 4 is already in the schedule (lecture-4).');
    expect(draftErrors(d, { doc: doc(NUMBERED), removed: ['lecture-4'] }).number).toBeUndefined();
  });

  it('needs a session on a lecture, a date unless Date TBC, a title on an event', () => {
    const d = { ...(newDraft('lecture', {}) as EntryDraft), session: '' as const };
    expect(draftErrors(d)).toMatchObject({ session: 'Give the session.', date: 'Give the date, or turn on Date TBC.' });
    expect(draftErrors({ ...d, session: 1, tbc: true })).toEqual({});
    expect(draftErrors({ ...(newDraft('other', {}) as EntryDraft), tbc: true }).title).toBe('A title is needed; the student site shows only this.');
  });

  it('refuses a due date before the hand-out', () => {
    const d = { ...(newDraft('assignment', {}) as AssignmentDraft), template: 't', handoutDate: '2026-10-13', handoutTime: '14:00', dueDate: '2026-10-12', dueTime: '23:59' };
    expect(draftErrors(d).due).toBe('Due must come after the hand-out.');
  });

  it('a break needs both days in order; one start, one end, one archive', () => {
    const t = 'semester:\n  start: {type: start, event_datetime: 2026-09-07}\n';
    const br = { ...(newDraft('semester', {}) as SemesterEntryDraft), from: '2026-10-23', to: '2026-10-19' };
    expect(draftErrors(br).to).toBe('The break must end on or after its first day.');
    expect(draftErrors({ ...br, type: 'start' as const }, { doc: doc(t) }).type).toBe('The semester already has a start entry (start).');
  });

  it('checks the readings twin with the lecture', () => {
    const d = { ...(newDraft('lecture', {}) as EntryDraft), slots: [{ date: '2026-10-06', time: '10:00', location: '', details: '' }] };
    const twin = { ...emptyTwin(d), slots: [{ date: '', time: '', location: '', details: '' }], list: inlineList('x') };
    expect(draftErrors({ ...d, twin })['twin.date']).toBe('Give the date, or turn on Date TBC.');
  });
});

describe('kept from the 0.10 editor (baby rule)', () => {
  it('defaults a file’s release time to an hour before a timed entry, the day’s start for a whole-day one', () => {
    expect(defaultReleaseAt('2026-10-08', '10:00')).toEqual(['2026-10-08', '09:00']);
    expect(defaultReleaseAt('2026-10-08', '00:30')).toEqual(['2026-10-07', '23:30']);
    expect(defaultReleaseAt('2026-10-08', '')).toEqual(['2026-10-08', '']);
    const first = { date: '2026-10-08', time: '10:00' };
    expect(releaseAt(first, '2026-10-08', '09:00')).toEqual({ atDate: '', atTime: '' });
    expect(releaseAt(first, '2026-10-08', '08:00')).toEqual({ atDate: '2026-10-08', atTime: '08:00' });
  });

  it('writes a file’s own release time only when given', () => {
    const t = 'lectures:\n  lecture-5:\n    session: 5\n    event_datetime: 2026-10-08T10:00\n    files:\n      - from_course_repo: m\n        from_path: l\n        release_datetime: 2026-10-07T09:00\n';
    const base = readDraft(doc(t), 'lecture-5') as EntryDraft;
    const d = { ...base, files: [{ ...base.files[0], atDate: '', atTime: '' }, { repo: 'm', path: 'l2', toRepo: '', toPath: '', atDate: '2026-10-10', atTime: '', public: false }] };
    expect(parse(save(t, d, base).text).lectures['lecture-5'].files).toEqual([{ from_course_repo: 'm', from_path: 'l' }, { from_course_repo: 'm', from_path: 'l2', release_datetime: '2026-10-10' }]);
    expect(draftErrors({ ...d, files: [{ ...d.files[0], atTime: '09:00' }] }).file0).toBe('l: give the release date too, or clear the time.');
  });

  it('keeps a moment as the file spells it when only the title changes, and never writes an assignment’s title', () => {
    const t = 'lectures:\n  s5:\n    session: 5\n    event_datetime: "2026-10-08T10:00:00+01:00"\n    title: Trees\nassignments:\n  a2:\n    number: 2\n    template: assignment-2-f2026\n    handout_datetime: 2026-09-15 10:00\n    due_datetime: 2026-09-27T23:59:00+02:00\n';
    const y = new YamlText(t);
    writeDraft(y, { ...(readDraft(doc(t), 's5') as EntryDraft), title: 'Trees and ensembles' }, doc(t));
    writeDraft(y, { ...(readDraft(doc(t), 'a2') as AssignmentDraft), title: 'Regression' }, doc(t));
    expect(y.text).toBe(t.replace('title: Trees\n', 'title: Trees and ensembles\n'));
  });

  it('refuses a solution shown before the late cutoff, in the engine’s words', () => {
    const a = { ...(newDraft('assignment', {}) as AssignmentDraft), id: 'assignment-1', number: 1, template: 't', handoutDate: '2026-09-15', dueDate: '2026-10-13', dueTime: '', solutionOn: true, solutionDate: '2026-10-16', solutionTime: '09:00' };
    expect(draftErrors(a, {}, cutoffOf('2026-10-13', '', 5)).solution).toBe(
      'The solution for assignment-1 is set to be shown on 2026-10-16 09:00, before its late cutoff on 2026-10-18 23:59. Students can still hand in until the late cutoff, so the solution must be shown on or after it.',
    );
    expect(draftErrors({ ...a, solutionDate: '2026-10-18', solutionTime: '23:59' }, {}, cutoffOf('2026-10-13', '23:59', 5))).toEqual({});
  });

  it('leaves keys the form does not show exactly as they are', () => {
    const t = 'assignments:\n  a2:\n    number: 2\n    template: assignment-2-f2026\n    title: Regression\n    due_datetime: 2026-09-27T23:59:00+02:00\n    grading_datetime: 2026-10-07\n';
    const y = new YamlText(t);
    writeDraft(y, { ...(readDraft(doc(t), 'a2') as AssignmentDraft), details: 'Fit it.' }, doc(t));
    expect(parse(y.text).assignments.a2).toMatchObject({ title: 'Regression', grading_datetime: '2026-10-07', details: 'Fit it.' });
  });

  it('edits the archive’s days after the semester ends, writing them only once they differ from the default, and only without a date', () => {
    const t = 'semester:\n  archive:\n    type: archive\n    grace_days: 30\n';
    const base = readDraft(doc(t), 'archive') as SemesterEntryDraft;
    expect(base.graceDays).toBe(30);
    expect(parse(save(t, { ...base, title: 'Frozen' }, base).text).semester.archive).toEqual({ type: 'archive', grace_days: 30, title: 'Frozen' });
    expect(parse(save(t, { ...base, from: '2027-02-16' }, base).text).semester.archive).toEqual({ type: 'archive', event_datetime: '2027-02-16' });
    const fresh = { ...(newDraft('semester', {}) as SemesterEntryDraft), type: 'archive' as const };
    expect(parse(save('', fresh).text).semester).toEqual({ archive: { type: 'archive' } });
    expect(parse(save('', { ...fresh, graceDays: 90 }).text).semester).toEqual({ archive: { type: 'archive', grace_days: 90 } });
    expect(draftErrors({ ...fresh, graceDays: -1 }).graceDays).toBe('A whole number of days, 0 or more.');
  });
});

describe('the reading list as a course file (decision 0044)', () => {
  const REPO = 'course-materials-f2026';
  const t = `${BASE}readings:\n  readings-4:\n    session: 4\n    reading_list:\n      from_course_repo: ${REPO}\n      from_path: readings/04/READINGS.md\n      to_path: week4/READINGS.md\n`;
  const ready = (text: string) => () => ({ kind: 'ready' as const, text, sha: 'blob1' });

  it('reads a file ref unread, then fills it from the file; a missing file is a New file at that path', () => {
    const d = readDraft(doc(t), 'readings-4') as EntryDraft;
    expect(d.list).toMatchObject({ repo: REPO, path: 'readings/04/READINGS.md', inline: false, loaded: false });
    expect(fillLists(d, ready('- A.\n')).list).toEqual({ repo: REPO, path: 'readings/04/READINGS.md', text: '- A.\n', sha: 'blob1', inline: false, loaded: true });
    expect(fillLists(d, () => ({ kind: 'absent' })).list).toMatchObject({ text: '', sha: null, loaded: true });
    expect(fillLists(d, () => ({ kind: 'loading' }))).toBe(d);
  });

  it('writes a file as {from_course_repo, from_path} over the keys the file had, never into files:', () => {
    const d = fillLists(readDraft(doc(t), 'readings-4') as EntryDraft, ready('- A.\n'));
    const moved = { ...d, list: { ...d.list!, path: 'lectures/04/READINGS.md' } };
    const out = parse(save(t, moved, d).text).readings['readings-4'];
    expect(out.reading_list).toEqual({ from_course_repo: REPO, from_path: 'lectures/04/READINGS.md', to_path: 'week4/READINGS.md' });
    expect(out.files).toBeUndefined();
  });

  it('writes nothing for a new file with nothing typed; Move to a file turns inline text into a file ref', () => {
    const d = { ...(newDraft('readings', doc(BASE)) as EntryDraft), session: 6 as const, slots: [{ date: '2026-10-20', time: '', location: '', details: '' }] };
    const empty = { ...d, list: { repo: REPO, path: 'readings/06/READINGS.md', text: '', sha: null, inline: false, loaded: true } };
    expect(parse(save(BASE, empty).text).readings['readings-6'].reading_list).toBeUndefined();
    const inline = `${BASE}readings:\n  readings-5:\n    session: 5\n    reading_list: "- Old."\n`;
    const legacy = readDraft(doc(inline), 'readings-5') as EntryDraft;
    expect(legacy.list).toEqual({ repo: '', path: '', text: '- Old.', sha: null, inline: true, loaded: true });
    const moved = { ...legacy, list: { ...legacy.list!, inline: false, repo: REPO, path: 'readings/05/READINGS.md' } };
    expect(parse(save(inline, moved, legacy).text).readings['readings-5'].reading_list).toEqual({ from_course_repo: REPO, from_path: 'readings/05/READINGS.md' });
  });

  it('reads an unmigrated reading_guide: as legacy text, and writes it back as reading_list: alone', () => {
    const old = `${BASE}readings:\n  readings-5:\n    session: 5\n    reading_guide: "- Old."\n`;
    const d = readDraft(doc(old), 'readings-5') as EntryDraft;
    expect(d.list).toEqual({ repo: '', path: '', text: '- Old.', sha: null, inline: true, loaded: true });
    const kept = parse(save(old, { ...d, title: 'Week 5' }, d).text).readings['readings-5'];
    expect(kept).toEqual({ session: 5, title: 'Week 5', reading_list: '- Old.' });
    const moved = { ...d, list: { ...d.list!, inline: false, repo: REPO, path: 'readings/05/READINGS.md' } };
    const out = parse(save(old, moved, d).text).readings['readings-5'];
    expect(out.reading_list).toEqual({ from_course_repo: REPO, from_path: 'readings/05/READINGS.md' });
    expect('reading_guide' in out).toBe(false);
    // An entry already on reading_list: keeps it; reading_guide: is never written.
    const t2 = `${BASE}readings:\n  readings-6:\n    session: 6\n    reading_list: "- New."\n`;
    const d2 = readDraft(doc(t2), 'readings-6') as EntryDraft;
    expect(parse(save(t2, { ...d2, title: 'Week 6' }, d2).text).readings['readings-6']).toEqual({ session: 6, title: 'Week 6', reading_list: '- New.' });
  });

  it('adopts an existing default file for an entry with no list, its twin included; never over a list it has', () => {
    const file = (path: string) => ({ repo: REPO, path, toRepo: '', toPath: '', atDate: '', atTime: '', public: false });
    const r = { ...(newDraft('readings', doc(BASE)) as EntryDraft), files: [file('readings/06/a.pdf')] };
    const there = (_: string, path: string) => path === 'readings/06/READINGS.md' || path === 'readings/03_trees/READINGS.md';
    const repoOf = () => REPO;
    expect(adoptDefaultList(r, repoOf, there).list).toEqual({ repo: REPO, path: 'readings/06/READINGS.md', text: '', sha: null, inline: false, loaded: false });
    expect(adoptDefaultList(r, repoOf, () => false)).toBe(r);
    const own = { ...r, list: inlineList('- Mine.') };
    expect(adoptDefaultList(own, repoOf, there)).toBe(own);
    const unit = { ...(newDraft('lecture', doc(BASE)) as EntryDraft), session: 3 as const, title: 'Trees' };
    const withTwin = { ...unit, twin: { ...emptyTwin(unit), session: 3 as const } };
    expect(adoptDefaultList(withTwin, repoOf, there).twin!.list).toMatchObject({ path: 'readings/03_trees/READINGS.md', loaded: false });
  });

  it('defaults the path beside the first reading file, else readings/<NN>_<slug>/READINGS.md', () => {
    const d = newDraft('readings', doc(BASE)) as EntryDraft;
    const file = (path: string) => ({ repo: REPO, path, toRepo: '', toPath: '', atDate: '', atTime: '', public: false });
    expect(defaultListPath({ ...d, files: [file('readings/04/a.pdf')] })).toBe('readings/04/READINGS.md');
    expect(defaultListPath({ ...d, files: [file('readings/04_week/')] })).toBe('readings/04_week/READINGS.md');
    expect(defaultListPath({ ...d, session: 3, title: 'Neural nets' })).toBe('readings/03_neural-nets/READINGS.md');
    expect(defaultListPath({ ...d, session: 3 }, 'Trees')).toBe('readings/03_trees/READINGS.md');
  });

  it('lists the files a Save writes: a changed file, a new file with text; not an unread or unchanged one', () => {
    const base = fillLists(readDraft(doc(t), 'readings-4') as EntryDraft, ready('- A.\n'));
    const read = ready('- A.\n');
    expect(listWrites([base], read)).toEqual([]);
    expect(listWrites([{ ...base, list: { ...base.list!, text: '- B.\n' } }], read)).toEqual([{ repo: REPO, path: 'readings/04/READINGS.md', text: '- B.\n', sha: 'blob1' }]);
    expect(listWrites([readDraft(doc(t), 'readings-4') as EntryDraft], read)).toEqual([]);
    const fresh = { ...base, list: { repo: REPO, path: 'readings/07/READINGS.md', text: '- C.', sha: null, inline: false, loaded: true } };
    expect(listWrites([fresh], () => ({ kind: 'absent' }))).toEqual([{ repo: REPO, path: 'readings/07/READINGS.md', text: '- C.', sha: null }]);
    expect(listWrites([{ ...fresh, list: { ...fresh.list, text: '  ' } }], () => ({ kind: 'absent' }))).toEqual([]);
  });
});
