// A semester's Home (decision 0035 rule 4): the site's landing page. The Updates box picks and
// words its bullets as the site's `announcements.html` does; the course's own words and the
// instructors follow.

import { readFileSync } from 'node:fs';
import { render } from 'preact-render-to-string';
import { describe, expect, it } from 'vitest';
import { factsFromStatus } from '../src/model/student';
import { HomeView, homeUpdates } from '../src/screens/StudentHome';

const DOC = JSON.parse(readFileSync(new URL('./fixtures/student-status.json', import.meta.url), 'utf8'));
const ORG = DOC.semester as string;
const NOW = Date.parse('2026-10-08T12:00:00Z');
const text = (v: preact.VNode) => render(v).replace(/<[^>]+>/g, ' ').replace(/&amp;/g, '&').replace(/\s+/g, ' ').trim();

function doc(over: Record<string, unknown> = {}) {
  const d = structuredClone(DOC);
  const archive = { ...d.rows[0], id: 'semester-archived', kind: 'special_event', when: '2026-10-20', title: 'Semester archived', details: 'Everything turns **read-only** on {date}.' };
  d.rows.push(archive, { ...d.rows[1], id: 'readings_02', kind: 'readings', when: '2026-09-29T10:00:00+02:00', title: 'Readings 2', links: [] });
  d.archive_datetime = '2026-10-20';
  return { ...d, ...over };
}

describe('the Updates box', () => {
  it('lists what reached students, newest first: released sessions, assignments handed out, announcements', () => {
    const f = factsFromStatus(doc({ archive_datetime: '2027-01-31' }));
    const u = homeUpdates(f, NOW);
    expect(u.map((x) => ('row' in x ? x.row.id : 'assignment' in x ? x.assignment.slug : 'news' in x ? x.news.title : 'archive'))).toEqual(
      ['readings_02', 'assignment-2-project', 'Room change', 'assignment-1', 'lecture_01'],
    );
    // Unreleased sessions, events, due dates and an assignment not handed out are not news.
    expect(JSON.stringify(u)).not.toContain('lecture_02');
    expect(JSON.stringify(u)).not.toContain('midterm');
    expect(JSON.stringify(u)).not.toContain('assignment-3');
  });

  it('keeps seven', () => {
    const d = doc({ archive_datetime: '2027-01-31' });
    d.announcements = Array.from({ length: 9 }, (_, i) => ({ when: `2026-10-0${i + 1}`, title: `News ${i + 1}`, details: '' }));
    const u = homeUpdates(factsFromStatus(d), NOW);
    expect(u).toHaveLength(7);
    expect('news' in u[0] && u[0].news.title).toBe('News 9');
  });

  it('warns of the archive only in the fortnight before it, with the row’s own text', () => {
    const at = (now: string) => homeUpdates(factsFromStatus(doc()), Date.parse(now)).some((x) => 'archive' in x);
    expect(at('2026-10-01T12:00:00Z')).toBe(false);
    expect(at('2026-10-08T12:00:00Z')).toBe(true);
    expect(at('2026-10-20T08:00:00Z')).toBe(true);
    expect(at('2026-10-21T12:00:00Z')).toBe(false);
    const d = doc();
    d.rows.find((r: { id: string }) => r.id === 'semester-archived').details = '';
    expect(homeUpdates(factsFromStatus(d), NOW).some((x) => 'archive' in x)).toBe(false);
  });

  it('words each bullet as the site does', () => {
    const t = text(<HomeView facts={factsFromStatus(doc())} org={ORG} now={NOW} />);
    expect(t).toContain('New lecture is up: Lecture 1 [ slides.pdf ]');
    expect(t).toContain('New readings are up: Readings 2');
    expect(t).toContain('New Assignment released: [ Assignment 1 ]');
    expect(t).toContain('Room change B1');
    expect(t).toContain('Everything turns read-only');
    const html = render(<HomeView facts={factsFromStatus(doc())} org={ORG} now={NOW} />);
    expect(html).toContain(`href="?semester=${ORG}#assignment-assignment-1"`);
    expect(html).toContain(`href="?semester=${ORG}#materials-materials%2F`);
  });
});

describe('Home', () => {
  it('pins the syllabus, then the description, the home text, previous offerings and the two columns of people', () => {
    const f = factsFromStatus(doc({ course_description: 'Neural networks, from scratch.', previous_offerings: [{ title: 'Fall 2025', url: 'https://example.org/f2025' }] }));
    const html = render(<HomeView facts={f} org={ORG} now={NOW} />);
    const order = ['Updates', '<b>Syllabus</b>', 'Course description', 'Neural networks, from scratch.', 'Welcome.', 'Previous offerings', 'href="https://example.org/f2025"', '>Instructors</h2>', 'Prof X', 'Teaching assistants', 'Tee A'];
    const at = order.map((s) => html.indexOf(s));
    expect(at.every((i) => i >= 0)).toBe(true);
    expect([...at].sort((a, b) => a - b)).toEqual(at);
  });

  it('shows only the teaching assistants when no instructor is listed', () => {
    const d = doc({ instructors: DOC.instructors.filter((c: { role: string }) => c.role === 'teaching_assistant') });
    const html = render(<HomeView facts={factsFromStatus(d)} org={ORG} now={NOW} />);
    expect(html).not.toContain('>Instructors</h2>');
    expect(html).toContain('>Teaching assistants</h2>');
  });

  it('leaves out what the semester does not have', () => {
    const d = doc({ announcements: [], syllabus: null, home_markdown: '', instructors: [], rows: [], assignments: [], course_description: '', previous_offerings: [] });
    const html = render(<HomeView facts={factsFromStatus(d)} org={ORG} now={NOW} />);
    for (const s of ['Updates', 'Syllabus', 'Course description', 'Previous offerings', 'Instructors', 'Teaching assistants']) expect(html).not.toContain(s);
  });
});
