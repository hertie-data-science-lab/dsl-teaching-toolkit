// The settings forms' output against the mock-ups (decision 0039): Course details and New
// course write `course.yml`, the semester's defaults, Instructors and Student site write
// `semester.yml`, the Students and Teams pages write `students.csv`. Each starts from the file
// as Bootstrap seeds it (`fixtures/config-forms/*.seed.yml`, exported by the engine) and must
// come out as the mock-up's blocks, every line it did not touch kept byte for byte. The
// golden files are the exact text; the mock-ups carry free comments no form writes, so their
// values are compared block by block.

import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { parse } from 'yaml';
import { readTable, writeTable } from '../src/edit/csv';
import { YamlText, obj, render } from '../src/edit/yamlText';
import { placeBlock, skeleton, withDefaults } from '../src/model/config';
import { DEFAULT_KEYS } from '../src/model/names';
import { parseRoster } from '../src/model/people';
import { institutionDefaults, validCourseFile, validSemesterBlocks, type Effectives } from '../src/model/settings';
import { courseFileAfter, detailsOf, withSyllabus } from '../src/screens/CourseEdit';
import { teamsIn, withTeams } from '../src/screens/Marking';
import { entryOf } from '../src/screens/People';
import { semesterDefaultsAfter } from '../src/screens/RunSettings';
import courseSchema from '../schemas/course.schema.json';

const read = (name: string) => readFileSync(new URL(`./fixtures/${name}`, import.meta.url), 'utf8');
const COURSE_SEED = read('config-forms/course.seed.yml');
const SEMESTER_SEED = read('config-forms/semester.seed.yml');
// The mock-ups' values (design/mockups/course.yml and semester.yml).
const COURSE_MOCK = {
  course: { name: 'Deep Learning for Public Policy', code: 'E1394', description: 'One paragraph students see on every semester site and on the open course site.' },
  people: { course_admins: [{ github_handle: 'janedoe', email: 'jane@hertie-school.org' }, { github_handle: 'asst01', email: 'alex@hertie-school.org' }] },
  defaults: { max_team_size: 3, team_formation: 'self_select' },
};
const SEMESTER_MOCK = {
  people: [
    { github_handle: 'pteach', role: 'instructor', email: 'p@hertie-school.org', name: 'Dr P. Teach', title: 'Lecturer', photo: 'https://github.com/pteach.png', url: 'https://...' },
    { github_handle: 'tahelp', role: 'teaching_assistant', email: 't@hertie-school.org', name: 'T. Help', start: '2026-09-01', end: '2027-01-31' },
  ],
  defaults: { late_window_days: 5 },
};
const yamlFile = (text: string) => ({ text, sha: 's', doc: obj(parse(text)), error: null });
/** The lines of `after` that are not in `before`, and those of `before` gone from `after`. */
function changed(before: string, after: string): { added: string[]; gone: string[] } {
  const a = before.split('\n'), b = after.split('\n');
  return { added: b.filter((l) => !a.includes(l)), gone: a.filter((l) => !b.includes(l)) };
}

describe('the defaults: block, as the engine seeds it', () => {
  it('keeps the skeleton’s key set and order, the engine’s (names.json)', () => {
    expect(DEFAULT_KEYS).toEqual(Object.keys(courseSchema.properties.defaults.properties).sort((x, y) => DEFAULT_KEYS.indexOf(x) - DEFAULT_KEYS.indexOf(y)));
    expect(new Set(DEFAULT_KEYS)).toEqual(new Set(Object.keys(courseSchema.properties.defaults.properties)));
    // The console's skeleton is the engine's, byte for byte, for the same inheritance.
    const seeded = COURSE_SEED.slice(COURSE_SEED.indexOf('defaults:'), COURSE_SEED.indexOf('\nopen_course_site:'));
    expect(`${skeleton({}, institutionDefaults())}`).toBe(seeded);
  });

  it('uncomments a key set here in place, and comments it back with what it inherits when cleared', () => {
    const set = withDefaults(COURSE_SEED, { max_team_size: 3 }, institutionDefaults());
    expect(changed(COURSE_SEED, set)).toEqual({ added: ['  max_team_size: 3'], gone: ['  # max_team_size: 5                        (institution)'] });
    expect(withDefaults(set, {}, institutionDefaults())).toBe(COURSE_SEED);
  });

  it('says the other half of the late pair is none once one half is set, as the engine does', () => {
    const out = withDefaults(COURSE_SEED, { late_window_days: 5 }, institutionDefaults());
    expect(out).toContain(`  late_window_days: 5\n${'  # late_penalty_per_day:'.padEnd(44)}(none: the late pair is set above)\n`);
    expect(withDefaults(out, {}, institutionDefaults())).toBe(COURSE_SEED);
  });

  it('writes the whole skeleton into a file that has no block, and edits a flow-style one as YAML', () => {
    const out = withDefaults('course:\n  name: ML\n', { max_team_size: 3 }, institutionDefaults());
    expect(out).toBe(`course:\n  name: ML\n\n${skeleton({ max_team_size: 3 }, institutionDefaults())}`);
    expect(parse(withDefaults('defaults: {max_team_size: 4}\n', { max_team_size: 3, timezone: 'UTC' }, institutionDefaults())).defaults).toEqual({ max_team_size: 3, timezone: 'UTC' });
  });

  it('touches nothing when nothing changed', () => {
    expect(withDefaults(SEMESTER_SEED, {}, institutionDefaults())).toBe(SEMESTER_SEED);
  });
});

