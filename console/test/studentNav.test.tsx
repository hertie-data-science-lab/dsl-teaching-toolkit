// The student nav as the site's tabs (decision 0035 rule 3): Home first, one tab per kind that
// has rows, each with the site's glyph; the old Marks, Join and Set up hashes land elsewhere;
// the status fields the tabs are read from.

import { readFileSync } from 'node:fs';
import { render } from 'preact-render-to-string';
import { describe, expect, it } from 'vitest';
import type { Estate, Semester } from '../src/model/discovery';
import { factsFromStatus } from '../src/model/student';
import { kindTabs, movedHash, parseHash, studentHref, studentLanding, studentScreens } from '../src/router';
import { materialHref } from '../src/screens/StudentFiles';
import { StudentNav } from '../src/ui/shell';

const DOC = JSON.parse(readFileSync(new URL('./fixtures/student-status.json', import.meta.url), 'utf8'));

/** The fixture as an engine before decision 0035 wrote it: no tab fields, no description, no offerings. */
function older() {
  const d = structuredClone(DOC);
  for (const k of Object.values(d.kinds) as { tab?: boolean }[]) delete k.tab;
  for (const r of d.rows) delete r.tabs;
  delete d.course_description;
  delete d.previous_offerings;
  return d;
}
const ORG = DOC.semester as string;
const sem: Semester = { org: ORG, term: 'f2026', termLabel: 'Fall 2026', courseOrg: 'c', courseName: 'Deep Learning', archived: false, role: 'student' };

/** The fixture with a lab, a lecture whose readings shipped, an undated drop-in folder and an off-schedule lab. */
function doc() {
  const d = structuredClone(DOC);
  const row = (id: string, kind: string, when: string | null, extra: object = {}) => ({ ...d.rows[1], id, kind, when, sort_at: when, links: [], readings: [], off_schedule: false, ...extra });
  d.kinds['drop-in'].tab = true;
  d.kinds.exam.tab = false;
  d.rows.find((r: { id: string }) => r.id === 'lecture_01').tabs = ['lecture', 'readings'];
  d.rows.push(row('lab_01', 'lab', '2026-09-09T10:00:00+02:00', { tabs: ['lab'] }), row('extra', 'drop-in', null, { tabs: ['drop-in'] }), row('lab_x', 'lab', '2026-09-10T10:00:00+02:00', { off_schedule: true, tabs: ['lab'] }));
  return d;
}

describe('the student status’s tab fields', () => {
  it('reads tabs, the kind’s tab flag, the course description and the previous offerings', () => {
    const d = doc();
    d.course_description = 'Neural networks.';
    d.previous_offerings = [{ title: 'Fall 2025', url: 'https://example.org/f2025' }, { title: '', url: 'x' }];
    const f = factsFromStatus(d);
    expect(f.courseDescription).toBe('Neural networks.');
    expect(f.previousOfferings).toEqual([{ title: 'Fall 2025', url: 'https://example.org/f2025' }]);
    expect(f.kinds?.['drop-in'].tab).toBe(true);
    expect(f.kinds?.exam.tab).toBe(false);
    expect(f.rows.find((r) => r.id === 'lecture_01')!.tabs).toEqual(['lecture', 'readings']);
  });

  it('defaults an older file: a row on its own kind’s tab, and no kind has a tab the file does not give it', () => {
    const f = factsFromStatus(older());
    expect(f.rows.find((r) => r.id === 'lecture_02')!.tabs).toEqual(['lecture']);
    expect(Object.entries(f.kinds!).filter(([, v]) => v.tab).map(([k]) => k)).toEqual([]);
    expect(f.courseDescription ?? '').toBe('');
    expect(f.previousOfferings ?? []).toEqual([]);
  });

  it('keeps undated and off-schedule rows for the kind tabs only, never on the schedule', () => {
    const f = factsFromStatus(doc());
    expect(f.rows.map((r) => r.id)).not.toContain('extra');
    expect(f.rows.map((r) => r.id)).not.toContain('lab_x');
    expect(f.tabRows!.map((r) => r.id)).toEqual(expect.arrayContaining(['extra', 'lab_x', 'lab_01', 'lecture_01']));
    expect(f.tabRows!.find((r) => r.id === 'extra')!.when).toBeNull();
  });
});

