import { parse } from 'yaml';
import { describe, expect, it } from 'vitest';
import { YamlText } from '../src/edit/yamlText';

import SEEDED from '../../templates/semester-config/schedule.yml?raw';
import SHEET from '../../example-course/semester-org/grading_sheets/assignment-4-project.yml?raw';

const comments = (t: string): string[] => t.split('\n').filter((l) => l.trim().startsWith('#'));

describe('YamlText on the seeded schedule.yml', () => {
  it('is byte-identical when nothing changes', () => {
    const y = new YamlText(SEEDED);
    y.assign([], y.toJS());
    expect(y.text).toBe(SEEDED);
  });

  it('fills the archive date and keeps the comment in its column', () => {
    const y = new YamlText(SEEDED);
    y.assign(['archive', 'event_datetime'], '2027-01-31');
    const line = y.text.split('\n').find((l) => l.startsWith('  event_datetime:'))!;
    const before = SEEDED.split('\n').find((l) => l.startsWith('  event_datetime:'))!;
    expect(line).toBe('  event_datetime: 2027-01-31  # optional (default: semester_end + 60 days)');
    expect(line.indexOf('#')).toBe(before.indexOf('#'));
    expect(comments(y.text)).toEqual(comments(SEEDED));
  });

  it('adds a release block after the others, keeping every comment and the order of keys', () => {
    const y = new YamlText(SEEDED);
    y.set(['releases', 'lecture-1'], {
      event_datetime: '2026-09-10T10:00',
      title: 'Intro',
      deploy: [{ course_source_repo: 'course-materials-f2026', course_source_path: 'lectures/01' }],
    });
    y.set(['releases', 'lab-1'], { event_datetime: '2026-09-11', kind: 'lab' });
    expect(comments(y.text)).toEqual(comments(SEEDED));
    expect(y.text.startsWith(SEEDED)).toBe(true);
    const d = parse(y.text);
    expect(Object.keys(d)).toEqual(['archive', 'releases']);
    expect(Object.keys(d.releases)).toEqual(['lecture-1', 'lab-1']);
    expect(d.releases['lecture-1'].deploy[0].course_source_path).toBe('lectures/01');
    expect(d.archive.details).toContain('This semester is archived on {{date}}');
  });

  it('edits, removes and re-adds entries without touching the neighbours', () => {
    const y = new YamlText(SEEDED);
    y.set(['releases', 'a'], { event_datetime: '2026-09-10', title: 'A' });
    y.set(['releases', 'b'], { event_datetime: '2026-09-17', title: 'B' });
    const withTwo = y.text;
    y.assign(['releases', 'a'], { event_datetime: '2026-09-10', title: 'A, renamed', details: 'Two\nlines' });
    expect(y.text.replace('A, renamed', 'A').replace('    details: |-\n      Two\n      lines\n', '')).toBe(withTwo);
    y.delete(['releases', 'a']);
    expect(Object.keys(parse(y.text).releases)).toEqual(['b']);
    expect(comments(y.text)).toEqual(comments(SEEDED));
  });

  it('turns a folded block into one line and back, keeping its header comment', () => {
    const y = new YamlText(SEEDED);
    y.assign(['archive', 'details'], 'Archived on {{date}}.');
    expect(y.text).toContain('  details: Archived on {{date}}. # optional, `{{date}}` is filled in automatically\n');
    y.assign(['archive', 'details'], 'One\nTwo');
    expect(parse(y.text).archive.details).toBe('One\nTwo');
    expect(parse(y.text).archive.title).toBe('Semester archived');
  });

  it('quotes strings PyYAML would read as booleans', () => {
    const y = new YamlText('a: 1\n');
    y.set(['b'], 'yes');
    y.set(['c'], '10%');
    expect(y.text).toBe('a: 1\nb: "yes"\nc: 10%\n');
  });

  it('clears a value to a bare key, the way the seeded files leave blanks', () => {
    const y = new YamlText('title: Cohort archived      # c\nx: 1\n');
    y.assign(['title'], null);
    expect(y.text).toBe('title:                      # c\nx: 1\n');
  });
});

describe('YamlText on a grading sheet', () => {
  it('writes a mark and a feedback line, keeping the /max comments aligned', () => {
    const y = new YamlText(SHEET);
    y.assign(['teams', 'team-alpha', 'score_group', 'Q4'], 8);
    y.assign(['teams', 'team-alpha', 'members', 'ben-baker', 'feedback_individual'], 'Good.');
    expect(y.text).toContain('      Q4: 8                     # /10\n');
    expect(y.text).toContain('        feedback_individual: Good.\n');
    expect(comments(y.text)).toEqual(comments(SHEET));
    const lines = (t: string) => t.split('\n').length;
    expect(lines(y.text)).toBe(lines(SHEET));
  });
});

describe('YamlText lists', () => {
  it('appends to, edits and removes from a list of maps', () => {
    const src = 'people:\n  instructors:\n    - github_handle: a   # first\n      email: a@x\n  teaching_assistants: []\n';
    const y = new YamlText(src);
    y.set(['people', 'instructors', 1], { github_handle: 'b', email: 'b@x' });
    y.assign(['people', 'instructors', 0, 'email'], 'a@y');
    y.set(['people', 'teaching_assistants'], [{ github_handle: 'c', email: 'c@x' }]);
    const d = parse(y.text);
    expect(d.people.instructors.map((p: { github_handle: string }) => p.github_handle)).toEqual(['a', 'b']);
    expect(d.people.instructors[0].email).toBe('a@y');
    expect(d.people.teaching_assistants[0].github_handle).toBe('c');
    expect(y.text).toContain('# first');
    y.delete(['people', 'instructors', 0]);
    expect(parse(y.text).people.instructors.map((p: { github_handle: string }) => p.github_handle)).toEqual(['b']);
  });
});