describe('Course details and New course: course.yml (mock-up course.yml)', () => {
  const meta = obj(parse(COURSE_SEED));
  const before = detailsOf(meta);
  const after = {
    ...before,
    about: { ...before.about, course_description: COURSE_MOCK.course.description.trim() },
    admins: COURSE_MOCK.people.course_admins.map((a: Record<string, string>) => ({ github_handle: a.github_handle, email: a.email, start: '', end: '' })),
    defaults: { max_team_size: 3, team_formation: 'self_select' },
  };
  const out = courseFileAfter(COURSE_SEED, before, after, meta);
  const text = 'text' in out ? out.text : '';

  it('writes the golden file', () => {
    expect(text).toBe(read('config-forms/course.golden.yml'));
    expect(validCourseFile(parse(text))).toBe(true);
  });

  it('has the mock-up’s course, admins and stated defaults', () => {
    const doc = parse(text);
    expect(doc.course).toMatchObject({ name: COURSE_MOCK.course.name, code: COURSE_MOCK.course.code, description: COURSE_MOCK.course.description.trim() });
    expect(doc.people.course_admins.map((a: Record<string, string>) => [a.github_handle, a.email])).toEqual(COURSE_MOCK.people.course_admins.map((a: Record<string, string>) => [a.github_handle, a.email]));
    expect(doc.defaults).toEqual({ max_team_size: COURSE_MOCK.defaults.max_team_size, team_formation: COURSE_MOCK.defaults.team_formation });
    // Every other key still present, commented with what it inherits.
    for (const k of DEFAULT_KEYS.filter((x) => !(x in doc.defaults))) expect(text).toMatch(new RegExp(`^  # ${k}:`, 'm'));
  });

  it('reads back what it wrote, and saves nothing more when nothing changed', () => {
    const again = detailsOf(obj(parse(text)));
    expect(again.defaults).toEqual(after.defaults);
    expect(courseFileAfter(text, again, again, obj(parse(text)))).toEqual({ text });
  });

  it('saves over each live course’s migrated course.yml, touching only what changed', () => {
    for (const org of ['hertie-dsl-demo-course-e1234', 'hertie-maths-data-science-C23', 'hertie-nlp-e1282']) {
      const src = read(`config-layers/${org}.course.yml`);
      const m = obj(parse(src));
      const b = detailsOf(m);
      expect(courseFileAfter(src, b, b, m), org).toEqual({ text: src });
      const out2 = courseFileAfter(src, b, { ...b, defaults: { ...b.defaults, archive_grace_days: 30 } }, m);
      expect('text' in out2 && changed(src, out2.text), org).toEqual({ added: ['  archive_grace_days: 30'], gone: ['  # archive_grace_days: 60                  (institution)'] });
    }
  });
});

