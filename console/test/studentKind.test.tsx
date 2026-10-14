// A kind tab (decision 0035 rule 5): the rows its tab names, dated then undated, the kind's
// own event last; Readings shows only reading files and the pending note; a file opens in the tab. The archive notice
// is Schedule's alone.

import { readFileSync } from 'node:fs';
import { render } from 'preact-render-to-string';
import { describe, expect, it } from 'vitest';
import { factsFromStatus } from '../src/model/student';
import { HomeView } from '../src/screens/StudentHome';
import { KindView, kindRows } from '../src/screens/StudentKind';
import { ScheduleView } from '../src/screens/StudentSchedule';

const DOC = JSON.parse(readFileSync(new URL('./fixtures/student-status.json', import.meta.url), 'utf8'));
const ORG = DOC.semester as string;
const NOW = Date.parse('2026-10-08T12:00:00Z');
const text = (v: preact.VNode) => render(v).replace(/<[^>]+>/g, ' ').replace(/&amp;/g, '&').replace(/\s+/g, ' ').trim();

function facts() {
  const d = structuredClone(DOC);
  const base = d.rows.find((r: { id: string }) => r.id === 'lecture_02');
  d.rows.push(
    { ...base, id: 'lecture_00', title: 'Lecture 0', when: '2026-09-01T10:00:00+02:00', sort_at: '2026-09-01T10:00:00+02:00', released: true, tabs: ['lecture'] },
    { ...base, id: 'extra_b', title: 'Extra B', when: null, sort_at: null, released: true, tabs: ['lecture'] },
    { ...base, id: 'extra_a', title: 'Extra A', when: null, sort_at: null, released: true, tabs: ['lecture'] },
    { ...base, id: 'hidden', title: 'Off plan', when: '2026-09-20T10:00:00+02:00', sort_at: '2026-09-20T10:00:00+02:00', off_schedule: true, released: true, tabs: ['lecture'] },
  );
  const l1 = d.rows.find((r: { id: string }) => r.id === 'lecture_01');
  l1.readings_pending = true;
  return factsFromStatus(d);
}

describe('a kind tab', () => {
  it('lists the dated rows by date, then the undated in the engine’s order, off-schedule ones too', () => {
    expect(kindRows(facts(), 'lecture').rows.map((r) => r.id)).toEqual(['lecture_00', 'lecture_01', 'lecture_01-2', 'hidden', 'lecture_02', 'lecture_03', 'extra_b', 'extra_a']);
  });

  it('lists a second slot of an entry as a row of its kind, with the entry’s files', () => {
    // `lecture_01` given twice in the engine's render (decision 0038 rule 3): its second
    // slot is the row `lecture_01-2`, "Lecture 1" with the entry's title and files.
    const f = facts();
    const row = f.rows.find((r) => r.id === 'lecture_01-2')!;
    expect(row.noMaterials).toBe(false);
    const tab = text(<KindView facts={f} kind="lecture" org={ORG} now={NOW} />);
    expect(tab).toMatch(/Lecture 1 : Intro The same lecture, online\./);
    const s = text(<ScheduleView facts={f} mine={null} now={NOW} org={ORG} />);
    expect(s).toMatch(/Lecture Wed 9 Sep 18:00 Lecture 1 : Intro The same lecture, online\./);
  });

  it('marks a row with no date yet TBC, after its kind’s last dated row, on its tab and the Schedule', () => {
    // lecture_03 is `event_datetime: tbc` in the engine's render: placed after lecture 2.
    const t = text(<KindView facts={facts()} kind="lecture" org={ORG} now={NOW} />);
    expect(t).toMatch(/Lecture 3 : Forests not released yet TBC/);
    const s = text(<ScheduleView facts={facts()} mine={null} now={NOW} org={ORG} />);
    expect(s).toMatch(/No date yet Lecture TBC Lecture 3 : Forests/);
    expect(text(<HomeView facts={{ ...facts(), rows: facts().rows.map((r) => (r.id === 'lecture_03' ? { ...r, released: true } : r)) }} now={NOW} org={ORG} />)).not.toContain('Forests');
  });

  it('lists an exam with no date yet as TBC, never a placeholder date', () => {
    const d = structuredClone(DOC);
    const mid = d.rows.find((r: { id: string }) => r.id === 'midterm');
    d.rows.push({ ...mid, id: 'resit', title: 'Resit', when: null, sort_at: '2026-12-18T23:00:00+01:00', dateless: true, tbc: true });
    const f = factsFromStatus(d);
    expect(kindRows(f, 'exam').events.map((r) => r.id)).toEqual(['midterm', 'resit']);
    expect(text(<KindView facts={f} kind="exam" org={ORG} now={NOW} />)).toMatch(/Resit TBC$/);
    expect(text(<ScheduleView facts={f} mine={null} now={NOW} org={ORG} />)).toMatch(/No date yet .* Exam TBC Resit Archive/);
  });

  it('puts the kind’s own event last, and leaves other kinds’ rows off', () => {
    const { rows, events } = kindRows(facts(), 'exam');
    expect(rows).toEqual([]);
    expect(events.map((r) => r.id)).toEqual(['midterm']);
    expect(text(<KindView facts={facts()} kind="exam" org={ORG} now={NOW} />)).toMatch(/^Midterm Wed 28 Oct/);
  });

  it('shows heading, details and files, and a row not released yet as such', () => {
    const t = text(<KindView facts={facts()} kind="lecture" org={ORG} now={NOW} />);
    expect(t).toContain('Lecture 1');
    expect(t).toContain('Files slides.pdf');
    expect(t).toMatch(/Lecture 2[^]*not released yet/);
    expect(t).not.toContain('Readings for this session are not yet released.');
  });

  it('on Readings lists a lecture under its own name with only its reading files, and the pending note', () => {
    const f = facts();
    expect(kindRows(f, 'readings').rows.map((r) => r.id)).toEqual(['lecture_01']);
    const t = text(<KindView facts={f} kind="readings" org={ORG} now={NOW} />);
    const lec = f.rows.find((r) => r.id === 'lecture_01')!;
    expect(t).toContain(`Files ${lec.readings[0].name}`);
    expect(t).not.toContain('slides.pdf');
    expect(t).toContain('Readings for this session are not yet released.');
  });

  it('opens its files in the tab itself, not in All materials', () => {
    const f = facts();
    const html = render(<KindView facts={f} kind="lecture" org={ORG} now={NOW} />);
    const hrefs = [...html.matchAll(/href="([^"]*)"/g)].map((m) => m[1].replace(/&amp;/g, '&')).filter((h) => h.startsWith('?semester='));
    expect(hrefs.length).toBeGreaterThan(0);
    for (const h of hrefs) expect(h.startsWith(`?semester=${ORG}#kind-lecture/`)).toBe(true);
  });

  it('says so when nothing is there', () => {
    expect(text(<KindView facts={facts()} kind="lab" org={ORG} now={NOW} />)).toBe('Nothing released yet.');
  });
});

