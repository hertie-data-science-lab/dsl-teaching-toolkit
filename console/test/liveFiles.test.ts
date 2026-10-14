// The live demo's course.yml as the 0.11.0 migration writes it (fixtures/config-layers), and
// its display-only staff entries, which the console's generated schemas refuse but the engine
// reads (research 07 section 5): the console saves them with a note.

import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { fieldErrors } from '../src/forms/Form';
import { courseFileAfter, detailsOf } from '../src/screens/CourseEdit';
import { YamlText, obj } from '../src/edit/yamlText';
import { PERSON } from '../src/tiers/people';

const COURSE = readFileSync(new URL('./fixtures/config-layers/hertie-dsl-demo-course-e1234.course.yml', import.meta.url), 'utf8');

describe('the live demo files', () => {
  const meta = obj(new YamlText(COURSE).toJS());
  const before = detailsOf(meta);

  it('saves course details over the migrated course.yml: its tier and console flag kept, only the code changed', () => {
    const out = courseFileAfter(COURSE, before, { ...before, about: { ...before.about, course_code: 'E1235' } }, meta);
    expect('text' in out && out.text).toBe(COURSE.replace('  code: E1234\n', '  code: E1235\n'));
  });

  it('still refuses what the engine would not read', () => {
    const bad = `${COURSE}lecturers: []\n`;
    const out = courseFileAfter(bad, before, { ...before, about: { ...before.about, course_code: 'E1235' } }, obj(new YamlText(bad).toJS()));
    expect('error' in out && out.error).toContain('must NOT have additional properties');
  });

  it('lets a staff entry with a name and no handle or email be saved, as display only', () => {
    expect(fieldErrors(null, PERSON, { role: 'ta', name: 'B. Sample' })).toEqual({});
    expect(fieldErrors(null, PERSON, { role: 'instructor', name: 'Prof. A. Example', email: 'a@example.org' })).toEqual({});
    expect(fieldErrors(null, PERSON, { role: 'ta' }).github_handle).toBe('Needed, or a display name for a card only.');
    expect(fieldErrors(null, PERSON, { role: 'ta', github_handle: 'b-sample' }).email).toBe('Needed.');
  });
});