describe('the semester’s settings: semester.yml (mock-up semester.yml)', () => {
  // What it inherits: the course's max team size and formation, the institution's rest.
  const inherited: Effectives = { ...institutionDefaults(), max_team_size: { value: 3, source: 'course' }, team_formation: { value: 'self_select', source: 'course' } };

  it('writes the golden file: the people, the window, the syllabus', () => {
    // As Instructors adds them: the first where the seed shows its commented example, then after it.
    const [first, ...rest] = SEMESTER_MOCK.people as Record<string, unknown>[];
    const y = new YamlText(placeBlock(SEMESTER_SEED, 'people', render({ people: [entryOf(first, {})] })));
    rest.forEach((p, i) => y.set(['people', i + 1], entryOf(p, {})));
    const withPeople = y.text;
    // The mock-up states the late window alone: the engine reads the penalty as none then.
    const out = semesterDefaultsAfter(yamlFile(withPeople), { late_window_days: 5 }, inherited);
    const text = 'text' in out ? out.text : '';
    const site = withSyllabus(text, 'Syllabus.md');
    const final = 'text' in site ? site.text : '';
    expect(final).toBe(read('config-forms/semester.golden.yml'));
    const doc = parse(final);
    expect(doc.people).toEqual(SEMESTER_MOCK.people);
    expect(doc.defaults).toEqual({ late_window_days: SEMESTER_MOCK.defaults.late_window_days });
    expect(doc.student_site).toEqual({ syllabus: 'Syllabus.md' });
    expect(validSemesterBlocks(doc, ['people', 'defaults', 'student_site'])).toBe(true);
    // The inherited keys are commented with their source, as the mock-up shows them.
    expect(final).toContain('  # max_team_size: 3                        (course)\n');
    expect(final).toContain('  # team_formation: self_select             (course)\n');
  });

  it('saves over each live semester’s migrated semester.yml, display-only people and all', () => {
    for (const org of ['hertie-dsl-demo-f2026', 'hertie-maths-data-science-f2026', 'hertie-nlp-f2026']) {
      const src = read(`config-layers/${org}.semester.yml`);
      const f = yamlFile(src);
      const own = obj(f.doc.defaults);
      const out = semesterDefaultsAfter(f, { ...own, archive_grace_days: 30 }, inherited);
      expect('text' in out && changed(src, out.text), org).toEqual({ added: ['  archive_grace_days: 30'], gone: ['  # archive_grace_days: 60                  (institution)'] });
      const site = withSyllabus(src, 'Course.md');
      expect('text' in site && parse(site.text).student_site, org).toEqual({ syllabus: 'Course.md' });
    }
  });
});

describe('the Students and Teams pages: students.csv (mock-up students-and-roster.md)', () => {
  const MOCK = 'email,name,role,team:4,team:6\nana@example.org,Ana Lopez,student,geneva,\nben@example.org,Ben Okafor,student,geneva,oslo\ncara@example.org,Cara Ng,auditor,,oslo\n';
  const SYSTEM = 'email,github_handle,github_id,enrol_code,code_sent_at\nana@example.org,ana-lopez,1234567,dsl-zb3hdm,2026-08-20T09:12:00Z\n';

  it('a team is the rows sharing a cell, one column per team assignment', () => {
    const rows = parseRoster(MOCK, SYSTEM).rows;
    expect(teamsIn(rows, 'team:4').teams).toEqual([{ name: 'geneva', members: ['ana@example.org', 'ben@example.org'] }]);
    expect(teamsIn(rows, 'team:6').teams).toEqual([{ name: 'oslo', members: ['ben@example.org', 'cara@example.org'] }]);
    expect(rows[0]).toMatchObject({ handle: 'ana-lopez', id: '1234567', sent: '2026-08-20T09:12:00Z' });
  });

  it('the Teams tab writes its own column, adding it when the file has none, every other cell kept', () => {
    const bare = writeTable({ header: ['email', 'name', 'role'], rows: readTable(MOCK).rows.map(({ email, name, role }) => ({ email, name, role })) });
    const four = withTeams(bare, 'team:4', { teams: [{ name: 'geneva', members: ['ana@example.org', 'ben@example.org'] }] });
    const six = withTeams(four, 'team:6', { teams: [{ name: 'oslo', members: ['ben@example.org', 'cara@example.org'] }] });
    expect(six).toBe(MOCK);
    // Moving Ana out of geneva blanks her cell and nothing else.
    expect(withTeams(MOCK, 'team:4', { teams: [{ name: 'geneva', members: ['ben@example.org'] }] })).toBe(MOCK.replace('Ana Lopez,student,geneva,', 'Ana Lopez,student,,'));
  });
});
