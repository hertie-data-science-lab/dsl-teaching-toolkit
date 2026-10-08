// A kind tab (decision 0035 rule 5): the rows its tab names, dated then undated, the kind's
// own event last; Readings shows only reading files and the pending note.

import { readFileSync } from 'node:fs';
import { render } from 'preact-render-to-string';
import { describe, expect, it } from 'vitest';
import { factsFromStatus } from '../src/model/student';
import { KindView, kindRows } from '../src/screens/StudentKind';

const DOC = JSON.parse(readFileSync(new URL('./fixtures/student-status.json', import.meta.url), 'utf8'));
const ORG = DOC.semester as string;
const NOW = Date.parse('2026-10-08T12:00:00Z');
const text = (v: preact.VNode) => render(v).replace(/<[^>]+>/g, ' ').replace(/&amp;/g, '&').replace(/\s+/g, ' ').trim();

function facts() {
  const d = structuredClone(DOC);
  const base = d.rows.find((r: { id: string }) => r.id === 'lecture_02');
  d.rows.push(
    { ...base, id: 'lecture_00', title: 'Lecture 0', when: '2026-09-01T10:00:00+02:00', released: true, tabs: ['lecture'] },
    { ...base, id: 'extra_b', title: 'Extra B', when: null, released: true, tabs: ['lecture'] },
    { ...base, id: 'extra_a', title: 'Extra A', when: null, released: true, tabs: ['lecture'] },
    { ...base, id: 'hidden', title: 'Off plan', when: '2026-09-20T10:00:00+02:00', off_schedule: true, released: true, tabs: ['lecture'] },
  );
  const l1 = d.rows.find((r: { id: string }) => r.id === 'lecture_01');
  l1.readings_pending = true;
  return factsFromStatus(d);
}

describe('a kind tab', () => {
  it('lists the dated rows by date, then the undated in the engine’s order, off-schedule ones too', () => {
    expect(kindRows(facts(), 'lecture').rows.map((r) => r.id)).toEqual(['lecture_00', 'lecture_01', 'hidden', 'lecture_02', 'extra_b', 'extra_a']);
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

  it('says so when nothing is there', () => {
    expect(text(<KindView facts={facts()} kind="lab" org={ORG} now={NOW} />)).toBe('Nothing released yet.');
  });
});