describe('the archive notice (decision 0035 rule 12)', () => {
  it('sits at the bottom of Schedule only', () => {
    const f = factsFromStatus(DOC);
    const sched = text(<ScheduleView facts={f} mine={null} now={NOW} org={ORG} />);
    expect(sched).toMatch(/Archive\. This semester is archived on [^.]+: every repository in it becomes read-only\.[^]*$/);
    expect(sched.indexOf('Archive.')).toBeGreaterThan(sched.indexOf('Semester ends'));
    expect(text(<ScheduleView facts={{ ...f, rows: [] }} mine={null} now={NOW} org={ORG} />)).toContain('Archive.');
    expect(text(<HomeView facts={f} org={ORG} now={NOW} />)).not.toContain('Archive.');
    expect(text(<KindView facts={f} kind="lecture" org={ORG} now={NOW} />)).not.toContain('Archive.');
  });
});

describe('the student schedule says what the site says', () => {
  it('words each row kind as the site, and says on a due row where to submit', () => {
    const f = facts();
    const s = text(<ScheduleView facts={f} mine={null} now={NOW} org={ORG} />);
    for (const w of ['Semester', 'Assignment out', 'Assignment due', 'Exam', 'Lecture']) expect(s).toContain(w);
    expect(s).not.toMatch(/\bhand out\b/);
    expect(s).toContain('Submit via assignment-1-&lt;your-handle>');
    expect(s).toContain('Submit via assignment-2-project-&lt;your-team>');
    const external = { ...f, assignments: f.assignments.map((a) => (a.slug === 'assignment-3' ? { ...a, shape: 'external', submitVia: 'external' as const } : a)) };
    expect(text(<ScheduleView facts={external} mine={null} now={NOW} org={ORG} />)).toContain('Handed in outside GitHub - see the brief');
  });
  it('words a kind the site declares by the site’s own label', () => {
    const f = facts();
    const base = f.rows.find((r) => r.id === 'lecture_02')!;
    const own = { ...f, rows: [...f.rows, { ...base, id: 'clinic_1', kind: 'clinic', title: 'Clinic 1' }], kinds: { ...f.kinds, clinic: { label: 'Coding clinic', plural: 'clinics', colour: '', background: '', tab: false } } };
    expect(text(<ScheduleView facts={own} mine={null} now={NOW} org={ORG} />)).toMatch(/Coding clinic .*Clinic 1/);
  });
});