describe('the student screens', () => {
  it('are the site’s tabs: the kinds with a tab and a row, in policy order, after Schedule', () => {
    const f = factsFromStatus(doc());
    expect(kindTabs(f)).toEqual([['kind-lecture', 'Lectures'], ['kind-lab', 'Labs'], ['kind-readings', 'Readings'], ['kind-drop-in', 'Drop-ins']]);
    expect(studentScreens(f).map(([k]) => k)).toEqual(['home', 'week', 'schedule', 'kind-lecture', 'kind-lab', 'kind-readings', 'kind-drop-in', 'assignments', 'materials', 'instructors']);
    // A kind no row names has no tab (no lab, drop-in or supporting files here); the exam's own row gives Exams one.
    expect(kindTabs(factsFromStatus(DOC))).toEqual([['kind-lecture', 'Lectures'], ['kind-readings', 'Readings'], ['kind-exam', 'Exams']]);
    // A file that does not say which kinds have a tab has none.
    expect(kindTabs(factsFromStatus(older()))).toEqual([]);
    // The fixed tabs alone while the facts are read.
    expect(studentScreens(null).map(([, t]) => t)).toEqual(['Home', 'This week', 'Schedule', 'Assignments', 'All materials', 'Instructors']);
    // A tab is named by its kind's plural, capitalised.
    const renamed = doc();
    renamed.kinds.lab.plural = 'practicals';
    expect(kindTabs(factsFromStatus(renamed))[1]).toEqual(['kind-lab', 'Practicals']);
  });

  it('route a kind tab and Home', () => {
    expect(parseHash('#kind-drop-in')).toEqual({ screen: 'kind-drop-in' });
    expect(movedHash('#kind-lab')).toBeNull();
    expect(movedHash('#home')).toBeNull();
    expect(studentHref(ORG)).toBe(`?semester=${ORG}#home`);
  });

  it('route a file open in a kind tab, after a slash, and keep it as written', () => {
    const href = materialHref(ORG, 'dl-materials', 'labs/01 intro/lab.md', 'kind-drop-in');
    expect(href).toBe(`?semester=${ORG}#kind-drop-in/${encodeURIComponent('dl-materials/labs/01 intro/lab.md')}`);
    const hash = href.slice(href.indexOf('#'));
    expect(parseHash(hash)).toEqual({ screen: 'kind-drop-in', entry: 'dl-materials/labs/01 intro/lab.md' });
    expect(movedHash(hash)).toBeNull();
    expect(movedHash('#kind-drop-in/dl-materials/lab.md')).toBeNull();
    // All materials keeps its dash.
    expect(materialHref(ORG, 'dl-materials', 'lab.md')).toBe(`?semester=${ORG}#materials-${encodeURIComponent('dl-materials/lab.md')}`);
  });

  it('land a person who teaches nothing on their one live semester (its Home)', () => {
    const estate: Estate = { courses: [], semesters: [sem], roles: new Map([[ORG, 'student' as const]]), kind: 'classic' };
    expect(studentLanding(estate, Date.parse('2026-10-08T12:00:00Z'))).toBe(ORG);
  });
});

describe('the student nav', () => {
  const tabs = (html: string) => [...html.matchAll(/<li><a href="\?semester=[\w-]+#([\w-]+)"[^>]*><span class="with-icon"><svg class="fa-icon" viewBox="0 0 (\d+) 512"[^>]*><path d="([^"]+)"/g)].map((m) => [m[1], m[2], m[3]]);

  it('lists the kind tabs of the open semester, each with the site’s glyph (decision 0035 rule 3)', () => {
    const nav = render(<StudentNav root="Your semesters" semesters={[sem]} semester={sem} facts={factsFromStatus(doc())} current="kind-lab" />);
    const t = tabs(nav);
    expect(t.map(([k]) => k)).toEqual(['home', 'week', 'schedule', 'kind-lecture', 'kind-lab', 'kind-readings', 'kind-drop-in', 'assignments', 'materials', 'instructors']);
    // home 576 wide, calendar-week and calendar-alt 448, book-reader 512, flask 448, book 448, folder 512 (a kind of its own), user-graduate 448, folder-open 576, chalkboard-teacher 640.
    expect(t.map(([, w]) => w)).toEqual(['576', '448', '448', '512', '448', '448', '512', '448', '576', '640']);
    expect(t[1][2]).not.toBe(t[2][2]);
    expect(nav).toMatch(/#kind-lab" aria-current="page"/);
    expect(nav).toContain('Drop-ins</span>');
  });

  it('opens a course’s Home from its name', () => {
    const nav = render(<StudentNav root="Your semesters" semesters={[sem]} semester={sem} current="home" />);
    expect(nav).toContain(`<a href="?semester=${ORG}#home"><span class="node-name">Deep Learning</span></a>`);
  });
});
